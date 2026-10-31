"""Animation keyframes: a later frame's rotor outlines are frame 0's, turned.

The transient route draws the rotor iron and the magnets of every keyframe over
the field.  It used to rebuild (and re-fillet) the whole machine for each one;
since 2026-10-31 it builds frame 0 only and turns its rotor iron and magnets by
the angle between the frames (``routes.simulation._rotor_polys_turned``).  This
pins that the turned outlines are the rebuilt ones on the configured machine, in
the sense the builder turns them: the magnets to the 5 um chord tolerance the
outlines are simplified to, the rotor iron to the builder's own angle-dependent
welding and corner rounding (measured 12-44 um, below the 50-100 um weld
tolerance; exact at a half turn).  Pure geometry: no mesh, no solve.
"""
from __future__ import annotations

import pytest

pytest.importorskip("shapely")


def _hausdorff(a, b):
    return float(a.hausdorff_distance(b))


@pytest.mark.parametrize("a0, a1", [(0.0, 3.75), (1.5, 17.25), (0.0, -9.0)])
def test_turned_rotor_outlines_are_the_rebuilt_ones(a0, a1):
    from motor_ai_sim.cadquery_geometry import CadQueryMotor
    from motor_ai_sim.routes.simulation import _rotor_polys_turned
    from motor_ai_sim.simulation.fem_solver_2d import _simplify_polys

    m = CadQueryMotor()
    p0 = _simplify_polys(m.get_2d_polygons(rotor_angle_deg=a0), tol_mm=0.005)
    p1 = _simplify_polys(m.get_2d_polygons(rotor_angle_deg=a1), tol_mm=0.005)
    t = _rotor_polys_turned(p0, a1 - a0)

    assert t["rotor"] is not None and p1["rotor"] is not None
    assert _hausdorff(t["rotor"], p1["rotor"]) < 1.0              # mm
    assert t["rotor"].area == pytest.approx(p1["rotor"].area, rel=1e-2)

    assert len(t["magnets"]) == len(p1["magnets"]) > 0
    for (mt, pt), (mr, pr) in zip(t["magnets"], p1["magnets"]):
        assert pt == pr                                  # polarity kept
        assert _hausdorff(mt, mr) < 0.05                          # mm
        assert mt.area == pytest.approx(mr.area, rel=1e-3)


def test_turning_by_zero_is_the_identity_and_none_survives():
    from shapely.geometry import box
    from motor_ai_sim.routes.simulation import _rotor_polys_turned

    sq = box(10.0, -1.0, 12.0, 1.0)
    t = _rotor_polys_turned({"rotor": sq, "magnets": [(sq, 1), (None, -1)]}, 0.0)
    assert t["rotor"].equals(sq)
    assert t["magnets"][0][0].equals(sq) and t["magnets"][0][1] == 1
    assert t["magnets"][1] == (None, -1)
    # a quarter turn counter-clockwise takes +x to +y
    q = _rotor_polys_turned({"rotor": sq, "magnets": []}, 90.0)["rotor"]
    assert q.centroid.y == pytest.approx(11.0) and abs(q.centroid.x) < 1e-9
    assert _rotor_polys_turned({}, 5.0) == {"rotor": None, "magnets": []}
