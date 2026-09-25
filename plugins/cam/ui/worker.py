# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Calculating off the main thread.

The house rule (docs/plugins.md): never touch the document from a worker.
The dock hands :func:`start` a Job built from its own copy of the state
(:meth:`~..state.CamState.work_job` makes a fresh one), and the worker
reports through the dock's ``Signal(object)`` — queued, to a bound method,
so the result lands on the main thread. ``object`` and not ``dict``: a
queued ``dict`` signal arrives as a COPY, and an unhashable engine result
does not survive that.

A calculation can be cancelled: the engine polls ``cancelled()`` between
operations. A newer calculation supersedes an older one — each carries a
generation number and the dock ignores results that are not the latest.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class Outcome:
    generation: int
    ok: bool
    compiled: object = None          # engine.compiler.CompileResult
    issues: list = field(default_factory=list)
    error: object = None             # the Issue that stopped compilation
    seconds: float = 0.0


def run(job, generation: int, cancelled=None) -> Outcome:
    """Compile, verify and gouge-check ``job``. Pure; used by the thread
    and directly by tests."""
    from ..engine import compiler, verify
    from ..engine.issues import CamError, Issue
    t0 = time.perf_counter()
    try:
        compiled = compiler.compile_job(job, cancelled=cancelled)
    except CamError as exc:
        return Outcome(generation, False, error=exc.issue, seconds=time.perf_counter() - t0)
    except Exception as exc:  # noqa: BLE001 — a bug must not kill the thread silently
        return Outcome(generation, False,
                       error=Issue("non_finite_motion", params={"detail": repr(exc)}),
                       seconds=time.perf_counter() - t0)
    report = verify.verify(job, compiled.toolpath)
    issues = list(compiled.warnings) + list(report.issues) + verify.check_gouges(job, compiled)
    return Outcome(generation, True, compiled, issues, seconds=time.perf_counter() - t0)


class Calculation:
    """One background calculation; ``cancel()`` asks it to stop."""

    def __init__(self, job, generation: int, deliver) -> None:
        self._cancel = threading.Event()
        self.generation = generation
        self._thread = threading.Thread(
            target=lambda: deliver(run(job, generation, self._cancel.is_set)),
            name=f"cam-calc-{generation}", daemon=True)

    def start(self) -> "Calculation":
        self._thread.start()
        return self

    def cancel(self) -> None:
        self._cancel.set()

    def is_alive(self) -> bool:
        return self._thread.is_alive()
