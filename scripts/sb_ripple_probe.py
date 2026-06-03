"""Probe SB transient: torque ripple, voltage smoothness, loss ripple."""
import os, time
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band

def run(mesh, steps, gamma):
    t0 = time.time()
    d = fem_transient_sliding_band(
        n_steps_per_period=steps, n_periods=1.0, gamma_deg=gamma,
        I_phase_rms=85.0, mesh_size_mm=mesh, min_size_mm=0.3,
        outer_air_factor=1.3, n_sectors=4, stator_fillet_mm=0.0,
        nonlinear_iterations=14,
    )
    Vpk = d["V_peak"]; fe = d["P_fe_W"]; mg = d["P_mag_eddy_W"]
    dt = time.time() - t0
    print(f"steps={steps:3d} g={gamma:+.0f} | T={d['T_avg_Nm']:5.1f} rip={d['T_ripple_pct']:4.1f}% "
          f"| Vpk={Vpk:5.1f} | P_fe[{min(fe):.0f}..{max(fe):.0f}] "
          f"P_mag[{min(mg):.0f}..{max(mg):.0f}]  ({dt:4.1f}s)")
    print(f"    P_mag={[round(x) for x in mg]}")
    return d

if __name__ == "__main__":
    print("=== SB voltage + loss ripple probe ===")
    run(3.0, 24, 0)
    run(3.0, 100, 0)
