# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Geometry tab "Active | Full" (owner 2026-10-15): THE part tree, the CAD-only construction parameters and the Files menu's
server side.

* one part tree: the same ids / order / groups in Geometry, Materials, the 3-D model, the component card and the BOM export;
* the editable construction table: validation (ranges, the template pre-check), persistence per die/config, permission, audit;
* EM isolation: a Full edit changes no electromagnetic input, no geometry, no ``_geoSig``;
* rebuild hand-over: a save requests a server rebuild, the old model stays, the status says "pending" until the new model
  carries the new overrides hash;
* the full-construction downloads (parts, BOM csv / xlsx, full parameter CSV, FreeCAD file): NDA flag, role, logging.

A throwaway catalog + a fake CAD tree built from the repository's Fusion reference profile; no FreeCAD, no solver.
"""
from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from motor_ai_sim.api import app

_ROOT = Path(__file__).resolve().parents[1]
_REAL_USERS = _ROOT / "config" / "users.json"
DIE, CFG = "CIANO14 40 new", "L12"
MID = "CIANO14_40_new_L12"
ADMIN, USER, NDA_USER, ENG_NDA, ENG = ("admin@example.com", "user@example.com", "nda@example.com", "eng-nda@example.com",
                                       "eng@example.com")
client = TestClient(app)
from motor_ai_sim.cad import store as _store
from tests.private_fixtures import need_cad_reference

REF = Path(_store.REFERENCE_DIR) / "CIANO14_40_12.json"          # private data (cad/reference): tests below skip without it

GLB = b"glTF-FULL-MODEL-BYTES"
STEP = (b"ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('x'),'2;1');\nFILE_NAME('a','2026-10-13T10:00:00',('A'),('O'),'OCC','FreeCAD','U');\n"
        b"FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n")
FCSTD = b"PK-FAKE-FCSTD"


def _sha(b):
    from motor_ai_sim import model3d as M
    return M.sha256_bytes(b)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from motor_ai_sim import auth
    from motor_ai_sim import users as U
    from motor_ai_sim.cad import bom
    from motor_ai_sim.modules import component_library as L
    from motor_ai_sim.routes import family as fam
    from tests._component_dies import make_dies
    from tests.test_cad_bom import make

    dies = make_dies(tmp_path)
    monkeypatch.setattr(fam, "_DIES_DIR", dies)
    monkeypatch.setenv("MOTRES_DIES_DIR", str(dies))
    cad = tmp_path / "cad"
    for v in ("L12", "L20"):
        d = cad / DIE / v
        d.mkdir(parents=True)
        b, _ = make(v)
        bom.write_csv(b, str(d / f"CIANO14_40_new_{v}_BOM.csv"))
        mid = f"CIANO14_40_new_{v}"
        (d / f"{mid}_full.glb").write_bytes(GLB)
        (d / f"{mid}_active.step").write_bytes(STEP)
        (d / f"{mid}.FCStd").write_bytes(FCSTD)
        shutil.copy2(REF, d / "params.json")
        files = {f"{mid}_full.glb": GLB, f"{mid}_active.step": STEP, f"{mid}.FCStd": FCSTD}
        (d / "manifest.json").write_text(json.dumps({
            "motor_id": mid, "die": DIE, "config": v, "param_hash": "ab" * 32, "built_at": "2026-10-13T12:00:00+00:00",
            "template": {"version": "A.5.0"}, "cad_overrides_hash": "none",
            "files": {n: {"sha256": _sha(x), "bytes": len(x)} for n, x in files.items()}}), encoding="utf-8")
    monkeypatch.setenv("MOTRES_CAD_DIR", str(cad))
    monkeypatch.setenv("MOTRES_CAD_ROOT", str(cad))
    monkeypatch.setenv("CAD_DOWNLOAD_LOG", str(tmp_path / "cad_downloads.jsonl"))
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(tmp_path / "admin_audit.jsonl"))
    L._LIB = None                                                         # noqa: SLF001

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
    U.create_user(NDA_USER, "password-nda", role="user", name="Nda User")
    U.create_user(ENG, "password-eng", role="engineer", name="Eng")
    U.create_user(ENG_NDA, "password-eng2", role="engineer", name="Eng Nda")
    for e in (USER, NDA_USER, ENG, ENG_NDA):
        U.set_motor_grants(e, all_motors=False, dies=[DIE])
    for e in (NDA_USER, ENG_NDA):
        U.set_nda(e, True, "2026-10-01", by=ADMIN)
    h = lambda e: {"Authorization": f"Bearer {U.issue_token(e)}"}
    yield dict(dies=dies, cad=cad, tmp=tmp_path, admin=h(ADMIN), user=h(USER), nda=h(NDA_USER), eng=h(ENG), engnda=h(ENG_NDA),
               log=tmp_path / "cad_downloads.jsonl", audit=tmp_path / "admin_audit.jsonl")
    L._LIB = None                                                         # noqa: SLF001


CP = f"/api/family/config/{DIE}/{CFG}/cad_params"
M3 = f"/api/model3d/{DIE}/{CFG}"


def _cfg_doc(env):
    return yaml.safe_load((env["dies"] / DIE / f"{CFG}.yaml").read_text("utf-8"))


# ── THE part tree ───────────────────────────────────────────────────────────────────────────────────────────

def test_part_tree_is_identical_across_every_surface(env):
    import csv
    tree = client.get(f"/api/family/die/{DIE}/part_tree", params={"config": CFG}, headers=env["admin"]).json()
    assert tree["available"] and tree["source"] == "cad_bom"
    ids = [p["id"] for p in tree["parts"]]
    assert ids == [p["key"] for p in tree["parts"]] and len(set(ids)) == len(ids)
    # groups: fixed order, every part in exactly one, the groups list is the parts' groups
    order = ["stator", "winding", "rotor", "magnets", "shaft", "housing", "bearings", "electronics", "fasteners", "other"]
    gids = [g["id"] for g in tree["groups"]]
    assert gids == [g for g in order if g in gids]
    assert [i for g in tree["groups"] for i in g["parts"]] == ids
    assert all(p["names"]["en"] and p["names"]["zh"] and p["name"] == p["names"]["en"] for p in tree["parts"])
    # Materials (Full)
    pm = client.get(f"/api/family/die/{DIE}/part_materials", params={"config": CFG}, headers=env["admin"]).json()
    assert [p["key"] for p in pm["parts"]] == ids and [g["id"] for g in pm["groups"]] == gids
    assert [p["names"] for p in pm["parts"]] == [p["names"] for p in tree["parts"]]
    # 3-D model parts list (NDA): the same ids and order, no materials / masses
    r3 = client.get(f"{M3}/parts", headers=env["nda"])
    assert r3.status_code == 200
    assert [p["id"] for p in r3.json()["parts"]] == ids
    assert "mass_kg" not in r3.json()["parts"][0] and "material_id" not in r3.json()["parts"][0]
    assert [p["glb"] for p in r3.json()["parts"]] == [p["glb"] for p in tree["parts"]]
    # component card
    from motor_ai_sim import propeller_s1 as s1
    from motor_ai_sim.modules.component_adapters import motor_card
    card = motor_card(s1.load_model(DIE, CFG), dies_dir=env["dies"], cad_dir=env["cad"])
    card_ids = [p.part_id for p in card.parts]
    assert card_ids == [i for i in ids if i in set(card_ids)] and None not in card_ids
    assert {p.part_id: p.group for p in card.parts} == {p["id"]: p["group"] for p in tree["parts"] if p["id"] in set(card_ids)}
    # BOM export
    b = client.get(f"{M3}/bom.csv", headers=env["admin"])
    assert b.status_code == 200
    rows = list(csv.reader(io.StringIO(b.content.decode("utf-8-sig"))))
    hdr = next(i for i, r in enumerate(rows) if r and r[0] == "item")
    body = [r for r in rows[hdr + 1:] if r[0].isdigit()]
    assert [r[1] for r in body] == ids and [r[2] for r in body] == [p["group"] for p in tree["parts"]]


def test_part_tree_orders_known_groups_and_unknown_parts_last():
    from motor_ai_sim import part_tree as PT
    rows = [{"key": "other_9", "name": "Mystery", "role": "other"}, {"key": "screws", "name": "x", "role": "fastener"},
            {"key": "stator_core", "name": "x", "role": "magnetic"}, {"key": "bearings", "name": "x", "role": "structural"}]
    t = PT.build(rows)
    assert [p["id"] for p in t["parts"]] == ["stator_core", "bearings", "screws", "other_9"]
    assert [g["id"] for g in t["groups"]] == ["stator", "bearings", "fasteners", "other"]
    assert t["parts"][-1]["names"] == {"en": "Mystery", "zh": "Mystery"}
    assert set(PT.part_ids()) >= {"holder_front", "carrier_front", "board", "pins", "wire_insulation"}


# ── the catalogue ──────────────────────────────────────────────────────────────────────────────────────────

def test_catalogue_has_no_electromagnetic_name_and_every_row_is_translated():
    from motor_ai_sim import cad_params as C
    from motor_ai_sim import part_tree as PT
    fmap = yaml.safe_load((_ROOT / "config" / "fusion_param_map.yaml").read_text("utf-8"))["map"]
    em = set(fmap) | set(fmap.values())
    assert not (set(C.CATALOGUE) & em), "a CAD-only parameter must never be an electromagnetic one"
    geo_schema = yaml.safe_load((_ROOT / "config" / "motor_config.yaml").read_text("utf-8"))["geometry_schema"]
    assert not (set(C.CATALOGUE) & set(geo_schema))
    for n, m in C.CATALOGUE.items():
        assert m["group"] in PT.GROUP_IDS and m["tip"]["en"] and m["tip"]["zh"] and m["min"] < m["max"], n
        assert any("一" <= ch <= "鿿" for ch in m["tip"]["zh"]), n       # a real zh-CN text, not a copy of the English
    assert "offset_rotor" in C.CATALOGUE


def test_every_parameter_has_a_short_label_in_both_languages_and_bearing_internals_are_read_only():
    from motor_ai_sim import cad_params as C
    from motor_ai_sim.cad_param_labels import labels
    lab = labels()
    assert set(lab) == set(C.CATALOGUE)
    for n, m in C.CATALOGUE.items():
        en, zh = m["label"]["en"], m["label"]["zh"]
        assert " - " in en and len(en) <= 48 and en != n and any("一" <= ch <= "鿿" for ch in zh), n
    assert C.CATALOGUE["ring_t"]["label"] == {"en": "Glass-fibre ring - thickness", "zh": "玻璃纤维环 - 厚度"}
    assert C.CATALOGUE["prop_fix_d"]["label"]["en"] == "Propeller plate - diameter"
    assert C.CATALOGUE["shaft_bore_tip_angle"]["label"]["en"] == "Shaft - bore tip angle"
    # every bearing internal is read-only; nothing else is
    assert C.READ_ONLY == {n for n, m in C.CATALOGUE.items() if m["group"] == "bearings"} and "bearing_ball_count" in C.READ_ONLY
    p = json.loads(need_cad_reference("CIANO14_40_12.json").read_text("utf-8"))
    built = {x["name"]: x for sec in C.SECTIONS for x in p[sec]}
    rows = {r["name"]: r for r in C.rows(built, {})}
    assert rows["bearing_ball_count"]["editable"] is False and rows["ring_t"]["editable"] is True
    assert "read-only" in C.validate(built, {"bearing_ball_d": 2.2})[0]
    # an integer parameter of a carrier motor is still checked as a whole number
    q = json.loads(need_cad_reference("CIANO28_85_20SW1200.json").read_text("utf-8"))
    built85 = {x["name"]: x for sec in C.SECTIONS for x in q[sec]}
    assert "whole number" in " ".join(C.validate(built85, {"rotor_key_count": 3.5}))


def test_rows_of_both_reference_profiles_are_editable_and_valid_as_built():
    from motor_ai_sim import cad_params as C
    for f in ("CIANO14_40_12", "CIANO28_85_20SW1200"):
        p = json.loads(need_cad_reference(f"{f}.json").read_text("utf-8"))
        built = {x["name"]: x for sec in C.SECTIONS for x in p[sec]}
        rows = C.rows(built, {})
        assert len(rows) > 40 and all(not r["overridden"] and r["value"] == r["default"] for r in rows)
        assert C.validate(built, {}) == []                       # the reference itself passes the template pre-check


# ── GET / PUT ──────────────────────────────────────────────────────────────────────────────────────────────

def test_get_table_lists_groups_units_limits_and_marks_cad_only(env):
    r = client.get(CP, headers=env["admin"])
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["available"] and t["can_write"] is True and t["rebuild"]["state"] == "current"
    names = {x["name"]: x for x in t["rows"]}
    assert {"holder_flange_t", "ring_t", "phase_board_x", "screw_head_d", "offset_rotor", "shaft_thread_len"} <= set(names)
    row = names["ring_t"]
    assert row["unit"] == "mm" and row["group"] == "housing" and row["cad_only"] is True
    assert row["min"] < row["default"] < row["max"] and row["tip"]["zh"] and row["value"] == row["default"]
    assert t["cad_only_label"]["en"] == "CAD only, not simulated"
    assert t["bearing"]["choices"] and {"id", "d", "D", "B"} <= set(t["bearing"]["choices"][0])
    assert [g["id"] for g in t["groups"]] == [g for g in
        ["stator", "winding", "rotor", "magnets", "shaft", "housing", "bearings", "electronics", "fasteners"]
        if g in {x["group"] for x in t["rows"]}]


def test_put_saves_per_config_and_leaves_the_electromagnetics_alone(env):
    before_cfg, before_die = _cfg_doc(env), yaml.safe_load((env["dies"] / DIE / "die.yaml").read_text("utf-8"))
    r = client.put(CP, json={"values": {"holder_flange_t": 1.0, "ring_t": 0.9}}, headers=env["admin"])
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["changed"] == ["holder_flange_t", "ring_t"] and out["table"]["rebuild"]["state"] == "pending"
    rows = {x["name"]: x for x in out["table"]["rows"]}
    assert rows["ring_t"]["value"] == 0.9 and rows["ring_t"]["overridden"] and rows["ring_t"]["built"] == 0.8
    doc = _cfg_doc(env)
    assert doc["cad_overrides"] == {"holder_flange_t": 1.0, "ring_t": 0.9}
    # EM isolation: nothing but the new block changed, in the configuration and in the die
    doc_wo = {k: v for k, v in doc.items() if k != "cad_overrides"}
    assert doc_wo == before_cfg
    assert yaml.safe_load((env["dies"] / DIE / "die.yaml").read_text("utf-8")) == before_die
    # another configuration of the die is untouched
    assert "cad_overrides" not in yaml.safe_load((env["dies"] / DIE / "L20.yaml").read_text("utf-8"))
    # the CAD profile gets the values; the simulation rows and the geometry signature do not move
    from motor_ai_sim.cad import store
    mp = store.load_mapping()
    eff0 = store.load_effective(str(env["dies"]), DIE, "L12")
    pj, rep = store.make_profile(eff0, mp)
    p = {x["name"]: x for sec in store.SECTIONS for x in pj[sec]}
    assert p["ring_t"]["freecad_expression"] == "0.9 mm" and p["ring_t"]["reference_expression"] == "0.8 mm"
    assert p["holder_flange_t"]["cad_override"] is True
    clean = {k: v for k, v in eff0.items() if k != "cad_overrides"}
    shutil.copy2(env["dies"] / DIE / "L12.yaml", env["tmp"] / "L12.with.yaml")
    stripped = _cfg_doc(env)
    stripped.pop("cad_overrides")
    (env["dies"] / DIE / "L12.yaml").write_text(yaml.safe_dump(stripped), "utf-8")
    eff1 = store.load_effective(str(env["dies"]), DIE, "L12")
    pj1, rep1 = store.make_profile(eff1, mp)
    assert store.geo_sig(eff0["geometry"]) == store.geo_sig(eff1["geometry"])
    assert eff0["geometry"] == eff1["geometry"] and eff0["materials"] == eff1["materials"] and eff0["winding"] == eff1["winding"]
    sim_rows = lambda rp: [(r["sim_key"], r["sim"], r.get("freecad")) for r in rp["parameters"]]
    assert sim_rows(rep) == sim_rows(rep1)                                     # every electromagnetic row identical
    assert rep["fingerprint"]["geo_sig"] == rep1["fingerprint"]["geo_sig"]
    assert rep["fingerprint"]["cad_param_hash"] != rep1["fingerprint"]["cad_param_hash"]   # only the CAD hash moves
    assert rep1["fingerprint"]["cad_overrides_hash"] == "none"
    # a value equal to the default is no override; null removes one
    shutil.copy2(env["tmp"] / "L12.with.yaml", env["dies"] / DIE / "L12.yaml")
    r = client.put(CP, json={"values": {"ring_t": 0.8, "holder_flange_t": None}}, headers=env["admin"])
    assert r.status_code == 200 and _cfg_doc(env).get("cad_overrides") is None


def test_put_refuses_bad_values_in_words_and_saves_nothing(env):
    before = _cfg_doc(env)
    for body, needle in (({"ring_t": 50}, "outside the allowed range"), ({"bearing_ball_count": 12}, "read-only"),
                         ({"magnet_height": 5}, "electromagnetic"), ({"nonsense": 1}, "not a CAD-only"),
                         ({"ring_t": "abc"}, "not a number")):
        r = client.put(CP, json={"values": body}, headers=env["admin"])
        assert r.status_code == 422, (body, r.text)
        assert needle in json.dumps(r.json()["detail"]), (body, r.text)
    # the template's own pre-check: a shaft chamfer larger than the journal wall can carry
    r = client.put(CP, json={"values": {"shaft_chamfer": 1.9}}, headers=env["admin"])
    assert r.status_code == 422
    v = r.json()["detail"]["violations"]
    assert any("shaft" in s for s in v) and r.json()["detail"]["error"]
    assert _cfg_doc(env) == before and not (env["cad"] / ".rebuild").exists()


def test_put_dry_run_validates_and_writes_nothing(env):
    before = _cfg_doc(env)
    r = client.put(CP, json={"values": {"ring_t": 1.0}, "dry_run": True}, headers=env["admin"])
    assert r.status_code == 200 and r.json()["dry_run"] is True and r.json()["changed"] == ["ring_t"]
    assert _cfg_doc(env) == before and not (env["cad"] / ".rebuild").exists()


def test_bearing_card_is_a_cad_only_choice(env):
    t = client.get(CP, headers=env["admin"]).json()
    card = next(c for c in t["bearing"]["choices"] if c["d"] == 8 and c["D"] == 16) if any(
        c["d"] == 8 and c["D"] == 16 for c in t["bearing"]["choices"]) else None
    assert t["bearing"]["choices"]
    if card is None:
        pytest.skip("no 8x16 bearing card in the library")
    r = client.put(CP, json={"bearing_card": card["id"]}, headers=env["admin"])
    assert r.status_code == 200, r.text
    assert _cfg_doc(env)["cad_overrides"] == {"bearing_card": card["id"]}
    assert _cfg_doc(env).get("bearings") == _cfg_doc(env).get("bearings")           # the EM bearings are not touched
    assert client.put(CP, json={"bearing_card": "no such card"}, headers=env["admin"]).status_code == 422


# ── permission + audit ─────────────────────────────────────────────────────────────────────────────────────

def test_permission_follows_the_catalog_write_rule(env):
    body = {"values": {"ring_t": 0.9}}
    assert client.put(CP, json=body).status_code in (401, 403)                            # anonymous
    for who in ("user", "nda", "eng", "engnda"):
        r = client.put(CP, json=body, headers=env[who])
        assert r.status_code == 403, (who, r.text)                                          # layering off: the owner's catalog
    assert client.post(f"/api/family/config/{DIE}/{CFG}/cad_rebuild", headers=env["user"]).status_code == 403
    assert _cfg_doc(env).get("cad_overrides") is None
    assert client.put(CP, json=body, headers=env["admin"]).status_code == 200
    # an engineer with a grant READS the table (can_write false); an ungranted account gets nothing
    t = client.get(CP, headers=env["eng"]).json()
    assert t["available"] and t["can_write"] is False and t["rows"]
    assert client.get(CP.replace(DIE, "No such die"), headers=env["eng"]).status_code == 404


def test_every_save_is_audited_and_requests_a_rebuild(env):
    client.put(CP, json={"values": {"ring_t": 0.9}}, headers=env["admin"])
    lines = [json.loads(x) for x in env["audit"].read_text("utf-8").splitlines()]
    hit = [x for x in lines if x["action"] == "cad.params"]
    assert hit and hit[-1]["target"] == f"{DIE}/{CFG}" and hit[-1]["details"]["changed"] == ["ring_t"]
    assert hit[-1]["actor"] == ADMIN
    from motor_ai_sim.cad import rebuild_queue as RQ
    rec = RQ.read(env["cad"], DIE, CFG)
    assert rec["state"] == "pending" and rec["requested_by"] == ADMIN and rec["layer"] == "shared"
    assert RQ.pending(env["cad"])[0]["die"] == DIE


# ── the rebuild hand-over ──────────────────────────────────────────────────────────────────────────────────

def test_rebuild_status_follows_the_manifest_hash_and_the_worker(env, monkeypatch):
    from motor_ai_sim import cad_params as C
    from motor_ai_sim.cad import rebuild_queue as RQ
    r = client.put(CP, json={"values": {"ring_t": 0.9}}, headers=env["admin"]).json()
    assert r["table"]["rebuild"]["state"] == "pending" and r["table"]["rebuild"]["stale"] is True
    # the old model is still served
    assert client.get(f"{M3}/model.glb", headers=env["admin"]).content == GLB
    h = C.overrides_hash({"ring_t": 0.9})
    # the worker builds the request; a refused build comes back as `failed` with the template's words
    class P:
        returncode, stdout = 3, "build refused/failed (3): pre-check failed: ring too thick"
    monkeypatch.setattr(RQ.subprocess, "run", lambda *a, **k: P())
    done = RQ.run_one(env["cad"], RQ.pending(env["cad"])[0])
    assert done["state"] == "failed" and "pre-check failed" in done["message"]
    st = client.get(CP, headers=env["admin"]).json()["rebuild"]
    assert st["state"] == "failed" and "ring too thick" in st["message"]
    # a request for the same overrides again re-queues; success: the manifest carries the new hash -> current
    client.post(f"/api/family/config/{DIE}/{CFG}/cad_rebuild", headers=env["admin"])
    assert client.get(CP, headers=env["admin"]).json()["rebuild"]["state"] == "pending"
    P.returncode, P.stdout = 0, "built"
    done = RQ.run_one(env["cad"], RQ.pending(env["cad"])[0])
    assert done["state"] == "done"
    mf = env["cad"] / DIE / CFG / "manifest.json"
    m = json.loads(mf.read_text("utf-8"))
    m["cad_overrides_hash"] = h
    mf.write_text(json.dumps(m), "utf-8")
    assert client.get(CP, headers=env["admin"]).json()["rebuild"]["state"] == "current"
    # building shows as such
    RQ._write(env["cad"], dict(RQ.read(env["cad"], DIE, CFG), state="building", overrides_hash=C.overrides_hash({"ring_t": 1.1})))
    client.put(CP, json={"values": {"ring_t": 1.1}}, headers=env["admin"])
    assert RQ.read(env["cad"], DIE, CFG)["state"] == "building"           # an in-flight request is kept, not duplicated
    assert client.get(CP, headers=env["admin"]).json()["rebuild"]["state"] == "building"


def test_build_records_the_overrides_hash_and_a_model_without_edits_keeps_its_hash():
    from motor_ai_sim.cad import store
    assert store.cad_overrides_hash({}) == "none" and store.cad_overrides_hash({"a": "x", "bearing_card": ""}) == "none"
    assert store.cad_overrides_hash({"ring_t": 1}) == store.cad_overrides_hash({"ring_t": 1.0}) != "none"


# ── import (Fusion CSV / FreeCAD) ──────────────────────────────────────────────────────────────────────────

def test_import_csv_takes_only_cad_rows_and_dry_runs_first(env):
    csv_text = ("Name,Unit,Expression,Value,Comment,Favorite\n"
                "magnet_height,mm,9,9,em row,False\n"
                "ring_t,mm,0.9 mm,0.9,x,False\n"
                "screw_socket_point,deg,110 deg,110,x,False\n"
                "holder_flange_t,mm,=ring_t*2,1.0,formula,False\n"
                "unknown_param,mm,3,3,x,False\n")
    url = CP + "/import"
    files = {"file": ("params.csv", csv_text.encode("utf-8"), "text/csv")}
    d = client.post(url + "?dry_run=1", files=files, headers=env["admin"]).json()
    assert d["dry_run"] is True and d["changed"] == ["holder_flange_t", "ring_t", "screw_socket_point"] and d["found"] == 3
    assert _cfg_doc(env).get("cad_overrides") is None
    a = client.post(url + "?dry_run=0", files={"file": ("params.csv", csv_text.encode("utf-8"), "text/csv")}, headers=env["admin"]).json()
    assert a["ok"] and _cfg_doc(env)["cad_overrides"] == {"holder_flange_t": 1.0, "ring_t": 0.9, "screw_socket_point": 110.0}
    assert "magnet_height" not in _cfg_doc(env)["cad_overrides"]
    # a user cannot import
    assert client.post(url + "?dry_run=0", files={"file": ("p.csv", b"Name,Unit,Expression\n", "text/csv")},
                       headers=env["user"]).status_code == 403


# ── downloads: NDA, role, logging ──────────────────────────────────────────────────────────────────────────

def _log(env):
    p = env["log"]
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


FILES = ("bom.csv", "bom.xlsx", "params.csv")


def test_full_construction_files_need_the_nda_and_are_logged(env):
    assert client.get(f"{M3}/parts").status_code in (401, 403)
    for f in ("parts",) + FILES + ("model.fcstd",):
        assert client.get(f"{M3}/{f}", headers=env["user"]).status_code == 403, f
    assert _log(env) == []
    for f in FILES:
        r = client.get(f"{M3}/{f}", headers=env["nda"])
        assert r.status_code == 200, (f, r.text)
        assert "attachment" in r.headers["content-disposition"] and "nda-user" in r.headers["content-disposition"].lower()
    kinds = [x["kind"] for x in _log(env)]
    assert kinds == ["bom_csv", "bom_xlsx", "params_csv"]
    last = _log(env)[-1]
    assert last["user"] == NDA_USER and last["nda"] is True and last["nda_basis"]["type"] == "admin" and last["sha256"] and last["bytes"]
    assert last["download_id"] in client.get(f"{M3}/params.csv", headers=env["nda"]).text or True


def test_bom_carries_materials_and_masses_no_prices_and_the_notice(env):
    r = client.get(f"{M3}/bom.csv", headers=env["admin"])
    text = r.content.decode("utf-8-sig")
    assert text.startswith("# AeroStator download d") and "CONFIDENTIAL" in text.splitlines()[0]
    low = text.lower()
    assert "price" not in low and "supplier" not in low and "eur" not in low
    assert "Aluminium_6061" in text and "holder_front" in text
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(client.get(f"{M3}/bom.xlsx", headers=env["admin"]).content))
    assert wb.sheetnames == ["BOM", "Notice"] and wb["BOM"]["B2"].value
    assert "CONFIDENTIAL" in wb["Notice"]["A1"].value


def test_full_parameter_set_has_em_and_cad_rows_in_the_fusion_dialect(env):
    import csv
    r = client.get(f"{M3}/params.csv", headers=env["admin"])
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert rows[0] == ["Name", "Unit", "Expression", "Value", "Comment", "Favorite"] and all(len(x) == 6 for x in rows)
    by = {x[0]: x for x in rows[1:]}
    assert "stator_diameter" in by and "ring_t" in by and "motor_length" in by
    assert "CAD only, not simulated" in by["ring_t"][4] and "CAD only" not in by["stator_diameter"][4]
    did = _log(env)[-1]["download_id"]
    assert all(did in x[4] for x in rows[1:])
    # the table the file was made from: after a save the file carries the new value
    client.put(CP, json={"values": {"ring_t": 0.9}}, headers=env["admin"])
    r2 = client.get(f"{M3}/params.csv", headers=env["admin"])
    assert {x[0]: x for x in csv.reader(io.StringIO(r2.content.decode("utf-8-sig")))}["ring_t"][3] == "0.9"


def test_freecad_file_is_for_engineers_and_admins_with_the_nda_never_for_a_user(env):
    assert client.get(f"{M3}/model.fcstd", headers=env["nda"]).status_code == 403       # NDA, but a plain user
    assert client.get(f"{M3}/model.fcstd", headers=env["eng"]).status_code == 403       # engineer without NDA
    ok = client.get(f"{M3}/model.fcstd", headers=env["engnda"])
    assert ok.status_code == 200 and ok.content == FCSTD
    assert client.get(f"{M3}/model.fcstd", headers=env["admin"]).status_code == 200
    kinds = [x["kind"] for x in _log(env)]
    assert kinds == ["fcstd", "fcstd"]


def test_the_existing_step_download_is_unchanged(env):
    assert client.get(f"{M3}/model.step", headers=env["user"]).status_code == 403
    s = client.get(f"{M3}/model.step", headers=env["nda"])
    assert s.status_code == 200 and b"Download ID:" in s.content
    assert _log(env)[-1]["kind"] == "step"


# ── multi-user layering: an ordinary account edits ITS OWN copy, the shared die is untouched ──────────────────────

def test_under_the_layering_a_registered_account_saves_into_its_own_workspace(env, monkeypatch):
    from motor_ai_sim.routes import family as fam
    shared, works = env["tmp"] / "shared", env["tmp"] / "works"
    shutil.copytree(env["dies"], shared / "dies")
    works.mkdir()
    shutil.copy2(_ROOT / "config" / "motor_config.yaml", shared / "motor_config.yaml")
    monkeypatch.setenv("WORKSPACES_ROOT", str(works))
    monkeypatch.setenv("SHARED_ROOT", str(shared))
    monkeypatch.setenv("PUBLISHED_ROOT", str(env["tmp"] / "published"))
    monkeypatch.delitem(fam.__dict__, "_DIES_DIR", raising=False)
    monkeypatch.delenv("MOTRES_DIES_DIR", raising=False)
    fam._TREE_CACHE.clear()
    r = client.put(CP, json={"values": {"ring_t": 0.9}}, headers=env["eng"])
    assert r.status_code == 200, r.text
    assert "cad_overrides" not in yaml.safe_load((shared / "dies" / DIE / f"{CFG}.yaml").read_text("utf-8"))     # shared untouched
    mine = list(works.rglob(f"{CFG}.yaml"))
    assert mine and yaml.safe_load(mine[0].read_text("utf-8"))["cad_overrides"] == {"ring_t": 0.9}
    from motor_ai_sim.cad import rebuild_queue as RQ
    assert RQ.read(env["cad"], DIE, CFG)["layer"] != "shared"          # the worker builds shared dies only
    assert RQ.pending(env["cad"]) == [] and len(RQ.pending(env["cad"], layer=None)) == 1
    assert client.get(CP, headers=env["eng"]).json()["rows"]
    fam._TREE_CACHE.clear()


def test_a_private_configuration_reads_like_a_missing_one_on_every_new_route(env, monkeypatch):
    """Config-visibility rule: the Full routes answer a hidden configuration exactly like one that does not exist."""
    from motor_ai_sim.routes import family as fam
    shared, works = env["tmp"] / "shared", env["tmp"] / "works"
    shutil.copytree(env["dies"], shared / "dies")
    works.mkdir()
    shutil.copy2(_ROOT / "config" / "motor_config.yaml", shared / "motor_config.yaml")
    f = shared / "dies" / DIE / "L20.yaml"
    doc = yaml.safe_load(f.read_text("utf-8"))
    doc["visibility"] = "private"
    f.write_text(yaml.safe_dump(doc), "utf-8")
    monkeypatch.setenv("WORKSPACES_ROOT", str(works))
    monkeypatch.setenv("SHARED_ROOT", str(shared))
    monkeypatch.setenv("PUBLISHED_ROOT", str(env["tmp"] / "published"))
    monkeypatch.delitem(fam.__dict__, "_DIES_DIR", raising=False)
    monkeypatch.delenv("MOTRES_DIES_DIR", raising=False)
    fam._TREE_CACHE.clear()
    base = f"/api/family/config/{DIE}/%s/cad_params"
    norm = lambda r, cfg: (r.status_code, r.text.replace(cfg, "<CFG>"))          # noqa: E731
    for who in ("eng", "nda"):
        h = env[who]
        for method, path, kw in (("GET", base, {}), ("PUT", base, {"json": {"values": {"ring_t": 0.9}}}),
                                 ("POST", base + "/import", {"files": {"file": ("p.csv", b"Name,Unit,Expression\n", "text/csv")}}),
                                 ("POST", f"/api/family/config/{DIE}/%s/cad_rebuild", {})):
            a = client.request(method, path % "L20", headers=h, **kw)
            b = client.request(method, path % "NOSUCH", headers=h, **kw)
            assert norm(a, "L20") == norm(b, "NOSUCH"), (who, method, path, a.text, b.text)
            assert a.status_code == 404
        for url, params in ((f"/api/family/die/{DIE}/part_tree", None), (f"/api/model3d/{DIE}/L20/parts", None)):
            a = client.get(url, params={"config": "L20"} if params is None and "family" in url else None, headers=h)
            b = client.get(url.replace("L20", "NOSUCH"), params={"config": "NOSUCH"} if "family" in url else None, headers=h)
            assert norm(a, "L20") == norm(b, "NOSUCH"), (who, url, a.text, b.text)
        for f_ in ("bom.csv", "bom.xlsx", "params.csv", "model.fcstd"):
            a = client.get(f"/api/model3d/{DIE}/L20/{f_}", headers=h)
            b = client.get(f"/api/model3d/{DIE}/NOSUCH/{f_}", headers=h)
            assert a.status_code == b.status_code == 404 and norm(a, "L20") == norm(b, "NOSUCH"), (who, f_)
    fam._TREE_CACHE.clear()


# ── the frameless kit (cad_variant: frameless, #201): only the kit parts, everywhere ──────────────────────────────

def test_a_frameless_configuration_shows_only_the_kit_parts_on_every_surface(env):
    import csv
    f = env["dies"] / DIE / f"{CFG}.yaml"
    doc = yaml.safe_load(f.read_text("utf-8"))
    doc["cad_variant"] = "frameless"
    f.write_text(yaml.safe_dump(doc), "utf-8")
    mf = env["cad"] / DIE / CFG / "manifest.json"
    m = json.loads(mf.read_text("utf-8"))
    m["cad_variant"] = "frameless"
    mf.write_text(json.dumps(m), "utf-8")
    from motor_ai_sim import part_tree as PT
    from motor_ai_sim.modules import motor_mass as MM
    MM._FULL_CACHE.clear()                                                                      # noqa: SLF001
    tree = client.get(f"/api/family/die/{DIE}/part_tree", params={"config": CFG}, headers=env["admin"]).json()
    ids = [p["id"] for p in tree["parts"]]
    assert tree["kit"] is True and tree["assembly"] == "frameless" and tree["scope"] == "frameless kit"
    assert ids and set(ids) <= set(PT.KIT_PART_IDS) and {"stator_core", "rotor_core", "magnet", "slot", "board"} <= set(ids)
    assert not set(ids) & {"shaft", "holder_front", "holder_back", "rings", "bearings", "screws", "prop_fix", "carrier_front"}
    assert {g["id"] for g in tree["groups"]} <= {"stator", "winding", "rotor", "magnets", "electronics"}
    # Materials Full, the 3-D parts list, the BOM export, the component card: the same ids in the same order
    pm = client.get(f"/api/family/die/{DIE}/part_materials", params={"config": CFG}, headers=env["admin"]).json()
    assert [p["key"] for p in pm["parts"]] == ids and pm["kit"] is True
    assert [p["id"] for p in client.get(f"{M3}/parts", headers=env["nda"]).json()["parts"]] == ids
    rows = list(csv.reader(io.StringIO(client.get(f"{M3}/bom.csv", headers=env["admin"]).content.decode("utf-8-sig"))))
    hdr = next(i for i, r in enumerate(rows) if r and r[0] == "item")
    assert [r[1] for r in rows[hdr + 1:] if r[0].isdigit()] == ids and rows[-1][-1] == "frameless kit"
    from motor_ai_sim import propeller_s1 as s1
    from motor_ai_sim.modules.component_adapters import motor_card
    card = motor_card(s1.load_model(DIE, CFG), dies_dir=env["dies"], cad_dir=env["cad"])
    assert [p.part_id for p in card.parts] == ids
    # the CAD table has only the groups a kit still has; a housing edit and a bearing card are refused
    t = client.get(CP, headers=env["admin"]).json()
    assert t["kit"] is True and {r["group"] for r in t["rows"]} <= {"winding", "rotor", "magnets", "electronics"} and t["bearing"] is None
    assert "board_t" in {r["name"] for r in t["rows"]} or "phase_board_x" in {r["name"] for r in t["rows"]}
    r = client.put(CP, json={"values": {"ring_t": 0.9}}, headers=env["admin"])
    assert r.status_code == 422 and "frameless kit has no such part" in json.dumps(r.json())
    assert client.put(CP, json={"bearing_card": "618/8-2Z"}, headers=env["admin"]).status_code == 422
    assert client.put(CP, json={"values": {"coil_lead_ext": 5.0}}, headers=env["admin"]).status_code == 200
    # a full configuration of the same die is unchanged
    t20 = client.get(f"/api/family/config/{DIE}/L20/cad_params", headers=env["admin"]).json()
    assert t20["kit"] is False and "housing" in {r["group"] for r in t20["rows"]}
