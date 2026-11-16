"""Active / Parked / Archived: how OUR side shows a project or a production in its lists (owner 2026-11-16).

Owner: «интерфейс для архивации проекта или неактуальности на данный момент проекта; лучше убирать его видимость, чтобы в
любой момент можно было возобновить», corrected the same day: a parked object never wakes by itself - it stays parked until
an admin or engineer sets it back to Active.

A PRESENTATION state for staff (admins and engineers) only.  It is not a security boundary and it changes nothing else:
nothing is deleted, moved or renamed, no access changes, the customer and the factory see their project / production exactly
as before, and the existing records stay byte-identical - the state lives in ONE sidecar file beside the stores
(``<shared root>/object_registry/display_state.json``, override ``MOTRES_OBJECT_REGISTRY_ROOT``), never inside a
``project.json`` or ``production.json``.

Why not the existing status fields: a project's ``status`` is its lifecycle stage and the customer sees it (``closed`` is
the customer's own "close the project"); a production's ``status`` reaches the factory (``archived`` productions drop out
of the revision acknowledgements, ``revision_ack``).  Writing either would change what the customer or the factory sees.
They are READ instead, as the default when no explicit state was set:

=================================  ==========
existing value                     shown as
=================================  ==========
project ``status: closed``          archived
project, any other status           active
production ``status: archived``     archived
production ``status: paused``       parked
production ``status: active``       active
=================================  ==========

An explicit state set here always wins over the default; setting the state back to the default value keeps the explicit
record (who / when stays visible), and ``clear`` removes it.  Every change is appended to ``history`` in the same file.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from motor_ai_sim import json_store

STATES = ("active", "parked", "archived")
KINDS = ("project", "production")
REASON_MAX = 200
HISTORY_MAX = 2000                      # oldest history rows are dropped beyond this; the current states never are

_PROJECT_DEFAULT = {"closed": "archived"}
_PRODUCTION_DEFAULT = {"archived": "archived", "paused": "parked"}


def registry_root() -> Path:
    env = (os.environ.get("MOTRES_OBJECT_REGISTRY_ROOT") or "").strip()
    if env:
        return Path(env).expanduser()
    from motor_ai_sim.workspace import shared_root
    return Path(str(shared_root())) / "object_registry"


def store_path() -> Path:
    return registry_root() / "display_state.json"


def object_key(kind: str, *, project_id: str = "", die: str = "", config: str = "") -> str:
    """``project:<id>`` or ``production:<die>/<config>``: the die and configuration names as stored (no slug)."""
    if kind == "project":
        if not project_id:
            raise ValueError("project id required")
        return f"project:{project_id}"
    if kind == "production":
        if not die or not config:
            raise ValueError("die and config required")
        return f"production:{die}/{config}"
    raise ValueError(f"unknown kind {kind!r}")


def default_state(kind: str, legacy_status: Any) -> str:
    """The state shown when nobody set one: read off the existing status (table in the module doc)."""
    table = _PROJECT_DEFAULT if kind == "project" else _PRODUCTION_DEFAULT
    return table.get(str(legacy_status or ""), "active")


def _empty() -> Dict[str, Any]:
    return {"version": 1, "states": {}, "history": []}


def load() -> Dict[str, Any]:
    data = json_store.read_json(store_path(), default=None)
    if not isinstance(data, dict):
        return _empty()
    data.setdefault("version", 1)
    if not isinstance(data.get("states"), dict):
        data["states"] = {}
    if not isinstance(data.get("history"), list):
        data["history"] = []
    return data


def effective(kind: str, key: str, legacy_status: Any, states: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """``{state, explicit, default, reason, by, at}`` for one object; ``states`` = ``load()["states"]`` for a batch."""
    states = load()["states"] if states is None else states
    default = default_state(kind, legacy_status)
    rec = states.get(key)
    if isinstance(rec, dict) and rec.get("state") in STATES:
        return {"state": rec["state"], "explicit": True, "default": default, "reason": str(rec.get("reason") or ""),
                "by": str(rec.get("by") or ""), "at": str(rec.get("at") or "")}
    return {"state": default, "explicit": False, "default": default, "reason": "", "by": "", "at": ""}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def set_state(key: str, state: Optional[str], *, by: str, reason: str = "") -> Optional[Dict[str, Any]]:
    """Set (``state`` in STATES) or clear (``state`` None) the explicit state of ``key``; returns the new record or None.

    Read-latest, merge, atomic write under the store's lock: a parallel change of another object is never lost."""
    if state is not None and state not in STATES:
        raise ValueError(f"state must be one of {STATES}")
    kind = key.split(":", 1)[0]
    if kind not in KINDS:
        raise ValueError("invalid object key")
    reason = " ".join(str(reason or "").split())[:REASON_MAX]
    at = _now()
    out: Dict[str, Any] = {}

    def mutate(data: Any) -> Any:
        if not isinstance(data, dict):
            data = _empty()
        states = data.setdefault("states", {})
        history = data.setdefault("history", [])
        before = (states.get(key) or {}).get("state")
        if state is None:
            states.pop(key, None)
        else:
            rec = {"state": state, "reason": reason, "by": by, "at": at}
            states[key] = rec
            out["rec"] = rec
        history.append({"key": key, "from": before, "to": state, "reason": reason, "by": by, "at": at})
        if len(history) > HISTORY_MAX:
            del history[: len(history) - HISTORY_MAX]
        return data

    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    json_store.mutate_json(path, mutate, default=_empty())
    return out.get("rec")


# ── a die / configuration rename or move: the production keys follow (die_rename.py, config_move.py) ─────────────────────

def _retarget(doc: Any, fn) -> int:
    """Rewrite every production key of ``doc`` (states and history) through ``fn(die, config) -> (die, config) | None``."""
    if not isinstance(doc, dict):
        return 0
    n = 0

    def new_key(key: str) -> Optional[str]:
        kind, _s, rest = str(key).partition(":")
        die, sep, config = rest.partition("/")
        if kind != "production" or not sep:
            return None
        hit = fn(die, config)
        return f"production:{hit[0]}/{hit[1]}" if hit else None

    states = doc.get("states") if isinstance(doc.get("states"), dict) else {}
    for key in list(states):
        nk = new_key(key)
        if nk and nk != key:
            if nk in states:
                raise ValueError(f"display state already has {nk!r}")
            states[nk] = states.pop(key)
            n += 1
    for row in doc.get("history") or []:
        if isinstance(row, dict):
            nk = new_key(row.get("key", ""))
            if nk and nk != row.get("key"):
                row["key"] = nk
                n += 1
    return n


def rename_die_in(doc: Any, old: str, new: str) -> int:
    return _retarget(doc, lambda die, config: (new, config) if die == old else None)


def move_config_in(doc: Any, die: str, config: str, new_die: str, new_config: str) -> int:
    return _retarget(doc, lambda d, c: (new_die, new_config) if (d, c) == (die, config) else None)
