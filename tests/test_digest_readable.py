"""Readable digest lines (owner 2026-10-27: «нужно немного подробнее, что было сделано, чтобы было понятно»).

Pure number / time formatting, the sentence a duty's results make, results grouped under their variant, and the production side:
parts named with what changed, documents grouped by part.  Neutral fixtures only (no customer names).
"""
from __future__ import annotations

import re

from tests.test_comm_prefs import ANNA, LI, env  # noqa: F401
from tests.test_digest import CFG, DIE, PID, T0, XSCOPE, PSCOPE, at, d, draft_of, run  # noqa: F401

from motor_ai_sim.digest import collect as C
from motor_ai_sim.digest import readable as RD

NB = RD.NB


# -- numbers and times --------------------------------------------------------------------------------------------------------------

def test_numbers_are_plain_with_thousands_separators_never_an_exponent():
    assert RD.fmt_num(1770.0) == f"1{NB}770"
    assert RD.fmt_num(1.77e3) == f"1{NB}770" and "e+" not in RD.fmt_num(1.07e3)
    assert RD.fmt_num(95.2) == "95.2" and RD.fmt_num(185000 / 1000) == "185"
    assert RD.fmt_num(4.123) == "4.12" and RD.fmt_num(4.0) == "4" and RD.fmt_num(12.34) == "12.3"
    assert RD.fmt_num(1234567.4) == f"1{NB}234{NB}567" and RD.fmt_num(float("nan")) == "" and RD.fmt_num(True) == ""
    assert RD.fmt_power(185000) == "185 kW" and RD.fmt_power(400) == "400 W" and RD.fmt_power(1_250_000) == f"1{NB}250 kW"
    assert RD.fmt_temp(199.6) == "200 °C"


def test_durations_read_at_a_glance_in_both_languages():
    assert RD.secs(137) == "2 min 17 s" and RD.secs(33) == "33 s" and RD.secs(4.24) == "4.2 s" and RD.secs(120) == "2 min"
    assert RD.secs(3900) == "1 h 05 min" and RD.secs(59.6) == "1 min" and RD.secs(-1) == "" and RD.secs(None) == ""
    assert RD.secs(137, "zh") == "2 分 17 秒" and RD.secs(33, "zh") == "33 秒" and RD.secs(3900, "zh") == "1 小时 05 分"


# -- the sentence a duty's results make -------------------------------------------------------------------------------------------------

def _limit_record():
    return {"mode": "limited", "coil_temp_c": 200.0, "magnet_temp_c": 57.2, "converged": False,
            "limited": {"part": "winding", "limit_c": 200.0, "t_cold_s": 137.0, "t_rated_s": 33.0},
            "time_to_limit": {"within_limits": False, "limiting_part": "winding", "limits_c": {"winding": 200.0},
                              "starts": {"cold": {"time_to_limit_s": 137.0},
                                         "rated": {"time_to_limit_s": 33.0,
                                                   "start_source": "the rated duty 'cruise''s own stored thermal map (its current fixed)"}}}}


def test_a_short_time_duty_says_when_it_reaches_the_limit():
    kinds = {"em_run": {"numbers": {"T_avg_Nm": 1770.4, "P_mech_W": 185000.0, "efficiency_pct": 95.1}}, "coupled_limit": _limit_record()}
    en, zh = RD.result_line("peak", kinds)
    assert en == (f"peak: 1{NB}770 N·m at 185 kW, efficiency 95.1 %; reaches the winding limit (200 °C) after 2 min 17 s from cold, "
                  "33 s after cruise; magnets 57 °C then")
    assert zh.startswith("peak：1") and "绕组达到限值 (200 °C)：冷态起 2 分 17 秒，cruise 之后 33 秒" in zh and "效率 95.1 %" in zh
    assert not re.search(r"\de[+-]\d", en + zh)


def test_a_continuous_duty_says_it_holds_and_the_torque_target_is_judged():
    coupled = {"coil_temp_c": 137.0, "magnet_temp_c": 82.0, "converged": True, "time_to_limit": {"within_limits": True},
               "em": {"T_em_avg_Nm": 1066.0, "P_mech_W": 95200.0},
               "hold_torque": {"basis": "held_torque", "target_nm": 1070.0, "torque_nm": 1069.5, "I_phase_rms_A": 270.4, "held": True}}
    en, zh = RD.result_line("cruise", {"coupled": coupled})
    # the HELD torque (the coupled run's, at the real magnet temperature) is the one stated, the power scaled with it
    assert en == (f"cruise: 1{NB}070 N·m at 95.5 kW; holds continuously: winding 137 °C, magnets 82 °C; "
                  f"torque target 1{NB}070 N·m held at 270 A")
    assert "可连续运行：绕组 137 °C，磁钢 82 °C" in zh and "转矩目标" in zh and "已保持" in zh
    coupled["hold_torque"].update(held=False, torque_nm=990.0)
    assert f"torque target 1{NB}070 N·m not reached, reached 990 N·m" in RD.result_line("cruise", {"coupled": coupled})[0]


def test_other_answers_and_the_fallback():
    assert RD.result_line("a", {"coupled": {"coil_temp_c": 128.4}})[0] == "a: steady state: winding 128 °C"
    assert RD.result_line("a", {"coupled": {"runaway": True, "coil_temp_c": 900.0}})[0] == "a: no steady state: the temperatures keep rising"
    assert "not settled yet: winding 150 °C" in RD.result_line("a", {"coupled": {"converged": False, "coil_temp_c": 150.0}})[0]
    assert RD.result_line("t", {"thermal": {"components": {"winding": {"max": 199.6}}}})[0] == "t: thermal map: hottest part 200 °C"
    assert RD.result_line("m", {"rotor_stress": {"sf_min": 3.456}})[0] == "m: rotor safety factor 3.46"
    assert RD.result_line("x", {"pwm": {"computed_at": "2026-01-01T00:00:00Z"}}) == ("x: results stored (inverter)", "x：已保存计算结果（逆变器）")


# -- grouped under the variant ---------------------------------------------------------------------------------------------------------

def _many_duties(die, cfg):
    return {"cruise": {"coupled": {"computed_at": at(-1700), "coil_temp_c": 137.0, "magnet_temp_c": 82.0, "converged": True,
                                   "time_to_limit": {"within_limits": True}}},
            "peak": {"coupled_limit": dict(_limit_record(), computed_at=at(-1600)),
                     "em_run": {"computed_at": at(-1600), "numbers": {"T_avg_Nm": 1770.0, "P_mech_W": 185000.0}}},
            "landing": {"em_run": {"computed_at": at(-1500), "numbers": {"T_avg_Nm": 1190.0, "P_mech_W": 125000.0}}}} if cfg == "L12" else {}


def test_the_duties_of_one_variant_stand_under_one_heading_with_one_link(d):
    run(d, scopes=[PSCOPE], now=T0)
    from motor_ai_sim.digest import build as B
    items = C.collect_project(d["PS"].get_project(PID), T0 - 7200, T0, results=_many_duties)
    res = [i for i in items if i.kind == "results" and "L12" in i.group[0]]
    assert sorted(i.en.split(":")[0] for i in res) == ["cruise", "landing", "peak"]
    assert len({i.group for i in res}) == 1 and all("CIANO28 85" not in i.en for i in res)
    group = B.Group(PSCOPE, "project", {}, "Example", "Example", [], True, "/projects/example")
    text, html = B.compose(group, sorted(res, key=lambda i: i.ts, reverse=True), 0, T0 - 86400, T0, True)
    en_part, zh_part = text.split("-" * 40)
    assert en_part.count("New results, CIANO28 85 / L12:") == 1 and zh_part.count("新计算结果，CIANO28 85 / L12:") == 1
    assert en_part.count("/projects/erium-x1/motors?die=CIANO28%2085&config=L12") == 1             # the link once per heading, not per duty
    assert "    - peak: " in en_part and "    - landing: " in en_part and "    - cruise: holds continuously" in en_part
    assert "    - peak：" in zh_part and "可连续运行" in zh_part and html.count("<ul") == 4 and "<img" not in html


def test_results_in_the_stored_draft_keep_their_heading(d):
    d["results"] = _many_duties
    run(d, scopes=[PSCOPE])
    draft = draft_of(d, PSCOPE)
    rows = [i for i in draft["items"] if i["kind"] == "results"]
    assert rows and all(i["group_en"].startswith("New results, CIANO28 85 / L12") for i in rows if i.get("motor") == ["CIANO28 85", "L12"])
    assert "holds continuously" in draft["text_body"] and "e+0" not in draft["text_body"]


# -- production: parts ---------------------------------------------------------------------------------------------------------------------

def _part(pid, en, zh, **kw):
    return dict({"id": pid, "name": {"en": en, "zh": zh, "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}},
                 "kind": "made", "qty_per_motor": 1, "revision": "1.0"}, **kw)


def _paudit(d, action, eid, snap, when, who=ANNA):
    d["PR"].append_audit(DIE, CFG, {"time": at(when), "who": who, "action": action, "entity": "part", "entity_id": eid, "snapshot": snap})


def test_parts_changed_names_the_parts_and_what_changed(d):
    names = [("pt_stator", "Stator core", "定子铁芯"), ("pt_rotor", "Rotor sleeve", "转子护套"), ("pt_shaft", "Shaft", "轴"),
             ("pt_cover", "End cover", "端盖"), ("pt_bolt", "Bolt M4", "螺栓 M4"), ("pt_nut", "Nut M4", "螺母 M4"), ("pt_pin", "Pin", "销")]
    d["PR"].save_file(DIE, CFG, "parts.json", {"parts": [_part(*n) for n in names]})
    # history before the period: stator and rotor existed with revision 1.0 and a material
    _paudit(d, "create", "pt_stator", {"part": _part("pt_stator", "Stator core", "定子铁芯", material_i18n={"en": "M270-35A", "zh": "M270-35A"})}, -90000)
    _paudit(d, "create", "pt_rotor", {"part": _part("pt_rotor", "Rotor sleeve", "转子护套")}, -90000)
    _paudit(d, "create", "pt_shaft", {"part": _part("pt_shaft", "Shaft", "轴", spec_i18n={"en": "h6", "zh": "h6"})}, -90000)
    # in the period
    _paudit(d, "update", "pt_stator", {"patch": {"revision": "1.1", "material": "M250-35A"}}, -3000)
    _paudit(d, "update", "pt_stator", {"patch": {"revision": "1.1", "material": "M250-35A", "kind": "made"}}, -2900)          # nothing new
    _paudit(d, "update", "pt_rotor", {"patch": {"revision": "1.0"}}, -2800)                                                  # same revision: not a change
    _paudit(d, "update", "pt_shaft", {"patch": {"spec": "h5"}}, -2700)
    for pid, en, zh in names[3:]:
        _paudit(d, "create", pid, {"part": _part(pid, en, zh)}, -2500)
    _paudit(d, "delete", "pt_pin", {}, -2400)                                                                                  # made and removed: silent
    items = [i for i in C.collect_production({"die": DIE, "config": CFG}, T0 - 7200, T0) if i.kind == "parts"]
    by = {i.key: i for i in items}
    assert set(by) == {"parts:new", "parts:revised", "parts:edited"}, [i.en for i in items]
    assert by["parts:new"].en == "New parts (3): End cover, Bolt M4, Nut M4"
    assert by["parts:new"].zh == "新增零件（3）：端盖、螺栓 M4、螺母 M4"
    assert by["parts:revised"].en == "Part revised: Stator core 1.0 → 1.1 (material)"
    assert by["parts:revised"].zh == "零件已改版：定子铁芯 1.0 → 1.1（材料）"
    assert by["parts:edited"].en == "Part edited: Shaft (specification)" and "Parts changed" not in " ".join(i.en for i in items)
    assert all(not i.requires for i in items)


def test_a_long_list_of_new_parts_is_capped(d):
    d["PR"].save_file(DIE, CFG, "parts.json", {"parts": [_part(f"pt_{i}", f"Part {i}", f"零件 {i}") for i in range(19)]})
    for i in range(19):
        _paudit(d, "create", f"pt_{i}", {"part": _part(f"pt_{i}", f"Part {i}", f"零件 {i}")}, -3000 + i)
    (item,) = [i for i in C.collect_production({"die": DIE, "config": CFG}, T0 - 7200, T0) if i.kind == "parts"]
    assert item.en == "New parts (19): Part 0, Part 1, Part 2, Part 3 +15 more" and item.zh.endswith("另有 15 项") and "Parts changed" not in item.en


# -- production: documents ---------------------------------------------------------------------------------------------------------------

def test_documents_are_grouped_by_part_and_counted_by_kind(d):
    d["PR"].save_file(DIE, CFG, "parts.json", {"parts": [_part("pt_stator", "Stator assembly", "定子总成"),
                                                         _part("pt_bench", "Test-bench tooling", "试验台工装")]})
    docs = [("a1", "pt_stator", "stator-front.jpg", False), ("a2", "pt_stator", "stator-back.JPG", False),
            ("b1", "pt_bench", "plate.step", False), ("b2", "pt_bench", "plate-2.stp", False), ("b3", "pt_bench", "rail.step", False),
            ("b4", "pt_bench", "plate.pdf", False), ("b5", "pt_bench", "rail.pdf", False), ("b6", "pt_bench", "notes.txt", False),
            ("c1", "pt_stator", "supplier-quote.pdf", True), ("e1", None, "single.pdf", False)]
    world = d["PR"].load_file(DIE, CFG, "documents.json")["documents"]              # the fixture's own two uploads stay (no part)
    d["PR"].save_file(DIE, CFG, "documents.json", {"documents": world + [{"id": f"doc_{i}", "name": n, "part_id": p, "internal": internal}
                                                                          for i, p, n, internal in docs]})
    for i, p, n, internal in docs:
        d["PR"].append_audit(DIE, CFG, {"time": at(-2000), "who": ANNA, "action": "create", "entity": "document", "entity_id": f"doc_{i}",
                                        "snapshot": {"doc": {"name": n}}})
    items = [i for i in C.collect_production({"die": DIE, "config": CFG}, T0 - 7200, T0) if i.kind == "document"]
    lines = {i.en: i for i in items}
    assert "Stator assembly: 2 photos added" in lines
    assert "Test-bench tooling: 2 STEP files, 3 drawings, 1 file added" not in lines          # one STEP file is a STEP file, not a drawing
    assert "Test-bench tooling: 3 STEP files, 2 drawings, 1 file added" in lines
    assert "Stator assembly: supplier-quote.pdf added (internal)" in lines and C.INTERNAL in lines["Stator assembly: supplier-quote.pdf added (internal)"].requires
    assert "General documents: 1 photo, 1 drawing added" in lines                      # assembly-photo.jpg of the fixture + single.pdf
    assert "New document (internal): internal-cost-sheet.xlsx" in lines and len(items) == 5
    zh = {i.en: i.zh for i in items}
    assert zh["Stator assembly: 2 photos added"] == "定子总成：新增 2 张照片"
    assert zh["Test-bench tooling: 3 STEP files, 2 drawings, 1 file added"] == "试验台工装：新增 3 个 STEP 文件，2 份图纸，1 个文件"
    assert all(i.link and "documents" in i.link for i in items)


def test_the_factory_text_never_shows_an_internal_document_group(d):
    d["PR"].save_file(DIE, CFG, "parts.json", {"parts": [_part("pt_stator", "Stator assembly", "定子总成")]})
    d["PR"].save_file(DIE, CFG, "documents.json", {"documents": [
        {"id": "doc_p1", "name": "front.jpg", "part_id": "pt_stator", "internal": False},
        {"id": "doc_p2", "name": "back.jpg", "part_id": "pt_stator", "internal": False},
        {"id": "doc_i1", "name": "margin-calc.xlsx", "part_id": "pt_stator", "internal": True},
        {"id": "doc_i2", "name": "margin-calc-2.xlsx", "part_id": "pt_stator", "internal": True}]})
    for n in ("p1", "p2", "i1", "i2"):
        d["PR"].append_audit(DIE, CFG, {"time": at(-2000), "who": ANNA, "action": "create", "entity": "document", "entity_id": f"doc_{n}",
                                        "snapshot": {"doc": {"name": n}}})
    run(d, scopes=[XSCOPE])
    text = draft_of(d, XSCOPE)["text_body"]
    assert "Stator assembly: 2 photos added" in text and "定子总成：新增 2 张照片" in text
    assert "internal" not in text.lower() and "margin" not in text and "xlsx" not in text and "内部" not in text

def test_a_held_peak_states_one_torque_the_held_one():
    rec = _limit_record()
    rec["hold_torque"] = {"basis": "held_torque", "target_nm": 1500.0, "torque_nm": 1496.5, "I_phase_rms_A": 265.8, "held": True}
    kinds = {"em_run": {"numbers": {"T_avg_Nm": 1546.1, "P_mech_W": 161905.6}}, "coupled_limit": rec}
    en, _zh = RD.result_line("peak", kinds)
    assert en.startswith(f"peak: 1{NB}496 N·m at 157 kW; ") and f"1{NB}546" not in en
    assert en.endswith(f"torque target 1{NB}500 N·m held at 266 A")

def test_a_generator_states_its_output_and_efficiency_never_a_negative_torque():
    kinds = {"em_run": {"numbers": {"T_avg_Nm": -0.292, "P_mech_W": 23.9, "efficiency": 0.8741, "P_elec_in_W": -21.89}}}
    en, zh = RD.result_line("generator_780", kinds)
    assert en == "generator_780: generator 22 W out, shaft torque 0.29 N·m, efficiency 87.4 %"
    assert "-0" not in en and "发电 22 W" in zh
    motor = {"em_run": {"numbers": {"T_avg_Nm": 1021.1, "P_mech_W": 90894.2, "efficiency": 0.961}}}
    assert RD.result_line("cruise", motor)[0] == f"cruise: 1{NB}021 N·m at 90.9 kW, efficiency 96.1 %"
