"""The stored topic of a production document (owner 2026-11-24): PATCH ``topic``, audited, validated, visibility unchanged."""
from __future__ import annotations

import json

from motor_ai_sim.production import models
from tests.test_production_access import CONFIG, DIE, prod_env  # noqa: F401  (fixture)
from tests.test_production_certification import BASE, _upload
from tests.test_production_files import _create


# -- the topic of a document (owner 2026-11-24) -------------------------------------------------------------------------------

def test_an_editor_sets_and_clears_the_topic_audited_and_visibility_stays(prod_env):
    from motor_ai_sim.production import store
    _create(prod_env)
    c = prod_env["client"]
    internal = _upload(prod_env, "engineer").json()
    assert "topic" not in internal
    url = "%s/%s" % (BASE, internal["id"])
    assert c.patch(url, headers=prod_env["engineer"], json={"internal": True}).json()["internal"] is True
    r = c.patch(url, headers=prod_env["engineer"], json={"topic": "tooling"})
    assert r.status_code == 200, r.text
    assert r.json()["topic"] == "tooling" and r.json()["internal"] is True and "group" not in r.json() and "section" not in r.json()
    # a stored topic does not release the file: the factory still does not see an internal document
    assert internal["id"] not in {d["id"] for d in c.get(BASE, headers=prod_env["oem_nda"]).json()["documents"]}
    stored = {d["id"]: d for d in c.get(BASE, headers=prod_env["engineer"]).json()["documents"]}
    assert stored[internal["id"]]["topic"] == "tooling"
    # a topic together with another field, and back to the derived topic with null
    r = c.patch(url, headers=prod_env["engineer"], json={"topic": "tests", "internal": False})
    assert r.json()["topic"] == "tests" and r.json()["internal"] is False
    assert internal["id"] in {d["id"] for d in c.get(BASE, headers=prod_env["oem_nda"]).json()["documents"]}
    r = c.patch(url, headers=prod_env["engineer"], json={"topic": None})
    assert r.status_code == 200 and "topic" not in r.json()
    # the audit trail names the change
    rows = [json.loads(x) for x in store.audit_path(DIE, CONFIG).read_text(encoding="utf-8").splitlines() if x.strip()]
    topics = [x for x in rows if x.get("entity") == "document" and x.get("entity_id") == internal["id"] and "topic" in (x.get("snapshot") or {})]
    assert [x["snapshot"]["topic"] for x in topics] == ["tooling", "tests", None]


def test_a_bad_topic_is_refused_and_the_factory_cannot_set_one(prod_env):
    _create(prod_env)
    c = prod_env["client"]
    d = _upload(prod_env, "engineer", {"internal": "false"}).json()
    url = "%s/%s" % (BASE, d["id"])
    for bad in ("superseded", "Tooling", "nonsense", ["tooling"], 7, ""):
        assert c.patch(url, headers=prod_env["engineer"], json={"topic": bad}).status_code == 422
    assert c.patch(url, headers=prod_env["oem_nda"], json={"topic": "tooling"}).status_code in (403, 404)
    assert "topic" not in {k for x in c.get(BASE, headers=prod_env["engineer"]).json()["documents"] for k in x}
    assert models.DOCUMENT_TOPICS[0] == "process" and "superseded" not in models.DOCUMENT_TOPICS


def test_the_one_superseded_rule_status_wins_and_the_marker_is_exact():
    sup = models.is_superseded
    assert sup({"name": "a.pdf", "status": "superseded"})
    assert sup({"name": "[superseded] a.pdf"}) and sup({"name": "[SUPERSEDED] x"}) and sup({"name": "[superseded x"})
    assert not sup({"name": "[supersededness] current.pdf"})
    assert not sup({"name": " [superseded] a.pdf"}) and not sup({"name": "a [superseded].pdf"}) and not sup({"name": "[superseded"})
    assert not sup({"name": "a.pdf"}) and not sup(None)
