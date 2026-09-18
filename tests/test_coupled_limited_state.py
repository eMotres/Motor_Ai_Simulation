"""SOLVE TO THE STEADY STATE, OR TO THE LIMITS — the coupled loop's two answers.

Owner, 2026-09-18: *«при каплинге, превышающем лимиты, будем ставить максимальные
значения этих лимитов и делать вычисление для них… то есть состояние мотора в
работе 24 секунды при заданной мощности»*, refined the same day to *«надо сделать
выбор — или считать до конца стабилизации температуры, или считать до лимитов и
находить время работы при заданных условиях»* and *«при заданной мощности и
заданном охлаждении»*.

So there are two questions and the user picks one.  What can go wrong is the
WIRING, and that is what this file pins:

  (a) THE DEFAULT IS UNCHANGED.  A body that says nothing is a ``steady`` run and
      its record is the record it has always been — no ``limited`` block, no
      extra electromagnetic pass, ``converged`` meaning what it meant.  This is
      the assertion that protects every stored duty in the catalog.
  (b) ``limits`` STOPS AT THE FIRST CROSSING and re-solves the machine there: one
      extra pass, at the node temperatures of that instant, and the record says
      so (``mode: "limited"``) with the part, the limit, both times, the
      temperatures at the limit and the cooling the answer is conditional on.
  (c) …AND ONLY ON A REAL CROSSING.  A point inside every limit, and a point
      whose step response settles UNDER the limit, are ``steady`` records in
      ``limits`` mode too: the steady state IS the answer there, and a moment
      that never arrives must not be reported as one.
  (d) THE POINT IS HELD on that final pass — a voltage-fed loop drives it with
      the same inverter and RECORDS where the colder machine landed.
  (e) IT SURVIVES THE STORE.  ``compact_coupled`` carries the three new keys, or
      the report and the catalog chip read a steady record where a limited one
      was solved.

Both halves of the loop are faked (the template of tests/test_coupled_duty_cycle)
— a real two-pass solve would prove the wiring most slowly and least clearly —
and so is the step response, because its own physics is pinned analytically in
tests/test_coupled_time_to_limit.py.
"""
from __future__ import annotations

import pytest

from tests.test_coupled import COOLING as PANEL_COOLING, EM_BODY

LOOP_BODY = {**EM_BODY, "thermal_settings": PANEL_COOLING,
             "magnet_temp_c": 90.0, "mechanical": False}

#: A step-response block as ``_ttl_step`` returns it for a point that IS past a
#: limit and DOES reach it — the L13 peak's shape, with the numbers rounded to
#: something a reader of this file can do arithmetic on.
def _ttl_block(*, reaches: bool = True, within: bool = False,
               t_cold: float = 24.0, t_rated: float | None = 9.0,
               winding_node: float = 172.5):
    if within:
        return {"within_limits": True, "time_to_limit_s": None,
                "limiting_part": None, "limits_c": {"winding": 200.0},
                "at_point_c": {"winding": 180.0}, "judged": ["winding"],
                "note": "every part is inside its limit"}
    cold = {"start": "cold", "time_to_limit_s": t_cold if reaches else None,
            "limiting_part": "winding" if reaches else None,
            "start_source": "every node at the ambient"}
    if reaches:
        cold["state_at_limit_c"] = {"winding": winding_node, "stator": 108.1,
                                    "rotor": 51.8, "magnet": 47.0}
    starts = {"cold": cold}
    if t_rated is not None and reaches:
        starts["rated"] = {"start": "rated", "time_to_limit_s": t_rated,
                           "limiting_part": "winding"}
    return {
        "within_limits": False,
        "time_to_limit_s": t_cold if reaches else None,
        "limiting_part": "winding",
        "limits_c": {"winding": 200.0, "magnet": 180.0},
        "at_point_c": {"winding": 430.0, "magnet": 95.0},
        "over_by_K": {"winding": 230.0},
        "over_parts": ["winding"], "judged": ["winding", "magnet"],
        "parts": [{"part": "winding", "node": "winding",
                   "quantity": "the winding hot spot", "limit_c": 200.0,
                   "limit_source": "the project's insulation class",
                   "offset_K": 27.5, "reaches": reaches,
                   "time_to_limit_s": t_cold if reaches else None,
                   **({"state_c": cold["state_at_limit_c"]} if reaches
                      else {"asymptote_c": 190.0})},
                  {"part": "magnet", "node": "magnet",
                   "quantity": "the hottest magnet element", "limit_c": 180.0,
                   "offset_K": 1.2, "reaches": False, "asymptote_c": 95.0}],
        "starts": starts,
        "network": {"available": True},
        "note": "over the winding limit",
    }


def _fake(monkeypatch, *, ttl=None, inverter=False):
    """``_em_run`` / ``_thermal_solve`` / ``_ttl_step`` as recorders.

    The map hands back a winding at 400 °C — past the class, which is the whole
    point — and every temperature the loop asks an electromagnetic run for is
    recorded, so "one extra pass, at the limit" is an assertion about a list.
    """
    from motor_ai_sim.routes import coupled as cp

    seen = {"coil_in": [], "magnet_in": [], "ttl_calls": 0}

    def _em(body, *, coil_temp_c, magnet_temp_c, **_k):
        seen["coil_in"].append(round(float(coil_temp_c), 4))
        seen["magnet_in"].append(None if magnet_temp_c is None
                                 else round(float(magnet_temp_c), 4))
        return {"summary": {"P_loss_total_W": 700.0 + len(seen["coil_in"]),
                            "T_em_avg_Nm": 5.0, "rpm": 1000.0,
                            "coil_temp_C": float(coil_temp_c)},
                "I_phase_rms_solved_A": 20.0}

    def _th(body, cooling, *, coil_temp_c, magnet_temp_c, rpm, **_k):
        return {"ok": True,
                "components": {"winding": {"avg": 400.0, "max": 430.0},
                               "magnet": {"avg": 93.0, "max": 95.0}}}

    def _ttl_step(body, cooling, em_summary, field, **_k):
        seen["ttl_calls"] += 1
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


def _run(client, **body):
    r = client.post("/api/coupled/run",
                    json={**LOOP_BODY, "max_iter": 3, "tol_k": 1.0, **body})
    assert r.status_code == 200, r.text[:800]
    return r.json()["coupling"]


# ---------------------------------------------------------------------------
# (a) the default
# ---------------------------------------------------------------------------

def test_the_default_is_the_steady_state_and_grows_no_limited_block(
        client, monkeypatch):
    """The assertion that protects every duty already in the catalog: a body
    that does not ask gets the loop it has always got."""
    seen = _fake(monkeypatch, ttl=_ttl_block())
    c = _run(client)
    assert c["solve_to"] == "steady"
    assert c["mode"] == "steady"
    assert "limited" not in c
    # The loop ran its budget on the winding at 400 °C and never once solved the
    # machine at 172.5 °C — no extra pass happened.
    assert 172.5 not in seen["coil_in"]
    assert c["em_runs"] == len(seen["coil_in"])
    assert c.get("warning_code") != "limited_operation"


def test_an_unknown_solve_to_is_refused_by_name(client, monkeypatch):
    _fake(monkeypatch, ttl=_ttl_block())
    r = client.post("/api/coupled/run", json={**LOOP_BODY, "solve_to": "asap"})
    assert r.status_code == 422
    d = r.json()["detail"]
    assert d["error_code"] == "unknown_solve_to"
    assert {"field": "solve_to"} in d["invalid_parameters"]


# ---------------------------------------------------------------------------
# (b) the limits
# ---------------------------------------------------------------------------

def test_limits_stops_at_the_first_crossing_and_solves_the_machine_there(
        client, monkeypatch):
    """THE claim.  The loop sees a pass past the class, stops there rather than
    iterating towards a state the machine never reaches, and makes ONE more
    electromagnetic run at the node temperatures of the crossing."""
    seen = _fake(monkeypatch, ttl=_ttl_block())
    c = _run(client, solve_to="limits")

    assert c["solve_to"] == "limits" and c["mode"] == "limited"
    # Pass 1 at the body's own 120 °C, then the pass AT the limit — and nothing
    # in between: the loop did not iterate towards the 400 °C map.
    assert seen["coil_in"] == [120.0, 172.5], seen["coil_in"]
    assert seen["magnet_in"] == [90.0, 47.0], seen["magnet_in"]
    # …and the record's own temperatures are that pass's, not the map's.
    assert c["coil_temp_c"] == 172.5
    assert c["magnet_temp_c"] == 47.0

    lim = c["limited"]
    assert lim["part"] == "winding"
    assert lim["limit_c"] == 200.0
    assert lim["t_cold_s"] == 24.0 and lim["t_rated_s"] == 9.0
    assert lim["t_cold_words"] == "24 s"
    assert lim["temperatures_at_limit"]["winding"] == 172.5
    assert lim["temperatures_at_limit"]["magnet"] == 47.0
    # The winding hot spot is AT the limit by construction — that is what the
    # §8 row is judged on, and it is the limit, not the node mean.
    assert lim["at_limit_c"]["winding"] == 200.0
    # What the steady state WOULD have been, beside a flag saying the loop was
    # stopped rather than iterated to it.
    assert lim["steady_state_would_be"]["winding"] == 430.0
    assert lim["steady_state_converged"] is False
    assert lim["em_run"] is True
    # THE COOLING the answer is conditional on, named (owner's addendum).
    assert "30 °C" in lim["cooling_words"] and "bore air" in lim["cooling_words"]


def test_the_sentence_is_the_one_every_surface_prints(client, monkeypatch):
    _fake(monkeypatch, ttl=_ttl_block())
    c = _run(client, solve_to="limits")
    line = c["limited"]["line"]
    assert line == ("Runs 24 s from cold (9.0 s from rated) at this power and "
                    "cooling, then the winding reaches 200 °C — the numbers "
                    "below are the machine at that moment")
    # …and it is what the record WARNS with: a limited run is not a failure to
    # converge and must not be described as one.
    assert c["warning"] == line
    assert c["warning_code"] == "limited_operation"
    # `converged` is false by construction — the state is an instant of a
    # transient, not a fixed point.
    assert c["converged"] is False


def test_the_final_pass_is_marked_in_the_history(client, monkeypatch):
    """A chart of the history must not read the last row as a residual that
    jumped: it is the pass made AT the limit, and it says so."""
    _fake(monkeypatch, ttl=_ttl_block())
    c = _run(client, solve_to="limits")
    assert c["history"][-1]["phase"] == "limit"
    assert c["history"][-1]["T_coil_in"] == 172.5
    assert c["history"][-1]["T_coil_out"] is None
    assert all("phase" not in r for r in c["history"][:-1])


# ---------------------------------------------------------------------------
# (c) …and only on a real crossing
# ---------------------------------------------------------------------------

def test_a_point_inside_every_limit_is_a_steady_record_in_limits_mode_too(
        client, monkeypatch):
    """*"within limits — the steady state after N passes, no time limit"*: the
    question was asked, the machine simply has no first limit to stop at."""
    seen = _fake(monkeypatch, ttl=_ttl_block(within=True))
    c = _run(client, solve_to="limits")
    assert c["solve_to"] == "limits"
    assert c["mode"] == "steady"
    assert "limited" not in c
    assert c["time_to_limit"]["within_limits"] is True
    assert c["time_to_limit"]["reported_state"] == "steady"
    assert 172.5 not in seen["coil_in"]


def test_a_limit_the_transient_never_reaches_leaves_the_steady_state_the_answer(
        client, monkeypatch):
    """The honest branch, kept honest in this mode: the map is over the limit
    for a reason the four nodes do not represent, so there is no moment to
    report and the loop goes on iterating towards the steady state."""
    seen = _fake(monkeypatch, ttl=_ttl_block(reaches=False))
    c = _run(client, solve_to="limits")
    assert c["mode"] == "steady"
    assert "limited" not in c
    assert 172.5 not in seen["coil_in"]
    # …and it did NOT stop after one pass: the loop kept going.
    assert len(seen["coil_in"]) > 1


def test_a_run_with_no_block_at_all_is_untouched(client, monkeypatch):
    """A machine that states no limit, or a map that could not be fitted: the
    loop answers what it always answered."""
    _fake(monkeypatch, ttl=None)
    c = _run(client, solve_to="limits")
    assert c["mode"] == "steady" and "limited" not in c
    assert "time_to_limit" not in c


# ---------------------------------------------------------------------------
# (d) the point is held on the final pass
# ---------------------------------------------------------------------------

def test_a_voltage_fed_loop_holds_the_point_and_records_where_it_landed(
        client, monkeypatch):
    """The regulator was aiming at this duty's current; the pass at the limit is
    driven by the same inverter, and how far the COLDER machine then landed is
    recorded rather than regulated away — there is exactly one extra pass."""
    from motor_ai_sim.routes import coupled as cp

    seen = _fake(monkeypatch, ttl=_ttl_block())
    monkeypatch.setattr(
        cp, "_inverter_settings",
        lambda body, *, rpm: {"f_carrier_hz": 24000.0, "v_phase_peak_V": 300.0,
                              "v_dc_V": 750.0, "carriers_per_period": 40,
                              "n_steps_per_period": 400,
                              "target_I_phase_rms_A": 20.0, "i_tol_pct": 1.0,
                              "v_phase_peak_max_V": 400.0,
                              "v_phase_peak_max_uncompensated_V": 420.0,
                              "schedule": "mixed", "v_delta_deg": 0.0,
                              "modulation_index": 0.8, "waveform": "svpwm",
                              "f_elec_hz": 100.0, "record_as": "",
                              "sources": {}},
        raising=True)
    monkeypatch.setattr(cp, "_pwm_dc_verdict",
                        lambda inv, s: (True, True, 0.0, 1.0, ""), raising=True)
    monkeypatch.setattr(cp, "_pwm_loss_map",
                        lambda *a, **k: ({}, {}), raising=True)
    monkeypatch.setattr(cp, "_regulate_v1", lambda inv, i, pts: None,
                        raising=True)

    c = _run(client, solve_to="limits", drive="pwm")
    assert c["mode"] == "limited"
    assert c["limited"]["drive_held"] == "pwm"
    assert c["limited"]["v_phase_peak_V"] == 300.0
    assert c["limited"]["I_phase_rms_solved_A"] == 20.0
    assert c["limited"]["point_error_pct"] == 0.0
    assert c["history"][-1]["phase"] == "limit"
    assert c["history"][-1]["v_phase_peak_V"] == 300.0
    # ONE extra pass, never two: the loop is not re-entered.
    assert seen["coil_in"] == [120.0, 172.5]


# ---------------------------------------------------------------------------
# (e) the record, and its persistence
# ---------------------------------------------------------------------------

LIMITED = {
    "part": "winding", "limit_c": 200.0, "t_cold_s": 24.0, "t_rated_s": 9.0,
    "t_cold_words": "24 s", "t_rated_words": "9.0 s",
    "temperatures_at_limit": {"winding": 172.5, "magnet": 47.0},
    "at_limit_c": {"winding": 200.0, "magnet": 48.2},
    "steady_state_would_be": {"winding": 430.0},
    "steady_state_converged": False,
    "cooling_words": "air 10 m/s at 30 °C, bore air 40 m/s",
    "line": "Runs 24 s from cold (9.0 s from rated) at this power and cooling, "
            "then the winding reaches 200 °C — the numbers below are the "
            "machine at that moment",
}


def test_the_three_keys_survive_compact_coupled_into_the_duty_record():
    """The report and the catalog chip read the DUTY's stored record, not the
    run: a mode that does not make that crossing reaches no page."""
    from motor_ai_sim.duty_results import compact_coupled
    rec = compact_coupled({"coupling": {"coil_temp_c": 172.5,
                                        "solve_to": "limits",
                                        "mode": "limited",
                                        "limited": dict(LIMITED)},
                           "computed_at": "2026-09-18T00:00:00"})
    assert rec["solve_to"] == "limits"
    assert rec["mode"] == "limited"
    assert rec["limited"]["t_cold_s"] == 24.0
    assert rec["limited"]["at_limit_c"]["winding"] == 200.0
    # …and a steady record grows no keys at all, rather than null ones.
    plain = compact_coupled({"coupling": {"coil_temp_c": 120.0}})
    assert "mode" not in plain and "limited" not in plain \
        and "solve_to" not in plain


def test_the_report_reads_the_mode_and_judges_the_limited_state():
    """§8 judges the machine AT the limit: the winding sits exactly ON its class
    — amber, "it runs 24 s" — and never red "it is 430 °C"."""
    from motor_ai_sim import report as rp
    rec = {"mode": "limited", "limited": dict(LIMITED), "converged": False}
    assert rp.coupled_mode(rec) == "limited"
    assert rp.limited_words(rec) == LIMITED["line"]
    # The "Converged" cell says what it stopped at, not "no".
    assert rp.converged_words(rec) == LIMITED["line"]
    # …and the one clause the coupled table prints over its temperatures.
    clause = rp.limited_state_clause(rec)
    assert clause.startswith("the limit, after 24 s from cold (9.0 s from rated)")
    assert "430 °C" in clause and "stopped at the limit" in clause
    # A steady record has none of it.
    assert rp.coupled_mode({"converged": True}) == "steady"
    assert rp.limited_state_clause({"converged": True}) == ""


def test_the_catalog_row_says_which_state_it_is():
    """The chip's job changes with the mode: a steady record warns that the
    point is past a limit, a limited one says how long it runs."""
    from motor_ai_sim.routes.family import _time_to_limit_row
    row = _time_to_limit_row({
        "mode": "limited", "limited": dict(LIMITED),
        "time_to_limit": _ttl_block()})
    assert row["mode"] == "limited"
    assert row["at_point_c"] == 200.0          # the limit, not the 430 °C map
    assert row["note"] == LIMITED["line"]
    assert row["cooling_words"] == LIMITED["cooling_words"]
    # …and a steady record's row is exactly what it always was.
    steady = _time_to_limit_row({"time_to_limit": _ttl_block()})
    assert "mode" not in steady
    assert steady["at_point_c"] == 430.0


def test_section_8_judges_the_limited_state_amber_not_red():
    """The winding sits exactly ON its class at the moment the pull ends, so §8
    reads "it runs 24 s" (amber) and never "it is 430 °C" (red) — the steady
    state is a machine this record explicitly does not describe."""
    from motor_ai_sim.report import duty_warnings

    ctx = {"duty": "peak", "coupled_mode": "limited", "limited": dict(LIMITED),
           "winding_temp_c": 200.0, "winding_limit_c": 200.0,
           "hot_spot_c": 200.0,
           "magnet_temp_c": 48.2, "magnet_limit_c": 180.0,
           "winding_limit_note": "class N; the machine AT the limit: "
                                 + LIMITED["line"]}
    rows = {w["rule"]: w for w in duty_warnings(ctx)}
    assert rows["winding_temperature"]["level"] == "amber"
    assert rows["winding_temperature"]["value"] == 200.0
    assert "the machine AT the limit" in rows["winding_temperature"]["note"]
    # …and the magnets, cold at that instant, are simply green.
    assert rows["magnet_temperature"]["level"] == "green"
    # NO second clause about the same time: the note already says it once.
    assert "held here" not in rows["winding_temperature"]["note"]
    # A part STILL past its own limit at that instant stays red.
    hot = {w["rule"]: w for w in duty_warnings(
        dict(ctx, magnet_temp_c=195.0))}
    assert hot["magnet_temperature"]["level"] == "red"


# ---------------------------------------------------------------------------
# the MAP at the same instant — one state, one picture
# ---------------------------------------------------------------------------

def _tiny_map():
    """Four triangles: a coil, a stator tooth, a rotor and a magnet, each with
    one vertex of its own plus one shared corner."""
    return {
        "ok": True,
        "triangles": [[0, 1, 2], [1, 2, 3], [2, 3, 4], [2, 4, 5]],
        "domain_per_tri": [2, 1, 5, 4],          # coil, stator, rotor, magnet
        "temperature_per_node": [100.0, 110.0, 120.0, 130.0, 140.0, 150.0],
        "T_max": 150.0, "T_min": 100.0,
        "components": {"winding": {"avg": 110.0, "max": 120.0},
                       "stator": {"avg": 120.0, "max": 130.0},
                       "rotor": {"avg": 130.0, "max": 140.0},
                       "magnet": {"avg": 136.7, "max": 150.0}},
    }


def test_the_map_is_translated_onto_the_instant_and_says_so():
    """The tables say the winding is at 200 °C; the picture beside them must be
    the same machine, or the reader has two states of one motor."""
    from motor_ai_sim.routes.thermal import rescale_map_to_nodes

    src = _tiny_map()
    before = list(src["temperature_per_node"])
    out = rescale_map_to_nodes(
        src, {"winding": 172.5, "stator": 108.1, "rotor": 51.8, "magnet": 47.0})
    # A COPY: the solved map is still the solved map.
    assert src["temperature_per_node"] == before
    assert out is not src
    # MARKED, so nothing downstream takes it for a solved steady map.
    snap = out["transient_snapshot"]
    assert snap["kind"] == "time_to_limit"
    assert snap["shift_K"]["winding"] == pytest.approx(172.5 - 110.0, abs=0.01)
    assert snap["shift_K"]["rotor"] == pytest.approx(51.8 - 130.0, abs=0.01)
    assert "shape is frozen" in snap["note"] or "SHAPE" in snap["note"] \
        or "shape" in snap["note"]
    # The winding went UP towards its limit and the rotor went DOWN — the whole
    # point of reporting the instant rather than the steady state.
    assert out["components"]["winding"]["avg"] > 110.0
    assert out["components"]["rotor"]["avg"] < 130.0
    # …and the headline temperatures are re-read off the shifted field, never
    # carried over from the map that was solved.
    assert out["T_max"] == pytest.approx(max(out["temperature_per_node"]), abs=0.05)
    assert out["T_min"] == pytest.approx(min(out["temperature_per_node"]), abs=0.05)
    assert out["T_max"] != src["T_max"]

    # …and a map with nothing to shift comes back untouched and UNMARKED: an
    # unmarked map is a solved one, and that promise must not be weakened.
    assert "transient_snapshot" not in rescale_map_to_nodes(_tiny_map(), {})
    assert "transient_snapshot" not in rescale_map_to_nodes({"ok": False}, {})
