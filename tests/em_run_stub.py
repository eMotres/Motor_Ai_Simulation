"""A stand-in for ``routes.thermal.latest_em_run`` in tests whose solver is
itself faked (they pin a route's history, refusals or record shape, not the
Electromagnetic lookup — that is ``tests/test_thermal_latest_em_run.py``).

Since 2026-09-30 the Thermal tab's routes take the LATEST Electromagnetic run
of the loaded machine and adopt its point; a test that fakes the conduction
solve has no real run to find, so it fakes the run too — at the point the
test is about.
"""
from __future__ import annotations

from typing import Any, Dict


def fake_em_run(*, run_id: str = "2026-09-30T09:00:00", **over) -> Dict[str, Any]:
    point = {
        "gamma_deg": 0.0, "I_phase_rms": 20.0, "rpm": 3000.0,
        "coil_temp_c": 120.0, "magnet_temp_c": None,
        "n_steps_per_period": 12, "n_periods": 1.0, "mesh_size_mm": 3.0,
        "min_size_mm": 0.3, "outer_air_factor": 1.3, "n_sectors": 2,
        "component_mesh": "", "op_mode": "motor",
    }
    point.update(over)
    summary = {"run_id": run_id, "computed_at": run_id + "+00:00",
               **{k: point[k] for k in ("I_phase_rms", "gamma_deg", "rpm",
                                        "coil_temp_c", "magnet_temp_c",
                                        "n_steps_per_period", "n_periods",
                                        "mesh_size_mm", "min_size_mm",
                                        "n_sectors", "op_mode")},
               "demag": False, "drive": "current", "solve_time_s": 1.0,
               "inputs_recorded": True}
    return {"key": ("fake", run_id), "entry": {}, "fields": {},
            "run_id": run_id, "point": point, "summary": summary}


def patch_latest_em_run(monkeypatch, **point) -> Dict[str, Any]:
    """Make ``latest_em_run`` answer one fake run; returns it."""
    from motor_ai_sim.routes import thermal as th

    run = fake_em_run(**point)
    monkeypatch.setattr(th, "latest_em_run", lambda geo_ov=None: run,
                        raising=True)
    return run
