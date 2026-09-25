# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Move tool: drag geometry (and everything connected to it) to a new spot.

UX (SketchUp-like):
- If there's a selection, Move acts on it; otherwise the first click grabs the
  edge / face under the cursor.
- First click sets the grab point (a snapped handle). Move the mouse — snap,
  axis lock (arrow keys) and the VCB all work, same as drawing — and the real
  geometry deforms live as you drag (faces tilt, walls stretch).
- Second click drops at the current offset. Typing a length + Enter moves
  exactly that far along the current direction; ``X;Y;Z`` + Enter is a 3D delta.
- Tapping Ctrl toggles COPY mode (SketchUp, issue #20): the original stays
  put and a translated copy is created (a component instance copies as a
  sibling instance); the copy's wireframe previews at the cursor. Right after
  a copy, typing ``3x`` (or ``3*``, ``*3``) makes an external array — three
  copies at multiples of the distance — and ``/3`` (or ``3/``) an internal
  one — three copies dividing the distance. Retyping re-lays the array.
- Esc (or switching tools) cancels and snaps the geometry back.

Move shifts *positions*: every point coincident with a grabbed vertex moves with
it, so connected faces deform instead of tearing — that's what lets you raise a
ridge edge into a gable roof.

The live preview mutates the scene directly (no history entry); the move only
lands on the undo stack on commit, and is reverted on cancel — so the undo
history stays clean while you still see the deformation as it happens.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QMatrix4x4, QVector3D

from core.group import Group, copy_group, transformed_attrs
from core.i18n import tr
from core.mesh import Edge, Face, Mesh
from core.dimension import Dimension
from core.history import (AddEdgeCommand, AddFaceCommand, CompoundCommand,
                          InsertGroupCommand, MoveGroupCommand,
                          MoveDimensionsCommand, MoveTextLabelsCommand,
                          MoveVerticesCommand)
from core.textlabel import TextLabel
from core.topology import _key
from tools.base import Tool, ToolContext
from core.units import fmt_len



def _dedup(positions: list[QVector3D]) -> list[QVector3D]:
    seen = set()
    out = []
    for p in positions:
        k = _key(p)
        if k not in seen:
            seen.add(k)
            out.append(QVector3D(p))
    return out


def gather_targets(ctx: ToolContext):
    """What a transform tool acts on: ``(groups, positions)``. EVERY selected
    (or hovered) group transforms as a unit — a mixed selection carries the
    groups AND the loose geometry together (rotating a whole drawing used to
    grab only the first group). Positions are the unique loose coordinates
    from the selection or the entity under the cursor. Shared by Move,
    Rotate and Scale."""
    viewport = ctx.viewport
    entities = list(viewport.scene.selection)
    if not entities:
        group = viewport.pick_group(ctx.screen.x(), ctx.screen.y())
        if group is not None:
            return [group], []
        edge = viewport.pick_edge(ctx.screen.x(), ctx.screen.y())
        if edge is not None:
            entities = [edge]
        else:
            face = viewport.pick_face(ctx.screen.x(), ctx.screen.y())
            if face is not None:
                entities = [face]
    groups = [ent for ent in entities if isinstance(ent, Group)]
    positions: list[QVector3D] = []
    for ent in entities:
        if isinstance(ent, Edge):
            positions.extend([ent.a, ent.b])
        elif isinstance(ent, Face):
            positions.extend(ent.vertices)
    return groups, _dedup(positions)


def gather_dimensions(ctx: ToolContext, groups, verts) -> dict:
    """Dimensions Move acts on, each with how: the selected ones, or (with
    nothing selected) the one under the cursor. ``"line"`` — both ends
    anchored to geometry: the dimension LINE slides and the extension
    lines stretch from their vertices (the user of DriveMeca's video:
    «no se puede mover después de colocarlo»); ``"rigid"`` — a free end
    travels with the delta. A dimension whose anchors are themselves
    being moved in this very drag is left alone: it rides along with its
    vertices, and shifting its line too would move it twice."""
    viewport = ctx.viewport
    dims = [d for d in viewport.scene.selection if isinstance(d, Dimension)]
    if not dims and not viewport.scene.selection:
        pick = getattr(viewport, "pick_dimension", None)
        d = pick(ctx.screen.x(), ctx.screen.y()) if pick else None
        if d is not None:
            dims = [d]
    moving_verts = {id(v) for v in verts}
    moving_groups = {id(g) for g in groups}
    out: dict = {}
    for d in dims:
        anchors = [an for an in (d.anchor_a, d.anchor_b) if an is not None]
        riding = [an for an in anchors
                  if id(an.vertex) in moving_verts
                  or any(id(g) in moving_groups for g in an.chain)]
        if len(anchors) == 2 and len(riding) == 2:
            continue                          # follows its vertices
        out[d] = "line" if len(anchors) == 2 else "rigid"
    return out


def gather_labels(ctx: ToolContext) -> list[TextLabel]:
    """Leader texts Move acts on: the selected ones, or (with nothing
    selected) the one whose text block is under the cursor — the glyphs
    overdraw all geometry, so that grab is exclusive. Their label end
    translates while the anchor stays pinned — SketchUp's Move-on-text
    behaviour."""
    viewport = ctx.viewport
    labels = [t for t in viewport.scene.selection if isinstance(t, TextLabel)]
    if not labels and not viewport.scene.selection:
        pick = getattr(viewport, "pick_text_label", None)
        lab = (pick(ctx.screen.x(), ctx.screen.y(), rect_only=True)
               if pick else None)
        if lab is not None:
            labels = [lab]
    return labels


def gather_images(ctx: ToolContext):
    """Reference images a transform tool acts on: the selected ones, or (with
    nothing selected) the picture under the cursor. Locked images are already
    filtered out by ``pick_image_plane`` — an aligned scan should not jump
    because the user grabbed near it. Shared by Move, Rotate and Scale."""
    from core.image_plane import ImagePlane
    viewport = ctx.viewport
    images = [e for e in viewport.scene.selection if isinstance(e, ImagePlane)]
    if not images and not viewport.scene.selection:
        pick = getattr(viewport, "pick_image_plane", None)
        hit = pick(ctx.screen.x(), ctx.screen.y()) if pick else None
        if hit is not None:
            images = [hit]
    return images


class MoveTool(Tool):
    name = "Move"
    shortcut = "M"
    vcb_label = "Distance"

    # Drag on a camera-facing vertical plane (not the ground) so pulling the
    # mouse up raises geometry — what lets you lift a roof ridge straight up.
    prefers_vertical_drag = True
    # Magnetically lock to the nearest world axis within this angle, projecting
    # onto the axis line. Keeps a move rigid and axis-aligned (a 3 m ridge stays
    # 3 m and level) without holding Shift; arrow-key locks still override it.
    magnetic_axis_deg = 15.0
    #: …and the two axes that plane can NEVER hold. The drag plane above is
    #: vertical and faces the camera, so it contains Z and one horizontal
    #: direction — the camera's own, which is X or Y only when the view is
    #: square to the model. From any oblique view the world detector saw
    #: X and Y at the camera's yaw and never fired: «MOVE/COPY cannot
    #: detect X, Y soft magnetic snaps, but it aligns well with the Z axis»
    #: (@pacaeiro, issue #42). The screen detector the Line tool got for
    #: issue #31 finds them by where the cursor points and projects onto
    #: the world axis, so the move goes along X or Y exactly.
    screen_axis_px = 9.0
    accepts_array = True  # VCB "3x" / "/3" after a copy (SketchUp arrays)

    def __init__(self) -> None:
        # ``start_point`` drives the viewport's snap / axis-lock machinery, same
        # as the drawing tools. ``grab`` is the handle the delta is measured
        # from (identical to start_point, kept separate for clarity).
        self.start_point: QVector3D | None = None
        self.grab: QVector3D | None = None
        self.hover_point: QVector3D | None = None
        self.chain_first_point: QVector3D | None = None  # silence close-snap
        self._positions: list[QVector3D] = []  # unique positions to translate
        self._verts: list = []                 # resolved vertex objects (identity)
        self._groups: list[Group] = []         # whole groups being moved
        self._splanes: list = []               # section planes being moved
        self._images: list = []                # reference images being moved
        self._labels: list[TextLabel] = []     # leader texts whose label moves
        self._dims: dict = {}                  # dimension → "line" | "rigid"
        self._preview_delta = QVector3D(0.0, 0.0, 0.0)  # currently applied live
        self._copy = False                     # Ctrl: move a COPY
        self._sel_faces: list = []             # loose geometry copy mode duplicates
        self._sel_edges: list = []
        self._base_segments: list = []         # wireframe for the copy preview
        self._last: dict | None = None         # the copy just made, for "3x" / "/3"

    # ---- Lifecycle ----------------------------------------------------------
    def on_activate(self, viewport) -> None:
        self._reset()
        self._copy = False
        self._last = None

    def on_deactivate(self, viewport) -> None:
        self._end_freeze(viewport)
        self._revert_preview(viewport)
        self._reset()
        self._copy = False
        self._last = None

    # ---- Keyboard -----------------------------------------------------------
    def on_key(self, viewport, key: int, modifiers) -> bool:
        # Ctrl toggles copy mode (SketchUp: move a copy, the original stays).
        if key == Qt.Key_Control:
            self._copy = not self._copy
            if self._copy:
                # The original stops following the cursor; the copy's
                # wireframe takes over in rubber_band_lines.
                self._revert_preview(viewport)
                park = getattr(viewport, "set_groups_preview_offset", None)
                if getattr(self, "_vp_preview", False) and park is not None:
                    park(QVector3D(0.0, 0.0, 0.0))
                viewport.flash_status(tr("Move a copy: on"))
            else:
                if self.grab is not None and self.hover_point is not None:
                    self._apply_preview(viewport, self.hover_point - self.grab)
                viewport.flash_status(tr("Move a copy: off"))
            viewport.update()
            return True
        return False

    # ---- Spatial input ------------------------------------------------------
    def on_click(self, ctx: ToolContext) -> None:
        viewport = ctx.viewport
        if self.start_point is None:
            groups, positions = self._gather(ctx)
            labels = gather_labels(ctx)
            from core.section import SectionPlane
            splanes = [p for p in viewport.scene.selection
                       if isinstance(p, SectionPlane)]
            if not splanes and not viewport.scene.selection:
                # SketchUp: Move grabs a section plane directly by hovering
                # its frame — no pre-selection needed. The cut follows live.
                pick = getattr(viewport, "pick_section_plane", None)
                sp = (pick(ctx.screen.x(), ctx.screen.y())
                      if pick is not None else None)
                if sp is not None:
                    splanes = [sp]
                    groups, positions = [], []
            if labels and not viewport.scene.selection:
                # A click on the glyphs grabs just the text, never the
                # geometry that happens to sit behind it.
                groups, positions = [], []
            images = gather_images(ctx)
            mesh = viewport.scene.mesh
            verts = [v for v in (mesh.vertex_at(p) for p in positions)
                     if v is not None]
            dims = gather_dimensions(ctx, groups, verts)
            if dims and not viewport.scene.selection:
                # A click on a dimension's lines grabs the dimension, not
                # the geometry behind it.
                groups, positions, verts = [], [], []
            if not (groups or positions or labels or splanes or images
                    or dims):
                return  # nothing under the cursor / selected to move
            self._last = None            # a new move ends the array window
            self.start_point = ctx.world
            self.grab = ctx.world
            self._groups = groups
            self._positions = positions
            self._labels = labels
            self._dims = dims
            self._splanes = splanes
            self._images = images
            # The grabbed positions resolved to vertex OBJECTS once: the live
            # preview then moves these identities directly, so dragging through
            # (or onto) a coincident vertex never drags the innocent one along.
            self._verts = verts
            self._vp_preview = False
            if (self._groups and not self._verts and not self._positions):
                # Groups-only drag: viewport-side preview — the caches
                # freeze and the movers render from one-time scratch VBOs
                # at a translated MVP (the per-frame rebuild storm was the
                # 'mover un grupo demora' lag).
                begin = getattr(viewport, "begin_groups_preview", None)
                if begin is not None:
                    begin(self._groups)
                    self._vp_preview = True
            self._gather_copy_entities(ctx)
            return
        self._commit(viewport, ctx.world - self.grab)

    def on_hover(self, ctx: ToolContext) -> None:
        self.hover_point = ctx.world
        if self.grab is not None and not self._copy:
            self._apply_preview(ctx.viewport, ctx.world - self.grab)
        ctx.viewport.update()

    def on_value(self, viewport, value) -> bool:
        if self.start_point is None or self.grab is None:
            return False
        if isinstance(value, tuple):
            if len(value) != 3:
                return False  # 2-tuple is a rectangle's W×H, not a move delta
            delta = QVector3D(value[0], value[1], value[2])
        else:
            if self.hover_point is None:
                return False
            direction = self.hover_point - self.grab
            if direction.length() < 1e-9:
                return False
            delta = direction.normalized() * value
        self._commit(viewport, delta)
        return True

    def on_array_value(self, viewport, count: int, mode: str,
                       step: float | None = None) -> bool:
        """SketchUp's arrays, typed right after a Move-copy: ``3x`` lays
        three copies at multiples of the distance (external), ``/3`` three
        copies dividing it (internal). Retyping re-lays the array; the
        window closes at the next click or tool change.

        ``5x10m`` (#111) carries the spacing too: typed right after a copy
        it re-lays the copies ten metres apart along the copy's direction;
        typed DURING a Ctrl-drag it makes the copy there and then — the
        cursor gave the direction, the entry the count and the spacing."""
        if step is not None and self.start_point is not None:
            if not self._copy or self.hover_point is None or self.grab is None:
                return False
            direction = self.hover_point - self.grab
            if direction.length() < 1e-9:
                return False
            self._commit(viewport, direction.normalized() * step)
            if self._last is None:
                return False
            return self.on_array_value(viewport, count, mode)
        if step is not None and self._last is not None:
            d = self._last["delta"]
            if d.length() < 1e-9:
                return False
            self._last["delta"] = d.normalized() * step
        last = self._last
        if last is None or self.start_point is not None or count < 1:
            return False
        stack = getattr(viewport.history, "undo_stack", None)
        if not stack or stack[-1] is not last["cmd"]:
            self._last = None
            return False
        delta = last["delta"]
        if mode == "/":
            deltas = [delta * (k / float(count)) for k in range(1, count + 1)]
        else:
            deltas = [delta * float(k) for k in range(1, count + 1)]
        viewport.history.undo()
        cmd = last["build"](deltas)
        if cmd is None:
            self._last = None
            return False
        viewport.history.execute(cmd)
        last["cmd"] = cmd
        viewport.flash_status(tr("{n} copies").format(n=count))
        viewport.update()
        return True

    def on_cancel(self, viewport) -> None:
        self._end_freeze(viewport)
        self._revert_preview(viewport)
        self._reset()
        self._last = None
        viewport.update()

    # ---- Visual preview -----------------------------------------------------
    def rubber_band_lines(self):
        # The geometry deforms live, so the only extra cue is the move vector
        # from the grab point to the cursor (also carries the axis-lock colour).
        if self.grab is None or self.hover_point is None:
            return []
        segments = [(self.grab, self.hover_point)]
        if self._copy and self._base_segments:
            # Copy mode: the original stays put — preview the translated
            # COPY as a wireframe at the cursor offset.
            d = self.hover_point - self.grab
            segments.extend((a + d, b + d) for a, b in self._base_segments)
        return segments

    def value_label(self):
        if self.grab is None or self.hover_point is None:
            return None
        delta = self.hover_point - self.grab
        mid = (self.grab + self.hover_point) * 0.5
        return (fmt_len(delta.length()), mid)

    # ---- Snap exclusion -----------------------------------------------------
    def snap_excluded(self):
        """What is in motion, for the snap engine to leave out of its
        candidates: ``(edge ids, group ids)`` or ``None``. SketchUp excludes
        the entities being moved from inference — otherwise the tool snaps
        to the very geometry it is dragging (issue #19, @pacaeiro). In copy
        mode nothing moves (the ghost is a wireframe), so nothing is left
        out."""
        if self.grab is None or self._copy:
            return None
        edges: set[int] = set()
        for v in self._verts:
            for e in getattr(v, "edges", ()):
                edges.add(id(e))
        groups = {id(g) for g in self._groups}
        if not edges and not groups:
            return None
        return edges, groups

    # ---- Internals ----------------------------------------------------------
    def _gather(self, ctx: ToolContext):
        return gather_targets(ctx)

    def _gather_copy_entities(self, ctx: ToolContext) -> None:
        """The faces/edges copy mode duplicates, and the wireframe segments
        the copy preview carries (group wireframe, or the loose selection).
        Mirrors RotateTool._gather_copy_entities."""
        viewport = ctx.viewport
        self._sel_faces, self._sel_edges, self._base_segments = [], [], []
        from core.group import iter_placements
        for group in self._groups:
            for pg, xf in iter_placements(group):
                for e in pg.mesh.edges:
                    a, b = QVector3D(e.a), QVector3D(e.b)
                    if xf is not None:
                        a, b = xf.map(a), xf.map(b)
                    self._base_segments.append((a, b))
        sel = list(viewport.scene.selection)
        if not sel and not self._groups:
            # Nothing selected and no group hover-picked: the click grabbed
            # the loose entity under the cursor (mirror of gather_targets).
            edge = viewport.pick_edge(ctx.screen.x(), ctx.screen.y())
            if edge is not None:
                sel = [edge]
            else:
                face = viewport.pick_face(ctx.screen.x(), ctx.screen.y())
                if face is not None:
                    sel = [face]
        self._sel_faces = [f for f in sel if isinstance(f, Face)]
        self._sel_edges = [e for e in sel if isinstance(e, Edge)]
        for f in self._sel_faces:
            for lp in (list(f.vertices), *[list(h) for h in f.holes]):
                n = len(lp)
                for i in range(n):
                    self._base_segments.append(
                        (QVector3D(lp[i]), QVector3D(lp[(i + 1) % n])))
        for e in self._sel_edges:
            self._base_segments.append((QVector3D(e.a), QVector3D(e.b)))

    def _make_copy_builder(self):
        """A closure that builds the copy command for a list of offsets —
        one copy per offset. Kept by the array window so ``3x`` / ``/3`` can
        re-lay the copies after the tool has reset."""
        groups = list(self._groups)
        faces = list(self._sel_faces)
        edges = list(self._sel_edges)

        def build(deltas):
            cmds: list = []
            for d in deltas:
                m = QMatrix4x4()
                m.translate(d)
                for group in groups:
                    cmds.append(InsertGroupCommand(copy_group(group, d)))
                for f in faces:
                    cmds.append(AddFaceCommand(
                        [v + d for v in f.vertices],
                        holes=[[v + d for v in h] for h in f.holes] or None,
                        auto=False,
                        attrs=transformed_attrs(f.attrs, m),
                    ))
                id_map: dict[int, int] = {}
                for e in edges:
                    curve = getattr(e, "curve", None)
                    if curve is not None and curve not in id_map:
                        id_map[curve] = Mesh.next_curve_id()
                    cmds.append(AddEdgeCommand(
                        e.a + d, e.b + d,
                        soft=getattr(e, "soft", False) or None,
                        curve=id_map.get(curve)))
            if not cmds:
                return None
            return cmds[0] if len(cmds) == 1 else CompoundCommand(cmds)

        return build

    def _shift(self, viewport, step: QVector3D) -> None:
        """Translate the live geometry by ``step`` — every grabbed group's
        mesh AND the grabbed loose vertex objects (identity-exact: apply and
        revert stay symmetric even when the drag crosses another vertex's
        position)."""
        for group in self._groups:
            if getattr(group, "xform", None) is not None:
                from PySide6.QtGui import QMatrix4x4
                t = QMatrix4x4()
                t.translate(step)
                group.xform = t * group.xform   # instance: O(1)
            else:
                for v in list(group.mesh.vertices):
                    group.mesh.move_vertex(v, step)
        for v in self._verts:
            viewport.scene.mesh.move_vertex(v, step)
        for lab in self._labels:
            lab.offset = lab.offset + step
        for dim, mode in self._dims.items():
            MoveDimensionsCommand.shift(dim, mode, step)
        for sp in self._splanes:
            sp.point = sp.point + step
        for im in self._images:
            im.origin = im.origin + step
        viewport.scene.version += 1

    def _apply_preview(self, viewport, target_delta: QVector3D) -> None:
        """Live-deform the scene so the current offset is ``target_delta`` from
        the grab point, by translating the incremental step."""
        step = target_delta - self._preview_delta
        if step.length() < 1e-12:
            return
        if getattr(self, "_vp_preview", False):
            # Groups-only drag: the viewport draws the frozen scratch copy
            # at the offset — no geometry mutation, no version churn, no
            # rebuilds. Labels/section planes ride along in Python (few).
            for lab in self._labels:
                lab.offset = lab.offset + step
            for dim, mode in self._dims.items():
                MoveDimensionsCommand.shift(dim, mode, step)
            for sp in self._splanes:
                sp.point = sp.point + step
            for im in self._images:
                im.origin = im.origin + step
            self._preview_delta = target_delta
            viewport.set_groups_preview_offset(target_delta)
            return
        self._shift(viewport, step)
        self._preview_delta = target_delta

    def _revert_preview(self, viewport) -> None:
        """Undo the live deformation, returning geometry to its grab-time spot."""
        if getattr(self, "_vp_preview", False):
            # Viewport-side preview: no geometry was mutated — only the
            # labels/section planes moved live. Put those back.
            if self._preview_delta.length() >= 1e-12:
                step = -self._preview_delta
                for lab in self._labels:
                    lab.offset = lab.offset + step
                for dim, mode in self._dims.items():
                    MoveDimensionsCommand.shift(dim, mode, step)
                for sp in self._splanes:
                    sp.point = sp.point + step
                for im in self._images:
                    im.origin = im.origin + step
            self._preview_delta = QVector3D(0.0, 0.0, 0.0)
            return
        if self._preview_delta.length() < 1e-12:
            return
        self._shift(viewport, -self._preview_delta)
        self._preview_delta = QVector3D(0.0, 0.0, 0.0)

    def _end_freeze(self, viewport) -> None:
        end = getattr(viewport, "end_groups_preview", None)
        if end is not None:
            end()

    def _commit(self, viewport, delta: QVector3D) -> None:
        self._end_freeze(viewport)
        # Revert the live preview, then apply the move as one undoable command so
        # the history holds a single clean entry (and the geometry doesn't shift
        # by double the delta).
        self._revert_preview(viewport)
        if self._copy and delta.length() > 1e-9:
            # Copy mode: the original never moved; stamp the copies.
            build = self._make_copy_builder()
            cmd = build([delta])
            if cmd is not None:
                viewport.history.execute(cmd)
                # SketchUp: right after the copy, "3x" / "/3" make an array.
                self._last = {"cmd": cmd, "build": build,
                              "delta": QVector3D(delta)}
            self._reset()
            viewport.update()
            return
        if delta.length() > 1e-9:
            commands = []
            commands.extend(MoveGroupCommand(g, delta) for g in self._groups)
            if self._positions:
                commands.append(MoveVerticesCommand(self._positions, delta))
            if self._labels:
                commands.append(MoveTextLabelsCommand(self._labels, delta))
            if self._dims:
                commands.append(MoveDimensionsCommand(self._dims, delta))
            if self._splanes:
                from core.history import MoveSectionPlanesCommand
                commands.append(
                    MoveSectionPlanesCommand(self._splanes, delta))
            if self._images:
                from core.history import MoveImagePlanesCommand
                commands.append(
                    MoveImagePlanesCommand(self._images, delta))
            if len(commands) == 1:
                viewport.history.execute(commands[0])
            elif commands:
                viewport.history.execute(CompoundCommand(commands))
        self._reset()
        viewport.update()

    def _reset(self) -> None:
        self.start_point = None
        self.grab = None
        self.hover_point = None
        self._positions = []
        self._verts = []
        self._groups = []
        self._labels = []
        self._dims = {}
        self._splanes = []
        self._images = []
        self._preview_delta = QVector3D(0.0, 0.0, 0.0)
        self._sel_faces = []
        self._sel_edges = []
        self._base_segments = []
        self._vp_preview = False
        self._copy = False      # the Ctrl modifier arms ONE operation
