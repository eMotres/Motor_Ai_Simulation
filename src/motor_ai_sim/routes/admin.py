"""Admin-only endpoints: user management + usage statistics.

The data source is the SELF-HOSTED account store (``users.py`` registry,
``sessions.py`` for last-seen, the per-user workspace folder for the design
count).  The Firebase Admin SDK this module once read is gone from the
deployment; until 2026-10-04 that silently turned the routes into the MOCK
dataset in production.  Mock data is now served only when ``ADMIN_MOCK_DATA=1``
is set explicitly (UI development without a registry), and is always flagged
``source: "mock"``.

Every route requires role == admin (require_admin). When AUTH_ENFORCE is off
(local dev) the gate is open and the caller is treated as admin.
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException

from motor_ai_sim import admin_audit as _AA
from motor_ai_sim.auth import require_admin, require_admin_or_token

router = APIRouter(prefix="/api/admin", tags=["admin"])

_VALID_ROLES = ("user", "admin")
_DAY_MS = 86_400_000.0

#: ``source`` of the real user list (the self-hosted registry).
_USERS_SOURCE = "self-hosted:users.json"
#: ``source`` of the ticket list (ticket_store; Firestore is gone).
_TICKETS_SOURCE = "self-hosted:tickets.json"


def _mock_enabled() -> bool:
    """Explicit dev switch for the demo dataset - never implied by a missing
    SDK or by AUTH_ENFORCE."""
    return os.environ.get("ADMIN_MOCK_DATA", "").strip().lower() in ("1", "true", "yes")


def _created_ms(created) -> Optional[float]:
    """users.json stores ``created`` as a local ISO string; the UI wants ms."""
    if isinstance(created, (int, float)):
        return float(created) * (1000.0 if created < 1e11 else 1.0)
    if isinstance(created, str) and created:
        try:
            return datetime.fromisoformat(created).timestamp() * 1000.0
        except ValueError:
            return None
    return None


def _design_count(email: str) -> int:
    """Dies in the account's own workspace (``<WORKSPACES_ROOT>/<id>/dies``).
    One listdir, no workspace is created or registered; 0 when absent."""
    try:
        from motor_ai_sim.workspace import workspaces_root, workspace_id
        base = workspaces_root()
        if base is None:
            return 0
        d = base / workspace_id(email) / "dies"
        return sum(1 for c in d.iterdir() if c.is_dir()) if d.is_dir() else 0
    except Exception:                                       # noqa: BLE001
        return 0


def _real_users() -> list[dict]:
    from motor_ai_sim import sessions as _S
    from motor_ai_sim import users as _U
    last_seen: dict = {}
    for r in _S.list_all():
        e = str(r.get("email") or "").strip().lower()
        t = float(r.get("last_seen") or r.get("created") or 0) * 1000.0
        if e and t > last_seen.get(e, 0.0):
            last_seen[e] = t
    out: list[dict] = []
    for u in _U.list_users():
        email = u["email"]
        out.append({
            "uid": email,                       # the registry key IS the id
            "email": email,
            "displayName": u.get("name") or email.split("@")[0],
            "createdAt": _created_ms(u.get("created")),
            "lastLoginAt": last_seen.get(email),
            "disabled": bool(u.get("disabled")),
            "role": u.get("role") or "user",
            "designCount": _design_count(email),
        })
    return out


def _mock_users() -> list[dict]:
    """Deterministic demo users (timestamps relative to now, so 'active' and the
    signup timeline look live). Served only with ADMIN_MOCK_DATA=1."""
    now = time.time() * 1000
    # (email, role, created_days_ago, last_login_days_ago|None, designs, disabled)
    rows = [
        ("vadim.owner@example.com", "admin", 240, 0, 14, False),
        ("eng.lead@example.com",    "admin", 220, 2, 9, False),
        ("alice.eng@example.com",   "user",  180, 1, 11, False),
        ("bob.design@example.com",  "user",  150, 3, 6, False),
        ("carla.eng@example.com",   "user",  140, 0, 22, False),
        ("dmitri.eng@example.com",  "user",  120, 5, 17, False),
        ("erin.user@example.com",   "user",  95, 4, 3, False),
        ("frank.user@example.com",  "user",  80, 12, 1, False),
        ("grace.user@example.com",  "user",  70, 40, 2, False),
        ("hugo.user@example.com",   "user",  55, 65, 0, False),
        ("ivy.eng@example.com",     "user",  42, 6, 5, False),
        ("jack.user@example.com",   "user",  30, 8, 1, False),
        ("kira.user@example.com",   "user",  18, 2, 0, False),
        ("leo.user@example.com",    "user",  9, 1, 1, False),
        ("mara.user@example.com",   "user",  3, None, 0, False),
        ("spam.bot@example.com",    "user",  60, 58, 0, True),
    ]
    out = []
    for i, (email, role, cago, lago, designs, disabled) in enumerate(rows):
        out.append({
            "uid": f"mock_{i:02d}",
            "email": email,
            "displayName": email.split("@")[0],
            "createdAt": now - cago * _DAY_MS,
            "lastLoginAt": (now - lago * _DAY_MS) if lago is not None else None,
            "disabled": disabled,
            "role": role,
            "designCount": designs,
        })
    return out


def _load_users() -> tuple[str, list[dict]]:
    if _mock_enabled():
        return "mock", _mock_users()
    return _USERS_SOURCE, _real_users()


def _day(ms: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


def _compute_stats(users: list[dict]) -> dict:
    now = time.time() * 1000
    by_role: dict = {}
    active7 = active30 = 0
    buckets: dict = {}
    for u in users:
        r = u.get("role") or "user"
        by_role[r] = by_role.get(r, 0) + 1
        c = u.get("createdAt")
        if c:
            buckets[_day(c)] = buckets.get(_day(c), 0) + 1
        ll = u.get("lastLoginAt")
        if ll is not None:
            if now - ll <= 7 * _DAY_MS:
                active7 += 1
            if now - ll <= 30 * _DAY_MS:
                active30 += 1
    signups = []
    cum = 0
    for d in sorted(buckets):
        cum += buckets[d]
        signups.append({"date": d, "count": buckets[d], "total": cum})
    disabled = sum(1 for u in users if u.get("disabled"))
    designs = sum(int(u.get("designCount") or 0) for u in users)
    return {
        "total": len(users),
        "disabled": disabled,
        "designs": designs,
        "byRole": by_role,
        "active7": active7,
        "active30": active30,
        "signups": signups,
    }


@router.get("/users")
def list_users(_admin: dict = Depends(require_admin)):
    """All users with role, sign-up / last-login times, and saved-design count."""
    source, users = _load_users()
    users.sort(key=lambda u: u.get("createdAt") or 0, reverse=True)
    return {"source": source, "count": len(users), "users": users}


@router.get("/stats")
def stats(_admin: dict = Depends(require_admin)):
    """Aggregate usage: totals, role split, active 7/30d, signup timeline."""
    source, users = _load_users()
    return {"source": source, **_compute_stats(users)}


@router.post("/users/{uid}/role")
def set_role(uid: str, body: dict = Body(default={}), _admin: dict = Depends(require_admin)):
    """Set an account's role in the self-hosted registry (``uid`` = its e-mail)."""
    _AA.record(_AA.actor_of(_admin), "user.role", str(uid), subject=str(uid), details={"role": (body or {}).get("role")})
    role = (body or {}).get("role")
    if role not in _VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"role must be one of {_VALID_ROLES}")
    if _mock_enabled():
        return {"ok": True, "source": "mock", "uid": uid, "role": role}
    from motor_ai_sim import users as _U
    try:
        _U.update_user(uid, role=role)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"user '{uid}' not found")
    return {"ok": True, "source": _USERS_SOURCE, "uid": uid, "role": role}


@router.post("/users/{uid}/disable")
def set_disabled(uid: str, body: dict = Body(default={}), _admin: dict = Depends(require_admin)):
    """Disable or re-enable an account in the self-hosted registry."""
    disabled = bool((body or {}).get("disabled", True))
    _AA.record(_AA.actor_of(_admin), "user.disable", str(uid), subject=str(uid), details={"disabled": disabled})
    if _mock_enabled():
        return {"ok": True, "source": "mock", "uid": uid, "disabled": disabled}
    from motor_ai_sim import users as _U
    try:
        _U.update_user(uid, disabled=disabled)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"user '{uid}' not found")
    return {"ok": True, "source": _USERS_SOURCE, "uid": uid, "disabled": disabled}


# ── Per-user motor access ─────────────────────────────────────────────────────
# WHICH catalog motors an account may open is registry data (users.json
# `motors: {all, dies}`), read by motor_access on every catalog request.  These
# three routes are the admin UI's side of it; nothing else writes grants.


@router.get("/motors")
def list_motors(_admin: dict = Depends(require_admin)):
    """Every die in the shared catalog — the grant picker's list."""
    from motor_ai_sim import passport_store as _ps
    from motor_ai_sim.routes.family import catalog_dies
    dies = catalog_dies()
    # Which configurations have a FULL passport card (a v1 record in the
    # passport store) - read from the store, never from a list of names.
    idx = _ps.card_index()
    for d in dies:
        have = idx.get(str(d.get("name")), {})
        d["cards"] = {c: have[c] for c in (d.get("config_names") or []) if c in have}
    return {"count": len(dies), "dies": dies}


@router.get("/users/{email}/motors")
def get_user_motors(email: str, _admin: dict = Depends(require_admin)):
    """This account's grants: `{"all": bool, "dies": [names]}`."""
    _AA.record(_AA.actor_of(_admin), "workspace.read", str(email), subject=str(email), details={"what": "motor grants"})
    from motor_ai_sim import users as U
    if U.get_user(email) is None:
        raise HTTPException(status_code=404, detail=f"user '{email}' not found")
    return {"email": email.strip().lower(), "motors": U.get_motor_grants(email)}


def _check_default(raw, all_motors: bool, dies: list):
    """Validate the per-user default motor: `{"die", "config"}` or null.

    The die must be GRANTED to the account (any die for an `all` grant) and the
    configuration must exist on it — a default the user could not open would be
    a Configure that starts empty with no explanation."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise HTTPException(status_code=422,
                            detail="'default' must be {\"die\": ..., \"config\": ...} or null")
    die = str(raw.get("die") or "").strip()
    cfg = str(raw.get("config") or "").strip()
    if not die or not cfg:
        raise HTTPException(status_code=422,
                            detail="the default motor needs both a die and a configuration")
    from motor_ai_sim.routes.family import die_config_names, die_names
    if die not in die_names():
        raise HTTPException(status_code=422, detail=f"unknown die '{die}' for the default motor")
    if not all_motors and die not in dies:
        raise HTTPException(status_code=422, detail=(
            f"the default motor '{die}' is not granted to this account — "
            "grant the die first or pick one of its granted dies"))
    cfgs = die_config_names(die)
    if cfg not in cfgs:
        raise HTTPException(status_code=422, detail=(
            f"die '{die}' has no configuration '{cfg}' — it has "
            + (", ".join(f"'{c}'" for c in cfgs) if cfgs else "none")))
    return {"die": die, "config": cfg}


@router.put("/users/{email}/motors")
def set_user_motors(email: str, body: dict = Body(default={}),
                    _admin: dict = Depends(require_admin)):
    """Replace this account's grants.  `all: true` means the whole catalog
    (present and future); otherwise `dies` is the exact list.

    Unknown die names are REFUSED and named — a grant silently dropped because
    a die was renamed is a user who still sees nothing and no way to find out
    why."""
    _AA.record(_AA.actor_of(_admin), "user.motors", str(email), subject=str(email), details={"all": (body or {}).get("all")})
    from motor_ai_sim import users as U
    if U.get_user(email) is None:
        raise HTTPException(status_code=404, detail=f"user '{email}' not found")
    body = body or {}
    all_motors = bool(body.get("all"))
    raw = body.get("dies")
    if raw is not None and not isinstance(raw, (list, tuple)):
        raise HTTPException(status_code=422,
                            detail="'dies' must be a list of die names")
    dies = _check_dies(raw)
    # The DEFAULT motor (die + configuration Configure opens on).  Key absent =
    # keep the stored one while the new grant still covers it; null = clear.
    if "default" in body:
        default = _check_default(body.get("default"), all_motors, dies)
        grants = U.set_motor_grants(email, all_motors=all_motors, dies=dies,
                                    default=default)
    else:
        grants = U.set_motor_grants(email, all_motors=all_motors, dies=dies)
    return {"ok": True, "email": email.strip().lower(), "motors": grants}


# ── Die-level access (public / selected clients) ────────────────────────────
# ORTHOGONAL to the per-user grant above: a grant says which dies an ACCOUNT
# may see; this says whether a DIE, on its own, is private (default), open to
# every signed-in account, or shared with a short list of named ones. One
# store (motor_access.die_access.json), admin-only writes, audited like every
# other admin action (sessions.record_event — the same log the Logs/Events
# admin tab already shows).

from motor_ai_sim import motor_access as _MA


def _used_by_count(die: str) -> int:
    """How many individual accounts hold this die in their OWN grant list —
    informational only (a public/selected die's real audience is wider; this
    is what the per-account grant table would show)."""
    from motor_ai_sim import users as U
    return sum(1 for row in U.list_users()
               if die in (row.get("motors") or {}).get("dies", []))


@router.get("/dies")
def list_dies_access(_admin: dict = Depends(require_admin)):
    """Every catalog die with its owner-set visibility, for the Motors access
    table: name, visibility, and how many accounts already hold it directly."""
    from motor_ai_sim.routes.family import catalog_dies
    from motor_ai_sim import data_publish as _DP
    from motor_ai_sim import data_sources as _DS
    access = _MA.all_die_access()
    scan = _DS.scan()
    journal_error = None
    try:
        moves = _DP.load_moves()
    except _DP.MoveError as exc:
        moves, journal_error = {}, str(exc)
    active: dict = {}
    for mv in moves.values():
        if mv.get("status") in _DP.ACTIVE and mv.get("at", "") >= active.get(mv.get("die"), {}).get("at", ""):
            active[mv.get("die")] = mv
    out = []
    for d in catalog_dies():
        name = d.get("name")
        a = access.get(name) or {"visibility": _MA.VIS_PRIVATE, "clients": []}
        s = scan.get(name)
        out.append({**d, "visibility": a["visibility"], "clients": a["clients"],
                    "used_by": _used_by_count(name),
                    "source": (s["source"] if s and s["source"] in _DS.SOURCES
                               else _DS.SOURCE_PRIVATE),
                    # False = the die is not in either data checkout (a shared or
                    # workspace die, or a clash): it counts as private and cannot
                    # be moved.
                    "source_movable": bool(s and s["source"] in _DS.SOURCES),
                    "source_clash": bool(s and s["clash"]),
                    "source_error": (s or {}).get("error"),
                    "source_pending": active.get(name)})
    # Clashing dies do not resolve, so the catalog listing leaves them out —
    # the admin still has to SEE them.
    # …and so must a die whose unfinished move left it in neither checkout.
    listed = {r["name"] for r in out}
    extra = {n for n, s in scan.items() if s["clash"]} | set(active)
    for name in sorted(extra - listed):
        s = scan.get(name) or {}
        out.append({"name": name, "stator_diameter": None, "configs": 0, "duties": 0,
                    "visibility": _MA.VIS_PRIVATE, "clients": [], "used_by": 0,
                    "source": _DS.SOURCE_PRIVATE, "source_movable": False,
                    "source_clash": bool(s.get("clash")), "source_error": s.get("error"),
                    "source_pending": active.get(name)})
    return {"count": len(out), "dies": out, "journal_error": journal_error}


# ── Data source: OPEN (public repo, Apache-2.0) / PRIVATE (private repo) ──────────
# Separate from visibility above: visibility is who may SEE a die on this
# server; the source is which git repository the die's files live in.
# PUBLICATION = the first push to the public repository: every check and the
# admin's confirmation of the exact snapshot happen BEFORE it
# (motor_ai_sim.data_publish).  The PRs are only for review/merge into main.

@router.get("/dies/{die}/source/preview")
def preview_die_source(die: str, target: str,
                       _admin: dict = Depends(require_admin)):
    """Exactly what moving the die to ``target`` would carry (the validated
    whitelist with hashes), what blocks it, the snapshot id to confirm, and
    the warning to show."""
    from motor_ai_sim import data_publish as _DP
    try:
        return _DP.preview(die, target)
    except _DP.MoveError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/dies/{die}/source")
def move_die_source(die: str, body: dict = Body(default={}),
                    admin_user: dict = Depends(require_admin)):
    """Move one die to ``target``.  ``confirm`` must be ``true`` and
    ``snapshot`` the id from the preview the admin read: confirming a publish
    pushes to the public repository IMMEDIATELY."""
    from motor_ai_sim import data_publish as _DP
    body = body or {}
    target = str(body.get("target") or "").strip().lower()
    if body.get("confirm") is not True or not str(body.get("snapshot") or ""):
        raise HTTPException(status_code=422, detail=(
            "a move needs 'confirm': true and the 'snapshot' of the preview you read "
            f"(GET /api/admin/dies/{die}/source/preview?target={target or 'open'})"))
    who = str(admin_user.get("id") or admin_user.get("email") or "")
    try:
        pv = _DP.preview(die, target)
    except _DP.MoveError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if pv["blockers"]:
        _DP.audit("move_refused", die=die, **{"from": pv["from"]}, to=target, by=who,
                  blockers=pv["blockers"])
        raise HTTPException(status_code=409, detail={
            "message": f"'{die}' cannot be moved — fix these first",
            "blockers": pv["blockers"]})
    try:
        rec = _DP.move_die(die, target, by=who, snapshot=str(body.get("snapshot")))
    except _DP.MoveError as exc:
        mv = None
        try:
            mv = _DP.active_move(die)
        except _DP.MoveError:
            pass
        if mv is None:
            _DP.audit("move_refused", die=die, **{"from": pv["from"]}, to=target,
                      by=who, error=str(exc)[:300])
        raise HTTPException(status_code=409, detail={"message": str(exc), "move": mv})
    return {"ok": True, **rec}


@router.get("/dies/source/moves")
def list_die_moves(_admin: dict = Depends(require_admin)):
    """The move journal, newest first."""
    from motor_ai_sim import data_publish as _DP
    try:
        moves = _DP.load_moves()
    except _DP.MoveError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"moves": sorted(moves.values(), key=lambda r: r.get("at", ""), reverse=True)}


@router.post("/dies/source/moves/{move_id}/resume")
def resume_die_move(move_id: str, admin_user: dict = Depends(require_admin)):
    """Re-run an incomplete move from its first unfinished step."""
    from motor_ai_sim import data_publish as _DP
    who = str(admin_user.get("id") or admin_user.get("email") or "")
    try:
        return {"ok": True, **_DP.resume(move_id, by=who)}
    except _DP.MoveError as exc:
        mv = None
        try:
            mv = _DP.get_move(move_id)
        except _DP.MoveError:
            pass
        raise HTTPException(status_code=409, detail={"message": str(exc), "move": mv})


@router.post("/dies/source/moves/{move_id}/rollback")
def rollback_die_move(move_id: str, body: dict = Body(default={}),
                      admin_user: dict = Depends(require_admin)):
    """Delete the move's branches and close its PRs (only before a merge)."""
    from motor_ai_sim import data_publish as _DP
    if (body or {}).get("confirm") is not True:
        raise HTTPException(status_code=422, detail="a rollback needs 'confirm': true")
    who = str(admin_user.get("id") or admin_user.get("email") or "")
    try:
        return {"ok": True, **_DP.rollback(move_id, by=who)}
    except _DP.MoveError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/dies/source/reconcile")
def reconcile_die_sources(admin_user: dict = Depends(require_admin)):
    """Clear 'pending publish' for every move whose PRs have been merged."""
    from motor_ai_sim import data_publish as _DP
    who = str(admin_user.get("id") or admin_user.get("email") or "")
    try:
        done = _DP.reconcile(by=who)
    except _DP.MoveError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True, "completed": done}


@router.get("/dies/source/audit")
def die_source_audit(die: Optional[str] = None,
                     _admin: dict = Depends(require_admin)):
    from motor_ai_sim import data_publish as _DP
    return {"events": _DP.read_audit(die)}


@router.get("/dies/{die}/access")
def get_die_access(die: str, _admin: dict = Depends(require_admin)):
    from motor_ai_sim.routes.family import die_names
    if die not in die_names():
        raise HTTPException(status_code=404, detail=f"die '{die}' not found")
    return {"die": die, **_MA.get_die_access(die)}


@router.put("/dies/{die}/access")
def set_die_access(die: str, body: dict = Body(default={}),
                   admin_user: dict = Depends(require_admin)):
    """Set one die's visibility: private (default), public (every signed-in
    account, incl. MCP agents — motor_access.may_see_die is the one gate both
    the web routes and the MCP tools ask), or selected (named accounts only).
    Recipients stay read-only regardless — this never grants catalog WRITE
    access, only the same read `may_see_die` already governs."""
    _AA.record(_AA.actor_of(admin_user), "die.access", str(die), subject=str(""), details={"visibility": (body or {}).get("visibility")})
    from motor_ai_sim.routes.family import die_names
    if die not in die_names():
        raise HTTPException(status_code=404, detail=f"die '{die}' not found")
    body = body or {}
    vis = str(body.get("visibility") or "").strip().lower()
    if vis not in _MA.VISIBILITIES:
        raise HTTPException(status_code=422,
                            detail=f"'visibility' must be one of {sorted(_MA.VISIBILITIES)}")
    clients = body.get("clients")
    if clients is not None and not isinstance(clients, (list, tuple)):
        raise HTTPException(status_code=422, detail="'clients' must be a list of e-mails")
    clients = [str(c).strip() for c in (clients or []) if str(c).strip()]
    if vis == _MA.VIS_SELECTED and clients:
        from motor_ai_sim import users as U
        unknown = sorted({c for c in clients if U.get_user(c) is None})
        if unknown:
            raise HTTPException(status_code=422,
                                detail=f"unknown account(s): {', '.join(unknown)}")
    result = _MA.set_die_access(die, visibility=vis, clients=clients)
    from motor_ai_sim import sessions as S
    S.record_event("die_access_change", email=str(admin_user.get("id") or admin_user.get("email") or ""),
                   reason=f"{die} -> {vis}" + (f" ({len(clients)} client(s))" if vis == _MA.VIS_SELECTED else ""),
                   path=f"/api/admin/dies/{die}/access")
    return {"ok": True, "die": die, **result}


# ── Invites ───────────────────────────────────────────────────────────────────
# The door for an external user, and the ONLY one on a host with
# PUBLIC_EXHIBIT=0 and CATALOG_GRANT_ALL_REGISTERED unset: an admin creates the
# registry row, sets its tier and picks its motors, and the person signs in with
# Google.  NO E-MAIL IS SENT — Hetzner blocks outbound 25/465, so a route that
# claimed to send one would be lying.  The admin tells the person.
#
# Three routes and no new store: an invite is a stamped row in users.json
# (users.invite_user), which is why disable / delete / grant changes made
# through the other admin routes cannot drift away from it.


def _check_dies(raw) -> list:
    """Validate a die list against the catalog and NAME the unknown ones.

    Shared with :func:`set_user_motors` above, which grew the rule first: a
    grant silently dropped because a die was renamed is a user who still sees
    nothing and no way to find out why.
    """
    from motor_ai_sim.routes.family import die_names
    if raw is None:
        raw = []
    if not isinstance(raw, (list, tuple)):
        raise HTTPException(status_code=422,
                            detail="'motors' must be a list of die names or \"all\"")
    dies = [str(d).strip() for d in raw if str(d).strip()]
    known = die_names()
    unknown = sorted({d for d in dies if d not in known})
    if unknown:
        raise HTTPException(status_code=422, detail=(
            "unknown die(s): " + ", ".join(f"'{d}'" for d in unknown)
            + " — the catalog has " + (", ".join(f"'{d}'" for d in sorted(known))
                                       if known else "no dies")))
    return dies


@router.post("/invite")
def invite(body: dict = Body(default={}),
           admin_user: dict = Depends(require_admin)):
    """Invite one external user: registry row + role + motors + workspace.

    `{email, role="user", motors: [die names] | "all" | [], note, name}`.

    Everything the account needs exists when this returns -- including its
    WORKSPACE, seeded from the shared machine, so the person's first request
    after signing in reads a working motor and not a half-created directory.
    Re-inviting an existing account re-sets its role, grants and note (and
    un-disables it); it never touches a password.
    """
    _AA.record(_AA.actor_of(admin_user), "user.invite", str(str((body or {}).get("email") or "")), subject=str(str((body or {}).get("email") or "")), details={"role": (body or {}).get("role")})
    from motor_ai_sim import users as U
    from motor_ai_sim import workspace as W
    body = body or {}
    email = str(body.get("email") or "").strip().lower()
    role = str(body.get("role") or "user").strip().lower()
    if role not in _VALID_ROLES:
        raise HTTPException(status_code=422,
                            detail=f"role must be one of {_VALID_ROLES}")
    motors = body.get("motors", [])
    all_motors = (motors is True
                  or (isinstance(motors, str) and motors.strip().lower() == "all")
                  or (isinstance(motors, dict) and bool(motors.get("all"))))
    dies = [] if all_motors else _check_dies(
        motors.get("dies") if isinstance(motors, dict) else motors)
    try:
        user = U.invite_user(email, role=role, name=str(body.get("name") or ""),
                             by=str(admin_user.get("email") or "") or "admin",
                             note=str(body.get("note") or ""))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    grants = U.set_motor_grants(email, all_motors=all_motors, dies=dies)
    ws = W.provision(email)
    return {"ok": True,
            "user": {**user, "motors": grants},
            "motors": grants,
            "workspace": (str(ws.root) if ws is not None else None),
            # Said out loud in the response because the admin is the messenger:
            # nothing left this server for the invited address.
            "emailed": False,
            "next": "no e-mail was sent — tell them to sign in with Google"}


@router.get("/invites")
def list_invites(_admin: dict = Depends(require_admin)):
    """Every invited account, newest first, with whether it has ever signed in."""
    from motor_ai_sim import sessions as S
    from motor_ai_sim import users as U
    rows = []
    for r in U.list_invites():
        try:
            seen = S.list_for(r["email"])
        except Exception:                               # noqa: BLE001
            seen = []
        last = max((float(s.get("last_seen") or s.get("created") or 0.0)
                    for s in seen), default=0.0)
        rows.append({**r, "accepted": bool(seen),
                     "last_seen": last or None})
    return {"count": len(rows), "invites": rows}


@router.delete("/invites/{email}")
def revoke_invite(email: str, _admin: dict = Depends(require_admin)):
    """Withdraw an invite: the registry row goes, every session of it is revoked.

    The account stops being able to sign in as itself — and if the person signs
    in with Google again they are an unknown address, i.e. `free` with NOTHING
    granted (auth._registry_role), which is the same as being outside.

    The WORKSPACE DIRECTORY IS NOT TOUCHED and its path is in the answer: it
    holds the person's own saved work, and deleting a user's data as a side
    effect of tidying an invite list is not a decision a DELETE on an invite
    gets to make.  Refuses (404) on an account that was never invited — those
    are removed through DELETE /api/auth/users/{email}, deliberately.
    """
    _AA.record(_AA.actor_of(_admin), "user.invite_revoke", str(email), subject=str(email), details=None)
    from motor_ai_sim import sessions as S
    from motor_ai_sim import users as U
    from motor_ai_sim import workspace as W
    email = (email or "").strip().lower()
    if U.get_user(email) is None:
        raise HTTPException(status_code=404, detail=f"user '{email}' not found")
    if U.invite_of(email) is None:
        raise HTTPException(status_code=404, detail=(
            f"'{email}' was not invited — delete it through "
            "DELETE /api/auth/users/{email} if that is what you mean"))
    n = S.revoke_all(email)
    U.delete_user(email)
    root = W.workspaces_root()
    ws_dir = (root / W.workspace_id(email)) if root is not None else None
    return {"ok": True, "email": email, "sessions_revoked": n,
            "workspace": {"id": W.workspace_id(email),
                          "path": str(ws_dir) if ws_dir else None,
                          "exists": bool(ws_dir and ws_dir.is_dir())}}


# ── Agent activity (MCP Stage 3) — drafts + runs of EVERY account ───────────
# Moved off the Motors catalog page (owner 2026-09-30: that page is not the
# right place for every AI agent's draft designs and job queue — move it into
# Admin). The job queue itself is already cross-account here (GET
# /api/jobs?all=true, honoured only for an admin caller — routes/jobs_api.py);
# this adds the missing half, drafts.


@router.get("/agent_designs")
def list_agent_designs(_admin: dict = Depends(require_admin)):
    """Every agent draft on this server, across every account, newest first."""
    _AA.record(_AA.actor_of(_admin), "agent_design.read", str("*"), subject=str(""), details=None)
    from motor_ai_sim import agent_designs as _AD
    return {"designs": [_AD.admin_view(d) for d in _AD.list_all_designs()]}


@router.delete("/agent_designs/{design_id}")
def delete_agent_design(design_id: str, admin_user: dict = Depends(require_admin)):
    """Delete one draft, whichever account owns it."""
    from motor_ai_sim import agent_designs as _AD
    try:
        owner = _AD.admin_delete_design(design_id)
    except _AD.DesignError as e:
        raise HTTPException(status_code=404, detail=str(e))
    _AA.record(_AA.actor_of(admin_user), "agent_design.delete", str(design_id), subject=str(owner), details=None)
    return {"deleted": design_id, "owner": owner}


# ── Sessions + auth events ────────────────────────────────────────────────────
# The forensic side of sign-in.  A session record says WHICH browser holds a
# live token (user agent, ip, first and last seen); the event log says what the
# server decided about every token it was shown, with the reason.  Together they
# answer the question that had no answer before 2026-09-03: was the user signed
# out because something rejected their token, or because that browser profile
# never kept it in the first place?


@router.get("/sessions")
def admin_sessions(email: Optional[str] = None,
                   _admin: dict = Depends(require_admin)):
    """Every session on the deployment, newest first; `?email=` narrows it."""
    _AA.record(_AA.actor_of(_admin), "session.list", str(email or "*"), subject=str(email or ""), details=None)
    from motor_ai_sim import sessions as S
    rows = [S.public(r) for r in S.list_all(email)]
    return {"count": len(rows), "sessions": rows}


@router.post("/sessions/{sid}/revoke")
def admin_revoke_session(sid: str, _admin: dict = Depends(require_admin)):
    """Kill one session immediately — its token stops verifying on the next
    request (reason `revoked`), no waiting for the 30-day expiry."""
    _AA.record(_AA.actor_of(_admin), "session.revoke", str(sid), subject=str(""), details=None)
    from motor_ai_sim import sessions as S
    rec = S.revoke(sid)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"no session '{sid}'")
    S.record_event("revoke", email=rec.get("email", ""), sid=sid,
                   reason="admin", path="/api/admin/sessions/revoke")
    return {"ok": True, "session": S.public(rec)}


@router.post("/users/{email}/revoke_all")
def admin_revoke_all(email: str, _admin: dict = Depends(require_admin)):
    """Sign an account out of everywhere (password reset, lost laptop)."""
    _AA.record(_AA.actor_of(_admin), "session.revoke_all", str(email), subject=str(email), details=None)
    from motor_ai_sim import sessions as S
    n = S.revoke_all(email)
    S.record_event("revoke", email=email, reason="admin_all",
                   path="/api/admin/users/revoke_all", count=n)
    return {"ok": True, "email": email.strip().lower(), "revoked": n}


@router.get("/auth_events")
def admin_auth_events(limit: int = 200, email: Optional[str] = None,
                      _admin: dict = Depends(require_admin_or_token)):
    """The tail of logs/auth_events.jsonl — login / logout / renew / reject /
    store_unavailable / revoke, newest first, each with its reason."""
    _AA.record(_AA.actor_of(_admin), "auth_events.read", str(email or "*"), subject=str(email or ""), details={"limit": limit})
    from motor_ai_sim import sessions as S
    limit = max(1, min(int(limit or 200), 2000))
    ev = S.read_events(limit=limit, email=email or "")
    return {"count": len(ev), "events": ev}


# ── Tickets (support: bugs / feature requests / questions) ────────────────────
_VALID_TICKET_STATUS = ("open", "in_progress", "resolved", "closed")


def _mock_tickets() -> list[dict]:
    now = time.time() * 1000
    # (type, title, description, status, email, days_ago)
    rows = [
        ("bug", "Efficiency map dark above 6000 rpm", "Looks like a render bug, not the battery limit.", "open", "alice.pro@example.com", 0.3),
        ("feature", "Add field-weakening to the speed range", "Want torque beyond base speed (constant-power).", "open", "dmitri.team@example.com", 1.2),
        ("question", "How do I export the efficiency map?", "Is CSV export part of Pro?", "resolved", "erin.free@example.com", 4.0),
        ("bug", "Battery bar caps below the real max voltage", "198-cell LFP reads 723 V but the bar stops earlier.", "in_progress", "carla.team@example.com", 2.1),
        ("feature", "Save more than 3 designs on Free", "", "closed", "frank.free@example.com", 9.0),
    ]
    out = []
    for i, (typ, title, desc, status, email, dago) in enumerate(rows):
        out.append({
            "id": f"mock_t{i:02d}", "uid": f"mock_{i:02d}",
            "type": typ, "title": title, "description": desc,
            "status": status, "email": email, "createdAt": now - dago * _DAY_MS,
        })
    return out


@router.get("/tickets")
def list_tickets(_admin: dict = Depends(require_admin_or_token)):
    """All support tickets across users (bugs / feature requests / questions).
    Read-only - also reachable with the ADMIN_API_TOKEN bearer (nightly agent).

    Tickets are filed through POST /api/support/tickets and kept by
    ``ticket_store``.  The demo set is served only with ADMIN_MOCK_DATA=1.
    (Visitor access requests live in ``/support/requests``.)"""
    _AA.record(_AA.actor_of(_admin), "tickets.read", str("*"), subject=str(""), details=None)
    if _mock_enabled():
        t = _mock_tickets()
        return {"source": "mock", "count": len(t), "tickets": t}
    from motor_ai_sim import ticket_store as _T
    t = _T.list_all()
    return {"source": _TICKETS_SOURCE, "count": len(t), "tickets": t}


@router.post("/tickets/status")
def set_ticket_status(body: dict = Body(default={}), _admin: dict = Depends(require_admin)):
    """Update a ticket's status (open / in_progress / resolved / closed)."""
    _AA.record(_AA.actor_of(_admin), "tickets.status", str(str((body or {}).get("id") or "")), subject=str(""), details={"status": (body or {}).get("status")})
    tid = (body or {}).get("id")
    status = (body or {}).get("status")
    if status not in _VALID_TICKET_STATUS:
        raise HTTPException(status_code=400, detail=f"status must be one of {_VALID_TICKET_STATUS}")
    if not tid:
        raise HTTPException(status_code=400, detail="id is required")
    if _mock_enabled():
        return {"ok": True, "source": "mock", "id": tid, "status": status}
    from motor_ai_sim import ticket_store as _T
    rec = _T.set_status(str(tid), status)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"no ticket '{tid}'")
    return {"ok": True, "source": _TICKETS_SOURCE, "id": tid, "status": status}


@router.get("/support")
def support_config(_admin: dict = Depends(require_admin)):
    """Non-secret status of the AI support assistant: provider, models, key set?
    Returns only masked key hints — never the API key itself."""
    from motor_ai_sim.routes import support as support_mod
    return support_mod.provider_status()


@router.post("/support")
def set_support_config(body: dict = Body(default={}), admin_user: dict = Depends(require_admin)):
    """Save AI support settings (provider, models, keys). Keys are write-only and
    stored server-side (Firestore config/ai) — never returned to the browser."""
    _AA.record(_AA.actor_of(admin_user), "support.config", str("support"), subject=str(""), details={"fields": sorted((body or {}).keys())})
    from motor_ai_sim.routes import support as support_mod
    who = admin_user.get("email") or admin_user.get("uid") or "admin"
    return support_mod.set_overrides(body or {}, who=who)


@router.get("/support/models")
def support_models(_admin: dict = Depends(require_admin)):
    """Available models per provider (live from the provider API, static fallback)."""
    from motor_ai_sim.routes import support as support_mod
    return support_mod.list_models()


# ── The visitor inbox ─────────────────────────────────────────────────────────
# A signed-out visitor talks to the assistant on the landing page.  Until
# 2026-09-17 that conversation ended in the browser and the team never heard it;
# now every anonymous turn is logged and a visitor who gives their contact
# details becomes an ACCESS REQUEST (support_store).  These four routes are the
# Admin tab's side of it, and they are admin-only like everything else here: the
# door (PUBLIC_EXHIBIT) keeps an anonymous caller out with 401, require_admin
# turns a signed-in non-admin away with 403.
#
# NOT `require_admin_or_token`: the static ADMIN_API_TOKEN is for the headless
# read-only tickets agent, and a visitor's conversation is personal data that has
# no business being readable by a shared static string.

_VALID_REQUEST_STATUS = ("new", "contacted", "invited", "declined")


@router.get("/support/requests")
def support_requests(_admin: dict = Depends(require_admin)):
    """Every access request a visitor left with the assistant, newest first."""
    _AA.record(_AA.actor_of(_admin), "support.requests.read", str("*"), subject=str(""), details=None)
    from motor_ai_sim import support_store as S
    rows = S.list_access_requests()
    return {"count": len(rows),
            "new": sum(1 for r in rows if r.get("status") == "new"),
            "requests": rows}


@router.patch("/support/requests/{request_id}")
def set_support_request_status(request_id: str, body: dict = Body(default={}),
                               _admin: dict = Depends(require_admin)):
    """Move one request through new → contacted / invited / declined."""
    _AA.record(_AA.actor_of(_admin), "support.requests.status", str(request_id), subject=str(""), details={"status": (body or {}).get("status")})
    from motor_ai_sim import support_store as S
    status = str((body or {}).get("status") or "").strip().lower()
    if status not in _VALID_REQUEST_STATUS:
        raise HTTPException(
            status_code=422,
            detail=f"status must be one of {_VALID_REQUEST_STATUS}")
    rec = S.set_status(request_id, status)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"no request '{request_id}'")
    return {"ok": True, "request": rec}


@router.delete("/support/requests/{request_id}")
def delete_support_request(request_id: str,
                           _admin: dict = Depends(require_admin)):
    """Drop one request — a test row, or one the team is finished with.

    The visitor CHAT log is not touched: it is the day's record of what was said
    and it ages out on its own (90 days), while the inbox is a worklist.
    """
    _AA.record(_AA.actor_of(_admin), "support.requests.delete", str(request_id), subject=str(""), details=None)
    from motor_ai_sim import support_store as S
    if not S.delete_request(request_id):
        raise HTTPException(status_code=404, detail=f"no request '{request_id}'")
    return {"ok": True, "id": request_id}


@router.get("/support/visitor_chats")
def support_visitor_chats(day: str = "", _admin: dict = Depends(require_admin)):
    """One day of visitor conversations, read-only.  `?day=YYYY-MM-DD`;
    without it, the newest day that has a log."""
    _AA.record(_AA.actor_of(_admin), "support.chats.read", str(day or "latest"), subject=str(""), details=None)
    from motor_ai_sim import support_store as S
    return S.conversations(day)
