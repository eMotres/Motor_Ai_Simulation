"""The motor PWM extra loss BY PART (owner 2026-11-02: «show the PWM loss penalty
split by part — iron, magnets, copper AC — in Configure, not as one number»).

Pure post-processing of solved anchor runs (no FEM here):

* :func:`anchor_loss_parts` — one anchor's ``dP_harm_W`` split into
  ``iron`` / ``magnet`` / ``copper_ac`` / ``other`` (:data:`stage3.PART_KEYS`),
  summing to it, from the anchor run JSON (``scripts/passport_full.py
  task_pwm``).  Three sources, best first, each named on the record:

  ``matched``          the route's own matched pair (``harm_ref.parts_v_W`` −
                       ``harm_ref.parts_ref_W``, stored since 2026-11-02): PWM
                       and its sine reference on the same mesh, step count and
                       eddy method — the very pair ``dP_harm_W`` is.  ``other``
                       = shaft + sleeve eddy + rounding.
  ``seed_normalised``  an older anchor stores the matched pair's TOTALS only.
                       The split is then read from the PWM summary against the
                       seed sine run (``sine_36``: same point, same fundamental,
                       but its own coarse step count and eddy method), and the
                       per-part shares of that difference are scaled to the
                       matched total: ``part = (PWM − seed)_part × dP_harm /
                       (PWM − seed)_total``.  The shares are measured; the
                       absolute values are the matched total's.  Stated so.
  ``iron_only``        the summaries carry the iron but not copper / magnet:
                       iron from PWM − seed (clamped to [0, dP]), the rest in
                       ``other``.
  ``unsplit``          nothing to split: the whole extra loss in ``other``.

* :func:`anchor_row` — the stage-3 FEM anchor of one run JSON (what
  ``fullcard.pwm_classes`` builds), now carrying ``dP_parts``.
* :func:`rebuild_record` — adds ``motor_pwm_loss_parts_W`` to every point of an
  EXISTING stored passport from its anchor run JSONs (the tags in each variant's
  ``provenance.motor_pwm_fem_runs``), keeping every stored total: per point the
  split is the per-part model's shares at that point (the same HDF rule per
  part) times the stored ``motor_pwm_loss_W``, so a BORROWED / ESTIMATE scaling
  carries over unchanged.  ``scripts/rebuild_pwm_loss_parts.py`` is the CLI.
"""
from __future__ import annotations

import copy
import math
import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from motor_ai_sim.passport_v1.stage3 import PART_KEYS, PwmModel

_ND = 4          # stored precision of a part [W]


def _f(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def summary_parts(s: Any) -> Optional[Dict[str, Any]]:
    """Loss by part of one solver SUMMARY (``routes/simulation.py`` summary keys):
    iron ``P_core_W``, magnet ``P_mag_W`` (older: ``P_solid_W`` − shaft − sleeve),
    copper ``P_stranded_W`` (DC + AC, all copper the solve carries), total
    ``P_loss_total_W``, the iron's Bertotti terms ``P_core_terms``.  None when the
    summary has no iron (nothing to split)."""
    if not isinstance(s, Mapping):
        return None
    iron = _f(s.get("P_core_W"))
    if iron is None:
        return None
    mag = _f(s.get("P_mag_W"))
    if mag is None and _f(s.get("P_solid_W")) is not None:
        mag = _f(s.get("P_solid_W")) - (_f(s.get("P_shaft_W")) or 0.0) \
            - (_f(s.get("P_sleeve_W")) or 0.0)
    terms = s.get("P_core_terms")
    it = None
    if isinstance(terms, Mapping) and terms:
        it = {}
        for half in ("stator", "rotor"):
            h = terms.get(half)
            if isinstance(h, Mapping):
                it[half] = {k: _f(h.get(k)) or 0.0 for k in ("hysteresis_W", "eddy_W", "excess_W")}
                if h.get("window_closed") is False:
                    it[half]["window_closed"] = False
    return {"iron": iron, "magnet": mag, "copper": _f(s.get("P_stranded_W")),
            "total": _f(s.get("P_loss_total_W")), "iron_terms": it or None,
            "steps": s.get("n_steps_per_period")}


def _harm_parts(p: Any) -> Optional[Dict[str, Any]]:
    """``harm_ref.parts_*_W`` (``postproc.loss_parts``) in :func:`summary_parts` shape."""
    if not isinstance(p, Mapping) or _f(p.get("iron_W")) is None:
        return None
    return {"iron": _f(p.get("iron_W")), "magnet": _f(p.get("magnet_W")),
            "copper": _f(p.get("copper_W")), "total": _f(p.get("total_W")),
            "iron_terms": p.get("iron_terms") if isinstance(p.get("iron_terms"), Mapping) else None}


def _terms_delta(a: Optional[Mapping[str, Any]], b: Optional[Mapping[str, Any]],
                 f: float = 1.0) -> Optional[Dict[str, Any]]:
    """(a − b) × f per half and Bertotti term; None unless both sides carry them."""
    if not a or not b:
        return None
    out: Dict[str, Any] = {}
    for half in ("stator", "rotor"):
        ha, hb = a.get(half), b.get(half)
        if not isinstance(ha, Mapping) or not isinstance(hb, Mapping):
            continue
        out[half] = {k: round(((_f(ha.get(k)) or 0.0) - (_f(hb.get(k)) or 0.0)) * f, _ND)
                     for k in ("hysteresis_W", "eddy_W", "excess_W")}
        if ha.get("window_closed") is False or hb.get("window_closed") is False:
            out[half]["window_closed"] = False
    return out or None


def _close(parts: Dict[str, float], dP: float) -> Dict[str, float]:
    """Round the named parts and let ``other`` take the remainder, so the four
    sum to ``dP`` exactly (to the stored precision)."""
    out = {k: round(float(parts.get(k) or 0.0), _ND) for k in PART_KEYS if k != "other"}
    out["other"] = round(dP - sum(out.values()), _ND)
    return {k: out[k] for k in PART_KEYS}


def anchor_loss_parts(d: Mapping[str, Any], dP: Optional[float] = None) -> Dict[str, Any]:
    """Split one anchor run's PWM extra loss by part (module docstring).

    ``dP`` is the anchor's stored extra loss (after the negative clamp of
    ``fullcard.pwm_classes``); default: the run's ``dP_harm_W`` clamped at 0."""
    raw = _f(d.get("dP_harm_W"))
    if dP is None:
        dP = max(0.0, raw or 0.0)
    dP = float(dP)
    zero = {k: 0.0 for k in PART_KEYS}
    if dP == 0.0:
        return {"mode": "zero", "dP_total_W": 0.0, "parts_W": zero, "iron_terms_W": None,
                "basis": ("PWM − sine = %s W stored as 0 W (below the run's resolution): "
                          "nothing to split" % ("%+.1f" % raw if raw is not None else "0"))}
    href = d.get("harm_ref") if isinstance(d.get("harm_ref"), Mapping) else {}
    pv, pr = _harm_parts(href.get("parts_v_W")), _harm_parts(href.get("parts_ref_W"))
    if pv and pr and None not in (pv["magnet"], pv["copper"], pr["magnet"], pr["copper"]):
        raw_p = {"iron": pv["iron"] - pr["iron"], "magnet": pv["magnet"] - pr["magnet"],
                 "copper_ac": pv["copper"] - pr["copper"]}
        return {"mode": "matched", "dP_total_W": dP, "parts_W": _close(raw_p, dP),
                "iron_terms_W": _terms_delta(pv["iron_terms"], pr["iron_terms"]),
                "raw_W": {k: round(v, _ND) for k, v in raw_p.items()},
                "basis": ("2-D FEM matched pair: PWM − sine reference at the extracted "
                          "fundamental, same mesh, steps and eddy method (harm_ref); other = "
                          "shaft/sleeve eddy + rounding")}
    sv, ss = summary_parts(d.get("pwm_summary")), summary_parts(d.get("sine_36"))
    steps = "seed sine %s vs PWM %s steps/period" % (
        (ss or {}).get("steps") or "?", d.get("steps_pwm") or (sv or {}).get("steps") or "?")
    if sv and ss:
        full = None not in (sv["magnet"], sv["copper"], sv["total"],
                            ss["magnet"], ss["copper"], ss["total"])
        raw_p = {"iron": sv["iron"] - ss["iron"]}
        if full:
            raw_p.update(magnet=sv["magnet"] - ss["magnet"], copper_ac=sv["copper"] - ss["copper"])
            d_seed = sv["total"] - ss["total"]
            if d_seed > 0.0:
                fct = dP / d_seed
                return {"mode": "seed_normalised", "dP_total_W": dP,
                        "parts_W": _close({k: v * fct for k, v in raw_p.items()}, dP),
                        "iron_terms_W": _terms_delta(sv["iron_terms"], ss["iron_terms"], fct),
                        "raw_W": dict({k: round(v, _ND) for k, v in raw_p.items()},
                                      total=round(d_seed, _ND)),
                        "normalised_by": round(fct, 6),
                        "basis": ("2-D FEM shares of PWM − seed sine (%s, not matched), "
                                  "normalised to the matched total %.1f W (× %.3f); the "
                                  "anchor stores the matched reference's total only"
                                  % (steps, dP, fct))}
            return {"mode": "unsplit", "dP_total_W": dP, "parts_W": _close(zero, dP),
                    "iron_terms_W": None,
                    "raw_W": dict({k: round(v, _ND) for k, v in raw_p.items()},
                                  total=round(d_seed, _ND)),
                    "basis": ("not split: PWM − seed sine total is %+.1f W (%s) — no usable "
                              "shares" % (d_seed, steps))}
        iron = min(max(raw_p["iron"], 0.0), dP)
        return {"mode": "iron_only", "dP_total_W": dP,
                "parts_W": _close({"iron": iron}, dP),
                "iron_terms_W": _terms_delta(sv["iron_terms"], ss["iron_terms"]),
                "raw_W": {"iron": round(raw_p["iron"], _ND)},
                "basis": ("iron from PWM − seed sine (%s, not matched, clamped to [0, dP]); "
                          "the anchor has no copper/magnet split: the rest is in other" % steps)}
    return {"mode": "unsplit", "dP_total_W": dP, "parts_W": _close(zero, dP),
            "iron_terms_W": None,
            "basis": "not split: the anchor run carries no loss breakdown"}


def anchor_row(d: Mapping[str, Any], tag: str, v_dc: float) -> Optional[Dict[str, Any]]:
    """The stage-3 FEM anchor of one PWM task output (``pwm_<M>_*.json``), or None
    when the run has no ``dP_harm_W``.  ``fullcard.pwm_classes`` and the rebuild
    both build their anchors here, so the two cannot drift."""
    if d.get("dP_harm_W") is None:
        return None
    c = d.get("controller") or {}
    ss = d.get("sine_36") or {}
    v1 = ss.get("V1_seed_peak_V")
    row: Dict[str, Any] = {
        "tag": tag, "rpm": d.get("rpm"), "I": d.get("I"), "gamma": d.get("gamma"),
        "dP_harm_W": d.get("dP_harm_W"), "harm_ref": d.get("harm_ref"),
        "m_index": (float(v1) / (v_dc / 2.0)) if v1 else None,
        "device": c.get("device"), "temps": d.get("temps"),
        "steps": d.get("steps_pwm"), "wall_s": d.get("pwm_wall_s"),
        "mesh_note": "PWM step count raises the slip ring (640+ nodes/period); the "
                     "iron template falls back to the gmsh build (solver-flagged "
                     "DEGRADED, shaft skin layer not resolved) — PWM and its sine "
                     "reference share that mesh"}
    row["point"] = d.get("point")
    dp = float(d.get("dP_harm_W"))
    ref = float(((d.get("harm_ref") or {}).get("P_loss_ref_W")) or 0.0)
    if dp < 0.0:
        # PWM − same-mesh sine reference came out NEGATIVE: the extra loss is
        # below what this run resolves (a few watts of a ~200 W total, 4 samples
        # per carrier read the ripple-driven losses LOW).  Stored as 0 W with the
        # raw value and the reason beside it — never a negative loss.
        row["dP_harm_W_raw"] = dp
        row["dP_harm_W"] = 0.0
        row["dP_note"] = ("FEM PWM − same-mesh sine reference = %+.1f W of %.1f W total "
                          "(%.2f %%): below this run's resolution; stored as 0 W"
                          % (dp, ref, 100.0 * dp / ref if ref else float("nan")))
    row["dP_parts"] = anchor_loss_parts(d, float(row["dP_harm_W"]))
    return row


# ─────────────────────────────────────────────────────────────────────────────
#  rebuild an existing stored passport (no FEM)
# ─────────────────────────────────────────────────────────────────────────────

_HDF_M = re.compile(r"HDF\(m\s*([0-9]*\.?[0-9]+)\)\s*$")
_ANCHOR_MS = re.compile(r"FEM anchors \([^,]*,\s*m\s*([0-9./]+)\)")


def _point_m(basis: Any) -> Optional[float]:
    m = _HDF_M.search(str(basis or ""))
    return float(m.group(1)) if m else None


#: Largest |m(anchor run) − m(stored basis)| accepted.  The stored basis prints m
#: to 3 decimals and the bus it was divided by may be rounded; another machine's
#: run under the same tag differs by far more than this.
M_MATCH_TOL = 0.005


def _check_anchor_ms(v: Mapping[str, Any], anchors: List[Mapping[str, Any]]) -> Optional[str]:
    """The loaded anchors must be the ones the variant was built from: the stored
    point bases list the anchors' m (3 decimals).  A mismatch = another
    machine's run under the same tag — refused."""
    stored = None
    for p in (v.get("points") or {}).values():
        if isinstance(p, Mapping):
            mm = _ANCHOR_MS.search(str(p.get("motor_pwm_loss_basis") or ""))
            if mm:
                stored = mm.group(1)
                break
    if stored is None:
        return None
    try:
        want = [float(x) for x in stored.split("/") if x]
    except ValueError:
        return "stored basis m list %r is unreadable" % stored
    have = sorted({round(float(x["m_index"]), 3) for x in anchors if x.get("m_index")})
    if len(have) == len(want) and all(abs(a - b) <= M_MATCH_TOL for a, b in zip(have, want)):
        return None
    return ("anchor m %s does not match the stored basis m %s (tolerance %g)"
            % ("/".join("%.3f" % m for m in have), stored, M_MATCH_TOL))


def rebuild_variant(v: Mapping[str, Any], anchors: List[Dict[str, Any]]
                    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """One stored variant with ``motor_pwm_loss_parts_W`` on every point that has a
    ``motor_pwm_loss_W`` (and the variant-level ``motor_pwm_loss_parts`` record).
    Stored totals are kept: per point the parts are the per-part model's shares
    times the stored total.  Returns (variant, report)."""
    rep: Dict[str, Any] = {"id": v.get("id"), "anchors": [a.get("tag") for a in anchors],
                           "split": 0, "unsplit": [], "max_sum_err_W": 0.0}
    out = copy.deepcopy(dict(v))
    model = PwmModel(anchors)
    if not model:
        rep["refused"] = "no usable anchor (dP_harm_W + m_index) for this variant"
        return out, rep
    bad = _check_anchor_ms(v, model.anchors)
    if bad:
        rep["refused"] = bad
        return out, rep
    rep["modes"] = sorted({(a.get("dP_parts") or {}).get("mode", "unsplit") for a in model.anchors})
    for key, p in (out.get("points") or {}).items():
        if not isinstance(p, dict):
            continue
        tot = _f(p.get("motor_pwm_loss_W"))
        if tot is None:
            continue
        rpm, I = _f(p.get("rpm")), _f(p.get("I_A"))
        exact = rpm is not None and I is not None and any(
            abs(float(a["rpm"]) - rpm) < 1e-6 and abs(float(a["I"]) - I) < 1e-6
            for a in model.anchors)
        m_p = _point_m(p.get("motor_pwm_loss_basis"))
        if not exact and m_p is None:
            rep["unsplit"].append(key)
            continue
        raw = model.parts_at(rpm or 0.0, I or 0.0, m_p if m_p is not None else 0.0)
        s = sum(raw.values())
        if abs(s) > 1e-12:
            parts = {k: raw[k] * tot / s for k in PART_KEYS}
        else:
            parts = {k: 0.0 for k in PART_KEYS}
            parts["other"] = tot
        p["motor_pwm_loss_parts_W"] = parts
        rep["split"] += 1
        rep["max_sum_err_W"] = max(rep["max_sum_err_W"], abs(sum(parts.values()) - tot))
    blk = model.parts_block()
    blk["rebuilt"] = ("from the stored anchor run JSONs, no FEM: per point = the per-part "
                      "model's shares × the stored motor_pwm_loss_W")
    out["motor_pwm_loss_parts"] = blk
    return out, rep


def rebuild_record(rec: Mapping[str, Any],
                   load_run: Callable[[str], Optional[Mapping[str, Any]]]
                   ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Every variant of a stored passport record, rebuilt with the split.

    ``load_run(tag)`` returns the anchor run JSON of a tag (None = not found).
    A variant whose anchors cannot all be found, or do not match its stored
    basis, is left exactly as it was and reported."""
    out = copy.deepcopy(dict(rec))
    reps: List[Dict[str, Any]] = []
    vs = []
    for v in out.get("pwm_variants") or []:
        if not isinstance(v, dict) or not isinstance(v.get("points"), dict) or not v["points"]:
            vs.append(v)
            continue
        tags = [t for t in ((v.get("provenance") or {}).get("motor_pwm_fem_runs") or []) if t]
        if not tags:
            vs.append(v)
            reps.append({"id": v.get("id"), "refused": "no motor_pwm_fem_runs in provenance"})
            continue
        v_dc = _f((v.get("bus_v") or {}).get("nom"))
        anchors, missing = [], []
        for t in tags:
            d = load_run(t)
            row = anchor_row(d, t, v_dc) if (d and v_dc) else None
            (anchors.append(row) if row else missing.append(t))
        if missing:
            vs.append(v)
            reps.append({"id": v.get("id"), "refused": "anchor run(s) not found: "
                         + ", ".join(missing)})
            continue
        nv, rep = rebuild_variant(v, anchors)
        vs.append(nv)
        reps.append(rep)
    out["pwm_variants"] = vs
    return out, reps
