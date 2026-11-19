"""T-Motor heavy-lift propellers of 42-47 in (added 2026-10-20, docs/PROPELLER_CATALOG_2026-10-05.md).

Pins that the four cards load as torque-measured entries, that the fit reproduces T-Motor's own rows,
and that the propeller-level tables' 'Output Power' really is shaft power (tau x omega).
Local and closed-form: no FEM.
"""
from __future__ import annotations

import math

import pytest

from motor_ai_sim import propeller as pp

G0 = 9.80665
LAB = ("tmotor_hep_s_42x16", "tmotor_vz42x16_5", "tmotor_vz45x18")   # propeller-level tables (output = shaft power)
ALL = LAB + ("tmotor_ns47x18",)                                        # + a motor-bench table (Pel = electrical)


@pytest.mark.parametrize("pid", ALL)
def test_card_is_torque_measured_with_sources(pid):
    p = pp.get_propeller(pid)
    s = pp.summary(p)
    assert s["selectable"] and s["power_data"] == "measured_torque", pid
    assert p.raw["accessed"] == "2026-10-20" and p.raw["source_urls"], pid
    for t in p.raw["performance"]["tables"]:
        assert t["source_url"].startswith("https://store.tmotor.com/"), pid
    assert 41.5 <= p.diameter_nominal_in <= 47.5, pid


@pytest.mark.parametrize("pid", ALL)
def test_fit_reproduces_the_published_rows(pid):
    p = pp.get_propeller(pid)
    rho = p.test_density
    lo, hi = p.rpm_range
    n = 0
    for t in p.raw["performance"]["tables"]:
        for r in t["rows"]:
            if not (lo <= r["rpm"] <= hi):
                continue
            n += 1
            assert abs(pp.thrust_N(p, r["rpm"], rho) / G0 * 1000.0 / r["thrust_g"] - 1.0) < 0.06, (pid, r)
            assert abs(pp.torque_Nm(p, r["rpm"], rho) / r["torque_Nm"] - 1.0) < 0.06, (pid, r)
    assert n >= 12, pid


@pytest.mark.parametrize("pid", LAB)
def test_lab_tables_output_power_is_shaft_power(pid):
    p = pp.get_propeller(pid)
    for r in p.raw["performance"]["tables"][0]["rows"]:
        assert "Pel" not in r                         # no electrical column in a propeller-level table
        tau_omega = r["torque_Nm"] * 2.0 * math.pi * r["rpm"] / 60.0
        assert abs(tau_omega / r["Pshaft_W"] - 1.0) < 0.003, (pid, r)


def test_hep_s_42_absorbs_13_kw_at_4000_rpm_in_its_test_air():
    """HEP-S 42*16 at 4000 rpm, T-Motor's own test air (22 C, 25 m): 31.0 N*m, 13.0 kW (inside the tested range)."""
    p = pp.get_propeller("tmotor_hep_s_42x16")
    op = pp.operating_point(p, 4000.0, p.test_density)
    assert not op["extrapolated"]
    assert op["torque_Nm"] == pytest.approx(31.0, abs=0.3)
    assert op["shaft_power_W"] == pytest.approx(13000.0, rel=0.02)


def test_ns47_is_extrapolated_above_its_tested_3210_rpm():
    p = pp.get_propeller("tmotor_ns47x18")
    assert p.rpm_range[1] == 3210.0
    assert pp.coefficients(p, 3000.0)["extrapolated"] is False
    assert pp.coefficients(p, 4000.0)["extrapolated"] is True
