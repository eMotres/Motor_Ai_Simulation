from fastapi import APIRouter, HTTPException, Request
from motor_ai_sim import auth
from motor_ai_sim.development import models, store
from motor_ai_sim.development.routes import _summary
from motor_ai_sim.development.access import is_engineer

router = APIRouter(prefix="/api/admin/projects", tags=["admin-development"])


def _email(user: dict) -> str:
    return str(user.get("email") or "").strip().lower()


@router.get("")
def admin_list(admin_user: dict = __import__("fastapi").Depends(auth.require_admin)):
    return {"projects": store.list_projects()}


@router.post("")
async def admin_create(request: Request, admin_user: dict = __import__("fastapi").Depends(auth.require_admin)):
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
