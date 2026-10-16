"""Validate fem_quasistatic_transient: genuine per-symmetry (Full = real stitched
disk, NOT forced to 1/4), one algorithm for all. Print T_avg/ripple/V_peak +
per-phase flux-linkage balance for n=4, 2, 1."""
import numpy as np
from motor_ai_sim.simulation.fem_solver_2d import fem_quasistatic_transient

for ns in (4, 2, 1):
    print("[running ns=%d ...]" % ns, flush=True)
    r = fem_quasistatic_transient(n_steps_per_period=12, n_periods=1.0, gamma_deg=0.0,
                                  I_phase_rms=120.0, mesh_size_mm=4.0, n_sectors=ns)
    amps = [np.ptp(np.asarray(r["psi_%s_Wb" % ph], float)) / 2 for ph in "ABC"]
    spread = max(amps) / max(min(amps), 1e-30)
    print("ns=%d: T_avg=%.2f  ripple=%.1f%%  V_peak=%.1f | psi_amp A/B/C=%.4e/%.4e/%.4e  spread=%.3f"
          % (ns, r["T_avg_Nm"], r["T_ripple_pct"], r["V_peak"], amps[0], amps[1], amps[2], spread),
          flush=True)
print("DONE", flush=True)
