"""Job reads are scoped to the account that started the job (an admin reads all).

Pinned: the job list, one job (local record, a job on a node, a progress-only entry) and the legacy simulation result
poll answer for the owner and an admin only - anything else reads as an unknown id; the queue snapshot is counts for a
non-admin and keeps its items for an admin.
"""
from __future__ import annotations

import threading

import pytest
from fastapi import HTTPException

from motor_ai_sim import jobs as J
from motor_ai_sim import progress as P
from motor_ai_sim import workspace as WS
from motor_ai_sim.routes import jobs_api as API

A, B, ADM = "a@example.test", "b@example.test", "admin@example.test"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setenv(WS.ENV_WORKSPACES_ROOT, str(tmp_path / "workspaces"))
    monkeypatch.delenv(J.ENV_ASYNC, raising=False)
    J.reset_queue(J.InProcessQueue(workers=1, field_limit=1))
    P.reset_registry()
    yield
    J.reset_queue(J.InProcessQueue(workers=1, field_limit=1))
    P.reset_registry()


def _as(email, admin=False):
    cm = [WS.use_workspace(WS.workspace_for_identity(email)), WS.use_caller({"id": email, "is_admin": admin})]

    class _Both:
        def __enter__(self):
            for c in cm:
                c.__enter__()

        def __exit__(self, *a):
            for c in reversed(cm):
                c.__exit__(*a)
    return _Both()


def _start(email, hold):
    """A running job of ``email`` that stays running until ``hold`` is set."""
    started = threading.Event()

    def work():
        started.set()
        hold.wait(10)
        return {}
    with _as(email):
        rec = J.make_record("duty", priority=J.Priority.DUTY)
        t = threading.Thread(target=lambda: J.queue().submit(rec, work, block=True), daemon=True)
        t.start()
    assert started.wait(5)
    return rec.run_id, t


def test_one_job_is_read_by_its_owner_and_an_admin_only():
    hold = threading.Event()
    rid, t = _start(A, hold)
    try:
        with _as(A):
            assert API.get_job(rid)["run_id"] == rid
        with _as(ADM, admin=True):
            assert API.get_job(rid)["run_id"] == rid
        for other in (B,):
            with _as(other):
                with pytest.raises(HTTPException) as e:
                    API.get_job(rid)
                assert e.value.status_code == 404 and "no such run" in e.value.detail
    finally:
        hold.set()
        t.join(5)


def test_the_queue_is_counts_for_a_non_admin_and_items_for_an_admin():
    hold = threading.Event()
    rid, t = _start(A, hold)
    try:
        with _as(B):
            q = API.list_jobs()["queue"]
            assert q["running"] == 1 and "items" not in q and "workers" in q
            assert API.list_jobs(all=True)["jobs"] == []
        with _as(ADM, admin=True):
            q = API.list_jobs()["queue"]
            assert [i["run_id"] for i in q["items"]] == [rid]
    finally:
        hold.set()
        t.join(5)


def test_a_progress_only_entry_and_a_node_job_follow_the_same_rule(monkeypatch):
    P.registry().entry("pr1", create=True, route="transient", owner=A)
    with _as(A):
        assert API.get_job("pr1")["state"] == "unknown"
    with _as(B):
        with pytest.raises(HTTPException) as e:
            API.get_job("pr1")
        assert e.value.status_code == 404
    from motor_ai_sim import compute_nodes as CN
    monkeypatch.setattr(CN, "get_job", lambda rid: {"run_id": rid, "owner": A, "state": "queued"} if rid == "nj1" else None)
    with _as(A):
        assert API.get_job("nj1")["run_id"] == "nj1"
    with _as(B):
        with pytest.raises(HTTPException) as e:
            API.get_job("nj1")
        assert e.value.status_code == 404


def test_an_ownerless_progress_entry_is_not_readable_by_another_account():
    P.registry().entry("pr0", create=True, route="transient", owner="")
    with _as(B):
        with pytest.raises(HTTPException) as e:
            API.get_job("pr0")
        assert e.value.status_code == 404
    with _as(ADM, admin=True):
        assert API.get_job("pr0")["state"] == "unknown"


def test_the_legacy_result_poll_is_the_starters_only():
    from motor_ai_sim.routes import simulation as S
    S._jobs["leg1"] = {"owner": A, "status": "done", "progress": 1.0, "result": {"x": 1}, "error": None, "start_t": None}
    try:
        with _as(A):
            assert S.get_result("leg1").result == {"x": 1}
        with _as(ADM, admin=True):
            assert S.get_result("leg1").status == "done"
        with _as(B):
            with pytest.raises(HTTPException) as e:
                S.get_result("leg1")
            assert e.value.status_code == 404
    finally:
        S._jobs.pop("leg1", None)
