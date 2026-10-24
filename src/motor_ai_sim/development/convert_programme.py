"""Offline dry run: how each Development project would map onto a programme.

    python -m motor_ai_sim.development.convert_programme --output manifest.json [--project ID ...]

Reads the store through ``legacy_adapter`` only and writes one manifest file
outside the store: the intended programme document per project, before/after
counts, sha256 of every source file, catalog links that do not resolve, access
warnings, and a store fingerprint taken before and after to show nothing moved.
The mutating migration is disabled: ``--apply`` exits with an error.

Mapping (one legacy project = one machine): programme ``legacy-<project id>``,
one machine component with one winding port, one variant per linked or
historically linked configuration (status current/trial/history, content hash
pinned from the catalog), one position, one lot of one unit allocated to it, and
no connections. Specification, thread and snapshots stay in the legacy project.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from motor_ai_sim import json_store
from motor_ai_sim.development import legacy_adapter, programme, store

MANIFEST_KIND = "development-programme-dry-run"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes(project_id: str) -> dict[str, str]:
    base = store.path_for(project_id)
    if not base.is_dir():
        return {}
    return {path.relative_to(base).as_posix(): _sha256_file(path)
            for path in sorted(base.rglob("*")) if path.is_file()}


def store_fingerprint() -> str:
    """sha256 over every file path, size and content under the projects root."""
    base = store.root()
    digest = hashlib.sha256()
    if base.is_dir():
        for path in sorted(base.rglob("*")):
            if path.is_file():
                digest.update(path.relative_to(base).as_posix().encode("utf-8") + b"\0")
                digest.update(_sha256_file(path).encode("ascii") + b"\n")
    return digest.hexdigest()


def _variant_id(die: str, config: str) -> str:
    # Source-scoped and deterministic: the same catalog identity maps to the same id on every run.
    return "v-" + hashlib.sha256(f"{die.casefold()}\0{config.casefold()}".encode("utf-8")).hexdigest()[:12]


def plan_project(view: dict, catalog_lookup: Callable[[str, str], str | None] | None) -> dict:
    project_id = str(view["id"])
    programme_id = f"legacy-{project_id}"
    unresolved, warnings, assumptions = [], [], ["one machine per legacy project, quantity 1"]
    variants, seen = [], set()

    def add(die: str, config: str, status: str, origin: str) -> None:
        identity = (die.casefold(), config.casefold())
        if not die or not config or identity in seen:
            return
        seen.add(identity)
        content_hash = None
        try:
            content_hash = catalog_lookup(die, config) if catalog_lookup else None
        except Exception:  # noqa: BLE001 - recorded as unresolved below
            content_hash = None
        if content_hash is None:
            unresolved.append({"die": die, "config": config, "origin": origin,
                               "reason": "catalog configuration not found; the link would not be pinned"})
        variants.append({"id": _variant_id(die, config), "status": status, "label": f"{die}/{config}",
                         "ref": {"source": "catalog", "die": die, "config": config, "content_hash": content_hash}})

    for status in ("current", "trial"):
        for row in view["linked_motors"][status]:
            add(str(row.get("die") or ""), str(row.get("config") or ""), status, f"linked_motors ({status})")
    for row in view["motor_history"]:
        if row.get("unlinked_at") or row.get("unlinked_by"):
            add(str(row.get("die") or ""), str(row.get("config") or ""), "history", "motor_history")

    current = [v["id"] for v in variants if v["status"] == "current"]
    if len(current) > 1:
        assumptions.append("several current configurations; an engineer must choose one before conversion")
    document = {
        "id": programme_id, "title": str(view.get("title") or project_id),
        "components": [{"id": "machine", "kind": "machine", "name": str(view.get("title") or ""),
                        "ports": [{"id": "w1", "kind": "winding"}], "variants": variants}],
        "positions": [{"id": "machine-1", "component": "machine"}],
        "instances": [{"id": "unit-1", "component": "machine", "variant": current[0] if len(current) == 1 else None,
                       "quantity": 1, "allocations": ["machine-1"]}],
        "connections": [],
    }
    if not programme.valid_programme_id(programme_id):
        issues = [programme.issue("invalid_id", "id", f"{programme_id!r} is longer than {programme.MAX_ID_LENGTH} characters")]
    else:
        # Validate as programme.validated would, minus pinning: unresolved links are reported above.
        _, issues = programme.normalize(document, programme_id)

    for member in view["members"]:
        email = str(member.get("email") or "").strip().lower()
        if email:
            warnings.append({"code": "member_loses_access", "email": email, "role": member.get("role"),
                             "message": "programmes are admin-only in Stage 1; this member would not see the programme"})
    if view["fields"].get("pending_invites"):
        warnings.append({"code": "pending_invites", "count": len(view["fields"]["pending_invites"]),
                         "message": "pending invitations are not carried into the programme"})

    linked = view["linked_motors"]
    return {
        "project_id": project_id,
        "programme_id": programme_id,
        "source_hashes": source_hashes(project_id),
        "before": {"members": len(view["members"]), "linked_current": len(linked["current"]),
                   "linked_trial": len(linked["trial"]), "motor_history": len(view["motor_history"]),
                   "snapshots": len(view["snapshots"]), "spec_versions": len(view["spec_versions"]),
                   "draft_revision": (view["draft"] or {}).get("revision"),
                   "unknown_fields": sorted(view["unknown_fields"])},
        "after": {"components": 1, "variants": len(variants), "positions": 1, "instances": 1, "connections": 0,
                  "retained_in_legacy_project": {"spec_versions": len(view["spec_versions"]),
                                                 "snapshots": len(view["snapshots"]), "members": len(view["members"])}},
        "intended_programme": document,
        "validation_issues": issues,
        "unresolved_links": unresolved,
        "access_warnings": warnings,
        "assumptions": assumptions,
    }


def dry_run(project_ids: list[str] | None = None, *,
            catalog_lookup: Callable[[str, str], str | None] | None = None) -> dict:
    before = store_fingerprint()
    ids = project_ids if project_ids else [p["id"] for p in store.list_projects() if isinstance(p.get("id"), str)]
    projects, missing = [], []
    for project_id in ids:
        view = legacy_adapter.read_project(project_id)
        if view is None:
            missing.append(project_id)
        else:
            projects.append(plan_project(view, catalog_lookup))
    return {
        "manifest_kind": MANIFEST_KIND, "manifest_version": 1, "mutating_migration": "disabled",
        "generated_at": datetime.now(timezone.utc).isoformat(), "store_root": str(store.root()),
        "store_fingerprint_before": before, "store_fingerprint_after": store_fingerprint(),
        "totals": {"projects": len(projects), "missing": len(missing),
                   "unresolved_links": sum(len(p["unresolved_links"]) for p in projects),
                   "access_warnings": sum(len(p["access_warnings"]) for p in projects),
                   "projects_with_issues": sum(1 for p in projects if p["validation_issues"])},
        "missing_projects": missing,
        "projects": projects,
    }


def write_manifest(manifest: dict, output: Path) -> Path:
    output = Path(output).expanduser().resolve()
    base = store.root()
    if output == base or base in output.parents:
        raise ValueError("the dry-run manifest must be written outside the project store")
    json_store.atomic_write_json(output, manifest)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", "-o", required=True, help="manifest path, outside the project store")
    parser.add_argument("--project", action="append", default=[], help="limit to this project id (repeatable)")
    parser.add_argument("--no-catalog", action="store_true", help="do not resolve catalog links")
    parser.add_argument("--apply", action="store_true", help="disabled in Stage 1")
    args = parser.parse_args(argv)
    if args.apply:
        print("The mutating programme migration is disabled; run without --apply for the dry run.", file=sys.stderr)
        return 2
    lookup = None
    if not args.no_catalog:
        from motor_ai_sim.development.programme_routes import catalog_lookup as lookup
    manifest = dry_run(args.project or None, catalog_lookup=lookup)
    try:
        path = write_manifest(manifest, Path(args.output))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"manifest": str(path), **manifest["totals"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
