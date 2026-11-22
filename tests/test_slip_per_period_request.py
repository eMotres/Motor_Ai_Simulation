"""A per-request air-gap ring density q (TASK_mesh_gap_stage3.md item 2): snapped to multiples of 24 and of the sector count, reported
(requested and effective), part of the cache key only when set, passed through run_one, refused up front when invalid."""
from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException

from motor_ai_sim.simulation.slip_ring import snap_slip_per_period as snap


@pytest.mark.parametrize("q,pp,n", [(144, 5, 2), (100, 5, 2), (24, 5, 2), (48, 7, 4), (24, 7, 4), (24, 5, 8), (96, 14, 2), (1, 1, 1), (192, 3, 5)])
def test_snap_is_a_multiple_of_24_and_of_the_sector_count(q, pp, n):
    got = snap(q, pp, n)
    assert got % 24 == 0 and (pp * got) % n == 0 and got >= q and got - q < 24 * n   # the smallest such value, not a jump


def test_snap_goes_up_until_the_wedge_ring_is_whole():
    # 3 pole pairs, 4 sectors: 3 * 24 = 72 = 4 * 18 is whole already, but 3 * 24 with 5 sectors is not until 24 * 5 = 120
    assert snap(24, 3, 5) == 120 and (3 * 120) % 5 == 0
    assert snap(25, 3, 1) == 48 and snap(48, 3, -1) == 48          # n_sectors -1 (full ring) and 1 are the same


def test_a_request_without_q_is_the_adaptive_ring_and_the_route_and_run_one_default_to_none():
    from motor_ai_sim.optimization.refine_proc import run_one
    from motor_ai_sim.routes.simulation import get_fem_transient
    assert inspect.signature(run_one).parameters["slip_per_period"].default is None
    assert inspect.signature(get_fem_transient).parameters["slip_per_period"].default is None


@pytest.mark.parametrize("bad", [0, 5, 23, 2401, 10_000])
def test_an_invalid_q_is_a_422_before_anything_is_solved(bad):
    from motor_ai_sim.routes.simulation import get_fem_transient
    with pytest.raises(HTTPException) as e:
        get_fem_transient(slip_per_period=bad, n_steps_per_period=48)
    assert e.value.status_code == 422 and "slip_per_period" in e.value.detail


def test_run_one_hands_q_to_the_transient_only_when_it_is_set(monkeypatch):
    import motor_ai_sim.optimization.refine_proc as R
    seen = []

    class K:
        def run(self, cap, payload):
            seen.append(dict(payload))
            raise RuntimeError("stop here")                          # the payload is all this test needs

    monkeypatch.setattr(R, "_kernel", lambda: K())
    for q in (None, 144):
        with pytest.raises(RuntimeError, match="stop here"):
            R.run_one({}, 10.0, 48, 120.0, slip_per_period=q)
    assert len(seen) == 2                                             # unconditional: both calls reached the kernel seam
    assert "slip_per_period" not in seen[0] and seen[1]["slip_per_period"] == 144


@pytest.mark.parametrize("bad", [25.9, "bad", True, float("nan"), 0, 23, 2401, "144"])
def test_one_strict_whole_number_check_on_every_transport(bad):
    from motor_ai_sim.simulation.slip_ring import validate_slip_per_period as v
    with pytest.raises(ValueError, match="slip_per_period"):
        v(bad)
    import motor_ai_sim.optimization.refine_proc as R
    with pytest.raises(ValueError, match="slip_per_period"):      # run_one refuses before any work, never int()-truncates
        R.run_one({}, 10.0, 48, 120.0, slip_per_period=bad)
    from motor_ai_sim.routes.simulation import get_fem_transient
    with pytest.raises(HTTPException) as e:                        # the route as the kernel seam calls it (no FastAPI parsing)
        get_fem_transient(slip_per_period=bad, n_steps_per_period=48)
    assert e.value.status_code == 422 and "slip_per_period" in e.value.detail


def test_a_whole_valued_q_is_accepted_and_none_stays_none():
    from motor_ai_sim.simulation.slip_ring import validate_slip_per_period as v
    assert v(None) is None and v(144) == 144 and v(144.0) == 144 and v(24) == 24 and v(2400) == 2400


def test_the_kernel_seam_refuses_a_fractional_or_text_q_with_a_failed_result():
    from motor_ai_sim.modules.solvers import EmTransientSolver
    for bad in (25.9, "bad"):
        out = EmTransientSolver().run({"slip_per_period": bad, "n_steps_per_period": 48})
        assert "slip_per_period" in str(out.model_dump() if hasattr(out, "model_dump") else out)


def _stamped(q):
    return {"T_avg_Nm": 1.0, "slip_per_period_requested": q, "slip_per_period_pole_pairs": 5,
            "slip_per_period_n_sectors": 1, "slip_per_period_gap_layers": 1.0}


@pytest.mark.parametrize("asked,internal", [(None, 48), (25, 48), (144, 144)])
def test_refinement_keeps_the_external_request_and_adds_no_key_when_none_was_asked(monkeypatch, asked, internal):
    """The refinement / probe re-solves stamp their effective ring as 'requested'; the public boundary puts the real request back."""
    import motor_ai_sim.simulation.fem_solver_2d as F
    monkeypatch.setattr(F, "_solve_with_gap_refinement", lambda kw, gap_refine=False: _stamped(internal))
    res = F.em_transient_eval(n_steps_per_period=48, n_periods=1.0, gamma_deg=0.0, I_phase_rms=10.0, slip_per_period=asked)
    if asked is None:
        assert not [k for k in res if k.startswith("slip_per_period")]
    else:
        assert res["slip_per_period_requested"] == asked              # 25 stays 25 although the effective ring is 48


def test_restore_helper_is_pure_on_a_result_without_the_keys():
    from motor_ai_sim.simulation.slip_ring import restore_request_provenance as r
    assert r({"a": 1}, None) == {"a": 1} and r({"a": 1}, 48) == {"a": 1, "slip_per_period_requested": 48}


def test_the_solver_reports_the_request_next_to_the_effective_ring():
    import motor_ai_sim.simulation.fem_solver_2d as F
    src = inspect.getsource(F._fem_transient_sliding_band_once)
    assert '"slip_per_period_requested"' in src and '"slip_nodes_per_period"' in src and "snap_slip_per_period" in src


def test_numpy_whole_numbers_are_accepted_and_numpy_fractions_refused():
    import numpy as np
    from motor_ai_sim.simulation.slip_ring import validate_slip_per_period as v
    assert v(np.int64(96)) == 96 and type(v(np.int64(96))) is int
    assert v(np.float64(96.0)) == 96
    for bad in (np.float64(25.9), np.float64("nan"), np.bool_(True)):
        with pytest.raises(ValueError):
            v(bad)
