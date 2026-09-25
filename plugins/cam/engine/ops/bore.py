# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Bores: a round hole milled by circular interpolation (port of 2DCam's
``BoreToolpathGenerator``).

The cutter runs on a circle of radius ``(D − d) / 2`` counter-clockwise —
climb, inside a hole — descending one step-down per turn as a helix, then
makes a flat full circle at the final depth to finish the wall.

2DCam stops there, which is only a hole when the bore is at most twice
the tool diameter: beyond that the helix sweeps a ring and leaves a solid
core standing (a 30 mm bore with a 6 mm cutter kept an 18 mm post). Here,
when a core would remain, each depth level also clears it with concentric
circles stepping inward (``docs/cam-2dcam-deviations.md``).
"""
from __future__ import annotations

import math

from ..issues import CamError
from ..toolpath import Arc, Linear, Rapid, Toolpath
from . import common

#: Inward step between the concentric circles clearing a bore's core, as a
#: fraction of the tool diameter.
CORE_STEP = 0.75


def generate(stock, setup, tool, params, strategy=None, tolerance=None) -> Toolpath:
    common.check_stock(stock)
    common.check_tool(tool)
    D, d = params.diameter, tool.diameter
    if not (math.isfinite(D) and D > d):
        raise CamError("bore_smaller_than_tool", bore=D, diameter=d)
    common.check_depth(params.depth, stock)
    common.check_step_down(params.stepDown)
    cx, cy = params.center[0], params.center[1]
    R = D * 0.5
    ox, oy, _ = stock.origin
    if not (cx - R >= ox - 1e-9 and cx + R <= ox + stock.width + 1e-9
            and cy - R >= oy - 1e-9 and cy + R <= oy + stock.depth + 1e-9):
        raise CamError("bore_outside_stock", x=cx, y=cy, bore=D)

    r = d * 0.5
    path_r = R - r
    top_z = stock.top_z
    safe_z = top_z + setup.safeHeight
    sx, sy = cx + path_r, cy
    passes = common.pass_count(params.depth, params.stepDown)
    rings = _core_rings(path_r, r, d)

    cmds = common.header("Bore", tool)
    cmds.append(Rapid((sx, sy, safe_z)))
    cmds.append(Linear((sx, sy, top_z), tool.plungeFeed))
    for n in range(1, passes + 1):
        z = top_z - min(n * params.stepDown, params.depth)
        cmds.append(Arc((sx, sy, z), (-path_r, 0.0, 0.0), tool.plungeFeed, clockwise=False))
        if rings:
            _clear_core(cmds, cx, cy, z, path_r, rings, tool)
    final_z = top_z - params.depth
    cmds.append(Arc((sx, sy, final_z), (-path_r, 0.0, 0.0), tool.cuttingFeed, clockwise=False))
    cmds.append(Rapid((sx, sy, safe_z)))
    cmds.extend(common.footer())
    return Toolpath(cmds)


def _core_rings(path_r: float, r: float, d: float) -> list:
    """Radii of the circles that clear what the outer ring leaves: nothing
    when the outer ring's sweep already reaches the centre."""
    inner_edge = path_r - r                  # radius of the core left standing
    if inner_edge <= 1e-9:
        return []
    count = max(1, int(math.ceil(inner_edge / (CORE_STEP * d))))
    # From just inside the outer ring down to a circle whose sweep covers
    # the centre (radius ≤ r/2), evenly spaced.
    last = min(r * 0.5, inner_edge)
    step = (path_r - last) / count
    return [path_r - step * k for k in range(1, count + 1)]


def _clear_core(cmds, cx, cy, z, path_r, rings, tool) -> None:
    for rad in rings:
        cmds.append(Linear((cx + rad, cy, z), tool.cuttingFeed))
        cmds.append(Arc((cx + rad, cy, z), (-rad, 0.0, 0.0), tool.cuttingFeed, clockwise=False))
    cmds.append(Linear((cx + path_r, cy, z), tool.cuttingFeed))
