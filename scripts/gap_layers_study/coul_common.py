"""Shared set-up for the Coulomb validation runs (sandbox, solver-direct).

compose() is adapted from the paused ripple study's ripple_ref.py
(branch study/torque-ripple-validation, read only).
"""
from __future__ import annotations

import math
import os
import time

import yaml

MACHINES = {"d40": ("d40", "L12"), "l13": ("l13", "L13"), "l155": ("l155", "L155 motor")}
DUTY_NAMES = {("d40", "rated"): "rated", ("l13", "rated"): "rated",
              ("l155", "rated"): "rated 1x9 mm"}
ABSENT_MEANS = {"sleeve_thickness": 0.0, "wire_parallel": 1, "wire_split": 1}
ABSENT_MATERIALS = {"slot_insulation": "Nomex", "wire_insulation": "polyimide"}


def compose(inp, machine, duty_key):
    sub, cfg = MACHINES[machine]
    d = yaml.safe_load(open(os.path.join(inp, sub, "die.yaml"), encoding="utf-8"))
    y = yaml.safe_load(open(os.path.join(inp, sub, cfg + ".yaml"), encoding="utf-8"))
    geo = dict(d.get("geometry") or {})
    geo.update({k: v for k, v in (y.get("geometry_overrides") or {}).items() if v is not None})
    for k, v in ABSENT_MEANS.items():
        if geo.get(k) is None:
            geo[k] = v
    ns = float(geo["num_seg"])
    pps, sps = float(geo["num_poles_per_segment"]), float(geo["num_slots_per_segment"])
    geo["num_poles"] = int(round(ns * pps)); geo["angle_pole"] = 360.0 / (ns * pps)
    geo["num_slots"] = int(round(ns * sps)); geo["angle_slot"] = 360.0 / (ns * sps)
    duty = next(x for x in y["duties"] if x["name"] == DUTY_NAMES[(machine, duty_key)])
    mats = {k: v for k, v in (y.get("materials") or {}).items() if v}
    for k, v in (duty.get("materials") or {}).items():
        if v:
            mats[k] = v
    for k, v in ABSENT_MATERIALS.items():
        if not mats.get(k):
            mats[k] = v
    st = dict(duty.get("mesh") or {})
    return d, y, geo, duty, mats, st


def setup(inp, machine, duty_key):
    """Write the machine into the run's private config; return the solver kwargs."""
    d, y, geo, duty, mats, st = compose(inp, machine, duty_key)
    cfg_path = os.environ["MOTOR_AI_SIM_CONFIG"]
    base = yaml.safe_load(open(cfg_path, encoding="utf-8"))
    base["geometry"].update(geo)
    base.setdefault("materials", {}).update(mats)
    base["winding"] = dict(y.get("winding") or {})
    base["parts"] = {"shaft": "included"}
    wnd = y.get("winding") or {}
    sd = str(wnd.get("star_delta") or duty.get("star_delta") or st.get("sim.starDelta") or "star")
    base.setdefault("simulation", {}).update(
        {"rpm": float(duty["rpm"]), "gamma_deg": float(duty["gamma_deg"]), "star_delta": sd})
    with open(cfg_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(base, f, allow_unicode=True, sort_keys=False)
    I_term = float(duty["current_arms"])
    I_wind = I_term / (math.sqrt(3.0) if sd == "delta" else 1.0)
    mt = st.get("sim.magnetTempC")
    kw = dict(
        gamma_deg=float(duty["gamma_deg"]), I_phase_rms=I_wind, rpm=float(duty["rpm"]),
        n_parallel=int(wnd.get("n_parallel") or 1), connection=wnd.get("connection"),
        star_delta=sd,
        daxis_deg=(float(d["daxis_deg"]) if d.get("daxis_deg") is not None else None),
        mesh_size_mm=float(st.get("mesh.meshSize", 4)),
        min_size_mm=float(st.get("mesh.minSize", 0.3)),
        outer_air_factor=float(st.get("mesh.outerAir", 1.2)),
        gap_layers=float(st.get("mesh.gapLayers", 1)),
        n_sectors=int(st.get("mesh.nSectors", 2)),
        stator_fillet_mm=0.0,
        coil_temp_c=float(st.get("sim.coilTemp", 120)),
        magnet_temp_c=(float(mt) if mt not in (None, "") else None),
        end_winding_factor=float(st.get("sim.endWinding", 0) or 0),
        pole_copy=bool(st.get("mesh.poleCopy", False)),
        iron_template=bool(st.get("mesh.ironTemplate", True)),
        geo_mesh=bool(st.get("mesh.geoMesh", True)),
        structured_gap=bool(st.get("mesh.structuredGap", True)),
        component_mesh_mm={k: float(v) for k, v in dict(st.get("mesh.componentMesh") or {}).items()},
        geo_override=dict(geo), drive="current", element_order=2)
    info = dict(duty_name=DUTY_NAMES[(machine, duty_key)], I_terminal_rms=I_term,
                I_winding_rms=I_wind, star_delta=sd, duty_demag=bool(st.get("sim.demag", False)),
                materials=mats)
    return kw, info


def scale_mesh(kw, s):
    if s == 1.0:
        return kw
    kw = dict(kw)
    kw["mesh_size_mm"] *= s
    kw["min_size_mm"] *= s
    kw["component_mesh_mm"] = {k: (v if "rel" in k else v * s)
                               for k, v in kw["component_mesh_mm"].items()}
    return kw


def install_coulomb_timer():
    """Time every prepared Coulomb evaluator call (per layer)."""
    from motor_ai_sim.simulation import virtual_work_torque as vwt
    stats = {"calls": 0, "seconds": 0.0}
    orig = vwt.prepare_coulomb_torque

    def prep(*a, **k):
        f = orig(*a, **k)

        def timed(A):
            t0 = time.perf_counter()
            v = f(A)
            stats["seconds"] += time.perf_counter() - t0
            stats["calls"] += 1
            return v
        return timed
    vwt.prepare_coulomb_torque = prep
    return stats
