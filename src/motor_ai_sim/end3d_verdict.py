"""Which flux factor a Stage A result applies: the REAL k_flux or k_flux_self, decided once from its 2-D verdict.

A Stage A run (``simulation.static3d.end_effect.run_stage_a``) gives two numbers at its own stack:

* ``k_flux`` = axial mean of the gap fundamental / the 2-D fundamental - what multiplies a 2-D flux, EMF, psi_PM;
* ``k_flux_self`` = the same mean / the 3-D MID-PLANE value - the end effect with the mid-plane 3-D/2-D
  difference taken out.

The passport writers used to put ``k_flux_self`` in the place of ``k_flux`` whenever the run's 2-D check said FAIL, and
the stored scalar then carried no mark of it (a stored entry with ``k_flux == k_flux_self`` exactly is that mark).  The
old check compared the mid-plane with 2-D at the reference stack within 2 %, which a short stack fails by END PHYSICS
(the mid-plane itself sits below 2-D: 0.957 at 12 mm on a 28-pole 80 mm machine, 0.9996 at 48 mm).  So:

====================================  ===================================  ==========================================
verdict (``two_d``)                   applied                               why
====================================  ===================================  ==========================================
PASS (either rule)                    k_flux                                the two models agree
NOT CHECKED (end-deficit rule)        k_flux                                one length: no model offset can be seen
FAIL (end-deficit rule)               k_flux_self                           the deficit grows with L: model disagreement
INVALID (2-D fundamental 0 / NaN)     k_flux_self                           no 2-D ratio exists
FAIL (old mid-plane rule)             k_flux, verdict "NOT CHECKED          the mid-plane test is not a model check
                                      (old mid-plane rule)"
no 2-D leg                            k_flux (= k_flux_self in that run)    nothing to compare
====================================  ===================================  ==========================================

The end-deficit rule is recognised by ``two_d["agreement_check"]`` (``end_effect.end_deficit_check``).  Every answer
carries ``value`` (what to apply), the real ``k_flux``, ``k_flux_self``, ``verdict``, ``rule`` and ``basis``, so a
writer stores the real k_flux beside the applied one and never substitutes silently.
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


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def applied_flux_factor(k_flux: Any, k_flux_self: Any, two_d: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """``{value, k_flux, k_flux_self, verdict, rule, basis, note}`` for one Stage A result (see the module table).
    ``value`` is None only when neither number is usable."""
    kf, ks = _num(k_flux), _num(k_flux_self)
    t = two_d if isinstance(two_d, Mapping) else None
    out: Dict[str, Any] = {"k_flux": kf, "k_flux_self": ks, "note": None}
    if t is None:
        out.update(rule=RULE_NONE, verdict="NOT CHECKED (no 2-D leg)")
        basis = BASIS_K_FLUX
    else:
        raw = str(t.get("verdict") or "").strip().upper()
        new_rule = isinstance(t.get("agreement_check"), Mapping)
        out["rule"] = RULE_END_DEFICIT if new_rule else RULE_MID_PLANE
        if raw == "INVALID":
            out["verdict"], basis = "INVALID", BASIS_SELF
        elif new_rule:
            out["verdict"] = raw or "NOT CHECKED"
            basis = BASIS_SELF if raw == "FAIL" else BASIS_K_FLUX
        elif raw == "FAIL":
            out["verdict"], basis = VERDICT_OLD_NOT_CHECKED, BASIS_K_FLUX
            r = _num(t.get("ratio_3d_mid_over_2d"))
            out["note"] = ("the run's mid-plane check said FAIL (3-D mid / 2-D = %s at the reference stack, 2 %%); "
                           "that is end physics at a short stack, not a model check, so k_flux is applied"
                           % ("%.4f" % r if r is not None else "?"))
        else:
            out["verdict"], basis = (raw or "NOT CHECKED"), BASIS_K_FLUX
    if basis == BASIS_K_FLUX and kf is None:
        basis = BASIS_SELF
    if basis == BASIS_SELF and ks is None:
        basis = BASIS_K_FLUX if kf is not None else basis
    out["basis"] = basis
    out["value"] = kf if basis == BASIS_K_FLUX else ks
    return out


def is_silent_substitution(entry: Optional[Mapping[str, Any]]) -> bool:
    """True for a stored end-effect entry whose scalar ``k_flux`` equals ``k_flux_self`` exactly and that does not say
    so (no ``k_flux_basis``): the mark of the old writers' silent substitution.  A run without a 2-D leg also has
    k_flux == k_flux_self; such an entry cannot be told apart from the scalars alone and is reported too (the
    list script shows the own-stack table value, which then also equals the scalar)."""
    if not isinstance(entry, Mapping) or entry.get("k_flux_basis"):
        return False
    kf, ks = _num(entry.get("k_flux")), _num(entry.get("k_flux_self"))
    return kf is not None and ks is not None and kf == ks
