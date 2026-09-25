# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The CAM dock: Job ▸ Tools ▸ Operations ▸ Output.

The dock owns a :class:`~..state.CamState` and keeps it and the document in
step, both ways:

- **dock → document.** Every edit marks the state dirty; 600 ms after the
  last one the whole state is written to ``scene.plugin_data["cam"]``
  through ``SetPluginData`` — ONE undo step per burst of edits, not one per
  keystroke — and the document is marked modified.
- **document → dock.** On every scene change the dock compares the
  document's ``plugin_data["cam"]`` with what it last wrote or read; when
  they differ (undo, redo, File ▸ Open, File ▸ New) it reloads.

Each edit also schedules a recalculation on a worker thread
(:mod:`.worker`), so the overlay follows the parameters. Export is
possible only from a current result whose verification has no errors, and
each posted file is read back and checked again before it is written.
"""
from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDockWidget, QFileDialog,
                               QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox,
                               QPushButton, QRadioButton, QScrollArea, QSpinBox, QTabWidget,
                               QToolButton, QVBoxLayout, QWidget)

from .. import build
from ..engine.issues import CamError, ERROR
from ..engine.models import Tool, new_id
from ..i18n import tr
from ..state import PLUGIN_KEY, CamState
from . import messages
from .op_forms import FeedSpin, LengthSpin, OperationForm, kind_label, tool_kind_label
from .overlay import ToolpathOverlay
from .worker import Calculation

DOCK_NAME = "cam_dock"
PERSIST_MS = 600
RECALC_MS = 350

#: The 9-point work zero, laid out as it looks from above.
ZERO_GRID = (("topLeft", "topCenter", "topRight"),
             ("centerLeft", "center", "centerRight"),
             ("bottomLeft", "bottomCenter", "bottomRight"))


def show_dock(viewport) -> "CamDock":
    """Open the CAM dock (creating it the first time) and return it."""
    win = viewport.window()
    dock = win.plugin_docks().get(DOCK_NAME) if hasattr(win, "plugin_docks") else None
    if dock is None:
        dock = CamDock(viewport, win)
    if hasattr(win, "add_plugin_dock"):
        dock = win.add_plugin_dock(dock)
    else:                                   # a host without H2: float it
        dock.show()
    return dock


def _scroll(widget: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QScrollArea.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    sa.setWidget(widget)
    return sa


class CamDock(QDockWidget):
    _calculated = Signal(object)            # object, not dict: see worker.py

    def __init__(self, viewport, parent=None) -> None:
        super().__init__(tr("CAM"), parent)
        self.setObjectName(DOCK_NAME)
        self.viewport = viewport
        self.state = CamState.new(translate=tr)
        self._last_doc = None                # the plugin_data dict last read or written
        self._loading = False
        self._generation = 0
        self._calc = None
        self._result = None                  # the latest worker.Outcome
        self._result_stale = True
        self._sim = None
        build.translate = tr
        self.overlay = ToolpathOverlay()
        self._calculated.connect(self._on_calculated, Qt.QueuedConnection)

        self._persist_timer = QTimer(self)
        self._persist_timer.setSingleShot(True)
        self._persist_timer.timeout.connect(self._persist)
        self._recalc_timer = QTimer(self)
        self._recalc_timer.setSingleShot(True)
        self._recalc_timer.timeout.connect(self.calculate)

        self._build()
        viewport.overlay_painters.append(self.overlay)
        viewport.sceneVersionChanged.connect(self._on_scene_changed)
        self.visibilityChanged.connect(self._on_visibility)
        self._reload_from_document(force=True)

    # ==== layout ==============================================================
    def _build(self) -> None:
        self.tabs = QTabWidget()
        self.tabs.addTab(_scroll(self._build_job()), tr("Job"))
        self.tabs.addTab(self._build_tools(), tr("Tools"))
        self.tabs.addTab(self._build_operations(), tr("Operations"))
        self.tabs.addTab(self._build_output(), tr("Output"))
        self.setWidget(self.tabs)

    # ---- Job ----
    def _build_job(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        self.btn_setup = QPushButton(tr("Set up from selection"))
        self.btn_setup.setToolTip(tr("Use the selected faces, edges or part to place the "
                                     "machining plane and size the stock."))
        self.btn_setup.clicked.connect(self._on_setup_from_selection)
        self.btn_part = QPushButton(tr("Part → operations"))
        self.btn_part.setToolTip(tr("Add every operation the selected part needs: pockets, "
                                    "holes and the cut-out with tabs."))
        self.btn_part.clicked.connect(self._on_part_operations)
        row.addWidget(self.btn_setup)
        row.addWidget(self.btn_part)
        lay.addLayout(row)
        self.btn_refresh = QPushButton(tr("Refresh from the part"))
        self.btn_refresh.setToolTip(tr("Read the part's outline and holes again after "
                                       "changing the model. Parameters are kept."))
        self.btn_refresh.clicked.connect(self._on_refresh)
        lay.addWidget(self.btn_refresh)

        box = QGroupBox(tr("Job"))
        f = QFormLayout(box)
        self.job_name = QLineEdit()
        self.job_name.editingFinished.connect(self._on_job_edited)
        f.addRow(tr("Name"), self.job_name)
        self.controller = QComboBox()
        self.controller.addItem("GRBL", "grbl")
        self.controller.addItem("LinuxCNC", "linuxcnc")
        self.controller.currentIndexChanged.connect(self._on_job_edited)
        f.addRow(tr("Controller"), self.controller)
        self.units = QComboBox()
        self.units.addItem(tr("Millimetres"), "millimeters")
        self.units.addItem(tr("Inches"), "inches")
        self.units.currentIndexChanged.connect(self._on_units_changed)
        f.addRow(tr("Units"), self.units)
        lay.addWidget(box)

        box = QGroupBox(tr("Stock"))
        f = QFormLayout(box)
        self.stock_w, self.stock_d, self.stock_h = LengthSpin(), LengthSpin(), LengthSpin()
        self.margin = LengthSpin(1000.0)
        for spin, label in ((self.stock_w, tr("Width")), (self.stock_d, tr("Depth")),
                            (self.stock_h, tr("Thickness")), (self.margin, tr("Margin"))):
            spin.valueChanged.connect(self._on_stock_edited)
            f.addRow(label, spin)
        self.btn_fit = QPushButton(tr("Fit stock to the part"))
        self.btn_fit.clicked.connect(self._on_fit_stock)
        f.addRow("", self.btn_fit)
        lay.addWidget(box)

        box = QGroupBox(tr("Work zero"))
        v = QVBoxLayout(box)
        grid = QGridLayout()
        self.zero_group = QButtonGroup(self)
        self.zero_buttons = {}
        for r, names in enumerate(ZERO_GRID):
            for c, name in enumerate(names):
                b = QRadioButton()
                b.setToolTip(self._zero_tooltip(name))
                self.zero_group.addButton(b)
                self.zero_buttons[name] = b
                grid.addWidget(b, r, c, Qt.AlignCenter)
        self.zero_group.buttonToggled.connect(self._on_zero_edited)
        v.addLayout(grid)
        self.zero_z = QComboBox()
        self.zero_z.addItem(tr("Z zero on the stock top"), "materialSurface")
        self.zero_z.addItem(tr("Z zero on the machine bed"), "machineBed")
        self.zero_z.currentIndexChanged.connect(self._on_zero_edited)
        v.addWidget(self.zero_z)
        lay.addWidget(box)

        box = QGroupBox(tr("Heights"))
        f = QFormLayout(box)
        self.safe = LengthSpin(500.0)
        self.clearance = LengthSpin(500.0)
        self.safe.valueChanged.connect(self._on_job_edited)
        self.clearance.valueChanged.connect(self._on_job_edited)
        f.addRow(tr("Safe height above the stock"), self.safe)
        f.addRow(tr("Clearance height for drilling"), self.clearance)
        lay.addWidget(box)

        box = QGroupBox(tr("Machine"))
        f = QFormLayout(box)
        self.max_rpm, self.min_rpm = QSpinBox(), QSpinBox()
        for s in (self.max_rpm, self.min_rpm):
            s.setRange(0, 100_000)
            s.setSingleStep(1000)
            s.setSuffix(" rpm")
            s.valueChanged.connect(self._on_job_edited)
        self.max_feed, self.max_plunge, self.rapid = FeedSpin(), FeedSpin(), FeedSpin()
        for s in (self.max_feed, self.max_plunge, self.rapid):
            s.valueChanged.connect(self._on_job_edited)
        f.addRow(tr("Maximum spindle speed"), self.max_rpm)
        f.addRow(tr("Minimum spindle speed"), self.min_rpm)
        f.addRow(tr("Maximum feed"), self.max_feed)
        f.addRow(tr("Maximum plunge feed (0 = same)"), self.max_plunge)
        f.addRow(tr("Rapid feed (for the time estimate)"), self.rapid)
        self.coolant = QCheckBox(tr("The machine has coolant (M8/M9)"))
        self.spindle_dwell = QCheckBox(tr("Wait for the spindle to reach speed"))
        self.flatten = QCheckBox(tr("Write arcs as straight segments (LinuxCNC)"))
        for c in (self.coolant, self.spindle_dwell, self.flatten):
            c.toggled.connect(self._on_job_edited)
            f.addRow("", c)
        lay.addWidget(box)
        lay.addStretch(1)
        return w

    def _zero_tooltip(self, name: str) -> str:
        return {
            "topLeft": tr("Back-left corner"), "topCenter": tr("Back edge centre"),
            "topRight": tr("Back-right corner"), "centerLeft": tr("Left edge centre"),
            "center": tr("Centre"), "centerRight": tr("Right edge centre"),
            "bottomLeft": tr("Front-left corner"), "bottomCenter": tr("Front edge centre"),
            "bottomRight": tr("Front-right corner"),
        }[name]

    # ---- Tools ----
    def _build_tools(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.tool_list = QListWidget()
        self.tool_list.currentRowChanged.connect(self._on_tool_selected)
        lay.addWidget(self.tool_list, 1)
        row = QHBoxLayout()
        for label, slot in ((tr("Add"), self._on_tool_add), (tr("Duplicate"), self._on_tool_dup),
                            (tr("Delete"), self._on_tool_delete)):
            b = QPushButton(label)
            b.clicked.connect(slot)
            row.addWidget(b)
        lay.addLayout(row)
        form_w = QWidget()
        f = QFormLayout(form_w)
        self.t_number = QSpinBox()
        self.t_number.setRange(1, 999)
        self.t_name = QLineEdit()
        self.t_kind = QComboBox()
        for k in ("flatEndMill", "ballEndMill", "bullNoseEndMill", "drill", "spotDrill"):
            self.t_kind.addItem(tool_kind_label(k), k)
        self.t_diameter = LengthSpin(200.0)
        self.t_flute = LengthSpin(300.0)
        self.t_length = LengthSpin(500.0)
        self.t_flutes = QSpinBox()
        self.t_flutes.setRange(1, 12)
        self.t_rpm = QSpinBox()
        self.t_rpm.setRange(1, 100_000)
        self.t_rpm.setSingleStep(500)
        self.t_rpm.setSuffix(" rpm")
        self.t_feed, self.t_plunge = FeedSpin(), FeedSpin()
        rows = ((tr("Number"), self.t_number), (tr("Name"), self.t_name),
                (tr("Type"), self.t_kind), (tr("Diameter"), self.t_diameter),
                (tr("Flute length"), self.t_flute), (tr("Overall length"), self.t_length),
                (tr("Flutes"), self.t_flutes), (tr("Spindle speed"), self.t_rpm),
                (tr("Feed"), self.t_feed), (tr("Plunge feed"), self.t_plunge))
        for label, widget in rows:
            f.addRow(label, widget)
        self.t_name.editingFinished.connect(self._on_tool_edited)
        self.t_kind.currentIndexChanged.connect(self._on_tool_edited)
        for s in (self.t_number, self.t_flutes, self.t_rpm, self.t_diameter, self.t_flute,
                  self.t_length, self.t_feed, self.t_plunge):
            s.valueChanged.connect(self._on_tool_edited)
        self.tool_form = form_w
        lay.addWidget(_scroll(form_w), 2)
        return w

    # ---- Operations ----
    def _build_operations(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        self.btn_add = QToolButton()
        self.btn_add.setText(tr("Add from selection"))
        self.btn_add.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.btn_add)
        for kind in ("outsideProfile", "insideProfile", "pocket", "drilling", "engraving"):
            menu.addAction(kind_label(kind), lambda k=kind: self._on_add(k))
        menu.addSeparator()
        menu.addAction(kind_label("facing"), lambda: self._on_add("facing"))
        menu.addSeparator()
        menu.addAction(tr("Every operation for the selected part"), self._on_part_operations)
        self.btn_add.setMenu(menu)
        row.addWidget(self.btn_add)
        for label, slot in (("↑", self._on_op_up), ("↓", self._on_op_down),
                            (tr("Delete"), self._on_op_delete)):
            b = QPushButton(label)
            b.clicked.connect(slot)
            row.addWidget(b)
        lay.addLayout(row)
        self.op_list = QListWidget()
        self.op_list.currentRowChanged.connect(self._on_op_selected)
        self.op_list.itemChanged.connect(self._on_op_checked)
        lay.addWidget(self.op_list, 1)
        self.op_form = OperationForm()
        self.op_form.changed.connect(self._on_op_edited)
        lay.addWidget(_scroll(self.op_form), 2)
        return w

    # ---- Output ----
    def _build_output(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        self.btn_calc = QPushButton(tr("Calculate"))
        self.btn_calc.clicked.connect(self.calculate)
        self.btn_cancel = QPushButton(tr("Cancel"))
        self.btn_cancel.clicked.connect(self._on_cancel)
        self.btn_cancel.setEnabled(False)
        row.addWidget(self.btn_calc)
        row.addWidget(self.btn_cancel)
        lay.addLayout(row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.stats = QLabel()
        self.stats.setWordWrap(True)
        self.stats.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.stats)
        lay.addWidget(QLabel(tr("Verification")))
        self.issue_list = QListWidget()
        self.issue_list.setWordWrap(True)
        lay.addWidget(self.issue_list, 1)
        box = QGroupBox(tr("Show in the model"))
        h = QHBoxLayout(box)
        self.show_cuts = QCheckBox(tr("Toolpaths"))
        self.show_rapids = QCheckBox(tr("Rapids"))
        self.show_stock = QCheckBox(tr("Stock"))
        for c in (self.show_cuts, self.show_rapids, self.show_stock):
            c.setChecked(True)
            c.toggled.connect(self._on_overlay_toggles)
            h.addWidget(c)
        lay.addWidget(box)
        self.btn_sim = QPushButton(tr("Simulate…"))
        self.btn_sim.setToolTip(tr("Play the job back and watch the stock being cut in 3D."))
        self.btn_sim.clicked.connect(self.simulate)
        self.btn_sim.setEnabled(False)
        lay.addWidget(self.btn_sim)
        self.btn_export = QPushButton(tr("Export G-code…"))
        self.btn_export.clicked.connect(self.export)
        self.btn_export.setEnabled(False)
        lay.addWidget(self.btn_export)
        return w

    # ==== document ⇄ dock =====================================================
    def _document_data(self):
        pd = getattr(self.viewport.scene, "plugin_data", None)
        return pd.get(PLUGIN_KEY) if isinstance(pd, dict) else None

    def _on_scene_changed(self, _version=None) -> None:
        if self._persist_timer.isActive():
            return                        # our own pending edit wins; it lands soon
        self._reload_from_document()

    def _reload_from_document(self, force: bool = False) -> None:
        doc = self._document_data()
        if not force and doc == self._last_doc:
            return
        self._last_doc = copy.deepcopy(doc)
        try:
            self.state = CamState.from_dict(doc) if doc else CamState.new(translate=tr)
        except Exception:  # noqa: BLE001 — a damaged block must not break the dock
            self.state = CamState.new(translate=tr)
            self._set_status(tr("The CAM data in this document could not be read; "
                                "a new job was started."), error=True)
        self._result = None
        self.overlay.clear()
        self._refresh_all()
        self._schedule_recalc()

    def _changed(self, recalc: bool = True) -> None:
        """Something in the state changed: persist soon, recalculate soon."""
        if self._loading:
            return
        self._persist_timer.start(PERSIST_MS)
        self._result_stale = True
        self.btn_export.setEnabled(False)
        self.btn_sim.setEnabled(False)
        self.overlay.set_stock(self.state)
        self.viewport.update()
        if recalc:
            self._schedule_recalc()

    def _persist(self) -> None:
        from core.history import SetPluginData
        data = self.state.to_dict()
        if data == self._last_doc:
            return
        self._last_doc = copy.deepcopy(data)
        self.viewport.history.execute(SetPluginData(PLUGIN_KEY, data))
        self.viewport.notify_scene_changed()

    def flush(self) -> None:
        """Write a pending edit now (tests, export)."""
        if self._persist_timer.isActive():
            self._persist_timer.stop()
            self._persist()

    # ==== refresh widgets from the state ======================================
    def _refresh_all(self) -> None:
        self._loading = True
        try:
            job = self.state.job
            inch = job.is_inch
            self.job_name.setText(job.name)
            self.controller.setCurrentIndex(max(0, self.controller.findData(job.post.controller)))
            self.units.setCurrentIndex(max(0, self.units.findData(job.units)))
            for s in (self.stock_w, self.stock_d, self.stock_h, self.margin, self.safe,
                      self.clearance, self.t_diameter, self.t_flute, self.t_length):
                s.set_inch(inch)
            for s in (self.max_feed, self.max_plunge, self.rapid, self.t_feed, self.t_plunge):
                s.set_inch(inch)
            self.op_form.set_units(inch)
            st = job.stock
            self.stock_w.set_mm(st.width)
            self.stock_d.set_mm(st.depth)
            self.stock_h.set_mm(st.height)
            self.margin.set_mm(self.state.margin)
            b = self.zero_buttons.get(st.referencePoint)
            if b is not None:
                b.setChecked(True)
            self.zero_z.setCurrentIndex(max(0, self.zero_z.findData(st.zeroPosition)))
            self.safe.set_mm(job.setup.safeHeight)
            self.clearance.set_mm(job.setup.clearanceHeight)
            m = job.machine
            self.max_rpm.setValue(m.maximumSpindleRPM)
            self.min_rpm.setValue(m.minimumSpindleRPM)
            self.max_feed.set_mm(m.maximumFeed)
            self.max_plunge.set_mm(m.maximumPlungeFeed or 0.0)
            self.rapid.set_mm(m.rapidFeed)
            self.coolant.setChecked(job.post.coolant)
            self.spindle_dwell.setChecked(job.post.spindleDwell)
            self.flatten.setChecked(job.post.flattenArcs)
            self.flatten.setEnabled(job.post.controller == "linuxcnc")
            self._refresh_tools()
            self._refresh_operations()
        finally:
            self._loading = False
        self.overlay.set_stock(self.state)
        self.viewport.update()

    def _refresh_tools(self, select: int | None = None) -> None:
        row = self.tool_list.currentRow() if select is None else select
        self.tool_list.blockSignals(True)
        self.tool_list.clear()
        for t in sorted(self.state.job.tools, key=lambda t: t.number):
            self.tool_list.addItem(f"T{t.number} · {t.name} · {tool_kind_label(t.kind)}")
        self.tool_list.blockSignals(False)
        if self.tool_list.count():
            self.tool_list.setCurrentRow(min(max(row, 0), self.tool_list.count() - 1))
        self._on_tool_selected(self.tool_list.currentRow())

    def _sorted_tools(self) -> list:
        return sorted(self.state.job.tools, key=lambda t: t.number)

    def _refresh_operations(self, select: int | None = None) -> None:
        row = self.op_list.currentRow() if select is None else select
        self.op_list.blockSignals(True)
        self.op_list.clear()
        for op in self.state.job.operations:
            it = QListWidgetItem(_op_label(op))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if op.isEnabled else Qt.Unchecked)
            self.op_list.addItem(it)
            _style_enabled(it, op.isEnabled)
        self.op_list.blockSignals(False)
        if self.op_list.count():
            self.op_list.setCurrentRow(min(max(row, 0), self.op_list.count() - 1))
        self._on_op_selected(self.op_list.currentRow())

    # ==== Job edits ===========================================================
    def _on_job_edited(self, *_args) -> None:
        if self._loading:
            return
        job = self.state.job
        job.name = self.job_name.text().strip() or job.name
        job.post.controller = self.controller.currentData()
        self.flatten.setEnabled(job.post.controller == "linuxcnc")
        job.setup.safeHeight = self.safe.mm()
        job.setup.clearanceHeight = self.clearance.mm()
        m = job.machine
        m.maximumSpindleRPM = self.max_rpm.value()
        m.minimumSpindleRPM = self.min_rpm.value()
        m.maximumFeed = self.max_feed.mm()
        m.maximumPlungeFeed = self.max_plunge.mm() or None
        m.rapidFeed = max(1.0, self.rapid.mm())
        job.post.coolant = self.coolant.isChecked()
        job.post.spindleDwell = self.spindle_dwell.isChecked()
        job.post.flattenArcs = self.flatten.isChecked()
        self._changed()

    def _on_units_changed(self, *_args) -> None:
        if self._loading:
            return
        self.state.job.units = self.units.currentData()
        self._refresh_all()
        self._changed(recalc=False)
        self._show_result()

    def _on_stock_edited(self, *_args) -> None:
        if self._loading:
            return
        st = self.state.job.stock
        new_margin = self.margin.mm()
        if abs(new_margin - self.state.margin) > 1e-9:
            self.state.margin = new_margin
            if self.state.stockAuto:
                self.state.fit_stock()
        elif abs(self.stock_w.mm() - st.width) > 1e-9 or abs(self.stock_d.mm() - st.depth) > 1e-9:
            self.state.stockAuto = False
            st.width, st.depth = self.stock_w.mm(), self.stock_d.mm()
        st.height = self.stock_h.mm()
        st.align_origin_to_reference()
        self._loading = True
        self.stock_w.set_mm(st.width)
        self.stock_d.set_mm(st.depth)
        self._loading = False
        self._changed()

    def _on_fit_stock(self) -> None:
        self.state.stockAuto = True
        self.state.fit_stock()
        self._refresh_all()
        self._changed()

    def _on_zero_edited(self, *_args) -> None:
        if self._loading:
            return
        st = self.state.job.stock
        for name, b in self.zero_buttons.items():
            if b.isChecked():
                st.referencePoint = name
        st.zeroPosition = self.zero_z.currentData()
        st.align_origin_to_reference()
        self._changed()

    # ==== selection → job =====================================================
    def _extract(self):
        from ..extract import extract_selection
        return extract_selection(self.viewport.scene, self.state.frame)

    def _on_setup_from_selection(self) -> None:
        from ..extract import extract_selection
        try:
            ex = extract_selection(self.viewport.scene, None)
        except CamError as exc:
            self._report_error(exc.issue)
            return
        if self.state.job.operations and self.state.frame is not None \
                and not self.state.frame.parallel_to(ex.frame):
            answer = QMessageBox.question(
                self, tr("CAM"),
                tr("The job's operations are on another plane. Start the job again on "
                   "the selected one?"))
            if answer != QMessageBox.Yes:
                return
            self.state.job.operations.clear()
            self.state.sources.clear()
        self.state.frame = ex.frame
        self.state.bounds = None
        self.state.stockAuto = True
        if ex.thickness:
            self.state.job.stock.height = round(ex.thickness, 4)
        self.state.sourceGroup = ex.group_uid
        self.state.include_bounds(ex.all_points())
        self.state.fit_stock()
        self._refresh_all()
        self._changed()
        self._set_status(tr("Machining plane and stock set from the selection."))

    def _find_group(self, uid):
        from core.group import iter_placements
        for top in self.viewport.scene.groups:
            for g, _m in iter_placements(top):
                if getattr(g, "uid", None) == uid:
                    return g
        return None

    def _on_refresh(self) -> None:
        from ..extract import extract_part
        uid = self.state.sourceGroup
        group = self._find_group(uid) if uid else None
        if group is None:
            self._set_status(tr("The part this job was made from is no longer in the model. "
                                "The job keeps its geometry."), error=True)
            return
        try:
            ex = extract_part(group, self.state.frame)
        except CamError as exc:
            self._report_error(exc.issue)
            return
        updated, unmatched = build.refresh_from_part(self.state, ex)
        self._refresh_all()
        self._changed()
        if unmatched:
            self._set_status(tr("Refreshed {count} operation(s). These no longer match the "
                                "part and kept their old geometry: {names}",
                                count=len(updated), names=", ".join(unmatched)), error=True)
        else:
            self._set_status(tr("Refreshed {count} operation(s) from the part.",
                                count=len(updated)))

    def _on_part_operations(self) -> None:
        try:
            ex = self._extract()
            ops = build.suggest_operations(self.state, ex)
        except CamError as exc:
            self._report_error(exc.issue)
            return
        self._after_add(ops)

    def _on_add(self, kind: str) -> None:
        try:
            ex = None if kind == "facing" else self._extract()
            ops = build.add_operations(self.state, kind, ex)
        except CamError as exc:
            self._report_error(exc.issue)
            return
        self._after_add(ops)

    def _after_add(self, ops) -> None:
        self._refresh_all()
        self.tabs.setCurrentIndex(2)
        if ops:
            self.op_list.setCurrentRow(self.state.job.operations.index(ops[-1]))
        self._set_status(tr("{count} operation(s) added.", count=len(ops)))
        self._changed()

    # ==== tools ===============================================================
    def _current_tool(self):
        tools = self._sorted_tools()
        r = self.tool_list.currentRow()
        return tools[r] if 0 <= r < len(tools) else None

    def _on_tool_selected(self, _row) -> None:
        t = self._current_tool()
        self.tool_form.setEnabled(t is not None)
        if t is None:
            return
        was = self._loading
        self._loading = True
        try:
            self.t_number.setValue(t.number)
            self.t_name.setText(t.name)
            self.t_kind.setCurrentIndex(max(0, self.t_kind.findData(t.kind)))
            self.t_diameter.set_mm(t.diameter)
            self.t_flute.set_mm(t.fluteLength)
            self.t_length.set_mm(t.overallLength)
            self.t_flutes.setValue(t.fluteCount)
            self.t_rpm.setValue(t.spindleRPM)
            self.t_feed.set_mm(t.cuttingFeed)
            self.t_plunge.set_mm(t.plungeFeed)
        finally:
            self._loading = was

    def _on_tool_edited(self, *_args) -> None:
        if self._loading:
            return
        t = self._current_tool()
        if t is None:
            return
        t.number = self.t_number.value()
        t.name = self.t_name.text().strip() or t.name
        t.kind = self.t_kind.currentData()
        t.diameter = self.t_diameter.mm()
        t.fluteLength = self.t_flute.mm()
        t.overallLength = max(self.t_length.mm(), t.fluteLength)
        t.fluteCount = self.t_flutes.value()
        t.spindleRPM = self.t_rpm.value()
        t.cuttingFeed = self.t_feed.mm()
        t.plungeFeed = self.t_plunge.mm()
        self._refresh_tools(select=self._sorted_tools().index(t))
        self.op_form.set_operation(self.op_form.op, self.state.job)
        self._changed()

    def _on_tool_add(self) -> None:
        n = max((t.number for t in self.state.job.tools), default=0) + 1
        t = Tool(number=n, name=tr("New tool"))
        self.state.job.tools.append(t)
        self._refresh_tools(select=self._sorted_tools().index(t))
        self._changed()

    def _on_tool_dup(self) -> None:
        t = self._current_tool()
        if t is None:
            return
        c = copy.deepcopy(t)
        c.id = new_id()
        c.number = max(x.number for x in self.state.job.tools) + 1
        self.state.job.tools.append(c)
        self._refresh_tools(select=self._sorted_tools().index(c))
        self._changed()

    def _on_tool_delete(self) -> None:
        t = self._current_tool()
        if t is None:
            return
        used = [o.name for o in self.state.job.operations
                if t.id in (o.toolID, o.finishingToolID)]
        if used:
            QMessageBox.information(self, tr("CAM"), tr(
                "This tool is used by: {operations}. Choose another tool for them first.",
                operations=", ".join(used)))
            return
        self.state.job.tools.remove(t)
        self._refresh_tools()
        self._changed()

    # ==== operations ===========================================================
    def _current_op(self):
        ops = self.state.job.operations
        r = self.op_list.currentRow()
        return ops[r] if 0 <= r < len(ops) else None

    def _on_op_selected(self, _row) -> None:
        op = self._current_op()
        self.op_form.set_operation(op, self.state.job)
        self.overlay.selected_op = op.id if op else None
        self.viewport.update()

    def _on_op_checked(self, item) -> None:
        if self._loading:
            return
        r = self.op_list.row(item)
        ops = self.state.job.operations
        if 0 <= r < len(ops):
            ops[r].isEnabled = item.checkState() == Qt.Checked
            self.op_list.blockSignals(True)
            _style_enabled(item, ops[r].isEnabled)
            self.op_list.blockSignals(False)
            self._changed()

    def _on_op_edited(self) -> None:
        op = self._current_op()
        r = self.op_list.currentRow()
        if op is not None and 0 <= r < self.op_list.count():
            self.op_list.blockSignals(True)
            self.op_list.item(r).setText(_op_label(op))
            self.op_list.blockSignals(False)
        self._changed()

    def _move_op(self, delta: int) -> None:
        ops = self.state.job.operations
        r = self.op_list.currentRow()
        if not (0 <= r < len(ops)) or not (0 <= r + delta < len(ops)):
            return
        ops[r], ops[r + delta] = ops[r + delta], ops[r]
        self._refresh_operations(select=r + delta)
        self._changed()

    def _on_op_up(self) -> None:
        self._move_op(-1)

    def _on_op_down(self) -> None:
        self._move_op(1)

    def _on_op_delete(self) -> None:
        op = self._current_op()
        if op is None:
            return
        self.state.job.operations.remove(op)
        self.state.sources.pop(op.id, None)
        self._refresh_operations()
        self._changed()

    # ==== calculation ==========================================================
    def _schedule_recalc(self) -> None:
        self._recalc_timer.start(RECALC_MS)

    def calculate(self) -> None:
        """Start a calculation of the current state (cancelling any)."""
        self._recalc_timer.stop()
        if self._calc is not None:
            self._calc.cancel()
        if not any(o.isEnabled for o in self.state.job.operations):
            self._result = None
            self.overlay.set_result(self.state, _EmptyResult())
            self._set_status(tr("Add operations from the selection to begin."))
            self._show_result()
            return
        self._generation += 1
        job = self.state.work_job()
        self._calc = Calculation(job, self._generation, self._calculated.emit).start()
        self.btn_cancel.setEnabled(True)
        self._set_status(tr("Calculating…"))

    def _on_cancel(self) -> None:
        if self._calc is not None:
            self._calc.cancel()

    def _on_calculated(self, outcome) -> None:
        if outcome.generation != self._generation:
            return                                    # superseded
        self._calc = None
        self.btn_cancel.setEnabled(False)
        self._result = outcome
        self._result_stale = False
        if outcome.ok:
            self.overlay.set_result(self.state, outcome.compiled)
        self.overlay.set_stock(self.state)
        self._show_result()
        self.viewport.update()

    def _show_result(self) -> None:
        self.issue_list.clear()
        inch = self.state.job.is_inch
        out = self._result
        if out is None:
            self.stats.setText("")
            self.btn_export.setEnabled(False)
            self.btn_sim.setEnabled(False)
            return
        if not out.ok:
            self._set_status(messages.describe(out.error, inch), error=True)
            self.stats.setText("")
            self.btn_export.setEnabled(False)
            self.btn_sim.setEnabled(False)
            return
        tp = out.compiled.toolpath
        st = tp.statistics()
        secs = tp.estimated_duration(self.state.job.machine)
        unit = "in" if inch else "mm"
        k = 1 / 25.4 if inch else 1.0
        self.stats.setText("\n".join((
            tr("Cutting: {length} {unit}", length=f"{st.cuttingLength * k:.0f}", unit=unit),
            tr("Rapid: {length} {unit}", length=f"{st.rapidLength * k:.0f}", unit=unit),
            tr("Estimated time: {time}", time=_duration(secs)),
            tr("Tool changes: {count}", count=st.toolChangeCount),
        )))
        errors = [i for i in out.issues if i.severity == ERROR]
        for issue in out.issues:
            it = QListWidgetItem(("⛔ " if issue.severity == ERROR else "⚠ ")
                                 + messages.describe(issue, inch))
            self.issue_list.addItem(it)
        if errors:
            self._set_status(tr("Verification found problems: fix them before exporting."),
                             error=True)
        else:
            self.issue_list.addItem("✓ " + tr("No problems found."))
            self._set_status(tr("Ready to export."))
        self.btn_export.setEnabled(not errors)
        self.btn_sim.setEnabled(True)
        if self._sim is not None and self._sim.isVisible():
            self._sim.load(self.state.work_job(), out.compiled)

    def _on_overlay_toggles(self, *_args) -> None:
        self.overlay.show_cuts = self.show_cuts.isChecked()
        self.overlay.show_rapids = self.show_rapids.isChecked()
        self.overlay.show_stock = self.show_stock.isChecked()
        self.viewport.update()

    def _on_visibility(self, _visible: bool) -> None:
        # Closed, not merely tabbed behind another tray: the toolpaths stay
        # in the model while the user looks at Properties.
        self.overlay.visible = not self.isHidden()
        self.viewport.update()

    # ==== simulation ===========================================================
    def simulate(self):
        """Open (or bring forward) the stock simulation of the current
        result; returns the window."""
        from .simview import SimulationWindow
        if self._result is None or not self._result.ok:
            return None
        if self._sim is None:
            self._sim = SimulationWindow(self)
            self._sim.playhead.connect(self._on_playhead)
        self._sim.load(self.state.work_job(), self._result.compiled)
        self._sim.show()
        self._sim.raise_()
        self._sim.activateWindow()
        return self._sim

    def _on_playhead(self, info) -> None:
        index, tip, tool = info
        self.overlay.set_playhead(self.state, index, tip,
                                  tool.diameter * 0.5 if tool is not None else 0.0)
        self.viewport.update()

    # ==== export ===============================================================
    def post(self):
        """Post the current result: ``(PostResult, job)``. Raises CamError."""
        from ..engine.post import post_job
        from ..i18n import translate
        if self._result is None or not self._result.ok or self._result_stale:
            raise CamError("empty_toolpath")
        job = self.state.work_job()
        return post_job(job, self._result.compiled.toolpath, translate=translate), job

    def export(self, path: str | None = None) -> list:
        """Ask for a file name (unless ``path``), post, check and write.
        Returns the written paths."""
        self.flush()
        try:
            result, job = self.post()
        except CamError as exc:
            self._report_error(exc.issue)
            return []
        if result.issues:
            for issue in result.issues:
                self.issue_list.addItem("⛔ " + messages.describe(issue, job.is_inch))
            self._set_status(tr("The G-code failed its final check and was not saved."),
                             error=True)
            return []
        ext = result.files[0].extension
        if path is None:
            start = str(Path.home() / (_safe_name(job.name) + ext))
            filt = tr("G-code ({pattern})", pattern=f"*{ext}")
            path, _ = QFileDialog.getSaveFileName(self, tr("Export G-code"), start, filt)
            if not path:
                return []
        base = Path(path)
        if base.suffix.lower() in (".nc", ".ngc", ".gcode", ".tap"):
            base = base.with_suffix("")
        targets = [base.with_name(base.name + f.suffix + f.extension) for f in result.files]
        if len(targets) > 1 and path is not None and not getattr(self, "_quiet", False):
            names = "\n".join(t.name for t in targets)
            answer = QMessageBox.question(self, tr("Export G-code"), tr(
                "GRBL has no tool changer, so this job is written as one file per tool. "
                "Run them in order, changing the tool and setting Z zero between them:"
                "\n\n{files}", files=names))
            if answer != QMessageBox.Yes:
                return []
        written = []
        for f, target in zip(result.files, targets):
            target.write_text(f.text, encoding="ascii", newline="\n")
            written.append(str(target))
        self._set_status(tr("Saved {count} file(s): {names}", count=len(written),
                            names=", ".join(Path(p).name for p in written)))
        return written

    # ==== helpers ============================================================
    def _set_status(self, text: str, error: bool = False) -> None:
        self.status.setText(text)
        self.status.setStyleSheet("color: #c0392b;" if error else "")

    def _report_error(self, issue) -> None:
        self._set_status(messages.describe(issue, self.state.job.is_inch), error=True)
        self.tabs.setCurrentIndex(3)

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt override
        self.flush()
        super().closeEvent(event)

    def dispose(self) -> None:
        """Detach from the viewport (tests; the dock lives as long as the
        window otherwise)."""
        self.flush()
        if self._sim is not None:
            self._sim.close()
        if self.overlay in self.viewport.overlay_painters:
            self.viewport.overlay_painters.remove(self.overlay)
        try:
            self.viewport.sceneVersionChanged.disconnect(self._on_scene_changed)
        except (RuntimeError, TypeError):
            pass


class _EmptyResult:
    """A CompileResult with nothing in it (clears the overlay)."""

    def __init__(self) -> None:
        from ..engine.toolpath import Toolpath
        self.toolpath = Toolpath([])
        self.operation_ranges = {}


def _style_enabled(item, enabled: bool) -> None:
    """A skipped operation reads as skipped even where the platform style
    draws no check box: greyed and struck through."""
    font = item.font()
    font.setStrikeOut(not enabled)
    item.setFont(font)
    item.setForeground(item.listWidget().palette().text() if enabled else Qt.gray)


def _op_label(op) -> str:
    """The operation's name, and its kind when the name does not say it."""
    kind = kind_label(op.kind)
    return op.name if op.name.startswith(kind) else f"{op.name}  ({kind})"


def _duration(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h} h {m:02d} min" if h else f"{m} min {s:02d} s"


def _safe_name(name: str) -> str:
    from ..engine.post.base import slug
    return slug(name) or "job"

