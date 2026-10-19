# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Template A.8 hand-over without FreeCAD (docs/FUSION_SYNC.md section 4): the profile block made from the latest Fusion
export, the refusal of an export without the required parts, the STEP / GLB / BOM stamps.  The FreeCAD build from a bundle
runs in the server sandbox CAD image (docs/FUSION_SYNC.md section 7)."""
from __future__ import annotations

import csv
import json
import struct

import pytest

from motor_ai_sim.fusion_sync import profile as FSP
from motor_ai_sim.fusion_sync import stamp as ST
from motor_ai_sim.fusion_sync import store as FS

DIE = "CIANO14 50 edited"


def _store_bundle(tmp_path, monkeypatch, comps=("Core", "Rotor", "Magnet", "Coil", "Shaft", "Ring")):
    monkeypatch.setenv("MOTRES_FUSION_SYNC_ROOT", str(tmp_path / "fusion"))
    monkeypatch.delenv("FUSION_SYNC_DIES", raising=False)
    parts = {c: dict(file="parts/%s.step" % c, bodies=[dict(name="B", volume_mm3=10.0), dict(name="C", volume_mm3=2.5)], occurrences=[])
             for c in comps}
    files = {"parameters.json": b"[]", "assembly.step": b"A"}
    files.update({"parts/%s.step" % c: c.encode() for c in comps})
    import hashlib
    m = dict(schema=FS.SCHEMA, document="CIANO14 50", urn="urn:x", version=3, exported_at="2026-10-19T21:00:00", exported_by="Owner",
             parts=parts, sketches={}, files={k: dict(sha256=hashlib.sha256(v).hexdigest()) for k, v in files.items()})
    files["manifest.json"] = json.dumps(m).encode()               # read_bundle keeps the zip's manifest among the files
    FS.store_bundle(DIE, "L15", m, files, b"zip", "admin@example.com")


def test_profile_block_from_the_latest_export(tmp_path, monkeypatch):
    _store_bundle(tmp_path, monkeypatch)
    fp = FSP.fusion_parts_for(DIE, {"motor_length": 15})
    assert fp["source"] == "bundle" and fp["dies"] == [DIE] and fp["ref_motor_length"] == 15
    assert fp["files"] == {"Core": "Core.step", "RotorCore": "Rotor.step", "Magnet": "Magnet.step", "Coil": "Coil.step",
                           "Shaft": "Shaft.step", "Ring": "Ring.step"}
    assert fp["volumes_mm3"]["Core"] == 12.5 and fp["multi"] == {"Coil": "fuse"}
    assert fp["dir"].replace("\\", "/").endswith("CIANO14_50_edited/bundles/v3/parts")
    assert fp["bundle"]["version"] == 3 and fp["bundle"]["document"] == "CIANO14 50"
    assert FSP.stamp_text(fp["bundle"]) == "Fusion 'CIANO14 50' v3"
    assert FSP.fusion_parts_for("CIANO14 40 new", {"motor_length": 12}) is None          # not synced: template / reference parts


def test_export_without_a_required_part_refuses_the_build(tmp_path, monkeypatch):
    _store_bundle(tmp_path, monkeypatch, comps=("Core", "Rotor", "Shaft"))
    with pytest.raises(FSP.MissingFusionPart) as e:
        FSP.fusion_parts_for(DIE, {"motor_length": 15})
    assert "Magnet" in str(e.value) and "Coil" in str(e.value)
    from motor_ai_sim.cad import store as CS
    with pytest.raises(RuntimeError):
        CS.fusion_bundle_parts(DIE, {"motor_length": 15})


def test_step_stamp(tmp_path):
    p = tmp_path / "m.step"
    p.write_bytes(b"ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('FreeCAD Model'),'2;1');\nFILE_NAME('m','t',('a'),('b'),'c','d','e');\n"
                  b"ENDSEC;\nDATA;\n#1=CARTESIAN_POINT('',(0.,0.,0.));\nENDSEC;\nEND-ISO-10303-21;\n")
    assert ST.stamp_step(str(p), "Fusion 'CIANO14 50' v3 -> AeroStator CAD")
    head = p.read_bytes().split(b"ENDSEC;")[0]
    assert b"FILE_DESCRIPTION(('Fusion ''CIANO14 50'' v3 -> AeroStator CAD','FreeCAD Model'),'2;1');" in head
    assert p.read_bytes().count(b"FILE_DESCRIPTION") == 1
    q = tmp_path / "n.step"
    q.write_bytes(b"no header")
    assert not ST.stamp_step(str(q), "x") and q.read_bytes() == b"no header"


def _glb(js: dict, binary: bytes = b"\x01\x02\x03\x04") -> bytes:
    j = json.dumps(js).encode()
    j += b" " * ((4 - len(j) % 4) % 4)
    body = struct.pack("<I4s", len(j), b"JSON") + j + struct.pack("<I4s", len(binary), b"BIN\x00") + binary
    return b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body


def test_glb_stamp_keeps_the_binary_chunk(tmp_path):
    p = tmp_path / "m.glb"
    p.write_bytes(_glb({"asset": {"version": "2.0"}, "meshes": []}, b"BINDATA!"))
    assert ST.stamp_glb(str(p), {"document": "CIANO14 50", "version": 3})
    data = p.read_bytes()
    assert struct.unpack_from("<I", data, 8)[0] == len(data)
    assert ST.read_glb_fusion(str(p)) == {"document": "CIANO14 50", "version": 3}
    assert data.endswith(b"BIN\x00BINDATA!")
    (tmp_path / "x.glb").write_bytes(b"not glb")
    assert not ST.stamp_glb(str(tmp_path / "x.glb"), {})


def test_bom_source_row_is_written_and_ignored_by_the_reader(tmp_path):
    from motor_ai_sim.cad import bom
    from motor_ai_sim.modules import motor_mass as MM
    b = {"lines": [dict(key="core", item="1", part="Stator core", type="part", qty=1, unit="pcs", total_mass_g=10.0,
                    in_total=True, to_be_specified=False)],
         "totals": {"motor_mass_g": 10.0}, "comparison": {"sim_source": "s", "sim_total_g": 10.0, "rows": [], "extra_parts_g": 0.0}}
    p = tmp_path / "x_BOM.csv"
    bom.write_csv(b, str(p), source="Fusion 'CIANO14 50' v3")
    rows = list(csv.reader(open(p, encoding="utf-8-sig")))
    assert rows[-1][0] == "S" and "Source: Fusion 'CIANO14 50' v3" in rows[-1]
    f = MM.read_bom_file(p)
    assert [l.part for l in f.lines] == ["Stator core"] and f.total_g == 10.0
