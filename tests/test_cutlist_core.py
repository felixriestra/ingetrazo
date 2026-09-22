# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""GuillotinePlanner: ported from 2DCutList's CutListCoreTests.swift.

Pure Python, no Qt — the planner (core/cutlist.py) never imports PySide6.
Only the subset relevant to IngeTrazo's v1 (no CSV import, no manual
recalculate — see core/cutlist.py's module docstring) is ported here.
"""
from __future__ import annotations

import pytest

from core.cutlist import (
    CutAxis,
    CutListError,
    CutListProject,
    CuttingNode,
    GrainAxis,
    GrainRule,
    GuillotinePlanner,
    ImportedPart,
    OrderedCut,
    Placement,
    PartInstance,
    PartSettings,
    PlanMetrics,
    Rect2D,
    RemainderLeaf,
    SheetPlan,
    SplitSpec,
    StockDefinition,
)


def _sample_project() -> CutListProject:
    """Port of ``CutListProject.sample`` (Models.swift)."""
    parts = [
        ImportedPart(id="SIDE", description="Cabinet side", length=2100, width=560, thickness=18, quantity=2),
        ImportedPart(id="SHELF", description="Adjustable shelf", length=760, width=520, thickness=18, quantity=4),
        ImportedPart(id="PLINTH", description="Plinth rail", length=760, width=100, thickness=18, quantity=2),
    ]
    settings = {p.id: PartSettings(material="Birch plywood", grain_axis=GrainAxis.LENGTH,
                                    grain_rule=GrainRule.REQUIRED, allow_rotation=True)
                for p in parts}
    return CutListProject(
        name="Sample Cabinet", parts=parts, part_settings=settings,
        stocks=[StockDefinition(name="Birch plywood 18 mm", material="Birch plywood",
                                 thickness=18, length=2500, width=1250, grain_axis=GrainAxis.LENGTH)])


def test_identical_parts_share_one_band_trim_instead_of_one_per_part():
    project = _sample_project()
    project.parts = [ImportedPart(id="S", description="Side", length=482, width=309, thickness=18, quantity=4)]
    project.part_settings = {"S": PartSettings(material="Birch plywood")}
    plan = GuillotinePlanner().generate(project)
    sheet = plan.sheets[0]
    assert plan.metrics.sheet_count == 1
    assert len(sheet.placements) == 4

    # Every part sits on the same band line.
    assert len({round(p.rect.y) for p in sheet.placements}) == 1
    # The offcut above the band spans the full usable width.
    assert any(abs(r.width - sheet.usable_rect.width) < 0.001 for r in sheet.remnants)
    # No leftover strip is trapped between two parts at band height.
    trapped = [r for r in sheet.remnants
               if r.height < 309 and r.width < sheet.usable_rect.width - 0.001]
    assert not trapped, f"unexpected captured trim: {trapped}"
    GuillotinePlanner().validate(plan)  # must not raise


def test_sheet_labels_are_unique_across_materials():
    project = _sample_project()
    project.stocks = [
        StockDefinition(name="New stock", material="Ply", thickness=18, length=2500, width=1250),
        StockDefinition(name="New stock", material="MDF", thickness=10, length=2500, width=1250),
    ]
    project.parts = [
        ImportedPart(id="A", description="Ply panel", length=800, width=600, thickness=18, quantity=1),
        ImportedPart(id="B", description="MDF panel", length=800, width=600, thickness=10, quantity=1),
    ]
    project.part_settings = {"A": PartSettings(material="Ply"), "B": PartSettings(material="MDF")}
    plan = GuillotinePlanner().generate(project)
    assert len(plan.sheets) == 2
    # Both sheets are number 1 for their own stock, so the old label collided.
    assert len({s.sheet_number for s in plan.sheets}) == 1
    labels = [plan.label(s) for s in plan.sheets]
    assert len(set(labels)) == 2, f"labels collided: {labels}"
    assert any("18 mm" in label for label in labels)
    assert any("10 mm" in label for label in labels)


def test_planner_opens_as_many_sheets_as_needed_by_default():
    project = _sample_project()
    project.parts = [ImportedPart(id="P", description="Panel", length=1200, width=1200, thickness=18, quantity=5)]
    project.part_settings = {"P": PartSettings(material="Birch plywood")}
    assert project.stocks[0].available_sheets is None
    plan = GuillotinePlanner().generate(project)
    assert plan.metrics.sheet_count > 1
    assert sum(len(s.placements) for s in plan.sheets) == 5


def test_sheet_limit_failure_names_the_setting_to_change():
    project = _sample_project()
    project.stocks[0].available_sheets = 1
    project.parts = [ImportedPart(id="P", description="Panel", length=1200, width=1200, thickness=18, quantity=5)]
    project.part_settings = {"P": PartSettings(material="Birch plywood")}
    with pytest.raises(CutListError) as exc_info:
        GuillotinePlanner().generate(project)
    message = str(exc_info.value)
    assert "Not enough stock" in message
    assert "Stock & Setup" in message


def test_oversized_part_is_reported_separately_from_stock_shortage():
    project = _sample_project()
    project.parts = [ImportedPart(id="HUGE", description="Slab", length=3000, width=1400, thickness=18, quantity=1)]
    project.part_settings = {"HUGE": PartSettings(material="Birch plywood")}
    with pytest.raises(CutListError) as exc_info:
        GuillotinePlanner().generate(project)
    message = str(exc_info.value)
    assert "Part too large" in message
    assert "HUGE" in message


def test_sample_plan_is_valid_and_uses_configurable_stock():
    project = _sample_project()
    project.stocks[0].length = 2400
    project.stocks[0].width = 1200
    planner = GuillotinePlanner()
    plan = planner.generate(project)
    assert plan.sheets
    assert sum(len(s.placements) for s in plan.sheets) == 8
    assert all(abs(s.usable_rect.width - 2397) < 0.001 for s in plan.sheets)
    assert all(abs(s.usable_rect.height - 1190) < 0.001 for s in plan.sheets)
    planner.validate(plan)  # must not raise


def test_required_grain_restricts_rotation():
    project = _sample_project()
    project.parts = [ImportedPart(id="G", description="Grained", length=1000, width=300, thickness=18, quantity=1)]
    project.part_settings = {"G": PartSettings(material="Birch plywood", grain_axis=GrainAxis.LENGTH,
                                                grain_rule=GrainRule.REQUIRED, allow_rotation=True)}
    project.stocks[0].grain_axis = GrainAxis.LENGTH
    plan = GuillotinePlanner().generate(project)
    assert plan.sheets[0].placements[0].rotated is False


def test_validator_rejects_cut_crossing_part_box():
    stock = StockDefinition(name="Test", material="MDF", thickness=18, length=1000, width=500)
    instance = PartInstance(id="P", source_part_id="P", description="Panel", length=600, width=300,
                             thickness=18, settings=PartSettings(material="MDF"))
    placement = Placement(instance=instance, rect=Rect2D(x=0, y=0, width=600, height=300), rotated=False)
    workpiece = Rect2D(x=0, y=0, width=1000, height=500)
    split = SplitSpec(axis=CutAxis.VERTICAL, coordinate=400, kerf=3, workpiece=workpiece)
    # The remainder starts at x=403 (past the 3mm kerf) but the cut at x=400
    # still crosses the placement's own box (x=0..600) — invalid geometry.
    root = CuttingNode.split_node(
        split, CuttingNode.part(placement),
        CuttingNode.remainder(RemainderLeaf(rect=Rect2D(x=403, y=0, width=597, height=500))))
    sheet = SheetPlan(stock=stock, sheet_number=1, usable_rect=workpiece, root=root,
                       placements=[placement], remnants=[], cuts=[OrderedCut(number=1, split=split)])
    metrics = PlanMetrics(sheet_count=1, cut_count=1, part_area=placement.rect.area,
                           usable_area=workpiece.area, reusable_remnant_area=0,
                           scrap_area=0, largest_remnant_area=0)
    with pytest.raises(CutListError):
        from core.cutlist import CuttingPlan
        GuillotinePlanner().validate(CuttingPlan(sheets=[sheet], metrics=metrics))
