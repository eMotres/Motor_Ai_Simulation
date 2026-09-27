"""Stage A method fixes, 2026-09-28 (failed night 2026-09-27 on the CIANO14 12/50).

Fast, solver-free checks of each root cause:
  1. materials: static3d resolves the machine's OWN assignment (request override
     on top of config) for k_f and for the passport label;
  2. sector cut: partner breakpoints straddling a quantum boundary still get
     ONE radius on both cuts;
  3. mesh sizes follow the physical gap / magnet width, not the OD;
  4. the axial box always leaves an air cap above a long stack;
  5. a warm-start mu from a mesh of another size is dropped, not fatal.
"""
import math
from types import SimpleNamespace

import numpy as np
import pytest
from shapely.geometry import Polygon, box

from motor_ai_sim.material_context import set_request_materials
from motor_ai_sim.simulation.static3d import motor_geometry as mg
from motor_ai_sim.simulation.static3d.end_effect import compatible_mu_init
from motor_ai_sim.simulation.static3d.motor_mesh import (axial_box_mm,
                                                         physical_mesh_sizes)


@pytest.fixture
def override():
    yield set_request_materials
    set_request_materials(None)


def test_effective_assignment_takes_request_override(override, monkeypatch):
    import motor_ai_sim.config as cfg
    monkeypatch.setattr(cfg, "get_material_assignments",
                        lambda *a, **k: {"stator_core": "B15AHV950M",
                                         "rotor_core": "B15AHV950M",
                                         "magnet": "N52UH_150C"})
    override({"assignment": {"stator_core": "20SW1200",
                             "rotor_core": "20SW1200"}, "materials": {}})
    a = mg.effective_material_assignments()
    assert a["stator_core"] == "20SW1200" and a["rotor_core"] == "20SW1200"
    assert a["magnet"] == "N52UH_150C"          # untouched keys keep config


def test_stack_factor_is_the_machine_steels(override, monkeypatch):
    import motor_ai_sim.config as cfg
    from motor_ai_sim.materials import get_material
    monkeypatch.setattr(cfg, "get_material_assignments",
                        lambda *a, **k: {"stator_core": "B15AHV950M"})
    kf_20 = float(get_material("steel", "20SW1200").stacking_factor)
    kf_b15 = float(get_material("steel", "B15AHV950M").stacking_factor)
    assert kf_20 != kf_b15                       # otherwise the test is blind
    override({"assignment": {"stator_core": "20SW1200"}, "materials": {}})
    assert mg._stack_factor("stator_core", 1.0) == pytest.approx(kf_20)
    override(None)
    assert mg._stack_factor("stator_core", 1.0) == pytest.approx(kf_b15)


def _wedge(r0, r1, a):
    return Polygon([(r0, 0.0), (r1, 0.0), (r1 * math.cos(a), r1 * math.sin(a)),
                    (r0 * math.cos(a), r0 * math.sin(a))])


def test_cut_canon_joins_partners_across_a_quantum_boundary():
    a = math.pi                                  # 180 deg anti-periodic sector
    # breakpoint 15.70499 mm on theta=0, 15.70501 mm on theta=180 deg: the old
    # independent rounding sent them to 15.700 and 15.710 (the Ø50 abort)
    p = Polygon([(15.0, 0.0), (15.70499, 0.0), (16.0, 0.0), (16.0, 1.0),
                 (-16.0, 1.0), (-16.0, 0.0), (-15.70501, 0.0), (-15.0, 0.0),
                 (-15.0, 0.5), (15.0, 0.5)])
    old = mg._snap_cut_vertices(p, a)
    canon = mg._cut_radius_canon([p], a)
    new = mg._snap_cut_vertices(p, a, canon=canon)

    def cut_radii(poly):
        r0, r1 = set(), set()
        for x, y in poly.exterior.coords:
            if abs(y) < 1e-9:
                (r0 if x > 0 else r1).add(round(abs(x), 9))
        return r0, r1
    o0, o1 = cut_radii(old)
    assert o0 != o1                              # the bug, reproduced
    n0, n1 = cut_radii(new)
    assert n0 == n1                              # fixed: identical cuts
    assert max(abs(r - 15.705) for r in n0 if 15.6 < r < 15.8) < 1e-4


def test_cut_canon_refuses_to_merge_distinct_features():
    a = math.pi / 2
    pts = [(10.0 + 0.009 * i, 0.0) for i in range(4)]   # chain 27 um wide
    p = Polygon(pts + [(12.0, 0.0), (12.0, 3.0), (10.0, 3.0)])
    with pytest.raises(ValueError, match="cluster wider"):
        mg._cut_radius_canon([p], a)


def _fake_section(gap, magnet_w, r_rot=10.0):
    mag = SimpleNamespace(polygon=box(0, 0, magnet_w, 10 * magnet_w))
    return SimpleNamespace(r_rotor_out_mm=r_rot, r_stator_in_mm=r_rot + gap,
                           magnet_regions=lambda: [mag])


@pytest.mark.parametrize("gap", [0.1, 0.25, 0.5, 1.0])
def test_mesh_resolves_the_gap_whatever_the_machine_size(gap):
    ph = physical_mesh_sizes(_fake_section(gap, 2.0))
    assert gap / ph["h_gap"] >= 3.0 - 1e-9       # >= 3 elements across the gap
    assert ph["h_solid"] >= ph["h_gap"]


def test_mesh_solid_follows_the_thinnest_magnet():
    ph = physical_mesh_sizes(_fake_section(0.1, 0.66))
    assert ph["h_solid"] <= 0.66 / 2 + 1e-9      # the Ø12's 0.66 mm magnets
    big = physical_mesh_sizes(_fake_section(1.0, 20.0))
    assert big["h_solid"] == pytest.approx(1.6)  # capped


def test_axial_box_leaves_air_above_a_long_stack():
    # Ø12 x 40 mm at 1.5 L: r_box 24, R_out 6, L/2 = 30 -> box used to be 24
    z = axial_box_mm(24.0, 6.0, 30.0)
    assert z - 30.0 == pytest.approx(18.0)
    # a short machine keeps its old box bit for bit
    assert axial_box_mm(400.0, 100.0, 50.0) == 400.0


def test_warm_start_from_another_mesh_is_dropped_not_fatal():
    mu = np.ones(12)
    assert compatible_mu_init(mu, 12) is mu or np.array_equal(
        compatible_mu_init(mu, 12), mu)
    assert compatible_mu_init(mu, 15) is None
    assert compatible_mu_init(np.ones((3, 12)), 12) is not None
    assert compatible_mu_init(None, 12) is None
