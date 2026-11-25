"""Owner queue routes + the auth gate (auth._OWNER_QUEUE_AGENT_PATH).  Same mechanism as tests/test_compute_nodes.py: the admin
check is replaced by a table of fake callers.  The ``oq_`` tokens are made by the store in a temp dir (throwaway)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim import admin_audit as AUD
from motor_ai_sim import auth
from motor_ai_sim import owner_queue as OQ
from motor_ai_sim.api import app

client = TestClient(app)
USERS = {"ua": {"uid": "a", "email": "a@x.com", "role": "user"},
         "adm": {"uid": "z", "email": "z@x.com", "role": "admin"}}
ADM = {"Authorization": "adm"}
AG = "/api/owner-queue/agent"


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_OWNER_QUEUE_DIR", str(tmp_path / "oq"))
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(tmp_path / "audit.jsonl"))

    def _who(a):
        u = USERS.get(str(a or ""))
        return (bool(u and u["role"] == "admin"), u)
    monkeypatch.setattr(auth, "_is_admin_caller", _who)
    yield


def hdr(tok):
    return {"Authorization": "Bearer " + tok}


def tok(name="a"):
    return OQ.create_token(name, "z@x.com")


def item(**kw):
    b = {"kind": "decision", "asked_by": "US · pump", "question": "Which winding?", "project_id": "p1",
         "options": [{"label": "A", "recommended": True}, {"label": "B"}], "detail": "SECRET-DETAIL-TEXT"}
    b.update(kw)
    return b


def mk(t, **kw):
    r = client.post(AG + "/items", json=item(**kw), headers=hdr(t))
    assert r.status_code == 200, r.text
    return r.json()["id"]


AGENT_ROUTES = [("GET", AG + "/priority?project=p1"), ("POST", AG + "/items"), ("GET", AG + "/items"), ("GET", AG + "/items/oq-000000000000"),
                ("POST", AG + "/items/oq-000000000000/withdraw")]


def call(method, path, **kw):
    return client.request(method, path, json={} if method == "POST" else None, **kw)


def test_every_agent_route_is_401_without_a_good_bearer():
    good = tok("good")
    revoked = tok("gone")
    OQ.revoke_token("gone")
    for method, path in AGENT_ROUTES:
        for h in ({}, {"Authorization": "Bearer"}, {"Authorization": "Bearer nonsense"}, {"Authorization": "Basic abc"},
                  hdr("oq_" + "x" * 40), hdr(revoked), {"Authorization": "adm"}, {"Authorization": "ua"},
                  {"Authorization": "Bearer mcnode_" + "x" * 40}):
            assert call(method, path, headers=h).status_code == 401, (method, path, h)
    assert call("GET", AG + "/items", headers=hdr(good)).status_code == 200


def test_agent_routes_pass_the_closed_door_but_nothing_else_does(monkeypatch):
    monkeypatch.setenv("PUBLIC_EXHIBIT", "0")
    for p in (AG + "/items", AG + "/items/oq-abc123", AG + "/items/oq-abc123/withdraw", AG + "/priority"):
        assert auth.anonymous_allowed(p)
    for p in ("/api/owner-queue/items", "/api/owner-queue/count", "/api/owner-queue/tokens", "/api/owner-queue/tokens/x/revoke",
              "/api/owner-queue/items/oq-abc/answer", AG + "/items/a/b/c", AG + "/answer", AG + "/items/x/answer",
              "/api/owner-queue/agentx/items", "/api/owner-queue/priority", "/api/owner-queue/priorities", AG + "/priority/x", AG + "/priorities", "/api/owner-queue/agent/items/a.b"):
        assert not auth.anonymous_allowed(p), p
    # through the real middleware: the agent route answers its own 401 (route check), the owner side is closed
    t = tok()
    assert client.get(AG + "/items", headers=hdr(t)).status_code == 200
    assert client.get("/api/owner-queue/items").status_code == 401


def test_create_get_list_withdraw_roundtrip():
    t = tok()
    iid = mk(t)
    assert iid.startswith("oq-") and len(iid) == 15
    assert mk(t) == iid                                         # idempotent
    got = client.get(f"{AG}/items/{iid}", headers=hdr(t)).json()
    assert got["state"] == "open" and got["token_name"] == "a" and got["answer"] is None
    assert [i["id"] for i in client.get(AG + "/items?state=open", headers=hdr(t)).json()["items"]] == [iid]
    assert client.get(AG + "/items?project_id=zz", headers=hdr(t)).json()["items"] == []
    assert client.post(f"{AG}/items/{iid}/withdraw", headers=hdr(t)).json()["state"] == "withdrawn"
    assert client.post(f"{AG}/items/{iid}/withdraw", headers=hdr(t)).status_code == 409
    # the client cannot choose the token name
    iid2 = mk(t, question="other", token_name="evil")
    assert client.get(f"{AG}/items/{iid2}", headers=hdr(t)).json()["token_name"] == "a"
    assert client.post(AG + "/items", json=item(question="x" * 301), headers=hdr(t)).status_code == 422


def test_token_of_a_cannot_read_or_withdraw_item_of_b():
    ta, tb = tok("a"), tok("b")
    ib = mk(tb)
    assert client.get(f"{AG}/items/{ib}", headers=hdr(ta)).status_code == 404
    assert client.get(f"{AG}/items/oq-ffffffffffff", headers=hdr(ta)).status_code == 404       # same answer as "exists, not yours"
    assert client.post(f"{AG}/items/{ib}/withdraw", headers=hdr(ta)).status_code == 404
    assert client.get(AG + "/items", headers=hdr(ta)).json()["items"] == []
    assert client.get(f"{AG}/items/{ib}", headers=hdr(tb)).status_code == 200


def test_flood_cap_429(monkeypatch):
    monkeypatch.setattr(OQ, "MAX_OPEN_PER_TOKEN", 3)
    t = tok()
    for i in range(3):
        mk(t, question=f"q{i}")
    assert client.post(AG + "/items", json=item(question="q9"), headers=hdr(t)).status_code == 429


OWNER_ROUTES = [("GET", "/api/owner-queue/items"), ("GET", "/api/owner-queue/count"),
                ("POST", "/api/owner-queue/items/oq-000000000000/answer"), ("GET", "/api/owner-queue/tokens"),
                ("POST", "/api/owner-queue/tokens"), ("POST", "/api/owner-queue/tokens/a/revoke"),
                ("GET", "/api/owner-queue/priorities"), ("POST", "/api/owner-queue/priority")]


def test_every_owner_route_is_closed_to_anonymous_and_non_admin_and_oq_tokens(monkeypatch):
    t = tok("zz")
    for closed in ("1", "0"):
        monkeypatch.setenv("PUBLIC_EXHIBIT", closed)
        for method, path in OWNER_ROUTES:
            assert call(method, path).status_code == 401, (method, path)
            # exhibit open: the route's own require_admin says 403; door closed: the door answers 401 first (the fake caller table
            # is behind it), which is just as closed
            assert call(method, path, headers={"Authorization": "ua"}).status_code == (403 if closed == "1" else 401), (method, path)
            assert call(method, path, headers=hdr(t)).status_code in (401, 403), (method, path)
            assert call(method, path, headers=hdr("oq_" + "y" * 40)).status_code in (401, 403)
    # not even the static service token style of bearer: an unknown bearer is no admin
    assert client.get("/api/owner-queue/count", headers={"Authorization": "Bearer service"}).status_code == 401


def test_oq_token_gets_nothing_elsewhere(monkeypatch):
    t = tok()
    monkeypatch.setenv("PUBLIC_EXHIBIT", "0")
    for p in ("/api/overview", "/api/family/tree", "/api/nodes", "/api/admin/audit"):
        r = client.get(p, headers=hdr(t))
        assert r.status_code in (401, 403, 404), (p, r.status_code)
        assert r.status_code != 200


def test_admin_answer_flow_records_the_admin_and_the_agent_reads_it():
    t = tok()
    iid = mk(t)
    assert client.post(f"/api/owner-queue/items/{iid}/answer", json={"option": "Z"}, headers=ADM).status_code == 422
    assert client.post(f"/api/owner-queue/items/{iid}/answer", json={}, headers=ADM).status_code == 422
    r = client.post(f"/api/owner-queue/items/{iid}/answer", json={"option": "B", "by": "forged@x.com"}, headers=ADM)
    assert r.status_code == 200
    ans = r.json()["answer"]
    assert ans["option"] == "B" and ans["by"] == "z@x.com" and ans["at"].endswith("Z") and ans["text"] is None
    assert client.post(f"/api/owner-queue/items/{iid}/answer", json={"option": "A"}, headers=ADM).status_code == 409
    seen = client.get(f"{AG}/items/{iid}", headers=hdr(t)).json()
    assert seen["state"] == "answered" and seen["answer"]["option"] == "B"
    assert client.post(f"{AG}/items/{iid}/withdraw", headers=hdr(t)).status_code == 409
    assert client.post("/api/owner-queue/items/oq-000000000000/answer", json={"text": "x"}, headers=ADM).status_code == 404


def test_message_read_and_reply_and_count_and_history_search():
    t = tok()
    m1 = mk(t, kind="message", question="Report ready", options=None, detail="long report")
    m2 = mk(t, kind="message", question="Another", options=None)
    d = mk(t)
    c = client.get("/api/owner-queue/count", headers=ADM).json()
    assert c["open_blocking"] == 1 and c["open_messages"] == 2 and c["oldest_open_at"]
    order = [i["id"] for i in client.get("/api/owner-queue/items?state=open", headers=ADM).json()["items"]]
    assert order[0] == d and set(order[1:]) == {m1, m2}                         # blocking first
    assert client.post(f"/api/owner-queue/items/{m1}/answer", json={"option": "read"}, headers=ADM).json()["state"] == "read"
    assert client.post(f"/api/owner-queue/items/{m2}/answer", json={"text": "thanks"}, headers=ADM).json()["state"] == "answered"
    hist = client.get("/api/owner-queue/items?q=report", headers=ADM).json()["items"]
    assert [i["id"] for i in hist] == [m1]
    assert len(client.get("/api/owner-queue/items", params={"asked_by": "US · pump", "project_id": "p1"}, headers=ADM).json()["items"]) == 3
    assert client.get("/api/owner-queue/items", params={"asked_by": "nobody"}, headers=ADM).json()["items"] == []


def test_token_admin_routes_and_secrecy(tmp_path):
    r = client.post("/api/owner-queue/tokens", json={"name": "us1-pump"}, headers=ADM)
    assert r.status_code == 200
    tkn = r.json()["token"]
    assert tkn.startswith("oq_")
    assert client.post("/api/owner-queue/tokens", json={"name": "us1-pump"}, headers=ADM).status_code == 409
    assert client.post("/api/owner-queue/tokens", json={"name": "bad name"}, headers=ADM).status_code == 422
    listed = client.get("/api/owner-queue/tokens", headers=ADM)
    assert tkn not in listed.text and "token_hash" not in listed.text and listed.json()["tokens"][0]["name"] == "us1-pump"
    iid = mk(tkn, question="audit me")
    client.post(f"/api/owner-queue/items/{iid}/answer", json={"text": "typed answer text"}, headers=ADM)
    client.post(f"{AG}/items/{mk(tkn, question='w')}/withdraw", headers=hdr(tkn))
    assert client.post("/api/owner-queue/tokens/us1-pump/revoke", headers=ADM).status_code == 200
    assert client.post("/api/owner-queue/tokens/nobody/revoke", headers=ADM).status_code == 404
    assert client.get(AG + "/items", headers=hdr(tkn)).status_code == 401
    # audit: the right actions, never the token, the detail or the typed text
    raw = (tmp_path / "audit.jsonl").read_text()
    assert tkn not in raw and "SECRET-DETAIL-TEXT" not in raw and "typed answer text" not in raw
    acts = [r["action"] for r in AUD.read(100)]
    for a in ("owner_queue.create", "owner_queue.answer", "owner_queue.withdraw", "owner_queue.token_create", "owner_queue.token_revoke"):
        assert a in acts, a
        assert a in AUD.ACTIONS
    create = [r for r in AUD.read(100) if r["action"] == "owner_queue.create"][0]
    assert create["actor"] == "us1-pump" and create["details"]["session"] == "US · pump"
    ans = [r for r in AUD.read(100) if r["action"] == "owner_queue.answer"][0]
    assert ans["actor"] == "z@x.com" and ans["details"]["answer_kind"] == "text"


def test_damaged_store_is_503_and_not_reset(tmp_path):
    t = tok()
    mk(t)
    p = tmp_path / "oq" / "items.jsonl"
    p.write_text(p.read_text() + "garbage\n")
    before = p.read_text()
    assert client.get(AG + "/items", headers=hdr(t)).status_code == 503
    assert client.post(AG + "/items", json=item(question="n"), headers=hdr(t)).status_code == 503
    assert client.get("/api/owner-queue/count", headers=ADM).status_code == 503
    assert p.read_text() == before


def test_approval_item_over_http_422_hash_returned_to_agent_and_admin():
    t = tok()
    ok = {"kind": "outside_message", "asked_by": "s", "question": "Send?", "detail": "Final", "recipient": "r@example.test"}
    for bad in ({**ok, "recipient": None}, {**ok, "detail": ""}, {**ok, "options": [{"label": "Yes"}, {"label": "No"}]}):
        assert client.post(AG + "/items", json=bad, headers=hdr(t)).status_code == 422
    iid = client.post(AG + "/items", json=ok, headers=hdr(t)).json()["id"]
    got = client.get(f"{AG}/items/{iid}", headers=hdr(t)).json()
    assert got["recipient"] == "r@example.test" and [o["label"] for o in got["options"]] == ["Approve", "Reject"]
    r = client.post(f"/api/owner-queue/items/{iid}/answer", json={"option": "Approve", "text": "ok"}, headers=ADM)
    h = OQ.approval_hash("outside_message", "Final", "r@example.test")
    assert r.status_code == 200 and r.json()["answer"]["approved_hash"] == h
    assert client.get(f"{AG}/items/{iid}", headers=hdr(t)).json()["answer"]["approved_hash"] == h
    adm = client.get("/api/owner-queue/items", headers=ADM).json()["items"]
    assert adm[0]["answer"]["approved_hash"] == h and adm[0]["answer"]["text"] == "ok"


def test_priority_routes_admin_write_agent_read_audit_and_order(tmp_path):
    t = tok()
    a = mk(t, project_id="p1", question="first")
    b = mk(t, project_id="p2", question="second")
    assert client.get(AG + "/priority", params={"project": "p2"}, headers=hdr(t)).json() == {"project_id": "p2", "priority": "normal"}
    assert client.get(AG + "/priority", headers=hdr(t)).json()["priority"] == "normal"
    # the agent can only read: its bearer is no admin, and a user session is no admin
    assert client.post("/api/owner-queue/priority", json={"project_id": "p2", "priority": "urgent"}, headers=hdr(t)).status_code in (401, 403)
    assert client.post("/api/owner-queue/priority", json={"project_id": "p2", "priority": "urgent"}, headers={"Authorization": "ua"}).status_code == 403
    assert client.post("/api/owner-queue/priority", json={"project_id": "p2", "priority": "high"}, headers=ADM).status_code == 422
    assert client.post("/api/owner-queue/priority", json={"project_id": "p2", "priority": "urgent"}, headers=ADM).json() == {"project_id": "p2", "priority": "urgent"}
    assert client.get(AG + "/priority", params={"project": "p2"}, headers=hdr(t)).json()["priority"] == "urgent"
    assert client.get("/api/owner-queue/priorities", headers=ADM).json()["priorities"]["p2"]["by"] == "z@x.com"
    order = client.get("/api/owner-queue/items", headers=ADM).json()["items"]
    assert [i["id"] for i in order] == [b, a] and order[0]["priority"] == "urgent"
    assert client.get(f"{AG}/items/{b}", headers=hdr(t)).json()["priority"] == "urgent"
    assert [i["priority"] for i in client.get(AG + "/items", headers=hdr(t)).json()["items"]] == ["urgent", "normal"]
    assert client.get("/api/owner-queue/count", headers=ADM).json()["open_urgent"] == 1
    rows = [r for r in AUD.read(100) if r["action"] == "owner_queue.priority"]
    assert rows and rows[0]["details"] == {"project_id": "p2", "old": "normal", "new": "urgent"} and rows[0]["actor"] == "z@x.com"
    assert "owner_queue.priority" in AUD.ACTIONS
