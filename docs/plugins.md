# Writing an IngeTrazo plugin

The plugin API is **not stable yet** — expect breaking changes during the
0.x series.

A plugin is a Python file (or a package directory) that defines one or more
tools. Drop it in either of the two places IngeTrazo scans at startup:

- `<app>/plugins/` — plugins bundled with the application (read-only in an
  installed build). A package plugin ships whole: every file under its
  directory (submodules, `i18n/*.json`, icons) except bytecode caches, so
  read your own data relative to `__file__`;
- your per-user directory — `~/.local/share/ingetrazo/plugins/` on Linux
  (honouring `$XDG_DATA_HOME`), `%APPDATA%\ingetrazo\plugins\` on Windows.
  **Extensions ▸ Open plugins folder** creates and opens it for you.

Every `Tool` subclass **defined in the file** gets an entry in the
**Extensions** menu. (Classes a plugin merely imports are ignored, so
importing `LineTool` to reuse it does not duplicate the built-in.)

## Minimal example

```python
# ~/.local/share/ingetrazo/plugins/hello_tool.py
from tools.base import Tool


class HelloTool(Tool):
    name = "Hello"
    shortcut = None      # or "Ctrl+Shift+H" — silently dropped if taken

    def on_activate(self, viewport):
        viewport.flash_status("Hello from a plugin!", 3000)

    def on_deactivate(self, viewport):
        pass
```

Rules of the road:

- **A broken plugin cannot break IngeTrazo.** If your file raises on import
  or your tool raises in its constructor, the app still starts; the menu
  shows a disabled `⚠ name (load error)` entry with the exception in its
  tooltip, and the full traceback goes to the `ingetrazo.plugins` logger.
- Menu plugin tools are **one-shot**: `on_activate` runs (typically opening
  a dialog) and the viewport's active drawing tool is left untouched.
- Plugins are imported by file path under a private module name — never
  rely on being importable as `plugins.yourname`, and never assume the
  plugin directory is on `sys.path`.

## Mutating the document

Read anything you like. To **modify** the model, go through the command
layer so your changes are undoable and mark the document dirty — the
Python Console (below) does this for you; a dialog-based plugin does it
explicitly:

```python
from core.history import SnapshotImport

def on_activate(self, viewport):
    def mutate(scene):
        scene.mesh.add_face([...])          # any mesh/group/layer edits
    viewport.history.execute(SnapshotImport(mutate))
    viewport.notify_scene_changed()
```

Useful, honest data points (what the app itself uses — see the bundled
`plugins/model_info.py` for a worked example):

- geometry: `scene.loose_mesh` (NOT `scene.mesh`, which is swapped while a
  group is open for editing), `scene.groups`;
- materials: per-face `attrs["color"]` (floats 0–1) and `attrs["texture"]`
  are the render truth; named identity lives in the registry
  (`scene.materials`, `attrs["mat"]` on faces — see `core/materials.py`);
- BIM: `core.bim` (`tag_faces`, `tag_group`, `collect_objects`) — the same
  calls behind the BIM tray and the IFC export;
- lengths for display: `scene.dimension_style` + the viewport's
  `_format_dim_value`.

## Side panels

A plugin can own a dock next to the trays. Build it the first time your
tool runs and hand it to the main window:

```python
from PySide6.QtWidgets import QDockWidget

def on_activate(self, viewport):
    win = viewport.window()
    dock = win.plugin_docks().get("myplugin_panel")
    if dock is None:
        dock = QDockWidget("My panel", win)
        dock.setObjectName("myplugin_panel")    # REQUIRED, keep it stable
        dock.setWidget(MyPanel(viewport))
    win.add_plugin_dock(dock)                   # tabbed with the trays
```

`add_plugin_dock(dock, area=Qt.RightDockWidgetArea, *, show=True)` gives
the dock a Window-menu entry, puts it back where the user left it last
session (the window layout remembers it by `objectName`), and returns the
dock in charge — calling it again with the same name returns the first
one, so it is safe to call on every activation.

## Viewport overlays

To draw world geometry over the model (toolpaths, markers), append a
callable to `viewport.overlay_painters`:

```python
def paint(painter, viewport):                 # QPainter, logical pixels
    a = viewport.world_to_pixel(QVector3D(0, 0, 0))    # metres → (x, y)
    b = viewport.world_to_pixel(QVector3D(1, 0, 0))
    if a and b:                                   # None: behind the camera
        painter.drawLine(QPointF(*a), QPointF(*b))

viewport.overlay_painters.append(paint)
viewport.update()                                # repaint now
```

Painters run on every overlay pass, after the georef layers and before
the dimensions and labels, with the painter state saved and restored
around each one. A painter that raises is logged once and removed. For
many points, `viewport.world_to_pixels(array_n_by_3)` returns
`(px, py, in_front)` NumPy arrays in one call.

## Document data

A plugin can keep its own data in the document: `scene.plugin_data` maps
a plugin key to a JSON-safe value, saved in the `.igz` as
`payload["plugin_data"]`. `app.document_data` / `app.set_document_data`
(`setup(app)`, below) are the convenient way in; from a tool, change it
through the undo stack directly:

```python
from core.history import SetPluginDataCommand

viewport.history.execute(SetPluginDataCommand("myplugin", {"setting": 1}))
viewport.notify_scene_changed()        # marks the document modified
```

`SetPluginDataCommand(key, None)` removes the entry. Values are copied (through
JSON) on the way in and out of history, so keep editing your own copy freely.
Rules: pick a key unlikely to clash (your plugin's name); store only JSON
types (an entry that fails to serialise is dropped from the file and
logged, the rest of the document is saved); data of plugins that are not
installed is carried along untouched when the document is re-saved.

## Startup hook

A plugin module may define `install(window)`: the window calls it once at
startup, after loading the plugin, before any of its tools is picked. Use it
for what must already work then, and keep it light — import the rest when
it is first used. The CAM plugin registers its file type there, so a
double-clicked `.igcam` opens into CAM without the panel having been opened:

```python
def install(window):
    window.file_openers[".igcam"] = lambda path: open_job(window, path)
```

A hook that raises is logged and skipped; it never stops the window.

## Workspaces: a document of your own

A plugin whose work is not the 3D model — the CAM plugin's jobs, 2.5D
drawings on the stock — can show its own document in the model's place:

```python
win = viewport.window()
win.enter_workspace(ws)     # the model is parked, untouched
...
win.leave_workspace()       # the model is back: geometry, undo, file, camera
```

`ws` provides `scene` and `history` (a `core.scene.Scene` and its
`core.history.History`), `title()`, `is_dirty()`, `save()`, `save_as()` and
`confirm_leave()` (True when it may go). Optional: `new()`, `open()`,
`allowed_tools` (tool keys; the rest are disabled while it shows),
`camera` (restored on entry, updated on leaving) and `left()`.

While a workspace shows, File ▸ New / Open / Save / Save As, the window
title, the unsaved-changes prompts and quitting go to it, and the
model's autosave pauses. Quitting asks the workspace first, then the
model. `win.file_openers[".ext"] = callable(path)` lets Open Recent, the
command line and a double-click open your own file type.

## Developing interactively

**Extensions → Python Console** (`Ctrl+Shift+P`) is a live REPL over the
open document — the fastest way to prototype a plugin. Everything a run
creates is one undo step; a run that raises is rolled back whole. "Run
script file…" executes a `.py` against the model the same way
(`scripts/create_architectural_showcase.py` is a worked example that
builds a small BIM-tagged pavilion).

## Building on the AI layer (`core/ai.py`)

The two AI plugins share a reusable layer that any plugin can import:

- `ai.run_transactional(viewport, code, scope)` — execute Python against
  the live document with the Python Console's guarantees: ONE undo step,
  whole rollback on error, no undo entry for inspect-only runs. This is
  the canonical way for generated or scripted code to mutate the model.
- A provider layer following the IngePresupuestos convention: one API
  key, provider detected by its prefix (`ai.detect_provider`), seven
  providers over two wire formats (Anthropic native + OpenAI-compatible),
  stdlib `urllib` only. `ai.chat(...)` does one blocking turn (run it on
  a worker thread), `ai.list_models(...)` returns what the key can
  actually use, `ai.probar_conexion(...)` validates credentials, and
  `ai.PROVIDERS` / `ai.DEFAULT_MODELS` / `ai.PROVIDER_INFO` feed a UI.

So "an assistant that speaks my domain" is a small plugin: your own
system prompt + `ai.chat` + `ai.run_transactional`. See
`plugins/ai_assistant.py` for the full worked example (agent loop with
screenshot feedback) and `docs/ai-bridge.md` for the MCP route.

Threading rule for any plugin doing network or background work: never
touch the document off the main thread. Relay results with a
`Signal(object)` on a queued connection to a bound method —
`Signal(dict)` would hand the slot a COPY of the payload, and a lambda
receiver runs on the WRONG thread (both bugs were hunted in these very
plugins; the details are in the AI plugins' comments).

## Beyond tools: `setup(app)`

A plugin that needs more than a menu entry defines a module-level
`setup(app)`. It is called once, when the main window is built, with an
`ExtensionApp` (`views/extension_api.py`, `API_VERSION` 1). A plugin may
have tools, a `setup`, or both; if `setup` raises, the plugin shows as a
load error and the application opens regardless.

```python
def setup(app):
    app.key                       # this plugin's name (its file stem)
    app.window, app.viewport, app.scene

    # Data IN THE DOCUMENT: one JSON-safe value per plugin, saved in the
    # .igz, reset by New/Open. Each set is one undo step.
    data = app.document_data(default={})
    app.set_document_data({"levels": [...]})
    app.on_document_changed(refresh)      # edits, undo, New, Open

    # A tab in the side tray, beside Properties / BIM / Terrain.
    app.add_panel("Levels", my_widget)

    # Drawn with a QPainter over every frame, whatever the active tool.
    app.add_overlay(lambda viewport, painter: ...)

    # Offered the snap engine's answer on every hover and click; return a
    # core.snap.SnapResult (its `label` is the ScreenTip) or None.
    app.add_snap_provider(lambda viewport, snap, px, py: None)
```

Rules the host enforces: a snap provider never overrides a named point
(endpoint, midpoint, centre, intersection, on edge…) — the user aimed at
it; an overlay or provider that raises is logged and skipped, never
breaking the frame or the cursor; document data that is not JSON-safe is
dropped on save rather than failing it.

**Worked example:** `examples/extensions/niveles.py` — building levels
(PB, PA…) kept in the document, a side panel to edit them, dashed guides in
parallel elevations and sections, and the cursor snapping to their heights
(«PA» on the tip). Idea and first version by José Castro Basso (FADU–UDELAR)
for teaching architectural representation. It ships with the app but is not loaded:
**Extensions ▸ Example extensions ▸ Niveles** copies it into your plugins
folder (and removes it again); restart to load it. Features only some users need
belong in extensions like this one, not in the core.

## Bundled reference plugins

- `plugins/model_info.py` — Model Info dialog (geometry / materials /
  layers / BIM statistics). A read-only, dialog-based plugin.
- `plugins/python_console.py` — the Python Console. A stateful,
  command-layer-integrated plugin.
- `plugins/solid_inspector.py` — Solid Inspector: per-edge watertightness
  diagnosis with viewport highlighting. A modeless, selection-driven
  plugin.
- `plugins/ai_assistant.py` — the in-app AI assistant (Ctrl+Shift+A): a
  multi-provider chat agent that models through `core.ai`. The reference
  for dialogs with worker threads and per-provider settings.
- `plugins/cam/` — CAM: 2.5D toolpaths and G-code for GRBL and LinuxCNC
  (a port of the 2DCam engine). The reference for **package** plugins: its
  own catalogues (`i18n/*.json` with its own `tr()`), a dock
  (`add_plugin_dock`), a viewport overlay (`overlay_painters`), document
  data (`plugin_data`), and a Qt-free engine tested headlessly. User
  guide: `docs/cam/guide.en.md` (also `.es`, `.pt-BR`).
- `plugins/ai_bridge.py` — the MCP bridge: a localhost TCP server that
  lets an external agent (Claude Code/Desktop) drive the document. The
  reference for socket servers and main-thread relays.

## Roadmap

- Tool registration — **done** (Extensions menu, this page).
- Viewport overlays and document data — **done** (above).
- Importer / exporter registration.
- Side-panel registration, document data, viewport overlays and snap
  providers — **done** (`setup(app)`, above).
- Free-standing plugin docks — **done** (`add_plugin_dock`, above).
- Plugin manifest (`plugin.toml`) for metadata and dependencies.
- Plugin manager UI (install, enable, disable, update) — after the API
  stabilises; a package format would freeze the API too early (see the
  discussion in PR #1).
