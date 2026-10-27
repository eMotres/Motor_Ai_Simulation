"""Plain-language lines for the activity digests (owner 2026-10-27: «нужно немного подробнее, что было сделано»).

Pure functions, no file or network access.  Every function returns BOTH languages as ``(en, zh)`` so the Chinese mirror of a
bilingual digest says exactly what the English line says.  The reader is a customer or a factory person, so

* numbers are written in plain notation with a no-break space as the thousands separator (``1 770 N·m``, never ``1.77e+03``);
* a result says what the run ANSWERED: holds continuously / settles at / reaches a limit after a time, and the torque target
  against what was achieved;
* a line stays one line.
"""
from __future__ import annotations

import math
import re
from typing import Any, Iterable, Optional

NB = " "            # thousands separator: a no-break space, so "1 770" never wraps in the middle


# -- numbers and times -----------------------------------------------------------------------------------------------------------

def num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def fmt_num(v: Any, decimals: Optional[int] = None) -> str:
    """``1770.0 -> "1 770"``, ``95.2 -> "95.2"``, ``4.123 -> "4.12"``.  Plain notation, no exponent, no trailing zeros.

    Default precision: whole numbers from 100 up, one decimal from 10, two below."""
    f = num(v)
    if f is None:
        return ""
    if decimals is None:
        a = abs(f)
        decimals = 0 if a >= 99.95 else 1 if a >= 9.995 else 2
    s = f"{round(f, decimals):,.{decimals}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    if s in ("-0", ""):
        s = "0"
    return s.replace(",", NB)


def fmt_temp(v: Any) -> str:
    f = num(v)
    return "" if f is None else f"{fmt_num(f, 0)} °C"


def fmt_power(watts: Any) -> str:
    f = num(watts)
    if f is None:
        return ""
    return f"{fmt_num(f / 1000.0)} kW" if abs(f) >= 1000.0 else f"{fmt_num(f, 0)} W"


def secs(v: Any, lang: str = "en") -> str:
    """``4.2 s``, ``33 s``, ``2 min 17 s``, ``1 h 05 min`` (zh: ``2 分 17 秒``)."""
    f = num(v)
    if f is None or f < 0:
        return ""
    zh = lang == "zh"
    if f < 10.0:
        return f"{fmt_num(f, 1)} {'秒' if zh else 's'}"
    total = int(round(f))
    if total < 60:
        return f"{total} {'秒' if zh else 's'}"
    if total < 3600:
        m, s = divmod(total, 60)
        if zh:
            return f"{m} 分" + (f" {s} 秒" if s else "")
        return f"{m} min" + (f" {s} s" if s else "")
    h, rest = divmod(total, 3600)
    m = rest // 60
    return f"{h} 小时" + (f" {m:02d} 分" if m else "") if zh else f"{h} h" + (f" {m:02d} min" if m else "")


def plural(n: int, one: str, many: Optional[str] = None) -> str:
    return one if n == 1 else (many or one + "s")


# -- parts of a machine ----------------------------------------------------------------------------------------------------------

PART_ZH = {"winding": "绕组", "magnet": "磁钢", "magnets": "磁钢", "stator": "定子", "rotor": "转子", "bearing": "轴承",
           "bearings": "轴承", "housing": "机壳", "shaft": "轴", "sleeve": "护套"}
PART_EN = {"magnets": "magnet"}


def part_en(part: Any) -> str:
    p = str(part or "").strip().lower()
    return PART_EN.get(p, p)


def part_zh(part: Any) -> str:
    p = str(part or "").strip().lower()
    return PART_ZH.get(p, p)


# -- the numbers a run keeps -------------------------------------------------------------------------------------------------------

def _d(x: Any) -> dict:
    return x if isinstance(x, dict) else {}


def _first(sources: Iterable[dict], keys: Iterable[str]) -> Optional[float]:
    keys = tuple(keys)
    for src in sources:
        for k in keys:
            v = num(src.get(k))
            if v is not None:
                return v
    return None


def mechanical_numbers(kinds: dict) -> dict:
    """``{T, P, eta}``: torque N·m, mechanical power W, efficiency % - the first record that has them."""
    em = _d(_d(kinds.get("em_run")).get("numbers"))
    coupled, climit = _d(kinds.get("coupled")), _d(kinds.get("coupled_limit"))
    src = (em, _d(coupled.get("em")), _d(climit.get("em")))
    eta = _first(src, ("efficiency_pct",))
    if eta is None:
        for rec in (coupled, climit):
            e2 = num(rec.get("efficiency_shaft"))
            if e2 is not None:
                eta = e2 * 100.0 if e2 <= 1.5 else e2
                break
    return {"T": _first(src, ("T_avg_Nm", "T_em_avg_Nm", "torque_nm")), "P": _first(src, ("P_mech_W",)), "eta": eta}


_RATED_NAME = re.compile(r"""rated duty (['"])(.+?)\1(?:'s| own|\s)""")


def rated_duty_name(rec: dict) -> str:
    """The duty the warm start of a time-to-limit answer came from (``starts.rated.start_source`` names it), else ``""``."""
    src = str(_d(_d(_d(rec.get("time_to_limit")).get("starts")).get("rated")).get("start_source") or "")
    m = _RATED_NAME.search(src)
    return m.group(2).strip()[:40] if m else ""


def limit_facts(rec: dict) -> Optional[dict]:
    """``{part, limit_c, t_cold, t_rated, rated}`` when the record says the point runs out at a limit, else ``None``."""
    limited = _d(rec.get("limited"))
    if str(rec.get("mode") or "") == "limited" and limited and num(limited.get("t_cold_s")) is not None:
        return {"part": part_en(limited.get("part")), "limit_c": num(limited.get("limit_c")),
                "t_cold": num(limited.get("t_cold_s")), "t_rated": num(limited.get("t_rated_s")), "rated": rated_duty_name(rec)}
    try:
        from motor_ai_sim.duty_class import short_time_info
        st = short_time_info(rec)
    except Exception:                                        # noqa: BLE001 - a record we cannot classify just has no limit sentence
        st = None
    if st and st.get("time_to_limit_s") is not None:
        return {"part": part_en(st.get("part")), "limit_c": st.get("limit_c"), "t_cold": st.get("time_to_limit_s"),
                "t_rated": st.get("time_from_rated_s"), "rated": rated_duty_name(rec)}
    return None


def _is_continuous(rec: dict) -> bool:
    ttl = _d(rec.get("time_to_limit"))
    if ttl.get("within_limits") is True:
        return True
    try:
        from motor_ai_sim.duty_class import CONTINUOUS, classify
        return (classify(None, rec) or {}).get("duty_class") == CONTINUOUS
    except Exception:                                        # noqa: BLE001
        return False


def _temps(rec: dict) -> tuple:
    return num(rec.get("coil_temp_c")), num(rec.get("magnet_temp_c"))


def _temp_pair(coil: Optional[float], mag: Optional[float]) -> tuple:
    en, zh = [], []
    if coil is not None:
        en.append(f"winding {fmt_temp(coil)}")
        zh.append(f"绕组 {fmt_temp(coil)}")
    if mag is not None:
        en.append(f"magnets {fmt_temp(mag)}")
        zh.append(f"磁钢 {fmt_temp(mag)}")
    return ", ".join(en), "，".join(zh)


def thermal_statement(rec: dict) -> Optional[tuple]:
    """What the coupled (thermal + electromagnetic) record answers, as ``(kind, en, zh)``.

    kind ``limit`` (the point runs out: time to the limit), ``continuous`` (holds for ever inside every limit), ``steady``
    (settles at these temperatures), ``runaway`` or ``open`` (not settled)."""
    if not rec:
        return None
    coil, mag = _temps(rec)
    lim = limit_facts(rec)
    if lim and lim["t_cold"] is not None:
        part = lim["part"] or "limiting part"
        limit = f" ({fmt_temp(lim['limit_c'])})" if lim["limit_c"] is not None else ""
        en = f"reaches the {part} limit{limit} after {secs(lim['t_cold'])} from cold"
        zh = f"{part_zh(part) or '限制部件'}达到限值{limit}：冷态起 {secs(lim['t_cold'], 'zh')}"
        if lim["t_rated"] is not None:
            if lim["rated"]:
                en += f", {secs(lim['t_rated'])} after {lim['rated']}"
                zh += f"，{lim['rated']} 之后 {secs(lim['t_rated'], 'zh')}"
            else:
                en += f", {secs(lim['t_rated'])} from the rated state"
                zh += f"，额定状态起 {secs(lim['t_rated'], 'zh')}"
        other = mag if part == "winding" else None
        if other is not None:
            en += f"; magnets {fmt_temp(other)} then"
            zh += f"；此时磁钢 {fmt_temp(other)}"
        return "limit", en, zh
    if rec.get("runaway"):
        return "runaway", "no steady state: the temperatures keep rising", "无稳态：温度持续上升"
    t_en, t_zh = _temp_pair(coil, mag)
    if not t_en:
        return None
    if rec.get("converged") is False:
        return "open", f"not settled yet: {t_en}", f"尚未稳定：{t_zh}"
    if _is_continuous(rec):
        return "continuous", f"holds continuously: {t_en}", f"可连续运行：{t_zh}"
    return "steady", f"steady state: {t_en}", f"稳态：{t_zh}"


def hold_statement(rec: dict) -> Optional[tuple]:
    """The duty's torque target against what the coupled run achieved (``hold_torque``), as ``(en, zh)``."""
    ht = _d(rec.get("hold_torque"))
    if not ht:
        return None
    target, got, cur = num(ht.get("target_nm")), num(ht.get("torque_nm")), num(ht.get("I_phase_rms_A"))
    if str(ht.get("basis") or "") == "held_torque" and target is not None:
        at_en = f" at {fmt_num(cur)} A" if cur is not None else ""
        at_zh = f"（{fmt_num(cur)} A）" if cur is not None else ""
        if ht.get("held"):
            return f"torque target {fmt_num(target)} N·m held{at_en}", f"转矩目标 {fmt_num(target)} N·m 已保持{at_zh}"
        reached = f", reached {fmt_num(got)} N·m" if got is not None else ""
        reached_zh = f"，实际 {fmt_num(got)} N·m" if got is not None else ""
        return f"torque target {fmt_num(target)} N·m not reached{reached}", f"转矩目标 {fmt_num(target)} N·m 未达到{reached_zh}"
    if target is not None and cur is not None:
        return (f"torque target {fmt_num(target)} N·m, current fixed at {fmt_num(cur)} A",
                f"转矩目标 {fmt_num(target)} N·m，电流固定为 {fmt_num(cur)} A")
    return None


KIND_WORDS = {"em_run": ("electromagnetic", "电磁"), "coupled": ("thermal", "热"), "coupled_limit": ("thermal", "热"),
              "thermal": ("thermal", "热"), "duty_cycle": ("duty cycle", "工作循环"), "rotor_stress": ("rotor strength", "转子强度"),
              "modes": ("rotor modes", "转子模态"), "critical_speeds": ("critical speeds", "临界转速"),
              "pwm": ("inverter", "逆变器"), "controller": ("inverter", "逆变器")}


def result_line(duty: str, kinds: dict) -> tuple:
    """``(en, zh)``: one line about one duty's fresh results, without the variant name (the caller puts that in a heading).

    ``kinds`` maps result kind -> stored record (only the records that are new in the period)."""
    duty = str(duty)
    mech = mechanical_numbers(kinds)
    first: list = []
    first_zh: list = []
    if mech["T"] is not None:
        first.append(f"{fmt_num(mech['T'])} N·m" + (f" at {fmt_power(mech['P'])}" if mech["P"] is not None else ""))
        first_zh.append(f"{fmt_num(mech['T'])} N·m" + (f"，{fmt_power(mech['P'])}" if mech["P"] is not None else ""))
    elif mech["P"] is not None:
        first.append(fmt_power(mech["P"]))
        first_zh.append(fmt_power(mech["P"]))
    if mech["eta"] is not None:
        first.append(f"efficiency {fmt_num(mech['eta'])} %")
        first_zh.append(f"效率 {fmt_num(mech['eta'])} %")
    parts_en = [", ".join(first)] if first else []
    parts_zh = ["，".join(first_zh)] if first_zh else []

    rec = None
    for k in ("coupled", "coupled_limit"):                    # a limit answer is the more telling one: it wins over a steady one
        r = _d(kinds.get(k))
        st = thermal_statement(r)
        if st and (rec is None or (st[0] == "limit" and rec[0] != "limit")):
            rec = (st[0], st[1], st[2], r)
    if rec is not None:
        parts_en.append(rec[1])
        parts_zh.append(rec[2])
        hold = hold_statement(rec[3])
        if hold:
            parts_en.append(hold[0])
            parts_zh.append(hold[1])
    else:
        th = _d(kinds.get("thermal"))
        hot = [m for c in _d(th.get("components")).values() if isinstance(c, dict) for m in [num(c.get("max"))] if m is not None]
        if hot:
            parts_en.append(f"thermal map: hottest part {fmt_temp(max(hot))}")
            parts_zh.append(f"温度场：最热部件 {fmt_temp(max(hot))}")
    sf = num(_d(kinds.get("rotor_stress")).get("sf_min"))
    if sf is not None:
        parts_en.append(f"rotor safety factor {fmt_num(sf, 2)}")
        parts_zh.append(f"转子安全系数 {fmt_num(sf, 2)}")
    if not parts_en:
        names = [KIND_WORDS.get(k, (k, k)) for k in sorted(kinds)]
        en_list = sorted({n[0] for n in names})
        zh_list = sorted({n[1] for n in names})
        parts_en.append("results stored" + (f" ({', '.join(en_list)})" if en_list else ""))
        parts_zh.append("已保存计算结果" + (f"（{'、'.join(zh_list)}）" if zh_list else ""))
    return f"{duty}: " + "; ".join(parts_en), f"{duty}：" + "；".join(parts_zh)


def headline(kinds: dict) -> str:
    """The same numbers as one comma-separated clause (the owner report's own lines): torque, power, efficiency, temperatures."""
    mech = mechanical_numbers(kinds)
    parts: list = []
    if mech["T"] is not None:
        parts.append(f"T {fmt_num(mech['T'])} N·m")
    if mech["P"] is not None:
        parts.append(f"P {fmt_num(mech['P'] / 1000.0)} kW")
    if mech["eta"] is not None:
        parts.append(f"η {fmt_num(mech['eta'])} %")
    coil, mag = _temps(_d(kinds.get("coupled")))
    if coil is not None:
        parts.append(f"winding {fmt_temp(coil)}")
    if mag is not None:
        parts.append(f"magnet {fmt_temp(mag)}")
    if coil is None:
        th = _d(kinds.get("thermal"))
        hot = [m for c in _d(th.get("components")).values() if isinstance(c, dict) for m in [num(c.get("max"))] if m is not None]
        if hot:
            parts.append(f"hottest part {fmt_temp(max(hot))}")
    sf = num(_d(kinds.get("rotor_stress")).get("sf_min"))
    if sf is not None:
        parts.append(f"rotor SF {fmt_num(sf, 2)}")
    return ", ".join(parts)


# -- production: parts and documents -----------------------------------------------------------------------------------------------

def name_list(names: list, cap: int = 4) -> tuple:
    """``("A, B, C +2 more", "A、B、C 另有 2 项")`` - names are ``(en, zh)`` pairs, duplicates dropped, capped."""
    seen: set = set()
    uniq = []
    for en, zh in names:
        if (en, zh) not in seen:
            seen.add((en, zh))
            uniq.append((en, zh))
    shown, more = uniq[:cap], max(0, len(uniq) - cap)
    en = ", ".join(n[0] for n in shown) + (f" +{more} more" if more else "")
    zh = "、".join(n[1] for n in shown) + (f" 另有 {more} 项" if more else "")
    return en, zh


PART_FIELD_WORDS = {"material": ("material", "材料"), "spec": ("specification", "规格"), "name": ("name", "名称"),
                    "description": ("description", "描述"), "qty_per_motor": ("quantity", "数量"), "kind": ("type", "类型"),
                    "parent_id": ("assembly", "所属总成"), "drawing_files": ("drawings", "图纸"), "optional_step": ("STEP file", "STEP 文件")}

DOC_CATEGORIES = (("photo", ("jpg", "jpeg", "png", "gif", "webp", "heic", "bmp", "tif", "tiff")),
                  ("step", ("step", "stp")),
                  ("drawing", ("pdf", "dxf", "dwg")))
DOC_WORDS = {"photo": (("photo", "photos"), "张照片"), "step": (("STEP file", "STEP files"), "个 STEP 文件"),
             "drawing": (("drawing", "drawings"), "份图纸"), "file": (("file", "files"), "个文件")}


def doc_category(name: Any) -> str:
    ext = str(name or "").rsplit(".", 1)[-1].lower() if "." in str(name or "") else ""
    for cat, exts in DOC_CATEGORIES:
        if ext in exts:
            return cat
    return "file"


def doc_counts(counts: dict) -> tuple:
    """``{"photo": 2, "step": 5}`` -> ``("2 photos, 5 STEP files", "2 张照片，5 个 STEP 文件")`` in a fixed order."""
    en, zh = [], []
    for cat in ("photo", "step", "drawing", "file"):
        n = int(counts.get(cat) or 0)
        if n:
            (one, many), zh_w = DOC_WORDS[cat]
            en.append(f"{n} {one if n == 1 else many}")
            zh.append(f"{n} {zh_w}")
    return ", ".join(en), "，".join(zh)
