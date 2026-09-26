# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""A tool as the library knows it, and the bridge into a job.

Port of 2DCam's ``LibraryTool.swift``. The library distinguishes eleven
tool types; a job's tool table only six *kinds* (``engine.models.
TOOL_KINDS``), because that is all the toolpath generators need to tell
apart. :data:`CAM_KIND` is the collapse, and it is why a job tool also
carries ``includedAngle`` and ``tipDiameter``: V-bits, engravers and
chamfer mills all become ``chamferMill``, and only those two numbers still
tell them apart.

**Unknown is not zero.** A geometry field the catalogue did not state is
``None`` all the way through: the library shows it as missing and refuses
to send the tool to a job rather than cutting with an invented number.

Form tools (a drawn moulding silhouette) are kept when a 2DCam library
file carries them, but they cannot go into a job: this port's generators
have no form-tool cutting (see ``docs/cam-2dcam-deviations.md``).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..engine.models import Tool, new_id

#: The library's tool types (2DCam's ``ToolType`` raw values, which are
#: also what the database stores).
TOOL_TYPES = ("end_mill", "ball_nose", "bull_nose", "v_bit", "engraver", "tapered_ball",
              "drill", "chamfer", "surfacing", "form", "drag_knife")

#: Library type → job tool kind (2DCam's ``ToolType.camKind``). A form tool
#: has no job kind here: it cannot be machined by this port.
CAM_KIND = {
    "end_mill": "flatEndMill", "surfacing": "flatEndMill", "drag_knife": "flatEndMill",
    "ball_nose": "ballEndMill", "tapered_ball": "ballEndMill",
    "bull_nose": "bullNoseEndMill",
    "v_bit": "chamferMill", "engraver": "chamferMill", "chamfer": "chamferMill",
    "drill": "drill",
}

#: Types whose geometry means nothing without an included angle.
NEEDS_ANGLE = ("v_bit", "engraver", "tapered_ball")

#: Stored values of the free-text columns the schema constrains.
CHIP_DIRECTIONS = ("up", "down", "compression", "straight")
SUBSTRATES = ("solid_carbide", "carbide_tipped", "hss", "diamond", "insert")
DISPLAY_UNITS = ("mm", "inch", "frac_inch")


def swift_round(x: float) -> float:
    """Swift's ``Double.rounded()``: halves away from zero. Python's
    ``round`` sends halves to the even neighbour, which would make a feed
    of 1 237.5 mm/min differ from 2DCam's by one."""
    return float(math.floor(x + 0.5)) if x >= 0 else -float(math.floor(-x + 0.5))


class LibraryToolError(Exception):
    """A library tool cannot become a job tool. ``code`` is one of
    ``incomplete`` (with ``missing``, a list of field codes) and
    ``form_tool_unsupported``."""

    def __init__(self, code: str, name: str, missing=()):
        super().__init__(code)
        self.code = code
        self.name = name
        self.missing = list(missing)


@dataclass
class FormPoint:
    """One point of a form tool's drawn silhouette: ``radius`` from the
    centre line and ``rise`` above the tip, mm. Kept only so a 2DCam
    library round-trips; nothing here cuts with it."""
    radius: float
    rise: float


@dataclass
class LibraryTool:
    """Identity, geometry and provenance. No cutting data: that lives in
    :class:`.resolver.CuttingPreset`, keyed by material and machine, because
    one cutter has different feeds in MDF and in acrylic."""
    name: str
    type: str = "end_mill"
    id: str = field(default_factory=new_id)
    vendor_id: str | None = None
    vendor_name: str | None = None
    product_id: str | None = None
    product_url: str | None = None
    series: str | None = None

    # Geometry, mm. None means UNKNOWN — never substitute a zero.
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
    traits: set = field(default_factory=set)
    #: A form tool's drawn silhouette (2DCam's ``formProfilePoints``).
    form_points: list = field(default_factory=list)
    form_scale_mode: str = "keepAngle"

    display_units: str = "mm"
    #: The vendor's own wording of the size, e.g. ``1/4"``.
    nominal_label: str | None = None
    notes: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    deleted_at: str | None = None

    # ---- what the operation can rely on ----------------------------------
    @property
    def recommended_doc_mm(self) -> float | None:
        """Geometry-derived depth of cut per pass: the lesser of 60 % of the
        diameter and 50 % of the cutting length. The diameter term keeps the
        chip load sane, the length term keeps the cut inside the flutes.
        Advice only — the operation picks the final depth."""
        dia = self.diameter_mm
        if dia is None or dia <= 0:
            return None
        by_diameter = 0.60 * dia
        flute = self.flute_length_mm
        if flute is None or flute <= 0:
            return by_diameter
        return min(by_diameter, 0.50 * flute)

    @property
    def is_machinable(self) -> bool:
        """The minimum needed to compute a toolpath at all."""
        d, z = self.diameter_mm, self.flute_count
        if d is None or d <= 0 or z is None or z <= 0:
            return False
        if self.type in NEEDS_ANGLE and self.included_angle_deg is None:
            return False
        if self.type == "form" and len(self.form_points) < 2:
            return False
        return True

    @property
    def missing_fields(self) -> list:
        """Field codes a user must fill before the tool can go into a job:
        the library's «incomplete» mark."""
        missing = []
        if self.diameter_mm is None:
            missing.append("diameter")
        if self.flute_count is None:
            missing.append("flute_count")
        if self.flute_length_mm is None:
            missing.append("cutting_length")
        if self.overall_length_mm is None:
            missing.append("overall_length")
        if self.type in NEEDS_ANGLE and self.included_angle_deg is None:
            missing.append("included_angle")
        if self.type == "bull_nose" and self.corner_radius_mm is None:
            missing.append("corner_radius")
        if self.type == "form" and len(self.form_points) < 2:
            missing.append("profile_shape")
        return missing

    @property
    def can_go_into_a_job(self) -> bool:
        return self.type in CAM_KIND and not self.missing_fields

    # ---- the bridge into a job ------------------------------------------
    def to_job_tool(self, number: int, resolved) -> Tool:
        """The job-side :class:`Tool`, with the cutting data ``resolved``
        (:class:`.resolver.ResolvedCuttingData`) for the job's material and
        machine. Refuses rather than inventing values: a tool with an
        unknown cutting length has no business generating G-code
        (2DCam's ``toolDefinition``). Everything stays in mm — the job's
        display units are the UI's business."""
        if self.type not in CAM_KIND:
            raise LibraryToolError("form_tool_unsupported", self.name)
        if (self.diameter_mm is None or self.flute_count is None
                or self.flute_length_mm is None or self.overall_length_mm is None):
            raise LibraryToolError("incomplete", self.name, self.missing_fields)
        kind = CAM_KIND[self.type]
        tool = Tool(
            number=number,
            name=self.name,
            kind=kind,
            diameter=self.diameter_mm,
            fluteLength=self.flute_length_mm,
            overallLength=max(self.overall_length_mm, self.flute_length_mm),
            fluteCount=self.flute_count,
            cornerRadius=self.corner_radius_mm or 0.0,
            spindleRPM=resolved.spindle_rpm,
            cuttingFeed=resolved.feed_xy_mm_min,
            plungeFeed=resolved.feed_z_mm_min,
            includedAngle=self.included_angle_deg,
            tipDiameter=self.tip_dia_mm,
            recommendedStepdown=resolved.stepdown_mm,
            recommendedStepdownProvenance=resolved.provenance,
        )
        if kind == "chamferMill":
            tool.includedAngle = self.included_angle_deg if self.included_angle_deg else 90.0
            tool.tipDiameter = self.tip_dia_mm or 0.0
        elif kind == "ballEndMill":
            # The job's ball is the full radius; the library may store it
            # (a validated ball nose always does) or leave it unknown.
            tool.cornerRadius = self.diameter_mm * 0.5
        return tool

    @classmethod
    def from_job_tool(cls, tool: Tool) -> "LibraryTool":
        """A library tool with a job tool's geometry — «Save to the
        library». The job's cutting data is NOT carried over here: it is
        tied to the job's material, so the caller stores it as a preset."""
        kind_type = {"flatEndMill": "end_mill", "ballEndMill": "ball_nose",
                     "bullNoseEndMill": "bull_nose", "chamferMill": "v_bit",
                     "drill": "drill", "spotDrill": "drill"}
        t = kind_type.get(tool.kind, "end_mill")
        if t == "v_bit" and (tool.tipDiameter or 0.0) > 0.05:
            t = "engraver"                       # a V with a flat is an engraver
        return cls(
            name=tool.name, type=t,
            diameter_mm=tool.diameter,
            corner_radius_mm=(tool.diameter * 0.5 if t == "ball_nose"
                              else (tool.cornerRadius or None) if t == "bull_nose" else None),
            included_angle_deg=tool.includedAngle if t in ("v_bit", "engraver") else None,
            tip_dia_mm=tool.tipDiameter if t in ("v_bit", "engraver") else None,
            flute_count=tool.fluteCount,
            flute_length_mm=tool.fluteLength,
            overall_length_mm=tool.overallLength,
        )

    # ---- silhouette -------------------------------------------------------
    def radius_at(self, z: float) -> float:
        """The cutter's radius at height ``z`` (mm) above its tip, clamped
        to the full radius above the shaped part (2DCam's
        ``CutterProfile.radius(atHeight:)``). One definition for the
        library preview and anything else that draws the cutter."""
        R = (self.diameter_mm or 0.0) / 2
        if z <= 0:
            return self.tip_radius
        t = self.type
        if t in ("end_mill", "surfacing", "drill", "drag_knife"):
            return R
        if t == "ball_nose":
            return R if z >= R else math.sqrt(R * R - (R - z) * (R - z))
        if t == "bull_nose":
            rc = min(self.corner_radius_mm or 0.0, R)
            if rc <= 0 or z >= rc:
                return R
            return (R - rc) + math.sqrt(rc * rc - (rc - z) * (rc - z))
        if t in ("v_bit", "engraver", "chamfer"):
            if self.included_angle_deg is None:
                return R
            half = math.radians(self.included_angle_deg / 2)
            return min(self.tip_radius + z * math.tan(half), R)
        if t == "tapered_ball":
            rt = min(self.corner_radius_mm or 0.0, R)
            if rt <= 0 or self.included_angle_deg is None:
                return R
            half = math.radians(self.included_angle_deg / 2)
            if z < rt * (1 - math.sin(half)):
                return math.sqrt(rt * rt - (rt - z) * (rt - z))
            r = rt * math.cos(half) + (z - rt * (1 - math.sin(half))) * math.tan(half)
            return min(r, R)
        if t == "form":
            pts = sorted(((p.rise, p.radius) for p in self.form_points))
            if not pts:
                return R
            if z <= pts[0][0]:
                return pts[0][1]
            for (z0, r0), (z1, r1) in zip(pts, pts[1:]):
                if z <= z1:
                    return r1 if z1 == z0 else r0 + (r1 - r0) * (z - z0) / (z1 - z0)
            return pts[-1][1]
        return R

    @property
    def tip_radius(self) -> float:
        """Radius at the very tip: 0 for a point, the flat for an engraver."""
        d = self.diameter_mm or 0.0
        t = self.type
        if t in ("ball_nose", "tapered_ball"):
            return 0.0
        if t in ("v_bit", "engraver", "chamfer"):
            return (self.tip_dia_mm or 0.0) / 2
        if t == "bull_nose":
            return max(0.0, d / 2 - min(self.corner_radius_mm or 0.0, d / 2))
        if t == "form":
            pts = sorted(((p.rise, p.radius) for p in self.form_points))
            return pts[0][1] if pts else d / 2
        return d / 2
