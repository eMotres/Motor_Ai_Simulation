"""Live activity of a development project (owner 2026-10-27) and the auto-link of new variants.

* attribution: every registered run knows the machine it is FOR (``run_target`` + ``JobRecord.target``) - what the request
  names, else the workspace's loaded context;
* ``GET /api/projects/{id}/activity``: only the project's own linked motors, customer-safe rows, the optional host status
  file merged, the same access as the project's motor list;
* a configuration created in a die that has a linked sibling joins the project at once (``motor.autolink``);
* the project list tells when each variant joined (``linked_at``).

Synthetic data only; the "solves" are events.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import jobs as J
from motor_ai_sim import progress as P
from motor_ai_sim import run_target as RT
from motor_ai_sim import workspace as WS
from motor_ai_sim.development import access, activity, autolink, motors, store


# ---------------------------------------------------------------------------
# attribution
# ---------------------------------------------------------------------------

@pytest.fixture()
def jobs_env(monkeypatch, tmp_path):
    monkeypatch.setenv(WS.ENV_WORKSPACES_ROOT, str(tmp_path / "workspaces"))
    monkeypatch.delenv(J.ENV_ASYNC, raising=False)
    monkeypatch.delenv(J.ENV_PER_USER, raising=False)
    J.reset_queue(J.InProcessQueue(workers=1, field_limit=1, persist=False))
    P.reset_registry()
    yield tmp_path
    J.reset_queue(J.InProcessQueue(workers=2, field_limit=2))
    P.reset_registry()


def _as(email: str):
    ws = WS.workspace_for_identity(email)
    return ws, WS.use_workspace(ws), WS.use_caller({"id": email, "is_admin": False, "role": ""})


def _load_context(ws, die: str, config: str, duty: str | None = None) -> None:
    ctx: Dict[str, Any] = {"die": die, "config": config, "at": "2026-10-27T10:00:00"}
    if duty:
        ctx["duty"] = duty
    root = Path(str(ws.root))
    root.mkdir(parents=True, exist_ok=True)
    (root / ".family_context.json").write_text(json.dumps(ctx), encoding="utf-8")


def _run_as(email: str, kind: str, call):
    """Call ``call(seen)`` inside an admitted job of ``kind``, return what ``live_targets`` saw while it ran."""
    ws, use_ws, use_who = _as(email)
    seen: list = []
    with use_ws, use_who:
        out = call(lambda: seen.extend(J.live_targets()))
    return seen, out


def test_a_request_that_names_the_machine_is_attributed_to_it(jobs_env):
    @J.queued("coupled.run", run_id_from=J.body_run_id("coupled"))
    def run(body: Dict[str, Any]):
        return J.live_targets()

    ws, use_ws, use_who = _as("eng@example.test")
    with use_ws, use_who:
        rows = run(body={"die": "DieX", "config": "L40", "duty": "rated", "current_a": 10})
    assert len(rows) == 1
    row = rows[0]
    assert (row["die"], row["config"], row["duty"], row["kind"], row["state"]) == ("DieX", "L40", "rated", "coupled.run", "running")
    # nothing that identifies a run, an account or a body leaves the process
    assert set(row) == {"kind", "state", "die", "config", "duty", "queued_at", "started_at", "progress", "position", "ahead", "eta"}
    assert "eng@example.test" not in json.dumps(rows)


def test_query_style_and_request_model_calls_are_attributed_too(jobs_env):
    class Req:                                  # a pydantic model with die / config (cogging)
        die, config, duty = "DieQ", "L12", None

    @J.queued("thermal.coupled")
    def by_query(die: str = "", config: str = "", duty: str = ""):
        return J.live_targets()

    @J.queued("cogging")
    def by_model(req):
        return J.live_targets()

    ws, use_ws, use_who = _as("eng@example.test")
    with use_ws, use_who:
        q = by_query(die="DieQ", config="L11", duty="peak")
        m = by_model(req=Req())
    assert [(r["die"], r["config"], r["duty"]) for r in q] == [("DieQ", "L11", "peak")]
    assert [(r["die"], r["config"], r["duty"]) for r in m] == [("DieQ", "L12", "")]


def test_a_run_that_names_nothing_belongs_to_the_loaded_context(jobs_env):
    @J.queued("transient", priority=J.Priority.INTERACTIVE, body_keys=("rpm",))
    def transient(rpm: float = 1000.0):
        return J.live_targets()

    ws, use_ws, use_who = _as("eng@example.test")
    _load_context(ws, "DieC", "L50", "continuous")
    with use_ws, use_who:
        rows = transient(rpm=3000.0)
    assert [(r["die"], r["config"], r["duty"], r["kind"]) for r in rows] == [("DieC", "L50", "continuous", "transient")]


def test_the_loaded_duty_is_never_borrowed_for_another_machine(jobs_env):
    ws, use_ws, use_who = _as("eng@example.test")
    _load_context(ws, "DieC", "L50", "continuous")
    with use_ws, use_who:
        same = RT.resolve({"die": "diec", "config": "l50"})
        other = RT.resolve({"die": "DieC", "config": "L51"})
    assert same["duty"] == "continuous"
    assert "duty" not in other and other["config"] == "L51"


def test_a_run_for_no_catalogued_machine_is_not_listed(jobs_env):
    @J.queued("mechanical.modes")
    def modes(n: int = 12):
        return J.live_targets()

    ws, use_ws, use_who = _as("eng@example.test")           # nothing loaded, nothing named
    with use_ws, use_who:
        assert modes(n=6) == []
    assert RT.clean({"die": "OnlyDie"}) == {} and RT.clean(None) == {} and RT.from_call({"body": "x", "n": 3}) == {}


def test_admitted_background_work_is_attributed_through_the_loaded_context(jobs_env):
    ws, use_ws, use_who = _as("eng@example.test")
    _load_context(ws, "DieC", "L50")
    with use_ws, use_who:
        with J.admit("static3d.solve"):
            rows = J.live_targets()
    assert [(r["die"], r["config"], r["kind"]) for r in rows] == [("DieC", "L50", "static3d.solve")]
    assert J.live_targets() == []                           # finished runs are gone


def test_a_waiting_run_is_queued_and_a_running_one_carries_its_progress(jobs_env):
    gate, started = threading.Event(), threading.Event()
    results: Dict[str, Any] = {}

    def first():
        ws, use_ws, use_who = _as("a@example.test")
        _load_context(ws, "DieA", "L1", "rated")
        with use_ws, use_who:
            def work():
                tracker = P.registry().entry(J.current_run_id()).tracker
                tracker.start(10, "march")
                tracker.update(done=4)
                started.set()
                gate.wait(5.0)
            J.run_job("coupled.run", work, target={"die": "DieA", "config": "L1", "duty": "rated"})

    def second():
        ws, use_ws, use_who = _as("b@example.test")
        with use_ws, use_who:
            J.run_job("transient", lambda: None, target={"die": "DieB", "config": "L2"})

    t1 = threading.Thread(target=first, daemon=True)
    t1.start()
    assert started.wait(5.0)
    t2 = threading.Thread(target=second, daemon=True)
    t2.start()
    deadline = time.time() + 5.0
    rows = J.live_targets()
    while len(rows) < 2 and time.time() < deadline:
        time.sleep(0.02)
        rows = J.live_targets()
    gate.set()
    t1.join(5.0)
    t2.join(5.0)
    by_state = {r["state"]: r for r in rows}
    assert by_state["running"]["die"] == "DieA" and by_state["running"]["progress"] == 0.4
    assert by_state["running"]["started_at"]
    assert by_state["queued"]["die"] == "DieB" and by_state["queued"]["progress"] is None and by_state["queued"]["started_at"] is None
    assert [r["state"] for r in rows] == ["running", "queued"]            # running first
    assert J.live_targets() == []


def test_old_job_files_without_a_target_still_load():
    old = {"run_id": "r1", "ws_id": "w", "owner": "o", "kind": "transient"}
    assert J.JobRecord(**old).target == {}


# ---------------------------------------------------------------------------
# the endpoint
# ---------------------------------------------------------------------------

IDENTITIES = {
    "engineer": {"email": "eng@example.test", "role": "engineer"},
    "customer": {"email": "cust@example.test", "role": "customer"},
    "stranger": {"email": "stranger@example.test", "role": "customer"},
}


@pytest.fixture()
def project_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    (tmp_path / "projects").mkdir()
    store.create_project({"id": "alpha", "status": "design",
                          "members": [{"email": "cust@example.test", "role": "customer"}],
                          "linked_motors": [{"die": "Canonical Die", "config": "A"},
                                            {"die": "Canonical Die", "config": "B", "status": "trial"}]})
    store.create_project({"id": "beta", "status": "design", "members": [],
                          "linked_motors": [{"die": "Other Die", "config": "Z"}]})
    monkeypatch.setattr(access, "require_signed_in", lambda request: IDENTITIES[request.headers.get("x-user")])
    monkeypatch.setattr(activity, "_alias_names", lambda canonical: {"alias die"} if canonical == "Canonical Die" else set())
    monkeypatch.setenv(activity.ENV_STATUS, str(tmp_path / "no-such-status.json"))
    activity._worker_cache.update(at=0.0, rows=[])
    app = FastAPI()
    app.include_router(activity.router)
    return TestClient(app), tmp_path


def _row(die, config, state="running", kind="coupled.run", duty="rated", progress=0.5, started=None):
    return {"die": die, "config": config, "duty": duty, "kind": kind, "state": state,
            "queued_at": 1_700_000_000.0, "started_at": started, "progress": progress}


def test_only_the_projects_own_linked_motors_are_returned_matched_like_linked_motors(project_env, monkeypatch):
    client, _ = project_env
    now = time.time()
    monkeypatch.setattr(activity, "app_rows", lambda: [
        _row("canonical die", "a", started=now - 125),                      # case-insensitive
        _row("Alias Die", "B", state="queued", kind="coupled.continuous_rating", started=None, duty="", progress=None),   # alias
        _row("Other Die", "Z"),                                             # another project's motor
        _row("Canonical Die", "NOT-LINKED"),                                # a variant that is not linked
        _row("Canonical Die", "A", kind="mechanical.rotor_stress", progress=None, started=now - 30),
    ])
    r = client.get("/api/projects/alpha/activity", headers={"x-user": "engineer"})
    assert r.status_code == 200, r.text
    rows = r.json()["activity"]
    assert [(x["die"], x["config"], x["state"], x["label"]) for x in rows] == [
        ("Canonical Die", "A", "running", "Coupled thermal run"),      # running first, the oldest start first
        ("Canonical Die", "A", "running", "Mechanics"),
        ("Canonical Die", "B", "queued", "Continuous rating (S1)"),
    ]
    first = rows[0]
    assert first["duty"] == "rated" and first["progress"] == 0.5 and first["started_at"].endswith("+00:00")
    text = json.dumps(r.json())
    assert "Other Die" not in text and "NOT-LINKED" not in text and "run_id" not in text and "@" not in text


def test_members_read_it_like_the_motor_list_and_strangers_do_not(project_env, monkeypatch):
    client, _ = project_env
    monkeypatch.setattr(activity, "app_rows", lambda: [_row("Canonical Die", "A")])
    assert client.get("/api/projects/alpha/activity", headers={"x-user": "customer"}).status_code == 200
    assert client.get("/api/projects/alpha/activity", headers={"x-user": "stranger"}).status_code == 404
    assert client.get("/api/projects/missing/activity", headers={"x-user": "engineer"}).status_code == 404
    # a global engineer reads any project; the customer of alpha learns nothing of beta
    assert client.get("/api/projects/beta/activity", headers={"x-user": "customer"}).status_code == 404


def test_the_host_status_file_is_merged_and_filtered_to_linked_motors(project_env, monkeypatch):
    client, tmp = project_env
    monkeypatch.setattr(activity, "app_rows", lambda: [])
    status = tmp / "status.json"
    monkeypatch.setenv(activity.ENV_STATUS, str(status))
    iso = lambda s: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - s))
    status.write_text(json.dumps({"updated": iso(10), "jobs": [
        {"id": "internal-17", "state": "running", "started": iso(600), "title": "Stage 3 scan /srv/private/path",
         "targets": [{"die": "Canonical Die", "config": "A", "what": "Optimisation step 4"},
                     {"die": "Unlinked", "config": "A", "what": "x"}], "eta_min": 12},
        {"id": "internal-18", "state": "queued", "title": "Report build",
         "targets": [{"die": "canonical die", "config": "b"}]},
        {"id": "internal-19", "state": "done", "targets": [{"die": "Canonical Die", "config": "A", "what": "old"}]},
        {"id": "internal-20", "state": "running", "targets": [{"die": "Other Die", "config": "Z", "what": "not ours"}]},
        "garbage", {"state": "running", "targets": "nope"},
    ]}), encoding="utf-8")
    rows = client.get("/api/projects/alpha/activity", headers={"x-user": "customer"}).json()["activity"]
    assert [(x["config"], x["state"], x["label"], x["kind"]) for x in rows] == [
        ("A", "running", "Optimisation step 4", "host"), ("B", "queued", "Report build", "host")]
    assert rows[0]["eta_min"] == 12 and rows[0]["started_at"].startswith("20")
    assert "internal-17" not in json.dumps(rows) and "Unlinked" not in json.dumps(rows)


@pytest.mark.parametrize("content", [None, "", "not json", "[]", '{"jobs": 3}', '{"jobs": [1, 2]}'])
def test_a_missing_or_invalid_host_file_is_ignored_silently(project_env, monkeypatch, content):
    client, tmp = project_env
    monkeypatch.setattr(activity, "app_rows", lambda: [_row("Canonical Die", "A")])
    status = tmp / "status.json"
    if content is not None:
        status.write_text(content, encoding="utf-8")
    monkeypatch.setenv(activity.ENV_STATUS, str(status))
    r = client.get("/api/projects/alpha/activity", headers={"x-user": "engineer"})
    assert r.status_code == 200 and [x["config"] for x in r.json()["activity"]] == ["A"]


def test_a_dead_schedulers_host_file_is_not_live_work(project_env, monkeypatch):
    client, tmp = project_env
    monkeypatch.setattr(activity, "app_rows", lambda: [])
    status = tmp / "status.json"
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3 * 86400))
    status.write_text(json.dumps({"updated": old, "jobs": [
        {"state": "running", "targets": [{"die": "Canonical Die", "config": "A", "what": "stuck"}]}]}), encoding="utf-8")
    monkeypatch.setenv(activity.ENV_STATUS, str(status))
    assert client.get("/api/projects/alpha/activity", headers={"x-user": "engineer"}).json()["activity"] == []


def test_the_api_process_asks_the_compute_worker_and_caches_the_answer(project_env, monkeypatch):
    client, _ = project_env
    from motor_ai_sim import roles, worker_client
    monkeypatch.setenv(roles.ENV_ROLE, "api")
    calls = []

    def fetch(timeout: float = 2.0):
        calls.append(1)
        return {"activity": [_row("Canonical Die", "A", started=time.time() - 60), "junk"]}
    monkeypatch.setattr(worker_client, "fetch_state", fetch)
    for _ in range(3):
        rows = client.get("/api/projects/alpha/activity", headers={"x-user": "engineer"}).json()["activity"]
        assert [x["config"] for x in rows] == ["A"]
    assert len(calls) == 1                                                  # one worker call for many viewers
    # a worker that is down (or an older build without the key) shows nothing, never an error
    activity._worker_cache.update(at=0.0, rows=[])
    monkeypatch.setattr(worker_client, "fetch_state", lambda timeout=2.0: None)
    assert client.get("/api/projects/alpha/activity", headers={"x-user": "engineer"}).json()["activity"] == []


def test_labels_are_short_customer_safe_phrases():
    assert activity.label_for("transient") == "Electromagnetic run"
    assert activity.label_for("coupled.run") == "Coupled thermal run"
    assert activity.label_for("coupled.continuous_rating") == "Continuous rating (S1)"
    assert activity.label_for("thermal.duty_cycle") == "Time to the temperature limit"
    assert activity.label_for("mechanical.critical_speeds") == "Mechanics"
    assert activity.label_for("optimizer.scan") == "Design search"
    assert activity.label_for("something.new") == "Computation"


def test_the_worker_state_carries_the_activity_without_ids(jobs_env):
    from motor_ai_sim.routes import worker_state
    ws, use_ws, use_who = _as("eng@example.test")
    seen: list = []
    with use_ws, use_who:
        J.run_job("coupled.run", lambda: seen.append(worker_state.state_payload()["activity"]),
                  target={"die": "DieA", "config": "L1"})
    assert [(r["die"], r["config"], r["kind"]) for r in seen[0]] == [("DieA", "L1", "coupled.run")]
    assert "eng@example.test" not in json.dumps(seen[0])


# ---------------------------------------------------------------------------
# auto-link
# ---------------------------------------------------------------------------

@pytest.fixture()
def link_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    (tmp_path / "projects").mkdir()
    store.create_project({"id": "alpha", "status": "design", "members": [], "linked_motors": [
        {"die": "Linked Die", "config": "A", "status": "current"}]})
    store.create_project({"id": "beta", "status": "design", "members": [], "linked_motors": [
        {"die": "Linked Die", "config": "Held", "status": "current"}]})
    store.save_index({"motors": {
        motors._key("Linked Die", "A"): {"project_id": "alpha", "die": "Linked Die", "config": "A"},
        motors._key("Linked Die", "Held"): {"project_id": "beta", "die": "Linked Die", "config": "Held"},
    }})
    monkeypatch.setattr(motors, "_capture_project_motor_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(autolink, "source_dies", lambda die, copied_from=None: [die] + ([copied_from] if copied_from else []))
    return tmp_path


def _audit(project_id):
    path = store.path_for(project_id) / "audit.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_a_new_configuration_joins_the_project_its_die_is_in(link_env):
    joined = autolink.autolink_new_config("linked die", "A2", actor="Eng@Example.test")
    # a motor belongs to ONE project (the index): the die is in two, the first one takes the new variant, the second is left clean
    assert joined == ["alpha"]
    project = store.get_project("alpha")
    row = next(m for m in project["linked_motors"] if m["config"] == "A2")
    assert row == {"die": "Linked Die", "config": "A2", "status": "current"}          # the project's own spelling of the die
    assert project["motor_history"][-1]["linked_at"] and project["motor_history"][-1]["linked_by"] == "eng@example.test"
    entry = _audit("alpha")[-1]
    assert (entry["action"], entry["entity_id"], entry["actor_email"]) == ("motor.autolink", "Linked Die/A2", "eng@example.test")
    assert store.load_index()["motors"][motors._key("Linked Die", "A2")]["project_id"] == "alpha"
    assert [m["config"] for m in store.get_project("beta")["linked_motors"]] == ["Held"]
    assert _audit("beta") == []


def test_it_never_links_into_a_project_the_die_is_not_in(link_env):
    assert autolink.autolink_new_config("Unrelated Die", "A", actor="e@example.test") == []
    for pid in ("alpha", "beta"):
        assert all(m["die"] != "Unrelated Die" for m in store.get_project(pid)["linked_motors"])
        assert not any(e["action"] == "motor.autolink" for e in _audit(pid))


def test_a_die_copied_from_a_linked_one_links_its_new_configuration_under_its_own_name(link_env):
    joined = autolink.autolink_new_config("Linked Die copy 2", "L9", copied_from="Linked Die")
    assert joined == ["alpha"]
    assert {"die": "Linked Die copy 2", "config": "L9", "status": "current"} in store.get_project("alpha")["linked_motors"]


def test_a_motor_that_is_already_linked_is_left_alone_and_bad_names_are_refused(link_env):
    assert autolink.autolink_new_config("Linked Die", "A") == []                  # already linked to alpha
    assert autolink.autolink_new_config("Linked Die", "Held") == []               # held by beta: never stolen
    assert autolink.autolink_new_config("Linked Die", "../x") == []
    assert autolink.autolink_new_config("", "A") == []
    assert [m["config"] for m in store.get_project("alpha")["linked_motors"]] == ["A"]


def test_a_failing_link_never_raises_and_leaves_the_project_as_it_was(link_env, monkeypatch):
    before = store.get_project("alpha")
    before_index = store.load_index()

    def boom(*a, **k):
        raise OSError("injected")
    monkeypatch.setattr(motors, "_sync_grants", boom)
    assert autolink.autolink_new_config("Linked Die", "A3") == []
    assert store.get_project("alpha") == before and store.load_index() == before_index


def test_the_family_hook_never_raises_and_names_the_actor(monkeypatch):
    from motor_ai_sim.routes import family
    seen = []
    monkeypatch.setattr(autolink, "autolink_new_config", lambda die, cfg, **kw: seen.append((die, cfg, kw)) or ["alpha"])
    family._autolink_new_config("D", "C", {"user": {"email": "Eng@Example.test"}}, copied_from="Src")
    assert seen == [("D", "C", {"copied_from": "Src", "actor": "eng@example.test"})]
    monkeypatch.setattr(autolink, "autolink_new_config", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    family._autolink_new_config("D", "C", None)                                    # swallowed


def test_every_way_to_make_a_variant_calls_the_hook():
    src = (Path(__file__).resolve().parents[1] / "src" / "motor_ai_sim" / "routes" / "family.py").read_text(encoding="utf-8")
    for needle in ("_autolink_new_config(die, name, _w)",                          # POST /config
                   "_autolink_new_config(die, new, _w)",                           # configuration copy
                   "_autolink_new_config(name, cfg_name, _w, copied_from=",        # save as new die
                   "_autolink_new_config(target, target_cfg, None, copied_from=die)"):   # automatic die transition
        assert needle in src, needle
    assert 'd["derived_from"] = {"die": die' in src                                  # a die copy remembers its source


# ---------------------------------------------------------------------------
# "Latest" needs to know when a variant joined
# ---------------------------------------------------------------------------

def test_linked_at_comes_from_the_row_or_the_current_link_event():
    project = {"linked_motors": [{"die": "D", "config": "old"}, {"die": "D", "config": "new"}, {"die": "D", "config": "row", "linked_at": "2026-10-27T09:00:00"}],
               "motor_history": [
                   {"die": "D", "config": "new", "linked_at": "2026-10-20T10:00:00", "unlinked_at": "2026-10-21T10:00:00"},
                   {"die": "D", "config": "new", "linked_at": "2026-10-26T10:00:00"},
                   {"die": "d", "config": "NEW", "linked_at": "2026-10-27T08:00:00"}]}
    stamps = motors.linked_at_index(project)
    assert stamps[motors._key("D", "new")] == "2026-10-27T08:00:00"             # the latest un-unlinked event, case-insensitive
    assert stamps[motors._key("D", "row")] == "2026-10-27T09:00:00"
    assert motors._key("D", "old") not in stamps                               # a legacy row has no stamp
