"""Transient smoke test: mean torque with FREE gap vs MAPPED (structured) gap.
Physics is unchanged, so mean torque should match within a few %.  Small n_steps
for speed.  n_sectors=2 (config default) so the mapped-gap path is active."""
import sys, time, numpy as np
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band

COMMON = dict(
    n_steps_per_period=12, n_periods=1.0, gamma_deg=15.0, I_phase_rms=20.0,
    mesh_size_mm=0.6, min_size_mm=0.3, outer_air_factor=1.2, gap_layers=2.0,
    n_sectors=2, stator_fillet_mm=1.0, coil_temp_c=120.0,
    rotor_eddy=False, demag=False, torque_filter=True,
)

def meanT(res):
    if "T_avg_Nm" in res: return float(res["T_avg_Nm"])
    Ts = res.get("T_em_Nm")
    return float(np.mean(Ts)) if Ts is not None else float("nan")

def run(tag, structured):
    t0=time.time()
    r = fem_transient_sliding_band(structured_gap=structured, **COMMON)
    dt=time.time()-t0
    tm=meanT(r)
    rp = r.get("T_ripple_pct")
    print(f"[{tag}] structured_gap={structured}: mean torque={tm:.4f} Nm  "
          f"ripple={rp}  ({dt:.1f}s)", flush=True)
    return tm

if __name__ == "__main__":
    tf = run("FREE", False)
    ts = run("MAPPED", True)
    if np.isfinite(tf) and np.isfinite(ts) and abs(tf)>1e-6:
        rel = abs(ts-tf)/abs(tf)*100
        print(f"mean torque: free={tf:.4f}  mapped={ts:.4f}  rel diff={rel:.2f}%  "
              f"{'OK(<8%)' if rel<8 else 'CHECK'}", flush=True)
        sys.exit(0 if rel<8 else 2)
    else:
        print("could not extract mean torque", flush=True); sys.exit(3)
