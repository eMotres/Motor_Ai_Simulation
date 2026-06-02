"""Probe SB transient ripple vs mesh size / steps / gamma to see if ripple is
numerical (mesh-driven) or physical (cogging)."""
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
    T = d.get("T_em_Nm", [])
    Tavg = d.get("T_avg_Nm", 0.0)
    rip = d.get("T_ripple_pct", 0.0)
    dt = time.time() - t0
    Tr = [round(x, 1) for x in T]
    print(f"mesh={mesh} steps={steps} gamma={gamma:+.0f} | "
          f"T_avg={Tavg:6.2f}  ripple={rip:5.1f}%  ({dt:4.1f}s)")
    print(f"    T(t)={Tr}")
    return d

if __name__ == "__main__":
    print("=== SB ripple probe ===")
    run(4.0, 12, -10)
    run(3.0, 12, -10)
    run(3.0, 24, -10)
    run(2.5, 24, -10)
