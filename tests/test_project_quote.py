"""The customer's read-only quote (owner 2026-11-03): an admin shares a FROZEN snapshot of the cost estimate; the members of
that project (and admins) read a whitelisted payload with the contingency folded into the prices.  Access control is the
point: non-members, members of another project, global engineers who are not members and MCP agents never get it.

Temporary project store, ECB served from a fixture string, no solver; neutral ids and made-up numbers only."""
from __future__ import annotations

import json
from decimal import Decimal

import pytest
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.testclient import TestClient

from motor_ai_sim import auth
from motor_ai_sim.development import cost_estimate as CE
from motor_ai_sim.development import cost_estimate_fx as FX
from motor_ai_sim.development import cost_estimate_routes as R
from motor_ai_sim.development import cost_estimate_sources as SRC
from motor_ai_sim.development import cost_quote as Q
from motor_ai_sim.development import quote_routes as QR
from motor_ai_sim.development import store
from motor_ai_sim.development.routes import router as project_router

ECB_XML = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
<Cube><Cube time='2026-11-02'><Cube currency='USD' rate='1.2500'/><Cube currency='CNY' rate='8.0000'/></Cube></Cube>
</gesmes:Envelope>"""

#: bearer token -> the resolved caller
USERS = {
    "admin": {"email": "admin@example.test", "role": "admin"},
    "owner": {"email": "customer@example.test", "role": "user"},          # project role "owner" of alpha
    "member-eng": {"email": "eng@example.test", "role": "user"},          # project role "engineer" of alpha (global role user)
    "viewer": {"email": "viewer@example.test", "role": "user"},           # project role "customer" of alpha
    "other": {"email": "other@example.test", "role": "user"},             # member of beta only
    "staff": {"email": "staff@example.test", "role": "engineer"},         # a global engineer, member of nothing
    "stranger": {"email": "stranger@example.test", "role": "user"},
    "owner2": {"email": "owner2@example.test", "role": "user"},           # a SECOND project owner of alpha
    "factory-owner": {"email": "factory@example.test", "role": "oem"},    # a factory account (global role oem), project owner of alpha
    "factory-flagged": {"email": "flagged@example.test", "role": "oem"},  # a factory account, customer member WITH the flag
}
MEMBERS = ("owner", "member-eng", "viewer")
NON_OWNER_MEMBERS = ("member-eng", "viewer")
FORBIDDEN_KEYS = {"note", "notes", "source", "source_ref", "contingency", "contingency_pct", "factor", "hint", "pct", "by",
                  "dhl", "dhl_lanes", "manual", "ecb", "fx", "revision", "snapshot_n", "shared_by", "diff", "estimate",
                  "updated_by", "analog_mass_kg", "target_mass_kg", "production", "project"}


def _h(who):
    return {"Authorization": "Bearer " + who} if who else {}


def _user(authorization):
    token = str(authorization or "").split(" ", 1)[-1].strip()
    return USERS.get(token)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MOTRES_PRODUCTION_ROOT", str(tmp_path / "production"))
    monkeypatch.setenv("MOTRES_MOTOR_TESTS_ROOT", str(tmp_path / "tests"))
    monkeypatch.setenv("MOTRES_FX_CACHE_DIR", str(tmp_path / "fx"))
    monkeypatch.setattr(FX, "fetch_text", lambda *_a, **_k: ECB_XML)
    monkeypatch.setattr(SRC, "active_mass", lambda die, config: None)
    monkeypatch.setattr(auth, "resolve_user", lambda authorization, **_k: _user(authorization))
    monkeypatch.setattr(auth, "_is_admin_caller", lambda authorization: ((_user(authorization) or {}).get("role") == "admin",
                                                                          _user(authorization)))
    base = {"customer_org": "Org", "status": "quote", "created_at": "t", "updated_at": "t", "owner_engineer": "eng@example.test",
            "linked_motors": [{"die": "DIE-A", "config": "big"}], "notes": "", "pending_invites": []}
    store.create_project({**base, "id": "alpha", "title": "Alpha",
                          "members": [{"email": "customer@example.test", "role": "owner"},
                                      {"email": "eng@example.test", "role": "engineer"},
                                      {"email": "viewer@example.test", "role": "customer"},
                                      {"email": "owner2@example.test", "role": "owner"}]})
    store.create_project({**base, "id": "beta", "title": "Beta", "members": [{"email": "other@example.test", "role": "owner"}]})
    app = FastAPI()
    app.include_router(R.router)
    app.include_router(QR.router)
    app.include_router(project_router)
    from motor_ai_sim.development import project_contract as _PC
    app.include_router(_PC.router)
    return TestClient(app)


def _fill(line, amount, weeks=None):
    line.update({"status": "filled", "amount": amount, "pct": None, "currency": "EUR", "source": "manual",
                 "by": "admin@example.test", "date": "2026-11-02", "note": "internal note", "weeks": weeks})


def _complete(client, *, contingency=10.0, terms="70 % before the start of an iteration, 30 % before shipment"):
    """Save a complete 2-iteration estimate: iteration 1 = tooling 1000 + 2 x 333.33 + testing 250 + shipping 101.11;
    iteration 2 = tooling 200 + 2 x 333.33 + no bench + shipping 101.11."""
    est = client.get("/api/admin/projects/alpha/cost-estimate", headers=_h("admin")).json()["estimate"]
    est["settings"].update({"contingency_pct": contingency, "payment_terms": terms,
                            "ship_to": {"country": "AR", "city": "Buenos Aires"}})
    est["fx"] = {"manual": {"CNY": 9.0}}                         # a manual rate: never sent to the customer
    one, two = est["iterations"]
    _fill(one["lines"]["tooling"], 1000.0, 6)
    _fill(one["lines"]["manufacturing"], 333.33, 4)
    _fill(one["lines"]["testing"], 250.0, 2)
    _fill(one["lines"]["shipping"], 101.11, 3)
    one["lines"]["shipping"]["source_ref"] = {"dhl": {"quoted_on": "2026-11-02", "from": "CN Shanghai", "to": "AR", "weight_kg": 85}}
    _fill(two["lines"]["tooling"], 200.0, 3)
    _fill(two["lines"]["manufacturing"], 333.33, 4)
    two["bench"] = "none"
    _fill(two["lines"]["shipping"], 101.11, 3)
    resp = client.put("/api/admin/projects/alpha/cost-estimate", json=est, headers=_h("admin"))
    assert resp.status_code == 200, resp.text
    return resp.json()["estimate"]


def _share(client, who="admin", **body):
    return client.post("/api/admin/projects/alpha/cost-estimate/share", json=body, headers=_h(who))


def _quote(client, who, project="alpha"):
    return client.get(f"/api/projects/{project}/quote", headers=_h(who))


def _keys(obj, out=None):
    out = set() if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            _keys(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _keys(v, out)
    return out


# ── access ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def _switch(client, email, value, who="owner"):
    return client.patch("/api/projects/alpha/members/sees_prices", json={"email": email, "sees_prices": value}, headers=_h(who))


@pytest.mark.parametrize("who", ("owner", "owner2", "admin"))
def test_owners_and_admins_read_the_shared_quote(client, who):
    """Owner 2026-11-22: every project owner sees the prices, always."""
    _complete(client)
    assert _share(client).status_code == 200
    resp = _quote(client, who)
    assert resp.status_code == 200, resp.text
    assert resp.headers["cache-control"] == "no-store"
    assert resp.json()["total_price"] > 0


@pytest.mark.parametrize("who", NON_OWNER_MEMBERS)
def test_an_engineer_or_customer_member_sees_no_price_by_default(client, who):
    _complete(client)
    assert _share(client).status_code == 200
    resp = _quote(client, who)
    assert resp.status_code == 404 and resp.json()["detail"] == "Quote not found"
    assert _quote(client, who, "nope").json() == resp.json()          # the answer of a project with no quote
    assert client.get("/api/projects/alpha", headers=_h(who)).json()["quote_shared"] is False
    assert client.get("/api/projects/alpha", headers=_h("owner")).json()["quote_shared"] is True


def test_the_owner_switches_one_member_on_and_off(client):
    _complete(client)
    _share(client)
    assert _switch(client, "viewer@example.test", True).json() == {"email": "viewer@example.test", "sees_prices": True}
    assert _quote(client, "viewer").status_code == 200 and _quote(client, "member-eng").status_code == 404
    assert client.get("/api/projects/alpha", headers=_h("viewer")).json()["quote_shared"] is True
    assert _switch(client, "viewer@example.test", False, "owner2").status_code == 200       # the second owner may switch it off
    assert _quote(client, "viewer").status_code == 404
    assert _switch(client, "viewer@example.test", True, "admin").status_code == 200         # and so may an admin
    assert _quote(client, "viewer").status_code == 200
    import json
    rows = [json.loads(x) for x in store._file("alpha", "audit.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = [r for r in rows if r["action"] == "sees_prices"]
    assert [(r["actor_email"], r["entity_id"], r["details"]["to"]) for r in rows] == [
        ("customer@example.test", "viewer@example.test", True), ("owner2@example.test", "viewer@example.test", False),
        ("admin@example.test", "viewer@example.test", True)] and all(r["time"] for r in rows)


def test_an_engineer_member_cannot_grant_anybody_the_flag_nor_make_an_owner(client):
    _complete(client)
    _share(client)
    assert _switch(client, "eng@example.test", True, "member-eng").status_code == 403          # not himself
    assert _switch(client, "viewer@example.test", True, "member-eng").status_code == 403
    assert _switch(client, "viewer@example.test", True, "viewer").status_code in (403, 404)
    add = lambda body, who: client.post("/api/projects/alpha/members", json=body, headers=_h(who))        # noqa: E731
    assert add({"email": "eng@example.test", "role": "owner"}, "member-eng").status_code == 403
    assert add({"email": "new@example.test", "role": "customer", "sees_prices": True}, "member-eng").status_code == 403
    assert _quote(client, "member-eng").status_code == 404


def test_owners_are_not_switchable_and_unknown_members_are_refused(client):
    assert _switch(client, "owner2@example.test", False).status_code == 422
    assert _switch(client, "nobody@example.test", True).status_code == 404
    assert client.patch("/api/projects/alpha/members/sees_prices", json={"email": "viewer@example.test", "sees_prices": "yes"},
                        headers=_h("owner")).status_code == 422
    assert _switch(client, "viewer@example.test", True, "stranger").status_code in (403, 404)


def test_a_pending_invite_carries_the_flag_until_it_is_claimed(client):
    r = client.post("/api/projects/alpha/members", json={"email": "later@example.test", "role": "customer", "sees_prices": True},
                    headers=_h("owner"))
    assert r.status_code == 200
    pending = store.get_project("alpha")["pending_invites"]
    assert [p.get("sees_prices") for p in pending if p["email"] == "later@example.test"] == [True]
    assert _switch(client, "later@example.test", False).status_code == 200
    assert not [p for p in store.get_project("alpha")["pending_invites"] if p["email"] == "later@example.test"][0].get("sees_prices")


@pytest.mark.parametrize("who", ["stranger", "other"])
def test_non_members_and_members_of_another_project_get_404(client, who):
    _complete(client)
    assert _share(client).status_code == 200
    resp = _quote(client, who)
    assert resp.status_code == 404 and resp.json()["detail"] == "Quote not found"
    assert resp.headers["cache-control"] == "no-store"
    # the answer is the same as for a project that does not exist: nothing is revealed
    assert _quote(client, who, "nope").json() == resp.json()


def test_anonymous_callers_are_refused(client):
    _complete(client)
    _share(client)
    assert _quote(client, None).status_code == 401
    assert _quote(client, "not-a-token").status_code == 401


def test_nothing_shared_and_unshared_give_404(client):
    _complete(client)
    for who in MEMBERS + ("admin",):
        assert _quote(client, who).status_code == 404                        # before sharing
    assert _share(client).status_code == 200
    assert _quote(client, "owner").status_code == 200
    stop = client.delete("/api/admin/projects/alpha/cost-estimate/share", headers=_h("admin"))
    assert stop.status_code == 200 and stop.json() == {"shared": None}
    for who in MEMBERS + ("admin",):
        assert _quote(client, who).status_code == 404                        # after unsharing


@pytest.mark.parametrize("who", MEMBERS + ("staff", "stranger"))
def test_only_admins_share_and_unshare(client, who):
    _complete(client)
    assert _share(client, who).status_code == 403
    assert client.delete("/api/admin/projects/alpha/cost-estimate/share", headers=_h(who)).status_code == 403
    assert _share(client, None).status_code == 401


def test_an_mcp_agent_is_refused_even_for_an_admin_or_a_member(client):
    """An MCP tool call runs in-process with the agent key owner set (``auth.use_agent_user``) and no Authorization header."""
    _complete(client)
    _share(client)
    request = Request({"type": "http", "method": "GET", "path": "/api/projects/alpha/quote", "headers": [], "query_string": b""})
    assert QR.get_quote("alpha", Request({**request.scope, "headers": [(b"authorization", b"Bearer owner")]}), Response())["total_price"]
    for who in ("admin", "owner"):
        with auth.use_agent_user(USERS[who]):
            with pytest.raises(HTTPException) as exc:
                QR.get_quote("alpha", request, Response())
        assert exc.value.status_code == 403, who
        with auth.use_agent_user(USERS[who]):
            with pytest.raises(HTTPException) as exc:
                R._admin(USERS["admin"])                                     # the share / unshare routes' dependency
        assert exc.value.status_code == 403, who


def test_the_mcp_guard_refuses_every_quote_key(client):
    from motor_ai_sim import mcp_tools as T
    _complete(client)
    _share(client)
    quote = _quote(client, "owner").json()
    with pytest.raises(AssertionError, match="commercial field leaked"):
        T.assert_no_commercial({"answer": quote})
    for leak in ({"quote_shared": True}, {"payment_terms": "x"}, {"exchange_rates": {}}, {"per_eur": {}}, {"total_price": 1},
                 {"shared_quote": {}}):
        with pytest.raises(AssertionError, match="commercial field leaked"):
            T.assert_no_commercial(leak)


# ── the frozen copy ─────────────────────────────────────────────────────────────────────────────────────────────────

def test_edits_after_sharing_stay_invisible_until_shared_again(client):
    est = _complete(client)
    shared = _share(client, revision=est["revision"])
    assert shared.status_code == 200, shared.text
    body = shared.json()
    assert body["shared"]["snapshot_n"] == 1 and body["shared"]["revision"] == est["revision"]
    assert body["snapshot"]["note"] == "shared" and "estimate" not in body["snapshot"]
    before = _quote(client, "owner").json()

    est["iterations"][0]["lines"]["tooling"]["amount"] = 5000.0
    est["settings"]["payment_terms"] = "100 % in advance"
    saved = client.put("/api/admin/projects/alpha/cost-estimate", json=est, headers=_h("admin"))
    assert saved.status_code == 200
    assert saved.json()["shared"]["revision"] == est["revision"]             # the admin sees which revision is shared
    assert _quote(client, "owner").json() == before                          # the customer still sees the shared numbers

    stale = _share(client, revision=est["revision"])                         # the admin's view is one save behind
    assert stale.status_code == 409
    again = _share(client, revision=saved.json()["estimate"]["revision"])
    assert again.status_code == 200 and again.json()["shared"]["snapshot_n"] == 2
    after = _quote(client, "owner").json()
    assert after["total_price"] == before["total_price"] + 4400                # (5000 - 1000) x 1.10
    assert after["payment_terms"] == "100 % in advance"


def test_sharing_is_refused_while_a_line_has_no_amount(client):
    est = client.get("/api/admin/projects/alpha/cost-estimate", headers=_h("admin")).json()["estimate"]
    assert _share(client).status_code == 409                                 # nothing saved yet
    _fill(est["iterations"][0]["lines"]["tooling"], 1000.0)
    assert client.put("/api/admin/projects/alpha/cost-estimate", json=est, headers=_h("admin")).status_code == 200
    resp = _share(client)
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "quote_incomplete"
    assert len(resp.json()["detail"]["missing"]) == 7
    assert CE.default_estimate(CE.load_schema(), "x")["settings"]["payment_terms"] == ""
    assert _quote(client, "owner").status_code == 404


def test_share_and_unshare_are_audited(client, tmp_path):
    _complete(client)
    _share(client)
    _share(client)
    client.delete("/api/admin/projects/alpha/cost-estimate/share", headers=_h("admin"))
    client.delete("/api/admin/projects/alpha/cost-estimate/share", headers=_h("admin"))   # nothing shared: no-op, no row
    rows = [json.loads(line) for line in
            (tmp_path / "projects" / "alpha" / "cost_estimate" / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    acts = [(r["action"], r["actor_email"], r["details"]) for r in rows if r["action"] != "cost_estimate.update"]
    assert acts == [("cost_estimate.share", "admin@example.test", {"snapshot": "0001", "revision": 1, "replaced": None}),
                    ("cost_estimate.share", "admin@example.test", {"snapshot": "0002", "revision": 1, "replaced": 1}),
                    ("cost_estimate.unshare", "admin@example.test", {"snapshot": 2, "revision": 1})]
    project_audit = tmp_path / "projects" / "alpha" / "audit.jsonl"
    assert not project_audit.exists() or "cost_estimate" not in project_audit.read_text(encoding="utf-8")


# ── the payload ─────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_payload_is_exactly_the_whitelist(client):
    _complete(client)
    _share(client)
    _switch(client, "viewer@example.test", True)
    q = _quote(client, "viewer").json()
    assert tuple(q) == Q.QUOTE_KEYS
    assert all(tuple(it) == Q.ITERATION_KEYS for it in q["iterations"])
    assert all(tuple(it["lines"]) == CE.LINES for it in q["iterations"])
    assert all(tuple(ln) == Q.LINE_KEYS for it in q["iterations"] for ln in it["lines"].values())
    assert tuple(q["exchange_rates"]) == Q.RATE_KEYS
    allowed = set(Q.QUOTE_KEYS) | set(Q.ITERATION_KEYS) | set(Q.LINE_KEYS) | set(Q.RATE_KEYS) | set(CE.LINES) | {"CNY", "USD"}
    keys = _keys(q)
    assert keys <= allowed, keys - allowed
    assert not keys & FORBIDDEN_KEYS
    text = json.dumps(q)
    for word in ("internal note", "Shanghai", "admin@example.test", "contingency", "9.0", "DIE-A"):
        assert word not in text, word
    assert q["currency"] == "EUR" and q["delivery"] == "DAP Buenos Aires"
    assert q["payment_terms"] == "70 % before the start of an iteration, 30 % before shipment"
    assert q["exchange_rates"] == {"date": "2026-11-02", "per_eur": {"CNY": 8.0, "USD": 1.25}}   # ECB, not the manual 9.0
    assert len(q["shared_on"]) == 10 and q["shared_on"][4] == "-"
    one, two = q["iterations"]
    assert (one["index"], one["motors"], one["bench"], two["bench"]) == (1, 2, "dyno", "none")
    assert one["weeks"] == 15 and two["weeks"] == 10 and q["total_weeks"] == 25
    assert one["lines"]["testing"]["weeks"] == 2 and two["lines"]["testing"] == {"price": 0, "weeks": None}


def test_contingency_is_folded_into_whole_eur_that_sum_exactly(client):
    _complete(client, contingency=7.5)
    _share(client)
    q = _quote(client, "owner").json()
    k = Decimal("1.075")
    # costs: it1 = 1000 + 666.66 + 250 + 101.11 = 2017.77; it2 = 200 + 666.66 + 0 + 101.11 = 967.77
    assert q["total_price"] == int((Decimal("2985.54") * k).quantize(Decimal(1), rounding="ROUND_HALF_UP")) == 3209
    assert sum(it["price"] for it in q["iterations"]) == q["total_price"]
    for it in q["iterations"]:
        prices = [ln["price"] for ln in it["lines"].values()]
        assert all(isinstance(p, int) for p in prices) and isinstance(it["price"], int)
        assert sum(prices) == it["price"]
    one = q["iterations"][0]["lines"]
    # 1075 + 716.66 -> 717 + 268.75 -> 269 + 108.69 -> 109 = 2170, but the iteration is 2169.10 -> 2169: the largest line gives 1
    assert [one[lid]["price"] for lid in CE.LINES] == [1074, 717, 269, 109] and q["iterations"][0]["price"] == 2169


def test_the_rounding_rule_adjusts_the_largest_part():
    # three lines of 0.5 EUR round up to 1 each (3) but the iteration is 2: the largest (first) line takes the difference
    assert Q._fit([Decimal("0.5"), Decimal("0.5"), Decimal("0.5")], 2) == [0, 1, 1]
    assert Q._fit([Decimal("10.4"), Decimal("20.4"), Decimal("5.4")], 37) == [10, 22, 5]
    schema = CE.load_schema()
    est = CE.default_estimate(schema, "alpha")
    for it in est["iterations"]:
        for lid in CE.LINES:
            it["lines"][lid].update({"status": "filled", "amount": 33.33})
    est["settings"]["contingency_pct"] = 12.5
    q = Q.build_quote(est, schema, "2026-11-03T10:00:00+00:00")
    assert q["shared_on"] == "2026-11-03" and q["delivery"] is None and q["exchange_rates"] is None
    assert sum(it["price"] for it in q["iterations"]) == q["total_price"] == 375        # 2 x (4 + 1 more motor) x 33.33 = 333.30 x 1.125
    for it in q["iterations"]:
        assert sum(ln["price"] for ln in it["lines"].values()) == it["price"]


# ── the project payload ─────────────────────────────────────────────────────────────────────────────────────────────

def test_the_project_payload_carries_only_the_flag(client):
    _complete(client)
    for who in ("owner", "viewer"):
        assert client.get("/api/projects/alpha", headers=_h(who)).json()["quote_shared"] is False
    _share(client)
    for email in ("eng@example.test", "viewer@example.test"):
        assert _switch(client, email, True).status_code == 200
    for who in MEMBERS + ("admin",):
        got = client.get("/api/projects/alpha", headers=_h(who))
        assert got.status_code == 200 and got.json()["quote_shared"] is True, who
        assert not {"shared_quote", "shared", "cost_estimate", "payment_terms", "total_price"} & _keys(got.json())
        assert "cost" not in got.text.lower() and "1000" not in got.text and "payment" not in got.text.lower()
    listed = client.get("/api/projects", headers=_h("owner")).json()["projects"]
    assert [(p["id"], p["quote_shared"]) for p in listed] == [("alpha", True)]
    # a global engineer is MOTRES staff: he reads every shared quote, member or not (coordinator 2026-11-23)
    staff = client.get("/api/projects", headers=_h("staff")).json()["projects"]
    assert {p["id"]: p["quote_shared"] for p in staff} == {"alpha": True, "beta": False}
    assert client.get("/api/projects", headers=_h("other")).json()["projects"][0]["quote_shared"] is False
    client.delete("/api/admin/projects/alpha/cost-estimate/share", headers=_h("admin"))
    assert client.get("/api/projects/alpha", headers=_h("owner")).json()["quote_shared"] is False


# ── the owner's member list for the switches (Astra pre-merge 2026-11-22) ───────────────────────────────────────────

def _switches(client, who, project="alpha"):
    return client.get(f"/api/projects/{project}", headers=_h(who)).json().get("member_switches")


def _invite(client, email, role="customer", who="owner", **extra):
    return client.post("/api/projects/alpha/members", json={"email": email, "role": role, **extra}, headers=_h(who))


@pytest.mark.parametrize("who", ("owner", "owner2", "admin"))
def test_an_ordinary_owner_gets_every_member_and_pending_invite_with_the_flag(client, who):
    """The summary of an ordinary (non-staff) owner holds only his own member record; the switches need the whole list."""
    assert _invite(client, "later@example.test", "engineer", sees_prices=True).status_code == 200
    assert _switch(client, "viewer@example.test", True).status_code == 200
    rows = {r["email"]: r for r in _switches(client, who)}
    assert set(rows) == {"customer@example.test", "eng@example.test", "viewer@example.test", "owner2@example.test", "later@example.test"}
    assert rows["customer@example.test"]["sees_prices"] is True and rows["owner2@example.test"]["sees_prices"] is True
    assert rows["eng@example.test"]["sees_prices"] is False and rows["viewer@example.test"]["sees_prices"] is True
    assert (rows["later@example.test"]["pending"], rows["later@example.test"]["role"], rows["later@example.test"]["sees_prices"]) == (True, "engineer", True)
    assert all(set(r) == {"email", "role", "pending", "sees_prices"} for r in rows.values())
    if who != "admin":
        mine = client.get("/api/projects/alpha", headers=_h(who)).json()
        assert [m["email"] for m in mine["members"]] == [f"{'customer' if who == 'owner' else 'owner2'}@example.test"]
        assert "notes" not in mine and "project_motor_grants" not in mine and "grant_removal_candidates" not in mine


def test_a_pending_invite_is_toggled_from_that_list(client):
    assert _invite(client, "later@example.test").status_code == 200
    assert {r["email"]: r["sees_prices"] for r in _switches(client, "owner")}["later@example.test"] is False
    assert _switch(client, "later@example.test", True).status_code == 200
    assert {r["email"]: r["sees_prices"] for r in _switches(client, "owner")}["later@example.test"] is True


@pytest.mark.parametrize("who", ("member-eng", "viewer", "stranger", "other"))
def test_no_other_caller_gets_the_switch_list(client, who):
    got = client.get("/api/projects/alpha", headers=_h(who))
    if got.status_code == 200:
        assert "member_switches" not in got.json()
    if who != "other":                    # "other" owns the project beta and gets the list of THAT project only
        assert "member_switches" not in client.get("/api/projects", headers=_h(who)).text


def test_a_re_role_keeps_the_flag_and_an_owner_role_drops_it(client):
    assert _invite(client, "later@example.test", "engineer", sees_prices=True).status_code == 200
    assert _invite(client, "later@example.test", "customer").status_code == 200                       # None keeps it
    assert {r["email"]: r["sees_prices"] for r in _switches(client, "owner")}["later@example.test"] is True
    assert _invite(client, "later@example.test", "owner").status_code == 200
    got = [p for p in store.get_project("alpha")["pending_invites"] if p["email"] == "later@example.test"][0]
    assert got["role"] == "owner" and "sees_prices" not in got
    assert _invite(client, "later@example.test", "customer").status_code == 200                       # back down: no stale flag
    assert {r["email"]: r["sees_prices"] for r in _switches(client, "owner")}["later@example.test"] is False


def test_a_revoked_member_who_is_invited_again_starts_without_the_flag(client):
    _complete(client)
    _share(client)
    assert _switch(client, "viewer@example.test", True).status_code == 200
    assert _quote(client, "viewer").status_code == 200
    assert _switch(client, "viewer@example.test", False).status_code == 200
    assert _quote(client, "viewer").status_code == 404
    assert client.delete("/api/projects/alpha/members?email=viewer@example.test", headers=_h("owner")).status_code == 200
    assert "viewer@example.test" not in {r["email"] for r in _switches(client, "owner")}
    assert _invite(client, "viewer@example.test").status_code == 200
    assert {r["email"]: r["sees_prices"] for r in _switches(client, "owner")}["viewer@example.test"] is False


def test_the_access_matrix_with_a_populated_next_unit(client):
    """Owners, admin and the switched-on member read the quote including the next unit; everybody else gets the same 404."""
    _complete(client)
    assert _share(client).status_code == 200
    assert _switch(client, "viewer@example.test", True).status_code == 200
    for who in ("owner", "owner2", "admin", "viewer", "staff"):
        assert _quote(client, who).status_code == 200, who
    for who in ("member-eng", "stranger", "other"):
        assert _quote(client, who).status_code == 404, who
    assert _quote(client, "").status_code in (401, 403)


# ── the central identity rule, indirect permission changes, durable audit, dry list (Astra 2026-11-23) ──────────────────────────

from motor_ai_sim.development import access  # noqa: E402


def _members(*rows):
    store.mutate_project("alpha", lambda p: p["members"].extend(rows))


def _events(project="alpha", action="sees_prices"):
    path = store._file(project, "audit.jsonl")
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.exists() else []
    return [r for r in rows if r["action"] == action]


def test_the_identity_rule_comes_before_roles_and_flags(client):
    """A factory account (global role oem) never sees prices - owner role or flag or not; staff always; the flag only for the rest."""
    _complete(client)
    _share(client)
    _members({"email": "factory@example.test", "role": "owner"}, {"email": "flagged@example.test", "role": "customer", "sees_prices": True})
    assert _quote(client, "factory-owner").status_code == 404
    assert _quote(client, "factory-flagged").status_code == 404
    assert client.get("/api/projects/alpha", headers=_h("factory-owner")).json()["quote_shared"] is False
    assert _quote(client, "staff").status_code == 200 and _quote(client, "admin").status_code == 200
    project = store.get_project("alpha")
    assert access.sees_prices({"email": "staff@example.test", "role": "engineer"}, project) is True
    assert access.sees_prices({"email": "factory@example.test", "role": "oem"}, project) is False
    assert access.sees_prices(None, project) is False


def test_an_agent_never_sees_prices_whatever_the_account(client):
    project = store.get_project("alpha")
    for who in ("owner", "admin", "staff"):
        assert access.sees_prices(USERS[who], project) is True
        with auth.use_agent_user(USERS[who]):
            assert access.sees_prices(USERS[who], project) is False and access.can_manage_prices(USERS[who], project) is False


def test_a_recipient_known_by_address_is_resolved_by_account(client, monkeypatch):
    from motor_ai_sim import users as U
    accounts = {"customer@example.test": {"role": "user"}, "factory@example.test": {"role": "oem"}, "staff@example.test": {"role": "engineer"},
                "off@example.test": {"role": "admin", "disabled": True}}
    monkeypatch.setattr(U, "get_user", lambda email: accounts.get(str(email).strip().lower()))
    _members({"email": "factory@example.test", "role": "owner"}, {"email": "off@example.test", "role": "owner"})
    project = store.get_project("alpha")
    got = {e: access.sees_prices_email(e, project) for e in accounts}
    assert got == {"customer@example.test": True, "factory@example.test": False, "staff@example.test": True, "off@example.test": False}
    assert access.sees_prices_email("nobody@example.test", project) is False


def test_a_project_engineer_cannot_demote_or_remove_a_price_holder(client, monkeypatch):
    """Astra: re-posting an existing owner as customer, or DELETE of an owner / flagged member, revoked price access indirectly."""
    from motor_ai_sim import users as U
    from motor_ai_sim.development import motors as MO
    monkeypatch.setattr(U, "get_user", lambda email: {"role": "user"})          # every address has an account: members, not pending
    monkeypatch.setattr(MO, "_sync_grants", lambda *a, **k: None)               # the motor grants are not under test
    _complete(client)
    _share(client)
    assert _switch(client, "viewer@example.test", True).status_code == 200
    add = lambda body, who: client.post("/api/projects/alpha/members", json=body, headers=_h(who))        # noqa: E731
    assert add({"email": "owner2@example.test", "role": "customer"}, "member-eng").status_code == 403
    assert add({"email": "owner2@example.test", "role": "engineer"}, "member-eng").status_code == 403
    assert client.delete("/api/projects/alpha/members?email=owner2@example.test", headers=_h("member-eng")).status_code == 403
    assert client.delete("/api/projects/alpha/members?email=viewer@example.test", headers=_h("member-eng")).status_code == 403
    assert _quote(client, "owner2").status_code == 200 and _quote(client, "viewer").status_code == 200
    # the same re-post without any change of the grant stays allowed (flag kept by None)
    assert add({"email": "viewer@example.test", "role": "customer"}, "member-eng").status_code == 200
    assert _quote(client, "viewer").status_code == 200
    # an owner and staff may do it
    assert add({"email": "viewer@example.test", "role": "engineer", "sees_prices": False}, "owner").status_code == 200
    assert _quote(client, "viewer").status_code == 404
    assert client.delete("/api/projects/alpha/members?email=owner2@example.test", headers=_h("owner")).status_code == 200


def test_global_engineers_may_switch_the_flag(client):
    assert _switch(client, "viewer@example.test", True, "staff").status_code == 200
    assert _switch(client, "viewer@example.test", False, "staff").status_code == 200
    assert [r["actor_email"] for r in _events()] == ["staff@example.test", "staff@example.test"]


def test_the_price_audit_is_committed_with_the_change(client, monkeypatch):
    """Astra reproduction: a failing audit append used to leave the flag on with no row, and the retry recorded nothing."""
    real = store.append_jsonl

    def broken(project_id, name, entry):
        if name == "audit.jsonl":
            raise OSError("disk full")
        return real(project_id, name, entry)

    monkeypatch.setattr(store, "append_jsonl", broken)
    resp = _switch(client, "viewer@example.test", True)
    assert resp.status_code == 200                                              # the change stands ...
    project = store.get_project("alpha")
    assert [m for m in project["members"] if m["email"] == "viewer@example.test"][0]["sees_prices"] is True
    pending = project["pending_audit"]                                           # ... and its row is stored WITH it
    assert [(e["action"], e["entity_id"], e["details"]) for e in pending] == [("sees_prices", "viewer@example.test", {"from": False, "to": True})]
    assert _events() == []
    monkeypatch.setattr(store, "append_jsonl", real)
    assert "pending_audit" not in client.get("/api/projects/alpha", headers=_h("owner")).json()
    assert _switch(client, "viewer@example.test", True).status_code == 200      # any later call writes the pending row once
    rows = _events()
    assert [(r["actor_email"], r["entity_id"], r["details"]["from"], r["details"]["to"]) for r in rows] == [
        ("customer@example.test", "viewer@example.test", False, True)]
    assert "pending_audit" not in store.get_project("alpha")
    assert _switch(client, "viewer@example.test", True).status_code == 200      # no change: no new row
    assert len(_events()) == 1


def test_role_changes_and_removals_of_a_price_holder_are_audited_with_old_and_new(client):
    assert client.post("/api/projects/alpha/members", json={"email": "owner2@example.test", "role": "customer"}, headers=_h("owner")).status_code == 200
    assert client.delete("/api/projects/alpha/members?email=viewer@example.test", headers=_h("owner")).status_code == 200      # no grant: no row
    assert _switch(client, "later@example.test", True).status_code == 404
    assert _switch(client, "eng@example.test", True).status_code == 200
    assert client.delete("/api/projects/alpha/members?email=eng@example.test", headers=_h("owner")).status_code == 200
    got = [(r["entity_id"], r["details"]["from"], r["details"]["to"]) for r in _events()]
    assert got == [("owner2@example.test", True, False), ("eng@example.test", False, True), ("eng@example.test", True, False)]


def test_the_dry_list_compares_before_and_after_per_account(client, tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("dry_list", __import__("pathlib").Path(__file__).resolve().parents[1] / "scripts" / "quote_price_visibility_dry_list.py")
    dry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dry)
    _complete(client)
    _share(client)
    _members({"email": "factory@example.test", "role": "owner"})
    users_file = tmp_path / "users.json"
    users_file.write_text(json.dumps({
        "admin@example.test": {"role": "admin"}, "staff@example.test": {"role": "engineer"}, "customer@example.test": {"role": "user"},
        "viewer@example.test": {"role": "user"}, "eng@example.test": {"role": "user"}, "owner2@example.test": {"role": "user"},
        "factory@example.test": {"role": "oem"}}), encoding="utf-8")
    accounts, bad = dry.load_accounts(users_file)
    assert bad is None
    rows, problems = dry.scan(tmp_path / "projects", accounts)
    row = [r for r in rows if r["project"] == "alpha"][0]
    assert problems == [] and row["snapshot_readable"] is True
    assert sorted(row["lose_the_view"]) == ["eng@example.test", "factory@example.test", "viewer@example.test"]
    assert row["gain_the_view"] == ["staff@example.test"]                   # admin keeps it, staff gains it
    assert row["members_without_account"] == []


def test_the_dry_list_reports_unreadable_inputs_instead_of_skipping(client, tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("dry_list2", __import__("pathlib").Path(__file__).resolve().parents[1] / "scripts" / "quote_price_visibility_dry_list.py")
    dry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dry)
    _complete(client)
    _share(client)
    root = tmp_path / "projects"
    snap = sorted((root / "alpha" / "cost_estimate" / "snapshots").glob("*.json"))[-1]
    snap.write_text("{broken", encoding="utf-8")
    (root / "beta" / "cost_estimate").mkdir(parents=True, exist_ok=True)
    (root / "beta" / "cost_estimate" / "shared_quote.json").write_text("not json", encoding="utf-8")
    rows, problems = dry.scan(root, {})
    assert {(p["project"], p["input"]) for p in problems} == {("alpha", "shared snapshot"), ("beta", "cost_estimate/shared_quote.json")}
    assert [r for r in rows if r["project"] == "alpha"][0]["snapshot_readable"] is False
    _, bad = dry.load_accounts(tmp_path / "missing_users.json")
    assert bad is None or "users file" in bad
    users_file = tmp_path / "u.json"
    users_file.write_text("[1", encoding="utf-8")
    assert "users file" in dry.load_accounts(users_file)[1]
    assert dry.main(["--root", str(root), "--users", str(users_file)]) == 1


# ── Astra round 2 (2026-11-23) ────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_flagged_project_engineer_cannot_make_himself_an_owner(client, monkeypatch):
    """Old and new price grants are both true, yet entering owner authority needs can_manage_prices."""
    from motor_ai_sim import users as U
    from motor_ai_sim.development import motors as MO
    monkeypatch.setattr(U, "get_user", lambda email: {"role": "user"})
    monkeypatch.setattr(MO, "_sync_grants", lambda *a, **k: None)
    assert _switch(client, "eng@example.test", True).status_code == 200
    add = lambda body, who: client.post("/api/projects/alpha/members", json=body, headers=_h(who))        # noqa: E731
    assert add({"email": "eng@example.test", "role": "owner"}, "member-eng").status_code == 403
    assert add({"email": "new@example.test", "role": "owner"}, "member-eng").status_code == 403
    role = {m["email"]: m["role"] for m in store.get_project("alpha")["members"]}
    assert role["eng@example.test"] == "engineer" and "new@example.test" not in role
    assert add({"email": "eng@example.test", "role": "owner"}, "owner").status_code == 200             # an owner may
    assert add({"email": "owner2@example.test", "role": "engineer"}, "viewer").status_code in (403, 404)   # leaving owner is gated too


def test_concurrent_member_changes_and_the_flag_switch_both_survive(client, monkeypatch):
    """POST /members used to write a snapshot over a PATCH that ran meanwhile (grant and audit intent lost)."""
    import threading
    import time
    from motor_ai_sim import users as U
    from motor_ai_sim.development import motors as MO
    monkeypatch.setattr(U, "get_user", lambda email: {"role": "user"})
    inside, release = threading.Event(), threading.Event()

    def slow_sync(*a, **k):                         # runs inside the membership change, before its write
        inside.set()
        time.sleep(0.4)

    monkeypatch.setattr(MO, "_sync_grants", slow_sync)
    out = {}
    t1 = threading.Thread(target=lambda: out.update(post=_invite(client, "late@example.test", "customer", sees_prices=True)))
    t1.start()
    assert inside.wait(5)
    out["patch"] = _switch(client, "viewer@example.test", True)                  # starts while the POST is mid-change
    t1.join(10)
    assert out["post"].status_code == 200 and out["patch"].status_code == 200
    project = store.get_project("alpha")
    flags = {m["email"]: m.get("sees_prices") for m in [*project["members"], *project.get("pending_invites", [])]}
    assert flags.get("viewer@example.test") is True and flags.get("late@example.test") is True
    pending = project.get("pending_audit", [])
    done = {r["entity_id"] for r in _events()} | {e["entity_id"] for e in pending}
    assert {"viewer@example.test", "late@example.test"} <= done


def test_the_project_payload_names_who_manages_prices(client):
    got = {who: client.get("/api/projects/alpha", headers=_h(who)).json().get("can_manage_prices") for who in
           ("owner", "owner2", "admin", "staff", "member-eng", "viewer")}
    assert got == {"owner": True, "owner2": True, "admin": True, "staff": True, "member-eng": False, "viewer": False}
    assert [r["email"] for r in _switches(client, "staff")]                      # staff get the member list for the switches


def _dry():
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location("dry_list3", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "quote_price_visibility_dry_list.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_dry_list_does_not_count_accountless_members_as_previous_readers(client, tmp_path):
    dry = _dry()
    _complete(client)
    _share(client)
    accounts = {"admin@example.test": "admin", "customer@example.test": "user", "owner2@example.test": "user"}     # viewer / eng: no account
    rows, problems = dry.scan(tmp_path / "projects", accounts)
    row = [r for r in rows if r["project"] == "alpha"][0]
    assert problems == []
    assert row["lose_the_view"] == [] and set(row["members_without_account"]) == {"viewer@example.test", "eng@example.test"}


def test_the_dry_list_gives_no_lists_for_an_unreadable_snapshot_and_reports_corrupt_projects(client, tmp_path):
    dry = _dry()
    _complete(client)
    _share(client)
    root = tmp_path / "projects"
    sorted((root / "alpha" / "cost_estimate" / "snapshots").glob("*.json"))[-1].write_text("{x", encoding="utf-8")
    (root / "gamma").mkdir()
    (root / "gamma" / "project.json").write_text("{corrupt", encoding="utf-8")        # no share at all
    rows, problems = dry.scan(root, {"admin@example.test": "admin"})
    row = [r for r in rows if r["project"] == "alpha"][0]
    assert row["snapshot_readable"] is False and row["lose_the_view"] is None and row["gain_the_view"] is None
    assert {(p["project"], p["input"]) for p in problems} == {("alpha", "shared snapshot"), ("gamma", "project.json")}


def test_the_contract_answers_a_hidden_and_an_unknown_project_alike(client):
    store.mutate_project("alpha", lambda p: None)
    hidden = client.get("/api/projects/alpha/contract", headers=_h("viewer"))
    unknown = client.get("/api/projects/nope/contract", headers=_h("viewer"))
    assert hidden.status_code == unknown.status_code == 404 and hidden.json() == unknown.json()


# ── Astra round 3 (2026-11-23) ────────────────────────────────────────────────────────────────────────────────────────────────

def _accounts(monkeypatch):
    from motor_ai_sim import users as U
    from motor_ai_sim.development import motors as MO
    monkeypatch.setattr(MO, "_sync_grants", lambda *a, **k: None)
    return U


def test_a_revoked_owner_invite_is_not_claimed_from_a_stale_copy(client, monkeypatch):
    from motor_ai_sim.development import routes as RT
    U = _accounts(monkeypatch)
    assert _invite(client, "late@example.test", "owner").status_code == 200
    assert [p["role"] for p in store.get_project("alpha")["pending_invites"] if p["email"] == "late@example.test"] == ["owner"]

    def revoke_then_account(email):                       # the invite is withdrawn after claim_pending_invites read it
        store.mutate_project("alpha", lambda p: p.update(pending_invites=[i for i in p["pending_invites"] if i["email"] != "late@example.test"]))
        return {"role": "user"}

    monkeypatch.setattr(U, "get_user", revoke_then_account)
    assert RT.claim_pending_invites("late@example.test") == []
    project = store.get_project("alpha")
    assert "late@example.test" not in {m["email"] for m in project["members"] + project["pending_invites"]}


def test_a_claim_takes_the_current_role_and_flag_of_the_invite(client, monkeypatch):
    from motor_ai_sim.development import routes as RT
    U = _accounts(monkeypatch)
    assert _invite(client, "late@example.test", "owner").status_code == 200

    def downgrade_then_account(email):                    # an owner demoted the invite to a flagged-off customer meanwhile
        def demote(p):
            for i in p["pending_invites"]:
                if i["email"] == "late@example.test":
                    i["role"] = "customer"
        store.mutate_project("alpha", demote)
        return {"role": "user"}

    monkeypatch.setattr(U, "get_user", downgrade_then_account)
    assert RT.claim_pending_invites("late@example.test") == ["alpha"]
    project = store.get_project("alpha")
    assert [m["role"] for m in project["members"] if m["email"] == "late@example.test"] == ["customer"]
    assert "late@example.test" not in {i["email"] for i in project["pending_invites"]}


def test_a_failed_index_write_undoes_only_its_own_change(client, monkeypatch):
    """The rollback used to restore the old WHOLE document, erasing a concurrent PATCH's grant and audit intent."""
    U = _accounts(monkeypatch)
    monkeypatch.setattr(U, "get_user", lambda email: {"role": "user"})
    real = store.sync_membership_index
    calls = {"n": 0}

    def failing(project_id, old, new):
        calls["n"] += 1
        if calls["n"] == 1:
            assert _switch(client, "viewer@example.test", True).status_code == 200      # a PATCH lands before the rollback
            raise OSError("index write failed")
        return real(project_id, old, new)

    monkeypatch.setattr(store, "sync_membership_index", failing)
    with pytest.raises(OSError):
        _invite(client, "late@example.test", "customer", sees_prices=True)
    monkeypatch.setattr(store, "sync_membership_index", real)
    project = store.get_project("alpha")
    assert {m["email"]: m.get("sees_prices") for m in project["members"]}["viewer@example.test"] is True       # the PATCH grant survives
    assert "late@example.test" not in {m["email"] for m in [*project["members"], *project.get("pending_invites", [])]}     # the POST is undone
    _flush = __import__("motor_ai_sim.development.routes", fromlist=["x"])._flush_price_audit
    _flush("alpha")
    assert "viewer@example.test" in [r["entity_id"] for r in _events()]            # the PATCH's audit intent survives
    assert [r["entity_id"] for r in _events(action="sees_prices_undone")] == ["late@example.test"]   # a flushed row of the undone POST is answered


# ── the admin-only Development line (owner 2026-11-26) never reaches the customer ──────────────────────────────────────

def _with_development(client, est, amount=1500.0, weeks=4):
    est["iterations"][0]["lines"]["development"] = {"amount": amount, "weeks": weeks, "note": "synthetic internal note"}
    resp = client.put("/api/admin/projects/alpha/cost-estimate", json=est, headers=_h("admin"))
    assert resp.status_code == 200, resp.text
    return resp.json()["estimate"]


def test_an_estimate_with_a_development_line_cannot_be_shared_or_quoted(client):
    est = _complete(client)
    saved = _with_development(client, est)
    assert saved["iterations"][0]["lines"]["development"]["amount"] == 1500.0
    resp = _share(client, revision=saved["revision"])
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "development_not_shareable"
    assert resp.json()["detail"]["iterations"] == [1]
    assert _quote(client, "owner").status_code == 404                       # nothing was shared
    with pytest.raises(Q.QuoteError, match="admin-only"):
        Q.build_quote(CE.from_stored(saved, CE.load_schema(), "alpha"), CE.load_schema(), "2026-11-26")


def test_a_quote_shared_before_the_development_line_carries_nothing_of_it(client):
    est = _complete(client)
    assert _share(client, revision=est["revision"]).status_code == 200
    before = _quote(client, "owner").json()
    _with_development(client, est)                                          # added to the LIVE estimate afterwards
    for who in ("owner", "admin"):                                          # members without the price flag get 404 anyway
        after = _quote(client, who)
        assert after.status_code == 200
        assert after.json() == before                                       # same numbers, same total, same weeks
        assert "development" not in json.dumps(after.json()).lower()
        assert "development" not in _keys(after.json())
    assert before["total_price"] == _quote(client, "owner").json()["total_price"]


def test_the_quote_of_an_estimate_without_the_line_is_unchanged_by_the_change(client):
    est = _complete(client)
    assert _share(client, revision=est["revision"]).status_code == 200
    quote = _quote(client, "owner").json()
    assert list(quote["iterations"][0]["lines"]) == ["tooling", "manufacturing", "testing", "shipping"]
    assert set(quote) == set(Q.QUOTE_KEYS)
