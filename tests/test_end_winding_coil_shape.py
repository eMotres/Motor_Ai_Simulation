"""Regression test for CAD coil shape end-winding factor vs Fusion 360 / FreeCAD coil lengths."""
from __future__ import annotations

import math
import pytest

from motor_ai_sim.masses import cad_coil_dimensions, end_winding_factor


class MotorParams:
    def __init__(self, stack_length_m: float, num_slots: int = 12, slot_width_m: float = 0.00282,
                 r_stator_in: float = 0.012, slot_height_m: float = 0.0061):
        self.stack_length = float(stack_length_m)
        self.num_slots = int(num_slots)
        self.slot_width_m = float(slot_width_m)
        self.r_stator_in = float(r_stator_in)
        self.slot_height_m = float(slot_height_m)


@pytest.mark.parametrize("config, L_mm, target_coil_len_mm", [
    ("L12", 12.0, 315.916),
    ("L20", 20.0, 427.916),
])
def test_end_winding_factor_reproduces_fusion_coil_lengths_within_3_percent(config, L_mm, target_coil_len_mm):
    """Regression test: CAD coil mean turn length must reproduce Fusion 360 / FreeCAD coil conductor lengths within ±3 %."""
    p = MotorParams(stack_length_m=L_mm * 1e-3)
    geo = {
        "tooth_width": 3.3,
        "wire_width": 2.5,
        "wire_height": 0.6,
        "num_wires_per_slot": 7,
        "insulation_thickness": 0.07,
        "wire_spacing_x": 0.1,
        "wire_spacing_y": 0.1,
        "wire_beng_r": 1.72,
        "coil_straight_extra": 1.0,      # CAD profile cad/reference/CIANO14_40_12.json (coil_straight_len - motor_length)
        "coil_lead_total": 9.1,          # coil_lead_ext 4.1 + coil_lead_len 5
    }
    dims = cad_coil_dimensions(p, geo)
    calc_len = dims["total_coil_length_mm"]
    err_pct = (calc_len - target_coil_len_mm) / target_coil_len_mm * 100.0

    # Strict requirement: within ±3 %
    assert abs(err_pct) < 3.0

    # Check k_end
    k_end = end_winding_factor(p, geo, method="cad_coil")
    assert k_end == pytest.approx(dims["k_end_cad"], rel=1e-7)
    # Check that k_end * 2 * L * turns == calc_len
    assert k_end * (2.0 * L_mm) * 7 == pytest.approx(calc_len, rel=1e-7)


def test_legacy_semicircle_mode_available():
    """Old formula is available behind method='semicircle' / 'legacy'."""
    p = MotorParams(stack_length_m=0.012)
    geo = {
        "tooth_width": 3.3,
        "wire_width": 2.5,
    }
    k_semi = end_winding_factor(p, geo, method="semicircle")
    # span = 3.3 + 2.5 = 5.8 mm; k_semi = (pi*5.8/2 + 12) / 12 = 1.7592
    assert k_semi == pytest.approx((math.pi * 5.8 / 2.0 + 12.0) / 12.0, rel=1e-6)

    k_cad = end_winding_factor(p, dict(geo, wire_height=0.6, num_wires_per_slot=7, insulation_thickness=0.07,
                                       wire_spacing_x=0.1, wire_spacing_y=0.1, wire_beng_r=1.72,
                                       coil_straight_extra=1.0, coil_lead_total=9.1), method="cad_coil")
    assert k_cad > k_semi  # CAD coil accounts for bends, overhangs and the lead tails, so it is longer
    assert end_winding_factor(p, geo) == k_semi       # auto without CAD coil parameters = the old factor


def test_benchmarks_85mm_and_150mm_within_3_percent():
    """Verify other motor families (85 mm and 150 mm) reproduce CAD BOM lengths within ±3 %."""
    # 85 mm L13: CIANO28 85 20SW1200 BOM target = 1005.3 mm
    p85 = MotorParams(stack_length_m=0.013, num_slots=24, slot_width_m=0.004)
    geo85 = {
        "tooth_width": 5.0,
        "wire_width": 3.5,
        "wire_height": 0.3,
        "num_wires_per_slot": 18,
        "insulation_thickness": 0.06,
        "wire_spacing_x": 0.1,
        "wire_spacing_y": 0.07,
        "wire_beng_r": 2.56,
        "coil_straight_extra": 0.5,
        "coil_lead_total": 11.0,
    }
    dims85 = cad_coil_dimensions(p85, geo85)
    err85 = (dims85["total_coil_length_mm"] - 1005.3) / 1005.3 * 100.0
    assert abs(err85) < 3.0

    # 150 mm L35: CIANO28 150 35 BOM target = 1604.58 mm
    p150 = MotorParams(stack_length_m=0.035, num_slots=24, slot_width_m=0.007)
    geo150 = {
        "tooth_width": 9.8,
        "wire_width": 5.0,
        "wire_height": 0.6,
        "num_wires_per_slot": 14,
        "insulation_thickness": 0.15,
        "wire_spacing_x": 0.1,
        "wire_spacing_y": 0.13,
        "wire_beng_r": 2.5,
        "coil_straight_extra": 1.0,
        "coil_lead_total": 20.0,
    }
    dims150 = cad_coil_dimensions(p150, geo150)
    err150 = (dims150["total_coil_length_mm"] - 1604.58) / 1604.58 * 100.0
    assert abs(err150) < 3.0


def test_edge_cases():
    """Edge cases: zero/negative stack length and missing geo."""
    p_zero = MotorParams(stack_length_m=0.0)
    assert end_winding_factor(p_zero) == 1.0

    p_neg = MotorParams(stack_length_m=-0.01)
    assert end_winding_factor(p_neg) == 1.0

    p_valid = MotorParams(stack_length_m=0.02)
    # Without the CAD coil parameters the old semicircle factor is used (no guessed coil shape)
    assert end_winding_factor(p_valid, None) == end_winding_factor(p_valid, None, method="semicircle")
    with pytest.raises(ValueError):
        end_winding_factor(p_valid, {"tooth_width": 3.3}, method="cad_coil")
