"""The MAGNETIC PULL of the air-gap field — a real load of the rotor mechanics.

Added 2026-10-26 for the owner's decision: the pull had been faked by a rotor
iron density x5.31 ("20SW1200_pull_k5.31", p ~ 0.70 MPa).  It is now a surface
traction on the rotor's air-gap facets, from the duty's stored EM field or from
an explicit uniform estimate (``simulation.mechanical.magnetic_pull``).

WHAT IS CLAIMED, in the order of trust:

  (a) the Maxwell traction itself: t_n = (B_n^2 - B_t^2)/2mu0, t_t = B_n B_t/mu0;
  (b) a synthetic field: a uniform radial B (B_r ∝ 1/r, curl- and div-free)
      gives p = B^2/2mu0 on the rotor and ZERO net force on a full ring, and
      the annulus check of the same field agrees; an old record that carries
      only A_z gives the same pull as one that carries B, through the EM
      model's own half-sector periodicity;
  (c) Lamé: a thick ring pulled uniformly on its outer (inner rotor) or inner
      (outer rotor) face against the closed form;
  (d) full vs sector: the pull load vector of a periodic ring is the same
      dof for dof, the worst-pole envelope picks the same pole, and on the
      4-pole spoke rotor the solved answers agree;
  (e) pull = none changes nothing: pinned numbers of the code before the pull;
  (f) the k-density body force against the surface traction on the spoke
      rotor, with the same total radial load: where they differ, and why;
  (g) the route: parameters, validation, the cache key, the stored record.

Direct structured polar meshes wherever the claim allows; gmsh only for the
spoke rotor that the existing symmetry suite already meshes.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from motor_ai_sim.simulation.mechanical import contact as ctc
from motor_ai_sim.simulation.mechanical import magnetic_pull as mpl
from motor_ai_sim.simulation.mechanical import rotor_stress as rs

MU0 = mpl.MU0
E_ST, NU_ST = 200e9, 0.30


# ---------------------------------------------------------------------------
# helpers — structured polar meshes, no gmsh
# ---------------------------------------------------------------------------

def _polar(ri, ro, nr, nt, th0=0.0, th1=2.0 * math.pi):
    """(p (2, nv), t (3, ne)) of an annulus / annular sector, structured.

    A full circle (th1 - th0 = 2 pi) wraps; a sector has nt + 1 columns."""
    full = abs((th1 - th0) - 2.0 * math.pi) < 1e-12
    ncol = nt if full else nt + 1
    rr = np.linspace(ri, ro, nr + 1)
    tt = th0 + (th1 - th0) * np.arange(ncol) / nt
    R, T = np.meshgrid(rr, tt, indexing="ij")
    p = np.stack([(R * np.cos(T)).ravel(), (R * np.sin(T)).ravel()])
    nid = np.arange(p.shape[1]).reshape(nr + 1, ncol)
    tri = []
    for i in range(nr):
        for j in range(nt):
            j1 = (j + 1) % ncol if full else j + 1
            a, b, c, d = nid[i, j], nid[i + 1, j], nid[i + 1, j1], nid[i, j1]
            tri += [[a, b, c], [a, c, d]]
    t = np.asarray(tri, dtype=np.int64).T
    v0, v1, v2 = p[:, t[0]], p[:, t[1]], p[:, t[2]]
    ar = (v1[0] - v0[0]) * (v2[1] - v0[1]) - (v2[0] - v0[0]) * (v1[1] - v0[1])
    bad = ar < 0
    t[:, bad] = t[[0, 2, 1]][:, bad]
    return p, t


def _ring_mesh(ri_mm, ro_mm, nr, nt, th0=0.0, th1=2.0 * math.pi):
    from skfem import MeshTri
    p, t = _polar(ri_mm * 1e-3, ro_mm * 1e-3, nr, nt, th0, th1)
    m = MeshTri(np.ascontiguousarray(p), np.ascontiguousarray(t))
    return m, np.full(m.t.shape[1], ctc.PART_ROTOR, dtype=np.int8)


def _synthetic_em(r_lo_mm, r_hi_mm, bfun, nr=6, nt=720, th0=0.0,
                  th1=2.0 * math.pi, with_stator=True, as_az=None):
    """A stored-EM-record lookalike: an air annulus [r_lo, r_hi] (tag 0) with
    B = bfun(x, y) at each element centroid, and a stator ring outside it
    (tag 1) so the annulus check has its bound.  ``as_az`` = A_z(x, y) in
    Wb/m stores the field the OLD way (A per node, no Bx/By)."""
    p, t = _polar(r_lo_mm, r_hi_mm, nr, nt, th0, th1)
    tags = np.zeros(t.shape[1], dtype=np.int64)
    if with_stator:
        ps, ts = _polar(r_hi_mm, r_hi_mm + 5.0, 2, nt, th0, th1)
        t = np.concatenate([t, ts + p.shape[1]], axis=1)
        p = np.concatenate([p, ps], axis=1)
        tags = np.concatenate([tags, np.ones(ts.shape[1], dtype=np.int64)])
    cen = p[:, t].mean(axis=1) * 1e-3                       # m
    rec = {"vertices": p.astype(np.float32), "triangles": t.astype(np.int32),
           "tags": tags.astype(np.int32), "meta": {"synthetic": True}}
    if as_az is not None:
        rec["a_z_per_node"] = as_az(p[0] * 1e-3, p[1] * 1e-3)
    else:
        bx, by = bfun(cen[0], cen[1])
        bx = np.where(tags == 0, bx, 0.0)
        by = np.where(tags == 0, by, 0.0)
        rec["bx_per_tri"], rec["by_per_tri"] = bx, by
    return mpl.em_field_from_record(rec)


def _radial_b(b0, r_o):
    """B_r = b0 r_o / r, B_t = 0 — a source-free radial field."""
    def f(x, y):
        r2 = x * x + y * y
        return b0 * r_o * x / r2, b0 * r_o * y / r2
    return f


def _iso(mesh):
    ne = mesh.t.shape[1]
    return (np.broadcast_to(rs.isotropic_C(E_ST, NU_ST), (ne, 3, 3)).copy(),
            np.zeros(ne))


# ---------------------------------------------------------------------------
# (a) the Maxwell traction
# ---------------------------------------------------------------------------

def test_a_maxwell_traction_components():
    """t_n = (B_n^2 - B_t^2)/2mu0, t_t = B_n B_t/mu0, and T.n agrees."""
    b0 = 1.2
    tn, tt = mpl.traction_components(b0, 0.0, 1.0, 0.0)
    assert tn == pytest.approx(b0 ** 2 / (2 * MU0), rel=1e-12)
    assert tt == pytest.approx(0.0, abs=1e-9)
    tn, tt = mpl.traction_components(0.0, b0, 1.0, 0.0)   # purely tangential B
    assert tn == pytest.approx(-b0 ** 2 / (2 * MU0), rel=1e-12)
    bn, bt = 0.9, 0.4
    a = 0.3
    nx, ny = math.cos(a), math.sin(a)
    bx, by = bn * nx - bt * ny, bn * ny + bt * nx
    tn, tt = mpl.traction_components(bx, by, nx, ny)
    assert tn == pytest.approx((bn ** 2 - bt ** 2) / (2 * MU0), rel=1e-12)
    assert tt == pytest.approx(bn * bt / MU0, rel=1e-12)
    tx, ty = mpl.maxwell_traction(bx, by, nx, ny)
    assert tx * nx + ty * ny == pytest.approx(float(tn), rel=1e-12)
    assert -tx * ny + ty * nx == pytest.approx(float(tt), rel=1e-12)


# ---------------------------------------------------------------------------
# (b) synthetic fields
# ---------------------------------------------------------------------------

R_I, R_O = 20.0, 40.0          # mm, the test rotor ring
GAP = 1.0                      # mm of air above it
B0 = 1.0                       # T at the rotor surface


@pytest.fixture(scope="module")
def ring():
    return _ring_mesh(R_I, R_O, 8, 192)


def test_b_uniform_radial_field_gives_b2_over_2mu0_and_no_net_force(ring):
    """B_r = B0 r_o/r on the gap: the pull is B0^2/2mu0 everywhere on the
    rotor face, the vector sum on the full ring is zero, and the annulus
    integral of the same field gives the same total.

    Measured 2026-10-26: mean 0.39622 MPa against B0^2/2mu0 = 0.39789 MPa
    (-0.42 %: the sampled element-mean B sits inside a 1/r field), net force
    2e-17 of the total, annulus check 0.9956.
    """
    mesh, part = ring
    fld = _synthetic_em(R_O, R_O + GAP, _radial_b(B0, R_O * 1e-3), nr=4)
    spec = mpl.PullSpec("em", field=fld, label="synthetic radial")
    f, raw = mpl.pull_load(mesh, part, spec, order=2)
    rep = mpl.finish_report(raw, spec, 0.05, 1, 8)
    p_exp = B0 ** 2 / (2 * MU0) * 1e-6
    print(f"\nradial field: mean {rep['pressure_mean_mpa']:.5f} MPa against "
          f"{p_exp:.5f}, net/total {rep['net_force_n'] / rep['radial_force_total_n']:.1e},"
          f" check {rep['check']['ratio']:.5f}")
    assert rep["pressure_mean_mpa"] == pytest.approx(p_exp, rel=5e-3)
    assert rep["pressure_peak_mpa"] == pytest.approx(p_exp, rel=5e-3)
    assert rep["net_force_n"] < 1e-8 * rep["radial_force_total_n"]
    # the assembled vector carries the same: zero resultant, total radial pull
    from skfem import Basis
    basis, _e = rs.make_basis(mesh, 2)
    fx, fy = f[0::2].sum(), f[1::2].sum()
    assert math.hypot(fx, fy) < 1e-8 * raw["pressure_mean_mpa"] * 1e6
    assert rep["check"]["ratio"] == pytest.approx(1.0, abs=0.01)
    assert rep["check"]["within_tol"] is True
    assert rep["b_source"].startswith("Bx, By")
    assert rep["matched_fraction"] == pytest.approx(1.0)


def test_b_an_old_record_with_only_a_z_reads_the_same_through_a_half_sector(ring):
    """A record written before Bx/By were stored carries A_z per node; B is
    rebuilt as curl A.  Here A_z = -B0 r_o theta (B_r = B0 r_o/r) on a 180°
    EM model, so the field also has to be EXTENDED to the full ring by the
    model's own periodicity.  Both paths must give the same pull.
    """
    mesh, part = ring
    r_o = R_O * 1e-3
    fld_b = _synthetic_em(R_O, R_O + GAP, _radial_b(B0, r_o), nr=4, nt=360,
                          th1=math.pi)
    fld_a = _synthetic_em(R_O, R_O + GAP, None, nr=4, nt=360, th1=math.pi,
                          as_az=lambda x, y: -B0 * r_o * np.arctan2(y, x))
    assert fld_a.sector_rad == pytest.approx(math.pi)
    assert "curl A_z" in fld_a.b_source
    reps = {}
    for name, fld in (("b", fld_b), ("a", fld_a)):
        spec = mpl.PullSpec("em", field=fld)
        _f, raw = mpl.pull_load(mesh, part, spec, order=2)
        reps[name] = mpl.finish_report(raw, spec, 0.05, 1, 8)
    assert reps["a"]["matched_fraction"] == pytest.approx(1.0)
    # Both are within half a per cent of B0^2/2mu0 and of each other — the
    # element-mean B of a 0.25 mm gap layer in a 1/r field, read once at the
    # centroid and once as the P1 gradient of A.  Measured 2026-10-26: A path
    # 0.39786 MPa (-0.006 %), B path 0.39622 MPa (-0.42 %).
    p_exp = B0 ** 2 / (2 * MU0) * 1e-6
    for k in ("a", "b"):
        assert reps[k]["pressure_mean_mpa"] == pytest.approx(p_exp, rel=5e-3)
    assert reps["a"]["net_force_n"] < 1e-6 * reps["a"]["radial_force_total_n"]


def test_b_a_field_of_another_rotor_is_refused(ring):
    """An EM field whose air does not sit where this rotor's gap is (here: a
    rotor 3 mm bigger) is refused, not silently sampled as zero."""
    mesh, part = ring
    fld = _synthetic_em(R_O + 3.0, R_O + 4.0, _radial_b(B0, R_O * 1e-3))
    with pytest.raises(mpl.PullFieldError, match="not of this rotor"):
        mpl.pull_load(mesh, part, mpl.PullSpec("em", field=fld), order=2)


def test_b_a_record_without_a_field_direction_is_refused():
    p, t = _polar(40.0, 41.0, 2, 64)
    with pytest.raises(mpl.PullFieldError, match="direction"):
        mpl.em_field_from_record({"vertices": p, "triangles": t,
                                  "tags": np.zeros(t.shape[1]),
                                  "b_mag_per_tri": np.ones(t.shape[1])})


# ---------------------------------------------------------------------------
# (c) Lamé
# ---------------------------------------------------------------------------

def _lame(ri, ro, s_in, s_out, r):
    """Plane-stress thick ring with sigma_rr(ri) = s_in, sigma_rr(ro) = s_out."""
    A = (s_out * ro ** 2 - s_in * ri ** 2) / (ro ** 2 - ri ** 2)
    B = (s_out - s_in) * ri ** 2 * ro ** 2 / (ro ** 2 - ri ** 2)
    return A - B / r ** 2, A + B / r ** 2


@pytest.mark.parametrize("side", ["outer", "inner"])
def test_c_lame_thick_ring_under_a_uniform_pull(side):
    """A free steel ring pulled by p = 0.70 MPa toward the stator: on its OD
    (inner rotor: sigma_rr(ro) = +p) or on its bore (outer rotor:
    sigma_rr(ri) = +p, the pull points inward).  FEM against Lamé, P2 on a
    structured 8 x 192 mesh.

    Measured 2026-10-26 (element-centroid stresses): hoop error 0.106 %
    (outer) / 0.174 % (inner) of the peak hoop stress, radial 0.092 % /
    0.151 %; the applied total is p 2 pi r L to 1e-3 (chords).
    """
    p = 0.70e6
    mesh, part = _ring_mesh(R_I, R_O, 8, 192)
    spec = mpl.uniform_spec(pull_mpa=0.70)
    f, raw = mpl.pull_load(mesh, part, spec, order=2, side=side)
    C, rho = _iso(mesh)
    sol = rs.solve_plane_stress(mesh, C, rho, 0.0, order=2, f_extra=f)
    cen = mesh.p[:, mesh.t].mean(axis=1).T
    r = np.hypot(cen[:, 0], cen[:, 1])
    phi = np.arctan2(cen[:, 1], cen[:, 0])
    srr, stt = rs._polar(sol.sigma_tri, phi)               # noqa: SLF001
    ri, ro = R_I * 1e-3, R_O * 1e-3
    s_in, s_out = (0.0, p) if side == "outer" else (p, 0.0)
    a_rr, a_tt = _lame(ri, ro, s_in, s_out, r)
    peak = float(np.abs(a_tt).max())
    err_tt = float(np.abs(stt - a_tt).max()) / peak
    err_rr = float(np.abs(srr - a_rr).max()) / peak
    print(f"\nLamé {side}: hoop err {100 * err_tt:.3f} %, radial err "
          f"{100 * err_rr:.3f} % of {peak * 1e-6:.4f} MPa")
    assert err_tt < 5e-3
    assert err_rr < 5e-3
    rep = mpl.finish_report(raw, spec, 0.05, 1, 8)
    assert rep["gap_side"] == side
    assert rep["check"]["ratio"] == pytest.approx(1.0, abs=1e-3)
    assert rep["pressure_mean_mpa"] == pytest.approx(0.70, rel=1e-3)
    sgn = 1.0 if side == "outer" else -1.0
    r_g = ro if side == "outer" else ri
    assert rep["radial_force_total_n"] == pytest.approx(
        sgn * p * 2 * math.pi * r_g * 0.05, rel=1e-3)


# ---------------------------------------------------------------------------
# (d) full vs sector — the load vector
# ---------------------------------------------------------------------------

NPER = 8


def _periodic_b(b0, r_o, eps_pole=0.0):
    """A pole-periodic field (period 2 pi / NPER in B^2) plus, optionally, a
    once-per-revolution modulation so the poles differ."""
    def f(x, y):
        r = np.hypot(x, y)
        th = np.arctan2(y, x)
        amp = b0 * r_o / r * (1.0 + 0.3 * np.cos(NPER * th)
                              + eps_pole * np.cos(th))
        bt = 0.1 * b0 * r_o / r * np.sin(NPER * th)
        c, s = np.cos(th), np.sin(th)
        return amp * c - bt * s, amp * s + bt * c
    return f


def _dof_map(b_from, b_to, decimals=9):
    """Index into ``b_to`` of every dof of ``b_from``, matched by location
    (x and y dofs separately — ElementVector interleaves them)."""
    def keys(b):
        loc = np.round(b.doflocs.T * 1e3, decimals)
        comp = np.arange(b.N) % 2
        return [(float(a), float(c), int(k)) for (a, c), k in zip(loc, comp)]
    idx = {k: i for i, k in enumerate(keys(b_to))}
    return np.array([idx[k] for k in keys(b_from)])


@pytest.mark.parametrize("eps_pole", [0.0, 0.05])
def test_d_full_and_sector_pull_vectors_are_the_same_dof_for_dof(eps_pole):
    """The sector [cut, cut + 2 pi/8] of a structured ring IS the full ring's
    first eighth, node for node.  The EM pull assembled on each must agree dof
    for dof — including when the field's poles differ (``eps_pole``), where
    both must pick the same worst pole and give it to every pole.

    Measured 2026-10-26: max |f_sector - f_full| / max |f| < 1e-12 (the same
    points sampled in the same field), and with eps_pole = 0.05 the poles
    spread by +9.5 % at the worst one — pole 0, around theta = 0 where
    cos(theta) = 1 — in both models, as the analytic field says.
    """
    from skfem import Basis
    P = 2 * math.pi / NPER
    cut = -0.5 * P
    full, pf = _ring_mesh(R_I, R_O, 4, NPER * 24, th0=cut, th1=cut + 2 * math.pi)
    sec, ps = _ring_mesh(R_I, R_O, 4, 24, th0=cut, th1=cut + P)
    fld = _synthetic_em(R_O, R_O + GAP, _periodic_b(B0, R_O * 1e-3, eps_pole),
                        nr=4, nt=NPER * 96)
    spec = mpl.PullSpec("em", field=fld)
    f_full, raw_f = mpl.pull_load(full, pf, spec, order=2, period_n=NPER,
                                  period_cut_rad=cut)
    f_sec, raw_s = mpl.pull_load(sec, ps, spec, order=2, period_n=NPER,
                                 period_cut_rad=cut)
    assert raw_f["envelope"]["worst_pole"] == raw_s["envelope"]["worst_pole"]
    bf, _e = rs.make_basis(full, 2)
    bs, _e = rs.make_basis(sec, 2)
    idx = _dof_map(bs, bf)
    # The sector's two cut-face nodes are shared with the NEIGHBOURING
    # sectors in the full model, whose facets add their own share there — the
    # pull acts on the gap facets only, and a cut node on the OD collects from
    # one facet in the sector and two in the full ring.  Compare the rest.
    th = np.arctan2(bs.doflocs[1], bs.doflocs[0])
    interior = (np.abs(th - cut) > 1e-9) & (np.abs(th - (cut + P)) > 1e-9)
    scale = float(np.abs(f_full).max())
    err = float(np.abs(f_sec[interior] - f_full[idx[interior]]).max()) / scale
    assert err < 1e-12
    rep_f = mpl.finish_report(raw_f, spec, 0.05, 1, NPER)
    rep_s = mpl.finish_report(raw_s, spec, 0.05, NPER, NPER)
    for k in ("pressure_peak_mpa", "pressure_mean_mpa", "radial_force_total_n",
              "radial_force_per_pole_n"):
        assert rep_s[k] == pytest.approx(rep_f[k], rel=1e-10), k
    spread = rep_f["envelope"]["pole_spread"]
    if eps_pole:
        # the same spread from the analytic field: sigma_rr ∝ amp^2 - bt^2
        # integrated over each pole's arc (the 1/r factor is common to all)
        tq = np.linspace(cut, cut + 2 * math.pi, NPER * 4000, endpoint=False)
        g = ((1 + 0.3 * np.cos(NPER * tq) + eps_pole * np.cos(tq)) ** 2
             - (0.1 * np.sin(NPER * tq)) ** 2)
        per_pole = g.reshape(NPER, -1).sum(axis=1)
        exp_hi = per_pole.max() / per_pole.mean() - 1.0
        print(f"envelope: spread {spread}, analytic +{exp_hi:.4f}")
        assert spread[1] == pytest.approx(exp_hi, rel=0.02)
        assert raw_f["envelope"]["worst_pole"] == int(np.argmax(per_pole)) == 0
    else:
        assert max(abs(spread[0]), abs(spread[1])) < 1e-9


# ---------------------------------------------------------------------------
# the 4-pole spoke rotor (the symmetry suite's fixture, meshed by gmsh)
# ---------------------------------------------------------------------------

SP_R_BORE, SP_R_OD = 20.0, 50.0
SP_MAG_W, SP_MAG_R0, SP_MAG_R1 = 8.0, 26.0, 46.0
SP_POLES = 4
SP_ASSIGN = {"rotor_core": "20SW1200", "magnet": "F52SH_120C",
             "sleeve": "HM63_UD_60", "shaft": "Aluminium_7075"}
SP_CONTACTS = {"magnet_rotor": ctc.ContactSpec("separation", 0.0)}
SP_RPM = 20000.0
SP_MESH = 1.0
SP_L_MM = 50.0
P_PULL_MPA = 0.70


def _spoke_polys(n_poles: int = SP_POLES):
    from shapely import affinity
    from shapely.geometry import Point, Polygon
    disk = Point(0, 0).buffer(SP_R_OD, resolution=128)
    annulus = disk.difference(Point(0, 0).buffer(SP_R_BORE, resolution=96))
    magnets = []
    for k in range(n_poles):
        slab = Polygon([(SP_MAG_R0, -SP_MAG_W / 2), (SP_MAG_R1, -SP_MAG_W / 2),
                        (SP_MAG_R1, SP_MAG_W / 2), (SP_MAG_R0, SP_MAG_W / 2)])
        magnets.append((affinity.rotate(slab, 360.0 * k / n_poles,
                                        origin=(0, 0)),
                        1 if k % 2 == 0 else -1))
    rotor = annulus
    for mp, _pol in magnets:
        rotor = rotor.difference(mp)
    return {"rotor": rotor, "magnets": magnets, "sleeve": None, "shaft": None,
            "sleeve_r_mm": (0.0, 0.0)}


def _spoke_solve(symmetry: str, *, loads: str = "centrifugal",
                 torque_nm: float = 0.0, pull=None, overrides=None,
                 rpm: float = SP_RPM, with_field: bool = False):
    return rs.solve_rotor_stress(
        _spoke_polys(), SP_ASSIGN, rpm, 1.0, 0.0, stack_length_mm=SP_L_MM,
        mesh_size_mm=SP_MESH, order=2, with_field=with_field,
        material_overrides=overrides, contacts=SP_CONTACTS, lift_off_solves=0,
        case_mode="single", loads=loads, torque_nm=torque_nm,
        symmetry=symmetry, pull=pull)


def _case(out):
    return out["cases"][out["primary_case"]]


def _spoke_em():
    """A field over the spoke rotor's OD: radial, 1/r, with a 4-fold (one per
    magnet pitch) modulation — periodic with the rotor."""
    r_o = SP_R_OD * 1e-3

    def f(x, y):
        r = np.hypot(x, y)
        th = np.arctan2(y, x)
        amp = 1.1 * r_o / r * (1.0 + 0.25 * np.cos(SP_POLES * th))
        return amp * np.cos(th), amp * np.sin(th)
    return _synthetic_em(SP_R_OD, SP_R_OD + 1.0, f, nr=4, nt=1440)


# ---------------------------------------------------------------------------
# (e) pull = none: nothing moves
# ---------------------------------------------------------------------------

#: Measured 2026-10-26 on the code BEFORE the pull existed (``git archive`` of
#: 3656d121, the same fixture, SuperLU) and reproduced bit for bit after it.
PINNED = {
    ("full", "both"): {"sf_min": 1.4031376881706277,
                       "rotor_vm_p995": 220.22892123143603,
                       "od_growth": 12.310842497352779},
    ("sector", "centrifugal"): {"sf_min": 1.3268430018180795,
                                "rotor_vm_p995": 200.28990204458694,
                                "od_growth": 12.32326121046532},
}


@pytest.mark.parametrize("sym_mode,loads", list(PINNED))
def test_e_no_pull_changes_nothing(sym_mode, loads):
    out = _spoke_solve(sym_mode, loads=loads,
                       torque_nm=(40.0 if loads == "both" else 0.0))
    c = _case(out)
    pin = PINNED[(sym_mode, loads)]
    assert out["pull_load"] is None
    assert "load_case" not in out["retention"]
    assert c["sf_min"] == pytest.approx(pin["sf_min"], rel=1e-6)
    assert c["parts"]["rotor"]["von_mises_p995_mpa"] == pytest.approx(
        pin["rotor_vm_p995"], rel=1e-6)
    assert c["rotor_od_growth_um"] == pytest.approx(pin["od_growth"], rel=1e-6)
    assert "magnet_pull_kn_per_m" not in c["magnet_retention"]


# ---------------------------------------------------------------------------
# (d) full vs sector — the solved answer
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def spoke_pull():
    uni = mpl.uniform_spec(pull_mpa=P_PULL_MPA)
    em = mpl.PullSpec("em", field=_spoke_em(), label="synthetic spoke field")
    return {(m, src): _spoke_solve(m, pull=spec)
            for m in ("full", "sector")
            for src, spec in (("uniform", uni), ("em", em))}


@pytest.mark.parametrize("src", ["uniform", "em"])
def test_d_full_and_sector_agree_with_the_pull(spoke_pull, src):
    """The same comparison the symmetry suite makes (element-field peaks, the
    contact, the growth — within 2 %), now with the pull on, and the pull
    reports themselves agreeing per pole.

    Measured 2026-10-26 (mesh 1.0 mm, P2, 20,000 rpm), full -> sector:
      uniform 0.70 MPa: 2.75 kN per pole both; rotor vM p99.5 (element)
        187.41 -> 186.69 (-0.38 %), magnet principal p99.5 +0.46 %,
        magnet_rotor p max +0.31 %, OD growth -0.02 %;
      synthetic EM field: 0.750 MPa peak, 1.94 kN per pole both, check
        0.9964; the same four within 0.45 %.
    """
    full, sec = spoke_pull[("full", src)], spoke_pull[("sector", src)]
    pf, ps = full["pull_load"], sec["pull_load"]
    print(f"\n[{src}] full: {mpl.summary_text(pf)}; check {pf['check']['ratio']:.4f}"
          f"\n[{src}] sector: {mpl.summary_text(ps)}; check {ps['check']['ratio']:.4f}")
    for k in ("pressure_peak_mpa", "pressure_mean_mpa",
              "radial_force_per_pole_n", "radial_force_total_n"):
        assert ps[k] == pytest.approx(pf[k], rel=2e-3), k
    assert ps["n_poles"] == pf["n_poles"] == SP_POLES
    assert ps["n_sectors"] == SP_POLES and pf["n_sectors"] == 1
    assert pf["net_force_n"] < 1e-3 * abs(pf["radial_force_total_n"])
    cf, cs = _case(full), _case(sec)
    checks = [
        ("rotor vM p99.5 (element)",
         cf["parts"]["rotor"]["von_mises_p995_unaveraged_mpa"],
         cs["parts"]["rotor"]["von_mises_p995_unaveraged_mpa"]),
        ("magnet principal p99.5",
         cf["parts"]["magnet"]["principal_max_p995_mpa"],
         cs["parts"]["magnet"]["principal_max_p995_mpa"]),
        ("magnet_rotor pressure_max",
         cf["interfaces"]["magnet_rotor"]["pressure_max_mpa"],
         cs["interfaces"]["magnet_rotor"]["pressure_max_mpa"]),
        ("rotor OD growth", cf["rotor_od_growth_um"], cs["rotor_od_growth_um"]),
    ]
    for n, a, b in checks:
        print(f"   {n}: full {a:.4f} sector {b:.4f} ({100 * (b - a) / a:+.2f} %)")
    bad = [n for n, a, b in checks if abs(b - a) > 0.02 * abs(a)]
    assert not bad, bad
    for out in (full, sec):
        assert "magnetic pull" in out["retention"]["load_case"]


def test_d_the_uniform_pull_carries_its_full_cylinder(spoke_pull):
    """On this rotor the gap surface IS the whole OD circle, so the applied
    pull is p x 2 pi r_o L exactly (the polygon's chords aside)."""
    pf = spoke_pull[("full", "uniform")]["pull_load"]
    exp = P_PULL_MPA * 1e6 * 2 * math.pi * SP_R_OD * 1e-3 * SP_L_MM * 1e-3
    assert pf["radial_force_total_n"] == pytest.approx(exp, rel=1e-3)
    assert pf["radial_force_per_pole_n"] == pytest.approx(exp / SP_POLES, rel=1e-3)
    assert pf["estimate"] is True
    assert pf["check"]["ratio"] == pytest.approx(1.0, abs=1e-3)


# ---------------------------------------------------------------------------
# (f) body force (density x k) against surface traction
# ---------------------------------------------------------------------------

def _iron_first_moment_m3() -> float:
    """integral(r dA) over the spoke rotor's iron, m^3 — so that the iron's
    centrifugal radial load is rho * omega^2 * this * L."""
    from shapely import vectorized
    core = _spoke_polys()["rotor"]
    rr = np.linspace(SP_R_BORE, SP_R_OD, 601)
    tt = np.linspace(0, 2 * math.pi, 2880, endpoint=False)
    R, T = np.meshgrid(0.5 * (rr[1:] + rr[:-1]), tt, indexing="ij")
    inside = vectorized.contains(core, R * np.cos(T), R * np.sin(T))
    dA = (rr[1] - rr[0]) * R * (tt[1] - tt[0])
    return float((R * dA * inside).sum()) * 1e-9


def _band_vm(out, case_key=None):
    """Mean element von Mises of the rotor iron in three radial bands: the hub
    under the magnets, the poles beside them, the bridges over them."""
    fld = out["field"]
    c = case_key or out["primary_case"]
    vm = np.asarray(fld["cases"][c]["vm_per_tri"], float)
    dom = np.asarray(fld["domain_per_tri"])
    v = np.asarray(fld["vertices"], float)
    t = np.asarray(fld["triangles"])
    cen = v[t].mean(axis=1)
    r = np.hypot(cen[:, 0], cen[:, 1])
    ar = 0.5 * np.abs((v[t[:, 1], 0] - v[t[:, 0], 0]) * (v[t[:, 2], 1] - v[t[:, 0], 1])
                      - (v[t[:, 2], 0] - v[t[:, 0], 0]) * (v[t[:, 1], 1] - v[t[:, 0], 1]))
    iron = dom == ctc.PART_ROTOR
    out_ = {}
    for name, lo, hi in (("hub", 0.0, SP_MAG_R0), ("poles", SP_MAG_R0, SP_MAG_R1),
                         ("bridges/OD", SP_MAG_R1, 1e9)):
        m = iron & (r >= lo) & (r < hi)
        out_[name] = float((vm[m] * ar[m]).sum() / ar[m].sum())
    return out_


#: The study's density factor (owner 2026-10-26: "20SW1200_pull_k5.31").
K_STUDY = 5.31


def test_f_density_k_body_force_is_not_the_pull():
    """The study's shortcut — rotor-iron density x 5.31 — against the real
    0.70 MPa traction, AT THE SPEED WHERE THE TWO CARRY THE SAME TOTAL RADIAL
    LOAD: (k - 1) rho omega^2 integral(r dA) L = p 2 pi r_o L.  On this rotor
    that is ~1,660 rpm (at 20,000 rpm spin dwarfs a 0.7 MPa pull and k would
    be 1.03).  Three full solves at that speed: spin alone, spin + traction,
    spin with rho x k.

    WHY THEY DIFFER.  The extra body force (k - 1) rho omega^2 r acts on EVERY
    piece of iron in proportion to its mass and radius — the hub under the
    magnets included, which the field never touches — and on nothing else.
    The magnetic pull acts only where the field meets the rotor, on the
    air-gap face, and is carried in from there.  On this fixture the poles
    are wide solid iron reaching the OD, so most of the iron's first moment
    sits under the pulled face and the two loads end up close in the iron;
    the measured split (mesh 1.0 mm, P2, full model, 1,655 rpm, 10,995 N):

                                   spin   +traction   rho x 5.31
        rotor vM p99.5 (element)  1.252     5.864       6.137  MPa
        magnet principal p99.5    0.071     0.270       0.200  MPa
        magnet_rotor p max        0.274     0.583       0.586  MPa
        rotor OD growth           0.084     0.435       0.428  µm
        iron vM mean, hub         0.730     3.385       3.646  MPa (+10 % body)
        iron vM mean, poles       0.230     1.193       1.126  MPa
        iron vM mean, bridges/OD  0.366     1.869       1.795  MPa

    i.e. the body force over-loads the hub by ~10 %, under-loads the OD face,
    and misses the magnets' share by a third (0.20 against 0.27 MPa).  Two
    things matter more than these per cents and are not visible in one
    solve: the body force scales with omega^2, so a k tuned at one speed is a
    pull 1.44x too big at 1.2x overspeed and ZERO at standstill, where the
    real pull is unchanged; and on a rotor whose pulled surface carries
    little mass (thin pole shoes, magnets at the gap, an outer rotor's
    magnets) the body force puts the load where the mass is, not where the
    field is.  The assertions pin that the totals are equal and the two
    answers are not.
    """
    p = P_PULL_MPA * 1e6
    rho = rs.part_mech("rotor", SP_ASSIGN["rotor_core"]).density
    mom = _iron_first_moment_m3()
    f_pull_per_m = p * 2 * math.pi * SP_R_OD * 1e-3
    w = math.sqrt(f_pull_per_m / ((K_STUDY - 1.0) * rho * mom))
    rpm = w * 60.0 / (2 * math.pi)
    raw = dict(rs._raw_material(("steel",), SP_ASSIGN["rotor_core"])[1])  # noqa: SLF001
    raw["density"] = float(raw["density"]) * K_STUDY
    raw["category"] = "steel"
    spin = _spoke_solve("full", rpm=rpm, with_field=True)
    trac = _spoke_solve("full", rpm=rpm, with_field=True,
                        pull=mpl.uniform_spec(pull_mpa=P_PULL_MPA))
    body = _spoke_solve("full", rpm=rpm, with_field=True,
                        overrides={SP_ASSIGN["rotor_core"]: raw})
    # equal total radial load, by construction — checked on the solver's own
    # report of the traction
    f_trac = trac["pull_load"]["radial_force_total_n"]
    f_body = (K_STUDY - 1.0) * rho * w ** 2 * mom * SP_L_MM * 1e-3
    assert f_trac == pytest.approx(f_body, rel=2e-3)

    bs, bt, bk = _band_vm(spin), _band_vm(trac), _band_vm(body)
    cs, ct, ck = _case(spin), _case(trac), _case(body)
    rows = [("rotor vM p99.5 (element) MPa",
             lambda c: c["parts"]["rotor"]["von_mises_p995_unaveraged_mpa"]),
            ("magnet principal p99.5 MPa",
             lambda c: c["parts"]["magnet"]["principal_max_p995_mpa"]),
            ("magnet_rotor pressure max MPa",
             lambda c: c["interfaces"]["magnet_rotor"]["pressure_max_mpa"]),
            ("rotor OD growth um", lambda c: c["rotor_od_growth_um"])]
    print(f"\nk = {K_STUDY} at {rpm:,.0f} rpm: F_pull = F_body = {f_trac:,.0f} N")
    print(f"{'':34s}{'spin':>9s}{'+traction':>11s}{'rho x k':>9s}")
    for label, fn in rows:
        print(f"{label:34s}{fn(cs):9.3f}{fn(ct):11.3f}{fn(ck):9.3f}")
    for band in bs:
        print(f"{'iron vM mean, ' + band + ' MPa':34s}{bs[band]:9.3f}"
              f"{bt[band]:11.3f}{bk[band]:9.3f}")
    # the two loads of the same total are not the same answer: the body force
    # adds more to the hub and less to the OD face than the traction does …
    d_t = {b: bt[b] - bs[b] for b in bs}
    d_k = {b: bk[b] - bs[b] for b in bs}
    print("added by the load:  traction " + ", ".join(f"{b} {v:+.2f}" for b, v in d_t.items())
          + " | rho x k " + ", ".join(f"{b} {v:+.2f}" for b, v in d_k.items()))
    assert d_k["hub"] > 1.05 * d_t["hub"]
    assert d_k["bridges/OD"] < d_t["bridges/OD"]
    # … and the magnets' share differs by a fifth or more
    mt = ct["parts"]["magnet"]["principal_max_p995_mpa"]
    mk = ck["parts"]["magnet"]["principal_max_p995_mpa"]
    assert abs(mt - mk) > 0.2 * mt


# ---------------------------------------------------------------------------
# radial visibility — the gap does not see inside the rotor
# ---------------------------------------------------------------------------

def test_visibility_hides_a_face_with_solid_further_out():
    """Two concentric rings, an inner one (iron, 20–30 mm) and an outer one
    (magnet, 32–40 mm): the inner ring's OD faces outward but the gap cannot
    see it — the outer ring is in the way — so it carries no pull, exactly as
    a pocket floor or a magnet top under an iron bridge."""
    from skfem import MeshTri
    p1, t1 = _polar(20e-3, 30e-3, 3, 96)
    p2, t2 = _polar(32e-3, 40e-3, 3, 96)
    p = np.concatenate([p1, p2], axis=1)
    t = np.concatenate([t1, t2 + p1.shape[1]], axis=1)
    mesh = MeshTri(np.ascontiguousarray(p), np.ascontiguousarray(t))
    part = np.concatenate([np.full(t1.shape[1], ctc.PART_ROTOR),
                           np.full(t2.shape[1], ctc.PART_MAGNET)]).astype(np.int8)
    th = np.linspace(0, 2 * math.pi, 50, endpoint=False) + 0.013
    # points ON the 96-gon's chords (the meshed surface), not on the circle
    h = math.pi / 96
    dth = np.mod(th, 2 * h) - h                    # angle from the chord's middle

    def on(R):
        r = R * math.cos(h) / np.cos(dth)
        return np.stack([r * np.cos(th), r * np.sin(th)], axis=1)
    assert mpl.radial_visibility(mesh, part, on(40e-3)).all()
    assert not mpl.radial_visibility(mesh, part, on(30e-3)).any()
    # and the pull with no band puts nothing on the hidden ring
    spec = mpl.uniform_spec(pull_mpa=0.5)
    spec_em_like = mpl.PullSpec("em", field=_synthetic_em(
        40.0, 41.0, _radial_b(1.0, 0.04)))
    _f, raw = mpl.pull_load(mesh, part, spec_em_like, order=2)
    assert set(raw["_by_part_n_per_m"]) == {"magnet"}
    _f, raw = mpl.pull_load(mesh, part, spec, order=2)
    assert raw["_total_n_per_m"] == pytest.approx(0.5e6 * 2 * math.pi * 0.04,
                                                  rel=2e-3)


# ---------------------------------------------------------------------------
# (g) the route, the stored record, the EM field record
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    return TestClient(app)


ROUTE_BASE = {"rpm": 6000, "mesh_size_mm": 3.0, "order": 1, "cases": "single",
              "loads": "centrifugal", "lift_off_solves": 0, "field": False}


@pytest.fixture
def quiet_last(monkeypatch, tmp_path):
    """Keep the route's 'last result' bookkeeping in a temp folder."""
    from motor_ai_sim.routes import mechanical as mech
    monkeypatch.setattr(mech, "_LAST", {}, raising=True)
    monkeypatch.setattr(mech, "_LAST_LOADED", True, raising=True)
    monkeypatch.setattr(mech, "_last_store_path",
                        lambda: str(tmp_path / ".last_mechanical.pkl"))


@pytest.mark.parametrize("params,field", [
    ({"pull": "sideways"}, "pull"),
    ({"pull": "uniform"}, "pull_mpa"),
    ({"pull": "uniform", "pull_mpa": 0.7, "pull_b_gap_t": 1.0}, "pull_b_gap_t"),
    ({"pull": "none", "pull_mpa": 0.7}, "pull"),
    ({"pull": "uniform", "pull_mpa": 0.7, "pull_duty": "rated"}, "pull_duty"),
    ({"pull": "em", "pull_mpa": 0.7}, "pull_mpa"),
])
def test_g_route_refuses_a_pull_it_cannot_honour(client, params, field):
    r = client.get("/api/mechanical/rotor_stress", params={**ROUTE_BASE, **params})
    assert r.status_code == 422, r.text[:300]
    bad = r.json()["detail"]["invalid_parameters"]
    assert bad and bad[0]["field"] == field, bad


def test_g_route_pull_em_needs_a_duty_and_its_field(client, monkeypatch):
    from motor_ai_sim import duty_fields as df
    monkeypatch.setattr(df, "active_context", lambda: None)
    r = client.get("/api/mechanical/rotor_stress",
                   params={**ROUTE_BASE, "pull": "em"})
    assert r.status_code == 422 and "duty" in r.text, r.text[:300]
    monkeypatch.setattr(df, "active_context", lambda: ("D", "C", "rated"))
    monkeypatch.setattr(df, "load", lambda *a, **k: None)
    r = client.get("/api/mechanical/rotor_stress",
                   params={**ROUTE_BASE, "pull": "em"})
    assert r.status_code == 422, r.text[:300]
    bad = r.json()["detail"]["invalid_parameters"][0]
    assert bad["field"] == "pull_duty" and bad["kind"] == "missing"


def test_g_pull_none_keeps_every_key_it_had():
    from motor_ai_sim.routes import mechanical as mech
    spec, params, tail = mech._resolve_pull("none", None, None, None, None)  # noqa: SLF001
    assert spec is None and params == {} and tail == ()
    _s, p_u, t_u = mech._resolve_pull("uniform", None, 0.7, None, None)  # noqa: SLF001
    assert p_u == {"pull": "uniform", "pull_mpa": 0.7} and t_u


@pytest.fixture(scope="module")
def route_uniform(client):
    from motor_ai_sim.routes import mechanical as mech
    saved = (dict(mech._LAST), mech._LAST_LOADED)          # noqa: SLF001
    try:
        r = client.get("/api/mechanical/rotor_stress",
                       params={**ROUTE_BASE, "pull": "uniform", "pull_mpa": 0.7})
        assert r.status_code == 200, r.text[:600]
        return r.json()
    finally:
        mech._LAST.clear()                                  # noqa: SLF001
        mech._LAST.update(saved[0])                          # noqa: SLF001


def test_g_route_uniform_pull_is_reported_and_in_the_verdict(route_uniform):
    out = route_uniform
    pl = out["pull_load"]
    assert pl["source"] == "uniform" and pl["estimate"] is True
    assert pl["pressure_peak_mpa"] == pytest.approx(0.7, rel=1e-6)
    assert pl["radial_force_per_pole_n"] > 0 and pl["n_poles"] >= 1
    assert 0.5 < pl["check"]["ratio"] <= 1.0 + 1e-6
    assert "magnetic pull 0.700 MPa" in out["retention"]["load_case"]


def test_g_the_stored_row_carries_the_pull_with_its_provenance(route_uniform):
    """``duty_results`` keeps the pull beside the case (source, size, check),
    and the motor view turns it into 'worst case: rpm, torque, pull'."""
    from motor_ai_sim import duty_results as dr
    from motor_ai_sim.development import motor_view as mv
    params = {"rpm": 6000, "loads": "centrifugal", "cases": "single",
              "pull": "uniform", "pull_mpa": 0.7}
    row = dr.compact_mechanical("rotor_stress", route_uniform, params, "fp")
    assert row["point"]["pull"] == "uniform" and row["point"]["pull_mpa"] == 0.7
    assert row["pull"]["source"] == "uniform"
    assert row["pull"]["pressure_peak_mpa"] == pytest.approx(0.7)
    assert row["pull"]["radial_force_per_pole_n"] > 0
    assert row["load_case"] and "magnetic pull" in row["load_case"]
    block = mv._rotor_stress_block(row)                    # noqa: SLF001
    assert block["worst_case"]["pull_source"] == "uniform"
    assert block["worst_case"]["pull_mpa"] == pytest.approx(0.7)
    assert block["load_case"] == row["load_case"]


def test_g_route_pull_em_reads_the_duty_field(client, monkeypatch, quiet_last,
                                              route_uniform):
    """End to end with a stored-record lookalike over the sandbox rotor's gap
    (radial 1/r field, B = 1 T at the rotor face): the route reads it as the
    active duty's, names it, and the pull it applies is B^2/2mu0 checked
    against the annulus integral."""
    from motor_ai_sim import duty_fields as df
    r_o = route_uniform["pull_load"]["r_gap_mm"]
    # the air reaches 3 mm inside the rotor face too, so notch floors the gap
    # sees are inside the record (the record's air overlapping iron is never
    # read: the field is sampled OUTSIDE the solid)
    fld = _synthetic_em(r_o - 3.0, r_o + 1.0, _radial_b(1.0, r_o * 1e-3),
                        nr=16, nt=1440)
    rec = {"vertices": fld.p * 1e3, "triangles": fld.t, "tags": fld.tags,
           "bx_per_tri": fld.bx, "by_per_tri": fld.by,
           "meta": {"computed_at": "2026-10-26T12:00:00", "saved_at": "s1"}}
    monkeypatch.setattr(df, "active_context", lambda: ("D", "C", "rated"))
    monkeypatch.setattr(df, "load", lambda die, cfg, duty, kind: rec
                        if (die, cfg, duty, kind) == ("D", "C", "rated", "em")
                        else None)
    r = client.get("/api/mechanical/rotor_stress",
                   params={**ROUTE_BASE, "pull": "em"})
    assert r.status_code == 200, r.text[:600]
    pl = r.json()["pull_load"]
    assert pl["source"] == "em" and pl["origin"]["duty"] == "rated"
    assert pl["origin"]["geometry"] in ("match", "unverified")
    assert pl["matched_fraction"] > 0.95
    # B = 1 T at the outermost faces (r_o), more on the floors between them
    # the gap sees (B ∝ 1/r below r_o), less per unit length on a slanted
    # flank (only its radial projection is pulled): the peak lies between
    # B^2/2mu0 and the deepest floor's (r_o/r)^2 times it, and the TOTAL —
    # spread over the full circle at r_o — is B^2/2mu0 to the check's own
    # tolerance, because the field is source-free (K / r_o).
    p_o = 1.0 / (2 * MU0) * 1e-6
    print(f"\nsandbox EM pull: peak {pl['pressure_peak_mpa']:.4f}, mean "
          f"{pl['pressure_mean_mpa']:.4f}, nominal {pl['pressure_nominal_mpa']:.4f}"
          f" MPa against {p_o:.4f}; check {pl['check']['ratio']:.4f}")
    assert p_o * 0.99 < pl["pressure_peak_mpa"] < p_o * (r_o / (r_o - 3.0)) ** 2
    assert pl["pressure_nominal_mpa"] == pytest.approx(p_o, rel=0.1)
    assert pl["check"]["ratio"] == pytest.approx(1.0, abs=0.1)


def test_g_pack_em_now_stores_the_field_components():
    """The EM record keeps Bx and By per element beside |B| (2026-10-26), and
    the pull reads them back as stored — not as curl A."""
    from motor_ai_sim import duty_fields as df
    p, t = _polar(40.0, 41.0, 2, 64)
    cen = p[:, t].mean(axis=1)
    bx, by = _radial_b(1.0, 0.04)(cen[0] * 1e-3, cen[1] * 1e-3)
    entry = {"field": {"P_mm": p, "T": t, "tags": np.zeros(t.shape[1], int),
                       "Bx": bx, "By": by},
             "scalars": {}, "meta": {}}
    packed = df.pack_em(entry)
    arr = packed["arrays"]
    np.testing.assert_allclose(arr["bx_per_tri"], bx, rtol=1e-6, atol=1e-7)
    np.testing.assert_allclose(arr["by_per_tri"], by, rtol=1e-6, atol=1e-7)
    np.testing.assert_allclose(arr["b_mag_per_tri"], np.hypot(bx, by), rtol=1e-6)
    assert packed["meta"]["units"]["bx_per_tri"] == "T"
    fld = mpl.em_field_from_record(arr)
    assert fld.b_source.startswith("Bx, By")
