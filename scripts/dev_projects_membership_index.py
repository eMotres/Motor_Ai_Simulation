#!/usr/bin/env python3
"""Explicit one-time migration for legacy Development project visibility.

Dry-run by default. Use --apply once after deploying the membership index; normal
sign-in checks never scan project documents or rebuild this index.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
from motor_ai_sim.development import store
from motor_ai_sim.development.motors import transaction


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write memberships.json; default is dry-run")
    parser.add_argument("--root", type=Path, help="project store root override")
    args = parser.parse_args()
    if args.root:
        os.environ["MOTRES_PROJECTS_ROOT"] = str(args.root.resolve())
    projects = store.list_projects()
    mapping = {}
    for project in projects:
        for member in project.get("members", []):
            email = str(member.get("email") or "").strip().lower() if isinstance(member, dict) else ""
            if email:
                mapping.setdefault(email, []).append(project["id"])
    mapping = {email: sorted(set(ids)) for email, ids in mapping.items()}
    if args.apply:
        with transaction():
            store.rebuild_membership_index()
    print(json.dumps({"mode": "apply" if args.apply else "dry-run", "projects": len(projects),
                      "members": len(mapping), "membership_index": mapping}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
