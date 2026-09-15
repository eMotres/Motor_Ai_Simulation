"""WHERE THE PWM CARRIER COMES FROM, and what the stored record keeps.

Two report-audit findings of 2026-09-14, both about a record that could not be
checked afterwards:

* **A1** — the ring-mode table's "nearest excitation" column was built on
  ``get_config()["simulation"]["f_switch"]``, i.e. on whatever machine the
  server had loaded when the step ran, not on the duty being solved.  The Ø85's
  48 kHz was published twelve times in the Ø200 L155 rated duty's table, turning
  a red "a rotor mode sits ON the carrier" into a green 13 % margin.  The
  carrier is now passed down from the run and stored beside the answer.
* **A4** — ``run_critical_speeds_at`` stored the crossings but not the Campbell
  sweep, so the report's diagram drew empty axes under a caption promising
  forward and backward whirl curves.

Nothing here solves: the two mechanical hooks and the modal solver are faked,
because what is under test is which number reaches them and which number comes
back out in the record.
"""
from __future__ import annotations

import pytest

from motor_ai_sim import duty_results as dr
from motor_ai_sim.routes import coupled
from motor_ai_sim.routes import mechanical as mech

GLOBAL_HZ = 48000.0          # the Ø85 on the server
DUTY_HZ = 24000.0            # the duty actually being solved


@pytest.fixture
def global_carrier(monkeypatch):
    """The process-global ``simulation`` block, holding ANOTHER machine."""
    import motor_ai_sim.config as cfgmod
    monkeypatch.setattr(cfgmod, "get_config",
                        lambda *a, **k: {"simulation": {"f_switch": GLOBAL_HZ,
                                                        "rpm": 3000.0}})


# ---------------------------------------------------------------------------
# _f_switch: explicit wins, and 0 is an answer
# ---------------------------------------------------------------------------

def test_the_explicit_carrier_wins_over_the_global(global_carrier):
    assert mech._f_switch(DUTY_HZ) == pytest.approx(DUTY_HZ)


def test_zero_means_no_carrier_not_go_and_look_at_the_global(global_carrier):
    # "this duty has no PWM line" must NOT fall through to another machine's.
    assert mech._f_switch(0.0) is None
    assert mech._f_switch(0) is None


def test_nothing_passed_still_reads_the_global_for_the_interactive_tab(
        global_carrier):
    assert mech._f_switch() == pytest.approx(GLOBAL_HZ)
    assert mech._f_switch(None) == pytest.approx(GLOBAL_HZ)


def test_an_unparseable_carrier_is_no_carrier(global_carrier):
    assert mech._f_switch("nonsense") is None


# ---------------------------------------------------------------------------
# the /modes route: the passed carrier reaches the solver and the record
# ---------------------------------------------------------------------------

class _Motor:
    parameters = {"num_poles": 10, "num_slots": 12}


@pytest.fixture
def faked_modal(monkeypatch):
    """Everything the ``/modes`` route touches except the carrier logic."""
    from motor_ai_sim.simulation.mechanical import modal as mdm

    seen: dict = {}
    monkeypatch.setattr(mech, "_live_polys", lambda geo: ({}, _Motor(), None))
    monkeypatch.setattr(mech, "_assignments", lambda: {})
    monkeypatch.setattr(mech, "_override_props", lambda: {})
    monkeypatch.setattr(mech, "_live_fingerprint", lambda ov: "fp-l155")
    monkeypatch.setattr(mech, "_remember_last",
                        lambda kind, result, params, fp: seen.update(
                            stored=result, params=params))
    monkeypatch.setattr(mdm, "cache_get", lambda key: None)
    monkeypatch.setattr(mdm, "cache_put", lambda key, out: seen.update(key=key))

    def _solve(polys, params, assign, **kw):
        seen["kw"] = dict(kw)
        return {"body": kw.get("body"), "modes": [{"index": 1, "f_hz": 24028.0}],
                "rpm": kw.get("rpm")}
    monkeypatch.setattr(mdm, "solve_modes", _solve)
    return seen


def test_modes_route_builds_the_table_on_the_passed_carrier(
        global_carrier, faked_modal):
    out = mech.modes(body="rotor", n=2, support="free", mesh_size_mm=2.5,
                     order=1, winding_mass=False, shapes=False, rpm=23000.0,
                     f_switch_hz=DUTY_HZ, geo=None)
    assert faked_modal["kw"]["f_switch"] == pytest.approx(DUTY_HZ)
    assert faked_modal["kw"]["f_switch"] != GLOBAL_HZ
    # …and the answer says which carrier it was built on.
    assert out["f_switch_hz"] == pytest.approx(DUTY_HZ)
    assert faked_modal["stored"]["f_switch_hz"] == pytest.approx(DUTY_HZ)


def test_modes_route_without_a_carrier_still_serves_the_tab(
        global_carrier, faked_modal):
    out = mech.modes(body="rotor", n=2, support="free", mesh_size_mm=2.5,
                     order=1, winding_mass=False, shapes=False, rpm=23000.0,
                     f_switch_hz=None, geo=None)
    assert faked_modal["kw"]["f_switch"] == pytest.approx(GLOBAL_HZ)
    assert out["f_switch_hz"] == pytest.approx(GLOBAL_HZ)


def test_two_carriers_are_two_cache_entries(global_carrier, faked_modal):
    keys = []
    for hz in (DUTY_HZ, GLOBAL_HZ):
        mech.modes(body="rotor", n=2, support="free", mesh_size_mm=2.5,
                   order=1, winding_mass=False, shapes=False, rpm=23000.0,
                   f_switch_hz=hz, geo=None)
        keys.append(faked_modal["key"])
    # Without the carrier in the key a 48 kHz table would be replayed for a
    # 24 kHz duty — the same failure in a different disguise.
    assert keys[0] != keys[1]


def test_run_modes_at_forwards_the_carrier(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(mech, "_mech_panel_settings", lambda auth: {})
    monkeypatch.setattr(mech, "modes",
                        lambda **kw: seen.update(kw) or {"ok": True})
    mech.run_modes_at(rpm=23000.0, f_switch_hz=DUTY_HZ)
    assert seen["f_switch_hz"] == pytest.approx(DUTY_HZ)


def test_run_critical_speeds_at_forwards_the_carrier(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(mech, "_mech_panel_settings", lambda auth: {})
    monkeypatch.setattr(mech, "critical_speeds",
                        lambda **kw: seen.update(kw) or {"ok": True})
    mech.run_critical_speeds_at(rpm=23000.0, f_switch_hz=DUTY_HZ)
    assert seen["f_switch_hz"] == pytest.approx(DUTY_HZ)


# ---------------------------------------------------------------------------
# the coupled orchestrator resolves the carrier WITH the run
# ---------------------------------------------------------------------------

def test_effective_f_switch_prefers_the_bodys_own(global_carrier):
    assert coupled._effective_f_switch(
        {"f_switch_hz": DUTY_HZ}) == pytest.approx(DUTY_HZ)
    assert coupled._effective_f_switch(
        {"f_switch": DUTY_HZ}) == pytest.approx(DUTY_HZ)
    # Nothing in the body: the shared configuration the ▶ route PATCHed.
    assert coupled._effective_f_switch({}) == pytest.approx(GLOBAL_HZ)
    assert coupled._effective_f_switch({"f_switch_hz": 0}) is None


def test_modal_steps_pass_the_runs_carrier_to_both_hooks(monkeypatch):
    seen: dict = {}

    def _modes(**kw):
        seen["modes"] = dict(kw)
        return {"modes": [{"f_hz": 24028.0,
                           "nearest": {"name": "PWM carrier", "hz": DUTY_HZ,
                                       "margin_pct": 0.1, "flag": True}}],
                "f_switch_hz": kw.get("f_switch_hz")}

    def _crit(**kw):
        seen["crit"] = dict(kw)
        return {"rated_rpm": 3000.0, "critical_speeds": [],
                "f_switch_hz": kw.get("f_switch_hz")}

    monkeypatch.setattr(mech, "run_modes_at", _modes)
    monkeypatch.setattr(mech, "run_critical_speeds_at", _crit)
    out = coupled._modal_steps({}, authorization=None, rpm=3000.0,
                               run_id="", f_switch_hz=DUTY_HZ)
    assert seen["modes"]["f_switch_hz"] == pytest.approx(DUTY_HZ)
    assert seen["crit"]["f_switch_hz"] == pytest.approx(DUTY_HZ)
    assert out["modes"]["f_switch_hz"] == pytest.approx(DUTY_HZ)


def test_modal_steps_say_no_carrier_rather_than_leave_it_to_the_global(
        monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(mech, "run_modes_at",
                        lambda **kw: seen.update(modes=dict(kw)) or {})
    monkeypatch.setattr(mech, "run_critical_speeds_at", lambda **kw: {})
    coupled._modal_steps({}, authorization=None, rpm=3000.0, run_id="",
                         f_switch_hz=None)
    # Explicitly 0 — the hook must not fall back to the process-global block.
    assert seen["modes"]["f_switch_hz"] == 0.0


# ---------------------------------------------------------------------------
# A4 — the Campbell sweep survives into the stored record
# ---------------------------------------------------------------------------

def _sweep(n: int = 41, n_modes: int = 3) -> dict:
    return {"rpm": [i * 500.0 for i in range(n)],
            "forward": [[100.0 * (k + 1) + i for k in range(n_modes)]
                        for i in range(n)],
            "backward": [[90.0 * (k + 1) + i for k in range(n_modes)]
                         for i in range(n)]}


def test_compact_campbell_keeps_the_solvers_shape():
    cam = dr.compact_campbell(_sweep())
    assert cam["n_points"] == 41
    assert len(cam["rpm"]) == 41
    # Indexed BY SPEED POINT: one per-mode frequency vector per rpm.
    assert len(cam["forward"]) == 41 and len(cam["forward"][0]) == 3
    assert len(cam["backward"]) == 41
    assert "decimated_from" not in cam


def test_compact_campbell_decimates_a_long_sweep():
    cam = dr.compact_campbell(_sweep(n=1000), cap=200)
    assert cam["n_points"] <= 200
    assert cam["decimated_from"] == 1000
    assert len(cam["forward"]) == cam["n_points"]
    assert cam["rpm"][-1] == pytest.approx(999 * 500.0)   # the last point kept


def test_compact_campbell_is_none_when_there_is_no_sweep():
    assert dr.compact_campbell(None) is None
    assert dr.compact_campbell({}) is None
    assert dr.compact_campbell({"rpm": [0.0, 1.0]}) is None


def test_the_stored_critical_speed_record_carries_both_branches():
    res = {"rated_rpm": 23000.0, "verdict": "subcritical",
           "rpm_plot_max": 29900.0, "f_switch_hz": DUTY_HZ,
           "critical_speeds": [{"mode": 1, "whirl": "forward", "rpm": 26207.0,
                                "margin_vs_rated_pct": 13.9}],
           "campbell": _sweep()}
    rec = dr.compact_mechanical("critical_speeds", res, {}, "fp", "now")
    assert rec["campbell"]["n_points"] == 41
    assert len(rec["campbell"]["forward"]) == 41
    assert len(rec["campbell"]["backward"]) == 41
    assert rec["rpm_plot_max"] == pytest.approx(29900.0)
    assert rec["f_switch_hz"] == pytest.approx(DUTY_HZ)
    assert rec["critical_speeds"][0]["rpm"] == 26207.0


def test_the_stored_modal_record_carries_the_carrier():
    res = {"modes": [{"f_hz": 24028.0}, {"f_hz": 24030.0}],
           "f_switch_hz": DUTY_HZ, "n_modes": 2}
    rec = dr.compact_mechanical("modes", res, {}, "fp", "now")
    assert rec["f_switch_hz"] == pytest.approx(DUTY_HZ)
    assert rec["frequencies_hz"] == [24028.0, 24030.0]


def test_a_record_of_a_run_without_pwm_says_so():
    rec = dr.compact_mechanical("modes", {"modes": [{"f_hz": 1.0}]}, {}, "fp")
    assert rec["f_switch_hz"] is None


# ---------------------------------------------------------------------------
# …and through the coupled block into the duty's coupled record
# ---------------------------------------------------------------------------

def test_compact_crit_block_keeps_the_sweep_and_the_carrier():
    blk = coupled._compact_crit({
        "rated_rpm": 23000.0, "verdict": "subcritical", "rpm_plot_max": 29900.0,
        "f_switch_hz": DUTY_HZ, "campbell": _sweep(),
        "critical_speeds": [{"whirl": "forward", "rpm": 26207.0}]})
    assert blk["campbell"]["n_points"] == 41
    assert len(blk["campbell"]["backward"]) == 41
    assert blk["f_switch_hz"] == pytest.approx(DUTY_HZ)
    assert blk["first_forward_rpm"] == 26207


def test_compact_modes_block_keeps_the_carrier():
    blk = coupled._compact_modes({
        "body": "rotor", "rpm": 23000.0, "f_switch_hz": DUTY_HZ,
        "modes": [{"f_hz": 24028.0,
                   "nearest": {"name": "PWM carrier", "hz": DUTY_HZ,
                               "margin_pct": 0.1, "flag": True}}]})
    assert blk["f_switch_hz"] == pytest.approx(DUTY_HZ)
    assert blk["tightest"]["excitation_hz"] == pytest.approx(DUTY_HZ)


def test_compact_coupled_keeps_the_critical_speed_block():
    out = {"coupling": {"iterations": 2, "mechanical": {
        "verdict": "ok", "sf_min": 1.4,
        "modes": {"f_switch_hz": DUTY_HZ, "n_modes": 12},
        "critical_speeds": {"first_forward_rpm": 26207,
                            "campbell": dr.compact_campbell(_sweep()),
                            "f_switch_hz": DUTY_HZ}}}}
    rec = dr.compact_coupled(out)
    cs = rec["mechanical"]["critical_speeds"]
    assert cs["campbell"]["n_points"] == 41
    assert len(cs["campbell"]["forward"]) == 41
    assert rec["mechanical"]["modes"]["f_switch_hz"] == pytest.approx(DUTY_HZ)
