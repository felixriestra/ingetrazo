# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The checks a catalogue row must pass — 2DCam's ``ToolValidator``.

A vendor spreadsheet is typed by hand, by several people, in several
units. The checks here catch the slips that matter: an inch value in a
metric column, a cutting diameter six times the shank, a chipload that only
makes sense if the feed column is in m/min. An ``error`` rejects the row; a
``warning`` lets it through with a mark; some rows are repaired in place (a
ball nose's radius is D/2, whatever the sheet says) and the repaired field
is recorded as *estimated*, never passed off as the vendor's.

Codes are 2DCam's, upper case, because the ``validation_issue`` table of a
shared library file stores them; ``ui/messages.py`` has a sentence for
each (:data:`ISSUE_CODES`).
"""
from __future__ import annotations

# ``field`` is also an attribute of ValidationIssue (2DCam's name), which would
# shadow the function inside that class body.
from dataclasses import dataclass
from dataclasses import field as _field

ERROR, WARNING, INFO = "error", "warning", "info"

#: Every code the validator and the importer produce.
ISSUE_CODES = (
    "MISSING_NAME", "MISSING_TYPE", "MISSING_DIAMETER", "DIA_OUT_OF_RANGE", "DIA_LARGE",
    "UNIT_MISLABEL", "SHANK_NONSTANDARD", "DIA_VS_SHANK", "FLUTES_RANGE",
    "FLUTES_HIGH", "FLUTE_LEN_GT_OAL", "FLUTE_LEN_RATIO", "BALL_RADIUS_MISMATCH",
    "MISSING_CORNER_RADIUS", "CORNER_RADIUS_RANGE", "MISSING_ANGLE", "ANGLE_RANGE",
    "ANGLE_UNUSUAL", "VBIT_HAS_FLAT", "FORM_NO_PROFILE", "FLAT_HAS_RADIUS", "RPM_RANGE",
    "RPM_BELOW_SPINDLE", "RPM_ABOVE_SPINDLE", "FEED_RANGE", "PLUNGE_GT_FEED", "PLUNGE_HIGH",
    "PLUNGE_LOW", "CHIPLOAD_RANGE", "CHIPLOAD_INCONSISTENT", "STEPDOWN_RANGE",
    "STEPDOWN_GT_FLUTE_LEN", "STEPDOWN_DEEP", "STEPOVER_RANGE", "EXISTS_CHANGED",
)


@dataclass
class ToolDraft:
    """A candidate tool from one catalogue row, in canonical units (mm,
    mm/min), not yet in the library."""
    row_index: int
    name: str = ""
    product_id: str | None = None
    series: str | None = None
    tool_type: str | None = None

    diameter_mm: float | None = None
    corner_radius_mm: float | None = None
    included_angle_deg: float | None = None
    tip_dia_mm: float | None = None
    flute_count: int | None = None
    flute_length_mm: float | None = None
    shank_dia_mm: float | None = None
    overall_length_mm: float | None = None
    neck_length_mm: float | None = None

    chip_direction: str | None = None
    substrate: str | None = None
    coating: str | None = None
    notes: str | None = None

    # Cutting data, only when the catalogue actually carries it.
    chipload_mm: float | None = None
    spindle_rpm: int | None = None
    feed_xy: float | None = None
    feed_z: float | None = None
    stepdown_mm: float | None = None
    stepover_mm: float | None = None
    material_class_id: str | None = None

    display_units: str = "mm"
    nominal_label: str | None = None
    #: Columns we filled in ourselves → ``field_provenance`` on commit.
    estimated_fields: set = _field(default_factory=set)
    raw_json: str = "{}"


@dataclass
class ValidationIssue:
    """One finding on one row. ``field`` names the column the way 2DCam does
    (``fluteCount``, ``shankDiameter``…: it is stored in a shared library
    file); ``params`` holds the numbers the message needs."""
    row_index: int
    severity: str
    code: str
    field: str | None = None
    params: dict = _field(default_factory=dict)
    raw_value: str | None = None

    @property
    def is_error(self) -> bool:
        return self.severity == ERROR


@dataclass
class ValidationLimits:
    """The bounds, 2DCam's defaults. A shank outside the standard sizes only
    warns: odd collets exist."""
    diameter_min: float = 0.4
    diameter_max: float = 120.0
    diameter_warn_max: float = 65.0
    shank_standards_mm: tuple = (3.0, 3.175, 4.0, 6.0, 6.35, 8.0, 9.525, 10.0, 12.0, 12.7,
                                 14.0, 16.0, 18.0, 20.0, 25.0, 25.4)
    shank_tolerance: float = 0.05
    dia_over_shank_error: float = 6.0
    dia_over_shank_warn: float = 2.0
    flute_min: int = 1
    flute_max: int = 8
    flute_warn_max: int = 4
    flute_length_over_dia_warn: float = 12.0
    angle_min: float = 5.0
    angle_max: float = 175.0
    common_angles: tuple = (15, 20, 30, 45, 60, 90, 120, 140, 150)
    rpm_min: int = 3000
    rpm_max: int = 40000
    feed_min: float = 50.0
    feed_max: float = 20000.0
    plunge_ratio_warn_high: float = 0.6
    plunge_ratio_warn_low: float = 0.15
    chipload_min: float = 0.005
    chipload_max: float = 0.8
    stepdown_over_dia_warn: float = 1.5
    #: A metric diameter below this that equals a common inch size is a
    #: mislabelled column, not a micro-cutter.
    unit_mislabel_dia_threshold: float = 1.5
    inch_like_values: tuple = (0.0625, 0.125, 0.25, 0.375, 0.5, 0.75, 1.0)


class ToolValidator:
    """Validates a :class:`ToolDraft` and repairs it in place. The caller
    rejects the row if any returned issue is an error."""

    def __init__(self, native_units: str = "mm", limits: ValidationLimits | None = None,
                 spindle_min_rpm: int | None = None, spindle_max_rpm: int | None = None):
        self.limits = limits or ValidationLimits()
        self.native_units = native_units
        self.spindle_min_rpm = spindle_min_rpm
        self.spindle_max_rpm = spindle_max_rpm

    def validate(self, d: ToolDraft) -> list:
        L = self.limits
        issues = []

        def err(code, fld, **params):
            issues.append(ValidationIssue(d.row_index, ERROR, code, fld, params))

        def warn(code, fld, **params):
            issues.append(ValidationIssue(d.row_index, WARNING, code, fld, params))

        # --- structurally required ------------------------------------------
        if not d.name.strip():
            err("MISSING_NAME", "name")
        t = d.tool_type
        if t is None:
            err("MISSING_TYPE", "toolType")
            return issues
        dia = d.diameter_mm
        if dia is None:
            err("MISSING_DIAMETER", "diameter")
            return issues

        # --- diameter ---------------------------------------------------------
        if dia <= 0 or dia > L.diameter_max or dia < L.diameter_min:
            err("DIA_OUT_OF_RANGE", "diameter", diameter=dia, min=L.diameter_min,
                max=L.diameter_max)
        elif dia > L.diameter_warn_max and t != "surfacing":
            warn("DIA_LARGE", "diameter", diameter=dia, type=t)
        # The single most valuable check: an inch value in a metric column.
        if (self.native_units == "mm" and dia < L.unit_mislabel_dia_threshold
                and any(abs(v - dia) < 0.001 for v in L.inch_like_values)):
            err("UNIT_MISLABEL", "diameter", diameter=dia, as_mm=dia * 25.4)

        # --- shank ------------------------------------------------------------
        shank = d.shank_dia_mm
        if shank is not None:
            if not any(abs(s - shank) <= L.shank_tolerance for s in L.shank_standards_mm):
                warn("SHANK_NONSTANDARD", "shankDiameter", shank=shank)
            if dia > shank * L.dia_over_shank_error:
                err("DIA_VS_SHANK", "diameter", diameter=dia, shank=shank,
                    factor=L.dia_over_shank_error)
            elif (dia > shank * L.dia_over_shank_warn
                  and t not in ("v_bit", "surfacing", "chamfer", "form")):
                # Same code as the error, as 2DCam stores it; the severity tells
                # the message apart.
                warn("DIA_VS_SHANK", "diameter", diameter=dia, shank=shank)

        # --- flutes -----------------------------------------------------------
        z = d.flute_count
        if z is not None:
            if z < L.flute_min or z > L.flute_max:
                err("FLUTES_RANGE", "fluteCount", flutes=z, min=L.flute_min, max=L.flute_max)
            elif z > L.flute_warn_max:
                warn("FLUTES_HIGH", "fluteCount", flutes=z)

        # --- lengths ----------------------------------------------------------
        flute = d.flute_length_mm
        if flute is not None:
            oal = d.overall_length_mm
            if oal is not None and flute > oal:
                err("FLUTE_LEN_GT_OAL", "fluteLength", flute=flute, overall=oal)
            if flute > dia * L.flute_length_over_dia_warn:
                warn("FLUTE_LEN_RATIO", "fluteLength", ratio=flute / dia)

        # --- shape-specific ---------------------------------------------------
        if t == "ball_nose":
            r = dia / 2
            if d.corner_radius_mm is None:
                d.corner_radius_mm = r
                d.estimated_fields.add("corner_radius_mm")
            elif abs(d.corner_radius_mm - r) > 0.01:
                warn("BALL_RADIUS_MISMATCH", "cornerRadius", radius=d.corner_radius_mm, half=r)
                d.corner_radius_mm = r
                d.estimated_fields.add("corner_radius_mm")
        elif t == "bull_nose":
            cr = d.corner_radius_mm
            if cr is None:
                err("MISSING_CORNER_RADIUS", "cornerRadius", type=t)
            elif cr <= 0 or cr > dia / 2 + 0.001:
                err("CORNER_RADIUS_RANGE", "cornerRadius", radius=cr, half=dia / 2)
        elif t in ("v_bit", "engraver", "chamfer", "drill"):
            angle = d.included_angle_deg
            if angle is None:
                if t in ("v_bit", "engraver"):
                    err("MISSING_ANGLE", "includedAngle", type=t)
            else:
                if angle < L.angle_min or angle > L.angle_max:
                    err("ANGLE_RANGE", "includedAngle", angle=angle, min=L.angle_min,
                        max=L.angle_max)
                elif not any(abs(a - angle) < 0.5 for a in L.common_angles):
                    warn("ANGLE_UNUSUAL", "includedAngle", angle=angle)
                if t == "v_bit":
                    tip = d.tip_dia_mm
                    if tip is not None and tip > 0.05:
                        warn("VBIT_HAS_FLAT", "tipDiameter", tip=tip)
                    elif tip is None:
                        d.tip_dia_mm = 0.0
                        d.estimated_fields.add("tip_dia_mm")
        elif t == "tapered_ball":
            if d.included_angle_deg is None:
                err("MISSING_ANGLE", "includedAngle", type=t)
            if d.corner_radius_mm is None:
                err("MISSING_CORNER_RADIUS", "cornerRadius", type=t)
        elif t == "form":
            warn("FORM_NO_PROFILE", None)
        elif t in ("end_mill", "surfacing", "drag_knife"):
            if d.corner_radius_mm is not None and d.corner_radius_mm > 0.001:
                warn("FLAT_HAS_RADIUS", "cornerRadius", radius=d.corner_radius_mm)

        # --- cutting data, when the catalogue supplied any ---------------------
        rpm = d.spindle_rpm
        if rpm is not None:
            if rpm < L.rpm_min or rpm > L.rpm_max:
                err("RPM_RANGE", "spindleRPM", rpm=rpm, min=L.rpm_min, max=L.rpm_max)
            if self.spindle_min_rpm is not None and rpm < self.spindle_min_rpm:
                warn("RPM_BELOW_SPINDLE", "spindleRPM", rpm=rpm, limit=self.spindle_min_rpm)
            if self.spindle_max_rpm is not None and rpm > self.spindle_max_rpm:
                warn("RPM_ABOVE_SPINDLE", "spindleRPM", rpm=rpm, limit=self.spindle_max_rpm)
        feed = d.feed_xy
        if feed is not None and (feed < L.feed_min or feed > L.feed_max):
            err("FEED_RANGE", "feedXY", feed=feed, min=L.feed_min, max=L.feed_max)
        plunge = d.feed_z
        if plunge is not None and feed is not None:
            if plunge > feed:
                err("PLUNGE_GT_FEED", "feedZ", plunge=plunge, feed=feed)
            elif plunge > feed * L.plunge_ratio_warn_high:
                warn("PLUNGE_HIGH", "feedZ", percent=round(plunge / feed * 100))
            elif plunge < feed * L.plunge_ratio_warn_low:
                warn("PLUNGE_LOW", "feedZ", percent=round(plunge / feed * 100))

        # Chipload, stated or implied: catches most transcription slips.
        implied = None
        if feed is not None and rpm and z:
            implied = feed / (rpm * z)
        fz = d.chipload_mm if d.chipload_mm is not None else implied
        if fz is not None:
            if fz < L.chipload_min or fz > L.chipload_max:
                err("CHIPLOAD_RANGE", "chipload", chipload=fz, min=L.chipload_min,
                    max=L.chipload_max)
            stated = d.chipload_mm
            if stated is not None and implied is not None:
                if abs(implied - stated) > max(0.01, stated * 0.25):
                    warn("CHIPLOAD_INCONSISTENT", "chipload", stated=stated, implied=implied)

        ap = d.stepdown_mm
        if ap is not None:
            if ap <= 0:
                err("STEPDOWN_RANGE", "stepdown")
            if flute is not None and ap > flute:
                err("STEPDOWN_GT_FLUTE_LEN", "stepdown", stepdown=ap, flute=flute)
            if ap > dia * L.stepdown_over_dia_warn:
                warn("STEPDOWN_DEEP", "stepdown", ratio=ap / dia)
        ae = d.stepover_mm
        if ae is not None and (ae <= 0 or ae > dia):
            err("STEPOVER_RANGE", "stepover", stepover=ae, diameter=dia)
        return issues
