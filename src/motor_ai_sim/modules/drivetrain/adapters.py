# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Builders that turn OUR existing data into the drive-train IRs.

  * ``motor_ir_from_s1_model`` - the passport's propeller-S1 model -> ``MotorPerformanceIR``
    (performance only: the S1 model's thermal geometry is NOT copied).
  * ``propulsor_from_propeller`` - a catalogue propeller (``motor_ai_sim.propeller``)
    -> ``PropulsorIR`` (static C_T / C_P sampled over the tested rpm range).

They add no physics: every number is read from the passport model / catalogue
fit and the IR says so in ``basis``.
"""
from __future__ import annotations

import math
import re
from typing import Any, List, Mapping, Optional

from ... import propeller as pp
from ... import propeller_s1 as s1
from ...contracts import (EfficiencyMap, ModelSource, MotorLimits, MotorPerformanceIR, PropulsorIR,
                          ShaftInterface)
from ...contracts.adapters import stamp


def _snake(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")
    return ("p_" + s if not s or not s[0].isalpha() else s)[:47]


def propulsor_from_propeller(prop: pp.Propeller, *, n_rpm: int = 41,
                             inertia_kg_m2: Optional[float] = None) -> PropulsorIR:
    """Sample the catalogue fit of ``prop`` over its tested rpm range (static, J = 0)."""
    if not prop.selectable:
        raise pp.PropellerDataError(f"{prop.id} has no usable C_T / C_P")
    lo, hi = prop.rpm_range
    n = max(3, int(n_rpm))
    rpm = [lo + (hi - lo) * i / (n - 1) for i in range(n)]
    co = [pp.coefficients(prop, r) for r in rpm]
    mass_g = prop.raw.get("mass_g") if isinstance(prop.raw, Mapping) else None
    return PropulsorIR(
        id=_snake(prop.id), name=f"{prop.vendor} {prop.model}".strip(), kind="propeller",
        diameter_m=prop.diameter_m, blades=prop.blades,
        speed_rpm=rpm, advance_ratio=[0.0], ct=[[c["ct"]] for c in co], cp=[[c["cp"]] for c in co],
        test_density_kg_m3=prop.test_density, cp_estimated=bool(prop.cp.estimated),
        inertia_kg_m2=inertia_kg_m2,
        mass_kg=(float(mass_g) / 1000.0 if isinstance(mass_g, (int, float)) and mass_g > 0 else None),
        source=ModelSource(kind="published" if not prop.cp.estimated else "datasheet",
                           reference=f"propeller catalogue entry {prop.id} ({prop.vendor} {prop.model})",
                           validated_against="vendor bench tables in the catalogue entry (robust power-law fit in rpm)"),
        basis={"ct": f"catalogue fit C_T(rpm) of {prop.id}, static (J = 0), sampled over the tested rpm range",
               "cp": f"catalogue fit C_P(rpm) of {prop.id}; " + ("ESTIMATED (no published torque)"
                                                                if prop.cp.estimated else "from published torque"),
               "diameter_m": "catalogue geometry (the diameter the coefficients are reduced with)"},
        provenance=stamp("adapter.propulsor_from_propeller"))


def motor_ir_from_s1_model(m: Mapping[str, Any], *, id: Optional[str] = None, name: str = "",
                           mass_kg: Optional[float] = None, rotor_inertia_kg_m2: Optional[float] = None,
                           shaft: Optional[ShaftInterface] = None, n_torque: int = 8) -> MotorPerformanceIR:
    """The shaft-side performance of a passport S1 model.

    Envelope: at each grid speed the largest shaft torque over the passport's current grid
    up to the current cap, at the model's reference magnet temperature, minus bearing +
    windage.  The VOLTAGE limit is NOT in this envelope (S1 applies it point by point), so
    the envelope can read high near the speed cap - ``peak_basis`` says so.  Efficiency:
    shaft output / (shaft output + motor losses) at the reference winding and magnet
    temperatures, sine drive, controller losses excluded."""
    rpm_ax = [float(r) for r in m["grid"]["rpm"] if float(r) <= s1.speed_cap(m) + 1e-9]
    t_m, t_w = float(m["magnet_ref_temp_C"]), float(m["R_temp_C"])
    cap = s1.current_cap(m)
    cur = [float(i) for i in m["grid"]["I_A"] if float(i) <= cap + 1e-9] + [cap]
    drv = s1.resolve_drive(m)

    def mech_torque(r: float) -> float:
        w = r * math.pi / 30.0
        return s1._mech_W(m, r) / w if w > 0 else 0.0

    peak: List[float] = []
    for r in rpm_ax:
        vals = [s1.em_torque(m, r, i, t_m) for i in cur]
        peak.append(max(0.0, max([v for v in vals if v is not None] or [0.0]) - mech_torque(r)))
    t_top = max(peak)
    t_ax = [t_top * (k + 1) / n_torque for k in range(n_torque)]
    eta: List[List[Optional[float]]] = []
    for r, pk in zip(rpm_ax, peak):
        w = r * math.pi / 30.0
        row: List[Optional[float]] = []
        for t in t_ax:
            if t > pk * (1 + 1e-9) or w <= 0:
                row.append(None)
                continue
            I, _ = s1.current_for_torque(m, r, t + mech_torque(r), t_m)
            if I is None:
                row.append(None)
                continue
            ls = s1.losses(m, r, I, t_w, drv)
            p_s = t * w
            row.append(None if ls["unavailable"] else p_s / (p_s + ls["motor_total"]))
        eta.append(row)
    lims = s1.limits_of(m)
    bat = m.get("battery") or {}
    die, cfg = m.get("die"), m.get("config")
    return MotorPerformanceIR(
        id=id or f"{die}:{cfg}", name=name or f"{die} {cfg}".strip(),
        speed_rpm=rpm_ax, torque_peak_Nm=peak,
        peak_basis="passport torque grid, largest value up to the current cap at the reference magnet "
                   "temperature, minus bearing + windage; the voltage limit is not applied here (S1 applies it)",
        efficiency=EfficiencyMap(speed_rpm=rpm_ax, torque_Nm=t_ax, eta=eta,
                                 basis="shaft output / (shaft output + motor losses), reference winding and magnet "
                                       "temperatures, sine drive, controller losses excluded"),
        limits=MotorLimits(speed_max_rpm=s1.speed_cap(m), winding_C=lims["winding_C"], magnet_C=lims["magnet_C"],
                           current_max_A=cap, dc_voltage_min_V=bat.get("v_min"), dc_voltage_max_V=bat.get("v_max")),
        mass_kg=mass_kg, rotor_inertia_kg_m2=rotor_inertia_kg_m2, shaft=shaft, s1_available=True,
        source=ModelSource(kind="fem", reference=f"motor passport S1 model {die} / {cfg}",
                           validated_against=None),
        basis={"torque_peak_Nm": "passport torque grid (2-D FEM, energy torque) x torque_factor x flux factor",
               "efficiency": "passport loss grid + R(T); see efficiency.basis",
               **({"mass_kg": "given by the caller (catalogue)"} if mass_kg else {}),
               **({"rotor_inertia_kg_m2": "given by the caller (catalogue)"} if rotor_inertia_kg_m2 else {})},
        provenance=stamp("adapter.motor_ir_from_s1_model"))
