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
