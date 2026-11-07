"""The motor PWM extra loss SPLIT BY PART (owner 2026-11-02): anchor split, per-part
HDF scaling, the rebuild of a stored passport without FEM, and old passports.

Fixtures are made-up numbers in the shape of a ``scripts/passport_full.py task_pwm``
output (``sine_36``, ``pwm_summary``, ``harm_ref``, ``dP_harm_W``)."""
from __future__ import annotations

import json
import math

import numpy as np
import pytest

from motor_ai_sim.passport_v1 import fullcard as FC
from motor_ai_sim.passport_v1 import pwm_parts as PP
from motor_ai_sim.passport_v1 import stage3 as S3
from motor_ai_sim.simulation.postproc import loss_parts

V_DC = 22.2


def _summary(total, iron, mag, cu, st_e, st_h, ro_e, ro_h, steps):
    return {"P_loss_total_W": total, "P_core_W": iron, "P_mag_W": mag, "P_solid_W": mag,
            "P_shaft_W": 0.0, "P_stranded_W": cu, "n_steps_per_period": steps,
            "V1_seed_peak_V": 10.0,
            "P_core_terms": {"stator": {"hysteresis_W": st_h, "eddy_W": st_e, "excess_W": 0.0},
                             "rotor": {"hysteresis_W": ro_h, "eddy_W": ro_e, "excess_W": 0.0}}}


def anchor_json(*, tag_point="rated", I=42.8, rpm=13000.0, v1=10.0, dP=3.5, ref_total=68.5,
                matched=False, seed=True):
    """total 67.7 -> 72.0 W; iron 9.2 -> 10.6 W (stator eddy 3.42 -> 4.57, stator hyst
    2.61 -> 2.68, rotor eddy 0.36 -> 0.51, rotor hyst 0.12 -> 0.13); the rest copper + magnet."""
    d = {"task": "pwm", "machine": "L12", "point": tag_point, "I": I, "rpm": rpm,
         "controller": {"device": "IQE018N06NM6SC", "f_carrier_hz": 48000, "dead_time_us": 0.1},
         "steps_pwm": 640, "dP_harm_W": dP,
         "harm_ref": {"P_loss_ref_W": ref_total, "P_loss_v_W": ref_total + dP}}
    if seed:
        d["sine_36"] = _summary(67.7, 9.2, 3.0, 55.5, 3.42, 2.61, 0.36, 0.12, 36)
        d["sine_36"]["V1_seed_peak_V"] = v1
        d["pwm_summary"] = _summary(72.0, 10.6, 3.6, 57.8, 4.57, 2.68, 0.51, 0.13, 640)
    else:
        d["sine_36"] = {"V1_seed_peak_V": v1}
    if matched:
        d["harm_ref"]["parts_ref_W"] = {"iron_W": 9.9, "magnet_W": 3.1, "copper_W": 55.4,
                                        "shaft_W": 0.1, "sleeve_W": 0.0, "total_W": 68.5}
        d["harm_ref"]["parts_v_W"] = {"iron_W": 11.2, "magnet_W": 3.6, "copper_W": 56.9,
                                      "shaft_W": 0.3, "sleeve_W": 0.0, "total_W": 72.0}
    return d


def _sum(p):
    return sum(p[k] for k in S3.PART_KEYS)


# ── the split at one anchor ─────────────────────────────────────────────────

def test_seed_split_is_normalised_to_the_matched_total_and_sums_to_it():
    r = PP.anchor_loss_parts(anchor_json())
    assert r["mode"] == "seed_normalised"
    assert _sum(r["parts_W"]) == pytest.approx(3.5, abs=1e-4)
    # PWM − seed: iron 1.4, magnet 0.6, copper 2.3 of 4.3 W, scaled to the matched 3.5 W
    f = 3.5 / 4.3
    assert r["parts_W"]["iron"] == pytest.approx(1.4 * f, abs=1e-4)
    assert r["parts_W"]["magnet"] == pytest.approx(0.6 * f, abs=1e-4)
    assert r["parts_W"]["copper_ac"] == pytest.approx(2.3 * f, abs=1e-4)
    assert r["parts_W"]["other"] == pytest.approx(0.0, abs=1e-3)
    st = r["iron_terms_W"]["stator"]
    assert st["eddy_W"] == pytest.approx((4.57 - 3.42) * f, abs=1e-4)
    assert st["hysteresis_W"] == pytest.approx((2.68 - 2.61) * f, abs=1e-4)
    assert r["iron_terms_W"]["rotor"]["eddy_W"] == pytest.approx((0.51 - 0.36) * f, abs=1e-4)
    assert "normalised" in r["basis"] and "36" in r["basis"] and "640" in r["basis"]


def test_matched_split_is_the_difference_of_the_matched_pair():
    r = PP.anchor_loss_parts(anchor_json(matched=True))
    assert r["mode"] == "matched"
    assert r["parts_W"]["iron"] == pytest.approx(1.3)
    assert r["parts_W"]["magnet"] == pytest.approx(0.5)
    assert r["parts_W"]["copper_ac"] == pytest.approx(1.5)
    assert r["parts_W"]["other"] == pytest.approx(0.2)          # the shaft eddy delta
    assert _sum(r["parts_W"]) == pytest.approx(3.5, abs=1e-9)


def test_iron_only_and_unsplit_anchors_put_the_rest_in_other():
    d = anchor_json()
    for s in (d["sine_36"], d["pwm_summary"]):
        for k in ("P_mag_W", "P_solid_W", "P_stranded_W"):
            s.pop(k)
    r = PP.anchor_loss_parts(d)
    assert r["mode"] == "iron_only"
    assert r["parts_W"]["iron"] == pytest.approx(1.4)
    assert r["parts_W"]["magnet"] == 0.0 and r["parts_W"]["copper_ac"] == 0.0
    assert r["parts_W"]["other"] == pytest.approx(3.5 - 1.4)
    assert "other" in r["basis"]
    u = PP.anchor_loss_parts(anchor_json(seed=False))
    assert u["mode"] == "unsplit" and u["parts_W"]["other"] == pytest.approx(3.5)
    z = PP.anchor_loss_parts(anchor_json(dP=-1.2))          # clamped to 0 W: nothing to split
    assert z["mode"] == "zero" and _sum(z["parts_W"]) == 0.0


def test_anchor_row_keeps_the_clamp_and_carries_the_split():
    row = PP.anchor_row(anchor_json(dP=-0.8), "pwm_L12_rated_x", V_DC)
    assert row["dP_harm_W"] == 0.0 and row["dP_harm_W_raw"] == -0.8
    assert row["dP_parts"]["mode"] == "zero"
    row = PP.anchor_row(anchor_json(), "pwm_L12_rated_x", V_DC)
    assert row["m_index"] == pytest.approx(10.0 / (V_DC / 2))
    assert _sum(row["dP_parts"]["parts_W"]) == pytest.approx(row["dP_harm_W"], abs=1e-4)
    assert PP.anchor_row({"rpm": 1}, "t", V_DC) is None


def test_postproc_loss_parts_reads_the_same_terms_the_summary_sums():
    raw = {"P_cu_W": [10.0, 12.0], "P_fe_W": [3.0, 5.0], "P_mag_eddy_W": [1.0],
           "P_shaft_eddy_W": [0.5, 0.5], "P_fe_terms": {"stator": {"hysteresis_W": 1, "eddy_W": 2}}}
    p = loss_parts(raw)
    assert (p["copper_W"], p["iron_W"], p["magnet_W"], p["shaft_W"], p["sleeve_W"]) == (11, 4, 1, 0.5, 0)
    assert p["total_W"] == pytest.approx(16.5)
    assert p["iron_terms"]["stator"] == {"hysteresis_W": 1.0, "eddy_W": 2.0, "excess_W": 0.0}


# ── per-part HDF scaling over the grid ──────────────────────────────────────

def _anchors():
    a1 = PP.anchor_row(anchor_json(I=21.4, v1=9.0, dP=3.0), "pwm_L12_I0.5I0_x", V_DC)
    a2 = PP.anchor_row(anchor_json(I=42.8, v1=10.5, dP=4.0, matched=True), "pwm_L12_rated_x", V_DC)
    return [a1, a2]


def test_each_part_scales_with_its_own_k_over_hdf_and_the_parts_sum_to_the_total():
    an = _anchors()
    m = S3.PwmModel(an)
    assert m.split
    for x in an:                                             # exact at the anchors
        p = m.parts_at(x["rpm"], x["I"], 0.5)
        assert p == pytest.approx(x["dP_parts"]["parts_W"])
    ms = sorted(x["m_index"] for x in an)
    for mp in (ms[0] - 0.2, 0.5 * (ms[0] + ms[1]), ms[1] + 0.05):
        p = m.parts_at(1000.0, 30.0, mp)
        tot, _how, _fem = m.at(1000.0, 30.0, mp)
        assert _sum(p) == pytest.approx(tot, rel=1e-12)
        for k in S3.PART_KEYS:                               # k_part = dP_part / HDF(m), linear in m
            ks = [x["dP_parts"]["parts_W"][k] / S3.hdf_svpwm(x["m_index"])
                  for x in sorted(an, key=lambda x: x["m_index"])]
            want = float(np.interp(mp, [round(v, 3) for v in ms], ks)) * S3.hdf_svpwm(mp)
            assert p[k] == pytest.approx(want, rel=1e-9, abs=1e-12)
    # the carrier scaling of an ESTIMATE variant moves every part by the same factor
    s = S3.PwmModel(an, scale=0.344, derived="ESTIMATE")
    assert s.parts_at(1000.0, 30.0, 0.8) == pytest.approx(
        {k: 0.344 * v for k, v in m.parts_at(1000.0, 30.0, 0.8).items()})


def test_anchors_without_a_split_keep_the_old_model():
    a = [{"tag": "lo", "rpm": 13000.0, "I": 20.0, "dP_harm_W": 4.0, "m_index": 0.9}]
    m = S3.PwmModel(a)
    assert not m.split
    assert m.parts_at(13000.0, 20.0, 0.9) == {"iron": 0.0, "magnet": 0.0, "copper_ac": 0.0,
                                              "other": 4.0}


# ── rebuild a stored passport (no FEM) ──────────────────────────────────────

def _stored_variant(an, scale=1.0, vid="si_48k"):
    """A stored variant as stage3.build_variants wrote it before the split."""
    m = S3.PwmModel([{k: v for k, v in x.items() if k != "dP_parts"} for x in an], scale=scale)
    pts = {}
    for rpm, I, mp in ((13000.0, 42.8, None), (6500.0, 42.8, 0.6), (3250.0, 21.4, 0.31)):
        mp_ = mp if mp is not None else an[1]["m_index"]
        dP, how, _ = m.at(rpm, I, mp_)
        pts["%grpm_%gA" % (rpm, I)] = {"rpm": rpm, "I_A": I, "motor_pwm_loss_W": dP,
                                        "motor_pwm_loss_basis": how}
    pts["19500rpm_42.8A"] = {"rpm": 19500.0, "I_A": 42.8, "status": "infeasible"}
    return {"id": vid, "device": "IQE018N06NM6SC", "carrier_hz": 48000.0,
            "bus_v": {"min": 18.0, "nom": V_DC, "max": 25.2},
            "provenance": {"motor_pwm_fem_runs": [x["tag"] for x in an]}, "points": pts}


def _runs():
    return {"pwm_L12_I0.5I0_x": anchor_json(I=21.4, v1=9.0, dP=3.0),
            "pwm_L12_rated_x": anchor_json(I=42.8, v1=10.5, dP=4.0, matched=True)}


def test_rebuild_adds_the_split_and_keeps_every_stored_total():
    an = _anchors()
    rec = {"schema": "passport-v1", "pwm_variants": [_stored_variant(an),
                                                     _stored_variant(an, 0.344, "gan_100k")]}
    before = json.loads(json.dumps(rec))
    new, reps = PP.rebuild_record(rec, _runs().get)
    assert rec == before                                     # the input is not mutated
    assert [r.get("refused") for r in reps] == [None, None]
    for v0, v in zip(before["pwm_variants"], new["pwm_variants"]):
        assert v["motor_pwm_loss_parts"]["keys"] == list(S3.PART_KEYS)
        for k, p in v["points"].items():
            p0 = v0["points"][k]
            assert p.get("motor_pwm_loss_W") == p0.get("motor_pwm_loss_W")   # totals unchanged
            if p0.get("motor_pwm_loss_W") is None:
                assert "motor_pwm_loss_parts_W" not in p
                continue
            q = p["motor_pwm_loss_parts_W"]
            assert _sum(q) == pytest.approx(p["motor_pwm_loss_W"], abs=1e-12)
    # an exact anchor point carries that anchor's own split (scaled by the variant's factor)
    q = new["pwm_variants"][1]["points"]["13000rpm_42.8A"]["motor_pwm_loss_parts_W"]
    want = an[1]["dP_parts"]["parts_W"]
    assert q == pytest.approx({k: 0.344 * v for k, v in want.items()}, abs=1e-9)
    # a scaled point: the per-part model's shares at the point's m
    m = S3.PwmModel(an)
    p = new["pwm_variants"][0]["points"]["6500rpm_42.8A"]
    raw = m.parts_at(6500.0, 42.8, 0.6)
    for k in S3.PART_KEYS:
        assert p["motor_pwm_loss_parts_W"][k] == pytest.approx(
            raw[k] / _sum(raw) * p["motor_pwm_loss_W"], rel=1e-9, abs=1e-12)


def test_rebuild_refuses_missing_or_foreign_anchors_and_leaves_the_variant_as_it_was():
    an = _anchors()
    rec = {"pwm_variants": [_stored_variant(an)]}
    runs = _runs()
    runs.pop("pwm_L12_rated_x")
    new, reps = PP.rebuild_record(rec, runs.get)
    assert "not found" in reps[0]["refused"]
    assert new["pwm_variants"][0] == rec["pwm_variants"][0]
    foreign = _runs()
    foreign["pwm_L12_rated_x"]["sine_36"]["V1_seed_peak_V"] = 6.0   # another machine's run, same tag
    new, reps = PP.rebuild_record(rec, foreign.get)
    assert "does not match" in reps[0]["refused"]
    assert "motor_pwm_loss_parts_W" not in json.dumps(new)


def test_cli_dry_run_then_write(tmp_path, capsys):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "rebuild_cli", Path(__file__).resolve().parents[1] / "scripts" / "rebuild_pwm_loss_parts.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    store = tmp_path / "passports" / "DIE"
    store.mkdir(parents=True)
    f = store / "L12.json"
    f.write_text(json.dumps({"schema": "passport-v1", "source": "nowhere/passport_L12.json",
                             "pwm_variants": [_stored_variant(_anchors())]}), encoding="utf-8")
    runs = tmp_path / "runs" / "full"
    runs.mkdir(parents=True)
    for t, d in _runs().items():
        (runs / f"{t}.json").write_text(json.dumps(d), encoding="utf-8")
    raw = f.read_text(encoding="utf-8")
    assert cli.main([str(tmp_path / "passports"), "--runs", str(tmp_path / "runs")]) == 0
    assert f.read_text(encoding="utf-8") == raw                  # dry run writes nothing
    assert cli.main([str(tmp_path / "passports"), "--runs", str(tmp_path / "runs"), "--write"]) == 0
    rec = json.loads(f.read_text(encoding="utf-8"))
    assert all("motor_pwm_loss_parts_W" in p for p in rec["pwm_variants"][0]["points"].values()
               if p.get("motor_pwm_loss_W") is not None)
    assert (store / "L12.json.pre-parts.bak").read_text(encoding="utf-8") == raw
    out = capsys.readouterr().out
    assert "points split" in out and "written" in out


# ── old passports still render ──────────────────────────────────────────────

def test_old_points_render_as_not_split_and_new_points_show_the_parts():
    assert FC.pwm_loss_cell({"motor_pwm_loss_W": 5.3}) == "5.30 (not split)"
    assert FC.pwm_loss_cell({"motor_pwm_loss_W": None}) is None
    c = FC.pwm_loss_cell({"motor_pwm_loss_W": 5.3, "motor_pwm_loss_parts_W": {
        "iron": 2.68, "magnet": 0.67, "copper_ac": 1.95, "other": 0.0}})
    assert c == "5.30 (Fe 2.68 · mag 0.67 · Cu 1.95 · other 0.00)"
    assert math.isclose(2.68 + 0.67 + 1.95, 5.3)


def test_the_store_still_serves_an_old_passport_without_parts(tmp_path, monkeypatch):
    from motor_ai_sim import passport_store as PS
    d = tmp_path / "DIE"
    d.mkdir()
    old = _stored_variant(_anchors())
    (d / "L12.json").write_text(json.dumps({"schema": "passport-v1", "pwm_variants": [old]}),
                                encoding="utf-8")
    monkeypatch.setattr(PS, "_DIR", tmp_path)
    vs = PS.variants_for("DIE L12")
    assert vs and vs[0]["points"]["13000rpm_42.8A"]["motor_pwm_loss_W"] == pytest.approx(
        old["points"]["13000rpm_42.8A"]["motor_pwm_loss_W"])
