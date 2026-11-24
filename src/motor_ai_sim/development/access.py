from __future__ import annotations

from fastapi import HTTPException, Request
from motor_ai_sim import auth
from motor_ai_sim.development import store


def is_engineer(user: dict) -> bool:
    return str(user.get("role") or "").lower() in {"admin", "engineer"}


def membership(email: str, project: dict) -> str | None:
    email = email.strip().lower()
    for member in project.get("members", []):
        if isinstance(member, dict) and str(member.get("email") or "").strip().lower() == email:
            return member.get("role")
    return None


def holds_price_grant(entry: dict | None) -> bool:
    """The PROJECT-level grant of one member or pending-invite entry: an owner always, anybody else only with ``sees_prices: true``.
    A grant is not yet access: ``sees_prices`` below puts the account's identity first."""
    if not isinstance(entry, dict):
        return False
    return str(entry.get("role") or "").lower() == "owner" or entry.get("sees_prices") is True


def sees_prices(user: dict | None, project: dict) -> bool:
    """THE central price rule (owner 2026-11-22 / coordinator 2026-11-23); the quote, the documents and messages marked
    price-restricted, the Contract section and the digest all ask this one function.  The IDENTITY comes first and no project role
    or flag overrides it:

    1. no account, an MCP agent (``auth._AGENT_USER`` set) or a factory account (global role ``oem``): NEVER;
    2. MOTRES staff (global role admin or engineer): ALWAYS, member or not;
    3. anybody else: a project OWNER, or a member whose entry carries ``sees_prices: true`` (a project owner or staff grants it).
    """
    if not user or auth._AGENT_USER.get() is not None:
        return False
    role = str(user.get("role") or "").lower()
    if role == "oem":
        return False
    if role in ("admin", "engineer"):
        return True
    email = str(user.get("email") or "").strip().lower()
    for member in project.get("members", []):
        if isinstance(member, dict) and str(member.get("email") or "").strip().lower() == email:
            return holds_price_grant(member)
    return False


def is_staff(user: dict | None) -> bool:
    """THE central staff rule for STAFF-ONLY messages (owner 2026-11-24, "one message stream"): MOTRES staff = the GLOBAL role admin or
    engineer, never an MCP agent (even one acting for a staff owner), never a factory account, never a project role.  Independent of
    ``sees_prices``: a message may carry both marks and a staff member always passes both."""
    if not user or auth._AGENT_USER.get() is not None:
        return False
    return is_engineer(user)


can_see_staff_only = is_staff


def is_staff_email(email: str) -> bool:
    """``is_staff`` for a RECIPIENT known only by address (digest, notifications): the account's global role is resolved from the user
    store.  An address with no (enabled) account is a pending invite or a stranger: False."""
    from motor_ai_sim import users
    key = str(email or "").strip().lower()
    account = users.get_user(key) if key else None
    if not account or account.get("disabled"):
        return False
    return is_staff({"email": key, "role": account.get("role", "user")})


def can_see_message(user: dict | None, project: dict, message: dict) -> bool:
    """Two independent marks.  ``staff_only`` (an internal note, owner 2026-11-24): only ``is_staff``.  ``price_restricted`` (owner
    2026-11-22): whoever ``sees_prices`` lets through now - the one central rule, no exception for the author (a revoked or demoted
    author loses it too).  Nobody else sees the text or the existence.  This judges the message itself; ``visible_messages`` also hides
    the replies of a hidden message."""
    if not isinstance(message, dict):
        return True
    if message.get("staff_only") and not is_staff(user):
        return False
    if message.get("price_restricted") and not sees_prices(user, project):
        return False
    return True


def visible_messages(user: dict | None, project: dict, messages: list) -> list:
    """The messages ``user`` may see.  A reply (any depth) to a hidden message is hidden with it, whatever its own flag says."""
    shown: list = []
    hidden: set = set()
    parent_of = {str(m.get("id")): str(m.get("reply_to") or "") for m in messages if isinstance(m, dict) and m.get("id")}
    for m in messages:
        if isinstance(m, dict) and m.get("id") and not can_see_message(user, project, m):
            hidden.add(str(m["id"]))
    changed = True
    while changed:                                         # descendants of a hidden message are hidden
        changed = False
        for mid, parent in parent_of.items():
            if parent and parent in hidden and mid not in hidden:
                hidden.add(mid)
                changed = True
    for m in messages:
        if not isinstance(m, dict) or str(m.get("id")) in hidden:
            continue
        shown.append(m)
    return shown


def may_restrict_messages(user: dict | None, project: dict) -> bool:
    """Who may mark a message price-restricted: MOTRES staff and the project owner (``can_manage_prices``)."""
    return can_manage_prices(user, project)


def sees_prices_email(email: str, project: dict) -> bool:
    """``sees_prices`` for a RECIPIENT known only by address (digest, notifications): the account's global role is resolved from the
    user store, never derived from the project role.  An address with no (enabled) account is a pending invite or a stranger: False."""
    from motor_ai_sim import users
    key = str(email or "").strip().lower()
    account = users.get_user(key) if key else None
    if not account or account.get("disabled"):
        return False
    return sees_prices({"email": key, "role": account.get("role", "user")}, project)


def can_manage_prices(user: dict | None, project: dict) -> bool:
    """Who may change who sees the prices: MOTRES staff (admin / global engineer) and the project OWNER.  Never an agent, a factory
    account or a project engineer / customer member."""
    if not user or auth._AGENT_USER.get() is not None:
        return False
    role = str(user.get("role") or "").lower()
    if role == "oem":
        return False
    if role in ("admin", "engineer"):
        return True
    return membership(str(user.get("email") or ""), project) == "owner"


def can_view(user: dict, project: dict) -> bool:
    return is_engineer(user) or membership(str(user.get("email") or ""), project) is not None


def can_edit_spec(user: dict, project: dict) -> bool:
    """Owner rule 2026-10-26: the specification is changed ONLY by the project owner (project role "owner") and by
    global admins.  Project engineers, customers and global engineers who are not admins only view it: they test in
    Configure and give recommendations in the project thread."""
    if str(user.get("role") or "").lower() == "admin":
        return True
    return membership(str(user.get("email") or ""), project) == "owner"


def require_signed_in(request: Request) -> dict:
    from motor_ai_sim.production.access import require_signed_in as production_require_signed_in
    return production_require_signed_in(request)


def require_project(request: Request, project_id: str, *, edit: bool = False) -> tuple[dict, dict, str | None]:
    return project_for(require_signed_in(request), project_id, edit=edit)


def project_for(user: dict, project_id: str, *, edit: bool = False) -> tuple[dict, dict, str | None]:
    """``require_project`` for a caller that is already resolved (the web session or an MCP agent acting for its owner):
    404 unless ``user`` may view the project, 403 for ``edit`` without project-engineer rights."""
    project = store.get_project(project_id) if store.valid_id(project_id) else None
    if project is None or not can_view(user, project):
        raise HTTPException(status_code=404, detail="Project not found")
    role = membership(str(user.get("email") or ""), project)
    if edit and not (is_engineer(user) or role in ("owner", "engineer")):
        raise HTTPException(status_code=403, detail="Project engineer access required")
    return user, project, role


def require_spec_editor(request: Request, project_id: str) -> tuple[dict, dict, str | None]:
    return spec_editor_for(require_signed_in(request), project_id)


def spec_editor_for(user: dict, project_id: str) -> tuple[dict, dict, str | None]:
    """``require_spec_editor`` for an already resolved caller: 404 / 403 unless ``can_edit_spec``."""
    user, project, role = project_for(user, project_id)
    if not can_edit_spec(user, project):
        raise HTTPException(status_code=403, detail="Only the project owner or an admin can edit the specification")
    return user, project, role
