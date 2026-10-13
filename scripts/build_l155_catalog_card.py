"""Build the private Motors-catalogue card ``cat_ciano10_200_opt_l155`` from the FULL passport of
CIANO10 200 opt / "L155 motor" (Ø200, 12 slots / 10 poles, 155 mm, DELTA).  No solve; PRIVATE (owner only).

    python scripts/build_l155_catalog_card.py --full <work>/out/full/passport_L155.json \
        --snapshot <work>/out/L155/snapshot.json > card.json

The passport is on the delta WINDING basis (currents = terminal / sqrt(3); psi, R, L, V of the winding).  The
catalogue stores a delta machine as its EQUIVALENT STAR (``stored_as``; passport.py): I = terminal (line) current
(x sqrt(3)), V and psi / sqrt(3), R and L / 3.  This script transforms a copy of the record that way and then reuses
``build_catalog_card_from_passport.build_block`` unchanged (the D85 structure).  Static fields come from the frozen
snapshot (geometry, winding, mesh, battery, rated duty), not from a preview card (L155 has none).  Not published:
the card carries ``owner`` = the owner's address, which keeps it private until the owner approves.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import build_catalog_card_from_passport as D85  # noqa: E402

SQ3 = math.sqrt(3.0)
CARD_ID = "cat_ciano10_200_opt_l155"
CARD_NAME = "CIANO10 200 opt L155"


def to_equivalent_star(full: Dict[str, Any]) -> Dict[str, Any]:
    """Winding-basis record -> equivalent-star record (I x sqrt3, psi and V / sqrt3, R and L / 3)."""
    f = copy.deepcopy(full)

    def r6(x):
        return round(float(x) * SQ3, 6)

    cc = f["card"]["constants_cold"]
    cc["psi_PM_Wb"]["value"] /= SQ3
    cc["R_phase_ohm"]["value"] /= 3.0
    cc["Ld_bench_mH"]["value"] /= 3.0
    cc["Lq_bench_mH"]["value"] /= 3.0
    for m in f["hot_map"]["mtpa"]:
        m["I"] = r6(m["I"])
    for p in f["hot_map"]["points"]:
        for k in ("I", "i_d", "i_q"):
            p[k] = float(p[k]) * SQ3
        for k in ("psi_d", "psi_q"):
            p[k] = float(p[k]) / SQ3
    f["demag"]["mtpa_retention"] = [[r6(i), v] for i, v in f["demag"]["mtpa_retention"]]
    f["loss_grid"]["plan"]["R_hot_ohm"] = float(f["loss_grid"]["plan"]["R_hot_ohm"]) / 3.0
    for p in f["loss_grid"]["points"]:
        if p.get("I_rms") is None:          # an infeasible plan node (no operating angle): no solve
            continue
        p["I_rms"] = r6(p["I_rms"])
        for k in ("i_d", "i_q"):
            if p.get(k) is not None:
                p[k] = float(p[k]) * SQ3
    return f


def static_from_snapshot(snap: Dict[str, Any]) -> Dict[str, Any]:
    """The static card (passport fields no solve of the passport produces) from the frozen snapshot."""
    from motor_ai_sim.passport import _slot_fill_measured
    from motor_ai_sim.winding import parse_connection, wire_parallel_from_geo, wire_split_from_geo
    g, rd, w = snap["geometry"], snap["rated_duty"], snap["winding"]
    cb = snap.get("current_basis") or {}
    I0 = float(cb.get("rated_terminal_arms") or rd["current_arms"])
    k_end = float(rd["end_winding_factor"])
    sf = _slot_fill_measured(g)
    m = snap["mesh"]
    delta = str(w.get("star_delta") or "star").lower().startswith("d")
    block = {
        "N0": float(g["num_wires_per_slot"]), "L0_mm": float(g["motor_length"]),
        "wireH0_mm": float(g["wire_height"]), "I0_A": I0, "rpm0": round(float(rd["rpm"])),
        "nP0": float(parse_connection(str(w["connection"]))[0]),
        "wire_parallel0": float(wire_parallel_from_geo(g)), "wire_split0": float(wire_split_from_geo(g)),
        "mode0": "motor", "role": str(snap.get("role") or "motor"),
        "star_delta": "delta" if delta else "star",
        "stored_as": "equivalent star" if delta else "star",
        "k_end0": k_end, "endWindFrac": 1.0 - 1.0 / k_end, "extraction_rev": 2, "emf_basis": "fundamental",
        **({"A_slot_mm2": round(sf["A_slot_mm2"], 2), "A_cu0_mm2": round(sf["A_cu_mm2"], 2),
            "slot_fill0_pct": round(100.0 * sf["fill"], 2)} if sf else {}),
        # the saved rated duty's own mass (it carries no mass in the stored summary: read from the duty result)
        "mass0_kg": float((rd.get("stored_summary") or {}).get("mass_total_kg") or 27.559),
        "mesh_settings": {"mesh_size_mm": m["mesh_size_mm"], "n_sectors": m["n_sectors"],
                          "min_size_mm": m["min_size_mm"], "outer_air_factor": m["outer_air_factor"],
                          "gap_layers": m["gap_layers"]},
        "mesh_settings_source": {k: "duty Mesh block (rated 1x9 mm)" for k in
                                 ("mesh_size_mm", "n_sectors", "min_size_mm", "outer_air_factor", "gap_layers")},
        "mesh_settings_note": None,
        "battery": dict(snap["battery"]),
    }
    return {"id": CARD_ID, "poles": int(g["num_poles"]), "slots": int(g["num_slots"]),
            "diameter_mm": 2 * float(g["stator_outer_radius"]),
            "passport": {
                "passport": block,
                "fit": {"slotHeight_mm": float(g["slot_height"]), "insulation_mm": float(g["insulation_thickness"]),
                        "wireSpacingY_mm": float(g["wire_spacing_y"]), "slotWidth_mm": float(g["slot_width"]),
                        "wireWidth_mm": float(g["wire_width"])},
                "geo": {"statorOR_mm": float(g["stator_outer_radius"]), "statorIR_mm": float(g["stator_inner_radius"]),
                        "rotorOR_mm": float(g["rotor_outer_radius"]), "rotorIR_mm": float(g["rotor_inner_radius"]),
                        "numSlots": int(g["num_slots"]), "numPoles": int(g["num_poles"]),
                        "magnetHeight_mm": float(g["magnet_height"])}}}


def loss_rpms(eq: Dict[str, Any], I_rows: List[float]) -> List[float]:
    """Loss-grid columns: the speeds that have a settled point at EVERY current row (rectangular grid)."""
    have: Dict[float, set] = {}
    for p in eq["loss_grid"]["points"]:
        if p.get("groups_W") and p.get("I_rms") is not None:
            have.setdefault(float(p["rpm"]), set()).add(round(float(p["I_rms"]), 4))
    rows = {round(i, 4) for i in I_rows}
    return sorted(n for n, cs in have.items() if rows <= cs)


def build(full: Dict[str, Any], snap: Dict[str, Any], owner: str) -> Dict[str, Any]:
    eq = to_equivalent_star(full) if (snap.get("current_basis") or {}).get("delta") else full
    static = static_from_snapshot(snap)
    pts = [p for p in eq["loss_grid"]["points"] if p.get("groups_W") and p.get("I_rms") is not None]
    cur_rows = sorted({round(float(p["I_rms"]), 6) for p in pts})
    rpms = loss_rpms(eq, cur_rows)
    if not rpms:
        raise SystemExit("no loss-grid speed is settled at every current row")
    block = D85.build_block(eq, static, rpms=rpms)
    block["source_note"] += ("; delta machine stored as its equivalent star (I = terminal x sqrt3, V and psi / sqrt3, "
                             "R and L / 3 of the winding-basis passport); loss-grid columns = speeds settled at every "
                             "current row: %s rpm" % ", ".join("%g" % r for r in rpms))
    card = D85.build_card(eq, static, CARD_ID, CARD_NAME, owner)
    card["passport"]["passport"] = block
    card["visibility_note"] = "PRIVATE: owner-only card, not published (owner approval pending)"
    return card


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--full", required=True)
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--owner", default="vadim@motresres.com")
    a = ap.parse_args(argv)
    full = json.load(open(a.full, encoding="utf-8"))
    snap = json.load(open(a.snapshot, encoding="utf-8"))
    json.dump(build(full, snap, a.owner), sys.stdout, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
