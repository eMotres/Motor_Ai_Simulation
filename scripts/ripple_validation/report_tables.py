"""Markdown tables for docs/TORQUE_RIPPLE_VALIDATION_2026-09-30.md.

usage: python report_tables.py OUTDIR RUN [RUN ...]
Writes OUTDIR/summary.json (every number, full precision) and prints tables.
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyse import analyse_run, subsample_maxwell, harmonics  # noqa: E402


def fmt(x, n=4):
    return "—" if x is None else ("%.*g" % (n, x))


def row(name, m, ref_mean=None):
    h = m.get("harm", {})
    return "| %s | %s | %s | %s | %s | %s | %s | %s |" % (
        name, fmt(m["mean"], 6), fmt(m["pp"], 4), fmt(m.get("pp_pct"), 4),
        fmt(h.get(6)), fmt(h.get(12)), fmt(h.get(18)), fmt(h.get(24)))


def main():
    outdir = sys.argv[1]
    runs = sys.argv[2:]
    allres = {}
    for r in runs:
        res = analyse_run(r)
        ser = res.pop("_series", None)
        vws = res.pop("_vw_frozen_series", None)
        name = os.path.basename(r)
        # angular-resolution study on the shipped-style waveform
        sub = {}
        for spc in (3, 6, 9, 12, 18, 36):
            s = subsample_maxwell(r, spc)
            if s:
                sub[spc] = s
        res["maxwell_subsampled"] = sub
        # same for the energy reference (trajectory) - subsampling the waveform
        if ser is not None:
            TB = np.asarray(ser["T_energy_traj"])
            e_sub = {}
            for spc in (3, 6, 9, 12, 18, 36):
                per = spc * 12
                if TB.size % per == 0:
                    s = TB.size // per
                    pps = [float(np.ptp(TB[ph::s])) for ph in range(s)]
                    e_sub[spc] = {"pp_min": min(pps), "pp_max": max(pps)}
            res["energy_subsampled"] = e_sub
        if vws is not None and ser is not None and len(vws) == len(ser["T_maxwell"]):
            TA = np.asarray(vws)[:, 1]
            TM = np.asarray(ser["T_maxwell"])
            res["maxwell_minus_vw_frozen"] = {
                "mean_diff": float(TM.mean() - TA.mean()),
                "ac_rms_diff": float(np.sqrt(np.mean(((TM - TM.mean()) - (TA - TA.mean())) ** 2))),
                "harm_of_diff": harmonics(TM - TA)}
        allres[name] = res
        res["_series"] = ser
        res["_vw_frozen_series"] = vws
    with open(os.path.join(outdir, "summary.json"), "w") as f:
        json.dump(allres, f, indent=1)
    for name, res in allres.items():
        print("\n### %s  (ring %d/period, gap layers %g, mesh x%g, %d centres, "
              "Newton all ok %s, max res %.1e, %d frames, %.0f s)" % (
                  name, res["ring"], res["gap_layers"], res["mesh_scale"], res["n_centres"],
                  res["newton_all_ok"], res["res_max"] or 0, res["n_rows"], res["wall_s"]))
        print("| method | mean N·m | p-p N·m | p-p % | h6 | h12 | h18 | h24 |")
        print("|---|---|---|---|---|---|---|---|")
        for key, lab in (("vw_frozen", "frozen-current virtual work (reference A)"),
                         ("energy_traj", "trajectory energy balance (reference B)"),
                         ("maxwell", "raw Maxwell (Arkkio)"),
                         ("shipped", "shipped: %s mean + Maxwell AC" % res.get("shipped_mean_source")),
                         ("vw_mortar_diag", "L2-mortar VW diagnostic")):
            if key in res and "pp" in res[key]:
                print(row(lab, res[key]))
        for k in ("vw_frozen_vs_traj_max_abs_Nm", "vw_frozen_vs_maxwell_rms_Nm",
                  "vw_d1_vs_richardson_max_abs_Nm", "sym60_max_abs_diff_Nm",
                  "sym60_max_abs_diff_maxwell_Nm", "maxwell_minus_vw_frozen", "dWdi_check",
                  "maxwell_subsampled", "energy_subsampled"):
            if k in res:
                print("-", k, json.dumps(res[k]))


if __name__ == "__main__":
    main()
