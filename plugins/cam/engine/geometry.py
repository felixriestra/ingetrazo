# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""2D geometry for the generators: offsets and booleans through
``manifold3d.CrossSection``, plus the small polygon helpers 2DCam's
``GeometryKernel`` provides.

Offsets replace 2DCam's ``ContourOffset`` + iOverlay union. The library is
manifold3d — Clipper2 inside, working in doubles — which IngeTrazo already
ships for its solid booleans, so CAM adds no native dependency. (The port
first used pyclipper; the switch was measured on the whole parity corpus
before it was made: 33 of 39 jobs give the identical toolpath, the others
differ by at most 0.09 mm in roughing and 0.05 mm in finishing, where
both stay 0.001–0.010 mm outside the exact tool radius, and every program
still passes LinuxCNC's and GRBL's own readers. See
docs/cam-2dcam-deviations.md, item 30.)

Round joins take manifold's ``circular_segments`` (segments per full
circle), computed from the arc tolerance the operation asks for
(:func:`segments`), so a chord never strays further from the true arc.

Conventions (the whole engine relies on them):

- a loop is a list of ``(x, y)`` without the closing duplicate;
- ``signed_area > 0`` means counter-clockwise (Y up, as in the model);
- :func:`offset` grows a region for ``delta > 0`` and shrinks it for
  ``delta < 0``, whatever the orientation of the loops handed in.
"""
from __future__ import annotations

import math

import manifold3d

#: Miter joins are effectively unbounded (2DCam intersects the offset edges
#: with no limit): a sharp outside corner keeps its sharp tool path. The
#: cap only guards against a near-zero angle shooting a vertex to infinity.
MITER_LIMIT = 100.0

_JOINS = {"miter": manifold3d.JoinType.Miter, "round": manifold3d.JoinType.Round,
          "square": manifold3d.JoinType.Square}


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


# ---- offsets and booleans ----------------------------------------------------

def segments(radius: float, arc_tolerance: float) -> int:
    """Segments for a FULL circle of ``radius`` whose chords stay within
    ``arc_tolerance`` of the arc — the arc tolerance as manifold's
    ``circular_segments``."""
    r = abs(radius)
    tol = max(arc_tolerance, 1e-6)
    if r <= tol:
        return 4
    step = 2.0 * math.acos(max(-1.0, 1.0 - tol / r))
    return max(4, int(math.ceil(2.0 * math.pi / step)))


def _section(loops, fill) -> "manifold3d.CrossSection":
    return manifold3d.CrossSection([[[p[0], p[1]] for p in lp] for lp in loops
                                    if len(lp) >= 3], fill)


def _pieces(cs) -> list:
    """A CrossSection as ``[(outer CCW, [holes CW])]``: ``decompose()``
    gives one piece per outer contour with its own holes; an island inside
    a hole is a piece of its own."""
    out = []
    for part in cs.decompose():
        loops = [[(float(x), float(y)) for x, y in poly] for poly in part.to_polygons()]
        outers = [lp for lp in loops if len(lp) >= 3 and signed_area(lp) > 0]
        holes = [oriented(lp, False) for lp in loops if len(lp) >= 3 and signed_area(lp) < 0]
        if not outers:
            continue
        outers.sort(key=lambda lp: -signed_area(lp))
        out.append((oriented(outers[0], True), holes))
        out.extend((oriented(extra, True), []) for extra in outers[1:])
    return out


def offset(outer, holes, delta: float, join: str = "round",
           arc_tolerance: float = 0.02) -> list:
    """Offset the region ``outer`` minus ``holes`` by ``delta`` (grow when
    positive). Returns ``[(outer_loop, [hole_loops])]`` — possibly several
    pieces when a narrow waist pinches off, possibly none when the region
    vanishes (a loop with no area is no region: nothing comes back). Outer
    loops come back CCW, holes CW."""
    contours = [oriented(outer, True)] + [oriented(h, False) for h in holes]
    cs = _section(contours, manifold3d.FillRule.Positive)
    return _pieces(cs.offset(delta, _JOINS[join], max(2.0, MITER_LIMIT),
                             segments(delta, arc_tolerance)))


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
    if not any(len(lp) >= 3 for lp in loops):
        return []
    return _pieces(_section(loops, manifold3d.FillRule.NonZero))


def difference(subject_loops, clip_loops) -> list:
    """``subject_loops`` minus ``clip_loops`` (each read even-odd)."""
    subject = _section(subject_loops, manifold3d.FillRule.EvenOdd)
    clip = _section(clip_loops, manifold3d.FillRule.EvenOdd)
    return _pieces(subject - clip)


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
