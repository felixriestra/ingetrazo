# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""CAM mode: an ``.igcam`` job shown in the model's place (host hook H5).

While a job is open the window shows the job's own scene — the drawing on
the stock, whose top is the ground plane, in plan view — and the 3D model
waits, parked, untouched. File ▸ Save saves the job; File ▸ New and Open
start or open another job; the title is the job's file; quitting asks
about the job first. Only 2D drawing tools can be picked, and until the
job setup is confirmed only Select: the setup comes first.
"""
from __future__ import annotations

import math
from pathlib import Path

from PySide6 import QtCore
from PySide6.QtWidgets import QFileDialog, QMessageBox

from ..i18n import tr
from ..jobfile import SUFFIX, JobFileError, save_job

#: What can be drawn on a CAM job: flat geometry on the stock. Push/pull,
#: follow me, the solid tools, sections, paint and the walk tools make no
#: sense on a 2.5D job and are switched off while it is open.
CAM_TOOLS = frozenset({
    "select", "line", "freehand", "rectangle", "rotated_rect", "circle", "polygon",
    "arc", "arc3", "center_arc", "pie", "offset", "move", "rotate", "scale", "flip",
    "fillet", "eraser", "tape", "protractor",
})
#: Before the job setup is confirmed.
SETUP_TOOLS = frozenset({"select"})

RECENT_KEY = "cam/recent_jobs"
RECENT_MAX = 8


def file_filter() -> str:
    return tr("CAM jobs (*.igcam)")


def recent_jobs() -> list:
    """The recently opened or saved jobs that still exist, newest first."""
    raw = QtCore.QSettings().value(RECENT_KEY, []) or []
    if isinstance(raw, str):
        raw = [raw]
    return [Path(p) for p in raw if Path(p).is_file()]


def remember_job(path) -> None:
    paths = [str(Path(path))] + [str(p) for p in recent_jobs() if str(p) != str(Path(path))]
    QtCore.QSettings().setValue(RECENT_KEY, paths[:RECENT_MAX])


def plan_camera(stock_width_mm: float, stock_depth_mm: float, aspect: float = 1.5) -> dict:
    """A camera dict (``MainWindow._camera_dict`` shape) looking straight
    down on the stock, orthographic, with the whole stock in view."""
    from PySide6.QtGui import QVector3D
    from core.camera import OrbitCamera
    cam = OrbitCamera()
    cam.set_view("top")
    cam.perspective = False
    cam.aspect = aspect
    w, d = stock_width_mm / 1000.0, stock_depth_mm / 1000.0
    # Straight down, so the stock's width against the view's width and its
    # depth against the height decide; 15 % to spare around it.
    cam.target = QVector3D(w * 0.5, d * 0.5, 0.0)
    half_h = max(d * 0.5, w * 0.5 / max(aspect, 1e-3)) * 1.15
    cam.distance = half_h / math.tan(math.radians(cam.fov_deg) / 2.0)
    t = cam.target
    out = {"target": [float(t.x()), float(t.y()), float(t.z())]}
    for k in ("distance", "yaw", "pitch", "fov_deg", "perspective", "two_point"):
        out[k] = getattr(cam, k)
    if not math.isfinite(out["distance"]) or out["distance"] <= 0:
        out["distance"] = max(w, d, 0.1) * 2
    return out


class CamWorkspace:
    """The H5 workspace of one open CAM job (see ``docs/plugins.md``)."""

    def __init__(self, dock, scene, path) -> None:
        from core.history import History
        self.dock = dock
        self.scene = scene
        self.history = History(scene)
        self.path = Path(path) if path is not None else None
        st = dock_state_stock(scene)
        self.camera = plan_camera(*st)
        self._saved_version = scene.version

    # ---- H5 protocol ------------------------------------------------------------
    @property
    def allowed_tools(self):
        return CAM_TOOLS if self.dock.state.setupDone else SETUP_TOOLS

    def title(self) -> str:
        return self.path.name if self.path is not None else tr("Untitled CAM job")

    def is_dirty(self) -> bool:
        # No flush here: the title asks this after every edit, and writing a
        # pending job edit then would stack it above the geometry edit that
        # prompted the question — undo would take them back out of order.
        return self.scene.version != self._saved_version or self.dock.has_pending_edit()

    def mark_saved(self) -> None:
        self._saved_version = self.scene.version

    def save(self) -> bool:
        self.dock.flush()
        if self.path is None:
            return self.save_as()
        try:
            # The dock's state, not the last one written to the undo stack:
            # operations that followed an edited path carry their new
            # geometry only there (see CamDock._follow_paths).
            save_job(self.scene, self.path, state=self.dock.state.to_dict())
        except (OSError, JobFileError) as exc:
            QMessageBox.critical(self.dock, tr("CAM"),
                                 tr("The job could not be saved: {error}", error=str(exc)))
            return False
        self.mark_saved()
        remember_job(self.path)
        self.dock.on_job_saved()
        return True

    def save_as(self) -> bool:
        start = str(self.path) if self.path else self.dock.state.job.name + SUFFIX
        path_str, _ = QFileDialog.getSaveFileName(self.dock, tr("Save CAM job"), start,
                                                  file_filter())
        if not path_str:
            return False
        path = Path(path_str)
        if path.suffix.lower() != SUFFIX:
            path = path.with_suffix(SUFFIX)
        self.path = path
        return self.save()

    def new(self) -> None:
        self.dock.new_job()

    def open(self) -> None:
        self.dock.open_job()

    def confirm_leave(self) -> bool:
        self.dock.flush()
        if not self.is_dirty():
            return True
        box = QMessageBox(
            QMessageBox.Question, tr("CAM"),
            tr("The CAM job «{name}» has unsaved changes.", name=self.title()),
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, self.dock)
        box.setOption(QMessageBox.Option.DontUseNativeDialog, True)
        box.setDefaultButton(QMessageBox.Save)
        answer = box.exec()
        if answer == QMessageBox.Save:
            return self.save()
        return answer == QMessageBox.Discard

    def left(self) -> None:
        self.dock.on_workspace_left(self)


def dock_state_stock(scene) -> tuple:
    """``(width, depth)`` of the stock in ``scene``'s job, mm."""
    from ..state import CamState
    d = (getattr(scene, "plugin_data", None) or {}).get("cam") or {}
    try:
        st = CamState.from_dict(d).job.stock
        return st.width, st.depth
    except Exception:  # noqa: BLE001
        return 100.0, 75.0
