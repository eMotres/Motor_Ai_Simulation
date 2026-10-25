"""The report's rotor SF rows on a retention FAIL (owner rule 2026-10-25).

Magnets are retained by the rotor iron ALONE — no adhesive.  A rotor-stress
answer whose separation contact does not retain them (did not converge, ended
tied, ran away and was only solved bonded for information) carries no safety
factor, and every place the report prints one must say "not retained — fails"
instead: a "—" would read as "not solved", and a number would read as a pass.
See tests/test_mechanical_contact_retention.py for the solver side.
"""
from __future__ import annotations

from motor_ai_sim import duty_results as DR
from motor_ai_sim import report as R

REASON = ("the magnet_rotor joint does not retain the part in the separation "
          "model (93% of it open at 24,000 rpm) — magnets not retained without "
          "adhesive")

FAILED_CASE = {
    "rpm": 24000.0, "sf_min": None, "sf_min_part": None, "sf_min_p05": None,
    "parts": {"rotor": {"material": "20SW1200", "von_mises_p995_mpa": 120.0,
                        "von_mises_max_mpa": 150.0, "governing_stress_mpa": 150.0,
                        "strength_mpa": 420.0, "safety_factor": None},
              "magnet": {"material": "F52SH_120C", "von_mises_p995_mpa": 40.0,
                         "von_mises_max_mpa": 50.0, "governing_stress_mpa": 20.0,
                         "strength_mpa": 80.0, "safety_factor": None}},
    "sf_info": {"label": "INFO ONLY — bonded", "sf_min": 2.8},
    "retention": {"status": "fail", "verdict": R.ROTOR_NOT_RETAINED,
                  "reasons": [REASON],
                  "message": f"{R.ROTOR_NOT_RETAINED}: {REASON}"},
}


def test_the_part_table_says_fail_where_the_sf_would_be():
    rows = R.mech_part_rows(FAILED_CASE)
    assert rows[0][-1] == "SF"
    assert [r[-1] for r in rows[1:]] == ["FAIL", "FAIL"]
    # the stresses themselves are still printed — they are what was solved
    assert rows[2][3] == R._fmt(150.0, 1, "MPa")


def test_the_sentence_under_the_table_is_the_verdict():
    txt = R.mech_percentile_text(FAILED_CASE)
    assert R.ROTOR_NOT_RETAINED in txt
    assert "no adhesive" in txt and REASON in txt
    assert "Lowest safety factor" not in txt
    # a passing case reads as before
    ok = R.mech_percentile_text({"sf_min": 2.0, "sf_min_part": "magnet"})
    assert "Lowest safety factor %s on magnet" % R._fmt(2.0, 2) in ok
    assert R.ROTOR_NOT_RETAINED not in ok


def test_the_compact_duty_record_keeps_the_verdict():
    rec = DR.compact_mechanical("rotor_stress", {
        "primary_case": "24,000 rpm", "rpm": 24000.0,
        "cases": {"24,000 rpm": FAILED_CASE}}, {}, None)
    assert rec["sf_min"] is None
    assert rec["retention"]["status"] == "fail"
    assert REASON in rec["retention"]["message"]
    assert R.case_not_retained(rec)


def test_the_warnings_section_raises_a_red_row_without_an_sf():
    rec = DR.compact_mechanical("rotor_stress", {
        "primary_case": "24,000 rpm", "rpm": 24000.0,
        "cases": {"24,000 rpm": FAILED_CASE}}, {}, None)
    col = {"duty": "overspeed", "em": {}, "d": {}, "result": {},
           "res": {"rotor_stress": rec}}
    ctx = R._warning_context(col, mats={}, batt={}, brg=None,
                             max_speed_rpm=24000.0, mag_lim=180.0,
                             mag_note="", ins_lim=200.0, ins_note="",
                             cold_k=1.0, cold_note="", slots=12)
    assert ctx["sf_min"] is None and REASON in ctx["rotor_not_retained"]
    ws = R.duty_warnings(ctx)
    w = next(w for w in ws if w["rule"] == "rotor_retention")
    assert w["level"] == "red" and w["value"] is None
    assert R.ROTOR_NOT_RETAINED in w["quantity"] and REASON in w["note"]
    assert ws[0]["level"] == "red"
    # the row renders: "OVER … flagged", never a number
    cells = R.warning_row(w)
    assert cells[0] == "OVER" and cells[3] == "flagged"
    # and no SF row pretends to grade a FAIL
    assert not any(x["rule"] == "safety_factor" for x in ws)


def test_the_comparison_table_says_it_too():
    rec = DR.compact_mechanical("rotor_stress", {
        "primary_case": "24,000 rpm", "rpm": 24000.0,
        "cases": {"24,000 rpm": FAILED_CASE}}, {}, None)
    ok = dict(rec, sf_min=2.5, retention={"status": "pass"})
    cols = [{"duty": "overspeed", "em": {}, "d": {}, "res": {"rotor_stress": rec}},
            {"duty": "rated", "em": {}, "d": {}, "res": {"rotor_stress": ok}}]
    rows = {r[0]: r[1:] for r in R.mech_compare_rows(cols)[1]}
    assert rows["Lowest safety factor"] == [R.ROTOR_NOT_RETAINED, R._fmt(2.5, 2)]
