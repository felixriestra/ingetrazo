# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""What every post-processor shares: number formatting, ASCII comments,
the program header, and the motion each canonical command becomes.

Ported from the base of 2DCam's ``GCodePostprocessor`` (capabilities,
program assembly, the number formatter); the GRBL and LinuxCNC dialects on
top of it are new. Rules that hold for both:

- **G-code is never translated.** Words are the same in every language and
  the decimal separator is always ``.`` — numbers are formatted with
  ``repr``-free f-strings, which ignore the locale.
- **Comments are ASCII.** Operation and tool names follow the job's
  language, then lose their accents (``Cajeado ñ`` → ``Cajeado n``) and
  any character a sender might choke on; parentheses become brackets.
- **Units convert here and only here.** The toolpath is in millimetres;
  an inch job is divided by 25.4 as each number is written, feeds too.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field

from ..models import MM_PER_INCH
from ..toolpath import Arc, Comment, arc_points

#: Header comment templates, translated like the generators' comments.
HEADER_TEMPLATES = (
    "IngeTrazo CAM - {controller}",
    "Job: {name}",
    "Units: {units}",
    "Stock: {width} x {depth} x {height} {unit}",
    "Work zero: {origin}",
    "Safe height: {safe} {unit}",
    "Load T{number} ({name}), set Z zero, then run this file",
    "Check the toolpath with an air cut before the first real cut",
)

#: Header wording of the work zero (translated like the comments).
POINT_NAMES = {
    "bottomLeft": "front-left corner", "bottomCenter": "front edge centre",
    "bottomRight": "front-right corner", "centerLeft": "left edge centre",
    "center": "centre", "centerRight": "right edge centre",
    "topLeft": "back-left corner", "topCenter": "back edge centre",
    "topRight": "back-right corner",
}
ZERO_NAMES = {"materialSurface": "stock top", "machineBed": "stock bottom"}


#: Arcs whose radius is below this post as a straight move: some
#: controllers reject them (GRBL's radius check), and at this size the
#: difference is below every tolerance.
MIN_ARC_RADIUS_MM = 0.01


def ascii_comment(text: str, limit: int | None = None) -> str:
    """``text`` reduced to printable ASCII, safe inside ``( … )``."""
    t = unicodedata.normalize("NFKD", str(text))
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = t.replace("(", "[").replace(")", "]").replace("\n", " ").replace("\r", " ")
    t = t.replace("·", "-").replace("–", "-").replace("—", "-").replace("×", "x")
    t = "".join(ch for ch in t if 32 <= ord(ch) < 127)
    t = re.sub(r"\s+", " ", t).strip()
    if limit is not None and len(t) > limit:
        t = t[:limit].rstrip()
    return t


@dataclass
class Numbers:
    """Formats millimetre values for the output unit. Three decimals in mm
    and five in inches keep every coordinate within 0.001 mm of the
    toolpath, which the round-trip check holds the post to."""
    inch: bool = False

    @property
    def scale(self) -> float:
        return 1.0 / MM_PER_INCH if self.inch else 1.0

    def coord(self, mm: float) -> str:
        return _fmt(mm * self.scale, 5 if self.inch else 3)

    def feed(self, mm_per_min: float) -> str:
        return _fmt(mm_per_min * self.scale, 3 if self.inch else 1)

    def seconds(self, s: float) -> str:
        return _fmt(s, 2)


def _fmt(value: float, decimals: int) -> str:
    if abs(value) < 0.5 * 10 ** -decimals:
        value = 0.0
    s = f"{value:.{decimals}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


@dataclass
class ProgramFile:
    """One file to write: ``suffix`` is appended to the job's base name
    (empty for a single file), ``tool_numbers`` the tools it uses."""
    suffix: str
    extension: str
    text: str
    tool_numbers: list = field(default_factory=list)


@dataclass
class PostResult:
    files: list = field(default_factory=list)
    issues: list = field(default_factory=list)


def arc_as_motion(start, c: Arc, *, flatten: bool, chordal: float):
    """What a canonical arc becomes: itself, or straight moves when the
    post flattens arcs or the radius is too small to post as an arc.
    Returns ``("arc", c)`` or ``("lines", [points…])``."""
    r = math.hypot(c.center[0], c.center[1])
    if not flatten and r >= MIN_ARC_RADIUS_MM:
        return "arc", c
    if r < MIN_ARC_RADIUS_MM:
        return "lines", [c.to]
    return "lines", arc_points(start, c, max_step_deg=chord_step_degrees(r, chordal))[1:]


def chord_step_degrees(radius: float, chordal: float) -> float:
    """The largest angle whose chord stays within ``chordal`` of an arc of
    ``radius`` (clamped to 1°–45°)."""
    if chordal >= radius:
        return 45.0
    step = math.degrees(2 * math.acos(max(-1.0, min(1.0, 1 - chordal / radius))))
    return max(1.0, min(45.0, step))


def header_lines(job, translate, controller_title: str, limit=None) -> list:
    """The ``( … )`` lines every program starts with."""
    def T(tpl, **kw):
        s = translate(tpl) if translate else tpl
        try:
            s = s.format(**kw)
        except (KeyError, IndexError, ValueError):
            s = tpl.format(**kw)
        return f"({ascii_comment(s, limit)})"

    n = Numbers(job.is_inch)
    unit = "in" if job.is_inch else "mm"
    st = job.stock
    tx = translate or (lambda s: s)
    origin = (f"{tx(POINT_NAMES.get(st.referencePoint, st.referencePoint))}, "
              f"{tx(ZERO_NAMES.get(st.zeroPosition, st.zeroPosition))}")
    return [
        T("IngeTrazo CAM - {controller}", controller=controller_title),
        T("Job: {name}", name=job.name),
        T("Units: {units}", units=unit),
        T("Stock: {width} x {depth} x {height} {unit}", width=n.coord(st.width),
          depth=n.coord(st.depth), height=n.coord(st.height), unit=unit),
        T("Work zero: {origin}", origin=origin),
        T("Safe height: {safe} {unit}", safe=n.coord(job.setup.safeHeight), unit=unit),
        T("Check the toolpath with an air cut before the first real cut"),
    ]


def comment_line(c: Comment, translate, limit=None) -> str:
    return f"({ascii_comment(c.render(translate), limit)})"


def slug(text: str) -> str:
    s = ascii_comment(text).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "tool"


class Writer:
    """Emits the motion of a toolpath in one dialect, modally, and records
    the motion it meant to write (``expected``, in millimetres) for the
    round-trip check. Dialects subclass it and override the hooks."""

    title = "G-code"
    extension = ".nc"
    comment_limit: int | None = None

    def __init__(self, job, translate=None) -> None:
        self.job = job
        self.translate = translate
        self.num = Numbers(job.is_inch)
        self.lines: list = []
        self.expected: list = []
        self.issues: list = []
        self.pos = [None, None, None]         # mm, exact
        self.out = [None, None, None]         # the strings last written per axis
        self.feed_out = None
        self.flatten = False

    # -- emission helpers --
    def emit(self, line: str) -> None:
        self.lines.append(line)

    def comment(self, c: Comment) -> None:
        self.emit(comment_line(c, self.translate, self.comment_limit))

    def _axes(self, to, force_all=False) -> str:
        words = []
        strs = [self.num.coord(v) for v in to]
        for k, axis in enumerate("XYZ"):
            if force_all or strs[k] != self.out[k]:
                words.append(f"{axis}{strs[k]}")
        if not words:                                  # a zero-length move: say where
            words = [f"{a}{s}" for a, s in zip("XYZ", strs)]
        self.out = strs
        return " ".join(words)

    def _feed(self, f: float) -> str:
        s = self.num.feed(f)
        if s == self.feed_out:
            return ""
        self.feed_out = s
        return f" F{s}"

    def _nowhere(self, to) -> bool:
        """A move to where the tool already is, as written: skipped. It
        does nothing on the machine and upsets LinuxCNC's cutter
        compensation, which needs a direction for every move."""
        return (None not in self.out
                and [self.num.coord(v) for v in to] == self.out)

    def rapid(self, to) -> None:
        from ..verify import ParsedMotion
        if self._nowhere(to):
            self.pos = list(to)
            return
        self.emit(f"G0 {self._axes(to)}")
        self.expected.append(ParsedMotion("rapid", tuple(to)))
        self.pos = list(to)

    def retract(self, z: float) -> None:
        from ..verify import ParsedMotion
        s = self.num.coord(z)
        if s == self.out[2]:
            self.pos[2] = z
            return
        self.emit(f"G0 Z{s}")
        self.out[2] = s
        self.expected.append(ParsedMotion("rapid", (self.pos[0], self.pos[1], z)))
        self.pos[2] = z

    def linear(self, to, feed: float) -> None:
        from ..verify import ParsedMotion
        if self._nowhere(to):
            self.pos = list(to)
            return
        self.emit(f"G1 {self._axes(to)}{self._feed(feed)}")
        self.expected.append(ParsedMotion("linear", tuple(to), feed=feed))
        self.pos = list(to)

    def arc(self, c: Arc) -> None:
        from ..verify import ParsedMotion
        start = tuple(self.pos)
        if None in start:
            self.linear(c.to, c.feed)          # no known start: cannot be an arc
            return
        kind, what = arc_as_motion(start, c, flatten=self.flatten,
                                   chordal=self.job.tolerance.chordal)
        if kind == "lines":
            for p in what:
                self.linear(p, c.feed)
            return
        strs = [self.num.coord(v) for v in c.to]
        words = [f"X{strs[0]}", f"Y{strs[1]}"]
        if strs[2] != self.out[2]:
            words.append(f"Z{strs[2]}")
        self.out = strs
        i, j = self.num.coord(c.center[0]), self.num.coord(c.center[1])
        g = "G2" if c.clockwise else "G3"
        self.emit(f"{g} {' '.join(words)} I{i} J{j}{self._feed(c.feed)}")
        self.expected.append(ParsedMotion("arcCW" if c.clockwise else "arcCCW", tuple(c.to),
                                          (c.center[0], c.center[1], 0.0), c.feed))
        self.pos = list(c.to)

    def dwell(self, seconds: float) -> None:
        self.emit(f"G4 P{self.num.seconds(seconds)}")

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"
