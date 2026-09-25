# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Building blocks shared by the generators: the operation header, tool
transitions, entry moves and contour passes (2DCam's private helpers in
``Advanced2DToolpathGenerator``), plus the direction rule.

Two places deliberately differ from 2DCam (``docs/cam-2dcam-deviations.md``):

- **Direction.** 2DCam's offsets come back clockwise whatever went in, so
  its "climb" is true climb only on outside profiles; inside profiles and
  pocket walls come out conventional. Here the rule is physical, for a
  clockwise (M3) spindle: *climb keeps the material on the cutter's
  right* — clockwise around a part, counter-clockwise around a hole or a
  pocket wall, clockwise around a pocket island.
- **Helix entry.** 2DCam spirals around the entry point itself, which
  puts half the helix across the wall it is about to cut — a gouge of a
  quarter of the tool diameter on every profile and on the pocket rows
  that start at a wall. Here the helix is centred off the path, on the
  waste side, and only where a circle of that radius fits; otherwise the
  entry falls back to a ramp.
"""
from __future__ import annotations

import math

from .. import geometry as geo
from ..issues import CamError
from ..toolpath import Arc, Comment, Coolant, Linear, Rapid, SpindleStart, SpindleStop, ToolChange

#: Every comment template a generator or the compiler may emit — the
#: translation catalogues must cover them (tests/test_cam_i18n.py).
COMMENT_TEMPLATES = (
    "Operation: {name}",
    "Tool T{number}: {name}",
    "Facing",
    "Outside profile",
    "Inside profile",
    "Profile finishing pass at depth pass {n} of {total}",
    "Profile spring pass {n} of {total}",
    "Profile finishing tool",
    "Pocket",
    "Pocket roughing pass {n} of {total}",
    "Pocket finishing pass {k} of {finishes} at depth pass {n} of {total}",
    "Pocket finishing tool",
    "Drilling",
    "Engraving",
)


def header(title: str, tool) -> list:
    return [Comment(title), ToolChange(tool.number),
            SpindleStart(tool.spindleRPM, True), Coolant(True)]


def footer() -> list:
    return [Coolant(False), SpindleStop()]


def tool_transition(tool, label: str) -> list:
    return [Coolant(False), SpindleStop(), Comment(label), ToolChange(tool.number),
            SpindleStart(tool.spindleRPM, True), Coolant(True)]


def pass_count(total_depth: float, step_down: float) -> int:
    """Depth levels to reach ``total_depth`` in ``step_down`` steps. The
    epsilon keeps 1.0 / 0.1 = 10.000000000000002 from adding an eleventh
    pass at the same depth."""
    return max(1, int(math.ceil(total_depth / step_down - 1e-9)))


def cut_direction_ccw(material_inside: bool, direction: str) -> bool:
    """Whether a contour must run counter-clockwise.

    ``material_inside`` is True when the material that stays is INSIDE the
    loop (an outside profile, a pocket island) — climb then runs clockwise;
    around a hole or a pocket wall climb runs counter-clockwise.
    Conventional is the reverse."""
    climb_ccw = not material_inside
    return climb_ccw if direction == "climb" else not climb_ccw


def start_at_lowest(loop) -> list:
    """Rotate ``loop`` to begin at its lowest-X (then lowest-Y) vertex — a
    deterministic start, and the one 2DCam's offsets happen to use."""
    if not loop:
        return list(loop)
    i = min(range(len(loop)), key=lambda k: (round(loop[k][0], 9), round(loop[k][1], 9)))
    return list(loop[i:]) + list(loop[:i])


def start_at_longest_edge(loop) -> list:
    """Rotate ``loop`` to begin at the midpoint of its longest edge (a
    vertex is inserted there). Leads then run along that edge, never past
    a corner into the wall."""
    n = len(loop)
    i = max(range(n), key=lambda k: geo.distance(loop[k], loop[(k + 1) % n]))
    a, b = loop[i], loop[(i + 1) % n]
    mid = ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5)
    return [mid] + list(loop[i + 1:]) + list(loop[:i + 1])


# ---- entries -------------------------------------------------------------

def helix_radius(tool) -> float:
    return max(tool.diameter * 0.25, 0.1)


def plunge(start, z, tool) -> list:
    return [Linear((start[0], start[1], z), tool.plungeFeed)]


def ramp_toward(start, end, z, tool) -> list:
    """2DCam's ramp: descend while moving a fraction of the way toward
    ``end`` (at most a quarter, at least 5 %)."""
    length = geo.distance(start, end)
    f = min(0.25, max(0.05, tool.diameter / max(length, tool.diameter)))
    return [Linear((start[0] + (end[0] - start[0]) * f,
                    start[1] + (end[1] - start[1]) * f, z), tool.plungeFeed)]


def ramp_and_return(start, end, z, top_z, tool) -> list:
    """Ramp down along ``start → end`` and come back to ``start`` at depth,
    so the cut then begins at ``start`` with the ramp's wedge cleaned. Used
    where the cut starts exactly at ``start`` (profiles, pocket walls)."""
    length = geo.distance(start, end)
    if length <= 1e-9:
        return plunge(start, z, tool)
    f = min(0.5, max(0.05, tool.diameter / length))
    p = (start[0] + (end[0] - start[0]) * f, start[1] + (end[1] - start[1]) * f)
    return [Linear((start[0], start[1], top_z), tool.plungeFeed),
            Linear((p[0], p[1], z), tool.plungeFeed),
            Linear((start[0], start[1], z), tool.cuttingFeed)]


#: Steepest descent a helix may take. 2DCam drops the whole depth in one
#: turn (30°+ on a small circle); ten degrees is a common ceiling for
#: centre-cutting end mills in wood and plastics.
MAX_HELIX_DEGREES = 10.0


def helix_at(start, center, z, top_z, tool) -> list:
    """A counter-clockwise helix from ``top_z`` down to ``z`` on the circle
    through ``start`` centred at ``center``, ending back at ``start`` — as
    many full turns as keep it within :data:`MAX_HELIX_DEGREES`."""
    off = (center[0] - start[0], center[1] - start[1], 0.0)
    r = math.hypot(off[0], off[1])
    drop = max(0.0, top_z - z)
    per_turn = 2 * math.pi * r * math.tan(math.radians(MAX_HELIX_DEGREES))
    turns = max(1, int(math.ceil(drop / per_turn - 1e-9))) if per_turn > 0 else 1
    out = [Linear((start[0], start[1], top_z), tool.plungeFeed)]
    for k in range(1, turns + 1):
        out.append(Arc((start[0], start[1], top_z - drop * k / turns), off, tool.plungeFeed,
                       clockwise=False))
    return out


def fits(center, radius: float, loops, inside_region) -> bool:
    """Whether a circle fits: ``center`` inside the region (by
    ``inside_region``) and at least ``radius`` from every loop."""
    if not inside_region(center):
        return False
    return all(geo.distance_to_loop(center, lp) >= radius - 1e-9 for lp in loops if len(lp) >= 2)


def entry(method, start, toward, z, top_z, tool, *, waste_normal=None,
          loops=(), inside_region=None) -> list:
    """The move from above ``start`` down to ``z``, ready to cut toward
    ``toward``. ``top_z`` is where material begins at this spot — the
    stock top on the first pass, the previous pass's floor after that —
    so ramps and helixes only descend through what is left to cut.

    ``waste_normal`` (a unit vector at ``start`` pointing to the side that
    is being cut away) and ``loops``/``inside_region`` (where the cutter
    centre may go) let a helix be placed safely; without them, or when it
    does not fit, a helix becomes a ramp."""
    if method == "ramp":
        return ramp_and_return(start, toward, z, top_z, tool)
    if method == "helix":
        r = helix_radius(tool)
        if waste_normal is not None and inside_region is not None:
            c = (start[0] + waste_normal[0] * r, start[1] + waste_normal[1] * r)
            if fits(c, r, loops, inside_region):
                return helix_at(start, c, z, top_z, tool)
        return ramp_and_return(start, toward, z, top_z, tool)
    return plunge(start, z, tool)


def left_normal(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    if n <= 1e-12:
        return (0.0, 0.0)
    return (-dy / n, dx / n)


# ---- region helpers ---------------------------------------------------------

def automatic_rect(stock, inset: float) -> list:
    """2DCam's ``.automatic`` geometry: the stock rectangle, inset."""
    ox, oy, _ = stock.origin
    if not (math.isfinite(inset) and inset >= 0 and stock.width > 2 * inset
            and stock.depth > 2 * inset):
        raise CamError("invalid_inset", inset=inset)
    return [(ox + inset, oy + inset), (ox + stock.width - inset, oy + inset),
            (ox + stock.width - inset, oy + stock.depth - inset),
            (ox + inset, oy + stock.depth - inset)]


def check_stock(stock) -> None:
    if not stock.is_valid:
        raise CamError("invalid_stock")


def check_depth(depth: float, stock) -> None:
    if not (isinstance(depth, (int, float)) and math.isfinite(depth) and depth > 0):
        raise CamError("invalid_depth", depth=depth)
    if depth > stock.height + 1e-9:
        raise CamError("depth_exceeds_stock", depth=depth, height=stock.height)


def check_step_down(step: float) -> None:
    if not (math.isfinite(step) and step > 0):
        raise CamError("invalid_step_down", step_down=step)


def check_stepover(fraction: float) -> None:
    if not (math.isfinite(fraction) and 0 < fraction <= 1):
        raise CamError("invalid_stepover", stepover=fraction)


def check_tool(tool) -> None:
    if not (math.isfinite(tool.diameter) and tool.diameter > 0):
        raise CamError("invalid_tool_diameter", diameter=tool.diameter)
