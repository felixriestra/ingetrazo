# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The CAM plugin's ``tr()``: its own catalogue first, then IngeTrazo's.

Same format as ``core/i18n.py`` — keys are the English source strings,
``i18n/<lang>.json`` maps them — but the CAM terms live with the plugin:
the host's catalogues stay untouched (rebases stay easy) and the plugin
behaves the same from the per-user plugin folder. Lookup order: this
plugin's catalogue, then ``core.i18n.tr`` (shared words like "Cancel"),
then the English source. The language is whatever IngeTrazo runs in.

``tests/test_cam_i18n.py`` fails if any ``tr("…")`` literal under
``plugins/cam/`` is missing from ``es.json`` or ``pt-BR.json``, if a
catalogue keeps stale keys, or if ``{placeholders}`` do not match.
"""
from __future__ import annotations

import json
from pathlib import Path

_DIR = Path(__file__).resolve().parent / "i18n"
_catalogs: dict = {}


def _language() -> str:
    try:
        from core.i18n import current_language
        return current_language() or "en"
    except Exception:  # noqa: BLE001 — headless use without the host
        return "en"


def catalog(lang: str) -> dict:
    """The plugin's catalogue for ``lang`` (``{}`` for English or when the
    file is missing or broken), loaded once."""
    if lang not in _catalogs:
        data = {}
        path = _DIR / f"{lang}.json"
        if lang != "en" and path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                data = {str(k): str(v) for k, v in raw.items() if not k.startswith("__")}
            except (OSError, ValueError):
                data = {}
        _catalogs[lang] = data
    return _catalogs[lang]


def translate(text: str) -> str:
    """``text`` in the current language, without interpolation."""
    lang = _language()
    out = catalog(lang).get(text)
    if out is None:
        try:
            from core.i18n import tr as host_tr
            out = host_tr(text)
        except Exception:  # noqa: BLE001
            out = text
    return out


def tr(text: str, /, **kwargs) -> str:
    """Translate ``text`` and fill ``{placeholders}`` from ``kwargs``; a
    translation with a broken placeholder falls back to the English."""
    out = translate(text)
    if kwargs:
        try:
            out = out.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            out = text.format(**kwargs)
    return out
