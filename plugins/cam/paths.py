# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""What in the drawing becomes a machining path.

A modeler stores a drawn rectangle as four edges (and a face). A CAM user
sees ONE closed path: the outline a profile follows or a pocket clears.
This module reads the whole model the way a CAM user reads it, so the CAM
tab can list the paths as soon as it opens and an operation can be put on
a path, not on a pile of edges:

- every face lying on (or parallel to) the machining plane gives its outer
  loop and each of its holes as a closed path — a rectangle, a board's top
  outline, a hole through it;
- loose edges (lines and arcs with no face) lying on such a plane are
  chained end to end into closed paths (they meet again) or open paths
  (engraving, a slot's centre line);
- a path seen twice in plan — the top and the bottom outline of a board, a
  through hole's two rims — is kept once, at its highest level, since the
  cutter comes from above.

Each path remembers the model edges it is made of, so clicking any one of
them in the viewport chooses the whole path. Like :mod:`.extract`, this is
where world metres become plane millimetres; the result feeds
:func:`.extract` 's :class:`~.extract.Extraction` and so every operation
builder unchanged.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .engine import geometry as geo
from .extract import (PARALLEL_DEG, PLANE_TOL_MM, Extraction, Loop, _dot, _mapper,
                      chain_vertices, circle_of, frame_for, newell, solid_depth)

#: Two loops in plan are the same path when their centroids and areas agree
#: this closely (mm, and fraction of the area).
SAME_PATH_MM = 0.05
SAME_AREA = 0.005


@dataclass
class CamPath:
    """One path on the machining plane, in millimetres."""
    points: list                       # [(u, v)]
    closed: bool
    w: float = 0.0                     # height (0 = the plane, below < 0)
    circle: tuple | None = None        # (cu, cv, diameter) when round
    edges: set = field(default_factory=set)     # id() of the model edges
    faces: list = field(default_factory=list)   # model faces it bounds (loose mesh)
    group_uid: str | None = None
    hole: bool = False                 # a face's hole loop
    area: float = 0.0                  # mm², closed paths (set by the reader)
    centroid: tuple = (0.0, 0.0)

    @property
    def length(self) -> float:
        pts = self.points + (self.points[:1] if self.closed else [])
        return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))

    @property
    def extent(self) -> tuple:
        us = [p[0] for p in self.points]
        vs = [p[1] for p in self.points]
        return max(us) - min(us), max(vs) - min(vs)


def plan_frame(scene) -> "object | None":
    """The default machining frame: plan view (world XY), on the highest
    point of the geometry that lies flat — a job machined from above."""
    top = None
    for mesh, m, _uid in _meshes(scene):
        f = _mapper(m)
        for face in mesh.faces:
            pts = [f(p) for p in face.vertices]
            if len(pts) >= 3 and abs(newell(pts)[2]) >= _COS:
                z = max(p[2] for p in pts)
                if top is None or z > top[2]:
                    top = pts[0][:2] + (z,)
        for e in mesh.edges:
            if not e.faces:
                for q in (f(e.v0.position), f(e.v1.position)):
                    if top is None or q[2] > top[2]:
                        top = q
    if top is None:
        return None
    return frame_for((0.0, 0.0, 1.0), top)


_COS = math.cos(math.radians(PARALLEL_DEG))


def _meshes(scene):
    """``(mesh, world matrix or None, group uid)`` for the loose geometry
    and every visible placement."""
    loose = getattr(scene, "loose_mesh", None)
    if loose is not None:
        yield loose, None, None
    placements = getattr(scene, "placements", None)
    if placements is not None:
        for g, m in placements():
            yield g.mesh, m, getattr(g, "uid", None)


def _loop_edges(loop) -> set:
    """``id()`` of the edges joining a face loop's consecutive vertices."""
    out = set()
    n = len(loop)
    for i in range(n):
        a, b = loop[i], loop[(i + 1) % n]
        for e in a.edges:
            if e.other(a) is b:
                out.add(id(e))
                break
    return out


def face_edges(face) -> set:
    """``id()`` of a face's edges, outline and holes (a selected face
    chooses the paths it is bounded by)."""
    out = _loop_edges(face.loop)
    for h in face.hole_loops:
        out |= _loop_edges(h)
    return out


def find_paths(scene, frame) -> list:
    """Every path of the model on ``frame``'s plane (or parallel to it)."""
    found: list = []
    for mesh, m, uid in _meshes(scene):
        f = _mapper(m)
        for face in mesh.faces:
            if getattr(face, "interior", False):
                continue
            outer = [f(p) for p in face.vertices]
            if len(outer) < 3 or abs(_dot(newell(outer), frame.n)) < _COS:
                continue
            loops = [(face.loop, False)] + [(h, True) for h in face.hole_loops]
            for vloop, is_hole in loops:
                if len(vloop) < 3:
                    continue
                path = _closed(frame, [f(v.position) for v in vloop], _loop_edges(vloop), uid)
                if path is None:
                    continue
                path.hole = is_hole
                if m is None:
                    path.faces = [face]
                found.append(path)
        wires = [e for e in mesh.edges if not e.faces]
        if wires:
            closed, open_ = chain_vertices(wires)
            for pts_v, chain in closed:
                if len(pts_v) >= 3:
                    path = _closed(frame, [f(v.position) for v in pts_v],
                                   {id(e) for e in chain}, uid)
                    if path is not None:
                        found.append(path)
            for pts_v, chain in open_:
                uvw = [frame.to_plane(f(v.position)) for v in pts_v]
                ws = [p[2] for p in uvw]
                if max(ws) - min(ws) > PLANE_TOL_MM:
                    continue
                found.append(CamPath([(p[0], p[1]) for p in uvw], False,
                                     sum(ws) / len(ws), None, {id(e) for e in chain}, [], uid))
    return _dedupe(found)


def _closed(frame, pts, ids, uid):
    uvw = [frame.to_plane(p) for p in pts]
    ws = [p[2] for p in uvw]
    if max(ws) - min(ws) > PLANE_TOL_MM:
        return None
    uv = [(p[0], p[1]) for p in uvw]
    if abs(geo.signed_area(uv)) < 1e-6:
        return None
    return CamPath(uv, True, sum(ws) / len(ws), circle_of(uv), set(ids), [], uid)


def _centroid(pts):
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def _same(a: CamPath, b: CamPath) -> bool:
    if a.closed != b.closed or not a.closed:
        return False
    return (math.dist(a.centroid, b.centroid) <= SAME_PATH_MM
            and abs(a.area - b.area) <= SAME_AREA * max(a.area, b.area, 1e-9))


def _dedupe(paths: list) -> list:
    """One path per outline in plan: the highest copy, which also takes the
    lower copies' edges (clicking a board's bottom edge still picks it).
    Twins are looked for in a grid of centroid cells (this and the eight
    around it), not against every path: a model of thousands of parts
    stays a fraction of a second."""
    for p in paths:
        p.area = abs(geo.signed_area(p.points)) if p.closed else 0.0
        p.centroid = _centroid(p.points)
    cell = SAME_PATH_MM * 4
    grid: dict = {}
    kept: list = []
    for p in sorted(paths, key=lambda p: -p.w):
        twin = None
        if p.closed:
            ci, cj = int(p.centroid[0] // cell), int(p.centroid[1] // cell)
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    twin = next((k for k in grid.get((ci + di, cj + dj), ()) if _same(k, p)),
                                None)
                    if twin is not None:
                        break
                if twin is not None:
                    break
        if twin is None:
            kept.append(p)
            if p.closed:
                grid.setdefault((ci, cj), []).append(p)
        else:
            twin.edges |= p.edges
            if not twin.faces and p.faces:
                twin.faces = list(p.faces)
    # Largest first: outlines before the holes and details inside them.
    kept.sort(key=lambda p: (not p.closed, -p.area if p.closed else -p.length))
    return kept


def paths_for_edges(paths: list, edge_ids: set) -> list:
    """Indices of the paths any of ``edge_ids`` belongs to."""
    return [i for i, p in enumerate(paths) if p.edges & edge_ids]


def extraction(paths: list, frame) -> Extraction:
    """The chosen paths as an :class:`~.extract.Extraction`: closed paths
    nested by containment (a path inside another is its hole or island),
    open paths as engraving paths. The board's depth is measured from the
    faces the paths bound, when they have any."""
    ex = Extraction(frame, kind="edges")
    closed = [p for p in paths if p.closed]
    opened = [p for p in paths if not p.closed]
    closed.sort(key=lambda p: -abs(geo.signed_area(p.points)))

    def loop(p):
        lp = Loop(list(p.points), p.w)
        lp.circle = p.circle
        return lp

    parent: dict = {}
    for i, p in enumerate(closed):
        c = p.points[0]                           # on the path, so inside its container
        for j in range(i - 1, -1, -1):          # the smallest container
            if geo.contains(c, closed[j].points):
                parent[i] = j
                break

    def depth(i):
        d = 0
        while i in parent:
            i = parent[i]
            d += 1
        return d

    for i, p in enumerate(closed):
        if depth(i) % 2 == 0:
            holes = [loop(closed[k]) for k in parent if parent[k] == i]
            ex.regions.append((loop(p), holes))
    for p in opened:
        ex.paths.append(list(p.points))
    faces = [f for p in paths for f in p.faces]
    if faces:
        ex.thickness = solid_depth(faces, frame)
    uids = {p.group_uid for p in paths if p.group_uid}
    if len(uids) == 1:
        ex.group_uid = uids.pop()
    return ex
