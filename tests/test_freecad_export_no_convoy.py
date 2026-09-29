"""The 2026-09-14 hang: a FAILING import, re-run on every request.

WHAT HAPPENED.  Windows Application Control began denying the DLL ``import
OCP`` pulls in, so ``import cadquery`` raised ImportError — and the probe
cached only SUCCESS, so every caller re-ran the whole failing import.  Under an
enforced policy that attempt costs ~0.5-3 s (the loader evaluates the policy and
logs an event), and importlib holds the module's lock for all of it, so the
callers did not run in parallel: they queued in
``importlib._bootstrap.acquire``.  The 3-D viewer (``/api/geometry/mesh``) and
the new FreeCAD export both probed per request, the queue grew faster than it
drained, all 40 anyio worker threads ended up parked in that lock, and every
SYNC endpoint — which is nearly all of them, ``/api/me`` included — hung behind
it.  The live API served one request at 10:44 and nothing at all until the
11:19 restart: 54 threads, 25 MB, 51 s of CPU, everyone blocked on a lock.

The same race also made the probe LIE.  While one thread runs
``cadquery/__init__.py`` a partially initialised ``cadquery`` sits in
``sys.modules``, so another thread's ``import cadquery`` takes importlib's
already-in-sys.modules path and gets that half-built module with no exception.
``ocp_available()`` answered "yes", the export skipped its own fallback, and
``build_solids`` died — 500s on a download that has a perfectly good fallback.

WHAT IS PINNED HERE: the probe is attempted at most once per process; a failure
is remembered as firmly as a success; a half-built module never passes as a
working kernel; and two consecutive exports plus a context read complete in
seconds, with only ONE build behind however many simultaneous downloads.

Nothing here writes state — the export reads the live config and the writers
are pure Python.
"""
from __future__ import annotations

import threading
import time

import pytest

from motor_ai_sim import cadquery_geometry as CG
from motor_ai_sim import freecad_io as F
from motor_ai_sim.routes import freecad as R


@pytest.fixture(autouse=True)
def _clean_slate():
    """Each test starts with an empty bundle memo; the probe's own answer is
    restored afterwards so the suite's later CAD tests see this machine's
    truth, not a fixture's."""
    R.reset_bundle_cache()
    saved = CG._CQ_PROBE
    yield
    R.reset_bundle_cache()
    CG._CQ_PROBE = saved


# ── the probe: one attempt per process, failure cached too ───────────────────

def test_a_failing_probe_is_attempted_exactly_once(monkeypatch):
    """THE regression.  Ten callers, one import attempt — the convoy that hung
    the API on 2026-09-14 needed the 2nd, 3rd, ... attempt to exist."""
    CG.reset_cadquery_probe()
    attempts = []

    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) \
        else __builtins__.__import__

    def _fake_import(name, *a, **k):
        if name == "cadquery" or name.startswith("cadquery."):
            attempts.append(name)
            raise ImportError("DLL load failed while importing OCP: An "
                              "Application Control policy has blocked this file.")
        return real_import(name, *a, **k)

    monkeypatch.setattr("builtins.__import__", _fake_import)
    outs = [CG.cadquery_probe() for _ in range(10)]
    monkeypatch.undo()

    assert len(attempts) == 1, (
        "the failing import ran %d times — that is the convoy: every extra "
        "attempt parks a worker thread in importlib's module lock" % len(attempts))
    assert all(o[0] is False for o in outs)
    assert "Application Control" in outs[0][1]
    assert CG._import_cadquery() is False
    assert CG.HAS_CADQUERY is False


def test_the_probe_refuses_a_half_built_module(monkeypatch):
    """importlib's already-in-sys.modules path hands a concurrent importer a
    module that is still executing its ``__init__``.  Such a module has no
    ``Workplane`` yet, and promising solids from it is how the export 500'd."""
    CG.reset_cadquery_probe()

    class _Partial:            # what a mid-import `cadquery` looks like
        pass

    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) \
        else __builtins__.__import__

    def _fake_import(name, *a, **k):
        if name == "cadquery":
            return _Partial()
        if name.startswith("cadquery"):
            raise ImportError("half built")
        return real_import(name, *a, **k)

    monkeypatch.setattr("builtins.__import__", _fake_import)
    ok, why = CG.cadquery_probe()
    monkeypatch.undo()
    assert ok is False
    assert "Workplane" in why or "partial" in why


def test_ocp_available_is_the_same_memoised_probe():
    """One answer for the whole process — freecad_io must not keep its own."""
    assert F.ocp_available() == CG.cadquery_probe()


# ── the endpoint: two exports + a context read, in seconds ───────────────────

BLOCKED = ("DLL load failed while importing OCP: An Application Control "
           "policy has blocked this file.")


def test_two_exports_and_a_context_read_finish_in_seconds(monkeypatch):
    monkeypatch.setattr(F, "ocp_available", lambda: (False, BLOCKED))
    t0 = time.perf_counter()
    first = R.export_freecad()
    second = R.export_freecad()
    from motor_ai_sim.routes.family import _read_ctx
    _read_ctx()
    dt = time.perf_counter() - t0
    assert first.headers["X-Bundle-Kind"] == "macro-dxf"
    assert bytes(second.body) == bytes(first.body)
    assert dt < 20.0, "two exports + a context read took %.1f s" % dt


def test_simultaneous_downloads_share_one_build(monkeypatch):
    """Eight clients, ONE build.  Without single-flight each took a worker
    thread of the shared 40-token anyio pool for the whole multi-second build,
    so a double-click or a browser retry could starve the whole API."""
    builds = []
    real = F.build_macro_bundle

    def _counting(*a, **k):
        builds.append(1)
        return real(*a, **k)

    monkeypatch.setattr(F, "ocp_available", lambda: (False, BLOCKED))
    monkeypatch.setattr(F, "build_macro_bundle", _counting)

    out, errs = [], []

    def _go():
        try:
            out.append(bytes(R.export_freecad().body))
        except Exception as e:      # noqa: BLE001
            errs.append(e)

    ts = [threading.Thread(target=_go) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=60)
    assert not errs, errs
    assert len(out) == 8
    assert len(set(out)) == 1, "the eight downloads disagreed about the machine"
    assert len(builds) == 1, "%d builds for 8 simultaneous downloads" % len(builds)


def test_a_positive_probe_that_dies_on_import_still_ships_a_bundle(monkeypatch):
    """Belt and braces for the race that produced the 500s: if the kernel says
    yes and then raises ImportError deeper in, the user gets the macro bundle,
    not a failed download."""
    monkeypatch.setattr(F, "ocp_available", lambda: (True, ""))

    def _boom(*a, **k):
        raise ImportError("DLL load failed while importing OCP.OCP")

    monkeypatch.setattr(F, "build_solids", _boom)
    r = R.export_freecad()
    assert r.headers["X-Bundle-Kind"] == "macro-dxf"
