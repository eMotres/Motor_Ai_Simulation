"""Shareable link to a production's documents page (owner 2026-10-19).

``/production/<die>/<config>/documents?since=2026-10-19`` is opened by engineers who may still have to register.  Under test:

* the return path is validated (allow-list of route shapes, no open redirect);
* it survives registration -> confirmation mail -> the server-rendered "confirmed" page (also a mail scanner's first click and
  a resend), and a hostile value is ignored without breaking the sign-up;
* the link grants NOTHING: a stranger gets a nameless "none" answer (same for a missing production), can ask for access through
  the ordinary ticket path (one ticket), and never sees a document name; a member registered by e-mail before the account
  existed lands with access; the factory role never sees internal documents.

Temp stores only; no real mail.
"""
from __future__ import annotations

import json
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim import return_path as RP
from motor_ai_sim.api import app
from tests.test_email_password_auth import ADMIN, NEW, PW, _register, _smtp_on, _token_from, env  # noqa: F401

client = TestClient(app)
LINK = "/api/auth/link"
DIE, CONFIG = "CIANO14 40 new", "L12"
PATH = "/production/CIANO14%2040%20new/L12/documents"
APP = "http://localhost:5173"
SECRET_NAME = "Rotor-secret-drawing-4711.pdf"
INTERNAL_NAME = "Internal-cost-sheet.xlsx"


def _plain(html: str) -> str:
    return html.replace("&amp;", "&")


# -- the validator ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (PATH, PATH),
    ("/production/CIANO14 40 new/L12/documents", PATH),                       # readable form is canonicalised
    (PATH + "?since=2026-10-19", PATH + "?since=2026-10-19"),
    (PATH + "?ids=doc_a1,doc-b2", PATH + "?ids=doc_a1,doc-b2"),
    (PATH + "?since=2026-10-19&signin=1&verify=abc&x=//evil.com", PATH + "?since=2026-10-19"),   # unknown keys dropped
    (PATH + "?since=2026-10-19#frag", PATH + "?since=2026-10-19"),                                 # fragment dropped
    ("/production/SiC%20Controller%20800V/Rev%20A/documents", "/production/SiC%20Controller%20800V/Rev%20A/documents"),
])
def test_valid_paths_are_canonical(raw, expected):
    assert RP.safe_app_path(raw) == expected


@pytest.mark.parametrize("raw", [
    None, 42, "", "   ", "production/a/b/documents",                          # not absolute
    "//evil.com/production/a/b/documents", "///evil.com", "/\\evil.com", "\\\\evil.com",
    "https://evil.com/production/a/b/documents", "http://localhost:5173/production/a/b/documents",
    "javascript:alert(1)", "data:text/html,x",
    "/", "/?verify=abc", "/agent-consent?request=1", "/admin", "/api/auth/logout", "/production", "/production/a/b",
    "/production/a/b/documents/extra", "/production/../admin/x/documents", "/production/a/..%2fb/documents",
    "/production/a%2Fb/c/documents", "/production/a%5Cb/c/documents", "/production/%2e%2e/b/documents",
    "/production//b/documents", "/production/a/b/documents?since=yesterday", "/production/a/b/documents?since=2026-13-45",
    "/production/a/b/documents?ids=" + ",".join("d%d" % i for i in range(60)),
    "/production/a/b/documents?ids=<script>", "/production/a/b/documents?ids=a//b",
    "/production/a/b/documents\r\nLocation: http://evil.com", "/production/a/b/documents\x00",
    "/production/" + "a" * 90 + "/b/documents", "/production/a/b/documents?" + "since=2026-10-19&" * 30,
])
def test_hostile_or_foreign_paths_are_refused(raw):
    assert RP.safe_app_path(raw) is None


def test_builder_round_trips_and_refuses_bad_parts():
    p = RP.production_documents_path(DIE, CONFIG, since="2026-10-19")
    assert p == PATH + "?since=2026-10-19"
    assert RP.safe_app_path(p) == p
    assert RP.production_documents_path(DIE, CONFIG, ids=["doc_1", "doc_2"]) == PATH + "?ids=doc_1,doc_2"
    assert RP.production_documents_path(DIE, "../x") is None
    assert RP.production_documents_path(DIE, CONFIG, since="soon") is None


# -- registration -> confirmation page -----------------------------------------------------------------------------------

def _register_next(nxt, email=NEW, ip="203.0.113.5"):
    return client.post("/api/auth/register", json={"email": email, "password": PW, "name": "New Person", "next": nxt},
                       headers={"X-Forwarded-For": ip})


def test_the_return_path_survives_register_and_confirmation(env):
    _smtp_on(env)
    nxt = PATH + "?since=2026-10-19"
    assert _register_next(nxt).status_code == 202
    sid = _token_from(env["outbox"][-1], "verify")
    # the mailed link stays short and carries no path (it is not a redirect; only the stored value is used)
    assert "production" not in env["outbox"][-1]["body"]
    page = client.get(f"{LINK}/v/{sid}")
    assert page.status_code == 200 and "E-mail confirmed" in page.text
    assert f'href="{APP}{nxt}"' in _plain(page.text)
    assert f"{APP}{nxt}&signin=1" in _plain(page.text)                  # the second button opens the sign-in dialog on it
    assert "Open the page you came for" in page.text
    assert env["U"].is_verified(env["U"].get_user(NEW))
    # a mail scanner clicked first / the person clicks again: still lands on the page
    again = client.get(f"{LINK}/v/{sid}")
    assert "Already confirmed" in again.text and f'href="{APP}{nxt}"' in _plain(again.text)


def test_zh_cn_page_has_the_label(env):
    _smtp_on(env)
    _register_next(PATH)
    sid = _token_from(env["outbox"][-1], "verify")
    page = client.get(f"{LINK}/v/{sid}?lang=zh")
    assert "打开您要访问的页面" in page.text and f'href="{APP}{PATH}"' in _plain(page.text)


@pytest.mark.parametrize("hostile", [
    "https://evil.example/phish", "//evil.example/x", "/\\evil.example", "javascript:alert(1)",
    "/production/a/b/documents/../../../admin", "/api/auth/logout", "/?signin=1&next=//evil.example",
])
def test_a_hostile_return_path_is_ignored_and_the_signup_still_works(env, hostile):
    _smtp_on(env)
    assert _register_next(hostile).status_code == 202
    sid = _token_from(env["outbox"][-1], "verify")
    page = client.get(f"{LINK}/v/{sid}")
    assert page.status_code == 200 and "E-mail confirmed" in page.text
    assert "evil" not in page.text and "production" not in page.text and "Open the page you came for" not in page.text
    assert f'href="{APP}/"' in page.text                                    # plain home, as before
    assert env["U"].is_verified(env["U"].get_user(NEW))


def test_a_poisoned_stored_value_is_revalidated_when_the_page_is_built(env):
    _smtp_on(env)
    _register_next(PATH)
    users = json.loads((env["tmp"] / "users.json").read_text())
    users[NEW]["short_links"][0]["nx"] = "https://evil.example/x"            # as if the store had been edited
    (env["tmp"] / "users.json").write_text(json.dumps(users))
    sid = _token_from(env["outbox"][-1], "verify")
    page = client.get(f"{LINK}/v/{sid}")
    assert "evil" not in page.text and f'href="{APP}/"' in page.text


def test_resend_keeps_the_return_path(env):
    _smtp_on(env)
    _register_next(PATH)
    r = client.post("/api/auth/register/resend", json={"email": NEW, "next": PATH + "?since=2026-10-19"},
                    headers={"X-Forwarded-For": "203.0.113.5"})
    assert r.status_code == 202
    sid = _token_from(env["outbox"][-1], "verify")
    page = client.get(f"{LINK}/v/{sid}")
    assert (PATH + "?since=2026-10-19") in _plain(page.text)


#: F2 (pre-launch review 2026-10-20): a SHORT allowed path (< MAX_LEN) whose query has more than parse_qsl's 20 fields
#: raised ValueError out of safe_app_path, and POST /api/auth/register answered 500.
_MANY_FIELDS = "/production/a/b/documents?" + "&".join("x=1" for _ in range(21))


def test_a_short_query_with_too_many_fields_is_refused_not_raised():
    assert len(_MANY_FIELDS) < RP.MAX_LEN
    assert RP.safe_app_path(_MANY_FIELDS) is None
    assert RP.safe_app_path(PATH + "?" + "&".join("x=%d" % i for i in range(25)) + "&since=2026-10-19") is None
    # exactly 20 fields is still parsed (the limit itself is unchanged)
    assert RP.safe_app_path(PATH + "?since=2026-10-19&" + "&".join("x=1" for _ in range(19))) == PATH + "?since=2026-10-19"


def test_too_many_query_fields_never_break_the_signup_or_the_resend(env):
    _smtp_on(env)
    r = TestClient(app, raise_server_exceptions=False).post(
        "/api/auth/register", json={"email": NEW, "password": PW, "name": "New Person", "next": _MANY_FIELDS},
        headers={"X-Forwarded-For": "203.0.113.5"})
    assert r.status_code == 202, r.text                                     # was 500
    r = TestClient(app, raise_server_exceptions=False).post(
        "/api/auth/register/resend", json={"email": NEW, "next": _MANY_FIELDS},
        headers={"X-Forwarded-For": "203.0.113.5"})
    assert r.status_code == 202, r.text
    sid = _token_from(env["outbox"][-1], "verify")
    page = client.get(f"{LINK}/v/{sid}")
    assert page.status_code == 200 and "E-mail confirmed" in page.text
    assert "Open the page you came for" not in page.text and f'href="{APP}/"' in page.text   # signed up, no return path
    assert env["U"].is_verified(env["U"].get_user(NEW))


def test_without_a_return_path_nothing_changes(env):
    _smtp_on(env)
    _register()
    sid = _token_from(env["outbox"][-1], "verify")
    page = client.get(f"{LINK}/v/{sid}")
    assert f'href="{APP}/"' in page.text and "Open the page you came for" not in page.text


# -- access: the link grants nothing ----------------------------------------------------------------------------------------

@pytest.fixture()
def prod(env, tmp_path, monkeypatch):
    from motor_ai_sim.production import models, store
    U = env["U"]
    monkeypatch.setenv("MOTRES_PRODUCTION_ROOT", str(tmp_path / "production"))
    monkeypatch.setenv("SUPPORT_STORE_DIR", str(tmp_path / "support"))
    (tmp_path / "production").mkdir()
    for email, role in (("prodowner@example.com", "user"), ("eng@example.com", "user"), ("factory@example.com", "oem"),
                        ("stranger@example.com", "user")):
        U.create_user(email, "password-xyz-123", role=role, name=email)
    U.set_nda("factory@example.com", True, "2026-10-01", by=ADMIN)
    production = models.normalize_production({
        "title": {"en": "Top secret motor title", "zh": "", "src_lang": "en", "status": {"en": "confirmed", "zh": "pending"}},
        "members": [{"email": "prodowner@example.com", "role": "owner"}, {"email": "eng@example.com", "role": "engineer"},
                    {"email": "factory@example.com", "role": "oem"}, {"email": "invited.later@example.com", "role": "engineer"}],
    }, DIE, CONFIG)
    store.save_file(DIE, CONFIG, "production.json", production)
    store.save_file(DIE, CONFIG, "documents.json", {"documents": [
        {"id": "doc_pub", "name": SECRET_NAME, "file_id": "f1", "uploader_email": "eng@example.com",
         "uploaded_at": "2026-10-19T08:00:00Z", "internal": False},
        {"id": "doc_int", "name": INTERNAL_NAME, "file_id": "f2", "uploader_email": "eng@example.com",
         "uploaded_at": "2026-10-19T09:00:00Z", "internal": True}]})

    def h(email):
        return {"Authorization": f"Bearer {U.issue_token(email)}"}
    return {"h": h, "base": f"/api/production/{quote(DIE)}/{CONFIG}"}


def test_member_roles_get_access_and_only_owner_and_admin_can_share(prod, env):
    base, h = prod["base"], prod["h"]
    for email, can_share, factory in (("prodowner@example.com", True, False), ("eng@example.com", False, False),
                                      ("factory@example.com", False, True), (ADMIN, True, False)):
        j = client.get(base + "/access", headers=h(email)).json()
        assert j["access"] == "member" and j["can_share"] is can_share and j["factory"] is factory, (email, j)


def test_a_stranger_gets_no_names_and_an_unknown_production_looks_the_same(prod):
    base, h = prod["base"], prod["h"]
    mine = client.get(base + "/access", headers=h("stranger@example.com"))
    other = client.get("/api/production/Nope/Nope/access", headers=h("stranger@example.com"))
    assert mine.status_code == other.status_code == 200
    assert mine.json() == other.json() == {"access": "none", "requested": False}
    blob = mine.text + other.text
    for secret in (SECRET_NAME, INTERNAL_NAME, "Top secret", "prodowner@example.com", "eng@example.com", "doc_pub"):
        assert secret not in blob
    # and every data route still refuses the stranger as before (the link opened nothing)
    for path in ("", "/documents", "/parts", "/files/f1"):
        r = client.get(base + path, headers=h("stranger@example.com"))
        assert r.status_code == 404 and SECRET_NAME not in r.text and "Top secret" not in r.text, path


def test_signed_out_gets_401(prod):
    assert client.get(prod["base"] + "/access").status_code == 401


def test_request_access_files_one_ticket_and_grants_nothing(prod):
    from motor_ai_sim import ticket_store as T
    base, h = prod["base"], prod["h"]
    for _ in range(2):
        r = client.post(base + "/request_access", headers=h("stranger@example.com"))
        assert r.status_code == 200 and r.json() == {"ok": True, "already_member": False}
    rows = [t for t in T.list_all() if t["email"] == "stranger@example.com"]
    assert len(rows) == 1 and rows[0]["type"] == "account" and "CIANO14 40 new" in rows[0]["title"]
    assert client.get(base + "/access", headers=h("stranger@example.com")).json() == {"access": "none", "requested": True}
    assert client.get(base + "/documents", headers=h("stranger@example.com")).status_code == 404       # still nothing
    # a member asking is told so and files nothing
    r = client.post(base + "/request_access", headers=h("eng@example.com"))
    assert r.json() == {"ok": True, "already_member": True}
    assert len([t for t in T.list_all() if t["email"] == "eng@example.com"]) == 0
    # a made-up production answers the same and the title cannot smuggle extra lines
    r = client.post("/api/production/No%3Cb%3Ex/Nope/request_access", headers=h("stranger@example.com"))
    assert r.json() == {"ok": True, "already_member": False}
    assert all("<" not in t["title"] and "\n" not in t["title"] for t in T.list_all())


def test_factory_role_sees_only_factory_visible_documents(prod):
    base, h = prod["base"], prod["h"]

    def names(who):
        return sorted(d["name"] for d in client.get(base + "/documents", headers=h(who)).json()["documents"])
    assert names("eng@example.com") == sorted([SECRET_NAME, INTERNAL_NAME])
    assert names("factory@example.com") == [SECRET_NAME]
    assert client.get(base + "/files/f2", headers=h("factory@example.com")).status_code == 404


def test_an_invited_email_that_registers_lands_with_access(prod, env):
    """The production module has no invitation object: the admin adds the e-mail as a member, and the account that
    registers (and confirms) with that address is that member - the link opens the page, nothing else is created."""
    _smtp_on(env)
    mail = "invited.later@example.com"
    assert _register_next(PATH, email=mail, ip="203.0.113.77").status_code == 202
    client.get(f"{LINK}/v/{_token_from(env['outbox'][-1], 'verify')}")
    tok = client.post("/api/auth/login", json={"email": mail, "password": PW},
                      headers={"X-Forwarded-For": "203.0.113.77"}).json()["token"]
    hdr = {"Authorization": f"Bearer {tok}"}
    j = client.get(prod["base"] + "/access", headers=hdr).json()
    assert j["access"] == "member" and j["role"] == "engineer"
    docs = client.get(prod["base"] + "/documents", headers=hdr)
    assert docs.status_code == 200 and len(docs.json()["documents"]) == 2
    # a stranger who registers through the same link gets the page but not the data
    assert _register_next(PATH, email="x.stranger@example.com", ip="203.0.113.78").status_code == 202
    client.get(f"{LINK}/v/{_token_from(env['outbox'][-1], 'verify')}")
    tok2 = client.post("/api/auth/login", json={"email": "x.stranger@example.com", "password": PW},
                       headers={"X-Forwarded-For": "203.0.113.78"}).json()["token"]
    h2 = {"Authorization": f"Bearer {tok2}"}
    assert client.get(prod["base"] + "/access", headers=h2).json()["access"] == "none"
    assert client.get(prod["base"] + "/documents", headers=h2).status_code == 404
