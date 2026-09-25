# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The CAM job as the document keeps it, and the coordinate systems.

Three frames meet here:

- **world** — IngeTrazo's model, in metres (float32 ``QVector3D``);
- **plane** — the machining plane: ``u``/``v`` in millimetres along two
  axes of that plane, ``w`` millimetres along its normal (0 on the plane,
  negative below it). ``extract.py`` is the only code that goes from
  world to plane;
- **work** — the machine's: X/Y/Z in millimetres from the work zero the
  job chose (a corner or the centre of the stock, at its top or bottom).

The saved job keeps its geometry in PLANE coordinates. Work coordinates
depend on the stock size, its margin and the work zero, all of which the
user may change after adding operations; storing plane coordinates means
those edits move nothing on the part. :meth:`CamState.work_job` produces
the engine's job, translated to work coordinates, whenever it is needed.

Qt-free on purpose (tests build it headlessly); persisted as
``scene.plugin_data["cam"]``.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field

from .engine import io
from .engine.models import Job, Region, Tool

PLUGIN_KEY = "cam"
STATE_VERSION = 1


@dataclass
class Frame:
    """The machining plane in the world: ``origin`` (metres) and three unit
    axes. ``n`` points out of the material (toward the spindle)."""
    origin: tuple = (0.0, 0.0, 0.0)
    u: tuple = (1.0, 0.0, 0.0)
    v: tuple = (0.0, 1.0, 0.0)
    n: tuple = (0.0, 0.0, 1.0)

    def to_plane(self, p) -> tuple:
        """World point (metres) → ``(u, v, w)`` in millimetres."""
        d = (p[0] - self.origin[0], p[1] - self.origin[1], p[2] - self.origin[2])
        return (_dot(d, self.u) * 1000.0, _dot(d, self.v) * 1000.0, _dot(d, self.n) * 1000.0)

    def to_world(self, u, v, w=0.0) -> tuple:
        s = 0.001
        return tuple(self.origin[k] + (self.u[k] * u + self.v[k] * v + self.n[k] * w) * s
                     for k in range(3))

    def parallel_to(self, other, degrees: float = 0.5) -> bool:
        return _dot(self.n, other.n) >= math.cos(math.radians(degrees))

    def to_dict(self) -> dict:
        return {"origin": list(self.origin), "u": list(self.u), "v": list(self.v),
                "n": list(self.n)}

    @classmethod
    def from_dict(cls, d) -> "Frame":
        return cls(tuple(d["origin"]), tuple(d["u"]), tuple(d["v"]), tuple(d["n"]))


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def default_tools() -> list:
    """A starter tool table: what a small router shop reaches for first."""
    return [
        Tool(number=1, name="6 mm flat end mill", kind="flatEndMill", diameter=6.0,
             fluteLength=22.0, overallLength=60.0, fluteCount=2, spindleRPM=18_000,
             cuttingFeed=1_500.0, plungeFeed=500.0),
        Tool(number=2, name="3 mm flat end mill", kind="flatEndMill", diameter=3.0,
             fluteLength=12.0, overallLength=38.0, fluteCount=2, spindleRPM=20_000,
             cuttingFeed=900.0, plungeFeed=300.0),
        Tool(number=3, name="5 mm drill", kind="drill", diameter=5.0, fluteLength=40.0,
             overallLength=80.0, fluteCount=2, spindleRPM=8_000, cuttingFeed=300.0,
             plungeFeed=300.0),
    ]


@dataclass
class CamState:
    """What the plugin stores in the document."""
    job: Job = field(default_factory=Job)
    frame: Frame | None = None
    #: Plane bounds of the part geometry ``(umin, vmin, umax, vmax)``, mm.
    bounds: tuple | None = None
    #: Stock beyond the part on each side, mm (automatic stock only).
    margin: float = 10.0
    #: Stock follows bounds + margin; turned off once the user types a size.
    stockAuto: bool = True
    #: ``uid`` of the group (part) the job was set up from, if any.
    sourceGroup: str | None = None
    #: Where each operation's geometry came from: ``{op_id: {...}}``.
    sources: dict = field(default_factory=dict)

    @classmethod
    def new(cls) -> "CamState":
        s = cls()
        s.job.tools = default_tools()
        s.job.setup.safeHeight = 10.0
        s.job.setup.clearanceHeight = 3.0
        s.job.stock.zeroPosition = "materialSurface"
        s.job.stock.referencePoint = "bottomLeft"
        s.job.stock.height = 18.0
        s.job.machine.maximumFeed = 6_000.0
        s.job.machine.rapidFeed = 5_000.0
        s.job.machine.maximumSpindleRPM = 24_000
        s.job.machine.minimumSpindleRPM = 6_000
        s.job.stock.align_origin_to_reference()
        return s

    # ---- persistence --------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "version": STATE_VERSION,
            "job": io.job_to_dict(self.job),
            "frame": self.frame.to_dict() if self.frame else None,
            "bounds": list(self.bounds) if self.bounds else None,
            "margin": self.margin,
            "stockAuto": self.stockAuto,
            "sourceGroup": self.sourceGroup,
            "sources": copy.deepcopy(self.sources),
        }

    @classmethod
    def from_dict(cls, d) -> "CamState":
        s = cls()
        s.job = io.job_from_dict(d.get("job") or {})
        s.frame = Frame.from_dict(d["frame"]) if d.get("frame") else None
        s.bounds = tuple(d["bounds"]) if d.get("bounds") else None
        s.margin = float(d.get("margin", 10.0))
        s.stockAuto = bool(d.get("stockAuto", True))
        s.sourceGroup = d.get("sourceGroup")
        s.sources = dict(d.get("sources") or {})
        return s

    # ---- stock and coordinates ------------------------------------------
    def include_bounds(self, pts) -> None:
        """Grow the part bounds to include plane points ``[(u, v), …]``."""
        pts = list(pts)
        if not pts:
            return
        us = [p[0] for p in pts]
        vs = [p[1] for p in pts]
        b = (min(us), min(vs), max(us), max(vs))
        if self.bounds is not None:
            b = (min(b[0], self.bounds[0]), min(b[1], self.bounds[1]),
                 max(b[2], self.bounds[2]), max(b[3], self.bounds[3]))
        self.bounds = b
        if self.stockAuto:
            self.fit_stock()

    def fit_stock(self) -> None:
        """Stock = part bounds plus the margin on every side."""
        if self.bounds is None:
            return
        st = self.job.stock
        st.width = max(1e-3, self.bounds[2] - self.bounds[0] + 2 * self.margin)
        st.depth = max(1e-3, self.bounds[3] - self.bounds[1] + 2 * self.margin)
        st.align_origin_to_reference()

    def stock_min_plane(self) -> tuple:
        """The stock's minimum corner in plane coordinates (mm). The stock
        is centred on the part bounds; without bounds it starts at 0."""
        st = self.job.stock
        if self.bounds is None:
            return (0.0, 0.0)
        cu = (self.bounds[0] + self.bounds[2]) * 0.5
        cv = (self.bounds[1] + self.bounds[3]) * 0.5
        return (cu - st.width * 0.5, cv - st.depth * 0.5)

    def plane_to_work_offset(self) -> tuple:
        """``(dx, dy)`` so that ``work = plane + (dx, dy)`` in X/Y."""
        umin, vmin = self.stock_min_plane()
        ox, oy, _ = self.job.stock.origin
        return (ox - umin, oy - vmin)

    def work_z(self, w: float) -> float:
        """Plane height (mm, 0 = the machining plane = stock top) → work Z."""
        return self.job.stock.top_z + w

    def work_to_world(self, x, y, z) -> tuple:
        """Work coordinates (mm) → world (metres), for drawing toolpaths."""
        dx, dy = self.plane_to_work_offset()
        frame = self.frame or Frame()
        return frame.to_world(x - dx, y - dy, z - self.job.stock.top_z)

    def work_job(self) -> Job:
        """A copy of the job with every geometry moved to work coordinates
        — what the engine compiles. The stored job is left alone."""
        job = io.job_from_dict(io.job_to_dict(self.job))
        dx, dy = self.plane_to_work_offset()

        def mv(p):
            return (p[0] + dx, p[1] + dy) + tuple(p[2:])

        for op in job.operations:
            g = op.strategy.geometry
            if isinstance(g, Region):
                g.boundary = [mv(p) for p in g.boundary]
                g.islands = [[mv(p) for p in i] for i in g.islands]
            pts = getattr(op.parameters, "points", None)
            if pts:
                op.parameters.points = [mv(p) for p in pts]
        return job
