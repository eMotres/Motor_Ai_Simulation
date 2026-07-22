"""Check peak phase voltage (V_peak) of the finalists at the RATED point
(30.5 Nm) — to test the new max-voltage constraint (140 V).  More turns → higher
back-EMF → higher V, so this is where the high-turn designs may fail."""
import os, sys, json, subprocess

ROOT = r"C:\Users\vadim\Projects\motor_ai_sim"
PY = sys.executable
GAMMA, STEPS, COIL_T, MESH, MINSZ, NSECT = -25.0, 24, 120.0, 4.0, 0.3, -1

# (name, overrides, current that gives ~30.5 Nm from the interpolation)
CASES = [
    ("Baseline (14t)",     {},                                              101.0),
    ("16 turns / mag 18",  {"num_wires_per_slot": 16, "magnet_height": 18},  75.0),
    ("18 turns / mag 16",  {"num_wires_per_slot": 18, "magnet_height": 16},  60.0),
]

def one(ov, I):
    spec = {"overrides": ov, "current_a": I, "steps": STEPS, "coil_temp_c": COIL_T,
            "n_periods": 1.0, "gamma_deg": GAMMA, "mesh_size_mm": MESH,
            "min_size_mm": MINSZ, "n_sectors": NSECT}
    for _ in range(2):
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

VMAX = 140.0
print("=== V_peak at the rated point (limit %.0f V) ===" % VMAX, flush=True)
for name, ov, I in CASES:
    res = one(ov, I)
    if res:
        v = res["V_peak"]; T = res["T_em_Nm"]; eff = res["efficiency"] * 100
        flag = "OK" if v <= VMAX else "OVER LIMIT"
        print("  %-20s  I=%5.1fA  T=%5.2f Nm  V_peak=%6.1f V  eff=%5.2f%%   [%s]" % (name, I, T, v, eff, flag), flush=True)
    else:
        print("  %-20s  failed" % name, flush=True)
print("DONE", flush=True)
