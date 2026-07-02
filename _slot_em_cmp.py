"""EM sanity: transient torque + back-EMF, FREE slot vs STRUCTURED slot, 40 mm
full-disk.  Physics must match within a few % (the insulation is mu_r=1 inert,
so the ONLY change is the mesh of the slot interior)."""
import _use40  # noqa
import sys, math, logging
import numpy as np
logging.basicConfig(level=logging.WARNING, format="%(levelname)s|%(message)s")
import motor_ai_sim.simulation.fem_solver_2d as F


def run(structured_slot, n_steps=12, gamma=12.0, I=60.0):
    return F.fem_transient_sliding_band(
        n_steps_per_period=12, n_periods=float(n_steps) / 12.0,
        gamma_deg=gamma, I_phase_rms=I,
        mesh_size_mm=1.5, min_size_mm=0.25, outer_air_factor=1.3,
        gap_layers=2.0, n_sectors=-1, structured_gap=False,
        structured_slot=structured_slot,
        return_field=False, torque_filter=True,
    )


def _peak(v):
    if isinstance(v, (list, tuple)):
        return max((abs(float(x)) for x in v), default=0.0)
    try:
        return abs(float(v))
    except Exception:
        return 0.0


def summ(tag, r):
    def g(k, d=0.0):
        return r.get(k, d)
    psi = max(_peak(g("psi_A_Wb")), _peak(g("psi_B_Wb")), _peak(g("psi_C_Wb")))
    pcu = _peak(g("P_cu_W"))
    print(f"{tag:8s} T_avg={float(g('T_avg_Nm')):.4f} Nm  Vpk={float(g('V_peak')):.2f} V  "
          f"psi_pk={psi:.5f} Wb  P_cu~{pcu:.1f}W  "
          f"P_loss={float(g('P_loss_total_avg_W')):.1f}W  "
          f"ripple={float(g('T_ripple_pct', float('nan'))):.1f}%", flush=True)
    return float(g("T_avg_Nm")), float(g("V_peak")), psi


if __name__ == "__main__":
    print("=== EM sanity: FREE vs STRUCTURED slot (40 mm, I=60A, gamma=12) ===")
    rf = run(False); Tf, Vf, Pf = summ("FREE", rf)
    rs = run(True);  Ts, Vs, Ps = summ("STRUCT", rs)
    def pct(a, b): return 100.0 * (a - b) / b if abs(b) > 1e-12 else float("nan")
    print(f"  dT_avg  = {pct(Ts, Tf):+.2f}%")
    print(f"  dV_peak = {pct(Vs, Vf):+.2f}%")
    print(f"  dpsi_pk = {pct(Ps, Pf):+.2f}%")
