"""Stage B: a CLOSED winding and ANISOTROPIC iron in the A-formulation.

Two things had to be true before a loaded 3D solve of this machine could mean
anything, and this file is what makes them measurable rather than asserted.

1. THE LOAD HAS TO BE CONSISTENT.  A straight extruded J_z is not a current —
   it is charge piling up at the end of the stack — and the A-formulation does
   not complain: the load simply acquires a component outside the range of the
   curl-curl operator and the Helmholtz projection quietly removes it.  Measured
   here on the project's own coil benchmark: a J source is 9.6e-3 inconsistent
   and the projection deletes 0.39 % of the load vector.  The same physics
   assembled as a SOURCE FIELD ``integral(T . curl v)`` is consistent to 3e-16
   and the projection removes exactly zero, because curl(grad psi) vanishes
   identically in the Nedelec space.  That is not a smaller error; it is a
   different kind of statement, and it is why ``winding3d`` exists.

2. THE IRON IS A LAMINATION STACK.  ``nedelec`` used to REFUSE a laminated
   region rather than solve it as solid — the honest thing while its weak form
   carried a scalar nu.  It now carries the diagonal, and the tests below pin
   both ends: k_f = 1 must reproduce the isotropic assembly bit for bit, and
   k_f < 1 must raise the in-plane flux and block the axial one, which is the
   signature that distinguishes the correct tensor from the transposed one.

Everything in the default run is small and inside two minutes; the
cross-formulation comparison and the real cross-section are behind
``STATIC3D_FULL=1``.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

import numpy as np
import pytest

from motor_ai_sim.simulation.static3d import exact
from motor_ai_sim.simulation.static3d.meshes import (sphere_with_iron_shell,
                                                     tube_in_box)
from motor_ai_sim.simulation.static3d.motor_geometry import load_motor_section
from motor_ai_sim.simulation.static3d.motor_mesh import axial_levels
from motor_ai_sim.simulation.static3d.nedelec import (axial_nu, azimuthal_J,
                                                      kf_array,
                                                      solve_static3d_A)
from motor_ai_sim.simulation.static3d.solver import (MU0, Region, axial_mu,
                                                     solve_static3d)
from motor_ai_sim.simulation.static3d.winding3d import (build_winding_T,
                                                        solenoid_T)

FULL = os.environ.get("STATIC3D_FULL") == "1"
requires_full = pytest.mark.skipif(
    not FULL, reason="set STATIC3D_FULL=1 (minutes, big meshes)")

PRESET = "my_40mm_last"
# The magnet the Stage A passport was measured with.  config/motor_config.yaml
# is live — it has since been reassigned to F52SH — and a Stage B number that
# silently changed magnet grade would not be comparable with Stage A at all.
STAGE_A_MATERIALS = {"stator_core": "B15AHV950M", "rotor_core": "B15AHV950M",
                     "magnet": "F45SH_120C", "shaft": "Aluminium_6061"}

K_COIL = 1.19 / MU0
R_I, R_O, L_SOL = 0.005, 0.006, 0.02
J_COIL = K_COIL / (R_O - R_I)


def _preset(name: str = PRESET) -> dict:
    root = Path(__file__).resolve().parents[1]
    with (root / "config" / "motor_presets.json").open(encoding="utf-8") as fh:
        return dict(json.load(fh)[name])


@pytest.fixture(scope="module")
def coil_mesh():
    """A coarse thick solenoid — the same geometry ``benchmarks.nedelec_coil_case``
    uses, meshed coarsely enough to solve in seconds."""
    return tube_in_box(r_inner=R_I, r_outer=R_O, length=L_SOL, box_factor=2.5,
                       h_tube=0.0030)


@pytest.fixture(scope="module")
def coil_regions(coil_mesh):
    return [Region("air", coil_mesh.elements("air")),
            Region("coil", coil_mesh.elements("coil"))]


# --------------------------------------------------------------------------
# blocker 1 — the load has to be consistent
# --------------------------------------------------------------------------

def test_a_T_source_load_is_consistent_to_round_off(coil_mesh, coil_regions):
    """``integral(T . curl v)`` is orthogonal to every discrete gradient BY
    CONSTRUCTION, so the projection has nothing to remove.

    This is the whole reason the winding is modelled as T and not as J: not
    because the residual is smaller, but because "nothing was absorbed" is a
    statement that can be checked, and here it comes out exact."""
    sol = solve_static3d_A(coil_mesh.mesh, coil_regions, gauge="tree",
                           T=solenoid_T(J_COIL, R_I, R_O, L_SOL))
    assert sol.load_norm > 0.0
    assert sol.source_residual < 1e-12, sol.source_residual
    # not "small": zero.  The projection was offered the load and declined it.
    assert sol.source_removed_norm == 0.0


def test_a_J_source_is_inconsistent_and_the_projection_eats_a_real_piece(
        coil_mesh, coil_regions):
    """The contrast, measured on the same mesh and the same physics.

    An azimuthal J is divergence-free POINTWISE and still lands off the range,
    because quadrature on a curved e_phi is not exact.  The number that matters
    is not the residual but how much of the load the projection then deletes:
    if a straight extruded slot current were used instead, that deletion is
    where the missing end turns would go, silently."""
    sol = solve_static3d_A(coil_mesh.mesh, coil_regions, gauge="tree",
                           J=azimuthal_J(coil_mesh.elements("coil"),
                                         coil_mesh.n_elements, J_COIL))
    assert sol.source_residual > 1e-4, sol.source_residual
    assert sol.source_removed_norm > 0.0
    frac = sol.source_removed_norm / sol.load_norm
    assert 1e-4 < frac < 1e-1, frac


def test_the_T_source_reproduces_the_exact_thick_solenoid(coil_mesh,
                                                          coil_regions):
    """curl(T) really is the current: on-axis B_z against
    ``exact.thick_solenoid_axis_Bz``.

    A source that is beautifully consistent and wrong would pass every test
    above.  The tolerance is loose because this is the LOWEST-order edge element
    on a deliberately coarse mesh — the finer-mesh numbers are in the Stage B
    record — but 'within a few percent of a closed form' is the difference
    between a winding model and a hypothesis."""
    sol = solve_static3d_A(coil_mesh.mesh, coil_regions, gauge="tree",
                           T=solenoid_T(J_COIL, R_I, R_O, L_SOL))
    th = np.linspace(0.0, 2 * math.pi, 16, endpoint=False)
    rp = 0.15 * R_I
    pts = np.vstack([rp * np.cos(th), rp * np.sin(th), np.zeros_like(th)])
    Bz = float(sol.B_at_points(pts)[2].mean())
    ex = float(exact.thick_solenoid_axis_Bz(np.array([0.0]), J_COIL, R_I, R_O,
                                            L_SOL)[0])
    assert abs(Bz - ex) / abs(ex) < 0.06, (Bz, ex)


def test_flux_linkage_from_T_equals_the_stored_energy_identity(coil_mesh,
                                                               coil_regions):
    """``integral(T . B) == A^T f == integral(B . H)`` on a LINEAR problem.

    Not a physical check but an ALGEBRAIC one, and that is exactly its value:
    ``loaded.flux_linkage`` computes lambda as ``integral(T_1 . B)``, and this
    pins that it is the same discrete pairing the load was assembled with — so
    the flux linkage cannot drift away from the energy the solver actually
    stored.  Any quadrature or sign slip in the linkage integral breaks it."""
    T = solenoid_T(J_COIL, R_I, R_O, L_SOL)
    sol = solve_static3d_A(coil_mesh.mesh, coil_regions, gauge="tree", T=T)
    basis = sol.basis
    Bq = basis.interpolate(sol.A).curl
    Tq = T(np.asarray(basis.global_coordinates()))
    lam = float(((Tq * Bq).sum(axis=0) * basis.dx).sum())
    # integral(B . H) with H = nu . B (isotropic here)
    BH = float(((sol.nu_el[:, None] * (Bq ** 2).sum(axis=0)) * basis.dx).sum())
    assert lam > 0.0
    assert abs(lam - BH) / abs(BH) < 1e-9, (lam, BH)


# --------------------------------------------------------------------------
# blocker 2 — the diagonal reluctivity
# --------------------------------------------------------------------------

def test_axial_nu_is_the_exact_inverse_of_the_scalar_solvers_axial_mu():
    """One homogenisation, one place.  ``nedelec.axial_nu`` may only invert
    ``solver.axial_mu`` — if it ever grew its own series average the two
    formulations would be solving different stacks."""
    mu = MU0 * np.array([1.0, 10.0, 200.0, 4600.0, 1e6])
    for kf in (1.0, 0.98, 0.92, 0.5):
        k = np.full(mu.shape, kf)
        got = 1.0 / axial_nu(1.0 / mu, k)
        # a reciprocal round-trip, so the bound is ulps rather than zero — the
        # point is that nothing but 1/x separates the two, not that IEEE is
        # exact under inversion
        assert np.allclose(got, axial_mu(mu, k), rtol=1e-15, atol=0)


def test_a_solid_region_takes_the_isotropic_path_bit_for_bit(coil_mesh):
    """k_f = 1 must be the isotropic assembly EXACTLY, not merely close.

    The lamination family has to contain the solid material as a member rather
    than as a special case; otherwise every isotropic result in the suite would
    shift the day the anisotropic branch was switched on."""
    regs = [Region("air", coil_mesh.elements("air")),
            Region("coil", coil_mesh.elements("coil"), mu_r=200.0,
                   stack_kf=1.0)]
    nu = np.full(coil_mesh.mesh.t.shape[1], 1.0 / MU0)
    nu[coil_mesh.elements("coil")] = 1.0 / (MU0 * 200.0)
    T = solenoid_T(J_COIL, R_I, R_O, L_SOL)
    iso = solve_static3d_A(coil_mesh.mesh, regs, gauge="tree", T=T)
    forced = solve_static3d_A(
        coil_mesh.mesh, regs, gauge="tree", T=T, nu_el=nu,
        nu_z_el=axial_nu(nu, kf_array(coil_mesh.mesh, regs)))
    scale = float(np.max(np.abs(iso.A)))
    assert float(np.max(np.abs(forced.A - iso.A))) < 1e-11 * scale


def test_lamination_helps_in_plane_flux_and_blocks_axial_flux():
    """The SIGNATURE that tells the correct tensor from the transposed one.

    Blocking the axial path through the insulation must make MORE of a
    transversely magnetised body's flux stay in the sheet plane and LESS of an
    axially magnetised one's cross the stack.  A nu tensor with the two indices
    swapped converges just as happily and gets both signs backwards, which is
    why this is a sign test and not a tolerance test."""
    tm = sphere_with_iron_shell(radius=0.01, shell_inner=0.013,
                                shell_outer=0.016, box_factor=2.5,
                                h_magnet=0.0040)
    mag, iron, air = tm.elements("magnet"), tm.elements("iron"), tm.elements("air")

    def iron_B(kf, Mvec):
        regs = [Region("air", air), Region("magnet", mag, mu_r=1.0, M=Mvec),
                Region("iron", iron, mu_r=4600.0, stack_kf=kf)]
        s = solve_static3d_A(tm.mesh, regs, gauge="tree")
        B = s.B_elementwise()[:, iron]
        v = s.basis.dx.sum(axis=1)[iron]
        return float((np.linalg.norm(B, axis=0) * v).sum() / v.sum())

    M = 1.19 / MU0
    in_plane = iron_B(0.92, (M, 0.0, 0.0)) / iron_B(1.0, (M, 0.0, 0.0))
    axial = iron_B(0.92, (0.0, 0.0, M)) / iron_B(1.0, (0.0, 0.0, M))
    assert in_plane > 1.02, in_plane
    assert axial < 0.95, axial


@requires_full
def test_the_two_formulations_agree_on_what_lamination_does():
    """Scalar P2 and the A-formulation must agree on the lamination RATIO.

    Not on B itself: ``benchmarks.iron_ladder`` already measured the lowest
    order edge element ~6x less accurate inside iron than the scalar P2 on the
    same mesh, so an absolute comparison there measures the element, not the
    tensor.  The ratio laminated/solid is where that error cancels, and the
    quantities with real signal — the gap and the magnet interior — agree to a
    few parts in a thousand."""
    tm = sphere_with_iron_shell(radius=0.01, shell_inner=0.013,
                                shell_outer=0.016, box_factor=3.0,
                                h_magnet=0.0028)
    mag, iron, air = tm.elements("magnet"), tm.elements("iron"), tm.elements("air")
    cen = np.asarray(tm.mesh.p)[:, np.asarray(tm.mesh.t)].mean(axis=1)
    rc = np.linalg.norm(cen, axis=0)
    gap = air[(rc[air] > 0.010) & (rc[air] < 0.013)]
    M = 1.19 / MU0

    def probe(which, kf):
        regs = [Region("air", air), Region("magnet", mag, mu_r=1.0,
                                           M=(0.0, 0.0, M)),
                Region("iron", iron, mu_r=4600.0, stack_kf=kf)]
        s = (solve_static3d_A(tm.mesh, regs, gauge="none") if which == "N0"
             else solve_static3d(tm.mesh, regs, order=2))
        B, v = s.B_elementwise(), s.basis.dx.sum(axis=1)
        return (float((np.linalg.norm(B[:, gap], axis=0) * v[gap]).sum()
                      / v[gap].sum()),
                float((B[2, mag] * v[mag]).sum() / v[mag].sum()))

    for i in (0, 1):
        p = probe("P2", 0.92)[i] / probe("P2", 1.0)[i]
        n = probe("N0", 0.92)[i] / probe("N0", 1.0)[i]
        assert abs(n - p) / abs(p) < 0.01, (i, p, n)


# --------------------------------------------------------------------------
# the winding itself
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def section():
    """The Stage A machine, with the Stage A magnet pinned in-process.

    ``load_motor_section`` reads the material ASSIGNMENTS from the live config,
    and that file has been reassigned to a different magnet grade since Stage A
    was measured.  Pinning here is a mutation of this process's cached config
    only — no file is written and the running backend is untouched."""
    from motor_ai_sim.config import get_config
    mats = (get_config() or {}).get("materials", {})
    for k, v in STAGE_A_MATERIALS.items():
        mats[k] = v
    return load_motor_section(geo_override=_preset()["geometry"])


def test_the_machine_is_the_one_stage_a_measured(section):
    """A guard, not a physics test: if the preset or the material assignment
    moves, every Stage B number stops being comparable with the passport, and
    that must fail loudly here rather than quietly downstream."""
    assert section.num_slots == 12 and section.num_poles == 14
    assert section.n_sectors == 2 and section.antiperiodic
    assert abs(section.Br_T - 1.19) < 5e-3, section.Br_T
    assert abs(section.mu_rec - 1.05) < 5e-3, section.mu_rec


def test_the_winding_closes_over_one_sector(section):
    """T must be anti-periodic, i.e. Psi(r, 180 deg) = 0 at every radius.

    For this single-layer 12s/14p layout the net ampere-turns per half are
    ``I_A - I_A + I_C - I_C + I_B - I_B``, identically zero for any currents at
    all — so the check is a check of the LAYOUT the 2D solver actually built,
    not of the arithmetic."""
    I = {"A": 40.0, "B": -25.0, "C": -15.0}
    wt = build_winding_T(section, I, n_r=61, n_theta=1801)
    rel = wt.meta["closure_Psi_max"] / wt.meta["closure_scale"]
    assert rel < 1e-9, rel


def test_the_winding_delivers_the_ampere_turns_build_materials_asked_for(section):
    """The MMF is the one thing that must be exact: it is what the machine is
    excited at, and the 2D leg it will be compared against is excited at the
    same number by the same call."""
    I = {"A": 40.0, "B": -25.0, "C": -15.0}
    wt = build_winding_T(section, I, n_r=61, n_theta=1801)
    assert wt.meta["ampere_turn_error_pct"] < 0.05, wt.meta["ampere_turn_error_pct"]
    want = wt.meta["ampere_turns_intended"]
    got = wt.meta["ampere_turns_delivered"]
    assert got, "no slot lay wholly inside the sector"
    scale = max(abs(v) for v in want.values())
    for k, v in got.items():
        assert abs(v - want[k]) < 1e-3 * scale, (k, v, want[k])


def test_this_layout_closes_for_ANY_currents_even_a_single_energised_phase(
        section):
    """Worth pinning separately, because it is a property of the LAYOUT and not
    of balance.

    Slots 0..5 carry ``A+ A- C- C+ B+ B-``: every phase appears once with each
    sign inside the 180 deg sector, so the net ampere-turns per sector are zero
    for arbitrary currents — including one phase energised alone, which is not a
    balanced set at all.  If that ever stopped being true the sector model would
    need a different justification, so it is asserted rather than assumed."""
    wt = build_winding_T(section, {"A": 40.0, "B": 0.0, "C": 0.0},
                         n_r=61, n_theta=1801)
    assert wt.meta["closure_Psi_max"] / wt.meta["closure_scale"] < 1e-9


def test_a_winding_whose_net_sector_current_is_not_zero_is_refused(section,
                                                                   monkeypatch):
    """A non-anti-periodic source on an anti-periodic sector is a different
    machine, and it must RAISE rather than be solved.

    This layout cannot produce that on its own (see the test above), so the
    layout is doctored: every slot in the sector is driven the same way, which
    is exactly the failure mode a hand-edited ``winding.layout`` in the config
    could introduce."""
    import motor_ai_sim.simulation.geometry_2d as G2

    n = section.num_slots
    monkeypatch.setattr(G2, "build_winding_layout",
                        lambda *a, **k: [("A", 1)] * n)
    with pytest.raises(ValueError, match="does not close"):
        build_winding_T(section, {"A": 40.0, "B": 0.0, "C": 0.0},
                        n_r=41, n_theta=901)


def test_a_cross_section_with_no_coils_is_refused(section):
    """Torque without a winding is cogging.  A geometry that carries no coil
    polygons must say so instead of returning a zero source."""
    import copy
    s = copy.copy(section)
    s.polys_full = dict(section.polys_full or {})
    s.polys_full["coils"] = []
    with pytest.raises(ValueError, match="no coil polygons"):
        build_winding_T(s, {"A": 1.0, "B": 0.0, "C": 0.0})


@requires_full
def test_a_loaded_sector_solve_is_anti_periodic_and_its_load_is_consistent(
        section):
    """The real machine, under load, on a coarse mesh.

    Two things are checked and they are the two that a wrong winding breaks
    first.  The solved A must satisfy ``A_slave = -A_master`` on the cut planes
    — an armature field that is not anti-periodic means the source was not — and
    the assembled load must still be consistent to round-off on a cross-section
    far messier than the solenoid benchmark's."""
    from motor_ai_sim.simulation.static3d import loaded as LD
    from motor_ai_sim.simulation.static3d.motor_mesh import build_section_mesh_2d

    sect = build_section_mesh_2d(section, box_factor=2.0, h_gap=0.9,
                                 h_solid=2.0)
    ls = LD.solve_sector_loaded(section, sect,
                                I_ph={"A": 40.0, "B": -25.0, "C": -15.0},
                                n_stack=3, n_cap=3, n_ew=2, linear_iron=True)
    assert ls.sol.source_residual < 1e-11, ls.sol.source_residual
    assert ls.sol.source_removed_norm == 0.0
    per = ls.periodic
    assert per is not None and per.sign == -1.0
    A = ls.sol.A
    scale = float(np.max(np.abs(A)))
    err = float(np.max(np.abs(A[per.slaves] - per.pair_signs() * A[per.masters])))
    assert err < 1e-9 * scale, err / scale


# --------------------------------------------------------------------------
# the end-winding band in the mesh
# --------------------------------------------------------------------------

def test_axial_levels_without_an_end_winding_band_are_stage_a_s():
    """The Stage A mesh must be reproducible bit for bit: the end-winding band
    is an addition, not a change."""
    a = axial_levels(6.0, 160.0, n_stack=8, n_cap=12)
    b = axial_levels(6.0, 160.0, n_stack=8, n_cap=12, h_ew_mm=None)
    assert np.array_equal(a, b)


def test_the_end_winding_band_is_resolved_and_lands_on_its_own_plane():
    """The tangential end-turn current lives between ``L/2`` and
    ``L/2 + h_ew``.  Both planes have to be mesh planes and the band has to hold
    more than one layer, or the end winding the 3D model exists to see is one
    element tall."""
    zh, h_ew, n_ew = 6.0, 2.8, 4
    z = axial_levels(zh, 160.0, n_stack=6, n_cap=8, h_ew_mm=h_ew, n_ew=n_ew)
    assert np.isclose(z, zh, atol=1e-9).any()
    assert np.isclose(z, zh + h_ew, atol=1e-9).any()
    inside = z[(z > zh - 1e-12) & (z < zh + h_ew + 1e-12)]
    assert inside.size == n_ew + 1, inside
    assert np.all(np.diff(z) > 0)
    assert z[0] == 0.0 and abs(z[-1] - 160.0) < 1e-9


def test_the_end_winding_profile_carries_all_of_the_current_across_the_band(
        section):
    """beta goes from 1 at the end of the stack to 0 at the top of the band, so
    every ampere-turn that went up the slot comes back tangentially inside the
    band and nothing leaks out of the top."""
    wt = build_winding_T(section, {"A": 40.0, "B": -25.0, "C": -15.0},
                         n_r=61, n_theta=1801)
    zh, h = wt.stack_half_m, wt.h_ew_m
    assert float(wt.beta(np.array([0.0]))[0]) == pytest.approx(1.0)
    assert float(wt.beta(np.array([zh]))[0]) == pytest.approx(1.0)
    assert float(wt.beta(np.array([zh + h]))[0]) == pytest.approx(0.0, abs=1e-12)
    assert float(wt.beta(np.array([zh + 2 * h]))[0]) == pytest.approx(0.0, abs=1e-12)
    # beta is monotone across the band: the end turn does not double back
    zz = np.linspace(zh, zh + h, 33)
    assert np.all(np.diff(wt.beta(zz)) <= 1e-15)


# --------------------------------------------------------------------------
# the passport's own arithmetic
# --------------------------------------------------------------------------

def _passport() -> dict:
    root = Path(__file__).resolve().parents[1]
    with (root / "config" / "end_effect_3d.json").open(encoding="utf-8") as fh:
        return json.load(fh)


def test_the_passport_never_quotes_a_k_T_it_cannot_reproduce():
    """``torque.k_T`` has to be recomputable from the passport's OWN rows.

    The honest failure mode for a number this expensive is not that it is wrong
    — it is that it stops being connected to the solves that produced it: a
    co-energy gets re-measured, a shift is dropped, a ratio is carried forward by
    hand.  So the passport records the co-energy at every rotor shift it solved
    and the step it quotes, and this recomputes ``T = dW'/dtheta`` and
    ``k_T = T / T_2d`` from those and demands the stored values back.

    ``k_T`` may legitimately be null (it was, for two Stage B passes).  What may
    never happen is a number without its solves, or a number that its own solves
    do not give.
    """
    torque = _passport()["stage_b"]["torque"]
    ce = torque.get("co_energy_k_T")
    if ce is None:
        assert torque.get("k_T") is None, \
            "k_T is quoted but co_energy_k_T (the solves behind it) is missing"
        assert torque.get("k_T_note"), "a null k_T must say why"
        return

    W = {int(r["shift"]): float(r["W_J"]) for r in ce["positions"]}
    pitch = math.radians(float(ce["ring_pitch_deg"]))
    T_2d = float(ce["T_2d_Nm"])
    for r in ce["positions"]:
        assert r["picard_converged"], f"position {r['shift']} did not converge"
        assert float(r["picard_residual"]) <= float(ce["tol"]), r

    for row in ce["central_differences"]:
        d = int(row["dm_total"]) // 2
        dth = 2 * d * pitch
        assert math.degrees(dth) == pytest.approx(row["d_theta_deg"], rel=1e-12)
        T = (W[d] - W[-d]) / dth
        assert T == pytest.approx(row["T_coenergy_Nm"], rel=1e-9), row
        assert T / T_2d == pytest.approx(row["k_T"], rel=1e-9), row

    quoted = [r for r in ce["central_differences"]
              if int(r["dm_total"]) == int(ce["quoted_dm_total"])]
    assert len(quoted) == 1, ce["quoted_dm_total"]


def test_the_quoted_k_T_is_the_matched_window_ratio_and_recomputes_from_it():
    """``torque.k_T`` must come from the MATCHED-window block and from nowhere
    else, and must recompute from that block's own stored energies.

    ``co_energy_k_T`` carries four k_T columns and not one of them is quotable:
    each divides a frozen-current 3D window mean by some reading of a 2D leg
    that is a different quantity, and they span 0.967 to 1.002.  The number the
    passport quotes is ``matched_2d_window``'s — a co-energy central difference
    over a co-energy central difference, same window, same frozen currents,
    same functional, same cross-section — so this test rebuilds BOTH legs from
    the stored ``W'`` rows and demands the stored ratio back.

    ``k_T`` may still legitimately be null.  What may never happen is a k_T
    that its own two legs do not give, or one taken from the superseded block."""
    torque = _passport()["stage_b"]["torque"]
    mw = torque.get("matched_2d_window")
    if mw is None:
        assert torque.get("k_T") is None, \
            "k_T is quoted but matched_2d_window (the denominator) is missing"
        assert torque.get("k_T_note"), "a null k_T must say why"
        return

    ce = torque["co_energy_k_T"]
    pitch = math.radians(float(ce["ring_pitch_deg"]))
    assert float(mw["mesh"]["n_tri"]) == float(ce["mesh"]["n_tri"]), \
        "the two legs are not on the same cross-section"
    assert int(mw["element_order"]) == 1, \
        "the 2D leg must be P1 — see matched_2d_window.why_the_2d_leg_is_P1"

    W3 = {int(r["shift"]): float(r["W_J"]) for r in ce["positions"]}
    W2 = {int(s): float(w) for s, w in zip(mw["shifts"], mw["W_2d_J"])}
    for row in mw["rows"]:
        d = int(row["dm_total"]) // 2
        dth = 2 * d * pitch
        assert math.degrees(dth) == pytest.approx(row["d_theta_deg"], rel=1e-12)
        T3 = (W3[d] - W3[-d]) / dth
        T2 = (W2[d] - W2[-d]) / dth
        assert T3 == pytest.approx(row["T_3d_Nm"], rel=1e-9), row
        assert T2 == pytest.approx(row["T_2d_Nm"], rel=1e-9), row
        assert T3 / T2 == pytest.approx(row["k_T"], rel=1e-9), row

    ks = [float(r["k_T"]) for r in mw["rows"]]
    assert len(ks) >= 2, "k_T needs at least two step sizes to be dm-independent"
    spread = 100.0 * (max(ks) - min(ks)) / (sum(ks) / len(ks))
    assert spread == pytest.approx(float(mw["k_T_spread_pct"]), rel=1e-9)

    quoted = [r for r in mw["rows"]
              if int(r["dm_total"]) == int(mw["quoted_dm_total"])]
    assert len(quoted) == 1, mw["quoted_dm_total"]
    assert mw["k_T"] == pytest.approx(quoted[0]["k_T"], rel=1e-12)
    if torque.get("k_T") is not None:
        assert torque["k_T"] == pytest.approx(mw["k_T"], rel=1e-12)
        assert float(torque["k_T_error_pct"]) == pytest.approx(spread, rel=1e-9)
        # a k_T that is not dm-independent is not an end effect, whatever else
        # it is; the bound is the one k_T_note names and measures
        assert spread < 0.5, ks
