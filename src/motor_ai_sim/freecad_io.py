"""FreeCAD round-trip for the parametric motor design.

EXPORT: a ``.FCStd`` project the user opens directly in FreeCAD — every solid
of the ACTIVE machine (stator, rotor, magnets, shaft, coils) plus a
``Parameters`` spreadsheet holding all geometry parameters with their names as
cell ALIASES.  The solids are extrusions of the exact 2-D polygons the solver
meshes (``get_2d_polygons``) — what you see in FreeCAD is what gets simulated,
not a display model.  A plain STEP and a ``.FCMacro`` ride along: the STEP for
any other CAD, the macro as a belt-and-braces path that rebuilds the same
document through FreeCAD's own API in case a FreeCAD version rejects our
hand-written ``Document.xml``.

IMPORT: the user edits the spreadsheet in FreeCAD (designs a housing around
the motor, tweaks dimensions) and uploads the ``.FCStd`` back; the parameter
values are read out of the sheet by ALIAS, validated by the same loud
geometry validator every other input path uses, and applied.  The master
parametric model stays THIS codebase — FreeCAD edits change parameters, never
the geometry engine (two engines drift; we measured what that costs).

``.FCStd`` is a ZIP: ``Document.xml`` (object tree + spreadsheet cells) +
one ``.brp`` (BREP) file per Part::Feature + ``GuiDocument.xml`` (colors).
No FreeCAD installation is involved on our side.

MACRO BUNDLE (no OCP).  Windows Defender Application Control blocks
``OCP``/``cadquery`` on the user's PC (2026-09-14: their VTK DLL is denied by
an enforced enterprise policy, so ``import OCP`` raises ImportError and no
BREP/STEP can be written here).  FreeCAD's OWN OpenCASCADE is signed and runs
fine on that machine, so the fallback ships the 2-D sections as a DXF plus a
macro that does the extrusion INSIDE FreeCAD — same solids, same Parameters
sheet, one extra click.  Same pattern as the mapbox_earcut/``triangle``
fallback in the 3-D viewer.
"""
from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Any, Dict, Iterable, List, Optional, Tuple

log = logging.getLogger(__name__)

# Rows of the Parameters sheet: A = name (alias on B), B = value, C = unit,
# D = description.  Values are plain numbers — FreeCAD expressions can bind to
# ``Parameters.<alias>`` directly.
_SHEET_HEADER = ("parameter", "value", "unit", "description")


# ─────────────────────────────────────────────────────────────────────────────
# 2-D polygons → CadQuery solids
# ─────────────────────────────────────────────────────────────────────────────

def ocp_available() -> Tuple[bool, str]:
    """(can we build solids here, why not).  App Control blocks OCP on the
    user's PC (2026-09-14) — the caller switches to the macro bundle.

    Delegates to the ONE memoised probe in ``cadquery_geometry``: probing here
    with a bare ``import cadquery`` re-ran a multi-second FAILING import on
    every export, parked the worker thread in importlib's module lock together
    with every 3-D viewer request, and — because a concurrent importer leaves a
    half-built ``cadquery`` in ``sys.modules`` — sometimes answered "yes" and
    sent the export into ``build_solids`` to die there.  That convoy is what
    hung the API on 2026-09-14; the whole story is in ``cadquery_geometry``.
    """
    from motor_ai_sim.cadquery_geometry import cadquery_probe
    return cadquery_probe()


def _cq():
    """The resolved CadQuery module — never a bare ``import cadquery``."""
    from motor_ai_sim import cadquery_geometry as _cg
    ok, why = _cg.cadquery_probe()
    if not ok:
        # ImportError on purpose: the export route treats exactly that as
        # "no kernel here" and ships the macro bundle instead of a 500.
        raise ImportError("CadQuery/OCP is not usable here: %s" % why)
    return _cg.cq


def _poly_to_face(poly) -> "Any":
    """Shapely Polygon (mm, holes included) → cq.Face on the XY plane."""
    cq = _cq()

    def _wire(coords) -> "Any":
        pts = [cq.Vector(float(x), float(y), 0.0) for x, y in list(coords)[:-1]]
        return cq.Wire.makePolygon(pts, close=True)

    outer = _wire(poly.exterior.coords)
    inners = [_wire(r.coords) for r in poly.interiors]
    return cq.Face.makeFromWires(outer, inners)


def _extrude(poly, length_mm: float) -> "Any":
    cq = _cq()
    geoms = getattr(poly, "geoms", None)
    if geoms is not None:                          # MultiPolygon
        solids = [_extrude(g, length_mm) for g in geoms]
        return cq.Compound.makeCompound(solids)
    return cq.Solid.extrudeLinear(_poly_to_face(poly),
                                  cq.Vector(0.0, 0.0, float(length_mm)))


def build_solids(geo: Dict[str, Any]) -> Dict[str, Any]:
    """name → cq Shape for every part of the machine, extruded by the stack.

    The SOLVER's polygons, not a separate display model.  Coils and magnets are
    compounds (312 conductor polygons as one object keeps the FreeCAD tree
    usable).  Insulation is skipped — sub-0.2 mm shells triple the file size
    and add nothing a housing designer needs.
    """
    cq = _cq()
    from motor_ai_sim.cadquery_geometry import CadQueryMotor

    motor = CadQueryMotor()
    motor.set_parameters(dict(geo))
    polys = motor.get_2d_polygons(rotor_angle_deg=0.0)
    L = float(geo.get("motor_length", 30.0))

    out: Dict[str, Any] = {}
    for name, key in (("Stator", "stator"), ("Rotor", "rotor"), ("Shaft", "shaft")):
        p = polys.get(key)
        if p is not None and getattr(p, "area", 0.0) > 0.0:
            out[name] = _extrude(p, L)
    mags = [mp for mp, _sign in (polys.get("magnets") or [])]
    if mags:
        out["Magnets"] = cq.Compound.makeCompound([_extrude(m, L) for m in mags])
    coils = list(polys.get("coils") or [])
    if coils:
        out["Coils"] = cq.Compound.makeCompound([_extrude(c, L) for c in coils])
    if not out:
        raise ValueError("no solids could be built from the active geometry")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# .FCStd writer
# ─────────────────────────────────────────────────────────────────────────────

_COLORS = {                       # packed RGBA the FreeCAD viewer uses
    "Stator":  0x8090A0FF,        # steel grey-blue
    "Rotor":   0x707890FF,
    "Shaft":   0xB0B0B0FF,
    "Magnets": 0xC03030FF,        # red
    "Coils":   0xC08020FF,        # copper
}


def _xml_escape(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _sheet_cells(params: List[Tuple[str, Any, str, str]]) -> str:
    rows = ['<Cell address="A1" content="%s" /><Cell address="B1" content="%s" />'
            '<Cell address="C1" content="%s" /><Cell address="D1" content="%s" />'
            % tuple(_xml_escape(h) for h in _SHEET_HEADER)]
    for i, (name, value, unit, desc) in enumerate(params, start=2):
        rows.append('<Cell address="A%d" content="%s" />' % (i, _xml_escape(name)))
        rows.append('<Cell address="B%d" content="%s" alias="%s" />'
                    % (i, _xml_escape(value), _xml_escape(name)))
        rows.append('<Cell address="C%d" content="%s" />' % (i, _xml_escape(unit)))
        rows.append('<Cell address="D%d" content="%s" />' % (i, _xml_escape(desc)))
    return "\n".join(rows)


def write_fcstd(solids: Dict[str, Any],
                params: List[Tuple[str, Any, str, str]],
                label: str) -> bytes:
    """Assemble the .FCStd ZIP in memory.  Layout follows what FreeCAD itself
    writes (SchemaVersion 4, one BREP per Part::Feature, spreadsheet cells in
    a ``Spreadsheet::PropertySheet``); verified against documents saved by
    FreeCAD 0.20/0.21/1.0 — the reader has been format-stable for years."""
    names = list(solids)
    objects_decl = ['<Object type="Spreadsheet::Sheet" name="Parameters" />']
    objects_data = ['''<Object name="Parameters">
<Properties Count="2">
<Property name="Label" type="App::PropertyString"><String value="Parameters"/></Property>
<Property name="cells" type="Spreadsheet::PropertySheet">
<Cells Count="%d" xlink="1">
%s
</Cells>
</Property>
</Properties>
</Object>''' % (4 * (len(params) + 1), _sheet_cells(params))]

    breps: Dict[str, bytes] = {}
    for nm in names:
        fn = "%sShape.brp" % nm
        buf = io.BytesIO()
        solids[nm].exportBrep(buf)
        breps[fn] = buf.getvalue()
        objects_decl.append('<Object type="Part::Feature" name="%s" />' % nm)
        objects_data.append('''<Object name="%s">
<Properties Count="2">
<Property name="Label" type="App::PropertyString"><String value="%s"/></Property>
<Property name="Shape" type="Part::PropertyPartShape"><Part file="%s"/></Property>
</Properties>
</Object>''' % (nm, nm, fn))

    doc = '''<?xml version='1.0' encoding='utf-8'?>
<Document SchemaVersion="4" ProgramVersion="0.21R" FileVersion="1">
<Properties Count="1" TransientCount="0">
<Property name="Label" type="App::PropertyString" status="1"><String value="%s"/></Property>
</Properties>
<Objects Count="%d" Dependencies="0">
%s
</Objects>
<ObjectData Count="%d">
%s
</ObjectData>
</Document>
''' % (_xml_escape(label), len(names) + 1,
       "\n".join(objects_decl), len(names) + 1, "\n".join(objects_data))

    gui_objs = []
    for nm in names:
        gui_objs.append('''<ViewProvider name="%s" expanded="0">
<Properties Count="1">
<Property name="ShapeColor" type="App::PropertyColor"><PropertyColor value="%d"/></Property>
</Properties>
</ViewProvider>''' % (nm, _COLORS.get(nm, 0x999999FF)))
    gui = '''<?xml version='1.0' encoding='utf-8'?>
<Document SchemaVersion="1">
<ViewProviderData Count="%d">
%s
</ViewProviderData>
</Document>
''' % (len(names), "\n".join(gui_objs))

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        # FreeCAD expects Document.xml FIRST in the archive.
        z.writestr("Document.xml", doc)
        z.writestr("GuiDocument.xml", gui)
        for fn, blob in breps.items():
            z.writestr(fn, blob)
    return out.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# STEP + fallback macro
# ─────────────────────────────────────────────────────────────────────────────

def write_step(solids: Dict[str, Any]) -> bytes:
    """Named-parts STEP via a cq.Assembly (opens in any CAD)."""
    import tempfile, os
    cq = _cq()
    asm = cq.Assembly(name="motor")
    for nm, s in solids.items():
        c = _COLORS.get(nm, 0x999999FF)
        asm.add(s, name=nm, color=cq.Color(((c >> 24) & 0xFF) / 255.0,
                                           ((c >> 16) & 0xFF) / 255.0,
                                           ((c >> 8) & 0xFF) / 255.0))
    fd, path = tempfile.mkstemp(suffix=".step")
    os.close(fd)
    try:
        # cq 2.7 renamed Assembly.save → export (save warns of removal)
        (asm.export if hasattr(asm, "export") else asm.save)(path)
        with open(path, "rb") as f:
            return f.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def write_macro(params: List[Tuple[str, Any, str, str]], step_name: str) -> str:
    """A .FCMacro that rebuilds the document through FreeCAD's own API —
    the guaranteed path if a FreeCAD version dislikes our hand-written FCStd:
    run it from Macro → Macros… with the STEP in the same folder."""
    lines = ["# Auto-generated by motor_ai_sim — rebuilds the motor project",
             "# through FreeCAD's own API.  Put %s next to this macro." % step_name,
             "import FreeCAD, ImportGui, Spreadsheet, os",
             "doc = FreeCAD.newDocument('motor')",
             "sh = doc.addObject('Spreadsheet::Sheet', 'Parameters')",
             "rows = ["]
    for name, value, unit, desc in params:
        lines.append("    (%r, %r, %r, %r)," % (name, value, unit, desc))
    lines += ["]",
              "sh.set('A1', 'parameter'); sh.set('B1', 'value')",
              "sh.set('C1', 'unit'); sh.set('D1', 'description')",
              "for i, (n, v, u, d) in enumerate(rows, start=2):",
              "    sh.set('A%d' % i, str(n)); sh.set('B%d' % i, str(v))",
              "    sh.set('C%d' % i, str(u)); sh.set('D%d' % i, str(d))",
              "    sh.setAlias('B%d' % i, str(n))",
              "doc.recompute()",
              "step = os.path.join(os.path.dirname(__file__), %r)" % step_name,
              "if os.path.exists(step):",
              "    ImportGui.insert(step, doc.Name)",
              "doc.recompute()"]
    return "\n".join(lines) + "\n"


# ═════════════════════════════════════════════════════════════════════════════
# OCP-FREE bundle: DXF sections + a macro that extrudes them inside FreeCAD
# ═════════════════════════════════════════════════════════════════════════════
# WHY this exists: App Control blocks OCP on the user's PC (2026-09-14), so no
# BREP/STEP can be written server-side.  Everything below is pure Python —
# shapely polygons in, text out — and the OpenCASCADE work happens in FreeCAD.

#: Air bands / slip-surface rings are solver scaffolding, not parts — excluded.
_DXF_PART_KEYS = (("stator", "stator"), ("rotor", "rotor"),
                  ("sleeve", "sleeve"), ("shaft", "shaft"))

CSV_HEADER = _SHEET_HEADER          # parameter,value,unit,description


def part_polygons(geo: Dict[str, Any]) -> List[Tuple[str, Any]]:
    """[(layer_name, shapely geom)] for every PART of the machine.

    The mesher's own polygons (``get_2d_polygons`` — pure shapely, no OCP), so
    the DXF is the simulated section and not a display model.  One layer per
    part: stator, rotor, magnet_<i>, coil_<i>, sleeve, shaft.
    """
    from motor_ai_sim.cadquery_geometry import CadQueryMotor

    motor = CadQueryMotor()
    motor.set_parameters(dict(geo))
    polys = motor.get_2d_polygons(rotor_angle_deg=0.0)

    out: List[Tuple[str, Any]] = []
    for layer, key in _DXF_PART_KEYS:
        p = polys.get(key)
        if p is not None and float(getattr(p, "area", 0.0)) > 0.0:
            out.append((layer, p))
    for i, item in enumerate(polys.get("magnets") or []):
        p = item[0] if isinstance(item, (tuple, list)) else item
        if float(getattr(p, "area", 0.0)) > 0.0:
            out.append(("magnet_%d" % i, p))
    for i, p in enumerate(polys.get("coils") or []):
        if float(getattr(p, "area", 0.0)) > 0.0:
            out.append(("coil_%d" % i, p))
    if not out:
        raise ValueError("no part polygons could be built from the active geometry")
    return out


def _rings(geom) -> Iterable[List[Tuple[float, float]]]:
    """Every closed ring of a Polygon/MultiPolygon, exteriors AND holes, with
    shapely's repeated closing point dropped (the DXF flags the wire closed)."""
    geoms = getattr(geom, "geoms", None)
    if geoms is not None:
        for g in geoms:
            for r in _rings(g):
                yield r
        return
    ext = getattr(geom, "exterior", None)
    if ext is None:
        return
    for coords in [ext.coords] + [r.coords for r in geom.interiors]:
        pts = [(float(x), float(y)) for x, y in list(coords)]
        if len(pts) > 1 and pts[0] == pts[-1]:
            pts = pts[:-1]
        if len(pts) >= 3:
            yield pts


def write_dxf(parts: List[Tuple[str, Any]]) -> bytes:
    """DXF R12, one LAYER per part, one closed POLYLINE per ring, units mm.

    R12 (not R14 LWPOLYLINE) because every CAD on earth still reads it and the
    hand-written fallback below stays twenty lines.  ezdxf when it is
    importable (pure Python, unaffected by App Control), else our own writer —
    both emit the SAME entity kinds so one parser reads either."""
    try:
        import ezdxf
    except ImportError:
        return _write_dxf_minimal(parts)
    doc = ezdxf.new("R12", setup=False)
    doc.header["$INSUNITS"] = 4                       # millimetres
    msp = doc.modelspace()
    for layer, geom in parts:
        if layer not in doc.layers:
            doc.layers.add(layer)
        for ring in _rings(geom):
            msp.add_polyline2d(ring, close=True, dxfattribs={"layer": layer})
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8")


def _write_dxf_minimal(parts: List[Tuple[str, Any]]) -> bytes:
    """Same file without ezdxf: HEADER + LAYER table + POLYLINE/VERTEX/SEQEND."""
    layers = [lay for lay, _g in parts]
    o: List[str] = ["0", "SECTION", "2", "HEADER",
                    "9", "$ACADVER", "1", "AC1009",
                    "9", "$INSUNITS", "70", "4",
                    "0", "ENDSEC",
                    "0", "SECTION", "2", "TABLES",
                    "0", "TABLE", "2", "LAYER", "70", str(len(layers))]
    for lay in layers:
        o += ["0", "LAYER", "2", lay, "70", "0", "62", "7", "6", "CONTINUOUS"]
    o += ["0", "ENDTAB", "0", "ENDSEC", "0", "SECTION", "2", "ENTITIES"]
    for lay, geom in parts:
        for ring in _rings(geom):
            o += ["0", "POLYLINE", "8", lay, "66", "1", "70", "1",
                  "10", "0.0", "20", "0.0", "30", "0.0"]
            for x, y in ring:
                o += ["0", "VERTEX", "8", lay,
                      "10", "%.6f" % x, "20", "%.6f" % y, "30", "0.0"]
            o += ["0", "SEQEND", "8", lay]
    o += ["0", "ENDSEC", "0", "EOF"]
    return ("\n".join(o) + "\n").encode("utf-8")


def macro_rows(geo: Dict[str, Any]) -> List[Tuple[str, Any, str, str]]:
    """(name, value, unit, description) for the PRIMARY parameters — the exact
    set the Fusion channel exports, taken from its own ``_rows()`` so the two
    channels can never drift.  Names are OUR geometry keys (not the Fusion
    rename map): the FCStd import reads the sheet back by these aliases."""
    from motor_ai_sim.routes.fusion import _rows
    return [(our_key, value, unit, desc)
            for _fusion_name, our_key, value, unit, desc in _rows()]


def write_parameters_csv(rows: List[Tuple[str, Any, str, str]]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(list(CSV_HEADER))
    for name, value, unit, desc in rows:
        w.writerow([name, value, unit, desc])
    return buf.getvalue().encode("utf-8")


def write_dxf_macro(rows: List[Tuple[str, Any, str, str]],
                    dxf_name: str, length_mm: float, label: str) -> str:
    """The .FCMacro that turns the DXF into the same solids the OCP bundle
    ships.  It parses the DXF itself (group codes — twenty lines) instead of
    calling importDXF: the legacy importer wants to download its library on
    first use, and a macro that reaches the internet is not a fallback."""
    head = [
        "# Auto-generated by motor_ai_sim — %s" % label,
        "# App Control blocks OCP on the server (2026-09-14), so the solids are",
        "# built HERE, by FreeCAD's own OpenCASCADE.  Put %s next to this file"
        % dxf_name,
        "# and run: Macro -> Macros... -> Execute.",
        "import os",
        "import FreeCAD as App",
        "import Part",
        "import Spreadsheet  # noqa: F401 (registers Spreadsheet::Sheet)",
        "",
        "DXF = %r" % dxf_name,
        "LENGTH = %r" % float(length_mm),
        "ROWS = [",
    ]
    for name, value, unit, desc in rows:
        head.append("    (%r, %r, %r, %r)," % (name, value, unit, desc))
    body = r''']


def read_rings(path):
    """[(layer, [(x, y), ...])] — closed POLYLINEs of a DXF R12 file."""
    with open(path, "r", errors="ignore") as fh:
        lines = fh.read().splitlines()
    pairs = []
    for k in range(0, len(lines) - 1, 2):
        try:
            pairs.append((int(lines[k].strip()), lines[k + 1].strip()))
        except ValueError:
            continue
    rings, cur, layer, pts, vx, vy = [], None, None, None, None, None
    for code, val in pairs:
        if code == 0:
            if cur == "VERTEX" and pts is not None and vx is not None:
                pts.append((vx, vy))
            if val == "POLYLINE":
                pts, layer = [], None
            elif val == "VERTEX":
                vx = vy = None
            elif val == "SEQEND":
                if pts:
                    rings.append((layer, pts))
                pts = None
            cur = val
        elif cur == "POLYLINE" and code == 8:
            layer = val
        elif cur == "VERTEX":
            if code == 10:
                vx = float(val)
            elif code == 20:
                vy = float(val)
    return rings


def face_from_wires(wires):
    """Even-odd fill: a ring inside an odd number of others is a HOLE.
    Reproduces exactly the shapely exterior/interior nesting of the export."""
    faces = [Part.Face(w) for w in wires]
    solid_faces, hole_faces = [], []
    for i, w in enumerate(wires):
        p = w.Vertexes[0].Point
        depth = 0
        for j, f in enumerate(faces):
            if i != j and f.isInside(App.Vector(p.x, p.y, 0.0), 1e-7, True):
                depth += 1
        (hole_faces if depth % 2 else solid_faces).append(faces[i])
    if not solid_faces:
        return None
    shape = solid_faces[0]
    for f in solid_faces[1:]:
        shape = shape.fuse(f)
    for f in hole_faces:
        shape = shape.cut(f)
    return shape


path = os.path.join(os.path.dirname(__file__), DXF)
if not os.path.exists(path):
    raise IOError("%s must sit next to this macro" % DXF)

doc = App.newDocument("motor")

sh = doc.addObject("Spreadsheet::Sheet", "Parameters")
sh.set("A1", "parameter"); sh.set("B1", "value")
sh.set("C1", "unit"); sh.set("D1", "description")
for i, (n, v, u, d) in enumerate(ROWS, start=2):
    sh.set("A%d" % i, str(n)); sh.set("B%d" % i, str(v))
    sh.set("C%d" % i, str(u)); sh.set("D%d" % i, str(d))
    sh.setAlias("B%d" % i, str(n))

by_layer = {}
for layer, pts in read_rings(path):
    by_layer.setdefault(layer, []).append(pts)

solids = {}
for layer in sorted(by_layer):
    wires = []
    for pts in by_layer[layer]:
        vecs = [App.Vector(x, y, 0.0) for x, y in pts]
        wires.append(Part.makePolygon(vecs + [vecs[0]]))
    face = face_from_wires(wires)
    if face is not None:
        solids[layer] = face.extrude(App.Vector(0.0, 0.0, LENGTH))

# One object per magnet/coil would be hundreds of tree items; the solids are
# compounded per family, exactly as the .FCStd export does.
GROUPS = [("magnet_", "Magnets"), ("coil_", "Coils")]
named = []
for prefix, name in GROUPS:
    members = [s for lay, s in sorted(solids.items()) if lay.startswith(prefix)]
    if members:
        named.append((name, Part.makeCompound(members)))
for layer, shape in sorted(solids.items()):
    if not any(layer.startswith(p) for p, _n in GROUPS):
        named.append((layer.capitalize(), shape))

for name, shape in named:
    obj = doc.addObject("Part::Feature", name)
    obj.Label = name
    obj.Shape = shape

doc.recompute()
print("motor rebuilt: %d object(s) from %d profile(s), extruded %.3f mm"
      % (len(named), len(solids), LENGTH))
'''
    return "\n".join(head) + body


def build_macro_bundle(geo: Dict[str, Any], label: str, safe: str,
                       reason: str = "") -> bytes:
    """The whole OCP-free ZIP: DXF sections + parameters.csv + macro + README."""
    parts = part_polygons(geo)
    rows = macro_rows(geo)
    length = float(geo.get("motor_length", 30.0))
    dxf_name = "%s.dxf" % safe
    dxf = write_dxf(parts)
    csv_blob = write_parameters_csv(rows)
    macro = write_dxf_macro(rows, dxf_name, length, label)
    readme = (
        "motor_ai_sim FreeCAD export (MACRO bundle) - %s\n"
        "\n"
        "WHY THIS IS NOT AN .FCStd\n"
        "  Windows Application Control blocks OCP/OpenCASCADE on the machine\n"
        "  running the simulator, so it cannot write solids itself:\n"
        "    %s\n"
        "  FreeCAD's own OpenCASCADE is not blocked, so the solids are built on\n"
        "  YOUR side instead - same geometry, one extra click.\n"
        "\n"
        "THREE STEPS\n"
        "  1. Unzip everything into one folder (the macro reads the DXF next to it).\n"
        "  2. FreeCAD -> Macro -> Macros... -> add this folder if needed ->\n"
        "     select build_motor.FCMacro -> Execute.\n"
        "  3. You get a 'motor' document: one Part::Feature per part (%s)\n"
        "     extruded %.3f mm, plus the 'Parameters' spreadsheet whose column B\n"
        "     cells carry each parameter name as an ALIAS.\n"
        "\n"
        "FILES\n"
        "  %s   2-D section of every part, DXF R12, millimetres, one LAYER per\n"
        "        part - open it in any CAD if you only need the section.\n"
        "  parameters.csv        the %d primary geometry parameters (same set as\n"
        "                        the Fusion 360 export).\n"
        "  build_motor.FCMacro   the rebuild macro (also embeds the parameters).\n"
        "\n"
        "ROUND-TRIP: edit values in the Parameters sheet, save the document as\n"
        ".FCStd, and upload it in the Geometry tab ('Import FreeCAD').  Values are\n"
        "validated by the same geometry checks as any manual edit; the solids in\n"
        "YOUR file are never read back - the system regenerates its own from the\n"
        "parameters (single master model).\n"
        % (label, reason or "import OCP failed",
           ", ".join(lay for lay, _g in parts[:4]) + (", ..." if len(parts) > 4 else ""),
           length, dxf_name, len(rows)))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(dxf_name, dxf)
        z.writestr("parameters.csv", csv_blob)
        z.writestr("build_motor.FCMacro", macro)
        z.writestr("README.txt", readme)
    return out.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# .FCStd reader (import path)
# ─────────────────────────────────────────────────────────────────────────────

def read_fcstd_params(data: bytes) -> Dict[str, float]:
    """alias → numeric value from every spreadsheet in the document.

    Reads what FreeCAD SAVED — aliases survive edits, added rows, renames of
    the sheet itself.  Non-numeric cells (notes the user typed) are skipped;
    a numeric cell whose alias matches nothing on our side is reported by the
    caller, not silently dropped."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xmls = [n for n in z.namelist() if n == "Document.xml"]
        if not xmls:
            raise ValueError("not a FreeCAD document: Document.xml is missing")
        root = ET.fromstring(z.read("Document.xml"))
    out: Dict[str, float] = {}
    for cell in root.iter("Cell"):
        alias = cell.get("alias")
        if not alias:
            continue
        raw = (cell.get("content") or "").strip()
        # FreeCAD may store '=<expr>' for computed cells; a plain number is the
        # common case.  Units typed into the cell ("7.1 mm") are tolerated.
        m = re.match(r"^=?\s*(-?\d+(?:[.,]\d+)?(?:[eE][+-]?\d+)?)\s*(?:mm|deg)?\s*$",
                     raw)
        if not m:
            continue
        try:
            out[str(alias)] = float(m.group(1).replace(",", "."))
        except ValueError:
            continue
    return out
