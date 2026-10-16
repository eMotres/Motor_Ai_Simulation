# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""REFERENCE transmission module - a simple single-stage gearbox.

Copy this file to start a transmission.  It shows the whole contract in ~120
lines: a class with CAPABILITY ``transmission.<id>``, a manifest, and run(payload)
returning a ``TransmissionIR`` built from a few physical parameters.

THE MODEL (an EXAMPLE of how to fill the loss maps, NOT a validated product)
===========================================================================
Heat dissipated, as a function of the INPUT shaft speed n [rpm] and torque T_in
[N*m], w = 2 pi n / 60::

    drag_constant        = T0 * w                    constant drag torque (seals, bearing preload)
    drag_speed_dependent = c * (n / 1000) * w        viscous / churning drag torque rising with speed
    mesh_load            = f_mesh * T_in * w         tooth friction: a fraction of the input power

so the loss is 0 W at standstill, rises with speed and torque, and the efficiency
rises from 0 at no load towards 1 - f_mesh.  The defaults are generic small-drone
values chosen for the example; every one is a parameter, and ``basis`` says so in
the IR.  Nothing here was fitted to a real gearbox: status stays ``reference``.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from ...contracts import CONTRACTS_VERSION, ModelSource, ShaftInterface, TransmissionIR, TransmissionLimits, Validity
from ...contracts.adapters import stamp
from ..base import ModuleManifest

DEFAULTS: Dict[str, float] = dict(
    ratio=3.0,                         # speed_in / speed_out
    drag_torque_const_Nm=0.004,        # T0 at the input shaft
    drag_viscous_Nm_per_krpm=0.0015,   # c: extra drag torque per 1000 rpm
    mesh_loss_fraction=0.015,          # f_mesh: share of input power lost in the mesh
    inertia_in_kg_m2=2.0e-6,
    inertia_out_kg_m2=1.5e-5,
    mass_kg=0.18,
    backlash_deg=0.5,                  # at the output shaft
    torque_in_max_Nm=2.0,
    speed_in_max_rpm=12000.0,
    loss_continuous_max_W=25.0,        # heat the housing can reject at 25 degC
    input_shaft_mm=8.0,
    output_shaft_mm=12.0,
)
N_SPEED, N_TORQUE = 13, 11             # loss-map resolution


class GearboxSingleStage:
    NAME, CAPABILITY, VERSION = "transmission-gearbox-single-stage", "transmission.gearbox_single_stage", "0.1.0"

    def manifest(self) -> ModuleManifest:
        return ModuleManifest(
            name=self.NAME, version=self.VERSION, capability=self.CAPABILITY, kind="compute",
            contracts_version=CONTRACTS_VERSION, depends_on=[],
            inputs=["MotorPerformanceIR"], outputs=["TransmissionIR"],
            summary="REFERENCE single-stage gearbox: ratio, constant + speed-dependent drag, mesh loss, "
                    "inertia, mass (generic example values, not a validated product)",
            status="reference", author="MOTRES d.o.o.")

    def run(self, payload: Optional[Dict[str, Any]] = None) -> TransmissionIR:
        """payload: ``{"params": {...any key of DEFAULTS...}}``; an optional
        ``"motor"`` (a MotorPerformanceIR dict) is accepted and ignored here - a
        sizing module would read the motor's torque and speed to pick its ratings."""
        given = dict((payload or {}).get("params") or {})
        unknown = set(given) - set(DEFAULTS)
        if unknown:
            raise ValueError(f"unknown parameters {sorted(unknown)}; allowed: {sorted(DEFAULTS)}")
        p = dict(DEFAULTS)
        p.update({k: float(v) for k, v in given.items()})
        if p["ratio"] <= 0 or not (0.0 <= p["mesh_loss_fraction"] < 1.0):
            raise ValueError("ratio must be > 0 and mesh_loss_fraction in [0, 1)")
        n_ax = [p["speed_in_max_rpm"] * i / (N_SPEED - 1) for i in range(N_SPEED)]
        t_ax = [p["torque_in_max_Nm"] * j / (N_TORQUE - 1) for j in range(N_TORQUE)]
        const: List[List[float]] = []
        visc: List[List[float]] = []
        mesh: List[List[float]] = []
        for n in n_ax:
            w = n * math.pi / 30.0
            const.append([p["drag_torque_const_Nm"] * w] * N_TORQUE)
            visc.append([p["drag_viscous_Nm_per_krpm"] * (n / 1000.0) * w] * N_TORQUE)
            mesh.append([p["mesh_loss_fraction"] * t * w for t in t_ax])
        return TransmissionIR(
            id="gearbox_single_stage", name="Single-stage gearbox (reference)", kind="gearbox",
            ratio=p["ratio"], speed_in_rpm=n_ax, torque_in_Nm=t_ax,
            loss_W={"drag_constant": const, "drag_speed_dependent": visc, "mesh_load": mesh},
            inertia_in_kg_m2=p["inertia_in_kg_m2"], inertia_out_kg_m2=p["inertia_out_kg_m2"],
            mass_kg=p["mass_kg"], backlash_deg=p["backlash_deg"],
            limits=TransmissionLimits(
                torque_in_max_Nm=p["torque_in_max_Nm"],
                torque_out_max_Nm=p["torque_in_max_Nm"] * p["ratio"] * (1 - p["mesh_loss_fraction"]),
                speed_in_max_rpm=p["speed_in_max_rpm"], speed_out_max_rpm=p["speed_in_max_rpm"] / p["ratio"],
                loss_continuous_max_W=p["loss_continuous_max_W"], ambient_ref_C=25.0),
            input_shaft=ShaftInterface(kind="clamp", diameter_mm=p["input_shaft_mm"]),
            output_shaft=ShaftInterface(kind="keyed", diameter_mm=p["output_shaft_mm"]),
            validity=Validity(ambient_C=(0.0, 40.0), notes="example model: not fitted to any real gearbox"),
            source=ModelSource(kind="analytical", reference="MOTRES reference example (docs/COMPONENT_SDK.md)",
                               validated_against=None),
            basis={"ratio": "parameter 'ratio' (given by the caller)",
                   "loss_W": "analytical: drag torque T0 + c*n/1000, plus f_mesh*P_in; defaults are generic example values",
                   "inertia": "parameters inertia_in/out_kg_m2 (generic example values)",
                   "mass": "parameter mass_kg (generic example value)"},
            provenance=stamp(self.NAME, version=self.VERSION))
