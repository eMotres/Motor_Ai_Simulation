"""Dry list (READ ONLY): projects with a shared quote and the members who stop seeing it when prices become owner-only by default
(owner 2026-11-22).  A member keeps the view only as a project owner or with ``sees_prices: true``.

    python scripts/quote_price_visibility_dry_list.py --root <projects root>
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

SHARED = Path("cost_estimate") / "shared_quote.json"


def scan(root: Path):
    rows = []
    for pdir in sorted(p for p in Path(root).iterdir() if p.is_dir() and (p / "project.json").is_file()):
        try:
            shared = json.loads((pdir / SHARED).read_text(encoding="utf-8")).get("current")
            project = json.loads((pdir / "project.json").read_text(encoding="utf-8"))
        except Exception:                                           # noqa: BLE001 - no shared quote / unreadable: nothing to list
            continue
        if not shared:
            continue
        members = [m for m in project.get("members", []) if isinstance(m, dict)]
        lose = [m.get("email") for m in members if m.get("role") != "owner" and m.get("sees_prices") is not True]
        rows.append({"project": pdir.name, "shared_at": shared.get("shared_at"),
                     "owners": [m.get("email") for m in members if m.get("role") == "owner"], "lose_the_view": lose})
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", required=True, type=Path)
    for r in scan(ap.parse_args(argv).root):
        print(r["project"], "owners:", r["owners"], "lose the view:", r["lose_the_view"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
