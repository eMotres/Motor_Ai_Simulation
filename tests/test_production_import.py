"""Tests for factory package import CLI and module in AeroStator Production.
Validates BOM table parsing, part classification, drawing matching,
process section categorization, file asset copying, and revision 1.1 publication.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from motor_ai_sim.production import import_package as IMP, store
from tests.test_production_access import (
    CONFIG,
    DIE,
    prod_env,
)


@pytest.fixture()
def synthetic_factory_package(tmp_path):
    assets_dir = tmp_path / "factory_assets"
    assets_dir.mkdir(parents=True)

    # Create dummy asset files
    shaft_file = assets_dir / "shaft_drawing.pdf"
    shaft_file.write_bytes(b"%PDF-1.4 dummy shaft drawing")
    magnet_file = assets_dir / "magnet.png"
    magnet_file.write_bytes(b"\x89PNG\r\n\x1a\n dummy magnet png")
    assembly_img = assets_dir / "assembly_guide.png"
    assembly_img.write_bytes(b"\x89PNG\r\n\x1a\n dummy assembly guide")

    package_data = {
        "title": "CIANO14 40 电机生产制造工艺规程",
        "subtitle": "小批量试制工艺规程与图纸",
        "file_name": "ciano14_40_l12_package.json",
        "sections": [
            {"h": "1. 订单与物料清单 (100台)"},
            {"h3": "L12 规格物料表"},
            {
                "table": [
                    ["序号", "零件", "类型", "每台", "订单合计（100 台）", "材料 / 表面", "规格", "图纸 / 要求"],
                    ["1", "主轴 (Shaft)", "自制件", "1", "100", "40Cr / 调质镀硬铬", "Ø8h6 x 115", "图纸 shaft_drawing.pdf"],
                    ["2", "钕铁硼磁钢 (Magnet)", "采购件", "28", "2800", "N48SH / 镀镍铜镍", "20 x 4 x 2", "图纸 magnet.png"],
                    ["3", "前支架 (Front holder)", "自制件", "1", "100", "AL6061-T6 / 阳极氧化黑", "Ø140 x 15", "需CNC精加工"],
                ]
            },
            {"h": "2. 轴承配合与公差要求 (Fits)"},
            {"p": "轴承位配合公差严控在 k6 (+0.009/+0.002mm)，压装力小于 500N。"},
            {"h": "3. 绕线与端接工艺 (Winding)"},
            {"bullets": ["采用 200 级聚酯亚胺漆包线", "槽绝缘纸探出端面 2.0±0.3mm"]},
            {"h": "4. 出厂综合检验 (Tests)"},
            {"p": "耐压测试：1500V 50Hz 历时 60 秒，无击穿闪络。"},
            {
                "drawings": [
                    ["主轴零件图纸", 1, "shaft_drawing.pdf"],
                    ["磁钢尺寸及公差图", 28, "magnet.png"],
                ]
            },
            {"img": "assembly_guide.png", "w": 12.5},
        ],
    }

    pkg_path = tmp_path / "synthetic_package.json"
    pkg_path.write_text(json.dumps(package_data, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "pkg_path": pkg_path,
        "assets_dir": assets_dir,
    }


def test_import_package_direct(prod_env, synthetic_factory_package):
    pkg_path = synthetic_factory_package["pkg_path"]
    assets_dir = synthetic_factory_package["assets_dir"]

    # Run direct import
    res = IMP.import_package(
        package_path=pkg_path,
        die=DIE,
        config=CONFIG,
        assets_dir=assets_dir,
    )

    assert res["parts_count"] == 3
    assert res["bom_rows_count"] == 3
    assert res["process_sections_count"] >= 3
    assert res["revision"] == "1.1"

    # Verify production.json
    prod = store.load_file(DIE, CONFIG, "production.json")
    assert prod["die"] == DIE
    assert prod["config"] == CONFIG
    assert prod["order_quantity"] == 100
    assert prod["current_revision"] == "1.1"

    # Verify parts.json
    parts = store.load_file(DIE, CONFIG, "parts.json")["parts"]
    assert len(parts) == 3

    # Check shaft
    shaft = [p for p in parts if "主轴" in p["name"]["zh"]][0]
    assert shaft["kind"] == "made"
    assert shaft["material"] == "40Cr / 调质镀硬铬"
    assert shaft["spec"] == "Ø8h6 x 115"
    assert len(shaft["drawing_files"]) == 1
    assert shaft["drawing_files"][0]["name"] == "shaft_drawing.pdf"
    assert (store.file_dir(DIE, CONFIG) / shaft["drawing_files"][0]["file_id"]).is_file()

    # Check magnet
    magnet = [p for p in parts if "磁钢" in p["name"]["zh"]][0]
    assert magnet["kind"] == "purchased"
    assert magnet["qty_per_motor"] == 28
    assert len(magnet["drawing_files"]) == 1
    assert magnet["drawing_files"][0]["name"] == "magnet.png"

    # Check front holder
    front = [p for p in parts if "前支架" in p["name"]["zh"]][0]
    assert front["kind"] == "made"
    assert front["qty_per_motor"] == 1

    # Verify bom.json
    bom_rows = store.load_file(DIE, CONFIG, "bom.json")["rows"]
    assert len(bom_rows) == 3
    shaft_row = [r for r in bom_rows if r["part_id"] == shaft["id"]][0]
    assert shaft_row["qty_per_motor"] == 1
    assert shaft_row["order_total"] == 100
    magnet_row = [r for r in bom_rows if r["part_id"] == magnet["id"]][0]
    assert magnet_row["qty_per_motor"] == 28
    assert magnet_row["order_total"] == 2800

    # Verify process sections kinds
    process_sections = store.load_file(DIE, CONFIG, "process.json")["sections"]
    kinds = {s["kind"] for s in process_sections}
    assert "fits" in kinds
    assert "winding" in kinds
    assert "tests" in kinds

    # Verify revisions.json published 1.1
    revisions = store.load_file(DIE, CONFIG, "revisions.json")["revisions"]
    assert len(revisions) == 1
    assert revisions[0]["number"] == "1.1"

    # Verify audit log
    audit_file = store.audit_path(DIE, CONFIG)
    assert audit_file.is_file()
    lines = [json.loads(l) for l in audit_file.read_text(encoding="utf-8").strip().splitlines() if l.strip()]
    assert any(a["action"] == "import_package" for a in lines)


def test_import_package_cli_invocation(prod_env, synthetic_factory_package, monkeypatch, capsys):
    pkg_path = synthetic_factory_package["pkg_path"]
    assets_dir = synthetic_factory_package["assets_dir"]

    cli_die = "Test Die CLI"
    cli_cfg = "C99"

    test_args = [
        "import_package.py",
        str(pkg_path),
        "--die", cli_die,
        "--config", cli_cfg,
        "--assets", str(assets_dir),
        "--order-quantity", "50",
    ]
    monkeypatch.setattr(sys, "argv", test_args)

    IMP.main()

    captured = capsys.readouterr()
    assert f"Successfully imported production for {cli_die} / {cli_cfg}" in captured.out
    assert "Current revision: 1.1" in captured.out

    # Verify order_quantity override was respected
    prod = store.load_file(cli_die, cli_cfg, "production.json")
    assert prod["order_quantity"] == 50
