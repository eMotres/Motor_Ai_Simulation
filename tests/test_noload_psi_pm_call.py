"""``noload_psi_pm`` must reach its solve — and hand it the loaded connection.

On 2026-09-08 the cache key moved into ``psipm_cache_key`` and the local
``_conn_pm`` the solve call still referenced went with it: every live run's
bench Ld/Lq probe died on a NameError ("the summary will carry none") for nine
hours.  Pinned here without a FEM solve: the transient evaluator is stubbed and
the call is inspected.
"""
from __future__ import annotations

import pytest

from motor_ai_sim.simulation import fem_solver_2d as F

GEO = {"num_slots": 12, "num_poles": 14, "stator_diameter": 30.0,
       "num_wires_per_slot": 6, "wire_parallel": 1, "wire_split": 1}
WIND = {"connection": "2S", "n_parallel": 1, "n_series": 2}


@pytest.fixture
def stubbed_solve(monkeypatch, tmp_path):
    calls = []

    def _fake_eval(**kw):
        calls.append(kw)
        return {"picard_converged": True,
                "rotor_angle_deg": [0.0, 60.0, 120.0, 180.0, 240.0, 300.0],
                "psi_A_Wb": [1e-3] * 6, "psi_B_Wb": [-5e-4] * 6,
                "psi_C_Wb": [-5e-4] * 6}
    monkeypatch.setattr(F, "em_transient_eval", _fake_eval)
    # A private, empty disk cache: the answer must come from the (stubbed)
    # solve, not from a key some earlier session filed.
    monkeypatch.setattr(F, "_daxis_disk_path", lambda: str(tmp_path / "daxis.json"))
    return calls


def test_the_no_load_solve_is_reached_and_carries_the_connection(stubbed_solve):
    psi_pm, psi_q0 = F.noload_psi_pm(GEO, WIND, pole_pairs=7, n_sectors=2,
                                     daxis_deg=60.0, connection="2S")
    assert len(stubbed_solve) == 1, "exactly one calibration solve"
    assert stubbed_solve[0].get("connection") == "2S"
    assert stubbed_solve[0]["I_phase_rms"] == 0.0          # NO LOAD
    assert psi_pm == pytest.approx(psi_pm) and psi_q0 == pytest.approx(psi_q0)


def test_the_winding_block_supplies_the_connection_when_the_call_gives_none(stubbed_solve):
    F.noload_psi_pm(GEO, WIND, pole_pairs=7, n_sectors=2, daxis_deg=60.0)
    assert stubbed_solve[0].get("connection") == "2S"


def test_no_connection_anywhere_means_the_evaluator_decides(stubbed_solve):
    F.noload_psi_pm(GEO, {}, pole_pairs=7, n_sectors=2, daxis_deg=60.0)
    assert "connection" not in stubbed_solve[0]
