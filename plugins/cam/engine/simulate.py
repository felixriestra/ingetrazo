# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Stock-removal simulation on a height field, and playback in machine time.

A port of 2DCam's ``HeightFieldStock`` and ``ToolpathStockSimulator``: the
stock top is a grid of cells, each holding the height of material left
over its centre; every cutting move sweeps the cutter's shape (flat, ball,
bull-nose, drill point, chamfer) along it and lowers the cells it passes
over. Rapids cut nothing (the verifier guarantees they miss the stock).

Two things are the port's own:

- **Vectorised sweeps.** 2DCam stamps the cutter sample by sample. Here
  all the samples of one move are stamped in one NumPy pass over the cells
  around the move — the same result, at a speed that lets the 3D view
  follow playback.
- **Machine-time playback.** :class:`Playback` advances the simulation to
  any TIME of the program (the controller-oriented estimate of
  :meth:`~.toolpath.Toolpath.cumulative_durations`), to the fraction of a
  move, so a slider scrubs the job as it would run on the machine. Going
  back restarts from the untouched stock.

Units: millimetres, work coordinates, like the rest of the engine.
"""
from __future__ import annotations

import bisect
import math

import numpy as np

from .toolpath import (Arc, DrillCycle, Dwell, Linear, Rapid, RetractZ, ToolChange,
                       arc_points, expand_drill)

#: 2DCam's quality presets: the largest cell size, in mm.
QUALITY_CELL_MM = {"preview": 2.0, "standard": 1.0, "fine": 0.5}


class HeightField:
    """The stock as heights over a grid of ``rows × cols`` cells."""

    def __init__(self, stock, cell_mm: float = 1.0) -> None:
        if not stock.is_valid:
            raise ValueError("invalid stock")
        if not (math.isfinite(cell_mm) and cell_mm > 0):
            raise ValueError("invalid cell size")
        self.stock = stock
        self.cols = max(1, int(math.ceil(stock.width / cell_mm)))
        self.rows = max(1, int(math.ceil(stock.depth / cell_mm)))
        self.cell_w = stock.width / self.cols
        self.cell_d = stock.depth / self.rows
        ox, oy, oz = stock.origin
        self.bottom = oz
        self.top = oz + stock.height
        self.x = ox + (np.arange(self.cols) + 0.5) * self.cell_w
        self.y = oy + (np.arange(self.rows) + 0.5) * self.cell_d
        self.heights = np.full((self.rows, self.cols), self.top, dtype=np.float64)

    def reset(self) -> None:
        self.heights.fill(self.top)

    @property
    def removed_volume(self) -> float:
        """mm³ of material cut away."""
        return float((self.top - self.heights).sum() * self.cell_w * self.cell_d)

    def _col(self, x: float) -> int:
        return min(max(int(math.floor((x - self.stock.origin[0]) / self.cell_w)), 0), self.cols - 1)

    def _row(self, y: float) -> int:
        return min(max(int(math.floor((y - self.stock.origin[1]) / self.cell_d)), 0), self.rows - 1)

    def sweep(self, points: np.ndarray, tool) -> None:
        """Lower the cells under the cutter placed at every point of
        ``points`` (N, 3) — tip positions — in one pass."""
        if len(points) == 0 or tool is None or not (tool.diameter > 0):
            return
        r = tool.diameter * 0.5
        top = self.top
        # Positions entirely above the stock cut nothing.
        points = points[points[:, 2] < top]
        if len(points) == 0:
            return
        c0 = self._col(points[:, 0].min() - r)
        c1 = self._col(points[:, 0].max() + r)
        r0 = self._row(points[:, 1].min() - r)
        r1 = self._row(points[:, 1].max() + r)
        xs = self.x[c0:c1 + 1]
        ys = self.y[r0:r1 + 1]
        window = self.heights[r0:r1 + 1, c0:c1 + 1]
        # Chunk so (samples × window cells) stays in memory.
        per = max(1, int(2_000_000 // max(1, window.size)))
        for s in range(0, len(points), per):
            p = points[s:s + per]
            dx = xs[None, None, :] - p[:, 0, None, None]
            dy = ys[None, :, None] - p[:, 1, None, None]
            d = np.sqrt(dx * dx + dy * dy)                   # (n, rows, cols)
            surface = p[:, 2, None, None] + cutter_rise(tool, d)
            surface = np.where(d <= r, surface, np.inf)
            np.minimum(window, np.maximum(self.bottom, surface.min(axis=0)), out=window)


def cutter_rise(tool, d):
    """Height of the cutter's surface above its tip at radial distance
    ``d`` (array), by tool kind (2DCam's ``removeCutter``)."""
    r = tool.diameter * 0.5
    kind = tool.kind
    if kind == "ballEndMill":
        return r - np.sqrt(np.maximum(0.0, r * r - d * d))
    if kind == "bullNoseEndMill":
        cr = min(max(tool.cornerRadius, 0.0), r)
        if cr <= 0:
            return np.zeros_like(d)
        flat = r - cr
        radial = np.maximum(0.0, d - flat)
        return np.where(d <= flat, 0.0, cr - np.sqrt(np.maximum(0.0, cr * cr - radial * radial)))
    if kind == "chamferMill":
        half = math.radians((tool.includedAngle or 90.0) / 2)
        tip = min(max((tool.tipDiameter or 0.0) * 0.5, 0.0), r)
        return np.maximum(0.0, d - tip) / max(math.tan(half), 1e-9)
    if kind == "drill":
        return d / math.tan(math.radians(59))
    if kind == "spotDrill":
        return d / math.tan(math.radians(45))
    return np.zeros_like(d)


class Playback:
    """Plays a toolpath against a :class:`HeightField` in machine time.

    ``seek(t)`` brings the stock to time ``t`` (seconds from program
    start): forward it simulates only what is new since the last position,
    to the fraction of the current move; backward it starts over. It also
    answers where the tool is and which tool is in the spindle.
    """

    def __init__(self, job, toolpath, cell_mm: float = 1.0) -> None:
        self.job = job
        self.toolpath = toolpath
        self.field = HeightField(job.stock, cell_mm)
        self.spacing = min(self.field.cell_w, self.field.cell_d) * 0.5
        self.times = toolpath.cumulative_durations(job.machine)
        self.total = self.times[-1] if self.times else 0.0
        self._motions = self._flatten()
        self._reset()

    # ---- the program as timed motion --------------------------------------
    def _flatten(self) -> list:
        """One entry per command: ``(kind, start, end, center, tool, t0, t1)``
        with ``kind`` in rapid|line|arc|none. A drill cycle becomes several
        entries sharing its time span."""
        out = []
        cur = None
        tool = None
        prev_t = 0.0
        for i, c in enumerate(self.toolpath.commands):
            t1 = self.times[i]
            if isinstance(c, ToolChange):
                tool = self.job.tool_by_number(c.number)
                out.append(("none", cur, cur, None, tool, prev_t, t1))
            elif isinstance(c, DrillCycle):
                subs = [s for s in expand_drill(c) if not isinstance(s, Dwell)]
                span = (t1 - prev_t) / max(1, len(subs))
                for k, s in enumerate(subs):
                    a, b = prev_t + k * span, prev_t + (k + 1) * span
                    kind = "line" if isinstance(s, Linear) else "rapid"
                    out.append((kind, cur, s.to, None, tool, a, b))
                    cur = s.to
            elif isinstance(c, Rapid):
                out.append(("rapid", cur, c.to, None, tool, prev_t, t1))
                cur = c.to
            elif isinstance(c, RetractZ):
                nxt = None if cur is None else (cur[0], cur[1], c.z)
                out.append(("rapid", cur, nxt, None, tool, prev_t, t1))
                cur = nxt
            elif isinstance(c, Linear):
                out.append(("line", cur, c.to, None, tool, prev_t, t1))
                cur = c.to
            elif isinstance(c, Arc):
                out.append(("arc", cur, c.to, c, tool, prev_t, t1))
                cur = c.to
            else:
                out.append(("none", cur, cur, None, tool, prev_t, t1))
            prev_t = t1
        return out

    def _reset(self) -> None:
        self.field.reset()
        self._index = 0          # next motion entry to process
        self._frac = 0.0         # fraction of it already cut
        self.time = 0.0

    # ---- positions ----------------------------------------------------------
    def _point(self, entry, f):
        kind, a, b, arc, *_ = entry
        if a is None or b is None:
            return b if b is not None else a
        if kind == "arc":
            pts = arc_points(a, arc, max_step_deg=1.0)
            if len(pts) < 2:
                return b
            k = f * (len(pts) - 1)
            i = min(int(k), len(pts) - 2)
            g = k - i
            return tuple(pts[i][j] + (pts[i + 1][j] - pts[i][j]) * g for j in range(3))
        return tuple(a[j] + (b[j] - a[j]) * f for j in range(3))

    def _entry_at(self, t: float):
        """``(index, fraction)`` of the motion running at time ``t``."""
        ends = [e[6] for e in self._motions]
        i = bisect.bisect_left(ends, t)
        if i >= len(self._motions):
            return len(self._motions), 0.0
        e = self._motions[i]
        span = e[6] - e[5]
        return i, (0.0 if span <= 0 else min(1.0, max(0.0, (t - e[5]) / span)))

    def tool_position(self):
        """``(point, tool)`` at the current time (``point`` may be None
        before the first move)."""
        if not self._motions:
            return None, None
        i = min(self._index, len(self._motions) - 1)
        e = self._motions[i]
        f = self._frac if self._index < len(self._motions) else 1.0
        return self._point(e, f), e[4]

    # ---- simulation ---------------------------------------------------------
    def _cut(self, entry, f0: float, f1: float) -> None:
        kind, a, b, arc, tool, *_ = entry
        if kind not in ("line", "arc") or a is None or tool is None or f1 <= f0:
            return
        if kind == "line":
            length = math.dist(a[:2], b[:2]) * (f1 - f0)
            n = max(1, int(math.ceil(length / self.spacing)))
            fs = np.linspace(f0, f1, n + 1)
            A, B = np.asarray(a, float), np.asarray(b, float)
            pts = A[None, :] + (B - A)[None, :] * fs[:, None]
        else:
            r = math.hypot(arc.center[0], arc.center[1])
            full = arc_points(a, arc, max_step_deg=max(0.2, math.degrees(self.spacing / max(r, 1e-9))))
            m = len(full) - 1
            lo, hi = int(math.floor(f0 * m)), int(math.ceil(f1 * m))
            pts = np.asarray(full[lo:hi + 1], float)
        self.field.sweep(pts, tool)

    def seek(self, t: float) -> None:
        t = min(max(0.0, t), self.total)
        if t < self.time - 1e-12:
            self._reset()
        target_i, target_f = self._entry_at(t)
        while self._index < target_i:
            self._cut(self._motions[self._index], self._frac, 1.0)
            self._index += 1
            self._frac = 0.0
        if self._index < len(self._motions) and target_f > self._frac:
            self._cut(self._motions[self._index], self._frac, target_f)
            self._frac = target_f
        self.time = t

    def run_to_end(self) -> None:
        self.seek(self.total)

    @property
    def command_index(self) -> int:
        """Index into the toolpath's commands of the move under way."""
        if not self._motions:
            return 0
        i = min(self._index, len(self._motions) - 1)
        t = self._motions[i][6]
        return min(bisect.bisect_left(self.times, t), len(self.times) - 1)
