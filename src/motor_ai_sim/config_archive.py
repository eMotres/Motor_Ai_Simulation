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
* admin only, refused while the configuration is in use (:func:`refusals`): linked by a project that is not closed, a
  Production record that is not archived, a running or queued job.  The last live configuration of a die may be archived:
  the die stays;
* every change writes a history row (who, when, reason) in the same file, in the same write, and the admin audit.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from motor_ai_sim import json_store

REASON_MAX = 200
HISTORY_MAX = 2000
#: the refusal codes in words (the API detail; the web translates the codes itself)
REFUSAL_TEXT = {"project_link": "a project links it (unlink it there first)",
                "production": "it has a Production record (archive the production first)",
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


def load() -> Dict[str, Any]:
    data = json_store.read_json(store_path(), default=None)
    if not isinstance(data, dict):
        return _empty()
    if not isinstance(data.get("configs"), dict):
        data["configs"] = {}
    if not isinstance(data.get("history"), list):
        data["history"] = []
    return data


def archived_keys() -> frozenset:
    """Every archived ``<die>/<cfg>``; read once per change of the file (every listing asks this per configuration).  An
    unreadable store archives nothing (a listing then shows what it showed before)."""
    path = store_path()
    try:
        st = path.stat()
        sig = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return frozenset()
    if _CACHE["sig"] != sig:
        try:
            keys = frozenset((load().get("configs") or {}).keys())
        except Exception:                                   # noqa: BLE001
            keys = frozenset()
        _CACHE.update(sig=sig, keys=keys)
    return _CACHE["keys"]


def is_archived(die: str, cfg: Optional[str]) -> bool:
    if not die or not cfg:
        return False
    keys = archived_keys()
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
    out: Dict[str, Any] = {}

    def mutate(data: Any) -> Any:
        if not isinstance(data, dict):
            data = _empty()
        data.setdefault("configs", {})
        data.setdefault("history", [])
        fn(data, out)
        if len(data["history"]) > HISTORY_MAX:
            del data["history"][: len(data["history"]) - HISTORY_MAX]
        return data

    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    json_store.mutate_json(path, mutate, default=_empty())
    return out


def archive(die: str, cfg: str, *, by: str, reason: str = "") -> Dict[str, Any]:
    """Archive ``<die>/<cfg>``; returns the record.  The record and its history row are one write."""
    k = key(die, cfg)
    rec = {"by": str(by or ""), "at": _now(), "reason": " ".join(str(reason or "").split())[:REASON_MAX]}

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

def refusals(die: str, cfg: str, *, projects: Optional[List[dict]] = None, productions: Optional[List[dict]] = None,
             jobs: Optional[List[Dict[str, str]]] = None) -> List[Dict[str, Any]]:
    """Why ``<die>/<cfg>`` cannot be archived now: ``[{"code", "names"}]``, empty when it can.  The sources are read when not
    given (the route); the tests pass them."""
    die, cfg = str(die), str(cfg)
    if projects is None:
        from motor_ai_sim.development import store as _ps
        projects = _ps.list_projects()
    if productions is None:
        from motor_ai_sim.production import routes as _PR
        productions = _PR._production_list_for("", True)
    if jobs is None:
        from motor_ai_sim import die_archive as _DA
        jobs = _DA._live_jobs()
    out: List[Dict[str, Any]] = []
    linked = sorted({str(p.get("title") or p.get("id") or "") for p in projects
                     if str(p.get("status") or "") != "closed"
                     and any(isinstance(m, dict) and str(m.get("die") or "") == die and str(m.get("config") or "") == cfg
                             for m in p.get("linked_motors") or [])})
    if linked:
        out.append({"code": "project_link", "names": linked})
    prod = sorted(f"{r.get('die')}/{r.get('config')}" for r in productions
                  if str(r.get("die") or "") == die and str(r.get("config") or "") == cfg
                  and str(r.get("status") or "active") != "archived")
    if prod:
        out.append({"code": "production", "names": prod})
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
    """:func:`rename_in` on the store, under its lock."""
    if not store_path().is_file():
        return 0
    return int(_mutate(lambda data, out: out.update(n=rename_in(data, old_die, new_die, old_cfg, new_cfg))).get("n") or 0)
