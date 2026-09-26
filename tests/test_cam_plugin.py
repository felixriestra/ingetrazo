# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The CAM plugin end to end, in a real main window.

A CAM job is its own ``.igcam`` file, opened in CAM mode: the model is
parked, the job's setup comes first, then 2D geometry is drawn on the
stock, read as paths, and operations are put on the paths. Here a board's
drawing — an outline, a round hole, a window and a pocket — goes from a
new job to G-code for GRBL and LinuxCNC, is saved, closed and reopened
intact, and the model is back untouched throughout. Plus the pieces
around it: the plugin loads from the Extensions menu, undo reaches the
job, units, the overlay, the keyboard, importing outlines from the model.
"""
from __future__ import annotations

import math
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QVector3D
from PySide6.QtWidgets import QApplication, QMessageBox

_app = QApplication.instance() or QApplication([])

from core.group import Group  # noqa: E402
from core.mesh import Mesh  # noqa: E402

T = 0.018            # board thickness, metres
W, D = 0.300, 0.200


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    path = tmp_path / "prefs.ini"
    factory = lambda *a: QSettings(str(path), QSettings.IniFormat)  # noqa: E731
    import PySide6.QtCore as qc
    import views.main_window as mw
    monkeypatch.setattr(qc, "QSettings", factory)
    monkeypatch.setattr(mw, "QSettings", factory)
    return path


def _circle(cx, cy, r, n=32):
    return [(cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n))
            for k in range(n)]


def _rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _walls(mesh, ring, z0, z1, outward=True):
    n = len(ring)
    for i in range(n):
        a, b = ring[i], ring[(i + 1) % n]
        if not outward:
            a, b = b, a
        mesh.add_face([QVector3D(a[0], a[1], z0), QVector3D(b[0], b[1], z0),
                       QVector3D(b[0], b[1], z1), QVector3D(a[0], a[1], z1)])


def board() -> Group:
    """A 300 × 200 × 18 mm board: a Ø5 mm through hole, a 40 × 30 mm
    through cut-out and a 60 × 40 mm pocket 6 mm deep."""
    outline = _rect(0, 0, W, D)
    drill = _circle(0.040, 0.040, 0.0025)
    window = _rect(0.200, 0.120, 0.240, 0.150)
    pocket = _rect(0.080, 0.080, 0.140, 0.120)
    pz = T - 0.006
    v = lambda loop, z: [QVector3D(x, y, z) for x, y in loop]  # noqa: E731
    m = Mesh()
    m.add_face(v(outline, T), [v(drill, T), v(window, T), v(pocket, T)])
    m.add_face(v(outline[::-1], 0.0), [v(drill[::-1], 0.0), v(window[::-1], 0.0)])
    _walls(m, outline, 0.0, T)
    _walls(m, drill, 0.0, T, outward=False)
    _walls(m, window, 0.0, T, outward=False)
    _walls(m, pocket, pz, T, outward=False)
    m.add_face(v(pocket, pz))
    return Group(m, name="Board")


def _window():
    from views.main_window import MainWindow
    win = MainWindow()
    win.show()
    return win


def _job(settings_file, tmp_path, name="board", setup=True):
    """A window with a model in it and a new CAM job open over it."""
    from plugins.cam.ui.dock import show_dock
    win = _window()
    model = win.viewport.scene
    model.mesh.add_face([QVector3D(0, 0, 0), QVector3D(1, 0, 0), QVector3D(1, 1, 0),
                         QVector3D(0, 1, 0)])
    win._saved_version = model.version
    dock = show_dock(win.viewport)
    assert dock.new_job(tmp_path / f"{name}.igcam")
    if setup:
        dock._on_start_job()
    return win, dock, model


def _draw(win, loop_mm, z=0.0):
    """Draw a closed loop (mm, on the stock top) as loose edges."""
    ring = [QVector3D(x / 1000.0, y / 1000.0, z) for x, y in loop_mm]
    for a, b in zip(ring, ring[1:] + ring[:1]):
        win.viewport.scene.mesh.add_edge(a, b)
    win.viewport.scene.version += 1
    win.viewport.notify_scene_changed()


def _draw_board(win, dock):
    """The board's drawing on a 320 × 220 stock, 10 mm in from its edge."""
    mm = lambda loop: [(x * 1000 + 10, y * 1000 + 10) for x, y in loop]  # noqa: E731
    _draw(win, mm(_rect(0, 0, W, D)))
    _draw(win, mm(_circle(0.040, 0.040, 0.0025)))
    _draw(win, mm(_rect(0.200, 0.120, 0.240, 0.150)))
    _draw(win, mm(_rect(0.080, 0.080, 0.140, 0.120)))
    dock.refresh_paths()


def _set_stock(dock, w, d, h):
    dock.stock_w.set_mm(w)
    dock.stock_d.set_mm(d)
    dock.stock_h.set_mm(h)
    dock._on_stock_edited()


def _choose(dock, predicate):
    rows = {i for i, p in enumerate(dock.paths) if predicate(p)}
    assert rows, "no path matches"
    dock.choose_paths(rows)


def _extent(p):
    return tuple(round(v) for v in p.extent)


def _wait(dock, timeout=20.0):
    t0 = time.monotonic()
    while dock._calc is not None or dock._recalc_timer.isActive():
        _app.processEvents()
        time.sleep(0.01)
        if dock._recalc_timer.isActive():
            dock._recalc_timer.stop()
            dock.calculate()
        assert time.monotonic() - t0 < timeout, "calculation did not finish"
    _app.processEvents()


def _close(win, dock):
    """Leave the job without prompts and tear the dock down."""
    ws = win.workspace()
    if ws is not None:
        ws.mark_saved()
        dock.flush()
        ws.mark_saved()
        win.leave_workspace()
    dock.dispose()


def test_the_plugin_is_in_the_extensions_menu():
    from core.extensions import discover_plugins
    from core.paths import app_root
    plugins, errors = discover_plugins([app_root() / "plugins"])
    assert not [e for e in errors if e.stem == "cam"], errors
    (cam,) = [p for p in plugins if p.stem == "cam"]
    assert [t.name for t in cam.tools] == ["CAM…"]


def test_with_the_model_in_front_the_dock_is_a_start_page(settings_file):
    from plugins.cam.ui.dock import show_dock
    win = _window()
    dock = show_dock(win.viewport)
    assert dock.pages.currentIndex() == 0
    assert not dock.in_job()
    assert len(dock.overlay.stock_edges) == 0          # no stock without a job
    assert not dock.btn_template.isEnabled()            # stock templates: later
    assert "cam" not in (win.viewport.scene.plugin_data or {})
    dock.dispose()


def test_a_new_job_starts_with_its_setup(settings_file, tmp_path):
    """The file first, then the setup: until it is confirmed only Select
    can be picked and only the Job tab is open."""
    win, dock, model = _job(settings_file, tmp_path, setup=False)
    assert (tmp_path / "board.igcam").is_file()
    assert win.viewport.scene is not model
    assert win.windowTitle() == "IngeTrazo — board.igcam"
    assert dock.state.job.name == "board"
    assert [dock.tabs.isTabEnabled(i) for i in range(4)] == [True, False, False, False]
    assert not win._tool_actions["line"].isEnabled()
    dock._on_start_job()
    assert all(dock.tabs.isTabEnabled(i) for i in range(4))
    assert win._tool_actions["line"].isEnabled()
    assert not win._tool_actions["pushpull"].isEnabled()    # 2D only
    assert not win._tool_actions["followme"].isEnabled()
    _close(win, dock)
    assert win.viewport.scene is model
    assert win._tool_actions["pushpull"].isEnabled()


def test_a_job_from_drawing_to_gcode_and_back(settings_file, tmp_path, monkeypatch):
    win, dock, model = _job(settings_file, tmp_path)
    _set_stock(dock, 320.0, 220.0, 18.0)
    _draw_board(win, dock)
    assert len(dock.paths) == 4
    _choose(dock, lambda p: _extent(p) == (60, 40))
    dock._on_add("pocket")
    _choose(dock, lambda p: p.circle is not None)
    dock._on_add("drilling")
    _choose(dock, lambda p: _extent(p) == (40, 30))
    dock._on_add("insideProfile")
    _choose(dock, lambda p: _extent(p) == (300, 200))
    dock._on_add("outsideProfile")
    ops = dock.state.job.operations
    kinds = [o.kind for o in ops]
    assert kinds == ["pocket", "drilling", "insideProfile", "outsideProfile"], kinds
    assert ops[1].parameters.depth == pytest.approx(18.0, abs=1e-3)      # through the stock
    assert len(ops[3].strategy.tabs) == 4
    st = dock.state.job.stock
    assert (st.width, st.depth, st.height) == pytest.approx((320.0, 220.0, 18.0))

    dock.calculate()
    _wait(dock)
    out = dock._result
    assert out is not None and out.ok, out and out.error
    assert not [i for i in out.issues if i.is_error], [(i.code, i.params) for i in out.issues]
    assert dock.btn_export.isEnabled()

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    written = dock.export(str(tmp_path / "board.nc"))
    assert len(written) == 3 and all(p.endswith(".nc") for p in written)   # GRBL: per tool
    dock.controller.setCurrentIndex(dock.controller.findData("linuxcnc"))
    dock.calculate()
    _wait(dock)
    written = dock.export(str(tmp_path / "board.ngc"))
    assert [p.rsplit(".", 1)[1] for p in written] == ["ngc", "tbl"]
    text = open(written[0]).read()
    assert "G99 G81" in text and "T1 M6" in text and "T3 M6" in text

    # Save, back to the model, reopen.
    assert win._is_dirty()
    assert dock.save_job()
    assert not win._is_dirty()
    assert dock.leave_job()
    assert win.viewport.scene is model and len(model.mesh.faces) == 1
    assert not win._is_dirty()                          # the model is as it was
    assert "cam" not in (model.plugin_data or {})       # no job inside the .igz
    assert dock.pages.currentIndex() == 0
    assert dock.recent_list.count() == 1
    assert dock.open_job(tmp_path / "board.igcam")
    assert [o.kind for o in dock.state.job.operations] == kinds
    assert dock.state.job.post.controller == "linuxcnc"
    assert len(dock.paths) == 4
    _close(win, dock)


def test_leaving_an_unsaved_job_asks(settings_file, tmp_path, monkeypatch):
    win, dock, model = _job(settings_file, tmp_path)
    dock._on_add("facing")
    dock.flush()
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.Cancel)
    assert not dock.leave_job()
    assert dock.in_job()
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.Discard)
    assert dock.leave_job()
    assert win.viewport.scene is model
    from plugins.cam.jobfile import load_job
    from core.scene import Scene
    assert load_job(Scene(), tmp_path / "board.igcam").job.operations == []   # discarded
    dock.dispose()


def test_a_job_file_opens_from_the_window(settings_file, tmp_path):
    """Open Recent, the command line and a double-click land in
    MainWindow.open_path: an .igcam goes to the CAM plugin."""
    win, dock, _model = _job(settings_file, tmp_path)
    _close(win, dock)
    from plugins.cam.ui.dock import show_dock
    dock = show_dock(win.viewport)
    assert win.open_path(tmp_path / "board.igcam")
    assert dock.in_job() and win.windowTitle() == "IngeTrazo — board.igcam"
    _close(win, dock)


def test_undo_reaches_the_job(settings_file, tmp_path):
    win, dock, _model = _job(settings_file, tmp_path)
    dock._on_add("facing")
    dock.flush()
    assert len(dock.state.job.operations) == 1
    win.viewport.history.undo()
    win.viewport.notify_scene_changed()
    _app.processEvents()
    assert dock.state.job.operations == []
    win.viewport.history.redo()
    win.viewport.notify_scene_changed()
    _app.processEvents()
    assert [o.kind for o in dock.state.job.operations] == ["facing"]
    _close(win, dock)


def test_units_switch_relabels_without_changing_numbers(settings_file, tmp_path):
    win, dock, _model = _job(settings_file, tmp_path)
    width = dock.state.job.stock.width
    dock.units.setCurrentIndex(dock.units.findData("inches"))
    assert dock.state.job.units == "inches"
    assert dock.state.job.stock.width == pytest.approx(width)
    assert dock.stock_w.suffix() == " in"
    assert dock.stock_w.value() == pytest.approx(width / 25.4, abs=1e-4)
    _close(win, dock)


def test_the_stock_top_is_the_drawing_surface(settings_file, tmp_path):
    """The stock's top is the ground plane of the job and its front-left
    corner the origin, so what is drawn on the ground is drawn on it; the
    body of the stock lies below."""
    win, dock, _model = _job(settings_file, tmp_path)
    _set_stock(dock, 200.0, 100.0, 20.0)
    zs = dock.overlay.stock_edges[:, :, 2]
    assert zs.max() == pytest.approx(0.0, abs=1e-9)
    assert zs.min() == pytest.approx(-0.020, abs=1e-9)
    xs = dock.overlay.stock_top[:, 0]
    ys = dock.overlay.stock_top[:, 1]
    assert (xs.min(), xs.max(), ys.min(), ys.max()) == pytest.approx((0, 0.2, 0, 0.1))
    # Geometry never moves the stock.
    _draw(win, _rect(500, 500, 600, 600))
    dock.refresh_paths()
    _choose(dock, lambda p: True)
    dock._on_add("engraving")
    assert dock.state.stock_min_plane() == (0.0, 0.0)
    _close(win, dock)


def test_extract_faces_and_edges_on_the_top_plane():
    from plugins.cam.extract import extract_edges, extract_faces, extract_part
    g = board()
    top = max(g.mesh.faces, key=lambda f: (f.normal().z(), f.area()))
    ex = extract_faces([top])
    (outer, holes) = ex.regions[0]
    assert len(holes) == 3
    assert ex.frame.n == (0.0, 0.0, 1.0)
    us = [p[0] for p in outer.points]
    assert max(us) - min(us) == pytest.approx(300.0, abs=1e-3)
    assert sum(1 for h in holes if h.circle) == 1
    ring = [e for e in g.mesh.edges if all(abs(p.z() - T) < 1e-6 for p in (e.a, e.b))
            and abs(e.a.x() - e.b.x()) + abs(e.a.y() - e.b.y()) > 0.1]
    ex = extract_edges(ring)
    assert len(ex.loops) == 1
    part = extract_part(g)
    assert part.thickness == pytest.approx(18.0, abs=1e-3)
    kinds = sorted((h.through, bool(h.circle)) for h in part.regions[0][1])
    assert kinds == [(False, False), (True, False), (True, True)]


def test_importing_outlines_from_the_model(settings_file, tmp_path):
    """On request only: what was selected in the model comes in flattened,
    centred on the stock; the model is only read."""
    from plugins.cam.ui.dock import show_dock
    win = _window()
    model = win.viewport.scene
    g = board()
    model.groups.append(g)
    model.selection.clear()
    model.selection.add(g)
    faces_before = len(g.mesh.faces)
    dock = show_dock(win.viewport)
    dock.new_job(tmp_path / "imp.igcam")
    dock._on_start_job()
    _set_stock(dock, 400.0, 300.0, 18.0)
    assert dock.path_list.count() == 0
    assert dock.import_from_model() == 4                  # outline + 3 holes
    assert len(dock.paths) == 4
    outline = max(dock.paths, key=lambda p: p.area)
    us = [p[0] for p in outline.points]
    vs = [p[1] for p in outline.points]
    assert (min(us), max(us), min(vs), max(vs)) == pytest.approx((50, 350, 50, 250), abs=1e-3)
    win.viewport.history.undo()                           # one undo step
    win.viewport.notify_scene_changed()
    dock.refresh_paths()
    assert dock.paths == []
    _close(win, dock)
    assert len(g.mesh.faces) == faces_before and g in model.groups


def test_toolpaths_stay_drawn_while_another_tray_is_in_front(settings_file, tmp_path):
    """The dock is tabbed with the trays; looking at Properties must not
    make the toolpaths vanish — only closing CAM does."""
    win, dock, _model = _job(settings_file, tmp_path)
    win.tray.raise_()
    _app.processEvents()
    assert dock.overlay.visible
    dock.toggleViewAction().trigger()           # closed from the Window menu
    _app.processEvents()
    assert not dock.overlay.visible
    _close(win, dock)


def test_bore_and_chamfer_on_paths(settings_file, tmp_path):
    win, dock, _model = _job(settings_file, tmp_path)
    _set_stock(dock, 320.0, 220.0, 18.0)
    _draw_board(win, dock)
    _choose(dock, lambda p: p.circle is not None)
    dock._on_add("bore")
    _choose(dock, lambda p: _extent(p) == (300, 200))
    dock._on_add("chamfer")
    kinds = [o.kind for o in dock.state.job.operations]
    assert kinds == ["bore", "chamfer"]
    bore = dock.state.job.operations[0]
    assert bore.parameters.diameter == pytest.approx(5.0, abs=0.01)
    assert dock.state.job.tool(bore.toolID).diameter < 5.0
    # The only default mill under 5 mm is the 3 mm one, whose 12 mm flute
    # cannot reach 18 mm down: that must be refused, and said plainly.
    dock.calculate()
    _wait(dock)
    assert not dock._result.ok
    assert dock._result.error.code == "axial_depth_exceeds_flute"
    assert "flute length" in dock.status.text()
    assert not dock.btn_export.isEnabled()
    bore.parameters.depth = 6.0                 # a blind bore within reach
    dock._changed()
    dock.calculate()
    _wait(dock)
    assert dock._result.ok, dock._result.error
    assert not [i for i in dock._result.issues if i.is_error], \
        [(i.code, i.params) for i in dock._result.issues]
    _close(win, dock)


def test_cam_hands_the_keyboard_back_to_the_model(settings_file, tmp_path):
    """Space is IngeTrazo's Select key, a window shortcut: a CAM field that
    keeps the focus swallows it. Pressing Enter in a field must leave the
    keyboard with the drawing."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    win, dock, _model = _job(settings_file, tmp_path)
    win.activateWindow()
    dock.tabs.setCurrentIndex(0)
    dock.job_name.setFocus()
    QTest.keyClick(dock.job_name, Qt.Key_Return)
    assert not dock.job_name.hasFocus()
    dock.safe.setFocus()
    QTest.keyClick(dock.safe.lineEdit(), Qt.Key_Enter)
    assert not dock.safe.hasFocus()
    _close(win, dock)


def test_a_drawn_rectangle_is_one_path_and_one_edge_picks_it(settings_file, tmp_path):
    """Reported 2026-09-26: «a rectangle in draw is not four lines in the
    CAM tab, it is a single path». Clicking one of its edges chooses the
    whole path, and an operation added then follows the whole rectangle."""
    from plugins.cam.engine import geometry as geo
    win, dock, _model = _job(settings_file, tmp_path)
    _draw(win, _rect(10, 10, 110, 70))
    dock.refresh_paths()
    assert dock.path_list.count() == 1
    scene = win.viewport.scene
    edge = scene.mesh.edges[0]
    scene.selection.clear()
    scene.selection.add(edge)
    dock._sync_paths_from_model()
    assert [p.closed for p in dock.chosen_paths()] == [True]
    assert len(dock.overlay.chosen_a) == 4             # the whole rectangle is highlighted
    dock._on_add("outsideProfile")
    (op,) = dock.state.job.operations
    assert abs(geo.signed_area(op.strategy.geometry.boundary)) == pytest.approx(6000.0)
    scene.selection.clear()
    dock._sync_paths_from_model()
    assert dock.chosen_paths() == []
    dock._on_add("pocket")                              # nothing chosen: said plainly
    assert "Choose a path first" in dock.status.text()
    _close(win, dock)


def test_operation_form_check_boxes_wrap_their_text_and_still_toggle(settings_file):
    """A check box's text is one line; in Spanish «Recorrido más corto entre
    agujeros» made the operation form 260 px wide. The text is now a
    wrapping row label, and clicking it toggles the box."""
    from plugins.cam.ui.op_forms import OperationForm
    form = OperationForm()
    assert form.nearest.text() == ""
    label = form.form.labelForField(form.nearest)
    assert label.wordWrap()
    form.setEnabled(True)                   # no operation loaded: enable by hand
    form.nearest.setEnabled(True)
    before = form.nearest.isChecked()
    label.mousePressEvent(None)
    assert form.nearest.isChecked() != before
