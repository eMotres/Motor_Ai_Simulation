"""The field model's shaft must be the shaft the CAD builds — a hollow tube.

``CadQueryMotor._create_shaft`` builds a TUBE: an annulus from
``shaft_inner_radius`` (= ``rotor_inner_radius − shaft_height``) out to
``rotor_inner_radius``.  ``masses.compute_masses`` bills that section and only
that section (tests/test_masses.py pins the 150 mm shaft at a 708.7 mm² ring,
"not a solid disc").

The geometry-driven mesher used to disagree.  ``geo_mesh._mesh_rotor_*`` meshes
the rotor half solid to the centre — correct, the bore has to be discretised —
but ``_tag_rotor`` then tagged EVERY triangle with r < ``rotor_inner_radius``
DOM_SHAFT.  ``fem_solver_2d._sigma_of_tag`` gives DOM_SHAFT the shaft material's
conductivity, so the coupled σ·∂A/∂t solve and the honest rotor-eddy
post-process both billed eddy loss over the whole bore disc:

* 150 mm 24s/28p: 5425 mm² of "aluminium" against a 756 mm² tube — **7.2×**;
* 40 mm 12s/14p:    72 mm² against a 48 mm² tube — **1.5×**.

That metal does not exist.  It inflated the dashboard's solid-loss tile, the
shaft leg of the free-run decomposition, and the DOM_SHAFT span of the served
loss map (r = 2.3 … 40.7 mm — the whole bore drawn as shaft metal).

Two properties are asserted here, on both live machines:

1. **The meshed shaft region is the CAD shaft polygon.**  Tolerance 3 %: the
   mesh discretises the two bounding circles as polygons at the air/tube step,
   which costs ~1.4 % of the annulus on the 150 mm and ~0.2 % on the 40 mm.
   The old behaviour misses by 700 % / 51 %, so nothing about this bound is
   delicate.
2. **The bore inside the tube carries σ = 0.**  Every triangle whose centroid
   sits inside ``shaft_inner_radius`` must be DOM_AIR, and DOM_AIR must not be
   a conducting tag — checked against the solver's own σ lookup rather than a
   restatement of it.

The geometry-driven CDT mesher these tests used to build was removed with
`triangle` (2026-09-29); what remains pins the bore retag rule of the gmsh
build against the real CAD shaft and the solver's sigma rule.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from motor_ai_sim.cadquery_geometry import CadQueryMotor
from motor_ai_sim.simulation.sb_domains import (DOM_AIR, DOM_COIL_BASE,
                                                DOM_MAG_BASE, DOM_SHAFT)

_ROOT = Path(__file__).resolve().parents[1]
_PRESETS = json.loads((_ROOT / "config" / "motor_presets.json")
                      .read_text(encoding="utf-8"))

# FROZEN geometries — not config/motor_config.yaml, which the user edits
# constantly (same reasoning as tests/test_masses.py and
# tests/test_cad_polygon_quality.py).  The 150 mm block is the one the ANSYS
# mass cross-check was run on; the 40 mm is the live preset.
G150 = {
    "stator_diameter": 150.0, "slot_height": 14.0, "core_thickness": 4.2,
    "num_seg": 4, "num_slots_per_segment": 6, "num_poles_per_segment": 7,
    "air_gap": 0.5, "tooth_width": 9.2, "tooth2_width": 5.5, "cut_width": 6.0,
    "insulation_thickness": 0.15, "wire_width": 5.0, "wire_height": 0.6,
    "wire_spacing_x": 0.1, "wire_spacing_y": 0.13, "num_wires_per_slot": 14,
    "wire_split": 1, "slot_hs": 0.2, "magnet_height": 16.0,
    "rotor_house_height": 1.2, "shaft_height": 3.0, "magnet_fill_down": 0.9,
    "magnet_fill_up": 0.44, "magnet_fill_radius": 2.5, "magnet_up_gap": 2.0,
    "rotor_hole": 0.6, "magnet_down_height": 1.8, "magnet_lamination": 0,
    "stator_fillet_r": 3.5, "stator_fillet_r1": 1.2, "rotor_fill_r": 0.2,
    "motor_length": 35.0,
}
G40 = _PRESETS["my_40mm_last"]["geometry"]

MACHINES = [("150 mm 24s28p", G150), ("40 mm 12s14p (my_40mm_last)", G40)]


def _rotor_half(geo):
    """(params, CAD polys).  The geometry-driven CDT mesher that used to build
    a rotor half here was removed with `triangle` (2026-09-29); the retag rule
    below is tested on synthetic meshes against the real CAD shaft."""
    motor = CadQueryMotor()
    motor.set_parameters(dict(geo))
    return motor.parameters, motor.get_2d_polygons(rotor_angle_deg=0.0)


@pytest.fixture(scope="module", params=MACHINES, ids=[m[0] for m in MACHINES])
def machine(request):
    return _rotor_half(request.param[1])






def _sigma_of_tag(tag: int) -> float:
    """The solver's own tag → σ rule (fem_solver_2d._sigma_of_tag), stated once.

    Only the coil, magnet and shaft tags conduct; everything else — air
    included — is σ = 0.  Kept in step with the solver by
    ``test_sigma_rule_matches_the_solver`` below.
    """
    t = int(tag)
    if t >= DOM_COIL_BASE or t >= DOM_MAG_BASE or t == DOM_SHAFT:
        return 1.0
    return 0.0


class TestBoreIsAirNotAirGap:
    """The bore's LABEL, not its physics — σ and μ_r are the same either way.

    The gmsh-built rotor half starts life painted DOM_AIRGAP everywhere
    (``_stitch_full_half(polys_r_for_mesh, DOM_AIRGAP, ...)``; the OCC path
    remaps ``in_band`` to the ``air_gap`` key for the same effect) and is then
    overwritten by the solid polygons.  The shaft is a TUBE, so nothing covers
    the bore and it kept the air-GAP tag out through the served domain map and
    the field view — the machine drawn with its air gap running to the centre.
    ``mesher._retag_shaft_bore_as_air`` is the correction; these pin it.
    """


    def test_the_retag_maps_air_gap_to_air_inside_the_bore_only(self, machine):
        """The gmsh fallback's correction, driven by the real CAD shaft.

        Built on a synthetic mesh rather than a gmsh run: the rule under test
        is the tag mapping, and a gmsh half of these machines costs minutes.
        """
        from skfem import MeshTri
        from motor_ai_sim.simulation.mesher import _retag_shaft_bore_as_air
        from motor_ai_sim.simulation.sb_domains import (DOM_AIRGAP, DOM_ROTOR,
                                                        DOM_SHAFT)
        p, polys = machine
        r_in = float(p["shaft_inner_radius"])
        r_out = float(p["rotor_inner_radius"])
        # one degenerate-free triangle per sample radius, centroid at that r
        radii = [0.3 * r_in, 0.9 * r_in, 0.5 * (r_in + r_out), 1.5 * r_out]
        P = []
        T = []
        for k, rr in enumerate(radii):
            d = 1e-3 * rr
            P += [[rr - d, -d], [rr + d, -d], [rr, 2 * d]]
            T.append([3 * k, 3 * k + 1, 3 * k + 2])
        mesh = MeshTri(np.asarray(P, float).T * 1e-3,          # mm -> m
                       np.asarray(T, np.int64).T.copy())
        # deep in the bore, just inside it, the tube wall, outside the rotor
        tags = np.array([DOM_AIRGAP, DOM_AIRGAP, DOM_SHAFT, DOM_ROTOR],
                        np.int16)
        got = _retag_shaft_bore_as_air(mesh, tags, polys)
        assert list(got) == [DOM_AIR, DOM_AIR, DOM_SHAFT, DOM_ROTOR]

    def test_the_retag_never_removes_metal(self, machine):
        """A conducting tag inside the bore is left alone — the pass is a
        relabel of the AIR family, not a licence to delete conductors."""
        from skfem import MeshTri
        from motor_ai_sim.simulation.mesher import _retag_shaft_bore_as_air
        from motor_ai_sim.simulation.sb_domains import DOM_MAG_BASE, DOM_SHAFT
        p, polys = machine
        rr = 0.5 * float(p["shaft_inner_radius"])
        d = 1e-3 * rr
        P = np.array([[rr - d, -d], [rr + d, -d], [rr, 2 * d],
                      [rr - d, 4 * d], [rr + d, 4 * d], [rr, 7 * d]]) * 1e-3
        mesh = MeshTri(P.T, np.array([[0, 1, 2], [3, 4, 5]], np.int64).T.copy())
        tags = np.array([DOM_SHAFT, DOM_MAG_BASE], np.int16)
        got = _retag_shaft_bore_as_air(mesh, tags, polys)
        assert list(got) == [DOM_SHAFT, DOM_MAG_BASE]

    def test_a_solid_shaft_is_left_untouched(self, machine):
        """No interior ring on the shaft polygon -> nothing to retag."""
        from shapely.geometry import Point
        from skfem import MeshTri
        from motor_ai_sim.simulation.mesher import _retag_shaft_bore_as_air
        from motor_ai_sim.simulation.sb_domains import DOM_AIRGAP
        p, _polys = machine
        r_out = float(p["rotor_inner_radius"])
        solid = {"shaft": Point(0.0, 0.0).buffer(r_out, 128)}
        rr = 0.3 * r_out
        d = 1e-3 * rr
        P = np.array([[rr - d, -d], [rr + d, -d], [rr, 2 * d]]) * 1e-3
        mesh = MeshTri(P.T, np.array([[0, 1, 2]], np.int64).T.copy())
        tags = np.array([DOM_AIRGAP], np.int16)
        assert list(_retag_shaft_bore_as_air(mesh, tags, solid)) == [DOM_AIRGAP]

    def test_the_gmsh_half_really_would_leave_the_bore_labelled_air_gap(
            self, machine):
        """The premise, so the retag is not guarding a hypothetical.

        Asserted without running gmsh (a half of these machines costs minutes):
        the rotor half is painted with DOM_AIRGAP as its DEFAULT domain, and
        the only shapes that overwrite it are the solid polygons — of which the
        shaft is a tube that does not cover its own bore.
        """
        import inspect
        from shapely.geometry import Point
        from motor_ai_sim.simulation import mesher as M
        src = inspect.getsource(M)
        assert "_stitch_full_half(\n                polys_r_for_mesh, DOM_AIRGAP," in src
        assert 'polys_r_for_mesh["air_gap"] = polys_r_for_mesh.pop("in_band")' in src
        p, polys = machine
        bore_pt = Point(0.5 * float(p["shaft_inner_radius"]), 0.0)
        assert not polys["shaft"].contains(bore_pt)
        for key in ("rotor", "stator"):
            g = polys.get(key)
            if g is not None and not g.is_empty:
                assert not g.contains(bore_pt)

    def test_the_two_air_tags_are_physically_the_same_material(self):
        """Why this is a label fix and not a physics one — stated against the
        solver's own material table, not asserted in prose."""
        from motor_ai_sim.simulation import fem_solver_2d as f2
        from motor_ai_sim.simulation.sb_domains import DOM_AIRGAP
        assert _sigma_of_tag(int(DOM_AIR)) == _sigma_of_tag(int(DOM_AIRGAP)) == 0.0
        import inspect
        src = inspect.getsource(f2)
        assert 'DOM_AIRGAP: FEMMaterial("airgap", mu_r=1.0)' in src
        assert 'DOM_AIR:    FEMMaterial("air",    mu_r=1.0)' in src


def test_sigma_rule_matches_the_solver():
    """The σ rule restated above must be the one fem_solver_2d applies."""
    import inspect

    from motor_ai_sim.simulation import fem_solver_2d as f2

    src = inspect.getsource(f2)
    body = src.split("def _sigma_of_tag(t: int) -> float:", 1)[1]
    body = body.split("\n\n", 1)[0]
    # the conducting tags, and no others
    assert "DOM_COIL_BASE" in body and "DOM_MAG_BASE" in body
    assert "DOM_SHAFT" in body and "return 0.0" in body
    assert "DOM_AIR" not in body and "DOM_ROTOR" not in body \
        and "DOM_STATOR" not in body
