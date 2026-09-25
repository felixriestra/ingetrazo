# CAM in IngeTrazo: porting the 2DCam engine to Python

> Status (2026-09-25): **v1 implemented in the fork, experimental.**
> Host hooks H1–H4, the engine (parity with the 2DCam corpus, deliberate
> differences in `docs/cam-2dcam-deviations.md`), GRBL and LinuxCNC posts
> with frozen goldens, the plugin UI, es/pt-BR catalogues and the user
> guide (`docs/cam/`) are done. Controller checks (2026-09-25, in a
> Linux container on the Mac): every corpus program in mm, inch, pecked
> and flattened-arc variants is accepted by LinuxCNC 2.9.4's own
> interpreter (`rs274`, 90 programs) and by GRBL 1.1's own parser
> (`gvalidate`, 101 programs); LinuxCNC's moves equal the plugin's within
> 0.001 mm, G83 pecks and G41 compensation included; the suite passes on
> Linux (Python 3.13). Still open from M4: the native-speaker review of
> the glossary, Flatpak/AppImage/Windows smoke runs, and test cuts on real
> machines. Lives in the
> `felixriestra/ingetrazo` fork, on branch `cam-plugin`, until the code is
> stable; nothing goes upstream before then.
> Workspace: `~/Documents/MacApps/IGTCam/` (the fork clone is in
> `IGTCam/ingetrazo/`).
> Source engine: `2DCam` (`Sources/TwoDCamCore`, Swift). 2DCam and
> MADRIDCam are the same author's work and will be published as
> GPL-3.0-or-later, the same licence as IngeTrazo.

## Why

2DCam is a capable 2.5D CAM, but it only runs on macOS 26. IngeTrazo
already reaches Linux (Flatpak, AppImage, tarball) and Windows, and its
users design exactly what gets cut on a router: boards, panels, signs and
furniture (see the Parts tray and cut list). A Python port of the 2DCam
**engine** running as an IngeTrazo **plugin** gives Linux and Windows users
a way to go from a model to G-code without leaving the modeler. Mac users
keep the native 2DCam.

## Decisions

| Topic | Decision |
|---|---|
| Licence | GPL-3.0-or-later for 2DCam, MADRIDCam and the port. The `LICENSE` files are added to the Swift repos when they are published, not as part of this work. |
| 2DCam | **Read-only reference. Never modified, and never built in place.** It keeps working exactly as it does today. Everything new lives under `IGTCam/`. |
| Where the code lives | The fork only, as a small rebased patch stack, until stable. |
| Controllers | **GRBL and LinuxCNC only**, both first-class. The Mach3/Mach4/Fanuc posts from 2DCam are **not** ported. |
| Languages | **Spanish, English and Portuguese (Brazil)** at launch, complete in all three. More can be added by dropping in a JSON file. |
| Relief | Relief carving from images (the Depth-Anything model) and STL relief are **not ported**. They stay in 2DCam as they are. |

## Scope

### In (v1)

- Operations: **outside/inside/on-line profile** (with tabs, lead-in/out,
  ramp/helix entry), **pocket** with islands, **drill** (with peck),
  **engrave** (follow path at depth), **facing**.
- Job setup: rectangular stock, work origin (9-point + top/bottom), safe
  and clearance heights, material, and units (mm or inch).
- A simple per-document tool table: flat, ball and bull-nose end mills,
  chamfer mills and drills.
- Toolpath verification (gouge, rapid-through-stock, depth limits) that
  must pass before export.
- G-code export for **GRBL** and **LinuxCNC**.
- Toolpath preview in IngeTrazo's viewport, with cycle-time estimate and
  statistics.
- A UI fully translated into **es, en and pt-BR**.

### Later (v1.x)

Bore, slot, chamfer and open pocket (`Advanced2DToolpathGenerator`); a
numpy heightfield stock simulation; a persistent tool library (SQLite + CSV
import, ported from `ToolLibrary/`); the feeds and speeds `PresetResolver`;
a setup sheet as a Qt PDF; nesting (`LayoutEngine`); bitmap-to-vector
tracing with the `vtracer` wheel.

### Out (archived, not ported)

- **Relief carving from images** (Depth-Anything CoreML model,
  `ReliefCarve/`, `ReliefCarveToolpathGenerator`,
  `ExperimentalReliefRoughingGenerator`, `ReliefGeometryAnalyzer`,
  `ReliefSimulationStockScope`, `ImportedReliefImage`).
- **STL relief** (`STLReliefToolpathGenerator`, `STLImport`).
- **The Mach3/Mach4/Generic ISO postprocessors.**
- Things IngeTrazo already does or doesn't need: 2DCam's sketch editor
  (`SketchEditing`, `VectorEditing`, `SketchConstraintSolver`,
  `ShapeRecognition`, `LassoExtraction`, `ManualTraceEditing`), the Metal
  viewport, the OCS socket import, `.2dcam` package documents, and the
  SVG validator/repair (IngeTrazo already imports DXF; revisit SVG only if
  users ask).

## Measured size of what gets ported

| 2DCam area (Swift) | Lines | v1? | Notes |
|---|---:|---|---|
| `CAM/` (excluding relief) | ~2,600 | yes | `Advanced2DToolpathGenerator` (748) carries most of the logic |
| `CAM/OperationGeometryRegionBuilder`, `OperationToolpathCompiler` | ~590 | yes | Operation → region → toolpath dispatch |
| `CAM/CanonicalToolpath` | 401 | yes | Command model, statistics, time estimate |
| `Postprocessing/` | 555 | base only | Post protocol and capabilities; the dialects are new (GRBL, LinuxCNC) |
| `Verification/` | 1,194 | yes | Verifier, G-code parser, round-trip check |
| `Models/` (stock, tool, operation, strategy, machine) | ~1,100 | yes | Codable → dataclasses |
| `CAD/ContourOffset`, `PolygonBoolean`, `GeometryKernel` | ~450 | replaced | Replaced by `pyclipper` (fewer lines in Python) |
| `Simulation/` (excluding relief) | ~490 | v1.x | numpy heightfield |
| `ToolLibrary/` | ~3,800 | v1.x | Only the schema and validator in v1 |

The v1 engine is about **6,300 Swift lines**. Expect roughly 4,000–5,000
lines of Python, plus about 1,500 lines of plugin UI and host hooks, plus
three language catalogues.

## Architecture

```text
IngeTrazo scene (metres, QVector3D float32)
   │  selection: faces / coplanar edges / a Part
   ▼
plugins/cam/extract.py ── project onto the machining plane, convert to float64 mm,
   │                      orient loops (outer CCW, holes CW), note the plane frame
   ▼
plugins/cam/engine/   (pure Python + pyclipper + numpy — NO Qt, NO i18n imports)
   models.py        Stock, MachineSetup, Tool, Operation(+params), Strategy, Region, Tab
   geometry.py      pyclipper offset/boolean, arc flattening, loop chaining, orientation
   toolpath.py      CanonicalToolpath, commands, sections, statistics, time estimate
   ops/             facing, profile, pocket, drill, engrave  (later bore, slot, chamfer)
   compiler.py      Operation list → one CanonicalToolpath (ordering, tool changes)
   verify.py        ToolpathVerifier + GCodeParser + round-trip validator
   post/            base.py, grbl.py, linuxcnc.py
   issues.py        Machine-readable error and warning codes + parameters
   io.py            JSON (field names follow 2DCam's Codable keys)
   ▼
plugins/cam/ui/  (PySide6)
   dock.py          CAM dock: Job ▸ Tools ▸ Operations ▸ Output
   op_forms.py      a parameter form per operation kind
   worker.py        QThread calculation, Signal(object) back to the main thread
   overlay.py       draws the toolpaths/stock wireframe via the viewport overlay hook
   messages.py      engine issue code → translated sentence
plugins/cam/i18n/     es.json, pt-BR.json (en is the source language)
plugins/cam/i18n.py   the plugin's tr(): own catalogue first, then core.i18n
plugins/cam/__init__.py   Tool subclass "CAM…" → opens/raises the dock
```

Rules:

- **The engine never imports Qt or translations.** It raises and returns
  **issue codes with parameters** (`ToolTooLarge(diameter=6.0,
  min_feature=4.2)`), never finished sentences. Only the UI turns them into
  text in the user's language. So the engine can be tested headlessly and
  its messages can't end up half-translated.
- **The engine works in millimetres and float64.** Conversion happens in
  exactly one place, `extract.py`. Geometry is re-based on the
  machining-plane origin *before* float precision matters, because
  IngeTrazo stores `QVector3D` (float32), and georeferenced models far
  from the origin would otherwise lose sub-millimetre precision. Inch jobs
  are converted only in the post (`G20`) and in the UI.
- **The job is authoritative, not the scene.** As in 2DCam, the CAM job
  stores a snapshot of its 2D loops. A **Refresh geometry** action
  re-extracts them from the linked faces. If the model changed and the
  link is lost, the job still opens and says so, rather than silently
  cutting different geometry.
- **JSON compatibility with 2DCam.** Dataclass field names follow 2DCam's
  `Codable` keys (`stepDown`, `stockAllowance`, `openEdgeIndices`, …).
  That makes the shared test fixtures possible and leaves the door open
  to exchanging jobs with 2DCam.

## Postprocessors: GRBL and LinuxCNC

Both are written from scratch against a shared `post/base.py`, which is
ported from 2DCam's `PostprocessorCapabilities` and program-assembly logic.
Where the two controllers differ:

| Concern | GRBL (1.1 / grblHAL-compatible subset) | LinuxCNC (2.9+) |
|---|---|---|
| Tool change | **Not supported by the controller.** A multi-tool job exports **one file per tool** (`job_T1_6mm-endmill.nc`, …), each starting with a manual-change comment. | `T<n> M6` with `G43 H<n>` from the tool table. One file. |
| Drilling | No canned cycles. Peck drilling is expanded to `G0`/`G1` moves. | `G81` / `G83` canned cycles, with `G80` to cancel. |
| Path blending | Controller default. | `G64 P<tolerance>` from the job tolerance. |
| Arcs | `G2`/`G3` with incremental `I`/`J` (`G91.1` default), with a minimum-radius guard. | The same, plus an optional switch to flatten arcs into lines. |
| Units | `G21` / `G20` | `G21` / `G20` |
| Dwell | `G4 P<seconds>` | `G4 P<seconds>` |
| Program end | `M5`, `M30` | `M5`, `M9`, `M2` |
| Comments | `( … )`, **ASCII only**: accents are stripped (`Perfil exterior` stays; `Cajeado ñ` → `Cajeado n`), because some GRBL senders reject non-ASCII. | `( … )`, also ASCII only, for consistency. |
| Extension | `.nc` (also `.gcode`) | `.ngc` |

The G-code itself is never translated: controller syntax is the same in
every language. Only the **comments** (operation and tool names) follow the
job's language, reduced to ASCII.

Verification for each dialect: the exported file is parsed back by
`verify.py` using that dialect's rules (for example, it must reject `M6` or
`G43` in a GRBL file) and checked against the toolpath.

## Languages: Spanish, English, Portuguese

IngeTrazo already has a translation mechanism: `core/i18n.py`, `tr()`
keyed on the English source string, and `i18n/{en,es,pt-BR}.json`. The CAM
plugin **uses the same format but ships its own catalogues**:

- `plugins/cam/i18n/es.json` and `pt-BR.json` (`en` is the source, with no
  file needed). The plugin's `tr()` looks in its own catalogue first, falls
  back to `core.i18n.tr` (for shared words like "Cancel"), and then to the
  English source string. The language is whatever IngeTrazo is set to
  (`core.i18n.current_language()`), with no separate setting.
- Why its own files: the CAM terms stay together, the host's `i18n/*.json`
  aren't touched (so rebases on upstream stay easy), and the plugin works
  the same when installed in the per-user plugin folder.
- **Complete in all three languages at release.** The host's `pt-BR.json`
  covers only ~1,350 of the ~2,045 Spanish entries; the CAM plugin must
  not repeat that gap. A test (`tests/test_cam_i18n.py`) extracts every
  `tr("…")` literal under `plugins/cam/` and fails if any is missing from
  `es.json` or `pt-BR.json`, or if a catalogue has stale keys or
  mismatched `{placeholders}`.
- **A CAM glossary** (`plugins/cam/i18n/GLOSSARY.md`) fixes the
  terminology before translation starts, so the same concept always gets
  the same word:

  | en | es | pt-BR |
  |---|---|---|
  | Toolpath | Trayectoria de herramienta | Percurso da ferramenta |
  | Stock | Material en bruto / Tablero | Material bruto / Chapa |
  | Outside profile | Perfilado exterior | Perfil externo |
  | Pocket | Vaciado (cajeado) | Rebaixo (bolsão) |
  | Drilling / Peck | Taladrado / Picoteo | Furação / Pica-pau |
  | Engrave | Grabado | Gravação |
  | Facing | Planeado | Faceamento |
  | Tab | Puente (pestaña) | Ponte (aba) |
  | Step-down | Profundidad por pasada | Profundidade por passada |
  | Stepover | Paso lateral | Passo lateral |
  | Feed / Plunge rate | Avance / Avance de penetración | Avanço / Avanço de mergulho |
  | Climb / Conventional | En concordancia / En oposición | Concordante / Discordante |
  | Safe height | Altura de seguridad | Altura de segurança |
  | Work origin | Origen de pieza (cero pieza) | Origem da peça (zero peça) |

  These are proposals. Have a machinist who speaks each language review the
  glossary (especially the regional variants for Spain vs. Latin America
  and for Brazil) before the catalogues are filled in.
- Numbers and units: lengths are shown with IngeTrazo's `format_length`
  in the job's units. G-code always uses `.` as the decimal separator
  whatever the language (tested).
- Adding a fourth language later means dropping in
  `plugins/cam/i18n/<code>.json` and running the coverage test.

## Host changes needed in IngeTrazo (fork patch stack)

Today the plugin API only registers menu tools (`core/extensions.py`).
A CAM workspace needs four more things. Each is a separate, small commit in
the fork, kept easy to rebase (and shaped so it could be proposed upstream
later, since all four are already on their roadmap in `docs/plugins.md`):

| # | Change | Where | Size |
|---|---|---|---|
| H1 | **Bundle package plugins.** `ingetrazo.spec` only copies `plugins/*.py`; add package dirs (with their `i18n/*.json`) and the `pyclipper` hidden import. Mirror this in the AppImage build and the Windows installer. | `ingetrazo.spec`, `packaging/`, `installer/` | ~20 lines |
| H2 | **Dock registration.** `main_window.add_plugin_dock(dock, area)` with the dock's state kept in the window-state save. | `views/main_window.py` | ~40 lines |
| H3 | **Viewport overlay hook.** `viewport.overlay_painters: list[callable(painter, viewport)]`, called next to `_draw_geo_paths` in `_draw_overlay` (`views/viewport.py:5516` on the `cam-plugin` base); `_world_to_pixel` exposed as public API. | `views/viewport.py` | ~30 lines |
| H4 | **Plugin data persistence.** `scene.plugin_data: dict[str, dict]` saved as `payload["plugin_data"]` in `.igz` and ignored gracefully by older readers (the same pattern `igz.py` already uses for optional keys). Undo goes through `SnapshotImport`. | `formats/igz.py`, `core/scene` | ~40 lines + tests |

Dependencies: add **`pyclipper>=1.4`** to `requirements.txt`. Verified
2026-09-25: cp314 wheels exist for manylinux x86_64/aarch64, win_amd64 and
macOS. `numpy` is already present. Hold off on `shapely` and `vtracer`
until a v1.x feature needs them, in keeping with the repo's "deliberately
minimal" dependency stance.

## Parity harness: 2DCam is the spec

The port is only trustworthy if it gets the same answers as the engine
that is already tested. So the first thing to build is a **shared fixture
corpus**:

1. Create **`IGTCam/twodcam-fixtures/`**, a small, separate Swift package
   (outside both repos, so 2DCam stays untouched). It depends on 2DCam's
   `TwoDCamCore` library product through a local path dependency
   (`.package(path: "../../2DCam")`); SwiftPM builds into the fixtures
   package's own `.build/`, so nothing is written inside 2DCam. Its
   executable rebuilds the v1-operation cases from
   `Tests/TwoDCamCoreTests` through `TwoDCamCore`'s public API and, for
   each, writes:
   ```text
   cam_fixtures/<case>/job.json        stock, setup, tool, operation, input regions
   cam_fixtures/<case>/toolpath.json   CanonicalToolpath (commands + sections)
   ```
2. Commit the corpus to the IngeTrazo fork under `tests/data/cam_fixtures/`.
3. `tests/test_cam_parity.py` runs the Python engine on every `job.json`
   and checks:
   - **Toolpath geometry within tolerance, not byte-identical.**
     `pyclipper` offsets differ from 2DCam's own offset + iOverlay in
     vertex placement. Compare cut-motion polylines by Hausdorff distance
     ≤ 0.02 mm per section, with the same section kinds and order, and
     depth levels exactly equal.
   - **Invariants for every fixture.** The verifier reports no gouges and
     no rapids below safe height.
4. When the Swift engine changes, regenerate the fixtures. The Python side
   fails loudly until it is brought back in line.

Posts have no Swift reference, because GRBL and LinuxCNC are new. They get:

- **Golden files**, reviewed by hand once and then frozen:
  `tests/data/cam_golden/<case>.{grbl.nc,linuxcnc.ngc}`.
- **A round-trip check.** Each posted file is parsed back with the
  dialect's rules and must reproduce the toolpath (after canned-cycle
  expansion for LinuxCNC) within 0.001 mm.
- **Dialect rules.** For example: no `M6` or `G43` and no `G8x` in GRBL
  files; ASCII-only output; `.` decimal separator under every UI language.
- **Simulator checks (M4):** run the goldens through LinuxCNC's `sim`
  configuration (`axis_mm`) and a GRBL simulator (`grbl-sim`) or
  a controller running in check mode (`$C`), with zero errors.

## Geometry extraction from IngeTrazo

`plugins/cam/extract.py` supports three ways to pick what gets cut:

1. **Selected face(s):** the outer `loop` plus `hole_loops`, projected onto
   the face plane. The face normal gives the machining direction; its top
   is Z0 by default.
2. **Selected coplanar edges:** edges chained into closed loops (open
   chains become engrave paths). Fails with a clear message if they are
   not coplanar.
3. **A Part from the Parts tray / cut list** (`core/parts.py`): the
   board's largest face becomes the machining plane, its outline the
   outside profile, and its through-holes the inside profiles or drills
   (circle detection: a loop whose points all sit at equal radius
   → drill candidate).

The stock defaults to the selection's bounding box in the plane, with the
part thickness as the stock height. The user can override both.

Selection must go through `scene.loose_mesh` / `scene.groups`, not
`scene.mesh`, as `docs/plugins.md` warns.

## UI (the CAM dock)

A `QDockWidget` on the right, sitting beside the existing trays:

- **Job**: stock size and thickness, origin (a 3×3 picker plus top/bottom),
  safe and clearance Z, units, and **controller: GRBL / LinuxCNC**.
- **Tools**: a table of number, type, diameter, flutes, feed, plunge and
  RPM. Add, duplicate, delete.
- **Operations**: an ordered list with checkboxes, and **Add from
  selection ▸ Profile / Pocket / Drill / Engrave / Face**. Selecting an
  operation shows its parameter form and highlights its region in the
  viewport.
- **Output**: Calculate (runs on a worker thread, cancellable), the
  verification report, statistics (cut/rapid length, estimated time),
  and **Export G-code…**, which is disabled while verification has errors.
  On GRBL with several tools, the export dialog explains that it will
  write one file per tool and lists them.

Viewport overlay: cuts drawn in each operation's colour, rapids as thin
dashed red lines, plunges and ramps highlighted, tabs as markers, and the
stock as a wireframe box. Toggles show or hide each item.

Follow the house rules: every visible string goes through the plugin's
`tr()`, the document is never touched off the main thread, and results
come back via `Signal(object)` to a bound method.

## Milestones

| M | Deliverable | Estimate | Done when |
|---|---|---|---|
| **M0** | **Preparation.** Build `IGTCam/twodcam-fixtures` (2DCam untouched) and generate the first corpus. Land H1–H4 in the fork. Write the CAM glossary (es/en/pt-BR). | 4–5 days | Corpus committed; a hello-world package plugin opens a dock with a translated title in all three languages, draws an overlay line, and its `plugin_data` survives save/reopen in a Flatpak build |
| **M1** | **Engine foundation.** `models`, `io`, `issues`, `geometry` (pyclipper), `toolpath` (statistics + time estimate), `post/base`, and `verify` (verifier, parser, round-trip). | 1.5 weeks | Verifier tests ported and green; round-trip works on the Swift `toolpath.json` fixtures |
| **M2** | **v1 operations + posts.** Facing, profile (in/out/on, tabs, leads, ramp/helix), pocket with islands, drill with peck, engrave; the operation compiler with nearest-neighbour ordering and tool changes; the **GRBL and LinuxCNC posts** (including GRBL's one-file-per-tool split and LinuxCNC's canned cycles). | 2 weeks | Geometry parity on all v1 fixtures; goldens reviewed and frozen for both dialects |
| **M3** | **Plugin UI.** Extraction (faces, edges, parts), the dock, operation forms, worker thread, overlay, persistence in `.igz`, and the issue-code → message layer. English strings only while the UI settles. | 1.5 weeks | End to end: model a board with holes, then Part → CAM → Calculate → Export for GRBL and for LinuxCNC; reopen the file with the job intact |
| **M4** | **Translation and hardening.** Fill `es.json` and `pt-BR.json` from the glossary, with a review by a native speaker. Performance pass (numpy in the hot loops; target < 2 s for a 1,000-segment pocket), user docs in three languages, simulator checks, packaging on every channel, and **test cuts on a real GRBL machine and a real LinuxCNC machine**. | 1–1.5 weeks | i18n coverage test green; Flatpak, AppImage and Windows installer all run the smoke job in es/en/pt-BR; both machines cut a test part matching the model within tolerance |

**Total for v1: about 7 weeks** of focused work. Dropping the Mach/ISO
posts saves a little; the two new dialects and the three-language
requirement add it back. M1 and the UI half of M3 can overlap if two
people work on it.

### v1.x order (driven by value)

1. ~~Stock-removal simulation~~ — **done**: numpy height field with
   vectorised sweeps, machine-time playback, a 3D window and a playhead
   in the viewport (`engine/simulate.py`, `ui/simview.py`).
2. ~~Bore, slot, chamfer and open pocket~~ — **done** (see the deviations
   document for where they differ from 2DCam's).
3. A persistent tool library with SQLite + CSV vendor import, and feeds
   and speeds presets.
4. A setup sheet PDF (QtPdf / `QPdfWriter`), translated like the UI.
5. Nesting several parts onto one sheet (`LayoutEngine`), a strong fit
   with IngeTrazo's cut list.
6. Bitmap tracing (`vtracer`), if users ask for it.

## Risks

| Risk | Mitigation |
|---|---|
| **Machine safety.** Wrong G-code breaks tools, machines and people. | Export is blocked on verifier errors. The G-code header states the controller, units and origin. The first run is a mandatory "air cut" check in the docs. The v1 release is labelled experimental. |
| **GRBL variants** (GRBL 1.1, grblHAL, FluidNC) disagree at the edges. | Target the common GRBL 1.1 subset only (no `G43`, no canned cycles, no `M6`). Anything beyond it is opt-in later. Check against a real controller in M4. |
| **pyclipper vs 2DCam geometry differences.** | Tolerance-based parity with invariants (above), not byte-equality. Keep the integer scale fixed (1 unit = 0.1 µm) and document it. |
| **Terminology drift across three languages.** | Glossary before catalogues; coverage and placeholder test; one native-speaker review per language. |
| **Python performance** on dense pockets and simulation. | Clipper does the heavy geometry in C++. Keep per-point Python loops out of hot paths; numpy for simulation; calculation runs on a worker thread, cancellable. |
| **Upstream drift** while the fork carries H1–H4 and the plugin. | Keep the host changes as four small, separate commits; rebase on `origin/main` regularly. The plugin itself touches no host files beyond those. |
| **float32 scene coordinates.** | Extract and re-base to the machining plane in float64 immediately (see Architecture). |

## Units: millimetres and inches (decided)

**Both are supported in v1.** Each job picks mm or inch; the UI shows and
takes lengths in the job's units, and the post writes `G21` or `G20` with
coordinates, feeds and depths in those units. The engine stays mm/float64
internally, so inch support lives only at the edges: the UI conversion
and the post. The parity fixtures and goldens cover at least one inch job
per dialect (round-trip checked in inch coordinates).
