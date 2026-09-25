# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Right-click ▸ Select (issue #106, @pacaeiro): «Right now we lack options
to select by connected, by layer, by material». SketchUp's submenu, answered
from the current editing context."""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from core.layers import assign_layer
from core.mesh import Edge, Face
from core.scene import Scene
from core.select_ops import (all_connected, bounding_edges, same_layer,
                             same_material)


def V(x, y, z=0.0):
    return QVector3D(float(x), float(y), float(z))


def _two_squares_and_one_apart(scene):
    """Two squares sharing an edge (one connected piece) and a third square
    far away (another piece)."""
    a = scene.mesh.add_face([V(0, 0), V(1, 0), V(1, 1), V(0, 1)])
    b = scene.mesh.add_face([V(1, 0), V(2, 0), V(2, 1), V(1, 1)])
    c = scene.mesh.add_face([V(5, 0), V(6, 0), V(6, 1), V(5, 1)])
    return a, b, c


def test_all_connected_takes_the_piece_and_leaves_the_other():
    scene = Scene()
    a, b, c = _two_squares_and_one_apart(scene)
    got = set(all_connected([a]))
    assert a in got and b in got and c not in got
    assert sum(isinstance(e, Edge) for e in got) == 7   # 4 + 4 − 1 shared


def test_bounding_edges_outline_the_selection_not_its_inside():
    scene = Scene()
    a, b, _c = _two_squares_and_one_apart(scene)
    edges = bounding_edges([a, b])
    assert len(edges) == 6                  # the shared edge is inside
    shared = [e for e in edges if a in e.faces and b in e.faces]
    assert shared == []
    assert len(bounding_edges([a])) == 4


def test_same_material_matches_named_and_plain_paint():
    scene = Scene()
    a, b, c = _two_squares_and_one_apart(scene)
    a.attrs["color"] = (0.8, 0.2, 0.2)
    c.attrs["color"] = (0.8, 0.2, 0.2)
    b.attrs["color"] = (0.1, 0.1, 0.9)
    assert set(same_material(scene, [a])) == {a, c}
    # a named material wins over a colour that happens to match
    a.attrs["mat"] = "Ladrillo"
    assert set(same_material(scene, [a])) == {a}
    # unpainted is a material too: it finds the other unpainted faces
    d = scene.mesh.add_face([V(8, 0), V(9, 0), V(9, 1), V(8, 1)])
    e = scene.mesh.add_face([V(10, 0), V(11, 0), V(11, 1), V(10, 1)])
    assert set(same_material(scene, [d])) == {d, e}


def test_same_layer_takes_faces_edges_and_groups_on_it():
    scene = Scene()
    a, b, c = _two_squares_and_one_apart(scene)
    assign_layer(a, "Muros")
    assign_layer(c, "Muros")
    got = set(same_layer(scene, [a]))
    assert {a, c} <= got and b not in got
    assert all(isinstance(e, Face) for e in got)   # edges stay on the default


def test_the_triple_click_still_selects_the_whole_piece():
    from PySide6.QtCore import QPointF, Qt
    from tools.base import ToolContext
    from tools.select import SelectTool
    scene = Scene()
    a, b, c = _two_squares_and_one_apart(scene)

    class _Vp:
        def __init__(self):
            self.scene = scene

        def update(self):
            pass

    vp = _Vp()
    tool = SelectTool()
    tool._pick = lambda viewport, x, y: a
    tool.on_triple_click(ToolContext(viewport=vp, world=V(0, 0),
                                     screen=QPointF(0, 0),
                                     modifiers=Qt.NoModifier, snap=None))
    assert a in scene.selection and b in scene.selection
    assert c not in scene.selection


def test_the_right_click_menu_offers_select_and_it_grows_the_selection():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QMenu
    from views.main_window import MainWindow
    win = MainWindow()
    try:
        scene = win.viewport.scene
        a, b, c = _two_squares_and_one_apart(scene)
        scene.select([a])
        menu = QMenu()
        win._add_select_submenu(menu, list(scene.selection))
        sub = menu.actions()[0].menu()
        acts = {act.text(): act for act in sub.actions()}
        assert len(acts) == 4
        sub.actions()[0].trigger()                      # All Connected
        assert a in scene.selection and b in scene.selection
        assert c not in scene.selection
    finally:
        win._saved_version = win.viewport.scene.version
        win.close()
