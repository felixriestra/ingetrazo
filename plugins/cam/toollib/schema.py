# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The tool library's SQLite schema — 2DCam's ``ToolLibrarySchema.swift``,
copied verbatim.

Verbatim on purpose: the same file layout means a ``ToolLibrary.sqlite``
made by 2DCam opens here unchanged, and one made here opens in 2DCam, so a
shop with Macs and Linux machines keeps one tool cabinet. Change it only in
step with 2DCam, as a new numbered migration; never edit :data:`DDL` in
place for an existing version.

Design notes carried over from 2DCam:

- lengths are mm, feeds mm/min, whatever a catalogue printed;
- a tool has no cutting data of its own — ``cutting_preset`` rows scope it
  to a material (or a material class) and optionally a machine, and
  ``chipload_rule`` / ``feed_curve`` rows estimate it when there is none;
- soft delete (``deleted_at``) everywhere a user can delete: the Trash;
- ``field_provenance`` is sparse — a row only for a value that is NOT
  verbatim from its source (estimated, derived or hand-edited), and a
  catalogue re-import never overwrites a ``user`` field;
- the seeded chipload bands are PLACEHOLDERS (source reliability 2): what
  is derived from them must be shown as an estimate.
"""
from __future__ import annotations

#: Highest migration number :data:`DDL` already includes.
VERSION = 3

#: Run once, on an empty database file.
DDL = """\
CREATE TABLE schema_migration (
    version     INTEGER NOT NULL PRIMARY KEY,
    name        TEXT    NOT NULL,
    applied_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO schema_migration (version, name) VALUES (1, 'initial');
INSERT INTO schema_migration (version, name) VALUES (2, 'feed_curve');
INSERT INTO schema_migration (version, name) VALUES (3, 'form_scale_mode');

CREATE TABLE app_setting (key TEXT PRIMARY KEY, value TEXT);
INSERT INTO app_setting (key, value) VALUES ('canonical_units', 'metric'), ('display_units', 'mm');

-- ---------------------------------------------------------------- provenance
CREATE TABLE vendor (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    country      TEXT,
    website      TEXT,
    native_units TEXT NOT NULL DEFAULT 'mm' CHECK (native_units IN ('mm','inch')),
    notes        TEXT
);

CREATE TABLE data_source (
    id           TEXT PRIMARY KEY,
    vendor_id    TEXT REFERENCES vendor(id) ON DELETE SET NULL,
    kind         TEXT NOT NULL CHECK (kind IN
                    ('catalog_pdf','web_page','csv','fusion_json','vectric_vtdb',
                     'manual','derived','generic')),
    title        TEXT NOT NULL,
    url          TEXT,
    file_hash    TEXT,
    retrieved_at TEXT,
    reliability  INTEGER NOT NULL DEFAULT 3 CHECK (reliability BETWEEN 1 AND 5),
    notes        TEXT
);

CREATE TABLE import_batch (
    id              TEXT PRIMARY KEY,
    source_id       TEXT REFERENCES data_source(id) ON DELETE SET NULL,
    started_at      TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at     TEXT,
    status          TEXT NOT NULL DEFAULT 'staged'
                    CHECK (status IN ('staged','committed','rolled_back','failed')),
    row_count       INTEGER NOT NULL DEFAULT 0,
    committed_count INTEGER NOT NULL DEFAULT 0,
    notes           TEXT
);

CREATE TABLE tool (
    id                 TEXT PRIMARY KEY,
    vendor_id          TEXT REFERENCES vendor(id) ON DELETE SET NULL,
    product_id         TEXT,
    product_url        TEXT,
    name               TEXT NOT NULL,
    series             TEXT,
    tool_type          TEXT NOT NULL CHECK (tool_type IN
                        ('end_mill','ball_nose','bull_nose','v_bit','engraver',
                         'tapered_ball','drill','chamfer','surfacing','form','drag_knife')),
    diameter_mm        REAL    CHECK (diameter_mm IS NULL OR (diameter_mm > 0 AND diameter_mm <= 200)),
    corner_radius_mm   REAL    CHECK (corner_radius_mm IS NULL OR corner_radius_mm >= 0),
    included_angle_deg REAL    CHECK (included_angle_deg IS NULL OR (included_angle_deg > 0 AND included_angle_deg < 180)),
    tip_dia_mm         REAL    CHECK (tip_dia_mm IS NULL OR tip_dia_mm >= 0),
    flute_count        INTEGER CHECK (flute_count IS NULL OR (flute_count >= 1 AND flute_count <= 12)),
    flute_length_mm    REAL    CHECK (flute_length_mm IS NULL OR flute_length_mm > 0),
    shank_dia_mm       REAL    CHECK (shank_dia_mm IS NULL OR shank_dia_mm > 0),
    overall_length_mm  REAL    CHECK (overall_length_mm IS NULL OR overall_length_mm > 0),
    neck_length_mm     REAL    CHECK (neck_length_mm IS NULL OR neck_length_mm >= 0),
    chip_direction     TEXT    CHECK (chip_direction IS NULL OR chip_direction IN ('up','down','compression','straight')),
    substrate          TEXT    CHECK (substrate IS NULL OR substrate IN ('solid_carbide','carbide_tipped','hss','diamond','insert')),
    coating            TEXT,
    display_units      TEXT NOT NULL DEFAULT 'mm' CHECK (display_units IN ('mm','inch','frac_inch')),
    form_scale_mode    TEXT NOT NULL DEFAULT 'keepAngle' CHECK (form_scale_mode IN ('keepAngle','fitEnvelope')),
    nominal_label      TEXT,
    notes              TEXT,
    source_id          TEXT REFERENCES data_source(id)  ON DELETE SET NULL,
    batch_id           TEXT REFERENCES import_batch(id) ON DELETE SET NULL,
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT NOT NULL DEFAULT (datetime('now')),
    deleted_at         TEXT
);
CREATE UNIQUE INDEX idx_tool_vendor_part ON tool(vendor_id, product_id)
    WHERE product_id IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX idx_tool_type_dia ON tool(tool_type, diameter_mm) WHERE deleted_at IS NULL;
CREATE INDEX idx_tool_batch    ON tool(batch_id);

CREATE TABLE tool_trait (
    tool_id TEXT NOT NULL REFERENCES tool(id) ON DELETE CASCADE,
    trait   TEXT NOT NULL,
    PRIMARY KEY (tool_id, trait)
);

-- radius as a function of height above the tip; form tools only
CREATE TABLE tool_profile_point (
    tool_id   TEXT NOT NULL REFERENCES tool(id) ON DELETE CASCADE,
    idx       INTEGER NOT NULL,
    z_mm      REAL NOT NULL CHECK (z_mm >= 0),
    radius_mm REAL NOT NULL CHECK (radius_mm >= 0),
    PRIMARY KEY (tool_id, idx)
);

-- ---------------------------------------------------------------- materials
CREATE TABLE material_class (
    id                   TEXT PRIMARY KEY,
    name                 TEXT NOT NULL UNIQUE,
    family               TEXT NOT NULL CHECK (family IN ('wood','plastic','metal','foam','composite','other')),
    melts                INTEGER NOT NULL DEFAULT 0,
    abrasive             INTEGER NOT NULL DEFAULT 0,
    prefers_single_flute INTEGER NOT NULL DEFAULT 0,
    prefers_downcut      INTEGER NOT NULL DEFAULT 0,
    max_rpm_hint         INTEGER,
    -- Feed multiplier vs the base (softwood) curve. CMT's "Factor Vf".
    feed_factor          REAL NOT NULL DEFAULT 1.0 CHECK (feed_factor > 0 AND feed_factor <= 2.0),
    sort_order           INTEGER NOT NULL DEFAULT 0,
    notes                TEXT
);

CREATE TABLE material (
    id           TEXT PRIMARY KEY,
    class_id     TEXT NOT NULL REFERENCES material_class(id) ON DELETE RESTRICT,
    name         TEXT NOT NULL UNIQUE,
    thickness_mm REAL CHECK (thickness_mm IS NULL OR thickness_mm > 0),
    notes        TEXT,
    deleted_at   TEXT
);

-- ---------------------------------------------------------------- machines
CREATE TABLE machine (
    id                 TEXT PRIMARY KEY,
    name               TEXT NOT NULL UNIQUE,
    make               TEXT,
    model              TEXT,
    controller         TEXT,
    work_x_mm          REAL,
    work_y_mm          REAL,
    work_z_mm          REAL,
    spindle_min_rpm    INTEGER CHECK (spindle_min_rpm IS NULL OR spindle_min_rpm > 0),
    spindle_max_rpm    INTEGER CHECK (spindle_max_rpm IS NULL OR spindle_max_rpm > 0),
    spindle_power_kw   REAL,
    max_feed_xy_mm_min REAL,
    max_feed_z_mm_min  REAL,
    rigidity_factor    REAL NOT NULL DEFAULT 1.0 CHECK (rigidity_factor > 0 AND rigidity_factor <= 1.0),
    collet_sizes_mm    TEXT,
    has_tool_changer   INTEGER NOT NULL DEFAULT 0,
    has_laser          INTEGER NOT NULL DEFAULT 0,
    notes              TEXT,
    deleted_at         TEXT
);

CREATE TABLE holder (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    vendor_id  TEXT REFERENCES vendor(id) ON DELETE SET NULL,
    product_id TEXT,
    notes      TEXT,
    deleted_at TEXT
);

CREATE TABLE holder_segment (
    holder_id    TEXT NOT NULL REFERENCES holder(id) ON DELETE CASCADE,
    idx          INTEGER NOT NULL,
    height_mm    REAL NOT NULL CHECK (height_mm > 0),
    lower_dia_mm REAL NOT NULL CHECK (lower_dia_mm >= 0),
    upper_dia_mm REAL NOT NULL CHECK (upper_dia_mm >= 0),
    PRIMARY KEY (holder_id, idx)
);

-- tool number belongs to the machine assignment, not to the tool
CREATE TABLE tool_assignment (
    machine_id      TEXT NOT NULL REFERENCES machine(id) ON DELETE CASCADE,
    tool_id         TEXT NOT NULL REFERENCES tool(id)    ON DELETE CASCADE,
    tool_number     INTEGER CHECK (tool_number IS NULL OR tool_number >= 0),
    holder_id       TEXT REFERENCES holder(id) ON DELETE SET NULL,
    stickout_mm     REAL CHECK (stickout_mm IS NULL OR stickout_mm > 0),
    length_offset   INTEGER,
    diameter_offset INTEGER,
    notes           TEXT,
    PRIMARY KEY (machine_id, tool_id)
);
CREATE UNIQUE INDEX idx_assignment_tnum ON tool_assignment(machine_id, tool_number)
    WHERE tool_number IS NOT NULL;

-- ---------------------------------------------------------------- library tree
CREATE TABLE tool_group (
    id         TEXT PRIMARY KEY,
    parent_id  TEXT REFERENCES tool_group(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    expanded   INTEGER NOT NULL DEFAULT 1,
    notes      TEXT,
    deleted_at TEXT
);
CREATE INDEX idx_group_parent ON tool_group(parent_id, sort_order);

-- a tool may live in several groups (Vectric cannot do this)
CREATE TABLE tool_group_member (
    group_id   TEXT NOT NULL REFERENCES tool_group(id) ON DELETE CASCADE,
    tool_id    TEXT NOT NULL REFERENCES tool(id) ON DELETE CASCADE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (group_id, tool_id)
);

-- ---------------------------------------------------------------- cutting data
CREATE TABLE cutting_preset (
    id                    TEXT PRIMARY KEY,
    tool_id               TEXT NOT NULL REFERENCES tool(id) ON DELETE CASCADE,
    material_id           TEXT REFERENCES material(id)        ON DELETE CASCADE,
    material_class_id     TEXT REFERENCES material_class(id)  ON DELETE CASCADE,
    machine_id            TEXT REFERENCES machine(id)         ON DELETE CASCADE,
    name                  TEXT NOT NULL,
    origin                TEXT NOT NULL DEFAULT 'user'
                          CHECK (origin IN ('user','vendor','derived','generic')),
    derivation            TEXT NOT NULL DEFAULT 'explicit'
                          CHECK (derivation IN ('explicit','from_chipload')),
    confidence            INTEGER NOT NULL DEFAULT 3 CHECK (confidence BETWEEN 1 AND 5),
    chipload_fz_mm        REAL CHECK (chipload_fz_mm IS NULL OR (chipload_fz_mm > 0 AND chipload_fz_mm <= 2.0)),
    vc_m_min              REAL CHECK (vc_m_min IS NULL OR vc_m_min > 0),
    spindle_rpm           INTEGER CHECK (spindle_rpm IS NULL OR (spindle_rpm >= 1000 AND spindle_rpm <= 60000)),
    spindle_dir           TEXT CHECK (spindle_dir IS NULL OR spindle_dir IN ('cw','ccw')),
    feed_xy_mm_min        REAL CHECK (feed_xy_mm_min IS NULL OR (feed_xy_mm_min > 0 AND feed_xy_mm_min <= 30000)),
    feed_z_mm_min         REAL CHECK (feed_z_mm_min  IS NULL OR (feed_z_mm_min  > 0 AND feed_z_mm_min  <= 30000)),
    ramp_feed_mm_min      REAL CHECK (ramp_feed_mm_min IS NULL OR ramp_feed_mm_min > 0),
    stepdown_mm           REAL CHECK (stepdown_mm IS NULL OR stepdown_mm > 0),
    stepover_mm           REAL CHECK (stepover_mm IS NULL OR stepover_mm > 0),
    clearance_stepover_mm REAL CHECK (clearance_stepover_mm IS NULL OR clearance_stepover_mm > 0),
    cut_direction         TEXT CHECK (cut_direction IS NULL OR cut_direction IN ('climb','conventional')),
    air_blast             INTEGER NOT NULL DEFAULT 0,
    notes                 TEXT,
    source_id             TEXT REFERENCES data_source(id)  ON DELETE SET NULL,
    batch_id              TEXT REFERENCES import_batch(id) ON DELETE SET NULL,
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT NOT NULL DEFAULT (datetime('now')),
    deleted_at            TEXT,
    CHECK (material_id IS NULL OR material_class_id IS NULL),
    CHECK (chipload_fz_mm IS NOT NULL OR feed_xy_mm_min IS NOT NULL OR spindle_rpm IS NOT NULL)
);
CREATE INDEX idx_preset_lookup ON cutting_preset(tool_id, material_id, material_class_id, machine_id)
    WHERE deleted_at IS NULL;

-- The fallback layer. This is the shape in which Sorotec, CMT and Leuco
-- actually publish: an fz band per material class and diameter range.
CREATE TABLE chipload_rule (
    id                 TEXT PRIMARY KEY,
    material_class_id  TEXT NOT NULL REFERENCES material_class(id) ON DELETE CASCADE,
    tool_type          TEXT,
    vendor_id          TEXT REFERENCES vendor(id) ON DELETE CASCADE,
    flute_count        INTEGER,
    dia_min_mm         REAL NOT NULL CHECK (dia_min_mm > 0),
    dia_max_mm         REAL NOT NULL CHECK (dia_max_mm > 0),
    fz_min_mm          REAL NOT NULL CHECK (fz_min_mm > 0),
    fz_typ_mm          REAL NOT NULL CHECK (fz_typ_mm > 0),
    fz_max_mm          REAL NOT NULL CHECK (fz_max_mm > 0),
    rpm_min            INTEGER,
    rpm_max            INTEGER,
    max_stepdown_x_dia REAL,
    source_id          TEXT REFERENCES data_source(id) ON DELETE SET NULL,
    notes              TEXT,
    CHECK (dia_max_mm >= dia_min_mm),
    CHECK (fz_max_mm >= fz_typ_mm AND fz_typ_mm >= fz_min_mm)
);
CREATE INDEX idx_chipload_lookup ON chipload_rule(material_class_id, dia_min_mm, dia_max_mm);

-- A vendor feed-vs-depth curve (e.g. CMT's Vf/H charts): at a fixed RPM the
-- safe feed falls as the depth of cut rises. Stored as a straight line
-- between two published points. This is the BASE (softwood, along-grain);
-- material_class.feed_factor and cross_grain_factor scale it at resolve time.
CREATE TABLE feed_curve (
    id                 TEXT PRIMARY KEY,
    vendor_id          TEXT REFERENCES vendor(id) ON DELETE CASCADE,
    series             TEXT,
    tool_type          TEXT,
    dia_min_mm         REAL NOT NULL CHECK (dia_min_mm > 0),
    dia_max_mm         REAL NOT NULL CHECK (dia_max_mm > 0),
    rpm                INTEGER NOT NULL CHECK (rpm > 0),
    doc_lo_mm          REAL NOT NULL CHECK (doc_lo_mm >= 0),
    feed_lo_mm_min     REAL NOT NULL CHECK (feed_lo_mm_min > 0),
    doc_hi_mm          REAL NOT NULL CHECK (doc_hi_mm >= 0),
    feed_hi_mm_min     REAL NOT NULL CHECK (feed_hi_mm_min > 0),
    cross_grain_factor REAL NOT NULL DEFAULT 0.7 CHECK (cross_grain_factor > 0 AND cross_grain_factor <= 1.0),
    chip_min_mm        REAL,
    chip_max_mm        REAL,
    source_id          TEXT REFERENCES data_source(id) ON DELETE SET NULL,
    notes              TEXT,
    CHECK (dia_max_mm >= dia_min_mm),
    CHECK (doc_hi_mm <> doc_lo_mm)
);
CREATE INDEX idx_feed_curve_lookup ON feed_curve(vendor_id, dia_min_mm, dia_max_mm);

-- ---------------------------------------------------------------- import QA
CREATE TABLE staging_record (
    id          TEXT PRIMARY KEY,
    batch_id    TEXT NOT NULL REFERENCES import_batch(id) ON DELETE CASCADE,
    row_index   INTEGER NOT NULL,
    raw_json    TEXT NOT NULL,
    mapped_json TEXT,
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','new','changed','unchanged','rejected','skipped')),
    tool_id     TEXT REFERENCES tool(id) ON DELETE SET NULL,
    UNIQUE (batch_id, row_index)
);

CREATE TABLE validation_issue (
    id          TEXT PRIMARY KEY,
    batch_id    TEXT REFERENCES import_batch(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    severity    TEXT NOT NULL CHECK (severity IN ('error','warning','info')),
    code        TEXT NOT NULL,
    field       TEXT,
    raw_value   TEXT,
    message     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_issue_batch ON validation_issue(batch_id, severity);

-- Sparse. A row exists only when a value is NOT verbatim from its source:
-- estimated, inferred, or hand-edited. 'user' rows are never overwritten
-- by a catalogue re-import.
CREATE TABLE field_provenance (
    entity_type TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    field       TEXT NOT NULL,
    origin      TEXT NOT NULL CHECK (origin IN ('vendor','user','estimated','derived','generic')),
    source_id   TEXT REFERENCES data_source(id) ON DELETE SET NULL,
    note        TEXT,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (entity_type, entity_id, field)
);

CREATE TABLE change_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL DEFAULT (datetime('now')),
    entity_type TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    op          TEXT NOT NULL CHECK (op IN ('insert','update','delete','restore')),
    before_json TEXT,
    after_json  TEXT
);
CREATE INDEX idx_changelog_entity ON change_log(entity_type, entity_id, at);

-- ---------------------------------------------------------------- views
CREATE VIEW v_preset_ranked AS
SELECT p.*,
    (CASE p.origin WHEN 'user' THEN 400 WHEN 'vendor' THEN 300
                   WHEN 'derived' THEN 200 ELSE 100 END)
  + (CASE WHEN p.material_id IS NOT NULL THEN 40
          WHEN p.material_class_id IS NOT NULL THEN 20 ELSE 0 END)
  + (CASE WHEN p.machine_id IS NOT NULL THEN 5 ELSE 0 END)
  + p.confidence AS score
FROM cutting_preset p WHERE p.deleted_at IS NULL;

CREATE VIEW v_tool_completeness AS
SELECT t.id, t.name, t.vendor_id,
    (t.diameter_mm IS NOT NULL)       AS has_diameter,
    (t.flute_count IS NOT NULL)       AS has_flutes,
    (t.flute_length_mm IS NOT NULL)   AS has_flute_length,
    (t.shank_dia_mm IS NOT NULL)      AS has_shank,
    (t.overall_length_mm IS NOT NULL) AS has_oal,
    (t.diameter_mm IS NOT NULL AND t.flute_count IS NOT NULL) AS is_machinable,
    (SELECT COUNT(*) FROM cutting_preset p WHERE p.tool_id = t.id AND p.deleted_at IS NULL) AS preset_count
FROM tool t WHERE t.deleted_at IS NULL;

CREATE VIEW v_tool_active AS SELECT * FROM tool WHERE deleted_at IS NULL;
CREATE VIEW v_tool_trash  AS SELECT * FROM tool WHERE deleted_at IS NOT NULL;

-- updated_at is set by the repository on every write, NOT by a trigger.
-- A trigger that rewrites updated_at re-fires the change_log trigger and
-- writes phantom no-op entries. Do not reintroduce one.
CREATE TRIGGER trg_tool_log_ins AFTER INSERT ON tool
BEGIN
    INSERT INTO change_log (entity_type, entity_id, op, after_json)
    VALUES ('tool', NEW.id, 'insert', json_object(
        'name', NEW.name, 'tool_type', NEW.tool_type,
        'diameter_mm', NEW.diameter_mm, 'flute_count', NEW.flute_count));
END;

CREATE TRIGGER trg_tool_log_upd AFTER UPDATE ON tool
FOR EACH ROW
BEGIN
    INSERT INTO change_log (entity_type, entity_id, op, before_json, after_json)
    VALUES ('tool', NEW.id,
        CASE WHEN OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL THEN 'delete'
             WHEN OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS NULL THEN 'restore'
             ELSE 'update' END,
        json_object('name', OLD.name, 'diameter_mm', OLD.diameter_mm,
                    'flute_count', OLD.flute_count, 'deleted_at', OLD.deleted_at),
        json_object('name', NEW.name, 'diameter_mm', NEW.diameter_mm,
                    'flute_count', NEW.flute_count, 'deleted_at', NEW.deleted_at));
END;
"""

#: Material classes, four vendors, a CMT feed curve and the fallback
#: chipload bands. Run once, right after :data:`DDL`.
SEED = """\
INSERT INTO material_class
    (id, name, family, melts, abrasive, prefers_single_flute, prefers_downcut, max_rpm_hint, feed_factor, sort_order) VALUES
    ('softwood',  'Softwood',             'wood',     0, 0, 0, 0, NULL,  1.0,  10),
    ('hardwood',  'Hardwood',             'wood',     0, 0, 0, 0, NULL,  0.9,  20),
    ('plywood',   'Plywood',              'wood',     0, 1, 0, 1, NULL,  0.9,  30),
    ('mdf',       'MDF / Particleboard',  'wood',     0, 1, 0, 0, NULL,  1.0,  40),
    ('acrylic',   'Acrylic (PMMA)',       'plastic',  1, 0, 1, 0, 18000, 0.7,  50),
    ('polycarb',  'Polycarbonate',        'plastic',  1, 0, 1, 0, 18000, 0.7,  60),
    ('pvc',       'PVC / Foamed PVC',     'plastic',  1, 0, 1, 0, 20000, 0.8,  70),
    ('hdpe',      'HDPE / PE / PP',       'plastic',  1, 0, 1, 0, 18000, 0.9,  80),
    ('abs',       'ABS',                  'plastic',  1, 0, 1, 0, 20000, 0.8,  90),
    ('pom',       'POM / Acetal',         'plastic',  1, 0, 1, 0, 20000, 0.8,  100),
    ('nylon',     'Nylon / PA',           'plastic',  1, 0, 1, 0, 18000, 0.8,  110),
    ('foam',      'Rigid foam / Tooling', 'foam',     0, 0, 0, 0, NULL,  1.2,  120),
    ('composite', 'Composite / Laminate', 'composite',0, 1, 0, 0, NULL,  0.8,  130),
    ('aluminium', 'Aluminium',            'metal',    0, 0, 1, 0, 24000, 0.5,  200);

INSERT INTO vendor (id, name, country, website, native_units, notes) VALUES
    ('v-sorotec',  'Sorotec',          'DE', 'https://www.sorotec.de', 'mm',
     'Metric. Publishes feed tables per material class -> chipload_rule, not per-tool presets.'),
    ('v-cmt',      'CMT Orange Tools', 'IT', 'https://www.cmtorangetools.com', 'mm',
     'Metric catalogue. Geometry per part number; cutting data only as general tables.'),
    ('v-amana',    'Amana Tool',       'US', 'https://www.amanatool.com', 'inch',
     'Imperial. Their PDF is an index only; real data lives in the Fusion/Vectric files.'),
    ('v-bitsbits', 'Bits & Bits',      'US', 'https://bitsbits.com', 'inch',
     'Imperial. Ships .vtdb and Fusion .tools.');

INSERT INTO data_source (id, vendor_id, kind, title, reliability, notes) VALUES
    ('src-generic-chipload', NULL, 'generic',
     'Generic chipload bands (community / textbook values)', 2,
     'PLACEHOLDER VALUES - replace with vendor-published tables before trusting.'),
    ('src-cmt-193-chart', 'v-cmt', 'catalog_pdf',
     'CMT series 193 Vf/H chart, Ø12-20 mm soft wood', 4,
     'Digitised from the CMT catalogue Vf/H envelope: 18000 rpm, DOC 3 mm -> 10000 mm/min, DOC 40 mm -> 6000 mm/min. Hardwood factor 0.9, cross-grain 0.7.');

-- The CMT series-193 helical bits, Ø12-20 mm, in soft wood at 18000 rpm.
-- Line: DOC 3 mm -> 10000 mm/min, DOC 40 mm -> 6000 mm/min (the base, softwood,
-- along-grain). hardwood 0.9 and cross-grain 0.7 are applied at resolve time.
INSERT INTO feed_curve
    (id, vendor_id, series, tool_type, dia_min_mm, dia_max_mm, rpm,
     doc_lo_mm, feed_lo_mm_min, doc_hi_mm, feed_hi_mm_min, cross_grain_factor,
     chip_min_mm, chip_max_mm, source_id, notes) VALUES
    ('fc-cmt-193', 'v-cmt', '193', 'end_mill', 12.0, 20.0, 18000,
     3.0, 10000.0, 40.0, 6000.0, 0.7, 0.5, 2.0, 'src-cmt-193-chart',
     'Soft wood base curve; hardwood 0.9 and cross-grain 0.7 applied on top.');

INSERT INTO chipload_rule
    (id, material_class_id, tool_type, dia_min_mm, dia_max_mm,
     fz_min_mm, fz_typ_mm, fz_max_mm, rpm_min, rpm_max, max_stepdown_x_dia, source_id) VALUES
    ('cr-sw-1',  'softwood', NULL, 0.5,  3.0, 0.02, 0.04, 0.08, 12000, 24000, 1.0, 'src-generic-chipload'),
    ('cr-sw-2',  'softwood', NULL, 3.0,  6.5, 0.06, 0.12, 0.20, 12000, 20000, 1.5, 'src-generic-chipload'),
    ('cr-sw-3',  'softwood', NULL, 6.5, 20.0, 0.12, 0.20, 0.35, 10000, 18000, 2.0, 'src-generic-chipload'),
    ('cr-hw-1',  'hardwood', NULL, 0.5,  3.0, 0.02, 0.03, 0.06, 12000, 24000, 0.8, 'src-generic-chipload'),
    ('cr-hw-2',  'hardwood', NULL, 3.0,  6.5, 0.05, 0.09, 0.15, 12000, 20000, 1.0, 'src-generic-chipload'),
    ('cr-hw-3',  'hardwood', NULL, 6.5, 20.0, 0.10, 0.16, 0.25, 10000, 18000, 1.5, 'src-generic-chipload'),
    ('cr-ply-1', 'plywood',  NULL, 0.5,  3.0, 0.02, 0.04, 0.07, 12000, 24000, 1.0, 'src-generic-chipload'),
    ('cr-ply-2', 'plywood',  NULL, 3.0,  6.5, 0.05, 0.10, 0.16, 12000, 20000, 1.2, 'src-generic-chipload'),
    ('cr-ply-3', 'plywood',  NULL, 6.5, 20.0, 0.10, 0.18, 0.28, 10000, 18000, 1.5, 'src-generic-chipload'),
    ('cr-mdf-1', 'mdf',      NULL, 0.5,  3.0, 0.03, 0.05, 0.09, 12000, 24000, 1.0, 'src-generic-chipload'),
    ('cr-mdf-2', 'mdf',      NULL, 3.0,  6.5, 0.06, 0.12, 0.20, 12000, 20000, 1.5, 'src-generic-chipload'),
    ('cr-mdf-3', 'mdf',      NULL, 6.5, 20.0, 0.12, 0.22, 0.35, 10000, 18000, 2.0, 'src-generic-chipload'),
    ('cr-acr-1', 'acrylic',  NULL, 0.5,  3.0, 0.03, 0.05, 0.08,  9000, 18000, 1.0, 'src-generic-chipload'),
    ('cr-acr-2', 'acrylic',  NULL, 3.0,  6.5, 0.06, 0.10, 0.15,  8000, 16000, 1.0, 'src-generic-chipload'),
    ('cr-acr-3', 'acrylic',  NULL, 6.5, 20.0, 0.10, 0.15, 0.25,  8000, 14000, 1.0, 'src-generic-chipload'),
    ('cr-pc-2',  'polycarb', NULL, 3.0,  6.5, 0.05, 0.09, 0.14,  8000, 16000, 1.0, 'src-generic-chipload'),
    ('cr-pvc-2', 'pvc',      NULL, 3.0,  6.5, 0.06, 0.12, 0.20,  8000, 18000, 1.5, 'src-generic-chipload'),
    ('cr-hdpe-2','hdpe',     NULL, 3.0,  6.5, 0.08, 0.15, 0.25,  8000, 16000, 1.5, 'src-generic-chipload'),
    ('cr-abs-2', 'abs',      NULL, 3.0,  6.5, 0.06, 0.10, 0.18,  8000, 18000, 1.2, 'src-generic-chipload'),
    ('cr-pom-2', 'pom',      NULL, 3.0,  6.5, 0.06, 0.12, 0.20,  8000, 18000, 1.2, 'src-generic-chipload'),
    ('cr-nyl-2', 'nylon',    NULL, 3.0,  6.5, 0.06, 0.11, 0.18,  8000, 16000, 1.2, 'src-generic-chipload'),
    ('cr-foam-2','foam',     NULL, 3.0, 20.0, 0.20, 0.40, 0.80, 10000, 24000, 3.0, 'src-generic-chipload');

INSERT INTO tool_group (id, parent_id, name, sort_order) VALUES
    ('g-sorotec', NULL, 'Sorotec',          10),
    ('g-cmt',     NULL, 'CMT Orange Tools', 20),
    ('g-mine',    NULL, 'My Tools',         30);
"""

#: Additive migrations for files made by an earlier schema version:
#: ``(version, name, sql)``. Each runs in its own transaction, only when
#: the stored version is below it; ALTER TABLE ADD COLUMN and CREATE TABLE
#: are the only shapes used, so none can lose data.
MIGRATIONS = [
    (2, "feed_curve", """\
ALTER TABLE material_class
    ADD COLUMN feed_factor REAL NOT NULL DEFAULT 1.0;
UPDATE material_class SET feed_factor = 0.9 WHERE id IN ('hardwood','plywood');
UPDATE material_class SET feed_factor = 0.7 WHERE id IN ('acrylic','polycarb');
UPDATE material_class SET feed_factor = 0.8 WHERE id IN ('pvc','abs','pom','nylon','composite');
UPDATE material_class SET feed_factor = 1.2 WHERE id = 'foam';
UPDATE material_class SET feed_factor = 0.5 WHERE id = 'aluminium';

CREATE TABLE IF NOT EXISTS feed_curve (
    id                 TEXT PRIMARY KEY,
    vendor_id          TEXT REFERENCES vendor(id) ON DELETE CASCADE,
    series             TEXT,
    tool_type          TEXT,
    dia_min_mm         REAL NOT NULL,
    dia_max_mm         REAL NOT NULL,
    rpm                INTEGER NOT NULL,
    doc_lo_mm          REAL NOT NULL,
    feed_lo_mm_min     REAL NOT NULL,
    doc_hi_mm          REAL NOT NULL,
    feed_hi_mm_min     REAL NOT NULL,
    cross_grain_factor REAL NOT NULL DEFAULT 0.7,
    chip_min_mm        REAL,
    chip_max_mm        REAL,
    source_id          TEXT REFERENCES data_source(id) ON DELETE SET NULL,
    notes              TEXT
);
CREATE INDEX IF NOT EXISTS idx_feed_curve_lookup ON feed_curve(vendor_id, dia_min_mm, dia_max_mm);

INSERT OR IGNORE INTO data_source (id, vendor_id, kind, title, reliability, notes) VALUES
    ('src-cmt-193-chart', 'v-cmt', 'catalog_pdf',
     'CMT series 193 Vf/H chart, dia 12-20 mm soft wood', 4,
     '18000 rpm, DOC 3 mm -> 10000 mm/min, DOC 40 mm -> 6000 mm/min. Hardwood 0.9, cross-grain 0.7.');

INSERT OR IGNORE INTO feed_curve
    (id, vendor_id, series, tool_type, dia_min_mm, dia_max_mm, rpm,
     doc_lo_mm, feed_lo_mm_min, doc_hi_mm, feed_hi_mm_min, cross_grain_factor,
     chip_min_mm, chip_max_mm, source_id, notes) VALUES
    ('fc-cmt-193', 'v-cmt', '193', 'end_mill', 12.0, 20.0, 18000,
     3.0, 10000.0, 40.0, 6000.0, 0.7, 0.5, 2.0, 'src-cmt-193-chart',
     'Soft wood base curve; hardwood 0.9 and cross-grain 0.7 applied on top.');
"""),
    (3, "form_scale_mode", """\
ALTER TABLE tool
    ADD COLUMN form_scale_mode TEXT NOT NULL DEFAULT 'keepAngle';
"""),
]
