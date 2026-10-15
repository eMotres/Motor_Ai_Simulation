"""freecadcmd script of tests/test_cad_simplified.py: synthetic slotted stator / pocketed + keyed rotor / layered coil -> simplified solids -> facts as JSON."""
import json, math, os, sys
sys.path.insert(0, os.environ["FC_CAD_ROOT"])
import FreeCAD as App, Part
from motor_templates.segmented_flatwire import simplified as S
from motor_templates.segmented_flatwire.runstatus import finish

V = App.Vector
RO, RBORE_S, L = 40.0, 25.0, 10.0
# stator: annulus with 12 slots and 12 yoke cut-outs (the detail that must NOT survive)
stator = Part.makeCylinder(RO, L, V(0, 0, -L / 2)).cut(Part.makeCylinder(RBORE_S, L, V(0, 0, -L / 2)))
for i in range(12):
    slot = Part.makeBox(4, 9, L, V(-2, RBORE_S + 1, -L / 2))
    notch = Part.makeBox(2, 3, L, V(-1, RO - 3, -L / 2))
    for s in (slot, notch):
        s.rotate(V(0, 0, 0), V(0, 0, 1), 30 * i)
        stator = stator.cut(s)
# rotor: bore 15 with 2 keys, 8 magnet pockets, magnets inside
RR, RB = 24.0, 15.0
rotor = Part.makeCylinder(RR, L, V(0, 0, -L / 2)).cut(Part.makeCylinder(RB, L, V(0, 0, -L / 2)))
for k in range(2):
    key = Part.makeBox(2, 1.5, L, V(-1, RB - 1.5, -L / 2))
    key.rotate(V(0, 0, 0), V(0, 0, 1), 180 * k)
    rotor = rotor.fuse(key)
mags = []
for i in range(8):
    pocket = Part.makeBox(6, 4, L, V(-3, 17, -L / 2))
    pocket.rotate(V(0, 0, 0), V(0, 0, 1), 45 * i)
    rotor = rotor.cut(pocket)
    m = Part.makeBox(5.6, 3.6, L, V(-2.8, 17.2, -L / 2))
    m.rotate(V(0, 0, 0), V(0, 0, 1), 45 * i)
    mags.append(m)
rotor = rotor.removeSplitter()
# coil: 6 turns of edge-wound flat wire, each a U ribbon in the XZ plane, stacked in y with gaps
n, wh, pitch, ytop = 6, 0.3, 0.37, 38.0
turns = []
for k in range(n):
    y = ytop - wh - k * pitch
    outer = Part.Face(Part.makePolygon([V(-6, y, -8), V(6, y, -8), V(6, y, 9), V(-6, y, 9), V(-6, y, -8)]))
    inner = Part.Face(Part.makePolygon([V(-3, y, -8), V(3, y, -8), V(3, y, 6), V(-3, y, 6), V(-3, y, -8)]))
    turns.append(outer.cut(inner).extrude(V(0, wh, 0)))
coil = turns[0]
for t in turns[1:]:
    coil = coil.fuse(t)

mono, rep = S.build_monolith(stator, rotor, mags, RO, RR)
block = S.coil_block(coil, ytop, wh, pitch, n)
blocks = [block]
bad = S.check(mono, rep, blocks)
# the check must REFUSE the real, un-filled stator (slots, cut-outs) and a coil kept as separate turns
bad_slotted = S.check(stator, dict(rep, stepped=False), blocks)
bad_coil = S.check(mono, rep, [coil])
radii = sorted({round(f.Surface.Radius, 4) for f in mono.Faces if f.Surface.__class__.__name__ == "Cylinder"})
out = dict(bad=bad, bad_slotted=bad_slotted, bad_turns=bad_coil, mono_solids=len(mono.Solids), mono_shells=len(mono.Shells), mono_faces=len(mono.Faces),
           mono_volume=mono.Volume, bore_edges=rep["bore_edges"], radii=radii, face_types=sorted({f.Surface.__class__.__name__ for f in mono.Faces}),
           envelope_volume=math.pi * RO ** 2 * L - rep["bore_area_mm2"] * L, stator_faces=len(stator.Faces), rotor_faces=len(rotor.Faces),
           coil_faces=len(coil.Faces), block_faces=len(block.Faces), block_solids=len(block.Solids), block_volume=block.Volume,
           turns_volume=coil.Volume, block_bbox=[block.BoundBox.YMin, block.BoundBox.YMax],
           mono_bbox=[mono.BoundBox.XMin, mono.BoundBox.XMax, mono.BoundBox.ZMin, mono.BoundBox.ZMax], stepped=rep["stepped"])
json.dump(out, open(os.environ["FC_REPORT"], "w"))
finish(True, "synthetic simplified model built")
