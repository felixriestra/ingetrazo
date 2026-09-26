# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The overlay's annotations (dimensions, leader texts) learn what is hidden
from the depth the frame just rendered, not from ray casts: a SketchUp 2018
house with 25 dimensions spent 2.8 s of EVERY frame casting 1350 rays at
284 000 triangles (26-09-2026); an orbit frame went from 1722 ms to 50 ms.

Needs a GL context that renders; skipped where the platform has none."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QVector3D as V
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


def _box(scene, x0, y0, z0, x1, y1, z1):
    p = [V(x0, y0, z0), V(x1, y0, z0), V(x1, y1, z0), V(x0, y1, z0),
         V(x0, y0, z1), V(x1, y0, z1), V(x1, y1, z1), V(x0, y1, z1)]
    for idx in ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5),
                (2, 3, 7, 6), (3, 0, 4, 7)):
        scene.mesh.add_face([p[i] for i in idx])


def test_a_point_behind_a_wall_is_hidden_and_one_in_front_is_not():
    from core.dimension import Dimension
    from views.main_window import MainWindow
    win = MainWindow()
    try:
        win.show()
        for _ in range(10):
            _app.processEvents()
        vp = win.viewport
        if getattr(vp, "_gl", None) is None or not vp.isValid():
            pytest.skip("no OpenGL context on this platform")
        sc = vp.scene
        _box(sc, -2, -0.1, 0, 2, 0.1, 3)                 # a wall across Y=0
        sc.dimensions.append(Dimension(V(-1, 2, 1), V(1, 2, 1), V(0, 0, 0.5)))
        sc.version += 1
        vp.camera.set_view("front")                      # looking toward +Y
        vp.camera.perspective = False
        vp.camera.fit_to(V(-3, -1, 0), V(3, 3, 3))
        for _ in range(5):
            _app.processEvents()
        vp.grabFramebuffer()       # a real frame, painted the way Qt does
        if getattr(vp, "_depth_snap", None) is None:
            pytest.skip("this GL context gives no depth read-back")
        behind = V(0, 2, 1)                              # the wall hides it
        front = V(0, -2, 1)                              # nothing in front
        on_face = V(0, -0.1, 1)                          # ON the wall's face
        assert vp._occluded_on_screen(behind)
        assert not vp._occluded_on_screen(front)
        assert not vp._occluded_on_screen(on_face)
        # the rays agree on all three
        assert vp._is_occluded(behind)
        assert not vp._is_occluded(front)
        assert not vp._is_occluded(on_face)
    finally:
        win._saved_version = win.viewport.scene.version
        win.close()
