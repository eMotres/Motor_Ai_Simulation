"""A released die context is never a dead end (2026-09-20).

12:47:08 that day: the owner had optimised the live machine and could not save
it.  A single-key ``PUT /api/geometry`` (``num_poles_per_segment`` 7 → 8, from
the Geometry table) had made the live machine a different lamination from the
active die 'CIANO14 50 edited', the identity guard released the context, and
the header strip offered only "press ▶ in Motors to load one" — which would
have overwritten the optimised geometry.  Second time ("опять та же самая
проблема — я всё оптимизировал, а сохранить не могу").

What is pinned here:

* ``release_context`` records WHICH configuration and duty were released, and
  ``/context`` turns the released record + the live machine into offers: the
  identity diff ("poles/segment 7 → 8"), whether the live machine is that die
  again, and — for a record written before this existed, the owner's own
  file — the single configuration / single duty of the die, filled in;
* ``POST /save_as_new_die`` keeps the work: a NEW unlocked die from the live
  geometry with provenance, one configuration from the live build, one duty at
  the live point, and the context made active on the new triple; the released
  die is untouched; a taken name is refused;
* the die-defining keys are ONE list (``DIE_IDENTITY_KEYS``), served by
  ``/context`` and enforced by the optimizer / sweep routes: a request varying
  one of them under an active die is refused with 422 and the reason, unless
  ``allow_new_lamination`` is set.

Everything runs against a throwaway catalog under the pytest tmp area with the
live config replaced by a dict — nothing here reads or writes ``config/``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException, Response

from motor_ai_sim.routes import family as fam

DIE = "CIANO14 50 edited"
CFG = "L15"
DUTY = "rated"

DIE_GEO = {"stator_diameter": 50, "num_seg": 2, "num_slots_per_segment": 6,
           "num_poles_per_segment": 7, "num_slots": 12, "num_poles": 14,
           "motor_length": 15, "magnet_height": 7.0, "tooth_width": 4.2,
           "wire_height": 1.0, "num_wires_per_slot": 8}

# The owner's ``.family_context.json`` at 12:47:08, byte for byte in content:
# no released_config / released_duty (the record predates them).
OWNER_RECORD = {"die": None, "config": None, "duty": None,
                "released_from": DIE,
                "reason": "live machine is not this die: num_poles_per_segment 7 → 8",
                "at": "2026-09-20T12:47:08"}


def _live(poles_per_segment: int = 8, **geo_extra) -> dict:
    geo = dict(DIE_GEO)
    geo["num_poles_per_segment"] = poles_per_segment
    geo["num_poles"] = geo["num_seg"] * poles_per_segment
    geo.update(geo_extra)
    return {
        "geometry": geo,
        "simulation": {"current_a": 60.0, "max_current": 60.0, "rpm": 20000.0,
                       "gamma_deg": 10.0, "mode": "motor", "daxis_deg": 25.7,
                       "end_winding_factor": 1.3},
        "winding": {"connection": "star", "star_delta": "star", "layers": 2},
        "materials": {"magnet": "N52UH", "stator_core": "B10AHV900M",
                      "rotor_core": "B10AHV900M", "slot_insulation": "Nomex"},
    }


@pytest.fixture
def dies(tmp_path, monkeypatch):
    root = tmp_path / "dies"
    (root / DIE).mkdir(parents=True)
    (root / DIE / "die.yaml").write_text(yaml.safe_dump({
        "name": DIE, "locked": False, "created": "2026-09-19T15:20:00",
        "geometry": dict(DIE_GEO)}, sort_keys=False), encoding="utf-8")
    (root / DIE / f"{CFG}.yaml").write_text(yaml.safe_dump({
        "name": CFG, "die": DIE, "role": "motor",
        "geometry_overrides": {"motor_length": 15, "wire_height": 1.0,
                               "num_wires_per_slot": 8},
        "winding": {"connection": "star"},
        "materials": {"magnet": "N52UH"},
        "duties": [{"name": DUTY, "mode": "motor", "current_arms": 55.0,
                    "rpm": 19000.0, "gamma_deg": 8.0, "note": ""}],
    }, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(fam, "_DIES_DIR", root)
    monkeypatch.setattr(fam, "_CTX_FILE", tmp_path / ".family_context.json")
    monkeypatch.setattr(fam, "_live_parts", lambda: {})
    # every caller sees every die in this throwaway catalog
    monkeypatch.setattr(fam, "may_see_die", lambda *_a, **_k: True)
    # the card thumbnail is cosmetic and slow — never part of this feature
    from motor_ai_sim.routes import presets as _presets
    monkeypatch.setattr(_presets, "_gen_thumb_svg", lambda *_a, **_k: None)
    return root


@pytest.fixture
def live(monkeypatch):
    """``live(poles_per_segment)`` installs that machine as the live config."""
    def _set(poles_per_segment: int = 8, **geo_extra):
        cfg = _live(poles_per_segment, **geo_extra)
        monkeypatch.setattr(fam, "_live_cfg", lambda: cfg)
        return cfg
    return _set


def _ctx_file() -> Path:
    return fam._ctx_file()


def _write_active():
    _ctx_file().write_text(json.dumps({"die": DIE, "config": CFG, "duty": DUTY,
                                       "at": "2026-09-20T12:00:00"}),
                           encoding="utf-8")


def _context() -> dict:
    return fam.context(Response(), authorization=None)


# ── the released record ──────────────────────────────────────────────────────

def test_release_context_records_the_configuration_and_the_duty(dies):
    _write_active()
    assert fam.release_context("test release") == DIE
    raw = json.loads(_ctx_file().read_text(encoding="utf-8"))
    assert raw["die"] is None
    assert raw["released_from"] == DIE
    assert raw["released_config"] == CFG
    assert raw["released_duty"] == DUTY
    assert raw["reason"] == "test release"


def test_the_identity_guard_release_carries_config_and_duty(dies, live):
    """The geometry save that turned 7 into 8 poles/segment: the guard refuses
    the sync, releases, and the record names what was loaded."""
    live(8)
    _write_active()
    saved = dict(DIE_GEO, num_poles_per_segment=8, num_poles=16)
    assert fam.sync_active_die_geometry(saved, prev_geo=dict(DIE_GEO)) is None
    raw = json.loads(_ctx_file().read_text(encoding="utf-8"))
    assert raw["die"] is None and raw["released_from"] == DIE
    assert raw["released_config"] == CFG and raw["released_duty"] == DUTY
    assert "num_poles_per_segment 7 → 8" in raw["reason"]
    # the die snapshot is untouched
    d = yaml.safe_load((dies / DIE / "die.yaml").read_text(encoding="utf-8"))
    assert d["geometry"]["num_poles_per_segment"] == 7


# ── /context on a released record ────────────────────────────────────────────

def test_context_names_the_identity_diff_and_fills_the_single_config_and_duty(dies, live):
    """The owner's exact record (no config/duty in it) + the live 12s16p
    machine → the strip gets everything it needs for the three offers."""
    live(8)
    _ctx_file().write_text(json.dumps(OWNER_RECORD), encoding="utf-8")
    c = _context()
    assert c["active"] is False and c["can_write"] is True
    assert c["released_from"] == DIE
    assert c["released_config"] == CFG          # filled: the die's only configuration
    assert c["released_duty"] == DUTY           # filled: that configuration's only duty
    assert c["reason"] == OWNER_RECORD["reason"] and c["at"] == OWNER_RECORD["at"]
    assert c["die_exists"] is True
    assert c["die_keys"] == list(fam.DIE_IDENTITY_KEYS)
    assert c["die_diffs"] == [{"key": "num_poles_per_segment",
                               "label": "poles/segment", "die": 7, "live": 8}]
    assert c["live_is_die"] is False
    assert c["live_topology"] == {"stator_diameter": 50, "slots": 12, "poles": 16,
                                  "motor_length": 15}


def test_context_says_when_the_live_machine_is_that_die_again(dies, live):
    """Reverting the edit (or a shape-only change) → no identity diff: the
    offers are then "re-attach" / "save as new configuration"."""
    live(7, tooth_width=4.5)
    _ctx_file().write_text(json.dumps(OWNER_RECORD), encoding="utf-8")
    c = _context()
    assert c["active"] is False
    assert c["die_diffs"] == [] and c["live_is_die"] is True


def test_context_does_not_fill_a_configuration_when_the_die_has_several(dies, live):
    live(8)
    (dies / DIE / "L20.yaml").write_text(yaml.safe_dump({
        "name": "L20", "die": DIE, "duties": []}), encoding="utf-8")
    _ctx_file().write_text(json.dumps(OWNER_RECORD), encoding="utf-8")
    c = _context()
    assert c["released_config"] is None and c["released_duty"] is None
    assert c["die_diffs"][0]["key"] == "num_poles_per_segment"


def test_context_with_no_record_at_all_is_plain(dies, live):
    live(8)
    c = _context()
    assert c == {"active": False, "can_write": True}


# ── save as NEW die ──────────────────────────────────────────────────────────

def test_save_as_new_die_keeps_the_live_machine_and_activates_it(dies, live):
    live(8)
    _ctx_file().write_text(json.dumps(OWNER_RECORD), encoding="utf-8")
    out = fam.save_as_new_die(fam.SaveAsNewDie(name="CIANO14 50 12s16p"), _w={})
    assert out["ok"] is True
    assert out["die"] == "CIANO14 50 12s16p"
    assert out["config"] == CFG and out["duty"] == DUTY       # defaults from the record
    assert out["topology"] == {"stator_diameter": 50, "num_slots": 12, "num_poles": 16}
    # the die: the LIVE geometry, unlocked, with provenance
    d = yaml.safe_load((dies / out["die"] / "die.yaml").read_text(encoding="utf-8"))
    assert d["locked"] is False
    assert d["geometry"]["num_poles_per_segment"] == 8 and d["geometry"]["num_poles"] == 16
    assert d["geometry"]["tooth_width"] == 4.2
    assert d["daxis_deg"] == 25.7
    assert d["derived_from"] == {"die": DIE, "config": CFG, "duty": DUTY,
                                 "reason": OWNER_RECORD["reason"],
                                 "changed": ["num_poles_per_segment 7 → 8"]}
    # the configuration: the live build (POST /config's own snapshot)
    c = yaml.safe_load((dies / out["die"] / f"{CFG}.yaml").read_text(encoding="utf-8"))
    assert c["die"] == out["die"] and c["role"] == "motor"
    assert c["geometry_overrides"]["motor_length"] == 15
    assert c["geometry_overrides"]["wire_height"] == 1.0
    assert c["winding"]["connection"] == "star"
    # every saved material key the live machine names (the duty save that
    # DEFINES a fresh build adopts the full map — the 2026-09-09 rule)
    assert c["materials"] == {"magnet": "N52UH", "stator_core": "B10AHV900M",
                              "rotor_core": "B10AHV900M", "slot_insulation": "Nomex"}
    # the duty: the LIVE operating point, not the released duty's
    (duty,) = c["duties"]
    assert duty["name"] == DUTY and duty["mode"] == "motor"
    assert (duty["current_arms"], duty["rpm"], duty["gamma_deg"]) == (60.0, 20000.0, 10.0)
    assert duty["star_delta"] == "star"
    # the context is ACTIVE on the new triple, so "Save to duty" works next
    ctx = json.loads(_ctx_file().read_text(encoding="utf-8"))
    assert (ctx["die"], ctx["config"], ctx["duty"]) == (out["die"], CFG, DUTY)
    c2 = _context()
    assert c2["active"] is True and c2["die"] == out["die"]
    assert c2["die_locked"] is False
    # the released die is exactly as it was
    d0 = yaml.safe_load((dies / DIE / "die.yaml").read_text(encoding="utf-8"))
    assert d0["geometry"]["num_poles_per_segment"] == 7
    c0 = yaml.safe_load((dies / DIE / f"{CFG}.yaml").read_text(encoding="utf-8"))
    assert c0["duties"][0]["current_arms"] == 55.0


def test_save_as_new_die_refuses_a_taken_name(dies, live):
    live(8)
    _ctx_file().write_text(json.dumps(OWNER_RECORD), encoding="utf-8")
    with pytest.raises(HTTPException) as ei:
        fam.save_as_new_die(fam.SaveAsNewDie(name=DIE), _w={})
    assert ei.value.status_code == 409
    # nothing was written, the record still stands
    assert json.loads(_ctx_file().read_text(encoding="utf-8")) == OWNER_RECORD


def test_save_as_new_die_works_without_any_released_record(dies, live):
    """No record (a machine built from scratch, or a preset): the configuration
    is named after the live stack and the duty 'rated'; no provenance."""
    live(8, motor_length=20)
    out = fam.save_as_new_die(fam.SaveAsNewDie(name="Fresh 12s16p", mode="generator"),
                              _w={})
    assert out["config"] == "L20" and out["duty"] == "rated"
    d = yaml.safe_load((dies / "Fresh 12s16p" / "die.yaml").read_text(encoding="utf-8"))
    assert "derived_from" not in d
    c = yaml.safe_load((dies / "Fresh 12s16p" / "L20.yaml").read_text(encoding="utf-8"))
    assert c["role"] == "generator" and c["duties"][0]["mode"] == "generator"


def test_save_as_new_die_honours_explicit_names_and_the_l_rule(dies, live):
    live(8)
    _ctx_file().write_text(json.dumps(OWNER_RECORD), encoding="utf-8")
    # "L12" on a 15 mm stack is corrected to L15 (the standing L-name rule)
    out = fam.save_as_new_die(fam.SaveAsNewDie(name="X 16p", config="L12",
                                               duty="peak"), _w={})
    assert out["config"] == "L15" and out["duty"] == "peak"
    with pytest.raises(HTTPException) as ei:
        fam.save_as_new_die(fam.SaveAsNewDie(name="Y 16p", mode="hybrid"), _w={})
    assert ei.value.status_code == 422


# ── the die-defining gate for sweeps / optimizations ─────────────────────────

def test_die_identity_keys_are_the_four_lamination_keys():
    assert fam.DIE_IDENTITY_KEYS == ("stator_diameter", "num_seg",
                                     "num_slots_per_segment", "num_poles_per_segment")
    assert fam.die_defining_variables(["tooth_width", "num_poles_per_segment",
                                       "gamma_deg", "stator_diameter"]) == \
        ["num_poles_per_segment", "stator_diameter"]


def test_refuse_die_defining_variables_only_under_an_active_die(dies):
    # no context at all → nothing to protect
    fam.refuse_die_defining_variables(["num_poles_per_segment"], False)
    # a RELEASED context is not an active die either
    _ctx_file().write_text(json.dumps(OWNER_RECORD), encoding="utf-8")
    fam.refuse_die_defining_variables(["num_poles_per_segment"], False)
    # active die + die-defining variable → 422 with the reason
    _write_active()
    fam.refuse_die_defining_variables(["tooth_width", "magnet_height"], False)
    with pytest.raises(HTTPException) as ei:
        fam.refuse_die_defining_variables(["tooth_width", "num_poles_per_segment"], False)
    assert ei.value.status_code == 422
    assert "die-defining" in ei.value.detail
    assert "num_poles_per_segment" in ei.value.detail and DIE in ei.value.detail
    assert "allow new lamination" in ei.value.detail
    # …unless the caller explicitly allows a new lamination
    fam.refuse_die_defining_variables(["num_poles_per_segment"], True)


def test_scan_and_descent_routes_refuse_a_die_defining_variable_with_422(dies):
    """Through the HTTP routes, BEFORE any state is touched: the sweep study's
    /scan and the optimizer's /descent/start."""
    from fastapi.testclient import TestClient
    from motor_ai_sim.api import app
    from motor_ai_sim.routes import optimization as opt

    _write_active()
    client = TestClient(app)
    var = {"name": "num_poles_per_segment", "min": 7, "max": 8, "mode": "sweep", "step": 1}
    r = client.post("/api/optimization/scan",
                    json={"variables": [var], "operating_points": [
                        {"gamma_deg": 0, "current_a": 60, "rpm": 20000}]})
    assert r.status_code == 422, r.text
    assert "die-defining" in r.json()["detail"] and DIE in r.json()["detail"]
    assert opt._scan_state.get("running") is not True
    r = client.post("/api/optimization/descent/start",
                    json={"variables": [var],
                          "operating_point": {"gamma_deg": 0, "current_a": 60, "rpm": 20000}})
    assert r.status_code == 422, r.text
    assert "allow new lamination" in r.json()["detail"]
    assert opt._descent_state.get("running") is not True
    r = client.post("/api/optimization/run", json={"variables": [var]})
    assert r.status_code == 422, r.text


def test_variables_route_flags_the_die_defining_ones():
    from motor_ai_sim.routes import optimization as opt
    rows = opt.list_optimizable_variables()["variables"]
    by = {r["name"]: r for r in rows}
    if "stator_diameter" in by:
        assert by["stator_diameter"]["die_defining"] is True
    for n in ("tooth_width", "magnet_height", "gamma_deg"):
        if n in by:
            assert by[n].get("die_defining", False) is False
