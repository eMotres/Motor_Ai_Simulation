"""The project timeline the customer sees (owner 2026-11-03): coarse lanes per iteration, plan from the admin's start date and
weeks, forecast from the production of the linked motor.  Access is the rule of the quote: members of that project and
admins; nobody else, no MCP agent.

Temporary project and production stores, ECB served from a fixture string, no solver; neutral ids and made-up dates."""
from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.testclient import TestClient

from motor_ai_sim import auth
from motor_ai_sim.development import cost_estimate_fx as FX
from motor_ai_sim.development import cost_estimate_routes as CR
from motor_ai_sim.development import cost_estimate_sources as SRC
from motor_ai_sim.development import store
from motor_ai_sim.development import timeline as TL
from motor_ai_sim.development import timeline_routes as R
from motor_ai_sim.development.routes import router as project_router
from motor_ai_sim.production import store as PS

ECB_XML = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
<Cube><Cube time='2026-11-02'><Cube currency='USD' rate='1.2500'/><Cube currency='CNY' rate='8.0000'/></Cube></Cube>
</gesmes:Envelope>"""

USERS = {
    "admin": {"email": "admin@example.test", "role": "admin"},
    "owner": {"email": "customer@example.test", "role": "user"},          # project role "owner" of alpha
    "member-eng": {"email": "eng@example.test", "role": "engineer"},      # project role "engineer" of alpha
    "viewer": {"email": "viewer@example.test", "role": "user"},           # project role "customer" of alpha
    "other": {"email": "other@example.test", "role": "user"},             # member of beta only
    "staff": {"email": "staff@example.test", "role": "engineer"},         # a global engineer, member of nothing
    "factory": {"email": "factory@example.test", "role": "oem"},          # the production's OEM member, not a project member
    "stranger": {"email": "stranger@example.test", "role": "user"},
}
MEMBERS = ("owner", "member-eng", "viewer")
DIE, CFG = "DIE-A", "big"
FORBIDDEN = {"supplier", "price", "part", "bom", "cost", "part_id", "part_name", "po_number", "unit_price", "die", "config",
             "members", "email", "contact", "rows", "procurement"}


def _h(who):
    return {"Authorization": "Bearer " + who} if who else {}


def _user(authorization):
    return USERS.get(str(authorization or "").split(" ", 1)[-1].strip())


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
            "linked_motors": [{"die": DIE, "config": CFG, "status": "current"}], "notes": "", "pending_invites": []}
    store.create_project({**base, "id": "alpha", "title": "Alpha",
                          "members": [{"email": "customer@example.test", "role": "owner"},
                                      {"email": "eng@example.test", "role": "engineer"},
                                      {"email": "viewer@example.test", "role": "customer"}]})
    store.create_project({**base, "id": "beta", "title": "Beta", "members": [{"email": "other@example.test", "role": "owner"}]})
    app = FastAPI()
    app.include_router(CR.router)
    app.include_router(R.router)
    app.include_router(R.admin_router)
    app.include_router(project_router)
    return TestClient(app)


def _production(rows, tooling_rows=(), procurement=()):
    """A production of the linked motor: motor BOM ``rows``, tooling list ``tooling_rows``, procurement rows."""
    PS.save_file(DIE, CFG, "production.json", {"die": DIE, "config": CFG, "title": {"en": "Test motor", "zh": "测试电机"},
                                               "order_quantity": 2, "status": "active",
                                               "members": [{"email": "factory@example.test", "role": "oem"}]})
    PS.save_file(DIE, CFG, "parts.json", {"parts": [{"id": p, "name": {"en": "secret part " + p, "zh": ""}} for p in list(rows) + list(tooling_rows)]})
    PS.save_file(DIE, CFG, "bom.json", {"rows": [{"part_id": p, "qty_per_motor": 1} for p in rows]})
    PS.save_file(DIE, CFG, "bom_tooling.json", {"rows": [{"part_id": p, "qty_per_motor": 1} for p in tooling_rows]})
    PS.save_file(DIE, CFG, "procurement.json", {"rows": [{"supplier": "Secret Supplier Ltd", "po_number": "PO-1", **r} for r in procurement]})


def _timeline(start="2026-11-09", **lanes):
    """One iteration: tooling 4 wk, motor 3 wk, free-run 1, dyno 2, shipping 1 (overrides via ``lanes``)."""
    weeks = {"tooling": 4, "motor": 3, "free_run": 1, "dyno": 2, "shipping": 1, **lanes}
    return {"production": None, "iterations": [{"start_date": start, "lanes": {lid: ({"plan_weeks": w} if not isinstance(w, dict) else w)
                                                                              for lid, w in weeks.items()}}]}


def _put(client, body, revision=0, who="admin"):
    return client.put("/api/admin/projects/alpha/timeline", json={**body, "revision": revision}, headers=_h(who))


def _view(client, who="owner", project="alpha"):
    return client.get(f"/api/projects/{project}/timeline", headers=_h(who))


def _lanes(view, index=1):
    it = next(i for i in view["iterations"] if i["index"] == index)
    return {lane["lane"]: lane for lane in it["lanes"]}


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


@pytest.fixture
def today(monkeypatch):
    monkeypatch.setattr(R, "_today", lambda: "2026-11-03")
    return "2026-11-03"


# ── access ──────────────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("who", MEMBERS + ("admin",))
def test_members_of_the_project_and_admins_read_the_timeline(client, today, who):
    assert _put(client, _timeline()).status_code == 200
    resp = _view(client, who)
    assert resp.status_code == 200, resp.text
    assert resp.headers["cache-control"] == "no-store"
    assert [lane["lane"] for lane in resp.json()["iterations"][0]["lanes"]] == ["tooling", "motor", "free_run", "dyno", "shipping"]


@pytest.mark.parametrize("who", ["stranger", "other", "staff", "factory"])
def test_non_members_other_projects_global_engineers_and_the_factory_get_404(client, today, who):
    _production(["p1"])
    assert _put(client, _timeline()).status_code == 200
    resp = _view(client, who)
    assert resp.status_code == 404 and resp.json()["detail"] == "Timeline not found"
    assert resp.headers["cache-control"] == "no-store"
    assert _view(client, who, "nope").json() == resp.json()                 # same answer as an unknown project


def test_anonymous_callers_get_401_and_no_timeline_gives_404(client, today):
    assert _view(client, "owner").status_code == 404                        # nothing stored yet
    assert _put(client, _timeline()).status_code == 200
    assert _view(client, None).status_code == 401
    assert _view(client, "not-a-token").status_code == 401


@pytest.mark.parametrize("who", MEMBERS + ("staff", "stranger", "factory"))
def test_only_admins_edit_seed_and_freeze(client, who):
    assert client.get("/api/admin/projects/alpha/timeline", headers=_h(who)).status_code == 403
    assert _put(client, _timeline(), who=who).status_code == 403
    assert client.post("/api/admin/projects/alpha/timeline/seed", json={"revision": 0}, headers=_h(who)).status_code == 403
    assert client.post("/api/admin/projects/alpha/timeline/baseline", json={"revision": 0}, headers=_h(who)).status_code == 403
    assert _put(client, _timeline(), who=None).status_code == 401


def test_an_mcp_agent_is_refused_even_for_an_admin_or_a_member(client, today):
    assert _put(client, _timeline()).status_code == 200
    request = Request({"type": "http", "method": "GET", "path": "/api/projects/alpha/timeline", "headers": [], "query_string": b""})
    assert R.get_timeline("alpha", Request({**request.scope, "headers": [(b"authorization", b"Bearer owner")]}), Response())["iterations"]
    for who in ("admin", "owner"):
        with auth.use_agent_user(USERS[who]):
            with pytest.raises(HTTPException) as exc:
                R.get_timeline("alpha", request, Response())
        assert exc.value.status_code == 403, who
        with auth.use_agent_user(USERS[who]):
            with pytest.raises(HTTPException) as exc:
                R._admin(USERS["admin"])
        assert exc.value.status_code == 403, who


def test_the_project_payload_carries_only_a_flag(client, today):
    assert client.get("/api/projects/alpha", headers=_h("owner")).json()["timeline_ready"] is False
    assert _put(client, _timeline()).status_code == 200
    for who in MEMBERS + ("admin",):
        assert client.get("/api/projects/alpha", headers=_h(who)).json()["timeline_ready"] is True, who
    assert client.get("/api/projects/alpha", headers=_h("staff")).json()["timeline_ready"] is False   # sees the project, not the timeline


# ── payload whitelist ───────────────────────────────────────────────────────────────────────────────────────────────

def test_the_customer_payload_is_a_whitelist_without_parts_suppliers_prices_or_the_bom(client, today):
    _production(["p1", "p2"], ["t1"], [{"part_id": "p1", "due_on": "2026-12-20"}, {"part_id": "t1", "due_on": "2026-11-20"}])
    body = _timeline()
    body["iterations"][0]["lanes"]["motor"]["note"] = "Stator lamination batch\nsecond line"
    assert _put(client, body).status_code == 200
    view = _view(client).json()
    assert tuple(view) == TL.CUSTOMER_KEYS
    assert tuple(view["iterations"][0]) == TL.CUSTOMER_ITERATION_KEYS
    for lane in view["iterations"][0]["lanes"]:
        assert tuple(lane) == TL.CUSTOMER_LANE_KEYS
    keys = _keys(view)
    assert not keys & FORBIDDEN
    assert not any(bad in k for k in keys for bad in ("supplier", "price", "part", "bom", "cost"))
    text = str(view)
    for leak in ("Secret Supplier", "secret part", "PO-1", DIE, "factory@example.test"):
        assert leak not in text
    assert view["motor"] == {"en": "Test motor", "zh": "测试电机"}
    assert _lanes(view)["motor"]["note"] == "Stator lamination batch second line"              # one line


# ── plan ────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_plan_follows_start_and_weeks_with_the_dependency_rules():
    doc = TL.normalize_timeline(_timeline("2026-11-02", tooling=4, motor=3, propeller=1))
    p = TL.plan_iteration(doc["iterations"][0])
    assert p["tooling"] == {"start": "2026-11-02", "end": "2026-11-30"}
    assert p["motor"] == {"start": "2026-11-02", "end": "2026-11-30"}              # 3 wk, but it waits for the 4-wk tooling
    assert p["free_run"] == {"start": "2026-11-30", "end": "2026-12-07"}
    assert p["dyno"] == {"start": "2026-12-07", "end": "2026-12-21"}
    assert p["propeller"] == {"start": "2026-12-21", "end": "2026-12-28"}
    assert p["shipping"] == {"start": "2026-12-28", "end": "2027-01-04"}
    # a longer motor ends on its own weeks; a manual start only delays; without a start date nothing is dated
    doc = TL.normalize_timeline(_timeline("2026-11-02", tooling=1, motor=5, dyno={"plan_weeks": 2, "start": "2026-12-14"}))
    p = TL.plan_iteration(doc["iterations"][0])
    assert p["motor"]["end"] == "2026-12-07" and p["free_run"] == {"start": "2026-12-07", "end": "2026-12-14"}
    assert p["dyno"] == {"start": "2026-12-14", "end": "2026-12-28"}
    doc = TL.normalize_timeline(_timeline(None))
    assert all(v == {"start": None, "end": None} for v in TL.plan_iteration(doc["iterations"][0]).values())


def test_without_a_start_date_the_customer_sees_the_durations_only(client, today):
    assert _put(client, _timeline(None)).status_code == 200
    lanes = _lanes(_view(client).json())
    assert lanes["tooling"]["weeks"] == 4 and lanes["dyno"]["weeks"] == 2
    assert all(lane["plan_start"] is None and lane["forecast_end"] is None and lane["status"] == "planned" for lane in lanes.values())


def test_validation_refuses_bad_dates_weeks_and_lanes(client):
    for bad in (_timeline("2026-13-01"), _timeline(tooling=-1), _timeline(tooling=500),
                {"iterations": [{"start_date": "2026-11-02", "lanes": {"assembly": {}}}]},
                _timeline(motor={"plan_weeks": 1, "start": "2026-11-10", "end": "2026-11-01"})):
        assert _put(client, bad).status_code == 422, bad


# ── forecast from production ────────────────────────────────────────────────────────────────────────────────────────

def test_without_part_dates_the_forecast_is_the_plan(client, today):
    _production(["p1", "p2"], ["t1"])
    assert _put(client, _timeline()).status_code == 200
    lanes = _lanes(_view(client).json())
    for lid, lane in lanes.items():
        assert (lane["forecast_start"], lane["forecast_end"]) == (lane["plan_start"], lane["plan_end"]), lid
    assert lanes["tooling"]["status"] == "planned" and lanes["tooling"]["readiness_pct"] == 0.0
    assert lanes["motor"]["readiness_pct"] == 0.0 and lanes["dyno"]["readiness_pct"] is None


def test_a_later_due_date_shifts_the_motor_and_every_following_lane(client, today):
    # plan: 2026-11-09 + tooling 4 wk -> 12-07; motor waits -> 12-07; free-run -> 12-14; dyno -> 12-28; shipping -> 2027-01-04
    _production(["p1", "p2"], ["t1"], [{"part_id": "p1", "due_on": "2026-11-20"}, {"part_id": "p2", "due_on": "2026-12-21"},
                                        {"part_id": "t1", "confirmed_on": "2026-12-01"}])
    assert _put(client, _timeline()).status_code == 200
    lanes = _lanes(_view(client).json())
    assert lanes["tooling"]["plan_end"] == "2026-12-07" and lanes["tooling"]["forecast_end"] == "2026-12-01"
    assert lanes["tooling"]["status"] == "planned"
    assert lanes["motor"]["forecast_end"] == "2026-12-21" and lanes["motor"]["status"] == "late"      # 2 weeks later than planned
    assert (lanes["free_run"]["forecast_start"], lanes["free_run"]["forecast_end"]) == ("2026-12-21", "2026-12-28")
    assert (lanes["dyno"]["forecast_start"], lanes["dyno"]["forecast_end"]) == ("2026-12-28", "2027-01-11")
    assert lanes["shipping"]["forecast_end"] == "2027-01-18" and lanes["shipping"]["plan_end"] == "2027-01-04"
    assert all(lanes[lid]["status"] == "late" for lid in ("free_run", "dyno", "shipping"))
    # the factory moves the part earlier: the forecast follows on the next read
    PS.save_file(DIE, CFG, "procurement.json", {"rows": [{"part_id": "p1", "due_on": "2026-11-20"}, {"part_id": "p2", "due_on": "2026-12-01"},
                                                         {"part_id": "t1", "confirmed_on": "2026-12-01"}]})
    lanes = _lanes(_view(client).json())
    assert lanes["motor"]["forecast_end"] == "2026-12-01" and lanes["motor"]["status"] == "planned"


def test_an_undated_open_part_keeps_the_forecast_no_earlier_than_the_plan(client, today):
    _production(["p1", "p2"], [], [{"part_id": "p1", "due_on": "2026-11-15"}])          # p2 has no date
    assert _put(client, _timeline()).status_code == 200
    assert _lanes(_view(client).json())["motor"]["forecast_end"] == "2026-12-07"


def test_received_parts_give_readiness_and_finish_the_lane(client, today):
    _production(["p1", "p2", "p3", "p4"], ["t1"],
                [{"part_id": "p1", "qty_received": 2, "received_on": "2026-11-01"},
                 {"part_id": "p2", "qty_received": 2, "received_on": "2026-11-02"},
                 {"part_id": "p3", "due_on": "2026-11-30"}, {"part_id": "p4", "due_on": "2026-11-30"},
                 {"part_id": "t1", "qty_received": 1, "received_on": "2026-10-30"}])
    assert _put(client, _timeline("2026-10-12", motor=8)).status_code == 200            # motor planned to end 2026-12-07
    lanes = _lanes(_view(client).json())
    assert lanes["motor"]["readiness_pct"] == 50.0 and lanes["tooling"]["readiness_pct"] == 100.0
    assert lanes["tooling"]["status"] == "done" and lanes["tooling"]["forecast_end"] == "2026-10-30"
    assert lanes["motor"]["status"] == "in_progress" and lanes["motor"]["forecast_end"] == "2026-11-30"


def test_a_done_date_wins_and_an_unfinished_lane_never_ends_in_the_past(client, today):
    body = _timeline("2026-09-01", tooling=2, motor=2)                    # tooling and motor planned to end 2026-09-15
    body["iterations"][0]["lanes"]["tooling"]["done_date"] = "2026-09-10"
    assert _put(client, body).status_code == 200
    lanes = _lanes(_view(client).json())
    assert lanes["tooling"]["status"] == "done" and lanes["tooling"]["forecast_end"] == "2026-09-10"
    assert lanes["motor"]["forecast_end"] == "2026-11-03" and lanes["motor"]["status"] == "late"
    assert lanes["free_run"]["forecast_start"] == "2026-11-03"


def test_the_production_feeds_the_first_iteration_whose_lane_is_not_done(client, today):
    _production(["p1"], [], [{"part_id": "p1", "due_on": "2027-03-01"}])
    body = _timeline("2026-09-01", tooling=1, motor=1)
    body["iterations"][0]["lanes"]["tooling"]["done_date"] = "2026-09-08"
    body["iterations"][0]["lanes"]["motor"]["done_date"] = "2026-09-08"
    body["iterations"].append({"start_date": "2027-01-04", "lanes": {"tooling": {"plan_weeks": 2}, "motor": {"plan_weeks": 6}}})
    assert _put(client, body).status_code == 200
    view = _view(client).json()
    assert _lanes(view, 1)["motor"]["forecast_end"] == "2026-09-08" and _lanes(view, 1)["motor"]["readiness_pct"] is None
    two = _lanes(view, 2)
    assert two["motor"]["plan_end"] == "2027-02-15" and two["motor"]["forecast_end"] == "2027-03-01"
    assert two["motor"]["readiness_pct"] == 0.0


def test_the_admin_picks_which_linked_motor_drives_the_forecast(client, today):
    _production(["p1"], [], [{"part_id": "p1", "due_on": "2027-03-01"}])
    body = _timeline()
    body["production"] = {"die": "DIE-X", "config": "not-linked"}         # not linked: the current linked motor is used
    assert _put(client, body).status_code == 200
    got = client.get("/api/admin/projects/alpha/timeline", headers=_h("admin")).json()
    assert got["production"]["die"] == DIE and got["production"]["config"] == CFG
    assert _lanes(got["view"])["motor"]["forecast_end"] == "2027-03-01"


# ── seeding, baseline, revision, audit ─────────────────────────────────────────────────────────────────────────────

def _fill(line, weeks):
    line.update({"status": "filled", "amount": 100.0, "pct": None, "currency": "EUR", "source": "manual", "by": "admin@example.test",
                 "date": "2026-11-02", "note": "", "weeks": weeks})


def test_create_from_cost_estimate_fills_iterations_and_weeks_and_keeps_dates(client, today):
    seed = lambda rev: client.post("/api/admin/projects/alpha/timeline/seed", json={"revision": rev}, headers=_h("admin"))
    assert seed(0).status_code == 409                                     # no cost estimate yet
    est = client.get("/api/admin/projects/alpha/cost-estimate", headers=_h("admin")).json()["estimate"]
    one, two = est["iterations"]
    for lid, w in (("tooling", 6), ("manufacturing", 4), ("testing", 2), ("shipping", 3)):
        _fill(one["lines"][lid], w)
    for lid, w in (("tooling", 2), ("manufacturing", 5), ("testing", 3), ("shipping", 1)):
        _fill(two["lines"][lid], w)
    two["bench"] = "propeller"
    assert client.put("/api/admin/projects/alpha/cost-estimate", json=est, headers=_h("admin")).status_code == 200
    resp = seed(0)
    assert resp.status_code == 200, resp.text
    doc = resp.json()["timeline"]
    assert [it["start_date"] for it in doc["iterations"]] == [None, None]
    w = lambda it: {lid: lane["plan_weeks"] for lid, lane in it["lanes"].items()}
    assert w(doc["iterations"][0]) == {"tooling": 6, "motor": 4, "free_run": 1, "dyno": 2, "shipping": 3}
    assert w(doc["iterations"][1]) == {"tooling": 2, "motor": 5, "free_run": 1, "dyno": 0, "propeller": 3, "shipping": 1}
    # the admin sets a start date and a note, then seeds again: they stay, the weeks follow the estimate
    doc["iterations"][0]["start_date"] = "2026-11-16"
    doc["iterations"][0]["lanes"]["motor"]["note"] = "Waiting for magnets"
    put = _put(client, doc, revision=doc["revision"])
    assert put.status_code == 200
    again = seed(put.json()["timeline"]["revision"]).json()["timeline"]
    assert again["iterations"][0]["start_date"] == "2026-11-16"
    assert again["iterations"][0]["lanes"]["motor"]["note"] == "Waiting for magnets"
    assert again["revision"] == 3


def test_baseline_freeze_keeps_the_promised_plan_for_the_customer(client, today):
    assert client.post("/api/admin/projects/alpha/timeline/baseline", json={"revision": 0}, headers=_h("admin")).status_code == 409
    assert _put(client, _timeline()).status_code == 200
    resp = client.post("/api/admin/projects/alpha/timeline/baseline", json={"revision": 1}, headers=_h("admin"))
    assert resp.status_code == 200, resp.text
    base = resp.json()["timeline"]["baseline"]
    assert base["frozen_by"] == "admin@example.test" and base["iterations"][0]["lanes"]["shipping"]["end"] == "2027-01-04"
    # the admin then lengthens the dyno: the live plan moves, the customer's plan bar stays the baseline, the lane is late
    body = _timeline(dyno=4)
    saved = _put(client, body, revision=2)
    assert saved.status_code == 200
    assert saved.json()["timeline"]["baseline"] == base                  # a save never drops the baseline
    view = _view(client).json()
    assert view["baseline_on"] == base["frozen_at"][:10]
    dyno = _lanes(view)["dyno"]
    assert dyno["plan_end"] == "2026-12-28" and dyno["forecast_end"] == "2027-01-11" and dyno["status"] == "late"
    live = next(lane for lane in saved.json()["computed"][0]["lanes"] if lane["lane"] == "dyno")
    assert live["live_end"] == "2027-01-11"


def test_a_stale_revision_is_refused_with_409(client):
    assert _put(client, _timeline()).status_code == 200
    resp = _put(client, _timeline(), revision=0)
    assert resp.status_code == 409 and resp.json()["detail"]["revision"] == 1


def test_every_change_is_audited(client, today):
    assert _put(client, _timeline()).status_code == 200
    assert _put(client, _timeline(dyno=3), revision=1).status_code == 200
    assert client.post("/api/admin/projects/alpha/timeline/baseline", json={"revision": 2}, headers=_h("admin")).status_code == 200
    rows = R.read_audit("alpha")
    assert [r["action"] for r in rows] == ["timeline.baseline", "timeline.update", "timeline.update"]
    assert rows[1]["details"]["paths"] == ["iterations[1].lanes.dyno.plan_weeks"]
    assert all(r["actor_email"] == "admin@example.test" for r in rows)
    assert client.get("/api/admin/projects/alpha/timeline", headers=_h("admin")).json()["audit"][0]["action"] == "timeline.baseline"
