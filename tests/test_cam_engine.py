# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The CAM engine's own rules — the places the port goes beyond 2DCam or
pins something the parity corpus does not reach.

Geometry conventions, the physical climb/conventional rule, helix entries
that never cross the wall, pockets whose islands merge with the walls,
inside profiles that split, tabs, peck drilling, the verifier's findings,
the job JSON round trip (and a 2DCam inch project read in millimetres),
and the engine staying free of Qt and translations.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import pytest

from plugins.cam.engine import compiler, geometry as geo, io, verify
from plugins.cam.engine.issues import CODES, CamError
from plugins.cam.engine.models import (DrillingParameters, EngravingParameters,
                                       FacingParameters, Job, MachineSetup, Operation,
                                       PocketParameters, ProfileParameters, Region, Stock,
                                       Strategy, Tab, Tool)
from plugins.cam.engine.ops.common import cut_direction_ccw
from plugins.cam.engine.toolpath import (Arc, DrillCycle, Linear, Rapid, RetractZ, Toolpath,
                                         expand_drill, motion_points)

ROOT = Path(__file__).resolve().parents[1]


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def job_with(*ops, stock=None, tools=None):
    t = Tool(number=1, diameter=6.0)
    job = Job(stock=stock or Stock(width=100, depth=75, height=20), tools=tools or [t])
    for op in ops:
        if op.toolID is None:
            op.toolID = job.tools[0].id
        job.operations.append(op)
    return job


def cut_loops(tp, z):
    """XY polylines of the horizontal cuts at ``z``, split at every rapid."""
    loops, cur_loop = [], []
    for kind, a, b, _i in motion_points(tp.commands):
        if kind == "cut" and abs(a[2] - z) < 1e-6 and abs(b[2] - z) < 1e-6:
            if not cur_loop:
                cur_loop = [a[:2]]
            cur_loop.append(b[:2])
        elif cur_loop:
            loops.append(cur_loop)
            cur_loop = []
    if cur_loop:
        loops.append(cur_loop)
    return loops


# ---- geometry -------------------------------------------------------------

def test_offset_grows_for_positive_delta_whatever_the_orientation():
    for loop in (rect(0, 0, 10, 10), rect(0, 0, 10, 10)[::-1]):
        (out,) = geo.offset_loop(loop, 1.0, "miter")
        assert geo.signed_area(out) == pytest.approx(144.0, abs=1e-3)
        (inn,) = geo.offset_loop(loop, -1.0, "miter")
        assert geo.signed_area(inn) == pytest.approx(64.0, abs=1e-3)


def test_offset_vanishes_when_the_region_is_too_narrow():
    assert geo.offset_loop(rect(0, 0, 5, 5), -3.0) == []


def test_offset_with_islands_merges_an_island_that_reaches_the_wall():
    pieces = geo.offset(rect(0, 0, 50, 30), [rect(20, 2, 30, 28)], -3.0, "round")
    # The island (grown by 3) touches both long walls (shrunk by 3): two pockets.
    assert len(pieces) == 2 and all(not holes for _loop, holes in pieces)


def test_remove_short_seams_heals_a_near_duplicate_closing_point():
    loop = [(0, 0), (80, 0), (80, 50), (0, 50), (0.22, 50.05)]
    cleaned = geo.remove_short_seams(loop, 0.4)
    assert len(cleaned) == 4


def test_contains_counts_the_edge_as_inside():
    sq = rect(0, 0, 10, 10)
    assert geo.contains((5, 5), sq) and geo.contains((10, 5), sq)
    assert not geo.contains((11, 5), sq)


# ---- direction ------------------------------------------------------------

def test_climb_is_clockwise_around_a_part_and_counterclockwise_in_a_hole():
    assert cut_direction_ccw(material_inside=True, direction="climb") is False
    assert cut_direction_ccw(material_inside=False, direction="climb") is True
    assert cut_direction_ccw(material_inside=True, direction="conventional") is True


@pytest.mark.parametrize("kind, climb_area_sign", [("outsideProfile", -1), ("insideProfile", 1)])
def test_profile_direction_follows_the_physical_rule(kind, climb_area_sign):
    for direction, sign in (("climb", climb_area_sign), ("conventional", -climb_area_sign)):
        op = Operation("P", kind, parameters=ProfileParameters(depth=2, stepDown=2),
                       strategy=Strategy(geometry=Region(rect(20, 20, 80, 55)),
                                         direction=direction))
        tp = compiler.compile_job(job_with(op)).toolpath
        (loop,) = cut_loops(tp, 18.0)
        assert math.copysign(1, geo.signed_area(loop[:-1])) == sign, (kind, direction)


# ---- helix entries never cross the wall ------------------------------------

@pytest.mark.parametrize("kind", ["outsideProfile", "insideProfile", "pocket"])
def test_helix_entries_do_not_gouge(kind):
    region = Region([(10, 10), (90, 10), (90, 35), (45, 35), (45, 65), (10, 65)])
    params = (PocketParameters(depth=5, stepDown=2.5) if kind == "pocket"
              else ProfileParameters(depth=5, stepDown=2.5))
    op = Operation("H", kind, parameters=params,
                   strategy=Strategy(geometry=region, entry="helix"))
    job = job_with(op)
    res = compiler.compile_job(job)
    assert any(isinstance(c, Arc) for c in res.toolpath.commands)
    assert verify.check_gouges(job, res) == []


def test_helix_keeps_within_ten_degrees():
    op = Operation("H", "outsideProfile", parameters=ProfileParameters(depth=6, stepDown=3),
                   strategy=Strategy(geometry=Region(rect(20, 20, 80, 55)), entry="helix"))
    tp = compiler.compile_job(job_with(op)).toolpath
    cur = None
    for c in tp.commands:
        if isinstance(c, Arc):
            r = math.hypot(*c.center[:2])
            drop = cur[2] - c.to[2]
            assert math.degrees(math.atan2(drop, 2 * math.pi * r)) <= 10.0 + 1e-6
        if hasattr(c, "to"):
            cur = c.to
        elif isinstance(c, RetractZ) and cur is not None:
            cur = (cur[0], cur[1], c.z)


# ---- pockets and profiles --------------------------------------------------

def test_a_pocket_too_small_for_the_tool_is_refused():
    op = Operation("P", "pocket", parameters=PocketParameters(depth=3, stepDown=1.5),
                   strategy=Strategy(geometry=Region(rect(40, 30, 45, 35))))
    with pytest.raises(CamError) as exc:
        compiler.compile_job(job_with(op))
    assert exc.value.code == "region_too_small_for_tool"
    assert exc.value.issue.operation_name == "P"


def test_pocket_island_merged_with_the_wall_still_cuts_both_sides():
    op = Operation("P", "pocket", parameters=PocketParameters(depth=2, stepDown=2),
                   strategy=Strategy(geometry=Region(rect(10, 10, 90, 40),
                                                     [rect(45, 12, 55, 38)])))
    job = job_with(op)
    res = compiler.compile_job(job)
    xs = [c.to[0] for c in res.toolpath.commands if isinstance(c, Linear)]
    assert min(xs) < 45 and max(xs) > 55
    assert verify.check_gouges(job, res) == []


def test_inside_profile_that_splits_cuts_every_piece():
    dumbbell = [(0, 0), (20, 0), (20, 8), (30, 8), (30, 0), (50, 0), (50, 20),
                (30, 20), (30, 12), (20, 12), (20, 20), (0, 20)]
    op = Operation("I", "insideProfile", parameters=ProfileParameters(depth=2, stepDown=2),
                   strategy=Strategy(geometry=Region([(x + 20, y + 20) for x, y in dumbbell])))
    tp = compiler.compile_job(job_with(op)).toolpath
    assert len(cut_loops(tp, 18.0)) == 2


def test_tabs_rise_to_their_height_over_their_width():
    op = Operation("T", "outsideProfile", parameters=ProfileParameters(depth=6, stepDown=6),
                   strategy=Strategy(geometry=Region(rect(20, 20, 80, 50)),
                                     tabs=[Tab(0.25, 8, 2)]))
    tp = compiler.compile_job(job_with(op)).toolpath
    tab = [c for c in tp.commands if isinstance(c, Linear) and abs(c.to[2] - 18.0) < 1e-9]
    assert tab, "no move at the tab height"
    # The tab's top run is the tab width long.
    pts = [c.to for c in tp.commands if isinstance(c, Linear)]
    run = 0.0
    for a, b in zip(pts, pts[1:]):
        if abs(a[2] - 18) < 1e-9 and abs(b[2] - 18) < 1e-9:
            run += math.dist(a[:2], b[:2])
    assert run == pytest.approx(8.0, abs=1e-6)


def test_leads_start_mid_edge_and_stay_on_it():
    op = Operation("L", "insideProfile", parameters=ProfileParameters(depth=2, stepDown=2),
                   strategy=Strategy(geometry=Region(rect(20, 20, 80, 50)),
                                     leadInLength=5, leadOutLength=5))
    job = job_with(op)
    res = compiler.compile_job(job)
    assert verify.check_gouges(job, res) == []


# ---- drilling -------------------------------------------------------------

def test_peck_expansion_matches_linuxcnc_g83():
    c = DrillCycle(10, 10, r=25, bottom=10, feed=100, peck=6)
    moves = expand_drill(c)
    zs = [(type(m).__name__, m.to[2]) for m in moves if hasattr(m, "to")]
    assert zs == [("Rapid", 25), ("Linear", 19), ("Rapid", 25), ("Rapid", 19.254),
                  ("Linear", 13), ("Rapid", 25), ("Rapid", 13.254), ("Linear", 10),
                  ("Rapid", 25)]


def test_drill_without_peck_is_2dcams_sequence():
    moves = expand_drill(DrillCycle(1, 2, r=25, bottom=12, feed=100))
    assert [(type(m).__name__, m.to) for m in moves] == [
        ("Rapid", (1, 2, 25)), ("Linear", (1, 2, 12)), ("Rapid", (1, 2, 25))]


def test_drill_points_outside_the_stock_are_refused():
    op = Operation("D", "drilling", parameters=DrillingParameters(depth=5, points=[(150, 5, 0)]))
    with pytest.raises(CamError) as exc:
        compiler.compile_job(job_with(op))
    assert exc.value.code == "point_outside_stock"


# ---- compiler and verifier --------------------------------------------------

def test_every_operation_starts_by_lifting_before_it_travels():
    op = Operation("F", "facing", parameters=FacingParameters())
    tp = compiler.compile_job(job_with(op)).toolpath
    motions = [c for c in tp.commands if isinstance(c, (Rapid, RetractZ, Linear))]
    assert isinstance(motions[0], RetractZ) and motions[0].z == pytest.approx(30.0)


def test_machine_limits():
    op = Operation("F", "facing", parameters=FacingParameters())
    job = job_with(op)
    job.tools[0].spindleRPM = 30_000
    with pytest.raises(CamError) as exc:
        compiler.compile_job(job)
    assert exc.value.code == "spindle_above_machine"
    job.tools[0].spindleRPM = 5_000
    res = compiler.compile_job(job)
    assert [w.code for w in res.warnings] == ["spindle_below_machine"]


def test_flute_length_limits_depth():
    op = Operation("E", "engraving", parameters=EngravingParameters(
        points=[(10, 10, 0), (50, 50, 0)], depth=15, stepDown=5))
    job = job_with(op)
    job.tools[0].fluteLength = 12
    with pytest.raises(CamError) as exc:
        compiler.compile_job(job)
    assert exc.value.code == "axial_depth_exceeds_flute"


def test_verifier_findings():
    job = job_with(Operation("F", "facing", parameters=FacingParameters()))
    job.setup = MachineSetup(safeHeight=5, clearanceHeight=8)
    tp = Toolpath([Linear((0, 0, -30), 100), Rapid((50, 30, 10)), Rapid((10, 30, 10))])
    codes = {i.code for i in verify.verify(job, tp).issues}
    assert {"clearance_height_invalid", "cut_spindle_stopped", "cut_before_tool_change",
            "cut_below_stock_bottom", "rapid_inside_stock", "rapid_through_stock"} <= codes


def test_every_issue_code_the_engine_raises_is_declared():
    src = "\n".join(p.read_text() for p in (ROOT / "plugins/cam/engine").rglob("*.py"))
    used = set(re.findall(r'CamError\(\s*"([a-z_]+)"', src))
    used |= set(re.findall(r'(?:_issue|Issue)\(\s*"([a-z_]+)"', src))
    assert used <= set(CODES), used - set(CODES)


# ---- io --------------------------------------------------------------------

def test_job_json_round_trip():
    op = Operation("P", "pocket", parameters=PocketParameters(depth=4),
                   strategy=Strategy(geometry=Region(rect(10, 10, 50, 40), [rect(20, 20, 30, 30)]),
                                     tabs=[Tab(0.5, 3, 1)], entry="ramp"))
    drill = Operation("D", "drilling",
                      parameters=DrillingParameters(depth=5, points=[(20, 20, 0)], peckDepth=2))
    job = job_with(op, drill)
    job.units = "inches"
    job.post.controller = "linuxcnc"
    d = json.loads(json.dumps(io.job_to_dict(job)))
    back = io.job_from_dict(d)
    assert io.job_to_dict(back) == d
    assert back.units == "inches" and back.post.controller == "linuxcnc"
    assert back.operations[1].parameters.peckDepth == 2
    # 2DCam reads the same file as a millimetre project.
    assert d["units"] == "millimeters"


def test_a_2dcam_inch_project_is_read_in_millimetres():
    d = json.loads((ROOT / "tests/data/cam_fixtures/inch_pocket/job.json").read_text())
    job = io.job_from_dict(d)
    assert job.units == "inches"
    assert job.stock.width == pytest.approx(4 * 25.4)
    assert job.tools[0].diameter == pytest.approx(0.25 * 25.4)
    assert job.tools[0].cuttingFeed == pytest.approx(40 * 25.4)
    assert job.setup.safeHeight == pytest.approx(0.4 * 25.4)


# ---- isolation ---------------------------------------------------------------

def test_the_engine_imports_no_qt_and_no_translations():
    for path in (ROOT / "plugins/cam/engine").rglob("*.py"):
        text = path.read_text()
        for banned in ("PySide6", "core.i18n", "from core", "import core", "QtCore"):
            assert banned not in text, (path.name, banned)
