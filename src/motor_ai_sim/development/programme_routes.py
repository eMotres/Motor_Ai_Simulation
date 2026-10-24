"""Admin-only programme endpoints, mounted under ``/api/admin/projects/programmes``.

Stage 1 ships these hidden: unless ``MOTRES_DEVELOPMENT_PROGRAMMES`` is on, every
route except ``capabilities`` answers 404. Non-admin callers are refused by
``auth.require_admin`` before anything programme-related is read.
"""
from __future__ import annotations

import hashlib
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from motor_ai_sim import auth
from motor_ai_sim.development import programme, programme_store

router = APIRouter(prefix="/programmes", tags=["admin-development-programmes"])

ENV_FLAG = "MOTRES_DEVELOPMENT_PROGRAMMES"


def enabled() -> bool:
    return os.environ.get(ENV_FLAG, "").strip().lower() in {"1", "true", "yes", "on"}


def catalog_lookup(die: str, config: str) -> str | None:
    """sha256 of the catalog configuration file as it is now, or ``None`` if it cannot be resolved."""
    try:
        from motor_ai_sim.routes import family
        path = family._cfg_file(die, config)
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    except Exception:  # noqa: BLE001 - unresolvable reads as missing
        return None


def _email(user: dict) -> str:
    return str(user.get("email") or "").strip().lower()


def _require(programme_id: str | None = None) -> None:
    if not enabled():
        raise HTTPException(status_code=404, detail="Not found")
    if programme_id is not None and not programme.valid_programme_id(programme_id):
        raise HTTPException(status_code=404, detail="Programme not found")


def _raise(exc: programme.ProgrammeError):
    if isinstance(exc, programme_store.RevisionConflict):
        raise HTTPException(status_code=409, detail={"message": str(exc), "revision": exc.current})
    if isinstance(exc, programme_store.IdempotencyConflict):
        raise HTTPException(status_code=409, detail={"message": str(exc)})
    if isinstance(exc, programme_store.StoreCorrupt):
        raise HTTPException(status_code=500, detail={"message": str(exc)})
    raise HTTPException(status_code=422, detail={"message": str(exc), "errors": exc.errors})


def _view(envelope: dict) -> dict:
    document = envelope["programme"]
    return {**envelope, "reconciliation": programme.reconciliation(document),
            "references": programme.reference_status(document, catalog_lookup)}


@router.get("/capabilities")
def capabilities(admin_user: dict = Depends(auth.require_admin)):
    return {"programmes": {"enabled": enabled(), "schema_version": programme.PROGRAMME_SCHEMA_VERSION,
                           "migration": "dry-run-only"}}


@router.get("")
def list_programmes(admin_user: dict = Depends(auth.require_admin)):
    _require()
    return {"programmes": programme_store.list_programmes()}


@router.get("/{programme_id}")
def get_programme(programme_id: str, admin_user: dict = Depends(auth.require_admin)):
    _require(programme_id)
    try:
        envelope = programme_store.read(programme_id)
    except programme.ProgrammeError as exc:
        _raise(exc)
    if envelope is None:
        raise HTTPException(status_code=404, detail="Programme not found")
    return {**_view(envelope), "history": programme_store.history(programme_id)}


@router.get("/{programme_id}/revisions/{revision}")
def get_revision(programme_id: str, revision: int, admin_user: dict = Depends(auth.require_admin)):
    _require(programme_id)
    try:
        envelope = programme_store.read(programme_id, revision)
    except programme.ProgrammeError as exc:
        _raise(exc)
    if envelope is None:
        raise HTTPException(status_code=404, detail="Programme revision not found")
    return _view(envelope)


@router.put("/{programme_id}")
async def put_programme(programme_id: str, request: Request, admin_user: dict = Depends(auth.require_admin)):
    _require(programme_id)
    body = await request.json()
    if not isinstance(body, dict) or not isinstance(body.get("programme"), dict):
        raise HTTPException(status_code=400, detail="Expected {programme, expected_revision, idempotency_key}")
    if not body.get("idempotency_key"):
        raise HTTPException(status_code=422, detail="idempotency_key required")
    try:
        result = programme_store.commit(programme_id, body["programme"], expected_revision=body.get("expected_revision"),
                                        actor=_email(admin_user), idempotency_key=body["idempotency_key"],
                                        catalog_lookup=catalog_lookup)
    except programme.ProgrammeError as exc:
        _raise(exc)
    return {**result, "reconciliation": programme.reconciliation(result["programme"])}


@router.get("/{programme_id}/recovery")
def recovery_report(programme_id: str, admin_user: dict = Depends(auth.require_admin)):
    _require(programme_id)
    try:
        return programme_store.recover(programme_id)
    except programme.ProgrammeError as exc:
        _raise(exc)


@router.post("/{programme_id}/recovery")
def recover(programme_id: str, admin_user: dict = Depends(auth.require_admin)):
    _require(programme_id)
    try:
        return programme_store.recover(programme_id, apply=True)
    except programme.ProgrammeError as exc:
        _raise(exc)
