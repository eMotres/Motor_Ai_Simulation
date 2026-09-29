"""``/api/history`` — browse and reuse the persistent run history.

One generic surface over every :mod:`motor_ai_sim.run_history` kind a route
has wired in (today: ``mechanical.rotor_stress``; see that module's
``_ROTOR_STRESS_HISTORY`` for the reference integration and
``run_history.py``'s module docstring for the layer this sits on top of).

Not gated (``auth._GATED`` is an allowlist of genuinely EXPENSIVE endpoints —
live FEM, optimisation, report builds).  Listing, loading or deleting an
already-computed row costs nothing: a load is a pickle read, never a solve.
Workspace isolation needs no extra code here — every ``RunHistory`` call
resolves ``workspace.root()`` for whichever caller the request middleware
already identified, exactly like every other per-workspace store in this
app (``/api/jobs`` is the same shape of "not gated, isolated by the
workspace resolver alone").
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from motor_ai_sim import run_history as _RH

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/history", tags=["history"])


def _kinds_to_search(kind: Optional[str]) -> List[str]:
    if kind:
        return [str(kind)]
    return _RH.known_kinds()


def _row_view(r: Dict[str, Any]) -> Dict[str, Any]:
    """Metadata only — never the payload, which can be MB-sized (a field map).
    ``current`` says whether THIS build would still serve it (see
    ``run_history``'s code_version gating)."""
    return {
        "key": r.get("key"), "kind": r.get("kind"),
        "computed_at": r.get("computed_at"), "summary": r.get("summary"),
        "params": r.get("params"),
        "current": r.get("code_version") == _RH.code_version(),
    }


@router.get("")
def list_history(kind: Optional[str] = Query(
        default=None, description="one solve kind, e.g. "
                                  "'mechanical.rotor_stress'; omitted = every "
                                  "kind this build has wired in"),
                 limit: int = Query(default=_RH.DEFAULT_CAP, ge=1, le=50),
                 ) -> Dict[str, Any]:
    """This caller's own history, newest first, grouped by kind."""
    kinds = _kinds_to_search(kind)
    if kind and kind not in _RH.known_kinds():
        raise HTTPException(status_code=404, detail={
            "error": f"unknown history kind {kind!r}",
            "known_kinds": _RH.known_kinds()})
    return {"kinds": {k: [_row_view(r) for r in _RH.history_for(k).list(limit=limit)]
                      for k in kinds}}


def _find_row(key: str, kind: Optional[str]):
    """``(kind, row)`` for the first kind whose index contains ``key`` —
    VERSION-BLIND (a stale-build row is still findable, for delete and for a
    load attempt that wants to explain itself rather than 404).  ``(None,
    None)`` when no wired kind's index has it."""
    for k in _kinds_to_search(kind):
        for r in _RH.history_for(k).list():
            if r.get("key") == key:
                return k, r
    return None, None


@router.post("/{key}/load")
def load_history(key: str, kind: Optional[str] = Query(default=None),
                 ) -> Dict[str, Any]:
    """Serve entry ``key`` as though it had just been solved — the History
    popover's click.  Delegates to whatever the owning route registered with
    ``run_history.register_loader``; a kind with none returns the stored
    payload verbatim, stamped ``served_from_history``.
    """
    found_kind, row = _find_row(key, kind)
    if row is None:
        raise HTTPException(status_code=404, detail={
            "error": "no history entry with that key"})
    if row.get("code_version") != _RH.code_version():
        raise HTTPException(status_code=409, detail={
            "error": "this point is from an older build and was not "
                    "reloaded — recompute it",
            "computed_at": row.get("computed_at")})
    hit = _RH.history_for(found_kind).get(key)
    if hit is None:                      # payload went missing between the
        raise HTTPException(status_code=404, detail={              # two reads
            "error": "no history entry with that key"})
    loader = _RH.loader_for(found_kind)
    if loader is not None:
        try:
            return loader(hit["entry"], hit["payload"])
        except HTTPException:
            raise
        except Exception as exc:                           # noqa: BLE001
            log.exception("history load failed for %s/%s", found_kind, key)
            raise HTTPException(status_code=500,
                                detail=f"{type(exc).__name__}: {exc}")
    out = hit["payload"]
    if isinstance(out, dict):
        out = dict(out)
        out["served_from_history"] = True
        out["computed_at"] = hit["entry"].get("computed_at")
        out["history_key"] = key
    return out


@router.delete("/{key}")
def delete_history(key: str, kind: Optional[str] = Query(default=None),
                   ) -> Dict[str, Any]:
    found_kind, row = _find_row(key, kind)
    if row is None:
        raise HTTPException(status_code=404, detail={
            "error": "no history entry with that key"})
    ok = _RH.history_for(found_kind).delete(key)
    return {"deleted": bool(ok), "key": key, "kind": found_kind}
