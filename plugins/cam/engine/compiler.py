# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Operation list → one canonical toolpath (port of 2DCam's
``OperationToolpathCompiler``).

Enabled operations are compiled in order, each after the checks 2DCam runs:
the tool exists and is valid, the machine can spin and feed it, and the
cut is within the flute (and within a user or manufacturer depth-per-pass
limit). Each operation's commands are appended, its sections re-based and
tagged with the operation, and its time estimated.

The port adds, per operation, an ``Operation: <name>`` comment and a
:class:`RetractZ` to the safe height before the first travel move — the
first rapid after a tool change or at program start then lifts before it
moves, whatever height the spindle was left at.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .issues import CamError, Issue, WARNING
from .models import FINISHING_TOOL_KINDS, PARAMETER_TYPES
from .ops import drill, engrave, facing, pocket, profile
from .toolpath import (Arc, Comment, Coolant, DrillCycle, Dwell, Linear, Rapid, RetractZ, Section,
                       Toolpath)


@dataclass
class CompileResult:
    toolpath: Toolpath
    operation_command_counts: dict = field(default_factory=dict)
    operation_durations: dict = field(default_factory=dict)
    operation_ranges: dict = field(default_factory=dict)   # id -> (start, stop)
    warnings: list = field(default_factory=list)


def compile_job(job, *, cancelled=None) -> CompileResult:
    """Compile every enabled operation of ``job``. Raises :class:`CamError`
    (with the operation attached to its issue) on the first failure.
    ``cancelled`` is polled between operations (worker threads)."""
    if not job.tolerance.is_valid:
        raise CamError("invalid_tolerance")
    enabled = [op for op in job.operations if op.isEnabled]
    if not enabled:
        raise CamError("no_operations")
    commands: list = []
    sections: list = []
    result = CompileResult(Toolpath([], [], job.tolerance))
    for op in enabled:
        if cancelled is not None and cancelled():
            raise CamError("cancelled")
        try:
            generated, warnings = compile_operation(job, op)
        except CamError as exc:
            exc.issue.operation_id = op.id
            exc.issue.operation_name = op.name
            raise
        for w in warnings:
            w.operation_id, w.operation_name = op.id, op.name
        result.warnings.extend(warnings)
        offset = len(commands)
        commands.extend(generated.commands)
        for s in generated.sections:
            sections.append(Section(s.kind, offset + s.start, s.count, op.id, op.name))
        result.operation_command_counts[op.id] = len(generated.commands)
        result.operation_durations[op.id] = generated.estimated_duration(job.machine)
        result.operation_ranges[op.id] = (offset, len(commands))
    result.toolpath = Toolpath(commands, sections, job.tolerance)
    return result


def compile_operation(job, op):
    """One operation to its own toolpath (indexed from zero) and the
    warnings it raised."""
    warnings: list = []
    tool = job.tool(op.toolID)
    if tool is None:
        raise CamError("missing_tool")
    _validate_tool(job, tool, warnings)
    _validate_load(op, tool)
    finishing = tool
    if op.kind in FINISHING_TOOL_KINDS and op.finishingToolID:
        finishing = job.tool(op.finishingToolID)
        if finishing is None:
            raise CamError("missing_tool")
        if finishing.id != tool.id:
            _validate_tool(job, finishing, warnings)
            _validate_load(op, finishing)
    expected = PARAMETER_TYPES.get(op.kind)
    if expected is None:
        raise CamError("unsupported_operation", kind=op.kind)
    if not isinstance(op.parameters, expected):
        raise CamError("parameter_mismatch", kind=op.kind)

    st, setup, p, tol = op.strategy, job.setup, op.parameters, job.tolerance
    k = op.kind
    if k == "facing":
        path = facing.generate(job.stock, st.resolved_setup(setup), tool, p, st, tol)
    elif k in ("outsideProfile", "insideProfile"):
        path = profile.generate(job.stock, setup, tool, p, st, k == "outsideProfile", tol,
                                finishing_tool=finishing)
    elif k == "pocket":
        path = pocket.generate(job.stock, setup, tool, p, st, tol, finishing_tool=finishing)
    elif k == "drilling":
        ordered = drill.nearest_neighbor(p.points) if st.ordering == "nearestNeighbor" \
            else list(p.points)
        from dataclasses import replace
        path = drill.generate(job.stock, st.resolved_setup(setup), tool,
                              replace(p, points=ordered), st, tol)
    elif k == "engraving":
        path = engrave.generate(job.stock, st.resolved_setup(setup), tool, p, st, tol)
    else:                                     # pragma: no cover — guarded above
        raise CamError("unsupported_operation", kind=k)
    _validate_finite(path)
    return _with_preamble(path, op, job, tool), warnings


def _with_preamble(path: Toolpath, op, job, tool) -> Toolpath:
    """Prefix the operation-name comment and, right after the spindle
    start of the header, a retract to the operation's safe height."""
    safe_z = job.stock.top_z + op.strategy.topHeight \
        + op.strategy.resolved_setup(job.setup).safeHeight
    head = [Comment("Operation: {name}", {"name": op.name})]
    cmds = list(path.commands)
    # The header is Comment, ToolChange, SpindleStart, Coolant: retract after it.
    at = next((i + 1 for i, c in enumerate(cmds[:6]) if isinstance(c, Coolant)), 0)
    cmds.insert(at, RetractZ(safe_z))
    shift = len(head)
    sections = []
    for s in path.sections:
        start = s.start + shift + (1 if s.start >= at else 0)
        sections.append(Section(s.kind, start, s.count + (1 if s.start < at < s.stop else 0),
                                s.operation_id, s.operation_name))
    return Toolpath(head + cmds, sections, path.tolerance)


def _validate_tool(job, tool, warnings) -> None:
    m = job.machine
    if not tool.is_valid:
        raise CamError("invalid_tool", number=tool.number)
    if tool.spindleRPM > m.maximumSpindleRPM:
        raise CamError("spindle_above_machine", rpm=tool.spindleRPM, limit=m.maximumSpindleRPM)
    if tool.spindleRPM < m.minimumSpindleRPM:
        warnings.append(Issue("spindle_below_machine", WARNING,
                              {"rpm": tool.spindleRPM, "limit": m.minimumSpindleRPM}))
    if max(tool.cuttingFeed, tool.plungeFeed) > m.maximumFeed:
        raise CamError("feed_above_machine", feed=max(tool.cuttingFeed, tool.plungeFeed),
                       limit=m.maximumFeed)
    if m.maximumPlungeFeed is not None and tool.plungeFeed > m.maximumPlungeFeed:
        raise CamError("plunge_above_machine", feed=tool.plungeFeed, limit=m.maximumPlungeFeed)


def machining_load(op):
    """``(axial depth, depth per pass)`` of an operation (2DCam's
    ``machiningLoad``)."""
    p = op.parameters
    if op.kind == "facing":
        return p.depth, p.depth
    if op.kind in ("outsideProfile", "insideProfile", "pocket", "engraving"):
        return p.depth, p.stepDown
    if op.kind == "drilling":
        return p.depth, p.depth
    return None, None


def _validate_load(op, tool) -> None:
    axial, per_pass = machining_load(op)
    if axial is not None and axial > tool.fluteLength + 1e-9:
        raise CamError("axial_depth_exceeds_flute", depth=axial, flute=tool.fluteLength)
    if per_pass is not None and per_pass > tool.fluteLength + 1e-9:
        raise CamError("pass_depth_exceeds_flute", depth=per_pass, flute=tool.fluteLength)
    limit = tool.authoritative_stepdown
    if limit is not None and per_pass is not None and per_pass > limit + 1e-9:
        raise CamError("pass_depth_exceeds_recommended", depth=per_pass, limit=limit)


def _validate_finite(path: Toolpath) -> None:
    for c in path.commands:
        ok = True
        if isinstance(c, Rapid):
            ok = all(math.isfinite(v) for v in c.to)
        elif isinstance(c, RetractZ):
            ok = math.isfinite(c.z)
        elif isinstance(c, Linear):
            ok = all(math.isfinite(v) for v in c.to) and math.isfinite(c.feed) and c.feed > 0
        elif isinstance(c, Arc):
            ok = (all(math.isfinite(v) for v in (*c.to, *c.center))
                  and math.isfinite(c.feed) and c.feed > 0)
        elif isinstance(c, DrillCycle):
            ok = all(math.isfinite(v) for v in (c.x, c.y, c.r, c.bottom, c.feed)) and c.feed > 0
        elif isinstance(c, Dwell):
            ok = math.isfinite(c.seconds) and c.seconds >= 0
        if not ok:
            raise CamError("non_finite_motion")
