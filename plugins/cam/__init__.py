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


def install(window) -> None:
    """Startup hook (core/extensions.py): claim ``.igcam`` so a job
    double-clicked in the file manager, passed on the command line or picked
    from Open Recent opens straight into CAM mode, whether or not the CAM
    panel was ever opened. Nothing else loads until a job is opened."""
    if hasattr(window, "file_openers"):
        window.file_openers[".igcam"] = lambda path: open_job(window, path)


def open_job(window, path) -> bool:
    """Open the CAM job at ``path`` in ``window``, with the CAM panel."""
    from pathlib import Path

    from .ui.dock import show_dock
    return show_dock(window.viewport).open_job(Path(path))


class CamTool(Tool):
    """Opens (or brings forward) the CAM dock."""

    name = "CAM…"
    shortcut = None

    def on_activate(self, viewport) -> None:
        from .ui.dock import show_dock
        show_dock(viewport)

    def on_deactivate(self, viewport) -> None:
        pass
