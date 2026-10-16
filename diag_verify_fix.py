"""Verify the weld fix: sector (4) vs full (1) must now AGREE on field AND torque,
both at open circuit and under load.  demag ON (production default)."""
import math, numpy as np
import motor_ai_sim.config as C
import motor_ai_sim.simulation.fem_solver_2d as fs
from motor_ai_sim.simulation.geometry_2d import params_from_config

p = params_from_config(); r_ri,r_ro,r_si = p.r_rotor_in,p.r_rotor_out,p.r_stator_in
pp = p.num_poles//2; DSHIFT = fs.DAXIS_SHIFT_DEG
def tri_B(V,Tt,A):
    x=V[:,0];y=V[:,1]
    x1,x2,x3=x[Tt[:,0]],x[Tt[:,1]],x[Tt[:,2]]; y1,y2,y3=y[Tt[:,0]],y[Tt[:,1]],y[Tt[:,2]]
    a1,a2,a3=A[Tt[:,0]],A[Tt[:,1]],A[Tt[:,2]]
    det=(x2-x1)*(y3-y1)-(x3-x1)*(y2-y1); det=np.where(np.abs(det)<1e-30,1e-30,det)
    return ((x2-x1)*(a3-a1)-(x3-x1)*(a2-a1))/det, -((y3-y1)*(a2-a1)-(y2-y1)*(a3-a1))/det

def gapB(r):
    V=np.array(r["vertices"]);Tt=np.array(r["triangles"]);A=np.array(r["A_z_per_node"])
    Bx,By=tri_B(V,Tt,A);Bmag=np.hypot(Bx,By)
    cx=V[Tt].mean(axis=1)[:,0];cy=V[Tt].mean(axis=1)[:,1];rc=np.hypot(cx,cy)
    return float(Bmag[(rc>=r_ro)&(rc<=r_si)].mean())

print("After WELD fix (demag ON). Compare sector(4) vs full(1):")
for label,I,th in (("open-circuit",0.0,0.0),("load 250A @rotor0",250.0,0.0)):
    g = -(th*pp+DSHIFT)
    out=[]
    for ns in (4,1):
        r=fs.fem_solve_for_sim(rotor_angle_deg=th,gamma_deg=(g if I>0 else 0.0),
                               mesh_size_mm=4.0,n_sectors=ns,I_phase_rms=I)
        out.append((gapB(r), float(r["T_em_Nm"])))
    print("  %-20s sector: GAP|B|=%.3f T=%+.2f | full: GAP|B|=%.3f T=%+.2f"
          % (label, out[0][0],out[0][1], out[1][0],out[1][1]), flush=True)
