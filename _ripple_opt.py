"""Autonomous torque-ripple optimizer (direct FEM transient, coordinate descent).
Minimise peak-peak torque ripple while keeping T_avg >= 0.95*baseline."""
import sys, os, time, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
import numpy as np
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band

LOG = open("_ripple_opt_log.txt", "a", buffering=1)
def out(s):
    print(s, flush=True); LOG.write(s + "\n")

def ev(over, steps=72):
    t0 = time.time()
    r = fem_transient_sliding_band(n_steps_per_period=steps, n_periods=1.0, gamma_deg=0.0,
        I_phase_rms=85.0, mesh_size_mm=2.0, min_size_mm=0.1, n_sectors=4,
        nonlinear_iterations=14, coil_temp_c=120.0, geo_override=(over or None))
    T = np.asarray(r.get("T_em_Nm") or r.get("torque_Nm"), float)
    Tavg = float(T.mean()); ripple = 100.0*(T.max()-T.min())/Tavg if Tavg else 1e9
    return Tavg, ripple, round(time.time()-t0, 1)

# Baseline (current geometry)
base = {}
Tavg0, rip0, dt = ev(base)
out(f"\n==== RIPPLE OPTIMIZATION ====  baseline T_avg={Tavg0:.3f} ripple={rip0:.2f}% ({dt}s)")
best = dict(base); bestT, bestR = Tavg0, rip0
Tfloor = 0.95 * Tavg0
out(f"T_avg floor = {Tfloor:.2f} N.m (keep within 5% of baseline)\n")

# Coordinate descent over ripple-driving params (value lists exclude the current best)
SWEEP = [
    ("magnet_fill_up",   [0.34, 0.38, 0.42, 0.48, 0.52]),
    ("magnet_up_gap",    [1.0, 1.5, 2.5, 3.0]),
    ("magnet_fill_down", [0.80, 0.85, 0.95, 1.00]),
    ("rotor_fill_r",     [0.8, 2.0, 2.6]),
    ("tooth2_width",     [4.5, 5.0, 6.0, 6.5]),
]
results = [dict(over={}, Tavg=Tavg0, ripple=rip0, tag="baseline")]
for pname, vals in SWEEP:
    out(f"-- sweep {pname} (current best={best.get(pname,'cfg')}, ripple={bestR:.2f}%) --")
    local_best = None
    for v in vals:
        over = dict(best); over[pname] = v
        try:
            Ta, Rp, dt = ev(over)
        except Exception as e:
            out(f"   {pname}={v}: FAIL {type(e).__name__}: {str(e)[:60]}"); continue
        ok = Ta >= Tfloor
        flag = "" if ok else "  (T below floor, rejected)"
        out(f"   {pname}={v:<6}  T_avg={Ta:6.3f}  ripple={Rp:6.2f}%  ({dt}s){flag}")
        results.append(dict(over=dict(over), Tavg=Ta, ripple=Rp, tag=f"{pname}={v}"))
        if ok and Rp < bestR - 0.05:
            local_best = (pname, v, Ta, Rp)
    if local_best:
        pn, v, Ta, Rp = local_best
        best[pn] = v; bestT, bestR = Ta, Rp
        out(f"   -> adopt {pn}={v}  new best ripple={bestR:.2f}% (T_avg={bestT:.3f})\n")
    else:
        out(f"   -> keep current ({pname} unchanged), best ripple={bestR:.2f}%\n")
    json.dump({"baseline":{"Tavg":Tavg0,"ripple":rip0}, "best_over":best,
               "best":{"Tavg":bestT,"ripple":bestR}, "results":results},
              open("_ripple_opt_results.json","w"), indent=2)

out(f"\n==== DONE ====  baseline ripple={rip0:.2f}% -> best ripple={bestR:.2f}%  "
    f"({rip0-bestR:+.2f}pp)  T_avg {Tavg0:.2f} -> {bestT:.2f}")
out(f"BEST OVERRIDES: {json.dumps(best)}")
