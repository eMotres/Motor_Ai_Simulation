"""Points-only Configure feed: GET /motors/points and PATCH /motors/note (synthetic names only)."""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from motor_ai_sim.development import access, motors, store

IDENTITIES = {
    "admin": {"email": "admin@example.test", "role": "admin"},
    "engineer": {"email": "engineer@example.test", "role": "engineer"},
    "customer": {"email": "customer@example.test", "role": "customer"},
    "owner": {"email": "owner@example.test", "role": "customer"},
    "pengineer": {"email": "pe@example.test", "role": "customer"},
    "stranger": {"email": "stranger@example.test", "role": "customer"},
}

DUTY_SOLVED = """
duties:
  - name: takeoff
    rpm: 3000
    current_arms: 40
    gamma_deg: 10
    torque_nm: 5.0
    summary:
      T_em_avg_Nm: 4.8
      P_mech_W: 1500.0
      efficiency_pct: 91.5
      P_loss_total_W: 140.0
      ripple_pct: 3.2
      V_line_peak_V: 48.0
      geometry_secret: not-a-metric
  - name: cruise
    rpm: 2000
    current_arms: 12
    summary:
      T_em_avg_Nm: 1.2
      efficiency_pct: 93.0
  - name: peak
    rpm: 3500
    current_arms: 60
"""
DUTY_EMPTY = "duties:\n  - name: cruise\n    rpm: 2000\n"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from motor_ai_sim.routes import family
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    (tmp_path / "projects").mkdir()
    members = [
        {"email": "customer@example.test", "role": "customer"},
        {"email": "owner@example.test", "role": "owner"},
        {"email": "pe@example.test", "role": "engineer"},
    ]
    store.create_project({"id": "alpha", "members": members, "linked_motors": [
        {"die": "die", "config": "opt1"},
        {"die": "die", "config": "opt2"},
        {"die": "die", "config": "opt3", "status": "trial"},
        {"die": "die", "config": "nogrant"},
    ]})
    store.save_index({"motors": {motors._key("die", c): {"project_id": "alpha"}
                                 for c in ("opt1", "opt2", "opt3", "nogrant")}})
    monkeypatch.setattr(access, "require_signed_in",
                        lambda request: IDENTITIES[request.headers.get("x-user")])
    die_dir = tmp_path / "catalog"
    die_dir.mkdir()
    (die_dir / "opt1.yaml").write_text(DUTY_SOLVED, encoding="utf-8")
    (die_dir / "opt2.yaml").write_text(DUTY_EMPTY, encoding="utf-8")
    (die_dir / "opt3.yaml").write_text(DUTY_SOLVED, encoding="utf-8")
    (die_dir / "nogrant.yaml").write_text(DUTY_SOLVED, encoding="utf-8")
    monkeypatch.setattr(family, "_iter_die_entries", lambda: [{"name": "die", "dir": die_dir, "layer": "shared"}])
    monkeypatch.setattr(family, "_die_dir", lambda die: die_dir)
    monkeypatch.setattr(family, "_cfg_file", lambda die, config: die_dir / f"{config}.yaml")

    def require(die, authorization, cfg=None, *args, **kwargs):
        if cfg == "nogrant":
            raise HTTPException(404, detail="not found")
    monkeypatch.setattr(family, "_require_die_access", require)
    monkeypatch.setattr(motors, "_stored_results_index", lambda *args: {})
    monkeypatch.setattr(motors, "_identity", lambda request: IDENTITIES[request.headers.get("x-user")])
    app = FastAPI()
    app.include_router(motors.router)
    return TestClient(app)


def get(client, who, query=""):
    return client.get("/api/projects/alpha/motors/points" + query, headers={"x-user": who})


def test_member_sees_variants_in_project_order_with_stored_points_only(client):
    response = get(client, "customer")
    assert response.status_code == 200
    body = response.json()
    # trial hidden, motor without grant omitted, order = link order, steps numbered 1..n
    assert [(v["step"], v["config"]) for v in body["variants"]] == [(1, "opt1"), (2, "opt2")]
    first, second = body["variants"]
    assert [p["duty"] for p in first["points"]] == ["takeoff", "cruise"]   # "peak" has no stored result
    assert second["points"] == []                                          # variant kept, no points yet
    takeoff = first["points"][0]
    assert takeoff["operating_point"]["rpm"] == 3000
    assert takeoff["comparison"]["torque_nm"] == 4.8
    assert takeoff["comparison"]["efficiency_pct"] == 91.5
    assert takeoff["comparison"]["p_loss_total_w"] == 140.0
    # nothing beyond allow-listed numbers: no invented values, no free-form summary keys
    assert "geometry_secret" not in json.dumps(body)
    assert "v_dc_required_v" not in takeoff["comparison"]
    assert set(takeoff) == {"duty", "operating_point", "electromagnetic", "comparison", "stored_results", "has_result"}
    assert body["selected"] is None


def test_show_all_adds_trial_and_selected_variant_is_echoed(client):
    body = get(client, "owner", "?show_all=true").json()
    assert [v["config"] for v in body["variants"]] == ["opt1", "opt2", "opt3"]
    assert body["variants"][2]["status"] == "trial"
    picked = get(client, "owner", "?die=die&config=opt2").json()
    assert picked["selected"] == {"die": "die", "config": "opt2"}


@pytest.mark.parametrize("who,expected", [("admin", 200), ("engineer", 200), ("customer", 200),
                                          ("owner", 200), ("pengineer", 200), ("stranger", 404)])
def test_access_matrix(client, who, expected):
    assert get(client, who).status_code == expected


def test_motor_without_grant_is_not_reachable_even_by_name(client):
    assert get(client, "customer", "?die=die&config=nogrant").status_code == 404
    assert get(client, "customer", "?die=die&config=missing").status_code == 404
    # a trial variant is not selectable until the caller asks for all versions
    assert get(client, "customer", "?die=die&config=opt3").status_code == 404
    assert get(client, "customer", "?die=die&config=opt3&show_all=true").status_code == 200


def test_customer_read_is_audited_but_engineer_read_is_not(client):
    get(client, "engineer")
    assert not (store.path_for("alpha") / "audit.jsonl").exists()
    get(client, "customer")
    rows = [json.loads(x) for x in (store.path_for("alpha") / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["action"] == "motor.points.read"


def patch_note(client, who, note, config="opt1"):
    return client.patch("/api/projects/alpha/motors/note", headers={"x-user": who},
                        json={"die": "die", "config": config, "note": note})


@pytest.mark.parametrize("who,expected", [("admin", 200), ("engineer", 200), ("owner", 200),
                                          ("pengineer", 200), ("customer", 403), ("stranger", 404)])
def test_note_edit_permissions(client, who, expected):
    response = patch_note(client, who, "baseline")
    assert response.status_code == expected, response.text
    stored = [m for m in store.get_project("alpha")["linked_motors"] if m["config"] == "opt1"][0]
    assert (stored.get("note") == "baseline") is (expected == 200)


def test_note_is_normalised_shown_to_customer_cleared_and_audited(client):
    assert patch_note(client, "engineer", "  mechanics   minimum:\n housing 2.1 mm ").json()["note"] == \
        "mechanics minimum: housing 2.1 mm"
    variants = get(client, "customer").json()["variants"]
    assert variants[0]["note"] == "mechanics minimum: housing 2.1 mm"
    assert variants[1]["note"] == ""
    listing = client.get("/api/projects/alpha/motors", headers={"x-user": "customer"}).json()["motors"]
    assert listing[0]["note"] == "mechanics minimum: housing 2.1 mm"
    assert listing[0]["can_open_points"] is True and listing[1]["can_open_points"] is False
    assert listing[0]["can_open_configure"] is False
    assert patch_note(client, "engineer", "").json()["note"] == ""
    assert "note" not in [m for m in store.get_project("alpha")["linked_motors"] if m["config"] == "opt1"][0]
    audit = [json.loads(x) for x in (store.path_for("alpha") / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [a["action"] for a in audit if a["action"] == "motor.note"] == ["motor.note", "motor.note"]


def test_note_validation_is_loud_and_non_mutating(client):
    before = store.get_project("alpha")
    too_long = patch_note(client, "engineer", "x" * (motors.MAX_STEP_NOTE + 1))
    assert too_long.status_code == 422 and "too long" in too_long.json()["detail"]
    assert patch_note(client, "engineer", "ok", config="missing").status_code == 404
    assert client.patch("/api/projects/alpha/motors/note", headers={"x-user": "engineer"},
                        json={"die": " ", "config": "opt1", "note": "x"}).status_code == 422
    assert store.get_project("alpha") == before


def test_steps_feed_carries_the_staff_marks_so_the_points_view_needs_no_project_motor_list(client):
    """Slow Configure load (owner 2026-10-26): the staff marks ride on the light steps feed (no second, heavy list)."""
    before = get(client, "customer").json()["variants"]
    assert all(v["recommended"] is None and v["report_reviewed"] is None for v in before)
    for what in ("recommended", "report_reviewed"):
        done = client.patch(f"/api/projects/alpha/motors/{what}", headers={"x-user": "admin"},
                            json={"die": "die", "config": "opt2", "value": True})
        assert done.status_code == 200, done.text
    after = {v["config"]: v for v in get(client, "customer").json()["variants"]}
    assert after["opt2"]["recommended"]["by"] and after["opt2"]["recommended"]["at"]
    assert after["opt2"]["report_reviewed"]["by"] and after["opt2"]["report_reviewed"]["at"]
    assert after["opt1"]["recommended"] is None and after["opt1"]["report_reviewed"] is None
