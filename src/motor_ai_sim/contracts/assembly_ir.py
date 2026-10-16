# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""``AssemblyIR`` - components placed in space and wired port to port (stage 2 seed; data only).

An assembly lists component instances (a reference to a ``ComponentCard`` plus a placement
frame) and the connections between their ports.  This module holds the document, the
structural/rating validator ``validate_assembly`` and the pure-math ``mass_properties``
(total mass, centre of mass, inertia by the parallel-axis theorem).  It solves nothing:
hover points, flight time and missions are later stages (docs/PRODUCT_COMPOSER_ROADMAP.md).

``validate_assembly`` needs the cards the assembly refers to; pass them as a mapping
``{component_id: ComponentCard}`` (the component library provides it).  Findings are
``Issue`` objects with a stable ``code``; ``errors`` make an assembly invalid, ``warnings``
do not:

======================  =========  ====================================================
code                    severity   meaning
======================  =========  ====================================================
unknown_component       error      the card is not in the library
card_changed            error      the pinned sha256 / version no longer matches the card
unknown_port            error      a connection names a port the card does not have
port_reused             error      one port is used by more than one connection
self_connection         error      both ends on the same instance and port
type_mismatch           error      port types differ
direction               error      in-in or out-out
unconnected_port        error      a required port is not connected
dc_voltage              error      the two dc_bus voltage ranges do not overlap
phase_voltage           error      the controller's DC link is above what the motor port is rated for
shaft_speed             error/warn source above a mechanical limit (error) or a load rating (warning)
shaft_torque            error/warn same for torque
dc_voltage_range        warning    the source range is not inside the sink range
dc_current              warning    the sink may draw more than the source rating
dc_current_sum          warning    the sinks on one source together may draw more than it can give
phase_current           warning    the controller can deliver more than the motor port is rated for
shaft_interface         warning    declared shaft diameters differ
======================  =========  ====================================================
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Literal, Mapping, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .common import Provenance
from .component_ir import (CARD_SCHEMA_VERSION, ComponentCard, Frame, Matrix3, Port, card_digest)
from .drivetrain_ir import UNITS_TAG

_INSTANCE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_TOL = 1e-9


# ---------------------------------------------------------------------------
# the document
# ---------------------------------------------------------------------------

class CardRef(BaseModel):
    """A reference to a card.  ``version`` and ``sha256`` (``card_digest``) are optional pins."""

    model_config = ConfigDict(extra="forbid")

    component_id: str
    version: Optional[str] = None
    sha256: Optional[str] = None


class Placement(Frame):
    """Where the instance's body frame sits in the assembly frame (mm, unit quaternion w, x, y, z)."""

    name: str = "placement"


class AssemblyComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance: str
    card: CardRef
    placement: Placement = Field(default_factory=Placement)
    note: str = ""

    @field_validator("instance")
    @classmethod
    def _name(cls, v: str) -> str:
        if not _INSTANCE.match(v):
            raise ValueError(f"instance name {v!r} must match {_INSTANCE.pattern}")
        return v


class PortRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance: str
    port: str

    def __str__(self) -> str:
        return f"{self.instance}.{self.port}"


class Connection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    a: PortRef
    b: PortRef
    note: str = ""


class AssemblyIR(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ir_schema: Literal["assembly-1"] = "assembly-1"
    schema_version: str = CARD_SCHEMA_VERSION
    units: str = UNITS_TAG
    id: str = Field(min_length=1)
    name: str = ""
    description: str = ""
    components: List[AssemblyComponent]
    connections: List[Connection] = Field(default_factory=list)
    provenance: Optional[Provenance] = None

    @model_validator(mode="after")
    def _refs(self) -> "AssemblyIR":
        if self.units != UNITS_TAG:
            raise ValueError(f"units must be {UNITS_TAG!r}")
        names = [c.instance for c in self.components]
        if len(set(names)) != len(names):
            raise ValueError("instance names must be unique")
        for cn in self.connections:
            for end in (cn.a, cn.b):
                if end.instance not in names:
                    raise ValueError(f"connection refers to the unknown instance {end.instance!r}")
        return self


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

@dataclass
class Issue:
    code: str
    severity: Literal["error", "warning"]
    message: str
    where: Optional[str] = None


@dataclass
class AssemblyReport:
    errors: List[Issue] = field(default_factory=list)
    warnings: List[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def codes(self, severity: Optional[str] = None) -> List[str]:
        rows = (self.errors + self.warnings) if severity is None else (
            self.errors if severity == "error" else self.warnings)
        return [i.code for i in rows]

    def as_dict(self) -> Dict[str, object]:
        row = lambda i: {"code": i.code, "message": i.message, "where": i.where}   # noqa: E731
        return {"ok": self.ok, "errors": [row(i) for i in self.errors], "warnings": [row(i) for i in self.warnings]}


Resolver = Union[Mapping[str, ComponentCard], Callable[[CardRef], ComponentCard]]


def _resolve(cards: Resolver, ref: CardRef) -> Optional[ComponentCard]:
    try:
        return cards(ref) if callable(cards) else cards[ref.component_id]
    except (KeyError, LookupError):
        return None


def validate_assembly(asm: AssemblyIR, cards: Resolver) -> AssemblyReport:
    """Check an assembly against its cards: port types and directions, every required port
    connected, no port used twice, dc_bus voltage ranges overlapping, shaft and phase ratings
    compatible.  Never raises on a bad assembly; the report says what is wrong."""
    rep = AssemblyReport()

    def err(code, msg, where=None):
        rep.errors.append(Issue(code, "error", msg, where))

    def warn(code, msg, where=None):
        rep.warnings.append(Issue(code, "warning", msg, where))

    card_of: Dict[str, ComponentCard] = {}
    for c in asm.components:
        card = _resolve(cards, c.card)
        if card is None:
            err("unknown_component", f"{c.instance}: no card {c.card.component_id!r} in the library", c.instance)
            continue
        if c.card.version is not None and c.card.version != card.version:
            err("card_changed", f"{c.instance}: pinned version {c.card.version}, library has {card.version}", c.instance)
        if c.card.sha256 is not None and c.card.sha256 != card_digest(card):
            err("card_changed", f"{c.instance}: the card {card.component_id!r} differs from the pinned sha256", c.instance)
        card_of[c.instance] = card

    uses: Dict[Tuple[str, str], int] = {}
    for cn in asm.connections:
        for end in (cn.a, cn.b):
            uses[(end.instance, end.port)] = uses.get((end.instance, end.port), 0) + 1
    for (inst, port), n in sorted(uses.items()):
        if n > 1:
            err("port_reused", f"{inst}.{port} is used by {n} connections", f"{inst}.{port}")

    sink_current: Dict[str, float] = {}      # source instance -> sum of the ratings of the sinks on its outputs
    for cn in asm.connections:
        if cn.a.instance == cn.b.instance and cn.a.port == cn.b.port:
            err("self_connection", f"{cn.a} is connected to itself", str(cn.a))
            continue
        ports: List[Optional[Port]] = []
        for end in (cn.a, cn.b):
            card = card_of.get(end.instance)
            if card is None:
                ports.append(None)
                continue
            try:
                ports.append(card.port(end.port))
            except KeyError:
                err("unknown_port", f"{end}: the card {card.component_id!r} has no port {end.port!r}", str(end))
                ports.append(None)
        pa, pb = ports
        if pa is None or pb is None:
            continue
        where = f"{cn.a} <-> {cn.b}"
        if pa.type != pb.type:
            err("type_mismatch", f"{where}: {pa.type} cannot connect to {pb.type}", where)
            continue
        if pa.direction == pb.direction and pa.direction != "bidirectional":
            err("direction", f"{where}: two '{pa.direction}' ports", where)
            continue
        # source = the 'out' end (a bidirectional end defers to the other)
        if pa.direction == "out" or pb.direction == "in":
            (src, src_ref), (snk, snk_ref) = (pa, cn.a), (pb, cn.b)
        else:
            (src, src_ref), (snk, snk_ref) = (pb, cn.b), (pa, cn.a)
        if pa.type == "dc_bus" and not (pa.direction == pb.direction == "bidirectional"):
            lo, hi = max(src.voltage_min_V, snk.voltage_min_V), min(src.voltage_max_V, snk.voltage_max_V)
            if lo > hi + _TOL:
                err("dc_voltage", f"{where}: voltage ranges {src.voltage_min_V:g}-{src.voltage_max_V:g} V and "
                                  f"{snk.voltage_min_V:g}-{snk.voltage_max_V:g} V do not overlap", where)
            elif src.voltage_min_V < snk.voltage_min_V - _TOL or src.voltage_max_V > snk.voltage_max_V + _TOL:
                warn("dc_voltage_range", f"{where}: the source range {src.voltage_min_V:g}-{src.voltage_max_V:g} V "
                                         f"is not inside the sink range {snk.voltage_min_V:g}-{snk.voltage_max_V:g} V", where)
            if snk.current_max_A > src.current_max_A * (1 + _TOL):
                warn("dc_current", f"{where}: the sink is rated {snk.current_max_A:g} A, the source "
                                   f"{src.current_max_A:g} A", where)
            sink_current[src_ref.instance] = sink_current.get(src_ref.instance, 0.0) + snk.current_max_A
        elif pa.type == "phase_ac":
            if src.voltage_max_V > snk.voltage_max_V * (1 + _TOL):
                err("phase_voltage", f"{where}: the drive runs from up to {src.voltage_max_V:g} V, the motor port "
                                     f"is rated {snk.voltage_max_V:g} V", where)
            if src.current_max_A > snk.current_max_A * (1 + _TOL):
                warn("phase_current", f"{where}: the controller can deliver {src.current_max_A:g} A, the motor "
                                      f"port is rated {snk.current_max_A:g} A", where)
        elif pa.type == "shaft":
            limit = snk.rating_kind == "limit"
            if src.max_speed_rpm > snk.max_speed_rpm * (1 + _TOL):
                (err if limit else warn)("shaft_speed", f"{where}: the source can reach {src.max_speed_rpm:g} rpm, the "
                                                        f"sink is rated {snk.max_speed_rpm:g} rpm", where)
            if limit and src.max_torque_Nm > snk.max_torque_Nm * (1 + _TOL):
                err("shaft_torque", f"{where}: the source can give {src.max_torque_Nm:g} N*m, the sink is limited to "
                                    f"{snk.max_torque_Nm:g} N*m", where)
            elif not limit and src.max_torque_Nm < snk.max_torque_Nm * (1 - _TOL):
                warn("shaft_torque", f"{where}: the source gives at most {src.max_torque_Nm:g} N*m, the load draws "
                                     f"{snk.max_torque_Nm:g} N*m at its top speed", where)
            if (src.interface and snk.interface
                    and abs(src.interface.diameter_mm - snk.interface.diameter_mm) > 1e-6):
                warn("shaft_interface", f"{where}: shaft diameters {src.interface.diameter_mm:g} mm and "
                                        f"{snk.interface.diameter_mm:g} mm", where)

    for inst, total in sorted(sink_current.items()):
        card = card_of.get(inst)
        if card is None:
            continue
        # one battery, several outputs: the pack rating is the largest output-port rating of that card
        rating = max((p.current_max_A for p in card.ports if p.type == "dc_bus" and p.direction == "out"), default=None)
        if rating is not None and total > rating * (1 + _TOL):
            warn("dc_current_sum", f"{inst}: the sinks on its outputs are rated {total:g} A together, "
                                   f"the source {rating:g} A", inst)

    for c in asm.components:
        card = card_of.get(c.instance)
        if card is None:
            continue
        for p in card.ports:
            if p.required and (c.instance, p.name) not in uses:
                err("unconnected_port", f"{c.instance}.{p.name} ({p.type}) is required and not connected",
                    f"{c.instance}.{p.name}")
    return rep


# ---------------------------------------------------------------------------
# mass properties (pure math)
# ---------------------------------------------------------------------------

class MassUnknown(ValueError):
    """A component has no mass properties: the total is not 0, it is unknown."""


@dataclass
class AssemblyMass:
    mass_kg: float
    com_m: Tuple[float, float, float]
    inertia_kg_m2: Matrix3                     # about ``com_m``, axes parallel to the assembly frame


def quat_to_matrix(q: Tuple[float, float, float, float]) -> List[List[float]]:
    w, x, y, z = q
    n = math.sqrt(w * w + x * x + y * y + z * z)
    w, x, y, z = w / n, x / n, y / n, z / n
    return [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]


def _mv(r, v):
    return [sum(r[i][j] * v[j] for j in range(3)) for i in range(3)]


def _rot_inertia(r, i):
    """R I R^T."""
    ri = [[sum(r[a][k] * i[k][b] for k in range(3)) for b in range(3)] for a in range(3)]
    return [[sum(ri[a][k] * r[b][k] for k in range(3)) for b in range(3)] for a in range(3)]


def mass_properties(asm: AssemblyIR, cards: Resolver) -> AssemblyMass:
    """Total mass, centre of mass (m, assembly frame) and inertia tensor about it (kg*m^2).

    Each component's centre of mass and inertia are rotated by its placement and moved to the
    placement position; the inertia is then summed about the common centre by the parallel-axis
    theorem: ``I = sum( R I_i R^T + m_i (|d|^2 E - d d^T) )``, ``d`` = component CoM minus total CoM.
    Raises ``MassUnknown`` (listing the instances) if any component has no mass properties."""
    rows = []
    missing = []
    for c in asm.components:
        card = _resolve(cards, c.card)
        if card is None:
            raise KeyError(f"{c.instance}: no card {c.card.component_id!r}")
        mp = card.mass_properties
        if mp is None:
            missing.append(c.instance)
            continue
        r = quat_to_matrix(c.placement.rotation_wxyz)
        p = [x / 1000.0 for x in c.placement.position_mm]
        com = [a + b for a, b in zip(_mv(r, list(mp.com_m)), p)]
        rows.append((mp.mass_kg, com, _rot_inertia(r, mp.inertia_kg_m2)))
    if missing:
        raise MassUnknown("no mass properties for: " + ", ".join(missing))
    total = sum(m for m, _, _ in rows)
    com = [sum(m * c[k] for m, c, _ in rows) / total for k in range(3)]
    inertia = [[0.0] * 3 for _ in range(3)]
    for m, c, i in rows:
        d = [c[k] - com[k] for k in range(3)]
        d2 = sum(x * x for x in d)
        for a in range(3):
            for b in range(3):
                inertia[a][b] += i[a][b] + m * ((d2 if a == b else 0.0) - d[a] * d[b])
    return AssemblyMass(mass_kg=total, com_m=(com[0], com[1], com[2]),
                        inertia_kg_m2=tuple(tuple(r) for r in inertia))      # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# parts and materials of the whole assembly (pure bookkeeping)
# ---------------------------------------------------------------------------

@dataclass
class AssemblyParts:
    """Every part of every instance (``rows``) and the mass per ``material_id`` (``by_material``).  Only parts
    counted in their card's mass enter the totals; ``uncounted`` lists the parts a card names that its mass does
    not contain (holders, rings, bearings of a motor: real, but outside the app's motor mass)."""

    rows: List[Dict[str, object]] = field(default_factory=list)
    by_material: Dict[str, float] = field(default_factory=dict)
    counted_kg: float = 0.0
    uncounted_kg: float = 0.0
    without_parts: List[str] = field(default_factory=list)       # instances whose card lists no parts


def parts_summary(asm: AssemblyIR, cards: Resolver) -> AssemblyParts:
    """Parts and materials of an assembly, from the cards' ``parts`` (the input of a later stress / vibration / thermal
    study).  An instance whose card has no parts is listed under ``without_parts``, never silently dropped."""
    out = AssemblyParts()
    for c in asm.components:
        card = _resolve(cards, c.card)
        if card is None:
            raise KeyError(f"{c.instance}: no card {c.card.component_id!r}")
        if not card.parts:
            out.without_parts.append(c.instance)
            continue
        for p in card.parts:
            out.rows.append(dict(instance=c.instance, part=p.name, material_id=p.material_id, mass_kg=p.mass_kg,
                                 role=p.role, status=p.status, counted=p.counted))
            if p.counted:
                out.counted_kg += p.mass_kg
                out.by_material[p.material_id] = out.by_material.get(p.material_id, 0.0) + p.mass_kg
            else:
                out.uncounted_kg += p.mass_kg
    return out
