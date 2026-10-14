# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Conformance gates for drive-train component modules (docs/COMPONENT_SDK.md).

A component (transmission, load) from any author is acceptable iff its IR passes
the gate for its type: schema, units, provenance, physical plausibility and
monotonic/passivity checks, and a JSON round trip.  The gates raise
``ConformanceError`` (an ``AssertionError``) naming the first broken rule; they
are real checks, not ``assert`` statements, so ``python -O`` cannot disable them.
"""
from __future__ import annotations

import math
import re
import time
from typing import Any, Dict, Iterable, Optional

from ..drivetrain_ir import (UNITS_TAG, MotorPerformanceIR, PropulsorIR, ShaftLoadIR,
                             TransmissionIR)

#: Capability prefixes the registry treats as drive-train components.
COMPONENT_PREFIXES = ("transmission.", "load.")

#: ``status`` values of a component manifest.  ``reference`` = an in-tree example
#: for authors (not on the ladder); the ladder itself is community < verified < integrated.
COMPONENT_STATUSES = ("reference", "community", "verified", "integrated")

#: Seconds a provider's run() may take to build its IR (it is a description, not a solve).
MAX_RUN_SECONDS = 5.0

_ID = re.compile(r"^[a-z][a-z0-9_]{1,47}$")
_TOL = 1e-9


class ConformanceError(AssertionError):
    """A component broke a conformance rule."""


def _req(cond: bool, msg: str) -> None:
    if not cond:
        raise ConformanceError(msg)


def _finite_all(name: str, rows: Iterable[Iterable[Optional[float]]]) -> None:
    for r in rows:
        for v in r:
            if v is not None:
                _req(math.isfinite(float(v)), f"{name}: non-finite value")


def _need_basis(ir: Any, keys: Iterable[str]) -> None:
    for k in keys:
        v = (ir.basis or {}).get(k)
        _req(isinstance(v, str) and len(v.strip()) >= 3,
             f"provenance: basis[{k!r}] must say where that number comes from")


def _common(ir: Any, schema: str, cls: type, *, component: bool = True) -> None:
    _req(isinstance(ir, cls), f"must return a {cls.__name__}, got {type(ir).__name__}")
    _req(ir.ir_schema == schema, f"ir_schema must be {schema!r}")
    _req(ir.units == UNITS_TAG, f"units must be {UNITS_TAG!r} (got {ir.units!r}): "
                                f"N*m, rpm, W, kg, kg*m^2, degC, m/s, kg/m^3; shaft dims mm")
    if component:                       # a motor id is an opaque portal handle ("die:config")
        _req(bool(_ID.match(ir.id)), f"id {ir.id!r} must match {_ID.pattern}")
    _req(ir.source.kind in ("analytical", "measured", "published", "datasheet", "fem", "cfd"), "bad source.kind")
    # JSON round trip - the wire format between modules
    back = cls.model_validate_json(ir.model_dump_json())
    _req(back.model_dump() == ir.model_dump(), "IR does not survive a JSON round trip unchanged")


def _no_geometry(ir: MotorPerformanceIR) -> None:
    """The motor IR is performance only: no key of the dumped IR may be on the
    project's geometry denylist (mcp_tools.GEOMETRY_DENYLIST), when it is importable."""
    try:
        from motor_ai_sim.mcp_tools import GEOMETRY_DENYLIST
    except Exception:                                               # noqa: BLE001
        return

    def walk(o: Any, path: str) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                _req(str(k) not in GEOMETRY_DENYLIST, f"geometry field in a motor IR: {path}.{k}")
                walk(v, f"{path}.{k}")
        elif isinstance(o, (list, tuple)):
            for i, v in enumerate(o):
                walk(v, f"{path}[{i}]")
    walk(ir.model_dump(), "$")


# ---------------------------------------------------------------------------
# MotorPerformanceIR (the portal's own output must pass it too)
# ---------------------------------------------------------------------------

def assert_motor_performance_ir(ir: MotorPerformanceIR) -> None:
    _common(ir, "motor_performance-1", MotorPerformanceIR, component=False)
    _no_geometry(ir)
    _finite_all("torque_peak_Nm", [ir.torque_peak_Nm])
    _req(all(t >= 0 for t in ir.torque_peak_Nm) and max(ir.torque_peak_Nm) > 0, "torque_peak_Nm must be >= 0 and not all zero")
    _req(ir.speed_rpm[-1] <= ir.limits.speed_max_rpm * (1 + _TOL), "the envelope extends beyond limits.speed_max_rpm")
    if ir.torque_continuous_Nm is not None:
        _req(bool(ir.continuous_basis), "torque_continuous_Nm needs continuous_basis (the cooling it assumes)")
        for c, p in zip(ir.torque_continuous_Nm, ir.torque_peak_Nm):
            _req(0 <= c <= p * (1 + 1e-6) + 1e-12, "continuous torque must be within [0, peak]")
    if ir.efficiency is not None:
        for row in ir.efficiency.eta:
            for v in row:
                _req(v is None or 0.0 <= v <= 1.0, "efficiency must be in [0, 1] (or null where unreachable)")
        _need_basis(ir, ["efficiency"])
    if ir.mass_kg is not None:
        _req(1e-3 <= ir.mass_kg <= 1e4, f"mass_kg = {ir.mass_kg:g} is implausible (units?)")
        _need_basis(ir, ["mass_kg"])
    if ir.rotor_inertia_kg_m2 is not None:
        _req(1e-9 <= ir.rotor_inertia_kg_m2 <= 1e3, f"rotor_inertia_kg_m2 = {ir.rotor_inertia_kg_m2:g} is implausible (units?)")
        _need_basis(ir, ["rotor_inertia_kg_m2"])
    _req(ir.limits.speed_max_rpm <= 1e6, "limits.speed_max_rpm is implausible (units?)")
    _need_basis(ir, ["torque_peak_Nm"])


# ---------------------------------------------------------------------------
# TransmissionIR
# ---------------------------------------------------------------------------

def assert_transmission_ir(ir: TransmissionIR) -> None:
    _common(ir, "transmission-1", TransmissionIR)
    _req(0.01 <= ir.ratio <= 1000.0, f"ratio = {ir.ratio:g} is implausible (ratio = speed_in / speed_out)")
    if ir.kind == "direct":
        _req(abs(ir.ratio - 1.0) < 1e-12, "a 'direct' coupling has ratio 1")
    _req(ir.mass_kg <= 1e4, f"mass_kg = {ir.mass_kg:g} is implausible (units?)")
    _req(ir.inertia_in_kg_m2 <= 1e3 and ir.inertia_out_kg_m2 <= 1e3, "inertia is implausible (units?)")
    if ir.backlash_deg is not None:
        _req(ir.backlash_deg <= 30.0, "backlash_deg is implausible (degrees at the output shaft)")
    lim = ir.limits
    _req(ir.speed_in_rpm[-1] >= lim.speed_in_max_rpm * (1 - _TOL),
         "the loss map must cover the declared input speed range (axis ends below limits.speed_in_max_rpm)")
    _req(ir.torque_in_Nm[-1] >= lim.torque_in_max_Nm * (1 - _TOL),
         "the loss map must cover the declared input torque range (axis ends below limits.torque_in_max_Nm)")
    if lim.torque_out_max_Nm is not None:
        _req(lim.torque_out_max_Nm <= lim.torque_in_max_Nm * ir.ratio * (1 + 1e-9),
             "torque_out_max_Nm exceeds torque_in_max_Nm * ratio (efficiency cannot exceed 1)")
    if lim.speed_out_max_rpm is not None:
        _req(lim.speed_out_max_rpm <= lim.speed_in_max_rpm / ir.ratio * (1 + 1e-9),
             "speed_out_max_rpm exceeds speed_in_max_rpm / ratio")
    for g, tab in ir.loss_W.items():
        _req(bool(re.match(r"^[a-z][a-z0-9_]*$", g)), f"loss group name {g!r} must be lower_snake_case")
        _finite_all(f"loss_W.{g}", tab)
        _req(all(v >= 0 for r in tab for v in r), f"loss_W.{g}: a loss cannot be negative")
        scale = max(max(r) for r in tab) or 1.0
        if ir.speed_in_rpm[0] == 0.0:
            _req(all(v <= _TOL * scale for v in tab[0]),
                 f"loss_W.{g}: heat must be 0 W at standstill (a drag torque gives loss = torque * speed)")
        for i in range(len(tab)):                                     # non-decreasing in torque
            for j in range(1, len(tab[i])):
                _req(tab[i][j] >= tab[i][j - 1] - _TOL * scale, f"loss_W.{g}: loss falls with torque at speed index {i}")
        for i in range(1, len(tab)):                                  # non-decreasing in speed
            for j in range(len(tab[i])):
                _req(tab[i][j] >= tab[i - 1][j] - _TOL * scale, f"loss_W.{g}: loss falls with speed at torque index {j}")
    # passivity: output power must rise with input torque (so the solve has ONE answer)
    # and the transmission must not make power (loss <= P_in) at its top torque
    for i, n in enumerate(ir.speed_in_rpm):
        w = n * math.pi / 30.0
        if w <= 0:
            continue
        tot = [sum(ir.loss_W[g][i][j] for g in ir.loss_W) for j in range(len(ir.torque_in_Nm))]
        for j in range(1, len(tot)):
            d_t = ir.torque_in_Nm[j] - ir.torque_in_Nm[j - 1]
            _req(tot[j] - tot[j - 1] < d_t * w * (1 + 1e-9) + 1e-12,
                 f"loss rises faster than the input power at {n:g} rpm: output power would fall with input torque")
        _req(tot[-1] <= ir.torque_in_Nm[-1] * w * (1 + 1e-9),
             f"loss exceeds the input power at the top of the torque axis, {n:g} rpm (efficiency < 0)")
    _need_basis(ir, ["ratio", "loss_W", "inertia", "mass"])


# ---------------------------------------------------------------------------
# Loads
# ---------------------------------------------------------------------------

def assert_shaft_load_ir(ir: ShaftLoadIR) -> None:
    _common(ir, "shaft_load-1", ShaftLoadIR)
    _finite_all("torque_Nm", ir.torque_Nm)
    _req(all(v >= 0 for r in ir.torque_Nm for v in r), "v0.1 loads absorb power: torque must be >= 0")
    _req(max(max(r) for r in ir.torque_Nm) <= 1e6, "torque is implausible (units?)")
    if ir.output is not None:
        _finite_all("output", ir.output)
    if ir.inertia_kg_m2 is not None:
        _req(ir.inertia_kg_m2 <= 1e3, "inertia is implausible (units?)")
    _need_basis(ir, ["torque_Nm"])


def assert_propulsor_ir(ir: PropulsorIR) -> None:
    _common(ir, "propulsor-1", PropulsorIR)
    _finite_all("ct", ir.ct)
    _finite_all("cp", ir.cp)
    _req(0.005 <= ir.diameter_m <= 20.0, "diameter_m is implausible")
    _req(ir.advance_ratio[-1] <= 3.0, "advance_ratio axis above 3 is implausible")
    _req(0.5 <= ir.test_density_kg_m3 <= 1.5, "test_density_kg_m3 is implausible for air")
    for i, (rc, rp) in enumerate(zip(ir.ct, ir.cp)):
        for j, (ct, cp) in enumerate(zip(rc, rp)):
            J = ir.advance_ratio[j]
            _req(0.0 <= ct <= 1.0 and 0.0 < cp <= 1.0, f"C_T / C_P out of the physical range at rpm index {i}, J index {j}")
            # propulsive efficiency J*C_T/C_P cannot reach 1 (momentum theory): catches swapped or mis-scaled tables
            _req(J * ct < cp, f"propulsive efficiency J*C_T/C_P >= 1 at rpm index {i}, J index {j}")
    if ir.inertia_kg_m2 is not None:
        _req(ir.inertia_kg_m2 <= 1e3, "inertia is implausible (units?)")
    _need_basis(ir, ["ct", "cp", "diameter_m"])


def assert_load_ir(ir: Any) -> None:
    if isinstance(ir, PropulsorIR):
        assert_propulsor_ir(ir)
    else:
        assert_shaft_load_ir(ir)


# ---------------------------------------------------------------------------
# Providers (the module around the IR)
# ---------------------------------------------------------------------------

def _provider_gate(provider: Any, prefix: str, outputs: Iterable[str], payload: Optional[Dict[str, Any]]) -> Any:
    man = provider.manifest()
    _req(man.capability.startswith(prefix), f"capability must start with {prefix!r}, got {man.capability!r}")
    suffix = man.capability[len(prefix):]
    _req(bool(_ID.match(suffix)), f"capability suffix {suffix!r} must match {_ID.pattern}")
    _req(bool(man.contracts_version), "manifest must stamp contracts_version")
    _req(man.status in COMPONENT_STATUSES, f"manifest.status must be one of {COMPONENT_STATUSES}, got {man.status!r}")
    _req(list(man.outputs) and set(man.outputs) <= set(outputs), f"manifest.outputs must be within {list(outputs)}")
    _req(bool(man.summary.strip()), "manifest.summary is required")
    t0 = time.perf_counter()
    ir = provider.run(dict(payload or {}))
    _req(time.perf_counter() - t0 <= MAX_RUN_SECONDS, f"run() took longer than {MAX_RUN_SECONDS:g} s: it must describe, not solve")
    _req(ir.id == suffix, f"IR id {ir.id!r} must equal the capability suffix {suffix!r}")
    _req(ir.model_dump() == provider.run(dict(payload or {})).model_dump(), "run() must be deterministic for the same payload")
    return ir


def assert_transmission_provider(provider: Any, *, payload: Optional[Dict[str, Any]] = None) -> TransmissionIR:
    ir = _provider_gate(provider, "transmission.", ["TransmissionIR"], payload)
    assert_transmission_ir(ir)
    return ir


def assert_load_provider(provider: Any, *, payload: Optional[Dict[str, Any]] = None) -> Any:
    ir = _provider_gate(provider, "load.", ["ShaftLoadIR", "PropulsorIR"], payload)
    assert_load_ir(ir)
    return ir


def assert_component_provider(provider: Any, *, payload: Optional[Dict[str, Any]] = None) -> Any:
    """Dispatch on the capability prefix; used by discovery before a plugin is registered."""
    cap = provider.manifest().capability
    if cap.startswith("transmission."):
        return assert_transmission_provider(provider, payload=payload)
    if cap.startswith("load."):
        return assert_load_provider(provider, payload=payload)
    raise ConformanceError(f"{cap!r} is not a component capability (prefixes: {COMPONENT_PREFIXES})")
