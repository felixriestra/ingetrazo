# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The stock-removal window: the job played back on a 3D block of stock.

:class:`StockView` is a small OpenGL 3.3 core view of its own (the same
context profile the main viewport asks for): the height field as a lit
block — the stock top in wood colour, what the cutter exposed a shade
lighter and warmer, through cuts as real holes (fragments at the stock
bottom are discarded) — plus the toolpath as lines and the cutter as a
cylinder at the playhead. Drag to orbit, right/middle-drag to pan, wheel
to zoom, double-click to fit.

:class:`SimulationWindow` drives :class:`~..engine.simulate.Playback`:
play/pause, back to the start, to the end, a time slider that scrubs in
machine time, a speed choice and a resolution choice. Each frame it
advances the simulation, re-uploads the heights and tells the dock where
the tool is, so the main viewport's overlay follows the same playhead.

Everything the view draws is in work millimetres; the stock is the whole
world here, so there is no model-space conversion to get wrong.
"""
from __future__ import annotations

import bisect
import math
import time

import numpy as np
from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QMatrix4x4, QOpenGLFunctions, QSurfaceFormat, QVector3D
from PySide6.QtOpenGL import QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram, \
    QOpenGLVertexArrayObject
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QListWidget, QPushButton,
                               QSlider, QVBoxLayout, QWidget)

from ..engine.simulate import Playback
from ..engine.toolpath import Comment, ToolChange
from ..i18n import tr

GL_FLOAT = 0x1406
GL_TRIANGLES = 0x0004
GL_LINES = 0x0001
GL_TRIANGLE_STRIP = 0x0005
GL_DEPTH_TEST = 0x0B71
GL_COLOR_BUFFER_BIT = 0x4000
GL_DEPTH_BUFFER_BIT = 0x0100
GL_BLEND = 0x0BE2
GL_SRC_ALPHA = 0x0302
GL_ONE_MINUS_SRC_ALPHA = 0x0303
GL_CULL_FACE = 0x0B44

#: Commands that move nothing.
_NO_MOTION = (Comment, ToolChange)

VERT = """#version 330 core
layout(location = 0) in vec3 pos;
layout(location = 1) in vec3 nrm;
uniform mat4 mvp;
out vec3 v_n;
out float v_z;
void main() {
    v_n = nrm;
    v_z = pos.z;
    gl_Position = mvp * vec4(pos, 1.0);
}
"""

FRAG = """#version 330 core
in vec3 v_n;
in float v_z;
uniform int mode;          // 0 stock, 1 lit solid colour, 2 unlit colour
uniform vec4 color;
uniform float top;
uniform float bottom;
out vec4 frag;
void main() {
    if (mode == 2) { frag = color; return; }
    vec3 n = normalize(v_n);
    float light = 0.35 + 0.55 * max(dot(n, normalize(vec3(0.35, -0.45, 0.85))), 0.0)
                + 0.20 * max(dot(n, normalize(vec3(-0.6, 0.5, 0.3))), 0.0);
    vec3 base;
    if (mode == 0) {
        if (v_z <= bottom + 0.02) discard;                 // cut through
        float depth = clamp((top - v_z) / max(top - bottom, 1e-6), 0.0, 1.0);
        vec3 wood = vec3(0.80, 0.63, 0.40);
        vec3 cut = vec3(0.95, 0.84, 0.64);
        base = (top - v_z) < 0.01 ? wood : mix(cut, vec3(0.78, 0.60, 0.40), depth * 0.6);
    } else {
        base = color.rgb;
    }
    frag = vec4(base * light, color.a);
}
"""


class StockView(QOpenGLWidget):
    """OpenGL view of a height field, a toolpath and a cutter."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # Ask for 3.3 core ourselves, as the main viewport does: the app's
        # default format is not guaranteed (a host embedding the plugin, a
        # test), and macOS gives a 2.1 legacy context otherwise.
        fmt = QSurfaceFormat(QSurfaceFormat.defaultFormat())
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.CoreProfile)
        fmt.setDepthBufferSize(24)
        fmt.setSamples(4)
        self.setFormat(fmt)
        self.setMinimumSize(420, 320)
        self.field = None
        self._dirty = False
        self._ready = False
        self._program = None
        self._n_idx = 0
        self._lines = np.empty((0, 3), dtype=np.float32)
        self._lines_dirty = False
        self._tool = None            # (tip (x,y,z), radius, length)
        self.yaw, self.pitch, self.dist = -35.0, 38.0, 300.0
        self.target = QVector3D(0, 0, 0)
        self._drag = None

    # ---- data ------------------------------------------------------------
    def set_field(self, field) -> None:
        self.field = field
        self._dirty = True
        self._geometry_changed = True
        self.fit()

    def heights_changed(self) -> None:
        self._dirty = True
        self.update()

    def set_toolpath_lines(self, segments) -> None:
        """``segments``: (N, 2, 3) work-mm line segments."""
        self._lines = np.asarray(segments, dtype=np.float32).reshape(-1, 3)
        self._lines_dirty = True
        self.update()

    def set_tool(self, tip, radius, length) -> None:
        self._tool = None if tip is None else (tip, radius, length)
        self.update()

    def fit(self) -> None:
        if self.field is None:
            return
        st = self.field.stock
        ox, oy, oz = st.origin
        self.target = QVector3D(ox + st.width / 2, oy + st.depth / 2, oz + st.height / 2)
        self.dist = 1.6 * max(st.width, st.depth, st.height)
        self.update()

    # ---- GL --------------------------------------------------------------------
    def initializeGL(self) -> None:
        self.gl = QOpenGLFunctions(self.context())
        self.gl.initializeOpenGLFunctions()
        p = QOpenGLShaderProgram(self)
        ok = (p.addShaderFromSourceCode(QOpenGLShader.Vertex, VERT)
              and p.addShaderFromSourceCode(QOpenGLShader.Fragment, FRAG) and p.link())
        if not ok:                       # pragma: no cover — driver without 3.3 core
            self._program = None
            return
        self._program = p
        self._stock = self._make_vao()
        self._skirt = self._make_vao()
        self._tool_vao = self._make_vao()
        self._line_vao = self._make_vao()
        self._ready = True
        self._geometry_changed = True
        self._dirty = True

    def _make_vao(self):
        vao = QOpenGLVertexArrayObject(self)
        vao.create()
        vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
        vbo.create()
        vbo.setUsagePattern(QOpenGLBuffer.DynamicDraw)
        return {"vao": vao, "vbo": vbo, "count": 0, "cap": 0}

    def _upload(self, obj, data: np.ndarray) -> None:
        raw = np.ascontiguousarray(data, dtype=np.float32).tobytes()
        obj["vao"].bind()
        obj["vbo"].bind()
        if len(raw) > obj["cap"]:
            obj["vbo"].allocate(raw, len(raw))
            obj["cap"] = len(raw)
        elif raw:
            obj["vbo"].write(0, raw, len(raw))
        stride = 6 * 4
        p = self._program
        p.enableAttributeArray(0)
        p.setAttributeBuffer(0, GL_FLOAT, 0, 3, stride)
        p.enableAttributeArray(1)
        p.setAttributeBuffer(1, GL_FLOAT, 12, 3, stride)
        obj["count"] = len(data)
        obj["vao"].release()

    def _stock_arrays(self):
        f = self.field
        X, Y = np.meshgrid(f.x, f.y)
        Z = f.heights
        gy, gx = np.gradient(Z, f.cell_d, f.cell_w)
        N = np.dstack([-gx, -gy, np.ones_like(Z)])
        N /= np.linalg.norm(N, axis=2, keepdims=True)
        verts = np.dstack([X, Y, Z, N]).reshape(-1, 6)
        return verts

    def _strip_order(self):
        """Vertex order that draws the grid as ONE triangle strip: row
        pairs zig-zag, joined by repeated vertices (degenerate triangles).
        PySide6's ``glDrawElements`` will not take a null index offset, so
        the heights are gathered in this order on upload instead."""
        f = self.field
        r, c = f.rows, f.cols
        idx = np.arange(r * c).reshape(r, c)
        rows = []
        for i in range(r - 1):
            pair = np.empty(2 * c, dtype=np.int64)
            pair[0::2] = idx[i + 1]
            pair[1::2] = idx[i]
            if rows:
                rows.append(pair[:1])            # degenerate join
            rows.append(pair)
            rows.append(pair[-1:])
        return np.concatenate(rows) if rows else np.arange(r * c)

    def _skirt_arrays(self):
        """The four sides and the bottom of the block, following the
        heights along the border."""
        f = self.field
        st = f.stock
        ox, oy, oz = st.origin
        x0, x1, y0, y1 = f.x[0], f.x[-1], f.y[0], f.y[-1]
        tris = []

        def side(xs, ys, zs, normal):
            for i in range(len(xs) - 1):
                p = [(xs[i], ys[i], zs[i]), (xs[i + 1], ys[i + 1], zs[i + 1]),
                     (xs[i + 1], ys[i + 1], oz), (xs[i], ys[i], oz)]
                for k in (0, 1, 2, 0, 2, 3):
                    tris.append((*p[k], *normal))

        H = f.heights
        side(f.x, np.full(f.cols, y0), H[0, :], (0, -1, 0))
        side(f.x[::-1], np.full(f.cols, y1), H[-1, ::-1], (0, 1, 0))
        side(np.full(f.rows, x0), f.y[::-1], H[::-1, 0], (-1, 0, 0))
        side(np.full(f.rows, x1), f.y, H[:, -1], (1, 0, 0))
        bottom = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        for k in (0, 2, 1, 0, 3, 2):
            tris.append((bottom[k][0], bottom[k][1], oz + 0.03, 0, 0, -1))
        return np.asarray(tris, dtype=np.float32)

    def _tool_arrays(self):
        (x, y, z), r, length = self._tool
        n = 24
        ang = np.linspace(0, 2 * np.pi, n + 1)
        tris = []
        for i in range(n):
            a0, a1 = ang[i], ang[i + 1]
            c0, s0, c1, s1 = math.cos(a0), math.sin(a0), math.cos(a1), math.sin(a1)
            p = [(x + r * c0, y + r * s0, z), (x + r * c1, y + r * s1, z),
                 (x + r * c1, y + r * s1, z + length), (x + r * c0, y + r * s0, z + length)]
            nm = [(c0, s0, 0), (c1, s1, 0), (c1, s1, 0), (c0, s0, 0)]
            for k in (0, 1, 2, 0, 2, 3):
                tris.append((*p[k], *nm[k]))
            tris.append((x, y, z, 0, 0, -1))
            tris.append((x + r * c1, y + r * s1, z, 0, 0, -1))
            tris.append((x + r * c0, y + r * s0, z, 0, 0, -1))
        return np.asarray(tris, dtype=np.float32)

    def _mvp(self) -> QMatrix4x4:
        proj = QMatrix4x4()
        aspect = self.width() / max(1, self.height())
        proj.perspective(35.0, aspect, max(0.1, self.dist * 0.01), self.dist * 20)
        yaw, pitch = math.radians(self.yaw), math.radians(self.pitch)
        eye = self.target + QVector3D(math.cos(pitch) * math.sin(yaw),
                                      -math.cos(pitch) * math.cos(yaw),
                                      math.sin(pitch)) * self.dist
        view = QMatrix4x4()
        view.lookAt(eye, self.target, QVector3D(0, 0, 1))
        return proj * view

    def paintGL(self) -> None:
        gl = getattr(self, "gl", None)
        if gl is None:
            return
        gl.glClearColor(0.93, 0.94, 0.96, 1.0)
        gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        if not self._ready or self.field is None:
            return
        gl.glEnable(GL_DEPTH_TEST)
        p = self._program
        p.bind()
        if self._dirty:
            if self._geometry_changed:
                self._order = self._strip_order()
            self._upload(self._stock, self._stock_arrays()[self._order])
            self._upload(self._skirt, self._skirt_arrays())
            self._geometry_changed = False
            self._dirty = False
        if self._lines_dirty:
            pts = self._lines
            data = np.hstack([pts, np.zeros_like(pts)]) if len(pts) else np.zeros((0, 6))
            self._upload(self._line_vao, data)
            self._lines_dirty = False
        p.setUniformValue("mvp", self._mvp())
        p.setUniformValue1f("top", float(self.field.top))
        p.setUniformValue1f("bottom", float(self.field.bottom))
        p.setUniformValue1i("mode", 0)
        p.setUniformValue("color", 1.0, 1.0, 1.0, 1.0)
        self._draw(self._stock, GL_TRIANGLE_STRIP)
        p.setUniformValue1i("mode", 1)
        p.setUniformValue("color", 0.72, 0.56, 0.36, 1.0)
        self._draw(self._skirt, GL_TRIANGLES)
        if self._tool is not None:
            self._upload(self._tool_vao, self._tool_arrays())
            p.setUniformValue("color", 0.55, 0.58, 0.63, 1.0)
            self._draw(self._tool_vao, GL_TRIANGLES)
        if self._line_vao["count"]:
            gl.glEnable(GL_BLEND)
            gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            p.setUniformValue1i("mode", 2)
            p.setUniformValue("color", 0.0, 0.35, 0.75, 0.45)
            self._draw(self._line_vao, GL_LINES)
            gl.glDisable(GL_BLEND)
        p.release()

    def _draw(self, obj, prim) -> None:
        if not obj["count"]:
            return
        obj["vao"].bind()
        self.gl.glDrawArrays(prim, 0, obj["count"])
        obj["vao"].release()

    # ---- mouse -------------------------------------------------------------
    def mousePressEvent(self, e) -> None:  # noqa: N802
        self._drag = (e.position().toPoint(), e.button())

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        self._drag = None

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._drag is None:
            return
        start, button = self._drag
        pos = e.position().toPoint()
        d = pos - start
        self._drag = (pos, button)
        if button == Qt.LeftButton:
            self.yaw += d.x() * 0.4
            self.pitch = max(-89.0, min(89.0, self.pitch + d.y() * 0.4))
        else:
            yaw = math.radians(self.yaw)
            right = QVector3D(math.cos(yaw), math.sin(yaw), 0)
            up = QVector3D(0, 0, 1)
            k = self.dist * 0.0015
            self.target = self.target - right * (d.x() * k) + up * (d.y() * k)
        self.update()

    def wheelEvent(self, e) -> None:  # noqa: N802
        self.dist *= 0.9 ** (e.angleDelta().y() / 120.0)
        self.update()

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        self.fit()


class SimulationWindow(QWidget):
    """2DCam's simulation around a :class:`StockView`.

    Transport: back to the start, play/pause, step one command, to the
    end, a slider in machine time, 2DCam's speeds. Two resolutions, as in
    2DCam: *Preview* (live, responsive) and *High quality* (for looking at
    the finished surface), with the cell size shown and explained; *Final
    simulation* runs the whole job at high quality. Beside the view the
    posted G-code, the running line highlighted; under it the phase
    (roughing, finishing, rapid…), the tool tip's coordinates, the
    operation, the removed volume and the tool. *Open G-code…* plays any
    program on the job's stock instead.
    """

    #: ``(command_index, work_point, tool)`` at every playback step; the
    #: dock relays it to the main viewport overlay (``None`` index: stop).
    playhead = Signal(object)

    #: 2DCam's speeds (RibbonCatalog.simulationSpeeds), × machine time.
    SPEEDS = (0.25, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 400, 600, 1000, 1500, 2000)
    #: The most cells the view re-meshes per frame while playing, and once
    #: for a final simulation. 2DCam's budgets are 1 M and 6 M; re-meshing
    #: in Python takes about 60 ms per million cells, so the port stays
    #: under these (see docs/cam-2dcam-deviations.md).
    LIVE_CELLS = 250_000
    FINAL_CELLS = 1_500_000

    def __init__(self, parent=None) -> None:
        super().__init__(parent, Qt.Window)
        self.setWindowTitle(tr("Stock simulation"))
        self.resize(1100, 720)
        self.view = StockView(self)
        self.pb = None
        self.job = None
        self.compiled = None
        self.toolpath = None
        self.ranges = {}
        self.op_names = {}
        self.lines: list = []
        self.command_line: list = []
        self.source_name = None          # an opened G-code file, else the job
        self._job_program = None         # (toolpath, ranges, lines, command_line)
        self._playing = False
        self._final = False
        self._last = None
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

        from PySide6.QtGui import QFontDatabase
        from PySide6.QtWidgets import QSplitter
        self.btn_start = QPushButton("⏮")
        self.btn_play = QPushButton("▶")
        self.btn_step = QPushButton("⏭")
        self.btn_end = QPushButton("⏩")
        for b, tip in ((self.btn_start, tr("Back to the start")),
                       (self.btn_play, tr("Play / pause")),
                       (self.btn_step, tr("Step forward: one command")),
                       (self.btn_end, tr("Jump to the end"))):
            b.setToolTip(tip)
            b.setFixedWidth(44)
        self.btn_start.clicked.connect(lambda: self.seek(0.0))
        self.btn_play.clicked.connect(self.toggle)
        self.btn_step.clicked.connect(self.step)
        self.btn_end.clicked.connect(lambda: self.seek(self.pb.total if self.pb else 0.0))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.sliderMoved.connect(self._on_slider)
        self.speed = QComboBox()
        for sp in self.SPEEDS:
            self.speed.addItem(f"{sp:g}×", sp)
        self.speed.setCurrentIndex(self.SPEEDS.index(100))
        self.quality = QComboBox()
        self.quality.addItem(tr("Preview"), "live")
        self.quality.addItem(tr("High quality"), "final")
        self.quality.currentIndexChanged.connect(self._rebuild)
        self.cells = QLabel()
        self.btn_final = QPushButton(tr("Final simulation"))
        self.btn_final.setToolTip(tr("Run the whole job at high quality."))
        self.btn_final.clicked.connect(self.run_final)
        self.btn_open = QPushButton(tr("Open G-code…"))
        self.btn_open.setToolTip(tr("Play a G-code program on this job's stock."))
        self.btn_open.clicked.connect(lambda: self.open_gcode())
        self.btn_job = QPushButton(tr("Job program"))
        self.btn_job.setToolTip(tr("Back to the program of this job."))
        self.btn_job.clicked.connect(self.show_job_program)
        self.btn_job.setVisible(False)
        self.clock = QLabel()
        self.phase = QLabel()
        self.coords = QLabel()
        mono = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        self.coords.setFont(mono)
        self.info = QLabel()
        self.info.setWordWrap(True)

        self.code_title = QLabel(tr("G-code"))
        self.code = QListWidget()
        self.code.setFont(mono)
        self.code.setUniformItemSizes(True)
        self.code.itemClicked.connect(self._on_code_clicked)
        code_box = QWidget()
        cl = QVBoxLayout(code_box)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self.code_title)
        cl.addWidget(self.code, 1)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.view)
        split.addWidget(code_box)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)

        row = QHBoxLayout()
        for w in (self.btn_start, self.btn_play, self.btn_step, self.btn_end):
            row.addWidget(w)
        row.addWidget(self.slider, 1)
        row.addWidget(self.clock)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel(tr("Speed")))
        row2.addWidget(self.speed)
        row2.addSpacing(12)
        row2.addWidget(QLabel(tr("Quality")))
        row2.addWidget(self.quality)
        row2.addWidget(self.cells)
        row2.addWidget(self.btn_final)
        row2.addSpacing(12)
        row2.addWidget(self.btn_open)
        row2.addWidget(self.btn_job)
        row2.addStretch(1)
        row2.addWidget(self.phase)
        row2.addSpacing(12)
        row2.addWidget(self.coords)
        lay = QVBoxLayout(self)
        lay.addWidget(split, 1)
        lay.addLayout(row)
        lay.addLayout(row2)
        lay.addWidget(self.info)
        hint = QLabel(tr("Drag to orbit, right-drag to pan, wheel to zoom."))
        hint.setEnabled(False)
        lay.addWidget(hint)

    # ---- loading -------------------------------------------------------------
    def load(self, job, compiled, listing=None) -> None:
        """Simulate ``compiled`` (a CompileResult) of the work ``job``;
        ``listing`` is the posted program as ``(lines, command_line)``
        (:func:`..engine.post.listing`)."""
        self.job, self.compiled = job, compiled
        self.op_names = {o.id: o.name for o in job.operations}
        lines, command_line = listing or ([], [])
        self._job_program = (compiled.toolpath, dict(compiled.operation_ranges), lines,
                             command_line)
        self.show_job_program()

    def show_job_program(self) -> None:
        if self._job_program is None:
            return
        self.toolpath, self.ranges, lines, command_line = self._job_program
        self.source_name = None
        self.btn_job.setVisible(False)
        self._set_program(lines, command_line)
        self.pb = None
        self._rebuild()

    def open_gcode(self, path=None) -> bool:
        """Play a G-code file on the job's stock (``path`` given: no
        dialog). The program's coordinates are taken as work coordinates,
        its tool from ``M6`` or else the job's first tool."""
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        from ..engine.simulate import toolpath_from_gcode
        if self.job is None:
            return False
        if path is None:
            path_str, _ = QFileDialog.getOpenFileName(
                self, tr("Open G-code"), "", tr("G-code (*.nc *.ngc *.gcode *.tap *.txt);;"
                                               "All files (*)"))
            if not path_str:
                return False
            path = path_str
        path = Path(path)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            toolpath, command_line = toolpath_from_gcode(text, self.job)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, tr("Stock simulation"), str(exc))
            return False
        if not any(not isinstance(c, _NO_MOTION) for c in toolpath.commands):
            QMessageBox.warning(self, tr("Stock simulation"),
                                tr("The file has no G0, G1, G2 or G3 moves to simulate."))
            return False
        self.toolpath, self.ranges = toolpath, {}
        self.source_name = path.name
        self.btn_job.setVisible(self._job_program is not None)
        self._set_program(text.splitlines(), command_line)
        self.pb = None
        self._rebuild()
        return True

    def _set_program(self, lines, command_line) -> None:
        self.lines, self.command_line = list(lines), list(command_line)
        self.code.clear()
        width = len(str(len(self.lines)))
        self.code.addItems([f"{i + 1:>{width}}  {ln}" for i, ln in enumerate(self.lines)])
        self._shown_line = None

    def _cell(self) -> float:
        from ..engine.simulate import cell_size
        mode = self.quality.currentData()
        return cell_size(self.job, mode,
                         self.FINAL_CELLS if mode == "final" else self.LIVE_CELLS)

    def _rebuild(self, *_a) -> None:
        if self.job is None or self.toolpath is None:
            return
        t = self.pb.time if self.pb else 0.0
        cell = self._cell()
        self.pb = Playback(self.job, self.toolpath, cell)
        self.view.set_field(self.pb.field)
        from ..engine.toolpath import motion_points
        segs = [(a, b) for kind, a, b, _i in motion_points(self.toolpath.commands)
                if kind == "cut"]
        self.view.set_toolpath_lines(np.asarray(segs, dtype=float).reshape(-1, 2, 3))
        inch = self.job.is_inch
        from . import messages
        self.cells.setText(tr("{size} cells", size=messages.format_length(cell, inch)))
        live = messages.format_length(self._cell_for("live"), inch)
        final = messages.format_length(self._cell_for("final"), inch)
        self.cells.setToolTip(tr(
            "Preview uses about {live} cells for responsive playback and can show wavy "
            "ridges that are the grid, not the G-code. High quality uses about {final} "
            "cells for looking at the surface, and takes longer.", live=live, final=final))
        self.seek(t)

    def _cell_for(self, mode) -> float:
        from ..engine.simulate import cell_size
        return cell_size(self.job, mode, self.FINAL_CELLS if mode == "final"
                         else self.LIVE_CELLS)

    # ---- transport -----------------------------------------------------------
    def toggle(self) -> None:
        if self.pb is None:
            return
        if not self._playing and self.pb.time >= self.pb.total:
            self.seek(0.0)
        self._playing = not self._playing
        self.btn_play.setText("⏸" if self._playing else "▶")
        self._last = time.monotonic()
        if self._playing:
            self._timer.start()
        else:
            self._timer.stop()

    def _stop(self) -> None:
        if self._playing:
            self.toggle()

    def step(self) -> None:
        """One command further (2DCam's Step Forward)."""
        if self.pb is None:
            return
        self._stop()
        self.pb.step()
        self.seek(self.pb.time)

    def run_final(self) -> None:
        """The whole job at high quality, drawn once it is done."""
        if self.pb is None:
            return
        self._stop()
        if self.quality.currentData() != "final":
            self.quality.blockSignals(True)
            self.quality.setCurrentIndex(self.quality.findData("final"))
            self.quality.blockSignals(False)
        self.pb = None
        self._rebuild()
        from PySide6.QtWidgets import QApplication
        self.btn_final.setEnabled(False)
        try:
            n = 40
            for k in range(1, n + 1):
                self.pb.seek(self.pb.total * k / n)
                self.info.setText(tr("Final simulation… {percent} %", percent=round(100 * k / n)))
                QApplication.processEvents()
        finally:
            self.btn_final.setEnabled(True)
        self.seek(self.pb.total)

    def _tick(self) -> None:
        now = time.monotonic()
        dt = now - (self._last or now)
        self._last = now
        t = self.pb.time + dt * self.speed.currentData()
        self.seek(t)
        if t >= self.pb.total and self._playing:
            self.toggle()

    def _on_slider(self, value: int) -> None:
        if self.pb is not None:
            self.seek(self.pb.total * value / 1000.0)

    def _on_code_clicked(self, item) -> None:
        """Clicking a line of the program runs the job up to it."""
        if self.pb is None or not self.command_line:
            return
        row = self.code.row(item)
        i = bisect.bisect_left(self.command_line, row)
        i = min(i, len(self.pb.times) - 1)
        self._stop()
        self.seek(self.pb.times[i])

    def seek(self, t: float) -> None:
        if self.pb is None:
            return
        self.pb.seek(t)
        self.view.heights_changed()
        tip, tool = self.pb.tool_position()
        if tip is not None and tool is not None:
            self.view.set_tool(tip, tool.diameter * 0.5, max(tool.fluteLength, 10.0))
        else:
            self.view.set_tool(None, 0, 0)
        if not self.slider.isSliderDown():
            self.slider.blockSignals(True)
            self.slider.setValue(int(round(1000 * self.pb.time / max(self.pb.total, 1e-9))))
            self.slider.blockSignals(False)
        self.clock.setText(f"{_clock(self.pb.time)} / {_clock(self.pb.total)}")
        idx = self.pb.command_index
        self._show_line(idx)
        phase = self.pb.phase
        self.phase.setText(phase_label(phase) if phase else "")
        inch = self.job.is_inch
        if tip is not None:
            k = 1 / 25.4 if inch else 1.0
            d = 4 if inch else 3
            self.coords.setText(f"X {tip[0] * k:.{d}f}  Y {tip[1] * k:.{d}f}  "
                                f"Z {tip[2] * k:.{d}f}")
        else:
            self.coords.setText("X —  Y —  Z —")
        op = (self.source_name or
              next((self.op_names.get(k) for k, (a, b) in self.ranges.items() if a <= idx < b),
                   ""))
        vol = self.pb.field.removed_volume
        vol_text = (f"{vol / 16387.064:.2f} in³" if inch else f"{vol / 1000:.1f} cm³")
        self.info.setText(tr("{operation} · removed {volume} · tool {tool}",
                             operation=op or "—", volume=vol_text,
                             tool=(f"T{tool.number}" if tool else "—")))
        if self.source_name is None:
            self.playhead.emit((idx, tip, tool))

    def _show_line(self, idx: int) -> None:
        total = len(self.pb.times)
        done = min(idx + 1, total) if self.pb.time > 0 else 0
        self.code_title.setText(tr("G-code  {done} / {total}", done=done, total=total))
        if not self.command_line or idx >= len(self.command_line):
            return
        row = self.command_line[idx] if self.pb.time > 0 else 0
        if row == getattr(self, "_shown_line", None) or row >= self.code.count():
            return
        self._shown_line = row
        self.code.setCurrentRow(row)
        self.code.scrollToItem(self.code.item(row), QListWidget.PositionAtCenter)

    def closeEvent(self, e) -> None:  # noqa: N802
        self._timer.stop()
        self._playing = False
        self.playhead.emit((None, None, None))
        super().closeEvent(e)


def phase_label(kind: str) -> str:
    return {
        "roughing": tr("Roughing"), "finishing": tr("Finishing"), "rapid": tr("Rapid"),
        "leadIn": tr("Lead-in"), "leadOut": tr("Lead-out"), "linking": tr("Linking"),
        "cutting": tr("Cutting"), "auxiliary": tr("Auxiliary"),
    }.get(kind, kind)


def _clock(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
