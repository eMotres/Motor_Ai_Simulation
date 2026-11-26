# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Gear kinematics (Willis equation, compound/stacked trains) and planetary assembly conditions."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .common import DictResultMixin, ValidityState
from .geometry import GearSpec, PairGeometryResult, analyze_pair


@dataclass
class StageKinematicsResult(DictResultMixin):
    """Speeds, ratio, and assembly verification for one gear stage."""

    stage_kind: str                  # "spur_pair" | "planetary" | "compound_planetary"
    ratio: float                     # signed omega_in / omega_out
    abs_ratio: float
    reverses_direction: bool
    fixed_member: Optional[str]
    input_member: str
    output_member: str
    input_speed_rpm: float
    output_speed_rpm: float
    member_speeds_rpm: Dict[str, float]
    planet_relative_speed_rpm: Optional[float] = None
    stationary_carrier_ratio_u: Optional[float] = None
    equal_spacing_possible: Optional[bool] = None
    planet_angles_deg: List[float] = field(default_factory=list)
    neighbour_clearance_mm: Optional[float] = None
    neighbour_clearance_ok: Optional[bool] = None
    coaxiality_error_mm: Optional[float] = None
    coaxiality_ok: Optional[bool] = None
    assembly_ok: bool = True
    state: ValidityState = "valid"
    applicability_evidence: Dict[str, Any] = field(default_factory=dict)
    missing_inputs: List[str] = field(default_factory=list)
    method: str = "Willis_Kinematics"
    flags: List[str] = field(default_factory=list)


@dataclass
class ChainKinematicsResult(DictResultMixin):
    """Kinematics across an ordered chain of stages."""

    stages: List[StageKinematicsResult]
    overall_ratio: float             # signed omega_in / omega_out
    abs_overall_ratio: float
    reverses_direction: bool
    input_speed_rpm: float
    output_speed_rpm: float
    all_assemblies_ok: bool
    state: ValidityState = "valid"
    applicability_evidence: Dict[str, Any] = field(default_factory=dict)
    missing_inputs: List[str] = field(default_factory=list)
    method: str = "StageChain_Kinematics"
    flags: List[str] = field(default_factory=list)


@dataclass
class PlanetaryAssemblyCheck(DictResultMixin):
    """Planetary assembly, spacing, coaxiality, and neighbour-clearance diagnostics."""

    n_planets: int
    equal_spacing_possible: bool
    assembly_index_value: float      # (z_s + z_r) / N for simple; (z_s*z_p2 + z_r*z_p1) / (N*gcd(z_p1,z_p2))
    planet_angles_deg: List[float]
    min_angular_step_deg: float
    neighbour_clearance_mm: float
    neighbour_clearance_ok: bool
    coaxiality_error_mm: float
    coaxiality_ok: bool
    assembly_ok: bool
    method: str = "PlanetaryAssemblyCondition"
    flags: List[str] = field(default_factory=list)


def check_planetary_assembly(
    *,
    z_s: int,
    z_r: int,
    n_planets: int,
    a_w_sp_mm: float,
    a_w_pr_mm: float,
    d_a_planet_mm: float,
    z_p1: Optional[int] = None,
    z_p2: Optional[int] = None,
    coaxiality_tol_mm: float = 0.05,
) -> PlanetaryAssemblyCheck:
    """Verify planetary assembly tooth-count condition, coaxiality, and neighbour clearance.

    For a simple planetary stage (``z_p1 == z_p2`` or omitted), equal spacing
    requires ``(z_s + z_r) / N`` to be an integer.

    For a compound (stepped-planet) stage with planet gears ``z_p1`` (sun side)
    and ``z_p2`` (ring side) rigidly keyed together on each planet pin, spinning
    the planet on its pin shifts ring engagement modulo ``gcd(z_p1, z_p2) / z_p1``
    teeth, so equal planet spacing is possible iff
    ``(z_s * z_p2 + z_r * z_p1) / (N * gcd(z_p1, z_p2))`` is an integer.

    When equal spacing is not an integer, valid non-equal planet angles on the
    discrete valid tooth lattice ``2*pi * m / M_slots`` are computed and checked
    for neighbour planet tip-to-tip clearance.
    """
    if n_planets <= 0:
        raise ValueError(f"n_planets must be >= 1, got {n_planets}")

    flags: List[str] = []
    if z_p1 is None or z_p2 is None or z_p1 == z_p2:
        n_slots = z_s + z_r
    else:
        g = math.gcd(z_p1, z_p2)
        n_slots = (z_s * z_p2 + z_r * z_p1) // g

    idx_val = n_slots / float(n_planets)
    equal_possible = (n_slots % n_planets) == 0

    if equal_possible:
        step = 360.0 / n_planets
        angles_deg = [k * step for k in range(n_planets)]
    else:
        flags.append("non_equal_planet_spacing_required")
        if n_slots < n_planets:
            raise ValueError(
                f"Cannot place {n_planets} planets when only {n_slots} valid mesh slots exist"
            )
        # Pick n_planets distinct valid integer slot indices closest to uniform spacing
        chosen: List[int] = []
        used = set()
        for k in range(n_planets):
            ideal = round(k * n_slots / n_planets) % n_slots
            slot = ideal
            offset = 0
            while slot in used:
                offset = -offset if offset > 0 else (-offset + 1)
                slot = (ideal + offset) % n_slots
            used.add(slot)
            chosen.append(slot)
        chosen.sort()
        angles_deg = [360.0 * s / n_slots for s in chosen]

    if n_planets == 1:
        min_step_deg = 360.0
        clearance_mm = math.inf
        clearance_ok = True
    else:
        diffs = [
            (angles_deg[(i + 1) % n_planets] - angles_deg[i]) % 360.0
            for i in range(n_planets)
        ]
        min_step_deg = min(diffs)
        # Adjacent planet centre-to-centre chord at orbital radius a_w
        a_orb = 0.5 * (a_w_sp_mm + a_w_pr_mm)
        chord_mm = 2.0 * a_orb * math.sin(0.5 * math.radians(min_step_deg))
        clearance_mm = chord_mm - d_a_planet_mm
        clearance_ok = clearance_mm > 0.0
        if not clearance_ok:
            flags.append("neighbour_planet_interference")

    coax_err = abs(a_w_sp_mm - a_w_pr_mm)
    coax_ok = coax_err <= coaxiality_tol_mm
    if not coax_ok:
        flags.append("coaxiality_mismatch")

    assembly_ok = clearance_ok and coax_ok
    if not equal_possible:
        # Equal spacing failed, though non-equal placement may still physically fit
        flags.append("equal_spacing_failed")

    return PlanetaryAssemblyCheck(
        n_planets=n_planets,
        equal_spacing_possible=equal_possible,
        assembly_index_value=idx_val,
        planet_angles_deg=angles_deg,
        min_angular_step_deg=min_step_deg,
        neighbour_clearance_mm=clearance_mm,
        neighbour_clearance_ok=clearance_ok,
        coaxiality_error_mm=coax_err,
        coaxiality_ok=coax_ok,
        assembly_ok=assembly_ok,
        flags=flags,
    )


def pair_kinematics(
    gear1: GearSpec,
    gear2: GearSpec,
    *,
    input_speed_rpm: float = 1000.0,
    centre_distance_mm: Optional[float] = None,
    pair_geom: Optional[PairGeometryResult] = None,
) -> StageKinematicsResult:
    """Kinematics of a single external or internal gear pair."""
    geom = pair_geom or analyze_pair(gear1, gear2, centre_distance_mm=centre_distance_mm)
    # External pair reverses shaft rotation; internal pair preserves direction
    signed_ratio = (gear2.z / gear1.z) if gear2.is_internal else -(gear2.z / gear1.z)
    out_rpm = input_speed_rpm / signed_ratio
    flags = list(geom.flags)
    return StageKinematicsResult(
        stage_kind="spur_pair",
        ratio=signed_ratio,
        abs_ratio=abs(signed_ratio),
        reverses_direction=(signed_ratio < 0.0),
        fixed_member=None,
        input_member="gear1",
        output_member="gear2",
        input_speed_rpm=float(input_speed_rpm),
        output_speed_rpm=out_rpm,
        member_speeds_rpm={"gear1": float(input_speed_rpm), "gear2": out_rpm},
        assembly_ok=geom.valid_mesh,
        state=("valid" if geom.valid_mesh else "failed"),
        applicability_evidence={"u_ratio": geom.u_ratio, "valid_mesh": geom.valid_mesh},
        method="SinglePair_Kinematics",
        flags=flags,
    )


def willis_speeds(
    u: float,
    *,
    fixed_member: str,
    input_member: str,
    output_member: str,
    input_speed_rpm: float,
) -> Tuple[float, Dict[str, float]]:
    """Solve member speeds from Willis equation: omega_s + u * omega_r - (1 + u) * omega_c = 0.

    ``u`` is the stationary-carrier ratio magnitude ``-(omega_s - omega_c)/(omega_r - omega_c)``:
    ``u = z_r / z_s`` for a simple planetary stage and ``u = (z_p1 * z_r) / (z_s * z_p2)``
    for a stepped-planet (compound) stage.
    """
    valid = {"sun", "ring", "carrier"}
    if fixed_member not in valid or input_member not in valid or output_member not in valid:
        raise ValueError(f"Members must be in {valid}, got {fixed_member}, {input_member}, {output_member}")
    if len({fixed_member, input_member, output_member}) != 3:
        raise ValueError("fixed_member, input_member, and output_member must be three distinct members")
    if u <= 1.0:
        raise ValueError(f"Planetary characteristic ratio u must be > 1, got {u}")

    # Linear system with omega_fixed = 0 and omega_input = input_speed_rpm:
    # sun + u * ring - (1 + u) * carrier = 0
    coeff = {"sun": 1.0, "ring": u, "carrier": -(1.0 + u)}
    ratio = -coeff[output_member] / coeff[input_member]  # omega_in / omega_out!
    # Check: coeff[in]*omega_in + coeff[out]*omega_out = 0 => omega_in / omega_out = -coeff[out] / coeff[in]
    speeds = {
        fixed_member: 0.0,
        input_member: float(input_speed_rpm),
        output_member: float(input_speed_rpm) / ratio,
    }
    return ratio, speeds


def planetary_kinematics(
    *,
    sun: GearSpec,
    planet: GearSpec,
    ring: GearSpec,
    n_planets: int = 3,
    fixed_member: str = "ring",
    input_member: str = "sun",
    output_member: str = "carrier",
    input_speed_rpm: float = 1000.0,
    planet2: Optional[GearSpec] = None,
    sun_planet_geom: Optional[PairGeometryResult] = None,
    planet_ring_geom: Optional[PairGeometryResult] = None,
) -> StageKinematicsResult:
    """Kinematics and assembly checks for a simple or stepped-planet planetary stage."""
    if not ring.is_internal:
        raise ValueError("ring GearSpec must have is_internal=True")
    if sun.is_internal or planet.is_internal:
        raise ValueError("sun and planet must be external gears")

    p_ring_gear = planet2 if planet2 is not None else planet
    if p_ring_gear.is_internal:
        raise ValueError("planet2 must be an external gear")

    sp = sun_planet_geom or analyze_pair(sun, planet)
    pr = planet_ring_geom or analyze_pair(p_ring_gear, ring)

    is_compound = planet2 is not None
    u = (planet.z * ring.z) / (sun.z * p_ring_gear.z)

    ratio, speeds = willis_speeds(
        u,
        fixed_member=fixed_member,
        input_member=input_member,
        output_member=output_member,
        input_speed_rpm=input_speed_rpm,
    )

    # Planet speed relative to carrier (from sun-planet mesh in carrier frame)
    omega_s = speeds["sun"]
    omega_c = speeds["carrier"]
    omega_p_rel = -(sun.z / planet.z) * (omega_s - omega_c)
    speeds["planet_abs"] = omega_c + omega_p_rel
    speeds["planet_rel_carrier"] = omega_p_rel

    d_a_max_planet = max(sp.gear2.d_a_mm, pr.gear1.d_a_mm)
    asm = check_planetary_assembly(
        z_s=sun.z,
        z_r=ring.z,
        n_planets=n_planets,
        a_w_sp_mm=sp.a_w_mm,
        a_w_pr_mm=pr.a_w_mm,
        d_a_planet_mm=d_a_max_planet,
        z_p1=planet.z,
        z_p2=p_ring_gear.z,
    )

    flags = list(asm.flags)
    flags.extend(f"sp:{fl}" for fl in sp.flags)
    flags.extend(f"pr:{fl}" for fl in pr.flags)
    if not (sp.valid_mesh and pr.valid_mesh):
        flags.append("mesh_geometry_invalid")

    return StageKinematicsResult(
        stage_kind="compound_planetary" if is_compound else "planetary",
        ratio=ratio,
        abs_ratio=abs(ratio),
        reverses_direction=(ratio < 0.0),
        fixed_member=fixed_member,
        input_member=input_member,
        output_member=output_member,
        input_speed_rpm=float(input_speed_rpm),
        output_speed_rpm=speeds[output_member],
        member_speeds_rpm=speeds,
        planet_relative_speed_rpm=omega_p_rel,
        stationary_carrier_ratio_u=u,
        equal_spacing_possible=asm.equal_spacing_possible,
        planet_angles_deg=asm.planet_angles_deg,
        neighbour_clearance_mm=asm.neighbour_clearance_mm,
        neighbour_clearance_ok=asm.neighbour_clearance_ok,
        coaxiality_error_mm=asm.coaxiality_error_mm,
        coaxiality_ok=asm.coaxiality_ok,
        assembly_ok=asm.assembly_ok and sp.valid_mesh and pr.valid_mesh,
        state=("valid" if (asm.assembly_ok and sp.valid_mesh and pr.valid_mesh) else "failed"),
        applicability_evidence={
            "n_planets": n_planets,
            "equal_spacing_possible": asm.equal_spacing_possible,
            "assembly_index_value": asm.assembly_index_value,
            "coaxiality_error_mm": asm.coaxiality_error_mm,
            "neighbour_clearance_mm": asm.neighbour_clearance_mm,
        },
        method="Willis_Planetary_Kinematics",
        flags=flags,
    )


def chain_kinematics(
    stage_results: Sequence[StageKinematicsResult],
) -> ChainKinematicsResult:
    """Combine an ordered sequence of stage kinematics results into an overall chain."""
    if not stage_results:
        raise ValueError("stage_results must be non-empty")
    ratio_total = 1.0
    flags: List[str] = []
    all_ok = True
    for idx, st in enumerate(stage_results):
        ratio_total *= st.ratio
        if not st.assembly_ok:
            all_ok = False
        flags.extend(f"stage{idx}:{fl}" for fl in st.flags)
    in_rpm = stage_results[0].input_speed_rpm
    out_rpm = in_rpm / ratio_total
    return ChainKinematicsResult(
        stages=list(stage_results),
        overall_ratio=ratio_total,
        abs_overall_ratio=abs(ratio_total),
        reverses_direction=(ratio_total < 0.0),
        input_speed_rpm=in_rpm,
        output_speed_rpm=out_rpm,
        all_assemblies_ok=all_ok,
        state=("valid" if all_ok else "failed"),
        applicability_evidence={"n_stages": len(stage_results), "overall_ratio": ratio_total},
        method="StageChain_Kinematics",
        flags=flags,
    )
