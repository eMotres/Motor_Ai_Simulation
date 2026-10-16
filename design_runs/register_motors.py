"""Register the two optimized motors (40 mm, 100 mm) into the app's library:
  - config/motor_presets.json : a loadable preset (geometry + operating point + mesh)
  - config/motor_catalog.json : an enriched MOTORS-tab card (perf + materials)

Idempotent: re-running replaces the same ids. Reads the frozen final configs in
design_runs/. Run:  PYTHONPATH=src python design_runs/register_motors.py
"""
from __future__ import annotations
import json
from pathlib import Path
import yaml

ROOT = Path(__file__).parent.parent
PRESETS = ROOT / "config" / "motor_presets.json"
CATALOG = ROOT / "config" / "motor_catalog.json"
DR = ROOT / "design_runs"

# ── achieved design points (see MOTOR_RESULTS.md) ────────────────────────────
MOTORS = [
    dict(
        preset_id="motor_40mm", final="motor_40mm_final.yaml",
        name="40 mm · 12s/14p high-speed",
        rpm=12000, current_a=35, gamma_deg=-42,
        T_avg_Nm=0.444, power_w=558, efficiency_pct=91.4, voltage_pk_v=9.7,
        magnet="F45SH", steel="20SW1200", length_mm=12, wire="2.5×0.6 · 8 turns",
        steps_per_period=16, mesh_size_mm=0.6, n_sectors=2,
        description=("40 mm spoke-PM inrunner, 12 slots / 14 poles. 0.44 N·m, 558 W @ "
                     "12 000 rpm, 91.4 % eff at 35 A. 6-cell (18–25 V) bus, lots of "
                     "voltage headroom. F45SH magnets, 20SW1200 steel. MTPA γ=−42°."),
    ),
    dict(
        preset_id="motor_100mm", final="motor_100mm_final.yaml",
        name="100 mm · 24s/28p mid-torque",
        rpm=3800, current_a=46, gamma_deg=-36,
        T_avg_Nm=6.0, power_w=2395, efficiency_pct=93.1, voltage_pk_v=29.4,
        magnet="F45SH", steel="JFE 20JNEH1200", length_mm=15, wire="3.2×0.65 · 10t",
        steps_per_period=14, mesh_size_mm=1.0, n_sectors=4,
        description=("100 mm spoke-PM inrunner, 24 slots / 28 poles. 6.0 N·m, 2.39 kW @ "
                     "3 800 rpm, 93.1 % eff (FEM) at 46 A, 29.4 V phase-peak — fits the 14-cell "
                     "(≈50 V) bus. OPTIMIZED: teeth widened 5.0→6.5 mm to relieve saturation + "
                     "10 turns of thicker wire → +2.8 pp efficiency vs the first cut, same torque "
                     "& voltage. F45SH magnets, JFE 20JNEH1200 steel. MTPA γ=−36°."),
    ),
]


def _numeric_geo(geo: dict) -> dict:
    return {k: v for k, v in geo.items() if isinstance(v, (int, float))}


def main() -> None:
    presets = json.loads(PRESETS.read_text(encoding="utf-8")) if PRESETS.exists() else {}
    cat = json.loads(CATALOG.read_text(encoding="utf-8")) if CATALOG.exists() else {}
    cat.setdefault("tiers", []); cat.setdefault("diameters_mm", []); cat.setdefault("motors", [])

    base_order = 1 + max([p.get("order", 0) for p in presets.values()] or [0])
    for i, m in enumerate(MOTORS):
        raw = yaml.safe_load((DR / m["final"]).read_text(encoding="utf-8"))
        geo = _numeric_geo(raw["geometry"])
        wind = raw.get("winding", {})
        dia = int(round(geo["stator_diameter"]))
        slots = int(round(geo["num_seg"] * geo["num_slots_per_segment"]))
        poles = int(round(geo["num_seg"] * geo["num_poles_per_segment"]))

        # ── preset (loadable) ────────────────────────────────────────────────
        presets[m["preset_id"]] = {
            "id": m["preset_id"], "name": m["name"], "description": m["description"],
            "order": base_order + i,
            "metrics": {"T_avg_Nm": m["T_avg_Nm"], "efficiency": m["efficiency_pct"] / 100.0,
                        "power_w": m["power_w"]},
            "geometry": geo,
            "winding": dict(wind),
            "simulation": {"max_current": m["current_a"], "rpm": float(m["rpm"]),
                           "phase_offset_deg": float(m["gamma_deg"]),
                           "steps_per_period": m["steps_per_period"],
                           "demag": False, "coil_temp_c": 120},
            "mesh": {"mesh_size_mm": m["mesh_size_mm"], "n_sectors": m["n_sectors"],
                     "gap_layers": 1.0},
        }

        # ── catalog card (enriched) ──────────────────────────────────────────
        cid = f"cat_{m['preset_id']}"
        entry = {
            "id": cid, "diameter_mm": dia, "name": m["name"],
            "topology": "Spoke-PM SPMSM", "slots": slots, "poles": poles,
            "rpm": m["rpm"], "current_a": m["current_a"],
            "T_avg_Nm": m["T_avg_Nm"], "ripple_pct": None, "gamma_deg": m["gamma_deg"],
            "power_w": m["power_w"], "efficiency_pct": m["efficiency_pct"],
            "voltage_pk_v": m["voltage_pk_v"], "magnet": m["magnet"], "steel": m["steel"],
            "length_mm": m["length_mm"], "wire": m["wire"],
            "tier": "free", "description": m["description"], "preset": m["preset_id"],
        }
        if dia not in cat["diameters_mm"]:
            cat["diameters_mm"] = sorted(set([*cat["diameters_mm"], dia]))
        cat["motors"] = [x for x in cat["motors"] if x.get("id") != cid] + [entry]
        print(f"registered {m['preset_id']}: Ø{dia} {slots}s/{poles}p  "
              f"{m['T_avg_Nm']} N·m / {m['power_w']} W / {m['efficiency_pct']}%")

    PRESETS.write_text(json.dumps(presets, indent=2, ensure_ascii=False), encoding="utf-8")
    CATALOG.write_text(json.dumps(cat, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {PRESETS.name} ({len(presets)} presets) + {CATALOG.name} "
          f"({len(cat['motors'])} catalog motors)")


if __name__ == "__main__":
    main()
