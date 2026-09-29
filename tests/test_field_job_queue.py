"""The field-solve queue, pinned.

The 2026-09-03 incident in one sentence: the field views started their own FEM
solves, the same solve could run twice at the same moment, as many could run as
there were requests, and nothing anywhere said any of it was happening.  The
frontend half of the fix is that a field view only solves on a click; this file
holds the server half.

1. DEDUPE — two concurrent requests with the same cache key are ONE solve, and
   both get its result.  Different keys are still two solves.
2. LIMIT — at most ``SB_FIELD_MAX_CONCURRENT`` solves run at once, FIFO.
3. VISIBILITY — whatever is solving or queued is readable, both from
   ``GET /physics/field_busy`` and from the transient-progress response the
   panel already polls.

Every test drives the registry with a stub "solver" gated by a
``threading.Event``, so nothing here touches the FEM.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from motor_ai_sim.field_jobs import FieldJobRegistry


def _run_in_thread(fn, out: list, idx: int, err: list):
    def _body():
        try:
            out[idx] = fn()
        except BaseException as e:            # noqa: BLE001 — reported, not raised
            err.append(e)
    t = threading.Thread(target=_body, daemon=True)
    t.start()
    return t


# ── 1. dedupe ────────────────────────────────────────────────────────────────

def test_two_concurrent_identical_requests_solve_once():
    """The second request WAITS for the first — it does not start a solve.

    This is the measured failure: two byte-identical /fem_field2d requests one
    second apart, each running its own 5-7 minute sliding-band transient.
    """
    reg = FieldJobRegistry(max_concurrent=2)
    started = threading.Event()
    release = threading.Event()
    calls = []

    def solve():
        calls.append("solve")
        started.set()
        assert release.wait(5), "the stub solver was never released"
        return {"payload": 1}

    out: list = [None, None]
    err: list = []
    t1 = _run_in_thread(lambda: reg.run(("k",), "24-frame eddy map", solve),
                        out, 0, err)
    assert started.wait(5), "the first request never reached the solver"
    t2 = _run_in_thread(lambda: reg.run(("k",), "24-frame eddy map", solve),
                        out, 1, err)

    # The twin is parked on the first job, not solving.
    time.sleep(0.2)
    snap = reg.snapshot()
    assert snap["solving"] == 1 and snap["queued"] == 0, snap
    assert snap["items"][0]["waiters"] == 1, snap
    assert len(calls) == 1, "the duplicate request started a SECOND solve"

    release.set()
    t1.join(5); t2.join(5)
    assert not err, err
    assert len(calls) == 1, "the duplicate request started a second solve"
    assert out[0] == {"payload": 1} and out[1] == {"payload": 1}, out
    assert reg.snapshot()["items"] == [], "the finished job stayed in the registry"


def test_different_keys_are_two_solves():
    """Dedupe is per key — a different request is a different picture."""
    reg = FieldJobRegistry(max_concurrent=2)
    release = threading.Event()
    calls: list = []
    lock = threading.Lock()

    def solve(tag):
        with lock:
            calls.append(tag)
        assert release.wait(5)
        return {"tag": tag}

    out: list = [None, None]
    err: list = []
    t1 = _run_in_thread(lambda: reg.run(("a",), "single-frame field",
                                        lambda: solve("a")), out, 0, err)
    t2 = _run_in_thread(lambda: reg.run(("b",), "single-frame field",
                                        lambda: solve("b")), out, 1, err)
    for _ in range(100):
        if len(calls) == 2:
            break
        time.sleep(0.02)
    assert sorted(calls) == ["a", "b"], calls
    release.set()
    t1.join(5); t2.join(5)
    assert not err, err
    assert out == [{"tag": "a"}, {"tag": "b"}], out


def test_a_failed_solve_reaches_the_waiter_as_the_same_error():
    """A twin must not get a silent empty payload when the real one 500s."""
    reg = FieldJobRegistry(max_concurrent=2)
    started = threading.Event()
    release = threading.Event()

    def solve():
        started.set()
        assert release.wait(5)
        raise RuntimeError("FEM solve failed")

    errs: list = []

    def call():
        try:
            reg.run(("k",), "single-frame field", solve)
        except Exception as e:                # noqa: BLE001
            errs.append(e)

    t1 = threading.Thread(target=call, daemon=True); t1.start()
    assert started.wait(5)
    t2 = threading.Thread(target=call, daemon=True); t2.start()
    time.sleep(0.15)
    release.set()
    t1.join(5); t2.join(5)
    assert len(errs) == 2 and all(isinstance(e, RuntimeError) for e in errs), errs
    assert reg.snapshot()["items"] == []


# ── 2. the concurrency limit ─────────────────────────────────────────────────

def test_limit_one_makes_the_second_solve_wait():
    """With ``SB_FIELD_MAX_CONCURRENT=1`` the second (different) key queues."""
    reg = FieldJobRegistry(max_concurrent=1)
    first_in = threading.Event()
    release = threading.Event()
    running: list = []
    lock = threading.Lock()

    def solve(tag):
        with lock:
            running.append(tag)
        if tag == "a":
            first_in.set()
        assert release.wait(5)
        return {"tag": tag}

    out: list = [None, None]
    err: list = []
    t1 = _run_in_thread(lambda: reg.run(("a",), "24-frame loss map",
                                        lambda: solve("a")), out, 0, err)
    assert first_in.wait(5)
    t2 = _run_in_thread(lambda: reg.run(("b",), "single-frame field",
                                        lambda: solve("b")), out, 1, err)
    time.sleep(0.25)
    assert running == ["a"], "the limit did not hold the second solve back"
    snap = reg.snapshot()
    assert snap["solving"] == 1 and snap["queued"] == 1, snap
    assert snap["limit"] == 1
    kinds = {it["state"]: it["kind"] for it in snap["items"]}
    assert kinds["solving"] == "24-frame loss map", snap
    assert kinds["queued"] == "single-frame field", snap

    release.set()
    t1.join(5); t2.join(5)
    assert not err, err
    assert sorted(running) == ["a", "b"], "the queued solve never ran"
    assert out == [{"tag": "a"}, {"tag": "b"}], out


def test_the_queue_is_fifo():
    """A request that arrived first is not overtaken while it waits."""
    reg = FieldJobRegistry(max_concurrent=1)
    first_in = threading.Event()
    release = threading.Event()
    order: list = []
    lock = threading.Lock()

    def solve(tag):
        with lock:
            order.append(tag)
        if tag == "a":
            first_in.set()
            assert release.wait(5)
        return tag

    out: list = [None, None, None]
    err: list = []
    ts = [_run_in_thread(lambda: reg.run(("a",), "k", lambda: solve("a")),
                         out, 0, err)]
    assert first_in.wait(5)
    for i, tag in enumerate(("b", "c"), start=1):
        ts.append(_run_in_thread(
            lambda tg=tag, ix=i: reg.run((tg,), "k", lambda: solve(tg)),
            out, i, err))
        # Arrival ORDER is what FIFO is about, so make it deterministic.
        while reg.snapshot()["queued"] < i:
            time.sleep(0.01)
    release.set()
    for t in ts:
        t.join(5)
    assert not err, err
    assert order == ["a", "b", "c"], order


# ── 3. the registry the UI reads ─────────────────────────────────────────────

def test_snapshot_reports_kind_age_and_a_short_key():
    reg = FieldJobRegistry(max_concurrent=2)
    started = threading.Event()
    release = threading.Event()

    def solve():
        started.set()
        assert release.wait(5)
        return {}

    key = ("sbfield", 0.0, 1.0, "a very long config fingerprint" * 5)
    t = _run_in_thread(lambda: reg.run(key, "8-frame demag probe", solve),
                       [None], 0, [])
    assert started.wait(5)
    snap = reg.snapshot()
    assert snap["solving"] == 1 and snap["queued"] == 0
    item = snap["items"][0]
    assert item["kind"] == "8-frame demag probe"
    assert item["state"] == "solving"
    assert item["since_s"] >= 0.0
    # Short, stable, and NOT the raw key (which carries the fingerprint).
    assert len(item["key_short"]) == 8 and str(key) not in str(item)
    release.set(); t.join(5)


def test_field_busy_rides_on_the_transient_progress_response():
    """The panel polls one endpoint; the field solves come along on it."""
    from motor_ai_sim.routes import simulation as sim

    body = asyncio.run(sim.get_fem_transient_progress())
    fb = body.get("field_busy")
    assert isinstance(fb, dict), body
    assert set(("solving", "queued", "items")).issubset(fb), fb
    assert fb["solving"] == 0 and fb["queued"] == 0 and fb["items"] == []

    # …and the dedicated endpoint answers with the same object.
    assert asyncio.run(sim.get_field_busy()) == fb


def test_field_busy_shows_a_running_field_solve(monkeypatch):
    """A solve in flight is VISIBLE — the whole point of the registry."""
    from motor_ai_sim import field_jobs
    from motor_ai_sim.routes import simulation as sim

    reg = FieldJobRegistry(max_concurrent=2)
    monkeypatch.setattr(field_jobs, "_REGISTRY", reg)
    started = threading.Event()
    release = threading.Event()

    def solve():
        started.set()
        assert release.wait(5)
        return {}

    t = _run_in_thread(lambda: reg.run(("k",), "24-frame eddy map", solve),
                       [None], 0, [])
    assert started.wait(5)
    fb = asyncio.run(sim.get_fem_transient_progress())["field_busy"]
    assert fb["solving"] == 1, fb
    assert fb["items"][0]["kind"] == "24-frame eddy map", fb
    release.set(); t.join(5)
    assert asyncio.run(sim.get_field_busy())["solving"] == 0


# ── 4. the route in front of the solver ──────────────────────────────────────

def test_the_route_dedupes_two_identical_field_requests(monkeypatch):
    """End-to-end on `get_fem_field2d`: same query twice, one solve.

    The wrapper builds the cache key with `_field2d_cache_key` — the SAME
    function the body uses — so "the same request" means one thing to the
    dedupe and to the cache.
    """
    from motor_ai_sim import field_jobs
    from motor_ai_sim.routes import simulation as sim

    reg = FieldJobRegistry(max_concurrent=2)
    monkeypatch.setattr(field_jobs, "_REGISTRY", reg)

    started = threading.Event()
    release = threading.Event()
    calls: list = []
    lock = threading.Lock()

    def fake_impl(**kw):
        with lock:
            calls.append(kw["_key"])
        started.set()
        assert release.wait(5), "the stub solver was never released"
        return {"ok": True, "source": "on-demand solve"}

    monkeypatch.setattr(sim, "_fem_field2d_impl", fake_impl)
    monkeypatch.setattr(sim, "_fem_field_cache", {})

    q = dict(gamma_deg=12.0, I_phase_rms=40.0, n_steps_per_period=24,
             eddy=True, rotor_eddy=True)
    out: list = [None, None]
    err: list = []
    t1 = _run_in_thread(lambda: sim.get_fem_field2d(**q), out, 0, err)
    assert started.wait(5)
    t2 = _run_in_thread(lambda: sim.get_fem_field2d(**q), out, 1, err)
    time.sleep(0.2)

    busy = field_jobs.field_busy()
    assert busy["solving"] == 1 and busy["queued"] == 0, busy
    assert busy["items"][0]["kind"] == "24-frame eddy map", busy

    release.set()
    t1.join(10); t2.join(10)
    assert not err, err
    assert len(calls) == 1, "the identical second request started its own solve"
    assert out[0] == out[1] == {"ok": True, "source": "on-demand solve"}


def test_a_snapshot_only_probe_never_queues(monkeypatch):
    """The probe answers from the store or says no — it must not wait for a
    solve to finish, or the free lookup becomes the slowest call on the page."""
    from motor_ai_sim import field_jobs
    from motor_ai_sim.routes import simulation as sim

    reg = FieldJobRegistry(max_concurrent=1)
    monkeypatch.setattr(field_jobs, "_REGISTRY", reg)
    started = threading.Event()
    release = threading.Event()

    def fake_impl(**kw):
        if kw.get("snapshot_only"):
            return {"ok": False, "no_snapshot": True}
        started.set()
        assert release.wait(5)
        return {"ok": True}

    monkeypatch.setattr(sim, "_fem_field2d_impl", fake_impl)
    monkeypatch.setattr(sim, "_fem_field_cache", {})

    q = dict(gamma_deg=3.0, n_steps_per_period=24, eddy=True)
    t = _run_in_thread(lambda: sim.get_fem_field2d(**q), [None], 0, [])
    assert started.wait(5)
    # The gate is full (limit 1) — the probe still answers immediately.
    t0 = time.time()
    probe = sim.get_fem_field2d(**q, snapshot_only=True)
    assert probe.get("no_snapshot") is True, probe
    assert time.time() - t0 < 2.0, "the probe queued behind a solve"
    release.set(); t.join(5)


def test_the_key_the_queue_uses_is_the_key_the_cache_uses(monkeypatch):
    """One definition of "the same request" — the wrapper's key must be the
    key the body stores its result under, or the dedupe protects nothing."""
    from motor_ai_sim.routes import simulation as sim

    seen: list = []

    def fake_impl(**kw):
        seen.append(kw["_key"])
        return {"ok": True}

    monkeypatch.setattr(sim, "_fem_field2d_impl", fake_impl)
    cache: dict = {}
    monkeypatch.setattr(sim, "_fem_field_cache", cache)

    q = dict(gamma_deg=5.0, n_steps_per_period=8, demag=True)
    sim.get_fem_field2d(**q)
    assert len(seen) == 1
    # The body would have cached under exactly this key; serve it back and the
    # route must answer from the cache without touching the solver again.
    cache[seen[0]] = {"ok": True, "cached": True}
    assert sim.get_fem_field2d(**q) == {"ok": True, "cached": True}
    assert len(seen) == 1, "a cached payload still went to the solver"


@pytest.mark.parametrize("steps,demag,eddy,expected", [
    (0,  False, False, "single-frame field"),
    (0,  True,  False, "8-frame demag probe"),
    (24, False, False, "24-frame loss map"),
    (24, False, True,  "24-frame eddy map"),
])
def test_job_kind_labels(steps, demag, eddy, expected):
    from motor_ai_sim.routes import simulation as sim
    assert sim._field2d_job_kind(steps, demag, eddy) == expected
