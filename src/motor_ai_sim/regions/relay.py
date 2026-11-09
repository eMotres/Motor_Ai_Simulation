"""Mail and LLM relay through the EU peer (regions stage 2; docs/regions/DESIGN.md, section 7).

Owner 2026-11-09: the US server has NO SMTP credentials and NO Anthropic / OpenRouter keys.  Its outgoing mail and its
help-chat / specification-assistant / production-translation LLM calls are RELAYED to the EU server over the signed peer
channel.  EU executes, US asks.

RELAY MODE = ``MOTRES_RELAY_VIA_PEER=1`` AND a configured peer (``regions.config.peer_configured``).  Without both,
``relay_enabled()`` is False and every caller keeps its old path unchanged (EU and single-region behave exactly as before).
A server in relay mode never EXECUTES a relayed request (no relay loops).

This is the one documented exception to "peer endpoints are pull only": the two endpoints perform an action.  They store
nothing of the request on EU.  The only trace is one audit line (``relay.mail`` / ``relay.llm``) carrying the sha256 of
each recipient (never a plain address), the message size, the model and the token counts; no subject, no body, no prompt,
no reply.

Executed-but-refused answers (cap exceeded, rate limit, provider failure) are HTTP 200 ``{"ok": false, "code": ...}`` so the
caller can tell them from "peer unreachable"; a missing / wrong signature is the usual 404.
"""
from __future__ import annotations

import base64
import email
import email.policy
import hashlib
import logging
import os
import threading
import time
from email.message import EmailMessage
from typing import Any, Dict, List, Optional, Tuple

from motor_ai_sim.regions import config as RC
from motor_ai_sim.regions import peer as P

log = logging.getLogger(__name__)

ENV_RELAY = "MOTRES_RELAY_VIA_PEER"

#: Largest serialized message (headers + body + attachments) the relay carries / accepts.
MAX_MAIL_BYTES = 2 * 1024 * 1024
MAX_MAIL_RCPT = 100
#: Largest LLM request (system + messages, or text to translate), in characters.
MAX_LLM_CHARS = 200_000
MAX_LLM_TOKENS = 8192
MAIL_TIMEOUT_S = 25.0
LLM_TIMEOUT_S = 100.0
#: EU-side caps, per UTC day / per minute, for the whole peer (the per-user / per-IP limiters run where the person is).
ENV_MAIL_HOUR_MAX, DEFAULT_MAIL_HOUR_MAX = "MOTRES_RELAY_MAIL_HOUR_MAX", 600
ENV_LLM_MIN_MAX, DEFAULT_LLM_MIN_MAX = "MOTRES_RELAY_LLM_MIN_MAX", 120
ENV_LLM_DAY_MAX, DEFAULT_LLM_DAY_MAX = "MOTRES_RELAY_LLM_DAY_MAX", 20000
PURPOSES = ("support", "translate")

_LOCK = threading.Lock()
_HITS: Dict[str, List[float]] = {}


class RelayError(RuntimeError):
    """The peer could not do it (unreachable, refused, provider failure).  The message never has content in it."""


def relay_enabled() -> bool:
    """Relay mode: asked for AND a peer exists (``MOTRES_RELAY_VIA_PEER`` is ignored in single-region mode)."""
    return str(os.environ.get(ENV_RELAY, "")).strip().lower() in ("1", "true", "yes", "on") and RC.peer_configured()


def reset_limits() -> None:
    with _LOCK:
        _HITS.clear()


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


def _charge(bucket: str, window_s: float, cap: int, now: Optional[float] = None) -> bool:
    t = time.time() if now is None else now
    with _LOCK:
        hits = [h for h in _HITS.get(bucket, []) if h > t - window_s]
        if len(hits) >= cap:
            _HITS[bucket] = hits
            return False
        hits.append(t)
        _HITS[bucket] = hits
        return True


def addr_hash(address: str) -> str:
    return hashlib.sha256(str(address or "").strip().lower().encode("utf-8")).hexdigest()


def _audit(action: str, details: Dict[str, Any]) -> None:
    from motor_ai_sim import admin_audit as AA
    AA.record("peer:" + RC.peer_region(), action, "", details=details)


# ── the asking side (US) ─────────────────────────────────────────────────────

def _ask(kind: str, payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    if not relay_enabled():
        raise RelayError("relay is not enabled")
    ans = P.post(f"/relay/{kind}", payload, timeout=timeout)
    if not isinstance(ans, dict):
        raise RelayError("the EU server could not be reached")
    if not ans.get("ok"):
        raise RelayError(f"the EU server refused ({str(ans.get('code') or 'error')[:40]})")
    return ans


def mail_wire(msg: EmailMessage) -> Dict[str, Any]:
    raw = msg.as_bytes()
    if len(raw) > MAX_MAIL_BYTES:
        raise RelayError("message too large for the relay (%d bytes)" % len(raw))
    return {"v": 1, "eml": base64.b64encode(raw).decode("ascii")}


def send_mail(msg: EmailMessage) -> None:
    """Hand one prepared message to the EU server's SMTP.  RAISES ``RelayError`` like an SMTP failure raises."""
    _ask("mail", mail_wire(msg), MAIL_TIMEOUT_S)


def _llm_size(system: str, messages: List[Dict[str, Any]]) -> int:
    return len(system or "") + sum(len(str(m.get("content") or "")) for m in messages)


def support_complete(messages: List[Dict[str, Any]], system: str, max_tokens: int = 2048) -> Tuple[str, str]:
    """-> ``(text, model)`` from the provider that answers the help chat on EU."""
    if _llm_size(system, messages) > MAX_LLM_CHARS:
        raise RelayError("request too large for the relay")
    ans = _ask("llm", {"v": 1, "purpose": "support", "system": system, "messages": messages,
                       "max_tokens": min(int(max_tokens), MAX_LLM_TOKENS)}, LLM_TIMEOUT_S)
    return str(ans.get("text") or ""), str(ans.get("model") or "")


def translate(text: str, src_lang: str, tgt_lang: str) -> Tuple[str, str]:
    """-> ``(translation, served_by)`` from the EU server's translation provider."""
    if len(text or "") > MAX_LLM_CHARS:
        raise RelayError("request too large for the relay")
    ans = _ask("llm", {"v": 1, "purpose": "translate", "text": str(text), "src": src_lang, "tgt": tgt_lang}, LLM_TIMEOUT_S)
    return str(ans.get("text") or ""), str(ans.get("served_by") or "")


# ── the executing side (EU) ──────────────────────────────────────────────────

def _refuse(code: str) -> Dict[str, Any]:
    return {"ok": False, "code": code}


def execute_mail(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Send one relayed message through the real sender (``auth_email.deliver``)."""
    if relay_enabled():                                     # a relaying server never executes (no loops)
        return _refuse("not_an_executor")
    try:
        raw = base64.b64decode(str((payload or {}).get("eml") or ""), validate=True)
    except Exception:                                       # noqa: BLE001
        return _refuse("bad_request")
    if not raw or len(raw) > MAX_MAIL_BYTES:
        return _refuse("too_large" if raw else "bad_request")
    try:
        msg = email.message_from_bytes(raw, policy=email.policy.SMTP)
        rcpt = [a for h in ("To", "Cc", "Bcc") for a in (m.addr_spec for m in _addresses(msg, h))]
    except Exception:                                       # noqa: BLE001
        return _refuse("bad_request")
    if not rcpt or len(rcpt) > MAX_MAIL_RCPT:
        return _refuse("bad_recipients")
    if not _charge("mail", 3600.0, _int_env(ENV_MAIL_HOUR_MAX, DEFAULT_MAIL_HOUR_MAX)):
        _audit("relay.mail", {"ok": False, "code": "rate_limited", "size": len(raw)})
        return _refuse("rate_limited")
    from motor_ai_sim import auth_email as AE
    for h in ("From", "Reply-To"):                          # an empty header from a sender without MAIL_FROM: EU fills it
        if not str(msg.get(h) or "").strip():
            del msg[h]
    ok, err = True, ""
    try:
        AE.deliver(msg)                                     # type: ignore[arg-type]
    except Exception as e:                                  # noqa: BLE001 - the caller gets the class name only
        ok, err = False, type(e).__name__
        log.warning("relay mail: send failed (%s)", err)
    _audit("relay.mail", {"ok": ok, "error": err, "size": len(raw), "n_rcpt": len(rcpt),
                          "to_sha256": [addr_hash(a) for a in rcpt[:20]]})
    return {"ok": True} if ok else _refuse("send_failed")


def _addresses(msg, header: str):
    out = []
    for v in msg.get_all(header, []) or []:
        out.extend(getattr(v, "addresses", None) or [])
    return out


def _usage(obj: Any) -> Tuple[int, int]:
    u = getattr(obj, "usage", None)
    if u is None and isinstance(obj, dict):
        u = obj.get("usage")
    def g(*names):
        for n in names:
            v = u.get(n) if isinstance(u, dict) else getattr(u, n, None)
            if isinstance(v, int):
                return v
        return 0
    return g("input_tokens", "prompt_tokens"), g("output_tokens", "completion_tokens")


def _support_llm(system: str, messages: List[Dict[str, Any]], max_tokens: int) -> Tuple[str, str, int, int]:
    """The same provider choice as the help chat on EU (``support._effective``)."""
    from motor_ai_sim.routes import support as S
    eff = S._effective()
    if eff["provider"] == "gemini" and eff["gemini"]["key"]:
        g = eff["gemini"]
        text = S._call_provider(lambda: S._gemini_reply(messages, g["key"], g["model"], system))
        return str(text or ""), g["model"], 0, 0
    if eff["provider"] == "anthropic":
        a = eff["anthropic"]
        client = S._anthropic_client(a["key"])
        if client is not None:
            resp = S._call_provider(lambda: client.messages.create(
                model=a["model"], max_tokens=max_tokens, system=system, messages=messages))
            tin, tout = _usage(resp)
            return next((b.text for b in resp.content if b.type == "text"), ""), a["model"], tin, tout
    raise RuntimeError("no provider configured")


def _clean_messages(raw: Any) -> Optional[List[Dict[str, str]]]:
    if not isinstance(raw, list) or not raw or len(raw) > 60:
        return None
    out = []
    for m in raw:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant") or not isinstance(m.get("content"), str):
            return None
        out.append({"role": m["role"], "content": m["content"]})
    return out


def execute_llm(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Run one relayed LLM request with EU's own provider and key; return the text, never anything else of the provider."""
    if relay_enabled():
        return _refuse("not_an_executor")
    p = payload if isinstance(payload, dict) else {}
    purpose = p.get("purpose")
    if purpose not in PURPOSES:
        return _refuse("bad_request")
    if purpose == "support":
        system, messages = str(p.get("system") or ""), _clean_messages(p.get("messages"))
        if messages is None:
            return _refuse("bad_request")
        size = _llm_size(system, messages)
    else:
        text = p.get("text")
        if not isinstance(text, str) or p.get("src") not in ("en", "zh") or p.get("tgt") not in ("en", "zh"):
            return _refuse("bad_request")
        size = len(text)
    if size > MAX_LLM_CHARS:
        return _refuse("too_large")
    if not (_charge("llm-min", 60.0, _int_env(ENV_LLM_MIN_MAX, DEFAULT_LLM_MIN_MAX))
            and _charge("llm-day", 86400.0, _int_env(ENV_LLM_DAY_MAX, DEFAULT_LLM_DAY_MAX))):
        _audit("relay.llm", {"ok": False, "code": "rate_limited", "purpose": purpose, "size": size})
        return _refuse("rate_limited")
    model, tin, tout, err = "", 0, 0, ""
    out: Dict[str, Any] = {"ok": True}
    try:
        if purpose == "support":
            mt = max(1, min(int(p.get("max_tokens") or 2048), MAX_LLM_TOKENS))
            text_out, model, tin, tout = _support_llm(system, messages, mt)
            out.update(text=text_out, model=model)
        else:
            from motor_ai_sim.production import translate as T
            payload_t = T._payload(text, str(p.get("src")), str(p.get("tgt")))
            data = T._post(payload_t, T.TIMEOUT_S)
            text_out, served = T._answer(data)
            model = str(payload_t.get("model") or "")
            tin, tout = _usage(data)
            out.update(text=text_out, served_by=served, model=model)
    except Exception as e:                                  # noqa: BLE001
        err = type(e).__name__
        log.warning("relay llm: %s call failed (%s)", purpose, err)
    _audit("relay.llm", {"ok": not err, "error": err, "purpose": purpose, "size": size, "model": model,
                         "tok_in": tin, "tok_out": tout})
    return out if not err else _refuse("provider_failed")
