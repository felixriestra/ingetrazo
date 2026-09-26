# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The persistent tool library: a port of 2DCam's ``ToolLibrary/``.

A job's tool table (:class:`..engine.models.Tool`) holds the cutters of ONE
job, with the feeds chosen for its material. The library is the shop's
tool cabinet, kept between jobs: every cutter with its geometry and where
that geometry came from, the cutting data known for it, and the fallback
rules that estimate feeds and speeds for any material when nothing better
is known. Adding a library tool to a job resolves its cutting data for the
job's material and machine and copies the result into the job; the job
never refers back to the library, so a job opens the same on a machine
with a different library, or none.

Like the engine, this package imports no Qt and no i18n and works in
millimetres. It speaks in codes with parameters; ``ui/messages.py``
turns them into sentences.

- :mod:`.model` — :class:`LibraryTool`, the tool types, the cutter
  silhouette and the bridge into a job's tool table;
- :mod:`.schema` — the SQLite schema, identical to 2DCam's so a 2DCam
  ``ToolLibrary.sqlite`` opens here unchanged;
- :mod:`.resolver` — what should this tool cut this material at?
- :mod:`.validator` — the checks a catalogue row must pass;
- :mod:`.csvread` / :mod:`.catalog` / :mod:`.importer` — vendor CSV
  catalogues: parsing, per-vendor column profiles, review and commit;
- :mod:`.repository` — the SQLite store.
"""
