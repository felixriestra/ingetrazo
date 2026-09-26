# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""SketchUp's Edit ▸ Invert Selection (Ctrl+Shift+I).

What is selected drops out, everything else in the open context comes in —
but only what a click or a box could pick: nothing hidden, nothing on a
hidden or locked layer, and nothing outside the group being edited.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QKeySequence, QVector3D
from PySide6.QtWidgets import QApplication

if QApplication.instance() is None:
    QApplication(sys.argv[:1])

from core.group import Group
from core.layers import Layer, assign_layer
from core.mesh import Mesh
from core.scene import Scene


def V(x, y, z=0.0):
    return QVector3D(float(x), float(y), float(z))


def _square(mesh, x0, size=1.0):
    return mesh.add_face([V(x0, 0), V(x0 + size, 0),
                          V(x0 + size, size), V(x0, size)])


def _edges_of(scene, face):
    return [e for e in scene.edges if face in e.faces]


def _loose(scene):
    return set(scene.edges) | set(scene.faces)


# ---- the core rule ----------------------------------------------------------

def test_invert_swaps_selected_and_unselected():
    scene = Scene()
    a = _square(scene.mesh, 0)
    b = _square(scene.mesh, 5)
    scene.select([a])
    before = scene.version

    n = scene.invert_selection()

    assert a not in scene.selection
    assert b in scene.selection
    assert set(_edges_of(scene, b)) <= scene.selection        # its edges come too
    assert n == len(scene.selection)
    assert scene.version > before                  # the viewport repaints


def test_invert_twice_is_the_original_selection():
    scene = Scene()
    a = _square(scene.mesh, 0)
    _square(scene.mesh, 5)
    scene.select([a, *_edges_of(scene, a)])
    original = set(scene.selection)
    scene.invert_selection()
    scene.invert_selection()
    assert scene.selection == original


def test_invert_with_nothing_selected_selects_everything():
    scene = Scene()
    _square(scene.mesh, 0)
    g = Group(Mesh(), name="caja")
    _square(g.mesh, 10)
    scene.groups.append(g)
    scene.invert_selection()
    assert scene.selection == _loose(scene) | {g}


def test_invert_with_everything_selected_clears():
    scene = Scene()
    _square(scene.mesh, 0)
    scene.select(_loose(scene))
    assert scene.invert_selection() == 0
    assert not scene.selection


# ---- what cannot be picked stays out ----------------------------------------

def test_hidden_group_stays_out():
    scene = Scene()
    shown = Group(Mesh(), name="visible")
    hidden = Group(Mesh(), name="oculto")
    hidden.hidden = True
    scene.groups.extend([shown, hidden])
    scene.invert_selection()
    assert shown in scene.selection
    assert hidden not in scene.selection


def test_hidden_face_stays_out_unless_the_hidden_view_is_on():
    scene = Scene()
    face = _square(scene.mesh, 0)
    face.attrs["hidden"] = True
    scene.invert_selection()
    assert face not in scene.selection
    scene.clear_selection()
    scene.show_hidden_geometry = True
    scene.invert_selection()
    assert face in scene.selection


def test_hidden_edge_stays_out_unless_the_hidden_view_is_on():
    scene = Scene()
    face = _square(scene.mesh, 0)
    edge = _edges_of(scene, face)[0]
    edge.hidden = True
    scene.invert_selection()
    assert edge not in scene.selection
    assert face in scene.selection
    scene.clear_selection()
    scene.show_hidden_geometry = True
    scene.invert_selection()
    assert edge in scene.selection


def test_locked_and_hidden_layers_stay_out():
    scene = Scene()
    scene.layers.append(Layer("Fondo", locked=True))
    scene.layers.append(Layer("Mobiliario", visible=False))
    free = _square(scene.mesh, 0)
    locked = _square(scene.mesh, 5)
    off = _square(scene.mesh, 10)
    assign_layer(locked, "Fondo")
    assign_layer(off, "Mobiliario")
    scene.invert_selection()
    assert free in scene.selection
    assert locked not in scene.selection
    assert off not in scene.selection


def test_inside_a_group_only_its_contents_count():
    scene = Scene()
    _square(scene.mesh, 0)                       # loose, outside the group
    outer = Group(Mesh(), name="exterior")
    _square(outer.mesh, 10)
    child = Group(Mesh(), name="hijo")
    outer.children.append(child)
    other = Group(Mesh(), name="otro")
    scene.groups.extend([outer, other])

    scene.begin_group_edit(outer)
    scene.invert_selection()

    assert child in scene.selection               # the open group's child
    assert set(outer.mesh.faces) <= scene.selection   # its own geometry
    assert other not in scene.selection           # the model around it: no
    assert outer not in scene.selection
    scene.end_group_edit()


# ---- the window: menu, shortcut, status bar ---------------------------------

def test_edit_menu_offers_it_with_sketchups_shortcut():
    from PySide6.QtGui import QAction
    from views.main_window import MainWindow
    win = MainWindow()
    try:
        acts = [a for a in win.findChildren(QAction)
                if a.shortcut() == QKeySequence("Ctrl+Shift+I")]
        assert len(acts) == 1                     # one owner: never ambiguous

        scene = win.viewport.scene
        a = _square(scene.mesh, 0)
        b = _square(scene.mesh, 5)
        scene.select([a])
        seen: list = []
        win.viewport.sceneVersionChanged.connect(seen.append)

        acts[0].trigger()

        assert b in scene.selection and a not in scene.selection
        assert seen and seen[-1] == scene.version  # the tray hears about it
        assert win.statusBar().currentMessage()
    finally:
        win._saved_version = win.viewport.scene.version
        win.close()
