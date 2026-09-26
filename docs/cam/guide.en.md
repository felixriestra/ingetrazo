# CAM in IngeTrazo — user guide

**Extensions ▸ CAM…** turns what you model into G-code for a router or mill
running **GRBL** or **LinuxCNC**. It machines in 2.5D: outlines, holes and
pockets cut in flat layers, straight down from the machining plane.

> **Experimental.** Toolpaths are checked before export, but no check
> replaces your eyes. Run every new program as an **air cut** first, with
> Z zero set well above the stock. Then check that the machine goes
> where you expect.

## Quick start: a board with holes

1. Model the part as a solid: a board with its holes and pockets. Make it
   a group, or split a component into parts.
2. Select the part in the model or in the Parts tray.
3. Open **Extensions ▸ CAM…**. On the **Job** tab, click
   **Part → operations**. CAM adds every operation the part needs:
   - a **pocket** for each blind hole, at its depth;
   - **drilling** for each round through hole a drill in the tool table
     fits;
   - an **inside profile** for every other through hole;
   - an **outside profile** around the outline, with four tabs.
4. Check the tools on the **Tools** tab. Diameter, flute length, feeds and
   spindle speed are your cutter's, not the defaults.
5. Choose the **controller**, the **units** and the **work zero** on the
   **Job** tab. The work zero is the point you will touch off on the
   machine: one of nine points on the stock, at its top or its bottom.
6. Open **Output**. The job calculates by itself. The toolpaths appear in
   the model. Verification must say **No problems found**.
7. **Export G-code…**

## Operations

The **Operations** tab lists the **paths** of the drawing as soon as CAM
opens: a rectangle, or a solid's top outline, is one closed path; lines that
meet end to end are one path; a round hole is a circle. Choose paths in the
list, or click any one of their edges in the model (the whole path is
chosen and drawn in orange), then **Add operation**. With no path chosen,
**Add operation** takes the selection in the model instead, such as a part
from the Parts list.

| Operation | From | Cuts |
|---|---|---|
| Outside profile | a face, a closed chain of edges, a part outline | around the outside: the part stays |
| Inside profile | holes of a face, closed edges | around the inside: the hole is cut out |
| Pocket | a face with its holes as islands, or closed edges | clears the whole area to a depth |
| Drilling | round holes (circles) | one hole per centre, optionally with pecks |
| Engraving | open or closed edges | follows the line itself |
| Facing | nothing (the whole stock) | flattens the stock top |
| Open pocket | a face touching the board's edge | like a pocket, but out through its open edges: a rebate, a notch |
| Bore | round holes | a round hole milled with circular moves, no drill needed |
| Slot | a long rectangle, or a straight edge | a straight slot the width of the rectangle (or of the tool) |
| Chamfer | a face (its outline and holes), edges | a 45° (or the bit's angle) chamfer on the top edge, with a V-bit |

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
  bores are cleared to the centre. **Part → operations** uses a bore for
  round holes no drill in the table matches.
- **Slot.** From a rectangle, the slot is that rectangle, with the
  cutter's radius in its inside corners. From a straight edge, it is a
  groove as wide as the tool, one tool radius longer at each end.
- **Chamfer.** Needs a **V-bit** (chamfer mill) in the tool table (new
  jobs have a 90° one). *Width* is how much of the edge is taken off. The
  depth for it depends on the bit's angle: with 90°, depth equals width.
  A face gives a chamfer around its outline and one inside each hole;
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

**Output ▸ Simulate…** opens the job on a 3D block of stock and plays it
back in machine time. The clock follows the time estimate, including
tool changes and spindle ramps. Play, pause, drag the slider to any
moment, or jump to the end. The speed goes up to 500×.

Material comes off as the cutter passes. Through cuts become holes,
tabs stay standing, and the removed volume is counted. In the model
window, the toolpaths already cut stay bright, the rest fade, and the
cutter is marked where it is. **Resolution** trades detail for speed.
On a large sheet the grid is coarsened automatically.

The simulation shows what the toolpath does. It is not a replacement for
an air cut on the machine.

## Keeping the job with the model

The job is saved in the `.igz` and every change is undoable. If you change
the model later, click **Refresh from the part** on the
**Job** tab. Operations follow the new outline and holes and keep their
settings. An operation that no longer matches keeps its old geometry, and
CAM names it.

## Verification

Export is blocked while verification reports an error:

- a cutter that would cut into the part (a *gouge*);
- a rapid move into or through the stock;
- a cut below the bottom of the stock;
- a depth beyond the tool's flute length;
- a speed or feed beyond the machine's limits (set on the Job tab).

After writing, each file is read back as the controller would read it,
and it must reproduce the toolpath to within 0.001 mm.
