# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Follow Me around a circle that shares the profile's axis is a lathe
(#125, #128): the classic SketchUp sphere — a circle swept along a circle
with the same centre — must come out as a closed sphere of the profile's
radius, whatever angle the profile stands at around the path."""
from __future__ import annotations

import math
from collections import Counter

import pytest
from PySide6.QtGui import QVector3D as V

from core.mesh import Mesh
from core.sweep import orient_closed_path, sweep_profile

N = 24


def _sweep(profile, path_r=1.0):
    m = Mesh()
    face = m.add_face(profile)
    path = [V(path_r * math.cos(2 * math.pi * k / N),
              path_r * math.sin(2 * math.pi * k / N), 0) for k in range(N)]
    assert sweep_profile(m, face, orient_closed_path(path, face), True)
    return m


def _circle(r, phi_deg):
    u = V(math.cos(math.radians(phi_deg)), math.sin(math.radians(phi_deg)), 0)
    return [u * (r * math.cos(2 * math.pi * k / N))
            + V(0, 0, r * math.sin(2 * math.pi * k / N)) for k in range(N)]


@pytest.mark.parametrize("phi", [0.0, 7.5, 33.0])     # on a vertex, between, anywhere
@pytest.mark.parametrize("r, path_r", [(1.0, 1.0), (0.5, 1.0), (1.0, 0.5)])
def test_circle_around_a_circle_is_a_closed_sphere(phi, r, path_r):
    m = _sweep(_circle(r, phi), path_r)
    radii = [p.length() for f in m.faces for p in f.vertices]
    assert max(abs(x - r) for x in radii) < 1e-5       # every vertex on it
    assert Counter(len(e.faces) for e in m.edges) == {2: len(m.edges)}
    # A 24 × 12 polyhedral sphere: the half profile, once (not twice).
    assert len(m.faces) == N * (N // 2)
    area = sum(f.area() for f in m.faces)
    assert 0.98 * 4 * math.pi * r * r < area < 4 * math.pi * r * r


def test_a_profile_off_the_axis_keeps_the_mitre_sweep():
    """A torus section beside the axis is still a revolution — and a profile
    whose plane does not hold the axis is left to the mitre construction."""
    ring = [V(2 + 0.3 * math.cos(2 * math.pi * k / N), 0,
              0.3 * math.sin(2 * math.pi * k / N)) for k in range(N)]
    m = _sweep(ring, 2.0)
    assert Counter(len(e.faces) for e in m.edges) == {2: len(m.edges)}
    for f in m.faces:
        for p in f.vertices:
            rho = math.hypot(p.x(), p.y())
            assert abs(math.hypot(rho - 2, p.z()) - 0.3) < 1e-4


@pytest.mark.parametrize("phi", [0.0, 7.5])
def test_the_sphere_is_one_smooth_surface(phi):
    """Path circle of the same radius = the equator, profile = a meridian:
    their edges existed before the sweep but lie INSIDE the new surface, so
    they soften with the rest — one surface to paint, not four."""
    m = Mesh()
    path = [V(math.cos(2 * math.pi * k / N), math.sin(2 * math.pi * k / N), 0)
            for k in range(N)]
    for i in range(N):
        m.add_edge(path[i], path[(i + 1) % N])
    face = m.add_face(_circle(1.0, phi))
    assert sweep_profile(m, face, orient_closed_path(path, face), True)
    assert [e for e in m.edges if not e.soft] == []
