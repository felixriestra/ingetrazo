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
import math

from .engine.models import (BoreParameters, ChamferParameters, DrillingParameters,
                            EngravingParameters, FacingParameters, OpenPocketParameters,
                            Operation, PocketParameters, ProfileParameters, Region,
                            SlotParameters, Strategy, Tab)

KIND_NAMES = {
    "outsideProfile": "Outside profile",
    "insideProfile": "Inside profile",
    "pocket": "Pocket",
    "drilling": "Drilling",
    "engraving": "Engraving",
    "facing": "Facing",
    "bore": "Bore",
    "slot": "Slot",
    "chamfer": "Chamfer",
    "openPocket": "Open pocket",
}

#: A drill matches a round hole within this much (mm) of its diameter.
DRILL_MATCH_MM = 0.1


def end_mill(job, max_diameter: float | None = None, depth: float | None = None):
    """The largest flat/bull-nose end mill (thinner than ``max_diameter``),
    lowest tool number first among equals — preferring, when ``depth`` is
    given, one whose flute reaches that deep."""
    mills = [t for t in job.tools if t.kind in ("flatEndMill", "bullNoseEndMill")]
    if max_diameter is not None:
        fitting = [t for t in mills if t.diameter < max_diameter]
        mills = fitting or mills
    if depth is not None:
        long_enough = [t for t in mills if t.fluteLength >= depth - 1e-9]
        mills = long_enough or mills
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
    outline_before = state.bounds
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
    elif kind == "bore":
        circles = [lp for lp in _all_loops(ex) if lp.circle]
        if not circles:
            raise CamError("no_points")
        for lp in circles:
            cu, cv, dia = lp.circle
            mill = end_mill(job, max_diameter=dia, depth=lp.depth if lp.depth else through)
            if mill is None or mill.diameter >= dia:
                raise CamError("bore_smaller_than_tool", bore=dia,
                               diameter=mill.diameter if mill else 0.0)
            depth = lp.depth if lp.depth else through
            op = Operation(_name(state, kind), kind, mill.id,
                           parameters=BoreParameters(center=(cu, cv, 0.0), diameter=round(dia, 4),
                                                     depth=depth, stepDown=step_down_for(mill)))
            ops.append(op)
            job.operations.append(op)
    elif kind == "slot":
        made = False
        for outer, _h in ex.regions:
            made |= _slot_from_loop(state, job, outer, through, ops)
        for lp in ex.loops:
            made |= _slot_from_loop(state, job, lp, through, ops)
        for path in ex.paths:
            if len(path) == 2:
                mill = end_mill(job)
                op = Operation(_name(state, kind), kind, mill.id, parameters=SlotParameters(
                    start=(path[0][0], path[0][1], 0.0), end=(path[1][0], path[1][1], 0.0),
                    width=mill.diameter, depth=min(5.0, through), stepDown=step_down_for(mill)))
                ops.append(op)
                job.operations.append(op)
                made = True
        if not made:
            raise CamError("insufficient_points")
    elif kind == "chamfer":
        vbit = next((t for t in sorted(job.tools, key=lambda t: t.number)
                     if t.kind == "chamferMill"), None)
        if vbit is None:
            raise CamError("chamfer_needs_chamfer_mill", number=0)
        half = math.radians((vbit.includedAngle or 90.0) / 2)
        width = 1.0
        depth = round(width / math.tan(half), 4)
        pieces = [(outer.points, True, False) for outer, _h in ex.regions]
        pieces += [(h.points, True, True) for _o, hs in ex.regions for h in hs]
        pieces += [(lp.points, True, False) for lp in ex.loops]
        pieces += [(p, False, False) for p in ex.paths]
        if not pieces:
            raise CamError("insufficient_points")
        for pts, closed, inside in pieces:
            op = Operation(_name(state, kind), kind, vbit.id, parameters=ChamferParameters(
                points=[(p[0], p[1]) for p in pts], width=width, depth=depth,
                isClosed=closed, inside=inside))
            ops.append(op)
            job.operations.append(op)
    elif kind == "openPocket":
        outline = outline_before
        for outer, holes in ex.regions:
            depth = round(-outer.w, 4) if outer.w < -0.01 else min(5.0, through)
            open_edges = _edges_on(outer.points, outline)
            op = Operation(_name(state, kind), kind, tool.id,
                           parameters=OpenPocketParameters(depth=depth,
                                                           stepDown=step_down_for(tool)),
                           strategy=Strategy(geometry=Region(list(outer.points),
                                                             [list(h.points) for h in holes],
                                                             open_edges)))
            ops.append(op)
            job.operations.append(op)
        if not ops:
            raise CamError("empty_selection")
    else:
        raise CamError("unsupported_operation", kind=kind)
    for op in ops:
        state.sources[op.id] = {"kind": ex.kind, "group": ex.group_uid}
    return ops


def _slot_from_loop(state, job, loop, through, ops) -> bool:
    """A slot for a rectangular loop: along its long axis, as wide as its
    short side, the rows' ends moved in by the tool radius so the cut is
    the rectangle (with the tool's radius in its corners)."""
    pts = loop.points
    if len(pts) != 4:
        return False
    sides = [math.dist(pts[i], pts[(i + 1) % 4]) for i in range(4)]
    if abs(sides[0] - sides[2]) > 1e-3 or abs(sides[1] - sides[3]) > 1e-3:
        return False
    k = 0 if sides[0] >= sides[1] else 1           # a long side starts at pts[k]
    length, width = sides[k], sides[(k + 1) % 4]
    mill = end_mill(job, max_diameter=width + 1e-6)
    if mill is None or mill.diameter > width + 1e-9:
        raise CamError("slot_narrower_than_tool", width=width,
                       diameter=mill.diameter if mill else 0.0)
    r = mill.diameter * 0.5
    a, b = pts[k], pts[(k + 1) % 4]
    c = pts[(k + 2) % 4]
    ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
    mid0 = ((a[0] + pts[(k + 3) % 4][0]) * 0.5, (a[1] + pts[(k + 3) % 4][1]) * 0.5)
    mid1 = ((b[0] + c[0]) * 0.5, (b[1] + c[1]) * 0.5)
    start = (mid0[0] + ux * r, mid0[1] + uy * r, 0.0)
    end = (mid1[0] - ux * r, mid1[1] - uy * r, 0.0)
    depth = round(-loop.w, 4) if loop.w < -0.01 else min(5.0, through)
    op = Operation(_name(state, "slot"), "slot", mill.id, parameters=SlotParameters(
        start=start, end=end, width=round(width, 4), depth=depth,
        stepDown=step_down_for(mill)))
    ops.append(op)
    job.operations.append(op)
    return True


def _edges_on(points, bounds, tol: float = 0.05) -> list:
    """Indices of the edges of ``points`` that lie on the ``bounds``
    rectangle: an open pocket reaching the board's edge is open there."""
    if not bounds:
        return []
    u0, v0, u1, v1 = bounds
    out = []
    n = len(points)
    for i in range(n):
        a, b = points[i], points[(i + 1) % n]
        for fixed, axis in ((u0, 0), (u1, 0), (v0, 1), (v1, 1)):
            if abs(a[axis] - fixed) <= tol and abs(b[axis] - fixed) <= tol:
                out.append(i)
                break
    return out


def suggest_operations(state, ex) -> list:
    """Everything a board plainly needs, in machining order: pockets, then
    drills (round holes a drill in the table fits), then bores (other round
    holes, blind or through, milled round), then inside profiles, then the
    outline cut out with tabs."""
    if ex.kind != "part":
        raise CamError("empty_selection")
    adopt(state, ex)
    job = state.job
    through = _through_depth(state, ex)
    pockets, drills, bores, insides, outlines = [], [], [], [], []
    for outer, holes in ex.regions:
        outlines.append(outer)
        for h in holes:
            round_ok = bool(h.circle) and (end_mill(job, max_diameter=h.circle[2]) or
                                           job.tools[0]).diameter < h.circle[2]
            if h.through is False and h.depth:
                (bores if round_ok else pockets).append(h)
            elif h.circle and matching_drill(job, h.circle[2]):
                drills.append(h)
            elif round_ok:
                bores.append(h)
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
    for h in bores:
        cu, cv, dia = h.circle
        mill = end_mill(job, max_diameter=dia,
                        depth=h.depth if (h.through is False and h.depth) else through)
        op = Operation(_name(state, "bore"), "bore", mill.id, parameters=BoreParameters(
            center=(cu, cv, 0.0), diameter=round(dia, 4),
            depth=round(h.depth, 4) if (h.through is False and h.depth) else through,
            stepDown=step_down_for(mill)))
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


# ---- refreshing a job from its part ----------------------------------------------

#: A new outline replaces an operation's old one when their centres are
#: this close (mm): a board edited by a few centimetres still matches.
REFRESH_MATCH_MM = 50.0


def refresh_from_part(state, ex) -> tuple:
    """Re-read the geometry of every operation that came from the part.

    ``ex`` is a fresh :func:`~.extract.extract_part` on the job's frame.
    Each part operation takes the nearest loop of its own role — the
    outline for an outside profile, a hole for the others, round holes'
    centres for drilling — and keeps all its parameters. Returns
    ``(updated, unmatched)`` operation names; an unmatched operation keeps
    its old geometry, so nothing is silently cut in a new place."""
    outlines = [o for o, _h in ex.regions]
    holes = [h for _o, hs in ex.regions for h in hs]
    updated, unmatched = [], []
    for op in state.job.operations:
        if state.sources.get(op.id, {}).get("kind") != "part":
            continue
        if op.kind == "drilling":
            circles = [h.circle for h in holes if h.circle]
            pts = []
            for p in op.parameters.points:
                c = min(circles, key=lambda c: (c[0] - p[0]) ** 2 + (c[1] - p[1]) ** 2,
                        default=None)
                if c is None or (c[0] - p[0]) ** 2 + (c[1] - p[1]) ** 2 > REFRESH_MATCH_MM ** 2:
                    pts = None
                    break
                pts.append((c[0], c[1], 0.0))
            if pts is None:
                unmatched.append(op.name)
            else:
                op.parameters.points = pts
                updated.append(op.name)
            continue
        region = op.strategy.geometry
        if region is None:
            continue
        pool = outlines if op.kind == "outsideProfile" else holes
        old = _centroid(region.boundary)
        best = min(pool, key=lambda lp: _d2(_centroid(lp.points), old), default=None)
        if best is None or _d2(_centroid(best.points), old) > REFRESH_MATCH_MM ** 2:
            unmatched.append(op.name)
            continue
        region.boundary = list(best.points)
        if op.kind == "pocket" and best.depth:
            op.parameters.depth = round(best.depth, 4)
        updated.append(op.name)
    state.bounds = None
    for op in state.job.operations:
        g = op.strategy.geometry
        if g is not None:
            state.include_bounds(g.boundary)
        for p in getattr(op.parameters, "points", None) or ():
            state.include_bounds([p])
    if ex.thickness:
        state.job.stock.height = round(ex.thickness, 4)
        state.job.stock.align_origin_to_reference()
    return updated, unmatched


def _centroid(pts):
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def _d2(a, b) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
