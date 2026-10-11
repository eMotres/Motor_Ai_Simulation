"""Write a propeller-S1 motor model (``config/propeller_s1/<die>/<config>.json``).

The S1 solver (``motor_ai_sim.propeller_s1``) needs, per configuration, a torque
map and a loss grid over (rpm, current) with the temperatures they hold at, the
copper resistance, the flux-vs-magnet-temperature slope, the current and speed
limits, the lumped thermal geometry and (optionally) the drive variant's
controller losses.  The full v1 passports carry all of it but live in
``docs/data`` (not in the API image), and the server's passport store keeps only
the drive variants - so this script extracts the compact model once.

Two sources::

    # a full v1 passport (Ø40 L12 / L20): everything from the passport itself
    python scripts/export_propeller_s1_model.py full docs/data/passport_pilot_d40/full/passport_L12.json \
        --store <store L12.json> --catalog <motor_catalog.json> --card cat_ciano14_40_new --magnet-limit 150

    # a passport-store record only (Ø85 L13: the full record is not kept): torque and
    # total motor loss from the drive variant's points, copper split by R at the
    # temperature the record was computed at, flux slope from the magnet card
    python scripts/export_propeller_s1_model.py store <store L13.json> --catalog ... --card cat_ciano28_85 \
        --r-ref 0.093962 --r-temp 149.3 --coil-c 180 --magnet-c 150 --flux-coeff -0.001199 --magnet-limit 150

Writes to ``config/propeller_s1/<die>/<config>.json`` (``--out`` overrides).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
ALPHA_CU = 0.00393


def _load(p: str) -> Dict[str, Any]:
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _val(d: Any) -> Optional[float]:
    if isinstance(d, dict):
        d = d.get("value")
    try:
        x = float(d)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _key(rpm: float, i: float) -> tuple:
    return (round(float(rpm), 1), round(float(i), 2))


def _variant(store: Optional[Dict[str, Any]], vid: Optional[str]) -> Optional[Dict[str, Any]]:
    if not store:
        return None
    vs = [v for v in store.get("pwm_variants") or [] if isinstance(v, dict) and v.get("points")]
    if vid:
        vs = [v for v in vs if v.get("id") == vid]
    return vs[0] if vs else None


def _variant_tables(v: Dict[str, Any]) -> Dict[str, Any]:
    rpm = [float(x) for x in v["grid"]["rpm"]]
    cur = [float(x) for x in v["grid"]["I_A"]]
    by = {_key(p["rpm"], p["I_A"]): p for p in v["points"].values() if isinstance(p, dict) and "rpm" in p}

    def table(fn) -> List[List[Optional[float]]]:
        out = []
        for r in rpm:
            row = []
            for i in cur:
                p = by.get(_key(r, i))
                row.append(None if (p is None or p.get("status")) else fn(p))
            out.append(row)
        return out

    def ctrl(p):
        inv = p.get("inverter_loss_W") or {}
        if not inv:
            return None
        return float(sum(float(x or 0) for x in inv.values())) + float(p.get("board_copper_W") or 0.0)

    return {"rpm": rpm, "I_A": cur, "table": table, "ctrl": table(ctrl)}


def _thermal_geom(catalog: str, card_id: str, stack_mm: float) -> Dict[str, float]:
    cat = _load(catalog)
    card = next((m for m in cat.get("motors", []) if m.get("id") == card_id), None)
    if card is None:
        raise SystemExit(f"card {card_id!r} not in {catalog}")
    sp = card["passport"]
    geo, fit = sp["geo"], sp["fit"]
    # the same derivation as ConfiguratorPanel.tsx thermalGeom
    return {"statorOD_mm": 2 * geo["statorOR_mm"], "stackLength_mm": float(stack_mm),
            "numSlots": geo["numSlots"], "slotHeight_mm": fit["slotHeight_mm"],
            "slotWidth_mm": fit["slotWidth_mm"], "insulation_mm": fit["insulation_mm"],
            "coreThickness_mm": max(0.0, geo["statorOR_mm"] - geo["statorIR_mm"] - fit["slotHeight_mm"]),
            "airGap_mm": max(0.0, geo["statorIR_mm"] - geo["rotorOR_mm"]),
            "magnetOD_mm": 2 * geo["rotorOR_mm"], "source": f"catalogue card {card_id} (passport fit/geo)"}


def _controller(v: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not v:
        return None
    t = _variant_tables(v)
    return {"variant": {"id": v.get("id"), "device": v.get("device"), "carrier_hz": v.get("carrier_hz"),
                        "bus_evaluated": v.get("bus_evaluated")},
            "rpm": t["rpm"], "I_A": t["I_A"], "loss_W": t["ctrl"],
            "basis": "inverter conduction + switching + dead time + board copper, passport drive variant"}


def from_full(args) -> Dict[str, Any]:
    d = _load(args.source)
    store = _load(args.store) if args.store else None
    v = _variant(store, args.variant)
    temps = d["inputs"]["temperatures"]
    plan = d["loss_grid"]["plan"]
    t_coil, t_mag, t_cold = float(temps["hot_coil_c"]), float(temps["hot_magnet_c"]), float(temps["cold_c"])
    r_hot = float(plan["R_hot_ohm"])
    ch, cc = d["card"]["constants_hot"], d["card"]["constants_cold"]
    if abs(_val(ch["R_phase_ohm"]) - r_hot) > 1e-9:
        raise SystemExit("R_hot of the loss grid differs from constants_hot.R_phase_ohm")
    psi_h, psi_c = _val(ch["psi_PM_Wb"]), _val(cc["psi_PM_Wb"])
    c_psi = (psi_h / psi_c - 1.0) / (t_mag - t_cold)
    rpm = [float(x) for x in plan["speeds"]]
    cur = [float(x) for x in plan["currents"]]
    by = {_key(p["rpm"], p["I_rms"]): p for p in d["loss_grid"]["points"] if p.get("rpm") is not None}

    def table(fn):
        out = []
        for r in rpm:
            row = []
            for i in cur:
                p = by.get(_key(r, i))
                row.append(None if p is None else fn(p))
            out.append(row)
        return out

    g = lambda p, k: float((p.get("groups_W") or {}).get(k) or 0.0)       # noqa: E731
    loss = {"cu_ac": table(lambda p: g(p, "P_cu_ac_W") + g(p, "P_cu_circulating_W")),
            "fe_stator": table(lambda p: g(p, "P_fe_stator_W")),
            "fe_rotor": table(lambda p: g(p, "P_fe_rotor_W")),
            "rotor_eddy": table(lambda p: g(p, "P_mag_W") + g(p, "P_shaft_W") + g(p, "P_sleeve_W"))}
    labels = ["2-D FEM loss grid (settled TDM, coupled eddy + demag) of the full v1 passport",
              f"torque x k_T(3-D) {(_val(d['stage2_3d']['k_T']) or 1.0):.4f}"]
    if v is not None:
        vt = _variant_tables(v)
        if vt["rpm"] == rpm and vt["I_A"] == cur:
            pw = vt["table"](lambda p: p.get("motor_pwm_loss_W"))
            # keep the PWM extra loss only where the base grid is feasible too
            if all((pw[a][b] is not None) == (by.get(_key(rpm[a], cur[b])) is not None)
                   for a in range(len(rpm)) for b in range(len(cur))):
                loss["pwm_extra"] = [[None if x is None else float(x) for x in row] for row in pw]
                labels.append(f"motor PWM extra loss of drive variant {v.get('id')} (FEM at a few points, scaled elsewhere)")
    mech = d["loss_grid"].get("mech") or {}
    mech_pts = sorted((float(k), float(x["P_W"])) for k, x in mech.items())
    stack = (v or {}).get("build", {}).get("length_mm") if v else None
    stack = float(args.stack_mm or stack or d["inputs"]["mesh"].get("motor_length") or 0)
    if not stack:
        raise SystemExit("stack length unknown: pass --stack-mm")
    m = {
        "schema": "propeller-s1-model-1",
        "die": d["machine"]["die"], "config": d["machine"]["configuration"],
        "source": {"kind": "full v1 passport", "file": Path(args.source).as_posix().split("/docs/")[-1]
                   if "/docs/" in Path(args.source).as_posix() else Path(args.source).name,
                   "snapshot_sha256": d["provenance"].get("snapshot_sha256"),
                   "store_variant": (v or {}).get("id")},
        "R_phase_ohm": r_hot, "R_temp_C": t_coil,
        "magnet_ref_temp_C": t_mag,
        "flux_temp_coeff_per_K": c_psi,
        "flux_coeff_basis": f"passport psi_PM hot {psi_h:.6g} Wb @ {t_mag:g} C vs cold {psi_c:.6g} Wb @ {t_cold:g} C",
        "torque_factor": _val(d["stage2_3d"]["k_T"]) or 1.0,
        "grid": {"rpm": rpm, "I_A": cur, "T_Nm": table(lambda p: float(p["T_Nm"])), "loss_W": loss},
        "mech_W": {"rpm": [x[0] for x in mech_pts], "W": [x[1] for x in mech_pts],
                   "basis": "bearings (SKF model) + windage, passport"},
        "controller": _controller(v),
        "limits": {"I_max_A": _val(d["demag_limit"]["I_limit_rms"]),
                   "I_max_basis": "passport demagnetisation current limit at the hot magnet temperature "
                                  f"({t_mag:g} C): {d['demag_limit'].get('how') or d['demag_limit'].get('rule')}",
                   "rpm_max": float(plan["n_max_rpm"]),
                   "rpm_max_basis": f"passport speed limit at v_nom {plan.get('v_dc')} V, m = {plan.get('m')} "
                                    "(voltage, characterised field weakening)",
                   "winding_C": 180.0, "winding_basis": "insulation class H (owner default)",
                   "magnet_C": float(args.magnet_limit),
                   "magnet_basis": args.magnet_basis},
        "thermal_geom": _thermal_geom(args.catalog, args.card, stack),
        "labels": labels,
    }
    return m


def from_store(args) -> Dict[str, Any]:
    s = _load(args.source)
    v = _variant(s, args.variant)
    if v is None:
        raise SystemExit("store record has no usable drive variant")
    t = _variant_tables(v)
    r_ref, t_ref, t_coil = float(args.r_ref), float(args.r_temp), float(args.coil_c)
    r_coil = r_ref * (1 + ALPHA_CU * (t_coil - 20)) / (1 + ALPHA_CU * (t_ref - 20))

    def rest(p):
        I = float(p["I_A"])
        x = float(p["P_motor_sine_W"]) - float(p.get("P_mech_W") or 0.0) - 3 * I * I * r_coil
        if x < -0.02 * float(p["P_motor_sine_W"]):
            raise SystemExit(f"negative non-copper loss {x:.2f} W at {p['rpm']} rpm / {I} A: the coil "
                             f"temperature {t_coil} C does not match the record")
        return max(x, 0.0)

    mech = {}
    for p in v["points"].values():
        if isinstance(p, dict) and p.get("P_mech_W") is not None:
            mech[float(p["rpm"])] = float(p["P_mech_W"])
    mech_pts = sorted(mech.items())
    stack = float(args.stack_mm or v.get("build", {}).get("length_mm") or 0)
    return {
        "schema": "propeller-s1-model-1",
        "die": s["machine"]["die"], "config": s["machine"]["configuration"],
        "source": {"kind": "passport store record (drive variant points)", "file": args.source_name or Path(args.source).name,
                   "store_variant": v.get("id"), "record_source": s.get("source")},
        "R_phase_ohm": r_ref, "R_temp_C": t_ref,
        "magnet_ref_temp_C": float(args.magnet_c),
        "flux_temp_coeff_per_K": float(args.flux_coeff),
        "flux_coeff_basis": args.flux_basis,
        "torque_factor": 1.0,
        "grid": {"rpm": t["rpm"], "I_A": t["I_A"], "T_Nm": t["table"](lambda p: float(p["T_op_Nm"])),
                 "loss_W": {"fe_stator": t["table"](rest)}},
        "mech_W": ({"rpm": [x[0] for x in mech_pts], "W": [x[1] for x in mech_pts], "basis": "passport store"}
                   if any(x[1] > 0 for x in mech_pts) else None),
        "controller": _controller(v),
        "limits": {"I_max_A": float(t["I_A"][-1]),
                   "I_max_basis": args.imax_basis,
                   "rpm_max": float(t["rpm"][-1]),
                   "rpm_max_basis": "top of the passport store speed grid (no voltage envelope in the record)",
                   "winding_C": 180.0, "winding_basis": "insulation class H (owner default)",
                   "magnet_C": float(args.magnet_limit), "magnet_basis": args.magnet_basis},
        "thermal_geom": _thermal_geom(args.catalog, args.card, stack),
        "labels": [f"torque = operating torque T_op of drive variant {v.get('id')} (store record)",
                   f"losses: total sine motor loss of the record at coil {t_coil:g} C / magnet {args.magnet_c} C; "
                   "DC copper split off with R(T), the rest (iron + AC + eddy, not split in the record) put on the stator",
                   "flux vs magnet temperature from the magnet card's Br coefficient (no cold/hot pair in the record)"],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kind", choices=["full", "store"])
    ap.add_argument("source")
    ap.add_argument("--store", help="passport store record (drive variants) for the full kind")
    ap.add_argument("--variant", help="drive variant id (default: the first, Configure's default)")
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--card", required=True)
    ap.add_argument("--stack-mm", type=float)
    ap.add_argument("--magnet-limit", type=float, required=True)
    ap.add_argument("--magnet-basis", default="magnet card max_working_temp_c (F52SH_120C, SH class 150 C)")
    ap.add_argument("--r-ref", type=float)
    ap.add_argument("--r-temp", type=float)
    ap.add_argument("--coil-c", type=float)
    ap.add_argument("--magnet-c", type=float)
    ap.add_argument("--flux-coeff", type=float)
    ap.add_argument("--flux-basis", default="magnet card alpha(Br) -0.1199 %/K (F52SH_120C)")
    ap.add_argument("--imax-basis", default="top of the passport store current grid (the peak duty current)")
    ap.add_argument("--source-name")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    if a.kind == "store" and None in (a.r_ref, a.r_temp, a.coil_c, a.magnet_c, a.flux_coeff):
        ap.error("store needs --r-ref --r-temp --coil-c --magnet-c --flux-coeff")
    m = from_full(a) if a.kind == "full" else from_store(a)
    out = Path(a.out) if a.out else ROOT / "config" / "propeller_s1" / m["die"] / f"{m['config']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(m, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
