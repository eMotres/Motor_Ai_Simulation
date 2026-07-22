"""Decisive test: is the demag runaway what collapses the full-disk field?
Run open-circuit sector vs full with _DEMAG_ENABLED=False and compare gap |B|.
If the full disk now matches the sector, the demag runaway is the proximate bug."""
import math, numpy as np
import motor_ai_sim.config as C
import motor_ai_sim.simulation.fem_solver_2d as fs
from motor_ai_sim.simulation.geometry_2d import params_from_config

fs._DEMAG_ENABLED = False     # turn OFF the self-consistent demag update

p = params_from_config(); r_ri,r_ro,r_si = p.r_rotor_in,p.r_rotor_out,p.r_stator_in
def tri_B(V,Tt,A):
    x=V[:,0];y=V[:,1]
    x1,x2,x3=x[Tt[:,0]],x[Tt[:,1]],x[Tt[:,2]]; y1,y2,y3=y[Tt[:,0]],y[Tt[:,1]],y[Tt[:,2]]
    a1,a2,a3=A[Tt[:,0]],A[Tt[:,1]],A[Tt[:,2]]
    det=(x2-x1)*(y3-y1)-(x3-x1)*(y2-y1); det=np.where(np.abs(det)<1e-30,1e-30,det)
    dAdx=((y3-y1)*(a2-a1)-(y2-y1)*(a3-a1))/det; dAdy=((x2-x1)*(a3-a1)-(x3-x1)*(a2-a1))/det
    return dAdy,-dAdx

print("DEMAG DISABLED — open circuit (I=0)")
for ns in (4,1):
    r=fs.fem_solve_for_sim(rotor_angle_deg=0.0,gamma_deg=0.0,mesh_size_mm=4.0,n_sectors=ns,I_phase_rms=0.0)
    V=np.array(r["vertices"]);Tt=np.array(r["triangles"]);A=np.array(r["A_z_per_node"])
    Bx,By=tri_B(V,Tt,A);Bmag=np.hypot(Bx,By)
    cx=V[Tt].mean(axis=1)[:,0];cy=V[Tt].mean(axis=1)[:,1];rc=np.hypot(cx,cy)
    gap=(rc>=r_ro)&(rc<=r_si); rot=(rc>=r_ri)&(rc<=r_ro)
    print(" n_sectors=%d : |A_z|max=%.4f  rotor|B|mean=%.3f  GAP|B|mean=%.3f"
          % (ns,float(np.abs(A).max()),float(Bmag[rot].mean()),float(Bmag[gap].mean())),flush=True)
