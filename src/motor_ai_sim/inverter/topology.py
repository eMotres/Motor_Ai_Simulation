"""COIL -> BRIDGE — which switch drives which coil, and nothing implied.

Owner, 2026-09-22: *«чтобы была возможность комбинировать мосты так, как нам
надо: один контроллер на один мотор, два контроллера на один мотор и т.д., один
мост на каждую катушку отдельно»*.  So the controller is not "a three-phase
inverter"; it is a MAP from the motor's coils onto bridges, and the presets are
named maps over that one structure:

  ``one_3ph``    one three-phase two-level inverter.  The coils are grouped
                 into the three phases the winding builder gives them, star or
                 delta as the DUTY says, 6 switches (3 legs x 2).
  ``two_3ph``    two three-phase inverters on the same shaft: coil set 1-3-5
                 and coil set 2-4-6, each with its own 6 switches and its own
                 star point.  For a 6-coil/12-slot machine the two sets sit one
                 coil apart, which on L155 (12 slots, 10 poles) is 150 degrees
                 electrical — reported, never assumed to be 30 or 60.
  ``h_bridge``   one full H-bridge per coil: N_coils bridges, 4 switches each,
                 every coil independent and across the FULL DC link.  This is
                 the Stage-3 six-coil study's topology, available now.
  ``custom``     an explicit list ``[{coil, bridge, leg, polarity}]``.
                 Validated to death: every coil assigned exactly once, every
                 bridge's legs consistent with its kind.

WHAT A BRIDGE OWNS
    * a KIND — ``"three_phase_2l"`` or ``"h_bridge"``;
    * its LEGS, and for each leg the coils it feeds and in which polarity;
    * the connection of its coil group (``star`` / ``delta`` / ``independent``);
    * its own DC link and its own device/parallel count (defaulted from the
      controller-wide choice, overridable per bridge).

WHAT A LEG CARRIES — and this is the number the whole loss model turns on:
    three-phase, STAR   the phase current;
    three-phase, DELTA  the LINE current, sqrt(3) x the phase current and
                        30 degrees shifted — the bridge is outside the delta,
                        and a delta machine quoted "325 A per phase" puts
                        562 A through each switch (this is exactly the trap the
                        duty records carry: ``point.I_phase_rms`` on a delta
                        duty is the LINE value);
    H-bridge            the coil current itself, through both legs.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "TopologyError", "Coil", "Leg", "Bridge", "Topology",
    "TOPOLOGY_PRESETS", "coils_from_winding", "build_topology",
]


class TopologyError(ValueError):
    """A mapping that does not describe a machine anyone can wire."""


TOPOLOGY_PRESETS: Dict[str, Dict[str, Any]] = {
    "one_3ph": {
        "label": "One 3-phase inverter",
        "hint": "6 switches; the coils grouped into the winding's three phases, "
                "star or delta as the duty says",
        "bridge_kind": "three_phase_2l",
    },
    "two_3ph": {
        "label": "Two 3-phase inverters",
        "hint": "2 x 6 switches; odd coils on inverter 1, even coils on "
                "inverter 2, each with its own star point",
        "bridge_kind": "three_phase_2l",
    },
    "h_bridge": {
        "label": "H-bridge per coil",
        "hint": "4 switches per coil; every coil independent across the full "
                "DC link, unipolar PWM",
        "bridge_kind": "h_bridge",
    },
    "custom": {
        "label": "Custom mapping",
        "hint": "an explicit coil -> bridge/leg list",
        "bridge_kind": None,
    },
}

#: Unipolar is the default for an H-bridge and it is a CHOICE, not a law: it
#: doubles the apparent ripple frequency at the coil and halves the volt-second
#: step, at the cost of switching both legs.  Bipolar is offered and reported.
H_BRIDGE_MODULATIONS = ("unipolar", "bipolar")


# ---------------------------------------------------------------------------
# The coils
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Coil:
    """One coil of the machine, as the winding builder names it."""
    index: int                 # 1-based, the order the builder produced
    phase: str                 # 'A' | 'B' | 'C'
    polarity: int              # +1 / -1, the builder's sign on the go-side slot
    slot_go: int               # 1-based slot of the going side
    slot_return: int           # 1-based slot of the returning side
    tooth: Optional[int]       # tooth the coil is wound on (concentrated only)
    angle_elec_deg: float      # electrical position of the go-side slot

    def as_dict(self) -> Dict[str, Any]:
        return {"index": self.index, "phase": self.phase,
                "polarity": self.polarity, "slot_go": self.slot_go,
                "slot_return": self.slot_return, "tooth": self.tooth,
                "angle_elec_deg": round(self.angle_elec_deg, 2),
                "label": self.label}

    @property
    def label(self) -> str:
        t = "" if self.tooth is None else f" · tooth {self.tooth}"
        return (f"coil {self.index} · {self.phase}{'+' if self.polarity > 0 else '-'}"
                f"{t} · slots {self.slot_go}/{self.slot_return}")


def coils_from_winding(num_slots: int, num_poles: int, *,
                       single_layer: bool = True,
                       layout_str: Optional[str] = None) -> List[Coil]:
    """The machine's coils, straight out of ``build_winding_layout``.

    SINGLE LAYER is one coil per TWO slots: the builder puts a coil's two sides
    in adjacent slots as ``(phase, +s)``, ``(phase, -s)``, so coil *j* spans
    slots ``2j`` and ``2j+1`` and sits on the tooth between them — L155's six
    coils on twelve slots.  DOUBLE LAYER gives one phasor per slot, so a coil
    per slot, and the return side is taken as the slot one coil-pitch away;
    that is the conventional reading and it is stated because a different
    winding tool may span differently.
    """
    from motor_ai_sim.simulation.geometry_2d import build_winding_layout
    slots, poles = int(num_slots), int(num_poles)
    if slots <= 0 or poles <= 0 or poles % 2:
        raise TopologyError(
            f"a winding needs a positive slot count and an even pole count; "
            f"got {num_slots} slots / {num_poles} poles")
    layout = build_winding_layout(slots, poles // 2,
                                  single_layer=bool(single_layer),
                                  layout_str=layout_str)
    alpha_e = (poles // 2) * 360.0 / slots
    coils: List[Coil] = []
    if single_layer:
        for j in range(slots // 2):
            ph, sgn = layout[2 * j]
            coils.append(Coil(index=j + 1, phase=str(ph), polarity=int(sgn),
                              slot_go=2 * j + 1, slot_return=2 * j + 2,
                              tooth=j * 2 + 1,
                              angle_elec_deg=(2 * j) * alpha_e % 360.0))
    else:
        pitch = max(1, round(slots / poles))
        for k in range(slots):
            ph, sgn = layout[k]
            coils.append(Coil(index=k + 1, phase=str(ph), polarity=int(sgn),
                              slot_go=k + 1,
                              slot_return=((k + pitch) % slots) + 1,
                              tooth=None,
                              angle_elec_deg=(k * alpha_e) % 360.0))
    if not coils:
        raise TopologyError("the winding builder produced no coils")
    return coils


# ---------------------------------------------------------------------------
# The bridge
# ---------------------------------------------------------------------------

@dataclass
class Leg:
    """One half-bridge: a high-side switch, a low-side switch, one terminal."""
    name: str                          # 'A' | 'B' | 'C' | 'P' | 'N'
    coils: List[int] = field(default_factory=list)   # coil indices it feeds
    polarity: int = 1

    def as_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "coils": list(self.coils),
                "polarity": self.polarity, "switches": 2}


@dataclass
class Bridge:
    """One inverter / one H-bridge — legs, connection, devices, its own bus."""
    id: str
    kind: str                           # 'three_phase_2l' | 'h_bridge'
    legs: List[Leg]
    connection: str                     # 'star' | 'delta' | 'independent'
    coils: List[int]
    label: str = ""
    modulation: str = "sine_triangle"   # or 'unipolar' / 'bipolar' for H-bridges
    devices_parallel: int = 1
    device: Optional[str] = None
    v_dc_V: Optional[float] = None
    phase_shift_deg: float = 0.0        # of this bridge's fundamental, vs bridge 1

    @property
    def n_switches(self) -> int:
        return 2 * len(self.legs)

    @property
    def n_devices(self) -> int:
        return self.n_switches * max(int(self.devices_parallel), 1)

    def as_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "label": self.label,
                "connection": self.connection, "coils": list(self.coils),
                "legs": [l.as_dict() for l in self.legs],
                "modulation": self.modulation,
                "devices_parallel": int(self.devices_parallel),
                "device": self.device, "v_dc_V": self.v_dc_V,
                "phase_shift_deg": round(self.phase_shift_deg, 2),
                "n_switches": self.n_switches, "n_devices": self.n_devices}


@dataclass
class Topology:
    """The whole controller: the coils, the bridges, and how they were built."""
    preset: str
    coils: List[Coil]
    bridges: List[Bridge]
    notes: List[str] = field(default_factory=list)
    star_delta: str = "star"

    @property
    def n_switches(self) -> int:
        return sum(b.n_switches for b in self.bridges)

    @property
    def n_devices(self) -> int:
        return sum(b.n_devices for b in self.bridges)

    def as_dict(self) -> Dict[str, Any]:
        return {"preset": self.preset,
                "preset_label": (TOPOLOGY_PRESETS.get(self.preset) or {}).get(
                    "label", self.preset),
                "star_delta": self.star_delta,
                "coils": [c.as_dict() for c in self.coils],
                "bridges": [b.as_dict() for b in self.bridges],
                "n_bridges": len(self.bridges),
                "n_switches": self.n_switches,
                "n_devices": self.n_devices,
                "mapping": [{"coil": c, "bridge": b.id, "leg": l.name,
                             "polarity": l.polarity}
                            for b in self.bridges for l in b.legs
                            for c in l.coils],
                "notes": list(self.notes)}


# ---------------------------------------------------------------------------
# Building one
# ---------------------------------------------------------------------------

def _phase_groups(coils: Sequence[Coil]) -> Dict[str, List[Coil]]:
    groups: Dict[str, List[Coil]] = {}
    for c in coils:
        groups.setdefault(c.phase, []).append(c)
    return groups


def _three_phase_bridge(bid: str, label: str, group: Sequence[Coil],
                        connection: str, **kw: Any) -> Bridge:
    by_phase = _phase_groups(group)
    names = sorted(by_phase)
    if len(names) != 3:
        raise TopologyError(
            f"{label}: a three-phase bridge needs coils of all three phases; "
            f"this group has {', '.join(names) or 'none'}. Use the H-bridge "
            "preset or a custom mapping for a set that is not three-phase.")
    legs = [Leg(name=ph, coils=[c.index for c in by_phase[ph]],
                polarity=by_phase[ph][0].polarity) for ph in names]
    return Bridge(id=bid, kind="three_phase_2l", legs=legs,
                  connection=connection,
                  coils=sorted(c.index for c in group), label=label, **kw)


def _mean_angle_deg(coils: Sequence[Coil]) -> float:
    """Circular mean of the electrical angles — a set's electrical position."""
    if not coils:
        return 0.0
    s = sum(math.sin(math.radians(c.angle_elec_deg)) for c in coils)
    cc = sum(math.cos(math.radians(c.angle_elec_deg)) for c in coils)
    return math.degrees(math.atan2(s, cc)) % 360.0


def build_topology(*, preset: str, coils: Sequence[Coil],
                   star_delta: str = "star",
                   device: Optional[str] = None,
                   devices_parallel: int = 1,
                   v_dc_V: Optional[float] = None,
                   h_bridge_modulation: str = "unipolar",
                   mapping: Optional[Sequence[Dict[str, Any]]] = None,
                   devices_parallel_by_bridge: Optional[Dict[str, Any]] = None
                   ) -> Topology:
    """Turn a preset (or an explicit mapping) into a validated :class:`Topology`.

    ``devices_parallel`` is the count EVERY switch position gets;
    ``devices_parallel_by_bridge`` overrides it per bridge (owner 2026-09-22:
    *«надо добавить number of parallel»* — visible and editable where the
    device is chosen, the same for all bridges by default).
    """
    p = str(preset or "").strip().lower()
    if p not in TOPOLOGY_PRESETS:
        raise TopologyError(
            f"topology {preset!r} is not one this controller knows: "
            + ", ".join(sorted(TOPOLOGY_PRESETS)))
    sd = str(star_delta or "star").strip().lower()
    if sd not in ("star", "delta", "wye", "y"):
        raise TopologyError(f"star_delta must be 'star' or 'delta'; got {star_delta!r}")
    sd = "delta" if sd == "delta" else "star"
    coils = list(coils)
    if not coils:
        raise TopologyError("a controller needs at least one coil to drive")
    npar = max(int(devices_parallel or 1), 1)
    common: Dict[str, Any] = {"devices_parallel": npar, "device": device,
                              "v_dc_V": v_dc_V}
    notes: List[str] = []

    if p == "one_3ph":
        b = _three_phase_bridge("INV1", "Inverter 1", coils, sd, **common)
        bridges = [b]
        notes.append(
            f"one three-phase two-level inverter, {sd}: each leg carries the "
            + ("LINE current (sqrt(3) x the phase current) because the bridge "
               "sits outside the delta" if sd == "delta"
               else "phase current"))
    elif p == "two_3ph":
        odd = [c for c in coils if c.index % 2 == 1]
        even = [c for c in coils if c.index % 2 == 0]
        if not odd or not even:
            raise TopologyError(
                "two 3-phase inverters need the coils to split into two sets; "
                f"this winding has {len(coils)} coil(s)")
        b1 = _three_phase_bridge("INV1", "Inverter 1 (odd coils)", odd, sd, **common)
        b2 = _three_phase_bridge("INV2", "Inverter 2 (even coils)", even, sd, **common)
        shift = (_mean_angle_deg(even) - _mean_angle_deg(odd)) % 360.0
        b2.phase_shift_deg = shift
        bridges = [b1, b2]
        notes.append(
            f"two three-phase inverters, {sd}, each with its own star point; "
            f"set 2 sits {shift:.1f} deg electrical from set 1 (from the winding "
            "builder's slot angles, not assumed)")
        notes.append("EQUAL SPLIT ASSUMPTION: the duty's current is divided "
                     "equally between the two inverters (same machine, same "
                     "control); an unbalanced split is not modelled")
    elif p == "h_bridge":
        mod = str(h_bridge_modulation or "unipolar").strip().lower()
        if mod not in H_BRIDGE_MODULATIONS:
            raise TopologyError("h_bridge_modulation must be "
                                + " or ".join(H_BRIDGE_MODULATIONS))
        bridges = []
        for c in coils:
            legs = [Leg(name="P", coils=[c.index], polarity=c.polarity),
                    Leg(name="N", coils=[c.index], polarity=-c.polarity)]
            bridges.append(Bridge(
                id=f"HB{c.index}", kind="h_bridge", legs=legs,
                connection="independent", coils=[c.index],
                label=(f"H-bridge {c.index} · coil {c.index} "
                       f"{c.phase}{'+' if c.polarity > 0 else '-'}"),
                modulation=mod,
                phase_shift_deg=(c.angle_elec_deg - coils[0].angle_elec_deg) % 360.0,
                **common))
        notes.append(
            f"one full H-bridge per coil, {mod} PWM: 4 switches per coil, each "
            "coil across the FULL DC link, no star point and no circulating "
            "path between coils")
        notes.append(
            "the coil current is taken as the machine's phase current (the "
            "coils of one phase carry it in series in this winding); a per-coil "
            "current the EM solver produces replaces it in Stage 2")
    else:                                            # custom
        if not mapping:
            raise TopologyError(
                "topology 'custom' needs a mapping: a list of "
                "{coil, bridge, leg} rows")
        bridges = _custom_bridges(mapping, coils, sd, common)
        notes.append("custom mapping: validated only for completeness "
                     "(every coil assigned exactly once) and leg consistency")

    if devices_parallel_by_bridge:
        known = {b.id for b in bridges}
        alien = [k for k in devices_parallel_by_bridge if k not in known]
        if alien:
            raise TopologyError(
                "devices_parallel_by_bridge names bridge(s) "
                + ", ".join(sorted(alien))
                + " that this topology does not have (" + ", ".join(sorted(known)) + ")")
        for b in bridges:
            v = devices_parallel_by_bridge.get(b.id)
            if v is None:
                continue
            try:
                n = int(v)
            except (TypeError, ValueError):
                raise TopologyError(
                    f"devices_parallel_by_bridge[{b.id}] must be a whole "
                    f"number of devices; got {v!r}")
            if n < 1:
                raise TopologyError(
                    f"devices_parallel_by_bridge[{b.id}] must be at least 1; "
                    f"got {n}")
            b.devices_parallel = n
        notes.append("devices in parallel per switch set per bridge: "
                     + ", ".join(f"{b.id} x{b.devices_parallel}" for b in bridges))

    _validate(bridges, coils)
    return Topology(preset=p, coils=coils, bridges=bridges, notes=notes,
                    star_delta=sd)


def _custom_bridges(mapping: Sequence[Dict[str, Any]], coils: Sequence[Coil],
                    sd: str, common: Dict[str, Any]) -> List[Bridge]:
    by_bridge: Dict[str, Dict[str, Leg]] = {}
    order: List[str] = []
    known = {c.index for c in coils}
    for i, row in enumerate(mapping):
        if not isinstance(row, dict):
            raise TopologyError(f"mapping[{i}] must be an object "
                                "{coil, bridge, leg}")
        try:
            ci = int(row.get("coil"))
        except (TypeError, ValueError):
            raise TopologyError(f"mapping[{i}].coil must be a coil index")
        if ci not in known:
            raise TopologyError(
                f"mapping[{i}].coil = {ci} is not a coil of this machine "
                f"(1..{max(known)})")
        bid = str(row.get("bridge") or "").strip()
        leg = str(row.get("leg") or "").strip().upper()
        if not bid or not leg:
            raise TopologyError(f"mapping[{i}] needs both bridge and leg")
        pol = row.get("polarity")
        pol = 1 if pol is None else (1 if int(pol) >= 0 else -1)
        if bid not in by_bridge:
            by_bridge[bid] = {}
            order.append(bid)
        lg = by_bridge[bid].setdefault(leg, Leg(name=leg, polarity=pol))
        lg.coils.append(ci)
    out: List[Bridge] = []
    for bid in order:
        legs = [by_bridge[bid][n] for n in sorted(by_bridge[bid])]
        names = {l.name for l in legs}
        if len(legs) == 3:
            kind, conn = "three_phase_2l", sd
        elif len(legs) == 2:
            kind, conn = "h_bridge", "independent"
        else:
            raise TopologyError(
                f"bridge {bid!r} has {len(legs)} leg(s) ({', '.join(sorted(names))}); "
                "a bridge is either 3 legs (three-phase two-level) or 2 "
                "(H-bridge)")
        cs = sorted({c for l in legs for c in l.coils})
        out.append(Bridge(id=bid, kind=kind, legs=legs, connection=conn,
                          coils=cs, label=f"Bridge {bid}", **common))
    return out


def _validate(bridges: Sequence[Bridge], coils: Sequence[Coil]) -> None:
    """Every coil driven by EXACTLY ONE BRIDGE — loudly, by coil number.

    Per BRIDGE, not per leg, and that distinction is the whole point of the
    H-bridge topology: a coil there appears on BOTH legs of its bridge because
    those legs are its two ENDS.  Two different bridges fighting over one coil
    is the error this catches.
    """
    seen: Dict[int, List[str]] = {}
    problems: List[str] = []
    for b in bridges:
        legs_of: Dict[int, List[str]] = {}
        for l in b.legs:
            for c in l.coils:
                legs_of.setdefault(c, []).append(l.name)
        want = 2 if b.kind == "h_bridge" else 1
        for c, names in sorted(legs_of.items()):
            seen.setdefault(c, []).append(f"{b.id}/{'+'.join(names)}")
            if len(names) != want:
                problems.append(
                    f"coil {c} sits on {len(names)} leg(s) of {b.id} "
                    f"({', '.join(names)}); a "
                    + ("full H-bridge drives a coil from BOTH its legs"
                       if b.kind == "h_bridge"
                       else "three-phase leg drives a coil from exactly one"))
    idx = {c.index for c in coils}
    missing = sorted(idx - set(seen))
    twice = sorted(c for c, where in seen.items() if len(where) > 1)
    alien = sorted(set(seen) - idx)
    if missing:
        problems.append("coil(s) " + ", ".join(map(str, missing))
                        + " are not connected to any bridge")
    if twice:
        problems.append("; ".join(
            f"coil {c} is driven by {' and '.join(seen[c])}" for c in twice))
    if alien:
        problems.append("bridge(s) reference coil(s) "
                        + ", ".join(map(str, alien)) + " that do not exist")
    if problems:
        raise TopologyError("this mapping does not wire the machine: "
                            + "; ".join(problems))
