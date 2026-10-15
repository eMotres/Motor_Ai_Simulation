"""Thread data of the 3D-viewer model (cad/fc_scripts/threads.py, glb_writer node extras, geometry_hash) and the FreeCAD export.

The CAD model keeps threads smooth (owner 2026-10-15); the GLB marks the threaded cylinders with node `extras.thread` and the web
viewer draws the relief (web/src/lib/threadTexture.ts).  Pure Python tests run everywhere; the export test needs FreeCAD and
runs the reference builds of CIANO14 40 and CIANO28 85 (set MOTRES_CAD_E2E=1; about 3 minutes per template).
"""
import json
import math
import os
import subprocess
import sys

import pytest

from motor_ai_sim.cad import store

FC_DIR = os.path.join(store.CAD_DIR, "fc_scripts")
sys.path.insert(0, FC_DIR)
import glb_writer as G  # noqa: E402
import threads as TH  # noqa: E402


# ------------------------------------------------------------------------------------------------ the ISO table

def test_coarse_pitches_follow_iso_261():
    assert TH.coarse_pitch(2.0) == 0.4 and TH.coarse_pitch(2.5) == 0.45 and TH.coarse_pitch(3.0) == 0.5
    assert TH.coarse_pitch(5.0) == 0.8 and TH.coarse_pitch(8.0) == 1.25 and TH.coarse_pitch(2.2) is None


def test_screw_shank_and_tap_hole_resolve_to_the_nominal_size():
    assert TH.thread_for("external", 1.0) == (2.0, 0.4)              # CIANO14 40 screw M2 (91292A032)
    assert TH.thread_for("external", 1.215) == (2.5, 0.45)           # CIANO28 85: shank modelled at 2.43 -> M2.5
    assert TH.thread_for("external", 2.5) == (5.0, 0.8)              # shaft thread M5
    assert TH.thread_for("internal", 1.623 / 2) == (2.0, 0.4)        # tapped hole, minor diameter 1.623 (M2x0.4 6H)
    assert TH.thread_for("internal", 2.459 / 2) == (3.0, 0.5)        # M3x0.5 tap
    assert TH.thread_for("internal", 2.0 / 2) is None or TH.thread_for("internal", 1.0)[0] != 2.0   # a 2.0 mm bore is no M2 tap
    assert TH.thread_for("external", 1.7) is None                    # between sizes: no thread record
    assert TH.minor_diameter(2.0) == pytest.approx(1.567, abs=1e-3)


def test_extras_record():
    ex = TH.thread_extras("external", 1.0, 17.0, (0, 0, -1), (14.85, 0.0, 3.0))
    t = ex["thread"]
    assert t["kind"] == "external" and t["d_mm"] == 2.0 and t["pitch_mm"] == 0.4 and t["length_mm"] == 17.0
    assert t["standard"] == "ISO 261" and t["designation"] == "M2x0.4" and t["hand"] == "right"
    assert t["axis"] == [0.0, 0.0, 1.0]                              # canonical sign ...
    assert t["origin"] == [14.85, 0.0, -14.0]                        # ... and the origin moved to the other end: z from -14 to 3
    assert TH.thread_extras("external", 1.7, 5, (0, 0, 1), (0, 0, 0)) is None
    json.dumps(ex)


# ------------------------------------------------------------------------------------------------ GLB nodes

def cyl(r=1.0, z0=0.0, z1=4.0, seg=16):
    v, t = [], []
    for k in range(seg):
        a = 2 * math.pi * k / seg
        v += [(r * math.cos(a), r * math.sin(a), z0), (r * math.cos(a), r * math.sin(a), z1)]
    for k in range(seg):
        i, j = 2 * k, 2 * ((k + 1) % seg)
        t += [(i, j, i + 1), (j, j + 1, i + 1)]
    return G.Mesh(v, t)


def cap(z, r=1.0, seg=16):
    v = [(0, 0, z)] + [(r * math.cos(2 * math.pi * k / seg), r * math.sin(2 * math.pi * k / seg), z) for k in range(seg)]
    return G.Mesh(v, [(0, 1 + k, 1 + (k + 1) % seg) for k in range(seg)])


def _cross(m, t):
    a, b, c = (m.vertices[i] for i in t)
    u = [b[k] - a[k] for k in range(3)]
    v = [c[k] - a[k] for k in range(3)]
    return [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]


def test_split_by_slab_keeps_area_and_winding_and_shares_cut_vertices():
    c = cyl(r=1.0, z0=0.0, z1=10.0, seg=16)
    area = lambda m: sum(0.5 * math.sqrt(sum(x * x for x in _cross(m, t))) for t in m.triangles)
    inside, outside = G.split_by_slab(c, (0, 0, 0), (0, 0, 1), 2.0, 7.0)
    assert len(outside) == 2
    parts = [inside] + outside
    assert sum(area(p) for p in parts) == pytest.approx(area(c), rel=1e-9)               # no area lost or added
    zs = [v[2] for v in inside.vertices]
    assert min(zs) == pytest.approx(2.0) and max(zs) == pytest.approx(7.0)
    assert max(v[2] for v in outside[0].vertices) == pytest.approx(2.0) and min(v[2] for v in outside[1].vertices) == pytest.approx(7.0)
    for p in parts:                                                                      # outward winding kept
        for t in p.triangles:
            n = _cross(p, t)
            cx = sum(p.vertices[i][0] for i in t) / 3
            cy = sum(p.vertices[i][1] for i in t) / 3
            assert n[0] * cx + n[1] * cy > 0
    assert len(inside.vertices) == 4 * 16              # 16 vertical + 16 diagonal edges cut per plane, each cut once (shared): smooth normals
    whole, none = G.split_by_slab(c, (0, 0, 0), (0, 0, 1), -1.0, 11.0)
    assert none == [] and len(whole.triangles) == len(c.triangles) and len(whole.vertices) == len(c.vertices)
    empty, rest = G.split_by_slab(c, (0, 0, 0), (0, 0, 1), 20.0, 30.0)
    assert empty is None and len(rest) == 1 and len(rest[0].triangles) == len(c.triangles)


def test_socket_head_thread_length_follows_iso_4762():
    assert [TH.socket_head_thread_length(d, 40, TH.coarse_pitch(d)) for d in (2.0, 2.5, 3.0, 4.0, 5.0)] == [16.0, 17.0, 18.0, 20.0, 22.0]
    assert TH.socket_head_thread_length(2.0, 25, 0.4) == 16.0          # CIANO14 40 L20: M2x25, 9 mm plain under the head
    assert TH.socket_head_thread_length(2.0, 17, 0.4) == 16.0          # L12: M2x17, 1 mm plain (more than 2 pitches)
    assert TH.socket_head_thread_length(2.0, 12, 0.4) == 12            # short screw: fully threaded
    assert TH.socket_head_thread_length(2.0, 16.5, 0.4) == 16.5        # plain part under 2 pitches: fully threaded


def test_screw_band_runs_from_the_tip_never_under_the_head():
    # shank face z -11.5..13.1, screw -11.9 (tip chamfer 0.4) .. 15.1 (head 2.0): the thread is 16 from the tip, clipped to the face
    t0, t1 = TH.socket_head_band((-11.5, 13.1), (-11.9, 15.1), 16.0)
    assert (round(t0, 6), round(t1, 6)) == (-11.5, 4.1)
    t0, t1 = TH.socket_head_band((-13.1, 11.5), (-15.1, 11.9), 16.0)      # the same screw with the axis the other way round
    assert (round(t0, 6), round(t1, 6)) == (-4.1, 11.5)
    assert TH.socket_head_band((-11.5, 13.1), (-11.9, 15.1), 40.0) == (-11.5, 13.1)     # fully threaded: the whole face
    assert TH.socket_head_band((0, 1), (-0.4, 3.0), 0.0) is None


def test_extras_carry_the_threaded_z_range_and_start_offset():
    ex = TH.thread_extras("external", 1.0, 15.6, (0, 0, 1), (7.4, 12.9, -11.5))["thread"]
    assert ex["z_range_mm"] == [-11.5, 4.1] and ex["length_mm"] == 15.6
    ex = TH.thread_extras("external", 1.0, 4.0, (0, 0, 1), (0, 0, 10.0), start=2.0)["thread"]
    assert ex["origin"][2] == 12.0 and ex["z_range_mm"] == [12.0, 16.0]
    ex = TH.thread_extras("external", 1.0, 4.0, (0, 0, -1), (0, 0, 10.0), start=2.0)["thread"]   # axis down: the band is z 4 .. 8
    assert ex["axis"] == [0.0, 0.0, 1.0] and ex["z_range_mm"] == [4.0, 8.0]
    assert "z_range_mm" not in TH.thread_extras("external", 1.0, 4.0, (1, 0, 0), (0, 0, 0))["thread"]


def test_thread_node_carries_extras_and_the_geometry_hash_does_not_change():
    ex = TH.thread_extras("external", 1.0, 4.0, (0, 0, 1), (0, 0, 0))
    plain = G.Group("Screws", (0.85, 0.86, 0.87), extras={"family": "Screws", "explode": 2.6})
    plain.add("Screw_1", [cap(0.0), cyl(), cap(4.0)])
    split = G.Group("Screws", (0.85, 0.86, 0.87), extras={"family": "Screws", "explode": 2.6})
    split.add("Screw_1", [cap(0.0), cap(4.0)])
    split.add("Screw_1_thread1", [cyl()], ex)
    assert G.geometry_hash([plain]) == G.geometry_hash([split])               # same triangles, only the node split differs
    moved = G.Group("Screws", (0.85, 0.86, 0.87))
    moved.add("Screw_1", [cap(0.0), cyl(r=1.001), cap(4.0)])
    assert G.geometry_hash([plain]) != G.geometry_hash([moved])
    renamed = G.Group("Screw2", (0.85, 0.86, 0.87))
    renamed.add("Screw_1", [cap(0.0), cyl(), cap(4.0)])
    assert G.geometry_hash([plain]) != G.geometry_hash([renamed])             # the group is part of the hash

    data, st = G.build_glb([split], {"motor_id": "X"})
    doc, _ = G.parse_glb(data)
    nodes = {n["name"]: n for n in doc["nodes"]}
    assert "extras" not in nodes["Screw_1"]                                   # an ordinary node is untouched
    t = nodes["Screw_1_thread1"]["extras"]["thread"]
    assert t["d_mm"] == 2.0 and t["pitch_mm"] == 0.4 and t["kind"] == "external" and t["standard"] == "ISO 261"
    assert nodes["Screws"]["children"] == [doc["nodes"].index(nodes["Screw_1"]), doc["nodes"].index(nodes["Screw_1_thread1"])]
    assert st["triangles"] == 16 + 16 + 32                                    # nothing lost or added by the split
    assert doc["materials"] and len(doc["materials"]) == 1                    # same material for the thread node: same colour


def test_old_groups_without_node_extras_write_the_same_file():
    g = G.Group("Core", (0.6, 0.6, 0.6), extras={"family": "Core"})
    g.add("Core", [cap(0.0), cyl(), cap(4.0)])
    doc, _ = G.parse_glb(G.build_glb([g])[0])
    assert all("extras" not in n for n in doc["nodes"] if "mesh" in n)


# ------------------------------------------------------------------------------------------------ FreeCAD export (E2E)

E2E = os.environ.get("MOTRES_CAD_E2E") == "1"


def _freecadcmd():
    from motor_ai_sim.cad import runner
    try:
        return runner.find_freecadcmd()
    except FileNotFoundError:
        return None


@pytest.mark.skipif(not E2E or _freecadcmd() is None, reason="set MOTRES_CAD_E2E=1 with FreeCAD installed")
@pytest.mark.parametrize("profile,tapped,screw_d,screws,variant", [("CIANO14_40_12.json", {2.0, 3.0}, 2.0, 6, "full"),
                                                                    ("CIANO28_85_20SW1200.json", {2.5, 3.0}, 2.5, 6, "full"),
                                                                    ("CIANO28_85_20SW1200.json", set(), None, 0, "frameless")])
def test_export_marks_screws_and_tapped_holes_and_keeps_the_geometry(tmp_path, profile, tapped, screw_d, screws, variant):
    root = os.path.dirname(store.CAD_DIR)
    out = tmp_path / "out"
    pfile = os.path.join(store.CAD_DIR, "reference", profile)
    if variant == "frameless":          # the frameless kit has no screws, holders or shaft: no thread node, the export still passes
        pj = json.load(open(pfile, encoding="utf-8"))
        pj["profile"]["cad_variant"] = "frameless"
        pfile = str(tmp_path / "frameless_profile.json")
        json.dump(pj, open(pfile, "w", encoding="utf-8"))
    cmd = [sys.executable, os.path.join(store.CAD_DIR, "run_build.py"), "build", "--profile-file", pfile,
           "--out-dir", str(out), "--no-gui", "--no-bom"]
    subprocess.run(cmd, check=True, cwd=root, timeout=3600)
    glb = next(p for p in out.iterdir() if p.name.endswith("_full.glb"))
    doc, _ = G.parse_glb(glb.read_bytes())
    th = [(n["name"], n["extras"]["thread"]) for n in doc["nodes"] if "extras" in n and "thread" in n["extras"]]
    assert doc["asset"]["extras"]["cad_variant"] == variant
    screw_nodes = [t for n, t in th if n.startswith("Screw_")]
    assert len(screw_nodes) == screws and all(t["kind"] == "external" and t["d_mm"] == screw_d for t in screw_nodes)
    # only the ISO 4762 thread length b = 2d + 12 from the tip, never the whole shank (M2x25: b 16, 9 mm plain under the head)
    assert all(t["pitch_mm"] == TH.coarse_pitch(screw_d) and 3 < t["length_mm"] <= 2 * screw_d + 12 + 1e-6 for t in screw_nodes)
    if profile.startswith("CIANO14"):                                        # L20 = M2x25, tip z -11.9, head under-side z 13.1
        assert all(abs(t["z_range_mm"][0] + 11.5) < 1e-3 and abs(t["z_range_mm"][1] - 4.1) < 1e-3 for t in screw_nodes)
        shaft = [t for n, t in th if n.startswith("Shaft_thread")]           # the thread is on the shaft's free END: the tip section
        assert len(shaft) == 1 and shaft[0]["d_mm"] == 5.0 and shaft[0]["pitch_mm"] == 0.8 and shaft[0]["kind"] == "external"
        assert shaft[0]["z_range_mm"] == pytest.approx([28.7, 36.7], abs=1e-3)
    holes = [t for n, t in th if n.startswith("Stator_holder_back")]
    assert all(t["kind"] == "internal" for t in holes) and {t["d_mm"] for t in holes} == tapped
    if variant == "frameless":
        assert not th and not any(n["name"].startswith(("Screw_", "Stator_holder", "Shaft")) for n in doc["nodes"])
    # compare with the export with the thread split switched off (FC_THREADS=0)
    # (exact triangle equality is not a given: OCC may pick other diagonals in planar faces from run to run)
    fcstd = next(p for p in out.iterdir() if p.name.endswith(".FCStd"))
    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    from motor_ai_sim.cad import runner
    code, _log, st = runner.run_script(os.path.join(FC_DIR, "export_models.py"),
                                       dict(FC_CAD_ROOT=store.CAD_DIR, FC_MODEL=str(fcstd), FC_OUT_DIR=str(plain_dir), FC_MOTOR_ID="plain",
                                            FC_REPORT=str(plain_dir / "r.json"), FC_THREADS="0"))
    assert code == 0, st
    plain = G.parse_glb((plain_dir / "plain_full.glb").read_bytes())[0]
    assert not [n for n in plain["nodes"] if "extras" in n and "thread" in n["extras"]]
    # geometry unchanged: every group has the same enclosed volume and keeps all its vertices; only the screw shank is cut at the end
    # of its thread, which ADDS the vertices of the cut circle (OCC may also pick other planar-face diagonals from run to run)
    a_ = G.glb_group_data((plain_dir / "plain_full.glb").read_bytes())
    b_ = G.glb_group_data(glb.read_bytes())
    assert set(a_) == set(b_)
    for k in a_:
        assert round(a_[k][2], 2) == round(b_[k][2], 2), k
        assert a_[k][1] <= b_[k][1], k
        if k != "Screws":
            assert a_[k][:2] == b_[k][:2], k
