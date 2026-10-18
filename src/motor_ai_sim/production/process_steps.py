"""The ASSEMBLY PROCESS of a production as a graph of steps (owner 2026-10-17: the Assembly tab shows the process).

A step: ``{id, n, title (bilingual), kind: operation | check, parts: [part ids or canonical part-name prefixes],
after: [step ids], note (bilingual or None)}``.  The default for a CIANO14-family propeller motor is the owner's list of
2026-10-17; editors change it, every member reads it.  Stored in ``assembly_process.json``."""
from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Optional

from motor_ai_sim.production import models


def _bi(en: str, zh: str) -> dict:
    return {"en": en, "zh": zh, "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}}


#: (id, title en, title zh, kind, part-name prefixes, after, note en, note zh) - the owner's list, in his order
_DEFAULT = [
    ("s1", "Press the coils", "线圈压制成型", "operation", ["Coil 40"], [], "", ""),
    ("s2", "Insert the coils with slot insulation into the slots", "带槽绝缘的线圈嵌入定子槽", "operation",
     ["Coil 40", "Slot insulation", "Stator core"], ["s1"], "", ""),
    ("s3", "Breakdown (hipot) test of every coil", "每个线圈耐压（击穿）测试", "check", [], ["s2"], "", ""),
    ("s4", "Insert the coil-fixing rings on both sides", "两侧装入线圈固定环", "operation", ["Coil-fixing ring"], ["s3"], "", ""),
    ("s5", "Insert the magnets into the rotor", "磁钢插入转子", "operation", ["Magnet", "Rotor core"], [], "", ""),
    ("s6", "Check the magnet insertion with magnetic viewing film", "用磁性观察片检查磁钢插装", "check", [], ["s5"],
     "The stripes must run in the axial direction.", "条纹应沿轴向。"),
    ("s7", "Press the shaft into the rotor", "轴压入转子", "operation", ["Shaft"], ["s6"], "", ""),
    ("s8", "Balance the rotor", "转子动平衡", "check", [], ["s7"], "", ""),
    ("s9", "Assemble the motor", "电机总装", "operation",
     ["Front holder", "Back holder", "Bearing", "Socket head screw", "Prop_fix"], ["s4", "s8"], "", ""),
    ("s10", "Solder the 3 phase lead wires to the phase board", "三根相线焊接到相板", "operation",
     ["Lead wire", "Phase board"], ["s9"], "", ""),
    ("s11", "Solder the phase board to the stator coils", "相板焊接到定子线圈", "operation", ["Phase board"], ["s10"], "", ""),
    ("s12", "Check the resistance of the three phases", "三相电阻检测", "check", [], ["s11"],
     "4-wire meter at the lead ends, 20 °C (values in the process section).", "四线制毫欧表，在引线末端测量，20 °C（数值见工艺章节）。"),
    ("s13", "Run the motor with an ESC", "用电调（ESC）试运行", "check", [], ["s12"], "", ""),
]

#: The default for an ELECTRONICS production (power converter: power board, control board, DC-link board, mechanics):
#: three board branches meet at the mechanical integration, then the tests.  Same tuple layout as _DEFAULT.
_DEFAULT_ELECTRONICS = [
    ("e1", "Incoming inspection of bare boards and components", "PCB 与元器件来料检验", "check", ["Power PCB", "Control PCB", "DC-link PCB"], [], "Bare-board electrical test result (IPC-D-356 netlist) from the fabricator.", "制板厂提供的光板电测报告（IPC-D-356 网表）。"),
    ("e2", "Power board: solder paste, SMT placement, reflow", "功率板：印锡、贴片、回流焊", "operation", ["Power PCB"], ["e1"], "", ""),
    ("e3", "Power board: AOI and X-ray of the power devices", "功率板：AOI 与功率器件 X 光检测", "check", [], ["e2"], "", ""),
    ("e4", "Power board: press-fit terminals and through-hole parts", "功率板：压接端子与插件", "operation", ["Press-fit terminal"], ["e3"], "", ""),
    ("e5", "Power board: cleaning and visual inspection", "功率板：清洗与目检", "check", [], ["e4"], "", ""),
    ("c1", "Control board: SMT assembly and AOI", "控制板：贴片组装与 AOI", "operation", ["Control PCB"], ["e1"], "", ""),
    ("c2", "Control board: firmware programming", "控制板：烧录固件", "operation", [], ["c1"], "", ""),
    ("d1", "DC-link capacitor board assembly", "直流母线电容板组装", "operation", ["DC-link PCB"], ["e1"], "", ""),
    ("m1", "Mechanical integration: boards, busbars, coldplate, harness", "机械总装：电路板、母排、冷板、线束", "operation", ["Busbar", "Coldplate", "Enclosure", "Harness", "Screw"], ["e5", "c2", "d1"], "Torques per the assembly drawing.", "拧紧力矩按装配图。"),
    ("m2", "Conformal coating", "三防漆涂覆", "operation", [], ["m1"], "", ""),
    ("t1", "Insulation (hipot) test", "绝缘耐压测试", "check", [], ["m2"], "", ""),
    ("t2", "Low-voltage functional test", "低压功能测试", "check", [], ["t1"], "", ""),
    ("t3", "Double-pulse test of every phase leg", "各桥臂双脉冲测试", "check", [], ["t2"], "", ""),
    ("t4", "Full-power run and thermal check", "满功率运行与温升检查", "check", [], ["t3"], "", ""),
    ("t5", "Final inspection and packing", "终检与包装", "operation", [], ["t4"], "", ""),
]

KINDS = ("operation", "check")


def default_steps(product_type: str = "motor") -> List[dict]:
    out = []
    table = _DEFAULT_ELECTRONICS if product_type == "electronics" else _DEFAULT
    for i, (sid, en, zh, kind, parts, after, nen, nzh) in enumerate(table, 1):
        out.append({"id": sid, "n": i, "title": _bi(en, zh), "kind": kind, "parts": list(parts), "after": list(after),
                    "note": _bi(nen, nzh) if (nen or nzh) else None})
    return out


def normalize_step(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("a step must be an object")
    sid = str(raw.get("id") or "").strip()[:40]
    if not sid or not all(c.isalnum() or c in "-_" for c in sid):
        raise ValueError("step id must be letters, digits, - or _")
    kind = str(raw.get("kind") or "operation")
    if kind not in KINDS:
        raise ValueError("step kind must be operation or check")
    title = models.normalize_bilingual(raw.get("title") or {})
    if not (title.get("en") or title.get("zh")):
        raise ValueError("a step needs a title")
    note = raw.get("note")
    return {"id": sid, "n": int(raw.get("n") or 0), "title": title, "kind": kind,
            "parts": [str(p)[:120] for p in (raw.get("parts") or []) if str(p).strip()][:40],
            "after": [str(a)[:40] for a in (raw.get("after") or []) if str(a).strip()][:20],
            # the process sections of this step, by id (owner 2026-10-18); empty = matched by the title keywords
            "sections": [str(x)[:60] for x in (raw.get("sections") or []) if str(x).strip()][:40],
            "note": models.normalize_bilingual(note) if isinstance(note, dict) and (note.get("en") or note.get("zh")) else None}


def validate_steps(raw_steps: Any) -> List[dict]:
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError("steps must be a non-empty list")
    steps = [normalize_step(s) for s in raw_steps][:200]
    ids = [s["id"] for s in steps]
    if len(set(ids)) != len(ids):
        raise ValueError("step ids must be unique")
    known = set(ids)
    for s in steps:
        bad = [a for a in s["after"] if a not in known or a == s["id"]]
        if bad:
            raise ValueError("step %s follows unknown steps %s" % (s["id"], bad))
    indeg = {s["id"]: len(s["after"]) for s in steps}
    nxt: Dict[str, List[str]] = {i: [] for i in ids}
    for s in steps:
        for a in s["after"]:
            nxt[a].append(s["id"])
    queue = [i for i, d in indeg.items() if d == 0]
    seen = 0
    while queue:
        i = queue.pop()
        seen += 1
        for j in nxt[i]:
            indeg[j] -= 1
            if indeg[j] == 0:
                queue.append(j)
    if seen != len(steps):
        raise ValueError("the steps form a cycle")
    return steps


def resolve_parts(step: dict, parts: List[dict]) -> List[dict]:
    """The production parts a step uses: by id, or by canonical English name prefix."""
    out: List[dict] = []
    for ref in step.get("parts") or []:
        for p in parts:
            en = ((p.get("name") or {}).get("en") or "")
            if (p.get("id") == ref or en.startswith(ref)) and p.get("kind") != "assembly" and p not in out:
                out.append(p)
    return [{"id": p["id"], "name": p.get("name")} for p in out]


def view(doc: Optional[dict], parts: List[dict], product_type: str = "motor") -> dict:
    steps = (doc or {}).get("steps") or default_steps(product_type)
    return {"default": not (doc or {}).get("steps"),
            "steps": [dict(s, parts_resolved=resolve_parts(s, parts)) for s in steps]}


# ── the process document, generated from the steps (owner 2026-10-18) ──────────────────────────────────────────────
# One document over every assembly and test step, in the graph's order and numbering: step n, its parts and note, then
# the process sections that belong to it numbered n.1, n.2 ...  Sections no step names come last ("General").

#: the default steps' keywords matched against the section titles (a step may name its sections itself: ``sections``)
STEP_KEYWORDS: Dict[str, List[str]] = {
    "s1": ["winding", "coil"], "s2": ["winding", "insulation"], "s3": ["pre-power", "inspection"], "s4": ["ring"],
    "s5": ["magnet"], "s6": ["magnet"], "s7": ["shaft", "fits"], "s8": ["balance"],
    "s9": ["assembly sequence", "bearing", "fits", "bracket"], "s10": ["phase plate", "wiring"],
    "s11": ["phase plate", "wiring"], "s12": ["pre-power", "winding"], "s13": ["pre-power"],
}

_NUM_PREFIX = re.compile(r"^\s*(\d+[a-z]?|[A-Z])[.)]\s+")


def related_sections(step: dict, sections: List[dict]) -> List[dict]:
    """The process sections of one step (the same rule as the Assembly tab's step panel)."""
    own = step.get("sections")
    if own:
        return [s for s in sections if s.get("id") in own]
    keys = STEP_KEYWORDS.get(str(step.get("id")), [])
    if not keys:
        return []
    out = []
    for s in sections:
        t = ("%s %s" % ((s.get("title") or {}).get("en") or "", (s.get("title") or {}).get("zh") or "")).lower()
        if any(re.search(r"\b%s\b" % re.escape(k), t) for k in keys):   # whole words: "ring" is not in "bearing"
            out.append(s)
    return out


def strip_number(text: str) -> str:
    """A section title without its own old numbering ("6a. Winding" -> "Winding"): the step gives the number."""
    return _NUM_PREFIX.sub("", text or "", count=1)


def step_number(step: dict, index: int) -> int:
    return int(step.get("n") or 0) or index + 1


def _txt(b: Any, lang: str) -> str:
    if not isinstance(b, dict):
        return str(b or "")
    other = "zh" if lang == "en" else "en"
    return str(b.get(lang) or b.get(other) or "")


_LABELS = {
    "en": {"title": "Assembly and test process", "parts": "Parts", "note": "Note", "operation": "Operation",
           "check": "Check", "general": "General", "same": "see %s", "revision": "Revision", "generated":
           "Generated from the production's assembly steps and process sections; the steps' numbers are the graph's.",
           "contents": "Contents"},
    "zh": {"title": "装配与测试工艺", "parts": "零件", "note": "备注", "operation": "工序", "check": "检验",
           "general": "通用", "same": "见 %s", "revision": "版本", "generated": "由生产模块的装配步骤和工艺章节生成；步骤编号与流程图一致。",
           "contents": "目录"},
}


def document_html(steps: List[dict], sections: List[dict], title: str, revision: Any, lang: str = "en") -> str:
    """The process document as one self-contained HTML page (view in the browser, print to PDF, download)."""
    lang = "zh" if lang == "zh" else "en"
    L = _LABELS[lang]
    e = html.escape
    order = sorted(enumerate(steps), key=lambda p: (step_number(p[1], p[0]), p[0]))
    first: Dict[str, str] = {}                    # section id -> the number it was printed under first
    toc: List[str] = []
    body: List[str] = []

    def section_html(num: str, s: dict) -> str:
        out = ['<h3 id="sec-%s">%s %s</h3>' % (e(num), e(num), e(strip_number(_txt(s.get("title"), lang))))]
        text = _txt(s.get("body"), lang).strip()
        if text:
            out.append("".join("<p>%s</p>" % e(par).replace("\n", "<br>") for par in text.split("\n\n")))
        ti = s.get("tables_i18n") or {}
        tables = ti.get(lang) or s.get("tables") or []
        for tb in tables:
            if not isinstance(tb, list) or not tb:
                continue
            rows = "".join("<tr>%s</tr>" % "".join(("<th>%s</th>" if i == 0 else "<td>%s</td>") % e(str(c))
                                                     for c in (r if isinstance(r, list) else [r]))
                           for i, r in enumerate(tb))
            out.append("<table>%s</table>" % rows)
        return "".join(out)

    for idx, st in order:
        n = step_number(st, idx)
        kind = L["check"] if st.get("kind") == "check" else L["operation"]
        name = _txt(st.get("title"), lang)
        toc.append('<li><a href="#step-%d">%d. %s</a></li>' % (n, n, e(name)))
        body.append('<h2 id="step-%d">%d. %s <span class="kind">%s</span></h2>' % (n, n, e(name), e(kind)))
        parts = [_txt(p.get("name"), lang) for p in st.get("parts_resolved") or []]
        if parts:
            body.append('<p class="parts"><b>%s:</b> %s</p>' % (e(L["parts"]), e(", ".join(parts))))
        note = _txt(st.get("note"), lang).strip() if st.get("note") else ""
        if note:
            body.append('<p class="note"><b>%s:</b> %s</p>' % (e(L["note"]), e(note)))
        for k, s in enumerate(related_sections(st, sections), start=1):
            num = "%d.%d" % (n, k)
            sid = str(s.get("id"))
            if sid in first:
                body.append('<p class="ref">%s %s - <a href="#sec-%s">%s</a></p>' % (
                    e(num), e(strip_number(_txt(s.get("title"), lang))), e(first[sid]), e(L["same"] % first[sid])))
                continue
            first[sid] = num
            body.append(section_html(num, s))
    rest = [s for s in sections if str(s.get("id")) not in first]
    if rest:
        g = (max((step_number(st, i) for i, st in enumerate(steps)), default=0) + 1)
        toc.append('<li><a href="#step-%d">%d. %s</a></li>' % (g, g, e(L["general"])))
        body.append('<h2 id="step-%d">%d. %s</h2>' % (g, g, e(L["general"])))
        for k, s in enumerate(rest, start=1):
            body.append(section_html("%d.%d" % (g, k), s))
    head = "%s - %s" % (title, L["title"])
    return ("<!doctype html><html lang=\"%s\"><head><meta charset=\"utf-8\"><title>%s</title>"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><style>"
            "body{font:14px/1.5 system-ui,-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;max-width:900px;"
            "margin:24px auto;padding:0 16px;color:#111}h1{font-size:22px;margin-bottom:2px}"
            "h2{font-size:17px;margin-top:28px;border-bottom:1px solid #ddd;padding-bottom:3px}"
            "h3{font-size:14.5px;margin:16px 0 4px}.kind{font-size:11px;font-weight:600;color:#555;background:#eef;"
            "border-radius:9px;padding:1px 8px;margin-left:6px;vertical-align:middle}.meta{color:#555;font-size:12px}"
            "table{border-collapse:collapse;margin:6px 0 10px;font-size:13px}td,th{border:1px solid #ccc;"
            "padding:3px 7px;text-align:left;vertical-align:top}th{background:#f3f4f6}.ref{color:#555}"
            "@media print{a{color:inherit;text-decoration:none}h2{break-after:avoid}}</style></head><body>"
            "<h1>%s</h1><p class=\"meta\">%s %s. %s</p><h2>%s</h2><ol class=\"toc\" style=\"list-style:none;"
            "padding-left:0\">%s</ol>%s</body></html>") % (
        lang, e(head), e(head), e(L["revision"]), e(str(revision if revision is not None else "-")),
        e(L["generated"]), e(L["contents"]), "".join(toc), "".join(body))
