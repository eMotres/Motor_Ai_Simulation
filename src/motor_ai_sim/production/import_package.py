"""Import CLI for factory-document package.json files into the AeroStator Production module.

Contract: docs/PRODUCTION_MODULE_DESIGN.md section 7 and BRIEF_production_module_stage1.md.

Usage:
    python -m motor_ai_sim.production.import_package <package.json> --die "CIANO14 40 new" --config L12 --assets <dir>
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Any, Optional

from motor_ai_sim.production import models, store

log = logging.getLogger(__name__)

# Keywords for matching drawing file names to motor parts
DRAWING_KEYWORDS: dict[str, list[str]] = {
    "shaft": ["轴", "shaft", "axis"],
    "magnet": ["磁钢", "磁体", "magnet"],
    "front_holder": ["前支架", "前端盖", "前盖", "front_holder", "front holder", "front"],
    "back_holder": ["后支架", "后端盖", "后盖", "back_holder", "back holder", "back", "rear"],
    "ring": ["环", "ring", "retaining"],
}

PART_NAME_MAP: list[tuple[str, str]] = [
    ("定子铁芯", "Stator core"),
    ("转子铁芯", "Rotor core"),
    ("线圈固定环", "Coil-fixing ring"),
    ("磁钢", "Magnet"),
    ("线圈", "Coil"),
    ("前支架", "Front holder"),
    ("后支架", "Back holder"),
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
    ("轴", "Shaft"),
]

# Classification of parts for naming rules
CONFIG_DIFFERING_PARTS = {"Stator core", "Rotor core", "Magnet", "Coil", "Shaft"}
PHASE_BOARD_PARTS = {"Phase board rev C", "Phase board"}
MANUFACTURED_PARTS = {"Front holder", "Back holder", "Coil-fixing ring"}

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def resolve_die_stator_diameter(die: str) -> str:
    """Find stator diameter for a die from catalog, or fallback from die name."""
    die_clean = str(die or "").strip()
    try:
        from motor_ai_sim.routes import catalog
        cat = catalog._load()
        die_lower = die_clean.lower()
        for m in cat.get("motors", []):
            if str(m.get("name") or "").strip().lower() == die_lower:
                diam = m.get("diameter_mm")
                if diam is not None:
                    return str(diam)
        for m in cat.get("motors", []):
            m_name = str(m.get("name") or "").strip().lower()
            if m_name and (die_lower.startswith(m_name) or m_name.startswith(die_lower)):
                diam = m.get("diameter_mm")
                if diam is not None:
                    return str(diam)
    except Exception:
        pass

    digits = re.findall(r"\d+", die_clean)
    if digits:
        for num in digits:
            if num in ("30", "40", "50", "85", "90", "100", "150", "200"):
                return num
        return digits[-1]
    return "40"


def resolve_config_digits(config: str) -> str:
    """Extract configuration digits (e.g. '12' from 'L12', '20' from 'L20', '12' from '40_12')."""
    digits = re.findall(r"\d+", str(config or ""))
    return digits[-1] if digits else str(config or "")


def _apply_part_naming_rule(base_en: str, size: str = "", cfg_digits: str = "") -> str:
    """Apply generic size and config suffixes to canonical English part names."""
    if not size:
        return base_en

    if base_en in CONFIG_DIFFERING_PARTS:
        if cfg_digits:
            return f"{base_en} {size}_{cfg_digits}"
        return f"{base_en} {size}"
    elif base_en in PHASE_BOARD_PARTS:
        return f"Phase board {size} rev C"
    elif base_en in MANUFACTURED_PARTS:
        return f"{base_en} {size}"
    return base_en


def resolve_part_name(name_raw: str, die: str = "", config: str = "", size: str = "", cfg_digits: str = "") -> tuple[str, str, bool]:
    """Resolve part name to (en, zh, unmapped) according to canonical part mappings and naming rules."""
    name_str = name_raw.strip()
    if not size and die:
        size = resolve_die_stator_diameter(die)
    if not cfg_digits and config:
        cfg_digits = resolve_config_digits(config)

    for zh_key, en_val in PART_NAME_MAP:
        if zh_key in name_str:
            final_en = _apply_part_naming_rule(en_val, size, cfg_digits)
            return final_en, name_str, False

    # Parentheses check e.g. "主轴 (Shaft)"
    m = re.search(r"[（(]([A-Za-z0-9_ /.-]+)[）)]", name_str)
    if m:
        en_part = m.group(1).strip()
        if en_part and not _CJK_RE.search(en_part):
            final_en = _apply_part_naming_rule(en_part, size, cfg_digits)
            return final_en, name_str, False

    # Pure English
    if not _CJK_RE.search(name_str) and any(c.isalpha() for c in name_str):
        # Check if matches any known base
        for _, en_val in PART_NAME_MAP:
            if en_val.lower() == name_str.lower():
                final_en = _apply_part_naming_rule(en_val, size, cfg_digits)
                return final_en, "", False
        final_en = _apply_part_naming_rule(name_str, size, cfg_digits)
        return final_en, "", False

    # Unknown: keep zh and flag
    return "", name_str, True



def _translate_tables(tables: list, translate_fn: Optional[Any] = None) -> list:
    """Translate table cells from zh to en. Cells without CJK copied as is, each distinct CJK cell translated once."""
    if not isinstance(tables, list):
        return []
    if translate_fn is None:
        try:
            from motor_ai_sim.production import translate as T
            translate_fn = T.translate_text
        except Exception:
            translate_fn = None

    cache: dict[str, str] = {}
    out_tables = []
    for tbl in tables:
        if not isinstance(tbl, list):
            out_tables.append(tbl)
            continue
        out_tbl = []
        for row in tbl:
            if not isinstance(row, list):
                out_tbl.append(row)
                continue
            out_row = []
            for cell in row:
                cell_str = str(cell) if cell is not None else ""
                if not _CJK_RE.search(cell_str):
                    out_row.append(cell_str)
                else:
                    if cell_str not in cache:
                        if translate_fn is not None:
                            try:
                                cache[cell_str] = str(translate_fn(cell_str, "zh", "en")).strip() or cell_str
                            except Exception:
                                cache[cell_str] = cell_str
                        else:
                            cache[cell_str] = cell_str
                    out_row.append(cache[cell_str])
            out_tbl.append(out_row)
        out_tables.append(out_tbl)
    return out_tables


def _is_bom_table(table: list) -> bool:
    if not isinstance(table, list) or not table:
        return False
    header = table[0]
    if not isinstance(header, list):
        return False
    header_str = " ".join(str(c) for c in header)
    return ("序号" in header_str and "零件" in header_str) or ("part" in header_str.lower() and "qty" in header_str.lower())


def _parse_order_qty_from_text(text: str) -> Optional[int]:
    m = re.search(r"[（(](\d+)\s*(?:台|pcs?|件)[）)]", text, re.IGNORECASE)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return None


def _map_part_kind(s: str) -> str:
    s = str(s or "").strip().lower()
    if "自制" in s or "made" in s:
        return "made"
    if "外购" in s or "采购" in s or "标准件" in s or "purchased" in s or "standard" in s:
        return "purchased"
    if "辅料" in s or "耗材" in s or "consumable" in s:
        return "consumable"
    return "made"


def _detect_section_kind(heading: str, text: str) -> str:
    combined = f"{heading} {text}".lower()
    if any(k in combined for k in ("公差", "配合", "fit", "tolerance")):
        return "fits"
    if any(k in combined for k in ("绕线", "排线", "接线", "线路板", "winding", "wire", "pcb", "board")):
        return "winding"
    if any(k in combined for k in ("测试", "检验", "检测", "test", "inspection")):
        return "tests"
    return "assembly"


def _match_part_for_drawing(drawing_name: str, parts: list[dict]) -> Optional[dict]:
    d_lower = drawing_name.lower()
    for cat, kws in DRAWING_KEYWORDS.items():
        if cat in d_lower or any(kw in d_lower for kw in kws):
            # Find matching part
            for p in parts:
                p_name_zh = p["name"].get("zh", "").lower()
                p_name_en = p["name"].get("en", "").lower()
                if any(kw in p_name_zh or kw in p_name_en for kw in kws):
                    return p
    # Fallback substring match
    for p in parts:
        zh = p["name"].get("zh", "")
        en = p["name"].get("en", "")
        if zh and zh in drawing_name:
            return p
        if en and en.lower() in d_lower:
            return p
    return None


def import_package(package_path: Path, die: str, config: str,
                   assets_dir: Optional[Path] = None,
                   order_quantity: Optional[int] = None) -> dict:
    if not package_path.is_file():
        raise FileNotFoundError(f"Package file not found: {package_path}")

    raw_data = json.loads(package_path.read_text(encoding="utf-8"))
    sections = raw_data.get("sections") or []

    # 1. Determine order quantity
    qty = order_quantity
    if qty is None:
        for s in sections:
            if isinstance(s, dict):
                for k in ("h", "h3", "title"):
                    if k in s and isinstance(s[k], str):
                        found = _parse_order_qty_from_text(s[k])
                        if found:
                            qty = found
                            break
            if qty is not None:
                break
    if qty is None or qty <= 0:
        qty = 100

    # 2. Extract BOM table for this config
    bom_table: Optional[list] = None
    bom_heading = ""
    current_heading = ""

    for s in sections:
        if not isinstance(s, dict):
            continue
        if "h" in s:
            current_heading = str(s["h"])
        elif "h3" in s:
            current_heading = str(s["h3"])
        elif "table" in s and _is_bom_table(s["table"]):
            # Check if this BOM table belongs to this config
            if config.lower() in current_heading.lower() or bom_table is None:
                bom_table = s["table"]
                bom_heading = current_heading
                if config.lower() in current_heading.lower():
                    # Exact config match
                    break

    parts: list[dict] = []
    bom_rows: list[dict] = []

    if bom_table and len(bom_table) > 1:
        # Header is row 0: 序号, 零件, 类型, 每台, 订单合计（N 台）, 材料 / 表面, 规格, 图纸 / 要求
        for row in bom_table[1:]:
            if not isinstance(row, list) or len(row) < 2:
                continue
            name_raw = str(row[1] if len(row) > 1 else "").strip()
            if not name_raw:
                continue
            kind_raw = str(row[2] if len(row) > 2 else "自制").strip()
            kind = _map_part_kind(kind_raw)

            qty_per_motor = 1
            try:
                if len(row) > 3 and str(row[3]).strip():
                    qty_per_motor = int(float(str(row[3]).strip()))
            except (ValueError, TypeError):
                qty_per_motor = 1

            order_tot = qty_per_motor * qty
            try:
                if len(row) > 4 and str(row[4]).strip():
                    order_tot = int(float(str(row[4]).strip()))
            except (ValueError, TypeError):
                order_tot = qty_per_motor * qty

            material = str(row[5] if len(row) > 5 else "").strip()
            spec = str(row[6] if len(row) > 6 else "").strip()
            desc = str(row[7] if len(row) > 7 else "").strip()

            part_id = models._new_id("pt")
            en_name, zh_name, unmapped = resolve_part_name(name_raw, die=die, config=config)
            part_name = {
                "en": en_name,
                "zh": zh_name,
                "src_lang": "en" if en_name else "zh",
                "status": {
                    "en": "confirmed" if en_name else "pending",
                    "zh": "confirmed" if zh_name else "pending",
                },
            }
            if unmapped:
                part_name["unmapped"] = True
            part_desc = models.make_bilingual(desc, "zh")

            material_i18n = models.ensure_i18n_translated({"zh": material, "en": ""})
            spec_i18n = models.ensure_i18n_translated({"zh": spec, "en": ""})

            part = models.normalize_part({
                "id": part_id,
                "name": part_name,
                "kind": kind,
                "qty_per_motor": qty_per_motor,
                "material": material,
                "spec": spec,
                "material_i18n": material_i18n,
                "spec_i18n": spec_i18n,
                "description": part_desc,
                "drawing_files": [],
                "revision": "1.0",
            })
            parts.append(part)
            bom_rows.append({
                "part_id": part_id,
                "qty_per_motor": qty_per_motor,
                "order_total": order_tot,
            })

    # 3. Collect drawings and images, copy assets to production file storage
    drawing_candidates: list[tuple[str, str]] = []  # (drawing_name_or_caption, file_path_str)
    for s in sections:
        if not isinstance(s, dict):
            continue
        if "drawings" in s and isinstance(s["drawings"], list):
            for item in s["drawings"]:
                if isinstance(item, list) and len(item) >= 3:
                    caption = str(item[0])
                    fpath = str(item[2])
                    drawing_candidates.append((caption, fpath))
        if "img" in s and isinstance(s["img"], str):
            fpath = str(s["img"])
            caption = Path(fpath).stem
            drawing_candidates.append((caption, fpath))

    # Also check if part descriptions mentioned image files (e.g. shaft.png)
    for p in parts:
        desc_text = p["description"].get("zh", "")
        for word in desc_text.split():
            if re.search(r"\.(png|jpg|jpeg|pdf|step)$", word, re.IGNORECASE):
                drawing_candidates.append((word, word))

    # Resolve asset files and attach to parts
    search_dirs = [package_path.parent]
    if assets_dir:
        search_dirs.insert(0, assets_dir)
    search_dirs.append(package_path.parent / "assets")

    for caption, rel_path in drawing_candidates:
        fname = Path(rel_path).name
        matched_part = _match_part_for_drawing(f"{caption} {fname}", parts)
        if not matched_part:
            continue

        # Look for the asset file
        found_file: Optional[Path] = None
        for sdir in search_dirs:
            candidate = sdir / rel_path
            if candidate.is_file():
                found_file = candidate
                break
            candidate2 = sdir / fname
            if candidate2.is_file():
                found_file = candidate2
                break

        if found_file:
            try:
                data = found_file.read_bytes()
                file_id = secrets.token_urlsafe(16)
                store.save_uploaded_file(die, config, file_id, data)
                is_pdf = found_file.suffix.lower() == ".pdf"
                drawing_entry = {
                    "file_id": file_id,
                    "kind": "pdf" if is_pdf else "png",
                    "name": fname,
                }
                # Check not already added
                if not any(df["file_id"] == file_id or df["name"] == fname for df in matched_part["drawing_files"]):
                    matched_part["drawing_files"].append(drawing_entry)
                    store.append_audit(
                        die, config,
                        models.audit_entry("system/import", "upload_drawing", "part", matched_part["id"],
                                           {"name": fname, "file_id": file_id})
                    )
            except Exception as ex:                     # noqa: BLE001
                log.warning("failed to copy asset %s: %s", found_file, ex)

    # 4. Process non-BOM sections -> process sections
    process_sections: list[dict] = []
    current_proc_title = ""
    current_proc_paragraphs: list[str] = []
    current_proc_tables: list[Any] = []

    def flush_proc_section():
        nonlocal current_proc_title, current_proc_paragraphs, current_proc_tables
        if not current_proc_title and not current_proc_paragraphs and not current_proc_tables:
            return
        title_zh = current_proc_title or "工艺说明"
        body_zh = "\n\n".join(current_proc_paragraphs)
        kind = _detect_section_kind(title_zh, body_zh)

        tables_en = _translate_tables(current_proc_tables)
        sec = models.normalize_process_section({
            "kind": kind,
            "title": models.make_bilingual(title_zh, "zh"),
            "body": models.make_bilingual(body_zh, "zh"),
            "tables": current_proc_tables,
            "tables_i18n": {"zh": current_proc_tables, "en": tables_en},
        })
        process_sections.append(sec)
        current_proc_title = ""
        current_proc_paragraphs = []
        current_proc_tables = []

    for s in sections:
        if not isinstance(s, dict):
            continue
        if "h" in s or "h3" in s:
            heading = str(s.get("h") or s.get("h3") or "").strip()
            # If this is the BOM heading, skip processing as a process section
            if "bom" in heading.lower() and config.lower() in heading.lower():
                flush_proc_section()
                continue
            flush_proc_section()
            current_proc_title = heading
        elif "p" in s:
            p_text = str(s["p"]).strip()
            if p_text:
                current_proc_paragraphs.append(p_text)
        elif "bullets" in s and isinstance(s["bullets"], list):
            for b in s["bullets"]:
                b_text = str(b).strip()
                if b_text:
                    current_proc_paragraphs.append(f"• {b_text}")
        elif "table" in s and not _is_bom_table(s["table"]):
            current_proc_tables.append(s["table"])

    flush_proc_section()

    # 5. Build and save production record
    title_text = raw_data.get("title") or f"{die} {config} 生产管理"
    production = models.normalize_production({
        "title": models.make_bilingual(title_text, "zh"),
        "order_quantity": qty,
        "members": [],
        "status": "active",
        "current_revision": "1.1",
    }, die, config)

    # 6. Revision 1.1
    rev_summary = models.make_bilingual(
        f"初始导入：{die} {config} 工厂技术资料包 (订单数量: {qty})", "zh"
    )
    rev_changes = [
        models.make_bilingual(f"导入 {len(parts)} 个零件及 BOM 配置", "zh"),
        models.make_bilingual(f"导入 {len(process_sections)} 个工艺工序说明", "zh"),
    ]
    revision = models.normalize_revision({
        "number": "1.1",
        "author_email": "system/import",
        "summary": rev_summary,
        "changes": rev_changes,
        "acknowledgements": [],
    })

    # Save everything
    store.save_file(die, config, "production.json", production)
    store.save_file(die, config, "parts.json", {"parts": parts})
    store.save_file(die, config, "bom.json", {"rows": bom_rows})
    if not (store.path_for(die, config) / "costs.json").is_file():
        store.save_file(die, config, "costs.json", {"rows": [], "extras": [], "margin_pct": 0.0})
    store.save_file(die, config, "process.json", {"sections": process_sections})
    store.save_file(die, config, "documents.json", {"documents": []})
    store.save_file(die, config, "threads.json", {"threads": []})
    store.save_file(die, config, "proposals.json", {"proposals": []})
    store.save_file(die, config, "revisions.json", {"revisions": [revision]})

    store.append_audit(
        die, config,
        models.audit_entry(
            "system/import", "import_package", "production", f"{die}/{config}",
            {"parts": len(parts), "bom_rows": len(bom_rows), "process_sections": len(process_sections), "order_quantity": qty}
        )
    )

    return {
        "production": production,
        "parts_count": len(parts),
        "bom_rows_count": len(bom_rows),
        "process_sections_count": len(process_sections),
        "revision": "1.1",
    }


def main():
    parser = argparse.ArgumentParser(
        description="Import factory package.json into AeroStator Production module"
    )
    parser.add_argument("package", type=Path, help="Path to factory package.json")
    parser.add_argument("--die", required=True, help="Motor die name (e.g. 'CIANO14 40 new')")
    parser.add_argument("--config", required=True, help="Motor config name (e.g. 'L12')")
    parser.add_argument("--assets", type=Path, default=None, help="Directory containing asset files/drawings")
    parser.add_argument("--order-quantity", type=int, default=None, help="Override order quantity")

    args = parser.parse_args()

    result = import_package(
        package_path=args.package,
        die=args.die,
        config=args.config,
        assets_dir=args.assets,
        order_quantity=args.order_quantity,
    )

    print(f"Successfully imported production for {args.die} / {args.config}:")
    print(f"  Parts: {result['parts_count']}")
    print(f"  BOM rows: {result['bom_rows_count']}")
    print(f"  Process sections: {result['process_sections_count']}")
    print(f"  Current revision: {result['revision']}")


if __name__ == "__main__":
    main()
