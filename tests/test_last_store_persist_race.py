"""The ``.last_*`` stores must survive a second thread writing while they persist.

THE INCIDENT (2026-09-15).  A full pytest run logged, from the modal route's
setup::

    WARNING motor_ai_sim.routes.mechanical:mechanical.py:340
        could not persist the last mechanical result:
        dictionary changed size during iteration

``_persist_last`` catches everything on purpose — "a viewer convenience never
breaks a solve" — so the only visible symptom is the one the user reported as a
blank tab after a restart: the pickle was never written.

TWO races produced it, and both are tested here.

1.  THE ENTRY.  Every cache-hit branch of the mechanical routes published its
    answer and then went on editing it::

        out = dict(hit)
        _remember_last("modes", out, _params, out.get("geo_fingerprint"))
        out["cached"] = True            # <- the pickler is already walking `out`
        if not shapes:
            out.pop("field", None)      # <- and now it is resized

    ``_persist_last`` pickles on a BACKGROUND thread, ``pickle`` walks a dict it
    is handed, and a dict resized mid-walk raises ``RuntimeError``.  The store
    now keeps a private shallow copy of ``result``.

2.  THE STORE.  ``{k: v for k, v in _LAST.items()}`` walked the live
    ``workspace.BoundedStore``: ``BoundedStore.__iter__`` materialised its key
    list straight out of ``self._d.keys()``, so a concurrent ``__setitem__`` —
    another solve, or the store's own cap-eviction — resized it mid-walk with
    the identical message.  ``BoundedStore.snapshot()`` takes that copy under
    the store's own lock and every persist path now uses it.

The tests below hold a writer thread on each half while the persist runs, for
all three routes, and assert what the incident lost: the file is written, it is
readable, and nothing was logged about it.
"""
from __future__ import annotations

import json
import logging
import pickle
import threading

import pytest

#: How long one persist may take before the test calls it hung.
_JOIN_S = 30.0
#: How many flooded persist rounds each route gets.  Pre-fix, one was enough on
#: this machine; twenty five makes it certain without making the suite slow.
_ROUNDS = 25


# ─────────────────────────────────────────────────────────────────────────────
#  helpers
# ─────────────────────────────────────────────────────────────────────────────

def _big_result(tag: str) -> dict:
    """A solve answer wide enough that pickling it takes measurable time."""
    out = {"geo_fingerprint": "fp-" + tag, "cached": False,
           "field": {"vm": list(range(4000)), "nodes": list(range(4000))}}
    for i in range(300):
        out["k%03d" % i] = [float(i)] * 32
    return out


class _Churn:
    """Threads that hammer the two racing surfaces for the length of a test."""

    def __init__(self) -> None:
        self.stop = threading.Event()
        self.threads: list = []
        self.error: list = []

    def spawn(self, fn) -> None:
        def _run():
            try:
                while not self.stop.is_set():
                    fn()
            except Exception as exc:                # noqa: BLE001
                self.error.append(exc)

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        self.threads.append(t)

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.stop.set()
        for t in self.threads:
            t.join(_JOIN_S)


def _entry_churn(d: dict):
    """The cache-hit branches' exact move: keep editing a published dict."""
    n = {"i": 0}

    def _tick():
        i = n["i"]
        n["i"] = i + 1
        d["cached"] = True
        d["churn%d" % (i % 64)] = i
        d.pop("churn%d" % (i % 64), None)
        d.pop("cached", None)

    return _tick


def _store_flood(store, cap: int):
    """Write the store past its cap from another thread, for ever."""
    n = {"i": 0}

    def _tick():
        i = n["i"]
        n["i"] = i + 1
        store["ghost-%d" % (i % (cap * 4))] = {"result": {"i": i}}

    return _tick


def _persist_and_wait(call) -> None:
    """Run ``call`` and join whatever background writer it started."""
    before = set(threading.enumerate())
    call()
    for t in threading.enumerate():
        if t not in before:
            t.join(_JOIN_S)
            assert not t.is_alive(), "the persist thread never finished"


def _complaints(caplog, *needles) -> list:
    return [r.getMessage() for r in caplog.records
            if r.levelno >= logging.WARNING
            and any(s in r.getMessage() for s in needles)]


# ─────────────────────────────────────────────────────────────────────────────
#  the store itself
# ─────────────────────────────────────────────────────────────────────────────

def test_bounded_store_snapshot_survives_a_flooding_writer():
    """``snapshot()`` never raises while another thread evicts past the cap.

    The reduced form of the incident: the comprehension the persist paths used
    to run, against a store being written at full speed.
    """
    from motor_ai_sim.workspace import BoundedStore

    st = BoundedStore("ws-test", "race", 16)
    with _Churn() as ch:
        ch.spawn(_store_flood(st, 16))
        for _ in range(3000):
            snap = st.snapshot()
            assert isinstance(snap, dict)
            assert len(snap) <= 16, "the cap is not honoured under the lock"
            # Plain keys, not the ``(ws_id, key)`` pairs the store really holds.
            assert all(not isinstance(k, tuple) for k in snap)
        assert not ch.error, ch.error


def test_state_mapping_exposes_snapshot():
    """The module-level NAME the routes hold must answer ``snapshot()`` too."""
    from motor_ai_sim import workspace as ws

    m = ws.ws_map("test.persist_race", 8)
    m["a"] = 1
    m["b"] = 2
    try:
        snap = m.snapshot()
        assert snap == {"a": 1, "b": 2}
        # A copy, not a view: mutating the store must not move under a pickler.
        m["c"] = 3
        assert "c" not in snap
    finally:
        m.clear()


# ─────────────────────────────────────────────────────────────────────────────
#  the three routes
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def _mech(tmp_path, monkeypatch):
    from motor_ai_sim.routes import mechanical as mech

    p = tmp_path / ".last_mechanical.pkl"
    monkeypatch.setattr(mech, "_last_store_path", lambda: str(p))
    mech._set_last_loaded(True)             # never read the sandbox's own store
    mech._LAST.clear()
    try:
        yield mech, p
    finally:
        mech._LAST.clear()


@pytest.fixture
def _therm(tmp_path, monkeypatch):
    from motor_ai_sim.routes import thermal as th

    p = tmp_path / ".last_thermal.pkl"
    monkeypatch.setattr(th, "_last_store_path", lambda: str(p))
    th._set_last_loaded(True)
    th._LAST.clear()
    try:
        yield th, p
    finally:
        th._LAST.clear()


@pytest.fixture
def _coup(tmp_path, monkeypatch):
    from motor_ai_sim.routes import coupled as cp

    p = tmp_path / ".last_coupled.json"
    monkeypatch.setattr(cp, "_last_store_path", lambda: str(p))
    cp._set_last_loaded(True)
    cp._LAST.clear()
    try:
        yield cp, p
    finally:
        cp._LAST.clear()


@pytest.mark.parametrize("kind", ["modes", "rotor_stress"])
def test_mechanical_pickle_is_written_while_another_thread_writes(
        _mech, caplog, kind):
    mech, p = _mech
    caplog.set_level(logging.WARNING)

    # (a) a quiet round proves the file is written and READABLE, by content.
    result = _big_result(kind)
    _persist_and_wait(lambda: mech._remember_last(kind, result,
                                                  {"token": "quiet"}, "fp-0"))
    blob = pickle.loads(p.read_bytes())
    assert isinstance(blob, dict)
    assert blob[kind]["params"]["token"] == "quiet"
    assert blob[kind]["result"]["geo_fingerprint"] == "fp-" + kind

    # (b) now both races at once, for every round.
    with _Churn() as ch:
        ch.spawn(_entry_churn(result))
        ch.spawn(_store_flood(mech._LAST, mech._LAST_MAX))
        for i in range(_ROUNDS):
            _persist_and_wait(lambda i=i: mech._remember_last(
                kind, result, {"token": i}, "fp-%d" % i))
        assert not ch.error, ch.error

    blob = pickle.loads(p.read_bytes())
    assert isinstance(blob, dict) and blob, "the pickle came back empty"
    assert not _complaints(caplog, "could not persist the last mechanical",
                           "could not remember the last mechanical",
                           "changed size during iteration")


def test_thermal_pickle_is_written_while_another_thread_writes(_therm, caplog):
    th, p = _therm
    caplog.set_level(logging.WARNING)

    result = _big_result("field")
    _persist_and_wait(lambda: th._remember_last("field", result,
                                                {"token": "quiet"}, "fp-0"))
    blob = pickle.loads(p.read_bytes())
    assert isinstance(blob, dict)
    assert blob["field"]["params"]["token"] == "quiet"

    with _Churn() as ch:
        ch.spawn(_entry_churn(result))
        ch.spawn(_store_flood(th._LAST, th._LAST_MAX))
        for i in range(_ROUNDS):
            _persist_and_wait(lambda i=i: th._remember_last(
                "field", result, {"token": i}, "fp-%d" % i))
        assert not ch.error, ch.error

    blob = pickle.loads(p.read_bytes())
    assert isinstance(blob, dict) and blob, "the pickle came back empty"
    assert not _complaints(caplog, "could not persist the last thermal",
                           "could not remember the last thermal",
                           "changed size during iteration")


def test_coupled_json_is_written_while_another_thread_writes(_coup, caplog):
    """Same guarantee, JSON and in-thread: the store is snapshotted, not walked.

    The coupled store is flat — the loop's own keys — and its writer runs in the
    request thread, so what races here is the STORE half: another run (or this
    store's cap-eviction) writing ``_LAST`` while this one serialises it.
    """
    cp, p = _coup
    caplog.set_level(logging.WARNING)

    out = {"geometry_fingerprint": "fp-0", "token": "quiet",
           "iterations": [{"t": i} for i in range(200)]}
    cp._remember_last(dict(out))
    d = json.loads(p.read_text(encoding="utf-8"))
    assert isinstance(d, dict)
    assert d["token"] == "quiet"

    with _Churn() as ch:
        ch.spawn(_store_flood(cp._LAST, cp._LAST_MAX))
        for i in range(_ROUNDS * 4):
            cp._remember_last(dict(out, token=i, geometry_fingerprint="fp-%d" % i))
        assert not ch.error, ch.error

    d = json.loads(p.read_text(encoding="utf-8"))
    assert isinstance(d, dict) and d, "the store came back empty"
    assert not _complaints(caplog, "could not persist the last coupled",
                           "changed size during iteration")


def test_persist_keeps_a_private_copy_of_the_answer(_mech):
    """The store must not alias the dict the route goes on editing.

    Half 1 of the incident, stated as a property rather than as a race: after
    ``_remember_last`` the caller owns its dict again and nothing it does to it
    can reach ``_LAST`` — which is also what the modal route's own comment says
    should happen ("what is remembered is the answer, not the fact that this
    request did not have to solve it").
    """
    mech, p = _mech
    result = {"geo_fingerprint": "fp", "field": {"vm": [1.0]}}
    _persist_and_wait(lambda: mech._remember_last("modes", result, {}, "fp"))

    result["cached"] = True
    result.pop("field", None)

    kept = mech._LAST["modes"]["result"]
    assert "cached" not in kept
    assert "field" in kept, "the stored answer lost its mode shapes"
    blob = pickle.loads(p.read_bytes())
    assert "cached" not in blob["modes"]["result"]
