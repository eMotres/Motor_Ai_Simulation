"""Electronics productions (owner 2026-10-18: the 800 V controller is produced in the module too) and internal documents."""
from __future__ import annotations

from tests.test_production_access import ADMIN, CONFIG, DIE, ENGINEER, OEM_NDA, prod_env  # noqa: F401  (fixture)


def _create(env, product_type="electronics"):
    payload = {"title": {"en": "Controller", "zh": "控制器", "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}},
               "order_quantity": 2, "product_type": product_type,
               "members": [{"email": ENGINEER, "role": "engineer"}, {"email": OEM_NDA, "role": "oem"}]}
    r = env["client"].post("/api/admin/production/%s/%s" % (DIE, CONFIG), json=payload, headers=env["admin"])
    assert r.status_code in (200, 201), r.text


def test_product_type_is_normalised_and_taken_at_creation(prod_env):
    from motor_ai_sim.production import models as M
    assert M.normalize_production({"product_type": "electronics"}, "D", "C")["product_type"] == "electronics"
    assert M.normalize_production({"product_type": "toaster"}, "D", "C")["product_type"] == "motor"
    assert M.normalize_production({}, "D", "C")["product_type"] == "motor"
    _create(prod_env)
    from motor_ai_sim.production import store as S
    assert S.load_file(DIE, CONFIG, "production.json")["product_type"] == "electronics"


def test_electronics_gets_its_own_tree_root_and_process(prod_env):
    from motor_ai_sim.production import models as M, store as S
    _create(prod_env)
    c = prod_env["client"]
    pr = c.get("/api/production/%s/%s/assembly_process" % (DIE, CONFIG), headers=prod_env["engineer"]).json()
    assert pr["default"] is True and pr["steps"][0]["id"] == "e1" and len(pr["steps"]) == 15
    tree = c.get("/api/production/%s/%s/assembly" % (DIE, CONFIG), headers=prod_env["engineer"]).json()["tree"]
    assert tree["name"]["en"] == "Assembly" and tree["sub_assemblies"] == []
    assert c.post("/api/production/%s/%s/assembly/default" % (DIE, CONFIG), headers=prod_env["engineer"]).status_code == 422
    assert M.build_assembly_tree([], "motor")["name"]["en"] == "Motor"


def test_internal_documents_are_hidden_from_the_factory(prod_env):
    _create(prod_env)
    c = prod_env["client"]
    url = "/api/production/%s/%s/documents" % (DIE, CONFIG)
    r = c.post(url, files={"file": ("notes.md", b"internal rationale", "text/markdown")}, headers=prod_env["engineer"])
    assert r.status_code in (200, 201), r.text
    doc = r.json()
    assert c.patch(url + "/" + doc["id"], json={"internal": True}, headers=prod_env["oem_nda"]).status_code == 403
    assert c.patch(url + "/" + doc["id"], json={"internal": True}, headers=prod_env["engineer"]).json()["internal"] is True
    assert [d["id"] for d in c.get(url, headers=prod_env["engineer"]).json()["documents"]] == [doc["id"]]
    assert c.get(url, headers=prod_env["oem_nda"]).json()["documents"] == []
    f = "/api/production/%s/%s/files/%s" % (DIE, CONFIG, doc["file_id"])
    assert c.get(f, headers=prod_env["oem_nda"]).status_code == 404
    assert c.get(f, headers=prod_env["engineer"]).status_code == 200
