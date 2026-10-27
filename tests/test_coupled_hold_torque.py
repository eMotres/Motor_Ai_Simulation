"""HOLD THE DUTY'S TORQUE at the magnet temperature the loop finds (owner 2026-10-27).

A 400 mm eVTOL motor peak: 1500 N·m @ 1000 rpm, 308.1 A sized with the magnet
card at 150 °C.  The loop kept that current while its thermal passes moved the
magnets to ≈ 83 °C, so the machine made 1767 N·m and every thermal answer
belonged to a current the duty does not need.  With a torque target the loop now
corrects the current after each electromagnetic pass so the SHAFT torque meets
the target at that pass's own temperatures, and converges torque and
temperatures together.

Both halves are faked (no FEM on this PC): the electromagnetic pass is a power
law in the current whose constant falls with the magnet temperature, the thermal
pass heats the magnets with I², so the fixed point is known and the loop has to
find it.
"""
from __future__ import annotations

import pytest

from tests.test_coupled import COOLING as PANEL_COOLING, EM_BODY
from tests.test_coupled_limited_state import _ttl_block

#: The fake machine: T = K0 · (1 − BR_TC·(T_mag − 20)) · (I / I0)^ALPHA.
K0, BR_TC, I0, ALPHA = 10.0, 0.0012, 20.0, 0.85

LOOP_BODY = {**EM_BODY, "thermal_settings": PANEL_COOLING,
             "magnet_temp_c": 150.0, "mechanical": False,
             "cold_constants": False}


def _torque(i, t_mag):
    return K0 * (1.0 - BR_TC * (float(t_mag) - 20.0)) * (float(i) / I0) ** ALPHA


def _fake(monkeypatch, *, ttl=None, extra_summary=None):
    from motor_ai_sim.routes import coupled as cp

    seen = {"em": [], "th": []}

    def _em(body, *, coil_temp_c, magnet_temp_c, **_k):
        i = float(body["I_phase_rms"])
        seen["em"].append((i, round(float(coil_temp_c), 4),
                           None if magnet_temp_c is None
                           else round(float(magnet_temp_c), 4)))
        tm = 150.0 if magnet_temp_c is None else magnet_temp_c
        return {"summary": {"T_em_avg_Nm": _torque(i, tm),
                            "P_loss_total_W": 2.0 * i * i, "rpm": 1000.0,
                            "I_phase_rms_A": i,
                            "coil_temp_C": float(coil_temp_c),
                            **(extra_summary or {})}}

    def _th(body, cooling, *, coil_temp_c, magnet_temp_c, rpm, **_k):
        i = float(body["I_phase_rms"])
        seen["th"].append(i)
        w, m = 30.0 + 0.2 * i * i, 30.0 + 0.1 * i * i
        return {"ok": True,
                "components": {"winding": {"avg": w, "max": w + 5.0},
                               "magnet": {"avg": m, "max": m + 2.0}}}

    def _ttl_step(body, cooling, em_summary, field, **_k):
        return None if ttl is None else dict(ttl)

    monkeypatch.setattr(cp, "_em_run", _em, raising=True)
    monkeypatch.setattr(cp, "_thermal_solve", _th, raising=True)
    monkeypatch.setattr(cp, "_ttl_step", _ttl_step, raising=True)
    monkeypatch.setattr(cp, "_attach_coupling", lambda em, block: False,
                        raising=True)
    monkeypatch.setattr(cp, "_remember_last", lambda out, **k: None,
                        raising=True)
    return seen


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    return TestClient(app)


def _post(client, **body):
    return client.post("/api/coupled/run",
                       json={**LOOP_BODY, "max_iter": 10, "tol_k": 0.5,
                             **body})


def _run(client, **body):
    r = _post(client, **body)
    assert r.status_code == 200, r.text[:800]
    return r.json()["coupling"]


# ---------------------------------------------------------------------------
# the current update itself
# ---------------------------------------------------------------------------

def test_one_pass_takes_a_proportional_step():
    from motor_ai_sim.routes import coupled as cp
    i, info = cp._hold_next_current([(100.0, 1767.0)], 1500.0,
                                    i_cap=1e9, i_floor=1.0)
    assert info["method"] == "proportional"
    assert i == pytest.approx(100.0 * 1500.0 / 1767.0, abs=1e-3)


def test_two_passes_take_a_secant_that_is_exact_on_a_power_law():
    from motor_ai_sim.routes import coupled as cp
    pts = [(20.0, _torque(20.0, 80.0)), (22.0, _torque(22.0, 80.0))]
    target = _torque(21.0, 80.0)
    i, info = cp._hold_next_current(pts, target, i_cap=1e9, i_floor=1.0)
    assert info["method"] == "secant"
    assert info["alpha_measured"] == pytest.approx(ALPHA, rel=1e-6)
    assert i == pytest.approx(21.0, abs=2e-3)


def test_the_step_is_bounded_and_the_ceiling_holds():
    from motor_ai_sim.routes import coupled as cp
    i, info = cp._hold_next_current([(100.0, 100.0)], 1000.0,
                                    i_cap=1e9, i_floor=1.0)
    assert info["step_bounded"] and i == pytest.approx(125.0)
    i, info = cp._hold_next_current([(100.0, 100.0)], 1000.0,
                                    i_cap=110.0, i_floor=1.0)
    assert info["capped"] and i == pytest.approx(110.0)
    i, _ = cp._hold_next_current([(100.0, 1000.0)], 10.0,
                                 i_cap=1e9, i_floor=1.0)
    assert i == pytest.approx(75.0)


def test_a_machine_that_no_longer_makes_torque_is_unreachable():
    from motor_ai_sim.routes import coupled as cp
    _, info = cp._hold_next_current([(100.0, 500.0), (120.0, 501.0)], 800.0,
                                    i_cap=1e9, i_floor=1.0)
    st = cp._hold_unreachable(-30.0, 120.0, info, {}, i_cap=1e9, v_link=None)
    assert st[0] == "torque_unreachable_saturation"
    # …the voltage and the demagnetisation are judged too, only when MORE
    # torque is needed.
    ok = {"alpha_measured": 1.0}
    assert cp._hold_unreachable(-5.0, 10.0, ok, {"V_line_peak_V": 900.0},
                                i_cap=1e9, v_link=800.0)[0] == (
        "torque_unreachable_voltage")
    assert cp._hold_unreachable(-5.0, 10.0, ok, {"demag": {"loss_pct": 3.0}},
                                i_cap=1e9, v_link=None)[0] == (
        "torque_unreachable_demag")
    assert cp._hold_unreachable(+5.0, 10.0, ok, {"V_line_peak_V": 900.0},
                                i_cap=1e9, v_link=800.0) is None


# ---------------------------------------------------------------------------
# where the target comes from
# ---------------------------------------------------------------------------

def _plan(monkeypatch, body, duty=None, *, flag_rpm=1000.0):
    from motor_ai_sim import duty_results as dr
    from motor_ai_sim.routes import coupled as cp
    from motor_ai_sim.routes import family as fam
    monkeypatch.setattr(dr, "active_context",
                        lambda: ("D", "C", "peak") if duty else None)
    monkeypatch.setattr(fam, "duty_entry", lambda *a: duty)
    return cp._hold_torque_plan(body, rpm=flag_rpm, max_iter=6,
                                suppressed=False, voltage_fed=False)


def test_the_duty_target_applies_only_to_the_duty_s_own_point(monkeypatch):
    duty = {"name": "peak", "current_arms": 308.1, "rpm": 1000.0,
            "torque_nm": 1500.0}
    p = _plan(monkeypatch, {"I_phase_rms": 308.1}, duty)
    assert p["active"] and p["target_nm"] == 1500.0
    assert p["target_source"] == "duty 'peak'"
    # a current typed by hand is a question about that current…
    p = _plan(monkeypatch, {"I_phase_rms": 250.0}, duty)
    assert not p["active"] and "not the duty's" in p["note"]
    # …unless the request says hold_torque: true.
    p = _plan(monkeypatch, {"I_phase_rms": 250.0, "hold_torque": True}, duty)
    assert p["active"] and p["target_nm"] == 1500.0
    # a generator's negative target is held as a magnitude
    p = _plan(monkeypatch, {"I_phase_rms": 308.1},
              dict(duty, torque_nm=-1500.0))
    assert p["active"] and p["target_nm"] == 1500.0
    # false keeps today's fixed current
    p = _plan(monkeypatch, {"I_phase_rms": 308.1, "hold_torque": False}, duty)
    assert not p["active"] and "hold_torque: false" in p["note"]


def test_an_impossible_explicit_hold_is_refused_not_dropped(monkeypatch):
    from fastapi import HTTPException

    from motor_ai_sim.routes import coupled as cp
    with pytest.raises(HTTPException) as e:
        _plan(monkeypatch, {"I_phase_rms": 20.0, "hold_torque": True})
    assert e.value.status_code == 422
    assert e.value.detail["error_code"] == "hold_torque_impossible"
    with pytest.raises(HTTPException):
        cp._hold_torque_plan({"I_phase_rms": 20.0, "torque_nm": 5.0,
                              "hold_torque": True}, rpm=1000.0, max_iter=6,
                             suppressed=False, voltage_fed=True)
    # without the explicit flag the same case is a fixed current, said
    p = cp._hold_torque_plan({"I_phase_rms": 20.0, "torque_nm": 5.0},
                             rpm=1000.0, max_iter=1, suppressed=False,
                             voltage_fed=False)
    assert not p["active"] and "single pass" in p["note"]


# ---------------------------------------------------------------------------
# the loop
# ---------------------------------------------------------------------------

def test_the_steady_loop_holds_the_torque_at_the_magnet_temperature_it_finds(
        client, monkeypatch):
    seen = _fake(monkeypatch)
    c = _run(client, torque_nm=10.0)
    ht = c["hold_torque"]
    assert ht["basis"] == "held_torque" and ht["held"] is True
    assert c["converged"] is True
    # every solve after the first was made at a corrected current…
    currents = [e[0] for e in seen["em"]]
    assert currents[0] == 20.0 and len(set(currents)) > 1
    # …and the thermal half was handed the same current as its EM pass
    assert seen["th"] == currents
    # THE RECORD IS ONE STATE: the current it ended at, the torque that
    # current makes at the magnet temperature it was solved at, and that
    # magnet temperature is a fixed point of the fake thermal half.
    i_end = ht["I_phase_rms_A"]
    assert i_end == currents[-1]
    assert ht["magnet_temp_c"] == pytest.approx(c["magnet_temp_c"])
    assert abs(_torque(i_end, c["magnet_temp_c"]) / 10.0 - 1.0) <= 0.005
    assert c["magnet_temp_c"] == pytest.approx(30.0 + 0.1 * i_end ** 2,
                                               abs=1.0)
    assert abs(ht["torque_error_pct"]) <= ht["tol_pct"] == 0.5
    # the stamp stays the DUTY's point: the record is found under it
    assert c["point"]["I_phase_rms"] == 20.0
    assert ht["I_requested_A"] == 20.0
    assert "current held for 10.0 N·m at magnet" in ht["line"]
    rows = c["history"]
    assert all("torque_error_pct" in r and "I_phase_rms_A" in r for r in rows)


def test_hold_torque_false_is_today_s_fixed_current(client, monkeypatch):
    seen = _fake(monkeypatch)
    c = _run(client, torque_nm=10.0, hold_torque=False)
    assert {e[0] for e in seen["em"]} == {20.0}
    assert c["hold_torque"]["basis"] == "fixed_current"
    assert c["hold_torque"]["I_phase_rms_A"] == 20.0


def test_no_target_keeps_the_current_and_says_why(client, monkeypatch):
    seen = _fake(monkeypatch)
    c = _run(client)
    assert {e[0] for e in seen["em"]} == {20.0}
    assert c["hold_torque"]["basis"] == "fixed_current"
    assert "no torque target" in c["hold_torque"]["note"]


def test_out_of_budget_the_record_says_the_torque_was_not_held(
        client, monkeypatch):
    _fake(monkeypatch)
    c = _run(client, torque_nm=10.0, max_iter=2)
    assert c["converged"] is False
    assert c["warning_code"] == "torque_not_held"
    assert "the torque was not held" in c["warning"]
    assert c["hold_torque"]["held"] is False


def test_a_target_past_the_current_ceiling_stops_and_says_so(
        client, monkeypatch):
    seen = _fake(monkeypatch)
    c = _run(client, torque_nm=30.0, hold_i_max_a=24.0)
    assert max(e[0] for e in seen["em"]) == 24.0
    assert c["warning_code"] == "torque_unreachable_current"
    assert "cannot be held" in c["warning"] and "24.0 A" in c["warning"]
    assert c["hold_torque"]["unreachable_code"] == "torque_unreachable_current"
    assert c["hold_torque"]["held"] is False


def test_limits_quotes_the_crossing_of_the_machine_making_the_duty_s_torque(
        client, monkeypatch):
    """The measured bug's own shape: pass 1 sits at the card's 150 °C… here the
    body's 120 °C coil / 150 °C magnets, past the winding class.  The crossing
    instant has the magnets at 48.2 °C, where the duty's current makes far too
    much torque — so the loop solves AT the instant's temperatures with a
    corrected current until the torque is held there, and only then stops and
    makes its pass at the limit."""
    seen = _fake(monkeypatch, ttl=_ttl_block())
    c = _run(client, torque_nm=10.5, solve_to="limits", max_iter=8)
    assert c["mode"] == "limited"
    ht = c["hold_torque"]
    assert ht["held"] is True, (seen["em"], ht)
    # the passes after the first were made at the instant's temperatures
    assert all(e[1] == 200.0 and e[2] == 48.2 for e in seen["em"][1:])
    # the limit pass is the last solve and holds the torque at 48.2 °C
    assert seen["em"][-1][0] == ht["I_phase_rms_A"]
    assert abs(_torque(ht["I_phase_rms_A"], 48.2) / 10.5 - 1.0) <= 0.005
    assert ht["magnet_temp_c"] == 48.2
    assert any(r.get("limit_chase") for r in c["history"])
    lim_row = [r for r in c["history"] if r.get("phase") == "limit"][-1]
    assert lim_row["I_phase_rms_A"] == ht["I_phase_rms_A"]
    assert c["em_runs"] == len(seen["em"])


def test_out_of_budget_the_pass_at_the_limit_corrects_its_own_current(
        client, monkeypatch):
    """Two passes are not enough to hold the torque at the instant; the loop
    stops at the limit as before and the pass AT the limit is re-solved at the
    same temperatures with a corrected current — at most twice, every solve
    counted."""
    from motor_ai_sim.routes import coupled as cp
    seen = _fake(monkeypatch, ttl=_ttl_block())
    c = _run(client, torque_nm=10.5, solve_to="limits", max_iter=2)
    assert c["mode"] == "limited"
    lim_row = [r for r in c["history"] if r.get("phase") == "limit"][-1]
    k = lim_row["hold_resolves"]
    assert 1 <= k <= cp.HOLD_LIMIT_MAX_PASSES
    assert c["hold_torque"]["limit_pass_corrections"] == k
    # every solve after the first at the limit's temperatures, every one counted
    assert all(e[1] == 200.0 and e[2] == 48.2 for e in seen["em"][1:])
    assert c["em_runs"] == len(seen["em"])
    assert seen["em"][-1][0] == c["hold_torque"]["I_phase_rms_A"]
    err = abs(_torque(seen["em"][-1][0], 48.2) / 10.5 - 1.0) * 100.0
    assert c["hold_torque"]["torque_error_pct"] == pytest.approx(
        _torque(seen["em"][-1][0], 48.2) / 10.5 * 100.0 - 100.0, abs=1e-3)
    if err > 0.5:
        assert c["warning_code"] == "torque_not_held"


def test_the_stored_record_keeps_the_basis(client, monkeypatch):
    from motor_ai_sim import duty_results as dr
    _fake(monkeypatch)
    out = _post(client, torque_nm=10.0).json()
    rec = dr.compact_coupled(out)
    ht = rec["hold_torque"]
    assert ht["basis"] == "held_torque" and ht["held"] is True
    for k in ("I_phase_rms_A", "torque_nm", "magnet_temp_c", "target_nm"):
        assert ht.get(k) is not None
    assert "passes" not in ht


def test_the_report_prints_one_line_for_a_held_record():
    from motor_ai_sim import report as rp
    rec = {"hold_torque": {"basis": "held_torque", "target_nm": 1500.0,
                           "I_phase_rms_A": 270.4, "I_requested_A": 308.1,
                           "magnet_temp_c": 83.2, "held": True}}
    assert rp.held_torque_words(rec) == (
        "270.4 A for 1500.0 N·m at magnet 83 °C (duty 308.1 A)")
    assert rp.held_torque_words({"hold_torque": {"basis": "fixed_current"}}) == ""
    assert rp.held_torque_words({}) == ""
    rec["hold_torque"]["held"] = False
    assert rp.held_torque_words(rec).endswith("— not held")


def test_the_warm_start_says_which_current_its_map_carries(monkeypatch):
    from motor_ai_sim import duty_results as dr
    from motor_ai_sim.routes import coupled as cp
    comps = {n: {"avg": 90.0} for n in ("winding", "stator", "rotor", "magnet")}
    recs = {"rated": {"thermal": {"components": comps},
                      "coupled": {"hold_torque": {
                          "basis": "held_torque", "I_phase_rms_A": 120.0,
                          "target_nm": 600.0}}}}
    monkeypatch.setattr(dr, "get", lambda die, cfg: recs)
    state, rated, why = cp._rated_state_c("D", "C", [{"name": "rated"},
                                                     {"name": "peak"}], "peak")
    assert rated == "rated" and state["magnet"] == 90.0
    assert "held at 120.0 A for 600.0 N·m" in why
