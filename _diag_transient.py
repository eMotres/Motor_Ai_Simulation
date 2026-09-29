"""Diagnose the transient back-EMF asymmetry + torque ripple.
Run the sliding-band transient and report, per phase, the flux-linkage
amplitude/phase and the voltage amplitude — to quantify how unbalanced the
3 phases really are, and compare n=4 (1/4) vs n=2 (1/2)."""
import sys, numpy as np
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band

def analyze(ns, nsteps=24, eddy=False):
    print("[running n=%d steps=%d eddy=%s ...]" % (ns, nsteps, eddy), flush=True)
    r = fem_transient_sliding_band(
        n_steps_per_period=nsteps, n_periods=1.0, gamma_deg=0.0,
        I_phase_rms=120.0, mesh_size_mm=4.0, n_sectors=ns, eddy=eddy)
    T = np.asarray(r["T_em_Nm"], float)
    print("\n===== n_sectors=%d  (%d steps) =====" % (ns, len(T)))
    print("T_em: mean=%.2f  pp=%.2f  ripple=%.1f%%"
          % (T.mean(), T.max()-T.min(), 100*(T.max()-T.min())/abs(T.mean())))
    for ph in "ABC":
        psi = np.asarray(r["psi_%s_Wb" % ph], float)
        V = np.asarray(r["V_%s" % ph], float)
        amp = (psi.max()-psi.min())/2
        # phase of fundamental
        F = np.fft.rfft(psi - psi.mean())
        k1 = 1 if len(F) > 1 else 0
        ph_deg = np.degrees(np.angle(F[k1])) if k1 else 0.0
        print("  psi_%s: amp=%.4e Wb  phase=%7.1f deg | V_%s: pp=%.1f V  rms=%.1f"
              % (ph, amp, ph_deg, ph, V.max()-V.min(), np.std(V)))
    # balance metric: max/min of the 3 psi amplitudes
    amps = [ (np.asarray(r["psi_%s_Wb"%ph],float).max()-np.asarray(r["psi_%s_Wb"%ph],float).min())/2 for ph in "ABC"]
    print("  psi amplitude spread: min=%.4e max=%.4e  ratio max/min=%.3f"
          % (min(amps), max(amps), max(amps)/max(min(amps),1e-30)))
    return r

ns = int(sys.argv[1]) if len(sys.argv) > 1 else 4
nsteps = int(sys.argv[2]) if len(sys.argv) > 2 else 24
eddy = (len(sys.argv) > 3 and sys.argv[3] in ("1", "eddy", "True"))
analyze(ns, nsteps, eddy)
print("\nDONE")
