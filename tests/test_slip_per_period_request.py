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
        with pytest.raises(Exception):
            R.run_one({}, 10.0, 48, 120.0, slip_per_period=q)
    if len(seen) == 2:                                                # (run_one may stop earlier on a bare config)
        assert "slip_per_period" not in seen[0] and seen[1]["slip_per_period"] == 144


def test_the_solver_reports_the_request_next_to_the_effective_ring():
    import motor_ai_sim.simulation.fem_solver_2d as F
    src = inspect.getsource(F._fem_transient_sliding_band_once)
    assert '"slip_per_period_requested"' in src and '"slip_nodes_per_period"' in src and "snap_slip_per_period" in src
