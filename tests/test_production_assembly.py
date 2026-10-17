"""Tests for BOM Assembly Graph, Assembly Hierarchy, Sub-assembly Rollup,
Permissions, and Receipt Timestamping.

Verification requirements:
- the default assembly tree;
- parent_id edit permissions (editor yes, OEM no) + audit;
- the cost roll-up per sub-assembly;
- receipt timestamp and completion rule.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from motor_ai_sim.api import app
from motor_ai_sim.production import models, store
from tests.test_production_access import (
    ADMIN,
    CONFIG,
    DIE,
    ENGINEER,
    NON_MEMBER,
    OEM_NDA,
    OEM_NO_NDA,
    prod_env,
)


@pytest.fixture()
def setup_assembly_env(prod_env):
    client = prod_env["client"]
    admin = prod_env["admin"]
    engineer = prod_env["engineer"]

    # 1. Create production
    prod_payload = {
        "title": {
            "en": "AeroStator 40 Assembly Production",
            "zh": "AeroStator 40 装配生产",
            "src_lang": "en",
            "status": {"en": "confirmed", "zh": "confirmed"},
        },
        "order_quantity": 20,
        "share_costs_with_oem": False,
        "members": [
            {"email": ENGINEER, "role": "engineer"},
            {"email": OEM_NDA, "role": "oem"},
        ],
    }
    client.post(f"/api/admin/production/{DIE}/{CONFIG}", headers=admin, json=prod_payload)

    # 2. Create canonical 40 mm motor parts (without parent_id)
    canonical_parts = [
        # Stator parts
        {"name": {"en": "Stator core 40_12", "zh": "定子铁芯 40_12", "src_lang": "en"}, "kind": "made", "qty_per_motor": 1},
        {"name": {"en": "Coil 40_12", "zh": "线圈 40_12", "src_lang": "en"}, "kind": "made", "qty_per_motor": 6},
        {"name": {"en": "Slot insulation", "zh": "槽绝缘", "src_lang": "en"}, "kind": "purchased", "qty_per_motor": 1},
        {"name": {"en": "Coil-fixing ring 40", "zh": "线圈固定环 40", "src_lang": "en"}, "kind": "made", "qty_per_motor": 2},
        {"name": {"en": "Phase board 40 rev C", "zh": "相板 40 rev C", "src_lang": "en"}, "kind": "made", "qty_per_motor": 1},
        {"name": {"en": "Lead wire", "zh": "引线", "src_lang": "en"}, "kind": "purchased", "qty_per_motor": 3},
        # Rotor parts
        {"name": {"en": "Rotor core 40_12", "zh": "转子铁芯 40_12", "src_lang": "en"}, "kind": "made", "qty_per_motor": 1},
        {"name": {"en": "Magnet 40_12", "zh": "磁钢 40_12", "src_lang": "en"}, "kind": "purchased", "qty_per_motor": 14},
        {"name": {"en": "Shaft 40_12", "zh": "轴 40_12", "src_lang": "en"}, "kind": "made", "qty_per_motor": 1},
        # Housing parts
        {"name": {"en": "Front holder 40", "zh": "前支架 40", "src_lang": "en"}, "kind": "made", "qty_per_motor": 1},
        {"name": {"en": "Back holder 40", "zh": "后支架 40", "src_lang": "en"}, "kind": "made", "qty_per_motor": 1},
        {"name": {"en": "Bearing 618/8-2Z C3", "zh": "轴承 618/8-2Z C3", "src_lang": "en"}, "kind": "purchased", "qty_per_motor": 2},
        {"name": {"en": "Socket head screw M2.5", "zh": "内六角螺钉 M2.5", "src_lang": "en"}, "kind": "purchased", "qty_per_motor": 6},
        {"name": {"en": "Prop_fix 40", "zh": "螺旋桨固定片 40", "src_lang": "en"}, "kind": "made", "qty_per_motor": 1},
    ]

    created_parts = []
    for cp in canonical_parts:
        res = client.post(f"/api/production/{DIE}/{CONFIG}/parts", headers=engineer, json=cp)
        assert res.status_code == 200
        created_parts.append(res.json())

    return {
        "client": client,
        "admin": admin,
        "engineer": engineer,
        "oem": prod_env["oem_nda"],
        "parts": created_parts,
    }


def test_default_assembly_tree_generation(setup_assembly_env):
    """Deterministic default assembly tree for 40 mm motor parts."""
    client = setup_assembly_env["client"]
    engineer = setup_assembly_env["engineer"]

    res = client.get(f"/api/production/{DIE}/{CONFIG}/assembly", headers=engineer)
    assert res.status_code == 200
    data = res.json()
    tree = data["tree"]
    assert tree["id"] == "motor"

    subs = {s["node"]["id"]: s for s in tree["sub_assemblies"]}
    assert "sub_stator" in subs
    assert "sub_rotor" in subs
    assert "sub_housing" in subs

    stator_children = [c["name"]["en"] for c in subs["sub_stator"]["children"]]
    rotor_children = [c["name"]["en"] for c in subs["sub_rotor"]["children"]]
    housing_children = [c["name"]["en"] for c in subs["sub_housing"]["children"]]

    # Stator assembly parts
    assert any("Stator core" in name for name in stator_children)
    assert any("Coil 40_12" in name for name in stator_children)
    assert any("Slot insulation" in name for name in stator_children)
    assert any("Coil-fixing ring" in name for name in stator_children)
    assert any("Phase board" in name for name in stator_children)
    assert any("Lead wire" in name for name in stator_children)

    # Rotor assembly parts
    assert any("Rotor core" in name for name in rotor_children)
    assert any("Magnet" in name for name in rotor_children)
    assert any("Shaft" in name for name in rotor_children)

    # Housing parts
    assert any("Front holder" in name for name in housing_children)
    assert any("Back holder" in name for name in housing_children)
    assert any("Bearing" in name for name in housing_children)
    assert any("Socket head screw" in name for name in housing_children)
    assert any("Prop_fix" in name for name in housing_children)


def test_parent_id_permissions_and_audit(setup_assembly_env):
    """Editors can change parent_id; OEM members cannot (403); change is recorded in audit."""
    client = setup_assembly_env["client"]
    engineer = setup_assembly_env["engineer"]
    oem = setup_assembly_env["oem"]
    parts = setup_assembly_env["parts"]
    test_part = parts[0]  # Stator core
    part_id = test_part["id"]

    # 1. OEM cannot change parent_id -> 403
    res_oem = client.patch(
        f"/api/production/{DIE}/{CONFIG}/parts/{part_id}",
        headers=oem,
        json={"parent_id": "sub_housing"},
    )
    assert res_oem.status_code == 403

    # 2. Engineer can change parent_id -> 200
    res_eng = client.patch(
        f"/api/production/{DIE}/{CONFIG}/parts/{part_id}",
        headers=engineer,
        json={"parent_id": "sub_housing"},
    )
    assert res_eng.status_code == 200
    assert res_eng.json()["parent_id"] == "sub_housing"

    # 3. Check audit log
    audit_file = store.audit_path(DIE, CONFIG)
    assert audit_file.is_file()
    lines = [json.loads(line) for line in audit_file.read_text(encoding="utf-8").strip().split("\n")]
    move_audits = [
        a for a in lines
        if a.get("action") == "update" and a.get("entity") == "part" and a.get("entity_id") == part_id
    ]
    assert len(move_audits) >= 1
    last_audit = move_audits[-1]
    assert last_audit["who"] == ENGINEER.lower()
    assert last_audit["snapshot"]["patch"]["parent_id"] == "sub_housing"


def test_cost_rollup_per_subassembly(setup_assembly_env):
    """Cost roll-up per sub-assembly: sub-assembly cost = sum of children costs."""
    client = setup_assembly_env["client"]
    engineer = setup_assembly_env["engineer"]
    parts = setup_assembly_env["parts"]

    # First apply default assembly structure
    apply_res = client.post(f"/api/production/{DIE}/{CONFIG}/assembly/default", headers=engineer)
    assert apply_res.status_code == 200

    parts_res = client.get(f"/api/production/{DIE}/{CONFIG}/parts", headers=engineer)
    all_parts = parts_res.json()["parts"]
    part_by_name = {p["name"]["en"]: p for p in all_parts}

    # Assign unit prices
    # Stator: Stator core = 100, Coil = 60, Phase board = 25 -> sum = 185
    # Rotor: Rotor core = 80, Magnet = 70, Shaft = 45 -> sum = 195
    # Housing: Front holder = 30, Back holder = 25 -> sum = 55
    cost_rows = [
        {"part_id": part_by_name["Stator core 40_12"]["id"], "unit_price": 100.0, "currency": "CNY"},
        {"part_id": part_by_name["Coil 40_12"]["id"], "unit_price": 60.0, "currency": "CNY"},
        {"part_id": part_by_name["Phase board 40 rev C"]["id"], "unit_price": 25.0, "currency": "CNY"},
        {"part_id": part_by_name["Rotor core 40_12"]["id"], "unit_price": 80.0, "currency": "CNY"},
        {"part_id": part_by_name["Magnet 40_12"]["id"], "unit_price": 70.0, "currency": "CNY"},
        {"part_id": part_by_name["Shaft 40_12"]["id"], "unit_price": 45.0, "currency": "CNY"},
        {"part_id": part_by_name["Front holder 40"]["id"], "unit_price": 30.0, "currency": "CNY"},
        {"part_id": part_by_name["Back holder 40"]["id"], "unit_price": 25.0, "currency": "CNY"},
    ]

    client.put(
        f"/api/production/{DIE}/{CONFIG}/costs",
        headers=engineer,
        json={"rows": cost_rows, "extras": [], "margin_pct": 10.0},
    )

    assembly_res = client.get(f"/api/production/{DIE}/{CONFIG}/assembly", headers=engineer)
    assert assembly_res.status_code == 200
    costs = assembly_res.json()["costs"]

    assert costs["sub_stator"] == 185.0
    assert costs["sub_rotor"] == 195.0
    assert costs["sub_housing"] == 55.0
    assert costs["motor"] == 435.0


def test_receipt_timestamp_and_completion_rule(setup_assembly_env):
    """Receipt timestamp recording and completion rule:
    - Partial receipt: received_on is None
    - Completing receipt with timestamp: received_on is set to that timestamp
    - Legacy receipt with date only: received_on is set to date
    """
    client = setup_assembly_env["client"]
    engineer = setup_assembly_env["engineer"]
    parts = setup_assembly_env["parts"]
    p1 = parts[0]["id"]  # qty_per_motor = 1, order_quantity = 20 -> qty_needed = 20

    # 1. Partial receipt with timestamp: accepted 8 < 20 -> status='partial', received_on=None
    client.put(
        f"/api/production/{DIE}/{CONFIG}/procurement/{p1}",
        headers=engineer,
        json={
            "ordered_on": "2026-10-10",
            "receipts": [
                {
                    "date": "2026-10-15",
                    "timestamp": "2026-10-15T10:30:00Z",
                    "qty": 8,
                    "accepted": 8,
                    "rejected": 0,
                    "inspector": "Inspector 1",
                    "note": "First batch",
                }
            ],
        },
    )
    res = client.get(f"/api/production/{DIE}/{CONFIG}/procurement", headers=engineer)
    row = next(r for r in res.json()["rows"] if r["part_id"] == p1)
    assert row["status"] == "partial"
    assert row["received_qty"] == 8.0
    assert row["received_on"] is None  # Empty until complete!

    # 2. Second receipt completes the quantity with timestamp: 8 + 12 = 20 >= 20 -> status='received', received_on='2026-10-17T14:45:00Z'
    client.put(
        f"/api/production/{DIE}/{CONFIG}/procurement/{p1}",
        headers=engineer,
        json={
            "ordered_on": "2026-10-10",
            "receipts": [
                {
                    "date": "2026-10-15",
                    "timestamp": "2026-10-15T10:30:00Z",
                    "qty": 8,
                    "accepted": 8,
                    "rejected": 0,
                    "inspector": "Inspector 1",
                },
                {
                    "date": "2026-10-17",
                    "timestamp": "2026-10-17T14:45:00Z",
                    "qty": 12,
                    "accepted": 12,
                    "rejected": 0,
                    "inspector": "Inspector 2",
                    "note": "Final batch completing order",
                },
            ],
        },
    )
    res = client.get(f"/api/production/{DIE}/{CONFIG}/procurement", headers=engineer)
    row = next(r for r in res.json()["rows"] if r["part_id"] == p1)
    assert row["status"] == "received"
    assert row["received_qty"] == 20.0
    assert row["received_on"] == "2026-10-17T14:45:00Z"
