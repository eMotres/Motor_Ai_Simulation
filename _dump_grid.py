import _use40  # noqa
import numpy as np
import motor_ai_sim.simulation.fem_solver_2d as F
from motor_ai_sim.cadquery_geometry import CadQueryMotor
m = CadQueryMotor()
cols, ins = F._slot_grid_columns(m.parameters)
print("n columns:", len(cols), "ins_w=", ins)
c = cols[0]
xs = c["xs"]; ys = c["ys"]
print("xs (mm):", [round(v,5) for v in xs])
print("  dx (um):", [round((xs[i+1]-xs[i])*1000,2) for i in range(len(xs)-1)])
print("ys (mm):", [round(v,5) for v in ys])
print("  dy (um):", [round((ys[i+1]-ys[i])*1000,2) for i in range(len(ys)-1)])
print("n_fit:", c["n_fit"], "ncells:", len(c["cells"]))
