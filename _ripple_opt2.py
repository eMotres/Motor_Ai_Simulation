"""Round-2 ripple refinement around round-1 best. Fixes the local-best tracking
(compare against running local best, not global)."""
import sys, os, time, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
import numpy as np
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band
LOG=open("_ripple_opt_log.txt","a",buffering=1)
def out(s): print(s,flush=True); LOG.write(s+"\n")
def ev(over):
    t0=time.time()
    r=fem_transient_sliding_band(n_steps_per_period=72,n_periods=1.0,gamma_deg=0.0,
        I_phase_rms=85.0,mesh_size_mm=2.0,min_size_mm=0.1,n_sectors=4,
        nonlinear_iterations=14,coil_temp_c=120.0,geo_override=(over or None))
    T=np.asarray(r.get("T_em_Nm") or r.get("torque_Nm"),float)
    Tavg=float(T.mean()); rip=100.0*(T.max()-T.min())/Tavg if Tavg else 1e9
    return Tavg,rip,round(time.time()-t0,1)

best={"magnet_fill_up":0.52,"magnet_up_gap":2.5,"rotor_fill_r":2.0}
bT,bR,dt=ev(best)
Tfloor=24.02
out(f"\n==== ROUND 2 ====  start best={json.dumps(best)}  T_avg={bT:.3f} ripple={bR:.2f}% ({dt}s)")
SWEEP=[("magnet_fill_up",[0.56,0.60,0.64]),
       ("magnet_up_gap",[2.6,2.7]),
       ("magnet_fill_down",[0.92,0.95]),
       ("air_gap",[0.55])]
for pn,vals in SWEEP:
    out(f"-- R2 sweep {pn} (best={best.get(pn,'cfg')}, ripple={bR:.2f}%) --")
    loc=None
    for v in vals:
        over=dict(best); over[pn]=v
        try: Ta,Rp,dt=ev(over)
        except Exception as e: out(f"   {pn}={v}: FAIL {str(e)[:50]}"); continue
        ok=Ta>=Tfloor; fl="" if ok else "  (T<floor, rej)"
        out(f"   {pn}={v:<6} T_avg={Ta:6.3f} ripple={Rp:6.2f}% ({dt}s){fl}")
        if ok and Rp < (loc[3] if loc else bR) - 0.05:   # track RUNNING local best
            loc=(pn,v,Ta,Rp)
    if loc:
        pn,v,Ta,Rp=loc; best[pn]=v; bT,bR=Ta,Rp
        out(f"   -> adopt {pn}={v} new best ripple={bR:.2f}% (T_avg={bT:.3f})\n")
    else:
        out(f"   -> keep {pn}, best ripple={bR:.2f}%\n")
json.dump({"best_over":best,"best":{"Tavg":bT,"ripple":bR}},open("_ripple_opt2_results.json","w"),indent=2)
out(f"==== ROUND 2 DONE ====  best ripple={bR:.2f}%  T_avg={bT:.3f}")
out(f"FINAL OVERRIDES: {json.dumps(best)}")
