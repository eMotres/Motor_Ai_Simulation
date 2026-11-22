"""Certification section of the production documents (owner 2026-11-19): ``section: "certification"`` files a document in the
Certification tab; staff only upload / move it, the factory sees the non-internal ones, old rows without ``section`` stay in Documents."""
from __future__ import annotations

from motor_ai_sim.production import models
from tests.test_production_access import CONFIG, DIE, prod_env  # noqa: F401  (fixture)
from tests.test_production_files import _create

BASE = "/api/production/%s/%s/documents" % (DIE, CONFIG)
PDF = ("DoC_draft.pdf", b"%PDF-1.4 declaration", "application/pdf")


def _upload(env, who, data=None, name="engineer"):
    return env["client"].post(BASE, headers=env[who], data=data or {}, files={"file": PDF})


def test_old_documents_have_no_section_and_the_helper_is_backward_compatible():
    doc = models.normalize_document({}, "f" * 32, "a@b.c", "x.pdf")
    assert "section" not in doc and "group" not in doc and not models.is_certification(doc)
    assert models.normalize_certification_fields({"section": "nonsense"}) == {}
    n = models.normalize_certification_fields({"section": "Certification", "group": "  EU   DoC ", "cert_status": "bogus"})
    assert n == {"section": "certification", "group": "EU DoC", "cert_status": "draft"}


def test_staff_upload_into_certification_is_internal_by_default(prod_env):
    _create(prod_env)
    r = _upload(prod_env, "engineer", {"section": "certification", "group": "EU DoC"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["section"] == "certification" and d["group"] == "EU DoC" and d["cert_status"] == "draft" and d["internal"] is True
    r2 = _upload(prod_env, "engineer", {"section": "certification", "internal": "false", "cert_status": "signed"})
    assert r2.json()["internal"] is False and r2.json()["cert_status"] == "signed"


def test_the_factory_cannot_upload_into_certification_and_bad_values_are_refused(prod_env):
    _create(prod_env)
    assert _upload(prod_env, "oem_nda", {"section": "certification"}).status_code == 403
    assert _upload(prod_env, "engineer", {"section": "other"}).status_code == 422
    assert _upload(prod_env, "engineer", {"section": "certification", "cert_status": "x"}).status_code == 422
    plain = _upload(prod_env, "oem_nda")                      # a normal upload by the factory still works
    assert plain.status_code == 200 and "section" not in plain.json()


def test_factory_sees_only_released_certification_documents(prod_env):
    _create(prod_env)
    internal = _upload(prod_env, "engineer", {"section": "certification"}).json()
    released = _upload(prod_env, "engineer", {"section": "certification", "internal": "false"}).json()
    c = prod_env["client"]
    ids = lambda who: {d["id"] for d in c.get(BASE, headers=prod_env[who]).json()["documents"]}
    assert ids("engineer") == {internal["id"], released["id"]}
    assert ids("oem_nda") == {released["id"]}
    # the file of the internal one is not downloadable by the factory either
    url = "/api/production/%s/%s/files/%s" % (DIE, CONFIG, internal["file_id"])
    assert c.get(url, headers=prod_env["oem_nda"]).status_code in (403, 404)
    assert c.get(url, headers=prod_env["engineer"]).status_code == 200


def test_staff_move_a_document_between_documents_and_certification_and_set_group_status(prod_env):
    _create(prod_env)
    c = prod_env["client"]
    plain = _upload(prod_env, "engineer").json()
    url = "%s/%s" % (BASE, plain["id"])
    r = c.patch(url, headers=prod_env["engineer"], json={"section": "certification", "group": "Labelling", "cert_status": "evidence_received"})
    assert r.status_code == 200, r.text
    assert (r.json()["section"], r.json()["group"], r.json()["cert_status"]) == ("certification", "Labelling", "evidence_received")
    r = c.patch(url, headers=prod_env["engineer"], json={"group": "Technical file", "cert_status": "signed"})
    assert (r.json()["group"], r.json()["cert_status"]) == ("Technical file", "signed")
    r = c.patch(url, headers=prod_env["engineer"], json={"internal": False})
    assert r.json()["internal"] is False and r.json()["section"] == "certification"
    r = c.patch(url, headers=prod_env["engineer"], json={"section": None})
    assert "section" not in r.json() and "group" not in r.json() and "cert_status" not in r.json()
    # certification fields on a normal document are refused; unknown values too
    assert c.patch(url, headers=prod_env["engineer"], json={"group": "x"}).status_code == 422
    assert c.patch(url, headers=prod_env["engineer"], json={"section": "certification", "cert_status": "x"}).status_code == 422
    assert c.patch(url, headers=prod_env["engineer"], json={}).status_code == 400
    assert c.patch(BASE + "/doc_missing", headers=prod_env["engineer"], json={"internal": True}).status_code == 404


def test_the_factory_cannot_patch_a_document(prod_env):
    _create(prod_env)
    d = _upload(prod_env, "engineer", {"section": "certification", "internal": "false"}).json()
    r = prod_env["client"].patch("%s/%s" % (BASE, d["id"]), headers=prod_env["oem_nda"], json={"cert_status": "signed"})
    assert r.status_code in (403, 404)


def test_a_project_viewer_never_gets_a_certification_file_listed_or_downloadable():
    from motor_ai_sim.production import project_link
    docs = [{"id": "a", "file_id": "f1" + "0" * 30},
            {"id": "b", "file_id": "f2" + "0" * 30, "section": "certification", "internal": False},
            {"id": "c", "file_id": "f3" + "0" * 30, "internal": True}]
    assert [d["id"] for d in project_link.visible_documents(docs)] == ["a"]


def test_a_refused_certification_upload_stores_no_file(prod_env, monkeypatch):
    from motor_ai_sim.production import routes
    stored = []
    real = routes._store_upload

    async def _spy(*a, **k):
        stored.append(1)
        return await real(*a, **k)
    monkeypatch.setattr(routes, "_store_upload", _spy)
    _create(prod_env)
    assert _upload(prod_env, "oem_nda", {"section": "certification"}).status_code == 403
    assert _upload(prod_env, "engineer", {"section": "other"}).status_code == 422
    assert _upload(prod_env, "engineer", {"section": "certification", "cert_status": "x"}).status_code == 422
    assert stored == []
    assert _upload(prod_env, "engineer", {"section": "certification"}).status_code == 200
    assert stored == [1]
