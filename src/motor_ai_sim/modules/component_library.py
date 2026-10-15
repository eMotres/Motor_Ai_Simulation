# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Component library - the read-only list of every ``ComponentCard`` the stores can produce.

The cards are built on demand by the adapters (``component_adapters``) from the passport
models, the propeller catalogue, the gearbox plugin and the example parts below, each one
checked by the conformance gate (``assert_component_card``); a card that fails is not
listed, it is reported under ``rejected`` with the reason.  Nothing here writes anything.

Served by ``GET /api/components`` and ``GET /api/components/{id}`` (routes/components.py)
and registered as the module ``component-library`` (capability ``component.library``), so
the System graph shows it with the other modules.

MOTOR MASS.  A motor card carries the FULL assembled mass (owner 2026-10-14): the app's active parts plus the CAD bill of
materials' shaft/carrier, holders, rings, bearings, screws, board, pins and enamel (``modules/motor_mass.py``), with a
material + mass breakdown per part; never a duty-record number.  Without a built CAD BOM the card carries the active mass
and the flag ``mass_partial`` ("partial mass (no CAD model yet)").  Where the die is not in this catalog the card is
listed with the flag ``mass_unknown`` and no mass.

EXAMPLE parts.  The stores hold no mass for a battery pack, a controller board or a frame.
The library therefore lists clearly labelled PLACEHOLDERS (``flags`` = ``placeholder``) so
that an assembly has numbers to add up: ``example_quad_frame_450``, one ``pack_*`` per
passport model and the controller board mass.  Replace them with measured values.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .. import propeller as pp
from .. import propeller_s1 as s1
from ..contracts import CONTRACTS_VERSION
from ..contracts.assembly_ir import CardRef
from ..contracts.component_ir import (ComponentCard, Envelope, Frame, MassProperties, box_inertia, card_digest)
from ..contracts.conformance import assert_component_card
from .base import ModuleManifest
from .component_adapters import (battery_card_from_block, controller_card_from_drive, motor_card,
                                 motor_card_from_s1_model, propeller_card, rigid_body_card, split_parts,
                                 transmission_card)
from .motor_mass import MotorMassError

log = logging.getLogger(__name__)

_REPO_CONFIG = Path(__file__).resolve().parents[3] / "config"

CONTROLLER_BOARD_MASS_KG = 0.015            # PLACEHOLDER (a small FOC board); owner to supply the real mass
EXAMPLE_PACK = dict(n_parallel=2, capacity_ah_per_string=5.0, i_continuous_A=70.0, i_peak_A=100.0, mass_kg=0.85,
                    n_outputs=4)            # PLACEHOLDER: an NMC pack of 21700 high-drain cells, nS x 2P
FRAME_ARM_RADIUS_MM = 225.0                 # 450 mm motor-to-motor diagonal


@dataclass
class Entry:
    card: ComponentCard
    die: Optional[str] = None                                  # catalogue die a motor card belongs to (access control)
    flags: List[str] = field(default_factory=list)            # "placeholder": numbers the stores do not hold
    config: Optional[str] = None                               # configuration of that die (a PRIVATE one is hidden: motor_access.may_see_config)


@dataclass
class ComponentLibrary:
    entries: Dict[str, Entry] = field(default_factory=dict)
    rejected: List[Dict[str, str]] = field(default_factory=list)

    def add(self, card: ComponentCard, *, die: Optional[str] = None, flags: Optional[List[str]] = None,
            config: Optional[str] = None) -> None:
        try:
            assert_component_card(card)
        except AssertionError as exc:
            self.rejected.append({"component_id": card.component_id, "reason": str(exc)})
            return
        if card.component_id in self.entries:
            self.rejected.append({"component_id": card.component_id, "reason": "duplicate component_id"})
            return
        self.entries[card.component_id] = Entry(card, die, list(flags or []), config)

    def get(self, component_id: str) -> ComponentCard:
        return self.entries[component_id].card

    def cards(self) -> Dict[str, ComponentCard]:
        return {k: e.card for k, e in self.entries.items()}


def summary(e: Entry) -> Dict[str, Any]:
    c = e.card
    mp = c.mass_properties
    return {
        "component_id": c.component_id, "kind": c.kind, "name": c.name, "version": c.version, "licence": c.licence,
        "model": c.model.ir_schema, "mass_kg": mp.mass_kg if mp else None,
        "mass_estimated": mp.estimated if mp else None, "flags": list(e.flags), "digest": card_digest(c),
        "parts": [{"name": p.name, "material_id": p.material_id, "mass_kg": p.mass_kg, "role": p.role,
                   "status": p.status, "counted": p.counted} for p in c.parts],
        "ports": [{"name": p.name, "type": p.type, "direction": p.direction, "required": p.required}
                  for p in c.ports],
    }


# ---------------------------------------------------------------------------
# building
# ---------------------------------------------------------------------------

def _passport_models() -> List[Mapping[str, Any]]:
    seen, out = set(), []
    for root in s1.model_dirs():
        if not root.is_dir():
            continue
        for d in sorted(p for p in root.iterdir() if p.is_dir()):
            for f in sorted(d.glob("*.json")):
                key = (d.name, f.stem)
                if key in seen:
                    continue
                seen.add(key)
                try:
                    out.append(s1.load_model(d.name, f.stem))
                except Exception as exc:                        # noqa: BLE001
                    log.warning("passport model %s / %s is unusable: %s", d.name, f.stem, exc)
    return out


def example_frame() -> ComponentCard:
    """A 450 mm quadcopter frame: a PLACEHOLDER rigid body (a flat plate), not a real part."""
    a = FRAME_ARM_RADIUS_MM / 2 ** 0.5
    mass = 0.25
    mp = MassProperties(mass_kg=mass, inertia_kg_m2=box_inertia(mass, 0.45, 0.45, 0.02), estimated=True,
                        basis="PLACEHOLDER: 0.25 kg homogeneous plate 450 x 450 x 20 mm, centre of mass at the "
                              "origin; replace with the real frame's mass properties")
    frames, mounts = [], []
    for k, (sx, sy) in enumerate([(1, 1), (-1, 1), (-1, -1), (1, -1)], start=1):
        frames.append(Frame(name=f"arm_{k}", position_mm=(sx * a, sy * a, 0.0), note="motor mount at the arm tip"))
        frames.append(Frame(name=f"esc_{k}", position_mm=(sx * a / 2, sy * a / 2, 0.0), note="controller mount on the arm"))
        mounts += [f"arm_{k}", f"esc_{k}"]
    frames.append(Frame(name="battery", position_mm=(0.0, 0.0, -10.0), note="battery mount under the plate"))
    mounts.append("battery")
    parts = split_parts(mass, [("Carbon-fibre plates and arms", "CFRP_QI_T800_epoxy", "structural", 0.80),
                               ("Aluminium standoffs and motor mounts", "Aluminium_6061", "structural", 0.16),
                               ("Screws", "Stainless_A2_70", "fastener", 0.04)],
                        status="placeholder",
                        basis="PLACEHOLDER split of the example frame: carbon/epoxy plates, aluminium standoffs, "
                              "stainless screws; not a real part")
    return rigid_body_card(
        component_id="example_quad_frame_450", kind="frame", name="Example quadcopter frame 450 (placeholder)",
        mass=mp, envelope=Envelope(shape="box", size_mm=(450.0, 450.0, 20.0),
                                   basis="PLACEHOLDER plate of the diagonal"),
        frames=frames, mount_ports=mounts, placeholder=True, parts=parts,
        basis_note="PLACEHOLDER example frame: not a real part",
        description="PLACEHOLDER rigid body for the quadcopter example: four arm mounts, four controller mounts "
                    "and a battery mount. Mass and inertia are example values.")


def build_library() -> ComponentLibrary:
    lib = ComponentLibrary()
    for m in _passport_models():
        die, cfg = str(m.get("die")), str(m.get("config"))
        try:
            try:
                card = motor_card(m)
                partial = "PARTIAL MASS" in card.mass_properties.basis       # no CAD bill of materials built yet
                lib.add(card, die=die, flags=["mass_partial"] if partial else [], config=cfg)
            except MotorMassError as exc:
                # the die is not in this catalog: the mass is UNKNOWN (never a placeholder); the card is still listed
                log.warning("motor %s %s listed without a mass: %s", die, cfg, exc)
                lib.add(motor_card_from_s1_model(m), die=die, flags=["mass_unknown"], config=cfg)
        except Exception as exc:                                # noqa: BLE001
            lib.rejected.append({"component_id": f"motor {die} {cfg}", "reason": str(exc)})
        try:
            lib.add(controller_card_from_drive(
                m, mass_kg=CONTROLLER_BOARD_MASS_KG,
                mass_basis="PLACEHOLDER board mass (not measured)"), die=die, flags=["placeholder"], config=cfg)
        except Exception as exc:                                # noqa: BLE001
            lib.rejected.append({"component_id": f"controller {die} {cfg}", "reason": str(exc)})
        if m.get("battery"):
            try:
                from .component_adapters import slug
                lib.add(battery_card_from_block(
                    m["battery"], component_id=slug("pack", die, cfg, "example"),
                    name=f"Example pack for {die} {cfg} (placeholder)",
                    mass_basis="PLACEHOLDER pack mass (not measured)",
                    placeholder_note="PLACEHOLDER: strings in parallel, capacity, discharge current and mass are "
                                     "example values for a 21700 high-drain NMC pack, not a real product.",
                    **EXAMPLE_PACK), die=die, flags=["placeholder"], config=cfg)
            except Exception as exc:                            # noqa: BLE001
                lib.rejected.append({"component_id": f"battery {die} {cfg}", "reason": str(exc)})
    try:
        for prop in pp.list_propellers():
            if prop.selectable:
                try:
                    lib.add(propeller_card(prop))
                except Exception as exc:                        # noqa: BLE001
                    lib.rejected.append({"component_id": prop.id, "reason": str(exc)})
    except Exception as exc:                                    # noqa: BLE001
        lib.rejected.append({"component_id": "propeller catalogue", "reason": str(exc)})
    try:
        from .component_plugins.gearbox_single_stage import GearboxSingleStage
        lib.add(transmission_card(GearboxSingleStage().run({}), status_note="REFERENCE module (generic example values)."),
                flags=["placeholder"])
    except Exception as exc:                                    # noqa: BLE001
        lib.rejected.append({"component_id": "gearbox_single_stage", "reason": str(exc)})
    lib.add(example_frame(), flags=["placeholder"])
    return lib


_LOCK = threading.Lock()
_LIB: Optional[ComponentLibrary] = None


def get_library(refresh: bool = False) -> ComponentLibrary:
    global _LIB
    with _LOCK:
        if _LIB is None or refresh:
            _LIB = build_library()
        return _LIB


def resolve(ref: CardRef) -> ComponentCard:
    """Resolver for ``validate_assembly`` / ``mass_properties``."""
    return get_library().get(ref.component_id)


# ---------------------------------------------------------------------------
# the module (registry / System graph)
# ---------------------------------------------------------------------------

class ComponentLibraryModule:
    NAME, CAPABILITY, VERSION = "component-library", "component.library", "0.1.0"

    def manifest(self) -> ModuleManifest:
        return ModuleManifest(
            name=self.NAME, version=self.VERSION, capability=self.CAPABILITY, kind="compute",
            contracts_version=CONTRACTS_VERSION, depends_on=[],
            inputs=[], outputs=["ComponentCard"],
            summary="Component library: motor, propeller, transmission, controller, battery and frame cards "
                    "from the existing stores, one card schema (performance only for motors)")

    def run(self, payload: Optional[Dict[str, Any]] = None) -> Any:
        """payload: ``{"id": ...}`` -> that ``ComponentCard``; ``{"kind": ...}`` or nothing -> the list of summaries."""
        p = payload or {}
        lib = get_library(bool(p.get("refresh")))
        # Same visibility rule as GET /api/components (a machine's card follows
        # the machine's die and configuration access) - this capability is also
        # reachable through POST /api/kernel/run.  No caller (a script): all.
        from motor_ai_sim import motor_access as _ma
        acc = _ma.catalog_access(None) if _ma.request_is_bound() else None

        def _vis(e: Entry) -> bool:
            return acc is None or e.die is None or _ma.may_see_config(acc, e.die, e.config)

        if p.get("id"):
            e = lib.entries[str(p["id"])]          # KeyError = unknown, as before
            if not _vis(e):
                raise KeyError(str(p["id"]))
            return e.card
        kind = p.get("kind")
        return [summary(e) for e in lib.entries.values()
                if (not kind or e.card.kind == kind) and _vis(e)]
