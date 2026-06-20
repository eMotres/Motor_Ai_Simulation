"""Admin-only endpoints: user management + usage statistics.

The data source is Firebase Auth (the user list) + Firestore (saved designs)
via the Firebase Admin SDK. On Cloud Run the SDK initialises from Application
Default Credentials — the service account, no key file. Locally there are no
credentials, so the SDK is unavailable and the endpoints serve a small MOCK
dataset flagged `source: "mock"`, letting the admin UI be built and exercised
without production access.

Every route requires tier == admin (require_admin). When AUTH_ENFORCE is off
(local dev) the gate is open and the caller is treated as admin.
"""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException

from motor_ai_sim.auth import require_admin, require_admin_or_token

router = APIRouter(prefix="/api/admin", tags=["admin"])

_VALID_TIERS = ("free", "pro", "team", "admin")
_DAY_MS = 86_400_000.0


# ── Firebase Admin SDK (lazy, optional) ──────────────────────────────────────
_admin_mod = None
_init_done = False
_init_error: Optional[str] = None


def _ensure_admin():
    """Initialise firebase-admin once and return the module, or None if the
    package isn't installed / no credentials are available (then we serve mock)."""
    global _admin_mod, _init_done, _init_error
    if _init_done:
        return _admin_mod
    _init_done = True
    try:
        import firebase_admin
        if not firebase_admin._apps:
            # initialize_app() with no args uses Application Default Credentials
            # (the Cloud Run service account). Raises locally with no creds.
            firebase_admin.initialize_app()
        _admin_mod = firebase_admin
    except Exception as e:  # package missing or no ADC -> mock mode
        _init_error = str(e)
        _admin_mod = None
    return _admin_mod


def _tier_of(user_record) -> str:
    """Mirror auth._tier_for for an Admin-SDK UserRecord: ADMIN_EMAILS wins,
    then a 'tier' custom claim, else 'free'."""
    from motor_ai_sim.auth import _ADMIN_EMAILS, _TIER_RANK
    email = (user_record.email or "").strip().lower()
    if email and email in _ADMIN_EMAILS:
        return "admin"
    claims = user_record.custom_claims or {}
    t = claims.get("tier")
    return t if t in _TIER_RANK else "free"


def _real_users(admin) -> list[dict]:
    from firebase_admin import auth as fb_auth
    out: list[dict] = []
    for u in fb_auth.list_users().iterate_all():
        md = u.user_metadata
        out.append({
            "uid": u.uid,
            "email": u.email,
            "displayName": u.display_name or (u.email.split("@")[0] if u.email else u.uid),
            "createdAt": md.creation_timestamp if md else None,        # ms epoch
            "lastLoginAt": md.last_sign_in_timestamp if md else None,  # ms epoch
            "disabled": bool(u.disabled),
            "tier": _tier_of(u),
        })
    return out


def _design_counts(admin) -> dict:
    """Designs per user from Firestore. Uses a collection-group query over
    'designs' so it counts even when the parent users/{uid} doc doesn't exist
    (subcollection docs don't create ancestor docs)."""
    try:
        from firebase_admin import firestore
        db = firestore.client()
        counts: dict = {}
        for d in db.collection_group("designs").stream():
            parent = d.reference.parent.parent  # users/{uid}
            if parent is None:
                continue
            counts[parent.id] = counts.get(parent.id, 0) + 1
        return counts
    except Exception:
        return {}


def _mock_users() -> list[dict]:
    """Deterministic demo users (timestamps relative to now, so 'active' and the
    signup timeline look live). Served only when the Admin SDK is unavailable."""
    now = time.time() * 1000
    # (email, tier, created_days_ago, last_login_days_ago|None, designs, disabled)
    rows = [
        ("vadim.owner@example.com", "admin", 240, 0, 14, False),
        ("eng.lead@example.com",    "admin", 220, 2, 9, False),
        ("alice.pro@example.com",   "pro",   180, 1, 11, False),
        ("bob.design@example.com",  "pro",   150, 3, 6, False),
        ("carla.team@example.com",  "team",  140, 0, 22, False),
        ("dmitri.team@example.com", "team",  120, 5, 17, False),
        ("erin.free@example.com",   "free",  95, 4, 3, False),
        ("frank.free@example.com",  "free",  80, 12, 1, False),
        ("grace.free@example.com",  "free",  70, 40, 2, False),
        ("hugo.free@example.com",   "free",  55, 65, 0, False),
        ("ivy.pro@example.com",     "pro",   42, 6, 5, False),
        ("jack.free@example.com",   "free",  30, 8, 1, False),
        ("kira.free@example.com",   "free",  18, 2, 0, False),
        ("leo.free@example.com",    "free",  9, 1, 1, False),
        ("mara.free@example.com",   "free",  3, None, 0, False),
        ("spam.bot@example.com",    "free",  60, 58, 0, True),
    ]
    out = []
    for i, (email, tier, cago, lago, designs, disabled) in enumerate(rows):
        out.append({
            "uid": f"mock_{i:02d}",
            "email": email,
            "displayName": email.split("@")[0],
            "createdAt": now - cago * _DAY_MS,
            "lastLoginAt": (now - lago * _DAY_MS) if lago is not None else None,
            "disabled": disabled,
            "tier": tier,
            "designCount": designs,
        })
    return out


def _load_users() -> tuple[str, list[dict]]:
    admin = _ensure_admin()
    if admin is None:
        return "mock", _mock_users()
    users = _real_users(admin)
    counts = _design_counts(admin)
    for u in users:
        u["designCount"] = counts.get(u["uid"], 0)
    return "firebase", users


def _day(ms: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


def _compute_stats(users: list[dict]) -> dict:
    now = time.time() * 1000
    by_tier: dict = {}
    active7 = active30 = 0
    buckets: dict = {}
    for u in users:
        t = u.get("tier") or "free"
        by_tier[t] = by_tier.get(t, 0) + 1
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
        "byTier": by_tier,
        "active7": active7,
        "active30": active30,
        "signups": signups,
    }


@router.get("/users")
def list_users(_admin: dict = Depends(require_admin)):
    """All users with tier, sign-up / last-login times, and saved-design count."""
    source, users = _load_users()
    users.sort(key=lambda u: u.get("createdAt") or 0, reverse=True)
    return {"source": source, "count": len(users), "users": users}


@router.get("/stats")
def stats(_admin: dict = Depends(require_admin)):
    """Aggregate usage: totals, tier split, active 7/30d, signup timeline."""
    source, users = _load_users()
    return {"source": source, **_compute_stats(users)}


@router.post("/users/{uid}/tier")
def set_tier(uid: str, body: dict = Body(default={}), _admin: dict = Depends(require_admin)):
    """Set a user's plan via a Firebase custom claim ('tier')."""
    tier = (body or {}).get("tier")
    if tier not in _VALID_TIERS:
        raise HTTPException(status_code=400, detail=f"tier must be one of {_VALID_TIERS}")
    admin = _ensure_admin()
    if admin is None:
        return {"ok": True, "source": "mock", "uid": uid, "tier": tier}
    from firebase_admin import auth as fb_auth
    current = fb_auth.get_user(uid).custom_claims or {}
    fb_auth.set_custom_user_claims(uid, {**current, "tier": tier})
    return {"ok": True, "source": "firebase", "uid": uid, "tier": tier}


@router.post("/users/{uid}/disable")
def set_disabled(uid: str, body: dict = Body(default={}), _admin: dict = Depends(require_admin)):
    """Disable or re-enable a user's account."""
    disabled = bool((body or {}).get("disabled", True))
    admin = _ensure_admin()
    if admin is None:
        return {"ok": True, "source": "mock", "uid": uid, "disabled": disabled}
    from firebase_admin import auth as fb_auth
    fb_auth.update_user(uid, disabled=disabled)
    return {"ok": True, "source": "firebase", "uid": uid, "disabled": disabled}


# ── Tickets (support: bugs / feature requests / questions) ────────────────────
_VALID_TICKET_STATUS = ("open", "in_progress", "resolved", "closed")


def _ms(v):
    """Best-effort convert a Firestore timestamp / number to epoch ms."""
    try:
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        return float(v.timestamp()) * 1000.0  # Firestore DatetimeWithNanoseconds
    except Exception:
        return None


def _ticket_view(d: dict) -> dict:
    return {
        "type": d.get("type") or "question",
        "title": d.get("title") or "(no title)",
        "description": d.get("description") or "",
        "status": d.get("status") or "open",
        "email": d.get("email"),
        "createdAt": _ms(d.get("createdAt")),
    }


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
    Read-only — also reachable with the ADMIN_API_TOKEN bearer (nightly agent)."""
    admin = _ensure_admin()
    if admin is None:
        t = _mock_tickets()
        return {"source": "mock", "count": len(t), "tickets": t}
    from firebase_admin import firestore
    db = firestore.client()
    out = []
    for d in db.collection_group("tickets").stream():
        parent = d.reference.parent.parent  # users/{uid}
        out.append({"id": d.id, "uid": parent.id if parent else None, **_ticket_view(d.to_dict() or {})})
    out.sort(key=lambda x: x.get("createdAt") or 0, reverse=True)
    return {"source": "firebase", "count": len(out), "tickets": out}


@router.post("/tickets/status")
def set_ticket_status(body: dict = Body(default={}), _admin: dict = Depends(require_admin)):
    """Update a ticket's status (open / in_progress / resolved / closed)."""
    uid = (body or {}).get("uid")
    tid = (body or {}).get("id")
    status = (body or {}).get("status")
    if status not in _VALID_TICKET_STATUS:
        raise HTTPException(status_code=400, detail=f"status must be one of {_VALID_TICKET_STATUS}")
    if not uid or not tid:
        raise HTTPException(status_code=400, detail="uid and id are required")
    admin = _ensure_admin()
    if admin is None:
        return {"ok": True, "source": "mock", "id": tid, "status": status}
    from firebase_admin import firestore
    db = firestore.client()
    db.collection("users").document(uid).collection("tickets").document(tid).update({"status": status})
    return {"ok": True, "source": "firebase", "id": tid, "status": status}


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
    from motor_ai_sim.routes import support as support_mod
    who = admin_user.get("email") or admin_user.get("uid") or "admin"
    return support_mod.set_overrides(body or {}, who=who)


@router.get("/support/models")
def support_models(_admin: dict = Depends(require_admin)):
    """Available models per provider (live from the provider API, static fallback)."""
    from motor_ai_sim.routes import support as support_mod
    return support_mod.list_models()
