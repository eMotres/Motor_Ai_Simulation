"""Who may see WHICH motors of the shared catalog.

The catalog (config/dies) is one shared library; the accounts that read it are
not one audience.  Until 2026-09-02 a signed-in non-admin saw the motors that
happened to carry a passported catalog card — a marketing rule ("show only what
a visitor can actually open") standing in for an access rule, which is why the
owner's own second account could not see the two 85 mm dies.

Access is now REGISTRY data: `motors: {"all": bool, "dies": [names]}` on the
user record (motor_ai_sim.users).  Three answers:

* ``all``        — admins (tier admin / ADMIN_EMAILS), any account granted
                   ``all``, and — when CATALOG_GRANT_ALL_REGISTERED is on —
                   every signed-in account.  Sees the whole catalog.
* ``granted``    — a signed-in account sees exactly its granted dies.  A new
                   account has no ``motors`` key at all and therefore sees
                   NOTHING until the vendor assigns motors to it.
* ``anonymous``  — no credentials: the public exhibit, still filtered by the
                   passport rule in routes/family.tree.  DELIBERATELY unchanged
                   here — it is the marketing landing, and the one place a
                   follow-up decision may flip (open everything to registered
                   users, or close the exhibit entirely).

That follow-up decision arrived on 2026-09-16, and it was "close it" — but NOT
here.  An internet-facing deployment sets ``PUBLIC_EXHIBIT=0`` and then no
anonymous request reaches this module at all: the door is one gate in
``auth.TierGateMiddleware`` (``auth.public_exhibit`` / ``auth.anonymous_allowed``)
and it answers 401 before any route runs.  ``MODE_ANONYMOUS`` stays exactly as
it is because the workstation — where the variable is unset — still uses it, and
a second copy of the rule per route is how such rules drift apart.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Optional

from motor_ai_sim.auth import ANON_OWNER, caller_identity
from motor_ai_sim.config import DEFAULT_CONFIG_PATH

MODE_ALL = "all"
MODE_GRANTED = "granted"
MODE_ANONYMOUS = "anonymous"

_ENV_GRANT_ALL = "CATALOG_GRANT_ALL_REGISTERED"

VIS_PRIVATE = "private"
VIS_PUBLIC = "public"
VIS_SELECTED = "selected"
VISIBILITIES = frozenset({VIS_PRIVATE, VIS_PUBLIC, VIS_SELECTED})


def grant_all_registered() -> bool:
    """The 'later' mode the user asked to keep switchable: every REGISTERED
    account sees the whole catalog.  Read per call, so flipping the env (or a
    test monkeypatching it) takes effect without a restart.  Default OFF."""
    return os.environ.get(_ENV_GRANT_ALL, "0").strip().lower() in (
        "1", "true", "yes", "on")


def catalog_access(authorization: Optional[str] = None) -> dict:
    """`{"mode", "dies", "email", "is_admin"}` for the calling credentials.

    One identity resolution for the whole request — `is_admin` here is the same
    answer `caller_identity` gives every other route, so `can_write` and
    visibility can never drift apart.
    """
    who = caller_identity(authorization)
    if who.get("is_admin"):
        return {"mode": MODE_ALL, "dies": frozenset(), "email": None,
                "is_admin": True}
    ident = str(who.get("id") or "")
    if not ident or ident == ANON_OWNER:
        return {"mode": MODE_ANONYMOUS, "dies": frozenset(), "email": None,
                "is_admin": False}
    if grant_all_registered():
        return {"mode": MODE_ALL, "dies": frozenset(), "email": ident,
                "is_admin": False}
    from motor_ai_sim import users as _users
    grants = _users.get_motor_grants(ident)
    if grants.get("all"):
        return {"mode": MODE_ALL, "dies": frozenset(), "email": ident,
                "is_admin": False}
    return {"mode": MODE_GRANTED, "dies": frozenset(grants.get("dies") or []),
            "email": ident, "is_admin": False}


def may_see_die(access: dict, die: str) -> bool:
    """May THIS caller read the named die at all?

    Anonymous is allowed through here on purpose: the public exhibit is
    filtered where it is BUILT (routes/family.tree) and its per-die reads have
    always been open — closing them is the follow-up decision documented above,
    not part of the per-user grant work.  For a signed-in non-admin the answer
    is the grant, OR the die's own owner-set visibility (public / selected
    clients — see ``get_die_access`` below), so hiding a motor in the tree is
    not cosmetic: the payload / datasheet / context routes 404 on it, and MCP
    tools (list_machines, check_fit) ask this same function.
    """
    mode = access.get("mode")
    if mode in (MODE_ALL, MODE_ANONYMOUS):
        return True
    if str(die) in access.get("dies", frozenset()):
        return True
    da = get_die_access(die)
    vis = da.get("visibility")
    if vis == VIS_PUBLIC:
        return True
    if vis == VIS_SELECTED:
        email = (access.get("email") or "").strip().lower()
        return bool(email) and email in da.get("clients", ())
    return False


# ── per-die access (public / selected clients) ───────────────────────────────
# A SECOND, ORTHOGONAL visibility rule on top of the per-user grant above: the
# grant says "this account may see its own list of dies"; this says "this die,
# regardless of who is asking, is public (any signed-in account) or shared
# with a short list of named accounts".  One admin-only store, keyed by die
# name, not duplicated per user — the owner sets it once on the die.
#
# Deliberately NOT anonymous-facing: "every signed-in user" is the whole
# promise here, same as the docstring above. The anonymous public exhibit
# stays exactly the passport-filtered set routes/family.tree already builds.
#
# Recipients are read-only: this module only ever answers "may see", nothing
# here grants write access to the owner's die (require_catalog_write is the
# separate, unaffected gate for that).

_DIE_ACCESS_FILE = Path(DEFAULT_CONFIG_PATH).parent / "die_access.json"
_DA_LOCK = threading.Lock()


def die_access_file() -> Path:
    """The path itself, for callers (routes/family._tree_signature) that only
    need its mtime — kept as a function so tests can monkeypatch the module
    attribute and have both readers/writers and this follow along."""
    return _DIE_ACCESS_FILE


def _load_die_access() -> dict:
    try:
        with open(_DIE_ACCESS_FILE, encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}          # unreadable → treat as "nothing set" (fail private)
    return d if isinstance(d, dict) else {}


def _save_die_access(d: dict) -> None:
    _DIE_ACCESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _DIE_ACCESS_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1, ensure_ascii=False, sort_keys=True)
    tmp.replace(_DIE_ACCESS_FILE)


def normalize_die_access(raw: Optional[dict]) -> dict:
    raw = raw or {}
    vis = str(raw.get("visibility") or VIS_PRIVATE).strip().lower()
    if vis not in VISIBILITIES:
        vis = VIS_PRIVATE
    clients = raw.get("clients")
    if not isinstance(clients, (list, tuple, set)):
        clients = []
    return {"visibility": vis,
            "clients": sorted({str(c).strip().lower() for c in clients if str(c).strip()})}


def get_die_access(die: str) -> dict:
    return normalize_die_access(_load_die_access().get(str(die)))


def all_die_access() -> dict:
    """``{die_name: {"visibility", "clients"}}`` for every die that has a
    non-default entry — used by the admin list (dies with no entry are
    private by default and not worth a row of zeros in the store)."""
    return {k: normalize_die_access(v) for k, v in _load_die_access().items()}


def set_die_access(die: str, *, visibility: str, clients: Optional[list] = None) -> dict:
    """Replace one die's access.  Same atomic-write discipline as
    users.set_motor_grants: one writer, one lock, one file."""
    die = str(die).strip()
    entry = normalize_die_access({"visibility": visibility, "clients": clients or []})
    with _DA_LOCK:
        store = _load_die_access()
        if entry["visibility"] == VIS_PRIVATE and not entry["clients"]:
            store.pop(die, None)   # back to default — do not carry dead rows
        else:
            store[die] = entry
        _save_die_access(store)
    return get_die_access(die)
