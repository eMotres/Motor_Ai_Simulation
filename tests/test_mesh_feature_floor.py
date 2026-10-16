"""Mesh-tab slider cap == solver clamp (2026-09-28, Ø12 CIANO14 slider report)."""
from motor_ai_sim.simulation.fem_solver_2d import mesh_feature_floor_mm


def test_ciano14_12_40_cap_is_half_the_tooth():
    geo = {"tooth_width": 1.0, "slot_width": 1.842}
    assert mesh_feature_floor_mm(geo, 0.3) == 0.5
    assert mesh_feature_floor_mm(geo, 0.3, hi_fidelity=True) == 0.3  # max(0.3, 0.25)


def test_value_below_cap_is_honoured_by_min():
    cap = mesh_feature_floor_mm({"tooth_width": 1.0, "slot_width": 1.842}, 0.3)
    for requested in (0.1, 0.3, 0.45):
        assert min(requested, cap) == requested


def test_big_motor_and_missing_features():
    assert mesh_feature_floor_mm({"tooth_width": 15.6, "slot_width": 20.0}, 0.3) == 7.8
    assert mesh_feature_floor_mm({}, 0.3) is None
    assert mesh_feature_floor_mm({"tooth_width": None, "slot_width": "x"}, 0.3) is None
