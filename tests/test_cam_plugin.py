# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The CAM plugin end to end, in a real main window.

A board is modelled with a round through hole, a square through hole and a
blind pocket; the part is selected and "Part → operations" run; the job is
calculated on the worker thread, verified, exported for GRBL and LinuxCNC,
saved in the .igz and reopened intact. Plus the pieces around it: the
plugin loads from the Extensions menu, its dock registers with the
window, extraction reads faces and edges, and undo reaches the job.
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


def _window_with_board(settings_file):
    from views.main_window import MainWindow
    win = MainWindow()
    win.show()
    g = board()
    win.viewport.scene.groups.append(g)
    win.viewport.scene.selection.clear()
    win.viewport.scene.selection.add(g)
    win.viewport.notify_scene_changed()
    return win, g


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


def test_the_plugin_is_in_the_extensions_menu():
    from core.extensions import discover_plugins
    from core.paths import app_root
    plugins, errors = discover_plugins([app_root() / "plugins"])
    assert not [e for e in errors if e.stem == "cam"], errors
    (cam,) = [p for p in plugins if p.stem == "cam"]
    assert [t.name for t in cam.tools] == ["CAM…"]


def test_part_to_gcode_end_to_end(settings_file, tmp_path, monkeypatch):
    from plugins.cam.ui.dock import show_dock
    win, g = _window_with_board(settings_file)
    dock = show_dock(win.viewport)
    assert win.plugin_docks()["cam_dock"] is dock

    dock._on_part_operations()
    ops = dock.state.job.operations
    kinds = [o.kind for o in ops]
    assert kinds == ["pocket", "drilling", "insideProfile", "outsideProfile"], kinds
    pocket, drill, window_op, outline = ops
    assert pocket.parameters.depth == pytest.approx(6.0, abs=1e-3)
    assert drill.parameters.depth == pytest.approx(18.0, abs=1e-3)
    assert len(drill.parameters.points) == 1
    assert outline.parameters.depth == pytest.approx(18.0, abs=1e-3)
    assert len(outline.strategy.tabs) == 4
    st = dock.state.job.stock
    assert (st.width, st.depth, st.height) == pytest.approx((320.0, 220.0, 18.0), abs=1e-3)

    dock.calculate()
    _wait(dock)
    out = dock._result
    assert out is not None and out.ok, out and out.error
    assert not [i for i in out.issues if i.is_error], [(i.code, i.params) for i in out.issues]
    assert dock.btn_export.isEnabled()
    assert len(dock.overlay.a) > 100

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    written = dock.export(str(tmp_path / "board.nc"))
    # GRBL: one file per tool (6 mm mill → drill → 6 mm mill again).
    assert len(written) == 3 and all(p.endswith(".nc") for p in written)
    dock.controller.setCurrentIndex(dock.controller.findData("linuxcnc"))
    dock.calculate()
    _wait(dock)
    written = dock.export(str(tmp_path / "board.ngc"))
    assert [p.rsplit(".", 1)[1] for p in written] == ["ngc", "tbl"]   # + tool table
    assert open(written[1]).read().splitlines()[1].startswith("T1 P1 D6.0000")
    text = open(written[0]).read()
    assert "G99 G81" in text and "T1 M6" in text and "T3 M6" in text

    # Saved in the document and back.
    dock.flush()
    from formats import igz
    path = tmp_path / "board.igz"
    igz.save_scene(win.viewport.scene, path)
    from core.scene import Scene
    back = Scene()
    igz.load_into(back, path)
    from plugins.cam.state import CamState
    again = CamState.from_dict(back.plugin_data["cam"])
    assert [o.kind for o in again.job.operations] == kinds
    assert again.job.post.controller == "linuxcnc"
    dock.dispose()


def test_undo_reaches_the_job(settings_file):
    from plugins.cam.ui.dock import show_dock
    win, _g = _window_with_board(settings_file)
    dock = show_dock(win.viewport)
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
    dock.dispose()


def test_units_switch_relabels_without_changing_numbers(settings_file):
    from plugins.cam.ui.dock import show_dock
    win, _g = _window_with_board(settings_file)
    dock = show_dock(win.viewport)
    dock._on_part_operations()
    width = dock.state.job.stock.width
    dock.units.setCurrentIndex(dock.units.findData("inches"))
    assert dock.state.job.units == "inches"
    assert dock.state.job.stock.width == pytest.approx(width)
    assert dock.stock_w.suffix() == " in"
    assert dock.stock_w.value() == pytest.approx(width / 25.4, abs=1e-4)
    dock.dispose()


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


def test_toolpaths_stay_drawn_while_another_tray_is_in_front(settings_file):
    """The dock is tabbed with the trays; looking at Properties must not
    make the toolpaths vanish from the model — only closing CAM does."""
    from plugins.cam.ui.dock import show_dock
    win, _g = _window_with_board(settings_file)
    dock = show_dock(win.viewport)
    win.tray.raise_()
    _app.processEvents()
    assert dock.overlay.visible
    dock.toggleViewAction().trigger()           # closed from the Window menu
    _app.processEvents()
    assert not dock.overlay.visible
    dock.dispose()


def test_refresh_follows_the_part_and_keeps_parameters(settings_file):
    """The model changes after the job was made: Refresh moves every part
    operation to the new geometry and keeps what the user set."""
    from plugins.cam.ui.dock import show_dock
    win, g = _window_with_board(settings_file)
    dock = show_dock(win.viewport)
    dock._on_part_operations()
    outline = dock.state.job.operations[-1]
    outline.parameters.stepDown = 4.0
    # Stretch the board 20 mm along X (every vertex right of 0.25 m).
    for v in g.mesh.vertices:
        if v.position.x() > 0.25:
            v.position.setX(v.position.x() + 0.020)
    win.viewport.notify_scene_changed()
    dock._on_refresh()
    us = [p[0] for p in outline.strategy.geometry.boundary]
    assert max(us) - min(us) == pytest.approx(320.0, abs=1e-3)
    assert outline.parameters.stepDown == 4.0
    win.viewport.scene.groups.remove(g)
    dock._on_refresh()
    assert "no longer in the model" in dock.status.text()
    dock.dispose()


def test_bore_and_chamfer_from_the_top_face_through_the_dock(settings_file):
    from plugins.cam.ui.dock import show_dock
    win, g = _window_with_board(settings_file)
    dock = show_dock(win.viewport)
    top = max(g.mesh.faces, key=lambda f: (round(f.normal().z(), 3), f.area()))
    sc = win.viewport.scene
    sc.selection.clear()
    sc.selection.add(top)
    dock._on_add("bore")
    dock._on_add("chamfer")
    kinds = [o.kind for o in dock.state.job.operations]
    assert kinds.count("bore") == 1 and kinds.count("chamfer") == 4   # outline + 3 holes
    bore = next(o for o in dock.state.job.operations if o.kind == "bore")
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
    # A blind 6 mm bore is within reach: then the job calculates clean.
    bore.parameters.depth = 6.0
    dock._changed()
    dock.calculate()
    _wait(dock)
    assert dock._result.ok, dock._result.error
    assert not [i for i in dock._result.issues if i.is_error], \
        [(i.code, i.params) for i in dock._result.issues]
    dock.dispose()
