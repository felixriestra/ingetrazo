# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Parameter forms: unit-aware spin boxes and the operation form.

Values are held in millimetres (the engine's unit) and SHOWN in the job's
units: :class:`LengthSpin` and :class:`FeedSpin` convert on the way in and
out, so switching a job to inches re-labels every field without changing
a single stored number.

:class:`OperationForm` shows the fields that apply to the selected
operation's kind (rows are hidden, not rebuilt, when the kind changes) and
writes them back to the operation on every edit, then says so with
``changed`` — the dock persists and recalculates.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QLabel, QLineEdit,
                               QSpinBox, QWidget)

from ..engine.models import (FINISHING_TOOL_KINDS, MM_PER_INCH, Region, Tab)
from ..i18n import tr

def tool_kind_label(kind: str) -> str:
    return {
        "flatEndMill": tr("Flat end mill"),
        "ballEndMill": tr("Ball end mill"),
        "bullNoseEndMill": tr("Bull-nose end mill"),
        "chamferMill": tr("Chamfer mill"),
        "drill": tr("Drill"),
        "spotDrill": tr("Spot drill"),
    }.get(kind, kind)


def kind_label(kind: str) -> str:
    return {
        "outsideProfile": tr("Outside profile"),
        "insideProfile": tr("Inside profile"),
        "pocket": tr("Pocket"),
        "drilling": tr("Drilling"),
        "engraving": tr("Engraving"),
        "facing": tr("Facing"),
        "bore": tr("Bore"),
        "slot": tr("Slot"),
        "chamfer": tr("Chamfer"),
        "openPocket": tr("Open pocket"),
    }.get(kind, kind)


#: Width a number box asks for. Qt sizes a spin box to fit its LARGEST
#: value with suffix ("10000.000 mm"), which made every form in the dock
#: wide; real values are short, and the box still grows with the dock.
COMPACT_WIDTH = 84


class _Compact:
    """Mixin: a spin box that asks for :data:`COMPACT_WIDTH`, not for the
    width of its maximum value, and grows to the space it is given."""

    def sizeHint(self):  # noqa: N802 — Qt override
        s = super().sizeHint()
        s.setWidth(min(s.width(), COMPACT_WIDTH))
        return s

    def minimumSizeHint(self):  # noqa: N802 — Qt override
        s = super().minimumSizeHint()
        s.setWidth(min(s.width(), COMPACT_WIDTH))
        return s


class CompactSpin(_Compact, QSpinBox):
    """A whole-number box of compact width."""


class CompactDouble(_Compact, QDoubleSpinBox):
    """A decimal box of compact width (for values that are not lengths)."""


class LengthSpin(_Compact, QDoubleSpinBox):
    """A length in millimetres, shown in mm or inches."""

    def __init__(self, maximum_mm: float = 10_000.0, minimum_mm: float = 0.0, parent=None):
        super().__init__(parent)
        self._inch = False
        self._max, self._min = maximum_mm, minimum_mm
        self.setKeyboardTracking(False)
        self.set_inch(False)

    def set_inch(self, inch: bool) -> None:
        mm = self.mm()
        self._inch = inch
        self.blockSignals(True)
        self.setDecimals(4 if inch else 3)
        scale = 1 / MM_PER_INCH if inch else 1.0
        self.setRange(self._min * scale, self._max * scale)
        self.setSingleStep(0.01 if inch else 0.5)
        self.setSuffix(" in" if inch else " mm")
        self.setValue(mm * scale)
        self.blockSignals(False)

    def mm(self) -> float:
        return self.value() * (MM_PER_INCH if self._inch else 1.0)

    def set_mm(self, value: float) -> None:
        self.blockSignals(True)
        self.setValue(value / MM_PER_INCH if self._inch else value)
        self.blockSignals(False)


class FeedSpin(_Compact, QDoubleSpinBox):
    """A feed in mm/min, shown in mm/min or in/min."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._inch = False
        self.setKeyboardTracking(False)
        self.set_inch(False)

    def set_inch(self, inch: bool) -> None:
        mm = self.mm()
        self._inch = inch
        self.blockSignals(True)
        self.setDecimals(1 if inch else 0)
        self.setRange(0.0, 60_000.0 / (MM_PER_INCH if inch else 1.0))
        self.setSingleStep(1.0 if inch else 50.0)
        self.setSuffix(" in/min" if inch else " mm/min")
        self.setValue(mm / (MM_PER_INCH if inch else 1.0))
        self.blockSignals(False)

    def mm(self) -> float:
        return self.value() * (MM_PER_INCH if self._inch else 1.0)

    def set_mm(self, value: float) -> None:
        self.blockSignals(True)
        self.setValue(value / (MM_PER_INCH if self._inch else 1.0))
        self.blockSignals(False)


def compact_combo() -> QComboBox:
    """A combo that does not ask to be as wide as its longest item."""
    c = QComboBox()
    c.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    c.setMinimumContentsLength(8)
    return c


def _combo(items) -> QComboBox:
    c = compact_combo()
    for value, label in items:
        c.addItem(label, value)
    return c


def _set_combo(combo: QComboBox, value) -> None:
    i = combo.findData(value)
    combo.blockSignals(True)
    combo.setCurrentIndex(max(0, i))
    combo.blockSignals(False)


def _check_label(check: QCheckBox) -> QLabel:
    """Move a check box's text to a word-wrapping row label. A check box's
    own text is one line: «Recorrido más corto entre agujeros» alone made
    the form 260 px wide. Clicking the label still toggles the box."""
    label = QLabel(check.text())
    label.setWordWrap(True)
    label.setToolTip(check.toolTip())
    check.setText("")
    label.mousePressEvent = lambda _e: check.isEnabled() and check.toggle()
    return label


class OperationForm(QWidget):
    """Edits one :class:`~..engine.models.Operation` in place."""

    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.op = None
        self.job = None
        self._loading = False
        f = self.form = QFormLayout(self)
        f.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        # A narrow dock puts the field under its label instead of clipping it.
        f.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.name = QLineEdit()
        self.tool = compact_combo()
        self.finishing_tool = compact_combo()
        self.depth = LengthSpin(1000.0)
        self.step_down = LengthSpin(1000.0)
        self.stepover = CompactSpin()
        self.stepover.setRange(5, 100)
        self.stepover.setSuffix(" %")
        self.allowance = LengthSpin(50.0)
        self.direction = _combo([("climb", tr("Climb")), ("conventional", tr("Conventional"))])
        self.entry = _combo([("plunge", tr("Plunge")), ("ramp", tr("Ramp")),
                             ("helix", tr("Helix"))])
        self.compensation = _combo([("computer", tr("In the program")),
                                    ("controller", tr("By the controller"))])
        self.compensation.setToolTip(tr("By the controller: G41/G42, LinuxCNC only."))
        self.finishing = CompactSpin()
        self.finishing.setRange(0, 5)
        self.lead_in = LengthSpin(100.0)
        self.lead_out = LengthSpin(100.0)
        self.tabs = CompactSpin()
        self.tabs.setRange(0, 16)
        self.tab_width = LengthSpin(200.0)
        self.tab_height = LengthSpin(100.0)
        self.peck = LengthSpin(200.0)
        self.peck.setToolTip(tr("0 drills in one plunge."))
        self.dwell = CompactDouble()
        self.dwell.setRange(0.0, 60.0)
        self.dwell.setDecimals(2)
        self.dwell.setSuffix(" s")
        self.nearest = QCheckBox(tr("Shortest route between holes"))
        self.closed = QCheckBox(tr("Closed path"))
        self.bore_diameter = LengthSpin(2000.0)
        self.width = LengthSpin(2000.0)
        self.inside = QCheckBox(tr("On a hole's edge"))
        self.open_edges = QLineEdit()
        self.open_edges.setPlaceholderText(tr("none"))
        self.open_edges.setToolTip(tr(
            "Edges the cutter may pass beyond (the board's edge), as numbers counted "
            "from the outline's first corner: 1, 3"))

        self._rows = [
            ("name", tr("Name"), self.name),
            ("tool", tr("Tool"), self.tool),
            ("finishing_tool", tr("Finishing tool"), self.finishing_tool),
            ("depth", tr("Depth"), self.depth),
            ("step_down", tr("Step-down"), self.step_down),
            ("stepover", tr("Stepover"), self.stepover),
            ("allowance", tr("Stock to leave"), self.allowance),
            ("direction", tr("Direction"), self.direction),
            ("entry", tr("Entry"), self.entry),
            ("compensation", tr("Tool radius offset"), self.compensation),
            ("finishing", tr("Finishing passes"), self.finishing),
            ("lead_in", tr("Lead-in"), self.lead_in),
            ("lead_out", tr("Lead-out"), self.lead_out),
            ("tabs", tr("Tabs"), self.tabs),
            ("tab_width", tr("Tab width"), self.tab_width),
            ("tab_height", tr("Tab height"), self.tab_height),
            ("peck", tr("Peck depth"), self.peck),
            ("dwell", tr("Dwell at the bottom"), self.dwell),
            ("nearest", "", self.nearest),
            ("closed", "", self.closed),
            ("bore_diameter", tr("Hole diameter"), self.bore_diameter),
            ("width", tr("Width"), self.width),
            ("inside", "", self.inside),
            ("open_edges", tr("Open edges"), self.open_edges),
        ]
        for _key, label, w in self._rows:
            if isinstance(w, QCheckBox) and not label:
                label = _check_label(w)
            f.addRow(label, w)
        self.name.editingFinished.connect(self._apply)
        self.open_edges.editingFinished.connect(self._apply)
        for w in (self.bore_diameter, self.width):
            w.valueChanged.connect(self._apply)
        self.inside.toggled.connect(self._apply)
        for w in (self.tool, self.finishing_tool, self.direction, self.entry, self.compensation):
            w.currentIndexChanged.connect(self._apply)
        for w in (self.depth, self.step_down, self.allowance, self.lead_in, self.lead_out,
                  self.tab_width, self.tab_height, self.peck, self.dwell):
            w.valueChanged.connect(self._apply)
        for w in (self.stepover, self.finishing, self.tabs):
            w.valueChanged.connect(self._apply)
        for w in (self.nearest, self.closed):
            w.toggled.connect(self._apply)
        self.setEnabled(False)

    # ---- which rows apply ------------------------------------------------------
    def _visible(self, kind: str) -> set:
        rows = {"name", "tool", "depth"}
        if kind in ("outsideProfile", "insideProfile"):
            rows |= {"finishing_tool", "step_down", "allowance", "direction", "entry",
                     "compensation", "finishing", "lead_in", "lead_out", "tabs", "tab_width",
                     "tab_height"}
        elif kind in ("pocket", "openPocket"):
            rows |= {"finishing_tool", "step_down", "stepover", "allowance", "direction",
                     "entry", "finishing"}
            if kind == "openPocket":
                rows -= {"allowance"}
                rows |= {"open_edges"}
        elif kind == "bore":
            rows |= {"bore_diameter", "step_down"}
        elif kind == "slot":
            rows |= {"width", "step_down", "stepover"}
        elif kind == "chamfer":
            rows |= {"width", "direction", "finishing", "inside"}
        elif kind == "facing":
            rows |= {"stepover", "direction", "entry"}
        elif kind == "drilling":
            rows |= {"peck", "dwell", "nearest"}
        elif kind == "engraving":
            rows |= {"step_down", "closed"}
        return rows

    def set_units(self, inch: bool) -> None:
        for w in (self.depth, self.step_down, self.allowance, self.lead_in, self.lead_out,
                  self.tab_width, self.tab_height, self.peck, self.bore_diameter, self.width):
            w.set_inch(inch)

    def set_operation(self, op, job) -> None:
        self.op, self.job = op, job
        self._loading = True
        try:
            self.setEnabled(op is not None)
            if op is None:
                return
            visible = self._visible(op.kind)
            for key, _label, w in self._rows:
                self.form.setRowVisible(w, key in visible)
            self.name.setText(op.name)
            self._fill_tools(self.tool, op.toolID, allow_same=False)
            self._fill_tools(self.finishing_tool, op.finishingToolID, allow_same=True)
            p, st = op.parameters, op.strategy
            if hasattr(p, "depth"):
                self.depth.set_mm(p.depth)
            if hasattr(p, "stepDown"):
                self.step_down.set_mm(p.stepDown)
            if hasattr(p, "stepoverFraction"):
                self.stepover.setValue(int(round(p.stepoverFraction * 100)))
            self.allowance.set_mm(getattr(p, "stockAllowance", 0.0))
            _set_combo(self.direction, st.direction)
            _set_combo(self.entry, st.entry)
            _set_combo(self.compensation, "controller" if st.compensation == "controller"
                       else "computer")
            self.finishing.setValue(max(0, st.finishingPasses))
            self.lead_in.set_mm(st.leadInLength)
            self.lead_out.set_mm(st.leadOutLength)
            self.tabs.setValue(len(st.tabs))
            if st.tabs:
                self.tab_width.set_mm(st.tabs[0].width)
                self.tab_height.set_mm(st.tabs[0].height)
            else:
                self.tab_width.set_mm(8.0)
                self.tab_height.set_mm(3.0)
            self.peck.set_mm(getattr(p, "peckDepth", 0.0))
            self.dwell.setValue(getattr(p, "dwellSeconds", 0.0))
            self.nearest.setChecked(st.ordering == "nearestNeighbor")
            self.closed.setChecked(bool(getattr(p, "isClosed", False)))
            if hasattr(p, "diameter"):
                self.bore_diameter.set_mm(p.diameter)
            if hasattr(p, "width"):
                self.width.set_mm(p.width)
            self.inside.setChecked(bool(getattr(p, "inside", False)))
            g = st.geometry
            self.open_edges.setText(", ".join(str(i + 1) for i in sorted(g.openEdgeIndices))
                                    if isinstance(g, Region) else "")
        finally:
            self._loading = False

    def _fill_tools(self, combo, tool_id, allow_same) -> None:
        combo.blockSignals(True)
        combo.clear()
        if allow_same:
            combo.addItem(tr("Same as the tool"), None)
        for t in sorted(self.job.tools, key=lambda t: t.number):
            combo.addItem(f"T{t.number} · {t.name}", t.id)
        i = combo.findData(tool_id)
        combo.setCurrentIndex(max(0, i))
        combo.blockSignals(False)

    # ---- write back -------------------------------------------------------------
    def _apply(self, *_args) -> None:
        if self._loading or self.op is None:
            return
        op, p, st = self.op, self.op.parameters, self.op.strategy
        op.name = self.name.text().strip() or op.name
        op.toolID = self.tool.currentData()
        if op.kind in FINISHING_TOOL_KINDS:
            op.finishingToolID = self.finishing_tool.currentData()
        if hasattr(p, "depth"):
            p.depth = self.depth.mm()
        if hasattr(p, "stepDown"):
            p.stepDown = self.step_down.mm()
        if hasattr(p, "stepoverFraction"):
            p.stepoverFraction = self.stepover.value() / 100.0
        if hasattr(p, "stockAllowance"):
            p.stockAllowance = self.allowance.mm()
        st.direction = self.direction.currentData()
        st.entry = self.entry.currentData()
        st.compensation = self.compensation.currentData()
        st.finishingPasses = self.finishing.value()
        st.leadInLength = self.lead_in.mm()
        st.leadOutLength = self.lead_out.mm()
        n = self.tabs.value()
        if op.kind in ("outsideProfile", "insideProfile"):
            width, height = self.tab_width.mm(), self.tab_height.mm()
            old = st.tabs
            if len(old) != n or any(abs(t.width - width) > 1e-9 or abs(t.height - height) > 1e-9
                                    for t in old):
                st.tabs = [Tab((k + 0.5) / n, width, height) for k in range(n)] if n else []
        if hasattr(p, "peckDepth"):
            p.peckDepth = self.peck.mm()
            p.dwellSeconds = self.dwell.value()
            st.ordering = "nearestNeighbor" if self.nearest.isChecked() else "input"
        if hasattr(p, "isClosed"):
            p.isClosed = self.closed.isChecked()
        if hasattr(p, "diameter"):
            p.diameter = self.bore_diameter.mm()
        if hasattr(p, "width"):
            p.width = self.width.mm()
        if hasattr(p, "inside"):
            p.inside = self.inside.isChecked()
        if op.kind == "openPocket" and isinstance(st.geometry, Region):
            n = len(st.geometry.boundary)
            picked = set()
            for part in self.open_edges.text().replace(";", ",").split(","):
                part = part.strip()
                if part.isdigit() and 1 <= int(part) <= n:
                    picked.add(int(part) - 1)
            st.geometry.openEdgeIndices = sorted(picked)
            self.open_edges.setText(", ".join(str(i + 1) for i in sorted(picked)))
        self.changed.emit()


def region_of(op):
    g = op.strategy.geometry
    return g if isinstance(g, Region) else None
