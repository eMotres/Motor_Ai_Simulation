"""Routes of the specification assistant (Development -> Specification side panel).

GET  /api/projects/{id}/spec/assistant                      may I use it, may I apply, is a provider configured
POST /api/projects/{id}/spec/assistant                      {messages, lang} -> {reply, proposals, source}
GET  /api/projects/{id}/spec/proposals                      open proposals (+ ?status=all), the field's current answer, the revision
POST /api/projects/{id}/spec/proposals/{pid}/apply          {revision} -> spec editors only; audit spec.proposal.apply
POST /api/projects/{id}/spec/proposals/{pid}/dismiss        spec editors only

Members of the project reach the assistant when ``DESIGN_AGENT_SPEC_ACCESS=members`` (default ``staff``).  The
assistant never writes the draft; only ``apply`` does, through the same normalise + revision check as ``PUT /spec``.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from motor_ai_sim.development import access, models, spec_assistant as A, spec_proposals as P, store

router = APIRouter(prefix="/api/projects", tags=["development-spec-assistant"])


def _gate(request: Request, project_id: str):
    if not store.valid_id(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    user, project, _ = access.require_project(request, project_id)
    return user, project, A.may_use(user, project), access.can_edit_spec(user, project)


def _allowed(request: Request, project_id: str):
    user, project, allowed, can_apply = _gate(request, project_id)
    if not allowed:
        raise HTTPException(status_code=403, detail="The specification assistant is not open for this account yet.")
    return user, project, can_apply


def _provider_configured() -> bool:
    try:
        from motor_ai_sim.routes import support
        eff = support._effective()
        return eff["provider"] != "none" and bool(eff[eff["provider"]]["key"])
    except Exception:                                         # noqa: BLE001
        return False


@router.get("/{project_id}/spec/assistant")
def assistant_status(request: Request, project_id: str):
    _, _, allowed, can_apply = _gate(request, project_id)
    return {"allowed": allowed, "can_apply": bool(allowed and can_apply),
            "configured": bool(allowed and _provider_configured()), "access": A.access_mode()}


@router.post("/{project_id}/spec/assistant")
async def assistant_turn(request: Request, project_id: str):
    user, project, can_apply = _allowed(request, project_id)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    body = body if isinstance(body, dict) else {}
    return A.turn(project_id, project, user, body.get("messages"), body.get("lang"), can_apply=can_apply)


@router.get("/{project_id}/spec/proposals")
def list_proposals(request: Request, project_id: str):
    _, _, can_apply = _allowed(request, project_id)
    wanted = request.query_params.get("status", "open")
    rows = P.list_proposals(project_id, None if wanted == "all" else "open")
    wrapper = P.draft_wrapper(project_id)
    return {"proposals": P.with_current(project_id, rows), "revision": int(wrapper.get("revision", 0)),
            "can_apply": can_apply}


@router.post("/{project_id}/spec/proposals/{proposal_id}/apply")
async def apply_proposal(request: Request, project_id: str, proposal_id: str):
    if not store.valid_id(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    user, project, _ = access.require_spec_editor(request, project_id)
    if not A.may_use(user, project):
        raise HTTPException(status_code=403, detail="The specification assistant is not open for this account yet.")
    try:
        body = await request.json()
    except ValueError:
        body = {}
    revision = body.get("revision") if isinstance(body, dict) else None
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise HTTPException(status_code=422, detail="revision must be a non-negative integer")
    try:
        result = P.apply(project_id, proposal_id, str(user.get("email") or ""), revision)
    except LookupError:
        raise HTTPException(status_code=404, detail="Proposal not found")
    except P.ProposalConflict as exc:
        detail = {"message": str(exc)}
        if exc.revision >= 0:
            detail["revision"] = exc.revision
        raise HTTPException(status_code=409, detail=detail)
    except P.ProposalError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    draft = result["draft"]
    if not access.is_engineer(user):
        from motor_ai_sim.development.routes import _customer_spec
        draft = _customer_spec({"draft": draft})["draft"]
    return {"proposal": result["proposal"], "revision": result["revision"], "draft": draft,
            "completeness": result["completeness"]}


@router.post("/{project_id}/spec/proposals/{proposal_id}/dismiss")
def dismiss_proposal(request: Request, project_id: str, proposal_id: str):
    if not store.valid_id(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    user, project, _ = access.require_spec_editor(request, project_id)
    if not A.may_use(user, project):
        raise HTTPException(status_code=403, detail="The specification assistant is not open for this account yet.")
    try:
        proposal = P.dismiss(project_id, proposal_id, str(user.get("email") or ""))
    except LookupError:
        raise HTTPException(status_code=404, detail="Proposal not found")
    except P.ProposalConflict as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc)})
    return {"proposal": proposal}
