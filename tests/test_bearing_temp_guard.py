"""A thermal map that ran away must not become the bearings' temperature.

2026-09-08 23:26: a coupled run of the 40 mm G2 generator at the Ø200's
687 A left a map with a 46 742 °C shaft in the store.  The next loop's first
pass read "the last thermal map of this machine" — same fingerprint, so it
qualified — and billed the bearings at 46 742 °C.  The reader now believes only
a physical seat temperature; anything else means "no usable map", and the
caller falls back to the assignment.
"""
from __future__ import annotations

import pytest

from motor_ai_sim import mech_losses as ml


def _map(shaft_avg=None, shaft_ends_mean=None):
    res = {"components": {"shaft": {"avg": shaft_avg, "max": shaft_avg}},
           "cooling": {"shaft_ends": {"t_shaft_mean_c": shaft_ends_mean}}}
    return res


def test_a_runaway_shaft_is_not_a_seat_temperature():
    assert ml.bearing_temp_from_map(_map(shaft_avg=46742.4)) is None
    assert ml.bearing_temp_from_map(_map(shaft_ends_mean=680.8)) is None


def test_a_physical_seat_temperature_is_read_with_its_source():
    t, where = ml.bearing_temp_from_map(_map(shaft_avg=121.0))
    assert t == pytest.approx(121.0 - ml._SHAFT_TO_BEARING_DROP_K)
    assert "average" in where
    t2, where2 = ml.bearing_temp_from_map(_map(shaft_avg=121.0, shaft_ends_mean=98.5))
    assert t2 == pytest.approx(98.5 - ml._SHAFT_TO_BEARING_DROP_K)
    assert "ends" in where2


def test_the_band_is_the_one_a_catalogue_would_accept():
    assert ml.BEARING_SEAT_MIN_C <= -40.0          # cold-start specs
    assert 200.0 <= ml.BEARING_SEAT_MAX_C <= 400.0  # past grease, before "map ran away"
    assert ml.bearing_temp_from_map(_map(shaft_avg=ml.BEARING_SEAT_MAX_C + 1.0)) is None
    assert ml.bearing_temp_from_map(_map(shaft_avg=ml.BEARING_SEAT_MAX_C)) is not None
