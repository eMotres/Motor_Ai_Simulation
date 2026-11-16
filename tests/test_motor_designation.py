"""The standard motor designation (owner 2026-11-16): generated from the parameters, never typed; recomputed at every
read, so it follows an edit made during simulation; the names in use are only checked, never renamed.

Synthetic geometry (round numbers, no catalog motor's data); the expected strings follow the owner's confirmed format."""
from __future__ import annotations

import copy

import pytest

from motor_ai_sim import motor_designation as MD

LIB = {
    "steel": {"NO20": {"description": "non-oriented (NGO) electrical steel, 0.20 mm", "form": "lamination"},
              "CO49": {"description": "CoFe alloy 0.20 mm", "form": "lamination"},
              "GO27": {"description": "grain-oriented electrical steel 0.27 mm", "form": "lamination"},
              "SMC1": {"description": "Soft Magnetic Composite powder core", "form": "solid"}},
    "magnet": {"NDFEB52": {"description": "sintered NdFeB"}, "FER4": {"description": "hard ferrite Y30"}},
}


def motor(slots=24, poles=28, od=200, length=40, rows=24, split=1, par=1, ww=7.0, wh=0.5, conn="4S", star="star",
          phases=3, steel="NO20", magnet="NDFEB52_150C", slot="copper", rot_r=73.0, sta_r=74.0):
    die = {"geometry": {"num_slots": slots, "num_poles": poles, "stator_diameter": od, "rotor_outer_radius": rot_r,
                        "stator_inner_radius": sta_r, "motor_length": 10}}
    cfg = {"geometry_overrides": {"motor_length": length, "num_wires_per_slot": rows, "wire_split": split,
                                  "wire_parallel": par, "wire_width": ww, "wire_height": wh},
           "winding": {"connection": conn, "star_delta": star, "phases": phases},
           "materials": {"stator_core": steel, "magnet": magnet, "slot": slot}}
    return die, cfg


def name(die_name, die, cfg, **kw):
    return MD.describe(die_name, die, cfg, library=LIB, **kw)


@pytest.mark.parametrize("die_name, kw, expected", [
    ("CILN28 200", {}, "CILN28 200_40 24F4SY"),
    ("CILN28", dict(length=65, rows=17), "CILN28 200_65 17F4SY"),
    ("CILN28", dict(length=200, rows=15, conn="2S-2P"), "CILN28 200_200 15F2S2PY"),
    ("CIANO20 200_50", dict(slots=24, poles=20, length=50, rows=15, conn="2S-2P"), "CIANO20 200_50 15F2S2PY"),
    ("CIANO28 80_12", dict(od=80, length=12, rows=10, split=6, ww=0.4, wh=0.4, rot_r=30.3, sta_r=30.8), "CIANO28 80_12 60R4SY"),
    ("CIANO28 80_12", dict(od=80, length=12, rows=14, ww=3.0, wh=0.35, rot_r=30.3, sta_r=30.8), "CIANO28 80_12 14F4SY"),
    ("CIANO56 400_90", dict(slots=48, poles=56, od=400, length=90, rows=15, conn="4S-2P", phases=6), "CIANDO56 400_90 15F4S2PY"),
    ("CIANO14 40 new", dict(slots=12, poles=14, od=40, length=12, rows=7, conn="2S", phases=None), "CIANO14 40_12 7F2SY"),
])
def test_the_owner_table(die_name, kw, expected):
    out = name(die_name, *motor(**kw))
    assert out["designation"] == expected and out["complete"]
    assert MD.parse(out["designation"]) == MD.fields(out)                  # round trip


def test_letters_from_the_parameters():
    assert name("CIAN28", *motor(steel="CO49"))["designation"].startswith("CIAC28 ")
    assert name("CIAN28", *motor(steel="GO27"))["designation"].startswith("CIAG28 ")
    assert name("CIAN28", *motor(magnet="FER4"))["designation"].startswith("CIANF28 ")
    assert name("CIAN28", *motor(slot="aluminium"))["designation"].startswith("CIANA28 ")
    assert name("CIAN28", *motor(slots=36, poles=4))["designation"].startswith("DIAN4 ")     # q = 3: distributed
    assert name("CIAN28", *motor(rot_r=80.0, sta_r=74.0))["designation"].startswith("COAN28 ")
    assert name("CIAN28", *motor(star="delta"))["designation"].endswith("24F4SD")
    assert name("CIAN28", *motor(conn="4P"))["designation"].endswith("24F4PY")


def test_nothing_is_guessed():
    out = name("100 mm mid-torque", *motor(steel="SMC1", magnet="UNKNOWN_GRADE"))
    assert not out["complete"]
    assert set(out["unknown"]) == {"cooling", "open", "steel", "ferrite"}
    assert out["designation"].startswith("CI??") and "?" in out["designation"]
    assert MD.parse(out["designation"]) is not None                       # the decoder still reads it


def test_rev_suffix():
    out = name("CILN28", *motor(), rev="B")
    assert out["designation"] == "CILN28 200_40 24F4SY Rev B" and MD.parse(out["designation"])["rev"] == "B"


# ── owner 2026-11-17: the name stays right when the motor is changed during simulation ────────────────────────────────

@pytest.mark.parametrize("edit, before, after", [
    (lambda c: c["geometry_overrides"].update(num_wires_per_slot=16), "17F4SY", "16F4SY"),
    (lambda c: c["winding"].update(connection="2S-2P"), "17F4SY", "17F2S2PY"),
    (lambda c: c["winding"].update(star_delta="delta"), "17F4SY", "17F4SD"),
    (lambda c: c["geometry_overrides"].update(wire_width=0.5), "17F4SY", "17R4SY"),
    (lambda c: c["geometry_overrides"].update(motor_length=60), "200_65 ", "200_60 "),
])
def test_an_edit_changes_the_designation_and_an_earlier_result_keeps_its_own(edit, before, after):
    die, cfg = motor(length=65, rows=17)
    earlier = name("CILN28", die, cfg)["designation"]                      # what a result computed now would record
    copy_cfg = copy.deepcopy(cfg)                                          # the edit is made on a copy configuration
    edit(copy_cfg)
    now = name("CILN28", die, copy_cfg)["designation"]
    assert before in earlier and after in now and earlier != now
    assert name("CILN28", die, cfg)["designation"] == earlier             # the source is not touched


def test_candidates_with_different_turns_each_get_their_own_designation():
    die, cfg = motor()
    names = set()
    for turns in (14, 15, 16):
        c = copy.deepcopy(cfg)
        c["geometry_overrides"]["num_wires_per_slot"] = turns
        names.add(name("CILN28", die, c)["designation"])
    assert names == {"CILN28 200_40 14F4SY", "CILN28 200_40 15F4SY", "CILN28 200_40 16F4SY"}


def test_the_check_report_lists_names_that_contradict_the_parameters():
    die, cfg = motor(length=65, rows=15)
    out = name("CILN28 vadim", die, cfg)
    codes = {c["code"] for c in MD.check("CILN28 vadim", "G2-L65-17t", out)}
    assert codes == {"config_name_turns"}
    codes = {c["code"] for c in MD.check("CIAN30 150", "L40", out)}
    assert codes == {"die_name_poles", "die_name_od", "config_name_length"}
    assert {c["code"] for c in MD.check("100 mm mid-torque", "L65", out)} == {"die_name_not_standard"}


def test_parse_refuses_what_is_not_a_designation():
    for bad in ("", "CILN28", "CILN28 200_40", "L12-F475", "CILN28 200_40 24X4SY", "ciln28 200_40 24F4SY"):
        assert MD.parse(bad) is None
