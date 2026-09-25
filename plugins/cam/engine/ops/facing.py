# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Facing: a zig-zag over the whole stock top, one tool radius past every
edge (port of 2DCam's ``FacingToolpathGenerator``)."""
from __future__ import annotations

import math

from ..issues import CamError
from ..toolpath import Arc, Linear, Rapid, Toolpath
from . import common


def generate(stock, setup, tool, params, strategy, tolerance=None) -> Toolpath:
    common.check_stock(stock)
    common.check_tool(tool)
    common.check_stepover(params.stepoverFraction)
    if not (math.isfinite(params.depth) and params.depth > 0):
        raise CamError("invalid_depth", depth=params.depth)
    if params.depth > stock.height + 1e-9:
        raise CamError("depth_exceeds_stock", depth=params.depth, height=stock.height)

    r = tool.diameter * 0.5
    ox, oy, _ = stock.origin
    min_x, max_x = ox - r, ox + stock.width + r
    min_y, max_y = oy - r, oy + stock.depth + r
    top_z = stock.top_z + strategy.topHeight
    cut_z = strategy.bottomHeight if strategy.bottomHeight is not None else top_z - params.depth
    safe_z = top_z + setup.safeHeight
    stepover = tool.diameter * params.stepoverFraction
    span = max_y - min_y
    count = max(int(math.ceil(span / stepover)) + 1, 2)
    step = span / (count - 1)

    cmds = common.header("Facing", tool)
    cmds.append(Rapid((min_x, min_y, safe_z)))
    if strategy.entry == "ramp":
        cmds.append(Linear((min(min_x + tool.diameter, max_x), min_y, cut_z), tool.plungeFeed))
    elif strategy.entry == "helix":
        # Off the stock by a full radius already: the helix cannot touch it.
        hr = common.helix_radius(tool)
        cmds.append(Linear((min_x + hr, min_y, top_z), tool.plungeFeed))
        cmds.append(Arc((min_x + hr, min_y, cut_z), (-hr, 0.0, 0.0), tool.plungeFeed,
                        clockwise=False))
    else:
        cmds.append(Linear((min_x, min_y, cut_z), tool.plungeFeed))

    climb = strategy.direction == "climb"
    for _ in range(max(1, strategy.finishingPasses)):
        for i in range(count):
            yi = i if climb else count - 1 - i
            y = min_y + yi * step
            dest_x = max_x if ((i % 2 == 0) == climb) else min_x
            cmds.append(Linear((dest_x, y, cut_z), tool.cuttingFeed))
            if i < count - 1:
                ni = i + 1 if climb else count - 2 - i
                cmds.append(Linear((dest_x, min_y + ni * step, cut_z), tool.cuttingFeed))

    # Retract straight up from where the last row ended. 2DCam rapids to
    # (final x, max y, safe z): for a conventional pass, which ends at the
    # FIRST edge, that diagonal crosses the whole stock below its top
    # (its own verifier flags it).
    last = next(c.to for c in reversed(cmds) if isinstance(c, Linear))
    cmds.append(Rapid((last[0], last[1], safe_z)))
    cmds.extend(common.footer())
    return Toolpath(cmds)
