"""The five simulation stages of a project variant and the project queue summary (owner 2026-10-27).

Synthetic duty stamps + synthetic live runs; the route is exercised with the stored side and the worker rows injected.
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim.development import access, activity, stages, store

NOW = 1_800_000_000.0
REAL_STORED_SIDE = activity._stored_side


def iso(delta_s=0.0):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(NOW + delta_s, tz=timezone.utc).isoformat(timespec="seconds")


def motor(duties=("rated", "peak", "cont", "burst"), em_done=("rated", "peak", "cont"), thermal=("rated",), mech=(),
          end3d=False, reviewed=None):
    stored = {}
    for d in thermal:
        stored.setdefault(d, {})["coupled"] = {"computed_at": iso(-3600)}
    for d in mech:
        stored.setdefault(d, {})["rotor_stress"] = {"computed_at": iso(-1800)}
    return {"die": "Die A", "config": "v3",
            "duties": [{"name": d, "em_done": d in em_done, "em_at": iso(-7200) if d in em_done else None} for d in duties],
            "stored": stored, "end3d": end3d, "end3d_at": iso(-100000) if end3d else None, "report_reviewed": reviewed}


def live(kind, state, duty="", progress=None, position=None, ahead=None, finish=None, start=None, conf="medium",
         die="Die A", config="v3", basis="history"):
    eta = {"expected_start": start, "expected_finish": finish, "confidence": conf, "basis": basis} if (finish or start) else None
    return {"die": die, "config": config, "duty": duty, "kind": kind, "label": "x", "state": state, "started_at": None,
            "progress": progress, "position": position, "ahead": ahead, "eta": eta}


def by_id(derived):
    return {s["id"]: s for s in derived["stages"]}


# ---------------------------------------------------------------------------
# stage derivation
# ---------------------------------------------------------------------------

def test_kinds_map_to_the_five_stages():
    assert [stages.stage_of_kind(k) for k in ("transient", "cogging", "coupled.run", "coupled.continuous_rating", "thermal.duty_cycle",
                                              "mechanical.rotor_stress", "static3d.solve", "optimizer.scan", "host", "")] == [
        "em", "em", "thermal", "thermal", "thermal", "mechanics", "end3d", None, None, None]


def test_stored_results_only_a_variant_with_some_done_stages():
    out = stages.derive_motor(motor(), [], [], NOW)
    s = by_id(out)
    assert [x["id"] for x in out["stages"]] == ["em", "thermal", "mechanics", "end3d", "report"]
    assert (s["em"]["state"], s["em"]["done"], s["em"]["total"], s["em"]["fraction"]) == ("partial", 3, 4, 0.75)
    assert (s["thermal"]["state"], s["thermal"]["done"], s["thermal"]["fraction"]) == ("partial", 1, 0.25)
    assert s["mechanics"]["state"] == "not_started" and s["end3d"]["state"] == "not_started" and s["report"]["state"] == "not_started"
    assert s["em"]["finished_at"] == iso(-7200)
    assert out["percent"] == round(100 * (0.75 + 0.25) / 5) and out["stage_index"] == 1 and out["complete"] is False
    assert out["eta"] is None and out["runs_running"] == 0


def test_a_computing_stage_fills_by_its_own_progress_and_the_finished_points():
    rows = [live("transient", "running", duty="burst", progress=0.4, finish=iso(1800), start=None, basis="progress", conf="high")]
    s = by_id(stages.derive_motor(motor(), rows, [], NOW))["em"]
    assert s["state"] == "computing" and s["done"] == 3 and s["total"] == 4
    assert s["fraction"] == pytest.approx((3 + 0.4) / 4, abs=1e-3)
    assert [d["state"] for d in s["detail"]] == ["done", "done", "done", "computing"]
    assert s["detail"][3]["progress"] == 0.4
    assert s["eta"]["expected_finish"] == iso(1800) and s["eta"]["confidence"] == "high"


def test_a_queued_stage_says_where_it_is_and_when():
    rows = [live("transient", "queued", duty="burst", position=4, ahead=3, start=iso(900), finish=iso(1800)),
            live("transient", "queued", duty="cont", position=2, ahead=1, start=iso(300), finish=iso(900))]
    s = by_id(stages.derive_motor(motor(em_done=("rated", "peak")), rows, [], NOW))["em"]
    assert s["state"] == "queued" and (s["position"], s["ahead"]) == (2, 1)
    assert s["eta"]["expected_start"] == iso(300) and s["eta"]["expected_finish"] == iso(1800)
    assert [d["state"] for d in s["detail"]] == ["done", "done", "queued", "queued"]


def test_one_unknown_estimate_makes_the_stage_estimate_unknown():
    rows = [live("coupled.run", "running", duty="rated", finish=iso(600)),
            live("coupled.run", "queued", duty="peak", position=1, ahead=0, start=iso(600), finish=None, conf="low", basis="no_history")]
    out = stages.derive_motor(motor(), rows, [], NOW)
    assert by_id(out)["thermal"]["eta"]["expected_finish"] is None and by_id(out)["thermal"]["eta"]["basis"] == "no_history"
    assert out["eta"]["expected_finish"] is None


def test_runs_without_a_duty_work_on_whatever_is_open():
    rows = [live("mechanical.modes", "running", duty="", progress=0.5, finish=iso(100))]
    s = by_id(stages.derive_motor(motor(), rows, [], NOW))["mechanics"]
    assert s["state"] == "computing" and s["fraction"] == pytest.approx(0.5 / 4, abs=1e-3)


def test_the_3d_passport_and_the_review_flag():
    done = by_id(stages.derive_motor(motor(end3d=True), [], [], NOW))["end3d"]
    assert (done["state"], done["done"], done["fraction"]) == ("done", 1, 1.0)
    running = by_id(stages.derive_motor(motor(), [live("static3d.solve", "running", progress=0.3, finish=iso(60))], [], NOW))["end3d"]
    assert running["state"] == "computing" and running["fraction"] == 0.3
    queued = by_id(stages.derive_motor(motor(), [live("static3d.solve", "queued", position=3, ahead=2)], [], NOW))["end3d"]
    assert queued["state"] == "queued" and queued["position"] == 3
    reviewed = by_id(stages.derive_motor(motor(), [], [], NOW))["report"]
    assert reviewed["state"] == "not_started"
    ok = by_id(stages.derive_motor(motor(reviewed={"by": "staff@example.test", "at": iso(-60)}), [], [], NOW))["report"]
    assert ok["state"] == "done" and ok["finished_at"] == iso(-60) and "staff" not in json.dumps(ok)


def test_the_report_waits_for_review_only_when_everything_before_it_is_done():
    full = motor(em_done=("rated", "peak", "cont", "burst"), thermal=("rated", "peak", "cont", "burst"),
                 mech=("rated", "peak", "cont", "burst"), end3d=True)
    out = stages.derive_motor(full, [], [], NOW)
    rep = by_id(out)["report"]
    assert rep["state"] == "not_started" and rep["note"] == "awaiting_review"
    assert out["stage_index"] == 5 and out["percent"] == 80
    full["report_reviewed"] = {"at": iso(-5)}
    done = stages.derive_motor(full, [], [], NOW)
    assert done["complete"] is True and done["percent"] == 100


def test_a_recent_failure_is_shown_until_a_newer_result_exists():
    fail = [{"kind": "mechanical.rotor_stress", "duty": "rated", "finished_at": NOW - 600, "die": "Die A", "config": "v3"}]
    assert by_id(stages.derive_motor(motor(), [], fail, NOW))["mechanics"]["state"] == "failed"
    assert by_id(stages.derive_motor(motor(mech=("rated",)), [], fail, NOW))["mechanics"]["state"] == "failed"      # the result stored 30 min ago is older than the failure 10 min ago
    old = [{"kind": "mechanical.rotor_stress", "duty": "rated", "finished_at": NOW - 7 * 3600}]
    assert by_id(stages.derive_motor(motor(), [], old, NOW))["mechanics"]["state"] == "not_started"          # history, not state
    retry = [live("mechanical.rotor_stress", "queued", duty="rated", position=1, ahead=0)]
    assert by_id(stages.derive_motor(motor(), retry, fail, NOW))["mechanics"]["state"] == "queued"         # a retry wins


def test_the_failure_of_an_earlier_run_does_not_hide_a_newer_result():
    fail = [{"kind": "coupled.run", "duty": "rated", "finished_at": NOW - 7200}]
    assert by_id(stages.derive_motor(motor(thermal=("rated",)), [], fail, NOW))["thermal"]["state"] == "partial"   # stored 1 h ago, failed 2 h ago


def test_a_variant_that_is_not_openable_still_shows_its_live_runs():
    bare = {"die": "Die A", "config": "v3", "duties": [], "stored": {}, "end3d": False, "end3d_at": None, "report_reviewed": None}
    out = stages.derive_motor(bare, [live("coupled.run", "running", progress=0.2, finish=iso(60))], [], NOW)
    s = by_id(out)["thermal"]
    assert s["state"] == "computing" and s["total"] == 0 and s["fraction"] == 0.0
    assert out["runs_running"] == 1


# ---------------------------------------------------------------------------
# project summary
# ---------------------------------------------------------------------------

def test_summary_states_idle_queued_computing_ready():
    m = [motor(thermal=("rated",))]
    idle = stages.derive([motor(em_done=(), thermal=())], [], [], NOW)["summary"]
    assert idle["state"] == "idle" and idle["runs_running"] == 0 and idle["expected_all_done"] is None
    ready_after = stages.derive(m, [], [], NOW, since=NOW - 3 * 86400)["summary"]
    assert ready_after["state"] == "results_ready" and ready_after["last_finished_at"] == iso(-3600)
    seen = stages.derive(m, [], [], NOW, since=NOW - 60)["summary"]
    assert seen["state"] == "idle"                                       # the viewer has been here since the last result
    old = [dict(motor(), stored={"rated": {"coupled": {"computed_at": iso(-3 * 86400)}}}, duties=[])]
    assert stages.derive(old, [], [], NOW)["summary"]["state"] == "idle"   # nothing finished in the last 24 h
    q = stages.derive(m, [live("transient", "queued", position=2, ahead=1, start=iso(600), finish=iso(1500))], [], NOW)["summary"]
    assert q["state"] == "queued" and q["runs_queued"] == 1 and q["expected_all_done"] == iso(1500)
    c = stages.derive(m, [live("transient", "running", finish=iso(300)), live("coupled.run", "queued", finish=iso(2700), conf="low")],
                      [], NOW, server={"running": 2, "queued": 5, "workers": 2})["summary"]
    assert c["state"] == "computing" and (c["runs_running"], c["runs_queued"]) == (1, 1)
    assert c["expected_all_done"] == iso(2700) and c["confidence"] == "low"
    assert c["server"] == {"running": 2, "queued": 5}


def test_summary_without_an_estimate_for_some_run_has_no_all_done_time():
    c = stages.derive([motor()], [live("transient", "running", finish=iso(300)), live("coupled.run", "queued")], [], NOW)["summary"]
    assert c["expected_all_done"] is None and c["basis"] == "no_history"


def test_host_rows_count_in_the_summary_but_belong_to_no_stage():
    host = live("host", "running", finish=iso(900))
    out = stages.derive([motor()], [host], [], NOW)
    assert out["summary"]["state"] == "computing" and out["summary"]["runs_running"] == 1
    assert all(s["state"] != "computing" for s in out["stages"][0]["stages"])


# ---------------------------------------------------------------------------
# the endpoint
# ---------------------------------------------------------------------------

IDENTITIES = {"engineer": {"email": "eng@example.test", "role": "engineer"},
              "customer": {"email": "cust@example.test", "role": "customer"},
              "stranger": {"email": "stranger@example.test", "role": "customer"}}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    (tmp_path / "projects").mkdir()
    store.create_project({"id": "alpha", "status": "design", "members": [{"email": "cust@example.test", "role": "customer"}],
                          "linked_motors": [{"die": "Die A", "config": "v3"}]})
    monkeypatch.setattr(access, "require_signed_in", lambda request: IDENTITIES[request.headers.get("x-user")])
    monkeypatch.setattr(activity, "_alias_names", lambda canonical: set())
    monkeypatch.setenv(activity.ENV_STATUS, str(tmp_path / "none.json"))
    activity._worker_cache.update(at=0.0, rows=[], server=None, failures=[])
    activity._stage_cache.clear()
    monkeypatch.setattr(activity, "_stored_side", lambda pid, project, request, user: [motor()])
    monkeypatch.setattr(activity, "app_extra", lambda: {"server": {"running": 2, "queued": 7, "workers": 2}, "failures": []})
    app = FastAPI()
    app.include_router(activity.router)
    return TestClient(app)


def test_the_response_carries_position_eta_summary_and_stages(env, monkeypatch):
    fin = iso(5400)
    monkeypatch.setattr(activity, "app_rows", lambda: [
        {"kind": "transient", "state": "queued", "die": "Die A", "config": "v3", "duty": "burst", "queued_at": 1.0,
         "started_at": None, "progress": None, "position": 3, "ahead": 2,
         "eta": {"expected_start": iso(900), "expected_finish": fin, "confidence": "medium", "basis": "queue"}}])
    r = env.get("/api/projects/alpha/activity", headers={"x-user": "customer"})
    assert r.status_code == 200, r.text
    body = r.json()
    row = body["activity"][0]
    assert (row["position"], row["ahead"], row["state"]) == (3, 2, "queued")
    assert row["eta"] == {"expected_start": iso(900), "expected_finish": fin, "confidence": "medium", "basis": "queue"}
    assert body["summary"]["state"] == "queued" and body["summary"]["expected_all_done"] == fin
    assert body["summary"]["server"] == {"running": 2, "queued": 7}
    mine = body["stages"][0]
    assert (mine["die"], mine["config"]) == ("Die A", "v3") and [s["id"] for s in mine["stages"]] == ["em", "thermal", "mechanics", "end3d", "report"]
    assert mine["stages"][0]["state"] == "queued" and mine["stages"][0]["ahead"] == 2


def test_nothing_of_other_accounts_or_internals_leaves_the_server(env, monkeypatch):
    monkeypatch.setattr(activity, "app_rows", lambda: [
        {"kind": "transient", "state": "queued", "die": "Die A", "config": "v3", "duty": "burst", "queued_at": 1.0,
         "started_at": None, "progress": None, "position": 9, "ahead": 8, "run_id": "run-abc123", "owner": "rival@example.test",
         "ws_id": "w-rival", "body": {"secret": 1}, "eta": {"expected_start": None, "expected_finish": None, "confidence": "low",
                                                              "basis": "no_history", "run_id": "run-abc123"}},
        {"kind": "transient", "state": "running", "die": "Rival Die", "config": "X", "duty": "d", "owner": "rival@example.test",
         "position": 0, "progress": 0.5},
    ])
    text = env.get("/api/projects/alpha/activity", headers={"x-user": "customer"}).text
    for forbidden in ("rival", "run-abc123", "w-rival", "secret", "Rival Die", "owner"):
        assert forbidden not in text
    data = json.loads(text)
    assert len(data["activity"]) == 1
    assert set(data["activity"][0]) == {"die", "config", "duty", "kind", "label", "state", "started_at", "progress", "position", "ahead", "eta"}
    assert data["summary"]["runs_queued"] == 1                 # the other account's running run is not counted for this project
    assert data["summary"]["server"] == {"running": 2, "queued": 7}   # only the totals of the server


def test_the_host_status_file_row_gets_a_rounded_estimate_from_its_minutes(env, monkeypatch, tmp_path):
    monkeypatch.setattr(activity, "app_rows", lambda: [])
    status = tmp_path / "status.json"
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    status.write_text(json.dumps({"updated": now_iso, "jobs": [
        {"state": "running", "title": "Step", "targets": [{"die": "Die A", "config": "v3", "what": "Optimisation step 4"}], "eta_min": 47}]}),
        encoding="utf-8")
    monkeypatch.setenv(activity.ENV_STATUS, str(status))
    body = env.get("/api/projects/alpha/activity", headers={"x-user": "engineer"}).json()
    eta = body["activity"][0]["eta"]
    assert eta["confidence"] == "medium" and eta["expected_finish"]
    from datetime import datetime
    assert datetime.fromisoformat(eta["expected_finish"]).timestamp() % 300 == 0


def test_since_moves_the_results_ready_window(env, monkeypatch):
    monkeypatch.setattr(activity, "app_rows", lambda: [])
    monkeypatch.setattr(activity, "_stored_side", lambda pid, project, request, user: [
        dict(motor(duties=("rated",), em_done=(), thermal=()), stored={"rated": {"coupled": {"computed_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 7200))}}})])
    assert env.get("/api/projects/alpha/activity", headers={"x-user": "customer"}).json()["summary"]["state"] == "results_ready"
    last_visit = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 60))
    assert env.get("/api/projects/alpha/activity", params={"since": last_visit},
                   headers={"x-user": "customer"}).json()["summary"]["state"] == "idle"
    assert env.get("/api/projects/alpha/activity", params={"since": "garbage"},
                   headers={"x-user": "customer"}).json()["summary"]["state"] == "results_ready"


def test_a_failing_stored_side_never_breaks_the_live_answer(env, monkeypatch):
    from motor_ai_sim.development import stages as S

    def boom(*a, **k):
        raise RuntimeError("disk")
    monkeypatch.setattr(S, "collect", boom)
    assert REAL_STORED_SIDE("alpha", {"linked_motors": []}, object(), {"email": "x"}) == []


def test_access_is_unchanged(env, monkeypatch):
    monkeypatch.setattr(activity, "app_rows", lambda: [])
    assert env.get("/api/projects/alpha/activity", headers={"x-user": "stranger"}).status_code == 404
    assert env.get("/api/projects/alpha/activity", headers={"x-user": "customer"}).status_code == 200


# ---------------------------------------------------------------------------
# the stored side (collector), with the catalogue helpers faked
# ---------------------------------------------------------------------------

def test_collect_reads_duties_stamps_passport_and_review_through_the_motor_list_gates(tmp_path, monkeypatch):
    from motor_ai_sim import duty_results
    from motor_ai_sim.development import motors as M
    from motor_ai_sim.motor_tests import model_source
    from motor_ai_sim.routes import family
    cfg = tmp_path / "v3.yaml"
    cfg.write_text("duties:\n  - {name: rated, summary: {T_em_avg_Nm: 3.0}}\n  - {name: peak}\n  - {name: burst, result: {T_avg_Nm: 9.0}}\n",
                   encoding="utf-8")
    monkeypatch.setattr(M, "_index", lambda: {"motors": {M._key("Die A", "v3"): {"project_id": "alpha"},
                                                         M._key("Die A", "t1"): {"project_id": "alpha"},
                                                         M._key("Die B", "x"): {"project_id": "other"}}})
    monkeypatch.setattr(M, "_identity", lambda request: {"id": "u"})
    monkeypatch.setattr(family, "_require_die_access", lambda die, authz, cfg_name=None: None)
    monkeypatch.setattr(family, "_cfg_file", lambda die, name: cfg)
    monkeypatch.setattr(family, "_die_file", lambda die: tmp_path / "die.yaml")
    monkeypatch.setattr(family, "_load_yaml", lambda path, what: {})
    monkeypatch.setattr(duty_results, "get", lambda die, config: {
        "peak": {"em_run": {"computed_at": "2026-10-27T09:00:00"}, "coupled": {"computed_at": "2026-10-27T10:00:00"},
                 "rotor_stress": {"recorded_at": "2026-10-27T11:00:00"}}})
    monkeypatch.setattr(model_source, "resolve_end3d", lambda die_doc, cfg_doc: {"k_flux": 0.95, "adopted_utc": "2026-10-01T00:00:00"})

    class Req:
        headers = {"authorization": None}

    project = {"linked_motors": [
        {"die": "Die A", "config": "v3", "report_reviewed": {"by": "staff@example.test", "at": "2026-10-27T12:00:00"}},
        {"die": "Die A", "config": "t1", "status": "trial"},                    # a trial variant is not part of the default view
        {"die": "Die B", "config": "x"}]}                                       # indexed to another project: skipped
    got = stages.collect("alpha", project, Req())
    assert len(got) == 1
    m = got[0]
    assert (m["die"], m["config"]) == ("Die A", "v3")
    assert {d["name"]: d["em_done"] for d in m["duties"]} == {"rated": True, "peak": True, "burst": True}   # saved summary / result / em_run
    assert m["end3d"] is True and m["end3d_at"] == "2026-10-01T00:00:00"
    assert m["report_reviewed"]["at"] == "2026-10-27T12:00:00"
    out = stages.derive_motor(m, [], [], NOW)
    s = by_id(out)
    assert s["thermal"]["done"] == 1 and s["mechanics"]["done"] == 1 and s["end3d"]["state"] == "done" and s["report"]["state"] == "done"
    assert s["em"]["state"] == "done"


def test_collect_marks_an_unopenable_variant_without_failing(tmp_path, monkeypatch):
    from motor_ai_sim.development import motors as M
    from motor_ai_sim.routes import family
    monkeypatch.setattr(M, "_index", lambda: {"motors": {M._key("Die A", "v3"): {"project_id": "alpha"}}})
    monkeypatch.setattr(M, "_identity", lambda request: {"id": "u"})

    def deny(*a, **k):
        raise PermissionError("no grant")
    monkeypatch.setattr(family, "_require_die_access", deny)

    class Req:
        headers = {}
    got = stages.collect("alpha", {"linked_motors": [{"die": "Die A", "config": "v3"}]}, Req())
    assert got == [{"die": "Die A", "config": "v3", "duties": [], "stored": {}, "end3d": False, "end3d_at": None, "report_reviewed": None}]
