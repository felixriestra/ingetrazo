# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Parity with 2DCam: the Python engine against the Swift engine's corpus.

``tests/data/cam_fixtures`` holds jobs 2DCam compiled (exported from its
own public API, see the README there). For each one the port must:

- fail where 2DCam fails, and succeed where it succeeds — except where the
  port deliberately refuses a 2DCam path that cuts into the part;
- cut at the SAME depth levels, exactly;
- cut the same places: every horizontal cutting move of one lies within
  0.02 mm of the other's and vice versa (a symmetric Hausdorff distance on
  sampled points — pyclipper and iOverlay place offset vertices a little
  differently, so byte equality is neither possible nor wanted);
- change tools as often, and verify clean.

Cases where the port knowingly differs (docs/cam-2dcam-deviations.md) are
listed in :data:`DEVIATIONS` with the reason, and compared only where the
difference does not reach.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from plugins.cam.engine import compiler, io, verify
from plugins.cam.engine.issues import CamError
from plugins.cam.engine.toolpath import Linear, expanded

ROOT = Path(__file__).parent / "data" / "cam_fixtures"
CASES = json.loads((ROOT / "index.json").read_text())

HAUSDORFF_MM = 0.02

#: case → how it differs on purpose, and how it is compared instead:
#:
#: ``rejects``   2DCam compiles it, the port refuses it (2DCam's path gouges);
#: ``xy``        every cut of the port lies on 2DCam's outline, in plan;
#: ``superset``  the port cuts everywhere 2DCam does at depth (and more);
#: ``gouge``     2DCam's path cuts into the part (proven below); only the
#:               depth levels are compared;
#: ``retract``   a different move off the part; compared in full.
DEVIATIONS = {
    # A 1 mm inverted loop in a 5 mm pocket with a 6 mm tool: 1 mm into
    # every wall.
    "error_pocket_too_small_for_tool": "rejects",
    # With leads the port starts mid-edge (a lead never runs past a corner
    # into the wall), so the leads and the tabs measured along the path move.
    "profile_outside_tabs_leads_ramp": "xy",
    # 2DCam's ramp never comes back, leaving each row's wedge uncut.
    "pocket_circle_ramp": "superset",
    # 2DCam's helix spirals around its entry point, across the wall.
    "pocket_lshape_conventional_helix": "gouge",
    "profile_outside_lshape_helix": "gouge",
    # 2DCam's conventional facing leaves with a rapid through the stock.
    "facing_conventional_ramp": "retract",
}


def _load(case):
    d = json.loads((ROOT / case / "job.json").read_text())
    exp = json.loads((ROOT / case / "expected.json").read_text())
    scale = 25.4 if d.get("units") == "inches" else 1.0
    theirs = None
    if exp["ok"]:
        theirs = io.toolpath_from_2dcam(json.loads((ROOT / case / "toolpath.json").read_text()),
                                        scale)
    return io.job_from_dict(d), exp, theirs, scale


def _horizontal_segments(commands):
    """``(a, b)`` of every cutting move that stays at one Z."""
    out, cur = [], None
    for c in expanded(commands):
        to = getattr(c, "to", None)
        if to is None:
            if hasattr(c, "z") and cur is not None:          # RetractZ
                cur = (cur[0], cur[1], c.z)
            continue
        if isinstance(c, Linear) and cur is not None and abs(cur[2] - to[2]) < 1e-6 \
                and math.dist(cur[:2], to[:2]) > 1e-6:
            out.append((cur, to))
        cur = to
    return out


def _samples(segs, step=0.25):
    pts = []
    for a, b in segs:
        n = max(1, int(math.ceil(math.dist(a, b) / step)))
        for k in range(n + 1):
            f = k / n
            pts.append([a[i] + (b[i] - a[i]) * f for i in range(3)])
    return np.asarray(pts, dtype=float).reshape(-1, 3)


def _directed(points, segs) -> float:
    if len(points) == 0:
        return 0.0
    A = np.asarray([s[0] for s in segs], dtype=float)
    B = np.asarray([s[1] for s in segs], dtype=float)
    AB = B - A
    sq = np.maximum((AB * AB).sum(1), 1e-18)
    worst = 0.0
    for s in range(0, len(points), 512):
        P = points[s:s + 512]
        AP = P[:, None, :] - A[None]
        t = np.clip((AP * AB[None]).sum(2) / sq[None], 0, 1)
        proj = A[None] + t[..., None] * AB[None]
        d = np.sqrt(((P[:, None, :] - proj) ** 2).sum(2)).min(1)
        worst = max(worst, float(d.max()))
    return worst


def _flatten_z(segs):
    return [((a[0], a[1], 0.0), (b[0], b[1], 0.0)) for a, b in segs]


@pytest.mark.parametrize("case", CASES)
def test_parity(case):
    job, exp, theirs, _scale = _load(case)
    why = DEVIATIONS.get(case)
    try:
        ours = compiler.compile_job(job)
    except CamError as exc:
        assert not exp["ok"] or why == "rejects", (
            f"2DCam compiles {case}; the port refuses it: {exc}")
        return
    assert exp["ok"], f"2DCam refuses {case} ({exp['error']}); the port compiles it"
    assert why != "rejects"

    tp = ours.toolpath
    assert tp.statistics().toolChangeCount == exp["statistics"]["toolChangeCount"]

    mine, their = _horizontal_segments(tp.commands), _horizontal_segments(theirs.commands)
    levels = lambda segs: {round(a[2], 6) for a, _ in segs}          # noqa: E731
    if why == "xy":
        h = _directed(_samples(_flatten_z(mine)), _flatten_z(their))
    else:
        assert levels(mine) == levels(their)
        if why == "superset":
            h = _directed(_samples(their), mine)
        elif why == "gouge":
            h = 0.0
        else:
            h = max(_directed(_samples(mine), their), _directed(_samples(their), mine))
    assert h <= HAUSDORFF_MM, f"{case}: cut outlines differ by {h:.4f} mm"

    report = verify.verify(job, tp)
    assert report.can_export, [i.code for i in report.errors]
    assert not verify.check_gouges(job, ours)
    if why is None:
        assert not exp["verification"], "2DCam's verifier objects to a case the port accepts"
        # Same motion, so the same machine-time estimate.
        theirs_s = exp["machineDurationSeconds"]
        assert tp.estimated_duration(job.machine) == pytest.approx(theirs_s, rel=2e-3)


def test_the_corpus_is_there_and_complete():
    assert len(CASES) >= 30
    kinds = {json.loads((ROOT / c / "job.json").read_text())["operations"][0]["kind"]
             for c in CASES}
    assert {"facing", "outsideProfile", "insideProfile", "pocket", "drilling",
            "engraving"} <= kinds


def test_every_2dcam_gouge_is_caught_by_the_port_verifier():
    """The corpus cases where 2DCam's own toolpath cuts into the part fail
    the port's gouge check — the reason those cases are deviations."""
    for case in ("error_pocket_too_small_for_tool", "profile_outside_lshape_helix",
                 "pocket_lshape_conventional_helix"):
        job, _exp, theirs, _ = _load(case)
        op = job.operations[0]
        fake = compiler.CompileResult(theirs, operation_ranges={op.id: (0, len(theirs.commands))})
        assert verify.check_gouges(job, fake), case
