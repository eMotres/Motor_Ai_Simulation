# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Conformance gate for ``ComponentCard`` (docs/COMPONENT_CARD.md).

A card from any author is acceptable iff it passes this gate: the schema (units, semver,
ports, frames), the gate of the IR it carries (motor, transmission, propeller, battery,
controller), a JSON round trip, mass sanity, and - for a motor - the privacy rule: the card
carries performance, mass properties and the outer envelope only.  The gate raises
``ConformanceError`` (an ``AssertionError``); it uses real checks, not ``assert``.
"""
from __future__ import annotations

import math
from typing import Any

from ..component_ir import (KIND_MODELS, BatteryIR, ComponentCard, ControllerIR)
from ..drivetrain_ir import MotorPerformanceIR, PropulsorIR, ShaftLoadIR, TransmissionIR
from .drivetrain import (ConformanceError, _need_basis, _req, assert_motor_performance_ir, assert_propulsor_ir,
                         assert_shaft_load_ir, assert_transmission_ir)


def _no_geometry_keys(doc: Any, path: str = "$") -> None:
    """No key of the dumped card may be on the project's geometry denylist."""
    try:
        from motor_ai_sim.mcp_tools import GEOMETRY_DENYLIST
    except Exception:                                               # noqa: BLE001
        return
    if isinstance(doc, dict):
        for k, v in doc.items():
            _req(str(k) not in GEOMETRY_DENYLIST, f"design geometry field on a motor card: {path}.{k}")
            _no_geometry_keys(v, f"{path}.{k}")
    elif isinstance(doc, (list, tuple)):
        for i, v in enumerate(doc):
            _no_geometry_keys(v, f"{path}[{i}]")


def assert_battery_ir(ir: BatteryIR) -> None:
    _req(isinstance(ir, BatteryIR), "must be a BatteryIR")
    _req(ir.source.kind in ("analytical", "measured", "published", "datasheet", "fem", "cfd"), "bad source.kind")
    _req(ir.cells_series * ir.cells_parallel >= 1, "a battery needs cells")
    _req(ir.mass_kg is None or 1e-3 <= ir.mass_kg <= 1e4, "battery mass_kg is implausible (units?)")
    _req(ir.capacity_Ah <= 1e5, "capacity_Ah is implausible")
    _need_basis(ir, ["r_internal_ohm", "i_continuous_max_A", "capacity_Ah"])


def assert_controller_ir(ir: ControllerIR) -> None:
    _req(isinstance(ir, ControllerIR), "must be a ControllerIR")
    _req(ir.source.kind in ("analytical", "measured", "published", "datasheet", "fem", "cfd"), "bad source.kind")
    _req(ir.mass_kg is None or 1e-4 <= ir.mass_kg <= 1e3, "controller mass_kg is implausible (units?)")
    _req(ir.bus_voltage_max_V <= 1e5 and ir.phase_current_continuous_A <= 1e5, "controller ratings are implausible (units?)")
    for k, v in ir.phase_current_by_cooling_A.items():
        _req(v > 0, f"phase_current_by_cooling_A[{k!r}] must be positive")
        _req(v >= ir.phase_current_continuous_A * (1 - 1e-9),
             "phase_current_continuous_A must be the most conservative cooling class")
    _need_basis(ir, ["phase_current_continuous_A", "bus_voltage"])


def assert_component_card(card: ComponentCard) -> None:
    _req(isinstance(card, ComponentCard), f"must be a ComponentCard, got {type(card).__name__}")
    back = ComponentCard.model_validate_json(card.model_dump_json())
    _req(back.model_dump() == card.model_dump(), "card does not survive a JSON round trip unchanged")
    _req(bool(card.provenance.module), "provenance.module is required")
    _req(card.model.ir_schema in KIND_MODELS[card.kind], f"a {card.kind} card cannot carry {card.model.ir_schema}")
    mp = card.mass_properties
    if mp is not None:
        _req(1e-4 <= mp.mass_kg <= 1e5, f"mass_kg = {mp.mass_kg:g} is implausible (units?)")
        _req(all(abs(c) < 1e3 for c in mp.com_m), "com_m is implausible (metres?)")
        _req(bool(mp.basis.strip()), "mass_properties.basis must say where the numbers come from")
        # the inertia must be consistent with the mass (no body is denser than ~20 000 kg/m^3 in a 1 m^3 box)
        for i in range(3):
            _req(mp.inertia_kg_m2[i][i] <= mp.mass_kg * 1e3, "inertia is implausible (units?)")
    m = card.model
    model_mass = getattr(m, "mass_kg", None)
    if mp is not None and model_mass is not None:
        _req(math.isclose(mp.mass_kg, model_mass, rel_tol=1e-6),
             f"card mass {mp.mass_kg:g} kg and model mass {model_mass:g} kg disagree")
    for p in card.ports:
        _req(p.type != "shaft" or p.max_speed_rpm <= 1e6, f"port {p.name}: max_speed_rpm is implausible")
    # the IR it carries must pass its own gate
    if isinstance(m, MotorPerformanceIR):
        assert_motor_performance_ir(m)
    elif isinstance(m, TransmissionIR):
        assert_transmission_ir(m)
    elif isinstance(m, PropulsorIR):
        assert_propulsor_ir(m)
    elif isinstance(m, ShaftLoadIR):
        assert_shaft_load_ir(m)
    elif isinstance(m, BatteryIR):
        assert_battery_ir(m)
    elif isinstance(m, ControllerIR):
        assert_controller_ir(m)
    if card.kind == "motor":
        _motor_privacy(card)


def _motor_privacy(card: ComponentCard) -> None:
    """A motor card: performance, mass properties, outer envelope - nothing else."""
    _no_geometry_keys(card.model_dump(mode="json"))
    if card.cad is not None:
        for what in ("step", "gltf"):
            ref = getattr(card.cad, what)
            _req(ref is None or ref.access == "nda",
                 f"motor CAD ({what}) may be referenced only with access='nda' (released through the NDA flow)")
    if card.envelope is not None:
        _req(card.envelope.shape in ("box", "cylinder"), "a motor envelope is a plain box or cylinder")
    _req(len(card.frames) <= 8, "a motor card lists mounting frames only (at most 8)")
