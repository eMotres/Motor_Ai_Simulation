"""PWM LIVES IN THE CONTROLLER — one carrier, one resolution, every consumer.

Owner, 2026-09-24 (a screenshot of the Controller tab's greyed "Carrier
20,000 Hz" placeholder): «Это значение нужно задавать в контроллере; PWM нужно
выкинуть из Electromagnetic.»  ``inverter.drive_source`` is the one resolution;
this file pins its order, the migration from the retired Simulation-tab
carrier, the backward-compatible acceptance of the old request fields, the
Electromagnetic route filling a ``pwm_voltage`` run from the Controller, and a
stored ``drive: "pwm"`` record still rendering its own carrier in the report.

Nothing here solves a field.
"""
from __future__ import annotations

import logging

import pytest

from motor_ai_sim.inverter import drive_source as ds


def _doc(**kw):
    doc = {"duties": [{"name": "rated", "mesh": {"sim.fSwitch": 24000}},
                      {"name": "peak", "mesh": {"sim.fSwitch": 48000}}],
           "battery": {"v_nom": 750.4, "v_min": 640.0, "v_max": 860.0}}
    doc.update(kw)
    return doc


@pytest.fixture
def catalog(monkeypatch):
    """A loaded configuration whose document the test decides."""
    state = {"doc": _doc()}
    monkeypatch.setattr(ds, "_context",
                        lambda die, cfg, duty: ("D", "C", duty or "rated", True))
    monkeypatch.setattr(ds, "load_config_doc", lambda die, cfg: state["doc"])
    monkeypatch.setattr(ds, "_live_sim", lambda: {"f_switch": 8000.0})
    monkeypatch.setattr(ds, "_live_battery_v", lambda: 400.0)
    ds._WARNED.clear()
    return state


# ---------------------------------------------------------------------------
# the carrier — the order
# ---------------------------------------------------------------------------

def test_the_saved_controller_carrier_is_the_carrier():
    a = ds.resolve_carrier(_doc(controller={"f_carrier_hz": 32000.0}),
                           duty="rated")
    assert a == {"hz": 32000.0, "origin": "controller",
                 "source": "the Controller settings (carrier)"}


def test_an_explicit_request_outranks_the_controller():
    a = ds.resolve_carrier(_doc(controller={"f_carrier_hz": 32000.0}),
                           duty="rated", request=48000)
    assert (a["hz"], a["origin"]) == (48000.0, "request")


def test_a_block_sent_by_reference_outranks_the_saved_one():
    a = ds.resolve_carrier(_doc(controller={"f_carrier_hz": 32000.0}),
                           controller={"f_carrier_hz": 16000.0})
    assert (a["hz"], a["origin"]) == (16000.0, "controller")


def test_migration_reads_the_duty_record_then_its_own_sim_fswitch():
    rec = (24383.0, "the PWM run")
    a = ds.resolve_carrier(_doc(), duty="peak", legacy_record=rec)
    assert (a["hz"], a["origin"]) == (24383.0, "legacy")
    assert "stored PWM record" in a["source"]
    a = ds.resolve_carrier(_doc(), duty="peak")
    assert (a["hz"], a["origin"]) == (48000.0, "legacy")
    assert "'peak'" in a["source"] and "sim.fSwitch" in a["source"]


def test_migration_falls_to_another_duty_then_the_simulation_block():
    doc = _doc()
    doc["duties"][0]["mesh"] = {}
    a = ds.resolve_carrier(doc, duty="rated")
    assert a["hz"] == 48000.0 and "'peak'" in a["source"]
    doc = {"simulation": {"f_switch": 12000.0}}
    a = ds.resolve_carrier(doc, duty="rated")
    assert (a["hz"], a["origin"]) == (12000.0, "legacy")
    a = ds.resolve_carrier({}, live_sim={"f_switch": 8000.0})
    assert (a["hz"], a["origin"]) == (8000.0, "legacy")


def test_a_null_controller_carrier_is_not_a_carrier():
    """A block saved before the field was a real value carries ``null`` —
    that is "not set", so the migration tier answers, not 0 Hz."""
    a = ds.resolve_carrier(_doc(controller={"f_carrier_hz": None}),
                           duty="rated")
    assert (a["hz"], a["origin"]) == (24000.0, "legacy")


def test_the_default_only_where_a_carrier_is_required():
    a = ds.resolve_carrier({})
    assert (a["hz"], a["origin"]) == (ds.DEFAULT_CARRIER_HZ, "default")
    assert "stated default" in a["source"]
    # A report / excitation table draws NO line rather than a made-up one.
    assert ds.resolve_carrier({}, default=False)["hz"] is None


# ---------------------------------------------------------------------------
# the DC link
# ---------------------------------------------------------------------------

def test_v_dc_order_manual_then_battery_then_legacy():
    assert ds.resolve_v_dc(_doc(controller={"v_dc_V": 700.0}))["V"] == 700.0
    a = ds.resolve_v_dc(_doc())
    assert a["V"] == 750.4 and "battery" in a["source"]
    a = ds.resolve_v_dc({}, legacy_record=(799.2, "the duty's own PWM bus"))
    assert (a["V"], a["origin"]) == (799.2, "legacy")
    assert ds.resolve_v_dc({})["V"] is None


# ---------------------------------------------------------------------------
# backward compatibility: the old request fields
# ---------------------------------------------------------------------------

def test_an_old_session_pwm_request_takes_the_controllers_values(catalog, caplog):
    catalog["doc"] = _doc(controller={"f_carrier_hz": 32000.0, "v_dc_V": 700.0})
    with caplog.at_level(logging.WARNING, logger=ds.__name__):
        out = ds.pwm_request_fields(
            {"drive": "pwm_voltage", "v_bus": 750.0, "f_switch": 24000.0},
            where="test")
    assert (out["f_switch"], out["v_bus"]) == (32000.0, 700.0)
    assert out["_drive_sources"]["f_switch"].startswith("the Controller")
    assert any("DEPRECATED" in r.getMessage() for r in caplog.records)


def test_an_old_session_field_is_kept_when_the_controller_has_none(catalog):
    out = ds.pwm_request_fields(
        {"drive": "pwm_voltage", "v_bus": 750.0, "f_switch": 24000.0},
        where="test")
    assert (out["f_switch"], out["v_bus"]) == (24000.0, 750.0)
    assert "deprecated" in out["_drive_sources"]["f_switch"]


def test_other_drives_restores_and_probes_pass_through(catalog):
    catalog["doc"] = _doc(controller={"f_carrier_hz": 32000.0})
    for p in ({"drive": "current"},
              {"drive": "pwm_voltage", "f_switch": 24000.0, "restore": True},
              {"drive": "pwm_voltage", "f_switch": 24000.0, "ledger_probe": True}):
        assert ds.pwm_request_fields(dict(p), where="test") == p


def test_the_kernel_run_seam_applies_the_controller(catalog, monkeypatch):
    """``POST /api/kernel/run`` is the Simulation tab's Run: an old session's
    ``pwm_voltage`` body is corrected there, before the route sees it."""
    import motor_ai_sim.modules.solvers as solvers
    import motor_ai_sim.routes.simulation as sim
    catalog["doc"] = _doc(controller={"f_carrier_hz": 32000.0})
    seen = {}
    monkeypatch.setattr(solvers, "_call_filtered",
                        lambda fn, payload: seen.update(payload) or {})
    monkeypatch.setattr(sim, "get_fem_transient", lambda **kw: {})
    solvers.EmTransientSolver().run({"drive": "pwm_voltage", "f_switch": 24000.0,
                                     "v_bus": 750.0})
    assert seen["f_switch"] == 32000.0 and seen["v_bus"] == 750.0


def test_the_simulation_config_patch_still_accepts_the_retired_fields():
    from motor_ai_sim.routes.simulation import SimConfigPatch
    p = SimConfigPatch(v_bus=750.0, f_switch=24000.0)
    assert (p.v_bus, p.f_switch) == (750.0, 24000.0)


# ---------------------------------------------------------------------------
# the Electromagnetic route fills a pwm_voltage run from the Controller
# ---------------------------------------------------------------------------

def test_a_pwm_voltage_run_without_bus_or_carrier_uses_the_controller(catalog):
    """The Simulation tab sends neither any more.  The route must take them
    from the Controller BEFORE it validates — so the refusal that follows (a
    fundamental no 700 V link can build) names the Controller's 700 V, not
    "needs a DC bus voltage"."""
    from fastapi import HTTPException
    from motor_ai_sim.routes import simulation as sim
    catalog["doc"] = _doc(controller={"f_carrier_hz": 32000.0, "v_dc_V": 700.0})
    with pytest.raises(HTTPException) as e:
        sim.get_fem_transient(drive="pwm_voltage", v_phase_peak=5000.0,
                              star_delta="star", I_phase_rms=10.0)
    assert e.value.status_code == 422
    assert "700.0 V DC link" in str(e.value.detail)


def test_the_pwm_calculator_takes_the_controllers_bus_and_carrier(catalog,
                                                                   monkeypatch):
    from motor_ai_sim.routes import simulation as sim
    from motor_ai_sim.simulation import pwm as pwm_mod
    catalog["doc"] = _doc(controller={"f_carrier_hz": 32000.0})
    seen = {}

    def _synth(**kw):
        seen.update(kw)
        return {"waveform": [], "n_samples": 0}
    monkeypatch.setattr(pwm_mod, "synthesize_pwm_current", _synth)
    out = sim.get_pwm_waveform(v_bus=None, f_switch=None, I_phase_rms=10.0,
                               R_phase_ohm=0.01, Ld_mH=0.1, Lq_mH=0.1,
                               psi_pm_mWb=10.0, poles=10, rpm=3000.0)
    assert seen["f_switch_hz"] == 32000.0
    assert seen["v_bus"] == 750.4                   # the battery, nominal
    assert out["drive_sources"]["f_switch"].startswith("the Controller")


# ---------------------------------------------------------------------------
# the report: the Controller's carrier, and an old PWM record still renders
# ---------------------------------------------------------------------------

MODES = [{"index": 6, "f_hz": 24028.07, "order": 3}]


def _col(coupled=None, fsw=24000):
    return {"duty": "rated", "d": {"rpm": 22900.0, "mesh": {"sim.fSwitch": fsw}},
            "em": {}, "result": {},
            "res": {"modes": {"modes": MODES},
                    **({"coupled": coupled} if coupled else {})}}


def _ctx(col, **kw):
    from motor_ai_sim import report as R
    return R._warning_context(col, mats={}, batt={}, brg=None,
                              max_speed_rpm=22900.0, mag_lim=180.0,
                              mag_note="", ins_lim=200.0, ins_note="",
                              cold_k=1.0, cold_note="", slots=12, **kw)


def test_report_judges_a_sine_duty_against_the_controllers_carrier():
    from motor_ai_sim import report as R
    doc = {"controller": {"f_carrier_hz": 32000.0},
           "duties": [{"name": "rated", "mesh": {"sim.fSwitch": 24000}}]}
    assert R.duty_carrier_hz(doc, "rated") == 32000.0
    ctx = _ctx(_col(), carrier_hz=R.duty_carrier_hz(doc, "rated"))
    assert "32,000 Hz" in ctx["ring_mode_note"]
    assert "Controller" in ctx["ring_mode_note"]
    # …the retired sim.fSwitch is the migration tier when nothing is saved
    doc.pop("controller")
    assert R.duty_carrier_hz(doc, "rated") == 24000.0
    assert R.duty_carrier_hz({}, "rated") is None


def test_an_old_pwm_record_still_renders_its_own_carrier():
    """A stored ``drive: "pwm"`` record switched at ITS carrier — the report
    keeps judging it against that, whatever the Controller says today."""
    inv = {"f_carrier_hz": 24000.0, "f_carrier_eff_hz": 24808.33,
           "carriers_per_period": 13, "v_dc_V": 1049.76, "m": 0.9515}
    col = _col({"drive": "pwm", "inverter": inv})
    ctx = _ctx(col, carrier_hz=32000.0)
    assert "24,808 Hz" in ctx["ring_mode_note"]
    assert abs(abs(ctx["ring_mode_margin_pct"]) - 3.145) < 0.01
    from motor_ai_sim import report as R
    assert R.duty_drive(col) == "pwm"
    assert R.duty_inverter(col)["f_carrier_hz"] == 24000.0
