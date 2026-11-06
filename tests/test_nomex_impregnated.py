"""Nomex stays dry for every motor; the impregnated liner is a card of its own (owner 2026-11-06)."""
import pytest

from motor_ai_sim import materials as M
from motor_ai_sim.report import current_density_limit


def test_nomex_keeps_the_dry_value():
    assert M.get_insulator("Nomex").thermal_conductivity == pytest.approx(0.14)


def test_the_impregnated_card_is_the_fitted_value():
    card = M.get_insulator("Nomex_impregnated")
    assert card.thermal_conductivity == pytest.approx(0.25)
    assert card.specific_heat == pytest.approx(M.get_insulator("Nomex").specific_heat)


def test_the_impregnated_liner_still_counts_as_organic():
    dry, _ = current_density_limit("liquid", {"slot_insulation": "Nomex"})
    wet, _ = current_density_limit("liquid", {"slot_insulation": "Nomex_impregnated"})
    assert wet == dry
