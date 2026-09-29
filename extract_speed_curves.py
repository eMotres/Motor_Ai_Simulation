"""Speed-sweep FEM for the reference passport: iron loss, magnet eddy loss,
torque and terminal voltage vs rpm.  The configurator interpolates these curves
instead of the analytical f^1.5 / f^2 loss laws.

Runs the stable sliding-band transient at the reference base current/gamma,
sweeping rpm by mutating the cached config (the solver reads
get_config()["simulation"]["rpm"], f_elec is derived).

Usage:
  python extract_speed_curves.py                 # full sweep 1000..6000
  python extract_speed_curves.py 2000 5000       # specific rpms (quick test)
  NSPP=6 python extract_speed_curves.py 2000      # coarse (fast) test
"""
import os
import sys
import json

from motor_ai_sim.config import get_config
from motor_ai_sim.routes.simulation import get_fem_transient

cfg = get_config()
rpms = [float(x) for x in sys.argv[1:]] or [1000.0, 2000.0, 3000.0, 4000.0, 5000.0, 6000.0]
NSPP = int(os.environ.get("NSPP", "12"))
I_RMS = float(os.environ.get("IRMS", "110"))
GAMMA = float(os.environ.get("GAMMA", "28"))

out = {"I_rms": I_RMS, "gamma_deg": GAMMA, "rpm": [], "T_Nm": [], "Vpeak_V": [],
       "Pfe_W": [], "Pmag_W": [], "Pcu_W": []}

for r in rpms:
    cfg.setdefault("simulation", {})["rpm"] = r
    # fresh=True bypasses the _sb_key cache — rpm is read from config (not a
    # param), so the cache key doesn't see it; without fresh every point would
    # return the first rpm's cached result.
    d = get_fem_transient(n_steps_per_period=NSPP, n_periods=1.0, gamma_deg=GAMMA,
                          I_phase_rms=I_RMS, mesh_size_mm=6.0, n_sectors=4,
                          sliding_band=True, rotor_eddy=True, fresh=True)

    def g(k):  # scalar value, or period-mean if the key is a time-series list
        v = d.get(k)
        if v is None:
            return 0.0
        if isinstance(v, (list, tuple)):
            return float(sum(v) / len(v)) if v else 0.0
        return float(v)

    T = g("T_avg_Nm") or g("T_em_Nm")
    Pfe = g("P_fe_W")                                  # iron (lamination) loss
    Pmag = g("P_mag_eddy_W") + g("P_shaft_eddy_W")     # magnet + shaft eddy loss
    Pcu = g("P_cu_W")                                  # copper (DC + AC)
    out["rpm"].append(r)
    out["T_Nm"].append(round(T, 2))
    out["Vpeak_V"].append(round(g("V_peak"), 1))
    out["Pfe_W"].append(round(Pfe, 1))
    out["Pmag_W"].append(round(Pmag, 1))
    out["Pcu_W"].append(round(Pcu, 1))
    print(f"rpm_set={r:.0f} rpm_used={d.get('rpm')} T={T:.1f} Vpk={g('V_peak'):.0f} "
          f"Pfe={Pfe:.1f} Pmag={Pmag:.1f} Pcu={Pcu:.1f}", flush=True)

print("CURVES_JSON " + json.dumps(out), flush=True)
