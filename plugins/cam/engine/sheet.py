# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The setup sheet's content — 2DCam's ``SetupSheet``.

A setup sheet is the page an operator keeps next to the machine: the stock
to clamp, where to set the work zero, the tools to load (with their speeds
and feeds), the operations in program order with their times, the files to
run, a top view of the toolpaths, and what to check before starting.

This module only gathers it, as numbers and codes (no Qt, no language);
``ui/setup_sheet.py`` words it in the user's language and draws the PDF.
The job is compiled afresh and left untouched.

Where the port differs from 2DCam:

- each operation is compiled on its own, so one that fails is named and
  the others keep their times and their place in the plan view (2DCam
  loses every time when one operation fails);
- it lists the **program files** in run order — on GRBL one per tool,
  which is what the operator actually loads;
- warnings are :class:`.issues.Issue` codes, not English sentences.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .compiler import compile_operation, machining_load
from .issues import CamError, Issue, WARNING
from .toolpath import Arc, DrillCycle, Linear, Rapid, RetractZ, Section, Toolpath, arc_points

#: Plan-view points kept at most; very large programs are thinned so the
#: PDF stays light (2DCam's budget).
POINT_BUDGET = 40_000
#: Drill marks drawn at most.
DRILL_LIMIT = 500
#: The sheet's own warnings are engine codes (``issues.CODES``,
#: ``sheet_*``), so the message table covers them like any other.


@dataclass
class SheetTool:
    number: int
    name: str
    kind: str
    diameter: float
    flute_length: float
    rpm: int
    feed: float
    plunge: float
    holder: tuple | None = None          # (diameter, length)
    included_angle: float | None = None
    #: Operation numbers (1-based, program order) that use it.
    used_by: list = field(default_factory=list)


@dataclass
class SheetOperation:
    number: int
    name: str
    kind: str
    enabled: bool
    tool_number: int | None = None
    tool_name: str | None = None
    finisher_number: int | None = None
    depth: float | None = None
    step: float | None = None
    holes: int | None = None
    seconds: float | None = None
    #: ``ok``, ``disabled``, ``no_tool`` or ``failed`` (then ``issue``).
    status: str = "ok"
    issue: Issue | None = None


@dataclass
class SheetFile:
    """One program file, named as export names it: the job's base name plus
    ``suffix`` and ``extension``."""
    suffix: str
    extension: str
    tool_numbers: list


@dataclass
class SetupSheet:
    job_name: str
    units: str                          # millimeters | inches
    controller: str
    stock: object                       # engine.models.Stock
    setup: object                       # engine.models.MachineSetup
    machine: object                     # engine.models.Machine
    tools: list
    operations: list
    #: ``(operation number, [(x, y), …])`` cutting moves seen from above.
    plan_paths: list
    #: ``(operation number, (x, y))`` holes.
    plan_points: list
    warnings: list
    files: list | None = None
    total_seconds: float | None = None
    tool_changes: int | None = None
    cutting_length: float | None = None

    @property
    def enabled_count(self) -> int:
        return sum(1 for o in self.operations if o.enabled)

    @property
    def has_tool_table(self) -> bool:
        """LinuxCNC export writes a ``.tbl`` beside the program."""
        return self.controller == "linuxcnc" and bool(self.files)


def build(job, post=True) -> SetupSheet:
    """The sheet for ``job`` (in WORK coordinates: ``CamState.work_job()``).
    ``post`` also names the program files (it posts the job, so it can be
    turned off where only the rest is needed)."""
    tools_by_id = {t.id: t for t in job.tools}
    usage: dict = {}
    rows, warnings = [], []
    commands, sections = [], []
    op_of_command: list = []
    failures = 0

    for index, op in enumerate(job.operations):
        number = index + 1
        tool = tools_by_id.get(op.toolID)
        finisher = tools_by_id.get(op.finishingToolID)
        depth, step = machining_load(op) if op.parameters is not None else (None, None)
        holes = (len(getattr(op.parameters, "points", []) or [])
                 if op.kind == "drilling" else None)
        row = SheetOperation(number, op.name, op.kind, op.isEnabled,
                             tool.number if tool else None, tool.name if tool else None,
                             finisher.number if finisher else None, depth,
                             step if step != depth else None, holes)
        rows.append(row)
        if not op.isEnabled:
            row.status = "disabled"
            continue
        if tool is None:
            row.status = "no_tool"
            warnings.append(Issue("sheet_operation_without_tool", WARNING,
                                  {"number": number, "name": op.name}))
            continue
        for t in (tool, finisher):
            if t is not None:
                usage.setdefault(t.id, []).append(number)
        try:
            generated, _ = compile_operation(job, op)
        except CamError as exc:
            failures += 1
            row.status = "failed"
            exc.issue.operation_id, exc.issue.operation_name = op.id, op.name
            row.issue = exc.issue
            warnings.append(exc.issue)
            continue
        row.seconds = generated.estimated_duration(job.machine)
        offset = len(commands)
        commands.extend(generated.commands)
        op_of_command.extend([number] * len(generated.commands))
        for s in generated.sections:
            sections.append(Section(s.kind, offset + s.start, s.count, op.id, op.name))

    if not job.operations:
        warnings.append(Issue("sheet_no_operations", WARNING))
    elif not any(op.isEnabled for op in job.operations):
        warnings.append(Issue("sheet_all_disabled", WARNING))
    warnings += _travel_warnings(job)

    # Tools actually loaded, by number; every tool when none is used yet.
    listed = [t for t in job.tools if not usage or t.id in usage]
    tool_rows = [SheetTool(t.number, t.name, t.kind, t.diameter, t.fluteLength, t.spindleRPM,
                           t.cuttingFeed, t.plungeFeed,
                           (t.holder.diameter, t.holder.length) if t.holder else None,
                           t.includedAngle if t.kind == "chamferMill" else None,
                           sorted(set(usage.get(t.id, []))))
                 for t in sorted(listed, key=lambda t: t.number)]

    sheet = SetupSheet(job.name, job.units, job.post.controller, job.stock, job.setup,
                       job.machine, tool_rows, rows, [], [], warnings)
    if not commands:
        return sheet
    path = Toolpath(commands, sections, job.tolerance)
    sheet.plan_paths = plan_paths(path, op_of_command)
    sheet.plan_points = drill_points(path, op_of_command)
    stats = path.statistics()
    sheet.tool_changes = stats.toolChangeCount
    sheet.cutting_length = stats.cuttingLength
    if failures == 0:
        # With a failure the total would silently leave an operation out.
        sheet.total_seconds = path.estimated_duration(job.machine)
        if post:
            sheet.files = _files(job, path)
    return sheet


def _files(job, path) -> list | None:
    from .post import post_job
    try:
        result = post_job(job, path)
    except CamError:
        return None
    return [SheetFile(f.suffix, f.extension, list(f.tool_numbers)) for f in result.files]


def _travel_warnings(job) -> list:
    m, st = job.machine, job.stock
    if not m.enforcesTravelLimits:
        return []
    if not m.has_valid_travel_envelope:
        return [Issue("sheet_travel_invalid", WARNING)]
    out = []
    spans = [m.travelMaximum[k] - m.travelMinimum[k] for k in range(3)]
    for code, size, span in (("sheet_stock_exceeds_x", st.width, spans[0]),
                             ("sheet_stock_exceeds_y", st.depth, spans[1]),
                             ("sheet_stock_exceeds_z", st.height, spans[2])):
        if size > span:
            out.append(Issue(code, WARNING, {"size": size, "travel": span}))
    return out


# ---- the plan view -------------------------------------------------------------

def plan_paths(path: Toolpath, op_of_command: list, budget: int = POINT_BUDGET) -> list:
    """Cutting moves seen from above, one polyline per continuous cut and
    operation; arcs sampled; thinned over ``budget`` points."""
    out, open_pts, open_op = [], [], 0
    pos = None

    def flush():
        nonlocal open_pts
        if len(open_pts) > 1:
            out.append((open_op, open_pts))
        open_pts = []

    def extend(pts, op):
        nonlocal open_op, open_pts
        if (open_pts and op == open_op
                and math.hypot(open_pts[-1][0] - pts[0][0], open_pts[-1][1] - pts[0][1]) < 1e-6):
            open_pts.extend(pts[1:])
        else:
            flush()
            open_op, open_pts = op, list(pts)

    for i, c in enumerate(path.commands):
        op = op_of_command[i] if i < len(op_of_command) else 0
        if isinstance(c, Rapid):
            flush()
            pos = c.to
        elif isinstance(c, RetractZ):
            flush()
            if pos is not None:
                pos = (pos[0], pos[1], c.z)
        elif isinstance(c, Linear):
            if pos is not None and math.hypot(c.to[0] - pos[0], c.to[1] - pos[1]) > 1e-9:
                extend([(pos[0], pos[1]), (c.to[0], c.to[1])], op)
            pos = c.to
        elif isinstance(c, Arc):
            if pos is not None:
                extend([(p[0], p[1]) for p in arc_points(pos, c, max_step_deg=7.5)], op)
            pos = c.to
        elif isinstance(c, DrillCycle):
            flush()
            pos = (c.x, c.y, c.r)
    flush()
    total = sum(len(p) for _n, p in out)
    if total <= budget:
        return out
    stride = math.ceil(total / budget)
    thinned = []
    for n, pts in out:
        kept = pts[::stride]
        if kept[-1] != pts[-1]:
            kept.append(pts[-1])
        thinned.append((n, kept))
    return thinned


def drill_points(path: Toolpath, op_of_command: list, limit: int = DRILL_LIMIT) -> list:
    """Every hole once, whatever the pecking."""
    seen, out = set(), []
    for i, c in enumerate(path.commands):
        if isinstance(c, DrillCycle):
            op = op_of_command[i] if i < len(op_of_command) else 0
            key = (op, round(c.x * 1000), round(c.y * 1000))
            if key not in seen and len(out) < limit:
                seen.add(key)
                out.append((op, (c.x, c.y)))
    return out


def compact_ranges(numbers) -> str:
    """``[1, 2, 3, 5, 7, 8]`` → ``1–3, 5, 7, 8`` (2DCam's rule: two in a
    row stay a pair, three or more become a range)."""
    s = sorted(set(numbers))
    parts, i = [], 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[j] + 1:
            j += 1
        if j == i:
            parts.append(str(s[i]))
        elif j - i == 1:
            parts.append(f"{s[i]}, {s[j]}")
        else:
            parts.append(f"{s[i]}–{s[j]}")
        i = j + 1
    return ", ".join(parts)


def duration(seconds: float | None) -> str:
    """``h:mm:ss``, or a dash."""
    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return "—"
    total = int(math.floor(seconds + 0.5))
    return f"{total // 3600}:{(total % 3600) // 60:02d}:{total % 60:02d}"
