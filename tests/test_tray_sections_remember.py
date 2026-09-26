# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The tray's sections open as the user left them (a user in Brazil, 25-09:
«sempre que eu abro o software ele vem aberta, independente de como eu
deixei… deve vir como eu deixei»)."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

_app = QApplication.instance() or QApplication([])


class ScenesLikePanel(QWidget):
    pass


class LayersLikePanel(QWidget):
    pass


def test_a_folded_section_stays_folded_and_the_others_stay_open():
    from views.tray import _Section
    s = _Section("Scenes", ScenesLikePanel())
    assert s._btn.isChecked()                      # a first start: open
    s._btn.setChecked(False)                       # the user folds it
    again = _Section("Escenas", ScenesLikePanel())  # next start, Spanish UI
    assert not again._btn.isChecked()
    assert again._content.isHidden()
    other = _Section("Layers", LayersLikePanel())
    assert other._btn.isChecked()                  # untouched ones open
    again._btn.setChecked(True)                    # unfolded again…
    assert _Section("Scenes", ScenesLikePanel())._btn.isChecked()
