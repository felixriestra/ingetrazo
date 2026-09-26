# Where the CAM port deliberately differs from 2DCam

The Python engine in `plugins/cam/engine/` is a port of 2DCam's
`TwoDCamCore`, and 2DCam is its specification: `tests/test_cam_parity.py`
runs every case of the corpus exported from 2DCam and requires the same
depth levels and the same cut outline within 0.02 mm. The cases below are
the exceptions. Each one is a place where 2DCam's output cuts into the part,
leaves material it means to remove, or disagrees with its own verifier.
The parity test names each case with its reason.

They are worth fixing in 2DCam too. The case names refer to
`tests/data/cam_fixtures/`.

## Cuts into the part (the port's gouge check catches all three)

1. **A pocket too small for the tool.** `error_pocket_too_small_for_tool`:
   a 5 × 5 mm pocket with a 6 mm cutter. 2DCam offsets each loop on its
   own and keeps the inverted remnant, a 1 × 1 mm loop, then cuts it. That
   takes 1 mm off every wall. The port offsets the region with its islands
   in one Clipper operation. When nothing is left, the port reports
   `region_too_small_for_tool`.
2. **Helix entries across the wall.** `profile_outside_lshape_helix` and
   `pocket_lshape_conventional_helix`: 2DCam spirals around the entry point
   itself, so half the helix, a quarter of the tool diameter, lands on the
   wrong side of the path. The port centres the helix on the waste side:
   outside a part, toward the middle of a hole, or ahead along a pocket row.
   It only does this where the whole circle fits. Otherwise the entry
   falls back to a ramp.
3. **Tangent leads past a corner.** 2DCam starts a profile at a corner and
   extends the lead along the first edge's line, backwards. On an inside
   profile that line runs into the wall. With leads or a helix, the port
   starts at the middle of the longest edge. Leads then stay on that edge,
   clamped to its length. Tabs are placed by fraction of the path length,
   so they sit at different places than in 2DCam
   (`profile_outside_tabs_leads_ramp`, compared in plan only).

## Leaves material or crosses the stock

4. **Ramp wedges.** 2DCam's ramp descends toward the row end and carries
   on, so the wedge under the ramp is never cut at full depth
   (`pocket_circle_ramp`). The port ramps down, comes back to the start at
   depth, and then cuts the row. It cuts everything 2DCam cuts, plus the
   wedge.
5. **Conventional facing exit.** 2DCam ends every facing with a rapid to
   (last X, maximum Y, safe Z). A conventional pass ends at the minimum-Y
   edge, so that rapid crosses the stock below its top. 2DCam's own
   verifier reports `Lateral rapid motion intersects the stock volume`
   (`facing_conventional_ramp`). The port retracts straight up.

## Bore, slot, chamfer and open pocket

6. **Bores wider than twice the tool.** 2DCam mills one ring at radius
   `(D − d)/2`. That only reaches the centre when `D ≤ 2d`. A 20 mm bore
   with a 6 mm cutter kept an 8 mm post standing, and a 30 mm bore an
   18 mm one (`bore_basic`, `bore_large_core`). The port clears the core
   at every depth level with concentric circles stepping inward, 0.75 of
   the tool diameter apart, down to a circle that covers the centre.
7. **Open pockets.** 2DCam uses the region as the area the cutter centre
   may cover, on every edge, whatever `openEdgeIndices` says. A closed
   wall of an open pocket was cut into by a full tool radius. The port
   honours the open edges. Before the usual inward offset, the region is
   grown outward by exactly the cutter radius past each open edge (and
   past corners between two open edges). The cutter centre then runs
   along each open edge, clearing up to it and one radius beyond, and
   keeps its radius from every closed wall and island. With every edge
   open, that is 2DCam's area exactly (`open_pocket_region_all_open`).
   2DCam also leaves each open pocket's outer contour pass open; the port
   closes it. An automatic open pocket (no region) is the whole stock top,
   so it is the closed pocket of the stock, as in 2DCam.
8. **Chamfer on a hole's edge** (`inside`, the port's). 2DCam offsets a
   closed chamfer path outward only, so it can chamfer a part's outline
   but not a hole. With `inside` the port offsets into the hole. An open
   polyline is offset to its left with mitred corners, where 2DCam rounds
   them; this matters only at the corners of open paths.
9. **Slots**, documented rather than changed. 2DCam's rows run from
   `start` to `end`, so a slot cuts a rounded rectangle that reaches one
   tool radius past both ends, with tool-radius corners. It does not cut
   a stadium with `width/2` ends. The port keeps that, and says so. When
   a slot is made from a rectangle in the model, `start` and `end` are
   the rectangle's ends moved in by the tool radius, so the cut is that
   rectangle.

## Behaviour

10. **Climb and conventional.** 2DCam's offsets always come back clockwise,
   whatever the input orientation. So its "climb" is true climb (material
   on the cutter's right with an M3 spindle) only on outside profiles.
   Inside profiles and pocket walls come out conventional. The port uses
   the physical rule: clockwise around a part or a pocket island,
   counter-clockwise around a hole or a pocket wall, and the reverse for
   conventional. Cutter compensation follows: climb is `G41`, conventional
   is `G42`.
11. **An inside profile that splits.** If the inward offset of a dumbbell
   splits into two loops, 2DCam cuts only the largest. The port cuts
   every loop.
12. **Entries start from the previous floor.** 2DCam's ramp and helix
   begin at the stock top on every depth pass. On pass 2 of a small helix
   that is a 32° dive. The port starts them at the previous pass's floor,
   which is already cleared. A helix takes as many turns as it needs to
   stay at or below 10°.
13. **A lift before the first move.** Every operation starts with a Z-only
   rapid to its safe height (`RetractZ`). 2DCam's first move is a
   straight-line rapid from wherever the spindle stands to a point above
   the part.
14. **Depth passes.** `ceil(depth / step)` gets a 1e-9 epsilon, so 1.0 /
    0.1 makes 10 passes, not an 11th pass at the same depth.

## Simulation

15. **Cell budgets.** 2DCam's playback resolutions are ported as they are
    (a twentieth of the smallest tool, 0.1–1 mm, for *Preview*; a fortieth,
    from 0.05 mm, for *High quality*), but the budgets that coarsen the grid
    on a big stock are 250 000 cells while playing and 1 500 000 for a final
    run, where 2DCam allows 1 M and 6 M. The 3D view is re-meshed in Python
    on every frame, about 60 ms per million cells. A 2440 × 1220 sheet
    therefore plays at about 3.4 mm cells, where 2DCam would use 1.7 mm.
16. **What is simulated.** 2DCam posts the program, reads it back and
    simulates the reconstruction. The port simulates the canonical toolpath
    and shows the posted program beside it, line by line. Every export
    already checks that the posted file reads back as that toolpath within
    0.001 mm (`verify.round_trip`), so the two are the same motion.
    *Open G-code…* simulates a program from elsewhere through the same
    reader, as 2DCam's imported G-code does.
17. **Sweeps.** 2DCam stamps the cutter sample by sample; the port stamps
    the same disc of cells around every sample of a move in one NumPy pass.
    The result is the same, and a final run of a small job takes seconds.

## Tool library

The library (`plugins/cam/toollib`) keeps 2DCam's schema, so one
`ToolLibrary.sqlite` serves both apps, and its resolver, validator and CSV
import match 2DCam's answers on the corpus in
`tests/data/cam_toollib_fixtures` (`swift run export-toollib-fixtures`).
Where it differs:

18. **Form tools stay in the library.** They round-trip through a shared
    file (profile points and scale mode included) but cannot go into a
    job: the port's generators have no form-tool cutting.
19. **Resolver notes are codes.** 2DCam's `clamped` strings are English
    sentences; the port returns `Note(code, params)` and the UI translates
    them. The numbers they carry are 2DCam's (for example the depth,
    rounded the same way).
20. **Validator issues carry parameters, not messages.** The codes, the
    severities and the field names are 2DCam's (they are stored in a
    shared file's `validation_issue` table, whose `message` column gets
    the code).
21. **Backups have their own prefix.** 2DCam writes
    `Backups/ToolLibrary-<time>.sqlite`; the port writes
    `ToolLibrary-IngeTrazo-<time>.sqlite` and rotates only its own, so a
    shared library never loses 2DCam's backups. Recovery takes the newest
    of either.
22. **A job's material picks the class.** 2DCam's library window has its
    own material picker. The port starts from the job's stock material
    (light wood → softwood, dark wood → hardwood, plastic → acrylic…) and
    lets the user change it; steel has no class, so nothing is guessed
    for it.
23. **Traits are not stored.** 2DCam's schema has `tool_trait`, but its
    repository never reads or writes it; the port does the same.

## Additions that keep parity when unused

- **Peck drilling and dwell** (`peckDepth`, `dwellSeconds`). 2DCam drills
  in one plunge. With both at 0 the moves are 2DCam's exactly. LinuxCNC
  gets `G81`, `G82` or `G83`. GRBL gets the same moves written out, with
  LinuxCNC's 0.254 mm peck back-off.
- **Operation comments.** Every operation gets an `Operation: <name>`
  comment. Comments are templates the post translates, then reduces to
  ASCII.

## Not ported (v1 scope)

Relief (image and STL), fixtures and clamps in the verifier, the
Mach3, Mach4 and Fanuc posts, and form-tool cutting. See `docs/cam-plan.md`.
