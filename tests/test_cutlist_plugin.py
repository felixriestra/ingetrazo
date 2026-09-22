# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Cut List plugin: selection -> parts extraction, and the dialog opens
without crashing. Follows tests/test_model_info_plugin.py's offscreen-Qt
recipe."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QVector3D
from PySide6.QtWidgets import QApplication

_inst = QApplication.instance()
if _inst is None:
    _app = QApplication([])
elif not isinstance(_inst, QApplication):
    pytest.skip("a non-widget QGuiApplication is already active",
                allow_module_level=True)

from core.group import Group                                     # noqa: E402
from core.mesh import Mesh                                       # noqa: E402
from core.scene import Scene                                     # noqa: E402
from plugins.cutlist import CutListTool, parts_from_selection     # noqa: E402


def _box_group(name: str, length: float, width: float, thickness: float,
               material: dict | None = None) -> Group:
    """A closed box mesh of the given metre dimensions, as a named Group."""
    mesh = Mesh()
    x, y, z = length, width, thickness
    corners = [
        [(0, 0, 0), (x, 0, 0), (x, y, 0), (0, y, 0)],   # bottom
        [(0, 0, z), (0, y, z), (x, y, z), (x, 0, z)],   # top
        [(0, 0, 0), (0, y, 0), (0, y, z), (0, 0, z)],   # sides...
        [(x, 0, 0), (x, 0, z), (x, y, z), (x, y, 0)],
        [(0, 0, 0), (0, 0, z), (x, 0, z), (x, 0, 0)],
        [(0, y, 0), (x, y, 0), (x, y, z), (0, y, z)],
    ]
    for ring in corners:
        mesh.add_face([QVector3D(*p) for p in ring])
    group = Group(mesh, name=name)
    if material is not None:
        group.material = material
    return group


def test_parts_from_selection_reads_dimensions_in_millimetres():
    scene = Scene()
    board = _box_group("Shelf", length=0.76, width=0.52, thickness=0.018)
    scene.groups.append(board)
    scene.selection = {board}

    parts, settings = parts_from_selection(scene)

    assert len(parts) == 1
    part = parts[0]
    assert part.quantity == 1
    assert abs(part.length - 760.0) < 0.1
    assert abs(part.width - 520.0) < 0.1
    assert abs(part.thickness - 18.0) < 0.1
    assert settings[part.id].material == "Unassigned"


def test_identical_selected_groups_collapse_into_one_part_with_quantity():
    scene = Scene()
    a = _box_group("Side", length=2.1, width=0.56, thickness=0.018)
    b = _box_group("Side", length=2.1, width=0.56, thickness=0.018)
    scene.groups.extend([a, b])
    scene.selection = {a, b}

    parts, _settings = parts_from_selection(scene)

    assert len(parts) == 1
    assert parts[0].quantity == 2


def test_material_name_comes_from_the_group_paint():
    scene = Scene()
    board = _box_group("Panel", length=1.0, width=0.5, thickness=0.018,
                        material={"mat": "Birch plywood", "color": (0.8, 0.6, 0.3)})
    scene.groups.append(board)
    scene.selection = {board}

    parts, settings = parts_from_selection(scene)

    assert settings[parts[0].id].material == "Birch plywood"


def test_unselected_groups_are_ignored():
    scene = Scene()
    board = _box_group("Ignored", length=1.0, width=1.0, thickness=0.02)
    scene.groups.append(board)
    scene.selection = set()  # nothing selected

    parts, _settings = parts_from_selection(scene)

    assert parts == []


def test_tool_metadata():
    tool = CutListTool()
    assert tool.name == "Cut List"
    assert tool.shortcut is None and tool.uses_snap is False


def test_dialog_opens_without_crashing(qtbot=None):
    from views.main_window import MainWindow

    win = MainWindow()
    try:
        board = _box_group("Shelf", length=0.76, width=0.52, thickness=0.018)
        win.viewport.scene.groups.append(board)
        win.viewport.scene.selection = {board}
        from plugins.cutlist import CutListDialog
        dialog = CutListDialog(win.viewport, parent=win)
        try:
            assert dialog._parts  # populated from the selection on init
        finally:
            dialog.close()
    finally:
        win._saved_version = win.viewport.scene.version
        win.close()
