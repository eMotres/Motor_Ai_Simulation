"""Diagnose where n_sectors=4 (1/4) and n_sectors=1 (full) diverge.

Run the SAME operating point (rotor=0, balanced frozen vector on phase A, 250 A)
in both, and compare the things that actually drive torque:
  - mesh size
  - total amp-turns placed in the slots  (is the current even there?)
  - |B| in the air gap                    (did the field solve?)
  - air-gap Arkkio integrand              (sign/cancellation?)
  - T_em
If the current or |B| collapse in the full model, the solve/mesh is broken;
if they match but torque differs, the torque integral is the problem."""
import math, numpy as np
import motor_ai_sim.config as C
import motor_ai_sim.simulation.fem_solver_2d as fs
from motor_ai_sim.simulation.geometry_2d import params_from_config

cfg = C.get_config(); cfg.setdefault("winding", {})
cfg["winding"]["layers"] = 1
cfg["winding"]["layout"] = "A|a|c|C|B|b|a|A|C|c|b|B|A|a|c|C|B|b|a|A|C|c|b|B"

p = params_from_config()
r_in  = p.r_rotor_out          # air-gap inner radius [m]
r_out = p.r_stator_in          # air-gap outer radius [m]
pp = p.num_poles // 2
DSHIFT = fs.DAXIS_SHIFT_DEG
I = 250.0
g0 = -(0.0 * pp + DSHIFT)       # freeze balanced vector on phase-A peak at rotor=0

def tri_B(V, Tt, A):
    """Per-triangle B=(dA/dy,-dA/dx) from nodal A (P1)."""
    x = V[:,0]; y = V[:,1]
    x1,x2,x3 = x[Tt[:,0]],x[Tt[:,1]],x[Tt[:,2]]
    y1,y2,y3 = y[Tt[:,0]],y[Tt[:,1]],y[Tt[:,2]]
    a1,a2,a3 = A[Tt[:,0]],A[Tt[:,1]],A[Tt[:,2]]
    det = (x2-x1)*(y3-y1)-(x3-x1)*(y2-y1)
    det = np.where(np.abs(det)<1e-30,1e-30,det)
    dAdx = ((y3-y1)*(a2-a1)-(y2-y1)*(a3-a1))/det
    dAdy = ((x2-x1)*(a3-a1)-(x3-x1)*(a2-a1))/det
    return dAdy, -dAdx, det/2.0   # Bx, By, signed area

for ns in (4, 1):
    r = fs.fem_solve_for_sim(rotor_angle_deg=0.0, gamma_deg=g0,
                             mesh_size_mm=4.0, n_sectors=ns, I_phase_rms=I)
    V = np.array(r["vertices"]); Tt = np.array(r["triangles"]); A = np.array(r["A_z_per_node"])
    Jz = np.array(r["J_z_per_tri"])
    Bx,By,area = tri_B(V,Tt,A); area = np.abs(area)
    cx = V[Tt].mean(axis=1)[:,0]; cy = V[Tt].mean(axis=1)[:,1]
    rc = np.hypot(cx,cy)
    gap = (rc>=r_in)&(rc<=r_out)
    Bmag = np.hypot(Bx,By)
    # amp-turns placed = sum |Jz|*area over coil tris
    amp_turns = float(np.sum(np.abs(Jz)*area))
    # arkkio integrand in the gap
    cosp=cx[gap]/rc[gap]; sinp=cy[gap]/rc[gap]
    Br = Bx[gap]*cosp+By[gap]*sinp; Bph=-Bx[gap]*sinp+By[gap]*cosp
    integ = float(np.sum(area[gap]*rc[gap]*Br*Bph))
    print("n_sectors=%d : tris=%6d  amp_turns=%.3e  |B|gap_max=%.3f mean=%.3f  arkkio_int=%+.3e  T_em=%+.3f Nm"
          % (ns, Tt.shape[0], amp_turns, float(Bmag[gap].max() if gap.any() else 0),
             float(Bmag[gap].mean() if gap.any() else 0), integ, r["T_em_Nm"]), flush=True)
