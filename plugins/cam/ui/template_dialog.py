# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Choosing a stock template for a new job (:mod:`..templates`).

The built-in templates first, then the user's own; the chosen one is
described beside the list (stock, material, controller, units, tools). The
user's templates can be deleted from here; the built-in ones cannot.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout)

from ..i18n import tr
from ..templates import builtin_templates, user_templates
from . import messages


class TemplateDialog(QDialog):
    def __init__(self, folder, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("New from a stock template"))
        self.resize(560, 360)
        self.folder = folder
        self.templates: list = []
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._describe)
        self.list.itemDoubleClicked.connect(lambda _it: self.accept())
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        self.detail.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.btn_delete = QPushButton(tr("Delete template"))
        self.btn_delete.clicked.connect(self._delete)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.ok = buttons.button(QDialogButtonBox.Ok)
        row = QHBoxLayout()
        row.addWidget(self.list, 1)
        col = QVBoxLayout()
        col.addWidget(self.detail, 1)
        col.addWidget(self.btn_delete)
        row.addLayout(col, 1)
        lay = QVBoxLayout(self)
        lay.addLayout(row, 1)
        lay.addWidget(buttons)
        self._fill()

    def _fill(self, select: int = 0) -> None:
        self.templates = builtin_templates(tr) + user_templates(self.folder)
        self.list.clear()
        for t in self.templates:
            it = QListWidgetItem(t.name if not t.builtin else f"{t.name}  · {tr('built in')}")
            self.list.addItem(it)
        if self.templates:
            self.list.setCurrentRow(min(select, len(self.templates) - 1))
        self._describe(self.list.currentRow())

    def chosen(self):
        r = self.list.currentRow()
        return self.templates[r] if 0 <= r < len(self.templates) else None

    def _describe(self, _row) -> None:
        t = self.chosen()
        self.ok.setEnabled(t is not None)
        self.btn_delete.setEnabled(t is not None and not t.builtin)
        if t is None:
            self.detail.setText("")
            return
        from .dock import material_label
        job = t.state.job
        st = job.stock
        inch = job.is_inch
        size = " × ".join(messages.format_length(v, inch)
                          for v in (st.width, st.depth, st.height))
        tools = ", ".join(f"T{x.number} {x.name}" for x in sorted(job.tools,
                                                                    key=lambda x: x.number))
        self.detail.setText("\n".join([
            tr("Stock: {size}", size=size),
            tr("Material: {material}", material=material_label(st.material)),
            tr("Controller: {controller}", controller={"grbl": "GRBL",
                                                       "linuxcnc": "LinuxCNC"}.get(
                job.post.controller, job.post.controller)),
            tr("Units: {units}", units=tr("Inches") if inch else tr("Millimetres")),
            tr("Tools: {tools}", tools=tools or "—"),
        ]))

    def _delete(self) -> None:
        t = self.chosen()
        if t is None or t.builtin:
            return
        box = QMessageBox(QMessageBox.Question, tr("CAM"),
                          tr("Delete the template «{name}»?", name=t.name),
                          QMessageBox.Yes | QMessageBox.No, self)
        box.setOption(QMessageBox.Option.DontUseNativeDialog, True)
        if box.exec() != QMessageBox.Yes:
            return
        try:
            t.path.unlink()
        except OSError:
            pass
        self._fill(self.list.currentRow())
