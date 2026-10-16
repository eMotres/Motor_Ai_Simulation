"""Stage B blocker 3: a ROTATABLE rotor whose mesh topology never changes.

Torque by the energy method is ``dW'/dtheta``, and the whole difficulty is that
the two states being differenced must sit on the SAME mesh — a remesh between
rotor positions moves the energy by far more than the torque does.  The design
(``static3d/band.py``) is the 2D solver's sliding band ported to 3D: the
cross-section is cut at the mid-gap radius into two pieces that each carry the
same uniform ring, and rotation is a RE-LABELLING of which ring edge dof is
welded to which.

Three things can go wrong and all three are silent:

* the two pieces can end up with DIFFERENT triangulations of the interface
  cylinder (the prism split picks each quad face's diagonal from a node key, and
  a key that is not monotone along the ring flips it);
* the anti-periodic WRAP sign and the edge ORIENTATION sign can fail to compose,
  in which case a signed union-find quietly drops the contradictory constraint
  and the air gap is torn where the picture still looks fine;
* the weld can be approximately right — coupling nearby dofs rather than
  corresponding ones — which is a coarse gap coupling masquerading as a fine one.

So the proofs here are exact, not tolerant.  PROOF A welds at zero shift and
compares against a genuinely CONFORMAL mesh built by merging the two rings; the
two must agree to round-off, because at zero shift the weld is supposed to be
doing exactly what shared nodes would do for free.  PROOF A2 shifts by a whole
sector, where the machine maps onto itself anti-periodically and the co-energy
must come back bit-identical.  PROOF B sweeps the magnet-only co-energy over
several ring pitches and checks it is smooth: a mis-welded ring shows up as a
sawtooth at the relabelling period, which no amount of physics can produce.

The fast subset is a deliberately tiny mesh (seconds); the smoothness sweep and
anything on a usable cross-section are behind ``STATIC3D_FULL=1``.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

import numpy as np
import pytest

from motor_ai_sim.simulation.static3d import band
from motor_ai_sim.simulation.static3d import loaded as LD
from motor_ai_sim.simulation.static3d import torque3d
from motor_ai_sim.simulation.static3d.loaded import _regions_for
from motor_ai_sim.simulation.static3d.motor_geometry import (MM,
                                                             load_motor_section)
from motor_ai_sim.simulation.static3d.motor_mesh import build_motor_mesh
from motor_ai_sim.simulation.static3d.nedelec import (mirror_plane_dofs,
                                                      solve_static3d_A,
                                                      truncation_dofs)
from motor_ai_sim.simulation.static3d.periodic import edge_dof_pairs

FULL = os.environ.get("STATIC3D_FULL") == "1"
requires_full = pytest.mark.skipif(
    not FULL, reason="set STATIC3D_FULL=1 (minutes, big meshes)")

PRESET = "my_40mm_last"
STAGE_A_MATERIALS = {"stator_core": "B15AHV950M", "rotor_core": "B15AHV950M",
                     "magnet": "F45SH_120C", "shaft": "Aluminium_6061"}

# A deliberately tiny model: the proofs below are ALGEBRAIC (round-off
# tolerances on identities), so mesh quality buys them nothing and costs
# seconds.  n_ring = 84 makes the ring pitch 4.29 deg / 0.90 mm of arc, which
# h_gap = 1.1 mm is comfortably above — the gate in ``_ring_nodes`` refuses
# anything finer than the element size, for the good reason that gmsh would then
# subdivide the ring and the two pieces would stop sharing it.
N_RING = 84
H_GAP, H_SOLID, BOX = 1.1, 3.0, 1.6
N_STACK, N_CAP = 2, 2
# The ungauged (gauge='none') system is SINGULAR by its gradient null space, so
# the preconditioned CG's achievable relative residual has a FLOOR.  Measured on
# this model: 1e-10 converges in 18 iterations, 1e-11 does not converge at all
# and grinds to the 40000-iteration cap.  That is not a reason to gauge the
# system — the co-energy identities below come out at 1e-14 from a 1e-10 solve,
# because CG converges in the ENERGY norm and the energy is what is compared.
CG_TOL = 1e-10


def _preset(name: str = PRESET) -> dict:
    root = Path(__file__).resolve().parents[1]
    with (root / "config" / "motor_presets.json").open(encoding="utf-8") as fh:
        return dict(json.load(fh)[name])


@pytest.fixture(scope="module")
def section():
    from motor_ai_sim.config import get_config
    mats = (get_config() or {}).get("materials", {})
    for k, v in STAGE_A_MATERIALS.items():
        mats[k] = v
    return load_motor_section(geo_override=_preset()["geometry"])


@pytest.fixture(scope="module")
def banded(section):
    return band.build_banded_section(section, n_ring=N_RING, box_factor=BOX,
                                     h_gap=H_GAP, h_solid=H_SOLID)


@pytest.fixture(scope="module")
def model(section, banded):
    return torque3d.BandedModel(section, banded, n_stack=N_STACK, n_cap=N_CAP,
                                n_ew=0, h_ew_mm=None, linear_iron=True)


# --------------------------------------------------------------------------
# the three-piece cross-section
# --------------------------------------------------------------------------

def test_both_pieces_carry_the_same_uniform_ring(banded, section):
    """The ring is the interface, so it has to be EXACT on both sides.

    Not "close": the weld pairs ring index k with ring index k+m, and if the two
    rings were merely similar the pairing would be a nearest-neighbour guess
    that happens to work at zero shift."""
    p = banded.sect.p
    for ids in (banded.rring, banded.sring):
        r = np.hypot(p[0, ids], p[1, ids])
        th = np.arctan2(p[1, ids], p[0, ids])
        assert np.allclose(r, banded.r_mid_mm, atol=1e-9), float(np.ptp(r))
        want = np.arange(ids.size) * banded.pitch_rad
        assert np.allclose(th, want, atol=1e-12), float(np.max(np.abs(th - want)))
    assert np.allclose(p[:, banded.rring], p[:, banded.sring], atol=1e-12)
    assert banded.n_sec_ring == N_RING // section.n_sectors + 1


def test_the_cross_section_is_torn_at_the_mid_gap_radius(banded):
    """The two pieces must share NO node.  A single shared node would make the
    rotor and the stator rigid with respect to each other at that point."""
    t = banded.sect.t
    nr = banded.n_rotor_nodes
    side = (t >= nr)
    assert np.all(side.min(axis=0) == side.max(axis=0)), \
        "a triangle spans both pieces — the section is not torn"
    assert set(banded.rring.tolist()).isdisjoint(banded.sring.tolist())


def test_the_ring_split_key_is_monotone_on_both_rings(banded):
    """The prism split picks each quad face's diagonal from the node key, so the
    interface triangulations correspond only if the key rises with the ring
    index on BOTH pieces.  A key that borrowed the cut-plane trick here (slave
    takes its master's key) would flip exactly one face per ring and the two
    surfaces would differ by two triangles nobody would ever look at."""
    key = np.asarray(banded.sect.meta["rot_key"], dtype=np.int64)
    for ids in (banded.rring, banded.sring):
        assert np.all(np.diff(key[ids]) > 0)
    other = np.setdiff1d(np.arange(key.size),
                         np.concatenate([banded.rring, banded.sring]))
    assert key[banded.rring].min() > key[other].max(), \
        "a ring key is not above every other key — the cut-plane diagonals no " \
        "longer correspond between the two cut planes"


# --------------------------------------------------------------------------
# the weld
# --------------------------------------------------------------------------

def test_every_interface_edge_family_exists_in_the_mesh(model):
    """Axial, angular AND diagonal.  ``SlipWeld`` raises if a diagonal it
    expects is missing, which is the direct test that the split rule and the key
    agree — so this asserts the counts it found rather than merely that it ran."""
    w = model.weld
    Nr, nz = w.Nring, w.nz
    assert w.meta["n_axial"] == Nr * (nz - 1)
    assert w.meta["n_angular"] == (Nr - 1) * nz
    assert w.meta["n_diagonal"] == (Nr - 1) * (nz - 1)
    assert w.meta["n_cut_pairs"] > 0


@pytest.mark.parametrize("m", [0, 1, 3, 7])
def test_every_weld_constraint_is_consistent_at_every_shift(model, m):
    """A signed union-find DROPS a union whose two dofs are already related with
    a different sign.  ``SlipWeld.build`` refuses to instead, because a dropped
    weld is a torn air gap that still solves and still looks plausible."""
    P, Dcols = model.projection(m)
    assert P.shape[0] == model.basis.N
    assert np.all(np.abs(P.data) == 1.0)
    assert np.array_equal(np.diff(P.indptr), np.ones(P.shape[0], dtype=int)), \
        "a dof belongs to more than one weld class"


def test_the_number_of_free_classes_does_not_depend_on_the_rotor_angle(model):
    """Rotation is a relabelling: the same constraints, applied to different
    pairs.  A shift that changed the class count would mean the wrap has created
    or destroyed a constraint, which is the signature of an off-by-one."""
    n = {m: model.projection(m)[0].shape[1] for m in (0, 1, 2, 5, 11)}
    assert len(set(n.values())) == 1, n


def test_the_mesh_never_moves_when_the_rotor_turns(model):
    """The point of the whole construction, asserted rather than assumed: the
    element geometry is bit-identical at every legal angle, so no part of a
    torque difference can come from a moved node."""
    p0 = model.tm.mesh.p.copy()
    t0 = model.tm.mesh.t.copy()
    for m in (0, 1, 5, 13):
        model.projection(m)
    assert np.array_equal(p0, model.tm.mesh.p)
    assert np.array_equal(t0, model.tm.mesh.t)


def test_the_ring_map_is_the_physical_rotation(model, banded, section):
    """rotor node k, turned by m pitches, must land ON stator node k+m — the
    same statement in angles that the weld makes in indices."""
    p = banded.sect.p
    ang_r = np.degrees(np.arctan2(p[1, banded.rring], p[0, banded.rring]))
    ang_s = np.degrees(np.arctan2(p[1, banded.sring], p[0, banded.sring]))
    for m in (0, 1, 4, model.weld.n_cell - 1):
        j, sg = model.weld.ring_map(m)
        want = np.mod(ang_r + m * banded.pitch_deg, section.sector_deg)
        err = np.abs(np.mod(want - ang_s[j] + 180.0, 360.0) - 180.0)
        assert float(err.max()) < 1e-9, (m, float(err.max()))
        # a node that has been pushed past the cut carries bc_sign
        wrapped = (np.arange(banded.n_sec_ring) + m) >= model.weld.n_cell
        assert np.allclose(sg[wrapped], banded.bc_sign)
        assert np.allclose(sg[~wrapped], 1.0)


def test_a_wrap_sign_error_is_caught_rather_than_solved(model, monkeypatch):
    """The guard itself has to be tested, or it is decoration.

    Note what does NOT trip it: flipping every wrap sign at once is a perfectly
    consistent (if different) constraint set, and so is flipping a whole family.
    What has to be caught is a sign that disagrees with the loop it sits in, and
    on this model there is exactly such a loop — the axial edge at the ring's
    first node and the one at its last node both weld to the SAME stator edge,
    and the rotor's own anti-periodic cut already relates them.  One corrupted
    entry closes that loop with the wrong sign, and the union-find has to say so
    rather than drop the constraint."""
    good = model.weld.ring_map

    def broken(m):
        j, s = good(m)
        s = s.copy()
        s[0] = -s[0]
        return j, s

    monkeypatch.setattr(model.weld, "ring_map", broken)
    with pytest.raises(RuntimeError, match="CONTRADICT"):
        model.weld.build(1, model.dirichlet)


# --------------------------------------------------------------------------
# PROOF A — the weld is exact
# --------------------------------------------------------------------------

def _solve_merged(section, banded, n_stack, n_cap, cg_tol=CG_TOL):
    """The same cross-section with the two rings IDENTIFIED — one conformal mesh
    with no weld at all, solved through Stage A's own periodic path."""
    from skfem import Basis
    from skfem.element import ElementTetN0

    ms = band.merged_section(banded)
    tm, _ = build_motor_mesh(section, sect=ms, n_stack=n_stack, n_cap=n_cap,
                             h_ew_mm=None, n_ew=0)
    b = Basis(tm.mesh, ElementTetN0())
    regs = _regions_for(tm, section, linear_iron=True)
    per = edge_dof_pairs(b, math.radians(section.sector_deg), -1.0)
    D = np.unique(np.concatenate(
        [mirror_plane_dofs(b, 0.0),
         truncation_dofs(b, ms.r_box_mm * MM, tm.meta["z_box_mm"] * MM)]))
    sol = solve_static3d_A(tm.mesh, regs, basis=b, periodic=per,
                           dirichlet_dofs=D, gauge="none", cg_tol=cg_tol)
    return torque3d.co_energy(sol, regs, section.n_sectors), tm, sol


def test_proof_a_the_weld_at_zero_shift_IS_a_conformal_mesh(section, banded,
                                                            model):
    """PROOF A.  Merging the two rings gives a genuinely conformal mesh of the
    same machine; welding them at zero shift must produce the same discrete
    problem, and therefore the same co-energy to round-off.

    This is the test that separates a weld from a coupling.  An interface whose
    two sides carried different diagonals, or a missing edge family, or an
    orientation sign taken from the wrong end, all still converge — and all
    still move this number in the third digit."""
    W_merged, tm, _ = _solve_merged(section, banded, N_STACK, N_CAP)
    assert tm.mesh.t.shape[1] == model.tm.mesh.t.shape[1], \
        "merging changed the element count — the split key is not merge-stable"
    ls = model.solve(0, cg_tol=CG_TOL)
    W_weld = model.co_energy(ls)
    rel = abs(W_weld - W_merged) / abs(W_merged)
    assert rel < 1e-11, (W_weld, W_merged, rel)


def test_proof_a2_a_whole_sector_shift_returns_the_co_energy(model):
    """PROOF A2.  Shifting by the whole sector maps this machine onto itself
    ANTI-periodically (7 poles per 180 deg is odd), so with the magnets alone the
    field is exactly the negative of itself on the stator side and the co-energy
    must return bit-identically.

    It is the wrap sign that this checks, and it checks it where nothing else
    can: every constraint in the model has been multiplied by ``bc_sign`` exactly
    once, so a wrap sign that was right by accident at small shifts fails here."""
    W0 = model.co_energy(model.solve(0, cg_tol=CG_TOL))
    Wf = model.co_energy(model.solve(model.weld.n_cell, cg_tol=CG_TOL))
    assert abs(Wf - W0) / abs(W0) < 1e-11, (W0, Wf)


def test_the_two_co_energy_routes_agree_on_a_linear_machine(model):
    """``1/2 A^T f`` and the volume integral of the co-energy density are the
    same number on a linear machine, and they are computed from completely
    different things (the load vector vs the B-H reading of every element).  On
    the nonlinear machine only the second one is valid, so this is what pins it
    down."""
    ls = model.solve(0, cg_tol=CG_TOL)
    a = model.co_energy(ls)
    b = model.co_energy_linear(ls)
    assert abs(a - b) / abs(a) < 1e-9, (a, b)


# --------------------------------------------------------------------------
# WHICH functional is the torque — the identity that settles it
# --------------------------------------------------------------------------

#: the Stage B operating point (``config/end_effect_3d.json``
#: ``stage_b.operating_point.I_ph``).  Any balanced set would do for the
#: identities below; this one is used so a failure is read against the numbers
#: the passport quotes.
I_OP = {"A": 21.204323896810546, "B": 39.85358585161611,
        "C": -61.05790974842665}
#: a COARSE Psi grid — the identities are algebraic, so what matters is that the
#: solve's source field and the unit windings the linkages are read with come
#: off the SAME grid, not how finely that grid resolves the copper's edges.
PSI_GRID = dict(n_r=61, n_theta=1801)
#: ``cg`` is called with ``rtol``, so the ungauged system's achievable residual
#: floor moves with the LOAD — and a magnets-off load is the winding's alone,
#: some forty times smaller than the magnet's.  ``CG_TOL`` = 1e-10 then sits
#: UNDER that floor and grinds to the 40000-iteration cap: measured on this
#: 63596-dof model, >20 minutes against 8 s at 1e-8.  1e-8 is not a weaker check
#: of the identities below.  CG converges in the ENERGY norm and the energy is
#: what is compared, so the identity comes out at 1.4e-11 at every tolerance
#: from 1e-6 to 1e-8 (13, 14 and 15 iterations) — it is the residual that is
#: unreachable, not the energy that is inaccurate.
CG_TOL_LOADED = 1e-8


def _loaded_model(section, banded, magnets_off: bool):
    """The tiny model with a winding, plus the unit windings that read it."""
    from motor_ai_sim.simulation.static3d.winding3d import build_winding_T

    h_ew = (0.5 * float(section.geo["tooth_width"])
            + 0.5 * float(section.geo["wire_width"]))
    kw = dict(h_ew_mm=h_ew, **PSI_GRID)
    wt = build_winding_T(section, dict(I_OP), **kw)
    units = {ph: build_winding_T(section,
                                 {p: (1.0 if p == ph else 0.0)
                                  for p in LD.PHASES}, **kw)
             for ph in LD.PHASES}
    mdl = torque3d.BandedModel(section, banded, n_stack=N_STACK, n_cap=N_CAP,
                               n_ew=2, h_ew_mm=h_ew, I_ph=dict(I_OP),
                               winding_field=wt, linear_iron=True,
                               magnets_off=magnets_off)
    return mdl, units


@pytest.fixture(scope="module")
def loaded_model_magnets_off(section, banded):
    return _loaded_model(section, banded, magnets_off=True)


@pytest.fixture(scope="module")
def loaded_model_magnets_on(section, banded):
    return _loaded_model(section, banded, magnets_off=False)


def _sweep(mdl, units, shifts=(-1, 0, 1)):
    """W', the winding's own half-linkage energy, and the magnet term."""
    W, W_T, W_Hc = [], [], []
    for m in shifts:
        ls = mdl.solve(m, cg_tol=CG_TOL_LOADED)
        W.append(mdl.co_energy(ls))
        psi = LD.flux_linkage(ls, units)
        W_T.append(0.5 * sum(I_OP[p] * psi[p] for p in LD.PHASES))
        W_Hc.append(torque3d.co_energy_from_load(ls.sol, mdl.section.n_sectors,
                                                 T_field=None))
    return np.array(W), np.array(W_T), np.array(W_Hc)


def test_with_the_magnets_off_the_two_torque_functionals_are_ONE_number(
        loaded_model_magnets_off):
    """The measurement that settles which torque functional is which.

    With no magnets and linear iron the whole co-energy is the winding's own,
    ``W' = 1/2 i^T L i = 1/2 sum_ph i_ph psi_ph``, so

        dW'/dtheta   and   1/2 sum_ph i_ph dpsi_ph/dtheta

    are the SAME number computed from the SAME solves.  Any difference here is
    an implementation error in one of the two — in ``torque3d.co_energy`` (the
    B-H reading of every element) or in ``loaded.flux_linkage`` (the
    ``integral(T_1 . B)`` pairing) — and not a modelling question.  The
    pointwise form is asserted first because it is the stronger statement: the
    derivative is a difference of two numbers that are already equal.

    What this test does NOT say is that the two agree on a machine WITH magnets.
    They do not, they must not, and the test below measures by how much."""
    mdl, units = loaded_model_magnets_off
    W, W_T, W_Hc = _sweep(mdl, units)
    assert np.allclose(W_Hc, 0.0, atol=1e-14 * abs(W).max()), W_Hc.tolist()
    rel = np.abs(W - W_T) / np.abs(W)
    assert rel.max() < 1e-9, (W.tolist(), W_T.tolist(), rel.tolist())
    dth = 2.0 * mdl.pitch_rad
    T_energy = (W[2] - W[0]) / dth
    T_winding = (W_T[2] - W_T[0]) / dth
    # the derivative divides a difference of ~1e-5 J by the step, so a 1e-9
    # relative agreement on W becomes ~1e-4 on T — stated rather than hidden
    assert abs(T_energy - T_winding) <= 1e-3 * abs(T_energy), (T_energy,
                                                               T_winding)


def test_with_the_magnets_ON_the_winding_functional_is_blind_to_the_magnet_term(
        loaded_model_magnets_on):
    """And the blind spot is a NAMED, exact quantity, not a discrepancy.

    The weak form gives ``W' = 1/2 integral((Hc + T) . B)`` exactly, so

        W'  =  1/2 integral(Hc . B)  +  1/2 sum_ph i_ph psi_ph
            =  [ W'_magnet_alone + 1/2 sum_ph i_ph psi_pm,ph ]  +  W_T

    (the bracket splits by reciprocity).  The winding functional sees ``W_T``
    and nothing else, so as a TORQUE it misses the magnet's own co-energy and
    keeps only HALF of the PM alignment term.  This asserts the decomposition
    to round-off and then asserts that the missing piece MOVES — a magnet term
    that happened to be constant in theta would make the omission harmless, and
    on this machine it is not."""
    mdl, units = loaded_model_magnets_on
    W, W_T, W_Hc = _sweep(mdl, units)
    rel = np.abs(W - (W_Hc + W_T)) / np.abs(W)
    assert rel.max() < 1e-9, (W.tolist(), W_Hc.tolist(), W_T.tolist(),
                              rel.tolist())
    dth = 2.0 * mdl.pitch_rad
    T_energy = (W[2] - W[0]) / dth
    T_winding = (W_T[2] - W_T[0]) / dth
    T_magnet = (W_Hc[2] - W_Hc[0]) / dth
    assert T_energy == pytest.approx(T_winding + T_magnet, rel=1e-6)
    # the omitted term is not a rounding detail: it is the same size as the
    # torque itself or larger, and on the real cross-section it carries the
    # opposite sign to the winding term
    assert abs(T_magnet) > 0.1 * abs(T_energy), (T_energy, T_magnet)


# --------------------------------------------------------------------------
# the co-energy of a saturable material
# --------------------------------------------------------------------------

def test_the_curve_co_energy_reduces_to_half_nu_B_squared_on_a_linear_curve():
    """``|H||B| - integral_0^{|B|} H dB'`` must give back ``1/2 nu |B|^2`` when
    the curve IS linear — including above the last sample, where the curve
    continues at the differential mu0 slope.

    The tail is not a corner case here: the saturated rotor bridges of this
    machine sit on it, and it is where the first version of this function
    silently broadcast the wrong shape."""
    from motor_ai_sim.simulation.static3d.solver import MU0
    mu = MU0 * 1000.0
    bs = np.linspace(0.0, 2.0, 21)[1:]
    curve = [(float(b / mu), float(b)) for b in bs]
    B = np.array([0.0, 0.1, 1.0, 1.999, 2.0, 2.5, 5.0])
    got = torque3d._curve_coenergy(curve, B)
    # inside the sampled range the material is exactly linear at mu
    inside = B <= 2.0
    assert np.allclose(got[inside], 0.5 * B[inside] ** 2 / mu, rtol=1e-12)
    # above it the slope is mu0, so the co-energy is the linear part plus the
    # mu0 continuation of the SAME construction
    dB = B[~inside] - 2.0
    h_last = 2.0 / mu
    H_tail = h_last + dB / MU0
    E_tail = 0.5 * 4.0 / mu + h_last * dB + 0.5 * dB * dB / MU0
    want = H_tail * (2.0 + dB) - E_tail
    assert np.allclose(got[~inside], want, rtol=1e-12), (got[~inside], want)
    assert np.all(np.diff(torque3d._curve_coenergy(
        curve, np.linspace(0.0, 6.0, 200))) > 0)


def test_the_curve_co_energy_is_below_the_linear_one_on_a_saturating_curve():
    """A real curve saturates, so at a given B its H is HIGHER than a linear
    extrapolation of the initial permeability would give — and the co-energy is
    correspondingly larger than ``1/2 nu_initial |B|^2``.  Getting this backwards
    (using the frozen Picard nu instead of the curve) is the mistake this
    function exists to avoid, and on a saturated bridge it is tens of percent."""
    curve = [(50.0, 0.5), (200.0, 1.0), (2000.0, 1.5), (30000.0, 1.8)]  # noqa
    B = np.array([1.5])
    got = float(torque3d._curve_coenergy(curve, B)[0])
    nu_secant = 2000.0 / 1.5            # H/B at that point
    assert got > 0.5 * nu_secant * 1.5 ** 2 * 0.5
    assert got < 2000.0 * 1.5           # cannot exceed |H||B|


# --------------------------------------------------------------------------
# PROOF B — the sweep is smooth
# --------------------------------------------------------------------------

@requires_full
def test_proof_b_the_loaded_sweep_is_smooth_and_agrees_with_the_flux_linkage(
        section):
    """PROOF B.  A co-energy sweep over several ring pitches must be SMOOTH, and
    the torque it gives must be the torque another method gives.

    A mis-welded ring does not produce a small error, it produces a PERIODIC one
    — the weld is the only thing that changes between shifts, so whatever it
    gets wrong repeats every pitch.  The sharpest form of that is an alternation
    between neighbouring shifts: energy at the Nyquist frequency of the shift
    index, where no machine can put physics.

    The sweep is done UNDER LOAD (linear iron, so it is cheap) rather than
    magnet-only, and the reason is measured rather than aesthetic: with linear
    iron the unsaturated rotor bridges short most of the magnet flux, so the
    magnet-only cogging of this cross-section is ~1e-5 N.m and sits under the
    band's own discretisation floor — see the next test, which measures that
    floor instead of pretending to resolve a signal below it.  Under load the
    armature reaction gives a torque three orders of magnitude larger, and the
    flux-linkage route (``dq_torque``, the 2D solver's own formula) provides an
    INDEPENDENT number to check the co-energy derivative against."""
    bs = band.build_banded_section(section, n_ring=336, box_factor=2.0,
                                   h_gap=0.5, h_solid=2.2)
    from motor_ai_sim.simulation.static3d import loaded as LD
    I = {"A": 21.204323896810546, "B": 39.85358585161611,
         "C": -61.05790974842665}
    mdl = torque3d.BandedModel(section, bs, n_stack=3, n_cap=3, n_ew=2,
                               I_ph=I, linear_iron=True)
    units = LD.unit_phase_windings(section, h_ew_mm=mdl.h_ew_mm)
    shifts = list(range(-3, 4))
    W, Tdq = [], []
    for m in shifts:
        ls = mdl.solve(m, cg_tol=CG_TOL)
        W.append(mdl.co_energy(ls))
        Tdq.append(torque3d.dq_torque(LD.flux_linkage(ls, units), I,
                                      section.pole_pairs, 1))
    W = np.array(W)
    # the energy method against the flux-linkage method, same solves
    T_energy = (W[shifts.index(1)] - W[shifts.index(-1)]) / (2 * mdl.pitch_rad)
    T_flux = float(np.mean(Tdq))
    assert abs(T_energy - T_flux) / abs(T_flux) < 0.05, (T_energy, T_flux)
    # and no sawtooth at the relabelling period
    sp = np.abs(np.fft.rfft(W - W.mean()))
    assert sp[-1] < 0.05 * sp[1:].max(), (sp / sp[1:].max()).tolist()


@requires_full
def test_proof_b2_the_band_reports_its_own_torque_noise_floor(section):
    """How small a torque this construction can resolve, MEASURED.

    With the magnets alone and linear iron the physical cogging of this
    cross-section is tiny, so what a sweep sees is almost entirely the band's own
    discretisation bias: the rotor's dofs weld to different parts of an
    unstructured stator gap mesh at every shift.  That bias is the floor under
    every torque this module will ever report, and it is worth having as a
    number rather than as a worry.

    Two things are asserted.  The floor must be small in absolute terms — orders
    below the loaded torque, or the whole method is useless — and it must not be
    a SAWTOOTH: a bias that alternated shift by shift would be a weld error
    rather than a mesh one."""
    bs = band.build_banded_section(section, n_ring=336, box_factor=2.0,
                                   h_gap=0.5, h_solid=2.2)
    mdl = torque3d.BandedModel(section, bs, n_stack=3, n_cap=3, n_ew=0,
                               h_ew_mm=None, linear_iron=True)
    W = np.array([mdl.co_energy(mdl.solve(m, cg_tol=CG_TOL))
                  for m in range(12)])
    floor = float(np.max(np.abs(np.diff(W))) / mdl.pitch_rad)
    assert floor < 1e-3, floor          # against a loaded torque of ~0.6 N.m
    sp = np.abs(np.fft.rfft(W - W.mean()))
    assert sp[-1] < 0.5 * sp[1:].max(), (sp / sp[1:].max()).tolist()


@requires_full
def test_a_loaded_banded_solve_is_consistent_and_anti_periodic(section):
    """The real thing on a coarse mesh: the winding's load must still be
    consistent to round-off on a TORN cross-section, and the solved field must
    still be anti-periodic across both pieces' cut planes."""
    bs = band.build_banded_section(section, n_ring=168, box_factor=2.0,
                                   h_gap=0.9, h_solid=2.2)
    mdl = torque3d.BandedModel(section, bs, n_stack=3, n_cap=3, n_ew=2,
                               I_ph={"A": 40.0, "B": -25.0, "C": -15.0},
                               linear_iron=True)
    ls = mdl.solve(0)
    assert ls.sol.source_residual < 1e-11, ls.sol.source_residual
    assert ls.sol.source_removed_norm == 0.0
    mm, ss, sg = band._blocked_edge_cut_pairs(
        mdl.basis, bs.sector_rad, bs.bc_sign,
        block_of_node=(np.arange(mdl.tm.mesh.p.shape[1])
                       % mdl.tm.meta["n_nodes_2d"]) >= bs.n_rotor_nodes)
    A = ls.sol.A
    scale = float(np.max(np.abs(A)))
    err = float(np.max(np.abs(A[ss] - sg * A[mm])))
    assert err < 1e-9 * scale, err / scale
