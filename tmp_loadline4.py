"""Extend the finalists' load lines over a WIDE current range so the curves are
long enough to OVERLAP in efficiency (Y) — then at a common η a horizontal slice
shows which design has more Nm/kg.  Merges into the existing public JSON."""
import os, sys, json, subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = r"C:\Users\vadim\Projects\motor_ai_sim"
PY = sys.executable
GAMMA, STEPS, COIL_T, MESH, MINSZ, NSECT = -25.0, 24, 120.0, 4.0, 0.3, -1
WORKERS, TIMEOUT = 4, 320
PUB = os.path.join(ROOT, "web", "public", "last_loadline.json")

DESIGNS = {
    "Baseline (14t)":     {},
    "16 turns / mag 18":  {"num_wires_per_slot": 16, "magnet_height": 18},
    "18 turns / mag 16":  {"num_wires_per_slot": 18, "magnet_height": 16},
}
NEW_CURRENTS = [55.0, 70.0, 105.0, 120.0, 140.0]   # added to the existing 85/90

def one(ov, I):
    spec = {"overrides": ov, "current_a": I, "steps": STEPS, "coil_temp_c": COIL_T,
            "n_periods": 1.0, "gamma_deg": GAMMA, "mesh_size_mm": MESH,
            "min_size_mm": MINSZ, "n_sectors": NSECT}
    for _ in range(2):
        try:
            p = subprocess.run([PY, "-m", "motor_ai_sim.optimization.refine_proc"],
                               input=json.dumps(spec), capture_output=True, text=True,
                               cwd=os.path.join(ROOT, "src"), timeout=TIMEOUT,
                               env={**os.environ, "PYTHONPATH": os.path.join(ROOT, "src")})
        except subprocess.TimeoutExpired:
            continue
        out = p.stdout or ""
        if "@@RESULT@@" in out:
            r = json.loads(out[out.index("@@RESULT@@") + 10:])
            if r.get("ok"):
                return r["res"]
    return None

data = json.load(open(PUB))
for d in DESIGNS:
    data.setdefault(d, [])

def have(name, I):
    return any(abs(p["I"] - I) < 0.1 for p in data.get(name, []))

jobs = [(n, ov, I) for n, ov in DESIGNS.items() for I in NEW_CURRENTS if not have(n, I)]
print("=== extending finalists: %d new evals (full disk) ===" % len(jobs), flush=True)

def run(job):
    n, ov, I = job
    return (n, I, one(ov, I))

failed = []
with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    for n, I, res in ex.map(run, jobs):
        if res:
            data[n].append({"I": I, "td": round(res["torque_per_mass_Nm_kg"], 3),
                            "eff": round(res["efficiency"] * 100, 3),
                            "T": round(res["T_em_Nm"], 2), "ripple": round(res["T_ripple_pct"], 1)})
        else:
            failed.append((n, I))
        print("  [%s] %s @ %.0fA" % ("ok " if res else "ERR", n, I), flush=True)

# sequential retry of any failures (dodge parallel JIT contention)
for n, I in failed:
    res = one(DESIGNS[n], I)
    if res:
        data[n].append({"I": I, "td": round(res["torque_per_mass_Nm_kg"], 3),
                        "eff": round(res["efficiency"] * 100, 3),
                        "T": round(res["T_em_Nm"], 2), "ripple": round(res["T_ripple_pct"], 1)})
    print("  [retry %s] %s @ %.0fA" % ("ok" if res else "ERR", n, I), flush=True)

for n in data:
    data[n].sort(key=lambda r: r["I"])
json.dump(data, open(PUB, "w"), indent=1)

print("\n=== EXTENDED LOAD LINES ===", flush=True)
for n, arr in data.items():
    print("\n%s:" % n)
    for r in arr:
        print("   %5.0fA  eff=%5.2f%%  td=%6.2f" % (r["I"], r["eff"], r["td"]))
print("\nDONE", flush=True)
