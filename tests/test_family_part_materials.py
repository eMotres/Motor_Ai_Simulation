# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Materials tab "Full" (owner 2026-10-14): the whole motor part by part, with a material per NON-active part saved
with the die.  Route + persistence on a temp catalog; the electromagnetic simulation keeps reading only the active
assignment (isolation test); the component card uses the full assignment.  No solver, no FEM."""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from motor_ai_sim.api import app

_ROOT = Path(__file__).resolve().parents[1]
_REAL_USERS = _ROOT / "config" / "users.json"
DIE, CFG, ADMIN, USER = "CIANO14 40 new", "L12", "admin@example.com", "user@example.com"
client = TestClient(app)


@pytest.fixture()
def catalog(tmp_path, monkeypatch):
    from motor_ai_sim.cad import bom
    from motor_ai_sim.modules import component_library as L
    from motor_ai_sim.routes import family as fam
    from tests._component_dies import make_dies
    from tests.test_cad_bom import make
    dies = make_dies(tmp_path)
    monkeypatch.setattr(fam, "_DIES_DIR", dies)
    monkeypatch.setenv("MOTRES_DIES_DIR", str(dies))
    for v in ("L12", "L20"):
        cad = tmp_path / "cad" / DIE / v
        cad.mkdir(parents=True)
        b, _ = make(v)
        bom.write_csv(b, str(cad / f"CIANO14_40_new_{v}_BOM.csv"))
    monkeypatch.setenv("MOTRES_CAD_DIR", str(tmp_path / "cad"))
    L._LIB = None                                                         # noqa: SLF001
    yield dies
    L._LIB = None                                                         # noqa: SLF001


@pytest.fixture()
def accounts(tmp_path, monkeypatch, catalog):
    from motor_ai_sim import auth
    from motor_ai_sim import users as U
    users_file = tmp_path / "users.json"
    if _REAL_USERS.exists():
        shutil.copy2(_REAL_USERS, users_file)
    monkeypatch.setattr(U, "_USERS_FILE", users_file)
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.delenv("CATALOG_GRANT_ALL_REGISTERED", raising=False)
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", True)
    U.create_user(ADMIN, "password-admin", role="admin", name="Admin")
    U.create_user(USER, "password-user", role="user", name="User")
    return {"admin": {"Authorization": f"Bearer {U.issue_token(ADMIN)}"},
            "user": {"Authorization": f"Bearer {U.issue_token(USER)}"}}


URL = f"/api/family/die/{DIE}/part_materials"


def _rows(h, cfg=CFG):
    r = client.get(URL, params={"config": cfg}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_full_list_is_the_active_parts_plus_every_cad_part_with_defaults(accounts):
    out = _rows(accounts["admin"])
    assert out["available"] and out["source"] == "app+cad_bom"
    by = {p["key"]: p for p in out["parts"]}
    active = [p for p in out["parts"] if p["em"]]
    assert {"stator_core", "rotor_core", "magnet", "slot", "shaft"} <= set(by)
    assert all(p["em"] and p["counted"] for p in active if p["mass_kg"])        # the app mass
    for k in ("holder_front", "holder_back", "rings", "prop_fix", "bearings", "screws", "board", "pins"):
        p = by[k]
        assert p["em"] is False and p["counted"] is False and p["mass_kg"] > 0 and not p["user_set"], k
        assert p["material_id"] == p["default_material_id"]
    assert by["holder_front"]["material_id"] == "Aluminium_6061" and by["screws"]["material_id"] == "Stainless_A2_70"
    assert by["board"]["material_id"] == "FR4_glass_epoxy" and by["bearings"]["material_id"] == "Bearing_steel_100Cr6"
    assert out["choices"] and {"steel", "magnet", "metal", "insulator"} <= {c["group"] for c in out["choices"]}
    assert not any(c["group"] == "coolant" for c in out["choices"])
    assert out["can_write"] is True


def test_put_saves_per_die_and_rescales_the_mass_by_density(accounts, catalog):
    h = accounts["admin"]
    before = {p["key"]: p for p in _rows(h)["parts"]}
    r = client.put(URL, json={"part": "holder_front", "material": "Aluminium_7075"}, headers=h)
    assert r.status_code == 200, r.text
    doc = yaml.safe_load((catalog / DIE / "die.yaml").read_text("utf-8"))
    assert doc["part_materials"] == {"holder_front": "Aluminium_7075"}               # die-level, in die.yaml
    after = {p["key"]: p for p in _rows(h)["parts"]}
    assert after["holder_front"]["material_id"] == "Aluminium_7075" and after["holder_front"]["user_set"]
    assert after["holder_front"]["default_material_id"] == "Aluminium_6061"
    assert after["holder_front"]["mass_kg"] == pytest.approx(before["holder_front"]["mass_kg"] * 2810 / 2700, rel=1e-9)
    assert "scaled by density" in after["holder_front"]["basis"]
    assert after["holder_back"]["material_id"] == "Aluminium_6061"                 # the others are untouched
    # the choice is the die's: another configuration of the die sees it too
    assert {p["key"]: p for p in _rows(h, "L20")["parts"]}["holder_front"]["material_id"] == "Aluminium_7075"
    # back to the default removes the block
    r = client.put(URL, json={"part": "holder_front", "material": None}, headers=h)
    assert r.status_code == 200
    assert "part_materials" not in yaml.safe_load((catalog / DIE / "die.yaml").read_text("utf-8"))
    assert {p["key"]: p for p in _rows(h)["parts"]}["holder_front"]["mass_kg"] == pytest.approx(
        before["holder_front"]["mass_kg"])


def test_put_refuses_active_parts_unknown_materials_and_coolants(accounts):
    h = accounts["admin"]
    for part in ("stator_core", "magnet", "shaft", "nonsense"):
        assert client.put(URL, json={"part": part, "material": "Aluminium_6061"}, headers=h).status_code == 400, part
    assert client.put(URL, json={"part": "screws", "material": "unobtainium"}, headers=h).status_code == 422
    assert client.put(URL, json={"part": "screws", "material": "water"}, headers=h).status_code == 422


def test_put_follows_the_catalog_write_permission(accounts, catalog):
    before = (catalog / DIE / "die.yaml").read_bytes()
    assert client.put(URL, json={"part": "screws", "material": "copper"}).status_code in (401, 403)
    assert client.put(URL, json={"part": "screws", "material": "copper"}, headers=accounts["user"]).status_code in (401, 403)
    assert (catalog / DIE / "die.yaml").read_bytes() == before                      # nothing written


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_the_em_inputs_do_not_see_a_non_active_choice(accounts, catalog):
    """A change to a non-active part leaves everything the electromagnetic simulation reads exactly as it was: the
    geometry, its signature, the CAD parameter fingerprint, the active material assignment, the active masses and the
    configuration file."""
    from motor_ai_sim.cad import store
    from motor_ai_sim.config import get_material_assignments
    from motor_ai_sim.modules import motor_mass as MM
    from motor_ai_sim.routes.presets import _geo_sig
    h = accounts["admin"]

    def em_inputs():
        eff = store.load_effective(str(catalog), DIE, CFG, materials_library=str(_ROOT / "config" / "materials_library.yaml"))
        fp = store.parameter_fingerprint(eff, store.load_mapping())
        mm = MM.app_motor_mass(DIE, CFG, dies_dir=catalog)
        return dict(geometry=eff["geometry"], materials=eff["materials"], winding=eff["winding"], fp=fp,
                    geo_sig=_geo_sig(eff["geometry"]), assigned=dict(get_material_assignments()),
                    masses=dict(mm.components), active_parts=[(p.key, p.material_id, p.mass_kg) for p in mm.parts],
                    cfg_hash=_sha(catalog / DIE / f"{CFG}.yaml"))

    die_geo = yaml.safe_load((catalog / DIE / "die.yaml").read_text("utf-8"))["geometry"]
    before = em_inputs()
    for part, mat in (("holder_front", "Aluminium_7075"), ("screws", "Stainless_316L"), ("board", "copper")):
        assert client.put(URL, json={"part": part, "material": mat}, headers=h).status_code == 200
    assert em_inputs() == before
    # only the die's non-active block moved: the geometry block of die.yaml is byte-for-byte what it was
    assert yaml.safe_load((catalog / DIE / "die.yaml").read_text("utf-8"))["geometry"] == die_geo
    # and the solver-side readers know no such part: the mass model's part table is the active set only
    from motor_ai_sim.masses import _PARTS
    from motor_ai_sim.materials import PART_CATEGORIES
    assert set(_PARTS) == {"stator_core", "rotor_core", "magnet", "slot", "shaft", "sleeve"}
    assert not ({"holder_front", "screws", "board", "bearings"} & (set(_PARTS) | set(PART_CATEGORIES)))


def test_the_component_card_uses_the_full_assignment(accounts):
    from motor_ai_sim import propeller_s1 as s1
    from motor_ai_sim.contracts.conformance import assert_component_card
    from motor_ai_sim.modules.component_adapters import motor_card
    h = accounts["admin"]
    model = s1.load_model(DIE, CFG)
    base = {p.name: p for p in motor_card(model).parts}
    assert base["Stator holder, front"].material_id == "Aluminium_6061"
    assert client.put(URL, json={"part": "holder_front", "material": "Aluminium_7075"}, headers=h).status_code == 200
    card = motor_card(model)
    assert_component_card(card)
    got = {p.name: p for p in card.parts}
    assert got["Stator holder, front"].material_id == "Aluminium_7075" and not got["Stator holder, front"].counted
    assert got["Stator holder, front"].mass_kg == pytest.approx(base["Stator holder, front"].mass_kg * 2810 / 2700)
    # the card's own mass is still the app's active mass, whatever the non-active choices
    assert card.mass_properties.mass_kg == pytest.approx(motor_card(model).mass_properties.mass_kg)


def test_a_motor_without_a_bom_lists_only_what_its_configuration_names(accounts, catalog, monkeypatch):
    monkeypatch.setenv("MOTRES_CAD_DIR", str(catalog.parent / "empty"))
    cfg = yaml.safe_load((catalog / DIE / "L12.yaml").read_text("utf-8"))
    out = _rows(accounts["admin"])
    assert out["source"] == "app" and not [p for p in out["parts"] if not p["em"]]       # nothing invented
    cfg["bearings"] = {"A": {"card": "618/8-2Z"}, "B": {"card": "618/8-2Z"}}
    (catalog / DIE / "L12.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    out = _rows(accounts["admin"])
    extra = [p for p in out["parts"] if not p["em"]]
    assert out["source"] == "app+config" and [p["key"] for p in extra] == ["bearings"] and extra[0]["mass_kg"] is None


def test_a_die_that_is_not_there_is_reported_not_invented(accounts):
    h = accounts["admin"]
    assert client.get("/api/family/die/NO SUCH DIE/part_materials", headers=h).status_code == 404
    out = client.get("/api/family/die/NO SUCH DIE/part_materials", params={"config": "L12"}, headers=h).json()
    assert out["available"] is False and out["parts"] == [] and "not found" in out["reason"]
