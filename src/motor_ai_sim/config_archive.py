"""Archive ONE configuration (motor) of a die, reversibly (owner 2026-11-22: "one motor per project; the one a project or
production links is current, the rest are old and archived; archive, never delete").

The same idea as ``die_archive`` one level down:

* nothing on disk is moved, renamed or deleted: the configuration file, its duties, results and stored points stay where they
  are.  The state lives in ONE sidecar file of the object registry (``<shared root>/object_registry/config_archive.json``,
  override ``MOTRES_OBJECT_REGISTRY_ROOT``), keyed ``<die>/<configuration>``;
* the archive is for EVERYBODY (a catalog decision of the owner, like Retire of a shared die): ``motor_access`` asks
  :func:`is_archived` in its one configuration rule.  A non-admin then gets exactly what a missing configuration gets (no
  listing, 404 on a direct link, no card); an admin keeps direct links, and the LISTINGS leave it out for everybody
  (``motor_access.may_see_config_file``) - the die view shows it in its "Archived" group, from :func:`listing`;
* SHARED-catalog configurations only (the owner's catalog; a workspace or published copy is not archived here);
* admin only, refused while the configuration is in use (:func:`refusals`): linked by a project that is not closed, a
  Production record of any state, a running or queued job of it (or of its die with no configuration named).  The refusal and
  the archive run under :func:`lifecycle_lock`, which a project link and a production creation also take and re-check under;
  a source that cannot be read refuses the archive.  The last live configuration of a die may be archived: the die stays;
* FAIL CLOSED: a damaged store makes every configuration count as archived for readers and refuses every change, so its
  history is never overwritten;
* every change writes a history row (who, when, reason) in the same file, in the same write, and the admin audit.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from motor_ai_sim import json_store

REASON_MAX = 200
HISTORY_MAX = 2000
#: the refusal codes in words (the API detail; the web translates the codes itself)
REFUSAL_TEXT = {"project_link": "a project links it (unlink it there first)",
                "production": "it has a Production record",
                "running_job": "a job is running or queued for it"}

_CACHE: Dict[str, Any] = {"sig": None, "keys": frozenset()}


def store_path() -> Path:
    from motor_ai_sim.display_state import registry_root
    return registry_root() / "config_archive.json"


def _empty() -> Dict[str, Any]:
    return {"version": 1, "configs": {}, "history": []}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def key(die: str, cfg: str) -> str:
    if not die or not cfg:
        raise ValueError("die and configuration required")
    return f"{die}/{cfg}"


class ArchiveUnavailable(Exception):
    """The archive store exists but cannot be read as an archive (damaged, wrong shape, unreadable).  FAIL CLOSED: every
    configuration then counts as archived for readers, and no change is written until it is repaired."""


def _validate(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict) or not isinstance(data.get("configs", {}), dict) or not isinstance(data.get("history", []), list):
        raise ArchiveUnavailable("config_archive.json has the wrong shape")
    configs = data.get("configs") or {}
    if any(not isinstance(k, str) or "/" not in k or not isinstance(v, dict) for k, v in configs.items()):
        raise ArchiveUnavailable("config_archive.json has a malformed entry")
    data.setdefault("version", 1)
    data.setdefault("configs", {})
    data.setdefault("history", [])
    return data


def load() -> Dict[str, Any]:
    """The archive; an ABSENT store is an empty archive, a damaged one raises :class:`ArchiveUnavailable`."""
    path = store_path()
    if not path.exists():
        return _empty()
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else None
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ArchiveUnavailable(f"config_archive.json unreadable: {exc}") from exc
    if data is None:
        raise ArchiveUnavailable("config_archive.json is empty")
    return _validate(data)


def lifecycle_lock():
    """THE lock of a configuration's archive state.  Archive / restore and every admission that could make a configuration
    in use (a project link, a production record) take it and re-check under it, so a refusal and the use it refers to are
    never interleaved.  The store's own lock (reentrant), so a store mutation inside it does not deadlock."""
    return json_store.lock_for(store_path())


def key(die: str, cfg: str) -> str:
    if not die or not cfg:
        raise ValueError("die and configuration required")
    return f"{die}/{cfg}"


def archived_keys() -> frozenset:
    """Every archived ``<die>/<cfg>``; read once per change of the file (every listing asks this per configuration).
    Raises :class:`ArchiveUnavailable` for a damaged store (remembered until the file changes)."""
    path = store_path()
    try:
        st = path.stat()
        sig = (str(path), st.st_mtime_ns, st.st_size)
    except FileNotFoundError:
        return frozenset()
    except OSError as exc:
        raise ArchiveUnavailable(f"config_archive.json not reachable: {exc}") from exc
    if _CACHE["sig"] != sig:
        try:
            keys: Any = frozenset((load().get("configs") or {}).keys())
        except ArchiveUnavailable as exc:
            keys = exc
        _CACHE.update(sig=sig, keys=keys)
    if isinstance(_CACHE["keys"], ArchiveUnavailable):
        raise _CACHE["keys"]
    return _CACHE["keys"]


def is_archived(die: str, cfg: Optional[str]) -> bool:
    """FAIL CLOSED: True for every configuration while the store is damaged (logged once per state of the file)."""
    if not die or not cfg:
        return False
    try:
        keys = archived_keys()
    except ArchiveUnavailable as exc:
        if _CACHE.get("logged") != _CACHE.get("sig"):
            _CACHE["logged"] = _CACHE.get("sig")
            import logging
            logging.getLogger(__name__).error("config archive unavailable (%s): every configuration is treated as archived", exc)
        return True
    return bool(keys) and f"{die}/{cfg}" in keys


def listing(die: Optional[str] = None) -> List[Dict[str, Any]]:
    """The archived configurations (of one die, or all), by name."""
    out = []
    for k, rec in sorted((load().get("configs") or {}).items()):
        d, _sep, cfg = k.partition("/")
        if isinstance(rec, dict) and (die is None or d == die):
            out.append({"die": d, "config": cfg, **{f: rec.get(f) for f in ("by", "at", "reason")}})
    return out


def history(die: str, cfg: str) -> List[Dict[str, Any]]:
    k = key(die, cfg)
    return [h for h in load().get("history") or [] if isinstance(h, dict) and h.get("key") == k]


def _mutate(fn) -> Dict[str, Any]:
    """Read-latest, validate, change, atomic write - all under the lifecycle lock.  A damaged store is never overwritten
    (:class:`ArchiveUnavailable`): the history it holds must not be replaced by an empty document."""
    out: Dict[str, Any] = {}
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with lifecycle_lock():
        data = load()
        fn(data, out)
        if len(data["history"]) > HISTORY_MAX:
            del data["history"][: len(data["history"]) - HISTORY_MAX]
        json_store.atomic_write_json(path, data)
    return out


def archive(die: str, cfg: str, *, by: str, reason: str) -> Dict[str, Any]:
    """Archive ``<die>/<cfg>``; returns the record.  The record and its history row are one write."""
    k = key(die, cfg)
    rec = {"by": str(by or ""), "at": _now(), "reason": " ".join(str(reason or "").split())[:REASON_MAX]}
    if not rec["reason"]:
        raise ValueError("a reason is required")

    def fn(data, out):
        data["configs"][k] = rec
        data["history"].append({"key": k, "to": "archived", **rec})
        out["rec"] = rec
    return _mutate(fn)["rec"]


def restore(die: str, cfg: str, *, by: str) -> bool:
    """Undo :func:`archive`; True iff it was archived."""
    k = key(die, cfg)

    def fn(data, out):
        if data["configs"].pop(k, None) is not None:
            data["history"].append({"key": k, "to": "active", "by": str(by or ""), "at": _now(), "reason": ""})
            out["hit"] = True
    return bool(_mutate(fn).get("hit"))


# ── what keeps a configuration in use ─────────────────────────────────────────────────────────────────────────────────

def _live_jobs_strict(die: str) -> List[Dict[str, str]]:
    """``[{"die", "config", "where"}]`` of every queued or running job naming ``die``: the application's queue and, when this
    process can see it, the agent job launcher's status file.  NOT fail-open: a source that exists but cannot be read raises
    (the archive is then refused - an unknown source is no proof that nothing uses the configuration)."""
    import os
    out: List[Dict[str, str]] = []
    from motor_ai_sim import jobs as _J
    q = _J.queue()
    recs = q.live_records() if hasattr(q, "live_records") else q.list_for_owner("", limit=500, is_admin=True)
    for r in recs:
        t = r.target or {}
        if r.state in (_J.JobState.QUEUED, _J.JobState.RUNNING) and str(t.get("die") or "") == die:
            out.append({"die": die, "config": str(t.get("config") or t.get("cfg") or ""), "where": f"{r.kind} {r.run_id}"})
    from motor_ai_sim import die_archive as _DA
    status = Path(os.environ.get("MOTRES_JOBS_STATUS") or _DA.JOBS_STATUS)
    if status.exists():
        doc = json.loads(status.read_text(encoding="utf-8"))
        for j in doc.get("jobs") or []:
            if isinstance(j, dict) and j.get("state") in ("queued", "running") and str(j.get("die") or "") == die:
                out.append({"die": die, "config": str(j.get("configuration") or j.get("config") or ""),
                            "where": f"job {j.get('tag') or ''}".strip()})
    return out


def refusals(die: str, cfg: str, *, projects: Optional[List[dict]] = None, productions: Optional[List[dict]] = None,
             jobs: Optional[List[Dict[str, str]]] = None) -> List[Dict[str, Any]]:
    """Why ``<die>/<cfg>`` cannot be archived now: ``[{"code", "names"}]``, empty when it can.  Sources that cannot be read
    raise (the route answers 503).  A Production record of ANY state refuses: its readers follow the production, not the
    catalog, so an archived configuration never has one."""
    die, cfg = str(die), str(cfg)
    if projects is None:
        from motor_ai_sim.development import store as _ps
        projects = _ps.list_projects()
    if productions is None:
        from motor_ai_sim.production import routes as _PR
        productions = _PR._production_list_for("", True)
    if jobs is None:
        jobs = _live_jobs_strict(die)
    out: List[Dict[str, Any]] = []
    linked = sorted({str(p.get("title") or p.get("id") or "") for p in projects
                     if str(p.get("status") or "") != "closed"
                     and any(isinstance(m, dict) and str(m.get("die") or "") == die and str(m.get("config") or "") == cfg
                             for m in p.get("linked_motors") or [])})
    if linked:
        out.append({"code": "project_link", "names": linked})
    prod = sorted(f"{r.get('die')}/{r.get('config')}" for r in productions
                  if str(r.get("die") or "") == die and str(r.get("config") or "") == cfg)
    if prod:
        out.append({"code": "production", "names": prod})
    # a job of another configuration of the die does not hold this one; a job that names no configuration holds the die
    running = sorted({j["where"] for j in jobs if j.get("die") == die and (not j.get("config") or j.get("config") == cfg)})
    if running:
        out.append({"code": "running_job", "names": running})
    return out


def refusal_text(die: str, cfg: str, refusals_: List[Dict[str, Any]]) -> str:
    return f"'{die}/{cfg}' cannot be archived: " + "; ".join(
        f"{REFUSAL_TEXT.get(r['code'], r['code'])} ({', '.join(r['names'])})" for r in refusals_)


# ── renames: the keys follow (die_rename.py, the configuration rename and move) ───────────────────────────────────────

def rename_in(doc: Any, old_die: str, new_die: str, old_cfg: Optional[str] = None, new_cfg: Optional[str] = None) -> int:
    """Rewrite every key of ``doc`` naming ``old_die`` (and, when given, ``old_cfg``) to the new names."""
    if not isinstance(doc, dict):
        return 0
    n = 0

    def nk(k: str) -> Optional[str]:
        d, sep, c = str(k).partition("/")
        if not sep or d != old_die or (old_cfg is not None and c != old_cfg):
            return None
        return f"{new_die}/{new_cfg if old_cfg is not None and new_cfg is not None else c}"

    configs = doc.get("configs") if isinstance(doc.get("configs"), dict) else {}
    for k in list(configs):
        new_k = nk(k)
        if new_k and new_k != k:
            if new_k in configs:
                raise ValueError(f"configuration archive already has {new_k!r}")
            configs[new_k] = configs.pop(k)
            n += 1
    for row in doc.get("history") or []:
        if isinstance(row, dict) and nk(row.get("key", "")) and nk(row["key"]) != row["key"]:
            row["key"] = nk(row["key"])
            n += 1
    return n


def rename(old_die: str, new_die: str, old_cfg: Optional[str] = None, new_cfg: Optional[str] = None) -> int:
    """:func:`rename_in` on the store, under the lifecycle lock (read-latest, so a concurrent archive is never lost)."""
    if not store_path().is_file():
        return 0
    return int(_mutate(lambda data, out: out.update(n=rename_in(data, old_die, new_die, old_cfg, new_cfg))).get("n") or 0)


def rename_step(ctx: Any, old_die: str, new_die: str, old_cfg: Optional[str] = None, new_cfg: Optional[str] = None,
                blocked: type = ValueError) -> None:
    """The rename / move hook of ``die_rename`` and ``config_move``: a dry run lists what would move; a real run moves the keys
    under the lifecycle lock and registers an undo that moves them back the same way (never a byte restore that could erase a
    later write).  A damaged store refuses the rename (``blocked``)."""
    try:
        doc = load()
    except ArchiveUnavailable as exc:
        raise blocked(str(exc)) from exc
    probe = json.loads(json.dumps(doc))
    try:
        n = rename_in(probe, old_die, new_die, old_cfg, new_cfg)
    except ValueError as exc:
        raise blocked(str(exc)) from exc
    if not n:
        return
    ctx.add("config_archive", store_path(), f"an archived configuration stays archived under its new name ({n})")
    if getattr(ctx, "dry_run", False):
        return
    rename(old_die, new_die, old_cfg, new_cfg)
    back_cfg = (new_cfg, old_cfg) if old_cfg is not None else (None, None)
    ctx.undo.append(lambda: rename(new_die, old_die, back_cfg[0], back_cfg[1]))
