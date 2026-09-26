# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Length formatting shared by the model's dimensions and the sheet cotas.

Model lengths are metres. A dimension style (or a sheet cota) picks how
they read: metric (``m`` / ``cm`` / ``mm``), decimal imperial (``in``,
``ft``, ``ft-in``) or fractional imperial the way a carpenter writes it
(``in-frac`` → ``1 1/2"``, ``ft-in-frac`` → ``1'6 1/2"``). For the
fractional forms the precision field picks the finest denominator:
0 → whole inches, 1 → 1/4, 2 → 1/16, 3 → 1/32, 4 → 1/64.
"""
from __future__ import annotations

from fractions import Fraction

IN_M = 0.0254
FT_M = 0.3048

#: Every unit a dimension style may name, in the order the pickers show.
UNIT_CHOICES = ("m", "cm", "mm", "in", "ft", "ft-in", "in-frac", "ft-in-frac")

_FRAC_DENOMS = {0: 1, 1: 4, 2: 16, 3: 32, 4: 64}


def _frac_inches(inches: float, decimals: int) -> str:
    """``6.5`` → ``6 1/2``; ``0.75`` → ``3/4``; ``6.0`` → ``6``."""
    denom = _FRAC_DENOMS.get(max(0, min(4, int(decimals))), 16)
    total = Fraction(round(inches * denom)), denom
    q = Fraction(total[0], total[1])
    whole = int(q)
    rest = q - whole
    if rest == 0:
        return str(whole)
    if whole == 0:
        return f"{rest.numerator}/{rest.denominator}"
    return f"{whole} {rest.numerator}/{rest.denominator}"


def format_length(metres: float, units: str = "m", decimals: int = 2) -> str:
    n = max(0, min(6, int(decimals)))
    u = units or "m"
    if u == "in":
        return f"{metres / IN_M:.{n}f}\""
    if u == "ft":
        return f"{metres / FT_M:.{n}f}'"
    if u in ("ft-in", "ft-in-frac", "in-frac"):
        sign = "-" if metres < 0 else ""
        total_in = abs(metres) / IN_M
        if u == "in-frac":
            return f"{sign}{_frac_inches(total_in, n)}\""
        feet = int(total_in // 12)
        inches = total_in - feet * 12
        if u == "ft-in":
            if round(inches, n) >= 12:
                feet, inches = feet + 1, 0.0
            return f"{sign}{feet}'{inches:.{n}f}\""
        txt = _frac_inches(inches, n)
        if txt == "12":
            feet, txt = feet + 1, "0"
        return f"{sign}{feet}'{txt}\""
    factor = {"m": 1.0, "cm": 100.0, "mm": 1000.0}.get(u, 1.0)
    return f"{metres * factor:.{n}f} {u if u in ('m', 'cm', 'mm') else 'm'}"


# ---- The model's units (issue #33, @pacaeiro) -------------------------------
#
# «Myself, when doing architecture I work in meters and with mechanical
# pieces all the work is in millimeters.» The document carries the unit a
# bare number is typed in and every readout is shown in (``Scene.units``);
# the live scene is bound here once so tools and panels — which mostly have
# no scene at hand — format through ``fmt_len``/``fmt_area``.

DEFAULT_MODEL_UNITS = {"length": "m", "precision": 2}

# English source strings, translated where they are shown (issue #65: the
# labels were written in Spanish, so an English UI showed Spanish).
UNIT_LABELS = {
    "m": "Metres (m)", "cm": "Centimetres (cm)", "mm": "Millimetres (mm)",
    "in": "Decimal inches (in)", "ft": "Decimal feet (ft)",
    "ft-in": "Feet and inches (1\'6\")", "in-frac": "Fractional inches",
    "ft-in-frac": "Fractional feet and inches",
}

#: Metres per typed unit when the number carries no unit of its own.
_BARE_SCALE = {"m": 1.0, "cm": 0.01, "mm": 0.001, "in": IN_M, "ft": FT_M,
               "ft-in": IN_M, "in-frac": IN_M, "ft-in-frac": IN_M}

_SCENE = None


def bind_scene(scene) -> None:
    """The scene whose ``units`` the formatters read (the viewport's; a
    document loads INTO that object, so one binding lasts the session)."""
    global _SCENE
    _SCENE = scene


def model_units_of(scene) -> dict:
    """The validated units of a scene — or of a raw document payload (a
    dict), which is how the .igz loader reads them."""
    if isinstance(scene, dict):
        u = scene.get("units")
    else:
        u = getattr(scene, "units", None) if scene is not None else None
    if isinstance(u, dict) and u.get("length") in _BARE_SCALE:
        return {"length": u["length"], "precision": int(u.get("precision", 2))}
    return dict(DEFAULT_MODEL_UNITS)


#: QSettings key of the units a NEW document starts with (issue #121: «I
#: model every part in millimetres» — the unit was lost with each new file).
NEW_DOCUMENT_KEY = "units/new_document"


def new_document_units() -> dict:
    """The units a new, empty document starts with: the ones the user last
    chose «for new documents» in Preferences, or metres. Opening a file never
    reads this — a document keeps its own units (an old .igz without them is
    in metres, as it was drawn)."""
    import json

    from PySide6.QtCore import QSettings
    try:
        raw = QSettings().value(NEW_DOCUMENT_KEY)
        return model_units_of({"units": json.loads(raw)} if raw else {})
    except (TypeError, ValueError):
        return dict(DEFAULT_MODEL_UNITS)


def remember_new_document_units(units: dict) -> None:
    import json

    from PySide6.QtCore import QSettings
    st = QSettings()
    st.setValue(NEW_DOCUMENT_KEY, json.dumps(model_units_of({"units": units})))
    st.sync()


def apply_units(scene, units: dict) -> None:
    """Give ``scene`` these units; the dimension style follows them, as the
    Preferences dialog has always done."""
    chosen = model_units_of({"units": units})
    scene.units = chosen
    style = getattr(scene, "dimension_style", None)
    if isinstance(style, dict):
        style["units"] = chosen["length"]
        style["decimals"] = chosen["precision"]


def model_units() -> dict:
    return model_units_of(_SCENE)


def model_unit() -> str:
    return model_units()["length"]


def model_precision() -> int:
    return model_units()["precision"]


def unit_label(code: str) -> str:
    from core.i18n import tr
    return tr(UNIT_LABELS[code]) if code in UNIT_LABELS else code


def bare_number_scale() -> float:
    """Metres per unit for a number typed without a unit: ``2`` is 2 m in
    a metric document and 2 mm in a millimetre one."""
    return _BARE_SCALE.get(model_unit(), 1.0)


def fmt_len(metres: float) -> str:
    """A length in the model's units and precision — the one formatter
    every readout uses (tool labels, Entity Info, status bar)."""
    return format_length(float(metres), model_unit(), model_precision())


def fmt_num(metres: float) -> str:
    """The number alone, for ``a × b`` pairs; imperial forms keep their
    marks because the mark IS the unit."""
    u = model_unit()
    if u in ("m", "cm", "mm"):
        factor = {"m": 1.0, "cm": 100.0, "mm": 1000.0}[u]
        return f"{float(metres) * factor:.{model_precision()}f}"
    return fmt_len(metres)


def fmt_pair(a: float, b: float) -> str:
    """``3.00 × 2.00 m`` (metric: one unit at the end) or
    ``3'0" × 2'0"`` (imperial: each number carries its mark)."""
    u = model_unit()
    if u in ("m", "cm", "mm"):
        return f"{fmt_num(a)} × {fmt_len(b)}"
    return f"{fmt_len(a)} × {fmt_len(b)}"


#: The fewest decimals that still resolve a millimetre (or 1/16") in each
#: unit — what a parts list needs whatever the document's display
#: precision is: a 4 mm hinge leaf in a document shown to the centimetre
#: read «0.00 m» and looked like a sheet with no thickness at all.
_FINE_DECIMALS = {"m": 3, "cm": 1, "mm": 0, "in": 2, "ft": 3,
                  "ft-in": 2, "in-frac": 2, "ft-in-frac": 2}


def fine_precision() -> int:
    """The document's precision, raised to millimetre resolution."""
    return max(model_precision(), _FINE_DECIMALS.get(model_unit(), 3))


def fmt_len_fine(metres: float) -> str:
    """:func:`fmt_len` at least to the millimetre — for part sizes and cut
    lists, where a thickness is the number that matters."""
    return format_length(float(metres), model_unit(), fine_precision())


def fmt_triple(a: float, b: float, c: float, fine: bool = False) -> str:
    """``0.58 × 0.45 × 0.04 m`` — a part's length × width × thickness, in
    the same idiom as :func:`fmt_pair`. ``fine`` resolves the millimetre
    (:func:`fmt_len_fine`)."""
    u = model_unit()
    n = fine_precision() if fine else model_precision()
    if u in ("m", "cm", "mm"):
        factor = {"m": 1.0, "cm": 100.0, "mm": 1000.0}[u]
        a_, b_ = (f"{float(v) * factor:.{n}f}" for v in (a, b))
        return f"{a_} × {b_} × {format_length(float(c), u, n)}"
    return " × ".join(format_length(float(v), u, n) for v in (a, b, c))


def fmt_area(square_metres: float) -> str:
    """An area in the model's units squared."""
    u = model_unit()
    n = model_precision()
    if u in ("m", "cm", "mm"):
        factor = {"m": 1.0, "cm": 1e4, "mm": 1e6}[u]
        return f"{float(square_metres) * factor:.{n}f} {u}²"
    if u == "ft" or u.startswith("ft"):
        return f"{float(square_metres) / (FT_M * FT_M):.{n}f} ft²"
    return f"{float(square_metres) / (IN_M * IN_M):.{n}f} in²"
