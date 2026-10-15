"""The verification flow: open a case from a signed NDA, run the checks, decide, write to the applicant, take the applicant's
answers, take the administrator's actions, and answer the one question the rest of the app asks - may this account use the
confidential 3-D files?  (:func:`access_ok`)

Everything that happens is an event in :mod:`motor_ai_sim.verification.store`; this module never keeps state of its own besides
a small in-memory cache of registry answers per case and a lock per case.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Optional

from motor_ai_sim.verification import config as C
from motor_ai_sim.verification import engine as ENG
from motor_ai_sim.verification import store as ST
from motor_ai_sim.verification import textutil as T
from motor_ai_sim.verification import texts as TX
from motor_ai_sim.verification.sources import Context, run_all

log = logging.getLogger(__name__)

#: run the checks in a daemon thread (the sign request must not wait for registries).  Tests set this False.
BACKGROUND = True
_CASE_LOCKS: dict[str, threading.Lock] = {}
_CACHE: dict[str, dict] = {}
_GUARD = threading.Lock()

OPEN_STATUSES = (ENG.PENDING, ENG.MORE_INFORMATION_REQUIRED, ENG.MANUAL_REVIEW)


def _lock_for(cid: str) -> threading.Lock:
    with _GUARD:
        return _CASE_LOCKS.setdefault(cid, threading.Lock())


# -- tokens (the personal link of the reply page) ----------------------------------------------------------------------

def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _key() -> bytes:
    secret = (os.environ.get("VERIFICATION_SECRET") or "").strip()
    if not secret:
        from motor_ai_sim import users as U
        secret = U._secret()
    return hmac.new(secret.encode("utf-8"), b"verification-reply-v1", hashlib.sha256).digest()


def make_token(case_id: str, ttl: Optional[int] = None, now: Optional[float] = None) -> str:
    """``<b64(json{c,e})>.<b64(HMAC-SHA256)>``: names the case and an expiry.  Whoever holds the link can answer for THIS case."""
    t = int(time.time() if now is None else now)
    payload = _b64(json.dumps({"c": case_id, "e": t + int(ttl or C.REPLY_TOKEN_TTL_S)}, separators=(",", ":"), sort_keys=True).encode())
    return payload + "." + _b64(hmac.new(_key(), payload.encode("ascii"), hashlib.sha256).digest())


def case_for_token(token: str, now: Optional[float] = None) -> Optional[str]:
    try:
        payload, sig = str(token or "").split(".", 1)
        if not hmac.compare_digest(_b64(hmac.new(_key(), payload.encode("ascii"), hashlib.sha256).digest()), sig):
            return None
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if float(claims["e"]) < (time.time() if now is None else now) or not ST.valid_case_id(claims.get("c")):
            return None
        return claims["c"]
    except Exception:                                        # noqa: BLE001 - any malformed token is simply invalid
        return None


def reply_url(case_id: str) -> str:
    from motor_ai_sim import auth_email as E
    return "%s/api/verification/reply/%s" % (E.app_url(), make_token(case_id))


# -- intake ----------------------------------------------------------------------------------------------------------

def validate_intake(body: dict, account_email: str = "") -> tuple[dict, dict]:
    """-> (clean fields, errors).  Everything is optional here (a gap is a question to the applicant, not a refusal), but what is
    given must be well-formed."""
    f, err = {}, {}
    raw_site = str((body or {}).get("website") or "").strip()
    if raw_site:
        site = T.normalize_website(raw_site)
        if site:
            f["website"] = site
        else:
            err["website"] = "invalid"
    purpose = re.sub(r"\s+", " ", str((body or {}).get("purpose") or "")).strip()
    if purpose:
        f["purpose"] = purpose[:1000]
    vat = re.sub(r"[^A-Za-z0-9 .\-]", "", str((body or {}).get("vat_no") or "")).strip()
    if vat:
        f["vat_no"] = vat[:30]
    we = str((body or {}).get("work_email") or "").strip().lower()
    if we and not T.valid_email(we):
        err["work_email"] = "invalid"
    f["work_email"] = we if we and T.valid_email(we) else str(account_email or "").lower()
    return f, err


def application_from_nda(rec: dict, intake: dict) -> dict:
    app = {"legal_name": rec.get("company", ""), "country": rec.get("country", ""), "reg_no": rec.get("reg_no", ""), "address": rec.get("address", ""),
           "applicant_name": rec.get("signatory", ""), "title": rec.get("title", ""), "website": "", "purpose": "", "vat_no": "",
           "work_email": str(rec.get("email") or "").lower()}
    app.update({k: v for k, v in intake.items() if v})
    return app


# -- the case view the sources read ------------------------------------------------------------------------------------

def _account_verified(user: str) -> bool:
    from motor_ai_sim import users as U
    rec = U.get_user(user)
    return bool(rec) and U.is_verified(rec)


def case_view(case: dict) -> dict:
    emails = [{"address": case["user"], "verified": _account_verified(case["user"]), "method": "account"}]
    seen = {case["user"]}
    for v in case["emails"]["verified"]:
        if v["email"] not in seen:
            emails.append({"address": v["email"], "verified": True, "method": v.get("method", "otp")})
            seen.add(v["email"])
    we = (case["application"].get("work_email") or "").lower()
    if we and we not in seen:
        emails.append({"address": we, "verified": False, "method": "claimed"})
    return {"id": case["id"], "user": case["user"], "application": dict(case["application"]), "emails": emails, "urls": list(case["urls"]),
            "notes": [n["note"] for n in case["notes"]], "documents": list(case["documents"])}


# -- mail ------------------------------------------------------------------------------------------------------------

def _zh(case: dict) -> bool:
    from motor_ai_sim import auth_email as E
    return E.wants_zh(case.get("lang") or "", case.get("user") or "")


def _send(case: dict, subject: str, body: str, codes=(), *, to: str = "", actor: str = "system", store_body: bool = True) -> bool:
    from motor_ai_sim import auth_email as E
    to = to or case["user"]
    try:
        delivered = E.send(to, subject, body, kind="verification")
    except Exception as e:                                   # noqa: BLE001 - mail trouble never blocks the flow
        log.warning("verification: mail to %s failed (%s)", to, type(e).__name__)
        delivered = False
    ST.append(case["id"], "mail_out", {"to": to, "subject": subject if store_body else "[one-time code mail]",
                                       "body": body if store_body else "[one-time code - not stored]",
                                       "codes": list(codes), "delivered": bool(delivered)}, actor=actor)
    return bool(delivered)


def _notify_admin(case: dict) -> None:
    from motor_ai_sim import auth_email as E
    from motor_ai_sim import nda as N
    to = N.admin_address()
    if not to or not E.smtp_configured():
        return
    subject, body = TX.admin_mail(case, E.app_url() + "/?admin=verification")
    try:
        E.send(to, subject, body, kind="verification-admin")
    except Exception:                                        # noqa: BLE001
        pass


def _notify(before: str, case: dict, decision: ENG.Decision, trigger: str) -> None:
    status = case["status"]
    zh = _zh(case)
    url = reply_url(case["id"])
    if status == ENG.VERIFIED:
        s, b = TX.verified_mail(zh)
        _send(case, s, b)
    elif status == ENG.MORE_INFORMATION_REQUIRED and decision.requests:
        codes = [r["code"] for r in decision.requests]
        last = next((c for c in reversed(case["correspondence"]) if c.get("dir") == "out" and c.get("codes")), None)
        if last and sorted(last["codes"]) == sorted(codes) and case.get("last_mail_ts", 0) > time.time() - 24 * 3600:
            return
        s, b = TX.need_info_mail(decision.requests, url, zh, name=(case["application"].get("applicant_name") or "").split(" ")[0])
        _send(case, s, b, codes)
    elif status == ENG.MANUAL_REVIEW and before != ENG.MANUAL_REVIEW:
        s, b = TX.review_mail(decision.requests, url, zh)
        _send(case, s, b, [r["code"] for r in decision.requests])
        _notify_admin(case)


# -- the checks ------------------------------------------------------------------------------------------------------

def _prior(case: dict) -> dict:
    return {"open_replies": case.get("open_replies", 0), "requested": list(case.get("requested") or []),
            "reply_since_request": bool(case.get("reply_since_request"))}


def run_checks(case_id: str, *, trigger: str = "system", actor: str = "system", notify: bool = True) -> Optional[dict]:
    """Run every applicable source, store the evidence, decide, move the status, write to the applicant.  Any exception ends in
    MANUAL_REVIEW (the case is never lost and never auto-approved by a failure)."""
    with _lock_for(case_id):
        case = ST.get(case_id)
        if case is None:
            return None
        try:
            ev = run_all(case_view(case), Context(cache=_CACHE.setdefault(case_id, {}), ttl=C.CACHE_S))
            decision = ENG.decide(case["application"], ev, _prior(case))
            ST.append(case_id, "check_run", {"trigger": trigger, "evidence": [e.to_dict() for e in ev], "decision": decision.to_dict()}, actor=actor)
        except Exception as e:                                           # noqa: BLE001
            log.exception("verification: run of %s failed", case_id)
            decision = ENG.Decision(ENG.MANUAL_REVIEW, [{"code": "exception", "text": "the checks failed with %s: %s" % (type(e).__name__, str(e)[:200])}])
            try:
                ST.append(case_id, "check_run", {"trigger": trigger, "evidence": [], "decision": decision.to_dict(), "error": str(e)[:300]}, actor=actor)
            except OSError:
                pass
        return _transition(case_id, decision, trigger, notify)


def _transition(case_id: str, decision: ENG.Decision, trigger: str, notify: bool) -> dict:
    case = ST.get(case_id)
    before = case["status"]
    sticky = before == ENG.DENIED or (before == ENG.VERIFIED and (case["status_actor"].startswith("admin:") or trigger != "admin_rerun"))
    if sticky:
        return case                                  # an administrator's decision, or a verified case: only an administrator changes it
    same = (before == decision.status and [r["code"] for r in case.get("reasons") or []] == [r["code"] for r in decision.reasons]
            and [r["code"] for r in case.get("requests") or []] == [r["code"] for r in decision.requests])
    if not same:
        reason = "; ".join(r["text"] for r in decision.reasons)[:600]
        ST.append(case_id, "status_changed", {"status": decision.status, "reason": reason, "reasons": decision.reasons, "requests": decision.requests})
        if decision.status == ENG.VERIFIED:
            ST.append(case_id, "access_granted", {"reason": reason})
        elif before == ENG.VERIFIED and case["access"]["granted"]:
            ST.append(case_id, "access_revoked", {"reason": reason})
    case = ST.get(case_id)
    if notify and trigger != "lazy" and (not same or decision.status == ENG.MORE_INFORMATION_REQUIRED):
        try:
            _notify(before, case, decision, trigger)
        except Exception:                                            # noqa: BLE001
            log.exception("verification: notification of %s failed", case_id)
        case = ST.get(case_id)
    return case


def schedule_checks(case_id: str, *, trigger: str = "system", actor: str = "system", notify: bool = True) -> None:
    if not BACKGROUND:
        run_checks(case_id, trigger=trigger, actor=actor, notify=notify)
        return
    threading.Thread(target=run_checks, kwargs=dict(case_id=case_id, trigger=trigger, actor=actor, notify=notify), daemon=True,
                     name="verification-" + case_id).start()


# -- opening a case ----------------------------------------------------------------------------------------------------

def start_case(user: str, application: dict, *, nda: dict, lang: str = "en", notify: bool = True, trigger: str = "nda_signed") -> dict:
    user = str(user).strip().lower()
    cid = ST.new_case_id()
    ST.append(cid, "case_created", {"user": user, "application": application, "lang": lang or "en",
                                     "nda": {k: nda.get(k) for k in ("id", "version", "text_sha256", "iso") if nda.get(k)}})
    schedule_checks(cid, trigger=trigger, notify=notify)
    return ST.get(cid)


def on_nda_signed(user: str, rec: dict, body: dict, lang: str = "en") -> dict:
    """Called by the NDA route right after a signature was stored.  Never raises into the signature (the caller wraps it)."""
    intake, _errors = validate_intake(body, user)
    return start_case(user, application_from_nda(rec, intake), nda=rec, lang=lang)


def ensure_case(user: str) -> Optional[dict]:
    """The governing case of an account that signed the NDA online before this system existed (or whose case was lost): opens one
    from the stored NDA record - silently, no mail (``lazy``) - so the account sees its status and a link to complete it."""
    from motor_ai_sim import nda as N
    from motor_ai_sim import users as U
    user = str(user).strip().lower()
    c = ST.latest_of(user)
    if c:
        return c
    nda = U.get_nda(user)
    if not (nda.get("signed") and nda.get("by") == "online" and nda.get("record_id")):
        return None
    rec = N.get_record(nda["record_id"])
    if not rec:
        return None
    return start_case(user, application_from_nda(rec, {}), nda=rec, notify=False, trigger="lazy")


def update_details(case_id: str, fields: dict, *, actor: str = "applicant", trigger: str = "details") -> Optional[dict]:
    allowed = {k: v for k, v in fields.items() if k in ("website", "purpose", "vat_no", "work_email", "reg_no") and isinstance(v, str)}
    if not allowed:
        return ST.get(case_id)
    ST.append(case_id, "application_updated", {"fields": allowed}, actor=actor)
    schedule_checks(case_id, trigger=trigger)
    return ST.get(case_id)


# -- the applicant answers -----------------------------------------------------------------------------------------------

_URL_RE = re.compile(r"https?://[^\s<>\"']{4,300}", re.I)


def parse_urls(text: str) -> list[str]:
    """Web addresses in free text (one per line, or separated by spaces / commas): ``https://...``, ``www....``, ``linkedin.com/...``."""
    out = []
    for tok in re.split(r"[\s,;]+", str(text or "")):
        tok = tok.strip().rstrip(".)>]")
        if not tok:
            continue
        if re.match(r"^https?://", tok, re.I):
            u = T.normalize_website(tok)
        elif re.match(r"^(www\.|([a-z]{2,3}\.)?linkedin\.com/)", tok, re.I):
            u = T.normalize_website(tok)
        else:
            continue
        if u and u not in out:
            out.append(u)
    return out[: C.MAX_URLS_PER_REPLY]


def reply(case_id: str, *, urls_text: str = "", note: str = "", fields: Optional[dict] = None) -> Optional[dict]:
    """The reply form: links, a note, corrections of the company details.  Every reply re-runs the checks."""
    urls = parse_urls(urls_text)
    note = re.sub(r"[ \t]+", " ", str(note or "")).strip()[: C.MAX_NOTE_CHARS]
    clean = {}
    for k, v in (fields or {}).items():
        v = str(v or "").strip()
        if not v:
            continue
        if k == "website":
            site = T.normalize_website(v)
            if site:
                clean[k] = site
        elif k in ("reg_no", "vat_no"):
            clean[k] = re.sub(r"[^A-Za-z0-9 .\-/]", "", v)[:40]
    if not (urls or note or clean):
        return ST.get(case_id)
    if clean:
        ST.append(case_id, "application_updated", {"fields": clean}, actor="applicant")
    ST.append(case_id, "reply_in", {"urls": urls, "note": note, "fields": clean}, actor="applicant")
    schedule_checks(case_id, trigger="reply")
    return ST.get(case_id)


def _otp_hash(case_id: str, address: str, code: str) -> str:
    return hmac.new(_key(), ("otp|%s|%s|%s" % (case_id, address, code)).encode("utf-8"), hashlib.sha256).hexdigest()


def start_email(case_id: str, address: str) -> tuple[bool, str]:
    """Send a six-digit code to a corporate address.  -> (ok, code): codes ``sent`` | ``invalid`` | ``free`` | ``no_domain`` | ``limit``."""
    from motor_ai_sim import email_domain as ED
    case = ST.get(case_id)
    address = str(address or "").strip().lower()
    if case is None or not T.valid_email(address):
        return False, "invalid"
    free, disposable = T.is_free_or_disposable(address)
    if free or disposable:
        return False, "free"
    if not ED.check(address).ok:
        return False, "no_domain"
    day = time.time() - 24 * 3600
    sends = sum(1 for c in case["correspondence"] if c.get("dir") == "out" and c.get("ts", 0) > day and c.get("body", "").startswith("[one-time"))
    if sends >= C.OTP_MAX_SENDS_PER_CASE_DAY:
        return False, "limit"
    code = "%06d" % secrets.randbelow(10 ** 6)
    ST.append(case_id, "email_otp_sent", {"email": address, "hash": _otp_hash(case_id, address, code), "expires": time.time() + C.OTP_TTL_S}, actor="applicant")
    s, b = TX.otp_mail(code, _zh(case))
    _send(case, s, b, to=address, store_body=False)
    return True, "sent"


def confirm_email(case_id: str, address: str, code: str) -> tuple[bool, str]:
    """-> (ok, code): ``verified`` | ``wrong`` | ``expired`` | ``locked`` | ``none``."""
    case = ST.get(case_id)
    address = str(address or "").strip().lower()
    p = (case or {}).get("emails", {}).get("pending", {}).get(address)
    if not p:
        return False, "none"
    if p["attempts"] >= C.OTP_MAX_ATTEMPTS:
        return False, "locked"
    if p["expires"] < time.time():
        return False, "expired"
    if not hmac.compare_digest(p["hash"], _otp_hash(case_id, address, re.sub(r"\D", "", str(code or "")))):
        ST.append(case_id, "email_otp_failed", {"email": address}, actor="applicant")
        return False, "wrong"
    ST.append(case_id, "email_verified", {"email": address, "method": "otp"}, actor="applicant")
    schedule_checks(case_id, trigger="email_verified")
    return True, "verified"


def add_document(case_id: str, filename: str, data: bytes) -> tuple[bool, str]:
    """Store ONE uploaded document privately.  -> (ok, code): ``stored`` | ``type`` | ``size`` | ``empty`` | ``limit``.  The file is
    never executed, never parsed here and never served inline (admins download it as an attachment)."""
    from motor_ai_sim.private_files import chmod_private, ensure_private_dir
    case = ST.get(case_id)
    if case is None:
        return False, "type"
    if not data:
        return False, "empty"
    if len(data) > C.UPLOAD_MAX_BYTES:
        return False, "size"
    if len(case["documents"]) >= C.MAX_DOCUMENTS_PER_CASE:
        return False, "limit"
    ext = str(filename or "").rsplit(".", 1)[-1].lower() if "." in str(filename or "") else ""
    magic = C.UPLOAD_EXTENSIONS.get(ext)
    if not magic or not data.startswith(magic):
        return False, "type"
    did = "d" + secrets.token_hex(8)
    d = ensure_private_dir(ST.documents_dir() / case_id)
    p = d / ("%s.%s" % (did, "jpg" if ext == "jpeg" else ext))
    p.write_bytes(data)
    chmod_private(p)
    safe = re.sub(r"[^\w.\- ]+", "_", str(filename))[:120]
    ST.append(case_id, "document_uploaded", {"id": did, "name": safe, "ext": "jpg" if ext == "jpeg" else ext, "bytes": len(data),
                                             "sha256": hashlib.sha256(data).hexdigest()}, actor="applicant")
    schedule_checks(case_id, trigger="document")
    return True, "stored"


def document_path(case_id: str, doc_id: str) -> Optional[tuple[Path, dict]]:
    case = ST.get(case_id)
    d = next((x for x in (case or {}).get("documents", []) if x.get("id") == doc_id), None)
    if not d:
        return None
    p = ST.documents_dir() / case_id / ("%s.%s" % (d["id"], d["ext"]))
    return (p, d) if p.is_file() else None


# -- administrator actions -----------------------------------------------------------------------------------------------

class ActionError(ValueError):
    pass


def _admin_actor(admin_email: str) -> str:
    return "admin:" + str(admin_email or "local-dev").strip().lower()


def _need_reason(reason: str) -> str:
    r = re.sub(r"\s+", " ", str(reason or "")).strip()
    if len(r) < 3:
        raise ActionError("a reason is required")
    return r[:600]


def admin_approve(case_id: str, admin_email: str, reason: str) -> dict:
    reason = _need_reason(reason)
    actor = _admin_actor(admin_email)
    with _lock_for(case_id):
        case = ST.get(case_id)
        if case is None:
            raise ActionError("no such case")
        ST.append(case_id, "admin_action", {"action": "approve", "reason": reason}, actor=actor)
        ST.append(case_id, "status_changed", {"status": ENG.VERIFIED, "reason": "approved by an administrator: " + reason,
                                              "reasons": [{"code": "admin_approved", "text": reason}], "requests": []}, actor=actor)
        ST.append(case_id, "access_granted", {"reason": reason}, actor=actor)
        case = ST.get(case_id)
    s, b = TX.verified_mail(_zh(case))
    _send(case, s, b, actor=actor)
    return ST.get(case_id)


def admin_deny(case_id: str, admin_email: str, reason: str) -> dict:
    reason = _need_reason(reason)
    actor = _admin_actor(admin_email)
    with _lock_for(case_id):
        case = ST.get(case_id)
        if case is None:
            raise ActionError("no such case")
        ST.append(case_id, "admin_action", {"action": "deny", "reason": reason}, actor=actor)
        ST.append(case_id, "status_changed", {"status": ENG.DENIED, "reason": "denied by an administrator: " + reason,
                                              "reasons": [{"code": "admin_denied", "text": reason}], "requests": []}, actor=actor)
        if case["access"]["granted"]:
            ST.append(case_id, "access_revoked", {"reason": reason}, actor=actor)
        case = ST.get(case_id)
    s, b = TX.denied_mail("", _zh(case))
    _send(case, s, b, actor=actor)
    return ST.get(case_id)


def admin_request_info(case_id: str, admin_email: str, text: str) -> dict:
    text = _need_reason(text)
    actor = _admin_actor(admin_email)
    with _lock_for(case_id):
        case = ST.get(case_id)
        if case is None:
            raise ActionError("no such case")
        if case["status"] == ENG.DENIED:
            raise ActionError("the case is denied; approve it first or open a new case")
        reqs = [{"code": ENG.REQ_FREE_TEXT, "params": {"text": text}}]
        ST.append(case_id, "admin_action", {"action": "request_info", "reason": text}, actor=actor)
        ST.append(case_id, "status_changed", {"status": ENG.MORE_INFORMATION_REQUIRED, "reason": "an administrator asked: " + text,
                                              "reasons": [{"code": "admin_request", "text": text}], "requests": reqs}, actor=actor)
        case = ST.get(case_id)
    s, b = TX.need_info_mail(reqs, reply_url(case_id), _zh(case), name=(case["application"].get("applicant_name") or "").split(" ")[0])
    _send(case, s, b, [ENG.REQ_FREE_TEXT], actor=actor)
    return ST.get(case_id)


def admin_rerun(case_id: str, admin_email: str) -> dict:
    actor = _admin_actor(admin_email)
    if ST.get(case_id) is None:
        raise ActionError("no such case")
    ST.append(case_id, "admin_action", {"action": "rerun", "reason": "checks re-run"}, actor=actor)
    _CACHE.pop(case_id, None)
    run_checks(case_id, trigger="admin_rerun", actor=actor)
    return ST.get(case_id)


# -- the gate --------------------------------------------------------------------------------------------------------

def governing_case(user: str) -> Optional[dict]:
    """The case that decides access: the newest VERIFIED / DENIED one (a newer case that is still open does not take away an earlier
    decision), else the newest open case."""
    cases = sorted(ST.cases_of(user), key=lambda c: -c.get("created", 0))
    for c in cases:
        if c["status"] in (ENG.VERIFIED, ENG.DENIED):
            return c
    return cases[0] if cases else None


def is_verified(user: str) -> bool:
    c = governing_case(user)
    return bool(c) and c["status"] == ENG.VERIFIED and c["access"]["granted"]


def _nda_of(user_rec: Optional[dict]) -> dict:
    nda = (user_rec or {}).get("nda")
    return nda if isinstance(nda, dict) else {}


def access_ok(identity: dict, user_rec: Optional[dict]) -> bool:
    """May this caller use the confidential 3-D tools and downloads?  The owner / admin: yes.  An account flagged "NDA signed" by an
    administrator (Admin -> Users): yes - the administrator vouched for it.  An account that signed the NDA ONLINE: only with a
    VERIFIED case (the signature alone is no longer enough)."""
    if identity.get("is_admin"):
        return True
    nda = _nda_of(user_rec)
    if not nda.get("signed"):
        return False
    if nda.get("by") != "online":
        return True
    try:
        return is_verified(identity.get("id") or "")
    except OSError:
        log.error("verification: store unreadable - access closed")
        return False


def public_state(case: Optional[dict]) -> str:
    if case is None:
        return "need_info"
    return {ENG.VERIFIED: "ok", ENG.MORE_INFORMATION_REQUIRED: "need_info", ENG.DENIED: "denied"}.get(case["status"], "in_progress")


def public_access(identity: dict, user_rec: Optional[dict], *, create: bool = True) -> dict:
    """What the web shows about access: ``{state, nda_signed, case, reply_url, requests}`` with ``state`` one of ``ok`` (open),
    ``nda`` (no NDA yet), ``in_progress`` (checks or a manual review under way), ``need_info`` (we wait for the applicant),
    ``denied``."""
    if identity.get("is_admin"):
        return {"state": "ok", "nda_signed": True}
    nda = _nda_of(user_rec)
    if not nda.get("signed"):
        return {"state": "nda", "nda_signed": False}
    if nda.get("by") != "online":
        return {"state": "ok", "nda_signed": True}
    uid = identity.get("id") or ""
    try:
        case = governing_case(uid) or (ensure_case(uid) if create else None)
    except OSError:
        return {"state": "in_progress", "nda_signed": True}
    if case and case["status"] == ENG.PENDING and time.time() - case.get("updated", 0) > 600 and not _lock_for(case["id"]).locked():
        schedule_checks(case["id"], trigger="stale")          # a run that never finished (the server restarted): start it again
        case = ST.get(case["id"]) or case
    st = public_state(case)
    out = {"state": st, "nda_signed": True, "case": case["id"] if case else None}
    if case and st in ("need_info", "in_progress", "denied"):
        out["reply_url"] = reply_url(case["id"]) if st != "denied" else ""
        out["requests"] = [r["code"] for r in case.get("requests") or []]
    if case is None:
        out["requests"] = [ENG.REQ_WEBSITE]
    return out


def basis_of(user: str) -> dict:
    """What the download log records about the verification behind a download."""
    c = governing_case(user)
    if not c:
        return {}
    return {"case": c["id"], "status": c["status"], "decided_by": c["status_actor"], "decided_at": c["status_at"]}


def note_download(row: dict) -> None:
    """Hook of the download log: put the delivery into the case's own timeline (the log file stays the master record)."""
    user = str(row.get("user") or "").lower()
    c = governing_case(user) if user else None
    if not c:
        return
    ST.append(c["id"], "download", {k: row.get(k) for k in ("download_id", "file", "motor_id", "sha256", "kind", "die", "config")}, actor="system")
