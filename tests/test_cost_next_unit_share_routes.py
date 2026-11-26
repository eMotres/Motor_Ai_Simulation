"""Cost estimate sharing with the next unit (owner 2026-11-22, Astra pre-merge review): a stale automatic next-unit shipping blocks
the share, an estimate-based shipping needs the admin's explicit word, the snapshot carries its terms version, and the combined
#800 access matrix on a populated ``next_unit``."""
from __future__ import annotations

import pytest

from tests.test_project_quote import _complete, _h, _quote, _share, client  # noqa: F401


def _put(client, est):
    r = client.put("/api/admin/projects/alpha/cost-estimate", json=est, headers=_h("admin"))
    assert r.status_code == 200, r.text
    return r.json()["estimate"]


def _auto_line(est, country="DE", hint=""):
    est["settings"]["ship_to"] = {"country": country, "city": "X"}
    est["next_unit"]["shipping"].update({
        "status": "filled", "amount": 600.0, "currency": "EUR", "source": "dhl", "weeks": 1, "hint": hint,
        "source_ref": {"dhl": {"basis": "rate_table", "zone": "EU", "zone_basis": "estimate", "from": "CN Shanghai", "to": country + " X"}}})


def test_a_stale_next_unit_shipping_blocks_the_share_until_manual_price_or_refresh(client):
    est = _complete(client)
    _auto_line(est, hint="no rate table zone for KP")
    est = _put(client, est)
    r = _share(client, revision=est["revision"])
    assert r.status_code == 409 and r.json()["detail"]["code"] == "next_unit_shipping_stale"
    est["next_unit"]["price"] = 4000.0
    est = _put(client, est)
    r = _share(client, revision=est["revision"], ack_estimated_shipping=True)
    assert r.status_code == 200, r.text


def test_estimated_shipping_needs_the_explicit_acknowledgement_and_the_audit_keeps_it(client, tmp_path, monkeypatch):
    from motor_ai_sim.development import cost_estimate_sources as SRC
    monkeypatch.setattr(SRC, "ship_data", lambda *_a, **_k: {"motor_mass_kg": 30.0, "motor_mass_basis": "complete"})   # the line can refresh
    est = _complete(client)
    est["settings"]["ship_to"] = {"country": "DE", "city": "X"}
    est["iterations"][0]["lines"]["shipping"].update({"source": "dhl", "source_ref": {"dhl": {"basis": "rate_table"}}})
    est = _put(client, est)
    line = est["iterations"][0]["lines"]["shipping"]
    assert line["source"] == "dhl" and not line["hint"] and line["source_ref"]["dhl"]["zone_basis"] == "estimate"      # recomputed on save
    r = _share(client, revision=est["revision"])
    assert r.status_code == 409 and r.json()["detail"]["code"] == "shipping_estimate_unacknowledged"
    assert r.json()["detail"]["lines"] == [{"iteration": 1, "zone": "EU", "basis": "estimate"}]
    assert _quote(client, "owner").status_code == 404                                  # nothing was shared
    assert _share(client, revision=est["revision"], ack_estimated_shipping=True).status_code == 200
    audit = (tmp_path / "projects" / "alpha" / "cost_estimate" / "audit.jsonl").read_text(encoding="utf-8")
    assert "estimated_shipping_acknowledged" in audit


def test_the_shared_snapshot_carries_the_terms_version_and_the_dap_sentence_without_a_next_unit(client):
    est = _complete(client)
    assert _share(client, revision=est["revision"]).status_code == 200
    q = _quote(client, "owner").json()
    assert q["next_unit"] is None and q["excluded"] == "Duties and import VAT are not included."


def test_access_matrix_with_a_populated_next_unit(client):
    """Explicit status per identity (Astra r2): a populated next unit with its own shipping; nothing passes by accident."""
    import json
    from fastapi import HTTPException, Request, Response
    from motor_ai_sim import auth
    from motor_ai_sim.development import quote_routes as QR
    from tests.test_project_quote import USERS, _members, _switch
    est = _complete(client)
    est["next_unit"]["shipping"].update({"status": "filled", "amount": 300.0, "currency": "EUR", "source": "manual", "weeks": 1})
    est = _put(client, est)
    assert _share(client, revision=est["revision"]).status_code == 200
    _members({"email": "factory@example.test", "role": "owner"}, {"email": "flagged@example.test", "role": "customer", "sees_prices": True})
    expected = {"admin": 200, "staff": 200, "owner": 200, "owner2": 200, "member-eng": 404, "viewer": 404, "other": 404, "stranger": 404,
                "factory-owner": 404, "factory-flagged": 404, None: 401}
    for who, status in expected.items():
        assert _quote(client, who).status_code == status, who
    # (manufacturing 333.33 + the next unit's shipping 300) x 1.10 contingency, whole EUR; weeks 4 + 1; shipping amount never shown
    body = _quote(client, "owner").json()
    assert body["next_unit"] == {"price": 697, "weeks": 5}
    assert body["excluded"] == "Duties and import VAT are not included."
    assert "300" not in json.dumps(body["next_unit"]) and list(body["iterations"][0]["lines"]) == ["tooling", "manufacturing", "testing", "shipping"]
    assert _quote(client, "admin").json() == body and _quote(client, "staff").json() == body
    # grant and revoke
    assert _switch(client, "viewer@example.test", True).status_code == 200
    assert _quote(client, "viewer").status_code == 200 and _quote(client, "viewer").json()["next_unit"] == {"price": 697, "weeks": 5}
    assert _switch(client, "viewer@example.test", False).status_code == 200
    assert _quote(client, "viewer").status_code == 404
    # an agent, even for an admin or an owner
    request = Request({"type": "http", "method": "GET", "path": "/api/projects/alpha/quote", "headers": [], "query_string": b""})
    for who in ("admin", "owner"):
        with auth.use_agent_user(USERS[who]):
            with pytest.raises(HTTPException) as exc:
                QR.get_quote("alpha", request, Response())
        assert exc.value.status_code == 403, who


def _dhl_iteration(est, country):
    est["settings"]["ship_to"] = {"country": country, "city": "X"}
    est["iterations"][0]["lines"]["shipping"].update({
        "status": "filled", "amount": 1200.0, "currency": "EUR", "source": "dhl", "weeks": 2, "hint": "",
        "source_ref": {"dhl": {"basis": "rate_table", "zone": "EU", "zone_basis": "calibrated", "from": "CN Shanghai", "to": "DE X"}}})


def test_a_stale_iteration_shipping_cannot_be_shared_whatever_the_next_unit_or_the_acknowledgement(client, monkeypatch):
    """Freight computed for DE must not be quoted as DAP KP (Astra review 2026-11-26): DE is shared first, the stale re-share is
    refused and the customer keeps the previous quote byte for byte."""
    from motor_ai_sim.development import cost_estimate_sources as SRC
    from motor_ai_sim.development import cost_estimate_store as CS
    monkeypatch.setattr(SRC, "ship_data", lambda *_a, **_k: {"motor_mass_kg": 30.0, "motor_mass_basis": "complete"})
    est = _complete(client)
    _dhl_iteration(est, "DE")
    est["next_unit"]["price"] = 4000.0                                   # an override of the next unit must not bypass it
    est = _put(client, est)
    line = est["iterations"][0]["lines"]["shipping"]
    assert line["source"] == "dhl" and not line["hint"]                  # a successful DE calculation
    assert _share(client, revision=est["revision"], ack_estimated_shipping=True).status_code == 200
    before, mark = _quote(client, "owner").content, CS.load_shared("alpha")
    est["settings"]["ship_to"] = {"country": "KP", "city": "X"}          # the destination changes; the rate table has no zone for it
    est = _put(client, est)
    assert est["iterations"][0]["lines"]["shipping"]["hint"]              # the refresh failed: the old amount is kept, with the reason
    r = _share(client, revision=est["revision"], ack_estimated_shipping=True)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "shipping_stale" and r.json()["detail"]["iterations"] == [1]
    assert _quote(client, "owner").content == before and CS.load_shared("alpha") == mark     # the previous quote is untouched
    # a manual number clears the refusal
    est["iterations"][0]["lines"]["shipping"].update({"source": "manual", "source_ref": None, "hint": ""})
    est = _put(client, est)
    assert _share(client, revision=est["revision"], ack_estimated_shipping=True).status_code == 200


def test_the_pure_quote_builder_refuses_a_stale_iteration_shipping_but_not_an_old_snapshot(client):
    from motor_ai_sim.development import cost_estimate as CE
    from motor_ai_sim.development import cost_quote as Q
    est = _complete(client)
    _dhl_iteration(est, "KP")                                            # computed for DE, the destination says KP
    schema = CE.load_schema()
    estimate = CE.from_stored(est, schema, "alpha")
    with pytest.raises(Q.QuoteError, match="out of date|another destination"):
        Q.build_quote(estimate, schema, "2026-11-26")
    assert Q.build_quote(estimate, schema, "2026-11-26", terms=1)["total_price"] > 0     # a snapshot frozen earlier is read as it was


def test_the_estimated_service_needs_the_acknowledgement_even_at_the_calibrated_point(client):
    """Express and air freight carry one factor and the one real quote is air freight: express is extrapolated, never calibrated."""
    from motor_ai_sim.development import cost_shipping_rates as SR
    table = SR.load_table()
    parcels = [{"weight_kg": 75.0, "box_cm": [60, 60, 40]}] * 2
    express, _ = SR.quote("AR", parcels, table, "express")
    air, _ = SR.quote("AR", parcels, table, "air_freight")
    assert express["amount"] == air["amount"]
    assert air["zone_basis"] == "calibrated" and express["zone_basis"] == "extrapolated"
