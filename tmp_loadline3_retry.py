"""Retry the two finalist evals that hit the intermittent LLVM crash, run
SEQUENTIALLY (no parallel JIT contention), and merge into the public JSON so
every finalist has a full 85/90 A segment."""
import os, sys, json, subprocess

ROOT = r"C:\Users\vadim\Projects\motor_ai_sim"
PY = sys.executable
GAMMA, STEPS, COIL_T, MESH, MINSZ, NSECT = -25.0, 24, 120.0, 4.0, 0.3, -1
PUB = os.path.join(ROOT, "web", "public", "last_loadline.json")

RETRY = [
    ("Baseline (14t)",     {},                                              90.0),
    ("18 turns / mag 16",  {"num_wires_per_slot": 18, "magnet_height": 16}, 90.0),
]

def one(ov, I):
    spec = {"overrides": ov, "current_a": I, "steps": STEPS, "coil_temp_c": COIL_T,
            "n_periods": 1.0, "gamma_deg": GAMMA, "mesh_size_mm": MESH,
            "min_size_mm": MINSZ, "n_sectors": NSECT}
    for attempt in range(2):
        try:
            p = subprocess.run([PY, "-m", "motor_ai_sim.optimization.refine_proc"],
                               input=json.dumps(spec), capture_output=True, text=True,
                               cwd=os.path.join(ROOT, "src"), timeout=320,
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
for name, ov, I in RETRY:
    res = one(ov, I)
    if res:
        data.setdefault(name, []).append({"I": I, "td": round(res["torque_per_mass_Nm_kg"], 3),
                                          "eff": round(res["efficiency"] * 100, 3),
                                          "T": round(res["T_em_Nm"], 2), "ripple": round(res["T_ripple_pct"], 1)})
        data[name].sort(key=lambda r: r["I"])
        print("  [ok ] %s @ %.0fA -> eff=%.2f%% td=%.3f" % (name, I, res["efficiency"]*100, res["torque_per_mass_Nm_kg"]), flush=True)
    else:
        print("  [ERR] %s @ %.0fA still failed" % (name, I), flush=True)

json.dump(data, open(PUB, "w"), indent=1)
print("\n=== FULL FINALIST SEGMENTS ===", flush=True)
for name, arr in data.items():
    print("  %-20s %s" % (name, "  ".join("%.0fA: eff=%.2f%% td=%.2f" % (r["I"], r["eff"], r["td"]) for r in arr)))
print("DONE", flush=True)
