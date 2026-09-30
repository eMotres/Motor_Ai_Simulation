"""Coulomb virtual-work torque (simulation/virtual_work_torque.py).

Small structured polar meshes, solved here with scikit-fem in well under a
second each.  What is checked:

1. a rigid rotation of an element contributes exactly zero;
2. EXACTNESS: on a family of meshes whose gap layer is actually deformed by
   the same field, the Coulomb torque equals the finite-difference derivative
   of the discrete co-energy (P1 and P2) — the formula is the exact
   Jacobian derivative, not an approximation of it;
3. ENERGY CONSERVATION: ∫T dθ along that family equals ΔW';
4. ANALYTIC: two sinusoidal current annuli in air, Dirichlet on both
   boundaries — the torque matches the closed-form mutual co-energy
   derivative and converges with the mesh;
5. LAYER INDEPENDENCE: two disjoint air layers agree, better on a finer mesh;
6. a layer that would deform a non-air element is refused.
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.integrate import quad

from skfem import (Basis, BilinearForm, ElementTriP0, ElementTriP1,
                   ElementTriP2, LinearForm, MeshTri, asm, condense, solve)
from skfem.helpers import dot, grad

from motor_ai_sim.simulation.virtual_work_torque import (
    MU0, NU0, CoulombLayerError, build_layer, coulomb_torque,
    deformed_elements, layer_self_check, prepare_coulomb_torque, radial_phi,
    rotation_displacement_gradient, sliding_band_layers,
)

L_STACK = 0.05          # m


# ── structured polar annulus ─────────────────────────────────────────────────
def polar_mesh(radii, n_theta):
    radii = np.asarray(radii, float)
    th = 2.0 * np.pi * np.arange(n_theta) / n_theta
    R, TH = np.meshgrid(radii, th, indexing="ij")          # (nr, nt)
    p = np.vstack((R.ravel() * np.cos(TH.ravel()), R.ravel() * np.sin(TH.ravel())))
    idx = np.arange(radii.size * n_theta).reshape(radii.size, n_theta)
    tris = []
    for i in range(radii.size - 1):
        for j in range(n_theta):
            a, b = idx[i, j], idx[i, (j + 1) % n_theta]
            c, d = idx[i + 1, j], idx[i + 1, (j + 1) % n_theta]
            if (i + j) % 2:
                tris += [(a, b, d), (a, d, c)]
            else:
                tris += [(a, b, c), (b, d, c)]
    return p, np.asarray(tris, int).T


def rotate(p, ang):
    c, s = np.cos(ang), np.sin(ang)
    return np.vstack((c * p[0] - s * p[1], s * p[0] + c * p[1]))


@BilinearForm
def _a(u, v, w):
    return w["nu"] * dot(grad(u), grad(v))


class Model:
    """Linear magnetostatics on an annulus, A = 0 on both boundaries.

    Element-attached materials and sources: ``nu_e`` per element, and a
    current density J(x; θ) given as a function of the CURRENT coordinates
    (rotor sources move with the rotor, so they are written in the rotor frame).
    """

    def __init__(self, p, t, nu_e, rotor_mask, stator_mask, J1, J2, pp, elem):
        self.p0, self.t = p, t
        self.nu_e, self.rotor_mask, self.stator_mask = nu_e, rotor_mask, stator_mask
        self.J1, self.J2, self.pp, self.elem = J1, J2, pp, elem

    def solve(self, p, theta):
        mesh = MeshTri(p, self.t)
        basis = Basis(mesh, self.elem)
        b0 = basis.with_element(ElementTriP0())
        K = asm(_a, basis, nu=b0.interpolate(self.nu_e))
        rm = b0.interpolate(self.rotor_mask.astype(float))
        sm = b0.interpolate(self.stator_mask.astype(float))
        J1, J2, pp = self.J1, self.J2, self.pp

        @LinearForm
        def _l(v, w):
            ph = np.arctan2(w.x[1], w.x[0])
            J = (rm * J1 * np.cos(pp * (ph - theta)) + sm * J2 * np.cos(pp * ph))
            return J * v

        f = asm(_l, basis)
        D = basis.get_dofs().all()                     # both circles: A = 0
        A = solve(*condense(K, f, D=D))
        wprime = 0.5 * L_STACK * float(f @ A)          # linear: W' = ½ ∫ J A
        return mesh, A, wprime


def _machine(n_theta, n_per_zone, pp=2, salient=True, elem=ElementTriP2()):
    """Rotor currents + (optionally salient) rotor iron | air gap | slotted
    stator iron + stator currents.  Returns (model, radii, zone radii)."""
    z = [0.010, 0.014, 0.018, 0.020, 0.024, 0.028, 0.032]
    # zones: rotor iron | rotor currents | gap | stator currents | stator iron | outer
    radii = np.unique(np.concatenate(
        [np.linspace(z[i], z[i + 1], n_per_zone[i] + 1) for i in range(len(z) - 1)]))
    p, t = polar_mesh(radii, n_theta)
    c = p[:, t].mean(axis=1)
    rc = np.hypot(c[0], c[1]); ang = np.arctan2(c[1], c[0])
    nu = np.full(t.shape[1], NU0)
    rotor_iron = (rc < z[1])
    if salient:
        rotor_iron &= (np.cos(2 * pp * ang) > 0)
    nu[rotor_iron] = NU0 / 200.0
    stator_iron = (rc > z[4]) & (rc < z[5])
    teeth = (rc > z[3]) & (rc < z[4]) & (np.cos(12 * ang) > 0.3)
    nu[stator_iron | teeth] = NU0 / 500.0
    rotor_src = (rc > z[1]) & (rc < z[2])
    stator_src = (rc > z[3]) & (rc < z[4]) & ~teeth
    model = Model(p, t, nu, rotor_src, stator_src, 4e6, 3e6, pp, elem)
    return model, radii, z


def _layer_phi(p, r_one, r_zero):
    eps = 1e-9 * r_zero                   # node radii carry round-off
    return radial_phi(np.hypot(p[0], p[1]), r_one + eps, r_zero - eps)


# ── 1. rigid rotation contributes nothing ────────────────────────────────────
def test_rigid_rotation_has_zero_symmetric_part():
    p = np.array([[0.02, 0.021, 0.0205], [0.001, 0.0012, 0.004]])
    t = np.array([[0], [1], [2]])
    D = rotation_displacement_gradient(p, t, np.ones(3))[0]
    assert D == pytest.approx(np.array([[0.0, -1.0], [1.0, 0.0]]), abs=1e-12)
    layer = build_layer(p, t, np.array([1.0, 0.0, 0.5]), np.array([True]))
    assert layer.elements.tolist() == [0]
    assert deformed_elements(t, np.ones(3)).size == 0


# ── 2 + 3. exact discrete derivative, energy conservation ────────────────────
@pytest.mark.parametrize("elem", [ElementTriP1(), ElementTriP2()], ids=["P1", "P2"])
def test_coulomb_is_exact_derivative_of_discrete_coenergy(elem):
    model, radii, z = _machine(48, [2, 2, 3, 2, 2, 1], elem=elem)
    phi = _layer_phi(model.p0, z[2], z[3])            # the whole gap
    air = np.isclose(model.nu_e, NU0) & ~model.rotor_mask & ~model.stator_mask

    def config(theta):
        # every node rotated by φ·θ: rotor rigid, gap sheared, stator fixed
        c, s = np.cos(phi * theta), np.sin(phi * theta)
        p0 = model.p0
        return np.vstack((c * p0[0] - s * p0[1], s * p0[0] + c * p0[1]))

    def torque_and_energy(theta):
        p = config(theta)
        mesh, A, w = model.solve(p, theta)
        layer = build_layer(p, model.t, phi, air)
        return coulomb_torque(mesh, elem, A, layer, L_STACK), w

    theta0, h = 0.03, 2e-5
    T0, _ = torque_and_energy(theta0)
    _, wp = torque_and_energy(theta0 + h)
    _, wm = torque_and_energy(theta0 - h)
    fd = (wp - wm) / (2 * h)
    assert abs(T0) > 1e-4
    assert T0 == pytest.approx(fd, rel=2e-6)

    # ∫ T dθ = ΔW' (Simpson on the same mesh family)
    ths = np.linspace(0.0, 0.08, 17)
    TW = [torque_and_energy(th) for th in ths]
    T = np.array([a for a, _ in TW]); W = np.array([b for _, b in TW])
    hstep = ths[1] - ths[0]
    integral = hstep / 3 * (T[0] + T[-1] + 4 * T[1:-1:2].sum() + 2 * T[2:-1:2].sum())
    assert integral == pytest.approx(W[-1] - W[0], rel=1e-5)


# ── 4. analytic: two current annuli in air ───────────────────────────────────
def _analytic_torque(pp, theta, a1, a2, b1, b2, R_in, R_out, J1, J2):
    def f(r, R):
        return r ** pp - R ** (2 * pp) * r ** (-pp)

    def df(r, R):
        return pp * r ** (pp - 1) + pp * R ** (2 * pp) * r ** (-pp - 1)

    def wr(rho):
        return df(rho, R_in) * f(rho, R_out) - f(rho, R_in) * df(rho, R_out)

    Cs = quad(lambda rho: f(rho, R_out) / wr(rho), b1, b2, epsabs=0, epsrel=1e-13)[0]
    Ir = quad(lambda r: r * f(r, R_in), a1, a2, epsabs=0, epsrel=1e-13)[0]
    return -pp * L_STACK * math.pi * J1 * J2 * MU0 * math.sin(pp * theta) * Cs * Ir


def _air_machine(n_theta, n_per_zone, elem):
    z = [0.010, 0.014, 0.018, 0.020, 0.024, 0.028, 0.032]
    radii = np.unique(np.concatenate(
        [np.linspace(z[i], z[i + 1], n_per_zone[i] + 1) for i in range(len(z) - 1)]))
    p, t = polar_mesh(radii, n_theta)
    c = p[:, t].mean(axis=1)
    rc = np.hypot(c[0], c[1])
    nu = np.full(t.shape[1], NU0)
    rotor_src = (rc > z[1]) & (rc < z[2])
    stator_src = (rc > z[3]) & (rc < z[4])
    return Model(p, t, nu, rotor_src, stator_src, 4e6, 3e6, 2, elem), z


@pytest.mark.parametrize("elem", [ElementTriP1(), ElementTriP2()], ids=["P1", "P2"])
def test_analytic_two_current_annuli(elem):
    theta = math.pi / 8                     # pθ = π/4
    ref = _analytic_torque(2, theta, 0.014, 0.018, 0.020, 0.024, 0.010, 0.032, 4e6, 3e6)
    errs = []
    for k in (1, 2):
        model, z = _air_machine(48 * k, [2 * k, 2 * k, 2 * k, 2 * k, 2 * k, 2 * k], elem)
        mesh, A, _ = model.solve(model.p0, theta)
        air = ~model.rotor_mask & ~model.stator_mask
        layer = build_layer(model.p0, model.t, _layer_phi(model.p0, z[2], z[3]), air)
        errs.append(abs(coulomb_torque(mesh, elem, A, layer, L_STACK) / ref - 1.0))
    print("analytic rel err", type(elem).__name__, errs)
    tol = 2e-2 if isinstance(elem, ElementTriP1) else 2e-3
    assert errs[1] < tol, errs
    assert errs[1] < errs[0], errs          # converges with the mesh


# ── 5. layer independence ─────────────────────────────────────────────────────
def test_two_layers_agree_and_converge():
    diffs = []
    for k in (1, 2):
        model, radii, z = _machine(48 * k, [2 * k, 2 * k, 4 * k, 2 * k, 2 * k, k])
        theta = 0.1
        mesh, A, _ = model.solve(rotate(model.p0, 0.0), theta)
        air = np.isclose(model.nu_e, NU0) & ~model.rotor_mask & ~model.stator_mask
        mid = 0.5 * (z[2] + z[3])
        lay_in = build_layer(model.p0, model.t, _layer_phi(model.p0, z[2], mid), air)
        lay_out = build_layer(model.p0, model.t, _layer_phi(model.p0, mid, z[3]), air)
        assert np.intersect1d(lay_in.elements, lay_out.elements).size == 0
        Ti = coulomb_torque(mesh, ElementTriP2(), A, lay_in, L_STACK)
        To = coulomb_torque(mesh, ElementTriP2(), A, lay_out, L_STACK)
        diffs.append(abs(Ti - To) / abs(0.5 * (Ti + To)))
        chk = layer_self_check([Ti], [To])
        assert chk["max_abs_diff_Nm"] == pytest.approx(abs(Ti - To))
    print("layer diffs", diffs)
    assert diffs[1] < 5e-3, diffs
    assert diffs[1] < diffs[0], diffs


# ── 6. validation ─────────────────────────────────────────────────────────────
def test_layer_touching_iron_is_refused():
    model, radii, z = _machine(24, [1, 1, 2, 1, 1, 1])
    air = np.isclose(model.nu_e, NU0) & ~model.rotor_mask & ~model.stator_mask
    with pytest.raises(CoulombLayerError):
        build_layer(model.p0, model.t, _layer_phi(model.p0, z[2], z[5]), air)
    with pytest.raises(CoulombLayerError):
        build_layer(model.p0, model.t, np.zeros(model.p0.shape[1]), air)


def test_sliding_band_layers_on_two_halves():
    """Two halves with a duplicated slip ring; the layers are disjoint, pure
    air and pinned exactly on the ring copies."""
    nt = 36
    pr, tr = polar_mesh(np.array([0.010, 0.014, 0.016, 0.017]), nt)   # rotor half
    ps, ts = polar_mesh(np.array([0.017, 0.018, 0.020, 0.024]), nt)   # stator half
    ns = ps.shape[1]
    p = np.hstack((ps, rotate(pr, 0.3)))                  # rotor half in its own frame
    t = np.hstack((ts, tr + ns))
    rc = np.hypot(*p[:, t].mean(axis=1))
    air = (rc > 0.014) & (rc < 0.020)
    rr = np.hypot(p[0], p[1])
    ring_r = np.where((np.arange(p.shape[1]) >= ns) & np.isclose(rr, 0.017))[0]
    ring_s = np.where((np.arange(p.shape[1]) < ns) & np.isclose(rr, 0.017))[0]
    lay = sliding_band_layers(p, t, ns, air, 0.014, 0.017, 0.020,
                              slip_nodes_rotor=ring_r, slip_nodes_stator=ring_s)
    r_el, s_el = lay["rotor_side"].elements, lay["stator_side"].elements
    assert np.all(r_el >= ts.shape[1]) and np.all(s_el < ts.shape[1])
    assert np.all(lay["rotor_side"].phi[ring_r] == 0.0)
    assert np.all(lay["stator_side"].phi[ring_s] == 1.0)
    assert np.all(lay["stator_side"].phi[ns:] == 1.0)
    with pytest.raises(CoulombLayerError):
        sliding_band_layers(p, t, ns, air, 0.017, 0.017, 0.020)


def test_prepared_evaluator_scales_with_sector_count():
    model, z = _air_machine(24, [1, 1, 2, 1, 1, 1], ElementTriP2())
    mesh, A, _ = model.solve(model.p0, 0.2)
    air = ~model.rotor_mask & ~model.stator_mask
    layer = build_layer(model.p0, model.t, _layer_phi(model.p0, z[2], z[3]), air)
    t1 = prepare_coulomb_torque(mesh, ElementTriP2(), layer, L_STACK)(A)
    t4 = prepare_coulomb_torque(mesh, ElementTriP2(), layer, L_STACK, sector_count=4)(A)
    assert t4 == pytest.approx(4.0 * t1, rel=1e-14)


# ── 7. shared per-frame post-processing ──────────────────────────────────────
def _two_halves():
    nt = 36
    pr, tr = polar_mesh(np.array([0.010, 0.014, 0.016, 0.017]), nt)
    ps, ts = polar_mesh(np.array([0.017, 0.018, 0.020, 0.024]), nt)
    ns = ps.shape[1]
    p = np.hstack((ps, rotate(pr, 0.3)))
    t = np.hstack((ts, tr + ns))
    rc = np.hypot(*p[:, t].mean(axis=1))
    tags = np.where((rc > 0.014) & (rc < 0.020), 3, 1)          # DOM_AIRGAP / iron
    nu = np.where(tags == 3, NU0, 0.0)
    return p, t, ns, tags, nu


def test_frame_torques_and_series_summary():
    from motor_ai_sim.simulation.virtual_work_torque import (
        air_mask_from, coulomb_series_summary, frame_torques,
        prepare_sliding_band_frame_torques)
    p, t, ns, tags, nu = _two_halves()
    mesh = MeshTri(p, t)
    air = air_mask_from(tags, nu)
    assert air.sum() == int((tags == 3).sum())
    ev = prepare_sliding_band_frame_torques(
        mesh, ElementTriP2(), stack_length_m=L_STACK, sector_count=2,
        maxwell_sector=lambda A: 0.25, n_stator_nodes=ns, air_mask=air,
        r_rotor_metal=0.014, r_slip=0.017, r_stator_metal=0.020)
    assert ev.unavailable_reason is None
    A = np.random.default_rng(1).normal(size=Basis(mesh, ElementTriP2()).N) * 1e-3
    out = frame_torques(ev, A)
    assert out["maxwell_Nm"] == 0.5
    assert out["coulomb_Nm"] == pytest.approx(
        0.5 * (out["coulomb_rotor_side_Nm"] + out["coulomb_stator_side_Nm"]), rel=1e-15)
    assert out["coulomb_layer_diff_Nm"] == pytest.approx(
        out["coulomb_rotor_side_Nm"] - out["coulomb_stator_side_Nm"], rel=1e-15)
    s = coulomb_series_summary([out["coulomb_Nm"]] * 3,
                               [(out["coulomb_rotor_side_Nm"], out["coulomb_stator_side_Nm"])] * 3)
    assert s["available"] and s["T_ripple_pp_coulomb"] == 0.0
    assert s["T_avg_coulomb_Nm"] == pytest.approx(out["coulomb_Nm"], rel=1e-15)
    # a layer that would cut iron: Coulomb unavailable, Maxwell unaffected
    ev_bad = prepare_sliding_band_frame_torques(
        mesh, ElementTriP2(), stack_length_m=L_STACK, sector_count=2,
        maxwell_sector=lambda A: 0.25, n_stator_nodes=ns, air_mask=air,
        r_rotor_metal=0.012, r_slip=0.017, r_stator_metal=0.020)
    assert ev_bad.unavailable_reason and "non-air" in ev_bad.unavailable_reason
    out_bad = frame_torques(ev_bad, A)
    assert out_bad["maxwell_Nm"] == 0.5 and out_bad["coulomb_Nm"] is None
    s_bad = coulomb_series_summary([None], [(None, None)], ev_bad.unavailable_reason)
    assert not s_bad["available"] and s_bad["unavailable_reason"] == ev_bad.unavailable_reason


def test_torque_method_diagnostics_reports_coulomb_differences():
    from motor_ai_sim.simulation.sb_postproc import torque_method_diagnostics
    n = 12
    th = np.arange(n) * 2 * np.pi / n
    ia, ib, ic = (10 * np.cos(th - k * 2 * np.pi / 3) for k in range(3))
    pa, pb, pc = (0.01 * np.sin(th - k * 2 * np.pi / 3) for k in range(3))
    tm = np.full(n, 0.5)
    d = torque_method_diagnostics(pa, pb, pc, ia, ib, ic, tm, 1, t_coulomb=[0.4] * n)
    assert d["coulomb_mean_Nm"] == pytest.approx(0.4)
    assert d["raw_maxwell_minus_coulomb_mean_Nm"] == pytest.approx(0.1)
    assert d["space_vector_minus_coulomb_mean_Nm"] == pytest.approx(
        d["space_vector_mean_candidate_Nm"] - 0.4)
    assert d["terminal_work_minus_coulomb_mean_Nm"] is None      # ineligible here
    d2 = torque_method_diagnostics(pa, pb, pc, ia, ib, ic, tm, 1, t_coulomb=[None] * n)
    assert d2["coulomb_mean_Nm"] is None


def test_self_check_flags_a_mesh_limited_ripple():
    ok = layer_self_check([1.0, 1.1, 1.0], [1.001, 1.101, 1.0])
    assert ok["ripple_mesh_limited"] is False
    bad = layer_self_check([1.0, 1.1, 1.0], [1.03, 1.08, 1.0])
    assert bad["ripple_mesh_limited"] is True
    assert layer_self_check([], [])["ripple_mesh_limited"] is None


# ── 8. Coulomb default, measured gap rule, coupled default (2026-09-30) ──────
def test_torque_method_defaults_to_coulomb():
    from motor_ai_sim.simulation.virtual_work_torque import (
        DEFAULT_TORQUE_METHOD, resolve_torque_method)
    assert DEFAULT_TORQUE_METHOD == "coulomb"
    assert resolve_torque_method(None, {}) == "coulomb"
    assert resolve_torque_method(None, None) == "coulomb"
    assert resolve_torque_method(None, {"torque_method": "hybrid_maxwell_ac"}) \
        == "hybrid_maxwell_ac"
    assert resolve_torque_method("hybrid_maxwell_ac", {"torque_method": "coulomb"}) \
        == "hybrid_maxwell_ac"
    with pytest.raises(ValueError):
        resolve_torque_method("maxwell", {})


def test_solver_entry_points_default_to_coulomb():
    import inspect
    from motor_ai_sim.simulation import fem_solver_2d as fs
    for fn in (fs.fem_transient_sliding_band, fs.em_transient_eval):
        assert inspect.signature(fn).parameters["torque_method"].default is None
    src = inspect.getsource(fs.fem_transient_sliding_band)
    assert "_resolve_torque_method(torque_method, sim)" in src


def test_gap_floor_helper(monkeypatch):
    from motor_ai_sim.simulation import sb_domains as sbd
    monkeypatch.setattr(sbd, "GAP_LAYERS_MIN", 1.0)
    assert sbd.effective_gap_layers(1) == 1.0          # no flat floor by default
    assert sbd.effective_gap_layers(None) == 1.0
    monkeypatch.setattr(sbd, "GAP_LAYERS_MIN", 3.0)    # the SB_GAP_LAYERS_MIN fallback
    assert sbd.effective_gap_layers(1) == 3.0
    assert sbd.effective_gap_layers(2.0) == 3.0
    assert sbd.effective_gap_layers(4.0) == 4.0
    assert sbd.effective_gap_layers(1, "internal_probe") == 1.0
    assert sbd.effective_gap_layers(float("nan")) == 3.0


def test_next_gap_layers_steps_one_per_side_to_the_cap():
    from motor_ai_sim.simulation.virtual_work_torque import next_gap_layers as n
    assert [n(g, 0.194) for g in (1.0, 2.0, 3.0, 4.0)] == [2.0, 3.0, 4.0, None]
    assert n(1.0, 0.021) is None       # Ø40 shipped duty, 2.1 %: passes
    assert n(1.0, None) is None


def test_self_check_uses_the_ripple_scale_floor():
    # a flat waveform: p-p 1e-4 N·m on a 10 N·m mean -> scale = 0.5 % of mean
    chk = layer_self_check([10.0, 10.0001, 10.0], [10.0005, 10.0006, 10.0005])
    assert chk["rel_to_ripple_scale"] == pytest.approx(0.0005 / 0.05, rel=1e-3)
    assert chk["ripple_mesh_limited"] is False
    assert chk["rel_to_pp"] > 1.0


def _fake_result(eps, gl, ring=144):
    return {"T_avg_Nm": 1.0, "T_ripple_pp_Nm": 0.1, "solve_wall_s": 1.0,
            "gap_layers_effective": gl, "gap_layers_requested": gl,
            "slip_nodes_per_period": ring,
            "coulomb_torque": {"layer_self_check": {"rel_to_ripple_scale": eps}}}


def test_gap_refinement_steps_up_until_the_rings_agree(monkeypatch):
    from motor_ai_sim.simulation import fem_solver_2d as fs
    calls = []
    # Ø40-like: 1/side 19.4 %, 2/side 7.4 %, 3/side 3.0 % -> passes at 3
    eps_at = {1.0: 0.194, 2.0: 0.074, 3.0: 0.030, 4.0: 0.005}

    def fake(**kw):
        calls.append(kw)
        return _fake_result(eps_at[kw["gap_layers"]], kw["gap_layers"])
    monkeypatch.setattr(fs, "fem_transient_sliding_band", fake)
    monkeypatch.delenv("SB_GAP_REFINE", raising=False)
    out = fs._solve_with_gap_refinement({"gap_layers": 1.0, "sampling_purpose": "standard"})
    assert [c["gap_layers"] for c in calls] == [1.0, 2.0, 3.0]
    assert all(c.get("slip_per_period") == 144 for c in calls[1:])
    gr = out["gap_refinement"]
    assert gr["applied"] and gr["passed"] and gr["persist_gap_layers"] == 3.0
    assert [a["gap_layers_per_side"] for a in gr["attempts"]] == [1.0, 2.0, 3.0]
    assert gr["first"]["gap_layers_per_side"] == 1.0
    assert out["gap_layers_note"] == "gap layers 1→3 after ring mismatch"
    # never passes: stops at the cap of 4, persists nothing, stays flagged
    calls.clear()
    monkeypatch.setattr(fs, "fem_transient_sliding_band",
                        lambda **kw: calls.append(kw) or _fake_result(0.2, kw["gap_layers"]))
    out = fs._solve_with_gap_refinement({"gap_layers": 1.0, "sampling_purpose": "standard"})
    assert [c["gap_layers"] for c in calls] == [1.0, 2.0, 3.0, 4.0]
    assert out["gap_refinement"]["persist_gap_layers"] is None
    assert "cap" in out["gap_layers_note"]
    # passing run: one solve, recorded as not applied
    calls.clear()
    monkeypatch.setattr(fs, "fem_transient_sliding_band",
                        lambda **kw: calls.append(kw) or _fake_result(0.02, 1.0))
    out = fs._solve_with_gap_refinement({"gap_layers": 1.0, "sampling_purpose": "standard"})
    assert len(calls) == 1 and out["gap_refinement"]["applied"] is False
    assert out["gap_refinement"]["persist_gap_layers"] is None
    # an UNSETTLED eddy run is refined too (TDM showed the L155 2/side self-check
    # is a mesh property), and the note says the first warm-up had not settled
    calls.clear()

    def fake_unsettled(**kw):
        calls.append(kw)
        return dict(_fake_result(0.18 if len(calls) == 1 else 0.03, kw["gap_layers"]),
                    eddy_settled=(len(calls) != 1))
    monkeypatch.setattr(fs, "fem_transient_sliding_band", fake_unsettled)
    out = fs._solve_with_gap_refinement({"gap_layers": 2.0, "sampling_purpose": "standard"})
    assert [c["gap_layers"] for c in calls] == [2.0, 3.0]
    assert out["gap_refinement"]["eddy_unsettled"] is True
    assert "had not settled" in out["gap_layers_note"]
    # optimization candidates, internal probes and the switch are never refined
    calls.clear()
    monkeypatch.setattr(fs, "fem_transient_sliding_band",
                        lambda **kw: calls.append(kw) or _fake_result(0.5, 1.0))
    out = fs._solve_with_gap_refinement({"gap_layers": 1.0, "sampling_purpose": "optimization"})
    assert "optimization candidate" in out["gap_refinement"]["skipped_reason"]
    assert out["gap_refinement"]["persist_gap_layers"] is None
    fs._solve_with_gap_refinement({"gap_layers": 1.0, "sampling_purpose": "internal_probe"})
    monkeypatch.setenv("SB_GAP_REFINE", "0")
    fs._solve_with_gap_refinement({"gap_layers": 1.0, "sampling_purpose": "standard"})
    monkeypatch.delenv("SB_GAP_REFINE")
    assert len(calls) == 3
    # the winner's final re-solve ("cogging_quality") does use the rule
    calls.clear()
    monkeypatch.setattr(fs, "fem_transient_sliding_band", fake)
    out = fs._solve_with_gap_refinement({"gap_layers": 1.0,
                                         "sampling_purpose": "cogging_quality"})
    assert out["gap_refinement"]["persist_gap_layers"] == 3.0


def test_passing_level_persists_only_for_the_live_machine(monkeypatch):
    from motor_ai_sim.routes import simulation as sim
    import motor_ai_sim.api as api
    written = []
    monkeypatch.setattr(api, "update_mesh_config", lambda patch: written.append(patch.gap_layers))

    def sb(level=2.0):
        return {"gap_layers_note": "gap layers 1→2 after ring mismatch",
                "gap_refinement": {"persist_gap_layers": level}}
    r = sb()
    sim._persist_gap_layers_default(r, geo_ov=None, sampling_purpose="standard")
    assert written == [2.0] and r["gap_refinement"]["persisted_to"]
    r = sb()                                   # champion re-check / candidate: override
    sim._persist_gap_layers_default(r, geo_ov={"air_gap": 0.3},
                                    sampling_purpose="cogging_quality")
    assert written == [2.0] and "override" in r["gap_refinement"]["persist_skipped"]
    r = sb()                                   # an optimization candidate
    sim._persist_gap_layers_default(r, geo_ov=None, sampling_purpose="optimization")
    assert written == [2.0] and "optimization" in r["gap_refinement"]["persist_skipped"]
    r = {"gap_refinement": {"persist_gap_layers": None}}
    sim._persist_gap_layers_default(r, geo_ov=None, sampling_purpose="standard")
    assert written == [2.0]


def test_coupled_probe_uses_the_runs_mesh_resolution():
    import inspect
    from pathlib import Path
    from motor_ai_sim.routes import simulation as sim
    assert inspect.signature(sim.get_fem_transient).parameters["gap_layers"].default is None
    src = (Path(__file__).resolve().parents[1] / "src" / "motor_ai_sim" / "routes"
           / "coupled.py").read_text(encoding="utf-8")
    assert '_f(body, "gap_layers"' not in src and '_f(body, "mesh_size_mm"' not in src
    assert '_resolve_gap_layers(body.get("gap_layers"))' in src
    assert "torque_method" in inspect.signature(sim.get_fem_transient).parameters

def test_mesh_settings_come_only_from_the_mesh_tab():
    """Owner 2026-09-30: every run path takes the Mesh settings from the request
    (Mesh tab) or the machine's saved Mesh settings; 1/side is only a labelled
    last-resort fallback."""
    import inspect
    import re
    from pathlib import Path
    from motor_ai_sim.routes import simulation as sim
    from motor_ai_sim.optimization import refine_proc
    from motor_ai_sim.routes import optimization as opt
    for fn in (sim.get_fem_transient, sim.get_fem_field2d,
               sim.build_fem_mesh_2d_sliding_band):
        for k in ("mesh_size_mm", "min_size_mm", "outer_air_factor", "gap_layers",
                  "n_sectors"):
            assert inspect.signature(fn).parameters[k].default is None, (fn.__name__, k)
    assert inspect.signature(refine_proc.run_one).parameters["gap_layers"].default is None
    for model in ("ScanRequest", "DescentRequest", "CmaesRequest"):
        m = getattr(opt, model, None)
        if m is not None and "gap_layers" in m.model_fields:
            assert m.model_fields["gap_layers"].default is None, model
    root = Path(__file__).resolve().parents[1]
    for rel in ("routes/optimization.py", "routes/coupled.py", "routes/thermal.py",
                "sweep_resume.py", "optimization/refine_proc.py"):
        src = (root / "src" / "motor_ai_sim" / rel).read_text(encoding="utf-8")
        assert not re.search(r"gap_layers(: float)? ?= ?[123]\.0|\"gap_layers\", [123]\.0", src), rel
    web = root / "web" / "src"
    for rel in ("lib/emRunPayload.ts", "components/sweep/SweepStudyPanel.tsx",
                "stores/motorStore.ts", "components/simulation/SimulationPanel.tsx",
                "components/simulation/FemFieldChart.tsx",
                "components/simulation/FemAnimationViewer.tsx"):
        src = (web / rel).read_text(encoding="utf-8")
        assert not re.search(r"gapLayers'\s*,\s*[123]\)|mesh\.gapLayers'\)\s*\?\?", src), rel


def test_mesh_settings_resolution_order():
    from motor_ai_sim.mesh_settings import (
        SOURCE_FALLBACK, SOURCE_MACHINE, SOURCE_REQUEST, fallback_note,
        resolve_gap_layers, resolve_mesh_settings)
    cfg = {"mesh": {"gap_layers": 3, "mesh_size_mm": 1.22, "n_sectors": 4}}
    vals, src = resolve_mesh_settings({"gap_layers": None, "mesh_size_mm": 2.0}, cfg)
    assert vals["gap_layers"] == 3.0 and src["gap_layers"] == SOURCE_MACHINE
    assert vals["mesh_size_mm"] == 2.0 and src["mesh_size_mm"] == SOURCE_REQUEST
    assert vals["min_size_mm"] == 0.3 and src["min_size_mm"] == SOURCE_FALLBACK
    assert isinstance(vals["n_sectors"], int) and vals["n_sectors"] == 4
    assert "min_size_mm" in fallback_note(src, vals) and "outer_air_factor" in fallback_note(src, vals)
    assert resolve_gap_layers(None, {"mesh": {}}) == (1.0, SOURCE_FALLBACK)
    assert resolve_gap_layers(2, {"mesh": {"gap_layers": 3}}) == (2.0, SOURCE_REQUEST)
    assert resolve_gap_layers(float("nan"), {"mesh": {"gap_layers": 3}}) == (3.0, SOURCE_MACHINE)
    full = {"mesh": {"gap_layers": 2, "mesh_size_mm": 1, "min_size_mm": 0.2,
                     "outer_air_factor": 1.2, "n_sectors": 2}}
    assert fallback_note(resolve_mesh_settings({}, full)[1]) is None

def test_optimizer_candidate_metrics_carry_the_self_check():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "src" / "motor_ai_sim"
    rp = (root / "optimization" / "refine_proc.py").read_text(encoding="utf-8")
    for k in ('"ripple_self_check_rel"', '"ripple_mesh_limited"', '"gap_layers_per_side"',
              '"gap_layers_persist"', '"gap_layers_note"'):
        assert k in rp
    from motor_ai_sim.routes.optimization import _RES_KEYS
    assert {"ripple_self_check_rel", "ripple_mesh_limited"} <= set(_RES_KEYS)


def test_passport_meshes_as_the_machine_mesh_settings_say():
    import inspect
    from pathlib import Path
    from motor_ai_sim import passport
    assert inspect.signature(passport.generate_passport).parameters["mesh_size_mm"].default is None
    src = Path(passport.__file__).read_text(encoding="utf-8")
    assert "mesh_size_mm + 1.0" not in src and "**_MESH" in src
    assert '"mesh_settings_note": _mesh_note' in src
    cat = (Path(passport.__file__).parent / "routes" / "catalog.py").read_text(encoding="utf-8")
    assert '"mesh": dict(_p.get("mesh") or {})' in cat
