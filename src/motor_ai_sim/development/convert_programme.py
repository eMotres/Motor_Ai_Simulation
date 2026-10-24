"""Offline dry-run conversion command for Development projects to Programme.

Produces an immutable migration manifest:
- Intended component / variant / instance mappings
- Before and after counts (motors, members, snapshots, versions, components, instances)
- Source SHA256 hashes of original files
- Unresolved catalog links and missing source configs
- Access warnings (broad roles, multiple owners, unverified members)
- Mutating migration stays STRICTLY DISABLED in Stage 1 (disabled until Stage 7).
- Pure read-only operation: performs zero store writes during dry run.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from motor_ai_sim import json_store
from motor_ai_sim.development import store
from motor_ai_sim.development.legacy_adapter import LegacyReadAdapter


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dry_run_migrate_project(
    project_id: str,
    *,
    catalog_lookup: Callable[[str, str], str | None] | None = None,
    output_path: Path | None = None,
) -> dict:
    """Run pure offline dry-run conversion for a project without mutating the store."""
    if not store.valid_id(project_id):
        raise ValueError(f"Invalid project id: {project_id}")

    proj_dir = store.path_for(project_id)
    if not proj_dir.is_dir():
        raise ValueError(f"Project directory does not exist: {project_id}")

    # Step 1: Read original project through pure legacy read adapter
    project = LegacyReadAdapter.read_project(project_id, as_admin=True)
    if not project:
        raise ValueError(f"Project {project_id} not found")

    if project.get("kind") == "programme":
        return {
            "project_id": project_id,
            "status": "already_programme",
            "message": "Project is already in programme format",
            "programme_revision": project.get("programme_revision", 1),
        }

    draft_info = LegacyReadAdapter.read_draft(project_id)
    versions = LegacyReadAdapter.read_versions(project_id)
    snapshots = LegacyReadAdapter.read_snapshots(project_id)

    # Step 2: Compute source file hashes
    source_hashes = {}
    p_json = proj_dir / "project.json"
    if p_json.is_file():
        source_hashes["project.json"] = _file_sha256(p_json)
    d_json = proj_dir / "draft.json"
    if d_json.is_file():
        source_hashes["draft.json"] = _file_sha256(d_json)

    for v_path in sorted(proj_dir.glob("versions/*.json")):
        source_hashes[f"versions/{v_path.name}"] = _file_sha256(v_path)
    for s_path in sorted(proj_dir.glob("snapshots/*.json")):
        source_hashes[f"snapshots/{s_path.name}"] = _file_sha256(s_path)

    linked_motors = project.get("linked_motors") or []
    members = project.get("members") or []

    before_counts = {
        "linked_motors": len(linked_motors),
        "members": len(members),
        "snapshots": len(snapshots),
        "versions": len(versions),
    }

    # Step 3: Analyze linked motors and propose component mapping
    # Rule: single-machine project becomes one default component with quantity 1, clearly marked inferred.
    # Multiple linked configs become design alternatives under the default component pending owner confirmation.
    unresolved_links = []
    access_warnings = []
    proposed_components = []
    proposed_instances = []
    proposed_variants = []

    default_comp_id = "M1"
    variants = []
    for idx, lm in enumerate(linked_motors):
        die = str(lm.get("die") or "").strip()
        config = str(lm.get("config") or "").strip()
        status = str(lm.get("status") or "current")
        var_id = f"var-{idx + 1}"

        source_hash = None
        if catalog_lookup:
            cat_hash = catalog_lookup(die, config)
            if cat_hash:
                source_hash = cat_hash
            else:
                unresolved_links.append({
                    "die": die,
                    "config": config,
                    "reason": "Catalog configuration not found or unavailable in current environment",
                })
        else:
            # Note unverified catalog link
            unresolved_links.append({
                "die": die,
                "config": config,
                "reason": "No catalog lookup provided during dry run; hash unpinned",
            })

        variants.append({
            "id": var_id,
            "label": f"{die}/{config}",
            "status": status if status in ("current", "trial", "history") else "current",
            "die": die,
            "config": config,
            "source_hash": source_hash,
            "planned_quantity": 1 if idx == 0 else 0,
        })
        proposed_variants.append(var_id)

    # Inferred default machine component
    proposed_components.append({
        "id": default_comp_id,
        "name": project.get("title") or "Main machine",
        "kind": "machine",
        "planned_quantity": 1,
        "ports": ["W1"],
        "inferred": True,
        "variants": variants,
        "notes": "Inferred single-machine component; multiple linked configs preserved as design variants pending owner confirmation",
    })

    # Default instance for the planned unit
    inst_id = f"{default_comp_id}-01"
    proposed_instances.append({
        "id": inst_id,
        "component_id": default_comp_id,
        "position": inst_id,
        "variant_id": variants[0]["id"] if variants else None,
        "allocation_state": "allocated",
    })

    # Step 4: Access warnings
    owners = [m for m in members if m.get("role") == "owner"]
    if len(owners) > 1:
        access_warnings.append({
            "code": "multiple_owners",
            "message": f"Project has {len(owners)} owners; Stage 2 requires explicit assigned programme admin pair",
        })
    for m in members:
        if m.get("role") == "engineer":
            access_warnings.append({
                "code": "global_engineer_access",
                "email": m.get("email"),
                "message": "Global engineer membership does not grant automatic access to restricted programme in Stage 2",
            })

    after_counts = {
        "components": len(proposed_components),
        "variants": len(proposed_variants),
        "instances": len(proposed_instances),
        "connections": 0,
    }

    intended_mapping = {
        "project_id": project_id,
        "kind": "programme",
        "title": project.get("title", ""),
        "components": proposed_components,
        "instances": proposed_instances,
        "connections": [],
        "contract_baselines": [],
    }

    manifest_content = {
        "manifest_type": "dry_run_migration_manifest",
        "stage": 1,
        "mutating_enabled": False,
        "project_id": project_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_hashes": source_hashes,
        "before_counts": before_counts,
        "after_counts": after_counts,
        "intended_mapping": intended_mapping,
        "unresolved_links": unresolved_links,
        "access_warnings": access_warnings,
        "notes": [
            "Mutating migration remains disabled until Stage 7.",
            "Single-machine topology inferred with quantity 1 pending owner review.",
            "No files were written or modified in the store during this dry run.",
        ],
    }

    manifest_bytes = json.dumps(manifest_content, sort_keys=True, indent=2).encode("utf-8")
    manifest_content["manifest_sha256"] = _sha256(manifest_bytes)

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(manifest_bytes)

    return manifest_content


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline dry-run conversion of development project to programme")
    parser.add_argument("project_id", help="Slug identifier of the project to dry-run")
    parser.add_argument("--output", "-o", help="Optional output path to write migration manifest JSON", default=None)
    parser.add_argument("--mutate", action="store_true", help="Mutating flag (strictly disabled in Stage 1)")

    args = parser.parse_args(argv)

    if args.mutate:
        sys.stderr.write("ERROR: Mutating migration mode is disabled in Stage 1. It will be enabled only in Stage 7.\n")
        return 1

    try:
        manifest = dry_run_migrate_project(
            args.project_id,
            output_path=Path(args.output) if args.output else None,
        )
        print(json.dumps(manifest, indent=2))
        return 0
    except Exception as exc:
        sys.stderr.write(f"Error during dry-run conversion: {exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
