# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""``Viewport.overlay_painters`` and the public projection (host hook H3).

A plugin that shows world geometry (the CAM toolpaths) paints it on the
viewport's 2D overlay, projected with the same math the host's georef
paths use. What is pinned here: painters run with the viewport, their
painter state cannot leak into the host's drawing, a painter that raises
is dropped instead of breaking every frame, and the vectorised projection
agrees with the per-point one.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import inspect

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QVector3D
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from views.viewport import Viewport  # noqa: E402


def _viewport():
    vp = Viewport(None)
    vp.resize(800, 600)
    return vp


def _paint(vp):
    img = QImage(800, 600, QImage.Format_ARGB32)
    img.fill(Qt.white)
    painter = QPainter(img)
    painter.setPen(QPen(QColor(0, 0, 255), 1))
    vp._draw_plugin_overlays(painter)
    pen_after = painter.pen().color()
    painter.end()
    return img, pen_after


def test_a_painter_runs_with_the_viewport_and_draws():
    vp = _viewport()
    seen = []

    def paint(painter, viewport):
        seen.append(viewport)
        painter.setPen(QPen(QColor(255, 0, 0), 5))
        painter.drawLine(10, 10, 200, 10)

    vp.overlay_painters.append(paint)
    img, pen_after = _paint(vp)
    assert seen == [vp]
    assert QColor(img.pixel(100, 10)).red() == 255
    # The host's pen came back: plugin state does not leak.
    assert pen_after == QColor(0, 0, 255)


def test_a_failing_painter_is_dropped_and_the_rest_still_run():
    vp = _viewport()
    calls = []

    def broken(painter, viewport):
        painter.setPen(QPen(QColor(0, 255, 0), 9))
        raise RuntimeError("boom")

    def fine(painter, viewport):
        calls.append(1)

    vp.overlay_painters.extend([broken, fine])
    _img, pen_after = _paint(vp)
    assert calls == [1]
    assert vp.overlay_painters == [fine]
    assert pen_after == QColor(0, 0, 255)
    _paint(vp)
    assert calls == [1, 1]


def test_the_overlay_pass_calls_the_plugin_layers():
    src = inspect.getsource(Viewport._draw_overlay)
    assert "self._draw_plugin_overlays(painter)" in src
    # Above the document's annotations, so dimensions stay readable.
    assert (src.index("_draw_plugin_overlays")
            < src.index("self._draw_dimensions(painter)"))


def test_the_vectorised_projection_matches_the_per_point_one():
    vp = _viewport()
    pts = [QVector3D(0, 0, 0), QVector3D(1.5, -2.0, 0.3),
           QVector3D(-3.0, 4.0, 1.0)]
    px, py, ok = vp.world_to_pixels([[p.x(), p.y(), p.z()] for p in pts])
    for i, p in enumerate(pts):
        one = vp.world_to_pixel(p)
        assert ok[i] == (one is not None)
        if one is not None:
            assert np.allclose((px[i], py[i]), one, atol=1e-3)
