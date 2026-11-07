"""An address added to a project is in it at once and gets one notice (owner 2026-11-08)."""
from motor_ai_sim.development import routes


def test_the_notice_says_who_and_where_in_both_languages_and_no_money():
    subject, body = routes._added_notice_text({"title": "Pump motor, 100 N*m, 1500 rpm"}, "project-1", "owner@example.test", "https://app.example.test")
    assert "Pump motor" in subject and "owner@example.test" in body
    assert "https://app.example.test/projects/project-1/specification" in body and "您好" in body
    for word in ("price", "quote", "cost", "fee", "budget"):
        assert word not in body.lower()


def test_one_notice_and_the_cap_only_stops_mail(monkeypatch):
    sent = []
    from motor_ai_sim import auth_email as E
    monkeypatch.setattr(E, "send", lambda to, subject, body, **kw: sent.append(to) or True)
    monkeypatch.setattr(E, "app_url", lambda: "https://app.example.test")
    monkeypatch.setattr(routes.store, "get_project", lambda pid: {"title": "T"})
    monkeypatch.setattr(routes.access, "is_engineer", lambda user: False)
    monkeypatch.setattr(routes, "_email", lambda user: "owner@example.test")
    monkeypatch.setattr(routes, "_added_notices", {})
    monkeypatch.setattr(routes, "_ADDED_NOTICE_DAY_MAX", 2)
    for i in range(3):
        routes._notify_added_member("p", f"m{i}@example.test", {})
    routes._notify_added_member("p", "owner@example.test", {})
    assert sent == ["m0@example.test", "m1@example.test"]
