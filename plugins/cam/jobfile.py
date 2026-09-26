# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The CAM job file, ``.igcam``.

A CAM job is not a model: it is the stock, the machine, the tools, the 2D
geometry drawn on the stock and the operations on its paths. It is saved
on its own so it can be reopened, updated and reused, and a model's
``.igz`` never carries it (docs/cam-plan.md, «The CAM workspace»).

The file is a zip:

- ``job.json`` — ``{"format": "igcam", "version": 1, "state": …}``, the
  :class:`~.state.CamState` (setup, tools, operations). It is the
  authority for everything CAM.
- ``geometry.igz`` — the job's drawing, written by IngeTrazo's own
  ``.igz`` writer so every kind of edge, arc and curve the tools make
  survives exactly. Its own plugin data is ignored on reading.

A stock template (later) is the same file with no geometry and no
operations.
"""
from __future__ import annotations

import json
import os
import tempfile
import zipfile
from pathlib import Path

from .state import CamState

SUFFIX = ".igcam"
FORMAT = "igcam"
VERSION = 1
PLUGIN_KEY = "cam"


class JobFileError(Exception):
    """The file is not a CAM job this version can read."""


def save_job(scene, path) -> None:
    """Write the job shown in ``scene`` (its geometry, and its CAM state in
    ``scene.plugin_data["cam"]``) to ``path``, atomically: a failed save
    leaves the previous file whole."""
    from formats import igz
    path = Path(path)
    state = (getattr(scene, "plugin_data", None) or {}).get(PLUGIN_KEY)
    if state is None:
        raise JobFileError("no CAM job in this scene")
    doc = {"format": FORMAT, "version": VERSION, "state": state}
    with tempfile.TemporaryDirectory() as tmp:
        geo = Path(tmp) / "geometry.igz"
        # The drawing only: the CAM state lives in job.json.
        saved_pd = scene.plugin_data
        scene.plugin_data = {}
        try:
            igz.save_scene(scene, geo)
        finally:
            scene.plugin_data = saved_pd
        part = path.with_name(path.name + ".part")
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("job.json", json.dumps(doc, indent=1, ensure_ascii=False))
            zf.write(geo, "geometry.igz")
        os.replace(part, path)


def load_job(scene, path) -> CamState:
    """Replace ``scene`` with the job at ``path``; returns its state (also
    put in ``scene.plugin_data["cam"]``)."""
    from formats import igz
    path = Path(path)
    try:
        with zipfile.ZipFile(path) as zf:
            doc = json.loads(zf.read("job.json").decode("utf-8"))
            with tempfile.TemporaryDirectory() as tmp:
                geo = Path(tmp) / "geometry.igz"
                geo.write_bytes(zf.read("geometry.igz"))
                igz.load_into(scene, geo)
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
        raise JobFileError(str(exc)) from exc
    if doc.get("format") != FORMAT or int(doc.get("version", 0)) > VERSION:
        raise JobFileError(f"not a CAM job this version reads ({doc.get('format')!r}, "
                           f"version {doc.get('version')!r})")
    state = CamState.from_dict(doc.get("state") or {})
    scene.plugin_data = {PLUGIN_KEY: state.to_dict()}
    return state
