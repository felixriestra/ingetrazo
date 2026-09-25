# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Machine-readable problems: codes with parameters, never sentences.

The engine never produces user-facing text. Everything it has to say — a
compilation failure, a verifier finding, a post-processor refusal — is an
:class:`Issue` carrying a stable ``code`` and the numbers that explain it
(``region_too_small_for_tool`` with ``{"diameter": 6.0}``). Only the UI
(``plugins/cam/ui/messages.py``) turns a code into a sentence in the user's
language, so messages cannot end up half-translated and the engine stays
testable headlessly.

Codes are part of the plugin's internal contract: tests assert on them and
the message table must cover every one (``tests/test_cam_i18n.py``). Add a
code here, and its sentence there, in the same commit.
"""
from __future__ import annotations

from dataclasses import dataclass, field

ERROR = "error"
WARNING = "warning"


@dataclass
class Issue:
    """One finding. ``params`` holds plain JSON values only (numbers,
    strings) so an issue can travel in a saved job or a test fixture."""
    code: str
    severity: str = ERROR
    params: dict = field(default_factory=dict)
    command_index: int | None = None
    operation_id: str | None = None
    operation_name: str | None = None

    @property
    def is_error(self) -> bool:
        return self.severity == ERROR


class CamError(Exception):
    """Raised by generators, the compiler and the posts; carries one
    :class:`Issue`. ``str(exc)`` is the code plus parameters — for logs
    and test output only, never shown to the user."""

    def __init__(self, code: str, **params) -> None:
        self.issue = Issue(code, ERROR, params)
        super().__init__(f"{code} {params}" if params else code)

    @property
    def code(self) -> str:
        return self.issue.code


#: Every code the engine can emit, with its severity. The UI message table
#: is checked against this set.
CODES: dict[str, str] = {
    # --- inputs (raised while generating) ---------------------------------
    "invalid_stock": ERROR,
    "invalid_tool": ERROR,
    "invalid_tool_diameter": ERROR,
    "invalid_stepover": ERROR,
    "invalid_depth": ERROR,
    "invalid_step_down": ERROR,
    "invalid_allowance": ERROR,
    "invalid_inset": ERROR,
    "depth_exceeds_stock": ERROR,
    "invalid_boundary": ERROR,
    "region_too_small_for_tool": ERROR,
    "no_points": ERROR,
    "insufficient_points": ERROR,
    "invalid_point": ERROR,
    "point_outside_stock": ERROR,
    "invalid_peck": ERROR,
    "invalid_dwell": ERROR,
    "invalid_tolerance": ERROR,
    # --- compiler ---------------------------------------------------------
    "no_operations": ERROR,
    "cancelled": ERROR,
    "missing_tool": ERROR,
    "parameter_mismatch": ERROR,
    "unsupported_operation": ERROR,
    "spindle_above_machine": ERROR,
    "spindle_below_machine": WARNING,
    "feed_above_machine": ERROR,
    "plunge_above_machine": ERROR,
    "axial_depth_exceeds_flute": ERROR,
    "pass_depth_exceeds_flute": ERROR,
    "pass_depth_exceeds_recommended": ERROR,
    "non_finite_motion": ERROR,
    # --- geometry selection (extraction and region building) --------------
    "empty_selection": ERROR,
    "open_path": ERROR,
    "degenerate_path": ERROR,
    "self_intersecting_path": ERROR,
    "disjoint_contours": ERROR,
    "not_planar": ERROR,
    # --- verifier ---------------------------------------------------------
    "safe_height_invalid": ERROR,
    "clearance_height_invalid": ERROR,
    "travel_limits_invalid": ERROR,
    "duplicate_tool_number": ERROR,
    "empty_toolpath": ERROR,
    "tool_change_spindle_running": ERROR,
    "tool_not_in_job": ERROR,
    "non_finite_coordinate": ERROR,
    "rapid_inside_stock": ERROR,
    "rapid_through_stock": ERROR,
    "feed_invalid": ERROR,
    "cut_spindle_stopped": ERROR,
    "cut_before_tool_change": ERROR,
    "cut_below_stock_bottom": ERROR,
    "travel_exceeded": ERROR,
    "arc_non_finite": ERROR,
    "arc_radius_mismatch": ERROR,
    "gouge": ERROR,
    # --- post-processors --------------------------------------------------
    "post_invalid_tool_number": ERROR,
    "post_invalid_spindle_speed": ERROR,
    "post_invalid_feed": ERROR,
    "post_invalid_dwell": ERROR,
    "post_compensation_unsupported": ERROR,
    "post_arc_too_small": WARNING,
    "post_round_trip_failed": ERROR,
    "post_dialect_violation": ERROR,
}
