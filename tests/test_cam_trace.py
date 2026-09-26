# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Bitmap tracing: a picture becomes outlines to machine.

The tracer against shapes whose true outline is known (a rectangle keeps
its four corners and its area; a circle its area; a letter O its hole),
the detail setting, the size in millimetres, the window that drives it,
and the outlines on the stock — pocketed shape by shape, undoable.
"""
from __future__ import annotations

import math
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QRectF, QSettings, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QVector3D
from PySide6.QtWidgets import QApplication, QDialog

_app = QApplication.instance() or QApplication([])

from plugins.cam.engine import trace as T  # noqa: E402


def _canvas(n=400):
    y, x = np.mgrid[0:n, 0:n]
    return x, y


def _gray(black):
    return np.where(black, 0, 255).astype(np.uint8)


def test_otsu_splits_a_two_tone_picture():
    g = np.full((50, 50), 230, np.uint8)
    g[10:40, 10:40] = 20
    assert 20 < T.otsu(g) <= 230
    assert (g < T.otsu(g)).sum() == 900


def test_a_rectangle_keeps_its_corners_and_its_area():
    x, y = _canvas()
    black = (x >= 50) & (x < 250) & (y >= 100) & (y < 180)
    for detail in (0.0, 0.5, 1.0):
        res = T.trace(_gray(black), T.settings_for(detail, _gray(black)))
        (loop,) = res.loops
        assert len(loop) == 4
        assert abs(T._area(loop)) == 200 * 80
        assert sorted(loop) == [(50.0, 100.0), (50.0, 180.0), (250.0, 100.0), (250.0, 180.0)]


def test_a_circle_keeps_its_area_and_gets_simpler_with_less_detail():
    x, y = _canvas()
    black = np.hypot(x - 199.5, y - 199.5) < 150
    counts = []
    for detail in (0.0, 0.5, 1.0):
        res = T.trace(_gray(black), T.settings_for(detail, _gray(black)))
        (loop,) = res.loops
        assert abs(T._area(loop)) == pytest.approx(math.pi * 150 ** 2, rel=0.01)
        counts.append(len(loop))
    assert counts[0] > counts[1] > counts[2]


def test_a_letter_o_has_its_hole_and_specks_go():
    x, y = _canvas()
    r = np.hypot(x - 199.5, y - 199.5)
    black = (r < 150) & (r > 80)
    black[5, 5] = True                      # a black speck outside
    black[199, 199 + 115] = False           # a white speck in the black
    res = T.trace(_gray(black), T.settings_for(0.35, _gray(black)))
    assert sorted(res.holes) == [False, True]
    outer = res.loops[res.holes.index(False)]
    hole = res.loops[res.holes.index(True)]
    assert abs(T._area(outer)) == pytest.approx(math.pi * 150 ** 2, rel=0.01)
    assert abs(T._area(hole)) == pytest.approx(math.pi * 80 ** 2, rel=0.02)


def test_shapes_touching_at_a_corner_stay_apart():
    g = np.full((6, 6), 255, np.uint8)
    g[1, 1] = g[2, 2] = 0
    assert len(T.boundary_loops(g < 128)) == 2


def test_invert_traces_the_light_areas():
    x, y = _canvas(100)
    black = (x >= 20) & (x < 60) & (y >= 20) & (y < 60)
    res = T.trace(_gray(black), T.TraceSettings(threshold=128, invert=True, speckle=1,
                                                tolerance=0.5))
    # The white page around the square: its outline is the page, the
    # square's is now a hole.
    assert sorted(res.holes) == [False, True]


def test_millimetres_width_aspect_and_y_up():
    x, y = _canvas()
    black = (x >= 100) & (x < 300) & (y >= 50) & (y < 150)     # 200 × 100 px
    res = T.trace(_gray(black), T.settings_for(0.0, _gray(black)))
    loops, (w, h) = T.to_mm(res, 80.0)
    assert (w, h) == (80.0, 40.0)
    xs = [p[0] for p in loops[0]]
    ys = [p[1] for p in loops[0]]
    assert (min(xs), max(xs), min(ys), max(ys)) == (0.0, 80.0, 0.0, 40.0)


def test_the_match_score():
    from plugins.cam.ui.trace_dialog import match_score
    x, y = _canvas()
    black = (x >= 50) & (x < 250) & (y >= 100) & (y < 180)
    res = T.trace(_gray(black), T.settings_for(0.35, _gray(black)))
    assert match_score(res) == pytest.approx(1.0, abs=1e-3)
    circle = np.hypot(x - 199.5, y - 199.5) < 150
    res = T.trace(_gray(circle), T.settings_for(1.0, _gray(circle)))
    assert 0.97 < match_score(res) < 1.0


# ---- nesting: shapes and their holes -------------------------------------------------

def _lp(points):
    return SimpleNamespace(points=points)


def _square(x0, y0, s):
    return _lp([(x0, y0), (x0 + s, y0), (x0 + s, y0 + s), (x0, y0 + s)])


def test_loops_nest_into_shapes_and_holes():
    from plugins.cam.build import nest_loops
    o_outer, o_hole = _square(0, 0, 100), _square(30, 30, 40)
    dot = _square(45, 45, 10)                    # inside the hole: a shape again
    apart = _square(200, 0, 50)
    groups = nest_loops([dot, apart, o_hole, o_outer])
    assert [(g[0], g[1]) for g in groups] == [(o_outer, [o_hole]), (apart, []), (dot, [])]


# ---- the window and the dock ---------------------------------------------------------

def _logo() -> QImage:
    img = QImage(600, 300, QImage.Format_ARGB32)
    img.fill(Qt.transparent)                     # a logo on a transparent background
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#111111"))
    p.setPen(Qt.NoPen)
    p.drawEllipse(QRectF(20, 40, 220, 220))
    p.setBrush(Qt.transparent)
    p.setCompositionMode(QPainter.CompositionMode_Clear)
    p.drawEllipse(QRectF(80, 100, 100, 100))
    p.setCompositionMode(QPainter.CompositionMode_SourceOver)
    p.setBrush(QColor("#111111"))
    p.drawRect(QRectF(320, 60, 240, 180))
    p.end()
    return img


def test_the_window_traces_and_hands_back_millimetres():
    from plugins.cam.ui.trace_dialog import TraceDialog
    dlg = TraceDialog(inch=False, width_mm=120.0)
    dlg.set_image(_logo())
    assert len(dlg.traced.loops) == 3 and sum(dlg.traced.holes) == 1
    assert "3 outlines (holes: 1)" in dlg.stats.text()
    fine = dlg.traced.node_count
    dlg.detail.setValue(100)
    assert dlg.traced.node_count < fine
    dlg._on_add()
    assert dlg.result() == QDialog.Accepted
    xs = [p[0] for lp in dlg.outlines_mm for p in lp]
    assert max(xs) - min(xs) == pytest.approx(120.0)
    dlg.close()


def test_an_unreadable_picture_says_so(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from plugins.cam.ui.trace_dialog import TraceDialog
    said = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a: said.append(a[2])))
    bad = tmp_path / "broken.png"
    bad.write_bytes(b"not a picture")
    dlg = TraceDialog()
    assert not dlg.load(str(bad)) and "could not be read" in said[0]
    dlg.close()


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    path = tmp_path / "prefs.ini"
    factory = lambda *a: QSettings(str(path), QSettings.IniFormat)  # noqa: E731
    import PySide6.QtCore as qc
    import views.main_window as mw
    monkeypatch.setattr(qc, "QSettings", factory)
    monkeypatch.setattr(mw, "QSettings", factory)
    return path


def test_traced_outlines_land_on_the_stock_and_pocket_shape_by_shape(settings_file, tmp_path,
                                                                     monkeypatch):
    from plugins.cam.ui import trace_dialog
    from plugins.cam.ui.dock import show_dock
    from views.main_window import MainWindow
    win = MainWindow()
    win.show()
    win.viewport.scene.mesh.add_face([QVector3D(0, 0, 0), QVector3D(1, 0, 0),
                                      QVector3D(1, 1, 0), QVector3D(0, 1, 0)])
    win._saved_version = win.viewport.scene.version
    dock = show_dock(win.viewport)
    try:
        assert dock.new_job(tmp_path / "sign.igcam")
        dock._on_start_job()

        def run(self):
            self.set_image(_logo())
            self._on_add()
            return QDialog.Accepted
        monkeypatch.setattr(trace_dialog.TraceDialog, "exec", run)
        assert dock.trace_image() == 3
        assert len(dock.paths) == 3
        assert "traced from the picture" in dock.status.text()
        # One undo takes the whole trace away.
        win.viewport.history.undo()
        dock.refresh_paths()
        assert dock.paths == []
        assert dock.trace_image() == 3
        # Every traced point is on the stock (centred on it).
        st = dock.state.job.stock
        for p in dock.paths:
            for u, v in p.points:
                assert 0 <= u <= st.width and 0 <= v <= st.depth
        # Pocket all three: the ring is one pocket with its island, the
        # square another — not one pocket with the square as an "island".
        dock.choose_paths(set(range(len(dock.paths))))
        before = len(dock.state.job.operations)
        dock._on_add("pocket")
        pockets = dock.state.job.operations[before:]
        assert sorted(len(op.strategy.geometry.islands) for op in pockets) == [0, 1]
    finally:
        ws = win.workspace()
        if ws is not None:
            ws.mark_saved()
            dock.flush()
            ws.mark_saved()
            win.leave_workspace()
        dock.dispose()
