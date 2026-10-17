"""Tests for production package importer: drawings per configuration + PDFs,
and CAD shaft material label.

Validates:
1. Config-specific parts (shaft, magnet, stator, rotor, coil, screws) attach strictly to
   their own configuration's drawing.
2. Common drawings (e.g. marked 通用 / common / universal) attach to both configurations.
3. Non-drawing report/question images (fem_*, q*_*) do not attach to parts.
4. Drawings with both PDF and PNG attach both, with the PDF placed first as the primary download.
5. Real CIANO14 40 package import attaches correct drawings per configuration.
6. CAD shaft material label reads '3Cr13 (stainless)' for 40 mm motor.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from motor_ai_sim.cad import bom
from motor_ai_sim.production import import_package as IMP, store
from tests.test_production_access import prod_env


@pytest.fixture()
def fake_package_with_l12_l20_drawings(tmp_path):
    """Build a synthetic package with L12, L20 and common drawings (both PDF and PNG)."""
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir(parents=True)
    assets_dir = pkg_dir / "assets"
    assets_dir.mkdir(parents=True)

    # Create dummy asset pairs (PDF and PNG)
    drawing_files = [
        "CIANO14_40_L12_shaft.pdf", "CIANO14_40_L12_shaft.png",
        "CIANO14_40_L20_shaft.pdf", "CIANO14_40_L20_shaft.png",
        "CIANO14_40_L12_magnet.pdf", "CIANO14_40_L12_magnet.png",
        "CIANO14_40_L20_magnet.pdf", "CIANO14_40_L20_magnet.png",
        "CIANO14_40_L12_stator.pdf", "CIANO14_40_L12_stator.png",
        "CIANO14_40_L20_stator.pdf", "CIANO14_40_L20_stator.png",
        "CIANO14_40_L12_rotor.pdf", "CIANO14_40_L12_rotor.png",
        "CIANO14_40_L20_rotor.pdf", "CIANO14_40_L20_rotor.png",
        "CIANO14_40_L12_coil.pdf", "CIANO14_40_L12_coil.png",
        "CIANO14_40_L20_coil.pdf", "CIANO14_40_L20_coil.png",
        "CIANO14_40_L12_screw.pdf", "CIANO14_40_L12_screw.png",
        "CIANO14_40_L20_screw.pdf", "CIANO14_40_L20_screw.png",
        "CIANO14_40_front_holder.pdf", "CIANO14_40_front_holder.png",
        "CIANO14_40_back_holder.pdf", "CIANO14_40_back_holder.png",
        "CIANO14_40_ring.pdf", "CIANO14_40_ring.png",
    ]
    for fname in drawing_files:
        p = assets_dir / fname
        if fname.endswith(".pdf"):
            p.write_bytes(f"%PDF-1.4 dummy content for {fname}".encode())
        else:
            p.write_bytes(f"\x89PNG\r\n\x1a\n dummy content for {fname}".encode())

    # Non-drawing images
    (assets_dir / "fem_front_holder_49N.png").write_bytes(b"\x89PNG fem")
    (assets_dir / "q1_ring.png").write_bytes(b"\x89PNG q1")
    (assets_dir / "q3_sample_shaft.jpeg").write_bytes(b"\xff\xd8\xff sample")

    package_data = {
        "title": "CIANO14 40 电机生产资料包",
        "sections": [
            {"h": "1. 订单 (100台)"},
            {"h3": "3.1 BOM — CIANO14 40 L12（100 台）"},
            {
                "table": [
                    ["序号", "零件", "类型", "每台", "订单合计（100 台）", "材料 / 表面", "规格", "图纸 / 要求"],
                    ["1", "定子铁芯", "冲压", "1", "100", "20SW1200", "40x24", "定子图"],
                    ["2", "转子铁芯", "冲压", "1", "100", "20SW1200", "23.5x9.9", "转子图"],
                    ["3", "磁钢", "定制", "14", "1400", "52SH", "12 mm", "磁钢图"],
                    ["4", "线圈", "自制", "6", "600", "2.5x0.6", "7 匝", "线圈图"],
                    ["5", "轴", "机加工", "1", "100", "3Cr13", "44.6", "轴图"],
                    ["6", "前支架", "机加工", "1", "100", "6061-T6", "通用", "前支架图"],
                    ["7", "后支架", "机加工", "1", "100", "6061-T6", "通用", "后支架图"],
                    ["8", "线圈固定环", "机加工", "2", "200", "GFRP", "通用", "环图"],
                    ["9", "内六角螺钉", "外购", "6", "600", "A2-70", "M2x18", "螺钉图"],
                ]
            },
            {"h3": "3.2 BOM — CIANO14 40 L20（40 台）"},
            {
                "table": [
                    ["序号", "零件", "类型", "每台", "订单合计（40 台）", "材料 / 表面", "规格", "图纸 / 要求"],
                    ["1", "定子铁芯", "冲压", "1", "40", "20SW1200", "40x24", "定子图"],
                    ["2", "转子铁芯", "冲压", "1", "40", "20SW1200", "23.5x9.9", "转子图"],
                    ["3", "磁钢", "定制", "14", "560", "52SH", "20 mm", "磁钢图"],
                    ["4", "线圈", "自制", "6", "240", "2.5x0.6", "7 匝", "线圈图"],
                    ["5", "轴", "机加工", "1", "40", "3Cr13", "52.6", "轴图"],
                    ["6", "前支架", "机加工", "1", "40", "6061-T6", "通用", "前支架图"],
                    ["7", "后支架", "机加工", "1", "40", "6061-T6", "通用", "后支架图"],
                    ["8", "线圈固定环", "机加工", "2", "80", "GFRP", "通用", "环图"],
                    ["9", "内六角螺钉", "外购", "6", "240", "A2-70", "M2x26", "螺钉图"],
                ]
            },
            {"h": "7. 有限元"},
            {"img": "assets/fem_front_holder_49N.png"},
            {"h": "8. 问题回复"},
            {"img": "assets/q1_ring.png"},
            {"img": "assets/q3_sample_shaft.jpeg"},
            {
                "drawings": [
                    ["轴 CIANO14 40 L12", 100, "assets/CIANO14_40_L12_shaft.png"],
                    ["轴 CIANO14 40 L20", 40, "assets/CIANO14_40_L20_shaft.png"],
                    ["磁钢 CIANO14 40 L12", 1400, "assets/CIANO14_40_L12_magnet.png"],
                    ["磁钢 CIANO14 40 L20", 560, "assets/CIANO14_40_L20_magnet.png"],
                    ["定子铁芯 CIANO14 40 L12", 100, "assets/CIANO14_40_L12_stator.png"],
                    ["定子铁芯 CIANO14 40 L20", 40, "assets/CIANO14_40_L20_stator.png"],
                    ["转子铁芯 CIANO14 40 L12", 100, "assets/CIANO14_40_L12_rotor.png"],
                    ["转子铁芯 CIANO14 40 L20", 40, "assets/CIANO14_40_L20_rotor.png"],
                    ["线圈 CIANO14 40 L12", 600, "assets/CIANO14_40_L12_coil.png"],
                    ["线圈 CIANO14 40 L20", 240, "assets/CIANO14_40_L20_coil.png"],
                    ["内六角螺钉 CIANO14 40 L12", 600, "assets/CIANO14_40_L12_screw.png"],
                    ["内六角螺钉 CIANO14 40 L20", 240, "assets/CIANO14_40_L20_screw.png"],
                    ["前支架 CIANO14 40（L12 / L20 通用）", 140, "assets/CIANO14_40_front_holder.png"],
                    ["后支架 CIANO14 40（L12 / L20 common）", 140, "assets/CIANO14_40_back_holder.png"],
                    ["线圈固定环 CIANO14 40（universal）", 280, "assets/CIANO14_40_ring.png"],
                ]
            },
        ],
    }

    pkg_path = pkg_dir / "package.json"
    pkg_path.write_text(json.dumps(package_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "pkg_path": pkg_path,
        "assets_dir": assets_dir,
    }


def test_import_drawings_configuration_isolation_and_pdf_priority(prod_env, fake_package_with_l12_l20_drawings):
    """Test importing L12 and L20 configurations:
    - Each config gets strictly its own drawings for config-specific parts.
    - Common drawings attach to both.
    - Both PDF and PNG are attached, with PDF as primary download (index 0).
    - FEM and question images are NOT attached.
    """
    pkg_path = fake_package_with_l12_l20_drawings["pkg_path"]
    die = "CIANO14 40 new"

    # --- 1. Import L12 ---
    res_l12 = IMP.import_package(
        package_path=pkg_path,
        die=die,
        config="L12",
        assets_dir=fake_package_with_l12_l20_drawings["assets_dir"],
    )
    assert res_l12["parts_count"] == 9

    parts_l12 = {p["name"]["en"]: p for p in store.load_file(die, "L12", "parts.json")["parts"]}

    # Verify Shaft 40_12: has strictly L12 drawings (PDF first, PNG second)
    shaft_12 = parts_l12["Shaft 40_12"]
    shaft_files_12 = shaft_12["drawing_files"]
    assert len(shaft_files_12) == 2, f"Expected 2 files (PDF+PNG), got {shaft_files_12}"
    assert shaft_files_12[0]["kind"] == "pdf"
    assert shaft_files_12[0]["name"] == "CIANO14_40_L12_shaft.pdf"
    assert shaft_files_12[1]["kind"] == "png"
    assert shaft_files_12[1]["name"] == "CIANO14_40_L12_shaft.png"
    assert not any("L20" in df["name"] for df in shaft_files_12)
    assert not any("sample" in df["name"] for df in shaft_files_12)

    # Verify Magnet 40_12: strictly L12
    mag_12 = parts_l12["Magnet 40_12"]
    assert len(mag_12["drawing_files"]) == 2
    assert mag_12["drawing_files"][0]["name"] == "CIANO14_40_L12_magnet.pdf"
    assert mag_12["drawing_files"][1]["name"] == "CIANO14_40_L12_magnet.png"

    # Verify Stator core 40_12: strictly L12
    stator_12 = parts_l12["Stator core 40_12"]
    assert len(stator_12["drawing_files"]) == 2
    assert stator_12["drawing_files"][0]["name"] == "CIANO14_40_L12_stator.pdf"
    assert stator_12["drawing_files"][1]["name"] == "CIANO14_40_L12_stator.png"

    # Verify Rotor core 40_12: strictly L12
    rotor_12 = parts_l12["Rotor core 40_12"]
    assert len(rotor_12["drawing_files"]) == 2
    assert rotor_12["drawing_files"][0]["name"] == "CIANO14_40_L12_rotor.pdf"
    assert rotor_12["drawing_files"][1]["name"] == "CIANO14_40_L12_rotor.png"

    # Verify Coil 40_12: strictly L12
    coil_12 = parts_l12["Coil 40_12"]
    assert len(coil_12["drawing_files"]) == 2
    assert coil_12["drawing_files"][0]["name"] == "CIANO14_40_L12_coil.pdf"
    assert coil_12["drawing_files"][1]["name"] == "CIANO14_40_L12_coil.png"

    # Verify Socket head screw (purchased): strictly L12 screw drawing
    screw_12 = parts_l12["Socket head screw"]
    assert len(screw_12["drawing_files"]) == 2
    assert screw_12["drawing_files"][0]["name"] == "CIANO14_40_L12_screw.pdf"
    assert screw_12["drawing_files"][1]["name"] == "CIANO14_40_L12_screw.png"

    # Verify Common parts: Front holder, Back holder, Coil-fixing ring
    front_12 = parts_l12["Front holder 40"]
    assert len(front_12["drawing_files"]) == 2
    assert front_12["drawing_files"][0]["name"] == "CIANO14_40_front_holder.pdf"
    assert front_12["drawing_files"][1]["name"] == "CIANO14_40_front_holder.png"
    assert not any("fem" in df["name"] for df in front_12["drawing_files"])

    back_12 = parts_l12["Back holder 40"]
    assert len(back_12["drawing_files"]) == 2
    assert back_12["drawing_files"][0]["name"] == "CIANO14_40_back_holder.pdf"
    assert back_12["drawing_files"][1]["name"] == "CIANO14_40_back_holder.png"

    ring_12 = parts_l12["Coil-fixing ring 40"]
    assert len(ring_12["drawing_files"]) == 2
    assert ring_12["drawing_files"][0]["name"] == "CIANO14_40_ring.pdf"
    assert ring_12["drawing_files"][1]["name"] == "CIANO14_40_ring.png"
    assert not any("q1" in df["name"] for df in ring_12["drawing_files"])

    # Verify saved files exist on disk
    file_dir = store.file_dir(die, "L12")
    for p in parts_l12.values():
        for df in p["drawing_files"]:
            assert (file_dir / df["file_id"]).is_file(), f"File missing on disk: {df['name']}"

    # --- 2. Import L20 ---
    res_l20 = IMP.import_package(
        package_path=pkg_path,
        die=die,
        config="L20",
        assets_dir=fake_package_with_l12_l20_drawings["assets_dir"],
    )
    assert res_l20["parts_count"] == 9

    parts_l20 = {p["name"]["en"]: p for p in store.load_file(die, "L20", "parts.json")["parts"]}

    # Verify Shaft 40_20: has strictly L20 drawings
    shaft_20 = parts_l20["Shaft 40_20"]
    shaft_files_20 = shaft_20["drawing_files"]
    assert len(shaft_files_20) == 2
    assert shaft_files_20[0]["kind"] == "pdf"
    assert shaft_files_20[0]["name"] == "CIANO14_40_L20_shaft.pdf"
    assert shaft_files_20[1]["kind"] == "png"
    assert shaft_files_20[1]["name"] == "CIANO14_40_L20_shaft.png"
    assert not any("L12" in df["name"] for df in shaft_files_20)

    # Verify Magnet 40_20: strictly L20
    mag_20 = parts_l20["Magnet 40_20"]
    assert len(mag_20["drawing_files"]) == 2
    assert mag_20["drawing_files"][0]["name"] == "CIANO14_40_L20_magnet.pdf"
    assert mag_20["drawing_files"][1]["name"] == "CIANO14_40_L20_magnet.png"

    # Verify Common parts also attach to L20
    front_20 = parts_l20["Front holder 40"]
    assert len(front_20["drawing_files"]) == 2
    assert front_20["drawing_files"][0]["name"] == "CIANO14_40_front_holder.pdf"
    assert front_20["drawing_files"][1]["name"] == "CIANO14_40_front_holder.png"

    back_20 = parts_l20["Back holder 40"]
    assert len(back_20["drawing_files"]) == 2
    assert back_20["drawing_files"][0]["name"] == "CIANO14_40_back_holder.pdf"

    ring_20 = parts_l20["Coil-fixing ring 40"]
    assert len(ring_20["drawing_files"]) == 2
    assert ring_20["drawing_files"][0]["name"] == "CIANO14_40_ring.pdf"


def test_real_ciano14_40_package_import_drawings(prod_env):
    """If real CIANO14 40 package is present on system, test importing it."""
    real_pkg = Path(r"C:\Users\vadim\Projects\CIANO14_40_motor_model\production\CIANO14_40\package.json")
    if not real_pkg.is_file():
        pytest.skip("Real CIANO14 40 package not found on this environment")

    die = "CIANO14 40 new"

    # Import L12
    IMP.import_package(package_path=real_pkg, die=die, config="L12")
    parts_12 = {p["name"]["en"]: p for p in store.load_file(die, "L12", "parts.json")["parts"]}

    shaft_12 = parts_12["Shaft 40_12"]
    shaft_names_12 = [df["name"] for df in shaft_12["drawing_files"]]
    # Must contain L12 drawings (PDF + PNG), must NOT contain L20 drawings
    assert "CIANO14_40_L12_shaft_2026-10-16.pdf" in shaft_names_12
    assert "CIANO14_40_L12_shaft_2026-10-16.png" in shaft_names_12
    assert not any("L20" in n for n in shaft_names_12)
    # PDF must be first (primary download)
    assert shaft_12["drawing_files"][0]["kind"] == "pdf"
    assert shaft_12["drawing_files"][0]["name"] == "CIANO14_40_L12_shaft_2026-10-16.pdf"

    # Import L20
    IMP.import_package(package_path=real_pkg, die=die, config="L20")
    parts_20 = {p["name"]["en"]: p for p in store.load_file(die, "L20", "parts.json")["parts"]}

    shaft_20 = parts_20["Shaft 40_20"]
    shaft_names_20 = [df["name"] for df in shaft_20["drawing_files"]]
    assert "CIANO14_40_L20_shaft_2026-10-16.pdf" in shaft_names_20
    assert "CIANO14_40_L20_shaft_2026-10-16.png" in shaft_names_20
    assert not any("L12" in n for n in shaft_names_20)
    assert shaft_20["drawing_files"][0]["kind"] == "pdf"
    assert shaft_20["drawing_files"][0]["name"] == "CIANO14_40_L20_shaft_2026-10-16.pdf"


def test_cad_shaft_material_label_3cr13_for_40mm():
    """Requirement: The CAD shaft material label must read 3Cr13 (stainless)
    for the 40 mm motor."""
    from tests.test_cad_bom import MATS, make
    b_l12, _ = make("L12", materials={**MATS, "Shaft": "3Cr13"})      # the Ø40 reference profile's shaft steel
    k_l12 = {l["key"]: l for l in b_l12["lines"]}
    assert k_l12["shaft"]["material"] == "3Cr13 (stainless)"

    # L20 is built from the same Ø40 reference profile (cad/store.py BASE_PROFILE), which states the shaft steel
    import json
    from motor_ai_sim.cad import store as CS
    assert '"Shaft": "3Cr13"' in open(CS.BASE_PROFILE, encoding="utf-8").read()
