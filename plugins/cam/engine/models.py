# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The CAM job model — stock, setup, machine, tools, operations.

A port of 2DCam's ``Models/`` (``StockDefinition``, ``MachineSetup``,
``MachineProfile``, ``ToolDefinition``, ``CAMOperation``,
``MachiningStrategy``) as plain dataclasses. Field names are 2DCam's
``Codable`` keys, camelCase included, on purpose: :mod:`.io` then reads a
2DCam project and writes a job 2DCam could read back with no renaming
table to drift, and the parity fixtures exported from Swift load as-is.

Units: **every length in the engine is a millimetre and every feed is
mm/min, in float64**, whatever the job's display units. An inch job only
differs in ``Job.units`` — the UI converts what the user types, and the
post-processor converts what it writes (``G20``). Keeping one internal unit
is what makes inch support cheap and safe: no generator ever sees a
mixed-unit number.

Points are tuples — ``(x, y)`` and ``(x, y, z)`` — because the generators
create them by the thousand and a tuple is both the cheapest and an
immutable value.
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field

Point2D = tuple  # (x, y)
Point3D = tuple  # (x, y, z)

MM_PER_INCH = 25.4


def new_id() -> str:
    """A fresh identifier, formatted as Swift's ``UUID.uuidString``."""
    return str(uuid.uuid4()).upper()


def _finite(*values) -> bool:
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)


# ---- stock and setup ---------------------------------------------------------

@dataclass
class Stock:
    """The raw board. ``origin`` is its minimum corner in work coordinates;
    the top of the stock is ``origin.z + height``."""
    width: float = 100.0
    depth: float = 75.0
    height: float = 20.0
    origin: Point3D = (0.0, 0.0, 0.0)
    shape: str = "rectangular"            # v1 machines rectangular stock
    material: str = "clearWood"
    referencePoint: str = "bottomLeft"    # topLeft|topRight|bottomLeft|bottomRight|center
    zeroPosition: str = "machineBed"      # materialSurface|machineBed
    isConfigured: bool = True

    @property
    def top_z(self) -> float:
        return self.origin[2] + self.height

    @property
    def is_valid(self) -> bool:
        return (self.isConfigured and _finite(self.width, self.depth, self.height, *self.origin)
                and self.width > 0 and self.depth > 0 and self.height > 0)

    def align_origin_to_reference(self) -> None:
        """Put the work zero where ``referencePoint``/``zeroPosition`` say
        (2DCam's ``alignOriginToReference``): X/Y from the 9-point corner
        choice, Z at the bed or at the top surface."""
        x, y = {
            "bottomLeft": (0.0, 0.0),
            "bottomRight": (-self.width, 0.0),
            "topLeft": (0.0, -self.depth),
            "topRight": (-self.width, -self.depth),
            "center": (-self.width * 0.5, -self.depth * 0.5),
        }.get(self.referencePoint, (0.0, 0.0))
        z = -self.height if self.zeroPosition == "materialSurface" else 0.0
        self.origin = (x, y, z)

    def contains_xy(self, p, tolerance: float = 0.0) -> bool:
        ox, oy, _ = self.origin
        if self.shape in ("circular", "elliptical"):
            rx, ry = self.width * 0.5 + tolerance, self.depth * 0.5 + tolerance
            if rx <= 0 or ry <= 0:
                return False
            dx = (p[0] - (ox + self.width * 0.5)) / rx
            dy = (p[1] - (oy + self.depth * 0.5)) / ry
            return dx * dx + dy * dy <= 1
        return (ox - tolerance <= p[0] <= ox + self.width + tolerance
                and oy - tolerance <= p[1] <= oy + self.depth + tolerance)


@dataclass
class MachineSetup:
    """Heights above the stock top: ``safeHeight`` for travel between cuts,
    ``clearanceHeight`` (lower) for the approach of a drill cycle."""
    workOffset: str = "G54"
    safeHeight: float = 10.0
    clearanceHeight: float = 5.0


@dataclass
class Machine:
    """The machine's limits and the numbers the time estimate needs
    (2DCam's ``MachineProfile``, less the Mach/Fanuc post selection — the
    controller lives on :class:`Job`)."""
    name: str = "Generic 3-Axis Router"
    maximumSpindleRPM: int = 24_000
    minimumSpindleRPM: int = 6_000
    maximumFeed: float = 5_000.0
    maximumPlungeFeed: float | None = None
    rapidFeed: float = 8_000.0
    cuttingAcceleration: float = 150.0
    rapidAcceleration: float = 300.0
    spindleRampSeconds: float = 3.0
    toolChangeSeconds: float = 20.0
    coolantDelaySeconds: float = 0.5
    enforcesTravelLimits: bool = False
    travelMinimum: Point3D = (0.0, 0.0, -200.0)
    travelMaximum: Point3D = (1_000.0, 1_000.0, 300.0)
    workOriginInMachineCoordinates: Point3D = (0.0, 0.0, 0.0)

    @property
    def has_valid_travel_envelope(self) -> bool:
        lo, hi, wo = self.travelMinimum, self.travelMaximum, self.workOriginInMachineCoordinates
        return (_finite(*lo, *hi, *wo)
                and lo[0] < hi[0] and lo[1] < hi[1] and lo[2] < hi[2]
                and self.maximumSpindleRPM > 0 and self.maximumFeed > 0 and self.rapidFeed > 0
                and 0 < self.minimumSpindleRPM <= self.maximumSpindleRPM
                and self.cuttingAcceleration > 0 and self.rapidAcceleration > 0
                and self.spindleRampSeconds >= 0 and self.toolChangeSeconds >= 0
                and self.coolantDelaySeconds >= 0)

    def machine_point(self, p) -> Point3D:
        o = self.workOriginInMachineCoordinates
        return (p[0] + o[0], p[1] + o[1], p[2] + o[2])

    def contains_work_point(self, p) -> bool:
        m = self.machine_point(p)
        lo, hi = self.travelMinimum, self.travelMaximum
        return all(lo[i] <= m[i] <= hi[i] for i in range(3))


@dataclass
class Tolerance:
    """Machining tolerances in mm (2DCam's ``MachiningTolerance``):
    ``chordal`` drives arc flattening in offsets, ``arc`` the radius check
    of arc moves, ``angularDegrees`` the arc sampling of the verifier."""
    linear: float = 0.01
    arc: float = 0.01
    chordal: float = 0.02
    angularDegrees: float = 0.5

    @property
    def is_valid(self) -> bool:
        vals = (self.linear, self.arc, self.chordal, self.angularDegrees)
        return _finite(*vals) and all(v > 0 for v in vals)


# ---- tools -------------------------------------------------------------------

TOOL_KINDS = ("flatEndMill", "ballEndMill", "bullNoseEndMill", "chamferMill",
              "drill", "spotDrill")


@dataclass
class ToolHolder:
    diameter: float = 25.0
    length: float = 40.0
    offsetFromTip: float = 35.0

    @property
    def is_valid(self) -> bool:
        return (_finite(self.diameter, self.length, self.offsetFromTip)
                and self.diameter > 0 and self.length > 0 and self.offsetFromTip > 0)


@dataclass
class Tool:
    """One cutter in the job's tool table (2DCam's ``ToolDefinition``)."""
    number: int = 1
    name: str = "6 mm Flat End Mill"
    kind: str = "flatEndMill"
    diameter: float = 6.0
    fluteLength: float = 20.0
    overallLength: float = 50.0
    fluteCount: int = 2
    cornerRadius: float = 0.0
    spindleRPM: int = 12_000
    cuttingFeed: float = 800.0
    plungeFeed: float = 250.0
    includedAngle: float | None = None
    tipDiameter: float | None = None
    recommendedStepdown: float | None = None
    recommendedStepdownProvenance: str | None = None
    holder: ToolHolder | None = None
    id: str = field(default_factory=new_id)

    @property
    def radius(self) -> float:
        return self.diameter * 0.5

    @property
    def is_valid(self) -> bool:
        return (self.number > 0 and _finite(self.diameter, self.fluteLength, self.overallLength,
                                             self.cornerRadius, self.cuttingFeed, self.plungeFeed)
                and self.diameter > 0 and self.fluteLength > 0
                and self.overallLength >= self.fluteLength
                and self.fluteCount > 0 and 0 <= self.cornerRadius <= self.diameter * 0.5
                and self.spindleRPM > 0 and self.cuttingFeed > 0 and self.plungeFeed > 0
                and (self.holder is None or self.holder.is_valid)
                and (self.includedAngle is None
                     or (_finite(self.includedAngle) and 0 < self.includedAngle < 180))
                and (self.tipDiameter is None
                     or (_finite(self.tipDiameter) and 0 <= self.tipDiameter <= self.diameter)))

    @property
    def authoritative_stepdown(self) -> float | None:
        """A depth-per-pass limit CAM must obey: only one the user or the
        manufacturer set (2DCam's provenance rule; estimates are advice)."""
        if (self.recommendedStepdownProvenance in ("userOverride", "manufacturerPreset")
                and self.recommendedStepdown is not None
                and _finite(self.recommendedStepdown) and self.recommendedStepdown > 0):
            return self.recommendedStepdown
        return None


# ---- operations --------------------------------------------------------------

@dataclass
class Region:
    """A closed outer boundary with islands (holes) — the geometry a
    profile or pocket machines. Loops are lists of ``(x, y)``; orientation
    is not significant on input (generators normalise it)."""
    boundary: list = field(default_factory=list)
    islands: list = field(default_factory=list)
    openEdgeIndices: list = field(default_factory=list)


@dataclass
class Tab:
    """A holding tab on a profile: centred at ``pathFraction`` of the path
    length, ``width`` long, leaving ``height`` of material standing."""
    pathFraction: float = 0.0
    width: float = 5.0
    height: float = 1.0
    id: str = field(default_factory=new_id)


@dataclass
class Strategy:
    """How an operation cuts (2DCam's ``MachiningStrategy``). ``geometry``
    is ``None`` for 2DCam's ``.automatic`` (the stock rectangle, inset)."""
    geometry: Region | None = None
    topHeight: float = 0.0
    bottomHeight: float | None = None
    stockAllowance: float = 0.0
    direction: str = "climb"              # climb|conventional
    compensation: str = "computer"        # computer|wear|controller
    entry: str = "plunge"                 # plunge|ramp|helix
    leadInLength: float = 0.0
    leadOutLength: float = 0.0
    safeHeightOverride: float | None = None
    clearanceHeightOverride: float | None = None
    finishingPasses: int = 1
    tabs: list = field(default_factory=list)
    ordering: str = "input"               # input|nearestNeighbor

    def resolved_setup(self, setup: MachineSetup) -> MachineSetup:
        return MachineSetup(
            workOffset=setup.workOffset,
            safeHeight=(self.safeHeightOverride if self.safeHeightOverride is not None
                        else setup.safeHeight),
            clearanceHeight=(self.clearanceHeightOverride
                             if self.clearanceHeightOverride is not None
                             else setup.clearanceHeight))


@dataclass
class FacingParameters:
    depth: float = 0.5
    stepoverFraction: float = 0.7


@dataclass
class ProfileParameters:
    depth: float = 5.0
    stepDown: float = 2.0
    inset: float = 5.0
    stockAllowance: float = 0.0


@dataclass
class PocketParameters:
    depth: float = 5.0
    stepDown: float = 2.0
    stepoverFraction: float = 0.6
    inset: float = 10.0
    stockAllowance: float = 0.0


@dataclass
class DrillingParameters:
    """``peckDepth`` and ``dwellSeconds`` are the port's additions (2DCam
    drills in one plunge); 0 keeps 2DCam's behaviour exactly."""
    depth: float = 8.0
    points: list = field(default_factory=list)      # [(x, y, z)]
    peckDepth: float = 0.0
    dwellSeconds: float = 0.0


@dataclass
class EngravingParameters:
    points: list = field(default_factory=list)      # [(x, y, z)]
    depth: float = 0.5
    stepDown: float = 0.25
    isClosed: bool = False


#: v1 operation kinds and their parameter classes. The keys are 2DCam's
#: ``CAMOperationKind`` raw values.
PARAMETER_TYPES = {
    "facing": FacingParameters,
    "outsideProfile": ProfileParameters,
    "insideProfile": ProfileParameters,
    "pocket": PocketParameters,
    "drilling": DrillingParameters,
    "engraving": EngravingParameters,
}

#: Kinds that may name a separate finishing tool (2DCam's
#: ``supportsSeparateFinishingTool``, less the v1.x open pocket).
FINISHING_TOOL_KINDS = ("outsideProfile", "insideProfile", "pocket")


def default_parameters(kind: str):
    if kind == "insideProfile":
        return ProfileParameters(inset=10.0)
    return PARAMETER_TYPES[kind]()


@dataclass
class Operation:
    name: str = "Operation"
    kind: str = "outsideProfile"
    toolID: str | None = None
    finishingToolID: str | None = None
    isEnabled: bool = True
    parameters: object = None
    strategy: Strategy = field(default_factory=Strategy)
    id: str = field(default_factory=new_id)

    def __post_init__(self) -> None:
        if self.parameters is None and self.kind in PARAMETER_TYPES:
            self.parameters = default_parameters(self.kind)


# ---- the job -----------------------------------------------------------------

CONTROLLERS = ("grbl", "linuxcnc")


@dataclass
class PostOptions:
    """What the post-processor needs beyond the toolpath. ``coolant`` off
    by default: most routers have none, and an M8 on a GRBL build without
    coolant support is an error on some senders."""
    controller: str = "grbl"              # grbl|linuxcnc
    coolant: bool = False
    spindleDwell: bool = True             # G4 after M3, for the spindle to reach speed
    flattenArcs: bool = False             # LinuxCNC option: arcs as line segments


@dataclass
class Job:
    """Everything needed to compute and post one CAM job.

    ``units`` is the DISPLAY and OUTPUT unit (``"millimeters"`` or
    ``"inches"``, 2DCam's raw values); the numbers stored in the job are
    millimetres regardless (see the module docstring)."""
    name: str = "Untitled Part"
    units: str = "millimeters"
    stock: Stock = field(default_factory=Stock)
    setup: MachineSetup = field(default_factory=MachineSetup)
    machine: Machine = field(default_factory=Machine)
    tolerance: Tolerance = field(default_factory=Tolerance)
    tools: list = field(default_factory=list)
    operations: list = field(default_factory=list)
    post: PostOptions = field(default_factory=PostOptions)

    def tool(self, tool_id) -> Tool | None:
        return next((t for t in self.tools if t.id == tool_id), None)

    def tool_by_number(self, number) -> Tool | None:
        return next((t for t in self.tools if t.number == number), None)

    @property
    def is_inch(self) -> bool:
        return self.units == "inches"
