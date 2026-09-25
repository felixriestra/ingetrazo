# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The GRBL and LinuxCNC post-processors.

What a controller receives is what can break a machine, so these pin the
dialect rules (no M6/G43/canned cycles/compensation in GRBL), the
one-file-per-tool split, canned cycles in LinuxCNC, units (G20 with every
number converted), ASCII-only comments, the ``.`` decimal separator under
any locale, and the round trip: every posted file read back reproduces
the toolpath within 0.001 mm.
"""
from __future__ import annotations

import locale

import pytest

from plugins.cam.engine import compiler
from plugins.cam.engine.issues import CamError
from plugins.cam.engine.models import (DrillingParameters, Job, Operation, PocketParameters,
                                       ProfileParameters, Region, Stock, Strategy, Tab, Tool)
from plugins.cam.engine.post import post_job
from plugins.cam.engine.post.base import ascii_comment
from plugins.cam.engine.verify import expected_motions, parse_gcode, round_trip


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _job(controller="grbl", units="millimeters"):
    t1 = Tool(number=1, name="6 mm Fräse ñ", diameter=6.0)
    t2 = Tool(number=2, name="3 mm end mill", diameter=3.0, fluteLength=12)
    drill = Tool(number=3, name="5 mm drill", kind="drill", diameter=5.0, fluteLength=30,
                 overallLength=60, cuttingFeed=300, plungeFeed=150)
    job = Job(name="Tablero ñandú", units=units, stock=Stock(width=100, depth=75, height=18),
              tools=[t1, t2, drill])
    job.post.controller = controller
    job.operations = [
        Operation("Perfil exterior", "outsideProfile", t1.id,
                  parameters=ProfileParameters(depth=6, stepDown=3),
                  strategy=Strategy(geometry=Region(rect(10, 10, 90, 65)),
                                    tabs=[Tab(0.25, 6, 2)], entry="helix")),
        Operation("Cajeado", "pocket", t2.id,
                  parameters=PocketParameters(depth=4, stepDown=2),
                  strategy=Strategy(geometry=Region(rect(30, 25, 70, 50)))),
        Operation("Taladros", "drilling", drill.id,
                  parameters=DrillingParameters(depth=10, points=[(20, 20, 0), (80, 55, 0)],
                                                peckDepth=4)),
        Operation("Otra vez T1", "outsideProfile", t1.id,
                  parameters=ProfileParameters(depth=2, stepDown=2),
                  strategy=Strategy(geometry=Region(rect(20, 20, 40, 40)))),
    ]
    return job


def _post(job, **kw):
    tp = compiler.compile_job(job).toolpath
    return tp, post_job(job, tp, **kw)


def test_grbl_writes_one_file_per_tool_change():
    job = _job("grbl")
    tp, res = _post(job)
    assert not res.issues, [(i.code, i.params) for i in res.issues]
    assert [f.tool_numbers for f in res.files] == [[1], [2], [3], [1]]
    assert all(f.extension == ".nc" for f in res.files)
    assert res.files[0].suffix == "_T1_6-mm-frase-n"
    for f in res.files:
        assert "and set Z zero before running this file" in f.text
        assert f.text.rstrip().endswith("M30")
        assert "G21" in f.text and "G90 G94 G17" in f.text


def test_grbl_files_never_contain_what_grbl_rejects():
    job = _job("grbl")
    _tp, res = _post(job)
    for f in res.files:
        body = "\n".join(line for line in f.text.splitlines() if not line.startswith("("))
        for word in ("M6", "G43", "G81", "G82", "G83", "G41", "G42", "M8"):
            assert f" {word} " not in f" {body} ".replace("\n", " "), (word, f.suffix)
        assert all(ord(ch) < 128 for ch in f.text)
        assert all(len(line) <= 80 for line in f.text.splitlines())


def test_grbl_refuses_controller_compensation():
    job = _job("grbl")
    job.operations[0].strategy.compensation = "controller"
    tp = compiler.compile_job(job).toolpath
    with pytest.raises(CamError) as exc:
        post_job(job, tp)
    assert exc.value.code == "post_compensation_unsupported"


def test_linuxcnc_one_file_with_tool_changes_and_canned_cycles():
    job = _job("linuxcnc")
    tp, res = _post(job)
    assert not res.issues, [(i.code, i.params) for i in res.issues]
    (f,) = res.files
    assert f.extension == ".ngc"
    text = f.text
    assert text.startswith("%\n") and text.rstrip().endswith("%")
    for needle in ("T1 M6", "G43 H1", "T2 M6", "G43 H2", "T3 M6", "G91.1", "G64 P0.01",
                   "M2"):
        assert needle in text, needle
    cycles = [ln for ln in text.splitlines() if " G83 " in f" {ln} "]
    assert len(cycles) == 2 and all("Q4" in ln and "G99" in ln and "R" in ln for ln in cycles)
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if " G83 " in f" {ln} ":
            assert lines[i + 1] == "G80"


def test_linuxcnc_plain_and_dwell_cycles():
    job = _job("linuxcnc")
    job.operations[2].parameters.peckDepth = 0
    _tp, res = _post(job)
    assert "G99 G81 " in res.files[0].text
    job.operations[2].parameters.dwellSeconds = 0.5
    _tp, res = _post(job)
    assert "G99 G82 " in res.files[0].text and " P0.5 " in res.files[0].text
    assert not res.issues


def test_linuxcnc_passes_controller_compensation_through():
    job = _job("linuxcnc")
    job.operations[0].strategy.compensation = "controller"
    _tp, res = _post(job)
    text = res.files[0].text
    assert "\nG41\n" in text and "\nG40\n" in text
    assert not res.issues


@pytest.mark.parametrize("controller", ["grbl", "linuxcnc"])
def test_inch_programs_convert_every_number(controller):
    mm_job, inch_job = _job(controller), _job(controller, "inches")
    _tp, mm = _post(mm_job)
    _tp, inch = _post(inch_job)
    assert not inch.issues, [(i.code, i.params) for i in inch.issues]
    assert "G20" in inch.files[0].text and "G21" not in inch.files[0].text
    # The same motion read back from both files, in millimetres.
    a = parse_gcode(mm.files[0].text, controller).motions
    b = parse_gcode(inch.files[0].text, controller).motions
    assert len(a) == len(b)
    for m, n in zip(a, b):
        for k in range(3):
            if m.to[k] is not None:
                assert n.to[k] == pytest.approx(m.to[k], abs=0.002)
        if m.feed is not None:
            assert n.feed == pytest.approx(m.feed, rel=1e-3, abs=0.1)


@pytest.mark.parametrize("controller", ["grbl", "linuxcnc"])
def test_decimal_point_whatever_the_locale(controller):
    for name in ("es_ES.UTF-8", "pt_BR.UTF-8", "de_DE.UTF-8"):
        try:
            old = locale.setlocale(locale.LC_NUMERIC, name)
        except locale.Error:
            continue
        try:
            _tp, res = _post(_job(controller))
        finally:
            locale.setlocale(locale.LC_NUMERIC, "C")
        for f in res.files:
            body = [ln for ln in f.text.splitlines() if not ln.startswith("(")]
            assert not any("," in ln for ln in body)
        del old


def test_round_trip_detects_a_tampered_file():
    job = _job("linuxcnc")
    tp, res = _post(job)
    f = res.files[0]
    parsed = parse_gcode(f.text).motions
    assert round_trip(f.expected, parsed) is None
    # And the expected motion is the canonical toolpath's, not the post's idea.
    assert len(f.expected) == len(expected_motions(tp.commands))
    tampered = f.text.replace("G1 ", "G1 X0.5 ", 1)
    problem = round_trip(f.expected, parse_gcode(tampered).motions)
    assert problem is not None and problem.code == "post_round_trip_failed"


def test_comments_are_ascii_and_translated():
    job = _job("linuxcnc")
    es = {"Operation: {name}": "Operación: {name}", "Outside profile": "Perfil exterior"}
    _tp, res = _post(job, translate=lambda s: es.get(s, s))
    text = res.files[0].text
    assert "(Operacion: Perfil exterior)" in text
    assert "(Operacion: Cajeado)" in text
    assert all(ord(ch) < 128 for ch in text)


def test_ascii_comment():
    assert ascii_comment("Cajeado ñ (fino) · 2") == "Cajeado n [fino] - 2"
    assert ascii_comment("Furação") == "Furacao"
    assert ascii_comment("x" * 100, 70) == "x" * 70


def test_arcs_can_be_flattened_for_linuxcnc():
    job = _job("linuxcnc")
    job.post.flattenArcs = True
    _tp, res = _post(job)
    body = "\n".join(ln for ln in res.files[0].text.splitlines() if not ln.startswith("("))
    assert "G2 " not in body and "G3 " not in body
    assert not res.issues
