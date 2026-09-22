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

from core.cutlist import GrainAxis, StockDefinition, WorkshopSettings  # noqa: E402
from core.group import Group                                     # noqa: E402
from core.mesh import Mesh                                       # noqa: E402
from core.scene import Scene                                     # noqa: E402
from plugins.cutlist import (                                     # noqa: E402
    CutListDialog,
    CutListTool,
    load_default_stocks,
    load_default_workshop,
    parts_from_selection,
    remember_stocks,
    remember_workshop,
)


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
        dialog = CutListDialog(win.viewport, parent=win)
        try:
            assert dialog._parts  # populated from the selection on init
        finally:
            dialog.close()
    finally:
        win._saved_version = win.viewport.scene.version
        win.close()


# ---------------------------------------------------------------------------
# Stock / workshop persistence (QSettings) — tests/conftest.py points
# QSettings at a session-temp store, so these never touch the developer's
# real preferences.
# ---------------------------------------------------------------------------

def test_stock_round_trips_through_a_dict():
    from plugins.cutlist import _stock_from_dict, _stock_to_dict
    stock = StockDefinition(name="Baltic birch", material="Birch plywood",
                             thickness=18.0, length=2440.0, width=1220.0,
                             available_sheets=3, grain_axis=GrainAxis.LENGTH)
    restored = _stock_from_dict(_stock_to_dict(stock))
    assert restored.name == stock.name
    assert restored.material == stock.material
    assert restored.thickness == stock.thickness
    assert restored.length == stock.length
    assert restored.width == stock.width
    assert restored.available_sheets == stock.available_sheets
    assert restored.grain_axis == stock.grain_axis


def test_unlimited_stock_round_trips_as_none():
    from plugins.cutlist import _stock_from_dict, _stock_to_dict
    stock = StockDefinition(name="MDF", material="MDF", thickness=10.0,
                             length=2500.0, width=1250.0, available_sheets=None)
    restored = _stock_from_dict(_stock_to_dict(stock))
    assert restored.available_sheets is None


def test_workshop_round_trips_through_a_dict():
    from plugins.cutlist import _workshop_from_dict, _workshop_to_dict
    workshop = WorkshopSettings(kerf=3.2, short_edge_cleanup=4.0, long_edge_trim=12.0,
                                 minimum_remnant_width=120.0, minimum_remnant_length=350.0)
    restored = _workshop_from_dict(_workshop_to_dict(workshop))
    assert restored == workshop


def test_stocks_and_workshop_persist_across_dialogs():
    """The exact scenario that motivated this: generate a plan (which syncs
    and remembers), close the dialog, open a fresh one — Stock & Setup is
    not empty."""
    from views.main_window import MainWindow

    win = MainWindow()
    try:
        board = _box_group("Shelf", length=0.76, width=0.52, thickness=0.018)
        win.viewport.scene.groups.append(board)
        win.viewport.scene.selection = {board}

        first = CutListDialog(win.viewport, parent=win)
        first._stocks = [StockDefinition(name="Remembered ply", material="HD plywood",
                                          thickness=18.0, length=2440.0, width=1220.0)]
        first._populate_stock_table()  # so _sync_stock_edits finds a matching row
        first._kerf_spin.setValue(4.0)  # a real UI edit, not a direct model change
        first.close()  # closeEvent syncs the table/spinboxes back before remembering

        second = CutListDialog(win.viewport, parent=win)
        try:
            names = [s.name for s in second._stocks]
            assert "Remembered ply" in names
            assert second._workshop.kerf == pytest.approx(4.0)
        finally:
            second.close()
    finally:
        win._saved_version = win.viewport.scene.version
        win.close()


def test_load_functions_tolerate_missing_or_corrupt_settings():
    from PySide6.QtCore import QSettings
    QSettings().remove("cutlist/default_stocks")
    QSettings().remove("cutlist/workshop_settings")
    assert load_default_stocks() == []
    assert load_default_workshop() == WorkshopSettings()

    QSettings().setValue("cutlist/default_stocks", "not json")
    QSettings().setValue("cutlist/workshop_settings", "not json")
    assert load_default_stocks() == []
    assert load_default_workshop() == WorkshopSettings()
