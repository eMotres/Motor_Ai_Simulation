"""The 3-D factor on torque-proportional numbers (owner 2026-09-30).

«если были старые расчёты 3D — применяй пока их»: torque, rotor power,
torque per mass and Kt / Km carry the MEASURED torque factor k_T when the
machine has one, else the Stage A flux factor k_flux (labelled), else nothing;
KV, ψ_PM and the voltages always carry k_flux.  One factor for every torque
number, so torque and Kt agree.
"""
from __future__ import annotations

import pytest

from motor_ai_sim import end3d_factors as F


def test_priority_measured_then_flux_then_2d():
    assert F.torque_factor({"k_flux": 0.95, "k_T": 0.98}) == (0.98, F.BASIS_MEASURED)
    assert F.torque_factor({"k_flux": 0.95}) == (0.95, F.BASIS_FLUX)
    assert F.torque_factor(None) == (None, F.BASIS_2D)
    assert F.torque_factor({}) == (None, F.BASIS_2D)
    # a resolved k_torque that is only the flux factor is not "measured"
    assert F.torque_factor({"k_flux": 0.95, "k_torque": 0.95,
                            "torque_basis": F.BASIS_FLUX}) == (0.95, F.BASIS_FLUX)


def test_basis_notes_are_one_clause():
    assert "measured torque factor k_T = 0.9795" in F.basis_note(0.9795, F.BASIS_MEASURED)
    assert "k_T not measured" in F.basis_note(0.952, F.BASIS_FLUX)
    assert F.basis_note(None, F.BASIS_2D).startswith("2-D")


def test_the_stage_b_k_T_of_the_40mm_is_found_by_its_fingerprint():
    """config/end_effect_3d.json: Ø40 12s/14p, 12 mm, k_T 0.97947."""
    m = F.measured_k_T("bb30c0242629c6ea")
    assert m and m["k_T"] == pytest.approx(0.979466, abs=1e-5)
    assert m["inherited"] is False
    assert "Stage B" in m["source"]


def test_an_earlier_geometry_of_the_same_machine_lends_its_k_T():
    geo = {"num_slots": 12, "num_poles": 14, "stator_diameter": 40.0,
           "motor_length": 12.0}
    m = F.measured_k_T("another-fingerprint", geo)
    assert m and m["inherited"] is True
    assert m["k_T"] == pytest.approx(0.979466, abs=1e-5)
    # …but not at another stack length: k_T depends on the stack
    assert F.measured_k_T("another-fingerprint", dict(geo, motor_length=20.0)) is None
    # …and not for another machine
    assert F.measured_k_T("x", dict(geo, stator_diameter=85.0)) is None


def test_the_stage_d_k_T_of_the_150mm():
    geo = {"num_slots": 24, "num_poles": 28, "stator_diameter": 150.0,
           "motor_length": 35.0}
    m = F.measured_k_T("47dd1b95904c0447", geo)
    assert m and m["k_T"] == pytest.approx(0.99245, abs=1e-5)


def test_enrich_adds_k_T_and_the_resolved_factor():
    e = F.enrich({"k_flux": 0.9517}, "bb30c0242629c6ea")
    assert e["k_T"] == pytest.approx(0.979466, abs=1e-5)
    assert e["k_torque"] == pytest.approx(0.979466, abs=1e-5)
    assert e["torque_basis"] == F.BASIS_MEASURED
    e2 = F.enrich({"k_flux": 0.93}, "no-such-fingerprint",
                  {"num_slots": 24, "num_poles": 28, "stator_diameter": 85.0,
                   "motor_length": 13.0})
    assert "k_T" not in e2
    assert e2["k_torque"] == pytest.approx(0.93) and e2["torque_basis"] == F.BASIS_FLUX
    assert F.enrich(None, "x") is None


def test_report_headline_and_section_three_use_one_factor():
    """Torque, rotor power and Kt of one run carry the same factor."""
    from motor_ai_sim import report as R
    em = {"T_em_avg_Nm": 100.0, "P_mech_W": 100000.0, "Kt_Nm_per_Arms": 0.5,
          "end3d": {"k_flux": 0.95, "k_T": 0.98, "T_corrected_Nm": 98.0}}
    assert R.torque_factor_3d(em) == pytest.approx(0.98)
    assert R.torque_basis_3d(em) == F.BASIS_MEASURED
    em2 = {"T_em_avg_Nm": 100.0, "end3d": {"k_flux": 0.95}}
    assert R.torque_factor_3d(em2) == pytest.approx(0.95)
    assert "k_T not measured" in R.kt_basis_note_of(em2)
    assert R.torque_factor_3d({"T_em_avg_Nm": 1.0}) is None
