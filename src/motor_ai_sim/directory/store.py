"""Store for the Companies & People Directory.

Lives under ``<shared>/directory/`` (override with ``MOTRES_DIRECTORY_ROOT`` for tests).
Uses ``motor_ai_sim.json_store`` for atomic writes under cross-process locks.
Maintains an append-only JSONL audit log.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Optional

from motor_ai_sim import json_store
from motor_ai_sim.directory.models import check_no_bank_fields, now_iso

log = logging.getLogger(__name__)


def directory_root() -> Path:
    env = (os.environ.get("MOTRES_DIRECTORY_ROOT") or "").strip()
    if env:
        p = Path(env)
        p.mkdir(parents=True, exist_ok=True)
        return p
    from motor_ai_sim.workspace import shared_root
    p = Path(str(shared_root())) / "directory"
    p.mkdir(parents=True, exist_ok=True)
    return p


def companies_path() -> Path:
    return directory_root() / "companies.json"


def people_path() -> Path:
    return directory_root() / "people.json"


def affiliations_path() -> Path:
    return directory_root() / "affiliations.json"


def audit_path() -> Path:
    return directory_root() / "audit.jsonl"


# -- Audit log -----------------------------------------------------------------

def append_audit(actor: str, action: str, target_type: str, target_id: str, details: Optional[dict] = None) -> dict:
    p = audit_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    ts = round(time.time(), 3)
    entry = {
        "ts": ts,
        "iso": now_iso(),
        "actor": actor or "system",
        "action": action,
        "target_type": target_type,
        "target_id": target_id,
        "details": details or {},
    }
    # Enforce no bank fields in audit either
    check_no_bank_fields(entry)
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
    lk = json_store.lock_for(p)
    with lk:
        try:
            with open(p, "a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError as e:
            log.warning("directory audit append failed: %s", e)
            raise
    return entry


def read_audit(limit: int = 500, target_id: Optional[str] = None) -> list[dict]:
    p = audit_path()
    if not p.is_file():
        return []
    entries = []
    try:
        with open(p, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    if target_id:
                        if e.get("target_id") == target_id or (e.get("details") or {}).get("company_id") == target_id:
                            entries.append(e)
                    else:
                        entries.append(e)
                except ValueError:
                    continue
    except OSError:
        return []
    return entries[-limit:][::-1]


# -- Companies -----------------------------------------------------------------

def load_companies() -> list[dict]:
    p = companies_path()
    data = json_store.read_json(p, default={"companies": []})
    if isinstance(data, dict):
        return list(data.get("companies") or [])
    if isinstance(data, list):
        return data
    return []


def mutate_companies(mutator: Callable[[list[dict]], Any]) -> list[dict]:
    p = companies_path()

    def _apply(doc: Any) -> dict:
        comps = list(doc.get("companies") or []) if isinstance(doc, dict) else (list(doc) if isinstance(doc, list) else [])
        res = mutator(comps)
        new_list = res if isinstance(res, list) else comps
        check_no_bank_fields(new_list)
        return {"companies": new_list}

    res_doc = json_store.mutate_json(p, _apply, default={"companies": []})
    return list(res_doc.get("companies") or [])


# -- People --------------------------------------------------------------------

def load_people() -> list[dict]:
    p = people_path()
    data = json_store.read_json(p, default={"people": []})
    if isinstance(data, dict):
        return list(data.get("people") or [])
    if isinstance(data, list):
        return data
    return []


def mutate_people(mutator: Callable[[list[dict]], Any]) -> list[dict]:
    p = people_path()

    def _apply(doc: Any) -> dict:
        people = list(doc.get("people") or []) if isinstance(doc, dict) else (list(doc) if isinstance(doc, list) else [])
        res = mutator(people)
        new_list = res if isinstance(res, list) else people
        check_no_bank_fields(new_list)
        return {"people": new_list}

    res_doc = json_store.mutate_json(p, _apply, default={"people": []})
    return list(res_doc.get("people") or [])


# -- Affiliations --------------------------------------------------------------

def load_affiliations() -> list[dict]:
    p = affiliations_path()
    data = json_store.read_json(p, default={"affiliations": []})
    if isinstance(data, dict):
        return list(data.get("affiliations") or [])
    if isinstance(data, list):
        return data
    return []


def mutate_affiliations(mutator: Callable[[list[dict]], Any]) -> list[dict]:
    p = affiliations_path()

    def _apply(doc: Any) -> dict:
        affs = list(doc.get("affiliations") or []) if isinstance(doc, dict) else (list(doc) if isinstance(doc, list) else [])
        res = mutator(affs)
        new_list = res if isinstance(res, list) else affs
        check_no_bank_fields(new_list)
        return {"affiliations": new_list}

    res_doc = json_store.mutate_json(p, _apply, default={"affiliations": []})
    return list(res_doc.get("affiliations") or [])
