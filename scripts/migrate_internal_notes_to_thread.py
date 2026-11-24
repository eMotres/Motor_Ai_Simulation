"""Move every project's "Internal notes" into its thread as a staff-only message (owner 2026-11-24).  DRY RUN by default.

    MOTRES_PROJECTS_ROOT=<projects root> PYTHONPATH=<repo>/src python scripts/migrate_internal_notes_to_thread.py            # dry run
    MOTRES_PROJECTS_ROOT=<projects root> PYTHONPATH=<repo>/src python scripts/migrate_internal_notes_to_thread.py --apply    # writes

Idempotent (stable ids), the original text is kept in ``<project>/internal_notes_backup.json`` and in ``project.json``, no mail and no
notification is sent.  See ``motor_ai_sim.development.notes_migration``."""
from __future__ import annotations

import argparse
import json
from collections import Counter

from motor_ai_sim.development import notes_migration


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write (without it: dry run, nothing is written)")
    args = parser.parse_args()
    rows = notes_migration.apply() if args.apply else notes_migration.plan()
    for row in rows:
        if row["state"] != "empty":
            print(json.dumps(row))
    print(json.dumps({"mode": "apply" if args.apply else "dry-run", "projects": len(rows), "by_state": dict(Counter(r["state"] for r in rows))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
