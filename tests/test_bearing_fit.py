"""Tests for bearing shrink/hot fit calculation and ISO 281 rating life.

Covers:
- The three dT fit loss regression numbers within +-10%:
  * 16 mm bearing: ~225 K
  * 44 mm bearing (85 mm motor): ~82 K
  * 72 mm bearing (150 mm motor): ~50 K
- Explicit convention for `offset_bearing` (diametral vs radial)
- Lamé contact pressure, hub hoop stress, and bearing clearance loss
- Bearing basic rating life L10 (1 vs 2 bearings carrying thrust)
- Mechanical API route `GET /api/mechanical/bearing_fit`
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim.api import app
from motor_ai_sim.bearing_fit import (
    HubMaterial,
    BearingMaterial,
    bearing_fit_analysis,
    bearing_fit_curve,
    compute_contact_pressure_and_stresses,
    compute_fit_loss_dT,
    compute_l10_bearing_life,
)


# ---------------------------------------------------------------------------
# 1. Regression numbers: dT where fit is lost (within +-10%)
# ---------------------------------------------------------------------------

def test_regression_16mm_bearing_dT_loss():
    """16 mm bearing (40 mm motor): dT lost at ~225 K within +-10%."""
    dT = compute_fit_loss_dT(16.0, offset_bearing_mm=0.04, offset_mode="diametral")
    assert 225.0 * 0.90 <= dT <= 225.0 * 1.10
    assert dT == pytest.approx(225.2, rel=0.02)


def test_regression_44mm_bearing_dT_loss():
    """44 mm bearing (85 mm motor): dT lost at ~82 K within +-10%."""
    dT = compute_fit_loss_dT(44.0, offset_bearing_mm=0.04, offset_mode="diametral")
    assert 82.0 * 0.90 <= dT <= 82.0 * 1.10
    assert dT == pytest.approx(81.9, rel=0.02)


def test_regression_72mm_bearing_dT_loss():
    """72 mm bearing (150 mm motor): dT lost at ~50 K within +-10%."""
    dT = compute_fit_loss_dT(72.0, offset_bearing_mm=0.04, offset_mode="diametral")
    assert 50.0 * 0.90 <= dT <= 50.0 * 1.10
    assert dT == pytest.approx(50.05, rel=0.02)


# ---------------------------------------------------------------------------
# 2. Convention: Diametral vs Radial
# ---------------------------------------------------------------------------

def test_offset_bearing_convention():
    """Verify explicit input convention for offset_bearing."""
    # Diametral 0.04 mm -> dT ~ 225.2 K
    dT_diametral = compute_fit_loss_dT(16.0, offset_bearing_mm=0.04, offset_mode="diametral")
    # Radial 0.04 mm means diametral 0.08 mm -> dT doubles to ~450.4 K
    dT_radial = compute_fit_loss_dT(16.0, offset_bearing_mm=0.04, offset_mode="radial")
    assert dT_radial == pytest.approx(2.0 * dT_diametral, rel=1e-5)

    with pytest.raises(ValueError, match="positive"):
        compute_fit_loss_dT(-10.0)


# ---------------------------------------------------------------------------
# 3. Lamé Contact Pressure & Stresses
# ---------------------------------------------------------------------------

def test_lame_contact_pressure_and_stresses():
    """Verify thick-walled cylinder solution (Lamé) for hub on outer ring."""
    # 16 mm OD, 8 mm bore, 0.04 mm diametral interference
    p, hoop, vm, comp_um, clear_um = compute_contact_pressure_and_stresses(
        d_od_mm=16.0,
        d_bore_mm=8.0,
        interference_mm=0.04,
    )
    # Contact pressure must be positive
    assert p > 0.0
    # Hub hoop stress is in tension (positive)
    assert hoop > 0.0
    assert vm > hoop  # von Mises accounts for radial compression -p
    # Bearing outer ring experiences inward compression and raceway clearance loss
    assert comp_um > 0.0
    assert clear_um > 0.0
    # Clearance loss is typically in the order of ~5-25 um for a 40 um interference
    assert 2.0 <= clear_um <= 35.0

    # Zero interference produces zero stress and zero clearance reduction
    p0, hoop0, vm0, comp0, clear0 = compute_contact_pressure_and_stresses(
        d_od_mm=16.0,
        d_bore_mm=8.0,
        interference_mm=0.0,
    )
    assert p0 == 0.0
    assert hoop0 == 0.0
    assert vm0 == 0.0
    assert clear0 == 0.0


def test_fit_curve_sweep():
    """Verify temperature sweep from assembly to max temperature."""
    res = bearing_fit_curve(
        bearing_od_mm=16.0,
        bearing_bore_mm=8.0,
        offset_bearing_mm=0.04,
        t_assembly_c=20.0,
        t_max_c=120.0,
        step_c=10.0,
    )
    assert res["dT_loss_k"] == pytest.approx(225.2, rel=0.02)
    assert res["is_fit_tight_at_t_max"] is True  # 120 C is dT=100 K < 225 K
    assert len(res["curve"]) > 10

    # Check points decreasing in interference
    pts = res["curve"]
    assert pts[0]["temp_c"] == 20.0
    assert pts[0]["interference_um"] == 40.0
    assert pts[0]["p_contact_mpa"] > 0.0
    assert pts[0]["is_tight"] is True

    # At higher temperatures, contact pressure decreases
    assert pts[-1]["p_contact_mpa"] < pts[0]["p_contact_mpa"]


# ---------------------------------------------------------------------------
# 4. ISO 281 L10 Rating Life: 1 vs 2 Bearings
# ---------------------------------------------------------------------------

def test_iso281_bearing_life_sharing():
    """Sharing axial thrust between 2 bearings increases L10 life significantly."""
    # 618/8-2Z: C = 1.33 kN, C0 = 0.585 kN, thrust = 49.05 N, rpm = 10000
    res = compute_l10_bearing_life(
        c_kn=1.33,
        c0_kn=0.585,
        rpm=10000.0,
        thrust_load_n=49.05,
        radial_load_n=1.0,
        fit_is_tight=True,
    )
    single = res["single_bearing"]
    shared = res["two_bearings_shared"]

    assert single["fa_n"] == 49.05
    assert shared["fa_n"] == pytest.approx(24.525, abs=0.01)

    # Life ratio when sharing thrust is ~4x to 8x
    ratio = res["life_ratio_shared_to_single"]
    assert 4.0 <= ratio <= 8.5
    assert shared["l10h_hours"] > single["l10h_hours"] * 4.0


# ---------------------------------------------------------------------------
# 5. Route: GET /api/mechanical/bearing_fit
# ---------------------------------------------------------------------------

def test_api_bearing_fit_endpoint():
    """Verify GET /api/mechanical/bearing_fit route."""
    client = TestClient(app)
    resp = client.get("/api/mechanical/bearing_fit?bearing=618/8-2Z&offset_bearing=0.04&rpm=6000")
    assert resp.status_code == 200
    data = resp.json()
    assert data["bearing_name"] == "618/8-2Z"
    assert "fit" in data
    assert "l10_life" in data
    assert data["fit"]["dT_loss_k"] == pytest.approx(225.2, rel=0.02)
    assert data["l10_life"]["life_ratio_shared_to_single"] >= 4.0
