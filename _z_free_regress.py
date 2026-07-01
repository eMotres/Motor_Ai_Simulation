import _use40  # noqa
import sys, numpy as np, logging
logging.basicConfig(level=logging.ERROR)
import motor_ai_sim.simulation.fem_solver_2d as F
r = F.fem_transient_sliding_band(
    n_steps_per_period=12, n_periods=6/12.0, gamma_deg=10.0, I_phase_rms=60.0,
    mesh_size_mm=1.5, min_size_mm=0.25, outer_air_factor=1.3, gap_layers=3,
    n_sectors=-1, structured_gap=False, return_field=False, torque_filter=False)
print(f"FREE gl=3: T_avg={r['T_avg_Nm']:.8f} ripple_raw={r.get('T_ripple_raw_pct',0):.6f} Ploss={r.get('P_loss_total_avg_W',0):.6f} psiA={np.mean(np.abs(r.get('psi_A_Wb',[0]))):.8f}", flush=True)
