# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Golden G-code: posted programs reviewed by hand once, then frozen.

The posts have no Swift reference (2DCam never had GRBL or LinuxCNC), so
their output is pinned byte for byte on a few corpus jobs. A change here
is a change to what machines receive: review the diff of
``tests/data/cam_golden/`` line by line, then regenerate with

    CAM_UPDATE_GOLDEN=1 python -m pytest tests/test_cam_golden.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from plugins.cam.engine import compiler, io
from plugins.cam.engine.post import post_job

ROOT = Path(__file__).parent / "data"
CASES = ["pocket_island", "profile_outside_tabs_leads_ramp", "drill_nearest_neighbor",
         "job_multi_operation", "inch_profile_outside", "profile_outside_controller_comp"]


def _programs(case, controller):
    job = io.job_from_dict(json.loads((ROOT / "cam_fixtures" / case / "job.json").read_text()))
    if case == "drill_nearest_neighbor":
        job.operations[0].parameters.peckDepth = 4.0     # exercise G83 / expanded pecks
    job.post.controller = controller
    tp = compiler.compile_job(job).toolpath
    res = post_job(job, tp)
    assert not res.issues
    return {f"{case}{f.suffix}.{controller}{f.extension}": f.text for f in res.files}


@pytest.mark.parametrize("controller", ["grbl", "linuxcnc"])
@pytest.mark.parametrize("case", CASES)
def test_golden(case, controller):
    if case == "profile_outside_controller_comp" and controller == "grbl":
        pytest.skip("GRBL refuses controller compensation (tested in test_cam_post)")
    gold = ROOT / "cam_golden"
    for name, text in _programs(case, controller).items():
        path = gold / name
        if os.environ.get("CAM_UPDATE_GOLDEN"):
            gold.mkdir(exist_ok=True)
            path.write_text(text, encoding="ascii", newline="\n")
        assert path.is_file(), f"no golden file {name}; generate and review it"
        assert text == path.read_text(encoding="ascii"), f"{name} changed"
