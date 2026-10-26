"""A machine with no retaining sleeve says nothing about one (owner 2026-10-26).

    «если нет слива, то не надо о нём ничего писать: Retaining sleeve M40X_UD_60 - просто выкинуть всю информацию»

Pinned: the one helper (``motor_ai_sim.sleeve.has_sleeve``) and the report tables that used to print a sleeve line for a
rotor whose ``sleeve_thickness`` is 0 while its material assignment still carries a sleeve card.  With a sleeve nothing changes.
"""
from __future__ import annotations

from motor_ai_sim import report as R
from motor_ai_sim.sleeve import has_sleeve, sleeve_thickness_mm


def test_helper_thickness_decides():
    assert has_sleeve(1.2) and has_sleeve("0.5") and has_sleeve({"sleeve_thickness": 2})
    assert not has_sleeve(0) and not has_sleeve(0.0) and not has_sleeve(-1) and not has_sleeve("0")
    assert not has_sleeve(None) and not has_sleeve("") and not has_sleeve(True) and not has_sleeve(float("nan"))
    assert not has_sleeve({"sleeve_thickness": 0, "materials": {"sleeve": "M40X_UD_60"}})   # the assignment alone is not a sleeve
    assert has_sleeve({"geometry": {"sleeve_thickness_mm": 0.8}}) and not has_sleeve({"geo": {"sleeve_thickness": 0}})
    assert sleeve_thickness_mm({"sleeve_thickness": "1.5"}) == 1.5 and sleeve_thickness_mm({}) is None


def test_helper_reads_a_run_summary_without_geometry():
    assert not has_sleeve({})
    assert not has_sleeve({"P_sleeve_W": 0.0, "rotor_inertia": {"sleeve": 0.0}, "mass_components": [{"name": "Magnets", "mass_kg": 0.1}]})
    assert has_sleeve({"mass_components": [{"name": "Sleeve (CFRP)", "mass_kg": 0.02}]})
    assert has_sleeve({"rotor_inertia": {"sleeve": 1e-5}})
    assert has_sleeve({"P_sleeve_W": 0.3})
    assert has_sleeve({"sleeve_hoop": {"sigma_hoop_MPa": 400}})


def test_materials_table_drops_the_sleeve_row_of_a_machine_without_one():
    mats = {"sleeve": "M40X_UD_60", "magnet": "N52UH_150C", "stator_core": "B15AHV950M", "rotor_core": "B15AHV950M"}
    rows = R.material_rows(mats, {}, {"sleeve_thickness": 0}, {})
    assert "Retaining sleeve" not in [r[0] for r in rows]
    assert "M40X_UD_60" not in str(rows) and "not installed" not in str(rows)
    with_sleeve = R.material_rows(mats, {}, {"sleeve_thickness": 1.2}, {})
    row = next(r for r in with_sleeve if r[0] == "Retaining sleeve")
    assert row[1] == "M40X_UD_60" and row[2] == "hoop-wound carbon fibre"


def test_geometry_table_has_no_sleeve_cell_without_a_sleeve():
    none = R.geometry_rows({"air_gap": 3.2, "sleeve_thickness": 0}, {"n_series": 2}, None, {})
    assert "Sleeve thickness" not in str(none) and "none" not in [c for r in none for c in r]
    some = R.geometry_rows({"air_gap": 3.2, "sleeve_thickness": 1.2}, {"n_series": 2}, None, {})
    assert any(r[0] == "Sleeve thickness" and r[2] == "mm" for r in some)
    assert len(none) == len(some)                                   # the row stays, only the empty cell pair differs
    assert any("Series coils" in r for r in none)


def test_inertia_and_loss_tables_drop_the_sleeve_without_one():
    inertia = {"J_kg_cm2": 10.0, "rotor_iron": 5e-4, "magnet": 4e-4, "shaft": 1e-4, "sleeve": 0.0}
    rows = R.rotor_inertia_rows({"rotor_inertia": inertia})
    assert [r[0] for r in rows[1:-1]] == ["Rotor iron", "Magnet", "Shaft"]
    sleeved = R.rotor_inertia_rows({"rotor_inertia": {**inertia, "sleeve": 5e-5}})
    assert "Sleeve" in [r[0] for r in sleeved]
    em = {"P_loss_total_W": 100.0, "P_stranded_W": 60.0, "P_core_W": 30.0, "P_mag_W": 4.0, "P_shaft_W": 1.0, "P_sleeve_W": 0.0}
    assert "sleeve" not in str(R.em_loss_rows(em, None)).lower()
    em_s = {**em, "P_sleeve_W": 0.2, "mass_components": [{"name": "Sleeve", "mass_kg": 0.02}]}
    assert "Sleeve (eddy)" in str(R.em_loss_rows(em_s, None))
