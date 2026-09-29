"""Σm·c_p per node — the thermal mass a duty cycle is integrated on.

Pinned on the machine the whole robot-joint feature was written for: the Ø85 /
13 mm ``CIANO28 85 20SW1200 / L13``, duty "rated 120С wire 80C NdFeB".  Its mass
rows are copied verbatim below rather than read out of ``config/`` — a unit test
must not depend on the catalogue the user happens to have open — and one test
DOES read the catalogue, to say so loudly if the machine behind these numbers
has moved.

What is claimed here:

  (a) the four capacities are the machine's own masses times the right c_p, and
      the SHAFT is in them: a customer-supplied part is billed at 0 kg in the
      active mass (it is not ours) and at its full 10 g here (heat does not care
      who bought it);
  (b) a specific heat that came from a DEFAULT says so.  20SW1200 carries
      ``specific_heat: null``, so both iron nodes are defaults, and a report
      that quoted 470 J/kg·K as if it were a datasheet value would be lying by
      omission;
  (c) a mass row this model cannot place is a LOUD failure, not a silently
      dropped capacity — a dropped part is a time constant silently shortened,
      and "the winding reaches 200 °C in 8 s" would still look reasonable.
"""
from __future__ import annotations

import math
import pathlib

import pytest

from motor_ai_sim.thermal_capacities import (
    CP_DEFAULT, NODES, CapacityError, capacities_j_per_k, capacity_rows,
    cp_sources, part_capacities, total_capacity_j_per_k)

#: The mass rows of the L13's rated duty, verbatim from
#: ``config/dies/CIANO28 85 20SW1200/L13.yaml`` (duties[0].summary), 2026-09-14.
L13_MASS_ROWS = [
    {"name": "Stator core (20SW1200)", "material": "electrical steel",
     "density_kg_m3": 7650, "volume_cm3": 16.5, "mass_kg": 0.126,
     "note": "CAD section 1335 mm² × stack, lamination k_f=0.950"},
    {"name": "Copper windings (Cu)", "material": "copper",
     "density_kg_m3": 8933, "volume_cm3": 12, "mass_kg": 0.107,
     "note": "measured copper section 454 mm² × stack × k_end 2.027"},
    {"name": "Magnets (F52SH_30C)", "material": "NdFeB",
     "density_kg_m3": 7500, "volume_cm3": 9.3, "mass_kg": 0.07,
     "note": "CAD magnet polygons 713 mm² × stack"},
    {"name": "Rotor back-iron (20SW1200)", "material": "electrical steel",
     "density_kg_m3": 7650, "volume_cm3": 8.4, "mass_kg": 0.064,
     "note": "CAD section 681 mm² × stack, lamination k_f=0.950"},
    {"name": "Shaft (Aluminium_6061) — customer-supplied", "material": "shaft",
     "density_kg_m3": 2700, "volume_cm3": 3.9, "mass_kg": 0,
     "note": "customer-supplied — modelled as Aluminium_6061 for the field, "
             "not included in mass (0.010 kg modelled); hollow shaft tube "
             "297 mm² × stack — NOT in active mass",
     "state": "reference", "counted": False, "mass_modelled_kg": 0.01},
]

L13_SUMMARY = {"mass_components": L13_MASS_ROWS,
               "part_states": {"shaft": "reference"}}
L13_MATERIALS = {"magnet": "F52SH_120C", "stator_core": "20SW1200",
                 "rotor_core": "20SW1200"}
L13_PARTS = {"shaft": "reference"}

DIE_FILE = (pathlib.Path(__file__).resolve().parents[1]
            / "config" / "dies" / "CIANO28 85 20SW1200" / "L13.yaml")


@pytest.fixture(scope="module")
def caps():
    return part_capacities(L13_SUMMARY, L13_MATERIALS, L13_PARTS)


# ---------------------------------------------------------------------------
# (a) the four numbers
# ---------------------------------------------------------------------------

def test_the_four_capacities_are_the_machine(caps):
    """41.2 / 59.2 / 39.0 / 32.2 J/K — mass × c_p, part by part."""
    C = capacities_j_per_k(caps)
    assert C["winding"] == pytest.approx(41.2, abs=0.1)    # 0.107 kg × 385
    assert C["stator"] == pytest.approx(59.2, abs=0.1)     # 0.126 kg × 470
    assert C["magnet"] == pytest.approx(32.2, abs=0.1)     # 0.070 kg × 460
    # rotor = back-iron 0.064×470 + shaft 0.010×896
    assert C["rotor"] == pytest.approx(30.08 + 8.96, abs=0.1)
    assert total_capacity_j_per_k(caps) == pytest.approx(sum(C.values()))
    assert set(caps) == set(NODES)


def test_the_reference_shaft_is_billed_at_its_modelled_mass(caps):
    """0 kg in the accounting, 10 g in the physics — and the row says which."""
    shaft = [r for r in capacity_rows(caps) if r["part"] == "shaft"]
    assert len(shaft) == 1
    row = shaft[0]
    assert row["node"] == "rotor"
    assert row["mass_kg"] == pytest.approx(0.010)
    assert "modelled" in row["mass_source"]
    assert row["C_J_per_K"] == pytest.approx(8.96, abs=0.01)


def test_every_node_is_present_even_with_no_parts():
    """A machine with no sleeve still answers "no sleeve", not KeyError."""
    caps = part_capacities(L13_SUMMARY, L13_MATERIALS, L13_PARTS)
    for n in NODES:
        assert "C_J_per_K" in caps[n] and "parts" in caps[n]
    # …and this machine's magnet node holds exactly the magnets (no sleeve row)
    assert [p["part"] for p in caps["magnet"]["parts"]] == ["magnet"]


# ---------------------------------------------------------------------------
# (b) a default says it is a default
# ---------------------------------------------------------------------------

def test_the_blank_steel_card_is_reported_as_a_default(caps):
    """20SW1200 has ``specific_heat: null`` — so the stator node is a DEFAULT."""
    src = cp_sources(caps)
    assert src["stator"] == "default"
    # The rotor mixes a defaulted steel with a library aluminium, and says so
    # rather than picking one of the two words.
    assert src["rotor"] == "mixed"
    # The magnet and the copper cards DO carry c_p, so those are library reads.
    assert src["magnet"] == "library"
    assert src["winding"] == "library"
    stator = caps["stator"]["parts"][0]
    assert stator["cp_J_per_kg_K"] == CP_DEFAULT["electrical steel"]
    assert stator["material"] == "20SW1200"


def test_a_supplied_datasheet_number_overrides_and_is_named():
    """The answer to "our steel is 460, not your 470"."""
    caps = part_capacities(L13_SUMMARY, L13_MATERIALS, L13_PARTS,
                           cp_overrides={"stator_core": 460.0})
    assert caps["stator"]["C_J_per_K"] == pytest.approx(0.126 * 460.0, abs=1e-3)
    assert cp_sources(caps)["stator"] == "given"


def test_the_assignment_wins_over_the_label():
    """The row says F52SH_30C, the configuration says F52SH_120C — the
    CONFIGURATION is what the machine is made of."""
    caps = part_capacities(L13_SUMMARY, L13_MATERIALS, L13_PARTS)
    assert caps["magnet"]["parts"][0]["material"] == "F52SH_120C"


# ---------------------------------------------------------------------------
# (c) loud failures
# ---------------------------------------------------------------------------

def test_an_unknown_mass_row_is_refused_by_name():
    bad = {"mass_components": L13_MASS_ROWS + [
        {"name": "Housing (Aluminium_6061)", "material": "aluminium",
         "mass_kg": 0.4, "volume_cm3": 148.0}]}
    with pytest.raises(CapacityError) as exc:
        part_capacities(bad, L13_MATERIALS, L13_PARTS)
    assert "Housing" in str(exc.value)


def test_no_mass_rows_at_all_is_refused_with_what_to_do():
    with pytest.raises(CapacityError) as exc:
        part_capacities({"mass_components": []})
    assert "Re-run" in str(exc.value)


def test_an_excluded_part_carries_no_capacity():
    """An excluded part is not there — not "there with zero mass"."""
    caps = part_capacities(L13_SUMMARY, L13_MATERIALS,
                           {"shaft": "excluded"})
    assert [p["part"] for p in caps["rotor"]["parts"]] == ["rotor_core"]
    assert caps["rotor"]["C_J_per_K"] == pytest.approx(30.08, abs=0.01)


# ---------------------------------------------------------------------------
# the catalogue behind the numbers above
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not DIE_FILE.exists(), reason="the L13 is not in this "
                                                  "checkout's catalogue")
def test_the_catalogue_still_holds_the_machine_these_numbers_came_from():
    """If the L13's masses move, THIS is the test that says so.

    The rows above are a copy; a copy that silently drifts from the machine is
    worse than no fixture at all, because every number in this file would go on
    looking right.
    """
    import yaml

    doc = yaml.safe_load(DIE_FILE.read_text(encoding="utf-8"))
    duty = next(d for d in doc["duties"] if d["name"].startswith("rated 120"))
    live = duty["summary"]["mass_components"]
    assert len(live) == len(L13_MASS_ROWS)
    for got, want in zip(live, L13_MASS_ROWS):
        assert got["name"] == want["name"]
        assert math.isclose(float(got["mass_kg"]), float(want["mass_kg"]),
                            abs_tol=1e-6)
        assert math.isclose(float(got.get("mass_modelled_kg") or 0.0),
                            float(want.get("mass_modelled_kg") or 0.0),
                            abs_tol=1e-6)
