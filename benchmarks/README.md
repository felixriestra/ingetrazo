# Benchmarks — the release history

Before every release, `scripts/release_check.sh <previous tag> <real .igz>`
measures the release candidate against the previous release **on the same
document**, and writes `results/<version>.json`. The files stay here, one per
release, so a slow drift or a memory leak shows up as a trend and not only
against the last version.

What each file holds:

- `viewport` — `scripts/bench_session.py`, two runs per version: paint,
  orbit frame, VBO resync, cold chunk rebuild, cold pick index, 60 hovers
  with Select and with Line (and edits inside a container group when the
  document has one). Milliseconds, median of each run.
- `startup_memory` — `scripts/bench_startup.py`: startup time, opening time,
  resident memory and the count of LIVE objects (faces, meshes, GL vertex
  arrays, progress dialogs) after each of six reopenings of the document.
  `leaked_objects` must be empty: a document that is replaced takes its
  objects with it. Resident memory only levels off (the allocator keeps its
  high-water mark), so it is a hint, not the verdict.
- `tests` — the last line of the fast and the slow suites.

Reading them: the same measure varies up to ~20 % between two runs of the
same build (compare the two runs before blaming a change). The script
flags ⚠ a measure more than 25 % slower than the previous release, and
⚠ LEAK when any object count grows across reopenings.

History: the first check (0.5.3, 25-09-2026) caught a leak present since
long before — every New/Open kept the previous document's render chunks
(and through them its faces), and every edited component its GPU buffers:
0.5.2 grew 610 → 1440 MB over five reopenings of the Plaza Yanque.

The reference document so far is the Plaza Yanque model
(`plaza.igz`, 23 MB: 1035 groups, 48 000 faces drawn), kept outside the
repository.
