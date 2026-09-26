# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The tool library on disk — 2DCam's ``ToolLibraryRepository``.

One SQLite file (:mod:`.schema`), opened with the safety settings 2DCam
uses: write-ahead log, ``synchronous=FULL``, foreign keys on. Every write
that touches more than one row runs in a transaction, and transactions
nest (an import commits a whole batch; the per-tool calls inside it open
their own) — only the outermost one is real, as SQLite has no nested
``BEGIN``.

A tool the user deletes goes to the Trash (``deleted_at``) and can be
restored; only a purge from the Trash removes the row, and the
``change_log`` trigger still remembers it. A catalogue import is one
batch: :meth:`rollback_batch` undoes it whole.

:func:`open_library` is the way in: it checks the file, restores the newest
backup when the file is damaged (or moves a damaged file aside when there
is no backup), and takes a fresh rotating backup.
"""
from __future__ import annotations

import datetime as _dt
import shutil
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ..engine.models import new_id
from . import schema
from .model import FormPoint, LibraryTool
from .resolver import (ChiploadRule, CuttingPreset, FeedCurve, MaterialClass, MachineLimits,
                       resolve)

FILE_NAME = "ToolLibrary.sqlite"
BACKUP_DIR = "Backups"
BACKUP_PREFIX = "ToolLibrary-"

_TOOL_COLUMNS = """
    t.id, t.vendor_id, t.product_id, t.product_url, t.name, t.series, t.tool_type,
    t.diameter_mm, t.corner_radius_mm, t.included_angle_deg, t.tip_dia_mm,
    t.flute_count, t.flute_length_mm, t.shank_dia_mm, t.overall_length_mm, t.neck_length_mm,
    t.chip_direction, t.substrate, t.coating, t.display_units, t.nominal_label, t.notes,
    t.created_at, t.updated_at, t.deleted_at, t.form_scale_mode, v.name
"""

_PRESET_COLUMNS = """
    id, tool_id, material_id, material_class_id, machine_id, name, origin, derivation,
    confidence, chipload_fz_mm, vc_m_min, spindle_rpm, feed_xy_mm_min, feed_z_mm_min,
    ramp_feed_mm_min, stepdown_mm, stepover_mm, clearance_stepover_mm, cut_direction,
    air_blast, notes
"""

#: A fresh library's tools, so a first run has something to add to a job
#: (2DCam's starter set). Seeded once; never brought back once deleted.
STARTER_TOOLS = (
    dict(name="16 mm End Mill", type="end_mill", diameter_mm=16, flute_count=2,
         flute_length_mm=32, shank_dia_mm=16, overall_length_mm=80, substrate="solid_carbide"),
    dict(name="8 mm End Mill", type="end_mill", diameter_mm=8, flute_count=2,
         flute_length_mm=25, shank_dia_mm=8, overall_length_mm=63, substrate="solid_carbide"),
    dict(name="6 mm End Mill", type="end_mill", diameter_mm=6, flute_count=2,
         flute_length_mm=20, shank_dia_mm=6, overall_length_mm=50, substrate="solid_carbide"),
    dict(name="3 mm Ball Nose", type="ball_nose", diameter_mm=3, corner_radius_mm=1.5,
         flute_count=2, flute_length_mm=12, shank_dia_mm=6, overall_length_mm=38,
         substrate="solid_carbide"),
)


class LibraryError(Exception):
    """``open`` (the file cannot be opened), ``sql`` (a statement failed) or
    ``corrupt`` (damaged and no backup). ``detail`` is SQLite's message."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def open_library(path, seed_starter_tools: bool = True) -> tuple:
    """``(repository, restored)`` for the library at ``path``. A damaged
    file is replaced by the newest backup (``restored`` is then
    ``"backup"``) or, with no backup, moved aside as ``*.damaged`` and a
    fresh library started (``"fresh"``). A healthy file gets a rotating
    backup on the way in."""
    path = Path(path)
    restored = None
    try:
        repo = ToolLibraryRepository(path, seed_starter_tools)
        healthy = repo.quick_check()
    except (LibraryError, sqlite3.DatabaseError):
        repo, healthy = None, False
    if not healthy:
        if repo is not None:
            repo.close()
        backups = sorted((path.parent / BACKUP_DIR).glob(BACKUP_PREFIX + "*.sqlite"), reverse=True)
        for suffix in ("-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)
        if path.exists():
            path.replace(path.with_name(path.name + ".damaged"))
        if backups:
            shutil.copy2(backups[0], path)
            restored = "backup"
        else:
            restored = "fresh"
        repo = ToolLibraryRepository(path, seed_starter_tools)
    try:
        repo.rotating_backup()
    except (LibraryError, OSError):
        pass                        # a backup that fails must not stop the library
    return repo, restored


class ToolLibraryRepository:
    """The library file. Not thread-safe: use it from the thread that made
    it (the UI thread), as the rest of the plugin does."""

    def __init__(self, path, seed_starter_tools: bool = True):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not self.path.exists()
        try:
            self._db = sqlite3.connect(str(self.path), isolation_level=None)
        except sqlite3.Error as exc:
            raise LibraryError("open", str(exc)) from exc
        self._depth = 0
        try:
            for pragma in ("journal_mode = WAL", "synchronous = FULL", "foreign_keys = ON",
                           "busy_timeout = 5000"):
                self._db.execute(f"PRAGMA {pragma}")
            if is_new:
                self._script(schema.DDL + "\n" + schema.SEED)
            else:
                self._migrate()
            if seed_starter_tools:
                self.seed_starter_tools()
        except BaseException:
            # A damaged file: let go of it, so open_library can move it aside
            # (Windows refuses to rename an open file).
            self._db.close()
            raise

    def close(self) -> None:
        self._db.close()

    # ---- plumbing ------------------------------------------------------------
    def _script(self, sql: str) -> None:
        """Run a multi-statement script as ONE transaction. (``executescript``
        commits whatever is pending first, so it cannot join an outer one.)"""
        try:
            self._db.executescript("BEGIN IMMEDIATE;\n" + sql + "\nCOMMIT;")
        except sqlite3.Error as exc:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            raise LibraryError("sql", str(exc)) from exc

    def _migrate(self) -> None:
        """Apply every migration above the stored version, each in its own
        transaction: a half-applied upgrade cannot happen."""
        row = self._db.execute("SELECT MAX(version) FROM schema_migration").fetchone()
        current = row[0] if row and row[0] is not None else 1
        for version, name, sql in schema.MIGRATIONS:
            if version > current:
                self._script(sql + f"\nINSERT INTO schema_migration (version, name) "
                             f"VALUES ({int(version)}, '{name}');")

    @contextmanager
    def transaction(self):
        """``with repo.transaction():`` — nested ones join the outermost."""
        if self._depth:
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            return
        self._db.execute("BEGIN IMMEDIATE")
        self._depth = 1
        try:
            yield
        except BaseException:
            self._depth = 0
            self._db.execute("ROLLBACK")
            raise
        self._depth = 0
        self._db.execute("COMMIT")

    def _run(self, sql: str, params=()) -> sqlite3.Cursor:
        try:
            return self._db.execute(sql, tuple(params))
        except sqlite3.Error as exc:
            raise LibraryError("sql", f"{exc} — {sql.strip()[:80]}") from exc

    # ---- integrity and backup ----------------------------------------------
    def quick_check(self) -> bool:
        """False means: restore the newest backup."""
        row = self._run("PRAGMA quick_check").fetchone()
        return bool(row) and row[0] == "ok"

    def backup(self, target) -> None:
        """A consistent snapshot of the whole file."""
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.unlink(missing_ok=True)
        self._run("VACUUM INTO ?", (str(target),))

    def rotating_backup(self, keep: int = 10) -> Path:
        """A snapshot in ``Backups/`` next to the file, keeping the newest
        ``keep``. Taken on open and after every import."""
        folder = self.path.parent / BACKUP_DIR
        stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H-%M-%S.%fZ")
        target = folder / f"{BACKUP_PREFIX}{stamp}.sqlite"
        self.backup(target)
        for stale in sorted(folder.glob(BACKUP_PREFIX + "*.sqlite"), reverse=True)[keep:]:
            stale.unlink(missing_ok=True)
        return target

    # ---- tools ----------------------------------------------------------------
    @staticmethod
    def _read_tool(r) -> LibraryTool:
        return LibraryTool(
            id=r[0], vendor_id=r[1], product_id=r[2], product_url=r[3], name=r[4] or "",
            series=r[5], type=r[6] or "end_mill", diameter_mm=r[7], corner_radius_mm=r[8],
            included_angle_deg=r[9], tip_dia_mm=r[10], flute_count=r[11], flute_length_mm=r[12],
            shank_dia_mm=r[13], overall_length_mm=r[14], neck_length_mm=r[15],
            chip_direction=r[16], substrate=r[17], coating=r[18], display_units=r[19] or "mm",
            nominal_label=r[20], notes=r[21], created_at=r[22], updated_at=r[23],
            deleted_at=r[24], form_scale_mode=r[25] or "keepAngle", vendor_name=r[26])

    def _with_profiles(self, tools: list) -> list:
        """Attach the drawn silhouette to form tools (a second pass keeps the
        row mapping plain)."""
        for t in tools:
            if t.type == "form":
                t.form_points = [FormPoint(radius=r, rise=z) for z, r in self._run(
                    "SELECT z_mm, radius_mm FROM tool_profile_point WHERE tool_id = ? "
                    "ORDER BY idx", (t.id,))]
        return tools

    def _select_tools(self, where: str, params=(), order="v.name, t.name COLLATE NOCASE"):
        rows = self._run(f"SELECT {_TOOL_COLUMNS} FROM tool t "
                         f"LEFT JOIN vendor v ON v.id = t.vendor_id {where} ORDER BY {order}",
                         params).fetchall()
        return self._with_profiles([self._read_tool(r) for r in rows])

    def tools(self, include_deleted: bool = False) -> list:
        return self._select_tools("" if include_deleted else "WHERE t.deleted_at IS NULL")

    def trashed_tools(self) -> list:
        return self._select_tools("WHERE t.deleted_at IS NOT NULL", order="t.deleted_at DESC")

    def tool(self, tool_id: str) -> LibraryTool | None:
        found = self._select_tools("WHERE t.id = ?", (tool_id,))
        return found[0] if found else None

    def tool_by_product(self, vendor_id: str, product_id: str) -> LibraryTool | None:
        found = self._select_tools(
            "WHERE t.vendor_id = ? AND t.product_id = ? AND t.deleted_at IS NULL",
            (vendor_id, product_id))
        return found[0] if found else None

    def _values(self, t: LibraryTool, vendor_id) -> tuple:
        return (vendor_id, t.product_id, t.product_url, t.name, t.series, t.type,
                t.diameter_mm, t.corner_radius_mm, t.included_angle_deg, t.tip_dia_mm,
                t.flute_count, t.flute_length_mm, t.shank_dia_mm, t.overall_length_mm,
                t.neck_length_mm, t.chip_direction, t.substrate, t.coating, t.display_units,
                t.nominal_label, t.notes, t.form_scale_mode)

    def insert(self, tool: LibraryTool, source_id=None, batch_id=None) -> LibraryTool:
        with self.transaction():
            vendor_id = self._vendor_id_for(tool)
            self._run("""
                INSERT INTO tool (vendor_id, product_id, product_url, name, series, tool_type,
                    diameter_mm, corner_radius_mm, included_angle_deg, tip_dia_mm, flute_count,
                    flute_length_mm, shank_dia_mm, overall_length_mm, neck_length_mm,
                    chip_direction, substrate, coating, display_units, nominal_label, notes,
                    form_scale_mode, id, source_id, batch_id, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                        datetime('now'),datetime('now'))
                """, self._values(tool, vendor_id) + (tool.id, source_id, batch_id))
            self._write_form_points(tool)
        tool.vendor_id = vendor_id
        return tool

    def update(self, tool: LibraryTool, mark_user_edited=()) -> None:
        """Save ``tool``. Every field named in ``mark_user_edited`` is
        recorded as the user's (``field_provenance``), which is what keeps a
        later catalogue re-import from overwriting a value set by hand."""
        with self.transaction():
            vendor_id = self._vendor_id_for(tool)
            self._run("""
                UPDATE tool SET vendor_id=?, product_id=?, product_url=?, name=?, series=?,
                    tool_type=?, diameter_mm=?, corner_radius_mm=?, included_angle_deg=?,
                    tip_dia_mm=?, flute_count=?, flute_length_mm=?, shank_dia_mm=?,
                    overall_length_mm=?, neck_length_mm=?, chip_direction=?, substrate=?,
                    coating=?, display_units=?, nominal_label=?, notes=?, form_scale_mode=?,
                    updated_at=datetime('now')
                 WHERE id=?
                """, self._values(tool, vendor_id) + (tool.id,))
            self._write_form_points(tool)
            for fld in mark_user_edited:
                self.record_provenance("tool", tool.id, fld, "user", None)
        tool.vendor_id = vendor_id

    def _write_form_points(self, tool: LibraryTool) -> None:
        self._run("DELETE FROM tool_profile_point WHERE tool_id = ?", (tool.id,))
        for i, p in enumerate(tool.form_points):
            self._run("INSERT INTO tool_profile_point (tool_id, idx, z_mm, radius_mm) "
                      "VALUES (?,?,?,?)", (tool.id, i, max(0.0, p.rise), max(0.0, p.radius)))

    def _vendor_id_for(self, tool: LibraryTool):
        """The vendor row for a tool: its explicit id, else an existing
        vendor with that name (so a hand-typed «CMT Orange Tools» links to
        the real CMT and inherits its feed curves), else a new vendor."""
        if tool.vendor_id:
            return tool.vendor_id
        name = (tool.vendor_name or "").strip()
        if not name:
            return None
        row = self._run("SELECT id FROM vendor WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
        if row:
            return row[0]
        vid = "v-" + new_id()[:8].lower()
        self._run("INSERT INTO vendor (id, name, native_units) VALUES (?, ?, 'mm')", (vid, name))
        return vid

    def delete(self, tool_id: str) -> None:
        """To the Trash: recoverable."""
        self._run("UPDATE tool SET deleted_at=datetime('now'), updated_at=datetime('now') "
                  "WHERE id=?", (tool_id,))

    def restore(self, tool_id: str) -> None:
        self._run("UPDATE tool SET deleted_at=NULL, updated_at=datetime('now') WHERE id=?",
                  (tool_id,))

    def purge(self, tool_id: str) -> None:
        """For good — from the Trash only."""
        self._run("DELETE FROM tool WHERE id=? AND deleted_at IS NOT NULL", (tool_id,))

    def duplicate(self, tool_id: str, suffix: str = " copy") -> LibraryTool | None:
        """A copy with its presets. The copy is no longer the vendor's part
        (no product id) and its presets become the user's."""
        original = self.tool(tool_id)
        if original is None or original.deleted_at:
            return None
        with self.transaction():
            presets = self.presets(tool_id)
            copy = LibraryTool(**{k: v for k, v in vars(original).items()
                                  if k not in ("id", "created_at", "updated_at")})
            copy.name += suffix
            copy.product_id = None
            self.insert(copy)
            for p in presets:
                p.id = new_id()
                p.tool_id = copy.id
                p.origin = "user"
                self.upsert_preset(p)
        return copy

    def seed_starter_tools(self) -> None:
        """The starter set, once per library file — guarded by a setting, so
        tools the user deletes never come back."""
        if self._run("SELECT 1 FROM app_setting WHERE key = 'starter_tools_seeded'").fetchone():
            return
        with self.transaction():
            for spec in STARTER_TOOLS:
                t = self.insert(LibraryTool(**spec))
                self.add_to_group(t.id, "g-mine")
            self._run("INSERT OR REPLACE INTO app_setting (key, value) "
                      "VALUES ('starter_tools_seeded', '1')")

    # ---- presets --------------------------------------------------------------
    def presets(self, tool_id: str | None = None) -> list:
        where = "AND tool_id = ?" if tool_id else ""
        rows = self._run(f"SELECT {_PRESET_COLUMNS} FROM cutting_preset "
                         f"WHERE deleted_at IS NULL {where}",
                         (tool_id,) if tool_id else ()).fetchall()
        return [CuttingPreset(
            id=r[0], tool_id=r[1], material_id=r[2], material_class_id=r[3], machine_id=r[4],
            name=r[5] or "", origin=r[6] or "user", derivation=r[7] or "explicit",
            confidence=r[8] if r[8] is not None else 3, chipload_mm=r[9], vc_m_min=r[10],
            spindle_rpm=r[11], feed_xy_mm_min=r[12], feed_z_mm_min=r[13], ramp_feed_mm_min=r[14],
            stepdown_mm=r[15], stepover_mm=r[16], clearance_stepover_mm=r[17],
            cut_direction=r[18], air_blast=bool(r[19]), notes=r[20]) for r in rows]

    def upsert_preset(self, p: CuttingPreset, source_id=None, batch_id=None) -> None:
        self._run("""
            INSERT INTO cutting_preset (id, tool_id, material_id, material_class_id, machine_id,
                name, origin, derivation, confidence, chipload_fz_mm, vc_m_min, spindle_rpm,
                feed_xy_mm_min, feed_z_mm_min, ramp_feed_mm_min, stepdown_mm, stepover_mm,
                clearance_stepover_mm, cut_direction, air_blast, notes, source_id, batch_id,
                created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                material_id=excluded.material_id, material_class_id=excluded.material_class_id,
                machine_id=excluded.machine_id, name=excluded.name, origin=excluded.origin,
                derivation=excluded.derivation, confidence=excluded.confidence,
                chipload_fz_mm=excluded.chipload_fz_mm, vc_m_min=excluded.vc_m_min,
                spindle_rpm=excluded.spindle_rpm, feed_xy_mm_min=excluded.feed_xy_mm_min,
                feed_z_mm_min=excluded.feed_z_mm_min, ramp_feed_mm_min=excluded.ramp_feed_mm_min,
                stepdown_mm=excluded.stepdown_mm, stepover_mm=excluded.stepover_mm,
                clearance_stepover_mm=excluded.clearance_stepover_mm,
                cut_direction=excluded.cut_direction, air_blast=excluded.air_blast,
                notes=excluded.notes, updated_at=datetime('now')
            """, (p.id, p.tool_id, p.material_id, p.material_class_id, p.machine_id, p.name,
                  p.origin, p.derivation, p.confidence, p.chipload_mm, p.vc_m_min, p.spindle_rpm,
                  p.feed_xy_mm_min, p.feed_z_mm_min, p.ramp_feed_mm_min, p.stepdown_mm,
                  p.stepover_mm, p.clearance_stepover_mm, p.cut_direction, int(p.air_blast),
                  p.notes, source_id, batch_id))

    def delete_preset(self, preset_id: str) -> None:
        self._run("UPDATE cutting_preset SET deleted_at=datetime('now') WHERE id=?", (preset_id,))

    # ---- reference data -------------------------------------------------------
    def material_classes(self) -> list:
        return [MaterialClass(id=r[0], name=r[1], family=r[2], melts=bool(r[3]),
                              prefers_downcut=bool(r[4]), max_rpm_hint=r[5],
                              feed_factor=r[6] if r[6] is not None else 1.0)
                for r in self._run("SELECT id, name, family, melts, prefers_downcut, "
                                   "max_rpm_hint, feed_factor FROM material_class "
                                   "ORDER BY sort_order")]

    def feed_curves(self) -> list:
        return [FeedCurve(id=r[0], vendor_id=r[1], series=r[2], tool_type=r[3],
                          dia_min_mm=r[4], dia_max_mm=r[5], rpm=r[6], doc_lo_mm=r[7],
                          feed_lo_mm_min=r[8], doc_hi_mm=r[9], feed_hi_mm_min=r[10],
                          cross_grain_factor=r[11] if r[11] is not None else 0.7,
                          chip_min_mm=r[12], chip_max_mm=r[13])
                for r in self._run("""
                    SELECT id, vendor_id, series, tool_type, dia_min_mm, dia_max_mm, rpm,
                           doc_lo_mm, feed_lo_mm_min, doc_hi_mm, feed_hi_mm_min,
                           cross_grain_factor, chip_min_mm, chip_max_mm FROM feed_curve""")]

    def chipload_rules(self) -> list:
        return [ChiploadRule(id=r[0], material_class_id=r[1], tool_type=r[2], vendor_id=r[3],
                             dia_min_mm=r[4], dia_max_mm=r[5], fz_min_mm=r[6], fz_typ_mm=r[7],
                             fz_max_mm=r[8], rpm_min=r[9], rpm_max=r[10],
                             max_stepdown_x_dia=r[11])
                for r in self._run("""
                    SELECT id, material_class_id, tool_type, vendor_id, dia_min_mm, dia_max_mm,
                           fz_min_mm, fz_typ_mm, fz_max_mm, rpm_min, rpm_max, max_stepdown_x_dia
                      FROM chipload_rule""")]

    def vendors(self) -> list:
        """``[(id, name)]``, by name."""
        return [tuple(r) for r in self._run("SELECT id, name FROM vendor ORDER BY name "
                                            "COLLATE NOCASE")]

    def resolve(self, tool: LibraryTool, material_class_id: str,
                machine: MachineLimits | None = None, depth_of_cut_mm: float | None = None,
                material_id: str | None = None):
        """The whole point of the library: what should this tool cut this
        material at? None when the class is unknown or the tool too
        incomplete to derive anything."""
        mc = next((m for m in self.material_classes() if m.id == material_class_id), None)
        if mc is None:
            return None
        return resolve(tool, mc, material_id=material_id, machine=machine,
                       presets=self.presets(tool.id), rules=self.chipload_rules(),
                       feed_curves=self.feed_curves(), depth_of_cut_mm=depth_of_cut_mm)

    # ---- import support -------------------------------------------------------
    def upsert_data_source(self, vendor_id, kind: str, title: str, file_hash: str,
                           reliability: int) -> str:
        row = self._run("SELECT id FROM data_source WHERE file_hash=? AND kind=?",
                        (file_hash, kind)).fetchone()
        if row:
            return row[0]
        sid = new_id()
        self._run("INSERT INTO data_source (id, vendor_id, kind, title, file_hash, retrieved_at, "
                  "reliability) VALUES (?,?,?,?,?,datetime('now'),?)",
                  (sid, vendor_id, kind, title, file_hash, reliability))
        return sid

    def create_batch(self, batch_id: str, source_id: str, row_count: int) -> None:
        self._run("INSERT INTO import_batch (id, source_id, status, row_count) "
                  "VALUES (?,?,'staged',?)", (batch_id, source_id, row_count))

    def finish_batch(self, batch_id: str, status: str, committed: int) -> None:
        self._run("UPDATE import_batch SET status=?, committed_count=?, "
                  "finished_at=datetime('now') WHERE id=?", (status, committed, batch_id))

    def stage(self, batch_id: str, row_index: int, raw_json: str, status: str, tool_id) -> None:
        self._run("INSERT INTO staging_record (id, batch_id, row_index, raw_json, status, tool_id) "
                  "VALUES (?,?,?,?,?,?)", (new_id(), batch_id, row_index, raw_json, status,
                                           tool_id))

    def record_issue(self, issue, batch_id: str) -> None:
        self._run("INSERT INTO validation_issue (id, batch_id, entity_type, entity_id, severity, "
                  "code, field, raw_value, message) VALUES (?,?,'staging_record',?,?,?,?,?,?)",
                  (new_id(), batch_id, f"{batch_id}#{issue.row_index}", issue.severity,
                   issue.code, issue.field, issue.raw_value, issue.code))

    def record_provenance(self, entity: str, entity_id: str, fld: str, origin: str,
                          source_id) -> None:
        self._run("""
            INSERT INTO field_provenance (entity_type, entity_id, field, origin, source_id,
                                          updated_at)
            VALUES (?,?,?,?,?,datetime('now'))
            ON CONFLICT(entity_type, entity_id, field) DO UPDATE SET
                origin=excluded.origin, source_id=excluded.source_id,
                updated_at=excluded.updated_at
            """, (entity, entity_id, fld, origin, source_id))

    def field_origins(self, entity: str, entity_id: str) -> dict:
        """``{field: origin}`` for the fields that are NOT verbatim from
        their source (estimated, derived, the user's)."""
        return {r[0]: r[1] for r in self._run(
            "SELECT field, origin FROM field_provenance WHERE entity_type=? AND entity_id=?",
            (entity, entity_id))}

    def user_edited_fields(self, entity: str, entity_id: str) -> set:
        """Fields set by hand: a catalogue re-import never overwrites them."""
        return {f for f, o in self.field_origins(entity, entity_id).items() if o == "user"}

    def rollback_batch(self, batch_id: str) -> None:
        """Undo an import: the tools it created go; tools it only updated
        stay (their earlier values are in the change log)."""
        with self.transaction():
            self._run("DELETE FROM tool WHERE batch_id = ?", (batch_id,))
            self.finish_batch(batch_id, "rolled_back", 0)

    def last_committed_batch(self) -> str | None:
        row = self._run("SELECT id FROM import_batch WHERE status='committed' "
                        "ORDER BY finished_at DESC, rowid DESC LIMIT 1").fetchone()
        return row[0] if row else None

    def add_to_group(self, tool_id: str, group_id: str) -> None:
        self._run("INSERT OR IGNORE INTO tool_group_member (group_id, tool_id) VALUES (?,?)",
                  (group_id, tool_id))
