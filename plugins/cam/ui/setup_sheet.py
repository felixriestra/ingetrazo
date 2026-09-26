# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The setup sheet as a PDF — 2DCam's ``SetupSheetPageView`` on
``QPdfWriter``.

:mod:`..engine.sheet` gathers the content as numbers and codes; this module
words it in the user's language (lengths in the job's units) and draws A4
pages with a QPainter: vector text and lines, so it prints sharp. The page
is always drawn light — it is paper, whatever the app's theme.

Layout and pagination follow 2DCam's: the page is cut into blocks of known
height (header, the four information boxes, the program files, warnings,
the plan view, then the two tables row by row), and a block that does not
fit starts a new page; a table title never ends a page alone.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass

from PySide6.QtCore import QMarginsF, QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QPageLayout, QPageSize, QPainter,
                           QPainterPath, QPdfWriter, QPen)

from ..engine import sheet as sheet_mod
from ..engine.models import MM_PER_INCH
from ..engine.post.base import POINT_NAMES, ZERO_NAMES
from ..i18n import tr
from . import messages
from .op_forms import kind_label, tool_kind_label

# ---- page geometry, in points (1/72 in) — 2DCam's -----------------------------
PAGE_W, PAGE_H = 595.0, 842.0
MARGIN = 36.0
FOOTER = 22.0
CONTENT_W = PAGE_W - 2 * MARGIN
CONTENT_H = PAGE_H - 2 * MARGIN - FOOTER
FIELD_ROW = 15.0
BOX_TITLE = 22.0
TABLE_ROW = 20.0
SECTION_TITLE = 30.0
PLAN_H = 250.0
SPACING = 12.0
WARNING_ROW = 28.0

#: One colour per operation in the plan view (2DCam's palette).
PALETTE = ("#DB2626", "#1A6BD9", "#1A994D", "#E68C0D", "#8C40BF", "#0D99A6", "#B35919",
           "#595966")

INK = QColor("#000000")
GREY = QColor("#808080")
RULE = QColor("#B3B3B3")
STOCK_FILL = QColor(242, 235, 217)


def color_for(op_number: int) -> QColor:
    return QColor(PALETTE[max(op_number - 1, 0) % len(PALETTE)])


# ---- the sheet in words -----------------------------------------------------------

@dataclass
class Words:
    """Everything the page prints, already worded."""
    title: str
    job_name: str
    job: list
    stock: list
    origin: list
    machine: list
    files: list            # [(file name, tools)]
    files_note: str
    warnings: list
    tools_title: str
    tool_header: list
    tools: list            # rows of cells
    operation_header: list
    operations: list       # (cells, dimmed)
    legend: dict           # op number -> name
    footer: str


def _num(mm: float, inch: bool) -> str:
    v = mm / MM_PER_INCH if inch else mm
    s = f"{v:.{4 if inch else 3}f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _feed(mm_min: float, inch: bool) -> str:
    return f"{mm_min / MM_PER_INCH:.1f}" if inch else f"{mm_min:.0f}"


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _controller_name(c: str) -> str:
    return {"grbl": "GRBL", "linuxcnc": "LinuxCNC"}.get(c, c)


def words(s, base_name: str, prepared: _dt.datetime | None = None) -> Words:
    """Word a :class:`..engine.sheet.SetupSheet`. ``base_name`` is what the
    program files are named after (export's default: the job's name)."""
    from .dock import material_label       # the dock's own words for materials
    inch = s.units == "inches"
    unit = "in" if inch else "mm"
    feed_unit = "in/min" if inch else "mm/min"
    prepared = prepared or _dt.datetime.now()
    st, setup, m = s.stock, s.setup, s.machine

    job = [(tr("Job"), s.job_name or tr("Untitled")),
           (tr("Prepared"), prepared.strftime("%Y-%m-%d %H:%M")),
           (tr("Units"), tr("Inches") if inch else tr("Millimetres")),
           (tr("Operations"), tr("{enabled} enabled of {total}", enabled=s.enabled_count,
                                 total=len(s.operations)))]
    if s.total_seconds is not None:
        job.append((tr("Estimated time"), sheet_mod.duration(s.total_seconds)))
    if s.tool_changes is not None:
        job.append((tr("Tool changes"), tr("{count} in the program", count=s.tool_changes)))
    if s.cutting_length is not None:
        job.append((tr("Cutting length"), f"{_num(s.cutting_length, inch)} {unit}"))

    stock = [(tr("Size"), f"{_num(st.width, inch)} × {_num(st.depth, inch)} × "
                          f"{_num(st.height, inch)} {unit}"),
             (tr("Material"), material_label(st.material))]

    origin = [(tr("Work offset"), setup.workOffset),
              (tr("X / Y zero"), _cap(tr(POINT_NAMES.get(st.referencePoint, st.referencePoint)))),
              (tr("Z zero"), _cap(tr(ZERO_NAMES.get(st.zeroPosition, st.zeroPosition)))),
              (tr("Safe height"), f"{_num(setup.safeHeight, inch)} {unit}"),
              (tr("Clearance height"), f"{_num(setup.clearanceHeight, inch)} {unit}")]

    machine = [(tr("Controller"), _controller_name(s.controller)),
               (tr("Machine"), m.name),
               (tr("Spindle"), tr("{rpm} rpm max", rpm=m.maximumSpindleRPM)),
               (tr("Feed"), tr("{feed} {unit} max", feed=_feed(m.maximumFeed, inch),
                               unit=feed_unit)),
               (tr("Travel limits"), tr("Enforced") if m.enforcesTravelLimits
                else tr("Not enforced"))]

    files, note = [], ""
    if s.files:
        for f in s.files:
            files.append((f"{base_name}{f.suffix}{f.extension}",
                          ", ".join(f"T{n}" for n in f.tool_numbers)))
        if s.controller == "grbl" and len(s.files) > 1:
            note = tr("Run the files in this order. Before each one, load its tool and set "
                      "Z zero again.")
        elif s.controller == "linuxcnc":
            note = tr("Load the tool table {name} into LinuxCNC before running.",
                      name=f"{base_name}.tbl")

    warnings = [messages.describe(w, inch) for w in s.warnings]

    tool_header = [tr("T"), tr("Tool"), tr("Type"), tr("Ø"), tr("Cut length"), tr("RPM"),
                   tr("Feed / plunge"), tr("Used by")]
    tools = []
    for t in s.tools:
        kind = tool_kind_label(t.kind)
        if t.included_angle:
            kind += f" {t.included_angle:g}°"
        tools.append([f"T{t.number}", t.name, kind, _num(t.diameter, inch),
                      _num(t.flute_length, inch), str(t.rpm),
                      f"{_feed(t.feed, inch)} / {_feed(t.plunge, inch)}",
                      sheet_mod.compact_ranges(t.used_by) or "—"])

    status = {"ok": tr("OK"), "disabled": tr("Disabled"), "no_tool": tr("No tool"),
              "failed": tr("Failed")}
    operation_header = [tr("#"), tr("Operation"), tr("Type"), tr("Tool"), tr("Details"),
                        tr("Time"), tr("Status")]
    operations = []
    for o in s.operations:
        tool = f"T{o.tool_number} · {o.tool_name}" if o.tool_number is not None else "—"
        if o.finisher_number is not None:
            tool += f" + T{o.finisher_number}"
        details = []
        if o.depth is not None:
            details.append(tr("Depth {depth}", depth=f"{_num(o.depth, inch)} {unit}"))
        if o.step is not None:
            details.append(tr("Step {step}", step=f"{_num(o.step, inch)} {unit}"))
        if o.holes is not None:
            details.append(tr("{count} holes", count=o.holes))
        operations.append(([str(o.number), o.name, kind_label(o.kind), tool,
                            " · ".join(details), sheet_mod.duration(o.seconds),
                            status[o.status]], not o.enabled))

    title = tr("Setup sheet")
    return Words(title=title, job_name=s.job_name or tr("Untitled"), job=job, stock=stock,
                 origin=origin, machine=machine, files=files, files_note=note,
                 warnings=warnings,
                 tools_title=tr("Tools · feeds in {unit}", unit=feed_unit),
                 tool_header=tool_header, tools=tools, operation_header=operation_header,
                 operations=operations, legend={o.number: o.name for o in s.operations},
                 footer=f"{title} · {s.job_name}")


# ---- pagination ----------------------------------------------------------------------

def blocks(w: Words, s) -> list:
    """The page blocks in order: ``(kind, payload, height)``."""
    def box(n):
        return BOX_TITLE + n * FIELD_ROW + 8

    out = [("header", None, 54.0),
           ("info_top", None, max(box(len(w.job)), box(len(w.stock)))),
           ("info_bottom", None, max(box(len(w.origin)), box(len(w.machine))))]
    if w.files:
        out.append(("files", None, box(len(w.files)) + (FIELD_ROW if w.files_note else 0)))
    if w.warnings:
        out.append(("warnings", None, BOX_TITLE + len(w.warnings) * WARNING_ROW + 8))
    if s.plan_paths or s.plan_points:
        out.append(("plan", None, PLAN_H))
    out.append(("section", w.tools_title, SECTION_TITLE))
    out.append(("tool_header", None, TABLE_ROW))
    out += [("tool", row, TABLE_ROW) for row in w.tools]
    out.append(("section", tr("Operations"), SECTION_TITLE))
    out.append(("operation_header", None, TABLE_ROW))
    out += [("operation", row, TABLE_ROW) for row in w.operations]
    return out


def paginate(items: list) -> list:
    """Blocks into pages. A table title keeps its header and first row."""
    pages, used = [[]], 0.0
    for i, item in enumerate(items):
        height = item[2] + SPACING
        required = height
        if item[0] == "section":
            required += sum(items[j][2] + SPACING for j in (i + 1, i + 2) if j < len(items))
        if used + required > CONTENT_H and pages[-1]:
            pages.append([])
            used = 0.0
        pages[-1].append(item)
        used += height
    return pages


# ---- drawing ----------------------------------------------------------------------------

def _font(size: float, bold: bool = False) -> QFont:
    f = QFont("Helvetica")
    f.setStyleHint(QFont.SansSerif)
    f.setPointSizeF(size)
    f.setBold(bold)
    return f


class _Page:
    """Draws on one page in points, top-left of the content at (0, 0)."""

    def __init__(self, painter: QPainter):
        self.p = painter

    def text(self, x, y, w, h, s, size=9.0, bold=False, color=INK, align=Qt.AlignLeft):
        self.p.setFont(_font(size, bold))
        self.p.setPen(color)
        s = QFontMetricsF(self.p.font()).elidedText(s, Qt.ElideRight, w)
        self.p.drawText(QRectF(x, y, w, h), int(align | Qt.AlignVCenter), s)

    def wrapped(self, x, y, w, h, s, size=9.0, color=INK):
        self.p.setFont(_font(size))
        self.p.setPen(color)
        self.p.drawText(QRectF(x, y, w, h), int(Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap), s)

    def rule(self, x, y, w, width=0.5, color=RULE):
        self.p.setPen(QPen(color, width))
        self.p.drawLine(QPointF(x, y), QPointF(x + w, y))

    def box_title(self, x, y, w, title):
        self.text(x, y, w, BOX_TITLE - 6, title.upper(), 8.0, True, GREY)
        self.rule(x, y + BOX_TITLE - 4, w)

    def field_box(self, x, y, w, title, fields, label_w=112.0):
        self.box_title(x, y, w, title)
        yy = y + BOX_TITLE
        for label, value in fields:
            self.text(x, yy, label_w, FIELD_ROW, label, 9.0, color=GREY)
            self.text(x + label_w + 6, yy, w - label_w - 6, FIELD_ROW, value, 9.0)
            yy += FIELD_ROW

    def table_row(self, y, cells, widths, header=False, dimmed=False):
        x = 0.0
        for cell, w in zip(cells, widths):
            self.text(x, y, w, TABLE_ROW, cell, 8.0 if header else 8.5, header,
                      GREY if (header or dimmed) else INK)
            x += w + 4
        self.rule(0, y + TABLE_ROW, CONTENT_W, 0.8 if header else 0.4,
                  QColor("#737373") if header else QColor("#D9D9D9"))


#: Column widths, pt (sum + 4 pt gaps = the content width).
TOOL_WIDTHS = (22, 108, 76, 36, 72, 38, 84, 59)
OPERATION_WIDTHS = (18, 94, 60, 90, 150, 40, 47)


def draw_plan(page: _Page, y: float, s, w: Words) -> None:
    """Top view: the stock, every cut coloured by operation, the holes, the
    work zero with its axes, and a legend (2DCam's plan view)."""
    p = page.p
    page.box_title(0, y, CONTENT_W, tr("Plan view · toolpaths from above"))
    area = QRectF(0, y + BOX_TITLE, CONTENT_W, PLAN_H - BOX_TITLE - 16)
    st = s.stock
    sx0, sy0 = st.origin[0], st.origin[1]
    xs = [sx0, sx0 + st.width, 0.0] + [pt[0] for _n, pts in s.plan_paths for pt in pts] + \
        [pt[0] for _n, pt in s.plan_points]
    ys = [sy0, sy0 + st.depth, 0.0] + [pt[1] for _n, pts in s.plan_paths for pt in pts] + \
        [pt[1] for _n, pt in s.plan_points]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    span_x, span_y = max(maxx - minx, 1e-6), max(maxy - miny, 1e-6)
    inset = 14.0
    scale = min((area.width() - 2 * inset) / span_x, (area.height() - 2 * inset) / span_y)
    ox = area.left() + (area.width() - span_x * scale) / 2
    oy = area.top() + (area.height() - span_y * scale) / 2

    def pt(x, yv):
        # Paper's y grows downwards; the machine's Y grows away from you.
        return QPointF(ox + (x - minx) * scale, oy + (maxy - yv) * scale)

    p.setPen(QPen(RULE, 0.5))
    p.setBrush(Qt.NoBrush)
    p.drawRect(area)
    stock_rect = QRectF(pt(sx0, sy0 + st.depth), pt(sx0 + st.width, sy0))
    p.setBrush(STOCK_FILL)
    p.setPen(QPen(GREY, 0.8, Qt.DashLine))
    p.drawRect(stock_rect)
    p.setBrush(Qt.NoBrush)
    for n, pts in s.plan_paths:
        path = QPainterPath(pt(*pts[0]))
        for q in pts[1:]:
            path.lineTo(pt(*q))
        p.setPen(QPen(color_for(n), 0.7))
        p.drawPath(path)
    for n, q in s.plan_points:
        p.setPen(Qt.NoPen)
        p.setBrush(color_for(n))
        p.drawEllipse(pt(*q), 2.6, 2.6)
    p.setBrush(Qt.NoBrush)
    zero, arm = pt(0.0, 0.0), 26.0
    p.setPen(QPen(INK, 1.2))
    p.drawLine(zero, QPointF(zero.x() + arm, zero.y()))
    p.drawLine(zero, QPointF(zero.x(), zero.y() - arm))
    p.setBrush(INK)
    p.drawEllipse(zero, 3.0, 3.0)
    p.setBrush(Qt.NoBrush)
    page.text(zero.x() + arm + 2, zero.y() - 6, 12, 12, "X", 8.0, True)
    page.text(zero.x() - 4, zero.y() - arm - 14, 12, 12, "Y", 8.0, True)
    page.text(zero.x() - 22, zero.y() + 3, 20, 10, "0,0", 7.0)
    # Legend.
    x, ly = 0.0, area.bottom() + 3
    numbers = sorted({n for n, _p in s.plan_paths} | {n for n, _q in s.plan_points})[:8]
    for n in numbers:
        p.fillRect(QRectF(x, ly + 5, 9, 3), color_for(n))
        label = f"{n} {w.legend.get(n, '')}"
        page.text(x + 12, ly, 110, 12, label, 8.0)
        x += 12 + min(110.0, QFontMetricsF(_font(8.0)).horizontalAdvance(label)) + 10


def draw_page(painter: QPainter, blocks_on_page: list, s, w: Words, number: int,
              count: int) -> None:
    painter.save()
    painter.translate(MARGIN, MARGIN)
    page = _Page(painter)
    y = 0.0
    for kind, payload, height in blocks_on_page:
        if kind == "header":
            page.text(0, y, CONTENT_W, 12, w.title.upper(), 9.0, True, GREY)
            page.text(0, y + 14, CONTENT_W, 30, w.job_name, 22.0, True)
            page.rule(0, y + 49, CONTENT_W, 1.5, INK)
        elif kind in ("info_top", "info_bottom"):
            half = (CONTENT_W - 12) / 2
            left, right = ((tr("Job"), w.job), (tr("Stock"), w.stock)) if kind == "info_top" \
                else ((tr("Work origin"), w.origin), (tr("Machine"), w.machine))
            page.field_box(0, y, half, left[0], left[1])
            page.field_box(half + 12, y, half, right[0], right[1])
        elif kind == "files":
            page.box_title(0, y, CONTENT_W, tr("Program files"))
            yy = y + BOX_TITLE
            for i, (name, tools) in enumerate(w.files, start=1):
                page.text(0, yy, 18, FIELD_ROW, f"{i}.", 9.0, color=GREY)
                page.text(18, yy, CONTENT_W - 110, FIELD_ROW, name, 9.0, True)
                page.text(CONTENT_W - 88, yy, 88, FIELD_ROW, tools, 9.0, color=GREY,
                          align=Qt.AlignRight)
                yy += FIELD_ROW
            if w.files_note:
                page.text(0, yy, CONTENT_W, FIELD_ROW, w.files_note, 8.5, color=GREY)
        elif kind == "warnings":
            page.box_title(0, y, CONTENT_W, tr("Check before machining"))
            yy = y + BOX_TITLE
            for text in w.warnings:
                page.text(0, yy, 14, 14, "⚠", 10.0, True)
                page.wrapped(16, yy + 1, CONTENT_W - 16, WARNING_ROW, text, 9.0)
                yy += WARNING_ROW
        elif kind == "plan":
            draw_plan(page, y, s, w)
        elif kind == "section":
            page.text(0, y + SECTION_TITLE - 18, CONTENT_W, 18, payload, 13.0, True)
        elif kind == "tool_header":
            page.table_row(y, w.tool_header, TOOL_WIDTHS, header=True)
        elif kind == "tool":
            page.table_row(y, payload, TOOL_WIDTHS)
        elif kind == "operation_header":
            page.table_row(y, w.operation_header, OPERATION_WIDTHS, header=True)
        elif kind == "operation":
            cells, dimmed = payload
            page.table_row(y, cells, OPERATION_WIDTHS, dimmed=dimmed)
        y += height + SPACING
    footer_y = CONTENT_H + FOOTER - 12
    page.rule(0, footer_y - 4, CONTENT_W, 0.5)
    page.text(0, footer_y, CONTENT_W - 100, 12, w.footer, 7.5, color=GREY)
    page.text(CONTENT_W - 100, footer_y, 100, 12,
              tr("Page {page} of {pages}", page=number, pages=count), 7.5, color=GREY,
              align=Qt.AlignRight)
    painter.restore()


def write_pdf(job, path: str, base_name: str, prepared: _dt.datetime | None = None) -> int:
    """Build, word and write the setup sheet of ``job`` (work coordinates)
    to ``path``. Returns the number of pages."""
    s = sheet_mod.build(job)
    w = words(s, base_name, prepared)
    pages = paginate(blocks(w, s))
    writer = QPdfWriter(path)
    writer.setTitle(f"{w.title} — {w.job_name}")
    writer.setCreator("IngeTrazo CAM")
    writer.setPageLayout(QPageLayout(QPageSize(QPageSize.A4), QPageLayout.Portrait,
                                     QMarginsF(0, 0, 0, 0)))
    writer.setResolution(72)                        # one unit = one point
    painter = QPainter(writer)
    painter.setRenderHint(QPainter.Antialiasing)
    try:
        for i, page_blocks in enumerate(pages):
            if i:
                writer.newPage()
            painter.fillRect(QRectF(0, 0, PAGE_W, PAGE_H), QColor("#FFFFFF"))
            draw_page(painter, page_blocks, s, w, i + 1, len(pages))
    finally:
        painter.end()
    return len(pages)

