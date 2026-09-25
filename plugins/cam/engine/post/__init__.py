# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""G-code post-processors: GRBL and LinuxCNC on a shared base.

:func:`post_job` is the one entry point: it writes the program(s) for the
job's controller, then reads every file back with that dialect's rules and
checks it reproduces the toolpath (:func:`..verify.round_trip`). A file
that fails either check comes back with an error issue and must not be
saved — the UI refuses to export it.
"""
from __future__ import annotations

from ..issues import CamError, Issue, ERROR
from . import grbl, linuxcnc
from .base import PostResult

POSTS = {"grbl": grbl, "linuxcnc": linuxcnc}
EXTENSIONS = {"grbl": ".nc", "linuxcnc": ".ngc"}


def post_job(job, toolpath, translate=None, controller=None) -> PostResult:
    """Post ``toolpath`` for ``controller`` (default: the job's) and verify
    the result. Raises :class:`CamError` when the dialect cannot express
    the toolpath at all."""
    from ..verify import parse_gcode, round_trip
    name = controller or job.post.controller
    module = POSTS.get(name)
    if module is None:
        raise CamError("unsupported_operation", kind=name)
    result = module.post(job, toolpath, translate)
    for f in result.files:
        parsed = parse_gcode(f.text, name)
        for line, rule in parsed.violations:
            result.issues.append(Issue("post_dialect_violation", ERROR,
                                       {"line": line, "rule": rule, "file": f.suffix}))
        problem = round_trip(f.expected, parsed.motions)
        if problem is not None:
            problem.params["file"] = f.suffix
            result.issues.append(problem)
    return result
