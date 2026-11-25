"""Which flux factor a Stage A result applies: the REAL k_flux or k_flux_self, decided once from its 2-D verdict.

A Stage A run (``simulation.static3d.end_effect.run_stage_a``) gives two numbers at its own stack:

* ``k_flux`` = axial mean of the gap fundamental / the 2-D fundamental - what multiplies a 2-D flux, EMF, psi_PM;
* ``k_flux_self`` = the same mean / the 3-D MID-PLANE value - the end effect with the mid-plane 3-D/2-D
  difference taken out.

The passport writers used to put ``k_flux_self`` in the place of ``k_flux`` whenever the run's 2-D check said FAIL, and
the stored scalar then carried no mark of it (a stored entry with ``k_flux == k_flux_self`` exactly is that mark).  The
old check compared the mid-plane with 2-D at the reference stack within 2 %, which a short stack fails by END PHYSICS
(the mid-plane itself sits below 2-D: 0.957 at 12 mm on a 28-pole 80 mm machine, 0.9996 at 48 mm).  So:

=====================================  ===================================  =========================================
verdict (``two_d``)                    applied                               why
=====================================  ===================================  =========================================
PASS (either rule)                     k_flux                                the two models agree
NOT CHECKED (end-deficit rule)         k_flux                                no model offset can be seen
FAIL (end-deficit rule)                k_flux_self                           model disagreement beyond the end effect
INVALID (2-D fundamental 0 / NaN)      k_flux_self                           no 2-D ratio exists
FAIL (old mid-plane rule), plausible   k_flux, verdict "NOT CHECKED (old     the mid-plane test is not a model check
                                       mid-plane rule)"
FAIL (old mid-plane rule), otherwise   k_flux_self, verdict "NOT CHECKED     the old gate may have caught a real fault
                                       (old rule, recompute)"
unknown verdict, k_flux_usable False   k_flux_self                           the run said not to use k_flux
no 2-D leg                             k_flux (= k_flux_self in that run)    nothing to compare
=====================================  ===================================  =========================================

"Plausible" for an old-rule FAIL (the only gate that used to catch real faults - a 2-D source M instead of M/mu_rec, an
unconverged 2-D Picard ~11 % low): the 2-D leg converged (``two_d.picard_converged`` True), 0.85 <= mid / 2-D < 1, and
k_flux <= k_flux_self - the signature of a short-stack mid-plane deficit.  Measured cases that pass it: a 12s14p 40 mm
machine at 12 mm (0.9724), a 28-pole 80 mm machine at 8 and 12 mm (0.9228, 0.9571).

The end-deficit rule is recognised by ``two_d["agreement_check"]`` or a passport ``version`` of static3d-stageA-3 or
later.  Numbers are accepted only inside ``end3d_factors.FACTOR_BAND``.  Every answer carries ``value`` (what to apply),
the real ``k_flux``, ``k_flux_self``, ``verdict``, ``rule``, ``basis`` and ``note`` (also when a missing number forced the
other one), so a writer stores the real k_flux beside the applied one and never substitutes silently.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional

BASIS_K_FLUX = "k_flux"
BASIS_SELF = "k_flux_self"
RULE_END_DEFICIT = "end deficit"
RULE_MID_PLANE = "mid-plane (old)"
RULE_NONE = "no 2-D leg"
VERDICT_OLD_NOT_CHECKED = "NOT CHECKED (old mid-plane rule)"


RULE_INVALID = "2-D invalid"
VERDICT_OLD_RECOMPUTE = "NOT CHECKED (old rule, recompute)"
#: an old-rule FAIL is read as end physics only inside these bounds of the mid-plane / 2-D ratio
OLD_RULE_RATIO_MIN = 0.85
OLD_RULE_RATIO_MAX = 1.0
_KNOWN = ("PASS", "FAIL", "INVALID", "NOT CHECKED")


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _factor(v: Any, what: str) -> Optional[float]:
    from motor_ai_sim.end3d_factors import accepted_factor
    return accepted_factor(v, what)


def _version_n(version: Any) -> Optional[int]:
    try:
        return int(str(version).rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return None


#: the 2-D magnet source was fixed to M/mu_rec on this date (static3d ref2d, 2026-08-04): an old-rule run generated
#: before it may be the M-source fault the mid-plane gate caught, so it never gets the short-stack reading
OLD_RULE_SOURCE_FIX_UTC = "2026-08-04T23:59:59Z"


def applied_flux_factor(k_flux: Any, k_flux_self: Any, two_d: Optional[Mapping[str, Any]],
                        version: Any = None, generated_utc: Any = None) -> Dict[str, Any]:
    """``{value, k_flux, k_flux_self, verdict, rule, basis, note}`` for one Stage A result (see the module table).
    ``value`` is None only when neither number is a usable factor."""
    kf, ks = _factor(k_flux, "k_flux"), _factor(k_flux_self, "k_flux_self")
    t = two_d if isinstance(two_d, Mapping) else None
    out: Dict[str, Any] = {"k_flux": kf, "k_flux_self": ks, "note": None}
    vn = _version_n(version)
    if t is None:
        out.update(rule=RULE_NONE, verdict="NOT CHECKED (no 2-D leg)")
        basis = BASIS_K_FLUX
    else:
        raw = str(t.get("verdict") or "").strip().upper()
        new_rule = isinstance(t.get("agreement_check"), Mapping) or (vn is not None and vn >= 3)
        out["rule"] = RULE_END_DEFICIT if new_rule else RULE_MID_PLANE
        if raw == "INVALID":
            out.update(rule=RULE_INVALID, verdict="INVALID")
            basis = BASIS_SELF
        elif raw not in _KNOWN and t.get("k_flux_usable") is False:
            out["verdict"], basis = "UNKNOWN (k_flux_usable False)", BASIS_SELF
            out["note"] = "the run's verdict %r is not known; it marked k_flux not usable" % (t.get("verdict"),)
        elif new_rule:
            out["verdict"] = raw or "NOT CHECKED"
            basis = BASIS_SELF if raw == "FAIL" else BASIS_K_FLUX
        elif raw == "FAIL":
            r = _num(t.get("ratio_3d_mid_over_2d"))
            dated = bool(generated_utc) and str(generated_utc) > OLD_RULE_SOURCE_FIX_UTC
            plausible = (dated and t.get("picard_converged") is True and r is not None
                         and OLD_RULE_RATIO_MIN <= r < OLD_RULE_RATIO_MAX
                         and kf is not None and ks is not None and kf <= ks)
            rs = "%.4f" % r if r is not None else "?"
            if plausible:
                out["verdict"], basis = VERDICT_OLD_NOT_CHECKED, BASIS_K_FLUX
                out["note"] = ("the run's mid-plane check said FAIL (3-D mid / 2-D = %s at the reference stack, 2 %%); "
                               "converged 2-D leg, %g <= ratio < 1 and k_flux <= k_flux_self: end physics at a short "
                               "stack, not a model check, so k_flux is applied" % (rs, OLD_RULE_RATIO_MIN))
            else:
                out["verdict"], basis = VERDICT_OLD_RECOMPUTE, BASIS_SELF
                out["note"] = ("the run's mid-plane check said FAIL (3-D mid / 2-D = %s, 2-D Picard converged: %s, "
                               "generated %s) outside the short-stack signature (or before the 2-D source fix of "
                               "2026-08-04, or undated): k_flux_self is applied; recompute Stage A"
                               % (rs, t.get("picard_converged"), generated_utc or "?"))
        else:
            out["verdict"], basis = (raw or "NOT CHECKED"), BASIS_K_FLUX
    if basis == BASIS_K_FLUX and kf is None and ks is not None:
        basis = BASIS_SELF
        out["note"] = ((out["note"] + "; ") if out["note"] else "") + "k_flux is missing or not a factor: k_flux_self applied"
    elif basis == BASIS_SELF and ks is None and kf is not None:
        basis = BASIS_K_FLUX
        out["note"] = ((out["note"] + "; ") if out["note"] else "") + "k_flux_self is missing or not a factor: k_flux applied"
    out["basis"] = basis
    out["value"] = kf if basis == BASIS_K_FLUX else ks
    return out


#: texts the old writers put on a substituted factor
_SUBST_TEXTS = ("2-d check failed", "raw k_flux not used")


def substitution_of(entry: Optional[Mapping[str, Any]], stack_mm: Optional[float] = None) -> Optional[str]:
    """How a stored end-effect block shows the old silent substitution, or None (not one, or declared).

    - ``"declared"`` blocks (``k_flux_applied`` written by the new writers) are not substitutions: None;
    - the old card / full-passport TEXT: fidelity or labels saying "2-D check failed" / "raw k_flux not used";
    - a full passport ``stage2_3d``: ``k_psi.value`` differs from the own-stack row's real ``k_flux_raw``;
    - a stored entry with ``k_flux == k_flux_self`` whose own-stack ``k_flux_vs_L`` row (the real k_flux) differs from
      the scalar.  Equal scalars WITHOUT such a row are what an INVALID / no-2-D run writes: not a substitution."""
    if (not isinstance(entry, Mapping) or entry.get("k_flux_applied")
            or entry.get("basis") in (BASIS_K_FLUX, BASIS_SELF)):
        return None
    labels = entry.get("labels")
    labels = labels if isinstance(labels, (list, tuple)) else ([] if labels is None else [labels])
    texts = [str(entry.get("fidelity") or "")] + [str(x) for x in labels]
    if any(t_ in x.lower() for x in texts for t_ in _SUBST_TEXTS):
        return ("old writer text (2-D check failed, k_flux_self applied): verify against the full passport "
                "(the full passport may have been a PASS)")
    s2 = entry.get("stage2_3d")
    if isinstance(s2, Mapping):
        kp = s2.get("k_psi") if isinstance(s2.get("k_psi"), Mapping) else {}
        if kp.get("basis"):
            return None
        v = _num(kp.get("value"))
        L = _num(kp.get("L_mm")) or _num(s2.get("L_mm")) or stack_mm
        curve = s2.get("k_psi_curve") if isinstance(s2.get("k_psi_curve"), list) else []
        own = next((r for r in curve
                    if isinstance(r, Mapping) and L is not None and _num(r.get("stack_mm")) is not None
                    and abs(float(r["stack_mm"]) - L) < 1e-6), None)
        raw = _num((own or {}).get("k_flux_raw"))
        if v is not None and raw is not None and abs(v - raw) > 1e-9:
            return "full passport: k_psi %.5f is not the own-stack k_flux %.5f" % (v, raw)
        return None
    kf, ks = _num(entry.get("k_flux")), _num(entry.get("k_flux_self"))
    if kf is None or ks is None or kf != ks:
        return None
    # an inherited table belongs to another reference length: only the entry's own stack_mm may pick its row
    st = _num(entry.get("stack_mm")) or (None if entry.get("inherited") else stack_mm)
    tab = entry.get("k_flux_vs_L") if isinstance(entry.get("k_flux_vs_L"), Mapping) else {}
    rows = [(x, y) for x, y in ((_num(a), _num(b)) for a, b in tab.items()) if x is not None and y is not None]
    if st is None and len(rows) == 1:
        st = rows[0][0]
    real = next((y for x, y in rows if st is not None and abs(x - st) <= 1e-6 * max(1.0, st)), None)
    if real is not None and abs(real - kf) > 1e-5:
        return "k_flux == k_flux_self while the own-stack table row holds the real k_flux %.5f" % real
    return None


def is_silent_substitution(entry: Optional[Mapping[str, Any]], stack_mm: Optional[float] = None) -> bool:
    """True when :func:`substitution_of` finds the old silent substitution in this block."""
    return substitution_of(entry, stack_mm) is not None


def status_of(two_d: Optional[Mapping[str, Any]], applied: Optional[Mapping[str, Any]] = None) -> str:
    """``verified`` / ``unverified`` / ``failed`` / ``invalid`` of a stored 3-D factor.

    With ``applied`` (:func:`applied_flux_factor`'s answer) the status follows what is APPLIED:
    k_flux_self because the check failed (FAIL, old-rule recompute, unknown + not usable) -> failed; k_flux_self because
    the 2-D leg is INVALID or k_flux itself is not a usable factor -> invalid; k_flux with an end-deficit PASS (an
    ``agreement_check`` or passport version >= 3) -> verified; any other k_flux (NOT CHECKED, an old mid-plane PASS,
    no 2-D leg) -> unverified.  Without ``applied``: from ``two_d`` alone, the same way."""
    if applied is None:
        t = two_d if isinstance(two_d, Mapping) else {}
        v = str(t.get("verdict") or "").strip().upper()
        new = isinstance(t.get("agreement_check"), Mapping)
        if v == "INVALID":
            return "invalid"
        if new and v == "FAIL":
            return "failed"
        return "verified" if (new and v == "PASS") else "unverified"
    basis = applied.get("basis")
    verdict = str(applied.get("verdict") or "").upper()
    if basis == BASIS_SELF:
        # FAIL / old-rule recompute / unknown-not-usable: the check said no; anything else forced k_flux_self because
        # there is no usable k_flux or no 2-D ratio
        if verdict == "FAIL" or verdict.startswith("NOT CHECKED (OLD RULE") or verdict.startswith("UNKNOWN"):
            return "failed"
        return "invalid"
    if verdict == "PASS" and applied.get("rule") == RULE_END_DEFICIT:
        return "verified"
    return "unverified"


#: ``k_flux_vs_L_method`` of a stored table whose rows are k_flux_self (the factor applied when the check failed)
K_FLUX_VS_L_SELF_APPLIED = "self_applied"


def applied_table(l_stack_curve: Any, applied: Mapping[str, Any], method: Any = None) -> Dict[str, Any]:
    """The per-length table a store entry carries for what it APPLIES, so the table and the scalar never disagree:
    ``{"k_flux_vs_L": {L: k}, "k_flux_vs_L_method": m, "k_flux_vs_L_raw": {L: raw k} | absent}``.

    The scalar is k_flux -> the rows' k_flux (the run's own table, its ``method`` kept).  The scalar is k_flux_self ->
    the rows' k_flux_self under ``self_applied``, and the raw k_flux rows beside it as ``k_flux_vs_L_raw``.  Rows are
    rounded to 5 decimals as the stores always did; a row without the number is left out."""
    rows = [e for e in (l_stack_curve or []) if isinstance(e, Mapping) and _num(e.get("stack_mm")) is not None]

    def tab(key: str) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for e in rows:
            v = _num(e.get(key))
            if v is not None:
                out[str(e["stack_mm"])] = round(v, 5)
        return out
    raw = tab("k_flux")
    if applied.get("basis") == BASIS_SELF:
        own = tab("k_flux_self")
        out: Dict[str, Any] = {"k_flux_vs_L": own or None}
        if own:
            out["k_flux_vs_L_method"] = K_FLUX_VS_L_SELF_APPLIED
        if raw:
            out["k_flux_vs_L_raw"] = raw
        return out
    out = {"k_flux_vs_L": raw or None}
    if raw and method:
        out["k_flux_vs_L_method"] = str(method)
    return out
