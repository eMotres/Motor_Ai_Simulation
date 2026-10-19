"""SHORT-TIME OR CONTINUOUS — what kind of duty a saved operating point is.

Owner, 2026-10-19 (CIANO28 150_35 new / L35 "rated": winding 200 °C 6 min 11 s
from cold, no S1 rating at its speed): *«помечай как кратковременный»* — a duty
the machine cannot hold for ever is marked SHORT-TIME everywhere it is named,
with the time it may run, and the catalog card and the datasheet's rated
column use a CONTINUOUS duty when the configuration has one.

The class is READ off the duty's stored coupled record, never computed here:

* ``short_time`` — the record's ``time_to_limit`` says the point is over a
  limit (``within_limits: false``) and states how long it runs from cold.
  That covers the owner's case (no S1 at the duty's speed at all) and every
  point whose S1 current is below its own: either way the duty is a pull, not
  a rating.
* ``continuous`` — the point is inside every limit (``within_limits: true``),
  or its S1 rating holds at least the duty's own current, or the duty was
  SAVED as a continuous point (``duty_class: continuous`` on the yaml entry).
* ``None`` — nothing says (never solved coupled, or an older record).

Pure: no I/O, no FastAPI.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional

SHORT_TIME = "short_time"
CONTINUOUS = "continuous"

#: The name the old duty is given when a continuous point takes over its name.
SHORT_TIME_SUFFIX = "_short_time"


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def fmt_duration(seconds: Optional[float]) -> str:
    """``"6 min 11 s"``, ``"45 s"``, ``"2 h 05 min"`` — the label's own words."""
    s = _num(seconds)
    if s is None or s < 0.0:
        return "—"
    s = int(round(s))
    if s < 60:
        return "%d s" % s
    if s < 3600:
        return "%d min %02d s" % (s // 60, s % 60)
    return "%d h %02d min" % (s // 3600, (s % 3600) // 60)


def short_time_info(coupled: Optional[Mapping[str, Any]]
                    ) -> Optional[Dict[str, Any]]:
    """``{time_to_limit_s, time_from_rated_s, part, limit_c}`` when the stored
    coupled record says this point runs out, else ``None``."""
    c = dict(coupled or {})
    if c.get("duty_class") == SHORT_TIME and _num(c.get("time_to_limit_s")):
        return {"time_to_limit_s": _num(c.get("time_to_limit_s")),
                "time_from_rated_s": _num(c.get("time_from_rated_s")),
                "part": c.get("short_time_part"),
                "limit_c": _num(c.get("short_time_limit_c"))}
    t = c.get("time_to_limit")
    if not isinstance(t, Mapping) or t.get("within_limits", True):
        return None
    starts = t.get("starts") if isinstance(t.get("starts"), Mapping) else {}
    cold = starts.get("cold") if isinstance(starts.get("cold"), Mapping) else {}
    rated = starts.get("rated") if isinstance(starts.get("rated"), Mapping) else {}
    t_cold = _num(cold.get("time_to_limit_s"))
    if t_cold is None:
        return None
    part = str(cold.get("limiting_part") or t.get("limiting_part") or "") or None
    return {"time_to_limit_s": t_cold,
            "time_from_rated_s": _num(rated.get("time_to_limit_s")),
            "part": part,
            "limit_c": _num((t.get("limits_c") or {}).get(part)) if part else None}


def _holds_its_current(coupled: Mapping[str, Any]) -> bool:
    t = coupled.get("time_to_limit")
    if isinstance(t, Mapping) and t.get("within_limits") is True:
        return True
    cr = coupled.get("continuous_rating")
    if not isinstance(cr, Mapping) or not cr:
        return False
    if not (cr.get("ok", True) and cr.get("feasible") is True
            and cr.get("trustworthy", True) and cr.get("verified") is not False):
        return False
    s = _num(cr.get("s"))
    return s is not None and s >= 0.999


def classify(entry: Optional[Mapping[str, Any]],
             coupled: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """``{"duty_class": …, …}`` for one duty — its yaml ``entry`` and its
    stored coupled record — or ``None`` when nothing says.

    A stored record that says SHORT-TIME wins over a yaml flag: a point saved
    as continuous that a later run found over its limit is short-time now, and
    saying otherwise would be the stale verdict the owner asked to get rid of.
    """
    c = dict(coupled or {})
    st = short_time_info(c)
    if st:
        return {"duty_class": SHORT_TIME, **st}
    if c and _holds_its_current(c):
        return {"duty_class": CONTINUOUS, "basis": "coupled record"}
    if str((entry or {}).get("duty_class") or "") == CONTINUOUS:
        return {"duty_class": CONTINUOUS,
                "basis": "saved as a continuous point"}
    return None


def short_time_words(info: Optional[Mapping[str, Any]]) -> str:
    """``"short-time: 6 min 11 s from cold (winding 200 °C)"`` or ``""``."""
    i = dict(info or {})
    if i.get("duty_class") not in (None, SHORT_TIME) \
            or _num(i.get("time_to_limit_s")) is None:
        return ""
    part = i.get("part")
    lim = _num(i.get("limit_c"))
    tail = ""
    if part:
        tail = " (%s%s)" % (part, "" if lim is None else " %g °C" % lim)
    return "short-time: %s from cold%s" % (fmt_duration(i["time_to_limit_s"]),
                                           tail)


def label(name: str, info: Optional[Mapping[str, Any]]) -> str:
    """The duty as every page names it: ``"rated — short-time: 6 min 11 s
    from cold (winding 200 °C)"`` for a short-time duty, the bare name
    otherwise."""
    words = short_time_words(info)
    return "%s — %s" % (name, words) if words else str(name)


def coupled_fields(coupled: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """The flat fields a COMPACTED coupled record carries (``duty_results.
    compact_coupled``), so the catalog and the datasheet read one key instead
    of re-deriving it: ``duty_class`` and, when short-time, the time and the
    part.  ``{}`` when nothing says."""
    cls = classify(None, coupled)
    if not cls:
        return {}
    out: Dict[str, Any] = {"duty_class": cls["duty_class"]}
    if cls["duty_class"] == SHORT_TIME:
        out.update({"time_to_limit_s": cls.get("time_to_limit_s"),
                    "time_from_rated_s": cls.get("time_from_rated_s"),
                    "short_time_part": cls.get("part"),
                    "short_time_limit_c": cls.get("limit_c")})
    return out


def prefer_continuous(default_name: Optional[str],
                      classes: Mapping[str, Optional[Mapping[str, Any]]],
                      names: Any) -> Optional[str]:
    """The duty a catalog card / a rated column shows: ``default_name``,
    unless it is SHORT-TIME and the configuration has a CONTINUOUS duty — then
    that one (a duty named ``rated…`` first, else the first continuous)."""
    if not default_name:
        return default_name
    own = (classes or {}).get(default_name) or {}
    if own.get("duty_class") != SHORT_TIME:
        return default_name
    cont = [str(n) for n in (names or ())
            if ((classes or {}).get(str(n)) or {}).get("duty_class") == CONTINUOUS]
    if not cont:
        return default_name
    rated = [n for n in cont if n.lower().startswith("rated")]
    return rated[0] if rated else cont[0]
