# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The CAM dock: a start page, then Job ▸ Tools ▸ Operations ▸ Output.

With the model in front, the dock is a start page: New CAM job, Open CAM
job, recent jobs. A job is its own ``.igcam`` file (:mod:`..jobfile`) and
opens in CAM mode (:mod:`.workspace`): the window shows the job's drawing
on the stock while the model waits, parked. The job setup comes first —
until it is confirmed, the other tabs and the drawing tools stay off.

Inside a job, the dock owns a :class:`~..state.CamState` and keeps it and
the job's scene in step, both ways:

- **dock → document.** Every edit marks the state dirty; 600 ms after the
  last one the whole state is written to ``scene.plugin_data["cam"]``
  through ``SetPluginDataCommand`` — ONE undo step per burst of edits, not
  one per keystroke — and the document is marked modified.
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
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDockWidget,
                               QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
                               QMessageBox, QPushButton, QScrollArea, QSpinBox, QTabWidget,
                               QToolButton, QVBoxLayout, QWidget)

from views.tray import FlowLayout       # the host's wrapping row

from .. import build
from ..jobfile import SUFFIX, JobFileError, load_job, save_job
from ..engine.issues import CamError, ERROR
from ..engine.models import Tool, new_id
from ..i18n import tr
from ..state import PLUGIN_KEY, CamState
from . import library, messages
from .op_forms import (CompactSpin, FeedSpin, LengthSpin, OperationForm, compact_combo,
                       kind_label, tool_kind_label)
from .overlay import ToolpathOverlay
from .worker import Calculation
from .workspace import CamWorkspace, file_filter, recent_jobs, remember_job

DOCK_NAME = "cam_dock"
PERSIST_MS = 600
RECALC_MS = 350
#: The path list follows the model this long after its last edit.
PATHS_MS = 250
#: How often the path list looks at the model's selection.
SELECTION_POLL_MS = 200

#: The 9-point work zero, laid out as it looks from above.
ZERO_GRID = (("topLeft", "topCenter", "topRight"),
             ("centerLeft", "center", "centerRight"),
             ("bottomLeft", "bottomCenter", "bottomRight"))

#: Stock materials: 2DCam's five plus the two sheet goods a router cuts most.
MATERIALS = ("clearWood", "darkWood", "mdf", "plywood", "plastic", "aluminium", "steel")


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
    dock.show_page()
    dock.give_focus_to_model()
    return dock


def _form(parent) -> QFormLayout:
    """A form that puts a field under its label when the dock is narrow,
    instead of demanding the width of the longest label plus field."""
    f = QFormLayout(parent)
    f.setRowWrapPolicy(QFormLayout.WrapLongRows)
    f.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    return f


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
        #: The drawing read as paths (:mod:`..paths`), on ``_path_frame``.
        self.paths: list = []
        self._path_frame = None
        self._sel_key = None
        #: Per operation: the model edges of each path it was made from, so
        #: an edited path is found again however far it moved (this
        #: session only; after reopening, the shape finds it).
        self._op_edges: dict = {}
        #: Per operation: why it could not follow its paths (None: it did).
        self.link_status: dict = {}
        self._paths_timer = QTimer(self)
        self._paths_timer.setSingleShot(True)
        self._paths_timer.timeout.connect(self.refresh_paths)
        # The host has no selection signal (the trays re-read the selection
        # on every scene change); a cheap poll of the selection's identity
        # keeps the path list in step with clicks in the model.
        self._sel_timer = QTimer(self)
        self._sel_timer.setInterval(SELECTION_POLL_MS)
        self._sel_timer.timeout.connect(self._sync_paths_from_model)

        self._build()
        self._hand_back_on_enter()
        viewport.overlay_painters.append(self.overlay)
        viewport.sceneVersionChanged.connect(self._on_scene_changed)
        self.visibilityChanged.connect(self._on_visibility)
        self._sel_timer.start()
        self.show_page()

    # ==== layout ==============================================================
    def _build(self) -> None:
        self.tabs = QTabWidget()
        # Tighter tabs: with Qt's default padding the four titles need about
        # 270 px, and a scrolling tab bar hides Output in a narrow dock.
        self.tabs.setStyleSheet("QTabBar::tab { padding: 4px 7px; }")
        self.tabs.addTab(_scroll(self._build_job()), tr("Job"))
        self.tabs.addTab(self._build_tools(), tr("Tools"))
        self.tabs.addTab(self._build_operations(), tr("Operations"))
        self.tabs.addTab(self._build_output(), tr("Output"))
        # In a narrow dock (or in Spanish, «Herramientas») the titles shorten
        # with an ellipsis rather than scroll Output out of sight; the full
        # title is the tab's tooltip.
        bar = self.tabs.tabBar()
        bar.setElideMode(Qt.ElideRight)
        bar.setUsesScrollButtons(False)
        for i in range(self.tabs.count()):
            bar.setTabToolTip(i, self.tabs.tabText(i))
        job_page = QWidget()
        col = QVBoxLayout(job_page)
        col.setContentsMargins(0, 0, 0, 0)
        col.addWidget(self._build_header())
        col.addWidget(self.tabs, 1)
        from PySide6.QtWidgets import QStackedWidget
        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_start())
        self.pages.addWidget(job_page)
        self.setWidget(self.pages)

    # ---- start page (the model is in front) ----
    def _build_start(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        intro = QLabel(tr("A CAM job is a 2.5D drawing on the stock, saved as its own "
                          "file. The model is put aside while a job is open, and comes "
                          "back untouched."))
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.btn_new_job = QPushButton(tr("New CAM job…"))
        self.btn_new_job.clicked.connect(lambda: self.new_job())
        self.btn_open_job = QPushButton(tr("Open CAM job…"))
        self.btn_open_job.clicked.connect(lambda: self.open_job())
        self.btn_template = QPushButton(tr("New from a stock template…"))
        self.btn_template.setToolTip(tr("Start with a saved setup: stock, material, "
                                        "machine and tools."))
        self.btn_template.clicked.connect(lambda: self.new_from_template())
        for b in (self.btn_new_job, self.btn_open_job, self.btn_template):
            lay.addWidget(b)
        lay.addWidget(QLabel(tr("Recent jobs")))
        self.recent_list = QListWidget()
        self.recent_list.setTextElideMode(Qt.ElideMiddle)
        self.recent_list.itemActivated.connect(
            lambda it: self.open_job(Path(it.data(Qt.UserRole))))
        lay.addWidget(self.recent_list, 1)
        return w

    def _refresh_recent(self) -> None:
        self.recent_list.clear()
        for p in recent_jobs():
            it = QListWidgetItem(p.name)
            it.setToolTip(str(p))
            it.setData(Qt.UserRole, str(p))
            self.recent_list.addItem(it)

    # ---- job header (CAM mode) ----
    def _build_header(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 4, 6, 0)
        self.job_title = QLabel()
        self.job_title.setTextFormat(Qt.PlainText)
        font = self.job_title.font()
        font.setBold(True)
        self.job_title.setFont(font)
        lay.addWidget(self.job_title)
        row = FlowLayout(spacing=4)
        self.btn_save_job = QPushButton(tr("Save"))
        self.btn_save_job.clicked.connect(self.save_job)
        self.btn_leave = QPushButton(tr("Back to the model"))
        self.btn_leave.setToolTip(tr("Close the CAM job and show the model again."))
        self.btn_leave.clicked.connect(self.leave_job)
        self.btn_sim_header = QPushButton(tr("Simulate…"))
        self.btn_sim_header.setToolTip(tr("Play the job back and watch the stock being cut "
                                          "in 3D."))
        self.btn_sim_header.clicked.connect(self.simulate)
        self.btn_sim_header.setEnabled(False)
        row.addWidget(self.btn_save_job)
        row.addWidget(self.btn_sim_header)
        row.addWidget(self.btn_leave)
        lay.addLayout(row)
        return w

    # ---- Job ----
    def _build_job(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.setup_note = QLabel(tr("Set up the job first: the stock, its material, the "
                                    "machine and the work zero. Then draw on the stock."))
        self.setup_note.setWordWrap(True)
        lay.addWidget(self.setup_note)

        box = QGroupBox(tr("Job"))
        f = _form(box)
        self.job_name = QLineEdit()
        self.job_name.editingFinished.connect(self._on_job_edited)
        f.addRow(tr("Name"), self.job_name)
        self.controller = compact_combo()
        self.controller.addItem("GRBL", "grbl")
        self.controller.addItem("LinuxCNC", "linuxcnc")
        self.controller.currentIndexChanged.connect(self._on_job_edited)
        f.addRow(tr("Controller"), self.controller)
        self.units = compact_combo()
        self.units.addItem(tr("Millimetres"), "millimeters")
        self.units.addItem(tr("Inches"), "inches")
        self.units.currentIndexChanged.connect(self._on_units_changed)
        f.addRow(tr("Units"), self.units)
        lay.addWidget(box)

        box = QGroupBox(tr("Stock"))
        f = _form(box)
        self.stock_w, self.stock_d, self.stock_h = LengthSpin(), LengthSpin(), LengthSpin()
        for spin, label in ((self.stock_w, tr("Width")), (self.stock_d, tr("Depth")),
                            (self.stock_h, tr("Thickness"))):
            spin.valueChanged.connect(self._on_stock_edited)
            f.addRow(label, spin)
        self.material = compact_combo()
        for key in MATERIALS:
            self.material.addItem(material_label(key), key)
        self.material.currentIndexChanged.connect(self._on_stock_edited)
        f.addRow(tr("Material"), self.material)
        lay.addWidget(box)

        box = QGroupBox(tr("Work zero"))
        v = QVBoxLayout(box)
        pad = QWidget()
        pad.setObjectName("cam_zero_pad")
        grid = QGridLayout(pad)
        grid.setContentsMargins(6, 6, 6, 6)
        grid.setSpacing(18)
        self.zero_group = QButtonGroup(self)
        self.zero_group.setExclusive(True)
        self.zero_buttons = {}
        for r, names in enumerate(ZERO_GRID):
            for c, name in enumerate(names):
                b = QToolButton()
                b.setCheckable(True)
                b.setFixedSize(18, 18)
                b.setToolTip(self._zero_tooltip(name))
                self.zero_group.addButton(b)
                self.zero_buttons[name] = b
                grid.addWidget(b, r, c, Qt.AlignCenter)
        _style_zero_pad(pad)
        self.zero_group.buttonToggled.connect(self._on_zero_edited)
        row = QHBoxLayout()
        row.addWidget(pad)
        row.addStretch(1)
        v.addLayout(row)
        self.zero_z = compact_combo()
        self.zero_z.addItem(tr("Z0 on the stock top"), "materialSurface")
        self.zero_z.addItem(tr("Z0 on the machine bed"), "machineBed")
        self.zero_z.currentIndexChanged.connect(self._on_zero_edited)
        v.addWidget(self.zero_z)
        lay.addWidget(box)

        box = QGroupBox(tr("Heights"))
        f = _form(box)
        self.safe = LengthSpin(500.0)
        self.clearance = LengthSpin(500.0)
        self.safe.valueChanged.connect(self._on_job_edited)
        self.clearance.valueChanged.connect(self._on_job_edited)
        self.safe.setToolTip(tr("Height above the stock top for moves between cuts."))
        self.clearance.setToolTip(tr("Height above the stock top where drilling starts to feed."))
        f.addRow(tr("Safe height"), self.safe)
        f.addRow(tr("Drill clearance"), self.clearance)
        lay.addWidget(box)

        box = QGroupBox(tr("Machine"))
        f = _form(box)
        self.max_rpm, self.min_rpm = CompactSpin(), CompactSpin()
        for s in (self.max_rpm, self.min_rpm):
            s.setRange(0, 100_000)
            s.setSingleStep(1000)
            s.setSuffix(" rpm")
            s.valueChanged.connect(self._on_job_edited)
        self.max_feed, self.max_plunge, self.rapid = FeedSpin(), FeedSpin(), FeedSpin()
        for s in (self.max_feed, self.max_plunge, self.rapid):
            s.valueChanged.connect(self._on_job_edited)
        self.max_plunge.setToolTip(tr("0: no separate limit for plunging."))
        self.rapid.setToolTip(tr("Used for the time estimate only."))
        f.addRow(tr("Max spindle"), self.max_rpm)
        f.addRow(tr("Min spindle"), self.min_rpm)
        f.addRow(tr("Max feed"), self.max_feed)
        f.addRow(tr("Max plunge"), self.max_plunge)
        f.addRow(tr("Rapid feed"), self.rapid)
        self.coolant = QCheckBox(tr("Coolant (M8/M9)"))
        self.spindle_dwell = QCheckBox(tr("Spindle warm-up pause"))
        self.spindle_dwell.setToolTip(tr("Wait for the spindle to reach speed (G4)."))
        self.flatten = QCheckBox(tr("Arcs as lines"))
        self.flatten.setToolTip(tr("Write arcs as straight segments (LinuxCNC)."))
        for c in (self.coolant, self.spindle_dwell, self.flatten):
            c.toggled.connect(self._on_job_edited)
            f.addRow("", c)
        lay.addWidget(box)
        rotary = QLabel(tr("Milling on a flat stock (2.5D). Turning on a rotary axis is "
                           "not supported yet."))
        rotary.setWordWrap(True)
        rotary.setEnabled(False)
        lay.addWidget(rotary)
        self.btn_save_template = QPushButton(tr("Save setup as template…"))
        self.btn_save_template.setToolTip(tr("Keep this stock, material, machine and tool "
                                             "table to start new jobs from."))
        self.btn_save_template.clicked.connect(lambda: self.save_template())
        lay.addWidget(self.btn_save_template)
        self.btn_start = QPushButton(tr("Start the job"))
        self.btn_start.setToolTip(tr("Confirm the setup: the drawing tools and the other "
                                     "tabs become available."))
        self.btn_start.clicked.connect(self._on_start_job)
        lay.addWidget(self.btn_start)
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
        row = FlowLayout(spacing=4)
        for label, slot in ((tr("Add"), self._on_tool_add), (tr("Duplicate"), self._on_tool_dup),
                            (tr("Delete"), self._on_tool_delete)):
            b = QPushButton(label)
            b.clicked.connect(slot)
            row.addWidget(b)
        lay.addLayout(row)
        # The shop's tool cabinet (ui/library.py): pick a cutter with its
        # feeds resolved for this job's material, or keep one for later jobs.
        lib_row = FlowLayout(spacing=4)
        self.btn_from_library = QPushButton(tr("From the library…"))
        self.btn_from_library.setToolTip(tr(
            "Add a tool from your tool library, with speeds and feeds for this job's material "
            "and machine."))
        self.btn_from_library.clicked.connect(self._on_tool_from_library)
        self.btn_to_library = QPushButton(tr("Save to the library"))
        self.btn_to_library.setToolTip(tr(
            "Keep this tool in your tool library, with its speeds and feeds as your own data "
            "for this job's material."))
        self.btn_to_library.clicked.connect(self._on_tool_to_library)
        lib_row.addWidget(self.btn_from_library)
        lib_row.addWidget(self.btn_to_library)
        lay.addLayout(lib_row)
        form_w = QWidget()
        f = _form(form_w)
        self.t_number = CompactSpin()
        self.t_number.setRange(1, 999)
        self.t_name = QLineEdit()
        self.t_kind = compact_combo()
        for k in ("flatEndMill", "ballEndMill", "bullNoseEndMill", "chamferMill", "drill",
                  "spotDrill"):
            self.t_kind.addItem(tool_kind_label(k), k)
        self.t_diameter = LengthSpin(200.0)
        self.t_flute = LengthSpin(300.0)
        self.t_length = LengthSpin(500.0)
        self.t_flutes = CompactSpin()
        self.t_flutes.setRange(1, 12)
        self.t_rpm = CompactSpin()
        self.t_rpm.setRange(1, 100_000)
        self.t_rpm.setSingleStep(500)
        self.t_rpm.setSuffix(" rpm")
        self.t_feed, self.t_plunge = FeedSpin(), FeedSpin()
        self.t_angle = CompactSpin()
        self.t_angle.setRange(10, 170)
        self.t_angle.setSuffix("°")
        self.t_tip = LengthSpin(50.0)
        rows = ((tr("Number"), self.t_number), (tr("Name"), self.t_name),
                (tr("Type"), self.t_kind), (tr("Included angle"), self.t_angle),
                (tr("Tip diameter"), self.t_tip), (tr("Diameter"), self.t_diameter),
                (tr("Flute length"), self.t_flute), (tr("Overall length"), self.t_length),
                (tr("Flutes"), self.t_flutes), (tr("Spindle speed"), self.t_rpm),
                (tr("Feed"), self.t_feed), (tr("Plunge feed"), self.t_plunge))
        for label, widget in rows:
            f.addRow(label, widget)
        self.t_name.editingFinished.connect(self._on_tool_edited)
        self.t_kind.currentIndexChanged.connect(self._on_tool_edited)
        for s in (self.t_number, self.t_flutes, self.t_rpm, self.t_diameter, self.t_flute,
                  self.t_length, self.t_feed, self.t_plunge, self.t_angle, self.t_tip):
            s.valueChanged.connect(self._on_tool_edited)
        self.tool_form = form_w
        self.tool_form_layout = f
        lay.addWidget(_scroll(form_w), 2)
        return w

    # ---- Operations ----
    def _build_operations(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.paths_label = QLabel(tr("Paths"))
        self.paths_label.setToolTip(tr(
            "The drawing read as machining paths: a rectangle or a solid's outline is one "
            "closed path, lines that meet end to end are one path. Choose paths here or "
            "click any of their edges in the model, then add an operation."))
        lay.addWidget(self.paths_label)
        self.path_list = QListWidget()
        self.path_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.path_list.setMaximumHeight(120)
        self.path_list.setTextElideMode(Qt.ElideRight)
        self.path_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.path_list.itemSelectionChanged.connect(self._on_paths_chosen)
        lay.addWidget(self.path_list)
        self.btn_import = QPushButton(tr("Import outlines from the model"))
        self.btn_import.setToolTip(tr(
            "Copy the outlines of what was selected in the model when CAM opened (faces, "
            "edges or one part), flattened onto the stock. The model is not changed."))
        self.btn_import.clicked.connect(self.import_from_model)
        lay.addWidget(self.btn_import)
        row = QHBoxLayout()
        self.btn_add = QToolButton()
        self.btn_add.setText(tr("Add operation"))
        self.btn_add.setToolTip(tr("For the chosen paths."))
        self.btn_add.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.btn_add)
        for kind in ("outsideProfile", "insideProfile", "pocket", "openPocket", "drilling",
                     "bore", "slot", "chamfer", "engraving"):
            menu.addAction(kind_label(kind), lambda k=kind: self._on_add(k))
        menu.addSeparator()
        menu.addAction(kind_label("facing"), lambda: self._on_add("facing"))
        self.btn_add.setMenu(menu)
        row.addWidget(self.btn_add)
        for label, slot, tip in (("↑", self._on_op_up, tr("Move up")),
                                 ("↓", self._on_op_down, tr("Move down")),
                                 ("✕", self._on_op_delete, tr("Delete"))):
            b = QToolButton()
            b.setText(label)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
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
        h = FlowLayout(box, spacing=8)
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

    # ==== keyboard focus =========================================================
    def give_focus_to_model(self) -> None:
        """Hand the keyboard back to the model. IngeTrazo's tool keys (Space
        for Select, and the rest) are window shortcuts, and a CAM field that
        keeps the focus swallows them: Space typed a blank into the field
        and the tool never changed (reported 2026-09-26)."""
        self.viewport.setFocus(Qt.OtherFocusReason)

    def _hand_back_on_enter(self) -> None:
        """Enter in any CAM field commits it and gives the keyboard back."""
        from PySide6.QtWidgets import QAbstractSpinBox
        for w in self.findChildren(QLineEdit):
            w.returnPressed.connect(self.give_focus_to_model)
        for w in self.findChildren(QAbstractSpinBox):
            if w.lineEdit() is not None:
                w.lineEdit().returnPressed.connect(self.give_focus_to_model)

    # ==== document ⇄ dock =====================================================
    def _document_data(self):
        if not self.in_job():
            return None                   # the model's .igz never carries a job
        pd = getattr(self.viewport.scene, "plugin_data", None)
        return pd.get(PLUGIN_KEY) if isinstance(pd, dict) else None

    def _on_scene_changed(self, _version=None) -> None:
        if not self.in_job():
            return
        if self.isVisible():                # tabbed away: read when shown again
            self._paths_timer.start(PATHS_MS)
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
        self._on_paths_chosen()
        self._schedule_recalc()

    def _changed(self, recalc: bool = True) -> None:
        """Something in the state changed: persist soon, recalculate soon."""
        if self._loading:
            return
        self._persist_timer.start(PERSIST_MS)
        self._result_stale = True
        self.btn_export.setEnabled(False)
        self._set_sim_enabled(False)
        self.overlay.set_stock(self.state)
        self.viewport.update()
        if recalc:
            self._schedule_recalc()

    def _persist(self) -> None:
        from core.history import SetPluginDataCommand
        data = self.state.to_dict()
        if data == self._last_doc:
            return
        self._last_doc = copy.deepcopy(data)
        self.viewport.history.execute(SetPluginDataCommand(PLUGIN_KEY, data))
        self.viewport.notify_scene_changed()

    def has_pending_edit(self) -> bool:
        return self._persist_timer.isActive()

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
            for s in (self.stock_w, self.stock_d, self.stock_h, self.safe,
                      self.clearance, self.t_diameter, self.t_flute, self.t_length, self.t_tip):
                s.set_inch(inch)
            for s in (self.max_feed, self.max_plunge, self.rapid, self.t_feed, self.t_plunge):
                s.set_inch(inch)
            self.op_form.set_units(inch)
            st = job.stock
            self.stock_w.set_mm(st.width)
            self.stock_d.set_mm(st.depth)
            self.stock_h.set_mm(st.height)
            self.material.setCurrentIndex(max(0, self.material.findData(st.material)))
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
        self._apply_setup_gate()
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
            status = self.link_status.get(op.id)
            it = QListWidgetItem(("⚠ " if status else "") + _op_label(op))
            if status:
                it.setToolTip(link_status_text(status))
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
        self.refresh_paths()                # the sizes in the path list
        self._changed(recalc=False)
        self._show_result()

    def _on_stock_edited(self, *_args) -> None:
        if self._loading:
            return
        st = self.state.job.stock
        resized = (abs(st.width - self.stock_w.mm()) > 1e-9
                   or abs(st.depth - self.stock_d.mm()) > 1e-9)
        st.width, st.depth, st.height = self.stock_w.mm(), self.stock_d.mm(), self.stock_h.mm()
        st.material = self.material.currentData() or st.material
        st.align_origin_to_reference()
        self._changed()
        if resized:
            self.frame_stock()

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
        """What the next operation is for: the chosen paths."""
        from ..paths import extraction
        chosen = self.chosen_paths()
        if not chosen:
            raise CamError("empty_selection")
        return extraction(chosen, self._path_frame)

    def _on_add(self, kind: str) -> None:
        chosen = [] if kind == "facing" else self.chosen_paths()
        try:
            ex = None if kind == "facing" else self._extract()
            ops = build.add_operations(self.state, kind, ex)
        except CamError as exc:
            self._report_error(exc.issue)
            return
        self._link(ops, chosen, kind)
        self._after_add(ops)

    # ==== operations follow their paths =======================================
    def _link(self, ops, chosen, kind) -> None:
        """Remember which paths ``ops`` were made from, and in which order
        one Add made them, so they can be made again when the paths
        change (:meth:`_follow_paths`)."""
        from ..paths import signature
        if not chosen:
            return
        sigs = [signature(p) for p in chosen]
        for i, op in enumerate(ops):
            self.state.sources[op.id] = {"kind": "paths", "opKind": kind, "paths": sigs,
                                         "index": i, "count": len(ops)}
            self._op_edges[op.id] = [set(p.edges) for p in chosen]
            self.link_status[op.id] = None

    def _follow_paths(self) -> bool:
        """After the drawing changed: find each operation's paths again and
        rebuild its geometry from them, keeping every setting. Nothing is
        written to the undo stack — the drawing edit is the undo step, and
        undoing it brings the paths, and so the operations, back. An
        operation whose paths are gone, or no longer give the operation it
        was, keeps its last geometry and is flagged. True when any
        operation changed."""
        from ..paths import extraction, find_again, same_shape, signature
        changed = False
        for op in self.state.job.operations:
            src = self.state.sources.get(op.id) or {}
            if src.get("kind") != "paths":
                continue
            taken, found = set(), []
            edges = self._op_edges.get(op.id) or [None] * len(src["paths"])
            for sig, eds in zip(src["paths"], edges):
                i = find_again(sig, self.paths, taken, eds)
                if i is None:
                    break
                taken.add(i)
                found.append(self.paths[i])
            if len(found) != len(src["paths"]):
                self.link_status[op.id] = "path_missing"
                continue
            self._op_edges[op.id] = [set(p.edges) for p in found]
            new_sigs = [signature(p) for p in found]
            if all(same_shape(a, b) for a, b in zip(src["paths"], new_sigs)):
                self.link_status[op.id] = None
                continue
            probe = CamState.from_dict(self.state.to_dict())
            probe.job.operations = []
            try:
                made = build.add_operations(probe, src.get("opKind", op.kind),
                                            extraction(found, self._path_frame))
            except CamError:
                made = []
            if len(made) != src.get("count", 1) or made[src.get("index", 0)].kind != op.kind:
                self.link_status[op.id] = "path_changed"
                continue
            build.copy_geometry(op, made[src.get("index", 0)])
            src["paths"] = new_sigs
            self.link_status[op.id] = None
            changed = True
        return changed

    def _after_add(self, ops) -> None:
        self._refresh_all()
        if self._path_frame is not self.state.frame:
            self.refresh_paths()            # the job just took its plane
        self.tabs.setCurrentIndex(2)
        if ops:
            self.op_list.setCurrentRow(self.state.job.operations.index(ops[-1]))
        self._set_status(tr("{count} operation(s) added.", count=len(ops)))
        self._changed()
        self.give_focus_to_model()

    # ==== the job: new, open, save, leave ========================================
    def in_job(self) -> bool:
        """A CAM job of this dock is shown (CAM mode)."""
        win = self.viewport.window()
        ws = win.workspace() if hasattr(win, "workspace") else None
        return isinstance(ws, CamWorkspace) and ws.dock is self

    def workspace(self):
        return self.viewport.window().workspace() if self.in_job() else None

    def show_page(self) -> None:
        """The start page with the model in front, the job in CAM mode."""
        if self.in_job():
            self.pages.setCurrentIndex(1)
            self.job_title.setText(self.workspace().title())
        else:
            self._refresh_recent()
            self.pages.setCurrentIndex(0)

    #: Where the user's stock templates live (tests point it elsewhere).
    template_folder = None

    def templates_dir(self):
        if self.template_folder is not None:
            return Path(self.template_folder)
        from PySide6.QtCore import QStandardPaths
        base = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
        return Path(base or Path.home() / ".ingetrazo") / "cam-templates"

    def new_from_template(self, template=None, path=None) -> bool:
        """A new job with a template's setup (``template`` given: no
        chooser; ``path`` given: no file dialog)."""
        if template is None:
            from .template_dialog import TemplateDialog
            dlg = TemplateDialog(self.templates_dir(), self)
            if dlg.exec() != TemplateDialog.Accepted or dlg.chosen() is None:
                return False
            template = dlg.chosen()
        return self.new_job(path, template=template)

    def save_template(self, name=None):
        """Save this job's setup as a template (``name`` given: no
        prompt); returns the file, or None."""
        from ..templates import save_template
        if name is None:
            from PySide6.QtWidgets import QInputDialog
            name, ok = QInputDialog.getText(self, tr("Save setup as template"),
                                            tr("Template name"), text=self.state.job.name)
            if not ok or not name.strip():
                return None
        self.flush()
        try:
            path = save_template(self.state, name.strip(), self.templates_dir())
        except (OSError, JobFileError) as exc:
            self._set_status(tr("The template could not be saved: {error}", error=str(exc)),
                             error=True)
            return None
        self._set_status(tr("Template «{name}» saved.", name=name.strip()))
        return path

    def new_job(self, path=None, template=None) -> bool:
        """Start a CAM job. The file comes first — the job is named after
        it — then the setup (``path`` given: no dialog)."""
        if path is None:
            path_str, _ = QFileDialog.getSaveFileName(
                self, tr("New CAM job"), tr("New job") + SUFFIX, file_filter())
            if not path_str:
                return False
            path = Path(path_str)
        path = Path(path)
        if path.suffix.lower() != SUFFIX:
            path = path.with_suffix(SUFFIX)
        if not self._leave_current_job():
            return False
        from core.scene import Scene
        scene = Scene()
        if template is not None:
            from ..templates import job_from_template
            state = job_from_template(template, path.stem)
        else:
            state = CamState.new_job(path.stem, translate=tr)
        scene.plugin_data = {PLUGIN_KEY: state.to_dict()}
        try:
            save_job(scene, path)
        except (OSError, JobFileError) as exc:
            self._set_status(tr("The job could not be saved: {error}", error=str(exc)),
                             error=True)
            return False
        return self._enter(scene, path)

    def open_job(self, path=None) -> bool:
        """Open a saved CAM job (``path`` given: no dialog)."""
        if path is None:
            path_str, _ = QFileDialog.getOpenFileName(self, tr("Open CAM job"), "",
                                                      file_filter())
            if not path_str:
                return False
            path = Path(path_str)
        path = Path(path)
        if not self._leave_current_job():
            return False
        from core.scene import Scene
        scene = Scene()
        try:
            load_job(scene, path)
        except JobFileError as exc:
            QMessageBox.warning(self, tr("CAM"), tr("{name} is not a CAM job this version "
                                                    "can open ({error}).", name=path.name,
                                                    error=str(exc)))
            return False
        return self._enter(scene, path)

    def _leave_current_job(self) -> bool:
        win = self.viewport.window()
        if hasattr(win, "workspace") and win.workspace() is not None:
            return win.leave_workspace()
        return True

    def _enter(self, scene, path) -> bool:
        win = self.viewport.window()
        self.state = CamState.from_dict(scene.plugin_data[PLUGIN_KEY])
        ws = CamWorkspace(self, scene, path)
        if not win.enter_workspace(ws):
            return False
        ws.mark_saved()                     # entering moved the version on
        remember_job(path)
        self.paths, self._path_frame = [], None
        self._op_edges, self.link_status = {}, {}
        self._reload_from_document(force=True)
        self.refresh_paths()
        self.show_page()
        self.frame_stock()
        self.tabs.setCurrentIndex(0 if not self.state.setupDone else 2)
        self.show()
        self.raise_()
        win._update_title()
        return True

    def frame_stock(self) -> None:
        """Look straight down on the whole stock (a job just opened, or its
        stock was resized during the setup)."""
        from .workspace import plan_camera
        if not self.in_job():
            return
        vp = self.viewport
        st = self.state.job.stock
        aspect = vp.width() / max(1, vp.height())
        self.viewport.window()._apply_camera_dict(plan_camera(st.width, st.depth, aspect))

    def save_job(self) -> bool:
        ws = self.workspace()
        ok = bool(ws and ws.save())
        self.viewport.window()._update_title()
        return ok

    def on_job_saved(self) -> None:
        ws = self.workspace()
        if ws is not None:
            self.job_title.setText(ws.title())
            self._set_status(tr("Saved {name}.", name=ws.title()))

    def leave_job(self) -> bool:
        return self._leave_current_job()

    def on_workspace_left(self, _ws) -> None:
        """The job is closed and the model is back: the start page again."""
        self._persist_timer.stop()
        self._recalc_timer.stop()
        self._paths_timer.stop()
        self._last_doc = None
        self._result = None
        self.paths, self._path_frame = [], None
        self.path_list.clear()
        self.overlay.clear()
        self.state = CamState.new(translate=tr)
        self.show_page()
        self.viewport.update()

    def _on_start_job(self) -> None:
        """Confirm the setup: drawing and operations open up."""
        self.state.setupDone = True
        self._changed()
        self._apply_setup_gate()
        self.tabs.setCurrentIndex(2)
        self._set_status(tr("Setup confirmed. Draw on the stock, then choose paths and add "
                            "operations."))
        self.give_focus_to_model()

    def _apply_setup_gate(self) -> None:
        done = self.state.setupDone
        for i in (1, 2, 3):
            self.tabs.setTabEnabled(i, done)
        self.btn_start.setVisible(not done)
        self.setup_note.setVisible(not done)
        ws = self.workspace()
        if ws is not None:
            self.viewport.window().set_tool_filter(ws.allowed_tools)

    def import_from_model(self) -> int:
        """Copy the outlines of what is selected in the parked model into
        the job, flattened onto the stock top; returns how many loops and
        lines came in. The model is only read."""
        win = self.viewport.window()
        parked = getattr(win, "_parked", None)
        model = parked.get("scene") if parked else None
        if model is None or not getattr(model, "selection", None):
            self._set_status(tr("Nothing is selected in the model. Select faces, edges or a "
                                "part there before opening the job."), error=True)
            return 0
        from ..extract import extract_selection
        try:
            ex = extract_selection(model, None)
        except CamError as exc:
            self._report_error(exc.issue)
            return 0
        loops = [o.points for o, _h in ex.regions] + [h.points for _o, hs in ex.regions
                                                         for h in hs]
        loops += [lp.points for lp in ex.loops]
        lines = list(ex.paths)
        pts = [p for lp in loops + lines for p in lp]
        if not pts:
            return 0
        # Onto the stock: the outlines' lower-left corner on the stock's,
        # 10 mm in, or centred when they fit with less.
        st = self.state.job.stock
        umin, vmin = min(p[0] for p in pts), min(p[1] for p in pts)
        umax, vmax = max(p[0] for p in pts), max(p[1] for p in pts)
        du = (st.width - (umax - umin)) * 0.5 - umin
        dv = (st.depth - (vmax - vmin)) * 0.5 - vmin
        from PySide6.QtGui import QVector3D
        from core.history import SnapshotImport

        def world(p):
            return QVector3D((p[0] + du) / 1000.0, (p[1] + dv) / 1000.0, 0.0)

        def add(scene):
            m = scene.mesh
            for lp in loops:
                ring = [world(p) for p in lp]
                for a, b in zip(ring, ring[1:] + ring[:1]):
                    m.add_edge(a, b)
            for ln in lines:
                ring = [world(p) for p in ln]
                for a, b in zip(ring, ring[1:]):
                    m.add_edge(a, b)

        self.flush()                        # a pending job edit goes first on the stack
        self.viewport.history.execute(SnapshotImport(add))
        self.viewport.notify_scene_changed()
        self.refresh_paths()
        n = len(loops) + len(lines)
        self._set_status(tr("{count} outline(s) imported from the model.", count=n))
        return n

    # ==== paths ===============================================================
    def refresh_paths(self) -> None:
        """Read the drawing as paths again (after an edit), keeping the
        paths that were chosen chosen."""
        from ..paths import find_paths, plan_frame
        if not self.in_job():
            return
        scene = self.viewport.scene
        before = set().union(*(p.edges for p in self.chosen_paths())) if self.paths else set()
        frame = self.state.frame or plan_frame(scene)
        try:
            self.paths = find_paths(scene, frame) if frame is not None else []
        except Exception:  # noqa: BLE001 — a model the reader trips on must not break the dock
            self.paths = []
        self._path_frame = frame
        inch = self.state.job.is_inch
        self.path_list.blockSignals(True)
        self.path_list.clear()
        from ..paths import problems
        st = self.state.job.stock
        for n, p in enumerate(self.paths, 1):
            bad = problems(p, st.width, st.depth)
            text = _path_label(p, n, inch)
            item = QListWidgetItem(("⚠ " + text) if bad else text)
            item.setToolTip("\n".join([text] + [path_problem_text(c) for c in bad]))
            if bad:
                item.setForeground(QColor(210, 120, 0))
            self.path_list.addItem(item)
            item.setSelected(bool(before & p.edges))
        self.path_list.blockSignals(False)
        self.paths_label.setText(tr("Paths ({count})", count=len(self.paths)))
        # The choice survives the re-read (by edges, above). The model's
        # selection is NOT applied again here: only a change of it counts,
        # or every edit would wipe paths chosen in the list.
        self._on_paths_chosen()
        before_status = dict(self.link_status)
        if self._follow_paths():
            self._result_stale = True
            self.overlay.set_outline(self.state, self._current_op())
            self._schedule_recalc()
        if self.link_status != before_status:
            self._refresh_operations()      # only then: never under the user's typing

    def chosen_paths(self) -> list:
        rows = sorted(i.row() for i in self.path_list.selectedIndexes())
        return [self.paths[r] for r in rows if r < len(self.paths)]

    def choose_paths(self, rows) -> None:
        self.path_list.blockSignals(True)
        for r in range(self.path_list.count()):
            self.path_list.item(r).setSelected(r in rows)
        self.path_list.blockSignals(False)
        self._on_paths_chosen()

    def _on_paths_chosen(self) -> None:
        if self._path_frame is not None:
            self.overlay.set_paths(self._path_frame, self.chosen_paths())
        self.viewport.update()

    def _sync_paths_from_model(self) -> None:
        """Clicking one edge of a rectangle in the model chooses the whole
        rectangle here (and a face, its outline and holes)."""
        if self.isHidden() or not self.in_job():
            return
        from core.mesh import Edge, Face
        from ..paths import face_edges, paths_for_edges
        sel = getattr(self.viewport.scene, "selection", ()) or ()
        key = frozenset(id(e) for e in sel)
        if key == self._sel_key:
            return
        self._sel_key = key
        ids: set = set()
        for ent in sel:
            if isinstance(ent, Edge):
                ids.add(id(ent))
            elif isinstance(ent, Face):
                ids |= face_edges(ent)
        self.choose_paths(set(paths_for_edges(self.paths, ids)) if ids else set())

    # ==== tools ===============================================================
    def _current_tool(self):
        tools = self._sorted_tools()
        r = self.tool_list.currentRow()
        return tools[r] if 0 <= r < len(tools) else None

    def _on_tool_selected(self, _row) -> None:
        t = self._current_tool()
        self.tool_form.setEnabled(t is not None)
        self.btn_to_library.setEnabled(t is not None)
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
            self.t_angle.setValue(int(round(t.includedAngle or 90)))
            self.t_tip.set_mm(t.tipDiameter or 0.0)
            v = t.kind == "chamferMill"
            self.tool_form_layout.setRowVisible(self.t_angle, v)
            self.tool_form_layout.setRowVisible(self.t_tip, v)
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
        if t.kind == "chamferMill":
            t.includedAngle = float(self.t_angle.value())
            t.tipDiameter = self.t_tip.mm()
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

    def _on_tool_from_library(self) -> None:
        repo = library.shared_library(self)
        if repo is None:
            return
        dlg = library.ToolLibraryDialog(repo, self.state.job, pick=True, parent=self)
        if dlg.exec() != QDialog.Accepted or dlg.chosen is None:
            return
        self.state.job.tools.append(dlg.chosen)
        self._refresh_tools(select=self._sorted_tools().index(dlg.chosen))
        self._changed()

    def _on_tool_to_library(self) -> None:
        t = self._current_tool()
        repo = library.shared_library(self) if t is not None else None
        if repo is None:
            return
        cls = library.material_class_for(self.state.job)
        library.save_job_tool(repo, t, cls)
        if cls is None:
            text = tr("«{name}» is now in your tool library, without speeds and feeds: this "
                      "stock material has no class in the library.", name=t.name)
        else:
            text = tr("«{name}» is now in your tool library, with its speeds and feeds for "
                      "{material}.", name=t.name, material=messages.material_class_label(cls))
        QMessageBox.information(self, tr("Tool library"), text)

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
        self.overlay.set_outline(self.state, op)
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
        self.overlay.set_outline(self.state, op)
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
            self._set_status(tr("Choose a path and add an operation to begin."))
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
            self._set_sim_enabled(False)
            return
        if not out.ok:
            self._set_status(messages.describe(out.error, inch), error=True)
            self.stats.setText("")
            self.btn_export.setEnabled(False)
            self._set_sim_enabled(False)
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
        self._set_sim_enabled(True)
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
        if self.isVisible():
            self._paths_timer.start(0)      # the model may have changed meanwhile
        self.viewport.update()

    # ==== simulation ===========================================================
    def _set_sim_enabled(self, on: bool) -> None:
        self.btn_sim.setEnabled(on)
        self.btn_sim_header.setEnabled(on)

    def simulate(self):
        """Open (or bring forward) the stock simulation of the current
        result; returns the window."""
        from .simview import SimulationWindow
        if self._result is None or not self._result.ok:
            return None
        if self._sim is None:
            self._sim = SimulationWindow(self)
            self._sim.playhead.connect(self._on_playhead)
        job = self.state.work_job()
        listing = None
        try:
            from ..engine.post import listing as post_listing, post_job
            res = post_job(job, self._result.compiled.toolpath, translate=tr)
            listing = post_listing(res, _safe_name(job.name))
        except CamError:
            pass                          # the simulation still runs; the panel stays empty
        self._sim.load(job, self._result.compiled, listing)
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
        if result.tool_table:
            # LinuxCNC stops at a T<n> M6 its tool table lacks: give it one.
            tbl = base.with_name(base.name + ".tbl")
            tbl.write_text(result.tool_table, encoding="ascii", newline="\n")
            written.append(str(tbl))
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
        self._sel_timer.stop()
        self._paths_timer.stop()
        if self.overlay in self.viewport.overlay_painters:
            self.viewport.overlay_painters.remove(self.overlay)
        try:
            self.viewport.sceneVersionChanged.disconnect(self._on_scene_changed)
        except (RuntimeError, TypeError):
            pass


def _style_zero_pad(pad: QWidget) -> None:
    """The 9-point work-zero picker, drawn as the stock seen from above: a
    framed rectangle with a small square at each point, the chosen one
    filled with the highlight colour.

    Palette ROLES, not colours, so IngeTrazo's theme switch re-resolves
    them (views/theme.py re-sets every stylesheet). Outlines use the
    placeholder-text grey: in the dark palette the usual ``mid`` role is
    within a shade of the window colour, which is why the unlabelled
    radio buttons this replaced read as black on black (reported
    2026-09-26)."""
    pad.setStyleSheet(
        "#cam_zero_pad { border: 2px solid palette(placeholder-text); border-radius: 4px;"
        " background: palette(base); }"
        "#cam_zero_pad QToolButton { border: 1px solid palette(placeholder-text);"
        " border-radius: 3px; background: palette(window); }"
        "#cam_zero_pad QToolButton:hover { border: 1px solid palette(highlight); }"
        "#cam_zero_pad QToolButton:checked { background: palette(highlight);"
        " border: 2px solid palette(text); }")


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


def path_problem_text(code: str) -> str:
    return {
        "crosses_itself": tr("The path crosses itself."),
        "outside_stock": tr("The path is outside the stock."),
        "partly_outside_stock": tr("The path is partly outside the stock."),
    }.get(code, code)


def link_status_text(code: str) -> str:
    return {
        "path_missing": tr("Its path is no longer in the drawing. The operation keeps its "
                           "last geometry."),
        "path_changed": tr("Its path changed too much to make the same operation. The "
                           "operation keeps its last geometry."),
    }.get(code, code)


def material_label(key: str) -> str:
    return {
        "clearWood": tr("Light wood"), "darkWood": tr("Dark wood"), "mdf": tr("MDF"),
        "plywood": tr("Plywood"), "plastic": tr("Plastic"), "aluminium": tr("Aluminium"),
        "steel": tr("Steel"),
    }.get(key, key)


def _path_label(p, n: int, inch: bool) -> str:
    """How a path reads in the list: its kind and its size."""
    size = lambda mm: messages.format_length(mm, inch)  # noqa: E731
    if p.circle:
        return tr("Circle {n} · Ø{diameter}", n=n, diameter=size(p.circle[2]))
    if p.closed:
        du, dv = p.extent
        text = tr("Hole {n} · {width} × {depth}") if p.hole else \
            tr("Closed path {n} · {width} × {depth}")
        return text.format(n=n, width=size(du), depth=size(dv))
    return tr("Open path {n} · {length}", n=n, length=size(p.length))


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

