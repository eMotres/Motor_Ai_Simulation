"""Ø40 passport "fill" (2026-10-05): the 3-D k(L) tables over 6–30 mm and the
GaN 100 kHz FEM variants folded into the L12 / L20 records, the HTML cards and
the passport store.

    python scripts/passport_d40_fill.py --work <dir with out/L12, out/L20, out/full>
        [--records-dir docs/data/passport_pilot_d40/full]
        [--html-dir <Downloads/...>] [--store]

Post-processing only (no FEM).  The runs it reads (``out/full``):

* ``stage_a_L<L>.json`` — 3-D magnetostatic Stage A at stack length L, each its
  own cold run with the 2-D leg (``passport_full_d40.py stage_a --length L``):
  k_ψ(L) = k_flux = (axial mean of the gap fundamental) / (2-D fundamental).
* ``stage_d_L<L>.json`` — 3-D loaded torque factor (``stage_d_kt_passport.py
  --shifts -1,0,1``, co-energy virtual work at constant current vs the matched
  2-D leg, rated current and angle): k_T(L).
* ``pwm_<M>_<point>_<device>_100k_20ns.json`` — the GaN 100 kHz PWM FEM
  (picked up by the record builder as the ``gan100_20ns`` class; the 100 kHz
  variants then read FEM instead of the 48 kHz-scaled estimate).

k_L(L): not re-measured — the stored Stage B long-stack law L = a·L + b
(earlier geometry), labelled inherited.

The generic builder (``passport_v1.fullcard``) is used as it is; only its
stage-2 block is supplied from the k(L) table here (the generic pipeline is
being generalised on another branch and is not edited from this script).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from motor_ai_sim.passport_v1 import fullcard as FC  # noqa: E402
from motor_ai_sim.passport_v1 import html as Hh  # noqa: E402

PEND = FC.PEND
LENGTHS = (6.0, 12.0, 16.0, 20.0, 24.0, 30.0)


def _load(p: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def k_table(full: Path) -> Dict[str, Any]:
    """{rows: [{L, k_psi, k_flux_self, B1_mid_T, two_d_verdict, k_T, k_T_spread_pct,
    k_L, sources}], law: {...}} over every length that has a run."""
    ee = _load(ROOT / "config" / "end_effect_3d.json") or {}
    sb = ee.get("stage_b") or {}
    lsf = ((sb.get("long_stack_honesty_test") or {}).get("the_inductive_end_effect_factor") or {})
    a, b = lsf.get("fit_a_uH_per_mm"), lsf.get("fit_b_uH")
    kT12 = (sb.get("torque") or {}).get("k_T")
    rows: List[Dict[str, Any]] = []
    Ls = sorted({float(p.stem.split("_L")[-1]) for p in full.glob("stage_a_L*.json")}
                | {float(p.stem.split("_L")[-1]) for p in full.glob("stage_d_L*.json")})
    for L in Ls:
        sa = _load(full / ("stage_a_L%g.json" % L)) or {}
        sd = _load(full / ("stage_d_L%g.json" % L)) or {}
        two = sa.get("two_d") or {}
        r = {"stack_mm": L,
             "k_psi": sa.get("k_flux"), "k_flux_self": sa.get("k_flux_self"),
             "B1_mid_T": (sa.get("spill_profile") or {}).get("B1_mid_T"),
             "B1_2d_T": two.get("B1_T"), "two_d_verdict": two.get("verdict"),
             "picard_converged": ((sa.get("solves") or [{}])[0]).get("picard_converged"),
             "stage_a_wall_s": sa.get("total_wall_s"),
             "k_T": sd.get("k_T"), "k_T_spread_pct": sd.get("k_T_spread_pct"),
             "k_T_dm_total": sd.get("k_T_quoted_dm_total"),
             "k_T_error": sd.get("k_T_error"),
             "stage_d_s_per_position": sd.get("s_per_position"),
             "k_L": ((a * L + b) / (a * L)) if (a and b) else None}
        r["source"] = ("Stage A L=%g mm (own run, 2-D leg %s)" % (L, r["two_d_verdict"])
                       if sa else "no Stage A run") + \
            ("; Stage D k_T (co-energy, ±1 shift)" if sd.get("k_T") else
             ("; Stage D FAILED: %s" % sd.get("k_T_error") if sd else "; no Stage D run"))
        rows.append(r)
    return {"rows": rows, "k_L_law": {"a_uH_per_mm": a, "b_uH": b,
                                       "source": "Stage B long-stack test, earlier geometry "
                                                 "(config/end_effect_3d.json) — inherited"},
            "k_T_inherited_12mm": kT12}


def _at(rows, key, L) -> Optional[float]:
    pts = sorted((r["stack_mm"], r[key]) for r in rows if r.get(key) is not None)
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return float(np.interp(L, xs, ys))


def make_stage2(tab: Dict[str, Any]):
    rows = tab["rows"]

    def stage2_block(full_dir: Path, repo: Path, L_mm: float) -> Dict[str, Any]:
        kpsi, kT, kL = _at(rows, "k_psi", L_mm), _at(rows, "k_T", L_mm), _at(rows, "k_L", L_mm)
        exact = {k: any(abs(r["stack_mm"] - L_mm) < 1e-6 and r.get(k) is not None for r in rows)
                 for k in ("k_psi", "k_T")}
        if kT is None and tab.get("k_T_inherited_12mm"):
            kT = 1.0 - (1.0 - float(tab["k_T_inherited_12mm"])) * 12.0 / float(L_mm)
            kT_method = "INHERITED Stage B 12 mm, end-region law (no Stage D run)"
            kT_labels = ["3-D (inherited)", "older geometry", PEND]
        else:
            kT_method = ("3-D Stage D: co-energy virtual work at constant current (rotor "
                         "shifts −1/0/+1, dm_total 2) / the matched 2-D leg on the same "
                         "cross-section, rated current and angle; %s"
                         % ("at this stack" if exact["k_T"] else "interpolated in L"))
            kT_labels = ["3-D", "loaded", "rated point"]
        return {
            "L_mm": L_mm,
            "k_psi_curve": [{"stack_mm": r["stack_mm"], "k_psi": r["k_psi"],
                             "k_flux_self": r["k_flux_self"], "B1_mid_T": r["B1_mid_T"],
                             "picard_converged": r["picard_converged"],
                             "source": r["source"]} for r in rows],
            "k_psi": {"value": kpsi,
                      "method": "3-D magnetostatic Stage A (I = 0): axial mean of the gap "
                                "fundamental / the 2-D fundamental (own run per length, "
                                "n_stack 4); %s" % ("at this stack" if exact["k_psi"]
                                                    else "interpolated in L"),
                      "labels": ["3-D", "no-load", "quick fidelity"]},
            "k_T": {"value": kT, "method": kT_method, "labels": kT_labels},
            "k_L": {"value": kL, "fit_a_uH_per_mm": tab["k_L_law"]["a_uH_per_mm"],
                    "fit_b_uH": tab["k_L_law"]["b_uH"],
                    "method": "Stage B long-stack law L(L) = a·L + b, k_L = 1 + b/(a·L)",
                    "labels": ["3-D (inherited)", "older geometry"]},
            "k_vs_L": rows,
            "magnet_segmentation": {
                "note": "magnets are one axial piece; rotor/magnet eddy loss is a 2-D value "
                        "(no axial return path) — an upper bound; segmentation not modelled",
                "labels": ["2-D", "upper bound"]},
        }
    return stage2_block


def end3d_block(tab: Dict[str, Any], L0: float, geo_sig: Optional[str]) -> Dict[str, Any]:
    rows = tab["rows"]
    kf = {("%g" % r["stack_mm"]): round(float(r["k_psi"]), 5) for r in rows if r.get("k_psi")}
    kt = {("%g" % r["stack_mm"]): round(float(r["k_T"]), 5) for r in rows if r.get("k_T")}
    kl = {("%g" % r["stack_mm"]): round(float(r["k_L"]), 5) for r in rows if r.get("k_L")}
    return {"k_flux": _at(rows, "k_psi", L0), "k_flux_vs_L": kf or None,
            "k_T": _at(rows, "k_T", L0), "k_T_vs_L": kt or None, "k_L_vs_L": kl or None,
            "fidelity": "3-D Stage A per stack length (own run + 2-D leg, n_stack 4); "
                        "k_T Stage D co-energy (±1 shift); k_L inherited law",
            "geometry_sig": geo_sig, "source": "Ø40 passport fill 2026-10-05"}


def k_table_html(tab: Dict[str, Any]) -> str:
    rows = [[r["stack_mm"], r.get("k_psi"), r.get("k_T"), r.get("k_L"),
             r.get("two_d_verdict"), Hh.H(Hh.esc(r["source"]))] for r in tab["rows"]]
    return ("<h3>k(L) — what Configure's length slider reads</h3>"
            + Hh.table(["stack mm", "k_ψ (Stage A)", "k_T (Stage D)", "k_L (law)",
                        "3-D/2-D check", "basis"], rows))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--records-dir", default=None)
    ap.add_argument("--html-dir", default=None)
    ap.add_argument("--store", action="store_true",
                    help="re-export config/passports from the written records")
    a = ap.parse_args(argv)
    work = Path(a.work)
    tab = k_table(work / "out" / "full")
    FC.stage2_block = make_stage2(tab)
    recs = {}
    rd = Path(a.records_dir) if a.records_dir else None
    for M in ("L12", "L20"):
        snap = json.loads((work / "out" / M / "snapshot.json").read_text(encoding="utf-8"))
        rec = FC.build_full(work, ROOT, M, {"version": (snap.get("owner_inputs") or {})
                                            .get("version")})
        rec["end3d"] = end3d_block(tab, float(snap["geometry"]["motor_length"]),
                                   (snap.get("signatures") or {}).get("geometry_sig"))
        rec["k_vs_L"] = tab
        recs[M] = rec
        out = rd or (work / "out" / M)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"passport_{M}.json").write_text(json.dumps(rec, indent=1, default=str),
                                                encoding="utf-8")
        print(M, "record written; k at L0:", {k: rec["stage2_3d"][k]["value"]
                                              for k in ("k_psi", "k_T", "k_L")}, flush=True)
    if a.html_dir:
        hd = Path(a.html_dir)
        hd.mkdir(parents=True, exist_ok=True)
        extra = k_table_html(tab)
        for M, r in recs.items():
            h = FC.machine_html(r, M).replace("<h2>Si vs GaN</h2>", extra + "<h2>Si vs GaN</h2>", 1)
            (hd / f"{M}.html").write_text(h, encoding="utf-8")
        (hd / "compare.html").write_text(FC.compare_html(recs), encoding="utf-8")
        print("html written to", hd)
    if a.store and rd:
        import subprocess
        subprocess.run([sys.executable, str(ROOT / "scripts" / "export_passport_store.py")]
                       + [str(rd / f"passport_{M}.json") for M in recs], check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
