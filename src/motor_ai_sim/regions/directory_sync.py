"""Pull the peer region's account directory entries (docs/regions/DESIGN.md section 8) - the 5-minute timer.

    python -m motor_ai_sim.regions.directory_sync

The same thing ``POST /api/admin/regions/directory/sync`` does for an admin, without a sign-in: the signed peer channel is
the credential.  Exit codes: 0 synced, 4 the peer did not answer (the last sync stays), 6 single-region mode.
"""
from __future__ import annotations

import sys
from typing import Any, Dict, Optional

from motor_ai_sim.regions import config as RC
from motor_ai_sim.regions import peer as P
from motor_ai_sim.regions import store as RS


def sync(transport: Any = None) -> Dict[str, Any]:
    """-> ``{ok, entries, updated}`` or ``{ok: False, code}``; stores nothing when the peer does not answer."""
    if not RC.peer_configured():
        return {"ok": False, "code": "single_region"}
    got = P.get("/directory", **({"transport": transport} if transport is not None else {}))
    if not isinstance(got, dict) or RC.norm_region(got.get("region")) != RC.peer_region():
        return {"ok": False, "code": "peer_unreachable"}
    doc = RS.store_remote_directory(got.get("entries") or {}, RC.peer_region())
    return {"ok": True, "entries": len(doc["entries"]), "updated": doc["updated"]}


def main(argv: Optional[list] = None) -> int:
    res = sync()
    print(res)
    return 0 if res["ok"] else (6 if res.get("code") == "single_region" else 4)


if __name__ == "__main__":
    sys.exit(main())
