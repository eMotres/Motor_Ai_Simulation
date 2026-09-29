"""Re-baseline ``config/motor_catalog.json`` from a solver-trials campaign (F7).

The catalog's stored performance numbers are historical and largely not
reproducible (docs/SOLVER_TRIALS_2026-07-30.md F7): deltas against a measured
run ran from +112 % to -48 %, and the two worst cases were a copy-pasted entry
and a missing winding connection rather than a solver error. Now that the speed
and the winding connection are per-request ARGUMENTS (F2/F3), a trial reproduces
each entry's own operating point exactly, so the numbers can be re-measured and
recorded.

What this writes, per catalog motor:

* ``reference_*``        - the measured numbers, from THIS campaign
* ``legacy_reference_*`` - the values the entry carried before, kept verbatim
* ``reference_provenance`` - what produced them: the trial key + timestamp, the
  protocol, the operating point, the winding connection actually applied, the
  gate results, and the ampere-turn scale ``k`` that says how far the solved
  machine sits from the requested one (F4/F5).

The DISPLAY fields (``T_avg_Nm``, ``ripple_pct``, ...) are deliberately left
alone. They are what the UI shows, and replacing them is a product decision, not
a measurement one — especially while F4/F5 is open, since every measured number
carries the ``k`` caveat recorded beside it.

Usage
-----
    python scripts/catalog_rebaseline.py --plan     # which presets need a run
    python scripts/catalog_rebaseline.py --write    # read the jsonl, patch the catalog

Run the trials themselves with the normal harness, against a FROZEN copy of the
config dir so a live backend cannot move the inputs mid-campaign::

    SOLVER_TRIALS_INPUT_DIR=<snapshot> python scripts/solver_trials.py --keys K1 K2 ...
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

CATALOG = ROOT / "config" / "motor_catalog.json"
RESULTS = ROOT / "scripts" / "_solver_trials_results.jsonl"

# Fields copied to legacy_reference_* before anything is added.
LEGACY_FIELDS = ("T_avg_Nm", "ripple_pct", "efficiency_pct", "power_w",
                 "voltage_pk_v", "mass_kg", "rpm", "current_a", "gamma_deg")


def _load(path: Path) -> Any:
    with io.open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _trial_key_for(preset: str) -> Optional[str]:
    """The solver-trials key that carries this preset's geometry.

    Trials dedupe by geometry, so a preset whose cross-section is byte-identical
    to an earlier one appears only as an ALIAS of that key (``my_motor`` /
    ``ciano14_30_10``). Resolve through the alias list rather than assuming the
    preset name is a key."""
    from solver_trials import build_motor_set
    for m in build_motor_set():
        if m["key"] == preset or preset in (m.get("aliases") or []):
            return m["key"]
    return None


def _records() -> List[Dict[str, Any]]:
    if not RESULTS.exists():
        return []
    return [json.loads(ln) for ln in io.open(RESULTS, encoding="utf-8") if ln.strip()]


def _latest_ok(key: str, after_ts: float = 0.0) -> Optional[Dict[str, Any]]:
    """The most recent SUCCESSFUL plain (non-variant) record for this key."""
    best = None
    for r in _records():
        if r.get("key") != key or not r.get("ok"):
            continue
        if float(r.get("ts_start") or 0.0) < after_ts:
            continue
        if best is None or float(r.get("ts_start") or 0) > float(best.get("ts_start") or 0):
            best = r
    return best


def _ampere_turn_scale(geo: Dict[str, Any]) -> Optional[float]:
    """k_legacy = A_copper_per_slot / (slot_width_m*slot_height_m*0.6).

    HISTORICAL ONLY. This used to be the factor by which the solved machine's
    coil MMF differed from the one that was requested — the solver normalised
    the winding source by that nominal rectangle while applying it over the real
    copper (docs/SOLVER_TRIALS_2026-07-30.md, F4+F5). The source is normalised
    by the slot's actual copper area now, so the excitation is exactly n_wires·I
    on every geometry and the live k is 1 by construction.

    It is still recorded per entry because it is exactly how far this entry's
    PREVIOUS `reference_*` numbers were off: the old Maxwell torque and ripple %
    over-read by this factor. Keeping it makes the re-baseline auditable instead
    of a silent jump."""
    try:
        from motor_ai_sim.cadquery_geometry import CadQueryMotor
        mot = CadQueryMotor()
        mot.set_parameters(dict(geo))
        polys = mot.get_2d_polygons(rotor_angle_deg=0.0)
        a_cu = sum(float(c.area) for c in (polys.get("coils") or []))
        n_slots = int(geo.get("num_slots")
                      or int(geo.get("num_seg", 1) or 1)
                      * int(geo.get("num_slots_per_segment", 0) or 0))
        slot_w = (float(geo["wire_width"])
                  + 2 * float(geo.get("wire_spacing_x", 0.0) or 0.0)
                  + 2 * float(geo.get("insulation_thickness", 0.0) or 0.0))
        slot_area = slot_w * float(geo["slot_height"]) * 0.6
        return round((a_cu / max(1, n_slots)) / slot_area, 4)
    except Exception:
        return None


def plan() -> None:
    cat = _load(CATALOG)
    print(f"{'catalog id':30s} {'preset':26s} {'trial key':26s} {'have record'}")
    for m in cat.get("motors", []):
        pre = str(m.get("preset") or "")
        key = _trial_key_for(pre) if pre else None
        rec = _latest_ok(key) if key else None
        print(f"{m['id']:30s} {pre:26s} {str(key):26s} "
              f"{'yes ' + time.strftime('%H:%M', time.localtime(rec['ts_start'])) if rec else 'NO'}")


def write(after_ts: float = 0.0, dry: bool = False) -> None:
    cat = _load(CATALOG)
    n_done = n_skip = 0
    for m in cat.get("motors", []):
        pre = str(m.get("preset") or "")
        key = _trial_key_for(pre) if pre else None
        rec = _latest_ok(key, after_ts) if key else None
        if rec is None:
            m["reference_provenance"] = {
                "status": "NOT re-measured",
                "reason": (f"preset {pre!r} is not in config/motor_presets.json"
                           if pre and not key else
                           f"no successful solver_trials record for {key or pre!r}"),
                "checked_at": time.strftime("%Y-%m-%d %H:%M"),
            }
            n_skip += 1
            continue

        met = rec.get("metrics") or {}
        op = rec.get("operating_point") or {}
        wind = rec.get("winding_used") or {}
        gates = rec.get("gates") or {}

        # Keep whatever the entry carried, verbatim, before adding anything.
        for f in LEGACY_FIELDS:
            if f in m and f"legacy_reference_{f}" not in m:
                m[f"legacy_reference_{f}"] = m[f]

        m["reference_T_avg_Nm"] = met.get("T_avg_Nm")
        m["reference_T_avg_maxwell_Nm"] = met.get("T_avg_maxwell_Nm")
        m["reference_ripple_pct"] = (round(float(met["T_ripple_raw_pct"]), 2)
                                     if met.get("T_ripple_raw_pct") is not None else None)
        m["reference_efficiency_pct"] = (round(float(met["efficiency"]) * 100.0, 2)
                                         if met.get("efficiency") is not None else None)
        m["reference_P_mech_W"] = (round(float(met["P_mech_avg_W"]), 1)
                                   if met.get("P_mech_avg_W") is not None else None)
        m["reference_P_loss_W"] = met.get("P_loss_total_W")
        m["reference_V_peak_V"] = (round(float(met["V_peak_V"]), 2)
                                   if met.get("V_peak_V") is not None else None)
        m["reference_rpm"] = op.get("rpm")
        m["reference_current_a"] = op.get("I_phase_rms_A")
        m["reference_gamma_deg"] = op.get("gamma_deg")

        k = _ampere_turn_scale(rec.get("geometry_used") or {})
        m["reference_provenance"] = {
            "status": "measured",
            "source": "scripts/solver_trials.py",
            "trial_key": rec.get("key"),
            "measured_at": time.strftime("%Y-%m-%d %H:%M",
                                         time.localtime(rec.get("ts_start") or 0)),
            "wall_s": rec.get("wall_s"),
            "geometry_sha256": rec.get("geometry_sha256"),
            "geometry_from_preset": pre,
            "aliased_trial_key": (key if key != pre else None),
            "solver": {k2: (rec.get("solver") or {}).get(k2)
                       for k2 in ("method", "element_order", "n_steps_per_period",
                                  "n_parallel", "drive")},
            "protocol": rec.get("protocol"),
            "operating_point_source": op.get("path"),
            "winding": {"connection": wind.get("connection"),
                        "n_parallel": wind.get("n_parallel"),
                        "source": wind.get("source")},
            "materials": (rec.get("materials_used") or {}).get("fallback"),
            "speed_channel": (rec.get("speed_handling") or {}).get("channel"),
            "gates": {g: (v or {}).get("pass") for g, v in gates.items()},
            "energy_vs_maxwell_pct": (gates.get("b_energy_vs_maxwell") or {}).get("rel_diff_pct"),
            "ampere_turn_scale_k": 1.0,
            "legacy_ampere_turn_scale_k": k,
            "caveat": (
                "The winding source is normalised by the slot's real copper "
                "area, so this run was excited at exactly n_wires*I "
                "ampere-turns (k = 1). legacy_ampere_turn_scale_k is how far "
                "this entry's PREVIOUS reference numbers were off: the old "
                "T_avg_maxwell and ripple % over-read by that factor. T_avg is "
                "still the energy/flux-linkage mean; what is left between it "
                "and T_avg_maxwell is the sliding-band Maxwell residual - see "
                "docs/SOLVER_TRIALS_2026-07-30.md, F4+F5 (fixed)."),
        }
        n_done += 1

    if dry:
        print(json.dumps(cat, indent=1, ensure_ascii=False)[:4000])
    else:
        # Byte-for-byte the writer the file already uses (indent=2,
        # ensure_ascii=False, no trailing newline), so the diff is the fields
        # that changed and not a reformat of a 300 kB file full of thumb SVGs.
        with io.open(CATALOG, "w", encoding="utf-8", newline="") as fh:
            fh.write(json.dumps(cat, indent=2, ensure_ascii=False))
    print(f"re-baselined {n_done} entries, {n_skip} left un-measured "
          f"({'dry run' if dry else CATALOG})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--dry", action="store_true", help="with --write: print, do not save")
    ap.add_argument("--after", type=float, default=0.0,
                    help="only accept trial records started after this unix ts")
    a = ap.parse_args()
    if a.plan:
        plan()
    elif a.write:
        write(after_ts=a.after, dry=a.dry)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
