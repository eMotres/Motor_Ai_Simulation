"""The per-length k_flux table: which tables a reader may interpolate, and run_stage_a's input guards.

Readers (the datasheet's Recalculate sheet, the Configure tuner, the passport lending) interpolate the passport's
``k_flux_vs_L``.  A multi-row table written before the 2026-11-24 fix (no ``k_flux_vs_L_method``) used the reference
length's mid-plane ratio for every row: only its reference row is right, so ``end3d_factors.k_flux_table`` keeps that
row alone and says the table is stale.  The guards: a zero / NaN 2-D fundamental gives self-normalised rows (no
division), a sweep without factor 1.0 still carries the reference row, a bad factor is refused before any solve.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from motor_ai_sim.end3d_factors import K_FLUX_VS_L_METHODS_OK, STALE_KL_FLAG, k_flux_table
from motor_ai_sim.simulation.static3d import end_effect as EE
from motor_ai_sim.simulation.static3d import ref2d as REF2D
from tests.test_kflux_stack_length import B1_2D, REF_MM, TABLE, stubbed  # noqa: F401  (fixture)

# a catalog quick passport as stored before the fix: 0.75 / 1.0 / 1.5 x 12 mm, nearly flat, no marker, no stack_mm
OLD = {"k_flux": 0.9518297570494533, "k_flux_vs_L": {"9.0": 0.94985, "12.0": 0.95183, "18.0": 0.95737}}


# --------------------------------------------------------------------------
# k_flux_table
# --------------------------------------------------------------------------

def test_a_marked_table_is_used_as_it_is():
    for method in sorted(K_FLUX_VS_L_METHODS_OK):
        rows, stale = k_flux_table({**OLD, "k_flux_vs_L_method": method})
        assert stale is False
        assert rows == {9.0: 0.94985, 12.0: 0.95183, 18.0: 0.95737}


def test_an_unmarked_multi_row_table_keeps_only_the_reference_row():
    rows, stale = k_flux_table(OLD)                       # the row equal to the scalar (rounded to 5) is the reference
    assert stale is True and rows == {12.0: OLD["k_flux"]}
    rows, stale = k_flux_table({**OLD, "stack_mm": 12.0})  # adopted entries record their own stack
    assert stale is True and rows == {12.0: OLD["k_flux"]}
    rows, stale = k_flux_table({**OLD, "k_flux_vs_L_method": "something else"})
    assert stale is True and rows == {12.0: OLD["k_flux"]}


def test_an_unmarked_table_whose_reference_cannot_be_found_gives_no_rows():
    rows, stale = k_flux_table({"k_flux": 0.9, "k_flux_vs_L": {"9.0": 0.8, "12.0": 0.85}})
    assert stale is True and rows == {}
    rows, stale = k_flux_table({"k_flux_vs_L": {"9.0": 0.8, "12.0": 0.85}})
    assert stale is True and rows == {}


def test_one_row_or_no_table_is_not_stale():
    assert k_flux_table({"k_flux": 0.96269, "k_flux_vs_L": {"20.0": 0.96269}}) == ({20.0: 0.96269}, False)
    assert k_flux_table({"k_flux": 0.95}) == ({}, False)
    assert k_flux_table(None) == ({}, False)
    # non-numbers are dropped, as the readers always did
    assert k_flux_table({"k_flux_vs_L": {"x": 0.9, "12.0": "nan", "13.0": 0.91}}) == ({13.0: 0.91}, False)


def test_the_flag_is_one_short_line():
    assert STALE_KL_FLAG == "k(L) computed before 2026-11-24: recompute"


# --------------------------------------------------------------------------
# run_stage_a guards
# --------------------------------------------------------------------------

def _rows(pp):
    return {round(r["stack_mm"], 6): r for r in pp["l_stack_curve"]}


@pytest.fixture
def stubbed_verbose(stubbed, monkeypatch):  # noqa: F811
    """The stub, with a cross-section the verbose prints can describe (they read p.shape)."""
    sect2d = SimpleNamespace(r_box_mm=160.0, n_tri=1, p=np.zeros((2, 1)), meta={"n_periodic_curves": 2})
    monkeypatch.setattr(EE, "build_section_mesh_2d", lambda *a, **kw: sect2d)


@pytest.mark.parametrize("b1", [0.0, float("nan"), -1.0, None])
def test_a_zero_or_nan_2d_fundamental_gives_self_normalised_rows(stubbed_verbose, monkeypatch, b1):
    r2 = SimpleNamespace(B1_T=b1, harmonics={}, n_tri=1, ndofs=1, wall_s=0.0, outer_r_mm=40.0, relaxation=1.0,
                         picard_sweeps=1, picard_residual=0.0, picard_converged=True, even_over_odd=0.0)
    monkeypatch.setattr(REF2D, "solve_2d_reference_converged", lambda *a, **kw: r2)
    pp = EE.run_stage_a(l_factors=(1.0, 0.5), do_bracket=False, do_2d=True, verbose=True)
    assert pp["two_d"]["verdict"] == "INVALID" and pp["two_d"]["k_flux_usable"] is False
    assert pp["two_d"]["ratio_3d_mid_over_2d"] is None and "zero or not a number" in pp["two_d"]["warning"]
    assert pp["k_flux"] == pp["k_flux_self"] == TABLE[REF_MM][1]
    for L, r in _rows(pp).items():
        assert r["k_flux"] == r["k_flux_self"] and "ratio_3d_mid_over_2d" not in r
    assert pp["k_flux_vs_L_method"] == EE.K_FLUX_VS_L_SELF


def test_a_sweep_without_the_reference_factor_still_carries_the_reference_row(stubbed):  # noqa: F811
    pp = EE.run_stage_a(l_factors=(0.5,), do_bracket=False, do_2d=True, verbose=False)
    curve = pp["l_stack_curve"]
    assert [r["stack_mm"] for r in curve] == [6.0, REF_MM]   # the caller's first factor stays first
    assert curve[-1]["k_flux"] == pp["k_flux"]
    b1, ks = TABLE[6.0]
    assert curve[0]["k_flux"] == pytest.approx(ks * b1 / B1_2D, rel=1e-12)


@pytest.mark.parametrize("bad", [(0.0,), (1.0, -0.5), (1.0, float("nan")), (float("inf"),)])
def test_a_bad_stack_factor_is_refused_before_any_solve(monkeypatch, bad):
    monkeypatch.setattr(EE, "load_motor_section", lambda **kw: pytest.fail("solved before the check"))
    with pytest.raises(ValueError, match="l_factors"):
        EE.run_stage_a(l_factors=bad, do_bracket=False, do_2d=False, verbose=False)


def test_the_valid_path_is_unchanged(stubbed_verbose):
    pp = EE.run_stage_a(l_factors=(1.0, 0.5), do_bracket=False, do_2d=True, verbose=True)
    b1, ks = TABLE[REF_MM]
    assert pp["k_flux"] == ks * (b1 / B1_2D)
    assert pp["two_d"]["ratio_3d_mid_over_2d"] == b1 / B1_2D
    assert pp["two_d"]["verdict"] == "FAIL"                  # 0.957 at 12 mm: see the proposal on this check
    assert pp["k_flux_vs_L_method"] == EE.K_FLUX_VS_L_OWN_MID
