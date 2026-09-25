# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""2D geometry for the generators: offsets and booleans through pyclipper,
plus the small polygon helpers 2DCam's ``GeometryKernel`` provides.

Offsets replace 2DCam's ``ContourOffset`` + iOverlay union. Clipper works
on integers, so every coordinate is scaled by :data:`SCALE` on the way in
and back on the way out: **1 unit = 0.1 µm**, fixed. That is three orders
of magnitude below any tolerance the job uses and leaves ~9 × 10¹¹ mm of
range in an int64 — a board of any size, re-based on its own origin by
``plugins/cam/extract.py`` first.

Conventions (the whole engine relies on them):

- a loop is a list of ``(x, y)`` without the closing duplicate;
- ``signed_area > 0`` means counter-clockwise (Y up, as in the model);
- :func:`offset` grows a region for ``delta > 0`` and shrinks it for
  ``delta < 0``, whatever the orientation of the loops handed in.
"""
from __future__ import annotations

import math

import pyclipper

SCALE = 10_000.0
#: Miter joins are effectively unbounded (2DCam intersects the offset edges
#: with no limit): a sharp outside corner keeps its sharp tool path. The
#: cap only guards against a near-zero angle shooting a vertex to infinity.
MITER_LIMIT = 100.0


# ---- basics ------------------------------------------------------------------

def distance(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def signed_area(loop) -> float:
    n = len(loop)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x0, y0 = loop[i][0], loop[i][1]
        x1, y1 = loop[(i + 1) % n][0], loop[(i + 1) % n][1]
        s += x0 * y1 - x1 * y0
    return s * 0.5


def oriented(loop, ccw: bool = True) -> list:
    """``loop`` as a list, reversed if needed to run counter-clockwise
    (``ccw=True``) or clockwise."""
    pts = [(p[0], p[1]) for p in loop]
    if (signed_area(pts) > 0) != ccw:
        pts.reverse()
    return pts


def perimeter(loop, closed: bool = True) -> float:
    n = len(loop)
    if n < 2:
        return 0.0
    total = sum(distance(loop[i], loop[i + 1]) for i in range(n - 1))
    if closed:
        total += distance(loop[-1], loop[0])
    return total


def interpolate(a, b, f: float):
    return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)


def contains(point, polygon, tolerance: float = 1e-6) -> bool:
    """Even-odd point-in-polygon; a point ON an edge (within ``tolerance``)
    counts as inside (2DCam's ``GeometryKernel.contains``)."""
    n = len(polygon)
    if n < 3:
        return False
    px, py = point[0], point[1]
    inside = False
    for i in range(n):
        ax, ay = polygon[i][0], polygon[i][1]
        bx, by = polygon[(i + 1) % n][0], polygon[(i + 1) % n][1]
        if _point_segment_distance(px, py, ax, ay, bx, by) <= tolerance:
            return True
        if (ay > py) != (by > py):
            cross_x = (bx - ax) * (py - ay) / (by - ay) + ax
            if px < cross_x:
                inside = not inside
    return inside


def _point_segment_distance(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    sq = dx * dx + dy * dy
    if sq <= 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def point_segment_distance(p, a, b) -> float:
    return _point_segment_distance(p[0], p[1], a[0], a[1], b[0], b[1])


def distance_to_loop(p, loop) -> float:
    n = len(loop)
    return min(_point_segment_distance(p[0], p[1], loop[i][0], loop[i][1],
                                       loop[(i + 1) % n][0], loop[(i + 1) % n][1])
               for i in range(n))


def segment_intersection(a, b, c, d, tolerance: float = 1e-6):
    """Intersection point of segments ``ab`` and ``cd`` or ``None``
    (2DCam's ``GeometryKernel.intersection``)."""
    rx, ry = b[0] - a[0], b[1] - a[1]
    sx, sy = d[0] - c[0], d[1] - c[1]
    den = rx * sy - ry * sx
    if abs(den) <= tolerance:
        return None
    ox, oy = c[0] - a[0], c[1] - a[1]
    t = (ox * sy - oy * sx) / den
    u = (ox * ry - oy * rx) / den
    if -tolerance <= t <= 1 + tolerance and -tolerance <= u <= 1 + tolerance:
        return (a[0] + rx * t, a[1] + ry * t)
    return None


def has_self_intersection(loop) -> bool:
    """True when two non-adjacent edges of the closed ``loop`` touch."""
    n = len(loop)
    if n <= 3:
        return False
    tol = 1e-6
    for i in range(n):
        a, b = loop[i], loop[(i + 1) % n]
        if distance(a, b) <= tol:
            continue
        for j in range(i + 1, n):
            if abs(i - j) <= 1 or (i == 0 and j == n - 1):
                continue
            c, d = loop[j], loop[(j + 1) % n]
            if distance(c, d) <= tol:
                continue
            if segment_intersection(a, b, c, d, tol) is not None:
                return True
    return False


# ---- cleanup -----------------------------------------------------------------

def remove_short_seams(loop, tolerance: float):
    """Drop a vertex of every edge shorter than ``tolerance`` whose
    neighbours are both longer (2DCam's ``removingShortSeams``: it heals
    the near-duplicate closing points traced outlines come with). Returns
    ``None`` when what remains is not a machinable loop."""
    spacing = max(tolerance, 1e-6)
    pts = [(p[0], p[1]) for p in loop if math.isfinite(p[0]) and math.isfinite(p[1])]
    if len(pts) < 3:
        return None
    removed = True
    while removed and len(pts) > 3:
        removed = False
        n = len(pts)
        for i in range(n):
            j = (i + 1) % n
            if distance(pts[i], pts[j]) > spacing:
                continue
            prev_len = distance(pts[(i - 1) % n], pts[i])
            next_len = distance(pts[j], pts[(j + 1) % n])
            if prev_len <= spacing or next_len <= spacing:
                continue
            del pts[i if j == 0 else j]
            removed = True
            break
    if len(pts) < 3 or abs(signed_area(pts)) <= spacing * spacing:
        return None
    return pts


def cleanup_tolerance(tool_diameter: float) -> float:
    """Seam-cleanup tolerance for a cutter (2DCam: 20 % of the diameter,
    clamped to 0.05–0.4 mm)."""
    return min(0.4, max(0.05, tool_diameter * 0.2))


# ---- clipper bridge ----------------------------------------------------------

def _to_clipper(loop):
    return [(int(round(p[0] * SCALE)), int(round(p[1] * SCALE))) for p in loop]


def _from_clipper(path):
    return [(x / SCALE, y / SCALE) for x, y in path]


def _offsetter(join: str, arc_tolerance: float):
    o = pyclipper.PyclipperOffset()
    o.MiterLimit = MITER_LIMIT
    o.ArcTolerance = max(1.0, arc_tolerance * SCALE)
    jt = {"miter": pyclipper.JT_MITER, "round": pyclipper.JT_ROUND,
          "square": pyclipper.JT_SQUARE}[join]
    return o, jt


def offset(outer, holes, delta: float, join: str = "round",
           arc_tolerance: float = 0.02) -> list:
    """Offset the region ``outer`` minus ``holes`` by ``delta`` (grow when
    positive). Returns ``[(outer_loop, [hole_loops])]`` — possibly several
    pieces when a narrow waist pinches off, possibly none when the region
    vanishes. Outer loops come back CCW, holes CW."""
    o, jt = _offsetter(join, arc_tolerance)
    o.AddPath(_to_clipper(oriented(outer, True)), jt, pyclipper.ET_CLOSEDPOLYGON)
    for h in holes:
        o.AddPath(_to_clipper(oriented(h, False)), jt, pyclipper.ET_CLOSEDPOLYGON)
    tree = o.Execute2(delta * SCALE)
    return _pieces(tree)


def _pieces(tree) -> list:
    out = []

    def walk(node):
        for child in node.Childs:          # outer contours
            loop = _from_clipper(child.Contour)
            holes = []
            for h in child.Childs:
                holes.append(oriented(_from_clipper(h.Contour), False))
                walk(h)                      # islands inside holes
            out.append((oriented(loop, True), holes))
    walk(tree)
    return out


def offset_loop(loop, delta: float, join: str = "round",
                arc_tolerance: float = 0.02) -> list:
    """Offset one closed loop; the resulting outer loops (CCW), largest
    first. Empty when the loop vanishes (shrunk past its width)."""
    if abs(delta) <= 1e-6:
        return [oriented(loop, True)]
    pieces = offset(loop, [], delta, join, arc_tolerance)
    loops = [p[0] for p in pieces]
    loops.sort(key=lambda lp: -abs(signed_area(lp)))
    return loops


def union(loops) -> list:
    """Union of closed loops (non-zero fill) as ``[(outer, [holes])]``."""
    pc = pyclipper.Pyclipper()
    for lp in loops:
        if len(lp) >= 3:
            pc.AddPath(_to_clipper(lp), pyclipper.PT_SUBJECT, True)
    tree = pc.Execute2(pyclipper.CT_UNION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
    return _pieces(tree)


def difference(subject_loops, clip_loops) -> list:
    pc = pyclipper.Pyclipper()
    for lp in subject_loops:
        pc.AddPath(_to_clipper(lp), pyclipper.PT_SUBJECT, True)
    for lp in clip_loops:
        pc.AddPath(_to_clipper(lp), pyclipper.PT_CLIP, True)
    tree = pc.Execute2(pyclipper.CT_DIFFERENCE, pyclipper.PFT_EVENODD, pyclipper.PFT_EVENODD)
    return _pieces(tree)


# ---- scanlines (pocket roughing rows) ---------------------------------------

def crossings(loop, y: float) -> list:
    """X of every edge of ``loop`` crossing the horizontal line ``y``,
    half-open in Y so a vertex on the line is counted once (2DCam)."""
    xs = []
    n = len(loop)
    for i in range(n):
        ax, ay = loop[i][0], loop[i][1]
        bx, by = loop[(i + 1) % n][0], loop[(i + 1) % n][1]
        if (ay <= y < by) or (by <= y < ay):
            xs.append(ax + (y - ay) * (bx - ax) / (by - ay))
    return xs


def spans(loops, y: float) -> list:
    """Inside intervals ``(x0, x1)`` of the even-odd region ``loops`` along
    the line ``y``."""
    xs = []
    for lp in loops:
        xs.extend(crossings(lp, y))
    xs.sort()
    out = []
    for i in range(0, len(xs) - 1, 2):
        if xs[i + 1] - xs[i] > 1e-6:
            out.append((xs[i], xs[i + 1]))
    return out
