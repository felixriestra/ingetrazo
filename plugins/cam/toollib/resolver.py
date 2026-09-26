# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""What should this tool cut this material at? — 2DCam's ``PresetResolver``.

Cutting data comes in layers, best first:

1. an explicit **preset** for this tool (the user's own, or a vendor's
   from a catalogue), scoped to a material, a material class and/or a
   machine — the most specific, most trusted one wins (:attr:`CuttingPreset.
   score`, the same ranking as the ``v_preset_ranked`` view);
2. a vendor **feed curve**: the way CMT publishes, a safe feed that falls
   as the depth of cut rises, evaluated at the operation's depth and scaled
   by the material's feed factor and the cross-grain factor;
3. a vendor **chipload band** for the material class and diameter;
4. a **generic** chipload band — placeholder values, always an estimate.

Every result then meets the real world, in a fixed order: a plastic's melt
limit on the spindle speed, the spindle's own range, the machine's feed and
plunge limits, and (for the generic bands only) the machine's rigidity.
Each adjustment is recorded as a :class:`Note` — a code with numbers, which
the UI turns into a sentence — so a user can see why a feed is what it is.

The arithmetic follows 2DCam's line by line, including Swift's rounding of
halves away from zero (:func:`.model.swift_round`); the parity fixtures in
``tests/data/cam_fixtures/toollib`` hold 2DCam's own answers.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..engine.models import new_id
from .model import LibraryTool, swift_round

#: The fallback spindle speed when neither a rule nor a machine gives one.
DEFAULT_RPM = 18_000
#: Plunge feed as a fraction of the cutting feed when nothing states it.
PLUNGE_RATIO = 0.35
#: Stepover as a fraction of the diameter when nothing states it.
STEPOVER_RATIO = 0.4


# ---- inputs -------------------------------------------------------------------

@dataclass
class CuttingPreset:
    """Cutting data for one tool, scoped to a material (or a class of them)
    and optionally a machine. ``chipload_mm`` (fz, mm per tooth) is the
    preferred source of truth: with ``derivation == "from_chipload"`` the
    feed is recomputed from it whenever the spindle speed moves."""
    tool_id: str
    name: str
    id: str = field(default_factory=new_id)
    material_id: str | None = None
    material_class_id: str | None = None
    machine_id: str | None = None
    origin: str = "user"                 # user | vendor | derived | generic
    derivation: str = "explicit"         # explicit | from_chipload
    confidence: int = 3                  # 1 … 5
    chipload_mm: float | None = None
    vc_m_min: float | None = None
    spindle_rpm: int | None = None
    feed_xy_mm_min: float | None = None
    feed_z_mm_min: float | None = None
    ramp_feed_mm_min: float | None = None
    stepdown_mm: float | None = None
    stepover_mm: float | None = None
    clearance_stepover_mm: float | None = None
    cut_direction: str | None = None
    air_blast: bool = False
    notes: str | None = None

    @property
    def score(self) -> int:
        """Mirrors the ``v_preset_ranked`` view. Higher wins: the user's own
        data beats a vendor's, a named material beats a class, a machine
        scope adds a little, then the stated confidence."""
        origin = {"user": 400, "vendor": 300, "derived": 200}.get(self.origin, 100)
        scope = 40 if self.material_id else (20 if self.material_class_id else 0)
        return origin + scope + (5 if self.machine_id else 0) + self.confidence


@dataclass
class ChiploadRule:
    """An fz band per material class and diameter range — the shape in which
    Sorotec, CMT and Leuco actually publish. ``vendor_id`` None = generic."""
    id: str
    material_class_id: str
    dia_min_mm: float
    dia_max_mm: float
    fz_min_mm: float
    fz_typ_mm: float
    fz_max_mm: float
    tool_type: str | None = None
    vendor_id: str | None = None
    rpm_min: int | None = None
    rpm_max: int | None = None
    max_stepdown_x_dia: float | None = None


@dataclass
class MaterialClass:
    """A family of stock materials that cut alike. ``feed_factor`` scales a
    vendor's base (softwood) feed curve — CMT's «Factor Vf»."""
    id: str
    name: str
    family: str = "other"
    melts: bool = False
    prefers_downcut: bool = False
    max_rpm_hint: int | None = None
    feed_factor: float = 1.0


@dataclass
class FeedCurve:
    """A vendor feed-vs-depth line: at ``rpm``, ``feed_lo`` at the shallow
    ``doc_lo`` falling to ``feed_hi`` at the deep ``doc_hi``. The base is
    softwood along the grain; material and cross-grain factors scale it."""
    id: str
    dia_min_mm: float
    dia_max_mm: float
    rpm: int
    doc_lo_mm: float
    feed_lo_mm_min: float
    doc_hi_mm: float
    feed_hi_mm_min: float
    vendor_id: str | None = None
    series: str | None = None
    tool_type: str | None = None
    cross_grain_factor: float = 0.7
    chip_min_mm: float | None = None
    chip_max_mm: float | None = None

    def base_feed(self, doc: float) -> float:
        """Base feed at a depth of cut, held to the published range: never
        extrapolate past what the vendor measured."""
        lo, hi = min(self.doc_lo_mm, self.doc_hi_mm), max(self.doc_lo_mm, self.doc_hi_mm)
        d = min(max(doc, lo), hi)
        if self.doc_hi_mm == self.doc_lo_mm:
            return self.feed_lo_mm_min
        t = (d - self.doc_lo_mm) / (self.doc_hi_mm - self.doc_lo_mm)
        return self.feed_lo_mm_min + (self.feed_hi_mm_min - self.feed_lo_mm_min) * t

    def matches(self, tool: LibraryTool) -> bool:
        dia = tool.diameter_mm
        if dia is None or not (self.dia_min_mm <= dia <= self.dia_max_mm):
            return False
        if self.vendor_id is not None and self.vendor_id != tool.vendor_id:
            return False
        if self.tool_type is not None and self.tool_type != tool.type:
            return False
        if self.series is not None and self.series != (tool.series or ""):
            return False
        return True


@dataclass
class MachineLimits:
    """What the resolver needs to know about the machine. ``None`` fields
    impose no limit. ``rigidity`` (0 … 1] derates generic chiploads for a
    light hobby frame."""
    id: str | None = None
    max_rpm: int | None = None
    min_rpm: int | None = None
    max_feed: float | None = None
    max_plunge: float | None = None
    rigidity: float = 1.0

    @classmethod
    def from_job_machine(cls, machine, machine_id: str | None = None) -> "MachineLimits":
        """From a job's :class:`..engine.models.Machine`."""
        return cls(id=machine_id,
                   max_rpm=machine.maximumSpindleRPM,
                   min_rpm=machine.minimumSpindleRPM,
                   max_feed=machine.maximumFeed,
                   max_plunge=machine.maximumPlungeFeed,
                   rigidity=getattr(machine, "rigidityFactor", 1.0) or 1.0)


# ---- output ---------------------------------------------------------------------

@dataclass
class Note:
    """One adjustment the resolver made, as a code with numbers. Codes:
    ``rpm_melt_cap`` (rpm, material), ``rpm_spindle_max`` (rpm),
    ``rpm_spindle_min`` (rpm), ``chipload_derated`` (percent),
    ``feed_machine_max`` (feed), ``plunge_z_max`` (feed), ``plunge_reduced``,
    ``doc_recommended`` (doc), ``doc_outside_curve`` (doc, lo, hi),
    ``feed_material_factor`` (factor, material), ``feed_cross_grain`` (factor)."""
    code: str
    params: dict = field(default_factory=dict)


#: Every note code, for the message-table test.
NOTE_CODES = ("rpm_melt_cap", "rpm_spindle_max", "rpm_spindle_min", "chipload_derated",
              "feed_machine_max", "plunge_z_max", "plunge_reduced", "doc_recommended",
              "doc_outside_curve", "feed_material_factor", "feed_cross_grain")


@dataclass
class ResolvedCuttingData:
    """Everything a job tool needs, with a paper trail. ``source`` is
    ``preset`` (with ``preset_origin``/``preset_name``), ``feed_curve`` or
    ``rule``; ``vendor_id`` is set for a vendor's curve or band."""
    spindle_rpm: int
    feed_xy_mm_min: float
    feed_z_mm_min: float
    chipload_mm: float
    stepdown_mm: float
    stepover_mm: float
    source: str
    confidence: int
    notes: list = field(default_factory=list)
    preset_origin: str | None = None
    preset_name: str | None = None
    vendor_id: str | None = None

    @property
    def is_estimate(self) -> bool:
        """True when nothing better than a band or a derived/generic preset
        was available. The UI must show these as estimates, not data."""
        if self.source == "preset":
            return self.preset_origin in ("derived", "generic")
        return self.source == "rule"

    @property
    def provenance(self) -> str:
        """The job tool's ``recommendedStepdownProvenance``: whether CAM
        must obey the stepdown (user or manufacturer data) or may treat it
        as advice (an estimate)."""
        if self.is_estimate:
            return "ruleEstimate"
        if self.source == "preset" and self.preset_origin == "user":
            return "userOverride"
        return "manufacturerPreset"


# ---- resolution ---------------------------------------------------------------

def resolve(tool: LibraryTool, material_class: MaterialClass, *, material_id: str | None = None,
            machine: MachineLimits | None = None, presets=(), rules=(), feed_curves=(),
            depth_of_cut_mm: float | None = None) -> ResolvedCuttingData | None:
    """The cutting data for ``tool`` in ``material_class`` on ``machine``.
    ``depth_of_cut_mm`` is the depth the operation will cut per pass; feed
    curves need it, the rest ignore it. Without it a curve is read at the
    tool's recommended depth, and the result says so. Returns None only
    when the tool's geometry is too incomplete to derive anything."""
    candidates = [p for p in presets
                  if p.tool_id == tool.id
                  and (p.material_id is None or p.material_id == material_id)
                  and (p.material_class_id is None or p.material_class_id == material_class.id)
                  and (p.machine_id is None or (machine is not None and p.machine_id == machine.id))]
    if candidates:
        best = max(candidates, key=lambda p: p.score)
        done = _materialise(best, tool, machine, material_class)
        if done is not None:
            return done

    dia, flutes = tool.diameter_mm, tool.flute_count
    if dia is None or flutes is None or dia <= 0 or flutes <= 0:
        return None                      # too incomplete: refuse rather than guess

    curves = [c for c in feed_curves if c.matches(tool)]
    if curves:
        curve = max(curves, key=lambda c: (2 if c.vendor_id else 0) + (1 if c.series else 0))
        doc = depth_of_cut_mm if depth_of_cut_mm is not None else tool.recommended_doc_mm
        return _resolve_curve(curve, tool, dia, flutes, machine, material_class, doc,
                              depth_of_cut_mm is not None)

    matching = [r for r in rules
                if r.material_class_id == material_class.id
                and r.dia_min_mm <= dia <= r.dia_max_mm
                and (r.tool_type is None or r.tool_type == tool.type)
                and (r.vendor_id is None or r.vendor_id == tool.vendor_id)]
    if not matching:
        return None
    # Vendor-specific beats generic; type-specific beats any type.
    rule = max(matching, key=lambda r: (2 if r.vendor_id else 0) + (1 if r.tool_type else 0))

    notes = []
    rpm = (rule.rpm_max if rule.rpm_max is not None
           else machine.max_rpm if machine is not None and machine.max_rpm is not None
           else DEFAULT_RPM)
    rpm = _clamp_rpm(rpm, machine, material_class, notes)

    rigidity = machine.rigidity if machine is not None else 1.0
    fz = rule.fz_typ_mm * rigidity
    if rigidity < 1.0:
        notes.append(Note("chipload_derated", {"percent": int(rigidity * 100)}))

    feed = fz * flutes * rpm
    feed = _clamp_feed(feed, machine, notes)
    plunge = _clamp_plunge(feed * PLUNGE_RATIO, machine, notes)
    flute_len = tool.flute_length_mm if tool.flute_length_mm is not None else float("inf")
    stepdown = min((rule.max_stepdown_x_dia if rule.max_stepdown_x_dia is not None else 1.0) * dia,
                   flute_len)
    return ResolvedCuttingData(
        spindle_rpm=rpm, feed_xy_mm_min=swift_round(feed), feed_z_mm_min=swift_round(plunge),
        chipload_mm=fz, stepdown_mm=stepdown, stepover_mm=dia * STEPOVER_RATIO,
        source="rule", vendor_id=rule.vendor_id,
        confidence=3 if rule.vendor_id else 1, notes=notes)


def _clamp_rpm(rpm: int, machine, material_class: MaterialClass, notes: list) -> int:
    """Melt limit, then the spindle's maximum, then its minimum."""
    melt = material_class.max_rpm_hint
    if melt is not None and rpm > melt:
        rpm = melt
        notes.append(Note("rpm_melt_cap", {"rpm": melt, "material": material_class.id}))
    if machine is not None and machine.max_rpm is not None and rpm > machine.max_rpm:
        rpm = machine.max_rpm
        notes.append(Note("rpm_spindle_max", {"rpm": rpm}))
    if machine is not None and machine.min_rpm is not None and rpm < machine.min_rpm:
        rpm = machine.min_rpm
        notes.append(Note("rpm_spindle_min", {"rpm": rpm}))
    return rpm


def _clamp_feed(feed: float, machine, notes: list) -> float:
    if machine is not None and machine.max_feed is not None and feed > machine.max_feed:
        feed = machine.max_feed
        notes.append(Note("feed_machine_max", {"feed": feed}))
    return feed


def _clamp_plunge(plunge: float, machine, notes: list) -> float:
    if machine is not None and machine.max_plunge is not None and plunge > machine.max_plunge:
        plunge = machine.max_plunge
        notes.append(Note("plunge_z_max", {"feed": plunge}))
    return plunge


def _resolve_curve(curve: FeedCurve, tool: LibraryTool, dia: float, flutes: int, machine,
                   material_class: MaterialClass, depth_mm, doc_from_operation: bool):
    """A vendor curve at the chosen depth, times the material and
    cross-grain factors, clamped to the machine. Rigidity is NOT applied:
    this is published vendor data, trusted and only limited."""
    notes = []
    doc = depth_mm if depth_mm is not None else curve.doc_lo_mm
    if not doc_from_operation:
        notes.append(Note("doc_recommended", {"doc": int(swift_round(doc))}))
    lo, hi = min(curve.doc_lo_mm, curve.doc_hi_mm), max(curve.doc_lo_mm, curve.doc_hi_mm)
    if doc < lo or doc > hi:
        notes.append(Note("doc_outside_curve", {"doc": int(swift_round(doc)), "lo": curve.doc_lo_mm,
                                                "hi": curve.doc_hi_mm}))
    feed = curve.base_feed(doc) * material_class.feed_factor * curve.cross_grain_factor
    if material_class.feed_factor != 1.0:
        notes.append(Note("feed_material_factor", {"factor": material_class.feed_factor,
                                                   "material": material_class.id}))
    notes.append(Note("feed_cross_grain", {"factor": curve.cross_grain_factor}))
    rpm = _clamp_rpm(curve.rpm, machine, material_class, notes)
    feed = _clamp_feed(feed, machine, notes)
    plunge = _clamp_plunge(feed * PLUNGE_RATIO, machine, notes)
    fz = feed / (rpm * flutes) if flutes > 0 else 0.0
    recommended = tool.recommended_doc_mm if tool.recommended_doc_mm is not None else doc
    flute_len = tool.flute_length_mm if tool.flute_length_mm is not None else float("inf")
    stepdown = min(depth_mm if depth_mm is not None else recommended, flute_len)
    return ResolvedCuttingData(
        spindle_rpm=rpm, feed_xy_mm_min=swift_round(feed), feed_z_mm_min=swift_round(plunge),
        chipload_mm=fz, stepdown_mm=stepdown, stepover_mm=dia * STEPOVER_RATIO,
        source="feed_curve", vendor_id=curve.vendor_id, confidence=4, notes=notes)


def _materialise(p: CuttingPreset, tool: LibraryTool, machine, material_class: MaterialClass):
    """A stored preset as usable numbers: fill any gap from its chipload and
    clamp to the machine."""
    dia = tool.diameter_mm
    if dia is None or dia <= 0:
        return None
    flutes = tool.flute_count or 0
    notes = []
    rpm = (p.spindle_rpm if p.spindle_rpm is not None
           else machine.max_rpm if machine is not None and machine.max_rpm is not None
           else DEFAULT_RPM)
    rpm = _clamp_rpm(rpm, machine, material_class, notes)

    # The feed is recomputed from the chipload when the preset says so, or
    # when there is no stored feed — so a clamp that moved the RPM above
    # moves the feed with it.
    stored = p.feed_xy_mm_min
    if ((p.derivation == "from_chipload" or stored is None)
            and p.chipload_mm is not None and flutes > 0):
        fz = p.chipload_mm
        feed = p.chipload_mm * flutes * rpm
    elif stored is not None:
        feed = stored
        fz = stored / (rpm * flutes) if flutes > 0 else (p.chipload_mm or 0.0)
    else:
        return None

    feed = _clamp_feed(feed, machine, notes)
    plunge = p.feed_z_mm_min if p.feed_z_mm_min is not None else feed * PLUNGE_RATIO
    plunge = _clamp_plunge(plunge, machine, notes)
    if plunge > feed:
        plunge = feed * 0.5
        notes.append(Note("plunge_reduced"))
    flute_len = tool.flute_length_mm if tool.flute_length_mm is not None else dia
    return ResolvedCuttingData(
        spindle_rpm=rpm, feed_xy_mm_min=swift_round(feed), feed_z_mm_min=swift_round(plunge),
        chipload_mm=fz,
        stepdown_mm=p.stepdown_mm if p.stepdown_mm is not None else min(dia, flute_len),
        stepover_mm=p.stepover_mm if p.stepover_mm is not None else dia * STEPOVER_RATIO,
        source="preset", preset_origin=p.origin, preset_name=p.name,
        confidence=p.confidence, notes=notes)
