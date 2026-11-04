"""The Simulation tab's "with free-run losses" figures: the newest free-run test's measured | model no-load loss per bench
speed (``comparison.freerun_loss``) and the routes that serve them, with the same read gates as the tests themselves."""
from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from motor_ai_sim.motor_tests import comparison, model_source, models
from motor_ai_sim.motor_tests import routes as MTR
from motor_ai_sim.motor_tests import store as MTS

LIB = {"magnet": {"GRADE_150C": {"grade": "GRADE", "temperature_c": 150, "Br": 1.20}}}


def _cfg_doc():
    # one no-load duty at 3000 rpm: EM no-load loss 300 (hysteresis) + 100 (eddy) = 400 W at its own speed
    return {"winding": {"star_delta": "star"}, "materials": {"magnet": "GRADE_150C"}, "duties": [
        {"name": "no-load 3000", "rpm": 3000, "current_arms": 0, "summary": {
            "rpm": 3000, "I_phase_rms_A": 0.0, "V_line_peak_V": 120.0, "V_line_rms_V": 84.0,
            "P_core_terms": {"stator": {"hysteresis_W": 300.0, "eddy_W": 100.0, "excess_W": 0.0}},
            "P_core_W": 400.0, "P_solid_W": 0.0, "P_mag_W": 0.0}}]}


def _mech(rpm, temp):
    return {"bearings_w": 0.01 * rpm, "windage_w": 11.0 * (rpm / 3000.0) ** 2}      # 30 + 11 = 41 W at 3000 rpm


_mech.has_bearings = True


def _model():
    return model_source.from_docs({}, _cfg_doc(), {}, LIB, mech=_mech)


def _model_w(rpm):
    r = rpm / 3000.0
    m = _mech(rpm, None)
    return 300.0 * r + 100.0 * r * r + m["bearings_w"] + m["windage_w"]


def _rec(date, rows, created_at="2026-01-01T00:00:00Z", kind="free_run"):
    rec = models.normalize({"kind": kind, "date": date, "rows": rows}, who="eng@example.test")
    rec["created_at"] = created_at
    return rec


def test_model_figure_is_the_one_the_tests_tab_compares():
    """734 W measured against 441 W of the model at 3000 rpm: the very rows of the "No-load loss (shaft power)" group."""
    rec = _rec("2026-10-30", [{"rpm": 2000, "shaft_power_w": 450}, {"rpm": 3000, "shaft_power_w": 734}])
    out = comparison.freerun_loss({"tests": [rec]}, _model(), "D", "C")
    assert out["available"] and out["test_id"] == rec["id"] and out["date"] == "2026-10-30"
    p3000 = out["points"][1]
    assert p3000["rpm"] == 3000 and p3000["measured_w"] == 734 and p3000["model_w"] == pytest.approx(441.0)
    assert out["points"][0]["model_w"] == pytest.approx(_model_w(2000))
    # the same number the comparison table prints, not a second model
    loss = [g for g in comparison.compare_record(rec, _model(), "D", "C")["groups"] if g["key"] == "loss"][0]
    assert [r["model"] for r in loss["rows"]] == [pytest.approx(p["model_w"]) for p in out["points"]]


def test_no_model_figure_without_a_noload_duty_and_no_invention():
    rec = _rec("2026-10-30", [{"rpm": 3000, "shaft_power_w": 734}])
    out = comparison.freerun_loss({"tests": [rec]}, model_source.from_docs({}, {"duties": []}, {}, LIB), "D", "C")
    assert out["available"] and out["points"] == [{"rpm": 3000.0, "measured_w": 734.0, "model_w": None}]


def test_the_newest_free_run_by_date_wins_and_other_kinds_are_ignored():
    old = _rec("2026-09-01", [{"rpm": 3000, "shaft_power_w": 900}])
    new = _rec("2026-10-30", [{"rpm": 3000, "shaft_power_w": 734}])
    same_day_later = _rec("2026-10-30", [{"rpm": 3000, "shaft_power_w": 750}], created_at="2026-10-30T12:00:00Z")
    load = _rec("2026-11-01", [{"rpm": 3000, "torque_nm": 10}], kind="load")
    out = comparison.freerun_loss({"tests": [old, new, load]}, _model(), "D", "C")
    assert out["test_id"] == new["id"] and out["points"][0]["measured_w"] == 734
    both = comparison.freerun_loss({"tests": [new, same_day_later, old]}, _model(), "D", "C")
    assert both["test_id"] == same_day_later["id"]


def test_no_free_run_with_a_measured_power_means_not_available():
    no_power = _rec("2026-10-30", [{"rpm": 3000, "v_ll_pp_v": 200}])
    load = _rec("2026-10-30", [{"rpm": 3000, "torque_nm": 10}], kind="load")
    assert comparison.freerun_loss({"tests": []}, _model(), "D", "C") == {"available": False}
    assert comparison.freerun_loss({"tests": [no_power, load]}, _model(), "D", "C") == {"available": False}


# -- the routes -----------------------------------------------------------------------------------------------------

IDENTITIES = {
    "engineer": {"email": "engineer@example.test", "role": "engineer"},
    "customer": {"email": "customer@example.test", "role": "customer"},
    "owner": {"email": "owner@example.test", "role": "customer"},
    "stranger": {"email": "stranger@example.test", "role": "customer"},
}


@pytest.fixture()
def project(tmp_path, monkeypatch):
    from motor_ai_sim import duty_results
    from motor_ai_sim.development import access, motors, store
    from motor_ai_sim.routes import family
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MOTRES_MOTOR_TESTS_ROOT", str(tmp_path / "motor_tests"))
    (tmp_path / "projects").mkdir()
    store.create_project({"id": "alpha", "members": [{"email": "customer@example.test", "role": "customer"},
                                                     {"email": "owner@example.test", "role": "owner"}],
                          "linked_motors": [{"die": "die", "config": "opt1"}, {"die": "die", "config": "opt2"},
                                            {"die": "die", "config": "nogrant"}]})
    store.save_index({"motors": {motors._key("die", c): {"project_id": "alpha"} for c in ("opt1", "opt2", "nogrant")}})
    monkeypatch.setattr(access, "require_signed_in", lambda request: IDENTITIES[request.headers.get("x-user")])
    die_dir = tmp_path / "catalog"
    die_dir.mkdir()
    for c in ("opt1", "opt2", "nogrant"):
        (die_dir / f"{c}.yaml").write_text("duties: []\n", encoding="utf-8")
    monkeypatch.setattr(family, "_iter_die_entries", lambda: [{"name": "die", "dir": die_dir, "layer": "shared"}])
    monkeypatch.setattr(family, "_die_dir", lambda die: die_dir)
    monkeypatch.setattr(family, "_cfg_file", lambda die, config: die_dir / f"{config}.yaml")

    def require(die, authorization, cfg=None, *args, **kwargs):
        if cfg == "nogrant":
            raise HTTPException(404, detail="not found")
    monkeypatch.setattr(family, "_require_die_access", require)
    monkeypatch.setattr(motors, "_identity", lambda request: IDENTITIES[request.headers.get("x-user")])
    monkeypatch.setattr(duty_results, "get", lambda die, cfg: {})
    monkeypatch.setattr(model_source, "collect", lambda die, config: _model())
    rows = [{"rpm": 2000, "shaft_power_w": 450}, {"rpm": 3000, "shaft_power_w": 734}]
    for c in ("opt1", "nogrant"):
        MTS.mutate("die", c, lambda d: d["tests"].append(
            {"id": "t_free", "kind": "free_run", "date": "2026-10-30", "conditions": {}, "rows": rows}))
    MTS.mutate("die", "opt2", lambda d: d["tests"].append(
        {"id": "t_par", "kind": "parameters", "date": "2026-10-30", "conditions": {},
         "resistance": {"temp_c": 20.0, "pairs": [{"pair": "1-2", "value_mohm": 17.0}]}}))
    app = FastAPI()
    app.include_router(MTR.project_router)
    app.include_router(motors.router)
    return TestClient(app), store


def _get(client, who, config="opt1"):
    return client.get(f"/api/projects/alpha/motors/tests/freerun-loss?die=die&config={config}",
                      headers={"x-user": who})


def test_member_view_staff_always_a_customer_only_behind_the_tests_flag(project):
    client, store = project
    for who in ("engineer", "owner"):
        r = _get(client, who)
        assert r.status_code == 200 and r.json()["available"] is True
        assert r.json()["points"][1] == {"rpm": 3000.0, "measured_w": 734.0, "model_w": pytest.approx(441.0)}
    # a customer without the flag gets exactly what a motor without a free-run test gets: nothing, no bench numbers
    hidden = _get(client, "customer")
    assert hidden.status_code == 200 and hidden.json() == {"available": False}
    assert _get(client, "owner", "opt2").json() == {"available": False}              # tests, but no free-run
    assert _get(client, "stranger").status_code == 404                                # not a member of the project
    MTS.mutate("die", "opt1", lambda d: d["settings"].update(tests_visible_to_customer=True))
    r = _get(client, "customer")
    assert r.status_code == 200 and r.json()["available"] is True
    audit = store._file("alpha", "audit.jsonl").read_text(encoding="utf-8")
    assert audit.count("motor.tests.read") == 1                                        # the customer's read, not staff's


def test_member_view_applies_the_project_motor_gate(project):
    client, _store = project
    assert _get(client, "engineer", "nogrant").status_code == 404                      # no grant on the motor
    assert _get(client, "engineer", "missing").status_code == 404                      # not linked to the project
