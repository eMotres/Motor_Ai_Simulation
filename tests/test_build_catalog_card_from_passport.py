"""The Ø85 L13 catalogue card is assembled from the full passport without a solve."""
import importlib.util
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "bcc", ROOT / "scripts" / "build_catalog_card_from_passport.py")
bcc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bcc)

from tests.private_fixtures import need_private

# The full passport of the Ø85 (a closed machine) is private data since 2026-10-19; the test skips without the private checkout.

STATIC = {
    "diameter_mm": 85, "poles": 28, "slots": 24,
    "passport": {"fit": {"slotHeight_mm": 7.4}, "geo": {"numPoles": 28},
                 "passport": {"N0": 18.0, "L0_mm": 13.0, "wireH0_mm": 0.3, "I0_A": 25.88,
                              "rpm0": 1000, "nP0": 1.0, "role": "motor", "mass0_kg": 0.397,
                              "endWindFrac": 0.5, "k_end0": 2.0}},
}


def test_card_is_not_reference_only_and_carries_20c_constants():
    FULL = need_private("docs", "data", "passport_d85", "passport_L13.json")
    full = json.loads(FULL.read_text(encoding="utf-8"))
    card = bcc.build_card(full, STATIC, "cat_x", "CIANO28 85 20SW1200 L13", "o@example.com")
    assert "reference_only" not in card and "visibility" not in card
    p = card["passport"]["passport"]
    cold = full["card"]["constants_cold"]
    # Kt at I0 equals the 20 degC constant, Kv follows the 20 degC psi_PM
    assert math.isclose(p["T0_Nm"] / p["I0_A"], cold["Kt_rated_Nm_per_A"]["value"], rel_tol=1e-9)
    kv = 60.0 / (2 * math.pi * 14 * math.sqrt(3) * (p["Vemf0_peak_V"] / (2 * math.pi * 14 * 1000 / 60)))
    assert math.isclose(kv, cold["Kv_rpm_per_V_line"]["value"], rel_tol=1e-6)
    assert math.isclose(p["R0_ohm"], cold["R_phase_ohm"]["value"], rel_tol=1e-9)
    assert math.isclose(p["ldq0"]["Ld_mH"], cold["Ld_bench_mH"]["value"], rel_tol=1e-9)
    assert p["ldq0"]["method"].startswith("differential")
    g = p["loss_grid"]
    assert len(g["Pfe_W"]) == len(g["I_A"]) and all(len(r) == len(g["rpm"]) for r in g["Pfe_W"])
    assert all(v is not None for v in p["current"]["V_peak_V"])
    assert p["current"]["I_A"] == sorted(p["current"]["I_A"])
    assert p["end3d"]["k_T"] == full["stage2_3d"]["k_T"]["value"]
