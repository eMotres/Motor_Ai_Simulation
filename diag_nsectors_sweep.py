"""The open-circuit field must NOT depend on the symmetry reduction n_sectors.
Sweep n_sectors and compare gap |B| (demag OFF to isolate the linear field).
n_sectors with ODD poles/sector use anti-periodic BC; EVEN use periodic; 1 = none.
Whatever value the majority agree on is physical; outliers are buggy."""
import math, numpy as np
import motor_ai_sim.simulation.fem_solver_2d as fs
from motor_ai_sim.simulation.geometry_2d import params_from_config

fs._DEMAG_ENABLED = False
p = params_from_config(); r_ri,r_ro,r_si = p.r_rotor_in,p.r_rotor_out,p.r_stator_in
def tri_B(V,Tt,A):
    x=V[:,0];y=V[:,1]
    x1,x2,x3=x[Tt[:,0]],x[Tt[:,1]],x[Tt[:,2]]; y1,y2,y3=y[Tt[:,0]],y[Tt[:,1]],y[Tt[:,2]]
    a1,a2,a3=A[Tt[:,0]],A[Tt[:,1]],A[Tt[:,2]]
    det=(x2-x1)*(y3-y1)-(x3-x1)*(y2-y1); det=np.where(np.abs(det)<1e-30,1e-30,det)
    return ((x2-x1)*(a3-a1)-(x3-x1)*(a2-a1))/det, -((y3-y1)*(a2-a1)-(y2-y1)*(a3-a1))/det

print("Open circuit, DEMAG OFF.  pole_pairs=%d, poles=%d" % (p.num_poles//2, p.num_poles))
print(" n_sectors | poles/sec | BC        | GAP|B|mean | |A_z|max")
for ns in (1,2,4,7,14,28):
    pps = p.num_poles/ns
    bc = "none" if ns==1 else ("anti-per" if (p.num_poles//ns)%2==1 else "periodic")
    try:
        r=fs.fem_solve_for_sim(rotor_angle_deg=0.0,gamma_deg=0.0,mesh_size_mm=4.0,n_sectors=ns,I_phase_rms=0.0)
        V=np.array(r["vertices"]);Tt=np.array(r["triangles"]);A=np.array(r["A_z_per_node"])
        Bx,By=tri_B(V,Tt,A);Bmag=np.hypot(Bx,By)
        cx=V[Tt].mean(axis=1)[:,0];cy=V[Tt].mean(axis=1)[:,1];rc=np.hypot(cx,cy)
        gap=(rc>=r_ro)&(rc<=r_si)
        print("  %7d  | %8.1f  | %-9s | %9.3f  | %.4f"
              % (ns, pps, bc, float(Bmag[gap].mean()), float(np.abs(A).max())), flush=True)
    except Exception as e:
        print("  %7d  | %8.1f  | %-9s | FAILED: %s" % (ns,pps,bc,str(e)[:50]), flush=True)
