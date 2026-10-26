"""An NDA signed outside the platform, listed before the account exists, flags the account at sign-in (synthetic)."""
import pytest

from motor_ai_sim import nda_preapproval as NP


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(NP, "path", lambda: tmp_path / "nda_preapproved.json")
    accounts = {}
    import motor_ai_sim.users as U
    monkeypatch.setattr(U, "get_user", lambda e: accounts.get(e))
    calls = []
    def set_nda(email, signed, date=None, by="", **k):
        accounts[email]["nda"] = {"signed": signed, "date": date, "by": by}
        calls.append((email, date, by))
    monkeypatch.setattr(U, "set_nda", set_nda)
    return accounts, calls


def test_listed_address_is_flagged_when_the_account_appears(env):
    accounts, calls = env
    NP.add("Person@Example.com", "2026-09-24", "Customer unilateral NDA, accepted 2026-09-24", by="admin@x")
    assert calls == []                                       # no account yet: nothing to flag
    accounts["person@example.com"] = {"nda": {}}
    assert NP.apply("person@example.com")["date"] == "2026-09-24"
    assert calls == [("person@example.com", "2026-09-24", "preapproved: Customer unilateral NDA, accepted 2026-09-24")]
    assert NP.apply("person@example.com") is None             # already flagged: idempotent


def test_existing_account_is_flagged_at_once_and_others_are_not(env):
    accounts, calls = env
    accounts["a@example.com"] = {"nda": {}}
    accounts["b@example.com"] = {"nda": {}}
    NP.add("a@example.com", "2026-10-01", "Paper NDA 2026-10-01", by="admin@x")
    assert [c[0] for c in calls] == ["a@example.com"]
    assert NP.apply("b@example.com") is None


@pytest.mark.parametrize("email, date, ref", [("bad", "2026-01-01", "r"), ("a@b.co", "01.01.2026", "r"), ("a@b.co", "2026-01-01", "")])
def test_invalid_entries_refused(env, email, date, ref):
    with pytest.raises(ValueError):
        NP.add(email, date, ref, by="admin@x")
