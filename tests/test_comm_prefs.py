"""Communication preferences (comm_prefs.py, routes/comm_prefs.py) and the launch mailing tool (mailing.py,
scripts/send_announcement.py).

Everything runs against tmp_path with a fake SMTP; the live registry, config/newsletter.json and the identity files must stay
untouched.  No real e-mail is ever sent: ``E.deliver`` and ``smtplib.SMTP`` are replaced in every test that could reach them.
"""
from __future__ import annotations

import base64
import email as emaillib
import hashlib
import importlib.util
import json
import smtplib
import time
from email import policy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim.api import app

_ROOT = Path(__file__).resolve().parents[1]
_REAL = [_ROOT / "config" / n for n in ("users.json", ".sessions.json", ".auth_secret", "newsletter.json",
                                        "comm_prefs.json", "comm_prefs_history.jsonl")]

ADMIN = "owner@example.com"
ANNA = "anna@example.com"
LI = "li@example.com"
BOB = "bob@example.com"
PW = "correct horse battery"

client = TestClient(app)


def _stamp(p: Path):
    return p.stat().st_mtime_ns if p.exists() else None


@pytest.fixture(scope="module", autouse=True)
def _real_files_untouched():
    before = [_stamp(p) for p in _REAL]
    yield
    assert before == [_stamp(p) for p in _REAL], "a test touched the live store"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from motor_ai_sim import auth as A
    from motor_ai_sim import auth_email as E
    from motor_ai_sim import comm_prefs as CP
    from motor_ai_sim import newsletter as N
    from motor_ai_sim import sessions as S
    from motor_ai_sim import users as U
    from motor_ai_sim.routes import comm_prefs as R

    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(S, "_SESSIONS_FILE", tmp_path / ".sessions.json")
    monkeypatch.setattr(S, "_EVENTS_FILE", tmp_path / "auth_events.jsonl")
    monkeypatch.setattr(N, "_STORE_FILE", tmp_path / "newsletter.json")
    monkeypatch.setattr(CP, "_STATE_FILE", tmp_path / "comm_prefs.json")
    monkeypatch.setattr(CP, "_HISTORY_FILE", tmp_path / "comm_prefs_history.jsonl")
    monkeypatch.setattr(CP, "_ROOT", tmp_path)
    monkeypatch.setenv("MAILING_LOG_DIR", str(tmp_path / "mailing"))
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.delenv("COMM_PREFS_SECRET", raising=False)
    monkeypatch.setenv("SMTP_USER", "noreply@aerostator.com")
    monkeypatch.setenv("SMTP_PASS", "x")
    monkeypatch.setenv("MAIL_FROM", "noreply@aerostator.com")
    monkeypatch.setenv("PUBLIC_APP_URL", "https://aerostator.com")
    monkeypatch.setenv("MOTRES_LEGAL_ROOT", str(tmp_path / "legal"))
    monkeypatch.setattr(A, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(A, "AUTH_ENFORCE", False)
    monkeypatch.setattr(A, "_reject_seen", {})
    monkeypatch.setattr(S, "_last_touch", {})
    monkeypatch.delenv("WORKSPACES_ROOT", raising=False)
    E.reset_limits()
    R.BAD_TOKEN_IP.reset_all()

    delivered: list = []
    monkeypatch.setattr(E, "deliver", lambda msg: delivered.append(msg))
    outbox: list = []
    monkeypatch.setattr(E, "send", lambda to, subject, body, *, block=False: outbox.append((to, subject, body)) or True)

    def no_smtp(*a, **k):
        raise AssertionError("a test reached a real SMTP connection")
    monkeypatch.setattr(smtplib, "SMTP", no_smtp)
    monkeypatch.setattr(smtplib, "SMTP_SSL", no_smtp)

    U.create_user(ADMIN, "password-owner", role="admin", name="Admin")
    for e, name in ((ANNA, "Anna Test"), (LI, "李"), (BOB, "Bob")):
        U.create_user(e, "password-" + e.split("@")[0], name=name)
    yield {"U": U, "N": N, "E": E, "CP": CP, "R": R, "delivered": delivered, "outbox": outbox, "tmp": tmp_path, "mp": monkeypatch}
    E.reset_limits()
    R.BAD_TOKEN_IP.reset_all()


def _login(who, ip="203.0.113.5"):
    r = client.post("/api/auth/login", json={"email": who, "password": "password-" + who.split("@")[0]},
                    headers={"X-Forwarded-For": ip})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _set_locale(env, who, locale):
    """Write the UI-language store the way /api/me/preferences does."""
    from motor_ai_sim.routes import account
    env["mp"].setattr(account, "_prefs_path", lambda: str(env["tmp"] / ".user_prefs.json"))
    p = Path(account._prefs_path())
    d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    d[who] = {"locale": locale}
    p.write_text(json.dumps(d), encoding="utf-8")


# -- defaults, history, wording ---------------------------------------------------------------

def test_every_topic_defaults_to_false(env):
    CP = env["CP"]
    assert CP.TOPICS == ("product_updates", "new_motors", "engineering_content", "co_development")
    st = CP.get(ANNA)
    assert st["topics"] == {t: False for t in CP.TOPICS} and st["unsubscribed_all"] is False and st["updated"] is None
    assert CP.summary()["any_topic"] == 0
    assert all(not CP.opted_in(ANNA, t) for t in CP.TOPICS)
    h = _login(ANNA)
    r = client.get("/api/me/comm-prefs", headers=h)
    assert r.status_code == 200 and r.json()["topics"] == {t: False for t in CP.TOPICS}
    assert r.json()["account"] == ANNA and r.json()["nda"] == {"signed": False, "signable": False}
    assert client.get("/api/me/comm-prefs").status_code == 401


def test_history_is_append_only_with_text_hash_source_ip_and_time(env):
    CP = env["CP"]
    before = time.time()
    CP.set_topics(ANNA, {"product_updates": True, "new_motors": True}, source="prefs_page", ip="198.51.100.4", lang="en")
    CP.set_topics(ANNA, {"new_motors": False}, source="email_link", ip="198.51.100.5", lang="zh-CN")
    CP.set_topics(ANNA, {"product_updates": True}, source="email_link")                      # unchanged: no line
    rows = CP.history(ANNA)
    assert [(r["topic"], r["value"], r["previous"], r["source"]) for r in rows] == [
        ("product_updates", True, False, "prefs_page"), ("new_motors", True, False, "prefs_page"),
        ("new_motors", False, True, "email_link")]
    assert all(r["text_version"] == "comm_prefs_v1" and before - 1 <= r["ts"] <= time.time() + 1 and r["iso"].endswith("Z") for r in rows)
    assert rows[0]["text_sha256"] == CP.wording_sha256("en") == rows[1]["text_sha256"]
    assert rows[2]["text_sha256"] == CP.wording_sha256("zh-CN") != CP.wording_sha256("en")
    assert [r["ip"] for r in rows] == ["198.51.100.4", "198.51.100.4", "198.51.100.5"]
    # the hash names exactly the wording
    blob = json.dumps(CP.wording("en"), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert CP.wording_sha256("en") == hashlib.sha256(blob).hexdigest()
    # the history file is plain appended lines; earlier lines are never rewritten by later changes
    first = (env["tmp"] / "comm_prefs_history.jsonl").read_text(encoding="utf-8").splitlines()
    CP.set_topics(ANNA, {"co_development": True}, source="prefs_page")
    again = (env["tmp"] / "comm_prefs_history.jsonl").read_text(encoding="utf-8").splitlines()
    assert again[:len(first)] == first and len(again) == len(first) + 1
    assert CP.get(ANNA)["topics"] == {"product_updates": True, "new_motors": False, "engineering_content": False, "co_development": True}
    assert CP.opted_in(ANNA, "co_development") and not CP.opted_in(ANNA, "new_motors")


def test_bad_changes_are_refused_loudly_and_change_nothing(env):
    CP = env["CP"]
    for bad in ({"newsletter": True}, {"new_motors": "yes"}, {"new_motors": 1}, "all", None, [True]):
        with pytest.raises(ValueError):
            CP.set_topics(ANNA, bad, source="prefs_page")
    with pytest.raises(ValueError):
        CP.set_topics(ANNA, {"new_motors": True}, source="carrier_pigeon")
    assert CP.history(ANNA) == [] and CP.get(ANNA)["topics"]["new_motors"] is False


def test_history_failure_changes_nothing(env):
    CP = env["CP"]
    env["mp"].setattr(CP, "_append_history", lambda rows: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        CP.set_topics(ANNA, {"new_motors": True}, source="prefs_page")
    assert CP.get(ANNA)["topics"]["new_motors"] is False


def test_server_wording_equals_the_text_on_screen():
    """The hash recorded with a consent must name the wording the page showed: web locales == server constants."""
    from motor_ai_sim import comm_prefs as CP
    for lang in CP.LANGS:
        loc = json.loads((_ROOT / "web" / "src" / "locales" / lang / "common.json").read_text(encoding="utf-8"))["commPrefs"]
        assert loc["intro"] == CP.INTRO[lang], lang
        assert loc["topics"] == CP.WORDING[lang], lang
    assert set(CP.WORDING["en"]) == set(CP.TOPICS) == set(CP.WORDING["zh-CN"])


# -- tokens ----------------------------------------------------------------------------------------

def test_token_sign_verify_expiry_tamper(env):
    CP = env["CP"]
    t = CP.make_token(ANNA)
    assert CP.verify_token(t) == CP.uid(ANNA) and CP.account_for_token(t) == ANNA
    assert ANNA not in t and "anna" not in base64.urlsafe_b64decode(t.split(".")[0] + "==").decode()   # no address in the URL
    now = time.time()
    assert CP.verify_token(CP.make_token(ANNA, ttl=60, now=now - 120)) is None                       # expired
    assert CP.verify_token(CP.make_token(ANNA, ttl=60, now=now)) is not None
    assert CP.TOKEN_TTL_S == 90 * 24 * 3600
    assert CP.verify_token(CP.make_token(ANNA, purpose="other")) is None                              # wrong purpose
    payload, sig = t.split(".")
    forged = base64.urlsafe_b64encode(json.dumps({"u": CP.uid(BOB), "p": "prefs", "e": int(now) + 999}).encode()).rstrip(b"=").decode()
    for bad in (t[:-2] + ("AA" if not t.endswith("AA") else "BB"), forged + "." + sig, payload, "", ".", "a.b", None, "x" * 5000):
        assert CP.verify_token(bad) is None, bad
    # a different key (secret) does not verify
    env["mp"].setenv("COMM_PREFS_SECRET", "another-secret")
    assert CP.verify_token(t) is None
    t2 = CP.make_token(ANNA)
    assert CP.account_for_token(t2) == ANNA
    env["mp"].delenv("COMM_PREFS_SECRET")
    assert CP.verify_token(t2) is None
    # an account that no longer exists resolves to nothing
    t3 = CP.make_token("gone@example.com")
    assert CP.verify_token(t3) and CP.account_for_token(t3) is None


# -- routes: by link ----------------------------------------------------------------------------------

def test_token_routes_read_and_change_without_a_session(env):
    CP = env["CP"]
    tok = CP.make_token(ANNA)
    r = client.get(f"/api/comm-prefs/{tok}")
    assert r.status_code == 200 and r.json()["account"] == "a***@example.com" and ANNA not in r.text
    assert all(v is False for v in r.json()["topics"].values())
    r = client.put(f"/api/comm-prefs/{tok}", json={"topics": {"new_motors": True, "co_development": True}, "lang": "zh-CN"},
                   headers={"X-Forwarded-For": "198.51.100.77"})
    assert r.status_code == 200 and r.json()["topics"]["new_motors"] and r.json()["topics"]["co_development"]
    rows = CP.history(ANNA)
    assert {r["source"] for r in rows} == {"email_link"} and rows[0]["lang"] == "zh-CN"
    assert client.put(f"/api/comm-prefs/{tok}", json={"topics": {"bogus": True}}).status_code == 422
    assert client.put(f"/api/comm-prefs/{tok}", json={"topics": {"new_motors": "yes"}}).status_code == 422
    assert client.put(f"/api/comm-prefs/{tok}", json={}).status_code == 422
    assert len(CP.history(ANNA)) == 2
    # a token opens ONLY its own account
    assert CP.get(BOB)["topics"] == {t: False for t in CP.TOPICS}


def test_bad_tokens_are_404_and_rate_limited(env):
    CP = env["CP"]
    assert client.get("/api/comm-prefs/not.a-token").status_code == 404
    assert client.post("/api/comm-prefs/not.a-token/unsubscribe").status_code == 404
    expired = CP.make_token(ANNA, ttl=1, now=time.time() - 100)
    assert client.get(f"/api/comm-prefs/{expired}").status_code == 404
    codes = [client.get(f"/api/comm-prefs/junk{i}", headers={"X-Forwarded-For": "203.0.113.200"}).status_code for i in range(40)]
    assert 429 in codes and codes[0] == 404
    # the limiter counts bad links per address: a good link from another address still works
    assert client.get(f"/api/comm-prefs/{CP.make_token(ANNA)}").status_code == 200


def test_one_click_unsubscribe_switches_everything_off(env):
    CP, N = env["CP"], env["N"]
    CP.set_topics(ANNA, {"product_updates": True, "engineering_content": True}, source="prefs_page")
    tok = CP.make_token(ANNA)
    # RFC 8058: the provider POSTs List-Unsubscribe=One-Click as a form, no session
    r = client.post(f"/api/comm-prefs/{tok}/unsubscribe", data={"List-Unsubscribe": "One-Click"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    st = CP.get(ANNA)
    assert st["unsubscribed_all"] is True and not any(st["topics"].values()) and st["unsubscribed_at"]
    assert CP.is_unsubscribed_all(ANNA) and not CP.opted_in(ANNA, "product_updates")
    rows = [r for r in CP.history(ANNA) if r["event"] == "unsubscribe_all"]
    assert {r["topic"] for r in rows} == {"product_updates", "engineering_content", "*"} and all(r["source"] == "unsubscribe" for r in rows)
    n = len(CP.history(ANNA))
    assert client.post(f"/api/comm-prefs/{tok}/unsubscribe").status_code == 200                 # idempotent
    assert len(CP.history(ANNA)) == n
    # an account that never ticked anything can still say "no more": the wish is stored
    assert client.post(f"/api/comm-prefs/{CP.make_token(BOB)}/unsubscribe").status_code == 200
    assert CP.get(BOB)["unsubscribed_all"] is True
    # ticking a topic later is a fresh consent and clears the flag
    client.put(f"/api/comm-prefs/{tok}", json={"topics": {"new_motors": True}})
    assert CP.get(ANNA)["unsubscribed_all"] is False and CP.opted_in(ANNA, "new_motors")
    assert N.status(ANNA)["status"] == "none"


def test_unsubscribe_ends_a_legacy_newsletter_subscription_and_back(env):
    CP, N = env["CP"], env["N"]
    N.record_pending(ANNA, source="settings")
    d = N._load()
    d["subscribers"][ANNA].update(status="confirmed")
    N._save(d)
    CP.set_topics(ANNA, {"new_motors": True}, source="prefs_page")
    client.post(f"/api/comm-prefs/{CP.make_token(ANNA)}/unsubscribe")
    assert N.status(ANNA)["status"] == "unsubscribed"
    # and the legacy one-click link switches the new topics off too
    CP.set_topics(BOB, {"new_motors": True}, source="prefs_page")
    d = N._load()
    d["subscribers"][BOB] = {"status": "confirmed"}
    N._save(d)
    assert N.unsubscribe_by_token(N.unsubscribe_token(BOB))
    assert CP.get(BOB)["unsubscribed_all"] is True and not CP.opted_in(BOB, "new_motors")


# -- routes: signed in ----------------------------------------------------------------------------------

def test_signed_in_put_records_prefs_page_source_and_ip(env):
    CP = env["CP"]
    h = _login(ANNA)
    r = client.put("/api/me/comm-prefs", json={"topics": {"engineering_content": True}, "lang": "en"}, headers={**h, "X-Forwarded-For": "198.51.100.9"})
    assert r.status_code == 200 and r.json()["topics"]["engineering_content"] is True
    row = CP.history(ANNA)[-1]
    assert row["source"] == "prefs_page" and row["topic"] == "engineering_content"
    r = client.put("/api/me/comm-prefs", json={"unsubscribe_all": True}, headers=h)
    assert r.status_code == 200 and r.json()["unsubscribed_all"] is True
    assert client.put("/api/me/comm-prefs", json={"topics": {"x": True}}, headers=h).status_code == 422
    assert client.put("/api/me/comm-prefs", json={"topics": {"new_motors": True}}).status_code == 401


def test_nda_state_reported_for_the_call_to_action(env):
    CP, U = env["CP"], env["U"]
    tok = CP.make_token(ANNA)
    assert client.get(f"/api/comm-prefs/{tok}").json()["nda"] == {"signed": False, "signable": False}   # no legal folder installed: request path
    U.set_nda(ANNA, True, by="admin")
    assert client.get(f"/api/comm-prefs/{tok}").json()["nda"]["signed"] is True
    from motor_ai_sim import nda as N
    env["mp"].setattr(N, "current", lambda lang: {"signable": True})
    assert client.get(f"/api/comm-prefs/{CP.make_token(BOB)}").json()["nda"] == {"signed": False, "signable": True}


# -- sign-up ---------------------------------------------------------------------------------------------

def test_signup_records_ticked_topics_only_and_nothing_by_default(env):
    CP = env["CP"]
    r = client.post("/api/auth/register", json={"email": "new1@example.com", "password": PW, "name": "N1"},
                    headers={"X-Forwarded-For": "203.0.113.31"})
    assert r.status_code == 202 and CP.history("new1@example.com") == []
    assert CP.get("new1@example.com")["topics"] == {t: False for t in CP.TOPICS}
    r = client.post("/api/auth/register",
                    json={"email": "new2@example.com", "password": PW, "name": "N2", "lang": "zh-CN",
                          "comm_prefs": {"new_motors": True, "co_development": True, "product_updates": False}},
                    headers={"X-Forwarded-For": "203.0.113.32"})
    assert r.status_code == 202
    rows = CP.history("new2@example.com")
    assert {(x["topic"], x["value"], x["source"], x["lang"]) for x in rows} == {
        ("new_motors", True, "signup", "zh-CN"), ("co_development", True, "signup", "zh-CN")}
    assert CP.get("new2@example.com")["topics"]["new_motors"] is True
    # a bad topic is refused before any account is made
    r = client.post("/api/auth/register", json={"email": "new3@example.com", "password": PW, "name": "N3", "comm_prefs": {"spam": True}},
                    headers={"X-Forwarded-For": "203.0.113.33"})
    assert r.status_code == 422 and env["U"].get_user("new3@example.com") is None
    # the legacy checkbox flag no longer subscribes anyone to anything new
    assert CP.get("new3@example.com")["topics"] == {t: False for t in CP.TOPICS}


def test_unverified_signup_is_never_mailed_even_with_ticked_boxes(env):
    CP, M = env["CP"], __import__("motor_ai_sim.mailing", fromlist=["x"])
    client.post("/api/auth/register", json={"email": "new4@example.com", "password": PW, "name": "N4", "comm_prefs": {"new_motors": True}},
                headers={"X-Forwarded-For": "203.0.113.34"})
    rcpt, skipped = M.collect_audience(M.load_users(), locales={})
    assert "new4@example.com" not in [r.email for r in rcpt] and ("new4@example.com", "unverified") in skipped


# -- the mailing tool -------------------------------------------------------------------------------------

def _script():
    spec = importlib.util.spec_from_file_location("send_announcement", _ROOT / "scripts" / "send_announcement.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture()
def mail(env):
    from motor_ai_sim import mailing as M
    S = _script()
    env["U"].create_user("unver@example.com", "password-unver")
    users = env["U"]._load()
    users["unver@example.com"]["email_verified"] = False
    users[BOB]["disabled"] = True
    env["U"]._save(users)
    _set_locale(env, LI, "zh-CN")
    env["M"], env["S"] = M, S
    return env


def run(env, *args):
    sleeps: list = []
    code = env["S"].main(list(args), deliver=lambda m: env["delivered"].append(m), sleep=sleeps.append)
    return code, sleeps


def test_dry_run_is_the_default_renders_one_eml_per_user_and_sends_nothing(mail, capsys):
    out = mail["tmp"] / "out"
    code, sleeps = run(mail, "--out-dir", str(out))
    assert code == 0 and mail["delivered"] == [] and sleeps == []
    files = sorted(p.name for p in out.glob("*.eml"))
    # verified + enabled: admin, anna, li (bob disabled, unver unverified)
    assert len(files) == 3 and any(f.endswith("anna_example.com.en.eml") for f in files) and any(f.endswith("li_example.com.zh-CN.eml") for f in files)
    text = capsys.readouterr().out
    assert "would send 3" in text and "DRY RUN" in text and "--send --confirm 3" in text
    assert "skipped 1: disabled" in text and "skipped 1: unverified" in text
    assert not (mail["tmp"] / "mailing").exists()                   # a dry run writes no send log: it never counts as sent
    m = emaillib.message_from_bytes((out / next(f for f in files if "li_example" in f)).read_bytes(), policy=policy.default)
    assert "10 月 19 日" in m["Subject"] and m["To"] == LI


def test_each_email_carries_personal_links_and_one_click_headers(mail):
    CP = mail["CP"]
    out = mail["tmp"] / "out"
    run(mail, "--out-dir", str(out))
    msg = emaillib.message_from_bytes(next(out.glob("*anna_example.com.en.eml")).read_bytes(), policy=policy.default)
    unsub = msg["List-Unsubscribe"]
    assert unsub.startswith("<https://aerostator.com/api/comm-prefs/") and unsub.endswith("/unsubscribe>")     # raw, not an encoded word
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert msg["Message-ID"] and msg["Date"] and msg["From"] == "noreply@aerostator.com"
    tok = unsub[len("<https://aerostator.com/api/comm-prefs/"):-len("/unsubscribe>")]
    assert CP.account_for_token(tok) == ANNA
    text = msg.get_body(("plain",)).get_content()
    html = msg.get_body(("html",)).get_content()
    assert "Hello Anna Test," in text and "https://aerostator.com/preferences?t=" in text and "&action=unsubscribe" in text
    assert "https://aerostator.com" in text and "MOTRES d.o.o., Kotnikova 34, 1000 Ljubljana, Slovenia" in text
    for needle in ("From idea to product, together.", "Connect your AI agents", "Bring your own compute", "NDA", "Choose what you hear from us",
                   "STEP", "Open AeroStator", "System graph", "Co-development", "FEM"):
        assert needle in text, needle
    # the unsubscribe link is on the page, not a GET that changes anything: the page POSTs it
    assert 'href="https://aerostator.com/preferences?t=' in html and "action=unsubscribe" in html
    zh = emaillib.message_from_bytes(next(out.glob("*li_example.com.zh-CN.eml")).read_bytes(), policy=policy.default)
    zt = zh.get_body(("plain",)).get_content()
    assert "李，您好" in zt and "https://aerostator.com/zh-cn/preferences?t=" in zt and "https://aerostator.com/zh-cn/" in zt
    assert "从创意到产品，携手同行" in zt and "连接您的 AI 智能体" in zt


def test_email_html_has_no_external_images_or_trackers_and_no_unpublished_matter(mail):
    out = mail["tmp"] / "out"
    run(mail, "--out-dir", str(out))
    for f in out.glob("*.eml"):
        m = emaillib.message_from_bytes(f.read_bytes(), policy=policy.default)
        html = m.get_body(("html",)).get_content()
        assert "<img" not in html and "<script" not in html and "<link" not in html and "src=" not in html and "url(" not in html
        hosts = {h.split("/")[0] for h in __import__("re").findall(r'href="https?://([^"]+)"', html)}
        assert hosts <= {"aerostator.com", "emotres.com"}, hosts
    for p in (_ROOT / "config" / "mail").glob("*.md"):
        txt = p.read_text(encoding="utf-8").lower()
        for banned in ("l155", "ø200", "patent", "pct", "unpublished", "ciano"):
            assert banned not in txt, (p.name, banned)


def test_users_who_unsubscribed_are_skipped(mail, capsys):
    CP, N = mail["CP"], mail["N"]
    CP.unsubscribe_all(ANNA, source="unsubscribe")
    d = N._load()
    d["subscribers"][LI] = {"status": "unsubscribed"}
    N._save(d)
    run(mail, "--out-dir", str(mail["tmp"] / "out"))
    text = capsys.readouterr().out
    assert "would send 1" in text and "skipped 1: unsubscribed" in text and "skipped 1: newsletter_unsubscribed" in text
    assert [p.name for p in (mail["tmp"] / "out").glob("*.eml")] == ["001_owner_example.com.en.eml"]


def test_send_requires_confirm_with_the_exact_count(mail, capsys):
    for args in (["--send"], ["--send", "--confirm", "2"], ["--send", "--confirm", "4"], ["--send", "--confirm", "0"]):
        code, _ = run(mail, *args)
        assert code == 2, args
    assert mail["delivered"] == [] and not (mail["tmp"] / "mailing").exists()
    assert "--confirm 3" in capsys.readouterr().err
    code, sleeps = run(mail, "--send", "--confirm", "3", "--delay", "3")
    assert code == 0 and len(mail["delivered"]) == 3 and sleeps == [3.0, 3.0]                  # rate limit between messages, not after the last
    assert {m["To"] for m in mail["delivered"]} == {ADMIN, ANNA, LI}


def test_send_is_idempotent_per_campaign_and_logs_user_time_message_id(mail, capsys):
    run(mail, "--send", "--confirm", "3")
    log = mail["tmp"] / "mailing" / "launch_2026-10-19.sendlog.jsonl"
    rows = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    sent = [r for r in rows if r["status"] == "sent"]
    assert len(sent) == 3 and len(rows) == 6                              # an attempt line before each send, a sent line after it
    assert all(r["campaign"] == "launch_2026-10-19" and r["user"] and r["ts"] and r["message_id"].startswith("<") for r in sent)
    assert {r["message_id"] for r in sent} == {m["Message-ID"] for m in mail["delivered"]}
    capsys.readouterr()
    # second run, same campaign: nobody is mailed again, and the old count is no longer valid
    code, _ = run(mail, "--send", "--confirm", "3")
    assert code == 2 and len(mail["delivered"]) == 3
    code, _ = run(mail, "--send", "--confirm", "0")
    assert code == 0 and len(mail["delivered"]) == 3
    # a new account that registers later is the only one a re-run would mail
    mail["U"].create_user("late@example.com", "password-late")
    code, _ = run(mail, "--send", "--confirm", "1")
    assert code == 0 and len(mail["delivered"]) == 4 and mail["delivered"][-1]["To"] == "late@example.com"
    # another campaign id is a different mailing: needs its own template
    assert run(mail, "--campaign", "nope_2027")[0] == 2


def test_unresolved_attempt_is_not_retried_blindly_and_failures_stop_the_run(mail):
    M = mail["M"]
    log = mail["tmp"] / "mailing" / "launch_2026-10-19.sendlog.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(json.dumps({"campaign": "launch_2026-10-19", "user": ANNA, "status": "attempt", "ts": 1}) + "\n", encoding="utf-8")
    p = M.plan("launch_2026-10-19", *M.collect_audience(M.load_users(), locales={}), M.read_log(log))
    assert ANNA not in [r.email for r in p.to_send] and (ANNA, "unresolved_attempt_check_manually") in p.skipped
    # five failures in a row abort the run; earlier failures are not retried unless asked
    log.unlink()
    for i in range(6):
        mail["U"].create_user(f"extra{i}@example.com", "password-extra")
    calls = []
    def boom(msg):
        calls.append(msg["To"])
        raise smtplib.SMTPAuthenticationError(535, b"bad login")
    code = mail["S"].main(["--send", "--confirm", "9"], deliver=boom, sleep=lambda s: None)
    assert code == 1 and len(calls) == 5
    rows = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    assert sum(1 for r in rows if r["status"] == "failed") == 5
    p = M.plan("launch_2026-10-19", *M.collect_audience(M.load_users(), locales={}), M.read_log(log))
    assert len(p.to_send) == 4 and any(why == "failed_before_use_retry_failed" for _, why in p.skipped)
    p = M.plan("launch_2026-10-19", *M.collect_audience(M.load_users(), locales={}), M.read_log(log), retry_failed=True)
    assert len(p.to_send) == 9


def test_test_to_sends_one_copy_to_that_address_only(mail, capsys):
    code, _ = run(mail, "--test-to", "me@elsewhere.example")
    assert code == 0 and len(mail["delivered"]) == 1 and mail["delivered"][0]["To"] == "me@elsewhere.example"
    assert mail["delivered"][0]["Subject"].startswith("[TEST] ")
    assert not (mail["tmp"] / "mailing").exists()                    # a test never counts as sent
    code, _ = run(mail, "--test-to", ANNA)                              # an account: its own links work
    CP = mail["CP"]
    msg = mail["delivered"][-1]
    assert CP.account_for_token(msg["List-Unsubscribe"].split("/api/comm-prefs/")[1].split("/")[0]) == ANNA
    assert run(mail, "--test-to", "not-an-address")[0] == 2
    assert len(mail["delivered"]) == 2


def test_send_refuses_without_smtp_and_dry_run_works_without_it(mail, monkeypatch):
    monkeypatch.delenv("SMTP_USER")
    monkeypatch.delenv("SMTP_PASS")
    assert run(mail, "--send", "--confirm", "3")[0] == 2 and mail["delivered"] == []
    assert run(mail, "--out-dir", str(mail["tmp"] / "o2"))[0] == 0


def test_concurrent_send_lock(mail):
    log = mail["tmp"] / "mailing" / "launch_2026-10-19.sendlog.jsonl"
    log.parent.mkdir(parents=True)
    Path(str(log) + ".lock").write_text("", encoding="utf-8")
    assert run(mail, "--send", "--confirm", "3")[0] == 2 and mail["delivered"] == []


def test_language_choice_ui_language_then_last_prefs_language_then_english(mail):
    M, CP = mail["M"], mail["CP"]
    CP.set_topics(ANNA, {"new_motors": True}, source="prefs_page", lang="zh-CN")
    rcpt, _ = M.collect_audience(M.load_users())
    langs = {r.email: r.lang for r in rcpt}
    assert langs[LI] == "zh-CN" and langs[ANNA] == "zh-CN" and langs[ADMIN] == "en"
    rcpt, _ = M.collect_audience(M.load_users(), locales={LI: "en"})
    assert {r.email: r.lang for r in rcpt}[LI] == "en"


def test_template_errors_are_loud(mail, tmp_path, monkeypatch):
    M = mail["M"]
    monkeypatch.setenv("MAIL_TEMPLATE_DIR", str(tmp_path / "t"))
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "c1.en.md").write_text("no subject line\n\nhi", encoding="utf-8")
    (tmp_path / "t" / "c2.en.md").write_text("Subject: s\n\nhi {{surprise}}", encoding="utf-8")
    with pytest.raises(ValueError):
        M.load_template("c1", "en")
    with pytest.raises(ValueError):
        M.render("c2", "en", email=ANNA, name="A")
    with pytest.raises(ValueError):
        M.check_campaign("../etc/passwd")


def test_a_name_with_markup_cannot_inject_html_or_headers(mail):
    M = mail["M"]
    m = M.build_message("launch_2026-10-19", "en", email=ANNA, name='<script>alert(1)</script>\r\nBcc: evil@example.com')
    assert "Bcc" not in m and "<script>" not in m.get_body(("html",)).get_content()


# -- admin, lifecycle ------------------------------------------------------------------------------------------

def test_admin_summary_counts_and_last_campaign(mail):
    CP = mail["CP"]
    CP.set_topics(ANNA, {"new_motors": True, "product_updates": True}, source="prefs_page")
    CP.set_topics(LI, {"new_motors": True}, source="prefs_page")
    CP.unsubscribe_all(BOB, source="unsubscribe")
    ha = _login(ADMIN)
    r = client.get("/api/admin/comm-prefs/summary", headers=ha)
    assert r.status_code == 200
    j = r.json()
    assert j["topics"] == {"product_updates": 1, "new_motors": 2, "engineering_content": 0, "co_development": 0}
    assert j["any_topic"] == 2 and j["unsubscribed_all"] == 1 and j["campaign"] is None
    run(mail, "--send", "--confirm", "3")
    c = client.get("/api/admin/comm-prefs/summary", headers=ha).json()["campaign"]
    assert c["campaign"] == "launch_2026-10-19" and c["sent"] == 3 and c["failed"] == 0 and c["last"] >= c["first"]
    assert client.get("/api/admin/comm-prefs/summary", headers=_login(ANNA)).status_code in (401, 403)
    assert client.get("/api/admin/comm-prefs/summary").status_code in (401, 403)


def test_account_deletion_removes_state_and_pseudonymises_the_proof(env):
    CP = env["CP"]
    CP.set_topics(ANNA, {"new_motors": True}, source="prefs_page", ip="198.51.100.4")
    CP.set_topics(BOB, {"new_motors": True}, source="prefs_page")
    n = CP.erase(ANNA, "subj_deadbeef")
    assert n >= 2 and CP.get(ANNA)["topics"]["new_motors"] is False
    assert CP.history(ANNA) == []
    rows = CP.history("subj_deadbeef")
    assert len(rows) == 1 and rows[0]["ip"].startswith("ip:") and "198.51.100.4" not in json.dumps(rows)
    assert ANNA not in (env["tmp"] / "comm_prefs_history.jsonl").read_text(encoding="utf-8")
    assert CP.get(BOB)["topics"]["new_motors"] is True
    assert CP.export_for(BOB)["history"][0]["topic"] == "new_motors"
