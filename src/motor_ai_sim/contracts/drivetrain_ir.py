# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Drive-train component contracts: motor -> transmission -> load on one shaft chain.

Three IRs (see docs/COMPONENT_SDK.md for the full specification):

  * ``MotorPerformanceIR`` - OUR side, read-only for modules: what a motor does at
    its shaft (torque-speed envelope, efficiency map, thermal limits, mass,
    inertia, shaft interface).  Built from the motor passport.  It carries
    PERFORMANCE only - never stator/rotor geometry or any AeroStator Core design
    data (``extra="forbid"`` + the conformance gate keep it that way).
  * ``TransmissionIR`` - a gearbox / belt / chain between two shafts: ratio, loss
    maps over (input speed, input torque) split into named groups, inertia, mass,
    backlash, limits.
  * ``ShaftLoadIR`` and ``PropulsorIR`` - what absorbs the shaft power: a generic
    torque map, or a propeller given by C_T / C_P over (rpm, advance ratio J).

Units are fixed for the whole family (``UNITS_TAG``): N*m, rpm, W, kg, kg*m^2,
degC, m/s, kg/m^3; shaft dimensions in mm; a propeller diameter in m.  Every
field name carries its unit.  Evaluation helpers REFUSE (``OutOfValidity``)
instead of extrapolating a loss or torque map; the one documented exception is a
propeller coefficient held at its tested edge and flagged ``extrapolated`` (the
behaviour of ``motor_ai_sim.propeller``).

This module imports nothing from the rest of motor_ai_sim except ``common``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import Provenance

#: One unit system for the whole family.  A producer must write exactly this tag.
UNITS_TAG = "SI-rpm-v1"

ModelKind = Literal["analytical", "measured", "published", "datasheet", "fem", "cfd"]
ShaftKind = Literal["keyed", "spline", "clamp", "flange", "bore", "press_fit", "other"]
TransmissionKind = Literal["gearbox", "belt", "chain", "direct", "other"]
LoadKind = Literal["propeller", "fan", "pump", "flywheel", "constant_torque", "generator", "other"]


class OutOfValidity(ValueError):
    """A map was asked for a point outside the range its producer declared valid."""


# ---------------------------------------------------------------------------
# numeric helpers (pure, no numpy: the IRs are the wire format)
# ---------------------------------------------------------------------------

def _is_increasing(xs: Sequence[float]) -> bool:
    return all(b > a for a, b in zip(xs, xs[1:]))


def _bracket(xs: Sequence[float], x: float) -> Tuple[int, float]:
    """(i, t) with xs[i] <= x <= xs[i+1] and t in [0, 1]; ``x`` must be inside xs."""
    if len(xs) == 1:
        return 0, 0.0
    lo, hi = 0, len(xs) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if xs[mid] <= x:
            lo = mid
        else:
            hi = mid
    return lo, (x - xs[lo]) / (xs[hi] - xs[lo])


def _inside(xs: Sequence[float], x: float, what: str) -> None:
    if not (xs[0] - 1e-9 <= x <= xs[-1] + 1e-9):
        raise OutOfValidity(f"{what} = {x:g} is outside the declared range [{xs[0]:g}, {xs[-1]:g}]")


def interp1(xs: Sequence[float], ys: Sequence[float], x: float, what: str = "x") -> float:
    """Linear interpolation; refuses outside ``xs``."""
    _inside(xs, x, what)
    i, t = _bracket(xs, min(max(x, xs[0]), xs[-1]))
    if len(xs) == 1:
        return float(ys[0])
    return float(ys[i]) * (1.0 - t) + float(ys[i + 1]) * t


def bilinear(xs: Sequence[float], ys: Sequence[float], tab: Sequence[Sequence[Optional[float]]],
             x: float, y: float, what: Tuple[str, str] = ("x", "y"), *, clamp: bool = False
             ) -> Optional[float]:
    """Bilinear interpolation of ``tab[i_x][j_y]``.  Refuses outside the axes unless
    ``clamp`` (then the coordinate is held at the edge).  ``None`` if a needed
    corner is ``None`` (an unreachable cell)."""
    if clamp:
        x = min(max(x, xs[0]), xs[-1])
        y = min(max(y, ys[0]), ys[-1])
    else:
        _inside(xs, x, what[0])
        _inside(ys, y, what[1])
        x = min(max(x, xs[0]), xs[-1])
        y = min(max(y, ys[0]), ys[-1])
    i, tx = _bracket(xs, x)
    j, ty = _bracket(ys, y)
    i1 = min(i + 1, len(xs) - 1)
    j1 = min(j + 1, len(ys) - 1)
    corners = [(tab[i][j], (1 - tx) * (1 - ty)), (tab[i1][j], tx * (1 - ty)),
               (tab[i][j1], (1 - tx) * ty), (tab[i1][j1], tx * ty)]
    total = 0.0
    for v, w in corners:
        if w <= 0.0:
            continue
        if v is None:
            return None
        total += float(v) * w
    return total


def _check_table(name: str, tab: Sequence[Sequence[Optional[float]]], n_rows: int, n_cols: int,
                 *, allow_none: bool = False) -> None:
    if len(tab) != n_rows or any(len(r) != n_cols for r in tab):
        raise ValueError(f"{name}: must be a {n_rows} x {n_cols} table [speed][second axis]")
    for r in tab:
        for v in r:
            if v is None:
                if not allow_none:
                    raise ValueError(f"{name}: null cells are not allowed")
            elif not math.isfinite(float(v)):
                raise ValueError(f"{name}: non-finite value")


def _check_axis(name: str, xs: Sequence[float], *, min_value: Optional[float] = None) -> None:
    if len(xs) < 1:
        raise ValueError(f"{name}: axis is empty")
    if any(not math.isfinite(float(x)) for x in xs):
        raise ValueError(f"{name}: non-finite value")
    if not _is_increasing(xs):
        raise ValueError(f"{name}: axis must be strictly increasing")
    if min_value is not None and xs[0] < min_value:
        raise ValueError(f"{name}: axis must be >= {min_value:g}")


# ---------------------------------------------------------------------------
# shared building blocks
# ---------------------------------------------------------------------------

class ShaftInterface(BaseModel):
    """The mechanical interface of one shaft end (the only motor 'dimensions' a
    module may see: what mates, not what is inside)."""

    model_config = ConfigDict(extra="forbid")

    kind: ShaftKind
    diameter_mm: float = Field(gt=0, le=2000)
    length_mm: Optional[float] = Field(default=None, gt=0, le=5000)
    torque_rating_Nm: Optional[float] = Field(default=None, gt=0)
    note: str = ""


class ModelSource(BaseModel):
    """Where the numbers come from.  ``validated_against`` is what the model was
    compared with (measurement, published data); None = NOT validated."""

    model_config = ConfigDict(extra="forbid")

    kind: ModelKind
    reference: str = Field(min_length=3)
    validated_against: Optional[str] = None


class Validity(BaseModel):
    """The range inside which the producer stands behind the numbers.  The map
    axes define the hard domain; this block states it in words and adds the
    ambient range the data hold for."""

    model_config = ConfigDict(extra="forbid")

    ambient_C: Tuple[float, float] = (-10.0, 50.0)
    notes: str = ""


# ---------------------------------------------------------------------------
# MotorPerformanceIR (our side)
# ---------------------------------------------------------------------------

class EfficiencyMap(BaseModel):
    """Motor efficiency at the SHAFT over (speed, shaft torque); ``None`` = the
    point is not reachable.  ``basis`` states the temperatures and the drive."""

    model_config = ConfigDict(extra="forbid")

    speed_rpm: List[float]
    torque_Nm: List[float]
    eta: List[List[Optional[float]]]            # [speed][torque]
    basis: str

    @model_validator(mode="after")
    def _shape(self) -> "EfficiencyMap":
        _check_axis("efficiency.speed_rpm", self.speed_rpm, min_value=0.0)
        _check_axis("efficiency.torque_Nm", self.torque_Nm, min_value=0.0)
        _check_table("efficiency.eta", self.eta, len(self.speed_rpm), len(self.torque_Nm), allow_none=True)
        return self


class MotorLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speed_max_rpm: float = Field(gt=0)
    winding_C: Optional[float] = None
    magnet_C: Optional[float] = None
    current_max_A: Optional[float] = Field(default=None, gt=0)
    dc_voltage_min_V: Optional[float] = Field(default=None, gt=0)
    dc_voltage_max_V: Optional[float] = Field(default=None, gt=0)


class MotorPerformanceIR(BaseModel):
    """What the motor does at its shaft.  Built by the portal from the passport;
    a module only READS it (to size or check itself).  No geometry, ever."""

    model_config = ConfigDict(extra="forbid")

    ir_schema: Literal["motor_performance-1"] = "motor_performance-1"
    units: str = UNITS_TAG
    id: str = Field(min_length=1)                # an opaque handle, e.g. "ciano14_40:L12"
    name: str = ""
    speed_rpm: List[float]                       # envelope axis
    torque_peak_Nm: List[float]                  # most shaft torque the motor gives at each speed (short time)
    peak_basis: str                              # which limits shaped the envelope
    torque_continuous_Nm: Optional[List[float]] = None
    continuous_basis: Optional[str] = None       # the cooling the continuous curve assumes
    efficiency: Optional[EfficiencyMap] = None
    limits: MotorLimits
    mass_kg: Optional[float] = Field(default=None, gt=0)
    rotor_inertia_kg_m2: Optional[float] = Field(default=None, gt=0)
    shaft: Optional[ShaftInterface] = None
    s1_available: bool = False                   # True: the portal can run the propeller-loaded S1 rating
    validity: Validity = Field(default_factory=Validity)
    source: ModelSource
    basis: Dict[str, str] = Field(default_factory=dict)   # quantity -> provenance of that number
    provenance: Optional[Provenance] = None

    @model_validator(mode="after")
    def _shape(self) -> "MotorPerformanceIR":
        _check_axis("speed_rpm", self.speed_rpm, min_value=0.0)
        n = len(self.speed_rpm)
        if len(self.torque_peak_Nm) != n:
            raise ValueError("torque_peak_Nm must match speed_rpm")
        if self.torque_continuous_Nm is not None and len(self.torque_continuous_Nm) != n:
            raise ValueError("torque_continuous_Nm must match speed_rpm")
        return self

    # -- evaluation -----------------------------------------------------
    def torque_peak_at(self, rpm: float) -> float:
        """Peak shaft torque at ``rpm`` (the first value is held below the first
        speed; refuses above the last: the motor was not characterised there)."""
        if rpm < self.speed_rpm[0]:
            return float(self.torque_peak_Nm[0])
        return interp1(self.speed_rpm, self.torque_peak_Nm, rpm, "motor speed [rpm]")

    def efficiency_at(self, rpm: float, torque_Nm: float) -> Optional[float]:
        if self.efficiency is None:
            return None
        e = self.efficiency
        return bilinear(e.speed_rpm, e.torque_Nm, e.eta, rpm, torque_Nm, ("motor speed [rpm]", "motor torque [N*m]"))


# ---------------------------------------------------------------------------
# TransmissionIR
# ---------------------------------------------------------------------------

class TransmissionLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    torque_in_max_Nm: float = Field(gt=0)
    torque_out_max_Nm: Optional[float] = Field(default=None, gt=0)
    speed_in_max_rpm: float = Field(gt=0)
    speed_out_max_rpm: Optional[float] = Field(default=None, gt=0)
    loss_continuous_max_W: Optional[float] = Field(default=None, gt=0)   # heat it can reject at ambient_ref_C
    ambient_ref_C: float = 25.0
    housing_temp_max_C: Optional[float] = None


class TransmissionIR(BaseModel):
    """A speed/torque converter between the motor shaft (input) and the load
    shaft (output).  The loss maps are over the INPUT side (motor side) and give
    the heat the transmission dissipates, in W, by named group; the efficiency
    is DERIVED (eta = 1 - loss / P_in), never stored, so the two cannot disagree."""

    model_config = ConfigDict(extra="forbid")

    ir_schema: Literal["transmission-1"] = "transmission-1"
    units: str = UNITS_TAG
    id: str = Field(min_length=1)
    name: str = ""
    kind: TransmissionKind
    ratio: float = Field(gt=0, le=10000)         # speed_in / speed_out; > 1 = reduction
    reverses_direction: bool = False
    speed_in_rpm: List[float]                    # loss-map axis 1 (strictly increasing, may start at 0)
    torque_in_Nm: List[float]                    # loss-map axis 2 (strictly increasing, may start at 0)
    loss_W: Dict[str, List[List[float]]]         # group -> [speed][torque], heat in W
    inertia_in_kg_m2: float = Field(ge=0)        # about the input shaft
    inertia_out_kg_m2: float = Field(ge=0)       # about the output shaft
    mass_kg: float = Field(gt=0)
    backlash_deg: Optional[float] = Field(default=None, ge=0)   # at the output shaft; 0 = none
    backdrivable: bool = False                   # v0.1 contract covers motoring (motor -> load) only
    limits: TransmissionLimits
    input_shaft: Optional[ShaftInterface] = None
    output_shaft: Optional[ShaftInterface] = None
    validity: Validity = Field(default_factory=Validity)
    source: ModelSource
    basis: Dict[str, str] = Field(default_factory=dict)
    provenance: Optional[Provenance] = None

    @model_validator(mode="after")
    def _shape(self) -> "TransmissionIR":
        _check_axis("speed_in_rpm", self.speed_in_rpm, min_value=0.0)
        _check_axis("torque_in_Nm", self.torque_in_Nm, min_value=0.0)
        if not self.loss_W:
            raise ValueError("loss_W needs at least one group")
        for g, tab in self.loss_W.items():
            _check_table(f"loss_W.{g}", tab, len(self.speed_in_rpm), len(self.torque_in_Nm))
        return self

    # -- evaluation -----------------------------------------------------
    def loss_split_W(self, speed_in_rpm: float, torque_in_Nm: float) -> Dict[str, float]:
        """Heat by group at an input (speed, torque); refuses outside the axes."""
        out: Dict[str, float] = {}
        for g, tab in self.loss_W.items():
            v = bilinear(self.speed_in_rpm, self.torque_in_Nm, tab, speed_in_rpm, torque_in_Nm,
                         ("transmission input speed [rpm]", "transmission input torque [N*m]"))
            out[g] = float(v)                       # no None cells (checked at construction)
        return out

    def loss_W_at(self, speed_in_rpm: float, torque_in_Nm: float) -> float:
        return sum(self.loss_split_W(speed_in_rpm, torque_in_Nm).values())

    def efficiency_at(self, speed_in_rpm: float, torque_in_Nm: float) -> Optional[float]:
        """1 - loss / P_in; None at zero input power."""
        p_in = torque_in_Nm * speed_in_rpm * math.pi / 30.0
        if p_in <= 0.0:
            return None
        return 1.0 - self.loss_W_at(speed_in_rpm, torque_in_Nm) / p_in

    def input_torque_for_output(self, speed_in_rpm: float, torque_out_Nm: float) -> float:
        """The motor-side torque that delivers ``torque_out_Nm`` at the output at
        input speed ``speed_in_rpm``: solves T_in * w_in - loss(n_in, T_in) =
        T_out * w_out by bisection (the conformance gate guarantees the left
        side rises with T_in).  At standstill no power flows: T_in = T_out / ratio."""
        if torque_out_Nm < 0.0:
            raise OutOfValidity("v0.1 transmissions are motoring-only: the output torque must be >= 0")
        w_in = speed_in_rpm * math.pi / 30.0
        if w_in <= 0.0:
            return torque_out_Nm / self.ratio
        p_out = torque_out_Nm * w_in / self.ratio

        def g(t: float) -> float:
            return t * w_in - self.loss_W_at(speed_in_rpm, t)

        lo, hi = 0.0, self.torque_in_Nm[-1]
        if g(hi) < p_out:
            raise OutOfValidity(
                f"the output needs more than the loss maps cover: input torque would exceed "
                f"{hi:g} N*m at {speed_in_rpm:g} rpm")
        if g(lo) >= p_out:
            return 0.0
        for _ in range(80):
            mid = 0.5 * (lo + hi)
            if g(mid) < p_out:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)


# ---------------------------------------------------------------------------
# Loads
# ---------------------------------------------------------------------------

@dataclass
class LoadPoint:
    """What a load says at one shaft speed (the common currency of the system solve)."""

    rpm: float
    torque_Nm: float
    shaft_power_W: float
    output_name: Optional[str] = None          # "thrust"
    output: float = 0.0                        # the useful quantity (N for thrust)
    output_unit: Optional[str] = None
    wake_speed_ms: Optional[float] = None      # developed slipstream speed (propulsors); cooling air for the motor
    extrapolated: bool = False
    power_estimated: bool = False
    notes: List[str] = field(default_factory=list)


class _LoadBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    units: str = UNITS_TAG
    id: str = Field(min_length=1)
    name: str = ""
    kind: LoadKind
    inertia_kg_m2: Optional[float] = Field(default=None, ge=0)     # polar, about the shaft
    mass_kg: Optional[float] = Field(default=None, gt=0)
    shaft_interface: Optional[ShaftInterface] = None
    validity: Validity = Field(default_factory=Validity)
    source: ModelSource
    basis: Dict[str, str] = Field(default_factory=dict)
    provenance: Optional[Provenance] = None


class ShaftLoadIR(_LoadBase):
    """A generic load as a torque map over (speed, one condition axis).  The
    condition is e.g. ``airspeed_ms`` or ``none`` (a single column ``[0.0]``)."""

    ir_schema: Literal["shaft_load-1"] = "shaft_load-1"
    speed_rpm: List[float]
    condition_name: str = "none"
    condition: List[float] = Field(default_factory=lambda: [0.0])
    torque_Nm: List[List[float]]                 # [speed][condition]
    output_name: Optional[str] = None
    output_unit: Optional[str] = None
    output: Optional[List[List[float]]] = None   # [speed][condition]

    @model_validator(mode="after")
    def _shape(self) -> "ShaftLoadIR":
        _check_axis("speed_rpm", self.speed_rpm, min_value=0.0)
        _check_axis("condition", self.condition)
        _check_table("torque_Nm", self.torque_Nm, len(self.speed_rpm), len(self.condition))
        if self.output is not None:
            _check_table("output", self.output, len(self.speed_rpm), len(self.condition))
            if not self.output_name or not self.output_unit:
                raise ValueError("output needs output_name and output_unit")
        return self

    def evaluate(self, rpm: float, *, rho: float = 1.225, airspeed_ms: float = 0.0) -> LoadPoint:
        cond = airspeed_ms if self.condition_name == "airspeed_ms" else self.condition[0]
        q = bilinear(self.speed_rpm, self.condition, self.torque_Nm, rpm, cond,
                     ("load speed [rpm]", f"load condition {self.condition_name}"))
        o = (bilinear(self.speed_rpm, self.condition, self.output, rpm, cond)
             if self.output is not None else 0.0)
        return LoadPoint(rpm=rpm, torque_Nm=float(q), shaft_power_W=float(q) * rpm * math.pi / 30.0,
                         output_name=self.output_name, output=float(o or 0.0), output_unit=self.output_unit)


class PropulsorIR(_LoadBase):
    """A propeller by its coefficients: C_T and C_P over (rpm, advance ratio J)::

        T = C_T * rho * n^2 * D^4     P = C_P * rho * n^3 * D^5     Q = P / (2 pi n)
        J = V / (n D)                 (n in rev/s, V the airspeed along the axis)

    ``rpm`` carries the Reynolds trend (one row is allowed).  A coefficient is
    HELD at the edge of the rpm axis and flagged ``extrapolated`` (the behaviour
    of ``motor_ai_sim.propeller``).  Outside the J axis the IR REFUSES: static
    data (``advance_ratio == [0.0]``) say nothing about forward flight."""

    ir_schema: Literal["propulsor-1"] = "propulsor-1"
    kind: LoadKind = "propeller"
    diameter_m: float = Field(gt=0, le=20)
    blades: Optional[int] = Field(default=None, ge=1, le=12)
    speed_rpm: List[float]
    advance_ratio: List[float] = Field(default_factory=lambda: [0.0])
    ct: List[List[float]]                        # [rpm][J]
    cp: List[List[float]]                        # [rpm][J]
    test_density_kg_m3: float = Field(default=1.225, gt=0.1, lt=3)
    cp_estimated: bool = False                   # C_P is an estimate, not measured torque

    @model_validator(mode="after")
    def _shape(self) -> "PropulsorIR":
        _check_axis("speed_rpm", self.speed_rpm, min_value=0.0)
        _check_axis("advance_ratio", self.advance_ratio, min_value=0.0)
        _check_table("ct", self.ct, len(self.speed_rpm), len(self.advance_ratio))
        _check_table("cp", self.cp, len(self.speed_rpm), len(self.advance_ratio))
        return self

    @property
    def disc_area_m2(self) -> float:
        return math.pi * self.diameter_m ** 2 / 4.0

    @property
    def static_only(self) -> bool:
        return self.advance_ratio == [0.0]

    def evaluate(self, rpm: float, *, rho: float = 1.225, airspeed_ms: float = 0.0) -> LoadPoint:
        n = abs(float(rpm)) / 60.0
        if n == 0.0:
            return LoadPoint(rpm=0.0, torque_Nm=0.0, shaft_power_W=0.0, output_name="thrust", output=0.0,
                             output_unit="N", wake_speed_ms=max(airspeed_ms, 0.0),
                             power_estimated=self.cp_estimated)
        D = self.diameter_m
        J = max(airspeed_ms, 0.0) / (n * D)
        if J > self.advance_ratio[-1] + 1e-9:
            raise OutOfValidity(
                f"advance ratio J = {J:.3f} is above the declared range [0, {self.advance_ratio[-1]:g}]"
                + (" (static data only)" if self.static_only else ""))
        extrap = not (self.speed_rpm[0] - 1e-9 <= rpm <= self.speed_rpm[-1] + 1e-9)
        ct = bilinear(self.speed_rpm, self.advance_ratio, self.ct, rpm, J, clamp=True)
        cp = bilinear(self.speed_rpm, self.advance_ratio, self.cp, rpm, J, clamp=True)
        thrust = ct * rho * n ** 2 * D ** 4
        power = cp * rho * n ** 3 * D ** 5
        v_wake = math.sqrt(max(airspeed_ms, 0.0) ** 2 + 2.0 * max(thrust, 0.0) / (rho * self.disc_area_m2))
        notes = []
        if extrap:
            notes.append("rpm outside the tested range: coefficients held at the edge")
        return LoadPoint(rpm=rpm, torque_Nm=power / (2.0 * math.pi * n), shaft_power_W=power,
                         output_name="thrust", output=thrust, output_unit="N", wake_speed_ms=v_wake,
                         extrapolated=extrap, power_estimated=self.cp_estimated, notes=notes)


ShaftLoad = ShaftLoadIR | PropulsorIR
