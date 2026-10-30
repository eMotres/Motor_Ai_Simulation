"""One 20 °C magnet card per grade, the temperature set in Simulation (owner 2026-10-30).

«нужно сделать в каталоге магниты все с одинаковой температурой 20 °C, а в
моделировании просто вводить нужную температуру, но не больше той, для которой
они предназначены».  What this file guards:

1. **The physics did not move.**  Every former per-temperature card
   (``tests/data/magnet_cards_pre_20c.json``, the library as it stood before the
   change) is reproduced by its grade's 20 °C card derived at that temperature:
   point for point for the manufacturer-sheet families (N52UH, F52SH, F45SH),
   within the documented tolerances for N45EH (whose shape was fitted).
2. **The library is one card per grade**, quoted at 20 °C, each with its
   maximum working temperature and a curve model, the stored 20 °C curve equal
   to what the model generates.
3. **The former names still resolve** — to the derived card, under the old name.
4. **The limit**: a typed temperature above the grade's maximum is refused (the
   route guard); a computed one is flagged, never clamped.
5. **The migration** maps stored inputs to grade + temperature and keeps a
   temperature a duty already set.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from motor_ai_sim import materials as M
from motor_ai_sim.magnet_migration import (migrate_config_doc, migrate_motor_payload,
                                           temp_text)

OLD = json.loads((Path(__file__).parent / "data" / "magnet_cards_pre_20c.json")
                 .read_text(encoding="utf-8"))["cards"]
EXACT = [n for n in OLD if not n.startswith("N45EH")]
FITTED = [n for n in OLD if n.startswith("N45EH")]


def _curve(c):
    return [(float(h), float(b)) for h, b in c]


# ── 1. the physics did not move ────────────────────────────────────────────

@pytest.mark.parametrize("name", EXACT)
def test_a_former_sheet_card_is_reproduced_point_for_point(name):
    old, new = OLD[name], M.get_magnet(name)
    assert new.temperature_c == old["temperature_c"]
    assert new.Br == old["Br"]
    assert new.Hc == old["Hc"]
    assert new.mu_rec == old["mu_rec"]
    assert new.alpha_br_pct_per_k == old["alpha_br_pct_per_k"]
    assert new.beta_hcj_pct_per_k == old["beta_hcj_pct_per_k"]
    assert new.max_working_temp_c == old["max_working_temp_c"]
    assert (new.sigma, new.density) == (old["sigma"], old["density"])
    assert _curve(new.bh_curve) == _curve(old["bh_curve"])


@pytest.mark.parametrize("name", FITTED)
def test_the_former_n45eh_cards_are_reproduced_within_the_fit(name):
    """N45EH had no manufacturer sheet; its two hand-made cards share one
    J/J_recoil(|H|/Hcj) shape, which the tanh model was fitted to."""
    old, new = OLD[name], M.get_magnet(name)
    old_knee = _curve(old["bh_curve"])[1][0]
    assert abs(new.Br / old["Br"] - 1.0) < 4e-4                 # 1.1480 -> 1.1482, 1.1020 -> 1.1016
    assert abs(new.h_knee / old_knee - 1.0) < 3e-4              # -856.3 / -619.0 kA/m, 0.02 %
    assert abs(new.Hc / old["Hc"] - 1.0) < 1e-5
    assert new.mu_rec == old["mu_rec"]
    # the curve, on the old card's own H points (piecewise-linear)
    import numpy as np
    nh = np.array([p[0] for p in new.bh_curve]); nb = np.array([p[1] for p in new.bh_curve])
    for h, b in _curve(old["bh_curve"])[1:]:
        assert abs(float(np.interp(h, nh, nb)) - b) < 6e-3, (name, h, b)


def test_a_typed_temperature_on_a_former_name_lands_where_the_grade_does():
    """N52UH_150C at 90 °C and N52UH at 90 °C are one card: derived from 20 °C,
    never stretched from the 150 °C copy."""
    a = M.get_magnet("N52UH_150C").at_temperature(90.0)
    b = M.get_magnet("N52UH").at_temperature(90.0)
    assert (a.Br, a.Hc, a.h_knee, a.bh_curve) == (b.Br, b.Hc, b.h_knee, b.bh_curve)


# ── 2. the library ─────────────────────────────────────────────────────────

def test_the_library_holds_one_20c_card_per_grade():
    names = M.list_materials("magnet")["magnet"]
    assert not [n for n in names if M.legacy_magnet(n) is not None], names
    for n in names:
        card = M.get_magnet(n)
        assert card.temperature_c == 20.0, n
    for g in {g for g, _t in M.LEGACY_MAGNET_CARDS.values()}:
        assert g in names


@pytest.mark.parametrize("grade,tmax", [("N52UH", 180), ("N45EH", 200),
                                        ("F45SH", 150), ("F52SH", 150)])
def test_every_grade_card_carries_its_limit_and_a_model_that_draws_its_curve(grade, tmax):
    card = M.get_magnet(grade)
    assert card.max_working_temp_c == tmax
    assert M._model_kind(card.demag_model)
    # the stored 20 °C curve IS the model's: forcing a derivation reproduces it
    regen = M._derive_curve(card.demag_model, card.Br,
                            float(card.demag_model["hcj_a_m"]), card.mu_rec)
    assert _curve(regen) == _curve(card.bh_curve)


def test_a_former_name_resolves_under_its_own_name_with_its_grade():
    c = M.get_magnet("Arnold_N52UH_150C")
    assert c.grade_card == "N52UH" and c.temperature_c == 150.0
    assert M.get_magnet("F52SH_120C").name == "F52SH_120C"
    assert M.legacy_magnet("F45SH 120C") == ("F45SH", 120.0)   # the catalog's display spelling
    with pytest.raises(KeyError):
        M.get_material("magnet", "F52SH_100C")                  # never a card: not invented


def test_derived_cards_keep_the_solver_conventions_over_the_service_range():
    for g in ("N52UH", "N45EH", "F45SH", "F52SH"):
        card = M.get_magnet(g)
        prev = None
        for t in range(-20, int(card.max_working_temp_c) + 1, 10):
            hot = card.at_temperature(float(t))
            hs = [p[0] for p in hot.bh_curve]
            assert hs == sorted(hs) and len(set(hs)) == len(hs), (g, t)
            assert hot.bh_curve[0][1] <= 0.0 and hot.bh_curve[-1][0] == 0.0
            assert hot.bh_curve[-1][1] == pytest.approx(hot.Br, abs=1.5e-4)
            if prev is not None:
                assert hot.Br < prev.Br and abs(hot.h_knee) < abs(prev.h_knee), (g, t)
            prev = hot


# ── 4. the limit ───────────────────────────────────────────────────────────

def test_a_typed_temperature_above_the_grade_limit_is_refused_with_a_reason():
    card = M.get_magnet("N52UH")
    assert M.magnet_temp_limit_error(card, 180.0) is None
    assert M.magnet_temp_limit_error(card, None) is None
    why = M.magnet_temp_limit_error(card, 181.0)
    assert why and "180" in why and "N52UH" in why
    assert M.magnet_temp_limit_error(M.get_magnet("N52UH_150C"), 185.0)   # the former name too
    assert M.magnet_temp_limit_error(card, float("nan")) == "not a number"


def test_the_route_guard_refuses_only_a_typed_solve(monkeypatch):
    from fastapi import HTTPException
    from motor_ai_sim.routes import simulation as S
    monkeypatch.setattr(S, "_assigned_magnet_card",
                        lambda ov=None: ("F52SH", M.get_magnet("F52SH")))
    S._typed_magnet_temp_guard(magnet_temp_c=150.0)
    S._typed_magnet_temp_guard(magnet_temp_c=None)
    S._typed_magnet_temp_guard(magnet_temp_c=170.0, restore=True)        # solves nothing
    with pytest.raises(HTTPException) as e:
        S._typed_magnet_temp_guard(magnet_temp_c=170.0)
    assert e.value.status_code == 422 and "150" in str(e.value.detail)


def test_a_computed_temperature_past_the_limit_is_flagged_not_clamped(monkeypatch):
    flag = M.magnet_over_limit(M.get_magnet("F45SH"), 163.4)
    assert flag == {"temp_c": 163.4, "limit_c": 150.0, "card": "F45SH"}
    assert M.magnet_over_limit(M.get_magnet("F45SH"), 150.0) is None
    # the derivation itself still answers above the limit (the loop solves there)
    assert M.get_magnet("F45SH").at_temperature(163.4).Br > 0
    from motor_ai_sim.routes import coupled as C
    from motor_ai_sim.routes import simulation as S
    monkeypatch.setattr(S, "_assigned_magnet_card", lambda: ("N52UH", M.get_magnet("N52UH")))
    blk = C._magnet_over_limit_block(176.0, 188.2)
    assert blk["magnet_over_limit"]["temp_c"] == 188.2
    assert blk["magnet_over_limit"]["basis"] == "hottest magnet element"
    assert C._magnet_over_limit_block(150.0, 170.0) == {}


# ── report: the card temperature and the KV walk ───────────────────────────

def test_the_report_reads_the_card_temperature_off_the_card():
    from motor_ai_sim import report as R
    assert R._magnet_card_temp("N52UH") == 20.0
    assert R._magnet_card_temp("F52SH_120C") == 120.0
    assert R._cold_br_card("N52UH") is None, "a 20 °C card is its own cold card"
    cc = R._cold_br_card("F52SH_120C")
    assert cc[0] == "F52SH" and cc[1] == 1.44
    k, note = R._cold_br_factor("N52UH")
    assert k == 1.0 and "20 °C" in note


def test_the_no_load_kv_walks_from_the_probe_temperature():
    from motor_ai_sim import report as R
    # a run whose probe ran AT the duty's temperature: no walk at all
    kv, why = R.kv_at_magnet_temp(50.0, "N52UH", 150.0, 150.0)
    assert kv == 50.0 and "probe" in why
    # probe at 150, duty at 20: KV falls by Br(150)/Br(20)
    kv, _ = R.kv_at_magnet_temp(50.0, "N52UH", 20.0, 150.0)
    c = M.get_magnet("N52UH")
    assert kv == pytest.approx(50.0 * c.at_temperature(150.0).Br / c.Br, rel=1e-9)
    # an old run (no probe stamp) on a former name: the card's own temperature, as before
    kv_old, _ = R.kv_at_magnet_temp(50.0, "N52UH_150C", 20.0)
    assert kv_old == pytest.approx(kv, rel=2e-3)


# ── 5. the migration ───────────────────────────────────────────────────────

def _cfg():
    return {"name": "L9", "materials": {"magnet": "N52UH_150C", "stator_core": "B10AHV900M"},
            "duties": [
                {"name": "rated", "mesh": {"sim.stepsPP": 60},
                 "runs": {"pwm_voltage": {"settings": {"sim.stepsPP": 60}}}},
                {"name": "peak", "mesh": {"sim.magnetTempC": "90"}},
                {"name": "cold", "materials": {"magnet": "N52UH_20C"}},
                {"name": "blank", "mesh": {"sim.magnetTempC": ""}},
            ]}


def test_the_migration_maps_names_to_grade_plus_temperature():
    out, notes = migrate_config_doc(_cfg())
    assert out["materials"]["magnet"] == "N52UH"
    d = {x["name"]: x for x in out["duties"]}
    assert d["rated"]["mesh"]["sim.magnetTempC"] == "150"
    assert d["rated"]["runs"]["pwm_voltage"]["settings"]["sim.magnetTempC"] == "150"
    assert d["peak"]["mesh"]["sim.magnetTempC"] == "90", "a typed temperature is kept"
    assert d["cold"]["materials"]["magnet"] == "N52UH"
    assert d["cold"]["mesh"] == {"sim.magnetTempC": "20"}
    assert d["blank"]["mesh"]["sim.magnetTempC"] == "150"
    assert any("kept at 90" in n for n in notes)
    # idempotent, and the input is untouched (a copy is returned)
    again, notes2 = migrate_config_doc(out)
    assert again == out and notes2 == []
    assert _cfg()["materials"]["magnet"] == "N52UH_150C"


def test_the_migration_restamps_results_onto_the_new_build_signature():
    from motor_ai_sim.routes.family import _build_sig
    die = {"geometry": {"num_slots": 12}}
    cfg = _cfg()
    sig = _build_sig(die, cfg)
    for d in cfg["duties"]:
        d["result"] = {"build_sig": sig}
    out, notes = migrate_config_doc(cfg, die)
    new = _build_sig(die, out)
    assert new != sig
    assert all(d["result"]["build_sig"] == new for d in out["duties"])
    assert any("re-stamped" in n for n in notes)


def test_a_configuration_without_former_names_is_left_alone():
    cfg = {"materials": {"magnet": "F52SH"}, "duties": [{"name": "a", "mesh": {}}]}
    out, notes = migrate_config_doc(cfg)
    assert out == cfg and notes == []


def test_a_saved_motor_payload_is_migrated_like_a_configuration():
    out, notes = migrate_motor_payload({"materials": {"magnet": "F45SH_120C"},
                                        "duty": {"name": "x", "mesh": {}}})
    assert out["materials"]["magnet"] == "F45SH"
    assert out["duty"]["mesh"]["sim.magnetTempC"] == "120"
    assert temp_text(87.5) == "87.5" and temp_text(150.0) == "150"


# ── the Run path and the summary (owner 2026-10-30, after #503 went live) ──

def test_the_kernel_run_refuses_a_typed_temperature_above_the_limit(monkeypatch):
    """POST /api/kernel/run is the route the Simulation tab's Run posts to; the
    guard on the GET route alone never saw it."""
    from fastapi import HTTPException
    from motor_ai_sim.routes import kernel as K
    from motor_ai_sim.routes import simulation as S
    seen = {}

    def _card(ov=None):
        seen["ov"] = ov
        return "N52UH_150C", M.get_magnet("N52UH_150C")
    monkeypatch.setattr(S, "_assigned_magnet_card", _card)
    monkeypatch.setattr(K, "_get_kernel", lambda: (_ for _ in ()).throw(AssertionError("solved")))
    with pytest.raises(HTTPException) as e:
        K.run(K.KernelRunRequest(capability="solver.em_transient",
                                 payload={"magnet_temp_c": 185.0,
                                          "mat": '{"assignment": {"magnet": "N52UH_150C"}}'}))
    assert e.value.status_code == 422 and "180" in e.value.detail
    assert seen["ov"]["assignment"]["magnet"] == "N52UH_150C", "the body's own override is used"
    assert S.typed_magnet_temp_refusal(150.0) is None
    assert S.typed_magnet_temp_refusal(None) is None


def test_the_summary_reports_the_magnet_temperature_of_a_former_card(monkeypatch):
    """A blank field on a former `N52UH_150C` solves at the card's 150 °C and the
    run says so; a typed 90 °C is reported as typed, with its Br."""
    from motor_ai_sim.routes import simulation as S
    monkeypatch.setattr(S, "_assigned_magnet_card",
                        lambda ov=None: ("N52UH_150C", M.get_magnet("N52UH_150C")))
    card = S._magnet_temperature_stamp(None)
    assert card["magnet_temp_source"] == "card" and card["magnet_temp_used_c"] == 150.0
    assert card["magnet_card_temp_c"] == 150.0 and card["magnet_limit_c"] == 180.0
    typed = S._magnet_temperature_stamp(90.0)
    assert typed["magnet_temp_source"] == "typed" and typed["magnet_temp_used_c"] == 90.0
    assert typed["magnet_Br_used_T"] == pytest.approx(1.3099, abs=1e-4)
    assert "magnet_over_limit" not in typed
