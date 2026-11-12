"""The confirmation mail to the submitter and the derived "submitted" marker (owner 2026-11-12).  tmp_path only; the sender is a fake."""
from __future__ import annotations

import pytest

from motor_ai_sim import auth_email
from motor_ai_sim.development import models, routes, spec_confirmation as C, store
from tests.test_development_core import _all_unknown_draft

CUST = "cust@example.test"
TITLE = "Robot joint motor, 0.6 N·m, 2000 rpm"


def _filled(value):
    return {"status": "filled", "value": value}


def _sample_draft(unknown: bool = False) -> dict:
    d = _all_unknown_draft()
    f = d["fields"]
    f["key_points"] = _filled([{"name": "Continuous", "torque_Nm": 0.6, "speed_rpm": 2000, "duration_s": "continuous"},
                               {"name": "Peak", "torque_Nm": 1.2, "speed_rpm": 1000, "duration_s": 2}])
    f["1_6"] = _filled({"maximum_rpm": 3000})
    f["2_1"] = _filled({"supply_type": "battery", "chemistry": "NMC", "series_cells": 12, "voltage_min_V": 36,
                        "voltage_nominal_V": 44.4, "voltage_max_V": 50.4})
    f["3_1"] = _filled({"cooling_medium": "air", "airflow": {"mode": "Still air"}})
    f["3_2"] = _filled({"temperature_max_C": 40})
    f["4_1"] = _filled({"outer_diameter_mm": 90, "length_mm": 40})
    f["4_2"] = _filled({"active_mass_kg": 0.45, "complete_mass_kg": 0.65})
    # commercial and free-text answers that must never reach the mail
    d["production"]["target_price"] = _filled(1234)
    f["6_3"] = _filled({"amount": 777, "currency": "EUR"})
    f["additional_requirements"] = _filled("SECRET NOTE")
    if not unknown:       # every required field answered: nothing is "not known yet"
        f["1_1"] = _filled("Robot joint")
        f["1_2"] = _filled("Motor, one direction")
        f["2_4"] = _filled({"manufacturer_model": "Own design"})
        f["4_6"] = _filled({"axial_thrust_N": 0})
        f["6_1"] = _filled({"prototypes": 1})
        f["6_2"] = _filled({"simulation_date": "2026-12-01"})
        d["mission_profile_status"] = "filled"
        d["mission_profile"] = [{"phase": "Cycle", "torque_Nm": 0.6, "rpm": 2000, "duration_s": 60, "voltage_V": 44.4}]
    return d


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setattr(routes.platform_agreement, "require_accepted", lambda user: None)
    monkeypatch.setattr(routes, "_require_data_agreement", lambda user: None)
    monkeypatch.setattr(routes, "_notify_staff", lambda *a, **k: None)
    sent = []
    monkeypatch.setattr(auth_email, "send", lambda to, subject, body, **k: sent.append((to, subject, body, k)) or True)
    monkeypatch.setattr(auth_email, "app_url", lambda: "https://app.example.test")
    monkeypatch.setattr(C, "lang_for", lambda email: "en")
    user = {"email": CUST, "role": "admin"}
    project = routes.create_project_for(user, {"title": TITLE, "customer_org": "Org"})
    return user, project["id"], sent


def _save(pid, draft, revision):
    store.save_json(pid, "draft.json", {"draft": draft, "revision": revision, "schema_version": 2, "version": None})


def test_submit_sends_one_confirmation_in_english(env):
    user, pid, sent = env
    _save(pid, _sample_draft(), 1)
    assert routes.submit_spec_for(user, pid, 1)["version"]["version"] == 1
    assert len(sent) == 1
    to, subject, body, kw = sent[0]
    assert to == CUST and kw.get("kind") == "spec-submitted"
    assert subject == f"AeroStator: specification v1 submitted - {TITLE}"
    assert f'Your specification version 1 for "{TITLE}" was submitted on ' in body and " UTC." in body
    assert "Status: Specification submitted - your motor is being prepared for the calculation." in body
    for line in ("Continuous: 0.6 N·m at 2000 rpm, continuous", "Peak: 1.2 N·m at 1000 rpm, 2 s", "Maximum speed: 3000 rpm",
                 "Power supply: 12S NMC, 36 / 44.4 / 50.4 V", "Cooling conditions: Air (still air), ambient 40 °C",
                 "Size envelope: 90 x 40 mm", "Mass target: 0.45 kg active / 0.65 kg complete"):
        assert line in body
    assert "Not known yet" not in body
    assert "https://app.example.test/projects/" + pid + "/specification" in body
    assert body.rstrip().endswith("AeroStator support, support@aerostator.com")
    assert "engineer" not in body.lower() and "unsubscribe" not in body.lower()


def test_commercial_fields_and_notes_never_appear(env):
    user, pid, sent = env
    _save(pid, _sample_draft(), 1)
    routes.submit_spec_for(user, pid, 1)
    body = sent[0][2]
    for banned in ("1234", "777", "eur", "secret note", "price", "budget", "cost", "quote", "lead time"):
        assert banned not in body.lower()


def test_not_known_yet_count_line(env):
    user, pid, sent = env
    _save(pid, _sample_draft(unknown=True), 1)
    routes.submit_spec_for(user, pid, 1)
    assert "Not known yet: " in sent[0][2] and "field(s)" in sent[0][2]


def test_chinese_account_gets_chinese(env, monkeypatch):
    user, pid, sent = env
    monkeypatch.setattr(C, "lang_for", lambda email: "zh-CN")
    _save(pid, _sample_draft(), 1)
    routes.submit_spec_for(user, pid, 1)
    _to, subject, body, _kw = sent[0]
    assert subject == f"AeroStator：规格书第 1 版已提交 - {TITLE}"
    assert "状态：规格书已提交，您的电机正在准备计算。" in body
    assert "最高转速：3000 rpm" in body and "工程师" not in body
    assert body.rstrip().endswith("AeroStator support, support@aerostator.com")


def test_unchanged_submit_sends_nothing(env):
    user, pid, sent = env
    _save(pid, _sample_draft(), 1)
    routes.submit_spec_for(user, pid, 1)
    assert len(sent) == 1
    rev = store.load_json(pid, "draft.json", {})["revision"]
    assert routes.submit_spec_for(user, pid, rev).get("unchanged") is True
    assert len(sent) == 1


def test_mail_failure_never_fails_the_submit(env, monkeypatch):
    user, pid, sent = env

    def boom(*a, **k):
        raise RuntimeError("smtp down")
    monkeypatch.setattr(auth_email, "send", boom)
    _save(pid, _sample_draft(), 1)
    assert routes.submit_spec_for(user, pid, 1)["version"]["version"] == 1


def test_payload_marker_is_derived_and_status_is_stored_unchanged(env):
    user, pid, sent = env
    assert routes._summary(store.get_project(pid), user)["spec_submitted"] is None
    _save(pid, _sample_draft(), 1)
    routes.submit_spec_for(user, pid, 1)
    marker = routes._summary(store.get_project(pid), user)["spec_submitted"]
    assert marker["version"] == 1 and marker["at"].endswith("+00:00")
    assert store.get_project(pid)["status"] == "spec"
