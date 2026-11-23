"""The mass-production checklist as an Excel file (owner 2026-11-23): an EXPORT of what the system holds, never an input.

Three sheets, bilingual (English + Chinese): the main list, the open-items tracker and the legend with the statistics.  There is no
import path: requirements and statuses change only in the production module.
"""
from __future__ import annotations

import datetime
import io
from typing import Any, List

from motor_ai_sim.production import checklist as C

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_MAX_CELL = 32000

_FILL = {"to_do": "E5E7EB", "to_confirm": "FEF3C7", "missing_info": "FECACA", "optional": "DBEAFE", "done": "BBF7D0"}


def _both(pair: Any) -> str:
    """'English' newline 'Chinese' (one line when they are the same)."""
    if isinstance(pair, dict):
        en, zh = str(pair.get("en") or ""), str(pair.get("zh") or "")
    else:
        en, zh = pair
    text = en if en == zh or not zh else (zh if not en else "%s\n%s" % (en, zh))
    return text[:_MAX_CELL]


def _label(table: dict, key: str) -> str:
    en, zh = table.get(key, (key, key))
    return "%s / %s" % (en, zh)


def _evidence_text(ev: Any) -> str:
    if not isinstance(ev, dict):
        return ""
    if ev.get("hidden"):
        return "(internal document)"
    return " - ".join(x for x in (str(ev.get("name") or ""), str(ev.get("note") or "")) if x)


def build_workbook(data: dict, production_title: Any = None, generated_at: str = "") -> bytes:
    """``data`` is ``checklist.build(...)``."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from motor_ai_sim.spreadsheet_text import force_text_cells

    items: List[dict] = data["items"]
    st = data["stats"]
    rev = data["revision"]
    wrap = Alignment(wrap_text=True, vertical="top")
    head_fill = PatternFill("solid", fgColor="1F2937")

    def header(ws, row_cells):
        ws.append(row_cells)
        for cell in ws[ws.max_row]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = head_fill
            cell.alignment = Alignment(wrap_text=True, vertical="center")

    def finish(ws, widths, freeze="A2"):
        for col, w in zip("ABCDEFGHIJKLMNOP", widths):
            ws.column_dimensions[col].width = w
        ws.freeze_panes = freeze
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = wrap
        force_text_cells(ws)                  # item names, requirements and notes are user text: never a formula

    # 1. the main list
    wb = Workbook()
    ws = wb.active
    ws.title = "Checklist 清单"
    header(ws, ["No\n序号", "Category\n类别", "Item\n项目", "Requirement / acceptance\n要求 / 验收标准", "How verified\n验证方式",
                "Responsible\n责任方", "Evidence\n证据", "Status\n状态", "Open item\n未决项 (Y/N)", "Source\n来源", "Revision\n版本"])
    for it in items:
        ws.append([it["no"], _label(C.CATEGORIES, it["category"]), _both(it["item"]), _both(it["requirement"]), _both(it["how"]),
                   _label(C.RESPONSIBLE_LABELS, it["responsible"]), _evidence_text(it.get("evidence")),
                   _label(C.STATUS_LABELS, it["status"]) + ("\n(changed since confirmed / 确认后有变更)" if it.get("changed") else ""),
                   "Y" if it["open"] else "N", it["source"], "%s%s" % (it["revision"], " (%s)" % it["revision_date"] if it.get("revision_date") else "")])
        ws.cell(row=ws.max_row, column=8).fill = PatternFill("solid", fgColor=_FILL[it["status"]])
    finish(ws, (6, 24, 36, 60, 34, 14, 30, 22, 12, 24, 14))
    ws.auto_filter.ref = ws.dimensions

    # 2. the open-items tracker
    ws2 = wb.create_sheet("Open items 未决项")
    header(ws2, ["No\n序号", "Category\n类别", "Item\n项目", "What is open\n待办内容", "Responsible\n责任方", "Status\n状态",
                 "Evidence / note\n证据 / 备注", "Source\n来源"])
    for it in items:
        if not it["open"]:
            continue
        ws2.append([it["no"], _label(C.CATEGORIES, it["category"]), _both(it["item"]), _both(it["requirement"]),
                    _label(C.RESPONSIBLE_LABELS, it["responsible"]), _label(C.STATUS_LABELS, it["status"]),
                    _evidence_text(it.get("evidence")), it["source"]])
        ws2.cell(row=ws2.max_row, column=6).fill = PatternFill("solid", fgColor=_FILL[it["status"]])
    finish(ws2, (6, 24, 36, 60, 14, 20, 30, 24))
    ws2.auto_filter.ref = ws2.dimensions

    # 3. legend and statistics
    ws3 = wb.create_sheet("Legend & stats 说明与统计")
    title = _both(production_title) if production_title else "%s / %s" % (data["die"], data["config"])
    ws3.append(["Production / 生产", "%s (%s / %s)" % (title, data["die"], data["config"])])
    ws3.append(["Revision / 版本", "%s%s" % (rev["number"], " (%s)" % rev["date"] if rev.get("date") else "")])
    ws3.append(["Generated / 生成时间", generated_at or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")])
    ws3.append(["Note / 说明", "Generated from the production module. Change requirements and statuses in the system, not in this file. "
                              "本表由生产模块生成；要求和状态只在系统中修改，不在此文件中修改。"])
    for row in ws3.iter_rows():
        row[0].font = Font(bold=True)
    ws3.append([])
    header(ws3, ["Status / 状态", "Items / 条数", "Share / 占比"])
    total = st["total"] or 1
    for s in C.STATUSES:
        ws3.append([_label(C.STATUS_LABELS, s), st["by_status"][s], "%.1f %%" % (100.0 * st["by_status"][s] / total)])
        ws3.cell(row=ws3.max_row, column=1).fill = PatternFill("solid", fgColor=_FILL[s])
    ws3.append(["Total / 合计", st["total"], ""])
    ws3.append(["Open items / 未决项", st["open"], "%.1f %%" % (100.0 * st["open"] / total)])
    for cell in ws3[ws3.max_row - 1] + ws3[ws3.max_row]:
        cell.font = Font(bold=True)
    ws3.append([])
    header(ws3, ["Category / 类别", "Items / 条数", "Open / 未决"])
    for k in C.CATEGORIES:
        c = st["by_category"][k]
        ws3.append([_label(C.CATEGORIES, k), c["total"], c["open"]])
    ws3.column_dimensions["A"].width = 34
    ws3.column_dimensions["B"].width = 60
    ws3.column_dimensions["C"].width = 16
    for row in ws3.iter_rows():
        for cell in row:
            cell.alignment = wrap
    force_text_cells(ws3)

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
