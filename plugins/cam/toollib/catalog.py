# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Catalogue profiles: how one vendor's spreadsheet maps onto a tool —
2DCam's ``CatalogProfile`` and ``BuiltinCatalogProfiles``.

A profile is data, not code: a JSON file naming, for each tool field, the
header spellings the vendor has used (``Schaft-Ø [mm]``, ``Schaft``,
``d``…), the unit of the column, tokens to strip (``Z2`` → ``2``) and words
to map (``VHM`` → ``solid_carbide``). Values a sheet states once in its
preamble (a whole sheet of Z3 solid carbide up-cut bits) are ``constants``;
a sheet with no type column gets the type from keywords in the
description (``typeKeywords``), marked as estimated.

The JSON keys are 2DCam's (camelCase), so a profile written for one app
works in the other. The built-in profiles live in ``catalogs/``.
"""
from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

#: Tool-draft fields a column can feed (2DCam's ``ToolField``) → the
#: :class:`.validator.ToolDraft` attribute.
FIELDS = {
    "productID": "product_id", "name": "name", "series": "series", "toolType": "tool_type",
    "diameter": "diameter_mm", "cornerRadius": "corner_radius_mm",
    "includedAngle": "included_angle_deg", "tipDiameter": "tip_dia_mm",
    "fluteCount": "flute_count", "fluteLength": "flute_length_mm",
    "shankDiameter": "shank_dia_mm", "overallLength": "overall_length_mm",
    "neckLength": "neck_length_mm", "chipDirection": "chip_direction",
    "substrate": "substrate", "coating": "coating", "notes": "notes",
    "chipload": "chipload_mm", "spindleRPM": "spindle_rpm", "feedXY": "feed_xy",
    "feedZ": "feed_z", "stepdown": "stepdown_mm", "stepover": "stepover_mm",
    "materialClass": "material_class_id",
}

#: Column units (2DCam's ``ColumnUnit``); ``auto`` takes the vendor's own.
UNITS = ("mm", "cm", "inch", "frac_inch", "degrees", "count", "rpm", "mm_min", "m_min",
         "mm_tooth", "text", "auto")

BUILTIN_DIR = Path(__file__).with_name("catalogs")
#: The order the import menu lists the built-in profiles in.
BUILTIN_ORDER = ("sorotec-cnc-2026", "cmt-industrial-2026", "cmt-193-helical")


class ProfileError(Exception):
    """A profile file that is not valid JSON or misses a required key."""


@dataclass
class ColumnRule:
    headers: list
    field: str
    unit: str = "auto"
    strip: list | None = None
    map: dict | None = None
    required: bool = False


@dataclass
class TypeKeywordRule:
    contains: list
    tool_type: str
    requires_angle: bool = False


@dataclass
class CatalogProfile:
    id: str
    vendor_id: str
    vendor_name: str
    native_units: str
    columns: list
    header_row: int = 0
    catalog_name: str | None = None
    delimiter: str | None = None
    decimal_separator: str | None = None
    encoding: str | None = None
    skip_rows_matching: list = field(default_factory=list)
    constants: dict = field(default_factory=dict)
    type_keywords: list = field(default_factory=list)
    note: str | None = None

    @property
    def menu_title(self) -> str:
        """Distinct per sheet, so a vendor with several catalogues does not
        show as a row of identical entries."""
        return self.catalog_name or self.vendor_name

    @classmethod
    def from_json(cls, data) -> "CatalogProfile":
        """From 2DCam's JSON (a string or an already parsed dict)."""
        try:
            d = json.loads(data) if isinstance(data, (str, bytes)) else dict(data)
            columns = []
            for c in d["columns"]:
                if c["field"] not in FIELDS:
                    raise ProfileError(f"unknown field {c['field']!r}")
                columns.append(ColumnRule(list(c["headers"]), c["field"], c.get("unit") or "auto",
                                          c.get("strip"), c.get("map"),
                                          bool(c.get("required") or False)))
            keywords = [TypeKeywordRule(list(k["contains"]), k["toolType"],
                                        bool(k.get("requiresAngle") or False))
                        for k in d.get("typeKeywords") or []]
            return cls(id=d["id"], vendor_id=d["vendorID"], vendor_name=d["vendorName"],
                       native_units=d["nativeUnits"], columns=columns,
                       header_row=int(d.get("headerRow", 0)), catalog_name=d.get("catalogName"),
                       delimiter=d.get("delimiter"), decimal_separator=d.get("decimalSeparator"),
                       encoding=d.get("encoding"),
                       skip_rows_matching=list(d.get("skipRowsMatching") or []),
                       constants=dict(d.get("constants") or {}), type_keywords=keywords,
                       note=d.get("note"))
        except ProfileError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ProfileError(str(exc)) from exc

    @classmethod
    def load(cls, path) -> "CatalogProfile":
        return cls.from_json(Path(path).read_text(encoding="utf-8"))

    def map_headers(self, headers) -> tuple:
        """``(mapping, unmatched)``: column index → rule, and the headers no
        rule claimed — returned rather than dropped, so a review can offer
        them. Each field is claimed by its first matching column only."""
        mapping, unmatched, used = {}, {}, set()
        for i, raw in enumerate(headers):
            key = normalize(raw)
            if not key:
                continue
            rule = next((r for r in self.columns
                         if r.field not in used and any(normalize(h) == key for h in r.headers)),
                        None)
            if rule is None:
                unmatched[i] = raw
            else:
                mapping[i] = rule
                used.add(rule.field)
        return mapping, unmatched


def normalize(s: str) -> str:
    """Lower-case, no diacritics, no punctuation or spaces: ``Schaft-Ø [mm]``,
    ``Schaft Ø`` and ``schaftø`` are one key."""
    folded = unicodedata.normalize("NFKD", s.casefold())
    return "".join(c for c in folded if c.isalnum() and not unicodedata.combining(c))


def builtin_profiles() -> list:
    """The profiles shipped with the plugin, in menu order."""
    return [CatalogProfile.load(BUILTIN_DIR / f"{pid}.json") for pid in BUILTIN_ORDER]


def builtin_profile(profile_id: str) -> CatalogProfile | None:
    return next((p for p in builtin_profiles() if p.id == profile_id), None)
