"""The mass model — pinned per-component masses for the two live machines.

``masses.compute_masses`` is the SINGLE SOURCE for every mass in the app (the
Simulation card, torque-per-mass in the optimizer objective, Compare), so a
change to it moves every torque-density in the product.  These pins are the
guard: each component is billed as

    (CAD cross-section) × (stack length) × (lamination k_f, cores only) × ρ(material)

and the numbers below were verified component-by-component against the user's
ANSYS model of the 150 mm 24s/28p (Motres_CIANO281_150), whose own active-mass
expression is magnets + copper·res_add + stator iron + rotor holder, no shaft,
no lamination factor, 7700 kg/m³ for every iron.

The geometries are FROZEN copies (config/motor_config.yaml as of 2026-08-04 and
the ``my_40mm_last`` preset), so these tests describe the mass model and not
whatever design the shared config happens to hold today.
"""
from __future__ import annotations

import math

import pytest

from motor_ai_sim.masses import (compute_masses, cad_areas_m2, end_winding_factor,
                                 parametric_areas_m2, part_material)
from motor_ai_sim.simulation.geometry_2d import params_from_config, merge_geo_override

# Materials pinned explicitly: the test describes the MODEL, not the current
# material assignment in the shared config.
MATS = {"stator_core": "B15AHV950M", "rotor_core": "B15AHV950M",
        "magnet": "F45SH_120C", "slot": "copper", "shaft": "Aluminium_6061"}

#: 150 mm 24s/28p, 35 mm stack — the machine the ANSYS cross-check was run on.
G150 = {
    "stator_diameter": 150, "slot_height": 14, "core_thickness": 4.2,
    "num_seg": 4, "num_slots_per_segment": 6, "num_poles_per_segment": 7,
    "air_gap": 0.5, "tooth_width": 9.2, "tooth2_width": 5.5, "cut_width": 6,
    "insulation_thickness": 0.15, "wire_width": 5, "wire_height": 0.6,
    "wire_spacing_x": 0.1, "wire_spacing_y": 0.13, "num_wires_per_slot": 14,
    "wire_split": 1, "slot_hs": 0.2, "magnet_height": 16,
    "rotor_house_height": 1.2, "shaft_height": 3, "magnet_fill_down": 0.9,
    "magnet_fill_up": 0.44, "magnet_fill_radius": 2.5, "magnet_up_gap": 2,
    "rotor_hole": 0.6, "magnet_down_height": 1.8, "magnet_lamination": 0,
    "stator_fillet_r": 3.5, "stator_fillet_r1": 1.2, "rotor_fill_r": 0.5,
    "motor_length": 35, "stator_outer_radius": 75,
    "stator_inner_radius": 56.8, "num_slots": 24, "num_poles": 28,
    "angle_slot": 15.0, "angle_pole": 360 / 28,
    "slot_pitch": 2 * math.pi / 24, "pole_pitch": 2 * math.pi / 28,
    "rotor_outer_radius": 56.3, "rotor_inner_radius": 39.1,
    "slot_width": 5.5, "shaft_diameter": 5,
}

#: 12s/14p 40 mm, 12 mm stack — preset ``my_40mm_last``.
G40 = {
    "stator_diameter": 40, "slot_height": 6, "core_thickness": 1.9,
    "num_seg": 2, "num_slots_per_segment": 6, "num_poles_per_segment": 7,
    "air_gap": 0.2, "tooth_width": 3.1, "tooth2_width": 1.7, "cut_width": 1,
    "insulation_thickness": 0.06, "wire_width": 2.5, "wire_height": 0.6,
    "wire_spacing_x": 0.1, "wire_spacing_y": 0.1, "num_wires_per_slot": 7,
    "wire_split": 1, "slot_hs": 0.267, "magnet_height": 5.9,
    "rotor_house_height": 1.2, "shaft_height": 2, "magnet_fill_down": 0.85,
    "magnet_fill_up": 0.4, "magnet_fill_radius": 0.2, "magnet_up_gap": 0.2,
    "rotor_hole": 0.8, "magnet_down_height": 1.1, "magnet_lamination": 0,
    "stator_fillet_r": 1, "stator_fillet_r1": 0.15, "rotor_fill_r": 0,
    "motor_length": 12, "stator_outer_radius": 20,
    "stator_inner_radius": 12.1, "num_slots": 12, "num_poles": 14,
    "angle_slot": 30, "angle_pole": 360 / 14,
    "slot_pitch": 2 * math.pi / 12, "pole_pitch": 2 * math.pi / 14,
    "rotor_outer_radius": 11.9, "rotor_inner_radius": 4.8,
    "slot_width": 2.82, "shaft_diameter": 5,
}


def _masses(G):
    p = params_from_config(geo_override=G)
    geo = merge_geo_override(dict(G150), G)      # frozen base: hermetic
    return compute_masses(p, geo, materials=MATS)


@pytest.fixture(scope="module")
def m150():
    return _masses(G150)


@pytest.fixture(scope="module")
def m40():
    return _masses(G40)


# ── per-component pins ───────────────────────────────────────────────────────

@pytest.mark.parametrize("part,kg", [
    ("stator", 1.20755),  # 4934.4 mm² CAD section × 35 mm × k_f 0.92 × 7600
    ("rotor",  0.53826),  # 2199.5 mm² — the holder AND the ribs between magnets
    ("cu",     0.5160),   # 1008 mm² measured copper × 35 mm × k_end 1.6373 × 8933
    ("mag",    0.7225),   # 2752.3 mm² of CAD magnet polygons (28 × 98.3 mm²)
    ("shaft",  0.0670),   # hollow 3 mm tube, 708.7 mm² — not a solid disc
    ("active", 2.98430),  # iron + copper + magnets: the ANSYS basis
    ("total",  3.05127),  # active + shaft: the torque-per-mass divisor
])
def test_150mm_component_masses(m150, part, kg):
    assert m150[part] == pytest.approx(kg, abs=5e-4)


@pytest.mark.parametrize("part,kg", [
    ("stator", 0.0323544),
    ("rotor",  0.0144743),
    ("cu",     0.023406),
    ("mag",    0.017486),
    ("shaft",  0.001547),
    ("active", 0.0877226),
    ("total",  0.0892696),
])
def test_40mm_component_masses(m40, part, kg):
    assert m40[part] == pytest.approx(kg, abs=5e-6)


def test_active_plus_shaft_is_total(m150, m40):
    for m in (m150, m40):
        assert m["active"] + m["shaft"] == pytest.approx(m["total"], rel=1e-12)
        # active is EXACTLY the four EM parts — no shaft, no housing
        assert (m["stator"] + m["rotor"] + m["cu"] + m["mag"]
                == pytest.approx(m["active"], rel=1e-12))


# ── the three corrections, each guarded on its own ───────────────────────────

def test_lamination_factor_comes_from_the_material(m150):
    """The MASS of a laminated core is k_f steel by volume, and k_f is the
    material's own stacking_factor — the same number fem_solver_2d folds into the
    B-H curve.  Nothing here may hard-code it."""
    rho, kf, name = part_material("stator_core", MATS)
    from motor_ai_sim.materials import get_material
    lib = get_material("steel", "B15AHV950M")
    assert name == "B15AHV950M"
    assert kf == pytest.approx(lib.stacking_factor)        # 0.925
    assert rho == pytest.approx(lib.density)               # 7600, not 7650/7700
    assert m150["k_f_stator"] == pytest.approx(kf)
    # and it is actually applied: mass = A · L · k_f · rho
    assert m150["stator"] == pytest.approx(
        m150["A_stator"] * 0.035 * kf * rho, rel=1e-9)


def test_a_solid_core_gets_no_stacking_factor():
    """An SMC (form: solid) is not a stack — k_f = 1 even though the record
    carries a stacking_factor field."""
    _rho, kf, _n = part_material("stator_core", {"stator_core": "Somaloy_700HR_5P"})
    assert kf == 1.0


def test_sections_are_the_cad_polygons_not_an_annulus(m150):
    """The magnets are 28 shaped CAD polygons, not (rotor annulus × fill_down):
    the annulus form over-read them by ~69 % and was the single largest error in
    the old mass.  Same for the rotor iron, which the CAD measures at ~3× the old
    (shaft-ring) formula because it includes the ribs between the magnets."""
    A = cad_areas_m2(merge_geo_override(dict(G150), G150))
    assert A is not None and m150["area_source"].startswith("CAD")
    assert m150["A_mag"] == pytest.approx(A["magnet"])
    p = params_from_config(geo_override=G150)
    annulus = math.pi * (p.r_rotor_out ** 2 - p.r_rotor_in ** 2) * p.magnet_fill_fraction
    assert A["magnet"] < 0.65 * annulus          # measured 0.593 of it
    # the shaft is a tube (r_shaft_in … r_rotor_in), never a solid disc
    assert A["shaft"] == pytest.approx(
        math.pi * (p.r_rotor_in ** 2 - p.r_shaft_in ** 2), rel=0.02)


def test_copper_is_the_measured_section_times_k_end(m150):
    """Copper mass, phase R and copper loss all scale the SAME measured in-slot
    section by the SAME k_end — the mass may not use the nominal rectangle when
    the CAD lays down something else."""
    p = params_from_config(geo_override=G150)
    geo = merge_geo_override(dict(G150), G150)
    assert m150["k_end"] == pytest.approx(end_winding_factor(p, geo))
    assert m150["A_cu"] == pytest.approx(cad_areas_m2(geo)["copper"])
    assert m150["cu"] == pytest.approx(
        m150["A_cu"] * 0.035 * m150["k_end"] * 8933.0, rel=1e-9)


def test_k_end_matches_the_ansys_end_turn_form():
    """k_end = (π·(tooth_w + wire_w)/2 + L)/L — the same half-loop the ANSYS model
    uses for res_add (theirs measures the loop over the insulated coil width, so
    it reads ~1 % higher; the physics is the same)."""
    p = params_from_config(geo_override=G150)
    k = end_winding_factor(p, G150)
    assert k == pytest.approx((math.pi * (9.2 + 5.0) * 1e-3 / 2 + 0.035) / 0.035)
    ansys_res_add = (math.pi * (9.2 / 2 + 5.4 / 2) * 1e-3 + 0.035) / 0.035
    assert k == pytest.approx(ansys_res_add, rel=0.02)


def test_parametric_fallback_is_the_same_machine():
    """When the CAD cannot build a candidate the fallback still has to describe a
    motor: a magnet BAND that is magnet_height deep, rotor iron filling the rest
    of it, and a hollow shaft tube.  Within a few percent of the CAD on the parts
    that are simple annuli, and never the old solid-disc shaft."""
    p = params_from_config(geo_override=G150)
    geo = merge_geo_override(dict(G150), G150)
    A_cad = cad_areas_m2(geo)
    A_par = parametric_areas_m2(p, geo)
    assert A_par["shaft"] == pytest.approx(A_cad["shaft"], rel=0.02)
    assert A_par["stator"] == pytest.approx(A_cad["stator"], rel=0.05)
    # magnet band + its iron together must match the CAD's band to a few percent
    assert (A_par["magnet"] + A_par["rotor"]) == pytest.approx(
        A_cad["magnet"] + A_cad["rotor"], rel=0.06)


def test_ansys_cross_check_150mm(m150):
    """Rebuild the ANSYS expression on OUR sections — their densities (7700 for
    every iron AND for the magnets), their res_add, and no lamination factor —
    and it lands within 3 % of their 3.0975 kg.  What is left is geometry detail
    (their magnet pockets / holder cut-outs), not a modelling difference.

    Our own active mass is LOWER than theirs by design: the lamination factor is
    real steel that is not there, and F45SH is 7500 kg/m³, not 7700.
    """
    L = 0.035
    res_add = (math.pi * (9.2 / 2 + 5.4 / 2) * 1e-3 + L) / L
    ansys_basis = L * (
        m150["A_mag"] * 7700.0
        + m150["A_cu"] * 8933.0 * res_add
        + (m150["A_stator"] + m150["A_rotor"]) * 7700.0)
    assert ansys_basis == pytest.approx(3.0975, rel=0.03)
    # and our number sits below it by the lamination + density difference only
    assert 0.95 < m150["active"] / 3.0975 < 1.0


# ─────────────────────────────────────────────────────────────────────────────
# The COST tab used to carry a SECOND mass model
# ─────────────────────────────────────────────────────────────────────────────
# ``modules/cost.py`` had its own density table (copper 8960 / magnet 7500 /
# steel 7650 / shaft 7850) applied to each GeometryIR region's area × stack.
# Three things that model could not know, all of which move a price:
#   * end windings — the copper anyone BUYS is k_end × the in-slot copper;
#   * the lamination fill factor — a laminated core is k_f steel by volume;
#   * which material a part is actually ASSIGNED (the shaft is aluminium on both
#     live machines, and was priced as 7850 kg/m³ steel).
# It is folded into ``compute_masses`` now.  These tests pin that the fold is
# EXACT — not "close" — because two mass models drifting apart is precisely the
# failure the single source exists to prevent.

def _cost_masses(G):
    """The Cost tab's priced masses for geometry ``G``, through the real
    geometry.2d → cost handoff (a GeometryIR, not a hand-made dict)."""
    from motor_ai_sim.modules.cost import masses_from_geometry_ir
    from motor_ai_sim.modules.geometry_2d.provider import AeroStatorGeometry2D
    gir = AeroStatorGeometry2D().build(dict(G))
    return masses_from_geometry_ir(gir), gir


def _canonical_masses(gir):
    """``compute_masses`` on the SAME geometry the GeometryIR was built from —
    the numbers the Simulation card, torque-per-mass and every optimizer use."""
    from motor_ai_sim.config import get_config
    geo = dict(gir.parameters)
    p = params_from_config(geo_override=geo)
    g = merge_geo_override(dict(get_config().get("geometry", {}) or {}), geo)
    return compute_masses(p, g)


@pytest.mark.parametrize("G", [G150, G40], ids=["150mm", "40mm"])
def test_cost_masses_are_the_compute_masses_masses(G):
    """Every priced bucket IS a compute_masses component, to the kilogram it is
    rounded at.  steel = stator + rotor; there is no fifth number anywhere."""
    got, gir = _cost_masses(G)
    m = _canonical_masses(gir)
    assert got["steel"] == pytest.approx(m["stator"] + m["rotor"], abs=5e-5)
    assert got["copper"] == pytest.approx(m["cu"], abs=5e-5)
    assert got["magnet"] == pytest.approx(m["mag"], abs=5e-5)
    assert got["shaft"] == pytest.approx(m["shaft"], abs=5e-5)
    # …and the four of them are the whole machine, not a subset of it
    structural = got["steel"] + got["copper"] + got["magnet"] + got["shaft"]
    assert structural == pytest.approx(m["total"], abs=2e-4)


@pytest.mark.parametrize("G", [G150, G40], ids=["150mm", "40mm"])
def test_the_priced_copper_is_the_copper_with_its_end_turns_on(G):
    """The old model billed the in-slot copper only — 29 % short on the 150 mm
    and 42 % on the 40 mm, on the single most price-sensitive bucket after the
    magnets."""
    got, gir = _cost_masses(G)
    m = _canonical_masses(gir)
    assert m["k_end"] > 1.3, "this machine HAS end turns — the test is vacuous otherwise"
    in_slot = m["A_cu"] * float(params_from_config(geo_override=dict(gir.parameters))
                                .stack_length) * m["RHO"]["cu"]
    assert got["copper"] == pytest.approx(in_slot * m["k_end"], rel=1e-3)
    assert got["copper"] > in_slot * 1.25


@pytest.mark.parametrize("G", [G150, G40], ids=["150mm", "40mm"])
def test_the_priced_shaft_is_the_material_the_config_assigns(G):
    """Not a 7850 kg/m³ literal.  On both live machines the shaft is aluminium,
    so the old table over-priced its mass by ~2.9×."""
    got, gir = _cost_masses(G)
    m = _canonical_masses(gir)
    rho = m["RHO"]["al"]
    assert got["shaft"] == pytest.approx(m["A_shaft"] * float(
        params_from_config(geo_override=dict(gir.parameters)).stack_length) * rho,
        abs=5e-5)
    assert rho < 7000.0, "the assigned shaft material is not steel on this machine"


def test_the_cost_module_no_longer_carries_a_density_table():
    """A private density table is a second mass model waiting to drift.  The
    insulator densities stay — they come from the materials LIBRARY by name, and
    no EM mass model carries a slot liner."""
    import inspect
    from motor_ai_sim.modules import cost as _cost
    assert not hasattr(_cost, "_DENSITY")
    assert not hasattr(_cost, "_ROLE_BUCKET")
    # Scanned as CODE, not as text — the numbers are named in the docstring on
    # purpose, and a test that cannot tell a literal from its own history is not
    # a test.  Nothing executable in this module may carry a metal density: the
    # only densities left are the insulator library lookup and its 1400 fallback.
    import ast
    tree = ast.parse(inspect.getsource(_cost))
    nums = [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, (int, float))
            and not isinstance(n.value, bool)]
    assert not [v for v in nums if 2000.0 <= float(v) <= 20000.0], (
        "a metal-density literal is back in modules/cost.py: "
        + str([v for v in nums if 2000.0 <= float(v) <= 20000.0]))


@pytest.mark.parametrize("G", [G150, G40], ids=["150mm", "40mm"])
def test_the_slot_liner_is_still_measured_on_the_geometry(G):
    """The one mass this module SHOULD own: a purchased insulator, priced by
    material name, that no EM mass model carries."""
    got, _ = _cost_masses(G)
    liners = {k: v for k, v in got.items()
              if k not in ("steel", "copper", "magnet", "shaft")}
    assert liners, "the slot liner disappeared with the density table"
    assert all(v > 0.0 for v in liners.values())


def test_an_explicit_stack_length_reaches_the_mass_model():
    """``length_mm`` is the caller quoting a different machine length.  It used
    to scale only the region integral; it has to scale the masses."""
    from motor_ai_sim.modules.cost import masses_from_geometry_ir
    from motor_ai_sim.modules.geometry_2d.provider import AeroStatorGeometry2D
    gir = AeroStatorGeometry2D().build(dict(G150))
    L = float(G150["motor_length"])
    a = masses_from_geometry_ir(gir, length_mm=L)
    b = masses_from_geometry_ir(gir, length_mm=2.0 * L)
    for k in ("steel", "magnet", "shaft"):
        assert b[k] == pytest.approx(2.0 * a[k], rel=1e-3), k
    # copper is NOT exactly 2x: the end turns do not grow with the stack, so
    # doubling the stack is LESS than doubling the copper.  That the two differ
    # at all is the end-winding factor being alive in the price.
    assert a["copper"] * 1.5 < b["copper"] < a["copper"] * 2.0
