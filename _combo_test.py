import _use40  # noqa
import numpy as np, logging
logging.basicConfig(level=logging.ERROR)
import motor_ai_sim.simulation.fem_solver_2d as F
# both structured gap AND structured slot on
r = F.fem_transient_sliding_band(
    n_steps_per_period=12, n_periods=6/12.0, gamma_deg=12.0, I_phase_rms=60.0,
    mesh_size_mm=1.5, min_size_mm=0.25, outer_air_factor=1.3, gap_layers=2,
    n_sectors=-1, structured_gap=True, structured_slot=True,
    return_field=False, torque_filter=True)
print(f"COMBO gap+slot: T_avg={r['T_avg_Nm']:.4f} Vpk={r.get('V_peak',0):.2f} "
      f"Ploss={r.get('P_loss_total_avg_W',0):.1f} ripple={r.get('T_ripple_pct',0):.1f}%", flush=True)
