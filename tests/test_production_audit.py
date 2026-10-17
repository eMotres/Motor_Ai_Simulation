"""Audit log tests for AeroStator Production module.
Verifies that all mutating operations append structured entries to audit.jsonl,
preserving chronological history with valid schema and actor identification.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest
from motor_ai_sim.production import store
from tests.test_production_access import (
    ADMIN,
    CONFIG,
    DIE,
    ENGINEER,
    OEM_NDA,
    prod_env,
)


def _load_audit_entries(die: str, config: str) -> list[dict]:
    p = store.audit_path(die, config)
    if not p.is_file():
        return []
    lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    return [json.loads(l) for l in lines]


def test_production_audit_trail_lifecycle(prod_env):
    client = prod_env["client"]
    admin = prod_env["admin"]
    engineer = prod_env["engineer"]
    oem_nda = prod_env["oem_nda"]

    # 1. Admin creates production -> audits "create" "production"
    init_payload = {
        "title": {
            "en": "Audit Trail Production",
            "zh": "审计追踪生产项目",
            "src_lang": "en",
            "status": {"en": "confirmed", "zh": "confirmed"},
        },
        "order_quantity": 50,
        "members": [
            {"email": ENGINEER, "role": "engineer"},
            {"email": OEM_NDA, "role": "oem"},
        ],
    }
    client.post(f"/api/admin/production/{DIE}/{CONFIG}", headers=admin, json=init_payload)

    entries = _load_audit_entries(DIE, CONFIG)
    assert len(entries) == 1
    e0 = entries[0]
    assert e0["who"] == ADMIN
    assert e0["action"] == "create"
    assert e0["entity"] == "production"
    assert e0["entity_id"] == f"{DIE}/{CONFIG}"

    # 2. Engineer creates a part -> audits "create" "part"
    part_payload = {
        "name": {"en": "Housing", "zh": "机壳", "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}},
        "kind": "made",
        "qty_per_motor": 1,
        "material": "Alloy",
        "spec": "OD150",
        "description": {"en": "CNC machined housing", "zh": "数控加工机壳", "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}},
    }
    part_res = client.post(f"/api/production/{DIE}/{CONFIG}/parts", headers=engineer, json=part_payload)
    part_id = part_res.json()["id"]

    entries = _load_audit_entries(DIE, CONFIG)
    assert len(entries) == 2
    e1 = entries[1]
    assert e1["who"] == ENGINEER
    assert e1["action"] == "create"
    assert e1["entity"] == "part"
    assert e1["entity_id"] == part_id

    # 3. Engineer patches the part -> audits "update" "part"
    client.patch(
        f"/api/production/{DIE}/{CONFIG}/parts/{part_id}",
        headers=engineer,
        json={"material": "AL6061-T6"},
    )
    entries = _load_audit_entries(DIE, CONFIG)
    assert len(entries) == 3
    e2 = entries[2]
    assert e2["who"] == ENGINEER
    assert e2["action"] == "update"
    assert e2["entity"] == "part"
    assert e2["entity_id"] == part_id

    # 4. Engineer updates BOM -> audits "update" "bom"
    client.put(
        f"/api/production/{DIE}/{CONFIG}/bom",
        headers=engineer,
        json={"rows": [{"part_id": part_id, "qty_per_motor": 1, "order_total": 50}]},
    )
    entries = _load_audit_entries(DIE, CONFIG)
    assert len(entries) == 4
    e3 = entries[3]
    assert e3["who"] == ENGINEER
    assert e3["action"] == "update"
    assert e3["entity"] == "bom"

    # 5. OEM creates proposal -> audits "create" "proposal"
    prop_res = client.post(
        f"/api/production/{DIE}/{CONFIG}/proposals",
        headers=oem_nda,
        json={"text": {"en": "Chamfer housing edge", "zh": "机壳边缘倒角", "src_lang": "en"}},
    )
    prop_id = prop_res.json()["id"]
    entries = _load_audit_entries(DIE, CONFIG)
    assert len(entries) == 5
    e4 = entries[4]
    assert e4["who"] == OEM_NDA
    assert e4["action"] == "create"
    assert e4["entity"] == "proposal"
    assert e4["entity_id"] == prop_id

    # 6. Engineer accepts proposal -> audits "accepted" "proposal"
    client.post(
        f"/api/production/{DIE}/{CONFIG}/proposals/{prop_id}/accept",
        headers=engineer,
        json={"note": {"en": "Approved", "zh": "批准", "src_lang": "en"}},
    )
    entries = _load_audit_entries(DIE, CONFIG)
    assert len(entries) == 6
    e5 = entries[5]
    assert e5["who"] == ENGINEER
    assert e5["action"] == "accepted"
    assert e5["entity"] == "proposal"

    # 7. Engineer implements proposal -> audits revision publication + proposal implementation
    impl_res = client.post(
        f"/api/production/{DIE}/{CONFIG}/proposals/{prop_id}/implement",
        headers=engineer,
    )
    assert impl_res.status_code == 200
    rev_number = impl_res.json()["linked_revision"]
    assert rev_number is not None

    entries = _load_audit_entries(DIE, CONFIG)
    # _decide_proposal implemented does _publish_revision (publishes revision audit) then _decide_proposal audit
    assert len(entries) >= 8
    rev_actions = [e["action"] for e in entries]
    assert "publish_revision" in rev_actions
    assert "implemented" in rev_actions

    # 8. OEM acknowledges revision -> audits "ack_revision" "revision"
    ack_res = client.post(
        f"/api/production/{DIE}/{CONFIG}/revisions/{rev_number}/ack",
        headers=oem_nda,
    )
    assert ack_res.status_code == 200
    entries = _load_audit_entries(DIE, CONFIG)
    assert any(e["action"] == "ack_revision" and e["who"] == OEM_NDA for e in entries)

    # 9. Validate schema across ALL audit entries
    for e in entries:
        assert "time" in e
        assert "who" in e
        assert "action" in e
        assert "entity" in e
        assert "entity_id" in e
        assert "snapshot" in e
        assert isinstance(e["snapshot"], dict)
        # Parse ISO timestamp to assert validity
        ts = e["time"]
        assert datetime.fromisoformat(ts) is not None
