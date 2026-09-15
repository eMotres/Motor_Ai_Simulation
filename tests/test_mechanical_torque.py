"""The TORQUE load on the rotor — analytic checks, friction, and the route.

Added 2026-09-07 for the user's request, in his words:

    "ты можешь это проверить: добавь ещё и момент на ротор, пусть действуют все
     силы; сделай меню, чтобы можно было выбрать центробежную, момент и обе."

The design behind it is a spoke rotor whose iron bridges are assembly features
only — they yield on the first spin-up — after which each pole is held
TANGENTIALLY by nothing but friction against the sleeve and the magnets.  So the
question these tests exist to make answerable is not "does the sleeve hold the
magnets down" (the centrifugal model already answers that) but "can the machine's
torque get out of the poles at all".

The order below is the order of trust:

  (a) a solid ring under pure torque against the closed form
      ``sigma_r_theta = T / (2 pi r^2 L)``, and the bore reaction against T;
  (b) superposition — ``both`` = ``centrifugal`` + ``torque`` — on a fully
      bonded model, where the problem IS linear and nothing may drift;
  (c) the regularised Coulomb friction: stick below the cone, slide on it, and
      the transmitted traction capped at mu*p;
  (d) the route: ``loads``, ``torque_nm``, ``cases=single``, and /last;
  (e) the machine the user is actually asking about, from a READ-ONLY copy of
      the live config.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from motor_ai_sim.simulation.mechanical import contact as ctc
from motor_ai_sim.simulation.mechanical import rotor_stress as rs

E_STEEL, NU_STEEL, RHO_STEEL = 210e9, 0.30, 7800.0


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _annulus(ri: float, ro: float, h: float):
    """Annulus mesh in METRES (gmsh, free triangles)."""
    import gmsh
    from skfem import MeshTri

    from motor_ai_sim.simulation.sb_domains import _GMSH_LOCK

    _GMSH_LOCK.acquire()
    try:
        try:
            gmsh.initialize([], interruptible=False)
        except TypeError:
            gmsh.initialize()
        try:
            gmsh.option.setNumber("General.Terminal", 0)
            gmsh.model.add("annulus")
            occ = gmsh.model.occ
            o = occ.addDisk(0, 0, 0, ro, ro)
            i = occ.addDisk(0, 0, 0, ri, ri)
            occ.cut([(2, o)], [(2, i)])
            occ.synchronize()
            gmsh.option.setNumber("Mesh.MeshSizeMin", h * 0.6)
            gmsh.option.setNumber("Mesh.MeshSizeMax", h)
            gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 120)
            gmsh.model.mesh.generate(2)
            nt, nc, _ = gmsh.model.mesh.getNodes()
            et, _, en = gmsh.model.mesh.getElements(2)
            p = np.asarray(nc).reshape(-1, 3)[:, :2]
            order = np.argsort(np.asarray(nt, np.int64))
            m = np.zeros(int(np.max(nt)) + 1, np.int64)
            m[np.asarray(nt, np.int64)[order]] = np.arange(len(nt))
            p = p[order]
            t = np.vstack([m[np.asarray(e, np.int64)].reshape(-1, 3)
                           for ty, e in zip(et, en) if ty == 2])
        finally:
            gmsh.finalize()
    finally:
        _GMSH_LOCK.release()

    used = np.unique(t)
    rm = -np.ones(len(p), np.int64)
    rm[used] = np.arange(used.size)
    p, t = p[used], rm[t]
    v0, v1, v2 = p[t[:, 0]], p[t[:, 1]], p[t[:, 2]]
    a = ((v1[:, 0] - v0[:, 0]) * (v2[:, 1] - v0[:, 1])
         - (v2[:, 0] - v0[:, 0]) * (v1[:, 1] - v0[:, 1]))
    t[a < 0] = t[a < 0][:, [0, 2, 1]]
    return MeshTri(np.ascontiguousarray(p.T), np.ascontiguousarray(t.T))


def _two_blocks(n: int = 8, w: float = 0.02):
    """A ``w`` x ``2w`` stack of two square blocks meeting at y = 0.

    The upper block is tagged ``magnet`` and the lower ``rotor``, so
    ``build_contact_system`` finds them as the ``magnet_rotor`` pair whose
    normal points DOWN (a -> b) and whose tangent is therefore +x.
    """
    from skfem import MeshTri

    xs = np.linspace(0.0, w, n + 1)
    ys = np.linspace(-w, w, 2 * n + 1)
    X, Y = np.meshgrid(xs, ys, indexing="ij")
    p = np.stack([X.reshape(-1), Y.reshape(-1)], axis=1)
    nid = np.arange(p.shape[0]).reshape(n + 1, 2 * n + 1)
    tri = []
    for i in range(n):
        for j in range(2 * n):
            a, b = nid[i, j], nid[i + 1, j]
            c, d = nid[i + 1, j + 1], nid[i, j + 1]
            tri += [[a, b, c], [a, c, d]]
    t = np.asarray(tri, dtype=np.int64)
    v0, v1, v2 = p[t[:, 0]], p[t[:, 1]], p[t[:, 2]]
    ar = ((v1[:, 0] - v0[:, 0]) * (v2[:, 1] - v0[:, 1])
          - (v2[:, 0] - v0[:, 0]) * (v1[:, 1] - v0[:, 1]))
    t[ar < 0] = t[ar < 0][:, [0, 2, 1]]
    cen = (p[t[:, 0]] + p[t[:, 1]] + p[t[:, 2]]) / 3.0
    part = np.where(cen[:, 1] > 0, ctc.PART_MAGNET, ctc.PART_ROTOR).astype(np.int8)
    return MeshTri(np.ascontiguousarray(p.T),
                   np.ascontiguousarray(t.T)), part, w


def _iso(mesh, E=E_STEEL, nu=NU_STEEL, rho=RHO_STEEL):
    ne = mesh.t.shape[1]
    C = np.broadcast_to(rs.isotropic_C(E, nu), (ne, 3, 3)).copy()
    return C, np.full(ne, rho)


def _solve_held(mesh, C, rho, f, hold):
    """One linear solve of ``K u = f`` with a ``HeldBoundary`` and no contact.

    The same saddle system ``solve_contact`` builds, minus the active set: this
    is what the analytic ring is driven with, so a failure points at the torque
    traction and the reaction rows and not at the contact iteration.
    Returns (u, reaction moment per metre of stack).
    """
    import scipy.sparse as sp
    import scipy.sparse.linalg as spla

    _basis, K, _fr, _fe = rs.assemble_plane_stress(mesh, C, rho, None, order=2)
    ndof = K.shape[0]
    s = math.sqrt(float(K.diagonal().mean()))
    rows = np.repeat(np.arange(hold.n_rows), 2)
    G = sp.coo_matrix((hold.dirs.reshape(-1) * s,
                       (rows, hold.dofs.reshape(-1))),
                      shape=(hold.n_rows, ndof)).tocsr()
    A = sp.bmat([[K, G.T], [G, None]], format="csc")
    sol = spla.spsolve(A, np.concatenate([f, np.zeros(hold.n_rows)]))
    mult = sol[ndof:] * s
    return sol[:ndof], float((mult * hold.arm).sum())


# ---------------------------------------------------------------------------
# (a) the closed form
# ---------------------------------------------------------------------------

RI, RO, LSTACK, TORQUE = 0.020, 0.050, 0.100, 180.0


@pytest.fixture(scope="module")
def ring_torque():
    mesh = _annulus(RI, RO, 0.0035)
    ne = mesh.t.shape[1]
    part = np.full(ne, rs.PART_ROTOR, np.int8)
    C, rho = _iso(mesh)
    f, rep = rs.torque_load(mesh, part, TORQUE, LSTACK, order=2)
    hold = rs.bore_hold(mesh, part, order=2)
    u, react = _solve_held(mesh, C, rho, f, hold)
    return mesh, part, C, u, react, rep


def test_ring_shear_matches_the_closed_form(ring_torque):
    """sigma_r_theta = T / (2 pi r^2 L) at every radius.

    This is the whole torque model in one equation: a uniform tangential
    traction on the OD of a ring produces a shear that falls as 1/r^2, because
    the SAME moment has to cross every circle.  Within 3 %, away from the two
    boundaries where the mesh and the bore rows are.
    """
    mesh, _part, C, u, _react, _rep = ring_torque
    sig, _peak, _vm = rs.recover_stress(mesh, u, C, None, order=2)
    p = mesh.p.T
    cen = (p[mesh.t[0]] + p[mesh.t[1]] + p[mesh.t[2]]) / 3.0
    r = np.hypot(cen[:, 0], cen[:, 1])
    phi = np.arctan2(cen[:, 1], cen[:, 0])
    c, s = np.cos(phi), np.sin(phi)
    sxx, syy, sxy = sig[:, 0], sig[:, 1], sig[:, 2]
    srt = (syy - sxx) * c * s + sxy * (c * c - s * s)
    exact = TORQUE / (2.0 * math.pi * r ** 2 * LSTACK)
    band = (r > RI * 1.25) & (r < RO * 0.90)
    assert band.sum() > 100
    err = np.abs(srt[band] / exact[band] - 1.0)
    assert err.max() < 0.03, f"worst {err.max():.4f}"
    # …and it is not a lucky mean: the whole field is on the curve.
    assert np.abs(np.mean(srt[band] / exact[band]) - 1.0) < 3e-3


def test_ring_bore_reaction_equals_the_applied_torque(ring_torque):
    """What went in at the gap comes out at the bore.

    The reaction is the multiplier of the tangential rows times their radius; if
    the traction, the constraint or the sign convention were wrong this is the
    number that would say so.  It is a constraint identity, so 1e-6 is generous.
    """
    *_, react, _rep = ring_torque
    assert react * LSTACK == pytest.approx(TORQUE, rel=1e-6)


def test_ring_traction_report_is_the_nominal_one_on_a_full_circle(ring_torque):
    """On a real (non-circular) pole top the applied traction is renormalised
    onto the surface that is actually there; on a full circle the two agree."""
    *_, rep = ring_torque
    assert rep["traction_mpa"] == pytest.approx(rep["traction_nominal_mpa"],
                                                rel=2e-3)
    assert rep["coverage"] == pytest.approx(1.0, abs=0.01)
    assert rep["r_gap_mm"] == pytest.approx(RO * 1e3, rel=2e-3)
    assert "motoring" in rep["direction"]


def test_a_torque_with_no_stack_length_is_refused():
    """tau = T / (2 pi r^2 L): L = 0 is an infinite traction, not a default."""
    mesh = _annulus(RI, RO, 0.006)
    part = np.full(mesh.t.shape[1], rs.PART_ROTOR, np.int8)
    with pytest.raises(ValueError, match="stack length"):
        rs.torque_load(mesh, part, TORQUE, 0.0, order=2)


# ---------------------------------------------------------------------------
# (b) superposition on a bonded model
# ---------------------------------------------------------------------------

def test_both_is_the_exact_superposition_of_centrifugal_and_torque():
    """Bonded everywhere, so nothing can open and the problem is LINEAR.

    Two parts (a shaft ring inside a rotor ring), joined by the default bonded
    ``shaft_rotor`` contact, and the same held bore in all three solves — the
    only way superposition is a statement about the loads rather than about the
    boundary conditions.  A drift here would mean the torque traction depends on
    the solution, which it must not.
    """
    mesh = _annulus(RI, RO, 0.004)
    p = mesh.p.T
    cen = (p[mesh.t[0]] + p[mesh.t[1]] + p[mesh.t[2]]) / 3.0
    r = np.hypot(cen[:, 0], cen[:, 1])
    part = np.where(r < 0.5 * (RI + RO), ctc.PART_SHAFT,
                    ctc.PART_ROTOR).astype(np.int8)
    spec = {k: ctc.ContactSpec("bonded", 0.0) for k in ctc.PAIR_ORDER}
    cs = ctc.build_contact_system(mesh, part, spec, order=2)
    assert cs.iface("shaft_rotor").n_facets > 0

    # The split mesh keeps the ELEMENT table, so the per-element material
    # arrays built on the conforming mesh apply to it unchanged.
    C, rho = _iso(mesh)
    basis, K, f_rot, _f_eig = rs.assemble_plane_stress(
        cs.mesh, C, rho, None, order=2)
    f_tq, _rep = rs.torque_load(cs.mesh, part, TORQUE, LSTACK, order=2)
    hold = rs.bore_hold(cs.mesh, part, order=2)
    omega = 20000.0 * 2.0 * math.pi / 60.0
    f_cent = f_rot * omega ** 2

    def run(f):
        return ctc.solve_contact(K, f, cs, basis, max_iter=30, hold=hold)

    a = run(f_cent)
    b = run(f_tq)
    ab = run(f_cent + f_tq)
    assert a.converged and b.converged and ab.converged
    ref = float(np.abs(ab.u).max())
    assert ref > 0
    err = float(np.abs(ab.u - (a.u + b.u)).max()) / ref
    assert err < 1e-6, f"superposition drifted by {err:.2e}"
    # …and the centrifugal case carries NO net torque while the torque one does
    assert abs(a.hold_torque_n_m_per_m) * LSTACK < 1e-6 * TORQUE
    assert b.hold_torque_n_m_per_m * LSTACK == pytest.approx(TORQUE, rel=1e-6)
    assert ab.hold_torque_n_m_per_m * LSTACK == pytest.approx(TORQUE, rel=1e-6)


# ---------------------------------------------------------------------------
# (c) regularised Coulomb friction
# ---------------------------------------------------------------------------

MU = 0.25
PRESS = 20e6           # Pa, pressing the two blocks together


def _shear_two_blocks(tau: float, mu: float = MU, p: float = PRESS):
    """Two blocks in the uniform state sigma = [[0, tau], [tau, -p]].

    The four outside faces carry exactly the tractions that state implies, so
    the load is self-equilibrated and the interface at y = 0 sees a normal
    pressure ``p`` and a shear ``tau`` — the textbook Coulomb experiment.
    Returns (solution, contact system, interface length).
    """
    from skfem import FacetBasis, LinearForm, asm

    mesh, part, w = _two_blocks(10, 0.02)
    C, rho = _iso(mesh)
    spec = {"magnet_rotor": ctc.ContactSpec("separation", mu),
            "sleeve_rotor": ctc.ContactSpec("separation", 0.0),
            "sleeve_magnet": ctc.ContactSpec("separation", 0.0),
            "shaft_rotor": ctc.ContactSpec("bonded", 0.0)}
    cs = ctc.build_contact_system(mesh, part, spec, order=2)
    basis, K, _fr, _fe = rs.assemble_plane_stress(cs.mesh, C, rho, None, order=2)
    _b, elem = rs.make_basis(cs.mesh, 2)

    tol = 1e-9
    faces = {
        "top": (lambda x: x[1] > w - tol, (tau, -p)),
        "bot": (lambda x: x[1] < -w + tol, (-tau, p)),
        "right": (lambda x: x[0] > w - tol, (0.0, tau)),
        "left": (lambda x: x[0] < tol, (0.0, -tau)),
    }
    f = np.zeros(K.shape[0])
    for _name, (where, (tx, ty)) in faces.items():
        ids = cs.mesh.facets_satisfying(where, boundaries_only=True)
        fb = FacetBasis(cs.mesh, elem, facets=ids, intorder=3)

        @LinearForm
        def _t(v, w_, tx=tx, ty=ty):
            return tx * v[0] + ty * v[1]

        f = f + asm(_t, fb)

    sol = ctc.solve_contact(K, f, cs, basis, max_iter=40)
    it = cs.iface("magnet_rotor")
    return sol, cs, float(it.length.sum())


def test_friction_sticks_below_the_cone():
    """tau = 0.5 mu p: nothing slides, and the joint passes the whole shear."""
    tau = 0.5 * MU * PRESS
    sol, cs, ln = _shear_two_blocks(tau)
    assert sol.converged
    rep = ctc.facet_report(cs, sol)["magnet_rotor"]
    ll = rep["length"]
    slip = float((rep["slip"] * ll).sum() / ll.sum())
    stick = float((rep["stick"] * ll).sum() / ll.sum())
    assert slip == pytest.approx(0.0, abs=1e-9), f"slip {slip}"
    assert stick == pytest.approx(1.0, abs=1e-9)
    # transmitted traction = sum of the pair tangential forces / length
    k = [i.label for i in cs.interfaces].index("magnet_rotor")
    m = cs.pair_iface == k
    trans = abs(float(sol.force_t[m].sum())) / ln
    assert trans == pytest.approx(tau, rel=0.05), f"{trans:.3e} vs {tau:.3e}"


def test_friction_slides_above_the_cone_and_caps_at_mu_p():
    """tau = 2 mu p: everything slides and the traction saturates at mu*p.

    The cap is the regularisation's whole promise — the secant stiffness is
    ``mu*Fn/|s|`` exactly so that ``k_t*s`` lands on the cone — so it is checked
    tightly, while the slip FRACTION is the state flag the panel reads.
    """
    tau = 2.0 * MU * PRESS
    sol, cs, ln = _shear_two_blocks(tau)
    assert sol.converged
    rep = ctc.facet_report(cs, sol)["magnet_rotor"]
    ll = rep["length"]
    slip = float((rep["slip"] * ll).sum() / ll.sum())
    assert slip > 0.99, f"slip {slip}"
    k = [i.label for i in cs.interfaces].index("magnet_rotor")
    m = cs.pair_iface == k
    trans = abs(float(sol.force_t[m].sum())) / ln
    assert trans == pytest.approx(MU * PRESS, rel=0.05), \
        f"{trans:.3e} vs mu*p {MU * PRESS:.3e}"
    # and it is BELOW what was asked of it — that is what "slipping" means
    assert trans < 0.75 * tau


def test_a_frictionless_separation_pair_counts_as_slipping():
    """mu = 0 leaves only the 1e-6 mechanism-removing spring, i.e. no shear
    path at all — reported as SLIP, never as "stuck", or the panel would show a
    load path that is not there."""
    tau = 0.1 * MU * PRESS
    sol, cs, _ln = _shear_two_blocks(tau, mu=0.0)
    rep = ctc.facet_report(cs, sol)["magnet_rotor"]
    ll = rep["length"]
    assert float((rep["stick"] * ll).sum() / ll.sum()) == pytest.approx(0.0,
                                                                       abs=1e-9)
    assert float((rep["slip"] * ll).sum() / ll.sum()) > 0.99


# ---------------------------------------------------------------------------
# (d) the route
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    return TestClient(app)


def test_route_rejects_an_unknown_load_selection(client):
    r = client.get("/api/mechanical/rotor_stress",
                   params={"rpm": 6000, "mesh_size_mm": 4.0, "order": 1,
                           "loads": "sideways"})
    assert r.status_code == 422, r.text[:300]
    bad = r.json()["detail"]["invalid_parameters"]
    assert bad and bad[0]["field"] == "loads"


@pytest.fixture
def no_last_run(monkeypatch):
    """A machine that has NEVER been run — constructed, not assumed.

    The route's default torque is the persisted last Simulation run, guarded by
    the geometry fingerprint.  That store is the USER's (the sandbox redirects
    the config, not the run pickle): the morning the live machine and its last
    run were both the G2 (2026-09-09), "the sandbox machine has never been run"
    stopped being true and both tests below found a 61 N·m torque to apply.
    Same family as every other sandbox leak — pin the premise.
    """
    from motor_ai_sim.routes import presets, simulation as sim

    monkeypatch.setattr(presets, "_last_transient_summary", lambda: None,
                        raising=True)
    monkeypatch.setattr(sim, "_last_transient_ref", {}, raising=True)


def test_route_refuses_loads_torque_with_no_torque_to_apply(client, no_last_run):
    """A machine that has never been run has no mean torque to default to.
    ``loads=torque`` then has nothing to solve and says so — it must not
    quietly solve the centrifugal case and call it the torque one."""
    r = client.get("/api/mechanical/rotor_stress",
                   params={"rpm": 6000, "mesh_size_mm": 4.0, "order": 1,
                           "loads": "torque"})
    assert r.status_code == 422, r.text[:300]
    bad = r.json()["detail"]["invalid_parameters"]
    assert bad and bad[0]["field"] == "torque_nm"


@pytest.fixture(scope="module")
def torque_result(client):
    r = client.get("/api/mechanical/rotor_stress",
                   params={"rpm": 6000, "mesh_size_mm": 3.0, "order": 1,
                           "cases": "single", "loads": "both",
                           "torque_nm": 40.0, "lift_off_solves": 0,
                           "field": False,
                           "contacts": '{"magnet_rotor": {"type": "separation",'
                                       ' "mu": 0.2}}'})
    assert r.status_code == 200, r.text[:600]
    return r.json()


def test_route_reports_the_loads_the_torque_and_the_reaction(torque_result):
    out = torque_result
    assert out["loads"] == "both"
    assert out["loads_requested"] == "both"
    assert out["torque_source"] == "given"
    assert out["torque_nm"] == pytest.approx(40.0)
    tl = out["torque_load"]
    assert tl["n_facets"] > 0 and tl["surface_mm"] > 0
    assert tl["traction_mpa"] > 0 and tl["r_gap_mm"] > 0
    assert 0.5 < tl["coverage"] < 1.5
    assert tl["reaction_at"].endswith("bore") and tl["reaction_rows"] > 0
    assert list(out["cases"]) == ["6,000 rpm"]
    c = out["cases"]["6,000 rpm"]
    # the identity the whole load path is checked by
    assert c["torque_reaction_nm"] == pytest.approx(40.0, rel=1e-2)
    assert c["torque_balance"] == pytest.approx(1.0, rel=1e-2)
    assert c["torque_path"] and c["torque_path"]["verdict"]


def test_route_reports_the_tangential_half_of_every_interface(torque_result):
    c = torque_result["cases"]["6,000 rpm"]
    for name, i in c["interfaces"].items():
        if not i:
            continue
        for k in ("slip_fraction", "stick_fraction", "slip_max_um",
                  "tangential_force_kn_per_m", "torque_transmitted_nm",
                  "friction_capacity_nm", "shear_mean_mpa"):
            assert k in i, f"{name}.{k}"
            assert math.isfinite(i[k]), f"{name}.{k} = {i[k]}"
        # open + slip + stick is the whole interface, by construction
        tot = i["open_fraction"] + i["slip_fraction"] + i["stick_fraction"]
        assert tot == pytest.approx(1.0, abs=1e-6), f"{name}: {tot}"
        # a frictionless pair can never be credited with a Coulomb capacity
        if not i["mu"]:
            assert i["friction_capacity_nm"] == 0.0, name


def test_centrifugal_only_reports_no_torque(client):
    r = client.get("/api/mechanical/rotor_stress",
                   params={"rpm": 6000, "mesh_size_mm": 3.0, "order": 1,
                           "cases": "single", "loads": "centrifugal",
                           "lift_off_solves": 0, "field": False})
    assert r.status_code == 200, r.text[:400]
    out = r.json()
    assert out["loads"] == "centrifugal"
    assert out["torque_nm"] == 0.0
    assert out["torque_load"] is None
    c = out["cases"][out["primary_case"]]
    assert c["torque_reaction_nm"] is None
    assert c["torque_path"] is None


def test_loads_both_downgrades_when_nothing_has_been_run(client, no_last_run):
    """``both`` is the API default, so a machine that has never been simulated
    must still get its centrifugal answer — and the response has to SAY that
    the torque half was dropped rather than showing an empty column."""
    r = client.get("/api/mechanical/rotor_stress",
                   params={"rpm": 6000, "mesh_size_mm": 4.0, "order": 1,
                           "cases": "single", "lift_off_solves": 0,
                           "field": False})
    assert r.status_code == 200, r.text[:400]
    out = r.json()
    assert out["loads_requested"] == "both"
    assert out["loads"] == "centrifugal"
    assert out["torque_source"] == "none"


def test_the_load_selection_is_part_of_the_cache_key():
    """Two answers about the same rotor under different loads are two answers."""
    from motor_ai_sim.routes.mechanical import _cache_key
    from motor_ai_sim.simulation.mechanical.contact import DEFAULT_CONTACTS

    args = (None, {"rotor_core": "M270-35A"}, 6000.0, 1.0, 0.0, 3.0, 1,
            dict(DEFAULT_CONTACTS), "single")
    assert _cache_key(*args, "both", 40.0) != _cache_key(*args, "centrifugal", 0.0)
    assert _cache_key(*args, "both", 40.0) != _cache_key(*args, "both", 41.0)
    # the default is still the pre-2026-09-07 key
    assert _cache_key(*args) == _cache_key(*args, "centrifugal", 0.0)


def test_last_round_trips_the_loads_and_the_torque(client, tmp_path,
                                                   monkeypatch):
    """Re-entering the tab restores the INPUTS too, or a Solve press would not
    reproduce the picture that is on screen."""
    from motor_ai_sim.routes import mechanical as mech

    monkeypatch.setattr(mech, "_LAST", {}, raising=True)
    monkeypatch.setattr(mech, "_LAST_LOADED", True, raising=True)
    monkeypatch.setattr(mech, "_last_store_path",
                        lambda: str(tmp_path / ".last_mechanical.pkl"))
    r = client.get("/api/mechanical/rotor_stress",
                   params={"rpm": 6000, "mesh_size_mm": 3.0, "order": 1,
                           "cases": "single", "loads": "both",
                           "torque_nm": 40.0, "lift_off_solves": 0,
                           "field": False,
                           "contacts": '{"magnet_rotor": {"type": "separation",'
                                       ' "mu": 0.2}}'})
    assert r.status_code == 200, r.text[:400]
    entry = client.get("/api/mechanical/last",
                       params={"field": False}).json()["rotor_stress"]
    assert entry is not None
    assert entry["params"]["loads"] == "both"
    assert entry["params"]["torque_nm"] == pytest.approx(40.0)
    res = entry["result"]
    assert res["torque_nm"] == pytest.approx(40.0)
    assert res["torque_load"]["n_facets"] > 0
    assert res["cases"][res["primary_case"]][
        "torque_reaction_nm"] == pytest.approx(40.0, rel=1e-2)


def test_a_part_with_no_load_path_is_bonded_or_refused_never_quoted(client):
    """With mu = 0 nothing holds the sandbox magnets tangentially, so the rotor
    solves as more than one body and the bore reaction stops matching the
    applied torque.

    Until 2026-09-09 that mismatch was RETURNED, with a "no verdict" torque
    path, as the cheapest way to see the path was broken.  Since the morning of
    2026-09-09 a bore reaction that does not close is a RUNAWAY: the route
    either bonds the joint that floated and says so (`contact_fallback`), or
    refuses by name (422).  The invariant that survives: no result with a
    broken torque balance is ever quoted, whichever machine is in the sandbox.
    """
    r = client.get("/api/mechanical/rotor_stress",
                   params={"rpm": 6000, "mesh_size_mm": 3.0, "order": 1,
                           "cases": "single", "loads": "both",
                           "torque_nm": 40.0, "lift_off_solves": 0,
                           "field": False})
    if r.status_code == 422:
        assert "held by nothing" in r.json()["detail"]["error"]
        return
    assert r.status_code == 200, r.text[:400]
    out = r.json()
    c = out["cases"][out["primary_case"]]
    assert c["torque_balance"] == pytest.approx(1.0, rel=1e-2)
    fb = out.get("contact_fallback")
    if fb:
        # a joint was bonded to close the path — the record names it and the
        # solved contact set agrees
        assert fb["from"] == "separation" and fb["to"] == "bonded"
        for pair in fb.get("pairs", [fb["pair"]]):
            assert out["contacts"][pair]["type"] == "bonded"
    tp = c["torque_path"]
    # a real yes/no when a separation joint with friction remains on the path;
    # "there is no separation joint" when the fallback bonded them all; the
    # honest "the contact did not settle" when the mu = 0 active set hit its
    # cap — reported with `contact.converged` false beside it; or, since
    # 2026-09-09 15:00, "held by the pocket walls" — mu = 0 on a form-locked
    # pocket carries the torque as normal pressure, the balance closes (asserted
    # above) and there is no friction margin to grade, so `held` is None and the
    # sentence says why (the solver grades it only when the contact settled or
    # its residual is within TORQUE_PATH_RESIDUAL_FRAC, and quotes the residual
    # in the sentence when `converged` is false).  Never "ran away": that
    # state is refused upstream now.
    assert (isinstance(tp["held"], bool)
            or "no separation joint" in tp["verdict"]
            or (not c["contact"]["converged"] and "did not settle" in tp["verdict"])
            or (tp["held"] is None and "form-locked" in tp["verdict"]))
    assert "ran away" not in tp["verdict"]


# ---------------------------------------------------------------------------
# (e) the machine the user is asking about
# ---------------------------------------------------------------------------
# READ-ONLY: the live config is read and a CadQueryMotor is built from a COPY of
# its geometry dict.  Nothing is written, no route is called, no cache is
# touched — the project's standing rule that verification never goes through a
# persisting path.
#
# The parameters are the user's, 2026-09-07: rotor_hole 1, magnet_up_gap 0,
# magnet_fill_down 0.95, sleeve 3 mm, 23 000 rpm, T = 180 N·m, mu = 0.2.  The
# air gap is raised to 3.6 mm because a 3 mm band does not fit the live 1.6 mm
# gap — see the assertion below, which is the finding, not a workaround.

LIVE_OVERRIDES = {"rotor_hole": 1, "magnet_up_gap": 0.0,
                  "magnet_fill_down": 0.95, "sleeve_thickness": 3.0,
                  "air_gap": 3.6}
LIVE_RPM, LIVE_TORQUE, LIVE_MU = 23000.0, 180.0, 0.2


def _live_polys(overrides):
    """Polygons + stack length from the FROZEN Ø200 snapshot, plus ``overrides``.

    Read ``config/motor_config.yaml`` until 2026-09-09: the morning the live
    machine was the 40 mm, these Ø200 overrides (a 3 mm band, a 3.6 mm gap,
    ``rotor_hole`` 1) produced a degenerate rotor and the fixture errored — the
    sandbox-leak family once more.  The snapshot in ``test_mechanical_contact``
    is the machine these numbers were written for; the live config stays
    untouched and unread.
    """
    from motor_ai_sim.cadquery_geometry import CadQueryMotor
    try:
        from test_mechanical_contact import FROZEN_GEO, FROZEN_MATERIALS
    except ImportError:  # a different import mode
        from tests.test_mechanical_contact import FROZEN_GEO, FROZEN_MATERIALS

    geo = dict(FROZEN_GEO)
    geo.update(overrides)
    m = CadQueryMotor()
    m.set_parameters(geo)
    return (m.get_2d_polygons(0.0), dict(FROZEN_MATERIALS),
            float(m.parameters.get("motor_length") or 0.0))


@pytest.fixture(scope="module")
def live_torque_result():
    polys, mats, stack = _live_polys(LIVE_OVERRIDES)
    if polys.get("sleeve") is None:
        pytest.skip("the live machine builds no sleeve at these parameters")
    contacts = {
        "magnet_rotor": ctc.ContactSpec("separation", LIVE_MU),
        "sleeve_rotor": ctc.ContactSpec("separation", LIVE_MU),
        "sleeve_magnet": ctc.ContactSpec("separation", LIVE_MU),
        "shaft_rotor": ctc.ContactSpec("bonded", 0.0),
    }
    return rs.solve_rotor_stress(
        polys, mats, LIVE_RPM, 1.0, 0.0, stack_length_mm=stack,
        mesh_size_mm=1.5, order=2, with_field=False, contacts=contacts,
        lift_off_solves=0, case_mode="single", loads="both",
        torque_nm=LIVE_TORQUE)


def test_live_machine_transmits_its_torque_to_the_bore(live_torque_result):
    """The number the user asked for: with mu = 0.2 and a 3 mm band, does the
    180 N·m get out of the poles?

    The reaction identity is the check that the answer is a solved one; the slip
    fractions and the bridge stress below are the answer itself.
    """
    out = live_torque_result
    case = out["cases"][out["primary_case"]]
    assert case["contact"]["converged"], "the contact never settled"
    assert not case["contact"]["unretained_parts"], \
        case["contact"]["unretained_parts"]
    assert case["torque_reaction_nm"] == pytest.approx(LIVE_TORQUE, rel=1e-2)


def test_live_machine_pole_and_magnet_slip_and_the_bridge_stress(
        live_torque_result, capsys):
    """Report the pole/sleeve and magnet/pole slip and the bridge von Mises.

    The assertions are deliberately loose — this is the live machine and it
    moves — but the PRINT is the deliverable: the three numbers the user is
    after, in one line, so they are in the test log next to the run that made
    them.
    """
    out = live_torque_result
    case = out["cases"][out["primary_case"]]
    ifs = case["interfaces"]
    pole_sleeve = ifs.get("sleeve_rotor")
    mag_pole = ifs.get("magnet_rotor")
    rotor = case["parts"]["rotor"]
    with capsys.disabled():
        print("\n--- live copy, 23 000 rpm, T = 180 N·m, mu = 0.2 ---")
        for lb in ("sleeve_rotor", "magnet_rotor", "sleeve_magnet"):
            i = ifs.get(lb)
            if not i:
                print(f"  {lb}: these parts do not touch")
                continue
            print(f"  {lb}: open {i['open_fraction'] * 100:5.1f} %  "
                  f"slip {i['slip_fraction'] * 100:5.1f} %  "
                  f"stuck {i['stick_fraction'] * 100:5.1f} %  "
                  f"max slip {i['slip_max_um']:8.1f} um  "
                  f"p_mean {i['pressure_mean_mpa']:7.1f} MPa  "
                  f"Mz {i['torque_transmitted_nm']:8.1f} N.m  "
                  f"mu*p capacity {i['friction_capacity_nm']:10.0f} N.m")
        print(f"  rotor iron (bridges): von Mises max "
              f"{rotor['von_mises_max_mpa']:.0f} MPa, p99.5 "
              f"{rotor['von_mises_p995_mpa']:.0f} MPa, yield "
              f"{rotor['strength_mpa']:.0f} MPa")
        print(f"  verdict: {case['torque_path']['verdict']}")
        print(f"  reaction {case['torque_reaction_nm']:.3f} N.m "
              f"(balance {case['torque_balance']:.6f})")
    assert pole_sleeve is not None and mag_pole is not None
    for i in (pole_sleeve, mag_pole):
        assert 0.0 <= i["slip_fraction"] <= 1.0
    # The bridges are the point: they are deliberately thin, so the model has to
    # report them ABOVE yield (that is the design intent — they yield and the
    # sleeve takes over), not quietly under it.
    assert rotor["von_mises_max_mpa"] > rotor["strength_mpa"]


def test_a_three_mm_band_does_not_fit_the_live_air_gap():
    """Why LIVE_OVERRIDES raises the air gap: at the live 1.6 mm gap the 3 mm
    band is built from the rotor OD INWARD, eating 1.4 mm of iron and magnet and
    reaching past the stator bore.  That is a geometry finding, and the test
    exists so nobody re-runs the case at the live gap and reads the numbers."""
    polys, _mats, _stack = _live_polys(
        {**LIVE_OVERRIDES, "air_gap": 1.6})
    r = polys.get("sleeve_r_mm")
    assert r is not None
    ri, ro = float(r[0]), float(r[1])
    assert ro - ri == pytest.approx(3.0, abs=1e-6)
    # the band's INNER radius is below the rotor OD it is supposed to sit on
    polys_ok, _m, _s = _live_polys(LIVE_OVERRIDES)
    ri_ok = float(polys_ok["sleeve_r_mm"][0])
    assert ri > ri_ok, "the 1.6 mm-gap band sits further out, i.e. in the gap"


# ---------------------------------------------------------------------------
# The verdict with no friction path (2026-09-09)
# ---------------------------------------------------------------------------
# User: "можно же считать с нулевым трением?" — on a spoke rotor the torque
# crosses the pocket walls as NORMAL pressure, so µ = 0 is a legitimate model
# and the old line "poles held: no — no separation joint is clamped (µ = 0…)"
# was wrong on it: the poles are held, there is just no friction margin to
# quote.  Every joint OPEN is the other case, and stays a "no".

def _sol_settled():
    from types import SimpleNamespace
    return SimpleNamespace(converged=True, free_parts=[], n_components=1,
                           residual=0.0)


def _iface(mu, open_fraction, slip=0.1):
    return {"type": "separation", "n_facets": 40, "mu": mu,
            "open_fraction": open_fraction, "friction_capacity_nm": 0.0,
            "slip_fraction": slip, "stick_fraction": 1.0 - slip,
            "slip_max_um": 0.0}


def test_frictionless_pocket_walls_are_held_not_graded():
    out = rs._torque_path({"magnet_rotor": _iface(0.0, 0.6),
                           "shaft_rotor": {"type": "bonded", "n_facets": 30}},
                          61.0, _sol_settled(), balance=1.0, r_out_m=0.073)
    assert out["held"] is None
    assert out["verdict"].startswith("poles held by the pocket walls")
    assert "µ = 0" in out["verdict"] and "form-locked" in out["verdict"]
    assert "no" not in out["verdict"].split("—")[0]
    assert out["worst_pair"] == "magnet_rotor"
    assert out["slip_fraction"] == pytest.approx(0.1)


def test_every_joint_open_is_still_a_no():
    out = rs._torque_path({"magnet_rotor": _iface(0.2, 1.0)},
                          61.0, _sol_settled(), balance=1.0, r_out_m=0.073)
    assert out["held"] is False
    assert out["verdict"].startswith("poles held: no")
    assert "open" in out["verdict"]
    # …and µ = 0 with nothing pressed is not "form-locked" either
    out0 = rs._torque_path({"magnet_rotor": _iface(0.0, 1.0)},
                           61.0, _sol_settled(), balance=1.0, r_out_m=0.073)
    assert out0["held"] is False
