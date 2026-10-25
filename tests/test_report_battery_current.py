"""Battery current per operating point in the motor report (owner 2026-10-26).

I_dc = P_dc / V_dc; motoring P_dc = P_shaft / (eta_motor x eta_inverter); generating P_dc = P_shaft x eta_motor x eta_inverter,
at the pack's minimum / nominal / maximum voltage.  Where the controller was not solved the report says "controller losses
not modelled - power devices unknown" instead of using an assumption silently.  All numbers are synthetic.
"""
from __future__ import annotations

import pytest

from motor_ai_sim import report as R


def _col(name, mode, p_mech_w, loss_w, controller_eta=None, duty_class=None):
    d = {"name": name, "mode": mode, "rpm": 3000}
    if duty_class:
        d["duty_class"] = duty_class
    res = {}
    if controller_eta is not None:
        res["controller"] = {"efficiency": {"inverter": controller_eta}}
    return {"duty": name, "d": d, "em": {"P_mech_W": p_mech_w, "P_loss_total_W": loss_w, "rpm": 3000},
            "res": res, "brg": None}


BATT = {"v_min": 600.0, "v_nom": 700.0, "v_max": 800.0}


def test_formula_motoring_and_generating():
    # motoring: 5 kW at the shaft, motor 95 %, inverter 98 % -> 5 / (0.95 x 0.98) kW from the bus
    p = R.battery_dc_power_w(5000, 0.95, 0.98, generating=False)
    assert p == pytest.approx(5000 / (0.95 * 0.98))
    assert R.battery_dc_current_a(p, 600) == pytest.approx(5000 / (0.95 * 0.98) / 600)
    # generating: the bus receives P x eta_motor x eta_inverter
    assert R.battery_dc_power_w(5000, 0.95, 0.98, generating=True) == pytest.approx(5000 * 0.95 * 0.98)
    # the magnitude is used (a generator's shaft power may be stored negative)
    assert R.battery_dc_power_w(-5000, 0.95, 0.98, generating=True) == pytest.approx(5000 * 0.95 * 0.98)
    # an inverter that is not modelled counts as 1
    assert R.battery_dc_power_w(5000, 0.95, None, False) == pytest.approx(5000 / 0.95)
    # unusable inputs give None, never a made-up number
    assert R.battery_dc_power_w(None, 0.95, 0.98, False) is None
    assert R.battery_dc_power_w(5000, 0, 0.98, False) is None
    assert R.battery_dc_current_a(5000, 0) is None and R.battery_dc_current_a(5000, None) is None


def test_the_current_is_lowest_at_the_highest_voltage():
    i = [R.battery_dc_current_a(5000, v) for v in (600, 700, 800)]
    assert i[0] > i[1] > i[2]


def test_rows_for_a_synthetic_duty_set():
    cols = [_col("rated", "motor", 5000, 250, controller_eta=0.98, duty_class="continuous"),
            _col("peak", "motor", 9000, 600, controller_eta=0.97),
            _col("brake", "generator", 4000, 200, controller_eta=0.98)]
    header, rows, notes = R.battery_current_rows(cols, BATT)
    assert header == "Battery current"
    by = {r[0]: r[1:] for r in rows}
    assert by["Direction"] == ["discharge", "discharge", "charge (regeneration)"]
    # rated: eta_motor = 5000 / 5250, P_elec 5250 W, / 0.98 = 5357.1 W; at 600 V 8.9 A, 700 V 7.7 A, 800 V 6.7 A
    assert by["Inverter efficiency [%]"][0] == "98"
    assert by["DC power [kW]"][0] == "5.36"
    assert by["Battery current at the pack minimum, 600 V [A]"][0] == "8.9"
    assert by["Battery current at the pack nominal, 700 V [A]"][0] == "7.7"
    assert by["Battery current at the pack maximum, 800 V [A]"][0] == "6.7"
    # peak: P_elec 9600 W / 0.97 = 9896.9 W -> 16.5 A at 600 V
    assert by["Battery current at the pack minimum, 600 V [A]"][1] == "16.5"
    # brake: P_elec = 4000 - 200 = 3800 W x 0.98 = 3724 W -> 6.2 A at 600 V
    assert by["DC power [kW]"][2] == "3.72"
    assert by["Battery current at the pack minimum, 600 V [A]"][2] == "6.2"
    assert notes == []                         # every duty has a controller result: nothing to disclaim


def test_missing_controller_says_so_instead_of_assuming():
    cols = [_col("rated", "motor", 5000, 250, controller_eta=0.98), _col("peak", "motor", 9000, 600)]
    _, rows, notes = R.battery_current_rows(cols, BATT)
    by = {r[0]: r[1:] for r in rows}
    assert by["Inverter efficiency [%]"] == ["98", "not modelled"]
    assert notes and notes[0].startswith("Controller losses not modelled — power devices unknown")
    # the duty without a controller is NOT given a generic efficiency: P_dc = P_elec (inverter taken as 1)
    assert by["DC power [kW]"][1] == "9.6"


def test_pack_summary_continuous_peak_and_charge():
    cols = [_col("rated", "motor", 5000, 250, controller_eta=0.98, duty_class="continuous"),
            _col("peak", "motor", 9000, 600, controller_eta=0.97),
            _col("brake", "generator", 4000, 200, controller_eta=0.98)]
    s = R.battery_current_summary(cols, BATT)
    assert [lab for lab, _ in s["levels"]] == ["minimum", "nominal", "maximum"]
    rows = {r["label"]: r for r in s["rows"]}
    cont = rows["Discharge, continuous [A]"]
    assert cont["duty"] == ["rated"] * 3 and cont["values"][0] == pytest.approx(5250 / 0.98 / 600)
    peak = rows["Discharge, peak [A]"]
    assert peak["duty"] == ["peak"] * 3 and peak["values"][0] == pytest.approx(9600 / 0.97 / 600)
    assert peak["values"][0] > cont["values"][0]
    charge = rows["Charge / regeneration, peak [A]"]
    assert charge["duty"] == ["brake"] * 3 and charge["values"][2] == pytest.approx(3800 * 0.98 / 800)


def test_without_a_stated_pack_the_duty_dc_link_is_used():
    col = _col("rated", "motor", 5000, 250, controller_eta=0.98)
    col["res"]["coupled"] = {"inverter": {"v_dc_V": 750.0}}
    col["res"]["controller"] = {"efficiency": {"inverter": 0.98}}
    _, rows, notes = R.battery_current_rows([col], {})
    labels = [r[0] for r in rows]
    assert "Battery current at the duty's DC link [A]" in labels
    assert any("No pack voltage is stated" in n for n in notes)


def test_the_report_section_builds_flowables():
    st = R._styles()
    cols = [_col("rated", "motor", 5000, 250), _col("brake", "generator", 4000, 200, controller_eta=0.98)]
    flow = R._battery_current(st, cols, BATT)
    assert len(flow) >= 4                     # heading, table, summary table, the "not modelled" flag, the note
    assert R._battery_current(st, [], BATT)   # the heading alone for an empty duty set


def test_the_no_load_speed_at_the_pack_maximum_is_a_report_finding_not_a_spec_question():
    """Owner 2026-10-26: the specification no longer asks for a runaway speed; the report works it out (KV x pack maximum)."""
    ws = R.duty_warnings({"duty": "u", "runaway_vmax_rpm": 12345.0, "runaway_vmax_v": 800.0})
    hit = next(w for w in ws if w["rule"] == "runaway_speed_vmax")
    assert hit["value"] == 12345.0 and hit["unit"] == "rpm" and hit["level"] == "info" and "800" in hit["note"]
    assert not [w for w in R.duty_warnings({"duty": "u"}) if w["rule"] == "runaway_speed_vmax"]     # no KV or no pack maximum: nothing printed
    assert "runaway_speed_vmax" in R.CONFIG_LEVEL_RULES                                              # one row for the configuration, not one per duty
