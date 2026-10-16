"""Thermal MASS of a machine — Σ m·c_p per lumped node, with every c_p sourced.

A steady map answers "how hot does it get"; a duty cycle answers "how fast does
it get there", and the only new physics that question needs is the heat
CAPACITY of each part.  This module is that half of it: it turns the mass rows a
solved duty already carries (``summary.mass_components`` — the same rows the
datasheet and the report print) into the four lumped capacities the transient
network integrates:

    winding  ←  Copper windings
    stator   ←  Stator core                       (the housing rides with it)
    rotor    ←  Rotor back-iron + Shaft
    magnet   ←  Magnets (+ the retaining sleeve, see ``SLEEVE_NODE``)

Two rules, both of them the project's own:

  * **the mass is the machine's, not a re-derivation.**  The rows come from the
    run that solved this duty, so a reference part (a customer-supplied shaft,
    counted at 0 kg in the active mass) still carries its MODELLED mass here —
    it is real aluminium that really absorbs heat, whoever bought it — and an
    excluded part is not in the rows at all;

  * **a default is REPORTED as a default** (the precedent is
    ``routes.thermal._sleeve_k``).  Half of the steel cards in
    ``config/materials_library.yaml`` carry ``specific_heat: null`` — 20SW1200,
    the steel of the machine this was written for, is one of them — so the
    numbers in :data:`CP_DEFAULT` are used and every part says which of the two
    it got, ``"library"`` or ``"default"``.  A capacity is a time constant; a
    silent 470 J/kg·K would be an answer nobody could argue with.

Pure: no FastAPI, no route, no I/O beyond the materials library.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

#: The four lumped nodes, in the order the network writes them.
NODES: Tuple[str, ...] = ("winding", "stator", "rotor", "magnet")

#: Which node each mass row belongs to.  ``shaft`` joins the ROTOR because it is
#: bolted to the rotor iron and shares its temperature to within the couple of
#: kelvin a lumped model resolves; the magnets are a node of their own because
#: they are the part with a LIMIT (demagnetisation), and the thing a duty cycle
#: is asked about is how close a pulse takes them to it.
PART_NODE: Dict[str, str] = {
    "stator_core": "stator",
    "slot": "winding",
    "magnet": "magnet",
    "rotor_core": "rotor",
    "shaft": "rotor",
    "sleeve": "magnet",
}

#: The retaining ring rides with the MAGNETS: it is bonded to their outer face,
#: it is the blanket their heat has to get through, and on the machines that
#: have one it is a third of the magnet node's own capacity.  Named rather than
#: buried in the table above because it is a modelling judgement, not a fact.
SLEEVE_NODE = PART_NODE["sleeve"]

#: Specific heats [J/(kg·K)] used when the assigned material card has none, by
#: the mass row's own material class.  Textbook room-temperature values:
#:
#:   electrical steel  470   (Fe-3%Si, non-oriented; 460-480 across suppliers)
#:   steel             480   (plain carbon / stainless shaft steel)
#:   copper            385   (the library's Cu card, kept here for a bare row)
#:   NdFeB             460   (sintered, 440-500 depending on grade)
#:   aluminium         896   (6061; the library's own Aluminium_6061 figure)
#:   insulator        1200   (aramid paper / epoxy; only if a row is ever cut
#:                            for the slot insulation, which today's are not)
#:   carbon fibre     1050   (UD CFRP laminate, through-thickness)
#:
#: Every one of them is within ±5 % of any datasheet an engineer would quote,
#: which is well inside what a lumped four-node model claims — but the SOURCE is
#: reported per part so the difference is never silently absorbed.
CP_DEFAULT: Dict[str, float] = {
    "electrical steel": 470.0,
    "steel": 480.0,
    "copper": 385.0,
    "NdFeB": 460.0,
    "aluminium": 896.0,
    "insulator": 1200.0,
    "carbon fibre": 1050.0,
}

#: Material-library categories searched for a card name, per part.  Ordered: the
#: first category that HAS the card wins, exactly as ``_sleeve_k`` does it.
_CARD_CATEGORIES: Dict[str, Tuple[str, ...]] = {
    "stator_core": ("steel",),
    "rotor_core": ("steel",),
    "magnet": ("magnet",),
    "slot": ("conductor", "insulator"),
    "shaft": ("conductor", "steel", "insulator"),
    "sleeve": ("insulator", "conductor", "steel"),
}

#: How a mass row's ``name`` identifies its part.  The rows are built in
#: ``routes.simulation`` (``_row("stator_core", "Stator core (20SW1200)", …)``)
#: and the prefix is the stable half of them — the parenthesised card name and
#: the "— customer-supplied" suffix both move with the machine.
_NAME_PREFIX: Tuple[Tuple[str, str], ...] = (
    ("stator core", "stator_core"),
    ("copper windings", "slot"),
    ("windings", "slot"),
    ("magnets", "magnet"),
    ("rotor back-iron", "rotor_core"),
    ("rotor back iron", "rotor_core"),
    ("shaft", "shaft"),
    ("sleeve", "sleeve"),
)

_CARD_IN_NAME = re.compile(r"\(([^()]+)\)")


class CapacityError(ValueError):
    """A mass row this model cannot place, or a mass it cannot believe.

    Loud on purpose: a row silently dropped from a capacity is a time constant
    silently shortened, and the answer — "the winding reaches 180 °C in 8 s" —
    would still look entirely reasonable.
    """


# ---------------------------------------------------------------------------
# Reading one mass row
# ---------------------------------------------------------------------------

def _part_of_row(row: Mapping[str, Any]) -> str:
    """Which modelled part this mass row is, by its name (never by its mass)."""
    name = str(row.get("name") or "").strip().lower()
    for prefix, part in _NAME_PREFIX:
        if name.startswith(prefix):
            return part
    raise CapacityError(
        "mass row %r is not one of the parts this thermal model knows "
        "(%s).  A new part needs a node before a duty cycle can be integrated "
        "— its heat capacity is not optional."
        % (row.get("name"), ", ".join(sorted(set(PART_NODE)))))


def _card_candidates(row: Mapping[str, Any], part: str,
                     materials: Optional[Mapping[str, Any]]) -> List[str]:
    """The material CARD this row was built with, best guess first.

    The configuration's own ``materials:`` block wins (it is the assignment the
    solve ran under); the parenthesised name in the row is next, and it is what
    a stored summary from a configuration nobody kept still has; the row's
    ``material`` field is last, and on the copper row it is the one that hits —
    the label reads "Copper windings (Cu)" (a chemical symbol, not a card) while
    the field carries the library's own ``copper``.
    """
    out: List[str] = []
    if materials:
        got = materials.get(part)
        if got:
            out.append(str(got))
    m = _CARD_IN_NAME.search(str(row.get("name") or ""))
    if m and m.group(1).strip():
        out.append(m.group(1).strip())
    if row.get("material"):
        out.append(str(row["material"]))
    seen: set = set()
    return [c for c in out if not (c in seen or seen.add(c))]


def _class_of(row: Mapping[str, Any], card: Optional[str]) -> str:
    """The :data:`CP_DEFAULT` class of a row — its ``material`` field, mapped
    onto the seven names that table uses.

    The ``material`` field is written by the mass model and is usually already
    one of them ("electrical steel", "NdFeB", "copper"); the two that are not
    are the SHAFT (whose field is literally ``"shaft"``) and a slot row on a
    machine with a named conductor card, so both are decided by the card name.
    """
    raw = str(row.get("material") or "").strip().lower()
    text = f"{raw} {str(card or '').lower()}"
    if "ndfeb" in text or raw in ("magnet", "pm"):
        return "NdFeB"
    if "carbon" in text or "cfrp" in text:
        return "carbon fibre"
    if "alumin" in text:
        return "aluminium"
    if "copper" in text or raw == "cu" or "cu_" in text:
        return "copper"
    if "electrical steel" in raw:
        return "electrical steel"
    if "steel" in text or "iron" in text or "titan" in text:
        return "steel"
    if raw in ("insulator", "insulation", "nomex", "kapton"):
        return "insulator"
    raise CapacityError(
        "mass row %r carries material %r, which is not one of the classes a "
        "default specific heat exists for (%s).  Add the class to CP_DEFAULT, "
        "or put a specific_heat on the card."
        % (row.get("name"), row.get("material"), ", ".join(sorted(CP_DEFAULT))))


def _library_cp(part: str,
                cards: Iterable[str]) -> Tuple[Optional[float], Optional[str]]:
    """``(specific_heat, the card it came off)`` — ``(None, card)`` when the card
    exists but carries no ``specific_heat`` (the normal case for the electrical
    steels in this library), ``(None, None)`` when no candidate resolves at all.
    """
    try:
        from motor_ai_sim.materials import get_material
    except Exception:  # noqa: BLE001 — a capacity must not need the library
        return None, None
    first_hit: Optional[str] = None
    for card in cards:
        for cat in _CARD_CATEGORIES.get(part, ("steel", "magnet", "conductor",
                                               "insulator")):
            try:
                mat = get_material(cat, str(card))
            except Exception:  # noqa: BLE001 — wrong category, or no such card
                continue
            first_hit = first_hit or str(card)
            try:
                cp = float(getattr(mat, "specific_heat", None))
            except (TypeError, ValueError):
                break            # this card HAS no c_p — try the next candidate
            if cp > 0.0:
                return cp, str(card)
            break
    return None, first_hit


def _row_mass_kg(row: Mapping[str, Any]) -> float:
    """The mass this row's part ACTUALLY has, reference parts included.

    ``mass_kg`` is the ACCOUNTING mass — 0 on a customer-supplied shaft, because
    it is not ours to bill — and ``mass_modelled_kg`` is the physical one.  Heat
    does not care who paid for the part, so the modelled mass is what a capacity
    is built from, and the row says which of the two it used.
    """
    if row.get("counted") is False or row.get("state") == "reference":
        m = row.get("mass_modelled_kg")
        if m is None:
            m = row.get("mass_kg")
        return float(m or 0.0)
    return float(row.get("mass_kg") or 0.0)


# ---------------------------------------------------------------------------
# The public call
# ---------------------------------------------------------------------------

def part_capacities(summary: Mapping[str, Any],
                    materials: Optional[Mapping[str, Any]] = None,
                    part_states: Optional[Mapping[str, str]] = None,
                    *,
                    cp_overrides: Optional[Mapping[str, float]] = None,
                    ) -> Dict[str, Dict[str, Any]]:
    """``{node: {C_J_per_K, mass_kg, cp_source, parts:[…]}}`` for one solved duty.

    Parameters
    ----------
    summary
        A duty's ``summary`` block — the one thing needed is
        ``mass_components``, the rows the run's own mass model produced.
    materials
        The configuration's ``materials:`` assignment (``{part: card}``).
        Optional: the card name in each row's label is the fallback.
    part_states
        ``{part: included|reference|excluded}``.  Defaults to the states the
        summary was computed under (``summary.part_states``).  An EXCLUDED part
        is dropped even if a stale row for it is still in the summary.
    cp_overrides
        ``{part: c_p}`` — a datasheet number the user supplied for a card the
        library has none for.  Reported as source ``"given"``.

    Every node is present even when it has no parts (``C_J_per_K`` 0.0 and an
    empty ``parts`` list): "this machine has no sleeve" is an answer, and a
    missing key is not.
    """
    rows: Iterable[Mapping[str, Any]] = (summary or {}).get("mass_components") or ()
    if not rows:
        raise CapacityError(
            "this duty's summary carries no mass_components, so there is no "
            "thermal mass to integrate.  Re-run the operating point (the mass "
            "rows are written by the same run that solves it) and save the duty "
            "again.")
    states = dict(part_states if part_states is not None
                  else ((summary or {}).get("part_states") or {}))
    overrides = dict(cp_overrides or {})

    out: Dict[str, Dict[str, Any]] = {
        n: {"C_J_per_K": 0.0, "mass_kg": 0.0, "cp_source": "none", "parts": []}
        for n in NODES}

    for row in rows:
        part = _part_of_row(row)
        if str(states.get(part, "")).lower() == "excluded":
            continue
        node = PART_NODE[part]
        mass = _row_mass_kg(row)
        if mass < 0.0:
            raise CapacityError("mass row %r has a negative mass (%g kg)"
                                % (row.get("name"), mass))
        cands = _card_candidates(row, part, materials)
        card = cands[0] if cands else None
        klass = _class_of(row, card)
        if part in overrides:
            cp, src = float(overrides[part]), "given"
        else:
            lib, used = _library_cp(part, cands)
            if lib is not None:
                cp, src, card = lib, "library", (used or card)
            else:
                cp, src = CP_DEFAULT[klass], "default"
        entry = {
            "part": part,
            "name": str(row.get("name") or part),
            "material": card,
            "material_class": klass,
            "mass_kg": round(mass, 6),
            "mass_source": ("modelled (reference part — not in the active mass)"
                            if (row.get("counted") is False
                                or row.get("state") == "reference")
                            else "mass_components"),
            "cp_J_per_kg_K": round(float(cp), 1),
            "cp_source": src,
            "C_J_per_K": round(mass * float(cp), 3),
        }
        node_out = out[node]
        node_out["parts"].append(entry)
        node_out["C_J_per_K"] = round(node_out["C_J_per_K"] + entry["C_J_per_K"], 3)
        node_out["mass_kg"] = round(node_out["mass_kg"] + mass, 6)

    for node, blk in out.items():
        srcs = {p["cp_source"] for p in blk["parts"]}
        blk["cp_source"] = ("none" if not srcs else
                            srcs.pop() if len(srcs) == 1 else "mixed")
        if blk["parts"] and blk["C_J_per_K"] <= 0.0:
            raise CapacityError(
                "node %r has parts but zero heat capacity — a transient solved "
                "on it would be instantaneous.  Rows: %s"
                % (node, [p["name"] for p in blk["parts"]]))
    return out


def capacities_j_per_k(caps: Mapping[str, Mapping[str, Any]]) -> Dict[str, float]:
    """``{node: C}`` — the four numbers the integrator wants, nothing else."""
    return {n: float(caps[n]["C_J_per_K"]) for n in NODES if n in caps}


def cp_sources(caps: Mapping[str, Mapping[str, Any]]) -> Dict[str, str]:
    """``{node: "library"|"default"|"given"|"mixed"|"none"}`` — where each
    node's specific heat came from, for the record and for the report."""
    return {n: str(caps[n]["cp_source"]) for n in NODES if n in caps}


def capacity_rows(caps: Mapping[str, Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Every part, flat, in node order — the table a report prints."""
    rows: List[Dict[str, Any]] = []
    for node in NODES:
        for p in (caps.get(node) or {}).get("parts", ()):
            rows.append(dict(p, node=node))
    return rows


def total_capacity_j_per_k(caps: Mapping[str, Mapping[str, Any]]) -> float:
    """ΣmC_p over the whole machine — the perfectly-mixed bound a transient
    answer is sanity-checked against."""
    return float(sum(float(caps[n]["C_J_per_K"]) for n in NODES if n in caps))
