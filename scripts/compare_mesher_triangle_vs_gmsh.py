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

Since stage S2 (docs/MESHER_TRANSITION.md) the geometry mesher has a gmsh CDT
backend next to Triangle, so ONE checkout with triangle installed runs both
sides: omit --old-src/--old-python and each side's child gets
MOTOR_AI_SIM_GEO_CDT=triangle|gmsh (the child refuses to run if the backend it
resolves differs from its side):

    python scripts/compare_mesher_triangle_vs_gmsh.py plan --new-src <tree>/src \
        --new-python <venv>/bin/python --config <config copy> --dies <dies copy> \
        --workdir <run dir> --threads 6

The report adds element counts (parsed from the child log), solve wall time
and peak RSS per side, and per mesh build its build time and quality (minimum
angle, aspect p99.9).

Netgen (the third CDT backend, docs/MESHER_NETGEN_2026-09-30.md) joins with
`--meshers triangle,gmsh,netgen` on `plan` and `report`; the first mesher is
the reference of the deltas and of the owner's acceptance criteria (torque
<= 1 %, ripple within max(0.5 pp, 10 %), total loss <= 5 %).

Two-tree usage (stage S1, old code with triangle vs new code without it):

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
    ("T_avg_Nm", "torque (reported)", "N*m"),
    ("T_ripple_pct", "torque ripple", "%"),
    ("T_avg_coulomb_Nm", "Coulomb mean torque", "N*m"),
    ("T_ripple_pp_coulomb_Nm", "Coulomb ripple p-p", "N*m"),
    ("T_ripple_pct_coulomb", "Coulomb ripple p-p / mean", "%"),
    ("coulomb_self_check", "Coulomb self-check (rel. to ripple scale)", "-"),
    ("P_cu_W", "copper loss", "W"),
    ("P_fe_W", "iron loss", "W"),
    ("P_mag_W", "magnet eddy loss", "W"),
    ("P_shaft_W", "shaft eddy loss", "W"),
    ("P_sleeve_W", "sleeve eddy loss", "W"),
    ("P_loss_total_W", "total loss", "W"),
    ("V_peak", "terminal voltage peak", "V"),
    ("P_in_W", "electrical input power", "W"),
    ("P_mech_W", "mechanical power", "W"),
    ("balance_residual_rel", "field power-balance residual (rel.)", "-"),
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
    # the duty's own `mesh` block is the saved statement of how it is run (the
    # app syncs it into the mesh config, routes/family.duty_mesh_patch); the last
    # run's settings only fill what the block does not state
    st = dict(((runs.get("current") or {}).get("settings")) or {})
    st.update(duty.get("mesh") or {})
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


def _versions():
    """Provenance: the mesher/solver library versions this solve ran on."""
    out = {"python": sys.version.split()[0]}
    for mod in ("gmsh", "triangle", "numpy", "scipy", "shapely", "pypardiso",
                "skfem"):
        try:
            m = __import__(mod)
            out[mod] = str(getattr(m, "__version__", "?"))
        except Exception:  # noqa: BLE001 — absent is a valid answer
            out[mod] = None
    return out


def run(spec_path, out_path):
    import logging
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        os.nice(19)
    except (AttributeError, OSError):
        pass
    spec = json.load(open(spec_path, encoding="utf-8"))
    # study knobs (e.g. SB_SKIN_H1_FRAC, SB_SLEEVE_LAYERS) set BEFORE any import
    os.environ.update({str(k): str(v) for k, v in (spec.get("env") or {}).items()})
    y, geo, duty, mats, st = _compose(spec["dies"], *spec["case"])
    # a duty without saved mesh settings runs with the ones named here (recorded)
    st = dict(st, **(spec.get("settings") or {}))
    sd, wnd = _write_sandbox_config(y, geo, mats, duty, st,
                                    [spec.get("live_config"), spec["dies"]])
    import importlib.util
    import numpy as np
    import motor_ai_sim
    from motor_ai_sim.simulation.fem_solver_2d import em_transient_eval

    have_triangle = importlib.util.find_spec("triangle") is not None
    try:     # S2 tree: one checkout, the CDT backend chosen per run
        from motor_ai_sim.simulation.geo_mesh import cdt_backend
        backend = cdt_backend()
    except ImportError:   # S1 tree: gmsh only where triangle is absent
        backend = "triangle" if have_triangle else "gmsh"
    if spec["mesher"] != backend:
        raise SystemExit(f"side {spec['mesher']!r} resolved to the {backend!r} "
                         "backend (MOTOR_AI_SIM_GEO_CDT / triangle install)")

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
        # spec "mesh_size_mm" overrides the duty's size (convergence checks)
        mesh_size_mm=float(spec.get("mesh_size_mm") or st.get("mesh.meshSize", 4)),
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
    # study overrides of the solve call (e.g. {"demag": false}), recorded in kwargs
    kw.update(spec.get("kw_override") or {})
    # Record every geometry-driven mesh build of this solve (hash of nodes,
    # triangles and tags, triangle count, band-ring node counts): identical
    # hashes between two code versions => identical numbers (the solve is
    # deterministic), so a pre-processing change can be shown to be a no-op.
    import hashlib
    from motor_ai_sim.simulation import geo_mesh as _gm
    _orig = _gm.geo_mesh_halves
    seen = []

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from mesher_campaign import _quality

    def _spy(p, polys, **k):
        _tb = time.time()
        out = _orig(p, polys, **k)
        _tb = time.time() - _tb
        ms, ts, _cs, mr, tr, _cr = out
        rec = {"kwargs": {kk: (vv if isinstance(vv, (int, float, str)) else str(vv))
                          for kk, vv in k.items() if kk != "skin_layers"}}
        for nm, m_, t_ in (("stator", ms, ts), ("rotor", mr, tr)):
            h = hashlib.sha1(np.ascontiguousarray(m_.p).tobytes()
                             + np.ascontiguousarray(m_.t).tobytes()
                             + np.ascontiguousarray(t_).tobytes()).hexdigest()
            rec[nm] = {"sha1": h, "n_tri": int(m_.t.shape[1]),
                       "quality": _quality(m_.p.T * 1e3, m_.t.T)}
        rec["t_build_s"] = _tb
        for nm, m_, rr in (("R1", mr, k.get("r1_band")), ("R2", ms, k.get("r2_band"))):
            if rr:
                P = m_.p.T * 1e3
                used = np.unique(m_.t)
                rad = np.hypot(P[used, 0], P[used, 1])
                rec[nm + "_nodes"] = int(np.sum(np.abs(rad - float(rr)) < 2e-3))
        seen.append(rec)
        return out
    _gm.geo_mesh_halves = _spy
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
    try:
        import resource
        maxrss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except ImportError:          # Windows
        maxrss_mb = None
    res = {
        "spec": spec, "module": motor_ai_sim.__file__, "have_triangle": have_triangle,
        "backend": backend, "wall_s": wall, "maxrss_mb": maxrss_mb,
        "versions": _versions(),
        "P_in_W": f("P_elec_in_W"), "P_mech_W": f("P_mech_avg_W"),
        "balance_residual_rel": ((r.get("power_balance") or {}).get("residual_rel")),
        # new defaults (#88): Coulomb virtual-work torque + measured gap rule
        "torque_method": r.get("torque_method") or r.get("torque_mean_source"),
        "T_avg_coulomb_Nm": f("T_avg_coulomb_Nm"),
        "T_ripple_pp_coulomb_Nm": f("T_ripple_pp_coulomb"),
        "T_ripple_pp_Nm": f("T_ripple_pp_Nm"),
        "T_ripple_pct_coulomb": (100.0 * f("T_ripple_pp_coulomb") / abs(f("T_avg_coulomb_Nm"))
                                 if f("T_ripple_pp_coulomb") is not None
                                 and f("T_avg_coulomb_Nm") else None),
        "coulomb_self_check": (((r.get("coulomb_torque") or {}).get("layer_self_check")
                                or {}).get("rel_to_ripple_scale")),
        "coulomb": {k: v for k, v in (r.get("coulomb_torque") or {}).items()
                    if k in ("available", "unavailable_reason", "layer_self_check",
                             "T_avg_coulomb_Nm", "T_ripple_pp_coulomb", "layers")},
        "gap_refinement": r.get("gap_refinement"),
        "gap_layers_effective": f("gap_layers_effective"),
        "mesher": r.get("mesher"),
        "eddy_settled": r.get("eddy_settled"),
        "kwargs": {k: v for k, v in kw.items() if k != "geo_override"},
        "mesh_build_events": r.get("mesh_build_events"),
        "mesh_build_notes": r.get("mesh_build_notes"),
        "mesh_builds": seen,
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
    # one tree (S2+): the same checkout and venv (with triangle installed),
    # the CDT backend switched by MOTOR_AI_SIM_GEO_CDT; two trees (S1): old
    # code with triangle vs new code without it.
    old_src = a.old_src or a.new_src
    old_py = a.old_python or a.new_python
    sides = tuple((m, old_src if m == "triangle" else a.new_src,
                   old_py if m == "triangle" else a.new_python)
                  for m in a.meshers.split(","))
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
                            "MOTOR_AI_SIM_GEO_CDT": mesher,
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


def _builds(res):
    """Sum over the solve's geometry-driven builds: build time, element count,
    worst minimum angle and worst aspect p99.9 over the halves."""
    b = res.get("mesh_builds") or []
    if not b:
        return {}
    q = [x[h]["quality"] for x in b for h in ("stator", "rotor") if "quality" in x.get(h, {})]
    return {"t_build_s": sum(float(x.get("t_build_s") or 0.0) for x in b),
            "n_builds": len(b),
            "n_tri_last": (b[-1]["stator"]["n_tri"] + b[-1]["rotor"]["n_tri"]),
            "min_angle_deg": min((x["min_angle_deg"] for x in q), default=None),
            "ar_p99_9": max((x["ar_p99_9"] for x in q), default=None)}


def _verdict(ref, oth):
    """The owner's criteria against the reference mesher (2026-09-30): Coulomb
    mean torque <= 1 %, ripple within max(0.5 pp, 10 %), total loss <= 5 %."""
    out = {}
    t0, t1 = ref.get("T_avg_coulomb_Nm"), oth.get("T_avg_coulomb_Nm")
    if t0 and t1 is not None:
        out["torque"] = abs(t1 - t0) <= 0.01 * abs(t0)
    r0, r1 = ref.get("T_ripple_pct_coulomb"), oth.get("T_ripple_pct_coulomb")
    if r0 is not None and r1 is not None:
        out["ripple"] = abs(r1 - r0) <= max(0.5, 0.10 * abs(r0))
    l0, l1 = ref.get("P_loss_total_W"), oth.get("P_loss_total_W")
    if l0 and l1 is not None:
        out["total_loss"] = abs(l1 - l0) <= 0.05 * abs(l0)
    return out


def report(a):
    meshers = a.meshers.split(",")
    ref_m = meshers[0]
    out = {}
    tags = sorted({fn.split("_")[0] for fn in os.listdir(a.workdir)
                   if fn.endswith(".json") and "_" in fn and not fn.endswith(".spec.json")
                   and fn != "report.json"})
    lines = ["# CDT backends: same duty, same settings (reference: %s)" % ref_m, "",
             "| case | quantity | " + " | ".join(meshers) + " | "
             + " | ".join("%s vs %s" % (m, ref_m) for m in meshers[1:]) + " |",
             "|---|---|" + "---:|" * (2 * len(meshers) - 1)]

    def _fmt(v, spec="{:.5g}"):
        return "" if v is None else spec.format(v)

    for tag in tags:
        for kind, metrics in (("load", METRICS), ("noload", EMF_METRICS)):
            res = {}
            for m in meshers:
                pth = os.path.join(a.workdir, f"{tag}_{kind}_{m}.json")
                if os.path.exists(pth):
                    res[m] = json.load(open(pth, encoding="utf-8"))
            if ref_m not in res or len(res) < 2:
                continue
            ref = res[ref_m]
            rows = [(k, f"{lbl} [{u}]", {m: res[m].get(k) for m in res}) for k, lbl, u in metrics]
            for m in res:
                res[m]["_b"] = _builds(res[m])
            for k, lbl in (("t_build_s", "mesh build time, all builds [s]"),
                           ("n_tri_last", "elements, last build (stator+rotor)"),
                           ("min_angle_deg", "minimum angle, worst half [deg]"),
                           ("ar_p99_9", "aspect p99.9, worst half")):
                rows.append((k, lbl, {m: res[m]["_b"].get(k) for m in res}))
            rows.append(("wall_s", "solve wall time [s]", {m: res[m].get("wall_s") for m in res}))
            rows.append(("maxrss_mb", "peak RSS [MB]", {m: res[m].get("maxrss_mb") for m in res}))
            for k, lbl, vals in rows:
                if all(v is None for v in vals.values()):
                    continue
                v0 = vals.get(ref_m)
                deltas = []
                for m in meshers[1:]:
                    vm = vals.get(m)
                    if k in ("T_ripple_pct", "T_ripple_pct_coulomb", "E_thd_pct") \
                            and v0 is not None and vm is not None:
                        deltas.append(f"{vm - v0:+.3f} pp")
                    elif isinstance(v0, (int, float)) and v0 and isinstance(vm, (int, float)):
                        deltas.append(f"{100.0 * (vm - v0) / abs(v0):+.3f} %")
                    else:
                        deltas.append("")
                    out.setdefault(tag, {}).setdefault(f"{kind}:{k}", {})[m] = vm
                out.setdefault(tag, {}).setdefault(f"{kind}:{k}", {})[ref_m] = v0
                lines.append(f"| {tag} {kind} | {lbl} | "
                             + " | ".join(_fmt(vals.get(m)) for m in meshers) + " | "
                             + " | ".join(deltas) + " |")
            if kind == "load":
                for m in meshers[1:]:
                    if m in res:
                        v = _verdict(ref, res[m])
                        out.setdefault(tag, {})[f"verdict:{m}"] = v
                        lines.append(f"| {tag} {kind} | owner criteria, {m} vs {ref_m} | "
                                     + " | ".join("" for _ in meshers) + " | "
                                     + " | ".join((", ".join(f"{kk} {'pass' if vv else 'FAIL'}"
                                                            for kk, vv in v.items())
                                                  if mm == m else "") for mm in meshers[1:])
                                     + " |")
            for m in res:
                out.setdefault(tag, {})[f"{kind}:mesher:{m}"] = res[m].get("mesher")
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
    p.add_argument("--old-src", default="",
                   help="src/ of a checkout WITH triangle (default: --new-src, "
                        "backend switched by MOTOR_AI_SIM_GEO_CDT)")
    p.add_argument("--old-python", default="",
                   help="python of the venv with triangle (default: --new-python)")
    p.add_argument("--new-src", required=True, help="src/ of this branch")
    p.add_argument("--new-python", default=sys.executable)
    p.add_argument("--config", required=True, help="config/ to COPY per case (read only)")
    p.add_argument("--dies", required=True, help="dies tree (read only; a copy is safest)")
    p.add_argument("--workdir", required=True)
    p.add_argument("--threads", type=int, default=6)
    p.add_argument("--cases", default="", help="comma list of " + ",".join(CASES))
    p.add_argument("--l13-die", default="", help="die holding the L13 to compare")
    p.add_argument("--no-emf", action="store_true", help="skip the no-load runs")
    p.add_argument("--meshers", default="triangle,gmsh",
                   help="comma list of CDT backends (triangle,gmsh,netgen)")
    q = sub.add_parser("report", help="tabulate finished runs")
    q.add_argument("--workdir", required=True)
    q.add_argument("--meshers", default="triangle,gmsh",
                   help="comma list; the first is the reference")
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
