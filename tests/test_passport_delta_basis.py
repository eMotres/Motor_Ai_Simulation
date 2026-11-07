"""Delta winding-current basis of the passport pipeline (L155, 2026-10-13) and the L155 card transform."""
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from motor_ai_sim.passport_v1 import psimap as PM  # noqa: E402


def test_voltage_limit_follows_snapshot_basis():
    try:
        PM.set_winding_basis({})
        star = PM.v_phase_limit(750.0, 0.95)
        assert abs(star - 0.95 * 750.0 / math.sqrt(3.0)) < 1e-9
        PM.set_winding_basis({"current_basis": {"delta": True}})
        # the delta winding carries the line voltage: m * Vdc
        assert abs(PM.v_phase_limit(750.0, 0.95) - 0.95 * 750.0) < 1e-9
    finally:
        PM.set_winding_basis({})


def test_spec_loads():
    from motor_ai_sim.passport_v1.spec import load_spec
    s = load_spec(ROOT / "config" / "passport_specs" / "CIANO10_200_opt.yaml")
    assert list(s["machines"]) == ["L155"]
    assert s["plan"]["m_margin"] == 0.95


def test_equivalent_star_transform():
    import build_l155_catalog_card as B
    k = math.sqrt(3.0)
    full = {
        "card": {"constants_cold": {"psi_PM_Wb": {"value": 0.0554}, "R_phase_ohm": {"value": 0.003},
                                    "Ld_bench_mH": {"value": 0.09}, "Lq_bench_mH": {"value": 0.12}}},
        "hot_map": {"mtpa": [{"I": 100.0}], "points": [{"I": 100.0, "i_d": -10.0, "i_q": 20.0,
                                                        "psi_d": 0.06, "psi_q": -0.01}]},
        "demag": {"mtpa_retention": [[100.0, 99.9]]},
        "loss_grid": {"plan": {"R_hot_ohm": 0.0045},
                      "points": [{"I_rms": 100.0, "i_d": -10.0, "i_q": 20.0}, {"gamma": None}]}}
    eq = B.to_equivalent_star(full)
    assert abs(eq["card"]["constants_cold"]["R_phase_ohm"]["value"] - 0.001) < 1e-12
    assert abs(eq["card"]["constants_cold"]["Ld_bench_mH"]["value"] - 0.03) < 1e-12
    assert abs(eq["hot_map"]["mtpa"][0]["I"] - round(100.0 * k, 6)) < 1e-9
    assert abs(eq["hot_map"]["points"][0]["psi_d"] - 0.06 / k) < 1e-12
    assert abs(eq["loss_grid"]["plan"]["R_hot_ohm"] - 0.0015) < 1e-12
    assert full["card"]["constants_cold"]["R_phase_ohm"]["value"] == 0.003     # input untouched
