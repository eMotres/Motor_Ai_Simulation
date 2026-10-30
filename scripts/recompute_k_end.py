"""Stored k_end INPUTS of every configuration -> the k_end of the one coil rule (``coil_build.coil_rule``).

Owner 2026-10-30: k_end is the mean turn of the BUILT coil (``masses.end_winding_factor``, ``coil_build``), recomputed on every
geometry change - "пересчитай теперь везде по всем моторам", "карточки не меняй только".  The code change makes every NEW solve use
it; THIS script refreshes the k_end values a configuration and its duties STORE as inputs for their next run.

WHAT IT WALKS (``--root /srv/motres``)::

    shared/dies/<die>/<cfg>.yaml               catalog configurations
    published/<ws>/<die>/<cfg>.yaml            published copies
    workspaces/<ws>/dies/<die>/<cfg>.yaml      workspace copies

(a root that itself holds ``<die>/die.yaml`` folders - a plain dies directory - is walked as such).

WHAT IT MAY WRITE, and nothing else:

    <cfg>.yaml  end_winding_factor                     the configuration's stored k_end (only where one is stored)
    <cfg>.yaml  duties[i].mesh.sim.endWinding          the duty's saved Simulation setting (its next run's input)
    <cfg>.yaml  duties[i].mesh.sim.endWindingGeo       the geometry value shown beside it

WHAT IT NEVER TOUCHES (the cards): duty results and summaries (``duties[i].result`` / ``summary``), stored runs and their settings
(``duties[i].runs``), run payload files, ``motor_catalog.json``, passports, datasheets, frozen product revisions (reported only), the
live ``workspaces/<ws>/motor_config.yaml`` (the Simulation tab re-derives it on load).  Every write is checked: the re-read document
must differ from the original ONLY at the paths above, or the file is not written.

The report gives, per configuration and duty: old -> new k_end, the coil the new value came from (bend radius, straight overhang,
leads, the reference profile or the template rules) and the shift a rerun would make: R_phase and the DC copper loss scale by
new/old (P_cu,dc = rho*J^2*V_active*k_end); the in-slot AC part does not move.  ``est dP_cu`` is that ratio on the duty's stored
copper loss - an upper bound, as if all of it were DC.

DRY RUN BY DEFAULT.  ``--apply`` writes, each changed file first copied to ``<file>.bak-kend-coil-<UTC stamp>``.

    python scripts/recompute_k_end.py --root /srv/motres
    python scripts/recompute_k_end.py --root /srv/motres --json /tmp/kend.json
    python scripts/recompute_k_end.py --root /srv/motres --apply
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

#: duty settings keys this script may write (under duties[i].mesh)
DUTY_KEYS = ("sim.endWinding", "sim.endWindingGeo")
#: k_end is stored to 3 decimals by the Simulation tab
NDIG = 3


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _frozen(root: Path) -> Set[Tuple[str, str]]:
    """(die, config) of every FROZEN product revision (motor_revisions): ``<root>/shared/motor_revisions`` for a data tree, the
    ``motor_revisions`` beside it for a plain ``shared/dies`` directory."""
    out: Set[Tuple[str, str]] = set()
    doc = None
    for p in (root / "shared" / "motor_revisions" / "revisions.json", root.parent / "motor_revisions" / "revisions.json"):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            break
        except Exception:                                   # noqa: BLE001
            continue
    if not isinstance(doc, dict):
        return out
    for lin in (doc.get("lineages") or {}).values():
        for rev in (lin or {}).get("revisions") or []:
            if (rev or {}).get("frozen"):
                out.add((str(rev.get("die")), str(rev.get("config"))))
    return out


def config_files(root: Path) -> Iterator[Path]:
    pats = ["shared/dies/*/*.yaml", "published/*/*/*.yaml", "workspaces/*/dies/*/*.yaml", "*/*.yaml"]
    seen: Set[Path] = set()
    for pat in pats:
        for p in sorted(root.glob(pat)):
            if p.name == "die.yaml" or p.name.startswith(".") or p in seen:
                continue
            if not (p.parent / "die.yaml").is_file():
                continue
            seen.add(p)
            yield p


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _diff_paths(a: Any, b: Any, path: str = "") -> List[str]:
    """Every path where two loaded YAML documents differ."""
    if isinstance(a, dict) and isinstance(b, dict):
        out: List[str] = []
        for k in set(a) | set(b):
            if k not in a or k not in b:
                out.append("%s/%s" % (path, k))
            else:
                out += _diff_paths(a[k], b[k], "%s/%s" % (path, k))
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [path]
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += _diff_paths(x, y, "%s[%d]" % (path, i))
        return out
    return [] if a == b else [path]


def allowed_path(path: str) -> bool:
    if path == "/end_winding_factor":
        return True
    parts = path.split("/")
    # /duties[i]/mesh/<key>
    return (len(parts) == 4 and parts[1].startswith("duties[") and parts[2] == "mesh" and parts[3] in DUTY_KEYS)


class _Machine:
    """p (the solver's params) and the merged geometry dict of one die/config - the inputs masses.end_winding_factor reads."""

    def __init__(self, die_doc: dict, cfg_doc: dict):
        from motor_ai_sim.config import get_config
        from motor_ai_sim.simulation.geometry_2d import merge_geo_override, params_from_config
        ov = dict(die_doc.get("geometry") or {})
        ov.update(cfg_doc.get("geometry_overrides") or {})
        self.override = ov
        self.geo = merge_geo_override(dict(get_config().get("geometry", {}) or {}), ov)
        self.p = params_from_config(geo_override=ov)


def compute(die: str, cfg: str, die_doc: dict, cfg_doc: dict) -> Dict[str, Any]:
    from motor_ai_sim.masses import cad_coil_dimensions, end_winding_factor
    m = _Machine(die_doc, cfg_doc)
    ovr = cfg_doc.get("cad_overrides") if isinstance(cfg_doc.get("cad_overrides"), dict) else {}
    k = end_winding_factor(m.p, m.geo, die=die, config=cfg, cad_overrides=ovr)
    d = cad_coil_dimensions(m.p, m.geo, die=die, config=cfg, cad_overrides=ovr)
    return {
        "k_end": round(float(k), NDIG),
        "k_end_semicircle": round(float(d["k_end_semicircle"]), NDIG),
        # what a die without its own CAD profile was billed at until 2026-10-30 (comparison only)
        "k_end_template": round(float(d["k_end_template"]), NDIG),
        "kind": d.get("coil_kind"),
        "stack_mm": round(float(d["stack_length_mm"]), 3),
        "turns": int(d["turns_per_coil"]),
        "wire_beng_r_mm": round(float(d["wire_beng_r_mm"]), 4),
        "coil_straight_extra_mm": round(float(d["coil_straight_extra_mm"]), 4),
        "coil_lead_total_mm": round(float(d["coil_lead_total_mm"]), 4),
        "mean_turn_mm": round(float(d["mean_turn_length_mm"]), 3),
        "source": d.get("coil_source"),
        "profile": d.get("coil_profile"),
    }


def plan_config(die: str, cfg: str, die_doc: dict, cfg_doc: dict) -> Tuple[dict, List[dict], Dict[str, Any]]:
    """(new document, rows of old -> new, the coil) - the document is a deep copy; only allowed paths change."""
    coil = compute(die, cfg, die_doc, cfg_doc)
    k_new = coil["k_end"]
    new = copy.deepcopy(cfg_doc)
    rows: List[dict] = []
    old_c = _num(cfg_doc.get("end_winding_factor"))
    row = {"level": "config", "duty": None, "key": "end_winding_factor", "old": old_c, "new": k_new}
    if old_c is None:
        row.update(new=None, note="not stored (derived at run time: semicircle %.3f before, built coil %.3f now)"
                   % (coil["k_end_semicircle"], k_new))
    else:
        row["ratio"] = round(k_new / old_c, 4) if old_c > 0 else None
        if abs(old_c - k_new) > 0.5 * 10 ** -NDIG:
            new["end_winding_factor"] = k_new
    rows.append(row)
    for i, du in enumerate(cfg_doc.get("duties") or []):
        if not isinstance(du, dict):
            continue
        mesh = du.get("mesh")
        name = du.get("name")
        if not isinstance(mesh, dict) or not any(k in mesh for k in DUTY_KEYS):
            rows.append({"level": "duty", "duty": name, "key": "sim.endWinding", "old": None, "new": None,
                         "note": "no stored k_end setting (the Simulation tab derives it from the coil)"})
            continue
        old_d = _num(mesh.get("sim.endWinding"))
        r = {"level": "duty", "duty": name, "key": "sim.endWinding", "old": old_d, "new": k_new,
             "ratio": round(k_new / old_d, 4) if old_d else None}
        p_str = _num((du.get("result") or {}).get("p_stranded_w"))
        if p_str is not None and old_d:
            r["p_stranded_w"] = p_str
            r["est_dP_cu_w_max"] = round(p_str * (k_new / old_d - 1.0), 1)
        rows.append(r)
        for key in DUTY_KEYS:
            if key in mesh and (_num(mesh[key]) is None or abs(_num(mesh[key]) - k_new) > 0.5 * 10 ** -NDIG):
                new["duties"][i]["mesh"][key] = k_new
    return new, rows, coil


def _backup(p: Path, stamp: str) -> Path:
    b = p.with_name(f"{p.name}.bak-kend-coil-{stamp}")
    shutil.copy2(p, b)
    return b


def run(root: Path, apply: bool = False, only_die: Optional[str] = None) -> Dict[str, Any]:
    stamp = _stamp()
    frozen = _frozen(root)
    report: Dict[str, Any] = {"root": str(root), "applied": bool(apply), "configs": [], "skipped_frozen": [], "errors": [],
                              "written": []}
    for p in config_files(root):
        die, cfg = p.parent.name, p.stem
        if only_die and die != only_die:
            continue
        try:
            raw = p.read_bytes()
            cfg_doc = yaml.safe_load(raw.decode("utf-8")) or {}
            die_doc = yaml.safe_load((p.parent / "die.yaml").read_text(encoding="utf-8")) or {}
        except Exception as exc:                            # noqa: BLE001
            report["errors"].append(f"{p}: unreadable ({exc})")
            continue
        if not isinstance(cfg_doc, dict) or not isinstance(die_doc, dict):
            continue
        try:
            new, rows, coil = plan_config(die, cfg, die_doc, cfg_doc)
        except Exception as exc:                            # noqa: BLE001
            report["errors"].append(f"{p}: k_end not computed ({type(exc).__name__}: {exc})")
            continue
        changed = _diff_paths(cfg_doc, new)
        entry = {"file": str(p), "die": die, "config": cfg, "coil": coil, "rows": rows, "changes": sorted(changed)}
        if (die, cfg) in frozen:
            report["skipped_frozen"].append(entry)
            continue
        report["configs"].append(entry)
        if not changed or not apply:
            continue
        bad = [c for c in changed if not allowed_path(c)]
        if bad:
            report["errors"].append(f"{p}: refused, would change {bad}")
            continue
        out = yaml.safe_dump(new, sort_keys=False, allow_unicode=True)
        back = yaml.safe_load(out)
        bad = [c for c in _diff_paths(cfg_doc, back) if not allowed_path(c)]
        if bad:                                             # the dump itself must not move anything else
            report["errors"].append(f"{p}: refused, the rewritten file would differ at {bad}")
            continue
        entry["backup"] = str(_backup(p, stamp))
        nl = b"\r\n" if b"\r\n" in raw else b"\n"          # keep the file's own line endings
        p.write_bytes(out.encode("utf-8").replace(b"\n", nl))
        report["written"].append(str(p))
    return report


def _fmt(v: Any) -> str:
    return "-" if v is None else ("%.3f" % v if isinstance(v, float) else str(v))


def print_report(rep: Dict[str, Any]) -> None:
    mode = "APPLIED" if rep["applied"] else "DRY RUN (nothing written; --apply to write)"
    print(f"== recompute_k_end: {mode}, root {rep['root']}")
    for e in rep["configs"] + [dict(x, frozen=True) for x in rep["skipped_frozen"]]:
        c = e["coil"]
        print(f"\n{e['die']} / {e['config']}{'   [FROZEN revision: left as is]' if e.get('frozen') else ''}   ({e['file']})")
        print(f"   coil: L {c['stack_mm']:g} mm, {c['turns']} turns, bend R {c['wire_beng_r_mm']:g}, overhang "
              f"{c['coil_straight_extra_mm']:g}, leads {c['coil_lead_total_mm']:g} mm, mean turn {c['mean_turn_mm']:g} mm"
              f" -> k_end {c['k_end']:.3f} (CIANO14 40 template rules {c['k_end_template']:.3f}, semicircle "
              f"{c['k_end_semicircle']:.3f}); {c['source']}")
        for r in e["rows"]:
            where = "config" if r["level"] == "config" else f"duty {r['duty']}"
            line = f"   {where:<28} {r['key']:<20} {_fmt(r['old'])} -> {_fmt(r['new'])}"
            if r.get("ratio"):
                line += f"   R, P_cu,dc x{r['ratio']:.4f}"
            if r.get("est_dP_cu_w_max") is not None:
                line += f"   est dP_cu <= {r['est_dP_cu_w_max']:+.1f} W of {r['p_stranded_w']:g} W"
            if r.get("note"):
                line += f"   ({r['note']})"
            print(line)
    if rep["written"]:
        print(f"\nwritten {len(rep['written'])} file(s); backups *.bak-kend-coil-*")
    if rep["errors"]:
        print("\nerrors:")
        for e in rep["errors"]:
            print(f"   {e}")
    n_cfg = len(rep["configs"])
    n_chg = sum(1 for e in rep["configs"] if e["changes"])
    print(f"\n{n_cfg} configuration(s), {n_chg} with stored k_end inputs to refresh, {len(rep['skipped_frozen'])} frozen, "
          f"{len(rep['errors'])} error(s)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True, help="data tree root (/srv/motres) or a plain dies directory")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--die", help="only this die")
    ap.add_argument("--json", help="also write the full report as JSON here")
    a = ap.parse_args(argv)
    rep = run(Path(a.root), apply=a.apply, only_die=a.die)
    print_report(rep)
    if a.json:
        Path(a.json).write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
    return 1 if rep["errors"] and a.apply else 0


if __name__ == "__main__":
    raise SystemExit(main())
