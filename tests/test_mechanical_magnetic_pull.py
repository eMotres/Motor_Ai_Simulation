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
  (g) the route: parameters, validation, the cache key, the stored record;
  (h) the independent review's findings (2026-10-26): the stored frames'
      envelope against a single instant (F1), per-magnet signed retention
      (F2), radial flanks and sector cut faces (F3), a current-carrying
      sleeve (F4), per-part completeness (F5).

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


# ---------------------------------------------------------------------------
# (h) the independent physics review of PR #435 (Codex Astra, 2026-10-26):
#     F1 single instant != worst case, F2 signed retention, F3 radial flanks,
#     F4 sleeve current, F5 per-part completeness.  Each test reproduces the
#     reviewer's counterexample; the pre-fix code fails it.
# ---------------------------------------------------------------------------

def _rec_of(fld, meta=None):
    """An EmField back to the stored-record shape (mm, arrays, meta)."""
    return {"vertices": fld.p * 1e3, "triangles": fld.t, "tags": fld.tags,
            "bx_per_tri": fld.bx, "by_per_tri": fld.by,
            "meta": dict(meta or {"eddy": False})}


def _phase_field(phi):
    """The reviewer's F1 field: B_r = B0 r_o/r cos(2θ) [1 + 0.2 cos(4θ + φ)],
    B_t = 0 — four poles, and EVERY pole sees the same slot phase φ."""
    r_o = R_O * 1e-3

    def f(x, y):
        r = np.hypot(x, y)
        th = np.arctan2(y, x)
        br = B0 * r_o / r * np.cos(2 * th) * (1 + 0.2 * np.cos(4 * th + phi))
        return br * np.cos(th), br * np.sin(th)
    return f


def _phase_total(phi):
    """∝ the total radial pull of ``_phase_field(phi)`` (∫ B_r² dθ at r_o)."""
    th = np.linspace(0, 2 * math.pi, 40000, endpoint=False)
    return float(np.mean((np.cos(2 * th) * (1 + 0.2 * np.cos(4 * th + phi)))
                         ** 2))


def _frames_records(phis):
    """(snapshot-only record, record with frames) of the phase field; the
    snapshot is the LAST phase, as the solver stores the last time step."""
    base = _synthetic_em(R_O, R_O + GAP, _phase_field(phis[-1]), nr=4, nt=720)
    rec = _rec_of(base)
    air = np.nonzero(base.tags == 0)[0]
    cen = base.centroids[air]
    bb = [_phase_field(ph)(cen[:, 0], cen[:, 1]) for ph in phis]
    rec_f = dict(rec, pull_frames_elem=air,
                 pull_frames_bx=np.stack([b[0] for b in bb]),
                 pull_frames_by=np.stack([b[1] for b in bb]),
                 pull_frames_rotor_angle_deg=np.arange(len(phis), dtype=float),
                 meta={"eddy": False, "pull_frames": {"n": len(phis),
                                                      "stride": 1}})
    return rec, rec_f


def test_h_f1_one_instant_misses_the_slot_phase_the_frames_catch(ring):
    """F1: all four poles share one slot phase, so the stored instant's pole
    spread is nil and says nothing — at φ = π the instant carries 32.8 % less
    total pull than the worst phase.  The pre-fix code applied that instant
    and called it conservative.  Now: the one-instant record is labelled
    ``single-snapshot load (not a worst case)``; with the settled window's
    frames the load is a pointwise envelope, at least every frame's traction
    at every point, and the max-total frame (φ = 0) is found and reported.

    Measured 2026-10-26 (ring 8 x 192, 24 frames): snapshot / max-total
    frame 0.6721 (analytic 0.6721); the pointwise envelope carries 1.18x the
    max-total frame; the annulus check 0.9956 on every frame.
    """
    mesh, part = ring
    F = 24
    phis = math.pi + 2 * math.pi * (np.arange(F) + 1) / F   # last: φ = π
    rec_s, rec_f = _frames_records(phis)
    fs, ff = mpl.em_field_from_record(rec_s), mpl.em_field_from_record(rec_f)
    assert fs.n_frames == 0 and ff.n_frames == F
    cut = -math.pi / 4
    out = {}
    for name, fld in (("snap", fs), ("frames", ff)):
        spec = mpl.PullSpec("em", field=fld, label=name)
        _f, raw = mpl.pull_load(mesh, part, spec, order=2, period_n=4,
                                period_cut_rad=cut)
        tri = raw["_by_tri_n_per_m"].copy()
        out[name] = (mpl.finish_report(raw, spec, 0.05, 1, 4), tri)
    rs_, _ = out["snap"]
    rf, tri_f = out["frames"]
    # the one instant: labelled, never "conservative"
    assert rs_["load_basis"] == "single_snapshot" and rs_["worst_case"] is False
    assert "not a worst case" in rs_["instant"]
    assert "not a worst case" in mpl.summary_text(rs_)
    assert abs(rs_["envelope"]["pole_spread"][1]) < 1e-9     # nothing to see
    assert rf["load_basis"] == "frames_envelope" and rf["worst_case"] is True
    assert rf["envelope"]["n_frames"] == F
    # the counterexample itself
    miss = 1.0 - _phase_total(math.pi) / max(_phase_total(p) for p in phis)
    snap_tot = rs_["radial_force_total_n"]
    max_tot = rf["envelope"]["max_total"]["radial_force_total_n"]
    print(f"\nF1: snapshot {snap_tot:.1f} N, max-total frame {max_tot:.1f} N "
          f"(ratio {snap_tot / max_tot:.4f}, analytic {1 - miss:.4f}); "
          f"envelope {rf['radial_force_total_n']:.1f} N; check range "
          f"{rf['check']['ratio_range']}")
    assert miss > 0.30
    assert snap_tot / max_tot == pytest.approx(1.0 - miss, rel=0.01)
    assert math.cos(phis[rf["envelope"]["max_total"]["frame"]]) == \
        pytest.approx(1.0)
    # the envelope dominates the max-total frame and every frame, pointwise
    assert rf["radial_force_total_n"] >= max_tot * (1 - 1e-12)
    scale = float(np.abs(tri_f).max())
    for ph in phis[::5]:
        rec1, _ = _frames_records([ph])
        spec1 = mpl.PullSpec("em", field=mpl.em_field_from_record(rec1))
        _f, raw1 = mpl.pull_load(mesh, part, spec1, order=2, period_n=4,
                                 period_cut_rad=cut)
        # (to the float32 round-off of the stored record's centroids)
        assert np.all(tri_f >= raw1["_by_tri_n_per_m"] - 1e-6 * scale)
    assert rf["check"]["within_tol"] is True


def test_h_f1_frames_round_trip_through_the_duty_record():
    """The EM record keeps the per-frame air field (and the sleeve flag) the
    solver hands over, and the pull reads them back."""
    from motor_ai_sim import duty_fields as df
    p, t = _polar(40.0, 41.0, 2, 64)
    cen = p[:, t].mean(axis=1)
    bx, by = _radial_b(1.0, 0.04)(cen[0] * 1e-3, cen[1] * 1e-3)
    elem = np.arange(t.shape[1])
    fr = {"elem": elem, "bx": np.stack([bx, 1.1 * bx, 0.9 * bx]),
          "by": np.stack([by, 1.1 * by, 0.9 * by]),
          "rotor_angle_deg": np.array([0.0, 1.0, 2.0]),
          "time_s": np.array([0.0, 1e-4, 2e-4]), "stride": 2}
    entry = {"field": {"P_mm": p, "T": t, "tags": np.zeros(t.shape[1], int),
                       "Bx": bx, "By": by, "pull_frames": fr,
                       "sleeve_eddy_in_field": False},
             "scalars": {}, "meta": {"eddy": False}}
    packed = df.pack_em(entry)
    arr = packed["arrays"]
    assert arr["pull_frames_bx"].shape == (3, t.shape[1])
    assert packed["meta"]["pull_frames"]["n"] == 3
    assert packed["meta"]["pull_frames"]["stride"] == 2
    assert packed["meta"]["sleeve_eddy_in_field"] is False
    fld = mpl.em_field_from_record(dict(arr, meta=packed["meta"]))
    assert fld.n_frames == 3 and fld.frames_info["stride"] == 2
    np.testing.assert_allclose(fld.frames_bx[1], 1.1 * bx, rtol=1e-6, atol=1e-7)


def test_h_f1_the_solver_stores_the_settled_frames():
    """A real (small) P2 sliding-band transient: the snapshot carries the air
    around the rotor on every frame of the reported window, in the rotor
    frame — its last frame IS the stored instant — and says whether the
    sleeve carried induced current.

    Measured 2026-10-26 (30 mm fixture, 4 steps): 4 frames x 784 air
    elements (168 air + 616 gap), last frame = snapshot to 6e-8 T, 13 s."""
    from motor_ai_sim.material_context import set_request_materials
    from motor_ai_sim.simulation.fem_solver_2d import (
        DOM_AIR, DOM_AIRGAP, DOM_BAND, DOM_SLEEVE, fem_transient_sliding_band)

    from tests.test_physics_regression import (CONNECTION, GEO_30MM, OVERRIDE,
                                               RPM)
    run = dict(n_steps_per_period=4, n_periods=1.0, mesh_size_mm=1.4,
               min_size_mm=0.35, gap_layers=1.0, n_sectors=2,
               # gmsh mesh and a GIVEN d-axis: the netgen CDT mesher (and
               # the d-axis calibration that uses it) cannot load on a
               # Windows workstation under App Control; the operating point
               # does not matter to a storage test
               structured_gap=True, iron_template=True, geo_mesh=False,
               daxis_deg=90.0,
               coil_temp_c=120.0, element_order=2, demag=False, eddy=False,
               rotor_eddy=False, I_phase_rms=60.0, gamma_deg=0.0, rpm=RPM,
               connection=CONNECTION, return_field=True)
    set_request_materials(OVERRIDE)
    try:
        d = fem_transient_sliding_band(geo_override=dict(GEO_30MM), **run)
    finally:
        set_request_materials(None)
    fld = d["field"]
    pf = fld.get("pull_frames")
    assert pf is not None, "no per-frame air field with the snapshot"
    assert fld["sleeve_eddy_in_field"] is False
    nfr = pf["bx"].shape[0]
    print(f"\nsolver: {nfr} frames x {pf['elem'].size} air elements, "
          f"angles {np.round(pf['rotor_angle_deg'], 3).tolist()}")
    assert nfr == len(d["T_em_Nm"]) == 4
    tags = np.asarray(fld["tags"])[pf["elem"]]
    assert np.isin(tags, (DOM_AIR, DOM_AIRGAP, DOM_BAND, DOM_SLEEVE)).all()
    # the last frame is the snapshot's own instant, element for element
    np.testing.assert_allclose(pf["bx"][-1], np.asarray(fld["Bx"])[pf["elem"]],
                               rtol=1e-5, atol=1e-6)
    np.testing.assert_allclose(pf["by"][-1], np.asarray(fld["By"])[pf["elem"]],
                               rtol=1e-5, atol=1e-6)
    # the frames differ (the field moves with the currents)
    assert float(np.abs(pf["bx"][0] - pf["bx"][-1]).max()) > 1e-3


# -- F2 ----------------------------------------------------------------------

def _outer_rotor_polys(n=4, half_deg=15.0):
    """An OUTER rotor: an iron ring 41–55 mm with four inset arc magnets
    41–45 mm on its bore (iron behind and beside them), a stator 20–40 mm
    inside it."""
    from shapely.geometry import Point, Polygon
    from shapely.ops import unary_union

    def wedge(a0, a1, R=100.0):
        k = 16
        return Polygon([(0, 0)] + [(R * math.cos(a0 + (a1 - a0) * i / k),
                                    R * math.sin(a0 + (a1 - a0) * i / k))
                                   for i in range(k + 1)])
    ring = Point(0, 0).buffer(55.0, resolution=128).difference(
        Point(0, 0).buffer(41.0, resolution=128))
    band = Point(0, 0).buffer(45.0, resolution=128).difference(
        Point(0, 0).buffer(41.0, resolution=128))
    h = math.radians(half_deg)
    mags = [(band.intersection(wedge(2 * math.pi * k / n - h,
                                     2 * math.pi * k / n + h)),
             1 if k % 2 == 0 else -1) for k in range(n)]
    rotor = ring.difference(unary_union([m for m, _ in mags]))
    stator = Point(0, 0).buffer(40.0, resolution=96).difference(
        Point(0, 0).buffer(20.0, resolution=64))
    return {"rotor": rotor, "magnets": mags, "sleeve": None, "shaft": None,
            "sleeve_r_mm": (0.0, 0.0), "stator": stator}


def _outer_solve(rpm):
    return rs.solve_rotor_stress(
        _outer_rotor_polys(), SP_ASSIGN, rpm, 1.0, 0.0, stack_length_mm=50.0,
        mesh_size_mm=1.5, order=2, with_field=False,
        contacts={"magnet_rotor": ctc.ContactSpec("separation", 0.0)},
        lift_off_solves=0, case_mode="single", loads="centrifugal",
        symmetry="full", pull=mpl.uniform_spec(pull_mpa=0.5))


@pytest.fixture(scope="module")
def outer_rotor():
    return {rpm: _outer_solve(rpm) for rpm in (0.0, 4000.0, 8000.0)}


def test_h_f2_outer_rotor_inward_pull_at_standstill_is_not_retained(outer_rotor):
    """F2: an outer rotor's field pulls its bore magnets INWARD, off the
    iron.  At 0 rpm that is the whole load, and with separation contacts
    nothing holds them — the pre-fix code took ``fc + pull <= 1`` for
    standstill and passed it.

    Measured 2026-10-26 (mesh 1.5 mm): each magnet −10.73 kN/m of pull, held
    by 0.17 kN/m of side-wall shear — NOT RETAINED, the case fails."""
    out = outer_rotor[0.0]
    mr = _case(out)["magnet_retention"]
    assert out["pull_load"]["gap_side"] == "inner"
    assert mr["load_direction"] == "inward"
    assert mr["magnet_centrifugal_kn_per_m"] == 0.0
    assert mr["magnet_pull_kn_per_m"] < -1.0
    assert "standstill" not in mr["verdict"]
    assert "not retained" in mr["verdict"]
    assert out["retention"]["status"] == "fail"
    assert all(r["direction"] == "inward" for r in mr["by_magnet"])


def test_h_f2_across_the_centrifugal_magnetic_crossover(outer_rotor):
    """F2: the same rotor below and above the speed where spin overtakes
    the pull (~5,800 rpm here): below it the net load is still inward and
    the magnets are not retained; above it it is outward, the iron behind the
    magnets carries it, and the balance closes per magnet."""
    lo, hi = _case(outer_rotor[4000.0]), _case(outer_rotor[8000.0])
    m_lo, m_hi = lo["magnet_retention"], hi["magnet_retention"]
    print(f"\n4000 rpm: {m_lo['load_direction']}, {m_lo['verdict'][:60]}"
          f"\n8000 rpm: {m_hi['load_direction']}, {m_hi['verdict']}")
    assert m_lo["magnet_centrifugal_kn_per_m"] > 0.0      # the real spin kept
    assert m_lo["load_direction"] == "inward"
    assert "not retained" in m_lo["verdict"]
    assert outer_rotor[4000.0]["retention"]["status"] == "fail"
    assert m_hi["load_direction"] == "outward"
    assert m_hi["verdict"].startswith("pocket bottom / shoulder (iron)")
    assert outer_rotor[8000.0]["retention"]["status"] == "pass"
    for row in m_hi["by_magnet"]:
        assert abs(row["balance_residual_n_per_m"]) < 0.02 * row["load_n_per_m"]
        assert row["held_n_per_m"]["pocket_radial_faces"] == pytest.approx(
            row["load_n_per_m"], rel=0.02)


# -- F3 ----------------------------------------------------------------------

def test_h_f3_radial_flanks_carry_br_bt_and_cut_faces_carry_nothing():
    """F3: one pole (an annular sector 20–40 mm, ±15°) in a field with
    B_r = b1 and B_t = b2 sin θ / sin a.  On a radial flank the radial
    traction is T_rθ = B_r B_t / μ0 — here +b1 b2/μ0 on BOTH flanks — and the
    pre-fix 0.1 normal-projection cut dropped it (about half this pole's
    pull).  The same faces named as a sector model's CUT rays carry nothing
    and are reported as omitted."""
    from skfem import MeshTri
    a = math.radians(15.0)
    b1, b2 = 1.0, 0.5
    p, t = _polar(20e-3, 40e-3, 10, 30, th0=-a, th1=a)
    mesh = MeshTri(np.ascontiguousarray(p), np.ascontiguousarray(t))
    part = np.full(mesh.t.shape[1], ctc.PART_ROTOR, dtype=np.int8)

    def f(x, y):
        th = np.arctan2(y, x)
        br, bt = b1 * np.ones_like(th), b2 * np.sin(th) / math.sin(a)
        c, s = np.cos(th), np.sin(th)
        return br * c - bt * s, br * s + bt * c
    fld = _synthetic_em(15.0, 45.0, f, nr=60, nt=1440)
    spec = mpl.PullSpec("em", field=fld)
    _f, raw = mpl.pull_load(mesh, part, spec, order=2, cut_angles=())
    got = raw["_total_n_per_m"]
    rep = mpl.finish_report(raw, spec, 0.05, 1, 1)
    thq = np.linspace(-a, a, 20001)
    top = 40e-3 * np.trapezoid((b1 ** 2 - (b2 * np.sin(thq) / math.sin(a)) ** 2)
                           / (2 * MU0), thq)
    flanks = 2 * b1 * b2 / MU0 * 20e-3
    print(f"\nF3: total {got:.1f} N/m against top {top:.1f} + flanks "
          f"{flanks:.1f} = {top + flanks:.1f}; flanks_mm {rep['flanks_mm']:.2f}")
    assert got == pytest.approx(top + flanks, rel=0.02)
    assert rep["flanks_mm"] == pytest.approx(40.0, rel=1e-3)
    assert rep["omitted"]["cut_faces_mm"] == 0.0
    # the bore (inward-facing, rotor further out) is hidden, not loaded
    assert rep["omitted"]["hidden_mm"] == pytest.approx(
        30 * 2 * 20.0 * math.sin(a / 30), rel=1e-3)
    # the same two faces as a sector model's cut rays: not surfaces
    _f, raw_c = mpl.pull_load(mesh, part, spec, order=2, cut_angles=(-a, a))
    got_c = raw_c["_total_n_per_m"]
    rep_c = mpl.finish_report(raw_c, spec, 0.05, 1, 1)
    assert got_c == pytest.approx(top, rel=0.02)
    assert rep_c["omitted"]["cut_faces_mm"] == pytest.approx(40.0, rel=1e-6)
    # and a sector mesh names its own cut rays when nobody does
    assert set(np.round(mpl.mesh_sector_rays(mesh), 9)) == {
        round(-a, 9), round(a, 9)}


# -- F4 ----------------------------------------------------------------------

def _sleeved_record(meta):
    """Air gap 41–42 mm over a 1 mm sleeve (tag 11) on a 40 mm rotor."""
    fld = _synthetic_em(40.0, 42.0, _radial_b(B0, R_O * 1e-3), nr=8)
    rec = _rec_of(fld, meta)
    cen = fld.centroids
    rc = np.hypot(cen[:, 0], cen[:, 1])
    tags = np.asarray(rec["tags"]).copy()
    tags[(tags == 0) & (rc < 41e-3)] = mpl.SLEEVE_TAG
    rec["tags"] = tags
    return rec


@pytest.mark.parametrize("meta,ok", [
    ({"eddy": True, "sleeve_eddy_in_field": True}, False),
    ({"eddy": True}, False),                     # an eddy run that cannot say
    ({"eddy": True, "sleeve_eddy_in_field": False}, True),
    ({"eddy": False}, True),
])
def test_h_f4_a_sleeve_that_carried_current_is_not_air(ring, meta, ok):
    """F4: the sleeve has a conductivity and an eddy run solves its induced
    current INTO the field; the stress read in it is then not the pull on
    the rotor.  Such a field (or a sleeved eddy record that cannot say) is
    refused; one whose sleeve carried no current is read in the sleeve."""
    mesh, part = ring
    fld = mpl.em_field_from_record(_sleeved_record(meta))
    assert fld.has_sleeve
    spec = mpl.PullSpec("em", field=fld)
    if not ok:
        with pytest.raises(mpl.PullFieldError, match="sleeve"):
            fld.check_current_free()
        with pytest.raises(mpl.PullFieldError, match="sleeve"):
            mpl.pull_load(mesh, part, spec, order=2)
        assert mpl.SLEEVE_TAG not in fld.air_tags()
        return
    _f, raw = mpl.pull_load(mesh, part, spec, order=2)
    rep = mpl.finish_report(raw, spec, 0.05, 1, 8)
    assert rep["matched_fraction"] == pytest.approx(1.0)
    assert rep["pressure_mean_mpa"] == pytest.approx(
        B0 ** 2 / (2 * MU0) * 1e-6, rel=5e-3)
    assert rep["check"]["within_tol"] is True


# -- F5 ----------------------------------------------------------------------

def test_h_f5_a_magnet_the_field_misses_is_refused_by_name():
    """F5: a ring rotor with ONE small surface magnet (40–42 mm, ±2°) and a
    field record that cannot be read anywhere around that magnet.  The
    magnet is ~3 % of the loaded surface, inside the old 5 % WHOLE-surface
    tolerance, so the pre-fix code zeroed its pull without a word; per part
    it is 100 % unread and refused, by name."""
    from skfem import MeshTri
    h = math.radians(2.0)
    p1, t1 = _polar(20e-3, 40e-3, 4, 192)
    p2, t2 = _polar(40e-3, 42e-3, 4, 8, th0=-h, th1=h)
    p = np.concatenate([p1, p2], axis=1)
    t = np.concatenate([t1, t2 + p1.shape[1]], axis=1)
    mesh = MeshTri(np.ascontiguousarray(p), np.ascontiguousarray(t))
    part = np.concatenate([np.full(t1.shape[1], ctc.PART_ROTOR),
                           np.full(t2.shape[1], ctc.PART_MAGNET)]).astype(np.int8)
    base = _synthetic_em(40.0, 44.0, _radial_b(B0, R_O * 1e-3), nr=16, nt=1440)
    rec = _rec_of(base)
    cen = base.centroids
    th = np.arctan2(cen[:, 1], cen[:, 0])
    rc = np.hypot(cen[:, 0], cen[:, 1])
    tags = np.asarray(rec["tags"]).copy()
    tags[(np.abs(th) < math.radians(3.0)) & (rc < 42.5e-3)] = 100  # "magnet"
    rec["tags"] = tags
    fld = mpl.em_field_from_record(rec)
    # the old rule's view: the unread share of the WHOLE loaded surface
    mag_len = 42e-3 * 2 * h + 2 * 2e-3
    iron_unread = 40e-3 * 2 * math.radians(1.0)
    total = 40e-3 * (2 * math.pi - 2 * h) + mag_len
    assert (mag_len + iron_unread) / total < mpl.MAX_UNMATCHED_FRAC
    with pytest.raises(mpl.PullFieldError, match=r"100 % of magnet 1"):
        mpl.pull_load(mesh, part, mpl.PullSpec("em", field=fld), order=2)
    # the same rotor with the field readable everywhere loads the magnet
    _f, raw = mpl.pull_load(mesh, part, mpl.PullSpec("em", field=base), order=2)
    assert raw["_by_part_n_per_m"]["magnet"] > 0.0
    rep = mpl.finish_report(raw, mpl.PullSpec("em", field=base), 0.05, 1, 1)
    m1 = [v for k, v in rep["matched_by_part"].items() if k.startswith("magnet 1")]
    assert m1 == [pytest.approx(1.0)]


def test_h_retention_unit_signed_balance():
    """F2 at the function: the same magnet, inward pull, no spin, no contact
    force.  The pre-fix code answered 'not loaded (standstill)' (it tested
    ``fc + pull <= 1``); now both the aggregate call and the per-magnet one
    say 'not retained'.  With neither spin nor pull it IS standstill."""
    from types import SimpleNamespace
    from skfem import MeshTri
    h = math.radians(15.0)
    pm, tm = _polar(41e-3, 45e-3, 2, 6, th0=-h, th1=h)
    pr, tr_ = _polar(45e-3, 55e-3, 2, 96)
    p = np.concatenate([pm, pr], axis=1)
    t = np.concatenate([tm, tr_ + pm.shape[1]], axis=1)
    mesh = MeshTri(np.ascontiguousarray(p), np.ascontiguousarray(t))
    part = np.concatenate([np.full(tm.shape[1], ctc.PART_MAGNET),
                           np.full(tr_.shape[1], ctc.PART_ROTOR)]).astype(np.int8)
    # contact pairs on the magnet's outer arc (iron behind it)
    va = np.nonzero(np.abs(np.hypot(pm[0], pm[1]) - 45e-3) < 1e-9)[0]
    pos = pm[:, va].T
    er = pos / np.linalg.norm(pos, axis=1)[:, None]
    it = SimpleNamespace(label="magnet_rotor", n_pairs=va.size, n_facets=1)
    cs = SimpleNamespace(mesh=mesh, part_tri=part, interfaces=[it],
                         pair_n=-er, pair_pos=pos,
                         pair_iface=np.zeros(va.size, int), pair_va=va,
                         pair_vb=np.zeros(va.size, int))
    ne = mesh.t.shape[1]
    cen = mesh.p[:, mesh.t].mean(axis=1).T
    v0, v1, v2 = (mesh.p[:, mesh.t[i]] for i in range(3))
    area = 0.5 * np.abs((v1[0] - v0[0]) * (v2[1] - v0[1])
                        - (v2[0] - v0[0]) * (v1[1] - v0[1]))
    rho = np.where(part == ctc.PART_MAGNET, 7500.0, 7650.0)
    present = {"magnet": part == ctc.PART_MAGNET}
    mag = part == ctc.PART_MAGNET
    pull_tri = np.where(mag, -1e4 * area / area[mag].sum(), 0.0)   # −10 kN/m
    open_sol = SimpleNamespace(lam_n=np.zeros(va.size), force_t=np.zeros(va.size))
    forces = ctc.interface_forces(cs, open_sol)
    old = rs._retention(cs, open_sol, forces, present, area, rho, cen, 0.0,  # noqa: SLF001
                        pull_n_per_m=-1e4)
    assert "not retained" in old["verdict"]      # pre-fix: "standstill"
    new = rs._retention(cs, open_sol, forces, present, area, rho, cen, 0.0,  # noqa: SLF001
                        pull_n_per_m=-1e4, pull_per_tri=pull_tri)
    assert new["load_direction"] == "inward"
    assert "not retained" in new["verdict"]
    none = rs._retention(cs, open_sol, forces, present, area, rho, cen, 0.0,  # noqa: SLF001
                         pull_n_per_m=0.0, pull_per_tri=np.zeros(ne))
    assert none["verdict"] == "not loaded (standstill)"
    # spun past the pull and pressed onto the iron behind: retained by it
    w = 1200.0
    fc = float((area[mag] * 7500.0 * w ** 2 * np.hypot(cen[mag, 0],
                                                       cen[mag, 1])).sum())
    load = fc - 1e4
    assert load > 0
    held_sol = SimpleNamespace(lam_n=np.full(va.size, load / va.size),
                               force_t=np.zeros(va.size))
    held = rs._retention(cs, held_sol, ctc.interface_forces(cs, held_sol),
                         present, area, rho, cen, w, pull_n_per_m=-1e4,
                         pull_per_tri=pull_tri)
    assert held["load_direction"] == "outward"
    assert held["verdict"] == "pocket bottom / shoulder (iron)"
    assert held["by_magnet"][0]["balance_residual_n_per_m"] == pytest.approx(
        0.0, abs=1e-6 * load)
