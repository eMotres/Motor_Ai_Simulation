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
    ring = outer.cut(inner).extrude(V(0, wh, 0))
    # the winding steps over to the next turn at the REAR end turn: each turn has only HALF of the rear bar (x < 0 on even turns, x > 0 on odd ones),
    # so ONE turn's plane section is open there (the 2026-10-15 defect) and only the union of the turns closes the end turn
    half = Part.makeBox(3, wh, 1.5, V(-3 if k % 2 == 0 else 0, y, -8))
    turns.append(ring.fuse(half))
coil = turns[0]
for t in turns[1:]:
    coil = coil.fuse(t)

# phase board: FR4 body with an outline notch, a window and 3 holes; copper layers on both sides, pins, a silkscreen-like sliver (all must vanish)
def polygon_prism(points, z0, z1):
    f = Part.Face(Part.makePolygon(points + [points[0]]))
    f.translate(V(0, 0, z0 - 1))
    return f.extrude(V(0, 0, z1 - z0 + 2))


WINDOW = [V(8 * math.cos(2 * math.pi * k / 720), 8 * math.sin(2 * math.pi * k / 720), 0) for k in range(720)]      # a round window drawn as 720 lines
HALF_DISC = [V(12 + 3 * math.cos(math.pi * k / 360), 12 + 3 * math.sin(math.pi * k / 360), 0) for k in range(361)]  # a D-shaped hole: 360 lines + a chord


def pcb_cut(solid, z0, z1):
    for (x, y, r) in ((-12, -12, 1.1), (12, -12, 1.1), (0, 14, 1.1)):
        solid = solid.cut(Part.makeCylinder(r, z1 - z0 + 2, V(x, y, z0 - 1)))
    solid = solid.cut(polygon_prism(WINDOW, z0, z1)).cut(polygon_prism(HALF_DISC, z0, z1))
    return solid.cut(Part.makeBox(10, 6, z1 - z0 + 2, V(-5, 17, z0 - 1)))          # a notch open to the outline
fr4 = pcb_cut(Part.makeBox(40, 40, 1.2, V(-20, -20, 0.2)), 0.2, 1.4)
cu_top = pcb_cut(Part.makeBox(40, 40, 0.2, V(-20, -20, 1.4)), 1.4, 1.6)
cu_bot = pcb_cut(Part.makeBox(40, 40, 0.2, V(-20, -20, 0.0)), 0.0, 0.2)
pin = Part.makeCylinder(1.0, 6.0, V(12, -12, -4))
silk = Part.makeBox(5, 1, 0.02, V(-3, 3, 1.6))
board, brep = S.board_plate(fr4, [fr4, cu_top, cu_bot])      # the pins and the silkscreen are not part of the board thickness
board_bad = S.check_board(board, brep)
board_bad_pin = S.check_board(Part.makeCompound([board, pin]), brep)
board_bad_sphere = S.check_board(Part.makeSphere(5), brep)
board_bad_whole = S.check_board(fr4.fuse(cu_top).fuse(pin).fuse(silk), brep)
mono, rep = S.build_monolith(stator, rotor, mags, RO, RR)
block = S.coil_block(coil, ytop, wh, pitch, n)
# the OLD block: the section of ONE turn extruded over the winding height - its rear end turn is open
one = S._turn_faces(coil, ytop, wh, pitch, [1], sub=1)[0]                       # noqa: SLF001
one.translate(V(0, ytop - wh - (n - 1) * pitch, 0))
open_block = one.extrude(V(0, wh + (n - 1) * pitch, 0))
coil_bad = S.check_coil(block, coil, ytop, wh, pitch, n)
coil_bad_open = S.check_coil(open_block, coil, ytop, wh, pitch, n)
blocks = [block]
bad = S.check(mono, rep, blocks, board, brep, dict(coil=coil, ytop=ytop, wire_h=wh, pitch=pitch, n=n))
# the check must REFUSE the real, un-filled stator (slots, cut-outs) and a coil kept as separate turns
bad_slotted = S.check(stator, dict(rep, stepped=False), blocks)
bad_coil = S.check(mono, rep, [coil])
radii = sorted({round(f.Surface.Radius, 4) for f in mono.Faces if f.Surface.__class__.__name__ == "Cylinder"})
out = dict(bad=bad, bad_slotted=bad_slotted, bad_turns=bad_coil, mono_solids=len(mono.Solids), mono_shells=len(mono.Shells), mono_faces=len(mono.Faces),
           mono_volume=mono.Volume, bore_edges=rep["bore_edges"], radii=radii, face_types=sorted({f.Surface.__class__.__name__ for f in mono.Faces}),
           envelope_volume=math.pi * RO ** 2 * L - rep["bore_area_mm2"] * L, stator_faces=len(stator.Faces), rotor_faces=len(rotor.Faces),
           coil_faces=len(coil.Faces), block_faces=len(block.Faces), block_solids=len(block.Solids), block_volume=block.Volume,
           turns_volume=coil.Volume, block_bbox=[block.BoundBox.YMin, block.BoundBox.YMax],
           mono_bbox=[mono.BoundBox.XMin, mono.BoundBox.XMax, mono.BoundBox.ZMin, mono.BoundBox.ZMax], stepped=rep["stepped"], coil_bad=coil_bad, coil_bad_open=coil_bad_open, block_wires=len(block.slice(V(0, 1, 0), ytop - 1.0)),
           board_bad=board_bad, board_bad_pin=board_bad_pin, board_bad_sphere=board_bad_sphere, board_bad_whole=board_bad_whole,
           board_solids=len(board.Solids), board_shells=len(board.Shells), board_faces=len(board.Faces), board_volume=board.Volume,
           board_types=sorted({f.Surface.__class__.__name__ for f in board.Faces}), board_cyl=sum(1 for f in board.Faces if f.Surface.__class__.__name__ == "Cylinder"), board_faces_in=len(fr4.Faces),
           board_holes=brep["holes"], board_z=brep["z"], board_area=brep["area_mm2"], board_bbox=[board.BoundBox.XLength, board.BoundBox.YLength, board.BoundBox.ZLength],
           board_parts_in=dict(fr4=len(fr4.Faces), cu_top=len(cu_top.Faces)))
json.dump(out, open(os.environ["FC_REPORT"], "w"))
finish(True, "synthetic simplified model built")
