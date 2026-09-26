# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Stock-removal simulation and playback (port of 2DCam's height-field
simulator, with vectorised sweeps and playback in machine time).

The simulated stock must agree with the geometry: a rectangular pocket
removes exactly its volume, a through cut reaches the bottom, tabs leave
material at their height, a ball end mill leaves a rounded groove, and
scrubbing back and forth gives the same stock as playing straight through.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

from plugins.cam.engine import compiler, io
from plugins.cam.engine.models import (Job, Operation, PocketParameters, ProfileParameters,
                                       Region, Stock, Strategy, Tab, Tool, EngravingParameters)
from plugins.cam.engine.simulate import HeightField, Playback

FIX = Path(__file__).parent / "data" / "cam_fixtures"


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _play(job, cell=0.5):
    tp = compiler.compile_job(job).toolpath
    pb = Playback(job, tp, cell)
    pb.run_to_end()
    return pb


def test_a_rectangular_pocket_removes_its_volume():
    """50 × 30 × 5 mm with a 6 mm cutter: the corners keep the cutter's
    radius, so the area is the rectangle less four corner fillets."""
    job = io.job_from_dict(json.loads((FIX / "pocket_rect" / "job.json").read_text()))
    pb = _play(job, 0.5)
    r = 3.0
    area = 50 * 30 - 4 * r * r * (1 - math.pi / 4)
    assert pb.field.removed_volume == pytest.approx(area * 5, rel=0.005)
    assert pb.field.heights.min() == pytest.approx(15.0)


def test_a_through_profile_reaches_the_bottom_and_tabs_stand():
    t = Tool(diameter=6.0, fluteLength=25)
    job = Job(stock=Stock(width=100, depth=80, height=12), tools=[t], operations=[
        Operation("C", "outsideProfile", t.id, parameters=ProfileParameters(depth=12, stepDown=4),
                  strategy=Strategy(geometry=Region(rect(20, 20, 80, 60)),
                                    tabs=[Tab(0.25, 8, 3)]))])
    pb = _play(job)
    f = pb.field
    j, i = np.argmin(abs(f.x - 50)), np.argmin(abs(f.y - 17))     # mid bottom edge, in the cut
    assert f.heights[i, j] == pytest.approx(0.0) or f.heights[i, j] == pytest.approx(9.0)
    assert f.heights.min() == pytest.approx(0.0)
    # A tab's height counts down from the stock top: material stands at 12 - 3.
    assert np.isclose(f.heights, 9.0).any(), "the tab left no material at its height"
    assert f.heights[np.argmin(abs(f.y - 40)), np.argmin(abs(f.x - 50))] == pytest.approx(12.0)


def test_a_ball_end_mill_leaves_a_round_groove():
    t = Tool(kind="ballEndMill", diameter=6.0, fluteLength=20)
    job = Job(stock=Stock(width=60, depth=40, height=10), tools=[t], operations=[
        Operation("G", "engraving", t.id, parameters=EngravingParameters(
            points=[(10, 20, 0), (50, 20, 0)], depth=3, stepDown=3))])
    pb = _play(job, 0.25)
    f = pb.field
    row = f.heights[np.argmin(abs(f.y - 20))]
    col = np.argmin(abs(f.x - 30))
    assert row[col] == pytest.approx(7.0, abs=0.05)                    # tip depth
    k = np.argmin(abs(f.y - 22))                                     # ~2 mm off-axis
    d = abs(f.y[k] - 20)
    assert f.heights[k, col] == pytest.approx(7.0 + 3 - math.sqrt(9 - d * d), abs=0.02)


def test_scrubbing_gives_the_same_stock_as_playing_through():
    job = io.job_from_dict(json.loads((FIX / "job_multi_operation" / "job.json").read_text()))
    tp = compiler.compile_job(job).toolpath
    a = Playback(job, tp, 1.0)
    a.run_to_end()
    b = Playback(job, tp, 1.0)
    for f in (0.3, 0.1, 0.8, 0.5, 0.95, 1.0):
        b.seek(b.total * f)
    assert np.array_equal(a.field.heights, b.field.heights)
    b.seek(b.total * 0.4)
    c = Playback(job, tp, 1.0)
    c.seek(c.total * 0.4)
    assert np.array_equal(b.field.heights, c.field.heights)
    assert 0 < c.field.removed_volume < a.field.removed_volume


def test_the_tool_position_follows_time():
    job = io.job_from_dict(json.loads((FIX / "drill_input_order" / "job.json").read_text()))
    tp = compiler.compile_job(job).toolpath
    pb = Playback(job, tp, 1.0)
    pb.seek(pb.total)
    tip, tool = pb.tool_position()
    assert tool is not None and tool.kind == "drill"
    assert tip[2] == pytest.approx(30.0)                   # back at safe height


def test_a_huge_sheet_is_simulated_at_a_coarser_grid():
    from plugins.cam.ui.simview import MAX_CELLS
    st = Stock(width=2440, depth=1220, height=18)
    cell = max(0.5, math.sqrt(st.width * st.depth / MAX_CELLS))
    f = HeightField(st, cell)
    assert f.rows * f.cols <= MAX_CELLS * 1.01


def test_the_simulation_window_drives_the_viewport_playhead(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    import PySide6.QtCore as qc
    import views.main_window as mw
    factory = lambda *a: QSettings(str(tmp_path / "p.ini"), QSettings.IniFormat)  # noqa: E731
    monkeypatch.setattr(qc, "QSettings", factory)
    monkeypatch.setattr(mw, "QSettings", factory)
    from tests.test_cam_plugin import _close, _draw_board, _job, _set_stock, _wait
    win, dock, _model = _job(None, tmp_path)
    _set_stock(dock, 320.0, 220.0, 18.0)
    _draw_board(win, dock)
    dock.choose_paths(set(range(len(dock.paths))))
    dock._on_add("pocket")
    dock.calculate()
    _wait(dock)
    assert dock.btn_sim.isEnabled()
    sim = dock.simulate()
    sim.seek(sim.pb.total * 0.5)
    assert dock.overlay.play_index is not None and dock.overlay.play_tool is not None
    assert "cm³" in sim.info.text()
    sim.close()
    assert dock.overlay.play_index is None
    _close(win, dock)
