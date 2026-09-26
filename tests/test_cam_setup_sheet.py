# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The setup sheet: the page for the machine.

Its content (engine/sheet.py) against 2DCam's rules and 2DCam's own time
estimates, what it says when something is wrong, and the PDF itself —
read back with QtPdf, so the test checks the words that reach the paper.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from plugins.cam.engine import io, sheet  # noqa: E402
from plugins.cam.engine.models import Operation  # noqa: E402

FIXTURES = Path(__file__).parent / "data" / "cam_fixtures"


def _job(name):
    return io.job_from_dict(json.loads((FIXTURES / name / "job.json").read_text()))


def _expected(name):
    return json.loads((FIXTURES / name / "expected.json").read_text())


@pytest.mark.parametrize("name", ["job_multi_operation", "drill_input_order",
                                  "pocket_finishing_tool", "profile_outside_tabs_leads_ramp"])
def test_the_estimated_time_is_the_programs(name):
    """The sheet's total is the time of the very program export writes, and
    so 2DCam's within the parity tolerance — except where the parity corpus
    records a known difference in the motion itself (tabs, for one)."""
    from plugins.cam.engine.compiler import compile_job
    from tests.test_cam_parity import DEVIATIONS as deviations
    job = _job(name)
    s = sheet.build(job, post=False)
    assert s.total_seconds == compile_job(job).toolpath.estimated_duration(job.machine)
    if name not in deviations:
        assert s.total_seconds == pytest.approx(_expected(name)["machineDurationSeconds"],
                                                rel=2e-3)


def test_the_multi_operation_job():
    job = _job("job_multi_operation")
    job.post.controller = "grbl"
    s = sheet.build(job)
    assert [(t.number, t.used_by) for t in s.tools] == [(1, [1, 2, 4]), (3, [3])]
    assert [o.status for o in s.operations] == ["ok", "ok", "ok", "ok", "disabled"]
    assert s.enabled_count == 4 and s.warnings == []
    # GRBL: one file per tool change, in run order.
    assert [f.tool_numbers for f in s.files] == [[1], [3], [1]]
    assert len(s.plan_points) == 2 and s.plan_paths
    assert {n for n, _p in s.plan_paths} == {1, 2, 4}
    job.post.controller = "linuxcnc"
    s = sheet.build(job)
    assert [(f.suffix, f.extension, f.tool_numbers) for f in s.files] == [("", ".ngc", [1, 3])]
    assert s.has_tool_table


def test_a_failing_operation_is_named_and_the_totals_are_withheld():
    s = sheet.build(_job("error_drill_outside_stock"))
    (op,) = s.operations
    assert op.status == "failed" and op.issue.code == "point_outside_stock"
    assert [w.code for w in s.warnings] == ["point_outside_stock"]
    assert s.total_seconds is None and s.files is None


def test_one_failure_leaves_the_other_operations_their_times():
    job = _job("job_multi_operation")
    job.operations[2].parameters.points = [(500.0, 500.0, 0.0)]      # a hole off the stock
    s = sheet.build(job)
    assert [o.status for o in s.operations] == ["ok", "ok", "failed", "ok", "disabled"]
    assert all(o.seconds for o in s.operations if o.status == "ok")
    assert s.total_seconds is None                  # it would leave the holes out


def test_what_the_sheet_warns_about():
    job = _job("pocket_rect")
    job.operations.append(Operation("Orphan", "pocket", toolID=None))
    job.machine.enforcesTravelLimits = True
    job.machine.travelMinimum = (0.0, 0.0, -10.0)
    job.machine.travelMaximum = (50.0, 1000.0, 300.0)
    codes = [w.code for w in sheet.build(job, post=False).warnings]
    assert "sheet_operation_without_tool" in codes and "sheet_stock_exceeds_x" in codes
    for op in job.operations:
        op.isEnabled = False
    assert "sheet_all_disabled" in [w.code for w in sheet.build(job, post=False).warnings]
    job.operations = []
    assert "sheet_no_operations" in [w.code for w in sheet.build(job, post=False).warnings]


def test_compact_ranges_and_durations_follow_2dcam():
    assert sheet.compact_ranges([8, 1, 2, 3, 5, 7]) == "1–3, 5, 7, 8"
    assert sheet.compact_ranges([]) == ""
    assert sheet.duration(3661.5) == "1:01:02" and sheet.duration(None) == "—"


def _pdf_text(path) -> list:
    from PySide6.QtPdf import QPdfDocument
    doc = QPdfDocument()
    doc.load(str(path))
    return [doc.getAllText(i).text() for i in range(doc.pageCount())]


def test_the_pdf_carries_everything(tmp_path):
    from plugins.cam.ui.setup_sheet import write_pdf
    job = _job("job_multi_operation")
    job.post.controller = "grbl"
    path = tmp_path / "sheet.pdf"
    pages = write_pdf(job, str(path), "board", prepared=dt.datetime(2026, 9, 26, 12, 30))
    text = _pdf_text(path)
    assert len(text) == pages >= 1
    whole = "\n".join(text)
    for words in ("SETUP SHEET", "Multi", "2026-09-26 12:30", "0:09:01", "Front-left corner",
                  "board_01_T1", "board_02_T3", "set Z zero again", "PLAN VIEW",
                  "Tools", "Operations", "1, 2, 4", "Disabled", f"Page {pages} of {pages}"):
        assert words in whole, words


def test_a_linuxcnc_sheet_names_its_tool_table(tmp_path):
    from plugins.cam.ui.setup_sheet import write_pdf
    job = _job("drill_input_order")
    job.post.controller = "linuxcnc"
    write_pdf(job, str(tmp_path / "s.pdf"), "part")
    whole = "\n".join(_pdf_text(tmp_path / "s.pdf"))
    assert "part.ngc" in whole and "part.tbl" in whole


def test_the_pdf_speaks_the_users_language(tmp_path, monkeypatch):
    import plugins.cam.i18n as cam_i18n
    from plugins.cam.ui.setup_sheet import write_pdf
    monkeypatch.setattr(cam_i18n, "_language", lambda: "es")
    write_pdf(_job("error_drill_outside_stock"), str(tmp_path / "s.pdf"), "x")
    whole = "\n".join(_pdf_text(tmp_path / "s.pdf"))
    assert "HOJA DE PREPARACIÓN" in whole and "Comprueba antes de mecanizar".upper() in whole.upper()


def test_many_operations_run_onto_more_pages(tmp_path):
    from plugins.cam.ui.setup_sheet import write_pdf
    job = _job("job_multi_operation")
    base = list(job.operations)
    job.operations = [Operation(f"Copy {i}", o.kind, o.toolID, o.finishingToolID, o.isEnabled,
                                o.parameters, o.strategy) for i in range(12) for o in base]
    pages = write_pdf(job, str(tmp_path / "long.pdf"), "long")
    text = _pdf_text(tmp_path / "long.pdf")
    assert pages >= 3 and len(text) == pages
    assert "Copy 11" in text[-1]


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    path = tmp_path / "prefs.ini"
    factory = lambda *a: QSettings(str(path), QSettings.IniFormat)  # noqa: E731
    import PySide6.QtCore as qc
    import views.main_window as mw
    monkeypatch.setattr(qc, "QSettings", factory)
    monkeypatch.setattr(mw, "QSettings", factory)
    return path


def test_the_output_tab_writes_the_sheet_of_the_open_job(settings_file, tmp_path):
    from PySide6.QtGui import QVector3D
    from plugins.cam.ui.dock import show_dock
    from views.main_window import MainWindow
    win = MainWindow()
    win.show()
    win.viewport.scene.mesh.add_face([QVector3D(0, 0, 0), QVector3D(1, 0, 0),
                                      QVector3D(1, 1, 0), QVector3D(0, 1, 0)])
    win._saved_version = win.viewport.scene.version
    dock = show_dock(win.viewport)
    try:
        assert dock.new_job(tmp_path / "shelf.igcam")
        dock._on_start_job()
        assert dock.btn_sheet.text().startswith("Setup sheet")
        written = dock.setup_sheet(str(tmp_path / "shelf-sheet"))
        assert written.endswith(".pdf") and Path(written).is_file()
        whole = "\n".join(_pdf_text(written))
        assert "shelf" in whole and "The job has no operations." in whole
        assert "Saved the setup sheet" in dock.status.text()
    finally:
        ws = win.workspace()
        if ws is not None:
            ws.mark_saved()
            dock.flush()
            ws.mark_saved()
            win.leave_workspace()
        dock.dispose()


@pytest.mark.parametrize("controller", ["grbl", "linuxcnc"])
@pytest.mark.parametrize("extra_ops", range(0, 40, 3))
def test_a_table_title_never_ends_a_page_without_its_header_and_a_row(extra_ops, controller):
    """Found while checking the Portuguese sheet: the tools table's title
    and header ended page 1, its rows began page 2 — the look-ahead forgot
    the spacing between blocks."""
    from plugins.cam.ui.setup_sheet import blocks, paginate, words
    job = _job("job_multi_operation")
    job.post.controller = controller
    base = list(job.operations)
    job.operations = base + [Operation(f"Extra {i}", base[0].kind, base[0].toolID,
                                       parameters=base[0].parameters,
                                       strategy=base[0].strategy) for i in range(extra_ops)]
    s = sheet.build(job)
    pages = paginate(blocks(words(s, "x"), s))
    for page in pages:
        kinds = [k for k, _p, _h in page]
        for i, k in enumerate(kinds):
            if k == "section":
                assert kinds[i + 1:i + 3] in (["tool_header", "tool"],
                                              ["operation_header", "operation"]), kinds
