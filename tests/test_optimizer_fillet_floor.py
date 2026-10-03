# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""The optimizer's 0.15 mm minimum-fillet floor (optimization/manufacturing.py).

Bounds the SEARCH only: a fillet variable is searched from 0.15 mm up; a machine
whose own fillet is below the floor keeps it and the variable is left out of
the search with a reason; non-fillet variables are untouched."""
from __future__ import annotations

import pytest

from motor_ai_sim.optimization import manufacturing as mf


def test_floor_raises_a_fillet_lower_bound():
    lo, hi, why = mf.apply_floor("stator_fillet_r1", 0.0, 3.0, 1.2)
    assert (lo, hi, why) == (mf.MIN_FILLET_MM, 3.0, None)
    for k in mf.FILLET_KEYS:
        assert mf.optimizer_floor(k) == pytest.approx(0.15)


def test_a_machine_below_the_floor_is_kept_and_not_searched():
    lo, hi, why = mf.apply_floor("rotor_fill_r", 0.0, 3.0, 0.0)
    assert (lo, hi) == (0.0, 3.0)            # bounds unchanged: nothing moved
    assert why and "left out of the search" in why and "rotor_fill_r" in why


def test_no_range_above_the_floor_is_reported():
    _lo, _hi, why = mf.apply_floor("magnet_fill_radius", 0.0, 0.1, None)
    assert why and "no range" in why


def test_other_variables_are_untouched():
    assert mf.apply_floor("tooth_width", 0.3, 5.0, 2.0) == (0.3, 5.0, None)


def test_doe_bounds_respect_the_floor(monkeypatch):
    import motor_ai_sim.config as cfgmod
    from motor_ai_sim.optimization.doe import sample_bounds
    cfg = {"geometry_schema": {
               "rotor_fill_r": {"min": 0.0, "max": 3.0, "unit": "mm"},
               "stator_fillet_r1": {"min": 0.0, "max": 3.0, "unit": "mm"},
               "tooth_width": {"min": 0.5, "max": 9.0, "unit": "mm"}},
           "geometry": {"rotor_fill_r": 0.2, "stator_fillet_r1": 0.0,
                        "tooth_width": 3.0},
           "sweep_whitelist": ["rotor_fill_r", "stator_fillet_r1", "tooth_width"]}
    monkeypatch.setattr(cfgmod, "get_config", lambda *a, **k: cfg)
    b = sample_bounds(0.9)
    assert b["rotor_fill_r"][0] == pytest.approx(0.15)       # 0.2 - 90 % -> floor
    assert "stator_fillet_r1" not in b                       # sharp by design: kept
    assert b["tooth_width"][0] == pytest.approx(0.5)         # untouched (schema min)
