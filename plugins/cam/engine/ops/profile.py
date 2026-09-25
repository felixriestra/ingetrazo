# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Outside / inside profiles with tabs, leads, entries, stock allowance and
an optional finishing tool (port of 2DCam's
``Advanced2DToolpathGenerator.generateProfile``).

The cutter centre follows the boundary offset by the tool radius (plus
allowances) with mitred corners, one depth level at a time. Differences
from 2DCam, all toward not cutting the part (see
``docs/cam-2dcam-deviations.md``): the climb/conventional rule is the
physical one; an inside offset that splits into several pieces cuts all of
them instead of the largest; with leads the path starts mid-edge so a lead
never runs past a corner into the wall; and a helix entry is placed on the
waste side of the path.
"""
from __future__ import annotations

import math

from .. import geometry as geo
from ..issues import CamError
from ..toolpath import Arc, Comment, CutterCompensation, Linear, Rapid, Section, Toolpath
from . import common


def generate(stock, setup, tool, params, strategy, outside: bool, tolerance,
             finishing_tool=None) -> Toolpath:
    finishing_tool = finishing_tool or tool
    if strategy.geometry is not None:
        raw = strategy.geometry.boundary
    else:
        raw = common.automatic_rect(stock, params.inset)
    boundary = geo.remove_short_seams(
        raw, geo.cleanup_tolerance(min(tool.diameter, finishing_tool.diameter)))
    if boundary is None or len(boundary) < 3:
        raise CamError("invalid_boundary")
    common.check_stock(stock)
    common.check_depth(params.depth, stock)
    common.check_step_down(params.stepDown)
    if not (math.isfinite(params.stockAllowance) and params.stockAllowance >= 0):
        raise CamError("invalid_allowance", allowance=params.stockAllowance)
    common.check_tool(tool)
    common.check_tool(finishing_tool)

    def radius_offset(t):
        return 0.0 if strategy.compensation == "controller" else t.diameter * 0.5

    rough_off = radius_offset(tool) + params.stockAllowance + strategy.stockAllowance
    finish_off = radius_offset(finishing_tool) + strategy.stockAllowance
    if strategy.geometry is None and not outside:
        avail_w = stock.width - 2 * params.inset
        avail_d = stock.depth - 2 * params.inset
        if not (avail_w > 2 * rough_off and avail_d > 2 * rough_off
                and avail_w > 2 * finish_off and avail_d > 2 * finish_off):
            raise CamError("region_too_small_for_tool", diameter=tool.diameter)

    sign = 1.0 if outside else -1.0
    arc_tol = tolerance.chordal
    rough_loops = geo.offset_loop(boundary, sign * rough_off, "miter", arc_tol)
    finish_loops = geo.offset_loop(boundary, sign * finish_off, "miter", arc_tol)
    if outside:
        rough_loops, finish_loops = rough_loops[:1], finish_loops[:1]
    if not rough_loops or not finish_loops:
        raise CamError("region_too_small_for_tool", diameter=tool.diameter)

    # Leads and helixes want room along a straight edge: start mid-edge.
    has_leads = (strategy.leadInLength > 0 or strategy.leadOutLength > 0
                 or strategy.entry == "helix")
    ccw = common.cut_direction_ccw(material_inside=outside, direction=strategy.direction)

    def prepare(loop):
        pts = geo.oriented(loop, ccw)
        return common.start_at_longest_edge(pts) if has_leads else common.start_at_lowest(pts)

    rough_paths = [prepare(lp) for lp in rough_loops]
    finish_paths = [prepare(lp) for lp in finish_loops]

    top_z = stock.top_z + strategy.topHeight
    final_depth = (max(0.0, top_z - strategy.bottomHeight)
                   if strategy.bottomHeight is not None else params.depth)
    safe_z = top_z + strategy.resolved_setup(setup).safeHeight
    passes = common.pass_count(final_depth, params.stepDown)
    finish_count = max(0, strategy.finishingPasses)
    distinct_finish = (finishing_tool is not tool and finishing_tool.id != tool.id) \
        or params.stockAllowance > 1e-6

    title = "Outside profile" if outside else "Inside profile"
    cmds = common.header(title, tool)
    sections: list = []

    def comp_on():
        if strategy.compensation != "computer":
            cmds.append(CutterCompensation("left" if strategy.direction == "climb" else "right"))

    def contour(paths, z, t, entry_top=None):
        for path in paths:
            _append_contour(cmds, path, z, top_z if entry_top is None else entry_top, safe_z,
                            t, strategy, outside, [p for p in paths if p is not path] + [path],
                            tab_top=top_z)

    def depth_z(n):
        return top_z - min(n * params.stepDown, final_depth)

    comp_on()
    for n in range(1, passes + 1):
        start = len(cmds)
        contour(rough_paths, depth_z(n), tool, entry_top=depth_z(n - 1) if n > 1 else top_z)
        if distinct_finish and finish_count > 0:
            _section(sections, "roughing", start, cmds)
    if distinct_finish and finish_count > 0:
        if finishing_tool is not tool and finishing_tool.id != tool.id:
            if strategy.compensation != "computer":
                cmds.append(CutterCompensation("off"))
            cmds.extend(common.tool_transition(finishing_tool, "Profile finishing tool"))
            comp_on()
        for n in range(1, passes + 1):
            start = len(cmds)
            cmds.append(Comment("Profile finishing pass at depth pass {n} of {total}",
                                {"n": n, "total": passes}))
            contour(finish_paths, depth_z(n), finishing_tool)
            _section(sections, "finishing", start, cmds)
        for k in range(1, finish_count):
            start = len(cmds)
            cmds.append(Comment("Profile spring pass {n} of {total}",
                                {"n": k, "total": finish_count - 1}))
            contour(finish_paths, top_z - final_depth, finishing_tool)
            _section(sections, "finishing", start, cmds)
    else:
        for _ in range(max(0, finish_count - 1)):
            contour(rough_paths, top_z - final_depth, tool)
    if strategy.compensation != "computer":
        cmds.append(CutterCompensation("off"))
    cmds.extend(common.footer())
    return Toolpath(cmds, sections or None)


def _section(sections, kind, start, cmds) -> None:
    if len(cmds) > start:
        sections.append(Section(kind, start, len(cmds) - start))


def _append_contour(cmds, path, z, top_z, safe_z, tool, strategy, outside, all_paths,
                    tab_top=None) -> None:
    """One closed pass around ``path`` at ``z``: lead-in, entry, the loop
    with its tabs, lead-out, retract (2DCam's ``appendProfileContour``).
    ``top_z`` is where the entry starts descending; tabs are measured from
    the stock top, ``tab_top``."""
    first = path[0]
    second = path[1] if len(path) > 1 else first
    length = max(geo.distance(first, second), 1e-6)
    ux, uy = (second[0] - first[0]) / length, (second[1] - first[1]) / length
    edge_room = geo.distance(first, second) if strategy.leadInLength > 0 else math.inf
    lead_in = min(strategy.leadInLength, edge_room)
    lead_start = (first[0] - ux * lead_in, first[1] - uy * lead_in)
    cmds.append(Rapid((lead_start[0], lead_start[1], safe_z)))

    # The waste side at the start: outside a part the cutter may wander
    # outward, inside a hole toward its middle.
    n_left = common.left_normal(first, second)
    ccw = geo.signed_area(path) > 0
    # For a CCW loop the left normal points inside the loop.
    inward = n_left if ccw else (-n_left[0], -n_left[1])
    waste = (-inward[0], -inward[1]) if outside else inward

    def inside_region(p):
        inside = geo.contains(p, path)
        return not inside if outside else inside

    cmds.extend(common.entry(strategy.entry, lead_start, second if lead_in <= 0 else first,
                             z, top_z, tool, waste_normal=waste if lead_in <= 0 else None,
                             loops=all_paths, inside_region=inside_region))
    last = cmds[-1].to if isinstance(cmds[-1], (Linear, Arc)) else None
    if last is None or math.dist(last[:2], first) > 1e-9 or abs(last[2] - z) > 1e-9:
        cmds.append(Linear((first[0], first[1], z), tool.cuttingFeed))
    _cut_with_tabs(cmds, list(path) + [first], z, top_z if tab_top is None else tab_top, tool,
                   strategy.tabs)
    last = cmds[-1]
    if isinstance(last, Linear) and abs(last.to[2] - z) > 1e-6:
        cmds.append(Linear((first[0], first[1], z), tool.plungeFeed))
    retract = first
    if strategy.leadOutLength > 0:
        lead_out = min(strategy.leadOutLength, geo.distance(first, second))
        retract = (first[0] + ux * lead_out, first[1] + uy * lead_out)
        cmds.append(Linear((retract[0], retract[1], z), tool.cuttingFeed))
    cmds.append(Rapid((retract[0], retract[1], safe_z)))


def _cut_with_tabs(cmds, pts, z, top_z, tool, tabs) -> None:
    """The loop at ``z``, rising to ``top_z - tab.height`` over each tab
    (2DCam's ``appendProfileCutSegments``: tab edges split the segments,
    and each interval takes the height its midpoint needs)."""
    seg = [geo.distance(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    total = sum(seg)
    if total <= 0:
        return
    active = [t for t in tabs if t.width > 0 and t.height > 0]
    cum = 0.0
    cur_z = z
    for i, length in enumerate(seg):
        a, b = pts[i], pts[i + 1]
        if length <= 0:
            continue
        s0, s1 = cum, cum + length
        splits = _tab_splits(s0, s1, total, active)
        iv_start, iv_point = s0, a
        for s in splits:
            mid = (iv_start + s) * 0.5
            tz = _tab_z(mid, z, top_z, total, active)
            if abs(cur_z - tz) > 1e-6:
                cmds.append(Linear((iv_point[0], iv_point[1], tz), tool.plungeFeed))
                cur_z = tz
            p = geo.interpolate(a, b, (s - s0) / length)
            cmds.append(Linear((p[0], p[1], tz), tool.cuttingFeed))
            iv_start, iv_point = s, p
        cum = s1


def _norm(d: float, total: float) -> float:
    if total <= 0:
        return 0.0
    v = math.fmod(d, total)
    return v if v >= 0 else v + total


def _tab_splits(start, end, total, tabs) -> list:
    eps = max(total, 1.0) * 1e-9
    vals = {round(end, 9)}
    for t in tabs:
        c = _norm(t.pathFraction * total, total)
        for edge in (c - t.width * 0.5, c + t.width * 0.5):
            n = _norm(edge, total)
            for cand in (n, n + total):
                if cand <= total and start + eps < cand < end - eps:
                    vals.add(round(cand, 9))
    return sorted(vals)


def _tab_z(d, z, top_z, total, tabs) -> float:
    target = z
    w = _norm(d, total)
    for t in tabs:
        c = _norm(t.pathFraction * total, total)
        delta = abs(w - c)
        if min(delta, total - delta) <= t.width * 0.5:
            target = max(target, top_z - t.height)
    return target
