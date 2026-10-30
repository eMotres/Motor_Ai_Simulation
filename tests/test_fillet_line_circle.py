# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""A fillet between a straight edge and a circle about the axis is Fusion's:
tangent to the line and to the circle, at the nominal radius (G2 vs Fusion,
2026-10-30).

Measured on G2-L40 against the Fusion v41 B-rep before the fix: the rotor pole
tips lay up to 0.053 mm outside Fusion's arc, and a bore vertex stayed behind
the tooth-tip fillet as a 0.063 mm spike into the air gap.  Hand geometry below: a disc R = 10 cut by the line x = 3,
fillet r = 1.
"""
from __future__ import annotations

import math

import pytest
from shapely.geometry import Point, Polygon, box

from motor_ai_sim.cadquery_geometry import _circle_points, _fillet_ring_corners

R, X0, RF = 10.0, 3.0, 1.0


def _all(radii, P):
    import numpy as np
    return np.ones(len(radii), bool)


def _arc_vertices(out, orig):
    """Vertices of the result that are not vertices of the input."""
    ov = {(round(x, 9), round(y, 9)) for x, y in orig.exterior.coords}
    return [(x, y) for x, y in out.exterior.coords if (round(x, 9), round(y, 9)) not in ov]


def test_convex_corner_rotor_tip():
    """Material inside the circle (rotor OD): centre on r = R − rf, rf from the
    line on the material side -> C = (X0 + rf, sqrt((R − rf)² − (X0 + rf)²)),
    tangent points (X0, C.y) on the line and C·R/(R − rf) on the circle."""
    disc = Polygon(_circle_points(R)).intersection(box(X0, -20, 20, 20))
    out = _fillet_ring_corners(disc, RF, _all, scale_mm=2 * R)
    cy = math.sqrt((R - RF) ** 2 - (X0 + RF) ** 2)
    for sy in (1.0, -1.0):
        C = (X0 + RF, sy * cy)
        arc = [v for v in _arc_vertices(out, disc) if math.dist(v, C) < 1.5 * RF]
        assert len(arc) >= 3
        tb = (C[0] * R / (R - RF), C[1] * R / (R - RF))
        end = min(arc, key=lambda v: math.dist(tb, v))
        # the circle-side end sits on the 256-gon chord, within its sagitta of
        # the true tangent point; every other arc vertex is ON the fillet circle
        assert math.dist(tb, end) < R * (1 - math.cos(math.pi / 256)) + 1e-12
        for v in arc:
            if v != end:
                assert math.dist(v, C) == pytest.approx(RF, abs=1e-9), v
        ys = sorted(v[1] for v in arc if abs(v[0] - X0) < 1e-9)
        assert ys and ys[0 if sy < 0 else -1] == pytest.approx(sy * cy, abs=1e-9)   # tangent on the line
    # nothing of the old corner is left outside the fillet: the result is the
    # input minus the two fillet crescents, no spike past the arc
    assert out.difference(disc).area < 1e-9
    # what the two fillets remove is the two crescents between corner and arc
    ideal = disc
    for sy in (1.0, -1.0):
        C = (X0 + RF, sy * cy)
        y = sy * math.sqrt(R * R - X0 * X0)
        tb = (C[0] * R / (R - RF), C[1] * R / (R - RF))
        # the corner cell: between the line, the circle and the two radii of
        # the fillet; what of the disc lies in it outside the fillet circle goes
        cell = Polygon([(X0, C[1]), (X0, 1.2 * y), (1.2 * tb[0], 1.2 * tb[1]), C])
        ideal = ideal.difference(disc.intersection(cell).difference(Point(C).buffer(RF, 4096)))
    assert disc.difference(out).area == pytest.approx(disc.difference(ideal).area, rel=0.03)


def test_concave_corner_stator_tooth_tip():
    """Material OUTSIDE the circle (stator bore): centre on r = R + rf."""
    ring = box(-20, -20, 20, 20).difference(Polygon(_circle_points(R)))
    tooth = ring.intersection(box(X0, 0, 20, 20))
    out = _fillet_ring_corners(tooth, RF, _all, scale_mm=2 * R)
    C = (X0 + RF, math.sqrt((R + RF) ** 2 - (X0 + RF) ** 2))
    arc = [v for v in _arc_vertices(out, tooth) if math.dist(v, C) < 1.5 * RF]
    assert len(arc) >= 3
    tb = (C[0] * R / (R + RF), C[1] * R / (R + RF))
    end = min(arc, key=lambda v: math.dist(tb, v))
    assert math.dist(tb, end) < R * (1 - math.cos(math.pi / 256)) + 1e-12
    for v in arc:
        if v != end:
            assert math.dist(v, C) == pytest.approx(RF, abs=1e-9), v
    assert out.difference(tooth).area < 1e-9
    # no bore vertex survives between the two tangent points (the old spike)
    th_line = math.atan2(math.sqrt(R * R - X0 * X0), X0)
    th_tb = math.atan2(tb[1], tb[0])
    lo, hi = sorted((th_line, th_tb))
    left = [v for v in out.exterior.coords
            if abs(math.hypot(*v) - R) < 1e-9 and lo + 1e-9 < math.atan2(v[1], v[0]) < hi - 1e-9]
    assert left == []


def test_straight_corners_keep_the_walk():
    """Two straight edges: the boundary-walking fillet as before (a square)."""
    sq = box(0, 0, 10, 10)
    out = _fillet_ring_corners(sq, 1.0, _all, scale_mm=20.0)
    assert out.area == pytest.approx(100 - 4 * (1 - math.pi / 4), rel=2e-3)
    assert out.contains(Point(5, 5))


def test_report_script_replays_the_old_fillet(tmp_path):
    """scripts/report_fillet_change.py builds one die with the legacy fillet
    replay and as now, reports every part, and writes nothing."""
    import subprocess
    import sys
    from pathlib import Path

    import yaml
    from tests.test_stator_outline_symmetry import _BASE

    repo = Path(__file__).resolve().parents[1]
    d = tmp_path / "dies" / "T 30"
    d.mkdir(parents=True)
    geo = {k: v for k, v in _BASE.items() if k not in ("rotor_inner_radius", "rotor_outer_radius",
                                                       "stator_inner_radius", "stator_outer_radius")}
    (d / "die.yaml").write_text(yaml.safe_dump({"name": "T 30", "geometry": geo}), encoding="utf-8")
    before = (d / "die.yaml").read_bytes()
    r = subprocess.run([sys.executable, str(repo / "scripts" / "report_fillet_change.py"),
                        str(tmp_path / "dies"), "--json", str(tmp_path / "out.json")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=str(repo), timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    import json
    rows = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert rows and rows[0]["machine"] == "T 30/die"
    assert {"stator", "rotor", "magnets"} <= set(rows[0])
    assert (d / "die.yaml").read_bytes() == before
