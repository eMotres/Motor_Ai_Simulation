"""Reshape the computed load-line sweep into a small static JSON the frontend can
fetch (served by vite from web/public, no backend involved)."""
import json, os
ROOT = r"C:\Users\vadim\Projects\motor_ai_sim"
res = json.load(open(os.path.join(ROOT, "tmp_loadline_result.json")))
out = {}
for dn in ("baseline", "optimized"):
    arr = []
    for k in sorted(res.get(dn, {}), key=lambda s: float(s)):
        r = res[dn][k]
        if "error" in r:
            continue
        arr.append({"I": float(k), "td": round(r["torque_per_mass_Nm_kg"], 3),
                    "eff": round(r["efficiency"] * 100, 3),
                    "T": round(r["T_em_Nm"], 2), "ripple": round(r["T_ripple_pct"], 1)})
    out[dn] = arr
pub = os.path.join(ROOT, "web", "public")
os.makedirs(pub, exist_ok=True)
json.dump(out, open(os.path.join(pub, "last_loadline.json"), "w"), indent=1)
print("wrote web/public/last_loadline.json:", len(out["baseline"]), "baseline +",
      len(out["optimized"]), "optimized points")
