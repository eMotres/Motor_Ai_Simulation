"""The report and the datasheet carry the two POSITIONS the information panel switches between: cruise and peak (owner 2026-11-01).

The continuous duty (cruise) leads, the peak duty closes, and a duty that was never computed (takeoff / landing of a project that
only asks for the two) has no column - an empty column states nothing.  Synthetic data only; nothing is solved.
"""
from __future__ import annotations

import contextlib
import io

from motor_ai_sim import duty_results as dr
from motor_ai_sim import report as R

CRUISE = {"name": "cruise", "rpm": 850, "current_arms": 152, "summary": {"T_em_avg_Nm": 937.3, "P_loss_total_W": 2848}}
PEAK = {"name": "peak", "rpm": 850, "current_arms": 300, "summary": {"T_em_avg_Nm": 1800.0, "P_loss_total_W": 9000}}
TAKEOFF = {"name": "takeoff", "rpm": 850, "current_arms": 250}                 # never computed
LANDING = {"name": "landing", "rpm": 850, "current_arms": 100}                 # never computed


@contextlib.contextmanager
def _store(doc):
    orig_get, orig_ctx = dr.get, dr.active_context
    dr.get = lambda die, cfg: doc
    dr.active_context = lambda: None
    try:
        yield
    finally:
        dr.get, dr.active_context = orig_get, orig_ctx


def _names(cols):
    return [c["duty"] for c in cols]


def test_the_report_has_two_positions_cruise_first_peak_last_whatever_the_saved_order():
    cfg_doc = {"duties": [PEAK, LANDING, TAKEOFF, CRUISE]}
    with _store({}):
        cols, _ = R._duty_columns("die", "cfg", cfg_doc, {}, {}, None)
    assert _names(cols) == ["cruise", "peak"]


def test_a_continuous_duty_named_otherwise_still_leads_and_other_computed_duties_sit_between():
    climb = {"name": "climb", "rpm": 800, "current_arms": 200, "summary": {"T_em_avg_Nm": 1200.0}}
    rated = {"name": "rated", "rpm": 800, "current_arms": 100, "summary": {"T_em_avg_Nm": 700.0}}
    with _store({}):
        cols, _ = R._duty_columns("die", "cfg", {"duties": [PEAK, climb, rated]}, {}, {}, None)
    assert _names(cols) == ["rated", "climb", "peak"]


def test_a_duty_with_only_a_stored_record_is_computed_too():
    with _store({"takeoff": {"coupled": {"coil_temp_c": 120.0, "magnet_temp_c": 90.0, "point": {"rpm": 850, "I_phase_rms": 250}}}}):
        cols, _ = R._duty_columns("die", "cfg", {"duties": [CRUISE, PEAK, TAKEOFF]}, {}, {}, None)
    assert _names(cols) == ["cruise", "takeoff", "peak"]


def test_the_requested_cover_duty_always_has_its_column_and_an_all_empty_configuration_keeps_every_duty():
    with _store({}):
        cols, _ = R._duty_columns("die", "cfg", {"duties": [CRUISE, PEAK, TAKEOFF]}, {}, {}, None, keep="takeoff")
        assert _names(cols) == ["cruise", "takeoff", "peak"]
        cols, _ = R._duty_columns("die", "cfg", {"duties": [LANDING, TAKEOFF]}, {}, {}, None)
        assert sorted(_names(cols)) == ["landing", "takeoff"]


def test_the_cover_is_the_continuous_computed_duty_unless_one_is_asked_for():
    cfg_doc = {"duties": [LANDING, PEAK, CRUISE]}
    assert R._pick_duty(cfg_doc, None)["name"] == "cruise"
    assert R._pick_duty(cfg_doc, "peak")["name"] == "peak"
    assert R._pick_duty({"duties": [LANDING, TAKEOFF, PEAK]}, None)["name"] == "peak"      # the first COMPUTED duty
    assert R._pick_duty({"duties": [LANDING, TAKEOFF]}, None)["name"] == "landing"        # nothing computed: as before
    assert R._pick_duty({"duties": []}, None) is None


def _sheet_columns(duties, coupled=None):
    from openpyxl import load_workbook
    from motor_ai_sim.datasheet import build_datasheet
    blob = build_datasheet(die="die", cfg="cfg", die_doc={"geometry": {}}, cfg_doc={"duties": duties, "winding": {}}, coupled=coupled)
    ws = load_workbook(io.BytesIO(blob)).active
    cells = [str(c.value) for row in ws.iter_rows(min_row=1, max_row=12) for c in row if c.value is not None]
    return " | ".join(cells)


def test_the_datasheet_has_the_computed_positions_only():
    text = _sheet_columns([PEAK, TAKEOFF, LANDING, CRUISE])
    assert "cruise" in text and "peak" in text
    assert "takeoff" not in text and "landing" not in text
