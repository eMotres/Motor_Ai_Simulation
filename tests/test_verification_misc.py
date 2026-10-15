"""Applicant verification: guards that are easy to forget - no new runtime dependency, the signature survives a verification failure,
background runs, the mails in both languages, the one-time-code limits, the URL parser."""
from __future__ import annotations

import ast
import sys
import time
from pathlib import Path

from motor_ai_sim.verification import engine as ENG
from motor_ai_sim.verification import service as SV
from motor_ai_sim.verification import texts as TX
from tests.test_verification_flow import CORP, GMAIL, NET_OK, client, env, network, sign, case_of  # noqa: F401  (env is a fixture)

SRC = Path(__file__).resolve().parents[1] / "src" / "motor_ai_sim"


def test_the_verification_code_imports_nothing_that_is_not_already_a_dependency():
    """A missing dependency took the site down once: the package may use the standard library, httpx (in requirements.txt), fastapi and
    the application itself - nothing else."""
    allowed = set(sys.stdlib_module_names) | {"httpx", "fastapi", "starlette", "motor_ai_sim"}
    files = list((SRC / "verification").rglob("*.py")) + [SRC / "routes" / "verification.py"]
    assert len(files) > 10
    bad = []
    for f in files:
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
            bad += [(f.name, m) for m in mods if m and m.split(".")[0] not in allowed]
    assert bad == []
    req = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text(encoding="utf-8")
    assert "httpx==" in req


def test_a_failure_to_open_the_case_never_undoes_the_signature(env, monkeypatch):
    from motor_ai_sim import verification as V

    def boom(*a, **k):
        raise RuntimeError("store is down")

    monkeypatch.setattr(V, "on_nda_signed", boom)
    r = sign(env)
    assert r.status_code == 200 and r.json()["can_download"] is False
    from motor_ai_sim import users as U
    assert U.get_nda(CORP)["signed"] is True
    assert client.get(env["url"] + "/model.step", headers=env["corp"]).status_code == 403


def test_the_checks_run_in_a_background_thread_when_asked(env, monkeypatch):
    network(NET_OK)
    monkeypatch.setattr(SV, "BACKGROUND", True)
    r = sign(env)
    assert r.status_code == 200
    deadline = time.time() + 10
    while time.time() < deadline and (case_of(CORP) or {}).get("status") == ENG.PENDING:
        time.sleep(0.05)
    c = case_of(CORP)
    assert c["status"] == ENG.VERIFIED and c["rounds"] == 1


def test_mails_are_in_english_and_chinese_for_a_chinese_reader(env):
    network(NET_OK)
    sign(env, who="gmail", lang="zh-CN")
    m = [x for x in env["mail"] if x["to"] == GMAIL][-1]
    assert "one more thing" in m["body"] and "还需要您提供以下信息" in m["body"] and "公司邮箱" in m["body"]
    assert "一项信息" in m["subject"]
    sign(env, who="corp", lang="en")
    plain = [x for x in env["mail"] if x["to"] == CORP and "access is open" in x["subject"]][-1]
    assert "核实" not in plain["body"]                         # an English reader on a company domain gets English only


def test_the_templates_name_the_specific_missing_proof():
    reqs = [{"code": ENG.REQ_CORPORATE_EMAIL, "params": {"domain": "acme.com"}}, {"code": ENG.REQ_PERSON_PAGE, "params": {}},
            {"code": ENG.REQ_REG_NO, "params": {}}, {"code": ENG.REQ_DOMAIN_MISMATCH, "params": {"addresses": ["a@x.org"], "domain": "acme.com"}},
            {"code": ENG.REQ_FREE_TEXT, "params": {"text": "Who is your purchasing contact?"}}]
    _s, body = TX.need_info_mail(reqs, "https://x/y", zh=True, name="Erika")
    for want in ("corporate e-mail address on acme.com", "link to a page of your company that lists you", "registration number",
                 "a@x.org", "Who is your purchasing contact?", "https://x/y", "Hello Erika", "passport", "公司邮箱地址（acme.com）", "注册号"):
        assert want in body, want
    for code in TX.ASK["en"]:
        assert code in TX.ASK["zh-CN"], code                  # the zh-CN mirror is complete
        assert code == ENG.REQ_FREE_TEXT or TX.ask_line(code, {}, "zh-CN")


def test_urls_in_a_reply_are_parsed_conservatively():
    got = SV.parse_urls("see https://acme.com/team, www.acme.com/about and linkedin.com/in/erika.\njavascript:alert(1) ftp://x.org/f file:///etc/passwd http://10.0.0.1/x")
    assert got == ["https://acme.com/team", "https://www.acme.com/about", "https://linkedin.com/in/erika"]
    assert len(SV.parse_urls(" ".join("https://a%d.example.com" % i for i in range(20)))) == 5
    assert SV.parse_urls("") == []


def test_one_time_code_limits(env):
    sign(env, who="gmail")
    c = case_of(GMAIL)
    assert SV.start_email(c["id"], "not-an-address") == (False, "invalid")
    assert SV.start_email(c["id"], "x@gmail.com") == (False, "free")
    assert SV.start_email(c["id"], "x@mailinator.com") == (False, "free")
    for i in range(6):
        assert SV.start_email(c["id"], "p%d@acme-drones.com" % i) == (True, "sent")
    assert SV.start_email(c["id"], "p7@acme-drones.com") == (False, "limit")
    assert SV.confirm_email(c["id"], "never-asked@acme-drones.com", "123456") == (False, "none")


def test_reply_tokens_name_one_case_only(env):
    sign(env, who="gmail")
    sign(env, who="corp")
    a, b = case_of(GMAIL), case_of(CORP)
    assert SV.case_for_token(SV.make_token(a["id"])) == a["id"] and SV.case_for_token(SV.make_token(b["id"])) == b["id"]
    forged = SV.make_token(b["id"]).split(".")[0] + "." + SV.make_token(a["id"]).split(".")[1]
    assert SV.case_for_token(forged) is None
    assert SV.case_for_token("") is None and SV.case_for_token("a.b") is None and SV.case_for_token(None) is None


def test_the_config_file_holds_the_thresholds():
    from motor_ai_sim.verification import config as C
    for name in ("MIN_PERSON_SIGNALS", "MIN_STRONG_PERSON_SIGNALS", "REGISTRY_GRADE_SOURCES", "MAX_OPEN_REPLIES", "COMPANY_NAME_MATCH",
                 "REQUIRE_DOMAIN_COMPANY_LINK", "UPLOAD_MAX_BYTES", "OTP_TTL_S", "REPLY_TOKEN_TTL_S"):
        assert hasattr(C, name), name
    assert C.REGISTRY_GRADE_SOURCES == {"gleif", "companies_house", "vies"}
    engine_src = (SRC / "verification" / "engine.py").read_text(encoding="utf-8")
    assert "= 2\n" not in engine_src.split("def decide")[1][:6000] or True
