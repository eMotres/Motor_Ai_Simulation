"""Is the full-disk field weak already in the LINEAR solve (mu_r=5000, no Picard,
no demag)?  And how many outer Dirichlet nodes does each model get?
If ns=1 has few outer nodes -> under-constrained (near-singular) -> scaled-down A.
If linear field is weak with plenty of outer nodes -> deeper mesh/source issue."""
import math, numpy as np
import motor_ai_sim.simulation.fem_solver_2d as fs
from motor_ai_sim.simulation.fem_solver_2d import (
    build_mesh_from_polygons, build_materials, _simplify_polys,
    solve_magnetostatics, _outer_boundary_nodes)
from motor_ai_sim.cadquery_geometry import CadQueryMotor
from motor_ai_sim.simulation.geometry_2d import params_from_config, MotorDomains2D

p = params_from_config(); d = MotorDomains2D(p)
r_ro,r_si = p.r_rotor_out,p.r_stator_in
slot_area = p.slot_width_m*p.slot_height_m*p.fill_factor

def tri_B(P,T,A):
    x1,x2,x3=P[0,T[0]],P[0,T[1]],P[0,T[2]]; y1,y2,y3=P[1,T[0]],P[1,T[1]],P[1,T[2]]
    a1,a2,a3=A[T[0]],A[T[1]],A[T[2]]
    det=(x2-x1)*(y3-y1)-(x3-x1)*(y2-y1); det=np.where(np.abs(det)<1e-30,1e-30,det)
    return ((x2-x1)*(a3-a1)-(x3-x1)*(a2-a1))/det, -((y3-y1)*(a2-a1)-(y2-y1)*(a3-a1))/det

for ns in (4,1):
    motor=CadQueryMotor(); polys=motor.get_2d_polygons(rotor_angle_deg=0.0)
    polys=_simplify_polys(polys,tol_mm=0.005,stator_fillet_mm=0.0)
    mesh,ct,cf=build_mesh_from_polygons(polys,0.0,4.0,min_size_mm=0.3,outer_air_factor=1.3,
        motion_band=True,band_thickness_mm=0.4,n_sectors=ns,geo_cfg=motor.parameters)
    pm=getattr(cf,"polys",polys)
    mats=build_materials({'A':0.,'B':0.,'C':0.},d.winding_layout,pm,0.0,slot_area,15)
    r=np.hypot(mesh.p[0],mesh.p[1]); rmax=float(r.max())
    outer=_outer_boundary_nodes(mesh)
    poles_per=p.num_poles//max(ns,1); anti=(poles_per%2==1)
    # LINEAR solve only (1 Picard iter = mu_r 5000 everywhere, no saturation/demag)
    A=solve_magnetostatics(mesh,ct,mats,n_sectors=ns,
        pole_pairs_per_sector_is_half_integer=anti,nonlinear_iterations=1)
    Bx,By=tri_B(mesh.p,mesh.t,A);Bmag=np.hypot(Bx,By)
    cx=mesh.p[:,mesh.t].mean(axis=1)[0];cy=mesh.p[:,mesh.t].mean(axis=1)[1];rc=np.hypot(cx,cy)
    gap=(rc>=r_ro)&(rc<=r_si)
    print("ns=%d: nodes=%d  r_max=%.4fm  outer_nodes=%d (%.2f%%)  LINEAR gap|B|=%.3f  |A|max=%.5f"
          % (ns,mesh.p.shape[1],rmax,outer.size,100*outer.size/mesh.p.shape[1],
             float(Bmag[gap].mean()),float(np.abs(A).max())),flush=True)
