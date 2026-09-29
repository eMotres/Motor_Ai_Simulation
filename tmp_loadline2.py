"""Extend both load lines so they OVERLAP on the torque-density (X) axis:
push the baseline UP in current and the optimized DOWN, then merge with the
existing sweep.  At a common Nm/kg, whichever design has the higher efficiency
wins — an apples-to-apples comparison the offset single points can't give."""
import os, sys, json, subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = r"C:\Users\vadim\Projects\motor_ai_sim"
PY = sys.executable
GAMMA, STEPS, COIL_T, MESH, MINSZ, NSECT = -25.0, 24, 120.0, 4.0, 0.3, -1
WORKERS, TIMEOUT = 4, 320

best = json.load(open(os.path.join(ROOT, "tmp_best_geom.json")))
DESIGN_OV = {"baseline": {}, "optimized": best}

# NEW points to fill the non-overlapping ends of the Nm/kg axis.
JOBS = [("baseline", 120.0), ("baseline", 140.0), ("baseline", 160.0),
        ("optimized", 45.0), ("optimized", 55.0)]

# Start from the existing sweep (65..105 A for both) if present.
try:
    results = json.load(open(os.path.join(ROOT, "tmp_loadline_result.json")))
except Exception:
    results = {"baseline": {}, "optimized": {}}
results.setdefault("baseline", {}); results.setdefault("optimized", {})

def _write():
    json.dump(results, open(os.path.join(ROOT, "tmp_loadline_result.json"), "w"), indent=2)

def one(name, current):
    spec = {"overrides": DESIGN_OV[name], "current_a": current, "steps": STEPS,
            "coil_temp_c": COIL_T, "n_periods": 1.0, "gamma_deg": GAMMA,
            "mesh_size_mm": MESH, "min_size_mm": MINSZ, "n_sectors": NSECT}
    try:
        p = subprocess.run([PY, "-m", "motor_ai_sim.optimization.refine_proc"],
                           input=json.dumps(spec), capture_output=True, text=True,
                           cwd=os.path.join(ROOT, "src"), timeout=TIMEOUT,
                           env={**os.environ, "PYTHONPATH": os.path.join(ROOT, "src")})
    except subprocess.TimeoutExpired:
        return (name, current, {"error": "TIMEOUT"})
    out = p.stdout or ""
    tag = "@@RESULT@@"
    if tag in out:
        r = json.loads(out[out.index(tag) + len(tag):])
        return (name, current, r["res"] if r.get("ok") else {"error": r.get("error", "?")})
    return (name, current, {"error": (p.stderr or out)[-160:] or "no output"})

print("=== extending load lines: %d new evals ===" % len(JOBS), flush=True)
with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    futs = {ex.submit(one, n, I): (n, I) for n, I in JOBS}
    for f in as_completed(futs):
        n, I, res = f.result()
        results[n][str(I)] = res
        _write()
        ok = "error" not in res
        print("  [%s] %s @ %3.0fA -> %s" % ("ok " if ok else "ERR", n, I,
              ("T=%.2f eff=%.2f%% td=%.3f rip=%.1f%%" % (res["T_em_Nm"], res["efficiency"]*100,
               res["torque_per_mass_Nm_kg"], res["T_ripple_pct"]) if ok else res["error"][:60])), flush=True)

print("\n=== MERGED load lines (sorted by current) ===", flush=True)
for dn in ("baseline", "optimized"):
    print("\n%s:" % dn)
    for k in sorted(results[dn], key=lambda s: float(s)):
        r = results[dn][k]
        if "error" in r: print("   %5.0fA  --- %s" % (float(k), r["error"][:50]))
        else: print("   %5.0fA  T=%6.2f  eff=%5.2f%%  td=%6.3f  rip=%4.1f%%" % (
            float(k), r["T_em_Nm"], r["efficiency"]*100, r["torque_per_mass_Nm_kg"], r["T_ripple_pct"]))
print("\nDONE", flush=True)
