"""Configure uses a configuration's OWN cooling (its ``thermal_defaults:`` block), 2026-11-03.

A liquid-jacketed generator (``thermal_defaults: {coolMode: manual, hConv: "1060", ambientT: "41", ...}``) with no full
passport was shown in Configure's default typed air (8 m/s, 25 °C) with every temperature red.  Now:

* ``thermal_settings.configure_cooling`` reduces the block to the film Configure's lumped estimate reads
  (manual h -> ``{"kind": "film", "h_W_m2K", "ambient_C"}``), built like the Thermal tab builds it;
* every Configure preset of such a configuration carries it as ``cooling_defaults``;
* a configuration WITHOUT the block gives a byte-identical preset (no new key), on a tracked die too;
* the stored Configure answers are keyed by it: the stamp covers the configuration files and the code, and the store
  version was bumped so answers stored before the fix are never served.

The web side (the film in the temperature row, "Coolant film" tile) is web/src/lib/__tests__/configureCooling.test.mjs.
Closed-form, no FEM, no solver.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from motor_ai_sim import configure_limits as cl
from motor_ai_sim import thermal_settings as ts

_ROOT = Path(__file__).resolve().parents[1]
_TRACKED = _ROOT / "config" / "dies" / "CIANO14 30_10"

#: a generic jacketed generator, the block exactly as a server configuration stores it (strings)
JACKET = {"coolMode": "manual", "hConv": "1060", "ambientT": "41", "frame": "housed", "boreMode": "none",
          "shaftExtMm": "0"}


def _cfg(name: str, thermal_defaults=None) -> dict:
    doc = {"name": name, "die": "TESTDIE 100", "role": "generator", "applications": ["marine"],
           "geometry_overrides": {"motor_length": 40.0, "num_wires_per_slot": 8, "wire_height": 1.0},
           "winding": {"n_parallel": 1},
           "duties": [{"name": "rated", "mode": "generator", "current_arms": 60.0, "rpm": 2200.0, "gamma_deg": 0.0}]}
    if thermal_defaults is not None:
        doc["thermal_defaults"] = thermal_defaults
    return doc


# ── the reduction ────────────────────────────────────────────────────────────

def test_a_manual_film_is_the_film_at_the_coolant_temperature():
    assert ts.configure_cooling(JACKET) == {"kind": "film", "h_W_m2K": 1060.0, "ambient_C": 41.0, "source": "config"}
    # numbers instead of strings read the same (a hand-written yaml)
    blk = ts.thermal_defaults_of_doc({"thermal_defaults": {"coolMode": "manual", "hConv": 1060, "ambientT": 41}})
    assert ts.configure_cooling(blk)["h_W_m2K"] == 1060.0


def test_the_other_modes_and_what_is_not_a_configure_cooling():
    assert ts.configure_cooling({"coolMode": "air", "airSpeed": "12", "ambientT": "35"}) == {
        "kind": "air", "air_speed_ms": 12.0, "ambient_C": 35.0, "source": "config"}
    liq = ts.configure_cooling({"coolMode": "liquid", "flowLpm": "6", "fluid": "water_glycol_50", "tIn": "45"})
    assert liq == {"kind": "liquid", "flow_lpm": 6.0, "fluid": "water_glycol_50", "ambient_C": 45.0, "source": "config"}
    assert ts.configure_cooling({}) is None and ts.configure_cooling(None) is None
    assert ts.configure_cooling({"coolMode": "robotics"}) is None                     # the application decides a robot
    assert ts.configure_cooling({"coolMode": "manual", "hConv": "0"}) is None         # nothing invented
    assert ts.configure_cooling({"coolMode": "liquid", "flowLpm": "0"}) is None


def test_the_block_reader_is_the_one_the_thermal_tab_uses(monkeypatch):
    from motor_ai_sim.routes import family as fam
    doc = _cfg("G1", {**JACKET, "propellerId": "x", "bogus": "1"})
    monkeypatch.setattr(fam, "config_doc", lambda die, cfg: doc)
    assert ts.config_thermal_defaults("TESTDIE 100", "G1") == JACKET == ts.thermal_defaults_of_doc(doc)


# ── the presets ──────────────────────────────────────────────────────────────

def test_a_jacketed_configuration_carries_its_film_and_a_plain_one_does_not():
    film, plain = cl.presets_for_die([_cfg("G1", JACKET), _cfg("G2")], {"motor_length": 40.0})
    assert film["config"] == "G1" and plain["config"] == "G2"
    assert film["cooling_defaults"] == {"kind": "film", "h_W_m2K": 1060.0, "ambient_C": 41.0, "source": "config"}
    assert "cooling_defaults" not in plain
    # the film is the ONLY difference the block makes
    rest = {k: v for k, v in film.items() if k not in ("cooling_defaults", "config")}
    assert rest == {k: v for k, v in plain.items() if k != "config"}


#: every key a preset carried before 2026-11-03 - a configuration without ``thermal_defaults`` keeps exactly these
_PRE_FIX_KEYS = {"config", "die", "knobs", "duty", "duty_points", "battery", "speed", "device", "pwm_variants",
                 "applications", "application", "drive_variant", "drive_variant_suggested"}


def test_a_tracked_die_without_thermal_defaults_is_byte_identical():
    die_geo = (yaml.safe_load((_TRACKED / "die.yaml").read_text(encoding="utf-8")) or {}).get("geometry") or {}
    docs = [yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted(_TRACKED.glob("*.yaml")) if p.name != "die.yaml"]
    assert docs and not any("thermal_defaults" in d for d in docs)
    before = copy.deepcopy(docs)
    presets = cl.presets_for_die(docs, die_geo)
    assert docs == before                                                         # read only
    for pr in presets:
        assert set(pr) == _PRE_FIX_KEYS, sorted(set(pr) ^ _PRE_FIX_KEYS)
        assert cl.preset_cooling(next(d for d in docs if d["name"] == pr["config"])) is None
    # the same documents with the reduction switched off give the very same JSON
    orig = cl.preset_cooling
    try:
        cl.preset_cooling = lambda doc: None
        off = cl.presets_for_die(docs, die_geo)
    finally:
        cl.preset_cooling = orig
    assert json.dumps(presets, sort_keys=True) == json.dumps(off, sort_keys=True)


# ── the stored answers ───────────────────────────────────────────────────────

def test_the_stored_answers_are_keyed_by_the_cooling(tmp_path, monkeypatch):
    from motor_ai_sim import workspace as ws
    from motor_ai_sim.routes import catalog as cat
    die = tmp_path / "TESTDIE 100"
    die.mkdir()
    (die / "die.yaml").write_text(yaml.safe_dump({"name": "TESTDIE 100", "geometry": {"motor_length": 40.0}}),
                                  encoding="utf-8")
    cfg = die / "G1.yaml"
    cfg.write_text(yaml.safe_dump(_cfg("G1")), encoding="utf-8")
    monkeypatch.setattr(ws, "iter_dies", lambda **kw: [{"name": "TESTDIE 100", "die": "TESTDIE 100", "dir": str(die)}])
    plain = cat._configure_stamp(None)
    assert cat._configure_stamp(None) == plain                                    # stable
    cfg.write_text(yaml.safe_dump(_cfg("G1", JACKET)), encoding="utf-8")
    film = cat._configure_stamp(None)
    assert film != plain                                                          # the film is a new answer
    # the code that reduces the block is part of the stamp, and answers stored before the fix are stale
    assert "thermal_settings.py" in cat._CONFIGURE_CODE
    assert cat.CONFIGURE_STORE_VERSION not in ("configure-1", "configure-2")


def test_the_anchor_carries_the_records_film():
    from motor_ai_sim import configure_anchor as anc
    entry = {"coupled": {"mode": "steady", "converged": True,
                         "em": {"P_loss_total_W": 1500.0, "P_stranded_W": 1000.0, "P_solid_W": 50.0}},
             "thermal": {"point": {"rpm": 2200.0, "I_phase_rms": 60.0, "cooling_mode": "manual", "h_conv": 1060.0,
                                   "ambient_temp": 41.0},
                         "components": {"winding": {"max": 180.0}, "magnet": {"max": 95.0}, "stator": {"avg": 70.0}},
                         "cooling": {"outer": {"mode": "manual", "h_conv": 1060.0, "t_sink_c": 41.0}}}}
    a = anc.anchor_from_record("rated", entry)
    assert a["cooling_mode"] == "manual" and a["h_W_m2K"] == 1060.0
    t = anc.anchored_temperatures(a, 1000.0, 50.0, 1500.0, 1060.0, a["h_W_m2K"], 41.0)
    assert t["winding"] == pytest.approx(180.0)                                   # the stored point IS the record
