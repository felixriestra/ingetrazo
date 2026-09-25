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


def test_the_window_menu_shortcuts_act_on_the_composer_when_it_is_in_front(monkeypatch):
    """#114, macOS: the menu bar is global, so the model window's Cmd+0
    (Window ▸ Clean screen) fired with the composer in front and blanked
    the MODEL. Routed: the composer in front takes it."""
    from PySide6.QtWidgets import QApplication
    from views.composer import ComposerWindow
    from views.main_window import MainWindow
    monkeypatch.setattr(ComposerWindow, "render_frame", lambda self, f: None)
    win = MainWindow()
    try:
        comp = win._ensure_composer()
        monkeypatch.setattr(QApplication, "activeWindow",
                            staticmethod(lambda: comp))
        win._act_clean_screen.trigger()           # what Cmd+0 does on a Mac
        assert comp._act_clean_screen.isChecked()
        assert not win._act_clean_screen.isChecked()
        win._act_clean_screen.trigger()
        assert not comp._act_clean_screen.isChecked()
        win._act_sidebar.trigger()                # Ctrl+F5 likewise
        assert not comp._act_sidebar.isChecked()
        assert win._act_sidebar.isChecked()
        comp._act_sidebar.setChecked(True)
        # with the model in front it is the model's own toggle again
        monkeypatch.setattr(QApplication, "activeWindow",
                            staticmethod(lambda: win))
        win._act_sidebar.trigger()
        assert not win._act_sidebar.isChecked()
        win._act_sidebar.trigger()
    finally:
        c = getattr(win, "_composer", None)
        if c is not None:
            c.close()
        win._saved_version = win.viewport.scene.version
        win.close()
