"""The coupled loop on the INVERTER — ``/api/coupled/run`` with ``drive: "pwm"``.

WHY THIS EXISTS (PWM study 2026-09-13 §2.9, the study's own headline
consequence).  Every thermal map, heat budget, bearing temperature and warning
in this project was computed on SINUSOIDAL losses, and the measured carrier cost
on the Ø200 L155 peak duty is **+2 196 W at 24 kHz with 96 % of it in the
stator** — copper +1 367 W, teeth and yoke +884 W, magnets/shaft/sleeve under a
watt (the 3.2 mm gap, the 2.5 mm sleeve and the 5 mm axial magnet slices screen
the carrier before it reaches the rotor).  The stator is being fed about 30 %
less heat than the inverter actually puts into it, and the accepted operating
point — Br, torque, efficiency, the demagnetisation margin — grew out of the
winding and magnet temperatures that map produced.

So the loop learns a second drive, and this file pins what that must mean:

  (a) OFF IS TODAY.  A body with no ``drive`` (or ``"sine"``) makes the
      electromagnetic call it always made — no ``v_bus``, no ``f_switch``, no
      environment switch — and its record says ``drive: "sine"``.
  (b) ON IS THE ROUTE'S OWN PWM PATH.  ``drive: "pwm"`` sends
      ``drive="pwm_voltage"`` with the bus, the carrier and the fundamental to
      ``get_fem_transient`` — the same function the Run button calls — so the
      star-equivalent substitution, the modulation gate and the settled DC
      anchor all come from the one implementation and not from a copy here.
  (c) THE DEFAULTS ARE NAMED.  The carrier is the duty's ``sim.fSwitch``, the
      bus is the pack's ``v_nom``, the frame count is the study's 20 samples per
      carrier, and the fundamental is the duty's own ``V1_seed_peak_V``.  Each
      is recorded with WHERE it came from.
  (d) AN UNSETTLED RUN IS REFUSED BY NAME.  ``pwm_dc_unconverged`` is a 422 with
      its own ``error_code`` — B5 measured 43-45 A of spurious DC reporting 55 %
      torque ripple where the settled orbit reads 25 %, and a coupled run's
      whole product is a state the report quotes.
  (e) THE THERMAL HALF SOLVES ON THE PWM MAP.  Not on a sine map, and not on a
      "closest run of this machine": the map is handed over through
      ``solve_thermal_field(_em_map=…)`` after an EXACT-key snapshot probe of
      the run just made, and the probe's key is spelled the way the transient
      route spells it — pinned here against that route's own source.
  (f) ONE POINT KEEPS SEVERAL EXCITATIONS.  A PWM record keeps the sine record
      it replaced under ``reference_sine`` (one level deep, never nested), and a
      second carrier is filed beside the main record under ``alt_carriers``
      instead of over it — so a report prints sine / 24 kHz / 48 kHz at one
      point without re-solving anything.

NOTHING SOLVES HERE.  Both halves are stubs: this file is about the WIRING —
which arguments reach which solver, which map reaches the conduction solve, and
what the records keep.  The real loop is exercised once, on a cheap machine, by
the smoke run described in the session notes; ``tests/test_coupled.py`` owns the
sinusoidal end-to-end.
"""
from __future__ import annotations

import inspect
import json
import os

import pytest


# ---------------------------------------------------------------------------
# A loop with both solvers stubbed
# ---------------------------------------------------------------------------

def _fake_field(coil=95.0, magnet=110.0, shaft=70.0):
    """The shape ``routes.coupled`` reads off a thermal answer."""
    return {
        "components": {
            "winding": {"avg": coil, "max": coil + 12.0},
            "magnet": {"avg": magnet, "max": magnet + 8.0},
            "shaft": {"avg": shaft, "max": shaft + 3.0},
            "rotor": {"avg": magnet - 4.0, "max": magnet},
        },
        "T_max": coil + 12.0,
    }


def _fake_em(*, dc_unconverged=False, computed_at="2026-09-14T22:00:00"):
    return {
        "computed_at": computed_at,
        "geo_fingerprint": "fp-test",
        "I_phase_rms_solved_A": 430.1,
        "pwm": {"modulation_index": 0.88, "equivalent_star": True,
                "v_bus_model_V": 1299.7, "carriers_per_period": 14,
                "f_switch_eff_Hz": 24000.0,
                "steps_per_switching_period": 20.0,
                "modulator": "ideal two-level"},
        "summary": {
            "T_em_avg_Nm": 235.1, "T_ripple_pct": 25.2,
            "P_stranded_W": 6285.0, "P_core_W": 3227.0, "P_solid_W": 373.0,
            "P_loss_total_W": 9886.0, "efficiency": 0.9803,
            "THD_I_pct": 6.81, "THD_LL_pct": 41.5,
            "star_delta": "delta",
            "delta_equivalent_star": {"v_bus_real_V": 750.4},
            "pwm_dc_residual_A": -0.287, "pwm_dc_residual_phase": "B",
            "pwm_dc_tol_A": 0.5,
            "pwm_dc_unconverged": bool(dc_unconverged),
            "bearing_temp_c": 70.0, "bearing_temp_source": "machine",
            "P_bearings_W": 1000.0, "efficiency_shaft": 0.977,
        },
    }


@pytest.fixture
def loop(monkeypatch):
    """``routes.coupled`` with the EM run, the thermal solve, the snapshot probe
    and every store replaced — and a record of what each was handed."""
    from motor_ai_sim.routes import coupled as cp

    seen = {"em": [], "thermal": [], "probe": [], "record": [],
            "env": [], "alt": []}

    def _em(**kw):
        seen["em"].append(dict(kw))
        seen["env"].append(os.environ.get("SB_PWM_COARSE_SETTLE"))
        return _fake_em()

    # `_call_filtered` drops every key the target does not declare, and a stub
    # declares none — so it is a pass-through here.  The real filter is what
    # keeps the loop's body from reaching a solver signature it does not fit,
    # and it is not what this file is testing.
    import motor_ai_sim.modules.solvers as _solvers
    monkeypatch.setattr(_solvers, "_call_filtered",
                        lambda fn, payload: fn(**dict(payload or {})))

    # The EM half: intercepted at `get_fem_transient`, which is where the loop
    # reaches the electromagnetic solver — so the argument list asserted below
    # is the one the Run button's own function would receive.
    import motor_ai_sim.routes.simulation as sim
    monkeypatch.setattr(sim, "get_fem_transient", _em, raising=True)
    monkeypatch.setattr(sim, "_effective_rpm", lambda v=None: 20000.0)

    def _th(**kw):
        seen["thermal"].append(dict(kw))
        return _fake_field()
    import motor_ai_sim.routes.thermal as th
    monkeypatch.setattr(th, "solve_thermal_field", _th, raising=True)
    monkeypatch.setattr(th, "_remember_last", lambda *a, **k: None)
    monkeypatch.setattr(th, "_field_params", lambda **k: dict(k))

    def _probe(**kw):
        seen["probe"].append(dict(kw))
        return {"ok": True, "from_transient": True, "n_triangles": 3,
                "loss_density_per_tri": [1.0, 2.0, 3.0],
                "transient_computed_at": "2026-09-14T22:00:00",
                "vertices": [], "triangles": [], "domain_per_tri": [],
                "P_cu_exact_W": 6285.0}
    monkeypatch.setattr(sim, "get_fem_field2d", _probe, raising=True)

    # Everything the loop would otherwise write, and the two panels it reads.
    monkeypatch.setattr(cp, "_remember_last", lambda out, **k: (
        seen["record"].append((dict(out), k)) or None))
    monkeypatch.setattr(cp, "_attach_coupling", lambda em, blk: True)
    monkeypatch.setattr(cp, "_preflight", lambda body, **k: None)
    monkeypatch.setattr(cp, "_pole_pairs", lambda body: 5)
    monkeypatch.setattr(cp, "_pack_nominal_v", lambda: 750.4)
    monkeypatch.setattr(cp, "_effective_f_switch", lambda body: 24000.0)
    monkeypatch.setattr(cp, "_duty_summary",
                        lambda: {"V1_seed_peak_V": 572.1396,
                                 "V1_seed_delta_deg": 34.259})
    monkeypatch.setattr(cp, "_magnet_reference_temp_c", lambda: 150.0)
    monkeypatch.setattr(cp, "_bearing_temp", lambda f: (72.0, "shaft ends"))

    import motor_ai_sim.thermal_settings as ts
    monkeypatch.setattr(ts, "thermal_panel_settings", lambda a=None: {})
    monkeypatch.setattr(ts, "cooling_issue", lambda s: None)
    monkeypatch.setattr(ts, "cooling_fields",
                        lambda s: {"cooling_mode": "air", "ambient_temp": 30.0,
                                   "h_conv": 50.0})
    monkeypatch.setattr(ts, "coupled_iteration_settings",
                        lambda s: {"max_iter": 1})
    return cp, seen


BODY = {"n_steps_per_period": 36, "n_periods": 1.0, "gamma_deg": 15.0,
        "I_phase_rms": 433.559, "coil_temp_c": 134.7, "mode": "motor",
        "eddy": True, "rotor_eddy": True, "max_iter": 1,
        "mesh_size_mm": 4.0, "min_size_mm": 0.3, "n_sectors": 2}


# ---------------------------------------------------------------------------
# (a) off is today
# ---------------------------------------------------------------------------

def test_no_drive_is_the_sinusoid_it_has_always_been(loop):
    cp, seen = loop
    out = cp.run(dict(BODY))
    assert len(seen["em"]) == 1
    kw = seen["em"][0]
    for k in ("v_bus", "f_switch", "v_phase_peak", "v_delta_deg", "harm_ref"):
        assert k not in kw, f"a sine loop must not send {k} to the EM run"
    assert kw["drive"] == "current" or "drive" not in kw or kw["drive"] is None
    assert out["coupling"]["drive"] == "sine"
    assert "inverter" not in out["coupling"]
    # …and the thermal half looked its own map up, as it always did.
    assert "_em_map" not in seen["thermal"][0]
    assert seen["probe"] == []


@pytest.mark.parametrize("spelling", ["sine", "current", "SINE", ""])
def test_the_sine_spellings_all_mean_the_sinusoid(loop, spelling):
    cp, seen = loop
    out = cp.run({**BODY, "drive": spelling})
    assert out["coupling"]["drive"] == "sine"


def test_an_unknown_drive_is_a_422_that_says_what_is_offered(loop):
    from fastapi import HTTPException
    cp, _ = loop
    with pytest.raises(HTTPException) as e:
        cp.run({**BODY, "drive": "voltage"})
    assert e.value.status_code == 422
    assert e.value.detail["error_code"] == "unknown_drive"
    assert "pwm" in e.value.detail["error"]


# ---------------------------------------------------------------------------
# (b) + (c) the inverter, and where every default came from
# ---------------------------------------------------------------------------

def test_pwm_sends_the_routes_own_pwm_voltage_arguments(loop):
    cp, seen = loop
    out = cp.run({**BODY, "drive": "pwm"})
    kw = seen["em"][0]
    assert kw["drive"] == "pwm_voltage"
    assert kw["v_bus"] == pytest.approx(750.4)          # the pack's v_nom
    assert kw["f_switch"] == pytest.approx(24000.0)     # the duty's sim.fSwitch
    assert kw["v_phase_peak"] == pytest.approx(572.1396)
    assert kw["v_delta_deg"] == pytest.approx(34.259)
    # The resolution-matched sinusoidal reference is OFF by default: it is a
    # second full transient inside every iteration (53 min of the first L155
    # peak run's three hours) for a number the loop never reads — the report
    # compares against `reference_sine`, the duty's own settled sine answer.
    assert kw["harm_ref"] is False
    # THE RESOLUTION RULE: 20 FEM steps per carrier period.  f_el = 20000 rpm x
    # 5 pole pairs / 60 = 1 666.7 Hz, so 24 kHz is 14 carriers and 280 steps —
    # the study's own `gp24` resolution.
    assert kw["n_steps_per_period"] == 280
    inv = out["coupling"]["inverter"]
    assert inv["f_carrier_hz"] == 24000.0 and inv["v_dc_V"] == 750.4
    assert inv["sources"]["f_carrier_hz"].startswith("the duty")
    assert inv["sources"]["v_dc_V"].startswith("the machine")
    assert "20 FEM steps per carrier" in inv["sources"]["n_steps_per_period"]
    assert inv["sources"]["v_phase_peak_V"].startswith("the duty")


def test_the_request_outranks_every_default(loop):
    cp, seen = loop
    cp.run({**BODY, "drive": "pwm",
            "inverter": {"f_carrier_hz": 48000, "v_dc_V": 850.4,
                         "n_steps_per_period": 600, "schedule": "fine",
                         "v_phase_peak_V": 500.0, "v_delta_deg": 30.0}})
    kw = seen["em"][0]
    assert (kw["f_switch"], kw["v_bus"], kw["n_steps_per_period"]) == (
        48000.0, 850.4, 600)
    assert kw["v_phase_peak"] == 500.0 and kw["v_delta_deg"] == 30.0


def test_the_settle_schedule_is_set_around_the_run_and_put_back(loop,
                                                                monkeypatch):
    cp, seen = loop
    monkeypatch.setenv("SB_PWM_COARSE_SETTLE", "sentinel")
    cp.run({**BODY, "drive": "pwm"})
    assert seen["env"][0] == "1", "the mixed schedule is the default"
    assert os.environ["SB_PWM_COARSE_SETTLE"] == "sentinel", (
        "a coupled run must not leave the process configured for the next one")
    seen["env"].clear()
    cp.run({**BODY, "drive": "pwm", "inverter": {"schedule": "fine"}})
    assert seen["env"][0] == "0"


def test_an_unusable_inverter_is_refused_before_anything_solves(loop,
                                                                monkeypatch):
    from fastapi import HTTPException
    cp, seen = loop
    # No pack, no explicit bus: the loop refuses rather than inventing a link
    # (v_nom against v_max is worth 0.14 pp of efficiency on this very duty).
    monkeypatch.setattr(cp, "_pack_nominal_v", lambda: None)
    with pytest.raises(HTTPException) as e:
        cp.run({**BODY, "drive": "pwm"})
    assert e.value.detail["error_code"] == "pwm_incomplete_inverter"
    assert seen["em"] == [], "nothing may solve behind an impossible inverter"


def test_a_bad_schedule_and_a_bad_record_target_are_named(loop):
    from fastapi import HTTPException
    cp, _ = loop
    for blk, field in (({"schedule": "coarse"}, "inverter.schedule"),
                       ({"record_as": "headline"}, "inverter.record_as")):
        with pytest.raises(HTTPException) as e:
            cp.run({**BODY, "drive": "pwm", "inverter": blk})
        assert e.value.detail["error_code"] == "bad_inverter"
        assert e.value.detail["invalid_parameters"] == [{"field": field}]


def test_harm_ref_can_still_be_asked_for(loop):
    cp, seen = loop
    cp.run({**BODY, "drive": "pwm", "inverter": {"harm_ref": True}})
    assert seen["em"][0]["harm_ref"] is True


# ---------------------------------------------------------------------------
# THE POINT: a voltage-fed loop that stays where the duty is
# ---------------------------------------------------------------------------
# The first live L155 peak run went 442.8 -> 496.1 -> 500.8 A on a fixed
# fundamental as the magnets settled, and reported 280.5 N·m where the duty is
# 230.  An honest answer to a question nobody asked.

def _reg(v, i, pts, target=444.83, tol=1.0, seed=571.4778):
    from motor_ai_sim.routes import coupled as cp
    inv = {"v_phase_peak_V": v, "v_phase_peak_seed_V": seed,
           "target_I_phase_rms_A": target, "i_tol_pct": tol}
    return cp._regulate_v1(inv, i, pts)


def test_no_target_means_no_regulation(loop):
    from motor_ai_sim.routes import coupled as cp
    assert cp._regulate_v1({"v_phase_peak_V": 571.0,
                            "v_phase_peak_seed_V": 571.0}, 500.0, []) is None


def test_the_first_correction_is_damped_and_points_the_right_way():
    """One point cannot know dI/dV — V₁ is mostly back-EMF, so I is a small
    difference of two large numbers and a full proportional step overshoots by
    a factor of several.  Down when the current is too high, and by less than
    the proportional step would be."""
    pts = []
    out = _reg(571.4778, 500.84, pts)
    prop = 571.4778 * (444.83 / 500.84)
    assert out["v_phase_peak_V"] < 571.4778
    assert out["v_phase_peak_V"] > prop, "a full proportional step would overshoot"
    assert pts == [(571.4778, 500.84)]


def test_the_second_correction_is_an_exact_secant():
    """From two points the relation the machine actually has (V ≈ E + I·Z) is
    affine, so the secant is not an approximation — it is the answer."""
    pts = []
    _reg(600.0, 520.0, pts)
    out = _reg(580.0, 480.0, pts)
    # through (600, 520) and (580, 480): dV/dI = 0.5 V/A -> 580 + (444.83-480)*0.5
    assert out["v_phase_peak_V"] == pytest.approx(580.0 + (444.83 - 480.0) * 0.5)


def test_a_current_already_inside_the_band_is_left_alone():
    pts = []
    assert _reg(571.0, 444.83 * 1.005, pts) is None       # 0.5 % < 1 %
    assert pts == [(571.0, 444.83 * 1.005)], "the point is still recorded"


def test_the_regulator_cannot_wander_off_the_machine():
    """A fundamental outside ±40 % of the seed is a regulator that has lost the
    machine, not one converging — clamped rather than solving an inverter
    nobody can build."""
    pts = []
    out = _reg(571.4778, 1.0, pts)                        # absurdly low current
    assert out["v_phase_peak_V"] <= 1.4 * 571.4778 + 1e-9


def test_the_loop_re_aims_between_passes_and_records_both(loop):
    cp, seen = loop
    out = cp.run({**BODY, "drive": "pwm", "max_iter": 2,
                  "inverter": {"target_I_phase_rms_A": 400.0}})
    h = out["coupling"]["history"]
    assert len(h) == 2
    # the stub always answers 430.1 A, which is 7.5 % high -> the second pass
    # must have been solved at a LOWER fundamental than the first
    assert h[0]["v_phase_peak_V"] == pytest.approx(572.1396)
    assert h[0]["v_phase_peak_next_V"] < h[0]["v_phase_peak_V"]
    assert h[1]["v_phase_peak_V"] == pytest.approx(h[0]["v_phase_peak_next_V"])
    assert seen["em"][1]["v_phase_peak"] == pytest.approx(h[1]["v_phase_peak_V"])


def test_the_record_says_what_the_run_was_aimed_at_and_where_it_landed(loop):
    cp, seen = loop
    out = cp.run({**BODY, "drive": "pwm", "max_iter": 1,
                  "inverter": {"target_I_phase_rms_A": 430.1}})
    inv = out["coupling"]["inverter"]
    assert inv["target_I_phase_rms_A"] == 430.1
    assert inv["point_error_pct"] == pytest.approx(0.0)
    assert inv["on_point"] is True
    # …and a run with no target grows none of those keys: "no target" and "on
    # target" must not read the same.
    plain = cp.run({**BODY, "drive": "pwm"})["coupling"]["inverter"]
    assert "target_I_phase_rms_A" not in plain and "on_point" not in plain


def test_an_off_point_run_says_so(loop):
    cp, _ = loop
    inv = cp.run({**BODY, "drive": "pwm", "max_iter": 1,
                  "inverter": {"target_I_phase_rms_A": 400.0}}
                 )["coupling"]["inverter"]
    assert inv["point_error_pct"] == pytest.approx(7.525, abs=0.01)
    assert inv["on_point"] is False


def test_a_bad_target_is_refused_by_name(loop):
    from fastapi import HTTPException
    cp, seen = loop
    for bad in ("nonsense", -5):
        with pytest.raises(HTTPException) as e:
            cp.run({**BODY, "drive": "pwm",
                    "inverter": {"target_I_phase_rms_A": bad}})
        assert e.value.detail["error_code"] == "bad_inverter"
    assert seen["em"] == []


# ---------------------------------------------------------------------------
# (d) an unsettled run is refused BY NAME
# ---------------------------------------------------------------------------

def _dc_em(residual, seen):
    """A run that came back carrying `residual` amps of DC."""
    def _f(**kw):
        seen["em"].append(dict(kw))
        d = _fake_em(dc_unconverged=True)
        d["summary"]["pwm_dc_residual_A"] = residual
        return d
    return _f


def test_a_big_dc_still_refuses_with_its_own_error_code(loop, monkeypatch):
    """Several per cent of DC is not the machine's current at all."""
    from fastapi import HTTPException
    cp, seen = loop
    import motor_ai_sim.routes.simulation as sim
    monkeypatch.setattr(sim, "get_fem_transient", _dc_em(-45.0, seen),
                        raising=True)
    with pytest.raises(HTTPException) as e:
        cp.run({**BODY, "drive": "pwm",
                "inverter": {"target_I_phase_rms_A": 444.83}})
    assert e.value.status_code == 422
    assert e.value.detail["error_code"] == "pwm_dc_unconverged"
    assert "45" in e.value.detail["error"]
    assert seen["thermal"] == [], (
        "no temperature field may be built on a current that is not the machine's")


def _dc_em_from_pass(n, seen, residual=-175.114):
    """Settles for the first ``n - 1`` passes, then comes back carrying DC.

    The 2026-09-15 shape: pass 1 (and 2) land inside the band, and a later pass —
    re-aimed onto a new fundamental, at temperatures the earlier passes fed back
    — does not settle.
    """
    calls = {"n": 0}

    def _f(**kw):
        calls["n"] += 1
        seen["em"].append(dict(kw))
        bad = calls["n"] >= n
        d = _fake_em(dc_unconverged=bad)
        if bad:
            d["summary"]["pwm_dc_residual_A"] = residual
        return d
    return _f


def test_a_later_pass_that_will_not_settle_keeps_the_last_good_pass(loop, monkeypatch):
    """The 93 minutes this rule was written for (2026-09-15).

    A three-pass Ø200 run solved pass 1 and 2 inside the DC band and threw both
    away when pass 3 came back with -175 A, because this one branch raised where
    its neighbour thirty lines up breaks.  An EM run that REFUSES on a later pass
    has kept the previous pass since 2026-09-09; a pass that fails the DC gate is
    the same situation and is now answered the same way.
    """
    cp, seen = loop
    import motor_ai_sim.routes.simulation as sim
    # pass 2 is the one that will not settle.  (Not pass 3: this stub returns
    # the same temperatures every time, so the loop CONVERGES after pass 2 and a
    # third pass would never be run — the test would then pass for the wrong
    # reason, which is how this assertion was caught.)
    monkeypatch.setattr(sim, "get_fem_transient", _dc_em_from_pass(2, seen),
                        raising=True)
    out = cp.run({**BODY, "drive": "pwm", "max_iter": 3,
                  "inverter": {"target_I_phase_rms_A": 444.83}})
    c = out["coupling"]
    # the run STANDS, on the last pass that settled
    assert len(c["history"]) == 1, "the one settled pass is the answer"
    assert c["iterations"] == 1 < 3, "it stopped before max_iter, by the break"
    assert len(seen["em"]) == 2, "pass 2 was solved, and then discarded"
    # (`converged` describes the TEMPERATURES, which this stub settles by
    # construction — it is not what the early stop is about, so it is not
    # asserted here.)
    # …and it says so, in prose and in a code a caller can branch on
    assert c["warning_code"] == "last_pass_dc_unconverged"
    assert "did not settle" in c["warning"]
    assert "175" in c["warning"]
    assert "last pass that settled" in c["warning"]


def test_the_first_pass_failing_the_gate_still_refuses(loop, monkeypatch):
    """Unchanged, and deliberately: nothing has been solved to keep, and
    catching it on pass 1 is what makes the refusal cost one transient
    instead of six."""
    from fastapi import HTTPException
    cp, seen = loop
    import motor_ai_sim.routes.simulation as sim
    monkeypatch.setattr(sim, "get_fem_transient", _dc_em_from_pass(1, seen),
                        raising=True)
    with pytest.raises(HTTPException) as e:
        cp.run({**BODY, "drive": "pwm", "max_iter": 3,
                "inverter": {"target_I_phase_rms_A": 444.83}})
    assert e.value.detail["error_code"] == "pwm_dc_unconverged"
    assert seen["thermal"] == []


def test_a_settled_run_grows_no_warning_code(loop):
    """The code appears only when the loop actually stopped early."""
    cp, _ = loop
    c = cp.run({**BODY, "drive": "pwm", "max_iter": 2,
                "inverter": {"target_I_phase_rms_A": 430.1}})["coupling"]
    assert "warning_code" not in c


def test_a_small_dc_costs_the_RIPPLE_and_nothing_else(loop, monkeypatch):
    """The night this rule was written for (2026-09-15): the L155 peak run died
    after 87 minutes on −4.66 A of DC against a 445 A fundamental — 0.01 % of
    I², i.e. nothing a temperature field can feel.  The run must stand, and the
    record must say which half of it may be quoted."""
    cp, seen = loop
    import motor_ai_sim.routes.simulation as sim
    monkeypatch.setattr(sim, "get_fem_transient", _dc_em(-4.66, seen),
                        raising=True)
    out = cp.run({**BODY, "drive": "pwm", "max_iter": 1,
                  "inverter": {"target_I_phase_rms_A": 444.83}})
    c = out["coupling"]
    assert seen["thermal"], "the loop must go on and solve the map"
    inv = c["inverter"]
    assert inv["dc_unconverged"] is True
    assert inv["ripple_quotable"] is False
    assert inv["dc_residual_A"] == -4.66
    assert inv["dc_band_A"] == pytest.approx(8.897, abs=1e-3)  # 2 % of 444.83 A
    note = c["dc_notes"][0]
    assert "NOT quotable" in note and "temperatures stand" in note


def test_the_band_is_two_per_cent_of_the_point_with_a_floor(loop):
    from motor_ai_sim.routes import coupled as cp_mod
    s = {"I1_phase_rms_A": 430.0}
    assert cp_mod._pwm_dc_band_a({"target_I_phase_rms_A": 444.83}, s) == \
        pytest.approx(8.8966)
    # THE MEASURED CASE this rule exists for: 4.66 A on 444.83 A is 1.05 %, and
    # a band drawn at 1 % would have failed the very night it was written for.
    assert cp_mod._pwm_dc_band_a({"target_I_phase_rms_A": 444.83}, s) >= 4.66
    # no target: measured against what the run actually drew
    assert cp_mod._pwm_dc_band_a({}, s) == pytest.approx(8.60)
    # …and a tiny machine never gets a band tighter than the solver's own
    assert cp_mod._pwm_dc_band_a({"target_I_phase_rms_A": 10.0}, {}) == 0.5


def test_a_settled_run_says_its_ripple_IS_quotable(loop):
    """True is written explicitly: a report must be able to tell "settled" from
    "this record predates the flag"."""
    cp, _ = loop
    inv = cp.run({**BODY, "drive": "pwm"})["coupling"]["inverter"]
    assert inv["ripple_quotable"] is True
    assert inv["dc_unconverged"] is False
    assert "dc_notes" not in cp.run({**BODY, "drive": "pwm"})["coupling"]


# ---------------------------------------------------------------------------
# (e) the thermal half solves on the PWM map
# ---------------------------------------------------------------------------

def test_the_pwm_map_is_handed_to_the_thermal_solve_not_looked_up(loop):
    cp, seen = loop
    cp.run({**BODY, "drive": "pwm"})
    kw = seen["thermal"][0]
    assert "_em_map" in kw and kw["_em_map"]["P_cu_exact_W"] == 6285.0
    src = kw["_em_loss_source"]
    assert src["kind"] == "pwm_run"
    assert src["drive"] == "pwm"
    assert src["inverter"]["f_carrier_hz"] == 24000.0
    # The note must say the carrier's watts are IN the map, per element — that
    # sentence is the whole claim this feature makes.
    assert "per element" in src["note"] and "scaled from a total" in src["note"]
    # …and the thermal call is at the PWM run's own frame count.
    assert kw["n_steps_per_period"] == 280


def test_the_snapshot_probe_demands_an_exact_pwm_key(loop):
    cp, seen = loop
    cp.run({**BODY, "drive": "pwm"})
    p = seen["probe"][0]
    assert p["snapshot_only"] is True
    assert p["latest_run_field"] is False, (
        "a near miss is another operating point's watts — never served here")
    assert p["snap_drive"] == "pwm_voltage"
    assert p["snap_excitation"] == "750.4/24000"


def test_the_excitation_key_is_spelled_the_way_the_run_spells_it():
    """One format string, two places, and a loss map that goes missing when they
    drift.  Pinned against the transient route's own source."""
    from motor_ai_sim.routes import coupled as cp
    from motor_ai_sim.routes import simulation as sim

    src = inspect.getsource(sim)
    assert '"%g/%g" % (float(v_bus), float(f_switch))' in src, (
        "the transient route changed how it spells a PWM snapshot key — "
        "coupled._pwm_snap_excitation has to follow or every coupled PWM run "
        "loses its loss map")
    assert cp._pwm_snap_excitation(
        {"v_dc_V": 750.4, "f_carrier_hz": 24000.0}) == "750.4/24000"


def test_a_map_from_another_run_is_refused(loop, monkeypatch):
    from fastapi import HTTPException
    cp, seen = loop
    import motor_ai_sim.routes.simulation as sim
    monkeypatch.setattr(sim, "get_fem_field2d", lambda **kw: {
        "ok": True, "from_transient": True, "n_triangles": 3,
        "loss_density_per_tri": [1.0, 2.0, 3.0],
        "transient_computed_at": "2026-01-01T00:00:00"}, raising=True)
    with pytest.raises(HTTPException) as e:
        cp.run({**BODY, "drive": "pwm"})
    assert e.value.detail["error_code"] == "pwm_loss_map_mismatch"


def test_a_missing_map_is_re_solved_once_before_it_is_refused(loop, monkeypatch):
    """THE NIGHT OF 2026-09-15.  The peak run died in three seconds: the
    electromagnetic step was answered from the RUN LEDGER — a stored result for
    the identical key, which by its own design does not touch the field
    snapshot store — so there was no map to solve a temperature field on.  The
    answer to "nothing solved" is to solve, not to refuse."""
    cp, seen = loop
    import motor_ai_sim.routes.simulation as sim
    calls = {"n": 0}

    def _probe(**kw):
        calls["n"] += 1
        seen["probe"].append(dict(kw))
        if calls["n"] == 1:                      # the store answered, no field
            return {"ok": False, "no_snapshot": True, "reason": "nothing matched"}
        return {"ok": True, "from_transient": True, "n_triangles": 3,
                "loss_density_per_tri": [1.0, 2.0, 3.0],
                "transient_computed_at": "2026-09-14T22:00:00",
                "P_cu_exact_W": 6285.0}
    monkeypatch.setattr(sim, "get_fem_field2d", _probe, raising=True)
    out = cp.run({**BODY, "drive": "pwm", "max_iter": 1})
    assert out["coupling"]["drive"] == "pwm"
    assert len(seen["em"]) == 2, "the pass must be solved again"
    assert seen["em"][0].get("fresh") is False
    assert seen["em"][1]["fresh"] is True, (
        "the re-solve must bypass every store, not ask the same one twice")
    assert seen["thermal"], "and the loop must go on to the conduction solve"


def test_a_map_still_missing_after_a_fresh_solve_is_refused(loop, monkeypatch):
    from fastapi import HTTPException
    cp, seen = loop
    import motor_ai_sim.routes.simulation as sim
    monkeypatch.setattr(sim, "get_fem_field2d", lambda **kw: {
        "ok": False, "no_snapshot": True, "reason": "nothing matched"},
        raising=True)
    with pytest.raises(HTTPException) as e:
        cp.run({**BODY, "drive": "pwm"})
    assert e.value.detail["error_code"] == "pwm_no_loss_map"
    assert "fresh solve left none either" in e.value.detail["error"]
    assert len(seen["em"]) == 2, "exactly one retry, not a loop of them"


def test_a_coupled_iteration_never_comes_from_a_STORE(loop):
    """The three doors into `get_fem_transient` that answer without solving —
    `restore`, the ledger, and the ledger probe — are all shut.  The ledger was
    the one this list missed, and it cost a night: it lives on disk, so even a
    backend restart does not clear it."""
    cp, seen = loop
    cp.run({**BODY, "drive": "pwm"})
    cp.run(dict(BODY))
    for kw in seen["em"]:
        assert kw["restore"] is False
        assert kw["ledger"] is False, "a coupled iteration must SOLVE"
        assert kw["ledger_probe"] is False


def test_the_field_view_loads_the_persisted_snapshot_before_it_gives_up():
    """An empty in-memory store is not proof nothing was solved — it is what a
    backend restart looks like.  `routes.thermal` has applied that rule since it
    was written; this view did not."""
    import motor_ai_sim.routes.simulation as sim
    src = inspect.getsource(sim._fem_field2d_impl)
    head = src[:src.index("_snap = _transient_field_snap.get(")]
    assert "if not _transient_field_snap:" in head
    assert "_load_last_transient_field_snapshot()" in head


# ---------------------------------------------------------------------------
# the record the loop leaves
# ---------------------------------------------------------------------------

def test_the_block_carries_the_inverter_the_report_prints(loop):
    cp, seen = loop
    out = cp.run({**BODY, "drive": "pwm"})
    c = out["coupling"]
    assert c["drive"] == "pwm"
    inv = c["inverter"]
    for k in ("f_carrier_hz", "v_dc_V", "m", "equivalent_star",
              "dc_residual_A", "ripple_pct", "thd_i_pct"):
        assert k in inv, f"the record must carry {k}"
    assert inv["m"] == 0.88 and inv["equivalent_star"] is True
    assert inv["dc_residual_A"] == -0.287 and inv["ripple_pct"] == 25.2
    assert inv["thd_i_pct"] == 6.81
    # the electromagnetic face a sine → PWM table compares
    assert c["em"]["P_stranded_W"] == 6285.0
    assert c["em"]["T_em_avg_Nm"] == 235.1
    # a voltage-fed run answers with a current — the history says which
    assert c["history"][0]["I_phase_rms_solved_A"] == 430.1


def test_the_alt_carrier_flag_reaches_the_per_duty_store(loop):
    cp, seen = loop
    cp.run({**BODY, "drive": "pwm"})
    assert seen["record"][-1][1] == {"alt_carrier": False}
    cp.run({**BODY, "drive": "pwm",
            "inverter": {"f_carrier_hz": 48000, "n_steps_per_period": 600,
                         "record_as": "alt_carrier"}})
    assert seen["record"][-1][1] == {"alt_carrier": True}


# ---------------------------------------------------------------------------
# (f) one point, several excitations — the store
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path, monkeypatch):
    from motor_ai_sim import duty_results as dr
    monkeypatch.setattr(dr, "store_path", lambda: tmp_path / ".duty_results.json")
    return dr


SINE = {"drive": "sine", "coil_temp_c": 134.7, "magnet_temp_c": 158.8,
        "efficiency_shaft": 0.982,
        "em": {"P_stranded_W": 5056.0, "P_loss_total_W": 7689.0,
               "T_em_avg_Nm": 236.66, "efficiency": 0.9847}}


def _pwm(f=24000.0, cu=6285.0):
    return {"drive": "pwm", "coil_temp_c": 160.2, "magnet_temp_c": 171.0,
            "efficiency_shaft": 0.975,
            "inverter": {"f_carrier_hz": f, "v_dc_V": 750.4, "m": 0.88,
                         "dc_residual_A": -0.287, "ripple_pct": 25.2,
                         "thd_i_pct": 6.81, "equivalent_star": True},
            "em": {"P_stranded_W": cu, "P_loss_total_W": 9886.0,
                   "T_em_avg_Nm": 235.1, "efficiency": 0.9803}}


def test_a_pwm_record_keeps_the_sine_it_replaced(store):
    dr = store
    assert dr.record("D", "C", "peak", "coupled", dict(SINE))
    assert dr.record("D", "C", "peak", "coupled", _pwm())
    e = dr.get("D", "C")["peak"]["coupled"]
    assert e["drive"] == "pwm"
    ref = e["reference_sine"]
    assert ref["drive"] == "sine"
    assert ref["em"]["P_stranded_W"] == 5056.0
    assert ref["coil_temp_c"] == 134.7
    # sine -> PWM at ONE point, without re-solving either:
    assert e["em"]["P_stranded_W"] - ref["em"]["P_stranded_W"] == 1229.0


def test_the_reference_never_nests(store):
    dr = store
    dr.record("D", "C", "peak", "coupled", dict(SINE))
    dr.record("D", "C", "peak", "coupled", _pwm())
    dr.record("D", "C", "peak", "coupled", _pwm(cu=6300.0))
    e = dr.get("D", "C")["peak"]["coupled"]
    assert e["em"]["P_stranded_W"] == 6300.0
    assert e["reference_sine"]["em"]["P_stranded_W"] == 5056.0
    assert "reference_sine" not in e["reference_sine"]


def test_a_sine_record_is_a_fresh_start(store):
    """A sine answer is the duty's primary one; it does not carry a reference to
    itself, and a re-measured sinusoid replaces the record outright."""
    dr = store
    dr.record("D", "C", "peak", "coupled", dict(SINE))
    dr.record("D", "C", "peak", "coupled", _pwm())
    dr.record("D", "C", "peak", "coupled", dict(SINE))
    e = dr.get("D", "C")["peak"]["coupled"]
    assert e["drive"] == "sine" and "reference_sine" not in e


def test_a_record_written_before_the_inverter_reads_as_a_sinusoid(store):
    dr = store
    dr.record("D", "C", "peak", "coupled", {"coil_temp_c": 100.0})
    dr.record("D", "C", "peak", "coupled", _pwm())
    ref = dr.get("D", "C")["peak"]["coupled"]["reference_sine"]
    assert ref["coil_temp_c"] == 100.0


def test_a_second_carrier_lands_beside_the_main_record(store):
    dr = store
    dr.record("D", "C", "peak", "coupled", dict(SINE))
    dr.record("D", "C", "peak", "coupled", _pwm(24000.0))
    assert dr.record_alt_carrier("D", "C", "peak", "coupled",
                                 _pwm(48000.0, cu=5654.0))
    e = dr.get("D", "C")["peak"]["coupled"]
    # the duty's own carrier is still the headline
    assert e["inverter"]["f_carrier_hz"] == 24000.0
    assert e["em"]["P_stranded_W"] == 6285.0
    alts = e["alt_carriers"]
    assert len(alts) == 1 and alts[0]["inverter"]["f_carrier_hz"] == 48000.0
    assert alts[0]["em"]["P_stranded_W"] == 5654.0
    # …and the sine reference is still there, so the report has all three
    assert e["reference_sine"]["em"]["P_stranded_W"] == 5056.0


def test_re_measuring_one_carrier_replaces_only_that_one(store):
    dr = store
    dr.record("D", "C", "peak", "coupled", _pwm(24000.0))
    dr.record_alt_carrier("D", "C", "peak", "coupled", _pwm(48000.0, cu=5654.0))
    dr.record_alt_carrier("D", "C", "peak", "coupled", _pwm(48000.0, cu=5600.0))
    alts = dr.get("D", "C")["peak"]["coupled"]["alt_carriers"]
    assert len(alts) == 1 and alts[0]["em"]["P_stranded_W"] == 5600.0


def test_a_new_main_record_keeps_the_other_carriers(store):
    dr = store
    dr.record("D", "C", "peak", "coupled", _pwm(24000.0))
    dr.record_alt_carrier("D", "C", "peak", "coupled", _pwm(48000.0, cu=5654.0))
    dr.record("D", "C", "peak", "coupled", _pwm(24000.0, cu=6290.0))
    e = dr.get("D", "C")["peak"]["coupled"]
    assert e["em"]["P_stranded_W"] == 6290.0
    assert [a["inverter"]["f_carrier_hz"] for a in e["alt_carriers"]] == [48000.0]


def test_an_alternative_to_nothing_is_not_written(store):
    dr = store
    assert dr.record_alt_carrier("D", "C", "peak", "coupled",
                                 _pwm(48000.0)) is False
    assert dr.get("D", "C") == {}


def test_an_alternative_without_a_carrier_is_refused(store):
    dr = store
    dr.record("D", "C", "peak", "coupled", _pwm())
    assert dr.record_alt_carrier("D", "C", "peak", "coupled",
                                 dict(SINE)) is False


def test_the_thermal_record_says_which_excitation_heated_it(store):
    """The temperature column must be able to name its own drive without the
    reader having to go and find the coupled record beside it."""
    dr = store
    rec = dr.compact_thermal(
        {"components": {"winding": {"avg": 160.0}},
         "loss_source": {"kind": "pwm_run", "drive": "pwm",
                         "inverter": {"f_carrier_hz": 24000.0}}},
        {"rpm": 20000.0}, "fp", "2026-09-14T22:00:00")
    assert rec["drive"] == "pwm"
    assert rec["inverter"]["f_carrier_hz"] == 24000.0
    plain = dr.compact_thermal({"components": {}}, {}, "fp", None)
    assert plain["drive"] == "sine" and "inverter" not in plain


def test_compact_coupled_carries_the_two_blocks_through(store):
    dr = store
    out = {"computed_at": "x", "coupling": _pwm()}
    c = dr.compact_coupled(out)
    assert c["drive"] == "pwm"
    assert c["inverter"]["f_carrier_hz"] == 24000.0
    assert c["em"]["P_stranded_W"] == 6285.0
    # and a plain sine coupling still reads exactly as it did
    assert dr.compact_coupled({"coupling": {"iterations": 2}})["drive"] == "sine"


# ---------------------------------------------------------------------------
# the field view's new probe fields
# ---------------------------------------------------------------------------

def test_the_field_views_cache_key_is_unchanged_for_the_sinusoid():
    """Every key already in the cache — and every caller that never heard of a
    carrier — must keep the key it has always had."""
    from motor_ai_sim.routes import simulation as sim
    base = sim._field2d_cache_key(gamma_deg=0.0, n_steps_per_period=4)
    assert sim._field2d_cache_key(gamma_deg=0.0, n_steps_per_period=4,
                                  snap_drive="current",
                                  snap_excitation="") == base
    pwm = sim._field2d_cache_key(gamma_deg=0.0, n_steps_per_period=4,
                                 snap_drive="pwm_voltage",
                                 snap_excitation="750.4/24000")
    assert pwm != base and pwm[:len(base)] == base


def test_the_probe_fields_reach_the_snapshot_key():
    """The two parameters exist for one reason: the exact key must be able to
    name a PWM run.  A signature that quietly dropped them would send every
    coupled PWM iteration to the relaxed same-machine fallback."""
    from motor_ai_sim.routes import simulation as sim
    for fn in (sim.get_fem_field2d, sim._fem_field2d_impl):
        p = inspect.signature(fn).parameters
        assert "snap_drive" in p and "snap_excitation" in p
        assert p["snap_drive"].default == "current"
        assert p["snap_excitation"].default == ""
    src = inspect.getsource(sim._fem_field2d_impl)
    assert 'drive=str(snap_drive or "current")' in src
    assert 'excitation=str(snap_excitation or "")' in src
