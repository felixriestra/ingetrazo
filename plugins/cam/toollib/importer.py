# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Vendor catalogue import: plan, review, commit — 2DCam's ``CSVImporter``.

Nothing reaches the library without being looked at. :func:`plan` reads the
file, maps its columns through the vendor's profile, validates every row
and compares it with what the library already holds, and returns an
:class:`ImportPlan`: each row is **new**, **changed** (with the differing
fields), **unchanged** or **rejected** (with the reasons). The UI shows the
plan; the user unticks what they do not want; :func:`commit` writes the
ticked rows in ONE transaction and records the batch, so
:meth:`.repository.ToolLibraryRepository.rollback_batch` can undo it whole.

A changed row updates geometry only, and never a field the user edited by
hand. A row that carries cutting data (Sorotec's sheets do) also becomes a
vendor preset for the material class it names.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from ..engine.models import new_id
from . import csvread
from .catalog import FIELDS, CatalogProfile, normalize
from .model import LibraryTool
from .resolver import CuttingPreset
from .validator import INFO, ToolDraft, ToolValidator, ValidationIssue, ValidationLimits

#: Draft fields read as lengths/feeds (converted to mm, mm/min) and as plain
#: numbers.
_LENGTHS = {"diameter", "cornerRadius", "tipDiameter", "fluteLength", "shankDiameter",
            "overallLength", "neckLength", "stepdown", "stepover", "chipload", "feedXY", "feedZ"}
_PLAIN = {"includedAngle", "fluteCount", "spindleRPM"}
_INTS = {"fluteCount", "spindleRPM"}


@dataclass
class ImportRow:
    """One catalogue row. ``disposition``: new | unchanged | changed |
    rejected | skipped. ``diff`` maps a column name to ``(old, new)``."""
    draft: ToolDraft
    issues: list
    disposition: str
    existing_tool_id: str | None = None
    diff: dict = field(default_factory=dict)
    include: bool = True

    @property
    def has_errors(self) -> bool:
        return any(i.is_error for i in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(i.severity == "warning" for i in self.issues)


@dataclass
class ImportPlan:
    batch_id: str
    profile: CatalogProfile
    file_name: str
    sha256: str
    headers: list
    delimiter: str
    decimal: str
    encoding: str
    #: Columns the profile has no rule for — offered, never silently dropped.
    unmatched_columns: dict
    rows: list

    @property
    def new_count(self) -> int:
        return sum(1 for r in self.rows if r.include and r.disposition == "new")

    @property
    def changed_count(self) -> int:
        return sum(1 for r in self.rows if r.include and r.disposition == "changed")

    @property
    def rejected_count(self) -> int:
        return sum(1 for r in self.rows if r.disposition == "rejected")

    @property
    def warning_count(self) -> int:
        return sum(1 for r in self.rows if r.has_warnings)


@dataclass
class ImportResult:
    batch_id: str
    inserted: int
    updated: int
    rejected: int


def plan(data: bytes, file_name: str, profile: CatalogProfile, repository,
         limits: ValidationLimits | None = None, machine=None) -> ImportPlan:
    """Read, map, validate and compare — writes nothing. ``machine`` (a
    :class:`.resolver.MachineLimits`) adds spindle-range warnings."""
    table = csvread.parse(data, profile)
    mapping, unmatched = profile.map_headers(table.headers)
    validator = ToolValidator(profile.native_units, limits,
                              machine.min_rpm if machine else None,
                              machine.max_rpm if machine else None)
    rows = []
    for index, cells in enumerate(table.rows):
        if not any(c for c in cells):
            continue
        draft = make_draft(index, cells, table.headers, mapping, table.decimal, profile)
        issues = validator.validate(draft)
        disposition, existing_id, diff = "new", None, {}
        if any(i.is_error for i in issues):
            disposition = "rejected"
        elif draft.product_id:
            existing = repository.tool_by_product(profile.vendor_id, draft.product_id)
            if existing is not None:
                existing_id = existing.id
                diff = diff_tool(existing, draft)
                disposition = "changed" if diff else "unchanged"
                if diff:
                    issues.append(ValidationIssue(index, INFO, "EXISTS_CHANGED", None,
                                                  {"count": len(diff)}))
        rows.append(ImportRow(draft, issues, disposition, existing_id, diff,
                              include=disposition not in ("rejected", "unchanged")))
    return ImportPlan(new_id(), profile, file_name, hashlib.sha256(data).hexdigest(),
                      table.headers, table.delimiter, table.decimal, table.encoding,
                      unmatched, rows)


def make_draft(row_index: int, cells, headers, mapping, decimal: str,
               profile: CatalogProfile) -> ToolDraft:
    """One row → a :class:`ToolDraft` in canonical units."""
    d = ToolDraft(row_index)
    raw = {}

    def text(rule, value):
        v = value.strip()
        for token in rule.strip or ():
            v = _replace_ci(v, token, "")
        v = v.strip()
        if rule.map:
            v = rule.map.get(v, rule.map.get(v.lower(), v))
        return v or None

    for i, cell in enumerate(cells):
        if i < len(headers):
            raw[headers[i]] = cell
        rule = mapping.get(i)
        if rule is None or not cell.strip():
            continue
        attr = FIELDS[rule.field]
        if rule.field in _LENGTHS:
            v = csvread.number(cell, decimal, rule.strip)
            value = (csvread.to_canonical(v, rule.unit, profile.native_units)
                     if v is not None else None)
        elif rule.field in _PLAIN:
            value = csvread.number(cell, decimal, rule.strip)
            if value is not None and rule.field in _INTS:
                value = int(value)
        elif rule.field == "toolType":
            v = text(rule, cell)
            value = v if v in _TOOL_TYPES else None
        else:
            value = text(rule, cell)
            if rule.field == "name":
                value = value or ""
        setattr(d, attr, value)

    # Catalogue-wide constants: what a series states once, not on every row.
    for key, value in (profile.constants or {}).items():
        if key not in _CONSTANT_FIELDS:
            continue
        attr = FIELDS[key]
        if getattr(d, attr) is not None:
            continue
        if key == "toolType":
            value = value if value in _TOOL_TYPES else None
        elif key == "fluteCount":
            try:
                value = int(value)
            except ValueError:
                value = None
        setattr(d, attr, value)

    # No type column? Infer it from the description — the usual case for CMT
    # and Sorotec — and mark it estimated so the review can show it.
    if d.tool_type is None and profile.type_keywords:
        hay = normalize(d.name)
        for rule in profile.type_keywords:
            hit = any(normalize(word) in hay for word in rule.contains)
            angle_ok = not rule.requires_angle or d.included_angle_deg is not None
            if hit and angle_ok and rule.tool_type in _TOOL_TYPES:
                d.tool_type = rule.tool_type
                d.estimated_fields.add("tool_type")
                break

    # Imperial catalogues keep the vendor's own wording for display.
    if profile.native_units in ("inch", "frac_inch"):
        d.display_units = "frac_inch"
        if d.diameter_mm is not None:
            d.nominal_label = csvread.fractional_inch_label(d.diameter_mm)

    if not d.name and d.product_id:
        d.name = d.product_id
    d.raw_json = json.dumps(raw, ensure_ascii=False)
    return d


def _replace_ci(s: str, token: str, repl: str) -> str:
    return re.sub(re.escape(token), repl, s, flags=re.IGNORECASE)


_TOOL_TYPES = ("end_mill", "ball_nose", "bull_nose", "v_bit", "engraver", "tapered_ball",
               "drill", "chamfer", "surfacing", "form", "drag_knife")
_CONSTANT_FIELDS = ("toolType", "substrate", "chipDirection", "series", "materialClass",
                    "fluteCount", "coating")


def diff_tool(existing: LibraryTool, d: ToolDraft) -> dict:
    """``{column: (old, new)}`` for the geometry a re-import would change.
    A value the vendor added (old None) or dropped (new None) counts."""
    out = {}

    def fmt(v):
        return f"{v:g}"

    def num(key, a, b, tolerance=0.005):
        if a is not None and b is not None:
            if abs(a - b) > tolerance:
                out[key] = (fmt(a), fmt(b))
        elif a is None and b is not None:
            out[key] = (None, fmt(b))
        elif a is not None and b is None:
            out[key] = (fmt(a), None)

    num("diameter_mm", existing.diameter_mm, d.diameter_mm)
    num("flute_length_mm", existing.flute_length_mm, d.flute_length_mm)
    num("shank_dia_mm", existing.shank_dia_mm, d.shank_dia_mm)
    num("overall_length_mm", existing.overall_length_mm, d.overall_length_mm)
    num("corner_radius_mm", existing.corner_radius_mm, d.corner_radius_mm)
    num("included_angle_deg", existing.included_angle_deg, d.included_angle_deg)
    if existing.flute_count != d.flute_count:
        out["flute_count"] = (None if existing.flute_count is None else str(existing.flute_count),
                              None if d.flute_count is None else str(d.flute_count))
    if d.tool_type is not None and existing.type != d.tool_type:
        out["tool_type"] = (existing.type, d.tool_type)
    if existing.name != d.name:
        out["name"] = (existing.name, d.name)
    return out


#: Diff column → the :class:`LibraryTool` attribute a changed row writes.
_APPLY = {"diameter_mm": "diameter_mm", "flute_length_mm": "flute_length_mm",
          "shank_dia_mm": "shank_dia_mm", "overall_length_mm": "overall_length_mm",
          "corner_radius_mm": "corner_radius_mm", "included_angle_deg": "included_angle_deg",
          "flute_count": "flute_count", "name": "name"}


def commit(plan_: ImportPlan, repository, group_id: str | None = None) -> ImportResult:
    """Write the ticked rows, all or nothing, and record the batch."""
    profile = plan_.profile
    inserted = updated = rejected = 0
    with repository.transaction():
        source_id = repository.upsert_data_source(
            profile.vendor_id, "csv", f"{profile.vendor_name} — {plan_.file_name}",
            plan_.sha256, 4)
        repository.create_batch(plan_.batch_id, source_id, len(plan_.rows))
        for row in plan_.rows:
            d = row.draft
            tool_id = None
            if row.include and not row.has_errors:
                if row.disposition == "new":
                    tool = LibraryTool(
                        name=d.name, type=d.tool_type or "end_mill", vendor_id=profile.vendor_id,
                        vendor_name=profile.vendor_name, product_id=d.product_id,
                        series=d.series, diameter_mm=d.diameter_mm,
                        corner_radius_mm=d.corner_radius_mm,
                        included_angle_deg=d.included_angle_deg, tip_dia_mm=d.tip_dia_mm,
                        flute_count=d.flute_count, flute_length_mm=d.flute_length_mm,
                        shank_dia_mm=d.shank_dia_mm, overall_length_mm=d.overall_length_mm,
                        neck_length_mm=d.neck_length_mm, chip_direction=d.chip_direction,
                        substrate=d.substrate, coating=d.coating,
                        display_units=d.display_units, nominal_label=d.nominal_label,
                        notes=d.notes)
                    repository.insert(tool, source_id, plan_.batch_id)
                    tool_id = tool.id
                    for fld in sorted(d.estimated_fields):
                        repository.record_provenance("tool", tool.id, fld, "estimated",
                                                     source_id)
                    if group_id:
                        repository.add_to_group(tool.id, group_id)
                    # Cutting data, when the catalogue carried it.
                    if d.feed_xy is not None and d.spindle_rpm is not None:
                        fz = d.chipload_mm
                        if fz is None and d.flute_count:
                            fz = d.feed_xy / (d.spindle_rpm * d.flute_count)
                        repository.upsert_preset(CuttingPreset(
                            tool_id=tool.id, material_class_id=d.material_class_id,
                            name=f"{d.material_class_id or 'Vendor'} ({profile.vendor_name})",
                            origin="vendor", derivation="from_chipload", confidence=4,
                            chipload_mm=fz, spindle_rpm=d.spindle_rpm, feed_xy_mm_min=d.feed_xy,
                            feed_z_mm_min=d.feed_z, stepdown_mm=d.stepdown_mm,
                            stepover_mm=d.stepover_mm), source_id, plan_.batch_id)
                    inserted += 1
                elif row.disposition == "changed" and row.existing_tool_id:
                    existing = repository.tool(row.existing_tool_id)
                    if existing is not None and not existing.deleted_at:
                        protected = repository.user_edited_fields("tool", existing.id)
                        for column in row.diff:
                            if column in protected:
                                continue
                            if column == "tool_type":
                                if d.tool_type:
                                    existing.type = d.tool_type
                            elif column in _APPLY:
                                setattr(existing, _APPLY[column], getattr(d, _APPLY[column]))
                        repository.update(existing)
                        tool_id = existing.id
                        updated += 1
            elif row.has_errors:
                rejected += 1
            repository.stage(plan_.batch_id, d.row_index, d.raw_json, row.disposition, tool_id)
            for issue in row.issues:
                repository.record_issue(issue, plan_.batch_id)
        repository.finish_batch(plan_.batch_id, "committed", inserted + updated)
    try:
        repository.rotating_backup()
    except Exception:  # noqa: BLE001 — a failed backup never undoes a good import
        pass
    return ImportResult(plan_.batch_id, inserted, updated, rejected)
