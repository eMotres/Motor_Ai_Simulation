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
    "member-eng": {"email": "eng@example.test", "role": "engineer"},      # project role "engineer" of alpha
    "viewer": {"email": "viewer@example.test", "role": "user"},           # project role "customer" of alpha
    "other": {"email": "other@example.test", "role": "user"},             # member of beta only
    "staff": {"email": "staff@example.test", "role": "engineer"},         # a global engineer, member of nothing
    "stranger": {"email": "stranger@example.test", "role": "user"},
}
MEMBERS = ("owner", "member-eng", "viewer")
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
                                      {"email": "viewer@example.test", "role": "customer"}]})
    store.create_project({**base, "id": "beta", "title": "Beta", "members": [{"email": "other@example.test", "role": "owner"}]})
    app = FastAPI()
    app.include_router(R.router)
    app.include_router(QR.router)
    app.include_router(project_router)
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

@pytest.mark.parametrize("who", MEMBERS + ("admin",))
def test_members_of_the_project_and_admins_read_the_shared_quote(client, who):
    _complete(client)
    assert _share(client).status_code == 200
    resp = _quote(client, who)
    assert resp.status_code == 200, resp.text
    assert resp.headers["cache-control"] == "no-store"
    assert resp.json()["total_price"] > 0


@pytest.mark.parametrize("who", ["stranger", "other", "staff"])
def test_non_members_members_of_another_project_and_global_engineers_get_404(client, who):
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
    for who in MEMBERS + ("admin",):
        got = client.get("/api/projects/alpha", headers=_h(who))
        assert got.status_code == 200 and got.json()["quote_shared"] is True, who
        assert not {"shared_quote", "shared", "cost_estimate", "payment_terms", "total_price"} & _keys(got.json())
        assert "cost" not in got.text.lower() and "1000" not in got.text and "payment" not in got.text.lower()
    listed = client.get("/api/projects", headers=_h("owner")).json()["projects"]
    assert [(p["id"], p["quote_shared"]) for p in listed] == [("alpha", True)]
    # a global engineer sees the project but is no member: no quote for him, so no flag either
    staff = client.get("/api/projects", headers=_h("staff")).json()["projects"]
    assert {p["id"]: p["quote_shared"] for p in staff} == {"alpha": False, "beta": False}
    assert client.get("/api/projects", headers=_h("other")).json()["projects"][0]["quote_shared"] is False
    client.delete("/api/admin/projects/alpha/cost-estimate/share", headers=_h("admin"))
    assert client.get("/api/projects/alpha", headers=_h("owner")).json()["quote_shared"] is False
