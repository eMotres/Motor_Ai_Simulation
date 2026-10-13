"""freecadcmd script (FreeCAD 1.0.2 / 1.1): the 3D-viewer outputs of a built motor model.  Started by motor_ai_sim.cad.build
after the STEP export.

  environment: FC_CAD_ROOT (cad/ dir), FC_MODEL (FCStd), FC_OUT_DIR (directory), FC_MOTOR_ID, FC_REPORT (JSON),
               FC_STATUS, FC_SET (what-if overrides, tests)

Writes into FC_OUT_DIR (each under a temporary name first, moved only after its check passed):
  <motor_id>_full.glb          the FULL model: every part of the active STEP plus the phase board, one coloured node per part
                               (Core, Coils/Coil_nn, Rotor, Magnets/Magnet_nn, Shaft, Bearings_front/back, Holder_front/back,
                               Ring_front/back, Screws/Screw_n, Prop_fix, PhaseBoard*); node extras carry the exploded-view axial factor
  <motor_id>_envelope.step     the ENVELOPE (what a customer without NDA may see and download): the motor as seen from OUTSIDE - the
                               stator with its real outer contour and the coils in its windows, rings, holders (spoke windows, bosses,
                               mounting holes, bolt circle), bearings, screws, shaft with seats and thread, Prop_fix, the phase board
                               outline with its notches, the outer copper layers and the pins.  Left out: rotor core, magnets and pockets
                               (the bore is closed by a plain cylinder at the rotor outer diameter) and the inner copper layers of the board.
  <motor_id>_envelope.glb      the same envelope as GLB
  <motor_id>_models3d.json     evidence: solids, volumes, triangle counts, mesh-vs-solid volume check, sha-free (the manifest holds hashes)

The model is opened, fully recomputed and must pass health.gate() like the STEP export.
"""
import json, math, os, sys, traceback

ROOT = os.environ["FC_CAD_ROOT"]
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "fc_scripts"))
import FreeCAD as App  # noqa: E402
import Import  # noqa: E402
import Part  # noqa: E402
import MeshPart  # noqa: E402
import glb_writer as G  # noqa: E402
from motor_templates.segmented_flatwire import params_sheet as ps, health, mechanics, electronics, view  # noqa: E402
from motor_templates.segmented_flatwire.runstatus import finish  # noqa: E402

# axial factor of the viewer's exploded view (units of the motor length; + = propeller side): physical order along the axis
EXPLODE = {"Core": 0.0, "Coils": -0.35, "Rotor": 0.9, "Magnets": 0.9, "Shaft": 2.2,
           "Ring_back": -0.7, "Holder_back": -1.15, "Bearings_back": -1.55, "PhaseBoard": -2.2,
           "Ring_front": 0.45, "Holder_front": 1.35, "Bearings_front": 1.75, "Screws": 2.6, "Prop_fix": 3.2}
BOARD_COLOURS = {"PhaseBoard_FR4": (0.16, 0.42, 0.24), "PhaseBoard_copper": (0.86, 0.66, 0.22),
                 "PhaseBoard_pins": (0.62, 0.64, 0.66), "PhaseBoard_other": (0.45, 0.47, 0.45)}
ENVELOPE_COLOUR = (0.66, 0.68, 0.72)
MIN_CUTOUT_AREA = 2.0           # mm2: every notch / hole of the board outline counts; vias and inner copper are not in the envelope
VOL_TOL = 0.03                  # mesh volume vs solid volume (relative; tiny parts are limited by the tessellation tolerance)


ANG = 0.3                       # angular deflection (rad) of the viewer meshes


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
    """Groups of one GLB with the mesh-vs-solid volume check."""

    def __init__(self, tol):
        self.tol = tol
        self.groups = {}
        self.checks = []
        self.solids = {}                # node name -> shape (the envelope STEP tree)

    def group(self, name, colour, family=None, explode=None):
        g = self.groups.get(name)
        if g is None:
            g = self.groups[name] = G.Group(name, colour, extras=dict(
                family=family or name, explode=EXPLODE.get(family or name, 0.0) if explode is None else explode))
        return g

    def add(self, gname, node, shape, colour, family=None, check=True):
        g = self.group(gname, colour, family)
        b = budget_of(node)
        patches, vol = tessellate(shape, budgeted_tol(shape, self.tol, b) if b else self.tol)
        g.add(node, patches)
        self.solids[node] = shape
        if check and shape.Volume > 0:
            err = abs(vol - shape.Volume) / shape.Volume
            self.checks.append((node, round(shape.Volume, 4), round(vol, 4), err))
        return patches

    def glb(self, order, asset_extras):
        gs = [self.groups[n] for n in order if n in self.groups]
        return G.build_glb(gs, asset_extras)


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
    return out, fr4


def board_outline(fr4):
    """FR4 outline slab: the outer wire and the cut-outs larger than MIN_CUTOUT_AREA of the biggest planar face, extruded
    over the FR4 thickness.  Small holes (vias, lead pass-throughs) are dropped: the envelope shows the board, not its routing."""
    best = None
    for f in fr4.Faces:
        if f.Surface.__class__.__name__ != "Plane":
            continue
        n = f.normalAt(0, 0)
        if abs(abs(n.z) - 1.0) < 1e-6 and (best is None or f.Area > best.Area):
            best = f
    if best is None:
        raise RuntimeError("phase board FR4 has no planar face")
    ow = best.OuterWire
    face = Part.Face(ow)
    for w in best.Wires:
        if w.isSame(ow):
            continue
        hole = Part.Face(w)
        if hole.Area >= MIN_CUTOUT_AREA:
            face = face.cut(hole)
    bb = fr4.BoundBox
    face.translate(App.Vector(0, 0, bb.ZMin - best.BoundBox.ZMin))
    slab = face.extrude(App.Vector(0, 0, bb.ZLength))
    return slab


def outer_copper(copper, fr4):
    """Copper solids on the outer faces of the board (visible from outside); inner layers are dropped."""
    lo, hi = fr4.BoundBox.ZMin, fr4.BoundBox.ZMax
    return [c for c in copper if c.BoundBox.ZMax <= lo + 0.011 or c.BoundBox.ZMin >= hi - 0.011]


def add_motor(col, envelope, parts, g):
    """The same parts for both models.  Envelope = everything visible from outside: the stator with its real outer contour and
    the coils in its windows, rings, holders, bearings, screws, shaft, Prop_fix, the board outline with notches, outer copper
    and pins; WITHOUT rotor core, magnets, pockets and the inner copper layers; the bore is closed by a plain cylinder at the
    rotor outer diameter."""
    core, rotor, coils, mags, mech, board = (parts[k] for k in ("core", "rotor", "coils", "mags", "mech", "board"))
    col.add("Core", "Core", core, view.COLORS["Core"])
    for i, s in enumerate(coils):
        col.add("Coils", "Coil_%02d" % (i + 1), s, view.COLORS["Coil"], check=i < 2)
    if envelope:
        z0, z1 = rotor.BoundBox.ZMin, rotor.BoundBox.ZMax
        fill = Part.makeCylinder(g("rotor_up_r"), z1 - z0, App.Vector(0, 0, z0), App.Vector(0, 0, 1))
        col.add("RotorFill", "RotorFill", fill, view.COLORS["RotorCore"])
    else:
        col.add("Rotor", "RotorCore", rotor, view.COLORS["RotorCore"])
        for i, s in enumerate(mags):
            col.add("Magnets", "Magnet_%02d" % (i + 1), s, view.COLORS["Magnet"], check=i < 2)
    for name, s in mech.items():
        if name == "Shaft":
            col.add("Shaft", name, s, view.COLORS["Shaft"])
        elif name.startswith("Bearing_618_8_"):
            side = "front" if "_front_" in name else "back"
            c = view.COLORS["Bearing_ball"] if "_ball_" in name else view.COLORS["Bearing_outer_ring"]
            col.add("Bearings_" + side, name, s, c, check="_ball_" not in name)
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
        bp, fr4 = classify_board(board)
        if envelope:
            bp["PhaseBoard_FR4"] = [board_outline(fr4)]
            bp["PhaseBoard_copper"] = outer_copper(bp["PhaseBoard_copper"], fr4)
        for gname, sols in bp.items():
            for i, s in enumerate(sols):
                col.add(gname, "%s_%03d" % (gname, i + 1), s, BOARD_COLOURS[gname], family="PhaseBoard", check=False)
        info = {k: len(v) for k, v in bp.items()}
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
        return finish(False, "health gate failed - NO 3D models written: " + " | ".join(gt["errors"][:8]), gate=gt["errors"])
    g = lambda n: ps.pval(sh, n)
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
                 tessellation_mm=round(tol, 4))
    order = ["Core", "Coils", "Rotor", "RotorFill", "Magnets", "Shaft", "Bearings_front", "Bearings_back", "Holder_front", "Holder_back",
             "Ring_front", "Ring_back", "Screws", "Prop_fix", "PhaseBoard_FR4", "PhaseBoard_copper", "PhaseBoard_other", "PhaseBoard_pins"]

    full = Collector(tol)
    board_info = add_motor(full, False, parts, g)
    glb_full, st_full = full.glb(order, dict(asset, variant="full"))
    env = Collector(tol)
    env_board = add_motor(env, True, parts, g)
    env.groups["RotorFill"].extras["explode"] = EXPLODE["Rotor"]
    glb_env, st_env = env.glb(order, dict(asset, variant="envelope"))
    env_solids = env.solids

    # ---------------------------------------------------------------- envelope STEP (+ re-import check)
    ex = App.newDocument("envelope")
    root = ex.addObject("App::Part", mid + "_envelope")

    def part(parent, name):
        p = ex.addObject("App::Part", name); p.Label = name; parent.addObject(p); return p

    def solid(parent, name, shape):
        f = ex.addObject("Part::Feature", name); f.Label = name; f.Shape = shape; parent.addObject(f); return f

    stator, wind, rotor_p, mech_p, elec = part(root, "Stator"), part(root, "Winding"), part(root, "Rotor"), part(root, "Mechanics"), None
    subs = {}
    for name, shp in env_solids.items():
        if name == "Core":
            solid(stator, name, shp)
        elif name.startswith("Coil_"):
            solid(wind, name, shp)
        elif name == "RotorFill":
            solid(rotor_p, name, shp)
        elif name.startswith("PhaseBoard"):
            elec = elec or part(root, "Electronics")
            solid(elec, name, shp)
        else:
            grp = mechanics.groups([name])[name]
            parent = mech_p
            if grp:
                subs[grp] = subs.get(grp) or part(mech_p, grp)
                parent = subs[grp]
            solid(parent, name, shp)
    ex.recompute()
    os.makedirs(out_dir, exist_ok=True)
    tmp_step = os.path.join(out_dir, mid + "_envelope.partial.step")
    Import.export([root], tmp_step)
    App.closeDocument(ex.Name)
    chk = App.newDocument("check")
    Import.insert(tmp_step, chk.Name)
    rows = [dict(label=o.Label, volume=round(o.Shape.Volume, 4)) for o in chk.Objects
            if o.TypeId == "Part::Feature" and not o.Shape.isNull() and o.Shape.Solids]
    App.closeDocument(chk.Name)
    expv = sum(s.Volume for s in env_solids.values())
    got = sum(r["volume"] for r in rows)
    step_ok = abs(got - expv) < 1e-6 * expv + 1e-3 and len(rows) == len(env_solids)

    # ---------------------------------------------------------------- checks and writes
    worst = max([c[3] for c in full.checks + env.checks] or [0.0])
    hidden = [n for n in env_solids if n == "RotorCore" or n.startswith("Magnet_")]
    inner_cu = True if not board_info else env_board.get("PhaseBoard_copper", 0) < board_info.get("PhaseBoard_copper", 0)
    ok = step_ok and worst < VOL_TOL and not hidden and inner_cu
    rep = dict(motor_id=mid, tessellation_mm=round(tol, 4), full=dict(glb=mid + "_full.glb", bytes=len(glb_full), **st_full),
               envelope=dict(glb=mid + "_envelope.glb", step=mid + "_envelope.step", glb_bytes=len(glb_env),
                             step_bytes=os.path.getsize(tmp_step), step_solids=len(rows), step_volume=round(got, 3),
                             expected_volume=round(expv, 3), solids=len(env_solids), board=env_board, **st_env),
               board=board_info, worst_mesh_volume_error=round(worst, 5),
               mesh_checks=[dict(node=c[0], solid_volume=c[1], mesh_volume=c[2], rel_error=round(c[3], 5)) for c in full.checks + env.checks],
               freecad_version=".".join(str(x) for x in App.Version()[:3]), ok=ok)
    with open(os.environ["FC_REPORT"], "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1)
    print(json.dumps({k: v for k, v in rep.items() if k != "mesh_checks"}, indent=1))
    App.closeDocument(src.Name)
    if not ok:
        os.remove(tmp_step)
        return finish(False, "3D model check failed: step_ok=%s worst_mesh_volume_error=%.4f hidden_part_in_envelope=%s inner_copper_dropped=%s"
                      % (step_ok, worst, hidden, inner_cu))
    os.replace(tmp_step, os.path.join(out_dir, mid + "_envelope.step"))
    for name, data in ((mid + "_full.glb", glb_full), (mid + "_envelope.glb", glb_env)):
        with open(os.path.join(out_dir, name), "wb") as fh:
            fh.write(data)
    return finish(True, "3D models written: full GLB %d bytes, envelope GLB %d bytes, envelope STEP %d solids" % (
        len(glb_full), len(glb_env), len(rows)))


try:
    main()
except Exception:
    traceback.print_exc()
    finish(False, "exception: " + traceback.format_exc().splitlines()[-1])
