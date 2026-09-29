"""Rebuild config/.last_descent.json from the data saved during the run
(tmp_objspace.json + tmp_best_geom.json), so the last optimization re-appears in
the objective-space plot after the backend reloads it at startup.  Scatter +
best + baseline are exact; the per-iteration trajectory line is omitted (its
per-iter efficiency wasn't captured)."""
import json, os

ROOT = r"C:\Users\vadim\Projects\motor_ai_sim"
obj  = json.load(open(os.path.join(ROOT, "tmp_objspace.json")))
geom = json.load(open(os.path.join(ROOT, "tmp_best_geom.json")))

# points: tmp_objspace stores [td, eff%, ripple%]; the frontend expects eff as a
# fraction (it multiplies by 100), ripple in %.
pts = [{"td": p[0], "eff": p[1] / 100.0, "ripple": p[2]}
       for p in obj["pts"] if p[0] is not None and p[2] is not None]

best_m = {"torque_per_mass": obj["best"][0], "efficiency": obj["best"][1] / 100.0,
          "T_ripple_pct": obj["best"][2], "T_em_Nm": 41.598}
base_m = {"torque_per_mass": obj["baseline"][0], "efficiency": obj["baseline"][1] / 100.0,
          "T_ripple_pct": 5.34, "T_em_Nm": 25.52}
ov = {k: round(float(v), 4) for k, v in geom.items()}

state = {
    "running": False, "iter": 10, "max_iters": 10, "n_evals": len(pts),
    "baseline": base_m,
    "best": {"metrics": best_m, "x": geom, "overrides": ov,
             "cost": -1.32, "F": 1.32},
    "current": best_m,
    "history": [],            # trajectory omitted (no per-iter efficiency captured)
    "points": pts,
    "result": {"best": {"metrics": best_m, "overrides": ov, "x": geom,
                        "cost": -1.32, "F": 1.32},
               "baseline": base_m, "n_evals": len(pts), "algorithm": "cmaes"},
    "error": None, "algorithm": "cmaes", "n_sectors": -1,
}

out = os.path.join(ROOT, "config", ".last_descent.json")
json.dump(state, open(out, "w", encoding="utf-8"))
print("wrote", out, "with", len(pts), "points; best td=%.3f eff=%.3f%%" %
      (best_m["torque_per_mass"], best_m["efficiency"] * 100))
