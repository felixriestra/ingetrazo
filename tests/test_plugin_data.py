# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""``scene.plugin_data`` — document data owned by plugins (host hook H4).

A CAM job belongs to the document it machines: it must survive save and
reopen, stay out of the way of builds that do not have the plugin, never
leak into the next document opened, and be undoable like any other edit.
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.history import History, SetPluginData
from core.scene import Scene
from formats import igz

JOB = {"units": "mm", "stock": {"x": 600.0, "y": 400.0, "z": 18.0},
       "operations": [{"kind": "profile", "texture": "not an image",
                       "tabs": [1, 2, 3], "nested": {"a": None}}]}


def _roundtrip(scene, tmp_path):
    path = tmp_path / "m.igz"
    igz.save_scene(scene, path)
    back = Scene()
    igz.load_into(back, path)
    return back, path


def test_plugin_data_survives_save_and_reopen(tmp_path):
    scene = Scene()
    scene.plugin_data["cam"] = JOB
    back, _ = _roundtrip(scene, tmp_path)
    # A "texture" key inside plugin data is the plugin's, not an image the
    # texture packer should chase.
    assert back.plugin_data == {"cam": JOB}


def test_a_document_without_plugin_data_stays_terse(tmp_path):
    _, path = _roundtrip(Scene(), tmp_path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert "plugin_data" not in doc["scene"]


def test_opening_a_document_does_not_inherit_the_previous_ones(tmp_path):
    path = tmp_path / "plain.igz"
    igz.save_scene(Scene(), path)
    scene = Scene()
    scene.plugin_data["cam"] = JOB
    igz.load_into(scene, path)
    assert scene.plugin_data == {}


def test_data_of_a_plugin_not_installed_is_kept(tmp_path):
    """Opening and re-saving a document where the plugin is missing must
    not destroy that plugin's data — it is carried along untouched."""
    scene = Scene()
    scene.plugin_data["someone_elses"] = {"x": 1}
    back, _ = _roundtrip(scene, tmp_path)
    again, _ = _roundtrip(back, tmp_path)
    assert again.plugin_data == {"someone_elses": {"x": 1}}


def test_an_unserialisable_entry_costs_that_plugin_only(tmp_path):
    scene = Scene()
    scene.plugin_data["good"] = {"x": 1}
    scene.plugin_data["bad"] = {"s": {1, 2}}                 # a set
    scene.plugin_data["nan"] = {"v": float("nan")}           # not strict JSON
    scene.plugin_data["notadict"] = [1, 2]
    back, _ = _roundtrip(scene, tmp_path)
    assert back.plugin_data == {"good": {"x": 1}}


def test_malformed_plugin_data_in_a_file_is_ignored(tmp_path):
    path = tmp_path / "m.igz"
    igz.save_scene(Scene(), path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["scene"]["plugin_data"] = {"ok": {"a": 1}, "junk": 7}
    path.write_text(json.dumps(doc), encoding="utf-8")
    back = Scene()
    igz.load_into(back, path)
    assert back.plugin_data == {"ok": {"a": 1}}
    doc["scene"]["plugin_data"] = "garbage"
    path.write_text(json.dumps(doc), encoding="utf-8")
    igz.load_into(back, path)
    assert back.plugin_data == {}


def test_file_new_clears_plugin_data():
    scene = Scene()
    scene.plugin_data["cam"] = {"x": 1}
    v = scene.version
    scene.clear()
    assert scene.plugin_data == {}
    assert scene.version > v


def test_set_plugin_data_is_undoable_and_isolated_from_the_live_dict():
    scene = Scene()
    hist = History(scene)
    job = {"n": 1}
    hist.execute(SetPluginData("cam", job))
    job["n"] = 99                         # the plugin keeps editing its dict
    assert scene.plugin_data == {"cam": {"n": 1}}
    hist.execute(SetPluginData("cam", {"n": 2}))
    assert scene.plugin_data["cam"] == {"n": 2}
    scene.plugin_data["cam"]["n"] = 50    # live mutation after the command
    hist.undo()
    assert scene.plugin_data["cam"] == {"n": 1}
    hist.undo()
    assert "cam" not in scene.plugin_data
    hist.redo()
    hist.redo()
    assert scene.plugin_data["cam"] == {"n": 2}


def test_set_plugin_data_none_removes_the_entry():
    scene = Scene()
    scene.plugin_data["cam"] = {"n": 1}
    hist = History(scene)
    hist.execute(SetPluginData("cam", None))
    assert "cam" not in scene.plugin_data
    hist.undo()
    assert scene.plugin_data == {"cam": {"n": 1}}
