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
    assert all(t["pitch_mm"] == TH.coarse_pitch(screw_d) and t["length_mm"] > 3 for t in screw_nodes)
    holes = [t for n, t in th if n.startswith("Stator_holder_back")]
    assert all(t["kind"] == "internal" for t in holes) and {t["d_mm"] for t in holes} == tapped
    if variant == "frameless":
        assert not th and not any(n["name"].startswith(("Screw_", "Stator_holder", "Shaft")) for n in doc["nodes"])
    # the geometry is identical with the thread split switched off (FC_THREADS=0): same groups, triangle counts, vertices, volumes
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
    assert G.glb_group_signature((plain_dir / "plain_full.glb").read_bytes()) == G.glb_group_signature(glb.read_bytes())
