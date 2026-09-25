# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""#114 (macOS): the sheet composer opened with no toolbar at all. With
every toolbar hidden there is nothing left to right-click to bring them
back, so a saved arrangement like that is never a choice: the composer
restores the factory layout when it shows."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_a_composer_saved_with_every_toolbar_hidden_opens_with_them(monkeypatch):
    from PySide6.QtWidgets import QApplication, QToolBar
    from views.composer import ComposerWindow
    from views.main_window import MainWindow
    monkeypatch.setattr(ComposerWindow, "render_frame", lambda self, f: None)
    app = QApplication.instance()
    win = MainWindow()
    comps = []
    try:
        c = ComposerWindow(win)
        comps.append(c)
        c.show()
        app.processEvents()
        for tb in c.findChildren(QToolBar):
            tb.hide()                          # the broken arrangement…
        c.close()                              # …saved on close
        c2 = ComposerWindow(win)
        comps.append(c2)
        c2.show()
        for _ in range(5):
            app.processEvents()
        assert c2._tools_tb.isVisible()
        assert c2._draw_tb.isVisible() and c2._sheet_tb.isVisible()
        # a single toolbar hidden on purpose stays hidden
        c2._draw_tb.hide()
        c2.close()
        c3 = ComposerWindow(win)
        comps.append(c3)
        c3.show()
        for _ in range(5):
            app.processEvents()
        assert c3._tools_tb.isVisible() and not c3._draw_tb.isVisible()
    finally:
        for c in comps:
            c.close()
        win._saved_version = win.viewport.scene.version
        win.close()
