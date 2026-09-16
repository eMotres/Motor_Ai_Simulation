"""The coupled loop FINDS the regime — the search, the feedback, the record.

User, 2026-09-16: *"каплинг на цикле S3 подбирает скважность для того чтобы можно
было влезть в лимиты"*.  Three claims, tested in three layers:

  (a) THE MODEL — ``coupled_duty_cycle`` on the L13 fixtures (the Ø85 robot
      joint, the same maps ``tests/test_thermal_duty_cycle`` pins the physics
      on).  The allowable ED must be the one the limits allow, the temperatures
      reported must be the ones AT it, the S2 answer must be a pull length, and
      a point with no feasible ratio must SAY so rather than hand back a
      settled-looking pair;
  (b) THE LOOP — the ED search wired into ``/api/coupled/run`` with both solvers
      faked and a monotone regime (a hotter winding → a lower allowable ED →
      a cooler cycle), because what is unproven elsewhere is the WIRING: that
      the fed-back pair is the cycle's and not the steady map's, that the loop
      does not stop while the ED is still walking, and that a ratio that does
      not fit comes back as an answer with a warning rather than as a failure;
  (c) THE RECORD — the block the coupled answer carries and the ``duty_cycle``
      record it files, which must print through the report's EXISTING section
      (the Allowable-regime line and the rows) with no new prose.

Nothing here solves anything electromagnetic or two-dimensional.
"""
from __future__ import annotations

import types

import pytest

from motor_ai_sim import coupled_duty_cycle as cdc
import motor_ai_sim.thermal_duty_cycle as tdc
from tests.test_thermal_capacities import L13_MATERIALS, L13_PARTS
from tests.test_thermal_duty_cycle import (D_HOUSING_M, L13_GEO, PEAK_MAP,
                                           PEAK_SUMMARY, RATED_SUMMARY,
                                           STACK_M)

COOLING = {"ambient_temp": 40.0, "emissivity": 0.9, "mount_g_w_per_k": 0.0,
           "mount_temp_c": 40.0}
S3 = {"kind": "S3", "ed_pct": 25.0, "cycle_s": 60.0, "rest_duty": None}
S2 = {"kind": "S2", "t_on_s": 2.0}
DUTIES = [{"name": "peak", "rpm": 1000, "summary": PEAK_SUMMARY}]


@pytest.fixture(scope="module")
def areas():
    return tdc.end_face_areas(RATED_SUMMARY, stack_m=STACK_M, geometry=L13_GEO)


def _model(block, areas, *, summary=PEAK_SUMMARY, thermal=PEAK_MAP, **kw):
    return cdc.build_model(
        block=block, duties=DUTIES, duty_name="peak", em_summary=summary,
        thermal_result=thermal, geometry=L13_GEO, cooling=COOLING,
        materials=L13_MATERIALS, part_states=L13_PARTS, side_areas=areas,
        d_housing_m=D_HOUSING_M, **kw)


# ---------------------------------------------------------------------------
# (a) THE MODEL
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def s3(areas):
    m = _model(dict(S3), areas)
    return m, cdc.solve_regime(m, samples_per_segment=cdc.FINAL_SAMPLES,
                               with_curve=True)


def test_the_network_is_fitted_to_the_map_this_pass_solved(areas):
    """THE difference from the duty-cycle tool: the calibration map is the map
    of the point being iterated, so the hot-spot offset is the one that point
    has — 27.5 K on the peak map against 2.4 K on the rated one, which is 25 K
    of winding the limit is judged on."""
    m = _model(dict(S3), areas)
    assert m.hot_spot_offset_k == pytest.approx(777.0 - 749.5, abs=0.01)
    assert m.network.calibration.get("duty") in (None, "peak")
    # …and the powered segment's watts are THIS run's, not a stored duty's
    assert m.profile.segments[0].name == "peak"
    assert m.profile.segments[0].total_W == pytest.approx(687.3, rel=0.02)


def test_the_allowable_ratio_is_the_one_the_limits_allow(s3):
    m, r = s3
    assert r["kind"] == "S3"
    ed = r["ed_allowable_pct"]
    assert 0.0 < ed < 25.0                    # 45.9 A is a hard pull for 13 mm
    assert r["limiting_part"] == "winding"
    # ON the limit: the hot spot at the found ratio sits on the class, not under
    # it by a margin nobody asked for and not over it by half a bisection step.
    assert r["winding_hot_peak_c"] == pytest.approx(
        m.limits["winding"], abs=0.5)
    assert r["at_allowable"]["peak_c"]["winding"] < r["winding_hot_peak_c"]
    assert r["t_on_allowable_s"] == pytest.approx(
        60.0 * ed / 100.0, rel=1e-6)


def test_the_temperatures_reported_are_the_ones_at_that_ratio(s3):
    """The pair the electromagnetic half is re-solved at: the winding NODE (a
    spatial mean, as ``coil_temp_c`` has always been) and the magnet node, both
    at their peak over the cycle."""
    _m, r = s3
    at = r["at_allowable"]
    assert r["coil_temp_c"] == pytest.approx(at["peak_c"]["winding"], abs=0.01)
    assert r["magnet_temp_c"] == pytest.approx(at["peak_c"]["magnet"], abs=0.01)
    # and NOT the steady map's, which is where this point would end up if the
    # pull never ended
    assert r["coil_temp_c"] < 0.5 * PEAK_MAP["components"]["winding"]["avg"]


def test_the_requested_ratio_is_judged_but_never_answered_with(s3):
    _m, r = s3
    assert r["ed_requested_pct"] == 25.0
    assert r["fits_requested"] is False
    assert r["ed_allowable_pct"] < 25.0
    assert "does NOT fit" in r["note"]


def test_a_ratio_that_fits_says_so(areas):
    """The same machine asked about a ratio it can hold: the answer is still the
    allowable one, and the request is marked as fitting under it."""
    m = _model(dict(S3, ed_pct=1.0), areas)
    r = cdc.solve_regime(m, samples_per_segment=12)
    assert r["fits_requested"] is True
    assert r["ed_allowable_pct"] > 1.0
    assert "fits under it" in r["note"]


def test_a_cycle_with_no_stated_ratio_is_simply_found(areas):
    blk = {k: v for k, v in S3.items() if k != "ed_pct"}
    m = _model(blk, areas)
    r = cdc.solve_regime(m, samples_per_segment=12)
    assert r["ed_requested_pct"] is None
    assert r["fits_requested"] is None
    assert r["ed_allowable_pct"] > 0.0


def test_the_s2_question_is_how_long_the_pull_may_last(areas):
    m = _model(dict(S2), areas)
    r = cdc.solve_regime(m, with_curve=True)
    assert r["kind"] == "S2"
    assert r["t_on_allowable_s"] > 2.0          # the 2 s asked for fits
    assert r["fits_requested"] is True
    assert r["limiting_part"] == "winding"
    # the temperatures are the ones at the END of the allowable pull…
    assert r["winding_hot_peak_c"] == pytest.approx(m.limits["winding"], abs=1.0)
    # …and the magnets, which have a 13 mm stack to heat, are nowhere near it
    assert r["magnet_peak_c"] < r["coil_temp_c"]
    assert r["feasible"] is True and r["unlimited"] is False


def test_a_pull_longer_than_the_machine_allows_does_not_fit(areas):
    m = _model({"kind": "S2", "t_on_s": 600.0}, areas)
    r = cdc.solve_regime(m, samples_per_segment=12)
    assert r["fits_requested"] is False
    assert "does NOT fit" in r["note"]


def test_a_point_with_no_feasible_ratio_says_so_and_offers_the_pull(areas):
    """NEVER A FAKE CONVERGENCE.  A magnet limit this point cannot respect at any
    duty ratio must come back as "there is no regime" plus the single-pulse time
    — the same number the duty-cycle tool prints — and not as a settled pair."""
    m = _model(dict(S3), areas, magnet_limit_c=45.0,
               magnet_limit_source="the request (magnet_limit_c)")
    r = cdc.solve_regime(m, samples_per_segment=12)
    assert r["feasible"] is False
    assert r["ed_allowable_pct"] == 0.0
    assert r["fits_requested"] is False
    assert r["s2_time_to_limit_s"] and r["s2_time_to_limit_s"] > 0.0
    assert "no duty ratio is allowable" in r["note"]
    # the pair that travels on is where that one pull ENDS — a real state
    assert r["coil_temp_c"] is not None and r["magnet_temp_c"] is not None


def test_the_magnets_are_only_judged_when_a_limit_was_given():
    assert cdc.magnet_limit({}, None)[0] is None
    assert cdc.magnet_limit({"magnet_limit_c": 120}, None)[0] == 120.0
    assert cdc.magnet_limit({"magnet_limit_c": 120}, 100)[0] == 100.0
    assert "not judged" in cdc.magnet_limit({}, None)[1]


def test_a_cycle_naming_another_duty_is_refused_by_name(areas):
    with pytest.raises(tdc.DutyCycleError) as exc:
        _model(dict(S3, duty="rated"), areas)
    assert exc.value.code == "duty_cycle_point_mismatch"


def test_a_run_with_no_mass_rows_cannot_be_integrated(areas):
    summary = {k: v for k, v in PEAK_SUMMARY.items() if k != "mass_components"}
    with pytest.raises(tdc.DutyCycleError) as exc:
        _model(dict(S3), areas, summary=summary)
    assert exc.value.code == "duty_cycle_no_capacity"


def test_more_copper_watts_is_a_lower_allowable_ratio(areas):
    """The monotone the search rests on.

    And the reason the loop converges on an impulse duty in a pass or two: what
    the cycle integrates is I²R(T_node), and ρ_Cu(T) is ALREADY inside the model
    (``_seg_powers`` re-references the watts from the temperature the run was
    solved at to the temperature the node is at).  So the ED barely moves with
    the temperature fed back — it moves with the WATTS, which is this test — and
    what is left for the loop to close is the iron, the magnets and the
    conductances of the map.
    """
    # the losses are read off the summary here (no `P_cu_exact_W` in the map),
    # so the network fitted to that map is the same one in both cases and the
    # only thing that moves is the copper.
    mp = {k: v for k, v in PEAK_MAP.items() if k != "P_cu_exact_W"}
    eds = []
    for watts in (500.0, 700.0):
        s = dict(PEAK_SUMMARY, P_stranded_W=watts)
        m = _model(dict(S3), areas, summary=s, thermal=mp)
        eds.append(cdc.solve_regime(m, samples_per_segment=12)
                   ["ed_allowable_pct"])
    assert eds[1] < eds[0]


# ---------------------------------------------------------------------------
# is it settled?
# ---------------------------------------------------------------------------

def test_a_first_pass_is_never_settled():
    assert cdc.regime_settled(None, {"kind": "S3", "ed_allowable_pct": 20.0}) \
        is False


def test_the_ratio_has_to_stop_moving_too():
    a = {"kind": "S3", "ed_allowable_pct": 20.0}
    assert cdc.regime_settled(a, {"kind": "S3", "ed_allowable_pct": 20.9})
    assert not cdc.regime_settled(a, {"kind": "S3", "ed_allowable_pct": 21.5})
    # an S2 is judged on its pull length, in per cent of itself
    b = {"kind": "S2", "t_on_allowable_s": 20.0}
    assert cdc.regime_settled(b, {"kind": "S2", "t_on_allowable_s": 20.1})
    assert not cdc.regime_settled(b, {"kind": "S2", "t_on_allowable_s": 22.0})
    # a kind that changed under the loop is not a comparison at all
    assert not cdc.regime_settled(a, b)


def test_the_progress_line_says_what_the_pass_found():
    words = cdc.progress_words(3, {"kind": "S3", "ed_allowable_pct": 21.6},
                               128.2, 131.4)
    assert words == "pass 3: ED 21.6 % allowable, coil 128 → 131 °C"
    assert "on-time 19.8 s allowable" in cdc.progress_words(
        1, {"kind": "S2", "t_on_allowable_s": 19.8}, None, None)


# ---------------------------------------------------------------------------
# (b) THE LOOP — the search wired into POST /api/coupled/run
# ---------------------------------------------------------------------------
# Both solvers faked, and the cycle step faked with a MONOTONE regime: a hotter
# winding gives a lower allowable ED, and a lower ED gives a cooler cycle.  What
# is unproven elsewhere is the WIRING, and a real two-pass solve would prove it
# most slowly and least clearly.

from tests.test_coupled import COOLING as PANEL_COOLING, EM_BODY  # noqa: E402

def _model_of(regime):
    """A fake cycle model that carries its own regime, so the loop's final
    full-resolution re-solve has something to re-solve."""
    return types.SimpleNamespace(regime=regime), regime


LOOP_BODY = {**EM_BODY, "thermal_settings": PANEL_COOLING, "magnet_temp_c": 90.0,
             "mechanical": False}


@pytest.fixture
def faked_loop(monkeypatch):
    """``_em_run``, ``_thermal_solve`` and ``_cycle_step`` replaced by recorders.

    The thermal map hands back a winding at 400 °C — where this point WOULD end
    up if the pull never ended — so a loop that fed back the steady map instead
    of the cycle would be visible immediately.
    """
    from motor_ai_sim.routes import coupled as cp

    seen = {"coil_in": [], "regimes": [], "final": 0, "filed": []}

    def _em(body, *, coil_temp_c, magnet_temp_c, **_k):
        seen["coil_in"].append(round(float(coil_temp_c), 4))
        return {"summary": {"P_loss_total_W": 700.0, "T_em_avg_Nm": 5.0,
                            "coil_temp_C": float(coil_temp_c), "rpm": 1000.0}}

    def _th(body, cooling, *, coil_temp_c, magnet_temp_c, rpm, **_k):
        return {"ok": True,
                "components": {"winding": {"avg": 400.0, "max": 430.0},
                               "magnet": {"avg": 380.0, "max": 395.0}}}

    def _regime(ed, coil):
        return {"kind": "S3", "duty": "peak", "cycle_s": 60.0,
                "ed_cycle_s": 60.0, "ed_requested_pct": 25.0,
                "ed_allowable_pct": round(ed, 4),
                "t_on_allowable_s": round(60.0 * ed / 100.0, 3),
                "limiting_part": "winding", "feasible": True,
                "fits_requested": bool(25.0 <= ed),
                "coil_temp_c": round(coil, 4), "magnet_temp_c": 90.0,
                "winding_hot_peak_c": 199.9, "magnet_peak_c": 90.0,
                "at_allowable": {"peak_c": {"winding": round(coil, 4),
                                            "magnet": 90.0}},
                "note": "the regime, as the fake sees it."}

    def _step(inputs, em_summary, field, *, with_curve=False,
              cycle_lengths=None, samples=None):
        coil_in = float(em_summary["coil_temp_C"])
        ed = 30.0 - 0.2 * (coil_in - 120.0)
        r = _regime(ed, 100.0 + ed)
        seen["regimes"].append(r)
        return types.SimpleNamespace(regime=r), r

    monkeypatch.setattr(cp, "_em_run", _em, raising=True)
    monkeypatch.setattr(cp, "_thermal_solve", _th, raising=True)
    monkeypatch.setattr(cp, "_cycle_step", _step, raising=True)
    monkeypatch.setattr(cp, "_attach_coupling", lambda em, block: False,
                        raising=True)
    monkeypatch.setattr(cp, "_remember_last", lambda out, **k: None,
                        raising=True)
    monkeypatch.setattr(
        cp, "_cycle_inputs",
        lambda body, cooling: {"duty": "peak", "die": "D", "config": "C",
                               "duties": [], "geometry": {}, "d_housing_m": 0.0,
                               "materials": {}, "part_states": None,
                               "block": dict(S3), "magnet_limit_c": None,
                               "magnet_limit_source": "", "rated_state_c": {},
                               "rated_duty": "", "rated_state_source": "",
                               "cooling": {}},
        raising=True)

    def _final(model, **kw):
        # the FINAL, full-resolution re-solve: the same search on the model the
        # last pass was fitted with, which the fake carries on the model itself
        seen["final"] += 1
        return dict(model.regime)

    monkeypatch.setattr(cdc, "solve_regime", _final, raising=True)
    monkeypatch.setattr(cdc, "cycle_record",
                        lambda model, regime, **kw: {"kind": "duty_cycle",
                                                     "cycle": {"converged": True}},
                        raising=True)
    from motor_ai_sim import duty_results as dr
    monkeypatch.setattr(
        dr, "note_duty_cycle",
        lambda rec, params, fp, at=None, **kw: seen["filed"].append((rec, kw)),
        raising=True)
    return seen


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    return TestClient(app)


def test_the_loop_closes_on_the_ratio_and_the_temperatures_together(
        client, faked_loop):
    """THE claim.  Pass 1 runs at the coil temperature the body states; every
    pass after that runs at the CYCLE's peak at the ratio the previous pass
    found, and the loop stops only when both have stopped moving."""
    r = client.post("/api/coupled/run",
                    json={**LOOP_BODY, "max_iter": 6, "tol_k": 1.0})
    assert r.status_code == 200, r.text[:600]
    c = r.json()["coupling"]

    # 120 -> ED 30 -> 130; the correction then flips sign, so the loop halves
    # its own step (the adaptive damping it has always had) and lands on 129.
    assert faked_loop["coil_in"] == [120.0, 130.0, 129.0]
    assert [round(x["ed_allowable_pct"], 2)
            for x in faked_loop["regimes"]] == [30.0, 28.0, 28.2]
    assert c["converged"] is True and c["iterations"] == 3
    # the pair REPORTED is the pair the last run was solved at…
    assert c["coil_temp_c"] == 129.0
    # …and the regime rides with it
    assert c["duty_cycle"]["ed_allowable_pct"] == 28.2
    assert c["duty_cycle"]["ed_requested_pct"] == 25.0
    assert c["duty_cycle"]["fits_requested"] is True
    # every pass's find is in the history the panel plots
    assert [row["ed_allowable_pct"] for row in c["history"]] == [30.0, 28.0, 28.2]


def test_the_temperature_fed_back_is_the_cycle_s_and_not_the_maps(
        client, faked_loop):
    """The steady map says 400 °C — the temperature this point would reach if it
    never ended.  Nothing in the answer may be that number."""
    r = client.post("/api/coupled/run",
                    json={**LOOP_BODY, "max_iter": 3, "tol_k": 1.0})
    c = r.json()["coupling"]
    assert 400.0 not in faked_loop["coil_in"]
    assert all(row["T_coil_out"] < 200.0 for row in c["history"])
    assert c["runaway"] is False               # …nor a runaway, which it is not


def test_temperatures_inside_the_band_are_not_enough(client, monkeypatch,
                                                     faked_loop):
    """A loop whose copper has stopped moving while the ratio it is being
    searched at still walks has not converged on anything (the ED band is one
    percentage point)."""
    from motor_ai_sim.routes import coupled as cp

    walk = {"n": 0}

    def _step(inputs, em_summary, field, **_k):
        walk["n"] += 1
        ed = 30.0 - 2.0 * walk["n"]            # 28, 26, 24 … never settles
        return _model_of({"kind": "S3", "ed_allowable_pct": ed,
                          "ed_requested_pct": 25.0, "ed_cycle_s": 60.0,
                          "feasible": True, "fits_requested": True,
                          "limiting_part": "winding",
                          "coil_temp_c": 125.0, "magnet_temp_c": 90.0,
                          "winding_hot_peak_c": 180.0, "magnet_peak_c": 90.0,
                          "at_allowable": {"peak_c": {"winding": 125.0,
                                                      "magnet": 90.0}},
                          "note": "still walking."})

    monkeypatch.setattr(cp, "_cycle_step", _step, raising=True)
    r = client.post("/api/coupled/run",
                    json={**LOOP_BODY, "max_iter": 4, "tol_k": 50.0,
                          "coil_temp_c": 125.0})
    c = r.json()["coupling"]
    assert c["converged"] is False and c["iterations"] == 4
    assert c["residual_coil_K"] == 0.0         # the temperatures never moved


def test_a_ratio_that_does_not_fit_is_an_answer_with_a_warning(
        client, monkeypatch, faked_loop):
    from motor_ai_sim.routes import coupled as cp

    def _step(inputs, em_summary, field, **_k):
        return _model_of({"kind": "S3", "ed_allowable_pct": 21.6,
                          "ed_requested_pct": 25.0, "ed_cycle_s": 60.0,
                          "feasible": True, "fits_requested": False,
                          "limiting_part": "winding", "coil_temp_c": 131.0,
                          "magnet_temp_c": 90.0, "winding_hot_peak_c": 199.8,
                          "magnet_peak_c": 111.0,
                          "at_allowable": {"peak_c": {"winding": 131.0,
                                                      "magnet": 111.0}},
                          "note": "21.6 % of a 60 s cycle is allowable; the "
                                  "25 % asked for does NOT fit under it."})

    monkeypatch.setattr(cp, "_cycle_step", _step, raising=True)
    r = client.post("/api/coupled/run",
                    json={**LOOP_BODY, "max_iter": 3, "tol_k": 1.0,
                          "coil_temp_c": 131.0})
    assert r.status_code == 200
    c = r.json()["coupling"]
    assert c["converged"] is True              # it SOLVED; it just does not fit
    assert c["warning_code"] == "duty_cycle_requested_over_allowable"
    assert "does NOT fit" in c["warning"]
    assert c["duty_cycle"]["fits_requested"] is False


def test_a_point_with_no_feasible_ratio_is_never_reported_as_settled(
        client, monkeypatch, faked_loop):
    from motor_ai_sim.routes import coupled as cp

    def _step(inputs, em_summary, field, **_k):
        return _model_of({"kind": "S3", "ed_allowable_pct": 0.0,
                          "ed_requested_pct": 25.0, "ed_cycle_s": 60.0,
                          "feasible": False, "fits_requested": False,
                          "limiting_part": "winding", "coil_temp_c": 180.0,
                          "magnet_temp_c": 90.0, "winding_hot_peak_c": 200.0,
                          "magnet_peak_c": 90.0,
                          "s2_time_to_limit_s": 19.8,
                          "at_allowable": {"peak_c": {"winding": 180.0,
                                                      "magnet": 90.0}},
                          "note": "no duty ratio is allowable at this operating "
                                  "point. One pull from 40 °C lasts 19.8 s."})

    monkeypatch.setattr(cp, "_cycle_step", _step, raising=True)
    r = client.post("/api/coupled/run",
                    json={**LOOP_BODY, "max_iter": 3, "tol_k": 1.0,
                          "coil_temp_c": 180.0})
    c = r.json()["coupling"]
    assert c["warning_code"] == "duty_cycle_no_allowable_ed"
    assert "no duty ratio is allowable" in c["warning"]
    assert c["duty_cycle"]["s2_time_to_limit_s"] == 19.8


def test_the_cycle_is_filed_under_the_duty_it_describes(client, faked_loop):
    client.post("/api/coupled/run",
                json={**LOOP_BODY, "max_iter": 2, "tol_k": 1.0})
    assert faked_loop["final"] == 1            # ONE full-resolution re-solve
    assert faked_loop["filed"], "no duty_cycle record was filed"
    rec, kw = faked_loop["filed"][-1]
    assert rec["kind"] == "duty_cycle"
    assert (kw["die"], kw["cfg"], kw["duty"]) == ("D", "C", "peak")


def test_an_s1_machine_is_untouched(client, monkeypatch, faked_loop):
    """THE regression.  With no cycle there is no regime, no `duty_cycle` block
    and no record — and the temperature fed back is the thermal map's own
    average, exactly as it has always been."""
    from motor_ai_sim.routes import coupled as cp

    monkeypatch.setattr(cp, "_cycle_inputs", lambda body, cooling: None,
                        raising=True)
    r = client.post("/api/coupled/run",
                    json={**LOOP_BODY, "max_iter": 2, "tol_k": 1.0})
    c = r.json()["coupling"]
    assert "duty_cycle" not in c
    assert faked_loop["coil_in"] == [120.0, 400.0]     # the map's winding avg
    assert faked_loop["final"] == 0 and not faked_loop["filed"]
    assert all("ed_allowable_pct" not in row for row in c["history"])


# ---------------------------------------------------------------------------
# (c) THE RECORD — it prints through the report's existing section
# ---------------------------------------------------------------------------

def test_the_record_is_the_shape_the_report_already_reads(s3):
    from motor_ai_sim.duty_results import compact_duty_cycle
    import motor_ai_sim.report as R

    m, r = s3
    doc = cdc.cycle_record(m, r)
    assert set(doc) >= {"spec", "network", "cycle", "split", "limits"}
    # the cycle that is STORED is the one at the allowable ratio, integrated for
    # real (the report's figures are a real cycle, never an extrapolated one)
    assert doc["spec"]["ed_pct"] == pytest.approx(r["ed_allowable_pct"])
    assert doc["spec"]["ed_given"] is False and doc["spec"]["found"] is True
    assert doc["cycle"]["converged"] is True
    assert doc["cycle"]["winding_hot_peak_c"] == pytest.approx(
        r["winding_hot_peak_c"], abs=1.0)
    assert doc["limits"]["ed_found"] is True
    assert doc["limits"]["ed_requested_pct"] == 25.0

    col = {"res": {"duty_cycle": compact_duty_cycle(doc, {"rpm": 1000.0},
                                                    "fp", "now")}}
    rec = R.duty_cycle_record(col)
    assert rec is not None                      # the section would print it
    line = R.duty_cycle_regime_text(rec)
    assert line.startswith("Allowable regime: S3, 60 s cycle")
    assert "limited by the winding at 200 °C" in line
    assert "one pull S2" in line
    rows = {row[0] for row in R.duty_cycle_rows(col, {})}
    assert "ED allowable / requested" in rows
    assert "At the allowable ED" in rows
    assert "S2 time to the limit" in rows


def test_the_span_of_periods_is_opt_in(areas):
    """It is the one expensive thing in the module (minutes on this machine), so
    the loop does not draw it unless a caller names the periods."""
    m = _model(dict(S3), areas)
    assert cdc.solve_regime(m, with_curve=True).get("ed_vs_cycle") in (None, [])
    r = cdc.solve_regime(m, with_curve=True, cycle_lengths=[60.0, 120.0])
    assert [row["cycle_s"] for row in r["ed_vs_cycle"]] == [60.0, 120.0]
    # a longer period is a lower allowable ratio — the machine has to be in
    # balance over it rather than riding its own heat capacity
    assert r["ed_vs_cycle"][1]["ed_allowable_pct"] \
        <= r["ed_vs_cycle"][0]["ed_allowable_pct"]


def test_the_warm_start_does_not_move_the_answer(areas):
    """The accelerator the coupled search runs on: seeding each cycle map from
    the neighbouring ED's converged state is a shorter path to the same fixed
    point, not a different one."""
    m = _model(dict(S3), areas)
    cold = tdc.allowable_ed(m.profile, m.network, m.caps, limits=m.limits,
                            with_curve=False, samples_per_segment=24)
    warm = tdc.allowable_ed(m.profile, m.network, m.caps, limits=m.limits,
                            with_curve=False, samples_per_segment=24,
                            warm_start=True)
    assert warm["ed_allowable_pct"] == pytest.approx(
        cold["ed_allowable_pct"], abs=0.05)
    assert warm["at_allowable"]["winding_hot_peak_c"] == pytest.approx(
        cold["at_allowable"]["winding_hot_peak_c"], abs=0.5)
