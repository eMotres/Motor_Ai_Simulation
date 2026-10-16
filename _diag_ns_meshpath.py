"""Which mesh-build path do ns=1 vs ns=4 actually take? (INFO logs on)

Runs run_one() in-process with logging.INFO and prints the mesh/band/copy
log lines for each sector count.  Usage: python _diag_ns_meshpath.py [ns]
"""
import io
import logging
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")

from motor_ai_sim.optimization.refine_proc import run_one

NS = int(sys.argv[1]) if len(sys.argv) > 1 else 4

KEY = ("copy", "template", "wedge", "stitch", "belt", "tris", "slip nodes", "band",
       "snapped", "welded", "sector", "structured", "gap (", "auto-refined",
       "FEM solve", "FEM:")
SKIP = ("CellBasis", "Assembling", "Solving", "Initializing")

buf = io.StringIO()
h = logging.StreamHandler(buf)
h.setLevel(logging.INFO)
logging.getLogger().addHandler(h)

res = run_one({}, 100.0, 24, coil_temp_c=120.0, n_periods=1.0, gamma_deg=28.0,
              mesh_size_mm=4.0, min_size_mm=0.3, n_sectors=NS, pole_copy=True,
              torque_filter=True, gap_layers=3.0, end_winding_factor=0.0,
              rotor_eddy=False, hi_fidelity=False, structured_gap=True,
              airgap_macro=False, iron_template=False, geo_mesh=False)

print(f"=== ns={NS} log lines ===")
for line in buf.getvalue().splitlines():
    if any(s in line for s in SKIP):
        continue
    if any(k.lower() in line.lower() for k in KEY):
        print(line)
r = res.get("res", res) if isinstance(res, dict) else {}
print(f"=== ns={NS} result: T={r.get('T_em_Nm') if not isinstance(r.get('T_em_Nm'), list) else r.get('T_avg_Nm')} "
      f"ripple={r.get('T_ripple_pct')} raw={r.get('T_ripple_raw_pct')} "
      f"noise={r.get('T_noise_floor_pct')} Vpk={r.get('V_peak')}")
