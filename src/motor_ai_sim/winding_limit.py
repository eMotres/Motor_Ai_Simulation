"""The winding temperature limit of ONE configuration (owner 2026-10-26).

«one winding temperature limit for every verdict of a project (e.g. 180 °C, not the
assumed class-N 200 °C)».  A configuration may state its own limit in its yaml::

    limits:
      winding_c: 180

and then EVERY path that judges the winding reads that number: the report's
limits and warnings, the S1 / continuous-rating search, the S2 time-to-limit,
the duty-cycle limits and Configure's tiles.  A configuration that states none
keeps the project defaults exactly as before (insulation class N 200 °C for the
overheat verdicts, the application's rated temperature for S1).

The paths that run on the LIVE machine (the coupled loop, the duty cycle) have
no configuration document in hand; they ask :func:`active_config_winding_limit_c`,
which reads the catalogued configuration the editor's context names.  Never
raises: a limit nobody stated is ``None``.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Optional, Tuple

#: Where the setting lives in a configuration yaml.
LIMITS_KEY = "limits"
WINDING_KEY = "winding_c"
#: A sane window for a typed winding limit [°C].
MIN_C, MAX_C = 40.0, 260.0


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def config_winding_limit_c(cfg_doc: Optional[Mapping[str, Any]]) -> Optional[float]:
    """The configuration's own winding limit [°C], or ``None`` when it states none."""
    if not isinstance(cfg_doc, Mapping):
        return None
    lim = cfg_doc.get(LIMITS_KEY)
    v = _num(lim.get(WINDING_KEY)) if isinstance(lim, Mapping) else None
    if v is None or not (MIN_C <= v <= MAX_C):
        return None
    return v


def active_config_winding_limit_c() -> Optional[float]:
    """The winding limit of the catalogued configuration the editor has loaded, or ``None``."""
    try:
        from motor_ai_sim.duty_results import active_context
        from motor_ai_sim.routes import family as _fam
        ctx = active_context()
        if not ctx:
            return None
        return config_winding_limit_c(_fam.config_doc(str(ctx[0]), str(ctx[1])))
    except Exception:                                        # noqa: BLE001 - no context is the ordinary case
        return None


def resolve(cfg_doc: Optional[Mapping[str, Any]] = None, *, use_active: bool = True
            ) -> Optional[float]:
    """The configuration's limit: the document's own when one is handed in, else the live context's."""
    if cfg_doc is not None:
        return config_winding_limit_c(cfg_doc)
    return active_config_winding_limit_c() if use_active else None


def basis_words(limit_c: float) -> str:
    """How the report and every verdict name the configured limit."""
    return "the project's winding limit (%g °C)" % float(limit_c)


def validate(value: Any) -> Tuple[Optional[float], Optional[str]]:
    """``(value, None)`` for a usable limit or a clear (``None``), ``(None, why)`` otherwise."""
    if value is None:
        return None, None
    v = _num(value)
    if v is None or not (MIN_C <= v <= MAX_C):
        return None, "winding limit must be a number between %g and %g °C; got %r" % (MIN_C, MAX_C, value)
    return v, None
