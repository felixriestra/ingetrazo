# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Engraving: follow a polyline at depth, in step-downs (port of 2DCam's
``EngravingToolpathGenerator``). The cutter centre runs ON the line."""
from __future__ import annotations

import math

from ..issues import CamError
from ..toolpath import Linear, Rapid, Toolpath
from . import common


def generate(stock, setup, tool, params, strategy=None, tolerance=None) -> Toolpath:
    common.check_stock(stock)
    if not (math.isfinite(params.depth) and params.depth > 0):
        raise CamError("invalid_depth", depth=params.depth)
    if params.depth > stock.height + 1e-9:
        raise CamError("depth_exceeds_stock", depth=params.depth, height=stock.height)
    common.check_step_down(params.stepDown)
    pts = params.points
    if len(pts) < 2:
        raise CamError("insufficient_points")
    for p in pts:
        if not all(math.isfinite(v) for v in p):
            raise CamError("invalid_point", x=p[0], y=p[1])
        if not stock.contains_xy(p):
            raise CamError("point_outside_stock", x=p[0], y=p[1])

    top_z = stock.top_z
    safe_z = top_z + setup.safeHeight
    passes = common.pass_count(params.depth, params.stepDown)
    first, last = pts[0], pts[-1]
    cmds = common.header("Engraving", tool)
    for n in range(1, passes + 1):
        z = top_z - min(n * params.stepDown, params.depth)
        cmds.append(Rapid((first[0], first[1], safe_z)))
        cmds.append(Linear((first[0], first[1], z), tool.plungeFeed))
        for p in pts[1:]:
            cmds.append(Linear((p[0], p[1], z), tool.cuttingFeed))
        if params.isClosed and (last[0], last[1]) != (first[0], first[1]):
            cmds.append(Linear((first[0], first[1], z), tool.cuttingFeed))
            cmds.append(Rapid((first[0], first[1], safe_z)))
        else:
            cmds.append(Rapid((last[0], last[1], safe_z)))
    cmds.extend(common.footer())
    return Toolpath(cmds)
