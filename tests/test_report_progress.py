"""The report's progress ring — ``GET /api/family/report/progress?run_id=…``.

User, 2026-09-16: *"нужно сделать ещё минимальный прогресс-ринг генерации
отчёта, чтобы было видно, что работает, а не висит"*.  A Word report is ~50 s
and its PDF half a minute more, and for all of that the catalogue row showed
one static caption.

What is pinned here is the CONTRACT the ring is drawn from:

  (a) the STAGES happen in order — records, figures, tables, the file — and the
      fraction never goes backwards, whatever the figure count turns out to be;
  (b) the run id ROUND TRIPS: the id the client polls with is the id the build
      publishes under, and the finished file names it back in ``X-Run-Id``;
  (c) a build that RAISES leaves the sentence in the entry (a ring that just
      stops is the bug this replaces) and leaves nothing running;
  (d) an id nobody has started is the idle answer, not a 404 — the first poll
      routinely beats the build's first line.

Nothing here builds a real 43-page document: the figures are mocked, because
what is under test is the counter and not matplotlib.  The ONE real figure is
in (e), which is the wiring that matters most — every picture in the report
becomes bytes in exactly one function, and that function is what ticks.
"""
from __future__ import annotations

import pytest

from motor_ai_sim import progress as PROG
from motor_ai_sim import report as R
from motor_ai_sim import report_progress as RP


@pytest.fixture(autouse=True)
def _fresh_registry():
    """A registry of this module's own — the process one is a live server's."""
    kept = PROG.registry()
    PROG.reset_registry()
    yield
    PROG.reset_registry(kept)


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    return TestClient(app)


def _fake_figure():
    """The cheapest thing matplotlib will save: one empty axes."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, _ax = plt.subplots(figsize=(1.0, 1.0), dpi=40)
    return fig


# ---------------------------------------------------------------------------
# (a) the stages, in order, with a fraction that only ever grows
# ---------------------------------------------------------------------------


def test_a_stage_sequence_and_monotone_fractions():
    rid = "test-run-a"
    seen = []

    def look():
        s = RP.snapshot(rid)
        seen.append((s["stage"], s["phase"], s["frac"], s["running"]))
        return s

    with RP.build(rid, "docx", key="DIE/CFG/docx", budget=4) as rp:
        assert rp is not None
        look()
        RP.stage("records")
        look()
        for _ in range(4):
            RP.figure_done()
            look()
        RP.stage("tables")
        look()
        RP.stage("writing")
        look()
    end = look()

    stages = [s for s, _p, _f, _r in seen]
    assert stages[0] == "records"
    assert stages[1] == "figures"
    assert "tables" in stages and "docx" in stages
    assert end["stage"] == "done" and end["done"] is True
    assert end["error"] is None

    fracs = [f for _s, _p, f, _r in seen]
    assert fracs == sorted(fracs), f"the ring went backwards: {fracs}"
    assert fracs[-1] == pytest.approx(1.0), "a finished build must read 100 %"
    # …and the run is NOT left running: a ring that spins after the file has
    # arrived is the same "is it hung?" the whole feature answers.
    assert end["running"] is False

    # The figure phase NAMES the count — that one short line is the tooltip.
    figs = [p for s, p, _f, _r in seen if s == "figures"]
    assert figs[-1] == "figure 4 / 4", figs


def test_a2_more_figures_than_budgeted_grow_the_bar():
    """A machine with more pictures than the last one must not sit at 100 %."""
    with RP.build("test-run-a2", "docx", key="", budget=2) as rp:
        RP.stage("records")
        for _ in range(5):
            RP.figure_done()
            assert RP.snapshot("test-run-a2")["frac"] < 1.0
        assert rp.figures == 5
        RP.stage("tables")
    s = RP.snapshot("test-run-a2")
    assert s["frac"] == pytest.approx(1.0) and s["done"] is True


def test_a3_fewer_figures_than_budgeted_still_reach_the_end():
    """…and one with fewer must not stop two thirds of the way."""
    with RP.build("test-run-a3", "pdf", key="", budget=20):
        RP.stage("records")
        for _ in range(3):
            RP.figure_done()
        RP.stage("tables")
        mid = RP.snapshot("test-run-a3")
    assert mid["stage"] == "tables"
    assert mid["frac"] > 0.6, (
        "with the unused budget handed back, 'tables' is nearly the end")
    assert RP.snapshot("test-run-a3")["frac"] == pytest.approx(1.0)


def test_a4_the_next_build_of_the_same_machine_is_budgeted_from_this_one():
    key = "REMEMBER/CFG/docx"
    with RP.build("test-run-a4", "docx", key=key, budget=20):
        RP.stage("records")
        for _ in range(7):
            RP.figure_done()
        RP.stage("tables")
    assert RP.remembered_figures(key) == 7
    with RP.build("test-run-a4b", "docx", key=key) as rp:
        assert rp.total == RP.TICKS_RECORDS + 7 + RP.TICKS_TABLES \
            + RP.TICKS_WRITE["docx"]


# ---------------------------------------------------------------------------
# (b) the run id round trip, through the real route
# ---------------------------------------------------------------------------


def test_b_run_id_round_trip_through_the_route(client, monkeypatch):
    """The id the client polls with is the id the build publishes under."""
    from motor_ai_sim.routes import family as fam
    from motor_ai_sim.routes import report as rroute

    rid = "ring-1234"
    mid = {}

    def fake_docx(**kw):
        # Inside the build: what a poll ARRIVING NOW would answer.  The route
        # function itself, so this is the endpoint's own code path and not a
        # second reading of the registry.
        RP.stage("records")
        for _ in range(3):
            RP.figure_done()
        mid.update(rroute.report_progress(run_id=rid))
        RP.stage("tables")
        RP.stage("writing")
        return b"PK\x03\x04 not really a docx"

    monkeypatch.setattr("motor_ai_sim.report_docx.build_motor_report_docx",
                        fake_docx)
    monkeypatch.setattr(fam, "_require_die_access", lambda *a, **k: None)
    monkeypatch.setattr(fam, "_load_yaml",
                        lambda *a, **k: {"name": "X", "duties": []})

    r = client.get("/api/family/report/DIE/CFG", params={"run_id": rid})
    assert r.status_code == 200, r.text[:400]
    assert r.headers.get("X-Run-Id") == rid

    # Mid-build the ring was moving, and it knew which stage it was in.
    assert mid.get("run_id") == rid
    assert mid.get("running") is True
    assert mid.get("stage") == "figures"
    assert 0.0 < float(mid.get("frac") or 0) < 1.0
    assert mid.get("phase", "").startswith("figure ")

    # …and the poll ROUTE answers the finished state afterwards.
    p = client.get("/api/family/report/progress", params={"run_id": rid}).json()
    assert p["done"] is True and p["running"] is False
    assert p["stage"] == "done" and p["error"] is None
    assert p["format"] == "docx"


def test_b2_a_client_that_sends_no_id_still_gets_one(client, monkeypatch):
    from motor_ai_sim.routes import family as fam

    monkeypatch.setattr("motor_ai_sim.report_docx.build_motor_report_docx",
                        lambda **kw: b"x")
    monkeypatch.setattr(fam, "_require_die_access", lambda *a, **k: None)
    monkeypatch.setattr(fam, "_load_yaml",
                        lambda *a, **k: {"name": "X", "duties": []})
    r = client.get("/api/family/report/DIE/CFG")
    assert r.status_code == 200
    minted = r.headers.get("X-Run-Id") or ""
    assert len(minted) >= 8, "a build with no id still publishes under one"
    assert client.get("/api/family/report/progress",
                      params={"run_id": minted}).json()["done"] is True


# ---------------------------------------------------------------------------
# (c) the error path carries the sentence
# ---------------------------------------------------------------------------


def test_c_a_failed_build_stores_its_message(client, monkeypatch):
    from motor_ai_sim.routes import family as fam

    def boom(**kw):
        RP.stage("records")
        RP.figure_done()
        raise RuntimeError("the thermal store is a directory")

    monkeypatch.setattr("motor_ai_sim.report_docx.build_motor_report_docx", boom)
    monkeypatch.setattr(fam, "_require_die_access", lambda *a, **k: None)
    monkeypatch.setattr(fam, "_load_yaml",
                        lambda *a, **k: {"name": "X", "duties": []})

    rid = "ring-fails"
    r = client.get("/api/family/report/DIE/CFG", params={"run_id": rid})
    assert r.status_code == 500
    p = client.get("/api/family/report/progress", params={"run_id": rid}).json()
    assert p["done"] is True and p["running"] is False
    assert p["stage"] == "failed"
    assert "the thermal store is a directory" in str(p["error"])


def test_d_an_id_nobody_started_is_the_idle_answer(client):
    p = client.get("/api/family/report/progress",
                   params={"run_id": "never-existed"}).json()
    assert p["stage"] == "unknown"
    assert p["running"] is False and p["done"] is False
    assert p["error"] is None
    # The invariant shape the solve strips already parse.
    for k in ("running", "step", "total", "elapsed_s", "eta_s", "frac",
              "phase", "run_id"):
        assert k in p, k


# ---------------------------------------------------------------------------
# (e) THE tick: one figure, one place
# ---------------------------------------------------------------------------


def test_e_every_figure_ticks_exactly_once():
    """``report._png_bytes`` is the only place a figure becomes bytes, and the
    only place that counts one — that is what keeps the twenty-first drawing
    function from being the one nobody remembers to instrument."""
    with RP.build("test-run-e", "docx", key="", budget=10) as rp:
        RP.stage("records")
        blob = R._png_bytes(_fake_figure())
        assert blob[:8] == b"\x89PNG\r\n\x1a\n"
        assert rp.figures == 1
        R._finish(_fake_figure())
        assert rp.figures == 2, "_finish goes through the same one place"


def test_e2_a_figure_outside_a_build_costs_nothing():
    """A report rendered from a script or a test has no bar — and no error."""
    assert RP.current() is None
    blob = R._png_bytes(_fake_figure())
    assert blob[:4] == b"\x89PNG"
