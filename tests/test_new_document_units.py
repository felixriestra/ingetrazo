# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The units chosen «for new documents» survive New and a restart (#121),
and never leak into a file that is opened: a document keeps its own."""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

if QApplication.instance() is None:
    QApplication(sys.argv[:1])

MM = {"length": "mm", "precision": 1}


def _close(win):
    win._saved_version = win.viewport.scene.version
    win.close()


def test_new_documents_start_in_the_remembered_units(tmp_path):
    from core import units
    from views.main_window import MainWindow
    units.remember_new_document_units(MM)
    try:
        win = MainWindow()                          # a restart
        try:
            scene = win.viewport.scene
            assert scene.units == MM
            assert scene.dimension_style["units"] == "mm"
            assert scene.version == win._saved_version   # not «modified»
            scene.units = {"length": "m", "precision": 2}
            win._saved_version = scene.version
            win._on_new()                           # File ▸ New
            assert scene.units == MM
        finally:
            _close(win)
    finally:
        units.remember_new_document_units(units.DEFAULT_MODEL_UNITS)


def test_an_opened_document_keeps_its_own_units(tmp_path):
    from core import units
    from core.scene import Scene
    from formats import igz
    path = tmp_path / "old.igz"
    igz.save_scene(Scene(), path)                   # drawn in metres
    units.remember_new_document_units(MM)
    try:
        loaded = Scene()
        igz.load_into(loaded, path)
        assert units.model_units_of(loaded)["length"] == "m"
    finally:
        units.remember_new_document_units(units.DEFAULT_MODEL_UNITS)
