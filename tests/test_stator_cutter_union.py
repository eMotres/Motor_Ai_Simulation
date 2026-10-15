# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""A dropped slot cutter never becomes a lobe (owner report 2026-10-15, again).

CIANO14 40 new / L20 at insulation 0.07 showed an outward arc lobe with a round
notch on one stator sector in Configure.  The machine is feasible (slot-mouth fill
radius 0.62 mm); the CONSTRUCTION failed: the cutters of neighbouring slot-pair
units share the half-slot ray (their mouth-fillet circles even have the same
centre) only to the last bit, and GEOS's cascaded `unary_union` then silently
DROPPED a whole cutter (247.9 mm2 of a 3359 mm2 union), so the stator kept the iron
of that slot (+23 % in one 60-degree sector).  Which inputs fail depends on the
last bit of sin/cos - the server failed at insulation 0.07 where 0.06 was fine,
this workstation was fine at both.

* `_union_cutters` checks the union covers every cutter and redoes it on a snapped
  grid when it does not (deterministic replay below: the mouth-circle centres moved
  by the last-bit offsets that broke the plain union here);
* the net: a stator that still comes out lopsided is refused BY NAME -
  `validate_geometry` (solver gate) and the mesh routes (422).
"""
from __future__ import annotations

import contextlib
import io
import json
import logging
import math
import os

import pytest

os.environ.setdefault("SB_NO_MESH_WARMUP", "1")

shapely = pytest.importorskip("shapely")
import shapely.ops  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import motor_ai_sim.cadquery_geometry as cg  # noqa: E402
from motor_ai_sim.cadquery_geometry import CadQueryMotor  # noqa: E402
from motor_ai_sim.geometry_validation import (  # noqa: E402
    stator_outline_violation, validate_geometry)
from tests.test_stator_outline_symmetry import (  # noqa: E402
    _BASE, _VARIANTS, _sector_areas)

_LIVE = {**_BASE, **_VARIANTS["40 new L20 ins 0.07 (live 2026-10-15)"]}

# Last-bit offsets (x, y) of the twelve mouth-fillet circle centres of the live
# geometry (6 units x 2 sides, build order) that made the plain cascaded union drop
# a cutter.  Magnitude 1e-16..1e-15 mm: below the print precision of any coordinate.
_CIRCLE_OFFSETS = [
    [3.282155310463053e-16, -1.9086768787032107e-16],
    [6.374173348165676e-16, 2.9437127017693855e-16],
    [3.9962127962397913e-16, 9.396400791218404e-16],
    [6.898657692290726e-16, -3.1672643368667535e-16],
    [-8.117748928881197e-17, 7.320236883713309e-16],
    [7.855676850642395e-16, 7.777079176597491e-17],
    [9.13961208622669e-17, -8.334391078943951e-16],
    [-5.744897549850404e-16, -7.633709271479e-16],
    [-3.4938459769685884e-16, -9.68158254909301e-16],
    [1.5452115858241245e-17, -9.988078452111449e-16],
    [-2.8060586033780502e-16, -7.957367509934799e-16],
    [-5.136676671011333e-16, 3.282496069500436e-16],
]
_FILL_R = 0.62        # live geometry: (6.5 - 6.19) / (1 - sin 30 deg)


@contextlib.contextmanager
def _quiet():
    logging.disable(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            yield
    finally:
        logging.disable(logging.NOTSET)


def _replay_circle_offsets(monkeypatch):
    """Move the mouth-circle centres by the recorded last-bit offsets."""
    orig = cg._arc_points
    state = {"i": 0}

    def moved(cx, cy, r, a0, sweep, *a, **k):
        if (abs(sweep - 2 * math.pi) < 1e-12 and abs(r - _FILL_R) < 1e-6
                and state["i"] < len(_CIRCLE_OFFSETS)):
            dx, dy = _CIRCLE_OFFSETS[state["i"]]
            state["i"] += 1
            cx, cy = cx + dx, cy + dy
        return orig(cx, cy, r, a0, sweep, *a, **k)

    monkeypatch.setattr(cg, "_arc_points", moved)
    return orig


def _motor(g=None):
    m = CadQueryMotor()
    m.set_parameters(dict(g or _LIVE))
    return m


def _spread(areas):
    return (max(areas) - min(areas)) / max(areas)


def test_live_geometry_is_symmetric_after_last_bit_noise(monkeypatch):
    """The input that dropped a cutter on the server: now one even stator,
    in the FEM polygon AND in the Configure mesh."""
    orig = _replay_circle_offsets(monkeypatch)
    m = _motor()
    with _quiet():
        poly = m.get_2d_polygons()["stator"]
    R = float(m.parameters["stator_outer_radius"])
    areas = _sector_areas(poly, R, 6)
    assert poly.geom_type == "Polygon" and poly.is_valid
    assert _spread(areas) < 0.005, areas
    assert stator_outline_violation(poly, m.parameters) is None

    monkeypatch.setattr(cg, "_arc_points", orig)
    _replay_circle_offsets(monkeypatch)            # fresh counter for the second builder
    m2 = _motor()
    with _quiet():
        m2.get_2d_mesh_data()
    assert m2._stator_outline_violation is None


def test_union_that_drops_a_part_is_detected_and_repaired(monkeypatch):
    """`_tool_covers` sees a union that lost a part; `_union_cutters` never returns
    one.  Independent of the platform: the lossy union is simulated."""
    from shapely.geometry import box
    real = shapely.ops.unary_union
    parts = [box(0, 0, 10, 10), box(10, 0, 20, 10), box(30, 0, 40, 10)]
    assert cg._tool_covers(real(parts), parts)
    assert not cg._tool_covers(real(parts[:2]), parts)                      # one part dropped
    assert not cg._tool_covers(real(parts + [box(50, 0, 60, 10)]), parts)   # more than the parts

    calls = {"n": 0}

    def lossy(geoms):
        calls["n"] += 1
        g = list(geoms)
        return real(g[:-1]) if calls["n"] == 1 else real(g)     # the first call drops a part
    monkeypatch.setattr(shapely.ops, "unary_union", lossy)
    tool = cg._union_cutters(parts, 40.0)
    assert cg._tool_covers(tool, parts)
    assert tool.area == pytest.approx(300.0)


def _drop_one_cutter(monkeypatch):
    """A union that drops one cutter on EVERY call - the failure the repair cannot
    undo, so the net has to fire."""
    real = shapely.ops.unary_union
    monkeypatch.setattr(cg, "_union_cutters",
                        lambda cutters, scale_mm: real(list(cutters)[1:]))


def test_net_refuses_a_lobed_stator_by_name(monkeypatch):
    _drop_one_cutter(monkeypatch)
    m = _motor()
    with _quiet():
        polys = m.get_2d_polygons()
    v = stator_outline_violation(polys["stator"], m.parameters)
    assert v is not None and v["spread"] > 0.005
    assert v["message"].startswith("stator outline not symmetric (construction failed) - ")

    with _quiet():
        res = validate_geometry(dict(_LIVE))
    assert not res.ok
    assert "stator_outline_not_symmetric" in {x.code for x in res.violations}
    assert "stator outline not symmetric (construction failed)" in res.summary()


def test_net_checks_the_outer_circle():
    from shapely.geometry import Point
    ring = Point(0, 0).buffer(20.0, 256).difference(Point(0, 0).buffer(12.0, 256))
    p = {"stator_outer_radius": 20.0, "num_slots": 12}
    assert stator_outline_violation(ring, p) is None
    spike = shapely.ops.unary_union([ring, Point(0, 21.0).buffer(1.0)])   # a lobe past R
    v = stator_outline_violation(spike, p)
    assert v is not None and v["message"].startswith(
        "stator outline not symmetric (construction failed) - a vertex lies at r")
    # an odd unit count cannot be compared: skipped, not refused
    assert stator_outline_violation(ring, {"stator_outer_radius": 20.0, "num_slots": 3}) is None


def test_mesh_routes_answer_422_for_a_lobed_stator(monkeypatch, tmp_path):
    from motor_ai_sim.routes import geometry as rg
    monkeypatch.setattr(rg, "_MESH_DISK_DIR", tmp_path, raising=False)
    _drop_one_cutter(monkeypatch)
    geo = json.dumps(_LIVE)
    with _quiet():
        for route in (rg.get_geometry_mesh2d, rg.get_geometry_mesh_extruded):
            with pytest.raises(HTTPException) as ei:
                route(geo=geo)
            assert ei.value.status_code == 422
            d = ei.value.detail
            assert d["error"].startswith(
                "stator outline not symmetric (construction failed) - ")
            assert d["invalid_parameters"][0]["kind"] == "stator_outline_not_symmetric"


def test_sound_stator_is_still_served_by_the_route(tmp_path, monkeypatch):
    from motor_ai_sim.routes import geometry as rg
    monkeypatch.setattr(rg, "_MESH_DISK_DIR", tmp_path, raising=False)
    with _quiet():
        data = rg.get_geometry_mesh2d(geo=json.dumps(_LIVE))
    assert len(data["stator_core"]["faces"]) > 100
