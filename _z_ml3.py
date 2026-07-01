import _use40  # noqa
import motor_ai_sim.simulation.fem_solver_2d as F
from motor_ai_sim.cadquery_geometry import CadQueryMotor
m=CadQueryMotor()
polys=m.get_2d_polygons(rotor_angle_deg=0.0)
polys=F._simplify_polys(polys, tol_mm=0.005, stator_fillet_mm=0.0, n_slip=1008, gap_layers=2, structured_gap=True, band_mode="merged")
print("spec:", polys.get("structured_gap_spec"))
ps,pr=F._split_polys_for_sliding_band(polys)
print("ROTOR spec:", pr.get("structured_gap_spec"))
print("STATOR spec:", ps.get("structured_gap_spec"))
