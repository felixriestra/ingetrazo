# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""SVG export: port of 2DCutList's testSVGContainsCutLabelsAtBothEndpoints."""
from __future__ import annotations

from core.cutlist import GuillotinePlanner
from core.cutlist_svg import render
from tests.test_cutlist_core import _sample_project


def test_svg_contains_cut_labels_at_both_endpoints():
    project = _sample_project()
    plan = GuillotinePlanner().generate(project)
    svg = render(project, plan)
    assert "A/B intersection is the production datum" in svg
    first = plan.sheets[0]
    if first.cuts:
        assert svg.count("C01") >= 2
