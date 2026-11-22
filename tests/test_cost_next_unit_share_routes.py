"""Cost estimate sharing with the next unit (owner 2026-11-22, Astra pre-merge review): a stale automatic next-unit shipping blocks
the share, an estimate-based shipping needs the admin's explicit word, the snapshot carries its terms version, and the combined
#800 access matrix on a populated ``next_unit``."""
from __future__ import annotations

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


def test_estimated_shipping_needs_the_explicit_acknowledgement_and_the_audit_keeps_it(client, tmp_path):
    est = _complete(client)
    line = est["iterations"][0]["lines"]["shipping"]
    line.update({"source": "dhl", "source_ref": {"dhl": {"basis": "rate_table", "zone": "EU", "zone_basis": "estimate"}}})
    est = _put(client, est)
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
    est = _complete(client)
    est["next_unit"]["shipping"].update({"status": "filled", "amount": 300.0, "currency": "EUR", "source": "manual", "weeks": 1})
    est = _put(client, est)
    assert _share(client, revision=est["revision"]).status_code == 200
    for who in ("admin", "owner", "member-eng", "viewer"):
        r = _quote(client, who)
        if r.status_code == 200:
            assert set(r.json()["next_unit"]) == {"price", "weeks"}
            assert "300" not in str(r.json()["next_unit"])
    assert _quote(client, "other").status_code == 404 and _quote(client, None).status_code in (401, 403, 404)
