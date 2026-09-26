# CAM in IngeTrazo — user guide

**Extensions ▸ CAM…** turns a 2D drawing on the stock into G-code for a
router or mill running **GRBL** or **LinuxCNC**. It machines in 2.5D:
outlines, holes and pockets cut in flat layers, straight down from the
stock top. Turning on a rotary axis is not supported yet.

> **Experimental.** Toolpaths are checked before export, but no check
> replaces your eyes. Run every new program as an **air cut** first, with
> Z zero set well above the stock. Then check that the machine goes
> where you expect.

## Quick start: a board with holes

1. Open **Extensions ▸ CAM…** and click **New CAM job…**. Name the file
   first: a CAM job is its own `.igcam` file, not part of the model. While
   the job is open the model is put aside; it comes back untouched.
2. **Set up the job** on the **Job** tab: the stock's width, depth,
   thickness and material, the **controller**, the **units**, the machine
   limits and the **work zero** (the point you will touch off on the
   machine: one of nine points on the stock, at its top or its bottom).
   Then click **Start the job**. Until then nothing else is available.
3. **Draw on the stock** with the drawing tools: lines, rectangles,
   circles, arcs, polygons, offset, move… The stock top is the ground, seen
   from above. 3D tools (push/pull, follow me, the solid tools) are off in a
   CAM job. To reuse a model, select its faces or part before opening CAM
   and click **Import outlines from the model**.
4. Check the tools on the **Tools** tab. Diameter, flute length, feeds and
   spindle speed are your cutter's, not the defaults. **From the library…**
   adds one of your own cutters with its feeds for this material.
5. On **Operations**, choose paths and **Add operation** (see below).
6. Open **Output**. The job calculates by itself and the toolpaths appear
   on the drawing. Verification must say **No problems found**.
7. **Export G-code…**, and **Save** the job (File ▸ Save saves it too).
   **Back to the model** closes the job.

## Operations

The **Operations** tab lists the **paths** of the drawing and follows every
edit: a rectangle is one closed path; lines that meet end to end are one
path; a round hole is a circle. Choose paths in the list, or click any one
of their edges in the drawing (the whole path is chosen and drawn in
orange), then **Add operation**. Closed paths inside another are its holes
or islands.

| Operation | From | Cuts |
|---|---|---|
| Outside profile | a closed path | around the outside: the part stays |
| Inside profile | a closed path (a hole) | around the inside: the hole is cut out |
| Pocket | a closed path, with the paths inside it as islands | clears the whole area to a depth |
| Drilling | circles | one hole per centre, optionally with pecks |
| Engraving | open or closed paths | follows the line itself |
| Facing | nothing (the whole stock) | flattens the stock top |
| Open pocket | a closed path touching the stock's edge | like a pocket, but out through its open edges: a rebate, a notch |
| Bore | circles | a round hole milled with circular moves, no drill needed |
| Slot | a long rectangle, or a straight line | a straight slot the width of the rectangle (or of the tool) |
| Chamfer | closed or open paths | a 45° (or the bit's angle) chamfer on the top edge, with a V-bit |

Each operation has a tool, a depth and a **step-down** (depth per pass). The
remaining settings depend on the operation:

- **Direction.** *Climb* keeps the material on the cutter's right with a
  clockwise spindle. It usually gives the better finish on a rigid
  machine. *Conventional* is the other way.
- **Entry.** *Plunge* goes straight down. *Ramp* descends along the cut
  and comes back to clean the wedge. *Helix* spirals down, and only
  where a helix fits beside the part. Otherwise it ramps.
- **Stock to leave** and **Finishing passes**. Rough a little oversize,
  then take a final light pass. The pass can use a separate
  **finishing tool**.
- **Tabs** hold a cut-out part to the sheet. Enter their number, width
  and height. Cut them with a flush-trim bit or a knife after the job.
- **Lead-in / lead-out** start and end the cut along an edge rather than
  on it.
- **Tool radius offset.** *In the program* is the usual choice.
  *By the controller* writes `G41`/`G42` and leaves the offset to the
  machine's tool table. It works on LinuxCNC only.
- **Drilling.** *Peck depth* drills in steps and clears chips between
  them. *Dwell* pauses at the bottom.
- **Bore.** Any round hole larger than the cutter: it spirals down one
  step-down per turn and finishes the wall with a flat circle. Wide
  bores are cleared to the centre.
- **Slot.** From a rectangle, the slot is that rectangle, with the
  cutter's radius in its inside corners. From a straight edge, it is a
  groove as wide as the tool, one tool radius longer at each end.
- **Chamfer.** Needs a **V-bit** (chamfer mill) in the tool table (new
  jobs have a 90° one). *Width* is how much of the edge is taken off. The
  depth for it depends on the bit's angle: with 90°, depth equals width.
  A closed path with holes gives a chamfer around it and one inside each hole;
  *On a hole's edge* switches the side.
- **Open pocket.** Edges on the board's outline are found and marked
  **open** automatically. The cutter runs right through them, and keeps
  its radius from every other edge. *Open edges* lists them by number;
  select the operation to see the numbers (and the open edges, dashed
  green) in the model.

## Controllers

**GRBL** (GRBL 1.1, grblHAL, FluidNC). GRBL has no tool changer, so a job
with several tools is written as **one file per tool change**, numbered in
running order (`board_01_T1_….nc`, `board_02_T3_….nc`, …). Run them in
order. Between files, change the tool and **set Z zero again**, because
each tool has its own length. Drill cycles are written out as plain
moves.

**LinuxCNC** (2.9 or later). One `.ngc` file. Tool changes are `T# M6`
with `G43 H#`, so the tool lengths come from your machine's tool table.
Drilling uses canned cycles (`G81`/`G82`/`G83`). Export also writes a **tool table** (`.tbl`) next to the program with
the job's tool numbers and diameters, for LinuxCNC's tool table: a
`T# M6` for a tool the machine's table lacks stops the program. Lengths
are left at 0, to be touched off on the machine. The table is in the
job's units, which must be the machine's.

Both take millimetres (`G21`) or inches (`G20`). The **Units** setting
of the job decides which. Comments are in your language, reduced to
plain ASCII.

## Simulation

**Simulate…** (in the job's header, or on **Output**) opens the job on a 3D
block of stock and plays it back in machine time. The clock follows the
time estimate, including tool changes and spindle ramps.

- **Transport:** back to the start, play/pause, **step forward** one
  command, jump to the end, or drag the slider to any moment. Speeds from
  0.25× to 2000×.
- **The G-code** of the job runs beside the view, the line being executed
  highlighted. Click a line to run the job up to it.
- Under the view: the **phase** (roughing, finishing, rapid, lead-in…),
  the tool tip's **X Y Z**, the operation, the removed volume and the tool.
- **Quality:** *Preview* keeps playback smooth; *High quality* uses finer
  cells to look at the finished surface. The cell size is shown beside it.
  **Final simulation** runs the whole job at high quality.
- **Open G-code…** plays any program (from another CAM, or edited by hand)
  on this job's stock; **Job program** goes back.

Material comes off as the cutter passes. Through cuts become holes,
tabs stay standing, and the removed volume is counted. In the drawing,
the toolpaths already cut stay bright, the rest fade, and the cutter is
marked where it is. On a large sheet the grid is coarsened
automatically.

The simulation shows what the toolpath does. It is not a replacement for
an air cut on the machine.

## The tool library

The **Tools** tab holds the cutters of one job. The **tool library** is your
tool cabinet, kept between jobs, with the speeds and feeds known for each
cutter in each material.

- **From the library…** opens the library. Pick a cutter and **Add to job**:
  it joins the job's tool table with its speed, feed and plunge **for the
  job's stock material and machine**. The job keeps a copy; changing the
  library later does not change a saved job.
- **Save to the library** keeps the selected job tool: its geometry, and its
  speed and feeds as *your own data* for this material. Next time the
  library gives your numbers back.

**Where the numbers come from.** For each cutter the library uses the best
data it has, and says which: your own data, the vendor's data or feed chart,
or an **estimate** from a chip-load table. Estimates are marked **≈**; the
built-in tables are general values, not tested data. The line under the
list explains every adjustment: a plastic that melts above a certain
speed, the spindle's range, the machine's feed limits. **Check an estimate
with an air cut before cutting.** Steel has no data in the library: type its
feeds yourself.

A cutter with missing dimensions (cutting length, overall length…) is
marked *incomplete* and cannot go into a job until you fill them in with
**Edit…**. Deleted tools go to the **Trash…**, where they can be restored.

**Vendor catalogues.** **Import catalogue…** reads a vendor's CSV sheet:
Sorotec and CMT (general catalogue and series 193) are built in, and a
*catalogue profile* (JSON) adds another vendor. Every row is checked
first — an inch value in a millimetre column, a cutter six times its
shank, a feed that cannot be right — and shown as new, changed, already
there or rejected, with the reasons. Nothing is added until you press
**Import**, and **Undo last import** takes it back. Values you corrected by
hand are never overwritten by a later import.

**Library file.** The library is one file in your user folder, backed up
automatically (the latest ten copies). On a Mac with 2DCam, **Library
file… ▸ Share 2DCam's library** makes both programs use the same one.

## Saving and reusing a job

A job is its own `.igcam` file: the setup, the tools, the drawing and the
operations.

**Stock templates.** **Save setup as template…** on the **Job** tab keeps
the stock, its material, the machine, the controller, the units and the tool
table under a name. **New from a stock template…** on the CAM start page
starts a job from one: a few common sheets and plates come built in, next
to your own. A template has no drawing and no operations. **Save** (or File ▸ Save) writes it; **Recent jobs** on the CAM
start page and File ▸ Open reopen it. Every change is undoable.

**Change the drawing, and the operations follow.** Move, stretch or offset a
path and every operation made from it is rebuilt from the new path and
recalculated, with all its settings kept. Undo brings both back. An
operation whose path was erased, or changed so much it no longer gives the
same operation, is marked **⚠** and keeps its last geometry until you
delete it or add it again.

**Paths are checked as you draw.** A path that crosses itself, or lies
partly or wholly outside the stock, is marked **⚠** in the list; its
tooltip says why.

## Verification

Export is blocked while verification reports an error:

- a cutter that would cut into the part (a *gouge*);
- a rapid move into or through the stock;
- a cut below the bottom of the stock;
- a depth beyond the tool's flute length;
- a speed or feed beyond the machine's limits (set on the Job tab).

After writing, each file is read back as the controller would read it,
and it must reproduce the toolpath to within 0.001 mm.
