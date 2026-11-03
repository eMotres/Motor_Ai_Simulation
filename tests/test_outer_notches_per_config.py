# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""`stator_outer_notches` is a CONFIGURATION option on a common die (owner
2026-11-03, after #563 made it die-defining).

One die, notched or smooth per configuration: the flag lives in the
configuration's ``geometry_overrides`` (only when it is OFF), a die lock never
refuses it, switching it never transitions to another die and never touches
die.yaml.  ON and OFF builds still never share a result: the configuration's
build signature, the geometry signature and every cache key carry it.

Throwaway catalog under the pytest tmp area; nothing reads or writes ``config/``.
Geometry only — no solve.
"""
from __future__ import annotations

import contextlib
import io
import json
import logging
from pathlib import Path

import pytest
import yaml

from motor_ai_sim.routes import family as fam

ROOT = Path(__file__).resolve().parents[1]
DIE = "Common die"


def _tracked_geo() -> dict:
    with open(ROOT / "config" / "dies" / "CIANO14 30_10" / "die.yaml", encoding="utf-8") as f:
        return dict(yaml.safe_load(f)["geometry"])


GEO = _tracked_geo()


def _cfg_doc(name: str, **ov) -> dict:
    base = {k: GEO[k] for k in fam.FREE_GEO_KEYS if k in GEO}
    base.update(ov)
    return {"name": name, "die": DIE, "role": "motor", "geometry_overrides": base,
            "duties": [{"name": "rated", "mode": "motor", "current_arms": 10.0,
                        "rpm": 3000.0, "gamma_deg": 10.0, "note": ""}]}


@pytest.fixture
def dies(tmp_path, monkeypatch):
    root = tmp_path / "dies"
    (root / DIE).mkdir(parents=True)
    (root / DIE / "die.yaml").write_text(yaml.safe_dump(
        {"name": DIE, "locked": True, "geometry": dict(GEO)}, sort_keys=False), encoding="utf-8")
    (root / DIE / "L10.yaml").write_text(yaml.safe_dump(_cfg_doc("L10"), sort_keys=False),
                                         encoding="utf-8")
    (root / DIE / "L10 smooth.yaml").write_text(yaml.safe_dump(
        _cfg_doc("L10 smooth", stator_outer_notches=False), sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(fam, "_DIES_DIR", root)
    monkeypatch.setattr(fam, "_CTX_FILE", tmp_path / ".family_context.json")
    monkeypatch.setattr(fam, "_acting_account", lambda *_a, **_k: ("Tester", "t@example.com"))
    monkeypatch.setattr(fam, "_autolink_new_config", lambda *_a, **_k: None)
    from motor_ai_sim.routes import presets as _presets
    monkeypatch.setattr(_presets, "_gen_thumb_svg", lambda *_a, **_k: None)
    return root


def _activate(cfg: str) -> None:
    fam._ctx_file().write_text(json.dumps({"die": DIE, "config": cfg, "duty": "rated"}),
                               encoding="utf-8")


def _bytes(root) -> dict:
    return {p.name: p.read_bytes() for p in sorted((root / DIE).glob("*.yaml"))}


def _dies(root) -> set:
    return {p.name for p in root.iterdir() if not p.name.startswith(".")}


def _cfg(root, name: str) -> dict:
    return yaml.safe_load((root / DIE / f"{name}.yaml").read_text(encoding="utf-8"))


# ── it is a configuration key ──────────────────────────────────────────────

def test_the_flag_is_a_configuration_key_free_under_a_die_lock():
    assert "stator_outer_notches" in fam.FREE_GEO_KEYS
    assert "stator_outer_notches" in fam.EDITABLE_UNDER_DIE_LOCK
    assert "stator_outer_notches" not in fam.DIE_IDENTITY_KEYS
    assert not [d for d in fam.die_shape_diffs(GEO, {**GEO, "stator_outer_notches": False})
                if d["key"] == "stator_outer_notches"]


def test_locked_die_accepts_the_override_without_a_423(dies):
    _activate("L10")
    # die locked, configuration unlocked: switching the notches off is allowed
    assert fam.geometry_lock_check({"stator_outer_notches": False}) is None
    assert fam.geometry_lock_check({"stator_outer_notches": True}) is None
    # a locked CONFIGURATION still holds its own value — and loads it
    for name in ("L10", "L10 smooth"):
        c = _cfg(dies, name)
        c["locked"] = True
        (dies / DIE / f"{name}.yaml").write_text(yaml.safe_dump(c, sort_keys=False),
                                                 encoding="utf-8")
    _activate("L10 smooth")
    assert fam.geometry_lock_check({"stator_outer_notches": False}) is None
    bad = fam.geometry_lock_check({"stator_outer_notches": True})
    assert bad and bad["invalid_parameters"][0]["field"] == "stator_outer_notches"
    assert "configuration" in bad["invalid_parameters"][0]["reason"]
    _activate("L10")
    assert fam.geometry_lock_check({"stator_outer_notches": True}) is None
    assert fam.geometry_lock_check({"stator_outer_notches": False}) is not None


def test_saving_the_override_on_a_locked_die_never_transitions(dies):
    """The geometry PUT's die sync: a live machine with the notches off is still
    this die — die.yaml is untouched, no die is minted, the context stays."""
    _activate("L10")
    before, names = _bytes(dies), _dies(dies)
    live = {**GEO, "stator_outer_notches": False}
    assert fam.sync_active_die_geometry(live, prev_geo=dict(GEO)) is None
    assert _bytes(dies) == before and _dies(dies) == names
    assert json.loads(fam._ctx_file().read_text(encoding="utf-8"))["die"] == DIE


def test_unlocked_shared_die_does_not_move_either(dies):
    d = yaml.safe_load((dies / DIE / "die.yaml").read_text(encoding="utf-8"))
    d["locked"] = False
    (dies / DIE / "die.yaml").write_text(yaml.safe_dump(d, sort_keys=False), encoding="utf-8")
    _activate("L10")
    before, names = _bytes(dies), _dies(dies)
    assert fam.sync_active_die_geometry({**GEO, "stator_outer_notches": False},
                                        prev_geo=dict(GEO)) is None
    assert _bytes(dies) == before and _dies(dies) == names
    ctx = json.loads(fam._ctx_file().read_text(encoding="utf-8"))
    assert ctx["die"] == DIE and "transitioned_from" not in ctx


# ── ON and OFF builds never share a result ──────────────────────────────────

def test_configuration_signatures_differ(dies):
    die_doc = yaml.safe_load((dies / DIE / "die.yaml").read_text(encoding="utf-8"))
    on, off = _cfg(dies, "L10"), _cfg(dies, "L10 smooth")
    assert fam._build_sig(die_doc, on) != fam._build_sig(die_doc, off)
    # a configuration without the override signs exactly as it did before
    # the flag existed (the key is simply absent)
    assert "stator_outer_notches" not in on["geometry_overrides"]
    from motor_ai_sim.routes.presets import _geo_sig
    g_on = {**die_doc["geometry"], **on["geometry_overrides"]}
    g_off = {**die_doc["geometry"], **off["geometry_overrides"]}
    assert _geo_sig(g_on) == _geo_sig(die_doc["geometry"])
    assert _geo_sig(g_off) != _geo_sig(g_on) and "stator_outer_notches:0" in _geo_sig(g_off)


def test_duty_solved_with_the_other_setting_is_flagged(dies):
    die_doc = yaml.safe_load((dies / DIE / "die.yaml").read_text(encoding="utf-8"))
    duty = {"geometry": {**GEO, "stator_outer_notches": False}}
    d = [x for x in fam.duty_geometry_diff(die_doc, _cfg(dies, "L10"), duty)
         if x["key"] == "stator_outer_notches"]
    assert d and d[0]["scope"] == "winding" and d[0]["live"] is True
    assert not [x for x in fam.duty_geometry_diff(die_doc, _cfg(dies, "L10 smooth"), duty)
                if x["key"] == "stator_outer_notches"]


# ── mass, contact and mesher read the MERGED die + configuration ────────────

def _merged(cfg_doc: dict) -> dict:
    g = dict(GEO)
    g.update({k: v for k, v in cfg_doc["geometry_overrides"].items() if v is not None})
    return g


def test_merged_geometry_drives_section_contact_and_mesher(dies):
    from motor_ai_sim.cadquery_geometry import CadQueryMotor, stator_od_contact
    from motor_ai_sim.masses import _area_key
    on, off = _merged(_cfg(dies, "L10")), _merged(_cfg(dies, "L10 smooth"))
    logging.disable(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            m_on = CadQueryMotor(); m_on.set_parameters(on)
            p_on = m_on.get_2d_polygons(0.0)
            m_off = CadQueryMotor(); m_off.set_parameters(off)
            p_off = m_off.get_2d_polygons(0.0)
            m_die = CadQueryMotor(); m_die.set_parameters(dict(GEO))
            p_die = m_die.get_2d_polygons(0.0)
    finally:
        logging.disable(logging.NOTSET)
    # the default configuration builds the die's own stator, byte for byte
    assert p_on["stator"].wkb == p_die["stator"].wkb
    # the override reaches the builder, the mesher's routing flag and the mass key
    assert p_on["stator_outer_notches"] is True and p_off["stator_outer_notches"] is False
    assert p_off["stator"].area > p_on["stator"].area
    assert _area_key(on) != _area_key(off)
    # and the thermal sleeve contact
    assert stator_od_contact(off, stator=p_off["stator"])["fraction"] == 1.0
    assert stator_od_contact(on, stator=p_on["stator"])["fraction"] == pytest.approx(0.4826, abs=1e-4)


def test_duty_save_on_a_locked_die_writes_the_override_to_the_configuration(dies, monkeypatch):
    """The real save path: the Geometry tab switched the notches off on the
    live machine, the duty is saved — the CONFIGURATION takes the override,
    die.yaml and the other configuration are byte-for-byte unchanged, no die
    is minted and nothing transitions."""
    die_before = (dies / DIE / "die.yaml").read_bytes()
    other_before = (dies / DIE / "L10 smooth.yaml").read_bytes()
    names = _dies(dies)
    live = {"geometry": {**GEO, "stator_outer_notches": False},
            "simulation": {"current_a": 10.0, "rpm": 3000.0, "gamma_deg": 10.0,
                           "mode": "motor"},
            "winding": {}, "materials": {}}
    monkeypatch.setattr(fam, "_live_cfg", lambda: live)
    monkeypatch.setattr(fam, "_live_parts", lambda: {})
    monkeypatch.setattr(fam, "may_see_die", lambda *_a, **_k: True)
    _activate("L10")
    spec = fam.DutySpec(name="rated", mode="motor", from_current=True)
    out = fam.upsert_duty(fam.DutyCreate(die=DIE, config="L10", duty=spec), _w={})
    assert not out.get("transitioned")
    assert out.get("die", DIE) == DIE
    assert (dies / DIE / "die.yaml").read_bytes() == die_before
    assert (dies / DIE / "L10 smooth.yaml").read_bytes() == other_before
    assert _dies(dies) == names
    assert _cfg(dies, "L10")["geometry_overrides"]["stator_outer_notches"] is False
