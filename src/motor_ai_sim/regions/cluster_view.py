"""The cluster / load payload one server hands the OTHER region's admin (docs/regions/DESIGN.md).

Same data the local admin graphs read (``/api/admin/nodes``, ``/api/admin/cluster/app``, ``/api/admin/load/live``,
``/api/admin/nodes/{id}/history``) in one answer: current + history of THIS server.  Server metrics only: account
names are replaced by "user N" and job ids by "job N", the per-user table and OS process owners are dropped, so
nothing that identifies a person leaves the server.
"""
from __future__ import annotations

import copy
from typing import Any, Dict

from motor_ai_sim.regions import config as RC

RANGES = ("15m", "1h", "24h", "7d")
HIST_RANGES = ("24h", "7d")


def _alias(owners: Dict[str, str], name: Any) -> str:
    s = str(name or "")
    if not s or s == "other":
        return s
    return owners.setdefault(s, f"user {len(owners) + 1}")


def _anon_jobs(view: Dict[str, Any], owners: Dict[str, str]) -> None:
    view.pop("per_user", None)
    for i, row in enumerate(view.get("items") or [], 1):
        row["owner"] = _alias(owners, row.get("owner"))
        row["run_id"] = f"job {i}"
        if row.get("agent"):
            row["agent"] = "agent"


def _anon_live(live: Dict[str, Any], owners: Dict[str, str]) -> None:
    ul = live.get("user_load") or {}
    ul["users"] = [_alias(owners, u) for u in (ul.get("users") or [])]
    ul["series"] = [{(k if k == "ts" else _alias(owners, k)): v for k, v in pt.items()}
                    for pt in (ul.get("series") or [])]
    live["user_load"] = ul
    snap = live.get("snapshot") or {}
    _anon_jobs(snap, owners)
    live["snapshot"] = snap


def build(rng: str = "1h", hist: str = "24h") -> Dict[str, Any]:
    from motor_ai_sim import cluster_monitor as CM
    from motor_ai_sim.routes import cluster as RCL
    rng = rng if rng in RANGES else "1h"
    hist = hist if hist in HIST_RANGES else "24h"
    live = copy.deepcopy(RCL.load_live(range=rng, top=6, _admin={}))      # the route caches: never mutate its copy
    nodes = copy.deepcopy(CM.list_nodes())
    for n in nodes["nodes"]:
        for p in ((n.get("sample") or {}).get("procs") or []):
            p["user"] = ""                                               # OS account names stay home
    app = CM.app_metrics()
    try:
        app["jobs"] = copy.deepcopy(CM.jobs_view())
    except Exception:                                                    # noqa: BLE001
        app["jobs"] = {"items": []}
    owners: Dict[str, str] = {}
    _anon_jobs(app["jobs"], owners)
    _anon_live(live, owners)
    history = {n["id"]: CM.history(n["id"], hist) for n in nodes["nodes"] if n["status"] != "revoked"}
    return {"region": RC.local_region(), "range": rng, "hist": hist, "nodes": nodes, "app": app, "live": live,
            "history": history}
