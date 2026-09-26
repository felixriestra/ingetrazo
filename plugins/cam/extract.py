# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""From IngeTrazo's selection to machining-plane geometry.

The ONLY place world coordinates (metres, float32 ``QVector3D``) become the
engine's (millimetres, float64). Three kinds of selection are understood:

1. **Faces** — each face is a region: its outer loop and its holes, on
   the face's plane. Several faces must be parallel (they may sit at
   different heights: a pocket floor below the top).
2. **Edges** — chained into loops (closed chains) and paths (open chains,
   engraving). They must be coplanar.
3. **A part** (a group, e.g. from the Parts tray) — the board's largest
   face gives the machining plane; its outline, its holes and their kind
   (through, blind pocket, round hole) come with it, ready for
   :func:`suggest_operations`.

The plane frame is chosen so a horizontal face is always machined from
above in plan view (u = world X, v = world Y), and every coordinate is
re-based on a point of the selection before it is scaled to millimetres —
float32 positions far from the origin (a georeferenced model) would
otherwise lose sub-millimetre precision in the subtraction.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .engine import geometry as geo
from .engine.issues import CamError
from .state import Frame

#: Heights within this (mm) are the same plane.
PLANE_TOL_MM = 0.05
#: Normals within this angle are parallel.
PARALLEL_DEG = 0.5


@dataclass
class Loop:
    """A closed loop on the machining plane (mm). ``w`` is its height
    (0 = the plane, negative = below it)."""
    points: list
    w: float = 0.0
    circle: tuple | None = None          # (cu, cv, diameter) when round
    through: bool | None = None          # holes of a part: through the board?
    depth: float | None = None           # holes of a part: blind depth (mm)


@dataclass
class Extraction:
    frame: Frame
    regions: list = field(default_factory=list)      # [(outer Loop, [hole Loops])]
    loops: list = field(default_factory=list)        # closed edge chains
    paths: list = field(default_factory=list)        # open edge chains [[(u, v)]]
    thickness: float | None = None                   # mm, when measurable
    group_uid: str | None = None
    kind: str = "faces"                              # faces|edges|part

    def all_points(self):
        for outer, holes in self.regions:
            yield from outer.points
            for h in holes:
                yield from h.points
        for lp in self.loops:
            yield from lp.points
        for p in self.paths:
            yield from p


# ---- small vector helpers (tuples, float64) --------------------------------

def _v(q) -> tuple:
    return (float(q.x()), float(q.y()), float(q.z()))


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(a):
    n = math.sqrt(_dot(a, a))
    return (a[0] / n, a[1] / n, a[2] / n) if n > 1e-15 else (0.0, 0.0, 0.0)


def newell(points) -> tuple:
    """Unit normal of a (roughly planar) loop, by Newell's method; its
    direction follows the loop's winding."""
    nx = ny = nz = 0.0
    n = len(points)
    for i in range(n):
        a, b = points[i], points[(i + 1) % n]
        nx += (a[1] - b[1]) * (a[2] + b[2])
        ny += (a[2] - b[2]) * (a[0] + b[0])
        nz += (a[0] - b[0]) * (a[1] + b[1])
    return _unit((nx, ny, nz))


def frame_for(normal, origin) -> Frame:
    """A machining frame on the plane through ``origin`` with ``normal``.
    A (nearly) horizontal plane is machined from above in plan view."""
    n = _unit(normal)
    if abs(n[2]) > 0.99:
        n = (0.0, 0.0, 1.0)
        u = (1.0, 0.0, 0.0)
    else:
        u = _unit(_cross((0.0, 0.0, 1.0), n))
    v = _cross(n, u)
    return Frame(tuple(origin), u, v, n)


def circle_of(points, rel_tol: float = 0.01):
    """``(cu, cv, diameter)`` when the loop's points all sit at one radius
    from their centroid (a drilled hole drawn as a polygon), else None."""
    if len(points) < 8:
        return None
    cu = sum(p[0] for p in points) / len(points)
    cv = sum(p[1] for p in points) / len(points)
    radii = [math.hypot(p[0] - cu, p[1] - cv) for p in points]
    mean = sum(radii) / len(radii)
    if mean <= 0 or (max(radii) - min(radii)) > rel_tol * mean:
        return None
    return (cu, cv, 2 * mean)


# ---- world geometry out of the scene -------------------------------------------

def _mapper(m):
    if m is None:
        return _v
    return lambda q: _v(m.map(q))


def _face_world(face, m=None):
    """``(outer, holes)`` of a face as world tuples (metres)."""
    f = _mapper(m)
    return [f(p) for p in face.vertices], [[f(p) for p in h] for h in face.holes]


def _project(frame: Frame, pts):
    """World points → plane ``(u, v)`` and their mean height ``w``."""
    uvw = [frame.to_plane(p) for p in pts]
    ws = [p[2] for p in uvw]
    if max(ws) - min(ws) > PLANE_TOL_MM:
        raise CamError("not_planar", spread=round(max(ws) - min(ws), 3))
    return [(p[0], p[1]) for p in uvw], sum(ws) / len(ws)


def _check_parallel(frame: Frame, normal) -> None:
    if abs(_dot(_unit(normal), frame.n)) < math.cos(math.radians(PARALLEL_DEG)):
        raise CamError("not_planar", spread=0.0)


def _loop(frame, pts) -> Loop:
    uv, w = _project(frame, pts)
    lp = Loop(uv, w)
    lp.circle = circle_of(uv)
    return lp


def extract_selection(scene, frame: Frame | None = None) -> Extraction:
    """What is selected, on ``frame`` (or on a frame chosen from the
    selection when the job has none yet)."""
    from core.group import Group
    from core.mesh import Edge, Face
    sel = list(getattr(scene, "selection", ()) or ())
    groups = [e for e in sel if isinstance(e, Group)]
    faces = [e for e in sel if isinstance(e, Face)]
    edges = [e for e in sel if isinstance(e, Edge)]
    if groups and not faces and not edges:
        if len(groups) != 1:
            raise CamError("empty_selection")
        return extract_part(groups[0], frame)
    if faces:
        return extract_faces(faces, frame)
    if edges:
        return extract_edges(edges, frame)
    raise CamError("empty_selection")


def extract_faces(faces, frame: Frame | None = None, m=None) -> Extraction:
    worlds = [_face_world(f, m) for f in faces]
    worlds = [w for w in worlds if len(w[0]) >= 3]
    if not worlds:
        raise CamError("empty_selection")
    if frame is None:
        big = max(worlds, key=lambda w: abs(_area3(w[0])))
        n = newell(big[0])
        frame = frame_for(n, big[0][0])
    ex = Extraction(frame, kind="faces")
    for outer, holes in worlds:
        _check_parallel(frame, newell(outer))
        ex.regions.append((_loop(frame, outer), [_loop(frame, h) for h in holes if len(h) >= 3]))
    if m is None:
        ex.thickness = solid_depth(faces, frame)
    return ex


def solid_depth(faces, frame):
    """How far the solid the ``faces`` belong to reaches below their plane,
    in mm — a board's thickness when its top face is selected — or None
    for a lone flat face. Everything connected to the faces through edges
    counts; the faces' mesh is already in world coordinates (the loose
    mesh, or an open group)."""
    seen, todo = set(), []
    for f in faces:
        for v in f.loop:
            if id(v) not in seen:
                seen.add(id(v))
                todo.append(v)
    lowest = 0.0
    top = max(frame.to_plane(_v(v.position))[2] for f in faces for v in f.loop)
    while todo:
        v = todo.pop()
        w = frame.to_plane(_v(v.position))[2]
        lowest = min(lowest, w - top)
        for e in v.edges:
            o = e.other(v)
            if id(o) not in seen:
                seen.add(id(o))
                todo.append(o)
    depth = -lowest
    return depth if depth > 0.01 else None


def _area3(pts) -> float:
    n = newell(pts)
    s = (0.0, 0.0, 0.0)
    for i in range(len(pts)):
        c = _cross(pts[i], pts[(i + 1) % len(pts)])
        s = (s[0] + c[0], s[1] + c[1], s[2] + c[2])
    return 0.5 * _dot(n, s)


def chain_edges(edges) -> tuple:
    """Chain edges by shared vertices: ``(closed, open)`` lists of vertex
    position sequences (world tuples). Branches (a vertex with three
    selected edges) end a chain there."""
    closed, open_ = chain_vertices(edges)
    return ([[_v(x.position) for x in seq] for seq, _e in closed],
            [[_v(x.position) for x in seq] for seq, _e in open_])


def chain_vertices(edges) -> tuple:
    """:func:`chain_edges` on the mesh objects: ``(closed, open)`` lists of
    ``(vertices, edges)`` — a closed chain's vertices do not repeat the
    first one."""
    adj: dict = {}
    for e in edges:
        adj.setdefault(e.v0, []).append(e)
        adj.setdefault(e.v1, []).append(e)
    used: set = set()
    closed, open_ = [], []

    def walk(start_v, first_edge):
        seq, chain = [start_v], []
        e, v = first_edge, start_v
        while e is not None and e not in used:
            used.add(e)
            chain.append(e)
            v = e.other(v)
            seq.append(v)
            nxt = [x for x in adj.get(v, ()) if x not in used]
            e = nxt[0] if len(adj.get(v, ())) == 2 and nxt else None
        return seq, chain

    # Open chains start at dead ends (or branch points).
    for v, es in adj.items():
        if len(es) != 2:
            for e in es:
                if e not in used:
                    open_.append(walk(v, e))
    for e in edges:
        if e not in used:
            seq, chain = walk(e.v0, e)
            if seq[0] is seq[-1]:
                closed.append((seq[:-1], chain))
            else:
                open_.append((seq, chain))
    return closed, open_


def extract_edges(edges, frame: Frame | None = None) -> Extraction:
    closed, open_ = chain_edges(edges)
    if frame is None:
        pts = [p for c in closed + open_ for p in c]
        n = None
        if closed:
            n = newell(max(closed, key=lambda c: abs(_area3(c)) if len(c) >= 3 else 0))
        if n is None or _dot(n, n) < 0.5:
            n = _plane_normal(pts)
        frame = frame_for(n, pts[0])
    ex = Extraction(frame, kind="edges")
    for c in closed:
        if len(c) >= 3:
            ex.loops.append(_loop(frame, c))
    for p in open_:
        uv, _w = _project(frame, p)
        ex.paths.append(uv)
    return ex


def _plane_normal(pts) -> tuple:
    """A plane through non-collinear points of ``pts``; +Z when all lie on
    a horizontal line or fewer than three are distinct."""
    if len(pts) >= 3:
        a = pts[0]
        b = max(pts, key=lambda p: _dot(_sub(p, a), _sub(p, a)))
        ab = _sub(b, a)
        best, n = 0.0, None
        for p in pts:
            c = _cross(ab, _sub(p, a))
            size = _dot(c, c)
            if size > best:
                best, n = size, c
        if n is not None and best > 1e-18:
            n = _unit(n)
            return n if n[2] >= 0 else (-n[0], -n[1], -n[2])
    return (0.0, 0.0, 1.0)


def extract_part(group, frame: Frame | None = None) -> Extraction:
    """A board: the machining plane is its largest face, turned to face up
    (or out, for a board standing on edge); the outline and holes of the
    top face become regions and every hole is classified."""
    from core.group import iter_placements
    faces = []
    for g, m in iter_placements(group):
        for f in g.mesh.faces:
            outer, holes = _face_world(f, m)
            if len(outer) >= 3:
                faces.append((outer, holes))
    if not faces:
        raise CamError("empty_selection")
    if frame is None:
        big = max(faces, key=lambda w: abs(_area3(w[0])))
        n = newell(big[0])
        if abs(n[2]) > 0.99:
            n = (0.0, 0.0, 1.0)
        # Top = the far side along n; the frame sits on it.
        top_pt = max((p for f, _h in faces for p in f), key=lambda p: _dot(p, n))
        frame = frame_for(n, top_pt)
    ws = [frame.to_plane(p)[2] for f, _h in faces for p in f]
    top, bottom = max(ws), min(ws)
    ex = Extraction(frame, kind="part", thickness=top - bottom,
                    group_uid=getattr(group, "uid", None))
    up, down, floors = [], [], []
    for outer, holes in faces:
        n = newell(outer)
        d = _dot(n, frame.n)
        if abs(d) < math.cos(math.radians(PARALLEL_DEG)):
            continue
        try:
            lp = _loop(frame, outer)
            hs = [_loop(frame, h) for h in holes if len(h) >= 3]
        except CamError:
            continue
        if d > 0 and abs(lp.w - top) <= PLANE_TOL_MM:
            up.append((lp, hs))
        elif d < 0 and abs(lp.w - bottom) <= PLANE_TOL_MM:
            down.append((lp, hs))
        elif d > 0:
            floors.append(lp)
    if not up:
        raise CamError("empty_selection")
    bottom_holes = [h for _o, hs in down for h in hs]
    for outer, holes in up:
        for h in holes:
            h.through = any(_same_loop(h, b) for b in bottom_holes)
            if not h.through:
                inside = [f for f in floors if geo.contains(_centroid(f.points), h.points)]
                if inside:
                    h.depth = top - min(f.w for f in inside)
                else:
                    h.through = None
        ex.regions.append((outer, holes))
    return ex


def _centroid(pts):
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def _same_loop(a: Loop, b: Loop) -> bool:
    ca, cb = _centroid(a.points), _centroid(b.points)
    area_a, area_b = abs(geo.signed_area(a.points)), abs(geo.signed_area(b.points))
    return (math.dist(ca, cb) <= 0.1
            and abs(area_a - area_b) <= 0.01 * max(area_a, area_b, 1e-9))
