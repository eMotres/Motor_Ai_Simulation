"""Existing datasheet-curve loss model vs the SPICE-table model (task E).

    python scripts/spice_compare_losses.py

Solver-direct (``losses.solve_controller`` in-process; no API, no config
write).  Needs the cards' ``switching_table`` blocks
(scripts/spice_build_tables.py).  Prints markdown tables for the doc and
writes ``config/devices/spice/_runs/compare_losses.json`` (git-ignored).

L155 motor rated (CIANO10 200 opt, stored coupled record — the §6 inputs of
docs/CONTROLLER_MODULE_2026-09-22.md): IMCQ120R004M2H, one 3-phase bridge,
N = 3, delta 314.3 A phase, 272.2 kW AC, m 0.6333, 750.4 V, 24 kHz, 0.5 us
dead time, V_GS 18/0 V, micro-channel coldplate water-glycol 50/50 8 L/min
65 degC, R_TIM 0.03 K/W, eta_shaft 0.9771.

Oe40 L12 (CIANO14 40_12): IQE050N08NM5SC, 22.2 V (6S), one 3-phase bridge,
N = 2, star 43.8 A, f_el 1516.7 Hz, 48 kHz, V_GS 10/0 V, R_G 1.6 ohm, 0.5 us,
forced air 10 m/s 35 degC, R_TIM 0.03, R_spread 0.02 K/W — the test suite's
L12 point moved to the 6S bus with m = 0.9 and a STATED power factor 0.9
(p_ac follows).  The stored L12 duty record carries no electrical point
(thermal only), so this is a device comparison at a stated point, not a
re-solve of the machine.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from motor_ai_sim.inverter import losses as lo  # noqa: E402
from motor_ai_sim.inverter.devices import get_device  # noqa: E402

L155 = dict(num_slots=12, num_poles=10, single_layer=True, star_delta="delta",
            device="IMCQ120R004M2H", devices_parallel=3, v_dc_V=750.4,
            i_phase_rms_A=314.3, p_ac_W=272_200.0, f_elec_hz=1183.3,
            f_carrier_hz=24_000.0, modulation_index=0.6333, efficiency_shaft=0.9771,
            topology="one_3ph", dead_time_us=0.5, v_gs_on_V=18.0, v_gs_off_V=0.0,
            r_g_ext_ohm=2.3,
            cooling={"coolant": "water_glycol_50", "flow_lpm": 8.0, "t_in_c": 65.0},
            r_tim_k_w=0.03, r_spread_k_w=0.0)

#: The "realistic driver" of the comparison (ASSUMPTION, stated in the doc):
#: one 4.7 ohm external resistor per device for both edges, V_GS 18/0 V.
RG_REAL = 4.7


def l12(v_dc=22.2, m=0.9, pf=0.9):
    i = 43.8
    s = 3.0 * (m * v_dc / (2.0 * math.sqrt(2.0))) * i
    return dict(num_slots=12, num_poles=10, single_layer=True, star_delta="star",
                device="IQE050N08NM5SC", devices_parallel=2, v_dc_V=v_dc,
                i_phase_rms_A=i, p_ac_W=pf * s, f_elec_hz=1516.7,
                f_carrier_hz=48_000.0, modulation_index=m, topology="one_3ph",
                dead_time_us=0.5, v_gs_on_V=10.0, v_gs_off_V=0.0, r_g_ext_ohm=1.6,
                cooling={"mode": "air_forced", "air_speed_mps": 10.0, "t_ambient_c": 35.0},
                r_tim_k_w=0.03, r_spread_k_w=0.02)


def row(name, req):
    out = lo.solve_controller(req)
    L = out["losses"]
    legs = [x for b in out["bridges"] for x in b["legs"]]
    return {"case": name, "cond_W": L["conduction_W"], "dead_W": L["third_quadrant_W"],
            "sw_W": L["switching_W"],
            "on_W": round(sum(x["p_switching_on_W"] for x in legs), 1),
            "off_W": round(sum(x["p_switching_off_W"] for x in legs), 1),
            "fr_W": round(sum(x["p_switching_recovery_W"] for x in legs), 1),
            "total_W": L["total_W"], "t_j_c": out["thermal"]["t_j_max_c"],
            "eta_inv": out["efficiency"]["inverter"],
            "eta_wall": out["efficiency"]["wall_to_shaft"],
            "src": L["switching_energy_source"], "feasible": out["feasible"],
            "warnings": out["warnings"][:3],
            "notes": [n for n in out["model_notes"] if "SPICE" in n or "nearest" in n][:3]}


def md(rows):
    h = ("| case | conduction W | dead time W | switching W (on/off/fr) | total W | T_j °C "
         "| η_inv | η wall-to-shaft |")
    s = [h, "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        s.append(f"| {r['case']} | {r['cond_W']:.0f} | {r['dead_W']:.0f} | {r['sw_W']:.0f} "
                 f"({r['on_W']:.0f}/{r['off_W']:.0f}/{r['fr_W']:.0f}) | **{r['total_W']:.0f}** | "
                 f"{r['t_j_c']:.1f} | {100 * r['eta_inv']:.2f} % | "
                 + (f"{100 * r['eta_wall']:.2f} %" if r['eta_wall'] else "—") + " |")
    return "\n".join(s)


def energy_rows(part, v, t, currents, ds_kw, sp_kw):
    """Per-event energies, existing path vs SPICE table, at one (V, T)."""
    c = get_device(part)
    out = []
    for i in currents:
        a = c.e_switch(i_d_A=i, t_j_c=t, v_dc_V=v, **ds_kw)
        b = c.e_switch(i_d_A=i, t_j_c=t, v_dc_V=v, source="spice", **sp_kw)
        out.append({"i_A": i, **{f"ds_{k}": a[k] * 1e6 for k in ("e_on_J", "e_off_J", "e_fr_J")},
                    **{f"sp_{k}": b[k] * 1e6 for k in ("e_on_J", "e_off_J", "e_fr_J")}})
    return out


def md_energy(rows, label):
    s = [f"| I_D A | E_on {label} / SPICE µJ | E_off {label} / SPICE | E_fr {label} / SPICE | "
         "E_tot ratio SPICE/{label} |".replace("{label}", label),
         "|---|---|---|---|---|"]
    for r in rows:
        tot_a = r["ds_e_on_J"] + r["ds_e_off_J"] + r["ds_e_fr_J"]
        tot_b = r["sp_e_on_J"] + r["sp_e_off_J"] + r["sp_e_fr_J"]
        s.append(f"| {r['i_A']:g} | {r['ds_e_on_J']:.1f} / {r['sp_e_on_J']:.1f} | "
                 f"{r['ds_e_off_J']:.1f} / {r['sp_e_off_J']:.1f} | "
                 f"{r['ds_e_fr_J']:.1f} / {r['sp_e_fr_J']:.1f} | "
                 f"{(tot_b / tot_a if tot_a else float('nan')):.2f} |")
    return "\n".join(s)


def main() -> int:
    res = {}
    rows = [
        row("datasheet curves, R_G 2.3 Ω (existing)", dict(L155)),
        row("SPICE, datasheet driver: R_G 2.3/2.3 Ω, L_σ 15 nH",
            dict(L155, switching_source="spice", r_g_ext_ohm=2.3, r_g_off_ext_ohm=2.3,
                 l_sigma_nH=15)),
        row(f"datasheet curves × existing R_G rule at {RG_REAL} Ω (existing)",
            dict(L155, r_g_ext_ohm=RG_REAL)),
        row(f"SPICE, R_G {RG_REAL}/{RG_REAL} Ω, L_σ 10 nH",
            dict(L155, switching_source="spice", r_g_ext_ohm=RG_REAL,
                 r_g_off_ext_ohm=RG_REAL, l_sigma_nH=10)),
        row(f"SPICE, R_G {RG_REAL}/{RG_REAL} Ω, L_σ 20 nH",
            dict(L155, switching_source="spice", r_g_ext_ohm=RG_REAL,
                 r_g_off_ext_ohm=RG_REAL, l_sigma_nH=20)),
    ]
    res["L155 rated"] = rows
    print("\n### L155 rated, IMCQ120R004M2H ×3, one 3-phase bridge\n")
    print(md(rows))
    for r in rows:
        if r["notes"] or r["warnings"]:
            print(" -", r["case"], "|", r["warnings"], "|", r["notes"])
    e = energy_rows("IMCQ120R004M2H", 750.4, 125.0, [50, 100, 185.2, 257],
                    dict(r_g_ext_ohm=2.3), dict(r_g_ext_ohm=2.3, r_g_off_ext_ohm=2.3, l_sigma_nH=15))
    res["R004 energies 750.4 V 125 C"] = e
    print("\n#### IMCQ120R004M2H per event, 750.4 V, 125 °C, R_G 2.3 Ω\n")
    print(md_energy(e, "curves"))

    rows = [row("times-and-charges fallback (existing), R_G 1.6 Ω", l12()),
            row("SPICE, R_G 1.6/1.6 Ω, L_σ 2 nH",
                dict(l12(), switching_source="spice", r_g_ext_ohm=1.6, r_g_off_ext_ohm=1.6,
                     l_sigma_nH=2)),
            row("SPICE, R_G 1.6/1.6 Ω, L_σ 5 nH",
                dict(l12(), switching_source="spice", r_g_ext_ohm=1.6, r_g_off_ext_ohm=1.6,
                     l_sigma_nH=5))]
    res["L12 22.2 V"] = rows
    print("\n### Ø40 L12, IQE050N08NM5SC ×2, 22.2 V, 48 kHz\n")
    print(md(rows))
    for r in rows:
        if r["notes"] or r["warnings"]:
            print(" -", r["case"], "|", r["warnings"], "|", r["notes"])
    e = energy_rows("IQE050N08NM5SC", 22.2, 25.0, [5, 12, 20, 31],
                    dict(r_g_ext_ohm=1.6, v_gs_on_V=10.0),
                    dict(r_g_ext_ohm=1.6, r_g_off_ext_ohm=1.6, l_sigma_nH=2, v_gs_on_V=10.0))
    res["IQE energies 22.2 V 25 C"] = e
    print("\n#### IQE050N08NM5SC per event, 22.2 V, 25 °C, R_G 1.6 Ω, L_σ 2 nH\n")
    print(md_energy(e, "fallback"))
    out = ROOT / "config" / "devices" / "spice" / "_runs" / "compare_losses.json"
    out.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
