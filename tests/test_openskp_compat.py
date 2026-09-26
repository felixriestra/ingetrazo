# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The run-time fix for legacy dimensions anchored inside groups
(formats/openskp_compat.py; upstream iamahsanmehmood/openskp#384).

A user's SketchUp 2018 house opened with 2 of its 72 root entities: a
dimension anchored to a vertex inside nested groups was read with a fixed
length, and the root reader stopped at the next item. Synthetic bytes —
the user's files are not in the repository."""
from __future__ import annotations

import struct


def big(slot: int) -> bytes:
    return struct.pack('<HI', 0x7FFF, slot)


def test_the_patch_is_installed_and_reads_instance_paths():
    from formats import openskp_compat
    from openskp import legacy as L
    openskp_compat.apply()
    assert hasattr(L, "_connection_paths")
    assert L._READERS['CDimensionLinear'] is L._read_dimlinear
    data = (big(0x0F69CA) + struct.pack('<I', 2) + big(0x166157)
            + big(0x165BBC) + struct.pack('<I', 0) + b'TAIL')
    r = L._R(data, 0)
    extra, p1, p2 = L._connection_paths(None, r)
    assert (extra, p1, p2) == (0x0F69CA, [0x166157, 0x165BBC], [])
    assert data[r.pos:] == b'TAIL'


def test_an_empty_path_is_the_old_fixed_layout():
    """Loose-geometry dimensions — every one that already parsed — keep
    their exact length: a null ref and two empty lists are 10 bytes."""
    from formats import openskp_compat
    from openskp import legacy as L
    openskp_compat.apply()
    r = L._R(b'\x00' * 10 + b'TAIL', 0)
    assert L._connection_paths(None, r) == (None, [], [])
    assert r.pos == 10


def test_apply_is_idempotent_and_leaves_a_fixed_upstream_alone(monkeypatch):
    from formats import openskp_compat
    openskp_compat.apply()
    assert openskp_compat.apply() is False          # second call: no-op
    monkeypatch.setattr(openskp_compat, "_applied", False)
    assert openskp_compat.apply() is False          # upstream has it now
