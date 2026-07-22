"""Validate the field-based (rotor_eddy) loss path vs the slab estimate.
Runs the sliding-band transient twice (n=2, 24 steps, I=120):
  A) rotor_eddy=False  -> slab magnet/shaft losses (old)
  B) rotor_eddy=True   -> J=sigma(-dA/dt+U) field losses (new, Ansys-style)
Checks: torque/EMF unchanged (eddy reaction small), loss magnitudes, timing.
Also prints the Maxwell-style Bertotti fit actually used for the iron."""
import time, numpy as np
from motor_ai_sim.materials import get_steel, fit_bertotti_from_curves
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band

s = get_steel('20SW1200')
fit = fit_bertotti_from_curves(s)
print('IRON FIT (Maxwell-style): kh=%.4g kc=%.4g ke=%.4g | err mean=%.1f%% p90=%.1f%% (%d pts)'
      % (fit[0], fit[1], fit[2], 100*fit[3]['rel_err_mean'], 100*fit[3]['rel_err_p90'], fit[3]['n_points']),
      flush=True)

res = {}
for label, re_flag in (('slab', False), ('field', True)):
    t0 = time.time()
    r = fem_transient_sliding_band(n_steps_per_period=24, n_periods=1.0,
                                   gamma_deg=0.0, I_phase_rms=120.0,
                                   mesh_size_mm=4.0, n_sectors=2,
                                   rotor_eddy=re_flag)
    dtw = time.time() - t0
    res[label] = r
    print('%-5s: %.1fs | T_avg=%.2f ripple=%.1f%% V_pk=%.1f | P_fe=%.0f P_mag=%.1f P_shaft=%.2f W (model=%s)'
          % (label, dtw, r['T_avg_Nm'], r['T_ripple_pct'], r['V_peak'],
             np.mean(r['P_fe_W']), np.mean(r['P_mag_eddy_W']),
             np.mean(r.get('P_shaft_eddy_W', [0])), r.get('loss_model')),
          flush=True)

a, b = res['slab'], res['field']
print('\nDELTAS field vs slab:')
print('  T_avg: %.2f -> %.2f (%.2f%%)' % (a['T_avg_Nm'], b['T_avg_Nm'],
      100*(b['T_avg_Nm']-a['T_avg_Nm'])/max(abs(a['T_avg_Nm']),1e-9)))
print('  P_mag: %.1f -> %.1f W' % (np.mean(a['P_mag_eddy_W']), np.mean(b['P_mag_eddy_W'])))
print('  P_shaft: %.2f -> %.2f W' % (np.mean(a.get('P_shaft_eddy_W',[0])), np.mean(b.get('P_shaft_eddy_W',[0]))))
print('  P_fe: %.0f -> %.0f W (both use fitted Bertotti now)' % (np.mean(a['P_fe_W']), np.mean(b['P_fe_W'])))
print('DONE', flush=True)
