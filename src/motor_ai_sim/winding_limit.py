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

A SEPARATE PEAK LIMIT (owner 2026-10-27, e.g. 200 °C while the continuous limit is 180 °C)::

    limits:
      winding_c: 180
      winding_peak_c: 200

The peak limit is what a SHORT, TRANSIENT pull is judged against - the S2 time-to-limit of a
duty and the "peak duration" requirement; every CONTINUOUS verdict (S1 search, continuous /
rated rating, steady-state warnings, Configure's continuous tiles) keeps ``winding_c``.  A
configuration that states no ``winding_peak_c`` has its peak limit equal to its ``winding_c``
(and, with neither, the project defaults), so nothing changes for it.

The solver paths (the coupled loop's S1 search, time-to-limit, the duty cycle)
pass the limit of the configuration they solve explicitly (:func:`for_solve`); no
path reads the editor's context implicitly.  Never raises: a limit nobody stated
is ``None``.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

#: Where the setting lives in a configuration yaml.
LIMITS_KEY = "limits"
WINDING_KEY = "winding_c"
WINDING_PEAK_KEY = "winding_peak_c"
#: A sane window for a typed winding limit [°C].
MIN_C, MAX_C = 40.0, 260.0


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _stated(cfg_doc: Optional[Mapping[str, Any]], key: str) -> Optional[float]:
    """``limits.<key>`` of a configuration document inside the sane window, else ``None``."""
    if not isinstance(cfg_doc, Mapping):
        return None
    lim = cfg_doc.get(LIMITS_KEY)
    v = _num(lim.get(key)) if isinstance(lim, Mapping) else None
    if v is None or not (MIN_C <= v <= MAX_C):
        return None
    return v


def config_winding_limit_c(cfg_doc: Optional[Mapping[str, Any]]) -> Optional[float]:
    """The configuration's own (continuous) winding limit [°C], or ``None`` when it states none."""
    return _stated(cfg_doc, WINDING_KEY)


def config_winding_peak_limit_c(cfg_doc: Optional[Mapping[str, Any]]) -> Optional[float]:
    """The winding limit for a short PEAK [°C]: ``limits.winding_peak_c`` when the configuration states it, else its
    ``winding_c``, else ``None`` (the project defaults).  Never raises."""
    v = _stated(cfg_doc, WINDING_PEAK_KEY)
    return v if v is not None else _stated(cfg_doc, WINDING_KEY)


#: (die, cfg) -> ((file mtime_ns, size), (continuous limit, peak limit)) - the yaml is read once per change of the file
_CACHE: Dict[Tuple[str, str], Tuple[Tuple[int, int], Tuple[Optional[float], Optional[float]]]] = {}


def limits_for_config(die: Any, cfg: Any) -> Tuple[Optional[float], Optional[float]]:
    """``(continuous limit, peak limit)`` of the catalogued configuration ``die/cfg``; the peak limit already falls
    back to the continuous one.  The yaml read is cached on the file's stamp.  ``(None, None)`` when the configuration
    states none or cannot be read; never raises."""
    die, cfg = str(die or "").strip(), str(cfg or "").strip()
    if not die or not cfg:
        return None, None
    try:
        from motor_ai_sim.routes import family as _fam
        try:
            st = _fam._cfg_file(die, cfg).stat()
            stamp = (int(st.st_mtime_ns), int(st.st_size))
        except Exception:                                    # noqa: BLE001 - no file stamp: read, do not cache
            stamp = None
        hit = _CACHE.get((die, cfg))
        if stamp is not None and hit is not None and hit[0] == stamp:
            return hit[1]
        doc = _fam.config_doc(die, cfg)
        lim = (config_winding_limit_c(doc), config_winding_peak_limit_c(doc))
        if stamp is not None:
            _CACHE[(die, cfg)] = (stamp, lim)
        return lim
    except Exception:                                        # noqa: BLE001
        return None, None


def for_config(die: Any, cfg: Any) -> Optional[float]:
    """The (continuous) winding limit of the catalogued configuration ``die/cfg`` - the configuration a solve is FOR.
    ``None`` when it states none or cannot be read."""
    return limits_for_config(die, cfg)[0]


def peak_for_config(die: Any, cfg: Any) -> Optional[float]:
    """The PEAK winding limit of ``die/cfg`` (``limits.winding_peak_c``, else its ``winding_c``); ``None`` when it
    states neither or cannot be read."""
    return limits_for_config(die, cfg)[1]


def solved_config(body: Optional[Mapping[str, Any]] = None) -> Tuple[str, str]:
    """``(die, cfg)`` a solve is FOR: the request's own ``die`` / ``config`` when it names them, else the catalogue
    context the solve itself is filed under (``duty_results.active_context`` - the same rule the coupled loop
    files its record by).  ``("", "")`` off the catalogue."""
    b = body if isinstance(body, Mapping) else {}
    die, cfg = str(b.get("die") or "").strip(), str(b.get("config") or "").strip()
    if die and cfg:
        return die, cfg
    try:
        from motor_ai_sim.duty_results import active_context
        ctx = active_context()
        return (str(ctx[0]), str(ctx[1])) if ctx else ("", "")
    except Exception:                                        # noqa: BLE001
        return "", ""


def for_solve(body: Optional[Mapping[str, Any]] = None) -> Optional[float]:
    """The winding limit of the configuration a solve is for (:func:`solved_config`); the S1 search, the S2 /
    duty-cycle and the time-to-limit calls pass this explicitly."""
    return for_config(*solved_config(body))


def peak_for_solve(body: Optional[Mapping[str, Any]] = None) -> Optional[float]:
    """The PEAK winding limit of the configuration a solve is for - what the S2 time-to-limit and the short pull of a
    duty cycle are judged against; falls back to :func:`for_solve`'s number."""
    return peak_for_config(*solved_config(body))


def active_config_winding_limit_c() -> Optional[float]:
    """The winding limit of the configuration the EDITOR has loaded - for the interactive panel only; no solver path
    reads it implicitly."""
    try:
        from motor_ai_sim.duty_results import active_context
        ctx = active_context()
        return for_config(ctx[0], ctx[1]) if ctx else None
    except Exception:                                        # noqa: BLE001 - no context is the ordinary case
        return None


def resolve(cfg_doc: Optional[Mapping[str, Any]] = None, *, limit_c: Any = None) -> Optional[float]:
    """An explicitly passed limit first, then the handed-in configuration document's own.  NEVER the editor's
    context: a caller that wants the solved configuration's limit passes it (:func:`for_solve`)."""
    v = _num(limit_c)
    if v is not None:
        return v
    return config_winding_limit_c(cfg_doc)


def basis_words(limit_c: float, peak: bool = False) -> str:
    """How the report and every verdict name the configured limit; ``peak`` names the limit of a short peak."""
    return "the project's %swinding limit (%g °C)" % ("peak " if peak else "", float(limit_c))


def validate(value: Any, what: str = "winding limit") -> Tuple[Optional[float], Optional[str]]:
    """``(value, None)`` for a usable limit or a clear (``None``), ``(None, why)`` otherwise."""
    if value is None:
        return None, None
    v = _num(value)
    if v is None or not (MIN_C <= v <= MAX_C):
        return None, "%s must be a number between %g and %g °C; got %r" % (what, MIN_C, MAX_C, value)
    return v, None
