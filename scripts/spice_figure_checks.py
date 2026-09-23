"""The two scaling rules of the datasheet-curve model, against the vendor
SPICE model AND the datasheet's own figures (task E, "where the linear
scaling is wrong").

    python scripts/spice_figure_checks.py

IMCQ120R004M2H, T_vj = 175 degC, I_D = 185.2 A, V_GS 0/18 V, L_sigma 15 nH:

* bus voltage — double pulses at 600…1000 V, R_G,ext 2.3 ohm, against the
  datasheet figure "E = f(V_DD)" (rev 1.10 p. 13, left panel) and against the
  card's rule E(V) = E(800 V)·(V/800)^1;
* gate resistance — 2.3 / 4.7 / 10 / 20 ohm at 800 V, against the figure
  "E = f(R_G,ext)" (p. 12, lower left) and against the card's implemented
  rule E(R) = E(2.3 ohm)·R/2.3 (E_on/E_off; E_fr held).

The figure values below are READ BY EYE off the published curves (±5 %,
basis: figure) — they are the datasheet's, not fitted.  Finished runs are
re-read from their stored waveforms (keyed on the netlist text), so a
second call simulates nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from motor_ai_sim.inverter.devices import get_device  # noqa: E402
from motor_ai_sim.inverter.spice.harness import run_double_pulse  # noqa: E402
from motor_ai_sim.inverter.spice.models import model_for, spice_dir  # noqa: E402
from motor_ai_sim.inverter.spice.netlist import DoublePulse, timing_for  # noqa: E402

PART = "IMCQ120R004M2H"
#: datasheet rev 1.10 p. 13 "E = f(V_DD)", V_GS 0/18 V, 185.2 A, 175 degC, 2.3 ohm
FIG_V = {600: (2950, 2850, 2250), 700: (3900, 3750, 2600), 800: (4920, 4780, 2990),
         900: (5950, 6000, 3400), 1000: (7050, 7500, 3800)}
#: datasheet rev 1.10 p. 12 "E = f(R_G,ext)", V_GS 0/18 V, 185.2 A, 175 degC, 800 V
FIG_RG = {2.3: (4920, 4780, 2990), 4.7: (6700, 7200, 1900), 10: (10500, 12800, 1200),
          20: (17800, 23000, 800)}


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    m = model_for(PART)
    card = get_device(PART)
    out = {"v": {}, "rg": {}}
    for v in FIG_V:
        dp = DoublePulse(v_dd=float(v), i_target=185.2, t_j=175.0, rg_on=2.3, rg_off=2.3,
                         l_sigma=15e-9)
        r = run_double_pulse(m, dp)
        mm = r["metrics"]
        ex = card.e_switch(i_d_A=185.2, t_j_c=175.0, v_dc_V=float(v), r_g_ext_ohm=2.3)
        out["v"][v] = {"fig": FIG_V[v],
                       "spice": ((mm["on"]["e_J"] or 0) * 1e6, (mm["off"]["e_J"] or 0) * 1e6,
                                 (mm["fr"]["e_fr_J"] or 0) * 1e6),
                       "rule": (ex["e_on_J"] * 1e6, ex["e_off_J"] * 1e6, ex["e_fr_J"] * 1e6),
                       "i_off": mm["i_off_A"], "i_on": mm["i_on_A"]}
    for rg in FIG_RG:
        dp = DoublePulse(v_dd=800.0, i_target=185.2, t_j=175.0, rg_on=rg, rg_off=rg,
                         l_sigma=15e-9, **timing_for(1200.0, rg))
        r = run_double_pulse(m, dp)
        mm = r["metrics"]
        ex = card.e_switch(i_d_A=185.2, t_j_c=175.0, v_dc_V=800.0, r_g_ext_ohm=rg)
        out["rg"][rg] = {"fig": FIG_RG[rg],
                         "spice": ((mm["on"]["e_J"] or 0) * 1e6, (mm["off"]["e_J"] or 0) * 1e6,
                                   (mm["fr"]["e_fr_J"] or 0) * 1e6),
                         "rule": (ex["e_on_J"] * 1e6, ex["e_off_J"] * 1e6, ex["e_fr_J"] * 1e6),
                         "t_r_ns": (mm["on"]["t_r_s"] or 0) * 1e9,
                         "t_f_ns": (mm["off"]["t_f_s"] or 0) * 1e9,
                         "dv_dt_on_V_ns": abs(mm["on"]["dv_dt_V_per_s"] or 0) * 1e-9}
    p = spice_dir() / "_runs" / "figure_checks.json"
    p.write_text(json.dumps(out, indent=1, default=float), encoding="utf-8")

    def line(k, d):
        f, s, r = d["fig"], d["spice"], d["rule"]
        return (f"| {k} | " + " | ".join(
            f"{f[j]:.0f} / {s[j]:.0f} / {r[j]:.0f}" for j in range(3)) + " |")
    print("| V_DD V | E_on fig / SPICE / rule µJ | E_off fig / SPICE / rule | E_fr fig / SPICE / rule |")
    print("|---|---|---|---|")
    for k, d in out["v"].items():
        print(line(k, d))
    print()
    print("| R_G,ext Ω | E_on fig / SPICE / rule µJ | E_off fig / SPICE / rule | E_fr fig / SPICE / rule |")
    print("|---|---|---|---|")
    for k, d in out["rg"].items():
        print(line(k, d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
