import json

from fastapi import APIRouter, Depends, HTTPException, Request
from motor_ai_sim import auth
from motor_ai_sim.development import grant_cleanup, models, store
from motor_ai_sim.development import motors as development_motors

router = APIRouter(prefix="/api/admin/projects", tags=["admin-development"])


def _email(user: dict) -> str:
    return str(user.get("email") or "").strip().lower()


def _drain(admin_user: dict) -> None:
    """The stale "grants may need review" rows go by themselves (owner 2026-10-27): an idempotent pass over the stored
    candidates before they are shown.  Never fails the read."""
    try:
        grant_cleanup.drain_all(actor=_email(admin_user))
    except Exception:                                        # noqa: BLE001
        pass


@router.get("")
def admin_list(admin_user: dict = Depends(auth.require_admin)):
    _drain(admin_user)
    projects = []
    for project in store.list_projects():
        draft = store.load_json(project["id"], "draft.json", {"draft": {}})
        activity = _last_activity(project)
        projects.append({**project,
                         "completeness": models.completeness(draft.get("draft", {})),
                         "motors": list(project.get("linked_motors") or []),
                         "last_activity": activity,
                         "grant_removal_candidates": list(project.get("grant_removal_candidates") or [])})
    return {"projects": projects}


def _last_activity(project: dict) -> str | None:
    audit_path = store.path_for(project["id"]) / "audit.jsonl"
    latest = None
    try:
        for line in audit_path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
                timestamp = event.get("time") if isinstance(event, dict) else None
                if timestamp and (latest is None or str(timestamp) > str(latest)):
                    latest = timestamp
            except (ValueError, TypeError):
                continue
    except OSError:
        pass
    return latest or project.get("updated_at") or project.get("created_at")


@router.post("")
async def admin_create(request: Request, admin_user: dict = Depends(auth.require_admin)):
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")
    project_id = str(body.get("id") or "").strip().lower()
    if not store.valid_id(project_id):
        raise HTTPException(status_code=422, detail="id must be a lowercase slug")
    try:
        project = models.project_record(project_id, body, _email(admin_user))
        store.create_project(project)
    except FileExistsError:
        raise HTTPException(status_code=409, detail="Project already exists")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    store.save_json(project_id, "draft.json", {"draft": {"fields": {}, "mission_profile": [], "mission_profile_status": "missing", "production": {}, "provenance": {}}, "revision": 0, "version": None})
    store.append_audit(project_id, _email(admin_user), "create", "project", project_id, {})
    return project


@router.post("/{project_id}/members")
async def admin_add_member(project_id: str, request: Request,
                           admin_user: dict = Depends(auth.require_admin)):
    from motor_ai_sim.development import routes as project_routes
    # Preserve the stage-1 transaction, grant ledger, rollback and audit behavior.
    return await project_routes.add_member(request, project_id)


@router.delete("/{project_id}/members")
async def admin_remove_member(project_id: str, request: Request, email: str,
                              admin_user: dict = Depends(auth.require_admin)):
    from motor_ai_sim.development import routes as project_routes
    return await project_routes.remove_member(request, project_id, email)


@router.post("/{project_id}/motors")
async def admin_link_motor(project_id: str, request: Request,
                           admin_user: dict = Depends(auth.require_admin)):
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")
    linked = development_motors.link_motor(project_id, body.get("die", ""), body.get("config", ""), request)
    store.append_audit(project_id, _email(admin_user), "motor.link", "motor",
                       f"{linked['die']}/{linked['config']}")
    return {"motor": linked}


@router.delete("/{project_id}/motors")
def admin_unlink_motor(project_id: str, die: str, config: str,
                       admin_user: dict = Depends(auth.require_admin)):
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    development_motors.unlink_motor(project_id, die, config, actor=_email(admin_user))
    store.append_audit(project_id, _email(admin_user), "motor.unlink", "motor", f"{die}/{config}")
    return {"ok": True}


@router.get("/{project_id}/grant-removal-candidates")
def admin_grant_removal_candidates(project_id: str,
                                   admin_user: dict = Depends(auth.require_admin)):
    _drain(admin_user)
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"candidates": list(project.get("grant_removal_candidates") or [])}
