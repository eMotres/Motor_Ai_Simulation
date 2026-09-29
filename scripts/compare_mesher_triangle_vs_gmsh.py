# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) MOTRES d.o.o. and contributors
"""Old Triangle CDT mesher vs gmsh: the same EM solve on saved duties.

Why: `triangle` was removed on 2026-09-29 (its licence forbids commercial use,
incompatible with the AGPL), together with the geometry-driven CDT mesher built
on it.  A geo-mesh request (the saved duties' default, mesh.geoMesh = True) is
now served by the gmsh build.  This script measures what that costs on the
reference machines: torque, losses and back-EMF with the OLD code (Triangle
installed) against the NEW code (gmsh), every other setting identical and read
from the saved duty.

Nothing here writes live data.  Each solve runs in its own child process with
  * a PRIVATE copy of motor_config.yaml / materials_library.yaml / ... in the
    work directory (MOTOR_AI_SIM_CONFIG points at it; a live path is refused),
  * the dies tree only READ,
  * `nice 19` (os.nice in the child), BLAS/OpenMP threads capped (default 6),
  * SB_NO_WARM_CACHE=1 (no shared warm-start cache).

Server usage (Linux, tonight, one run after another, ~minutes per case):

    # 1. two checkouts side by side (no stash, plain worktrees)
    git -C /opt/motres/app fetch origin
    git -C /opt/motres/app worktree add /srv/cmp/old origin/main          # has triangle
    git -C /opt/motres/app worktree add /srv/cmp/new origin/chore/license-agpl
    # 2. an isolated venv per side (never the service's own)
    python3.11 -m venv /srv/cmp/venv-old && /srv/cmp/venv-old/bin/pip install \
        -r /srv/cmp/old/requirements.txt triangle==20250106
    python3.11 -m venv /srv/cmp/venv-new && /srv/cmp/venv-new/bin/pip install \
        -r /srv/cmp/new/requirements.txt
    #    (install requirements-pardiso.txt into BOTH or NEITHER: same solver)
    # 3. plan: all cases, both meshers, loaded + no-load
    /srv/cmp/venv-new/bin/python /srv/cmp/new/scripts/compare_mesher_triangle_vs_gmsh.py plan \
        --old-src /srv/cmp/old/src --old-python /srv/cmp/venv-old/bin/python \
        --new-src /srv/cmp/new/src --new-python /srv/cmp/venv-new/bin/python \
        --config /srv/cmp/new/config --dies /srv/motres/config/dies \
        --workdir /srv/cmp/run1 --threads 6
    # 4. table (also written to <workdir>/report.md and report.json)
    /srv/cmp/venv-new/bin/python /srv/cmp/new/scripts/compare_mesher_triangle_vs_gmsh.py \
        report --workdir /srv/cmp/run1

`--dies` must be a READ-ONLY view of the dies (a copy is safest:
`cp -a <live dies> /srv/cmp/dies`).  Re-running `plan` skips finished cases;
`--cases` narrows the list (tags below), `--no-emf` skips the no-load runs.
Progress: <workdir>/progress.txt; logs: <workdir>/<tag>.err.

Cases (die / configuration / duty):
  L155  CIANO10 200 opt / L155 motor / rated 1x9 mm
  L180  CIANO10 200 opt / L180 gen   / rated 1x9 mm
  L13   CIANO28 85 20SW1200 / L13    / rated   (--l13-die to pick the other L13)
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

CASES = {
    "L155": ("CIANO10 200 opt", "L155 motor", "rated 1x9 mm"),
    "L180": ("CIANO10 200 opt", "L180 gen", "rated 1x9 mm"),
    "L13": ("CIANO28 85 20SW1200", "L13", "rated"),
}
CONFIG_FILES = ("motor_config.yaml", "materials_library.yaml", "wire_stock.yaml",
                "end_effect_3d.json")
METRICS = (  # (key, label, unit)
    ("T_avg_Nm", "torque (energy)", "N*m"),
    ("T_ripple_pct", "torque ripple", "%"),
    ("P_cu_W", "copper loss", "W"),
    ("P_fe_W", "iron loss", "W"),
    ("P_mag_W", "magnet eddy loss", "W"),
    ("P_shaft_W", "shaft eddy loss", "W"),
    ("P_sleeve_W", "sleeve eddy loss", "W"),
    ("P_loss_total_W", "total loss", "W"),
    ("V_peak", "terminal voltage peak", "V"),
)
EMF_METRICS = (
    ("E_peak_V", "no-load EMF peak (phase)", "V"),
    ("E_h1_V", "no-load EMF fundamental", "V"),
    ("E_thd_pct", "no-load EMF THD", "%"),
    ("T_cog_pp_Nm", "cogging torque p-p", "N*m"),
)


# ── child: one solve ─────────────────────────────────────────────────────────
def _compose(dies, die, cfg, dname):
    import yaml
    from motor_ai_sim.routes.family import _ABSENT_MATERIALS, _ABSENT_MEANS
    d = yaml.safe_load(open(os.path.join(dies, die, "die.yaml"), encoding="utf-8"))
    y = yaml.safe_load(open(os.path.join(dies, die, cfg + ".yaml"), encoding="utf-8"))
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
    return y, geo, duty, mats, st


def _write_sandbox_config(y, geo, mats, duty, st, forbidden_roots):
    import yaml
    cfg_path = os.path.abspath(os.environ["MOTOR_AI_SIM_CONFIG"])
    for root in forbidden_roots:
        if root and cfg_path.startswith(os.path.abspath(root) + os.sep):
            raise SystemExit(f"refusing to write {cfg_path}: inside {root}")
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


def _harm(series, n):
    import numpy as np
    x = np.asarray(series, float)
    n = int(n or 0)
    if x.size < n or n < 4:
        return None
    x = x[-n:]
    F = np.fft.rfft(x) / n
    return [float(2 * abs(F[h])) for h in range(n // 2)]


def run(spec_path, out_path):
    import logging
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        os.nice(19)
    except (AttributeError, OSError):
        pass
    spec = json.load(open(spec_path, encoding="utf-8"))
    y, geo, duty, mats, st = _compose(spec["dies"], *spec["case"])
    sd, wnd = _write_sandbox_config(y, geo, mats, duty, st,
                                    [spec.get("live_config"), spec["dies"]])
    import importlib.util
    import numpy as np
    import motor_ai_sim
    from motor_ai_sim.simulation.fem_solver_2d import em_transient_eval

    have_triangle = importlib.util.find_spec("triangle") is not None
    if spec["mesher"] == "triangle" and not have_triangle:
        raise SystemExit("the 'triangle' side needs the old code WITH triangle installed")
    if spec["mesher"] == "gmsh" and have_triangle:
        raise SystemExit("the 'gmsh' side must run without triangle installed")

    n_par = int(wnd.get("n_parallel") or 1)
    I_term = float(duty["current_arms"])
    I_wind = I_term / (math.sqrt(3.0) if sd == "delta" else 1.0)
    noload = bool(spec.get("noload"))
    steps = int(st.get("sim.stepsPP", 36))
    mt = st.get("sim.magnetTempC")
    nsec = int(st.get("mesh.nSectors", 2) or 1)
    kw = dict(
        n_steps_per_period=steps, n_periods=1.0, gamma_deg=float(duty["gamma_deg"]),
        I_phase_rms=(0.0 if noload else I_wind), rpm=float(duty["rpm"]),
        n_parallel=n_par, connection=wnd.get("connection"), star_delta=sd,
        mesh_size_mm=float(st.get("mesh.meshSize", 4)),
        min_size_mm=float(st.get("mesh.minSize", 0.3)),
        outer_air_factor=float(st.get("mesh.outerAir", 1.2)),
        gap_layers=float(st.get("mesh.gapLayers", 1)),
        n_sectors=(nsec if nsec > 1 else 1),
        coil_temp_c=float(st.get("sim.coilTemp", 120)),
        magnet_temp_c=(float(mt) if mt not in (None, "") else None),
        end_winding_factor=float(st.get("sim.endWinding", 0) or 0),
        rotor_eddy=True, demag=bool(st.get("sim.demag", False)),
        pole_copy=bool(st.get("mesh.poleCopy", False)),
        iron_template=bool(st.get("mesh.ironTemplate", True)),
        # the SAME request on both sides: old code -> Triangle CDT, new -> gmsh
        geo_mesh=bool(st.get("mesh.geoMesh", True)),
        structured_gap=bool(st.get("mesh.structuredGap", True)),
        component_mesh_mm=dict(st.get("mesh.componentMesh") or {}),
        geo_override=dict(geo), eddy=True)
    t0 = time.time()
    r = em_transient_eval(**kw)
    wall = time.time() - t0

    def f(k):
        try:
            v = r.get(k)
            return None if v is None else float(v)
        except (TypeError, ValueError):
            return None

    def mean(k):
        v = r.get(k)
        try:
            return None if v is None else float(np.mean(v))
        except (TypeError, ValueError):
            return None

    nper = r.get("n_steps_per_period") or steps
    T = np.asarray(r.get("T_em_Nm") or [], float)
    res = {
        "spec": spec, "module": motor_ai_sim.__file__, "have_triangle": have_triangle,
        "wall_s": wall,
        "kwargs": {k: v for k, v in kw.items() if k != "geo_override"},
        "mesh_build_events": r.get("mesh_build_events"),
        "mesh_build_notes": r.get("mesh_build_notes"),
        "mesh": {k: v for k, v in r.items()
                 if isinstance(v, (int, float)) and any(
                     s in k.lower() for s in ("tri", "node", "n_elem", "dof"))},
        "T_avg_Nm": f("T_avg_Nm"), "T_ripple_pct": f("T_ripple_pct"),
        "P_cu_W": mean("P_cu_W"), "P_fe_W": mean("P_fe_W"),
        "P_mag_W": mean("P_mag_eddy_W"), "P_shaft_W": mean("P_shaft_eddy_W"),
        "P_sleeve_W": mean("P_sleeve_eddy_W"),
        "P_loss_total_W": mean("P_loss_total_W"), "V_peak": f("V_peak"),
    }
    if noload:
        V = np.asarray(r.get("V_A") or [], float)
        hv = _harm(V, nper)
        res["E_peak_V"] = float(np.abs(V[-int(nper):]).max()) if V.size else None
        if hv:
            h1 = hv[1]
            res["E_h1_V"] = h1
            res["E_thd_pct"] = (100.0 * math.sqrt(sum(a * a for a in hv[2:])) / h1
                                if h1 > 0 else None)
        res["T_cog_pp_Nm"] = float(T.max() - T.min()) if T.size else None
    json.dump(res, open(out_path, "w", encoding="utf-8"), indent=1, default=str)
    print(json.dumps({k: res.get(k) for k in ("wall_s", "T_avg_Nm", "P_loss_total_W",
                                               "V_peak", "E_h1_V")}, default=str))


# ── parent: the plan ─────────────────────────────────────────────────────────
def plan(a):
    os.makedirs(a.workdir, exist_ok=True)
    cases = dict(CASES)
    if a.l13_die:
        cases["L13"] = (a.l13_die, "L13", "rated")
    tags = a.cases.split(",") if a.cases else list(cases)
    prog = os.path.join(a.workdir, "progress.txt")
    sides = (("triangle", a.old_src, a.old_python), ("gmsh", a.new_src, a.new_python))
    for tag in tags:
        for noload in ((False,) if a.no_emf else (False, True)):
            for mesher, src, py in sides:
                name = f"{tag}_{'noload' if noload else 'load'}_{mesher}"
                out = os.path.join(a.workdir, name + ".json")
                if os.path.exists(out):
                    continue
                cd = os.path.join(a.workdir, "cfg", name)
                shutil.rmtree(cd, ignore_errors=True)
                os.makedirs(cd)
                for fn in CONFIG_FILES:
                    p = os.path.join(a.config, fn)
                    if os.path.exists(p):
                        shutil.copy(p, cd)
                spec = {"tag": tag, "case": list(cases[tag]), "mesher": mesher,
                        "noload": noload, "dies": os.path.abspath(a.dies),
                        "live_config": os.path.abspath(a.config)}
                sp = os.path.join(a.workdir, name + ".spec.json")
                json.dump(spec, open(sp, "w", encoding="utf-8"), indent=1)
                thr = str(a.threads)
                env = dict(os.environ)
                env.update({"PYTHONPATH": os.path.abspath(src),
                            "MOTOR_AI_SIM_CONFIG": os.path.join(cd, "motor_config.yaml"),
                            "SB_NO_WARM_CACHE": "1", "OMP_NUM_THREADS": thr,
                            "MKL_NUM_THREADS": thr, "OPENBLAS_NUM_THREADS": thr,
                            "NUMEXPR_MAX_THREADS": thr})
                with open(prog, "a") as pf:
                    pf.write(f"{time.strftime('%H:%M:%S')} {name} start\n")
                t0 = time.time()
                with open(os.path.join(a.workdir, name + ".out"), "w") as fo, \
                        open(os.path.join(a.workdir, name + ".err"), "w") as fe:
                    rc = subprocess.call([py, os.path.abspath(__file__), "run", sp, out],
                                         cwd=cd, env=env, stdout=fo, stderr=fe)
                with open(prog, "a") as pf:
                    pf.write(f"{time.strftime('%H:%M:%S')} {name} exit={rc} "
                             f"wall={time.time() - t0:.0f}s\n")
    with open(prog, "a") as pf:
        pf.write(f"{time.strftime('%H:%M:%S')} PLAN DONE\n")


def report(a):
    rows, out = [], {}
    tags = sorted({fn.split("_")[0] for fn in os.listdir(a.workdir)
                   if fn.endswith(".json") and "_" in fn and not fn.endswith(".spec.json")
                   and fn != "report.json"})
    lines = ["# Triangle CDT vs gmsh — same duty, same settings", "",
             "| case | quantity | triangle (old) | gmsh (new) | delta |",
             "|---|---|---:|---:|---:|"]
    for tag in tags:
        for kind, metrics in (("load", METRICS), ("noload", EMF_METRICS)):
            p_old = os.path.join(a.workdir, f"{tag}_{kind}_triangle.json")
            p_new = os.path.join(a.workdir, f"{tag}_{kind}_gmsh.json")
            if not (os.path.exists(p_old) and os.path.exists(p_new)):
                continue
            o = json.load(open(p_old, encoding="utf-8"))
            n = json.load(open(p_new, encoding="utf-8"))
            for key, label, unit in metrics:
                vo, vn = o.get(key), n.get(key)
                if vo is None and vn is None:
                    continue
                d = (100.0 * (vn - vo) / abs(vo)
                     if vo not in (None, 0) and vn is not None else None)
                out.setdefault(tag, {})[key] = {"triangle": vo, "gmsh": vn,
                                                "delta_pct": d}
                lines.append(f"| {tag} | {label} [{unit}] | "
                             f"{'' if vo is None else f'{vo:.4g}'} | "
                             f"{'' if vn is None else f'{vn:.4g}'} | "
                             f"{'' if d is None else f'{d:+.2f} %'} |")
            out.setdefault(tag, {})[f"{kind}_mesh"] = {"triangle": o.get("mesh"),
                                                       "gmsh": n.get("mesh")}
            out[tag][f"{kind}_wall_s"] = {"triangle": o.get("wall_s"),
                                          "gmsh": n.get("wall_s")}
            out[tag][f"{kind}_gmsh_build_notes"] = n.get("mesh_build_notes")
    rows = "\n".join(lines) + "\n"
    open(os.path.join(a.workdir, "report.md"), "w", encoding="utf-8").write(rows)
    json.dump(out, open(os.path.join(a.workdir, "report.json"), "w", encoding="utf-8"),
              indent=1, default=str)
    print(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="(internal) one solve in this process")
    r.add_argument("spec"); r.add_argument("out")
    p = sub.add_parser("plan", help="run every case on both meshers")
    p.add_argument("--old-src", required=True, help="src/ of a checkout WITH triangle")
    p.add_argument("--old-python", required=True, help="python of the venv with triangle")
    p.add_argument("--new-src", required=True, help="src/ of this branch")
    p.add_argument("--new-python", default=sys.executable)
    p.add_argument("--config", required=True, help="config/ to COPY per case (read only)")
    p.add_argument("--dies", required=True, help="dies tree (read only; a copy is safest)")
    p.add_argument("--workdir", required=True)
    p.add_argument("--threads", type=int, default=6)
    p.add_argument("--cases", default="", help="comma list of " + ",".join(CASES))
    p.add_argument("--l13-die", default="", help="die holding the L13 to compare")
    p.add_argument("--no-emf", action="store_true", help="skip the no-load runs")
    q = sub.add_parser("report", help="tabulate finished runs")
    q.add_argument("--workdir", required=True)
    a = ap.parse_args()
    if a.cmd == "run":
        run(a.spec, a.out)
    elif a.cmd == "plan":
        if a.threads > 6:
            raise SystemExit("threads <= 6 (the server is shared)")
        plan(a)
    else:
        report(a)


if __name__ == "__main__":
    main()
