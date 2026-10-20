# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Astra re-audit #3 independent probes, adapted to the fixed behaviour (B2 flipped). No FEM."""
import gzip
import io
import json
import math
from pathlib import Path
import zipfile

import pytest
import yaml

from tests.test_config_visibility import client, DIE, PUB, PLAIN
from tests.test_reaudit_r1_r2_r3 import env, gate, planted, thermal_last, _thermal_result
from tests.test_reaudit2_r5_r6_r8 import layered, _export, _ws_cfg

OUT = Path(__file__).resolve().parent


def evidence(name, obj):
    """Astra wrote evidence files here; the repository copy only checks."""
    json.dumps(obj)


def area_of(result, tag):
    total = 0.0
    for tri, domain in zip(result["triangles"], result["domain_per_tri"]):
        if domain != tag:
            continue
        a, b, c = [result["vertices"][i] for i in tri]
        total += abs((b[0]-a[0])*(c[1]-a[1]) -
                     (b[1]-a[1])*(c[0]-a[0])) / 2
    return total * result["symmetry_mult"]


@pytest.mark.parametrize("who", ["plain", "factory"])
def test_public_mesh_and_conductance_no_longer_recover_the_fraction(thermal_last, monkeypatch, who):
    from motor_ai_sim.routes import thermal
    from motor_ai_sim.simulation.cooling_models import rotor_end_faces_open, air_properties
    from motor_ai_sim.simulation.sb_domains import DOM_ROTOR
    radius, wash, fraction = 0.015, 12.0, 0.4321
    # A hand-written 8-triangle section, not a generated mesh or a solve.
    result = _thermal_result()
    result.update(vertices=[[0., 0.]] + [
        [radius*math.cos(i*math.pi/4), radius*math.sin(i*math.pi/4)]
        for i in range(8)],
        triangles=[[0, i+1, (i+1) % 8+1] for i in range(8)],
        domain_per_tri=[DOM_ROTOR]*8, symmetry_mult=1, rpm=0.0,
        ambient_temp=40.0)
    a = area_of(result, DOM_ROTOR)
    face = rotor_end_faces_open(air_speed_mps=wash*fraction, rpm=0.,
        t_wall_c=90., t_ambient_c=40., area_m2=a, radius_m=radius)
    # Use route precision and route field names, not the model's extra fields.
    result["cooling"]["end_faces"]["rotor"] = {
        "film_kind": "forced", "mode": "forced", "n_faces": 2,
        "area_m2": round(a*2, 8), "area_per_face_m2": round(a, 8),
        "char_len_mm": radius*2000, "h_conv": round(face["h_conv"], 3),
        "G_W_per_K": round(face["G_W_per_K"], 5),
        "heat_removed_W": round(face["heat_removed_W"], 3),
        "t_wall_c": 90.0, "t_sink_c": 40.0, "t_mean_c": 90.0,
        "air_speed_mps": round(wash*fraction, 2), "note": face["note"]}
    result["cooling"]["gap_flow"] = {"air_speed_mps": wash}
    monkeypatch.setattr(thermal, "_LAST", {"field": {
        "result": result, "params": {"rpm": 0.0}, "geometry_fingerprint": None}})
    resp = client.get("/api/thermal/last", headers=thermal_last[who])
    assert resp.status_code == 200
    public = resp.json()["field"]["result"]
    f = public["cooling"]["end_faces"]["rotor"]
    # B2, flipped: the CAD-derived face carries no G, no watts, no temperature
    # and no area, so the mesh area has nothing to divide
    for k in ("G_W_per_K", "heat_removed_W", "t_wall_c", "t_mean_c", "t_sink_c",
              "h_conv", "area_m2", "air_speed_mps", "n_faces"):
        assert f.get(k) is None, k
    assert area_of(public, DOM_ROTOR) > 0          # the mesh itself stays public
    hb = public["cooling"].get("heat_budget") or {}
    assert {k for k, v in hb.items() if v is not None} <= {
        "losses_W", "losses_total_W", "residual_W", "residual_pct", "heat_to_air_W",
        "detail", "symmetry_mult", "frame", "note", "passes"}


def test_zip_documents_and_compression(layered):
    root = _ws_cfg(PLAIN).parent
    root.mkdir(parents=True, exist_ok=True)
    secret = {"bearings": {"A": {"card": "synthetic"}, "temp_c": 60,
                           "thermal_path": {"rotor_face_open_fraction": 0.4321}}}
    blobs = {"a.yaml": yaml.safe_dump(secret).encode(),
             "b.yml": yaml.safe_dump(secret).encode(),
             "c.json": json.dumps(secret).encode(),
             "d.jsonl": (json.dumps(secret)+"\n"+json.dumps({"own": 42})+"\n").encode()}
    blobs.update({n+".gz": gzip.compress(b) for n, b in list(blobs.items())})
    blobs["bad.json"] = b'{"thermal_path": broken'
    for name, data in blobs.items():
        (root/name).write_bytes(data)
    before = {n: (root/n).read_bytes() for n in blobs}
    with _export(layered["plain"]) as z:
        checked = []
        for name in blobs:
            member = f"workspace/dies/{DIE}/{name}"
            if name == "bad.json":
                assert member not in z.namelist()
                assert member in z.read("workspace/WITHHELD.txt").decode()
                continue
            data = z.read(member)
            if name.endswith(".gz"):
                data = gzip.decompress(data)
            assert b"thermal_path" not in data and b"0.4321" not in data
            assert b"synthetic" in data
            checked.append(name)
    assert {n: (root/n).read_bytes() for n in blobs} == before
    evidence("zip_formats", {"redacted_formats": checked, "redacted_count": len(checked),
                             "withheld_malformed": 1, "source_files_unchanged": True})


def test_old_dirty_workspace_save_and_publish(layered):
    from tests.test_reaudit2_r5_r6_r8 import _plant_old_copy
    from motor_ai_sim.routes import family
    from motor_ai_sim import workspace
    p = _plant_old_copy(PLAIN)
    response = client.patch(f"/api/family/config/{DIE}/{PUB}/controller",
                            json={"devices_parallel": 3}, headers=layered["plain"])
    assert response.status_code == 200, response.text
    assert "thermal_path" not in p.read_text("utf-8")
    assert all("thermal_path" not in h.read_text("utf-8")
               for h in p.parent.parent.rglob("*.yaml"))
    # Exercise the common publish document seam under the actual non-gated identity.
    from motor_ai_sim.auth import caller_identity
    doc = {"bearings": {"thermal_path": {"rotor_face_open_fraction": 0.4321}}}
    from unittest.mock import patch
    with patch.object(workspace, "caller", return_value=caller_identity(layered["plain"]["Authorization"])):
        dst = p.parent/"published_probe.yaml"
        family._pub_put(dst, doc)
    assert "thermal_path" not in dst.read_text("utf-8")
    evidence("old_save_publish", {"controller_status": response.status_code,
                                 "saved_clean": True, "history_clean": True,
                                 "publish_document_clean": True})
