# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Chamfers with a chamfer mill (V-bit) along a top edge (port of 2DCam's
``Advanced2DToolpathGenerator.generateChamfer``).

At depth ``h`` a cutter of included angle ``α`` and tip diameter ``t`` is
``t/2 + h·tan(α/2)`` wide in radius at the stock top. For a chamfer
``width`` wide, its centre therefore runs that radius minus ``width``
off the edge, on the waste side — outside a part's outline, or (the port's
``inside``) inside a hole. Open paths are offset to their left, as 2DCam
does. One pass at the full depth, repeated for extra finishing passes.
"""
from __future__ import annotations

import math

from .. import geometry as geo
from ..issues import CamError
from ..toolpath import Linear, Rapid, Toolpath
from . import common


def generate(stock, setup, tool, params, strategy, tolerance) -> Toolpath:
    if tool.kind != "chamferMill":
        raise CamError("chamfer_needs_chamfer_mill", number=tool.number)
    angle = tool.includedAngle if tool.includedAngle is not None else 90.0
    tip_r = max(0.0, tool.tipDiameter or 0.0) * 0.5
    tan_half = math.tan(math.radians(angle) / 2)
    reach = tip_r + params.depth * tan_half          # cutting radius at the stock top
    if not (math.isfinite(params.width) and params.width > 0):
        raise CamError("invalid_width", width=params.width)
    if not (math.isfinite(params.depth) and params.depth > 0):
        raise CamError("invalid_depth", depth=params.depth)
    if not (0 < angle < 180 and tip_r <= tool.diameter * 0.5):
        raise CamError("invalid_tool", number=tool.number)
    if params.width > reach + 1e-9:
        raise CamError("chamfer_too_wide", width=params.width, reach=reach)
    if reach > tool.diameter * 0.5 + 1e-9:
        raise CamError("chamfer_too_deep", depth=params.depth, diameter=tool.diameter)

    if params.points:
        pts = [tuple(p[:2]) for p in params.points]
    elif strategy.geometry is not None:
        pts = [tuple(p[:2]) for p in strategy.geometry.boundary]
    else:
        ox, oy, _ = stock.origin
        pts = [(ox, oy), (ox + stock.width, oy), (ox + stock.width, oy + stock.depth),
               (ox, oy + stock.depth)]
    if len(pts) < 2:
        raise CamError("invalid_boundary")
    center_offset = reach - params.width

    if params.isClosed:
        if len(pts) < 3:
            raise CamError("invalid_boundary")
        sign = -1.0 if params.inside else 1.0
        loops = geo.offset_loop(pts, sign * center_offset, "round", tolerance.chordal * 0.5)
        if not loops:
            raise CamError("region_too_small_for_tool", diameter=tool.diameter)
        ccw = common.cut_direction_ccw(material_inside=not params.inside,
                                       direction=strategy.direction)
        path = common.start_at_lowest(geo.oriented(loops[0], ccw))
    else:
        path = _offset_open(pts, center_offset)
        if strategy.direction != "climb":
            path = list(reversed(path))

    top_z = stock.top_z + strategy.topHeight
    z = top_z - params.depth
    safe_z = top_z + strategy.resolved_setup(setup).safeHeight
    cmds = common.header("Chamfer", tool)
    for _ in range(max(1, strategy.finishingPasses)):
        s = path[0]
        cmds.append(Rapid((s[0], s[1], safe_z)))
        cmds.append(Linear((s[0], s[1], z), tool.plungeFeed))
        for p in path[1:]:
            cmds.append(Linear((p[0], p[1], z), tool.cuttingFeed))
        end = s
        if params.isClosed:
            cmds.append(Linear((s[0], s[1], z), tool.cuttingFeed))
        else:
            end = path[-1]
        cmds.append(Rapid((end[0], end[1], safe_z)))
    cmds.extend(common.footer())
    return Toolpath(cmds)


def _offset_open(pts, dist: float) -> list:
    """An open polyline moved ``dist`` to its left, corners mitred."""
    if abs(dist) <= 1e-12:
        return list(pts)
    normals = []
    for a, b in zip(pts, pts[1:]):
        n = common.left_normal(a, b)
        normals.append(n)
    out = [(pts[0][0] + normals[0][0] * dist, pts[0][1] + normals[0][1] * dist)]
    for i in range(1, len(pts) - 1):
        n0, n1 = normals[i - 1], normals[i]
        bis = (n0[0] + n1[0], n0[1] + n1[1])
        blen = math.hypot(*bis)
        if blen < 1e-9:
            out.append((pts[i][0] + n1[0] * dist, pts[i][1] + n1[1] * dist))
            continue
        cos_half = (bis[0] * n0[0] + bis[1] * n0[1]) / blen
        k = dist / max(cos_half, 0.1)
        out.append((pts[i][0] + bis[0] / blen * k, pts[i][1] + bis[1] / blen * k))
    out.append((pts[-1][0] + normals[-1][0] * dist, pts[-1][1] + normals[-1][1] * dist))
    return out
