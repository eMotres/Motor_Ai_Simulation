"""Where a production really is (owner 2026-11-17): prepared / in production / finished, set by an admin, never guessed.

Owner: "In production now is ONLY this: CIANO14 40 (40_12 and 40_20). G2 has finished production, 200_50 has finished
too. All the others are at the simulation or confirmation stage. Production starts when the money from the customer
arrives."  Until then the derived stage said "in production" for every motor that merely had a production RECORD.

What the production record already stores (``production/models.normalize_production``): ``status`` active / paused /
archived (how the record is handled, reused as the display default of ``display_state``), ``order_quantity``, the
procurement rows with their readiness, the assembly phases.  None of them says whether the customer has paid or whether
the batch is built, so the state is a new explicit fact, kept beside the other object stores
(``<shared root>/object_registry/production_lifecycle.json``, override ``MOTRES_OBJECT_REGISTRY_ROOT``), never inside a
``production.json``:

====================  ==============================================================================================
``prepared``          the default for every production record: it exists, nothing is started
``in_production``     started: the customer's payment has arrived.  Setting it REQUIRES a confirmation line
                      ("payment received": a date or a note; no price or amount is stored)
``finished``          built: delivered or on the shelf
====================  ==============================================================================================

Every change is an admin action, written to the admin audit and to the store's own history with who / when / source.
The factory (the production's members) sees the state; it cannot set it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from motor_ai_sim import json_store

STATES = ("prepared", "in_production", "finished")
NOTE_MAX = 200
HISTORY_MAX = 2000


def store_path() -> Path:
    from motor_ai_sim.display_state import registry_root
    return registry_root() / "production_lifecycle.json"


def key(die: str, config: str) -> str:
    if not die or not config:
        raise ValueError("die and config required")
    return f"production:{die}/{config}"


def _empty() -> Dict[str, Any]:
    return {"version": 1, "states": {}, "history": []}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load() -> Dict[str, Any]:
    data = json_store.read_json(store_path(), default=None)
    if not isinstance(data, dict):
        return _empty()
    if not isinstance(data.get("states"), dict):
        data["states"] = {}
    if not isinstance(data.get("history"), list):
        data["history"] = []
    return data


def state_of(die: str, config: str, states: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """``{state, explicit, payment, by, at, source}``; a production nobody set is ``prepared`` (explicit False)."""
    states = load()["states"] if states is None else states
    rec = states.get(key(die, config))
    if isinstance(rec, dict) and rec.get("state") in STATES:
        return {"state": rec["state"], "explicit": True, "payment": rec.get("payment"), "by": str(rec.get("by") or ""),
                "at": str(rec.get("at") or ""), "source": str(rec.get("source") or "")}
    return {"state": "prepared", "explicit": False, "payment": None, "by": "", "at": "", "source": ""}


def set_state(die: str, config: str, state: str, *, by: str, payment: str = "", source: str = "") -> Dict[str, Any]:
    """Set the state; ``in_production`` needs the payment line (owner's rule for the start).  Read-latest, merge, atomic."""
    if state not in STATES:
        raise ValueError(f"state must be one of {STATES}")
    payment = " ".join(str(payment or "").split())[:NOTE_MAX]
    if state == "in_production" and not payment:
        raise ValueError("starting a production needs the payment line (a date or a note)")
    k = key(die, config)
    at = _now()
    rec: Dict[str, Any] = {"state": state, "by": str(by or ""), "at": at, "source": " ".join(str(source or "").split())[:NOTE_MAX]}

    def mutate(data: Any) -> Any:
        if not isinstance(data, dict):
            data = _empty()
        states = data.setdefault("states", {})
        history = data.setdefault("history", [])
        before = (states.get(k) or {})
        # the payment line of a started production stays with it when it is later marked finished
        rec["payment"] = payment or (before.get("payment") if state == "finished" else None) or None
        states[k] = rec
        history.append({"key": k, "from": before.get("state"), "to": state, "by": rec["by"], "at": at,
                        "payment": rec["payment"], "source": rec["source"]})
        if len(history) > HISTORY_MAX:
            del history[: len(history) - HISTORY_MAX]
        return data

    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    json_store.mutate_json(path, mutate, default=_empty())
    return rec


def seed(rows: Iterable[Dict[str, Any]], *, by: str, source: str, dry_run: bool = True) -> list:
    """The initial states on the owner's words (``[{"die", "config", "state", "payment"?}]``): what would change; with
    ``dry_run`` False it is written (each row through :func:`set_state`)."""
    states = load()["states"]
    plan = []
    for r in rows:
        cur = state_of(str(r["die"]), str(r["config"]), states)
        plan.append({"die": r["die"], "config": r["config"], "from": cur["state"], "to": r["state"]})
        if not dry_run and cur["state"] != r["state"]:
            set_state(str(r["die"]), str(r["config"]), str(r["state"]), by=by, payment=str(r.get("payment") or ""), source=source)
    return plan


# ── a die / configuration rename or move: the keys follow (die_rename.py, config_move.py) ─────────────────────────────

def _retarget(doc: Any, fn) -> int:
    if not isinstance(doc, dict):
        return 0
    n = 0

    def new_key(k: str) -> Optional[str]:
        kind, _s, rest = str(k).partition(":")
        die, sep, cfg = rest.partition("/")
        if kind != "production" or not sep:
            return None
        hit = fn(die, cfg)
        return f"production:{hit[0]}/{hit[1]}" if hit else None

    states = doc.get("states") if isinstance(doc.get("states"), dict) else {}
    for k in list(states):
        nk = new_key(k)
        if nk and nk != k:
            if nk in states:
                raise ValueError(f"production lifecycle already has {nk!r}")
            states[nk] = states.pop(k)
            n += 1
    for row in doc.get("history") or []:
        if isinstance(row, dict):
            nk = new_key(row.get("key", ""))
            if nk and nk != row.get("key"):
                row["key"] = nk
                n += 1
    return n


def rename_die_in(doc: Any, old: str, new: str) -> int:
    return _retarget(doc, lambda die, cfg: (new, cfg) if die == old else None)


def move_config_in(doc: Any, die: str, config: str, new_die: str, new_config: str) -> int:
    return _retarget(doc, lambda d, c: (new_die, new_config) if (d, c) == (die, config) else None)
