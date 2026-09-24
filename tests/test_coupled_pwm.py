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
  (c) THE DEFAULTS ARE NAMED.  The carrier is the CONTROLLER's (since
      2026-09-24; the retired ``sim.fSwitch`` is only its migration tier), the
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
    # 2026-09-24: the carrier and the bus are the CONTROLLER's
    # (``inverter.drive_source``); stood in for here so the wiring under test
    # never reads the catalog of whichever machine the server has loaded.  The
    # bus still goes through ``_pack_nominal_v`` so the "no pack" refusal below
    # keeps its meaning.
    monkeypatch.setattr(cp, "_drive_carrier", lambda body, default=True: {
        "hz": 24000.0, "origin": "controller",
        "source": "the Controller settings (carrier)"})

    def _vdc(body):
        v = cp._pack_nominal_v()
        return {"V": v, "origin": "controller" if v else None,
                "source": "the machine's battery v_nom" if v else None}
    monkeypatch.setattr(cp, "_drive_v_dc", _vdc)
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
        "mesh_size_mm": 4.0, "min_size_mm": 0.3, "n_sectors": 2,
        # THE 20 °C CATALOGUE PASS IS OFF (owner 2026-09-18): every assertion
        # in this file counts what the BRIDGE was asked for, pass by pass, and
        # the cold pass is one more electromagnetic run at the end of the loop.
        # tests/test_coupled_cold_constants.py is where it is switched on.
        "cold_constants": False}


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
    assert kw["f_switch"] == pytest.approx(24000.0)     # the Controller's carrier
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
    assert inv["sources"]["f_carrier_hz"].startswith("the Controller")
    assert inv["carrier_origin"] == "controller"
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


def test_two_passes_at_the_same_fundamental_still_produce_a_correction():
    """The secant needs two different VOLTAGES, not merely two passes.

    2026-09-15, CIANO10 200 opt / L155 'rated 1x9 mm': pass 1 landed inside the
    band so nothing was nudged, pass 2 therefore ran at the SAME V₁ — and the
    secant through (V, I₁) and (V, I₂) has zero run, which collapsed to "leave it
    where it is" and was reported as "next V1: None".  With one usable direction
    only the damped proportional step is the answer.
    """
    pts = []
    assert _reg(571.0, 444.83 * 1.002, pts) is None       # in band: not nudged
    out = _reg(571.0, 430.0, pts)                         # …so pass 2 ran at 571
    assert out is not None, "a zero-run secant used to report no correction"
    assert out["v_phase_peak_V"] > 571.0, "3 % LOW asks for more fundamental"


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
# …and the POINT is part of convergence, not a number beside it
# ---------------------------------------------------------------------------
# 2026-09-15, CIANO10 200 opt / L155 'rated 1x9 mm' at max_iter 3: pass 1 landed
# at 325.06 A (+0.17 %, in band, nothing nudged), pass 2 drifted to 314.25 A
# (-3.16 %) as the winding heated — and the loop STOPPED there, because the three
# temperature residuals were inside tol (coil 117.6 -> 116.0, magnet 133.0 ->
# 132.3, bearing 122.9 -> 122.4).  The regulator never got the pass it needed and
# the record said `on_point: false`.  Temperatures settling is NECESSARY; on a
# voltage-fed loop it is not sufficient.

def _drifting_em(seen, *, seed=572.1396, first=325.5, drift=314.7, aimed=325.4):
    """The measured shape: pass 1 inside the band, pass 2 drifting off it at the
    same fundamental, and any pass at a RE-AIMED fundamental back on the point."""
    def _f(**kw):
        seen["em"].append(dict(kw))
        d = _fake_em()
        v = float(kw["v_phase_peak"])
        if abs(v - seed) > 1e-6:
            d["I_phase_rms_solved_A"] = aimed
        else:
            d["I_phase_rms_solved_A"] = first if len(seen["em"]) == 1 else drift
        return d
    return _f


def test_settled_temperatures_off_point_earn_another_pass(loop, monkeypatch):
    cp, seen = loop
    import motor_ai_sim.routes.simulation as sim
    monkeypatch.setattr(sim, "get_fem_transient", _drifting_em(seen),
                        raising=True)
    c = cp.run({**BODY, "drive": "pwm", "max_iter": 3,
                "inverter": {"target_I_phase_rms_A": 325.0}})["coupling"]
    h = c["history"]
    assert len(h) == 3, "a loop that settled off point is not finished"
    # pass 2: the temperatures have stopped moving…
    assert h[1]["T_coil_in"] == h[1]["T_coil_out"]
    assert h[1]["T_magnet_in"] == h[1]["T_magnet_out"]
    # …and the current has drifted 3 % off the duty's
    assert h[1]["point_error_pct"] == pytest.approx(-3.169, abs=0.01)
    # pass 1 was inside the band, so it was NOT nudged — which is exactly the
    # case whose secant has no run
    assert "v_phase_peak_next_V" not in h[0]
    assert h[1]["v_phase_peak_V"] == pytest.approx(h[0]["v_phase_peak_V"])
    assert h[1]["v_phase_peak_next_V"] > h[1]["v_phase_peak_V"]
    # …and the third pass really ran at the re-aimed fundamental, and landed
    assert h[2]["v_phase_peak_V"] == pytest.approx(h[1]["v_phase_peak_next_V"])
    assert seen["em"][2]["v_phase_peak"] == pytest.approx(h[2]["v_phase_peak_V"])
    assert abs(h[2]["point_error_pct"]) <= 1.0
    assert c["converged"] is True and "warning_code" not in c
    assert c["inverter"]["on_point"] is True


def test_a_point_that_never_arrives_keeps_the_last_pass_and_says_so(loop):
    """Out of budget with the point still off: the last pass IS a solved state
    and is kept — temperatures, map and record all belong to it — but the run
    says so with its own code, like every other 'stopped early' here."""
    cp, seen = loop
    c = cp.run({**BODY, "drive": "pwm", "max_iter": 2,
                "inverter": {"target_I_phase_rms_A": 430.1 / 1.03}})["coupling"]
    assert len(c["history"]) == 2 and len(seen["em"]) == 2
    assert c["converged"] is False
    assert c["warning_code"] == "point_not_converged"
    assert "+3.00" in c["warning"] and "max_iter" in c["warning"]
    assert c["coil_temp_c"] == pytest.approx(95.0), "the last pass is kept"
    inv = c["inverter"]
    assert inv["on_point"] is False
    # THE RECORD separates what the last pass RAN at from what the regulator
    # would have aimed at next — computed on the final pass too.
    assert inv["v_phase_peak_V"] == pytest.approx(
        c["history"][-1]["v_phase_peak_V"])
    assert inv["v_phase_peak_next_V"] == pytest.approx(
        c["history"][-1]["v_phase_peak_next_V"])
    assert inv["v_phase_peak_next_V"] != inv["v_phase_peak_V"]


def test_a_current_fed_loop_converges_on_temperatures_alone_as_before(loop):
    """Sine/current drive is UNTOUCHED: the current is imposed there, so the
    point error is 0 by construction and the temperatures decide alone.  Same
    for a PWM run with the fundamental pinned instead of a target."""
    cp, _ = loop
    c = cp.run({**BODY, "max_iter": 3})["coupling"]
    assert c["drive"] == "sine"
    assert c["converged"] is True and len(c["history"]) == 2
    assert "warning_code" not in c and c["warning"] is None
    assert all("point_error_pct" not in r for r in c["history"])
    pinned = cp.run({**BODY, "drive": "pwm", "max_iter": 3})["coupling"]
    assert pinned["converged"] is True and len(pinned["history"]) == 2
    assert "warning_code" not in pinned


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
    """Pass 1 may carry ledger=True when history_fresh is false; every later
    pass must be ledger=False; restore and ledger_probe stay False everywhere.

    The ledger is one of three doors into get_fem_transient that answer without
    solving; a coupled iteration solves at its own temperature conditions rather
    than reusing cached results, except for the first pass when history_fresh is
    false — which is the identical point a plain Run would have made anyway."""
    cp, seen = loop

    # Run 1: PWM coupling (max_iter=1 from fixture, so 1 EM call)
    cp.run({**BODY, "drive": "pwm"})
    assert len(seen["em"]) == 1, "expected 1 EM call per run (max_iter=1)"
    kw = seen["em"][0]
    assert kw["restore"] is False, "restore must stay False"
    # Pass 1 may carry ledger=True when history_fresh is False (the default)
    assert kw["ledger_probe"] is False, "ledger_probe must stay False"

    # Run 2: sine coupling
    seen["em"].clear()
    cp.run(dict(BODY))
    assert len(seen["em"]) == 1, "expected 1 EM call per run (max_iter=1)"
    kw = seen["em"][0]
    assert kw["restore"] is False, "restore must stay False"
    # Pass 1 may carry ledger=True when history_fresh is False (the default)
    assert kw["ledger_probe"] is False, "ledger_probe must stay False"


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


# ---------------------------------------------------------------------------
# (g) THE GENERATOR — the same loop, run backwards
# ---------------------------------------------------------------------------
# CIANO10 200 opt / L180 gen, 2026-09-15.  Two generating duties on the same
# Ø200 die: 'rated 0.5x9 mm' (600.38 A line, 20 900 rpm, γ = −15°, delta) and
# 'peak 0.5x9 mm' (614.46 A, 22 900 rpm).  Everything the motor campaign relies
# on has a mirror here that is easy to get wrong, so each one is pinned:
#
#   * the SEED is the duty's own solved terminal phasor and is already in the
#     generator's frame — `postproc.fundamental_voltage` extracts it from the
#     run that had γ+180 folded in, in exactly the (v_phase_peak, v_delta_deg)
#     coordinates the PWM source consumes.  Nothing may add a second 180°;
#   * the 180° belongs to the CURRENT.  The voltage source is placed by
#     v_delta_deg + daxis alone (pwm.PwmVoltageSource), so the generator's shift
#     must travel with `mode` to the transient route and nowhere else;
#   * the TARGET is the WINDING current: in delta the catalogued 600.38 A is the
#     line current and the branch carries 346.63 A, which is what the solver
#     reports back as `I_phase_rms_solved_A`;
#   * the DIRECTION of the regulator's first, damped step.  Measured on this
#     machine's own stored dq numbers (psi_pm 0.070592 Wb, Ld 0.0976 mH, Lq
#     0.0693 mH, R 7.032 mOhm, 1741.7 Hz) the phasor model reproduces the duty's
#     346.63 A to 0.07 % and gives dlnI/dlnV = +0.62: on THIS generator more
#     fundamental means more generating current, the same sign as a motor (the
#     terminal phasor sits 81° off the (V−E) direction, not past it).  So the
#     damped step keeps its direction — but a generator CAN sit on the other
#     side of that null, and there the secant must walk the other way, which is
#     what `test_a_negative_measured_slope_is_walked_the_right_way` pins.


def _gen_body(**kw):
    """The L180 generating duty as the background runner sends it."""
    b = {"n_steps_per_period": 36, "n_periods": 1.0, "gamma_deg": -15.0,
         "I_phase_rms": 600.38, "coil_temp_c": 127.0, "mode": "generator",
         "star_delta": "delta", "eddy": True, "rotor_eddy": True,
         "max_iter": 1, "mesh_size_mm": 4.0, "min_size_mm": 0.3,
         "n_sectors": 2, "rpm": 20900.0, "drive": "pwm",
         # Same as BODY above: the 20 °C catalogue pass is off, because these
         # tests read `seen["em"][-1]` and count the passes the bridge was
         # asked for.
         "cold_constants": False,
         "inverter": {"f_carrier_hz": 24000.0, "v_dc_V": 799.2,
                      "v_phase_peak_V": 729.6062, "v_delta_deg": -29.456,
                      "target_I_phase_rms_A": 346.63, "i_tol_pct": 1.0}}
    b.update(kw)
    return b


def test_a_generators_seed_reaches_the_bridge_verbatim(loop):
    """No second 180°, no sign flip: the duty's solved terminal phasor IS the
    request, because it was extracted in the drive's own coordinates."""
    cp, seen = loop
    cp.run(_gen_body())
    em = seen["em"][-1]
    assert em["v_phase_peak"] == pytest.approx(729.6062)
    assert em["v_delta_deg"] == pytest.approx(-29.456)


def test_the_generators_180_travels_with_the_mode_not_with_the_voltage(loop):
    """The transient route turns `mode: generator` into γ+180 itself, so the
    coupled half hands it the PANEL angle and the mode — while the loss-map
    probe, which speaks the solver's already-shifted frame, asks for the shifted
    one.  Backwards, that probe matches no run."""
    cp, seen = loop
    cp.run(_gen_body())
    em = seen["em"][-1]
    assert em["mode"] == "generator"
    assert em["gamma_deg"] == pytest.approx(-15.0), "the panel angle, unshifted"
    assert seen["probe"][-1]["gamma_deg"] == pytest.approx(165.0)


def test_the_generators_seed_falls_back_to_the_duty_summary(loop, monkeypatch):
    """A negative load angle is the generator's normal case, and `_num` refuses
    non-positive numbers — so the angle must NOT go through it."""
    cp, _ = loop
    monkeypatch.setattr(cp, "_duty_summary",
                        lambda: {"V1_seed_peak_V": 789.7464,
                                 "V1_seed_delta_deg": -31.277})
    inv = cp._inverter_settings({"star_delta": "delta", "mode": "generator",
                                 "inverter": {"v_dc_V": 799.2,
                                              "f_carrier_hz": 24000.0}},
                                rpm=22900.0)
    assert inv["v_phase_peak_V"] == pytest.approx(789.7464)
    assert inv["v_delta_deg"] == pytest.approx(-31.277)
    assert inv["sources"]["v_phase_peak_V"].startswith("the duty's saved")


# ── the point, and the direction the regulator walks to hold it ─────────────

def _reg_gen(v, i, pts, target=346.63, tol=1.0, seed=729.6062, cap=None):
    """The L180 'rated 0.5x9 mm' regulator: the target is the WINDING current
    (600.38 A line over √3), the seed the duty's own V₁."""
    from motor_ai_sim.routes import coupled as cp
    inv = {"v_phase_peak_V": v, "v_phase_peak_seed_V": seed,
           "target_I_phase_rms_A": target, "i_tol_pct": tol}
    if cap is not None:
        inv["v_phase_peak_max_V"] = cap
    return cp._regulate_v1(inv, i, pts)


def test_the_generators_target_is_the_winding_current_not_the_line(loop):
    """600.38 A at the terminals is 346.63 A in a delta branch, and
    `I_phase_rms_solved_A` is the BRANCH current — judging one against the other
    would read a machine on point as 73 % over it."""
    cp, seen = loop
    cp.run(_gen_body())
    rec = seen["record"][-1][0]["coupling"]["inverter"]
    assert rec["target_I_phase_rms_A"] == pytest.approx(346.63)
    assert rec["point_error_pct"] == pytest.approx(
        100.0 * (430.1 - 346.63) / 346.63, abs=1e-3)


def test_the_generators_first_step_is_damped_and_follows_the_machine():
    """One point, so no slope is known: the damped proportional step, in the
    direction this machine measures (dlnI/dlnV = +0.62 — more fundamental, more
    generating current) and short of the full step."""
    pts = []
    out = _reg_gen(729.6062, 330.0, pts)        # 4.8 % LOW on the generator
    prop = 729.6062 * (346.63 / 330.0)
    assert out["v_phase_peak_V"] > 729.6062, "too little current asks for more V₁"
    assert out["v_phase_peak_V"] < prop, "a full proportional step overshoots"
    out = _reg_gen(729.6062, 365.0, [])         # …and 5.3 % HIGH walks back
    assert out["v_phase_peak_V"] < 729.6062


def test_a_negative_measured_slope_is_walked_the_right_way():
    """THE GENERATOR'S OWN HAZARD.  |I| = |V − E|/|Z|, so a machine whose
    terminal phasor sits past the (V − E) normal answers MORE current to LESS
    fundamental.  The secant is measured, not assumed, and must follow it — a
    regulator that hard-coded the motor's sign would run to its clamp."""
    pts = [(700.0, 380.0)]                      # …and this pass: 20 V MORE
    out = _reg_gen(720.0, 360.0, pts, target=400.0)     # gave 20 A LESS
    assert out is not None
    assert out["v_phase_peak_V"] < 720.0, (
        "with dI/dV < 0, raising the current means lowering V₁")
    # the secant is exact on the measured slope: 720 + (400−360)·(20/−20)
    assert out["v_phase_peak_V"] == pytest.approx(680.0)


def test_the_regulator_never_asks_for_more_than_the_link_can_build():
    """The rated generating duty runs at 729.61 V of a link whose LINEAR ceiling
    is 795.95 V — but what the regulator asks for is the fundamental the
    modulator must APPLY, and at this duty's 14 carriers that ceiling is
    747.18 V.  One honest correction upward would otherwise be a 422 from the
    electromagnetic half, hours into the loop."""
    cap = 747.1802
    out = _reg_gen(729.6062, 300.0, [], target=400.0, seed=729.6062, cap=cap)
    assert out["v_phase_peak_V"] == pytest.approx(cap)
    assert out["v_phase_peak_at_modulation_ceiling"] is True
    # …and a step that does not need the ceiling says so, rather than carrying
    # an earlier pass's flag forward.
    out = _reg_gen(729.6062, 400.0, [], target=354.76, seed=729.6062, cap=cap)
    assert out["v_phase_peak_V"] < 729.6062
    assert out["v_phase_peak_at_modulation_ceiling"] is False


def test_the_ceiling_is_the_delta_ceiling_and_reaches_the_record(loop):
    """m = 2·V₁/(√3·V_dc) in delta (pwm.modulation_index), so the ceiling the
    regulator is given carries the same √3 — 795.95 V of REFERENCE on the L180's
    799.2 V pack, not 459.5 V — and the compensated ceiling under it carries the
    same √3 with it."""
    import math
    from motor_ai_sim.simulation.pwm import (MAX_MODULATION_INDEX,
                                             modulation_index)
    cp, seen = loop
    inv = cp._inverter_settings({"star_delta": "delta", "mode": "generator",
                                 "inverter": {"v_dc_V": 799.2,
                                              "f_carrier_hz": 24000.0,
                                              "v_phase_peak_V": 729.6062,
                                              "v_delta_deg": -29.456}},
                                rpm=20900.0)
    assert inv["v_phase_peak_max_uncompensated_V"] == pytest.approx(
        0.5 * MAX_MODULATION_INDEX * math.sqrt(3.0) * 799.2, rel=1e-6)
    assert modulation_index(inv["v_phase_peak_max_uncompensated_V"], 799.2,
                            star_delta="delta") == pytest.approx(
                                MAX_MODULATION_INDEX, rel=1e-5)
    # …and the star machine keeps the ceiling it always had, √3 below.
    inv_star = cp._inverter_settings({"star_delta": "star",
                                      "inverter": {"v_dc_V": 799.2,
                                                   "f_carrier_hz": 24000.0,
                                                   "v_phase_peak_V": 400.0,
                                                   "v_delta_deg": 10.0}},
                                     rpm=22900.0)
    assert inv_star["v_phase_peak_max_uncompensated_V"] == pytest.approx(
        0.5 * MAX_MODULATION_INDEX * 799.2, rel=1e-6)
    assert inv_star["v_phase_peak_max_V"] < inv["v_phase_peak_max_V"]
    cp.run(_gen_body())
    rec = seen["record"][-1][0]["coupling"]["inverter"]
    assert rec["v_phase_peak_max_uncompensated_V"] == pytest.approx(
        795.9466, abs=0.01)
    assert rec["v_phase_peak_max_V"] == pytest.approx(747.18, abs=0.01)
    assert rec["modulator_gain_factor"] == pytest.approx(0.94345, abs=1e-4)


# ── the ceiling is the one the MODULATOR can apply, not the one the bridge
#    can chop (2026-09-15, CIANO10 200 opt / L180 gen 'rated 0.5x9 mm') ──────
#
# The first clamp used 0.5·1.15·V_bus — the largest REFERENCE.  But
# `pwm.build_pwm_source` does not apply the reference it is handed: it solves for
# the reference whose APPLIED fundamental is the requested one, and refuses when
# that solved reference leaves the linear region.  So on that generator (24 kHz,
# 799.2 V link, 20 900 rpm → 1 741.7 Hz → 14 carriers per period) the regulator
# aimed pass 4 at 761.7 V — under the 795.95 V it had been given, over the 747 V
# the modulator can actually build — and the electromagnetic half refused it
# ("needs m = 1.161, past the 1.15 linear limit").  The loop lost its last pass
# and saved 3.4 % off point.

def test_the_ceiling_is_what_the_modulator_can_apply_not_what_it_can_chop(loop):
    """The L180 rated case, end to end: 14 carriers on a 799.2 V delta link give
    a REFERENCE ceiling of 795.95 V and an APPLIED ceiling of 747.18 V, and it is
    the second one the regulator is handed."""
    cp, _ = loop
    inv = cp._inverter_settings({"star_delta": "delta", "mode": "generator",
                                 "inverter": {"v_dc_V": 799.2,
                                              "f_carrier_hz": 24000.0,
                                              "v_phase_peak_V": 729.6062,
                                              "v_delta_deg": -29.456}},
                                rpm=20900.0)
    assert inv["carriers_per_period"] == 14
    assert inv["f_elec_hz"] == pytest.approx(1741.6667, abs=1e-3)
    assert inv["v_phase_peak_max_uncompensated_V"] == pytest.approx(795.9466,
                                                                   abs=0.01)
    assert inv["v_phase_peak_max_V"] == pytest.approx(747.18, abs=0.01)
    # the gain is MEASURED off the modulator, and the 0.5 % margin is under it
    g = inv["modulator_gain_factor"]
    assert g == pytest.approx(0.94345, abs=1e-4)
    assert inv["v_phase_peak_max_V"] == pytest.approx(
        inv["v_phase_peak_max_uncompensated_V"] * g * 0.995, abs=1e-3)


def test_the_bridge_really_accepts_the_ceiling_and_really_refused_761_7():
    """The proof, against the modulator itself: a request AT the ceiling builds,
    and the 761.7 V the old clamp allowed is the 422 that killed the pass."""
    import math
    from motor_ai_sim.simulation.pwm import (build_pwm_source, ExcitationError,
                                             star_equivalent_bus)
    f_el = 20900.0 * 5 / 60.0
    kw = dict(pole_pairs=5, daxis_deg=0.0, v_delta_deg=-29.456,
              v_bus=star_equivalent_bus(799.2, "delta"), f_switch_hz=24000.0,
              f_elec_hz=f_el, v_bus_real=799.2)
    src = build_pwm_source(v_phase_peak=747.1802, **kw)
    assert src.carriers == 14
    assert src.m <= 1.15
    assert src.applied_fundamental()[0] == pytest.approx(747.18, abs=1.0)
    with pytest.raises(ExcitationError) as e:
        build_pwm_source(v_phase_peak=761.7, **kw)
    assert "14 carriers per period needs m = 1.161" in str(e.value)
    # …and the old ceiling would have let exactly that request through.
    assert 761.7 < 0.5 * 1.15 * math.sqrt(3.0) * 799.2


def test_a_clamped_step_costs_a_pass_no_longer_and_says_it_is_out_of_inverter(loop):
    """The defect's own consequence, reversed: the step is clamped to something
    the bridge CAN build, the pass runs, and the run that ends off point at the
    ceiling says it ran out of INVERTER rather than out of iterations."""
    cp, seen = loop
    body = _gen_body(max_iter=2)
    body["inverter"] = dict(body["inverter"], target_I_phase_rms_A=600.0)
    c = cp.run(body)["coupling"]
    h = c["history"]
    assert len(h) == 2 and len(seen["em"]) == 2, "the clamped pass RAN"
    # pass 1 asked for far more fundamental than the link can build…
    assert h[0]["v_phase_peak_next_V"] == pytest.approx(747.1802, abs=0.01)
    # …and pass 2 ran at the ceiling, not at the 816 V the secant wanted
    assert h[1]["v_phase_peak_V"] == pytest.approx(747.1802, abs=0.01)
    assert seen["em"][1]["v_phase_peak"] == pytest.approx(747.1802, abs=0.01)
    assert c["converged"] is False
    assert c["warning_code"] == "point_limited_by_modulation"
    assert "747.18 V peak" in c["warning"] and "14 carriers" in c["warning"]
    assert "430.10 A" in c["warning"], "the current the bridge CAN reach"
    assert c["inverter"]["at_modulation_ceiling"] is True
    assert c["inverter"]["on_point"] is False


def test_a_seed_over_the_compensated_ceiling_is_refused_by_name_not_clamped(loop):
    """THE SEED IS NOT THE REGULATOR'S TO MOVE.  The peak generating duty asks
    for 789.75 V, which needs m = 1.204 once the modulator's gain is compensated
    — no clamp can make that valid, and clamping the run's own operating point
    down to the ceiling would be this function inventing a duty.  So no ceiling
    is handed on, and the electromagnetic half refuses the run by name."""
    import math
    from motor_ai_sim.simulation.pwm import (build_pwm_source, ExcitationError,
                                             star_equivalent_bus)
    cp, _ = loop
    inv = cp._inverter_settings({"star_delta": "delta", "mode": "generator",
                                 "inverter": {"v_dc_V": 799.2,
                                              "f_carrier_hz": 24000.0,
                                              "v_phase_peak_V": 789.7464,
                                              "v_delta_deg": -29.456}},
                                rpm=20900.0)
    assert "v_phase_peak_max_V" not in inv, "nothing is clamped to a seed"
    # …but both numbers are still recorded, so the refusal can be read
    assert inv["v_phase_peak_max_uncompensated_V"] == pytest.approx(795.9466,
                                                                    abs=0.01)
    assert inv["modulator_gain_factor"] == pytest.approx(0.94345, abs=1e-4)
    assert cp._regulate_v1(dict(inv, v_phase_peak_seed_V=789.7464,
                                target_I_phase_rms_A=400.0),
                           300.0, [])["v_phase_peak_V"] > 789.7464
    with pytest.raises(ExcitationError) as e:
        build_pwm_source(pole_pairs=5, daxis_deg=0.0, v_phase_peak=789.7464,
                         v_delta_deg=-29.456,
                         v_bus=star_equivalent_bus(799.2, "delta"),
                         f_switch_hz=24000.0, f_elec_hz=20900.0 * 5 / 60.0,
                         v_bus_real=799.2)
    assert "past the 1.15 linear limit" in str(e.value)
    assert "m = 1.204" in str(e.value)
    assert 789.7464 < 0.5 * 1.15 * math.sqrt(3.0) * 799.2


def test_the_l155_seed_is_well_inside_and_nothing_about_it_moves(loop):
    """The L155 rated duty — 280 steps / 14 carriers on a 750.4 V link — sits at
    572.14 V against a compensated ceiling of 701.5 V.  A ceiling that bit there
    would be the fix breaking the case it was not about."""
    cp, seen = loop
    inv = cp._inverter_settings({"star_delta": "delta",
                                 "inverter": {"v_dc_V": 750.4,
                                              "f_carrier_hz": 24000.0,
                                              "v_phase_peak_V": 572.1396,
                                              "v_delta_deg": 34.259}},
                                rpm=20000.0)
    assert inv["carriers_per_period"] == 14
    assert inv["n_steps_per_period"] == 280
    assert inv["v_phase_peak_max_V"] == pytest.approx(701.50, abs=0.05)
    # 18 % of headroom: the regulator's whole ±40 % band on the low side and
    # most of it on the high side is untouched by the clamp.
    assert inv["v_phase_peak_max_V"] > 1.18 * 572.1396
    out = cp._regulate_v1(dict(inv, v_phase_peak_seed_V=572.1396,
                               target_I_phase_rms_A=460.0), 430.1, [])
    assert out["v_phase_peak_V"] > 572.1396
    assert out["v_phase_peak_at_modulation_ceiling"] is False


def test_the_peak_generating_duty_is_refused_by_name_on_the_wrong_pack():
    """789.75 V of delta branch fundamental needs 795.95 V of link at m = 1.15.
    On the L155's 750.4 V pack — or on this pack's own 749.5 V floor — it is
    m = 1.215 and the run must REFUSE, by name, rather than saturate silently;
    on the L180's own 799.2 V nominal it is m = 1.141 and it stands."""
    from motor_ai_sim.simulation.pwm import (MAX_MODULATION_INDEX,
                                             modulation_index)
    for v_dc in (749.5, 750.4):
        assert modulation_index(789.7464, v_dc,
                                star_delta="delta") > MAX_MODULATION_INDEX
    assert modulation_index(789.7464, 799.2, star_delta="delta") == \
        pytest.approx(1.1410, abs=5e-4)
    assert modulation_index(729.6062, 799.2, star_delta="delta") == \
        pytest.approx(1.0541, abs=5e-4)
