"""A re-save of the SAME excitation as the duty's primary refreshes the headline (US · hpgs, 2026-11-18).

A duty whose first and only run was custom_current (a diode-rectifier duty) made that run its primary; a later save of
custom_current used to land only under ``runs[custom_current]`` and the headline kept the old numbers.  Pinned, for the
duty save (POST /duty) and the result record (POST /duty_result):

* custom_current primary + custom_current save  -> the new headline (summary / result);
* sine ("current") primary + custom_current save -> the headline is untouched, the run goes beside it;
* a sine save always replaces a sine primary, as before.
"""
from __future__ import annotations

import pytest
import yaml

from motor_ai_sim.routes import family as fam

DIE, CFG, DUTY = "TEST 200 die", "L150", "1 kW at 120 rpm"
GEO = {"stator_diameter": 200.0, "num_seg": 4, "num_slots_per_segment": 6, "num_poles_per_segment": 7,
       "core_thickness": 5.6, "slot_height": 20.4, "air_gap": 0.65, "magnet_height": 18.7,
       "rotor_house_height": 2.8, "tooth_width": 11.3, "motor_length": 150, "wire_width": 7.0,
       "wire_spacing_x": 0.1, "insulation_thickness": 0.2, "wire_height": 0.43, "num_wires_per_slot": 33}
MATS = {"magnet": "N52UH", "slot_insulation": "Nomex"}


def _write(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def _duty(drive, p=1.0):
    return {"name": DUTY, "mode": "generator", "current_arms": 10.0, "rpm": 120.0, "gamma_deg": 0.0, "note": "",
            "summary": {"drive": drive, "P_out_W": p}, "mesh": {"sim.drive": drive}}


@pytest.fixture
def make(tmp_path, monkeypatch):
    root = tmp_path / "dies"

    def build(primary_drive):
        _write(root / DIE / "die.yaml", {"name": DIE, "locked": False, "geometry": dict(GEO)})
        _write(root / DIE / f"{CFG}.yaml", {
            "name": CFG, "die": DIE, "role": "generator", "locked": False,
            "geometry_overrides": {k: GEO[k] for k in ("wire_width", "wire_spacing_x", "insulation_thickness",
                                                       "wire_height", "num_wires_per_slot", "motor_length")},
            "winding": {"connection": "4S"}, "materials": dict(MATS), "duties": [_duty(primary_drive)]})
    monkeypatch.setattr(fam, "_DIES_DIR", root)
    monkeypatch.setattr(fam, "_CTX_FILE", tmp_path / ".family_context.json")
    monkeypatch.setattr(fam, "_acting_account", lambda *_a, **_k: ("Vadim", "vadim@example.com"))
    monkeypatch.setattr(fam, "_autolink_new_config", lambda *_a, **_k: None)
    monkeypatch.setattr(fam, "_live_parts", lambda: {})
    monkeypatch.setattr(fam, "may_see_die", lambda *_a, **_k: True)
    from motor_ai_sim.routes import presets as _presets
    monkeypatch.setattr(_presets, "_gen_thumb_svg", lambda *_a, **_k: None)
    live = {"geometry": dict(GEO), "simulation": {"current_a": 10.0, "rpm": 120.0, "mode": "generator"},
            "winding": {"connection": "4S"}, "materials": dict(MATS)}
    monkeypatch.setattr(fam, "_live_cfg", lambda: live)
    return build, root


def _stored(root):
    c = yaml.safe_load((root / DIE / f"{CFG}.yaml").read_text(encoding="utf-8"))
    return c["duties"][0]


def _save(drive, p):
    spec = fam.DutySpec(**{k: v for k, v in _duty(drive, p).items() if k != "name"}, name=DUTY, drive=drive)
    return fam.upsert_duty(fam.DutyCreate(die=DIE, config=CFG, duty=spec), _w={})


def test_save_custom_current_over_a_custom_current_primary_refreshes_the_headline(make):
    build, root = make
    build("custom_current")
    _save("custom_current", 2.0)
    d = _stored(root)
    assert d["summary"]["P_out_W"] == 2.0 and fam._primary_drive(d) == "custom_current"


def test_save_custom_current_beside_a_current_primary_keeps_the_headline(make):
    build, root = make
    build("current")
    _save("custom_current", 2.0)
    d = _stored(root)
    assert d["summary"]["P_out_W"] == 1.0 and fam._primary_drive(d) == "current"


def test_save_current_over_a_current_primary_still_replaces_it(make):
    build, root = make
    build("current")
    _save("current", 3.0)
    assert _stored(root)["summary"]["P_out_W"] == 3.0


def _record(drive):
    return fam.record_duty_result(fam.DutyResult(die=DIE, config=CFG, duty=DUTY, result={"efficiency_pct": 90.0},
                                                 drive=drive), _w={})


def test_result_of_the_same_custom_drive_becomes_the_primary_result(make):
    build, root = make
    build("custom_current")
    d0 = _stored(root)
    d0["result"] = {"efficiency_pct": 50.0}
    c = yaml.safe_load((root / DIE / f"{CFG}.yaml").read_text(encoding="utf-8"))
    c["duties"][0] = d0
    _write(root / DIE / f"{CFG}.yaml", c)
    out = _record("custom_current")
    assert out["primary"] is True and _stored(root)["result"]["efficiency_pct"] == 90.0


def test_result_of_another_drive_stays_beside_a_current_primary(make):
    build, root = make
    build("current")
    c = yaml.safe_load((root / DIE / f"{CFG}.yaml").read_text(encoding="utf-8"))
    c["duties"][0]["result"] = {"efficiency_pct": 50.0}
    _write(root / DIE / f"{CFG}.yaml", c)
    out = _record("custom_current")
    assert out["primary"] is False and _stored(root)["result"]["efficiency_pct"] == 50.0
