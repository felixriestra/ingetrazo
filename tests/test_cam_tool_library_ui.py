# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The tool library from the CAM dock: pick a cutter into a job with its
feeds for the job's material, keep a job's tool for later, edit, trash and
import a vendor catalogue. Dialogs are driven directly — a modal
``exec()`` would hang the test run."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtCore
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QVector3D
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

_app = QApplication.instance() or QApplication([])

FIXTURES = Path(__file__).parent / "data" / "cam_toollib_fixtures"


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    path = tmp_path / "prefs.ini"
    factory = lambda *a: QSettings(str(path), QSettings.IniFormat)  # noqa: E731
    import PySide6.QtCore as qc
    import views.main_window as mw
    monkeypatch.setattr(qc, "QSettings", factory)
    monkeypatch.setattr(mw, "QSettings", factory)
    return path


@pytest.fixture
def lib(settings_file, tmp_path, monkeypatch):
    """The shared library, in a temporary file, and no message box ever
    shown modally: each call is recorded instead."""
    from plugins.cam.ui import library
    QtCore.QSettings().setValue(library.SETTINGS_KEY, str(tmp_path / "lib" / "ToolLibrary.sqlite"))
    said = []
    for name in ("information", "warning"):
        monkeypatch.setattr(QMessageBox, name,
                            staticmethod(lambda *a, **k: said.append(a[2]) or QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: said.append(a[2]) or QMessageBox.Yes))
    library.close_shared_library()
    repo = library.shared_library()
    yield library, repo, said
    library.close_shared_library()


def _job_dock(tmp_path):
    from plugins.cam.ui.dock import show_dock
    from views.main_window import MainWindow
    win = MainWindow()
    win.show()
    model = win.viewport.scene
    model.mesh.add_face([QVector3D(0, 0, 0), QVector3D(1, 0, 0), QVector3D(1, 1, 0),
                         QVector3D(0, 1, 0)])
    win._saved_version = model.version
    dock = show_dock(win.viewport)
    assert dock.new_job(tmp_path / "board.igcam")
    dock._on_start_job()
    return win, dock


def _close(win, dock):
    ws = win.workspace()
    if ws is not None:
        ws.mark_saved()
        dock.flush()
        ws.mark_saved()
        win.leave_workspace()
    dock.dispose()


def _row_of(dlg, name):
    return next(i for i, (t, _v, _r) in enumerate(dlg._tools) if t.name == name)


def test_a_library_tool_goes_into_the_job_with_feeds_for_its_material(lib, tmp_path,
                                                                     monkeypatch):
    library, repo, _said = lib
    win, dock = _job_dock(tmp_path)
    try:
        job = dock.state.job
        job.stock.material = "mdf"
        before = len(job.tools)

        def pick(self):
            self.table.selectRow(_row_of(self, "6 mm End Mill"))
            self._on_add()
            return QDialog.Accepted
        monkeypatch.setattr(library.ToolLibraryDialog, "exec", pick)
        dock._on_tool_from_library()
        assert len(job.tools) == before + 1
        added = job.tools[-1]
        assert added.name == "6 mm End Mill" and added.number == before + 1
        want = repo.resolve(next(t for t in repo.tools() if t.name == "6 mm End Mill"), "mdf",
                            library.machine_limits(job))
        assert (added.spindleRPM, added.cuttingFeed, added.plungeFeed) == (
            want.spindle_rpm, want.feed_xy_mm_min, want.feed_z_mm_min)
        assert added.recommendedStepdownProvenance == "ruleEstimate"
        # The tool list shows it, selected.
        assert dock.tool_list.currentItem().text().endswith("6 mm End Mill") or \
            "6 mm End Mill" in dock.tool_list.currentItem().text()
    finally:
        _close(win, dock)


def test_a_jobs_tool_is_kept_in_the_library_with_its_feeds_as_the_users(lib, tmp_path):
    library, repo, said = lib
    win, dock = _job_dock(tmp_path)
    try:
        dock.state.job.stock.material = "plywood"
        dock.tool_list.setCurrentRow(0)
        t = dock._current_tool()
        dock._on_tool_to_library()
        kept = next(x for x in repo.tools() if x.name == t.name)
        (preset,) = repo.presets(kept.id)
        assert (preset.origin, preset.material_class_id, preset.feed_xy_mm_min) == (
            "user", "plywood", t.cuttingFeed)
        # Resolving it for plywood now gives the user's own numbers back.
        r = repo.resolve(kept, "plywood", library.machine_limits(dock.state.job))
        assert (r.source, r.provenance, r.feed_xy_mm_min) == ("preset", "userOverride",
                                                               t.cuttingFeed)
        assert "plywood".lower() in said[-1].lower() or "Plywood" in said[-1]
    finally:
        _close(win, dock)


def test_steel_gets_no_guessed_feeds(lib, tmp_path):
    library, repo, said = lib
    win, dock = _job_dock(tmp_path)
    try:
        dock.state.job.stock.material = "steel"
        dlg = library.ToolLibraryDialog(repo, dock.state.job, pick=True)
        # No material class for steel: the choice falls to the first class,
        # and the user sees which one — nothing is resolved for «steel».
        assert library.material_class_for(dock.state.job) is None
        dlg.close()
        dock.tool_list.setCurrentRow(0)
        dock._on_tool_to_library()
        kept = next(x for x in repo.tools() if x.name == dock._current_tool().name)
        assert repo.presets(kept.id) == []
    finally:
        _close(win, dock)


def test_the_library_window_shows_estimates_and_explains_them(lib, tmp_path):
    library, repo, _said = lib
    from plugins.cam.engine.models import Job
    job = Job()
    job.stock.material = "plastic"                      # → acrylic, which melts
    dlg = library.ToolLibraryDialog(repo, job)
    assert dlg.class_id() == "acrylic"
    dlg.table.selectRow(_row_of(dlg, "16 mm End Mill"))
    speed = dlg.table.item(dlg.table.currentRow(), 6).text()
    assert speed.startswith("≈ ")                        # a generic band: an estimate
    text = dlg.detail.text()
    assert "Generic chip-load estimate" in text and "air cut" in text
    dlg.close()


def test_incomplete_and_form_tools_cannot_be_added(lib, tmp_path):
    library, repo, _said = lib
    from plugins.cam.engine.models import Job
    from plugins.cam.toollib.model import FormPoint, LibraryTool
    repo.insert(LibraryTool(name="Half known", diameter_mm=6, flute_count=2))
    repo.insert(LibraryTool(name="Ogee", type="form", diameter_mm=20, flute_count=2,
                            flute_length_mm=20, overall_length_mm=60,
                            form_points=[FormPoint(3, 0), FormPoint(10, 12)]))
    dlg = library.ToolLibraryDialog(repo, Job(), pick=True)
    for name, word in (("Half known", "Missing"), ("Ogee", "form tool")):
        dlg.table.selectRow(_row_of(dlg, name))
        assert not dlg.btn_add.isEnabled()
        assert word in dlg.detail.text()
    dlg.close()


def test_edit_marks_hand_set_values_and_a_ball_keeps_half_its_diameter(lib):
    library, repo, _said = lib
    tool = next(t for t in repo.tools() if t.type == "ball_nose")
    ed = library.ToolEditor(tool, repo, inch=False)
    ed.diameter.put(4.0)
    ed.corner.put(1.0)                                 # wrong on purpose
    ed.flute_len.put(15.0)
    ed._on_save()
    assert ed.result() == QDialog.Accepted
    assert tool.corner_radius_mm == 2.0                # corrected to D/2
    repo.update(tool, mark_user_edited=ed.edited_fields())
    assert {"diameter_mm", "flute_length_mm"} <= repo.user_edited_fields("tool", tool.id)


def test_an_editor_contradiction_is_refused(lib):
    library, repo, said = lib
    tool = repo.tools()[0]
    ed = library.ToolEditor(tool, repo, inch=False)
    ed.flute_len.put(90.0)
    ed.overall.put(50.0)
    ed._on_save()
    assert ed.result() != QDialog.Accepted
    assert "longer than the overall length" in said[-1]


def test_trash_and_restore_from_the_window(lib):
    library, repo, _said = lib
    from plugins.cam.engine.models import Job
    dlg = library.ToolLibraryDialog(repo, Job())
    dlg.table.selectRow(_row_of(dlg, "8 mm End Mill"))
    dlg._on_delete()
    assert "8 mm End Mill" not in [t.name for t, _v, _r in dlg._tools]
    trash = library.TrashDialog(repo)
    trash.list.setCurrentRow(0)
    trash._on_restore()
    dlg.refresh()
    assert "8 mm End Mill" in [t.name for t, _v, _r in dlg._tools]
    trash.close()
    dlg.close()


def test_a_vendor_catalogue_is_reviewed_then_imported_then_undone(lib):
    library, repo, said = lib
    from plugins.cam.engine.models import Job
    dlg = library.ImportDialog(repo, Job())
    dlg.profile.setCurrentIndex(0)                    # Sorotec
    dlg.load(FIXTURES / "sorotec_cp1252.csv")
    plan = dlg.plan
    assert (plan.new_count, plan.rejected_count) == (5, 3)
    assert "5 new" in dlg.summary.text() and "3 rejected" in dlg.summary.text()
    rejected = next(i for i, r in enumerate(plan.rows) if r.disposition == "rejected")
    assert dlg.table.item(rejected, 0).flags() & Qt.ItemIsUserCheckable == Qt.NoItemFlags
    dlg.table.selectRow(rejected)
    assert dlg.problems.text().startswith("✕")
    # Untick one new row: it stays out.
    first_new = next(i for i, r in enumerate(plan.rows) if r.disposition == "new")
    dlg.table.item(first_new, 0).setCheckState(Qt.Unchecked)
    assert plan.new_count == 4
    before = len(repo.tools())
    dlg._on_import()
    assert dlg.result_.inserted == 4 and len(repo.tools()) == before + 4
    window = library.ToolLibraryDialog(repo, Job())
    assert window.btn_undo.isEnabled()
    window._on_undo_import()
    assert len(repo.tools()) == before
    dlg.close()
    window.close()


def test_the_dock_stays_narrow_with_the_library_buttons(lib, tmp_path):
    from views.tray import Tray  # noqa: F401 — the host's width contract
    win, dock = _job_dock(tmp_path)
    try:
        dock.tabs.setCurrentIndex(1)                    # Tools
        dock.resize(245, 700)
        _app.processEvents()
        assert dock.btn_from_library.isVisible() and dock.btn_to_library.isVisible()
        assert dock.widget().minimumSizeHint().width() <= 245
    finally:
        _close(win, dock)
