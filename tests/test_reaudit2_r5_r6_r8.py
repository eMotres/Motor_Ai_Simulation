# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Codex Astra RE-AUDIT #2 2026-10-20 — R5, R6, R8.

R5 (BLOCKER) — the CAD bearing basis (``bearings.thermal_path``) must not reach a
caller without the full-construction gate through a FILE: the layered
copy-on-write (any save of a shared configuration — the controller here — and
every sibling configuration of the copied die), the account ZIP export, and the
copies written before this rule (``construction_privacy scrub-workspaces``).  An
authorized caller keeps it; the shared originals never change; ordinary edits
and the export of the person's own data still work.

R6 (HIGH) — the open rotor-face film's note and the rotor channels' note print
the face air speed (= the wash × the MEASURED open fraction): for a caller
without the gate they read neutrally, without numbers — fresh payloads and
payloads stored before the cooling-level source key existed.

R8 (MEDIUM) — the live route's heat budget counts the friction generated in the
solved bearing nodes: element + node generation = the whole-system input, and
the node friction's split (rotor + stator + straight to the air) sums to it.

Synthetic stores and scalar models only: no FEM, no CAD build, no server.
"""
from __future__ import annotations

import ast
import io
import sqlite3
import zipfile
from pathlib import Path

import pytest
import yaml

from tests.test_config_visibility import DIE, PLAIN, PUB, client
from tests.test_construction_nda_gate import VERIFIED
from tests.test_reaudit_r1_r2_r3 import (FACTORY, NEEDLES, SECRET, _thermal_result,  # noqa: F401
                                         env, gate, planted, thermal_last)

SIBLING = "L42"


def _clean(text: str) -> bool:
    return not any(n in text for n in NEEDLES)


# ── R5: copy-on-write, export, cleanup ──────────────────────────────────────

@pytest.fixture()
def layered(planted, monkeypatch, tmp_path):
    """The planted catalog, layered only, with a public SIBLING configuration
    that carries the basis too, and the account-export metadata stores kept in
    memory (the workspace archive and both HTTP export endpoints stay real)."""
    if not planted["layered"]:
        pytest.skip("copy-on-write and the per-account export exist with layering only")
    from motor_ai_sim import account_lifecycle as AL
    from motor_ai_sim import admin_audit as AA
    from motor_ai_sim import agent_keys as AK
    from motor_ai_sim import comm_prefs as CP
    from motor_ai_sim import newsletter as NL
    from motor_ai_sim import oauth as OAuth
    from motor_ai_sim import sessions as Sessions
    from motor_ai_sim import support_store as Support
    from motor_ai_sim import usage_stats as Usage
    sib = yaml.safe_load(planted["cfg_path"].read_text("utf-8"))
    sib["name"] = SIBLING
    (planted["dies_dir"] / DIE / f"{SIBLING}.yaml").write_text(yaml.safe_dump(sib), encoding="utf-8")
    monkeypatch.setattr(NL, "_load", lambda: {"subscribers": {}})
    monkeypatch.setattr(Support, "list_access_requests", lambda: [])
    monkeypatch.setattr(Sessions, "list_for", lambda _e: [])
    monkeypatch.setattr(Sessions, "_EVENTS_FILE", tmp_path / "auth_events.jsonl")
    monkeypatch.setattr(AK, "list_keys", lambda _e: [])
    monkeypatch.setattr(AK, "audit_path", lambda: tmp_path / "agent_audit.jsonl")
    monkeypatch.setattr(OAuth, "list_grants", lambda _e: [])
    monkeypatch.setattr(CP, "export_for", lambda _e: {})
    monkeypatch.setattr(AA, "for_subject", lambda _e, limit=10000: [])

    def _db():
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE activity (day,event,key,n,user)")
        con.execute("CREATE TABLE storage (day,category,bytes,user)")
        con.execute("CREATE TABLE usage (run_id,ts_start,ts_end,kind,machine,wall_s,cpu_s,status,user)")
        return con
    monkeypatch.setattr(Usage, "_db", _db)
    real = AL.build_export
    monkeypatch.setattr(AL, "build_export",
                        lambda email, dest=None: real(email, dest=tmp_path / ("export-%s.zip" % email)))
    shared = {p.name: p.read_bytes() for p in (planted["dies_dir"] / DIE).glob("*.yaml")}
    return dict(planted, shared_before=shared)


def _ws_cfg(email, name=PUB):
    from motor_ai_sim import account_lifecycle as AL
    return AL._ws_dirs(email)["workspace"] / "dies" / DIE / f"{name}.yaml"


def _export(headers):
    link = client.post("/api/account/export", headers=headers)
    assert link.status_code == 200, link.text[:300]
    got = client.get(link.json()["url"])
    assert got.status_code == 200, got.text[:300]
    return zipfile.ZipFile(io.BytesIO(got.content))


def _shared_unchanged(env):
    now = {p.name: p.read_bytes() for p in (env["dies_dir"] / DIE).glob("*.yaml")}
    return now == env["shared_before"]


@pytest.mark.parametrize("who,email", [("plain", PLAIN), ("factory", FACTORY)])
def test_a_save_without_the_gate_copies_no_basis_and_the_zip_has_none(layered, who, email):
    from motor_ai_sim import construction_privacy as CPV
    assert CPV.may_see_full_construction(layered[who]["Authorization"]) is False
    r = client.patch(f"/api/family/config/{DIE}/{PUB}/controller",
                     json={"devices_parallel": 2}, headers=layered[who])
    assert r.status_code == 200, r.text[:300]
    # the copy: the edited configuration AND its sibling, with the public bearings
    for name in (PUB, SIBLING):
        doc = yaml.safe_load(_ws_cfg(email, name).read_text("utf-8"))
        assert doc["bearings"]["A"]["card"] == "618/8-2Z", name
        assert "thermal_path" not in doc["bearings"], name
        assert _clean(_ws_cfg(email, name).read_text("utf-8")), name
    # the edit itself landed (a normal save still works)
    assert yaml.safe_load(_ws_cfg(email).read_text("utf-8"))["controller"]["devices_parallel"] == 2
    hist = list(_ws_cfg(email).parent.parent.rglob("*.yaml"))
    assert all(_clean(p.read_text("utf-8")) for p in hist)          # .history included
    with _export(layered[who]) as z:
        names = z.namelist()
        assert f"workspace/dies/{DIE}/{PUB}.yaml" in names              # their own data is exported
        assert all(_clean(z.read(n).decode("utf-8", "replace")) for n in names)
    assert _shared_unchanged(layered)


def test_an_authorized_caller_keeps_the_basis_in_the_copy_and_the_zip(layered):
    r = client.patch(f"/api/family/config/{DIE}/{PUB}/controller",
                     json={"devices_parallel": 2}, headers=layered["verified"])
    assert r.status_code == 200, r.text[:300]
    doc = yaml.safe_load(_ws_cfg(VERIFIED).read_text("utf-8"))
    assert doc["bearings"]["thermal_path"] == SECRET
    with _export(layered["verified"]) as z:
        packed = yaml.safe_load(z.read(f"workspace/dies/{DIE}/{PUB}.yaml"))
    assert packed["bearings"]["thermal_path"] == SECRET
    assert _shared_unchanged(layered)


def _plant_old_copy(email):
    """A workspace copy written BEFORE this fix: the basis inside, plus a stored
    thermal payload that carries it."""
    p = _ws_cfg(email)
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = {"name": PUB, "die": DIE, "bearings": {"A": {"card": "618/8-2Z"},
                                                  "lubrication": "grease",
                                                  "thermal_path": SECRET}}
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    import json
    (p.parent / "thermal_last.json").write_text(json.dumps(_thermal_result()), encoding="utf-8")
    return p


def test_an_old_dirty_copy_is_scrubbed_in_the_export(layered):
    _plant_old_copy(PLAIN)
    with _export(layered["plain"]) as z:
        for n in z.namelist():
            assert _clean(z.read(n).decode("utf-8", "replace")), n
        packed = yaml.safe_load(z.read(f"workspace/dies/{DIE}/{PUB}.yaml"))
    assert packed["bearings"] == {"A": {"card": "618/8-2Z"}, "lubrication": "grease"}


def test_the_cleanup_command_scrubs_old_copies_of_owners_without_the_gate(layered):
    from motor_ai_sim import construction_privacy as CPV
    dirty = _plant_old_copy(PLAIN)
    kept = _plant_old_copy(VERIFIED)
    dry = CPV.scrub_workspaces(dry_run=True)
    assert dry["dry_run"] and dry["files_scrubbed"] >= 2 and dry["errors"] == 0
    assert "thermal_path" in dirty.read_text("utf-8")                  # a dry run writes nothing
    rep = CPV.scrub_workspaces(dry_run=False)
    assert rep["files_scrubbed"] >= 2 and rep["errors"] == 0
    assert _clean(dirty.read_text("utf-8"))
    assert _clean((dirty.parent / "thermal_last.json").read_text("utf-8"))
    assert yaml.safe_load(dirty.read_text("utf-8"))["bearings"]["A"]["card"] == "618/8-2Z"
    assert yaml.safe_load(kept.read_text("utf-8"))["bearings"]["thermal_path"] == SECRET
    assert _shared_unchanged(layered)
    assert CPV.scrub_workspaces(dry_run=False)["files_scrubbed"] == 0  # idempotent


def test_scrub_bytes_on_each_document_kind():
    import gzip
    import json
    from motor_ai_sim import construction_privacy as CPV
    y = yaml.safe_dump({"bearings": {"A": {"card": "x"}, "lubrication": "grease",
                                     "thermal_path": SECRET}}).encode()
    j = json.dumps({"cfg": {"bearings": {"A": {"card": "x"}, "temp_c": 70,
                                         "thermal_path": SECRET}}}).encode()
    jl = (json.dumps({"thermal_path": SECRET}) + "\n" + json.dumps({"ok": 1}) + "\n").encode()
    for name, data in (("a.yaml", y), ("a.json", j), ("a.jsonl", jl),
                       ("a.json.gz", gzip.compress(j))):
        out = CPV.scrub_bytes(name, data)
        text = (gzip.decompress(out) if name.endswith(".gz") else out).decode()
        assert _clean(text), name
    assert CPV.scrub_bytes("a.yaml", b"name: clean\n") is None          # nothing to do
    assert CPV.scrub_bytes("a.npz", b"thermal_path") is None             # not a document
    with pytest.raises(ValueError):
        CPV.scrub_bytes("a.json", b"{thermal_path: broken")


# ── R6: the CAD-derived speed in the notes ──────────────────────────────────

def _with_real_face_note(result, *, cooling_key=True):
    from motor_ai_sim.simulation.cooling_models import (rotor_axial_channels,
                                                        rotor_end_faces_open)
    face = rotor_end_faces_open(air_speed_mps=12.0 * 0.4321, rpm=0.0, t_wall_c=25.0,
                                t_ambient_c=25.0, area_m2=1e-4, radius_m=0.015)
    result["cooling"]["end_faces"]["rotor"].update(
        {k: face[k] for k in ("mode", "film_kind", "air_speed_mps", "note", "h_conv",
                              "h_plate", "nu", "re", "G_W_per_K", "orientation")})
    ch = rotor_axial_channels(channels=[], length_m=0.012, face_speed_mps=12.0 * 0.4321,
                              rpm=0.0, t_air_c=25.0, t_wall_c=60.0)
    result["cooling"]["rotor_channels"]["note"] = ch["note"]
    if not cooling_key:
        # a payload stored before #351: channels OFF (no source key on the block)
        # and no cooling-level key; only the channels' notes name the CAD fraction
        rc = result["cooling"]["rotor_channels"]
        rc.pop("holder_open_fraction_source")
        rc.pop("holder_open_fraction")
        rc["mode"] = "off"
        result["cooling"].pop("holder_open_fraction_source")
    return result


@pytest.mark.parametrize("stored", [False, True])
@pytest.mark.parametrize("who", ["plain", "factory"])
def test_the_face_speed_is_not_in_any_note_without_the_gate(thermal_last, monkeypatch, who, stored):
    from motor_ai_sim.routes import thermal
    result = _with_real_face_note(_thermal_result(), cooling_key=not stored)
    monkeypatch.setattr(thermal, "_LAST", {"field": {"result": result, "params": {},
                                                      "geometry_fingerprint": None}})
    r = client.get("/api/thermal/last", headers=thermal_last[who])
    assert r.status_code == 200, r.text[:300]
    cooling = r.json()["field"]["result"]["cooling"]
    face = cooling["end_faces"]["rotor"]
    assert face.get("air_speed_mps") is None and face.get("re") is None and face.get("h_plate") is None
    assert "m/s" not in face["note"] and "Re_D" not in face["note"]
    assert face["note"] == "detailed cooling paths with NDA"         # re-audit #3 B2
    assert "m/s" not in cooling["rotor_channels"]["note"]
    for needle in ("5.2 m/s", "5.19 m/s", "Re_D", "0.43,", "0.4321"):
        assert needle not in r.text, (who, stored, needle)
    assert face.get("G_W_per_K") is None          # B2: no per-path G of a CAD-derived paths


def test_the_admin_still_reads_the_note_as_it_was(thermal_last, monkeypatch):
    from motor_ai_sim.routes import thermal
    result = _with_real_face_note(_thermal_result())
    monkeypatch.setattr(thermal, "_LAST", {"field": {"result": result, "params": {},
                                                      "geometry_fingerprint": None}})
    r = client.get("/api/thermal/last", headers=thermal_last["admin"])
    assert "flat plate at 5.2 m/s" in r.json()["field"]["result"]["cooling"]["end_faces"]["rotor"]["note"]


def test_no_other_note_in_the_bearing_network_echoes_a_cad_number():
    """Every text the bearing network writes from the CAD block is a resistance
    ``source`` or the film's — both replaced; the notes carry no CAD number."""
    import json
    from motor_ai_sim.construction_privacy import redact
    from tests.test_reaudit_r1_r2_r3 import _bearing_node_block
    bn = {"cooling": {"bearing_node": _bearing_node_block()}}
    raw = json.dumps(bn, ensure_ascii=False)
    assert "7.778" in raw                                                 # the CAD numbers are in it...
    out = json.dumps(redact(bn), ensure_ascii=False)
    for needle in ("7.778", "13.131", "31.4 mm", "27.2 cm", "29.979", "30 mm envelope", "segments_mm"):
        assert needle not in out, needle                                  # ...and none survives


# ── R8: the route's whole-system budget ─────────────────────────────────────

ROUTE = Path(__file__).resolve().parents[1] / "src" / "motor_ai_sim" / "routes" / "thermal.py"


def _fn() -> ast.FunctionDef:
    tree = ast.parse(ROUTE.read_text(encoding="utf-8"))
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                and n.name == "solve_thermal_field")


def _assigned(name):
    for n in ast.walk(_fn()):
        if (isinstance(n, (ast.Assign, ast.AnnAssign))
                and isinstance(getattr(n, "targets", [getattr(n, "target", None)])[0], ast.Name)
                and getattr(n, "targets", [getattr(n, "target", None)])[0].id == name):
            return n.value
    raise AssertionError(name)


def _dict_value(assignment, key):
    d = _assigned(assignment)
    for k, v in zip(d.keys, d.values):
        if isinstance(k, ast.Constant) and k.value == key:
            return v
    raise AssertionError((assignment, key))


def _eval(expr, values):
    return eval(compile(ast.Expression(expr), str(ROUTE), "eval"),
                {"round": round, "float": float}, values)


def test_the_route_budget_counts_the_bearing_node_friction():
    """Astra's R8 repro (10 W element sources + 2 W in the solved bearing nodes,
    closed shaft ends), evaluating the route's own expressions: the whole-system
    input is 12 W and the friction's split sums to the 2 W."""
    from motor_ai_sim.simulation import bearing_holder as bh
    v = {"_p_brg_in": 2.0, "_brg_modelled": False, "_brg_at_nodes": True,
         "_p_wind_gap": 0.0, "_wind_modelled": False, "_p_wind_faces": 0.0,
         "_gen_w": 10.0, "_p_brg_dropped": 0.0, "_p_wind_dropped": 0.0}
    v["_p_mech_in"] = _eval(_dict_value("mech_losses", "P_mech_into_map_W"), v)
    v["_p_node_gen_w"] = _eval(_assigned("_p_node_gen_w"), v)
    assert _eval(_dict_value("mech_losses", "P_mech_at_nodes_W"), v) == 2.0
    sources = bh.node_sources({"P_to_rotor_W": 1.1, "P_to_stator_W": 0.6,
                               "P_to_air_W": 0.3}, v["_p_node_gen_w"])
    v["_bn_sources"] = sources
    got = {k: _eval(_dict_value("heat_budget", k), v) for k in (
        "losses_W", "losses_at_bearing_nodes_W", "losses_total_W", "bearing_friction_W",
        "bearing_friction_in_map_W", "bearing_friction_at_nodes_W", "mech_loss_in_map_W")}
    assert got == {"losses_W": 10.0, "losses_at_bearing_nodes_W": 2.0, "losses_total_W": 12.0,
                   "bearing_friction_W": 2.0, "bearing_friction_in_map_W": 0.0,
                   "bearing_friction_at_nodes_W": 2.0, "mech_loss_in_map_W": 0.0}
    # the dynamic nodes + the direct-to-air share = the node friction; + elements = the input
    assert sum(sources.values()) == pytest.approx(2.0)
    assert got["losses_W"] + sum(sources.values()) == pytest.approx(got["losses_total_W"])
    # open shaft ends: unchanged, the friction is an element source
    v2 = dict(v, _brg_modelled=True, _brg_at_nodes=False, _gen_w=12.0)
    v2["_p_node_gen_w"] = _eval(_assigned("_p_node_gen_w"), v2)
    v2["_p_mech_in"] = _eval(_dict_value("mech_losses", "P_mech_into_map_W"), v2)
    v2["_bn_sources"] = None
    assert _eval(_dict_value("heat_budget", "losses_total_W"), v2) == 12.0
    assert _eval(_dict_value("heat_budget", "bearing_friction_in_map_W"), v2) == 2.0


def test_node_sources_without_a_split_go_to_the_rotor():
    from motor_ai_sim.simulation import bearing_holder as bh
    assert bh.node_sources({}, 2.0) == {"to_rotor_W": 2.0, "to_stator_W": 0.0, "to_air_W": 0.0}
