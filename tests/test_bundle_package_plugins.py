# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Package plugins travel with every installer, and so does what they need.

``ingetrazo.spec`` copied ``plugins/*.py`` only: a package plugin (a
directory with ``__init__.py``, submodules and its own ``i18n/*.json``) ran
from the repo and the Flatpak — which copies the whole tree — and was
silently missing from the AppImage, the tarball, the snap, the macOS app
and the Windows installer, all of which are the one PyInstaller bundle.
The CAM plugin is the first package plugin, and its offset kernel
(``pyclipper``) is a native extension imported only from that plugin, so
analysis never sees it either.
"""
from __future__ import annotations

import re
from pathlib import Path

from core.extensions import bundled_package_files

ROOT = Path(__file__).resolve().parents[1]


def _tree(tmp_path: Path) -> Path:
    plugins = tmp_path / "plugins"
    files = [
        "loose.py",                              # the spec's own glob
        "cam/__init__.py",
        "cam/extract.py",
        "cam/engine/__init__.py",
        "cam/engine/geometry.py",
        "cam/i18n/es.json",
        "cam/i18n/pt-BR.json",
        "cam/i18n/GLOSSARY.md",
        "cam/__pycache__/extract.cpython-314.pyc",
        "cam/engine/__pycache__/geometry.cpython-314.pyc",
        "cam/stray.pyc",
        "cam/.DS_Store",
        "cam/.cache/junk.txt",
        "notapackage/data.json",                 # no __init__: not a plugin
        "_private/__init__.py",                  # dunder/underscore: skipped
    ]
    for rel in files:
        f = plugins / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("")
    return plugins


def test_a_package_plugin_is_bundled_whole_with_its_catalogues(tmp_path):
    plugins = _tree(tmp_path)
    got = {(Path(src).relative_to(plugins).as_posix(), Path(dst).as_posix())
           for src, dst in bundled_package_files(plugins)}
    assert got == {
        ("cam/__init__.py", "plugins/cam"),
        ("cam/extract.py", "plugins/cam"),
        ("cam/engine/__init__.py", "plugins/cam/engine"),
        ("cam/engine/geometry.py", "plugins/cam/engine"),
        ("cam/i18n/es.json", "plugins/cam/i18n"),
        ("cam/i18n/pt-BR.json", "plugins/cam/i18n"),
        ("cam/i18n/GLOSSARY.md", "plugins/cam/i18n"),
    }


def test_no_plugins_folder_means_nothing_to_bundle(tmp_path):
    assert bundled_package_files(tmp_path / "absent") == []


def test_the_spec_bundles_package_plugins_and_pyclipper():
    spec = (ROOT / "ingetrazo.spec").read_text(encoding="utf-8")
    assert "('plugins/*.py'" in spec                    # loose plugins still
    assert "bundled_package_files(ROOT / 'plugins')" in spec
    assert "datas += _plugin_pkg_files" in spec
    assert "collect_submodules('pyclipper')" in spec


def test_pyclipper_is_a_declared_runtime_dependency():
    reqs = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert re.search(r"^pyclipper>=1\.4\s*$", reqs, re.M)


def test_every_workflow_that_lists_its_deps_by_hand_installs_pyclipper():
    """release.yml (AppImage, tarball, snap) and build-windows.yml install
    their packages by name instead of from requirements.txt; each such
    line must not drift from it."""
    lines = [
        (wf.name, line)
        for wf in sorted((ROOT / ".github" / "workflows").glob("*.yml"))
        for line in wf.read_text(encoding="utf-8").splitlines()
        if "pip install" in line and "manifold3d" in line
    ]
    assert len(lines) >= 3, lines
    for name, line in lines:
        assert "pyclipper>=1.4" in line, (name, line)


def test_the_self_check_reports_pyclipper(capsys):
    import main

    main._self_check()
    out = capsys.readouterr().out
    assert re.search(r"pyclipper\s*: found", out), out


def test_the_spec_and_the_self_check_carry_sqlite_for_the_tool_library(capsys):
    spec = (ROOT / "ingetrazo.spec").read_text(encoding="utf-8")
    assert "hiddenimports += ['sqlite3']" in spec
    import main

    main._self_check()
    out = capsys.readouterr().out
    assert re.search(r"sqlite3\s*: found", out), out
    assert re.search(r"CAM tool data\s*: found", out), out


def test_the_self_check_reports_the_cam_plugin(capsys):
    import main

    main._self_check()
    out = capsys.readouterr().out
    assert re.search(r"CAM plugin\s*: found", out), out
    assert re.search(r"CAM catalogues\s*: found", out), out


def test_the_cam_plugin_is_bundled_whole():
    files = {Path(dst, Path(src).name).as_posix()
             for src, dst in bundled_package_files(ROOT / "plugins")}
    for need in ("plugins/cam/__init__.py", "plugins/cam/engine/compiler.py",
                 "plugins/cam/engine/post/grbl.py", "plugins/cam/ui/dock.py",
                 "plugins/cam/i18n/es.json", "plugins/cam/i18n/pt-BR.json",
                 "plugins/cam/toollib/repository.py",
                 "plugins/cam/toollib/catalogs/sorotec-cnc-2026.json"):
        assert need in files, need
