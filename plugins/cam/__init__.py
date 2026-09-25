# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""CAM — 2.5D toolpaths and G-code for GRBL and LinuxCNC (Extensions ▸ CAM…).

A package plugin: the engine (``engine/``, a Python port of 2DCam's
``TwoDCamCore``: profiles, pockets, drilling, engraving, facing, the
verifier and the posts) plus its Qt face (``ui/``). This module only
registers the menu entry; everything else loads the first time it runs,
so a broken CAM installation cannot slow IngeTrazo's start-up.

The plugin is imported BY PATH under a private name (core/extensions.py):
every import inside the package is relative for that reason.
"""
from __future__ import annotations

from tools.base import Tool


class CamTool(Tool):
    """Opens (or brings forward) the CAM dock."""

    name = "CAM…"
    shortcut = None

    def on_activate(self, viewport) -> None:
        from .ui.dock import show_dock
        show_dock(viewport)

    def on_deactivate(self, viewport) -> None:
        pass
