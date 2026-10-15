"""freecadcmd script (FreeCAD 1.0.2 / 1.1): the 3D-viewer model of a built motor.  Started by motor_ai_sim.cad.build after the
STEP export.

  environment: FC_CAD_ROOT (cad/ dir), FC_MODEL (FCStd), FC_OUT_DIR (directory), FC_MOTOR_ID, FC_REPORT (JSON),
               FC_STATUS, FC_SET (what-if overrides, tests)

Writes into FC_OUT_DIR (under a temporary name first, moved only after its check passed):
  <motor_id>_full.glb          the model for the web viewer, ONE model for everybody (owner 2026-10-13): every part of the active
                               STEP plus the phase board with its copper, one coloured node per part (Core, Coils/Coil_nn, Rotor,
                               Magnets/Magnet_nn, Shaft, Bearings_front/back, Holder_front/back, Ring_front/back, Screws/Screw_n,
                               Prop_fix, PhaseBoard*); node extras carry the exploded-view axial factor.  Viewing is open to every
                               signed-in user who may see the motor; the STEP download is for NDA accounts and the owner only.
  <motor_id>_models3d.json     evidence: triangle counts and the mesh-vs-solid volume check (the manifest holds the hashes)

The model is opened, fully recomputed and must pass health.gate() like the STEP export; the export refuses if a mesh differs
from its solid by more than 3 % in volume.
"""
import json, math, os, sys, traceback

ROOT = os.environ["FC_CAD_ROOT"]
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "fc_scripts"))
import FreeCAD as App  # noqa: E402
import MeshPart  # noqa: E402
import glb_writer as G  # noqa: E402
from motor_templates.segmented_flatwire import params_sheet as ps, health, mechanics, electronics, view, palette, frameless  # noqa: E402
from motor_templates.segmented_flatwire.runstatus import finish  # noqa: E402

# axial factor of the viewer's exploded view (units of the motor length; + = propeller side): physical order along the axis
EXPLODE = {"Core": 0.0, "Coils": -0.35, "Rotor": 0.9, "Magnets": 0.9, "Shaft": 2.2,
           "Ring_back": -0.7, "Holder_back": -1.15, "Bearings_back": -1.55, "PhaseBoard": -2.2,
           "Ring_front": 0.45, "Holder_front": 1.35, "Bearings_front": 1.75, "Screws": 2.6, "Prop_fix": 3.2}
# board groups use THE palette (palette.py): the board body is the solder-mask green, copper layers + pads gold, pins brass, the
# remaining solids (notch walls) the FR4 edge colour
BOARD_COLOURS = {"PhaseBoard_FR4": palette.rgb("pcb_mask"), "PhaseBoard_copper": palette.rgb("pcb_gold"),
                 "PhaseBoard_pins": palette.rgb("pcb_pins"), "PhaseBoard_other": palette.rgb("pcb_fr4")}
VOL_TOL = 0.03                  # mesh volume vs solid volume (relative; tiny parts are limited by the tessellation tolerance)
ANG = 0.3                       # angular deflection (rad) of the viewer meshes
ORDER = ["Core", "Coils", "Rotor", "Magnets", "Magnets_S", "Shaft", "Shaft_rear", "Bearings_front", "Bearings_front_balls", "Bearings_front_seals",
         "Bearings_back", "Bearings_back_balls", "Bearings_back_seals", "Holder_front", "Holder_back",
         "Ring_front", "Ring_back", "Screws", "Prop_fix", "PhaseBoard_FR4", "PhaseBoard_copper", "PhaseBoard_other", "PhaseBoard_pins"]


def _mesh(sol, tol):
    """Triangulate `sol` with BRepMesh (linear `tol`, angular ANG) and return the facet count.  The triangulation stays on the
    shape's faces, so Face.tessellate(tol) below returns exactly it (its own default angular deflection is far finer)."""
    return MeshPart.meshFromShape(Shape=sol, LinearDeflection=tol, AngularDeflection=ANG).CountFacets


def _count(shape, tol):
    return sum(_mesh(sol, tol) for sol in shape.Solids)


def budgeted_tol(shape, tol, budget):
    """Smallest doubling of `tol` (at most to 3 % of the part's size) that fits the triangle `budget`; stops when the count no
    longer falls.  Keeps tiny round parts (balls, screws, pins, board copper) from outweighing the motor."""
    cap = max(tol, 0.03 * shape.BoundBox.DiagonalLength)
    n = _count(shape, tol)
    while tol < cap and n > budget:
        n2 = _count(shape, tol * 2.0)
        tol *= 2.0
        if n2 >= 0.9 * n:
            break
        n = n2
    return tol


def budget_of(node):
    """Triangle budget per solid (None = the global tolerance decides)."""
    for key, b in (("_ball_", 250), ("_ring", 2500), ("Screw_", 2500), ("PhaseBoard_pin", 1200), ("PhaseBoard_copper", 700),
                   ("PhaseBoard_other", 800), ("PhaseBoard_FR4", 9000), ("Magnet_", 700)):
        if key in node:
            return b
    return None


def tessellate(shape, tol):
    """-> ([G.Mesh per face, ...], mesh volume).  One patch per face: hard edges between faces."""
    patches, vol = [], 0.0
    for sol in shape.Solids:
        _mesh(sol, tol)
        sv = 0.0
        sp = []
        for f in sol.Faces:
            verts, tris = f.tessellate(tol)
            if not tris:
                continue
            v = [(p.x, p.y, p.z) for p in verts]
            sp.append(G.Mesh(v, [tuple(t) for t in tris]))
            sv += G.signed_volume(v, tris)
        if sv < 0:                                      # inward winding: flip this solid
            for m in sp:
                m.triangles = [(a, c, b) for a, b, c in m.triangles]
            sv = -sv
        patches += sp
        vol += sv
    return patches, vol


class Collector:
    """Groups of the GLB with the mesh-vs-solid volume check."""

    def __init__(self, tol):
        self.tol = tol
        self.groups = {}
        self.checks = []

    def add(self, gname, node, shape, colour, family=None, check=True):
        g = self.groups.get(gname)
        if g is None:
            fam = family or gname
            metallic, roughness = palette.group_material(gname)
            g = self.groups[gname] = G.Group(gname, colour, extras=dict(family=fam, explode=EXPLODE.get(fam, 0.0)),
                                             metallic=metallic, roughness=roughness)
        b = budget_of(node)
        patches, vol = tessellate(shape, budgeted_tol(shape, self.tol, b) if b else self.tol)
        g.add(node, patches)
        if check and shape.Volume > 0:
            self.checks.append((node, round(shape.Volume, 4), round(vol, 4), abs(vol - shape.Volume) / shape.Volume))

    def glb(self, asset_extras):
        return G.build_glb([self.groups[n] for n in ORDER if n in self.groups], asset_extras)


def classify_board(shape):
    """PhaseBoard solids -> {group name: [solids]} (FR4, copper layers + pads, pins, other: notch walls / mask)."""
    pins = electronics.pins(shape)
    out = {"PhaseBoard_FR4": [], "PhaseBoard_copper": [], "PhaseBoard_pins": list(pins), "PhaseBoard_other": []}
    rest = [s for s in shape.Solids if all(not s.isSame(p) for p in pins)]
    fr4 = max(rest, key=lambda s: s.Volume)
    for s in rest:
        if s.isSame(fr4):
            out["PhaseBoard_FR4"].append(s)
        elif s.BoundBox.ZLength < 0.25:
            out["PhaseBoard_copper"].append(s)
        else:
            out["PhaseBoard_other"].append(s)
    return out


def add_motor(col, parts):
    core, rotor, coils, mags, mech, board = (parts[k] for k in ("core", "rotor", "coils", "mags", "mech", "board"))
    col.add("Core", "Core", core, view.COLORS["Core"])
    for i, s in enumerate(coils):
        col.add("Coils", "Coil_%02d" % (i + 1), s, view.COLORS["Coil"], check=i < 2)
    col.add("Rotor", "RotorCore", rotor, view.COLORS["RotorCore"])
    for i, s in enumerate(mags):                                  # alternating poles N red / S blue (palette.magnet_colour)
        col.add("Magnets" if i % 2 == 0 else "Magnets_S", "Magnet_%02d" % (i + 1), s, palette.magnet_colour(i),
                family="Magnets", check=i < 2)
    for name, s in mech.items():
        if name == "Shaft":
            col.add("Shaft", name, s, view.COLORS["Shaft"])
        elif name == "Shaft_rear":
            col.add("Shaft_rear", name, s, view.COLORS["Shaft_rear"])
        elif name.startswith("Bearing_618_8_"):
            side = "front" if "_front_" in name else "back"
            ball, seal = "_ball_" in name, "_seal_" in name
            col.add("Bearings_" + side + ("_balls" if ball else "_seals" if seal else ""), name, s,
                    view.COLORS["Bearing_ball"] if ball else view.COLORS["Bearing_seal"] if seal else view.COLORS["Bearing_outer_ring"],
                    family="Bearings_" + side, check=not ball)
        elif name == "Stator_holder":
            col.add("Holder_front", name, s, view.COLORS["Stator_holder"])
        elif name == "Stator_holder_back":
            col.add("Holder_back", name, s, view.COLORS["Stator_holder_back"])
        elif name in ("Ring_front", "Ring_back"):
            col.add(name, name, s, view.COLORS["Ring"])
        elif name == "Prop_fix":
            col.add("Prop_fix", name, s, view.COLORS["Prop_fix"])
        elif name.startswith("Screw_"):
            col.add("Screws", name, s, view.COLORS["Screw"], check=False)
    info = None
    if board is not None:
        info = {}
        for gname, sols in classify_board(board).items():
            info[gname] = len(sols)
            for i, s in enumerate(sols):
                col.add(gname, "%s_%03d" % (gname, i + 1), s, BOARD_COLOURS[gname], family="PhaseBoard", check=False)
    return info


def main():
    model = os.environ["FC_MODEL"]
    out_dir = os.environ["FC_OUT_DIR"]
    mid = os.environ["FC_MOTOR_ID"]
    src = App.openDocument(model)
    for o in src.Objects:
        o.touch()
    src.recompute()
    sh = src.getObject("Params")
    overrides = ps.parse_overrides(os.environ.get("FC_SET", ""))
    for k, v in overrides.items():
        ps.set_param(sh, k, v)
    if overrides:
        src.recompute()
    gt = health.gate(src)
    if not gt["ok"]:
        App.closeDocument(src.Name)
        return finish(False, "health gate failed - NO 3D model written: " + " | ".join(gt["errors"][:8]), gate=gt["errors"])
    meta = dict(src.Meta or {})
    parts = dict(
        core=src.getObject("Core").Shape.copy(), rotor=src.getObject("RotorCore").Shape.copy(),
        coils=[s.copy() for s in [o for o in src.Objects if o.Label == "Coils"][0].Shape.Solids],
        mags=[s.copy() for s in [o for o in src.Objects if o.Label == "MagnetArray"][0].Shape.Solids],
        mech=mechanics.solids(src), board=electronics.board_shape(src) if electronics.has_board(src) else None)
    allbb = [parts["core"].BoundBox, parts["rotor"].BoundBox] + [s.BoundBox for s in parts["mech"].values()] \
        + ([parts["board"].BoundBox] if parts["board"] else [])
    diag = math.sqrt(sum((max(getattr(b, a + "Max") for b in allbb) - min(getattr(b, a + "Min") for b in allbb)) ** 2 for a in "XYZ"))
    tol = min(0.2, max(0.01, 0.0006 * diag))
    asset = dict(motor_id=mid, die=meta.get("motor_die"), config=meta.get("motor_variant"), template=meta.get("motor_template"),
                 cad_variant="frameless" if frameless.is_frameless(src) else "full", tessellation_mm=round(tol, 4))
    col = Collector(tol)
    board_info = add_motor(col, parts)
    glb, st = col.glb(asset)
    plate_board = electronics.has_board(src) and electronics.is_plate(src)
    App.closeDocument(src.Name)

    worst = max([c[3] for c in col.checks] or [0.0])
    ok = worst < VOL_TOL and (board_info is None or board_info.get("PhaseBoard_copper", 0) > 0          # the copper board is in the model
                              or (plate_board and board_info.get("PhaseBoard_pins", 0) > 0))               # (CIANO28 85: plate + lead sleeves)
    rep = dict(motor_id=mid, tessellation_mm=round(tol, 4), glb=mid + "_full.glb", bytes=len(glb), board=board_info,
               worst_mesh_volume_error=round(worst, 5), freecad_version=".".join(str(x) for x in App.Version()[:3]), ok=ok, **st,
               mesh_checks=[dict(node=c[0], solid_volume=c[1], mesh_volume=c[2], rel_error=round(c[3], 5)) for c in col.checks])
    os.makedirs(out_dir, exist_ok=True)
    with open(os.environ["FC_REPORT"], "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1)
    print(json.dumps({k: v for k, v in rep.items() if k != "mesh_checks"}, indent=1))
    if not ok:
        return finish(False, "3D model check failed: worst_mesh_volume_error=%.4f board=%s" % (worst, board_info))
    with open(os.path.join(out_dir, mid + "_full.glb"), "wb") as fh:
        fh.write(glb)
    return finish(True, "3D model written: %d bytes, %d triangles" % (len(glb), st["triangles"]))


try:
    main()
except Exception:
    traceback.print_exc()
    finish(False, "exception: " + traceback.format_exc().splitlines()[-1])
