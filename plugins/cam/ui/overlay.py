# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Drawing the job in the viewport, through the host's overlay hook.

Everything to draw is converted to WORLD coordinates once, when a result
arrives (:meth:`ToolpathOverlay.set_result`) — the toolpath is in work
millimetres, the viewport in metres on the machining plane — and kept as
NumPy arrays. Each frame then only projects them in one vectorised call
(``viewport.world_to_pixels``) and draws lines: a pocket of tens of
thousands of moves costs a projection, not a Python loop.

Cuts are drawn in each operation's colour (the selected operation thicker),
plunges and ramps in orange, rapids as thin dashed red lines, and the stock
as a wireframe box. Each can be switched off.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QLineF, QPointF, Qt
from PySide6.QtGui import QColor, QPen

from ..engine.toolpath import motion_points

#: Operation colours, cycled (colour-blind-safe Okabe–Ito set).
OP_COLORS = ["#0072B2", "#009E73", "#CC79A7", "#56B4E9", "#E69F00", "#D55E00", "#F0E442",
             "#000000"]
RAPID = QColor(220, 40, 40, 200)
PLUNGE = QColor(240, 140, 20, 230)
STOCK = QColor(130, 110, 80, 200)

CUT, PLUNGE_KIND, RAPID_KIND = 0, 1, 2


class ToolpathOverlay:
    """A viewport overlay painter (``viewport.overlay_painters`` entry)."""

    def __init__(self) -> None:
        self.a = np.empty((0, 3))
        self.b = np.empty((0, 3))
        self.kind = np.empty(0, dtype=np.int8)
        self.op = np.empty(0, dtype=np.int32)
        self.stock_edges = np.empty((0, 2, 3))
        self.op_ids: list = []
        self.selected_op: str | None = None
        self.show_cuts = True
        self.show_rapids = True
        self.show_stock = True
        self.visible = True
        self.cmd = np.empty(0, dtype=np.int64)
        #: Playback: command index reached, and the tool marker
        #: ``(world_point, radius_m)``; ``None`` when not playing back.
        self.play_index: int | None = None
        self.play_tool = None

    def clear(self) -> None:
        self.__init__()

    # ---- data ----------------------------------------------------------------
    def set_stock(self, state) -> None:
        st = state.job.stock
        ox, oy, oz = st.origin
        xs, ys, zs = (ox, ox + st.width), (oy, oy + st.depth), (oz, oz + st.height)
        corners = {(i, j, k): state.work_to_world(xs[i], ys[j], zs[k])
                   for i in (0, 1) for j in (0, 1) for k in (0, 1)}
        edges = []
        for i in (0, 1):
            for j in (0, 1):
                edges.append((corners[(i, j, 0)], corners[(i, j, 1)]))
        for k in (0, 1):
            for i in (0, 1):
                edges.append((corners[(i, 0, k)], corners[(i, 1, k)]))
            for j in (0, 1):
                edges.append((corners[(0, j, k)], corners[(1, j, k)]))
        self.stock_edges = np.asarray(edges, dtype=float)

    def set_result(self, state, compiled) -> None:
        """World-space segments for ``compiled`` (a CompileResult)."""
        tp = compiled.toolpath
        self.op_ids = list(compiled.operation_ranges)
        op_of = np.full(len(tp.commands), -1, dtype=np.int32)
        for n, (start, stop) in enumerate(compiled.operation_ranges.values()):
            op_of[start:stop] = n
        rows_a, rows_b, kinds, ops, cmds = [], [], [], [], []
        for kind, a, b, i in motion_points(tp.commands):
            cmds.append(i)
            rows_a.append(a)
            rows_b.append(b)
            if kind == "rapid":
                kinds.append(RAPID_KIND)
            elif abs(a[2] - b[2]) > 1e-6:
                kinds.append(PLUNGE_KIND)
            else:
                kinds.append(CUT)
            ops.append(op_of[i])
        if not rows_a:
            self.a = self.b = np.empty((0, 3))
            self.kind = np.empty(0, dtype=np.int8)
            self.op = np.empty(0, dtype=np.int32)
            return
        self.a = self._to_world(state, np.asarray(rows_a, dtype=float))
        self.b = self._to_world(state, np.asarray(rows_b, dtype=float))
        self.kind = np.asarray(kinds, dtype=np.int8)
        self.op = np.asarray(ops, dtype=np.int32)
        self.cmd = np.asarray(cmds, dtype=np.int64)

    def set_playhead(self, state, index, work_point, radius_mm) -> None:
        """Show playback at command ``index`` with the tool at
        ``work_point`` (work mm); ``index=None`` ends playback display."""
        self.play_index = index
        if index is None or work_point is None:
            self.play_tool = None
            return
        centre = self._to_world(state, np.asarray([work_point], dtype=float))[0]
        self.play_tool = (centre, radius_mm * 0.001, state)

    @staticmethod
    def _to_world(state, work) -> np.ndarray:
        """Work mm (N, 3) → world metres (N, 3), vectorised."""
        from ..state import Frame
        frame = state.frame or Frame()
        dx, dy = state.plane_to_work_offset()
        top = state.job.stock.top_z
        u = work[:, 0] - dx
        v = work[:, 1] - dy
        w = work[:, 2] - top
        o, U, V, N = (np.asarray(x, dtype=float) for x in (frame.origin, frame.u, frame.v, frame.n))
        return o[None, :] + (u[:, None] * U[None, :] + v[:, None] * V[None, :]
                             + w[:, None] * N[None, :]) * 0.001

    # ---- painting ------------------------------------------------------------
    def __call__(self, painter, viewport) -> None:
        if not self.visible:
            return
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)
        if self.show_stock and len(self.stock_edges):
            pen = QPen(STOCK, 1.0)
            pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            self._lines(painter, viewport, self.stock_edges[:, 0], self.stock_edges[:, 1],
                        np.ones(len(self.stock_edges), dtype=bool))
        if not len(self.a):
            return
        if self.show_rapids:
            pen = QPen(RAPID, 1.0)
            pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            self._lines(painter, viewport, self.a, self.b, self.kind == RAPID_KIND)
        if self.show_cuts:
            sel = self.op_ids.index(self.selected_op) if self.selected_op in self.op_ids else -1
            done = (self.cmd <= self.play_index) if self.play_index is not None \
                else np.ones(len(self.a), dtype=bool)
            for n in range(len(self.op_ids)):
                color = QColor(OP_COLORS[n % len(OP_COLORS)])
                width = 2.4 if n == sel else 1.4
                if sel >= 0 and n != sel:
                    color.setAlpha(110)
                mask = (self.kind == CUT) & (self.op == n)
                painter.setPen(QPen(color, width))
                self._lines(painter, viewport, self.a, self.b, mask & done)
                if self.play_index is not None:
                    color.setAlpha(45)
                    painter.setPen(QPen(color, 1.0))
                    self._lines(painter, viewport, self.a, self.b, mask & ~done)
            painter.setPen(QPen(PLUNGE, 1.6))
            self._lines(painter, viewport, self.a, self.b, (self.kind == PLUNGE_KIND) & done)
        if self.play_tool is not None:
            self._draw_tool(painter, viewport)

    def _draw_tool(self, painter, viewport) -> None:
        """The cutter at the playhead: its footprint circle on the machining
        plane at the tip, and its axis."""
        centre, radius, state = self.play_tool
        from ..state import Frame
        frame = state.frame or Frame()
        U, V, N = (np.asarray(v, dtype=float) for v in (frame.u, frame.v, frame.n))
        ang = np.linspace(0, 2 * np.pi, 33)
        ring = centre[None, :] + radius * (np.cos(ang)[:, None] * U + np.sin(ang)[:, None] * V)
        axis = np.asarray([centre, centre + N * max(radius * 8, 0.02)])
        painter.setPen(QPen(QColor(20, 20, 20, 230), 2.0))
        px, py, ok = viewport.world_to_pixels(ring)
        if ok.all():
            painter.drawPolyline([QPointF(x, y) for x, y in zip(px, py)])
        ax, ay, aok = viewport.world_to_pixels(axis)
        if aok.all():
            painter.setPen(QPen(QColor(20, 20, 20, 200), 3.0))
            painter.drawLine(QLineF(ax[0], ay[0], ax[1], ay[1]))

    @staticmethod
    def _lines(painter, viewport, a, b, mask) -> None:
        if not mask.any():
            return
        A, B = a[mask], b[mask]
        ax, ay, aok = viewport.world_to_pixels(A)
        bx, by, bok = viewport.world_to_pixels(B)
        ok = aok & bok
        if not ok.any():
            return
        painter.drawLines([QLineF(x0, y0, x1, y1) for x0, y0, x1, y1
                           in zip(ax[ok], ay[ok], bx[ok], by[ok])])
