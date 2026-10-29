"""Which torque ripple may be quoted (owner 2026-10-29, option 1).

The machine runs at its configured air-gap layers (1/side by default).  That
is enough for torque, losses and heat, but not always for the ripple: the
rotor- and stator-side Coulomb rings then disagree by more than the 5 % gate
(``ripple_mesh_limited``).  The engineer may ask for a refined ripple ("Refine
ripple": the same point at >= 3 layers/side, routes.simulation
``ripple_refine``); it rides on the run summary as ``ripple_refined``.

ONE rule for every reader (the card, the report, the datasheet): the refined
ripple when there is one, else the run's own ripple, flagged NOT ACCURATE when
its ring check failed.  Pure: no I/O.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional

#: What a table prints next to a ripple the ring check did not pass.
NOT_ACCURATE_MARK = "not accurate"


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def quoted_ripple(summary: Optional[Mapping[str, Any]],
                  fallback: Any = None) -> Dict[str, Any]:
    """``{value, accurate, refined, layers, ring_error}`` for a run summary.

    ``value``     the ripple to quote [%] (refined when present), or None;
    ``accurate``  True / False / None (unknown: no ring check on record);
    ``refined``   True when ``value`` is the refined ripple;
    ``layers``    gap layers/side the quoted value was solved at, if known;
    ``ring_error`` the ring check of the quoted value, as a fraction of the
                  ripple scale.
    ``fallback`` is the value used when the summary carries no ripple at all
    (a catalogue ``result.ripple_pct``)."""
    s = summary if isinstance(summary, Mapping) else {}
    ref = s.get("ripple_refined")
    if isinstance(ref, Mapping) and _f(ref.get("T_ripple_pct")) is not None:
        return {"value": _f(ref.get("T_ripple_pct")),
                "accurate": bool(ref.get("passed")),
                "refined": True,
                "layers": _f(ref.get("gap_layers_per_side")),
                "ring_error": _f(ref.get("self_check_rel_to_ripple_scale"))}
    val = _f(s.get("T_ripple_pct"))
    if val is None:
        val = _f(fallback)
    lim = s.get("ripple_mesh_limited")
    return {"value": val,
            "accurate": None if lim is None else not bool(lim),
            "refined": False,
            "layers": _f(s.get("gap_layers_effective")),
            "ring_error": _f(s.get("ripple_self_check_rel"))}


def ripple_text(summary: Optional[Mapping[str, Any]], fmt, fallback: Any = None) -> str:
    """The table cell: the quoted value through ``fmt``, followed by
    ``NOT_ACCURATE_MARK`` when the ring check of that value failed."""
    q = quoted_ripple(summary, fallback)
    txt = fmt(q["value"])
    if q["value"] is not None and q["accurate"] is False:
        return f"{txt} ({NOT_ACCURATE_MARK})"
    return txt


def ripple_note(summary: Optional[Mapping[str, Any]]) -> str:
    """One clause for a table's note column: where the quoted value comes from."""
    q = quoted_ripple(summary)
    if q["value"] is None:
        return ""
    lay = f"{q['layers']:g}" if q["layers"] is not None else "?"
    if q["refined"]:
        return f"refined at {lay} air-gap layers/side"
    if q["accurate"] is False:
        err = (f"{100.0 * q['ring_error']:.0f} %" if q["ring_error"] is not None
               else "above 5 %")
        return (f"ring error {err} of the ripple scale at {lay} layer(s)/side; "
                "needs >= 3 layers (Refine ripple)")
    return ""
