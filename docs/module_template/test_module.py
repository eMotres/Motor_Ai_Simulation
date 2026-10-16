# SPDX-License-Identifier: Apache-2.0
# Copyright (C) <YEAR> <YOUR NAME OR ORGANISATION> and contributors
"""Tests every module submission carries.  Run:  python -m pytest -q   (in this folder).

1. the conformance gate (the same one the portal runs before it registers a module);
2. one physics check against YOUR validation data (replace the placeholder numbers).
"""
from __future__ import annotations

import math

from motor_ai_sim.contracts.conformance import assert_transmission_provider

from module import MyBeltDrive            # TODO: your class


def test_conformance_gate():
    assert_transmission_provider(MyBeltDrive())


def test_efficiency_against_validation_data():
    # TODO: replace with a point from validation/ (value, source and tolerance written in PROVENANCE.md).
    ir = MyBeltDrive().run({})
    n, t = 5000.0, 0.75                    # a map node
    expected_eta = 1.0 - (0.002 * n * math.pi / 30.0 + 0.03 * t * n * math.pi / 30.0) / (t * n * math.pi / 30.0)
    assert abs(ir.efficiency_at(n, t) - expected_eta) < 1e-9
