"""Datasheet (corrected fallback) vs the uniform SPICE basis — L155 and Oe40 L12.

    python scripts/spice_uniform_compare.py

Solver-direct (``losses.solve_controller`` in-process; no API, no config
write).  The two machine points are those of ``spice_compare_losses.py``
(L155: IMCQ120R004M2H x3 one_3ph, 750.4 V, 24 kHz; L12: IQE050N08NM5SC x2,
22.2 V, 48 kHz).  Two reference rows isolate the vendor model's
Kelvin-to-power-source path: the same SPICE solve with the conduction read
at the Kelvin pin instead of the power pin (for the doc only — the basis
itself is never edited by hand).  Writes
``config/devices/spice/_runs/uniform_compare.json`` (git-ignored).
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from motor_ai_sim.inverter import losses as lo  # noqa: E402
from motor_ai_sim.inverter.devices import DeviceCard, get_device  # noqa: E402
from spice_compare_losses import L155, l12  # noqa: E402


def solve(req, card=None):
    orig = lo.get_device
    if card is not None:
        lo.get_device = lambda name: card
    try:
        out = lo.solve_controller(req)
    finally:
        lo.get_device = orig
    L = out["losses"]
    legs = [x for b in out["bridges"] for x in b["legs"]]
    return {"cond_W": L["conduction_W"], "dead_W": L["third_quadrant_W"],
            "sw_W": L["switching_W"],
            "on_W": round(sum(x["p_switching_on_W"] for x in legs), 1),
            "off_W": round(sum(x["p_switching_off_W"] for x in legs), 1),
            "fr_W": round(sum(x["p_switching_recovery_W"] for x in legs), 1),
            "total_W": L["total_W"], "t_j_c": out["thermal"]["t_j_max_c"],
            "eta_inv": out["efficiency"]["inverter"],
            "eta_wall": out["efficiency"]["wall_to_shaft"],
            "basis": L.get("basis_label"), "dev": L.get("spice_deviation_line"),
            "r_ds_mohm": legs[0]["r_ds_on_mohm"] if legs else None,
            "feasible": out["feasible"], "warnings": out["warnings"][:4]}


def kelvin_card(part):
    doc = copy.deepcopy(get_device(part).doc)
    st = doc["static_table"]
    st["forward"] = [[r[0], r[1], r[2], r[3], r[3]] for r in st["forward"]]
    st["third_quadrant"] = [[r[0], r[1], r[2], r[3], r[3]] for r in st["third_quadrant"]]
    return DeviceCard(doc)


def no_static_card(part):
    doc = copy.deepcopy(get_device(part).doc)
    doc.pop("static_table", None)
    return DeviceCard(doc)


def main():
    rows = []
    cases = [
        ("L155", "datasheet (corrected fallback), R_G 2.3 ohm", dict(L155, switching_source="datasheet"), None),
        ("L155", "SPICE uniform, R_G 2.3/2.3 ohm, L_sigma 15 nH", dict(L155, switching_source="spice"), None),
        ("L155", "SPICE switching + datasheet conduction (reference)", dict(L155, switching_source="spice"),
         no_static_card("IMCQ120R004M2H")),
        ("L155", "SPICE uniform, conduction at the Kelvin pin (reference)", dict(L155, switching_source="spice"),
         kelvin_card("IMCQ120R004M2H")),
        ("L155", "datasheet (corrected fallback), R_G 4.7 ohm", dict(L155, switching_source="datasheet", r_g_ext_ohm=4.7), None),
        ("L155", "SPICE uniform, R_G 4.7/4.7 ohm, L_sigma 15 nH", dict(L155, switching_source="spice", r_g_ext_ohm=4.7), None),
        ("L12", "datasheet (times-and-charges fallback), R_G 1.6 ohm", dict(l12(), switching_source="datasheet"), None),
        ("L12", "SPICE uniform, R_G 1.6/1.6 ohm, L_sigma 2 nH", dict(l12(), switching_source="spice"), None),
        ("L12", "SPICE switching + datasheet conduction (reference)", dict(l12(), switching_source="spice"),
         no_static_card("IQE050N08NM5SC")),
    ]
    for m, name, req, card in cases:
        r = solve(req, card)
        r.update(machine=m, case=name)
        rows.append(r)
    print("| machine | case | conduction W | dead time W | switching W (on/off/fr) | total W | T_j °C | η_inv | η wall-to-shaft | R_DS eff mΩ | feasible |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['machine']} | {r['case']} | {r['cond_W']:.1f} | {r['dead_W']:.1f} | "
              f"{r['sw_W']:.1f} ({r['on_W']:.1f}/{r['off_W']:.1f}/{r['fr_W']:.1f}) | "
              f"**{r['total_W']:.1f}** | {r['t_j_c']:.1f} | {100 * r['eta_inv']:.2f} % | "
              + (f"{100 * r['eta_wall']:.2f} %" if r['eta_wall'] else "—")
              + f" | {r['r_ds_mohm']:.2f} | {'yes' if r['feasible'] else 'no'} |")
    for r in rows:
        if r["dev"]:
            print(f"- {r['machine']} {r['case']}: {r['dev']}")
    out = ROOT / "config" / "devices" / "spice" / "_runs" / "uniform_compare.json"
    out.write_text(json.dumps(rows, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
