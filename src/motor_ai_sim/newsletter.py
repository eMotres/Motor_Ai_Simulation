"""Legacy newsletter store (RETIRED 2026-10-15) + in-app product notices.

The single "Newsletter" opt-in is gone: the Communication preferences page (``comm_prefs.py``) is the only place where a person
chooses what e-mail they get, and the announcement mailing (``mailing.py``) is the only sender.  What stays here:

* the old ``newsletter.json`` records, READ-ONLY history for the Admin "Mailing" card: subscribers with their consent record
  (status, source, wording version, time) and past campaigns.  Nobody is moved into the new topics: the old consent had a
  different wording, so those people choose again (the launch e-mail invites them).
* the one-click unsubscribe of the links and ``List-Unsubscribe`` headers of mails that were ALREADY sent
  (``/api/newsletter/unsubscribe``).  An unsubscribe here also switches every new topic off; ``comm_prefs.unsubscribe_all`` ends
  the legacy record too, so the two never disagree.
* in-app product notices (maintenance, new feature): not marketing, independent of any e-mail consent; each user has read state.

There is no sending, no queue and no subscribe / confirm code any more.  Nothing here logs a token.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

import jwt

from motor_ai_sim import auth_email as E
from motor_ai_sim import users as U
from motor_ai_sim.config import DEFAULT_CONFIG_PATH

log = logging.getLogger(__name__)

_STORE_FILE = Path(DEFAULT_CONFIG_PATH).parent / "newsletter.json"
_LOCK = threading.RLock()

_ISS = "motor-ai-sim-newsletter"

#: the unsubscribe endpoint, per IP
LINK_IP = E.Limiter(max_hits=30, window_s=600.0, lock_s=600.0)



# ── store ────────────────────────────────────────────────────────────────────

def _empty() -> dict:
    return {"subscribers": {}, "campaigns": {}, "send_log": [],
            "notices": {}, "notice_reads": {}}


def _load() -> dict:
    with _LOCK:
        try:
            with open(_STORE_FILE, encoding="utf-8") as f:
                d = json.load(f)
        except FileNotFoundError:
            return _empty()
        base = _empty()
        base.update(d if isinstance(d, dict) else {})
        return base


def _save(d: dict) -> None:
    with _LOCK:
        from motor_ai_sim.private_files import ensure_private_dir, open_private
        ensure_private_dir(_STORE_FILE.parent)
        tmp = _STORE_FILE.with_suffix(".tmp")
        with open_private(tmp, "w") as f:   # 0600 — audit 2026-09-29 #9
            json.dump(d, f, indent=1, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, _STORE_FILE)


def _key(label: bytes) -> bytes:
    return hmac.new(U._secret().encode("utf-8"), label, hashlib.sha256).digest()


# ── tokens (the links of mails that were already sent) ──────────────────────

UNSUB_TTL_S = 400 * 24 * 3600


def _token(email: str, purpose: str, ttl: int, **extra) -> str:
    now = int(time.time())
    return jwt.encode({"sub": email, "purpose": purpose, "iat": now,
                       "exp": now + ttl, "iss": _ISS, **extra},
                      _key(b"newsletter-link-v1"), algorithm="HS256")


def _claims(token: str, purpose: str) -> Optional[dict]:
    try:
        c = jwt.decode(token or "", _key(b"newsletter-link-v1"),
                       algorithms=["HS256"], issuer=_ISS,
                       options={"require": ["exp", "sub"]})
    except Exception:
        return None
    return c if c.get("purpose") == purpose else None


def unsubscribe_token(email: str, campaign: str = "") -> str:
    return _token(U._norm(email), "nl_unsub", UNSUB_TTL_S, c=campaign)


# ── legacy consent record (read-only history + unsubscribe) ───────────────────

def _hist(rec: dict, event: str, source: str, **kw) -> None:
    h = rec.setdefault("history", [])
    h.append({"ts": time.time(), "event": event, "source": source, **kw})
    del h[:-50]


def status(email: str) -> dict:
    rec = _load()["subscribers"].get(U._norm(email)) or {}
    return {"status": rec.get("status") or "none",
            "subscribed": rec.get("status") == "confirmed",
            "confirmed_at": rec.get("confirmed_at")}


def unsubscribe(email: str, *, source: str, campaign: str = "", propagate: bool = True) -> bool:
    """Instant.  Returns True when a subscription/pending consent ended.

    ``propagate``: an unsubscribe is a wish to hear nothing more, so it also switches off every topic of the communication
    preferences (``comm_prefs``); that module passes False when it is the one calling."""
    email = U._norm(email)
    with _LOCK:
        d = _load()
        rec = d["subscribers"].get(email)
        if not rec or rec.get("status") not in ("confirmed", "pending"):
            return False
        rec.pop("nonce", None)
        rec.update(status="unsubscribed", unsubscribed_at=time.time())
        _hist(rec, "unsubscribed", source, campaign=campaign)
        camp = d["campaigns"].get(campaign) if campaign else None
        if camp is not None:
            camp["unsubscribes"] = int(camp.get("unsubscribes") or 0) + 1
        _save(d)
    log.info("newsletter: unsubscribed via %s", source)
    if propagate:
        try:
            from motor_ai_sim import comm_prefs as CP
            CP.unsubscribe_all(email, source="prefs_page" if source == "settings" else "unsubscribe")
        except Exception as e:                               # noqa: BLE001 - the newsletter record is already saved
            log.warning("newsletter: comm_prefs unsubscribe failed: %s", type(e).__name__)
    return True


def unsubscribe_by_token(token: str) -> bool:
    """False only for a bad token; an already-unsubscribed address is True
    (the link is idempotent and says nothing about the address)."""
    c = _claims(token, "nl_unsub")
    if not c:
        return False
    unsubscribe(c.get("sub") or "", source="link", campaign=str(c.get("c") or ""))
    return True


def subscribers(roles: Optional[list[str]] = None,
                only_confirmed: bool = False) -> list[dict]:
    out = []
    for email, rec in sorted(_load()["subscribers"].items()):
        st = rec.get("status") or "none"
        if only_confirmed and st != "confirmed":
            continue
        role = (U.get_user(email) or {}).get("role") or ""
        if roles and role not in roles:
            continue
        out.append({"email": email, "status": st, "role": role,
                    "source": rec.get("source") or "",
                    "text_version": rec.get("text_version") or "",
                    "consent_at": rec.get("consent_at"),
                    "confirmed_at": rec.get("confirmed_at"),
                    "unsubscribed_at": rec.get("unsubscribed_at")})
    return out


def is_confirmed(email: str) -> bool:
    rec = _load()["subscribers"].get(U._norm(email)) or {}
    return rec.get("status") == "confirmed"


# ── past campaigns (read-only history) ───────────────────────────────────────

def _clean_roles(roles) -> list[str]:
    return sorted({str(t) for t in (roles or []) if str(t).strip()})


def public_campaign(rec: dict) -> dict:
    counts = {"pending": 0, "sending": 0, "sent": 0, "failed": 0, "bounced": 0,
              "skipped": 0}
    for r in (rec.get("recipients") or {}).values():
        counts[r.get("status", "pending")] = counts.get(r.get("status", "pending"), 0) + 1
    return {k: rec.get(k) for k in ("id", "subject", "body_md", "roles", "author",
                                    "created", "status", "scheduled_at",
                                    "started_at", "finished_at")} | {
        "stats": {**counts, "total": len(rec.get("recipients") or {}),
                  "unsubscribes": int(rec.get("unsubscribes") or 0)}}


def list_campaigns() -> list[dict]:
    return sorted((public_campaign(c) for c in _load()["campaigns"].values()),
                  key=lambda c: -(c.get("created") or 0))


def get_campaign(cid: str) -> Optional[dict]:
    rec = _load()["campaigns"].get(cid)
    return public_campaign(rec) if rec else None


# ── in-app notices ───────────────────────────────────────────────────────────

LEVELS = ("info", "warning", "important")


def post_notice(title: str, body: str = "", *, level: str = "info",
                emails=None, roles=None, expires_at: Optional[float] = None,
                author: str = "") -> dict:
    title = (title or "").strip()
    if not title or len(title) > 140:
        raise ValueError("title must be 1–140 characters")
    if level not in LEVELS:
        raise ValueError(f"level must be one of {LEVELS}")
    nid = uuid.uuid4().hex[:12]
    rec = {"id": nid, "title": title, "body": (body or "")[:2000], "level": level,
           "emails": sorted({U._norm(e) for e in (emails or []) if e}),
           "roles": _clean_roles(roles), "created": time.time(),
           "expires_at": expires_at, "active": True, "author": author or ""}
    with _LOCK:
        d = _load()
        d["notices"][nid] = rec
        _save(d)
    return rec


def withdraw_notice(nid: str) -> None:
    with _LOCK:
        d = _load()
        if nid not in d["notices"]:
            raise KeyError(nid)
        d["notices"][nid]["active"] = False
        _save(d)


def _targets(n: dict, email: str, role: str) -> bool:
    if n.get("emails") and email not in n["emails"]:
        return False
    if n.get("roles") and role not in n["roles"]:
        return False
    return True


def notices_for(email: str, role: str = "", now: Optional[float] = None) -> list[dict]:
    t = time.time() if now is None else now
    email = U._norm(email)
    d = _load()
    read = set(d["notice_reads"].get(email) or [])
    out = []
    for n in sorted(d["notices"].values(), key=lambda n: -n["created"]):
        if not n.get("active") or (n.get("expires_at") and n["expires_at"] < t):
            continue
        if not _targets(n, email, role):
            continue
        out.append({k: n[k] for k in ("id", "title", "body", "level", "created")}
                   | {"read": n["id"] in read})
    return out


def mark_read(email: str, nid: str) -> bool:
    email = U._norm(email)
    with _LOCK:
        d = _load()
        if nid not in d["notices"]:
            return False
        lst = d["notice_reads"].setdefault(email, [])
        if nid not in lst:
            lst.append(nid)
            _save(d)
    return True


def all_notices() -> list[dict]:
    d = _load()
    reads = d["notice_reads"]
    return [n | {"read_count": sum(1 for v in reads.values() if n["id"] in v)}
            for n in sorted(d["notices"].values(), key=lambda n: -n["created"])]
