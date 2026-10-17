"""Tests for Production Readiness Tracker & Project Timeline.

Covers:
- Dynamic procurement statuses (received, partial, late, at_risk, confirmed, ordered, to_order, not_ordered)
- Receipt inspection validation (accepted + rejected == qty, date ordering, calendar validation)
- Readiness summary and assembly risk warnings
- Critical Path Method (CPM) and slack calculation on task DAG
- Cycle detection in depends_on DAG (refused with 422)
- Schedule baseline freezing (POST /schedule/baseline, editor-only, OEM blocked with 403)
- Automatic milestones (All POs placed, All parts received)
- Dynamic Ordering phase actual dates derivation
- OEM permissions (OEM can edit procurement and Ordering/Assembly/Testing actuals; cannot edit BOM, parts, baseline or milestones)
- Audit log trail (old -> new values recorded)
- Cross-configuration die timeline (GET /api/production/{die}/timeline respecting access)
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
def setup_tracker_env(prod_env):
    client = prod_env["client"]
    admin = prod_env["admin"]
    engineer = prod_env["engineer"]

    prod_payload = {
        "title": {
            "en": "AeroStator Tracker Production",
            "zh": "AeroStator 跟踪生产",
            "src_lang": "en",
            "status": {"en": "confirmed", "zh": "confirmed"},
        },
        "order_quantity": 10,
        "share_costs_with_oem": False,
        "members": [
            {"email": ENGINEER, "role": "engineer"},
            {"email": OEM_NDA, "role": "oem"},
            {"email": OEM_NO_NDA, "role": "oem"},
        ],
    }
    client.post(f"/api/admin/production/{DIE}/{CONFIG}", headers=admin, json=prod_payload)

    # Create 3 parts
    p1 = client.post(
        f"/api/production/{DIE}/{CONFIG}/parts",
        headers=engineer,
        json={
            "name": {"en": "Stator core", "zh": "定子铁芯", "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}},
            "kind": "made",
            "qty_per_motor": 1,
            "material": "Silicon Steel",
            "spec": "OD140",
            "description": {"en": "Stator stack", "zh": "定子叠片", "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}},
        },
    ).json()

    p2 = client.post(
        f"/api/production/{DIE}/{CONFIG}/parts",
        headers=engineer,
        json={
            "name": {"en": "Magnet 40_12", "zh": "磁钢 40_12", "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}},
            "kind": "purchased",
            "qty_per_motor": 14,
            "material": "N48SH",
            "spec": "20x4x2",
            "description": {"en": "Magnets", "zh": "磁体", "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}},
        },
    ).json()

    p3 = client.post(
        f"/api/production/{DIE}/{CONFIG}/parts",
        headers=engineer,
        json={
            "name": {"en": "Shaft", "zh": "转轴", "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}},
            "kind": "made",
            "qty_per_motor": 1,
            "material": "SUS420",
            "spec": "D15",
            "description": {"en": "Rotor shaft", "zh": "电机轴", "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}},
        },
    ).json()

    # Save BOM
    bom_rows = [
        {"part_id": p1["id"], "qty_per_motor": 1, "order_total": 10},
        {"part_id": p2["id"], "qty_per_motor": 14, "order_total": 140},
        {"part_id": p3["id"], "qty_per_motor": 1, "order_total": 10},
    ]
    client.put(f"/api/production/{DIE}/{CONFIG}/bom", headers=engineer, json={"rows": bom_rows})

    return {
        "client": client,
        "admin": admin,
        "engineer": engineer,
        "oem": prod_env["oem_nda"],
        "oem_no_nda": prod_env["oem_no_nda"],
        "non_member": prod_env["non_member"],
        "p1": p1,
        "p2": p2,
        "p3": p3,
    }


def test_status_priorities_all_reachable(setup_tracker_env):
    """Verify that every status in the priority list is reachable:
    received, partial, late, at_risk, confirmed, ordered, to_order, not_ordered.
    """
    client = setup_tracker_env["client"]
    eng = setup_tracker_env["engineer"]
    p1 = setup_tracker_env["p1"]["id"]

    today = "2026-10-20"

    # 1. not_ordered: initially no fields set
    res = client.get(f"/api/production/{DIE}/{CONFIG}/procurement?today={today}", headers=eng)
    assert res.status_code == 200
    rows = {r["part_id"]: r for r in res.json()["rows"]}
    assert rows[p1]["status"] == "not_ordered"

    # 2. to_order: need_by set, lead_time_days set, today >= order_by (2026-10-25 - 10 = 2026-10-15 <= today)
    client.put(
        f"/api/production/{DIE}/{CONFIG}/procurement/{p1}",
        headers=eng,
        json={"need_by": "2026-10-25", "lead_time_days": 10},
    )
    res = client.get(f"/api/production/{DIE}/{CONFIG}/procurement?today={today}", headers=eng)
    rows = {r["part_id"]: r for r in res.json()["rows"]}
    assert rows[p1]["status"] == "to_order"
    assert rows[p1]["order_by"] == "2026-10-15"

    # 3. ordered: ordered_on set, expected arrival in future and before need_by
    client.put(
        f"/api/production/{DIE}/{CONFIG}/procurement/{p1}",
        headers=eng,
        json={"ordered_on": "2026-10-16", "need_by": "2026-10-30", "lead_time_days": 5},  # arrival: 2026-10-21
    )
    res = client.get(f"/api/production/{DIE}/{CONFIG}/procurement?today={today}", headers=eng)
    rows = {r["part_id"]: r for r in res.json()["rows"]}
    assert rows[p1]["status"] == "ordered"
    assert rows[p1]["expected_arrival"] == "2026-10-21"

    # 4. confirmed: supplier confirmed_on date set
    client.put(
        f"/api/production/{DIE}/{CONFIG}/procurement/{p1}",
        headers=eng,
        json={"ordered_on": "2026-10-16", "confirmed_on": "2026-10-22", "need_by": "2026-10-30"},
    )
    res = client.get(f"/api/production/{DIE}/{CONFIG}/procurement?today={today}", headers=eng)
    rows = {r["part_id"]: r for r in res.json()["rows"]}
    assert rows[p1]["status"] == "confirmed"
    assert rows[p1]["expected_arrival"] == "2026-10-22"

    # 5. at_risk: expected_arrival > need_by
    client.put(
        f"/api/production/{DIE}/{CONFIG}/procurement/{p1}",
        headers=eng,
        json={"ordered_on": "2026-10-16", "confirmed_on": "2026-11-05", "need_by": "2026-10-30"},
    )
    res = client.get(f"/api/production/{DIE}/{CONFIG}/procurement?today={today}", headers=eng)
    rows = {r["part_id"]: r for r in res.json()["rows"]}
    assert rows[p1]["status"] == "at_risk"
    assert rows[p1]["is_at_risk"] is True

    # 6. late: expected_arrival < today and not fully received
    client.put(
        f"/api/production/{DIE}/{CONFIG}/procurement/{p1}",
        headers=eng,
        json={"ordered_on": "2026-10-10", "confirmed_on": "2026-10-18", "need_by": "2026-10-30"},
    )
    res = client.get(f"/api/production/{DIE}/{CONFIG}/procurement?today={today}", headers=eng)
    rows = {r["part_id"]: r for r in res.json()["rows"]}
    assert rows[p1]["status"] == "late"
    assert rows[p1]["is_late"] is True

    # 7. partial: some accepted (< 10 qty_needed)
    client.put(
        f"/api/production/{DIE}/{CONFIG}/procurement/{p1}",
        headers=eng,
        json={
            "ordered_on": "2026-10-10",
            "confirmed_on": "2026-10-25",
            "need_by": "2026-10-30",
            "receipts": [
                {"date": "2026-10-18", "qty": 4, "accepted": 4, "rejected": 0, "inspector": "Alice", "note": "batch 1"}
            ],
        },
    )
    res = client.get(f"/api/production/{DIE}/{CONFIG}/procurement?today={today}", headers=eng)
    rows = {r["part_id"]: r for r in res.json()["rows"]}
    assert rows[p1]["status"] == "partial"
    assert rows[p1]["received_qty"] == 4.0

    # 8. received: accepted >= qty_needed (10)
    client.put(
        f"/api/production/{DIE}/{CONFIG}/procurement/{p1}",
        headers=eng,
        json={
            "ordered_on": "2026-10-10",
            "confirmed_on": "2026-10-25",
            "need_by": "2026-10-30",
            "receipts": [
                {"date": "2026-10-18", "qty": 4, "accepted": 4, "rejected": 0, "inspector": "Alice", "note": "batch 1"},
                {"date": "2026-10-19", "qty": 6, "accepted": 6, "rejected": 0, "inspector": "Alice", "note": "batch 2"},
            ],
        },
    )
    res = client.get(f"/api/production/{DIE}/{CONFIG}/procurement?today={today}", headers=eng)
    rows = {r["part_id"]: r for r in res.json()["rows"]}
    assert rows[p1]["status"] == "received"
    assert rows[p1]["received_qty"] == 10.0
    assert rows[p1]["received_on"] == "2026-10-19"


def test_receipt_inspection_and_validation(setup_tracker_env):
    """Incoming inspection validation: accepted + rejected == qty, date ordering, invalid formats."""
    client = setup_tracker_env["client"]
    eng = setup_tracker_env["engineer"]
    p1 = setup_tracker_env["p1"]["id"]

    # 1. Accepted + rejected != qty -> 422
    res = client.put(
        f"/api/production/{DIE}/{CONFIG}/procurement/{p1}",
        headers=eng,
        json={
            "ordered_on": "2026-10-10",
            "receipts": [
                {"date": "2026-10-15", "qty": 10, "accepted": 8, "rejected": 1, "inspector": "Bob", "note": "mismatch"}
            ],
        },
    )
    assert res.status_code == 422
    assert "inspection mismatch" in res.json()["detail"].lower()

    # 2. Receipt date before ordered_on -> 422
    res = client.put(
        f"/api/production/{DIE}/{CONFIG}/procurement/{p1}",
        headers=eng,
        json={
            "ordered_on": "2026-10-15",
            "receipts": [
                {"date": "2026-10-10", "qty": 5, "accepted": 5, "rejected": 0, "inspector": "Bob", "note": "too early"}
            ],
        },
    )
    assert res.status_code == 422
    assert "cannot be before" in res.json()["detail"].lower()

    # 3. Invalid calendar date -> 422
    res = client.put(
        f"/api/production/{DIE}/{CONFIG}/procurement/{p1}",
        headers=eng,
        json={"ordered_on": "2026-02-31"},
    )
    assert res.status_code == 422
    assert "invalid calendar date" in res.json()["detail"].lower()

    # 4. Garbage date format -> 422
    res = client.put(
        f"/api/production/{DIE}/{CONFIG}/procurement/{p1}",
        headers=eng,
        json={"ordered_on": "not-a-date"},
    )
    assert res.status_code == 422
    assert "invalid date format" in res.json()["detail"].lower()

    # 5. Valid partial receipt with rejection flags has_quality_issue
    res = client.put(
        f"/api/production/{DIE}/{CONFIG}/procurement/{p1}",
        headers=eng,
        json={
            "ordered_on": "2026-10-10",
            "receipts": [
                {"date": "2026-10-15", "qty": 10, "accepted": 8, "rejected": 2, "inspector": "Bob", "note": "2 scratched"}
            ],
        },
    )
    assert res.status_code == 200
    res_get = client.get(f"/api/production/{DIE}/{CONFIG}/procurement", headers=eng)
    row = next(r for r in res_get.json()["rows"] if r["part_id"] == p1)
    assert row["received_qty"] == 8.0
    assert row["rejected_qty"] == 2.0
    assert row["has_quality_issue"] is True


def test_readiness_summary_and_assembly_warning(setup_tracker_env):
    """Summary counts, percentages, and assembly risk warning when expected arrival pushes Assembly."""
    client = setup_tracker_env["client"]
    eng = setup_tracker_env["engineer"]
    p1 = setup_tracker_env["p1"]["id"]
    p2 = setup_tracker_env["p2"]["id"]
    p3 = setup_tracker_env["p3"]["id"]

    # Set Assembly phase planned_start to 2026-11-10
    sched = client.get(f"/api/production/{DIE}/{CONFIG}/schedule", headers=eng).json()
    phases = sched["phases"]
    for ph in phases:
        if ph["key"] == "assembly":
            ph["planned_start"] = "2026-11-10"
            ph["planned_end"] = "2026-11-20"
    client.put(f"/api/production/{DIE}/{CONFIG}/schedule", headers=eng, json={"phases": phases})

    # Part 1: fully received (10 / 10)
    client.put(
        f"/api/production/{DIE}/{CONFIG}/procurement/{p1}",
        headers=eng,
        json={"ordered_on": "2026-10-01", "receipts": [{"date": "2026-10-05", "qty": 10, "accepted": 10, "rejected": 0}]},
    )

    # Part 2: expected arrival 2026-11-15 (after Assembly 2026-11-10) -> at risk!
    client.put(
        f"/api/production/{DIE}/{CONFIG}/procurement/{p2}",
        headers=eng,
        json={"ordered_on": "2026-10-01", "confirmed_on": "2026-11-15", "need_by": "2026-11-10"},
    )

    # Part 3: not ordered
    res = client.get(f"/api/production/{DIE}/{CONFIG}/procurement?today=2026-10-20", headers=eng)
    summary = res.json()["summary"]
    assert summary["parts_total_count"] == 3
    assert summary["parts_received_count"] == 1
    assert summary["ready_for_assembly"] is False
    assert summary["at_risk_count"] >= 1
    assert len(summary["assembly_warnings"]) >= 1
    assert "Assembly at risk" in summary["assembly_warnings"][0]["message"]

    # Now receive parts 2 and 3 fully
    client.put(
        f"/api/production/{DIE}/{CONFIG}/procurement/{p2}",
        headers=eng,
        json={"ordered_on": "2026-10-01", "receipts": [{"date": "2026-10-08", "qty": 140, "accepted": 140, "rejected": 0}]},
    )
    client.put(
        f"/api/production/{DIE}/{CONFIG}/procurement/{p3}",
        headers=eng,
        json={"ordered_on": "2026-10-01", "receipts": [{"date": "2026-10-09", "qty": 10, "accepted": 10, "rejected": 0}]},
    )

    res_all = client.get(f"/api/production/{DIE}/{CONFIG}/procurement?today=2026-10-20", headers=eng)
    summary_all = res_all.json()["summary"]
    assert summary_all["parts_received_count"] == 3
    assert summary_all["received_pct"] == 100.0
    assert summary_all["ready_for_assembly"] is True
    assert summary_all["status_message"] == "Ready for assembly"


def test_schedule_cpm_critical_path_and_slack(setup_tracker_env):
    """Critical Path Method and slack days calculation on task DAG."""
    client = setup_tracker_env["client"]
    eng = setup_tracker_env["engineer"]

    # Custom phase graph:
    # A (5d) -> B (10d) -> D (5d)  [Main path: 20 days]
    # A (5d) -> C (2d) -> D (5d)   [Side path: 12 days, slack = 8 days]
    phases = [
        {"id": "task_a", "title": {"en": "Task A"}, "planned_start": "2026-10-01", "planned_end": "2026-10-05", "depends_on": []},
        {"id": "task_b", "title": {"en": "Task B"}, "planned_start": "2026-10-06", "planned_end": "2026-10-15", "depends_on": ["task_a"]},
        {"id": "task_c", "title": {"en": "Task C"}, "planned_start": "2026-10-06", "planned_end": "2026-10-07", "depends_on": ["task_a"]},
        {"id": "task_d", "title": {"en": "Task D"}, "planned_start": "2026-10-16", "planned_end": "2026-10-20", "depends_on": ["task_b", "task_c"]},
    ]

    res = client.put(f"/api/production/{DIE}/{CONFIG}/schedule", headers=eng, json={"phases": phases})
    assert res.status_code == 200
    sched = res.json()
    phase_dict = {p["id"]: p for p in sched["phases"]}

    assert phase_dict["task_a"]["is_critical"] is True
    assert phase_dict["task_a"]["slack_days"] == 0

    assert phase_dict["task_b"]["is_critical"] is True
    assert phase_dict["task_b"]["slack_days"] == 0

    assert phase_dict["task_d"]["is_critical"] is True
    assert phase_dict["task_d"]["slack_days"] == 0

    # Task C is shorter and has 8 days slack
    assert phase_dict["task_c"]["is_critical"] is False
    assert phase_dict["task_c"]["slack_days"] == 8


def test_schedule_cycle_detection_refuses_422(setup_tracker_env):
    """Cycle in depends_on DAG is detected and refused with 422."""
    client = setup_tracker_env["client"]
    eng = setup_tracker_env["engineer"]

    cyclic_phases = [
        {"id": "t1", "title": {"en": "Task 1"}, "depends_on": ["t3"]},
        {"id": "t2", "title": {"en": "Task 2"}, "depends_on": ["t1"]},
        {"id": "t3", "title": {"en": "Task 3"}, "depends_on": ["t2"]},
    ]

    res = client.put(f"/api/production/{DIE}/{CONFIG}/schedule", headers=eng, json={"phases": cyclic_phases})
    assert res.status_code == 422
    assert "cycle detected" in res.json()["detail"].lower()


def test_baseline_freeze_and_permissions(setup_tracker_env):
    """POST /schedule/baseline freezes planned dates; OEM is refused with 403."""
    client = setup_tracker_env["client"]
    eng = setup_tracker_env["engineer"]
    oem = setup_tracker_env["oem"]

    # OEM cannot freeze baseline -> 403
    oem_res = client.post(f"/api/production/{DIE}/{CONFIG}/schedule/baseline", headers=oem)
    assert oem_res.status_code == 403

    # Engineer freezes baseline -> 200
    eng_res = client.post(f"/api/production/{DIE}/{CONFIG}/schedule/baseline", headers=eng)
    assert eng_res.status_code == 200
    data = eng_res.json()
    assert "baseline" in data
    assert "frozen_at" in data["baseline"]
    assert len(data["baseline"]["phases"]) > 0

    # Read back via GET /schedule
    sched = client.get(f"/api/production/{DIE}/{CONFIG}/schedule", headers=eng).json()
    assert sched["baseline"]["frozen_by"] == ENGINEER


def test_automatic_milestones_and_ordering_derivation(setup_tracker_env):
    """Auto milestones trigger and ordering phase derives dates."""
    client = setup_tracker_env["client"]
    eng = setup_tracker_env["engineer"]
    p1 = setup_tracker_env["p1"]["id"]
    p2 = setup_tracker_env["p2"]["id"]
    p3 = setup_tracker_env["p3"]["id"]

    # Place POs for all parts
    client.put(f"/api/production/{DIE}/{CONFIG}/procurement/{p1}", headers=eng, json={"ordered_on": "2026-10-02"})
    client.put(f"/api/production/{DIE}/{CONFIG}/procurement/{p2}", headers=eng, json={"ordered_on": "2026-10-04"})
    client.put(f"/api/production/{DIE}/{CONFIG}/procurement/{p3}", headers=eng, json={"ordered_on": "2026-10-05"})

    sched = client.get(f"/api/production/{DIE}/{CONFIG}/schedule", headers=eng).json()
    ms_dict = {m["id"]: m for m in sched["milestones"]}
    # 'All POs placed' milestone is automatically done
    assert ms_dict["ms_all_pos_placed"]["done"] is True
    # 'All parts received' is not done yet
    assert ms_dict["ms_all_parts_received"]["done"] is False

    # Ordering phase actual_start is derived from earliest ordered_on: 2026-10-02
    ordering_phase = next(p for p in sched["phases"] if p["key"] == "ordering")
    assert ordering_phase["actual_start"] == "2026-10-02"
    assert ordering_phase["status"] == "in_progress"

    # Now receive all parts
    client.put(
        f"/api/production/{DIE}/{CONFIG}/procurement/{p1}",
        headers=eng,
        json={"ordered_on": "2026-10-02", "receipts": [{"date": "2026-10-10", "qty": 10, "accepted": 10, "rejected": 0}]},
    )
    client.put(
        f"/api/production/{DIE}/{CONFIG}/procurement/{p2}",
        headers=eng,
        json={"ordered_on": "2026-10-04", "receipts": [{"date": "2026-10-12", "qty": 140, "accepted": 140, "rejected": 0}]},
    )
    client.put(
        f"/api/production/{DIE}/{CONFIG}/procurement/{p3}",
        headers=eng,
        json={"ordered_on": "2026-10-05", "receipts": [{"date": "2026-10-15", "qty": 10, "accepted": 10, "rejected": 0}]},
    )

    sched_after = client.get(f"/api/production/{DIE}/{CONFIG}/schedule", headers=eng).json()
    ms_after = {m["id"]: m for m in sched_after["milestones"]}
    assert ms_after["ms_all_parts_received"]["done"] is True

    # Ordering phase actual_end is derived from latest receipt: 2026-10-15
    ordering_after = next(p for p in sched_after["phases"] if p["key"] == "ordering")
    assert ordering_after["actual_end"] == "2026-10-15"
    assert ordering_after["status"] == "done"


def test_oem_permissions_and_limits(setup_tracker_env):
    """OEM member permissions:
    - CAN edit procurement lines & add receipts
    - CANNOT edit BOM or parts
    - CAN update Ordering, Assembly, Testing actuals & status
    - CANNOT update planned dates, depends_on, or non-factory phases (Requirements, Design)
    - CANNOT update milestones or baseline
    """
    client = setup_tracker_env["client"]
    oem = setup_tracker_env["oem"]
    p1 = setup_tracker_env["p1"]["id"]

    # 1. OEM edits procurement line -> 200 OK
    res_proc = client.put(
        f"/api/production/{DIE}/{CONFIG}/procurement/{p1}",
        headers=oem,
        json={
            "supplier": "Guangdong Magnet Co",
            "po_number": "PO-2026-001",
            "confirmed_on": "2026-10-25",
            "receipts": [
                {"date": "2026-10-24", "qty": 5, "accepted": 5, "rejected": 0, "inspector": "Factory QC"}
            ],
        },
    )
    assert res_proc.status_code == 200

    # 2. OEM cannot edit BOM -> 403 Forbidden
    res_bom = client.put(
        f"/api/production/{DIE}/{CONFIG}/bom",
        headers=oem,
        json={"rows": [{"part_id": p1, "qty_per_motor": 99}]},
    )
    assert res_bom.status_code == 403

    # 3. OEM cannot create/edit parts -> 403 Forbidden
    res_part = client.post(
        f"/api/production/{DIE}/{CONFIG}/parts",
        headers=oem,
        json={"name": {"en": "Hacked", "zh": "篡改"}, "kind": "made"},
    )
    assert res_part.status_code == 403

    # 4. OEM updates Assembly actuals & status -> 200 OK
    res_sched = client.put(
        f"/api/production/{DIE}/{CONFIG}/schedule",
        headers=oem,
        json={
            "phases": [
                {
                    "id": "assembly",
                    "actual_start": "2026-11-12",
                    "actual_end": "2026-11-18",
                    "status": "in_progress",
                    "percent_complete": 50,
                }
            ]
        },
    )
    assert res_sched.status_code == 200
    sched = res_sched.json()
    assembly_ph = next(p for p in sched["phases"] if p["id"] == "assembly")
    assert assembly_ph["actual_start"] == "2026-11-12"
    assert assembly_ph["status"] == "in_progress"

    # 5. OEM attempts to modify Requirements phase -> 403 Forbidden
    res_req = client.put(
        f"/api/production/{DIE}/{CONFIG}/schedule",
        headers=oem,
        json={
            "phases": [
                {
                    "id": "requirements",
                    "status": "done",
                }
            ]
        },
    )
    assert res_req.status_code == 403

    # 6. OEM attempts to modify planned_start on Assembly -> 403 Forbidden
    res_planned = client.put(
        f"/api/production/{DIE}/{CONFIG}/schedule",
        headers=oem,
        json={
            "phases": [
                {
                    "id": "assembly",
                    "planned_start": "2026-12-01",
                }
            ]
        },
    )
    assert res_planned.status_code == 403

    # 7. OEM attempts to modify milestones -> 403 Forbidden
    res_ms = client.put(
        f"/api/production/{DIE}/{CONFIG}/schedule",
        headers=oem,
        json={
            "milestones": [
                {"id": "ms_all_parts_received", "done": True}
            ]
        },
    )
    assert res_ms.status_code == 403


def test_audit_trail_for_procurement_and_schedule(setup_tracker_env):
    """Audit log records changes with old and new values."""
    client = setup_tracker_env["client"]
    eng = setup_tracker_env["engineer"]
    p1 = setup_tracker_env["p1"]["id"]

    client.put(
        f"/api/production/{DIE}/{CONFIG}/procurement/{p1}",
        headers=eng,
        json={"supplier": "Initial Supplier", "po_number": "PO-100"},
    )
    client.put(
        f"/api/production/{DIE}/{CONFIG}/procurement/{p1}",
        headers=eng,
        json={"supplier": "Updated Supplier", "po_number": "PO-200"},
    )

    # Read audit.jsonl directly
    p = store.audit_path(DIE, CONFIG)
    assert p.is_file()
    lines = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    proc_audits = [a for a in lines if a.get("entity") == "procurement" and a.get("entity_id") == p1]
    assert len(proc_audits) >= 2
    latest = proc_audits[-1]
    assert latest["snapshot"]["new"]["supplier"] == "Updated Supplier"
    assert latest["snapshot"]["old"]["supplier"] == "Initial Supplier"


def test_cross_config_die_timeline_access(setup_tracker_env):
    """GET /api/production/{die}/timeline returns cross-config overview and respects access."""
    client = setup_tracker_env["client"]
    admin = setup_tracker_env["admin"]
    eng = setup_tracker_env["engineer"]
    oem = setup_tracker_env["oem"]
    non_member = setup_tracker_env["non_member"]

    # Create second config: L20 under same DIE
    CONFIG_2 = "L20"
    client.post(
        f"/api/admin/production/{DIE}/{CONFIG_2}",
        headers=admin,
        json={
            "title": {"en": "L20 Stator", "zh": "L20 定子"},
            "order_quantity": 20,
            "members": [
                {"email": ENGINEER, "role": "engineer"},
                # OEM is NOT a member of L20
            ],
        },
    )

    # Admin sees both L12 and L20
    res_admin = client.get(f"/api/production/{DIE}/timeline", headers=admin)
    assert res_admin.status_code == 200
    prods_admin = res_admin.json()["productions"]
    config_names = [p["config"] for p in prods_admin]
    assert CONFIG in config_names
    assert CONFIG_2 in config_names

    # Engineer is a member of both -> sees both
    res_eng = client.get(f"/api/production/{DIE}/timeline", headers=eng)
    assert res_eng.status_code == 200
    prods_eng = res_eng.json()["productions"]
    assert len(prods_eng) == 2

    # OEM is a member of only L12 -> sees ONLY L12
    res_oem = client.get(f"/api/production/{DIE}/timeline", headers=oem)
    assert res_oem.status_code == 200
    prods_oem = res_oem.json()["productions"]
    assert len(prods_oem) == 1
    assert prods_oem[0]["config"] == CONFIG

    # Non-member sees none
    res_non = client.get(f"/api/production/{DIE}/timeline", headers=non_member)
    assert res_non.status_code == 200
    assert len(res_non.json()["productions"]) == 0
