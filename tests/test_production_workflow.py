"""Workflow tests for AeroStator Production module:
Proposal -> accept -> implement -> revision -> acknowledge lifecycle,
rejection, manual revision publishing, and idempotent acknowledgements.
"""
from __future__ import annotations

import pytest
from tests.test_production_access import (
    ADMIN,
    CONFIG,
    DIE,
    ENGINEER,
    OEM_NDA,
    OEM_NO_NDA,
    NON_MEMBER,
    prod_env,
)


def test_proposal_lifecycle_and_revisions(prod_env):
    client = prod_env["client"]
    admin = prod_env["admin"]
    engineer = prod_env["engineer"]
    oem_nda = prod_env["oem_nda"]
    non_member = prod_env["non_member"]

    # 1. Admin creates production with Engineer and OEM_NDA
    init_payload = {
        "title": {
            "en": "CIANO14 40 L12 Production",
            "zh": "CIANO14 40 L12 生产",
            "src_lang": "en",
            "status": {"en": "confirmed", "zh": "confirmed"},
        },
        "order_quantity": 100,
        "members": [
            {"email": ENGINEER, "role": "engineer"},
            {"email": OEM_NDA, "role": "oem"},
        ],
    }
    create_res = client.post(f"/api/admin/production/{DIE}/{CONFIG}", headers=admin, json=init_payload)
    assert create_res.status_code == 200

    # 2. Non-member cannot create proposal (404)
    res_non = client.post(
        f"/api/production/{DIE}/{CONFIG}/proposals",
        headers=non_member,
        json={"text": {"en": "Change rotor", "zh": "更改转子", "src_lang": "en"}},
    )
    assert res_non.status_code == 404

    # 3. OEM creates proposal
    proposal_text = {
        "en": "Request chamfer change on rotor teeth",
        "zh": "请求转子齿倒角修改",
        "src_lang": "en",
    }
    prop_res = client.post(
        f"/api/production/{DIE}/{CONFIG}/proposals",
        headers=oem_nda,
        json={"part_id": None, "text": proposal_text},
    )
    assert prop_res.status_code == 200
    prop = prop_res.json()
    assert prop["status"] == "open"
    assert prop["author_email"] == OEM_NDA
    assert prop["text"]["en"] == proposal_text["en"]
    assert prop["text"]["zh"] == proposal_text["zh"]
    prop_id = prop["id"]

    # 4. OEM cannot accept or implement proposals (403)
    oem_accept = client.post(
        f"/api/production/{DIE}/{CONFIG}/proposals/{prop_id}/accept",
        headers=oem_nda,
    )
    assert oem_accept.status_code == 403

    oem_impl = client.post(
        f"/api/production/{DIE}/{CONFIG}/proposals/{prop_id}/implement",
        headers=oem_nda,
    )
    assert oem_impl.status_code == 403

    # 5. Engineer accepts proposal with decision note
    eng_accept = client.post(
        f"/api/production/{DIE}/{CONFIG}/proposals/{prop_id}/accept",
        headers=engineer,
        json={"note": {"en": "Feasible in CNC setup", "zh": "数控装夹可行", "src_lang": "en"}},
    )
    assert eng_accept.status_code == 200
    prop_accepted = eng_accept.json()
    assert prop_accepted["status"] == "accepted"
    assert prop_accepted["decision_note"]["en"] == "Feasible in CNC setup"
    assert prop_accepted["resolved_at"] is not None

    # 6. Engineer implements proposal -> auto-publishes revision
    eng_impl = client.post(
        f"/api/production/{DIE}/{CONFIG}/proposals/{prop_id}/implement",
        headers=engineer,
    )
    assert eng_impl.status_code == 200
    prop_impl = eng_impl.json()
    assert prop_impl["status"] == "implemented"
    rev_number = prop_impl["linked_revision"]
    assert rev_number is not None

    # Verify revision in revisions list
    revs_res = client.get(f"/api/production/{DIE}/{CONFIG}/revisions", headers=oem_nda)
    assert revs_res.status_code == 200
    revisions = revs_res.json()["revisions"]
    assert len(revisions) == 1
    rev = revisions[0]
    assert rev["number"] == rev_number
    assert rev["acknowledgements"] == []

    # Verify production.json current_revision is bumped
    prod_get = client.get(f"/api/production/{DIE}/{CONFIG}", headers=oem_nda)
    assert prod_get.status_code == 200
    assert prod_get.json()["current_revision"] == rev_number

    # 7. OEM acknowledges the revision
    ack_res = client.post(
        f"/api/production/{DIE}/{CONFIG}/revisions/{rev_number}/ack",
        headers=oem_nda,
    )
    assert ack_res.status_code == 200
    ack_rev = ack_res.json()
    assert any(a["email"] == OEM_NDA for a in ack_rev["acknowledgements"])

    # Idempotent: repeated ack does not duplicate
    ack_res_repeat = client.post(
        f"/api/production/{DIE}/{CONFIG}/revisions/{rev_number}/ack",
        headers=oem_nda,
    )
    assert ack_res_repeat.status_code == 200
    ack_rev_repeat = ack_res_repeat.json()
    assert len([a for a in ack_rev_repeat["acknowledgements"] if a["email"] == OEM_NDA]) == 1


def test_proposal_rejection(prod_env):
    client = prod_env["client"]
    admin = prod_env["admin"]
    engineer = prod_env["engineer"]
    oem_nda = prod_env["oem_nda"]

    init_payload = {
        "title": {"en": "Rejection Test", "zh": "拒绝测试", "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}},
        "order_quantity": 20,
        "members": [
            {"email": ENGINEER, "role": "engineer"},
            {"email": OEM_NDA, "role": "oem"},
        ],
    }
    client.post(f"/api/admin/production/{DIE}/{CONFIG}", headers=admin, json=init_payload)

    # OEM creates proposal
    prop_res = client.post(
        f"/api/production/{DIE}/{CONFIG}/proposals",
        headers=oem_nda,
        json={"text": {"en": "Change magnet grade to N35", "zh": "磁钢更换为N35", "src_lang": "en"}},
    )
    assert prop_res.status_code == 200
    prop_id = prop_res.json()["id"]

    # Engineer rejects proposal
    rej_res = client.post(
        f"/api/production/{DIE}/{CONFIG}/proposals/{prop_id}/reject",
        headers=engineer,
        json={"note": {"en": "Flux density too low", "zh": "磁通密度过低", "src_lang": "en"}},
    )
    assert rej_res.status_code == 200
    prop = rej_res.json()
    assert prop["status"] == "rejected"
    assert prop["decision_note"]["en"] == "Flux density too low"
    assert prop["linked_revision"] is None

    # No revision was published
    revs = client.get(f"/api/production/{DIE}/{CONFIG}/revisions", headers=engineer).json()["revisions"]
    assert len(revs) == 0


def test_manual_revision_publish_and_ack(prod_env):
    client = prod_env["client"]
    admin = prod_env["admin"]
    engineer = prod_env["engineer"]
    oem_nda = prod_env["oem_nda"]

    init_payload = {
        "title": {"en": "Manual Rev Test", "zh": "手动版本测试", "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}},
        "order_quantity": 50,
        "members": [
            {"email": ENGINEER, "role": "engineer"},
            {"email": OEM_NDA, "role": "oem"},
        ],
    }
    client.post(f"/api/admin/production/{DIE}/{CONFIG}", headers=admin, json=init_payload)

    # OEM cannot publish revision -> 403
    oem_pub = client.post(
        f"/api/production/{DIE}/{CONFIG}/revisions",
        headers=oem_nda,
        json={"summary": {"en": "Illegal", "zh": "非法", "src_lang": "en"}, "changes": []},
    )
    assert oem_pub.status_code == 403

    # Engineer publishes manual revision
    rev_payload = {
        "summary": {
            "en": "Updated slot insulation thickness to 0.25mm",
            "zh": "定子槽绝缘厚度更新至0.25毫米",
            "src_lang": "en",
        },
        "changes": [
            {
                "en": "Slot liner material changed to Nomex 410",
                "zh": "槽绝缘纸材料变更为Nomex 410",
                "src_lang": "en",
            }
        ],
    }
    pub_res = client.post(
        f"/api/production/{DIE}/{CONFIG}/revisions",
        headers=engineer,
        json=rev_payload,
    )
    assert pub_res.status_code == 200
    rev = pub_res.json()
    assert rev["number"] == "1.0" or rev["number"] == "1.1"
    assert rev["author_email"] == ENGINEER
    assert rev["summary"]["en"] == rev_payload["summary"]["en"]
    assert len(rev["changes"]) == 1

    # OEM acknowledges
    ack_res = client.post(
        f"/api/production/{DIE}/{CONFIG}/revisions/{rev['number']}/ack",
        headers=oem_nda,
    )
    assert ack_res.status_code == 200
    assert any(a["email"] == OEM_NDA for a in ack_res.json()["acknowledgements"])
