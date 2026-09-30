"""The machine's Mesh settings — the ONLY source of mesh settings for a run.

Owner decision, 2026-09-30: gap layers, and every Mesh-tab setting, come from the
Mesh tab — the saved duty's ``mesh.*`` block, which activation syncs into the
machine's mesh config (``motor_config.yaml`` ``mesh:``, the file the Mesh tab
PATCHes and every consumer reads).  No path — Simulation, coupled loop,
optimizer, sweeps, passports, MCP / agent drafts — may substitute a hidden
default of its own.  A caller either passes the Mesh tab's value explicitly or
passes nothing (``None``) and gets the machine's saved setting from here.  Only
when the machine has NO saved value for a key is the labelled last-resort
fallback used, and the run says so (``mesh_settings_source`` /
``mesh_settings_note`` in the result and the summary).

A path with no Mesh context of its own (an agent-draft sandbox, a passport
generation) runs on a workspace whose config IS the base machine's — the draft
sandbox copies the base machine's mesh block and its duty's ``mesh.*`` into
it — so the same resolution yields the base machine's saved settings.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

#: The five Mesh-tab keys a run consumes, and their LAST-RESORT fallbacks —
#: used only when neither the request nor the machine's saved Mesh settings
#: carry the key (and then reported).  gap_layers is element rows PER SIDE of
#: the slip circle (owner 2026-09-30: 1 per side).
MESH_FALLBACK: Dict[str, float] = {
    "mesh_size_mm": 4.0,
    "min_size_mm": 0.3,
    "outer_air_factor": 1.3,
    "gap_layers": 1.0,
    "n_sectors": 4,
}

SOURCE_REQUEST = "request (Mesh tab)"
SOURCE_MACHINE = "machine Mesh settings"
SOURCE_FALLBACK = "fallback: the machine has no saved Mesh setting"


def _finite(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool) or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def machine_mesh_settings(cfg: Optional[Mapping[str, Any]] = None) -> Dict[str, float]:
    """The machine's saved Mesh settings (finite values of the five keys only)."""
    if cfg is None:
        try:
            from motor_ai_sim.config import get_config
            cfg = get_config()
        except Exception:     # noqa: BLE001 — no config = no saved settings
            cfg = {}
    m = (cfg or {}).get("mesh") or {}
    out: Dict[str, float] = {}
    for k in MESH_FALLBACK:
        f = _finite(m.get(k)) if isinstance(m, Mapping) else None
        if f is not None:
            out[k] = f
    return out


def resolve_mesh_settings(requested: Mapping[str, Any],
                          cfg: Optional[Mapping[str, Any]] = None
                          ) -> Tuple[Dict[str, float], Dict[str, str]]:
    """``(values, sources)`` for the five Mesh keys.

    A finite requested value wins (the Mesh tab sent it); otherwise the
    machine's saved setting; otherwise the labelled fallback.  ``n_sectors``
    comes back as an int.
    """
    saved = None
    vals: Dict[str, float] = {}
    src: Dict[str, str] = {}
    for k, fb in MESH_FALLBACK.items():
        v = _finite(requested.get(k)) if k in requested else None
        if v is not None:
            vals[k], src[k] = v, SOURCE_REQUEST
            continue
        if saved is None:
            saved = machine_mesh_settings(cfg)
        if k in saved:
            vals[k], src[k] = saved[k], SOURCE_MACHINE
        else:
            vals[k], src[k] = float(fb), SOURCE_FALLBACK
    vals["n_sectors"] = int(round(vals["n_sectors"]))
    return vals, src


def resolve_gap_layers(requested: Any,
                       cfg: Optional[Mapping[str, Any]] = None) -> Tuple[float, str]:
    """Gap layers per side for ``requested`` (None = the machine's setting)."""
    vals, src = resolve_mesh_settings({"gap_layers": requested}, cfg)
    return float(vals["gap_layers"]), src["gap_layers"]


def fallback_note(sources: Mapping[str, str],
                  values: Optional[Mapping[str, float]] = None) -> Optional[str]:
    """One line naming the keys that fell back, or None when none did."""
    fb = [k for k, s in sources.items() if s == SOURCE_FALLBACK]
    if not fb:
        return None
    parts = ["%s=%g" % (k, (values or MESH_FALLBACK)[k]) for k in fb]
    return ("no saved Mesh setting for %s — last-resort fallback %s used"
            % (", ".join(fb), ", ".join(parts)))
