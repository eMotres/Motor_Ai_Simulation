"""GLB writer of the 3D model menu (cad/fc_scripts/glb_writer.py) and the server outputs it feeds: pure Python, no FreeCAD.

The FreeCAD side (export_models.py: tessellation, mesh-vs-solid volume check) is verified on the server build,
see docs/CAD_GENERATOR.md; here: the file format, the normals, the node tree with its extras, the manifest listing.
"""
import json
import math
import os
import struct
import sys

import pytest

from motor_ai_sim.cad import manifest, store

sys.path.insert(0, os.path.join(store.CAD_DIR, "fc_scripts"))
import glb_writer as G  # noqa: E402


def cube(c=(0.0, 0.0, 0.0), s=1.0):
    """Closed unit-ish cube as ONE patch (shared corners), outward winding."""
    x, y, z = c
    v = [(x + dx * s, y + dy * s, z + dz * s) for dz in (-1, 1) for dy in (-1, 1) for dx in (-1, 1)]
    # corner index = dx + 2 dy + 4 dz (dx,dy,dz in {0,1})
    t = [(0, 2, 1), (1, 2, 3), (4, 5, 6), (5, 7, 6), (0, 1, 4), (1, 5, 4), (2, 6, 3), (3, 6, 7), (0, 4, 2), (2, 4, 6), (1, 3, 5), (3, 7, 5)]
    return G.Mesh(v, t)


def square_face(z=0.0):
    return G.Mesh([(0, 0, z), (1, 0, z), (1, 1, z), (0, 1, z)], [(0, 1, 2), (0, 2, 3)])


def read_accessor(doc, blob, i):
    a = doc["accessors"][i]
    bv = doc["bufferViews"][a["bufferView"]]
    n = {"VEC3": 3, "SCALAR": 1}[a["type"]]
    fmt = {5126: "f", 5125: "I"}[a["componentType"]]
    return list(struct.unpack_from("<%d%s" % (a["count"] * n, fmt), blob, bv["byteOffset"]))


def test_cube_volume_and_winding():
    m = cube(s=1.0)                                  # side 2
    assert G.signed_volume(m.vertices, m.triangles) == pytest.approx(8.0)
    flipped = [(a, c, b) for a, b, c in m.triangles]
    assert G.signed_volume(m.vertices, flipped) == pytest.approx(-8.0)


def test_planar_face_normals_are_exact_and_unit():
    n = G.smooth_normals(square_face(2.0).vertices, square_face(2.0).triangles)
    for i in range(4):
        assert (n[3 * i], n[3 * i + 1], n[3 * i + 2]) == pytest.approx((0, 0, 1))


def test_glb_roundtrip_nodes_extras_materials():
    g1 = G.Group("Core", (0.6, 0.62, 0.65), extras={"family": "Core", "explode": 0.0})
    g1.add("Core", [cube(s=1.0)])
    g2 = G.Group("Coils", (0.8, 0.47, 0.22), extras={"family": "Coils", "explode": -0.35})
    g2.add("Coil_01", [square_face(0)])
    g2.add("Coil_02", [square_face(5)])
    g3 = G.Group("Empty", (1, 1, 1))
    g3.add("Nothing", [G.Mesh([], [])])
    data, st = G.build_glb([g1, g2, g3], {"motor_id": "X_L12"})
    assert len(data) % 4 == 0
    doc, blob = G.parse_glb(data)
    assert doc["asset"]["version"] == "2.0" and doc["asset"]["extras"]["motor_id"] == "X_L12"
    assert doc["asset"]["extras"]["units"] == "mm"
    groups = {n["name"]: n for n in doc["nodes"] if "children" in n}
    meshes = {n["name"]: n for n in doc["nodes"] if "mesh" in n}
    names = groups
    assert set(groups) == {"Core", "Coils", "Model"} and set(meshes) == {"Core", "Coil_01", "Coil_02"}   # the empty group is dropped
    assert [doc["nodes"][i]["name"] for i in names["Model"]["children"]] == ["Core", "Coils"]
    assert names["Coils"]["extras"]["explode"] == -0.35 and names["Coils"]["extras"]["part"] == "Coils"
    assert [doc["nodes"][i]["name"] for i in names["Coils"]["children"]] == ["Coil_01", "Coil_02"]
    assert st["triangles"] == 12 + 2 + 2 and st["groups"] == {"Core": 12, "Coils": 4}
    assert [m["name"] for m in doc["materials"]] == ["Core", "Coils", "Empty"]
    assert doc["materials"][1]["pbrMetallicRoughness"]["baseColorFactor"][:3] == [0.8, 0.47, 0.22]
    # accessor contents: the cube's positions and indices come back exactly
    prim = doc["meshes"][meshes["Core"]["mesh"]]["primitives"][0]
    pos = read_accessor(doc, blob, prim["attributes"]["POSITION"])
    idx = read_accessor(doc, blob, prim["indices"])
    assert len(pos) == 3 * 8 and len(idx) == 3 * 12 and max(idx) == 7
    acc = doc["accessors"][prim["attributes"]["POSITION"]]
    assert acc["min"] == [-1, -1, -1] and acc["max"] == [1, 1, 1]
    nrm = read_accessor(doc, blob, prim["attributes"]["NORMAL"])
    assert all(abs(math.sqrt(nrm[i] ** 2 + nrm[i + 1] ** 2 + nrm[i + 2] ** 2) - 1) < 1e-5 for i in range(0, len(nrm), 3))
    assert doc["buffers"][0]["byteLength"] == len(blob) or len(blob) - doc["buffers"][0]["byteLength"] < 4


def test_patches_stay_separate_hard_edges_between_faces():
    # two coplanar-looking patches with different orientation must not share a smoothed normal across the patch border
    a = G.Mesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)])
    b = G.Mesh([(0, 0, 0), (0, 0, 1), (0, 1, 0)], [(0, 2, 1)])        # perpendicular plane, same corner positions
    g = G.Group("P", (1, 1, 1))
    g.add("P", [a, b])
    doc, blob = G.parse_glb(G.build_glb([g])[0])
    prim = doc["meshes"][0]["primitives"][0]
    nrm = read_accessor(doc, blob, prim["attributes"]["NORMAL"])
    assert nrm[0:3] == pytest.approx([0, 0, 1]) and nrm[9:12] == pytest.approx([1, 0, 0])


def test_parse_rejects_garbage():
    with pytest.raises(ValueError):
        G.parse_glb(b"\x00" * 40)


def test_manifest_lists_the_viewer_files_with_sha256(tmp_path):
    for n, b in (("m_full.glb", b"1"), ("m_active.step", b"22"), ("m_models3d.json", b"333")):
        (tmp_path / n).write_bytes(b)
    rows = manifest.file_rows(str(tmp_path), ["m_full.glb", "m_active.step", "m_models3d.json", "missing.glb"])
    assert set(rows) == {"m_full.glb", "m_active.step", "m_models3d.json"}
    assert rows["m_models3d.json"]["bytes"] == 3 and len(rows["m_active.step"]["sha256"]) == 64


def test_build_cli_has_the_models_switch():
    from motor_ai_sim.cad import build
    src = open(build.__file__, encoding="utf-8").read()
    assert "--no-models" in src and "export_models.py" in src and "envelope" not in src
    assert os.path.isfile(os.path.join(store.CAD_DIR, "fc_scripts", "export_models.py"))
