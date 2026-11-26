"""Saved gearbox records (stage 3 of the gear module).  Pure storage: no FastAPI, no calculation.

One JSON file per record under ``<shared_root>/gearboxes/<scope>/<id>.json``.  The scope is a project id or the
workspace scope ``_workspace``; records are never keyed by a die name (so ``die_rename`` has nothing to move).
Closed design data: the same rule as die data, never in the public repository.

Record (schema_version 1)::

    id, name, scope, state ("active" | "archived"), version (int, +1 on every write),
    created_at, updated_at (UTC ISO), author, updated_by,
    request            complete canonical ``GearboxRequest.to_dict()``
    request_hash       core ``request_hash`` of that request at save time
    core_version       core version that computed the response
    catalogue_digest   SHA-256 of the four catalogue listings at save time (the core does not hash catalogue contents)
    response           last ``GearboxResponse.to_dict()`` or null
    checksum           SHA-256 of the record body without this field (detects a torn or hand-edited file)

Reading never raises for a damaged record: ``load`` returns it with ``flags`` (``request_hash_mismatch``,
``request_invalid``, ``checksum_mismatch``, ``core_stale``, ``catalogue_changed``); ``response_stale`` is true when
the response may no longer be trusted (any flag but ``checksum_mismatch`` alone does not matter: a mismatch of the
checksum also marks it).  An unreadable file is reported by ``list_records`` under ``unreadable`` and never breaks
the listing.  Writes are read-latest + atomic replace under ``json_store.lock_for`` (cross-process in a deployment).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
WORKSPACE_SCOPE = "_workspace"
MAX_REQUEST_BYTES = 1_000_000
MAX_RESPONSE_BYTES = 8_000_000
MAX_NAME_CHARS = 120
STATES = ("active", "archived")
_ID_RE = re.compile(r"^gb_[0-9a-f]{16}$")
_SCOPE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")


class StoreError(Exception):
    """``status`` is the HTTP status the route should answer."""

    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def valid_id(rid: Any) -> bool:
    return isinstance(rid, str) and bool(_ID_RE.match(rid))


def clean_scope(scope: Optional[str]) -> str:
    if scope is None or scope == "":
        return WORKSPACE_SCOPE
    if not isinstance(scope, str) or not _SCOPE_RE.match(scope) or scope.endswith(".") or ".." in scope:
        raise StoreError(422, "invalid_scope", "Project id may contain letters, digits, '_', '-', '.' (1-64 characters)")
    return scope


def root_dir() -> Path:
    from motor_ai_sim.workspace import shared_root
    return Path(str(shared_root())) / "gearboxes"


def _scope_dir(scope: str) -> Path:
    return root_dir() / clean_scope(scope)


def _file(scope: str, rid: str) -> Path:
    if not valid_id(rid):
        raise StoreError(404, "not_found", "No such gearbox record")
    return _scope_dir(scope) / f"{rid}.json"


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=False)


def _checksum(rec: Dict[str, Any]) -> str:
    body = {k: v for k, v in rec.items() if k != "checksum"}
    return hashlib.sha256(_canon(body).encode("utf-8")).hexdigest()


def catalogue_digest() -> str:
    from motor_ai_sim import gears
    data = [f() for f in (gears.list_material_catalogue, gears.list_lubricant_catalogue,
                          gears.list_bearing_catalogue, gears.list_preset_catalogue)]
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ validation of a save

def prepare_request(request: Any) -> Tuple[Dict[str, Any], str]:
    """Decode through the core (raises ``FieldValidationError`` / ValueError), return (canonical dict, hash)."""
    from motor_ai_sim.gears import GearboxRequest, request_hash
    req = GearboxRequest.from_dict(request)
    canon = req.to_dict()
    if len(_canon(canon)) > MAX_REQUEST_BYTES:
        raise StoreError(413, "too_large", f"Request is larger than {MAX_REQUEST_BYTES} bytes")
    return canon, request_hash(req)


def prepare_response(response: Any, expected_hash: str) -> Optional[Dict[str, Any]]:
    if response is None:
        return None
    from motor_ai_sim.gears import GearboxResponse
    if not isinstance(response, dict):
        raise StoreError(422, "invalid_response", "response must be a JSON object or null")
    if len(_canon(response)) > MAX_RESPONSE_BYTES:
        raise StoreError(413, "too_large", f"Response is larger than {MAX_RESPONSE_BYTES} bytes")
    try:
        resp = GearboxResponse.from_dict(response)
    except Exception as exc:                                   # noqa: BLE001 - any decoder error is a 422
        raise StoreError(422, "invalid_response", f"response is not a valid GearboxResponse: {exc}")
    if resp.request_hash != expected_hash:
        raise StoreError(422, "response_mismatch", "response was computed from a different request")
    return resp.to_dict()


def clean_name(name: Any) -> str:
    if not isinstance(name, str) or not name.strip():
        raise StoreError(422, "invalid_name", "name is required")
    name = name.strip()
    if len(name) > MAX_NAME_CHARS:
        raise StoreError(422, "invalid_name", f"name is longer than {MAX_NAME_CHARS} characters")
    return name


# ------------------------------------------------------------------ read

def _flags(rec: Dict[str, Any]) -> List[str]:
    from motor_ai_sim.gears import CORE_VERSION, GearboxRequest, request_hash
    flags: List[str] = []
    try:
        if rec.get("checksum") != _checksum(rec):
            flags.append("checksum_mismatch")
    except (TypeError, ValueError):
        flags.append("checksum_mismatch")
    try:
        if request_hash(GearboxRequest.from_dict(rec["request"])) != rec.get("request_hash"):
            flags.append("request_hash_mismatch")
    except Exception:                                          # noqa: BLE001 - the core no longer reads it
        flags.append("request_invalid")
    if rec.get("core_version") != CORE_VERSION:
        flags.append("core_stale")
    try:
        if rec.get("catalogue_digest") != catalogue_digest():
            flags.append("catalogue_changed")
    except Exception:                                          # noqa: BLE001
        flags.append("catalogue_changed")
    return flags


def _read_file(path: Path) -> Dict[str, Any]:
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise StoreError(404, "not_found", "No such gearbox record")
    except (OSError, ValueError) as exc:
        raise StoreError(500, "unreadable", f"Record file cannot be read: {exc}")
    if not isinstance(rec, dict) or not isinstance(rec.get("request"), dict) or not valid_id(rec.get("id")):
        raise StoreError(500, "unreadable", "Record file has no valid structure")
    return rec


def _decorate(rec: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(rec)
    flags = _flags(rec)
    out["flags"] = flags
    out["response_stale"] = bool(rec.get("response") is not None and flags)
    return out


def load(scope: str, rid: str) -> Dict[str, Any]:
    return _decorate(_read_file(_file(scope, rid)))


def list_records(scope: str, state: str = "active") -> Dict[str, Any]:
    """Summaries (no request/response bodies).  ``state``: active | archived | all."""
    if state not in STATES + ("all",):
        raise StoreError(422, "invalid_state", "state must be active, archived or all")
    d = _scope_dir(scope)
    items: List[Dict[str, Any]] = []
    unreadable: List[Dict[str, str]] = []
    for p in sorted(d.glob("gb_*.json")) if d.is_dir() else []:
        try:
            rec = _decorate(_read_file(p))
        except StoreError as exc:
            unreadable.append({"file": p.name, "error": exc.message})
            continue
        except Exception as exc:                               # noqa: BLE001 - one bad file never breaks the list
            unreadable.append({"file": p.name, "error": str(exc)})
            continue
        if state != "all" and rec.get("state") != state:
            continue
        items.append({k: rec.get(k) for k in ("id", "name", "scope", "state", "version", "created_at", "updated_at",
                                              "author", "updated_by", "core_version", "request_hash", "flags",
                                              "response_stale")}
                     | {"has_response": rec.get("response") is not None})
    items.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
    return {"scope": clean_scope(scope), "items": items, "unreadable": unreadable}


# ------------------------------------------------------------------ write

def _write(path: Path, mutator) -> Dict[str, Any]:
    from motor_ai_sim import json_store

    def run(_doc: Any) -> Dict[str, Any]:
        cur = _read_file(path) if path.exists() else None
        rec = mutator(cur)
        rec["checksum"] = _checksum(rec)
        return rec

    return json_store.mutate_json(path, run, default={})


def create(scope: str, name: str, request: Dict[str, Any], response: Any, author: str) -> Dict[str, Any]:
    from motor_ai_sim.gears import CORE_VERSION
    scope = clean_scope(scope)
    name = clean_name(name)
    canon, rhash = prepare_request(request)
    resp = prepare_response(response, rhash)
    rid = "gb_" + uuid.uuid4().hex[:16]
    stamp = now_iso()

    def mk(cur):
        if cur is not None:
            raise StoreError(409, "exists", "Record id already exists")
        return {"schema_version": SCHEMA_VERSION, "id": rid, "name": name, "scope": scope, "state": "active",
                "version": 1, "created_at": stamp, "updated_at": stamp, "author": author, "updated_by": author,
                "request": canon, "request_hash": rhash,
                "core_version": resp["core_version"] if resp else CORE_VERSION,
                "catalogue_digest": catalogue_digest(), "response": resp}

    return _decorate(_write(_file(scope, rid), mk))


def update(scope: str, rid: str, expected_version: Any, author: str, *, name: Any = None, request: Any = None,
           response: Any = None, response_given: bool = False) -> Dict[str, Any]:
    """Optimistic version check: a stale ``expected_version`` is a 409 carrying the current version.

    A new request without a response clears the old response (it was computed from another request).
    """
    from motor_ai_sim.gears import CORE_VERSION
    scope = clean_scope(scope)
    path = _file(scope, rid)
    if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 1:
        raise StoreError(422, "invalid_version", "version must be a positive integer")
    new_name = clean_name(name) if name is not None else None
    canon = rhash = None
    if request is not None:
        canon, rhash = prepare_request(request)

    def mk(cur):
        if cur is None:
            raise StoreError(404, "not_found", "No such gearbox record")
        if cur.get("version") != expected_version:
            raise StoreError(409, "stale_version", "The record changed since it was loaded",
                             current_version=cur.get("version"))
        if cur.get("state") == "archived":
            raise StoreError(409, "archived", "Restore the record before changing it")
        rec = dict(cur)
        if new_name is not None:
            rec["name"] = new_name
        target_hash = rec["request_hash"]
        if canon is not None:
            rec.update(request=canon, request_hash=rhash, core_version=CORE_VERSION,
                       catalogue_digest=catalogue_digest(), response=None)
            target_hash = rhash
        if response_given:
            rec["response"] = prepare_response(response, target_hash)
            if rec["response"] is not None:
                rec["core_version"] = rec["response"]["core_version"]
                rec["catalogue_digest"] = catalogue_digest()
        rec["version"] = cur["version"] + 1
        rec["updated_at"] = now_iso()
        rec["updated_by"] = author
        return rec

    return _decorate(_write(path, mk))


def set_state(scope: str, rid: str, state: str, author: str, expected_version: Optional[int] = None) -> Dict[str, Any]:
    scope = clean_scope(scope)
    path = _file(scope, rid)

    def mk(cur):
        if cur is None:
            raise StoreError(404, "not_found", "No such gearbox record")
        if expected_version is not None and cur.get("version") != expected_version:
            raise StoreError(409, "stale_version", "The record changed since it was loaded",
                             current_version=cur.get("version"))
        rec = dict(cur)
        if rec.get("state") != state:
            rec.update(state=state, version=cur["version"] + 1, updated_at=now_iso(), updated_by=author)
        return rec

    return _decorate(_write(path, mk))
