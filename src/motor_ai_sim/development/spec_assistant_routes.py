"""Routes of the specification assistant (Development -> Specification side panel).

GET  /api/projects/{id}/spec/assistant                      may I use it, may I apply, is a provider configured
POST /api/projects/{id}/spec/assistant                      {messages, lang} -> {reply, proposals, source}
GET  /api/projects/{id}/spec/proposals                      open proposals (+ ?status=all), the field's current answer, the revision
POST /api/projects/{id}/spec/proposals/{pid}/apply          {revision} -> spec editors only; audit spec.proposal.apply
POST /api/projects/{id}/spec/proposals/{pid}/dismiss        spec editors only
POST /api/projects/{id}/spec/proposals/{pid}/undo           spec editors only: restore what an assistant fill replaced
GET  /api/projects/{id}/spec/assistant/history              this user's kept conversation (staff: ?user=<email>)
DELETE /api/projects/{id}/spec/assistant/history            clear this user's conversation

Members of the project reach the assistant when ``DESIGN_AGENT_SPEC_ACCESS=members`` (default ``staff``).  The
assistant never writes the draft; only ``apply`` does, through the same normalise + revision check as ``PUT /spec``.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from motor_ai_sim.development import access, models, spec_assistant as A, spec_assistant_history as H, spec_proposals as P, store

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
        from motor_ai_sim.regions import relay
        if relay.relay_enabled():
            return True
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
    fill = can_apply and body.get("fill") is not False        # fill mode: on by default for whoever can apply
    out = A.turn(project_id, project, user, body.get("messages"), body.get("lang"), can_apply=can_apply, fill=fill)
    _remember(project_id, user, body.get("messages"), out)
    return out


def _remember(project_id: str, user: dict, raw_messages, out: dict) -> None:
    """Keep this turn in the user's server-side history (a failure here never fails the answer)."""
    try:
        sent = [m for m in (raw_messages if isinstance(raw_messages, list) else []) if isinstance(m, dict)]
        last = sent[-1] if sent and sent[-1].get("role") == "user" else None
        rows = []
        if last and isinstance(last.get("content"), str) and last["content"].strip():
            rows.append({"role": "user", "text": last["content"]})
        if out.get("source") not in ("rate_limited", "mock", "error"):
            rows.append({"role": "assistant", "text": out.get("reply", ""), "proposals": [p["id"] for p in out.get("proposals", [])],
                         "applied": [p["id"] for p in out.get("applied", [])]})
        H.append(project_id, str(user.get("email") or ""), rows)
    except Exception:                                         # noqa: BLE001
        pass
    try:                                                      # the archive for service analysis: separate from the user's view
        from motor_ai_sim import assistant_archive as AR
        email = str(user.get("email") or "").lower()
        last = next((m for m in reversed(raw_messages) if isinstance(m, dict) and m.get("role") == "user"), None) \
            if isinstance(raw_messages, list) else None
        text = last.get("content") if last and isinstance(last.get("content"), str) else ""
        AR.log_turn(assistant="spec", conv_id=AR.conv_key("spec", project_id, email), account=email, project_id=project_id,
                    page="development.specification", user_text=text, reply=out.get("reply", ""), model=out.get("model", ""),
                    source=out.get("source", ""), error="provider error" if out.get("source") == "error" else "",
                    fill_mode=out.get("fill_mode"), applied=out.get("applied"), proposed=out.get("proposals"),
                    refused=out.get("refused"), dropped=out.get("dropped", 0))
    except Exception:                                         # noqa: BLE001
        pass


def _event(project_id: str, user: dict, event: str, proposal: dict) -> None:
    try:
        from motor_ai_sim import assistant_archive as AR
        email = str(user.get("email") or "").lower()
        AR.log_event(assistant="spec", event=event, conv_id=AR.conv_key("spec", project_id, email), account=email,
                     project_id=project_id, field_id=str(proposal.get("field_id") or ""), proposal_id=str(proposal.get("id") or ""))
    except Exception:                                         # noqa: BLE001
        pass


def _history_owner(request: Request, user: dict) -> str:
    who = str(request.query_params.get("user") or "").strip().lower()
    if who and not H.readable_by(user, who):
        raise HTTPException(status_code=403, detail="Only that user and staff can read this conversation")
    return who or str(user.get("email") or "").lower()


@router.get("/{project_id}/spec/assistant/history")
def assistant_history(request: Request, project_id: str):
    user, _, _ = _allowed(request, project_id)
    return {"messages": H.load(project_id, _history_owner(request, user))}


@router.delete("/{project_id}/spec/assistant/history")
def assistant_history_clear(request: Request, project_id: str):
    user, _, _ = _allowed(request, project_id)
    H.clear(project_id, str(user.get("email") or ""))
    return {"messages": []}


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
    _event(project_id, user, "dismiss", proposal)
    return {"proposal": proposal}


@router.post("/{project_id}/spec/proposals/{proposal_id}/undo")
def undo_proposal(request: Request, project_id: str, proposal_id: str):
    if not store.valid_id(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    user, project, _ = access.require_spec_editor(request, project_id)
    if not A.may_use(user, project):
        raise HTTPException(status_code=403, detail="The specification assistant is not open for this account yet.")
    try:
        result = P.undo(project_id, proposal_id, str(user.get("email") or ""))
    except LookupError:
        raise HTTPException(status_code=404, detail="Proposal not found")
    except P.ProposalConflict as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc)})
    except P.ProposalError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    _event(project_id, user, "undo", result["proposal"])
    draft = result["draft"]
    if not access.is_engineer(user):
        from motor_ai_sim.development.routes import _customer_spec
        draft = _customer_spec({"draft": draft})["draft"]
    return {"proposal": result["proposal"], "revision": result["revision"], "draft": draft,
            "completeness": result["completeness"]}
