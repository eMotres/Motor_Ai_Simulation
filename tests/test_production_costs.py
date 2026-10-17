"""Tests for production BOM costs, margin calculation, OEM access control,
missing-price warnings, and canonical English part name mapping.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from motor_ai_sim.api import app
from motor_ai_sim.production import import_package as IMP, models, store
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
def setup_production_with_bom(prod_env):
    client = prod_env["client"]
    admin = prod_env["admin"]
    engineer = prod_env["engineer"]

    # 1. Admin creates a production
    prod_payload = {
        "title": {
            "en": "AeroStator CIANO14 40 L12 Production",
            "zh": "AeroStator CIANO14 40 L12 生产规程",
            "src_lang": "en",
            "status": {"en": "confirmed", "zh": "confirmed"},
        },
        "order_quantity": 50,
        "share_costs_with_oem": False,
        "members": [
            {"email": ENGINEER, "role": "engineer"},
            {"email": OEM_NDA, "role": "oem"},
            {"email": OEM_NO_NDA, "role": "oem"},
        ],
    }
    client.post(f"/api/admin/production/{DIE}/{CONFIG}", headers=admin, json=prod_payload)

    # 2. Engineer creates parts with canonical English names
    part1_res = client.post(
        f"/api/production/{DIE}/{CONFIG}/parts",
        headers=engineer,
        json={
            "name": {"en": "Stator core", "zh": "定子铁芯", "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}},
            "kind": "made",
            "qty_per_motor": 1,
            "material": "Silicon Steel 0.2mm",
            "spec": "OD140 x ID90 x L40",
            "description": {"en": "Laminated stator stack", "zh": "叠片定子铁芯", "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}},
        },
    )
    assert part1_res.status_code == 200
    p1 = part1_res.json()

    part2_res = client.post(
        f"/api/production/{DIE}/{CONFIG}/parts",
        headers=engineer,
        json={
            "name": {"en": "Magnet", "zh": "磁钢", "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}},
            "kind": "purchased",
            "qty_per_motor": 28,
            "material": "N48SH",
            "spec": "20 x 4 x 2",
            "description": {"en": "Permanent magnets", "zh": "永磁体", "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}},
        },
    )
    assert part2_res.status_code == 200
    p2 = part2_res.json()

    part3_res = client.post(
        f"/api/production/{DIE}/{CONFIG}/parts",
        headers=engineer,
        json={
            "name": {"en": "Shaft", "zh": "轴", "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}},
            "kind": "made",
            "qty_per_motor": 1,
            "material": "40Cr",
            "spec": "Ø8h6 x 115",
            "description": {"en": "Main rotor shaft", "zh": "转子主轴", "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}},
        },
    )
    assert part3_res.status_code == 200
    p3 = part3_res.json()

    # 3. Engineer sets BOM rows
    bom_rows = [
        {"part_id": p1["id"], "qty_per_motor": 1, "order_total": 50},
        {"part_id": p2["id"], "qty_per_motor": 28, "order_total": 1400},
        {"part_id": p3["id"], "qty_per_motor": 1, "order_total": 50},
    ]
    client.put(f"/api/production/{DIE}/{CONFIG}/bom", headers=engineer, json={"rows": bom_rows})

    return {
        "p1": p1,
        "p2": p2,
        "p3": p3,
    }


def test_editor_sets_prices_and_summary_math(prod_env, setup_production_with_bom):
    client = prod_env["client"]
    engineer = prod_env["engineer"]
    p1 = setup_production_with_bom["p1"]
    p2 = setup_production_with_bom["p2"]
    p3 = setup_production_with_bom["p3"]

    # 1. Engineer sets prices: p1 = 120 CNY, p2 = 80 CNY, p3 = 50 CNY
    # Extras: Packaging = 15 CNY, Labour = 35 CNY (extra subtotal = 50 CNY)
    # margin_pct = 20%
    # parts subtotal = 120 + 80 + 50 = 250 CNY
    # extra subtotal = 15 + 35 = 50 CNY
    # cost per motor = 250 + 50 = 300 CNY
    # price per motor = cost / (1 - 0.20) = 300 / 0.8 = 375 CNY
    # order totals (order_quantity = 50):
    # order_parts_total = 250 * 50 = 12500 CNY
    # order_extra_total = 50 * 50 = 2500 CNY
    # order_cost_total = 300 * 50 = 15000 CNY
    # order_price_total = 375 * 50 = 18750 CNY
    costs_payload = {
        "rows": [
            {"part_id": p1["id"], "unit_price": 120.0, "currency": "CNY", "note": "stator stack + insulation"},
            {"part_id": p2["id"], "unit_price": 80.0, "currency": "CNY", "note": "28 magnets"},
            {"part_id": p3["id"], "unit_price": 50.0, "currency": "CNY", "note": "precision ground"},
        ],
        "extras": [
            {"id": "ec_pack", "name": {"en": "Packaging", "zh": "包装", "src_lang": "en"}, "amount": 15.0},
            {"id": "ec_labour", "name": {"en": "Assembly labour", "zh": "装配人工", "src_lang": "en"}, "amount": 35.0},
        ],
        "margin_pct": 20.0,
    }

    put_res = client.put(f"/api/production/{DIE}/{CONFIG}/costs", headers=engineer, json=costs_payload)
    assert put_res.status_code == 200
    data = put_res.json()

    summary = data["summary"]
    assert summary["parts_subtotal"] == 250.0
    assert summary["extra_subtotal"] == 50.0
    assert summary["cost_per_motor"] == 300.0
    assert summary["margin_pct"] == 20.0
    assert summary["price_per_motor"] == 375.0
    assert summary["order_quantity"] == 50
    assert summary["order_parts_total"] == 12500.0
    assert summary["order_extra_total"] == 2500.0
    assert summary["order_cost_total"] == 15000.0
    assert summary["order_price_total"] == 18750.0
    assert summary["missing_prices_count"] == 0
    assert summary["warnings"] == []

    # GET /costs returns the same summary
    get_res = client.get(f"/api/production/{DIE}/{CONFIG}/costs", headers=engineer)
    assert get_res.status_code == 200
    assert get_res.json()["summary"] == summary

    # GET /bom also returns enriched rows and costs
    bom_res = client.get(f"/api/production/{DIE}/{CONFIG}/bom", headers=engineer)
    assert bom_res.status_code == 200
    bom_data = bom_res.json()
    assert "summary" in bom_data
    assert bom_data["summary"]["cost_per_motor"] == 300.0
    assert len(bom_data["rows"]) == 3
    r1 = [r for r in bom_data["rows"] if r["part_id"] == p1["id"]][0]
    assert r1["unit_price"] == 120.0
    assert r1["line_total"] == 6000.0
    assert r1["currency"] == "CNY"
    assert r1["note"] == "stator stack + insulation"


def test_missing_price_warning_not_silent_zero(prod_env, setup_production_with_bom):
    client = prod_env["client"]
    engineer = prod_env["engineer"]
    p1 = setup_production_with_bom["p1"]
    p2 = setup_production_with_bom["p2"]
    # p3 left unpriced!

    costs_payload = {
        "rows": [
            {"part_id": p1["id"], "unit_price": 100.0, "currency": "CNY", "note": None},
            {"part_id": p2["id"], "unit_price": 50.0, "currency": "CNY", "note": None},
        ],
        "extras": [],
        "margin_pct": 10.0,
    }
    put_res = client.put(f"/api/production/{DIE}/{CONFIG}/costs", headers=engineer, json=costs_payload)
    assert put_res.status_code == 200
    summary = put_res.json()["summary"]

    # 1 part missing out of 3 in BOM
    assert summary["missing_prices_count"] == 1
    assert len(summary["warnings"]) == 1
    assert "missing prices" in summary["warnings"][0]
    assert summary["parts_subtotal"] == 150.0

    # In GET /bom, unpriced row has unit_price None and line_total None
    bom_res = client.get(f"/api/production/{DIE}/{CONFIG}/bom", headers=engineer)
    assert bom_res.status_code == 200
    p3_id = setup_production_with_bom["p3"]["id"]
    r3 = [r for r in bom_res.json()["rows"] if r["part_id"] == p3_id][0]
    assert r3["unit_price"] is None
    assert r3["line_total"] is None


def test_oem_costs_visibility_and_share_switch(prod_env, setup_production_with_bom):
    client = prod_env["client"]
    admin = prod_env["admin"]
    engineer = prod_env["engineer"]
    oem_nda = prod_env["oem_nda"]
    p1 = setup_production_with_bom["p1"]

    # 1. Engineer sets costs
    client.put(
        f"/api/production/{DIE}/{CONFIG}/costs",
        headers=engineer,
        json={"rows": [{"part_id": p1["id"], "unit_price": 100.0}], "extras": [], "margin_pct": 0.0},
    )

    # 2. By default, share_costs_with_oem is False
    # OEM GET /costs -> 403 Forbidden
    oem_costs_res = client.get(f"/api/production/{DIE}/{CONFIG}/costs", headers=oem_nda)
    assert oem_costs_res.status_code == 403

    # OEM GET /bom -> stripped server-side: no unit_price, no line_total, no summary, no costs
    oem_bom_res = client.get(f"/api/production/{DIE}/{CONFIG}/bom", headers=oem_nda)
    assert oem_bom_res.status_code == 200
    oem_bom = oem_bom_res.json()
    assert "summary" not in oem_bom
    assert "costs" not in oem_bom
    for row in oem_bom["rows"]:
        assert "unit_price" not in row
        assert "line_total" not in row
        assert "currency" not in row
        assert "note" not in row
        assert set(row.keys()) == {"part_id", "qty_per_motor", "order_total"}

    # OEM attempts PUT /costs -> 403 Forbidden
    oem_put_res = client.put(f"/api/production/{DIE}/{CONFIG}/costs", headers=oem_nda, json={"rows": []})
    assert oem_put_res.status_code == 403

    # 3. Admin switches share_costs_with_oem = True
    switch_res = client.put(
        f"/api/admin/production/{DIE}/{CONFIG}/share_costs",
        headers=admin,
        json={"share_costs_with_oem": True},
    )
    assert switch_res.status_code == 200
    assert switch_res.json()["share_costs_with_oem"] is True

    # 4. Now OEM CAN see costs in GET /costs
    oem_costs_res2 = client.get(f"/api/production/{DIE}/{CONFIG}/costs", headers=oem_nda)
    assert oem_costs_res2.status_code == 200
    assert oem_costs_res2.json()["summary"]["parts_subtotal"] == 100.0

    # OEM CAN see cost fields in GET /bom
    oem_bom_res2 = client.get(f"/api/production/{DIE}/{CONFIG}/bom", headers=oem_nda)
    assert oem_bom_res2.status_code == 200
    assert "summary" in oem_bom_res2.json()
    r1 = [r for r in oem_bom_res2.json()["rows"] if r["part_id"] == p1["id"]][0]
    assert r1["unit_price"] == 100.0

    # BUT OEM STILL CANNOT edit costs (PUT /costs -> 403)
    oem_put_res2 = client.put(f"/api/production/{DIE}/{CONFIG}/costs", headers=oem_nda, json={"rows": []})
    assert oem_put_res2.status_code == 403


def test_audit_logs_every_cost_change_and_switch(prod_env, setup_production_with_bom):
    client = prod_env["client"]
    admin = prod_env["admin"]
    engineer = prod_env["engineer"]

    # Perform updates
    client.put(
        f"/api/production/{DIE}/{CONFIG}/costs",
        headers=engineer,
        json={"rows": [], "extras": [], "margin_pct": 15.0},
    )
    client.put(
        f"/api/admin/production/{DIE}/{CONFIG}/share_costs",
        headers=admin,
        json={"share_costs_with_oem": True},
    )

    audit_file = store.audit_path(DIE, CONFIG)
    assert audit_file.is_file()
    lines = [json.loads(l) for l in audit_file.read_text(encoding="utf-8").strip().splitlines() if l.strip()]

    cost_audits = [a for a in lines if a.get("entity") == "costs"]
    assert len(cost_audits) >= 1
    assert cost_audits[-1]["action"] == "update"
    assert cost_audits[-1]["who"] == ENGINEER

    switch_audits = [a for a in lines if a.get("entity") == "share_costs_with_oem"]
    assert len(switch_audits) >= 1
    assert switch_audits[-1]["who"] == ADMIN
    assert switch_audits[-1]["snapshot"]["share_costs_with_oem"] is True


def test_canonical_part_name_mapping_requirement_c_and_e():
    """Requirement C & E: Mapping table in import_package.py matches all specified parts
    to canonical English, applying generic size and config suffix rules, and flags unknown parts."""
    # 1. Base mapping without die / size
    mapping_checks = [
        ("定子铁芯", "Stator core"),
        ("高强度定子铁芯", "Stator core"),
        ("转子铁芯", "Rotor core"),
        ("磁钢", "Magnet"),
        ("钕铁硼磁钢 (Magnet)", "Magnet"),
        ("线圈", "Coil"),
        ("轴", "Shaft"),
        ("主轴", "Shaft"),
        ("前支架", "Front holder"),
        ("后支架", "Back holder"),
        ("线圈固定环", "Coil-fixing ring"),
        ("轴承", "Bearing 618/8-2Z C3"),
        ("内六角螺钉", "Socket head screw"),
        ("相板 rev C", "Phase board rev C"),
        ("相板", "Phase board rev C"),
        ("引线", "Lead wire"),
        ("槽绝缘", "Slot insulation"),
        ("浸漆 / 环氧胶", "Varnish / epoxy"),
        ("浸漆", "Varnish / epoxy"),
        ("环氧胶", "Varnish / epoxy"),
        ("胶粘剂", "Adhesive"),
    ]

    for item in mapping_checks:
        zh_in = item[0]
        expected_en = item[1]
        en, zh, unmapped = IMP.resolve_part_name(zh_in)
        assert en == expected_en, f"Failed mapping for {zh_in}: expected {expected_en}, got {en}"
        assert unmapped is False
        assert zh == zh_in

    # 2. Importer naming rules with DIE="CIANO14 40 new" (size=40) and CONFIG="L12" (cfg_digits=12):
    # - Manufactured parts get motor size suffix ' 40'
    # - Config differing parts get ' 40_12'
    # - Purchased parts get no suffix
    # - Phase board gets 'Phase board 40 rev C'
    die_40 = "CIANO14 40 new"
    cfg_12 = "L12"

    rule_checks_12 = [
        ("定子铁芯", "Stator core 40_12"),
        ("转子铁芯", "Rotor core 40_12"),
        ("磁钢", "Magnet 40_12"),
        ("线圈", "Coil 40_12"),
        ("轴", "Shaft 40_12"),
        ("前支架", "Front holder 40"),
        ("后支架", "Back holder 40"),
        ("线圈固定环", "Coil-fixing ring 40"),
        ("相板 rev C", "Phase board 40 rev C"),
        ("相板", "Phase board 40 rev C"),
        ("轴承", "Bearing 618/8-2Z C3"),
        ("内六角螺钉", "Socket head screw"),
        ("引线", "Lead wire"),
        ("槽绝缘", "Slot insulation"),
        ("浸漆 / 环氧胶", "Varnish / epoxy"),
        ("胶粘剂", "Adhesive"),
    ]

    for zh_in, expected_en in rule_checks_12:
        en, zh, unmapped = IMP.resolve_part_name(zh_in, die=die_40, config=cfg_12)
        assert en == expected_en, f"Failed rule for {zh_in} under {die_40}/{cfg_12}: expected {expected_en}, got {en}"
        assert unmapped is False

    # Config L20 gives 40_20 for differing parts
    en_stator_20, _, _ = IMP.resolve_part_name("定子铁芯", die=die_40, config="L20")
    assert en_stator_20 == "Stator core 40_20"
    en_shaft_20, _, _ = IMP.resolve_part_name("轴", die=die_40, config="L20")
    assert en_shaft_20 == "Shaft 40_20"
    en_front_20, _, _ = IMP.resolve_part_name("前支架", die=die_40, config="L20")
    assert en_front_20 == "Front holder 40"

    # Unknown Chinese part name -> keeps zh and flags as unmapped
    en_u, zh_u, unmapped_u = IMP.resolve_part_name("定制碳纤维保护套", die=die_40, config=cfg_12)
    assert en_u == ""
    assert zh_u == "定制碳纤维保护套"
    assert unmapped_u is True


def test_material_and_spec_i18n_requirement_e(prod_env):
    """Requirement E: Data carries part.material_i18n and part.spec_i18n = {zh, en}.
    create_part and update_part automatically translate them."""
    client = prod_env["client"]
    engineer = prod_env["engineer"]

    # 1. normalize_part helper
    p_norm = models.normalize_part({
        "material": "AL6061-T6",
        "spec": "O140 x 15",
    })
    assert p_norm["material"] == "AL6061-T6"
    assert p_norm["spec"] == "O140 x 15"
    assert p_norm["material_i18n"] == {"zh": "AL6061-T6", "en": ""}
    assert p_norm["spec_i18n"] == {"zh": "O140 x 15", "en": ""}

    # 2. ensure_i18n_translated copies non-CJK strings to en
    trans_mat = models.ensure_i18n_translated(p_norm["material_i18n"])
    assert trans_mat["en"] == "AL6061-T6"

    # 3. create_part via API
    admin = prod_env["admin"]
    client.post(
        f"/api/admin/production/{DIE}/{CONFIG}",
        headers=admin,
        json={
            "title": {"en": "Prod", "zh": "生产", "src_lang": "en"},
            "order_quantity": 50,
            "members": [{"email": ENGINEER, "role": "engineer"}],
        },
    )
    res = client.post(
        f"/api/production/{DIE}/{CONFIG}/parts",
        headers=engineer,
        json={
            "name": {"en": "Front holder 40", "zh": "前支架", "src_lang": "en"},
            "kind": "made",
            "qty_per_motor": 1,
            "material": "6061-T6 阳极氧化",
            "spec": "O140 x 15",
        },
    )
    assert res.status_code == 200
    part = res.json()
    part_id = part["id"]
    assert "material_i18n" in part
    assert "spec_i18n" in part
    assert part["material_i18n"]["zh"] == "6061-T6 阳极氧化"
    assert part["spec_i18n"]["zh"] == "O140 x 15"
    # Non-CJK spec was translated/copied to en
    assert part["spec_i18n"]["en"] == "O140 x 15"

    # 4. update_part via API
    res_patch = client.patch(
        f"/api/production/{DIE}/{CONFIG}/parts/{part_id}",
        headers=engineer,
        json={
            "material_i18n": {"zh": "不锈钢 304", "en": "Stainless steel 304"},
            "spec": "M3 x 8",
        },
    )
    assert res_patch.status_code == 200
    updated = res_patch.json()
    assert updated["material"] == "不锈钢 304"
    assert updated["material_i18n"]["en"] == "Stainless steel 304"
    assert updated["spec"] == "M3 x 8"
    assert updated["spec_i18n"]["en"] == "M3 x 8"



def test_process_section_tables_i18n_requirement_d(prod_env, tmp_path):
    """Requirement D: Process section tables carry tables_i18n = {zh: tables, en: tables}."""
    pkg_data = {
        "title": "测试规程",
        "sections": [
            {"h": "1. 订单 (50台)"},
            {
                "table": [
                    ["序号", "零件", "类型", "每台", "订单合计（50 台）", "材料", "规格", "图纸"],
                    ["1", "定子铁芯", "自制", "1", "50", "Silicon Steel", "140", ""],
                ]
            },
            {"h": "2. 装配公差配合 (Fits)"},
            {"p": "配合公差要求"},
            {
                "table": [
                    ["配合部位", "公差代号", "尺寸要求 (mm)"],
                    ["轴承位", "k6", "+0.009 / +0.002"],
                    ["铁芯外圆", "h6", "0 / -0.016"],
                ]
            },
        ],
    }
    pkg_file = tmp_path / "proc_test_pkg.json"
    pkg_file.write_text(json.dumps(pkg_data, ensure_ascii=False), encoding="utf-8")

    res = IMP.import_package(
        package_path=pkg_file,
        die="ProcDie",
        config="ProcConfig",
        order_quantity=50,
    )
    assert res["process_sections_count"] >= 1

    proc_sections = store.load_file("ProcDie", "ProcConfig", "process.json")["sections"]
    fits_sec = [s for s in proc_sections if s["kind"] == "fits"][0]

    assert "tables_i18n" in fits_sec
    assert "zh" in fits_sec["tables_i18n"]
    assert "en" in fits_sec["tables_i18n"]
    zh_tbl = fits_sec["tables_i18n"]["zh"][0]
    en_tbl = fits_sec["tables_i18n"]["en"][0]

    # Non-CJK cells copied as is
    assert en_tbl[1][1] == "k6"
    assert en_tbl[1][2] == "+0.009 / +0.002"
    assert en_tbl[2][1] == "h6"
    assert en_tbl[2][2] == "0 / -0.016"
