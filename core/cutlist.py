# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Guillotine cutting-diagram planner — pure data, no Qt (see plugins/cutlist.py).

A line-for-line port of ``GuillotinePlanner.swift`` + ``Models.swift`` from
2DCutList (a native macOS workshop utility, same author): given a list of
rectangular parts and a set of stock sheet definitions, produces a
guillotine-valid cutting plan — which sheet each part goes on, where, in
what order to cut, and which offcuts are worth keeping.

Deliberately excluded from this port (2DCutList's own v2 features):
``recalculate``/``repackSubtree`` (manual per-cut editing) and CSV import —
IngeTrazo reads parts straight from the selected model geometry instead
(see ``plugins/cutlist.py::parts_from_selection``).

All distances are millimetres, matching the workshop domain (kerf, sheet
sizes, remnant thresholds) regardless of the document's own display unit —
the plugin converts from IngeTrazo's internal metres at the boundary.
"""
from __future__ import annotations

import functools
import itertools
import uuid
from dataclasses import dataclass, field
from enum import Enum

EPSILON = 0.0001


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class GrainAxis(Enum):
    NONE = "none"
    LENGTH = "length"
    WIDTH = "width"


class GrainRule(Enum):
    IRRELEVANT = "irrelevant"
    PREFERRED = "preferred"
    REQUIRED = "required"


class CutAxis(Enum):
    VERTICAL = "vertical"
    HORIZONTAL = "horizontal"


class _SortStrategy(Enum):
    LONGEST = "longest"
    AREA = "area"
    LENGTH = "length"
    WIDTH = "width"


class _SplitOrder(Enum):
    VERTICAL_FIRST = "vertical_first"
    HORIZONTAL_FIRST = "horizontal_first"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Rect2D:
    x: float
    y: float
    width: float
    height: float

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    @property
    def max_x(self) -> float:
        return self.x + self.width

    @property
    def max_y(self) -> float:
        return self.y + self.height


@dataclass
class ImportedPart:
    """One CSV/scene row: a part type that may repeat via ``quantity``."""
    id: str
    description: str
    length: float
    width: float
    thickness: float
    quantity: int


@dataclass
class PartSettings:
    material: str = "Unassigned"
    grain_axis: GrainAxis = GrainAxis.NONE
    grain_rule: GrainRule = GrainRule.IRRELEVANT
    allow_rotation: bool = True


@dataclass
class StockDefinition:
    name: str
    material: str
    thickness: float
    length: float
    width: float
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    #: Sheets on hand. ``None`` = the planner opens as many as the parts
    #: need — the normal case when buying new material.
    available_sheets: int | None = None
    grain_axis: GrainAxis = GrainAxis.NONE


@dataclass
class WorkshopSettings:
    kerf: float = 3
    short_edge_cleanup: float = 3
    long_edge_trim: float = 10
    minimum_remnant_width: float = 100
    minimum_remnant_length: float = 300


@dataclass
class CutListProject:
    name: str = "Untitled Project"
    parts: list[ImportedPart] = field(default_factory=list)
    part_settings: dict[str, PartSettings] = field(default_factory=dict)
    stocks: list[StockDefinition] = field(default_factory=list)
    workshop: WorkshopSettings = field(default_factory=WorkshopSettings)


@dataclass
class PartInstance:
    """A physical piece to cut — one expansion of an ``ImportedPart``'s
    ``quantity`` (``id`` is e.g. ``"SIDE-1"``, or bare ``"SIDE"`` if qty==1)."""
    id: str
    source_part_id: str
    description: str
    length: float
    width: float
    thickness: float
    settings: PartSettings


@dataclass
class Placement:
    instance: PartInstance
    rect: Rect2D
    rotated: bool

    @property
    def id(self) -> str:
        return self.instance.id


@dataclass
class RemainderLeaf:
    rect: Rect2D
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class SplitSpec:
    axis: CutAxis
    coordinate: float
    kerf: float
    workpiece: Rect2D
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class CuttingNode:
    """Tagged union mirroring Swift's ``indirect enum CuttingNode`` — one of
    ``part`` (leaf), ``remainder`` (leaf), or ``split`` (internal node with a
    ``low``/``high`` child pair). Use the factory classmethods, not the
    constructor directly."""
    kind: str
    placement: Placement | None = None
    leaf: RemainderLeaf | None = None
    split: SplitSpec | None = None
    low: "CuttingNode | None" = None
    high: "CuttingNode | None" = None

    @classmethod
    def part(cls, placement: Placement) -> "CuttingNode":
        return cls(kind="part", placement=placement)

    @classmethod
    def remainder(cls, leaf: RemainderLeaf) -> "CuttingNode":
        return cls(kind="remainder", leaf=leaf)

    @classmethod
    def split_node(cls, split: SplitSpec, low: "CuttingNode", high: "CuttingNode") -> "CuttingNode":
        return cls(kind="split", split=split, low=low, high=high)


@dataclass
class OrderedCut:
    number: int
    split: SplitSpec

    @property
    def id(self) -> str:
        return self.split.id


@dataclass
class SheetPlan:
    stock: StockDefinition
    sheet_number: int
    usable_rect: Rect2D
    root: CuttingNode
    placements: list[Placement]
    remnants: list[Rect2D]
    cuts: list[OrderedCut]
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class PlanMetrics:
    sheet_count: int
    cut_count: int
    part_area: float
    usable_area: float
    reusable_remnant_area: float
    scrap_area: float
    largest_remnant_area: float

    @property
    def utilization(self) -> float:
        return self.part_area / self.usable_area if self.usable_area > 0 else 0.0


@dataclass
class CuttingPlan:
    sheets: list[SheetPlan]
    metrics: PlanMetrics
    warnings: list[str] = field(default_factory=list)

    def position(self, sheet: SheetPlan) -> int:
        """1-based position across the WHOLE plan — ``sheet.sheet_number``
        counts per stock, so two sheets of different materials can both be
        number 1."""
        for i, s in enumerate(self.sheets):
            if s.id == sheet.id:
                return i + 1
        return sheet.sheet_number

    def label(self, sheet: SheetPlan) -> str:
        """Globally unique, human-readable name for a sheet."""
        thickness = sheet.stock.thickness
        if abs(round(thickness) - thickness) < 0.05:
            mm = str(int(round(thickness)))
        else:
            mm = f"{thickness:.1f}"
        return (f"Sheet {self.position(sheet)} of {len(self.sheets)} — "
                f"{sheet.stock.name} · {mm} mm")


class CutListError(Exception):
    """One error kind per Swift ``CutListError`` case; ``kind`` lets a
    caller branch on category while ``str(exc)`` carries the same wording."""

    def __init__(self, kind: str, message: str) -> None:
        self.kind = kind
        super().__init__(message)

    @classmethod
    def missing_settings(cls, part_id: str) -> "CutListError":
        return cls("missing_settings", f"Part {part_id} has no material assignment.")

    @classmethod
    def no_matching_stock(cls, detail: str) -> "CutListError":
        return cls("no_matching_stock", f"No matching stock for {detail}.")

    @classmethod
    def insufficient_stock(cls, detail: str) -> "CutListError":
        return cls("insufficient_stock", f"Not enough stock: {detail}")

    @classmethod
    def part_too_large(cls, detail: str) -> "CutListError":
        return cls("part_too_large", f"Part too large: {detail}")

    @classmethod
    def invalid_geometry(cls, detail: str) -> "CutListError":
        return cls("invalid_geometry", f"Invalid cutting geometry: {detail}.")


# ---------------------------------------------------------------------------
# Cutting-tree walk helpers (module-level: they only touch CuttingNode)
# ---------------------------------------------------------------------------

def collect_leaves(node: CuttingNode) -> list[RemainderLeaf]:
    if node.kind == "part":
        return []
    if node.kind == "remainder":
        return [node.leaf]
    return collect_leaves(node.low) + collect_leaves(node.high)


def collect_placements(node: CuttingNode) -> list[Placement]:
    if node.kind == "part":
        return [node.placement]
    if node.kind == "remainder":
        return []
    return collect_placements(node.low) + collect_placements(node.high)


def collect_remainders(node: CuttingNode) -> list[Rect2D]:
    if node.kind == "part":
        return []
    if node.kind == "remainder":
        return [node.leaf.rect]
    return collect_remainders(node.low) + collect_remainders(node.high)


def collect_cuts(node: CuttingNode) -> list[SplitSpec]:
    if node.kind in ("part", "remainder"):
        return []
    return [node.split] + collect_cuts(node.low) + collect_cuts(node.high)


def _replacing_leaf(node: CuttingNode, leaf_id: str, replacement: CuttingNode) -> CuttingNode:
    if node.kind == "part":
        return node
    if node.kind == "remainder":
        return replacement if node.leaf.id == leaf_id else node
    return CuttingNode.split_node(
        node.split,
        _replacing_leaf(node.low, leaf_id, replacement),
        _replacing_leaf(node.high, leaf_id, replacement),
    )


def _contains(outer: Rect2D, inner: Rect2D) -> bool:
    return (inner.x >= outer.x - EPSILON and inner.y >= outer.y - EPSILON
            and inner.max_x <= outer.max_x + EPSILON
            and inner.max_y <= outer.max_y + EPSILON)


def _overlaps(lhs: Rect2D, rhs: Rect2D) -> bool:
    return (lhs.x < rhs.max_x - EPSILON and lhs.max_x > rhs.x + EPSILON
            and lhs.y < rhs.max_y - EPSILON and lhs.max_y > rhs.y + EPSILON)


# ---------------------------------------------------------------------------
# Planner-internal helper structs
# ---------------------------------------------------------------------------

@dataclass
class _SheetState:
    stock: StockDefinition
    sheet_number: int
    usable_rect: Rect2D
    root: CuttingNode


@dataclass
class _LeafCandidate:
    sheet_index: int
    leaf: RemainderLeaf
    rotated: bool
    order: _SplitOrder
    cost: float


@dataclass
class _RowCandidate:
    sheet_index: int
    leaf: RemainderLeaf
    count: int
    node: CuttingNode
    cost: float


def _candidate_cmp(lhs: _LeafCandidate, rhs: _LeafCandidate) -> int:
    if abs(lhs.cost - rhs.cost) > EPSILON:
        return -1 if lhs.cost < rhs.cost else 1
    if lhs.sheet_index != rhs.sheet_index:
        return -1 if lhs.sheet_index < rhs.sheet_index else 1
    if abs(lhs.leaf.rect.y - rhs.leaf.rect.y) > EPSILON:
        return -1 if lhs.leaf.rect.y < rhs.leaf.rect.y else 1
    if abs(lhs.leaf.rect.x - rhs.leaf.rect.x) > EPSILON:
        return -1 if lhs.leaf.rect.x < rhs.leaf.rect.x else 1
    if lhs.leaf.id == rhs.leaf.id:
        return 0
    return -1 if lhs.leaf.id < rhs.leaf.id else 1


_CANDIDATE_KEY = functools.cmp_to_key(_candidate_cmp)


# ---------------------------------------------------------------------------
# The planner
# ---------------------------------------------------------------------------

#: Reward for consuming a leaf whose height or width already equals the
#: part, which means no trim strip is produced. Sized to compete with the
#: largest-remnant term.
_BAND_FIT_BONUS = 150_000.0


class GuillotinePlanner:
    """Stateless — safe to reuse across calls."""

    def generate(self, project: CutListProject) -> CuttingPlan:
        strategies = [_SortStrategy.LONGEST, _SortStrategy.AREA,
                      _SortStrategy.LENGTH, _SortStrategy.WIDTH]
        plans: list[CuttingPlan] = []
        last_error: CutListError | None = None
        for strategy in strategies:
            try:
                plans.append(self._build(project, strategy))
            except CutListError as exc:
                last_error = exc
        if not plans:
            raise last_error or CutListError.invalid_geometry("No plan could be generated")
        best = min(plans, key=self._score)
        self.validate(best)
        return best

    def validate(self, plan: CuttingPlan) -> None:
        for sheet in plan.sheets:
            for placement in sheet.placements:
                if not _contains(sheet.usable_rect, placement.rect):
                    raise CutListError.invalid_geometry(
                        f"{placement.id} lies outside sheet {sheet.sheet_number}")
            for i, first in enumerate(sheet.placements):
                for second in sheet.placements[i + 1:]:
                    if _overlaps(first.rect, second.rect):
                        raise CutListError.invalid_geometry(
                            f"{first.id} overlaps {second.id}")
            self._validate_node(sheet.root)

    # -- build ---------------------------------------------------------

    def _build(self, project: CutListProject, strategy: _SortStrategy) -> CuttingPlan:
        instances = self._make_instances(project)
        instances.sort(key=lambda inst: (-self._sort_key(inst, strategy), inst.id))

        states: list[_SheetState] = []
        used_counts: dict[str, int] = {}

        index = 0
        while index < len(instances):
            # Identical parts are placed as a group so their band trim is
            # shared instead of producing one captured offcut per part.
            run_end = index + 1
            while (run_end < len(instances)
                   and self._same_footprint(instances[run_end], instances[index])):
                run_end += 1
            pending = instances[index:run_end]

            while pending:
                head = pending[0]
                singles = self._placement_candidates(head, states, project.workshop.kerf)
                best_single = min(singles, key=_CANDIDATE_KEY) if singles else None
                # A leaf whose height already equals the part continues an
                # open band, which beats starting a new one.
                continues_band = False
                if best_single is not None:
                    _, h = self._dimensions(head, best_single.rotated)
                    continues_band = abs(best_single.leaf.rect.height - h) < EPSILON
                row = (self._best_row(pending, states, project.workshop.kerf)
                       if len(pending) >= 2 else None)

                if not continues_band and row is not None:
                    states[row.sheet_index].root = _replacing_leaf(
                        states[row.sheet_index].root, row.leaf.id, row.node)
                    pending = pending[row.count:]
                elif best_single is not None:
                    subtree = self._make_subtree(
                        head, best_single.leaf.rect, best_single.rotated,
                        best_single.order, project.workshop.kerf)
                    states[best_single.sheet_index].root = _replacing_leaf(
                        states[best_single.sheet_index].root,
                        best_single.leaf.id, subtree)
                    pending = pending[1:]
                else:
                    stock = self._open_stock(head, project, used_counts)
                    rect = self._usable_rect(stock, project.workshop)
                    states.append(_SheetState(
                        stock=stock, sheet_number=used_counts.get(stock.id, 1),
                        usable_rect=rect, root=CuttingNode.remainder(RemainderLeaf(rect=rect))))
                    if not self._placement_candidates(head, states, project.workshop.kerf):
                        raise CutListError.insufficient_stock(
                            f"no legal position remains for {head.id}.")
            index = run_end

        sheets = [self._make_sheet(stock=s.stock, sheet_number=s.sheet_number,
                                    usable_rect=s.usable_rect, root=s.root)
                  for s in states]
        metrics = self._make_metrics(sheets, project.workshop)
        return CuttingPlan(sheets=sheets, metrics=metrics)

    def _make_instances(self, project: CutListProject) -> list[PartInstance]:
        result: list[PartInstance] = []
        for part in project.parts:
            settings = project.part_settings.get(part.id)
            if settings is None or settings.material == "Unassigned" or not settings.material:
                raise CutListError.missing_settings(part.id)
            for number in range(1, part.quantity + 1):
                result.append(PartInstance(
                    id=part.id if part.quantity == 1 else f"{part.id}-{number}",
                    source_part_id=part.id, description=part.description,
                    length=part.length, width=part.width, thickness=part.thickness,
                    settings=settings))
        return result

    def _same_footprint(self, lhs: PartInstance, rhs: PartInstance) -> bool:
        return (abs(lhs.length - rhs.length) < EPSILON
                and abs(lhs.width - rhs.width) < EPSILON
                and abs(lhs.thickness - rhs.thickness) < EPSILON
                and lhs.settings == rhs.settings)

    # -- stock selection -------------------------------------------------

    def _open_stock(self, instance: PartInstance, project: CutListProject,
                     used_counts: dict[str, int]) -> StockDefinition:
        workshop = project.workshop
        matching = [s for s in project.stocks if self._stock_matches(s, instance)]
        if not matching:
            raise CutListError.no_matching_stock(
                f"{instance.settings.material}, {instance.thickness:.1f} mm")
        orientable = [s for s in matching if self._orientations(instance, s)]
        if not orientable:
            raise CutListError.no_matching_stock(
                f"{instance.id}: its required grain direction, because no "
                f"{instance.settings.material} sheet declares a grain axis. "
                "Set the sheet grain in Stock & Setup, or relax the grain "
                "rule to Preferred")
        roomiest = max(orientable, key=lambda s: self._usable_area(s, workshop))
        fitting = [s for s in orientable if self._fits(instance, s, workshop)]
        if not fitting:
            usable = self._usable_rect(roomiest, workshop)
            raise CutListError.part_too_large(
                f"{instance.id} is {instance.length:.1f} × {instance.width:.1f} mm, "
                f"but the largest usable area of {roomiest.name} is "
                f"{usable.width:.1f} × {usable.height:.1f} mm after edge cleanup.")
        available = sorted(
            (s for s in fitting
             if s.available_sheets is None or used_counts.get(s.id, 0) < s.available_sheets),
            key=lambda s: self._usable_area(s, workshop))
        if not available:
            limits = ", ".join(
                f"{s.name} ({s.available_sheets if s.available_sheets is not None else 'unlimited'})"
                for s in fitting)
            raise CutListError.insufficient_stock(
                f"{instance.id} needs one more sheet, but every sheet limit "
                f"is used up: {limits}. Raise the limit or switch it off in "
                "Stock & Setup.")
        stock = available[0]
        used_counts[stock.id] = used_counts.get(stock.id, 0) + 1
        return stock

    def _usable_rect(self, stock: StockDefinition, workshop: WorkshopSettings) -> Rect2D:
        return Rect2D(x=0.0, y=0.0,
                      width=max(0.0, stock.length - workshop.short_edge_cleanup),
                      height=max(0.0, stock.width - workshop.long_edge_trim))

    def _usable_area(self, stock: StockDefinition, workshop: WorkshopSettings) -> float:
        return self._usable_rect(stock, workshop).area

    def _fits(self, instance: PartInstance, stock: StockDefinition,
              workshop: WorkshopSettings) -> bool:
        usable = self._usable_rect(stock, workshop)
        for rotated in self._orientations(instance, stock):
            w, h = self._dimensions(instance, rotated)
            if w <= usable.width + EPSILON and h <= usable.height + EPSILON:
                return True
        return False

    def _stock_matches(self, stock: StockDefinition, instance: PartInstance) -> bool:
        return (stock.material.casefold() == instance.settings.material.casefold()
                and abs(stock.thickness - instance.thickness) < 0.05)

    # -- banded (identical-part) placement --------------------------------

    def _best_row(self, queue: list[PartInstance], states: list[_SheetState],
                  kerf: float) -> _RowCandidate | None:
        if not queue:
            return None
        head = queue[0]
        best: _RowCandidate | None = None
        for sheet_index, state in enumerate(states):
            if not self._stock_matches(state.stock, head):
                continue
            for leaf in collect_leaves(state.root):
                for rotated in self._orientations(head, state.stock):
                    node, count = self._make_row(queue, leaf.rect, rotated, kerf)
                    if count < 2:
                        continue
                    cost = self._layout_cost(node, head, state.stock, rotated, leaf.rect)
                    if (best is None or count > best.count
                            or (count == best.count and cost < best.cost)):
                        best = _RowCandidate(sheet_index=sheet_index, leaf=leaf,
                                              count=count, node=node, cost=cost)
        return best

    def _make_row(self, queue: list[PartInstance], leaf: Rect2D, rotated: bool,
                  kerf: float) -> tuple[CuttingNode, int]:
        if not queue:
            return CuttingNode.remainder(RemainderLeaf(rect=leaf)), 0
        head = queue[0]
        w, h = self._dimensions(head, rotated)
        if w > leaf.width + EPSILON or h > leaf.height + EPSILON:
            return CuttingNode.remainder(RemainderLeaf(rect=leaf)), 0
        band = Rect2D(x=leaf.x, y=leaf.y, width=leaf.width, height=h)
        filled_node, filled_count = self._fill_band(queue, band, rotated, kerf)
        excess = leaf.height - h
        if excess <= EPSILON:
            return filled_node, filled_count
        above = Rect2D(x=leaf.x, y=leaf.y + h + kerf, width=leaf.width,
                        height=max(0.0, excess - kerf))
        split = SplitSpec(axis=CutAxis.HORIZONTAL, coordinate=leaf.y + h,
                           kerf=kerf, workpiece=leaf)
        return (CuttingNode.split_node(split, filled_node, CuttingNode.remainder(RemainderLeaf(rect=above))),
                filled_count)

    def _fill_band(self, queue: list[PartInstance], band: Rect2D, rotated: bool,
                   kerf: float) -> tuple[CuttingNode, int]:
        if not queue:
            return CuttingNode.remainder(RemainderLeaf(rect=band)), 0
        head = queue[0]
        w, h = self._dimensions(head, rotated)
        if w > band.width + EPSILON or h > band.height + EPSILON:
            return CuttingNode.remainder(RemainderLeaf(rect=band)), 0
        placement = Placement(instance=head,
                               rect=Rect2D(x=band.x, y=band.y, width=w, height=h),
                               rotated=rotated)
        excess = band.width - w
        if excess <= EPSILON:
            return CuttingNode.part(placement), 1
        rest = Rect2D(x=band.x + w + kerf, y=band.y, width=max(0.0, excess - kerf),
                      height=band.height)
        split = SplitSpec(axis=CutAxis.VERTICAL, coordinate=band.x + w, kerf=kerf,
                          workpiece=band)
        tail_node, tail_count = self._fill_band(queue[1:], rest, rotated, kerf)
        return CuttingNode.split_node(split, CuttingNode.part(placement), tail_node), 1 + tail_count

    # -- single-part placement --------------------------------------------

    def _placement_candidates(self, instance: PartInstance, states: list[_SheetState],
                               kerf: float) -> list[_LeafCandidate]:
        result: list[_LeafCandidate] = []
        for sheet_index, state in enumerate(states):
            if not self._stock_matches(state.stock, instance):
                continue
            for leaf in collect_leaves(state.root):
                for rotated in self._orientations(instance, state.stock):
                    w, h = self._dimensions(instance, rotated)
                    if w > leaf.rect.width + EPSILON or h > leaf.rect.height + EPSILON:
                        continue
                    for order in (_SplitOrder.VERTICAL_FIRST, _SplitOrder.HORIZONTAL_FIRST):
                        node = self._make_subtree(instance, leaf.rect, rotated, order, kerf)
                        cost = self._layout_cost(node, instance, state.stock, rotated, leaf.rect)
                        result.append(_LeafCandidate(sheet_index=sheet_index, leaf=leaf,
                                                      rotated=rotated, order=order, cost=cost))
        return result

    def _orientations(self, instance: PartInstance, stock: StockDefinition) -> list[bool]:
        options = [False, True] if instance.settings.allow_rotation else [False]
        settings = instance.settings
        if settings.grain_rule != GrainRule.REQUIRED or settings.grain_axis == GrainAxis.NONE:
            return options
        if stock.grain_axis == GrainAxis.NONE:
            return []
        return [r for r in options
                if self._effective_grain(settings.grain_axis, r) == stock.grain_axis]

    def _preferred_grain_penalty(self, instance: PartInstance, stock: StockDefinition,
                                  rotated: bool) -> float:
        settings = instance.settings
        if (settings.grain_rule != GrainRule.PREFERRED or settings.grain_axis == GrainAxis.NONE
                or stock.grain_axis == GrainAxis.NONE):
            return 0.0
        return 0.0 if self._effective_grain(settings.grain_axis, rotated) == stock.grain_axis else 1_000_000.0

    def _effective_grain(self, axis: GrainAxis, rotated: bool) -> GrainAxis:
        if not rotated:
            return axis
        return {GrainAxis.LENGTH: GrainAxis.WIDTH,
                GrainAxis.WIDTH: GrainAxis.LENGTH,
                GrainAxis.NONE: GrainAxis.NONE}[axis]

    def _dimensions(self, instance: PartInstance, rotated: bool) -> tuple[float, float]:
        return (instance.width, instance.length) if rotated else (instance.length, instance.width)

    def _make_subtree(self, instance: PartInstance, leaf: Rect2D, rotated: bool,
                       order: _SplitOrder, kerf: float) -> CuttingNode:
        w, h = self._dimensions(instance, rotated)
        placement = Placement(instance=instance,
                               rect=Rect2D(x=leaf.x, y=leaf.y, width=w, height=h),
                               rotated=rotated)

        def horizontal(rect: Rect2D, low: CuttingNode) -> CuttingNode:
            excess = rect.height - h
            if excess <= EPSILON:
                return low
            high = Rect2D(x=rect.x, y=rect.y + h + kerf, width=rect.width,
                          height=max(0.0, excess - kerf))
            split = SplitSpec(axis=CutAxis.HORIZONTAL, coordinate=rect.y + h,
                              kerf=kerf, workpiece=rect)
            return CuttingNode.split_node(split, low, CuttingNode.remainder(RemainderLeaf(rect=high)))

        def vertical(rect: Rect2D, low: CuttingNode) -> CuttingNode:
            excess = rect.width - w
            if excess <= EPSILON:
                return low
            high = Rect2D(x=rect.x + w + kerf, y=rect.y, width=max(0.0, excess - kerf),
                          height=rect.height)
            split = SplitSpec(axis=CutAxis.VERTICAL, coordinate=rect.x + w,
                              kerf=kerf, workpiece=rect)
            return CuttingNode.split_node(split, low, CuttingNode.remainder(RemainderLeaf(rect=high)))

        if order == _SplitOrder.VERTICAL_FIRST:
            left = Rect2D(x=leaf.x, y=leaf.y, width=w, height=leaf.height)
            return vertical(leaf, horizontal(left, CuttingNode.part(placement)))
        bottom = Rect2D(x=leaf.x, y=leaf.y, width=leaf.width, height=h)
        return horizontal(leaf, vertical(bottom, CuttingNode.part(placement)))

    # -- cost / scoring ----------------------------------------------------

    def _layout_cost(self, node: CuttingNode, instance: PartInstance,
                      stock: StockDefinition, rotated: bool, leaf: Rect2D) -> float:
        remnants = collect_remainders(node)
        narrow = sum(r.area * 4 for r in remnants if min(r.width, r.height) < 80)
        fragments = float(sum(1 for r in remnants if r.area > EPSILON))
        largest = max((r.area for r in remnants), default=0.0)
        w, h = self._dimensions(instance, rotated)
        exact_height = abs(leaf.height - h) < EPSILON
        exact_width = abs(leaf.width - w) < EPSILON
        fit_bonus = (_BAND_FIT_BONUS if exact_height else 0.0) + (_BAND_FIT_BONUS if exact_width else 0.0)
        return (narrow + fragments * 2_000 + len(collect_cuts(node)) * 500
                - largest * 0.05 - fit_bonus
                + self._preferred_grain_penalty(instance, stock, rotated))

    def _score(self, plan: CuttingPlan) -> float:
        m = plan.metrics
        return (m.sheet_count * 1_000_000_000_000.0
                + m.scrap_area * 10 + m.cut_count * 2_000 - m.largest_remnant_area)

    def _sort_key(self, instance: PartInstance, strategy: _SortStrategy) -> float:
        if strategy == _SortStrategy.LONGEST:
            return max(instance.length, instance.width) * 1_000_000 + instance.length * instance.width
        if strategy == _SortStrategy.AREA:
            return instance.length * instance.width
        if strategy == _SortStrategy.LENGTH:
            return instance.length
        return instance.width

    # -- sheet / metrics assembly -------------------------------------------

    def _make_sheet(self, stock: StockDefinition, sheet_number: int, usable_rect: Rect2D,
                     root: CuttingNode) -> SheetPlan:
        placements = collect_placements(root)
        remnants = [r for r in collect_remainders(root) if r.area > EPSILON]
        cuts = [OrderedCut(number=i + 1, split=s) for i, s in enumerate(collect_cuts(root))]
        return SheetPlan(stock=stock, sheet_number=sheet_number, usable_rect=usable_rect,
                          root=root, placements=placements, remnants=remnants, cuts=cuts)

    def _is_reusable(self, rect: Rect2D, workshop: WorkshopSettings) -> bool:
        return (max(rect.width, rect.height) >= workshop.minimum_remnant_length
                and min(rect.width, rect.height) >= workshop.minimum_remnant_width)

    def _make_metrics(self, sheets: list[SheetPlan], workshop: WorkshopSettings) -> PlanMetrics:
        part_area = sum(p.rect.area for s in sheets for p in s.placements)
        usable_area = sum(s.usable_rect.area for s in sheets)
        all_remnants = [r for s in sheets for r in s.remnants]
        reusable = [r for r in all_remnants if self._is_reusable(r, workshop)]
        reusable_area = sum(r.area for r in reusable)
        terminal_area = sum(r.area for r in all_remnants)
        return PlanMetrics(
            sheet_count=len(sheets), cut_count=sum(len(s.cuts) for s in sheets),
            part_area=part_area, usable_area=usable_area, reusable_remnant_area=reusable_area,
            scrap_area=max(0.0, terminal_area - reusable_area),
            largest_remnant_area=max((r.area for r in reusable), default=0.0))

    # -- validation ----------------------------------------------------------

    def _validate_node(self, node: CuttingNode) -> list[Placement]:
        if node.kind == "part":
            return [node.placement]
        if node.kind == "remainder":
            return []
        split = node.split
        if split.axis == CutAxis.VERTICAL:
            if not (split.coordinate >= split.workpiece.x - EPSILON
                    and split.coordinate <= split.workpiece.max_x + EPSILON):
                raise CutListError.invalid_geometry("Vertical cut lies outside its active workpiece")
        else:
            if not (split.coordinate >= split.workpiece.y - EPSILON
                    and split.coordinate <= split.workpiece.max_y + EPSILON):
                raise CutListError.invalid_geometry("Horizontal cut lies outside its active workpiece")
        low_placements = self._validate_node(node.low)
        high_placements = self._validate_node(node.high)
        for placement in low_placements + high_placements:
            if split.axis == CutAxis.VERTICAL:
                crosses = (placement.rect.x < split.coordinate - EPSILON
                           and placement.rect.max_x > split.coordinate + EPSILON)
            else:
                crosses = (placement.rect.y < split.coordinate - EPSILON
                           and placement.rect.max_y > split.coordinate + EPSILON)
            if crosses:
                raise CutListError.invalid_geometry(f"Cut crosses the bounding box of {placement.id}")
        return low_placements + high_placements
