# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""SketchUp's right-click ▸ Select: grow a selection by what it touches or
by what it shares (issue #106, @pacaeiro).

Every function answers from the CURRENT editing context — the loose mesh the
scene exposes (``scene.faces``/``scene.edges``, which inside a group are that
group's), plus the groups at that level — never from the model around it,
the same rule Select All follows.
"""
from __future__ import annotations

from core.layers import layer_of
from core.mesh import Edge, Face


def all_connected(entities) -> list:
    """Everything physically connected to *entities* — the whole solid —
    walked through shared vertices (SketchUp's triple click)."""
    seeds = []
    for e in entities:
        if isinstance(e, Face):
            seeds.extend(e.loop)
            for hole in e.hole_loops:
                seeds.extend(hole)
        elif isinstance(e, Edge):
            seeds.extend((e.v0, e.v1))
    seen_v = set(seeds)
    edges: set = set()
    faces: set = set()
    stack = list(seen_v)
    while stack:
        v = stack.pop()
        for e in v.edges:
            if e in edges:
                continue
            edges.add(e)
            for f in e.faces:
                if f in faces:
                    continue
                faces.add(f)
                for lp in (f.loop, *f.hole_loops):
                    for w in lp:
                        if w not in seen_v:
                            seen_v.add(w)
                            stack.append(w)
            w = e.other(v)
            if w not in seen_v:
                seen_v.add(w)
                stack.append(w)
    return list(edges) + list(faces)


def bounding_edges(entities) -> list:
    """The edges that outline the selected faces: those with exactly ONE of
    the selected faces on them (SketchUp's Select ▸ Bounding Edges)."""
    faces = {e for e in entities if isinstance(e, Face)}
    out = []
    seen = set()
    for f in faces:
        for e in _face_edges(f):
            if e in seen:
                continue
            seen.add(e)
            if sum(1 for g in e.faces if g in faces) == 1:
                out.append(e)
    return out


def _face_edges(face):
    """The edges running round *face*'s outer and hole loops."""
    for lp in (face.loop, *face.hole_loops):
        n = len(lp)
        for i in range(n):
            a, b = lp[i], lp[(i + 1) % n]
            for e in a.edges:
                if e.other(a) is b and face in e.faces:
                    yield e
                    break


def material_key(face):
    """What «the same material» means for a face: its named material when
    it has one, otherwise its colour and texture image. ``None`` = unpainted
    (the default material, which is a material too — SketchUp selects all
    the unpainted faces from an unpainted one)."""
    attrs = getattr(face, "attrs", None) or {}
    if attrs.get("mat"):
        return ("mat", attrs["mat"])
    color = attrs.get("color")
    tex = attrs.get("texture")
    path = tex.get("path") if isinstance(tex, dict) else None
    if color is None and path is None:
        return None
    col = tuple(round(float(c), 4) for c in color) if color else None
    return ("paint", col, path)


def same_material(scene, entities) -> list:
    """Every face in the current context painted like one of the selected
    faces. Groups are matched by their own material when they carry one."""
    keys = {material_key(e) for e in entities if isinstance(e, Face)}
    out = [f for f in scene.faces if material_key(f) in keys] if keys else []
    group_mats = [getattr(g, "material", None) for g in entities
                  if _is_group(g) and getattr(g, "material", None)]
    if group_mats:
        out += [g for g in _context_groups(scene)
                if getattr(g, "material", None) in group_mats]
    return out


def same_layer(scene, entities) -> list:
    """Every edge, face and group in the current context on one of the
    selected entities' layers (SketchUp's Select ▸ All on Same Tag)."""
    layers = {layer_of(e) for e in entities
              if isinstance(e, (Face, Edge)) or _is_group(e)}
    if not layers:
        return []
    pool = list(scene.edges) + list(scene.faces) + list(_context_groups(scene))
    return [e for e in pool if layer_of(e) in layers]


def _is_group(e) -> bool:
    from core.group import Group
    return isinstance(e, Group)


def _context_groups(scene):
    ctx = getattr(scene, "edit_group", None)
    return scene.groups if ctx is None else (getattr(ctx, "children", None)
                                             or [])
