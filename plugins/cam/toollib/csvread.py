# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Reading vendor CSV catalogues — 2DCam's ``CSVReader`` and ``ValueParser``.

Tool vendors export spreadsheets from whatever their office uses: German
ones in Windows-1252 with ``;`` between columns and ``,`` for decimals,
American ones in UTF-8 with fractional inches. Nothing here trusts the
locale of the machine running it; the delimiter, the decimal separator and
the encoding are read from the file itself unless the catalogue profile
states them.

Python's :mod:`csv` is not used on purpose: the catalogue profile filters
banner and footer rows BEFORE the header is found, which needs the lines
first, and the quote handling has to match 2DCam's so both apps read the
same file the same way.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


class CSVError(Exception):
    """``unreadable`` (no encoding decodes it) or ``no_header_row``."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class CSVTable:
    headers: list
    rows: list
    delimiter: str
    decimal: str
    encoding: str


# ---- decoding ------------------------------------------------------------------

def decode(data: bytes, hint: str | None = None) -> tuple:
    """``(text, encoding_used)``. A stated ``hint`` (utf8, cp1252, utf16) is
    tried first; then UTF-8, UTF-16 only when the file has its byte-order
    mark, Windows-1252 and finally Latin-1, which decodes anything."""
    tries = {"utf8": "utf-8", "cp1252": "cp1252", "utf16": "utf-16"}
    order = []
    if hint and hint.lower() in tries:
        order.append(hint.lower())
    order.append("utf8")
    # UTF-16 only when the file says so: without a BOM almost any even-length
    # byte string «decodes» as UTF-16 — into CJK mojibake.
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        order.append("utf16")
    order += ["cp1252", "latin1"]
    for name in order:
        codec = tries.get(name, name)
        try:
            text = data.decode(codec)
        except (UnicodeDecodeError, LookupError):
            continue
        return text.lstrip("﻿"), name
    raise CSVError("unreadable")


# ---- sniffing ------------------------------------------------------------------

def sniff_delimiter(lines) -> str:
    """The delimiter giving the most, and most consistent, columns in the
    first 20 lines."""
    best = (",", 0, float("inf"))
    for d in (";", ",", "\t", "|"):
        counts = [len(line.split(d)) for line in lines[:20]]
        if not counts or max(counts) <= 1:
            continue
        top = max(counts)
        variance = sum(abs(c - top) for c in counts)
        if top > best[1] or (top == best[1] and variance < best[2]):
            best = (d, top, variance)
    return best[0]


_COMMA_DECIMAL = re.compile(r"\d,\d{1,2}(?!\d)")


def sniff_decimal(delimiter: str, text: str) -> str:
    """A ``;``-delimited European file writes decimals with ``,``; a
    ``,``-delimited file cannot, or its columns would not parse."""
    if delimiter == ",":
        return "."
    return "," if _COMMA_DECIMAL.search(text) else "."


# ---- parsing -------------------------------------------------------------------

def parse(data: bytes, profile) -> CSVTable:
    """Parse ``data`` with a :class:`.catalog.CatalogProfile`'s encoding,
    delimiter, decimal, banner-row filters and header row."""
    text, used = decode(data, profile.encoding)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if profile.skip_rows_matching:
        pats = [re.compile(p) for p in profile.skip_rows_matching]
        lines = [ln for ln in lines if not any(p.search(ln) for p in pats)]
    lines = [ln for ln in lines if ln.strip()]

    delimiter = profile.delimiter[0] if profile.delimiter else sniff_delimiter(lines)
    decimal = (profile.decimal_separator[0] if profile.decimal_separator
               else sniff_decimal(delimiter, text))

    # A quoted field may span lines: re-join while the quotes are unbalanced.
    records = []
    i = 0
    while i < len(lines):
        line = lines[i]
        while line.count('"') % 2 != 0 and i + 1 < len(lines):
            i += 1
            line += "\n" + lines[i]
        records.append(split_line(line, delimiter))
        i += 1

    if profile.header_row >= len(records):
        raise CSVError("no_header_row")
    headers = [h.strip() for h in records[profile.header_row]]
    return CSVTable(headers, records[profile.header_row + 1:], delimiter, decimal, used)


def split_line(line: str, delimiter: str) -> list:
    """RFC 4180 fields: quotes, doubled quotes, delimiters inside quotes.
    Fields are trimmed."""
    fields, cur = [], []
    in_quotes = False
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if in_quotes:
            if ch == '"':
                if i + 1 < n and line[i + 1] == '"':
                    cur.append('"')                # an escaped ""
                    i += 1
                else:
                    in_quotes = False
            else:
                cur.append(ch)
        elif ch == '"':
            in_quotes = True
        elif ch == delimiter:
            fields.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    fields.append("".join(cur).strip())
    return fields


# ---- values --------------------------------------------------------------------

_KEEP = set("0123456789.,-/+")


def number(raw: str, decimal: str, strip=None) -> float | None:
    """A numeric cell, with no locale involved. Handles ``6,35``, ``6.35``,
    ``1 234,5``, ``Ø6``, ``6 mm``, ``Z2``, ``60°``, ``1/4``, ``1-1/4``."""
    s = raw.strip()
    if not s or s == "-" or s.lower() == "n/a":
        return None
    for token in strip or ():
        s = re.sub(re.escape(token), "", s, flags=re.IGNORECASE)
    s = s.replace("⁄", "/").replace("″", "").replace("′", "")
    s = "".join(c for c in s if c in _KEEP)
    if not s:
        return None
    if "/" in s:
        return fraction(s)
    thousands = "." if decimal == "," else ","
    s = s.replace(thousands, "").replace(decimal, ".")
    return _to_float(s)


def fraction(s: str) -> float | None:
    """``1/4`` → 0.25, ``1-1/4`` → 1.25."""
    def simple(t: str):
        parts = t.split("/")
        if len(parts) != 2:
            return None
        num, den = _to_float(parts[0]), _to_float(parts[1])
        if num is None or den is None or den == 0:
            return None
        return num / den

    parts = [p for p in s.split("-", 1) if p]
    if len(parts) == 2:
        whole, frac = _to_float(parts[0]), simple(parts[1])
        if whole is not None and frac is not None:
            return whole + frac
    only = simple(s)
    return only if only is not None else _to_float(s)


def _to_float(s: str) -> float | None:
    try:
        return float(s)
    except ValueError:
        return None


def to_canonical(value: float, unit: str, native: str) -> float:
    """A parsed value in its column's unit → mm, mm/min, degrees…
    ``auto`` columns take the vendor's native unit."""
    effective = native if unit == "auto" else unit
    if effective in ("inch", "frac_inch"):
        return value * 25.4
    if effective == "cm":
        return value * 10.0
    if effective == "m_min":
        return value * 1000.0
    return value


def fractional_inch_label(mm: float, tolerance: float = 0.02) -> str:
    """A stored mm value written the way an imperial vendor would: 6.35 →
    ``1/4"``; off every 64th by more than ``tolerance``, decimal inches."""
    inches = mm / 25.4
    sixty_fourths = int(inches * 64 + 0.5) if inches >= 0 else 0
    if sixty_fourths <= 0 or abs(sixty_fourths / 64 * 25.4 - mm) > tolerance:
        return f'{inches:.3f}"'
    num, den = sixty_fourths, 64
    while num % 2 == 0 and den > 1:
        num //= 2
        den //= 2
    whole, rem = divmod(num, den)
    if rem == 0:
        return f'{whole}"'
    return f'{whole}-{rem}/{den}"' if whole > 0 else f'{rem}/{den}"'
