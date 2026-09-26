# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""«Trace an image…»: a picture becomes outlines on the stock.

A logo, a scanned drawing, lettering: the window shows the picture with the
traced outlines over it and a match score, and one **Detail** setting runs
from every pixel to smooth and simple (2DCam's candidate ladder as a
slider). The threshold is found automatically (Otsu) until the user moves
it; **Invert** traces the white instead of the black. The width is set in
the job's units and the height follows the picture.

The outlines are added to the job's drawing as ordinary lines — paths like
any drawn ones, for profiles, pockets or engraving — centred on the stock,
in one undoable step. The picture itself is not kept.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QImage, QImageReader, QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QMessageBox, QPushButton, QSlider,
                               QVBoxLayout, QWidget)

from ..engine import trace as tracer
from ..i18n import tr
from .op_forms import LengthSpin

#: The picture is traced at most this big on its long side (2DCam's limit):
#: finer than a router cuts at any sensible size, and fast.
MAX_SIDE = 1024
#: Where the Detail slider starts: fine enough that curves look round at
#: sign sizes, coarse enough to drop scanning dirt.
DEFAULT_DETAIL = 25


def image_filter() -> str:
    formats = sorted({bytes(f).decode().lower() for f in QImageReader.supportedImageFormats()})
    wanted = [f for f in ("png", "jpg", "jpeg", "bmp", "gif", "tif", "tiff", "webp") if f in formats]
    return tr("Images ({patterns})", patterns=" ".join(f"*.{f}" for f in wanted))


def gray_of(image: QImage) -> np.ndarray:
    """A picture as grey levels 0 … 255, transparency composited on white
    (a logo on a transparent background traces as the logo), scaled down
    to :data:`MAX_SIDE`."""
    if max(image.width(), image.height()) > MAX_SIDE:
        image = image.scaled(MAX_SIDE, MAX_SIDE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    flat = QImage(image.size(), QImage.Format_RGB32)
    flat.fill(QColor("#FFFFFF"))
    p = QPainter(flat)
    p.drawImage(0, 0, image)
    p.end()
    g = flat.convertToFormat(QImage.Format_Grayscale8)
    w, h, stride = g.width(), g.height(), g.bytesPerLine()
    buf = np.frombuffer(g.constBits(), dtype=np.uint8, count=stride * h).reshape(h, stride)
    return buf[:, :w].copy()


def outline_path(result, scale: float = 1.0, dx: float = 0.0, dy: float = 0.0) -> QPainterPath:
    path = QPainterPath()
    path.setFillRule(Qt.OddEvenFill)
    for lp in result.loops:
        path.moveTo(QPointF(lp[0][0] * scale + dx, lp[0][1] * scale + dy))
        for x, y in lp[1:]:
            path.lineTo(QPointF(x * scale + dx, y * scale + dy))
        path.closeSubpath()
    return path


def match_score(result) -> float:
    """The traced shapes filled and compared with the thresholded picture."""
    img = QImage(result.width, result.height, QImage.Format_Grayscale8)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, False)
    p.fillPath(outline_path(result), QColor("#FFFFFF"))
    p.end()
    stride = img.bytesPerLine()
    drawn = np.frombuffer(img.constBits(), dtype=np.uint8,
                          count=stride * result.height).reshape(result.height, stride)
    return tracer.match(result.mask, drawn[:, :result.width] > 127)


class TraceDialog(QDialog):
    """After ``exec()`` returns Accepted, :attr:`outlines_mm` holds the
    loops in millimetres (lower-left at 0, 0; Y up)."""

    def __init__(self, inch: bool = False, width_mm: float = 100.0, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Trace an image"))
        self.gray = None
        self.traced = None
        self.outlines_mm = []
        self.resize(900, 620)
        lay = QHBoxLayout(self)
        self.preview = QLabel(tr("Choose a picture: a logo, lettering or a scanned drawing. "
                                 "Dark areas become shapes to machine."))
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setWordWrap(True)
        self.preview.setMinimumSize(520, 520)
        self.preview.setStyleSheet("background: #FFFFFF; color: #555555;")
        lay.addWidget(self.preview, 1)

        side = QWidget()
        side.setMaximumWidth(320)
        col = QVBoxLayout(side)
        choose = QPushButton(tr("Choose image…"))
        choose.clicked.connect(self._on_choose)
        col.addWidget(choose)
        f = QFormLayout()
        f.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.detail = QSlider(Qt.Horizontal)
        self.detail.setRange(0, 100)
        self.detail.setValue(DEFAULT_DETAIL)
        self.detail.setToolTip(tr("Left: every pixel. Right: smooth, simple outlines and no "
                                  "small specks."))
        f.addRow(tr("Detail"), self.detail)
        self.auto_threshold = QCheckBox(tr("Automatic"))
        self.auto_threshold.setChecked(True)
        f.addRow(tr("Threshold"), self.auto_threshold)
        self.threshold = QSlider(Qt.Horizontal)
        self.threshold.setRange(1, 255)
        self.threshold.setValue(128)
        self.threshold.setEnabled(False)
        f.addRow("", self.threshold)
        self.invert = QCheckBox(tr("Trace the light areas"))
        f.addRow("", self.invert)
        self.width_spin = LengthSpin(5000.0, 1.0)
        self.width_spin.set_inch(inch)
        self.width_spin.set_mm(width_mm)
        f.addRow(tr("Width"), self.width_spin)
        self.height_label = QLabel("—")
        f.addRow(tr("Height"), self.height_label)
        col.addLayout(f)
        self.stats = QLabel()
        self.stats.setWordWrap(True)
        col.addWidget(self.stats)
        col.addStretch(1)
        box = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.btn_add = box.addButton(tr("Add to the job"), QDialogButtonBox.AcceptRole)
        self.btn_add.setEnabled(False)
        self.btn_add.clicked.connect(self._on_add)
        box.rejected.connect(self.reject)
        col.addWidget(box)
        lay.addWidget(side)
        self._inch = inch

        self.detail.valueChanged.connect(self.retrace)
        self.threshold.valueChanged.connect(self.retrace)
        self.invert.toggled.connect(self.retrace)
        self.auto_threshold.toggled.connect(self._on_auto)
        self.width_spin.valueChanged.connect(self._update_size)

    # ---- input -------------------------------------------------------------
    def _on_choose(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Trace an image"), "", image_filter())
        if path:
            self.load(path)

    def load(self, path: str) -> bool:
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            QMessageBox.warning(self, tr("Trace an image"),
                                tr("This picture could not be read: {reason}",
                                   reason=reader.errorString()))
            return False
        self.set_image(image)
        return True

    def set_image(self, image: QImage) -> None:
        self.gray = gray_of(image)
        if self.auto_threshold.isChecked():
            self.threshold.blockSignals(True)
            self.threshold.setValue(tracer.otsu(self.gray))
            self.threshold.blockSignals(False)
        self.retrace()

    def _on_auto(self, on: bool) -> None:
        self.threshold.setEnabled(not on)
        if on and self.gray is not None:
            self.threshold.setValue(tracer.otsu(self.gray))
        self.retrace()

    # ---- tracing -------------------------------------------------------------
    def retrace(self, *_args) -> None:
        if self.gray is None:
            return
        settings = tracer.settings_for(self.detail.value() / 100.0, self.gray,
                                       self.threshold.value(), self.invert.isChecked())
        self.traced = tracer.trace(self.gray, settings)
        holes = sum(self.traced.holes)
        score = match_score(self.traced) if self.traced.loops else 0.0
        self.stats.setText(tr(
            "{count} outlines (holes: {holes}), {points} points. Match with the picture: "
            "{match} %", count=len(self.traced.loops), holes=holes,
            points=self.traced.node_count, match=round(score * 100)))
        self.btn_add.setEnabled(bool(self.traced.loops))
        self._update_size()
        self._draw_preview()

    def _update_size(self, *_args) -> None:
        if not self.traced or not self.traced.loops:
            self.height_label.setText("—")
            return
        _loops, (_w, h) = tracer.to_mm(self.traced, self.width_spin.mm())
        from .messages import format_length
        self.height_label.setText(format_length(h, self._inch))

    def _draw_preview(self) -> None:
        g = self.gray
        h, w = g.shape
        size = self.preview.size()
        scale = min((size.width() - 20) / w, (size.height() - 20) / h)
        scale = max(scale, 0.05)
        pix = QPixmap(size)
        pix.fill(QColor("#FFFFFF"))
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        dx = (size.width() - w * scale) / 2
        dy = (size.height() - h * scale) / 2
        img = QImage(g.tobytes(), w, h, w, QImage.Format_Grayscale8)
        p.setOpacity(0.35)
        p.drawImage(QRectF(dx, dy, w * scale, h * scale), img)
        p.setOpacity(1.0)
        path = outline_path(self.traced, scale, dx, dy)
        p.fillPath(path, QColor(26, 107, 217, 60))
        p.setPen(QPen(QColor("#1A6BD9"), 1.2))
        p.drawPath(path)
        p.end()
        self.preview.setPixmap(pix)

    def resizeEvent(self, event) -> None:  # noqa: N802 — Qt override
        super().resizeEvent(event)
        if self.traced is not None:
            self._draw_preview()

    def _on_add(self) -> None:
        if not self.traced or not self.traced.loops:
            return
        self.outlines_mm, _size = tracer.to_mm(self.traced, self.width_spin.mm())
        self.accept()
