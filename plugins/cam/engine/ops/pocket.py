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


def generate(stock, setup, tool, params, strategy, tolerance, finishing_tool=None,
             is_open: bool = False) -> Toolpath:
    """A closed pocket, or with ``is_open`` an open pocket: the edges named
    by the region's ``openEdgeIndices`` let the cutter pass beyond them."""
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
    open_segments = []
    if strategy.geometry is not None:
        boundary, islands = strategy.geometry.boundary, list(strategy.geometry.islands)
        if is_open:
            n = len(boundary)
            open_segments = [(boundary[i % n], boundary[(i + 1) % n])
                             for i in strategy.geometry.openEdgeIndices if 0 <= i < n]
    else:
        # The whole stock top, inset: its edges have no material beyond
        # them, so 2DCam's "open" automatic pocket is this closed one.
        boundary, islands = common.automatic_rect(stock, params.inset), []
    tol = geo.cleanup_tolerance(min(tool.diameter, finishing_tool.diameter))
    boundary = geo.remove_short_seams(boundary, tol)
    cleaned_islands = [geo.remove_short_seams(i, tol) for i in islands]
    if boundary is None or any(i is None for i in cleaned_islands):
        raise CamError("invalid_boundary")
    open_edges = _open_edges(boundary, open_segments, tol)

    # Round joins are flattened into chords, and a chord lies INSIDE its arc
    # by up to the arc tolerance: the cutter would clip every corner it
    # rounds by that much. Flattening at half the chordal tolerance and
    # offsetting half of it further keeps every chord outside the true
    # radius, at a cost of at most one chordal tolerance of stock.
    arc_tol = tolerance.chordal * 0.5

    def centre_region(radius):
        # Grown by exactly what the offset then takes back: the cutter
        # centre ends ON each open edge — the cutter clears right up to it
        # and one radius past, and not a millimetre further.
        grow = radius + arc_tol
        outers = _grown(boundary, open_edges, grow) if open_edges else [boundary]
        pieces = []
        for outer in outers:
            pieces.extend(geo.offset(outer, cleaned_islands, -(radius + arc_tol), "round",
                                     arc_tol))
        return pieces

    rough = centre_region(rough_r)
    finish = centre_region(finish_r)
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

    cmds = common.header("Open pocket" if is_open else "Pocket", tool)
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


def _open_edges(boundary, segments, tol) -> list:
    """Indices of ``boundary`` edges lying on one of the open ``segments``
    (matched by geometry: seam cleanup may have renumbered the vertices)."""
    if not segments:
        return []
    n = len(boundary)
    out = []
    for i in range(n):
        a, b = boundary[i], boundary[(i + 1) % n]
        mid = ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5)
        if any(geo.point_segment_distance(mid, s0, s1) <= max(tol, 1e-6)
               and geo.point_segment_distance(a, s0, s1) <= max(tol, 1e-6)
               and geo.point_segment_distance(b, s0, s1) <= max(tol, 1e-6)
               for s0, s1 in segments):
            out.append(i)
    return out


def _grown(boundary, open_edges, reach) -> list:
    """The region with every open edge pushed ``reach`` outward (and the
    corner between two open edges filled), as outer loops: offsetting that
    inward by the cutter radius lets the cutter centre reach the open
    edges themselves while it keeps its radius from the closed ones."""
    loop = geo.oriented(boundary, True)              # CCW: outward = right normal
    if geo.signed_area(boundary) < 0:
        n = len(boundary)
        open_edges = [(n - 2 - i) % n for i in open_edges]   # edges renumbered by reversal
    n = len(loop)
    extra = []

    def outward(i):
        a, b = loop[i], loop[(i + 1) % n]
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = max((dx * dx + dy * dy) ** 0.5, 1e-12)
        return (dy / length, -dx / length)

    opened = set(open_edges)
    for i in opened:
        a, b = loop[i], loop[(i + 1) % n]
        o = outward(i)
        extra.append([a, b, (b[0] + o[0] * reach, b[1] + o[1] * reach),
                      (a[0] + o[0] * reach, a[1] + o[1] * reach)])
        if (i + 1) % n in opened:                     # corner shared with the next open edge
            v, o2 = b, outward((i + 1) % n)
            extra.append([v, (v[0] + o[0] * reach, v[1] + o[1] * reach),
                          (v[0] + (o[0] + o2[0]) * reach, v[1] + (o[1] + o2[1]) * reach),
                          (v[0] + o2[0] * reach, v[1] + o2[1] * reach)])
    # All counter-clockwise: under the non-zero rule an overlap of a CW and
    # a CCW piece would cancel out.
    pieces = geo.union([loop] + [geo.oriented(e, True) for e in extra])
    return [outer for outer, _holes in pieces]
