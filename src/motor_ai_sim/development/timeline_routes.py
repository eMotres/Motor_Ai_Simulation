"""Routes and storage of the project TIMELINE (owner 2026-11-03); the rules are in ``timeline``.

Customer (read-only):

``GET /api/projects/{id}/timeline``   the whitelisted timeline (``timeline.customer_view``): plan and forecast per lane,
                                      computed on every read from the stored plan and the production of the motor.

Access, the rule of the quote (``quote_routes``): members of THAT project (any project role) and global admins; a global
engineer who is not a member, a member of another project, the factory (OEM) of the production: 404 "Timeline not found",
the same answer as an unknown project or a project without a timeline; no session 401; an MCP agent 403;
``Cache-Control: no-store``.  The factory keeps updating the part dates in Production and never sees this timeline; the
customer never sees the factory (no part, supplier, price, BOM row or contact leaves here).

Admin (global admins only, never an MCP agent):

``GET  /api/admin/projects/{id}/timeline``            the stored timeline (or an empty one), the computed lanes (live
                                                      plan, baseline, forecast), the customer view, the linked motors,
                                                      which production drives the forecast, the last audit rows
``PUT  /api/admin/projects/{id}/timeline``            save ``{revision, production, iterations}`` (optimistic revision,
                                                      409 with the stored one)
``POST /api/admin/projects/{id}/timeline/seed``       "Create from cost estimate": iterations and plan weeks from the
                                                      current estimate (start dates, done dates and notes kept)
``POST /api/admin/projects/{id}/timeline/baseline``   freeze the current plan as the baseline

Every change is audited in ``<project>/timeline/audit.jsonl``."""
from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from motor_ai_sim import auth, json_store
from motor_ai_sim.development import access, store
from motor_ai_sim.development import timeline as TL

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["development-timeline"])
admin_router = APIRouter(prefix="/api/admin/projects", tags=["admin-timeline"])

TIMELINE = "timeline/timeline.json"
AUDIT = "timeline/audit.jsonl"
_NOT_FOUND = "Timeline not found"
_NO_STORE = {"Cache-Control": "no-store"}


# ── storage ─────────────────────────────────────────────────────────────────────────────────────────────────────────

class RevisionConflict(Exception):
    def __init__(self, current: Optional[dict]):
        super().__init__("timeline changed meanwhile")
        self.current = current


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.date.today().isoformat()


def load(project_id: str) -> Optional[dict]:
    return TL.from_stored(store.load_json(project_id, TIMELINE, None))


def save(project_id: str, expected_revision: int, build, actor: str) -> tuple:
    """Under the file lock: refuse a stale ``expected_revision`` (``RevisionConflict``), else write ``build(previous)`` as
    revision + 1.  Returns ``(previous, saved)``."""
    store.ensure_writable()
    path = store._file(project_id, TIMELINE)
    path.parent.mkdir(parents=True, exist_ok=True)
    seen: Dict[str, Any] = {}

    def mutate(doc: Any) -> dict:
        previous = TL.from_stored(doc)
        current_rev = int((previous or {}).get("revision") or 0)
        if int(expected_revision) != current_rev:
            raise RevisionConflict(previous)
        new = build(previous)
        new["revision"] = current_rev + 1
        new["updated_at"] = _now()
        new["updated_by"] = actor
        seen["previous"] = previous
        return new

    saved = json_store.mutate_json(path, mutate, default={})
    return seen.get("previous"), TL.from_stored(saved)


def audit(project_id: str, actor: str, action: str, details: Optional[dict] = None) -> None:
    store.append_jsonl(project_id, AUDIT, {"time": _now(), "actor_email": str(actor or "").lower(), "action": action,
                                           "details": details or {}})


def read_audit(project_id: str, limit: int = 20) -> List[dict]:
    import json
    out: List[dict] = []
    try:
        for line in store._file(project_id, AUDIT).read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append(row)
    except OSError:
        pass
    return out[-limit:][::-1]


# ── the production of the motor ─────────────────────────────────────────────────────────────────────────────────────

def _linked(project: dict) -> List[dict]:
    return [{"die": str(m.get("die")), "config": str(m.get("config")), "status": str(m.get("status") or "current")}
            for m in project.get("linked_motors") or [] if isinstance(m, dict) and m.get("die") and m.get("config")]


def _load_production(die: str, config: str) -> Optional[dict]:
    from motor_ai_sim.production import access as PA
    try:
        prod = PA.load_production(die, config)
    except ValueError:
        return None
    return prod if isinstance(prod, dict) and prod.get("die") else None


def production_for(project: dict, doc: Optional[dict]) -> Optional[dict]:
    """``{"die", "config", "production"}`` of the motor whose production drives the forecast: the admin's pick when it is
    still linked to the project, else the first ``current`` linked motor with a production, else any linked one with a
    production; ``None`` when there is none."""
    linked = _linked(project)
    pick = (doc or {}).get("production")
    candidates: List[dict] = []
    if pick and any(m["die"] == pick["die"] and m["config"] == pick["config"] for m in linked):
        candidates.append(pick)
    else:
        candidates += [m for m in linked if m["status"] == "current"] + [m for m in linked if m["status"] != "current"]
    for m in candidates:
        prod = _load_production(m["die"], m["config"])
        if prod is not None:
            return {"die": str(prod.get("die")), "config": str(prod.get("config") or m["config"]), "production": prod}
    return None


def production_progress(die: str, config: str, production: dict, today: str) -> Dict[str, Optional[dict]]:
    """``{"motor": list_progress, "tooling": list_progress}`` from the Production module's procurement rows (the motor BOM
    and the tooling list).  Only dates and received flags are read; nothing else leaves this function."""
    from motor_ai_sim.production import routes as PR
    from motor_ai_sim.production import store as PS
    raw = {str(r.get("part_id")): r for r in (PS.load_file(die, config, "procurement.json") or {}).get("rows", [])
           if isinstance(r, dict) and r.get("part_id")}
    out: Dict[str, Optional[dict]] = {}
    for lid, list_name in TL.PRODUCTION_LANES.items():
        try:
            rows, _summary = PR._get_procurement_data(die, config, production, today=today, list_name=list_name)
        except Exception:                                   # noqa: BLE001 - a broken list never breaks the timeline
            log.warning("timeline: procurement of %s/%s (%s) unreadable", die, config, list_name or "motor", exc_info=True)
            out[lid] = None
            continue
        facts = []
        for row in rows:
            r = raw.get(str(row.get("part_id")), {})
            facts.append({"received": row.get("status") == "received", "received_on": row.get("received_on"),
                          "confirmed_on": r.get("confirmed_on"), "due_on": r.get("due_on") or r.get("need_by"),
                          "requested_on": r.get("requested_on"), "ordered_on": r.get("ordered_on"),
                          "lead_time_days": r.get("lead_time_days")})
        out[lid] = TL.list_progress(facts)
    return out


def _progress(project: dict, doc: dict, today: str) -> tuple:
    try:
        link = production_for(project, doc)
    except Exception:                                       # noqa: BLE001
        log.warning("timeline: production of project %s unreadable", project.get("id"), exc_info=True)
        link = None
    if link is None:
        return None, {}
    try:
        return link, production_progress(link["die"], link["config"], link["production"], today)
    except Exception:                                       # noqa: BLE001
        log.warning("timeline: progress of %s/%s unreadable", link["die"], link["config"], exc_info=True)
        return link, {}


def _motor_title(link: Optional[dict]) -> Optional[dict]:
    title = ((link or {}).get("production") or {}).get("title")
    return title if isinstance(title, dict) else None


# ── customer ────────────────────────────────────────────────────────────────────────────────────────────────────────

def _refuse(status: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail=detail, headers=_NO_STORE)


def can_read_timeline(user: Optional[dict], project: dict) -> bool:
    """A global admin, or a member of this project (any role).  Never a global engineer who is not a member."""
    if not user:
        return False
    if str(user.get("role") or "").lower() == "admin":
        return True
    return access.membership(str(user.get("email") or ""), project) is not None


def timeline_ready(user: Optional[dict], project: dict) -> bool:
    """The only thing the project payload says about the timeline: ``True`` when ``user`` may read it and it has iterations."""
    try:
        if not can_read_timeline(user, project):
            return False
        doc = load(str(project.get("id") or ""))
        return bool(doc and doc["iterations"])
    except Exception:                                       # noqa: BLE001 - a flag never breaks the project payload
        log.warning("timeline flag failed for %s", project.get("id"), exc_info=True)
        return False


@router.get("/{project_id}/timeline")
def get_timeline(project_id: str, request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    if auth._AGENT_USER.get() is not None:
        raise _refuse(403, "The timeline is not available to agents.")
    try:
        user = access.require_signed_in(request)
    except HTTPException as exc:
        raise _refuse(exc.status_code, str(exc.detail)) from None
    project = store.get_project(project_id) if store.valid_id(project_id) else None
    if project is None or not can_read_timeline(user, project):
        raise _refuse(404, _NOT_FOUND)
    doc = load(project_id)
    if not doc or not doc["iterations"]:
        raise _refuse(404, _NOT_FOUND)
    today = _today()
    link, progress = _progress(project, doc, today)
    return TL.customer_view(doc, today, progress, _motor_title(link))


# ── admin ───────────────────────────────────────────────────────────────────────────────────────────────────────────

def _admin(admin_user: dict = Depends(auth.require_admin)) -> dict:
    if auth._AGENT_USER.get() is not None:
        raise HTTPException(status_code=403, detail="The timeline is not available to agents.")
    return admin_user


def _email(user: dict) -> str:
    return str(user.get("email") or "admin").strip().lower()


def _project(project_id: str) -> dict:
    project = store.get_project(project_id) if store.valid_id(project_id) else None
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


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


def _conflict(current: Optional[dict]) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": "revision_conflict",
                                                  "message": "The timeline was changed meanwhile; reload it.",
                                                  "revision": int((current or {}).get("revision") or 0)})


def _estimate(project: dict) -> Optional[dict]:
    from motor_ai_sim.development import cost_estimate as CE
    from motor_ai_sim.development import cost_estimate_store as CS
    stored = CS.load(project["id"])
    if stored is None:
        return None
    return CE.from_stored(stored, CE.load_schema(), project["id"], linked_motors=project.get("linked_motors") or [])


def _admin_payload(project: dict, doc: Optional[dict], **extra: Any) -> dict:
    doc = doc or TL.empty_timeline()
    today = _today()
    link, progress = _progress(project, doc, today)
    prod = None
    if link:
        prod = {"die": link["die"], "config": link["config"], "title": _motor_title(link),
                "progress": {k: v for k, v in progress.items()}}
    return {"timeline": doc, "computed": TL.compute(doc, today, progress), "view": TL.customer_view(doc, today, progress, _motor_title(link)),
            "linked_motors": _linked(project), "production": prod, "estimate_available": _estimate_available(project),
            "audit": read_audit(project["id"]), "today": today, **extra}


def _estimate_available(project: dict) -> bool:
    try:
        est = _estimate(project)
    except Exception:                                       # noqa: BLE001
        return False
    return bool(est and est.get("iterations"))


@admin_router.get("/{project_id}/timeline")
def admin_get_timeline(project_id: str, response: Response, admin_user: dict = Depends(_admin)):
    response.headers["Cache-Control"] = "no-store"
    project = _project(project_id)
    return _admin_payload(project, load(project_id))


@admin_router.put("/{project_id}/timeline")
async def admin_put_timeline(project_id: str, request: Request, response: Response, admin_user: dict = Depends(_admin)):
    response.headers["Cache-Control"] = "no-store"
    project = _project(project_id)
    body = await _body(request)
    expected = _revision(body)
    try:
        candidate = TL.normalize_timeline(body)
    except TL.TimelineError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    def build(previous):
        return {**candidate, "baseline": (previous or {}).get("baseline")}

    try:
        previous, saved = save(project_id, expected, build, _email(admin_user))
    except RevisionConflict as exc:
        raise _conflict(exc.current) from None
    paths = TL.diff_paths(previous, saved)
    audit(project_id, _email(admin_user), "timeline.update", {"revision": saved["revision"], "changes": len(paths), "paths": paths[:60]})
    return _admin_payload(project, saved, changes=paths)


@admin_router.post("/{project_id}/timeline/seed")
async def admin_seed_timeline(project_id: str, request: Request, response: Response, admin_user: dict = Depends(_admin)):
    """"Create from cost estimate": the iterations and plan weeks of the current estimate (owner 2026-11-03)."""
    response.headers["Cache-Control"] = "no-store"
    project = _project(project_id)
    body = await _body(request)
    expected = _revision(body)
    estimate = _estimate(project)
    if not estimate or not estimate.get("iterations"):
        raise HTTPException(status_code=409, detail="The project has no cost estimate to create the timeline from.")

    def build(previous):
        return {**TL.seed_from_estimate(estimate, previous), "baseline": (previous or {}).get("baseline")}

    try:
        previous, saved = save(project_id, expected, build, _email(admin_user))
    except RevisionConflict as exc:
        raise _conflict(exc.current) from None
    paths = TL.diff_paths(previous, saved)
    audit(project_id, _email(admin_user), "timeline.seed", {"revision": saved["revision"], "iterations": len(saved["iterations"]),
                                                             "estimate_revision": estimate.get("revision"), "paths": paths[:60]})
    return _admin_payload(project, saved, changes=paths)


@admin_router.post("/{project_id}/timeline/baseline")
async def admin_freeze_baseline(project_id: str, request: Request, response: Response, admin_user: dict = Depends(_admin)):
    """Freeze the current plan as the baseline: the customer's plan bars (and "late") follow it from now on."""
    response.headers["Cache-Control"] = "no-store"
    project = _project(project_id)
    body = await _body(request)
    expected = _revision(body)
    current = load(project_id)
    if not current or not any(it.get("start_date") for it in current["iterations"]):
        raise HTTPException(status_code=409, detail="Set an iteration start date before freezing the baseline.")
    actor = _email(admin_user)

    def build(previous):
        doc = dict(previous or current)
        doc["baseline"] = TL.baseline_from(doc, actor, _now())
        return doc

    try:
        previous, saved = save(project_id, expected, build, actor)
    except RevisionConflict as exc:
        raise _conflict(exc.current) from None
    audit(project_id, actor, "timeline.baseline", {"revision": saved["revision"], "baseline": saved["baseline"],
                                                   "replaced": bool((previous or {}).get("baseline"))})
    return _admin_payload(project, saved)
