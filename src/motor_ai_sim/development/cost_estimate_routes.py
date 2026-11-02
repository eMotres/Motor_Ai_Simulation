"""Admin-only routes of the project cost estimate (owner 2026-11-02).

``GET  /api/admin/projects/{id}/cost-estimate``            the estimate (or the defaults when none is stored; an old
                                                           68-line estimate converted on read), its totals, the schema
                                                           and the project's linked motors
``PUT  /api/admin/projects/{id}/cost-estimate``            save (optimistic ``revision``; 409 with the stored one)
``POST /api/admin/projects/{id}/cost-estimate/suggest``    fill missing lines from built motors (scaled by active mass)
                                                           and other projects' estimates
``POST /api/admin/projects/{id}/cost-estimate/snapshot``   freeze the estimate with a note and the "what changed" diff
``GET  /api/admin/projects/{id}/cost-estimate/snapshots``  the frozen snapshots

Global admins only (``auth.require_admin``).  Never a project customer, a project viewer, a project engineer who is not an
admin, the factory (OEM) or an MCP agent - an agent call acting for an admin is refused as well (owner rule: no price or
cost through the MCP)."""
from __future__ import annotations

import datetime
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from motor_ai_sim import auth
from motor_ai_sim.development import cost_estimate as CE
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


def _payload(project: dict, estimate: dict, schema: dict, *, stored: bool, **extra: Any) -> Dict[str, Any]:
    return {"estimate": estimate, "computed": CE.compute(estimate, schema), "schema": schema, "stored": stored,
            "linked_motors": _linked(project), **extra}


def _today() -> str:
    return datetime.date.today().isoformat()


def _read(project: dict, stored: Any, schema: dict) -> dict:
    """A stored document of any shape as the 4-line estimate (the old 68-line shape is converted, see
    ``cost_estimate.migrate_v1``); never raises."""
    return CE.from_stored(stored, schema, project["id"], linked_motors=project.get("linked_motors") or [], today=_today())


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
def get_estimate(project_id: str, response: Response, admin_user: dict = Depends(_admin)):
    response.headers["Cache-Control"] = "no-store"
    project = _project(project_id)
    schema = CE.load_schema()
    estimate, stored = _current(project, schema)
    return _payload(project, estimate, schema, stored=stored)


@router.put("/{project_id}/cost-estimate")
async def put_estimate(project_id: str, request: Request, response: Response, admin_user: dict = Depends(_admin)):
    response.headers["Cache-Control"] = "no-store"
    project = _project(project_id)
    schema = CE.load_schema()
    body = await _body(request)
    expected = _revision(body)
    try:
        candidate = CE.normalize_estimate(body, schema, project_id)
    except CE.EstimateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    try:
        previous, saved = CS.save(project_id, expected, lambda _previous: candidate, _email(admin_user))
    except CS.RevisionConflict as exc:
        raise _conflict(project, schema, exc.current) from None
    old = _read(project, previous, schema) if previous else None
    changes = CE.diff(old, saved)
    CS.audit(project_id, _email(admin_user), "cost_estimate.update",
             {"revision": saved["revision"], "changes": len(changes), "paths": [c["path"] for c in changes][:60]})
    return _payload(project, saved, schema, stored=True, changes=changes)


@router.post("/{project_id}/cost-estimate/suggest")
async def suggest_estimate(project_id: str, request: Request, response: Response, admin_user: dict = Depends(_admin)):
    response.headers["Cache-Control"] = "no-store"
    project = _project(project_id)
    schema = CE.load_schema()
    body = await _body(request)
    expected = _revision(body)
    current, _stored = _current(project, schema)
    if int(current.get("revision") or 0) != expected:
        raise _conflict(project, schema, current)
    data = SRC.gather(project_id, current, schema)
    today = _today()
    report: Dict[str, Any] = {}

    def build(previous):
        base = _read(project, previous, schema) if previous else current
        new, rep = CE.suggest(base, schema, data, today=today, refresh=bool(body.get("refresh")))
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
    return _payload(project, saved, schema, stored=True, changes=changes, report=report)


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
    computed = CE.compute(estimate, schema)

    def make(previous, n):
        prev_est = (previous or {}).get("estimate")
        prev_est = _read(project, prev_est, schema) if isinstance(prev_est, dict) else None   # an old-shape snapshot too
        try:
            changes = CE.diff(prev_est, estimate, schema)
        except CE.EstimateError:
            changes = []
        return {"id": f"{n:04d}", "n": n, "note": note, "created_at": CS.now(), "created_by": _email(admin_user),
                "revision": estimate.get("revision"), "parent": (previous or {}).get("id"), "estimate": estimate,
                "totals": {k: computed[k] for k in ("subtotal", "contingency", "total", "currencies", "missing_count")},
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
