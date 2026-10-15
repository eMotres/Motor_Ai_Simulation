"""The links in the account mails open small SERVER-rendered pages: no JavaScript, no external asset, EN + zh-CN, idempotent.

Why (2026-10-15): a visitor in China clicked the confirmation link but the single-page app's 1.2 MB bundle aborted on the China ->
Germany line, so the browser-side confirmation never ran.  Everything here is plain HTTP + HTML: GET to confirm, an HTML form POST to
set a password or ask for a new link.  Temp store only; no real mail.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

from motor_ai_sim import auth as A
from motor_ai_sim import link_pages as LP
from tests.test_email_password_auth import ADMIN, NEW, PW, _login, _register, _smtp_on, _token_from, client, env  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
LINK = "/api/auth/link"


def sid_of(mail, kind="verify"):
    return _token_from(mail, kind)


def assert_light(page: str):
    """No JavaScript, no external asset, small."""
    low = page.lower()
    assert len(page.encode("utf-8")) < 10_000, len(page.encode("utf-8"))
    assert "<script" not in low and "src=" not in low and "<link" not in low and "@import" not in low and "url(" not in low
    assert "javascript:" not in low and " onclick" not in low and " onload" not in low
    hosts = set(re.findall(r"""(?:href|action)=["'](https?://[^/"']+)""", page))
    assert hosts <= {"http://localhost:5173"}, hosts                       # only links back to the app itself


# -- the mail -------------------------------------------------------------------------------------------------------

def test_the_mailed_link_is_short_and_on_its_own_line(env):
    _smtp_on(env)
    _register()
    body = env["outbox"][-1]["body"]
    links = [ln for ln in body.splitlines() if "/api/auth/link/v/" in ln]
    assert len(links) == 1 and links[0] == links[0].strip() and links[0].startswith("http")
    assert len(links[0]) <= 60, len(links[0])                              # does not wrap in a mail client
    assert "?verify=" not in body and "eyJ" not in body                    # no long JWT in the mail
    assert "Confirm" in env["outbox"][-1]["subject"] and "请" not in body  # an English address: English only


def test_chinese_line_under_the_english_for_zh_users_and_chinese_providers(env):
    _smtp_on(env)
    client.post("/api/auth/register", json={"email": "a@gmail.com", "password": PW, "name": "A", "lang": "zh-CN"},
                headers={"X-Forwarded-For": "203.0.113.41"})
    client.post("/api/auth/register", json={"email": "b@qq.com", "password": PW, "name": "B", "lang": "en"},
                headers={"X-Forwarded-For": "203.0.113.42"})
    client.post("/api/auth/register", json={"email": "c@gmail.com", "password": PW, "name": "C", "lang": "en"},
                headers={"X-Forwarded-For": "203.0.113.43"})
    a, b, c = env["outbox"][-3:]
    for m in (a, b):                                                       # stored zh, or a qq.com address
        assert "Confirm your e-mail address / 请确认您的邮箱" == m["subject"]
        assert "confirm this address" in m["body"] and "请点击上面的链接确认此邮箱" in m["body"]
        assert m["body"].index("confirm this address") < m["body"].index("请点击")          # Chinese under the English
        assert sum("/api/auth/link/v/" in ln for ln in m["body"].splitlines()) == 1        # the link appears once
    assert "请" not in c["body"] and c["subject"] == "Confirm your e-mail address"
    # the reset mail too
    env["outbox"].clear()
    client.post("/api/auth/reset/request", json={"email": "b@qq.com"})
    r = env["outbox"][-1]
    assert r["subject"] == "Reset your password / 重置密码" and "/api/auth/link/r/" in r["body"] and "请点击上面的链接设置新密码" in r["body"]
    assert len(next(ln for ln in r["body"].splitlines() if "/api/auth/link/r/" in ln)) <= 60


# -- confirming -------------------------------------------------------------------------------------------------------

def test_the_link_confirms_on_the_server_and_a_second_click_is_harmless(env):
    _smtp_on(env)
    _register()
    assert _login().status_code == 403                                     # not confirmed yet (this PR does not change that)
    sid = sid_of(env["outbox"][-1])
    r = client.get(f"{LINK}/v/{sid}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "E-mail confirmed" in r.text and NEW in r.text and 'href="http://localhost:5173/"' in r.text
    assert "signin=1" in r.text and 'lang="en"' in r.text
    assert_light(r.text)
    assert env["U"].is_verified(env["U"].get_user(NEW))
    assert _login().status_code == 200
    # a mail scanner opened it first / the user clicks twice: same page, "already confirmed", nothing breaks
    again = client.get(f"{LINK}/v/{sid}")
    assert again.status_code == 200 and "Already confirmed" in again.text and NEW in again.text
    assert_light(again.text)
    # the page cannot be framed or cached and sends no referrer; only inline style is allowed
    h = r.headers
    assert h["cache-control"] == "no-store" and h["x-frame-options"] == "DENY" and h["referrer-policy"] == "no-referrer"
    assert "default-src 'none'" in h["content-security-policy"] and "script" not in h["content-security-policy"]


def test_zh_rendering_by_account_language_accept_language_query_and_provider(env):
    _smtp_on(env)
    client.post("/api/auth/register", json={"email": "zh1@gmail.com", "password": PW, "name": "Z", "lang": "zh-CN"},
                headers={"X-Forwarded-For": "203.0.113.51"})
    r = client.get(f"{LINK}/v/{sid_of(env['outbox'][-1])}")                # stored language zh-CN
    assert 'lang="zh-CN"' in r.text and "邮箱已确认 ✓" in r.text and "打开 AeroStator" in r.text and 'href="http://localhost:5173/zh-cn/"' in r.text
    assert "English" in r.text and "?lang=en" in r.text                    # the switch to the other language
    assert_light(r.text)
    # a stored "en" does not beat a Chinese browser (the sign-up form always sends the interface language)
    client.post("/api/auth/register", json={"email": "zh2@gmail.com", "password": PW, "name": "Z", "lang": "en"},
                headers={"X-Forwarded-For": "203.0.113.52"})
    r = client.get(f"{LINK}/v/{sid_of(env['outbox'][-1])}", headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    assert "邮箱已确认" in r.text
    client.post("/api/auth/register", json={"email": "en1@gmail.com", "password": PW, "name": "E", "lang": "en"},
                headers={"X-Forwarded-For": "203.0.113.53"})
    sid = sid_of(env["outbox"][-1])
    assert "E-mail confirmed" in client.get(f"{LINK}/v/{sid}", headers={"Accept-Language": "en-US,en;q=0.9"}).text
    assert "邮箱已确认" in client.get(f"{LINK}/v/{sid}?lang=zh").text      # explicit choice (the spent link: "already confirmed")
    # a Chinese mail provider with nothing else to go on
    assert LP.pick_lang(email="x@163.com") == "zh-CN" and LP.pick_lang(email="x@gmail.com") == "en"
    assert LP.pick_lang(query="en", stored="zh-CN", email="x@qq.com") == "en"


def test_expired_unknown_and_forged_links_show_the_resend_page(env):
    _smtp_on(env)
    _register()
    U = env["U"]
    old = U.issue_short_link(NEW, "verify", ttl_s=-5)
    for sid in (old, "AAAAAAAAAAAAAAAA", "x", sid_of(env["outbox"][-1])[:-1] + "_"):
        r = client.get(f"{LINK}/v/{sid}")
        assert r.status_code == 200 and 'method="post"' in r.text and 'action="/api/auth/link/resend"' in r.text
        assert "expired" in r.text and not U.is_verified(U.get_user(NEW)), sid
        assert_light(r.text)
    # an expired link of a known account prefills the address
    assert f'value="{NEW}"' in client.get(f"{LINK}/v/{old}").text
    # the resend form is a plain POST and mails a new link; the answer is the same page whoever is asked about
    n = len(env["outbox"])
    r = client.post(f"{LINK}/resend", data={"email": NEW, "kind": "verify"})
    assert r.status_code == 200 and "Check your inbox" in r.text and len(env["outbox"]) == n + 1
    assert client.get(f"{LINK}/v/{sid_of(env['outbox'][-1])}").text.count("E-mail confirmed") >= 1
    r2 = client.post(f"{LINK}/resend", data={"email": "nobody@example.com", "kind": "verify"})
    assert r2.status_code == 200 and "Check your inbox" in r2.text and len(env["outbox"]) == n + 1
    assert client.post(f"{LINK}/resend", data={"email": "not an address"}).status_code == 200


def test_resend_form_is_rate_limited_and_bad_links_count(env):
    _smtp_on(env)
    _register()
    n = len(env["outbox"])
    for _ in range(5):
        assert client.post(f"{LINK}/resend", data={"email": NEW, "kind": "verify"}).status_code == 200
    assert len(env["outbox"]) == n + 2                                      # 3 an hour per address in all (one went out at sign-up)
    codes = [client.get(f"{LINK}/v/BADBADBADBADBAD{i}", headers={"X-Forwarded-For": "203.0.113.99"}).status_code for i in range(40)]
    assert 429 in codes


def test_the_old_long_links_still_confirm_and_are_idempotent(env):
    _smtp_on(env)
    _register()
    U = env["U"]
    t = U.issue_link_token(NEW, "verify")                                   # what an old mail carried
    r = client.get(f"{LINK}/verify", params={"t": t})
    assert r.status_code == 200 and "E-mail confirmed" in r.text and U.is_verified(U.get_user(NEW))
    assert "Already confirmed" in client.get(f"{LINK}/verify", params={"t": t}).text
    bad = client.get(f"{LINK}/verify", params={"t": t[:-3] + "xyz"})
    assert "expired" in bad.text and 'action="/api/auth/link/resend"' in bad.text
    assert "expired" in client.get(f"{LINK}/verify").text
    # the JSON endpoints of the single-page app still work with either kind of token
    _register(email="j@example.com", ip="203.0.113.60")
    assert client.post("/api/auth/verify", json={"token": sid_of(env["outbox"][-1])}).status_code == 200
    assert client.post("/api/auth/verify", json={"token": sid_of(env["outbox"][-1])}).status_code == 400


def test_nginx_redirects_the_old_spa_links_before_any_javascript():
    conf = (ROOT / "deploy" / "nginx.conf").read_text(encoding="utf-8")
    assert re.search(r"location = / \{[^}]*\$arg_verify[^}]*return 302 /api/auth/link/verify\?t=\$arg_verify;", conf, re.S)
    assert "return 302 /api/auth/link/reset?t=$arg_reset;" in conf
    assert "return 302 /api/auth/link/verify?t=$arg_verify&continue=$arg_continue;" in conf       # an old sign-up from an AI app
    assert conf.count("absolute_redirect off;") >= 2                                              # TLS ends on the host: relative redirect


def test_the_link_routes_are_open_to_an_anonymous_visitor_on_a_closed_server():
    for p in ("/api/auth/link/v/abc", "/api/auth/link/r/abc", "/api/auth/link/verify", "/api/auth/link/reset", "/api/auth/link/resend"):
        assert A.anonymous_allowed(p), p
    assert not A.anonymous_allowed("/api/auth/users")


# -- the reset form ---------------------------------------------------------------------------------------------------

def _reset_link(env, email=ADMIN):
    _smtp_on(env)
    client.post("/api/auth/reset/request", json={"email": email})
    return sid_of(env["outbox"][-1], "reset")


def test_reset_form_posts_without_javascript_and_the_link_is_burnt_only_by_a_good_password(env):
    sid = _reset_link(env)
    g = client.get(f"{LINK}/r/{sid}")
    assert g.status_code == 200 and '<form method="post" action="/api/auth/link/reset">' in g.text
    assert f'name="id" value="{sid}"' in g.text and ADMIN in g.text and 'type="password"' in g.text
    assert_light(g.text)
    old_tok = _login(ADMIN, "password-admin", ip="198.51.100.20").json()["token"]
    mismatch = client.post(f"{LINK}/reset", data={"id": sid, "password": "a brand new secret", "password2": "another one entirely"})
    assert mismatch.status_code == 200 and "do not match" in mismatch.text and 'role="alert"' in mismatch.text
    weak = client.post(f"{LINK}/reset", data={"id": sid, "password": "short", "password2": "short"})
    assert "At least" in weak.text or "10" in weak.text
    assert _login(ADMIN, "password-admin", ip="198.51.100.21").status_code == 200            # nothing changed, link still alive
    ok = client.post(f"{LINK}/reset", data={"id": sid, "password": "a brand new secret", "password2": "a brand new secret"})
    assert ok.status_code == 200 and "Password changed" in ok.text and "signin=1" in ok.text
    assert_light(ok.text)
    assert _login(ADMIN, "password-admin", ip="198.51.100.22").status_code == 401
    assert _login(ADMIN, "a brand new secret", ip="198.51.100.23").status_code == 200
    assert env["U"].verify_token(old_tok).reason == "revoked"                                 # every other session signed out
    # single use
    assert "expired or was already used" in client.get(f"{LINK}/r/{sid}").text
    assert "expired or was already used" in client.post(f"{LINK}/reset", data={"id": sid, "password": "yet another secret", "password2": "yet another secret"}).text


def test_reset_form_in_chinese_and_for_an_old_long_link(env):
    _smtp_on(env)
    U = env["U"]
    t = U.issue_link_token(ADMIN, "reset")
    g = client.get(f"{LINK}/reset", params={"t": t}, headers={"Accept-Language": "zh-CN"})
    assert "设置新密码" in g.text and "再次输入新密码" in g.text and f'name="t" value="{t}"' in g.text
    assert_light(g.text)
    mismatch = client.post(f"{LINK}/reset", data={"t": t, "password": "a brand new secret", "password2": "nope nope nope"},
                           headers={"Accept-Language": "zh-CN"})
    assert "两次输入的密码不一致" in mismatch.text
    assert U.peek_link_token(t, "reset") == ADMIN                                            # not burnt by a refused attempt
    weak = client.post(f"{LINK}/reset", data={"t": t, "password": "short", "password2": "short"}, headers={"Accept-Language": "zh-CN"})
    assert "密码不符合要求" in weak.text
    ok = client.post(f"{LINK}/reset", data={"t": t, "password": "a brand new secret", "password2": "a brand new secret"},
                     headers={"Accept-Language": "zh-CN"})
    assert "密码已修改" in ok.text and "登录" in ok.text
    assert _login(ADMIN, "a brand new secret", ip="198.51.100.30").status_code == 200
    assert "已过期" in client.post(f"{LINK}/reset", data={"t": t, "password": "x" * 12, "password2": "x" * 12}, headers={"Accept-Language": "zh-CN"}).text


def test_change_of_address_and_admin_edit_burn_the_old_short_links(env):
    _smtp_on(env)
    _register(email="d.xu@movigor.tech")
    old = sid_of(env["outbox"][-1])
    r = client.post("/api/auth/register/change-email", json={"email": "d.xu@movigor.tech", "password": PW, "new_email": "d.xu@movigor.com"},
                    headers={"X-Forwarded-For": "203.0.113.5"})
    assert r.status_code == 202
    assert "expired" in client.get(f"{LINK}/v/{old}").text                  # the old address's link is dead
    new = sid_of(env["outbox"][-1])
    assert env["outbox"][-1]["to"] == "d.xu@movigor.com" and "E-mail confirmed" in client.get(f"{LINK}/v/{new}").text


def test_page_budget_for_every_page_in_both_languages():
    pages = []
    for lang in ("en", "zh-CN"):
        pages += [LP.verified(lang, "someone.with.a.long.address@example-company.com", open_url="https://aerostator.com/", signin_url="https://aerostator.com/?signin=1",
                              continue_url="https://aerostator.com/agent-consent?request=abc", switch_url="/x?lang=zh"),
                  LP.invalid(lang, kind="verify", email="a@b.com", action="/api/auth/link/resend", switch_url="/x"),
                  LP.invalid(lang, kind="reset", email="a@b.com", action="/api/auth/link/resend"),
                  LP.sent(lang, open_url="https://aerostator.com/"),
                  LP.reset_form(lang, email="a@b.com", action="/api/auth/link/reset", fields={"t": "x" * 400}, error="boom <b>"),
                  LP.reset_done(lang, signin_url="https://aerostator.com/?signin=1", open_url="https://aerostator.com/")]
    for pg in pages:
        assert len(pg.encode("utf-8")) < 10_000 and "<script" not in pg.lower()
    assert "boom &lt;b&gt;" in pages[4] and "<b>" not in pages[4]                              # every dynamic value is escaped
    x = LP.verified("en", '"><script>alert(1)</script>', open_url="/", signin_url="/")
    assert "<script>" not in x and "&lt;script&gt;" in x
