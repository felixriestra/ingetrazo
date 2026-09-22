# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""SVG rendering of a CuttingPlan — a direct port of 2DCutList's
``SVGExporter.swift``: raw string templating (simple enough that no SVG
library earns its keep), matching layout constants/CSS classes/labeling
conventions so the diagrams look the same as 2DCutList's own output.

This module is independent of ``plugins/cutlist.py``'s QPainter-based
preview/PDF/PNG path (see that module's docstring for why both exist).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.cutlist import CutAxis, CuttingPlan, CutListProject, GrainAxis, SheetPlan

_CUT_COLORS = ["#d32f2f", "#1565c0", "#ef6c00", "#2e7d32",
               "#7b1fa2", "#00838f", "#c2185b", "#455a64"]

_PART_PALETTE = ["#f3c98b", "#9ec5e5", "#b7d7a8", "#d5b5e5",
                 "#f4a7a7", "#a7d8d2", "#e6d59a"]

_CSS = """<style>
text{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;fill:#17202a}
.title{font-size:26px;font-weight:700}
.subtitle{font-size:13px;fill:#52606d}
.part{stroke:#34495e;stroke-width:1.5}
.partLabel{font-size:13px;font-weight:700;text-anchor:middle;dominant-baseline:middle}
.remnant{fill:#dff3e4;stroke:#548b60;stroke-width:1;stroke-dasharray:5 3}
.scrap{fill:#f5f5f5;stroke:#bdbdbd;stroke-width:1;stroke-dasharray:3 3}
.cut{fill:none;stroke-width:3;stroke-dasharray:7 4}
.cutLabel{font-size:12px;font-weight:800;paint-order:stroke;stroke:#fff;stroke-width:4px;stroke-linejoin:round}
.sheet{fill:#f7f4ec;stroke:#263238;stroke-width:2}
</style>"""


@dataclass
class SVGOptions:
    #: Which sheets to include. ``None`` = all sheets.
    sheet_ids: set[str] | None = None
    #: Append the parts-list BOM table after the sheet diagrams.
    include_parts_table: bool = True


SVGOptions.ALL = SVGOptions(sheet_ids=None, include_parts_table=True)
SVGOptions.NO_TABLE = SVGOptions(sheet_ids=None, include_parts_table=False)


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _fmt(v: float) -> str:
    return str(int(round(v))) if abs(round(v) - v) < 0.001 else f"{v:.1f}"


def _integer(v: float) -> str:
    return str(int(v))


def _part_color(part_id: str) -> str:
    h = 0
    for b in part_id.encode("utf-8"):
        h = (h * 31 + b) & 0x7fffffff
    return _PART_PALETTE[h % len(_PART_PALETTE)]


def _cut_label(label: str, x: float, y: float, color: str) -> str:
    return f'<text x="{_fmt(x)}" y="{_fmt(y)}" class="cutLabel" fill="{color}">{label}</text>'


def render(project: CutListProject, plan: CuttingPlan, options: SVGOptions | None = None) -> str:
    options = options or SVGOptions.ALL
    sheets = [s for s in plan.sheets if options.sheet_ids is None or s.id in options.sheet_ids]
    is_single_sheet = len(sheets) == 1

    page_w = 1200.0
    sheet_h = 620.0
    table_row_h = 24.0
    table_top_pad = 56.0
    table_bot_pad = 30.0
    header_h = 0.0 if is_single_sheet else 120.0

    parts_for_table = _sorted_parts_for_table(project, plan) if options.include_parts_table else []
    table_h = (0.0 if not parts_for_table
               else table_top_pad + (len(parts_for_table) + 1) * table_row_h + table_bot_pad)

    total_h = header_h + len(sheets) * sheet_h + table_h

    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_integer(page_w)}" '
        f'height="{_integer(total_h)}" viewBox="0 0 {_integer(page_w)} {_integer(total_h)}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        _CSS,
    ]

    if not is_single_sheet:
        out += [
            f'<text x="40" y="42" class="title">{_esc(project.name)} — cutting plan</text>',
            '<text x="40" y="70" class="subtitle">'
            f'{plan.metrics.sheet_count} sheet(s) · '
            f'{plan.metrics.cut_count} production cuts · '
            f'{plan.metrics.utilization * 100:.1f}% component area · '
            f'kerf {_fmt(project.workshop.kerf)} mm</text>',
            '<text x="40" y="94" class="subtitle">'
            f'New-sheet prep: square short edge ({_fmt(project.workshop.short_edge_cleanup)} mm), '
            f'trim {_fmt(project.workshop.long_edge_trim)} mm along long edge. '
            'A/B intersection is the production datum.</text>',
        ]

    for idx, sheet in enumerate(sheets):
        out += _sheet_panel(sheet, plan, project, panel_y=header_h + idx * sheet_h,
                             is_single=is_single_sheet)

    if parts_for_table:
        out += _parts_table(parts_for_table, project,
                             start_y=header_h + len(sheets) * sheet_h,
                             page_w=page_w, row_h=table_row_h, top_pad=table_top_pad)

    out.append("</svg>")
    return "\n".join(out)


def _sheet_panel(sheet: SheetPlan, plan: CuttingPlan, project: CutListProject,
                  panel_y: float, is_single: bool) -> list[str]:
    out: list[str] = []
    origin_x = 40.0
    drawing_w = 800.0
    drawing_h = 500.0
    origin_y = panel_y + (40.0 if is_single else 58.0)
    scale = min(drawing_w / sheet.usable_rect.width, drawing_h / sheet.usable_rect.height)
    actual_w = sheet.usable_rect.width * scale
    actual_h = sheet.usable_rect.height * scale

    def px(v: float) -> float:
        return origin_x + v * scale

    def py(v: float) -> float:
        return origin_y + actual_h - v * scale

    header_y = panel_y + (20.0 if is_single else 28.0)
    sheet_label = (f"{_esc(project.name)} — {_esc(plan.label(sheet))}" if is_single
                   else _esc(plan.label(sheet)))
    out.append(f'<text x="40" y="{_fmt(header_y)}" class="title" '
               f'style="font-size:20px">{sheet_label}</text>')
    out.append(f'<text x="840" y="{_fmt(header_y)}" class="subtitle">'
               f'Stock {_fmt(sheet.stock.length)} × {_fmt(sheet.stock.width)} mm · '
               f'usable {_fmt(sheet.usable_rect.width)} × {_fmt(sheet.usable_rect.height)} mm</text>')

    out.append(f'<rect x="{_fmt(origin_x)}" y="{_fmt(origin_y)}" width="{_fmt(actual_w)}" '
               f'height="{_fmt(actual_h)}" class="sheet"/>')

    for r in sheet.remnants:
        if r.area <= 1:
            continue
        rw, rh = r.width * scale, r.height * scale
        reusable = (max(r.width, r.height) >= project.workshop.minimum_remnant_length
                    and min(r.width, r.height) >= project.workshop.minimum_remnant_width)
        cls = "remnant" if reusable else "scrap"
        out.append(f'<rect x="{_fmt(px(r.x))}" y="{_fmt(py(r.max_y))}" width="{_fmt(rw)}" '
                   f'height="{_fmt(rh)}" class="{cls}"/>')

    char_w = 7.8
    for p in sheet.placements:
        color = _part_color(p.instance.source_part_id)
        pw, ph = p.rect.width * scale, p.rect.height * scale
        out.append(f'<rect x="{_fmt(px(p.rect.x))}" y="{_fmt(py(p.rect.max_y))}" '
                   f'width="{_fmt(pw)}" height="{_fmt(ph)}" fill="{color}" class="part"/>')
        cx = px(p.rect.x + p.rect.width / 2)
        cy = py(p.rect.y + p.rect.height / 2)
        label = _esc(p.id) + (" ↻" if p.rotated else "")
        lw = len(label) * char_w
        if lw <= pw - 6:
            out.append(f'<text x="{_fmt(cx)}" y="{_fmt(cy)}" class="partLabel">{label}</text>')
        elif lw <= ph - 6:
            out.append(f'<text x="{_fmt(cx)}" y="{_fmt(cy)}" class="partLabel" '
                       f'transform="rotate(-90,{_fmt(cx)},{_fmt(cy)})">{label}</text>')
        else:
            max_chars = max(1, int((pw - 6) / char_w))
            clipped = label[:max_chars - 1] + "…" if len(label) > max_chars else label
            out.append(f'<text x="{_fmt(cx)}" y="{_fmt(cy)}" class="partLabel">{clipped}</text>')

    for cut in sheet.cuts:
        s = cut.split
        color = _CUT_COLORS[(cut.number - 1) % len(_CUT_COLORS)]
        label = f"C{cut.number:02d}"
        if s.axis == CutAxis.VERTICAL:
            lx, y1, y2 = px(s.coordinate), py(s.workpiece.y), py(s.workpiece.max_y)
            out.append(f'<line x1="{_fmt(lx)}" y1="{_fmt(y1)}" x2="{_fmt(lx)}" y2="{_fmt(y2)}" '
                       f'class="cut" stroke="{color}"/>')
            out.append(_cut_label(label, lx + 5, min(y1, y2) + 15, color))
            out.append(_cut_label(label, lx + 5, max(y1, y2) - 6, color))
        else:
            ly, x1, x2 = py(s.coordinate), px(s.workpiece.x), px(s.workpiece.max_x)
            out.append(f'<line x1="{_fmt(x1)}" y1="{_fmt(ly)}" x2="{_fmt(x2)}" y2="{_fmt(ly)}" '
                       f'class="cut" stroke="{color}"/>')
            out.append(_cut_label(label, min(x1, x2) + 5, ly - 6, color))
            out.append(_cut_label(label, max(x1, x2) - 34, ly - 6, color))

    note_y = origin_y + actual_h + 22
    out.append(f'<text x="{_fmt(origin_x)}" y="{_fmt(note_y)}" class="subtitle">'
               'Datum A/B (0,0) at lower-left · cut IDs at both endpoints · '
               'lines span their active rectangular workpiece</text>')
    return out


@dataclass
class _TableRow:
    part_id: str
    description: str
    length: float
    width: float
    thickness: float
    quantity: int
    material: str
    sheets: str
    grain: str


def _source_part_id(instance_id: str) -> str:
    """``"P01-2"`` -> ``"P01"``; falls back to the bare id when there is no
    ``-N`` suffix (quantity == 1)."""
    pieces = instance_id.split("-")
    source = "-".join(pieces[:-1])
    return source or instance_id


def _sorted_parts_for_table(project: CutListProject, plan: CuttingPlan) -> list[_TableRow]:
    sheets_by_part_id: dict[str, set[int]] = {}
    for sheet in plan.sheets:
        pos = plan.position(sheet)
        for p in sheet.placements:
            key = _source_part_id(p.id)
            sheets_by_part_id.setdefault(key, set()).add(pos)

    def sort_key(part):
        material = project.part_settings.get(part.id)
        material = material.material if material else ""
        return (material, part.thickness, part.id)

    rows = []
    for part in sorted(project.parts, key=sort_key):
        settings = project.part_settings.get(part.id)
        sheet_nums = sorted(sheets_by_part_id.get(part.id, set()))
        sheets_str = ", ".join(str(n) for n in sheet_nums) if sheet_nums else "—"
        grain_axis = settings.grain_axis if settings else GrainAxis.NONE
        grain_str = {GrainAxis.NONE: "—", GrainAxis.LENGTH: "Along length",
                     GrainAxis.WIDTH: "Along width"}[grain_axis]
        rows.append(_TableRow(
            part_id=part.id, description=part.description, length=part.length,
            width=part.width, thickness=part.thickness, quantity=part.quantity,
            material=settings.material if settings else "Unassigned",
            sheets=sheets_str, grain=grain_str))
    return rows


def _parts_table(parts: list[_TableRow], project: CutListProject,
                  start_y: float, page_w: float, row_h: float, top_pad: float) -> list[str]:
    out: list[str] = []
    pad = 40.0
    table_w = page_w - pad * 2

    cols = [
        ("Part ID", 90, "start"), ("Description", 200, "start"),
        ("Length mm", 90, "end"), ("Width mm", 90, "end"), ("Thick mm", 80, "end"),
        ("Qty", 50, "end"), ("Material", 200, "start"), ("Grain", 120, "start"),
        ("Sheet(s)", 80, "end"),
    ]
    col_x: list[float] = []
    cx = pad
    for _header, w, _align in cols:
        col_x.append(cx)
        cx += w + 10

    title_y = start_y + 36
    hdr_y = start_y + top_pad
    body_y = hdr_y + row_h

    out.append(f'<text x="{_fmt(pad)}" y="{_fmt(title_y)}" class="title" '
               f'style="font-size:18px">Parts list</text>')
    total_qty = sum(p.quantity for p in parts)
    out.append(f'<text x="{_fmt(pad + 200)}" y="{_fmt(title_y)}" class="subtitle">'
               f'{len(parts)} parts · {total_qty} pieces total</text>')

    out.append(f'<rect x="{_fmt(pad)}" y="{_fmt(hdr_y)}" width="{_fmt(table_w)}" '
               f'height="{_fmt(row_h)}" fill="#263238"/>')

    for i, (header, w, align) in enumerate(cols):
        tx = col_x[i] + w if align == "end" else col_x[i] + 4
        anchor = "end" if align == "end" else "start"
        out.append(f'<text x="{_fmt(tx)}" y="{_fmt(hdr_y + 16)}" '
                   f'style="font-size:11px;font-weight:700;fill:#ffffff;'
                   f'text-anchor:{anchor}">{header}</text>')

    for row_idx, row in enumerate(parts):
        ry = body_y + row_idx * row_h
        fill = "#f7f4ec" if row_idx % 2 == 0 else "#ffffff"
        out.append(f'<rect x="{_fmt(pad)}" y="{_fmt(ry)}" width="{_fmt(table_w)}" '
                   f'height="{_fmt(row_h)}" fill="{fill}"/>')

        cells = [row.part_id, row.description, _fmt(row.length), _fmt(row.width),
                 _fmt(row.thickness), str(row.quantity), row.material, row.grain, row.sheets]
        for i, cell in enumerate(cells):
            _header, w, align = cols[i]
            tx = col_x[i] + w if align == "end" else col_x[i] + 4
            anchor = "end" if align == "end" else "start"
            bold = ";font-weight:700" if i == 0 else ""
            out.append(f'<text x="{_fmt(tx)}" y="{_fmt(ry + 16)}" '
                       f'style="font-size:11px;text-anchor:{anchor}{bold}">{_esc(cell)}</text>')

        out.append(f'<line x1="{_fmt(pad)}" y1="{_fmt(ry + row_h)}" '
                   f'x2="{_fmt(pad + table_w)}" y2="{_fmt(ry + row_h)}" '
                   'stroke="#dee2e6" stroke-width="0.5"/>')

    total_rows_h = (len(parts) + 1) * row_h
    out.append(f'<rect x="{_fmt(pad)}" y="{_fmt(hdr_y)}" width="{_fmt(table_w)}" '
               f'height="{_fmt(total_rows_h)}" fill="none" stroke="#263238" stroke-width="1.5"/>')

    footer_y = hdr_y + total_rows_h + 20
    w = project.workshop
    out.append(f'<text x="{_fmt(pad)}" y="{_fmt(footer_y)}" class="subtitle">'
               f'Kerf {_fmt(w.kerf)} mm · Short-edge cleanup {_fmt(w.short_edge_cleanup)} mm · '
               f'Long-edge trim {_fmt(w.long_edge_trim)} mm · '
               f'Reusable remnant ≥ {_fmt(w.minimum_remnant_length)} × '
               f'{_fmt(w.minimum_remnant_width)} mm</text>')

    return out
