"""Motor passport — FULL card tasks (stage 2: 3-D + mechanics, stage 3:
PWM + controller, the coupled EM-thermal runs).  Server sandbox only.
Machine-agnostic: the die, configurations, controller and cooling studies come
from the spec (``--spec``, config/passport_specs/<die>.yaml).

Stage 1 lives in scripts/passport_pilot.py (same /work layout).  This file
adds independent TASKS that run as separate processes inside ONE container
(``parallel``), each in its own copy of the machine's sandbox workspace so no
two processes ever write the same store:

  stage_a  [--length L]   3-D Stage A on today's cross-section (k_flux); one
                          length = its own cold reference + the 2-D leg
  coupled  --machine M    the rated duty's coupled EM-thermal loop (routes.
                          coupled.run, sine) on the owner's Thermal-tab cooling
  cooling  --machine M --study ID
                          one of the spec's ``cooling_studies``: the coupled loop
                          with solve_to = continuous (S1 rating) or limits (time to
                          a limit) under ``robotics`` or ``propeller_air`` cooling
  mech     --machine M    rotor-stress limit speed (SF = 1, averaged) and, when
                          the spec has a beam, the critical speeds
  pwm      --machine M --point rated|peak [--fsw 48000 --dead-us 0.1 --spc 20]
                          the Controller's bridge (drive="inverter": device
                          drops + dead time + current loop, centred SVPWM) vs a
                          resolution-matched sine reference (harm_ref) at the
                          card's operating point, HOT temperatures
  demagseq --machine M --fi F
  parallel T1 ; T2 ; …    run tasks concurrently (threads split), wait for all

Every task writes ``/work/out/full/<task>.json`` (full precision + provenance).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parents[1]
SPEC: Dict[str, Any] = {}
DIE = ""


def _use_spec(name: str) -> None:
    global SPEC, DIE
    sys.path.insert(0, str(REPO / "src"))
    from motor_ai_sim.passport_v1.spec import default_spec_path, load_spec
    SPEC = load_spec(default_spec_path(REPO, name))
    DIE = str(SPEC["die"])


def _mspec(M: str) -> Dict[str, Any]:
    return dict(SPEC["machines"][M])


def _ctrl(M: str) -> Dict[str, Any]:
    """The machine's controller (spec); refuses when none is set."""
    c = _mspec(M).get("controller")
    if not c or c.get("status") != "set":
        raise SystemExit("%s: no controller set in the spec — PWM is not computed" % M)
    return dict(c)


PWM_SAMPLES_PER_CARRIER = 20


def _out(work: Path, name: str) -> Path:
    p = work / "out" / "full"
    p.mkdir(parents=True, exist_ok=True)
    return p / (name + ".json")


def _dump(path: Path, obj: Any) -> None:
    from motor_ai_sim.passport_v1.jobs import prune
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(prune(obj), fh, indent=1)
    os.replace(tmp, path)


def _snap(work: Path, M: str) -> Dict[str, Any]:
    return json.load(open(work / "out" / M / "snapshot.json", encoding="utf-8"))


def _state(work: Path, M: str) -> Dict[str, Any]:
    return json.load(open(work / "out" / M / "state.json", encoding="utf-8"))


def _hot_map(work: Path, M: str):
    from motor_ai_sim.passport_v1 import card
    od = work / "out" / M
    recs = card.load_records(od)
    recs.pop("__failed__", None)
    st = _state(work, M)
    pts, hm = card._map(recs, st, "hot", "hot_mtpa", 7)
    return hm, st, recs


def _materials(snap) -> Dict[str, str]:
    return {k: v["name"] for k, v in snap["materials"].items()}


def _task_ws(work: Path, M: str, task: str, with_dies: bool = False) -> Path:
    """A private copy of the machine's sandbox workspace for one task."""
    ws = work / "tasks" / task
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir(parents=True)
    shutil.copy2(work / f"ws_{M}" / "motor_config.yaml", ws / "motor_config.yaml")
    if with_dies:
        dd = ws / "dies" / DIE
        dd.mkdir(parents=True)
        src = work / "inputs" / str(SPEC["inputs_subdir"])
        for f in ["die.yaml"] + ["%s.yaml" % m["config"] for m in SPEC["machines"].values()]:
            shutil.copy2(src / f, dd / f)
    return ws


# ─────────────────────────────────────────────────────────────────────────────
#  stage_a — 3-D end effect
# ─────────────────────────────────────────────────────────────────────────────

def task_stage_a(work: Path, a) -> None:
    from motor_ai_sim.material_context import set_request_materials
    from motor_ai_sim.simulation.static3d.end_effect import run_stage_a
    snap = _snap(work, a.machine)
    set_request_materials({"assignment": _materials(snap), "materials": {}})
    L0 = float(snap["geometry"]["motor_length"])
    t0 = time.time()
    if a.length:
        # ONE stack length as its own reference (cold start), + the 2-D leg:
        # the 7-point warm-started sweep cost 69 min per length under load
        # (2026-10-05) — the card needs 12 mm (L12) and 20 mm (L20).
        geo = dict(snap["geometry"])
        geo["motor_length"] = float(a.length)
        p = run_stage_a(geo_override=geo, n_stack=4, l_factors=(1.0,),
                        do_bracket=False, do_2d=True, verbose=True)
        name = "stage_a_L%g" % float(a.length)
    else:
        lengths = [float(x) for x in (_mspec(a.machine).get("stage_a_lengths_mm")
                                      or [6.0, 9.0, 12.0, 16.0, 20.0, 24.0, 30.0])]
        p = run_stage_a(geo_override=dict(snap["geometry"]), n_stack=4,
                        l_factors=tuple(L / L0 for L in lengths),
                        do_bracket=True, do_2d=True, verbose=True)
        name = "stage_a"
    p["pilot"] = {"fidelity": "quick (n_stack=4), today's die cross-section",
                  "machine": a.machine, "wall_s": time.time() - t0, "geometry_sig":
                      snap["signatures"]["geometry_sig"]}
    _dump(_out(work, name), p)


# ─────────────────────────────────────────────────────────────────────────────
#  coupled — L12 rated EM-thermal loop
# ─────────────────────────────────────────────────────────────────────────────

def task_coupled(work: Path, a) -> None:
    M = a.machine
    snap = _snap(work, M)
    hm, st, _ = _hot_map(work, M)
    rd = snap["rated_duty"]
    from motor_ai_sim.material_context import set_request_materials
    from motor_ai_sim.routes import coupled as cp
    from motor_ai_sim.routes import simulation as sim
    from motor_ai_sim.simulation.fem_solver_2d import _NO_WARM_CACHE_CTX
    set_request_materials({"assignment": _materials(snap), "materials": {}})
    panel = json.load(open(work / "inputs" / "panel_settings.json", encoding="utf-8"))
    th = dict(((panel.get("thermal") or {}).get(str(SPEC.get("workspace_user"))) or {})
              .get("settings") or {})
    th = {k: v for k, v in th.items() if k not in ("view", "eqTemp", "showFlux",
                                                   "compareRows")}
    th["maxIter"] = str(int(a.max_iter))
    lp = st.get("loss_plan2") or st["loss_plan"]
    g_r = [p for p in lp["points"] if p.get("id") and abs(p["rpm"] - rd["rpm"]) < 1e-6
           and abs(p["I"] - rd["current_arms"]) < 1e-6][0]["gamma"]
    m = snap["mesh"]
    body = {
        "restore": False, "n_periods": 1,
        "n_steps_per_period": int(rd["steps_per_period"]),
        "gamma_deg": round(float(g_r), 3), "I_phase_rms": float(rd["current_arms"]),
        "rpm": float(rd["rpm"]), "mode": "motor",
        "mesh_size_mm": m["mesh_size_mm"], "min_size_mm": m["min_size_mm"],
        "outer_air_factor": m["outer_air_factor"], "gap_layers": m["gap_layers"],
        "n_sectors": m["n_sectors"], "motion_band": True, "band_thickness_mm": 0.4,
        "stator_fillet_mm": 0, "sliding_band": True,
        "rotor_eddy": True, "eddy": True, "field_snapshot": True, "demag": True,
        "torque_filter": False, "pole_copy": m["pole_copy"], "structured_gap": True,
        "airgap_macro": False, "element_order": 2, "iron_template": m["iron_template"],
        "geo_mesh": m["geo_mesh"],
        "coil_temp_c": float(rd["coil_temp_c"]),
        "magnet_temp_c": float(rd["magnet_temp_c"]),
        "end_winding_factor": float(rd["end_winding_factor"]),
        "connection": snap["winding"]["connection"],
        "star_delta": snap["winding"].get("star_delta", "star"),
        "component_mesh": json.dumps(m.get("component_mesh_mm") or {}),
        "geo": json.dumps(snap["geometry"]),
        "mat": json.dumps({"assignment": _materials(snap), "materials": {}}),
        "include_frames": False, "n_frames": 0, "run_id": "pp-coupled-" + M,
        "mechanical": False, "cold_constants": False,
        "max_iter": int(a.max_iter), "drive": "current",
        "thermal_settings": th, "solve_to": "steady",
    }
    sim._BACKGROUND_RUN.set(False)        # the loss map must be kept (field snapshot)
    tok = _NO_WARM_CACHE_CTX.set(True)
    t0 = time.time()
    try:
        out = cp.run(dict(body))
        err = None
    except Exception as e:                # noqa: BLE001
        out, err = None, "%s: %s" % (type(e).__name__, getattr(e, "detail", e))
        traceback.print_exc()
    finally:
        _NO_WARM_CACHE_CTX.reset(tok)
    rec = {"task": "coupled", "machine": M, "wall_s": time.time() - t0, "error": err,
           "body": {k: v for k, v in body.items() if k not in ("geo", "mat")},
           "thermal_settings_source": "owner's Thermal tab (.panel_settings.json, "
                                      "updated 2026-09-30) — L12 rated has no stored "
                                      "thermal record of its own",
           "coupling": (out or {}).get("coupling"),
           "thermal": {k: (out or {}).get("thermal", {}).get(k)
                       for k in ("T_max", "cooling", "point", "components",
                                 "P_loss_total_W")} if out else None,
           "summary": ((out or {}).get("transient") or {}).get("summary")}
    _dump(_out(work, "coupled_" + M), rec)


# ─────────────────────────────────────────────────────────────────────────────
#  cooling — S1 rating / time to a limit per cooling option (spec cooling_studies)
# ─────────────────────────────────────────────────────────────────────────────

#: Ambient of every cooling study (°C): the saved robotics setup of the duty
#: (2026-09-28: still air 40 °C); the propeller studies use the same air so the
#: two coolings are compared at one ambient (stated on every record).
COOLING_AMBIENT_C = 40.0


def cooling_panel(cooling: str, *, rpm: float, ambient_c: float = COOLING_AMBIENT_C,
                  propeller: str = None) -> Dict[str, Any]:
    """The Thermal-panel fields of one cooling option (string values, as stored).

    ``robotics``: the existing robotics mode exactly as the rated duty saved it
    (still air + radiation, emissivity 0.9, heat path into the housing / mount,
    both end faces in still air, still air in the bore, housed frame).
    ``propeller_air``: the air mode with the housing film's air speed computed
    from the propeller's slipstream at this rpm (``propeller.air_speed_for_
    thermal``, default motor position behind the hub, factor 0.4 — an
    engineering assumption to be calibrated); bore still air, housed frame."""
    base = {"ambientT": "%g" % ambient_c, "boreMode": "still", "frame": "housed",
            "shaftExtMm": "0"}
    if cooling == "robotics":
        return dict(base, coolMode="robotics", emissivity="0.9", heatPath="housing",
                    endFaces="still", endFaceSides="2"), None
    if cooling == "propeller_air":
        from motor_ai_sim import propeller as PP
        op = PP.air_speed_for_thermal(str(propeller), float(rpm), ambient_c=ambient_c)
        return dict(base, coolMode="air", airSpeed="%.4f" % float(op["air_speed_mps"])), op
    raise SystemExit("unknown cooling %r" % cooling)


def task_cooling(work: Path, a) -> None:
    M = a.machine
    studies = {s["id"]: s for s in (_mspec(M).get("cooling_studies") or [])}
    if a.study not in studies:
        raise SystemExit("study %r not in the spec (%s)" % (a.study, sorted(studies)))
    sd = dict(studies[a.study])
    if a.prop:
        sd["propeller"] = a.prop
    snap = _snap(work, M)
    hm, st, _ = _hot_map(work, M)
    from motor_ai_sim.passport_v1 import psimap as PM
    from motor_ai_sim.material_context import set_request_materials
    from motor_ai_sim.routes import coupled as cp
    from motor_ai_sim.routes import simulation as sim
    from motor_ai_sim.simulation.fem_solver_2d import _NO_WARM_CACHE_CTX
    from motor_ai_sim.passport_v1.stages import loss_steps
    set_request_materials({"assignment": _materials(snap), "materials": {}})
    lp = st.get("loss_plan3") or st.get("loss_plan2") or st["loss_plan"]
    I0 = float(snap["rated_duty"]["current_arms"])
    I = I0 if sd.get("current") == "rated" else (
        float(lp["I_peak_rms"]) if sd.get("current") == "peak" else float(sd["current"]))
    n = float(sd["rpm"])
    vlim = PM.v_phase_limit(float(lp["v_dc"]), float(lp["m"]))
    g, how = hm.operating_gamma(I, n, float(lp["R_hot_ohm"]), vlim)
    if g is None:
        raise SystemExit("%s: no operating angle at %g A, %g rpm" % (a.study, I, n))
    th, prop_op = cooling_panel(str(sd["cooling"]), rpm=n, propeller=sd.get("propeller"))
    th["maxIter"] = str(int(a.max_iter))
    rd = snap["rated_duty"]
    m = snap["mesh"]
    Tm0, Tc0 = snap["temperatures"]["hot_magnet_c"], snap["temperatures"]["hot_coil_c"]
    body = {
        "restore": False, "n_periods": 1,
        "n_steps_per_period": loss_steps(snap),
        "gamma_deg": round(float(g), 3), "I_phase_rms": float(I),
        "rpm": n, "mode": "motor",
        "mesh_size_mm": m["mesh_size_mm"], "min_size_mm": m["min_size_mm"],
        "outer_air_factor": m["outer_air_factor"], "gap_layers": m["gap_layers"],
        "n_sectors": m["n_sectors"], "motion_band": True, "band_thickness_mm": 0.4,
        "stator_fillet_mm": 0, "sliding_band": True,
        "rotor_eddy": True, "eddy": True, "field_snapshot": True, "demag": True,
        "torque_filter": False, "pole_copy": m["pole_copy"], "structured_gap": True,
        "airgap_macro": False, "element_order": 2, "iron_template": m["iron_template"],
        "geo_mesh": m["geo_mesh"],
        "coil_temp_c": float(Tc0), "magnet_temp_c": float(Tm0),
        "end_winding_factor": float(rd["end_winding_factor"]),
        "connection": snap["winding"]["connection"],
        "star_delta": snap["winding"].get("star_delta", "star"),
        "component_mesh": json.dumps(m.get("component_mesh_mm") or {}),
        "geo": json.dumps(snap["geometry"]),
        "mat": json.dumps({"assignment": _materials(snap), "materials": {}}),
        "include_frames": False, "n_frames": 0, "run_id": "pp-cool-%s-%s" % (M, a.study),
        "mechanical": False, "cold_constants": False, "fresh": True,
        "max_iter": int(a.max_iter), "drive": "current",
        "thermal_settings": th, "solve_to": str(sd["solve_to"]),
    }
    sim._BACKGROUND_RUN.set(False)        # the loss map must be kept (field snapshot)
    tok = _NO_WARM_CACHE_CTX.set(True)
    from motor_ai_sim.passport_v1.jobs import wait_while_paused
    wait_while_paused()
    t0 = time.time()
    try:
        out = cp.run(dict(body))
        err = None
    except Exception as e:                # noqa: BLE001
        out, err = None, "%s: %s" % (type(e).__name__, getattr(e, "detail", e))
        traceback.print_exc()
    finally:
        _NO_WARM_CACHE_CTX.reset(tok)
    rec = {"task": "cooling", "machine": M, "study": sd, "wall_s": time.time() - t0,
           "error": err, "point": {"I_rms": I, "rpm": n, "gamma_deg": g, "gamma_mode": how,
                                   "start_temps_c": {"magnet": Tm0, "coil": Tc0}},
           "thermal_settings": th, "ambient_c": COOLING_AMBIENT_C,
           "propeller_operating_point": prop_op,
           "body": {k: v for k, v in body.items() if k not in ("geo", "mat")},
           "coupling": (out or {}).get("coupling"),
           "thermal": {k: (out or {}).get("thermal", {}).get(k)
                       for k in ("T_max", "cooling", "point", "components",
                                 "P_loss_total_W")} if out else None,
           "summary": ((out or {}).get("transient") or {}).get("summary")}
    _dump(_out(work, "cooling_%s_%s" % (M, a.study)), rec)


# ─────────────────────────────────────────────────────────────────────────────
#  mech — rotor-stress limit speed + critical speeds
# ─────────────────────────────────────────────────────────────────────────────

def task_mech(work: Path, a) -> None:
    M = a.machine
    snap = _snap(work, M)
    rd = snap["rated_duty"]
    from motor_ai_sim.material_context import set_request_materials
    from motor_ai_sim.routes import mechanical as mech
    set_request_materials({"assignment": _materials(snap), "materials": {}})
    panel = json.load(open(work / "inputs" / "panel_settings.json", encoding="utf-8"))
    mp = dict(((panel.get("mechanical") or {}).get(str(SPEC.get("workspace_user"))) or {})
              .get("settings") or {})
    contacts = mp.get("contacts") or {}
    # the larger stored duty torque (rated / peak): the stress check is on the
    # worst torque the rotor carries (Ø85: the stored rated result is a
    # demagnetised 210 °C solve, the peak duty's torque is the real load)
    pk = snap.get("peak_duty") or {}
    T_rated = max(float(rd["stored_summary"].get("T_em_avg_Nm") or 0.0),
                  float((pk.get("stored_summary") or {}).get("T_em_avg_Nm") or 0.0))
    Tm = float(snap["temperatures"]["hot_magnet_c"])
    geo = json.dumps(snap["geometry"])
    out: Dict[str, Any] = {"task": "mech", "machine": M, "panel_used": {
        "contacts": contacts, "meshMm": mp.get("meshMm"), "osf": mp.get("osf"),
        "beam_panel": mp.get("beam")}}
    t0 = time.time()
    try:
        r = mech.limit_speed(rpm=float(rd["rpm"]), interference_mm=0.0,
                             mesh_size_mm=float(mp.get("meshMm") or 0.5), order=2,
                             contacts=json.dumps(contacts), loads="both",
                             torque_nm=T_rated, rotor_temp_c=Tm, sleeve_temp_c=Tm,
                             magnet_temp_c=Tm, rotor_core_temp_c=Tm, shaft_temp_c=Tm,
                             symmetry="full", target_sf=1.0, max_factor=20.0,
                             max_solves=12, geo=geo, fresh=True)
        out["limit_speed"] = r
    except Exception as e:                # noqa: BLE001
        out["limit_speed_error"] = "%s: %s" % (type(e).__name__, getattr(e, "detail", e))
        traceback.print_exc()
    ms = dict(_mspec(M).get("mech") or {})
    out["torque_used_Nm"] = T_rated
    if not ms.get("critical_speeds", True):
        out["critical_speeds_status"] = str(ms.get("critical_note") or "not computed (spec)")
    else:
        L = float(snap["geometry"]["motor_length"])
        bm = dict(ms.get("beam") or {})
        # The shaft line is the DRAWING; none is in the repo.  Default pending
        # owner (spec): bearings at the stack ends, overhangs, the Mechanical
        # tab's bearing stiffness.
        span = L + float(bm.get("bearing_span_extra_mm", 16.0))
        oa, ob = float(bm.get("overhang_a_mm", 5.0)), float(bm.get("overhang_b_mm", 5.0))
        beam = {"bearing_span_mm": span, "overhang_a_mm": oa, "overhang_b_mm": ob,
                "bearing_k_n_per_m": float(((mp.get("beam") or {}).get("bearing_k_n_per_m"))
                                           or 1.5e7),
                "source": str(bm.get("source") or "default pending owner")}
        out["beam_used"] = beam
        try:
            c = mech.critical_speeds(bearing_span_mm=span, stack_length_mm=0.0,
                                     stack_offset_mm=0.0, overhang_a_mm=oa, overhang_b_mm=ob,
                                     shaft_od_mm=0.0, shaft_id_mm=-1.0,
                                     bearing_k_n_per_m=beam["bearing_k_n_per_m"],
                                     stack_stiffness_fraction=0.0, rpm=float(rd["rpm"]),
                                     n_modes=6, rpm_max_factor=1.3, mesh_size_mm=1.0,
                                     f_switch_hz=48000.0, geo=geo)
            out["critical_speeds"] = c
        except Exception as e:                # noqa: BLE001
            out["critical_speeds_error"] = "%s: %s" % (type(e).__name__, getattr(e, "detail", e))
            traceback.print_exc()
    out["wall_s"] = time.time() - t0
    _dump(_out(work, "mech_" + M), out)


# ─────────────────────────────────────────────────────────────────────────────
#  pwm — the Controller's bridge vs a matched sine
# ─────────────────────────────────────────────────────────────────────────────

def _route_kw(snap, *, I, g, rpm, Tm, Tc, steps):
    m = snap["mesh"]
    return dict(
        n_steps_per_period=int(steps), n_periods=1.0, gamma_deg=float(g),
        I_phase_rms=float(I), rpm=float(rpm), mode="motor",
        connection=snap["winding"]["connection"],
        star_delta=snap["winding"].get("star_delta", "star"),
        mesh_size_mm=m["mesh_size_mm"], min_size_mm=m["min_size_mm"],
        outer_air_factor=m["outer_air_factor"], gap_layers=m["gap_layers"],
        n_sectors=m["n_sectors"], component_mesh=json.dumps(m.get("component_mesh_mm") or {}),
        coil_temp_c=float(Tc), magnet_temp_c=float(Tm),
        end_winding_factor=float(snap["rated_duty"]["end_winding_factor"]),
        eddy=True, rotor_eddy=True, demag=True, field_snapshot=False,
        include_frames=False, n_frames=0, ledger=False, fresh=True, restore=False,
        pole_copy=m["pole_copy"], iron_template=m["iron_template"],
        geo_mesh=m["geo_mesh"], structured_gap=True, element_order=2,
        geo=json.dumps(snap["geometry"]),
        mat=json.dumps({"assignment": _materials(snap), "materials": {}}))


def task_pwm(work: Path, a) -> None:
    M, point = a.machine, a.point
    snap = _snap(work, M)
    hm, st, _ = _hot_map(work, M)
    from motor_ai_sim.passport_v1 import psimap as PM
    from motor_ai_sim.routes import simulation as sim
    from motor_ai_sim.inverter.coupling import fit_device_drop
    from motor_ai_sim.inverter.devices import get_device
    lp = st.get("loss_plan3") or st.get("loss_plan2") or st["loss_plan"]
    I0, n0 = float(snap["rated_duty"]["current_arms"]), float(snap["rated_duty"]["rpm"])
    if a.fi:
        # an extra current anchor at rated speed (2026-10-05 PWM grid)
        I, n = float(a.fi) * I0, n0
        point = "I%gI0" % float(a.fi)
    elif point == "rated":
        I, n = I0, n0
    else:
        pk = snap.get("peak_duty")
        I, n = float(lp["I_peak_rms"]), (float(pk["rpm"]) if pk else n0)
    vdc = float(snap["battery"]["v_nom"])
    C = _ctrl(M)
    m = float(C["m"])
    g, how = hm.operating_gamma(I, n, float(lp["R_hot_ohm"]), PM.v_phase_limit(vdc, m))
    if g is None:
        raise SystemExit("%s %s: no operating angle at m = %g" % (M, point, m))
    g = round(g, 3)
    Tm, Tc = snap["temperatures"]["hot_magnet_c"], snap["temperatures"]["hot_coil_c"]
    t_src = "snapshot hot temperatures (rated duty)"
    cp_path = _out(work, "coupled_" + M)
    if cp_path.exists():
        cpl = (json.load(open(cp_path, encoding="utf-8")).get("coupling") or {})
        if cpl.get("converged") and cpl.get("coil_temp_c") is not None:
            Tm, Tc = float(cpl["magnet_temp_c"]), float(cpl["coil_temp_c"])
            t_src = ("coupled EM-thermal rated loop, converged (owner default 4, "
                     "pending owner)")
    sim._BACKGROUND_RUN.set(True)
    ctrl = {k: C[k] for k in ("device", "r_g_ohm", "n_parallel", "v_gs_on_V", "v_gs_off_V",
                              "dead_time_us", "f_carrier_hz", "modulation", "m",
                              "t_j_assumed_c")}
    ctrl["devices_parallel"] = int(C.get("n_parallel") or 1)
    if a.device:
        ctrl["device"] = a.device
    if a.dead_us:
        ctrl["dead_time_us"] = float(a.dead_us)
    if a.fsw:
        ctrl["f_carrier_hz"] = float(a.fsw)
    rec: Dict[str, Any] = {"task": "pwm", "machine": M, "point": point, "I": I, "rpm": n,
                           "gamma": g, "gamma_mode": how, "v_dc": vdc, "m_limit": m,
                           "temps": {"magnet_c": Tm, "coil_c": Tc, "source": t_src},
                           "controller": ctrl}
    from motor_ai_sim.passport_v1.jobs import wait_while_paused
    wait_while_paused()
    t0 = time.time()
    # 1) the sine current-drive run at the point: the feed-forward fundamental
    from motor_ai_sim.passport_v1.stages import loss_steps
    s = sim.get_fem_transient(**_route_kw(snap, I=I, g=g, rpm=n, Tm=Tm, Tc=Tc,
                                          steps=loss_steps(snap)))
    ss = s.get("summary") or {}
    rec["sine_36"] = ss
    rec["sine_36_wall_s"] = time.time() - t0
    v1, dl = float(ss["V1_seed_peak_V"]), float(ss["V1_seed_delta_deg"])
    # 2) the Controller's bridge
    from motor_ai_sim.passport_v1.stages import pole_pairs
    f_el = n * pole_pairs(snap) / 60.0
    fsw = float(ctrl["f_carrier_hz"])
    nc = max(int(round(fsw / f_el)), 1)
    spc = int(a.spc or PWM_SAMPLES_PER_CARRIER)
    steps = spc * nc
    rec["samples_per_carrier"] = spc
    card = get_device(ctrl["device"])
    gan = str(card.doc.get("technology") or "").startswith("gan")
    npar = int(ctrl["devices_parallel"])
    drop = fit_device_drop(card, t_j_c=float(C["t_j_assumed_c"]),
                           n_parallel=npar, i_leg_peak_A=I * math.sqrt(2.0),
                           v_gs_on_V=(5.0 if gan else float(C["v_gs_on_V"])),
                           v_gs_off_V=0.0,
                           dead_time_s=float(ctrl["dead_time_us"]) * 1e-6)
    kw = _route_kw(snap, I=I, g=g, rpm=n, Tm=Tm, Tc=Tc, steps=steps)
    kw.update(drive="inverter", v_bus=vdc, f_switch=fsw, v_phase_peak=v1,
              v_delta_deg=dl, harm_ref=True, inv_modulation="svpwm",
              inv_r_ds_ohm=float(drop.r_ds_ohm), inv_v_sd_v0_V=float(drop.v_sd_v0_V),
              inv_v_sd_rd_ohm=float(drop.v_sd_rd_ohm),
              inv_dead_time_us=float(drop.dead_time_s) * 1e6,
              inv_device=card.part, inv_devices_parallel=npar,
              inv_t_j_c=float(drop.t_j_c), inv_topology="one_3ph")
    rec["drop"] = drop.__dict__ if hasattr(drop, "__dict__") else str(drop)
    rec["steps_pwm"], rec["carriers_per_period"] = steps, nc
    # The PWM step count raises the slip ring (>= 640 nodes/period); netgen
    # then rejects the iron-template wedge and the build falls back to gmsh,
    # whose rotor mesh is not pole-periodic — the route's sine reference
    # (harm_ref) asks for TDM there and is refused (TdmMeshNotPeriodic).  The
    # PWM run itself marches (TDM does not apply to a PWM circuit), so the
    # reference is made to march too: same mesh, same steps, same eddy method
    # — its only difference stays the drive.
    os.environ["SB_EDDY_METHOD"] = "march"
    rec["eddy_method_both"] = "march (SB_EDDY_METHOD) — see the comment in task_pwm"
    t1 = time.time()
    try:
        p = sim.get_fem_transient(**kw)
        rec["pwm_summary"] = p.get("summary")
        rec["pwm_block"] = p.get("pwm")
        rec["harm_ref"] = p.get("harm_ref")
        rec["dP_harm_W"] = p.get("dP_harm_W")
        rec["pwm_raw_keys"] = {k: p.get(k) for k in (
            "I_phase_rms_solved_A", "eddy_method", "eddy_method_note", "steady_state",
            "steady_state_note", "demag_settled", "voltage_settle", "v_dc_residual_A",
            "n_frames_solved", "solve_wall_s", "gap_layers_effective")}
    except Exception as e:                # noqa: BLE001
        rec["pwm_error"] = "%s: %s" % (type(e).__name__, getattr(e, "detail", e))
        traceback.print_exc()
    rec["pwm_wall_s"] = time.time() - t1
    tag = "pwm_%s_%s_%s_%dk_%dns" % (M, point, card.part, int(round(fsw / 1000)),
                                      int(round(float(ctrl["dead_time_us"]) * 1000)))
    if a.spc:
        tag += "_spc%d" % spc
    _dump(_out(work, tag), rec)


# ─────────────────────────────────────────────────────────────────────────────
#  demagseq — the owner-default demag current limit (default 2, pending owner)
# ─────────────────────────────────────────────────────────────────────────────

def task_demagseq(work: Path, a) -> None:
    """Overload, then the rated point on the SAME damaged magnet.

    Owner default (2), pending owner: the demag current limit is the highest
    current after which the subsequent rated-point torque drops by <= 1 %.
    One process per overload level: (1) the overload point on the hot MTPA
    line at rated speed (TDM coupled eddy + full demag pre-pass, the stage-1
    demag-probe settings); (2) the rated operating point, its Br ratchet
    CONTINUED from (1) through the solver's own sweep seed
    (SB_SEED_FROM_PREVIOUS=1: the damaged per-element Br is projected onto the
    rated run and the ratchet stays active; ``demag_seeded`` must come back
    true or the pair is refused).  The reference is the same rated point from
    virgin magnets (its own pre-pass), solved here too."""
    os.environ["SB_NO_WARM_CACHE"] = "0"
    os.environ["SB_SEED_FROM_PREVIOUS"] = "1"
    M, fI = a.machine, float(a.fi)
    snap = _snap(work, M)
    hm, st, recs = _hot_map(work, M)
    from motor_ai_sim.passport_v1 import jobs as J
    from motor_ai_sim.passport_v1 import psimap as PM
    from motor_ai_sim.passport_v1 import stages as S
    from motor_ai_sim.material_context import set_request_materials
    from motor_ai_sim.simulation import fem_solver_2d as F
    set_request_materials({"assignment": _materials(snap), "materials": {}})
    Tm, Tc = S._hot(snap)
    I0, n0 = S._I0(snap), S._n0(snap)
    steps = S.loss_steps(snap)
    b = S._base(snap, st, static=False)
    lp = st.get("loss_plan2") or st["loss_plan"]
    vlim = PM.v_phase_limit(float(lp["v_dc"]), float((_mspec(M).get("controller") or {}).get("m")
                                                     or 0.89))
    g_r, how_r = hm.operating_gamma(I0, n0, float(lp["R_hot_ohm"]), vlim)
    lv = [m for m in st["hot_mtpa"] if abs(m["fI"] - fI) < 1e-9]
    g_o = float(lv[0]["gamma_mtpa"]) if lv else float(hm.gamma_mtpa(fI * I0))
    out: Dict[str, Any] = {"task": "demagseq", "machine": M, "fI": fI, "I_over": fI * I0,
                           "gamma_over": g_o, "I_rated": I0, "gamma_rated": g_r,
                           "gamma_rated_mode": how_r, "rpm": n0, "temps": [Tm, Tc]}
    t0 = time.time()

    def solve(I, g):
        j = J.loss_job("x", b, I_rms=I, gamma_deg=round(g, 3), rpm=n0, magnet_temp_c=Tm,
                       coil_temp_c=Tc, steps=steps)
        r = F.em_transient_eval(**j["kw"])
        ds = r.get("demag_summary") or {}
        return {"T_Nm": float(r["T_avg_Nm"]), "demag_seeded": r.get("demag_seeded"),
                "demag_seed_from": r.get("demag_seed_from"),
                "br_kept_vol_pct": ds.get("br_kept_vol_pct"),
                "br_corner_pct": (ds.get("br_corner") or {}).get("br_pct"),
                "demag_settled": r.get("demag_settled"), "eddy_settled": r.get("eddy_settled"),
                "steady_state": r.get("steady_state"), "P_loss_total_W": r.get("P_loss_total_W")}
    F._SB_WARM_CACHE.clear()
    out["rated_virgin"] = solve(I0, g_r)          # reference, own pre-pass
    F._SB_WARM_CACHE.clear()
    try:
        F._warm_cache_path().unlink()
    except OSError:
        pass
    out["overload"] = solve(fI * I0, g_o)
    out["rated_after"] = solve(I0, g_r)
    ok = bool(out["rated_after"]["demag_seeded"])
    out["valid"] = ok
    out["torque_drop_pct"] = (100.0 * (1.0 - out["rated_after"]["T_Nm"]
                                       / out["rated_virgin"]["T_Nm"]) if ok else None)
    out["wall_s"] = time.time() - t0
    out["method"] = ("overload (TDM + demag pre-pass) -> rated point with the Br ratchet "
                     "continued (SB_SEED_FROM_PREVIOUS); drop vs the rated point from "
                     "virgin magnets; rated speed, hot temperatures")
    _dump(_out(work, "demagseq_%s_%g" % (M, fI)), out)


# ─────────────────────────────────────────────────────────────────────────────
#  parallel supervisor
# ─────────────────────────────────────────────────────────────────────────────

def task_parallel(work: Path, a) -> None:
    specs = [s.strip() for s in " ".join(a.rest).split(";") if s.strip()]
    procs = []
    for s in specs:
        parts = s.split()
        threads = "1"
        if parts and parts[0].startswith("threads="):
            threads = parts.pop(0).split("=", 1)[1]
        name = "_".join(p.replace("--", "") for p in parts)
        M = parts[parts.index("--machine") + 1] if "--machine" in parts else             next(iter(SPEC["machines"]))
        ws = _task_ws(work, M, name, with_dies=(parts[0] in ("coupled", "cooling", "mech")))
        env = dict(os.environ, MOTOR_AI_SIM_CONFIG=str(ws / "motor_config.yaml"),
                   MKL_NUM_THREADS=threads, OMP_NUM_THREADS=threads,
                   OPENBLAS_NUM_THREADS=threads)
        log = open(work / "out" / "full" / ("log_" + name + ".txt"), "w")
        cmd = [sys.executable, "-u", __file__, "--work", str(work),
               "--spec", SPEC["_path"]] + parts
        procs.append((name, subprocess.Popen(cmd, env=env, stdout=log,
                                             stderr=subprocess.STDOUT), time.time()))
        print("started", name, "threads", threads, flush=True)
    for name, p, t0 in procs:
        rc = p.wait()
        print("done %s rc=%s %.0f s" % (name, rc, time.time() - t0), flush=True)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", default="/work")
    ap.add_argument("--spec", required=True)
    ap.add_argument("task")
    ap.add_argument("--machine", default=None)
    ap.add_argument("--study", default=None)
    ap.add_argument("--prop", default=None, help="propeller id for a propeller_air study")
    ap.add_argument("--spc", type=int, default=None,
                    help="PWM samples per carrier (default 20; >= 4 is the solver's floor)")
    ap.add_argument("--point", default="rated")
    ap.add_argument("--max-iter", type=int, default=6)
    ap.add_argument("--fsw", type=float, default=None)
    ap.add_argument("--dead-us", type=float, default=None)
    ap.add_argument("--device", default=None)
    ap.add_argument("--fi", type=float, default=None)
    ap.add_argument("--length", type=float, default=None)
    ap.add_argument("rest", nargs="*")
    a = ap.parse_args(argv)
    _use_spec(a.spec)
    if a.machine is None:
        a.machine = next(iter(SPEC["machines"]))
    work = Path(a.work)
    (work / "out" / "full").mkdir(parents=True, exist_ok=True)
    fn = {"stage_a": task_stage_a, "coupled": task_coupled, "mech": task_mech,
          "pwm": task_pwm, "demagseq": task_demagseq, "cooling": task_cooling,
          "parallel": task_parallel}[a.task]
    fn(work, a)


if __name__ == "__main__":
    main()
