"""The two mesh / solve presets, "Optimization" and "Simulation" (owner 2026-11-17).

The values are in ONE data file, ``config/mesh_presets.yaml`` (versioned; each value carries its source: owner / measured /
provisional).  A preset fixes every numerical setting of a solve; the mesh sizes are RULES relative to the machine and are
resolved to millimetres per motor here (:func:`resolve`), so one preset works for every motor from 12 mm to 400 mm.

This module only reads the file and computes numbers; it never touches a solve.  The transient route uses it when a request
names a ``preset`` (opt-in): the preset FILLS the fields the request left empty, and a field the request set to something else
is reported as ``custom`` (never silently).  Every result of such a request carries :func:`record` (name, version, resolved
and effective settings).
"""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import yaml

SOURCES = ("owner", "measured", "provisional")
PRESET_NAMES = ("optimization", "simulation")
#: the file, beside the other library files
PATH = Path(__file__).resolve().parents[2] / "config" / "mesh_presets.yaml"
COIL_REL_CHOICES = (0.5, 1.0, 2.0)

_SCALAR_KEYS = ("gap_layers", "n_periods", "outer_air_factor", "element_order", "eddy", "rotor_eddy", "demag",
                "global_edge_factor", "coil_rel")

class PresetError(ValueError):
    """The data file is invalid, or a preset cannot be resolved for this machine."""


def _check_source(where: str, spec: Mapping[str, Any]) -> None:
    if spec.get("source") not in SOURCES:
        raise PresetError(f"{where}: 'source' must be one of {SOURCES}, got {spec.get('source')!r}")


def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def validate(doc: Any) -> Dict[str, Any]:
    """The data file, checked.  Raises :class:`PresetError` naming the first problem."""
    if not isinstance(doc, dict) or not isinstance(doc.get("version"), int) or doc["version"] < 1:
        raise PresetError("mesh_presets: 'version' must be a whole number >= 1")
    classes = doc.get("size_classes")
    if not isinstance(classes, list) or not classes:
        raise PresetError("mesh_presets: 'size_classes' missing")
    prev = None
    for i, c in enumerate(classes):
        w = f"size_classes[{i}]"
        if not isinstance(c, dict):
            raise PresetError(f"{w}: not a mapping")
        _check_source(w, c)
        lo, hi = _num(c.get("from_mm")), _num(c.get("to_mm"))
        if lo is None or hi is None or not (0 < lo < hi) or (prev is not None and lo != prev):
            raise PresetError(f"{w}: from_mm < to_mm, and each class starts where the previous one ends")
        prev = hi
        for k in ("c_D", "f_min", "air"):
            v = _num(c.get(k))
            if v is None or v <= 0:
                raise PresetError(f"{w}.{k}: a positive number required")
        if c["f_min"] >= 1:
            raise PresetError(f"{w}.f_min: a fraction below 1")
    presets = doc.get("presets")
    if not isinstance(presets, dict) or tuple(presets) != PRESET_NAMES:
        raise PresetError(f"mesh_presets: 'presets' must be exactly {PRESET_NAMES} (in this order)")
    for name, p in presets.items():
        w = f"preset '{name}'"
        if not isinstance(p, dict) or not p.get("label"):
            raise PresetError(f"{w}: needs a label")
        for key in ("steps_per_period",) + (("cogging_steps_per_period",) if name == "simulation" else ()):
            s = p.get(key)
            if not isinstance(s, dict):
                raise PresetError(f"{w}: '{key}' missing")
            _check_source(f"{w}.{key}", s)
            if not ((_num(s.get("value")) or 0) >= 1 or (_num(s.get("min")) or 0) >= 1):
                raise PresetError(f"{w}.{key}: needs 'value' or 'min' >= 1")
        for key in _SCALAR_KEYS:
            s = p.get(key)
            if not isinstance(s, dict) or "value" not in s:
                raise PresetError(f"{w}: '{key}' missing")
            _check_source(f"{w}.{key}", s)
        gl = _num(p["gap_layers"]["value"])
        if gl is None or not (1 <= gl <= 2):
            raise PresetError(f"{w}.gap_layers: must be 1..2 (owner: never above 2 rows per side)")
        if p["element_order"]["value"] != 2:
            raise PresetError(f"{w}.element_order: only 2 (P2) exists")
        g = _num(p["global_edge_factor"]["value"])
        if g is None or not (0 < g <= 3):
            raise PresetError(f"{w}.global_edge_factor: 0 < g <= 3")
        if _num(p["coil_rel"]["value"]) not in COIL_REL_CHOICES:
            raise PresetError(f"{w}.coil_rel: one of {COIL_REL_CHOICES}")
        ns = p.get("n_sectors")
        if not isinstance(ns, dict) or ns.get("rule") != "symmetry":
            raise PresetError(f"{w}: n_sectors must be {{rule: symmetry}}")
        _check_source(f"{w}.n_sectors", ns)
    return doc


@lru_cache(maxsize=1)
def _load_cached(path: str, mtime_ns: int) -> Dict[str, Any]:
    return validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def load(path: Optional[Path] = None) -> Dict[str, Any]:
    """The validated data file (cached until it changes on disk)."""
    p = Path(path or PATH)
    return _load_cached(str(p), p.stat().st_mtime_ns)


def names() -> List[str]:
    return list(PRESET_NAMES)


def version() -> int:
    return int(load()["version"])


def _derived(geometry: Mapping[str, Any]) -> Dict[str, Any]:
    """The geometry with every derived radius recomputed from the primaries (a stored derived value goes stale)."""
    g = dict(geometry)
    try:
        from motor_ai_sim.geometry.motor_geometry import derived_geometry
        g.update(derived_geometry(g))
    except Exception:                                       # noqa: BLE001 - no derivation: the given values stand
        pass
    return g


def _positive(g: Mapping[str, Any], key: str) -> float:
    v = _num(g.get(key))
    if v is None or v <= 0:
        raise PresetError(f"the machine has no {key}: a mesh preset cannot be resolved for it")
    return v


def size_class(diameter_mm: float) -> Dict[str, Any]:
    for c in load()["size_classes"]:
        if c["from_mm"] <= diameter_mm < c["to_mm"]:
            return c
    raise PresetError(f"stator diameter {diameter_mm:g} mm is outside the preset range "
                      f"{load()['size_classes'][0]['from_mm']:g}..{load()['size_classes'][-1]['to_mm']:g} mm")


def h0_mm(geometry: Mapping[str, Any]) -> Dict[str, float]:
    """h0 = min(c_D D, tooth_width/2, yoke/2, magnet_height/3, pole_pitch/6) and its inputs (mm); PresetError when a feature is missing."""
    g = _derived(geometry)
    d = _positive(g, "stator_diameter")
    cls = size_class(d)
    tooth, yoke, mag = _positive(g, "tooth_width"), _positive(g, "core_thickness"), _positive(g, "magnet_height")
    poles = _positive(g, "num_poles")
    r_gap = _positive(g, "stator_inner_radius")
    pitch = 2.0 * math.pi * r_gap / poles
    parts = {"diameter": cls["c_D"] * d, "tooth": tooth / 2.0, "yoke": yoke / 2.0, "magnet": mag / 3.0, "pole_pitch": pitch / 6.0}
    h0 = min(parts.values())
    return {"h0": h0, "limit": min(parts, key=parts.get), "class": cls, **{f"{k}_mm": v for k, v in parts.items()}}


def cogging_steps(geometry: Mapping[str, Any], spec: Mapping[str, Any]) -> int:
    """Steps for the 20 C cogging run: max(120, multiple_of x ceil(samples_per_cycle x cycles per electrical period / multiple_of))."""
    from motor_ai_sim.simulation.eddy_steps import cogging_cycles_per_period
    cycles = cogging_cycles_per_period(geometry.get("num_slots"), geometry.get("num_poles"))
    k = int(spec.get("multiple_of", 24))
    need = k * math.ceil(int(spec.get("samples_per_cycle", 6)) * cycles / k) if cycles else 0
    return int(max(int(spec["min"]), need))


def steps_for(preset: str, geometry: Mapping[str, Any], *, cogging: bool = False) -> int:
    """Steps per electrical period of ``preset`` for this machine: a fixed value, or ``max(min, per_cogging_cycle x cycles)``;
    the cogging run of the Simulation preset has its own rule."""
    from motor_ai_sim.simulation.eddy_steps import cogging_cycles_per_period
    p = _preset(preset)
    if cogging and "cogging_steps_per_period" in p:
        return cogging_steps(geometry, p["cogging_steps_per_period"])
    s = p["steps_per_period"]
    if "value" in s:
        return int(s["value"])
    cycles = cogging_cycles_per_period(geometry.get("num_slots"), geometry.get("num_poles"))
    return int(max(int(s["min"]), int(s.get("per_cogging_cycle", 0)) * cycles))


def _preset(name: str) -> Dict[str, Any]:
    key = str(name or "").strip().lower()
    if key not in PRESET_NAMES:
        raise PresetError(f"unknown mesh preset {name!r}; the presets are {', '.join(PRESET_NAMES)}")
    return load()["presets"][key]


def resolve(preset: str, geometry: Mapping[str, Any], *, cogging: bool = False) -> Dict[str, Any]:
    """Every setting of ``preset`` in millimetres / counts for THIS machine (``geometry`` = the machine's geometry dict).

    ``{"name", "version", "values": {...request-shaped keys...}, "sources": {key: source}, "h0": {...}}``.  The keys in
    ``values`` are the transient route's own parameter names, so a caller can pass them on unchanged.
    """
    p = _preset(preset)
    h = h0_mm(geometry)
    cls = h["class"]
    slots, poles = _num(geometry.get("num_slots")), _num(geometry.get("num_poles"))
    if not slots or not poles:
        raise PresetError("the machine has no num_slots / num_poles: a mesh preset cannot be resolved for it")
    sectors = max(1, math.gcd(int(round(slots)), int(round(poles))))
    h0 = h["h0"]
    edge = float(p["global_edge_factor"]["value"]) * h0
    values: Dict[str, Any] = {
        "n_steps_per_period": steps_for(preset, geometry, cogging=cogging),
        "gap_layers": float(p["gap_layers"]["value"]),
        "n_periods": float(p["n_periods"]["value"]),
        "mesh_size_mm": round(edge, 4),
        "min_size_mm": round(cls["f_min"] * h0, 4),
        "outer_air_factor": float(p["outer_air_factor"]["value"]),
        "n_sectors": sectors,
        # iron and magnet at h0 (the reference of the pilots), the exterior air at a = air x h0, the wire cell relative to the wire
        "component_mesh": {"stator": round(h0, 4), "rotor": round(h0, 4), "magnet": round(h0, 4),
                           "outer": round(cls["air"] * h0, 4), "coil_rel": float(p["coil_rel"]["value"])},
        "element_order": int(p["element_order"]["value"]),
        "eddy": bool(p["eddy"]["value"]),
        "rotor_eddy": bool(p["rotor_eddy"]["value"]),
        "demag": bool(p["demag"]["value"]),
    }
    steps_src = p["cogging_steps_per_period" if cogging and "cogging_steps_per_period" in p else "steps_per_period"]["source"]
    sources = {"n_steps_per_period": steps_src, "n_sectors": p["n_sectors"]["source"], "component_mesh": p["coil_rel"]["source"],
               "mesh_size_mm": cls["source"], "min_size_mm": cls["source"]}
    for k in ("gap_layers", "n_periods", "outer_air_factor", "element_order", "eddy", "rotor_eddy", "demag"):
        sources[k] = p[k]["source"]
    return {"name": str(preset).strip().lower(), "label": p["label"], "version": version(), "policy": load().get("policy"),
            "values": values, "sources": sources,
            "h0": {"mm": round(h0, 4), "limited_by": h["limit"], "stator_diameter_class": [cls["from_mm"], cls["to_mm"]]}}


#: the route parameters a preset FILLS when the request left them empty, and how "empty" reads for each
FILLABLE = {"n_steps_per_period": None, "gap_layers": None, "mesh_size_mm": None, "min_size_mm": None,
            "outer_air_factor": None, "n_sectors": None, "component_mesh": ""}
#: the route parameters a preset only COMPARES (their route defaults are not "empty")
COMPARED = ("n_periods", "eddy", "rotor_eddy", "demag")


def fill(resolved: Mapping[str, Any], requested: Mapping[str, Any]) -> Dict[str, Any]:
    """The settings to solve with, and what differs from the preset.

    ``requested`` = the request's own values for the keys in :data:`FILLABLE` and :data:`COMPARED` (empty = the field's
    "empty" value).  Empty fillable fields take the preset's value; a field the request set to another value stays as the
    request has it and is listed in ``overridden`` (``{key: {"preset": x, "requested": y}}``): the result is then "custom".
    A requested ``component_mesh`` is merged key by key (the caller's keys win and are listed).
    """
    import json
    vals = resolved["values"]
    out: Dict[str, Any] = {}
    over: Dict[str, Dict[str, Any]] = {}
    for key, empty in FILLABLE.items():
        req = requested.get(key, empty)
        if key == "component_mesh":
            try:
                have = json.loads(req) if isinstance(req, str) and req.strip() else (dict(req) if isinstance(req, dict) else {})
            except ValueError:
                have = {}
            merged = dict(vals["component_mesh"])
            for ck, cv in have.items():
                if ck in merged and merged[ck] != cv:
                    over[f"component_mesh.{ck}"] = {"preset": merged[ck], "requested": cv}
                merged[ck] = cv
            out[key] = json.dumps(merged, sort_keys=True)
            continue
        if req is None or req == empty:
            out[key] = vals[key]
        else:
            out[key] = req
            if not _same(req, vals[key]):
                over[key] = {"preset": vals[key], "requested": req}
    for key in COMPARED:
        if key in requested and requested[key] is not None and not _same(requested[key], vals[key]):
            over[key] = {"preset": vals[key], "requested": requested[key]}
    return {"settings": out, "overridden": over, "custom": bool(over)}


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    fa, fb = _num(a), _num(b)
    return fa is not None and fb is not None and abs(fa - fb) <= 1e-9 * max(1.0, abs(fb))


def record(resolved: Mapping[str, Any], filled: Mapping[str, Any], effective: Mapping[str, Any]) -> Dict[str, Any]:
    """What a stored result carries (owner 2026-11-17): the preset name and version, the resolved values, what the request
    overrode (``custom``) and the EFFECTIVE settings the solver really used after its clamps, floors and snaps.  A requested
    value that a clamp changed is listed in ``changed`` (never hidden)."""
    changed = {k: {"requested": v["requested"], "effective": v["effective"]}
               for k, v in (effective.get("pairs") or {}).items() if not _same(v["requested"], v["effective"])}
    return {"name": resolved["name"], "label": resolved["label"], "version": resolved["version"],
            "custom": bool(filled.get("custom")), "overridden": dict(filled.get("overridden") or {}),
            "resolved": dict(resolved["values"]), "sources": dict(resolved["sources"]),
            "effective": {k: v for k, v in effective.items() if k != "pairs"}, "changed": changed}


def effective_from_result(settings: Mapping[str, Any], geometry: Mapping[str, Any], sbres: Mapping[str, Any]) -> Dict[str, Any]:
    """The effective settings of a finished solve, from what the solver reports plus its mesh ceiling.

    ``settings`` = the values the solve was started with.  The steps and gap rows come from the solver's own fields
    (``n_steps_per_period`` / ``steps_snapped``, ``gap_layers_effective``); the iron mesh size is lowered to the solver's
    feature ceiling when the requested one is above it (``fem_solver_2d.mesh_feature_floor_mm``)."""
    out: Dict[str, Any] = {}
    pairs: Dict[str, Dict[str, Any]] = {}
    steps_eff = sbres.get("n_steps_per_period")
    if steps_eff is not None:
        out["n_steps_per_period"] = steps_eff
        out["steps_snapped"] = bool(sbres.get("steps_snapped"))
        pairs["n_steps_per_period"] = {"requested": sbres.get("n_steps_per_period_requested", settings.get("n_steps_per_period")),
                                       "effective": steps_eff}
    gl = sbres.get("gap_layers_effective")
    if gl is not None:
        out["gap_layers"] = gl
        pairs["gap_layers"] = {"requested": sbres.get("gap_layers_requested", settings.get("gap_layers")), "effective": gl}
    try:
        g = _derived(geometry)
        feat = min(_positive(g, "tooth_width"), _positive(g, "slot_width"))
        ceiling = max(float(settings["min_size_mm"]), feat / 2.0)       # the solver's own ceiling (fem_solver_2d.mesh_feature_floor_mm)
        ms = float(settings["mesh_size_mm"])
        out["mesh_size_mm"] = round(min(ms, ceiling), 4)
        pairs["mesh_size_mm"] = {"requested": ms, "effective": out["mesh_size_mm"]}
    except (PresetError, KeyError, TypeError, ValueError):
        pass
    for k in ("min_size_mm", "outer_air_factor", "n_sectors", "n_periods", "element_order", "eddy", "rotor_eddy", "demag"):
        if k in settings:
            out[k] = settings[k]
    out["pairs"] = pairs
    return out
