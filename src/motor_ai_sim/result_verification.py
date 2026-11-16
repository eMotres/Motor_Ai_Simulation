"""Is a finished transient result fit to be stored as a duty's result?

The audit of the CIANO28 80_12 / L12-F475 water duties (2026-11-16) found runs that were stored although their own
checks said the torque was not trustworthy: the rotor-side and the stator-side Coulomb gap rings disagreed by 100 % of
the ripple scale and by 90 % of the mean torque.  The checks existed; nothing acted on them.

``assess`` reads only numbers the run already carries and returns a verdict; it never changes a result.

* ``ok``      every available check passes.
* ``warn``    a check fails the solver's own gate (``SELF_CHECK_GATE``, 5 % of the ripple scale): the result is flagged
              "not verified" and stored with that flag.
* ``reject``  the ring disagreement is gross (more than half the ripple scale, or the two rings' MEAN torques differ by
              more than a fifth of max(|mean|, ripple pp)): refused unless the caller asks explicitly to store it.

Missing inputs are never read as a pass or a fail: a check that cannot be evaluated is listed under ``unchecked``.
"""
from __future__ import annotations

from typing import Any, Optional

WARN_REL_TO_SCALE = 0.05      # = virtual_work_torque.SELF_CHECK_GATE
REJECT_REL_TO_SCALE = 0.5
REJECT_RING_MEAN_DIFF = 0.2
WARN_POWER_RESIDUAL_REL = 0.10


def _num(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float("inf") else None


def _dig(d: Any, *path: str) -> Any:
    for k in path:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _is_transient(summary: Optional[dict], payload: Optional[dict]) -> bool:
    return any(_dig(src, k) is not None
               for src in (summary, payload)
               for k in ("ripple_self_check_rel", "coulomb_torque"))


def assess(summary: Optional[dict], payload: Optional[dict] = None) -> dict:
    """Verdict for one run: ``{"status", "verified", "reasons", "unchecked", "values"}``."""
    summary = summary if isinstance(summary, dict) else {}
    payload = payload if isinstance(payload, dict) else {}
    if not _is_transient(summary, payload):
        return {"status": "ok", "verified": None, "reasons": [], "unchecked": ["not a transient run"],
                "values": {}}
    chk = _dig(payload, "coulomb_torque", "layer_self_check") or {}
    scale = _num(summary.get("ripple_self_check_rel"))
    if scale is None:
        scale = _num(chk.get("rel_to_ripple_scale"))
    if scale is None:
        scale = _num(_dig(payload, "gap_refinement", "self_check_rel_to_ripple_scale"))
    ct = _dig(payload, "coulomb_torque") or {}
    md, tav, pp = _num(chk.get("mean_diff_Nm")), _num(ct.get("T_avg_coulomb_Nm")), _num(ct.get("T_ripple_pp_coulomb"))
    to_mean = (abs(md) / max(abs(tav or 0.0), abs(pp or 0.0), 1e-12)
               if md is not None and (tav is not None or pp is not None) else None)
    resid = _num(_dig(payload, "power_balance", "residual_rel"))
    values = {"ring_disagreement_rel_to_ripple_scale": scale, "ring_mean_diff_rel_to_torque_scale": to_mean,
              "power_balance_residual_rel": resid}
    unchecked = [k for k, v in values.items() if v is None]
    reasons, status = [], "ok"
    if scale is not None and scale > WARN_REL_TO_SCALE:
        status = "warn"
        reasons.append("the two gap rings disagree by %.0f %% of the ripple scale (gate %.0f %%)"
                       % (100 * scale, 100 * WARN_REL_TO_SCALE))
    if resid is not None and resid > WARN_POWER_RESIDUAL_REL:
        status = "warn"
        reasons.append("the field power balance is off by %.0f %%" % (100 * resid))
    if ((scale is not None and scale > REJECT_REL_TO_SCALE)
            or (to_mean is not None and to_mean > REJECT_RING_MEAN_DIFF)):
        status = "reject"
        reasons.append("gross gap-ring disagreement (%s) - the torque cannot be trusted"
                       % ", ".join(s for s in (
                           "%.0f %% of the ripple scale" % (100 * scale) if scale is not None else "",
                           "ring means differ by %.0f %% of the torque" % (100 * to_mean) if to_mean is not None else "") if s))
    return {"status": status, "verified": status == "ok", "reasons": reasons, "unchecked": unchecked,
            "values": values}
