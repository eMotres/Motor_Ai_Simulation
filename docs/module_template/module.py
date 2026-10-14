# SPDX-License-Identifier: Apache-2.0
# Copyright (C) <YEAR> <YOUR NAME OR ORGANISATION> and contributors
"""TEMPLATE transmission module (a belt drive).  Copy this folder, rename it, and
replace every line marked TODO.  The whole contract is: a class with
CAPABILITY ``transmission.<id>``, manifest(), and run(payload) -> TransmissionIR.

Keep run() a pure DESCRIPTION: build the IR from parameters, no network, no file
access, no randomness, finishes in well under a second.  Units: N*m, rpm, W, kg,
kg*m^2, degC; shaft dimensions in mm (docs/COMPONENT_SDK.md).
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

# The ONLY imports from the core: the contracts (and the manifest class).
from motor_ai_sim.contracts import CONTRACTS_VERSION, ModelSource, ShaftInterface, TransmissionIR, TransmissionLimits, Validity
from motor_ai_sim.modules.base import ModuleManifest

PARAMS: Dict[str, float] = dict(          # TODO: your parameters, each with a default
    ratio=2.0,                            # speed_in / speed_out (pulley ratio)
    loss_fraction=0.03,                   # TODO: share of input power lost (fit it to YOUR data, see validation/)
    drag_torque_Nm=0.002,                 # TODO: speed-independent drag torque at the input shaft
    mass_kg=0.12, inertia_in_kg_m2=1.0e-6, inertia_out_kg_m2=6.0e-6,
    torque_in_max_Nm=1.5, speed_in_max_rpm=10000.0, loss_continuous_max_W=15.0,
)


class MyBeltDrive:                        # TODO: rename
    NAME, CAPABILITY, VERSION = "transmission-my-belt-drive", "transmission.my_belt_drive", "0.1.0"   # TODO

    def manifest(self) -> ModuleManifest:
        return ModuleManifest(
            name=self.NAME, version=self.VERSION, capability=self.CAPABILITY, kind="compute",
            contracts_version=CONTRACTS_VERSION, depends_on=[],
            inputs=["MotorPerformanceIR"], outputs=["TransmissionIR"],
            summary="TODO: one sentence - what it is and what it was validated against",
            status="community",           # always "community" when you submit; maintainers promote it
            author="TODO your name / organisation")

    def run(self, payload: Optional[Dict[str, Any]] = None) -> TransmissionIR:
        given = dict((payload or {}).get("params") or {})
        if set(given) - set(PARAMS):
            raise ValueError(f"unknown parameters {sorted(set(given) - set(PARAMS))}; allowed: {sorted(PARAMS)}")
        p = {**PARAMS, **{k: float(v) for k, v in given.items()}}
        n_ax = [p["speed_in_max_rpm"] * i / 10 for i in range(11)]
        t_ax = [p["torque_in_max_Nm"] * j / 10 for j in range(11)]
        drag = [[p["drag_torque_Nm"] * n * math.pi / 30.0] * len(t_ax) for n in n_ax]
        slip = [[p["loss_fraction"] * t * n * math.pi / 30.0 for t in t_ax] for n in n_ax]
        return TransmissionIR(
            id="my_belt_drive", name="My belt drive", kind="belt", ratio=p["ratio"],
            speed_in_rpm=n_ax, torque_in_Nm=t_ax, loss_W={"drag": drag, "belt_friction": slip},
            inertia_in_kg_m2=p["inertia_in_kg_m2"], inertia_out_kg_m2=p["inertia_out_kg_m2"], mass_kg=p["mass_kg"],
            backlash_deg=0.0,
            limits=TransmissionLimits(torque_in_max_Nm=p["torque_in_max_Nm"], speed_in_max_rpm=p["speed_in_max_rpm"],
                                      loss_continuous_max_W=p["loss_continuous_max_W"]),
            input_shaft=ShaftInterface(kind="clamp", diameter_mm=8.0),            # TODO
            output_shaft=ShaftInterface(kind="clamp", diameter_mm=10.0),          # TODO
            validity=Validity(ambient_C=(0.0, 40.0), notes="TODO: what the data cover"),
            source=ModelSource(kind="measured",                                    # TODO analytical|measured|published|datasheet
                               reference="TODO: citation or dataset id (see validation/SOURCE.md)",
                               validated_against=None),                            # TODO: set when you have a comparison
            basis={"ratio": "TODO: pulley tooth counts / datasheet",
                   "loss_W": "TODO: how the maps were obtained",
                   "inertia": "TODO", "mass": "TODO"})
