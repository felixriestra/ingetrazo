# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Engine issue codes → sentences in the user's language.

The engine speaks in codes and numbers (:mod:`..engine.issues`); this is
the one place they become text. Lengths arrive in millimetres and are shown
in the job's units; feeds likewise per minute. Every code the engine
declares must have a sentence here (``tests/test_cam_i18n.py``).
"""
from __future__ import annotations

from ..engine.models import MM_PER_INCH
from ..i18n import tr

#: Parameters that are lengths (mm) or feeds (mm/min) in the engine.
LENGTH_KEYS = {"diameter", "depth", "height", "step_down", "allowance", "inset", "x", "y", "z",
               "peck", "flute", "bottom", "start_radius", "end_radius", "spread", "safe",
               "delta", "bore", "width", "reach"}
FEED_KEYS = {"feed", "limit_feed"}


def _templates() -> dict:
    """Code → English template. Built by a function so the literals stay
    inside ``tr(...)`` calls the i18n test can find."""
    return {
        "invalid_stock": tr("The stock size must be greater than zero."),
        "invalid_tool": tr("Tool T{number} has invalid dimensions or cutting data."),
        "invalid_tool_diameter": tr("The tool diameter ({diameter}) is not valid."),
        "invalid_stepover": tr("The stepover must be more than 0 and at most 1 (it is {stepover})."),
        "invalid_depth": tr("The depth ({depth}) must be greater than zero."),
        "invalid_step_down": tr("The step-down ({step_down}) must be greater than zero."),
        "invalid_allowance": tr("The stock allowance ({allowance}) cannot be negative."),
        "invalid_inset": tr("The inset ({inset}) leaves no room inside the stock."),
        "depth_exceeds_stock": tr("The depth ({depth}) is more than the stock height ({height})."),
        "invalid_boundary": tr("The selected outline cannot be machined."),
        "region_too_small_for_tool": tr(
            "The {diameter} tool does not fit in this shape. Choose a smaller tool."),
        "no_points": tr("There are no holes to drill in the selection."),
        "insufficient_points": tr("Engraving needs a path of at least two points."),
        "invalid_point": tr("A point ({x}, {y}) has an invalid coordinate."),
        "point_outside_stock": tr("The point ({x}, {y}) lies outside the stock."),
        "invalid_peck": tr("The peck depth ({peck}) cannot be negative."),
        "invalid_dwell": tr("The dwell ({dwell} s) cannot be negative."),
        "invalid_tolerance": tr("The machining tolerances must be greater than zero."),
        "invalid_width": tr("The width ({width}) must be greater than zero."),
        "bore_smaller_than_tool": tr(
            "The {bore} hole is not larger than the {diameter} tool. Choose a smaller tool "
            "or drill it."),
        "bore_outside_stock": tr("The {bore} hole at ({x}, {y}) does not fit inside the stock."),
        "slot_narrower_than_tool": tr(
            "The slot ({width}) is narrower than the {diameter} tool."),
        "slot_endpoints_coincide": tr("The slot's start and end are the same point."),
        "slot_outside_stock": tr("The slot does not fit inside the stock near ({x}, {y})."),
        "chamfer_needs_chamfer_mill": tr(
            "A chamfer needs a chamfer mill (V-bit). Add one to the tool table and choose it."),
        "chamfer_too_wide": tr(
            "A {width} chamfer needs the cutter deeper: at this depth it is only {reach} wide."),
        "chamfer_too_deep": tr("At this depth the chamfer would be wider than the {diameter} tool."),
        "no_operations": tr("There are no enabled operations to calculate."),
        "cancelled": tr("Calculation cancelled."),
        "missing_tool": tr("The operation has no tool from the tool table."),
        "parameter_mismatch": tr("The operation's parameters do not match its kind."),
        "unsupported_operation": tr("This kind of operation is not available ({kind})."),
        "spindle_above_machine": tr(
            "The spindle speed {rpm} rpm is above the machine's maximum ({limit} rpm)."),
        "spindle_below_machine": tr(
            "The spindle speed {rpm} rpm is below the machine's minimum ({limit} rpm)."),
        "feed_above_machine": tr("The feed {feed} is above the machine's maximum ({limit})."),
        "plunge_above_machine": tr(
            "The plunge feed {feed} is above the machine's maximum plunge feed ({limit})."),
        "axial_depth_exceeds_flute": tr(
            "The depth {depth} is more than the tool's flute length ({flute})."),
        "pass_depth_exceeds_flute": tr(
            "The depth per pass {depth} is more than the tool's flute length ({flute})."),
        "pass_depth_exceeds_recommended": tr(
            "The depth per pass {depth} is more than the tool's recommended {limit}."),
        "non_finite_motion": tr("The calculation produced an invalid coordinate."),
        "empty_selection": tr(
            "Choose a path first: in the path list, or click one of its edges in the "
            "drawing."),
        "open_path": tr("One selected path is open. Close it to use it as an outline."),
        "degenerate_path": tr("One selected outline encloses almost no area."),
        "self_intersecting_path": tr("One selected outline crosses itself."),
        "disjoint_contours": tr(
            "The selected outlines do not form one region with islands inside it."),
        "not_planar": tr(
            "The selection is not flat, or not parallel to the job's machining plane."),
        "safe_height_invalid": tr("The safe height must be greater than zero."),
        "clearance_height_invalid": tr(
            "The clearance height must be greater than zero and not above the safe height."),
        "travel_limits_invalid": tr("The machine travel limits are not valid."),
        "duplicate_tool_number": tr("Tool number T{number} is used by more than one tool."),
        "empty_toolpath": tr("The toolpath is empty."),
        "tool_change_spindle_running": tr("A tool change happens while the spindle is running."),
        "tool_not_in_job": tr("Tool T{number} is not in the tool table."),
        "non_finite_coordinate": tr("A move has an invalid coordinate."),
        "rapid_inside_stock": tr("A rapid move ends inside the stock."),
        "rapid_through_stock": tr("A rapid move crosses the stock."),
        "feed_invalid": tr("A cutting move has no valid feed."),
        "cut_spindle_stopped": tr("A cutting move happens with the spindle stopped."),
        "cut_before_tool_change": tr("A cutting move happens before any tool is loaded."),
        "cut_below_stock_bottom": tr("A cut goes below the bottom of the stock ({z})."),
        "travel_exceeded": tr("A move leaves the machine's travel limits."),
        "arc_non_finite": tr("An arc has an invalid centre."),
        "arc_radius_mismatch": tr("An arc's start and end radii do not match."),
        "gouge": tr("The cutter would cut into the part by {depth} near ({x}, {y})."),
        "post_invalid_tool_number": tr("Tool number {number} is not valid for G-code."),
        "post_invalid_spindle_speed": tr("Spindle speed {rpm} is not valid for G-code."),
        "post_invalid_feed": tr("Feed {feed} is not valid for G-code."),
        "post_invalid_dwell": tr("Dwell {seconds} s is not valid for G-code."),
        "post_compensation_unsupported": tr(
            "{controller} cannot offset the path itself. "
            "Use computer compensation for this operation."),
        "post_arc_too_small": tr("A very small arc was written as a straight move."),
        "post_round_trip_failed": tr(
            "The G-code does not read back as the toolpath ({reason}). Do not run it."),
        "post_dialect_violation": tr(
            "The G-code contains something the controller rejects ({rule}, line {line})."),
    }


def format_length(mm, inch: bool) -> str:
    """``mm`` in the job's units, trailing zeros dropped."""
    return _format_length(mm, inch)


def _format_length(mm, inch: bool) -> str:
    if inch:
        return f"{mm / MM_PER_INCH:.4f}".rstrip("0").rstrip(".") + " in"
    return f"{mm:.3f}".rstrip("0").rstrip(".") + " mm"


def _format_feed(mm_min, inch: bool) -> str:
    if inch:
        return f"{mm_min / MM_PER_INCH:.1f} in/min"
    return f"{mm_min:.0f} mm/min"


def describe(issue, inch: bool = False) -> str:
    """The sentence for ``issue``, with the operation's name in front."""
    params = {}
    for k, v in (issue.params or {}).items():
        if isinstance(v, (int, float)) and k in LENGTH_KEYS:
            params[k] = _format_length(float(v), inch)
        elif isinstance(v, (int, float)) and k in ("feed",):
            params[k] = _format_feed(float(v), inch)
        elif k == "limit" and issue.code in ("feed_above_machine", "plunge_above_machine"):
            params[k] = _format_feed(float(v), inch)
        elif k == "limit" and issue.code == "pass_depth_exceeds_recommended":
            params[k] = _format_length(float(v), inch)
        else:
            params[k] = v
    template = _templates().get(issue.code)
    if template is None:
        text = issue.code
    else:
        try:
            text = template.format(**params)
        except (KeyError, IndexError, ValueError):
            text = template
    if issue.operation_name:
        return tr("{operation}: {message}", operation=issue.operation_name, message=text)
    return text


def codes_with_messages() -> set:
    return set(_templates())


# ---- the tool library (plugins/cam/toollib) -----------------------------------
#
# The library speaks in codes too: resolver notes, catalogue-row issues,
# refusals. Catalogue values are shown in millimetres, as catalogues are
# written; feeds and depths the resolver chose are shown in the job's units.

def tool_type_label(tool_type: str) -> str:
    return {
        "end_mill": tr("End mill"), "ball_nose": tr("Ball nose"), "bull_nose": tr("Bull nose"),
        "v_bit": tr("V-bit"), "engraver": tr("Engraving cutter"),
        "tapered_ball": tr("Tapered ball nose"), "drill": tr("Drill"),
        "chamfer": tr("Chamfer mill"), "surfacing": tr("Surfacing cutter"),
        "form": tr("Form tool"), "drag_knife": tr("Drag knife"),
    }.get(tool_type, tool_type)


def material_class_label(class_id: str) -> str:
    """The seeded material classes by id; a class the user or a newer 2DCam
    added keeps its stored name (the caller passes it as the fallback)."""
    return {
        "softwood": tr("Softwood"), "hardwood": tr("Hardwood"), "plywood": tr("Plywood"),
        "mdf": tr("MDF / particleboard"), "acrylic": tr("Acrylic (PMMA)"),
        "polycarb": tr("Polycarbonate"), "pvc": tr("PVC / foamed PVC"),
        "hdpe": tr("HDPE / PE / PP"), "abs": tr("ABS"), "pom": tr("POM / acetal"),
        "nylon": tr("Nylon / PA"), "foam": tr("Rigid foam / tooling board"),
        "composite": tr("Composite / laminate"), "aluminium": tr("Aluminium"),
    }.get(class_id, class_id)


def missing_fields_text(codes) -> str:
    names = {
        "diameter": tr("diameter"), "flute_count": tr("flute count"),
        "cutting_length": tr("cutting length"), "overall_length": tr("overall length"),
        "included_angle": tr("included angle"), "corner_radius": tr("corner radius"),
        "profile_shape": tr("profile shape"),
    }
    return ", ".join(names.get(c, c) for c in codes)


def _note_templates() -> dict:
    return {
        "rpm_melt_cap": tr("Speed capped at {rpm} rpm: {material} melts at higher speeds."),
        "rpm_spindle_max": tr("Speed limited to the spindle's maximum ({rpm} rpm)."),
        "rpm_spindle_min": tr("Speed raised to the spindle's minimum ({rpm} rpm)."),
        "chipload_derated": tr("Chip load reduced to {percent}% for the machine's rigidity."),
        "feed_machine_max": tr("Feed limited to the machine's maximum ({feed})."),
        "plunge_z_max": tr("Plunge limited to the Z maximum ({feed})."),
        "plunge_reduced": tr("The plunge was faster than the cutting feed and was halved."),
        "doc_recommended": tr(
            "Feed shown for this tool's recommended depth of cut ({doc}); the operation "
            "sets the final depth."),
        "doc_outside_curve": tr(
            "The depth {doc} is outside the vendor's chart ({lo} to {hi}); the feed is held "
            "at the nearest published value."),
        "feed_material_factor": tr("Feed ×{factor} for {material}."),
        "feed_cross_grain": tr("Feed ×{factor} for cutting across the grain."),
    }


def library_note(note, inch: bool = False) -> str:
    """A resolver note (:class:`..toollib.resolver.Note`) as a sentence."""
    params = {}
    for k, v in (note.params or {}).items():
        if k == "feed":
            params[k] = _format_feed(float(v), inch)
        elif k in ("doc", "lo", "hi"):
            params[k] = _format_length(float(v), inch)
        elif k == "material":
            params[k] = material_class_label(v)
        elif k == "factor":
            params[k] = f"{float(v):g}"
        else:
            params[k] = v
    template = _note_templates().get(note.code)
    if template is None:
        return note.code
    try:
        return template.format(**params)
    except (KeyError, IndexError, ValueError):
        return template


def cutting_data_source(resolved, vendors: dict | None = None) -> str:
    """Where a resolved feed came from, in one line."""
    vendor = (vendors or {}).get(resolved.vendor_id, resolved.vendor_id or "")
    if resolved.source == "preset":
        if resolved.preset_origin == "user":
            return tr("Your own data: {name}", name=resolved.preset_name or "")
        if resolved.preset_origin == "vendor":
            return tr("Vendor data: {name}", name=resolved.preset_name or "")
        return tr("Estimated preset: {name}", name=resolved.preset_name or "")
    if resolved.source == "feed_curve":
        return tr("Vendor feed chart ({vendor})", vendor=vendor)
    if resolved.vendor_id:
        return tr("Vendor chip-load table ({vendor})", vendor=vendor)
    return tr("Generic chip-load estimate")


def _issue_templates() -> dict:
    return {
        "MISSING_NAME": tr("The row has no name or description."),
        "MISSING_TYPE": tr("The tool type could not be read from a column or the description."),
        "MISSING_DIAMETER": tr("No cutting diameter: the tool cannot be used."),
        "DIA_OUT_OF_RANGE": tr("Diameter {diameter} mm is outside {min}–{max} mm."),
        "DIA_LARGE": tr("Diameter {diameter} mm is large for a {type}. Is it a surfacing cutter?"),
        "UNIT_MISLABEL": tr(
            "Diameter {diameter} looks like inches in a millimetre column ({as_mm} mm?). Fix "
            "the column's unit in the catalogue profile before importing."),
        "SHANK_NONSTANDARD": tr("Shank {shank} mm is not a standard size."),
        "DIA_VS_SHANK": tr(
            "The {diameter} mm cutting diameter is more than {factor}× the {shank} mm shank: "
            "one of the two columns is wrong."),
        "DIA_VS_SHANK/warning": tr(
            "The {diameter} mm cutting diameter is unusually large for a {shank} mm shank."),
        "FLUTES_RANGE": tr("{flutes} flutes is outside {min}–{max}."),
        "FLUTES_HIGH": tr("{flutes} flutes is a lot for a router bit."),
        "FLUTE_LEN_GT_OAL": tr(
            "The cutting length {flute} mm is longer than the overall length {overall} mm."),
        "FLUTE_LEN_RATIO": tr("The cutting length is {ratio}× the diameter. Check it."),
        "BALL_RADIUS_MISMATCH": tr(
            "A ball nose's radius is half its diameter ({half} mm), not {radius} mm; corrected."),
        "MISSING_CORNER_RADIUS": tr("A {type} needs a corner radius."),
        "CORNER_RADIUS_RANGE": tr(
            "The corner radius {radius} mm must be more than 0 and at most {half} mm."),
        "MISSING_ANGLE": tr("A {type} needs an included angle."),
        "ANGLE_RANGE": tr("The included angle {angle}° is outside {min}–{max}°."),
        "ANGLE_UNUSUAL": tr("The included angle {angle}° is not a usual value."),
        "VBIT_HAS_FLAT": tr(
            "A {tip} mm flat tip makes this an engraving cutter, not a true V-bit."),
        "FORM_NO_PROFILE": tr(
            "A form tool without its profile. It stays in the library but cannot cut here."),
        "FLAT_HAS_RADIUS": tr("A flat cutter with a {radius} mm corner radius: is it a bull nose?"),
        "RPM_RANGE": tr("{rpm} rpm is outside {min}–{max} rpm."),
        "RPM_BELOW_SPINDLE": tr(
            "{rpm} rpm is below the spindle's minimum ({limit} rpm); the feed will be derived "
            "again."),
        "RPM_ABOVE_SPINDLE": tr("{rpm} rpm is above the spindle's maximum ({limit} rpm)."),
        "FEED_RANGE": tr("Feed {feed} mm/min is outside {min}–{max} mm/min."),
        "PLUNGE_GT_FEED": tr("The plunge {plunge} mm/min is faster than the feed {feed} mm/min."),
        "PLUNGE_HIGH": tr("The plunge is {percent}% of the cutting feed."),
        "PLUNGE_LOW": tr("The plunge is only {percent}% of the cutting feed."),
        "CHIPLOAD_RANGE": tr(
            "A chip load of {chipload} mm per tooth is outside {min}–{max}: check the feed, "
            "speed and flute columns."),
        "CHIPLOAD_INCONSISTENT": tr(
            "The stated chip load ({stated} mm per tooth) does not match the feed, speed and "
            "flutes ({implied} mm per tooth)."),
        "STEPDOWN_RANGE": tr("The step-down must be more than zero."),
        "STEPDOWN_GT_FLUTE_LEN": tr(
            "The step-down {stepdown} mm is deeper than the {flute} mm cutting length."),
        "STEPDOWN_DEEP": tr("The step-down is {ratio}× the diameter."),
        "STEPOVER_RANGE": tr(
            "The stepover {stepover} mm must be more than 0 and at most the diameter "
            "({diameter} mm)."),
        "EXISTS_CHANGED": tr("Already in the library; {count} value(s) differ."),
    }


def _num(v) -> str:
    if isinstance(v, float):
        return f"{v:.4f}".rstrip("0").rstrip(".") if abs(v) < 1 else f"{v:.2f}".rstrip("0").rstrip(".")
    return str(v)


def library_issue(issue) -> str:
    """A catalogue-row issue (:class:`..toollib.validator.ValidationIssue`)."""
    params = {k: (tool_type_label(v) if k == "type" else _num(v))
              for k, v in (issue.params or {}).items()}
    templates = _issue_templates()
    template = templates.get(f"{issue.code}/{issue.severity}") or templates.get(issue.code)
    if template is None:
        return issue.code
    try:
        return template.format(**params)
    except (KeyError, IndexError, ValueError):
        return template


def library_error(code: str, **params) -> str:
    """Refusals and file problems of the library, by code."""
    templates = {
        "incomplete": tr("{name} is missing: {fields}. Fill them in before using it in a job."),
        "form_tool_unsupported": tr(
            "{name} is a form tool. This version cannot cut with form tools."),
        "no_cutting_data": tr(
            "{name} has no cutting data for {material}, and not enough geometry to estimate "
            "any."),
        "unreadable": tr("The file could not be read as text (UTF-8, UTF-16 or Windows-1252)."),
        "no_header_row": tr("The header row the catalogue profile expects is not in the file."),
        "bad_profile": tr("This catalogue profile cannot be used: {detail}"),
        "open": tr("The tool library could not be opened: {detail}"),
        "sql": tr("The tool library could not do that: {detail}"),
        "restored_backup": tr(
            "The tool library file was damaged and has been restored from its latest backup."),
        "restored_fresh": tr(
            "The tool library file was damaged and there was no backup: a new library was "
            "started. The damaged file was kept next to it."),
    }
    template = templates.get(code, code)
    try:
        return template.format(**params)
    except (KeyError, IndexError, ValueError):
        return template


def library_codes_with_messages() -> dict:
    """For the i18n test: every library code and whether it has a sentence."""
    return {"notes": set(_note_templates()),
            "issues": {k.split("/")[0] for k in _issue_templates()}}
