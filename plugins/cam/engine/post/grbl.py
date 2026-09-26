# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""GRBL 1.1 (the grblHAL/FluidNC-compatible subset).

What GRBL cannot do shapes this post:

- **No tool changer, no M6, no G43.** A job with several tools becomes
  ONE FILE PER TOOL CHANGE (``job_01_T1_6-mm-flat-end-mill.nc``, …), numbered
  in running order, each complete —
  units, work offset, spindle, end — and each opening with the comment to
  load that tool and set Z zero, since a new tool has a new length.
  Consecutive operations with the same tool stay in one file.
- **No canned cycles.** Drilling (and peck drilling) is written out as
  the plain G0/G1 moves of the cycle.
- **No cutter compensation.** A job asking the controller to offset the
  path (G41/G42) is refused with ``post_compensation_unsupported``.
- Coolant (M8/M9) only when the job says the machine has it, and comments
  are kept short: GRBL's line buffer is 80 characters and not every
  sender strips comments before streaming.
"""
from __future__ import annotations

from ..issues import CamError
from ..toolpath import (Arc, Comment, Coolant, CutterCompensation, DrillCycle, Dwell, Linear,
                        Rapid, RetractZ, SpindleStart, SpindleStop, ToolChange, expand_drill)
from .base import PostResult, ProgramFile, Writer, ascii_comment, header_lines, slug

TITLE = "GRBL"


class GrblWriter(Writer):
    title = TITLE
    extension = ".nc"
    # Two parentheses short of GRBL 1.1's 80-character line buffer.
    comment_limit = 76


def _chunks(commands) -> list:
    """Split at every change to a DIFFERENT tool; comments that lead into
    the change travel with it."""
    chunks, cur, tool = [], [], None
    for c in commands:
        if isinstance(c, ToolChange) and tool is not None and c.number != tool:
            lead = []
            while cur and isinstance(cur[-1], Comment):
                lead.insert(0, cur.pop())
            chunks.append((tool, cur))
            cur = lead
        if isinstance(c, ToolChange):
            tool = c.number
        cur.append(c)
    if cur:
        chunks.append((tool, cur))
    return chunks


def post(job, toolpath, translate=None) -> PostResult:
    if any(isinstance(c, CutterCompensation) and c.mode != "off" for c in toolpath.commands):
        raise CamError("post_compensation_unsupported", controller=TITLE)
    result = PostResult()
    chunks = _chunks(toolpath.commands)
    multi = len({t for t, _ in chunks}) > 1 or len(chunks) > 1
    for seq, (tool_number, cmds) in enumerate(chunks, 1):
        w = GrblWriter(job, translate)
        _program(w, job, tool_number, cmds, translate)
        tool = job.tool_by_number(tool_number)
        # The run order leads the name: the same tool may come back later
        # (T1, T3, T1), and each file must be its own — and sort in order.
        suffix = (f"_{seq:02d}_T{tool_number}_{slug(tool.name if tool else 'tool')}"
                  if multi else "")
        result.files.append(ProgramFile(suffix, w.extension, w.text(),
                                        [tool_number] if tool_number is not None else [],
                                        w.command_lines))
        result.files[-1].expected = w.expected
    return result


def _program(w: GrblWriter, job, tool_number, cmds, translate) -> None:
    lines = header_lines(job, translate, TITLE, w.comment_limit)
    w.lines.extend(lines)
    tool = job.tool_by_number(tool_number) if tool_number is not None else None
    if tool is not None:
        tpl = "Load T{number} ({name}), set Z zero, then run this file"
        s = (translate(tpl) if translate else tpl)
        try:
            s = s.format(number=tool.number, name=tool.name)
        except (KeyError, IndexError, ValueError):
            s = tpl.format(number=tool.number, name=tool.name)
        w.emit(f"({ascii_comment(s, w.comment_limit)})")
    w.emit("G90 G94 G17")
    w.emit("G20" if job.is_inch else "G21")
    w.emit(job.setup.workOffset)
    spindle_on = False
    for c in cmds:
        if isinstance(c, Comment):
            w.comment(c)
        elif isinstance(c, ToolChange):
            t = job.tool_by_number(c.number)
            if c.number <= 0:
                raise CamError("post_invalid_tool_number", number=c.number)
            w.comment(Comment("Tool T{number}: {name}",
                              {"number": c.number, "name": t.name if t else ""}))
        elif isinstance(c, SpindleStart):
            if c.rpm <= 0:
                raise CamError("post_invalid_spindle_speed", rpm=c.rpm)
            w.emit(f"S{c.rpm} {'M3' if c.clockwise else 'M4'}")
            spindle_on = True
            if job.post.spindleDwell and job.machine.spindleRampSeconds > 0:
                w.dwell(job.machine.spindleRampSeconds)
        elif isinstance(c, SpindleStop):
            w.emit("M5")
            spindle_on = False
        elif isinstance(c, Coolant):
            if job.post.coolant:
                w.emit("M8" if c.enabled else "M9")
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
            for s in expand_drill(c):
                if isinstance(s, Rapid):
                    w.rapid(s.to)
                elif isinstance(s, Linear):
                    w.linear(s.to, s.feed)
                elif isinstance(s, Dwell):
                    w.dwell(s.seconds)
        w.done()
    if spindle_on:
        w.emit("M5")
    if job.post.coolant:
        w.emit("M9")
    w.emit("M30")


def _check_feed(feed: float) -> None:
    import math
    if not (math.isfinite(feed) and feed > 0):
        raise CamError("post_invalid_feed", feed=feed)
