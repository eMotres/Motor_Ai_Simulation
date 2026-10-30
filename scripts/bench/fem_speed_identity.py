"""Per-frame speed-up check: wall time and result identity of one owner run.

Sandbox only (``/opt/motres/compute/sbx/sbx.sh start sN 6 16g``), never the
live API.  One point goes through the real route in-process
(``routes.simulation.get_fem_transient``, fresh, no ledger write) exactly like
``/opt/motres/compute/jobs/run_point.py``; the point comes from a run-ledger
entry's ``key_fields`` (the owner's own run) or from a JSON dict of the
route's arguments.

  # the same point on the base tree and on this branch (PYTHONPATH overlay of
  # the branch's src/ copied into the sandbox), one thread like production:
  python fem_speed_identity.py run  point.json base.json
  PYTHONPATH=/tmp/branch/src python fem_speed_identity.py run point.json new.json
  # the ordering cache alone (same tree, cache off / on):
  python fem_speed_identity.py run point.json off.json SB_PARDISO_PERM_CACHE_MB=0
  # where the time goes (cProfile, top 40 by cumulative time, in the output):
  python fem_speed_identity.py run point.json prof.json PROFILE=1

  python fem_speed_identity.py compare base.json new.json   # deltas + walls

``point.json``: ``{"ledger": "<path to .run_ledger/*.json.gz>"}`` or
``{"params": {...get_fem_transient kwargs...}}``, optionally
``"identity": "<email>"`` (default vadim@motresres.com).
"""
from __future__ import annotations

import gzip
import json
import os
import sys
import time

TOL = 1e-3          # the brief's identity bar: 0.1 %


def _params_from_ledger(path):
    kf = json.load(gzip.open(path))["key_fields"]
    geo = {k: v for k, v in kf["geo_ov"]}
    return dict(
        n_steps_per_period=int(kf["n_steps_per_period"]),
        sampling_purpose=kf["sampling_purpose"], n_periods=kf["n_periods"],
        gamma_deg=float(kf["gamma_deg"]), I_phase_rms=float(kf["I_phase_rms"]),
        rpm=float(kf["rpm"]), connection=kf["winding"][1],
        mesh_size_mm=float(kf["mesh_size_mm"]), min_size_mm=float(kf["min_size_mm"]),
        outer_air_factor=float(kf["outer_air_factor"]), n_sectors=int(kf["n_sectors"]),
        gap_layers=float(kf["gap_layers"]), stator_fillet_mm=kf["stator_fillet_mm"],
        coil_temp_c=kf["coil_temp_c"], magnet_temp_c=float(kf["magnet_temp_c"]),
        end_winding_factor=float(kf["end_winding_factor"]),
        component_mesh=json.dumps(dict(kf["comp_mesh"])), eddy=bool(kf["eddy"]),
        rotor_eddy=bool(kf["rotor_eddy"]), demag=bool(kf["demag"]),
        iron_template=bool(kf["iron_template"]), geo_mesh=bool(kf["geo_mesh"]),
        structured_gap=bool(kf["structured_gap"]), torque_method=kf["torque_method"],
        element_order=int(kf["element_order"]), drive=kf["drive"],
        star_delta=kf["star_delta"], n_frames=int(kf["n_frames"]),
        harm_ref=bool(kf["harm_ref"]), geo=json.dumps(geo), include_frames=True)


def _scalars(r, prefix="", out=None, depth=0):
    """Every finite number in the result, flattened (series reduced to their
    mean / max / min so a waveform change is seen too)."""
    out = {} if out is None else out
    if depth > 3:
        return out
    if isinstance(r, dict):
        for k, v in r.items():
            _scalars(v, f"{prefix}{k}.", out, depth + 1)
    elif isinstance(r, bool) or r is None:
        return out
    elif isinstance(r, (int, float)):
        out[prefix[:-1]] = float(r)
    elif isinstance(r, list) and r and all(
            isinstance(x, (int, float)) and not isinstance(x, bool) for x in r):
        out[prefix + "mean"] = sum(r) / len(r)
        out[prefix + "max"] = max(r)
        out[prefix + "min"] = min(r)
    return out


#: timing / bookkeeping keys: reported, never compared
_NOT_PHYSICS = ("wall", "_s.", "elapsed", "eta", "ts_", "linear_solver",
                "kpw_", "n_frames_solved", "warm", "picard_iters", "seed")


def run(point_path, out_path, env):
    for k, v in env.items():
        os.environ[k] = v
    point = json.load(open(point_path))
    params = (_params_from_ledger(point["ledger"]) if "ledger" in point
              else dict(point["params"]))
    from motor_ai_sim import workspace as W
    from motor_ai_sim.routes import simulation as S
    ws = W.workspace_for_identity(point.get("identity", "vadim@motresres.com"))
    prof = None
    if os.environ.get("PROFILE") == "1":
        import cProfile
        prof = cProfile.Profile()
    with W.use_workspace(ws):
        t0 = time.time()
        if prof:
            prof.enable()
        r = S.get_fem_transient(**params, fresh=True, ledger=False)
        if prof:
            prof.disable()
        wall = time.time() - t0
    if hasattr(r, "body"):
        r = json.loads(r.body)
    res = {"wall_s": wall, "env": env, "scalars": _scalars(r),
           "linear_solver": r.get("linear_solver"),
           "solve_wall_s": r.get("solve_wall_s"),
           "n_frames_solved": r.get("n_frames_solved"),
           "six_phase_probe": (r.get("six_phase") or {}).get("probe")}
    if prof:
        import io
        import pstats
        s = io.StringIO()
        pstats.Stats(prof, stream=s).sort_stats("cumulative").print_stats(40)
        res["profile_cumulative"] = s.getvalue()
        s = io.StringIO()
        pstats.Stats(prof, stream=s).sort_stats("tottime").print_stats(40)
        res["profile_tottime"] = s.getvalue()
    json.dump(res, open(out_path, "w"), indent=1, default=str)
    print("DONE wall %.1f s, solve %s s, frames %s, linear %s" % (
        wall, res["solve_wall_s"], res["n_frames_solved"], res["linear_solver"]))


def compare(a_path, b_path):
    a, b = json.load(open(a_path)), json.load(open(b_path))
    sa, sb = a["scalars"], b["scalars"]
    worst, n_eq, n_cmp, bad = 0.0, 0, 0, []
    for k in sorted(set(sa) & set(sb)):
        if any(t in k for t in _NOT_PHYSICS):
            continue
        x, y = sa[k], sb[k]
        n_cmp += 1
        if x == y:
            n_eq += 1
            continue
        rel = abs(y - x) / max(abs(x), abs(y), 1e-12)
        worst = max(worst, rel)
        if rel > TOL:
            bad.append((k, x, y, rel))
    print("wall  %8.1f s -> %8.1f s  (x%.2f)" % (
        a["wall_s"], b["wall_s"], a["wall_s"] / max(b["wall_s"], 1e-9)))
    print("solve %8s s -> %8s s" % (a.get("solve_wall_s"), b.get("solve_wall_s")))
    print("linear", a.get("linear_solver"), "->", b.get("linear_solver"))
    print("compared %d physics scalars: %d bit-identical, worst rel %.3g" % (
        n_cmp, n_eq, worst))
    for k, x, y, rel in bad[:40]:
        print("  OVER 0.1 %%: %-60s %.6g -> %.6g (%.3g)" % (k, x, y, rel))
    return 0 if not bad else 1


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "run":
        env = dict(kv.split("=", 1) for kv in sys.argv[4:])
        run(sys.argv[2], sys.argv[3], env)
    elif cmd == "compare":
        sys.exit(compare(sys.argv[2], sys.argv[3]))
    else:
        sys.exit(__doc__)
