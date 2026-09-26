# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The crossing of two guide lines is exact, not 2 mm off (#110).

Guides reached the snap engine as their ±10 km segment. float32 rounds a
coordinate of 10 000 m to ~1 mm, so the green X of two diagonal guides — and
every line drawn from it — missed the guides by up to 2 mm, visible when
zooming in. The snap engine now gets the guide clipped to the view."""
from __future__ import annotations

import math
import random
import sys

from PySide6.QtGui import QVector3D
from PySide6.QtWidgets import QApplication

if QApplication.instance() is None:
    QApplication(sys.argv[:1])


def _exact_crossing(g1, g2):
    p, d = (g1.point.x(), g1.point.y()), (g1.direction.x(), g1.direction.y())
    q, e = (g2.point.x(), g2.point.y()), (g2.direction.x(), g2.direction.y())
    den = d[0] * e[1] - d[1] * e[0]
    s = ((q[0] - p[0]) * e[1] - (q[1] - p[1]) * e[0]) / den
    return p[0] + d[0] * s, p[1] + d[1] * s


def test_guide_crossings_are_exact_in_top_view():
    from core.guide import Guide
    from core.topology import segment_intersection
    from views.main_window import MainWindow
    win = MainWindow()
    vp = win.viewport
    try:
        vp.resize(1200, 800)
        rnd = random.Random(110)
        worst_old = worst_new = 0.0
        checked = 0
        for _ in range(200):
            a = math.radians(rnd.uniform(10, 80))
            b = math.radians(rnd.choice([0.0, 90.0, rnd.uniform(0, 180)]))
            if abs(math.sin(a - b)) < 0.1:
                continue
            g1 = Guide(QVector3D(rnd.uniform(-3, 3), rnd.uniform(-3, 3), 0),
                       QVector3D(math.cos(a), math.sin(a), 0))
            g2 = Guide(QVector3D(rnd.uniform(-3, 3), rnd.uniform(-3, 3), 0),
                       QVector3D(math.cos(b), math.sin(b), 0))
            x, y = _exact_crossing(g1, g2)
            old = segment_intersection(g1.a, g1.b, g2.a, g2.b)
            if old is not None:
                worst_old = max(worst_old, math.hypot(old.x() - x, old.y() - y))
            s1, s2 = vp._guide_snap_segment(g1), vp._guide_snap_segment(g2)
            if s1 is None or s2 is None:
                continue
            hit = segment_intersection(*s1, *s2)
            if hit is None:
                continue  # crossing outside the view: nothing to snap to
            checked += 1
            worst_new = max(worst_new, math.hypot(hit.x() - x, hit.y() - y))
        assert checked > 50
        assert worst_old > 5e-4          # the bug: ~mm through the 10 km segment
        assert worst_new < 1e-5          # clipped to the view: below 0.01 mm
    finally:
        win._saved_version = vp.scene.version
        win.close()


def test_clipped_guide_stays_on_its_line_and_off_screen():
    from core.guide import Guide
    from views.main_window import MainWindow
    win = MainWindow()
    vp = win.viewport
    try:
        vp.resize(1200, 800)
        g = Guide(QVector3D(0.3, -0.2, 0), QVector3D(1, 1, 0))
        a, b = vp._guide_snap_segment(g)
        for p in (a, b):
            # on the line (2-D cross product with the direction)…
            r = p - g.point
            assert abs(r.x() * g.direction.y() - r.y() * g.direction.x()) < 1e-5
            # …and its ends outside the viewport, so they never read as
            # endpoints to snap to.
            px = vp._world_to_pixel(p)
            assert px is None or not (0 <= px[0] <= 1200 and 0 <= px[1] <= 800)
    finally:
        win._saved_version = vp.scene.version
        win.close()
