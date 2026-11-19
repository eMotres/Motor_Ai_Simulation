"""Dry list: which production members would lose the Assembly tab when `assembly_process` is read behind the NDA / full-view gate.

READ ONLY.  For every production it checks each factory (`oem`) member with the same rule the other production reads use
(`production.access.full_view_denial` -> `model3d.can_download` on the member's account) and prints the productions with members
that would be refused, with their addresses on the terminal (the PR text carries none).

    python scripts/assembly_nda_dry_list.py            # the production root of this environment (MOTRES_PRODUCTION_ROOT)
    python scripts/assembly_nda_dry_list.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def members_without_view(root: Path | None = None) -> List[Dict[str, Any]]:
    from motor_ai_sim import model3d as M3
    from motor_ai_sim import users as U
    from motor_ai_sim.production import store
    base = Path(root) if root is not None else store.production_root()
    out: List[Dict[str, Any]] = []
    for pj in sorted(base.glob("*/*/production.json")):
        rec = store._read_json(pj, default={})
        if not isinstance(rec, dict) or not rec.get("die") or not rec.get("config"):
            continue
        lost = []
        for m in rec.get("members") or []:
            email = str(m.get("email") or "").strip().lower()
            role = str(m.get("role") or "")
            if not email or role != "oem":
                continue                                              # owners and engineers are not gated by the NDA
            try:
                ok = bool(M3.can_download({"id": email, "is_admin": False, "role": role}, U.get_user(email)))
            except Exception:                                         # noqa: BLE001 - when in doubt the gate refuses
                ok = False
            if not ok:
                lost.append(email)
        out.append({"die": rec["die"], "config": rec["config"], "status": rec.get("status") or "active",
                    "oem_members": sum(1 for m in rec.get("members") or [] if m.get("role") == "oem"), "would_lose": lost})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", type=Path)
    a = ap.parse_args(argv)
    rows = members_without_view()
    n = sum(len(r["would_lose"]) for r in rows)
    print("%d productions checked, %d factory member(s) would lose the Assembly tab" % (len(rows), n))
    for r in rows:
        if r["would_lose"]:
            print("%s / %s (%s): %d of %d factory members: %s" % (r["die"], r["config"], r["status"], len(r["would_lose"]),
                                                                   r["oem_members"], ", ".join(r["would_lose"])))
    if a.json:
        a.json.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
