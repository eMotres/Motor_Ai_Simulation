"""The Assembly tab shows the assembly PROCESS (owner 2026-10-17): default steps, editor-only changes, validation, audit."""
from __future__ import annotations

from tests.test_production_access import ADMIN, CONFIG, DIE, ENGINEER, OEM_NDA, prod_env  # noqa: F401  (fixture)


def _create(env):
    payload = {"title": {"en": "P", "zh": "P", "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}},
               "order_quantity": 10,
               "members": [{"email": ENGINEER, "role": "engineer"}, {"email": OEM_NDA, "role": "oem"}]}
    r = env["client"].post("/api/admin/production/%s/%s" % (DIE, CONFIG), json=payload, headers=env["admin"])
    assert r.status_code in (200, 201), r.text
    for name in ("Coil 40_12", "Magnet 40_12", "Shaft 40_12"):
        part = {"name": {"en": name, "zh": "", "src_lang": "en", "status": {"en": "confirmed", "zh": "pending"}},
                "kind": "made", "qty_per_motor": 1}
        assert env["client"].post("/api/production/%s/%s/parts" % (DIE, CONFIG), json=part, headers=env["engineer"]).status_code in (200, 201)


def _url():
    return "/api/production/%s/%s/assembly_process" % (DIE, CONFIG)


def test_default_process_is_the_owners_13_steps_with_two_branches(prod_env):
    _create(prod_env)
    r = prod_env["client"].get(_url(), headers=prod_env["oem_nda"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["default"] is True and len(d["steps"]) == 13
    by = {s["id"]: s for s in d["steps"]}
    assert by["s1"]["after"] == [] and by["s5"]["after"] == []          # stator and rotor branches start in parallel
    assert sorted(by["s9"]["after"]) == ["s4", "s8"]                     # they meet at the motor assembly
    assert by["s3"]["kind"] == "check" and by["s13"]["kind"] == "check"
    assert [p["name"]["en"] for p in by["s1"]["parts_resolved"]] == ["Coil 40_12"]
    assert [p["name"]["en"] for p in by["s7"]["parts_resolved"]] == ["Shaft 40_12"]
    assert "axial" in by["s6"]["note"]["en"]


def test_only_editors_change_the_process_and_cycles_are_refused(prod_env):
    _create(prod_env)
    c = prod_env["client"]
    steps = c.get(_url(), headers=prod_env["engineer"]).json()["steps"]
    steps.append({"id": "s14", "title": {"en": "Pack", "zh": "包装", "src_lang": "en"}, "kind": "operation", "after": ["s13"]})
    assert c.put(_url(), json={"steps": steps}, headers=prod_env["oem_nda"]).status_code == 403
    r = c.put(_url(), json={"steps": steps}, headers=prod_env["engineer"])
    assert r.status_code == 200 and r.json()["default"] is False and len(r.json()["steps"]) == 14
    cyc = [dict(s) for s in steps]
    cyc[0]["after"] = ["s14"]
    assert c.put(_url(), json={"steps": cyc}, headers=prod_env["engineer"]).status_code == 422
    unknown = [dict(s) for s in steps]
    unknown[1]["after"] = ["nope"]
    assert c.put(_url(), json={"steps": unknown}, headers=prod_env["engineer"]).status_code == 422


def test_a_new_part_is_a_bom_line_at_once(prod_env):
    _create(prod_env)
    rows = prod_env["client"].get("/api/production/%s/%s/bom" % (DIE, CONFIG), headers=prod_env["engineer"]).json()["rows"]
    parts = prod_env["client"].get("/api/production/%s/%s/parts" % (DIE, CONFIG), headers=prod_env["engineer"]).json()["parts"]
    assert {r["part_id"] for r in rows} >= {p["id"] for p in parts if p.get("kind") != "assembly"}
