"""Routes of the specification assistant (Development -> Specification side panel).

GET  /api/projects/{id}/spec/assistant                      may I use it, may I apply, is a provider configured
POST /api/projects/{id}/spec/assistant                      {messages, lang} -> {reply, proposals, source}
POST /api/projects/{id}/spec/assistant/file                 multipart file (+ lang, messages) -> proposal CARDS read from a TXT / DOCX / PDF / XLSX /
                                                            XLS file; spec editors only (owner, admin), motor projects only; never fills, never submits
GET  /api/projects/{id}/spec/proposals                      open proposals (+ ?status=all), the field's current answer, the revision
POST /api/projects/{id}/spec/proposals/{pid}/apply          {revision} -> spec editors only; audit spec.proposal.apply
POST /api/projects/{id}/spec/proposals/{pid}/dismiss        spec editors only
POST /api/projects/{id}/spec/proposals/{pid}/undo           spec editors only: restore what an assistant fill replaced
GET  /api/projects/{id}/spec/assistant/history              this user's kept conversation (staff: ?user=<email>)
DELETE /api/projects/{id}/spec/assistant/history            clear this user's conversation

ONE assistant (owner 2026-11-11): the chat bubble talks to ``POST /api/support/chat`` with a ``place`` naming the project, which runs
the same ``spec_assistant.turn``; the POST route here stays for API callers.  Every member of the project reaches it (staff too).
The assistant never writes the draft itself; only ``apply`` / fill mode do, through the same normalise + revision check as ``PUT /spec``.
"""
from __future__ import annotations

import json as _json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from motor_ai_sim.development import access, models, spec_assistant as A, spec_assistant_history as H, spec_proposals as P, store
from motor_ai_sim.development import spec_file_assistant as FA, spec_file_extract as FX

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
            "configured": bool(allowed and _provider_configured()), "access": A.access_mode(),
            "file_upload": bool(allowed and can_apply), "file_max_bytes": FX.MAX_BYTES, "file_extensions": list(FX.EXTENSIONS)}


@router.post("/{project_id}/spec/assistant")
async def assistant_turn(request: Request, project_id: str):
    user, project, can_apply = _allowed(request, project_id)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    body = body if isinstance(body, dict) else {}
    fill = can_apply and body.get("fill") is not False        # fill mode: on by default for whoever can apply
    base = None
    try:                                                      # the ONE assistant's prompt for this caller's role (help + spec)
        from motor_ai_sim.routes import support as S
        auth = request.headers.get("authorization")
        base = S.build_system_prompt(S._caller_role(auth), anon=False, authorization=auth, spec=True)
    except Exception:                                         # noqa: BLE001 - the customer prompt is the fallback
        base = None
    out = A.turn(project_id, project, user, body.get("messages"), body.get("lang"), can_apply=can_apply, fill=fill, base_prompt=base)
    _remember(project_id, user, body.get("messages"), out)
    return out


def _refusal(exc: FX.ExtractError, lang: str, status: int = 422) -> HTTPException:
    return HTTPException(status_code=status, detail={"message": exc.message(lang), "code": exc.code})


async def _read_upload(request: Request) -> tuple[Any, Any]:
    """The multipart body, with a STREAMING bound: the stream is counted as it arrives and refused (413) as soon as it passes the file
    limit plus the form's own overhead, whether or not a Content-Length was declared; at most ONE file part and a few small fields."""
    from starlette.formparsers import MultiPartException, MultiPartParser
    bound = FX.MAX_BYTES + 256 * 1024

    async def limited():
        seen = 0
        async for chunk in request.stream():
            seen += len(chunk)
            if seen > bound:
                raise HTTPException(status_code=413, detail={"message": FX._MSG["too_big"][0], "code": "too_big"})
            yield chunk

    if "multipart/form-data" not in request.headers.get("content-type", "").lower():
        raise HTTPException(status_code=400, detail="multipart/form-data expected")
    try:
        form = await MultiPartParser(request.headers, limited(), max_files=1, max_fields=8).parse()
    except MultiPartException:
        raise HTTPException(status_code=400, detail="One file and a few small fields expected") from None
    return form, form.get("file")


@router.post("/{project_id}/spec/assistant/file")
async def assistant_file(request: Request, project_id: str):
    """Read an attached specification file (owner 2026-11-24).  Only the project owner and admins (``can_edit_spec``; anyone else gets
    403 / 404 like the other spec routes), motor projects only, signed in only.  The answer is a reply with proposal cards.

    Order: access -> streaming body bound -> file name and cheap type / size checks -> provider configured -> extraction in a limited
    worker -> sanitising -> ONLY THEN the quotas are charged -> the original is stored -> the model.  A refused file (type, size, active
    content, no text) uses no quota and leaves no bytes behind."""
    if not store.valid_id(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    user, project, _ = access.require_spec_editor(request, project_id)
    if not A.may_use(user, project):                           # a controller project has no assistant: motor projects only
        raise HTTPException(status_code=403, detail="The specification assistant is not open for this account yet.")
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        declared = 0
    if declared > FX.MAX_BYTES + 256 * 1024:
        raise _refusal(FX.ExtractError("too_big", *FX._MSG["too_big"]), "en", 413)
    form, uploaded = await _read_upload(request)
    try:
        if uploaded is None or isinstance(uploaded, str):
            raise HTTPException(status_code=400, detail="No file uploaded")
        lang = A.lang_of(form.get("lang"))
        try:
            raw_messages = _json.loads(str(form.get("messages") or "[]"))
        except ValueError:
            raw_messages = []
        name = str(uploaded.filename or "")
        email = str(user.get("email") or "").strip().lower()
        data = await run_in_threadpool(lambda: uploaded.file.read(FX.MAX_BYTES + 1))
        try:
            FX.precheck(name, data)                            # name, type, size, macro container, old binary file: nothing is parsed
        except FX.ExtractError as exc:
            raise _refusal(exc, lang, 413 if exc.code == "too_big" else 422)
        if not _provider_configured():
            raise HTTPException(status_code=503, detail={"message": A.REPLIES["unavailable"][lang], "code": "unavailable"})
        hit = FA.check_limits(email, project_id)
        if hit or not FA.attempt(email):
            code = hit or "tries"
            raise HTTPException(status_code=429, detail={"message": FA.LIMIT_TEXT[code][lang], "code": "file_" + code})
        try:
            extracted = await run_in_threadpool(FX.extract_isolated, name, data)
            san = FA.sanitise(extracted.blocks)
            FA.check_size(san)
            if not san.blocks:
                raise FX.ExtractError("no_text", *FX._MSG["no_text"])
        except FX.ExtractError as exc:
            raise _refusal(exc, lang, 413 if exc.code in ("too_big", "storage_full") else 422)
        # the file is accepted: now the quotas are used (the chat day cap, the account cap, the file limits)
        if not access.is_engineer(user):
            from motor_ai_sim.routes import support as S
            if email and not S._charge_account(email):
                raise HTTPException(status_code=429, detail={"message": "The assistant has reached its daily limit for your account - please try again tomorrow."
                                                             if lang == "en" else "今日助手使用次数已达上限，请明天再试。", "code": "account_day"})
        if not A._charge(email):
            raise HTTPException(status_code=429, detail={"message": "The assistant has reached its daily limit for your account - please try again tomorrow."
                                                         if lang == "en" else "今日助手使用次数已达上限，请明天再试。", "code": "chat_day"})
        hit = FA.charge_file(email, project_id)
        if hit:
            raise HTTPException(status_code=429, detail={"message": FA.LIMIT_TEXT[hit][lang], "code": "file_" + hit})
        try:
            row = await run_in_threadpool(FA.store_original, project_id, user, name, data, str(getattr(uploaded, "content_type", "") or ""))
        except FX.ExtractError as exc:
            raise _refusal(exc, lang, 413)
        except ValueError:                                     # the store's own size / empty refusal
            raise HTTPException(status_code=413, detail={"message": FX._MSG["too_big"][0 if lang == "en" else 1], "code": "too_big"})
    finally:
        await form.close()
    base = None
    try:
        from motor_ai_sim.routes import support as S
        auth = request.headers.get("authorization")
        base = S.build_system_prompt(S._caller_role(auth), anon=False, authorization=auth, spec=True)
    except Exception:                                         # noqa: BLE001 - the customer prompt is the fallback
        base = None
    out = await run_in_threadpool(lambda: FA.run_turn(project_id, project, user, name=row["name"], document_id=row["id"], san=san,
                                                      raw_messages=raw_messages, raw_lang=lang, base_prompt=base))
    out["file"] = {**(out.get("file") or {}), "name": row["name"], "document_id": row["id"], "sha256": row["sha256"], "size": row["size"],
                   "kind": extracted.kind, "warnings": extracted.warnings}
    _remember_file(project_id, user, row, out)
    return out


def _remember_file(project_id: str, user: dict, row: dict, out: dict) -> None:
    """The user's history keeps the file name and document id, never its text; the service archive gets a row WITHOUT any text
    (``file_turn``: the whole conversation stays out of every export, ``assistant_archive.SPEC_FILE_TEXT_IN_TRAINING``)."""
    email = str(user.get("email") or "").lower()
    try:
        rows = [{"role": "user", "text": row["name"], "file": {"name": row["name"], "document_id": row["id"]}}]
        if out.get("source") not in ("rate_limited", "mock", "error"):
            rows.append({"role": "assistant", "text": out.get("reply", ""), "proposals": [p["id"] for p in out.get("proposals", [])],
                         "applied": []})
        H.append(project_id, email, rows)
    except Exception:                                         # noqa: BLE001
        pass
    try:
        from motor_ai_sim import assistant_archive as AR
        AR.log_turn(assistant="assistant", context="spec", conv_id=AR.conv_key("spec", project_id, email), account=email,
                    project_id=project_id, page="development.specification", user_text=row["name"], reply=out.get("reply", ""),
                    model=out.get("model", ""), source=out.get("source", ""), error="provider error" if out.get("source") == "error" else "",
                    fill_mode=False, applied=[], proposed=out.get("proposals"), refused=out.get("refused"), dropped=out.get("dropped", 0),
                    file_turn=True)
    except Exception:                                         # noqa: BLE001
        pass


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
        AR.log_turn(assistant="assistant", context="spec", conv_id=AR.conv_key("spec", project_id, email), account=email,
                    project_id=project_id, page="development.specification", user_text=text, reply=out.get("reply", ""),
                    model=out.get("model", ""), source=out.get("source", ""),
                    error="provider error" if out.get("source") == "error" else "",
                    fill_mode=out.get("fill_mode"), applied=out.get("applied"), proposed=out.get("proposals"),
                    refused=out.get("refused"), dropped=out.get("dropped", 0), searches=out.get("searches"),
                    sources=out.get("sources"))
    except Exception:                                         # noqa: BLE001
        pass


def _event(project_id: str, user: dict, event: str, proposal: dict) -> None:
    try:
        from motor_ai_sim import assistant_archive as AR
        email = str(user.get("email") or "").lower()
        AR.log_event(assistant="assistant", context="spec", event=event, conv_id=AR.conv_key("spec", project_id, email), account=email,
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
