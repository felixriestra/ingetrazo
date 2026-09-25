# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""LinuxCNC 2.9+ (RS274NGC).

One file for the whole job:

- tool changes as ``T<n> M6`` then ``G43 H<n>`` (lengths from the
  machine's tool table — the header says so);
- drilling as canned cycles: ``G81`` (plain), ``G82`` (with dwell),
  ``G83`` (peck), each with ``G99`` (retract to R) and cancelled with
  ``G80`` on the next line;
- path blending ``G64 P<tolerance>``; arcs with incremental centres
  (``G91.1``, written explicitly), or flattened to lines when the job asks;
- cutter compensation ``G41``/``G42``/``G40`` when the operation leaves
  the offset to the controller;
- program end ``M5 M9 M2`` inside ``%`` markers.
"""
from __future__ import annotations

import math

from ..issues import CamError
from ..toolpath import (Arc, Comment, Coolant, CutterCompensation, DrillCycle, Dwell, Linear,
                        Rapid, RetractZ, SpindleStart, SpindleStop, ToolChange, expand_drill)
from .base import PostResult, ProgramFile, Writer, ascii_comment, header_lines

TITLE = "LinuxCNC"


class LinuxCncWriter(Writer):
    title = TITLE
    extension = ".ngc"


def post(job, toolpath, translate=None) -> PostResult:
    w = LinuxCncWriter(job, translate)
    w.flatten = bool(job.post.flattenArcs)
    w.emit("%")
    w.lines.extend(header_lines(job, translate, TITLE))
    w.emit("G20" if job.is_inch else "G21")
    w.emit("G17 G90 G91.1 G94 G40 G49 G80")
    w.emit(f"G64 P{w.num.coord(job.tolerance.linear)}")
    w.emit(job.setup.workOffset)
    tools_used = []
    for c in toolpath.commands:
        if isinstance(c, Comment):
            w.comment(c)
        elif isinstance(c, ToolChange):
            if c.number <= 0:
                raise CamError("post_invalid_tool_number", number=c.number)
            t = job.tool_by_number(c.number)
            w.comment(Comment("Tool T{number}: {name}",
                              {"number": c.number, "name": t.name if t else ""}))
            w.emit(f"T{c.number} M6")
            w.emit(f"G43 H{c.number}")
            # After M6 the spindle may stand anywhere: nothing is known.
            w.pos = [None, None, None]
            w.out = [None, None, None]
            if c.number not in tools_used:
                tools_used.append(c.number)
        elif isinstance(c, SpindleStart):
            if c.rpm <= 0:
                raise CamError("post_invalid_spindle_speed", rpm=c.rpm)
            w.emit(f"S{c.rpm} {'M3' if c.clockwise else 'M4'}")
            if job.post.spindleDwell and job.machine.spindleRampSeconds > 0:
                w.dwell(job.machine.spindleRampSeconds)
        elif isinstance(c, SpindleStop):
            w.emit("M5")
        elif isinstance(c, Coolant):
            if job.post.coolant:
                w.emit("M8" if c.enabled else "M9")
        elif isinstance(c, CutterCompensation):
            w.emit({"off": "G40", "left": "G41", "right": "G42"}[c.mode])
        elif isinstance(c, RetractZ):
            w.retract(c.z)
        elif isinstance(c, Rapid):
            w.rapid(c.to)
        elif isinstance(c, Linear):
            _check_feed(c.feed)
            w.linear(c.to, c.feed)
        elif isinstance(c, Arc):
            _check_feed(c.feed)
            w.arc(c)
        elif isinstance(c, Dwell):
            if c.seconds < 0:
                raise CamError("post_invalid_dwell", seconds=c.seconds)
            w.dwell(c.seconds)
        elif isinstance(c, DrillCycle):
            _check_feed(c.feed)
            _drill(w, c)
    if not isinstance(next((c for c in reversed(toolpath.commands)
                            if isinstance(c, (SpindleStart, SpindleStop))), None), SpindleStop):
        w.emit("M5")
    w.emit("M9")
    w.emit("M2")
    w.emit("%")
    result = PostResult()
    result.files.append(ProgramFile("", w.extension, w.text(), tools_used))
    result.files[-1].expected = w.expected
    result.tool_table = tool_table(job, tools_used)
    return result


def tool_table(job, numbers=None) -> str:
    """A LinuxCNC tool table (``.tbl``) for the job's tools — or just
    ``numbers`` — so ``T<n> M6`` finds every tool the program asks for.

    Diameters are real (cutter compensation uses them); lengths are 0,
    because only the machine can measure them: touch off each tool (or
    load this table into the machine's and keep its measured Z). Written
    in the job's units, which must match the machine's configuration."""
    k = 1 / 25.4 if job.is_inch else 1.0
    lines = [f";IngeTrazo CAM tool table - {'inch' if job.is_inch else 'mm'} - set Z per tool on the machine"]
    for t in sorted(job.tools, key=lambda t: t.number):
        if numbers is not None and t.number not in numbers:
            continue
        lines.append(f"T{t.number} P{t.number} D{t.diameter * k:.4f} Z+0.0000 "
                     f";{ascii_comment(t.name)}")
    return "\n".join(lines) + "\n"


def _drill(w: LinuxCncWriter, c: DrillCycle) -> None:
    from ..verify import ParsedMotion
    n = w.num
    if c.peck > 0:
        code, extra = "G83", f" Q{n.coord(c.peck)}"
    elif c.dwell > 0:
        code, extra = "G82", f" P{n.seconds(c.dwell)}"
    else:
        code, extra = "G81", ""
    feed = n.feed(c.feed)
    w.emit(f"G99 {code} X{n.coord(c.x)} Y{n.coord(c.y)} Z{n.coord(c.bottom)} "
           f"R{n.coord(c.r)}{extra} F{feed}")
    w.emit("G80")
    w.feed_out = feed
    for s in expand_drill(c):
        if isinstance(s, Rapid):
            w.expected.append(ParsedMotion("rapid", s.to))
        elif isinstance(s, Linear):
            w.expected.append(ParsedMotion("linear", s.to, feed=s.feed))
    w.pos = [c.x, c.y, c.r]
    w.out = [n.coord(c.x), n.coord(c.y), n.coord(c.r)]


def _check_feed(feed: float) -> None:
    if not (math.isfinite(feed) and feed > 0):
        raise CamError("post_invalid_feed", feed=feed)
