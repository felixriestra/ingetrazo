# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The tool library against 2DCam's own answers.

``tests/data/cam_toollib_fixtures/expected.json`` is written by
``twodcam-fixtures`` (``swift run export-toollib-fixtures``), which runs
2DCam's ``PresetResolver``, ``ToolValidator``, ``ValueParser`` and
``CSVImporter`` on fixed inputs. Feeds and speeds go into G-code, so the
resolver must agree with 2DCam to the last rounding, and an import must
classify every catalogue row the same way.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from plugins.cam.toollib import csvread, importer
from plugins.cam.toollib.catalog import CatalogProfile, builtin_profile
from plugins.cam.toollib.model import LibraryTool
from plugins.cam.toollib.repository import ToolLibraryRepository
from plugins.cam.toollib.resolver import CuttingPreset, MachineLimits, resolve
from plugins.cam.toollib.validator import ToolDraft, ToolValidator

DATA = Path(__file__).parent / "data" / "cam_toollib_fixtures"
EXPECTED = json.loads((DATA / "expected.json").read_text(encoding="utf-8"))


def _none(v):
    return None if v is None else v


@pytest.fixture(scope="module")
def reference(tmp_path_factory):
    """The seeded reference data, from OUR schema — which must hold the
    same bands and curves 2DCam's does."""
    repo = ToolLibraryRepository(tmp_path_factory.mktemp("lib") / "lib.sqlite",
                                 seed_starter_tools=False)
    return repo.material_classes(), repo.chipload_rules(), repo.feed_curves()


def test_material_classes_match_2dcam(reference):
    classes, _, _ = reference
    assert [(c.id, c.feed_factor, c.max_rpm_hint) for c in classes] == [
        (c["id"], c["feedFactor"], c["maxRPMHint"]) for c in EXPECTED["materialClasses"]]


def _tool(d) -> LibraryTool:
    return LibraryTool(id=d["id"], name=d["name"], type=d["type"], vendor_id=d["vendorID"],
                       series=d["series"], diameter_mm=d["diameterMM"],
                       corner_radius_mm=d["cornerRadiusMM"],
                       included_angle_deg=d["includedAngleDeg"], tip_dia_mm=d["tipDiaMM"],
                       flute_count=d["fluteCount"], flute_length_mm=d["fluteLengthMM"],
                       overall_length_mm=d["overallLengthMM"])


def _preset(d) -> CuttingPreset:
    return CuttingPreset(id=d["id"], tool_id=d["toolID"], material_class_id=d["materialClassID"],
                         machine_id=d["machineID"], name=d["name"], origin=d["origin"],
                         derivation=d["derivation"], confidence=d["confidence"],
                         chipload_mm=d["chiploadMM"], spindle_rpm=d["spindleRPM"],
                         feed_xy_mm_min=d["feedXYmmMin"], feed_z_mm_min=d["feedZmmMin"],
                         stepdown_mm=d["stepdownMM"], stepover_mm=d["stepoverMM"])


def _machine(d):
    if d is None:
        return None
    return MachineLimits(id=d["id"], max_rpm=d["maxRPM"], min_rpm=d["minRPM"],
                         max_feed=d["maxFeed"], max_plunge=d["maxPlunge"], rigidity=d["rigidity"])


def test_resolver_matches_2dcam_on_every_case(reference):
    classes, rules, curves = reference
    by_class = {c.id: c for c in classes}
    tools = {t["id"]: _tool(t) for t in EXPECTED["tools"]}
    machines = {k: _machine(v) for k, v in EXPECTED["machines"].items()}
    presets = [_preset(p) for p in EXPECTED["presets"]]
    failures = []
    for case in EXPECTED["resolver"]:
        got = resolve(tools[case["tool"]], by_class[case["materialClass"]],
                      machine=machines[case["machine"]],
                      presets=presets if case["presets"] else (), rules=rules,
                      feed_curves=curves, depth_of_cut_mm=case["depth"])
        want = case["result"]
        if want is None or got is None:
            if not (want is None and got is None):
                failures.append((case, got))
            continue
        same = (got.spindle_rpm == want["spindleRPM"]
                and got.feed_xy_mm_min == want["feedXY"]
                and got.feed_z_mm_min == want["feedZ"]
                and math.isclose(got.chipload_mm, want["chiploadMM"], rel_tol=1e-12, abs_tol=1e-12)
                and math.isclose(got.stepdown_mm, want["stepdownMM"], rel_tol=1e-12)
                and math.isclose(got.stepover_mm, want["stepoverMM"], rel_tol=1e-12)
                and got.source == want["source"]
                and got.confidence == want["confidence"]
                and got.is_estimate == want["isEstimate"]
                and got.provenance == want["provenance"]
                and len(got.notes) == len(want["notes"]))
        if not same:
            failures.append((case, got))
    assert not failures, f"{len(failures)} of {len(EXPECTED['resolver'])} differ; first: {failures[0]}"


_DRAFT_KEYS = {
    "rowIndex": "row_index", "name": "name", "productID": "product_id", "series": "series",
    "toolType": "tool_type", "diameterMM": "diameter_mm", "cornerRadiusMM": "corner_radius_mm",
    "includedAngleDeg": "included_angle_deg", "tipDiaMM": "tip_dia_mm",
    "fluteCount": "flute_count", "fluteLengthMM": "flute_length_mm",
    "shankDiaMM": "shank_dia_mm", "overallLengthMM": "overall_length_mm",
    "neckLengthMM": "neck_length_mm", "chipDirection": "chip_direction",
    "substrate": "substrate", "coating": "coating", "notes": "notes",
    "chiploadMM": "chipload_mm", "spindleRPM": "spindle_rpm", "feedXY": "feed_xy",
    "feedZ": "feed_z", "stepdownMM": "stepdown_mm", "stepoverMM": "stepover_mm",
    "materialClassID": "material_class_id", "displayUnits": "display_units",
    "nominalLabel": "nominal_label",
}


def _draft_from(d) -> ToolDraft:
    return ToolDraft(**{py: d[sw] for sw, py in _DRAFT_KEYS.items()})


def _draft_to(d: ToolDraft) -> dict:
    out = {sw: getattr(d, py) for sw, py in _DRAFT_KEYS.items()}
    out["estimatedFields"] = sorted(d.estimated_fields)
    return out


def _close(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        return a is not None and b is not None and math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)
    return a == b


def _same_draft(got: dict, want: dict) -> list:
    return [k for k in want if k in got and not _close(got[k], want[k])]


def _issues(issues) -> list:
    return [(i.code, i.severity, i.field) for i in issues]


def _want_issues(issues) -> list:
    return [(i["code"], i["severity"], i["field"]) for i in issues]


@pytest.mark.parametrize("case", EXPECTED["validator"], ids=lambda c: c["input"]["name"] or "empty")
def test_validator_matches_2dcam(case):
    d = _draft_from(case["input"])
    issues = ToolValidator("mm", spindle_min_rpm=6000, spindle_max_rpm=24000).validate(d)
    assert _issues(issues) == _want_issues(case["issues"])
    assert _same_draft(_draft_to(d), case["repaired"]) == []


@pytest.mark.parametrize("case", EXPECTED["numbers"], ids=lambda c: repr(c["raw"]))
def test_value_parser_matches_2dcam(case):
    got = csvread.number(case["raw"], case["decimal"], case["strip"])
    assert _close(got, case["value"]), (got, case["value"])


def test_fractional_inch_labels_match_2dcam():
    for case in EXPECTED["labels"]:
        assert csvread.fractional_inch_label(case["mm"]) == case["label"], case


def _profile(case) -> CatalogProfile:
    if case["profileFile"]:
        return CatalogProfile.load(DATA / case["profileFile"])
    return builtin_profile(case["profile"])


def _compare_plan(got, want, name):
    assert got.headers == want["headers"], name
    assert (got.delimiter, got.decimal, got.encoding) == (
        want["delimiter"], want["decimal"], want["encoding"]), name
    assert {str(k): v for k, v in got.unmatched_columns.items()} == want["unmatched"], name
    assert len(got.rows) == len(want["rows"]), name
    for i, (g, w) in enumerate(zip(got.rows, want["rows"])):
        where = f"{name} row {i}"
        assert g.disposition == w["disposition"], where
        assert g.include == w["include"], where
        assert _same_draft(_draft_to(g.draft), w["draft"]) == [], where
        assert _issues(g.issues) == _want_issues(w["issues"]), where
        assert {k: list(v) for k, v in g.diff.items()} == w["diff"], where


def test_catalogue_imports_match_2dcam(tmp_path):
    """Plan → commit → plan again, in 2DCam's order on one library, then an
    edited sheet: every row classified, mapped and diffed as 2DCam does."""
    repo = ToolLibraryRepository(tmp_path / "lib.sqlite", seed_starter_tools=False)
    machine = _machine(EXPECTED["machines"]["default"])
    profiles = {}
    for case in EXPECTED["imports"]:
        profile = profiles[case["name"]] = _profile(case)
        data = (DATA / case["file"]).read_bytes()
        first = importer.plan(data, case["file"], profile, repo, machine=machine)
        _compare_plan(first, case["plan"], case["name"])
        result = importer.commit(first, repo)
        assert (result.inserted, result.updated, result.rejected) == (
            case["committed"]["inserted"], case["committed"]["updated"],
            case["committed"]["rejected"]), case["name"]
        _compare_plan(importer.plan(data, case["file"], profile, repo, machine=machine),
                      case["replan"], case["name"] + " (again)")
    edited = importer.plan((DATA / "amana_edited.csv").read_bytes(), "amana_edited.csv",
                           profiles["amana_inch"], repo, machine=machine)
    _compare_plan(edited, EXPECTED["editedAmana"], "amana edited")
