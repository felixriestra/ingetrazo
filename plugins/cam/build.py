# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Turning extracted geometry into operations, with sensible defaults.

Qt-free: the dock calls these with an :class:`~.extract.Extraction` and a
:class:`~.state.CamState`, and tests call them with hand-built ones.

- :func:`add_operations` — the "Add from selection ▸ Profile / Pocket /
  Drill / Engrave / Face" menu: one operation per region, loop or path.
- :func:`suggest_operations` — the "Part → CAM" flow: from a board, every
  operation it plainly needs (blind holes pocketed to their depth, round
  through holes drilled when a drill of that size exists, other holes
  profiled from inside, the outline cut out with tabs).

Operation NAMES are English source strings (``"Outside profile"``); the
UI shows them through ``tr`` and the user may rename them.
"""
from __future__ import annotations

from .engine.issues import CamError
from .engine.models import (DrillingParameters, EngravingParameters, FacingParameters,
                            Operation, PocketParameters, ProfileParameters, Region, Strategy,
                            Tab)

KIND_NAMES = {
    "outsideProfile": "Outside profile",
    "insideProfile": "Inside profile",
    "pocket": "Pocket",
    "drilling": "Drilling",
    "engraving": "Engraving",
    "facing": "Facing",
}

#: A drill matches a round hole within this much (mm) of its diameter.
DRILL_MATCH_MM = 0.1


def end_mill(job, max_diameter: float | None = None):
    """The largest flat/bull-nose end mill (at most ``max_diameter``),
    lowest tool number first among equals."""
    mills = [t for t in job.tools if t.kind in ("flatEndMill", "bullNoseEndMill")]
    if max_diameter is not None:
        fitting = [t for t in mills if t.diameter < max_diameter]
        mills = fitting or mills
    if not mills:
        return job.tools[0] if job.tools else None
    return max(mills, key=lambda t: (t.diameter, -t.number))


def matching_drill(job, diameter: float):
    for t in sorted(job.tools, key=lambda t: t.number):
        if t.kind in ("drill", "spotDrill") and abs(t.diameter - diameter) <= DRILL_MATCH_MM:
            return t
    return None


def step_down_for(tool) -> float:
    """Half the diameter per pass (a conservative default for wood and
    plastics), never past the flute."""
    return round(min(tool.diameter * 0.5, tool.fluteLength), 3)


def adopt(state, ex) -> None:
    """Take the extraction's frame when the job has none, and grow the
    part bounds (and the automatic stock) to the new geometry."""
    if state.frame is None:
        state.frame = ex.frame
        if ex.thickness:
            state.job.stock.height = round(ex.thickness, 4)
            state.job.stock.align_origin_to_reference()
        if ex.group_uid:
            state.sourceGroup = ex.group_uid
    state.include_bounds(ex.all_points())


#: Names new operations get: the UI sets this to its ``tr`` so they are
#: born in the user's language (a name is the user's data from then on).
translate = None


def _name(state, kind) -> str:
    base = KIND_NAMES[kind]
    if translate is not None:
        base = translate(base)
    n = sum(1 for o in state.job.operations if o.kind == kind) + 1
    return base if n == 1 else f"{base} {n}"


def _through_depth(state, ex) -> float:
    return round(ex.thickness if ex.thickness else state.job.stock.height, 4)


def _profile(state, kind, loop, tool, depth, tabs=False) -> Operation:
    tabs_list = []
    if tabs:
        width = round(max(2 * tool.diameter, 6.0), 3)
        height = round(min(3.0, depth / 3.0), 3)
        tabs_list = [Tab(f, width, height) for f in (0.125, 0.375, 0.625, 0.875)]
    op = Operation(_name(state, kind), kind, tool.id,
                   parameters=ProfileParameters(depth=depth, stepDown=step_down_for(tool)),
                   strategy=Strategy(geometry=Region(list(loop.points)), tabs=tabs_list))
    return op


def add_operations(state, kind: str, ex=None) -> list:
    """New operations of ``kind`` for the extraction ``ex`` (``None`` for
    facing). They are appended to the job and returned."""
    job = state.job
    ops: list = []
    if kind == "facing":
        tool = end_mill(job)
        ops.append(Operation(_name(state, kind), kind, tool.id if tool else None,
                             parameters=FacingParameters(depth=0.5, stepoverFraction=0.7)))
        job.operations.extend(ops)
        return ops
    if ex is None:
        raise CamError("empty_selection")
    adopt(state, ex)
    tool = end_mill(job)
    if tool is None:
        raise CamError("missing_tool")
    through = _through_depth(state, ex)

    closed = [outer for outer, _h in ex.regions] + list(ex.loops)
    if kind == "outsideProfile":
        for loop in closed:
            ops.append(_profile(state, kind, loop, tool, through, tabs=True))
            job.operations.append(ops[-1])
    elif kind == "insideProfile":
        holes = [h for _o, hs in ex.regions for h in hs]
        for loop in holes or closed:
            depth = loop.depth if loop.depth else through
            ops.append(_profile(state, kind, loop, tool, depth))
            job.operations.append(ops[-1])
    elif kind == "pocket":
        regions = [(o, hs) for o, hs in ex.regions]
        if not regions and ex.loops:
            # Closed chains: the largest is the wall, the others islands.
            loops = sorted(ex.loops, key=lambda lp: -abs(_area(lp.points)))
            regions = [(loops[0], loops[1:])]
        for outer, holes in regions:
            depth = round(-outer.w, 4) if outer.w < -0.01 else min(5.0, through)
            op = Operation(_name(state, kind), kind, tool.id,
                           parameters=PocketParameters(depth=depth, stepDown=step_down_for(tool)),
                           strategy=Strategy(geometry=Region(list(outer.points),
                                                             [list(h.points) for h in holes])))
            ops.append(op)
            job.operations.append(op)
    elif kind == "drilling":
        circles = [lp.circle for lp in _all_loops(ex) if lp.circle]
        if not circles:
            raise CamError("no_points")
        diameter = circles[0][2]
        drill = matching_drill(job, diameter) or next(
            (t for t in job.tools if t.kind in ("drill", "spotDrill")), tool)
        op = Operation(_name(state, kind), kind, drill.id,
                       parameters=DrillingParameters(depth=through,
                                                     points=[(c[0], c[1], 0.0) for c in circles]))
        ops.append(op)
        job.operations.append(op)
    elif kind == "engraving":
        small = min(job.tools, key=lambda t: t.diameter)
        paths = [(p, False) for p in ex.paths] + [(lp.points, True) for lp in closed]
        if not paths:
            raise CamError("insufficient_points")
        for pts, is_closed in paths:
            op = Operation(_name(state, kind), kind, small.id,
                           parameters=EngravingParameters(points=[(p[0], p[1], 0.0) for p in pts],
                                                          depth=1.0, stepDown=0.5,
                                                          isClosed=is_closed))
            ops.append(op)
            job.operations.append(op)
    else:
        raise CamError("unsupported_operation", kind=kind)
    for op in ops:
        state.sources[op.id] = {"kind": ex.kind, "group": ex.group_uid}
    return ops


def suggest_operations(state, ex) -> list:
    """Everything a board plainly needs, in machining order: pockets, then
    drills, then inside profiles, then the outline cut out with tabs."""
    if ex.kind != "part":
        raise CamError("empty_selection")
    adopt(state, ex)
    job = state.job
    through = _through_depth(state, ex)
    pockets, drills, insides, outlines = [], [], [], []
    for outer, holes in ex.regions:
        outlines.append(outer)
        for h in holes:
            if h.through is False and h.depth:
                pockets.append(h)
            elif h.circle and matching_drill(job, h.circle[2]):
                drills.append(h)
            else:
                insides.append(h)
    ops = []
    for h in pockets:
        width = min(_extent(h.points))
        tool = end_mill(job, max_diameter=width)
        op = Operation(_name(state, "pocket"), "pocket", tool.id,
                       parameters=PocketParameters(depth=round(h.depth, 4),
                                                   stepDown=step_down_for(tool)),
                       strategy=Strategy(geometry=Region(list(h.points))))
        job.operations.append(op)
        ops.append(op)
    by_drill: dict = {}
    for h in drills:
        by_drill.setdefault(matching_drill(job, h.circle[2]).id, []).append(h)
    for tool_id, hs in by_drill.items():
        op = Operation(_name(state, "drilling"), "drilling", tool_id,
                       parameters=DrillingParameters(
                           depth=through, points=[(h.circle[0], h.circle[1], 0.0) for h in hs]),
                       strategy=Strategy(ordering="nearestNeighbor"))
        job.operations.append(op)
        ops.append(op)
    for h in insides:
        width = min(_extent(h.points))
        tool = end_mill(job, max_diameter=width)
        op = _profile(state, "insideProfile", h, tool, through)
        job.operations.append(op)
        ops.append(op)
    tool = end_mill(job)
    for outer in outlines:
        op = _profile(state, "outsideProfile", outer, tool, through, tabs=True)
        job.operations.append(op)
        ops.append(op)
    for op in ops:
        state.sources[op.id] = {"kind": "part", "group": ex.group_uid}
    return ops


def _all_loops(ex):
    for outer, holes in ex.regions:
        yield outer
        yield from holes
    yield from ex.loops


def _area(pts) -> float:
    s = 0.0
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        s += a[0] * b[1] - b[0] * a[1]
    return s * 0.5


def _extent(pts):
    us = [p[0] for p in pts]
    vs = [p[1] for p in pts]
    return (max(us) - min(us), max(vs) - min(vs))
