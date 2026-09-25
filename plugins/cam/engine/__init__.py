# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The CAM engine: a Python port of 2DCam's ``TwoDCamCore`` (v1 operations).

Pure Python + pyclipper + numpy. **No Qt and no translations** — the engine
works in float64 millimetres and reports problems as issue codes
(:mod:`.issues`); the plugin's UI does the rest. ``tests/test_cam_engine_
isolation.py`` keeps it that way.
"""
