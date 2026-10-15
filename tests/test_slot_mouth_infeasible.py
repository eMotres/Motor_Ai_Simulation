# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""A wire wider than the slot mouth allows is refused loudly, with the limit.

Owner report 2026-10-15: an outward arc lobe with a round notch at the top of
the CIANO14 30_10 cross-section in Configure.  Configure lets the user change
the winding, so the live geometry can hold a wire whose column overruns the slot
(slot-mouth fill radius <= 0) while the die itself is fine; the slot cutters then
collide and the stator is drawn with a lobe.  Such a machine is never drawn or
solved: the viewer routes answer 422 and the solver gate names the same limit.
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


def test_split_names_the_strip_count():
    bad = slot_mouth_infeasibility({**_GEO, "wire_split": 2, "wire_width": 1.6})
    assert bad is not None and bad["field"] == "wire_split"
    assert bad["message"].startswith("wire split too wide for this slot")


def test_mesh_routes_refuse_it_with_a_422():
    from motor_ai_sim.routes import geometry as rg
    bad_geo = json.dumps({**_GEO, "wire_width": 2.48})
    for route in (rg.get_geometry_mesh2d, rg.get_geometry_mesh):
        with pytest.raises(HTTPException) as ei:
            route(geo=bad_geo)
        assert ei.value.status_code == 422
        d = ei.value.detail
        assert d["error"].startswith("infeasible geometry - wire too wide for this slot")
        f = d["invalid_parameters"][0]
        assert f["field"] == "wire_width" and f["kind"] == "wire_too_wide_for_slot"
        assert f["max"] == pytest.approx(2.30, abs=1e-6) and f["turns"] == 6
    with pytest.raises(HTTPException) as ei:
        rg.get_geometry_mesh_extruded(geo=bad_geo)
    assert ei.value.status_code == 422
    rg._refuse_infeasible_section(_GEO)          # feasible: no exception


def test_solver_gate_names_the_same_limit():
    res = validate_geometry({**_GEO, "wire_width": 2.48})
    assert not res.ok
    text = res.summary()
    assert "Wire too wide for this slot: max wire width 2.30 mm." in text
