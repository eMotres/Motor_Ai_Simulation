"""The "Published motors" knowledge block of the in-app assistant (owner 2026-10-19).

The assistant used to know the UI and nothing else, so "which 40 mm motors do you
have and what is their continuous torque?" got a shrug.  This module renders the
answer from the LIVE catalog at call time - nothing is typed in, so it cannot go
stale - and appends it to the system prompt of ``routes/support.py``.

WHO SEES WHAT (the hard rule).  The block is built from the same tree the Motors
tab reads (``routes.family.tree_for_access``) and the same access answer
(``motor_access.catalog_access``), so a caller reads exactly the motors they may
open and a private die / configuration never appears for someone without access;
an admin reads all.  A VISITOR has no account, but sign-up is automatic and
instant, so a visitor is told what a FRESH sign-up with no grants could open at
once (the same tree for ``granted`` access with no dies and no configurations) -
never more: nothing a person would have to be granted can reach an anonymous
chat.

WHAT IS IN IT (a datasheet's facts, taken from explicit whitelisted fields, never
a free-text card or a duty note): name, stator diameter, slots/poles, stack,
magnet and steel, the RATED point (the continuous one - ``family.config_rated_duty``,
a short-time duty is never the rating when a continuous one exists) with rpm, A,
N.m, kW, efficiency, limiting part and cooling, the peak / short-time duties with
their time, KV, Kt, mass, battery, application and allowed propellers, whether a
3-D model exists, and that the 40 mm motor is in production.  NEVER a cost, a
supplier, stock, an internal note or anything about an unpublished patent.

Cached a few minutes per access shape; the tree underneath has its own memo.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

#: Stator diameters (mm) of the machines that are in production (owner 2026-10-19:
#: the 40 mm motor).  A fact about the product line, not a per-card field.
IN_PRODUCTION_DIAMETERS_MM = (40.0,)

#: How long a rendered block is reused (seconds).
CACHE_TTL_S = 300.0
_CACHE_MAX = 64

#: At most this many propeller ids are listed per configuration.
_MAX_PROPELLERS = 8
#: At most this many extra (non-rated, non-peak) duties are listed.
_MAX_EXTRA_DUTIES = 2

HEADER = (
    "## Published motors — the catalog, as THIS caller may open it\n"
    "These are the catalog values (saved 2-D FEM results at the stated cooling). For exact live numbers "
    "the datasheet (**⭳ datasheet** on the Motors tab) and Configure are the source — say so when asked "
    "for a precise figure, and never present a number as a guarantee.\n"
    "Answer motor questions ONLY from this list. A motor that is not listed here is not open to this "
    "caller: do not name, hint at or confirm any other motor, and never quote a price, stock, supplier "
    "or anything internal — none of that is in the list.")

_EMPTY_SIGNED_IN = (
    "No motor is open to this account yet. Do not describe or guess any motor; say so plainly and offer "
    "to pass the request to the team as a ticket.")
_EMPTY_VISITOR = (
    "No motor is open to a new account right away. Do not describe or guess any motor; say that the team "
    "opens motors to accounts, and point to sign-up on aerostator.com and support@aerostator.com.")


# ── small formatting helpers ────────────────────────────────────────────────

_CTRL = re.compile(r"[\x00-\x1f\x7f]+")


def _clean(s: Any, limit: int = 160) -> str:
    """A machine-written label made safe for a prompt: one line, bounded."""
    t = _CTRL.sub(" ", str(s if s is not None else "")).strip()
    t = re.sub(r"\s{2,}", " ", t)
    return t[:limit]


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and x not in (float("inf"), float("-inf")) else None


def _n(v: Any, nd: int = 2) -> Optional[str]:
    """``12.50`` -> ``12.5``, ``41.0`` -> ``41``; None for no number."""
    x = _f(v)
    if x is None:
        return None
    s = f"{x:.{nd}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def _join(parts: List[Optional[str]], sep: str = ", ") -> str:
    return sep.join(p for p in parts if p)


def _point(duty: Dict[str, Any]) -> str:
    """``13000 rpm, 41 A rms, 0.55 N·m, 0.74 kW`` of one duty."""
    rpm, cur = _n(duty.get("rpm"), 0), _n(duty.get("current_arms"), 1)
    t, p = _n(abs(_f(duty.get("torque_nm")) or 0) or None, 2), _n(duty.get("power_kw"), 2)
    return _join([f"{rpm} rpm" if rpm else None, f"{cur} A rms" if cur else None,
                  f"{t} N·m" if t else None, f"{p} kW" if p else None])


def _is_peak(name: Any) -> bool:
    return "peak" in str(name or "").lower()


# ── one configuration ───────────────────────────────────────────────────────

def _pick_rated(c: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    duties = [d for d in (c.get("duties") or []) if isinstance(d, dict)]
    if not duties:
        return None
    want = c.get("default_duty")
    return next((d for d in duties if d.get("name") == want), duties[0])


def _is_continuous(d: Dict[str, Any]) -> bool:
    """Does the duty hold for ever?  The S1 block is the evidence; the stored class
    is the fallback (the same order ``duty_class`` documents)."""
    cr = d.get("continuous_rating") if isinstance(d.get("continuous_rating"), dict) else {}
    i_cont, cur = _f(cr.get("i_cont_A")), _f(d.get("current_arms"))
    if cr.get("feasible") and i_cont and cur and i_cont >= 0.98 * cur:
        return True
    return str(d.get("duty_class") or "") == "continuous"


def _rated_lines(d: Dict[str, Any]) -> List[str]:
    r = d.get("result") if isinstance(d.get("result"), dict) else {}
    cr = d.get("continuous_rating") if isinstance(d.get("continuous_rating"), dict) else {}
    st = d.get("short_time") if isinstance(d.get("short_time"), dict) else {}
    eff = _n(r.get("efficiency_pct"), 1)
    head = _point(d) + (f", efficiency {eff} %" if eff else "")
    ttl = d.get("time_to_limit") if isinstance(d.get("time_to_limit"), dict) else {}
    if _is_continuous(d):
        lim = _clean(cr.get("part"), 40)
        lim_c = _n(cr.get("limit_c"), 0)
        cool = _clean(cr.get("cooling_label") or ttl.get("cooling_words"))
        tail = _join([f"limited by the {lim}" + (f" ({lim_c} °C)" if lim_c else "") if lim else None,
                      f"cooling: {cool}" if cool else None], "; ")
        return [f"Rated (continuous): {head}" + (f" — {tail}" if tail else "")]
    if str(d.get("duty_class") or "") != "short_time":
        # Nothing says either way (never solved coupled): quote the point, claim nothing about the hours.
        out = [f"Rated point (a continuous rating is not established for it): {head}"]
    else:
        # The saved rated point cannot be held for ever: say so, never pass it off as the rating.
        words = _clean(st.get("words") or d.get("duty_label"))
        out = [f"Rated point (short-time only, NOT a continuous rating): {head}"
               + (f" — {words}" if words else "")]
    i_cont, t_cont = _n(cr.get("i_cont_A"), 1), _n(cr.get("torque_Nm"), 2)
    if cr.get("feasible") and i_cont:
        out.append(f"Continuous (S1) at the saved cooling: {i_cont} A rms"
                   + (f", about {t_cont} N·m" if t_cont else "")
                   + (f", limited by the {_clean(cr.get('part'), 40)}" if cr.get("part") else ""))
    return out


def _other_duties(c: Dict[str, Any], rated: Dict[str, Any]) -> List[str]:
    """Peak first, then the other short-time duties, each with its time."""
    others = [d for d in (c.get("duties") or [])
              if isinstance(d, dict) and d is not rated and d.get("name") != rated.get("name")]
    others.sort(key=lambda d: (0 if _is_peak(d.get("name")) else 1))
    out = []
    for d in others[:_MAX_EXTRA_DUTIES]:
        st = d.get("short_time") if isinstance(d.get("short_time"), dict) else {}
        r = d.get("result") if isinstance(d.get("result"), dict) else {}
        eff = _n(r.get("efficiency_pct"), 1)
        label = "Peak" if _is_peak(d.get("name")) else f"Duty \"{_clean(d.get('name'), 40)}\""
        words = _clean(st.get("words")) if st else ""
        out.append(f"{label}: {_point(d)}" + (f", efficiency {eff} %" if eff else "")
                   + (f" — {words}" if words else ""))
    return out


def _kv_kt(rated: Dict[str, Any]) -> Optional[str]:
    r = rated.get("result") if isinstance(rated.get("result"), dict) else {}
    kv = _n(r.get("kv_rpm_per_v"), 1)
    t, i = _f(rated.get("torque_nm")), _f(rated.get("current_arms"))
    kt = _n(abs(t) / i, 3) if (t and i) else None
    return _join([f"KV {kv} rpm/V" if kv else None,
                  f"Kt {kt} N·m/A rms (T/I at the rated point)" if kt else None], ", ") or None


def _mass(c: Dict[str, Any], rated: Dict[str, Any]) -> Optional[str]:
    m = c.get("mass") if isinstance(c.get("mass"), dict) else {}
    r = rated.get("result") if isinstance(rated.get("result"), dict) else {}
    kg = _n(m.get("full_kg"), 3) or _n(r.get("mass_kg"), 3)
    if not kg:
        return None
    partial = bool(m.get("partial", r.get("mass_partial", False)))
    return f"mass {kg} kg" + (" (active parts only, the full assembly is not built yet)" if partial else "")


def _battery(c: Dict[str, Any]) -> Optional[str]:
    b = c.get("battery") if isinstance(c.get("battery"), dict) else {}
    cells, v_nom = b.get("cells"), _n(b.get("v_nom"), 1)
    if not (cells or v_nom):
        return None
    lo, hi = _n(b.get("v_min"), 1), _n(b.get("v_max"), 1)
    return ("battery " + _join([f"{cells}S" if cells else None, _clean(b.get("chemistry"), 12) or None,
                                f"{v_nom} V nominal" if v_nom else None], " ")
            + (f" ({lo}–{hi} V)" if lo and hi else ""))


_APP_WORDS = {"propeller": "propeller (drone) motor, cooled by its propeller's slipstream",
              "robot": "robot-joint motor (still-air cooling)",
              "aviation": "aviation motor", "marine": "marine motor"}


def _application(c: Dict[str, Any]) -> Optional[str]:
    apps = [a for a in (c.get("applications") or []) if isinstance(a, str)]
    return ", ".join(_APP_WORDS.get(a, _clean(a, 30)) for a in apps) or None


def _model_text(model: Optional[Dict[str, Any]]) -> str:
    if not model or not model.get("available"):
        return "3-D model: not built yet"
    bits = ["viewable after sign-up"]
    if model.get("simplified_available"):
        bits.append("simplified STEP downloadable without an NDA")
    if model.get("step_available"):
        bits.append("full STEP after the online NDA")
    return "3-D model: " + ", ".join(bits)


def _propellers_text(cool: Optional[Dict[str, Any]]) -> Optional[str]:
    props = [str(p) for p in ((cool or {}).get("propellers") or []) if str(p).strip()]
    if not props:
        return None
    shown = ", ".join(props[:_MAX_PROPELLERS])
    more = len(props) - _MAX_PROPELLERS
    return "allowed propellers: " + shown + (f" (+{more} more)" if more > 0 else "")


def render_config(die: Dict[str, Any], c: Dict[str, Any], *,
                  cooling: Optional[Dict[str, Any]] = None,
                  model: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """The few lines for one configuration, or None when it has no duty to quote."""
    rated = _pick_rated(c)
    if rated is None:
        return None
    dia = _f(die.get("stator_diameter"))
    slots, poles = die.get("slots"), die.get("poles")
    title = f"{_clean(die.get('name'), 80)} / {_clean(c.get('name'), 40)}"
    geom = _join([f"stator Ø{_n(dia, 1)} mm" if dia else None,
                  f"{slots} slots / {poles} poles" if slots and poles else None,
                  f"stack {_n(c.get('stack_mm'), 1)} mm" if _n(c.get("stack_mm"), 1) else None,
                  f"magnet {_clean(c.get('magnet'), 40)}" if c.get("magnet") else None,
                  f"steel {_clean(c.get('steel'), 40)}" if c.get("steel") else None])
    role = str(c.get("role") or "")
    lines = [f"- **{title}** — {geom}" + (f"; {role}" if role in ("generator", "mixed") else "")
             + ("; IN PRODUCTION" if dia is not None and any(
                 abs(dia - d) < 0.5 for d in IN_PRODUCTION_DIAMETERS_MM) else "")]
    for ln in _rated_lines(rated) + _other_duties(c, rated):
        lines.append("  " + ln)
    extra = _join([_kv_kt(rated), _mass(c, rated), _battery(c)], "; ")
    if extra:
        lines.append("  " + extra)
    use = _join([f"application: {_application(c)}" if _application(c) else None,
                 _propellers_text(cooling), _model_text(model)], "; ")
    lines.append("  " + use)
    return "\n".join(lines)


# ── the whole block ─────────────────────────────────────────────────────────

def _shared(dies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Only the vendor's catalog: the caller's own workspace copies and the
    community layer are not "published motors"."""
    return [d for d in dies if d.get("layer") in (None, "shared")]


def render_block(dies: List[Dict[str, Any]], *, visitor: bool = False,
                 cooling_of: Optional[Callable[[str, str], Optional[Dict[str, Any]]]] = None,
                 model_of: Optional[Callable[[str, str], Optional[Dict[str, Any]]]] = None) -> str:
    """The block for a tree already filtered to the caller's access (pure)."""
    parts: List[str] = []
    for d in sorted(_shared(dies), key=lambda x: (_f(x.get("stator_diameter")) or 0, str(x.get("name")))):
        for c in d.get("configs") or []:
            # Same rule as the Motors tab for clients: a configuration without a catalogue
            # card has no datasheet yet and is not shown.
            if not c.get("card_date"):
                continue
            name, cfg = str(d.get("name") or ""), str(c.get("name") or "")
            try:
                cool = cooling_of(name, cfg) if cooling_of else None
                mdl = model_of(name, cfg) if model_of else None
                text = render_config(d, c, cooling=cool, model=mdl)
            except Exception as e:                           # noqa: BLE001 - one bad card never blanks the rest
                logger.warning("support catalog: %s / %s skipped (%s: %s)", name, cfg, type(e).__name__, e)
                continue
            if text:
                parts.append(text)
    if not parts:
        return HEADER + "\n\n" + (_EMPTY_VISITOR if visitor else _EMPTY_SIGNED_IN)
    return HEADER + "\n\n" + "\n".join(parts)


# ── live data ───────────────────────────────────────────────────────────────

def _cooling_of(die: str, cfg: str) -> Optional[Dict[str, Any]]:
    from motor_ai_sim.cooling_options import cooling_options
    return cooling_options(die, cfg)


def _models_index() -> Dict[tuple, Dict[str, Any]]:
    from motor_ai_sim import model3d
    return {(e["die"], e["config"]): model3d.model_info(e) for e in model3d.scan()}


def _visitor_access() -> dict:
    """What a brand-new account (no grants at all) is allowed to open."""
    from motor_ai_sim import motor_access as MA
    mode = MA.MODE_ALL if MA.grant_all_registered() else MA.MODE_GRANTED
    return {"mode": mode, "dies": frozenset(), "email": None, "is_admin": False,
            "configs": frozenset()}


def _access_key(a: dict, visitor: bool) -> tuple:
    return ("visitor" if visitor else "user", str(a.get("mode")), bool(a.get("is_admin")),
            tuple(sorted(str(x) for x in (a.get("dies") or ()))),
            tuple(sorted(str(x) for x in (a.get("configs") or ()))),
            a.get("email") if not visitor else None)


_lock = threading.Lock()
_cache: Dict[tuple, tuple] = {}


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def published_motors_block(authorization: Optional[str] = None, *, visitor: bool = False,
                           now: Optional[float] = None) -> str:
    """The block for one caller.  ``visitor`` = no account (the landing-page widget).

    Never raises: a failure here must not cost the user their answer, so it logs and
    returns "" (the assistant then simply has no motor knowledge for that call).
    """
    try:
        from fastapi import Response
        from motor_ai_sim import motor_access as MA
        from motor_ai_sim.routes import family as fam
        acc = _visitor_access() if visitor else MA.catalog_access(authorization)
        key = _access_key(acc, visitor)
        t = time.monotonic() if now is None else now
        with _lock:
            hit = _cache.get(key)
        if hit is not None and t - hit[0] < CACHE_TTL_S:
            return hit[1]
        dies = list((fam.tree_for_access(Response(), acc) or {}).get("dies") or [])
        try:
            models = _models_index()
        except Exception:                                    # noqa: BLE001
            models = {}
        text = render_block(dies, visitor=visitor, cooling_of=_cooling_of,
                            model_of=lambda d, c: models.get((d, c)))
        with _lock:
            if len(_cache) >= _CACHE_MAX:
                _cache.clear()
            _cache[key] = (t, text)
        return text
    except Exception as e:                                   # noqa: BLE001
        logger.warning("support catalog: block unavailable (%s: %s)", type(e).__name__, e)
        return ""
