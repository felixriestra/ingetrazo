# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Verification: what must hold before a toolpath may become G-code.

Three layers:

1. :func:`verify` — 2DCam's ``ToolpathVerifier`` ported: setup heights,
   duplicate tool numbers, spindle/tool state around every cut, rapids
   that end in or pass through the stock, cuts below the stock bottom,
   machine travel, arc geometry. (2DCam's fixture and relief checks are
   out of v1's scope.)
2. :func:`check_gouges` — the port's own: for every profile and pocket,
   the cutter (the right one, finishing tool included) must stay a tool
   radius clear of the part at every point below the stock top. 2DCam has
   no such check, and its corpus shows why one is worth having (the 5 mm
   pocket cut with a 6 mm tool, helix entries across the wall).
3. :func:`parse_gcode` and :func:`round_trip` — a posted program is read
   back with its dialect's rules and must reproduce the toolpath.

Export is allowed only when none of them reports an error.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

from . import geometry as geo
from .issues import ERROR, WARNING, Issue
from .models import MM_PER_INCH
from .toolpath import (Arc, CutterCompensation, DrillCycle, Linear, Rapid, RetractZ,
                       SpindleStart, SpindleStop, ToolChange, expand_drill, motion_points)


@dataclass
class Report:
    issues: list = field(default_factory=list)

    @property
    def errors(self) -> list:
        return [i for i in self.issues if i.severity == ERROR]

    @property
    def warnings(self) -> list:
        return [i for i in self.issues if i.severity == WARNING]

    @property
    def can_export(self) -> bool:
        return not self.errors


def _issue(code, index=None, **params) -> Issue:
    return Issue(code, ERROR, params, index)


# ---- 1. the 2DCam verifier ----------------------------------------------------

def verify(job, toolpath) -> Report:
    issues: list = []
    setup = job.setup
    if not (math.isfinite(setup.safeHeight) and setup.safeHeight > 0):
        issues.append(_issue("safe_height_invalid", height=setup.safeHeight))
    if not (math.isfinite(setup.clearanceHeight) and 0 < setup.clearanceHeight <= setup.safeHeight):
        issues.append(_issue("clearance_height_invalid", height=setup.clearanceHeight,
                             safe=setup.safeHeight))
    m = job.machine
    if m.enforcesTravelLimits and not m.has_valid_travel_envelope:
        issues.append(_issue("travel_limits_invalid"))
    numbers: dict = {}
    for t in job.tools:
        numbers[t.number] = numbers.get(t.number, 0) + 1
    for n in sorted(k for k, v in numbers.items() if v > 1):
        issues.append(_issue("duplicate_tool_number", number=n))
    if not toolpath.commands:
        issues.append(_issue("empty_toolpath"))

    stock = job.stock
    spindle = False
    tool_number = None
    cur = None
    for i, c in enumerate(toolpath.commands):
        if isinstance(c, ToolChange):
            if spindle:
                issues.append(_issue("tool_change_spindle_running", i))
            tool_number = c.number
            if job.tool_by_number(c.number) is None:
                issues.append(_issue("tool_not_in_job", i, number=c.number))
        elif isinstance(c, SpindleStart):
            spindle = True
        elif isinstance(c, SpindleStop):
            spindle = False
        elif isinstance(c, Rapid):
            _check_rapid(cur, c.to, job, i, issues)
            cur = c.to
        elif isinstance(c, RetractZ):
            if not math.isfinite(c.z):
                issues.append(_issue("non_finite_coordinate", i))
            elif cur is not None:
                nxt = (cur[0], cur[1], c.z)
                _check_rapid(cur, nxt, job, i, issues)
                cur = nxt
        elif isinstance(c, Linear):
            _check_cut(c.to, c.feed, spindle, tool_number, job, i, issues)
            cur = c.to
        elif isinstance(c, Arc):
            _check_cut(c.to, c.feed, spindle, tool_number, job, i, issues)
            if cur is not None:
                _check_arc(cur, c, job, i, issues)
            cur = c.to
        elif isinstance(c, DrillCycle):
            # The cycle's own rapids run inside the hole it is drilling (the
            # peck back-offs), so they are not "rapids into the stock"; what
            # must hold is where it starts, how deep it goes and the feed.
            start = (c.x, c.y, c.r)
            if cur is not None and (abs(cur[0] - c.x) > 1e-6 or abs(cur[1] - c.y) > 1e-6):
                _check_rapid(cur, (c.x, c.y, max(cur[2], c.r)), job, i, issues)
            if c.r < stock.top_z - 1e-9:
                issues.append(_issue("rapid_inside_stock", i))
            _check_cut((c.x, c.y, c.bottom), c.feed, spindle, tool_number, job, i, issues)
            _check_travel(start, job, i, issues)
            cur = start
    return Report(issues)


def _finite(p) -> bool:
    return all(math.isfinite(v) for v in p)


def _check_travel(p, job, i, issues) -> None:
    m = job.machine
    if m.enforcesTravelLimits and m.has_valid_travel_envelope and not m.contains_work_point(p):
        issues.append(_issue("travel_exceeded", i))


def _inside_stock(p, stock) -> bool:
    return stock.contains_xy(p) and stock.origin[2] < p[2] < stock.top_z


def _check_rapid(cur, to, job, i, issues) -> None:
    if not _finite(to):
        issues.append(_issue("non_finite_coordinate", i))
        return
    _check_travel(to, job, i, issues)
    if _inside_stock(to, job.stock):
        issues.append(_issue("rapid_inside_stock", i))
    if (cur is not None and (abs(cur[0] - to[0]) > 1e-6 or abs(cur[1] - to[1]) > 1e-6)
            and _segment_hits_box(cur, to, job.stock)):
        issues.append(_issue("rapid_through_stock", i))


def _check_cut(to, feed, spindle, tool_number, job, i, issues) -> None:
    if not _finite(to):
        issues.append(_issue("non_finite_coordinate", i))
    if not (math.isfinite(feed) and feed > 0):
        issues.append(_issue("feed_invalid", i, feed=feed))
    if not spindle:
        issues.append(_issue("cut_spindle_stopped", i))
    if tool_number is None:
        issues.append(_issue("cut_before_tool_change", i))
    if to[2] < job.stock.origin[2] - 1e-9:
        issues.append(_issue("cut_below_stock_bottom", i, z=to[2], bottom=job.stock.origin[2]))
    _check_travel(to, job, i, issues)


def _check_arc(start, c: Arc, job, i, issues) -> None:
    if not _finite(c.center):
        issues.append(_issue("arc_non_finite", i))
        return
    cx, cy = start[0] + c.center[0], start[1] + c.center[1]
    r0 = math.hypot(start[0] - cx, start[1] - cy)
    r1 = math.hypot(c.to[0] - cx, c.to[1] - cy)
    if r0 <= 0 or abs(r0 - r1) > max(job.tolerance.arc, 1e-9):
        issues.append(_issue("arc_radius_mismatch", i, start_radius=r0, end_radius=r1))


def _segment_hits_box(a, b, stock) -> bool:
    """Does segment ``ab`` pass through the OPEN stock box (2DCam's
    slab test: touching a face is not intersecting)?"""
    lo = stock.origin
    hi = (lo[0] + stock.width, lo[1] + stock.depth, lo[2] + stock.height)
    t0, t1 = 0.0, 1.0
    for k in range(3):
        d = b[k] - a[k]
        if abs(d) < 1e-9:
            if a[k] <= lo[k] or a[k] >= hi[k]:
                return False
            continue
        f, s = (lo[k] - a[k]) / d, (hi[k] - a[k]) / d
        t0, t1 = max(t0, min(f, s)), min(t1, max(f, s))
        if t0 >= t1:
            return False
    return t1 > 0 and t0 < 1


# ---- 2. gouges ----------------------------------------------------------------

def check_gouges(job, compiled) -> list:
    """Issues for cutter positions that eat into the part, per operation.

    ``compiled`` is a :class:`~.compiler.CompileResult`. For profiles and
    pockets every sampled cutter position below the stock top must keep its
    radius clear of the part: outside an outside profile's boundary,
    inside an inside profile's, inside a pocket and outside its islands.
    Drilling, engraving and facing cut on their own lines by definition."""
    out = []
    tp = compiled.toolpath
    for op in job.operations:
        if op.id not in compiled.operation_ranges or op.kind not in (
                "outsideProfile", "insideProfile", "pocket", "openPocket", "bore", "slot"):
            continue
        if op.strategy.compensation == "controller":
            continue                  # the controller offsets the programmed edge
        start, stop = compiled.operation_ranges[op.id]
        loops, keep_out_inside = _protected(job, op)
        if not loops:
            continue
        top = job.stock.top_z + op.strategy.topHeight - 1e-6
        tol = job.tolerance.chordal + 0.005
        radius_by_tool = {t.number: t.diameter * 0.5 for t in job.tools}
        # Which tool is cutting at each command of the operation.
        tool_at = {}
        current = None
        for i in range(start, stop):
            c = tp.commands[i]
            if isinstance(c, ToolChange):
                current = c.number
            tool_at[i] = current
        pts, idx = [], []
        for kind, a, b, i in motion_points(tp.commands[start:stop]):
            if kind != "cut":
                continue
            n = max(1, int(math.ceil(math.dist(a[:2], b[:2]) / 0.5)))
            for k in range(n + 1):
                f = k / n
                p = (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f,
                     a[2] + (b[2] - a[2]) * f)
                if p[2] < top:
                    pts.append(p)
                    idx.append(start + i)
        if not pts:
            continue
        P = np.asarray(pts, dtype=float)
        r_max = max(radius_by_tool.values(), default=0.0)
        radius = np.array([radius_by_tool.get(tool_at.get(i), 0.0) for i in idx])
        if keep_out_inside is None:
            # Open pocket: only the closed edges (``loops`` holds them as
            # 2-point segments) are walls; the cutter may cross the rest.
            dist = _distance_to_segments(P[:, :2], loops, cutoff=r_max + 1.0)
            wrong_side = np.zeros(len(P), dtype=bool)
        else:
            dist = _distance_to_loops(P[:, :2], loops, cutoff=r_max + 1.0)
            inside = _inside_even_odd(P[:, :2], loops)
            wrong_side = inside if keep_out_inside else ~inside
        bad = wrong_side | (dist < radius - tol)
        if bad.any():
            k = int(np.argmax(np.where(bad, radius - dist + wrong_side * 1e6, -np.inf)))
            depth = float(radius[k] - dist[k]) if not wrong_side[k] else float(radius[k] + dist[k])
            out.append(Issue("gouge", ERROR,
                             {"x": round(pts[k][0], 3), "y": round(pts[k][1], 3),
                              "z": round(pts[k][2], 3), "depth": round(depth, 3)},
                             idx[k], op.id, op.name))
    return out


def _protected(job, op):
    """The loops a cutter must keep clear of, and whether the forbidden
    side is the INSIDE of those loops (even-odd) — or ``None`` for an open
    pocket, whose "loops" are its wall segments."""
    from .ops.common import automatic_rect
    g = op.strategy.geometry
    p = op.parameters
    if op.kind == "bore":
        n = 180
        R = p.diameter * 0.5
        # The circumscribed polygon: its edges are no nearer the centre
        # than the true circle, so a cutter clear of it is clear of the hole.
        Rc = R / math.cos(math.pi / n)
        return [[(p.center[0] + Rc * math.cos(2 * math.pi * k / n),
                  p.center[1] + Rc * math.sin(2 * math.pi * k / n)) for k in range(n)]], False
    if op.kind == "slot":
        tool = job.tool(op.toolID)
        r = tool.diameter * 0.5 if tool is not None else 0.0
        return [_slot_outline(p.start, p.end, p.width * 0.5, r)], False
    if op.kind == "openPocket":
        if g is None:
            return [automatic_rect(job.stock, p.inset)], False
        n = len(g.boundary)
        opened = set(g.openEdgeIndices)
        walls = [[g.boundary[i], g.boundary[(i + 1) % n]] for i in range(n) if i not in opened]
        for isl in g.islands:
            walls.extend([[isl[i], isl[(i + 1) % len(isl)]] for i in range(len(isl))])
        return walls, None
    try:
        if op.kind == "pocket":
            boundary = g.boundary if g is not None else automatic_rect(job.stock, op.parameters.inset)
            islands = list(g.islands) if g is not None else []
            return [boundary] + islands, False          # must stay INSIDE the region
        boundary = g.boundary if g is not None else automatic_rect(job.stock, op.parameters.inset)
    except Exception:  # noqa: BLE001 — an invalid region was reported by the compiler
        return [], False
    return [boundary], op.kind == "outsideProfile"


def _slot_outline(a, b, half_width, r) -> list:
    """What a slot cuts: the rectangle its cutter centres sweep (``start``
    to ``end``, ``width − d`` across) grown by the tool radius."""
    from . import geometry as geo
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = max(math.hypot(dx, dy), 1e-12)
    nx, ny = -dy / length, dx / length
    m = max(half_width - r, 0.0)
    inner = [(a[0] - nx * m, a[1] - ny * m), (b[0] - nx * m, b[1] - ny * m),
             (b[0] + nx * m, b[1] + ny * m), (a[0] + nx * m, a[1] + ny * m)]
    if m < 1e-3:                 # one row: a line; 1 µm wide so Clipper keeps it
        m = 1e-3
        inner = [(a[0] - nx * m, a[1] - ny * m), (b[0] - nx * m, b[1] - ny * m),
                 (b[0] + nx * m, b[1] + ny * m), (a[0] + nx * m, a[1] + ny * m)]
    (grown,) = geo.offset_loop(inner, r, "round", 0.002)[:1]
    return grown


def _distance_to_segments(P, segments, cutoff: float | None = None) -> np.ndarray:
    """Distance to the nearest of open ``segments`` ([[a, b], …])."""
    if not segments:
        return np.full(len(P), np.inf if cutoff is None else float(cutoff))
    A = np.asarray([s[0] for s in segments], dtype=float)
    B = np.asarray([s[1] for s in segments], dtype=float)
    d = _pairs_min(P, A, B)
    return d if cutoff is None else np.minimum(d, cutoff)


def _distance_to_loops(P, loops, cutoff: float | None = None) -> np.ndarray:
    """Distance from each point of ``P`` (N, 2) to the nearest edge of
    ``loops``. With ``cutoff``, anything farther is reported as
    ``cutoff``: points and edges are binned on a grid of that size, and a
    point is only measured against the edges in its own and the eight
    neighbouring cells — a pocket of thousands of moves beside a
    thousand-edge outline costs a fraction of the all-pairs work."""
    A = np.concatenate([np.asarray(lp, dtype=float) for lp in loops])
    B = np.concatenate([np.roll(np.asarray(lp, dtype=float), -1, axis=0) for lp in loops])
    if cutoff is None or cutoff <= 0:
        return _pairs_min(P, A, B)
    best = np.full(len(P), float(cutoff))
    cell = float(cutoff)
    lo = np.minimum(A, B).min(axis=0) - cell
    pc = np.floor((P - lo) / cell).astype(np.int64)
    seg_lo = np.floor((np.minimum(A, B) - lo) / cell).astype(np.int64)
    seg_hi = np.floor((np.maximum(A, B) - lo) / cell).astype(np.int64)
    grid: dict = {}
    for s in range(len(A)):
        for i in range(seg_lo[s, 0], seg_hi[s, 0] + 1):
            for j in range(seg_lo[s, 1], seg_hi[s, 1] + 1):
                grid.setdefault((i, j), []).append(s)
    keys, inverse = np.unique(pc, axis=0, return_inverse=True)
    inverse = inverse.reshape(-1)
    for k, (ci, cj) in enumerate(keys):
        segs = set()
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                segs.update(grid.get((ci + di, cj + dj), ()))
        if not segs:
            continue
        idx = np.nonzero(inverse == k)[0]
        sel = np.fromiter(segs, dtype=np.int64)
        best[idx] = np.minimum(best[idx], _pairs_min(P[idx], A[sel], B[sel]))
    return best


def _pairs_min(P, A, B) -> np.ndarray:
    best = np.full(len(P), np.inf)
    for s in range(0, len(A), 256):
        a, b = A[s:s + 256], B[s:s + 256]
        ab = b - a
        sq = np.maximum((ab * ab).sum(1), 1e-18)
        ap = P[:, None, :] - a[None, :, :]
        t = np.clip((ap * ab[None]).sum(2) / sq[None], 0.0, 1.0)
        proj = a[None] + t[..., None] * ab[None]
        best = np.minimum(best, np.sqrt(((P[:, None, :] - proj) ** 2).sum(2)).min(1))
    return best


def _inside_even_odd(P, loops) -> np.ndarray:
    inside = np.zeros(len(P), dtype=bool)
    x, y = P[:, 0], P[:, 1]
    for lp in loops:
        A = np.asarray(lp, dtype=float)
        B = np.roll(A, -1, axis=0)
        for (ax, ay), (bx, by) in zip(A, B):
            cond = (ay > y) != (by > y)
            with np.errstate(divide="ignore", invalid="ignore"):
                xc = (bx - ax) * (y - ay) / (by - ay) + ax
            inside ^= cond & (x < xc)
    return inside


# ---- 3. G-code read-back -----------------------------------------------------

_WORD = re.compile(r"([A-Z])\s*([-+]?(?:\d+\.?\d*|\.\d+))")


@dataclass
class ParsedMotion:
    kind: str                         # rapid|linear|arcCW|arcCCW
    to: tuple                         # mm; an axis never set yet is None
    center: tuple | None = None       # mm, offset from start
    feed: float | None = None         # mm/min


@dataclass
class ParsedProgram:
    units: str | None = None          # millimeters|inches
    motions: list = field(default_factory=list)
    violations: list = field(default_factory=list)    # (line, rule)


#: What a GRBL 1.1 file must never contain: no tool changer (M6), no tool
#: length offsets (G43), no cutter compensation (G41/G42) and no canned
#: cycles (G81–G89) — the controller rejects them.
GRBL_FORBIDDEN = {("M", 6), ("G", 43), ("G", 41), ("G", 42), ("G", 81), ("G", 82),
                  ("G", 83), ("G", 73), ("G", 85), ("G", 89)}


def _strip_comments(line: str) -> str:
    out, depth = [], 0
    for ch in line:
        if ch == "(":
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
        elif depth == 0:
            if ch == ";":
                break
            out.append(ch)
    return "".join(out)


def parse_gcode(text: str, dialect: str = "linuxcnc") -> ParsedProgram:
    """Read back a program we posted: G0/G1/G2/G3, G81/G82/G83 (expanded
    exactly as :func:`~.toolpath.expand_drill` does), G20/G21, modal F.
    Coordinates come back in millimetres. Dialect rules are reported as
    violations, not exceptions, so a test can list them all."""
    prog = ParsedProgram()
    scale = 1.0
    pos = [None, None, None]
    feed = None
    motion_mode = None
    retract_mode = "G98"
    arc_incremental = True
    cycle = None                   # (code, r, z, q, p)
    for n, raw in enumerate(text.splitlines(), 1):
        if any(ord(ch) > 127 for ch in raw):
            prog.violations.append((n, "non_ascii"))
        if "," in _strip_comments(raw):
            prog.violations.append((n, "comma"))
        line = _strip_comments(raw).strip().upper()
        if not line or line == "%":
            continue
        words = [(l, float(v)) for l, v in _WORD.findall(line)]
        codes = [(l, v) for l, v in words if l in "GM"]
        if dialect == "grbl":
            for l, v in codes:
                if (l, int(v)) in GRBL_FORBIDDEN and float(v).is_integer():
                    prog.violations.append((n, f"{l}{int(v)}"))
        gs = [v for l, v in codes if l == "G"]
        if ("M", 6.0) in codes:
            pos = [None, None, None]          # the changer leaves the spindle anywhere
        if 91 in gs:
            prog.violations.append((n, "incremental"))
        if 20 in gs:
            prog.units, scale = "inches", MM_PER_INCH
        if 21 in gs:
            prog.units, scale = "millimeters", 1.0
        if 91.1 in gs:
            arc_incremental = True
        if 90.1 in gs:
            arc_incremental = False
        if 98 in gs:
            retract_mode = "G98"
        if 99 in gs:
            retract_mode = "G99"
        vals = {l: v for l, v in words if l not in "GMNOT"}
        if "F" in vals:
            feed = vals["F"] * scale
        for g in gs:
            if g in (0, 1, 2, 3):
                motion_mode, cycle = int(g), None
            elif g in (81, 82, 83):
                motion_mode = int(g)
            elif g == 80:
                motion_mode, cycle = None, None
        axes = [vals.get(k) for k in "XYZ"]
        if motion_mode is None or not any(a is not None for a in axes + [vals.get("R")]):
            continue
        if motion_mode in (81, 82, 83):
            start_z = pos[2]
            x = vals["X"] * scale if "X" in vals else pos[0]
            y = vals["Y"] * scale if "Y" in vals else pos[1]
            z = vals["Z"] * scale if "Z" in vals else (cycle[1] if cycle else None)
            r = vals["R"] * scale if "R" in vals else (cycle[0] if cycle else None)
            q = vals["Q"] * scale if "Q" in vals else (cycle[2] if cycle else 0.0)
            p = vals.get("P", cycle[3] if cycle else 0.0)
            cycle = (r, z, q, p)
            if (x, y) != (pos[0], pos[1]) and start_z is not None:
                prog.motions.append(ParsedMotion("rapid", (x, y, start_z)))
            dc = DrillCycle(x, y, r, z, feed or 0.0, peck=q if motion_mode == 83 else 0.0,
                            dwell=p if motion_mode == 82 else 0.0)
            for m in expand_drill(dc):
                if isinstance(m, Rapid):
                    prog.motions.append(ParsedMotion("rapid", m.to))
                elif isinstance(m, Linear):
                    prog.motions.append(ParsedMotion("linear", m.to, feed=feed))
            final_z = r if retract_mode == "G99" or start_z is None else max(start_z, r)
            if retract_mode == "G98" and start_z is not None and start_z > r:
                prog.motions.append(ParsedMotion("rapid", (x, y, start_z)))
            pos = [x, y, final_z]
            continue
        new = [axes[k] * scale if axes[k] is not None else pos[k] for k in range(3)]
        if motion_mode == 0:
            prog.motions.append(ParsedMotion("rapid", tuple(new)))
        elif motion_mode == 1:
            prog.motions.append(ParsedMotion("linear", tuple(new), feed=feed))
        else:
            i_, j_, k_ = (vals.get(a, 0.0) * scale for a in "IJK")
            if not arc_incremental and pos[0] is not None:
                i_, j_ = i_ - pos[0], j_ - pos[1]
            prog.motions.append(ParsedMotion("arcCW" if motion_mode == 2 else "arcCCW",
                                             tuple(new), (i_, j_, k_), feed))
        pos = new
    return prog


def expected_motions(commands) -> list:
    """The motion a canonical command list must produce once posted
    (drill cycles expanded, a retract as a Z-only rapid)."""
    out = []
    cur = [None, None, None]
    for c in commands:
        subs = expand_drill(c) if isinstance(c, DrillCycle) else (c,)
        for s in subs:
            if isinstance(s, Rapid):
                out.append(ParsedMotion("rapid", s.to))
                cur = list(s.to)
            elif isinstance(s, RetractZ):
                out.append(ParsedMotion("rapid", (cur[0], cur[1], s.z)))
                cur[2] = s.z
            elif isinstance(s, Linear):
                out.append(ParsedMotion("linear", s.to, feed=s.feed))
                cur = list(s.to)
            elif isinstance(s, Arc):
                out.append(ParsedMotion("arcCW" if s.clockwise else "arcCCW", s.to,
                                        (s.center[0], s.center[1], 0.0), s.feed))
                cur = list(s.to)
    return out


def round_trip(expected: list, parsed: list, tolerance: float = 0.001) -> Issue | None:
    """Compare motions; ``None`` when they agree, else a
    ``post_round_trip_failed`` issue naming the first difference."""
    if len(expected) != len(parsed):
        return Issue("post_round_trip_failed", ERROR,
                     {"reason": "count", "expected": len(expected), "actual": len(parsed)})
    for i, (e, p) in enumerate(zip(expected, parsed)):
        if e.kind != p.kind:
            return Issue("post_round_trip_failed", ERROR, {"reason": "kind", "motion": i + 1})
        for k in range(3):
            if e.to[k] is None or p.to[k] is None:
                if (e.to[k] is None) != (p.to[k] is None):
                    return Issue("post_round_trip_failed", ERROR,
                                 {"reason": "axis", "motion": i + 1})
                continue
            if abs(e.to[k] - p.to[k]) > tolerance:
                return Issue("post_round_trip_failed", ERROR,
                             {"reason": "endpoint", "motion": i + 1,
                              "delta": abs(e.to[k] - p.to[k])})
        if e.center is not None:
            if p.center is None or any(abs(e.center[k] - p.center[k]) > tolerance
                                       for k in range(2)):
                return Issue("post_round_trip_failed", ERROR, {"reason": "center", "motion": i + 1})
        if e.feed is not None:
            if p.feed is None or abs(e.feed - p.feed) > max(0.05, e.feed * 0.001):
                return Issue("post_round_trip_failed", ERROR, {"reason": "feed", "motion": i + 1})
    return None


def uses_controller_compensation(commands) -> bool:
    return any(isinstance(c, CutterCompensation) and c.mode != "off" for c in commands)
