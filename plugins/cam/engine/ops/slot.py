# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Straight slots (port of 2DCam's ``SlotToolpathGenerator``).

The rows run from ``start`` to ``end`` and are spread across the width the
cutter centre may use, ``width − d``. So the slot cut is a ROUNDED
RECTANGLE: ``width`` wide, reaching one tool radius past ``start`` and
``end``, with tool-radius corners. For a rectangular slot drawn in the
model, ``start``/``end`` are therefore its ends moved in by the tool
radius (``build.py`` does that). A slot exactly the tool wide is one row:
a groove with round ends. At each depth level the cutter plunges at the
first row's start and zig-zags.
"""
from __future__ import annotations

import math

from ..issues import CamError
from ..toolpath import Linear, Rapid, Toolpath
from . import common


def generate(stock, setup, tool, params, strategy=None, tolerance=None) -> Toolpath:
    common.check_stock(stock)
    common.check_tool(tool)
    if not (math.isfinite(params.width) and params.width >= tool.diameter - 1e-9):
        raise CamError("slot_narrower_than_tool", width=params.width, diameter=tool.diameter)
    common.check_depth(params.depth, stock)
    common.check_step_down(params.stepDown)
    common.check_stepover(params.stepoverFraction)
    (x0, y0), (x1, y1) = params.start[:2], params.end[:2]
    length = math.hypot(x1 - x0, y1 - y0)
    if length <= 1e-6:
        raise CamError("slot_endpoints_coincide")
    half = params.width * 0.5
    ox, oy, _ = stock.origin
    for x, y in ((x0, y0), (x1, y1)):
        if not (x - half >= ox - 1e-9 and x + half <= ox + stock.width + 1e-9
                and y - half >= oy - 1e-9 and y + half <= oy + stock.depth + 1e-9):
            raise CamError("slot_outside_stock", x=x, y=y)

    px, py = -(y1 - y0) / length, (x1 - x0) / length       # left normal
    m = max(0.0, (params.width - tool.diameter) * 0.5)      # max centre offset
    step = tool.diameter * params.stepoverFraction
    rows = 1 if m == 0 else max(2, int(math.ceil(2 * m / step)) + 1)
    actual = 0.0 if rows == 1 else 2 * m / (rows - 1)
    top_z = stock.top_z
    safe_z = top_z + setup.safeHeight
    passes = common.pass_count(params.depth, params.stepDown)

    def at(pt, off):
        return (pt[0] + px * off, pt[1] + py * off)

    cmds = common.header("Slot", tool)
    for n in range(1, passes + 1):
        z = top_z - min(n * params.stepDown, params.depth)
        first = at((x0, y0), 0.0 if rows == 1 else -m)
        cmds.append(Rapid((first[0], first[1], safe_z)))
        cmds.append(Linear((first[0], first[1], z), tool.plungeFeed))
        final = first
        for k in range(rows):
            off = 0.0 if rows == 1 else -m + k * actual
            dest = at((x1, y1), off) if k % 2 == 0 else at((x0, y0), off)
            cmds.append(Linear((dest[0], dest[1], z), tool.cuttingFeed))
            final = dest
            if k < rows - 1:
                nxt_off = -m + (k + 1) * actual
                nxt = at((x1, y1), nxt_off) if k % 2 == 0 else at((x0, y0), nxt_off)
                cmds.append(Linear((nxt[0], nxt[1], z), tool.cuttingFeed))
        cmds.append(Rapid((final[0], final[1], safe_z)))
    cmds.extend(common.footer())
    return Toolpath(cmds)
