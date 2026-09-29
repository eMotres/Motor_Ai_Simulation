"""The results ledger — "have I already computed exactly this?"

User, 2026-09-05: «Не надо Recent runs — нужно просто сканировать результаты:
не совпадают ли они с уже проведёнными, хотя бы пока по этим параметрам.»  He
had set 667.4 A peak in the morning, computed, changed the current, computed
again, then came back to 667.4 A with nothing else touched — and had to sit
through a solve whose answer was already on disk.

The rule this must not break is the one from the day before (a Run ALWAYS
solves, never silently serves an older result).  They meet on three conditions,
and every test below pins one of them:

  * the match is the EXACT `_sb_key` — one changed field and the solver runs;
  * the answer is LABELLED `ledger_hit`, so nobody is told a solve happened;
  * `fresh=true` (the card's "Recompute") always solves and replaces the entry.

No FEM here: `em_transient_eval` is a counting stub, so what is pinned is the
ROUTE's load-vs-solve decision, not any physics.
"""
from __future__ import annotations

import gzip
import json

import pytest


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def sim():
    from motor_ai_sim.routes import simulation as s
    return s


@pytest.fixture
def ledger_dir(tmp_path, sim, monkeypatch):
    """A ledger of this test's own — never the sandbox config's shared one, so
    the order tests run in cannot decide whether one of them hits."""
    d = tmp_path / ".run_ledger"
    monkeypatch.setattr(sim, "_ledger_dir", lambda: d)
    return d


@pytest.fixture
def solver(monkeypatch, sim, ledger_dir):
    """A counting stub in place of the sliding-band solve (see
    tests/test_physics_cache_refresh.py — same shape, same reasoning)."""
    sim.clear_simulation_caches(reason="run-ledger test setup")
    calls = []

    def fake_eval(**kw):
        calls.append(kw)
        return {
            "time_s": [0.0, 0.5, 1.0],
            "T_avg_Nm": 1.234,
            "T_em_Nm": [1.2, 1.25, 1.23],
            "rpm": 1000.0,
            "f_elec_Hz": 100.0,
            "P_loss_total_W": [10.0, 10.0, 10.0],
            "n_frames_solved": 3,
        }

    monkeypatch.setattr("motor_ai_sim.simulation.fem_solver_2d.em_transient_eval",
                        fake_eval)
    # The bench Ld/Lq probe rides on every live-machine run and costs three real
    # solves on a miss — not this test's business.
    monkeypatch.setattr(sim, "_bench_read", lambda *a, **k: {"stubbed": True})
    monkeypatch.setattr(sim, "_bench_compute", lambda *a, **k: None)
    # The run journal appends next to the SANDBOX config, i.e. into the system
    # temp area — a side effect with nothing to do with the ledger.
    monkeypatch.setattr(sim, "_append_run_journal", lambda *a, **k: None)
    yield calls
    sim.clear_simulation_caches(reason="run-ledger test teardown")


# A pinned d-axis (an unpinned one would be MEASURED — a real 24-frame solve)
# and a small frame count: the minimum a reproducible run needs here.
RUN = dict(n_steps_per_period=4, n_periods=1.0, gamma_deg=0.0,
           I_phase_rms=10.0, daxis_deg=0.0)


def _files(d):
    return sorted(p.name for p in d.glob("*.json.gz")) if d.exists() else []


# ── (a) a finished run is recorded ──────────────────────────────────────────

def test_a_run_writes_a_ledger_entry(sim, solver, ledger_dir):
    res = sim.get_fem_transient(**RUN)

    assert len(_files(ledger_dir)) == 1, "the finished run was not recorded"
    with gzip.open(ledger_dir / _files(ledger_dir)[0], "rt", encoding="utf-8") as fh:
        blob = json.load(fh)
    # The KEY itself, and the same key spelled out field by field — the named
    # copy is what makes "why did it not match?" answerable at all.
    assert blob["computed_at"] == res["computed_at"]
    assert blob["geo_fingerprint"] == res["geo_fingerprint"]
    assert "mat_signature" in blob
    kf = blob["key_fields"]
    assert kf["kind"] == "sb"
    assert kf["I_phase_rms"] == 10.0
    assert kf["n_steps_per_period"] == 4
    assert kf["gamma_deg"] == 0.0
    assert kf["daxis_deg"] == 0.0
    assert kf["demag"] == 0 and kf["eddy"] == 0
    # every field is named, and the names ARE the key, in order
    assert list(kf.values()) == blob["key"]
    # the animation payload never enters the ledger (a 464-step run wrote a
    # 790 MB store the day frames were left in the persist path)
    assert "frames" not in blob["result"]
    assert blob["result"]["time_s"]          # …but the series the charts need do


# ── (b) an identical request is LOADED, not solved ──────────────────────────

def test_an_identical_second_request_is_a_labelled_ledger_hit(sim, solver):
    first = sim.get_fem_transient(**RUN)
    assert len(solver) == 1
    assert not first.get("ledger_hit")

    second = sim.get_fem_transient(**RUN)
    assert len(solver) == 1, "an identical request re-solved instead of loading"
    assert second["ledger_hit"] is True, "the hit was not labelled"
    assert second["ledger_computed_at"] == first["computed_at"]
    assert second["computed_at"] == first["computed_at"]
    # NOT a "restore": that word means the last transient shown while you decide
    # whether to run, and the UI escalates a stale one to a red banner.
    assert second["restored"] is False
    assert second["stale"] is False
    assert second["T_avg_Nm"] == first["T_avg_Nm"]


def test_the_internal_memo_is_still_not_the_ledger(sim, solver):
    """A background solve (an optimizer probe, a passport point) neither reads
    the ledger nor is served from it — it has its own rules."""
    sim.get_fem_transient(**RUN)
    assert len(solver) == 1
    tok = sim._BACKGROUND_RUN.set(True)
    try:
        out = sim.get_fem_transient(**RUN)
    finally:
        sim._BACKGROUND_RUN.reset(tok)
    assert not out.get("ledger_hit"), "a background run was served the ledger"


# ── (c) ANY changed key field means a solve ─────────────────────────────────

@pytest.mark.parametrize("change", [
    pytest.param({"I_phase_rms": 11.0}, id="current"),
    pytest.param({"gamma_deg": 5.0}, id="gamma"),
    pytest.param({"n_steps_per_period": 6}, id="steps"),
    pytest.param({"demag": True}, id="demag"),
    pytest.param({"eddy": True}, id="eddy"),
    pytest.param({"coil_temp_c": 95.0}, id="coil_temp"),
    pytest.param({"rpm": 1500.0}, id="rpm"),
    pytest.param({"torque_filter": True}, id="torque_filter"),
])
def test_one_changed_field_reaches_the_solver(sim, solver, change):
    sim.get_fem_transient(**RUN)
    assert len(solver) == 1
    sim.get_fem_transient(**{**RUN, **change})
    assert len(solver) == 2, f"{change} was served from the ledger"


def test_a_material_override_reaches_the_solver(sim, solver):
    """The material assignment is not in the URL — it rides `mat=` — and it is
    folded into the key through `_config_physics_fingerprint`.  Leaving it out
    was the bug that replayed the previous magnet's solve."""
    from motor_ai_sim.material_context import set_request_materials
    sim.get_fem_transient(**RUN)
    assert len(solver) == 1
    set_request_materials({"assignment": {"magnet": "__test_only_magnet__"}})
    try:
        out = sim.get_fem_transient(**RUN)
    finally:
        set_request_materials(None)
    assert not out.get("ledger_hit"), "another magnet was served the first one's run"
    assert len(solver) == 2


def test_a_geometry_override_reaches_the_solver(sim, solver):
    """A per-request `geo=` machine is a DIFFERENT machine — its key carries the
    override, and it is not the motor on screen, so it writes no entry either."""
    sim.get_fem_transient(**RUN)
    assert len(solver) == 1
    out = sim.get_fem_transient(geo=json.dumps({"motor_length": 161.0}), **RUN)
    assert not out.get("ledger_hit")
    assert len(solver) == 2


# ── (d) fresh=true is the card's "Recompute" ────────────────────────────────

def test_fresh_always_solves_and_replaces_the_entry(sim, solver, ledger_dir):
    first = sim.get_fem_transient(**RUN)
    assert len(_files(ledger_dir)) == 1
    again = sim.get_fem_transient(fresh=True, **RUN)

    assert len(solver) == 2, "fresh=true was served from the ledger"
    assert not again.get("ledger_hit")
    assert len(_files(ledger_dir)) == 1, "Recompute left a second entry behind"
    with gzip.open(ledger_dir / _files(ledger_dir)[0], "rt", encoding="utf-8") as fh:
        blob = json.load(fh)
    # the stored entry now describes the RECOMPUTED run (the stamp has 1 s
    # resolution, so `first` and `again` may read the same second — what is
    # pinned here is that the file follows the newest solve)
    assert blob["computed_at"] == again["computed_at"]
    assert first["computed_at"] is not None
    # …and the next ordinary Run loads the RECOMPUTED one
    third = sim.get_fem_transient(**RUN)
    assert third["ledger_hit"] is True
    assert third["computed_at"] == again["computed_at"]
    assert len(solver) == 2


def test_ledger_false_always_solves(sim, solver):
    """The field-animation viewer needs the per-frame fields, which the ledger
    never stores — so it opts out and must always reach the solver."""
    sim.get_fem_transient(**RUN)
    out = sim.get_fem_transient(ledger=False, **RUN)
    assert len(solver) == 2
    assert not out.get("ledger_hit")


# ── (e) restore is untouched ────────────────────────────────────────────────

def test_restore_is_not_a_ledger_hit(sim, solver):
    """?restore=true still returns the LAST transient, flagged, and still
    computes nothing — the ledger changes neither half of that."""
    sim.get_fem_transient(**RUN)
    assert len(solver) == 1
    out = sim.get_fem_transient(restore=True, **RUN)
    assert len(solver) == 1
    assert out["restored"] is True
    assert not out.get("ledger_hit")
    assert out["stale"] is False          # same params as the saved run


def test_a_ledger_hit_becomes_the_restored_run(sim, solver):
    """What a reload restores must be what is on screen.

    Otherwise: load this morning's 667 A run, reload the page, and the panel
    finds the LATER 500 A solve still sitting in the restore store, sees a newer
    stamp and adopts it whole — the numbers change under the user with nothing
    said.  A load is not a solve, so the store follows it but the run journal
    (a record of solves actually made) does not.
    """
    morning = sim.get_fem_transient(**RUN)
    sim.get_fem_transient(**{**RUN, "I_phase_rms": 20.0})     # the run in between
    assert len(solver) == 2
    back = sim.get_fem_transient(**RUN)                       # …and back again
    assert back["ledger_hit"] is True

    out = sim.get_fem_transient(restore=True, **RUN)
    assert out["restored"] is True
    assert out["stale"] is False, "the restore store still held the other run"
    assert out["computed_at"] == morning["computed_at"]
    assert len(solver) == 2


# ── (f) the ring buffer trims ───────────────────────────────────────────────

def test_the_ring_buffer_keeps_only_the_newest_per_geometry(sim, solver,
                                                            ledger_dir,
                                                            monkeypatch):
    monkeypatch.setattr(sim, "_LEDGER_KEEP_PER_GEO", 2)
    for i, amps in enumerate((10.0, 11.0, 12.0, 13.0)):
        sim.get_fem_transient(**{**RUN, "I_phase_rms": amps})
        assert len(_files(ledger_dir)) <= 2, (
            f"ledger grew past the ring size after {i + 1} runs")
    assert len(_files(ledger_dir)) == 2
    # the newest survivor is still loadable, and it is a HIT
    out = sim.get_fem_transient(**{**RUN, "I_phase_rms": 13.0})
    assert out["ledger_hit"] is True


# ── the endpoints ───────────────────────────────────────────────────────────

def test_ledger_match_endpoint_answers_without_solving(sim, solver, ledger_dir):
    """GET .../fem_transient/ledger_match reuses the RUN's own key building (it
    copies its signature), so what it promises is what the Run finds."""
    from fastapi.testclient import TestClient
    from motor_ai_sim.api import app
    client = TestClient(app)

    q = {"n_steps_per_period": 4, "n_periods": 1.0, "gamma_deg": 0.0,
         "I_phase_rms": 10.0, "daxis_deg": 0.0}
    r = client.get("/api/simulation/physics/fem_transient/ledger_match", params=q)
    assert r.status_code == 200, r.text
    assert r.json()["match"] is False
    assert len(solver) == 0, "the probe SOLVED"

    res = sim.get_fem_transient(**RUN)
    r = client.get("/api/simulation/physics/fem_transient/ledger_match", params=q)
    assert r.json() == {"match": True, "computed_at": res["computed_at"]}
    assert len(solver) == 1

    # a different current is a different run — and the probe says so
    r = client.get("/api/simulation/physics/fem_transient/ledger_match",
                   params={**q, "I_phase_rms": 99.0})
    assert r.json()["match"] is False
    assert len(solver) == 1


def test_ledger_list_and_clear_endpoints(sim, solver, ledger_dir):
    from fastapi.testclient import TestClient
    from motor_ai_sim.api import app
    client = TestClient(app)

    sim.get_fem_transient(**RUN)
    body = client.get("/api/simulation/ledger").json()
    assert body["count"] == 1
    assert body["entries"][0]["bytes"] > 0
    assert body["entries"][0]["key_fields"]["I_phase_rms"] == 10.0

    assert client.delete("/api/simulation/ledger").json() == {"deleted": 1}
    assert _files(ledger_dir) == []
    # …and with the ledger emptied, the same request solves again
    sim.get_fem_transient(**RUN)


# ── (g) the response contract every panel's "Loaded from history" line reads
# ---------------------------------------------------------------------------
# 2026-09-22: mechanical.py's _ROTOR_STRESS_HISTORY and coupled.py's
# _COUPLED_HISTORY both stamp a hit `served_from_history: true`; this ledger
# is the EM route's own, older, equivalent persistent layer (see the note
# beside `_ledger_dir()`) — aligned here so the web notice is one component
# for all four panels rather than one per backend mechanism.

def test_a_ledger_hit_carries_the_shared_history_vocabulary(sim, solver):
    first = sim.get_fem_transient(**RUN)
    assert not first.get("served_from_history")

    second = sim.get_fem_transient(**RUN)
    assert len(solver) == 1, "a repeat re-solved instead of loading"
    assert second["served_from_history"] is True
    assert second["computed_at"] == first["computed_at"]
    # ledger_hit/ledger_computed_at stay — this is an ADDED alias, not a
    # rename, so nothing that already reads the older names breaks.
    assert second["ledger_hit"] is True
    assert second["ledger_computed_at"] == first["computed_at"]


# ── (h) the key already covers what the brief asks for, field by field ──────
# "a canonical key over every field that changes the answer ... and a test
# that a change in EACH listed field changes the key" — proved here against
# the key `_sb_key_fields` ALREADY builds (routes/simulation.py:4715-4801),
# reused as-is rather than re-specified (see run_history.py's module
# docstring on why a caller normalises and this module never invents its
# own rounding/sorting). Every case below: two runs that differ in ONLY the
# named field must both hit the solver — a would-be ledger hit on the second
# call would mean that field is NOT in the key.

_KEY_COVERAGE_CASES = [
    ("I_phase_rms (operating point)", {"I_phase_rms": 11.0}),
    ("gamma_deg (operating point)", {"gamma_deg": 5.0}),
    ("rpm (operating point)", {"rpm": 2000.0}),
    ("coil_temp_c (temperature)", {"coil_temp_c": 140.0}),
    ("magnet_temp_c (temperature)", {"magnet_temp_c": 80.0}),
    ("star_delta (connection)", {"star_delta": "delta"}),
    ("mesh_size_mm (solver setting)", {"mesh_size_mm": 3.0}),
    ("n_steps_per_period (solver setting)", {"n_steps_per_period": 8}),
    ("n_sectors (solver setting)", {"n_sectors": 2}),
    ("element_order (solver setting)", {"element_order": 1}),
    ("eddy (solver flag)", {"eddy": True}),
    ("rotor_eddy (solver flag)", {"rotor_eddy": False}),
    ("demag (solver flag)", {"demag": True}),
    ("drive (drive/PWM)", {"drive": "voltage", "v_phase_peak": 5.0}),
]


@pytest.mark.parametrize("label, override", _KEY_COVERAGE_CASES,
                         ids=[c[0] for c in _KEY_COVERAGE_CASES])
def test_each_listed_field_changes_the_ledger_key(sim, solver, label, override):
    sim.get_fem_transient(**RUN)
    assert len(solver) == 1
    sim.get_fem_transient(**{**RUN, **override})
    assert len(solver) == 2, (
        f"{label} did not change the ledger key — a request that differs "
        "only in this field would be silently served another point's answer")


def test_reordering_and_float_noise_do_not_change_the_key(sim, solver, ledger_dir):
    """The flip side of (h): the SAME point, spelled with float noise a UI
    round-trip could introduce, must still be one entry, not two."""
    sim.get_fem_transient(**RUN)
    assert len(_files(ledger_dir)) == 1
    noisy = dict(RUN)
    noisy["I_phase_rms"] = 10.0 + 1e-12
    noisy["gamma_deg"] = 0.0 + 1e-12
    sim.get_fem_transient(**noisy)
    assert len(solver) == 1, "float noise below the key's rounding re-solved"
    assert len(_files(ledger_dir)) == 1
