"""Surrogate-guided + FEM-confirmed geometry optimization of one motor.

Pipeline:
  1. write the motor's final.yaml into config (so refine_proc reads it as base + rpm)
  2. LHS-sample the geometry whitelist; score each candidate with the analytical
     surrogate (evaluate_design) — instant, thousands/sec
  3. pre-filter by buildability (the cut_x slot-opening constraint that the CadQuery
     build needs) + surrogate feasibility
  4. rank by torque-density and by efficiency; take the top few distinct
  5. FEM-confirm those + the baseline via refine_proc.run_one at the motor's MTPA γ
     (real sliding-band transient: torque, efficiency, ripple, losses, mass, V)

Keeps wire / turns fixed (spec) — optimizes the magnetic circuit (magnet + iron
proportions) only. Does NOT save anything; it reports a table to decide from.

Usage:  PYTHONPATH=src python design_runs/optimize_motor.py FINAL.yaml I rpm gamma [N] [topk] [mesh] [n_sectors]
"""
from __future__ import annotations
import sys, math
from pathlib import Path
import yaml
import numpy as np
from scipy.stats import qmc

from motor_ai_sim.config import DEFAULT_CONFIG_PATH, clear_config_cache, get_config
from motor_ai_sim.optimization.design_eval import evaluate_design
from motor_ai_sim.optimization.refine_proc import run_one
from motor_ai_sim.geometry_constraints import clamp as clamp_geo

FINAL = sys.argv[1]
CUR   = float(sys.argv[2])
RPM   = float(sys.argv[3])
GAMMA = float(sys.argv[4])
N     = int(sys.argv[5]) if len(sys.argv) > 5 else 4000
TOPK  = int(sys.argv[6]) if len(sys.argv) > 6 else 8
MESH  = float(sys.argv[7]) if len(sys.argv) > 7 else 0.8
NSEC  = int(sys.argv[8]) if len(sys.argv) > 8 else 2

ROOT = Path(__file__).parent.parent
CFG = Path(DEFAULT_CONFIG_PATH)

# 1 ── write motor config (base for refine_proc + rpm) ────────────────────────
raw = yaml.safe_load((ROOT / "design_runs" / FINAL).read_text(encoding="utf-8"))
raw["simulation"]["rpm"] = RPM
raw["simulation"]["max_current"] = CUR
CFG.write_text(yaml.dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
clear_config_cache()

cfg = get_config()
base_geo = {k: v for k, v in cfg["geometry"].items() if isinstance(v, (int, float))}
wind = dict(cfg.get("winding", {}))
sim = dict(cfg.get("simulation", {}))
schema = cfg.get("geometry_schema", {})


def buildable(g: dict) -> bool:
    """The slot cutter must leave room: cut_x < (stator_IR + cut_width)·sin(180/half_slots).
    This is the constraint whose violation makes fill_r negative -> shapely TopologyException."""
    half = g["num_seg"] * g["num_slots_per_segment"] / 2.0
    sIR = g["stator_diameter"] / 2.0 - g["core_thickness"] - g["slot_height"]
    if sIR <= 0 or half <= 0:
        return False
    cut_x = (g["tooth_width"] / 2.0 + 2 * g["insulation_thickness"] + g["wire_width"]
             + 2 * g["wire_spacing_x"] + g["tooth2_width"])
    limit = (sIR + g["cut_width"]) * math.sin(math.radians(180.0 / half))
    return cut_x < 0.95 * limit


# 2 ── variables: band around the BASELINE value (NOT the 200mm-oriented schema —
#       its mins, e.g. tooth_width≥4, don't fit a 40mm motor). num_wires_per_slot
#       is discrete; the 40mm has big voltage headroom so turns can grow. ──────────
def _band(cur, frac_lo, frac_hi, floor=0.3):
    return (max(floor, cur * frac_lo), cur * frac_hi)

VARS = [
    ("core_thickness",   *_band(base_geo["core_thickness"], 0.6, 1.5)),
    ("slot_height",      *_band(base_geo["slot_height"], 0.85, 1.5)),   # up = thicker wire fits
    ("tooth_width",      *_band(base_geo["tooth_width"], 0.7, 1.45)),
    ("tooth2_width",     *_band(base_geo["tooth2_width"], 0.6, 1.5)),
    ("magnet_height",    *_band(base_geo["magnet_height"], 0.7, 1.55)),
    ("magnet_fill_up",   max(0.3, base_geo["magnet_fill_up"] * 0.7), min(0.95, base_geo["magnet_fill_up"] * 1.25)),
    ("magnet_up_gap",    *_band(base_geo["magnet_up_gap"], 0.5, 1.6)),
]
# turns: vary by default; "fixturns" arg pins them (e.g. the 100mm is voltage-limited)
if "fixturns" in sys.argv:
    NWIRE = [int(base_geo["num_wires_per_slot"])]
else:
    NWIRE = sorted({int(base_geo["num_wires_per_slot"]) + d for d in (-1, 0, 1, 2) if int(base_geo["num_wires_per_slot"]) + d >= 1})
print("variables:", {v: (round(lo, 2), round(hi, 2)) for v, lo, hi in VARS}, "| turns:", NWIRE)

# 3 ── surrogate LHS sampling (pre-clamp each candidate so the surrogate sees the
#       SAME wire-thinning the FEM build applies to fit the slot) ───────────────
dim = len(VARS)
lo = np.array([x[1] for x in VARS]); hi = np.array([x[2] for x in VARS])
unit = qmc.LatinHypercube(d=dim, seed=7).random(n=N)
samp = qmc.scale(unit, lo, hi)
cands = []
n_build = 0
for ri, row in enumerate(samp):
    ov = {VARS[k][0]: float(row[k]) for k in range(dim)}
    ov["num_wires_per_slot"] = NWIRE[ri % len(NWIRE)]
    g = {**base_geo, **ov}
    if not buildable(g):
        continue
    g, _ = clamp_geo(g)                       # thin wire_height to fit the slot
    ov["wire_height"] = g["wire_height"]
    n_build += 1
    m = evaluate_design(g, wind, sim, GAMMA, CUR, RPM, coil_temp_c=120.0)
    if m.feasible and m.T_em_Nm > 0:
        cands.append((ov, m))
mb = evaluate_design(base_geo, wind, sim, GAMMA, CUR, RPM, coil_temp_c=120.0)
print(f"surrogate: {N} sampled, {n_build} buildable, {len(cands)} feasible. "
      f"baseline surrogate: T={mb.T_em_Nm:.3f} eff={mb.efficiency:.4f}")

# 4 ── pick top distinct by ABSOLUTE torque and by efficiency ──────────────────
cands.sort(key=lambda c: -c[1].T_em_Nm); top_t = cands[:TOPK]
cands.sort(key=lambda c: -c[1].efficiency); top_eff = cands[:5]
sel, seen = [], set()
for ov, m in top_t + top_eff:
    key = tuple(round(ov.get(v, 0), 2) for v, _, _ in VARS) + (ov.get("num_wires_per_slot"),)
    if key in seen:
        continue
    seen.add(key); sel.append((ov, m))


# 5 ── FEM-confirm baseline + selected ────────────────────────────────────────
def fem(ov):
    return run_one(ov, CUR, steps=8, coil_temp_c=120.0, n_periods=1.0, gamma_deg=GAMMA,
                   mesh_size_mm=MESH, min_size_mm=0.3, n_sectors=NSEC, rotor_eddy=True,
                   gap_layers=1.0)

print(f"\nFEM-confirming baseline + {len(sel)} candidates at γ={GAMMA} (mesh {MESH}, sec {NSEC})...")
try:
    b = fem({})
    print(f"  BASELINE   T={b['T_em_Nm']:.3f}  eff={b['efficiency']:.4f}  rip={b['T_ripple_pct']:.1f}%  "
          f"Pcu={b['P_cu_W']:.0f} Pfe={b['P_fe_W']:.0f}  V={b['V_peak']:.1f}  m={b['mass_total_kg']:.3f}")
except Exception as e:
    print("  BASELINE FEM FAILED:", str(e)[:80]); b = None

rows = []
for i, (ov, m) in enumerate(sel):
    try:
        r = fem(ov)
        rows.append((ov, r))
        d = {k: round(ov[k], 2) for k in ov}
        print(f"  [{i:2d}] T={r['T_em_Nm']:.3f}  eff={r['efficiency']:.4f}  rip={r['T_ripple_pct']:.1f}%  "
              f"Pcu={r['P_cu_W']:.0f} Pfe={r['P_fe_W']:.0f}  V={r['V_peak']:.1f}  m={r['mass_total_kg']:.3f}  {d}")
    except Exception as e:
        print(f"  [{i:2d}] FEM FAILED: {str(e)[:70]}")

# 6 ── summarize best by torque and by efficiency ─────────────────────────────
if rows:
    bt = max(rows, key=lambda r: r[1]["T_em_Nm"])
    be = max(rows, key=lambda r: r[1]["efficiency"])
    print(f"\nBEST TORQUE : T={bt[1]['T_em_Nm']:.3f} eff={bt[1]['efficiency']:.4f} rip={bt[1]['T_ripple_pct']:.1f}%  {bt[0]}")
    print(f"BEST EFF    : T={be[1]['T_em_Nm']:.3f} eff={be[1]['efficiency']:.4f} rip={be[1]['T_ripple_pct']:.1f}%  {be[0]}")
