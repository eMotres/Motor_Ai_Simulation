# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""/api/fusion/sync (docs/FUSION_SYNC.md section 5): allow-list, auth (admin session or an admin's agent key with the scope
fusion:sync), link, bundle upload (validation, immutable versions, rebuild request), Import parameters, version guard.

A throwaway catalog: the public demo die "CIANO14 30_10" copied under the synced die's name, plus a stand-in of the frozen
40 mm die that must be refused.  Bundles are made with the add-in's own bundle module, so client and server agree.
"""
from __future__ import annotations

import io
import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from motor_ai_sim.api import app

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "fusion" / "AeroStatorSync"))
from aerostator_sync import bundle as AB  # noqa: E402

DIE, CFG, FROZEN = "CIANO14 50 edited", "L15", "CIANO14 40 new"
ADMIN, USER = "admin@example.com", "user@example.com"
URN = "urn:adsk.wipprod:dm.lineage:TEST"
client = TestClient(app)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from motor_ai_sim import agent_keys as AK
    from motor_ai_sim import auth
    from motor_ai_sim import users as U
    from motor_ai_sim.routes import family as fam

    dies = tmp_path / "dies"
    src = ROOT / "config" / "dies" / "CIANO14 30_10"
    for name, cfg in ((DIE, CFG), (FROZEN, "L12")):
        d = dies / name
        d.mkdir(parents=True)
        die = yaml.safe_load((src / "die.yaml").read_text(encoding="utf-8"))
        die["name"] = name
        (d / "die.yaml").write_text(yaml.safe_dump(die), encoding="utf-8")
        c = yaml.safe_load((src / "L10.yaml").read_text(encoding="utf-8"))
        c["name"], c["die"] = cfg, name
        (d / (cfg + ".yaml")).write_text(yaml.safe_dump(c), encoding="utf-8")
    monkeypatch.setattr(fam, "_DIES_DIR", dies)
    monkeypatch.setenv("MOTRES_DIES_DIR", str(dies))
    monkeypatch.setenv("MOTRES_FUSION_SYNC_ROOT", str(tmp_path / "fusion"))
    monkeypatch.delenv("FUSION_SYNC_DIES", raising=False)
    monkeypatch.setenv("MOTRES_CAD_ROOT", str(tmp_path / "cad"))
    monkeypatch.setenv("MCP_KEYS_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(tmp_path / "admin_audit.jsonl"))
    (tmp_path / "keys").mkdir()
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", True)
    U.create_user(ADMIN, "password-admin", role="admin", name="Admin")
    U.create_user(USER, "password-user", role="user", name="User")
    keys = {
        "admin": AK.create_key(ADMIN, "fusion", ["fusion:sync"])[0],
        "admin_noscope": AK.create_key(ADMIN, "mcp", ["catalog:read"])[0],
        "user": AK.create_key(USER, "fusion", ["fusion:sync"])[0],
    }
    hdr = {k: {"Authorization": "Bearer " + v} for k, v in keys.items()}
    hdr["admin_session"] = {"Authorization": "Bearer " + U.issue_token(ADMIN)}
    hdr["user_session"] = {"Authorization": "Bearer " + U.issue_token(USER)}
    geo = dict(yaml.safe_load((dies / DIE / "die.yaml").read_text("utf-8"))["geometry"])
    geo.update(yaml.safe_load((dies / DIE / (CFG + ".yaml")).read_text("utf-8")).get("geometry_overrides") or {})
    yield dict(tmp=tmp_path, dies=dies, h=hdr, keys=keys, geo=geo)


def _rows(env):
    r = client.get(f"/api/fusion/sync/{DIE}/{CFG}/params.json", headers=env["h"]["admin"])
    assert r.status_code == 200, r.text
    return r.json()["parameters"]


def make_bundle(env, tmp, version=1, overrides=None, step_stamp="t1", extra_member=None, tamper=False):
    """A bundle as the add-in writes it: parameters = the die/config's own values (+ overrides), bodies sized like the
    geometry (core / rotor / magnet bounding boxes), 14 magnet and 6 coil occurrences."""
    g = env["geo"]
    rows = _rows(env)
    params = [dict(name=r["name"], expression="%g %s" % (r["value"], r["unit"]), unit=r["unit"], value=r["value"], comment="")
              for r in rows]
    for p in params:
        if overrides and p["name"] in overrides:
            p["value"] = overrides[p["name"]]
    root = tmp / ("b%d_%s" % (version, step_stamp))
    (root / "parts").mkdir(parents=True)
    (root / "sketches").mkdir()
    (root / "parameters.json").write_text(json.dumps(params), encoding="utf-8")
    (root / "assembly.step").write_text("ISO-10303-21; assembly " + step_stamp)
    L, R = float(g["motor_length"]), float(g["stator_diameter"]) / 2
    ro = float(g.get("rotor_outer_radius") or R * 0.6)
    occ = lambda n: [dict(path="x:%d" % i, hidden=False, matrix=[1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]) for i in range(n)]
    parts = {}
    for comp, bb, n in (("Core", [-R, -R, -L / 2, R, R, L / 2], 1), ("Rotor", [-ro, -ro, -L / 2, ro, ro, L / 2], 1),
                        ("Magnet", [-1, ro - 7, -L / 2, 1, ro - 0.2, L / 2], int(g["num_seg"] * g["num_poles_per_segment"])),
                        ("Coil", [-5, R - 9, -L / 2 - 2, 5, R - 2.5, L / 2 + 2], int(g["num_seg"] * 3))):
        (root / "parts" / (comp + ".step")).write_text("ISO-10303-21; %s %s" % (comp, step_stamp))
        parts[comp] = dict(file="parts/%s.step" % comp, bodies=[dict(name="Body1", volume_mm3=1000.0 + len(comp), bbox_mm=bb)],
                           occurrences=occ(n))
    (root / "sketches" / "Core__Sketch1.dxf").write_text("0\nSECTION")
    m = AB.make_manifest(document="CIANO14 50", urn=URN, version=version, link={"die": DIE, "config": CFG}, fusion_build="t",
                         user="Owner", parts=parts, sketches={"Core/Sketch1": {"file": "sketches/Core__Sketch1.dxf"}},
                         root_dir=str(root), exported_at="2026-10-19T20:00:00")
    z = AB.write_zip(str(root), m, str(tmp / ("v%d_%s.zip" % (version, step_stamp))))
    data = Path(z).read_bytes()
    if extra_member or tamper:
        buf = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as zi, zipfile.ZipFile(buf, "w") as zo:
            for it in zi.infolist():
                blob = zi.read(it)
                if tamper and it.filename == "parts/Core.step":
                    blob = b"changed"
                zo.writestr(it.filename, blob)
            if extra_member:
                zo.writestr(extra_member, b"x")
        data = buf.getvalue()
    return data


def _upload(env, data, who="admin"):
    return client.post("/api/fusion/sync/bundles", files={"file": ("b.zip", data, "application/zip")}, headers=env["h"][who])


def _link(env, who="admin", die=DIE, cfg=CFG):
    return client.put("/api/fusion/sync/link", json=dict(urn=URN, document="CIANO14 50", die=die, config=cfg), headers=env["h"][who])


# ── auth ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_auth_admin_key_or_session_only(env):
    url = "/api/fusion/sync/targets"
    assert client.get(url).status_code == 401
    assert client.get(url, headers=env["h"]["user_session"]).status_code == 403
    r = client.get(url, headers=env["h"]["admin_noscope"])
    assert r.status_code == 403 and r.json()["detail"]["code"] == "scope_missing"
    r = client.get(url, headers=env["h"]["user"])
    assert r.status_code == 403 and r.json()["detail"]["code"] == "admin_only"
    assert client.get(url, headers=env["h"]["admin"]).status_code == 200
    assert client.get(url, headers=env["h"]["admin_session"]).status_code == 200
    from motor_ai_sim import agent_keys as AK
    kid = env["keys"]["admin"][len("emk_"):].split("_")[0]
    AK.revoke_key(ADMIN, kid)
    assert client.get(url, headers=env["h"]["admin"]).status_code == 401


def test_the_auth_door_lets_the_key_reach_the_route(env, monkeypatch):
    """With the public exhibit closed the door knows only session tokens; the prefix is on its own-credentials list."""
    from motor_ai_sim import auth
    monkeypatch.setenv("PUBLIC_EXHIBIT", "0")
    assert auth.anonymous_allowed("/api/fusion/sync/targets")
    assert client.get("/api/fusion/sync/targets", headers=env["h"]["admin"]).status_code == 200
    assert client.get("/api/fusion/sync/targets").status_code == 401


def test_fusion_scope_is_admin_only_and_never_offered_by_oauth(env):
    from motor_ai_sim import agent_keys as AK
    assert "fusion:sync" not in AK.SCOPES                       # OAuth's supported/default list
    adm = client.get("/api/agent_keys", headers=env["h"]["admin_session"]).json()
    usr = client.get("/api/agent_keys", headers=env["h"]["user_session"]).json()
    assert "fusion:sync" in adm["scopes"] and "fusion:sync" not in usr["scopes"]
    r = client.post("/api/agent_keys", json={"name": "x", "scopes": ["fusion:sync"]}, headers=env["h"]["user_session"])
    assert r.status_code == 403
    r = client.post("/api/agent_keys", json={"name": "x", "scopes": ["fusion:sync"]}, headers=env["h"]["admin_session"])
    assert r.status_code == 200 and r.json()["token"].startswith("emk_")


# ── allow-list and link ──────────────────────────────────────────────────────────────────────────────────────────────
def test_only_allow_listed_dies_are_served(env):
    t = client.get("/api/fusion/sync/targets", headers=env["h"]["admin"]).json()["targets"]
    assert [x["die"] for x in t] == [DIE] and t[0]["configs"] == [CFG]
    for url in (f"/api/fusion/sync/{FROZEN}/L12/params.json", f"/api/fusion/sync/{FROZEN}/L12/status"):
        r = client.get(url, headers=env["h"]["admin"])
        assert r.status_code == 403 and r.json()["detail"]["code"] == "fusion_sync_not_enabled"
    r = _link(env, die=FROZEN, cfg="L12")
    assert r.status_code == 403
    assert not (env["tmp"] / "fusion" / "CIANO14_40_new").exists()


def test_link_round_trip(env):
    assert client.get("/api/fusion/sync/link", params={"urn": URN}, headers=env["h"]["admin"]).status_code == 404
    r = _link(env)
    assert r.status_code == 200 and r.json()["die"] == DIE and r.json()["sketches"]
    g = client.get("/api/fusion/sync/link", params={"urn": URN}, headers=env["h"]["admin"]).json()
    assert g["config"] == CFG and g["linked_by"] == ADMIN
    r = client.put("/api/fusion/sync/link", json=dict(urn="urn:other", document="Other", die=DIE, config=CFG), headers=env["h"]["admin"])
    assert r.status_code == 409 and r.json()["detail"]["code"] == "die_linked_elsewhere"


# ── upload ───────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_upload_stores_checks_and_queues_the_rebuild(env):
    assert _link(env).status_code == 200
    r = _upload(env, make_bundle(env, env["tmp"]))
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["version"] == 1 and not j["already_stored"] and j["param_diff"] == []
    assert j["body_checks"] and all(c["ok"] for c in j["body_checks"]), j["body_checks"]
    assert j["rebuild"]["state"] == "pending" and j["rebuild"]["fusion_version"] == 1
    q = list((env["tmp"] / "cad" / ".rebuild").glob("*.json"))
    assert len(q) == 1 and json.loads(q[0].read_text())["fusion_version"] == 1
    v = env["tmp"] / "fusion" / "CIANO14_50_edited" / "bundles" / "v1"
    assert (v / "manifest.json").is_file() and (v / "parts" / "Core.step").is_file() and (v / "bundle.zip").is_file()
    lst = client.get("/api/fusion/sync/bundles", params={"die": DIE}, headers=env["h"]["admin"]).json()
    assert lst["latest"]["version"] == 1 and [x["version"] for x in lst["versions"]] == [1]
    # nothing in the die store changed
    assert sorted(p.name for p in (env["dies"] / DIE).iterdir()) == ["L15.yaml", "die.yaml"]


def test_same_version_reexport_is_idempotent_other_content_is_refused(env):
    _link(env)
    assert _upload(env, make_bundle(env, env["tmp"], step_stamp="a")).status_code == 200
    r = _upload(env, make_bundle(env, env["tmp"], step_stamp="b"))              # STEP timestamps differ, content equal
    assert r.status_code == 200 and r.json()["already_stored"] and r.json()["rebuild"] is None
    r = _upload(env, make_bundle(env, env["tmp"], step_stamp="c", overrides={"motor_length": 99.0}))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "version_changed"
    assert _upload(env, make_bundle(env, env["tmp"], version=2, step_stamp="d")).status_code == 200
    lst = client.get("/api/fusion/sync/bundles", params={"die": DIE}, headers=env["h"]["admin"]).json()
    assert lst["latest"]["version"] == 2


def test_bad_bundles_are_refused(env):
    _link(env)
    r = _upload(env, make_bundle(env, env["tmp"], tamper=True))
    assert r.status_code == 422 and "sha256 mismatch" in r.json()["detail"]["message"]
    r = _upload(env, make_bundle(env, env["tmp"], step_stamp="x", extra_member="../evil.py"))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "bad_member"
    r = _upload(env, make_bundle(env, env["tmp"], step_stamp="y", extra_member="parts/extra.step"))
    assert r.status_code == 422 and "not listed" in r.json()["detail"]["message"]
    r = _upload(env, b"not a zip")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "not_a_zip"
    assert not (env["tmp"] / "fusion" / "CIANO14_50_edited" / "bundles").exists()


def test_unlinked_document_is_refused(env):
    r = _upload(env, make_bundle(env, env["tmp"]))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_linked"


# ── Import parameters and the version guard ──────────────────────────────────────────────────────────────────────────
def test_params_are_the_named_die_config(env):
    rows = _rows(env)
    by = {r["name"]: r for r in rows}
    assert by["motor_length"]["value"] == pytest.approx(float(env["geo"]["motor_length"])) and by["motor_length"]["unit"] == "mm"
    assert "slot_hs" not in by                                   # FUSION_EXCLUDED_NAMES, like /api/fusion/params.csv
    assert by["num_seg"]["unit"] == ""


def test_status_warns_on_aerostator_change_param_diff_and_body_mismatch(env):
    _link(env)
    _upload(env, make_bundle(env, env["tmp"]))
    s = client.get(f"/api/fusion/sync/{DIE}/{CFG}/status", headers=env["h"]["admin"]).json()
    assert s["latest_export"]["version"] == 1 and s["model_built_from"] is None
    assert s["aerostator_changed_since_export"] is False and s["param_diff"] == []
    assert any("not built from Fusion" in w for w in s["warnings"])
    # AeroStator (optimiser / engineer) changes the configuration after the export
    cfile = env["dies"] / DIE / (CFG + ".yaml")
    c = yaml.safe_load(cfile.read_text("utf-8"))
    c.setdefault("geometry_overrides", {})["motor_length"] = float(env["geo"]["motor_length"]) + 3
    cfile.write_text(yaml.safe_dump(c), encoding="utf-8")
    s = client.get(f"/api/fusion/sync/{DIE}/{CFG}/status", headers=env["h"]["admin"]).json()
    assert s["aerostator_changed_since_export"] is True
    assert [d["name"] for d in s["param_diff"]] == ["motor_length"]
    assert {c["name"] for c in s["body_checks"] if c["ok"] is False} >= {"core length", "rotor length", "magnet length"}
    assert any("Import into Fusion pending" in w for w in s["warnings"])


def test_model_built_from_an_older_version_is_flagged(env):
    _link(env)
    _upload(env, make_bundle(env, env["tmp"], version=3))
    d = env["tmp"] / "cad" / DIE / CFG
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({"die": DIE, "config": CFG, "fusion": {"document": "CIANO14 50", "version": 2}}))
    s = client.get(f"/api/fusion/sync/{DIE}/{CFG}/status", headers=env["h"]["admin"]).json()
    assert s["model_built_from"]["version"] == 2
    assert any("3-D model is from CIANO14 50 v2; latest export is v3" in w for w in s["warnings"])
