# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Every file type IngeTrazo claims has its document icon wherever the
platforms look for it: the freedesktop MIME package names an icon that
exists in every hicolor size, the Windows installer ships each .ico it
points the registry at, and a macOS document type's icon is an .icns the
bundle carries.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICONS = ROOT / "resources" / "icons"
SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
NS = {"m": "http://www.freedesktop.org/standards/shared-mime-info"}


def test_every_mime_type_has_its_icon_in_every_size():
    tree = ET.parse(ROOT / "resources" / "mime" / "ingetrazo.xml")
    names = [t.find("m:icon", NS).get("name") for t in tree.getroot().findall("m:mime-type", NS)]
    assert "application-x-ingetrazo-cam" in names          # the CAM job's own
    for name in names:
        for size in SIZES:
            png = ICONS / "hicolor" / f"{size}x{size}" / "mimetypes" / f"{name}.png"
            assert png.is_file(), png
            assert png.read_bytes()[:4] == b"\x89PNG"


def test_windows_installer_ships_every_icon_it_registers():
    iss = (ROOT / "installer" / "ingetrazo.iss").read_text(encoding="utf-8")
    shipped = set(re.findall(r'Source: "\.\.\\resources\\icons\\mimetypes\\([^"]+)"', iss))
    used = set(re.findall(r'\{app\}\\(ingetrazo-[a-z]+\.ico)', iss))
    assert "ingetrazo-igcam.ico" in used
    assert used <= shipped
    for name in shipped:
        assert (ICONS / "mimetypes" / name).is_file(), name


def test_macos_document_type_icons_exist_and_are_bundled():
    spec = (ROOT / "ingetrazo.spec").read_text(encoding="utf-8")
    for name in re.findall(r"'CFBundleTypeIconFile': '([^']+)'", spec):
        assert (ICONS / "mimetypes" / name).is_file(), name
        assert (ICONS / "mimetypes" / name).read_bytes()[:4] == b"icns"
    assert "('resources/icons/mimetypes/*.icns', '.')" in spec
