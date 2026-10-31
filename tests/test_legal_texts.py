"""The legal texts of the free simulation launch (legal_texts.py, routes/legal.py) and the sign-up acceptance behind them.

Texts: the routes serve the repo copies verbatim, ``{{version}}`` filled, with the SHA-256 of what is served (hashes pinned here so a
silent edit of an approved text fails the test).  Acceptance: a NEW account (e-mail form, first Google sign-in) is refused without the
ticked box, the record carries the hashes of the texts shown, an existing account is asked once, deletion pseudonymises the lines,
``training_allowed`` follows the four-step precedence.  Everything runs on tmp_path; no mail, no solver.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim import legal_texts as LT
from motor_ai_sim import platform_agreement as PA
from motor_ai_sim.api import app

_ROOT = Path(__file__).resolve().parents[1]
ADMIN, ENG, OWN = "owner@example.com", "eng@example.com", "vadim@motresres.com"
ANNA, BOB, LONE = "anna@example.com", "bob@example.com", "solo@solo-works.example"
PW = "correct horse battery"
client = TestClient(app)

#: SHA-256 of the texts as served (``{{version}}`` = v0.4), EN and zh-CN.  Change a text -> publish a NEW version (docs/LEGAL_TEXTS.md) and re-pin.
PINNED = {
    ("terms", "en"): "d1fe1060b95ca44debd600c2e2979efddcc1fad696ac406005b9120f45c8af88",
    ("terms", "zh-CN"): "51b227835596f7c8885e09a7f8db56a8ac77adc3571e4a9d521b07f8941547a8",
    ("privacy", "en"): "14f47739049ea6df8b8677c0b85bfd2f2ef38f28e93a0fd2161de999ef047ec4",
    ("privacy", "zh-CN"): "966800829e3e4f836dc5b8990156763cb5ea026b06509bb201921592aa8fd10d",
    ("platform-agreement", "en"): "d2502cd7ed68d483dff40ee72d80d0c77418a77ac33dd72675658e1a2b95d43a",
    ("platform-agreement", "zh-CN"): "1ad9302a464484ac3b9fe9677fd241a2f8e02abff4eba48bf6929d26d0d04095",
    ("schedules", "en"): "ef21c88310e7b8608439e9f716cb5a9a605d77aded0c38f6819839cb0a8b5213",
    ("schedules", "zh-CN"): "afeeb5c7addb47599cebf9d2d18289b4836775b9c772ed18bac445db180bfbd2",
}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from motor_ai_sim import auth as A
    from motor_ai_sim import auth_email as E
    from motor_ai_sim import comm_prefs as CP
    from motor_ai_sim import newsletter as N
    from motor_ai_sim import sessions as S
    from motor_ai_sim import users as U

    monkeypatch.delenv("TERMS_ACCEPTANCE_DISABLED", raising=False)           # the suite default is off; these tests are about it
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(S, "_SESSIONS_FILE", tmp_path / ".sessions.json")
    monkeypatch.setattr(S, "_EVENTS_FILE", tmp_path / "auth_events.jsonl")
    monkeypatch.setattr(N, "_STORE_FILE", tmp_path / "newsletter.json")
    monkeypatch.setattr(CP, "_STATE_FILE", tmp_path / "comm_prefs.json")
    monkeypatch.setattr(CP, "_HISTORY_FILE", tmp_path / "comm_prefs_history.jsonl")
    monkeypatch.setattr(CP, "_ROOT", tmp_path)
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setenv("MOTRES_LEGAL_ROOT", str(tmp_path / "legal"))
    monkeypatch.setenv("MOTRES_DIRECTORY_ROOT", str(tmp_path / "directory"))
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(tmp_path / "admin_audit.jsonl"))
    monkeypatch.delenv("PLATFORM_AGREEMENT_DISABLED", raising=False)
    monkeypatch.setattr(A, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(A, "AUTH_ENFORCE", False)
    monkeypatch.setattr(A, "_reject_seen", {})
    monkeypatch.setattr(S, "_last_touch", {})
    monkeypatch.delenv("WORKSPACES_ROOT", raising=False)
    PA._CACHE.update(sig=None, rows=[])
    E.reset_limits()
    outbox: list = []
    monkeypatch.setattr(E, "send", lambda to, subject, body, *, block=False, **kw: outbox.append((to, subject, body)) or True)
    U.create_user(ADMIN, "password-owner", role="admin", name="Admin")
    U.create_user(ENG, "password-eng", role="engineer", name="Eng")
    for e in (ANNA, BOB, LONE, OWN):
        U.create_user(e, "password-" + e.split("@")[0], role="user", name=e.split("@")[0])
    yield {"U": U, "A": A, "E": E, "CP": CP, "tmp": tmp_path, "mp": monkeypatch, "outbox": outbox,
           "h": lambda e: {"Authorization": f"Bearer {U.issue_token(e)}"}}
    E.reset_limits()


def signup(body_extra=None, email="new1@example.com", ip="203.0.113.31", ua="pytest-agent", **kw):
    body = {"email": email, "password": PW, "name": "New One", **(body_extra or {})}
    return client.post("/api/auth/register", json=body, headers={"X-Forwarded-For": ip, "User-Agent": ua}, **kw)


TICKED = {"accept_terms": True, "terms_version": LT.TERMS_VERSION, "country": "DE", "business_name": "", "capacity": "sole_trader"}


def lines(env):
    p = env["tmp"] / "terms_acceptances.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


# -- the texts -----------------------------------------------------------------------------------------------------

def test_the_four_texts_are_served_verbatim_with_the_version_filled_and_their_hash():
    for doc in ("terms", "privacy", "platform-agreement", "schedules"):
        r = client.get(f"/api/legal/{doc}")                                   # no sign-in
        assert r.status_code == 200, doc
        j = r.json()
        assert j["doc"] == doc and j["version"] == "v0.4" and j["lang"] == "en"
        assert j["sha256"] == hashlib.sha256(j["text"].encode("utf-8")).hexdigest()
        assert "{{version}}" not in j["text"] and "Version v0.4." in j["text"]
        assert j["text"].startswith("# ")
    terms = client.get("/api/legal/terms").json()["text"]
    assert "**In short:**" in terms and "MOTRES may use the engineering data you supply after accepting to train its own AI models" in terms
    # the agreement keeps its company placeholders (the click-through of platform_agreement.py fills them), only {{version}} is ours;
    # the public rendering has the conditional block markers stripped, so BOTH section 13 variants are visible
    agr = client.get("/api/legal/platform-agreement").json()["text"]
    assert "{{company}}" in agr and "{{#" not in agr and "{{/" not in agr
    assert "*Variant A" in agr and "*Variant B" in agr and "13.1 This Agreement is governed by Slovenian substantive law" in agr
    assert "13.1 This Agreement and its arbitration agreement are governed by Hong Kong SAR law" in agr
    assert "|---|" in client.get("/api/legal/schedules").json()["text"]       # the one table the page must scroll


def test_the_text_is_the_repo_file_except_the_version_and_the_conditional_markers():
    import re
    for doc, (sub, stem, version) in LT.DOCS.items():
        for lang in LT.LANGS:
            raw = (_ROOT / "config" / "legal" / sub / version / f"{stem}_{lang}.md").read_bytes().decode("utf-8").replace("\r\n", "\n")
            want = raw.replace("{{version}}", version)
            want = re.sub(r"\n{3,}", "\n\n", re.sub(r"(?m)^\{\{[#/](?:if|unless)[^}]*\}\}\n", "", want))
            assert LT.read(doc, lang)["text"] == want, (doc, lang)
    # nothing but the marker lines differs: every other line of the agreement is served as it is in the file
    sub, stem, version = LT.DOCS["platform-agreement"]
    raw = (_ROOT / "config" / "legal" / sub / version / f"{stem}_en.md").read_text(encoding="utf-8").replace("\r\n", "\n").replace("{{version}}", version)
    kept = [l for l in raw.split("\n") if l.strip() and not l.startswith(("{{#", "{{/"))]
    assert kept == [l for l in LT.read("platform-agreement", "en")["text"].split("\n") if l.strip()]


def test_pinned_hashes_of_the_approved_texts():
    got = {(d, l): LT.read(d, l)["sha256"] for d in LT.DOCS for l in LT.LANGS}
    assert got == PINNED, "an approved text changed: publish a new version (or, before release, re-pin) - docs/LEGAL_TEXTS.md"


def test_chinese_mirror_is_served_and_is_not_the_english_text():
    for doc in LT.DOCS:
        zh = client.get(f"/api/legal/{doc}", params={"lang": "zh-CN"})
        assert zh.status_code == 200, doc
        j = zh.json()
        assert j["lang"] == "zh-CN" and j["version"] == "v0.4" and any("一" <= c <= "鿿" for c in j["text"])
        assert j["sha256"] != LT.read(doc, "en")["sha256"] and "{{version}}" not in j["text"]
        assert "{{#" not in j["text"] and "{{/" not in j["text"]                  # the conditional markers are stripped in Chinese too
        assert client.get(f"/api/legal/{doc}", params={"lang": "zh-cn"}).json()["sha256"] == j["sha256"]


def test_country_list_is_public_and_is_the_nda_list():
    from motor_ai_sim import nda as N
    r = client.get("/api/legal/countries")
    assert r.status_code == 200 and r.json()["countries"] == list(N.COUNTRIES) and "DE" in r.json()["countries"] and "HK" in r.json()["countries"]


def test_unknown_document_or_language_is_404():
    assert client.get("/api/legal/nope").status_code == 404
    assert client.get("/api/legal/terms", params={"lang": "fr"}).status_code == 404
    assert client.get("/api/legal/terms", params={"lang": "zh"}).status_code == 404
    assert client.get("/api/legal/terms.md").status_code == 404


def test_texts_are_readable_signed_out_on_a_closed_server(env):
    env["mp"].setattr(env["A"], "AUTH_ENFORCE", True)
    assert client.get("/api/legal/privacy").status_code == 200
    assert client.get("/api/legal/status").status_code == 401                  # the status needs a session


def test_version_comparison_is_numeric():
    assert LT.version_ge("v0.4", "v0.4") and LT.version_ge("v0.10", "v0.4") and LT.version_ge("v1", "v0.4")
    assert not LT.version_ge("v0.2", "v0.4") and not LT.version_ge("", "v0.4") and not LT.version_ge("0.3", "v0.4")
    assert not LT.version_ge("v0-draft", "v0.4")


# -- sign-up: e-mail form -------------------------------------------------------------------------------------------

def test_signup_without_the_box_is_refused_422_terms_required_and_makes_no_account(env):
    cap = {"capacity": "sole_trader"}
    for extra in ({}, {"accept_terms": False, "terms_version": LT.TERMS_VERSION, "country": "DE", **cap}, {"accept_terms": True, "country": "DE", **cap},
                  {"accept_terms": True, "terms_version": "v0.2", "country": "DE", **cap}, {"terms_version": LT.TERMS_VERSION, "country": "DE", **cap}):
        r = signup(extra)
        assert r.status_code == 422 and r.json()["code"] == "terms_required", extra
    assert env["U"].get_user("new1@example.com") is None and lines(env) == [] and env["outbox"] == []


def test_signup_with_the_box_records_the_acceptance_with_the_hashes_of_the_texts_shown(env):
    r = signup({**TICKED, "lang": "zh-CN"}, ua="Mozilla/5.0 test")
    assert r.status_code == 202, r.text
    (rec,) = lines(env)
    assert rec["user"] == "new1@example.com" and rec["source"] == "signup" and rec["lang"] == "zh-CN"
    assert (rec["terms_version"], rec["privacy_version"], rec["agreement_public_version"]) == ("v0.4", "v0.4", "v0.4")
    assert (rec["country"], rec["business_name"], rec["forum_variant"]) == ("DE", "", "A")
    assert (rec["capacity"], rec["historical_opt_in"]) == ("sole_trader", False)
    assert rec["ip"] == "203.0.113.31" and rec["user_agent"] == "Mozilla/5.0 test"
    assert rec["id"].startswith("t") and rec["iso"].endswith("Z") and rec["ts"] > 0
    assert set(rec["text_sha256"]) == {"terms", "privacy", "platform_agreement", "schedules"}
    assert rec["text_sha256"]["terms"] == LT.read("terms", "zh-CN")["sha256"]            # the language it was shown in
    assert rec["text_sha256"]["schedules"] == LT.read("schedules", "zh-CN")["sha256"]
    assert all(len(h) == 64 for h in rec["text_sha256"].values())
    assert env["U"].get_user("new1@example.com") is not None
    if os.name == "posix":
        assert stat.S_IMODE((env["tmp"] / "terms_acceptances.jsonl").stat().st_mode) == 0o600
    # the English form records the English hashes
    assert signup(TICKED, email="new2@example.com", ip="203.0.113.32").status_code == 202
    assert lines(env)[-1]["text_sha256"]["privacy"] == LT.read("privacy", "en")["sha256"] and lines(env)[-1]["lang"] == "en"


def test_signup_needs_the_country_of_the_business_and_records_name_and_forum_variant(env):
    base = {"accept_terms": True, "terms_version": LT.TERMS_VERSION, "capacity": "sole_trader"}
    for extra in ({}, {"country": ""}, {"country": "XX"}, {"country": "Germany"}, {"country": "DE", "business_name": "x" * 301}):
        r = signup({**base, **extra})
        assert r.status_code == 422, extra
        assert r.json()["code"] in ("country_required", "business_name_too_long"), (extra, r.json())
    assert env["U"].get_user("new1@example.com") is None and lines(env) == []
    # lower case code and a spaced business name are normalised; Mainland China and Hong Kong are forum variant B
    assert signup({**base, "country": " cn ", "business_name": "  Shenzhen   Drive Co. "}, email="cn@example.com", ip="203.0.113.60").status_code == 202
    assert signup({**base, "country": "HK"}, email="hk@example.com", ip="203.0.113.61").status_code == 202
    assert signup({**base, "country": "SI", "business_name": "Motor d.o.o.", "capacity": "business"}, email="si@example.com",
                  ip="203.0.113.62").status_code == 202
    by = {r["user"]: r for r in lines(env)}
    assert (by["cn@example.com"]["country"], by["cn@example.com"]["business_name"], by["cn@example.com"]["forum_variant"]) == (
        "CN", "Shenzhen Drive Co.", "B")
    assert (by["hk@example.com"]["country"], by["hk@example.com"]["business_name"], by["hk@example.com"]["forum_variant"]) == ("HK", "", "B")
    assert by["si@example.com"]["forum_variant"] == "A" and by["si@example.com"]["business_name"] == "Motor d.o.o."


def test_the_recorded_hash_is_that_of_the_public_rendering_with_both_variants(env):
    assert signup(TICKED).status_code == 202
    rec = lines(env)[0]
    served = client.get("/api/legal/platform-agreement").json()
    assert rec["text_sha256"]["platform_agreement"] == served["sha256"] == hashlib.sha256(served["text"].encode("utf-8")).hexdigest()
    assert "*Variant A" in served["text"] and "*Variant B" in served["text"] and "{{#" not in served["text"]


def test_a_repeated_signup_of_an_existing_address_files_no_acceptance_for_it(env):
    assert signup(TICKED, email=ANNA, ip="203.0.113.33").status_code == 202           # same answer as for a new address
    assert lines(env) == []
    assert signup(TICKED, email="new3@example.com", ip="203.0.113.34").status_code == 202
    assert signup(TICKED, email="new3@example.com", ip="203.0.113.35").status_code == 202     # pending row: not a new account
    assert len(lines(env)) == 1


def test_a_storage_failure_never_blocks_the_account(env):
    def boom(*a, **k):
        raise OSError("disk full")
    env["mp"].setattr(LT, "record_acceptance", boom)
    assert signup(TICKED, email="new4@example.com", ip="203.0.113.36").status_code == 202
    assert env["U"].get_user("new4@example.com") is not None
    # ... and the person is asked once at the next sign-in
    h = env["h"]("new4@example.com")
    assert client.get("/api/legal/status", headers=h).json()["required"] is True


# -- sign-up: first Google sign-in ------------------------------------------------------------------------------------

def _google_as(env, email, extra=None):
    env["mp"].setattr(env["A"], "GOOGLE_CLIENT_ID", "test-client")
    env["mp"].setattr(env["A"], "_verify_google_token", lambda cred: {"email": email, "email_verified": True, "sub": "g-1", "name": "G User"})
    return client.post("/api/auth/google", json={"credential": "x", **(extra or {})}, headers={"X-Forwarded-For": "203.0.113.50"})


def test_first_google_sign_in_needs_the_box_but_an_existing_account_never_does(env):
    r = _google_as(env, "gnew@example.com")
    assert r.status_code == 422 and r.json()["code"] == "terms_required"
    assert env["U"].get_user("gnew@example.com") is None and lines(env) == []
    r = _google_as(env, "gnew@example.com", {**TICKED, "lang": "zh-CN"})
    assert r.status_code == 200 and r.json()["token"]
    (rec,) = lines(env)
    assert rec["user"] == "gnew@example.com" and rec["source"] == "google" and rec["lang"] == "zh-CN"
    # an account that already exists signs in with Google without the box and without a new line
    assert _google_as(env, ANNA).status_code == 200
    assert _google_as(env, "gnew@example.com").status_code == 200
    assert len(lines(env)) == 1


# -- existing accounts ---------------------------------------------------------------------------------------------------

def test_existing_account_status_then_accept_records_source_existing(env):
    h = env["h"](ANNA)
    assert client.get("/api/legal/status").status_code == 401
    st = client.get("/api/legal/status", headers=h).json()
    assert st["version"] == "v0.4" and st["required"] is True and st["accepted_version"] is None
    # not ticked / wrong version -> 422, nothing recorded
    assert client.post("/api/legal/accept", json={}, headers=h).status_code == 422
    bad = client.post("/api/legal/accept", json={"accept_terms": True, "terms_version": "v0.1", "country": "DE"}, headers=h)
    assert bad.status_code == 422 and bad.json()["code"] == "terms_required" and lines(env) == []
    nocountry = client.post("/api/legal/accept", json={"accept_terms": True, "terms_version": LT.TERMS_VERSION}, headers=h)
    assert nocountry.status_code == 422 and nocountry.json()["code"] == "country_required" and lines(env) == []
    ok = client.post("/api/legal/accept", json={**TICKED, "country": "HK", "business_name": "Acme", "capacity": "business", "lang": "zh-CN"},
                     headers={**h, "User-Agent": "ua-1"})
    assert ok.status_code == 200 and ok.json()["ok"] is True and ok.json()["status"]["required"] is False
    (rec,) = lines(env)
    assert rec["user"] == ANNA and rec["source"] == "existing" and rec["lang"] == "zh-CN" and rec["user_agent"] == "ua-1"
    assert (rec["country"], rec["business_name"], rec["forum_variant"], rec["capacity"]) == ("HK", "Acme", "B", "business")
    again = client.post("/api/legal/accept", json=TICKED, headers=h)
    assert again.json()["already"] is True and len(lines(env)) == 1
    assert client.get("/api/legal/status", headers=h).json()["required"] is False


def test_an_older_acceptance_does_not_count_for_the_current_version(env):
    p = env["tmp"] / "terms_acceptances.jsonl"
    p.write_text(json.dumps({"id": "t20260101-aaaaaaaaaaaa", "user": ANNA, "terms_version": "v0.2", "iso": "2026-01-01T00:00:00Z"}) + "\n",
                 encoding="utf-8")
    st = client.get("/api/legal/status", headers=env["h"](ANNA)).json()
    assert st["required"] is True and st["accepted_version"] == "v0.2"


def test_staff_and_motres_accounts_are_not_asked(env):
    for who in (ADMIN, ENG, OWN):
        st = client.get("/api/legal/status", headers=env["h"](who)).json()
        assert st["required"] is False, who
    assert client.get("/api/legal/status", headers=env["h"](BOB)).json()["required"] is True


def test_switch_off_asks_nobody(env):
    env["mp"].setenv("TERMS_ACCEPTANCE_DISABLED", "1")
    assert client.get("/api/legal/status", headers=env["h"](ANNA)).json()["required"] is False
    assert signup().status_code == 202 and lines(env) == []


# -- deletion and export ---------------------------------------------------------------------------------------------------

def test_deletion_pseudonymises_the_lines_and_keeps_the_proof(env):
    from motor_ai_sim import account_lifecycle as AL
    assert signup(TICKED, email="gone@example.com", ip="198.51.100.9").status_code == 202
    assert signup(TICKED, email="stays@example.com", ip="198.51.100.10").status_code == 202
    before = {r["user"]: r for r in lines(env)}
    rep = AL.purge("gone@example.com", actor="test", reason="test")
    assert rep["ok"], rep
    after = lines(env)
    assert len(after) == 2
    gone = next(r for r in after if r["id"] == before["gone@example.com"]["id"])
    assert "gone@example.com" not in json.dumps(gone) and "198.51.100.9" not in json.dumps(gone)
    assert gone["user"] == AL.subject_hash("gone@example.com") and gone["ip"] and gone["ip"] != "198.51.100.9"
    assert gone["text_sha256"] == before["gone@example.com"]["text_sha256"] and gone["terms_version"] == "v0.4"   # the proof stays
    assert next(r for r in after if r["user"] == "stays@example.com")["ip"] == "198.51.100.10"
    assert LT.records("gone@example.com") == []


def test_the_data_export_carries_the_accounts_own_lines(env):
    import zipfile
    from motor_ai_sim import account_lifecycle as AL
    assert signup(TICKED, email="mine@example.com", ip="198.51.100.11").status_code == 202
    assert signup(TICKED, email="other@example.com", ip="198.51.100.12").status_code == 202
    z = AL.build_export("mine@example.com")
    try:
        with zipfile.ZipFile(z) as zf:
            d = json.loads(zf.read("terms_acceptances.json"))
    finally:
        z.unlink()
    assert [r["user"] for r in d["acceptances"]] == ["mine@example.com"] and d["current_version"] == "v0.4"


# -- training_allowed: company -> account -> terms -> fail-closed ------------------------------------------------------------

_CLOCK = [1767225600.0]          # 2026-01-01T00:00:00Z; every helper record is one minute after the previous one (call order = time order)


def _tick() -> float:
    _CLOCK[0] += 60
    return _CLOCK[0]


def _agreement_record(env, key, mode, user):
    p = env["tmp"] / "legal" / "platform_agreement_acceptances.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    ts = _tick()
    row = dict(id="a20261101-" + "0" * 12, ts=ts, iso=LT._iso(ts), key=key, scope="company" if key.startswith("company:") else "account",
               user=user, mode=mode, version="v1", ai_training_allowed=mode == "FREE")
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    PA._CACHE.update(sig=None, rows=[])


def _terms_line(env, email, version, **extra):
    ts = _tick()
    with open(env["tmp"] / "terms_acceptances.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "t20261101-" + "1" * 12, "user": email, "terms_version": version, "ts": ts, "iso": LT._iso(ts), **extra}) + "\n")


def _company(env, name, reg, emails):
    from motor_ai_sim.directory import service as D
    comp = D.create_company(dict(legal_name=name, reg_no=reg, country="DE", address="Hauptstr. 1, Berlin"))
    for e in emails:
        person = D.create_person(dict(name=e.split("@")[0], emails=[e]))
        D.create_affiliation(comp["id"], person["id"], status="confirmed")
    return comp


def test_training_allowed_d_nothing_accepted_is_false(env):
    assert PA.training_allowed(ANNA) is False and PA.training_allowed("user:" + ANNA) is False
    assert PA.training_allowed("") is False and PA.training_allowed("company:nope") is False
    _terms_line(env, ANNA, "v0.2")                                    # a terms version before the FREE data use does not count
    assert PA.training_allowed(ANNA) is False


def test_training_allowed_c_the_terms_box_of_v03_or_later_allows(env):
    _terms_line(env, ANNA, "v0.4", capacity="business")
    assert PA.training_allowed(ANNA) is True and PA.training_allowed("user:" + ANNA) is True and PA.training_allowed(BOB) is False
    _terms_line(env, BOB, "v0.4", capacity="sole_trader")
    assert PA.training_allowed(BOB) is True
    assert PA.training_allowed("company:whatever") is False           # a company id alone has no terms line


def test_training_allowed_b_an_account_level_agreement_decides_over_the_terms_line(env):
    _terms_line(env, ANNA, "v0.4", capacity="business")
    _agreement_record(env, "user:" + ANNA, "PRIVATE", ANNA)
    assert PA.training_allowed(ANNA) is False                         # PRIVATE is never overridden by the Terms line
    _agreement_record(env, "user:" + LONE, "FREE", LONE)
    assert PA.training_allowed(LONE) is True                          # FREE by the agreement alone, no terms line


def test_training_allowed_a_the_company_acceptance_decides_for_its_confirmed_users(env):
    comp = _company(env, "Acme Drones Ltd", "HRB 12345", [ANNA, BOB])
    _terms_line(env, ANNA, "v0.4", capacity="business")
    _terms_line(env, BOB, "v0.4", capacity="sole_trader")
    _agreement_record(env, "company:" + comp["id"], "PRIVATE", ANNA)
    assert PA.training_allowed(ANNA) is False and PA.training_allowed(BOB) is False       # the company decided, both users' ticks lose
    assert PA.training_allowed(comp["id"]) is False
    _agreement_record(env, "company:" + comp["id"], "FREE", ANNA)                          # a later FREE replaces it
    assert PA.training_allowed(ANNA) is True and PA.training_allowed(comp["id"]) is True


def test_training_allowed_a_company_without_acceptance_falls_through_to_the_users_terms(env):
    comp = _company(env, "Bolt Motors", "HRB 777", [ANNA, BOB])
    assert PA.training_allowed(ANNA) is False and PA.training_allowed(comp["id"]) is False
    _terms_line(env, ANNA, "v0.4", capacity="business")
    assert PA.training_allowed(ANNA) is True and PA.training_allowed(BOB) is False


def test_training_allowed_keeps_motres_own_accounts_true(env):
    assert PA.training_allowed(OWN) is True and PA.training_allowed("user:" + OWN) is True
    assert PA.training_allowed(ADMIN) is False                        # an arbitrary admin account is not MOTRES's domain


# -- the help chat notice ---------------------------------------------------------------------------------------------------

def test_me_exposes_the_help_chat_provider_kind_and_never_a_key(env):
    from motor_ai_sim.routes import support
    eff = {"provider": "gemini", "gemini": {"key": "AIza-secret-key", "model": "gemini-x", "key_source": "env"},
           "anthropic": {"key": "", "model": "claude-x", "key_source": "none"}}
    env["mp"].setattr(support, "_effective", lambda: eff)
    r = client.get("/api/me")
    assert r.status_code == 200 and r.json()["supportProvider"] == "gemini"
    assert "AIza-secret-key" not in r.text and "gemini-x" not in r.text
    eff["provider"] = "anthropic"
    assert support.effective_provider() == "none"                       # chosen but no key: replies are the mock, nothing goes to Anthropic
    eff["anthropic"]["key"] = "sk-x"
    assert client.get("/api/me").json()["supportProvider"] == "anthropic"
    eff["provider"], eff["gemini"]["key"] = "gemini", ""
    assert client.get("/api/me").json()["supportProvider"] == "none"


# -- the web routes resolve: /terms, /privacy, /platform-agreement and their /zh-cn/ twins (the mails link to the latter) ---------------------

def _nginx_location_for(path):
    """Which location of deploy/nginx.conf serves ``path``, by nginx's rules: an exact match wins; else the longest ``^~`` prefix; else the
    FIRST regex location in file order that matches; else the longest plain prefix."""
    import re
    conf = (_ROOT / "deploy" / "nginx.conf").read_text(encoding="utf-8")
    locs = re.findall(r"^\s{4}location\s+(?:(=|~\*|~|\^~)\s+)?(\S+)\s*\{", conf, re.M)
    exact = [p for op, p in locs if op == "=" and p == path]
    if exact:
        return ("=", exact[0])
    pref = [(op, p) for op, p in locs if op in ("", "^~") and path.startswith(p)]
    best = max(pref, key=lambda x: len(x[1]), default=None)
    if best and best[0] == "^~":
        return best
    for op, p in locs:
        if op in ("~", "~*") and re.search(p, path, re.I if op == "~*" else 0):
            return (op, p)
    return best


@pytest.mark.parametrize("path", ["/terms", "/privacy", "/platform-agreement", "/zh-cn/terms", "/zh-cn/privacy", "/zh-cn/platform-agreement",
                                  "/terms/", "/zh-cn/privacy/"])
def test_legal_pages_resolve_to_the_spa_shell_in_nginx(path):
    import re
    op, pat = _nginx_location_for(path)
    assert op == "~" and "terms|privacy|platform-agreement" in pat, (path, op, pat)
    conf = (_ROOT / "deploy" / "nginx.conf").read_text(encoding="utf-8")
    block = re.search(re.escape("location ~ " + pat) + r" \{(.*?)\n    \}", conf, re.S).group(1)
    assert "try_files /index.html =404;" in block and "noindex" not in block          # served, and indexable
    # the pre-rendered Chinese landing is untouched, and so are the preferences and consent pages
    assert _nginx_location_for("/zh-cn/") == ("=", "/zh-cn/") and _nginx_location_for("/zh-cn/preferences")[1].startswith("^/(zh-cn/)?preferences")


def test_the_web_routes_and_the_mail_links_agree():
    from motor_ai_sim import mailing as M
    import re
    for url in M.PRIVACY_URL.values():
        path = url.replace("https://aerostator.com", "")
        assert _nginx_location_for(path)[1].endswith("(terms|privacy|platform-agreement)/?$"), url
    # Root.tsx routes the same paths (legalLogic.matchLegalPage is node-tested)
    root = (_ROOT / "web" / "src" / "Root.tsx").read_text(encoding="utf-8")
    assert "matchLegalPage(path)" in root and re.search(r"<LegalPage kind=\{legalKind\}", root)
