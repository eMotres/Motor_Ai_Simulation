"""Build a shared-catalogue Configure card from a FULL passport record.

Configure reads a machine's scaling inputs from the ``passport`` block of its
catalogue card (``motorScaling.ts: Passport``) and its drive variants from the
passport store.  ``generate_passport`` (FEM, minutes) writes that block for a
card; for a machine whose FULL passport (``passport-v1-full-1``) already holds
the solved data - the Ø85 CIANO28 85 20SW1200 / L13 - this script assembles the
same block from the record, with no solve, and says where every field came from
(``source_note``).  Static, thermally independent fields (winding, slot window,
mass, mesh, pack) are taken from an existing preview card of the SAME build.

Thermal basis of the assembled block (stated on the card, never implied):
  * 20 degC (spec COLD) - T0, the torque-vs-current curve at the measured levels,
    Vemf0 / psi_PM, R0, Ld / Lq (small-signal differential, bench).
  * card state (magnet 69.2 degC, winding 75.1 degC, fixed gamma_ref) - the
    iron / magnet / AC-copper loss grid, the terminal-voltage curve, the demag
    retention, and the torque-curve levels the 20 degC solves did not cover
    (hot torque x the 20 degC / hot ratio, flat beyond the last measured level).

    python scripts/build_catalog_card_from_passport.py \
        --full docs/data/passport_d85/passport_L13.json \
        --static-card <catalog.json>:<candidate id> \
        --id cat_ciano28_85_20sw1200_l13 --name "CIANO28 85 20SW1200 L13" \
        --owner vadim@motresres.com > card.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from typing import Any, Dict, List

#: fields copied unchanged from the static (same-build) card block
STATIC_KEYS = ("N0", "L0_mm", "wireH0_mm", "I0_A", "rpm0", "nP0", "wire_parallel0",
               "wire_split0", "mode0", "role", "star_delta", "stored_as", "k_end0",
               "endWindFrac", "extraction_rev", "emf_basis", "A_slot_mm2", "A_cu0_mm2",
               "slot_fill0_pct", "mass0_kg", "mesh_settings", "mesh_settings_source",
               "mesh_settings_note", "battery")


def _val(block: Dict[str, Any], key: str) -> float:
    return float(block[key]["value"])


def _copy_phase_metadata(out: Dict[str, Any], sp: Dict[str, Any], I0: float) -> None:
    """Keep set topology on assembled cards while storing current on equivalent-total basis."""
    phase_count = int(sp.get("phase_count") or 3)
    if phase_count not in (3, 6):
        raise ValueError(f"unsupported passport phase_count={phase_count}")
    if phase_count == 3:
        return
    six = dict(sp.get("six_phase") or {})
    six["current_per_inverter_A"] = I0 / 2.0
    out.update({"phase_count": 6, "phase_sets": 2,
                "current_basis": "equivalent_three_phase_total", "six_phase": six})


def build_block(full: Dict[str, Any], static: Dict[str, Any]) -> Dict[str, Any]:
    """The ``passport`` dict (inner block) of the card."""
    sp = static["passport"]["passport"]
    static_phase_count = int(sp.get("phase_count") or 3)
    full_phase_count = int(full.get("phase_count") or 3)
    if static_phase_count != full_phase_count:
        raise ValueError(
            "passport source topology mismatch: static is %d-phase, full record is %d-phase"
            % (static_phase_count, full_phase_count))
    if static_phase_count == 6:
        static_six = sp.get("six_phase") if isinstance(sp.get("six_phase"), dict) else {}
        full_six = full.get("six_phase") if isinstance(full.get("six_phase"), dict) else {}
        for key in ("set_connection", "neutrals", "n_parallel", "set1_paths",
                    "set2_paths", "slot_set"):
            if key in static_six and key in full_six and static_six[key] != full_six[key]:
                raise ValueError(f"passport source topology mismatch: six_phase.{key}")
    cold = full["card"]["constants_cold"]
    hot_mtpa = {round(float(m["fI"]), 4): m for m in full["hot_map"]["mtpa"]}
    k_levels = {round(float(k["fI"]), 4): k for k in full["cold"]["k_levels"]}
    I0 = float(sp["I0_A"])
    n0 = float(sp["rpm0"])
    pp = int(static["poles"]) // 2

    out: Dict[str, Any] = {k: sp[k] for k in STATIC_KEYS if k in sp}
    _copy_phase_metadata(out, sp, I0)

    # -- 20 degC constants ---------------------------------------------------
    psi_c = _val(cold, "psi_PM_Wb")
    w_e0 = 2.0 * math.pi * pp * n0 / 60.0
    out["Vemf0_peak_V"] = psi_c * w_e0
    out["R0_ohm"] = _val(cold, "R_phase_ohm")
    out["T0_Nm"] = float(k_levels[1.0]["T_cold"])
    out["ldq0"] = {"Ld_mH": _val(cold, "Ld_bench_mH"), "Lq_mH": _val(cold, "Lq_bench_mH"),
                   "I_probe_arms": 2.0, "connection": sp.get("ldq0", {}).get("connection", "4S"),
                   "method": "differential (tangent reluctivity), +-2 A pairs, 20 degC, 2-D"}

    # -- torque / voltage / demag curve over phase current ---------------------
    levels = sorted(hot_mtpa)
    meas = sorted(k_levels)

    def ratio(f: float) -> float:
        if f in k_levels:
            return float(k_levels[f]["T_cold"]) / float(k_levels[f]["T_hot"])
        lo = [m for m in meas if m < f]
        hi = [m for m in meas if m > f]
        if lo and hi:
            a, b = lo[-1], hi[0]
            ra, rb = ratio(a), ratio(b)
            return ra + (rb - ra) * (f - a) / (b - a)
        return ratio(lo[-1] if lo else hi[0])

    ret = {round(float(i), 2): float(v) for i, v in full["demag"]["mtpa_retention"]}
    rd = float(full["loss_grid"]["plan"]["R_hot_ohm"])
    w1 = 2.0 * math.pi * pp * n0 / 60.0
    pts = {p["id"]: p for p in full["hot_map"]["points"]}
    cur: Dict[str, List[float]] = {"I_A": [], "T_Nm": [], "V_peak_V": [], "demag_keep_pct": []}
    for f in levels:
        m = hot_mtpa[f]
        p = pts.get(m["conf_id"])
        cur["I_A"].append(float(m["I"]))
        cur["T_Nm"].append(float(k_levels[f]["T_cold"]) if f in k_levels
                           else float(m["T_fem_vertex"]) * ratio(f))
        if p:
            vd = rd * p["i_d"] - w1 * p["psi_q"]
            vq = rd * p["i_q"] + w1 * p["psi_d"]
            cur["V_peak_V"].append(math.hypot(vd, vq))
        else:
            cur["V_peak_V"].append(None)
        cur["demag_keep_pct"].append(ret.get(round(float(m["I"]), 2), 100.0))
    out["current"] = cur
    i0_i = cur["I_A"].index(I0)
    out["Vload0_peak_V"] = cur["V_peak_V"][i0_i]
    out["Vload0_fund_V"] = cur["V_peak_V"][i0_i]

    # -- loss grid (card state), rows = currents, cols = rpm ------------------
    pts_l = [p for p in full["loss_grid"]["points"] if p.get("groups_W")]
    if int(out.get("phase_count") or 3) == 6:
        rpms = sorted({float(p["rpm"]) for p in pts_l})
        pairs = {(float(p["rpm"]), float(p["I_rms"])) for p in pts_l}
        currents = sorted({i for _, i in pairs})
        rpms = [r for r in rpms if all((r, i) in pairs for i in currents)]
        cur_l = [i for i in currents if all((r, i) in pairs for r in rpms)]
        if not rpms or not cur_l:
            raise ValueError("six-phase passport loss points do not form a complete current-speed grid")
    else:
        rpms = [250.0, 500.0, 1000.0, 1500.0, 2000.0]
        cur_l = sorted({float(p["I_rms"]) for p in pts_l if p["rpm"] in rpms})
    grid: Dict[str, Any] = {"I_A": cur_l, "rpm": rpms, "Pfe_W": [], "Pmag_W": [], "cuAC": [],
                            "Pcu_dc_W": [], "Pcu_ac_W": []}
    by = {(float(p["rpm"]), float(p["I_rms"])): p for p in pts_l}
    for I in cur_l:
        rows = {k: [] for k in ("Pfe_W", "Pmag_W", "cuAC", "Pcu_dc_W", "Pcu_ac_W")}
        for r in rpms:
            g = by[(r, I)]["groups_W"]
            dc, ac = float(g["P_cu_dc_W"]), float(g["P_cu_ac_W"])
            rows["Pfe_W"].append(float(g["P_fe_stator_W"]) + float(g["P_fe_rotor_W"]))
            rows["Pmag_W"].append(float(g["P_mag_W"]) + float(g["P_shaft_W"]) + float(g.get("P_sleeve_W") or 0.0))
            rows["Pcu_dc_W"].append(dc)
            rows["Pcu_ac_W"].append(ac)
            rows["cuAC"].append((dc + ac) / dc if dc > 0 else 1.0)
        for k, v in rows.items():
            grid[k].append(v)
    out["loss_grid"] = grid
    ri = cur_l.index(I0)
    ci = rpms.index(n0)
    out["Pfe0_W"] = grid["Pfe_W"][ri][ci]
    out["Pmag0_W"] = grid["Pmag_W"][ri][ci]
    g0 = by[(n0, I0)]["groups_W"]
    out["P_fe_stator0_W"] = float(g0["P_fe_stator_W"])
    out["P_fe_rotor0_W"] = float(g0["P_fe_rotor_W"])
    out["ripple0_pct"] = round(float(by[(n0, I0)]["T_ripple_pct"]), 2)
    out["speed"] = {"rpm": rpms, "Pfe_W": grid["Pfe_W"][ri], "Pmag_W": grid["Pmag_W"][ri]}
    if int(out.get("phase_count") or 3) == 6:
        six = dict(out.get("six_phase") or {})
        six["voltage_per_set_V"] = out["Vload0_fund_V"]
        six["resistance_per_set_ohm"] = 2.0 * out["R0_ohm"]
        six["resistance_basis"] = "one set has half the parallel paths: 2 x equivalent phase resistance"
        pcu_set = (grid["Pcu_dc_W"][ri][ci] + grid["Pcu_ac_W"][ri][ci]) / 2.0
        six["copper_loss_per_set_W"] = pcu_set
        six["losses_per_set_W"] = {"copper_W": pcu_set, "iron_W": None,
                                    "magnet_W": None, "shaft_W": None}
        six["loss_allocation_note"] = (
            "base card-state copper is split equally; iron and rotor losses remain machine totals")
        out["six_phase"] = six

    # -- 3-D end effect (Stage A, own stack) ----------------------------------
    stage_a = full.get("stage2_3d") if isinstance(full.get("stage2_3d"), dict) else {}
    k_psi = (stage_a.get("k_psi") or {}).get("value")
    k_t = (stage_a.get("k_T") or {}).get("value")
    if k_psi is not None and k_t is not None:
        k = float(k_psi)
        out["end3d"] = {"k_flux": k, "k_flux_vs_L": None,
                        "fidelity": "Stage A k_flux_self at the own stack (2-D check failed, raw k_flux not used)",
                        "k_T": float(k_t)}
    elif int(out.get("phase_count") or 3) == 6:
        out["end3d"] = None
    else:
        raise ValueError("full passport is missing its Stage A 3-D coefficients")
    out["pwm"] = None
    out["source_note"] = (
        "assembled from the full passport %s (no solve): 20 degC - T0, Vemf0, R0, Ld/Lq, torque curve "
        "at the measured levels; card state (magnet %.1f degC, winding %.1f degC, fixed gamma_ref) - loss "
        "grid, terminal voltage, demag retention, torque levels beyond the 20 degC solves "
        "(hot x measured cold/hot ratio)" % (
            full.get("machine", {}).get("tag"),
            full["inputs"]["temperatures"]["hot_magnet_c"], full["inputs"]["temperatures"]["hot_coil_c"]))
    if int(out.get("phase_count") or 3) == 6:
        out["source_note"] += "; two in-phase 3-phase sets share phase current equally and retain full phase voltage per set"
        if out.get("end3d") is None:
            out["source_note"] += "; Stage A 3-D correction unavailable, Configure remains on 2-D values"
    return out


def build_card(full: Dict[str, Any], static_card: Dict[str, Any], card_id: str, name: str,
               owner: str) -> Dict[str, Any]:
    block = build_block(full, static_card)
    sp = static_card["passport"]
    return {"id": card_id, "name": name, "diameter_mm": static_card["diameter_mm"], "owner": owner,
            "poles": static_card["poles"], "slots": static_card["slots"],
            "passport": {"passport": block, "fit": sp["fit"], "geo": sp["geo"],
                         "poles": static_card["poles"], "slots": static_card["slots"]}}


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--full", required=True)
    ap.add_argument("--static-card", required=True, help="<catalog.json>:<card id>")
    ap.add_argument("--id", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--owner", required=True)
    a = ap.parse_args(argv)
    full = json.load(open(a.full, encoding="utf-8"))
    path, cid = a.static_card.rsplit(":", 1)
    cat = json.load(open(path, encoding="utf-8"))
    static = next(m for m in cat["motors"] if m["id"] == cid)
    json.dump(build_card(full, static, a.id, a.name, a.owner), sys.stdout, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
