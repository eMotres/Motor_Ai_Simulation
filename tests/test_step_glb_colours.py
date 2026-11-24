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
    assert all({k: v for k, v in p["extras"].items() if k != "label"} == {"family": p["name"], "explode": 0} for p in parts)
    labels = [p["extras"].get("label") for p in parts]                                                     # the STEP's component names
    assert [next(x for x in ("pcb", "chip", "plain", "pins") if lab and x in lab) for lab in labels] == ["pcb", "chip", "plain", "pins"]
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
    p.write_bytes(b"ISO-10303-21;\n" + b"x" * ((1 << 20) - 20) + b"\n#5=COLOUR_RGB('',1.,0.,0.);\n")
    assert step_glb.names_colours(str(p))
    p.write_bytes(b"ISO-10303-21;\nDATA;\nENDSEC;\n")
    assert not step_glb.names_colours(str(p))


def test_the_child_process_and_the_v2_cache(tmp_path):
    src = _coloured(tmp_path / "c.step")
    data = step_glb.glb_for_step(src, "m1", tmp_path / "cache")
    cached = (tmp_path / "cache" / "m1.v3.glb").read_bytes()
    # the cache keeps the component names; a caller gets them only when it asks
    assert step_glb.glb_for_step(src, "m1", tmp_path / "cache", labels=True) == cached
    assert b'"label"' in cached and b'"label"' not in data
    assert data == step_glb.without_labels(cached)
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
    for solid, faces, _name in found:
        cols = {tuple(round(x, 3) for x in c) if c else None for _f, c in faces}
        by_x.setdefault(round(solid.BoundingBox().xmin, 3), []).append(cols)
    assert sorted(by_x) == [0.0, 10.0] and len(found) == 3                                                  # placements kept
    assert {(0.6, 0.4, 0.2)} in by_x[0.0] and {None} in by_x[0.0]           # placement 1: the part's colour; the loose box: steel
    assert by_x[10.0] == [{(0.2, 0.3, 0.7)}]                                # placement 2: its own colour wins


def test_a_file_that_names_a_colour_no_face_uses_gives_the_plain_bytes(tmp_path):
    """Review note on #837: many exports name a COLOUR_RGB that no part uses; the colour reader then finds none and the plain path
    runs - the GLB is byte-identical to the file without that entity."""
    src = _plain(tmp_path / "p.step")
    text = src.read_text()
    at = text.rindex("ENDSEC;", 0, text.rindex("END-ISO-10303-21;"))
    named = tmp_path / "named.step"
    named.write_text(text[:at] + "#999999 = COLOUR_RGB('',1.,0.,0.);\n" + text[at:])
    assert step_glb.names_colours(str(named)) and step_glb.coloured_solids(str(named)) is None
    step_glb.convert_in_process(str(named), str(tmp_path / "named.glb"))
    step_glb.convert_in_process(str(src), str(tmp_path / "plain.glb"))
    assert (tmp_path / "named.glb").read_bytes() == (tmp_path / "plain.glb").read_bytes()


@pytest.mark.parametrize("body, coloured", [
    (b"/* COLOUR_RGB('',1.,0.,0.); */\n#1=PRODUCT('x','y','',(#2));\n", False),                       # in a comment
    (b"#1=PRODUCT('COLOUR_RGB(1,0,0)','it''s a COLOUR_RGB(','',(#2));\n", False),                      # in quoted names
    (b"#1=PRODUCT('a /* b','c',' */ COLOUR_RGB(',(#2));\n", False),                                    # comment marks inside strings
    (b"#7 = ( COLOUR_RGB ( '', 1., 0., 0. ) );\n", True),                                              # a real (complex) instance
    (b"#7=DRAUGHTING_PRE_DEFINED_COLOUR('red');\n", True),
])
def test_only_real_colour_entities_count_not_words_in_comments_or_names(tmp_path, body, coloured):
    """Astra on #837 (P2): a comment or a quoted name that contains COLOUR_RGB must not send the file to the colour reader."""
    p = tmp_path / "x.step"
    p.write_bytes(b"ISO-10303-21;\nDATA;\n" + body + b"ENDSEC;\nEND-ISO-10303-21;\n")
    assert step_glb.names_colours(str(p)) is coloured


def test_an_entity_after_a_long_comment_and_past_a_megabyte_is_found(tmp_path):
    p = tmp_path / "x.step"
    pad = b"/*" + b"COLOUR_RGB(" * 120000 + b"*/\n"                                                    # > 1 MB of a comment
    p.write_bytes(b"ISO-10303-21;\nDATA;\n" + pad + b"#9=COLOUR_RGB('',0.,1.,0.);\nENDSEC;\n")
    assert len(pad) > (1 << 20) and step_glb.names_colours(str(p))
    p.write_bytes(b"ISO-10303-21;\nDATA;\n" + pad + b"ENDSEC;\n")
    assert not step_glb.names_colours(str(p))


def _with_external_part(tmp_path):
    """An assembly written by OCCT's multi-file writer: the coloured part lives in a SECOND STEP file next to the main one."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import XCAFDoc_ColorType, XCAFDoc_DocumentTool
    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    XCAFApp_Application.GetApplication_s().InitDocument(doc)
    st, ct = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main()), XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
    far = st.AddShape(BRepPrimAPI_MakeBox(50, 50, 50).Shape(), False)
    ct.SetColor(far, Quantity_Color(0.9, 0.1, 0.1, Quantity_TOC_RGB), XCAFDoc_ColorType.XCAFDoc_ColorSurf)
    asm = st.NewShape()
    st.AddComponent(asm, far, __import__("OCP.TopLoc", fromlist=["TopLoc_Location"]).TopLoc_Location())
    st.UpdateAssemblies()
    w = STEPCAFControl_Writer()
    w.SetColorMode(True)
    assert w.Transfer(doc, STEPControl_AsIs, "ext")                       # multi-file: components into their own files
    main = tmp_path / "main.step"
    w.Write(str(main))
    return main


def test_a_step_that_references_another_file_never_reaches_the_colour_reader(tmp_path, monkeypatch):
    """Astra on #837 (P1): STEPCAFControl_Reader would load the referenced file (the bound OCCT has no switch for that); such a
    file keeps the plain view, which does not follow references - the referenced geometry never enters the GLB."""
    main = _with_external_part(tmp_path)
    others = [p for p in tmp_path.iterdir() if p.suffix.lower() in (".step", ".stp") and p != main]
    assert others, "the writer did not produce the referenced file"
    assert b"DOCUMENT_FILE" in main.read_bytes()
    called = []
    monkeypatch.setattr(step_glb, "coloured_solids", lambda p: called.append(p) or None)
    try:
        info = step_glb.convert_in_process(str(main), str(tmp_path / "out.glb"))
    except Exception:                                                    # noqa: BLE001 - the main file alone has no geometry: the plain
        info = None                                                      # path fails as v1 did (in the child: "could not be turned ..."), never loads it
    assert called == []
    if info is not None:
        assert max(info["bbox_mm"]) < 50                                 # the 50 mm box of the other file is not in the view


def test_a_document_reference_alone_keeps_the_plain_bytes(tmp_path):
    src = _plain(tmp_path / "p.step")
    text = src.read_text()
    at = text.rindex("ENDSEC;", 0, text.rindex("END-ISO-10303-21;"))
    ref = tmp_path / "ref.step"
    ref.write_text(text[:at] + "#999998 = DOCUMENT_FILE('other.stp','','',#999997,'','');\n#999999 = COLOUR_RGB('',1.,0.,0.);\n" + text[at:])
    step_glb.convert_in_process(str(ref), str(tmp_path / "ref.glb"))
    step_glb.convert_in_process(str(src), str(tmp_path / "plain.glb"))
    assert (tmp_path / "ref.glb").read_bytes() == (tmp_path / "plain.glb").read_bytes()


def test_the_entity_scan_is_linear_on_long_names_spaces_and_open_strings(tmp_path):
    """A 1 MB name, a 1 MB run of spaces or an unclosed 1 MB string must not cost the converter's time budget (no backtracking)."""
    import time
    p = tmp_path / "x.step"
    for body in (b"x" * (2 << 20), b"A" + b" " * (2 << 20) + b"B", b"'" + b"a" * (2 << 20)):
        p.write_bytes(body)
        t = time.monotonic()
        assert step_glb.step_entities(str(p), step_glb.COLOUR_ENTITIES | step_glb.EXTERNAL_ENTITIES) == set()
        assert time.monotonic() - t < 2.0


@pytest.mark.parametrize("raw, shown", [
    ("Core:1", "Core:1"), ("  Stator_holder\n:1 ", "Stator_holder :1"), ("", ""), (None, ""), ("NAUO12", ""), ("=>[0:1:1:2]", ""),
    ("0:1:1:2", ""), ("SOLID", ""), ("COMPOUND", ""), ("Solid rotor", "Solid rotor"), ("x" * 300, "x" * 120),
])
def test_a_component_name_is_one_short_line_and_a_generated_one_is_dropped(raw, shown):
    assert step_glb.clean_label(raw) == shown


def test_the_view_is_handed_out_without_names_unless_asked(tmp_path):
    src = _coloured(tmp_path / "c.step")
    named = step_glb.glb_for_step(src, "m1", tmp_path / "cache", labels=True)
    plain = step_glb.glb_for_step(src, "m1", tmp_path / "cache")
    labels = [n["extras"].get("label") for n in _glb(named)["nodes"][1:]]
    assert any("pcb" in (x or "") for x in labels)
    assert all(word not in plain for word in (b"pcb", b"chip", b"pins", b"label"))
    a, b = _glb(named), _glb(plain)
    for n in a["nodes"]:
        n.get("extras", {}).pop("label", None)
    assert a == b and step_glb.without_labels(plain) is plain                                                # nothing else changes
    assert named[-1000:] == plain[-1000:]                                                                   # the binary chunk


@pytest.mark.parametrize("cap, what", [("MAX_PARTS", "parts"), ("MAX_TRIANGLES", "triangles")])
def test_an_assembly_over_a_budget_is_refused_with_a_message(tmp_path, monkeypatch, cap, what):
    src = _coloured(tmp_path / "c.step")
    monkeypatch.setattr(step_glb, cap, 2)
    with pytest.raises(step_glb.StepConversionError) as err:
        step_glb.convert_in_process(str(src), str(tmp_path / "c.glb"))
    assert err.value.status == 413 and what in err.value.detail and "download the STEP" in err.value.detail
    assert not (tmp_path / "c.glb").exists()


def test_a_too_deep_assembly_is_refused(tmp_path, monkeypatch):
    src = _coloured(tmp_path / "c.step")
    monkeypatch.setattr(step_glb, "MAX_DEPTH", 0)
    with pytest.raises(step_glb.StepConversionError) as err:
        step_glb.convert_in_process(str(src), str(tmp_path / "c.glb"))
    assert err.value.status == 413 and "nested levels" in err.value.detail


@pytest.mark.parametrize("line, status, text", [
    ("StepConversionError(413): /srv/data/secret.step has 9 parts", 413, "This assembly is too large for the 3-D view"),
    ("StepConversionError(422): whatever", 422, "The STEP file has no solid geometry."),
    ("StepConversionError(200): fine", 422, "This STEP file could not be turned"),
    ("StepConversionError(302): http://example.invalid", 422, "This STEP file could not be turned"),
    ("StepConversionError(500): trace", 422, "This STEP file could not be turned")])
def test_a_child_refusal_becomes_a_fixed_status_and_text(tmp_path, monkeypatch, line, status, text):
    class Done:
        returncode, stdout, stderr = 3, "", chr(10).join(("noise", line, ""))
    monkeypatch.setattr(step_glb.subprocess, "run", lambda *a, **k: Done())
    with pytest.raises(step_glb.StepConversionError) as err:
        step_glb._run_child(tmp_path / "a.step", tmp_path / "a.glb", 5)
    assert err.value.status == status and err.value.detail.startswith(text)
    assert "secret" not in err.value.detail and "invalid" not in err.value.detail


def test_names_over_the_total_budget_are_dropped_but_the_view_stays(tmp_path, monkeypatch):
    monkeypatch.setattr(step_glb, "MAX_LABEL_BYTES", 5)
    assert step_glb.bounded_labels(["ab", "cd"]) == ["ab", "cd"] and step_glb.bounded_labels(["abc", "def"]) == ["", ""]
    src = _coloured(tmp_path / "c.step")
    info = step_glb.convert_in_process(str(src), str(tmp_path / "c.glb"))
    doc = _glb((tmp_path / "c.glb").read_bytes())
    assert info["colours"] == 3 and all("label" not in n["extras"] for n in doc["nodes"][1:])


def test_a_huge_raw_name_is_not_read_and_a_failing_name_never_costs_the_colours(tmp_path, monkeypatch):
    import cadquery as cq
    a = cq.Assembly(name="top")
    a.add(cq.Workplane().box(1, 1, 1), name="x" * 5000, color=cq.Color(1, 0, 0))
    a.add(cq.Workplane().box(1, 1, 1).translate((3, 0, 0)), name="ok", color=cq.Color(0, 1, 0))
    a.save(str(tmp_path / "h.step"))
    step_glb.convert_in_process(str(tmp_path / "h.step"), str(tmp_path / "h.glb"))
    glb = (tmp_path / "h.glb").read_bytes()
    assert b"x" * 200 not in glb
    monkeypatch.setattr(step_glb, "clean_label", lambda raw: (_ for _ in ()).throw(RuntimeError("bad name")))
    info = step_glb.convert_in_process(str(tmp_path / "h.step"), str(tmp_path / "h2.glb"))
    assert info["colours"] == 2
