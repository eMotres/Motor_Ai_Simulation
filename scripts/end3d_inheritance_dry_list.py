"""Dry list: which catalog configuration changes its 3-D (Stage A) flux factor with the stack-length rule of inheritance.

READ ONLY.  Nothing stored is written.  For every ``<die>/<config>.yaml`` under ``--root`` it asks the lookup twice:

* OLD: the newest passport of the same slots / poles / OD, whatever its stack length (what ``_end3d_lookup`` did);
* NEW: ``routes.simulation._end3d_lookup`` as it is now (same stack length within 5 %, the passport's own
  ``k_flux_vs_L`` entry for this length first, else "no 3-D factor").

A configuration with a passport of its own (exact fingerprint) is unchanged by construction and is listed only when
the passport's length table disagrees with its scalar by more than 0.0005 (``charge_analytic`` now reads the scalar,
like every other reader).

    python scripts/end3d_inheritance_dry_list.py --root /srv/motres/shared/dies --store /srv/motres/shared/end_effect_passports.json
    python scripts/end3d_inheritance_dry_list.py --root <dies> --store <store.json> --json out.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from recompute_k_end import _Machine, config_files        # noqa: E402  (same walker, same geometry merge)


def _legacy(store: Dict[str, dict], desc: str):
    cands = [(k, v) for k, v in store.items()
             if isinstance(v, dict) and v.get("machine") == desc and v.get("k_flux") is not None]
    if not cands:
        return None
    _when = lambda v: str(v.get("adopted_utc") or v.get("generated_utc") or "")        # noqa: E731
    return max(cands, key=lambda kv: _when(kv[1]))


def _curve_at(rec: dict, stack: float) -> Optional[float]:
    from motor_ai_sim.routes.simulation import _end3d_stack_at
    k, _ = _end3d_stack_at(rec, stack)
    return k


def run(root: Path, store_path: Path) -> Dict[str, Any]:
    from motor_ai_sim import end3d_store
    from motor_ai_sim.routes import simulation as S
    store = json.loads(store_path.read_text(encoding="utf-8"))
    saved = end3d_store.load
    end3d_store.load = lambda: store                                       # the lookup reads exactly this file
    try:
        return _run(root, store, S)
    finally:
        end3d_store.load = saved


def _run(root: Path, store: Dict[str, dict], S: Any) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    errors: List[str] = []
    n = 0
    for p in config_files(root):
        die, cfg = p.parent.name, p.stem
        try:
            cfg_doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            die_doc = yaml.safe_load((p.parent / "die.yaml").read_text(encoding="utf-8")) or {}
            ov = dict(die_doc.get("geometry") or {})
            ov.update(cfg_doc.get("geometry_overrides") or {})
            fp = S._geometry_fingerprint(ov)
            desc = S._machine_desc(ov)
            stack = S._geo_stack_mm(ov)
        except Exception as exc:                                           # noqa: BLE001
            errors.append(f"{die}/{cfg}: {type(exc).__name__}: {exc}")
            continue
        n += 1
        own = store.get(fp)
        if isinstance(own, dict):
            kt = _curve_at(own, stack)
            if kt is not None and own.get("k_flux") is not None and abs(kt - float(own["k_flux"])) > 5e-4:
                rows.append({"die": die, "config": cfg, "stack_mm": stack, "kind": "own passport, table != scalar (info; scalar is used)",
                             "old": float(own["k_flux"]), "new": float(own["k_flux"]),
                             "table_at_stack": round(kt, 5)})
            continue
        old = _legacy(store, desc) if desc else None
        d: Dict[str, Any] = {}
        new = S._end3d_lookup(fp, ov, diag=d)
        o = float(old[1]["k_flux"]) if old else None
        nw = float(new["k_flux"]) if new else None
        if o == nw:
            continue
        rows.append({"die": die, "config": cfg, "stack_mm": stack, "machine": desc,
                     "kind": "inherited -> changed" if new else "inherited -> no 3-D factor",
                     "old": o, "old_from": old[0] if old else None,
                     "old_passport_stack_mm": (old[1].get("stack_mm") if old else None),
                     "new": nw, "new_from": new.get("inherited_from") if new else None,
                     "new_basis": new.get("inherited_stack_basis") if new else d.get("note")})
    return {"configs_checked": n, "changed": rows, "errors": errors}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--store", required=True, type=Path)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args(argv)
    rep = run(a.root, a.store)
    print(f"{rep['configs_checked']} configurations checked, {len(rep['changed'])} listed, {len(rep['errors'])} errors")
    for r in rep["changed"]:
        f = lambda v: "-" if v is None else f"{v:.5f}"                     # noqa: E731
        print(f"{r['die']}/{r['config']}  L={r['stack_mm']:g}  {r['kind']}:  {f(r['old'])} -> {f(r['new'])}"
              + (f"  [{r.get('new_basis')}]" if r.get("new_basis") else ""))
    for e in rep["errors"]:
        print("ERROR", e)
    if a.json:
        a.json.write_text(json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
