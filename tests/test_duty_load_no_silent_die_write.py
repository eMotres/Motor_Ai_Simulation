"""Loading a duty never silently writes die-level geometry.

Incident 2026-09-27 ~20:20: loading "CIANO14 12_40 Ø12 12s10p / L10 / rated"
through the owner API (activate → PUT /api/geometry) rewrote the die:
wire_width 0.8→2, wire_height 0.2→0.190333 (the slot-fit bound the clamp
derived for 6 wires), num_wires_per_slot 5→6, motor_length 40→10.  The PUT's
die sync copied the whole live machine — config-level keys and clamp output
included — into die.yaml; activate never looked at the duty's own stamp.
"""
from __future__ import annotations

import json

import pytest
import yaml
from fastapi import HTTPException

from motor_ai_sim.routes import family as fam

DIE = "TINY 12"
CFG = "L10"

DIE_GEO = {"stator_diameter": 12.0, "num_seg": 2, "num_slots_per_segment": 6,
           "num_poles_per_segment": 5, "tooth_width": 1.5,
           "wire_width": 0.8, "wire_height": 0.2, "num_wires_per_slot": 5,
           "motor_length": 40}

# the stale stamp: same lamination, other winding + tooth, stack 10
SIG = ("motor_length:10|num_poles_per_segment:5|num_seg:2|"
       "num_slots_per_segment:6|num_wires_per_slot:6|stator_diameter:12|"
       "tooth_width:1.7|wire_height:0.2|wire_width:2")


@pytest.fixture
def dies(monkeypatch, tmp_path):
    root = tmp_path / "dies"
    (root / DIE).mkdir(parents=True)
    (root / DIE / "die.yaml").write_text(yaml.safe_dump(
        {"name": DIE, "locked": False, "geometry": dict(DIE_GEO)},
        sort_keys=False), encoding="utf-8")
    (root / DIE / f"{CFG}.yaml").write_text(yaml.safe_dump({
        "name": CFG, "die": DIE, "geometry_overrides": {"motor_length": 10},
        "duties": [{"name": "rated", "mode": "motor", "rpm": 15000.0,
                    "summary": {"_geoSig": SIG}},
                   {"name": "clean", "mode": "motor", "rpm": 15000.0}],
    }, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(fam, "_DIES_DIR", root)
    monkeypatch.setattr(fam, "_CTX_FILE", tmp_path / ".family_context.json")
    monkeypatch.setattr(fam, "_sync_mesh_config_from_duty", lambda c, n: False)
    return root


def _die(root):
    return yaml.safe_load((root / DIE / "die.yaml").read_text(encoding="utf-8"))


def _hist(root):
    h = root / ".history" / DIE
    return sorted(h.glob("*.yaml")) if h.is_dir() else []


def test_differing_duty_opens_on_the_current_build_and_is_stale(dies, tmp_path):
    """Owner 2026-10-29: a duty ALWAYS opens on the configuration's current
    build.  No 409, no dialog, nothing written: the answer flags the saved
    results as stale and names the diff."""
    before = (dies / DIE / "die.yaml").read_bytes()
    cfg_before = (dies / DIE / f"{CFG}.yaml").read_bytes()
    out = fam.activate(fam.Activate(die=DIE, config=CFG, duty="rated"), _w={})
    assert out["ok"] and out["geometry_stale"] is True
    assert "geometry_applied" not in out
    by = {x["key"]: x for x in out["geometry_diffs"]}
    assert by["wire_width"]["scope"] == "winding" and by["wire_width"]["duty"] == 2
    assert by["num_wires_per_slot"]["duty"] == 6
    assert by["tooth_width"]["scope"] == "die"
    assert "motor_length" not in by            # config says 10 too — no diff
    assert (dies / DIE / "die.yaml").read_bytes() == before
    assert (dies / DIE / f"{CFG}.yaml").read_bytes() == cfg_before
    assert json.loads((tmp_path / ".family_context.json").read_text(
        encoding="utf-8"))["duty"] == "rated"
    assert _hist(dies) == []


def test_differing_duty_on_a_locked_die_still_opens(dies):
    """A locked die changes nothing about opening a stale duty."""
    d = yaml.safe_load((dies / DIE / "die.yaml").read_text(encoding="utf-8"))
    d["locked"] = True
    (dies / DIE / "die.yaml").write_text(yaml.safe_dump(d, sort_keys=False),
                                         encoding="utf-8")
    out = fam.activate(fam.Activate(die=DIE, config=CFG, duty="rated"), _w={})
    assert out["geometry_stale"] is True
    assert _hist(dies) == []


def test_revert_to_the_duty_build_is_refused_and_writes_nothing(dies):
    """Owner 2026-10-29 «и никаких вопросов не задавалось»: loading never
    reverts the configuration or the die to a duty's older build."""
    before = (dies / DIE / "die.yaml").read_bytes()
    cfg_before = (dies / DIE / f"{CFG}.yaml").read_bytes()
    with pytest.raises(HTTPException) as ei:
        fam.activate(fam.Activate(die=DIE, config=CFG, duty="rated",
                                  geometry_choice="apply_duty"), _w={})
    assert ei.value.status_code == 422
    assert ei.value.detail["code"] == "geometry_choice_removed"
    assert (dies / DIE / "die.yaml").read_bytes() == before
    assert (dies / DIE / f"{CFG}.yaml").read_bytes() == cfg_before
    assert _hist(dies) == []
    assert not hasattr(fam, "_apply_duty_geometry")


def test_solved_on_stamp_makes_the_duty_current(dies, tmp_path):
    """Root cause 2026-10-29: a coupled run solved on the current build left
    the duty's older EM `_geoSig` in place.  `note_duty_solved_build` stamps
    the duty with the build the run solved; the duty then opens clean."""
    (tmp_path / ".family_context.json").write_text(
        json.dumps({"die": DIE, "config": CFG, "duty": "rated"}), encoding="utf-8")
    live = dict(DIE_GEO, motor_length=10)
    assert fam.note_duty_solved_build(live, "coupled") is True
    c = yaml.safe_load((dies / DIE / f"{CFG}.yaml").read_text(encoding="utf-8"))
    so = c["duties"][0]["solved_on"]
    assert so["source"] == "coupled" and so["build_sig"]
    assert c["duties"][0]["summary"]["_geoSig"] == SIG   # EM summary untouched
    # same build again -> no second write (no .history churn)
    n_hist = len(_hist(dies))
    assert fam.note_duty_solved_build(live, "coupled") is False
    assert len(_hist(dies)) == n_hist
    out = fam.activate(fam.Activate(die=DIE, config=CFG, duty="rated"), _w={})
    assert out["geometry_stale"] is False and out["geometry_diffs"] == []


def test_solved_on_needs_a_loaded_duty(dies, tmp_path):
    assert fam.note_duty_solved_build(dict(DIE_GEO), "coupled") is False
    (tmp_path / ".family_context.json").write_text(
        json.dumps({"die": DIE, "config": CFG, "duty": "nope"}), encoding="utf-8")
    assert fam.note_duty_solved_build(dict(DIE_GEO), "coupled") is False


def test_keep_die_loads_without_writing(dies):
    before = (dies / DIE / "die.yaml").read_bytes()
    out = fam.activate(fam.Activate(die=DIE, config=CFG, duty="rated",
                                    geometry_choice="keep_die"), _w={})
    assert out["ok"] and out["geometry_stale"] is True
    assert (dies / DIE / "die.yaml").read_bytes() == before
    assert _hist(dies) == []


def test_duty_without_stamp_loads_as_before(dies):
    out = fam.activate(fam.Activate(die=DIE, config=CFG, duty="clean"), _w={})
    assert out["ok"] and out["geometry_diffs"] == []
    assert out["geometry_stale"] is False


def test_foreign_lamination_duty_opens_on_the_die_and_is_stale(dies):
    c = yaml.safe_load((dies / DIE / f"{CFG}.yaml").read_text(encoding="utf-8"))
    c["duties"][0]["summary"]["_geoSig"] = SIG.replace("stator_diameter:12",
                                                       "stator_diameter:30")
    (dies / DIE / f"{CFG}.yaml").write_text(yaml.safe_dump(c), encoding="utf-8")
    out = fam.activate(fam.Activate(die=DIE, config=CFG, duty="rated"), _w={})
    assert out["geometry_stale"] is True
    assert any(x["scope"] == "identity" for x in out["geometry_diffs"])
    assert _die(dies)["geometry"]["stator_diameter"] == 12.0


def test_geometry_put_sync_never_writes_config_keys_into_die(dies, tmp_path):
    """The PUT's die sync after a load: live carries the config overrides and
    the clamp-derived wire_height — die.yaml must not change at all."""
    (tmp_path / ".family_context.json").write_text(
        json.dumps({"die": DIE, "config": CFG, "duty": "rated"}), encoding="utf-8")
    before = (dies / DIE / "die.yaml").read_bytes()
    live = dict(DIE_GEO, wire_width=2, wire_height=0.190333,
                num_wires_per_slot=6, motor_length=10)
    assert fam.sync_active_die_geometry(live, prev_geo=dict(DIE_GEO)) is None
    assert (dies / DIE / "die.yaml").read_bytes() == before
    # a genuine die-level edit still syncs, free keys still the die's own
    live2 = dict(live, tooth_width=1.6)
    assert fam.sync_active_die_geometry(live2, prev_geo=dict(DIE_GEO)) == DIE
    g = _die(dies)["geometry"]
    assert g["tooth_width"] == 1.6 and g["wire_height"] == 0.2
    assert g["motor_length"] == 40 and g["num_wires_per_slot"] == 5


def test_coupled_run_stamps_the_build_it_solved(dies, tmp_path, monkeypatch):
    """The coupled route's hook: live config geometry (+ the request's `geo`
    override) is what gets stamped on the loaded duty."""
    import motor_ai_sim.config as mcfg
    from motor_ai_sim.routes import coupled
    (tmp_path / ".family_context.json").write_text(
        json.dumps({"die": DIE, "config": CFG, "duty": "rated"}), encoding="utf-8")
    monkeypatch.setattr(mcfg, "get_config",
                        lambda *a, **k: {"geometry": dict(DIE_GEO, motor_length=10)})
    assert coupled._note_solved_build({}) is True
    out = fam.activate(fam.Activate(die=DIE, config=CFG, duty="rated"), _w={})
    assert out["geometry_stale"] is False


def test_solved_on_survives_an_operating_point_resave(dies, tmp_path):
    """A duty re-save that brings no new primary summary keeps the stamp; one
    that brings a summary replaces it (that summary is the newest statement)."""
    (tmp_path / ".family_context.json").write_text(
        json.dumps({"die": DIE, "config": CFG, "duty": "rated"}), encoding="utf-8")
    assert fam.note_duty_solved_build(dict(DIE_GEO, motor_length=10), "coupled")
    spec = dict(name="rated", mode="motor", current_arms=10.0, rpm=15000.0,
                gamma_deg=0.0)
    fam.upsert_duty(fam.DutyCreate(die=DIE, config=CFG, duty=spec), _w={})
    c = yaml.safe_load((dies / DIE / f"{CFG}.yaml").read_text(encoding="utf-8"))
    assert c["duties"][-1]["solved_on"]["source"] == "coupled"
    fam.upsert_duty(fam.DutyCreate(die=DIE, config=CFG, duty=dict(
        spec, summary={"_geoSig": SIG})), _w={})
    c = yaml.safe_load((dies / DIE / f"{CFG}.yaml").read_text(encoding="utf-8"))
    assert "solved_on" not in c["duties"][-1]
