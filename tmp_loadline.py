"""Load-line analysis (robust): evaluate the optimized (star) design and the
baseline across a sweep of phase current, gamma fixed at the design angle.
Connect the points -> a load line per design.

Hardened vs the first version: per-eval timeout (kills hung FEM solves instead
of blocking forever), modest parallelism (avoid 10-way contention), one retry
for failed/timed-out evals, and incremental JSON writes so a hang never loses
already-finished points."""
import os, sys, json, subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = r"C:\Users\vadim\Projects\motor_ai_sim"
PY = sys.executable
CURRENTS = [65.0, 75.0, 85.0, 95.0, 105.0]
GAMMA = -25.0
STEPS = 24
COIL_T = 120.0
MESH = 4.0
MINSZ = 0.3
NSECT = -1            # full disk (honest model)
WORKERS = 4          # was 10 -> caused contention/hangs
TIMEOUT = 300        # s per eval; hung solve -> recorded as failed, not blocking

best = json.load(open(os.path.join(ROOT, "tmp_best_geom.json")))
DESIGNS = {"baseline": {}, "optimized": best}

results = {d: {} for d in DESIGNS}

def _write():
    json.dump(results, open(os.path.join(ROOT, "tmp_loadline_result.json"), "w"), indent=2)

def one(design_name, overrides, current):
    spec = {
        "overrides": overrides, "current_a": current, "steps": STEPS,
        "coil_temp_c": COIL_T, "n_periods": 1.0, "gamma_deg": GAMMA,
        "mesh_size_mm": MESH, "min_size_mm": MINSZ, "n_sectors": NSECT,
    }
    try:
        p = subprocess.run(
            [PY, "-m", "motor_ai_sim.optimization.refine_proc"],
            input=json.dumps(spec), capture_output=True, text=True,
            cwd=os.path.join(ROOT, "src"), timeout=TIMEOUT,
            env={**os.environ, "PYTHONPATH": os.path.join(ROOT, "src")},
        )
    except subprocess.TimeoutExpired:
        return (design_name, current, {"error": "TIMEOUT (%ss) - solve did not converge" % TIMEOUT})
    out = p.stdout or ""
    tag = "@@RESULT@@"
    if tag in out:
        r = json.loads(out[out.index(tag) + len(tag):])
        if r.get("ok"):
            return (design_name, current, r["res"])
        return (design_name, current, {"error": r.get("error", "?")})
    return (design_name, current, {"error": (p.stderr or out)[-200:] or "no output"})

def run_batch(jobs):
    done = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(one, *j): j for j in jobs}
        for f in as_completed(futs):
            dn, I, res = f.result()
            results[dn][I] = res
            _write()
            ok = "error" not in res
            print("  [%s] %s @ %3.0fA -> %s" % (
                "ok " if ok else "ERR", dn, I,
                ("T=%.2f eff=%.2f%% td=%.3f rip=%.2f%%" % (
                    res["T_em_Nm"], res["efficiency"]*100,
                    res["torque_per_mass_Nm_kg"], res["T_ripple_pct"]) if ok else res["error"][:70])),
                flush=True)
            done.append((dn, I, ok))
    return done

jobs = [(d, ov, I) for d, ov in DESIGNS.items() for I in CURRENTS]
print("=== load-line sweep: %d evals, %d workers, %ss timeout ===" % (len(jobs), WORKERS, TIMEOUT), flush=True)
run_batch(jobs)

# one sequential retry for any that failed/timed out
retry = [(d, DESIGNS[d], I) for d in DESIGNS for I in CURRENTS if "error" in results[d][I]]
if retry:
    print("--- retrying %d failed eval(s) sequentially ---" % len(retry), flush=True)
    for j in retry:
        dn, I, res = one(*j)
        results[dn][I] = res
        _write()
        print("  [retry %s] %s @ %3.0fA" % ("ok" if "error" not in res else "ERR", dn, I), flush=True)

print("\n=== TABLE ===", flush=True)
for dn in DESIGNS:
    print("\n%s:" % dn)
    print("   I(A)   T(Nm)   eff%%   td(Nm/kg)  ripple%%  Pcu(W)  Pfe(W)")
    for I in CURRENTS:
        r = results[dn][I]
        if "error" in r:
            print("   %5.0f   --- %s" % (I, r["error"][:60]))
        else:
            print("   %5.0f  %6.2f  %5.2f   %6.3f     %5.2f   %5.0f   %5.0f" % (
                I, r["T_em_Nm"], r["efficiency"]*100, r["torque_per_mass_Nm_kg"],
                r["T_ripple_pct"], r["P_cu_W"], r["P_fe_W"]))
print("\nDONE", flush=True)
