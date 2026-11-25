#!/usr/bin/env python3
"""Stage A (3-D end-effect) of ONE catalogue machine as a queued job, for machines the catalogue child cannot finish
inside its 7200 s timeout (``motor_ai_sim.end3d_cost``: the passport then ships 2-D with "3-D passport: run as a job").

The app does not submit jobs.  The recipe (us1 agent jobs, ``motres-agent-job``, background class):

    mkdir -p ~/jobs/<tag> && sudo motres-agent-job prep <tag>
    sudo motres-agent-catalog get <tag> "<die>" "<configuration>"          # read-only copy into ~/jobs/<tag>/bundle
    cp scripts/stage_a_job.py ~/jobs/<tag>/
    # run.sh:  cd /job && python -u stage_a_job.py --die-dir "/job/bundle/<die>" --config "<configuration>" \\
    #              --out /job/stage_a.json --estimate-only   (first, seconds)  then without --estimate-only
    # job.json: schema 1, kind study, the exact die / configuration names
    sudo motres-agent-job submit <tag> 4 <mem_gb> app background
    # memory: measured 9.1 GB quick 478k dofs, 18.9 GB default 708-746k, 22.7 GB 861k: --estimate-only prints dofs

The result (``--out``, the run_stage_a passport) is adopted by an admin through the EXISTING route
``POST /api/static3d/passport/adopt`` with body ``{"stage_a": <the JSON>, "note": "<job tag>"}``; adopt_entry stores the
applied factor, the per-length table and the 2-D check status.  Delete the bundle afterwards.

Fidelity: ``--fidelity quick`` = the catalogue's (n_stack 4, n_cap 6, max_iter 45); ``default`` = n_stack 8, n_cap 10,
max_iter 60 (final passports).  Stack lengths: ``l_factors='auto'`` plus the catalogue's 0.75 row.  Payload only: the
script reads the bundle and writes ``--out``; it touches no store.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

FIDELITY = {"quick": dict(n_stack=4, n_cap=6, max_iter=45), "default": dict(n_stack=8, n_cap=10, max_iter=60)}


def _machine(die_dir: Path, config: str):
    import yaml
    from motor_ai_sim.passport_v1.snapshot import resolve_geometry
    die = yaml.safe_load((die_dir / "die.yaml").read_text(encoding="utf-8"))
    cfg = yaml.safe_load((die_dir / ("%s.yaml" % config)).read_text(encoding="utf-8"))
    g = resolve_geometry(die, cfg)
    cm = cfg.get("materials") or {}
    mats = {k: cm[k] for k in ("stator_core", "rotor_core", "magnet", "shaft") if cm.get(k)}
    return g, mats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--die-dir", type=Path, help="a catalogue bundle folder holding die.yaml and <config>.yaml")
    src.add_argument("--machine-json", type=Path, help='{"geometry": {...}, "materials": {...}} (the catalogue child\'s file)')
    ap.add_argument("--config", help="configuration name (with --die-dir)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--fidelity", choices=sorted(FIDELITY), default="quick")
    ap.add_argument("--estimate-only", action="store_true", help="print the end3d_cost estimate and stop")
    a = ap.parse_args(argv)
    if a.die_dir and not a.config:
        ap.error("--die-dir needs --config")
    if a.die_dir:
        g, mats = _machine(a.die_dir, a.config)
    else:
        m = json.loads(a.machine_json.read_text(encoding="utf-8"))
        g, mats = dict(m.get("geometry") or {}), dict(m.get("materials") or {})
    if mats:
        from motor_ai_sim.material_context import set_request_materials
        set_request_materials({"assignment": mats, "materials": {}})
    fid = FIDELITY[a.fidelity]
    from motor_ai_sim.end3d_cost import preflight
    est = preflight(geo_override=g, n_stack=fid["n_stack"], n_cap=fid["n_cap"], auto_extra=(0.75,),
                    timeout_s=float("inf"))
    print("ESTIMATE " + json.dumps(est, default=float), flush=True)
    if a.estimate_only:
        return 0
    from motor_ai_sim.simulation.static3d.end_effect import run_stage_a, write_passport
    t0 = time.time()
    p = run_stage_a(geo_override=g, order=2, l_factors="auto", auto_extra=(0.75,), do_bracket=False, do_2d=True,
                    tol=3e-3, verbose=True, **fid)
    p["job"] = {"fidelity": a.fidelity, "wall_s": round(time.time() - t0, 1), "estimate": est,
                "materials_pinned": mats}
    write_passport(p, str(a.out))
    print("RESULT " + json.dumps({"k_flux": p["k_flux"], "k_flux_self": p["k_flux_self"],
                                  "verdict": (p.get("two_d") or {}).get("verdict"),
                                  "k_flux_status": (p.get("two_d") or {}).get("k_flux_status"),
                                  "wall_s": p["job"]["wall_s"]}, default=float), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
