# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Re-export alias for ISO 281 bearing life helpers (named ``bearings_life`` in DESIGN.md)."""
from __future__ import annotations

from .bearings import (
    BearingLifeResult,
    BearingSpectrumLifeResult,
    GearBearingSpec,
    equivalent_bearing_loads,
    evaluate_bearing,
    evaluate_bearing_spectrum,
    iso281_a_iso,
    reference_viscosity_nu1,
)

__all__ = [
    "GearBearingSpec",
    "BearingLifeResult",
    "BearingSpectrumLifeResult",
    "equivalent_bearing_loads",
    "reference_viscosity_nu1",
    "iso281_a_iso",
    "evaluate_bearing",
    "evaluate_bearing_spectrum",
]
