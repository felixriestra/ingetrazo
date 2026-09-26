# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The tool library beyond the parity corpus: file handling (reading the
bytes a vendor ships), the SQLite store's promises (Trash, provenance,
rollback, recovery, migration) and the bridge into a job."""
from __future__ import annotations

import sqlite3

import pytest

from plugins.cam.engine.models import Tool
from plugins.cam.toollib import csvread, importer, schema
from plugins.cam.toollib.catalog import CatalogProfile, builtin_profiles, normalize
from plugins.cam.toollib.model import LibraryTool, LibraryToolError
from plugins.cam.toollib.repository import (BACKUP_DIR, ToolLibraryRepository, open_library)
from plugins.cam.toollib.resolver import CuttingPreset, MachineLimits


# ---- reading vendor files ---------------------------------------------------------

def test_a_semicolon_sheet_with_comma_decimals_is_read_as_european():
    lines = ["Nr;D;L", "A;6,35;50", "B;3,175;38"]
    assert csvread.sniff_delimiter(lines) == ";"
    assert csvread.sniff_decimal(";", "\n".join(lines)) == ","


def test_a_comma_sheet_cannot_use_comma_decimals():
    lines = ["Nr,D,L", "A,6.35,50"]
    assert csvread.sniff_delimiter(lines) == ","
    assert csvread.sniff_decimal(",", "\n".join(lines)) == "."


def test_a_semicolon_sheet_with_whole_numbers_keeps_the_point():
    assert csvread.sniff_decimal(";", "Nr;D\nA;6\nB;12") == "."


def test_encodings_windows_1252_utf16_with_bom_and_a_utf8_bom():
    text = "Schaftfräser Ø6"
    assert csvread.decode(text.encode("cp1252")) == (text, "cp1252")
    assert csvread.decode(text.encode("utf-16")) == (text, "utf16")
    assert csvread.decode(b"\xef\xbb\xbf" + text.encode("utf-8")) == (text, "utf8")
    # Without a BOM, UTF-16 is never guessed (it would «decode» anything).
    assert csvread.decode(b"ab")[1] == "utf8"


def test_quoted_fields_keep_delimiters_doubled_quotes_and_line_breaks():
    assert csvread.split_line('a;"b;c";"say ""hi""";d', ";") == ["a", "b;c", 'say "hi"', "d"]
    profile = CatalogProfile.from_json({
        "id": "t", "vendorID": "v", "vendorName": "V", "nativeUnits": "mm",
        "columns": [{"headers": ["Name"], "field": "name"}]})
    table = csvread.parse('Name;Note\n"two\nlines";x\n'.encode(), profile)
    assert table.rows == [["two\nlines", "x"]]


def test_header_spellings_collapse_to_one_key():
    assert normalize("Schaft-Ø [mm]") == normalize("schaft ø mm")
    assert normalize("CÓDIGO") == normalize("codigo")


def test_every_built_in_profile_loads():
    ids = [p.id for p in builtin_profiles()]
    assert ids == ["sorotec-cnc-2026", "cmt-industrial-2026", "cmt-193-helical"]


# ---- the store ----------------------------------------------------------------------

@pytest.fixture
def repo(tmp_path):
    r = ToolLibraryRepository(tmp_path / "ToolLibrary.sqlite")
    yield r
    r.close()


def test_a_new_library_has_the_starter_tools_once(tmp_path):
    path = tmp_path / "ToolLibrary.sqlite"
    r = ToolLibraryRepository(path)
    assert len(r.tools()) == 4
    r.delete(r.tools()[0].id)
    r.close()
    again = ToolLibraryRepository(path)
    assert len(again.tools()) == 3               # a deleted starter never comes back
    again.close()


def test_delete_goes_to_the_trash_and_only_the_trash_can_be_purged(repo):
    t = repo.tools()[0]
    repo.purge(t.id)                             # not in the Trash: ignored
    assert repo.tool(t.id) is not None
    repo.delete(t.id)
    assert t.id not in [x.id for x in repo.tools()]
    assert [x.id for x in repo.trashed_tools()] == [t.id]
    repo.restore(t.id)
    assert repo.trashed_tools() == []
    repo.delete(t.id)
    repo.purge(t.id)
    assert repo.tool(t.id) is None


def test_a_duplicate_is_the_users_own_with_its_presets(repo):
    t = repo.tools()[0]
    t.product_id = "X-1"
    repo.update(t)
    repo.upsert_preset(CuttingPreset(tool_id=t.id, name="mdf", origin="vendor",
                                     material_class_id="mdf", spindle_rpm=18000,
                                     feed_xy_mm_min=2000))
    copy = repo.duplicate(t.id, suffix=" (2)")
    assert copy.name == t.name + " (2)" and copy.product_id is None
    (p,) = repo.presets(copy.id)
    assert p.origin == "user" and p.feed_xy_mm_min == 2000


def test_a_vendor_typed_by_name_links_to_the_known_vendor(repo):
    t = repo.insert(LibraryTool(name="x", vendor_name="cmt orange tools", diameter_mm=6))
    assert t.vendor_id == "v-cmt"
    new = repo.insert(LibraryTool(name="y", vendor_name="Acme Bits", diameter_mm=6))
    assert new.vendor_id.startswith("v-") and ("Acme Bits" in dict(repo.vendors()).values())


def test_a_reimport_never_overwrites_a_value_set_by_hand(tmp_path):
    repo = ToolLibraryRepository(tmp_path / "lib.sqlite", seed_starter_tools=False)
    profile = CatalogProfile.from_json({
        "id": "t", "vendorID": "v-cmt", "vendorName": "CMT Orange Tools", "nativeUnits": "mm",
        "delimiter": ";", "decimalSeparator": ",", "columns": [
            {"headers": ["Code"], "field": "productID"},
            {"headers": ["Name"], "field": "name"},
            {"headers": ["D"], "field": "diameter", "unit": "mm"},
            {"headers": ["L"], "field": "fluteLength", "unit": "mm"}],
        "constants": {"toolType": "end_mill", "fluteCount": "2"}})
    importer.commit(importer.plan(b"Code;Name;D;L\nA1;Bit;6;20\n", "a.csv", profile, repo), repo)
    (tool,) = repo.tools()
    tool.flute_length_mm = 18.0                  # the user measured it
    repo.update(tool, mark_user_edited={"flute_length_mm"})
    plan = importer.plan(b"Code;Name;D;L\nA1;Bit;6,5;25\n", "b.csv", profile, repo)
    assert plan.rows[0].disposition == "changed"
    importer.commit(plan, repo)
    (tool,) = repo.tools()
    assert tool.diameter_mm == 6.5 and tool.flute_length_mm == 18.0


def test_an_import_rolls_back_whole(tmp_path):
    repo = ToolLibraryRepository(tmp_path / "lib.sqlite", seed_starter_tools=False)
    profile = next(p for p in builtin_profiles() if p.id == "cmt-193-helical")
    data = "CÓDIGO;Cut Diameter;Cut length;Total length;Shank\n193.160.11;16;42;100;16\n"
    result = importer.commit(importer.plan(data.encode(), "c.csv", profile, repo), repo)
    assert result.inserted == 1 and repo.last_committed_batch() == result.batch_id
    repo.rollback_batch(result.batch_id)
    assert repo.tools() == []


def test_the_library_backs_itself_up_and_recovers_from_damage(tmp_path):
    path = tmp_path / "ToolLibrary.sqlite"
    repo, restored = open_library(path)
    assert restored is None and len(list((tmp_path / BACKUP_DIR).iterdir())) == 1
    repo.insert(LibraryTool(name="mine", diameter_mm=5))
    repo.rotating_backup()
    repo.close()
    path.write_bytes(b"this is not a database" * 100)
    for suffix in ("-wal", "-shm"):
        (tmp_path / (path.name + suffix)).unlink(missing_ok=True)
    repo, restored = open_library(path)
    assert restored == "backup"
    assert "mine" in [t.name for t in repo.tools()]
    assert (tmp_path / "ToolLibrary.sqlite.damaged").exists()
    repo.close()


def test_a_damaged_library_without_backups_starts_fresh_and_keeps_the_old_file(tmp_path):
    path = tmp_path / "ToolLibrary.sqlite"
    path.write_bytes(b"garbage" * 1000)
    repo, restored = open_library(path)
    assert restored == "fresh" and len(repo.tools()) == 4
    assert (tmp_path / "ToolLibrary.sqlite.damaged").read_bytes().startswith(b"garbage")
    repo.close()


def test_backups_rotate_without_touching_2dcams(repo):
    """A library shared with 2DCam: its backups sit in the same folder under
    ``ToolLibrary-<time>.sqlite``; rotation must never delete them."""
    folder = repo.path.parent / BACKUP_DIR
    folder.mkdir(exist_ok=True)
    theirs = folder / "ToolLibrary-2026-01-01T00:00:00Z.sqlite"
    theirs.write_bytes(b"2DCam's")
    for _ in range(13):
        repo.rotating_backup(keep=10)
    assert len(list(folder.glob("ToolLibrary-IngeTrazo-*.sqlite"))) == 10
    assert theirs.exists()


def test_a_version_1_library_is_migrated_without_losing_tools(tmp_path):
    """A file from 2DCam's first schema: no feed curves, no feed factor, no
    form scale mode. Opening it applies migrations 2 and 3 and keeps data."""
    v1 = (schema.DDL
          .replace("INSERT INTO schema_migration (version, name) VALUES (2, 'feed_curve');", "")
          .replace("INSERT INTO schema_migration (version, name) VALUES (3, 'form_scale_mode');",
                   "")
          .replace("    form_scale_mode    TEXT NOT NULL DEFAULT 'keepAngle' CHECK (form_scale_mode "
                   "IN ('keepAngle','fitEnvelope')),\n", "")
          .replace("    -- Feed multiplier vs the base (softwood) curve. CMT's \"Factor Vf\".\n"
                   "    feed_factor          REAL NOT NULL DEFAULT 1.0 CHECK (feed_factor > 0 "
                   "AND feed_factor <= 2.0),\n", ""))
    start = v1.index("-- A vendor feed-vs-depth curve")
    end = v1.index("-- ---------------------------------------------------------------- import QA")
    v1 = v1[:start] + v1[end:]
    assert "feed_curve" not in v1 and "form_scale_mode" not in v1 and "feed_factor" not in v1
    path = tmp_path / "old.sqlite"
    db = sqlite3.connect(path)
    db.executescript(v1)
    # As 2DCam's first version seeded it: the vendors and material classes.
    db.execute("INSERT INTO vendor (id, name) VALUES ('v-cmt', 'CMT Orange Tools')")
    db.execute("INSERT INTO material_class (id, name, family) VALUES ('hardwood', 'Hardwood', "
               "'wood')")
    db.execute("INSERT INTO tool (id, name, tool_type, diameter_mm) VALUES ('T1', 'old', "
               "'end_mill', 6)")
    db.commit()
    db.close()
    repo = ToolLibraryRepository(path, seed_starter_tools=False)
    assert [t.name for t in repo.tools()] == ["old"]
    assert repo.tools()[0].form_scale_mode == "keepAngle"
    assert [c.id for c in repo.feed_curves()] == ["fc-cmt-193"]
    assert next(c for c in repo.material_classes() if c.id == "hardwood").feed_factor == 0.9
    repo.close()


# ---- into a job ----------------------------------------------------------------------

def _resolved(repo, tool, material="mdf"):
    return repo.resolve(tool, material, MachineLimits(max_rpm=24000, min_rpm=6000,
                                                      max_feed=6000))


def test_a_library_tool_goes_into_a_job_with_its_resolved_feeds(repo):
    ball = next(t for t in repo.tools() if t.type == "ball_nose")
    r = _resolved(repo, ball)
    job_tool = ball.to_job_tool(7, r)
    assert (job_tool.number, job_tool.kind, job_tool.diameter) == (7, "ballEndMill", 3.0)
    assert job_tool.cornerRadius == 1.5 and job_tool.is_valid
    assert (job_tool.spindleRPM, job_tool.cuttingFeed) == (r.spindle_rpm, r.feed_xy_mm_min)
    # A generic band is advice: the operation is free to choose its depth.
    assert job_tool.recommendedStepdownProvenance == "ruleEstimate"
    assert job_tool.authoritative_stepdown is None


def test_the_users_own_preset_makes_the_stepdown_binding(repo):
    t = repo.tools()[0]
    repo.upsert_preset(CuttingPreset(tool_id=t.id, name="mine", origin="user",
                                     material_class_id="mdf", spindle_rpm=16000,
                                     feed_xy_mm_min=2000, stepdown_mm=3.0))
    job_tool = t.to_job_tool(1, _resolved(repo, t))
    assert job_tool.authoritative_stepdown == 3.0


def test_an_incomplete_or_form_tool_is_refused_not_guessed():
    t = LibraryTool(name="half", diameter_mm=6, flute_count=2)
    with pytest.raises(LibraryToolError) as e:
        t.to_job_tool(1, None)
    assert e.value.code == "incomplete"
    assert e.value.missing == ["cutting_length", "overall_length"]
    form = LibraryTool(name="ogee", type="form", diameter_mm=20, flute_count=2,
                       flute_length_mm=20, overall_length_mm=60)
    with pytest.raises(LibraryToolError) as e:
        form.to_job_tool(1, None)
    assert e.value.code == "form_tool_unsupported"


def test_v_bits_and_engravers_become_chamfer_mills_that_keep_their_shape(repo):
    v = LibraryTool(name="V", type="engraver", diameter_mm=6, included_angle_deg=30,
                    tip_dia_mm=0.2, flute_count=1, flute_length_mm=5, overall_length_mm=40)
    jt = v.to_job_tool(3, _resolved(repo, v))
    assert (jt.kind, jt.includedAngle, jt.tipDiameter) == ("chamferMill", 30, 0.2)
    back = LibraryTool.from_job_tool(jt)
    assert (back.type, back.included_angle_deg, back.tip_dia_mm) == ("engraver", 30, 0.2)


def test_a_job_tool_saved_to_the_library_keeps_its_geometry():
    jt = Tool(number=2, name="6 mm bull", kind="bullNoseEndMill", diameter=6.0, fluteLength=18,
              overallLength=55, fluteCount=3, cornerRadius=1.0)
    t = LibraryTool.from_job_tool(jt)
    assert (t.type, t.diameter_mm, t.corner_radius_mm, t.flute_count) == ("bull_nose", 6.0, 1.0, 3)
    assert t.can_go_into_a_job


def test_the_cutter_silhouette():
    ball = LibraryTool(name="b", type="ball_nose", diameter_mm=6)
    assert ball.radius_at(0) == 0 and ball.radius_at(3) == 3 and ball.radius_at(10) == 3
    v = LibraryTool(name="v", type="v_bit", diameter_mm=12, included_angle_deg=90, tip_dia_mm=0)
    assert v.radius_at(2) == pytest.approx(2.0) and v.radius_at(20) == 6
    bull = LibraryTool(name="r", type="bull_nose", diameter_mm=10, corner_radius_mm=2)
    assert bull.tip_radius == 3 and bull.radius_at(2) == 5
