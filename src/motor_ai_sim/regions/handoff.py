"""The sign-in hand-off between the two regions (docs/regions/DESIGN.md, sections 5 and 13).

Bearer tokens are per server (own ``.auth_secret``, own sessions) and the browser keeps them per origin, so a person sent
to the other region's server would have to sign in again.  The REFUSING server, once it knows who the caller is, mints a
short single-use ticket; ``GET /region-handoff#t=<ticket>`` on the target (a web page: the fragment never reaches a
server log) posts it to ``POST /api/auth/region-handoff``, which consumes it and opens a local session.

A ticket is minted ONLY after the refusing server has verified the caller itself — never for an e-mail alone:

* ``session``  — a valid bearer of a guarded (migrated) account hit the region guard;
* ``google``   — a verified Google ID token of an account the peer hosts;
* ``password`` — the correct password of the account's not-yet-deleted local copy (after the 421 decision, which comes
  before any password check; a wrong password gives the 421 without a ticket).

Format: ``v1.<base64url(JSON)>.<hex HMAC-SHA256(peer secret, "motres-handoff-v1\\n" + body)>`` with the JSON
``{e: email, to: target region, from: minting region, exp: unix s, n: nonce, m: method, ph: password digest}``.
The domain prefix keeps a ticket from ever being valid as a peer-request signature (``regions/peer.py``) and vice versa.

The target accepts it only in multi-region mode, from the configured peer region, addressed to itself, within its 60 s
life (10 s clock grace), with a nonce never used before (kept in ``<identity>/region_handoff_used.json`` so every API
worker sees it), for a local, enabled, verified, non-staff account whose home region is this server.  ``session`` and
``password`` tickets also need the target's password digest to equal the one in the ticket: a password changed on the
target after the migration makes every ticket minted from the old copy worthless.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict, Optional

from motor_ai_sim.file_lock import store_lock
from motor_ai_sim.regions import config as RC

TTL_S = 60
GRACE_S = 10
PREFIX = "v1"
_DOMAIN = b"motres-handoff-v1\n"
METHODS = ("session", "google", "password")
_MAX_LEN = 2048

_USED_LOCK = store_lock(lambda: used_path())


class HandoffError(Exception):
    """Why a ticket was refused (``reason``); the route answers one generic message whatever it is."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def used_path() -> Path:
    env = (os.environ.get("MOTRES_REGION_HANDOFF_FILE") or "").strip()
    if env:
        return Path(env).expanduser()
    from motor_ai_sim import users as U
    return Path(U._USERS_FILE).parent / "region_handoff_used.json"


def pw_digest(rec: Optional[dict]) -> str:
    """A digest of the stored password hash: equal on both servers right after a migration (the hash is copied), different
    as soon as the password is changed on either side.  Not reversible to anything useful (a hash of an argon2 hash)."""
    r = rec if isinstance(rec, dict) else {}
    raw = "\n".join((str(r.get("pw_algo") or ""), str(r.get("pw_hash") or ""), str(r.get("pw_salt") or "")))
    return hashlib.sha256(b"motres-handoff-pw\n" + raw.encode("utf-8")).hexdigest()[:32]


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sig(secret: str, body: str) -> str:
    return hmac.new(secret.encode("utf-8"), _DOMAIN + body.encode("ascii"), hashlib.sha256).hexdigest()


def mint(email: str, target_region: str, *, method: str, rec: Optional[dict] = None,
         now: Optional[float] = None) -> str:
    """A ticket for ``email`` to sign in on ``target_region`` — ``""`` when no peer is configured, the target is this
    server or the method is unknown (the caller then answers the 421 without a ticket)."""
    target = RC.norm_region(target_region)
    secret = RC.peer_secret()
    if (method not in METHODS or not RC.peer_configured() or target != RC.peer_region()
            or target == RC.local_region() or not secret):
        return ""
    from motor_ai_sim import users as U
    em = U._norm(email)
    if not em or "@" not in em:
        return ""
    t = int(time.time() if now is None else now)
    payload = {"e": em, "to": target, "from": RC.local_region(), "exp": t + TTL_S, "n": secrets.token_urlsafe(18),
               "m": method, "ph": pw_digest(rec if rec is not None else U.get_user(em)) if method != "google" else ""}
    body = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return f"{PREFIX}.{body}.{_sig(secret, body)}"


def _parse(ticket: str) -> Dict[str, Any]:
    if not isinstance(ticket, str) or len(ticket) > _MAX_LEN:
        raise HandoffError("malformed")
    parts = ticket.strip().split(".")
    if len(parts) != 3 or parts[0] != PREFIX:
        raise HandoffError("malformed")
    secret = RC.peer_secret()
    if not secret:
        raise HandoffError("not_configured")
    if not hmac.compare_digest(parts[2], _sig(secret, parts[1])):
        raise HandoffError("bad_signature")
    try:
        doc = json.loads(_unb64(parts[1]).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise HandoffError("malformed")
    if not isinstance(doc, dict):
        raise HandoffError("malformed")
    return doc


def _spend(nonce: str, exp: int, now: float) -> None:
    from motor_ai_sim.private_files import chmod_private, ensure_private_dir, open_private
    p = used_path()
    with _USED_LOCK:
        try:
            used = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            used = {}
        used = {k: v for k, v in (used if isinstance(used, dict) else {}).items()
                if isinstance(v, (int, float)) and v >= now - GRACE_S}
        if nonce in used:
            raise HandoffError("replay")
        used[nonce] = exp
        ensure_private_dir(p.parent)
        tmp = p.with_name(p.name + ".%d.tmp" % os.getpid())
        with open_private(tmp, "w") as f:
            json.dump(used, f)
        os.replace(tmp, p)
        chmod_private(p)


def consume(ticket: str, *, now: Optional[float] = None) -> Dict[str, str]:
    """Check and burn a ticket -> ``{"email", "method"}``; raises :class:`HandoffError` otherwise.

    The ticket is burned only once everything about it checks out, so a refused one never costs the real holder."""
    if not RC.peer_configured():
        raise HandoffError("not_configured")
    doc = _parse(ticket)
    t = time.time() if now is None else now
    if RC.norm_region(doc.get("to")) != RC.local_region():
        raise HandoffError("wrong_target")
    if RC.norm_region(doc.get("from")) != RC.peer_region():
        raise HandoffError("wrong_source")
    try:
        exp = int(doc.get("exp"))
    except (TypeError, ValueError):
        raise HandoffError("malformed")
    if t > exp + GRACE_S or exp > t + TTL_S + GRACE_S:
        raise HandoffError("expired")
    method = str(doc.get("m") or "")
    if method not in METHODS:
        raise HandoffError("malformed")
    nonce = str(doc.get("n") or "")
    if not (16 <= len(nonce) <= 64):
        raise HandoffError("malformed")
    from motor_ai_sim import users as U
    from motor_ai_sim.regions import store as RS
    em = U._norm(str(doc.get("e") or ""))
    rec = U.get_user(em) if em else None
    if not isinstance(rec, dict):
        raise HandoffError("no_account")
    if rec.get("disabled") or not U.is_verified(rec) or RS.is_staff(rec):
        raise HandoffError("not_allowed")
    if RS.effective_region(rec) != RC.local_region():
        raise HandoffError("not_home")
    if method != "google" and not hmac.compare_digest(str(doc.get("ph") or ""), pw_digest(rec)):
        raise HandoffError("password_changed")
    _spend(nonce, exp, t)
    return {"email": em, "method": method}
