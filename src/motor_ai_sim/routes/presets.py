"""Motor 'garage' — a library of ready-made motor designs.

Each preset bundles a full geometry parameter set plus the operating point
(current / rpm / load-angle gamma).  Applying a preset writes those values into
config/motor_config.yaml and flushes the geometry + simulation caches, so the
whole app (Geometry, Mesh, Simulation) switches to that motor.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/presets", tags=["presets"])

_ROOT = Path(__file__).parent.parent.parent.parent
_PRESETS_PATH = _ROOT / "config" / "motor_presets.json"
_CONFIG_PATH = _ROOT / "config" / "motor_config.yaml"
_CATALOG_PATH = _ROOT / "config" / "motor_catalog.json"


def _thumb_path_d(geom, c: float, s: float) -> str:
    """Shapely Polygon/MultiPolygon -> SVG path 'd' (mm->view units, y flipped)."""
    if geom is None or getattr(geom, "is_empty", True):
        return ""
    geoms = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    parts = []
    for g in geoms:
        if g.is_empty:
            continue
        for ring in [g.exterior, *g.interiors]:
            pts = list(ring.coords)
            if len(pts) < 3:
                continue
            d = " L ".join(f"{c + x * s:.2f} {c - y * s:.2f}" for x, y in pts)
            parts.append("M " + d + " Z")
    return " ".join(parts)


def _gen_thumb_svg(geo: dict):
    """Real-geometry cross-section SVG (stator + slots, rotor, coils, magnets by
    polarity, shaft) from the ACTUAL CadQuery geometry — the same themed snapshot
    the prebuilt catalog cards use (design_runs/gen_thumbnails.py). Returns the SVG
    string, or None if the geometry can't be built (card falls back to a schematic)."""
    try:
        from motor_ai_sim.cadquery_geometry import CadQueryMotor
        C, VIEW, MARGIN = 60.0, 120.0, 4.0
        COL = dict(stator="#26344a", rotor="#314158", shaft="#4a5a73",
                   coil="#c6822f", magN="#e0556a", magS="#5b8def", edge="#46597a")
        m = CadQueryMotor()
        m.set_parameters(geo)
        P = m.get_2d_polygons()
        R = float(m.parameters["stator_outer_radius"])
        s = (VIEW / 2 - MARGIN) / R
        out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {VIEW:.0f} {VIEW:.0f}">']
        out.append(f'<path d="{_thumb_path_d(P["stator"], C, s)}" fill="{COL["stator"]}" '
                   f'fill-rule="evenodd" stroke="{COL["edge"]}" stroke-width="0.7"/>')
        out.append(f'<path d="{_thumb_path_d(P["rotor"], C, s)}" fill="{COL["rotor"]}" fill-rule="evenodd"/>')
        for c in P.get("coils", []):
            out.append(f'<path d="{_thumb_path_d(c, C, s)}" fill="{COL["coil"]}"/>')
        for poly, pol in P.get("magnets", []):
            out.append(f'<path d="{_thumb_path_d(poly, C, s)}" fill="{COL["magN"] if pol > 0 else COL["magS"]}"/>')
        out.append(f'<path d="{_thumb_path_d(P["shaft"], C, s)}" fill="{COL["shaft"]}"/>')
        out.append("</svg>")
        return "".join(out)
    except Exception:
        return None


def _mat_name(mid) -> str:
    """Short display name for a material id (Arnold_N52UH_150C -> N52UH 150C)."""
    s = str(mid or "").strip()
    for pref in ("Arnold_", "JFE_", "Hitachi_", "Vacuumschmelze_", "VAC_"):
        if s.startswith(pref):
            s = s[len(pref):]
            break
    return s.replace("_", " ")


def _last_transient_summary():
    """The most recent FEM transient result (config/.last_transient.json) so a
    just-saved motor's card carries real torque / power / efficiency / voltage
    (the user simulates a motor, then saves it)."""
    try:
        p = _ROOT / "config" / ".last_transient.json"
        if not p.exists():
            return None
        blob = json.loads(p.read_text(encoding="utf-8"))
        r = blob.get("result")
        return r if isinstance(r, dict) else None
    except Exception:
        return None


def _enrich_card_entry(entry: dict, geo: dict, sim: dict, met: dict) -> None:
    """Fill the card with the SAME headline + detail fields the prebuilt motors
    show (power, efficiency, voltage, magnet, steel, length, wire), so user-saved
    cards look identical. Geometry/material fields are deterministic; torque /
    power / eff / voltage come from the saved metrics or the last FEM run."""
    import math

    def _num(v):
        try:
            return float(v)
        except Exception:
            return None

    ml = _num(geo.get("motor_length"))
    if ml:
        entry["length_mm"] = round(ml, 1)
    ww, wh, nw = _num(geo.get("wire_width")), _num(geo.get("wire_height")), _num(geo.get("num_wires_per_slot"))
    if ww and wh and nw:
        entry["wire"] = f"{ww:g}x{wh:g} mm, {int(round(nw))}t"   # ASCII only (no mojibake)

    try:
        from motor_ai_sim.config import get_config
        mats = get_config().get("materials") or {}
        if mats.get("magnet"):
            entry["magnet"] = _mat_name(mats["magnet"])
        if mats.get("stator_core"):
            entry["steel"] = _mat_name(mats["stator_core"])
    except Exception:
        pass

    # Prefer the metrics saved with the motor; else fall back to the last FEM run.
    mm = dict(met or {})
    if not any(mm.get(k) is not None for k in ("efficiency", "T_avg_Nm", "T_em_Nm")):
        mm = _last_transient_summary() or mm

    def _pick(*keys):
        for k in keys:
            v = _num(mm.get(k))
            if v is not None:
                return v
        return None

    tavg = _pick("T_avg_Nm", "T_em_Nm", "torque")
    # Card voltage = LINE-TO-LINE peak of the actual waveforms (what the DC bus
    # must cover).  The PHASE peak is inflated by triplen harmonics that cancel
    # line-to-line, and √3×phase overstates the true LL peak by up to ~25 % on
    # concentrated windings — so no fallback conversion: without an honest LL
    # value the field is dropped (it reappears on the next save/run).
    vpk = _pick("V_line_peak_V", "v_line_peak_v")
    rip = _pick("T_ripple_pct", "ripple_pct", "ripple")
    rpm = _num(sim.get("rpm")) or 0.0
    pmech = _pick("P_mech_avg_W", "P_mech_W", "power_w")
    pelec = _pick("P_elec_in_W")
    eff = _pick("efficiency", "efficiency_pct")
    if eff is None and pmech and pelec and pelec > 0:
        eff = pmech / pelec     # the FEM result stores the power split, not η directly

    if tavg is not None:
        entry["T_avg_Nm"] = round(tavg, 3)
    if pmech:
        entry["power_w"] = round(pmech, 1)
    elif tavg is not None and rpm > 0:
        entry["power_w"] = round(tavg * 2 * math.pi * rpm / 60.0, 1)
    if eff is not None:
        entry["efficiency_pct"] = round(eff * 100.0 if eff <= 1.0 else eff, 1)
    if vpk is not None:
        entry["voltage_pk_v"] = round(vpk, 1)
    else:
        entry.pop("voltage_pk_v", None)   # never keep a stale PHASE-peak value

    # Active mass: prefer the saved run's summary value; else compute it
    # DETERMINISTICALLY from the preset geometry (same compute_masses as the
    # Simulation summary card — no FEM needed).
    mass = _pick("mass_total_kg", "mass_kg")
    if mass is None:
        try:
            from motor_ai_sim.simulation.geometry_2d import params_from_config as _pfc
            from motor_ai_sim.masses import compute_masses as _cm
            _p = _pfc(geo_override=geo)
            from motor_ai_sim.config import get_config as _gc
            from motor_ai_sim.simulation.geometry_2d import merge_geo_override as _mgo
            # merge_geo_override, not a dict-update: the preset supplies
            # primaries, the config supplies the DERIVED fields, and a plain
            # merge would mass a preset against the active design's counts.
            _gcfg = _mgo(dict(_gc().get("geometry", {})), geo)
            mass = float(_cm(_p, _gcfg, k_end=0.0)["total"])
        except Exception:
            mass = None
    if mass is not None:
        entry["mass_kg"] = round(mass, 2)
    if rip is not None:
        entry["ripple_pct"] = round(rip, 1)


def _upsert_catalog_entry(preset_id: str, preset: dict, gen_thumb: bool = True) -> None:
    """Add/update a Motors-catalog card for this preset so saved motors show
    up in the Motors tab and can be re-loaded."""
    try:
        cat = (json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
               if _CATALOG_PATH.exists() else {})
    except Exception:
        return
    geo = preset.get("geometry", {}) or {}
    sim = preset.get("simulation", {}) or {}
    met = preset.get("metrics", {}) or {}

    def _i(v):
        try: return int(round(float(v)))
        except Exception: return 0
    dia = _i(geo.get("stator_diameter"))
    ns = _i(geo.get("num_seg"))
    slots = ns * _i(geo.get("num_slots_per_segment"))
    poles = ns * _i(geo.get("num_poles_per_segment"))
    cid = f"cat_{preset_id}"
    # Real-geometry thumbnail from the ACTUAL geometry, stored inline on the card.
    # Generate on a geometry save; on a mesh/sim-only save reuse the card's
    # existing SVG so it isn't dropped (and we don't rebuild CadQuery needlessly).
    thumb = _gen_thumb_svg(geo) if gen_thumb else None
    if not thumb:
        for _m in cat.get("motors", []):
            if _m.get("id") == cid and _m.get("thumb_svg"):
                thumb = _m["thumb_svg"]
                break
    entry = {
        "id": cid, "diameter_mm": dia or 0, "name": preset.get("name", preset_id),
        "topology": "Spoke-PM SPMSM", "slots": slots, "poles": poles,
        "rpm": sim.get("rpm", 0), "current_a": sim.get("max_current", 0),
        "T_avg_Nm": met.get("T_avg_Nm", 0), "ripple_pct": met.get("ripple_pct", 0),
        "gamma_deg": sim.get("phase_offset_deg", 0), "tier": "free",
        "description": preset.get("description") or f"Your saved motor — {preset.get('name', preset_id)}.",
        "preset": preset_id, "owner": "user",
    }
    if thumb:
        entry["thumb_svg"] = thumb
    _enrich_card_entry(entry, geo, sim, met)   # power/eff/voltage/magnet/steel/length/wire
    cat.setdefault("tiers", []); cat.setdefault("diameters_mm", []); cat.setdefault("motors", [])
    if dia and dia not in cat["diameters_mm"]:
        cat["diameters_mm"] = sorted(set([*cat["diameters_mm"], dia]))
    cat["motors"] = [m for m in cat["motors"] if m.get("id") != cid] + [entry]
    try:
        _CATALOG_PATH.write_text(json.dumps(cat, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _load_presets() -> dict:
    if not _PRESETS_PATH.exists():
        return {}
    try:
        return json.loads(_PRESETS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_presets(d: dict) -> None:
    _PRESETS_PATH.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")


def _summary(p: dict) -> dict:
    return {
        "id": p.get("id"),
        "name": p.get("name", p.get("id")),
        "description": p.get("description", ""),
        "metrics": p.get("metrics", {}),
        "order": p.get("order", 999),
    }


class SavePresetRequest(BaseModel):
    id: Optional[str] = None
    name: str
    description: str = ""
    metrics: Optional[dict] = None
    # Full mesh + simulation settings as the web UI holds them (incl. the
    # localStorage-only ones: per-part sizes, steps, coil temp, demag…).  A
    # "motor" preset bundles geometry + mesh + simulation so loading it
    # restores the ENTIRE working setup, not just the geometry.
    mesh: Optional[dict] = None
    simulation: Optional[dict] = None


class SettingsPatch(BaseModel):
    """Update an existing motor's saved geometry / mesh / simulation settings.
    This is the auto-save: the loaded motor is "my copy" and every edit lands
    in it (geometry untouched unless explicitly sent)."""
    mesh: Optional[dict] = None
    simulation: Optional[dict] = None
    geometry: Optional[dict] = None


@router.get("")
def list_presets():
    """List all saved motor presets (sorted by order)."""
    presets = _load_presets()
    items = [_summary(p) for p in presets.values()]
    items.sort(key=lambda x: (x["order"], x["name"]))
    return {"presets": items}


@router.get("/{preset_id}")
def get_preset(preset_id: str):
    p = _load_presets().get(preset_id)
    if not p:
        raise HTTPException(status_code=404, detail=f"preset '{preset_id}' not found")
    return p


@router.post("/{preset_id}/apply")
def apply_preset(preset_id: str):
    """Apply a preset: write its geometry + operating point into config.yaml and
    flush all caches so the app switches to that motor."""
    p = _load_presets().get(preset_id)
    if not p:
        raise HTTPException(status_code=404, detail=f"preset '{preset_id}' not found")

    geometry = p.get("geometry", {}) or {}
    simulation = p.get("simulation", {}) or {}
    mesh = p.get("mesh", {}) or {}

    try:
        config = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
        geo_sec = config.setdefault("geometry", {})
        for k, v in geometry.items():
            geo_sec[k] = v
        sim_sec = config.setdefault("simulation", {})
        for k, v in simulation.items():
            sim_sec[k] = v
        # Mesh: write the config-backed slider values so the Mesh tab (which
        # lets config win on mount) switches to this motor's mesh too.  The
        # per-part component_mesh lives only in the browser → seeded there.
        mesh_sec = config.setdefault("mesh", {})
        for _mk in ("mesh_size_mm", "min_size_mm", "outer_air_factor",
                    "gap_layers", "normal_deviation", "n_sectors"):
            if mesh.get(_mk) is not None:
                mesh_sec[_mk] = mesh[_mk]
        # Synchronous machine: keep frequency LOCKED to rpm (f = rpm·pp/60).
        # Presets store rpm but not frequency; leaving a stale frequency from
        # the previous motor scaled the solver's loss derivatives wrong.
        try:
            _pp = int(round(float(geo_sec.get("num_seg", 0)))) \
                * int(round(float(geo_sec.get("num_poles_per_segment", 0)))) // 2
            if _pp > 0 and sim_sec.get("rpm"):
                sim_sec["frequency"] = round(float(sim_sec["rpm"]) * _pp / 60.0, 2)
        except Exception:
            pass
        # Atomic write (temp + replace) so a concurrent reader never catches the
        # config file mid-truncate and parses it empty (which would nuke it).
        import os as _os
        _tmp = _CONFIG_PATH.with_suffix(".yaml.tmp")
        _tmp.write_text(
            yaml.dump(config, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        _os.replace(_tmp, _CONFIG_PATH)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"failed to write config: {e}")

    # Flush caches + reset in-memory geometry so every tab re-solves on the new motor.
    try:
        from motor_ai_sim.config import clear_config_cache
        clear_config_cache()
    except Exception:
        pass
    try:
        import motor_ai_sim.services.geometry_service as gs
        gs._current_geometry = None
        gs.invalidate_mesh_cache()
    except Exception:
        pass
    try:
        from motor_ai_sim.routes.geometry import _mesh_cache, _mesh_extruded_cache
        _mesh_cache["hash"] = None
        _mesh_extruded_cache["hash"] = None
    except Exception:
        pass
    try:
        from motor_ai_sim.routes.simulation import clear_simulation_caches
        clear_simulation_caches()
    except Exception:
        pass
    try:
        from motor_ai_sim.cadquery_geometry import CadQueryCache
        CadQueryCache().clear_all()
    except Exception:
        pass

    # Return the FULL preset (geometry + mesh + simulation) so the loader can
    # seed the browser-only settings (per-part sizes, steps, demag…) that
    # config.yaml does not hold.
    return {"status": "ok", "applied": preset_id,
            "geometry": geometry, "simulation": simulation, "mesh": mesh,
            "preset": p}


@router.post("")
def save_current_as_preset(req: SavePresetRequest):
    """Snapshot the CURRENT config geometry + the UI's mesh/sim as a new motor."""
    from motor_ai_sim.config import get_config
    cfg = get_config()
    geo = dict(cfg.get("geometry", {}))
    sim = cfg.get("simulation", {})
    # keep only plain numeric geometry keys (drop nested schema/groups)
    geometry = {k: v for k, v in geo.items() if isinstance(v, (int, float))}
    # Prefer the FULL simulation/mesh the UI sent (it has the browser-only
    # fields); fall back to the config operating point.
    simulation = dict(req.simulation) if req.simulation else {
        k: sim.get(k) for k in ("max_current", "rpm", "phase_offset_deg")
        if sim.get(k) is not None}
    mesh = dict(req.mesh) if req.mesh else dict(cfg.get("mesh", {}))

    presets = _load_presets()
    pid = req.id or req.name.lower().replace(" ", "_").replace("/", "_")
    order = 1 + max([p.get("order", 0) for p in presets.values()] or [0])
    presets[pid] = {
        "id": pid, "name": req.name, "description": req.description,
        "order": order, "metrics": req.metrics or {}, "owner": "user",
        "geometry": geometry, "simulation": simulation, "mesh": mesh,
    }
    _save_presets(presets)
    _upsert_catalog_entry(pid, presets[pid])   # show it in the Motors tab
    return {**_summary(presets[pid]), "id": pid}


@router.post("/{preset_id}/settings")
def save_motor_settings(preset_id: str, patch: SettingsPatch):
    """Update an EXISTING motor's saved mesh and/or simulation settings.

    This is the "Save mesh/simulation to motor" button: it stamps the current
    UI settings onto the motor we loaded, WITHOUT touching its geometry.
    """
    presets = _load_presets()
    p = presets.get(preset_id)
    if not p:
        raise HTTPException(status_code=404, detail=f"motor '{preset_id}' not found")
    if patch.geometry is not None:
        p["geometry"] = {**(p.get("geometry") or {}), **patch.geometry}
    if patch.mesh is not None:
        p["mesh"] = {**(p.get("mesh") or {}), **patch.mesh}
    if patch.simulation is not None:
        p["simulation"] = {**(p.get("simulation") or {}), **patch.simulation}
    presets[preset_id] = p
    _save_presets(presets)
    # keep the Motors-tab card in sync for user motors; only rebuild the
    # geometry thumbnail when the geometry actually changed (else reuse it).
    if p.get("owner") == "user":
        _upsert_catalog_entry(preset_id, p, gen_thumb=(patch.geometry is not None))
    return {"status": "ok", "id": preset_id,
            "saved": [k for k in ("geometry", "mesh", "simulation")
                      if getattr(patch, k) is not None]}


@router.post("/{preset_id}/open")
def open_motor(preset_id: str):
    """Open a motor as the user's EDITABLE copy.

    A template (owner != "user", e.g. a curated catalog motor) is forked into
    "my_<id>" first so the shared template stays pristine and the user's edits
    auto-save into their own copy.  A user motor opens in place.  Then the copy
    is applied to config and returned (full) so the loader can seed the browser
    settings + mark it the active working motor.
    """
    import copy as _copy
    presets = _load_presets()
    src = presets.get(preset_id)
    if not src:
        raise HTTPException(status_code=404, detail=f"motor '{preset_id}' not found")
    if src.get("owner") == "user":
        target = preset_id
    else:
        target = f"my_{preset_id}"
        if target not in presets:
            c = _copy.deepcopy(src)
            c["id"] = target
            c["owner"] = "user"
            c["order"] = 1 + max([p.get("order", 0) for p in presets.values()] or [0])
            presets[target] = c
            _save_presets(presets)
    return apply_preset(target)


class RenamePresetRequest(BaseModel):
    name: str
    description: Optional[str] = None


@router.patch("/{preset_id}")
def rename_preset(preset_id: str, req: RenamePresetRequest):
    """Rename a saved motor (display name only — the id stays stable, so the
    catalog card, apply/open flows and any geometry links keep working)."""
    presets = _load_presets()
    if preset_id not in presets:
        raise HTTPException(status_code=404, detail=f"preset '{preset_id}' not found")
    new_name = (req.name or "").strip()
    if not new_name:
        raise HTTPException(status_code=422, detail="name must not be empty")
    presets[preset_id]["name"] = new_name
    if req.description is not None:
        presets[preset_id]["description"] = req.description
    _save_presets(presets)
    # Refresh the Motors-tab card in place; keep its thumbnail (no CadQuery run).
    _upsert_catalog_entry(preset_id, presets[preset_id], gen_thumb=False)
    return {"status": "ok", "id": preset_id, "name": new_name}


@router.delete("/{preset_id}")
def delete_preset(preset_id: str):
    presets = _load_presets()
    if preset_id not in presets:
        raise HTTPException(status_code=404, detail=f"preset '{preset_id}' not found")
    del presets[preset_id]
    _save_presets(presets)
    return {"status": "ok", "deleted": preset_id}
