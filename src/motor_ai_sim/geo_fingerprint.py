"""The canonical machine fingerprint and its legacy alias (2026-11-25).

``routes.simulation._geometry_fingerprint`` is a CACHE KEY: the md5 of ``{"g": config geometry block, "glive": the live geometry
object}``.  On 2026-11-15 the geometry object gained four keys at their DEFAULT value (``wire_omit``, ``k_end_by_turns``,
``magnet_flat``, ``magnet_pocket_clearance``; the "absent means" table of ``routes.family``), so every fingerprint stored before
that day stopped matching the SAME machine (45 vs 49 keys).  RULE (owner / coordinator 2026-11-25): a geometry key added after
the 2026-10-20 baseline, at its default value, does not change the fingerprint.

* ``canonical_geometry`` drops those keys when they equal their default; the canonical print reproduces the pre-2026-11-15
  fingerprint for a machine at defaults.
* ``legacy49`` is the print with every key kept: records written since 2026-11-15 carry it.
* both are hashed from ONE snapshot of the geometry (``routes.simulation._geometry_fingerprint_pair``) and registered as
  equivalent; a reader that matches a stored fingerprint against the live one accepts either (``same`` / ``accepted``).
  Nothing stored is rewritten.
* writers keep storing legacy49 until ``WRITE_CANONICAL`` is switched on in a later deploy (see that constant and
  ``docs/GEO_FINGERPRINT_ROLLOUT_2026-11-26.md``).

A NEW geometry key must be registered consciously: ``KNOWN_POST_BASELINE`` lists every key the live object may carry beyond
the baseline set; ``tests/test_geo_fingerprint.py`` fails when ``get_current_geometry().to_dict()`` gains a key that is in
neither list.  Pure module (no import of the routes at import time).
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Dict, Iterable, List, Mapping, Optional

#: What an ABSENT geometry key means for the builders (``routes.family._ABSENT_MEANS`` reads this table: ONE source).  A stored
#: configuration written before a key existed names none; applying it fills these.
ABSENT_MEANS: Dict[str, Any] = {
    "sleeve_thickness": 0.0,   # no retaining ring
    "wire_parallel": 1,        # one wire in hand
    "wire_split": 1,           # one strip per wire row
    "wire_omit": 0,            # every grid position filled
    "k_end_by_turns": 0,       # the layer-count end-turn rule of the stored machines
    "magnet_flat": 0,          # the arc-topped magnet of the stored machines
    "magnet_pocket_clearance": 0.0,   # the pocket is the magnet
}

#: Keys added to the geometry AFTER the 2026-10-20 baseline (PR-D / PR-E of the CIANO10 12_40 series, 2026-11-15).  At their
#: default (``ABSENT_MEANS``) they are left out of the canonical fingerprint; a non-default value is part of the machine.
POST_BASELINE_KEYS = ("wire_omit", "k_end_by_turns", "magnet_flat", "magnet_pocket_clearance", "wire_bore_margin")

#: The default of every post-baseline key: ``ABSENT_MEANS`` where the application fills one, else "unset" (None / blank):
#: ``wire_bore_margin`` (schema key of 2026-11-15, absent / blank = the insulation thickness) is never filled in.
DEFAULT_OF: Dict[str, Any] = {**ABSENT_MEANS, "wire_bore_margin": None}

#: Every other key the live geometry object may carry that is NOT in the 2026-10-20 baseline and is NOT dropped at its default:
#: register a new key in ``POST_BASELINE_KEYS`` (it then must have an ``ABSENT_MEANS`` default) or here, never silently.
KNOWN_POST_BASELINE: tuple = ()

#: The geometry keys of the 2026-10-20 baseline (the live object of the repo configuration at 95c4a5b4 plus the die-level free keys
#: a catalogue machine adds): the test pins the live object against baseline + registered keys.
BASELINE_KEYS = frozenset({
    "air_gap", "angle_pole", "angle_slot", "core_thickness", "cut_width", "insulation_thickness", "magnet_down_height",
    "magnet_fill_down", "magnet_fill_radius", "magnet_fill_up", "magnet_height", "magnet_lamination", "magnet_up_gap",
    "motor_length", "num_poles", "num_poles_per_segment", "num_seg", "num_slots", "num_slots_per_segment", "num_wires_per_slot",
    "pole_pitch", "rotor_fill_r", "rotor_hole", "rotor_house_height", "rotor_inner_radius", "rotor_outer_radius",
    "shaft_diameter", "shaft_height", "slot_height", "slot_hs", "slot_pitch", "slot_width", "stator_diameter",
    "stator_fillet_r", "stator_fillet_r1", "stator_inner_radius", "stator_outer_radius", "tooth2_width", "tooth_width",
    "wire_height", "wire_spacing_x", "wire_spacing_y", "wire_split", "wire_width",
    # keys a catalogue machine / the die-level save adds (all at least as old as the baseline)
    "wire_parallel", "sleeve_thickness", "stator_outer_notches",
})


def _is_default(key: str, value: Any) -> bool:
    """``value`` is what an absent ``key`` means (None / blank = unset = the default)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return True
    want = DEFAULT_OF[key]
    if want is None:
        return False
    try:
        return float(value) == float(want)
    except (TypeError, ValueError):
        return False


def canonical_geometry(geo: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """A copy of the geometry dict without the post-baseline keys that sit at their default; ``None`` stays ``None``."""
    if geo is None:
        return None
    return {k: v for k, v in dict(geo).items() if not (k in POST_BASELINE_KEYS and _is_default(k, v))}


#: Staged rollout (review of PR #891, 2026-11-26).  During a deploy the API and the worker may run different builds for a few
#: minutes, and a build before this one compares the legacy49 print LITERALLY.  Stage 1 (this build): writers keep writing the
#: legacy49 print (bit-identical to the build before), readers accept both prints.  Stage 2 (a LATER deploy, once no process of
#: a build without these readers is left): set this True and writers store the canonical print.  Readers need no change then.
WRITE_CANONICAL = False


# ── the alias book: every fingerprint -> the group of fingerprints computed for the SAME machine (one geometry snapshot) in
# this process.  Symmetric: canonical -> legacy49 and legacy49 -> canonical. ───────────────────────────────────────────────
_BOOK: "OrderedDict[str, frozenset]" = OrderedDict()
_BOOK_MAX = 1024
_LOCK = threading.Lock()


def register(canonical: str, legacy: str) -> None:
    """Record two prints of ONE machine as equivalent.  Callers derive both from one snapshot of its geometry."""
    if not canonical or not legacy or canonical == legacy:
        return
    with _LOCK:
        group = set(_BOOK.pop(canonical, ())) | set(_BOOK.pop(legacy, ())) | {canonical, legacy}
        frozen = frozenset(group)
        for k in group:
            _BOOK.pop(k, None)
            _BOOK[k] = frozen
        while len(_BOOK) > _BOOK_MAX:
            _BOOK.popitem(last=False)


def aliases(fp: Optional[str]) -> tuple:
    """The OTHER prints known for the machine of ``fp`` (canonical or legacy49), sorted; empty when none is known."""
    if not fp:
        return ()
    with _LOCK:
        return tuple(sorted(_BOOK.get(str(fp), frozenset()) - {str(fp)}))


def accepted(fp: Optional[str], live_fp_for: Optional[Any] = None) -> List[str]:
    """``[fp, *its equivalent prints]`` for ``fp`` (canonical or legacy49 of a machine this process has fingerprinted).  When the
    book does not know ``fp`` yet, ``live_fp_for`` (``None`` = the machine loaded now, or a geometry override dict) is
    fingerprinted first, which registers the pair if ``fp`` is one of that machine's prints."""
    if not fp:
        return []
    fp = str(fp)
    if not aliases(fp):
        try:
            from motor_ai_sim.routes.simulation import _geometry_fingerprint_pair
            _geometry_fingerprint_pair(live_fp_for if isinstance(live_fp_for, dict) else None)
        except Exception:                                         # noqa: BLE001 - no machine to resolve: this print only
            pass
    return [fp, *aliases(fp)]


def same(stored: Optional[str], live: Optional[str], geo_override: Optional[dict] = None) -> bool:
    """Is the STORED fingerprint the machine whose live fingerprint is ``live``?  Equal, or one is the other's equivalent print
    (canonical <-> legacy49), in either direction."""
    if not stored or not live:
        return False
    if str(stored) == str(live):
        return True
    return str(stored) in accepted(live, geo_override) or str(live) in accepted(stored)


def any_of(stored: Iterable[Optional[str]], live: Optional[str], geo_override: Optional[dict] = None) -> bool:
    return any(same(s, live, geo_override) for s in stored)
