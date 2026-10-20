# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Codex Astra RE-AUDIT #3 2026-10-20 — B1, B2.

B1 (BLOCKER) — the protected construction schema is defined ONCE
(``construction_privacy``: ``cad_overrides``, die.yaml's ``part_materials``,
``bearings.thermal_path`` / any non-public bearings key, and — fail closed — any
other ``cad_*`` key of a die / configuration document except ``cad_variant``)
and applied at every copy / save / history / publish / export / scrub seam.
Ordinary and factory callers without the gate get none of it in their workspace
copies or their ZIP; an authorized caller keeps it; old copies and history are
scrubbed at export and by ``scrub-workspaces``; the shared originals never change.

B2 (BLOCKER) — a caller without the gate gets NO per-path detail of the
cooling paths that ran on a CAD-measured input (rotor end faces in the wash,
rotor channels, the bearing / holder network): no G, no watts, no temperature
difference per such path, and a heat budget of motor-level aggregates only — so
nothing combines with the public mesh area into the open fraction.  Fresh,
stored (compact per-duty), fitted-network, split and heat-path-model copies.

Synthetic stores, a hand-written mesh and scalar models only: no FEM.
"""
from __future__ import annotations

import json
import math

import pytest
import yaml

from tests.test_config_visibility import DIE, PLAIN, PUB, client
from tests.test_construction_nda_gate import VERIFIED
from tests.test_reaudit_r1_r2_r3 import (FACTORY, _thermal_result, env, gate,  # noqa: F401
                                         planted, thermal_last)
from tests.test_reaudit2_r5_r6_r8 import SIBLING, _export, _ws_cfg, layered  # noqa: F401

OVERRIDES = {"shaft_shoulder_len": 3.14159}
NEW_CAD_KEY = "cad_holder_pockets"        # a construction key nobody has whitelisted
B1_NEEDLES = ("cad_overrides", "3.14159", "part_materials", NEW_CAD_KEY, "PA66-GF30-secret",
              "thermal_path", "0.4321")
NDA = "detailed cooling paths with NDA"


def _b1_clean(text: str) -> bool:
    return not any(n in text for n in B1_NEEDLES)


# ── B1 ──────────────────────────────────────────────────────────────────────

@pytest.fixture()
def construction(layered):
    """The layered catalog with the WHOLE construction schema on the shared
    documents: cad_overrides (the real producer shape) and an unknown cad_* key
    on the configuration and its sibling, part_materials on die.yaml."""
    from motor_ai_sim import cad_params
    from motor_ai_sim.routes import family
    assert cad_params.clean_overrides(OVERRIDES) == OVERRIDES
    for name in (PUB, SIBLING):
        p = layered["dies_dir"] / DIE / f"{name}.yaml"
        doc = yaml.safe_load(p.read_text("utf-8"))
        doc[cad_params.OVERRIDES_KEY] = OVERRIDES
        doc[NEW_CAD_KEY] = {"depth_mm": 1.23456}
        doc["cad_variant"] = "full"
        p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    die = layered["dies_dir"] / DIE / "die.yaml"
    d = yaml.safe_load(die.read_text("utf-8"))
    d["part_materials"] = {"holder": "PA66-GF30-secret"}
    die.write_text(yaml.safe_dump(d), encoding="utf-8")
    family._TREE_CACHE.clear()
    shared = {p.name: p.read_bytes() for p in (layered["dies_dir"] / DIE).glob("*.yaml")}
    return dict(layered, shared_before=shared)


def _shared_unchanged(env):
    return {p.name: p.read_bytes() for p in (env["dies_dir"] / DIE).glob("*.yaml")} == env["shared_before"]


def test_the_schema_is_one_list_and_fails_closed():
    from motor_ai_sim import construction_privacy as CPV
    doc = {"name": "L1", "geometry_overrides": {}, "cad_overrides": {"a": 1},
           "cad_variant": "full", "cad_tomorrow": 1, "bearings": {"A": {"card": "x"},
                                                                 "thermal_path": {}},
           "controller": {"device": "y"}}
    assert set(CPV.protected_doc_keys(doc)) == {"cad_overrides", "cad_tomorrow", "bearings"}
    out = CPV.redact(doc)
    assert "cad_overrides" not in out and "cad_tomorrow" not in out
    assert out["cad_variant"] == "full" and out["controller"] == {"device": "y"}
    assert out["bearings"] == {"A": {"card": "x"}}
    assert CPV.redact({"geometry": {}, "part_materials": {"x": "y"}}) == {"geometry": {}}


@pytest.mark.parametrize("who,email", [("plain", PLAIN), ("factory", FACTORY)])
def test_without_the_gate_no_construction_block_reaches_the_copy_or_the_zip(construction, who, email):
    denied = client.get(f"/api/family/config/{DIE}/{PUB}/cad_params", headers=construction[who])
    assert denied.status_code == 403
    r = client.patch(f"/api/family/config/{DIE}/{PUB}/controller",
                     json={"devices_parallel": 2}, headers=construction[who])
    assert r.status_code == 200, r.text[:300]
    ws_die = _ws_cfg(email).parent
    for p in list(ws_die.glob("*.yaml")) + list(ws_die.parent.rglob(".history/**/*.yaml")):
        assert _b1_clean(p.read_text("utf-8")), p
    doc = yaml.safe_load(_ws_cfg(email).read_text("utf-8"))
    assert doc["cad_variant"] == "full" and doc["controller"]["devices_parallel"] == 2
    with _export(construction[who]) as z:
        assert f"workspace/dies/{DIE}/{PUB}.yaml" in z.namelist()
        for n in z.namelist():
            assert _b1_clean(z.read(n).decode("utf-8", "replace")), n
    assert _shared_unchanged(construction)


def test_an_authorized_caller_keeps_the_construction(construction):
    r = client.patch(f"/api/family/config/{DIE}/{PUB}/controller",
                     json={"devices_parallel": 2}, headers=construction["verified"])
    assert r.status_code == 200, r.text[:300]
    doc = yaml.safe_load(_ws_cfg(VERIFIED).read_text("utf-8"))
    assert doc["cad_overrides"] == OVERRIDES and doc[NEW_CAD_KEY] == {"depth_mm": 1.23456}
    die = yaml.safe_load((_ws_cfg(VERIFIED).parent / "die.yaml").read_text("utf-8"))
    assert die["part_materials"] == {"holder": "PA66-GF30-secret"}
    with _export(construction["verified"]) as z:
        packed = yaml.safe_load(z.read(f"workspace/dies/{DIE}/{PUB}.yaml"))
    assert packed["cad_overrides"] == OVERRIDES
    assert _shared_unchanged(construction)


def _plant_old(email):
    """A copy + a history snapshot written BEFORE this fix, construction inside."""
    p = _ws_cfg(email)
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = {"name": PUB, "die": DIE, "geometry_overrides": {"motor_length": 40.0},
           "cad_overrides": OVERRIDES, NEW_CAD_KEY: {"depth_mm": 1.23456}}
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    h = p.parent.parent / ".history" / DIE
    h.mkdir(parents=True, exist_ok=True)
    (h / f"{PUB}.20261019-120000.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    (p.parent / "die.yaml").write_text(yaml.safe_dump(
        {"name": DIE, "geometry": {"num_slots": 12}, "part_materials": {"holder": "PA66-GF30-secret"}}),
        encoding="utf-8")
    return p, h


def test_old_copies_and_history_are_scrubbed_at_export_and_by_the_command(construction):
    from motor_ai_sim import construction_privacy as CPV
    p, h = _plant_old(PLAIN)
    kept, _ = _plant_old(VERIFIED)
    with _export(construction["plain"]) as z:
        names = z.namelist()
        assert any(".history" in n for n in names)
        for n in names:
            assert _b1_clean(z.read(n).decode("utf-8", "replace")), n
    assert "cad_overrides" in p.read_text("utf-8")                    # the export never edits
    dry = CPV.scrub_workspaces(dry_run=True)
    assert dry["files_scrubbed"] >= 3 and dry["errors"] == 0
    assert "cad_overrides" in p.read_text("utf-8")                    # a dry run writes nothing
    rep = CPV.scrub_workspaces(dry_run=False)
    assert rep["files_scrubbed"] >= 3 and rep["errors"] == 0
    for f in [p, p.parent / "die.yaml"] + list(h.glob("*.yaml")):
        assert _b1_clean(f.read_text("utf-8")), f
    assert "cad_overrides" in kept.read_text("utf-8")                 # the gated owner keeps hers
    assert _shared_unchanged(construction)
    assert CPV.scrub_workspaces(dry_run=False)["files_scrubbed"] == 0


# ── B2 ──────────────────────────────────────────────────────────────────────

def _mesh_result(radius=0.015):
    """Astra's re-audit #3 inversion setup: a hand-written 8-triangle rotor
    section, a real rotor-face film at 12 m/s × 0.4321, per-path numbers at
    route precision, and the heat budget / splits that name the path."""
    from motor_ai_sim.simulation.cooling_models import rotor_end_faces_open
    from motor_ai_sim.simulation.sb_domains import DOM_ROTOR
    res = _thermal_result()
    res.update(vertices=[[0., 0.]] + [[radius * math.cos(i * math.pi / 4),
                                       radius * math.sin(i * math.pi / 4)] for i in range(8)],
               triangles=[[0, i + 1, (i + 1) % 8 + 1] for i in range(8)],
               domain_per_tri=[DOM_ROTOR] * 8, symmetry_mult=1, ambient_temp=40.0)
    a = sum(abs((res["vertices"][t[1]][0] - res["vertices"][t[0]][0])
                * (res["vertices"][t[2]][1] - res["vertices"][t[0]][1])
                - (res["vertices"][t[1]][1] - res["vertices"][t[0]][1])
                * (res["vertices"][t[2]][0] - res["vertices"][t[0]][0])) / 2
            for t in res["triangles"])
    face = rotor_end_faces_open(air_speed_mps=12.0 * 0.4321, rpm=0.0, t_wall_c=90.0,
                                t_ambient_c=40.0, area_m2=a, radius_m=radius)
    res["cooling"]["end_faces"] = {
        "mode": "open", "sides": 2,
        "rotor": {**{k: face[k] for k in face if not isinstance(face[k], dict)},
                  "t_mean_c": 90.0},
        "winding": {"film_kind": "natural", "heat_removed_W": 1.0, "G_W_per_K": 0.01}}
    res["cooling"]["gap_flow"] = {"air_speed_mps": 12.0, "rotor_side_W": 0.3}
    res["cooling"]["heat_budget"] = {
        "losses_W": 30.0, "losses_total_W": 30.1, "residual_W": 0.05, "residual_pct": 0.2,
        "housing_W": 20.0, "end_faces_W": face["heat_removed_W"] + 1.0, "rotor_W": 4.0,
        "gap_W": 2.0, "bore_W": 0.0, "bearings_W": 0.2, "rotor_channels_W": 0.0,
        "bearing_holders_from_stator_W": 1.5, "symmetry_mult": 1,
        "rotor_heat_split": {"gap_W": 2.0, "axial_end_faces_W": face["heat_removed_W"]},
        "stator_heat_split": {"stator_W": 26.0, "end_faces_W": 1.0},
        "bearing_node_sources_W": {"to_rotor_W": 0.1, "to_stator_W": 0.05, "to_air_W": 0.05}}
    return res, face, a


def _numbers(obj):
    """Every (path, number) inside ``obj``."""
    out = []

    def walk(o, path):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, path + (k,))
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, path + (i,))
        elif isinstance(o, (int, float)) and not isinstance(o, bool):
            out.append((path, o))
    walk(obj, ())
    return out


@pytest.mark.parametrize("who", ["plain", "factory"])
def test_no_per_path_number_of_a_cad_derived_path_leaves(thermal_last, monkeypatch, who):
    """From a non-gated response, no G, W or temperature of a CAD-derived path
    exists — so nothing combines with the mesh area into the open fraction."""
    from motor_ai_sim.routes import thermal
    res, face, area = _mesh_result()
    monkeypatch.setattr(thermal, "_LAST", {"field": {"result": res, "params": {"rpm": 0.0},
                                                      "geometry_fingerprint": None}})
    r = client.get("/api/thermal/last", headers=thermal_last[who])
    assert r.status_code == 200, r.text[:300]
    pub = r.json()["field"]["result"]
    c = pub["cooling"]
    # the mesh is still public (it is the geometry the user sees anyway)...
    assert pub["triangles"] and pub["vertices"]
    # ...and the CAD-derived paths carry no number at all
    assert _numbers(c["end_faces"]["rotor"]) == []
    assert _numbers(c["rotor_channels"]) == []
    assert [p for p, _ in _numbers(c["bearing_node"])
            if not str(p[-1]).endswith("_c")] == []                 # only bearing temperatures
    # the heat budget: motor-level aggregates only
    from motor_ai_sim.construction_privacy import BUDGET_PUBLIC_KEYS
    hb = c["heat_budget"]
    assert {k for k, v in hb.items() if v is not None} <= BUDGET_PUBLIC_KEYS
    assert hb["heat_to_air_W"] == pytest.approx(30.05) and hb["losses_total_W"] == 30.1
    assert c["cooling_detail"] == NDA and hb["detail"] == NDA
    # Astra's inversion has nothing to start from: G, W, ΔT of the face are gone
    for k in ("G_W_per_K", "heat_removed_W", "t_wall_c", "t_mean_c", "t_sink_c",
              "h_conv", "area_m2", "air_speed_mps"):
        assert c["end_faces"]["rotor"].get(k) is None, k
    for v in (face["G_W_per_K"], face["heat_removed_W"]):
        assert json.dumps(round(v, 5)) not in r.text
    # a non-CAD path keeps its detail (the winding end face in the wash)
    assert c["end_faces"]["winding"]["heat_removed_W"] == 1.0


def test_the_admin_keeps_the_per_path_detail(thermal_last, monkeypatch):
    from motor_ai_sim.routes import thermal
    res, face, _a = _mesh_result()
    monkeypatch.setattr(thermal, "_LAST", {"field": {"result": res, "params": {},
                                                      "geometry_fingerprint": None}})
    c = client.get("/api/thermal/last", headers=thermal_last["admin"]).json()["field"]["result"]["cooling"]
    assert c["end_faces"]["rotor"]["G_W_per_K"] == pytest.approx(face["G_W_per_K"])
    assert c["heat_budget"]["rotor_W"] == 4.0 and "cooling_detail" not in c


def test_stored_network_split_and_heat_path_copies_are_reduced_too():
    """The compact per-duty map, the fitted transient network, its time-averaged
    split and the heat-path model of a CAD-derived map: each carries the marker
    and, through ``redact``, no per-path number of a CAD-derived path."""
    from motor_ai_sim import construction_privacy as CPV
    from motor_ai_sim import duty_results as DR
    from motor_ai_sim import thermal_duty_cycle as TDC
    from motor_ai_sim.thermal_heat_paths import heat_path_model
    res, face, _a = _mesh_result()
    res["components"] = {n: {"avg": t, "max": t} for n, t in
                         (("winding", 120.0), ("stator", 100.0), ("rotor", 95.0), ("magnet", 94.0))}
    res["cooling"]["outer"] = {"mode": "air", "t_sink_c": 40.0, "area_m2": 0.004,
                               "heat_removed_W": 20.0}
    res["cooling"]["bearing_node"]["delta"] = {"G_rotor_air_W_per_K": 0.01,
                                               "G_rotor_stator_W_per_K": 0.02,
                                               "G_stator_air_W_per_K": 0.03}
    res["P_cu_exact_W"], res["P_mag_eddy_W"] = 20.0, 0.5
    # 1. the compact stored copy
    compact = DR.compact_thermal(res, {"rpm": 0.0}, "fp")
    assert compact["cooling"]["construction_basis"] == CPV.CONSTRUCTION_BASIS
    red = CPV.redact(compact)
    assert _numbers(red["cooling"]["end_faces"]["rotor"]) == []
    assert red["cooling"]["heat_budget"].get("end_faces_W") is None
    assert red["cooling"]["heat_budget"].get("rotor_heat_split") is None
    # 2. the fitted network
    net = TDC.network_from_steady(res, calibration_duty="x")
    rec = net.as_record()
    assert rec["calibration"]["construction_basis"] == CPV.CONSTRUCTION_BASIS
    red = CPV.redact(rec)
    # re-audit #4 superseded the per-key redaction: the whole network goes
    assert red["withheld"] is True and "G_W_per_K" not in red and "calibration" not in red
    # 3. a time-averaged split
    split = {"generated_total_W": 30.0, "rotor_side_W": 3.0, "stator_side_W": 27.0,
             "rotor_end_faces_W": 0.6, "magnet_end_faces_W": 0.1, "housing_W": 20.0,
             "bearings_W": 0.2, "gap_W": 2.0, "rotor_pct": 10.0, "stator_pct": 90.0}
    red = CPV.redact({"network": rec, "split": split})["split"]
    # re-audit #4: only the totals of a split survive
    assert set(red) <= {"generated_total_W", "heat_to_air_W", "closure_pct", "basis", "detail"}
    # 4. the heat-path model
    model = heat_path_model(res)
    assert model.get("construction_basis") == CPV.CONSTRUCTION_BASIS
    red = CPV.redact(model)
    for s in red["sinks"]:
        if s["id"] in ("end_face_rotor", "end_face_magnet"):
            assert _numbers({k: v for k, v in s.items() if k != "placement"}) == [], s
    assert red["totals"]["rotor_side_W"] is None


def test_a_non_cad_open_frame_keeps_its_detail():
    """No CAD basis, fraction 1.0 (the face speed IS the public wash): nothing
    to withhold, so a fresh payload keeps every path."""
    from motor_ai_sim import construction_privacy as CPV
    res, face, _a = _mesh_result()
    c = res["cooling"]
    c["bearing_node"] = {"mode": "off", "notes": []}
    c["rotor_channels"] = {"mode": "off", "notes": ["rotor_channels = 'off': the face fully in the wash"]}
    c["holder_open_fraction_source"] = "no CAD holder: the face is taken fully open"
    assert not CPV.cooling_is_cad_sensitive(c)
    assert CPV.redact(res)["cooling"]["heat_budget"]["rotor_W"] == 4.0


@pytest.mark.parametrize("who", ["plain", "factory", "online"])
def test_the_report_is_not_built_without_the_gate_for_a_cad_cooled_machine(planted, who):
    """A PDF / Word file is not an answer the JSON redaction can reach: the
    report of a machine whose cooling rests on the CAD basis needs the gate."""
    r = client.get(f"/api/family/report/{DIE}/{PUB}", params={"format": "pdf"},
                   headers=planted[who])
    assert r.status_code == 403 and r.json()["detail"]["code"] == "nda_required"


def test_the_report_gate_lets_the_admin_through(planted, monkeypatch):
    from motor_ai_sim import construction_privacy as CPV
    calls = []
    monkeypatch.setattr(CPV, "config_cooling_is_cad",
                        lambda *a, **k: calls.append(1) or True)
    r = client.get(f"/api/family/report/{DIE}/{PUB}", params={"format": "pdf"},
                   headers=planted["admin"])
    assert r.status_code != 403 and calls == []          # the gate passed first


def test_config_cooling_is_cad_reads_the_doc_and_the_stores(planted, monkeypatch):
    from motor_ai_sim import construction_privacy as CPV
    from motor_ai_sim import duty_results as DR
    from motor_ai_sim.routes import thermal
    monkeypatch.setattr(thermal, "_load_last", lambda: None)
    monkeypatch.setattr(thermal, "_LAST", {})
    assert CPV.config_cooling_is_cad(DIE, PUB, {"bearings": {"A": {"card": "x"}, "thermal_path": {}}})
    monkeypatch.setattr(DR, "get", lambda d, c: {})
    assert not CPV.config_cooling_is_cad(DIE, PUB, {"bearings": {"A": {"card": "x"}}})
    res, _f, _a = _mesh_result()
    monkeypatch.setattr(DR, "get", lambda d, c: {"rated": {"thermal": {"cooling": res["cooling"]}}})
    assert CPV.config_cooling_is_cad(DIE, PUB, {})
    monkeypatch.setattr(DR, "get", lambda d, c: {})
    monkeypatch.setattr(thermal, "_LAST", {"field": {"result": res}})
    assert CPV.config_cooling_is_cad(DIE, PUB, {})
