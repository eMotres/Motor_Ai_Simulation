"""Coloured STEP -> GLB (owner 2026-11-23: "upload the coloured STEP").  Synthetic STEP files made here with cadquery (no customer
file): a coloured assembly keeps its colours - one material per colour, a part stays one node with one primitive per colour - and a
STEP without colours, or a failing colour reader, gives exactly the GLB of the earlier converter."""
from __future__ import annotations

import json
import struct

import pytest

cq = pytest.importorskip("cadquery")

from motor_ai_sim.production import step_glb  # noqa: E402


def _glb(data: bytes):
    n = struct.unpack("<I", data[12:16])[0]
    return json.loads(data[20:20 + n])


def _coloured(path):
    """A board-like assembly: a green board, a dark chip, a plain part with one red face, and an uncoloured connector (cadquery
    writes no colour of a sub-assembly; inheritance is checked with an XCAF-made file below)."""
    a = cq.Assembly(name="board")
    a.add(cq.Workplane().box(10, 10, 1.6), name="pcb", color=cq.Color(0.0, 0.5, 0.0))
    a.add(cq.Workplane().box(3, 3, 1).translate((0, 0, 1.3)), name="chip", color=cq.Color(0.1, 0.1, 0.1))
    plain = cq.Workplane().box(2, 2, 2).translate((8, 0, 0))
    a.add(plain, name="plain")
    a.addSubshape(plain.val().Faces()[0], name="redface", color=cq.Color(1, 0, 0))
    sub = cq.Assembly(name="connector", color=cq.Color(0.8, 0.6, 0.2))
    sub.add(cq.Workplane().box(1, 4, 1).translate((-8, 0, 0)), name="pins")
    a.add(sub, name="connector")
    a.save(str(path))
    return path


def _plain(path):
    """The same kind of geometry without any colour."""
    shapes = cq.Workplane().box(10, 10, 1.6).union(cq.Workplane().box(3, 3, 1).translate((0, 0, 1.3)))
    cq.exporters.export(cq.Workplane().add(shapes.val()).add(cq.Workplane().box(2, 2, 2).translate((8, 0, 0)).val()), str(path))
    return path


def test_a_coloured_step_keeps_its_colours_one_material_each_and_one_node_per_part(tmp_path):
    src = _coloured(tmp_path / "c.step")
    assert step_glb.names_colours(str(src))
    info = step_glb.convert_in_process(str(src), str(tmp_path / "c.glb"))
    doc = _glb((tmp_path / "c.glb").read_bytes())
    names = [m["name"] for m in doc["materials"]]
    assert names[0] == "steel" and info["colours"] == len(names) - 1 == 3
    rgb = {m["name"]: m["pbrMetallicRoughness"]["baseColorFactor"][:3] for m in doc["materials"]}
    assert any(abs(c[0] - 1) < 1e-3 and c[1] < 1e-3 and c[2] < 1e-3 for c in rgb.values())               # the red face
    assert any(c[0] < 1e-3 and 0.1 < c[1] < 0.4 and c[2] < 1e-3 for c in rgb.values())                    # the green board (linear)
    model = doc["nodes"][0]
    assert model["name"] == "Model" and len(model["children"]) == info["solids"] == 4                     # one node per part
    parts = [doc["nodes"][i] for i in model["children"]]
    assert [p["name"] for p in parts] == ["Part_1", "Part_2", "Part_3", "Part_4"]
    assert all(p["extras"] == {"family": p["name"], "explode": 0} for p in parts)
    prims = [doc["meshes"][p["mesh"]]["primitives"] for p in parts]
    assert sorted(len(p) for p in prims) == [1, 1, 1, 2]                                                    # the plain part: steel + red
    two = next(p for p in prims if len(p) == 2)
    assert two[0]["material"] == 0 and names[two[1]["material"]].startswith("colour_")
    single = [names[p[0]["material"]] for p in prims if len(p) == 1]
    assert sorted(n == "steel" for n in single) == [False, False, True]                                     # board, chip; pins steel


def test_a_step_without_colours_gives_exactly_the_earlier_glb(tmp_path):
    src = _plain(tmp_path / "p.step")
    assert not step_glb.names_colours(str(src))
    step_glb.convert_in_process(str(src), str(tmp_path / "now.glb"))
    step_glb._convert_plain(str(src), str(tmp_path / "plain.glb"))
    now = (tmp_path / "now.glb").read_bytes()
    assert now == (tmp_path / "plain.glb").read_bytes()
    doc = _glb(now)
    assert [m["name"] for m in doc["materials"]] == ["steel"]
    assert all(len(m["primitives"]) == 1 for m in doc["meshes"])


def test_a_failing_colour_reader_falls_back_to_the_view_without_colours(tmp_path, monkeypatch, caplog):
    src = _coloured(tmp_path / "c.step")
    step_glb._convert_plain(str(src), str(tmp_path / "plain.glb"))
    monkeypatch.setattr(step_glb, "coloured_solids", lambda p: (_ for _ in ()).throw(RuntimeError("no XCAF here")))
    step_glb.convert_in_process(str(src), str(tmp_path / "out.glb"))
    assert (tmp_path / "out.glb").read_bytes() == (tmp_path / "plain.glb").read_bytes()
    assert "STEP colours not read" in caplog.text


def test_colour_names_are_found_across_read_chunks(tmp_path):
    p = tmp_path / "x.step"
    p.write_bytes(b"ISO-10303-21;\n" + b"x" * ((1 << 20) - 20) + b"COLOUR_RGB('',1.,0.,0.);\n")
    assert step_glb.names_colours(str(p))
    p.write_bytes(b"ISO-10303-21;\nDATA;\nENDSEC;\n")
    assert not step_glb.names_colours(str(p))


def test_the_child_process_and_the_v2_cache(tmp_path):
    src = _coloured(tmp_path / "c.step")
    data = step_glb.glb_for_step(src, "m1", tmp_path / "cache")
    assert (tmp_path / "cache" / "m1.v2.glb").read_bytes() == data
    assert len(_glb(data)["materials"]) == 4


def _inherited(path):
    """An XCAF document written by OCCT: a part whose definition carries a colour, placed twice in an assembly - the second placement
    with a colour of its own; a loose part has none.  (A colour on the assembly ROOT is not carried by OCCT's STEP round trip at
    all, so it cannot be tested from here; KiCad writes its colours on the solids.)"""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Trsf, gp_Vec
    from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopLoc import TopLoc_Location
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import XCAFDoc_ColorType, XCAFDoc_DocumentTool

    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    XCAFApp_Application.GetApplication_s().InitDocument(doc)
    st, ct = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main()), XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
    part = st.AddShape(BRepPrimAPI_MakeBox(2, 2, 2).Shape(), False)
    loose = st.AddShape(BRepPrimAPI_MakeBox(1, 1, 1).Shape(), False)
    asm = st.NewShape()

    def at(x):
        t = gp_Trsf()
        t.SetTranslation(gp_Vec(x, 0, 0))
        return TopLoc_Location(t)
    st.AddComponent(asm, part, at(0))
    own = st.AddComponent(asm, part, at(10))
    ct.SetColor(part, Quantity_Color(0.6, 0.4, 0.2, Quantity_TOC_RGB), XCAFDoc_ColorType.XCAFDoc_ColorGen)  # the definition's, generic
    ct.SetColor(own, Quantity_Color(0.2, 0.3, 0.7, Quantity_TOC_RGB), XCAFDoc_ColorType.XCAFDoc_ColorSurf)  # this placement's own
    st.UpdateAssemblies()
    w = STEPCAFControl_Writer()
    w.SetColorMode(True)
    assert w.Transfer(doc, STEPControl_AsIs)
    w.Write(str(path))
    return path, loose


def test_a_placement_inherits_its_parts_colour_and_its_own_colour_wins(tmp_path):
    src, _ = _inherited(tmp_path / "a.step")
    assert step_glb.names_colours(str(src))
    found = step_glb.coloured_solids(str(src))
    by_x = {}
    for solid, faces in found:
        cols = {tuple(round(x, 3) for x in c) if c else None for _f, c in faces}
        by_x.setdefault(round(solid.BoundingBox().xmin, 3), []).append(cols)
    assert sorted(by_x) == [0.0, 10.0] and len(found) == 3                                                  # placements kept
    assert {(0.6, 0.4, 0.2)} in by_x[0.0] and {None} in by_x[0.0]           # placement 1: the part's colour; the loose box: steel
    assert by_x[10.0] == [{(0.2, 0.3, 0.7)}]                                # placement 2: its own colour wins
