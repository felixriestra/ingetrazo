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
    from plugins.cam.engine.simulate import cell_size
    from plugins.cam.ui.simview import SimulationWindow
    job = Job(stock=Stock(width=2440, depth=1220, height=18),
              tools=[Tool(number=1, diameter=6.0)])
    for mode, cap in (("live", SimulationWindow.LIVE_CELLS),
                      ("final", SimulationWindow.FINAL_CELLS)):
        f = HeightField(job.stock, cell_size(job, mode, cap))
        assert f.rows * f.cols <= cap * 1.01


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
    # 2DCam's controls: the posted G-code beside the view, the running line
    # highlighted; one command at a time; a phase; the tool's coordinates.
    assert sim.code.count() > 20 and sim.code.item(0).text().strip().startswith("1")
    sim.seek(0.0)
    sim.step()
    first = sim.pb.command_index
    sim.step()
    assert sim.pb.command_index > first or sim.pb.time > 0
    row = sim.code.currentRow()
    assert sim.lines[row].split()[0][:1] in "GMTSF("
    assert sim.coords.text().startswith("X ")
    sim.seek(sim.pb.total * 0.5)
    assert sim.phase.text()
    assert "/" in sim.code_title.text()
    # Clicking a line runs the job up to it.
    target = sim.command_line[len(sim.command_line) // 2]
    sim._on_code_clicked(sim.code.item(target))
    assert sim.command_line[sim.pb.command_index] >= target
    # High quality, and the final run.
    assert "cells" in sim.cells.text()
    sim.run_final()
    assert sim.quality.currentData() == "final" and sim.pb.time == pytest.approx(sim.pb.total)
    # Any G-code file plays on the job's stock.
    nc = tmp_path / "other.nc"
    nc.write_text("G21 G90\nG0 Z5\nG0 X50 Y50\nG1 Z-2 F300\nG1 X120 Y60 F800\nG0 Z5\nM30\n")
    assert sim.open_gcode(nc)
    assert sim.btn_job.isVisible() or not sim.isVisible()
    sim.seek(sim.pb.total)
    assert sim.pb.field.removed_volume > 0
    assert "other.nc" in sim.info.text()
    sim.show_job_program()
    assert sim.source_name is None
    sim.close()
    assert dock.overlay.play_index is None
    _close(win, dock)


# ---- 2DCam's simulation logic around the height field --------------------

def _fixture_job(case, controller="grbl"):
    job = io.job_from_dict(json.loads((FIX / case / "job.json").read_text()))
    job.post.controller = controller
    return job


@pytest.mark.parametrize("controller", ["grbl", "linuxcnc"])
def test_every_command_knows_its_gcode_line(controller):
    """The G-code panel follows playback: each toolpath command maps to
    the line it ends on, across GRBL's per-tool files too."""
    from plugins.cam.engine.post import listing, post_job
    from plugins.cam.engine.toolpath import Linear, Rapid
    job = _fixture_job("job_multi_operation", controller)
    tp = compiler.compile_job(job).toolpath
    res = post_job(job, tp)
    lines, command_line = listing(res, "job")
    assert len(command_line) == len(tp.commands)
    assert command_line == sorted(command_line)               # in program order
    for i, (c, k) in enumerate(zip(tp.commands, command_line)):
        if i and k == command_line[i - 1]:
            continue                      # wrote nothing (a move to where it stood)
        if isinstance(c, Linear):
            assert lines[k].startswith(("G1", "G2", "G3")), (c, lines[k])
        elif isinstance(c, Rapid):
            assert lines[k].startswith("G0") or lines[k].startswith("G1"), (c, lines[k])
    if controller == "grbl":
        assert sum(ln.startswith("(=== job_") for ln in lines) == len(res.files)


def test_2dcams_cell_sizes():
    """Live: a twentieth of the smallest tool, 0.1–1 mm, 1 M cells at most;
    final: a fortieth, from 0.05 mm, 6 M cells at most."""
    from plugins.cam.engine.simulate import cell_size
    job = _fixture_job("job_multi_operation")
    dmin = min(t.diameter for t in job.tools)
    area = job.stock.width * job.stock.depth
    live = cell_size(job, "live")
    assert live == pytest.approx(max(0.1, min(1.0, dmin * 0.05), math.sqrt(area / 1e6)))
    final = cell_size(job, "final")
    assert final == pytest.approx(max(0.05, dmin * 0.025, math.sqrt(area / 6e6)))
    job.stock.width, job.stock.depth = 2440.0, 1220.0          # a full sheet
    assert cell_size(job, "live") == pytest.approx(math.sqrt(2440 * 1220 / 1e6))
    assert cell_size(job, "live", max_cells=250_000) == pytest.approx(
        math.sqrt(2440 * 1220 / 250_000))


def test_step_forward_and_the_phase():
    job = _fixture_job("job_multi_operation")
    tp = compiler.compile_job(job).toolpath
    pb = Playback(job, tp, 1.0)
    seen = set()
    last = -1.0
    for _ in range(len(tp.commands) + 5):
        pb.step()
        assert pb.time >= last
        last = pb.time
        seen.add(pb.phase)
    assert pb.time == pytest.approx(pb.total)
    assert {"rapid"} <= seen and seen & {"roughing", "finishing", "cutting"}


@pytest.mark.parametrize("controller", ["grbl", "linuxcnc"])
def test_a_gcode_file_simulates_like_its_toolpath(controller):
    """2DCam also plays back G-code from elsewhere. Our own posted program,
    read back, must remove the same stock as the toolpath it came from."""
    from plugins.cam.engine.post import post_job
    from plugins.cam.engine.simulate import toolpath_from_gcode
    job = _fixture_job("pocket_island", controller)
    tp = compiler.compile_job(job).toolpath
    direct = Playback(job, tp, 1.0)
    direct.run_to_end()
    text = post_job(job, tp).files[0].text
    imported, lines = toolpath_from_gcode(text, job, controller)
    assert len(lines) == len(imported.commands)
    via_gcode = Playback(job, imported, 1.0)
    via_gcode.run_to_end()
    assert via_gcode.field.removed_volume == pytest.approx(direct.field.removed_volume,
                                                           rel=0.01)
    assert text.splitlines()[lines[-1]].startswith(("G0", "G1", "G2", "G3"))
