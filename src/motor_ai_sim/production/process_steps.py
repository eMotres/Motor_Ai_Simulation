"""The ASSEMBLY PROCESS of a production as a graph of steps (owner 2026-10-17: the Assembly tab shows the process).

A step: ``{id, n, title (bilingual), kind: operation | check, parts: [part ids or canonical part-name prefixes],
after: [step ids], note (bilingual or None)}``.  The default for a CIANO14-family propeller motor is the owner's list of
2026-10-17; editors change it, every member reads it.  Stored in ``assembly_process.json``."""
from __future__ import annotations

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
