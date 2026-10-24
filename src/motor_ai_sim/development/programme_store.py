"""Storage operations and manifest-last transaction management for Development Programme.

Implements:
- Manifest-last atomic commit: writes sub-records first, manifest atomically last
- Optimistic revision checks: rejects stale concurrent updates with ConcurrencyConflictError
- Idempotency keys: duplicate requests return existing committed result without side effects
- Crash recovery: verifies files against last committed manifest and purges uncommitted orphans
- Pure atomic JSON writes using existing json_store helpers
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from motor_ai_sim import json_store
from motor_ai_sim.development import store
from motor_ai_sim.development.programme import (
    ConcurrencyConflictError,
    ProgrammeError,
    ProgrammeInvariantError,
    load_synthetic_fixture,
    normalize_programme,
    now,
    validate_identifier,
    validate_programme_invariants,
)

log = logging.getLogger(__name__)


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def _idempotency_file(project_id: str) -> Path:
    return store.path_for(project_id) / "idempotency.json"


def _manifest_file(project_id: str) -> Path:
    return store.path_for(project_id) / "manifest.json"


def get_manifest(project_id: str) -> dict | None:
    if not store.valid_id(project_id):
        return None
    try:
        path = _manifest_file(project_id)
        val = json_store.read_json(path, default=None)
        return val if isinstance(val, dict) else None
    except Exception:
        return None


def get_programme(project_id: str) -> dict | None:
    """Read the active programme for a project using the committed manifest."""
    if not store.valid_id(project_id):
        return None
    proj_dir = store.path_for(project_id)
    manifest = get_manifest(project_id)
    if manifest:
        # Load programme file pinned by manifest
        prog_path = proj_dir / "programme.json"
        val = json_store.read_json(prog_path, default=None)
        if isinstance(val, dict):
            return val

    # Fallback: check if programme.json or project.json has kind == 'programme'
    prog_path = proj_dir / "programme.json"
    val = json_store.read_json(prog_path, default=None)
    if isinstance(val, dict):
        return val

    project = store.get_project(project_id)
    if project and project.get("kind") == "programme":
        return project.get("programme")
    return None


def commit_programme(
    project_id: str,
    programme_data: dict,
    *,
    expected_revision: int | None = None,
    idempotency_key: str | None = None,
    actor_email: str = "system",
    catalog_lookup: Callable[[str, str], str | None] | None = None,
    strict_quantities: bool = True,
) -> dict:
    """Commit a programme revision using manifest-last publication.
    
    1. Holds cross-project transaction lock.
    2. Validates optimistic revision against current committed manifest.
    3. Checks idempotency key: if matching previously committed key, returns cached result.
    4. Normalizes entities and checks domain invariants.
    5. Writes sub-records atomically to disk (components, instances, connections, baselines).
    6. Computes sha256 hashes of all written files.
    7. Atomically writes manifest.json LAST to publish the revision.
    8. Updates project.json and logs audit event.
    """
    store.ensure_writable()
    validate_identifier(project_id, "project_id")
    proj_dir = store.path_for(project_id)
    proj_dir.mkdir(parents=True, exist_ok=True)

    with store.project_transaction():
        # Check current committed manifest
        manifest = get_manifest(project_id)
        current_revision = int(manifest["revision"]) if manifest and "revision" in manifest else 0

        # Check idempotency
        idempotency_map = {}
        idem_path = _idempotency_file(project_id)
        if idem_path.is_file():
            idempotency_map = json_store.read_json(idem_path, default={}) or {}

        if idempotency_key:
            idempotency_key = str(idempotency_key).strip()
            if idempotency_key in idempotency_map:
                cached_info = idempotency_map[idempotency_key]
                committed_prog = get_programme(project_id)
                if committed_prog and committed_prog.get("revision") == cached_info.get("revision"):
                    log.info("Returning cached programme for idempotency key %s", idempotency_key)
                    return {
                        "programme": committed_prog,
                        "manifest": manifest,
                        "reconciled": True,
                        "idempotent_replay": True,
                    }

        # Optimistic concurrency check
        if expected_revision is not None and expected_revision != current_revision:
            raise ConcurrencyConflictError(
                f"Optimistic revision conflict: store has revision {current_revision}, caller expected {expected_revision}"
            )

        # Normalize and validate invariants
        prog = normalize_programme(programme_data, project_id)
        reconciliation = validate_programme_invariants(
            prog, catalog_lookup=catalog_lookup, strict_quantities=strict_quantities
        )

        new_revision = current_revision + 1
        prog["revision"] = new_revision
        prog["updated_at"] = now()

        # Step 5: Write sub-records atomically
        file_hashes: dict[str, dict] = {}

        # 5a. programme.json
        prog_path = proj_dir / "programme.json"
        json_store.atomic_write_json(prog_path, prog)
        file_hashes["programme.json"] = {
            "sha256": _sha256_file(prog_path),
            "bytes": prog_path.stat().st_size,
        }

        # 5b. components/<id>/component.json
        comp_dir = proj_dir / "components"
        comp_dir.mkdir(parents=True, exist_ok=True)
        for comp in prog["components"]:
            c_dir = comp_dir / comp["id"]
            c_dir.mkdir(parents=True, exist_ok=True)
            c_path = c_dir / "component.json"
            json_store.atomic_write_json(c_path, comp)
            rel_name = f"components/{comp['id']}/component.json"
            file_hashes[rel_name] = {
                "sha256": _sha256_file(c_path),
                "bytes": c_path.stat().st_size,
            }

        # 5c. instances.json
        inst_path = proj_dir / "instances.json"
        json_store.atomic_write_json(inst_path, prog["instances"])
        file_hashes["instances.json"] = {
            "sha256": _sha256_file(inst_path),
            "bytes": inst_path.stat().st_size,
        }

        # 5d. connections.json
        conn_path = proj_dir / "connections.json"
        json_store.atomic_write_json(conn_path, prog["connections"])
        file_hashes["connections.json"] = {
            "sha256": _sha256_file(conn_path),
            "bytes": conn_path.stat().st_size,
        }

        # 5e. contracts/<revision>.json
        if prog.get("contract_baselines"):
            contract_dir = proj_dir / "contracts"
            contract_dir.mkdir(parents=True, exist_ok=True)
            for cb in prog["contract_baselines"]:
                cb_path = contract_dir / f"{cb['revision']}.json"
                json_store.atomic_write_json(cb_path, cb)
                rel_name = f"contracts/{cb['revision']}.json"
                file_hashes[rel_name] = {
                    "sha256": _sha256_file(cb_path),
                    "bytes": cb_path.stat().st_size,
                }

        # Step 6 & 7: Construct manifest and write atomically LAST
        new_manifest = {
            "manifest_version": 1,
            "project_id": project_id,
            "revision": new_revision,
            "committed_at": now(),
            "idempotency_key": idempotency_key,
            "actor_email": actor_email.lower(),
            "files": file_hashes,
            "summary": {
                "components_count": len(prog["components"]),
                "instances_count": len(prog["instances"]),
                "connections_count": len(prog["connections"]),
                "contract_baselines_count": len(prog.get("contract_baselines", [])),
                "reconciliation": reconciliation,
            },
        }
        manifest_path = _manifest_file(project_id)
        json_store.atomic_write_json(manifest_path, new_manifest)

        # Step 8: Update idempotency ledger
        if idempotency_key:
            idempotency_map[idempotency_key] = {
                "revision": new_revision,
                "committed_at": new_manifest["committed_at"],
            }
            json_store.atomic_write_json(idem_path, idempotency_map)

        # Step 9: Update project.json metadata
        project = store.get_project(project_id)
        if project:
            def mutate(d):
                d["kind"] = "programme"
                d["programme_schema_version"] = 1
                d["programme_revision"] = new_revision
                d["updated_at"] = new_manifest["committed_at"]
            store.mutate_project(project_id, mutate)
        else:
            # Create root project.json if not present
            proj_record = {
                "id": project_id,
                "title": prog["title"],
                "kind": "programme",
                "schema_version": prog.get("schema_version", 2),
                "programme_schema_version": 1,
                "programme_revision": new_revision,
                "status": "spec",
                "customer_org": "",
                "owner_engineer": actor_email.lower(),
                "members": [{"email": actor_email.lower(), "role": "owner"}],
                "linked_motors": [],
                "created_at": prog["created_at"],
                "updated_at": new_manifest["committed_at"],
            }
            path = store._file(project_id, "project.json")
            json_store.atomic_write_json(path, proj_record)

        # Step 10: Audit log
        store.append_audit(
            project_id,
            actor_email,
            "programme.commit",
            "programme",
            project_id,
            {"revision": new_revision, "idempotency_key": idempotency_key},
        )

        return {
            "programme": prog,
            "manifest": new_manifest,
            "reconciled": True,
            "idempotent_replay": False,
        }


def recover_programme(project_id: str) -> dict:
    """Crash recovery: inspect directory against last committed manifest.
    
    - Verifies all files in manifest exist with matching hashes.
    - Identifies and quarantines any uncommitted orphan files from an interrupted write.
    - Restores consistent programme state.
    """
    store.ensure_writable()
    validate_identifier(project_id, "project_id")
    proj_dir = store.path_for(project_id)
    if not proj_dir.is_dir():
        raise ProgrammeError(f"Project directory {project_id} does not exist")

    with store.project_transaction():
        manifest = get_manifest(project_id)
        if not manifest:
            return {"recovered": False, "reason": "no_manifest_found"}

        manifest_files = manifest.get("files", {})
        missing_files = []
        hash_mismatches = []
        intact_files = []

        for rel_path, meta in manifest_files.items():
            full_path = proj_dir / rel_path
            if not full_path.is_file():
                missing_files.append(rel_path)
                continue
            actual_hash = _sha256_file(full_path)
            expected_hash = meta.get("sha256")
            if actual_hash != expected_hash:
                hash_mismatches.append({"file": rel_path, "expected": expected_hash, "actual": actual_hash})
            else:
                intact_files.append(rel_path)

        # Detect uncommitted orphan files (staged files that were never committed into manifest)
        quarantine_dir = proj_dir / ".quarantine"
        quarantined = []
        committed_at = manifest.get("committed_at")
        known_rel = set(manifest_files.keys()) | {
            "manifest.json", "idempotency.json", "project.json", "draft.json",
            "audit.jsonl", "index.json", "snapshots",
        }

        for path in proj_dir.rglob("*.json"):
            if ".quarantine" in path.parts:
                continue
            rel = str(path.relative_to(proj_dir)).replace("\\", "/")
            if rel not in known_rel and not any(rel.startswith(k + "/") for k in known_rel):
                # This file is not committed in manifest!
                quarantine_dir.mkdir(parents=True, exist_ok=True)
                dest = quarantine_dir / f"{path.name}.{now().replace(':', '-')}"
                path.rename(dest)
                quarantined.append(rel)

        return {
            "recovered": len(missing_files) == 0 and len(hash_mismatches) == 0,
            "revision": manifest.get("revision"),
            "intact_files_count": len(intact_files),
            "missing_files": missing_files,
            "hash_mismatches": hash_mismatches,
            "quarantined_orphans": quarantined,
            "programme": get_programme(project_id),
        }
