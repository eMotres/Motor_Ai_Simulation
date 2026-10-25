"""A tapered pocket must engage its other walls instead of cycling or tying."""
import numpy as np
import pytest
from skfem import MeshTri
from motor_ai_sim.simulation.mechanical import contact as c
from motor_ai_sim.simulation.mechanical import rotor_stress as rs


def pocket():
    mesh = MeshTri.init_tensor(np.array([-10., -6., -3., 0., 3., 6., 10.])*1e-3,
                               np.linspace(.020, .040, 11))
    centers = mesh.p[:, mesh.t].mean(axis=1)
    magnet = (abs(centers[0]) < .006) & (centers[1] > .022) & (centers[1] < .038)
    part = np.where(magnet, c.PART_MAGNET, c.PART_ROTOR)
    points = mesh.p.copy()
    inner = abs(points[0]) <= .006
    width = .006 - .003 * np.clip((points[1] - .022)/.016, 0., 1.)
    points[0, inner] *= width[inner]/.006
    mesh = MeshTri(points, mesh.t)
    cs = c.build_contact_system(mesh, part,
                               {'magnet_rotor': c.ContactSpec('separation', .2)}, order=1)
    ne = len(part)
    C = np.broadcast_to(rs.isotropic_C(2e11, .3), (ne,3,3)).copy()
    eps = np.zeros((ne,3)); eps[part==c.PART_ROTOR,:2] = 1e-3
    rho = np.where(part==c.PART_ROTOR,40000.,7600.)
    basis,K,fr,fe = rs.assemble_plane_stress(cs.mesh,C,rho,eps,order=1)
    vertices = np.flatnonzero(cs.mesh.p[1] < .02000001)
    dofs = np.repeat(basis.nodal_dofs[:,vertices].T,2,axis=0)
    hold = c.HeldBoundary(dofs=dofs,dirs=np.tile(np.eye(2),(len(vertices),1)),
                          arm=np.zeros(len(dofs)),vertices=vertices,label='foundation')
    ids = np.unique(basis.element_dofs[:,part==c.PART_MAGNET]); ix=ids[ids%2==0]
    ramp=np.zeros(basis.N);ramp[ix]=5000./len(ix)
    return K,fe+fr*500.**2,cs,basis,hold,ramp


@pytest.mark.parametrize('backend', ['superlu', 'pypardiso'])
def test_tapered_pocket_reaches_unilateral_equilibrium(backend):
    if backend == 'superlu':
        from scipy.sparse.linalg import spsolve
        solve = spsolve
    else:
        name, solve = c._solver()
        if name != 'pypardiso':
            pytest.skip('PARDISO unavailable or its threading layer is unsafe')
    K,f,cs,basis,hold,ramp = pocket()
    sol=c.solve_contact(K,f,cs,basis,hold=hold,f_ramp=ramp,load_steps=3,solve_fn=solve)
    assert sol.converged and sol.consistent
    # Independent physical balance: a force clipped after a secant solve must
    # not be reported as convergence merely because Boolean flags repeat.
    residual=K@(sol.u-sol.u_seat)-f-ramp
    for tangent,force in [(False,sol.lam_n),(True,sol.force_t)]:
        r,col,val,pairs=c._pair_rows(cs,np.ones(cs.n_pairs,dtype=bool),tangent)
        np.add.at(residual,col,val*np.repeat(force[pairs],4))
    np.add.at(residual,hold.dofs.ravel(),
              (hold.dirs*sol.hold_mult[:,None]).ravel())
    assert np.linalg.norm(residual)/np.linalg.norm(f+ramp) < 1e-8
    kp=c.CONTACT_STIFFNESS*K.diagonal().mean()
    expected=np.clip(kp*sol.slip, -cs.pair_mu*sol.force_n.clip(0),
                     cs.pair_mu*sol.force_n.clip(0))
    un=cs.pair_unilateral
    assert np.linalg.norm(sol.force_t[un]-expected[un])/np.linalg.norm(f+ramp) < 1e-8
    assert sol.tension_frac == 0
    assert sol.free_parts == []
    assert np.max(np.abs(sol.u)) < .001
    mu=cs.pair_mu
    assert np.all(np.abs(sol.force_t[cs.pair_unilateral]) <=
                  mu[cs.pair_unilateral]*sol.force_n[cs.pair_unilateral]+1e-8)


@pytest.mark.parametrize('order', [1, 2])
def test_periodic_seam_penalty_is_counted_once(order):
    from motor_ai_sim.simulation.mechanical import symmetry as sym
    nsector = 4

    def ring(nangle, full):
        angles = np.linspace(0., 2*np.pi if full else np.pi/2,
                             nangle, endpoint=not full)
        radii = np.array([.02, .03, .04])
        points = np.array([[r*np.cos(a), r*np.sin(a)] for a in angles for r in radii]).T
        triangles=[]; parts=[]
        for j in range(nangle if full else nangle-1):
            nxt=(j+1)%nangle
            for i in range(2):
                a,b,d,e=j*3+i,j*3+i+1,nxt*3+i,nxt*3+i+1
                triangles.extend([[a,b,e],[a,e,d]])
                parts.extend([c.PART_ROTOR if i==0 else c.PART_MAGNET]*2)
        mesh=MeshTri(points,np.array(triangles).T)
        return c.build_contact_system(mesh,np.array(parts),
                    {'magnet_rotor':c.ContactSpec('separation',.2)},order=order)

    full=ring(32,True);sector=ring(9,False)
    plan=sym.SectorPlan(nsector,np.pi/2,0.)
    weights=sym.cut_face_pair_weights(sector,plan)
    assert (weights==.5).sum()==2
    assert nsector*weights.sum()==full.n_pairs
    # The old all-one weights add one whole spring per sector at the seam.
    assert nsector*sector.n_pairs > full.n_pairs
    # Energy, normal preload and Coulomb bound all use this same weight.
    gap=1e-8;kc=1e12
    assert nsector*np.sum(.5*kc*weights*gap**2)==pytest.approx(.5*kc*full.n_pairs*gap**2)
    basis,_=rs.make_basis(sector.mesh,order)
    ties=sym.build_cyclic_ties(basis,sector.mesh,sector.part_tri,plan)
    assert ties.match_error_m < 1e-12
    assert len(ties.dofs_a)==len(ties.dofs_b)
