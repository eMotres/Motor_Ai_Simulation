"""High-resolution (steps=120) re-verification of the overnight campaign's top
candidates — steps=60 ripple is noisy ~±1.5 pp, so re-evaluate each finalist at
2x resolution at its own (torque-77.7-solving) current for trustworthy numbers.
Standalone (direct run_one) — no backend needed. Writes config/verify_steps120.json."""
import json, math, sys
from motor_ai_sim.optimization.refine_proc import run_one

DS = "config/.opt_dataset.jsonl"
recs = [json.loads(l) for l in open(DS, encoding="utf-8") if l.strip()]
feas = [r for r in recs if r.get("torque") and abs(float(r["torque"]) - 77.7) <= 3.5
        and (r.get("v_peak") or 0) <= 278 and r.get("overrides")
        and float(r["eff"]) > 0.5]
for r in feas:
    r["eff"] = float(r["eff"]); r["td"] = float(r["td"]); r["ripple"] = float(r["ripple"])

def best(pred, key):
    c = [r for r in feas if pred(r)]
    return max(c, key=key) if c else None

cands = {
    "balanced (max eff*td, ripple<=7)": best(lambda r: r["ripple"] <= 7.0, lambda r: r["eff"] * r["td"]),
    "eff-priority (max td, eff>=95%, ripple<=7)": best(lambda r: r["eff"] >= 0.95 and r["ripple"] <= 7.0, lambda r: r["td"]),
    "high-eff (max eff, td>=7.7, ripple<=9)": best(lambda r: r["td"] >= 7.7 and r["ripple"] <= 9.0, lambda r: r["eff"]),
    "max-td (most compact, ripple<=8)": best(lambda r: r["ripple"] <= 8.0, lambda r: r["td"]),
}

# de-dup identical geometries, keep the labels
seen = {}
for label, r in cands.items():
    if not r:
        continue
    k = json.dumps({kk: round(float(vv), 3) for kk, vv in r["overrides"].items()}, sort_keys=True)
    seen.setdefault(k, {"r": r, "labels": []})["labels"].append(label)

print(f"=== verifying {len(seen)} unique candidates @ steps=120 (full disk) ===", flush=True)
out = []
for k, info in seen.items():
    r = info["r"]; ov = {kk: float(vv) for kk, vv in r["overrides"].items()}
    I = float(r.get("current_a") or 100.0)
    labels = " / ".join(info["labels"])
    print(f"\n>>> {labels}\n    steps60: eff {r['eff']*100:.2f}% td {r['td']:.3f} ripple {r['ripple']:.1f}% @ {I:.1f}A", flush=True)
    try:
        m = run_one(ov, I, 120, 120.0, n_periods=1.0, gamma_deg=28.0, mesh_size_mm=4.0,
                    min_size_mm=0.3, n_sectors=-1, pole_copy=True, torque_filter=True)
        rec = {"labels": info["labels"], "current_a": I, "overrides": ov,
               "steps60": {"eff": r["eff"], "td": r["td"], "ripple": r["ripple"], "torque": r["torque"]},
               "steps120": {"eff": m.get("efficiency"), "td": m.get("torque_per_mass_Nm_kg"),
                            "ripple": m.get("T_ripple_pct"), "torque": m.get("T_em_Nm"),
                            "mass": m.get("mass_total_kg"), "v_peak": m.get("V_peak"),
                            "p_loss": m.get("P_loss_total_W")}}
        s = rec["steps120"]
        print(f"    steps120: eff {s['eff']*100:.2f}% td {s['td']:.3f} ripple {s['ripple']:.1f}% "
              f"T {s['torque']:.1f} mass {s['mass']:.2f} Vpk {s['v_peak']:.0f}", flush=True)
        out.append(rec)
    except Exception as e:
        print(f"    FAILED: {type(e).__name__}: {str(e)[:120]}", flush=True)
        out.append({"labels": info["labels"], "current_a": I, "overrides": ov, "error": str(e)[:160]})

json.dump(out, open("config/verify_steps120.json", "w"), indent=2, default=float)
print("\n=== DONE — wrote config/verify_steps120.json ===", flush=True)
