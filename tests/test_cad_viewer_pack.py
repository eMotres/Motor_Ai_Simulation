# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Compressed VIEWER copies of the 3-D models (cad/viewer_pack.py, owner 2026-10-20: the models took 8-16 s to load).

* the structural check refuses a packed file the viewer would see differently: another node tree (names, extras, parents, child
  order), other triangle counts, added node transforms (the viewer reads positions in the CAD frame), quantised positions, an extension
  three.js does not load, moved bounding boxes, a file that is not smaller;
* a failed / missing tool never leaves a copy behind, a stale copy of a removed canonical GLB is deleted;
* ``repack`` writes the copies' rows and the ``viewer_pack`` block into the manifest; the ``pack`` CLI does it for a whole root;
* with the real gltfpack (``$MOTRES_GLTFPACK`` or PATH; skipped otherwise) a synthetic model packs smaller and passes the check.
Synthetic models only (cad/fc_scripts/glb_writer.py); no FreeCAD, no motor data."""
from __future__ import annotations

import json
import math
import os
import struct
import sys
import textwrap

import pytest

from motor_ai_sim.cad import build, store
from motor_ai_sim.cad import viewer_pack as V

sys.path.insert(0, os.path.join(store.CAD_DIR, "fc_scripts"))
import glb_writer as G  # noqa: E402


def tube(r0, r1, z0, z1, n=48):
    """A closed tube (annulus extruded along z), one patch per face, `n` facets: a few thousand triangles."""
    patches = []
    for r, out in ((r1, True), (r0, False)):                                  # outer / inner wall
        v = [(r * math.cos(2 * math.pi * k / n), r * math.sin(2 * math.pi * k / n), z) for z in (z0, z1) for k in range(n)]
        t = []
        for k in range(n):
            a, b, c, d = k, (k + 1) % n, n + k, n + (k + 1) % n
            t += [(a, b, d), (a, d, c)] if out else [(a, d, b), (a, c, d)]
        patches.append(G.Mesh(v, t))
    for z, up in ((z0, False), (z1, True)):                                    # end rings
        v = [(r * math.cos(2 * math.pi * k / n), r * math.sin(2 * math.pi * k / n), z) for r in (r0, r1) for k in range(n)]
        t = []
        for k in range(n):
            a, b, c, d = k, (k + 1) % n, n + k, n + (k + 1) % n
            t += [(a, c, d), (a, d, b)] if up else [(a, d, c), (a, b, d)]
        patches.append(G.Mesh(v, t))
    return patches


def synthetic_glb(n=48):
    """A motor-shaped synthetic model: Core (one node), Coils (two nodes, one with node extras), the viewer's group extras."""
    core = G.Group("Core", (0.6, 0.62, 0.65), extras={"family": "Core", "explode": 0.0})
    core.add("Core", tube(10.0, 20.0, -5.0, 5.0, n))
    coils = G.Group("Coils", (0.8, 0.47, 0.22), extras={"family": "Coils", "explode": -0.35})
    coils.add("Coil_01", tube(12.0, 14.0, -6.0, 6.0, n))
    coils.add("Coil_02", tube(15.0, 17.0, -6.0, 6.0, n), extras={"thread": {"pitch_mm": 0.5}})
    return G.build_glb([core, coils], asset_extras={"motor_id": "SYNTH_L1"})[0]


def with_json(data, edit, bin_bytes=None):
    """The same GLB with its JSON chunk edited by ``edit(doc)`` (and optionally another BIN chunk)."""
    doc, blob = G.parse_glb(data)
    edit(doc)
    blob = blob if bin_bytes is None else bin_bytes
    js = json.dumps(doc, separators=(",", ":")).encode()
    js += b" " * ((-len(js)) % 4)
    blob += b"\0" * ((-len(blob)) % 4)
    total = 28 + len(js) + len(blob)
    return struct.pack("<III", G.GLB_MAGIC, 2, total) + struct.pack("<II", len(js), G.CHUNK_JSON) + js \
        + struct.pack("<II", len(blob), G.CHUNK_BIN) + blob


def packed_like(src, edit=lambda d: None):
    """What gltfpack -vpf makes of ``src`` as far as the check can see: the same JSON + the meshopt extensions, a smaller BIN."""
    def e(d):
        d["extensionsUsed"] = d["extensionsRequired"] = ["EXT_meshopt_compression", "KHR_mesh_quantization"]
        edit(d)
    return with_json(src, e, bin_bytes=b"\1" * 64)


def node(doc, name):
    return next(n for n in doc["nodes"] if n["name"] == name)


# ---------------------------------------------------------------------------------------------------------------- the check

def test_the_check_accepts_the_same_model_and_refuses_one_that_is_not_smaller():
    src = synthetic_glb()
    assert V.check(src, packed_like(src)) == []
    assert any("not smaller" in p for p in V.check(src, src))


@pytest.mark.parametrize("edit, says", [
    (lambda d: node(d, "Coil_01").__setitem__("name", "Coil_1"), "node tree"),
    (lambda d: node(d, "Coil_02").__setitem__("extras", {}), "node tree"),
    (lambda d: node(d, "Coils").__setitem__("children", node(d, "Coils")["children"][::-1]), "node tree"),            # child order
    (lambda d: node(d, "Core").__setitem__("translation", [0, 0, 1]), "transforms"),
    (lambda d: d["accessors"][d["meshes"][0]["primitives"][0]["indices"]].__setitem__("count", 3), "triangle counts"),
    (lambda d: d["accessors"][d["meshes"][0]["primitives"][0]["attributes"]["POSITION"]].update(componentType=5123), "FLOAT"),
    (lambda d: d["accessors"][d["meshes"][0]["primitives"][0]["attributes"]["POSITION"]].update(min=[-30, -30, -30]), "bounding box"),
    (lambda d: d.__setitem__("extensionsRequired", ["KHR_meshopt_compression"]), "extensions"),
    (lambda d: d["materials"][0]["pbrMetallicRoughness"].__setitem__("baseColorFactor", [1, 0, 0, 1]), "materials"),
    (lambda d: d["asset"].__setitem__("extras", {}), "asset extras"),
])
def test_the_check_refuses_what_the_viewer_would_see_differently(edit, says):
    src = synthetic_glb()
    problems = V.check(src, packed_like(src, edit))
    assert problems and any(says in p for p in problems), problems


def test_the_check_refuses_an_unreadable_file():
    assert V.check(synthetic_glb(), b"not a glb at all") == ["unreadable: not a GLB 2.0 file"]


# ---------------------------------------------------------------------------------------------------------------- pack / repack

FAKE = textwrap.dedent('''
    import json, os, struct, sys
    sys.path.insert(0, sys.argv[1])
    import glb_writer as G
    a = sys.argv[2:]
    if a == ["-v"]:
        print("gltfpack 9.9 (fake)"); sys.exit(0)
    mode = os.environ.get("FAKE_MODE", "ok")
    if mode == "fail":
        print("boom", file=sys.stderr); sys.exit(1)
    src, dst = a[a.index("-i") + 1], a[a.index("-o") + 1]
    doc, blob = G.parse_glb(open(src, "rb").read())
    doc["extensionsUsed"] = doc["extensionsRequired"] = ["EXT_meshopt_compression"]
    if mode == "transform":
        doc["nodes"][0]["scale"] = [0.01, 0.01, 0.01]
    js = json.dumps(doc).encode(); js += b" " * ((-len(js)) % 4)
    blob = b"\\1" * 64
    open(dst, "wb").write(struct.pack("<III", G.GLB_MAGIC, 2, 28 + len(js) + len(blob)) + struct.pack("<II", len(js), G.CHUNK_JSON) + js
                          + struct.pack("<II", len(blob), G.CHUNK_BIN) + blob)
''')


@pytest.fixture()
def fake_tool(tmp_path):
    p = tmp_path / "fake_gltfpack.py"
    p.write_text(FAKE, encoding="utf-8")
    return [sys.executable, str(p), os.path.join(store.CAD_DIR, "fc_scripts")]


def _built(root, mid="SYNTH_L1", simplified=True):
    d = root / "SYNTH" / "L1"
    d.mkdir(parents=True)
    names = [mid + "_full.glb"] + ([mid + "_simplified.glb"] if simplified else [])
    for n in names:
        (d / n).write_bytes(synthetic_glb())
    from motor_ai_sim.cad import manifest as mf
    (d / "manifest.json").write_text(json.dumps({"motor_id": mid, "files": mf.file_rows(str(d), names)}), encoding="utf-8")
    return d


def test_pack_writes_a_checked_copy(tmp_path, fake_tool):
    src = tmp_path / "m_full.glb"
    src.write_bytes(synthetic_glb())
    r = V.pack(str(src), str(tmp_path / "m_full.meshopt.glb"), fake_tool)
    assert r["ok"] and r["bytes_out"] < r["bytes_in"] and r["ratio"] > 1
    assert V.json_chunk((tmp_path / "m_full.meshopt.glb").read_bytes())["extensionsRequired"] == ["EXT_meshopt_compression"]
    assert sorted(os.listdir(tmp_path)) == ["fake_gltfpack.py", "m_full.glb", "m_full.meshopt.glb"]       # no temporary file left


@pytest.mark.parametrize("mode, says", [("fail", "exit 1"), ("transform", "transforms")])
def test_a_failed_pack_leaves_no_copy(tmp_path, fake_tool, monkeypatch, mode, says):
    monkeypatch.setenv("FAKE_MODE", mode)
    src = tmp_path / "m_full.glb"
    src.write_bytes(synthetic_glb())
    r = V.pack(str(src), str(tmp_path / "m_full.meshopt.glb"), fake_tool)
    assert not r["ok"] and says in r["message"]
    assert sorted(os.listdir(tmp_path)) == ["fake_gltfpack.py", "m_full.glb"]


def test_no_tool_is_reported_not_fatal(tmp_path, monkeypatch):
    monkeypatch.delenv("MOTRES_GLTFPACK", raising=False)
    monkeypatch.setattr(V.shutil, "which", lambda name: None)
    d = _built(tmp_path)
    info = V.pack_dir(str(d), "SYNTH_L1")
    assert info["ok"] is False and info["files"] == {} and "gltfpack not found" in info["message"]
    assert not any(n.endswith(".meshopt.glb") for n in os.listdir(d))


def test_the_tool_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("MOTRES_GLTFPACK", 'node "C:/tools x/gltfpack/cli.js"' if os.name == "nt" else "node '/tools x/gltfpack/cli.js'")
    assert V.find_tool()[0] == "node" and V.find_tool()[1].endswith("tools x/gltfpack/cli.js")


def test_repack_updates_the_manifest_and_drops_stale_copies(tmp_path, fake_tool, monkeypatch):
    d = _built(tmp_path, simplified=False)
    (d / "SYNTH_L1_simplified.meshopt.glb").write_bytes(b"stale copy of a model that is gone")
    info = V.repack(str(d), fake_tool)
    assert info["ok"] and list(info["files"]) == ["SYNTH_L1_full.meshopt.glb"] and info["tool"] == "gltfpack 9.9 (fake)"
    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    row = m["files"]["SYNTH_L1_full.meshopt.glb"]
    assert row["sha256"] == store.sha256_file(str(d / "SYNTH_L1_full.meshopt.glb")) and m["viewer_pack"]["args"] == " ".join(V.TOOL_ARGS)
    assert not (d / "SYNTH_L1_simplified.meshopt.glb").exists()
    monkeypatch.setenv("FAKE_MODE", "fail")                                   # a later failure removes the copy AND its manifest row
    info = V.repack(str(d), fake_tool)
    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    assert not info["ok"] and "SYNTH_L1_full.meshopt.glb" not in m["files"] and not (d / "SYNTH_L1_full.meshopt.glb").exists()
    assert "SYNTH_L1_full.glb" in m["files"]


def test_the_pack_cli_packs_every_built_model(tmp_path, fake_tool, monkeypatch, capsys):
    _built(tmp_path)
    monkeypatch.setenv("MOTRES_GLTFPACK", " ".join('"%s"' % t if os.name == "nt" else t for t in fake_tool))
    assert build.main(["pack", "--out-root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "SYNTH/L1:" in out and "full.meshopt.glb" in out and "simplified.meshopt.glb" in out
    (tmp_path / "empty").mkdir()
    assert build.main(["pack", "--out-root", str(tmp_path / "empty")]) == 3                        # nothing built: says so, non-zero


# ---------------------------------------------------------------------------------------------------------------- the real tool

@pytest.mark.skipif(V.find_tool() is None, reason="gltfpack not installed ($MOTRES_GLTFPACK or PATH)")
def test_real_gltfpack_packs_smaller_with_the_same_tree(tmp_path):
    src = tmp_path / "m_full.glb"
    src.write_bytes(synthetic_glb(n=256))
    r = V.pack(str(src), str(tmp_path / "m_full.meshopt.glb"))
    assert r["ok"], r["message"]
    assert r["ratio"] >= 3, r
    doc = V.json_chunk((tmp_path / "m_full.meshopt.glb").read_bytes())
    assert "EXT_meshopt_compression" in doc["extensionsRequired"]
    assert [n["name"] for n in doc["nodes"]] and not any(k in n for n in doc["nodes"] for k in ("translation", "scale", "matrix"))


def test_the_image_self_test(fake_tool, monkeypatch, capsys):
    ok, msg = V.selftest(fake_tool)
    assert ok and "gltfpack 9.9 (fake)" in msg
    monkeypatch.setenv("MOTRES_GLTFPACK", "no-such-gltfpack-binary")
    assert build.main(["pack-selftest"]) == 3 and "self-test failed" in capsys.readouterr().out


def test_every_build_writes_the_viewer_copies_into_its_manifest():
    import inspect
    src = inspect.getsource(build.build)
    assert "viewer_pack.pack_dir(work, mid)" in src and "names += viewer_pack.produced(vpack)" in src and 'm["viewer_pack"] = vpack' in src
    assert "--no-viewer-pack" in inspect.getsource(build.main)
