# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""The coil the CAD build actually draws, as numbers: bend radius, straight overhang, leads.

Owner 2026-10-30: k_end must come from the coil that is BUILT from the machine's geometry - the same coil the 3-D view and the CAD
export show - and be recomputed on every geometry change.  ``masses.end_winding_factor`` (the single source of copper mass, phase R
and copper loss) used the CAD coil only when a die typed three coil keys into its geometry, and the idealised semicircle otherwise;
most dies typed nothing, so most machines got the semicircle.

Where the built coil's parameters come from - exactly the path of the CAD build (``motor_ai_sim.cad.store.make_profile``):

1. the die's CAD REFERENCE PROFILE (``store.reference_for(die)``: ``cad/reference/<die slug>.json`` when the die has its own, else
   the CIANO14 40 reference every other die is built from), with the simulation geometry laid on top of it row by row (the mapping
   of ``config/fusion_param_map.yaml``), the die's documented deviations, and the configuration's ``cad_overrides`` (Geometry >
   Full: lead extension / stub length are CAD-only edits), then every expression evaluated.  The coil rows are formulas of the
   geometry - e.g. the base profile's bend radius is ``tooth_width/2 + insulation_thickness + wire_spacing_x/2 - 0.05 mm`` and its
   straight length ``motor_length + 1 mm`` - so a geometry edit moves them the way it moves the built coil;
2. without the reference files on this host (they are private data, ``store.find_reference_dir``): the template's own base rules,
   the ones ``cad/motor_templates/segmented_flatwire/coil.py`` documents (bend axis 0.05 mm off the leg's inner side, straight legs
   1 mm longer than the stack, leads 4.1 + 5 mm).  The answer says so (``source``), it is never silent;
3. explicit ``wire_beng_r`` / ``coil_straight_extra`` / ``coil_lead_total`` in the geometry stay as per-die OVERRIDES of either.

Wire: flat wire wound ON EDGE around the tooth end.  The builder draws each layer as a ``wire_width x wire_height`` rectangle whose
``wire_width`` side lies across the slot (x) and bends it about axes parallel to the radial (y) direction - an edgewise bend, whose
centre-line radius is ``wire_beng_r + wire_width/2`` (``masses.cad_coil_length``).

Pure and cheap after the first call: the reference profile and the name mapping are read once (mtime-checked), an evaluation is
memoised on the inputs it reads.
"""
from __future__ import annotations

import copy
import json
import os
import re
import threading
import time
from typing import Any, Dict, Mapping, Optional, Tuple

__all__ = ["COIL_KEYS", "TEMPLATE_BASE_RULES", "coil_parameters", "context_machine", "clear_cache"]

#: geometry keys of the coil shape (mm) - also the names of the CAD profile rows
COIL_KEYS = ("wire_beng_r", "coil_straight_extra", "coil_lead_total")

#: the template's base coil rules (CIANO14 40 reference, documented in cad/motor_templates/segmented_flatwire/coil.py) - used only
#: when the CAD reference files are not on this host; a test pins them against the reference profile wherever it is available
TEMPLATE_BASE_RULES = {
    "coil_x_in": "tooth_width / 2 + insulation_thickness + wire_spacing_x / 2",
    "wire_beng_r": "coil_x_in - 0.05",
    "coil_straight_len": "motor_length + 1",
    "coil_straight_extra": "coil_straight_len - motor_length",
    "coil_lead_ext": "4.1",
    "coil_lead_len": "5",
    "coil_lead_total": "coil_lead_ext + coil_lead_len",
}
#: geometry inputs of the base rules and their template defaults (mm), the defaults of the template's own Params
_BASE_INPUTS = {"tooth_width": 3.3, "insulation_thickness": 0.07, "wire_spacing_x": 0.1, "motor_length": 20.0}

_lock = threading.Lock()
_profiles: Dict[str, Tuple[float, Dict[str, Dict[str, Any]], dict]] = {}     # path|die -> (mtime, rows, deviations)
_mapping: Dict[str, Any] = {}
_memo: Dict[Any, Dict[str, Any]] = {}
_MEMO_MAX = 512
_ctx_cache: Dict[str, Tuple[float, Dict[str, str]]] = {}
_ovr_cache: Dict[Tuple[str, str], Tuple[float, dict]] = {}
_CTX_TTL_S = 2.0


def clear_cache() -> None:
    with _lock:
        _profiles.clear()
        _mapping.clear()
        _memo.clear()
        _ctx_cache.clear()
        _ovr_cache.clear()


# ── which machine ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def context_machine() -> Dict[str, str]:
    """``{"die", "config"}`` the caller's solve is for: the job's registered machine, else the workspace's loaded context, else the
    ``.family_context.json`` next to the config file this process reads (an optimizer eval subprocess has no workspace, only
    ``MOTOR_AI_SIM_CONFIG``).  ``{}`` when nothing names one."""
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


def config_cad_overrides(die: str, config: str) -> dict:
    """The configuration's ``cad_overrides`` block (Geometry > Full), ``{}`` when it has none or cannot be read."""
    if not die or not config:
        return {}
    key = (die, config)
    now = time.monotonic()
    hit = _ovr_cache.get(key)
    if hit is not None and now - hit[0] < _CTX_TTL_S:
        return hit[1]
    out: dict = {}
    try:
        from motor_ai_sim.routes.family import config_doc
        doc = config_doc(die, config) or {}
        raw = doc.get("cad_overrides")
        out = dict(raw) if isinstance(raw, dict) else {}
    except Exception:                                   # noqa: BLE001 - a missing store means no overrides
        out = {}
    _ovr_cache[key] = (now, out)
    return out


# ── the CAD reference profile ─────────────────────────────────────────────────────────────────────────────────────────────
def _store():
    from motor_ai_sim.cad import store
    return store


def _reference(die: Optional[str]) -> Optional[Tuple[str, Dict[str, Dict[str, Any]], dict]]:
    """(path, {row name: row}, deviations) of the die's CAD reference profile, or None without the reference files."""
    st = _store()
    try:
        path = st.reference_for(die or "")[0]
    except Exception:                                   # noqa: BLE001
        return None
    if not path or not os.path.exists(path):
        return None
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    ck = "%s|%s" % (path, die or "")
    with _lock:
        hit = _profiles.get(ck)
        if hit is not None and hit[0] == mtime:
            return path, hit[1], hit[2]
    try:
        with open(path, encoding="utf-8") as fh:
            pj = json.load(fh)
        if st.restrict_fusion_parts(pj.setdefault("profile", {}), die or ""):     # the same two calls as make_profile
            st.apply_fusion_param_overrides(pj)
        rows = {p["name"]: p for sec in st.SECTIONS for p in pj.get(sec, []) if isinstance(p, dict) and p.get("name")}
        devs = dict(pj.get("deviations") or {})
    except Exception:                                   # noqa: BLE001 - an unreadable profile = no profile, said in the source
        return None
    with _lock:
        _profiles[ck] = (mtime, rows, devs)
    return path, rows, devs


def _name_mapping() -> Dict[str, Any]:
    with _lock:
        if _mapping:
            return _mapping
    mp = _store().load_mapping()
    with _lock:
        _mapping.update(mp)
    return mp


def _closure(exprs: Mapping[str, str], wanted) -> Dict[str, str]:
    """The expressions ``wanted`` depend on (transitively) - evaluating ~300 rows of a profile for three is wasted work."""
    out: Dict[str, str] = {}
    todo = list(wanted)
    while todo:
        n = todo.pop()
        if n in out or n not in exprs:
            continue
        out[n] = exprs[n]
        for tok in set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", str(exprs[n]))):
            if tok in exprs and tok not in out:
                todo.append(tok)
    return out


def _numeric_geo(geo: Optional[Mapping[str, Any]]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for k, v in (geo or {}).items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[str(k)] = float(v)
    return out


def _from_reference(ref, die: str, config: str, geo: Dict[str, float], overrides: dict) -> Dict[str, Any]:
    st = _store()
    path, rows, devs = ref
    params = {n: dict(r) for n, r in rows.items()}
    for _key, fname, v, unit, _desc in st.primary_rows(geo, _name_mapping()):
        p = params.get(fname)
        if p is not None:
            p["freecad_expression"] = st._fmt(v, unit)
    for dn, dd in devs.items():
        if dn in params and isinstance(dd, dict) and dd.get("expr"):
            params[dn]["freecad_expression"] = dd["expr"]
    ovr = {k: v for k, v in st.clean_cad_overrides(overrides).items() if k in params}
    applied = st.apply_cad_overrides(params, ovr, die, config) if ovr else []
    exprs = {n: p["freecad_expression"] for n, p in params.items()}
    vals = st.evaluate(_closure(exprs, COIL_KEYS))
    out = {k: float(vals[k]) for k in COIL_KEYS}
    out["profile"] = os.path.basename(path)
    out["source"] = "CAD coil of %s (reference profile %s%s)" % (
        die or "the template", os.path.basename(path), ("; cad_overrides " + ", ".join(applied)) if applied else "")
    if applied:
        out["cad_overrides"] = applied
    return out


def _from_template(geo: Dict[str, float], overrides: dict) -> Dict[str, Any]:
    st = _store()
    exprs = dict(TEMPLATE_BASE_RULES)
    for k, d in _BASE_INPUTS.items():
        exprs[k] = repr(float(geo.get(k, d)))
    applied = []
    for k, v in st.clean_cad_overrides(overrides).items():
        if k in ("coil_lead_ext", "coil_lead_len"):
            exprs[k] = repr(float(v))
            applied.append(k)
    vals = st.evaluate(exprs)
    out = {k: float(vals[k]) for k in COIL_KEYS}
    out["profile"] = None
    out["source"] = ("CAD template base coil rules (the CAD reference files are not on this host%s)"
                     % ("; cad_overrides " + ", ".join(sorted(applied)) if applied else ""))
    if applied:
        out["cad_overrides"] = sorted(applied)
    return out


def coil_parameters(geo: Optional[Mapping[str, Any]], die: Optional[str] = None, config: Optional[str] = None,
                    cad_overrides: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """``{"wire_beng_r", "coil_straight_extra", "coil_lead_total"}`` [mm] of the coil the CAD build draws for this geometry, plus
    ``source`` (where they came from, in words), ``profile`` (the reference file, or None) and ``typed`` (the keys the geometry
    itself carries - per-die overrides that win over the built coil's).

    ``die`` / ``config`` default to the machine of the caller (:func:`context_machine`); ``cad_overrides`` defaults to that
    configuration's stored block.  Never raises: the worst case is the template's base rules, named as such."""
    g = _numeric_geo(geo)
    if die is None:
        ctx = context_machine()
        die, config = ctx.get("die"), (config or ctx.get("config"))
    die = str(die or "")
    config = str(config or "")
    if cad_overrides is None:
        cad_overrides = config_cad_overrides(die, config)
    ovr = dict(cad_overrides or {})
    ref = _reference(die)
    memo_key = (ref[0] if ref else None, die, config, tuple(sorted(g.items())),
                json.dumps(ovr, sort_keys=True, default=str))
    with _lock:
        hit = _memo.get(memo_key)
    if hit is None:
        try:
            hit = _from_reference(ref, die, config, g, ovr) if ref else _from_template(g, ovr)
        except Exception as exc:                        # noqa: BLE001 - a profile that does not evaluate falls to the template
            hit = _from_template(g, ovr)
            hit["source"] += "; the reference profile did not evaluate: %s" % exc
        with _lock:
            if len(_memo) >= _MEMO_MAX:
                _memo.clear()
            _memo[memo_key] = hit
    out = copy.deepcopy(hit)
    typed = [k for k in COIL_KEYS if (geo or {}).get(k) is not None]
    for k in typed:
        out[k] = float((geo or {})[k])
    out["typed"] = typed
    if typed:
        out["source"] = "geometry keys %s typed per die; the rest from the %s" % (
            ", ".join(typed), out["source"][0].lower() + out["source"][1:])
    out["die"] = die or None
    out["config"] = config or None
    return out
