# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Adapters: today's stores -> ``ComponentCard`` (docs/COMPONENT_CARD.md).

Read-only.  No physics changes; every number is read from the passport model, the propeller
catalogue, the gearbox plugin, the battery block or the controller drive and the card's
``basis`` says so.  Anything the stores do not hold is either left empty (mass unknown is
``None``, never 0) or passed in by the caller and labelled as such.

  * ``motor_card_from_s1_model`` - passport propeller-S1 model -> motor card.  PERFORMANCE
    ONLY: of the model's ``thermal_geom`` only the two outer dimensions (stator OD, stack
    length) are read, to size the outer envelope; nothing else of it is copied.
  * ``propeller_card`` - a catalogue propeller -> propeller card (``PropulsorIR``).
  * ``transmission_card`` - a ``TransmissionIR`` (e.g. the gearbox plugin) -> card.
  * ``battery_card_from_block`` - a family ``battery:`` block via ``simulation.battery`` -> card.
  * ``controller_card_from_drive`` - a passport drive variant + its power-device card -> card.
  * ``rigid_body_card`` - a frame / link / wheel by mass properties (used for placeholders).
"""
from __future__ import annotations

import re
from typing import Any, List, Mapping, Optional, Tuple

from .. import propeller as pp
from ..contracts import ModelSource, Provenance
from ..contracts.adapters import stamp
from ..contracts.component_ir import (BatteryIR, ComponentCard, ControllerIR, ControllerLossModel, Envelope,
                                      Frame, MassProperties, Port, RigidBodyIR, box_inertia, cylinder_inertia)
from ..contracts.drivetrain_ir import TransmissionIR
from .drivetrain.adapters import motor_ir_from_s1_model, propulsor_from_propeller

LICENCE_REPO = "Apache-2.0"
LICENCE_VENDOR_DATA = "LicenseRef-vendor-published-data"


def slug(*parts: Any) -> str:
    s = "_".join(str(p) for p in parts if p not in (None, ""))
    s = re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
    return (s if s and s[0].isalnum() else "c_" + s)[:95]


def _prov(name: str) -> Provenance:
    return stamp(name)


def _need(m: Mapping[str, Any], *path: str) -> Any:
    cur: Any = m
    for k in path:
        if not isinstance(cur, Mapping) or cur.get(k) is None:
            raise ValueError("the model carries no " + ".".join(path))
        cur = cur[k]
    return cur


def estimate_block_mass_properties(mass_kg: float, density_kg_m3: float, what: str,
                                   basis: str) -> Tuple[MassProperties, Envelope]:
    """A homogeneous cube of the given density: centre of mass at the body origin, the inertia of
    that cube, and the cube as an envelope.  Used for parts whose shape the stores do not hold;
    labelled ``estimated`` so nobody mistakes it for CAD data."""
    side = (mass_kg / density_kg_m3) ** (1.0 / 3.0)
    mp = MassProperties(mass_kg=mass_kg, inertia_kg_m2=box_inertia(mass_kg, side, side, side), estimated=True,
                        basis=f"{basis}; inertia ESTIMATED as a homogeneous cube of {density_kg_m3:g} kg/m3 ({what})")
    env = Envelope(shape="box", size_mm=(side * 1e3,) * 3,
                   basis=f"edge of a homogeneous cube of {density_kg_m3:g} kg/m3 (estimate, {what})")
    return mp, env


# ---------------------------------------------------------------------------
# motor
# ---------------------------------------------------------------------------

def motor_card_from_s1_model(m: Mapping[str, Any], *, mass_kg: Optional[float] = None, mass_basis: str = "",
                             component_id: Optional[str] = None, name: str = "") -> ComponentCard:
    """Performance + outer envelope + mass.  ``m`` is a validated S1 model (``propeller_s1.load_model``)."""
    die, cfg = str(m.get("die") or "motor"), str(m.get("config") or "")
    ir = motor_ir_from_s1_model(m, mass_kg=mass_kg)
    lim = ir.limits
    if lim.dc_voltage_max_V is None or lim.current_max_A is None:
        raise ValueError("the model carries no battery voltage / current cap: the phase port cannot be rated")
    # the ONLY two numbers read from the model's geometry block: the outer dimensions
    tg = m.get("thermal_geom") or {}
    d_mm, l_mm = tg.get("statorOD_mm"), tg.get("stackLength_mm")
    envelope = None
    frames: List[Frame] = []
    mass_props = None
    if d_mm and l_mm:
        envelope = Envelope(shape="cylinder", diameter_mm=float(d_mm), length_mm=float(l_mm), axis="z",
                            basis="outer dimensions of the active part (stator OD x stack length) read from the "
                                  "passport model; housing, shaft stub and end windings are NOT included")
        frames = [Frame(name="mount", position_mm=(0.0, 0.0, -float(l_mm) / 2.0),
                        note="mounting face, at the back of the envelope (derived from the envelope)"),
                  Frame(name="shaft", position_mm=(0.0, 0.0, float(l_mm) / 2.0),
                        note="shaft axis on the front of the envelope (derived from the envelope)")]
        if mass_kg:
            mass_props = MassProperties(
                mass_kg=mass_kg, inertia_kg_m2=cylinder_inertia(mass_kg, float(d_mm) / 1e3, float(l_mm) / 1e3),
                estimated=True,
                basis=(mass_basis or "mass given by the caller") + "; centre of mass at the envelope centre and "
                      "inertia ESTIMATED as a homogeneous cylinder of the envelope (no CAD mass properties)")
    ports = [
        Port(name="shaft", type="shaft", direction="out", frame="shaft" if frames else None, required=True,
             max_torque_Nm=max(ir.torque_peak_Nm), max_speed_rpm=lim.speed_max_rpm, rating_kind="limit",
             note="largest torque of the passport envelope; speed cap of the passport"),
        Port(name="phase", type="phase_ac", direction="in", required=True, voltage_max_V=lim.dc_voltage_max_V,
             current_max_A=lim.current_max_A,
             note="voltage = largest DC link the passport was characterised for; current = passport current cap"),
        Port(name="mount", type="mount", direction="bidirectional", frame="mount" if frames else None)]
    return ComponentCard(
        component_id=component_id or slug("motor", die, cfg), kind="motor", name=name or f"{die} {cfg}".strip(),
        licence=LICENCE_REPO,
        description="Motor by its passport performance (torque-speed envelope, efficiency, limits). "
                    "No stator, rotor or winding data.",
        provenance=_prov("adapter.motor_card_from_s1_model"),
        envelope=envelope, frames=frames, mass_properties=mass_props, ports=ports, model=ir)


# ---------------------------------------------------------------------------
# propeller
# ---------------------------------------------------------------------------

def propeller_card(prop: pp.Propeller, *, component_id: Optional[str] = None) -> ComponentCard:
    ir = propulsor_from_propeller(prop)
    rpm_hi = ir.speed_rpm[-1]
    lp = ir.evaluate(rpm_hi)
    d_mm = prop.diameter_m * 1000.0
    thick = max(d_mm * 0.02, 1.0)
    envelope = Envelope(shape="cylinder", diameter_mm=d_mm, length_mm=thick, axis="z",
                        basis="catalogue diameter; thickness ESTIMATED as 2 % of the diameter (hub + blade)")
    mp = None
    if ir.mass_kg:
        m, D = ir.mass_kg, prop.diameter_m
        w, t = D / 10.0, D / 50.0
        mp = MassProperties(
            mass_kg=m, estimated=True,
            inertia_kg_m2=((m * (w * w + t * t) / 12.0, 0, 0), (0, m * (D * D + t * t) / 12.0, 0),
                           (0, 0, m * (D * D + w * w) / 12.0)),
            basis="mass from the catalogue entry (mass_g); inertia ESTIMATED as a slender blade box "
                  "D x D/10 x D/50 (no measured moment of inertia)")
    ports = [Port(name="shaft", type="shaft", direction="in", frame="hub", required=True, rating_kind="demand",
                  max_speed_rpm=rpm_hi, max_torque_Nm=max(lp.torque_Nm, 1e-6),
                  note="demand at the top of the tested rpm range, static, sea-level air")]
    return ComponentCard(
        component_id=component_id or slug(prop.id), kind="propeller",
        name=f"{prop.vendor} {prop.model}".strip(), licence=LICENCE_VENDOR_DATA,
        description=f"Propeller {prop.id}: C_T and C_P from the catalogue fit; the data are the vendor's published "
                    f"bench data (data quality: {prop.data_quality}).",
        provenance=_prov("adapter.propeller_card"), envelope=envelope,
        frames=[Frame(name="hub", note="hub centre, rotation axis z")], mass_properties=mp, ports=ports, model=ir)


# ---------------------------------------------------------------------------
# transmission
# ---------------------------------------------------------------------------

def transmission_card(ir: TransmissionIR, *, component_id: Optional[str] = None, licence: str = LICENCE_REPO,
                      status_note: str = "") -> ComponentCard:
    lim = ir.limits
    t_out = lim.torque_out_max_Nm or lim.torque_in_max_Nm * ir.ratio
    n_out = lim.speed_out_max_rpm or lim.speed_in_max_rpm / ir.ratio
    mp, env = estimate_block_mass_properties(ir.mass_kg, 2700.0, "transmission housing",
                                             "mass_kg of the transmission model")
    ports = [Port(name="shaft_in", type="shaft", direction="in", frame="input", required=True, rating_kind="limit",
                  max_torque_Nm=lim.torque_in_max_Nm, max_speed_rpm=lim.speed_in_max_rpm, interface=ir.input_shaft),
             Port(name="shaft_out", type="shaft", direction="out", frame="output", required=True, rating_kind="limit",
                  max_torque_Nm=t_out, max_speed_rpm=n_out, interface=ir.output_shaft),
             Port(name="mount", type="mount", direction="bidirectional", frame="housing")]
    half = env.size_mm[0] / 2.0
    return ComponentCard(
        component_id=component_id or slug(ir.id), kind="transmission", name=ir.name or ir.id, licence=licence,
        description=("Transmission by its loss maps, ratio, inertia and limits. " + status_note).strip(),
        provenance=_prov("adapter.transmission_card"), envelope=env, mass_properties=mp,
        frames=[Frame(name="input", position_mm=(0, 0, -half)), Frame(name="output", position_mm=(0, 0, half)),
                Frame(name="housing", position_mm=(0, 0, -half))],
        ports=ports, model=ir)


# ---------------------------------------------------------------------------
# battery
# ---------------------------------------------------------------------------

def battery_card_from_block(batt: Mapping[str, Any], *, component_id: str, name: str = "",
                            n_parallel: Optional[int] = None, capacity_ah_per_string: Optional[float] = None,
                            i_continuous_A: Optional[float] = None, i_peak_A: Optional[float] = None,
                            mass_kg: Optional[float] = None, mass_basis: str = "", n_outputs: int = 1,
                            placeholder_note: str = "") -> ComponentCard:
    """A pack from a family ``battery:`` block (chemistry, cells, v_min / v_nom / v_max) through
    ``simulation.battery.pack_from_config``.  What the block lacks (strings in parallel, capacity,
    discharge current, mass) is taken from the keyword arguments and labelled in ``basis``;
    with nothing given the battery.py placeholders apply and ``basis`` says so."""
    from ..simulation.battery import pack_from_config
    block = dict(batt)
    if n_parallel is not None:
        block["n_parallel"] = n_parallel
    if capacity_ah_per_string is not None:
        block["capacity_ah"] = capacity_ah_per_string
    pack = pack_from_config(block)
    if pack is None:
        raise ValueError("the battery block carries no usable voltage")
    v_min, v_nom, v_max = (float(block.get(k) or pack.v_oc) for k in ("v_min", "v_nom", "v_max"))
    cap = pack.capacity_pack_ah
    i_cont = i_continuous_A if i_continuous_A is not None else cap          # 1 C placeholder
    src = dict(pack.sources)
    basis = {
        "r_internal_ohm": "simulation/battery.py: R_pack = NS * r_int_cell / NP, constant over the state of charge "
                          "(the model has none); " + src.get("r_int_mohm", "r_int given in the block"),
        "capacity_Ah": ("given by the caller (per string x strings)" if capacity_ah_per_string is not None
                        else src.get("capacity_ah", "from the block")),
        "i_continuous_max_A": ("given by the caller" if i_continuous_A is not None
                               else "PLACEHOLDER: 1 C of the capacity (no discharge rating in the store)"),
    }
    if placeholder_note:
        basis["note"] = placeholder_note
    ir = BatteryIR(
        id=component_id, name=name or component_id, chemistry=pack.chemistry, cells_series=pack.cells,
        cells_parallel=pack.n_parallel, v_nominal_V=v_nom, v_min_V=v_min, v_max_V=v_max, capacity_Ah=cap,
        soc=[0.0, 1.0], r_internal_ohm=[pack.r_pack_ohm, pack.r_pack_ohm], i_continuous_max_A=i_cont,
        i_peak_max_A=i_peak_A, mass_kg=mass_kg,
        source=ModelSource(kind="analytical", reference="simulation/battery.py BatteryPack (pack_from_config)",
                           validated_against=None),
        basis=basis, provenance=_prov("adapter.battery_card_from_block"))
    mp = env = None
    if mass_kg:
        mp, env = estimate_block_mass_properties(mass_kg, 2000.0, "battery pack",
                                                 mass_basis or "mass given by the caller")
    ports = [Port(name=f"dc_out_{k}", type="dc_bus", direction="out", required=True, voltage_min_V=v_min,
                  voltage_max_V=v_max, current_max_A=i_cont, current_peak_A=i_peak_A,
                  note="the rating is for the whole pack; the outputs share it") for k in range(1, n_outputs + 1)]
    ports.append(Port(name="mount", type="mount", direction="bidirectional", frame="body"))
    return ComponentCard(
        component_id=component_id, kind="battery", name=name or component_id, licence=LICENCE_REPO,
        description="Battery pack as an EMF behind a resistance (no state-of-charge model in the store)."
                    + (" " + placeholder_note if placeholder_note else ""),
        provenance=_prov("adapter.battery_card_from_block"), envelope=env, mass_properties=mp,
        frames=[Frame(name="body")], ports=ports, model=ir)


# ---------------------------------------------------------------------------
# controller
# ---------------------------------------------------------------------------

def controller_card_from_drive(m: Mapping[str, Any], drive_id: Optional[str] = None, *,
                               component_id: Optional[str] = None, mass_kg: Optional[float] = None,
                               mass_basis: str = "") -> ComponentCard:
    """A controller from a passport drive variant (``m["drives"][drive_id]``): bus range, board
    continuous current per cooling class, the power device (loss model reference) and its
    junction-temperature limit from the device card."""
    drives = m.get("drives") or {}
    did = drive_id or m.get("default_drive")
    if did not in drives:
        raise ValueError(f"the model has no drive variant {did!r}")
    d = drives[did]
    bus = _need(d, "bus_v")
    lim = {str(k): float(v) for k, v in (d.get("i_board_limit_A") or {}).items()}
    if not lim:
        raise ValueError("the drive variant carries no board current limit")
    i_cont = min(lim.values())
    tj = None
    try:
        from ..inverter.devices import get_device
        tj = float(get_device(d["device"]).t_j_max_c)
    except Exception:                                                  # noqa: BLE001 - the card is useful without it
        tj = None
    die, cfg = str(m.get("die") or ""), str(m.get("config") or "")
    cid = component_id or slug("ctrl", die, cfg, did)
    ir = ControllerIR(
        id=cid, name=f"Controller {did} ({die} {cfg})".strip(), bus_voltage_min_V=float(bus["min"]),
        bus_voltage_max_V=float(bus["max"]), phase_current_continuous_A=i_cont, phase_current_by_cooling_A=lim,
        loss_model=ControllerLossModel(kind="device_card", device_part=str(d["device"]),
                                       n_parallel=int(d.get("n_parallel") or 1), carrier_hz=d.get("carrier_hz"),
                                       note="inverter/losses.py with this part; the passport drive variant "
                                            f"{did!r} holds the computed loss grid"),
        t_junction_max_C=tj, mass_kg=mass_kg,
        source=ModelSource(kind="analytical", reference=f"passport drive variant {did} of {die} {cfg}",
                           validated_against=None),
        basis={"phase_current_continuous_A": "passport board model: lowest of the cooling classes "
                                             + ", ".join(sorted(lim)),
               "bus_voltage": "passport drive variant bus_v (battery pack range)"},
        provenance=_prov("adapter.controller_card_from_drive"))
    mp = env = None
    if mass_kg:
        mp, env = estimate_block_mass_properties(mass_kg, 2500.0, "controller board",
                                                 mass_basis or "mass given by the caller")
    ports = [Port(name="dc_in", type="dc_bus", direction="in", required=True, voltage_min_V=ir.bus_voltage_min_V,
                  voltage_max_V=ir.bus_voltage_max_V, current_max_A=i_cont,
                  note="bus current bounded by the phase rms current for modulation <= 0.94 "
                       "(I_dc = 1.06 m cos(phi) I_rms)"),
             Port(name="phase", type="phase_ac", direction="out", required=True, voltage_max_V=ir.bus_voltage_max_V,
                  current_max_A=i_cont),
             Port(name="command", type="signal", direction="in", protocol="torque or current command"),
             Port(name="mount", type="mount", direction="bidirectional", frame="body")]
    return ComponentCard(
        component_id=cid, kind="controller", name=ir.name, licence=LICENCE_REPO,
        description="Motor controller by its ratings and loss-model reference.",
        provenance=_prov("adapter.controller_card_from_drive"), envelope=env, mass_properties=mp,
        frames=[Frame(name="body")], ports=ports, model=ir)


# ---------------------------------------------------------------------------
# frame / link / wheel
# ---------------------------------------------------------------------------

def rigid_body_card(*, component_id: str, kind: str, name: str, mass: MassProperties, envelope: Optional[Envelope],
                    frames: List[Frame], mount_ports: List[str], placeholder: bool, basis_note: str,
                    description: str = "") -> ComponentCard:
    ir = RigidBodyIR(id=component_id, name=name, placeholder=placeholder,
                     source=ModelSource(kind="analytical", reference=basis_note),
                     basis={"mass_properties": basis_note}, provenance=_prov("adapter.rigid_body_card"))
    return ComponentCard(
        component_id=component_id, kind=kind, name=name, licence=LICENCE_REPO, description=description,
        provenance=_prov("adapter.rigid_body_card"), envelope=envelope, frames=frames, mass_properties=mass,
        ports=[Port(name=n, type="mount", direction="bidirectional", frame=n) for n in mount_ports], model=ir)
