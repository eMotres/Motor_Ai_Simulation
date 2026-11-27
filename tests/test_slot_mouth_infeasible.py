# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""A wire wider than the slot mouth allows is refused loudly, with the limit.

Owner report 2026-10-15: an outward arc lobe with a round notch at the top of
the CIANO14 30_10 cross-section in Configure.  Configure lets the user change
the winding, so the live geometry can hold a wire whose column overruns the slot
(slot-mouth fill radius <= 0) while the die itself is fine; the slot cutters then
collide and the stator is drawn with a lobe.  Such a machine is never drawn or
solved: the solver gate names the limit.  Since 2026-11-27 (owner: "even with a split everything must build") the VIEWER routes draw
it anyway and attach the warning (`_warnings`: the field, the limit, what is compared); only a machine that could not be built at
all is still the 422.
"""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("SB_NO_MESH_WARMUP", "1")

from fastapi import HTTPException  # noqa: E402

from motor_ai_sim.geometry_validation import (  # noqa: E402
    slot_cut_limits, slot_mouth_infeasibility, validate_geometry)
from tests.test_stator_outline_symmetry import _BASE  # noqa: E402

_GEO = {**_BASE, "num_wires_per_slot": 6}


def test_feasible_wire_is_not_refused():
    assert slot_mouth_infeasibility(_GEO) is None
    for w in (0.5, 1.0, 2.0, 2.25):
        assert slot_mouth_infeasibility({**_GEO, "wire_width": w}) is None


def test_too_wide_wire_is_refused_with_the_right_maximum():
    cut_x, cut_x_max, _ = slot_cut_limits(_GEO)
    w_max = _GEO["wire_width"] + (cut_x_max - cut_x)       # fill radius = 0
    assert w_max == pytest.approx(2.30, abs=1e-6)
    bad = slot_mouth_infeasibility({**_GEO, "wire_width": 2.48})
    assert bad is not None
    assert bad["field"] == "wire_width" and bad["code"] == "wire_too_wide_for_slot"
    assert bad["max"] == pytest.approx(w_max, abs=1e-6)
    assert bad["turns"] == 6
    assert bad["message"] == ("wire too wide for this slot: max wire width "
                              "2.30 mm at 6 turns")
    assert bad["fill_r_mm"] < 0
    # the limit is exact: just under passes, just over is refused
    assert slot_mouth_infeasibility({**_GEO, "wire_width": w_max - 0.01}) is None
    assert slot_mouth_infeasibility({**_GEO, "wire_width": w_max + 0.01}) is not None


def test_split_names_the_strip_count_and_says_what_a_split_is():
    bad = slot_mouth_infeasibility({**_GEO, "wire_split": 2, "wire_width": 1.6})
    assert bad is not None and bad["field"] == "wire_split"
    assert bad["message"].startswith("wire split too wide for this slot")
    # what wire_split MEANS (winding.py): N strips side by side in EACH wire row, every strip a SERIES turn: 6 rows -> 12 series turns
    assert bad["turns"] == 6 and bad["wire_split"] == 2 and bad["series_turns"] == 12
    assert "wire_split = 2 puts 2 strips of 1.6 mm side by side in each of the 6 wire rows (12 series turns)" in bad["message"]
    # the comparison is in the answer: the half-slot side vs the limit the tooth pitch leaves
    assert bad["side_at_mm"] > bad["limit_mm"] and bad["over_mm"] == pytest.approx(bad["side_at_mm"] - bad["limit_mm"])
    assert bad["column_mm"] == pytest.approx(2 * 1.6 + 2.0 * _GEO["wire_spacing_x"])


def test_the_same_split_without_outer_notches_fits_the_pocket_rule():
    """The comparison is NOT 'strips + gaps < slot_width': it is the half-slot outer side (tooth_width/2 + 2 insulation + column +
    wire_spacing_x [+ tooth2_width when notched]) against the half-sector line; a stator without outer notches compares the pocket
    wall (tooth2_width taken off) against the line at the bore, so the same split can fit there and not with notches."""
    geo = {**_GEO, "wire_split": 2, "wire_width": 1.6}
    notched = slot_cut_limits(geo)
    plain = slot_cut_limits({**geo, "stator_outer_notches": False})
    assert notched[0] - plain[0] == pytest.approx(geo["tooth2_width"])        # the notched side carries tooth2_width
    assert notched[0] != plain[0] and notched[1] != plain[1]


def test_viewer_routes_draw_an_infeasible_winding_with_a_warning(monkeypatch):
    """The geometry window must not stay empty: the mesh is built and the warning rides in `_warnings`."""
    from motor_ai_sim.routes import geometry as rg
    from motor_ai_sim import cadquery_geometry as cg

    class _Motor:
        _stator_outline_violation = None
        def set_parameters(self, p): self.p = p
        def build_all(self): pass
        def get_all_mesh_data(self): return {"stator_core": {"vertices": [0, 0, 0], "faces": [0, 0, 0]}}
        def get_extruded_mesh_data(self, depth=None): return {"stator_core": {"vertices": [0, 0, 0], "faces": [0, 0, 0]}}
        def get_2d_mesh_data(self): return {"stator_core": {"vertices": [0, 0, 0], "faces": [0, 0, 0]}}
    monkeypatch.setattr(cg, "CadQueryMotor", _Motor)
    monkeypatch.setattr(rg, "_mesh_disk_get", lambda *a, **k: None)
    monkeypatch.setattr(rg, "_mesh_disk_put", lambda *a, **k: None)
    monkeypatch.setattr(rg, "_warm_live_mesh", lambda: None, raising=False)
    for cache in (rg._mesh_cache, rg._mesh2d_cache, rg._mesh_extruded_cache):
        cache["hash"], cache["data"] = None, None
    bad_geo = json.dumps({**_GEO, "wire_width": 2.48})
    for route in (rg.get_geometry_mesh2d, rg.get_geometry_mesh, rg.get_geometry_mesh_extruded):
        out = route(geo=bad_geo)
        assert "stator_core" in out
        w = out["_warnings"][0]
        assert w["field"] == "wire_width" and w["code"] == "wire_too_wide_for_slot"
        assert w["max"] == pytest.approx(2.30, abs=1e-6) and w["turns"] == 6
    # a feasible request carries no warning key, and a cached answer never leaks another request's warning
    good = json.dumps(_GEO)
    for route in (rg.get_geometry_mesh2d, rg.get_geometry_mesh, rg.get_geometry_mesh_extruded):
        assert "_warnings" not in route(geo=good)
    for cache in (rg._mesh_cache, rg._mesh2d_cache, rg._mesh_extruded_cache):
        assert cache["data"] is None or "_warnings" not in cache["data"]


def test_viewer_routes_still_refuse_when_the_builder_could_not_draw_it(monkeypatch):
    from motor_ai_sim.routes import geometry as rg
    from motor_ai_sim import cadquery_geometry as cg

    class _Broken:
        def set_parameters(self, p): raise RuntimeError("the cutters collided")
    monkeypatch.setattr(cg, "CadQueryMotor", _Broken)
    monkeypatch.setattr(rg, "_mesh_disk_get", lambda *a, **k: None)
    for cache in (rg._mesh_cache, rg._mesh2d_cache, rg._mesh_extruded_cache):
        cache["hash"], cache["data"] = None, None
    bad_geo = json.dumps({**_GEO, "wire_width": 2.48})
    for route in (rg.get_geometry_mesh2d, rg.get_geometry_mesh, rg.get_geometry_mesh_extruded):
        with pytest.raises(HTTPException) as ei:
            route(geo=bad_geo)
        assert ei.value.status_code == 422
        d = ei.value.detail
        assert d["error"].startswith("infeasible geometry - wire too wide for this slot")
        f = d["invalid_parameters"][0]
        assert f["field"] == "wire_width" and f["kind"] == "wire_too_wide_for_slot"
        assert f["max"] == pytest.approx(2.30, abs=1e-6) and f["turns"] == 6
    assert rg._refuse_infeasible_section(_GEO) is None          # feasible: no warning, no exception


def test_solver_gate_names_the_same_limit():
    res = validate_geometry({**_GEO, "wire_width": 2.48})
    assert not res.ok
    text = res.summary()
    assert "Wire too wide for this slot: max wire width 2.30 mm." in text


# ── the other degenerate case: a THIN wire (large notch radius) ──────────────

def _stator(g):
    import contextlib
    import io
    import logging
    from motor_ai_sim.cadquery_geometry import CadQueryMotor
    m = CadQueryMotor()
    m.set_parameters(g)
    logging.disable(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            return (m.get_2d_polygons()["stator"],
                    m.get_2d_mesh_data()["stator_core"])
    finally:
        logging.disable(logging.NOTSET)


@pytest.mark.parametrize("w", [0.5, 0.6, 0.8, 1.0, 1.2, 1.3])
def test_thin_wire_is_still_one_valid_stator_piece(w):
    """wire_width <= 1.2 on 30_10 used to return the body plus 0.0001 mm2 ghost
    pieces from the OD-corner fillet (fill radius 2.2..3.6 mm); a notch that large
    is a legitimate machine (the joint neck stays cut_width thick), so it is
    drawn — as ONE piece."""
    poly, mesh = _stator({**_GEO, "wire_width": w})
    assert poly.geom_type == "Polygon" and poly.is_valid, (w, poly.geom_type)
    assert slot_mouth_infeasibility({**_GEO, "wire_width": w}) is None
    assert len(mesh["faces"]) > 100


def test_notch_grows_and_area_falls_smoothly_with_thinner_wire():
    areas = [_stator({**_GEO, "wire_width": w})[0].area
             for w in (2.0, 1.5, 1.0, 0.5)]
    assert areas == sorted(areas)                    # thinner wire -> bigger notch -> ... area rises
    assert max(areas) - min(areas) < 12.0            # ... but only by the notch, no collapse


def test_stator_pieces_violation_flags_a_real_split_only():
    from shapely.geometry import MultiPolygon, box
    from motor_ai_sim.geometry_validation import stator_pieces_violation
    body = box(0, 0, 10, 10)
    assert stator_pieces_violation(body) is None
    ghost = box(20, 0, 20.01, 0.01)                  # 1e-4 mm2: residue, not a piece
    assert stator_pieces_violation(MultiPolygon([body, ghost])) is None
    real = box(20, 0, 24, 4)
    v = stator_pieces_violation(MultiPolygon([body, real]))
    assert v is not None and v["n"] == 2 and "separate pieces" in v["message"]
