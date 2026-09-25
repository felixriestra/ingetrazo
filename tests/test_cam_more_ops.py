# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Bore, slot, chamfer and open pocket (2DCam's v1.x operations, ported).

Checked where it matters, on the simulated stock: a bore is a clean hole
to its bottom (2DCam's leaves a core when it is wider than twice the
tool), a slot is its stadium, a chamfer is on the right side of its edge
at the right width, and an open pocket cuts right through its open edges
while it keeps its closed walls.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from plugins.cam.engine import compiler, io, verify
from plugins.cam.engine.issues import CamError
from plugins.cam.engine.models import (BoreParameters, ChamferParameters, Job, Operation,
                                       OpenPocketParameters, Region, SlotParameters, Stock,
                                       Strategy, Tool)
from plugins.cam.engine.simulate import Playback


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


MILL = Tool(number=1, diameter=6.0, fluteLength=25)
VBIT = Tool(number=5, name="V", kind="chamferMill", diameter=12.0, fluteLength=10,
            includedAngle=90.0, tipDiameter=0.0)


def _job(op, stock=None):
    job = Job(stock=stock or Stock(width=100, depth=75, height=20), tools=[MILL, VBIT],
              operations=[op])
    return job


def _run(job, cell=0.5):
    res = compiler.compile_job(job)
    pb = Playback(job, res.toolpath, cell)
    pb.run_to_end()
    return res, pb.field


def _h(field, x, y):
    return field.heights[np.argmin(abs(field.y - y)), np.argmin(abs(field.x - x))]


def test_a_wide_bore_is_a_clean_hole_with_no_core():
    op = Operation("B", "bore", MILL.id, parameters=BoreParameters(center=(50, 37.5, 0),
                                                                   diameter=30, depth=4,
                                                                   stepDown=2))
    job = _job(op)
    res, f = _run(job)
    for x, y in ((50, 37.5), (55, 37.5), (50, 44), (62, 37.5)):     # centre … near the wall
        assert _h(f, x, y) == pytest.approx(16.0), (x, y)
    assert _h(f, 50 + 16, 37.5) == pytest.approx(20.0)               # beyond the wall
    assert not verify.check_gouges(job, res)


def test_a_bore_no_wider_than_twice_the_tool_is_2dcams_single_helix():
    op = Operation("B", "bore", MILL.id, parameters=BoreParameters(center=(30, 30, 0),
                                                                   diameter=10, depth=5,
                                                                   stepDown=2.5))
    tp = compiler.compile_job(_job(op)).toolpath
    from plugins.cam.engine.toolpath import Arc, Linear
    cuts = [c for c in tp.commands if isinstance(c, (Arc, Linear))]
    assert all(isinstance(c, Arc) for c in cuts[1:])             # plunge, then only arcs


def test_bore_errors():
    too_small = Operation("B", "bore", MILL.id, parameters=BoreParameters(diameter=5))
    with pytest.raises(CamError) as e:
        compiler.compile_job(_job(too_small))
    assert e.value.code == "bore_smaller_than_tool"
    outside = Operation("B", "bore", MILL.id, parameters=BoreParameters(center=(5, 5, 0),
                                                                        diameter=20))
    with pytest.raises(CamError) as e:
        compiler.compile_job(_job(outside))
    assert e.value.code == "bore_outside_stock"


def test_a_slot_is_a_rounded_rectangle_a_radius_past_its_ends():
    op = Operation("S", "slot", MILL.id, parameters=SlotParameters(
        start=(20, 37.5, 0), end=(80, 37.5, 0), width=14, depth=6, stepDown=3,
        stepoverFraction=0.5))
    job = _job(op)
    res, f = _run(job)
    assert _h(f, 50, 37.5) == pytest.approx(14.0)
    assert _h(f, 50, 37.5 + 6.5) == pytest.approx(14.0)           # inside the width
    assert _h(f, 50, 37.5 + 7.5) == pytest.approx(20.0)           # outside it
    assert _h(f, 20 - 2.5, 37.5) == pytest.approx(14.0)           # a radius past start
    assert _h(f, 20 - 3.5, 37.5) == pytest.approx(20.0)           # not further
    assert not verify.check_gouges(job, res)
    narrow = Operation("S", "slot", MILL.id, parameters=SlotParameters(width=4))
    with pytest.raises(CamError) as e:
        compiler.compile_job(_job(narrow))
    assert e.value.code == "slot_narrower_than_tool"


@pytest.mark.parametrize("inside", [False, True])
def test_a_chamfer_is_on_the_waste_side_at_its_width(inside):
    """1 mm × 45° on the edge of a 40 × 30 rectangle: at the stock top the
    V-bit takes 1 mm off the part side of the edge, nothing further."""
    op = Operation("C", "chamfer", VBIT.id, parameters=ChamferParameters(
        width=1.0, depth=1.5, inside=inside),
        strategy=Strategy(geometry=Region(rect(30, 20, 70, 50))))
    _res, f = _run(_job(op), cell=0.1)
    edge_y = 20.0
    part_side = +1 if not inside else -1      # the part is inside the rectangle unless a hole
    at = lambda dy: _h(f, 50, edge_y + dy)    # noqa: E731
    assert at(part_side * 0.5) == pytest.approx(19.5, abs=0.06)   # halfway across the chamfer
    assert at(part_side * 1.2) == pytest.approx(20.0)             # past it: untouched
    assert at(-part_side * 0.3) < 19.0                             # the waste side is cut


def test_a_chamfer_needs_a_chamfer_mill():
    op = Operation("C", "chamfer", MILL.id, parameters=ChamferParameters(),
                   strategy=Strategy(geometry=Region(rect(30, 20, 70, 50))))
    with pytest.raises(CamError) as e:
        compiler.compile_job(_job(op))
    assert e.value.code == "chamfer_needs_chamfer_mill"


def test_an_open_pocket_cuts_through_open_edges_and_keeps_closed_walls():
    """A rebate along the left of the board: the region 0..30 × 0..75
    opens onto the board's left, front and back edges; its right edge
    (x = 30) is a wall."""
    region = Region(rect(0, 0, 30, 75), openEdgeIndices=[0, 2, 3])
    op = Operation("R", "openPocket", MILL.id,
                   parameters=OpenPocketParameters(depth=5, stepDown=2.5),
                   strategy=Strategy(geometry=region))
    job = _job(op)
    res, f = _run(job)
    assert _h(f, 0.3, 37.5) == pytest.approx(15.0)                # right at the open edge
    assert _h(f, 15, 0.3) == pytest.approx(15.0)                  # front edge, open
    assert _h(f, 29.7, 37.5) == pytest.approx(15.0)               # up to the wall …
    assert _h(f, 30.4, 37.5) == pytest.approx(20.0)               # … and not past it
    assert not verify.check_gouges(job, res)


def test_open_pocket_with_no_open_edge_is_a_closed_pocket():
    region = Region(rect(20, 20, 60, 50))
    a = Operation("A", "openPocket", MILL.id, parameters=OpenPocketParameters(depth=4),
                  strategy=Strategy(geometry=region))
    b = Operation("B", "pocket", MILL.id,
                  parameters=__import__("plugins.cam.engine.models",
                                        fromlist=["PocketParameters"]).PocketParameters(depth=4),
                  strategy=Strategy(geometry=region))
    ta = compiler.compile_job(_job(a)).toolpath.commands
    tb = compiler.compile_job(_job(b)).toolpath.commands
    from plugins.cam.engine.toolpath import Linear
    la = [c.to for c in ta if isinstance(c, Linear)]
    lb = [c.to for c in tb if isinstance(c, Linear)]
    assert la == lb


def test_the_new_operations_round_trip_through_json():
    ops = [Operation("B", "bore", MILL.id, parameters=BoreParameters(center=(40, 30, 0))),
           Operation("S", "slot", MILL.id, parameters=SlotParameters(start=(10, 10, 0))),
           Operation("C", "chamfer", VBIT.id, parameters=ChamferParameters(
               points=[(1, 2), (3, 4)], isClosed=False, inside=True)),
           Operation("O", "openPocket", MILL.id, parameters=OpenPocketParameters(inset=2),
                     strategy=Strategy(geometry=Region(rect(0, 0, 10, 10),
                                                       openEdgeIndices=[1, 3])))]
    job = Job(tools=[MILL, VBIT], operations=ops)
    back = io.job_from_dict(io.job_to_dict(job))
    assert back.operations[0].parameters.center == (40, 30, 0)
    assert back.operations[1].parameters.start == (10, 10, 0)
    assert back.operations[2].parameters.points == [(1, 2), (3, 4)]
    assert back.operations[2].parameters.inside is True
    assert back.operations[3].strategy.geometry.openEdgeIndices == [1, 3]
    assert io.job_to_dict(back) == io.job_to_dict(job)


# ---- from selection to operations (build.py) ---------------------------------

def _state_with_part():
    from plugins.cam.state import CamState
    s = CamState.new()
    s.frame = __import__("plugins.cam.state", fromlist=["Frame"]).Frame()
    s.include_bounds([(0, 0), (300, 200)])            # the board's outline, as a part sets it
    s.job.stock.height = 18
    return s


def _ex(regions=(), loops=(), paths=(), thickness=18.0):
    from plugins.cam.extract import Extraction
    from plugins.cam.state import Frame
    ex = Extraction(Frame(), kind="faces", thickness=thickness)
    ex.regions = list(regions)
    ex.loops = list(loops)
    ex.paths = list(paths)
    return ex


def _loop(points, w=0.0, **kw):
    from plugins.cam.extract import Loop, circle_of
    lp = Loop([tuple(p) for p in points], w)
    lp.circle = circle_of(lp.points)
    for k, v in kw.items():
        setattr(lp, k, v)
    return lp


def _circle(cx, cy, r, n=48):
    return [(cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n))
            for k in range(n)]


def test_round_holes_become_bores_when_no_drill_fits():
    from plugins.cam import build
    s = _state_with_part()
    outer = _loop(rect(0, 0, 300, 200))
    hole = _loop(_circle(100, 100, 6), through=True)                 # Ø12, no Ø12 drill
    blind = _loop(_circle(200, 100, 10), through=False, depth=8.0)   # Ø20 counterbore
    ex = _ex(regions=[(outer, [hole, blind])])
    ex.kind = "part"
    ops = build.suggest_operations(s, ex)
    bores = [o for o in ops if o.kind == "bore"]
    assert sorted(round(o.parameters.diameter) for o in bores) == [12, 20]
    assert {round(o.parameters.depth, 3) for o in bores} == {18.0, 8.0}
    assert not [o for o in ops if o.kind == "insideProfile"]


def test_a_rectangle_becomes_a_slot_that_cuts_exactly_it():
    from plugins.cam import build
    s = _state_with_part()
    (op,) = build.add_operations(s, "slot", _ex(regions=[(_loop(rect(50, 40, 110, 54)), [])]))
    p = op.parameters
    assert p.width == pytest.approx(14)
    tool = s.job.tool(op.toolID)
    r = tool.diameter / 2
    assert (p.start[0], p.end[0]) == pytest.approx((50 + r, 110 - r))
    assert p.start[1] == pytest.approx(47) and p.end[1] == pytest.approx(47)


def test_chamfers_go_outside_the_outline_and_inside_the_holes():
    from plugins.cam import build
    s = _state_with_part()
    ex = _ex(regions=[(_loop(rect(0, 0, 300, 200)), [_loop(rect(100, 80, 140, 120))])])
    ops = build.add_operations(s, "chamfer", ex)
    assert [o.parameters.inside for o in ops] == [False, True]
    vbit = s.job.tool(ops[0].toolID)
    assert vbit.kind == "chamferMill"
    assert ops[0].parameters.depth == pytest.approx(1.0)   # 1 mm wide at 90°


def test_an_open_pocket_opens_where_it_meets_the_board_edge():
    from plugins.cam import build
    s = _state_with_part()
    rebate = _loop(rect(0, 0, 30, 200), w=-6.0)            # along the left edge, 6 deep
    (op,) = build.add_operations(s, "openPocket", _ex(regions=[(rebate, [])]))
    assert op.strategy.geometry.openEdgeIndices == [0, 2, 3]   # bottom, top, left
    assert op.parameters.depth == pytest.approx(6.0)
