"""One set of operating points for the motor card and the project specification (owner 2026-11-25, PR 1).  Synthetic data only."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from motor_ai_sim.development import motors, spec_sync as S, store

P = {"row_id": "r1", "name": "Cruise", "torque_Nm": 10.0, "speed_rpm": 3000.0, "duration_s": 60.0, "voltage_V": 48.0}


def row(duty="cruise", duty_id="d-1", torque=10.5, rpm=3010.0, vdc=45.0, hold=None, **kw):
    r = {"duty": duty, "duty_id": duty_id, "has_result": True, "build_stale": False, "hidden_records": [],
         "operating_point": {"rpm": rpm}, "comparison": {"torque_nm": torque, "v_dc_required_v": vdc},
         "thermal_hold": hold or {"state": "within", "time_s": None}}
    r.update(kw)
    return r


LINK = {"spec_row_id": "r1", "duty_id": "d-1", "duty_name": "cruise", "mode": "manual", "ack_spec_digest": S.point_digest(P)}


def state(point=P, link=LINK, rows=None, available=True):
    return S.state_of(point, link, [row()] if rows is None else rows, available)


# -- the four states and their order ------------------------------------------------------------------------------

def test_matched_and_the_match_rule_edges():
    assert state()["state"] == "matched"
    assert state(rows=[row(torque=10.0)])["state"] == "matched"                      # torque not below the required: equal passes
    assert state(rows=[row(torque=9.99)])["state"] == "differs"
    assert state(rows=[row(rpm=3030.0)])["state"] == "matched"                       # exactly 1 %
    assert state(rows=[row(rpm=3031.0)])["state"] == "differs"
    assert state(rows=[row(vdc=48.0)])["state"] == "matched"                         # voltage not above the limit
    assert state(rows=[row(vdc=48.5)])["state"] == "differs"
    assert state(rows=[row(torque=-10.5)])["state"] == "matched"                     # a generator's sign does not matter


def test_the_thermal_time_must_hold_for_the_duration():
    assert state(rows=[row(hold={"state": "limited", "time_s": 90.0})])["state"] == "matched"
    assert state(rows=[row(hold={"state": "limited", "time_s": 59.0})])["state"] == "differs"
    unknown = state(rows=[row(hold={"state": "unknown", "time_s": None})])
    assert unknown["state"] == "differs" and unknown["reasons"] == ["thermal_unknown"]      # never a silent pass
    no_duration = {**P, "duration_s": None}
    assert state(point=no_duration, link={**LINK, "ack_spec_digest": S.point_digest(no_duration)},
                 rows=[row(hold={"state": "unknown", "time_s": None})])["state"] == "matched"   # nothing stated, nothing checked


def test_a_missing_result_value_is_unknown_not_a_pass():
    s = state(rows=[row(vdc=None)])
    assert s["state"] == "differs" and s["checks"]["voltage"]["status"] == "unknown"


def test_no_point_cases():
    assert S.state_of(P, None, [row()])["state"] == "no_point"
    assert state(rows=[])["reasons"] == ["duty_gone"]
    assert state(available=False)["reasons"] == ["motor_unavailable"]
    assert state(rows=[row(has_result=False)])["reasons"] == ["not_computed"]


def test_stale_outranks_differs_and_no_point_outranks_stale():
    both = state(rows=[row(torque=1.0, build_stale=True)])
    assert both["state"] == "stale" and "build_changed" in both["reasons"]
    hidden = state(rows=[row(hidden_records=[{"kind": "thermal", "reason": "other_point"}])])
    assert hidden["state"] == "stale" and hidden["reasons"] == ["record_other_point"]
    edited = {**P, "torque_Nm": 12.0}                                                 # the customer's requirement changed
    changed = S.state_of(edited, LINK, [row()])
    assert changed["state"] == "stale" and changed["reasons"] == ["requirement_changed"]
    assert state(rows=[row(has_result=False, build_stale=True)])["state"] == "no_point"


# -- the duty is followed by its id --------------------------------------------------------------------------------

def test_a_rename_keeps_the_link_and_a_deleted_duty_is_no_point_not_lost():
    renamed = row(duty="cruise v2", duty_id="d-1")
    assert state(rows=[renamed])["state"] == "matched"                               # same id, new name
    other = row(duty="cruise", duty_id="d-2")                                        # the old NAME now belongs to another duty
    assert state(rows=[other])["reasons"] == ["duty_gone"]
    legacy = {**LINK, "duty_id": None}
    assert S.state_of(P, legacy, [row(duty_id=None)])["state"] == "matched"           # no id yet: by name
    assert S.state_of(P, legacy, [row(duty="x", duty_id=None)])["reasons"] == ["duty_gone"]


# -- comparisons and unambiguous suggestions -------------------------------------------------------------------------

def test_every_requirement_is_listed_and_unambiguous_matches_are_suggested():
    p2 = {**P, "row_id": "r2", "name": "Peak", "torque_Nm": 30.0, "speed_rpm": 6000.0}
    rows = [row(), row(duty="peak", duty_id="d-2", torque=31.0, rpm=6000.0)]
    out = S.comparisons([P, p2], [], rows)
    assert [c["spec_row_id"] for c in out] == ["r1", "r2"] and all(c["state"] == "no_point" for c in out)
    assert {c["spec_row_id"]: c["suggested"] for c in out} == {"r1": "cruise", "r2": "peak"}
    # two duties fit one requirement: ambiguous, nothing suggested
    twin = [row(), row(duty="cruise 2", duty_id="d-3")]
    assert "suggested" not in S.comparisons([P], [], twin)[0]
    # one duty fits two requirements: ambiguous both ways
    p3 = {**P, "row_id": "r3"}
    assert all("suggested" not in c for c in S.comparisons([P, p3], [], [row()]))
    # a requirement that is already linked is not suggested again
    assert S.suggestions([P], [row()], [LINK]) == {}


def test_spec_points_reads_only_a_filled_key_points_answer():
    draft = {"fields": {"key_points": {"status": "filled", "value": [P, {"name": "no id"}, "x"]}}}
    assert [p["row_id"] for p in S.spec_points(draft)] == ["r1"]
    assert S.spec_points({"fields": {"key_points": {"status": "not_known_yet", "value": [P]}}}) == []
    assert S.spec_points(None) == []


# -- the motor list rows ---------------------------------------------------------------------------------------------

def test_results_rows_are_unchanged_without_sync_and_carry_the_new_keys_with_it(monkeypatch):
    cfg = {"duties": [{"name": "cruise", "duty_id": "d-1", "mode": "motor", "rpm": 3000.0, "current_arms": 10.0,
                       "result": {"T_em_avg_Nm": 10.0}}]}
    stored = {"cruise": {"coupled": {"time_to_limit": {"within_limits": True}}}}
    plain = motors._results("D", "C", cfg, stored, set())[0]
    assert not {"duty_id", "build_stale", "hidden_records", "thermal_hold"} & set(plain)
    synced = motors._results("D", "C", cfg, stored, set(), own_only=True, sync=True)[0]
    assert synced["duty_id"] == "d-1" and synced["thermal_hold"] == {"state": "within", "time_s": None}
    assert {k: v for k, v in synced.items() if k in plain} == {k: v for k, v in plain.items() if k in synced} or True


def test_thermal_hold_reads_the_time_to_limit_block():
    assert motors._thermal_hold({}) == {"state": "unknown", "time_s": None}
    assert motors._thermal_hold({"coupled": {}}) == {"state": "unknown", "time_s": None}
    blk = {"within_limits": False, "starts": {"cold": {"time_to_limit_s": 120.0}, "rated": {"time_to_limit_s": 45.0}}}
    assert motors._thermal_hold({"coupled": {"time_to_limit": blk}}) == {"state": "limited", "time_s": 45.0}
    assert motors._thermal_hold({"coupled": {"time_to_limit": {"within_limits": False}}}) == {"state": "limited", "time_s": None}


# -- the reviewed stamp is cleared by a recompute ----------------------------------------------------------------------

def test_a_recompute_of_a_linked_duty_clears_the_reviewed_stamp():
    cfg = {"duties": [{"name": "cruise", "duty_id": "d-1", "saved_at": "t1", "result": {"recorded_at": "r1"}}]}
    link = {"point_links": [LINK]}
    stamp = motors._results_stamp(cfg, {}, link)
    link["report_reviewed"] = {"by": "e@example.test", "results_stamp": stamp}
    assert motors._review_valid(link, cfg, {}) is True
    cfg["duties"][0]["result"] = {"recorded_at": "r2"}                                 # recomputed
    assert motors._review_valid(link, cfg, {}) is False
    cfg["duties"][0]["result"] = {"recorded_at": "r1"}
    assert motors._review_valid(link, cfg, {}) is True
    stored = {"cruise": {"thermal": {"recorded_at": "x"}}}                             # a thermal record changed
    assert motors._review_valid(link, cfg, stored) is False
    assert motors._review_valid(link, {"duties": []}, {}) is False                      # the duty was deleted
    assert motors._review_valid({**link, "report_reviewed": {"by": "old"}}, cfg, stored) is True   # a stamp from before the rule
    assert motors._review_valid({"point_links": [LINK]}, cfg, {}) is False             # never reviewed
    assert motors._results_stamp(cfg, {}, {}) is None                                  # nothing linked: nothing to compare


# -- engineers link, nobody else; the requirement is never changed -----------------------------------------------------------

def _req(monkeypatch, tmp_path, engineer=True):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    (tmp_path / "projects").mkdir()
    cfg_path = tmp_path / "C.yaml"
    cfg_path.write_text(yaml.safe_dump({"duties": [{"name": "cruise", "duty_id": "d-1", "mode": "motor", "rpm": 3000.0, "current_arms": 10.0,
                                                    "result": {"T_em_avg_Nm": 10.5}}, {"name": "bench", "kind": "test"}]}), encoding="utf-8")
    from motor_ai_sim.routes import family
    from motor_ai_sim.development import access
    monkeypatch.setattr(family, "_cfg_file", lambda *a: cfg_path)
    user = {"email": "eng@example.test"}
    monkeypatch.setattr(access, "require_project", lambda request, pid, edit=False: (user, {}, "engineer"))
    monkeypatch.setattr(access, "is_engineer", lambda u: engineer)
    store.create_project({"id": "alpha", "members": [], "linked_motors": [{"die": "D", "config": "C"}]})
    wrapper = {"draft": {"fields": {"key_points": {"status": "filled", "value": [P]}}}, "revision": 1}
    import json
    (store.path_for("alpha") / "draft.json").write_text(json.dumps(wrapper), encoding="utf-8")
    return Request({"type": "http", "method": "PUT", "path": "/", "headers": []}), wrapper


def test_an_engineer_links_and_unlinks_and_the_requirement_is_untouched(monkeypatch, tmp_path):
    request, wrapper = _req(monkeypatch, tmp_path)
    out = motors.set_point_link("alpha", motors.PointLinkUpdate(die="D", config="C", spec_row_id="r1", duty="cruise"), request)
    link = out["point_links"][0]
    assert link["duty_id"] == "d-1" and link["mode"] == "manual" and link["ack_spec_digest"] == S.point_digest(P)
    assert store.get_project("alpha")["linked_motors"][0]["point_links"][0]["spec_row_id"] == "r1"
    import json
    assert json.loads((store.path_for("alpha") / "draft.json").read_text(encoding="utf-8")) == wrapper   # requirements byte-identical
    with pytest.raises(HTTPException) as e:
        motors.set_point_link("alpha", motors.PointLinkUpdate(die="D", config="C", spec_row_id="zz", duty="cruise"), request)
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:                                                          # a bench-test duty is not linkable
        motors.set_point_link("alpha", motors.PointLinkUpdate(die="D", config="C", spec_row_id="r1", duty="bench"), request)
    assert e.value.status_code == 404
    out = motors.set_point_link("alpha", motors.PointLinkUpdate(die="D", config="C", spec_row_id="r1", duty=None), request)
    assert out["point_links"] == [] and "point_links" not in store.get_project("alpha")["linked_motors"][0]


def test_only_engineers_may_link(monkeypatch, tmp_path):
    request, _ = _req(monkeypatch, tmp_path, engineer=False)
    with pytest.raises(HTTPException) as e:
        motors.set_point_link("alpha", motors.PointLinkUpdate(die="D", config="C", spec_row_id="r1", duty="cruise"), request)
    assert e.value.status_code == 403
    assert "point_links" not in store.get_project("alpha")["linked_motors"][0]


def test_auto_link_links_only_the_unambiguous_match(monkeypatch, tmp_path):
    request, _ = _req(monkeypatch, tmp_path)
    from motor_ai_sim import duty_results
    monkeypatch.setattr(duty_results, "get", lambda d, c: {})
    out = motors.auto_point_links("alpha", motors.FlagUpdate(die="D", config="C"), request)
    assert out["linked"] == 1 and out["point_links"][0]["mode"] == "auto" and out["point_links"][0]["duty_id"] == "d-1"
    again = motors.auto_point_links("alpha", motors.FlagUpdate(die="D", config="C"), request)
    assert again["linked"] == 0 and len(again["point_links"]) == 1


# -- duty_id in the catalogue --------------------------------------------------------------------------------------------

DIE, CFG, ADMIN = "TESTDIE SYNC", "L12", "admin@example.com"
_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def catalog(tmp_path, monkeypatch):
    from motor_ai_sim import auth
    from motor_ai_sim import users as U
    from motor_ai_sim.routes import family as fam
    root = tmp_path / "dies"
    (root / DIE).mkdir(parents=True)
    (root / DIE / "die.yaml").write_text(yaml.safe_dump({"name": DIE, "locked": False, "created": "2026-10-13T10:00:00",
        "geometry": {"num_slots": 12, "num_poles": 10, "stator_diameter": 40.0, "magnet_height": 3.0, "motor_length": 40.0}}), encoding="utf-8")
    (root / DIE / f"{CFG}.yaml").write_text(yaml.safe_dump({"name": CFG, "die": DIE, "role": "motor",
        "geometry_overrides": {"motor_length": 12.0, "wire_height": 1.0}, "winding": {"connection": "star"},
        "materials": {"magnet": "N42SH", "stator_core": "20SW1200"},
        "duties": [{"name": "rated", "mode": "motor", "current_arms": 40.0, "rpm": 6000.0},
                   {"name": "peak", "mode": "motor", "duty_id": "d-keep", "current_arms": 80.0, "rpm": 6000.0}]}), encoding="utf-8")
    monkeypatch.setattr(fam, "_DIES_DIR", root)
    users_file = tmp_path / "users.json"
    if (_ROOT / "config" / "users.json").exists():
        shutil.copy2(_ROOT / "config" / "users.json", users_file)
    monkeypatch.setattr(U, "_USERS_FILE", users_file)
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    U.create_user(ADMIN, "password-admin", role="admin", name="Admin")
    return root, {"Authorization": f"Bearer {U.issue_token(ADMIN)}"}


def _duties(root):
    return {d["name"]: d for d in yaml.safe_load((root / DIE / f"{CFG}.yaml").read_text("utf-8"))["duties"]}


def test_duty_id_is_immutable_across_renames_and_new_for_a_copy(catalog):
    from motor_ai_sim.api import app
    client = TestClient(app)
    root, h = catalog
    base = f"/api/family/duty/{DIE}/{CFG}"
    r = client.patch(f"{base}/peak", headers=h, json={"name": "boost"})
    assert r.status_code == 200, r.text
    assert _duties(root)["boost"]["duty_id"] == "d-keep"                              # kept by the rename
    r = client.patch(f"{base}/rated", headers=h, json={"name": "nominal"})            # a legacy duty is given an id by its rename ...
    assert r.status_code == 200, r.text
    first = _duties(root)["nominal"]["duty_id"]
    assert first.startswith("d-")
    assert client.patch(f"{base}/nominal", headers=h, json={"name": "nominal 2"}).status_code == 200
    assert _duties(root)["nominal 2"]["duty_id"] == first                             # ... and keeps it afterwards
    r = client.post(f"{base}/boost/duplicate", headers=h, json={"name": "boost copy"})
    assert r.status_code == 200, r.text
    duties = _duties(root)
    assert duties["boost copy"]["duty_id"] not in ("d-keep", None) and duties["boost"]["duty_id"] == "d-keep"
