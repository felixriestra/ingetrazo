# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The CAM plugin ships complete in Spanish and Portuguese (Brazil).

Every ``tr("…")`` literal under ``plugins/cam/``, every G-code comment
template the engine emits, and every name the header gives a work zero
must be in ``plugins/cam/i18n/es.json`` and ``pt-BR.json``, with the same
``{placeholders}``; neither catalogue may keep keys nothing uses any more.
Every engine issue code must have a sentence. (The host's pt-BR covers
about two thirds of its Spanish; the plugin must not repeat that gap.)
"""
from __future__ import annotations

import ast
import json
import re
import string
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CAM = ROOT / "plugins" / "cam"


def source_strings() -> set:
    keys = set()
    for path in CAM.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "tr"
                    and node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                keys.add(node.args[0].value)
    from plugins.cam.build import KIND_NAMES
    from plugins.cam.engine.ops.common import COMMENT_TEMPLATES
    from plugins.cam.engine.post.base import HEADER_TEMPLATES, POINT_NAMES, ZERO_NAMES
    keys |= set(COMMENT_TEMPLATES) | set(HEADER_TEMPLATES)
    keys |= set(POINT_NAMES.values()) | set(ZERO_NAMES.values()) | set(KIND_NAMES.values())
    from plugins.cam.state import DEFAULT_JOB_NAME, STARTER_TOOL_NAMES
    keys |= set(STARTER_TOOL_NAMES) | {DEFAULT_JOB_NAME}
    return keys


def fields(s: str) -> set:
    return {f for _t, f, _s, _c in string.Formatter().parse(s) if f}


@pytest.mark.parametrize("lang", ["es", "pt-BR"])
def test_catalogue_is_complete_and_current(lang):
    cat = json.loads((CAM / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
    keys = source_strings()
    missing = sorted(keys - set(cat))
    stale = sorted(set(cat) - keys)
    assert not missing, f"{lang}.json lacks {missing}"
    assert not stale, f"{lang}.json keeps unused {stale}"
    bad = [k for k, v in cat.items() if fields(k) != fields(v)]
    assert not bad, f"{lang}.json placeholders differ: {bad}"
    untranslated = [k for k, v in cat.items() if not v.strip()]
    assert not untranslated


def test_every_engine_code_has_a_sentence():
    from plugins.cam.engine.issues import CODES
    from plugins.cam.ui.messages import codes_with_messages
    assert set(CODES) <= codes_with_messages(), set(CODES) - codes_with_messages()


@pytest.mark.parametrize("lang", ["es", "pt-BR"])
def test_gcode_comments_stay_ascii_in_every_language(lang, monkeypatch):
    import plugins.cam.i18n as cam_i18n
    from plugins.cam.engine import compiler
    from plugins.cam.engine.models import (Job, Operation, PocketParameters, Region, Strategy,
                                           Tool)
    from plugins.cam.engine.post import post_job
    monkeypatch.setattr(cam_i18n, "_language", lambda: lang)
    t = Tool()
    job = Job(tools=[t], operations=[Operation("Vaciado ñandú", "pocket", t.id,
                                               parameters=PocketParameters(depth=3),
                                               strategy=Strategy(geometry=Region(
                                                   [(20, 20), (60, 20), (60, 50), (20, 50)])))])
    tp = compiler.compile_job(job).toolpath
    for controller in ("grbl", "linuxcnc"):
        res = post_job(job, tp, translate=cam_i18n.translate, controller=controller)
        assert not res.issues
        text = res.files[0].text
        assert all(ord(ch) < 128 for ch in text)
        assert "nandu" in text
        body = [ln for ln in text.splitlines() if not ln.startswith("(")]
        assert not any(re.search(r"\d,\d", ln) for ln in body)


def test_the_plugin_tr_falls_back_to_the_host_then_english(monkeypatch):
    import plugins.cam.i18n as cam_i18n
    monkeypatch.setattr(cam_i18n, "_language", lambda: "es")
    assert cam_i18n.tr("Pocket") == "Vaciado"
    assert cam_i18n.tr("Operation: {name}", name="X") == "Operación: X"
    assert cam_i18n.tr("A string nobody translated") == "A string nobody translated"
