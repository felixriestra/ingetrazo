# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Pockets with islands: at each depth level a contour pass around the
cutter-centre region, then parallel rows across it; finishing passes on
the final walls (port of 2DCam's
``Advanced2DToolpathGenerator.generatePocket``, closed pockets).

The cutter-centre region is the pocket region offset inward by the tool
radius (plus allowances) as ONE Clipper offset of the boundary with its
islands — so an island that grows into a wall, or into another island,
merges with it, and a narrow waist splits the region, instead of 2DCam's
independent loop offsets overlapping. A region the cutter does not fit
in is an error: 2DCam turned a 5 mm square pocket with a 6 mm cutter into
an inverted 1 mm loop that cut 1 mm into every wall.
"""
from __future__ import annotations

import math

from .. import geometry as geo
from ..issues import CamError
from ..toolpath import Comment, Linear, Rapid, Section, Toolpath
from . import common


def generate(stock, setup, tool, params, strategy, tolerance, finishing_tool=None) -> Toolpath:
    finishing_tool = finishing_tool or tool
    common.check_stock(stock)
    common.check_depth(params.depth, stock)
    common.check_step_down(params.stepDown)
    common.check_stepover(params.stepoverFraction)
    if not (math.isfinite(params.stockAllowance) and params.stockAllowance >= 0):
        raise CamError("invalid_allowance", allowance=params.stockAllowance)
    if not (math.isfinite(params.inset) and params.inset >= 0):
        raise CamError("invalid_inset", inset=params.inset)
    common.check_tool(tool)
    common.check_tool(finishing_tool)

    finish_r = finishing_tool.diameter * 0.5 + strategy.stockAllowance
    rough_r = tool.diameter * 0.5 + strategy.stockAllowance + params.stockAllowance
    if strategy.geometry is not None:
        boundary, islands = strategy.geometry.boundary, list(strategy.geometry.islands)
    else:
        boundary, islands = common.automatic_rect(stock, params.inset), []
    tol = geo.cleanup_tolerance(min(tool.diameter, finishing_tool.diameter))
    boundary = geo.remove_short_seams(boundary, tol)
    cleaned_islands = [geo.remove_short_seams(i, tol) for i in islands]
    if boundary is None or any(i is None for i in cleaned_islands):
        raise CamError("invalid_boundary")

    # Round joins are flattened into chords, and a chord lies INSIDE its arc
    # by up to the arc tolerance: the cutter would clip every corner it
    # rounds by that much. Flattening at half the chordal tolerance and
    # offsetting half of it further keeps every chord outside the true
    # radius, at a cost of at most one chordal tolerance of stock.
    arc_tol = tolerance.chordal * 0.5
    rough = geo.offset(boundary, cleaned_islands, -(rough_r + arc_tol), "round", arc_tol)
    finish = geo.offset(boundary, cleaned_islands, -(finish_r + arc_tol), "round", arc_tol)
    finish_count = max(0, strategy.finishingPasses)
    if not rough or (finish_count > 0 and not finish):
        raise CamError("region_too_small_for_tool", diameter=tool.diameter)
    rough_loops = _contours(rough, strategy.direction)
    finish_loops = _contours(finish, strategy.direction)
    all_rough = [lp for lp, _ in rough_loops]

    ys = [p[1] for lp in all_rough for p in lp]
    min_y, max_y = min(ys), max(ys)
    if max_y <= min_y:
        raise CamError("invalid_boundary")
    top_z = stock.top_z + strategy.topHeight
    safe_z = top_z + strategy.resolved_setup(setup).safeHeight
    final_depth = (max(0.0, top_z - strategy.bottomHeight)
                   if strategy.bottomHeight is not None else params.depth)
    passes = common.pass_count(final_depth, params.stepDown)
    desired = tool.diameter * params.stepoverFraction
    row_count = max(2, int(math.ceil((max_y - min_y) / desired)) + 1)
    row_step = (max_y - min_y) / (row_count - 1)

    rows = []
    for r in range(row_count):
        y = min_y + r * row_step
        for x0, x1 in geo.spans(all_rough, y):
            if x1 - x0 > 1e-6:
                rows.append(((x0, y), (x1, y)))
    conventional = strategy.direction == "conventional"
    if conventional:
        rows.reverse()

    def inside_rough(p):
        return sum(1 for lp in all_rough if geo.contains(p, lp, 0.0)) % 2 == 1

    cmds = common.header("Pocket", tool)
    sections: list = []

    def depth_z(n):
        return top_z - min(n * params.stepDown, final_depth)

    def roughing(n):
        z = depth_z(n)
        start = len(cmds)
        cmds.append(Comment("Pocket roughing pass {n} of {total}", {"n": n, "total": passes}))
        _contour_set(cmds, rough_loops, z, safe_z, tool.cuttingFeed, tool.plungeFeed)
        for i, (a, b) in enumerate(rows):
            forward = (i % 2 == 0) != conventional
            s, e = (a, b) if forward else (b, a)
            cmds.append(Rapid((s[0], s[1], safe_z)))
            # A row starts on the region's edge: a helix fits only ahead of
            # it, along the row, and only where the rows above and below
            # leave room — otherwise the entry ramps.
            length = max(geo.distance(s, e), 1e-12)
            waste = ((e[0] - s[0]) / length, (e[1] - s[1]) / length)
            entry_top = depth_z(n - 1) if n > 1 else top_z
            cmds.extend(common.entry(strategy.entry, s, e, z, entry_top, tool,
                                     waste_normal=waste, loops=all_rough,
                                     inside_region=inside_rough))
            cmds.append(Linear((e[0], e[1], z), tool.cuttingFeed))
            cmds.append(Rapid((e[0], e[1], safe_z)))
        _section(sections, "roughing", start, cmds)

    def finishing(n, k):
        start = len(cmds)
        cmds.append(Comment("Pocket finishing pass {k} of {finishes} at depth pass {n} of {total}",
                            {"k": k, "finishes": finish_count, "n": n, "total": passes}))
        _contour_set(cmds, finish_loops, depth_z(n), safe_z, finishing_tool.cuttingFeed,
                     finishing_tool.plungeFeed)
        _section(sections, "finishing", start, cmds)

    same_tool = finishing_tool is tool or finishing_tool.id == tool.id
    if same_tool:
        for n in range(1, passes + 1):
            roughing(n)
            for k in range(1, finish_count + 1):
                finishing(n, k)
    else:
        for n in range(1, passes + 1):
            roughing(n)
        if finish_count > 0:
            cmds.extend(common.tool_transition(finishing_tool, "Pocket finishing tool"))
            for n in range(1, passes + 1):
                for k in range(1, finish_count + 1):
                    finishing(n, k)
    cmds.extend(common.footer())
    return Toolpath(cmds, sections)


def _contours(pieces, direction) -> list:
    """``[(loop, is_island)]`` ordered like 2DCam — every outer wall first
    (largest first), then every island — each oriented for ``direction``
    and starting at its lowest vertex."""
    outers = sorted((p[0] for p in pieces), key=lambda lp: -abs(geo.signed_area(lp)))
    holes = sorted((h for p in pieces for h in p[1]), key=lambda lp: -abs(geo.signed_area(lp)))
    out = []
    for lp in outers:
        ccw = common.cut_direction_ccw(material_inside=False, direction=direction)
        out.append((common.start_at_lowest(geo.oriented(lp, ccw)), False))
    for lp in holes:
        ccw = common.cut_direction_ccw(material_inside=True, direction=direction)
        out.append((common.start_at_lowest(geo.oriented(lp, ccw)), True))
    return out


def _contour_set(cmds, loops, z, safe_z, feed, plunge_feed) -> None:
    """2DCam's ``appendContour`` for every loop: rapid over its start,
    plunge, go round, close, retract."""
    for loop, _island in loops:
        if len(loop) < 3:
            continue
        s = loop[0]
        cmds.append(Rapid((s[0], s[1], safe_z)))
        cmds.append(Linear((s[0], s[1], z), plunge_feed))
        for p in loop[1:]:
            cmds.append(Linear((p[0], p[1], z), feed))
        cmds.append(Linear((s[0], s[1], z), feed))
        cmds.append(Rapid((s[0], s[1], safe_z)))


def _section(sections, kind, start, cmds) -> None:
    if len(cmds) > start:
        sections.append(Section(kind, start, len(cmds) - start))
