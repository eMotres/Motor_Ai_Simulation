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

import contextlib
import json
import os
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
    """The WHOLE schema of an existing store: version 1, a ``configs`` object of ``<die>/<cfg>`` -> {by, at, reason}, a
    ``history`` list of {key, to, by, at} rows.  Anything missing or malformed is damage (only an ABSENT file is an empty
    archive)."""
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("configs"), dict) \
            or not isinstance(data.get("history"), list):
        raise ArchiveUnavailable("config_archive.json has the wrong shape")
    for k, v in data["configs"].items():
        if not isinstance(k, str) or k.count("/") < 1 or not k.split("/", 1)[0] or not k.split("/", 1)[1] \
                or not isinstance(v, dict) or not all(isinstance(v.get(f), str) for f in ("by", "at", "reason")):
            raise ArchiveUnavailable("config_archive.json has a malformed entry")
    for h in data["history"]:
        if not isinstance(h, dict) or not isinstance(h.get("key"), str) or h.get("to") not in ("archived", "active"):
            raise ArchiveUnavailable("config_archive.json has a malformed history row")
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


ARCHIVED, ACTIVE, UNKNOWN = "archived", "active", "unknown"


def _case_insensitive_fs() -> bool:
    """Does the catalog's file system ignore letter case (Windows, macOS)?  Then ``C.yaml`` and ``c.yaml`` are ONE file and the
    archive key is compared without case; on a case-sensitive file system they are distinct configurations."""
    hit = _CACHE.get("ci")
    if hit is None:
        probe = store_path().parent
        try:
            probe.mkdir(parents=True, exist_ok=True)
            hit = (probe / "X").exists() == (probe / "x").exists() and os.path.normcase("A") == "a" or \
                Path(str(probe).swapcase()).exists() and str(probe).swapcase() != str(probe)
        except OSError:
            hit = False
        _CACHE["ci"] = bool(hit)
    return bool(_CACHE["ci"])


def same_name(a: Any, b: Any) -> bool:
    """Do two stored names point at the SAME catalog file?  Exactly equal, or - on a file system that ignores letter case -
    equal without case (a project link stored as ``D/c`` refers to ``C.yaml`` there; round 3 of #820).  On a case-sensitive
    file system ``C`` and ``c`` stay distinct configurations."""
    a, b = str(a or ""), str(b or "")
    return a == b or (_case_insensitive_fs() and a.casefold() == b.casefold())


def state(die: str, cfg: Optional[str]) -> str:
    """``archived`` | ``active`` | ``unknown`` (the store is damaged: the state of this configuration cannot be known)."""
    if not die or not cfg:
        return ACTIVE
    try:
        keys = archived_keys()
    except ArchiveUnavailable as exc:
        if _CACHE.get("logged") != _CACHE.get("sig"):
            _CACHE["logged"] = _CACHE.get("sig")
            import logging
            logging.getLogger(__name__).error("config archive unavailable (%s): shared configurations are hidden from "
                                              "non-admins until it is restored", exc)
        return UNKNOWN
    if not keys:
        return ACTIVE
    k = f"{die}/{cfg}"
    if k in keys:
        return ARCHIVED
    if _case_insensitive_fs() and k.casefold() in {x.casefold() for x in keys}:
        return ARCHIVED
    return ACTIVE


def is_archived(die: str, cfg: Optional[str]) -> bool:
    """True when archived OR unknown (a damaged store fails closed for the shared catalog; callers that must keep an admin's
    recovery view or another layer ask :func:`state` / pass the layer to ``motor_access``)."""
    return state(die, cfg) != ACTIVE


def backup_path() -> Path:
    return store_path().with_name("config_archive.recovery.json")


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
        data["generation"] = int(data.get("generation") or 0) + 1
        json_store.atomic_write_json(path, data)
        json_store.atomic_write_json(backup_path(), data)   # the recovery copy an admin can restore (restore_from_recovery)
    return out


def restore_from_recovery(*, by: str) -> Dict[str, Any]:
    """Admin recovery of a damaged store: the last recovery copy (validated) becomes the store again; the damaged file is kept
    beside it as ``config_archive.damaged-<time>.json``.  Raises :class:`ArchiveUnavailable` when there is no valid copy."""
    path = store_path()
    with lifecycle_lock():
        try:
            data = _validate(json.loads(backup_path().read_text(encoding="utf-8")))
        except (OSError, ValueError, ArchiveUnavailable) as exc:
            raise ArchiveUnavailable(f"no valid recovery copy: {exc}") from exc
        if path.exists():
            path.replace(path.with_name("config_archive.damaged-%s.json" % _now().replace(":", "")))
        data["history"].append({"key": "*", "to": "active", "by": str(by or ""), "at": _now(), "reason": "restored from the recovery copy"})
        json_store.atomic_write_json(path, data)
    return {"configs": len(data["configs"]), "generation": data.get("generation")}


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
        if r.state in (_J.JobState.QUEUED, _J.JobState.RUNNING) and same_name(t.get("die"), die):
            out.append({"die": die, "config": str(t.get("config") or t.get("cfg") or ""), "where": f"{r.kind} {r.run_id}"})
    from motor_ai_sim import die_archive as _DA
    status = Path(os.environ.get("MOTRES_JOBS_STATUS") or _DA.JOBS_STATUS)
    if status.exists():
        doc = json.loads(status.read_text(encoding="utf-8"))
        for j in doc.get("jobs") or []:
            if isinstance(j, dict) and j.get("state") in ("queued", "running") and same_name(j.get("die"), die):
                out.append({"die": die, "config": str(j.get("configuration") or j.get("config") or ""),
                            "where": f"job {j.get('tag') or ''}".strip()})
    return out


def _projects_strict() -> List[dict]:
    """Every project record, read STRICTLY: a project.json that cannot be parsed raises (its links are unknown, so the archive is
    refused) - unlike the tolerant listing readers, which skip it."""
    from motor_ai_sim.development import store as _ps
    base = _ps.root()
    out: List[dict] = []
    if not base.is_dir():
        return out
    for d in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")):
        f = d / "project.json"
        if not f.exists():
            continue
        doc = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            raise ValueError(f"{f} is not a project record")
        out.append(doc)
    return out


def _productions_strict(die: str, cfg: str) -> List[dict]:
    """A Production record of ``<die>/<cfg>``: its FILE existing is enough (any state, any content - damaged too)."""
    from motor_ai_sim.production import store as _prs
    f = _prs.path_for(die, cfg) / "production.json"
    return [{"die": die, "config": cfg}] if f.exists() else []


class Archived(Exception):
    """The configuration is archived (or its state is unknown): it is not linked, and no production is made of it."""


from contextlib import contextmanager  # noqa: E402


@contextmanager
def admission(die: str, cfg: str, layer: Optional[str] = None):
    """THE way to make a configuration in use (a project link - manual or automatic - or a Production record): under the
    lifecycle lock, re-checking its state; raises :class:`Archived` when it is archived or unknown.  The caller writes its link
    / record inside the ``with`` block, so an archive can never interleave.  The archive is of the SHARED catalog: the layer
    is resolved FIRST (``layer``, else what the caller's catalog resolves the die to), and a workspace or published
    configuration is admitted whatever the archive holds - also while it is damaged (round 3 of #820)."""
    from motor_ai_sim import motor_access as _MA
    with lifecycle_lock():
        if _MA._die_layer_of(str(die), layer) == "shared" and state(str(die), str(cfg)) != ACTIVE:
            raise Archived(f"'{die}/{cfg}' is archived; restore it in the catalog first")
        yield


def refusals(die: str, cfg: str, *, projects: Optional[List[dict]] = None, productions: Optional[List[dict]] = None,
             jobs: Optional[List[Dict[str, str]]] = None) -> List[Dict[str, Any]]:
    """Why ``<die>/<cfg>`` cannot be archived now: ``[{"code", "names"}]``, empty when it can.  Sources that cannot be read
    raise (the route answers 503).  A Production record of ANY state refuses: its readers follow the production, not the
    catalog, so an archived configuration never has one."""
    die, cfg = str(die), str(cfg)
    if projects is None:
        projects = _projects_strict()
    if productions is None:
        productions = _productions_strict(die, cfg)
    if jobs is None:
        jobs = _live_jobs_strict(die)
    out: List[Dict[str, Any]] = []
    linked = sorted({str(p.get("title") or p.get("id") or "") for p in projects
                     if str(p.get("status") or "") != "closed"
                     and any(isinstance(m, dict) and same_name(m.get("die"), die) and same_name(m.get("config"), cfg)
                             for m in p.get("linked_motors") or [])})
    if linked:
        out.append({"code": "project_link", "names": linked})
    prod = sorted(f"{r.get('die')}/{r.get('config')}" for r in productions
                  if same_name(r.get("die"), die) and same_name(r.get("config"), cfg))
    if prod:
        out.append({"code": "production", "names": prod})
    # a job of another configuration of the die does not hold this one; a job that names no configuration holds the die
    running = sorted({j["where"] for j in jobs if same_name(j.get("die"), die) and (not j.get("config") or same_name(j.get("config"), cfg))})
    if running:
        out.append({"code": "running_job", "names": running})
    return out


def refusal_text(die: str, cfg: str, refusals_: List[Dict[str, Any]]) -> str:
    return f"'{die}/{cfg}' cannot be archived: " + "; ".join(
        f"{REFUSAL_TEXT.get(r['code'], r['code'])} ({', '.join(r['names'])})" for r in refusals_)


# ── renames: the keys follow (die_rename.py, the configuration rename and move) ───────────────────────────────────────

def _renamer(old_die: str, new_die: str, old_cfg: Optional[str], new_cfg: Optional[str]):
    def nk(k: str) -> Optional[str]:
        d, sep, c = str(k).partition("/")
        if not sep or d != old_die or (old_cfg is not None and c != old_cfg):
            return None
        return f"{new_die}/{new_cfg if old_cfg is not None and new_cfg is not None else c}"
    return nk


def publish_in(doc: Any, old_die: str, new_die: str, old_cfg: Optional[str] = None, new_cfg: Optional[str] = None) -> List[str]:
    """Archive the NEW names too (a copy of each entry), before a rename / move publishes them: while the files move, both
    names read as archived.  Returns the keys added."""
    if not isinstance(doc, dict):
        return []
    nk = _renamer(old_die, new_die, old_cfg, new_cfg)
    configs = doc.get("configs") if isinstance(doc.get("configs"), dict) else {}
    added = []
    for k in list(configs):
        new_k = nk(k)
        if new_k and new_k != k:
            if new_k in configs:
                raise ValueError(f"configuration archive already has {new_k!r}")
            configs[new_k] = json.loads(json.dumps(configs[k]))
            added.append(new_k)
    return added


def rename_in(doc: Any, old_die: str, new_die: str, old_cfg: Optional[str] = None, new_cfg: Optional[str] = None,
              *, published: bool = False) -> int:
    """Rewrite every key of ``doc`` naming ``old_die`` (and, when given, ``old_cfg``) to the new names.  ``published``: the new
    names were added by :func:`publish_in`, so an equal entry under the new name is expected and the old one is dropped."""
    if not isinstance(doc, dict):
        return 0
    n = 0
    nk = _renamer(old_die, new_die, old_cfg, new_cfg)
    configs = doc.get("configs") if isinstance(doc.get("configs"), dict) else {}
    for k in list(configs):
        new_k = nk(k)
        if new_k and new_k != k:
            if new_k in configs:
                if not (published and configs[new_k] == configs[k]):
                    raise ValueError(f"configuration archive already has {new_k!r}")
                configs.pop(k)
            else:
                configs[new_k] = configs.pop(k)
            n += 1
    for row in doc.get("history") or []:
        if isinstance(row, dict) and nk(row.get("key", "")) and nk(row["key"]) != row["key"]:
            row["key"] = nk(row["key"])
            n += 1
    return n


def rename(old_die: str, new_die: str, old_cfg: Optional[str] = None, new_cfg: Optional[str] = None, *,
           published: bool = False) -> int:
    """:func:`rename_in` on the store, under the lifecycle lock (read-latest, so a concurrent archive is never lost)."""
    if not store_path().is_file():
        return 0
    return int(_mutate(lambda data, out: out.update(n=rename_in(data, old_die, new_die, old_cfg, new_cfg, published=published))).get("n") or 0)


def transaction_lock(glob_scope: bool):
    """The lock a die rename / configuration move or rename holds from its identity checks to the end of its rollback: an
    archive, a restore or an admission can never run between its steps (round 3 of #820).  Taken FIRST, before any store lock
    of the steps (the same order as every admission).  A workspace-scope rename does not touch the shared archive."""
    return lifecycle_lock() if glob_scope else contextlib.nullcontext()


def publish_step(ctx: Any, old_die: str, new_die: str, old_cfg: Optional[str] = None, new_cfg: Optional[str] = None,
                 blocked: type = ValueError) -> None:
    """The FIRST step of a rename / move: an archived configuration is archived under its new name too before any file of the
    new name exists, so a reader never sees the new name active.  Undo removes the added names."""
    with lifecycle_lock():
        try:
            doc = load()
        except ArchiveUnavailable as exc:
            raise blocked(str(exc)) from exc
        try:
            added = publish_in(json.loads(json.dumps(doc)), old_die, new_die, old_cfg, new_cfg)
        except ValueError as exc:
            raise blocked(str(exc)) from exc
        if not added:
            return
        ctx.add("config_archive", store_path(), f"an archived configuration stays archived under its new name ({len(added)})")
        ctx.archive_published = True
        if getattr(ctx, "dry_run", False):
            return
        _mutate(lambda data, out: publish_in(data, old_die, new_die, old_cfg, new_cfg))

    def undo() -> None:
        _mutate(lambda data, out: [data["configs"].pop(k, None) for k in added])
    ctx.undo.append(undo)


def rename_step(ctx: Any, old_die: str, new_die: str, old_cfg: Optional[str] = None, new_cfg: Optional[str] = None,
                blocked: type = ValueError) -> None:
    """The LAST step of a rename / move (after :func:`publish_step`): the old names are dropped and the history follows.  A dry
    run lists nothing more (the publish step did).  Undo moves the keys back (never a byte restore that could erase a later
    write); the publish step's undo then has nothing left to remove."""
    with lifecycle_lock():
        try:
            doc = load()
        except ArchiveUnavailable as exc:
            raise blocked(str(exc)) from exc
        try:
            n = rename_in(json.loads(json.dumps(doc)), old_die, new_die, old_cfg, new_cfg, published=True)
        except ValueError as exc:
            raise blocked(str(exc)) from exc
        if not n:
            return
        if not getattr(ctx, "archive_published", False):        # called alone (no publish step): list it here
            ctx.add("config_archive", store_path(), f"an archived configuration stays archived under its new name ({n})")
        if getattr(ctx, "dry_run", False):
            return
        rename(old_die, new_die, old_cfg, new_cfg, published=True)
    back_cfg = (new_cfg, old_cfg) if old_cfg is not None else (None, None)
    ctx.undo.append(lambda: rename(new_die, old_die, back_cfg[0], back_cfg[1]))
