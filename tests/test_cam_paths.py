# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The CAM plugin reads the drawing as paths (``plugins/cam/paths.py``).

Reported 2026-09-26: «a rectangle in draw is not four lines in the CAM
tab, it is a single path». These pin that reading: a drawn rectangle, a
pulled-up block, four loose lines, a hole in a face and an open polyline
each become the one path a machinist would name, and any edge of it picks
the whole path.
"""
from __future__ import annotations

import math
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QVector3D as V

from core.scene import Scene
from plugins.cam import build
from plugins.cam.engine import geometry as geo
from plugins.cam.paths import extraction, find_paths, paths_for_edges, plan_frame
from plugins.cam.state import CamState


def _rect(w=0.1, d=0.06, z=0.0, x=0.0, y=0.0):
    return [V(x, y, z), V(x + w, y, z), V(x + w, y + d, z), V(x, y + d, z)]


def _circle(cx, cy, r, z=0.0, n=24):
    return [V(cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n), z)
            for k in range(n)]


def _paths(scene):
    frame = plan_frame(scene)
    return frame, find_paths(scene, frame)


def test_a_drawn_rectangle_is_one_closed_path():
    scene = Scene()
    scene.mesh.add_face(_rect())
    _frame, paths = _paths(scene)
    assert len(paths) == 1
    p = paths[0]
    assert p.closed and len(p.points) == 4 and len(p.edges) == 4
    assert p.extent == pytest.approx((100.0, 60.0))


def test_a_pulled_up_rectangle_is_one_path_on_its_top():
    scene = Scene()
    m = scene.mesh
    b, t = _rect(), _rect(z=0.02)
    m.add_face(list(reversed(b)))
    m.add_face(t)
    for i in range(4):
        j = (i + 1) % 4
        m.add_face([b[i], b[j], t[j], t[i]])
    frame, paths = _paths(scene)
    assert len(paths) == 1
    top = paths[0]
    assert top.w == pytest.approx(0.0, abs=1e-6)       # the frame sits on the top
    assert len(top.edges) == 8                          # top rim and bottom rim
    ex = extraction(paths, frame)
    assert ex.thickness == pytest.approx(20.0, abs=1e-6)


def test_four_loose_lines_chain_into_one_closed_path():
    scene = Scene()
    r = _rect()
    for i in range(4):
        scene.mesh.add_edge(r[i], r[(i + 1) % 4])
    _frame, paths = _paths(scene)
    assert len(paths) == 1 and paths[0].closed and len(paths[0].edges) == 4


def test_an_open_polyline_is_one_open_path():
    scene = Scene()
    pts = [V(0, 0, 0), V(0.05, 0, 0), V(0.05, 0.03, 0), V(0.08, 0.03, 0)]
    for a, b in zip(pts, pts[1:]):
        scene.mesh.add_edge(a, b)
    _frame, paths = _paths(scene)
    assert len(paths) == 1
    assert not paths[0].closed and paths[0].length == pytest.approx(110.0)


def test_any_edge_picks_its_whole_path():
    scene = Scene()
    scene.mesh.add_face(_rect())
    scene.mesh.add_face(_rect(0.02, 0.02, x=0.2))
    _frame, paths = _paths(scene)
    edge = next(e for e in scene.mesh.edges if e.v0.position.x() > 0.15)
    assert paths_for_edges(paths, {id(edge)}) == [1]    # the small square, sorted second


def test_a_hole_is_its_own_path_and_nests_as_the_regions_hole():
    scene = Scene()
    scene.mesh.add_face(_rect(), [_circle(0.05, 0.03, 0.01)])
    frame, paths = _paths(scene)
    assert len(paths) == 2
    circle = next(p for p in paths if p.circle)
    assert circle.circle[2] == pytest.approx(20.0, rel=0.01)
    ex = extraction(paths, frame)
    assert len(ex.regions) == 1 and len(ex.regions[0][1]) == 1


def test_standing_faces_make_no_paths():
    scene = Scene()
    scene.mesh.add_face([V(0, 0, 0), V(0.1, 0, 0), V(0.1, 0, 0.05), V(0, 0, 0.05)])
    frame = plan_frame(scene)
    assert frame is None or find_paths(scene, frame) == []


def test_operations_on_a_chosen_path():
    """The builders take a path like any selection: an outside profile
    follows the rectangle, a pocket clears inside it with the circle as an
    island."""
    scene = Scene()
    scene.mesh.add_face(_rect(), [_circle(0.05, 0.03, 0.01)])
    frame, paths = _paths(scene)
    state = CamState.new()
    ops = build.add_operations(state, "outsideProfile", extraction(paths[:1], frame))
    assert len(ops) == 1
    assert abs(geo.signed_area(ops[0].strategy.geometry.boundary)) == pytest.approx(6000.0)
    ops = build.add_operations(state, "pocket", extraction(paths, frame))
    assert len(ops[0].strategy.geometry.islands) == 1
