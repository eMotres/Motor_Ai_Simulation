"""Dry list: what the 3-D (Stage A) factors of every catalog configuration do between the BASE lookup and this PR's lookup.

READ ONLY.  Nothing stored is written.  Both sides are the real ``routes.simulation._end3d_lookup`` imported from a
source tree, never a re-implementation:

* OLD = the lookup of the BASE checkout (``--base-src``);
* NEW = the lookup of the PR checkout (``--pr-src``).

Each side runs in its own subprocess (``PYTHONPATH=<src>``) over every ``<die>/<config>.yaml`` under ``--root`` with the
passport store ``--store``.  The geometry given to the lookup is the MERGED one (config geometry + die + configuration
overrides, ``merge_geo_override``), exactly what the application passes; the machine class ("24s/28p OD 200"), the stack
length and the fingerprint come from it.  (An earlier version of this script passed the raw die geometry: the machine
class then came from the live config for every configuration and nothing was ever listed.)

Per configuration it prints the k_flux and the k_T of both sides and their difference, and the borrowed passport (key,
machine, its stack, length ratio, basis).  A configuration is LISTED when k_flux or k_T differs by more than
``--threshold`` (default 0.0005 absolute) or one side has a factor and the other none; ``--all`` prints every one.  The
passports of the store are listed first.  ``k_T`` comes from the source tree's own ``config/stage_d_kt.json`` (the job's
``/app/config`` on EU): the torque factor the lookup attaches.

Command (EU throw-away container; both paths are the checkouts as they exist there):

    python scripts/end3d_inheritance_dry_list.py \\
        --root /srv/motres/shared/dies --store /srv/motres/shared/end_effect_passports.json \\
        --base-src /base/src --pr-src /pr/src [--all] [--threshold 0.0005] [--json out.json]

Defaults: ``--pr-src`` = this checkout's ``src`` (the folder above ``scripts``); ``--base-src`` is required.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

THIS_SRC = str(Path(__file__).resolve().parents[1] / "src")


def config_files(root: Path):
    pats = ["shared/dies/*/*.yaml", "published/*/*/*.yaml", "workspaces/*/dies/*/*.yaml", "*/*.yaml"]
    seen = set()
    for pat in pats:
        for p in sorted(root.glob(pat)):
            if p.name == "die.yaml" or p.name.startswith(".") or p in seen:
                continue
            if not (p.parent / "die.yaml").is_file():
                continue
            seen.add(p)
            yield p


def _worker(root: Path, store_path: Path) -> Dict[str, Any]:
    """Run INSIDE one source tree: the lookup of this tree for every configuration."""
    import inspect

    import yaml
    from motor_ai_sim import end3d_store
    from motor_ai_sim.config import get_config
    from motor_ai_sim.routes import simulation as S
    from motor_ai_sim.simulation.geometry_2d import merge_geo_override

    store = json.loads(store_path.read_text(encoding="utf-8"))
    end3d_store.load = lambda: store                                      # the lookup reads exactly this file
    takes_diag = "diag" in inspect.signature(S._end3d_lookup).parameters
    out: List[Dict[str, Any]] = []
    errors: List[str] = []
    for p in config_files(root):
        die, cfg = p.parent.name, p.stem
        try:
            cfg_doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            die_doc = yaml.safe_load((p.parent / "die.yaml").read_text(encoding="utf-8")) or {}
            ov = dict(die_doc.get("geometry") or {})
            ov.update(cfg_doc.get("geometry_overrides") or {})
            geo = merge_geo_override(dict(get_config().get("geometry", {}) or {}), ov)
            fp = S._geometry_fingerprint(geo)
            stack = float(geo.get("motor_length") or 0.0)
            d: Dict[str, Any] = {}
            r = S._end3d_lookup(fp, geo, diag=d) if takes_diag else S._end3d_lookup(fp, geo)
            row: Dict[str, Any] = {"die": die, "config": cfg, "stack_mm": stack, "own": fp in store,
                                   "machine": S._machine_desc(geo), "note": d.get("note")}
            if r:
                src = store.get(r.get("inherited_from")) if r.get("inherited") else store.get(fp)
                row.update(k_flux=r.get("k_flux"), k_T=r.get("k_T"), k_T_inherited=bool(r.get("k_T_inherited")),
                           inherited=bool(r.get("inherited")), basis=r.get("inherited_stack_basis"),
                           from_key=(r.get("inherited_from") or fp)[:12],
                           from_stack_mm=(src or {}).get("stack_mm"))
            out.append(row)
        except Exception as exc:                                          # noqa: BLE001
            errors.append(f"{die}/{cfg}: {type(exc).__name__}: {exc}")
    return {"rows": out, "errors": errors}


def _run_side(src: str, root: Path, store: Path) -> Dict[str, Any]:
    env = dict(os.environ)
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    cp = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", "--root", str(root),
                         "--store", str(store)], capture_output=True, text=True, env=env, cwd=src)
    if cp.returncode != 0:
        raise SystemExit(f"worker for {src} failed:\n{cp.stderr[-1500:]}")
    return json.loads(cp.stdout.split("WORKER_JSON:", 1)[1])


def _d(a: Optional[float], b: Optional[float]) -> Optional[float]:
    return None if a is None or b is None else float(b) - float(a)


def compare(old: Dict[str, Any], new: Dict[str, Any], threshold: float) -> List[Dict[str, Any]]:
    by_old = {(r["die"], r["config"]): r for r in old["rows"]}
    rows = []
    for n in new["rows"]:
        o = by_old.get((n["die"], n["config"]), {})
        dk, dt = _d(o.get("k_flux"), n.get("k_flux")), _d(o.get("k_T"), n.get("k_T"))
        presence = (o.get("k_flux") is None) != (n.get("k_flux") is None) or (o.get("k_T") is None) != (n.get("k_T") is None)
        changed = presence or (dk is not None and abs(dk) > threshold) or (dt is not None and abs(dt) > threshold)
        ps = n.get("from_stack_mm")
        rows.append({"die": n["die"], "config": n["config"], "machine": n.get("machine"), "stack_mm": n["stack_mm"],
                     "own_passport": n["own"], "changed": bool(changed),
                     "k_flux_old": o.get("k_flux"), "k_flux_new": n.get("k_flux"), "k_flux_diff": dk,
                     "k_T_old": o.get("k_T"), "k_T_new": n.get("k_T"), "k_T_diff": dt,
                     "old_from": o.get("from_key"), "old_from_stack_mm": o.get("from_stack_mm"),
                     "new_from": n.get("from_key"), "new_from_stack_mm": ps, "new_basis": n.get("basis"),
                     "length_ratio": (n["stack_mm"] / float(ps)) if ps else None, "new_note": n.get("note")})
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--store", required=True, type=Path)
    ap.add_argument("--base-src", help="src folder of a checkout of the BASE (required)")
    ap.add_argument("--pr-src", default=THIS_SRC, help="src folder of the PR checkout (default: this checkout)")
    ap.add_argument("--threshold", type=float, default=0.0005, help="absolute k_flux / k_T difference that lists a row")
    ap.add_argument("--all", action="store_true", help="print every configuration, not only the changed ones")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.worker:
        print("WORKER_JSON:" + json.dumps(_worker(a.root, a.store), default=float))
        return 0
    if not a.base_src:
        ap.error("--base-src is required")
    old, new = _run_side(a.base_src, a.root, a.store), _run_side(a.pr_src, a.root, a.store)
    rows = compare(old, new, a.threshold)
    store = json.loads(a.store.read_text(encoding="utf-8"))
    print(f"passports in the store ({len(store)}):")
    for k, v in sorted(store.items(), key=lambda kv: (str(kv[1].get("machine")), float(kv[1].get("stack_mm") or 0))):
        print(f"  {k[:12]}  {v.get('machine')}  stack {v.get('stack_mm')}  k_flux {v.get('k_flux')}  "
              f"rows {len(v.get('k_flux_vs_L') or {})}  method {v.get('k_flux_vs_L_method') or '-'}")
    errors = old["errors"] + new["errors"]
    shown = [r for r in rows if a.all or r["changed"]]
    print(f"\n{len(rows)} configurations checked, {sum(r['changed'] for r in rows)} changed "
          f"(|diff| > {a.threshold:g} in k_flux or k_T, or a factor appears / disappears), {len(errors)} errors")
    f = lambda v: "-" if v is None else f"{v:.5f}"                        # noqa: E731
    for r in shown:
        print(f"{'*' if r['changed'] else ' '} {r['die']}/{r['config']}  {r['machine']}  L={r['stack_mm']:g}"
              f"{'  own passport' if r['own_passport'] else ''}\n"
              f"    k_flux {f(r['k_flux_old'])} -> {f(r['k_flux_new'])} (diff {f(r['k_flux_diff'])});"
              f"  k_T {f(r['k_T_old'])} -> {f(r['k_T_new'])} (diff {f(r['k_T_diff'])})\n"
              f"    old from {r['old_from']} (L {r['old_from_stack_mm']});  new from {r['new_from']} (L {r['new_from_stack_mm']}, "
              f"ratio {f(r['length_ratio'])}, basis {r['new_basis']})" + (f"\n    note: {r['new_note']}" if r["new_note"] else ""))
    for e in errors:
        print("ERROR", e)
    if a.json:
        a.json.write_text(json.dumps({"configs_checked": len(rows), "threshold": a.threshold, "rows": rows,
                                      "errors": errors}, indent=1, ensure_ascii=False, default=float), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
