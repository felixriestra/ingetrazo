# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Plugin workspaces (host hook H5): a plugin shows its own document — the
CAM plugin's job, a 2.5D drawing on the stock — in place of the model.

The model is parked, not closed: it comes back with its geometry, undo
steps, file, camera and saved state. Meanwhile the File actions, the
title, the unsaved-changes prompt and the tool set belong to the
workspace.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QCloseEvent, QVector3D as V
from PySide6.QtWidgets import QApplication

from core.history import History
from core.scene import Scene

_app = QApplication.instance() or QApplication([])


@pytest.fixture
def win(tmp_path, monkeypatch):
    path = tmp_path / "prefs.ini"
    import PySide6.QtCore as qc
    import views.main_window as mw
    factory = lambda *a: QSettings(str(path), QSettings.IniFormat)  # noqa: E731
    monkeypatch.setattr(qc, "QSettings", factory)
    monkeypatch.setattr(mw, "QSettings", factory, raising=False)
    from views.main_window import MainWindow
    w = MainWindow()
    yield w
    w._workspace = None                 # let the window close without prompts
    w._saved_version = w.viewport.scene.version
    w.close()


class FakeWorkspace:
    def __init__(self, allowed=None, leave=True):
        self.scene = Scene()
        self.history = History(self.scene)
        self.allowed_tools = allowed
        self.saved = 0
        self.dirty = False
        self.leave_answer = leave
        self.gone = False

    def title(self):
        return "job.igcam"

    def is_dirty(self):
        return self.dirty

    def save(self):
        self.saved += 1
        self.dirty = False

    def save_as(self):
        self.save()

    def confirm_leave(self):
        return self.leave_answer

    def left(self):
        self.gone = True



def test_the_model_is_parked_and_comes_back(win):
    model = win.viewport.scene
    model.mesh.add_face([V(0, 0, 0), V(1, 0, 0), V(1, 1, 0), V(0, 1, 0)])
    history = win.viewport.history
    win._saved_version = model.version                   # a saved model
    ws = FakeWorkspace()
    assert win.enter_workspace(ws)
    assert win.viewport.scene is ws.scene and win.viewport.history is ws.history
    assert ws.scene.version > model.version              # no render cache is shared
    assert win.windowTitle() == "IngeTrazo — job.igcam"
    assert not win.enter_workspace(FakeWorkspace())      # one at a time
    assert win.leave_workspace()
    assert win.viewport.scene is model and win.viewport.history is history
    assert len(model.mesh.faces) == 1
    assert not win._is_dirty()                           # still saved
    assert ws.gone


def test_file_actions_and_the_title_go_to_the_workspace(win):
    ws = FakeWorkspace()
    win.enter_workspace(ws)
    ws.dirty = True
    win._update_title()
    assert win.windowTitle().endswith("job.igcam *")
    assert win._is_dirty()
    win._on_save()
    assert ws.saved == 1 and not win._is_dirty()
    win.leave_workspace()


def test_the_tool_filter_keeps_3d_tools_out(win):
    ws = FakeWorkspace(allowed={"select", "line", "rectangle"})
    win.enter_workspace(ws)
    assert not win._tool_actions["pushpull"].isEnabled()
    assert win._tool_actions["line"].isEnabled()
    win._activate_tool("pushpull")
    assert win.viewport.active_tool is not win._tools["pushpull"]
    win.leave_workspace()
    assert win._tool_actions["pushpull"].isEnabled()


def test_a_workspace_that_will_not_go_stops_quitting(win):
    ws = FakeWorkspace(leave=False)
    win.enter_workspace(ws)
    event = QCloseEvent()
    win.closeEvent(event)
    assert not event.isAccepted()
    assert win.workspace() is ws
    ws.leave_answer = True
    assert win.leave_workspace()


def test_a_plugin_opens_its_own_file_type(win):
    opened = []
    win.file_openers[".igcam"] = lambda p: opened.append(p) or True
    assert win.open_path(Path("/tmp/a.igcam"))
    assert opened == [Path("/tmp/a.igcam")]


def test_autosave_pauses_while_the_model_is_parked(win, monkeypatch):
    from core import autosave
    written = []
    monkeypatch.setattr(autosave, "write", lambda *a, **k: written.append(a))
    ws = FakeWorkspace()
    win.enter_workspace(ws)
    ws.dirty = True
    win._on_autosave_tick()
    assert written == []
    win.leave_workspace()


def test_a_plugin_install_hook_runs_at_startup(tmp_path, monkeypatch):
    """``install(window)`` in a plugin module is called once when the window
    loads its plugins; one that raises is skipped without breaking it."""
    good = tmp_path / "hooked"
    good.mkdir()
    (good / "__init__.py").write_text(
        "from tools.base import Tool\n"
        "class HookedTool(Tool):\n"
        "    name = 'Hooked'\n"
        "    def on_activate(self, viewport): pass\n"
        "    def on_deactivate(self, viewport): pass\n"
        "def install(window):\n"
        "    window.file_openers['.hooked'] = lambda p: True\n")
    bad = tmp_path / "broken_hook"
    bad.mkdir()
    (bad / "__init__.py").write_text(
        "from tools.base import Tool\n"
        "class BrokenTool(Tool):\n"
        "    name = 'Broken'\n"
        "    def on_activate(self, viewport): pass\n"
        "    def on_deactivate(self, viewport): pass\n"
        "def install(window):\n"
        "    raise RuntimeError('boom')\n")
    import core.extensions as ext
    real = ext.plugin_dirs
    monkeypatch.setattr(ext, "plugin_dirs", lambda: [tmp_path] + list(real()))
    path = tmp_path / "prefs.ini"
    import PySide6.QtCore as qc
    import views.main_window as mw
    factory = lambda *a: QSettings(str(path), QSettings.IniFormat)  # noqa: E731
    monkeypatch.setattr(qc, "QSettings", factory)
    monkeypatch.setattr(mw, "QSettings", factory, raising=False)
    from views.main_window import MainWindow
    w = MainWindow()
    assert ".hooked" in w.file_openers
    assert ".igcam" in w.file_openers            # the CAM plugin's, from its hook
    assert w.open_path(Path("/tmp/x.hooked"))
    w.close()


def test_the_launcher_hands_a_plugin_file_to_the_window():
    """A double-click (macOS Apple Event, or argv on Linux/Windows) goes
    through main._open_document_in: a suffix a plugin claimed must reach
    the window's open_path, not be dropped as unknown."""
    import main
    opened = []

    class Win:
        file_openers = {".igcam": None}

        def open_path(self, p):
            opened.append(p)
            return True

    main._open_document_in(Win(), Path("/tmp/job.igcam"))
    main._open_document_in(Win(), Path("/tmp/notes.txt"))
    assert opened == [Path("/tmp/job.igcam")]
