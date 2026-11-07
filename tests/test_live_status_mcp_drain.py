"""2026-11-03: a worker drain waited an hour for two MCP "requests in flight" (55 min old, no users, no jobs).

An MCP tool call in JSON-response mode is answered only when the tool returns, and nothing read the client's disconnect
(nginx cuts ``/mcp`` after 120 s), so a call whose tool never returned stayed counted for ever.  Now: the count ends on
every exit (answer, exception, cancellation, client disconnect), an MCP answer that is a server-sent-event stream is a
stream, and an MCP request older than ``mcp_stale_s()`` is ``stale_requests`` - shown, never busy.
"""
from __future__ import annotations

import asyncio

import pytest

from motor_ai_sim import jobs as J
from motor_ai_sim import live_status as L
from motor_ai_sim import roles
from motor_ai_sim import worker


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setenv(roles.ENV_ROLE, "worker")
    monkeypatch.delenv("MCP_STALE_REQUEST_S", raising=False)
    monkeypatch.setattr(L, "_tabs", {})
    monkeypatch.setattr(L, "_inflight", {})
    monkeypatch.setattr(L, "_streams", {})
    monkeypatch.setattr(L, "_background", {})
    monkeypatch.setattr(L, "_notice_path", lambda: tmp_path / "notice.json")
    J.set_draining(False)
    J.reset_queue(J.InProcessQueue(workers=2, persist=False))
    yield
    J.reset_queue()


def _state(snap=None):
    return {"live": snap if snap is not None else L.local_snapshot()}


class _Client:
    """An ASGI client: sends the body, then 'disconnects' when ``leave()`` is called."""

    def __init__(self, body=b'{"jsonrpc":"2.0","id":1,"method":"tools/call"}'):
        self.body = body
        self.sent = False
        self.gone = asyncio.Event()
        self.messages = []

    async def receive(self):
        if not self.sent:
            self.sent = True
            return {"type": "http.request", "body": self.body, "more_body": False}
        await self.gone.wait()
        return {"type": "http.disconnect"}

    async def send(self, message):
        self.messages.append(message)

    def leave(self):
        self.gone.set()


def _scope(method="POST", path="/mcp", accept="application/json, text/event-stream"):
    return {"type": "http", "method": method, "path": path, "headers": [(b"accept", accept.encode())]}


async def _until(cond, timeout=2.0):
    for _ in range(int(timeout / 0.01)):
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached")


def _hung_tool_app(seen):
    """Like McpGate + the SDK in JSON mode: read the body, then wait for the tool's answer - and never read ``receive``
    again (so the client's disconnect is never seen by the app)."""
    async def app(scope, receive, send):
        msg = await receive()
        seen["body"] = msg.get("body")
        try:
            await asyncio.Event().wait()                    # the tool never returns
        except asyncio.CancelledError:
            seen["cancelled"] = True
            raise
    return app


# ── the count ends on every exit ─────────────────────────────────────────────────────────────────────────────────────

def test_a_hung_mcp_call_ends_its_count_when_the_client_disconnects():
    seen = {}

    async def main():
        client = _Client()
        task = asyncio.create_task(L.InflightMiddleware(_hung_tool_app(seen))(_scope(), client.receive, client.send))
        await _until(lambda: L.local_snapshot()["requests_total"] == 1)
        assert not worker.idle(_state()), "while the client waits, a tool call is work"
        client.leave()                                      # nginx gives up after 120 s
        await asyncio.wait_for(task, 2.0)

    asyncio.run(main())
    assert seen["body"].startswith(b'{"jsonrpc"'), "the app still gets the body through the watcher"
    assert seen.get("cancelled"), "the abandoned call is cancelled"
    snap = L.local_snapshot()
    assert snap["requests_total"] == 0 and snap["busy_total"] == 0 and worker.idle(_state(snap))


def test_a_cancelled_request_ends_its_count():
    for path in ("/mcp", "/api/thermal/run"):
        seen = {}

        async def main():
            client = _Client()
            task = asyncio.create_task(L.InflightMiddleware(_hung_tool_app(seen))(_scope(path=path), client.receive,
                                                                                client.send))
            await _until(lambda: L.local_snapshot()["requests_total"] == 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(main())
        assert L.local_snapshot()["requests_total"] == 0, path


def test_a_failing_mcp_call_ends_its_count_and_raises():
    async def app(scope, receive, send):
        await receive()
        raise ValueError("tool crashed")

    async def main():
        client = _Client()
        await L.InflightMiddleware(app)(_scope(), client.receive, client.send)

    with pytest.raises(ValueError, match="tool crashed"):
        asyncio.run(main())
    assert L.local_snapshot()["requests_total"] == 0


def test_an_answered_mcp_call_is_counted_until_its_answer_and_not_cancelled_after_it():
    seen = {}

    async def app(scope, receive, send):
        await receive()
        seen["during"] = L.local_snapshot()["modules"]["mcp"]["requests"]
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b"{}"})
        # after the answer (the SDK's 202 path keeps working here): a disconnect now must not cancel it
        await asyncio.sleep(0.05)
        seen["after"] = True

    async def main():
        client = _Client()
        client.leave()                                      # the client hangs up as soon as it has the answer
        await L.InflightMiddleware(app)(_scope(), client.receive, client.send)
        return client

    client = asyncio.run(main())
    assert seen == {"during": 1, "after": True}
    assert [m["type"] for m in client.messages] == ["http.response.start", "http.response.body"]
    assert L.local_snapshot()["requests_total"] == 0


def test_a_non_mcp_request_is_not_cancelled_by_a_disconnect():
    """A solve that runs inside a request (the passport) keeps counting: only MCP calls have no work of their own."""
    release = {}

    async def app(scope, receive, send):
        await receive()
        release["ev"] = asyncio.Event()
        await release["ev"].wait()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def main():
        client = _Client()
        client.leave()
        task = asyncio.create_task(L.InflightMiddleware(app)(_scope(path="/api/catalog/x/passport"), client.receive,
                                                             client.send))
        await _until(lambda: "ev" in release)
        await asyncio.sleep(0.05)
        assert L.local_snapshot()["requests_total"] == 1 and not task.done()
        release["ev"].set()
        await asyncio.wait_for(task, 2.0)

    asyncio.run(main())
    assert L.local_snapshot()["requests_total"] == 0


def test_the_real_mcp_gate_and_sdk_answer_through_the_watcher(monkeypatch, tmp_path):
    """McpGate + the SDK (stateless, JSON) behind ``InflightMiddleware`` as in api.py: a call is answered, the count
    ends, the plain GET is a stream."""
    pytest.importorskip("mcp")
    from contextlib import asynccontextmanager

    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    from motor_ai_sim import agent_keys as K
    from motor_ai_sim import mcp_app
    monkeypatch.setenv("MCP_KEYS_DIR", str(tmp_path))
    monkeypatch.setenv("TRUSTED_PROXY_HOSTS", "")
    K.reset_quotas()

    @asynccontextmanager
    async def _ls(_app):
        async with mcp_app.lifespan():
            yield

    app = Starlette(routes=[], lifespan=_ls)
    mcp_app.install(app)
    app.add_middleware(L.InflightMiddleware)                # added after McpGate = outside it, as in api.py
    with TestClient(app) as c:
        r = c.post("/mcp", headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        assert r.status_code == 200 and r.json()["result"]["tools"], r.text
        assert L.local_snapshot()["requests_total"] == 0
        assert c.get("/mcp", headers={"Accept": "text/event-stream"}).status_code == 405
        snap = L.local_snapshot()
        assert snap["requests_total"] == 0 and snap["streams_total"] == 0


# ── a streaming MCP answer is a stream ───────────────────────────────────────────────────────────────────────────────

def test_an_mcp_post_answered_with_an_event_stream_counts_as_a_stream():
    seen = {}

    async def app(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream"), (b"cache-control", b"no-cache")]})
        await send({"type": "http.response.body", "body": b"event: message\n\n", "more_body": True})
        seen["snap"] = L.local_snapshot()
        seen["idle"] = worker.idle(_state(seen["snap"]))
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def main():
        client = _Client()
        await L.InflightMiddleware(app)(_scope(), client.receive, client.send)

    asyncio.run(main())
    snap = seen["snap"]
    assert snap["modules"]["mcp"]["requests"] == 0 and snap["modules"]["mcp"]["streams"] == 1
    assert snap["requests_total"] == 0 and snap["streams_total"] == 1 and snap["busy_total"] == 0 and seen["idle"]
    after = L.local_snapshot()
    assert after["requests_total"] == 0 and after["streams_total"] == 0


def test_an_mcp_event_stream_ends_when_the_client_leaves():
    async def app(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/event-stream")]})
        await asyncio.Event().wait()                        # a stream that never ends on its own

    async def main():
        client = _Client()
        task = asyncio.create_task(L.InflightMiddleware(app)(_scope(), client.receive, client.send))
        await _until(lambda: L.local_snapshot()["streams_total"] == 1)
        client.leave()
        await asyncio.wait_for(task, 2.0)

    asyncio.run(main())
    assert L.local_snapshot()["streams_total"] == 0


# ── the ceiling: a stale MCP request is shown, never busy ────────────────────────────────────────────────────────────

def test_a_stale_mcp_request_does_not_block_but_is_shown():
    """The production picture: mcp requests 2, oldest 3294.5 s, users 0, jobs 0."""
    now = 10_000.0
    L._inflight.update({1: ("mcp", now - 3294.5), 2: ("mcp", now - 3000.0)})
    snap = L.local_snapshot(now=now)
    mcp = snap["modules"]["mcp"]
    assert mcp["requests"] == 0 and mcp["stale_requests"] == 2 and mcp["oldest_stale_s"] == 3294.5
    assert snap["requests_total"] == 0 and snap["stale_total"] == 2 and snap["busy_total"] == 0
    assert L.idle(snap, ["all"]) and L.idle(snap, ["mcp"])
    assert worker.idle(_state(snap)), "the worker drain no longer waits for a stale MCP request"


def test_a_fresh_mcp_request_still_blocks():
    now = 10_000.0
    L._inflight.update({1: ("mcp", now - 30.0), 2: ("mcp", now - 3294.5)})
    snap = L.local_snapshot(now=now)
    mcp = snap["modules"]["mcp"]
    assert mcp["requests"] == 1 and mcp["oldest_request_s"] == 30.0 and mcp["stale_requests"] == 1
    assert snap["requests_total"] == 1 and snap["busy_total"] == 1
    assert not L.idle(snap, ["all"]) and not worker.idle(_state(snap))


def test_the_ceiling_is_15_min_and_can_be_set(monkeypatch):
    now = 10_000.0
    L._inflight.update({1: ("mcp", now - 899.0)})
    assert L.mcp_stale_s() == 900.0 and L.local_snapshot(now=now)["requests_total"] == 1
    monkeypatch.setenv("MCP_STALE_REQUEST_S", "600")
    assert L.local_snapshot(now=now)["stale_total"] == 1
    monkeypatch.setenv("MCP_STALE_REQUEST_S", "nonsense")
    assert L.mcp_stale_s() == 900.0


def test_an_old_request_of_another_module_still_blocks():
    """Only MCP requests go stale: a passport generated inside a request is real work, however long it runs."""
    now = 10_000.0
    L._inflight.update({1: ("motors", now - 5000.0)})
    snap = L.local_snapshot(now=now)
    assert snap["modules"]["motors"]["requests"] == 1 and "stale_requests" not in snap["modules"]["motors"]
    assert not L.idle(snap, ["all"]) and not worker.idle(_state(snap))


def test_a_running_job_still_blocks_beside_a_stale_mcp_request():
    import threading
    gate = threading.Event()
    q = J.queue()
    t = threading.Thread(target=lambda: J.run_job("agent.coupled", lambda: gate.wait(5)))
    t.start()
    try:
        for _ in range(200):
            if q.snapshot()["running"] == 1:
                break
            gate.wait(0.01)
        L._inflight.update({1: ("mcp", 0.0)})               # opened at the epoch: very stale
        state = _state(L.local_snapshot())
        assert state["live"]["jobs"]["running"] == 1 and state["live"]["stale_total"] == 1
        assert not worker.idle(state) and not L.idle(state["live"], ["all"])
    finally:
        gate.set()
        t.join(5)


def test_the_api_merges_the_workers_stale_requests_without_counting_them(monkeypatch):
    monkeypatch.setenv(roles.ENV_ROLE, "api")
    monkeypatch.setenv("WORKSPACES_ROOT", "")
    api = L.local_snapshot(now=10_000.0)
    worker_live = {"jobs": {"running": 0, "queued": 0, "kinds": {}}, "requests_total": 0, "stale_total": 2,
                   "streams_total": 0,
                   "modules": {"mcp": {"users": 0, "requests": 0, "oldest_request_s": 0.0, "stale_requests": 2,
                                       "oldest_stale_s": 3294.5}}}
    snap = L.merge_worker(api, {"live": worker_live, "draining": False})
    assert snap["modules"]["mcp"] == {"users": 0, "requests": 0, "oldest_request_s": 0.0, "stale_requests": 2,
                                      "oldest_stale_s": 3294.5}
    assert snap["stale_total"] == 2 and snap["requests_total"] == 0 and L.idle(snap, ["all"])
