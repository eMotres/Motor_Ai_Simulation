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
"""
from __future__ import annotations

import os
from typing import Optional

from motor_ai_sim.auth import ANON_OWNER, caller_identity

MODE_ALL = "all"
MODE_GRANTED = "granted"
MODE_ANONYMOUS = "anonymous"

_ENV_GRANT_ALL = "CATALOG_GRANT_ALL_REGISTERED"


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
    is the grant and nothing else, so hiding a motor in the tree is not
    cosmetic: the payload / datasheet / context routes 404 on it.
    """
    mode = access.get("mode")
    if mode in (MODE_ALL, MODE_ANONYMOUS):
        return True
    return str(die) in access.get("dies", frozenset())
