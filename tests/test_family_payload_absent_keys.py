"""``/api/family/payload`` describes the WHOLE machine.

A die saved before a knob existed carries no key for it, and the frontend's
``PUT /api/geometry`` merges the payload over whatever is loaded — so the
previous machine's value survived.  Loading any pre-sleeve die from the sleeved
Ø200 kept its 2.5 mm band and was refused by the sleeve-vs-gap rule (user,
2026-09-08: "хочу загрузить G2-L40, а он не грузится").  The payload now says
what absence means.
"""
from __future__ import annotations

import pathlib


import pytest
import yaml

from motor_ai_sim.routes import family as fam

DIE = "OLDDIE 40"
CFG = "L40"


@pytest.fixture
def dies(monkeypatch, tmp_path):
    root = tmp_path / "dies"
    (root / DIE).mkdir(parents=True)
    (root / DIE / "die.yaml").write_text(yaml.safe_dump({
        "name": DIE, "locked": True,
        # No sleeve_thickness, no wire_parallel, no wire_split — a 2026-08 die.
        "geometry": {"stator_diameter": 40.0, "air_gap": 0.65, "num_seg": 2,
                     "num_slots_per_segment": 6, "num_poles_per_segment": 7,
                     "wire_width": 2.5, "wire_height": 0.6,
                     "num_wires_per_slot": 8},
    }, sort_keys=False), encoding="utf-8")
    (root / DIE / f"{CFG}.yaml").write_text(yaml.safe_dump({
        "name": CFG, "die": DIE,
        # An explicit null (older saves wrote it) must read as absent too.
        "geometry_overrides": {"motor_length": 40, "wire_parallel": None},
        "winding": {"connection": "4S", "n_parallel": 1, "n_series": 4},
        "duties": [],
    }, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(fam, "_DIES_DIR", root)
    monkeypatch.setattr(fam, "_require_die_access", lambda *a, **k: None)
    return root


def test_absent_keys_are_spelled_out(dies):
    out = fam.payload(DIE, CFG)
    geo = out["geometry"]
    assert geo["sleeve_thickness"] == 0.0
    assert geo["wire_parallel"] == 1
    assert geo["wire_split"] == 1
    # …and what the die DID say is untouched.
    assert geo["air_gap"] == 0.65 and geo["motor_length"] == 40


def test_the_lock_accepts_what_absence_means_and_nothing_else(dies, monkeypatch):
    """Loading the locked configuration PUTs the payload back — the lock must
    read `sleeve_thickness: 0` / `wire_parallel: 1` as the canonical values of
    a die that never carried the keys, and still refuse a real band."""
    monkeypatch.setattr(fam, "_read_ctx", lambda: {"die": DIE, "config": CFG})
    (dies / DIE / f"{CFG}.yaml").write_text(yaml.safe_dump({
        "name": CFG, "die": DIE, "locked": True,
        "geometry_overrides": {"motor_length": 40, "wire_parallel": None},
        "winding": {"connection": "4S", "n_parallel": 1, "n_series": 4},
        "duties": [],
    }, sort_keys=False), encoding="utf-8")
    geo = fam.payload(DIE, CFG)["geometry"]
    assert fam.geometry_lock_check(dict(geo)) is None      # its own load passes
    refusal = fam.geometry_lock_check({**geo, "sleeve_thickness": 0.3})
    assert refusal is not None
    assert [b["field"] for b in refusal["invalid_parameters"]] == ["sleeve_thickness"]


def test_the_liner_and_the_enamel_are_spelled_out_too(dies):
    """2026-09-09: the Ø200's Al2O3 ceramic liner (tried on 2026-09-07/08) sat
    on every motor loaded after it, because a load carried only the three parts
    a build is defined by and the shared assignment kept the rest.  The payload
    now names the project's standard for the parts the configuration does not."""
    out = fam.payload(DIE, CFG)
    assert out["materials"]["slot_insulation"] == "Nomex"
    assert out["materials"]["wire_insulation"] == "polyimide"
    # …and a configuration that DOES name one keeps its own.
    p = dies / DIE / f"{CFG}.yaml"
    d = yaml.safe_load(p.read_text(encoding="utf-8"))
    d["materials"] = {"magnet": "N52UH_150C", "slot_insulation": "Al2O3"}
    p.write_text(yaml.safe_dump(d, sort_keys=False), encoding="utf-8")
    out2 = fam.payload(DIE, CFG)
    assert out2["materials"]["slot_insulation"] == "Al2O3"
    assert out2["materials"]["magnet"] == "N52UH_150C"
    assert out2["materials"]["wire_insulation"] == "polyimide"


def test_a_die_that_carries_the_key_keeps_its_own_value(dies):
    p = dies / DIE / "die.yaml"
    d = yaml.safe_load(p.read_text(encoding="utf-8"))
    d["geometry"]["sleeve_thickness"] = 0.3
    d["geometry"]["wire_parallel"] = 2
    p.write_text(yaml.safe_dump(d, sort_keys=False), encoding="utf-8")
    geo = fam.payload(DIE, CFG)["geometry"]
    assert geo["sleeve_thickness"] == 0.3
    # the configuration's explicit null is an override that says nothing: the
    # DIE's value stands, and only a key nobody carries takes the default.
    assert geo["wire_parallel"] == 2


# ---------------------------------------------------------------------------
# …and what a configuration SAVES (2026-09-09)
# ---------------------------------------------------------------------------
# User: *"почему изоляция в этой машине опять Nomex, я же менял её на Al2O3"*.
# The save kept three material keys — magnet, stator_core, rotor_core — so a
# slot liner, a wire enamel, a conductor or a shaft grade chosen in Materials
# had nowhere to live, and the next activation filled the absent keys from
# `_ABSENT_MATERIALS` (Nomex, polyimide).  The two rules are complements: what a
# configuration NAMES it keeps; only what it does not name falls back.

def test_the_save_keeps_every_solid_part_s_material():
    """The list is the claim: a machine is not three parts."""
    keys = set(fam._SAVED_MATERIAL_KEYS)
    assert {"magnet", "stator_core", "rotor_core"} <= keys, "the old three"
    # …the four the user lost, by name
    assert {"slot_insulation", "wire_insulation", "slot", "shaft"} <= keys
    assert "sleeve" in keys
    # the air placeholders are NOT saved: always air, three dead lines per file
    assert not keys & {"air_gap", "in_band", "out_band"}


def test_the_saved_keys_and_the_absent_defaults_agree():
    """Every part that has a fallback must also be saveable — otherwise the
    fallback is not a default, it is an override that cannot be turned off."""
    assert set(fam._ABSENT_MATERIALS) <= set(fam._SAVED_MATERIAL_KEYS)
