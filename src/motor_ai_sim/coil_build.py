# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""The coil the CAD build actually draws, as numbers: bend radius, straight overhang, leads.

Owner 2026-10-30: k_end must come from the coil that is BUILT from the machine's geometry - the same coil the 3-D view and the CAD
export show - and be recomputed on every geometry change.  ``masses.end_winding_factor`` (the single source of copper mass, phase R
and copper loss) used the CAD coil only when a die typed three coil keys into its geometry, and the idealised semicircle otherwise;
most dies typed nothing, so most machines got the semicircle.

Where the built coil's parameters come from - exactly the path of the CAD build (``motor_ai_sim.cad.store.make_profile``):

1. the die's OWN CAD REFERENCE PROFILE (``store.reference_for(die)``: ``cad/reference/<die slug>.json``; the CIANO14 40 base profile
   is the own profile of the CIANO14 40 dies only), with the simulation geometry laid on top of it row by row (the mapping of
   ``config/fusion_param_map.yaml``), the die's documented deviations, and the configuration's ``cad_overrides`` (Geometry > Full:
   lead extension / stub length are CAD-only edits), then every expression evaluated.  The coil rows are formulas of the geometry -
   e.g. the base profile's bend radius is ``tooth_width/2 + insulation_thickness + wire_spacing_x/2 - 0.05 mm`` and its straight
   length ``motor_length + 1 mm`` - so a geometry edit moves them the way it moves the built coil;
2. a die WITHOUT its own profile (or a host without the private reference files): an ESTIMATE from the machine's own size
   (:func:`estimate_coil`, owner 2026-10-30: the CIANO14 40 constants - 1 mm overhang, 4.1 + 5 mm leads, a bend radius that makes
   every end a semicircle - are meaningless on a 400 mm machine).  ``kind`` is ``"estimate"`` and the UI says "estimated (no CAD
   profile)", never "from coil";
3. explicit ``wire_beng_r`` / ``coil_straight_extra`` / ``coil_lead_total`` in the geometry stay as per-die OVERRIDES of either.

The template's base rules (``TEMPLATE_BASE_RULES``) remain only as a named comparison (:func:`template_coil_parameters`).

Wire: flat wire wound ON EDGE around the tooth end.  The builder draws each layer as a ``wire_width x wire_height`` rectangle whose
``wire_width`` side lies across the slot (x) and bends it about axes parallel to the radial (y) direction - an edgewise bend, whose
centre-line radius is ``wire_beng_r + wire_width/2`` (``masses.cad_coil_length``).

Pure and cheap after the first call: the reference profile and the name mapping are read once (mtime-checked), an evaluation is
memoised on the inputs it reads.
"""
from __future__ import annotations

import copy
import json
import math
import os
import re
import threading
import time
from typing import Any, Dict, Mapping, Optional, Tuple

__all__ = ["COIL_KEYS", "TEMPLATE_BASE_RULES", "K_BEND", "coil_parameters", "estimate_coil", "template_coil_parameters",
           "context_machine", "clear_cache"]

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
#: dies whose own CAD profile IS the base profile (it was drawn from the CIANO14 40 Fusion model); every other die on the base
#: profile only borrows it, so its coil is estimated instead
_BASE_PROFILE_OWNERS = ("CIANO14_40",)

# ── the estimate for a die without its own CAD profile (owner 2026-10-30) ───────────────────────────────────────────────────
#: smallest inner bend radius / wire dimension in the bend plane.  The end turn bends the wire about a radial axis, so the
#: dimension in the bend plane is ``wire_width`` (edgewise when wire_width > wire_height, flatwise otherwise; for a round wire it
#: reads r >= k d).  The radius never exceeds the leg's inner side x1 (that is the semicircle end).  k = 0.7 is CALIBRATED on
#: the five coils drawn in Fusion (the private reference profiles, 2026-10-30): their bends run from 0.5·w to the semicircle
#: at 0.68-0.84·w.  With 0.7 the estimated k_end of their seven configurations is within 2.6 % (mean 1.0 %) of the CAD coil;
#: 0.5 gives up to -3.8 %, 1.0 up to +5.4 %, the old CIANO14 40 template rules up to +3.5 %.  An estimate, not a supplier
#: figure: a die's CAD profile or a typed ``wire_beng_r`` replaces it.
K_BEND = 0.7
#: axial overhang of the straight leg PER END = coil former / end insulation + clearance, scaled with the machine: 0.5 mm (rule W2,
#: legs = stack + 1 mm, decided on the 40 mm and 85 mm machines) up to Ø100, then 0.5 % of the stator diameter (Ø200 -> 1 mm,
#: Ø400 -> 2 mm per end) - end plates, slot-liner cuffs and creepage grow with the machine and its voltage.  On the five Fusion
#: coils (Ø40 to Ø200) the rule is within 0.5 mm of the drawn overhang
OVERHANG_MIN_MM = 0.5
OVERHANG_PER_D = 0.005
#: a lead per coil end = the circumferential run to the connection point, half a slot pitch at the coil's mean radius, plus the
#: axial stub to the phase board: 5 mm (the template's stub) or two wire widths, whichever is longer (a wide strip needs the room
#: to be bent up and soldered).  On the five Fusion coils the rule is within 13 % of the drawn leads (2 to 8 % on four of them).
LEAD_STUB_MIN_MM = 5.0
LEAD_STUB_PER_WIDTH = 2.0

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


def _owns_base_profile(die: Optional[str]) -> bool:
    sl = _store().slug(die or "")
    return any(sl == o or sl.startswith(o + "_") for o in _BASE_PROFILE_OWNERS)


def _reference(die: Optional[str]) -> Optional[Tuple[str, Dict[str, Dict[str, Any]], dict]]:
    """(path, {row name: row}, deviations) of the die's OWN CAD reference profile; None without the reference files or when the
    die only borrows the base profile."""
    st = _store()
    try:
        path = st.reference_for(die or "")[0]
    except Exception:                                   # noqa: BLE001
        return None
    if not path or not os.path.exists(path):
        return None
    if os.path.abspath(path) == os.path.abspath(st.BASE_PROFILE) and not _owns_base_profile(die):
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
    out["kind"] = "cad"
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
    out["kind"] = "template"
    out["source"] = ("CIANO14 40 template base coil rules%s"
                     % ("; cad_overrides " + ", ".join(sorted(applied)) if applied else ""))
    if applied:
        out["cad_overrides"] = sorted(applied)
    return out


def template_coil_parameters(geo: Optional[Mapping[str, Any]],
                             cad_overrides: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """The CIANO14 40 template's base coil rules on this geometry - a named COMPARISON only (what every die without its own
    profile was billed at until 2026-10-30), never the answer for a machine."""
    return _from_template(_numeric_geo(geo), dict(cad_overrides or {}))


def _g(geo: Mapping[str, float], key: str, default: float) -> float:
    v = geo.get(key)
    return float(v) if v is not None and v > 0 else float(default)


def estimate_coil(geo: Optional[Mapping[str, Any]], cad_overrides: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Bend radius, straight overhang and leads [mm] of a tooth coil ESTIMATED from the machine's own size, for a die without its
    own CAD profile (see ``K_BEND``, ``OVERHANG_*``, ``LEAD_STUB_*``).  A lead extension / stub length set in the configuration's
    ``cad_overrides`` (Geometry > Full) replaces its part of the lead."""
    g = _numeric_geo(geo)
    tw = _g(g, "tooth_width", _BASE_INPUTS["tooth_width"])
    ins = g.get("insulation_thickness", _BASE_INPUTS["insulation_thickness"])
    sx = g.get("wire_spacing_x", _BASE_INPUTS["wire_spacing_x"])
    ww = _g(g, "wire_width", 2.5)
    D = _g(g, "stator_diameter", 40.0)
    x1 = tw / 2.0 + ins + sx / 2.0                     # inner side of a straight leg (masses.cad_coil_length)
    rb = min(x1, K_BEND * ww)
    per_end = max(OVERHANG_MIN_MM, OVERHANG_PER_D * D)
    # coil's mean radius: the middle of the slot of an inner-rotor stator (design_eval.build_params radii)
    r_mid = D / 2.0 - _g(g, "core_thickness", 0.06 * D) - _g(g, "slot_height", 0.1 * D) / 2.0
    q = g.get("num_slots") or (g.get("num_seg", 0.0) * g.get("num_slots_per_segment", 0.0)) or 12.0
    ext = math.pi * max(r_mid, 0.25 * D) / float(q)     # half a slot pitch = 2·pi·r / Q / 2
    stub = max(LEAD_STUB_MIN_MM, LEAD_STUB_PER_WIDTH * ww)
    applied = []
    for k, v in _store().clean_cad_overrides(dict(cad_overrides or {})).items():
        if k == "coil_lead_ext":
            ext = float(v)
            applied.append(k)
        elif k == "coil_lead_len":
            stub = float(v)
            applied.append(k)
    edge = "edgewise" if ww > g.get("wire_height", 0.0) else "flatwise"
    return {
        "wire_beng_r": rb, "coil_straight_extra": 2.0 * per_end, "coil_lead_total": ext + stub,
        "profile": None, "kind": "estimate",
        "source": ("estimated (no CAD profile): %s bend R = min(%g x wire width, leg side %.2f mm); overhang 2 x %.2f mm "
                   "(max(%g mm, %g %% of the stator diameter)); leads = half a slot pitch %.2f + stub %.2f mm%s"
                   % (edge, K_BEND, x1, per_end, OVERHANG_MIN_MM, OVERHANG_PER_D * 100.0, ext, stub,
                      ("; cad_overrides " + ", ".join(sorted(applied))) if applied else "")),
        **({"cad_overrides": sorted(applied)} if applied else {}),
    }


def coil_parameters(geo: Optional[Mapping[str, Any]], die: Optional[str] = None, config: Optional[str] = None,
                    cad_overrides: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """``{"wire_beng_r", "coil_straight_extra", "coil_lead_total"}`` [mm] of the coil the CAD build draws for this geometry, plus
    ``source`` (where they came from, in words), ``profile`` (the reference file, or None), ``kind`` (``"cad"`` = the die's own
    CAD profile, ``"estimate"`` = no CAD profile, :func:`estimate_coil`) and ``typed`` (the keys the geometry itself carries -
    per-die overrides that win over either).

    ``die`` / ``config`` default to the machine of the caller (:func:`context_machine`); ``cad_overrides`` defaults to that
    configuration's stored block.  Never raises: the worst case is the estimate, named as such."""
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
            hit = _from_reference(ref, die, config, g, ovr) if ref else estimate_coil(g, ovr)
        except Exception as exc:                        # noqa: BLE001 - a profile that does not evaluate falls to the estimate
            hit = estimate_coil(g, ovr)
            hit["source"] += "; the reference profile %s did not evaluate: %s" % (os.path.basename(ref[0]) if ref else "-", exc)
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
