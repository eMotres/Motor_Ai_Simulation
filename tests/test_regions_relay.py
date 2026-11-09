"""Regions stage 2: mail and LLM relay through the EU peer (docs/regions/DESIGN.md, section 7).

* the two ``relay/*`` endpoints need a valid peer signature and do not exist in single-region mode;
* EU executes with the REAL sender (SMTP / provider mocked at the library edge) and leaves only a hashed audit line;
* a US server in relay mode never touches smtplib or a provider client, and fails clearly when the peer is down;
* without ``MOTRES_RELAY_VIA_PEER`` nothing changes.
"""
from __future__ import annotations

import hashlib
import json
import logging
from email.message import EmailMessage
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim import auth_email as AE
from motor_ai_sim import notify as NT
from motor_ai_sim.api import app
from motor_ai_sim.regions import config as RC
from motor_ai_sim.regions import peer as P
from motor_ai_sim.regions import relay as RL

SECRET = "r" * 40
client = TestClient(app)
ADDR = "Customer.Person@example.com"

_ENV = ("MOTRES_REGION", "MOTRES_PEER_URL", "MOTRES_PEER_REGION", "MOTRES_PEER_HMAC_SECRET", "MOTRES_PEER_HMAC_SECRET_FILE",
        "MOTRES_REGION_GUARD", "MOTRES_RELAY_VIA_PEER", "SMTP_USER", "SMTP_PASS", "SMTP_PASSWORD", "SMTP_HOST", "SMTP_PORT",
        "MAIL_FROM", "NOTIFY_FROM", "MAIL_REPLY_TO", "NOTIFY_TO", "MOTRES_RELAY_MAIL_HOUR_MAX", "MOTRES_RELAY_LLM_MIN_MAX",
        "MOTRES_RELAY_LLM_DAY_MAX")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(tmp_path / "admin_audit.jsonl"))
    P.reset_nonces()
    RL.reset_limits()
    return SimpleNamespace(tmp=tmp_path, audit=tmp_path / "admin_audit.jsonl", mp=monkeypatch)


def _eu(env):
    env.mp.setenv("MOTRES_REGION", "eu")
    env.mp.setenv("MOTRES_PEER_URL", "http://10.9.0.2:8000")
    env.mp.setenv("MOTRES_PEER_HMAC_SECRET", SECRET)


def _us(env, relay=True):
    env.mp.setenv("MOTRES_REGION", "us")
    env.mp.setenv("MOTRES_PEER_URL", "http://10.9.0.1:8000")
    env.mp.setenv("MOTRES_PEER_HMAC_SECRET", SECRET)
    if relay:
        env.mp.setenv("MOTRES_RELAY_VIA_PEER", "1")


def _signed_post(path: str, payload: dict, region: str = "us", secret: str = SECRET):
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    h = P.sign_headers("POST", path, body, secret=secret, region=region)
    h["Content-Type"] = "application/json"
    return client.post(path, content=body, headers=h)


def _msg(to: str = ADDR) -> EmailMessage:
    m = EmailMessage()
    m["To"] = to
    m["Subject"] = "Confirm your account"
    m.set_content("Secret body text with a link")
    return m


class FakeSMTP:
    """Stands in for smtplib.SMTP: records the messages it was asked to send."""
    sent: list = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        pass

    def starttls(self, **k):
        pass

    def login(self, *a):
        pass

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


def _boom(*a, **k):
    raise AssertionError("smtplib / provider must not be touched on a relaying server")


# ── mode ─────────────────────────────────────────────────────────────────────

def test_relay_needs_both_the_variable_and_a_peer(env):
    assert not RL.relay_enabled()
    env.mp.setenv("MOTRES_RELAY_VIA_PEER", "1")
    assert not RL.relay_enabled()                           # single-region: the variable is ignored
    env.mp.delenv("MOTRES_RELAY_VIA_PEER")
    _us(env, relay=False)
    assert not RL.relay_enabled()                           # a peer but no variable
    _us(env)
    assert RL.relay_enabled()


# ── signature / mode on the endpoints ────────────────────────────────────────

@pytest.mark.parametrize("path", [P.PREFIX + "/relay/mail", P.PREFIX + "/relay/llm"])
def test_endpoints_404_in_single_region_mode(env, path):
    assert _signed_post(path, {}).status_code == 404


@pytest.mark.parametrize("path", [P.PREFIX + "/relay/mail", P.PREFIX + "/relay/llm"])
def test_endpoints_need_a_valid_signature(env, path):
    _eu(env)
    assert client.post(path, json={}).status_code == 404                              # unsigned
    assert _signed_post(path, {}, secret="x" * 40).status_code == 404                 # wrong secret
    assert _signed_post(path, {}, region="eu").status_code == 404                     # not the peer's region
    assert _signed_post(path, {"purpose": "nope"}).status_code == 200                 # valid signature: answered (refused)


# ── EU executes mail ─────────────────────────────────────────────────────────

def test_eu_sends_with_the_real_sender_and_audits_hashes_only(env):
    _eu(env)
    env.mp.setenv("SMTP_USER", "mailer@example.com")
    env.mp.setenv("SMTP_PASS", "pw")
    env.mp.setenv("MAIL_FROM", "no-reply@example.com")
    FakeSMTP.sent = []
    env.mp.setattr(AE.smtplib, "SMTP", FakeSMTP)
    r = _signed_post(P.PREFIX + "/relay/mail", RL.mail_wire(_msg()))
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert len(FakeSMTP.sent) == 1
    sent = FakeSMTP.sent[0]
    assert sent["To"] == ADDR and sent["From"] == "no-reply@example.com" and "Secret body" in sent.get_content()
    line = env.audit.read_text(encoding="utf-8")
    rec = json.loads(line.strip().splitlines()[-1])
    assert rec["action"] == "relay.mail" and rec["actor"] == "peer:us"
    assert rec["details"]["to_sha256"] == [hashlib.sha256(ADDR.lower().encode()).hexdigest()]
    assert rec["details"]["size"] > 0 and rec["details"]["ok"] is True
    for needle in ("example.com", "Customer", "Secret body", "Confirm your account"):
        assert needle not in line


def test_eu_reports_a_send_failure_and_the_caps(env):
    _eu(env)
    env.mp.setenv("SMTP_USER", "mailer@example.com")
    env.mp.setenv("SMTP_PASS", "pw")
    env.mp.setattr(AE.smtplib, "SMTP", _boom_smtp)
    r = _signed_post(P.PREFIX + "/relay/mail", RL.mail_wire(_msg()))
    assert r.json() == {"ok": False, "code": "send_failed"}
    assert _signed_post(P.PREFIX + "/relay/mail", {"v": 1, "eml": "!!notbase64"}).json()["code"] == "bad_request"
    big = {"v": 1, "eml": __import__("base64").b64encode(b"x" * (RL.MAX_MAIL_BYTES + 1)).decode()}
    assert _signed_post(P.PREFIX + "/relay/mail", big).json()["code"] == "too_large"
    env.mp.setenv("MOTRES_RELAY_MAIL_HOUR_MAX", "1")
    RL.reset_limits()
    env.mp.setattr(AE.smtplib, "SMTP", FakeSMTP)
    env.mp.setattr(RL, "_int_env", lambda n, d: 1 if n == "MOTRES_RELAY_MAIL_HOUR_MAX" else d)
    assert _signed_post(P.PREFIX + "/relay/mail", RL.mail_wire(_msg())).json() == {"ok": True}
    assert _signed_post(P.PREFIX + "/relay/mail", RL.mail_wire(_msg())).json()["code"] == "rate_limited"


def _boom_smtp(*a, **k):
    raise OSError("connection refused")


def test_a_relaying_server_never_executes(env):
    _us(env)
    assert RL.execute_mail(RL.mail_wire(_msg())) == {"ok": False, "code": "not_an_executor"}
    assert RL.execute_llm({"purpose": "support"}) == {"ok": False, "code": "not_an_executor"}


# ── US side: mail ────────────────────────────────────────────────────────────

def test_without_the_variable_mail_still_uses_smtp(env):
    env.mp.setenv("MOTRES_RELAY_VIA_PEER", "1")             # no peer: ignored
    env.mp.setenv("SMTP_USER", "mailer@example.com")
    env.mp.setenv("SMTP_PASS", "pw")
    FakeSMTP.sent = []
    env.mp.setattr(AE.smtplib, "SMTP", FakeSMTP)
    AE.deliver(_msg())
    assert len(FakeSMTP.sent) == 1


def test_us_in_relay_mode_never_touches_smtplib(env):
    _us(env)
    env.mp.setattr(AE.smtplib, "SMTP", _boom)
    env.mp.setattr(AE.smtplib, "SMTP_SSL", _boom)
    env.mp.setattr(NT.smtplib, "SMTP", _boom)
    calls = []
    env.mp.setattr(P, "post", lambda path, payload, **kw: calls.append((path, payload, kw)) or {"ok": True})
    assert AE.smtp_configured() is True and NT.smtp_configured() is True
    AE.deliver(_msg())
    assert AE.send(ADDR, "Subject", "Body", block=True, kind="") is True
    assert NT._send_email("Subject", "Body", reply_to="x@example.com") is True
    assert [c[0] for c in calls] == ["/relay/mail"] * 3
    assert all(c[1]["v"] == 1 and c[1]["eml"] for c in calls)


def test_us_peer_down_raises_like_smtp_and_logs(env, caplog):
    _us(env)
    env.mp.setattr(AE.smtplib, "SMTP", _boom)
    env.mp.setattr(P, "post", lambda *a, **k: None)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(RL.RelayError):
            AE.deliver(_msg())
    assert "relay through the peer failed" in caplog.text
    assert AE.send(ADDR, "S", "B", block=True) is False       # the forgiving wrapper
    assert NT._send_email("S", "B") is False
    big = _msg()
    big.set_content("x" * (RL.MAX_MAIL_BYTES + 10))
    with pytest.raises(RL.RelayError):
        AE.deliver(big)


# ── US side: LLM ─────────────────────────────────────────────────────────────

def test_us_chat_goes_through_the_peer_and_never_creates_a_provider_client(env):
    from motor_ai_sim.routes import support as S
    _us(env)
    env.mp.setattr(S, "_anthropic_client", _boom)
    env.mp.setattr(S, "_gemini_reply", _boom)
    env.mp.setattr(S, "_load_overrides", lambda: {})
    env.mp.setattr(S, "ENV_ANTHROPIC_KEY", "")
    env.mp.setattr(S, "ENV_GEMINI_KEY", "")
    env.mp.setattr(S, "_charge_anonymous", lambda ip: None)
    env.mp.setattr(S, "_deliver", lambda reply, source, **kw: {"reply": reply, "source": source, "model": kw.get("model", "")})
    seen = []
    env.mp.setattr(P, "post", lambda path, payload, **kw: seen.append((path, payload)) or {"ok": True, "text": "Hello", "model": "m-1"})
    out = S.chat(None, {"messages": [{"role": "user", "content": "hi"}]}, None)
    assert out == {"reply": "Hello", "source": "claude", "model": "m-1"}
    assert seen[0][0] == "/relay/llm" and seen[0][1]["purpose"] == "support" and seen[0][1]["messages"][-1]["content"] == "hi"


def test_us_chat_peer_down_answers_with_the_error_reply(env):
    from motor_ai_sim.routes import support as S
    _us(env)
    env.mp.setattr(S, "_load_overrides", lambda: {})
    env.mp.setattr(S, "_charge_anonymous", lambda ip: None)
    env.mp.setattr(S, "_deliver", lambda reply, source, **kw: {"reply": reply, "source": source})
    env.mp.setattr(P, "post", lambda *a, **k: None)
    out = S.chat(None, {"messages": [{"role": "user", "content": "hi"}]}, None)
    assert out["source"] == "error" and "couldn't answer" in out["reply"]


def test_us_spec_assistant_and_translation_use_the_relay(env):
    from motor_ai_sim.development import spec_assistant as SA
    from motor_ai_sim.production import translate as T
    _us(env)
    env.mp.setattr(T, "_post", _boom)
    answers = iter([{"ok": True, "text": "reply", "model": "m-2"}, {"ok": True, "text": "stator", "served_by": "anthropic:m"}])
    seen = []
    env.mp.setattr(P, "post", lambda path, payload, **kw: seen.append(payload) or next(answers))
    assert SA.complete([{"role": "user", "content": "q"}], "sys") == ("reply", "claude", "m-2")
    assert T.translate_text("定子", "zh", "en") == "stator"
    assert [s["purpose"] for s in seen] == ["support", "translate"] and seen[1]["src"] == "zh"
    env.mp.setattr(P, "post", lambda *a, **k: None)
    with pytest.raises(T.TranslateError):
        T.translate_text("x", "en", "zh")
    with pytest.raises(SA.AssistantUnavailable):
        SA.complete([{"role": "user", "content": "q"}], "sys")


# ── EU executes the LLM ──────────────────────────────────────────────────────

def test_eu_runs_the_provider_and_audits_counts_only(env):
    from motor_ai_sim.routes import support as S
    _eu(env)
    env.mp.setattr(S, "_load_overrides", lambda: {})
    env.mp.setattr(S, "ENV_PROVIDER", "")
    env.mp.setattr(S, "ENV_GEMINI_KEY", "")
    env.mp.setattr(S, "ENV_ANTHROPIC_KEY", "k")
    asked = {}

    class Client:
        class messages:
            @staticmethod
            def create(**kw):
                asked.update(kw)
                return SimpleNamespace(content=[SimpleNamespace(type="text", text="the answer")],
                                       usage=SimpleNamespace(input_tokens=11, output_tokens=7))

    env.mp.setattr(S, "_anthropic_client", lambda key: Client)
    r = _signed_post(P.PREFIX + "/relay/llm", {"v": 1, "purpose": "support", "system": "SYSTEM-PROMPT-TEXT",
                                               "messages": [{"role": "user", "content": "private question"}], "max_tokens": 99999})
    j = r.json()
    assert j["ok"] is True and j["text"] == "the answer" and j["model"] == S.ENV_ANTHROPIC_MODEL
    assert asked["max_tokens"] == RL.MAX_LLM_TOKENS and asked["messages"][0]["content"] == "private question"
    line = env.audit.read_text(encoding="utf-8")
    rec = json.loads(line.strip().splitlines()[-1])
    assert rec["action"] == "relay.llm" and rec["details"]["tok_in"] == 11 and rec["details"]["tok_out"] == 7
    assert rec["details"]["model"] == S.ENV_ANTHROPIC_MODEL and rec["details"]["size"] == len("SYSTEM-PROMPT-TEXT") + len("private question")
    for needle in ("private question", "SYSTEM-PROMPT", "the answer"):
        assert needle not in line


def test_eu_translation_runs_with_its_own_key(env):
    from motor_ai_sim.production import translate as T
    _eu(env)
    env.mp.setattr(T, "_post", lambda payload, timeout: {"content": [{"type": "text", "text": "stator"}], "model": "claude-x",
                                                         "usage": {"input_tokens": 5, "output_tokens": 2}})
    j = _signed_post(P.PREFIX + "/relay/llm", {"v": 1, "purpose": "translate", "text": "定子", "src": "zh", "tgt": "en"}).json()
    assert j["ok"] is True and j["text"] == "stator" and j["served_by"].startswith("anthropic:")
    rec = json.loads(env.audit.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["details"]["tok_in"] == 5 and rec["details"]["purpose"] == "translate"


def test_eu_llm_refusals(env):
    _eu(env)
    path = P.PREFIX + "/relay/llm"
    assert _signed_post(path, {"purpose": "support", "system": "", "messages": "bad"}).json()["code"] == "bad_request"
    assert _signed_post(path, {"purpose": "design_agent"}).json()["code"] == "bad_request"
    assert _signed_post(path, {"purpose": "translate", "text": "x", "src": "fr", "tgt": "en"}).json()["code"] == "bad_request"
    big = {"purpose": "support", "system": "s" * (RL.MAX_LLM_CHARS + 1), "messages": [{"role": "user", "content": "q"}]}
    assert _signed_post(path, big).json()["code"] == "too_large"
    env.mp.setattr(RL, "_int_env", lambda n, d: 1 if n == "MOTRES_RELAY_LLM_MIN_MAX" else d)
    from motor_ai_sim.production import translate as T
    env.mp.setattr(T, "_post", lambda p, t: {"content": [{"type": "text", "text": "ok"}]})
    ok = {"purpose": "translate", "text": "a", "src": "en", "tgt": "zh"}
    assert _signed_post(path, ok).json()["ok"] is True
    assert _signed_post(path, ok).json()["code"] == "rate_limited"
    env.mp.setattr(T, "_post", lambda p, t: (_ for _ in ()).throw(RuntimeError("provider down")))
    RL.reset_limits()
    assert _signed_post(path, ok).json() == {"ok": False, "code": "provider_failed"}


# ── end to end in one process: US asks, EU executes ──────────────────────────

def test_end_to_end_mail_us_to_eu(env):
    env.mp.setenv("SMTP_USER", "mailer@example.com")
    env.mp.setenv("SMTP_PASS", "pw")
    FakeSMTP.sent = []

    def via_eu(path, payload, **kw):                          # the "network": sign as US, answer as EU
        full = P.PREFIX + path
        env.mp.setenv("MOTRES_REGION", "eu")
        env.mp.setenv("MOTRES_PEER_URL", "http://10.9.0.2:8000")
        env.mp.delenv("MOTRES_RELAY_VIA_PEER")
        env.mp.setattr(AE.smtplib, "SMTP", FakeSMTP)
        try:
            return _signed_post(full, payload).json()
        finally:
            _us(env)
            env.mp.setattr(AE.smtplib, "SMTP", _boom)

    _us(env)
    env.mp.setattr(AE.smtplib, "SMTP", _boom)
    env.mp.setattr(P, "post", via_eu)
    AE.deliver(_msg())
    assert len(FakeSMTP.sent) == 1 and FakeSMTP.sent[0]["To"] == ADDR
