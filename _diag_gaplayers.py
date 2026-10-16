"""Unification proof: optimizer path (refine_proc.run_one -> kernel -> get_fem_transient)
vs Simulation path (get_fem_transient directly), same geometry + matched args + pole=False.
After threading the 4 ambient params, the two should produce IDENTICAL torque/ripple/eff."""
import json, sys
from motor_ai_sim.config import get_config
from motor_ai_sim.optimization.refine_proc import run_one
from motor_ai_sim.routes.simulation import get_fem_transient

cfg = get_config()
oa = float(cfg.get("mesh", {}).get("outer_air_factor", 1.2))
sf = 0.0   # Simulation hardcodes stator_fillet_mm=0 (native geometry)
dm = bool(cfg.get("simulation", {}).get("demag", False))
cm = json.dumps(cfg.get("mesh", {}).get("component_mesh") or {})
I, g, steps, ns, ct, ms, mn, gl, ew = 37.86, -32.0, 72, 1, 120.0, 0.6, 0.3, 1.0, 1.68

opt = run_one({}, I, steps, ct, n_periods=1.0, gamma_deg=g, mesh_size_mm=ms, min_size_mm=mn,
              n_sectors=ns, gap_layers=gl, pole_copy=False, torque_filter=True,
              end_winding_factor=ew, rotor_eddy=True)
print(f"OPT : T={opt['T_em_Nm']:.4f}  ripple={opt['T_ripple_pct']:.3f}  eff={opt['efficiency']:.5f}")
sys.stdout.flush()

sim = get_fem_transient(n_steps_per_period=72, n_periods=1.0, gamma_deg=g, I_phase_rms=I,
                        mesh_size_mm=ms, min_size_mm=mn, outer_air_factor=oa, gap_layers=gl,
                        n_sectors=ns, stator_fillet_mm=sf, coil_temp_c=ct, end_winding_factor=ew,
                        component_mesh=cm, rotor_eddy=True, demag=dm, torque_filter=True,
                        pole_copy=False, sliding_band=True, fresh=True)
pm = sim.get("P_mech_avg_W"); pe = sim.get("P_elec_in_W")
eff = (pm / pe) if (pm and pe) else None
print(f"SIM : T={sim['T_avg_Nm']:.4f}  ripple={sim['T_ripple_pct']:.3f}  eff={eff:.5f}" if eff else
      f"SIM : T={sim['T_avg_Nm']:.4f}  ripple={sim['T_ripple_pct']:.3f}")
print("MATCH" if abs(opt['T_em_Nm'] - sim['T_avg_Nm']) < 1e-3 and abs(opt['T_ripple_pct'] - sim['T_ripple_pct']) < 0.05 else "DIFFER")
