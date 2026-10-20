"""The public motor preview of the landing page (``GET /api/public/motors``).

A visitor without an account sees, above the five-step diagram, one card per PUBLIC catalogue
motor with its datasheet-level headline numbers.  This module builds that list.

WHAT "PUBLIC" MEANS.  The same answer the support assistant gives a visitor
(``support_catalog``): the motors a FRESHLY REGISTERED account with no grants may open at once.
That is the one rule the catalogue already has for the unsigned audience - the die the owner
marked ``public`` in Admin, a configuration marked ``public``, and the shared baseline die every
registered account gets - read through ``motor_access`` and ``routes.family.tree_for_access``,
so a private die or configuration can never appear, and the list is never typed in here.
Only machines with a catalogue card (``card_date``) are listed, like the Motors tab for clients.

WHAT IS IN IT.  A STRICT ALLOW-LIST (``MOTOR_KEYS`` / ``RATED_KEYS``): the die and configuration
names, the application, and the RATED point's power, torque, speed, efficiency and the full motor
mass.  Each row is built key by key from those names; nothing is copied wholesale from a catalogue
document, so a field added to the tree tomorrow stays out until it is listed here on purpose.
NEVER geometry, winding, materials, bearings, drawings (``thumb_svg``), construction (CAD) keys,
costs, notes, owners or anything about a user.  Every answer is also run through the same
denylists the MCP uses (``mcp_tools.GEOMETRY_DENYLIST``) and the construction-privacy protected
keys, and the build raises rather than serve a leak.

CACHING.  The built list is kept 5 minutes per process (the tree underneath has its own memo); the
route adds ``Cache-Control: public, max-age=300``.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: How long a built list is reused (seconds).
CACHE_TTL_S = 300.0

#: The ONLY keys of one motor row, and of its ``rated`` block.
MOTOR_KEYS = ("die", "configuration", "application", "rated")
RATED_KEYS = ("power_kw", "torque_nm", "speed_rpm", "efficiency_pct", "mass_kg",
              "mass_is_partial", "continuous")

_SAFE_WORD = re.compile(r"[^A-Za-z0-9 _.\-]+")
#: What a catalogue die / configuration name can be (``routes.family._check_name``): a name outside it is
#: not a catalogue name, so the row is left out rather than served altered.
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.,()#+\-]{0,63}$")


def _num(v: Any, nd: int) -> Optional[float]:
    """A finite number rounded to ``nd`` decimals, else None (bool, text, NaN and inf are not numbers)."""
    if isinstance(v, bool) or v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x or x in (float("inf"), float("-inf")):
        return None
    return round(x, nd)


def _rpm(v: Any) -> Optional[int]:
    x = _num(v, 0)
    return int(x) if x is not None else None


def _word(v: Any, limit: int = 60) -> str:
    return _SAFE_WORD.sub("", str(v or "")).strip()[:limit]


def build_row(die: Dict[str, Any], c: Dict[str, Any],
              mass: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """The allow-listed row of one configuration, or None when it has no duty to quote (pure)."""
    from motor_ai_sim import support_catalog as SC
    rated = SC._pick_rated(c)
    if rated is None:
        return None
    r = rated.get("result") if isinstance(rated.get("result"), dict) else {}
    # THE motor mass is the FULL assembled mass; a machine whose full mass is incomplete says so
    # and gives the active mass (the same rule as the datasheet and the assistant).
    m = mass if isinstance(mass, dict) else {}
    full, active = _num(m.get("full_kg"), 3), _num(m.get("active_kg"), 3)
    partial = bool(m.get("partial", True)) if m else True
    if full is not None and not partial:
        kg: Optional[float] = full
    else:
        kg, partial = (active if active is not None else _num(r.get("mass_kg"), 3)), True
    torque = _num(rated.get("torque_nm"), 3)
    die_name, cfg_name = str(die.get("name") or ""), str(c.get("name") or "")
    if not (_NAME.match(die_name) and _NAME.match(cfg_name)):
        return None
    out = {
        "die": die_name,
        "configuration": cfg_name,
        "application": _word(c.get("application"), 24) or None,
        "rated": {
            "power_kw": _num(rated.get("power_kw"), 3),
            "torque_nm": abs(torque) if torque is not None else None,
            "speed_rpm": _rpm(rated.get("rpm")),
            "efficiency_pct": _num(r.get("efficiency_pct"), 1),
            "mass_kg": kg,
            "mass_is_partial": bool(partial),
            "continuous": bool(SC._is_continuous(rated)),
        },
    }
    return out


def build_list(dies: List[Dict[str, Any]],
               mass_of=None) -> List[Dict[str, Any]]:
    """Rows for a tree already filtered to the visitor's access (pure given ``mass_of``)."""
    from motor_ai_sim import support_catalog as SC
    rows: List[Dict[str, Any]] = []
    for d in sorted(SC._shared(dies), key=lambda x: (SC._f(x.get("stator_diameter")) or 0, str(x.get("name")))):
        for c in d.get("configs") or []:
            if not c.get("card_date"):          # no datasheet yet: not shown (the Motors tab's client rule)
                continue
            try:
                row = build_row(d, c, mass_of(d, c) if mass_of else None)
            except Exception as e:              # noqa: BLE001 - one bad card never blanks the rest
                logger.warning("public motors: %s / %s skipped (%s: %s)",
                               d.get("name"), c.get("name"), type(e).__name__, e)
                continue
            if row:
                rows.append(row)
    return rows


# -- the leak guard -----------------------------------------------------------------------------

def forbidden_keys() -> frozenset:
    """Every key that must never appear in the answer: the MCP's geometry denylist plus the protected
    construction keys of ``construction_privacy`` (and any other ``cad_*`` key)."""
    from motor_ai_sim import construction_privacy as CP
    from motor_ai_sim.mcp_tools import GEOMETRY_DENYLIST
    return frozenset(GEOMETRY_DENYLIST) | frozenset(CP.PROTECTED_KEYS) | frozenset(CP.CAD_KEYS)


def assert_clean(obj: Any, path: str = "$") -> None:
    """Raise when a key is outside the allow-list or on a denylist (recursively)."""
    bad = forbidden_keys()
    if isinstance(obj, dict):
        for k, v in obj.items():
            ks = str(k)
            if ks in bad or ks.startswith("cad_"):
                raise AssertionError(f"construction/geometry key leaked: {path}.{ks}")
            assert_clean(v, f"{path}.{ks}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            assert_clean(v, f"{path}[{i}]")


def assert_allowlisted(rows: List[Dict[str, Any]]) -> None:
    for row in rows:
        if set(row) != set(MOTOR_KEYS) or set(row["rated"]) != set(RATED_KEYS):
            raise AssertionError(f"public motor row outside the allow-list: {sorted(row)}")


# -- live data ----------------------------------------------------------------------------------

_lock = threading.Lock()
_cache: Dict[str, Any] = {"at": None, "rows": None}


def clear_cache() -> None:
    with _lock:
        _cache["at"], _cache["rows"] = None, None


def public_motors(now: Optional[float] = None) -> List[Dict[str, Any]]:
    """The public motors, cached for ``CACHE_TTL_S``.  A copy: the cache is never handed out."""
    t = time.monotonic() if now is None else now
    with _lock:
        if _cache["rows"] is not None and _cache["at"] is not None and t - _cache["at"] < CACHE_TTL_S:
            return [{**r, "rated": dict(r["rated"])} for r in _cache["rows"]]
    from contextlib import nullcontext
    from fastapi import Response
    from motor_ai_sim import support_catalog as SC
    from motor_ai_sim import workspace as WS
    from motor_ai_sim.routes import family as fam
    acc = SC._visitor_access()
    neutral = SC._neutral_workspace()
    with (WS.use_workspace(neutral) if neutral is not None else nullcontext()):
        dies = list((fam.tree_for_access(Response(), acc, with_mass=False) or {}).get("dies") or [])
        rows = build_list(dies, mass_of=SC._mass_of)
    assert_allowlisted(rows)
    assert_clean(rows)
    with _lock:
        _cache["at"], _cache["rows"] = t, rows
    return [{**r, "rated": dict(r["rated"])} for r in rows]
