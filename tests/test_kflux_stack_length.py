"""k_flux of the Stage A stack-length sweep uses each length's OWN mid-plane ratio.

``run_stage_a`` sweeps the stack length and reports, per length, the 3-D flux
factor k_flux(L) = <B1>_L / B1_2D = k_flux_self(L) * B1_mid(L) / B1_2D.  It used
to multiply every row by the REFERENCE length's B1_mid / B1_2D, which held
k_flux nearly flat in L (the mid-plane deficit against 2-D is itself a
stack-length effect) and read short stacks high.

The 3-D solves are replaced by a table, so this runs in milliseconds and needs
no private preset: the numbers are the mid-plane fundamentals and self-normalised
factors measured on a 28-pole 80 mm machine (reference 12 mm, rows 8 and 6 mm,
2-D B1 = 1.7552 T).  Only the bookkeeping of ``run_stage_a`` is under test.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from motor_ai_sim.simulation.static3d import end_effect as EE
from motor_ai_sim.simulation.static3d import motor_mesh as MMESH
from motor_ai_sim.simulation.static3d import ref2d as REF2D

B1_2D = 1.7552
REF_MM = 12.0
#: stack mm -> (B1_mid_T, k_flux_self), measured
TABLE = {12.0: (1.67993, 0.96404), 8.0: (1.62005, 0.96102), 6.0: (1.56407, 0.96335)}


def _row_of(stack_mm: float):
    L = min(TABLE, key=lambda k: abs(k - stack_mm))
    assert abs(L - stack_mm) < 1e-6
    return TABLE[L]


@pytest.fixture
def stubbed(monkeypatch):
    section = SimpleNamespace(
        fingerprint="0000test", num_slots=24, num_poles=28, pole_pairs=14,
        r_stator_out_mm=40.0, r_stator_in_mm=25.0, r_rotor_out_mm=24.6,
        mid_r_mm=24.8, stack_mm=REF_MM, Br_T=1.44, mu_rec=1.05, materials={},
        sector_deg=90.0, n_sectors=4, antiperiodic=False, regions=[],
        summary=lambda: "stub")
    sect2d = SimpleNamespace(r_box_mm=160.0, n_tri=1, p=[[0.0]],
                             meta={"n_periodic_curves": 2})

    def fake_solve(section_, sect2d_, stack_mm=None, **kw):
        sol = SimpleNamespace(ndofs=1, order=2, solver="stub", assemble_time=0.0,
                              solve_time=0.0, picard={"converged": True},
                              boundary_flux=lambda: 0.0, mu_converged=None)
        tm = SimpleNamespace(n_elements=1,
                             meta={"z_box_mm": 160.0, "n_layers": 1, "z_levels_mm": [0.0]})
        return SimpleNamespace(sol=sol, tm=tm, stack_mm=float(stack_mm), wall_s=0.0)

    def fake_profile(ss, n_theta=180, **kw):
        b1, ks = _row_of(ss.stack_mm)
        return dict(z_m=[0.0], z_over_half=[0.0], B1_T=[b1], phase=[0.0],
                    profile=[1.0], B1_mid_T=b1, k_flux_self=ks,
                    stack_half_m=0.5e-3 * ss.stack_mm)

    r2 = SimpleNamespace(B1_T=B1_2D, harmonics={1: B1_2D}, n_tri=1, ndofs=1,
                         wall_s=0.0, outer_r_mm=40.0, relaxation=1.0,
                         picard_sweeps=1, picard_residual=0.0,
                         picard_converged=True, even_over_odd=0.0)
    monkeypatch.setattr(EE, "load_motor_section", lambda **kw: section)
    monkeypatch.setattr(EE, "build_section_mesh_2d", lambda *a, **kw: sect2d)
    monkeypatch.setattr(EE, "solve_sector", fake_solve)
    monkeypatch.setattr(EE, "spill_profile", fake_profile)
    monkeypatch.setattr(EE, "demag_exposure", lambda ss: {})
    monkeypatch.setattr(MMESH, "physical_mesh_sizes", lambda s: dict(
        h_gap=0.1, h_solid=0.3, h_axial_end=0.2, h_axial_max=2.0,
        n_gap=4, n_magnet=6, gap_mm=0.4, magnet_width_mm=2.0))
    monkeypatch.setattr(REF2D, "solve_2d_reference_converged", lambda *a, **kw: r2)
    monkeypatch.setattr(REF2D, "solve_2d_reference_on_section_mesh",
                        lambda *a, **kw: SimpleNamespace(B1_T=B1_2D))


def _run(do_2d=True):
    return EE.run_stage_a(l_factors=(1.0, 8.0 / 12.0, 0.5), do_bracket=False,
                          do_2d=do_2d, verbose=False)


def _rows(pp):
    return {round(r["stack_mm"], 6): r for r in pp["l_stack_curve"]}


def test_each_length_uses_its_own_mid_plane_ratio(stubbed):
    rows = _rows(_run())
    for L, (b1, ks) in TABLE.items():
        assert rows[L]["k_flux"] == pytest.approx(ks * b1 / B1_2D, rel=1e-12), L
    for L, (b1, ks) in TABLE.items():
        assert rows[L]["ratio_3d_mid_over_2d"] == pytest.approx(b1 / B1_2D, rel=1e-12)
    # the measured values: 12 / 8 / 6 mm = 0.9227 / 0.8870 / 0.8585
    assert rows[12.0]["k_flux"] == pytest.approx(0.92270, abs=5e-5)
    assert rows[8.0]["k_flux"] == pytest.approx(0.88702, abs=5e-5)
    assert rows[6.0]["k_flux"] == pytest.approx(0.85845, abs=5e-5)
    # a shorter stack keeps less flux: the factor falls with the length
    assert rows[12.0]["k_flux"] > rows[8.0]["k_flux"] > rows[6.0]["k_flux"]


def test_reference_length_is_bit_identical(stubbed):
    pp = _run()
    ref = _rows(pp)[REF_MM]
    b1, ks = TABLE[REF_MM]
    # the passport's k_flux and the reference row: exactly the old expression
    assert pp["k_flux"] == ks * (b1 / B1_2D)
    assert ref["k_flux"] == pp["k_flux"]
    assert pp["two_d"]["ratio_3d_mid_over_2d"] == b1 / B1_2D


def test_without_a_2d_leg_rows_keep_k_flux_self(stubbed):
    pp = _run(do_2d=False)
    for L, (b1, ks) in TABLE.items():
        r = _rows(pp)[L]
        assert r["k_flux"] == ks
        assert "ratio_3d_mid_over_2d" not in r
    assert pp["k_flux"] == TABLE[REF_MM][1]


def test_new_table_carries_the_method_marker(stubbed):
    pp = _run()
    assert pp["version"] == EE.PASSPORT_VERSION == "static3d-stageA-2"
    assert pp["k_flux_vs_L_method"] == EE.K_FLUX_VS_L_OWN_MID == "own_mid_ratio"
    b1, ks = TABLE[REF_MM]
    assert pp["k_flux_self"] == ks          # reference values untouched
    assert pp["k_flux"] == ks * (b1 / B1_2D)
    assert _run(do_2d=False)["k_flux_vs_L_method"] == EE.K_FLUX_VS_L_SELF
    assert "k_flux_vs_L_method" in EE.SCHEMA["fields"]


def test_adopted_entry_and_region_mirror_keep_the_marker(stubbed):
    from motor_ai_sim.regions import catalog_manifest as CM
    from motor_ai_sim.routes import static3d as S
    pp = _run()
    pp["machine"]["stator_od_mm"] = 80.0
    _fp, e = S.adopt_entry(pp)
    assert e["k_flux_vs_L_method"] == "own_mid_ratio"
    assert e["k_flux_vs_L"]["8.0"] == pytest.approx(0.88702, abs=5e-5)
    assert e["k_flux"] == pp["k_flux"]
    out = CM.clean_passport(e, {})
    assert out["k_flux_vs_L_method"] == "own_mid_ratio"
    assert out["k_flux_vs_L"] == e["k_flux_vs_L"]
