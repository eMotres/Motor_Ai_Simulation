"""The settings of an optimisation run (Sweep, Descent, Auto), resolved in ONE place (owner 2026-11-24).

A panel keeps a small record ``{preset, overrides}`` per machine.  The server turns it into the COMPLETE effective settings the
solver receives (``resolve``); the panel displays exactly that answer and the run request carries the same record, which the
route resolves with the same function.  A preset is a button (``optimization`` / ``simulation``); an override is any single
setting the user changed afterwards (the label then reads ``custom`` and lists what differs).

Keys the run request cannot carry (the coupled-eddy switch, the outer-air factor and the per-part mesh sizes are read by the
solver from the machine's stored Mesh / Simulation block) are reported with their ACTUAL values and source ``machine``; they
count in the label, so a machine whose stored block differs from a preset never reads as that preset."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, List, Mapping, Optional

from motor_ai_sim import mesh_presets as MP

PRESETS = ("optimization", "simulation")
#: settings the run request carries (and a user may override), by mode
_OVERRIDABLE = {
    "sweep": ("steps_per_period", "gap_layers", "mesh_size_mm", "min_size_mm", "n_sectors", "rotor_eddy", "demag",
              "hi_fidelity"),
    "descent": ("steps_per_period", "gap_layers", "mesh_size_mm", "min_size_mm", "n_sectors", "rotor_eddy"),
    "auto": ("steps_per_period", "gap_layers", "mesh_size_mm", "min_size_mm", "n_sectors", "rotor_eddy", "demag"),
}
_MACHINE_KEYS = ("eddy", "outer_air_factor", "component_mesh")
_BOUNDS = {"steps_per_period": (4, 180), "gap_layers": (1.0, 2.0), "mesh_size_mm": (0.02, 12.0),
           "min_size_mm": (0.002, 3.0)}


class RunSettingsError(ValueError):
    """The record cannot be resolved (unknown preset, a key that cannot be overridden here, a non-numeric value)."""


def _num(key: str, v: Any) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
        raise RunSettingsError("%s must be a finite number, got %r" % (key, v))
    return float(v)


def _machine_values(cfg: Mapping[str, Any]) -> Dict[str, Any]:
    mesh = dict(cfg.get("mesh") or {})
    sim = dict(cfg.get("simulation") or {})
    return {"eddy": bool(sim.get("eddy", False)), "demag_cfg": bool(sim.get("demag", False)),
            "outer_air_factor": float(mesh.get("outer_air_factor", 1.2)),
            "component_mesh": dict(mesh.get("component_mesh") or {})}


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    return MP._same(a, b)


def identity_sig(settings: Mapping[str, Any]) -> int:
    """A numeric digest of the COMPLETE settings (two results compare only when it is known and equal)."""
    keys = ("steps_per_period", "gap_layers", "mesh_size_mm", "min_size_mm", "n_sectors", "element_order", "eddy",
            "rotor_eddy", "demag", "hi_fidelity", "outer_air_factor", "component_mesh")
    body = {k: settings.get(k) for k in keys}
    return int(hashlib.md5(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:12], 16)


def resolve(preset: Optional[str], overrides: Optional[Mapping[str, Any]], cfg: Mapping[str, Any],
            mode: str = "sweep") -> Dict[str, Any]:
    """The complete effective settings of one run, the label and every difference from the preset, the clamps applied."""
    name = str(preset or "optimization").strip().lower()
    if name not in PRESETS:
        raise RunSettingsError("unknown preset %r; the presets are %s" % (preset, ", ".join(PRESETS)))
    if mode not in _OVERRIDABLE:
        raise RunSettingsError("unknown run mode %r" % (mode,))
    geometry = dict(cfg.get("geometry") or {})
    try:
        pv = MP.resolve(name, geometry)
    except MP.PresetError as exc:
        raise RunSettingsError("the %s preset cannot be resolved for this machine: %s" % (name, exc))
    v = pv["values"]
    mach = _machine_values(cfg)
    base = {"steps_per_period": int(v["n_steps_per_period"]), "gap_layers": float(v["gap_layers"]),
            "mesh_size_mm": float(v["mesh_size_mm"]), "min_size_mm": float(v["min_size_mm"]),
            "n_sectors": int(v["n_sectors"]), "element_order": int(v["element_order"]),
            "rotor_eddy": bool(v["rotor_eddy"]), "demag": bool(v["demag"]), "hi_fidelity": False}
    settings: Dict[str, Any] = dict(base)
    sources = {k: "preset" for k in base}
    clamps: List[Dict[str, Any]] = []
    given: Dict[str, Any] = {}
    allowed = _OVERRIDABLE[mode]
    for k, raw in dict(overrides or {}).items():
        if k not in allowed:
            why = ("is read from the machine's stored settings" if k in _MACHINE_KEYS or k == "demag"
                   else "is not a setting of this run")
            raise RunSettingsError("%s cannot be set for a %s run: it %s" % (k, mode, why))
        given[k] = raw
        if k in ("rotor_eddy", "demag", "hi_fidelity"):
            if not isinstance(raw, bool):
                raise RunSettingsError("%s must be true or false, got %r" % (k, raw))
            settings[k] = raw
        elif k == "n_sectors":
            n = _num(k, raw)
            if n != int(n) or n < 1 or n > 64:
                raise RunSettingsError("n_sectors must be a whole number from 1 to 64, got %r" % (raw,))
            settings[k] = int(n)
        else:
            x = _num(k, raw)
            lo, hi = _BOUNDS[k]
            used = min(max(x, lo), hi)
            if k == "steps_per_period":
                used = int(round(used))
            if used != x:
                clamps.append({"key": k, "requested": x, "used": used,
                               "reason": "outside %g..%g" % (lo, hi)
                               + (" (gap rows never above 2)" if k == "gap_layers" and x > 2 else "")})
            settings[k] = used
        sources[k] = "override"
    if mode == "descent":                     # the descent evals read demag from the stored Simulation block
        settings["demag"], sources["demag"] = mach["demag_cfg"], "machine"
    settings["eddy"], sources["eddy"] = mach["eddy"], "machine"
    settings["outer_air_factor"], sources["outer_air_factor"] = mach["outer_air_factor"], "machine"
    settings["component_mesh"], sources["component_mesh"] = mach["component_mesh"], "machine"
    if settings["hi_fidelity"]:
        clamps.append({"key": "gap_layers", "requested": settings["gap_layers"],
                       "used": max(4.0, settings["gap_layers"]),
                       "reason": "hi_fidelity raises the gap rows to 4 or more in the solver"})
    # what differs from the chosen preset: every key the solver receives
    differs: Dict[str, Any] = {}
    for k, w in base.items():
        cur = settings[k]
        same = (cur >= float(w) - 1e-9) if (k == "steps_per_period" and name == "simulation") else _same(cur, w)
        if not same:
            differs[k] = {"preset": w, "current": cur}
    pm = pv["values"]
    if not _same(settings["outer_air_factor"], pm["outer_air_factor"]) and "outer_air_factor" in (cfg.get("mesh") or {}):
        differs["outer_air_factor"] = {"preset": pm["outer_air_factor"], "current": settings["outer_air_factor"]}
    for ck, cv in settings["component_mesh"].items():           # a part size the machine does not store is the default
        if ck in pm["component_mesh"] and not _same(cv, pm["component_mesh"][ck]):
            differs["component_mesh.%s" % ck] = {"preset": pm["component_mesh"][ck], "current": cv}
    if bool(pm.get("eddy", True)) != settings["eddy"]:
        differs["eddy"] = {"preset": bool(pm.get("eddy", True)), "current": settings["eddy"]}
    custom = bool(differs)
    return {"preset": name, "overrides": given, "label": "custom" if custom else name, "custom": custom,
            "preset_label": pv["label"], "differs": differs, "settings": settings, "sources": sources,
            "clamps": clamps, "version": MP.version(), "mode": mode, "sig": identity_sig(settings)}
