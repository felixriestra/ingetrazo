# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Felix Riestra (2DCam engine port).
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""JSON in and out: CAM jobs, 2DCam projects and canonical toolpaths.

The job format IS 2DCam's ``MachiningProject`` encoding — same keys, and
Swift's ``Codable`` shape for enums with payloads (``{"pocket": {"_0":
{...}}}``, ``{"region": {"_0": {...}}}``) — plus a few keys 2DCam ignores:

- ``igtcam``: ``{"format": 1, "displayUnits": "millimeters"|"inches",
  "post": {...}}``. The numbers of a saved job are ALWAYS millimetres, and
  ``units`` is written as ``"millimeters"`` so 2DCam reads the same job
  correctly; the job's own display/output unit lives here.
- ``peckDepth`` / ``dwellSeconds`` inside drilling parameters.

Reading a real 2DCam project in inches converts every length and feed to
millimetres (2DCam projects are native-unit) and keeps ``units`` as the
display unit.
"""
from __future__ import annotations

import math

from .models import (MM_PER_INCH, FacingParameters, DrillingParameters, EngravingParameters,
                     Job, Machine, MachineSetup, Operation, PocketParameters, PostOptions,
                     ProfileParameters, Region, Stock, Strategy, Tab, Tolerance, Tool,
                     ToolHolder, new_id)
from .toolpath import (Arc, Comment, Coolant, CutterCompensation, DrillCycle, Dwell, Linear,
                       Rapid, RetractZ, Section, SpindleStart, SpindleStop, ToolChange,
                       Toolpath)

FORMAT = 1


# ---- small helpers -------------------------------------------------------

def _p3(d, default=(0.0, 0.0, 0.0)):
    if not isinstance(d, dict):
        return default
    return (float(d.get("x", 0.0)), float(d.get("y", 0.0)), float(d.get("z", 0.0)))


def _p2(d):
    return (float(d.get("x", 0.0)), float(d.get("y", 0.0)))


def _d3(p):
    return {"x": p[0], "y": p[1], "z": p[2]}


def _d2(p):
    return {"x": p[0], "y": p[1]}


def _enum(d):
    """Swift enum with payload → ``(case, payload)``. ``{"region": {"_0":
    x}}`` → ``("region", x)``; ``{"automatic": {}}`` → ``("automatic", {})``;
    a bare string → ``(string, None)``."""
    if isinstance(d, str):
        return d, None
    if isinstance(d, dict) and len(d) == 1:
        case, payload = next(iter(d.items()))
        if isinstance(payload, dict) and set(payload) == {"_0"}:
            payload = payload["_0"]
        return case, payload
    return None, None


def _fields(cls, d: dict, skip=()):
    """Kwargs for dataclass ``cls`` from the keys of ``d`` it knows."""
    names = cls.__dataclass_fields__.keys()
    return {k: v for k, v in d.items() if k in names and k not in skip}


# ---- reading --------------------------------------------------------------

def job_from_dict(d: dict) -> Job:
    """A :class:`Job` from a 2DCam project or a job this module wrote."""
    ext = d.get("igtcam") if isinstance(d.get("igtcam"), dict) else None
    native_inches = ext is None and d.get("units") == "inches"
    s = MM_PER_INCH if native_inches else 1.0

    def L(v):
        return None if v is None else float(v) * s

    job = Job()
    job.name = str(d.get("name", job.name))
    job.units = (ext.get("displayUnits", "millimeters") if ext is not None
                 else d.get("units", "millimeters"))
    if job.units not in ("millimeters", "inches"):
        job.units = "millimeters"

    st = d.get("stock") or {}
    job.stock = Stock(**_fields(Stock, st, skip=("origin", "width", "depth", "height")))
    job.stock.width = L(st.get("width", 100.0 / s))
    job.stock.depth = L(st.get("depth", 75.0 / s))
    job.stock.height = L(st.get("height", 20.0 / s))
    job.stock.origin = tuple(v * s for v in _p3(st.get("origin")))

    se = d.get("setup") or {}
    job.setup = MachineSetup(workOffset=se.get("workOffset", "G54"),
                             safeHeight=L(se.get("safeHeight", 10.0 / s)),
                             clearanceHeight=L(se.get("clearanceHeight", 5.0 / s)))

    m = d.get("machine") or {}
    job.machine = Machine(**_fields(Machine, m, skip=(
        "maximumFeed", "maximumPlungeFeed", "rapidFeed", "cuttingAcceleration",
        "rapidAcceleration", "travelMinimum", "travelMaximum", "workOriginInMachineCoordinates")))
    for key in ("maximumFeed", "rapidFeed", "cuttingAcceleration", "rapidAcceleration"):
        if key in m:
            setattr(job.machine, key, L(m[key]))
    job.machine.maximumPlungeFeed = L(m.get("maximumPlungeFeed"))
    for key in ("travelMinimum", "travelMaximum", "workOriginInMachineCoordinates"):
        if key in m:
            setattr(job.machine, key, tuple(v * s for v in _p3(m[key])))

    t = d.get("machiningTolerance") or {}
    job.tolerance = Tolerance(**{k: L(v) if k != "angularDegrees" else float(v)
                                 for k, v in _fields(Tolerance, t).items()})

    job.tools = [_tool(td, L) for td in d.get("tools") or []]
    job.operations = [_operation(od, L) for od in d.get("operations") or []]

    post = (ext or {}).get("post")
    if isinstance(post, dict):
        job.post = PostOptions(**_fields(PostOptions, post))
    return job


def _tool(d, L) -> Tool:
    t = Tool(**_fields(Tool, d, skip=("diameter", "fluteLength", "overallLength", "cornerRadius",
                                        "cuttingFeed", "plungeFeed", "tipDiameter",
                                        "recommendedStepdown", "holder", "id")))
    t.id = str(d.get("id") or new_id())
    for key in ("diameter", "fluteLength", "overallLength", "cornerRadius", "cuttingFeed",
                "plungeFeed", "tipDiameter", "recommendedStepdown"):
        if key in d:
            setattr(t, key, L(d[key]))
    h = d.get("holder")
    if isinstance(h, dict):
        t.holder = ToolHolder(L(h.get("diameter", 25 / 1)), L(h.get("length", 40)),
                              L(h.get("offsetFromTip", 35)))
    return t


def _loop(pts, L):
    return [(L(p["x"]), L(p["y"])) for p in pts or []]


def _strategy(d, L) -> Strategy:
    d = d or {}
    st = Strategy(**_fields(Strategy, d, skip=(
        "geometry", "topHeight", "bottomHeight", "stockAllowance", "leadInLength",
        "leadOutLength", "safeHeightOverride", "clearanceHeightOverride", "tabs")))
    for key in ("topHeight", "bottomHeight", "stockAllowance", "leadInLength", "leadOutLength",
                "safeHeightOverride", "clearanceHeightOverride"):
        if key in d:
            setattr(st, key, L(d[key]))
    case, payload = _enum(d.get("geometry", {"automatic": {}}))
    if case == "region" and isinstance(payload, dict):
        st.geometry = Region(boundary=_loop(payload.get("boundary"), L),
                             islands=[_loop(i, L) for i in payload.get("islands") or []],
                             openEdgeIndices=sorted(payload.get("openEdgeIndices") or []))
    st.tabs = [Tab(pathFraction=float(t.get("pathFraction", 0.0)), width=L(t.get("width", 5)),
                   height=L(t.get("height", 1)), id=str(t.get("id") or new_id()))
               for t in d.get("tabs") or []]
    return st


_LENGTH_PARAMS = ("depth", "stepDown", "inset", "stockAllowance", "peckDepth")


def _parameters(kind, payload, L):
    payload = payload or {}
    cls = {"facing": FacingParameters, "outsideProfile": ProfileParameters,
           "insideProfile": ProfileParameters, "pocket": PocketParameters,
           "drilling": DrillingParameters, "engraving": EngravingParameters}.get(kind)
    if cls is None:
        return None
    kw = _fields(cls, payload, skip=("points",))
    for key in _LENGTH_PARAMS:
        if key in kw:
            kw[key] = L(kw[key])
    obj = cls(**kw)
    if "points" in payload and hasattr(obj, "points"):
        obj.points = [tuple(v * L(1.0) for v in _p3(p)) for p in payload["points"]]
    return obj


def _operation(d, L) -> Operation:
    kind = d.get("kind", "outsideProfile")
    case, payload = _enum(d.get("parameters", {}))
    params = _parameters(kind, payload, L) if case == kind else None
    op = Operation(name=str(d.get("name", "Operation")), kind=kind,
                   toolID=d.get("toolID"), finishingToolID=d.get("finishingToolID"),
                   isEnabled=bool(d.get("isEnabled", True)), parameters=params,
                   strategy=_strategy(d.get("strategy"), L), id=str(d.get("id") or new_id()))
    if case is not None and case != kind:
        op.parameters = "mismatch"            # the compiler reports parameter_mismatch
    return op


# ---- writing --------------------------------------------------------------

def job_to_dict(job: Job) -> dict:
    """The job as 2DCam-compatible JSON (millimetres, see module docstring)."""
    st = job.stock
    m = job.machine
    return {
        "formatVersion": 1,
        "name": job.name,
        "units": "millimeters",
        "igtcam": {"format": FORMAT, "displayUnits": job.units,
                   "post": dict(job.post.__dict__)},
        "stock": {"isConfigured": st.isConfigured, "shape": st.shape, "material": st.material,
                  "width": st.width, "depth": st.depth, "height": st.height,
                  "origin": _d3(st.origin), "referencePoint": st.referencePoint,
                  "jobSidedness": "singleSided", "zeroPosition": st.zeroPosition},
        "setup": {"workOffset": job.setup.workOffset, "safeHeight": job.setup.safeHeight,
                  "clearanceHeight": job.setup.clearanceHeight},
        "machine": {**{k: getattr(m, k) for k in (
            "name", "maximumSpindleRPM", "minimumSpindleRPM", "maximumFeed", "rapidFeed",
            "cuttingAcceleration", "rapidAcceleration", "spindleRampSeconds",
            "toolChangeSeconds", "coolantDelaySeconds", "enforcesTravelLimits")},
            **({"maximumPlungeFeed": m.maximumPlungeFeed}
               if m.maximumPlungeFeed is not None else {}),
            "travelMinimum": _d3(m.travelMinimum), "travelMaximum": _d3(m.travelMaximum),
            "workOriginInMachineCoordinates": _d3(m.workOriginInMachineCoordinates),
            # 2DCam-valid values for its own post selection (ours is igtcam.post).
            "controller": "genericISO", "postprocessor": "genericFanuc"},
        "machiningTolerance": dict(job.tolerance.__dict__),
        "tools": [_tool_dict(t) for t in job.tools],
        "operations": [_operation_dict(o) for o in job.operations],
        "fixtures": [],
    }


def _tool_dict(t: Tool) -> dict:
    d = {k: getattr(t, k) for k in (
        "id", "number", "name", "kind", "diameter", "fluteLength", "overallLength",
        "fluteCount", "cornerRadius", "spindleRPM", "cuttingFeed", "plungeFeed")}
    for k in ("includedAngle", "tipDiameter", "recommendedStepdown",
              "recommendedStepdownProvenance"):
        if getattr(t, k) is not None:
            d[k] = getattr(t, k)
    d["recommendedStepdownNotes"] = []
    if t.holder is not None:
        d["holder"] = dict(t.holder.__dict__)
    return d


def _strategy_dict(s: Strategy) -> dict:
    d = {k: getattr(s, k) for k in (
        "topHeight", "stockAllowance", "direction", "compensation", "entry", "leadInLength",
        "leadOutLength", "finishingPasses", "ordering")}
    for k in ("bottomHeight", "safeHeightOverride", "clearanceHeightOverride"):
        if getattr(s, k) is not None:
            d[k] = getattr(s, k)
    if s.geometry is None:
        d["geometry"] = {"automatic": {}}
    else:
        d["geometry"] = {"region": {"_0": {
            "boundary": [_d2(p) for p in s.geometry.boundary],
            "islands": [[_d2(p) for p in i] for i in s.geometry.islands],
            "openEdgeIndices": list(s.geometry.openEdgeIndices)}}}
    d["tabs"] = [{"id": t.id, "pathFraction": t.pathFraction, "width": t.width,
                  "height": t.height} for t in s.tabs]
    return d


def _operation_dict(o: Operation) -> dict:
    p = o.parameters
    pd = {}
    if p is not None and hasattr(p, "__dataclass_fields__"):
        pd = {k: v for k, v in p.__dict__.items() if k != "points"}
        if hasattr(p, "points"):
            pd["points"] = [_d3(tuple(pt) + (0.0,) * (3 - len(pt))) for pt in p.points]
    d = {"id": o.id, "name": o.name, "kind": o.kind, "isEnabled": o.isEnabled,
         "parameters": {o.kind: {"_0": pd}}, "strategy": _strategy_dict(o.strategy),
         "calculationState": {"needsRecalculation": {}}}
    if o.toolID is not None:
        d["toolID"] = o.toolID
    if o.finishingToolID is not None:
        d["finishingToolID"] = o.finishingToolID
    return d


# ---- toolpaths ---------------------------------------------------------------

def toolpath_from_2dcam(d: dict, scale: float = 1.0) -> Toolpath:
    """A 2DCam ``CanonicalToolpath`` (``toolpath.json`` of the fixtures).
    ``scale`` converts lengths (25.4 for an inch project)."""
    def P(v):
        x, y, z = _p3(v)
        return (x * scale, y * scale, z * scale)

    cmds = []
    for c in d.get("commands", []):
        case, payload = _enum(c)
        pl = payload if isinstance(payload, dict) else {}
        if case == "comment":
            cmds.append(Comment(str(payload if not isinstance(payload, dict) else pl.get("_0", ""))))
        elif case == "toolChange":
            cmds.append(ToolChange(int(pl["number"])))
        elif case == "spindleStart":
            cmds.append(SpindleStart(int(pl["rpm"]), bool(pl.get("clockwise", True))))
        elif case == "spindleStop":
            cmds.append(SpindleStop())
        elif case == "coolant":
            cmds.append(Coolant(bool(pl["enabled"])))
        elif case == "cutterCompensation":
            cmds.append(CutterCompensation(str(payload if isinstance(payload, str) else pl.get("_0"))))
        elif case == "rapid":
            cmds.append(Rapid(P(pl["to"])))
        elif case == "linear":
            cmds.append(Linear(P(pl["to"]), float(pl["feed"]) * scale))
        elif case in ("arcClockwise", "arcCounterclockwise"):
            cmds.append(Arc(P(pl["to"]), P(pl["centerOffset"]), float(pl["feed"]) * scale,
                            clockwise=case == "arcClockwise"))
        elif case == "dwell":
            cmds.append(Dwell(float(pl["seconds"])))
    sections = [Section(s["kind"], int(s["startCommandIndex"]), int(s["commandCount"]),
                        s.get("operationID"), s.get("operationName"))
                for s in d.get("sections") or []]
    return Toolpath(cmds, sections)


def toolpath_to_dict(tp: Toolpath) -> dict:
    """A toolpath as JSON (our shape; for caching and debugging)."""
    out = []
    for c in tp.commands:
        if isinstance(c, Comment):
            out.append({"comment": c.text, "params": c.params})
        elif isinstance(c, ToolChange):
            out.append({"toolChange": c.number})
        elif isinstance(c, SpindleStart):
            out.append({"spindleStart": c.rpm, "clockwise": c.clockwise})
        elif isinstance(c, SpindleStop):
            out.append({"spindleStop": True})
        elif isinstance(c, Coolant):
            out.append({"coolant": c.enabled})
        elif isinstance(c, CutterCompensation):
            out.append({"compensation": c.mode})
        elif isinstance(c, Rapid):
            out.append({"rapid": list(c.to)})
        elif isinstance(c, RetractZ):
            out.append({"retractZ": c.z})
        elif isinstance(c, Linear):
            out.append({"linear": list(c.to), "feed": c.feed})
        elif isinstance(c, Arc):
            out.append({"arc": list(c.to), "center": list(c.center), "feed": c.feed,
                        "clockwise": c.clockwise})
        elif isinstance(c, Dwell):
            out.append({"dwell": c.seconds})
        elif isinstance(c, DrillCycle):
            out.append({"drill": [c.x, c.y], "r": c.r, "bottom": c.bottom, "feed": c.feed,
                        "peck": c.peck, "dwell": c.dwell})
    return {"commands": out,
            "sections": [{"kind": s.kind, "start": s.start, "count": s.count,
                          "operation": s.operation_id} for s in tp.sections]}


def is_finite_job(job: Job) -> bool:
    vals = [job.stock.width, job.stock.depth, job.stock.height, *job.stock.origin]
    return all(math.isfinite(v) for v in vals)
