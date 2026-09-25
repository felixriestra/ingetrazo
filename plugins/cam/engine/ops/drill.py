# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Drilling (port of 2DCam's ``DrillingToolpathGenerator``, plus peck and
dwell). Each hole is one :class:`DrillCycle`; with no peck and no dwell it
expands to exactly 2DCam's moves."""
from __future__ import annotations

import math

from ..issues import CamError
from ..toolpath import DrillCycle, Rapid, Toolpath
from . import common


def nearest_neighbor(points) -> list:
    """2DCam's ``optimized(_, ordering: .nearestNeighbor)``: greedy, from
    the first point, by XY distance."""
    if len(points) <= 2:
        return list(points)
    remaining = list(points[1:])
    out = [points[0]]
    while remaining:
        cur = out[-1]
        i = min(range(len(remaining)),
                key=lambda k: math.hypot(cur[0] - remaining[k][0], cur[1] - remaining[k][1]))
        out.append(remaining.pop(i))
    return out


def generate(stock, setup, tool, params, strategy=None, tolerance=None) -> Toolpath:
    common.check_stock(stock)
    if not (math.isfinite(params.depth) and params.depth > 0):
        raise CamError("invalid_depth", depth=params.depth)
    if params.depth > stock.height + 1e-9:
        raise CamError("depth_exceeds_stock", depth=params.depth, height=stock.height)
    if not params.points:
        raise CamError("no_points")
    if not (math.isfinite(params.peckDepth) and params.peckDepth >= 0):
        raise CamError("invalid_peck", peck=params.peckDepth)
    if not (math.isfinite(params.dwellSeconds) and params.dwellSeconds >= 0):
        raise CamError("invalid_dwell", dwell=params.dwellSeconds)
    for p in params.points:
        if not all(math.isfinite(v) for v in p[:2]):
            raise CamError("invalid_point", x=p[0], y=p[1])
        if not stock.contains_xy(p):
            raise CamError("point_outside_stock", x=p[0], y=p[1])

    top_z = stock.top_z
    safe_z = top_z + setup.safeHeight
    clear_z = top_z + setup.clearanceHeight
    bottom = top_z - params.depth
    cmds = common.header("Drilling", tool)
    for p in params.points:
        cmds.append(Rapid((p[0], p[1], safe_z)))
        cmds.append(DrillCycle(p[0], p[1], clear_z, bottom, tool.plungeFeed,
                               peck=params.peckDepth, dwell=params.dwellSeconds))
    last = params.points[-1]
    cmds.append(Rapid((last[0], last[1], safe_z)))
    cmds.extend(common.footer())
    return Toolpath(cmds)
