"""Tests -> "Compute it" (owner 2026-11-02): the action carries ``gamma_deg``, the click creates a TEST duty and queues its
solve, and only an administrator can do it.

No solver: the queue is a stub that records the parked work, the solve is replaced by a canned transient answer; the die
folder is a temp directory.  SYNTHETIC numbers only.
"""
from __future__ import annotations

import pytest
import yaml
from fastapi import HTTPException
from fastapi.testclient import TestClient

from motor_ai_sim import jobs as J
from motor_ai_sim.motor_tests import comparison, compute, model_source, models
from motor_ai_sim.motor_tests import routes as MTR  # noqa: F401  (the router must import with compute)

ADMIN = "admin@example.com"
ENGINEER = "engineer@example.com"
DIE, CONFIG = "Demo die", "L50"
BASE = f"/api/motor-tests/{DIE}/{CONFIG}"
LIB = {"magnet": {}}


def _doc():
    return {"winding": {"star_delta": "star", "connection": "2S"}, "default_duty": "rated", "duties": [
        {"name": "rated", "mode": "motor", "rpm": 3000, "current_arms": 100.0, "gamma_deg": 20.0,
         "mesh": {"mesh.meshSize": 1.0, "mesh.nSectors": 2},
         "summary": {"rpm": 3000, "I_phase_rms_A": 100, "gamma_deg": 21.0, "T_em_avg_Nm": 30.0, "coil_temp_C": 60.0}},
        {"name": "light", "mode": "motor", "rpm": 3000, "current_arms": 20.0, "gamma_deg": 5.0},
        {"name": "bench 1", "mode": "motor", "rpm": 1000, "current_arms": 50.0, "gamma_deg": 99.0, "kind": "test"}]}


def _model():
    return model_source.from_docs({}, _doc(), {}, LIB)


def _action(rpm, cur, name=None, gamma="derive", die="D", config="C", model=None):
    a = comparison._create_duty(die, config, rpm=rpm, current=cur, name=name, model=model or _model())
    if gamma != "derive":
        a["body"]["duty"].pop("gamma_deg") if gamma is None else a["body"]["duty"].update(gamma_deg=gamma)
    return a


# ── gamma_deg in the action ─────────────────────────────────────────────────────────────────

def test_the_model_carries_each_duty_angle_and_the_run_angle_wins():
    rows = {d["name"]: d for d in _model()["duties"]}
    assert rows["rated"]["gamma_deg"] == 21.0                      # the run's own angle outranks the duty's setting
    assert rows["light"]["gamma_deg"] == 5.0 and rows["bench 1"]["kind"] == "test"
    assert _model()["sim_gamma_deg"] == 20.0                       # the default duty's own setting


def test_gamma_is_the_nearest_ordinary_duty_by_current_never_a_test_duty():
    m = _model()
    assert comparison.gamma_for(m, 90.0)[0] == 21.0                # nearest to 100 A
    assert comparison.gamma_for(m, 25.0)[0] == 5.0                 # nearest to 20 A
    g, src = comparison.gamma_for(m, 50.0)                         # the test duty at 50 A (99 deg) is NOT a source
    assert g in (21.0, 5.0) and "bench 1" not in src


def test_gamma_falls_back_to_the_configuration_angle_then_zero_and_noload_is_zero():
    only_test = {"winding": {}, "simulation": {"gamma_deg": 12.5}, "duties": [
        {"name": "t", "rpm": 1000, "current_arms": 10, "gamma_deg": None, "kind": "test"}]}
    m = model_source.from_docs({}, only_test, {}, LIB)
    assert comparison.gamma_for(m, 30.0) == (12.5, "the configuration's own angle")
    assert comparison.gamma_for({"duties": []}, 30.0) == (0.0, "default 0 deg")
    assert comparison.gamma_for(_model(), 0.0)[0] == 0.0           # no torque current: no angle


def test_the_load_action_body_has_gamma_deg_from_the_nearest_duty():
    rec = models.normalize({"kind": "load", "date": "2026-01-15",
                            "rows": [{"rpm": 1491, "i_phase_a": 21.0, "torque_nm": 4.0}]}, who="eng@example.test")
    out = comparison.compare_record(rec, _model(), "D", "C")
    act = out["groups"][0]["rows"][0]["action"]
    duty = act["body"]["duty"]
    assert duty["gamma_deg"] == 5.0 and duty["kind"] == "test" and duty["name"] == "test 1491 rpm 21 A"
    assert "light" in act["gamma_source"]


def test_action_key_rounds_rpm_to_10_and_current_to_half_an_ampere():
    assert comparison.action_key(1491, 13.4) == comparison.action_key(1494, 13.3)
    assert comparison.action_key(1491, 13.4) != comparison.action_key(1491, 13.9)
    assert comparison.action_key(1491, 13.4) != comparison.action_key(1520, 13.4)


# ── prepare(): validation and the de-duplication of bulk "compute all missing" ──────────────

def test_prepare_deduplicates_by_rpm_10_and_current_half_ampere():
    acts = [_action(1491, 13.4, die=DIE, config=CONFIG), _action(1494, 13.3, die=DIE, config=CONFIG),
            _action(1491, 13.9, die=DIE, config=CONFIG), _action(2000, 13.4, die=DIE, config=CONFIG)]
    points, skipped = compute.prepare(DIE, CONFIG, acts, _model(), _doc()["duties"])
    assert [p["name"] for p in points] == ["test 1491 rpm 13.4 A", "test 1491 rpm 13.9 A", "test 2000 rpm 13.4 A"]
    assert [s["name"] for s in skipped] == ["test 1494 rpm 13.3 A"] and skipped[0]["reason"] == "repeat"


def test_prepare_fills_a_missing_gamma_from_the_stored_duties():
    a = _action(1491, 95.0, die=DIE, config=CONFIG, gamma=None)
    assert "gamma_deg" not in a["body"]["duty"]
    (pt,), _ = compute.prepare(DIE, CONFIG, [a], _model(), [])
    assert pt["gamma_deg"] == 21.0 and "rated" in pt["gamma_source"]


@pytest.mark.parametrize("mutate", [
    lambda a: a["body"].update(die="Other"),                       # another motor than the URL's
    lambda a: a["body"]["duty"].update(kind="operating"),          # only a test point
    lambda a: a["body"]["duty"].update(rpm=0),
    lambda a: a["body"]["duty"].update(current_arms=-1),
    lambda a: a["body"]["duty"].update(mode="sideways"),
    lambda a: a["body"]["duty"].update(gamma_deg=900),
    lambda a: a.update(kind="delete_duty"),
])
def test_prepare_refuses_a_malformed_action(mutate):
    a = _action(1491, 13.4, die=DIE, config=CONFIG)
    mutate(a)
    with pytest.raises(HTTPException) as e:
        compute.prepare(DIE, CONFIG, [a], _model(), [])
    assert e.value.status_code == 422


def test_prepare_never_overwrites_an_ordinary_duty():
    a = _action(3000, 100.0, name="rated", die=DIE, config=CONFIG)
    points, skipped = compute.prepare(DIE, CONFIG, [a], _model(), _doc()["duties"])
    assert points == [] and "ordinary duty" in skipped[0]["reason"]
    # a test duty of the same name is a re-solve of that point
    b = _action(1000, 50.0, name="bench 1", die=DIE, config=CONFIG)
    points, _ = compute.prepare(DIE, CONFIG, [b], _model(), _doc()["duties"])
    assert [p["name"] for p in points] == ["bench 1"]


def test_the_standard_solve_arguments_follow_the_reference_duty():
    duties = _doc()["duties"]
    ref = compute._reference_duty(duties, 90.0)
    assert ref["name"] == "rated"                                  # nearest ordinary duty WITH stored mesh / summary
    pt = {"name": "p", "mode": "motor", "rpm": 1491.0, "current_arms": 90.0, "gamma_deg": 21.0}
    pay = {"winding": {"n_parallel": 2, "connection": "2S"}, "sim": {"star_delta": "star", "daxis_deg": 33.0}}
    kw = compute._solve_kwargs(pt, pay, ref, {"mesh_size_mm": 1.0, "n_sectors": 2}, "run1")
    assert kw["I_phase_rms"] == 90.0 and kw["rpm"] == 1491.0 and kw["gamma_deg"] == 21.0
    assert kw["n_parallel"] == 2 and kw["daxis_deg"] == 33.0 and kw["coil_temp_c"] == 60.0
    assert kw["mesh_size_mm"] == 1.0 and kw["n_sectors"] == 2 and kw["ledger"] is False and kw["eddy"] is True


# ── the route: admin only, creates a TEST duty with gamma_deg, queues the solve ─────────────

class _FakeQueue:
    """Records the parked work instead of running a solve."""

    def __init__(self):
        self.parked = []

    def submit(self, rec, work=None, block=None):
        rec.position = len(self.parked) + 1
        self.parked.append((rec, work))
        raise J.JobAccepted(rec)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from motor_ai_sim import auth, users as U
    from motor_ai_sim.api import app
    from motor_ai_sim.routes import family as fam

    root = tmp_path / "dies"
    (root / DIE).mkdir(parents=True)
    (root / DIE / "die.yaml").write_text(yaml.safe_dump({
        "name": DIE, "locked": False, "created": "2026-10-13T10:00:00",
        "geometry": {"num_slots": 24, "num_poles": 20, "stator_diameter": 200.0, "motor_length": 50.0}},
        sort_keys=False), encoding="utf-8")
    (root / DIE / f"{CONFIG}.yaml").write_text(yaml.safe_dump(
        {"name": CONFIG, "die": DIE, "role": "motor", "geometry_overrides": {"motor_length": 50.0},
         "winding": {"connection": "star"}, **_doc()}, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(fam, "_DIES_DIR", root)
    monkeypatch.setenv("MOTRES_PRODUCTION_ROOT", str(tmp_path / "production"))
    monkeypatch.setenv("MOTRES_MOTOR_TESTS_ROOT", str(tmp_path / "motor_tests"))
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-motor-tests-1234")
    monkeypatch.delenv("CATALOG_GRANT_ALL_REGISTERED", raising=False)
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(tmp_path / "admin_audit.jsonl"))
    monkeypatch.setattr(model_source, "collect", lambda die, config: model_source.from_docs({}, _doc(), {}, LIB))
    queue = _FakeQueue()
    monkeypatch.setattr(J, "queue", lambda: queue)
    U.create_user(ADMIN, "password-admin", role="admin", name="Admin")
    U.create_user(ENGINEER, "password-eng", role="engineer", name="Engineer")
    client = TestClient(app)
    h = {e: {"Authorization": f"Bearer {U.issue_token(e)}"} for e in (ADMIN, ENGINEER)}
    r = client.post(f"/api/admin/production/{DIE}/{CONFIG}", headers=h[ADMIN], json={
        "title": {"en": "t", "zh": "t", "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}},
        "order_quantity": 1, "members": [{"email": ENGINEER, "role": "engineer"}]})
    assert r.status_code == 200, r.text
    return client, h, root, queue


def _stored(root):
    return {d["name"]: d for d in yaml.safe_load((root / DIE / f"{CONFIG}.yaml").read_text("utf-8"))["duties"]}


def test_compute_is_admin_only_and_writes_nothing_for_anybody_else(env):
    client, h, root, queue = env
    body = {"actions": [_action(1491, 13.4, die=DIE, config=CONFIG)]}
    assert client.post(BASE + "/compute", json=body).status_code == 401                     # anonymous
    eng = client.post(BASE + "/compute", headers=h[ENGINEER], json=body)                   # a production engineer
    assert eng.status_code == 403 and "administrator" in eng.json()["detail"]
    assert "test 1491 rpm 13.4 A" not in _stored(root) and queue.parked == []


def test_compute_creates_a_test_duty_with_gamma_and_queues_its_solve(env):
    client, h, root, queue = env
    r = client.post(BASE + "/compute", headers=h[ADMIN], json={"actions": [
        _action(1491, 21.0, die=DIE, config=CONFIG), _action(1494, 20.8, die=DIE, config=CONFIG)]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert [j["duty"] for j in out["jobs"]] == ["test 1491 rpm 21 A"]                       # the repeat is dropped
    assert out["skipped"] == [{"name": "test 1494 rpm 20.8 A", "reason": "repeat"}]
    job = out["jobs"][0]
    assert job["state"] == "queued" and job["position"] == 1 and job["run_id"]
    duty = _stored(root)["test 1491 rpm 21 A"]
    assert duty["kind"] == "test" and duty["gamma_deg"] == 5.0 and duty["current_arms"] == 21.0 and duty["rpm"] == 1491.0
    assert "summary" not in duty                                                            # nothing is solved yet
    (rec, work), = queue.parked
    assert rec.kind == compute.JOB_KIND and rec.run_id == job["run_id"]
    assert callable(work)
    # the ordinary duties are untouched, and the audit line says who computed what
    assert _stored(root)["rated"]["current_arms"] == 100.0 and "kind" not in _stored(root)["rated"]
    audit = client.get(BASE + "/audit", headers=h[ADMIN]).json()["audit"]
    assert audit[-1]["action"] == "compute" and audit[-1]["who"] == ADMIN


def test_compute_refuses_a_point_that_would_overwrite_an_ordinary_duty(env):
    client, h, root, queue = env
    a = _action(3000, 100.0, name="rated", die=DIE, config=CONFIG)
    r = client.post(BASE + "/compute", headers=h[ADMIN], json={"actions": [a]})
    assert r.status_code == 200 and r.json()["jobs"] == []
    assert "ordinary duty" in r.json()["skipped"][0]["reason"]
    assert queue.parked == [] and _stored(root)["rated"]["gamma_deg"] == 20.0
    assert client.post(BASE + "/compute", headers=h[ADMIN], json={"actions": []}).status_code == 422


def test_the_queued_work_files_the_summary_on_the_test_duty_and_the_comparison_reads_it(env, monkeypatch):
    client, h, root, queue = env
    client.post(BASE + "/compute", headers=h[ADMIN], json={"actions": [_action(1491, 21.0, die=DIE, config=CONFIG)]})
    (rec, work), = queue.parked
    summary = {"rpm": 1491.0, "I_phase_rms_A": 21.0, "gamma_deg": 5.0, "T_em_avg_Nm": 4.2, "efficiency": 0.9}
    monkeypatch.setattr(compute, "_solve", lambda die, config, pt, w, tmp, run_id: (
        {"summary": summary}, {"mesh.meshSize": 1.0}))
    assert work() == {"ok": True, "duty": "test 1491 rpm 21 A"}
    duty = _stored(root)["test 1491 rpm 21 A"]
    assert duty["kind"] == "test" and duty["summary"]["T_em_avg_Nm"] == 4.2 and duty["gamma_deg"] == 5.0
    # the comparison now has the model point: Delta % fills in
    doc = yaml.safe_load((root / DIE / f"{CONFIG}.yaml").read_text("utf-8"))
    m = model_source.from_docs({}, doc, {}, LIB)
    rec2 = models.normalize({"kind": "load", "date": "2026-01-15",
                             "rows": [{"rpm": 1491, "i_phase_a": 21.0, "torque_nm": 4.0}]}, who="eng@example.test")
    row = comparison.compare_record(rec2, m, DIE, CONFIG)["groups"][0]["rows"][0]
    assert row["status"] == "ok" and row["model"] == pytest.approx(4.2) and row["delta_pct"] == pytest.approx(5.0)


def test_a_solve_without_a_summary_fails_loudly_and_changes_nothing(env, monkeypatch):
    client, h, root, queue = env
    client.post(BASE + "/compute", headers=h[ADMIN], json={"actions": [_action(1491, 21.0, die=DIE, config=CONFIG)]})
    (rec, work), = queue.parked
    monkeypatch.setattr(compute, "_solve", lambda *a, **k: ({"error": "x"}, None))
    with pytest.raises(RuntimeError, match="no summary"):
        work()
    assert "summary" not in _stored(root)["test 1491 rpm 21 A"]


def test_the_compute_route_is_a_worker_path_and_the_rest_of_the_tests_api_is_not():
    from motor_ai_sim import roles
    assert roles.is_worker_path("/api/motor-tests/Demo die/L50/compute")
    assert not roles.is_worker_path("/api/motor-tests/Demo die/L50")
    assert not roles.is_worker_path("/api/motor-tests/Demo die/L50/tests")


def test_the_solve_runs_in_a_sandbox_workspace_with_the_stored_machine_and_never_records(env, monkeypatch, tmp_path):
    """``_solve`` builds the sandbox config from the stored configuration and calls the transient route inside it."""
    from motor_ai_sim import run_recording as RR
    from motor_ai_sim import workspace as WS
    from motor_ai_sim.routes import simulation as SIM
    client, h, root, queue = env
    seen = {}

    def fake_transient(**kw):
        ws = WS.workspace()
        seen["kw"], seen["ws_root"], seen["recording"] = kw, ws.root, RR.recording()
        seen["cfg"] = yaml.safe_load(open(ws.config_file, encoding="utf-8"))
        return {"summary": {"rpm": kw["rpm"]}}
    monkeypatch.setattr(SIM, "get_fem_transient", fake_transient)
    pt = {"name": "bench 1", "mode": "motor", "rpm": 1000.0, "current_arms": 50.0, "gamma_deg": 21.0}
    sandbox = tmp_path / "sandbox"
    answer, mesh_block = compute._solve(DIE, CONFIG, pt, {"authorization": None}, sandbox, "runX")
    assert answer == {"summary": {"rpm": 1000.0}}
    assert mesh_block == {"mesh.meshSize": 1.0, "mesh.nSectors": 2}                         # the reference duty's fidelity
    kw = seen["kw"]
    assert kw["I_phase_rms"] == 50.0 and kw["gamma_deg"] == 21.0 and kw["ledger"] is False and kw["run_id"] == "runX"
    assert kw["mesh_size_mm"] == 1.0 and kw["n_sectors"] == 2 and kw["coil_temp_c"] == 60.0
    assert seen["ws_root"] == sandbox and seen["recording"] is False                       # nothing filed under any duty
    sim = seen["cfg"]["simulation"]
    assert sim["current_a"] == 50.0 and sim["gamma_deg"] == 21.0 and sim["rpm"] == 1000.0
    assert seen["cfg"]["geometry"]["num_slots"] == 24 and seen["cfg"]["geometry"]["motor_length"] == 50.0
    assert WS.workspace().root != sandbox                                                   # the caller's workspace is back
