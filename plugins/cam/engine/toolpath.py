# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The canonical toolpath: controller-neutral commands, sections, statistics
and the machining-time estimate (a port of 2DCam's ``CanonicalToolpath``).

Generators emit these commands; the verifier checks them; the overlay draws
them; each post-processor turns them into one dialect of G-code. Nothing
here knows about GRBL or LinuxCNC.

Two commands are the port's own:

- :class:`RetractZ` — a Z-only rapid at the current X/Y. Every operation
  starts with one, so the first move after a tool change or at program
  start lifts the cutter BEFORE it travels, instead of a straight-line
  rapid from wherever the spindle happens to be.
- :class:`DrillCycle` — one hole, kept whole so LinuxCNC can post it as a
  ``G81``/``G82``/``G83`` canned cycle. Anything that needs plain motion
  (statistics, the verifier, GRBL, the overlay) calls :func:`expand_drill`,
  which yields exactly the moves LinuxCNC's cycle performs. With no peck
  and no dwell that is 2DCam's own sequence (rapid to R, feed to the
  bottom, rapid to R), so parity is kept.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .models import Machine, Tolerance


# ---- commands ----------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Comment:
    """A program comment. ``text`` is an English TEMPLATE (``"Pocket
    roughing pass {n} of {total}"``) and ``params`` fills it: the post
    renders it through a translator the UI supplies, so comments follow
    the job's language, then reduces it to ASCII. The templates are listed
    in :data:`plugins.cam.engine.ops.common.COMMENT_TEMPLATES` for the
    translation catalogues."""
    text: str
    params: dict = field(default_factory=dict, compare=False)

    def render(self, translate=None) -> str:
        tpl = translate(self.text) if translate is not None else self.text
        if not self.params:
            return tpl
        try:
            return tpl.format(**self.params)
        except (KeyError, IndexError, ValueError):
            return self.text.format(**self.params)


@dataclass(frozen=True, slots=True)
class ToolChange:
    number: int


@dataclass(frozen=True, slots=True)
class SpindleStart:
    rpm: int
    clockwise: bool = True


@dataclass(frozen=True, slots=True)
class SpindleStop:
    pass


@dataclass(frozen=True, slots=True)
class Coolant:
    enabled: bool


@dataclass(frozen=True, slots=True)
class CutterCompensation:
    mode: str                     # off|left|right


@dataclass(frozen=True, slots=True)
class Rapid:
    to: tuple


@dataclass(frozen=True, slots=True)
class RetractZ:
    z: float


@dataclass(frozen=True, slots=True)
class Linear:
    to: tuple
    feed: float


@dataclass(frozen=True, slots=True)
class Arc:
    """A circular (or helical) move in the XY plane. ``center`` is the
    offset from the START point to the centre (``I``/``J``/``K``)."""
    to: tuple
    center: tuple
    feed: float
    clockwise: bool


@dataclass(frozen=True, slots=True)
class Dwell:
    seconds: float


@dataclass(frozen=True, slots=True)
class DrillCycle:
    """A drilled hole at ``(x, y)``: from the current position above it,
    rapid down to ``r`` (the clearance plane), feed to ``bottom`` — in
    ``peck`` steps when ``peck > 0`` — optionally dwell, and rapid back to
    ``r``."""
    x: float
    y: float
    r: float
    bottom: float
    feed: float
    peck: float = 0.0
    dwell: float = 0.0


#: LinuxCNC's G83 backs off by this much above the previous peck bottom
#: before feeding again (0.010 in; ``canon`` uses the same number in mm
#: jobs as 0.254 mm).
PECK_BACKOFF_MM = 0.254

MOTION_TYPES = (Rapid, RetractZ, Linear, Arc, DrillCycle)


def expand_drill(c: DrillCycle) -> list:
    """The plain motion a :class:`DrillCycle` performs (LinuxCNC G81/G82/
    G83 with G99 retract), used by everything that does not post cycles."""
    x, y = c.x, c.y
    out = [Rapid((x, y, c.r))]
    if c.peck > 0 and c.bottom < c.r:
        depth = c.r
        while True:
            nxt = max(depth - c.peck, c.bottom)
            if depth < c.r:                   # back down to just above the last bottom
                out.append(Rapid((x, y, depth + PECK_BACKOFF_MM)))
            out.append(Linear((x, y, nxt), c.feed))
            if nxt <= c.bottom + 1e-12:
                break
            out.append(Rapid((x, y, c.r)))
            depth = nxt
    else:
        out.append(Linear((x, y, c.bottom), c.feed))
    if c.dwell > 0 and c.peck <= 0:
        out.append(Dwell(c.dwell))
    out.append(Rapid((x, y, c.r)))
    return out


def expanded(commands) -> list:
    """``commands`` with every :class:`DrillCycle` replaced by its moves."""
    out = []
    for c in commands:
        if isinstance(c, DrillCycle):
            out.extend(expand_drill(c))
        else:
            out.append(c)
    return out


# ---- sections ----------------------------------------------------------------

SECTION_KINDS = ("rapid", "leadIn", "roughing", "cutting", "finishing", "leadOut",
                 "linking", "auxiliary")


@dataclass
class Section:
    kind: str
    start: int
    count: int
    operation_id: str | None = None
    operation_name: str | None = None

    @property
    def stop(self) -> int:
        return self.start + self.count


def _section_kind(c) -> str:
    if isinstance(c, (Rapid, RetractZ)):
        return "rapid"
    if isinstance(c, (Linear, Arc, DrillCycle)):
        return "cutting"
    return "auxiliary"


def infer_sections(commands) -> list:
    """2DCam's fallback when a generator gives no sections: runs of rapid /
    cutting / auxiliary commands."""
    if not commands:
        return []
    out, start, kind = [], 0, _section_kind(commands[0])
    for i in range(1, len(commands)):
        k = _section_kind(commands[i])
        if k != kind:
            out.append(Section(kind, start, i - start))
            start, kind = i, k
    out.append(Section(kind, start, len(commands) - start))
    return out


# ---- the toolpath ------------------------------------------------------------

@dataclass
class Statistics:
    rapidLength: float = 0.0
    cuttingLength: float = 0.0
    estimatedDurationSeconds: float = 0.0
    toolChangeCount: int = 0
    motionCount: int = 0


def _dist(a, b) -> float:
    return math.hypot(math.hypot(b[0] - a[0], b[1] - a[1]), b[2] - a[2])


def arc_length(start, c: Arc) -> float:
    """Length of an arc (helix) from ``start``; a zero sweep is a full turn."""
    r = math.hypot(c.center[0], c.center[1])
    if r <= 0:
        return _dist(start, c.to)
    cx, cy = start[0] + c.center[0], start[1] + c.center[1]
    a0 = math.atan2(start[1] - cy, start[0] - cx)
    a1 = math.atan2(c.to[1] - cy, c.to[0] - cx)
    sweep = (a0 - a1) if c.clockwise else (a1 - a0)
    sweep = math.fmod(sweep, 2 * math.pi)
    if sweep < 0:
        sweep += 2 * math.pi
    if sweep < 1e-9:
        sweep = 2 * math.pi
    return math.hypot(r * sweep, c.to[2] - start[2])


def _retract_point(current, z):
    return None if current is None else (current[0], current[1], z)


@dataclass
class Toolpath:
    commands: list = field(default_factory=list)
    sections: list | None = None
    tolerance: Tolerance = field(default_factory=Tolerance)

    def __post_init__(self) -> None:
        if self.sections is None:
            self.sections = infer_sections(self.commands)

    def section_commands(self, s: Section) -> list:
        return self.commands[max(0, s.start):max(0, s.stop)]

    # -- statistics (feed time only, 2DCam's ``statistics``) --
    def statistics(self) -> Statistics:
        st = Statistics()
        cur = None
        for c in expanded(self.commands):
            if isinstance(c, ToolChange):
                st.toolChangeCount += 1
            elif isinstance(c, Rapid):
                if cur is not None:
                    st.rapidLength += _dist(cur, c.to)
                cur = c.to
                st.motionCount += 1
            elif isinstance(c, RetractZ):
                nxt = _retract_point(cur, c.z)
                if cur is not None:
                    st.rapidLength += abs(c.z - cur[2])
                cur = nxt
                st.motionCount += 1
            elif isinstance(c, Linear):
                if cur is not None:
                    length = _dist(cur, c.to)
                    st.cuttingLength += length
                    if c.feed > 0:
                        st.estimatedDurationSeconds += length / c.feed * 60
                cur = c.to
                st.motionCount += 1
            elif isinstance(c, Arc):
                if cur is not None:
                    length = arc_length(cur, c)
                    st.cuttingLength += length
                    if c.feed > 0:
                        st.estimatedDurationSeconds += length / c.feed * 60
                cur = c.to
                st.motionCount += 1
            elif isinstance(c, Dwell):
                st.estimatedDurationSeconds += max(0.0, c.seconds)
        return st

    # -- the controller-oriented estimate (2DCam's ``estimatedDurationSeconds(machine:)``) --
    def estimated_duration(self, machine: Machine) -> float:
        cum = self.cumulative_durations(machine)
        return cum[-1] if cum else 0.0

    def cumulative_durations(self, machine: Machine) -> list:
        """Estimated seconds at every command boundary (one value per
        command of ``self.commands``; a drill cycle counts as one). Motion
        with acceleration; consecutive near-collinear moves at one feed
        blend into a single look-ahead chain, as a controller would."""
        seconds = 0.0
        cur = None
        chain = None                 # [mode, feed, accel, length, direction]
        out = []

        def chain_seconds(ch):
            return _accel_seconds(ch[3], ch[1], ch[2]) if ch else 0.0

        def flush():
            nonlocal seconds, chain
            if chain is not None:
                seconds += chain_seconds(chain)
                chain = None

        def motion(a, b, mode, feed, accel):
            nonlocal chain
            length = _dist(a, b)
            if length <= 0:
                return
            d = ((b[0] - a[0]) / length, (b[1] - a[1]) / length, (b[2] - a[2]) / length)
            if (chain is not None and chain[0] == mode
                    and (chain[4][0] * d[0] + chain[4][1] * d[1] + chain[4][2] * d[2])
                    >= _BLEND_COS):
                chain[3] += length
                chain[4] = d
            else:
                flush()
                chain = [mode, feed, accel, length, d]

        def step(c):
            nonlocal cur, seconds
            if isinstance(c, ToolChange):
                flush()
                seconds += machine.toolChangeSeconds
            elif isinstance(c, SpindleStart):
                flush()
                seconds += machine.spindleRampSeconds
            elif isinstance(c, Coolant):
                if c.enabled:
                    flush()
                    seconds += machine.coolantDelaySeconds
            elif isinstance(c, Dwell):
                flush()
                seconds += max(0.0, c.seconds)
            elif isinstance(c, Rapid):
                if cur is not None:
                    motion(cur, c.to, ("rapid",), machine.rapidFeed, machine.rapidAcceleration)
                cur = c.to
            elif isinstance(c, RetractZ):
                nxt = _retract_point(cur, c.z)
                if cur is not None:
                    motion(cur, nxt, ("rapid",), machine.rapidFeed, machine.rapidAcceleration)
                cur = nxt
            elif isinstance(c, Linear):
                if cur is not None:
                    motion(cur, c.to, ("cut", c.feed), c.feed, machine.cuttingAcceleration)
                cur = c.to
            elif isinstance(c, Arc):
                flush()
                if cur is not None:
                    seconds += _accel_seconds(arc_length(cur, c), c.feed,
                                              machine.cuttingAcceleration)
                cur = c.to
            elif isinstance(c, (SpindleStop, CutterCompensation)):
                flush()

        for c in self.commands:
            if isinstance(c, DrillCycle):
                for sub in expand_drill(c):
                    step(sub)
            else:
                step(c)
            out.append(seconds + chain_seconds(chain))
        return out


#: Thirty degrees: sharper corners start a new motion chain (2DCam).
_BLEND_COS = math.cos(math.radians(30))


def _accel_seconds(length: float, feed_per_minute: float, accel: float) -> float:
    if length <= 0 or feed_per_minute <= 0 or accel <= 0:
        return 0.0
    v = feed_per_minute / 60.0
    accel_dist = v * v / accel
    if length >= accel_dist:
        return length / v + v / accel
    return 2 * math.sqrt(length / accel)


def motion_points(commands):
    """Yield ``(kind, start, end, command_index)`` for every straight piece
    of motion — ``kind`` is ``"rapid"`` or ``"cut"``; arcs are flattened
    to chords of at most 5°. What the overlay and the gouge check walk."""
    cur = None
    for i, c in enumerate(commands):
        subs = expand_drill(c) if isinstance(c, DrillCycle) else (c,)
        for s in subs:
            if isinstance(s, Rapid):
                if cur is not None:
                    yield "rapid", cur, s.to, i
                cur = s.to
            elif isinstance(s, RetractZ):
                if cur is not None:
                    nxt = (cur[0], cur[1], s.z)
                    yield "rapid", cur, nxt, i
                    cur = nxt
            elif isinstance(s, Linear):
                if cur is not None:
                    yield "cut", cur, s.to, i
                cur = s.to
            elif isinstance(s, Arc):
                if cur is not None:
                    prev = cur
                    for p in arc_points(cur, s, max_step_deg=5.0)[1:]:
                        yield "cut", prev, p, i
                        prev = p
                cur = s.to


def arc_points(start, c: Arc, max_step_deg: float = 5.0) -> list:
    """Points along an arc from ``start`` to ``c.to`` (both included)."""
    cx, cy = start[0] + c.center[0], start[1] + c.center[1]
    r = math.hypot(c.center[0], c.center[1])
    if r <= 0:
        return [start, c.to]
    a0 = math.atan2(start[1] - cy, start[0] - cx)
    a1 = math.atan2(c.to[1] - cy, c.to[0] - cx)
    sweep = (a0 - a1) if c.clockwise else (a1 - a0)
    sweep = math.fmod(sweep, 2 * math.pi)
    if sweep < 0:
        sweep += 2 * math.pi
    if sweep < 1e-9:
        sweep = 2 * math.pi
    n = max(1, int(math.ceil(math.degrees(sweep) / max_step_deg)))
    sign = -1.0 if c.clockwise else 1.0
    pts = [start]
    for k in range(1, n + 1):
        f = k / n
        a = a0 + sign * sweep * f
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a),
                    start[2] + (c.to[2] - start[2]) * f))
    pts[-1] = c.to
    return pts
