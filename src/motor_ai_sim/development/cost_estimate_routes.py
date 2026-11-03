"""Admin-only routes of the project cost estimate (owner 2026-11-02).

``GET  /api/admin/projects/{id}/cost-estimate``            the estimate (or the defaults when none is stored; an old
                                                           68-line estimate converted on read), its totals, the schema
                                                           and the project's linked motors.  Every route takes
                                                           ``?display_currency=EUR|CNY|USD`` (the UI language's currency,
                                                           used unless the owner chose one in the estimate's
                                                           ``settings.display_currency``): the totals are also
                                                           given in it (``computed.converted``) at the estimate's dated
                                                           exchange rates (``estimate.fx``, ECB or manual)
``PUT /api/admin/projects/{id}/cost-estimate``            save (optimistic ``revision``; 409 with the stored one); every
                                                           automatic ``dhl`` shipping line is recomputed from the
                                                           iteration's motors on each save
``POST /api/admin/projects/{id}/cost-estimate/suggest``    fill missing lines from built motors (scaled by active mass)
                                                           and other projects' estimates
``POST /api/admin/projects/{id}/cost-estimate/suggestions`` what suggest WOULD fill in the empty cells of the posted estimate
                                                           (the table's grey placeholders; nothing is saved)
                                                           and, as ``auto``, what the automatic DHL lines would become
``POST /api/admin/projects/{id}/cost-estimate/snapshot``   freeze the estimate with a note and the "what changed" diff
``GET  /api/admin/projects/{id}/cost-estimate/snapshots``  the frozen snapshots

Global admins only (``auth.require_admin``).  Never a project customer, a project viewer, a project engineer who is not an
admin, the factory (OEM) or an MCP agent - an agent call acting for an admin is refused as well (owner rule: no price or
cost through the MCP)."""
from __future__ import annotations

import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from motor_ai_sim import auth
from motor_ai_sim.development import cost_estimate as CE
from motor_ai_sim.development import cost_estimate_fx as FX
from motor_ai_sim.development import cost_estimate_sources as SRC
from motor_ai_sim.development import cost_estimate_store as CS
from motor_ai_sim.development import store

router = APIRouter(prefix="/api/admin/projects", tags=["admin-cost-estimate"])


def _admin(admin_user: dict = Depends(auth.require_admin)) -> dict:
    if auth._AGENT_USER.get() is not None:
        # an in-process MCP call acting for an admin key owner: the cost estimate never leaves through the MCP
        raise HTTPException(status_code=403, detail="The cost estimate is not available to agents.")
    return admin_user


def _email(user: dict) -> str:
    return str(user.get("email") or "admin").strip().lower()


def _project(project_id: str) -> dict:
    project = store.get_project(project_id) if store.valid_id(project_id) else None
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _linked(project: dict) -> list:
    return [{"die": str(m.get("die")), "config": str(m.get("config")), "status": str(m.get("status") or "current")}
            for m in project.get("linked_motors") or [] if isinstance(m, dict) and m.get("die") and m.get("config")]


def _compute(estimate: dict, schema: dict, display: Optional[str]) -> dict:
    try:
        return CE.compute(estimate, schema, display)
    except CE.EstimateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


def _payload(project: dict, estimate: dict, schema: dict, *, stored: bool, display: Optional[str] = None,
             fx_status: Optional[dict] = None, **extra: Any) -> Dict[str, Any]:
    return {"estimate": estimate, "computed": _compute(estimate, schema, display), "schema": schema, "stored": stored,
            "fx_status": fx_status or _fx_status(estimate, None), "linked_motors": _linked(project), **extra}


def _today() -> str:
    return datetime.date.today().isoformat()


def _fx_status(estimate: dict, live_status: Optional[dict]) -> dict:
    """What the web says about the rates: ``state`` ``ok`` (ECB rates of ``date``, fetched in the last day), ``stale`` (the ECB
    could not be reached: the last stored rates of ``date`` are kept), ``none`` (no ECB rate is known: a manual rate or the
    per-currency totals only); ``manual`` lists the currencies whose rate is the owner's."""
    fx = estimate.get("fx") or {}
    ecb = fx.get("ecb") or {}
    state = (live_status or {}).get("state") or "ok"
    if not ecb:
        state = "none"
    elif state == "none":
        state = "stale"
    return {"state": state, "date": ecb.get("date"), "manual": sorted((fx.get("manual") or {}).keys()),
            "error": (live_status or {}).get("error")}


def _with_live_fx(estimate: dict, schema: dict) -> tuple[dict, dict]:
    """The estimate with the rates refreshed from the ECB (cached for a day; a failure keeps the stored ones) - in memory only,
    it is stored by the next save / suggest / snapshot.  Returns ``(estimate, fx status)``."""
    live, status = FX.live_rates()
    out = dict(estimate)
    out["fx"] = CE.merge_fx(estimate.get("fx"), (estimate.get("fx") or {}).get("manual"), live, schema, _today())
    return out, _fx_status(out, status)


def _read(project: dict, stored: Any, schema: dict) -> dict:
    """A stored document of any shape as the 4-line estimate (the old 68-line shape is converted, see
    ``cost_estimate.migrate_v1``); never raises."""
    cached = FX.cached_rates()                           # no network here: an old non-EUR line is converted at the stored or cached rate
    return CE.from_stored(stored, schema, project["id"], linked_motors=project.get("linked_motors") or [], today=_today(),
                          fallback_rates=(cached or {}).get("rates"))


def _current(project: dict, schema: dict) -> tuple[dict, bool]:
    stored = CS.load(project["id"])
    if stored is None:
        return CE.default_estimate(schema, project["id"], linked_motors=project.get("linked_motors") or []), False
    return _read(project, stored, schema), True


async def _body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Expected JSON object") from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")
    return body


def _revision(body: dict) -> int:
    try:
        return int(body.get("revision"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="revision (the revision the change is based on) is required") from None


def _conflict(project: dict, schema: dict, current: Any) -> HTTPException:
    est = _read(project, current, schema) if isinstance(current, dict) else None
    return HTTPException(status_code=409, detail={"code": "revision_conflict",
                                                  "message": "The cost estimate was changed meanwhile; reload it.",
                                                  "revision": (est or {}).get("revision", 0)})


@router.get("/{project_id}/cost-estimate")
def get_estimate(project_id: str, response: Response, display_currency: Optional[str] = Query(None),
                 admin_user: dict = Depends(_admin)):
    response.headers["Cache-Control"] = "no-store"
    project = _project(project_id)
    schema = CE.load_schema()
    FX.live_rates()                                      # refresh the ECB cache first: old non-EUR lines are converted at it
    estimate, stored = _current(project, schema)
    estimate, status = _with_live_fx(estimate, schema)
    return _payload(project, estimate, schema, stored=stored, display=display_currency, fx_status=status)


@router.put("/{project_id}/cost-estimate")
async def put_estimate(project_id: str, request: Request, response: Response, display_currency: Optional[str] = Query(None),
                       admin_user: dict = Depends(_admin)):
    response.headers["Cache-Control"] = "no-store"
    project = _project(project_id)
    schema = CE.load_schema()
    body = await _body(request)
    expected = _revision(body)
    try:
        candidate = CE.normalize_estimate(body, schema, project_id)
    except CE.EstimateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    live, live_status = FX.live_rates()                  # before the file lock: it may touch the network
    manual = (candidate.get("fx") or {}).get("manual") or {}
    # the automatic DHL lines follow the iteration's motors on EVERY save (owner 2026-11-03); the motor mass is read before the lock
    auto = any((it["lines"]["shipping"].get("status") == "filled" and it["lines"]["shipping"].get("source") == "dhl")
               for it in candidate["iterations"])
    ship = SRC.ship_data(candidate, schema) if auto else None
    auto_changes: list = []

    def build(previous):
        # the rates follow the ECB (the stored ones are kept when it cannot be reached); only the owner's manual rates
        # come from the request
        old_fx = (_read(project, previous, schema) if previous else {}).get("fx")
        doc = {**candidate, "fx": CE.merge_fx(old_fx, manual, live, schema, _today())}
        if ship is not None:
            doc, changes = CE.refresh_dhl(doc, schema, ship["motor_mass_kg"], ship["motor_mass_basis"], today=_today())
            auto_changes[:] = changes
        return doc

    try:
        previous, saved = CS.save(project_id, expected, build, _email(admin_user))
    except CS.RevisionConflict as exc:
        raise _conflict(project, schema, exc.current) from None
    old = _read(project, previous, schema) if previous else None
    changes = CE.diff(old, saved)
    CS.audit(project_id, _email(admin_user), "cost_estimate.update",
             {"revision": saved["revision"], "changes": len(changes), "paths": [c["path"] for c in changes][:60],
              "auto_dhl": len(auto_changes)})
    return _payload(project, saved, schema, stored=True, display=display_currency, fx_status=_fx_status(saved, live_status),
                    changes=changes)


@router.post("/{project_id}/cost-estimate/suggest")
async def suggest_estimate(project_id: str, request: Request, response: Response, display_currency: Optional[str] = Query(None),
                           admin_user: dict = Depends(_admin)):
    response.headers["Cache-Control"] = "no-store"
    project = _project(project_id)
    schema = CE.load_schema()
    body = await _body(request)
    expected = _revision(body)
    live, live_status = FX.live_rates()
    current, _stored = _current(project, schema)
    if int(current.get("revision") or 0) != expected:
        raise _conflict(project, schema, current)
    data = SRC.gather(project_id, current, schema)
    today = _today()
    report: Dict[str, Any] = {}

    def build(previous):
        base = _read(project, previous, schema) if previous else current
        base = {**base, "fx": CE.merge_fx(base.get("fx"), (base.get("fx") or {}).get("manual"), live, schema, today)}
        new, rep = CE.suggest(base, schema, data, today=today, refresh=bool(body.get("refresh")))      # the analog's money -> EUR at these rates
        report.update(rep)
        return new

    try:
        previous, saved = CS.save(project_id, expected, build, _email(admin_user))
    except CS.RevisionConflict as exc:
        raise _conflict(project, schema, exc.current) from None
    old = _read(project, previous, schema) if previous else current
    changes = CE.diff(old, saved)
    CS.audit(project_id, _email(admin_user), "cost_estimate.suggest",
             {"revision": saved["revision"], "filled": report.get("filled_count", 0), "refresh": bool(body.get("refresh")),
              "analogs": {k: v.get("ref") for k, v in (report.get("analogs") or {}).items()}})
    return _payload(project, saved, schema, stored=True, display=display_currency, fx_status=_fx_status(saved, live_status),
                    changes=changes, report=report)


@router.post("/{project_id}/cost-estimate/suggestions")
async def preview_suggestions(project_id: str, request: Request, response: Response, admin_user: dict = Depends(_admin)):
    """What ``suggest`` would put in the EMPTY cells of the estimate on screen (the body), for the table's grey placeholders:
    nothing is saved and nothing is audited.  The rates are the stored / live ECB ones plus the manual ones of the body."""
    response.headers["Cache-Control"] = "no-store"
    project = _project(project_id)
    schema = CE.load_schema()
    body = await _body(request)
    try:
        candidate = CE.normalize_estimate(body, schema, project_id)
    except CE.EstimateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    live, _status = FX.live_rates()
    stored, _ = _current(project, schema)
    candidate["fx"] = CE.merge_fx(stored.get("fx"), (candidate.get("fx") or {}).get("manual") or {}, live, schema, _today())
    data = SRC.gather(project_id, candidate, schema)
    after, report = CE.suggest(candidate, schema, data, today=_today())
    refreshed, _changes = CE.refresh_dhl(candidate, schema, data.get("motor_mass_kg"), data.get("motor_mass_basis") or "")
    return {"cells": CE.suggestion_cells(candidate, after), "auto": CE.auto_cells(candidate, refreshed),
            "motor_mass_kg": data.get("motor_mass_kg"),
            "motor_mass_basis": data.get("motor_mass_basis") or "",
            "analogs": report.get("analogs"), "notes": report.get("notes")}


@router.post("/{project_id}/cost-estimate/snapshot")
async def snapshot_estimate(project_id: str, request: Request, admin_user: dict = Depends(_admin)):
    project = _project(project_id)
    schema = CE.load_schema()
    body = await _body(request)
    note = str(body.get("note") or "").strip()[:500]
    estimate, stored = _current(project, schema)
    if not stored:
        raise HTTPException(status_code=409, detail="Save the cost estimate before freezing it.")
    if "revision" in body and _revision(body) != int(estimate.get("revision") or 0):
        raise _conflict(project, schema, estimate)
    estimate, _status = _with_live_fx(estimate, schema)      # the snapshot keeps the rates its numbers were computed with
    computed = _compute(estimate, schema, str(body.get("display_currency") or "").strip() or None)

    def make(previous, n):
        prev_est = (previous or {}).get("estimate")
        prev_est = _read(project, prev_est, schema) if isinstance(prev_est, dict) else None   # an old-shape snapshot too
        try:
            changes = CE.diff(prev_est, estimate, schema)
        except CE.EstimateError:
            changes = []
        return {"id": f"{n:04d}", "n": n, "note": note, "created_at": CS.now(), "created_by": _email(admin_user),
                "revision": estimate.get("revision"), "parent": (previous or {}).get("id"), "estimate": estimate,
                "totals": {k: computed[k] for k in ("subtotal", "contingency", "total", "currencies", "converted", "missing_count",
                                                    "weeks_total")},
                "iterations": [{k: it[k] for k in it if k != "lines"} for it in computed["iterations"]],
                "diff": changes if previous else []}

    snap = CS.add_snapshot(project_id, make)
    CS.audit(project_id, _email(admin_user), "cost_estimate.snapshot",
             {"snapshot": snap["id"], "revision": snap["revision"], "changes": len(snap["diff"])})
    return {"snapshot": snap}


@router.get("/{project_id}/cost-estimate/snapshots")
def list_snapshots(project_id: str, response: Response, admin_user: dict = Depends(_admin)):
    response.headers["Cache-Control"] = "no-store"
    _project(project_id)
    rows = [{k: v for k, v in s.items() if k != "estimate"} for s in CS.list_snapshots(project_id)]
    return {"snapshots": list(reversed(rows))}
