"""One progress bar per RUN, not one per route (migration Stage 4).

What was wrong was never :class:`~motor_ai_sim.progress.ProgressTracker` — it is
the same class, untouched — but its OWNERSHIP: ``routes/thermal.py`` held one
instance, ``routes/mechanical.py`` another, ``routes/coupled.py`` a third and
``routes/simulation.py`` a dict under the literal key ``"current"``.  One live
solve per router FOR THE WHOLE SERVER.  With two accounts that is not cosmetic:
B's Run calls ``start()``, which ZEROES the counters A is watching; A's poll then
reports B's frame count and B's ETA; and the id A's Stop button sends is the one
it read off that shared bar.

Three promises are pinned here, and the third is the one that let this ship:

  (a) a run is addressable by its id, whoever asks;
  (b) two runs in the same router do not touch each other's counters;
  (c) the NO-ARGUMENT form still answers — the caller's newest run in that
      router, and this router's default tracker when there is none.  That is
      what keeps today's frontend (``SolveProgressStrip.tsx`` polls with no
      argument) working while it migrates.
"""
from __future__ import annotations

import time

import pytest

from motor_ai_sim import jobs as J
from motor_ai_sim import progress as P
from motor_ai_sim import workspace as WS


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setenv(WS.ENV_WORKSPACES_ROOT, str(tmp_path / "workspaces"))
    monkeypatch.delenv(P.ENV_TTL, raising=False)
    P.reset_registry()
    J.reset_queue(J.InProcessQueue(workers=4, field_limit=2))
    yield
    P.reset_registry()
    J.reset_queue(J.InProcessQueue(workers=4, field_limit=2))


#: The account the HTTP tests below are signed in as.  A NON-admin one, and
#: that matters: an anonymous caller on this workstation is the OWNER and the
#: owner is an admin (``auth.caller_identity``), so an ownership check tested
#: anonymously would pass for the wrong reason — an admin may cancel anything.
USER = "poller@x.com"


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from motor_ai_sim import auth as _auth
    from motor_ai_sim.api import app

    # The middleware resolves Authorization -> caller_identity -> workspace;
    # patching the identity is the smallest honest way to BE somebody.
    monkeypatch.setattr(_auth, "caller_identity",
                        lambda *a, **kw: {"id": USER, "email": USER,
                                          "tier": "pro", "is_admin": False})
    return TestClient(app)


def _as(email: str):
    """Act as a signed-in caller — workspace and identity, as the middleware does."""
    return (WS.use_workspace(WS.workspace_for_identity(email)),
            WS.use_caller({"id": email, "is_admin": False}))


# ---------------------------------------------------------------------------
# (a) by run id
# ---------------------------------------------------------------------------

def test_a_run_is_addressable_by_its_id():
    reg = P.registry()
    t = reg.tracker("run-1", route="thermal", owner="a@x.com")
    t.start(4, "conduction", kind="field")
    t.update(done=2)

    snap = reg.entry("run-1").snapshot()
    assert snap["step"] == 2 and snap["total"] == 4 and snap["running"] is True
    assert reg.entry("run-1").kind == "field"
    assert reg.get("nope") is None


def test_two_runs_in_one_router_do_not_touch_each_other():
    """THE bug: ``start()`` zeroes the counters, and there was one object.

    A is at 30/40 when B presses Run.  Before Stage 4 A's next poll said 0/96
    and then reported B's frames to the end.
    """
    reg = P.registry()
    a = reg.tracker("run-a", route="transient", owner="a@x.com")
    a.start(40, "fem-solve", kind="transient")
    a.update(done=30)

    b = reg.tracker("run-b", route="transient", owner="b@x.com")
    b.start(96, "fem-solve", kind="transient")
    b.update(done=1)

    assert reg.entry("run-a").snapshot()["step"] == 30
    assert reg.entry("run-a").snapshot()["total"] == 40
    assert reg.entry("run-b").snapshot()["step"] == 1


def test_the_route_proxy_follows_the_run_in_flight():
    """``_progress`` is the same NAME with the same call sites; what it resolves
    to is the tracker of the run this call is inside."""
    prog = P.route_progress("thermal")
    with P.use_run("run-x", route="thermal", owner="a@x.com"):
        prog.start(2, "conduction", kind="field")
        prog.update(done=1)
        assert prog.snapshot()["step"] == 1
    # Outside the run: the router's DEFAULT tracker — untouched by the above.
    assert prog.snapshot()["step"] == 0
    assert P.registry().entry("run-x").snapshot()["step"] == 1


# ---------------------------------------------------------------------------
# (b) whose bar is it
# ---------------------------------------------------------------------------

def test_three_users_each_see_only_their_own_progress():
    """The migration plan's acceptance test for the bar itself.

    Each account polls with NO argument — which is what today's strip does — and
    gets its own run, not the newest run on the server.
    """
    reg = P.registry()
    for who, rid, step in (("a@x.com", "r-a", 3), ("b@x.com", "r-b", 7),
                           ("c@x.com", "r-c", 11)):
        t = reg.tracker(rid, route="transient", owner=who)
        t.start(20, "fem-solve", kind="transient")
        t.update(done=step)

    for who, step in (("a@x.com", 3), ("b@x.com", 7), ("c@x.com", 11)):
        ws, caller = _as(who)
        with ws, caller:
            assert P.poll("transient")["step"] == step, who


def test_an_owned_run_is_not_returned_to_another_callers_no_arg_poll():
    reg = P.registry()
    t = reg.tracker("r-b", route="mechanical", owner="b@x.com")
    t.start(9, "rotor stress", kind="rotor_stress")
    t.update(done=5)
    ws, caller = _as("a@x.com")
    with ws, caller:
        out = P.poll("mechanical")
    assert out["step"] == 0 and out["running"] is False, out


# ---------------------------------------------------------------------------
# (c) the no-argument compatibility promise
# ---------------------------------------------------------------------------

def test_the_no_arg_form_answers_before_anything_has_run(client):
    """A strip that mounts on a cold server must render, not guard for keys."""
    for path in ("/api/thermal/progress", "/api/mechanical/progress",
                 "/api/coupled/progress"):
        out = client.get(path).json()
        assert set(out) == {"running", "step", "total", "elapsed_s", "eta_s",
                            "per_step_s", "frac", "phase", "composition",
                            "ts_start", "kind"}, path
        assert out["running"] is False


def test_the_no_arg_form_finds_the_callers_newest_run(client):
    t = P.registry().tracker("r-1", route="thermal", owner=USER)
    t.start(2, "conduction", kind="field")
    t.update(done=1)
    out = client.get("/api/thermal/progress").json()
    assert out["step"] == 1 and out["kind"] == "field"
    # ...and by id, which is the form the frontend moves to.
    assert client.get("/api/thermal/progress",
                      params={"run_id": "r-1"}).json()["step"] == 1


def test_an_unknown_run_id_is_an_idle_bar_not_a_500(client):
    out = client.get("/api/thermal/progress",
                     params={"run_id": "never-existed"}).json()
    assert out["running"] is False and out["phase"] == "unknown run"


def test_a_queued_run_reports_its_position_and_only_then(client, monkeypatch):
    """``queued``/``position`` appear ONLY while the job is waiting.

    The key set of this payload is pinned by ``tests/test_progress_routes.py``
    (it asserts equality), so the two extra keys are a deliberate exception,
    present exactly when there is a queue position to report.
    """
    import threading

    monkeypatch.setenv(WS.ENV_WORKSPACES_ROOT, "")        # process workspace
    J.reset_queue(J.InProcessQueue(workers=1, field_limit=1, per_user=1))
    hold = threading.Event()
    started = threading.Event()

    def _blocker():
        J.run_job("thermal.field", lambda: (started.set(), hold.wait(5)),
                  priority=J.Priority.FIELD, run_id="busy")

    t = threading.Thread(target=_blocker, daemon=True)
    t.start()
    started.wait(5)

    def _waiter():
        try:
            J.run_job("thermal.field", lambda: None, priority=J.Priority.FIELD,
                      run_id="waiting")
        except BaseException:                             # noqa: BLE001
            pass

    t2 = threading.Thread(target=_waiter, daemon=True)
    t2.start()
    time.sleep(0.3)

    out = client.get("/api/thermal/progress", params={"run_id": "waiting"}).json()
    assert out.get("queued") is True and out.get("position") == 1

    hold.set()
    t.join(5)
    t2.join(5)
    done = client.get("/api/thermal/progress", params={"run_id": "waiting"}).json()
    assert "queued" not in done and "position" not in done


# ---------------------------------------------------------------------------
# TTL eviction
# ---------------------------------------------------------------------------

def test_a_finished_run_is_evicted_after_its_ttl():
    reg = P.ProgressRegistry(ttl_s=0.2)
    t = reg.tracker("old", route="thermal", owner="a@x.com")
    t.start(1, "x")
    t.finish()
    assert reg.entry("old") is not None
    time.sleep(0.35)
    assert reg.entry("old") is None, "a finished run outlived its TTL"


def test_a_running_run_is_never_evicted():
    """However long it has been going.  A ninety-minute PWM transient is the
    reason: it reports one frame every forty seconds, and an eviction on age
    would take the bar away from the run that needs it most."""
    reg = P.ProgressRegistry(ttl_s=0.1)
    t = reg.tracker("long", route="transient", owner="a@x.com")
    t.start(5984, "fem-solve")
    time.sleep(0.3)
    reg.sweep()
    assert reg.entry("long") is not None
    t.finish()
    time.sleep(0.2)
    assert reg.entry("long") is None


def test_the_cap_drops_history_not_live_runs():
    reg = P.ProgressRegistry(ttl_s=3600, cap=5)
    live = reg.tracker("live", route="transient", owner="a@x.com")
    live.start(10, "fem-solve")
    for i in range(20):
        t = reg.tracker("done-%d" % i, route="transient", owner="a@x.com")
        t.start(1, "x")
        t.finish()
    assert len(reg) <= 6
    assert reg.entry("live") is not None


def test_the_default_trackers_are_never_evicted():
    """They are the routers' own idle bars: evicting one would make the
    no-argument poll of a quiet router build a new object on every call."""
    reg = P.reset_registry(P.ProgressRegistry(ttl_s=0.1))
    prog = P.route_progress("mechanical")
    prog.start(1, "mesh", kind="mesh")
    prog.finish()
    time.sleep(0.25)
    reg.sweep()
    assert prog.snapshot()["step"] == 1, "the default tracker was evicted"


# ---------------------------------------------------------------------------
# The two legacy shapes
# ---------------------------------------------------------------------------

def test_the_transient_dict_proxy_is_per_run_and_still_a_dict():
    """``_fem_transient_progress["current"]`` — twelve write sites, unchanged.

    The march writes this dict field by field from inside a per-frame callback,
    at sites that are load-bearing for the Stop button.  Rewriting them to a
    tracker for tidiness is exactly the risk this stage may not take, so the
    NAME keeps behaving like ``{"current": {...}}`` and hands back the dict of
    the run in flight.
    """
    m = P.TransientProgressMap("transient")
    with P.use_run("r-a", route="transient", owner="a@x.com"):
        m["current"] = {"running": True, "step": 0, "total": 40,
                        "ts_start": time.time(), "phase": "fem-solve",
                        "composition": "", "elapsed_s": 0.0, "eta_s": 0.0}
        m["current"]["step"] = 12
        assert m.get("current")["step"] == 12
    with P.use_run("r-b", route="transient", owner="b@x.com"):
        assert m["current"]["step"] == 0, "two runs shared one dict"
    with P.use_run("r-a", route="transient", owner="a@x.com"):
        assert m["current"]["step"] == 12


def test_the_transient_dict_snapshots_in_the_tracker_shape():
    """So ``/api/jobs`` can print a transient beside a thermal run without two
    ETA rules — and so the ETA is 0 until a frame has actually completed."""
    reg = P.registry()
    e = reg.entry("r-t", create=True, route="transient", owner="a@x.com")
    e.raw.update(running=True, step=0, total=40, ts_start=time.time() - 10)
    assert e.snapshot()["eta_s"] == 0.0          # no completed frame, no sample
    e.raw.update(step=10)
    snap = e.snapshot()
    assert snap["frac"] == 0.25 and snap["eta_s"] > 0


def test_the_static3d_state_map_is_per_run():
    """``static3d._solve_state`` was one slot server-wide: a second account's
    solve was refused 409 because a STRANGER was solving, and its cancel flag
    stopped that stranger's run."""
    seed = lambda: {"running": False, "phase": "idle", "cancel": False}
    m = P.RunStateMap("static3d", seed)
    with P.use_run("s-a", route="static3d", owner="a@x.com"):
        m.update(running=True, phase="meshing")
    with P.use_run("s-b", route="static3d", owner="b@x.com"):
        assert m["running"] is False
        m["cancel"] = True
    assert m.for_run("s-a")["phase"] == "meshing"
    assert m.for_run("s-a")["cancel"] is False
    assert m.for_run("s-b")["cancel"] is True
    assert m.for_run("never") is None


# ---------------------------------------------------------------------------
# The job listing
# ---------------------------------------------------------------------------

def test_the_jobs_endpoint_lists_the_callers_runs_with_their_progress(client):
    J.reset_queue(J.InProcessQueue(workers=2, field_limit=2))
    t = P.registry().tracker("job-1", route="thermal.field", owner=USER)

    def _work():
        t.start(2, "conduction", kind="field")
        t.update(done=2)
        return None

    ws, caller = _as(USER)
    with ws, caller:
        rec = J.make_record("thermal.field", priority=J.Priority.FIELD,
                            run_id="job-1")
        J.queue().submit(rec, _work, block=True)

    out = client.get("/api/jobs").json()
    ids = [j["run_id"] for j in out["jobs"]]
    assert "job-1" in ids
    row = [j for j in out["jobs"] if j["run_id"] == "job-1"][0]
    assert row["state"] == J.JobState.DONE
    assert row["progress"]["step"] == 2
    assert out["queue"]["workers"] >= 1

    one = client.get("/api/jobs/job-1").json()
    assert one["run_id"] == "job-1" and one["kind"] == "thermal.field"
    assert client.get("/api/jobs/nope").status_code == 404


def test_cancel_through_the_jobs_endpoint_is_owner_checked(client):
    J.reset_queue(J.InProcessQueue(workers=2, field_limit=2))
    ws, caller = _as("someone-else@x.com")
    with ws, caller:
        rec = J.make_record("duty", priority=J.Priority.DUTY, run_id="theirs")
        J.queue().submit(rec, lambda: None, block=True)
    # The TestClient is anonymous → the process owner, which is not them.
    r = client.post("/api/jobs/theirs/cancel")
    assert r.status_code == 403, r.text[:200]
    assert "another account" in r.json()["detail"]


def test_the_per_route_cancel_delegates_to_the_same_check(client):
    """One rule about who may stop what, not five."""
    J.reset_queue(J.InProcessQueue(workers=2, field_limit=2))
    ws, caller = _as("someone-else@x.com")
    with ws, caller:
        rec = J.make_record("transient", priority=J.Priority.INTERACTIVE,
                            run_id="theirs-2")
        J.queue().submit(rec, lambda: None, block=True)
    r = client.post("/api/simulation/physics/fem_transient/cancel",
                    params={"run_id": "theirs-2"})
    assert r.status_code == 403, r.text[:200]
    r = client.post("/api/coupled/cancel", params={"run_id": "theirs-2"})
    assert r.status_code == 403, r.text[:200]
    # ...and a run nobody owns is still cancellable (the frontend's fresh nonce).
    assert client.post("/api/simulation/physics/fem_transient/cancel",
                       params={"run_id": "brand-new"}).status_code == 200
