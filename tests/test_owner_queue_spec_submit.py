"""A specification submit puts one owner-queue decision: simulate / optimise / not now (owner 2026-11-25).  tmp_path only; mail is a fake."""
from __future__ import annotations

import pytest

from motor_ai_sim import auth_email, owner_queue as OQ, owner_queue_events as EV
from motor_ai_sim.development import routes, spec_confirmation as C, store
from tests.test_spec_submit_confirmation import TITLE, _sample_draft, _save

CUST = "cust@example.test"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MOTRES_OWNER_QUEUE_DIR", str(tmp_path / "oq"))
    monkeypatch.setattr(routes.platform_agreement, "require_accepted", lambda user: None)
    monkeypatch.setattr(routes, "_require_data_agreement", lambda user: None)
    monkeypatch.setattr(routes, "_notify_staff", lambda *a, **k: None)
    monkeypatch.setattr(auth_email, "send", lambda *a, **k: True)
    monkeypatch.setattr(auth_email, "app_url", lambda: "https://app.example.test")
    monkeypatch.setattr(C, "lang_for", lambda email: "en")
    user = {"email": CUST, "role": "admin"}
    project = routes.create_project_for(user, {"title": TITLE, "customer_org": "Org"})
    return user, project["id"]


def test_new_version_creates_one_decision_unchanged_none(env):
    user, pid = env
    _save(pid, _sample_draft(), 1)
    assert routes.submit_spec_for(user, pid, 1)["version"]["version"] == 1
    items = OQ.list_items()
    assert len(items) == 1
    it = items[0]
    assert it["kind"] == "decision" and it["asked_by"] == "app" and it["project_id"] == pid and it["state"] == "open"
    assert it["token_name"] == OQ.SPEC_SUBMIT_TOKEN
    assert [o["label"] for o in it["options"]] == ["Simulate on existing sizes", "Optimise geometry", "Not now"]
    assert not any(o["recommended"] for o in it["options"])               # the form stores no diameter mode yet
    assert it["question"] == f"Specification v1 submitted: {TITLE}. Start?"
    d = it["detail"]
    assert f"Submitted by: {CUST} (staff)" in d and "Company: Org" in d and "Completeness: " in d
    assert f"https://app.example.test/projects/{pid}/specification" in d
    for secret in ("1234", "777", "SECRET NOTE"):                    # commercial and free-text answers never reach the item
        assert secret not in d
    assert "Changed since" not in d                                    # the first version has nothing to compare
    assert OQ.counts()["open_spec_submits"] == 1 and OQ.counts()["open_blocking"] == 1
    # a second submit with the same content: "unchanged", no version and no item
    cur = store.load_json(pid, "draft.json", {})
    assert routes.submit_spec_for(user, pid, int(cur["revision"])).get("unchanged") is True
    assert len(OQ.list_items()) == 1


def test_customer_submit_is_marked_customer(env, monkeypatch):
    user, pid = env
    _save(pid, _sample_draft(), 1)
    monkeypatch.setattr(routes.access, "spec_editor_for", lambda u, p: ({"email": "c2@example.test", "role": "customer"}, None, None))
    routes.submit_spec_for(user, pid, 1)
    assert "Submitted by: c2@example.test (customer)" in OQ.list_items()[0]["detail"]


def test_queue_failure_never_blocks_the_submit(env, monkeypatch, tmp_path):
    user, pid = env
    _save(pid, _sample_draft(), 1)
    (tmp_path / "oq").mkdir()
    (tmp_path / "oq" / "items.jsonl").write_text("garbage\n")             # a damaged store raises StoreDamaged
    assert routes.submit_spec_for(user, pid, 1)["version"]["version"] == 1
    monkeypatch.setattr(OQ, "create_item", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    EV.spec_submitted(user, pid, {"version": 2, "draft": {}}, {})           # swallowed and logged


def test_agent_tokens_never_see_these_items(env):
    user, pid = env
    _save(pid, _sample_draft(), 1)
    routes.submit_spec_for(user, pid, 1)
    assert not OQ._NAME_RE.match(OQ.SPEC_SUBMIT_TOKEN)                   # no real token can be created with this name
    with pytest.raises(OQ.Refused):
        OQ.create_token(OQ.SPEC_SUBMIT_TOKEN, "owner")
    assert OQ.list_items(token_name="t1") == []


def test_completeness_line_and_long_title():
    assert EV.completeness_line({"answered": 41, "total_required": 45, "unknown": 4, "simulation_ready": True}) == \
        "Completeness: 41 of 45 required answered, 4 not known yet; simulation ready"
    line = EV.completeness_line({"answered": 3, "total_required": 45, "simulation_ready": False,
                                 "simulation_missing": [{"label": f"F{i}"} for i in range(7)]})
    assert line == "Completeness: 3 of 45 required answered; simulation needs F0, F1, F2, F3, F4, +2"
    body = EV.spec_item({"email": "a@b"}, "p1", {"version": 3}, {"title": "T\n" + "x" * 400}, completeness={}, app_url="u", staff=False)
    assert len(body["question"]) <= OQ.MAX_QUESTION and "\n" not in body["question"]
    OQ.validate_new(body)


def test_second_version_lists_changed_field_labels_only(env):
    user, pid = env
    _save(pid, _sample_draft(), 1)
    routes.submit_spec_for(user, pid, 1)
    d2 = _sample_draft()
    d2["fields"]["1_6"] = {"status": "filled", "value": {"maximum_rpm": 4321}}
    d2["fields"]["4_1"] = {"status": "filled", "value": {"outer_diameter_mm": 95, "length_mm": 40}}
    cur = store.load_json(pid, "draft.json", {})
    store.save_json(pid, "draft.json", dict(cur, draft=d2))
    assert routes.submit_spec_for(user, pid, int(cur["revision"]))["version"]["version"] == 2
    it = next(i for i in OQ.list_items() if i["question"].startswith("Specification v2 "))
    assert "Changed since v1: Maximum speed, Size envelope" in it["detail"] and "4321" not in it["detail"]
    assert not any(o["recommended"] for o in it["options"])
    assert len(OQ.list_items()) == 2


def test_existing_diameter_mode_recommends_simulate():
    # the form does not store od_mode yet (it refuses the key); the card reads it from the size envelope once it does
    v = {"version": 1, "draft": {"fields": {"4_1": {"value": {"outer_diameter_mm": 95, "od_mode": "existing"}}}}}
    body = EV.spec_item({"email": "a@b"}, "p1", v, {"title": "T"}, completeness={}, app_url="u", staff=False)
    assert [o["label"] for o in body["options"] if o["recommended"]] == ["Simulate on existing sizes"]
    OQ.validate_new(body)
    v["draft"]["fields"]["4_1"]["value"]["od_mode"] = "custom"
    body = EV.spec_item({"email": "a@b"}, "p1", v, {"title": "T"}, completeness={}, app_url="u", staff=False)
    assert not any(o["recommended"] for o in body["options"])


def test_od_mode_and_changed_fields_pure():
    assert EV.od_mode({"fields": {"4_1": {"value": {"od_mode": "custom"}}}}) == "custom"
    assert EV.od_mode({"fields": {"4_1": {"value": {"od_mode": "other"}}}}) is None and EV.od_mode({}) is None
    assert EV.changed_fields(None, {"fields": {}}) == []
    assert EV.changed_fields({"fields": {"1_6": 1}}, {"fields": {"1_6": 1}}) == []
