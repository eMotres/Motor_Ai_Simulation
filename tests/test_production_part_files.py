"""Tooling files (owner 2026-11-12): the Tooling tab shows each part's drawings and a rotatable 3-D view of its STEP.

``GET /part_files?list=tooling`` lists the files per part; ``GET /files/<id>/model.glb`` serves the STEP converted to GLB (cached in
``viewcache/``), with the same authorization as the STEP download and a trace in the download log.  The converter child process is
replaced by a stub in the route tests (it needs OpenCASCADE); the real tessellation runs where cadquery loads.
"""
from __future__ import annotations

import json
import struct

import pytest

from tests.test_production_access import CONFIG, DIE, ENGINEER, OEM_NDA, OEM_NO_NDA, prod_env  # noqa: F401  (fixture)

BASE = f"/api/production/{DIE}/{CONFIG}"
STEP_TEXT = b"ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION((''),'2;1');\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"
PDF = b"%PDF-1.4 drawing"
PNG = b"\x89PNG\r\n\x1a\n drawing"


def _bi(en: str) -> dict:
    return {"en": en, "zh": en, "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}}


def _upload(c, headers, name, data, mime, part_id=None, step_id=None):
    form = {}
    if part_id:
        form["part_id"] = part_id
    if step_id:
        form["step_id"] = step_id
    r = c.post(f"{BASE}/documents", headers=headers, data=form, files={"file": (name, data, mime)})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture()
def prod(prod_env):
    c, admin, eng = prod_env["client"], prod_env["admin"], prod_env["engineer"]
    r = c.post(f"/api/admin/production/{DIE}/{CONFIG}", headers=admin, json={
        "title": _bi("Tooling files test"), "order_quantity": 10, "share_costs_with_oem": False,
        "members": [{"email": ENGINEER, "role": "engineer"}, {"email": OEM_NDA, "role": "oem"}, {"email": OEM_NO_NDA, "role": "oem"}]})
    assert r.status_code in (200, 201), r.text
    base = c.post(f"{BASE}/parts?list=tooling", headers=eng, json={"name": _bi("Press base"), "kind": "made", "qty_per_motor": 1}).json()
    punch = c.post(f"{BASE}/parts?list=tooling", headers=eng, json={"name": _bi("Press punch"), "kind": "made", "qty_per_motor": 1}).json()
    motor = c.post(f"{BASE}/parts", headers=eng, json={"name": _bi("Shaft"), "kind": "made", "qty_per_motor": 1}).json()
    docs = {
        "pdf": _upload(c, eng, "press_base.pdf", PDF, "application/pdf", base["id"]),
        "png": _upload(c, eng, "press_base.png", PNG, "image/png", base["id"]),
        "step": _upload(c, eng, "press_base.step", STEP_TEXT, "application/octet-stream", base["id"]),
        "other": _upload(c, eng, "press_base_notes.txt", b"notes", "text/plain", base["id"]),
        "photo": _upload(c, eng, "step_photo.png", PNG, "image/png", base["id"], step_id="s1"),
        "shaft": _upload(c, eng, "shaft.step", STEP_TEXT, "application/octet-stream", motor["id"]),
    }
    return {**prod_env, "base": base, "punch": punch, "motor": motor, "docs": docs}


@pytest.fixture()
def stub_converter(monkeypatch):
    """Replace the child process: write a one-triangle GLB, count the runs."""
    import numpy as np
    from motor_ai_sim.production import step_glb
    calls = []

    def fake(step_path, glb_path, timeout):
        calls.append(step_path.name)
        pos = np.array([[0, 0, 0], [10, 0, 0], [0, 10, 0]], dtype="float32")
        glb_path.write_bytes(step_glb.build_glb([("Part", pos, np.tile([0, 0, 1], (3, 1)), np.array([0, 1, 2]))]))

    monkeypatch.setattr(step_glb, "_run_child", fake)
    return calls


def _download_log(tmp_path, monkeypatch):
    from motor_ai_sim import model3d as M
    log = tmp_path / "downloads.jsonl"
    monkeypatch.setattr(M, "download_log_path", lambda: log)
    return log


# ── the list ────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_part_files_lists_drawings_then_step_of_each_tooling_part(prod):
    r = prod["client"].get(f"{BASE}/part_files?list=tooling", headers=prod["engineer"])
    assert r.status_code == 200, r.text
    rows = {p["part_id"]: p["files"] for p in r.json()["parts"]}
    assert [f["kind"] for f in rows[prod["base"]["id"]]] == ["pdf", "image", "step"]
    assert [f["name"] for f in rows[prod["base"]["id"]]] == ["press_base.pdf", "press_base.png", "press_base.step"]
    assert rows[prod["punch"]["id"]] == []                      # a tooling part without files is listed, empty
    assert prod["motor"]["id"] not in rows                      # the motor BOM is another list
    # no step photo, no text note
    ids = {f["file_id"] for f in rows[prod["base"]["id"]]}
    assert prod["docs"]["photo"]["file_id"] not in ids and prod["docs"]["other"]["file_id"] not in ids


def test_part_files_reads_the_part_drawing_files_and_optional_step(prod):
    c, eng = prod["client"], prod["engineer"]
    d = prod["docs"]
    r = c.patch(f"{BASE}/parts/{prod['punch']['id']}", headers=eng, json={
        "drawing_files": [{"file_id": d["pdf"]["file_id"], "kind": "pdf", "name": "punch.pdf"}],
        "optional_step": {"file_id": d["step"]["file_id"]}})
    assert r.status_code == 200, r.text
    files = {p["part_id"]: p["files"] for p in c.get(f"{BASE}/part_files?list=tooling", headers=eng).json()["parts"]}[prod["punch"]["id"]]
    assert [(f["kind"], f["name"]) for f in files] == [("pdf", "punch.pdf"), ("step", "press_base.step")]


def test_part_files_hides_internal_files_from_the_factory(prod):
    c = prod["client"]
    assert c.patch(f"{BASE}/documents/{prod['docs']['step']['id']}", headers=prod["engineer"], json={"internal": True}).status_code == 200
    kinds = lambda h: [f["kind"] for p in c.get(f"{BASE}/part_files?list=tooling", headers=h).json()["parts"]
                       if p["part_id"] == prod["base"]["id"] for f in p["files"]]
    assert kinds(prod["engineer"]) == ["pdf", "image", "step"]
    assert kinds(prod["oem_nda"]) == ["pdf", "image"]


def test_part_files_needs_membership_and_the_nda(prod):
    c = prod["client"]
    assert c.get(f"{BASE}/part_files?list=tooling").status_code == 401
    assert c.get(f"{BASE}/part_files?list=tooling", headers=prod["non_member"]).status_code == 404
    assert c.get(f"{BASE}/part_files?list=tooling", headers=prod["oem_no_nda"]).status_code == 403
    assert c.get(f"{BASE}/part_files?list=sideways", headers=prod["admin"]).status_code == 422


# ── the 3-D view ────────────────────────────────────────────────────────────────────────────────────────────────────

def test_step_view_converts_once_caches_and_is_traced(prod, stub_converter, tmp_path, monkeypatch):
    log = _download_log(tmp_path, monkeypatch)
    c, fid = prod["client"], prod["docs"]["step"]["file_id"]
    r = c.get(f"{BASE}/files/{fid}/model.glb", headers=prod["engineer"])
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "model/gltf-binary" and r.headers["cache-control"] == "no-store"
    assert r.content[:4] == b"glTF" and struct.unpack_from("<I", r.content, 8)[0] == len(r.content)
    cached = list(prod["root"].rglob("viewcache/*.glb"))
    assert len(cached) == 1 and cached[0].read_bytes() == r.content
    assert stub_converter == [fid]
    r2 = c.get(f"{BASE}/files/{fid}/model.glb", headers=prod["oem_nda"])
    assert r2.status_code == 200 and r2.content == r.content and stub_converter == [fid]      # the cache served it
    rows = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert [x["kind"] for x in rows] == ["production_step_view", "production_step_view"]
    assert rows[0]["file"] == "press_base.step" and rows[0]["user"] == ENGINEER.lower() and rows[0]["sha256"]
    assert rows[1]["user"] == OEM_NDA.lower()


def test_cache_files_do_not_count_as_uploads_or_downloads(prod, stub_converter):
    c, fid = prod["client"], prod["docs"]["step"]["file_id"]
    assert c.get(f"{BASE}/files/{fid}/model.glb", headers=prod["engineer"]).status_code == 200
    cache_name = next(prod["root"].rglob("viewcache/*.glb")).name
    # the cache name is not a file id: it can never be fetched as a production file
    assert c.get(f"{BASE}/files/{cache_name}", headers=prod["admin"]).status_code == 404


def test_step_view_follows_the_download_rules(prod, stub_converter):
    c, fid = prod["client"], prod["docs"]["step"]["file_id"]
    url = f"{BASE}/files/{fid}/model.glb"
    assert c.get(url).status_code == 401                                          # never anonymous
    assert c.get(url, headers=prod["non_member"]).status_code == 404
    assert c.get(url, headers=prod["oem_no_nda"]).status_code == 403              # the factory needs its NDA
    assert c.patch(f"{BASE}/documents/{prod['docs']['step']['id']}", headers=prod["engineer"], json={"internal": True}).status_code == 200
    assert c.get(url, headers=prod["oem_nda"]).status_code == 404                 # internal: hidden from the factory
    assert c.get(url, headers=prod["admin"]).status_code == 200
    assert c.get(f"{BASE}/files/not-a-real-file-id/model.glb", headers=prod["admin"]).status_code == 404
    assert c.get(f"{BASE}/files/....%2F....%2Fproduction.json/model.glb", headers=prod["admin"]).status_code in (400, 404)


def test_a_file_that_is_not_a_step_is_refused(prod, stub_converter):
    r = prod["client"].get(f"{BASE}/files/{prod['docs']['pdf']['file_id']}/model.glb", headers=prod["admin"])
    assert r.status_code == 422 and stub_converter == []


def test_a_failed_conversion_is_reported_and_not_cached(prod, monkeypatch):
    from motor_ai_sim.production import step_glb

    def broken(step_path, glb_path, timeout):
        raise step_glb.StepConversionError("This STEP file could not be turned into a 3-D view.")

    monkeypatch.setattr(step_glb, "_run_child", broken)
    r = prod["client"].get(f"{BASE}/files/{prod['docs']['step']['file_id']}/model.glb", headers=prod["admin"])
    assert r.status_code == 422 and "3-D view" in r.json()["detail"]
    assert not list(prod["root"].rglob("viewcache/*.glb"))


def test_an_oversized_step_is_refused_before_conversion(prod, stub_converter, monkeypatch):
    from motor_ai_sim.production import step_glb
    monkeypatch.setattr(step_glb, "MAX_STEP_BYTES", 10)
    r = prod["client"].get(f"{BASE}/files/{prod['docs']['step']['file_id']}/model.glb", headers=prod["admin"])
    assert r.status_code == 413 and stub_converter == []


def test_an_unwritable_cache_still_serves_the_view(prod, stub_converter, monkeypatch):
    from pathlib import Path
    real = Path.write_bytes

    def no_cache(self, data):
        if "viewcache" in str(self):
            raise OSError("read-only")
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", no_cache)
    r = prod["client"].get(f"{BASE}/files/{prod['docs']['step']['file_id']}/model.glb", headers=prod["admin"])
    assert r.status_code == 200 and r.content[:4] == b"glTF"


# ── the writer and the real tessellation ──────────────────────────────────────────────────────────────────────────

def _parse_glb(data: bytes):
    magic, version, total = struct.unpack_from("<III", data, 0)
    assert (magic, version, total) == (0x46546C67, 2, len(data))
    jl, jt = struct.unpack_from("<II", data, 12)
    assert jt == 0x4E4F534A and jl % 4 == 0
    return json.loads(data[20:20 + jl]), data[28 + jl:]


def test_glb_layout_is_what_the_viewer_reads():
    import numpy as np
    from motor_ai_sim.production import step_glb
    pos = np.array([[0, 0, 0], [4, 0, 0], [0, 3, 0], [0, 0, 5]], dtype="float32")
    nrm, idx = np.tile([0.0, 0.0, 1.0], (4, 1)), np.array([0, 1, 2, 0, 2, 3])
    doc, blob = _parse_glb(step_glb.build_glb([("Part_1", pos, nrm, idx), ("Part_2", pos + 10, nrm, idx)]))
    model = doc["nodes"][doc["scenes"][0]["nodes"][0]]
    assert model["name"] == "Model" and len(model["children"]) == 2
    first = doc["nodes"][model["children"][0]]
    assert first["name"] == "Part_1" and first["extras"] == {"family": "Part_1", "explode": 0}
    acc = doc["accessors"][doc["meshes"][first["mesh"]]["primitives"][0]["attributes"]["POSITION"]]
    assert acc["count"] == 4 and acc["min"] == [0, 0, 0] and acc["max"] == [4, 3, 5]
    assert doc["buffers"][0]["byteLength"] == len(blob)
    assert all(v["byteOffset"] % 4 == 0 for v in doc["bufferViews"])


def test_face_normals_are_smooth_inside_a_face_only():
    import numpy as np
    from motor_ai_sim.production import step_glb
    flat = step_glb.smooth_normals(np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype="float32"), np.array([[0, 1, 2]]))
    assert np.allclose(flat, [[0, 0, 1]] * 3)
    with pytest.raises(step_glb.StepConversionError):
        step_glb.merge_faces([])


def test_step_header_check():
    from motor_ai_sim.production import step_glb
    assert step_glb.looks_like_step(b"  \niso-10303-21;\nHEADER;")
    assert not step_glb.looks_like_step(b"%PDF-1.4")


def test_real_step_becomes_a_box_sized_glb(tmp_path):
    cq = pytest.importorskip("cadquery", exc_type=ImportError)
    import numpy as np
    from motor_ai_sim.production import step_glb
    step = tmp_path / "box.step"
    cq.exporters.export(cq.Workplane("XY").box(10, 20, 30).faces(">Z").hole(4), str(step))
    out = tmp_path / "box.glb"
    info = step_glb.convert_in_process(str(step), str(out))
    assert info["solids"] == 1 and sorted(info["bbox_mm"]) == [10.0, 20.0, 30.0]
    doc, blob = _parse_glb(out.read_bytes())
    prim = doc["meshes"][0]["primitives"][0]
    pa, ia = doc["accessors"][prim["attributes"]["POSITION"]], doc["accessors"][prim["indices"]]
    pos = np.frombuffer(blob, "<f4", pa["count"] * 3, doc["bufferViews"][pa["bufferView"]]["byteOffset"]).reshape(-1, 3)
    tri = np.frombuffer(blob, "<u4", ia["count"], doc["bufferViews"][ia["bufferView"]]["byteOffset"]).reshape(-1, 3)
    p = pos[tri].astype("f8")
    volume = np.einsum("ij,ij->i", p[:, 0], np.cross(p[:, 1], p[:, 2])).sum() / 6.0
    assert volume > 0                                                             # outward winding
    assert volume == pytest.approx(10 * 20 * 30 - np.pi * 2 ** 2 * 30, rel=0.01)  # box minus the blind-through hole
