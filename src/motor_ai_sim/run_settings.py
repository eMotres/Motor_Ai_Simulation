"""The steps per period and the gap rows of an optimisation run (Sweep, Descent, Auto), resolved in ONE place (owner 2026-11-24).

A panel keeps a small record ``{preset, overrides}`` per machine.  The server turns it into the settings the run receives
(``resolve``); the panel displays exactly that answer and the run request carries the same record, which the route resolves
with the same function.  A preset is a button (``optimization`` / ``simulation``); an override is a setting the user typed
afterwards.

WHAT THIS GUARANTEES: the STEPS PER PERIOD and the GAP ROWS of the run are what the panel shows, are chosen by the preset button
or typed in the panel, and never come from another tab.  Every coercion of those two numbers (bounds of the mode, the preset's
own value outside the bounds, ...) happens HERE, so the display equals the run for them.  The label ("optimization" /
"simulation" / "custom") and the digest (``sig``) are about that PAIR only.  The other settings (mesh sizes, sectors, rotor eddy,
demag, and the keys the solver reads from the machine's stored block) are returned for display, each with its source, and are
NOT part of the label or the digest, EXCEPT the preset version and the wire cell (``coil_rel``, read from the machine's stored
block, 1 when absent): they ride in the digest so that sweeps computed under different preset versions or wire cells are never
ranked together.  The complete run-settings identity is a follow-up."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, List, Mapping, Optional

from motor_ai_sim import mesh_presets as MP

PRESETS = ("optimization", "simulation")
#: the third input mode (owner 2026-11-24, one place for the settings): the request carries the VISIBLE values of the Mesh /
#: Simulation tabs as ``overrides``; nothing is taken from a preset, the label is found by MATCH against the two presets
TAB = "tab"
#: the values a ``tab`` request must carry (a missing one is refused: no silent default)
TAB_REQUIRED = ("steps_per_period", "gap_layers", "mesh_size_mm", "min_size_mm", "n_sectors", "coil_rel")
#: settings the run request carries (and a user may override), by mode
_OVERRIDABLE = {
    "sweep": ("steps_per_period", "gap_layers", "mesh_size_mm", "min_size_mm", "n_sectors", "rotor_eddy", "demag", "coil_rel"),
    "descent": ("steps_per_period", "gap_layers", "mesh_size_mm", "min_size_mm", "n_sectors", "rotor_eddy", "coil_rel"),
    "auto": ("steps_per_period", "gap_layers", "mesh_size_mm", "min_size_mm", "n_sectors", "rotor_eddy", "demag", "coil_rel"),
}
_MACHINE_KEYS = ("eddy", "outer_air_factor", "component_mesh")
_BOUNDS = {"steps_per_period": (4, 180), "gap_layers": (1.0, 2.0), "mesh_size_mm": (0.02, 12.0),
           "min_size_mm": (0.002, 3.0)}
#: the lowest step count each run kind accepts (the descent / auto plans keep 8 frames or more)
_MIN_STEPS = {"sweep": 4, "descent": 8, "auto": 8}


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


#: the scope of the digest a stamp carries: 2 = steps, gap rows, preset version and wire cell (a sweep stamped before has no
#: scope and is read as unknown, never ranked)
SIG_SCOPE = 2
#: the scope of the digest of a CUSTOM run (3): every resolved setting that reaches the solver (mesh sizes, sectors, element order,
#: the eddy / rotor eddy / demag switches, outer air, per-part sizes, the wire cell, steps, gap rows) plus the preset version.  A
#: preset run keeps the scope-2 digest, so stored preset sweeps stay comparable; a reader that does not know scope 3 treats it as
#: unknown (never ranked).
SIG_SCOPE_FULL = 3


def identity_sig(steps: Any, gap_layers: Any, preset_version: Any = None, coil_rel: Any = None) -> int:
    """A numeric digest of the run identity: steps, gap rows, the preset version and the wire cell (``coil_rel``) the run used.
    Two sweeps are ranked together only when it is known and equal.  Without the last two arguments it is the old pair digest."""
    body = [int(steps), round(float(gap_layers), 4)]
    if preset_version is not None:
        body += [int(preset_version), round(float(coil_rel if coil_rel is not None else 1.0), 4)]
    return int(hashlib.md5(json.dumps(body).encode()).hexdigest()[:12], 16)


def full_sig(settings: Mapping[str, Any], preset_version: Any) -> int:
    """The digest of a custom run: EVERY resolved setting the run uses (``settings`` is the resolver's dict) and the preset version.
    Two custom runs rank together only when all of them are equal."""
    body = {"v": int(preset_version) if preset_version is not None else None,
            "s": {k: settings[k] for k in sorted(settings)}}
    return int(hashlib.md5(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:12], 16)


def _bound(key: str, x: float, mode: str, source: str, clamps: List[Dict[str, Any]]) -> float:
    lo, hi = _BOUNDS[key]
    if key == "steps_per_period":
        lo = _MIN_STEPS[mode]
    used = min(max(x, lo), hi)
    if key == "steps_per_period":
        used = int(round(used))
    if used != x:
        clamps.append({"key": key, "requested": x, "used": used, "source": source,
                       "reason": "outside %g..%g for a %s run" % (lo, hi, mode)
                       + (" (gap rows never above 2)" if key == "gap_layers" and x > 2 else "")})
    return used


def finalist_note(steps: int, geometry: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """The REQUIREMENT a finalist is held to, when the run's steps are below it (sampling purpose ``cogging_quality``).

    A finalist (winner validation, Apply check, on-demand re-check) needs at least six raw samples per cogging cycle: the solver's
    own policy (``fem_solver_2d._cogging_frame_policy``) gives that minimum.  Whether a finalist reaches it depends on its slip ring
    (the solver raises the steps only to an existing divisor of the ring and otherwise keeps the run's count, recorded as
    insufficient), which this function does not know; so it states the requirement, never the count a solve will use.  It is a NOTE,
    not part of the label or the digest.  ``None`` when the run's steps already reach the requirement, or when the machine's counts
    are not known."""
    try:
        from motor_ai_sim.simulation.fem_solver_2d import _cogging_frame_policy
        slots = int(round(float(geometry.get("num_slots") or 0)))
        poles = int(round(float(geometry.get("num_poles") or 0)))
        if slots <= 0 or poles <= 0 or poles % 2:
            return None
        pol = _cogging_frame_policy(slots, poles, poles // 2, int(steps), int(steps), sampling_purpose="cogging_quality")
    except Exception:       # noqa: BLE001 - a display note must never break the resolve
        return None
    need = int(pol["min_required_steps_per_period"])
    if need <= int(steps):
        return None
    return {"min_steps": need, "run_steps": int(steps), "reason": "cogging_quality_requirement"}


def resolution_token(out: Mapping[str, Any], geometry: Mapping[str, Any]) -> str:
    """A COMPLETE token of one resolution (separate from the comparison digest ``sig``): the mode, the record, every setting the run
    uses with its source, the preset version and the machine's slot / pole counts.  A run or a baseline that carries the token the
    panel showed is refused (409) when the machine resolves to anything else now (e.g. sectors 2 -> 4 after a geometry change, which
    keeps the digest)."""
    body = {"mode": out["mode"], "preset": out["preset"], "overrides": out["overrides"], "settings": out["settings"],
            "sources": out["sources"], "version": out["version"],
            "slots": geometry.get("num_slots"), "poles": geometry.get("num_poles")}
    return hashlib.md5(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


#: the settings a ``tab`` request is matched on (the demag of a Descent run is the machine's, so it is not matched there)
_MATCH_KEYS = ("steps_per_period", "gap_layers", "mesh_size_mm", "min_size_mm", "n_sectors", "coil_rel", "rotor_eddy", "demag")


def _preset_numbers(pv: Mapping[str, Any]) -> Dict[str, Any]:
    v = pv["values"]
    return {"steps_per_period": int(v["n_steps_per_period"]), "gap_layers": float(v["gap_layers"]),
            "mesh_size_mm": float(v["mesh_size_mm"]), "min_size_mm": float(v["min_size_mm"]),
            "n_sectors": int(v["n_sectors"]), "coil_rel": float((v.get("component_mesh") or {}).get("coil_rel", 1.0)),
            "rotor_eddy": bool(v["rotor_eddy"]), "demag": bool(v["demag"])}


def _match_tab(settings: Mapping[str, Any], geometry: Mapping[str, Any], mode: str, opt_pv: Mapping[str, Any],
               opt_base: Mapping[str, Any]):
    """``(matched, reference label, differs)`` of a ``tab`` request: the preset whose numbers the visible values equal
    (Optimization first), else ``None`` with the keys that differ from the Optimization preset (the reference of a new sweep).  The Simulation preset reads "that many steps or more" (owner 2026-11-17: 96 or more), decided HERE so
    the panel never repeats the rule."""
    keys = [k for k in _MATCH_KEYS if not (k == "demag" and mode == "descent")]
    cands = [("optimization", opt_pv["label"], _preset_numbers(opt_pv))]
    try:
        sim_pv = MP.resolve("simulation", geometry)
        cands.append(("simulation", sim_pv["label"], _preset_numbers(sim_pv)))
    except MP.PresetError:
        pass                                # the Simulation preset cannot be resolved for this machine: it cannot match
    first = None
    for pname, plabel, nums in cands:
        diff: Dict[str, Any] = {}
        for k in keys:
            cur, w = settings[k], nums[k]
            same = (float(cur) >= float(w) - 1e-9) if (k == "steps_per_period" and pname == "simulation") else _same(cur, w)
            if not same:
                diff[k] = {"preset": w, "current": cur}
        if not diff:
            return pname, plabel, {}
        if first is None:
            first = (pname, plabel, diff)
    return None, first[1], first[2]


def resolve(preset: Optional[str], overrides: Optional[Mapping[str, Any]], cfg: Mapping[str, Any],
            mode: str = "sweep") -> Dict[str, Any]:
    """The complete effective settings of one run, the label and every difference from the preset, the clamps applied."""
    name = str(preset or "optimization").strip().lower()
    tab = name == TAB
    if name not in PRESETS and not tab:
        raise RunSettingsError("unknown preset %r; the presets are %s, %s" % (preset, ", ".join(PRESETS), TAB))
    if tab:
        missing = [k for k in TAB_REQUIRED if k not in dict(overrides or {})]
        if missing:
            raise RunSettingsError("a tab request must carry %s; missing %s" % (", ".join(TAB_REQUIRED), ", ".join(missing)))
    if mode not in _OVERRIDABLE:
        raise RunSettingsError("unknown run mode %r" % (mode,))
    geometry = dict(cfg.get("geometry") or {})
    try:
        pv = MP.resolve("optimization" if tab else name, geometry)
    except MP.PresetError as exc:
        raise RunSettingsError("the %s preset cannot be resolved for this machine: %s" % (name, exc))
    v = pv["values"]
    mach = _machine_values(cfg)
    base = {"steps_per_period": int(v["n_steps_per_period"]), "gap_layers": float(v["gap_layers"]),
            "mesh_size_mm": float(v["mesh_size_mm"]), "min_size_mm": float(v["min_size_mm"]),
            "n_sectors": int(v["n_sectors"]), "coil_rel": float((v.get("component_mesh") or {}).get("coil_rel", 1.0)),
            "element_order": int(v["element_order"]),
            "rotor_eddy": bool(v["rotor_eddy"]), "demag": bool(v["demag"]), "hi_fidelity": False}
    settings: Dict[str, Any] = dict(base)
    sources = {k: "preset" for k in base}
    clamps: List[Dict[str, Any]] = []
    nominal = {"steps_per_period": base["steps_per_period"], "gap_layers": base["gap_layers"]}
    # the preset's own value is bounded like a typed one (e.g. 324 steps on a 36-slot / 10-pole machine): the run gets the
    # bounded number and the label reads custom, because it is no longer the preset's number
    if not tab:             # a tab request takes every value from the request: the preset base is a scaffold, not a source
        settings["steps_per_period"] = _bound("steps_per_period", float(base["steps_per_period"]), mode, "preset", clamps)
        settings["gap_layers"] = _bound("gap_layers", float(base["gap_layers"]), mode, "preset", clamps)
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
        elif k == "coil_rel":
            x = _num(k, raw)
            if not any(abs(x - c) < 1e-9 for c in MP.COIL_REL_CHOICES):
                raise RunSettingsError("coil_rel must be one of %s, got %r" % (list(MP.COIL_REL_CHOICES), raw))
            settings[k] = x
        elif k == "n_sectors":
            n = _num(k, raw)
            if n != int(n) or n < 1 or n > 64:
                raise RunSettingsError("n_sectors must be a whole number from 1 to 64, got %r" % (raw,))
            settings[k] = int(n)
        else:
            x = _num(k, raw)
            if k in ("steps_per_period", "gap_layers") and x != int(x):
                # a count of rows / frames: the presets hold whole numbers, the Mesh tab and the stored configs use 1 and 2; a
                # fraction would give two digests (and two displays) for ONE eval-cache key
                raise RunSettingsError("%s is a count and must be a whole number, got %r" % (k, raw))
            if k in ("steps_per_period", "gap_layers"):
                used = _bound(k, x, mode, "override", clamps)
            else:
                lo, hi = _BOUNDS[k]
                used = min(max(x, lo), hi)
                if used != x:
                    clamps.append({"key": k, "requested": x, "used": used, "source": "override",
                                   "reason": "outside %g..%g" % (lo, hi)})
            settings[k] = used
        sources[k] = "tab" if tab else "override"
    if mode == "descent":                     # the descent evals read demag from the stored Simulation block
        settings["demag"], sources["demag"] = mach["demag_cfg"], "machine"
    settings["eddy"], sources["eddy"] = mach["eddy"], "machine"
    settings["outer_air_factor"], sources["outer_air_factor"] = mach["outer_air_factor"], "machine"
    settings["component_mesh"], sources["component_mesh"] = mach["component_mesh"], "machine"   # the stored block; coil_rel above is the run's
    # The solver solves a full-ring request on the machine's natural symmetry sector (P2); sectors do not change the steps or the
    # gap rows, but the effective number is shown, with the reason.
    if int(settings["n_sectors"]) <= 1:
        sym = max(1, math.gcd(int(round(float(geometry.get("num_slots", 1) or 1))),
                              int(round(float(geometry.get("num_poles", 1) or 1)))))
        if sym >= 2:
            clamps.append({"key": "n_sectors", "requested": int(settings["n_sectors"]), "used": sym, "source": sources["n_sectors"],
                           "reason": "a full ring is solved on the natural symmetry sector (it changes neither the steps nor the gap rows)"})
            settings["n_sectors"] = sym
    # THE LABEL IS ABOUT THE PAIR (steps, gap rows) ONLY: what differs from the chosen preset's own numbers
    differs: Dict[str, Any] = {}
    matched: Optional[str] = None
    ref_label = pv["label"]
    for k in (() if tab else ("steps_per_period", "gap_layers")):
        cur, w = settings[k], nominal[k]
        same = (cur >= float(w) - 1e-9) if (k == "steps_per_period" and name == "simulation") else _same(cur, w)
        if not same:
            differs[k] = {"preset": w, "current": cur}
    # THE WIRE CELL OF THE RUN is the preset's (or the user's override), whatever the machine's stored Mesh block says: when it differs
    # from the stored one the run carries it as a per-run override (``coil_rel_override``) and the stored block is never written.
    # Only an explicit override of the cell makes the run custom.
    try:
        coil_rel_stored = float((mach["component_mesh"] or {}).get("coil_rel", 1.0))
    except (TypeError, ValueError):
        coil_rel_stored = 1.0
    coil_rel_used = float(settings["coil_rel"])
    coil_rel_preset = float(base["coil_rel"])
    if tab:
        matched, ref_label, differs = _match_tab(settings, geometry, mode, pv, base)
    elif not _same(coil_rel_used, coil_rel_preset):
        differs["coil_rel"] = {"preset": coil_rel_preset, "current": coil_rel_used}
    coil_rel_override = None if _same(coil_rel_used, coil_rel_stored) else coil_rel_used
    custom = bool(differs) if not tab else matched is None
    label = ("custom" if custom else (matched if tab else name))
    out = {"preset": name, "overrides": given, "label": label, "custom": custom,
            "preset_label": ref_label, "matched": matched,
            "record_name": label if tab else name, "differs": differs, "settings": settings, "sources": sources,
            "clamps": clamps, "version": MP.version(), "mode": mode,
            "finalist_note": finalist_note(settings["steps_per_period"], geometry),
            "coil_rel": coil_rel_used, "coil_rel_stored": coil_rel_stored, "coil_rel_override": coil_rel_override,
            "sig_scope": SIG_SCOPE_FULL if custom else SIG_SCOPE,
            "sig": (full_sig(settings, MP.version()) if custom else
                    identity_sig(settings["steps_per_period"], settings["gap_layers"], MP.version(), coil_rel_used))}
    out["token"] = resolution_token(out, geometry)
    return out
