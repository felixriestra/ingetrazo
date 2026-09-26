# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""What an extension can reach beyond its tools — ``setup(app)``.

A plugin module that defines ``setup(app)`` gets it called once, when the
main window is built, with an :class:`ExtensionApp`. Through it the plugin
can keep data IN THE DOCUMENT, add a panel to the side tray, draw over the
viewport and offer the cursor an inference — whatever tool is active. That
is enough to build a whole feature outside the core (the Levels plugin is
the worked example), which is the point: what only some users need lives
in an extension they choose, not in everyone's IngeTrazo.

Contract (``API_VERSION`` 1; still 0.x — see docs/plugins.md):

- **Document data** is ONE JSON-safe value per extension (its key: the
  plugin's file name). It is saved in the .igz, reset by New/Open, and every
  change through :meth:`ExtensionApp.set_document_data` is one undo step.
- **Overlays** draw with a ``QPainter`` over the finished frame, after the
  active tool's own; each call is wrapped in save/restore.
- **Snap providers** see the snap engine's answer and may return another
  :class:`core.snap.SnapResult` (with a ``label``) — but never over a named
  point (endpoint, midpoint, centre, intersection…), which the user aimed at.
- A provider or overlay that raises is logged and skipped: an extension
  cannot break painting or the cursor.
"""
from __future__ import annotations

API_VERSION = 1


class ExtensionApp:
    """One plugin's handle on the running application."""

    api_version = API_VERSION

    def __init__(self, window, key: str) -> None:
        self._window = window
        self.key = str(key)

    # ---- Where things are ------------------------------------------------
    @property
    def window(self):
        return self._window

    @property
    def viewport(self):
        return self._window.viewport

    @property
    def scene(self):
        return self._window.viewport.scene

    # ---- Document data -----------------------------------------------------
    def document_data(self, default=None):
        """This extension's value in the open document (a copy: change it
        with :meth:`set_document_data`, never in place)."""
        import json
        data = getattr(self.scene, "plugin_data", {}) or {}
        if self.key not in data:
            return default
        return json.loads(json.dumps(data[self.key]))

    def set_document_data(self, value) -> None:
        """Store ``value`` (JSON-safe; ``None`` removes it) in the document,
        as one undo step — the document is then unsaved, like any edit."""
        from core.history import SetPluginDataCommand
        vp = self.viewport
        vp.history.execute(SetPluginDataCommand(self.key, value))
        notify = getattr(vp, "notify_scene_changed", None)
        if callable(notify):
            notify()
        vp.update()

    def on_document_changed(self, fn) -> None:
        """Call ``fn()`` whenever the document changes — an edit, an undo,
        New, Open — so a panel can show the current data."""
        self.viewport.sceneVersionChanged.connect(lambda _v: fn())

    # ---- Side panel ----------------------------------------------------------
    def add_panel(self, title: str, widget):
        """Put ``widget`` in the side tray as a tab of its own, beside
        Properties / BIM / Terrain. Returns the dock."""
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QDockWidget, QWidget
        win = self._window
        dock = QDockWidget(title, win)
        dock.setObjectName(f"extension_{self.key}")
        dock.setWidget(widget)
        dock.setTitleBarWidget(QWidget(dock))   # the tab already names it
        win.addDockWidget(Qt.RightDockWidgetArea, dock)
        anchor = next((d for d in reversed(win._sidebar_docks())
                       if d is not dock), None)
        if anchor is not None:
            win.tabifyDockWidget(anchor, dock)
        win._extension_docks.append(dock)
        tray = getattr(win, "tray", None)
        if tray is not None:
            tray.raise_()                   # Properties stays the one in front
        return dock

    # ---- Viewport ------------------------------------------------------------
    def add_overlay(self, fn) -> None:
        """``fn(viewport, painter)`` draws over every frame."""
        self.viewport._ext_overlays.append(fn)
        self.viewport.update()

    def add_snap_provider(self, fn) -> None:
        """``fn(viewport, snap, px, py)`` → a ``SnapResult`` to use instead,
        or ``None`` to leave the engine's answer."""
        self.viewport._ext_snap_providers.append(fn)
