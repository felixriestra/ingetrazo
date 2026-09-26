# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Fixes to the pinned OpenSKP that are waiting upstream — applied at run
time only while the installed OpenSKP still lacks them.

**Legacy dimensions anchored inside groups** (iamahsanmehmood/openskp#384).
A pre-2021 ``CDimensionLinear`` follows each connection ref with an entity
ref and two lists of refs — the instance paths of the anchored entity. The
pinned reader skips a FIXED 42/82 bytes there, right only while the lists
are empty (a dimension on loose geometry). Anchored to a vertex inside
nested groups, the read slides off the record and the root entity reader,
which stops at the first unreadable item, drops everything after it:
Juan José Noriega's SketchUp 2018 house opened with 2 of its 72 root
entities — walls and lawn, no upper floor, roofs, openings or trees
(12 placements instead of 2384).

The patch is detected by BEHAVIOUR, not by version: once the upstream
reader carries ``_connection_paths`` it is left alone.
"""
from __future__ import annotations

import struct

_applied = False


def apply() -> bool:
    """Install the fixes into ``openskp.legacy`` when needed. Returns True
    when a patch was installed (idempotent)."""
    global _applied
    if _applied:
        return False
    try:
        from openskp import legacy as L
    except Exception:  # noqa: BLE001 — no openskp: nothing to fix
        return False
    _applied = True
    if hasattr(L, "_connection_paths") or not hasattr(L, "_read_dimlinear"):
        return False

    def _path_ref(ar, r):
        # MFC writes an object in full the first time anything points at it:
        # a dimension serialized before the group it is anchored in carries
        # that whole group here. Read it like any object then.
        tag = r.peek_u16()
        new = tag == 0xFFFF or (tag != 0x7FFF and tag & 0x8000)
        if tag == 0x7FFF:
            big = struct.unpack_from('<I', r.data, r.pos + 2)[0]
            new = bool(big & 0x80000000)
        if new:
            slot, _name, _value = ar.read_object(r)
            return slot
        return L._entity_ref(ar, r)

    def _connection_paths(ar, r):
        extra = L._entity_ref(ar, r)
        lists = []
        for _ in range(2):
            count = r.u32()
            if count > 1000:
                raise L.LegacyParseError(
                    f"implausible dimension path length {r.ctx()}")
            lists.append([_path_ref(ar, r) for _ in range(count)])
        return extra, lists[0], lists[1]

    def _read_dimlinear(ar, r):
        L._preamble(ar, r)
        db = L._drawbase(ar, r)
        text = r.utf16()
        ar.read_object(r, expect='CSkFont')
        b37 = r.raw(37)
        c1 = L._entity_ref(ar, r)
        path1 = _connection_paths(ar, r)
        b32 = r.raw(32)
        c2 = L._entity_ref(ar, r)
        path2 = _connection_paths(ar, r)
        b72 = r.raw(72)
        offset = struct.unpack_from('<d', b72, 52)[0]
        out = {'k': 'dimension', 'db': db, 'text': text,
               'connect': (c1, c2), 'paths': (path1, path2),
               'offset': offset}
        if struct.unpack_from('<I', b37, 5)[0] == 1:
            out['a'] = struct.unpack_from('<3d', b37, 13)
        if struct.unpack_from('<I', b32, 0)[0] == 1:
            out['b'] = struct.unpack_from('<3d', b32, 8)
        return out

    L._path_ref = _path_ref
    L._connection_paths = _connection_paths
    L._read_dimlinear = _read_dimlinear
    L._READERS['CDimensionLinear'] = _read_dimlinear
    return True
