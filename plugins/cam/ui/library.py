# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The tool library window, and the two doors between it and a job.

The library (:mod:`..toollib`) is the shop's tool cabinet, one SQLite file
per user shared by every job. From the dock's Tools tab:

- **From the library…** opens :class:`ToolLibraryDialog` to pick a cutter.
  Its cutting data is resolved for the job's stock material and machine
  and COPIED into the job's tool table — the job never refers back to the
  library, so it opens the same on a machine with another library.
- **Save to the library** keeps a job's tool: its geometry as a library
  tool, its feeds and speeds as the user's own preset for the job's
  material.

The window is a dialog, not a dock tab: a tool table wants width, and the
CAM dock must stay narrow. Everything the library says comes as codes;
:mod:`.messages` has the sentences.

Where the file lives: ``cam/ToolLibrary.sqlite`` in the app's data folder,
unless the user picked another one (``Library file…``) — on a Mac, for
instance, 2DCam's own library, so both apps share one cabinet.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6 import QtCore
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QTableWidget,
                               QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget)

from views.tray import FlowLayout

from ..engine.models import MM_PER_INCH
from ..i18n import tr
from ..toollib import importer
from ..toollib.catalog import CatalogProfile, ProfileError, builtin_profiles
from ..toollib.csvread import CSVError
from ..toollib.model import CAM_KIND, TOOL_TYPES, LibraryTool, LibraryToolError
from ..toollib.repository import FILE_NAME, LibraryError, open_library
from ..toollib.resolver import CuttingPreset, MachineLimits, resolve
from ..toollib.validator import ToolDraft, ToolValidator
from . import messages
from .op_forms import CompactSpin, LengthSpin, compact_combo

SETTINGS_KEY = "cam/toolLibraryFile"

#: A job's stock material → the library's material class for feeds. Steel
#: has no class: a router library has no data for it, and a guess would be
#: dangerous — the user picks a class, or types the feeds.
CLASS_FOR_STOCK = {"clearWood": "softwood", "darkWood": "hardwood", "mdf": "mdf",
                   "plywood": "plywood", "plastic": "acrylic", "aluminium": "aluminium",
                   "steel": None}


# ---- where the library lives ---------------------------------------------------------

def default_library_path() -> Path:
    base = QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.AppDataLocation)
    return Path(base or Path.home()) / "cam" / FILE_NAME


def twodcam_library_path() -> Path | None:
    """2DCam's own library file, on a Mac that has 2DCam."""
    if sys.platform != "darwin":
        return None
    p = Path.home() / "Library" / "Application Support" / "2DCam" / "ToolLibrary.db"
    return p if p.is_file() else None


def library_path() -> Path:
    chosen = QtCore.QSettings().value(SETTINGS_KEY, "") or ""
    return Path(chosen) if chosen else default_library_path()


_open = {"path": None, "repo": None}


def shared_library(parent=None):
    """The library, opened once per session and per file. Tells the user,
    once, if a damaged file had to be replaced. None if it cannot open."""
    path = library_path()
    if _open["repo"] is not None and _open["path"] == path:
        return _open["repo"]
    close_shared_library()
    try:
        repo, restored = open_library(path)
    except (LibraryError, OSError) as exc:
        QMessageBox.warning(parent, tr("Tool library"),
                            messages.library_error("open", detail=str(exc)))
        return None
    if restored:
        QMessageBox.information(parent, tr("Tool library"),
                                messages.library_error(f"restored_{restored}"))
    _open.update(path=path, repo=repo)
    return repo


def close_shared_library() -> None:
    if _open["repo"] is not None:
        _open["repo"].close()
    _open.update(path=None, repo=None)


# ---- the doors between the library and a job -------------------------------------------

def material_class_for(job) -> str | None:
    return CLASS_FOR_STOCK.get(job.stock.material, "softwood")


def machine_limits(job) -> MachineLimits:
    return MachineLimits.from_job_machine(job.machine)


def next_tool_number(job) -> int:
    return max((t.number for t in job.tools), default=0) + 1


def library_tool_into_job(repo, job, tool: LibraryTool, class_id: str | None):
    """``(job_tool, None)`` or ``(None, message)``: the library tool with its
    cutting data for ``class_id`` on the job's machine, numbered after the
    job's last tool. Nothing is guessed: no data, no tool."""
    if tool.type not in CAM_KIND:
        return None, messages.library_error("form_tool_unsupported", name=tool.name)
    if tool.missing_fields:
        return None, messages.library_error(
            "incomplete", name=tool.name, fields=messages.missing_fields_text(tool.missing_fields))
    resolved = repo.resolve(tool, class_id, machine_limits(job)) if class_id else None
    if resolved is None:
        return None, messages.library_error(
            "no_cutting_data", name=tool.name,
            material=messages.material_class_label(class_id or ""))
    try:
        return tool.to_job_tool(next_tool_number(job), resolved), None
    except LibraryToolError as exc:
        return None, messages.library_error(
            exc.code, name=exc.name, fields=messages.missing_fields_text(exc.missing))


def save_job_tool(repo, job_tool, class_id: str | None) -> LibraryTool:
    """Keep a job's tool in the library: geometry as a tool, its speed and
    feeds as the user's own preset for ``class_id`` (when known)."""
    lib = LibraryTool.from_job_tool(job_tool)
    with repo.transaction():
        repo.insert(lib)
        repo.add_to_group(lib.id, "g-mine")
        if class_id:
            repo.upsert_preset(CuttingPreset(
                tool_id=lib.id, material_class_id=class_id,
                name=messages.material_class_label(class_id), origin="user", confidence=4,
                spindle_rpm=job_tool.spindleRPM, feed_xy_mm_min=job_tool.cuttingFeed,
                feed_z_mm_min=job_tool.plungeFeed, stepdown_mm=job_tool.authoritative_stepdown))
    return lib


# ---- small helpers ---------------------------------------------------------------------

def _length(mm, inch: bool) -> str:
    return "—" if mm is None else messages.format_length(mm, inch)


def _feed(mm_min, inch: bool) -> str:
    if inch:
        return f"{mm_min / MM_PER_INCH:.1f} in/min"
    return f"{mm_min:.0f} mm/min"


def _item(text: str, data=None) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    if data is not None:
        it.setData(Qt.UserRole, data)
    return it


def _wide_combo() -> QComboBox:
    """A list as wide as its longest entry. (The dock's compact combos are
    for the narrow dock; these dialogs have the room.)"""
    c = QComboBox()
    c.setSizeAdjustPolicy(QComboBox.AdjustToContents)
    return c


def _buttons_row(*buttons) -> FlowLayout:
    row = FlowLayout(spacing=4)
    for b in buttons:
        row.addWidget(b)
    return row


# ---- the library window ------------------------------------------------------------------

class ToolLibraryDialog(QDialog):
    """Browse, edit and pick. ``pick`` adds an «Add to job» button; after
    ``exec()`` returns Accepted, :attr:`chosen` is the job tool."""

    @staticmethod
    def headers() -> list:
        return [tr("Name"), tr("Type"), tr("Diameter"), tr("Flutes"), tr("Cutting length"),
                tr("Vendor"), tr("Speed"), tr("Feed")]

    def __init__(self, repo, job, pick: bool = False, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Tool library"))
        self.repo = repo
        self.job = job
        self.pick = pick
        self.inch = job.is_inch
        self.chosen = None
        self._tools = []
        self.resize(980, 620)

        lay = QVBoxLayout(self)
        top = FlowLayout(spacing=6)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Search name, code, vendor…"))
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(260)
        self.search.textChanged.connect(self.refresh)
        top.addWidget(self.search)
        self.type_filter = _wide_combo()
        self.type_filter.addItem(tr("All types"), None)
        for t in TOOL_TYPES:
            self.type_filter.addItem(messages.tool_type_label(t), t)
        self.type_filter.currentIndexChanged.connect(self.refresh)
        top.addWidget(self.type_filter)
        top.addWidget(QLabel(tr("Feeds for")))
        self.material = _wide_combo()
        for mc in repo.material_classes():
            self.material.addItem(_class_label(mc), mc.id)
        wanted = material_class_for(job)
        if wanted is not None:
            self.material.setCurrentIndex(max(0, self.material.findData(wanted)))
        self.material.currentIndexChanged.connect(self.refresh)
        top.addWidget(self.material)
        lay.addLayout(top)

        headers = self.headers()
        self.table = QTableWidget(0, len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for c in range(1, len(headers)):
            self.table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._on_selected)
        self.table.cellDoubleClicked.connect(self._on_double_click)
        lay.addWidget(self.table, 3)

        self.detail = QLabel()
        self.detail.setWordWrap(True)
        self.detail.setTextFormat(Qt.RichText)
        self.detail.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.detail.setMinimumHeight(90)
        lay.addWidget(self.detail, 1)

        self.btn_new = QPushButton(tr("New…"))
        self.btn_edit = QPushButton(tr("Edit…"))
        self.btn_dup = QPushButton(tr("Duplicate"))
        self.btn_del = QPushButton(tr("Delete"))
        self.btn_trash = QPushButton(tr("Trash…"))
        self.btn_import = QPushButton(tr("Import catalogue…"))
        self.btn_undo = QPushButton(tr("Undo last import"))
        self.btn_file = QPushButton(tr("Library file…"))
        for b, slot in ((self.btn_new, self._on_new), (self.btn_edit, self._on_edit),
                        (self.btn_dup, self._on_duplicate), (self.btn_del, self._on_delete),
                        (self.btn_trash, self._on_trash), (self.btn_import, self._on_import),
                        (self.btn_undo, self._on_undo_import), (self.btn_file, self._on_file)):
            b.clicked.connect(slot)
        lay.addLayout(_buttons_row(self.btn_new, self.btn_edit, self.btn_dup, self.btn_del,
                                   self.btn_trash, self.btn_import, self.btn_undo,
                                   self.btn_file))

        self.status = QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        self.btn_add = None
        if pick:
            self.btn_add = box.addButton(tr("Add to job"), QDialogButtonBox.AcceptRole)
            self.btn_add.clicked.connect(self._on_add)
        lay.addWidget(box)
        self.refresh()

    # ---- data -------------------------------------------------------------
    def class_id(self):
        return self.material.currentData()

    def refresh(self, *_args) -> None:
        keep = self.current_tool().id if self.current_tool() else None
        needle = self.search.text().strip().casefold()
        wanted_type = self.type_filter.currentData()
        vendors = dict(self.repo.vendors())
        self._vendors = vendors
        classes = {m.id: m for m in self.repo.material_classes()}
        mc = classes.get(self.class_id())
        rules, curves = self.repo.chipload_rules(), self.repo.feed_curves()
        presets = self.repo.presets()
        machine = machine_limits(self.job)
        rows = []
        for t in self.repo.tools():
            vendor = vendors.get(t.vendor_id, "")
            hay = " ".join(filter(None, (t.name, t.product_id, t.series, vendor))).casefold()
            if needle and needle not in hay:
                continue
            if wanted_type and t.type != wanted_type:
                continue
            r = (resolve(t, mc, machine=machine, presets=[p for p in presets if p.tool_id == t.id],
                         rules=rules, feed_curves=curves) if mc is not None else None)
            rows.append((t, vendor, r))
        self._tools = rows
        self.table.setRowCount(len(rows))
        select = 0
        for i, (t, vendor, r) in enumerate(rows):
            if t.id == keep:
                select = i
            type_text = messages.tool_type_label(t.type)
            cells = [t.name, type_text, t.nominal_label or _length(t.diameter_mm, self.inch),
                     "—" if t.flute_count is None else str(t.flute_count),
                     _length(t.flute_length_mm, self.inch), vendor]
            if not t.can_go_into_a_job:
                cells += [tr("incomplete"), ""]
            elif r is None:
                cells += [tr("no data"), ""]
            else:
                mark = "≈ " if r.is_estimate else ""
                cells += [f"{mark}{r.spindle_rpm} rpm", mark + _feed(r.feed_xy_mm_min, self.inch)]
            for c, text in enumerate(cells):
                self.table.setItem(i, c, _item(text, t.id if c == 0 else None))
        if rows:
            self.table.selectRow(select)
        self._on_selected()
        self.btn_undo.setEnabled(self.repo.last_committed_batch() is not None)

    def current_tool(self):
        r = self.table.currentRow() if hasattr(self, "table") else -1
        return self._tools[r][0] if 0 <= r < len(self._tools) else None

    def _current_row(self):
        r = self.table.currentRow()
        return self._tools[r] if 0 <= r < len(self._tools) else None

    def _on_selected(self) -> None:
        row = self._current_row()
        has = row is not None
        for b in (self.btn_edit, self.btn_dup, self.btn_del):
            b.setEnabled(has)
        if self.btn_add is not None:
            self.btn_add.setEnabled(has and row[0].can_go_into_a_job and row[2] is not None)
        if not has:
            self.detail.setText(tr("The library is empty for this search."))
            return
        t, vendor, r = row
        lines = [f"<b>{_esc(t.name)}</b> — {_esc(messages.tool_type_label(t.type))}"]
        if t.type not in CAM_KIND:
            lines.append(_esc(messages.library_error("form_tool_unsupported", name=t.name)))
        elif t.missing_fields:
            lines.append(_esc(tr("Missing: {fields}",
                                 fields=messages.missing_fields_text(t.missing_fields))))
        elif r is None:
            lines.append(_esc(messages.library_error(
                "no_cutting_data", name=t.name,
                material=messages.material_class_label(self.class_id() or ""))))
        else:
            lines.append(_esc(messages.cutting_data_source(r, self._vendors)))
            if r.is_estimate:
                lines.append("<i>" + _esc(tr(
                    "An estimate, not tested data: check it with an air cut before cutting.")) +
                    "</i>")
            lines.append(_esc(tr(
                "{rpm} rpm, feed {feed}, plunge {plunge}, step-down {stepdown}, stepover "
                "{stepover}", rpm=r.spindle_rpm, feed=_feed(r.feed_xy_mm_min, self.inch),
                plunge=_feed(r.feed_z_mm_min, self.inch),
                stepdown=_length(r.stepdown_mm, self.inch),
                stepover=_length(r.stepover_mm, self.inch))))
            lines += ["• " + _esc(messages.library_note(n, self.inch)) for n in r.notes]
        self.detail.setText("<br>".join(lines))

    # ---- actions ------------------------------------------------------------
    def _on_double_click(self, *_args) -> None:
        if self.pick and self.btn_add is not None and self.btn_add.isEnabled():
            self._on_add()
        else:
            self._on_edit()

    def _on_add(self) -> None:
        t = self.current_tool()
        if t is None:
            return
        job_tool, problem = library_tool_into_job(self.repo, self.job, t, self.class_id())
        if job_tool is None:
            QMessageBox.information(self, tr("Tool library"), problem)
            return
        self.chosen = job_tool
        self.accept()

    def _on_new(self) -> None:
        ed = ToolEditor(LibraryTool(name=tr("New tool")), self.repo, self.inch, self)
        if ed.exec() == QDialog.Accepted:
            tool = self.repo.insert(ed.tool)
            self.repo.add_to_group(tool.id, "g-mine")
            self.refresh()
            self._select(tool.id)

    def _on_edit(self) -> None:
        t = self.current_tool()
        if t is None:
            return
        ed = ToolEditor(t, self.repo, self.inch, self)
        if ed.exec() == QDialog.Accepted:
            self.repo.update(ed.tool, mark_user_edited=ed.edited_fields())
            self.refresh()
            self._select(t.id)

    def _on_duplicate(self) -> None:
        t = self.current_tool()
        if t is not None:
            copy = self.repo.duplicate(t.id, suffix=tr(" (copy)"))
            self.refresh()
            if copy is not None:
                self._select(copy.id)

    def _on_delete(self) -> None:
        t = self.current_tool()
        if t is not None:
            self.repo.delete(t.id)
            self.status.setText(tr("«{name}» moved to the Trash.", name=t.name))
            self.refresh()

    def _on_trash(self) -> None:
        TrashDialog(self.repo, self).exec()
        self.refresh()

    def _on_import(self) -> None:
        dlg = ImportDialog(self.repo, self.job, self)
        if dlg.exec() == QDialog.Accepted and dlg.result_ is not None:
            r = dlg.result_
            self.status.setText(tr("Imported: {new} new, {updated} updated, {rejected} rejected.",
                                   new=r.inserted, updated=r.updated, rejected=r.rejected))
            self.refresh()

    def _on_undo_import(self) -> None:
        batch = self.repo.last_committed_batch()
        if batch is None:
            return
        if QMessageBox.question(self, tr("Tool library"), tr(
                "Remove the tools the last catalogue import added? Tools it updated keep "
                "their new values.")) != QMessageBox.Yes:
            return
        self.repo.rollback_batch(batch)
        self.status.setText(tr("The last import was undone."))
        self.refresh()

    def _on_file(self) -> None:
        dlg = LibraryFileDialog(self)
        if dlg.exec() == QDialog.Accepted:
            repo = shared_library(self)
            if repo is not None:
                self.repo = repo
                self.material.blockSignals(True)
                current = self.class_id()
                self.material.clear()
                for mc in repo.material_classes():
                    self.material.addItem(_class_label(mc), mc.id)
                self.material.setCurrentIndex(max(0, self.material.findData(current)))
                self.material.blockSignals(False)
                self.refresh()

    def _select(self, tool_id) -> None:
        for i, (t, _v, _r) in enumerate(self._tools):
            if t.id == tool_id:
                self.table.selectRow(i)
                return


def _class_label(mc) -> str:
    """The seeded classes in the user's language; any other by its name."""
    label = messages.material_class_label(mc.id)
    return mc.name if label == mc.id else label


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---- one tool's editor ---------------------------------------------------------------

class _OptionalLength(LengthSpin):
    """A length that may be unknown: the lowest value reads «unknown»."""

    def __init__(self, maximum_mm: float = 500.0):
        super().__init__(maximum_mm, -1.0)
        self.setSpecialValueText(tr("unknown"))

    def get(self):
        v = self.mm()
        return None if v < 0 else v

    def put(self, mm) -> None:
        self.set_mm(-1.0 if mm is None else mm)


class _OptionalCount(CompactSpin):
    def __init__(self, maximum: int):
        super().__init__()
        self.setRange(0, maximum)
        self.setSpecialValueText(tr("unknown"))

    def get(self):
        return self.value() or None

    def put(self, v) -> None:
        self.setValue(v or 0)


class _OptionalAngle(QDoubleSpinBox):
    def __init__(self):
        super().__init__()
        self.setRange(0.0, 179.0)
        self.setDecimals(1)
        self.setSuffix("°")
        self.setSpecialValueText(tr("unknown"))

    def get(self):
        return self.value() or None

    def put(self, v) -> None:
        self.setValue(v or 0.0)


#: Editor attribute → the ``field_provenance`` name a hand edit is recorded
#: under (the database column, as 2DCam records it).
_PROVENANCE = {"name": "name", "type": "tool_type", "diameter_mm": "diameter_mm",
               "corner_radius_mm": "corner_radius_mm", "included_angle_deg": "included_angle_deg",
               "tip_dia_mm": "tip_dia_mm", "flute_count": "flute_count",
               "flute_length_mm": "flute_length_mm", "shank_dia_mm": "shank_dia_mm",
               "overall_length_mm": "overall_length_mm", "product_id": "product_id",
               "series": "series", "notes": "notes"}


class ToolEditor(QDialog):
    """Name, type, vendor and geometry of one library tool. Unknown stays
    unknown; the catalogue checks run on save (errors stop it, warnings
    ask)."""

    def __init__(self, tool: LibraryTool, repo, inch: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Library tool"))
        self.tool = tool
        self._before = {k: getattr(tool, k) for k in _PROVENANCE}
        f = QFormLayout(self)
        f.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.name = QLineEdit(tool.name)
        self.type = compact_combo()
        for t in TOOL_TYPES:
            self.type.addItem(messages.tool_type_label(t), t)
        self.type.setCurrentIndex(max(0, self.type.findData(tool.type)))
        self.vendor = QComboBox()
        self.vendor.setEditable(True)
        self.vendor.addItem("", None)
        vendors = repo.vendors()
        for vid, name in vendors:
            self.vendor.addItem(name, vid)
        current_vendor = dict(vendors).get(tool.vendor_id, tool.vendor_name or "")
        self.vendor.setCurrentText(current_vendor)
        self.product = QLineEdit(tool.product_id or "")
        self.series = QLineEdit(tool.series or "")
        self.diameter = _OptionalLength(200.0)
        self.corner = _OptionalLength(100.0)
        self.angle = _OptionalAngle()
        self.tip = _OptionalLength(50.0)
        self.flutes = _OptionalCount(12)
        self.flute_len = _OptionalLength(300.0)
        self.shank = _OptionalLength(50.0)
        self.overall = _OptionalLength(500.0)
        for w in (self.diameter, self.corner, self.tip, self.flute_len, self.shank, self.overall):
            w.set_inch(inch)
        self.notes = QTextEdit(tool.notes or "")
        self.notes.setMaximumHeight(70)
        self.diameter.put(tool.diameter_mm)
        self.corner.put(tool.corner_radius_mm)
        self.angle.put(tool.included_angle_deg)
        self.tip.put(tool.tip_dia_mm)
        self.flutes.put(tool.flute_count)
        self.flute_len.put(tool.flute_length_mm)
        self.shank.put(tool.shank_dia_mm)
        self.overall.put(tool.overall_length_mm)
        rows = ((tr("Name"), self.name), (tr("Type"), self.type), (tr("Vendor"), self.vendor),
                (tr("Product code"), self.product), (tr("Series"), self.series),
                (tr("Diameter"), self.diameter), (tr("Corner radius"), self.corner),
                (tr("Included angle"), self.angle), (tr("Tip diameter"), self.tip),
                (tr("Flutes"), self.flutes), (tr("Cutting length"), self.flute_len),
                (tr("Shank diameter"), self.shank), (tr("Overall length"), self.overall),
                (tr("Notes"), self.notes))
        for label, w in rows:
            f.addRow(label, w)
        self._form = f
        self.type.currentIndexChanged.connect(self._shape_rows)
        self._shape_rows()
        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.accepted.connect(self._on_save)
        box.rejected.connect(self.reject)
        f.addRow(box)

    def _shape_rows(self) -> None:
        t = self.type.currentData()
        self._form.setRowVisible(self.corner, t in ("bull_nose", "tapered_ball", "ball_nose"))
        angled = t in ("v_bit", "engraver", "chamfer", "tapered_ball", "drill")
        self._form.setRowVisible(self.angle, angled)
        self._form.setRowVisible(self.tip, t in ("v_bit", "engraver", "chamfer"))

    def _collect(self) -> LibraryTool:
        t = self.tool
        t.name = self.name.text().strip()
        t.type = self.type.currentData()
        # By name: the store links it to a known vendor (so a hand-typed «CMT
        # Orange Tools» gets CMT's feed charts) or adds a new one.
        vendor_text = self.vendor.currentText().strip()
        t.vendor_id, t.vendor_name = None, (vendor_text or None)
        t.product_id = self.product.text().strip() or None
        t.series = self.series.text().strip() or None
        t.diameter_mm = self.diameter.get()
        t.corner_radius_mm = self.corner.get() if self._form.isRowVisible(self.corner) else None
        t.included_angle_deg = self.angle.get() if self._form.isRowVisible(self.angle) else None
        t.tip_dia_mm = self.tip.get() if self._form.isRowVisible(self.tip) else None
        t.flute_count = self.flutes.get()
        t.flute_length_mm = self.flute_len.get()
        t.shank_dia_mm = self.shank.get()
        t.overall_length_mm = self.overall.get()
        t.notes = self.notes.toPlainText().strip() or None
        return t

    def _on_save(self) -> None:
        t = self._collect()
        draft = ToolDraft(0, name=t.name, tool_type=t.type, diameter_mm=t.diameter_mm,
                          corner_radius_mm=t.corner_radius_mm,
                          included_angle_deg=t.included_angle_deg, tip_dia_mm=t.tip_dia_mm,
                          flute_count=t.flute_count, flute_length_mm=t.flute_length_mm,
                          shank_dia_mm=t.shank_dia_mm, overall_length_mm=t.overall_length_mm)
        issues = ToolValidator("mm").validate(draft)
        # Unknown geometry is allowed here (the tool is just «incomplete»),
        # so only real contradictions stop the save.
        errors = [i for i in issues if i.is_error and i.code != "MISSING_DIAMETER"]
        if errors:
            QMessageBox.warning(self, tr("Library tool"),
                                "\n".join(messages.library_issue(i) for i in errors))
            return
        warnings = [i for i in issues if i.severity == "warning"]
        if warnings and QMessageBox.question(
                self, tr("Library tool"),
                "\n".join(messages.library_issue(i) for i in warnings) + "\n\n" +
                tr("Save anyway?")) != QMessageBox.Yes:
            return
        # A ball nose's radius is half its diameter, whatever was typed.
        t.corner_radius_mm = draft.corner_radius_mm if t.type == "ball_nose" else t.corner_radius_mm
        if t.type == "v_bit" and t.tip_dia_mm is None:
            t.tip_dia_mm = 0.0
        self.accept()

    def edited_fields(self) -> set:
        return {col for attr, col in _PROVENANCE.items()
                if getattr(self.tool, attr) != self._before[attr]}


# ---- the Trash --------------------------------------------------------------------------

class TrashDialog(QDialog):
    def __init__(self, repo, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Tool library Trash"))
        self.repo = repo
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        lay.addWidget(self.list)
        restore = QPushButton(tr("Restore"))
        purge = QPushButton(tr("Delete for good"))
        restore.clicked.connect(self._on_restore)
        purge.clicked.connect(self._on_purge)
        lay.addLayout(_buttons_row(restore, purge))
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self.resize(420, 360)
        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        for t in self.repo.trashed_tools():
            it = QListWidgetItem(f"{t.name} — {messages.tool_type_label(t.type)}")
            it.setData(Qt.UserRole, t.id)
            self.list.addItem(it)

    def _current(self):
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _on_restore(self) -> None:
        tid = self._current()
        if tid:
            self.repo.restore(tid)
            self.refresh()

    def _on_purge(self) -> None:
        tid = self._current()
        if tid and QMessageBox.question(self, tr("Tool library"), tr(
                "Delete this tool and its cutting data for good? This cannot be undone.")
        ) == QMessageBox.Yes:
            self.repo.purge(tid)
            self.refresh()


# ---- which file ----------------------------------------------------------------------------

class LibraryFileDialog(QDialog):
    """Use the default file, 2DCam's (on a Mac that has it), or any other."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Library file"))
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(tr("The tool library in use:")))
        self.current = QLabel(str(library_path()))
        self.current.setWordWrap(True)
        self.current.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.current)
        default = QPushButton(tr("Use the default file"))
        default.clicked.connect(lambda: self._use(""))
        lay.addWidget(default)
        theirs = twodcam_library_path()
        if theirs is not None:
            share = QPushButton(tr("Share 2DCam's library"))
            share.setToolTip(str(theirs))
            share.clicked.connect(lambda: self._use(str(theirs)))
            lay.addWidget(share)
        other = QPushButton(tr("Another file…"))
        other.clicked.connect(self._on_other)
        lay.addWidget(other)
        box = QDialogButtonBox(QDialogButtonBox.Cancel)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def _use(self, path: str) -> None:
        QtCore.QSettings().setValue(SETTINGS_KEY, path)
        self.accept()

    def _on_other(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, tr("Tool library file"), str(library_path().parent),
            tr("Tool library (*.sqlite *.db)"), options=QFileDialog.DontConfirmOverwrite)
        if path:
            self._use(path)


# ---- catalogue import -------------------------------------------------------------------

class ImportDialog(QDialog):
    """Pick a vendor profile and a CSV file, review every row, import the
    ticked ones. Nothing is written before «Import»."""

    @staticmethod
    def status_label(disposition: str) -> str:
        return {"new": tr("New"), "changed": tr("Changed"),
                "unchanged": tr("Already in the library"), "rejected": tr("Rejected"),
                "skipped": tr("Skipped")}.get(disposition, disposition)

    def __init__(self, repo, job, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Import a tool catalogue"))
        self.repo = repo
        self.machine = machine_limits(job)
        self.plan = None
        self.result_ = None
        self.resize(980, 600)
        lay = QVBoxLayout(self)
        row = FlowLayout(spacing=6)
        row.addWidget(QLabel(tr("Catalogue")))
        self.profile = _wide_combo()
        for p in builtin_profiles():
            self.profile.addItem(p.menu_title, p)
        row.addWidget(self.profile)
        more = QPushButton(tr("Profile file…"))
        more.setToolTip(tr("A catalogue profile (JSON) for another vendor's sheet."))
        more.clicked.connect(self._on_profile_file)
        row.addWidget(more)
        choose = QPushButton(tr("Choose CSV file…"))
        choose.clicked.connect(self._on_choose)
        row.addWidget(choose)
        lay.addLayout(row)
        self.note = QLabel()
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["", tr("Row"), tr("Code"), tr("Name"), tr("Type"), tr("Diameter"), tr("Status")])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.itemChanged.connect(self._on_ticked)
        self.table.itemSelectionChanged.connect(self._on_row)
        lay.addWidget(self.table, 3)
        self.problems = QLabel()
        self.problems.setWordWrap(True)
        self.problems.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.problems.setMinimumHeight(60)
        lay.addWidget(self.problems, 1)
        self.summary = QLabel()
        lay.addWidget(self.summary)
        box = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.btn_import = box.addButton(tr("Import"), QDialogButtonBox.AcceptRole)
        self.btn_import.setEnabled(False)
        self.btn_import.clicked.connect(self._on_import)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self._show_profile_note()
        self.profile.currentIndexChanged.connect(self._show_profile_note)

    def _show_profile_note(self, *_args) -> None:
        p = self.profile.currentData()
        self.note.setText(tr("Choose the vendor's CSV file. Nothing is added until you press "
                             "Import.") if p is None else (p.note or ""))

    def _on_profile_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Catalogue profile"), "",
                                              tr("Catalogue profile (*.json)"))
        if not path:
            return
        try:
            p = CatalogProfile.load(path)
        except (ProfileError, OSError, ValueError) as exc:
            QMessageBox.warning(self, tr("Import a tool catalogue"),
                                messages.library_error("bad_profile", detail=str(exc)))
            return
        self.profile.addItem(p.menu_title, p)
        self.profile.setCurrentIndex(self.profile.count() - 1)

    def _on_choose(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Tool catalogue"), "",
                                              tr("CSV files (*.csv *.txt)"))
        if path:
            self.load(Path(path))

    def load(self, path: Path) -> None:
        """Plan the import of ``path`` with the chosen profile and show it."""
        profile = self.profile.currentData()
        try:
            self.plan = importer.plan(path.read_bytes(), path.name, profile, self.repo,
                                      machine=self.machine)
        except CSVError as exc:
            QMessageBox.warning(self, tr("Import a tool catalogue"),
                                messages.library_error(exc.code))
            return
        except OSError as exc:
            QMessageBox.warning(self, tr("Import a tool catalogue"), str(exc))
            return
        self._fill()

    def _fill(self) -> None:
        plan = self.plan
        self.table.blockSignals(True)
        self.table.setRowCount(len(plan.rows))
        for i, row in enumerate(plan.rows):
            d = row.draft
            tick = QTableWidgetItem()
            if row.disposition in ("rejected", "unchanged"):
                tick.setFlags(Qt.ItemIsEnabled)
            else:
                tick.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                tick.setCheckState(Qt.Checked if row.include else Qt.Unchecked)
            self.table.setItem(i, 0, tick)
            type_text = messages.tool_type_label(d.tool_type) if d.tool_type else "—"
            if "tool_type" in d.estimated_fields:
                type_text += " ?"
            status = self.status_label(row.disposition)
            if row.has_warnings and row.disposition != "rejected":
                status += " ⚠"
            cells = [str(d.row_index + 1), d.product_id or "", d.name, type_text,
                     _length(d.diameter_mm, False), status]
            for c, text in enumerate(cells, start=1):
                self.table.setItem(i, c, _item(text))
        self.table.blockSignals(False)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        unmatched = ", ".join(plan.unmatched_columns.values())
        self.note.setText(tr("Columns not used: {columns}", columns=unmatched) if unmatched
                          else "")
        self._update_summary()
        if plan.rows:
            self.table.selectRow(0)

    def _on_ticked(self, item) -> None:
        if item.column() == 0 and self.plan is not None:
            self.plan.rows[item.row()].include = item.checkState() == Qt.Checked
            self._update_summary()

    def _on_row(self) -> None:
        r = self.table.currentRow()
        if self.plan is None or not (0 <= r < len(self.plan.rows)):
            self.problems.setText("")
            return
        row = self.plan.rows[r]
        lines = [("✕ " if i.is_error else "⚠ " if i.severity == "warning" else "• ")
                 + messages.library_issue(i) for i in row.issues]
        for col, (old, new) in row.diff.items():
            lines.append(tr("{field}: {old} → {new}", field=col, old=old or "—", new=new or "—"))
        self.problems.setText("\n".join(lines) or tr("No problems."))

    def _update_summary(self) -> None:
        p = self.plan
        self.summary.setText(tr(
            "{new} new, {changed} changed, {rejected} rejected, {warnings} with warnings.",
            new=p.new_count, changed=p.changed_count, rejected=p.rejected_count,
            warnings=p.warning_count))
        self.btn_import.setEnabled(p.new_count + p.changed_count > 0)

    def _on_import(self) -> None:
        if self.plan is None:
            return
        try:
            self.result_ = importer.commit(self.plan, self.repo)
        except LibraryError as exc:
            QMessageBox.warning(self, tr("Import a tool catalogue"),
                                messages.library_error("sql", detail=exc.detail))
            return
        self.accept()
