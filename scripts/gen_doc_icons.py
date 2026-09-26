# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Regenerate the *document* icons for IngeTrazo's file types (.igz/.dae/.skp/.igcam).

These are the icons the file manager shows on saved/opened files — a white
document sheet with a dog-eared corner, a coloured title bar naming the format,
faint table rows, and the IngeTrazo cube badge in the lower-right corner (the
same visual family as IngePresupuestos' branded ``.db`` icon). One design,
three accents so the formats read apart at a glance while staying clearly part
of the same product.

Outputs (committed to the repo):
  * resources/icons/hicolor/<size>/mimetypes/<mime>.png   ← Linux icon theme
  * resources/icons/mimetypes/ingetrazo-<fmt>.ico         ← Windows DefaultIcon
  * resources/icons/mimetypes/ingetrazo-<fmt>.icns        ← macOS document type
    (for the formats listed in ICNS; needs ``iconutil``, i.e. macOS)

The CAM job (.igcam, plugins/cam) keeps the family but trades the table rows
for its own motif: a stock outline with a pocket's zig-zag toolpath and a
drilled hole, so a job reads apart from a model at a glance.

Freedesktop MIME icon names (``/`` → ``-``) each format resolves to:
  * .igz → application/x-ingetrazo         → application-x-ingetrazo
  * .dae → model/vnd.collada+xml           → model-vnd.collada+xml
  * .skp → application/vnd.sketchup.skp     → application-vnd.sketchup.skp
  * .igcam → application/x-ingetrazo-cam   → application-x-ingetrazo-cam

Needs Inkscape (SVG render) and ImageMagick (composite/resize/.ico) on PATH
(on macOS the Inkscape app bundle is found without it):

    python scripts/gen_doc_icons.py            # every format
    python scripts/gen_doc_icons.py igcam      # just these (others untouched)
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / "resources" / "icons"
BADGE = ICONS / "ingetrazo_256.png"           # the cube mark, composited as badge
HICOLOR = ICONS / "hicolor"
MIME_DIR = ICONS / "mimetypes"

SIZES = [16, 24, 32, 48, 64, 128, 256, 512]
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]     # what we pack into the .ico

# fmt key → (freedesktop mime icon name, label on the sheet, accent colour)
FORMATS = {
    "igz": ("application-x-ingetrazo",         "IGZ", "#2f7fe6"),
    "dae": ("model-vnd.collada+xml",           "DAE", "#1f9e6e"),
    "skp": ("application-vnd.sketchup.skp",     "SKP", "#e0872a"),
    "igcam": ("application-x-ingetrazo-cam",    "CAM", "#7a4fd0"),
}
#: The formats that also get a macOS .icns (a document type the bundle owns).
ICNS = {"igcam"}

#: The CAM job's motif, in place of the table rows: the stock seen from
#: above, a pocket being cleared in zig-zag, a drilled hole — all left of
#: the cube badge, which covers the sheet's lower-right corner.
CAM_MOTIF = """
  <rect x="74" y="132" width="62" height="62" rx="3" fill="#f3eee6"
        stroke="#c9b99f" stroke-width="2.5"/>
  <path d="M83 142 H106 V150 H83 V158 H106 V166 H83 V174 H106 V184 H83"
        fill="none" stroke="{accent}" stroke-width="3.2" stroke-linejoin="round"
        stroke-linecap="round"/>
  <circle cx="123" cy="146" r="6.5" fill="#ffffff" stroke="{accent}" stroke-width="3"/>
"""
TABLE_ROWS = """
  <g fill="#d9dbdf">
    <rect x="78"  y="136" width="104" height="9" rx="4.5"/>
    <rect x="78"  y="156" width="44"  height="9" rx="4.5"/>
    <rect x="132" y="156" width="50"  height="9" rx="4.5"/>
    <rect x="78"  y="176" width="104" height="9" rx="4.5"/>
  </g>
"""


def _sheet_svg(label: str, accent: str, motif: str = TABLE_ROWS) -> str:
    """The document sheet (everything except the raster cube badge)."""
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">
  <defs>
    <linearGradient id="fold" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#eceded"/>
      <stop offset="1" stop-color="#c9cbce"/>
    </linearGradient>
  </defs>

  <!-- Soft sheet shadow -->
  <path d="M62 30 H158 L202 74 V236 H62 Z" fill="#20242b" fill-opacity="0.14"/>

  <!-- Sheet with a dog-eared top-right corner -->
  <path d="M60 24 H156 L200 68 V232 H60 Z"
        fill="#ffffff" stroke="#d3d5d9" stroke-width="2.5" stroke-linejoin="round"/>
  <path d="M156 24 L200 68 H156 Z" fill="url(#fold)" stroke="#c2c4c8"
        stroke-width="2" stroke-linejoin="round"/>

  <!-- Coloured title bar naming the format -->
  <rect x="76" y="86" width="108" height="34" rx="7" fill="{accent}"/>
  <text x="130" y="111" text-anchor="middle"
        font-family="DejaVu Sans, Arial, sans-serif" font-weight="bold"
        font-size="24" letter-spacing="2" fill="#ffffff">{label}</text>

  <!-- The format's motif: faint table rows, or the CAM job's toolpath -->
  {motif.format(accent=accent)}
</svg>
"""


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _inkscape() -> str:
    exe = shutil.which("inkscape")
    mac = Path("/Applications/Inkscape.app/Contents/MacOS/inkscape")
    if not exe and mac.is_file():
        exe = str(mac)
    if not exe:
        sys.exit("Error: Inkscape not found on PATH (needed to render the SVG).")
    return exe


def _magick() -> list[str]:
    if shutil.which("magick"):
        return ["magick"]
    if shutil.which("convert"):
        return ["convert"]   # ImageMagick 6 fallback
    sys.exit("Error: ImageMagick (magick/convert) not found on PATH.")


def _icns(fmt: str, mime: str, tmp: Path) -> None:
    """A macOS .icns from the hicolor PNGs (iconutil's iconset layout)."""
    if not shutil.which("iconutil"):
        print(f"  {fmt}: iconutil not found (not macOS) — .icns skipped")
        return
    iconset = tmp / f"{fmt}.iconset"
    iconset.mkdir()
    for size in (16, 32, 128, 256, 512):
        shutil.copy(HICOLOR / f"{size}x{size}" / "mimetypes" / f"{mime}.png",
                    iconset / f"icon_{size}x{size}.png")
        double = size * 2
        src = HICOLOR / f"{double}x{double}" / "mimetypes" / f"{mime}.png"
        if src.exists():
            shutil.copy(src, iconset / f"icon_{size}x{size}@2x.png")
    _run(["iconutil", "-c", "icns", str(iconset), "-o",
          str(MIME_DIR / f"ingetrazo-{fmt}.icns")])


def build(only=None) -> None:
    if not BADGE.exists():
        sys.exit(f"Error: cube badge missing at {BADGE}")
    ink, mag = _inkscape(), _magick()
    unknown = set(only or ()) - set(FORMATS)
    if unknown:
        sys.exit(f"Error: unknown format(s) {sorted(unknown)}; known: {sorted(FORMATS)}")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for fmt, (mime, label, accent) in FORMATS.items():
            if only and fmt not in only:
                continue
            svg = tmp / f"{fmt}.svg"
            motif = CAM_MOTIF if fmt == "igcam" else TABLE_ROWS
            svg.write_text(_sheet_svg(label, accent, motif), encoding="utf-8")

            # 1) Render the sheet at 512, then composite the cube badge into the
            #    lower-right corner (badge ≈ 40% of the canvas).
            sheet = tmp / f"{fmt}_sheet.png"
            _run([ink, str(svg), "--export-type=png", "-w", "512", "-h", "512",
                  f"--export-filename={sheet}"])

            badge = tmp / f"{fmt}_badge.png"
            _run([*mag, str(BADGE), "-resize", "210x210", str(badge)])

            master = tmp / f"{fmt}_512.png"
            # gravity SouthEast + a small inset so the badge sits inside the sheet
            _run([*mag, str(sheet), str(badge), "-gravity", "SouthEast",
                  "-geometry", "+40+44", "-composite", str(master)])

            # 2) Fan out to every hicolor size.
            for size in SIZES:
                out_dir = HICOLOR / f"{size}x{size}" / "mimetypes"
                out_dir.mkdir(parents=True, exist_ok=True)
                out = out_dir / f"{mime}.png"
                _run([*mag, str(master), "-resize", f"{size}x{size}", str(out)])

            # 3) Windows .ico (multi-resolution) for the DefaultIcon registry key.
            MIME_DIR.mkdir(parents=True, exist_ok=True)
            srcs = [str(HICOLOR / f"{s}x{s}" / "mimetypes" / f"{mime}.png")
                    for s in ICO_SIZES]
            _run([*mag, *srcs, str(MIME_DIR / f"ingetrazo-{fmt}.ico")])
            if fmt in ICNS:
                _icns(fmt, mime, tmp)

            print(f"  {fmt}: {mime}  ✓  ({len(SIZES)} PNG sizes + .ico)")

    print("Document icons regenerated.")


if __name__ == "__main__":
    build(sys.argv[1:] or None)
