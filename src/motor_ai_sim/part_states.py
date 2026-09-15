"""Per-part ACCOUNTING state — included / reference / excluded.

A frameless motor is sold as a rotor+stator pair: the customer supplies the
shaft (and, on some builds, the housing) and bolts our active parts onto it.
That part is REAL to the field — a steel shaft carries flux and dissipates eddy
currents that heat OUR machine — but it is NOT ours to weigh, and billing a
customer's shaft into N·m/kg would flatter the datasheet with metal we never
shipped.  Hence three states, per part, for ANY part:

  ``included``   (default, and what every build did before this module)
                 in the field solve, in the mass, in the inertia, in every
                 per-mass density and in the datasheet.

  ``reference``  "not ours, but something will sit there".  The part stays in
                 the field solve with its assigned material — identical physics,
                 identical losses, which stay in P_loss and efficiency because
                 the heat is real — and leaves the MASS side completely: no
                 mass, no rotor inertia term, no row in any total, and the
                 datasheet says "customer-supplied".

  ``excluded``   solved as AIR (mu_r = 1, sigma = 0), zero mass, absent from
                 every total, and invisible in the 3D / cross-section views.

The state map rides the SAME channel the per-part MATERIAL assignment already
uses — the shared config's ``parts:`` block and the per-request material
context's ``parts`` key (``?mat=``) — deliberately, so that:

  * every cache key and fingerprint that already hashes the material context
    (``routes/simulation._config_physics_fingerprint``, which folds in
    ``get_request_materials()`` verbatim) distinguishes the three states with
    no extra plumbing, and
  * a family configuration's ``parts:`` block lands in ``family._build_sig``
    beside its ``materials:``, so flipping a state marks stored duty results as
    "computed on an older build" exactly as re-assigning a steel does.

Absent map = every part ``included`` = byte-identical behaviour to before.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

INCLUDED = "included"
REFERENCE = "reference"
EXCLUDED = "excluded"

#: The three states, in the order the UI cycles them.
STATES = (INCLUDED, REFERENCE, EXCLUDED)

#: Parts that can carry a state.  Same keys the material assignment uses
#: (``masses._PARTS`` / config ``materials:``), so one part means one thing
#: everywhere: mass row, density, field domain, 3D mesh key.
STATEFUL_PARTS = ("stator_core", "rotor_core", "magnet", "slot", "shaft",
                  # The carbon-fibre retaining ring on the rotor OD.  It only
                  # EXISTS when sleeve_thickness > 0; a state carried for a
                  # machine that has none is inert (there is no region to
                  # build), exactly as a state for a part with zero area is.
                  "sleeve")

#: Parts whose exclusion is a RADICAL experiment rather than a packaging
#: choice — removing them removes the machine's magnetics.  The solver refuses
#: nothing (the user asked for "любую деталь"); the UI warns in amber.
MAGNETICALLY_ACTIVE_PARTS = ("stator_core", "rotor_core", "magnet", "slot")


class UnknownPartStateError(ValueError):
    """A parts map naming a state or a part that does not exist.

    Loud on purpose (client-facing validation rule): a typo'd ``"exclude"``
    silently read as ``included`` would answer a different question — the whole
    class of bug this project refuses to ship.
    """


def normalize_states(raw: Any, *, strict: bool = False) -> Dict[str, str]:
    """``{part: state}`` cleaned up: lower-cased, unknown entries dropped
    (or raised on with ``strict``), and explicit ``included`` KEPT.

    ``included`` survives normalisation because a per-request override must be
    able to say "this part is ours after all" over a config that says
    ``reference``.  ``resolve`` drops the redundant entries at the very end.
    """
    out: Dict[str, str] = {}
    if not raw:
        return out
    try:
        items = dict(raw).items()
    except Exception:                     # noqa: BLE001 — not a mapping
        if strict:
            raise UnknownPartStateError(f"parts must be a mapping, got {type(raw).__name__}")
        return out
    for k, v in items:
        part = str(k or "").strip()
        state = str(v or "").strip().lower()
        if not part or not state:
            continue
        if part not in STATEFUL_PARTS:
            if strict:
                raise UnknownPartStateError(
                    f"unknown part {part!r} in the parts map — "
                    f"valid parts: {', '.join(STATEFUL_PARTS)}")
            continue
        if state not in STATES:
            if strict:
                raise UnknownPartStateError(
                    f"unknown state {v!r} for part {part!r} — "
                    f"valid states: {', '.join(STATES)}")
            continue
        out[part] = state
    return out


def config_part_states() -> Dict[str, str]:
    """The shared config's ``parts:`` block (config/motor_config.yaml)."""
    try:
        from motor_ai_sim.config import get_config
        cfg = get_config() or {}
        raw = cfg.get("parts")
        try:                                # OmegaConf DictConfig → plain
            from omegaconf import OmegaConf
            if OmegaConf.is_config(raw):
                raw = OmegaConf.to_container(raw, resolve=True)
        except Exception:                   # noqa: BLE001
            pass
        return normalize_states(raw)
    except Exception:                       # noqa: BLE001 — no config is not a
        return {}                           # reason to invent a state


def request_part_states() -> Dict[str, str]:
    """This request's ``parts`` map, carried inside the material context."""
    try:
        from motor_ai_sim.material_context import get_request_materials
        return normalize_states((get_request_materials() or {}).get("parts"))
    except Exception:                       # noqa: BLE001
        return {}


def resolve(states: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The effective ``{part: state}`` — ONLY the non-default entries.

    Precedence mirrors ``masses._assignments`` exactly: shared config, then the
    per-request override, then an explicit argument (a caller scoring a
    candidate under a different accounting than the saved one).  An empty dict
    means "everything included", i.e. today's behaviour.
    """
    merged: Dict[str, str] = {}
    merged.update(config_part_states())
    merged.update(request_part_states())
    merged.update(normalize_states(states))
    return {k: v for k, v in merged.items() if v != INCLUDED}


def part_state(part: str, states: Optional[Dict[str, str]] = None) -> str:
    """This part's state; ``included`` when nothing says otherwise."""
    return resolve(states).get(part, INCLUDED)


def is_excluded(part: str, states: Optional[Dict[str, str]] = None) -> bool:
    """Solved as air, weighs nothing, drawn nowhere."""
    return part_state(part, states) == EXCLUDED


def is_reference(part: str, states: Optional[Dict[str, str]] = None) -> bool:
    """In the field with its own material; out of every mass/inertia total."""
    return part_state(part, states) == REFERENCE


def counts_in_mass(part: str, states: Optional[Dict[str, str]] = None) -> bool:
    """Does this part's metal belong to the machine we ship and weigh?"""
    return part_state(part, states) == INCLUDED


def state_note(part: str, material: str = "", state: Optional[str] = None) -> str:
    """The one-line honesty label a card / datasheet row carries for a part.

    Empty for an ``included`` part — the default must add nothing anywhere.
    """
    st = state or part_state(part)
    if st == REFERENCE:
        return ("customer-supplied — modelled as "
                f"{material or 'the assigned material'} for the field, "
                "not included in mass")
    if st == EXCLUDED:
        return "excluded — solved as air, no mass, not drawn"
    return ""
