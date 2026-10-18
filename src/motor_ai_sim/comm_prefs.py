"""Communication preferences: the per-topic opt-in consent behind every marketing e-mail.

GDPR / ePrivacy shape (MOTRES d.o.o. is an EU controller):

* An e-mail cannot itself collect valid consent with checkboxes.  The one-time launch announcement is a SERVICE message to
  account holders (they registered for this service); it links to the Communication preferences page, where the user ticks
  opt-in boxes.  Every box is UNTICKED by default.  Marketing mail (product updates, new motors, engineering content,
  co-development invitations) goes only to accounts that ticked the matching topic.
* Every change is recorded: topic, value, server timestamp, the id and SHA-256 of the consent wording that was shown (in the
  language it was shown in), the source and the client IP.  The history is APPEND-ONLY; the current state is a second file that
  can always be rebuilt from it.
* Every message carries a one-click unsubscribe (link plus ``List-Unsubscribe`` / ``List-Unsubscribe-Post`` headers).
  "Unsubscribe from all" switches every topic off and sets ``unsubscribed_all``; the launch mailing tool skips such accounts.
  Ticking a topic later is a fresh consent and clears the flag.

Where things live (``<identity root>`` = the directory of the process config, ``/srv/motres/identity`` on the server, the
persistent mount; the same root as ``users.json``):

    <identity root>/comm_prefs.json              current state per account (0600)
    <identity root>/comm_prefs_history.jsonl     append-only consent history (0600)

E-mail links carry a signed token, not an address: ``<b64url(json{u,p,e})>.<b64url(HMAC-SHA256)>`` where ``u`` is a
pseudonymous account id (an HMAC of the address, so the e-mail never appears in URLs, referrers or proxy logs), ``p`` the purpose
(``prefs``) and ``e`` the expiry (90 days).  The key is derived from ``COMM_PREFS_SECRET`` when set, otherwise from the
application's session secret (``users._secret``); no secret is ever committed.  Whoever holds the link can manage THIS account's
four preferences and unsubscribe, nothing else.

The wording shown to the user lives in ``web/src/locales/*/common.json`` (``commPrefs.topics``); the copy below is what the server
hashes.  ``tests/test_comm_prefs.py`` fails when the two drift apart, so the hash always names the text on screen.

Nothing here logs a token.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from motor_ai_sim.config import DEFAULT_CONFIG_PATH
from motor_ai_sim.file_lock import store_lock

log = logging.getLogger(__name__)

TOPICS = ("product_updates", "new_motors", "engineering_content", "co_development")
SOURCES = ("prefs_page", "email_link", "unsubscribe", "signup", "google", "email_change")
LANGS = ("en", "zh-CN")

#: Bump the version whenever ANY wording below changes (the hash would change anyway, the version makes it readable).
TEXT_VERSION = "comm_prefs_v1"

#: The sentence above the boxes and the four topic lines, per language.  Canonical for the SHA-256 recorded with each change.
INTRO = {
    "en": "Send me e-mail about (optional, you can change this at any time):",
    "zh-CN": "请通过电子邮件向我发送以下内容（可选，随时可更改）：",
}
WORDING = {
    "en": {
        "product_updates": "Product updates: release notes and new features",
        "new_motors": "New motors: additions to the catalogue and new passports",
        "engineering_content": "Engineering content: articles, webinars and case studies",
        "co_development": "Co-development: beta programmes and invitations to build modules together",
    },
    "zh-CN": {
        "product_updates": "产品更新：版本说明和新功能",
        "new_motors": "新电机：目录新增电机和新的性能档案",
        "engineering_content": "工程内容：文章、网络研讨会和案例研究",
        "co_development": "共同开发：测试计划及共同开发模块的邀请",
    },
}

TOKEN_TTL_S = 90 * 24 * 3600
TOKEN_PURPOSE = "prefs"

_LOCK = store_lock(lambda: _STATE_FILE)       # cross-process (deploy stage 2)
_ROOT = Path(DEFAULT_CONFIG_PATH).parent
_STATE_FILE = _ROOT / "comm_prefs.json"
_HISTORY_FILE = _ROOT / "comm_prefs_history.jsonl"


def _norm(email) -> str:
    return str(email or "").strip().lower()


def norm_lang(lang) -> str:
    """``zh-CN`` for any Chinese tag, else ``en``."""
    t = str(lang or "").strip().lower().replace("_", "-")
    return "zh-CN" if t == "zh" or t.startswith("zh-") else "en"


# -- the wording and its hash ---------------------------------------------------------

def wording(lang: str) -> dict:
    """``{version, lang, intro, topics{topic: line}}`` as shown to the user."""
    lang = norm_lang(lang)
    return {"version": TEXT_VERSION, "lang": lang, "intro": INTRO[lang], "topics": dict(WORDING[lang])}


def wording_sha256(lang: str) -> str:
    """SHA-256 of the canonical JSON of :func:`wording` (sorted keys, UTF-8, no spaces)."""
    blob = json.dumps(wording(lang), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


# -- stores -----------------------------------------------------------------------------

def _iso(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load() -> dict:
    with _LOCK:
        try:
            with open(_STATE_FILE, encoding="utf-8") as f:
                d = json.load(f)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            # a damaged state file must never read as "everybody consented" or "nobody": refuse, the history can rebuild it
            raise OSError(f"comm_prefs state unreadable: {e}") from e
        return d if isinstance(d, dict) else {}


def _save(d: dict) -> None:
    from motor_ai_sim.private_files import ensure_private_dir, open_private
    with _LOCK:
        ensure_private_dir(_STATE_FILE.parent)
        tmp = _STATE_FILE.with_suffix(".tmp")
        with open_private(tmp, "w") as f:
            json.dump(d, f, indent=1, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, _STATE_FILE)


def _append_history(rows: list[dict]) -> None:
    """Append lines; raises OSError (the caller changes nothing then)."""
    if not rows:
        return
    from motor_ai_sim.private_files import ensure_private_dir, open_private
    ensure_private_dir(_HISTORY_FILE.parent)
    with open_private(_HISTORY_FILE, "a") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")


def history(email: Optional[str] = None) -> list[dict]:
    """The consent history (oldest first), optionally of one account."""
    rows: list[dict] = []
    try:
        with open(_HISTORY_FILE, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and (email is None or _norm(r.get("user")) == _norm(email)):
                    rows.append(r)
    except OSError:
        return []
    return rows


def _blank() -> dict:
    return {"topics": {t: False for t in TOPICS}, "unsubscribed_all": False, "updated": None, "text_version": ""}


def _view(rec: Optional[dict]) -> dict:
    out = _blank()
    if isinstance(rec, dict):
        stored = rec.get("topics") if isinstance(rec.get("topics"), dict) else {}
        out["topics"] = {t: stored.get(t) is True for t in TOPICS}
        out["unsubscribed_all"] = rec.get("unsubscribed_all") is True
        out["updated"] = rec.get("updated")
        out["text_version"] = str(rec.get("text_version") or "")
        if rec.get("unsubscribed_at"):
            out["unsubscribed_at"] = rec["unsubscribed_at"]
    return out


def get(email: str) -> dict:
    """The current state: ``{topics{all four -> bool}, unsubscribed_all, updated, text_version}`` (default: nothing ticked)."""
    return _view(_load().get(_norm(email)))


# -- changes ----------------------------------------------------------------------------

def _line(email: str, event: str, topic: str, value: bool, previous: bool, *, source: str, ip: str, lang: str, now: float) -> dict:
    return dict(ts=round(now, 3), iso=_iso(now), user=email, event=event, topic=topic, value=value, previous=previous,
                text_version=TEXT_VERSION, text_sha256=wording_sha256(lang), lang=lang, source=source, ip=ip or "")


def _check(changes) -> dict:
    if not isinstance(changes, dict):
        raise ValueError("topics must be an object {topic: true|false}")
    bad = [k for k in changes if k not in TOPICS]
    if bad:
        raise ValueError("unknown topic(s): " + ", ".join(sorted(str(b) for b in bad)))
    nonbool = [k for k, v in changes.items() if not isinstance(v, bool)]
    if nonbool:
        raise ValueError("topic values must be true or false: " + ", ".join(sorted(nonbool)))
    return dict(changes)


def set_topics(email: str, changes: dict, *, source: str, ip: str = "", lang: str = "en") -> dict:
    """Apply ``{topic: bool}`` for an account; one history line per topic that actually changes.  -> the new state.

    The history is written FIRST: if it cannot be written (OSError) nothing changes.  Ticking any topic is a fresh consent and
    clears ``unsubscribed_all``.  ValueError for an unknown topic / non-boolean value / unknown source."""
    email = _norm(email)
    if not email:
        raise ValueError("no account")
    if source not in SOURCES:
        raise ValueError("unknown source: %r" % (source,))
    changes = _check(changes)
    lang = norm_lang(lang)
    with _LOCK:
        d = _load()
        rec = _view(d.get(email))
        now = time.time()
        rows = [_line(email, "set", t, v, rec["topics"][t], source=source, ip=ip, lang=lang, now=now)
                for t, v in changes.items() if rec["topics"][t] != v]
        if not rows:
            return rec
        _append_history(rows)
        for r in rows:
            rec["topics"][r["topic"]] = r["value"]
        if any(r["value"] for r in rows):
            rec["unsubscribed_all"] = False
            rec.pop("unsubscribed_at", None)
        rec["updated"] = _iso(now)
        rec["text_version"] = TEXT_VERSION
        d[email] = rec
        _save(d)
        return _view(rec)


def unsubscribe_all(email: str, *, source: str = "unsubscribe", ip: str = "", lang: str = "en") -> dict:
    """Switch every topic off and remember the wish to hear nothing, even when nothing was ticked.  Idempotent.  -> the new state.

    Also ends a legacy newsletter subscription of the same address, so the two stores never disagree."""
    email = _norm(email)
    if not email:
        raise ValueError("no account")
    if source not in SOURCES:
        raise ValueError("unknown source: %r" % (source,))
    lang = norm_lang(lang)
    with _LOCK:
        d = _load()
        rec = _view(d.get(email))
        if rec["unsubscribed_all"] and not any(rec["topics"].values()):
            _legacy_newsletter_off(email, source)
            return rec
        now = time.time()
        rows = [_line(email, "unsubscribe_all", t, False, True, source=source, ip=ip, lang=lang, now=now)
                for t in TOPICS if rec["topics"][t]]
        rows.append(_line(email, "unsubscribe_all", "*", True, False, source=source, ip=ip, lang=lang, now=now))
        _append_history(rows)
        rec["topics"] = {t: False for t in TOPICS}
        rec["unsubscribed_all"] = True
        rec["unsubscribed_at"] = _iso(now)
        rec["updated"] = _iso(now)
        rec["text_version"] = TEXT_VERSION
        d[email] = rec
        _save(d)
    _legacy_newsletter_off(email, source)
    return _view(rec)


def _legacy_newsletter_off(email: str, source: str) -> None:
    try:
        from motor_ai_sim import newsletter as N
        N.unsubscribe(email, source="link" if source != "prefs_page" else "settings", propagate=False)
    except Exception as e:                                   # noqa: BLE001 - the new store is the record
        log.warning("comm_prefs: legacy newsletter unsubscribe failed: %s", type(e).__name__)


def move(old: str, new: str, *, ip: str = "") -> None:
    """The sign-up address was a typo and the (unverified) account moved to ``new``: what the person ticked follows, recorded as
    fresh ``email_change`` lines under the new address; the old address keeps its own history (it is what that address ticked)."""
    old, new = _norm(old), _norm(new)
    with _LOCK:
        d = _load()
        rec = d.get(old)
        if rec is None or old == new:
            return
        st = _view(rec)
        now = time.time()
        rows = [_line(new, "set", t, True, False, source="email_change", ip=ip, lang=history_lang(old), now=now)
                for t in TOPICS if st["topics"][t]]
        _append_history(rows)
        d.pop(old)
        if rows:
            st["updated"] = _iso(now)
            d[new] = {k: v for k, v in st.items() if k != "unsubscribed_at" or st["unsubscribed_all"]}
        _save(d)


def history_lang(email: str) -> str:
    return last_lang(email) or "en"


def is_unsubscribed_all(email: str) -> bool:
    return get(email)["unsubscribed_all"]


def opted_in(email: str, topic: str) -> bool:
    """May marketing mail of ``topic`` go to this account?  Only an explicit tick counts, and never after an unsubscribe."""
    if topic not in TOPICS:
        raise ValueError("unknown topic: %r" % (topic,))
    st = get(email)
    return st["topics"][topic] and not st["unsubscribed_all"]


def last_lang(email: str) -> Optional[str]:
    """The language of the user's most recent consent event (the page language they used), if any."""
    for r in reversed(history(email)):
        if r.get("lang") in LANGS:
            return r["lang"]
    return None


def summary(users_total: Optional[int] = None) -> dict:
    """Admin overview: how many accounts opted in per topic, how many unsubscribed from everything."""
    d = _load()
    per = {t: 0 for t in TOPICS}
    any_topic = unsub = 0
    for rec in d.values():
        v = _view(rec)
        if v["unsubscribed_all"]:
            unsub += 1
        if any(v["topics"].values()) and not v["unsubscribed_all"]:
            any_topic += 1
        for t in TOPICS:
            if v["topics"][t] and not v["unsubscribed_all"]:
                per[t] += 1
    out = {"topics": per, "any_topic": any_topic, "unsubscribed_all": unsub, "records": len(d), "text_version": TEXT_VERSION}
    if users_total is not None:
        out["users_total"] = users_total
    return out


# -- account erasure / export -----------------------------------------------------------

def erase(email: str, subj: str) -> int:
    """Account deletion: drop the state, pseudonymise the consent history (the proof of consent may be kept, the person not).
    Returns the number of changed records."""
    email = _norm(email)
    n = 0
    with _LOCK:
        d = _load()
        if d.pop(email, None) is not None:
            _save(d)
            n += 1
        if _HISTORY_FILE.is_file():
            from motor_ai_sim.log_redaction import hash_ip
            out, changed = [], 0
            for ln in _HISTORY_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    r = json.loads(ln)
                except ValueError:
                    out.append(ln)
                    continue
                if isinstance(r, dict) and _norm(r.get("user")) == email:
                    r["user"] = subj
                    if r.get("ip"):
                        r["ip"] = hash_ip(str(r["ip"]))
                    changed += 1
                    ln = json.dumps(r, ensure_ascii=False, sort_keys=True)
                out.append(ln)
            if changed:
                from motor_ai_sim.private_files import open_private
                tmp = _HISTORY_FILE.with_suffix(".jsonl.tmp")
                with open_private(tmp, "w") as f:
                    f.write("\n".join(out) + "\n")
                os.replace(tmp, _HISTORY_FILE)
                n += changed
    return n


def export_for(email: str) -> dict:
    """The account's own consent data for the data-export ZIP."""
    return {"state": get(email), "history": history(email)}


# -- signed links ---------------------------------------------------------------------------

def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _key() -> bytes:
    secret = (os.environ.get("COMM_PREFS_SECRET") or "").strip()
    if not secret:
        from motor_ai_sim import users as U
        secret = U._secret()                                 # the application's session secret (never committed)
    return hmac.new(secret.encode("utf-8"), b"comm-prefs-link-v1", hashlib.sha256).digest()


def uid(email: str) -> str:
    """The pseudonymous account id carried in links (not reversible without the secret)."""
    return hmac.new(_key(), b"uid|" + _norm(email).encode("utf-8"), hashlib.sha256).hexdigest()[:24]


def make_token(email: str, purpose: str = TOKEN_PURPOSE, ttl: int = TOKEN_TTL_S, now: Optional[float] = None) -> str:
    t = int(time.time() if now is None else now)
    payload = _b64(json.dumps({"u": uid(email), "p": purpose, "e": t + int(ttl)}, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64(hmac.new(_key(), payload.encode("ascii"), hashlib.sha256).digest())
    return payload + "." + sig


def verify_token(token: str, purpose: str = TOKEN_PURPOSE, now: Optional[float] = None) -> Optional[str]:
    """-> the pseudonymous account id of a genuine, unexpired token of this purpose; None otherwise.  Never raises."""
    try:
        payload, sig = str(token or "").split(".", 1)
        want = _b64(hmac.new(_key(), payload.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(want, sig):
            return None
        claims = json.loads(_unb64(payload))
        t = time.time() if now is None else now
        if claims.get("p") != purpose or float(claims.get("e")) < t or not isinstance(claims.get("u"), str):
            return None
        return claims["u"]
    except Exception:                                        # noqa: BLE001 - any malformed token is simply invalid
        return None


def resolve(user_id: str) -> Optional[str]:
    """The account (e-mail) behind a pseudonymous id, None when it no longer exists."""
    from motor_ai_sim import users as U
    for email in U._load_soft():
        if hmac.compare_digest(uid(email), user_id):
            return _norm(email)
    return None


def account_for_token(token: str, purpose: str = TOKEN_PURPOSE, now: Optional[float] = None) -> Optional[str]:
    u = verify_token(token, purpose, now)
    return resolve(u) if u else None


def prefs_url(base: str, email: str, lang: str = "en", *, action: str = "") -> str:
    """The Communication preferences page link of an account (``/zh-cn/preferences`` for Chinese)."""
    path = "/zh-cn/preferences" if norm_lang(lang) == "zh-CN" else "/preferences"
    q = "t=" + make_token(email)
    if action:
        q += "&action=" + action
    return base.rstrip("/") + path + "?" + q


def one_click_url(api_base: str, email: str) -> str:
    """The RFC 8058 target: a mailbox provider POSTs here without a session."""
    return api_base.rstrip("/") + "/api/comm-prefs/" + make_token(email) + "/unsubscribe"
