"""Which 3-D factor a TORQUE-proportional number carries — one rule, one place.

Owner 2026-09-30 (refining #90): «если были старые расчёты 3D — применяй пока
их» — wherever a machine has an existing 3-D result, use it for now, for the
torque AND for Kt / Km, so that torque and Kt stay consistent:

1. a MEASURED torque factor k_T (Stage B / Stage D co-energy virtual work) of
   this geometry — or, flagged ``inherited``, of an earlier geometry of the
   same machine at the same stack length;
2. otherwise the Stage A flux factor k_flux, labelled "3-D, flux factor
   (k_T not measured)";
3. otherwise nothing: plain 2-D.

KV and ψ_PM keep k_flux in every case — the flux factor is what Stage A
measures.  Voltages keep k_flux too.  So does Kt, the NO-LOAD torque constant:
it is formed from KV (``kv_kt``: Kt = 11.695 / KV, owner 2026-11-05), so it
carries KV's factor and the pair always agrees.  Torque at a load point,
T / I at that load ("Kt at load"), Km and Km per mass are torque-proportional
and carry the torque factor above.

Every reader applies a stored factor only inside :data:`FACTOR_BAND`
(:func:`accepted_factor`): both sides of 1 are physical, so a factor above 1 is
applied, never dropped.  A value outside the band is refused AND logged.

The measured k_T records live in the repository's config (they are research
results, not per-workspace state): ``config/end_effect_3d.json`` →
``stage_b.torque.k_T`` (Ø40, 12 mm, 0.97947) and ``config/stage_d_kt.json`` →
``k_T`` (Ø150, 35 mm, 0.99245).  A passport entry may also carry ``k_T``
itself; that wins.
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

_CONFIG = Path(__file__).resolve().parents[2] / "config"

#: Short labels (one per printed value) and the one-clause explanations.
BASIS_MEASURED = "3-D"
BASIS_FLUX = "3-D, flux factor"
BASIS_2D = "2-D"


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


#: The open band a stored 3-D / end-effect factor (k_flux, k_T, k_3d) is applied in.  BOTH SIDES OF 1 ARE PHYSICAL: a
#: short stack loses gap flux to fringing (k < 1); flux linked beyond the stack, or a factor calibrated on a bench EMF
#: that reads above the model (a 40 mm-stack test motor: +8.5 %), puts it above 1.  Readers used to keep k <= 1.0 only
#: and silently printed the uncorrected number for such a factor (EMF audit 2026-11-07).  Outside this band, or not
#: finite, the number is not a correction but a broken record: it is refused, and the refusal is logged.
FACTOR_BAND = (0.5, 1.5)


def accepted_factor(k: Any, what: str = "3-D factor") -> Optional[float]:
    """``k`` as a float when it is a finite number inside :data:`FACTOR_BAND` (open), else ``None``.

    ``None`` in = no factor, quietly ``None`` out (the normal state of a machine without a 3-D result).  Anything else
    that is refused - outside the band, NaN / inf, not a number - is logged as a warning naming ``what``, so a dropped
    factor is never silent."""
    if k is None:
        return None
    f = _num(k)
    lo, hi = FACTOR_BAND
    if f is None or not math.isfinite(f) or not (lo < f < hi):
        log.warning("end3d_factors: %s = %r is outside (%g, %g) or not a number; not applied", what, k, lo, hi)
        return None
    return f


def _records() -> List[Dict[str, Any]]:
    """Every measured k_T this repository holds, as
    ``{fingerprint, k_T, slots, poles, od_mm, stack_mm, source}``."""
    out: List[Dict[str, Any]] = []
    try:
        d = json.loads((_CONFIG / "end_effect_3d.json").read_text(encoding="utf-8"))
        k = _num(((d.get("stage_b") or {}).get("torque") or {}).get("k_T"))
        m = d.get("machine") or {}
        if k:
            out.append({"fingerprint": str(d.get("geometry_fingerprint") or ""),
                        "k_T": k, "slots": m.get("num_slots"),
                        "poles": m.get("num_poles"), "od_mm": m.get("stator_od_mm"),
                        "stack_mm": m.get("stack_mm"),
                        "source": "Stage B co-energy virtual work "
                                  "(config/end_effect_3d.json)"})
    except Exception:          # noqa: BLE001 — no record is a normal state
        log.debug("end3d_factors: end_effect_3d.json unreadable", exc_info=True)
    try:
        d = json.loads((_CONFIG / "stage_d_kt.json").read_text(encoding="utf-8"))
        k = _num(d.get("k_T"))
        m = d.get("machine") or {}
        if k:
            out.append({"fingerprint": str(d.get("geometry_fingerprint") or ""),
                        "k_T": k, "slots": m.get("num_slots"),
                        "poles": m.get("num_poles"), "od_mm": m.get("stator_od_mm"),
                        "stack_mm": m.get("stack_mm"),
                        "source": "Stage D co-energy virtual work "
                                  "(config/stage_d_kt.json)"})
    except Exception:          # noqa: BLE001
        log.debug("end3d_factors: stage_d_kt.json unreadable", exc_info=True)
    return out


def measured_k_T(fingerprint: Optional[str], geo: Optional[Dict[str, Any]] = None
                 ) -> Optional[Dict[str, Any]]:
    """The measured k_T for this geometry: exact fingerprint first; else one
    measured on an earlier geometry of the SAME machine (slots, poles, OD
    within 0.5 mm) at the SAME stack length (within 5 %), flagged inherited.
    None when neither exists."""
    recs = _records()
    fp = str(fingerprint or "")
    for r in recs:
        if fp and r["fingerprint"] == fp:
            return {"k_T": r["k_T"], "source": r["source"], "inherited": False}
    g = geo or {}
    try:
        ns = int(g.get("num_seg") or 0)
        slots = int(g.get("num_slots") or 0) or ns * int(g.get("num_slots_per_segment") or 0)
        poles = int(g.get("num_poles") or 0) or ns * int(g.get("num_poles_per_segment") or 0)
        od = float(g.get("stator_diameter") or 0.0) or 2.0 * float(
            g.get("stator_outer_radius") or 0.0)
        L = float(g.get("motor_length") or 0.0)
    except (TypeError, ValueError):
        return None
    if not (slots and poles and od and L):
        return None
    for r in recs:
        try:
            if (int(r["slots"]) == slots and int(r["poles"]) == poles
                    and abs(float(r["od_mm"]) - od) <= 0.5
                    and abs(float(r["stack_mm"]) - L) <= 0.05 * L):
                return {"k_T": r["k_T"], "source": r["source"], "inherited": True,
                        "inherited_note": (
                            "measured on an earlier geometry of this machine "
                            "(%ss/%sp OD %g, %g mm stack) — used until it is "
                            "re-measured" % (r["slots"], r["poles"],
                                             float(r["od_mm"]),
                                             float(r["stack_mm"])))}
        except (TypeError, ValueError, KeyError):
            continue
    return None


def torque_factor(end3d: Optional[Dict[str, Any]]) -> Tuple[Optional[float], str]:
    """``(k, basis)`` for torque, Kt, Km, Km per mass and every torque- or
    power-derived number of a run whose ``end3d`` block is given."""
    e = end3d if isinstance(end3d, dict) else {}
    k = _num(e.get("k_T"))
    if k is None:
        k = _num(e.get("k_torque")) if e.get("torque_basis") == BASIS_MEASURED else None
    k = accepted_factor(k, "k_T")
    if k is not None:
        return k, BASIS_MEASURED
    kf = accepted_factor(e.get("k_flux"), "k_flux")
    if kf is not None:
        return kf, BASIS_FLUX
    return None, BASIS_2D


def basis_note(k: Optional[float], basis: str) -> str:
    """The one clause a report row / the tooltip carries."""
    if basis == BASIS_MEASURED and k:
        return "3-D corrected with the measured torque factor k_T = %.4f" % k
    if basis == BASIS_FLUX and k:
        return ("3-D with the flux factor k_flux = %.4f (k_T not measured)" % k)
    return "2-D — no 3-D result for this geometry"


def enrich(end3d: Optional[Dict[str, Any]], fingerprint: Optional[str],
           geo: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """The passport block plus the torque factor fields: ``k_T`` (+ source /
    inherited) when one was measured, and ``k_torque`` / ``torque_basis``
    always.  None stays None."""
    if not isinstance(end3d, dict):
        return end3d
    out = dict(end3d)
    if _num(out.get("k_T")) is None:
        # a passport inherited from an earlier geometry looks its k_T up
        # under that geometry's fingerprint first
        m = measured_k_T(out.get("inherited_from") or fingerprint, geo)
        if m is None and out.get("inherited_from"):
            m = measured_k_T(fingerprint, geo)
        if m:
            out["k_T"] = m["k_T"]
            out["k_T_source"] = m["source"]
            if m.get("inherited"):
                out["k_T_inherited"] = True
                out["k_T_note"] = m.get("inherited_note")
    k, basis = torque_factor(out)
    out["k_torque"] = k
    out["torque_basis"] = basis
    return out
