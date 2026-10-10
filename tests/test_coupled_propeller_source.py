"""Route-level regression for propeller cooling in the ordinary coupled run.

Both solver halves are replaced with recorders: this exercises the real
``POST /api/coupled/run`` request validation and handoff without starting FEM.
"""
from __future__ import annotations

import pytest

from tests.test_coupled import COOLING, EM_BODY


PROP_SETTINGS = {
    **COOLING,
    "coolMode": "air",
    "airSpeedSource": "propeller",
    "propellerId": "tmotor_g32x11",
    "propellerPosition": "behind_hub",
    "propellerContextKey": "vadim@example.test|D85|L13|reference-85-l13",
    "ambientT": "27",
}


@pytest.fixture
def client():
    """A local in-process client; no external API or server is contacted."""
    from fastapi.testclient import TestClient
    from motor_ai_sim.api import app
    return TestClient(app)


@pytest.fixture
def coupled_halves(monkeypatch):
    """Record the real route's EM and thermal calls, never solving either half."""
    from motor_ai_sim.routes import coupled as cp
    from motor_ai_sim.routes import simulation as sim
    calls = {"em": [], "thermal": [], "context": []}

    def record_context(settings, authorization=None, **kw):
        calls["context"].append((dict(settings), authorization, dict(kw)))

    def context_check(settings, authorization=None, **kw):
        record_context(settings, authorization, **kw)
        # Model a valid server-side account/family/allowlist binding; this test
        # focuses on the standard coupled route's solver handoff.
        return None

    def em_run(body, *, coil_temp_c, magnet_temp_c, **kwargs):
        calls["em"].append((dict(body), coil_temp_c, magnet_temp_c, kwargs))
        return {
            "computed_at": "fake-em-pass",
            "summary": {
                "P_loss_total_W": 100.0,
                "T_em_avg_Nm": 5.0,
                "coil_temp_C": coil_temp_c,
                "efficiency_shaft": 0.88,
            },
        }

    def thermal_solve(body, cooling, *, coil_temp_c, magnet_temp_c, rpm,
                      **kwargs):
        calls["thermal"].append((dict(body), dict(cooling), rpm, kwargs))
        return {
            "ok": True,
            "components": {
                "winding": {"avg": 120.0, "max": 130.0},
                "magnet": {"avg": 90.0, "max": 95.0},
            },
        }

    monkeypatch.setattr(sim, "_effective_rpm", lambda rpm=None: 7777.0)
    monkeypatch.setattr("motor_ai_sim.thermal_settings.propeller_cooling_context_issue",
                        context_check)
    monkeypatch.setattr(cp, "_em_run", em_run)
    monkeypatch.setattr(cp, "_thermal_solve", thermal_solve)
    monkeypatch.setattr(cp, "_attach_coupling", lambda em, block: False)
    monkeypatch.setattr(cp, "_remember_last", lambda out, **kwargs: None)
    calls["record_context"] = record_context
    return calls


def test_coupled_run_passes_propeller_settings_and_effective_rpm_to_thermal(
        client, coupled_halves):
    """The normal coupled endpoint uses the selected propeller at resolved RPM."""
    body = {
        **EM_BODY,
        "thermal_settings": PROP_SETTINGS,
        "max_iter": 1,
        "cold_constants": False,
        "fresh": True,
    }

    response = client.post("/api/coupled/run", json=body)

    assert response.status_code == 200, response.text[:1000]
    calls = coupled_halves
    assert len(calls["context"]) == 1
    checked_settings, _, _ = calls["context"][0]
    assert checked_settings["airSpeedSource"] == "propeller"
    assert checked_settings["propellerId"] == "tmotor_g32x11"
    assert len(calls["em"]) == 1
    assert len(calls["thermal"]) == 1
    _, cooling, rpm, _ = calls["thermal"][0]
    assert rpm == pytest.approx(7777.0)
    assert cooling["cooling_mode"] == "air"
    assert cooling["air_speed_source"] == "propeller"
    assert cooling["propeller_id"] == "tmotor_g32x11"
    assert cooling["ambient_temp"] == pytest.approx(27.0)


def test_coupled_run_refuses_stale_propeller_context_before_em(
        client, monkeypatch, coupled_halves):
    def stale_context(settings, authorization=None, **kw):
        coupled_halves["record_context"](settings, authorization, **kw)
        return "propeller selection belongs to a different motor or account"

    monkeypatch.setattr(
        "motor_ai_sim.thermal_settings.propeller_cooling_context_issue",
        stale_context)
    body = {
        **EM_BODY,
        "thermal_settings": PROP_SETTINGS,
        "max_iter": 1,
        "cold_constants": False,
        "fresh": True,
    }

    response = client.post("/api/coupled/run", json=body)

    assert response.status_code == 422, response.text[:1000]
    assert response.json()["detail"]["error_code"] == "propeller_cooling_context"
    assert coupled_halves["context"]
    assert coupled_halves["em"] == []
    assert coupled_halves["thermal"] == []
