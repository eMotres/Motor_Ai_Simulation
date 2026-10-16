# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""The component card: ONE description for every part of a product (docs/COMPONENT_CARD.md).

A ``ComponentCard`` says what a part is (identity), where it sits and how it is
held (named mounting frames, outer envelope, optional CAD references), how heavy
it is (mass, centre of mass, inertia tensor), how it connects (typed ports) and
how it behaves (a reduced-order ``model``, one of the existing drive-train IRs
or one of the three new minimal IRs defined here).

Reused, not duplicated: ``MotorPerformanceIR``, ``TransmissionIR``, ``PropulsorIR``
and ``ShaftLoadIR`` come from ``drivetrain_ir``; new here are ``BatteryIR``,
``ControllerIR`` and ``RigidBodyIR``.

PARTS (schema 0.2.0).  A card lists its ``parts``: name, ``material_id`` (resolved by
``motor_ai_sim.part_materials`` to a material card with mechanical and thermal properties and a
source for every value), mass and role.  The counted parts add up to the card mass.

PRIVACY (motors).  A motor card carries performance, mass properties, the OUTER
ENVELOPE and, per part, material + mass only (no volume, no dimension).  There is no field for stator, rotor or winding design anywhere in
this module (``extra="forbid"`` everywhere) and no field is called ``geometry``
(that key is on the project's denylist, ``mcp_tools.GEOMETRY_DENYLIST``, which the
conformance gate walks).  CAD of a motor can be referenced only as ``access="nda"``.

Units are the drive-train family's ``UNITS_TAG`` (N*m, rpm, W, kg, kg*m^2, degC, V, A).
Lengths on the card are in mm (frame positions, envelope); the centre of mass is in
m (the inertia tensor is in kg*m^2 about it).  Every field name carries its unit.

This module imports nothing from the rest of motor_ai_sim except ``common`` and
``drivetrain_ir``.
"""
from __future__ import annotations

import math
import re
from typing import Dict, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing_extensions import Annotated

from .common import Provenance
from .drivetrain_ir import (UNITS_TAG, ModelSource, MotorPerformanceIR, PropulsorIR, ShaftInterface,
                            ShaftLoadIR, TransmissionIR, Validity, interp1, _check_axis)

#: Version of the card schema itself (semver).  Same major = readable.
CARD_SCHEMA_VERSION = "0.2.0"          # 0.2.0: ``parts`` (material + mass per part)

ComponentKind = Literal["motor", "propeller", "transmission", "controller", "battery", "frame", "link",
                        "wheel", "other"]
PartRole = Literal["structural", "magnetic", "conductor", "insulation", "fastener", "electronic", "cell", "other"]
#: how a part's mass and material are known: ``computed`` (the app's own mass model), ``catalogue`` (a vendor's
#: published figure), ``measured``, ``assumed`` (a documented default, e.g. a material by vendor/series),
#: ``placeholder`` (an example number until real data exist).
PartStatus = Literal["computed", "catalogue", "measured", "assumed", "placeholder"]
PortType = Literal["shaft", "dc_bus", "phase_ac", "thermal", "signal", "mount"]
PortDirection = Literal["in", "out", "bidirectional"]

_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$")
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_CID = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,95}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
#: Loose SPDX expression: ids joined by AND / OR / WITH, optional parentheses and '+'.
_SPDX = re.compile(r"^[A-Za-z0-9.+\-: ()]+$")


def _tuple3(name: str, v: Tuple[float, float, float]) -> None:
    if any(not math.isfinite(float(x)) for x in v):
        raise ValueError(f"{name}: non-finite value")


# ---------------------------------------------------------------------------
# geometry-ish building blocks
# ---------------------------------------------------------------------------

class CadRef(BaseModel):
    """A reference to a CAD file (STEP for engineering, glTF for the viewer): a path
    or URL plus the sha256 of the file.  ``access="nda"`` = released only through
    the NDA click-through flow, never served by the component library."""

    model_config = ConfigDict(extra="forbid")

    uri: str = Field(min_length=1)
    sha256: Optional[str] = None
    access: Literal["public", "nda"] = "public"

    @field_validator("sha256")
    @classmethod
    def _hash(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _SHA256.match(v):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return v


class CadRefs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: Optional[CadRef] = None
    gltf: Optional[CadRef] = None


class Envelope(BaseModel):
    """The OUTER envelope only: a box or a cylinder (mm).  Centre in the body frame."""

    model_config = ConfigDict(extra="forbid")

    shape: Literal["box", "cylinder"]
    size_mm: Optional[Tuple[float, float, float]] = None        # box: x, y, z
    diameter_mm: Optional[float] = Field(default=None, gt=0, le=20000)   # cylinder
    length_mm: Optional[float] = Field(default=None, gt=0, le=20000)     # cylinder, along ``axis``
    axis: Literal["x", "y", "z"] = "z"
    centre_mm: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    basis: str = ""                                              # where the size comes from

    @model_validator(mode="after")
    def _shape(self) -> "Envelope":
        if self.shape == "box":
            if self.size_mm is None or self.diameter_mm is not None or self.length_mm is not None:
                raise ValueError("a box envelope needs size_mm and nothing else")
            if any(not (0 < x <= 20000) for x in self.size_mm):
                raise ValueError("box size_mm must be in (0, 20000]")
        else:
            if self.diameter_mm is None or self.length_mm is None or self.size_mm is not None:
                raise ValueError("a cylinder envelope needs diameter_mm and length_mm and nothing else")
        _tuple3("envelope.centre_mm", self.centre_mm)
        return self


class Frame(BaseModel):
    """A named frame of the part: position (mm) and rotation (unit quaternion w, x, y, z)
    relative to the part's body frame."""

    model_config = ConfigDict(extra="forbid")

    name: str
    position_mm: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation_wxyz: Tuple[float, float, float, float] = (1.0, 0.0, 0.0, 0.0)
    note: str = ""

    @model_validator(mode="after")
    def _ok(self) -> "Frame":
        if not _NAME.match(self.name):
            raise ValueError(f"frame name {self.name!r} must match {_NAME.pattern}")
        _tuple3("frame.position_mm", self.position_mm)
        n = math.sqrt(sum(float(q) ** 2 for q in self.rotation_wxyz))
        if not math.isfinite(n) or abs(n - 1.0) > 1e-6:
            raise ValueError(f"frame {self.name!r}: rotation_wxyz must be a unit quaternion (norm {n:g})")
        return self


# ---------------------------------------------------------------------------
# mass properties
# ---------------------------------------------------------------------------

Matrix3 = Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[float, float, float]]


class MassProperties(BaseModel):
    """Mass (kg), centre of mass (m, in the body frame) and the inertia tensor (kg*m^2)
    about the centre of mass, axes parallel to the body frame.  ``estimated`` = the
    inertia (or the whole set) is an estimate from the envelope, not a CAD/measured value."""

    model_config = ConfigDict(extra="forbid")

    mass_kg: float = Field(gt=0, le=1e5)
    com_m: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    inertia_kg_m2: Matrix3
    estimated: bool = False
    basis: str = Field(default="", description="where mass, centre and inertia come from")

    @model_validator(mode="after")
    def _physical(self) -> "MassProperties":
        _tuple3("com_m", self.com_m)
        I = self.inertia_kg_m2
        scale = max(abs(I[i][j]) for i in range(3) for j in range(3)) or 1.0
        for i in range(3):
            for j in range(3):
                if not math.isfinite(I[i][j]):
                    raise ValueError("inertia: non-finite value")
                if abs(I[i][j] - I[j][i]) > 1e-9 * scale + 1e-15:
                    raise ValueError("inertia tensor must be symmetric")
        # positive definite (Sylvester): all leading principal minors > 0
        m1 = I[0][0]
        m2 = I[0][0] * I[1][1] - I[0][1] * I[1][0]
        m3 = (I[0][0] * (I[1][1] * I[2][2] - I[1][2] * I[2][1])
              - I[0][1] * (I[1][0] * I[2][2] - I[1][2] * I[2][0])
              + I[0][2] * (I[1][0] * I[2][1] - I[1][1] * I[2][0]))
        if not (m1 > 0 and m2 > 0 and m3 > 0):
            raise ValueError("inertia tensor must be positive definite")
        # a physical body: principal moments obey the triangle inequality (Ia + Ib >= Ic)
        ev = sorted(_eig_sym3(I))
        if ev[0] + ev[1] < ev[2] * (1 - 1e-9):
            raise ValueError("inertia is not physical: principal moments violate Ia + Ib >= Ic")
        return self


def _eig_sym3(a) -> List[float]:
    """Eigenvalues of a symmetric 3x3 matrix (closed form, pure python)."""
    p1 = a[0][1] ** 2 + a[0][2] ** 2 + a[1][2] ** 2
    q = (a[0][0] + a[1][1] + a[2][2]) / 3.0
    if p1 == 0.0:
        return [a[0][0], a[1][1], a[2][2]]
    p2 = (a[0][0] - q) ** 2 + (a[1][1] - q) ** 2 + (a[2][2] - q) ** 2 + 2 * p1
    p = math.sqrt(p2 / 6.0)
    b = [[(a[i][j] - (q if i == j else 0.0)) / p for j in range(3)] for i in range(3)]
    detb = (b[0][0] * (b[1][1] * b[2][2] - b[1][2] * b[2][1])
            - b[0][1] * (b[1][0] * b[2][2] - b[1][2] * b[2][0])
            + b[0][2] * (b[1][0] * b[2][1] - b[1][1] * b[2][0]))
    r = max(-1.0, min(1.0, detb / 2.0))
    phi = math.acos(r) / 3.0
    e1 = q + 2 * p * math.cos(phi)
    e3 = q + 2 * p * math.cos(phi + 2 * math.pi / 3)
    return [e1, 3 * q - e1 - e3, e3]


def diag_inertia(ixx: float, iyy: float, izz: float) -> Matrix3:
    return ((ixx, 0.0, 0.0), (0.0, iyy, 0.0), (0.0, 0.0, izz))


def box_inertia(mass_kg: float, x_m: float, y_m: float, z_m: float) -> Matrix3:
    """Homogeneous box about its centre."""
    return diag_inertia(mass_kg * (y_m ** 2 + z_m ** 2) / 12.0, mass_kg * (x_m ** 2 + z_m ** 2) / 12.0,
                        mass_kg * (x_m ** 2 + y_m ** 2) / 12.0)


def cylinder_inertia(mass_kg: float, diameter_m: float, length_m: float, axis: str = "z") -> Matrix3:
    """Homogeneous solid cylinder about its centre, symmetry axis ``axis``."""
    r2 = (diameter_m / 2.0) ** 2
    ia = 0.5 * mass_kg * r2                                       # about the axis
    it = mass_kg * (3.0 * r2 + length_m ** 2) / 12.0              # transverse
    d = {"x": (ia, it, it), "y": (it, ia, it), "z": (it, it, ia)}[axis]
    return diag_inertia(*d)



# ---------------------------------------------------------------------------
# parts: material + mass (for load simulation: stress, vibration, thermal)
# ---------------------------------------------------------------------------

class ComponentPart(BaseModel):
    """One physical part of a component: WHAT it is made of and how heavy it is.  ``material_id`` resolves to a
    material card (``motor_ai_sim.part_materials.resolve``: density, E, nu, strengths, thermal properties, each
    with its source).  No dimensions, no topology: a motor card lists material + mass only (privacy rule)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    material_id: str = Field(min_length=1, max_length=96)
    mass_kg: float = Field(gt=0, le=1e5)
    volume_cm3: Optional[float] = Field(default=None, gt=0)       # not set on motor parts
    role: PartRole
    status: PartStatus = "assumed"
    counted: bool = Field(default=True, description="False = a real part of the component that is NOT inside "
                                                    "``mass_properties.mass_kg`` (listed so a load study knows it exists)")
    basis: str = ""
    part_id: Optional[str] = Field(default=None, max_length=64, description="the id of the part in THE part tree "
                                   "(``motor_ai_sim.part_tree``): the same id, name and group in Geometry, Materials, the 3-D model "
                                   "and the BOM export (motor parts only)")
    group: Optional[str] = Field(default=None, max_length=32, description="part-tree group: stator, winding, rotor, magnets, shaft, "
                                 "housing, bearings, electronics, fasteners or other")

# ---------------------------------------------------------------------------
# ports
# ---------------------------------------------------------------------------

#: Which rating fields each port type may carry (every other rating field must stay None).
PORT_RATING_FIELDS: Dict[str, Tuple[str, ...]] = {
    "shaft": ("max_torque_Nm", "max_speed_rpm", "rating_kind", "interface"),
    "dc_bus": ("voltage_min_V", "voltage_max_V", "current_max_A", "current_peak_A"),
    "phase_ac": ("voltage_max_V", "current_max_A"),
    "thermal": ("heat_flow_max_W", "temperature_max_C"),
    "signal": ("protocol",),
    "mount": ("load_max_N", "moment_max_Nm"),
}
_ALL_RATINGS = sorted({f for fs in PORT_RATING_FIELDS.values() for f in fs})
#: Rating fields a port of that type MUST carry (so a connection can be checked).
PORT_REQUIRED_RATINGS: Dict[str, Tuple[str, ...]] = {
    "shaft": ("max_torque_Nm", "max_speed_rpm"),
    "dc_bus": ("voltage_min_V", "voltage_max_V", "current_max_A"),
    "phase_ac": ("voltage_max_V", "current_max_A"),
}


class Port(BaseModel):
    """A typed connection point.  Ratings by type:

    * ``shaft``: ``max_torque_Nm``, ``max_speed_rpm``; ``rating_kind`` = ``limit`` (a mechanical
      limit: a motor shaft, a gearbox input) or ``demand`` (what a load draws at its top speed:
      a propeller); optional ``interface`` (diameter, kind).
    * ``dc_bus``: ``voltage_min_V``, ``voltage_max_V``, ``current_max_A`` (continuous), ``current_peak_A``.
    * ``phase_ac``: ``voltage_max_V`` (DC-link voltage the drive runs from), ``current_max_A`` (rms per phase).
    * ``thermal``: ``heat_flow_max_W``, ``temperature_max_C``.  ``signal``: ``protocol``.
    * ``mount``: ``load_max_N``, ``moment_max_Nm``.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    type: PortType
    direction: PortDirection
    frame: Optional[str] = None
    required: bool = False
    # shaft
    max_torque_Nm: Optional[float] = Field(default=None, gt=0)
    max_speed_rpm: Optional[float] = Field(default=None, gt=0)
    rating_kind: Optional[Literal["limit", "demand"]] = None
    interface: Optional[ShaftInterface] = None
    # dc_bus / phase_ac
    voltage_min_V: Optional[float] = Field(default=None, ge=0)
    voltage_max_V: Optional[float] = Field(default=None, gt=0)
    current_max_A: Optional[float] = Field(default=None, gt=0)
    current_peak_A: Optional[float] = Field(default=None, gt=0)
    # thermal
    heat_flow_max_W: Optional[float] = Field(default=None, gt=0)
    temperature_max_C: Optional[float] = None
    # signal
    protocol: Optional[str] = None
    # mount
    load_max_N: Optional[float] = Field(default=None, gt=0)
    moment_max_Nm: Optional[float] = Field(default=None, gt=0)
    note: str = ""

    @model_validator(mode="after")
    def _by_type(self) -> "Port":
        if not _NAME.match(self.name):
            raise ValueError(f"port name {self.name!r} must match {_NAME.pattern}")
        allowed = set(PORT_RATING_FIELDS[self.type])
        for f in _ALL_RATINGS:
            if f not in allowed and getattr(self, f) is not None:
                raise ValueError(f"port {self.name!r}: {f} is not a rating of a {self.type} port")
        for f in PORT_REQUIRED_RATINGS.get(self.type, ()):
            if getattr(self, f) is None:
                raise ValueError(f"port {self.name!r}: a {self.type} port needs {f}")
        if self.type == "shaft" and self.rating_kind is None:
            self.rating_kind = "limit"
        if self.type in ("dc_bus",) and self.voltage_min_V is not None and self.voltage_min_V > self.voltage_max_V:
            raise ValueError(f"port {self.name!r}: voltage_min_V above voltage_max_V")
        if self.type == "mount" and self.direction != "bidirectional":
            raise ValueError(f"port {self.name!r}: a mount is bidirectional")
        return self


# ---------------------------------------------------------------------------
# the three new minimal reduced-order models
# ---------------------------------------------------------------------------

class BatteryIR(BaseModel):
    """A battery pack as the DC link sees it: an EMF behind a resistance that depends on
    the state of charge, with current limits.  Built from ``simulation/battery.py``
    (``BatteryPack``); that model has no state of charge, so its resistance table is flat
    and says so in ``basis``."""

    model_config = ConfigDict(extra="forbid")

    ir_schema: Literal["battery-1"] = "battery-1"
    units: str = UNITS_TAG
    id: str = Field(min_length=1)
    name: str = ""
    chemistry: Optional[str] = None
    cells_series: int = Field(ge=1, le=2000)
    cells_parallel: int = Field(ge=1, le=2000)
    v_nominal_V: float = Field(gt=0)
    v_min_V: float = Field(gt=0)
    v_max_V: float = Field(gt=0)
    capacity_Ah: float = Field(gt=0)                       # whole pack
    soc: List[float]                                       # strictly increasing, within [0, 1]
    r_internal_ohm: List[float]                            # whole pack, at each soc
    ocv_V: Optional[List[float]] = None                    # open-circuit voltage at each soc (optional)
    i_continuous_max_A: float = Field(gt=0)
    i_peak_max_A: Optional[float] = Field(default=None, gt=0)
    peak_duration_s: Optional[float] = Field(default=None, gt=0)
    mass_kg: Optional[float] = Field(default=None, gt=0)
    validity: Validity = Field(default_factory=Validity)
    source: ModelSource
    basis: Dict[str, str] = Field(default_factory=dict)
    provenance: Optional[Provenance] = None

    @model_validator(mode="after")
    def _shape(self) -> "BatteryIR":
        _check_axis("soc", self.soc, min_value=0.0)
        if self.soc[-1] > 1.0 + 1e-12:
            raise ValueError("soc axis must lie within [0, 1]")
        if len(self.r_internal_ohm) != len(self.soc) or any(not (r > 0 and math.isfinite(r)) for r in self.r_internal_ohm):
            raise ValueError("r_internal_ohm must be positive and match soc")
        if self.ocv_V is not None and (len(self.ocv_V) != len(self.soc) or any(not (v > 0) for v in self.ocv_V)):
            raise ValueError("ocv_V must be positive and match soc")
        if not (self.v_min_V <= self.v_nominal_V <= self.v_max_V):
            raise ValueError("need v_min_V <= v_nominal_V <= v_max_V")
        if self.i_peak_max_A is not None and self.i_peak_max_A < self.i_continuous_max_A:
            raise ValueError("i_peak_max_A is below i_continuous_max_A")
        return self

    def r_internal_at(self, soc: float) -> float:
        """Pack resistance at a state of charge; refuses outside the table."""
        return interp1(self.soc, self.r_internal_ohm, soc, "state of charge")

    def terminal_voltage(self, soc: float, current_A: float) -> float:
        """V = V_oc(soc) - I R(soc) for a discharge current ``current_A`` >= 0.  Without an
        ``ocv_V`` table V_oc is the nominal voltage (the battery.py convention)."""
        voc = interp1(self.soc, self.ocv_V, soc, "state of charge") if self.ocv_V else self.v_nominal_V
        return voc - current_A * self.r_internal_at(soc)


class ControllerLossModel(BaseModel):
    """How the controller's losses are obtained: a device-card reference (the loss model in
    ``inverter/losses.py`` evaluated with that part) or a table of loss over phase current."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["device_card", "table"]
    device_part: Optional[str] = None                      # config/devices/<part>.yaml
    n_parallel: Optional[int] = Field(default=None, ge=1)
    carrier_hz: Optional[float] = Field(default=None, gt=0)
    i_phase_A: Optional[List[float]] = None                # table axis (rms per phase), increasing
    loss_W: Optional[List[float]] = None                   # table values at the nominal bus
    note: str = ""

    @model_validator(mode="after")
    def _shape(self) -> "ControllerLossModel":
        if self.kind == "device_card":
            if not self.device_part:
                raise ValueError("a device_card loss model needs device_part")
        else:
            if not self.i_phase_A or self.loss_W is None or len(self.loss_W) != len(self.i_phase_A):
                raise ValueError("a table loss model needs i_phase_A and loss_W of the same length")
            _check_axis("i_phase_A", self.i_phase_A, min_value=0.0)
            if any(v < 0 or not math.isfinite(v) for v in self.loss_W):
                raise ValueError("loss_W must be >= 0")
        return self


class ControllerIR(BaseModel):
    """A motor controller (inverter / ESC) by its ratings: DC-bus voltage range, phase
    currents, loss model, junction-temperature limit.  The current limits are per cooling
    class where the source gives them (``phase_current_continuous_A`` is the most
    conservative one)."""

    model_config = ConfigDict(extra="forbid")

    ir_schema: Literal["controller-1"] = "controller-1"
    units: str = UNITS_TAG
    id: str = Field(min_length=1)
    name: str = ""
    phases: int = Field(default=3, ge=1, le=12)
    bus_voltage_min_V: float = Field(gt=0)
    bus_voltage_max_V: float = Field(gt=0)
    phase_current_continuous_A: float = Field(gt=0)        # rms per phase
    phase_current_peak_A: Optional[float] = Field(default=None, gt=0)
    phase_current_by_cooling_A: Dict[str, float] = Field(default_factory=dict)
    bus_current_continuous_A: Optional[float] = Field(default=None, gt=0)
    loss_model: ControllerLossModel
    t_junction_max_C: Optional[float] = None
    mass_kg: Optional[float] = Field(default=None, gt=0)
    validity: Validity = Field(default_factory=Validity)
    source: ModelSource
    basis: Dict[str, str] = Field(default_factory=dict)
    provenance: Optional[Provenance] = None

    @model_validator(mode="after")
    def _shape(self) -> "ControllerIR":
        if self.bus_voltage_min_V > self.bus_voltage_max_V:
            raise ValueError("bus_voltage_min_V above bus_voltage_max_V")
        if (self.phase_current_peak_A is not None
                and self.phase_current_peak_A < self.phase_current_continuous_A):
            raise ValueError("phase_current_peak_A is below the continuous current")
        return self


class RigidBodyIR(BaseModel):
    """A frame, link or wheel: nothing but mass properties and mounts (the card's
    ``mass_properties`` and ``frames`` / ``ports`` carry them; the model only states that the
    part is a rigid body and where the numbers come from)."""

    model_config = ConfigDict(extra="forbid")

    ir_schema: Literal["rigid_body-1"] = "rigid_body-1"
    units: str = UNITS_TAG
    id: str = Field(min_length=1)
    name: str = ""
    rigid: Literal[True] = True
    placeholder: bool = False                              # True = an example/stand-in, not a real part
    source: ModelSource
    basis: Dict[str, str] = Field(default_factory=dict)
    provenance: Optional[Provenance] = None


ComponentModel = Annotated[
    Union[MotorPerformanceIR, TransmissionIR, PropulsorIR, ShaftLoadIR, BatteryIR, ControllerIR, RigidBodyIR],
    Field(discriminator="ir_schema")]

#: Which model schemas each kind of card may carry.
KIND_MODELS: Dict[str, Tuple[str, ...]] = {
    "motor": ("motor_performance-1",),
    "propeller": ("propulsor-1", "shaft_load-1"),
    "transmission": ("transmission-1",),
    "controller": ("controller-1",),
    "battery": ("battery-1",),
    "frame": ("rigid_body-1",),
    "link": ("rigid_body-1",),
    "wheel": ("rigid_body-1",),
    "other": ("motor_performance-1", "transmission-1", "propulsor-1", "shaft_load-1", "battery-1",
              "controller-1", "rigid_body-1"),
}


# ---------------------------------------------------------------------------
# the card
# ---------------------------------------------------------------------------

class ComponentCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ir_schema: Literal["component_card-1"] = "component_card-1"
    schema_version: str = CARD_SCHEMA_VERSION
    units: str = UNITS_TAG
    # identity
    component_id: str
    kind: ComponentKind
    name: str = Field(min_length=1)
    version: str = "0.1.0"
    licence: str = Field(min_length=2, description="SPDX expression")
    description: str = ""
    provenance: Provenance
    # shape and mass
    cad: Optional[CadRefs] = None
    envelope: Optional[Envelope] = None
    frames: List[Frame] = Field(default_factory=list)
    mass_properties: Optional[MassProperties] = None
    parts: List[ComponentPart] = Field(default_factory=list)
    # connections and behaviour
    ports: List[Port] = Field(default_factory=list)
    model: ComponentModel

    @field_validator("schema_version")
    @classmethod
    def _semver(cls, v: str) -> str:
        m = _SEMVER.match(v)
        if not m:
            raise ValueError("schema_version must be semver (MAJOR.MINOR.PATCH)")
        if int(m.group(1)) != int(CARD_SCHEMA_VERSION.split(".")[0]):
            raise ValueError(f"schema_version {v} is not readable by schema {CARD_SCHEMA_VERSION} (other major)")
        return v

    @model_validator(mode="after")
    def _consistent(self) -> "ComponentCard":
        if not _CID.match(self.component_id):
            raise ValueError(f"component_id {self.component_id!r} must match {_CID.pattern}")
        if not _SEMVER.match(self.version):
            raise ValueError("version must be semver")
        if self.units != UNITS_TAG:
            raise ValueError(f"units must be {UNITS_TAG!r}")
        if not _SPDX.match(self.licence):
            raise ValueError("licence must be an SPDX licence expression")
        if self.model.ir_schema not in KIND_MODELS[self.kind]:
            raise ValueError(f"a {self.kind} card cannot carry a {self.model.ir_schema} model "
                             f"(allowed: {', '.join(KIND_MODELS[self.kind])})")
        names = [f.name for f in self.frames]
        if len(set(names)) != len(names):
            raise ValueError("frame names must be unique")
        pnames = [p.name for p in self.ports]
        if len(set(pnames)) != len(pnames):
            raise ValueError("port names must be unique")
        for p in self.ports:
            if p.frame is not None and p.frame not in names:
                raise ValueError(f"port {p.name!r} refers to the unknown frame {p.frame!r}")
        part_names = [p.name for p in self.parts]
        if len(set(part_names)) != len(part_names):
            raise ValueError("part names must be unique")
        counted = sum(p.mass_kg for p in self.parts if p.counted)
        if self.parts and self.mass_properties is not None and counted > 0:
            m = self.mass_properties.mass_kg
            if abs(counted - m) > 0.02 * m + 1e-9:
                raise ValueError(f"the counted parts add up to {counted:.6g} kg but mass_properties.mass_kg is {m:.6g} kg "
                                 "(parts must add up to the card mass within 2 %; use counted=false for a part outside it)")
        if self.parts and self.mass_properties is None and any(p.counted for p in self.parts):
            raise ValueError("counted parts need mass_properties on the card")
        if self.kind in ("frame", "link", "wheel") and self.mass_properties is None:
            raise ValueError(f"a {self.kind} card needs mass_properties (its model carries nothing else)")
        return self

    def port(self, name: str) -> Port:
        for p in self.ports:
            if p.name == name:
                return p
        raise KeyError(name)

    def frame(self, name: str) -> Frame:
        for f in self.frames:
            if f.name == name:
                return f
        raise KeyError(name)


def card_digest(card: ComponentCard) -> str:
    """sha256 of the canonical JSON of a card (what an AssemblyIR card reference may pin)."""
    import hashlib
    import json
    doc = json.dumps(card.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(doc.encode("utf-8")).hexdigest()
