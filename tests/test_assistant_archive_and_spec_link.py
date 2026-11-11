"""Assistant conversation archive + staff view (owner 2026-11-11) and the "describe your motor" link of the launch mail.
Synthetic data only."""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import account_lifecycle, admin_audit, assistant_archive as AR, auth, mailing, return_path
from motor_ai_sim.routes import assistant_conversations as ROUTE, support


@pytest.fixture
def arch(tmp_path, monkeypatch):
    monkeypatch.setattr(account_lifecycle, "identity_dir", lambda: tmp_path / "identity")
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(tmp_path / "admin_audit.jsonl"))
    return tmp_path


def _client(admin: bool):
    app = FastAPI()
    app.include_router(ROUTE.router)
    app.dependency_overrides[auth.require_admin] = (lambda: {"email": "boss@example.test", "role": "admin"}) if admin else _deny
    return TestClient(app)


def _deny():
    from fastapi import HTTPException
    raise HTTPException(status_code=403, detail="Admin access required.")


def _seed():
    AR.log_turn(assistant="spec", conv_id=AR.conv_key("spec", "alpha", "own@example.test"), account="own@example.test", project_id="alpha",
                page="development.specification", user_text="supply is 50 V, my key is sk-abcdefghijklmnop1234", reply="Filled the supply.",
                model="m1", source="claude", fill_mode=True, applied=[{"field_id": "2_1", "basis": "stated", "id": "p-1"}],
                proposed=[{"field_id": "4_8", "id": "p-2"}], refused=["6_1"])
    AR.log_event(assistant="spec", event="undo", conv_id=AR.conv_key("spec", "alpha", "own@example.test"), account="own@example.test",
                 project_id="alpha", field_id="2_1", proposal_id="p-1")
    AR.log_turn(assistant="support", conv_id=AR.conv_key("support", "visitorsid12345"), page="landing", user_text="what is this?",
                reply="A motor tool.", model="m1", source="claude")
    AR.log_turn(assistant="support", conv_id=AR.conv_key("support", "visitorsid12345"), user_text="hi", reply="x", source="error", error="boom")


def test_archive_is_append_only_redacted_and_keeps_no_hidden_context(arch):
    _seed()
    files = list((arch / "identity" / "assistant_conversations").glob("*.jsonl"))
    assert len(files) == 1
    rows = [json.loads(x) for x in files[0].read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 4 and rows[0]["assistant"] == "spec" and rows[0]["fill_mode"] is True
    assert "sk-abcdefghijklmnop1234" not in files[0].read_text(encoding="utf-8") and "[redacted-key]" in rows[0]["user_text"]
    assert not any("system" in r or "snapshot" in r for r in rows)
    assert all(r.get("context") in ("help", "spec") for r in rows)          # a label of where the chat ran, never the hidden context
    assert all(r.get("time") and r["time"] == r["iso"] for r in rows)         # every record carries its time (11-11: time was null)
    assert rows[0]["applied"][0]["field_id"] == "2_1" and rows[0]["refused"] == ["6_1"]
    assert rows[2]["anon"] is True and rows[2]["account"] == ""            # a visitor: no account, keyed by a random session id, no IP field
    assert not any("ip" in r for r in rows)
    assert not hasattr(AR, "delete") and not hasattr(AR, "update")


def test_clearing_the_users_view_leaves_the_archive(arch, monkeypatch):
    from motor_ai_sim.development import spec_assistant_history as HI, store
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(arch / "projects"))
    store.create_project({"id": "alpha", "title": "A", "members": [], "linked_motors": []})
    HI.append("alpha", "own@example.test", [{"role": "user", "text": "hello"}])
    _seed()
    HI.clear("alpha", "own@example.test")
    assert HI.load("alpha", "own@example.test") == []
    assert len(list(AR.iter_records(assistant="spec"))) == 2


def test_support_chat_archives_visitors_by_session_id_and_accounts_by_email(arch, monkeypatch):
    msgs = [{"role": "user", "content": "how do I start?"}]
    support._archive_turn({"sid": "abcdef123456", "context": {"tab": "motors"}}, None, True, msgs, {"reply": "Open Motors.", "source": "claude", "model": "m"})
    monkeypatch.setattr(auth, "caller_identity", lambda authorization=None: {"id": "Anna@Example.test", "role": "user"})
    support._archive_turn({"sid": "bad sid!"}, "Bearer x", False, msgs, {"reply": "ok", "source": "error", "detail": "provider down"})
    rows = list(AR.iter_records(assistant="support"))                        # the help context, old and new records alike
    assert rows[0]["anon"] and rows[0]["account"] == "" and rows[0]["page"] == "motors" and rows[0]["conv_id"] == AR.conv_key("assistant", "abcdef123456")
    assert rows[0]["assistant"] == "assistant" and rows[0]["context"] == "help" and rows[0]["time"]
    assert rows[1]["account"] == "anna@example.test" and rows[1]["error"] == "provider down"
    assert rows[1]["conv_id"] == AR.conv_key("assistant", "anna@example.test")      # an invalid session id is ignored, never the IP
    assert list(AR.iter_records(context="help")) == rows and list(AR.iter_records(assistant="spec")) == []


def test_admin_api_lists_filters_opens_counts_and_audits(arch):
    _seed()
    c = _client(True)
    allc = c.get("/api/admin/assistant-conversations").json()
    assert allc["count"] == 2
    spec = c.get("/api/admin/assistant-conversations?assistant=spec&project=alpha&account=own@").json()["conversations"]
    assert len(spec) == 1 and spec[0]["filled"] == 1 and spec[0]["proposed"] == 1
    assert c.get("/api/admin/assistant-conversations?date_from=2999-01-01").json()["count"] == 0
    assert c.get("/api/admin/assistant-conversations?date_from=bad").status_code == 422
    opened = c.get(f"/api/admin/assistant-conversations/{spec[0]['conv_id']}").json()
    assert len(opened["records"]) == 2 and c.get("/api/admin/assistant-conversations/s-0000000000000000").status_code == 404
    stats = c.get("/api/admin/assistant-conversations/stats").json()["total"]
    assert stats["conversations"] == 2 and stats["turns"] == 3 and stats["filled"] == 1 and stats["proposed"] == 1
    assert stats["undone"] == 1 and stats["refusals"] == 1 and stats["errors"] == 1
    actions = [json.loads(x)["action"] for x in (arch / "admin_audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert actions.count("assistant_conv.read") >= 5 and "unknown:assistant_conv.read" not in actions


def test_export_is_jsonl_logged_and_marks_candidates_without_bypassing_the_gate(arch, monkeypatch):
    _seed()
    from motor_ai_sim import platform_agreement as PA
    monkeypatch.setattr(PA, "training_allowed", lambda email: email == "own@example.test")
    c = _client(True)
    r = c.get("/api/admin/assistant-conversations/export?date_from=2000-01-01&assistant=spec")
    assert r.status_code == 200 and "ndjson" in r.headers["content-type"] and "attachment" in r.headers["content-disposition"]
    lines = [json.loads(x) for x in r.text.splitlines()]
    assert len(lines) == 2 and all(x["dataset"]["candidate"] for x in lines)
    assert lines[0]["dataset"]["training_allowed"] is True and "decides downstream" in lines[0]["dataset"]["gate"]
    visitors = [json.loads(x) for x in c.get("/api/admin/assistant-conversations/export?assistant=support").text.splitlines()]
    assert visitors and all(v["dataset"]["training_allowed"] is False for v in visitors)       # an anonymous visitor never is
    log = [json.loads(x) for x in (arch / "admin_audit.jsonl").read_text(encoding="utf-8").splitlines()]
    exp = [e for e in log if e["action"] == "assistant_conv.export"]
    assert len(exp) == 2 and exp[0]["details"]["records"] == 2


def test_only_admins_reach_the_admin_api(arch):
    _seed()
    c = _client(False)
    for url in ("", "/stats", "/export", "/s-0000000000000000"):
        assert c.get("/api/admin/assistant-conversations" + url).status_code == 403


# -- the launch mail's link ----------------------------------------------------

@pytest.mark.parametrize("lang,link,button", [
    ("en", "https://aerostator.com/projects?new=spec", "Describe your motor"),
    ("zh-CN", "https://aerostator.com/zh-cn/projects?new=spec", "描述您的电机")])
def test_launch_mail_button_opens_the_specification_link(lang, link, button, monkeypatch):
    monkeypatch.setattr(mailing.CP, "prefs_url", lambda base, email, lang, action="": f"https://aerostator.com/preferences?t=x&action={action}")
    r = mailing.render("free_simulation_launch_2026-11", lang, email="a@example.test", name="Anna")
    assert link in r.text and button in r.text and f'href="{link}"' in r.html


def test_other_campaigns_keep_open_url(monkeypatch):
    monkeypatch.setattr(mailing.CP, "prefs_url", lambda base, email, lang, action="": "https://aerostator.com/preferences?t=x")
    r = mailing.render("launch_2026-10-19", "zh-CN", email="a@example.test", name="Li")
    assert "https://aerostator.com/zh-cn/" in r.text and "projects?new=spec" not in r.text


def test_sign_up_return_path_keeps_the_spec_link_only():
    assert return_path.safe_app_path("/projects?new=spec") == "/projects?new=spec"
    assert return_path.safe_app_path("/zh-cn/projects?new=spec&signin=1") == "/zh-cn/projects?new=spec"
    assert return_path.safe_app_path("/projects") is None and return_path.safe_app_path("/projects?new=x") is None
    assert return_path.safe_app_path("//evil.test/projects?new=spec") is None
