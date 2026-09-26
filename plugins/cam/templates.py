# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Stock templates: a job setup to start new jobs from.

A template is everything a job's setup holds and nothing it draws: the
stock (size, thickness, material, work zero), the units, the controller,
the machine limits, the heights, the post options and the tool table. No
geometry, no operations. It is stored as an ``.igcam`` like any job
(:mod:`.jobfile`), in the user's template folder, so a template can also
be opened, looked at and shared as a file.

A few templates come built in (:data:`BUILTIN`): common sheet goods and
plates. Their names are English here and translated by the UI.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .jobfile import SUFFIX, JobFileError, load_job, save_job
from .state import CamState

#: Built-in templates: (key, English name, width, depth, thickness mm,
#: material). Everything else is a new job's default setup.
BUILTIN = (
    ("mdf-2440x1220x18", "MDF sheet 2440 × 1220 × 18 mm", 2440.0, 1220.0, 18.0, "mdf"),
    ("plywood-1220x610x12", "Plywood 1220 × 610 × 12 mm", 1220.0, 610.0, 12.0, "plywood"),
    ("hardwood-600x200x20", "Hardwood board 600 × 200 × 20 mm", 600.0, 200.0, 20.0,
     "darkWood"),
    ("acrylic-600x400x3", "Acrylic sheet 600 × 400 × 3 mm", 600.0, 400.0, 3.0, "plastic"),
    ("aluminium-200x150x6", "Aluminium plate 200 × 150 × 6 mm", 200.0, 150.0, 6.0,
     "aluminium"),
)


@dataclass
class Template:
    """One template: built in (``path`` None) or the user's own file."""
    key: str
    name: str
    state: CamState
    path: Path | None = None

    @property
    def builtin(self) -> bool:
        return self.path is None


def as_template(state: CamState) -> CamState:
    """The setup of ``state`` alone: no operations, no drawing links, the
    setup not yet confirmed (a job made from it confirms its own)."""
    t = CamState.from_dict(state.to_dict())
    t.job.operations = []
    t.sources = {}
    t.bounds = None
    t.setupDone = False
    return t


def builtin_templates(translate=None) -> list:
    tx = translate or (lambda s: s)
    out = []
    for key, name, w, d, h, material in BUILTIN:
        s = CamState.new_job(tx(name), translate=translate)
        st = s.job.stock
        st.width, st.depth, st.height, st.material = w, d, h, material
        st.align_origin_to_reference()
        out.append(Template(key, tx(name), s))
    return out


def user_templates(folder) -> list:
    """The templates saved in ``folder``, by name. A file that is not a
    readable job is skipped."""
    from core.scene import Scene
    folder = Path(folder)
    out = []
    if not folder.is_dir():
        return out
    for p in sorted(folder.glob("*" + SUFFIX)):
        try:
            state = load_job(Scene(), p)
        except JobFileError:
            continue
        out.append(Template(p.stem, state.job.name or p.stem, as_template(state), p))
    return sorted(out, key=lambda t: t.name.lower())


def save_template(state: CamState, name: str, folder) -> Path:
    """Save the setup of ``state`` as a template called ``name`` in
    ``folder``; returns the file. A template of the same name is
    replaced."""
    from core.scene import Scene
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    t = as_template(state)
    t.job.name = name
    path = folder / (file_stem(name) + SUFFIX)
    scene = Scene()
    scene.plugin_data = {"cam": t.to_dict()}
    save_job(scene, path)
    return path


def job_from_template(template: Template, name: str) -> CamState:
    """A new job called ``name`` with the template's setup."""
    s = as_template(template.state)
    s.job.name = name
    s.frame = s.frame or CamState.new_job(name).frame
    s.stockFixed = True
    s.stockAuto = False
    return s


def file_stem(name: str) -> str:
    """A file name for ``name``: letters, digits, dashes and underscores."""
    s = re.sub(r"[^\w\-]+", "-", name.strip(), flags=re.UNICODE).strip("-")
    return s or "template"
