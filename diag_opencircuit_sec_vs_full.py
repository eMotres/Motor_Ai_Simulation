"""Open-circuit (I=0, magnets only) sector vs full diagnostic.

Removes the armature entirely so we compare the MAGNET field alone, which has a
known physical magnitude (NdFeB Br=1.19 T -> air-gap |B| ~ 0.5-0.9 T peak).
Whichever model lands there is right.  Also reports the field by radial region
(rotor iron+magnets / air gap / stator iron) to see WHERE the field collapses."""
import math, numpy as np
import motor_ai_sim.config as C
import motor_ai_sim.simulation.fem_solver_2d as fs
from motor_ai_sim.simulation.geometry_2d import params_from_config

p = params_from_config()
r_ri, r_ro = p.r_rotor_in, p.r_rotor_out
r_si = p.r_stator_in
print("radii [mm]: rotor_in=%.1f rotor_out=%.1f stator_in=%.1f  gap=%.2f mm"
      % (r_ri*1e3, r_ro*1e3, r_si*1e3, (r_si-r_ro)*1e3))

def tri_B(V, Tt, A):
    x = V[:,0]; y = V[:,1]
    x1,x2,x3 = x[Tt[:,0]],x[Tt[:,1]],x[Tt[:,2]]
    y1,y2,y3 = y[Tt[:,0]],y[Tt[:,1]],y[Tt[:,2]]
    a1,a2,a3 = A[Tt[:,0]],A[Tt[:,1]],A[Tt[:,2]]
    det = (x2-x1)*(y3-y1)-(x3-x1)*(y2-y1)
    det = np.where(np.abs(det)<1e-30,1e-30,det)
    dAdx = ((y3-y1)*(a2-a1)-(y2-y1)*(a3-a1))/det
    dAdy = ((x2-x1)*(a3-a1)-(x3-x1)*(a2-a1))/det
    return dAdy, -dAdx

for ns in (4, 1):
    r = fs.fem_solve_for_sim(rotor_angle_deg=0.0, gamma_deg=0.0,
                             mesh_size_mm=4.0, n_sectors=ns, I_phase_rms=0.0)
    V = np.array(r["vertices"]); Tt = np.array(r["triangles"]); A = np.array(r["A_z_per_node"])
    Bx,By = tri_B(V,Tt,A); Bmag = np.hypot(Bx,By)
    cx = V[Tt].mean(axis=1)[:,0]; cy = V[Tt].mean(axis=1)[:,1]; rc = np.hypot(cx,cy)
    rotor = (rc>=r_ri)&(rc<=r_ro)
    gap   = (rc>=r_ro)&(rc<=r_si)
    def stat(m): return (float(Bmag[m].mean()) if m.any() else 0, float(Bmag[m].max()) if m.any() else 0, int(m.sum()))
    rm,rx,rn = stat(rotor); gm,gx,gn = stat(gap)
    print("n_sectors=%d : |A_z| range [%.4f,%.4f]  rotor|B| mean=%.3f max=%.2f (n=%d)  GAP|B| mean=%.3f max=%.2f (n=%d)"
          % (ns, float(A.min()), float(A.max()), rm, rx, rn, gm, gx, gn), flush=True)
