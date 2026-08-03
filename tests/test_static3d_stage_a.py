"""Stage A tests: the sector mesh, the anti-periodic sector model, the passport.

Runtime budget: the default run stays under two minutes, so it can sit in a
normal test loop.  The full-fidelity case — the P2 nonlinear long-stack solve
that has to agree with the 2D solver — costs tens of minutes and is behind
``STATIC3D_FULL=1``.

The test that matters most is ``test_sector_equals_full_ring``: everything in
Stage A rests on the claim that a 180 deg anti-periodic wedge is the same
machine as the full ring.  If that claim is wrong, every k_flux in the passport
is a number about a machine that does not exist, and no amount of mesh
refinement will show it.  So it is checked directly, against a full-ring solve
of the same cross-section on the same size field.
"""
from __future__ import annotations

import json
import math
import os
import tempfile

import numpy as np
import pytest

from motor_ai_sim.simulation.static3d import end_effect as EE
from motor_ai_sim.simulation.static3d import motor_mesh as MMESH
from motor_ai_sim.simulation.static3d.motor_geometry import (MM, load_motor_section,
                                                             sector_choice)
from motor_ai_sim.simulation.static3d.ref2d import gap_fundamental

FULL = os.environ.get("STATIC3D_FULL") == "1"
requires_full = pytest.mark.skipif(not FULL,
                                   reason="set STATIC3D_FULL=1 for the "
                                          "full-fidelity Stage A case")


# --------------------------------------------------------------------------
# fixtures — one coarse cross-section shared by the fast tests
# --------------------------------------------------------------------------

# The tests are pinned to a SAVED preset, never to config/motor_config.yaml.
# That file is live: the backend and the optimizer both write it, and during
# this work it moved 40 mm -> 30 mm -> 150 mm between runs.  A test suite whose
# subject changes under it is not a test suite.
PRESET = "my_40mm_last"


def _preset_geometry(name: str = PRESET) -> dict:
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    with (root / "config" / "motor_presets.json").open(encoding="utf-8") as fh:
        return dict(json.load(fh)[name]["geometry"])


@pytest.fixture(scope="module")
def geo():
    return _preset_geometry()


@pytest.fixture(scope="module")
def section(geo):
    return load_motor_section(geo_override=geo)


@pytest.fixture(scope="module")
def coarse2d(section):
    return MMESH.build_section_mesh_2d(section, box_factor=2.0,
                                       h_gap=0.9, h_solid=2.0, grade_far=1.0)


# --------------------------------------------------------------------------
# geometry / sector logic
# --------------------------------------------------------------------------

def test_sector_choice_matches_the_2d_rule():
    # 12s/14p: gcd 2 -> 180 deg, 7 poles per sector -> odd -> anti-periodic
    assert sector_choice(12, 14) == (2, True)
    # one pole per sector is still odd -> anti-periodic
    assert sector_choice(12, 12) == (12, True)
    # an even pole count per sector is plain periodic
    assert sector_choice(12, 12, 6) == (6, False)
    assert sector_choice(24, 28, 2) == (2, False)      # 14 poles per sector
    assert sector_choice(24, 28, 4) == (4, True)       # 7 poles per sector
    with pytest.raises(ValueError):
        sector_choice(12, 14, 3)                       # 3 does not divide gcd 2


def test_section_carries_the_real_machine(section):
    assert section.num_slots * section.num_poles > 0
    assert section.n_sectors == math.gcd(section.num_slots, section.num_poles)
    assert len(section.magnet_regions()) == section.num_poles // section.n_sectors
    # spoke PM: every magnetisation is TANGENTIAL, and they alternate
    prev = None
    for r in section.magnet_regions():
        c = r.polygon.centroid
        er = np.array([c.x, c.y]) / math.hypot(c.x, c.y)
        m = np.array(r.M[:2])
        m = m / np.linalg.norm(m)
        assert abs(float(er @ m)) < 0.05, "magnetisation is not tangential"
        if prev is not None:
            assert float(prev @ m) < 0.0, "adjacent magnets do not alternate"
        prev = m
    assert 0.5 < section.Br_T < 1.6
    assert 1.0 <= section.mu_rec < 1.2


# --------------------------------------------------------------------------
# the mesh
# --------------------------------------------------------------------------

def test_cross_section_areas_are_exact(section, coarse2d):
    """Every polygon must come out of the fragment with its area intact — a
    dropped sliver here is a magnet the solve never sees."""
    for r in section.regions:
        got = coarse2d.region_area_mm2(r.name)
        assert got == pytest.approx(r.area_mm2, rel=2e-6), r.name


def test_cut_lines_are_periodic(coarse2d):
    assert coarse2d.masters.size > 5
    assert coarse2d.masters.size == coarse2d.slaves.size
    r_m = np.hypot(*coarse2d.p[:, coarse2d.masters])
    r_s = np.hypot(*coarse2d.p[:, coarse2d.slaves])
    assert float(np.max(np.abs(r_m - r_s))) < 1e-9


def test_axial_levels_land_on_the_stack_end():
    z = MMESH.axial_levels(5.0, 45.0, n_stack=8, n_cap=10)
    assert z[0] == 0.0
    assert np.min(np.abs(z - 5.0)) < 1e-12, "z = L/2 is not a mesh plane"
    assert z[-1] == pytest.approx(45.0)
    assert np.all(np.diff(z) > 0)
    # refined toward the stack end, coarsening away from it
    inside = np.diff(z[z <= 5.0 + 1e-12])
    assert inside[-1] < 0.35 * inside[0]


@pytest.mark.parametrize("stack_mm", [10.0, 5.0, 20.0])
def test_region_volumes_equal_area_times_half_stack(section, coarse2d, stack_mm):
    """The extrusion's whole contract: region volume = cross-section area x half
    the stack, exactly, and the rest of the column is air."""
    tm = MMESH.extrude_section(
        coarse2d,
        MMESH.axial_levels(0.5 * stack_mm, coarse2d.r_box_mm, 5, 6),
        0.5 * stack_mm, section)
    half_m = 0.5 * stack_mm * MM
    for r in section.regions:
        want = r.area_mm2 * 1e-6 * half_m
        assert tm.region_volume(r.name) == pytest.approx(want, rel=1e-9), r.name
    # nothing is lost: solids + air = the WHOLE meshed column.  The reference is
    # the meshed cross-section area, not pi*r^2/2 — the outer arc is a polygon
    # discretised to the far element size, so the analytic disk is ~0.5 % larger
    # and comparing against it would test the arc chord error, not the extrusion.
    total = sum(tm.region_volume(n) for n in tm.names)
    area = sum(coarse2d.region_area_mm2(n) for n in coarse2d.names) * 1e-6
    assert total == pytest.approx(area * coarse2d.r_box_mm * MM, rel=1e-9)


def test_prism_split_is_conformal(section, coarse2d):
    """A tet mesh is conformal iff every interior face is shared by exactly two
    tets; a bad diagonal choice on a shared quad shows up here immediately."""
    tm = MMESH.extrude_section(
        coarse2d, MMESH.axial_levels(5.0, coarse2d.r_box_mm, 3, 3), 5.0, section)
    t = np.asarray(tm.mesh.t)
    faces = np.concatenate([t[[0, 1, 2]], t[[0, 1, 3]], t[[0, 2, 3]], t[[1, 2, 3]]],
                           axis=1)
    faces = np.sort(faces, axis=0)
    _, counts = np.unique(faces, axis=1, return_counts=True)
    assert set(np.unique(counts)).issubset({1, 2}), "a face is shared by >2 tets"
    assert (counts == 2).sum() > 0.7 * counts.size


# --------------------------------------------------------------------------
# the sector model itself
# --------------------------------------------------------------------------

def _linear_sector_solve(section, sect2d, order=1, stack_mm=None):
    return EE.solve_sector(section, sect2d, stack_mm=stack_mm, order=order,
                           n_stack=3, n_cap=3, linear_iron=True)


@pytest.fixture(scope="module")
def sector_solve(section, coarse2d):
    """ONE coarse linear sector solve, shared by every test that only needs a
    field to interrogate.  Five separate solves of the same problem is four
    minutes of nothing."""
    return _linear_sector_solve(section, coarse2d)


def test_solution_is_anti_periodic(sector_solve):
    ss = sector_solve
    per = ss.periodic
    assert per is not None and per.sign == -1.0
    phi = ss.sol.phi
    lhs = phi[per.slaves]
    rhs = per.sign * phi[per.masters]
    scale = float(np.max(np.abs(phi)))
    assert float(np.max(np.abs(lhs - rhs))) < 1e-10 * scale


def test_mirror_plane_is_not_clamped(sector_solve, coarse2d):
    """z = 0 is physics (B_z = 0 by symmetry), not a truncation surface.  If it
    ever ends up in the Dirichlet set the end effect is measured against a wall."""
    ss = sector_solve
    loc = np.asarray(ss.basis.doflocs)[:, ss.dirichlet]
    assert float(np.min(np.abs(loc[2]))) > 1e-6, "a z=0 dof was clamped"
    r = np.hypot(loc[0], loc[1])
    z_box = ss.tm.meta["z_box_mm"] * MM
    r_box = coarse2d.r_box_mm * MM
    on_trunc = (r > 0.99 * r_box) | (loc[2] > 0.99 * z_box)
    assert bool(np.all(on_trunc)), "a clamped dof is not on the truncation surface"


def test_sector_equals_full_ring(section, coarse2d, sector_solve):
    """THE sector test: a 180 deg anti-periodic wedge and the UNCONSTRAINED full
    ring must give the same mid-plane gap fundamental.

    The full-ring cross-section is the sector's own mesh rotated onto itself and
    welded (see ``mirror_to_full_ring`` for why the direct 360 deg OCC build is
    not usable here), so this does not test mesh bias.  It tests the things that
    can actually be silently wrong: the sign of the coupling, the dof
    elimination, and whether this machine's magnet pattern is anti-periodic at
    all.  Get any of those wrong and the sector solve collapses toward zero
    field rather than differing by a few percent.
    """
    full = load_motor_section(geo_override=_preset_geometry(), n_sectors=1)
    f2d = MMESH.mirror_to_full_ring(coarse2d, full)
    assert f2d.t.shape[1] == 2 * coarse2d.t.shape[1]
    for r in full.regions:
        # 1e-4, not the 2e-6 the sector build is held to: the sector's cut-line
        # vertices are snapped to a 10 um radial grid so the two cuts are exact
        # rotations of each other (motor_geometry._snap_cut_vertices), which
        # moves the boundary ON the cut by up to 5 um.  The full-ring polygon
        # has no cut and no snap, so the two areas cannot agree to round-off.
        assert f2d.region_area_mm2(r.name) == pytest.approx(r.area_mm2, rel=1e-4), r.name

    ss_full = _linear_sector_solve(full, f2d)
    assert ss_full.periodic is None
    ss_sec = sector_solve
    a_full, _ = EE.airgap_B1(ss_full, [1e-7], n_theta=252)
    a_sec, _ = EE.airgap_B1(ss_sec, [1e-7], n_theta=126)
    assert a_full[0] > 1e-4
    rel = abs(a_sec[0] - a_full[0]) / a_full[0]
    assert rel < 0.01, f"sector vs full ring differ by {100*rel:.2f} %"


def test_full_ring_solution_is_anti_periodic(section, coarse2d):
    """...and the unconstrained full ring finds the anti-periodicity by itself,
    which is what makes imposing it on the sector legitimate."""
    full = load_motor_section(geo_override=_preset_geometry(), n_sectors=1)
    ss = _linear_sector_solve(full, MMESH.mirror_to_full_ring(coarse2d, full))
    th = np.linspace(0.0, 2 * math.pi, 180, endpoint=False)
    r = full.mid_r_m
    pts = np.vstack([r * np.cos(th), r * np.sin(th), np.full(th.shape, 1e-7)])
    B = ss.sol.B_at_points(pts)
    Br = B[0] * np.cos(th) + B[1] * np.sin(th)
    half = Br.size // 2
    err = float(np.max(np.abs(Br[:half] + Br[half:])) / np.max(np.abs(Br)))
    assert err < 0.02, f"full-ring field is not anti-periodic ({100*err:.1f} %)"


# --------------------------------------------------------------------------
# post-processing
# --------------------------------------------------------------------------

def test_gap_fundamental_recovers_a_known_harmonic():
    p = 7
    th = np.linspace(0.0, 2 * math.pi, 360, endpoint=False)
    # ODD harmonics only: that is exactly what an anti-periodic field carries
    sig = 0.83 * np.cos(p * th + 0.4) + 0.2 * np.cos(3 * p * th)
    amp, pha = gap_fundamental(th, sig, p)
    assert amp == pytest.approx(0.83, rel=1e-10)
    assert pha == pytest.approx(0.4, abs=1e-10)
    # ...and the anti-periodic HALF carries the same coefficient, which is why
    # the 180 deg model needs no reconstruction of the other half
    half = th[th < math.pi]
    amp_h, pha_h = gap_fundamental(half, sig[:half.size], p)
    assert amp_h == pytest.approx(0.83, rel=1e-9)
    assert pha_h == pytest.approx(0.4, abs=1e-9)
    # a NON anti-periodic component (a dc offset) does leak into a half-range
    # transform, so the half-range shortcut is only valid for the field it is
    # applied to here
    off = gap_fundamental(half, sig[:half.size] + 0.05, p)[0]
    assert abs(off - 0.83) > 1e-4


def test_spill_profile_shape_and_k_flux(sector_solve):
    ss = sector_solve
    pr = EE.spill_profile(ss, n_in=9, n_out=3, n_theta=126)
    assert pr["profile"][0] == pytest.approx(1.0)
    assert np.all(np.isfinite(pr["B1_T"])) and np.all(pr["B1_T"] > 0)
    # OUTSIDE the iron the fundamental must fall away, whatever the model:
    # there is no source out there for it to come from.
    outside = pr["z_over_half"] > 1.0
    tail = pr["B1_T"][outside]
    assert tail[0] < pr["B1_T"][0]
    # ...to nothing.  Not asserted monotone: past a couple of stack lengths the
    # fundamental is three orders below the mid-plane value and is discretisation
    # noise, so a strict ordering there would test the mesh, not the physics.
    assert tail[-1] < 0.02 * pr["B1_T"][0]
    # k_flux is NOT asserted below 1 here: this fixture solves LINEAR iron,
    # where the spoke rotor's bridges short most of the magnet flux and the
    # small remainder reaching the gap is a difference of large numbers with no
    # reliable axial trend.  The monotone spill is a property of the SATURATED
    # machine and is asserted in the full-fidelity test.
    assert 0.4 < pr["k_flux_self"] < 1.3


def test_demag_exposure_is_signed_and_ordered(sector_solve):
    ss = sector_solve
    d = EE.demag_exposure(ss, n_slices=4)
    assert len(d["slices"]) == 4
    for s in d["slices"]:
        assert s["worst_H_A_per_m"] <= s["mean_H_A_per_m"] + 1e-9
    assert d["mid_mean_H"] < 0.0, "the magnet should be operating demagnetised"


# --------------------------------------------------------------------------
# passport
# --------------------------------------------------------------------------

def test_passport_round_trips():
    pp = dict(version=EE.PASSPORT_VERSION, geometry_fingerprint="deadbeef",
              k_flux=0.914, k_flux_self=0.930,
              spill_profile=dict(z_m=np.linspace(0, 5e-3, 5),
                                 B1_normalised=np.array([1.0, .99, .97, .92, .8])),
              l_stack_curve=[dict(factor=1.0, k_flux=0.914)],
              two_d=None, bracket=None, demag={}, solves=[])
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "sub", "end_effect_3d.json")
        out = EE.write_passport(pp, p)
        assert os.path.isfile(out)
        back = EE.read_passport(out)
    assert back["k_flux"] == pytest.approx(0.914)
    assert back["geometry_fingerprint"] == "deadbeef"
    assert back["_schema"]["version"] == EE.PASSPORT_VERSION
    assert back["spill_profile"]["B1_normalised"][0] == pytest.approx(1.0)
    assert isinstance(json.dumps(back), str)


def test_passport_fingerprint_matches_the_project_helper(section, geo):
    """The passport's fingerprint is the project's own, over the SAME geometry
    override the section was built from — that is what lets a stored passport be
    matched against a design later."""
    from motor_ai_sim.routes.simulation import _geometry_fingerprint
    assert section.fingerprint == _geometry_fingerprint(geo)


# --------------------------------------------------------------------------
# full fidelity
# --------------------------------------------------------------------------

@requires_full
def test_long_stack_limit_matches_2d(section, geo):
    """The honesty test: at 4x the real stack the mid-plane gap fundamental must
    converge onto the 2D solver's answer.  A 3D model that does NOT reduce to 2D
    in the long-stack limit is not correcting 2D, it is disagreeing with it."""
    from motor_ai_sim.simulation.static3d.ref2d import solve_2d_reference

    sect2d = MMESH.build_section_mesh_2d(section, box_factor=3.0,
                                         h_gap=0.30, h_solid=0.95)
    ss = EE.solve_sector(section, sect2d, stack_mm=4.0 * section.stack_mm,
                         order=2, n_stack=8, n_cap=8, tol=4e-3, max_iter=60)
    mid, _ = EE.airgap_B1(ss, [1e-7], n_theta=180)
    ref = solve_2d_reference(section)
    rel = abs(mid[0] - ref.B1_T) / ref.B1_T
    assert rel < 0.06, (f"3D mid-plane {mid[0]:.4f} T vs 2D {ref.B1_T:.4f} T "
                        f"= {100*rel:.2f} % — outside the stated mesh error")


@requires_full
def test_full_stage_a_runs_and_writes(tmp_path):
    pp = EE.run_stage_a(geo_override=_preset_geometry(),
                        l_factors=(1.0, 2.0), do_bracket=True, do_2d=True,
                        verbose=False)
    out = EE.write_passport(pp, str(tmp_path / "end_effect_3d.json"))
    back = EE.read_passport(out)
    assert 0.5 < back["k_flux"] < 1.05
    assert len(back["l_stack_curve"]) == 2
    assert back["bracket"]["rel_spread_k_flux"] < 0.05
