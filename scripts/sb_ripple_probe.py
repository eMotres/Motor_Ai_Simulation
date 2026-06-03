"""Probe SB transient: torque, voltage, losses, copper temperature/end-winding."""
import os, time
for k in ("OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS"): os.environ.setdefault(k,"1")
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band

def run(steps, gamma, T=120.0, kend=0.0):
    t0=time.time()
    d=fem_transient_sliding_band(n_steps_per_period=steps, n_periods=1.0, gamma_deg=gamma,
        I_phase_rms=85.0, mesh_size_mm=3.0, coil_temp_c=T, end_winding_factor=kend)
    fe=d["P_fe_W"]; mg=d["P_mag_eddy_W"]
    print(f"steps={steps} g={gamma:+.0f} T={T:.0f}C | T_avg={d['T_avg_Nm']:5.2f} rip={d['T_ripple_pct']:4.1f}% "
          f"Vpk={d['V_peak']:5.1f} | P_cu={d['P_cu_W'][0]:5.0f} k_end={d['end_winding_factor']:.2f} "
          f"P_fe[{min(fe):.0f}-{max(fe):.0f}] P_mag[{min(mg):.0f}-{max(mg):.0f}] ({time.time()-t0:4.1f}s)")
    return d

if __name__=="__main__":
    print("=== SB copper + saturation probe ===")
    run(24, 0)
    run(24, -10)
