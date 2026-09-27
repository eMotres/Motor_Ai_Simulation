"""Six-coil study (Controller Stage 3) — solver-direct runs of one saved duty.

    python scripts/six_coil_study.py run  <spec.json> <out.json>
    python scripts/six_coil_study.py plan <plan.json> <workdir>

``run`` solves ONE case in this process; ``plan`` runs a list of cases one
after another, each in its own child process (Normal priority, BLAS threads
capped, a private copy of the config — the live config/ is only READ), and
skips a case whose result file already exists.

Env of a ``run``: MOTOR_AI_SIM_CONFIG must point at a SANDBOX motor_config.yaml
(``plan`` makes one per case); the duty itself is read from the main repo's
config/dies (MOTRES_DIES, default ../config/dies of the main checkout).

Spec keys (all optional except tag):
  tag           name of the case
  die, cfg, duty    the saved duty (default L155 motor, rated 1x9 mm)
  mode          "std"      the stock three-phase current drive (drive=current)
                "per_coil" PerCoilCurrentSource
  harmonics     [[h, a_h, psi_h_deg], ...]   per-coil harmonic injection
  i_rms_scale   coil rms / the duty's coil rms (default 1 = equal copper loss)
  gamma_deg     overrides the duty's gamma
  open_coils    [0-based coil indices] opened (needs n_sectors 1, eddy off)
  daxis_deg     given d-axis (None = measured)
  steps, n_sectors, eddy, rotor_eddy, demag, frames (return_frames=steps)
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import time

MAIN = os.environ.get("MOTRES_MAIN", r"C:\Users\vadim\Projects\motor_ai_sim")
DIES = os.environ.get("MOTRES_DIES", os.path.join(MAIN, "config", "dies"))
DEFAULT = dict(die="CIANO10 200 opt", cfg="L155 motor", duty="rated 1x9 mm")


def compose(spec):
    import yaml
    from motor_ai_sim.routes.family import _ABSENT_MEANS, _ABSENT_MATERIALS
    die = spec.get("die", DEFAULT["die"])
    cfg = spec.get("cfg", DEFAULT["cfg"])
    dname = spec.get("duty", DEFAULT["duty"])
    d = yaml.safe_load(open(os.path.join(DIES, die, "die.yaml"), encoding="utf-8"))
    y = yaml.safe_load(open(os.path.join(DIES, die, cfg + ".yaml"), encoding="utf-8"))
    geo = dict(d.get("geometry") or {})
    geo.update({k: v for k, v in (y.get("geometry_overrides") or {}).items()
                if v is not None})
    for k, v in _ABSENT_MEANS.items():
        if geo.get(k) is None:
            geo[k] = v
    ns = float(geo["num_seg"])
    pps, sps = float(geo["num_poles_per_segment"]), float(geo["num_slots_per_segment"])
    geo["num_poles"] = int(round(ns * pps)); geo["angle_pole"] = 360.0 / (ns * pps)
    geo["num_slots"] = int(round(ns * sps)); geo["angle_slot"] = 360.0 / (ns * sps)
    duty = next(x for x in y["duties"] if x["name"] == dname)
    mats = {k: v for k, v in (y.get("materials") or {}).items() if v}
    for k, v in (duty.get("materials") or {}).items():
        if v and not mats.get(k):
            mats[k] = v
    for k, v in _ABSENT_MATERIALS.items():
        if not mats.get(k):
            mats[k] = v
    runs = duty.get("runs") or {}
    st = dict(((runs.get("current") or {}).get("settings")) or duty.get("mesh") or {})
    return d, y, geo, duty, mats, st


def _write_sandbox_config(y, geo, mats, duty, st):
    import yaml
    cfg_path = os.environ["MOTOR_AI_SIM_CONFIG"]
    if os.path.abspath(cfg_path).startswith(os.path.abspath(os.path.join(MAIN, "config"))):
        raise SystemExit("refusing to write the live config: set MOTOR_AI_SIM_CONFIG "
                         "to a sandbox copy")
    base = yaml.safe_load(open(cfg_path, encoding="utf-8"))
    base["geometry"].update(geo)
    base["materials"].update(mats)
    base["winding"] = dict(y.get("winding") or {})
    base["parts"] = dict(y.get("parts") or base.get("parts") or {})
    wnd = y.get("winding") or {}
    sd = str(wnd.get("star_delta") or duty.get("star_delta")
             or st.get("sim.starDelta") or "star")
    base["simulation"].update({"rpm": float(duty["rpm"]),
                               "gamma_deg": float(duty["gamma_deg"]),
                               "star_delta": sd})
    with open(cfg_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(base, f, allow_unicode=True, sort_keys=False)
    from motor_ai_sim.config import clear_config_cache
    clear_config_cache()
    return sd, wnd


def _harm(series, n_per_period):
    import numpy as np
    x = np.asarray(series, float)
    n = int(n_per_period)
    if x.size < n or n < 4:
        return {}
    x = x[-n:]
    F = np.fft.rfft(x) / n
    out = {"mean": float(F[0].real)}
    for h in range(1, n // 2):
        out[str(h)] = float(2 * abs(F[h]))
    return out


def run(spec_path, out_path):
    import logging
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    spec = json.load(open(spec_path, encoding="utf-8"))
    d, y, geo, duty, mats, st = compose(spec)
    sd, wnd = _write_sandbox_config(y, geo, mats, duty, st)
    import numpy as np
    import motor_ai_sim
    from motor_ai_sim.simulation.fem_solver_2d import em_transient_eval
    from motor_ai_sim.simulation.geometry_2d import build_winding_layout
    from motor_ai_sim.simulation import per_coil as pc

    n_par = int(wnd.get("n_parallel") or 1)
    I_term = float(duty["current_arms"])
    I_wind = I_term / (math.sqrt(3.0) if sd == "delta" else 1.0)
    scale = float(spec.get("i_rms_scale", 1.0))
    I_coil_rms = I_wind / n_par * scale
    gamma = float(spec.get("gamma_deg", duty["gamma_deg"]))
    steps = int(spec.get("steps") or st.get("sim.stepsPP", 36))
    mt = st.get("sim.magnetTempC")
    nsec = int(spec.get("n_sectors", st.get("mesh.nSectors", 2)))
    kw = dict(
        n_steps_per_period=steps, n_periods=1.0, gamma_deg=gamma,
        I_phase_rms=I_wind * scale, rpm=float(duty["rpm"]),
        n_parallel=n_par, connection=wnd.get("connection"), star_delta=sd,
        daxis_deg=(None if spec.get("daxis_deg") is None else float(spec["daxis_deg"])),
        mesh_size_mm=float(st.get("mesh.meshSize", 4)),
        min_size_mm=float(st.get("mesh.minSize", 0.3)),
        outer_air_factor=float(st.get("mesh.outerAir", 1.2)),
        gap_layers=float(st.get("mesh.gapLayers", 1)),
        n_sectors=(nsec if nsec > 1 else 1),
        coil_temp_c=float(st.get("sim.coilTemp", 120)),
        magnet_temp_c=(float(mt) if mt not in (None, "") else None),
        end_winding_factor=float(st.get("sim.endWinding", 0) or 0),
        rotor_eddy=bool(spec.get("rotor_eddy", True)),
        demag=bool(spec.get("demag", st.get("sim.demag", False))),
        pole_copy=bool(st.get("mesh.poleCopy", False)),
        iron_template=bool(st.get("mesh.ironTemplate", True)),
        geo_mesh=bool(st.get("mesh.geoMesh", True)),
        structured_gap=bool(st.get("mesh.structuredGap", True)),
        component_mesh_mm=dict(st.get("mesh.componentMesh") or {}),
        geo_override=dict(geo), eddy=bool(spec.get("eddy", True)),
        return_frames=(steps if spec.get("frames") else 0))
    open_coils = [int(j) for j in (spec.get("open_coils") or [])]
    if open_coils and (nsec > 1 or kw["eddy"]):
        raise SystemExit("an open coil needs n_sectors 1 and eddy off")

    layout = build_winding_layout(int(geo["num_slots"]), int(geo["num_poles"]) // 2,
                                  single_layer=int(wnd.get("layers", 1)) == 1,
                                  layout_str=wnd.get("layout") or None)
    coils = pc.coil_map(layout)
    wave = pc.CoilWaveform(0.0, tuple(tuple(h) for h in (spec.get("harmonics") or [])))
    wave = wave.scaled_to_rms(I_coil_rms)
    src = None
    mode = spec.get("mode", "per_coil")
    if mode == "per_coil":
        if kw["daxis_deg"] is None:
            raise SystemExit("per_coil needs the d-axis GIVEN (the source is built "
                             "before the solver would measure it)")
        scl = [0.0 if c.index in open_coils else 1.0 for c in coils]
        scl = [s * float(spec.get("live_coil_scale", 1.0)) for s in scl]
        src = pc.PerCoilCurrentSource(coils, wave, pole_pairs=int(geo["num_poles"]) // 2,
                                      daxis_deg=kw["daxis_deg"], gamma_deg=gamma,
                                      coil_scale=scl)
        kw["excitation"] = src
    t0 = time.time()
    if open_coils:
        with pc.open_coils_layout(open_coils):
            r = em_transient_eval(**kw)
    else:
        r = em_transient_eval(**kw)
    wall = time.time() - t0

    def f(k):
        v = r.get(k)
        try:
            return None if v is None else float(v)
        except Exception:
            return None

    def mean(k):
        v = r.get(k)
        if v is None:
            return None
        try:
            return float(np.mean(v))
        except Exception:
            return None

    T = np.asarray(r.get("T_em_Nm") or [], float)
    Tm = np.asarray(r.get("T_em_maxwell_Nm") or [], float)
    res = {
        "tag": spec.get("tag"), "spec": spec, "module": motor_ai_sim.__file__,
        "wall_s": wall, "kwargs": {k: v for k, v in kw.items()
                                  if k not in ("geo_override", "excitation")},
        "drive": r.get("drive"), "daxis_deg": f("daxis_deg"),
        "daxis_source": r.get("daxis_source"),
        "n_steps_per_period": r.get("n_steps_per_period"),
        "torque_method": r.get("torque_method"),
        "T_avg_Nm": f("T_avg_Nm"), "T_avg_maxwell_Nm": f("T_avg_maxwell_Nm"),
        "T_ripple_pct": f("T_ripple_pct"),
        "T_pp_Nm": float(T.max() - T.min()) if T.size else None,
        "T_harm": _harm(T, r.get("n_steps_per_period") or steps),
        "Tm_harm": _harm(Tm, r.get("n_steps_per_period") or steps),
        "P_cu_W": mean("P_cu_W"), "P_cu_dc_W": f("P_cu_dc_W"),
        "P_cu_ac_W": mean("P_cu_ac_W"), "P_fe_W": mean("P_fe_W"),
        "P_mag_W": mean("P_mag_eddy_W"), "P_shaft_W": mean("P_shaft_eddy_W"),
        "P_sleeve_W": mean("P_sleeve_eddy_W"),
        "P_loss_total_W": mean("P_loss_total_W"),
        "V_peak": f("V_peak"), "I_phase_rms_solved_A": f("I_phase_rms_solved_A"),
        "eddy_settled": r.get("eddy_settled"), "demag_summary": r.get("demag_summary"),
        "coil_I_rms_A": ({str(k + 1): v for k, v in pc.coil_rms(src).items()}
                         if src is not None else None),
        "coil_I_rms_target_A": I_coil_rms,
        "waveform": wave.as_dict(),
        "series": {k: [float(x) for x in (r.get(k) or [])]
                   for k in ("T_em_Nm", "T_em_maxwell_Nm", "I_A", "I_B", "I_C",
                             "V_A", "V_B", "V_C", "psi_A_Wb", "psi_B_Wb", "psi_C_Wb",
                             "rotor_angle_deg")},
    }
    if spec.get("frames"):
        # the field itself, so the gap integral can be redone without a solve
        fm = r.get("frames_mesh") or {}
        fr = r.get("frames") or []
        if fm and fr:
            np.savez_compressed(
                os.path.splitext(out_path)[0] + ".frames.npz",
                T=np.asarray(fm["T"], np.int32), tags=np.asarray(fm["tags"], np.int32),
                nsn=int(fm["nsn"]), P_mm=np.asarray(fr[0]["P_mm"], np.float64),
                Bx=np.asarray([f["Bx"] for f in fr], np.float32),
                By=np.asarray([f["By"] for f in fr], np.float32),
                step_idx=np.asarray([f.get("step_idx", i) for i, f in enumerate(fr)]))
        L = float(geo.get("stack_length") or 0.0)
        L = L / 1000.0 if L > 5 else L
        try:
            res["gap"] = pc.gap_forces(r, L if L > 0 else 1.0)
            res["gap"]["stack_length_m"] = L
        except Exception as e:  # noqa: BLE001 — a diagnostic must not lose the run
            res["gap"] = {"error": repr(e)}
    json.dump(res, open(out_path, "w"), indent=1, default=str)
    print(json.dumps({k: res[k] for k in ("tag", "wall_s", "T_avg_Nm", "T_avg_maxwell_Nm",
                                           "T_ripple_pct", "P_cu_W", "P_fe_W", "P_mag_W",
                                           "V_peak")}, default=str))


def plan(plan_path, workdir):
    specs = json.load(open(plan_path, encoding="utf-8"))
    os.makedirs(workdir, exist_ok=True)
    here = os.path.dirname(os.path.abspath(__file__))
    src_root = os.path.join(os.path.dirname(here), "src")
    prog = os.path.join(workdir, "progress.txt")
    for spec in specs:
        tag = spec["tag"]
        out = os.path.join(workdir, f"{tag}.json")
        if os.path.exists(out):
            continue
        cd = os.path.join(workdir, "cfg", tag)
        if os.path.isdir(cd):
            shutil.rmtree(cd)
        os.makedirs(cd)
        for fn in ("motor_config.yaml", "materials_library.yaml", "wire_stock.yaml",
                   "end_effect_3d.json"):
            p = os.path.join(MAIN, "config", fn)
            if os.path.exists(p):
                shutil.copy(p, cd)
        sp = os.path.join(workdir, f"{tag}.spec.json")
        json.dump(spec, open(sp, "w"), indent=1)
        env = dict(os.environ)
        thr = str(spec.get("threads", 6))
        env.update({"PYTHONPATH": src_root,
                    "MOTOR_AI_SIM_CONFIG": os.path.join(cd, "motor_config.yaml"),
                    "SB_NO_WARM_CACHE": "1", "OMP_NUM_THREADS": thr,
                    "MKL_NUM_THREADS": thr, "OPENBLAS_NUM_THREADS": thr})
        with open(prog, "a") as pf:
            pf.write(f"{time.strftime('%H:%M:%S')} {tag} start\n")
        t0 = time.time()
        with open(os.path.join(workdir, f"{tag}.out"), "w") as fo, \
                open(os.path.join(workdir, f"{tag}.err"), "w") as fe:
            rc = subprocess.call([sys.executable, os.path.abspath(__file__), "run", sp,
                                  out], cwd=cd, env=env, stdout=fo, stderr=fe)
        with open(prog, "a") as pf:
            pf.write(f"{time.strftime('%H:%M:%S')} {tag} exit={rc} "
                     f"wall={time.time() - t0:.0f}s\n")
    with open(prog, "a") as pf:
        pf.write(f"{time.strftime('%H:%M:%S')} PLAN DONE {os.path.basename(plan_path)}\n")


if __name__ == "__main__":
    if sys.argv[1] == "run":
        run(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == "plan":
        plan(sys.argv[2], sys.argv[3])
    else:
        raise SystemExit(__doc__)
