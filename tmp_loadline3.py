"""Stage 2 of the workflow: take the optimizer's BEST candidates and load-line
ONLY them at FULL DISK (accurate) over two currents (85/90 A) — a real,
apples-to-apples comparison of the finalists.  Writes web/public/last_loadline.json
as {design_name: [{I, td, eff, ...}]} so the Sweep-tab chart draws one segment
per candidate."""
import os, sys, json, subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = r"C:\Users\vadim\Projects\motor_ai_sim"
PY = sys.executable
CURRENTS = [85.0, 90.0]
GAMMA, STEPS, COIL_T, MESH, MINSZ, NSECT = -25.0, 24, 120.0, 4.0, 0.3, -1   # full disk
WORKERS, TIMEOUT = 6, 320

# The finalists (overrides on the baseline geometry — only the two swept params).
DESIGNS = {
    "Baseline (14t)":      {},                                              # nw14 / mh16
    "16 turns / mag 18":   {"num_wires_per_slot": 16, "magnet_height": 18}, # the sweet spot
    "18 turns / mag 16":   {"num_wires_per_slot": 18, "magnet_height": 16}, # high-td (old 'optimized' direction)
}

results = {name: [] for name in DESIGNS}

def one(name, ov, I):
    spec = {"overrides": ov, "current_a": I, "steps": STEPS, "coil_temp_c": COIL_T,
            "n_periods": 1.0, "gamma_deg": GAMMA, "mesh_size_mm": MESH,
            "min_size_mm": MINSZ, "n_sectors": NSECT}
    try:
        p = subprocess.run([PY, "-m", "motor_ai_sim.optimization.refine_proc"],
                           input=json.dumps(spec), capture_output=True, text=True,
                           cwd=os.path.join(ROOT, "src"), timeout=TIMEOUT,
                           env={**os.environ, "PYTHONPATH": os.path.join(ROOT, "src")})
    except subprocess.TimeoutExpired:
        return (name, I, None)
    out = p.stdout or ""
    tag = "@@RESULT@@"
    if tag in out:
        r = json.loads(out[out.index(tag) + len(tag):])
        if r.get("ok"):
            return (name, I, r["res"])
    return (name, I, None)

jobs = [(n, ov, I) for n, ov in DESIGNS.items() for I in CURRENTS]
print("=== load-lining %d finalists x %d currents (full disk) ===" % (len(DESIGNS), len(CURRENTS)), flush=True)
with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    futs = {ex.submit(one, *j): j for j in jobs}
    for f in as_completed(futs):
        name, I, res = f.result()
        if res:
            results[name].append({"I": I, "td": round(res["torque_per_mass_Nm_kg"], 3),
                                  "eff": round(res["efficiency"] * 100, 3),
                                  "T": round(res["T_em_Nm"], 2), "ripple": round(res["T_ripple_pct"], 1)})
        print("  [%s] %s @ %.0fA -> %s" % ("ok " if res else "ERR", name, I,
              ("eff=%.2f%% td=%.3f" % (res["efficiency"]*100, res["torque_per_mass_Nm_kg"]) if res else "failed")), flush=True)

for name in results:
    results[name].sort(key=lambda r: r["I"])
pub = os.path.join(ROOT, "web", "public")
json.dump(results, open(os.path.join(pub, "last_loadline.json"), "w"), indent=1)

print("\n=== FINALIST SEGMENTS (full disk) ===", flush=True)
for name, arr in results.items():
    seg = "  ".join("%.0fA: eff=%.2f%% td=%.2f" % (r["I"], r["eff"], r["td"]) for r in arr)
    print("  %-20s %s" % (name, seg))
print("DONE", flush=True)
