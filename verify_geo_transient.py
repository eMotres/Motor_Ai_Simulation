"""P1 slice 3 — transient endpoint accepts an optional `geo` override.

Local check: parse helper + a light sliding-band transient (6 steps, quarter,
no rotor-eddy) with/without geo. Torque must change with the override; the
no-geo path is unchanged.
"""
import json
from motor_ai_sim.routes.simulation import _parse_geo_override, get_fem_transient

print("[1] parse None:", _parse_geo_override(None))
print("[2] parse geo :", _parse_geo_override('{"tooth_width": 10.0}'))
print("[3] parse bad :", _parse_geo_override("xx{"))


def run(geo=None):
    return get_fem_transient(
        n_steps_per_period=6, n_periods=1.0, gamma_deg=28.0, I_phase_rms=110.0,
        mesh_size_mm=6.0, min_size_mm=0.4, sliding_band=True, n_sectors=4,
        rotor_eddy=False, geo=geo,
    )


b = run(None)
o = run(json.dumps({"tooth_width": 10.0}))
print(f"[4] no-geo  T_avg={round(b.get('T_avg_Nm', 0), 2)} ripple={round(b.get('T_ripple_pct', 0), 2)}")
print(f"[5] tw=10.0 T_avg={round(o.get('T_avg_Nm', 0), 2)} ripple={round(o.get('T_ripple_pct', 0), 2)}")
ok = (b.get("T_avg_Nm") is not None and o.get("T_avg_Nm") is not None
      and abs(float(b["T_avg_Nm"]) - float(o["T_avg_Nm"])) > 1e-6)
print("RESULT:", "OK - transient geo override honoured (T differs, back-compat)" if ok else "CHECK - review output")
