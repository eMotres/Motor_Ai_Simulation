"""Archive a die that is not needed any more, in ANY layer, reversibly (owner 2026-11-17: archive what is not needed in the
Motors catalog; we never delete).

Before this, only a SHARED die could be hidden (``POST /api/family/die/{die}/retire``, a tombstone that hides it from every
read).  A die that lives in a workspace had only DELETE.  This is the same idea for the other layers, and lighter:

* nothing on disk is moved, renamed or deleted.  The state lives in ONE sidecar file beside the other object stores
  (``<shared root>/object_registry/die_archive.json``, override ``MOTRES_OBJECT_REGISTRY_ROOT``, see ``display_state``);
* only the LISTINGS drop an archived die: the Motors catalog tree, the pickers built from it, the admin motor picker
  and the Overview "Active" view.  Every read path (``resolve_die_dir``, stored results, projects and productions that
  name the die, the name check of a new die) is untouched, so nothing that names the die breaks;
* the archive is per workspace owner: the key is ``<workspace id>/<die>``, and an entry is archived only for the viewer
  whose workspace that is.  Archiving the owner's old copy hides it in his catalog and nowhere else; his own PUBLISHED
  copy of the same name answers to the same key, while the people it is published to keep seeing it (review of #726:
  an archive must never hide a die from a grantee).  Another account's published die cannot be archived here, and a
  shared die keeps the existing Retire (a tombstone for every user);
* admin only, written to the admin audit both ways, and refused while the die is in use (:func:`refusals`): a running
  or queued job, an open production, a current link of an open project, a product revision.

``die_rename.py`` moves the keys with the die (:func:`rename_die_in`).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from motor_ai_sim import json_store

REASON_MAX = 200
HISTORY_MAX = 2000
#: the refusal codes in words (the API detail; the web translates the codes itself)
REFUSAL_TEXT = {"running_job": "a job is running or queued for it", "open_production": "it has an open production",
                "project_link": "an open project links it", "product_revision": "it holds a product revision"}
#: the agent job launcher's status file (``motres-agent-job``); read when this process can see it
JOBS_STATUS = "/var/lib/motres-agent-job/jobs_status.json"


def store_path() -> Path:
    from motor_ai_sim.display_state import registry_root
    return registry_root() / "die_archive.json"


def _empty() -> Dict[str, Any]:
    return {"version": 1, "dies": {}, "history": []}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load() -> Dict[str, Any]:
    data = json_store.read_json(store_path(), default=None)
    if not isinstance(data, dict):
        return _empty()
    if not isinstance(data.get("dies"), dict):
        data["dies"] = {}
    if not isinstance(data.get("history"), list):
        data["history"] = []
    return data


def key(scope: str, die: str) -> str:
    """``<workspace id>/<die>``: the die name as stored on disk."""
    if not scope or not die:
        raise ValueError("scope and die required")
    return f"{scope}/{die}"


def _viewer() -> Optional[str]:
    try:
        from motor_ai_sim.workspace import workspace
        return str(workspace().id or "") or None
    except Exception:                                       # noqa: BLE001
        return None


def scope_of_entry(entry: Dict[str, Any]) -> Optional[str]:
    """The archive scope an ``iter_dies`` entry answers to FOR THIS VIEWER: the viewer's workspace id for a die of the
    viewer's own workspace or own published namespace; None for anything else (a shared die: Retire covers it; another
    account's published die: not the viewer's to archive, and never hidden by somebody else's archive)."""
    layer = str(entry.get("layer") or "")
    me = _viewer()
    if layer == "workspace":
        return me
    if layer == "published" and me and str(entry.get("owner_id") or "") == me:
        return me
    return None


def archived_keys(doc: Optional[Dict[str, Any]] = None) -> frozenset:
    """Every archived key; empty when the store cannot be read (a listing then shows what it showed before)."""
    try:
        return frozenset((doc if doc is not None else load()).get("dies") or {})
    except Exception:                                       # noqa: BLE001
        return frozenset()


def is_archived(entry: Dict[str, Any], keys: Optional[Iterable[str]] = None) -> bool:
    """Is this ``iter_dies`` entry archived?  ``keys`` = :func:`archived_keys` for a batch."""
    scope = scope_of_entry(entry)
    if not scope:
        return False
    keys = archived_keys() if keys is None else keys
    return f"{scope}/{entry.get('die') or entry.get('name')}" in keys


def for_scope(scope: str, doc: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """The archived dies of one workspace, by name."""
    dies = (doc if doc is not None else load()).get("dies") or {}
    out = []
    for k, rec in sorted(dies.items()):
        s, _sep, die = k.partition("/")
        if s == scope and isinstance(rec, dict):
            out.append({"die": die, **{f: rec.get(f) for f in ("layer", "by", "at", "reason", "configs")}})
    return out


def _mutate(fn) -> Dict[str, Any]:
    out: Dict[str, Any] = {}

    def mutate(data: Any) -> Any:
        if not isinstance(data, dict):
            data = _empty()
        data.setdefault("dies", {})
        data.setdefault("history", [])
        fn(data, out)
        if len(data["history"]) > HISTORY_MAX:
            del data["history"][: len(data["history"]) - HISTORY_MAX]
        return data

    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    json_store.mutate_json(path, mutate, default=_empty())
    return out


def archive(scope: str, die: str, *, layer: str, by: str, reason: str = "", configs: Iterable[str] = ()) -> Dict[str, Any]:
    """Archive ``die`` for ``scope``; returns the record.  Read-latest, merge, atomic write under the store's lock."""
    k = key(scope, die)
    rec = {"layer": str(layer), "by": str(by or ""), "at": _now(),
           "reason": " ".join(str(reason or "").split())[:REASON_MAX], "configs": sorted(str(c) for c in configs)}

    def fn(data, out):
        data["dies"][k] = rec
        data["history"].append({"key": k, "to": "archived", "by": rec["by"], "at": rec["at"], "reason": rec["reason"]})
        out["rec"] = rec
    return _mutate(fn)["rec"]


def restore(scope: str, die: str, *, by: str) -> bool:
    """Undo :func:`archive`; True iff it was archived."""
    k = key(scope, die)

    def fn(data, out):
        if data["dies"].pop(k, None) is not None:
            data["history"].append({"key": k, "to": "active", "by": str(by or ""), "at": _now(), "reason": ""})
            out["hit"] = True
    return bool(_mutate(fn).get("hit"))


# ── what keeps a die in use ───────────────────────────────────────────────────────────────────────────────────────────

def _live_jobs() -> List[Dict[str, str]]:
    """``[{"die", "where"}]`` of every queued or running job: the application's own queue (``jobs.JobRecord.target``) and,
    when this process can read it, the agent job launcher's status file (its ``job.json`` names the die).

    FAILS OPEN: a source that cannot be read (no queue, no status file in this container, a job that names no die)
    contributes nothing, so the running-job refusal is a best-effort guard, not a lock.  That is acceptable because the
    archive moves nothing: a job that runs on an archived die finishes and files its results as before, and Restore
    brings the die back into the listing."""
    out: List[Dict[str, str]] = []
    try:
        from motor_ai_sim import jobs as _J
        q = _J.queue()
        recs = q.live_records() if hasattr(q, "live_records") else q.list_for_owner("", limit=500, is_admin=True)
        for r in recs:
            if r.state in (_J.JobState.QUEUED, _J.JobState.RUNNING) and (r.target or {}).get("die"):
                out.append({"die": str(r.target["die"]), "where": f"{r.kind} {r.run_id}"})
    except Exception:                                       # noqa: BLE001
        pass
    try:
        doc = json.loads(Path(os.environ.get("MOTRES_JOBS_STATUS") or JOBS_STATUS).read_text(encoding="utf-8"))
        for j in doc.get("jobs") or []:
            if isinstance(j, dict) and j.get("state") in ("queued", "running") and j.get("die"):
                out.append({"die": str(j["die"]), "where": f"job {j.get('tag') or ''}".strip()})
    except Exception:                                       # noqa: BLE001
        pass
    return out


def refusals(die: str, *, projects: Optional[List[dict]] = None, productions: Optional[List[dict]] = None,
             jobs: Optional[List[Dict[str, str]]] = None, revisions: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Why ``die`` cannot be archived now: ``[{"code", "names"}]``, empty when it can.  The sources are read when not
    given (the route); the tests pass them."""
    die = str(die)
    if projects is None:
        from motor_ai_sim.development import store as _ps
        projects = _ps.list_projects()
    if productions is None:
        from motor_ai_sim.production import routes as _PR
        productions = _PR._production_list_for("", True)
    jobs = _live_jobs() if jobs is None else jobs
    if revisions is None:
        revisions = _revisions(die)
    out: List[Dict[str, Any]] = []
    running = sorted({j["where"] for j in jobs if j.get("die") == die})
    if running:
        out.append({"code": "running_job", "names": running})
    open_prod = sorted(f"{r.get('die')}/{r.get('config')}" for r in productions
                       if str(r.get("die") or "") == die and str(r.get("status") or "active") != "archived")
    if open_prod:
        out.append({"code": "open_production", "names": open_prod})
    linked = sorted({str(p.get("title") or p.get("id") or "") for p in projects
                     if str(p.get("status") or "") != "closed"
                     and any(isinstance(m, dict) and str(m.get("die") or "") == die
                             and str(m.get("status") or "current") == "current" for m in p.get("linked_motors") or [])})
    if linked:
        out.append({"code": "project_link", "names": linked})
    if revisions:
        out.append({"code": "product_revision", "names": sorted(revisions)})
    return out


def _revisions(die: str) -> List[str]:
    try:
        from motor_ai_sim import motor_revisions as _mr
        if not _mr._has_store():
            return []
        return [f"{r.get('config')} ({_mr.label(r.get('rev'))})" for _l, r in _mr.revisions_of_die(die)]
    except Exception:                                       # noqa: BLE001
        return []


# ── a die rename: the keys follow (die_rename.py) ─────────────────────────────────────────────────────────────────────

def rename_die_in(doc: Any, old: str, new: str, scope: Optional[str] = None) -> int:
    """Rewrite every key of ``doc`` (archived dies and history) naming ``old`` to ``new``: in every scope, or only in
    ``scope`` (a workspace die renamed in its own workspace must not move another workspace's key of the same name)."""
    if not isinstance(doc, dict):
        return 0
    n = 0

    def nk(k: str) -> Optional[str]:
        sc, sep, die = str(k).partition("/")
        return f"{sc}/{new}" if sep and die == old and (scope is None or sc == scope) else None

    dies = doc.get("dies") if isinstance(doc.get("dies"), dict) else {}
    for k in list(dies):
        new_k = nk(k)
        if new_k:
            if new_k in dies:
                raise ValueError(f"die archive already has {new_k!r}")
            dies[new_k] = dies.pop(k)
            n += 1
    for row in doc.get("history") or []:
        if isinstance(row, dict) and nk(row.get("key", "")):
            row["key"] = nk(row["key"])
            n += 1
    return n
