"""List the proposals that are still open in every production (READ-ONLY; owner 2026-11-15 removed the Proposals tab).

Nothing is changed or deleted: the stored proposals stay, staff read them in the Discussion tab ("Earlier proposals, read only").
Run in the API container (it reads MOTRES_PRODUCTION_ROOT / the shared root):

    docker exec -w /app deploy-api-green-1 env PYTHONPATH=/app/src python scripts/production_proposals_report.py [--all]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List


def collect(root: Path, include_all: bool = False) -> List[Dict[str, Any]]:
    """One row per proposal (open and accepted-not-implemented by default; all with ``include_all``) across ``root/<die>/<config>``."""
    rows: List[Dict[str, Any]] = []
    for pj in sorted(Path(root).glob("*/*/production.json")):
        try:
            prod = json.loads(pj.read_text(encoding="utf-8"))
            props = json.loads((pj.parent / "proposals.json").read_text(encoding="utf-8")).get("proposals", []) if (pj.parent / "proposals.json").is_file() else []
        except (OSError, ValueError):
            continue
        for p in props:
            if not isinstance(p, dict):
                continue
            status = str(p.get("status") or "")
            if not include_all and status not in ("open", "accepted"):
                continue
            text = p.get("text") if isinstance(p.get("text"), dict) else {}
            rows.append({"die": prod.get("die"), "config": prod.get("config"), "id": p.get("id"), "part_id": p.get("part_id"),
                         "status": status, "author": p.get("author_email"), "created": str(p.get("created_at") or "")[:10],
                         "text": str(text.get("en") or text.get("zh") or "")[:200]})
    return rows


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--all", action="store_true", help="include rejected and implemented proposals")
    args = ap.parse_args(argv)
    from motor_ai_sim.production import store
    rows = collect(store.production_root(), args.all)
    for r in rows:
        print("%s | %s | %s | %s | %s | %s | %s" % (r["die"], r["config"], r["status"], r["created"], r["author"], r["part_id"] or "-", r["text"]))
    print("%d proposal(s)" % len(rows), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
