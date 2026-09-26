"""Task 6 — the Thermal tab takes its losses from the coupled loop's passes.

THE DEFECT: Thermal Solve (``GET /api/thermal/field``) refused
``no_electromagnetic_run`` at the coupled loop's own S1 point (20.4 A, coil
200.2 °C) minutes after the loop had solved it.  Every coupled pass is an
ordinary Electromagnetic run, but the run snapshot store keeps ONE entry and the
loop makes more runs after its answer, so the pass the record reported was gone
by the time the Thermal tab asked — and the refusal printed the current at
0.1 A, so it could not even say which point the loop HAD solved.

What is pinned here:

  (a) a pass filed with ``register_coupled_pass`` answers a Thermal request at
      its exact point, ``loss_source.kind == "coupled_pass"``, with no EM run;
  (b) a point that does not match refuses in ONE line, names the requested
      point at the key's precision, and names the coupled passes of the machine
      that ARE filed;
  (c) a PWM pass never answers a sine-current request (it is named, labelled);
  (d) a pass without a per-element loss map is not filed;
  (e) the coupled loop files its final pass and its pass at the limit, at the
      temperatures those passes were solved at, and lists them in
      ``em_passes``.

The loop's EM and thermal halves are faked exactly as
``tests/test_coupled_limited_state`` fakes them.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from tests.test_coupled_limited_state import LOOP_BODY, _fake, _ttl_block


def _fake_map(n_tri: int = 4, *, with_losses: bool = True) -> dict:
    return {"ok": True, "from_transient": True, "n_triangles": n_tri,
            "vertices": [[0.0, 0.0], [0.01, 0.0], [0.0, 0.01], [0.01, 0.01]],
            "triangles": [[0, 1, 2]] * n_tri,
            "domain_per_tri": [1] * n_tri,
            "loss_density_per_tri": ([1.0e5] * n_tri if with_losses else []),
            "P_cu_W": 12.0}


POINT = dict(gamma_deg=0.0, I_phase_rms=20.44, n_steps_per_period=24,
             n_periods=1.0, mesh_size_mm=3.0, min_size_mm=0.3,
             outer_air_factor=1.3, n_sectors=1, coil_temp_c=200.2,
             component_mesh="")


@pytest.fixture
def registry(monkeypatch, tmp_path):
    """The thermal stores, empty and off the user's disk, and the run snapshot
    store reads as empty."""
    from motor_ai_sim.routes import thermal as th

    monkeypatch.setattr(th, "_loss_maps_path",
                        lambda: str(tmp_path / ".loss_maps.pkl"))
    monkeypatch.setattr(th, "_LOSS_MAPS_LOADED", True, raising=True)
    th._LOSS_MAPS.clear()
    th._COUPLED_PASSES.clear()
    monkeypatch.setattr(th, "_snapshot_loss_entry",
                        lambda probe: (None, None, "no run stored (test)"))
    yield th
    th._COUPLED_PASSES.clear()
    th._LOSS_MAPS.clear()


@pytest.fixture
def no_runs(monkeypatch, registry):
    """…and any EM or coupled run started by the lookup is a test failure:
    the thermal solve never starts one itself."""
    from motor_ai_sim.routes import coupled as cp
    from motor_ai_sim.routes import simulation as sim

    def _never(*_a, **_k):
        raise AssertionError("the thermal lookup started an EM/coupled run")

    monkeypatch.setattr(sim, "get_fem_transient", _never)
    monkeypatch.setattr(sim, "get_fem_field2d", _never)
    monkeypatch.setattr(cp, "_run", _never)
    return registry


def _lookup(th, **over):
    kw = {**POINT, **over}
    return th._em_loss_map(**kw, geo=None, geo_ov=None)


def _register(th, phase="s1_verify", **over):
    kw = {**POINT, **over}
    drive = kw.pop("drive", None)
    exc = kw.pop("excitation", None)
    probe = th.coupled_pass_probe(**kw, drive=drive, excitation=exc)
    return th.register_coupled_pass(phase=phase, probe=probe, em=_fake_map(),
                                    run_id="2026-09-26T12:00:00")


# ---------------------------------------------------------------------------
# (a) the exact point is answered from the filed pass
# ---------------------------------------------------------------------------

def test_a_filed_pass_answers_its_exact_point_without_an_em_run(no_runs):
    th = no_runs
    point = _register(th)
    assert point["I_phase_rms"] == 20.44 and point["coil_temp_c"] == 200.2
    assert point["n_steps_per_period"] == 24 and point["drive"] == "current"

    em, src = _lookup(th)
    assert src["kind"] == "coupled_pass", src
    assert src["phase"] == "s1_verify"
    assert src["run_id"] == "2026-09-26T12:00:00"
    assert "no electromagnetic solve ran" in src["note"]
    assert em["loss_density_per_tri"] == [1.0e5] * 4


def test_the_filed_pass_survives_later_runs_and_persists(registry, tmp_path):
    """Nothing the EM tab does afterwards touches this store; it is on disk."""
    import pickle
    import time

    th = registry
    _register(th)
    time.sleep(0.3)                 # the persist is a daemon thread
    p = tmp_path / ".thermal_coupled_passes.pkl"
    assert p.exists()
    with open(p, "rb") as fh:
        d = pickle.load(fh)
    assert len(d) == 1 and next(iter(d.values()))["phase"] == "s1_verify"


# ---------------------------------------------------------------------------
# (b) a miss refuses in one line, naming the point AND the filed passes
# ---------------------------------------------------------------------------

def test_a_near_miss_is_refused_in_one_line_naming_both_points(no_runs):
    th = no_runs
    _register(th)
    with pytest.raises(HTTPException) as ei:
        _lookup(th, I_phase_rms=20.4)            # the record's rounded display
    d = ei.value.detail
    assert ei.value.status_code == 422
    assert d["error_code"] == "no_electromagnetic_run"
    assert d["invalid_parameters"] == []
    msg = d["error"]
    assert "\n" not in msg
    assert "no Electromagnetic run of this machine at I = 20.4 A" in msg, msg
    assert "coil 200.2 °C" in msg and "24 steps/period" in msg, msg
    assert "S1 verification pass at I = 20.44 A" in msg, msg


def test_the_refusal_names_the_current_at_the_keys_precision(no_runs):
    th = no_runs
    with pytest.raises(HTTPException) as ei:
        _lookup(th, I_phase_rms=20.44)
    msg = ei.value.detail["error"]
    assert "I = 20.44 A" in msg, msg
    assert "coupled loop" not in msg          # nothing filed, nothing named


# ---------------------------------------------------------------------------
# (c) a PWM pass is named, never served as the sine point
# ---------------------------------------------------------------------------

def test_a_pwm_pass_never_answers_a_sine_current_request(no_runs):
    th = no_runs
    _register(th, phase="pwm_final", drive="inverter",
              excitation="48/20000/0.01/0.7/0.002/0.5")
    with pytest.raises(HTTPException) as ei:
        _lookup(th)
    msg = ei.value.detail["error"]
    assert "PWM final pass at I = 20.44 A" in msg, msg
    assert "inverter drive — not a sine-current map" in msg, msg


# ---------------------------------------------------------------------------
# (d) no per-element map, no filing
# ---------------------------------------------------------------------------

def test_a_pass_without_a_per_element_map_is_not_filed(registry):
    th = registry
    probe = th.coupled_pass_probe(**POINT)
    with pytest.raises(ValueError):
        th.register_coupled_pass(phase="final", probe=probe,
                                 em=_fake_map(with_losses=False))
    assert not th._COUPLED_PASSES


# ---------------------------------------------------------------------------
# (e) the loop files what it reports
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    return TestClient(app)


def _fake_with_capture(monkeypatch, ttl=None):
    """``_fake`` plus a thermal half that fills ``capture`` the way
    ``solve_thermal_field(_em_capture=…)`` does."""
    from motor_ai_sim.routes import coupled as cp

    seen = _fake(monkeypatch, ttl=ttl)
    inner = cp._thermal_solve

    def _th(body, cooling, *, capture=None, **k):
        if capture is not None:
            capture["em"] = _fake_map()
        return inner(body, cooling, **k)

    monkeypatch.setattr(cp, "_thermal_solve", _th, raising=True)
    return seen


def test_the_loop_files_its_final_pass_at_its_own_point(client, monkeypatch,
                                                        registry):
    th = registry
    seen = _fake_with_capture(monkeypatch, ttl=_ttl_block())
    r = client.post("/api/coupled/run",
                    json={**LOOP_BODY, "max_iter": 3, "tol_k": 1.0,
                          "cold_constants": False})
    assert r.status_code == 200, r.text[:800]
    c = r.json()["coupling"]
    finals = [p for p in c["em_passes"] if p["phase"] == "final"]
    assert len(finals) == 1 and finals[0]["registered"], c["em_passes"]
    # …at the temperature the LAST pass was solved at, not the map's answer.
    assert finals[0]["point"]["coil_temp_c"] == round(seen["coil_in"][-1], 1)
    assert finals[0]["point"]["I_phase_rms"] == round(
        float(LOOP_BODY["I_phase_rms"]), 2)
    assert len(th._COUPLED_PASSES) == 1


def test_the_loop_files_its_pass_at_the_limit(client, monkeypatch, registry):
    """The limit pass has no thermal solve of its own (its map is rescaled),
    so its loss map is replayed from the snapshot it just stored — a lookup
    through ``_em_loss_map``, never a solve."""
    th = registry
    seen = _fake_with_capture(monkeypatch, ttl=_ttl_block())
    looked_up = []

    def _lookup_map(**kw):
        looked_up.append(kw["coil_temp_c"])
        return _fake_map(), {"kind": "simulation_run"}

    monkeypatch.setattr(th, "_em_loss_map", _lookup_map)
    r = client.post("/api/coupled/run",
                    json={**LOOP_BODY, "max_iter": 3, "tol_k": 1.0,
                          "cold_constants": False, "solve_to": "limits"})
    assert r.status_code == 200, r.text[:800]
    c = r.json()["coupling"]
    phases = {p["phase"]: p for p in c["em_passes"]}
    assert set(phases) == {"final", "limit"}, c["em_passes"]
    assert all(p["registered"] for p in phases.values()), c["em_passes"]
    assert phases["limit"]["point"]["coil_temp_c"] == seen["coil_in"][-1]
    assert looked_up == [seen["coil_in"][-1]]
    assert len(th._COUPLED_PASSES) == 2


def test_the_s1_verification_pass_is_filed_and_answers_thermal(
        client, monkeypatch, registry):
    """The case in the brief: the Thermal tab asks for the S1 point the record
    reports, and is answered from the loop's own verification pass."""
    from tests.test_coupled_continuous_solve_to import (_RATING_BLOCK,
                                                        _fake_rating,
                                                        _fake_verify)
    from motor_ai_sim.routes import coupled as cp
    from motor_ai_sim.routes.simulation import _parse_geo_override

    th = registry
    _fake_rating(monkeypatch, block=dict(_RATING_BLOCK))
    _fake_verify(monkeypatch, winding_max_at=lambda i: 100.0,
                 magnet_max_at=lambda i: 148.0)
    inner = cp._thermal_solve

    def _th(body, cooling, *, capture=None, **k):
        if capture is not None:
            capture["em"] = _fake_map()
        return inner(body, cooling, **k)

    monkeypatch.setattr(cp, "_thermal_solve", _th, raising=True)
    r = client.post("/api/coupled/run",
                    json={**LOOP_BODY, "max_iter": 3, "tol_k": 1.0,
                          "cold_constants": False, "solve_to": "continuous"})
    assert r.status_code == 200, r.text[:800]
    c = r.json()["coupling"]
    s1 = [p for p in c["em_passes"] if p["phase"] == "s1_verify"]
    assert len(s1) == 1 and s1[0]["registered"], c["em_passes"]
    pt = s1[0]["point"]
    assert pt["I_phase_rms"] == round(_RATING_BLOCK["I_cont_A_rms"], 2)
    assert pt["coil_temp_c"] == c["coil_temp_c"] == 103.0
    assert pt["magnet_temp_c"] == c["magnet_temp_c"]

    # The Thermal tab, at exactly that point: answered, no EM run.
    em, src = th._em_loss_map(
        gamma_deg=float(LOOP_BODY.get("gamma_deg") or 0.0),
        I_phase_rms=c["continuous_rating"]["I_cont_A_rms"],
        n_steps_per_period=int(LOOP_BODY["n_steps_per_period"]),
        n_periods=float(LOOP_BODY.get("n_periods") or 1.0),
        mesh_size_mm=float(LOOP_BODY.get("mesh_size_mm") or 3.0),
        min_size_mm=float(LOOP_BODY.get("min_size_mm") or 0.3),
        outer_air_factor=float(LOOP_BODY.get("outer_air_factor") or 1.3),
        n_sectors=int(LOOP_BODY.get("n_sectors") or 1),
        coil_temp_c=c["coil_temp_c"], magnet_temp_c=c["magnet_temp_c"],
        component_mesh=str(LOOP_BODY.get("component_mesh") or ""),
        geo=LOOP_BODY["geo"], geo_ov=_parse_geo_override(LOOP_BODY["geo"]),
        op_mode=LOOP_BODY.get("mode"))
    assert src["kind"] == "coupled_pass" and src["phase"] == "s1_verify"


def test_a_pass_at_another_speed_is_filed_under_its_own_speed(no_runs):
    """The Thermal probe keys on the config's speed; a coupled body may run at
    its own.  Filed under the speed it was SOLVED at, it answers only that."""
    from motor_ai_sim.routes.simulation import _effective_rpm

    th = no_runs
    other = _effective_rpm(None) + 1000.0
    point = _register(th, rpm=other)
    assert point["rpm"] == round(other, 3)
    with pytest.raises(HTTPException) as ei:
        _lookup(th)
    msg = ei.value.detail["error"]
    assert format(int(round(other)), ",d").replace(",", " ") + " rpm" in msg, msg
