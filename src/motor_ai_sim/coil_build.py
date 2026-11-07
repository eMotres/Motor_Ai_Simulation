# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""The tooth coil k_end is billed at, as numbers: bend radius, straight overhang, leads - ONE rule for every machine.

Owner 2026-10-30: «не надо цеплять к CAD-ам, считай всё по правилам, чтобы везде было одинаково».  k_end (the single source of
copper mass, phase R and copper loss, ``masses.end_winding_factor``) is the mean turn of a tooth coil built by the same rule on
every machine, recomputed on every geometry change.  No CAD reference profile is read on this path (the private profiles stay for
the CAD export only).

The coil end is RECTANGULAR (the owner's drawing, variant B): a straight bridge across the tooth end, 2·(x1 − r), between two 90°
bends of inner radius r, where x1 = tooth_width/2 + insulation_thickness + wire_spacing_x/2 is the inner side of a straight leg.
The bend turns the wire about a radial axis, so the wire dimension in the bend plane is ``wire_width``:

* bend radius  r = 0.6 x wire_width (``K_BEND``, owner: a 7.5 mm strip bends at 4.5 mm).  Edgewise when wire_width > wire_height
  (flat wire on edge); flatwise when wire_width <= wire_height, which includes a round wire (w = h = d): the same r = 0.6 x w then
  reads r = 0.6 d.  The radius is capped at x1 - a bend wider than the leg's offset is the semicircle end, the only coil that fits;
* straight overhang per end = max(0.5 mm, 0.5 % of the stator diameter) (``OVERHANG_*``);
* lead per coil end = half a slot pitch at the slot's mid radius + max(5 mm, 2 x wire_width) (``LEAD_STUB_*``); a lead extension /
  stub length set in the configuration's ``cad_overrides`` (Geometry > Full) replaces its part.

Explicit ``wire_beng_r`` / ``coil_straight_extra`` / ``coil_lead_total`` in a die's geometry stay as per-die OVERRIDES.  The CIANO14
40 template's base rules (``TEMPLATE_BASE_RULES``) remain only as a named comparison (:func:`template_coil_parameters`).

PER CONFIGURATION (owner 2026-11-02: «все бери из Fusion»): machines that share one die and one wire can still have different
coils in their Fusion models, so a configuration may set its own ``wire_beng_r`` / ``coil_straight_extra`` (``CONFIG_COIL_KEYS``,
mm) in its ``coil_overrides`` block.  Order, lowest first: the rule, the die's geometry keys, the configuration's block.  A key the
block does not name keeps the die's value (or the rule's), exactly as before.  The block belongs to the configuration document,
not to the live geometry, so loading a sibling configuration of the same die never carries another one's coil along.

Geometry of the built coil: ``masses.cad_coil_length`` (centre-line bend radius r + wire_width/2).
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import math
import time
from typing import Any, Dict, Iterator, Mapping, Optional, Tuple

__all__ = ["COIL_KEYS", "CONFIG_COIL_KEYS", "CONFIG_COIL_BLOCK", "CONFIG_COIL_LIMITS_MM", "TEMPLATE_BASE_RULES", "K_BEND",
           "coil_parameters", "coil_rule", "estimate_coil", "template_coil_parameters", "context_machine", "machine_scope",
           "config_coil_overrides", "clean_coil_overrides", "validate_coil_override", "clear_cache"]

#: geometry keys of the coil shape (mm) - per-die overrides when a die's geometry carries them
COIL_KEYS = ("wire_beng_r", "coil_straight_extra", "coil_lead_total")

#: the coil keys a CONFIGURATION may set for itself (Fusion names, mm) and the block of the configuration yaml that holds them
CONFIG_COIL_KEYS = ("wire_beng_r", "coil_straight_extra")
CONFIG_COIL_BLOCK = "coil_overrides"
#: sane bounds [mm], the lower one exclusive: the inner bend radius of the wire, and the straight overhang of the legs beyond
#: the stack summed over BOTH ends (Fusion: ``coil_straight_len - motor_length``)
CONFIG_COIL_LIMITS_MM: Dict[str, Tuple[float, float]] = {
    "wire_beng_r": (0.0, 50.0),
    "coil_straight_extra": (0.0, 100.0),
}

#: the CIANO14 40 template's base coil rules (cad/motor_templates/segmented_flatwire/coil.py) - a COMPARISON only
TEMPLATE_BASE_RULES = {
    "coil_x_in": "tooth_width / 2 + insulation_thickness + wire_spacing_x / 2",
    "wire_beng_r": "coil_x_in - 0.05",
    "coil_straight_len": "motor_length + 1",
    "coil_straight_extra": "coil_straight_len - motor_length",
    "coil_lead_ext": "4.1",
    "coil_lead_len": "5",
    "coil_lead_total": "coil_lead_ext + coil_lead_len",
}
#: geometry inputs of the rules and their template defaults (mm), the defaults of the template's own Params
_BASE_INPUTS = {"tooth_width": 3.3, "insulation_thickness": 0.07, "wire_spacing_x": 0.1, "motor_length": 20.0}

#: inner bend radius / wire dimension in the bend plane (owner 2026-10-30: 7.5 mm wire -> 4.5 mm)
K_BEND = 0.6
#: axial overhang of the straight leg PER END = coil former / end insulation + clearance, scaled with the machine: 0.5 mm (legs =
#: stack + 1 mm, the design rule of the small machines) up to Ø100, then 0.5 % of the stator diameter (Ø200 -> 1 mm, Ø400 -> 2 mm)
OVERHANG_MIN_MM = 0.5
OVERHANG_PER_D = 0.005
#: a lead per coil end = the circumferential run to the connection point, half a slot pitch at the coil's mean radius, plus the
#: axial stub to the phase board: 5 mm or two wire widths, whichever is longer (a wide strip needs room to be bent up and soldered)
LEAD_STUB_MIN_MM = 5.0
LEAD_STUB_PER_WIDTH = 2.0

_ctx_cache: Dict[str, Tuple[float, Dict[str, str]]] = {}
_ovr_cache: Dict[Tuple[str, str], Tuple[float, Dict[str, dict]]] = {}
_CTX_TTL_S = 2.0

#: the machine a catalog computation is FOR (:func:`machine_scope`) - wins over the loaded context
_scope: "contextvars.ContextVar[Optional[Dict[str, str]]]" = contextvars.ContextVar("coil_build_machine_scope", default=None)


def clear_cache() -> None:
    _ctx_cache.clear()
    _ovr_cache.clear()


@contextlib.contextmanager
def machine_scope(die: Optional[str], config: Optional[str], cfg_doc: Optional[Mapping[str, Any]] = None) -> Iterator[None]:
    """Inside the block every coil read that names no machine is for ``die`` / ``config``, not the loaded context.

    For catalog computations of a NAMED configuration (its mass, its datasheet) that reach k_end several calls deep: without the
    scope they would bill the coil of whatever machine the workspace has loaded - a sibling configuration's coil overrides.
    ``cfg_doc`` = the configuration document the caller already read (its ``cad_overrides`` / ``coil_overrides`` are then used
    as they are, so the coil is the one of the very file the rest of the computation reads)."""
    val: Dict[str, Any] = {"die": str(die), "config": str(config)} if die and config else {}
    if val and isinstance(cfg_doc, Mapping):
        val["blocks"] = {blk: (dict(cfg_doc.get(blk)) if isinstance(cfg_doc.get(blk), Mapping) else {})
                         for blk in ("cad_overrides", CONFIG_COIL_BLOCK)}
    tok = _scope.set(val)
    try:
        yield
    finally:
        _scope.reset(tok)


# ── which machine (for its cad_overrides / coil_overrides) ─────────────────────────────────────────────────────────────────
def context_machine() -> Dict[str, str]:
    """``{"die", "config"}`` the caller's solve is for: an enclosing :func:`machine_scope`, else the job's registered machine,
    else the workspace's loaded context, else the ``.family_context.json`` next to the config file this process reads (an
    optimizer eval subprocess has no workspace, only ``MOTOR_AI_SIM_CONFIG``).  ``{}`` when nothing names one."""
    scoped = _scope.get()
    if scoped is not None:
        return {k: scoped[k] for k in ("die", "config") if k in scoped}
    try:
        from motor_ai_sim import run_target
        found = run_target.run_machine()
        if found:
            return found
    except Exception:                                   # noqa: BLE001 - attribution never fails a solve
        pass
    try:
        from motor_ai_sim.config import config_path
        path = str(config_path().parent / ".family_context.json")
    except Exception:                                   # noqa: BLE001
        return {}
    now = time.monotonic()
    hit = _ctx_cache.get(path)
    if hit is not None and now - hit[0] < _CTX_TTL_S:
        return hit[1]
    ctx: Dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        if isinstance(raw, dict) and raw.get("die") and raw.get("config"):
            ctx = {"die": str(raw["die"]), "config": str(raw["config"])}
    except Exception:                                   # noqa: BLE001 - no context is the ordinary case
        ctx = {}
    _ctx_cache[path] = (now, ctx)
    return ctx


def _config_blocks(die: str, config: str) -> Dict[str, dict]:
    """``{"cad_overrides": {...}, "coil_overrides": {...}}`` of the configuration document: one cached read for both."""
    empty: Dict[str, dict] = {"cad_overrides": {}, CONFIG_COIL_BLOCK: {}}
    if not die or not config:
        return empty
    scoped = _scope.get()
    if scoped and scoped.get("blocks") is not None and scoped.get("die") == die and scoped.get("config") == config:
        return scoped["blocks"]
    key = (die, config)
    now = time.monotonic()
    hit = _ovr_cache.get(key)
    if hit is not None and now - hit[0] < _CTX_TTL_S:
        return hit[1]
    out: Dict[str, dict] = {"cad_overrides": {}, CONFIG_COIL_BLOCK: {}}
    try:
        from motor_ai_sim.routes.family import config_doc
        doc = config_doc(die, config) or {}
        for blk in out:
            raw = doc.get(blk)
            out[blk] = dict(raw) if isinstance(raw, dict) else {}
    except Exception:                                   # noqa: BLE001 - a missing store means no overrides
        out = empty
    _ovr_cache[key] = (now, out)
    return out


def config_cad_overrides(die: str, config: str) -> dict:
    """The configuration's ``cad_overrides`` block (Geometry > Full), ``{}`` when it has none or cannot be read."""
    return dict(_config_blocks(die, config)["cad_overrides"])


def config_coil_overrides(die: str, config: str) -> Dict[str, float]:
    """The configuration's own coil keys (its ``coil_overrides`` block, valid values only), ``{}`` when it sets none."""
    return clean_coil_overrides(_config_blocks(die, config)[CONFIG_COIL_BLOCK])


# ── per-configuration coil keys: validation ───────────────────────────────────────────────────────────────────────────────
def validate_coil_override(key: str, value: Any) -> Tuple[Optional[float], Optional[str]]:
    """``(value in mm, None)`` for an acceptable configuration coil value, else ``(None, the reason in words)``."""
    if key not in CONFIG_COIL_KEYS:
        return None, "%s is not a configuration coil key (allowed: %s)" % (key, ", ".join(CONFIG_COIL_KEYS))
    if isinstance(value, bool):
        return None, "%s must be a number in mm" % key
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None, "%s must be a number in mm, got %r" % (key, value)
    lo, hi = CONFIG_COIL_LIMITS_MM[key]
    if not math.isfinite(v) or v <= lo or v > hi:
        return None, "%s = %r mm is outside (%g, %g] mm" % (key, value, lo, hi)
    return v, None


def clean_coil_overrides(raw: Any) -> Dict[str, float]:
    """The stored ``coil_overrides`` block as ``{key: mm}``: unknown keys, nulls and invalid values are dropped (the write route
    refuses them; a hand-edited file must never stop k_end)."""
    out: Dict[str, float] = {}
    if not isinstance(raw, Mapping):
        return out
    for k in CONFIG_COIL_KEYS:
        if raw.get(k) is None:
            continue
        v, why = validate_coil_override(k, raw.get(k))
        if why is None and v is not None:
            out[k] = v
    return out


# ── the rule ───────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _store():
    from motor_ai_sim.cad import store                  # expression evaluator + cad_overrides cleaner (no private files)
    return store


def _numeric_geo(geo: Optional[Mapping[str, Any]]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for k, v in (geo or {}).items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[str(k)] = float(v)
    return out


def _g(geo: Mapping[str, float], key: str, default: float) -> float:
    v = geo.get(key)
    return float(v) if v is not None and v > 0 else float(default)


def _lead_overrides(cad_overrides: Optional[Mapping[str, Any]]) -> Dict[str, float]:
    ovr = _store().clean_cad_overrides(dict(cad_overrides or {}))
    return {k: float(ovr[k]) for k in ("coil_lead_ext", "coil_lead_len") if k in ovr}


def coil_rule(geo: Optional[Mapping[str, Any]], cad_overrides: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Bend radius, straight overhang and leads [mm] of the tooth coil by the ONE rule (module docstring)."""
    g = _numeric_geo(geo)
    tw = _g(g, "tooth_width", _BASE_INPUTS["tooth_width"])
    ins = g.get("insulation_thickness", _BASE_INPUTS["insulation_thickness"])
    sx = g.get("wire_spacing_x", _BASE_INPUTS["wire_spacing_x"])
    ww = _g(g, "wire_width", 2.5)
    D = _g(g, "stator_diameter", 40.0)
    x1 = tw / 2.0 + ins + sx / 2.0                     # inner side of a straight leg (masses.cad_coil_length)
    rb = min(K_BEND * ww, x1)
    per_end = max(OVERHANG_MIN_MM, OVERHANG_PER_D * D)
    # coil's mean radius: the middle of the slot of an inner-rotor stator (design_eval.build_params radii)
    r_mid = D / 2.0 - _g(g, "core_thickness", 0.06 * D) - _g(g, "slot_height", 0.1 * D) / 2.0
    q = g.get("num_slots") or (g.get("num_seg", 0.0) * g.get("num_slots_per_segment", 0.0)) or 12.0
    ext = math.pi * max(r_mid, 0.25 * D) / float(q)     # half a slot pitch = 2·pi·r / Q / 2
    stub = max(LEAD_STUB_MIN_MM, LEAD_STUB_PER_WIDTH * ww)
    lo = _lead_overrides(cad_overrides)
    ext = lo.get("coil_lead_ext", ext)
    stub = lo.get("coil_lead_len", stub)
    edge = "edgewise" if ww > g.get("wire_height", 0.0) else "flatwise"
    end = ("rectangular end, bridge %.2f mm" % (2.0 * (x1 - rb))) if rb < x1 else "semicircle end (0.6 x w >= leg side)"
    return {
        "wire_beng_r": rb, "coil_straight_extra": 2.0 * per_end, "coil_lead_total": ext + stub,
        "profile": None, "kind": "rule",
        "source": ("coil rule: %s bend R = %g x wire width = %.2f mm, %s; overhang 2 x %.2f mm (max(%g mm, %g %% of the stator "
                   "diameter)); leads = half a slot pitch %.2f + stub %.2f mm%s"
                   % (edge, K_BEND, K_BEND * ww, end, per_end, OVERHANG_MIN_MM, OVERHANG_PER_D * 100.0, ext, stub,
                      ("; cad_overrides " + ", ".join(sorted(lo))) if lo else "")),
        **({"cad_overrides": sorted(lo)} if lo else {}),
    }


#: the name this function had while it was the fallback for dies without a CAD profile (#512)
estimate_coil = coil_rule


def template_coil_parameters(geo: Optional[Mapping[str, Any]],
                             cad_overrides: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """The CIANO14 40 template's base coil rules on this geometry - a named COMPARISON only, never the answer for a machine."""
    g = _numeric_geo(geo)
    exprs = dict(TEMPLATE_BASE_RULES)
    for k, d in _BASE_INPUTS.items():
        exprs[k] = repr(float(g.get(k, d)))
    lo = _lead_overrides(cad_overrides)
    for k, v in lo.items():
        exprs[k] = repr(v)
    vals = _store().evaluate(exprs)
    out: Dict[str, Any] = {k: float(vals[k]) for k in COIL_KEYS}
    out.update(profile=None, kind="template",
               source="CIANO14 40 template base coil rules%s" % ("; cad_overrides " + ", ".join(sorted(lo)) if lo else ""))
    return out


def coil_parameters(geo: Optional[Mapping[str, Any]], die: Optional[str] = None, config: Optional[str] = None,
                    cad_overrides: Optional[Mapping[str, Any]] = None,
                    coil_overrides: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """``{"wire_beng_r", "coil_straight_extra", "coil_lead_total"}`` [mm] of the coil k_end is billed at: :func:`coil_rule` on this
    geometry, the keys a die's geometry carries winning over it (``typed``), and the configuration's own ``coil_overrides``
    winning over both (``config_keys``).  Also ``source`` (in words), ``kind`` (``"rule"``), ``profile`` (always None: no CAD
    profile on this path), ``die`` / ``config``.

    ``die`` / ``config`` (default: the caller's machine, :func:`context_machine`) select that configuration's ``cad_overrides``
    (lead edits) and ``coil_overrides`` (bend radius, straight overhang); a block passed explicitly wins.  Never raises."""
    if die is None:
        ctx = context_machine()
        die, config = ctx.get("die"), (config or ctx.get("config"))
    die = str(die or "")
    config = str(config or "")
    if cad_overrides is None:
        cad_overrides = config_cad_overrides(die, config)
    own = clean_coil_overrides(coil_overrides) if coil_overrides is not None else config_coil_overrides(die, config)
    try:
        out = coil_rule(geo, cad_overrides)
    except Exception:                                   # noqa: BLE001 - an unreadable cad_overrides block never stops k_end
        out = coil_rule(geo, None)
    typed = [k for k in COIL_KEYS if (geo or {}).get(k) is not None]
    for k in typed:
        out[k] = float((geo or {})[k])
    out["typed"] = typed
    if typed:
        out["source"] = "geometry keys %s typed per die; the rest from the %s" % (", ".join(typed), out["source"])
    cfg_keys = [k for k in CONFIG_COIL_KEYS if k in own]
    for k in cfg_keys:
        out[k] = float(own[k])
    out["config_keys"] = cfg_keys
    if cfg_keys:
        out["source"] = "configuration '%s' sets %s; %s" % (
            config, ", ".join("%s = %g mm" % (k, own[k]) for k in cfg_keys), out["source"])
    out["die"] = die or None
    out["config"] = config or None
    return out
