# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Codex Astra RE-AUDIT 2026-10-20 repro probes (astra_reaudit/test_reaudit.py + test_regressions.py),
adapted to expect the FIXED behaviour.  Synthetic stores only; no network servers, CAD builds or FEM."""
import io
import json
import zipfile

import pytest
import yaml

from tests.test_config_visibility import env, client, DIE, PUB, PRIV, NOSUCH
from tests.test_construction_nda_gate import gate
from tests.test_email_password_auth import env as auth_env, _smtp_on, NEW, PW


def test_cache_filters_memory_disk_and_load(env, monkeypatch):
    from motor_ai_sim import answer_store as st, auth, users
    from motor_ai_sim.routes import catalog
    monkeypatch.setattr(auth, "AUTH_ENFORCE", True)
    monkeypatch.setattr(catalog, "_preset_mass", lambda *a: None)
    monkeypatch.setenv(st.ENV_ROOT, str(env["tmp"] / "reaudit_answers"))
    st.clear_memory()
    url = "/api/catalog/cat_pub/configure_context"
    names = lambda r: [p["config"] for p in r.json()["presets"]]
    assert names(client.get(url, headers=env["plain"])) == [PUB]
    assert PRIV in names(client.get(url, headers=env["admin"]))
    rows = {}
    for role in ("plain", "eng"):
        rows[role] = names(client.get(url, headers=env[role]))
        assert rows[role] == [PUB]
        for config in (PRIV, NOSUCH):
            r = client.get("/api/catalog/cat_pub/configure_load",
                           params={"config": config}, headers=env[role])
            assert r.status_code == 404
        load = client.get("/api/catalog/cat_pub/configure_load",
                          params={"config": PUB}, headers=env[role])
        assert load.status_code == 200, load.text[:200]
        assert names(type("Response", (), {"json": lambda _: load.json()["context"]})()) == [PUB]
        assert "_config_access" not in load.text
    st.clear_memory()
    disk = client.get(url, headers=env["plain"])
    assert names(disk) == [PUB]
    assert "_config_access" not in disk.text
    print(json.dumps({"probe": "fixed_cache", "layered": env["layered"],
                      "roles": rows, "disk": names(disk), "private_load": 404}))


def test_short_overflow_falls_back_and_signup_succeeds(auth_env):
    from motor_ai_sim.return_path import safe_app_path
    from motor_ai_sim.api import app
    from fastapi.testclient import TestClient
    raw = "/production/a/b/documents?" + "&".join("x=1" for _ in range(21))
    assert len(raw) == 109 and safe_app_path(raw) is None
    _smtp_on(auth_env)
    r = TestClient(app, raise_server_exceptions=False).post("/api/auth/register",
        json={"email": NEW, "password": PW, "name": "Synthetic re-audit", "next": raw})
    assert r.status_code == 202
    print(json.dumps({"probe": "fixed_return_path", "length": len(raw), "http": r.status_code}))


def test_compressed_xml_is_bounded(monkeypatch):
    from motor_ai_sim.cad import store
    xml = b'<Document>' + b' ' * 1024 * 1024 + b'<Object name="Params"><Property name="cells"><Cell alias="x" content="1"/></Property></Object></Document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Document.xml", xml)
    data = buf.getvalue()
    assert store.read_fcstd_params(io.BytesIO(data)) == {"x": "1"}
    production_limit = store.MAX_DOCUMENT_XML_BYTES
    monkeypatch.setattr(store, "MAX_DOCUMENT_XML_BYTES", 65536)
    with pytest.raises(store.FcstdLimitError) as caught:
        store.read_fcstd_params(io.BytesIO(data))
    assert caught.value.status == 413
    print(json.dumps({"probe": "fixed_fcstd_limit", "production_limit": production_limit,
                      "test_limit": 65536, "compressed": len(data), "expanded": len(xml), "http": 413}))


def test_cad_denial_and_anonymous_exports(gate):
    from motor_ai_sim import auth, model3d, users
    who = auth.caller_identity(gate["plain"]["Authorization"])
    assert not model3d.can_download(who, users.get_user(who["id"]))
    r = client.get(f"/api/family/config/{DIE}/{PUB}/cad_params", headers=gate["plain"])
    assert r.status_code == 403 and "3.14159" not in r.text
    codes = {}
    for path in ("/api/freecad/export", "/api/fusion/params.csv", "/api/fusion/params.json"):
        r = client.get(path)
        codes[path] = r.status_code
        assert r.status_code == 401
    print(json.dumps({"probe": "fixed_cad", "layered": gate["layered"], "cad": 403, "anon_exports": codes}))


def test_thermal_path_is_withheld_from_the_tree(gate, monkeypatch):
    """R1, flipped: the ordinary and the online-NDA (unverified) callers no longer read the CAD dimensions."""
    from motor_ai_sim.routes import family
    from motor_ai_sim import auth, model3d, users
    path = gate["dies_dir"] / DIE / f"{PUB}.yaml"
    cfg = yaml.safe_load(path.read_text("utf-8"))
    secret = {"A": {"shaft_segments_mm": [[3.14159, 27.1828]],
                    "holder": {"contact_core_mm2": 31.4159, "film_area_mm2": 2718.28,
                               "arm_segments_mm": [[1.23456, 9.87654]]}},
              "rotor_face_open_fraction": 0.4321}
    cfg["bearings"]["thermal_path"] = secret
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    family._TREE_CACHE.clear()
    # Avoid unrelated mass geometry; serialization/auth/filtering run unchanged.
    monkeypatch.setattr(family, "_overlay_mass", lambda die, c: c)
    for role in ("plain", "online"):
        who = auth.caller_identity(gate[role]["Authorization"])
        assert not model3d.can_download(who, users.get_user(who["id"]))
        r = client.get("/api/family/tree", headers=gate[role])
        assert r.status_code == 200
        die = next(d for d in r.json()["dies"] if d["name"] == DIE)
        row = next(c for c in die["configs"] if c["name"] == PUB)
        assert "thermal_path" not in row["bearings"] and "3.14159" not in r.text
        assert row["bearings"]["A"]["card"] == "618/8-2Z"


def test_factory_account_no_longer_reads_thermal_path(gate, monkeypatch):
    from motor_ai_sim import auth, users, model3d
    from motor_ai_sim.routes import family
    email = "factory-reaudit@example.com"
    users.create_user(email, "synthetic-password-only", role="oem", name="Synthetic factory")
    users.set_motor_grants(email, all_motors=True)
    headers = {"Authorization": "Bearer " + users.issue_token(email)}
    who = auth.caller_identity(headers["Authorization"])
    assert not model3d.can_download(who, users.get_user(email))
    path = gate["dies_dir"] / DIE / f"{PUB}.yaml"
    cfg = yaml.safe_load(path.read_text("utf-8"))
    cfg["bearings"]["thermal_path"] = {"A": {"shaft_segments_mm": [[3.14159, 27.1828]]}}
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    family._TREE_CACHE.clear()
    monkeypatch.setattr(family, "_overlay_mass", lambda die, c: c)
    r = client.get("/api/family/tree", headers=headers)
    assert r.status_code == 200 and "3.14159" not in r.text
    assert client.get(f"/api/family/config/{DIE}/{PUB}/cad_params", headers=headers).status_code == 403
    # Match the synthetic exhibit passport to the synthetic die.
    root = gate["tmp"] / "exhibit"
    root.mkdir()
    die_path = gate["dies_dir"] / DIE / "die.yaml"
    die_doc = yaml.safe_load(die_path.read_text("utf-8"))
    geometry = die_doc["geometry"]
    geometry["stator_outer_radius"] = geometry["stator_diameter"] / 2
    die_path.write_text(yaml.safe_dump(die_doc), encoding="utf-8")
    (root / "motor_catalog.json").write_text(json.dumps({"motors": [{"passport": {
        "geo": {"numSlots": geometry["num_slots"], "numPoles": geometry["num_poles"],
                "statorOR_mm": geometry["stator_outer_radius"],
                "magnetHeight_mm": geometry["magnet_height"]}}}]}),
        encoding="utf-8")
    monkeypatch.setattr(family, "_ws_root_f", lambda: root)
    monkeypatch.setenv("PUBLIC_EXHIBIT", "1")
    family._TREE_CACHE.clear()
    anon = client.get("/api/family/tree")
    assert anon.status_code == 200 and "3.14159" not in anon.text
    # Record anonymous behavior with the ordinary production door closed.
    monkeypatch.setenv("PUBLIC_EXHIBIT", "0")
    assert client.get("/api/family/tree").status_code == 401



def test_new_bearing_heat_reaches_the_transient_segment():
    from motor_ai_sim import thermal_duty_cycle as dc, coupled_time_to_limit as ttl
    summary = {"P_stranded_W": 10.0, "P_core_W": 0.0, "P_solid_W": 0.0,
               "coil_temp_C": 100.0, "rpm": 1000.0}
    thermal = {"P_cu_exact_W": 10.0, "P_mag_eddy_W": 0.0,
               "cooling": {"bearing_node": {"mode": "solved", "friction_W": 2.0},
                           "mech_losses": {"P_bearings_W": 2.0,
                                           "P_bearings_at_bearing_nodes_W": 2.0,
                                           "bearings_modelled": True,
                                           "shaft_ends_open": False,
                                           "P_windage_gap_W": 0.0}}}
    loss = dc.losses_by_node(summary, thermal)
    segment = ttl._segment(summary, thermal, "synthetic")
    # R2, flipped: this map has no source split (cooling.bearing_node.delta), so the
    # friction is put on the rotor node - the 2 W are no longer lost
    assert loss["total_W"] == 12.0 and segment.total_W == 12.0
    assert loss["input_total_W"] == 12.0
    assert not any("NOT in this network" in n for n in loss["notes"])


def test_bearing_save_keeps_the_cad_thermal_path(gate):
    path = gate["dies_dir"] / DIE / f"{PUB}.yaml"
    cfg = yaml.safe_load(path.read_text("utf-8"))
    cfg["bearings"]["thermal_path"] = {"A": {"shaft_segments_mm": [[3.14159, 27.1828]]}}
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    r = client.patch(f"/api/family/config/{DIE}/{PUB}/bearings",
                     headers=gate["admin"],
                     json={"A": {"card": "618/8-2Z"}, "B": {"card": "618/8-2Z"},
                           "temp_source": "thermal"})
    assert r.status_code == 200, r.text
    saved = yaml.safe_load(path.read_text("utf-8"))
    # R3, flipped: the same cards - the measured basis is kept, and the answer says so
    assert saved["bearings"]["thermal_path"] == {"A": {"shaft_segments_mm": [[3.14159, 27.1828]]}}
    assert r.json()["thermal_path_status"]["status"] == "kept"
