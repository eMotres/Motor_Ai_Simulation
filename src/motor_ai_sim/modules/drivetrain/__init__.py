# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Drive-train system solve (motor -> transmission -> load).  See docs/COMPONENT_SDK.md."""
from __future__ import annotations

from .adapters import motor_ir_from_s1_model, propulsor_from_propeller
from .system import chain_at, make_load_hook, solve_operating_point, solve_s1

__all__ = ["motor_ir_from_s1_model", "propulsor_from_propeller", "chain_at", "make_load_hook",
           "solve_operating_point", "solve_s1"]
