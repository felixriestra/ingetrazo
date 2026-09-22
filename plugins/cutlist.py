# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Cut List plugin — guillotine cutting-diagram generator (Extensions menu).

Ports 2DCutList's planner (``core/cutlist.py``, a separate native macOS app
by the same author) into IngeTrazo directly: parts come from the selected
Groups' bounding boxes, not a CSV round-trip, since the model geometry is
already here. See ``core/cutlist.py``'s module docstring for what v1
deliberately leaves out (manual per-cut recalculation, CSV import).

Read-only plugin: it only reads the Scene to build the parts list and never
mutates it, so unlike ``python_console.py`` there is nothing to wrap in
``viewport.history.execute(...)``.

Rendering has two independent paths, on purpose: the on-screen preview and
PDF/PNG export share one QPainter routine (:func:`paint_sheet`, one sheet at
a time — the simple case); SVG export instead calls the already complete,
tested :mod:`core.cutlist_svg` string generator (which additionally lays out
the multi-sheet header and the parts-list table) rather than reimplementing
that layout a second time in QPainter just to redirect it through
``QSvgGenerator``.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPdfWriter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.cutlist import (
    CutAxis,
    CutListError,
    CutListProject,
    GrainAxis,
    GrainRule,
    GuillotinePlanner,
    ImportedPart,
    PartSettings,
    StockDefinition,
    WorkshopSettings,
)
from core.i18n import tr
from tools.base import Tool

_CUT_COLORS = ["#d32f2f", "#1565c0", "#ef6c00", "#2e7d32",
               "#7b1fa2", "#00838f", "#c2185b", "#455a64"]
_PART_PALETTE = ["#f3c98b", "#9ec5e5", "#b7d7a8", "#d5b5e5",
                 "#f4a7a7", "#a7d8d2", "#e6d59a"]


# ---------------------------------------------------------------------------
# Stock/workshop persistence (QSettings, same JSON-blob convention as the
# composer's "composer/default_cota_style" — see views/composer.py). Parts
# and their material/grain settings stay session-only (they come from
# whatever is selected right now); the stock list and workshop numbers are
# workshop facts that outlive any one selection, so they are worth
# remembering across dialog opens and documents.
# ---------------------------------------------------------------------------

_STOCKS_KEY = "cutlist/default_stocks"
_WORKSHOP_KEY = "cutlist/workshop_settings"


def _stock_to_dict(s: StockDefinition) -> dict:
    return {"name": s.name, "material": s.material, "thickness": s.thickness,
            "length": s.length, "width": s.width,
            "available_sheets": s.available_sheets,
            "grain_axis": s.grain_axis.value}


def _stock_from_dict(d: dict) -> StockDefinition:
    return StockDefinition(
        name=str(d.get("name", "Stock")), material=str(d.get("material", "Unassigned")),
        thickness=float(d.get("thickness", 18.0)), length=float(d.get("length", 2440.0)),
        width=float(d.get("width", 1220.0)),
        available_sheets=d["available_sheets"] if d.get("available_sheets") is not None else None,
        grain_axis=GrainAxis(d.get("grain_axis", "none")))


def _workshop_to_dict(w: WorkshopSettings) -> dict:
    return {"kerf": w.kerf, "short_edge_cleanup": w.short_edge_cleanup,
            "long_edge_trim": w.long_edge_trim,
            "minimum_remnant_width": w.minimum_remnant_width,
            "minimum_remnant_length": w.minimum_remnant_length}


def _workshop_from_dict(d: dict) -> WorkshopSettings:
    fields = {"kerf", "short_edge_cleanup", "long_edge_trim",
              "minimum_remnant_width", "minimum_remnant_length"}
    return WorkshopSettings(**{k: float(v) for k, v in d.items() if k in fields})


def load_default_stocks() -> list[StockDefinition]:
    """The stock list remembered from the last time any Cut List dialog ran
    ``_sync_stock_edits`` — a fresh dialog starts with them already in the
    Stock & Setup table instead of empty."""
    import json
    from PySide6.QtCore import QSettings
    raw = QSettings().value(_STOCKS_KEY, "")
    try:
        data = json.loads(str(raw or "")) if raw else []
    except Exception:  # noqa: BLE001
        data = []
    stocks = []
    for d in data if isinstance(data, list) else []:
        try:
            stocks.append(_stock_from_dict(d))
        except Exception:  # noqa: BLE001 — one bad entry does not lose the rest
            pass
    return stocks


def load_default_workshop() -> WorkshopSettings:
    import json
    from PySide6.QtCore import QSettings
    raw = QSettings().value(_WORKSHOP_KEY, "")
    try:
        data = json.loads(str(raw or "")) if raw else {}
    except Exception:  # noqa: BLE001
        data = {}
    try:
        return _workshop_from_dict(data) if isinstance(data, dict) and data else WorkshopSettings()
    except Exception:  # noqa: BLE001 — a corrupt blob falls back to defaults
        return WorkshopSettings()


def remember_stocks(stocks: list[StockDefinition]) -> None:
    import json
    from PySide6.QtCore import QSettings
    try:
        QSettings().setValue(_STOCKS_KEY, json.dumps([_stock_to_dict(s) for s in stocks]))
    except Exception:  # noqa: BLE001 — a stock that will not serialise
        pass


def remember_workshop(workshop: WorkshopSettings) -> None:
    import json
    from PySide6.QtCore import QSettings
    try:
        QSettings().setValue(_WORKSHOP_KEY, json.dumps(_workshop_to_dict(workshop)))
    except Exception:  # noqa: BLE001
        pass


def _part_color(part_id: str) -> QColor:
    h = 0
    for b in part_id.encode("utf-8"):
        h = (h * 31 + b) & 0x7fffffff
    return QColor(_PART_PALETTE[h % len(_PART_PALETTE)])


# ---------------------------------------------------------------------------
# Reading parts from the selection (pure data — no GUI, no Qt)
# ---------------------------------------------------------------------------

def parts_from_selection(scene) -> tuple[list[ImportedPart], dict[str, PartSettings]]:
    """One :class:`ImportedPart` per distinct (footprint, material) among the
    selected Groups — identical selections collapse into one entry with
    ``quantity > 1``, the same shape a CSV row would have. Returns a
    suggested :class:`PartSettings` per part id (material prefilled from the
    group's paint; grain/rotation left at the defaults for the user to set).

    Dimensions come from an axis-aligned bounding box
    (``core.group.oriented_bounds`` without a custom frame) — a group
    rotated off the world axes gets an inflated, non-tight box. A true OBB
    is a natural follow-up, not required for the common case of
    axis-aligned panels.
    """
    from core.group import Group, effective_material, oriented_bounds, placement_points

    groups = [e for e in scene.selection if isinstance(e, Group)]
    parts: dict[tuple, ImportedPart] = {}
    settings: dict[str, PartSettings] = {}
    names_used: dict[str, tuple] = {}
    order: list[tuple] = []

    for g in groups:
        pts = placement_points(g)
        if len(pts) == 0:
            continue
        _frame, lo, hi = oriented_bounds(g.mesh, points=pts)
        dims_mm = sorted(((hi[i] - lo[i]) * 1000.0 for i in range(3)), reverse=True)
        length_mm, width_mm, thickness_mm = dims_mm
        material = effective_material(g)
        material_name = (material or {}).get("mat") or "Unassigned"
        key = (round(length_mm, 1), round(width_mm, 1), round(thickness_mm, 1), material_name)

        if key not in parts:
            name = g.name
            n = 2
            while names_used.get(name, key) != key:
                name = f"{g.name} ({n})"
                n += 1
            names_used[name] = key
            parts[key] = ImportedPart(id=name, description=g.name, length=length_mm,
                                       width=width_mm, thickness=thickness_mm, quantity=0)
            settings[name] = PartSettings(material=material_name)
            order.append(key)
        parts[key].quantity += 1

    return [parts[k] for k in order], settings


# ---------------------------------------------------------------------------
# QPainter rendering (on-screen preview + PDF/PNG; see module docstring)
# ---------------------------------------------------------------------------

def paint_sheet(painter: QPainter, plan, sheet_index: int, box_w: float, box_h: float) -> None:
    """Paint one sheet's diagram into ``painter``, fit to a ``box_w``×``box_h``
    area at the painter's current origin — the shared routine behind the
    preview widget, PDF export, and PNG export."""
    sheet = plan.sheets[sheet_index]
    scale = min(box_w / sheet.usable_rect.width, box_h / sheet.usable_rect.height)
    actual_w = sheet.usable_rect.width * scale
    actual_h = sheet.usable_rect.height * scale

    def px(v: float) -> float:
        return v * scale

    def py(v: float) -> float:
        return actual_h - v * scale

    painter.setPen(QPen(QColor("#263238"), 2))
    painter.setBrush(QColor("#f7f4ec"))
    painter.drawRect(0, 0, round(actual_w), round(actual_h))

    for r in sheet.remnants:
        if r.area <= 1:
            continue
        reusable = (max(r.width, r.height) >= 100 and min(r.width, r.height) >= 100)
        painter.setPen(QPen(QColor("#548b60" if reusable else "#bdbdbd"), 1, Qt.DashLine))
        painter.setBrush(QColor("#dff3e4" if reusable else "#f5f5f5"))
        painter.drawRect(round(px(r.x)), round(py(r.max_y)), round(r.width * scale), round(r.height * scale))

    font = QFont()
    font.setBold(True)
    font.setPointSizeF(max(6.0, min(11.0, scale * 40)))
    painter.setFont(font)
    for p in sheet.placements:
        color = _part_color(p.instance.source_part_id)
        painter.setPen(QPen(QColor("#34495e"), 1.5))
        painter.setBrush(color)
        rx, ry = round(px(p.rect.x)), round(py(p.rect.max_y))
        rw, rh = round(p.rect.width * scale), round(p.rect.height * scale)
        painter.drawRect(rx, ry, rw, rh)
        painter.setPen(QPen(QColor("#17202a")))
        label = p.id + (" ↻" if p.rotated else "")
        painter.drawText(rx, ry, rw, rh, Qt.AlignCenter, label)

    for cut in sheet.cuts:
        s = cut.split
        color = QColor(_CUT_COLORS[(cut.number - 1) % len(_CUT_COLORS)])
        pen = QPen(color, 2, Qt.DashLine)
        painter.setPen(pen)
        label = f"C{cut.number:02d}"
        if s.axis == CutAxis.VERTICAL:
            lx = px(s.coordinate)
            y1, y2 = py(s.workpiece.y), py(s.workpiece.max_y)
            painter.drawLine(round(lx), round(y1), round(lx), round(y2))
            painter.setPen(QColor("#17202a"))
            painter.drawText(round(lx) + 4, round(min(y1, y2)) + 14, label)
            painter.drawText(round(lx) + 4, round(max(y1, y2)) - 4, label)
        else:
            ly = py(s.coordinate)
            x1, x2 = px(s.workpiece.x), px(s.workpiece.max_x)
            painter.drawLine(round(x1), round(ly), round(x2), round(ly))
            painter.setPen(QColor("#17202a"))
            painter.drawText(round(min(x1, x2)) + 4, round(ly) - 4, label)
            painter.drawText(round(max(x1, x2)) - 34, round(ly) - 4, label)


class _PreviewWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.plan = None
        self.sheet_index = 0
        self.setMinimumSize(400, 300)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("white"))
        if self.plan is not None and self.plan.sheets:
            margin = 12
            painter.save()
            painter.translate(margin, margin)
            paint_sheet(painter, self.plan, self.sheet_index,
                        self.width() - 2 * margin, self.height() - 2 * margin)
            painter.restore()
        painter.end()


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

_GRAIN_AXES = [(tr("None"), GrainAxis.NONE), (tr("Along length"), GrainAxis.LENGTH),
               (tr("Along width"), GrainAxis.WIDTH)]
_GRAIN_RULES = [(tr("Irrelevant"), GrainRule.IRRELEVANT), (tr("Preferred"), GrainRule.PREFERRED),
                (tr("Required"), GrainRule.REQUIRED)]


class CutListDialog(QDialog):
    def __init__(self, viewport, parent=None) -> None:
        super().__init__(parent or viewport.window())
        self._viewport = viewport
        self._parts: list[ImportedPart] = []
        self._settings: dict[str, PartSettings] = {}
        # Remembered across dialog opens and documents (QSettings) — a
        # fresh dialog does not start from an empty Stock & Setup tab.
        self._stocks: list[StockDefinition] = load_default_stocks()
        self._workshop = load_default_workshop()
        self._plan = None
        self.setWindowTitle(tr("Cut List"))
        self.resize(760, 620)
        self._build_ui()
        self._populate_stock_table()
        self._load_from_selection()

    # -- layout ----------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self._tabs = QTabWidget()
        layout.addWidget(self._tabs, stretch=1)

        self._tabs.addTab(self._build_parts_tab(), tr("Parts & Grain"))
        self._tabs.addTab(self._build_stock_tab(), tr("Stock & Setup"))
        self._tabs.addTab(self._build_plan_tab(), tr("Plan"))

        btn_row = QHBoxLayout()
        close_btn = QPushButton(tr("Close"))
        close_btn.clicked.connect(self.close)
        btn_row.addStretch(1)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _build_parts_tab(self) -> QWidget:
        tab = QWidget()
        v = QVBoxLayout(tab)
        reload_btn = QPushButton(tr("Reload from selection"))
        reload_btn.clicked.connect(self._load_from_selection)
        v.addWidget(reload_btn)
        self._parts_table = QTableWidget()
        self._parts_table.setColumnCount(8)
        self._parts_table.setHorizontalHeaderLabels([
            tr("Part ID"), tr("L × W × T (mm)"), tr("Qty"), tr("Material"),
            tr("Grain axis"), tr("Grain rule"), tr("Allow rotation"), tr(""),
        ])
        self._parts_table.horizontalHeader().setStretchLastSection(True)
        self._parts_table.verticalHeader().setVisible(False)
        v.addWidget(self._parts_table, stretch=1)
        return tab

    def _build_stock_tab(self) -> QWidget:
        tab = QWidget()
        v = QVBoxLayout(tab)

        row = QHBoxLayout()
        add_btn = QPushButton(tr("Add stock"))
        add_btn.clicked.connect(self._add_stock_row)
        remove_btn = QPushButton(tr("Remove selected"))
        remove_btn.clicked.connect(self._remove_stock_row)
        row.addWidget(add_btn)
        row.addWidget(remove_btn)
        row.addStretch(1)
        v.addLayout(row)

        self._stock_table = QTableWidget()
        self._stock_table.setColumnCount(7)
        self._stock_table.setHorizontalHeaderLabels([
            tr("Name"), tr("Material"), tr("Thickness (mm)"), tr("Length (mm)"),
            tr("Width (mm)"), tr("Sheets on hand (blank = unlimited)"), tr("Grain axis"),
        ])
        self._stock_table.horizontalHeader().setStretchLastSection(True)
        self._stock_table.verticalHeader().setVisible(False)
        v.addWidget(self._stock_table, stretch=1)

        v.addWidget(QLabel(tr("Workshop settings")))
        form = QHBoxLayout()
        self._kerf_spin = self._mm_spin(self._workshop.kerf)
        self._short_edge_spin = self._mm_spin(self._workshop.short_edge_cleanup)
        self._long_edge_spin = self._mm_spin(self._workshop.long_edge_trim)
        self._min_remnant_w_spin = self._mm_spin(self._workshop.minimum_remnant_width)
        self._min_remnant_l_spin = self._mm_spin(self._workshop.minimum_remnant_length)
        for label, widget in (
            (tr("Kerf"), self._kerf_spin),
            (tr("Short-edge cleanup"), self._short_edge_spin),
            (tr("Long-edge trim"), self._long_edge_spin),
            (tr("Min. remnant width"), self._min_remnant_w_spin),
            (tr("Min. remnant length"), self._min_remnant_l_spin),
        ):
            form.addWidget(QLabel(label))
            form.addWidget(widget)
        v.addLayout(form)
        return tab

    def _mm_spin(self, value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 10000.0)
        spin.setDecimals(1)
        spin.setValue(value)
        spin.setSuffix(" mm")
        return spin

    def _build_plan_tab(self) -> QWidget:
        tab = QWidget()
        v = QVBoxLayout(tab)

        row = QHBoxLayout()
        generate_btn = QPushButton(tr("Generate plan"))
        generate_btn.clicked.connect(self._generate_plan)
        row.addWidget(generate_btn)
        self._prev_btn = QPushButton(tr("◀"))
        self._prev_btn.clicked.connect(lambda: self._step_sheet(-1))
        self._next_btn = QPushButton(tr("▶"))
        self._next_btn.clicked.connect(lambda: self._step_sheet(1))
        self._sheet_label = QLabel(tr("No plan yet"))
        row.addWidget(self._prev_btn)
        row.addWidget(self._sheet_label)
        row.addWidget(self._next_btn)
        row.addStretch(1)
        v.addLayout(row)

        self._preview = _PreviewWidget()
        v.addWidget(self._preview, stretch=1)

        export_row = QHBoxLayout()
        for label, handler in ((tr("Export SVG…"), self._export_svg),
                               (tr("Export PDF…"), self._export_pdf),
                               (tr("Export PNG…"), self._export_png)):
            btn = QPushButton(label)
            btn.clicked.connect(handler)
            export_row.addWidget(btn)
        export_row.addStretch(1)
        v.addLayout(export_row)
        return tab

    # -- parts & settings sync ---------------------------------------------

    def _load_from_selection(self) -> None:
        self._parts, suggested = parts_from_selection(self._viewport.scene)
        for part_id, settings in suggested.items():
            self._settings.setdefault(part_id, settings)
        self._populate_parts_table()

    def _populate_parts_table(self) -> None:
        table = self._parts_table
        table.setRowCount(len(self._parts))
        for row, part in enumerate(self._parts):
            settings = self._settings.setdefault(part.id, PartSettings())
            table.setItem(row, 0, QTableWidgetItem(part.id))
            table.setItem(row, 1, QTableWidgetItem(
                f"{part.length:.1f} × {part.width:.1f} × {part.thickness:.1f}"))
            table.setItem(row, 2, QTableWidgetItem(str(part.quantity)))

            material_item = QTableWidgetItem(settings.material)
            table.setItem(row, 3, material_item)

            axis_combo = QComboBox()
            for label, axis in _GRAIN_AXES:
                axis_combo.addItem(label, axis)
            axis_combo.setCurrentIndex([a for _l, a in _GRAIN_AXES].index(settings.grain_axis))
            axis_combo.currentIndexChanged.connect(
                lambda _i, pid=part.id, combo=axis_combo:
                    setattr(self._settings[pid], "grain_axis", combo.currentData()))
            table.setCellWidget(row, 4, axis_combo)

            rule_combo = QComboBox()
            for label, rule in _GRAIN_RULES:
                rule_combo.addItem(label, rule)
            rule_combo.setCurrentIndex([r for _l, r in _GRAIN_RULES].index(settings.grain_rule))
            rule_combo.currentIndexChanged.connect(
                lambda _i, pid=part.id, combo=rule_combo:
                    setattr(self._settings[pid], "grain_rule", combo.currentData()))
            table.setCellWidget(row, 5, rule_combo)

            rotate_check = QCheckBox()
            rotate_check.setChecked(settings.allow_rotation)
            rotate_check.toggled.connect(
                lambda checked, pid=part.id: setattr(self._settings[pid], "allow_rotation", checked))
            table.setCellWidget(row, 6, rotate_check)
        table.resizeRowsToContents()

    def _sync_material_edits(self) -> None:
        for row, part in enumerate(self._parts):
            item = self._parts_table.item(row, 3)
            if item is not None:
                self._settings[part.id].material = item.text().strip() or "Unassigned"

    # -- stock table ---------------------------------------------------------

    def _add_stock_row(self) -> None:
        self._stocks.append(StockDefinition(name=tr("New stock"), material="Unassigned",
                                             thickness=18.0, length=2500.0, width=1250.0))
        self._populate_stock_table()

    def _remove_stock_row(self) -> None:
        rows = sorted({idx.row() for idx in self._stock_table.selectedIndexes()}, reverse=True)
        for row in rows:
            del self._stocks[row]
        self._populate_stock_table()

    def _populate_stock_table(self) -> None:
        table = self._stock_table
        table.setRowCount(len(self._stocks))
        for row, stock in enumerate(self._stocks):
            table.setItem(row, 0, QTableWidgetItem(stock.name))
            table.setItem(row, 1, QTableWidgetItem(stock.material))
            table.setItem(row, 2, QTableWidgetItem(f"{stock.thickness:.1f}"))
            table.setItem(row, 3, QTableWidgetItem(f"{stock.length:.1f}"))
            table.setItem(row, 4, QTableWidgetItem(f"{stock.width:.1f}"))
            table.setItem(row, 5, QTableWidgetItem(
                "" if stock.available_sheets is None else str(stock.available_sheets)))
            axis_combo = QComboBox()
            for label, axis in _GRAIN_AXES:
                axis_combo.addItem(label, axis)
            axis_combo.setCurrentIndex([a for _l, a in _GRAIN_AXES].index(stock.grain_axis))
            table.setCellWidget(row, 6, axis_combo)
        table.resizeRowsToContents()

    def _sync_stock_edits(self) -> None:
        for row, stock in enumerate(self._stocks):
            def cell_text(col: int) -> str:
                item = self._stock_table.item(row, col)
                return item.text().strip() if item is not None else ""

            stock.name = cell_text(0) or stock.name
            stock.material = cell_text(1) or "Unassigned"
            try:
                stock.thickness = float(cell_text(2))
                stock.length = float(cell_text(3))
                stock.width = float(cell_text(4))
            except ValueError:
                pass
            sheets_text = cell_text(5)
            stock.available_sheets = int(sheets_text) if sheets_text else None
            combo = self._stock_table.cellWidget(row, 6)
            if isinstance(combo, QComboBox):
                stock.grain_axis = combo.currentData()

        self._workshop = WorkshopSettings(
            kerf=self._kerf_spin.value(), short_edge_cleanup=self._short_edge_spin.value(),
            long_edge_trim=self._long_edge_spin.value(),
            minimum_remnant_width=self._min_remnant_w_spin.value(),
            minimum_remnant_length=self._min_remnant_l_spin.value())
        remember_stocks(self._stocks)
        remember_workshop(self._workshop)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        # Catches edits made but never run through Generate — Stock & Setup
        # is meant to survive closing the dialog, not just closing it after
        # a successful plan.
        self._sync_stock_edits()
        super().closeEvent(event)

    # -- plan generation / preview -----------------------------------------

    def _project(self) -> CutListProject:
        self._sync_material_edits()
        self._sync_stock_edits()
        return CutListProject(name=tr("Selection"), parts=list(self._parts),
                               part_settings=dict(self._settings), stocks=list(self._stocks),
                               workshop=self._workshop)

    def _generate_plan(self) -> None:
        if not self._parts:
            QMessageBox.information(self, tr("Cut List"),
                                     tr("Select one or more groups first, then "
                                        "Reload from selection on the Parts & Grain tab."))
            return
        try:
            self._plan = GuillotinePlanner().generate(self._project())
        except CutListError as exc:
            QMessageBox.critical(self, tr("Cut List"), str(exc))
            return
        self._preview.plan = self._plan
        self._preview.sheet_index = 0
        self._preview.update()
        self._update_sheet_label()
        self._tabs.setCurrentIndex(2)

    def _update_sheet_label(self) -> None:
        if self._plan is None or not self._plan.sheets:
            self._sheet_label.setText(tr("No plan yet"))
            return
        sheet = self._plan.sheets[self._preview.sheet_index]
        self._sheet_label.setText(self._plan.label(sheet))

    def _step_sheet(self, delta: int) -> None:
        if self._plan is None or not self._plan.sheets:
            return
        n = len(self._plan.sheets)
        self._preview.sheet_index = (self._preview.sheet_index + delta) % n
        self._preview.update()
        self._update_sheet_label()

    # -- export --------------------------------------------------------------

    def _export_svg(self) -> None:
        if self._plan is None:
            return
        path, _filter = QFileDialog.getSaveFileName(self, tr("Export SVG"), "", "SVG (*.svg)")
        if not path:
            return
        from core.cutlist_svg import render
        svg = render(self._project(), self._plan)
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg)

    def _export_pdf(self) -> None:
        if self._plan is None:
            return
        path, _filter = QFileDialog.getSaveFileName(self, tr("Export PDF"), "", "PDF (*.pdf)")
        if not path:
            return
        writer = QPdfWriter(path)
        writer.setResolution(300)
        painter = QPainter(writer)
        try:
            for i in range(len(self._plan.sheets)):
                if i > 0:
                    writer.newPage()
                painter.save()
                painter.scale(writer.width() / 1000.0, writer.height() / 1000.0)
                paint_sheet(painter, self._plan, i, 1000.0, 1000.0 * writer.height() / writer.width())
                painter.restore()
        finally:
            painter.end()

    def _export_png(self) -> None:
        if self._plan is None:
            return
        path, _filter = QFileDialog.getSaveFileName(self, tr("Export PNG"), "", "PNG (*.png)")
        if not path:
            return
        from PySide6.QtGui import QImage
        sheet = self._plan.sheets[self._preview.sheet_index]
        scale = 4.0  # px per mm, a print-quality raster
        w = round(sheet.usable_rect.width * scale)
        h = round(sheet.usable_rect.height * scale)
        img = QImage(w, h, QImage.Format_RGB32)
        img.fill(Qt.white)
        painter = QPainter(img)
        try:
            paint_sheet(painter, self._plan, self._preview.sheet_index, w, h)
        finally:
            painter.end()
        img.save(path)


# ---------------------------------------------------------------------------
# Tool registration (the plugin entry point)
# ---------------------------------------------------------------------------

class CutListTool(Tool):
    """Extensions-menu tool that opens the Cut List dialog."""
    name = "Cut List"
    shortcut = None
    uses_snap = False

    def on_activate(self, viewport) -> None:
        CutListDialog(viewport, parent=viewport.window()).exec()

    def on_deactivate(self, viewport) -> None:
        pass
