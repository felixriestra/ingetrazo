# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""``MainWindow.add_plugin_dock`` — the side-panel hook for plugins (H2).

A plugin builds its dock lazily, the first time its tool runs, long after
the window restored its saved layout. These tests pin what such a dock
must still get: a place next to the trays, a Window-menu entry, and the
place the user gave it last session.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QDockWidget, QLabel

_app = QApplication.instance() or QApplication([])


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    """A throwaway INI for every QSettings() the windows open."""
    path = tmp_path / "prefs.ini"
    factory = lambda *a: QSettings(str(path), QSettings.IniFormat)  # noqa: E731
    import PySide6.QtCore as qc
    import views.main_window as mw
    monkeypatch.setattr(qc, "QSettings", factory)
    monkeypatch.setattr(mw, "QSettings", factory)
    return path


def _dock(win, name="plugin_test_dock", title="Test panel"):
    dock = QDockWidget(title, win)
    if name:
        dock.setObjectName(name)
    dock.setWidget(QLabel("hello", dock))
    return dock


def test_a_dock_without_an_object_name_is_refused(settings_file):
    from views.main_window import MainWindow
    win = MainWindow()
    with pytest.raises(ValueError):
        win.add_plugin_dock(_dock(win, name=""))


def test_a_new_dock_opens_tabbed_with_the_trays(settings_file):
    from views.main_window import MainWindow
    win = MainWindow()
    dock = win.add_plugin_dock(_dock(win))
    assert win.dockWidgetArea(dock) == Qt.RightDockWidgetArea
    assert dock in win.tabifiedDockWidgets(win.georef_tray)
    assert not dock.isHidden()
    assert win.plugin_docks() == {"plugin_test_dock": dock}


def test_the_window_menu_gets_an_entry_below_its_own_separator(settings_file):
    from views.main_window import MainWindow
    win = MainWindow()
    win.show()                  # the toggle mirrors on-screen visibility
    assert not win._plugin_dock_sep.isVisible()
    dock = win.add_plugin_dock(_dock(win))
    actions = win._window_menu.actions()
    toggle = dock.toggleViewAction()
    assert toggle in actions
    i = actions.index(toggle)
    assert actions[i - 1] is win._plugin_dock_sep
    assert actions[i + 1] is win._plugin_dock_anchor
    assert win._plugin_dock_sep.isVisible()
    toggle.trigger()
    assert dock.isHidden()
    toggle.trigger()
    assert not dock.isHidden()


def test_registering_twice_returns_the_first_dock(settings_file):
    from views.main_window import MainWindow
    win = MainWindow()
    first = win.add_plugin_dock(_dock(win))
    first.hide()
    second = win.add_plugin_dock(_dock(win))
    assert second is first
    assert not first.isHidden()                 # show=True reopened it
    toggles = [a for a in win._window_menu.actions()
               if a is first.toggleViewAction()]
    assert len(toggles) == 1


def test_show_false_registers_without_opening(settings_file):
    from views.main_window import MainWindow
    win = MainWindow()
    dock = _dock(win)
    dock.hide()
    win.add_plugin_dock(dock, show=False)
    assert dock.isHidden()


def test_a_late_dock_comes_back_where_the_user_left_it(settings_file):
    """The saved window state remembers a plugin dock by objectName; the
    next session restores the layout BEFORE the plugin has made its dock,
    and adding it later must still land it in that place."""
    from views.main_window import MainWindow
    win = MainWindow()
    win.show()
    dock = win.add_plugin_dock(_dock(win))
    win.removeDockWidget(dock)
    win.addDockWidget(Qt.LeftDockWidgetArea, dock)
    dock.show()
    import views.main_window as mw
    mw.QSettings().setValue("ui/window_state", win.saveState())

    again = MainWindow()                        # restores ui/window_state
    dock2 = again.add_plugin_dock(_dock(again))
    assert again.dockWidgetArea(dock2) == Qt.LeftDockWidgetArea
