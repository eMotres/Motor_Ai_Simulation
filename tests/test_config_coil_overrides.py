"""A configuration's own coil: ``wire_beng_r`` / ``coil_straight_extra`` per configuration (owner 2026-11-02: «все бери из Fusion»).

Three machines on one die with one wire can have different coils in their Fusion models; a die-level value moved all of them.
Pinned here:

* without a ``coil_overrides`` block the die's value (or the coil rule) stands, exactly as before;
* a configuration's value wins over the die's and moves k_end of THAT configuration only - the sibling on the same die keeps its
  k_end, also when the computation is scoped to a named configuration (catalog mass, datasheet);
* the values are validated (mm, > 0, sane upper bound) and a refused write writes nothing; a locked configuration refuses;
* PATCH / GET ``/api/family/config/{die}/{cfg}/coil`` round-trip through the yaml, ``null`` clears, an unsent field is kept;
* the Fusion import reads them (formula -> evaluated Value, ``coil_straight_len - motor_length``) into the LOADED configuration.

Throwaway catalog under the pytest tmp area; nothing reads or writes ``config/dies``.  No FEM.
"""
from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException, UploadFile

from motor_ai_sim import coil_build
from motor_ai_sim.masses import end_winding_factor
from motor_ai_sim.routes import family as fam

DIE = "TEST 85 shared die"
MOTOR, GEN1, GEN2 = "M1-L40", "G1-L40", "G2-L40"


class P:
    """The few params the coil length reads (metres)."""

    def __init__(self, L_mm: float):
        self.stack_length = L_mm * 1e-3
        self.num_slots = 12
        self.slot_width_m = 0.0
        self.r_stator_in = 0.0
        self.slot_height_m = 0.0


#: a tooth-coil cross-section, generic numbers (7 mm flat wire on edge)
GEO = {"stator_diameter": 85.0, "num_seg": 1, "num_slots_per_segment": 12, "num_poles_per_segment": 14,
       "tooth_width": 9.0, "wire_width": 7.0, "wire_height": 1.0, "num_wires_per_slot": 8, "insulation_thickness": 0.25,
       "wire_spacing_x": 0.1, "wire_spacing_y": 0.1, "motor_length": 40.0}


def _write(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _fresh_cache():
    coil_build.clear_cache()
    yield
    coil_build.clear_cache()


@pytest.fixture
def dies(tmp_path, monkeypatch):
    """One die, three configurations on it; only the motor sets its own coil."""
    root = tmp_path / "dies"
    _write(root / DIE / "die.yaml", {"name": DIE, "locked": True, "geometry": dict(GEO, wire_beng_r=4.2)})
    _write(root / DIE / f"{MOTOR}.yaml", {"name": MOTOR, "die": DIE, "geometry_overrides": {"motor_length": 40.0},
                                           "coil_overrides": {"wire_beng_r": 2.0, "coil_straight_extra": 6.0},
                                           "duties": [{"name": "rated", "summary": {"torque_Nm": 1.0}}]})
    _write(root / DIE / f"{GEN1}.yaml", {"name": GEN1, "die": DIE, "geometry_overrides": {"motor_length": 40.0}})
    _write(root / DIE / f"{GEN2}.yaml", {"name": GEN2, "die": DIE, "geometry_overrides": {"motor_length": 40.0}})
    monkeypatch.setattr(fam, "_DIES_DIR", root)
    monkeypatch.setattr(fam, "_CTX_FILE", tmp_path / ".family_context.json")
    monkeypatch.setattr(fam, "_require_die_access", lambda *_a, **_k: None)
    monkeypatch.setattr(fam, "_require_die_write", lambda *_a, **_k: None)
    return root


# ── the resolution order ──────────────────────────────────────────────────────────────────────────────────────────────────
def test_without_a_block_the_die_value_stands_and_without_a_die_value_the_rule():
    geo = dict(GEO, wire_beng_r=4.2)
    c = coil_build.coil_parameters(geo, die="d", config="c", cad_overrides={}, coil_overrides={})
    assert c["wire_beng_r"] == 4.2 and c["config_keys"] == [] and c["typed"] == ["wire_beng_r"]
    assert c["coil_straight_extra"] == pytest.approx(coil_build.coil_rule(geo)["coil_straight_extra"])
    rule = coil_build.coil_parameters(GEO, die="d", config="c", cad_overrides={}, coil_overrides={})
    assert rule["wire_beng_r"] == pytest.approx(coil_build.K_BEND * 7.0)
    # an absent / null / invalid stored key means "not set" - never a crash and never a zero
    for blk in ({"wire_beng_r": None}, {"wire_beng_r": -1}, {"wire_beng_r": "x"}, {"other": 3.0}, None):
        assert coil_build.coil_parameters(geo, die="d", config="c", cad_overrides={}, coil_overrides=blk)["wire_beng_r"] == 4.2


def test_the_configuration_value_wins_over_the_die_value():
    geo = dict(GEO, wire_beng_r=4.2, coil_straight_extra=1.0)
    c = coil_build.coil_parameters(geo, die="d", config="c", cad_overrides={},
                                   coil_overrides={"wire_beng_r": 2.0, "coil_straight_extra": 6.0})
    assert c["wire_beng_r"] == 2.0 and c["coil_straight_extra"] == 6.0
    assert c["config_keys"] == ["wire_beng_r", "coil_straight_extra"]
    assert c["source"].startswith("configuration 'c' sets wire_beng_r = 2 mm, coil_straight_extra = 6 mm;")
    # only one key set: the other keeps the die's
    one = coil_build.coil_parameters(geo, die="d", config="c", cad_overrides={}, coil_overrides={"coil_straight_extra": 6.0})
    assert one["wire_beng_r"] == 4.2 and one["coil_straight_extra"] == 6.0


def test_k_end_moves_for_that_configuration_only(dies):
    geo = dict(GEO, wire_beng_r=4.2)
    k_motor = end_winding_factor(P(40), geo, die=DIE, config=MOTOR)
    k_gen1 = end_winding_factor(P(40), geo, die=DIE, config=GEN1)
    k_gen2 = end_winding_factor(P(40), geo, die=DIE, config=GEN2)
    k_die = end_winding_factor(P(40), geo, die=DIE, config=GEN1, coil_overrides={})
    assert k_gen1 == pytest.approx(k_die, rel=1e-12) and k_gen2 == pytest.approx(k_die, rel=1e-12)
    assert k_motor != pytest.approx(k_die, rel=1e-4)
    # by hand: per turn the legs grow by 2 x (6 - 1) mm; the +z end turn = bridge 2(x1 - r) + pi(r + w/2), and the -z one
    # sqrt(plus^2 + pitch^2): the motor's smaller bend radius shortens both
    x1 = 9.0 / 2 + 0.25 + 0.1 / 2

    def mean_turn(r, extra):
        plus = 2 * (x1 - r) + 3.141592653589793 * (r + 3.5)
        minus = (plus ** 2 + 1.1 ** 2) ** 0.5
        lead = coil_build.coil_rule(geo)["coil_lead_total"]
        return (2 * 8 * (40 + extra) + 8 * plus + 7 * minus + 2 * lead) / 8

    rule_extra = coil_build.coil_rule(geo)["coil_straight_extra"]
    assert k_motor - k_die == pytest.approx((mean_turn(2.0, 6.0) - mean_turn(4.2, rule_extra)) / 80.0, rel=1e-9)


def test_a_scoped_catalog_computation_bills_the_named_configuration(dies, tmp_path):
    """The loaded machine is the MOTOR; the mass / datasheet of a sibling (machine_scope) must not take the motor's coil."""
    (tmp_path / ".family_context.json").write_text(json.dumps({"die": DIE, "config": MOTOR}), encoding="utf-8")
    geo = dict(GEO, wire_beng_r=4.2)
    with coil_build.machine_scope(DIE, GEN1):
        k_gen1 = end_winding_factor(P(40), geo)
    with coil_build.machine_scope(DIE, MOTOR):
        k_motor = end_winding_factor(P(40), geo)
    k_die = end_winding_factor(P(40), geo, die=DIE, config=GEN1, coil_overrides={})
    assert k_gen1 == pytest.approx(k_die, rel=1e-12) and k_motor != pytest.approx(k_die, rel=1e-4)
    # a scope that carries the very document read by the caller uses it as it is
    with coil_build.machine_scope(DIE, GEN2, {"coil_overrides": {"wire_beng_r": 2.0, "coil_straight_extra": 6.0}}):
        assert end_winding_factor(P(40), geo) == pytest.approx(k_motor, rel=1e-12)
    # outside the scope the loaded context answers again
    assert coil_build.context_machine() != {"die": DIE, "config": GEN2}


# ── validation ────────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("key,value", [("wire_beng_r", 0), ("wire_beng_r", -0.5), ("wire_beng_r", 50.01), ("wire_beng_r", "abc"),
                                       ("wire_beng_r", True), ("wire_beng_r", float("nan")), ("coil_straight_extra", 0.0),
                                       ("coil_straight_extra", 100.5), ("coil_straight_extra", float("inf")),
                                       ("coil_lead_total", 5.0)])
def test_validation_refuses_nonsense(key, value):
    v, why = coil_build.validate_coil_override(key, value)
    assert v is None and why


def test_validation_accepts_lengths_in_mm():
    assert coil_build.validate_coil_override("wire_beng_r", "1.72") == (1.72, None)
    assert coil_build.validate_coil_override("coil_straight_extra", 100) == (100.0, None)


def test_a_refused_write_writes_nothing_and_a_locked_configuration_refuses(dies):
    f = dies / DIE / f"{GEN1}.yaml"
    before = f.read_text(encoding="utf-8")
    for body in ({"wire_beng_r": 0}, {"wire_beng_r": 2.0, "coil_straight_extra": -1}, {"coil_lead_total": 5}):
        with pytest.raises(HTTPException) as e:
            fam.set_config_coil(DIE, GEN1, body)
        assert e.value.status_code == 422
    assert f.read_text(encoding="utf-8") == before
    doc = _yaml(f)
    doc["locked"] = True
    _write(f, doc)
    with pytest.raises(HTTPException) as e:
        fam.patch_config_coil(DIE, GEN1, fam.CoilPatch(wire_beng_r=2.0), _w={})
    assert e.value.status_code == 423
    assert "coil_overrides" not in _yaml(f)


# ── the route round trip ──────────────────────────────────────────────────────────────────────────────────────────────────
def test_patch_and_get_round_trip_through_the_configuration_yaml(dies):
    f = dies / DIE / f"{GEN2}.yaml"
    sig0 = fam._build_sig(_yaml(dies / DIE / "die.yaml"), _yaml(f))
    view0 = fam.get_config_coil(DIE, GEN2)
    assert view0["overrides"] == {} and view0["die_values"] == {"wire_beng_r": 4.2, "coil_straight_extra": None}
    assert view0["coil"]["wire_beng_r"] == 4.2 and view0["coil"]["config_keys"] == []

    out = fam.patch_config_coil(DIE, GEN2, fam.CoilPatch(wire_beng_r=1.72, coil_straight_extra=9.0), _w={})
    assert out["changed"] == ["coil_straight_extra", "wire_beng_r"]
    assert _yaml(f)["coil_overrides"] == {"wire_beng_r": 1.72, "coil_straight_extra": 9.0}
    got = fam.get_config_coil(DIE, GEN2)
    assert got["overrides"] == {"wire_beng_r": 1.72, "coil_straight_extra": 9.0}
    assert got["coil"]["wire_beng_r"] == 1.72 and got["coil"]["coil_straight_extra"] == 9.0
    assert got["coil"]["config_keys"] == ["wire_beng_r", "coil_straight_extra"]
    assert view0["k_end"] and got["k_end"]
    assert out["k_end_before"] == pytest.approx(view0["k_end"]) and out["k_end_after"] == pytest.approx(got["k_end"])
    assert got["k_end"] != pytest.approx(view0["k_end"], rel=1e-4)
    assert out["results_stale"] is False                     # this configuration has no stored results
    # the coil is part of the build: results computed before the edit are on an older build
    assert fam._build_sig(_yaml(dies / DIE / "die.yaml"), _yaml(f)) != sig0

    # an unsent field is kept, an explicit null clears (the die's value stands again)
    out = fam.patch_config_coil(DIE, GEN2, fam.CoilPatch(wire_beng_r=None), _w={})
    assert out["changed"] == ["wire_beng_r"] and _yaml(f)["coil_overrides"] == {"coil_straight_extra": 9.0}
    assert fam.get_config_coil(DIE, GEN2)["coil"]["wire_beng_r"] == 4.2
    out = fam.patch_config_coil(DIE, GEN2, fam.CoilPatch(coil_straight_extra=None), _w={})
    assert "coil_overrides" not in _yaml(f)
    assert fam._build_sig(_yaml(dies / DIE / "die.yaml"), _yaml(f)) == sig0          # back to the old build exactly
    # the sibling configurations were never touched
    assert _yaml(dies / DIE / f"{MOTOR}.yaml")["coil_overrides"] == {"wire_beng_r": 2.0, "coil_straight_extra": 6.0}
    assert "coil_overrides" not in _yaml(dies / DIE / f"{GEN1}.yaml")


def test_the_block_never_reaches_the_live_geometry(dies):
    """Loading a configuration PUTs the payload's geometry; the coil stays in the configuration document."""
    out = fam.payload(DIE, MOTOR, authorization=None)
    geo = out.get("geometry") or {}
    assert "coil_overrides" not in geo
    assert geo.get("wire_beng_r") == 4.2                     # the die's own key, as before


# ── Fusion ────────────────────────────────────────────────────────────────────────────────────────────────────────────────
CSV = ("Name,Unit,Expression,Value,Comment,Favorite\n"
       "wire_beng_r,mm,tooth_width / 2 + insulation_thickness + wire_spacing_x / 2 - 0.05 mm,1.85,,false\n"
       "coil_straight_len,mm,motor_length + 3 mm,43,,false\n"
       "motor_length,mm,40 mm,40,,false\n")


def _import(text: str, dry_run: int):
    from motor_ai_sim.routes.fusion import import_params
    up = UploadFile(filename="params.csv", file=io.BytesIO(text.encode("utf-8-sig")))
    return asyncio.run(import_params(file=up, dry_run=dry_run, _admin={"role": "admin"}))


def test_the_fusion_import_sets_the_loaded_configuration_coil(dies, tmp_path, monkeypatch):
    from motor_ai_sim.routes import fusion
    monkeypatch.setattr(fusion, "get_config", lambda: {"geometry": dict(GEO)})
    (tmp_path / ".family_context.json").write_text(json.dumps({"die": DIE, "config": GEN1}), encoding="utf-8")
    dry = _import(CSV, 1)
    assert dry["applied"] == {} and dry["unchanged"] == ["motor_length"]
    assert dry["coil"]["config"] == GEN1
    assert dry["coil"]["applied"] == {"coil_straight_extra": 3.0, "wire_beng_r": 1.85}
    assert "coil_overrides" not in _yaml(dies / DIE / f"{GEN1}.yaml")          # a dry run writes nothing
    done = _import(CSV, 0)
    assert done["coil"]["applied"] == {"coil_straight_extra": 3.0, "wire_beng_r": 1.85}
    assert _yaml(dies / DIE / f"{GEN1}.yaml")["coil_overrides"] == {"wire_beng_r": 1.85, "coil_straight_extra": 3.0}
    assert "coil_overrides" not in _yaml(dies / DIE / f"{GEN2}.yaml")
    # the same file again: nothing to apply
    again = _import(CSV, 0)
    assert again["coil"]["applied"] == {} and "nothing to apply" in again["note"]


def test_the_fusion_import_without_a_loaded_configuration_says_so(dies, monkeypatch):
    from motor_ai_sim.routes import fusion
    monkeypatch.setattr(fusion, "get_config", lambda: {"geometry": dict(GEO)})
    out = _import(CSV, 0)
    assert out["coil"]["applied"] == {} and "no configuration is loaded" in out["coil"]["not_applied"]
    for name in (MOTOR, GEN1, GEN2):
        assert _yaml(dies / DIE / f"{name}.yaml").get("coil_overrides") in (None, {"wire_beng_r": 2.0, "coil_straight_extra": 6.0})
