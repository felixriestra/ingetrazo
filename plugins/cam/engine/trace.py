# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Bitmap tracing: a picture → closed outlines to machine.

2DCam's CAM trace (``BitmapTraceWorkflow``, purpose ``.cam``) thresholds
the image to black and white and traces the black as cut geometry. 2DCam
traces with vtracer; this port traces with NumPy alone, because the
``vtracer`` wheel (0.6.15) crashes the interpreter on Python 3.14 — a
segfault, which in the app would close IngeTrazo — while working on 3.13
(checked 2026-09-26 on macOS and Linux). For cut geometry the two are
equivalent: clean closed outlines, holes kept as holes, specks dropped.

The steps, all on a grey image (0 black … 255 white, the caller composites
transparency on white):

1. **threshold** — Otsu's automatic level (:func:`otsu`), or the user's;
   ``invert`` traces the white instead;
2. **outline** — every boundary between a black and a white pixel becomes
   a unit edge, and the edges are chained into closed loops
   (:func:`boundary_loops`). Two black pixels that touch only at a corner
   are separate shapes (a cutter cannot pass between them anyway);
3. **clean** — loops enclosing less than the speck area are dropped (a
   white speck in a black area too), the pixel staircase is replaced by
   the midpoints of its steps, and Douglas–Peucker simplification removes
   points closer than the tolerance to the outline;
4. **fit** — :func:`to_mm` scales the outlines to a width in millimetres
   and turns the picture's Y (down) into the machine's (up).

One **detail** setting (0 = every pixel, 1 = smooth and simple) moves the
speck area and the tolerance together, as 2DCam's candidate ladder does.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class TraceSettings:
    threshold: int = 128
    invert: bool = False
    #: Loops enclosing fewer pixels than this are dropped.
    speckle: float = 4.0
    #: Douglas–Peucker tolerance, pixels.
    tolerance: float = 0.8


@dataclass
class TraceResult:
    """Closed loops in PIXELS (x right, y down), each a list of ``(x, y)``
    without the closing repeat; ``holes[i]`` says whether loop ``i`` is a
    hole (white inside black)."""
    loops: list
    holes: list
    width: int
    height: int
    mask: np.ndarray

    @property
    def node_count(self) -> int:
        return sum(len(lp) for lp in self.loops)


def otsu(gray: np.ndarray) -> int:
    """The grey level that best splits the image in two (Otsu's method)."""
    hist = np.bincount(np.clip(gray, 0, 255).astype(np.uint8).ravel(), minlength=256)
    hist = hist.astype(np.float64)
    total = hist.sum()
    if total == 0:
        return 128
    levels = np.arange(256, dtype=np.float64)
    w0 = np.cumsum(hist)
    m0 = np.cumsum(hist * levels)
    mt = m0[-1]
    w1 = total - w0
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (mt * w0 / total - m0) ** 2 / (w0 * w1)
    between[~np.isfinite(between)] = -1.0
    # The first level of the best plateau; black pixels are those BELOW it.
    return int(np.argmax(between)) + 1


def settings_for(detail: float, gray: np.ndarray, threshold: int | None = None,
                 invert: bool = False) -> TraceSettings:
    """The settings for a detail level 0 … 1 (2DCam's ladder, CAM purpose):
    the speck area grows geometrically from a floor to a ceiling set by the
    image size, the tolerance linearly from a third of a pixel to two."""
    d = min(1.0, max(0.0, detail))
    pixels = gray.size
    floor, ceiling = max(2.0, pixels / 250_000), max(24.0, pixels / 12_000)
    return TraceSettings(threshold=otsu(gray) if threshold is None else int(threshold),
                         invert=invert, speckle=floor * (ceiling / floor) ** d,
                         tolerance=0.35 + 1.65 * d)


def trace(gray: np.ndarray, settings: TraceSettings) -> TraceResult:
    """The outlines of the black in ``gray`` (a 2-D array, 0 … 255)."""
    gray = np.asarray(gray)
    mask = gray < settings.threshold
    if settings.invert:
        mask = ~mask
    loops = []
    holes = []
    for raw in boundary_loops(mask):
        area = _area(raw)
        if abs(area) < settings.speckle:
            continue
        pts, forced = _smoothed(raw)
        pts = simplify_closed(pts, settings.tolerance, forced)
        if len(pts) < 3:
            continue
        loops.append(pts)
        # Edges run clockwise (on screen) around black: a hole runs the
        # other way.
        holes.append(area < 0)
    return TraceResult(loops, holes, gray.shape[1], gray.shape[0], mask)


# ---- outlining ---------------------------------------------------------------------

#: Direction codes: 0 right, 1 down, 2 left, 3 up (screen coordinates).
_STEP = ((1, 0), (0, 1), (-1, 0), (0, -1))


def boundary_loops(mask: np.ndarray) -> list:
    """Every black/white boundary as closed loops of pixel corners — one
    point per unit step — each running clockwise on screen around black
    (so holes run the other way).
    At a corner where two black pixels touch diagonally the walk turns
    right, keeping them apart."""
    m = np.pad(np.asarray(mask, dtype=bool), 1)
    h, w = m.shape
    black = m[1:-1, 1:-1]
    ys, xs = np.nonzero(black)
    ys, xs = ys + 1, xs + 1
    starts, dirs = [], []
    # Clockwise around each black pixel (x, y)…(x+1, y+1), kept where the
    # neighbour across that side is white. Corner coordinates are in the
    # padded grid; one is taken off at the end.
    for d, (dy, dx), (sx, sy) in ((0, (-1, 0), (0, 0)),     # top: (x,y)→(x+1,y)
                                  (1, (0, 1), (1, 0)),       # right: (x+1,y)→(x+1,y+1)
                                  (2, (1, 0), (1, 1)),       # bottom: (x+1,y+1)→(x,y+1)
                                  (3, (0, -1), (0, 1))):     # left: (x,y+1)→(x,y)
        keep = ~m[ys + dy, xs + dx]
        starts.append(np.stack([xs[keep] + sx, ys[keep] + sy], axis=1))
        dirs.append(np.full(int(keep.sum()), d, dtype=np.int8))
    if not starts or sum(len(s) for s in starts) == 0:
        return []
    start = np.concatenate(starts)
    direction = np.concatenate(dirs)
    key = start[:, 1].astype(np.int64) * (w + 1) + start[:, 0]
    outgoing: dict = {}
    for i, k in enumerate(key.tolist()):
        outgoing.setdefault(k, []).append(i)
    used = np.zeros(len(key), dtype=bool)
    loops = []
    for first in range(len(key)):
        if used[first]:
            continue
        loop = []
        i = first
        while not used[i]:
            used[i] = True
            x, y = int(start[i, 0]), int(start[i, 1])
            loop.append((x - 1, y - 1))
            d = int(direction[i])
            nx, ny = x + _STEP[d][0], y + _STEP[d][1]
            choices = [j for j in outgoing.get(ny * (w + 1) + nx, ()) if not used[j]]
            if not choices:
                break
            if len(choices) > 1:
                # Prefer turning right, then straight, then left.
                order = {(d + 1) % 4: 0, d: 1, (d + 3) % 4: 2}
                choices.sort(key=lambda j: order.get(int(direction[j]), 3))
            i = choices[0]
        if len(loop) >= 4:
            loops.append(loop)
    return loops


def _area(loop) -> float:
    """Signed area (shoelace), screen coordinates: positive = clockwise on
    screen = around black."""
    a = 0.0
    n = len(loop)
    for k in range(n):
        x0, y0 = loop[k]
        x1, y1 = loop[(k + 1) % n]
        a += x0 * y1 - x1 * y0
    return a * 0.5


#: A turn is a real corner, not a staircase step, when the straight runs on
#: both sides of it are at least this long (pixels).
CORNER_RUN = 3


def _smoothed(loop: list) -> tuple:
    """``(points, corners)``: the staircase smoothed (:func:`_midpoints`),
    with every real corner put back exactly and its index listed, so
    simplification keeps it. Without this a rectangle's corner is a tiny
    chamfer, and simplification may drop half of it and tilt a whole side."""
    n = len(loop)
    step = [(loop[(k + 1) % n][0] - loop[k][0], loop[(k + 1) % n][1] - loop[k][1])
            for k in range(n)]
    # Straight-run length ending at each step and starting at each step.
    before = [1] * n
    after = [1] * n
    for _ in range(2):                       # twice round: runs cross the start
        for k in range(n):
            if step[k] == step[k - 1]:
                before[k] = before[k - 1] + 1
        for k in range(n - 1, -1, -1):
            if step[k] == step[(k + 1) % n]:
                after[k] = after[(k + 1) % n] + 1
    mids = _midpoints(loop)
    pts, corners = [], []
    for k in range(n):
        # Vertex loop[k] sits between step k-1 and step k.
        if step[k] != step[k - 1] and before[k - 1] >= CORNER_RUN and after[k] >= CORNER_RUN:
            if pts and pts[-1] == mids[k - 1]:
                pts.pop()                        # the chamfer point before it
            corners.append(len(pts))
            pts.append((float(loop[k][0]), float(loop[k][1])))
            continue
        pts.append(mids[k])
    # The corner at vertex 0 also removes the last midpoint (the one before it).
    if corners and corners[0] == 0 and len(pts) > 1 and pts[-1] == mids[n - 1]:
        pts.pop()
    return pts, corners


def _midpoints(loop: list) -> list:
    """The middle of every unit step: a diagonal's or a curve's staircase
    becomes a line through its middle, while a straight side stays straight
    and a square corner loses only a half-pixel chamfer. (Midpoints of
    whole runs would turn a rectangle into a diamond.)"""
    n = len(loop)
    return [((loop[k][0] + loop[(k + 1) % n][0]) * 0.5,
             (loop[k][1] + loop[(k + 1) % n][1]) * 0.5) for k in range(n)]


# ---- simplification -------------------------------------------------------------------

def simplify_open(pts: list, tolerance: float) -> list:
    """Douglas–Peucker on an open polyline (both ends kept)."""
    if len(pts) < 3 or tolerance <= 0:
        return list(pts)
    a = np.asarray(pts, dtype=np.float64)
    keep = np.zeros(len(a), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(a) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        seg = a[j] - a[i]
        length = math.hypot(seg[0], seg[1])
        rel = a[i + 1:j] - a[i]
        if length < 1e-12:
            dist = np.hypot(rel[:, 0], rel[:, 1])
        else:
            dist = np.abs(rel[:, 0] * seg[1] - rel[:, 1] * seg[0]) / length
        k = int(np.argmax(dist))
        if dist[k] > tolerance:
            m = i + 1 + k
            keep[m] = True
            stack += [(i, m), (m, j)]
    return [tuple(p) for p in a[keep].tolist()]


def simplify_closed(pts: list, tolerance: float, keep=()) -> list:
    """Douglas–Peucker on a closed loop. Points whose index is in ``keep``
    survive (and the loop is simplified between them); with none, it is
    split at the point farthest from the first."""
    if len(pts) < 4:
        return list(pts)
    keep = sorted(set(keep))
    if keep:
        out = []
        n = len(pts)
        for a, b in zip(keep, keep[1:] + [keep[0] + n]):
            chain = [pts[k % n] for k in range(a, b + 1)]
            out.extend(simplify_open(chain, tolerance)[:-1])
        return out
    a = np.asarray(pts, dtype=np.float64)
    far = int(np.argmax(np.hypot(a[:, 0] - a[0, 0], a[:, 1] - a[0, 1])))
    if far == 0:
        return list(pts)
    first = simplify_open([tuple(p) for p in a[:far + 1].tolist()], tolerance)
    second = simplify_open([tuple(p) for p in np.vstack([a[far:], a[:1]]).tolist()], tolerance)
    return first[:-1] + second[:-1]


# ---- fitting ------------------------------------------------------------------------

def to_mm(result: TraceResult, width_mm: float) -> tuple:
    """``(loops_mm, (width, height))``: the outlines scaled so their
    bounding box is ``width_mm`` wide, with its lower-left corner at 0, 0
    and Y up (as the machine and the stock have it)."""
    pts = [p for lp in result.loops for p in lp]
    if not pts:
        return [], (0.0, 0.0)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    span = max(x1 - x0, 1e-9)
    s = width_mm / span
    loops = [[((x - x0) * s, (y1 - y) * s) for x, y in lp] for lp in result.loops]
    return loops, (width_mm, (y1 - y0) * s)


def match(mask: np.ndarray, drawn: np.ndarray) -> float:
    """How well the outlines reproduce the picture: the overlap of black
    (intersection over union), 1.0 for a perfect trace."""
    a = np.asarray(mask, dtype=bool)
    b = np.asarray(drawn, dtype=bool)
    union = np.logical_or(a, b).sum()
    if union == 0:
        return 1.0
    return float(np.logical_and(a, b).sum() / union)
