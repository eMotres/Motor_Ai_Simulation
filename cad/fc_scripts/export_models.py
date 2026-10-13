"""freecadcmd script (FreeCAD 1.0.2 / 1.1): the 3D-viewer outputs of a built motor model.  Started by motor_ai_sim.cad.build
after the STEP export.

  environment: FC_CAD_ROOT (cad/ dir), FC_MODEL (FCStd), FC_OUT_DIR (directory), FC_MOTOR_ID, FC_REPORT (JSON),
               FC_STATUS, FC_SET (what-if overrides, tests)

Writes into FC_OUT_DIR (each under a temporary name first, moved only after its check passed):
  <motor_id>_full.glb          the FULL model: every part of the active STEP plus the phase board, one coloured node per part
                               (Core, Coils/Coil_nn, Rotor, Magnets/Magnet_nn, Shaft, Bearings_front/back, Holder_front/back,
                               Ring_front/back, Screws/Screw_n, Prop_fix, PhaseBoard*); node extras carry the exploded-view axial factor
  <motor_id>_envelope.step     the SIMPLIFIED ENVELOPE (what a customer without NDA may see and download): the outer contour of the
                               stator (a plain cylinder of the stator outer radius, rings and both holders fused with it, so nothing inside
                               is left on any surface), both holders with their mounting holes / bolt circle, the shaft with its seats
                               and thread section, Prop_fix, the phase board OUTLINE (FR4 outline and its large cut-outs, no copper, no
                               vias) and the three pins.  No core slots, coils, rotor, magnets, bearings, screws, copper routing.
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
MIN_CUTOUT_AREA = 10.0          # mm2: board cut-outs smaller than this (vias, lead holes) are not part of the outline
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


def fused_housing(g, mech, tol_len):
    """Stator outer cylinder + both rings + both holders as ONE solid: no surface of the stator interior remains."""
    L = g("motor_length")
    cyl = Part.makeCylinder(g("stator_up_r"), L, App.Vector(0, 0, -L / 2), App.Vector(0, 0, 1))
    parts = [cyl, mech["Ring_front"], mech["Ring_back"], mech["Stator_holder"], mech["Stator_holder_back"]]
    fused = parts[0].fuse(parts[1:])
    fused = fused.removeSplitter()
    sols = fused.Solids
    if not fused.isValid() or not sols:
        raise RuntimeError("envelope housing fuse is not a valid solid")
    return fused, parts


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
    core = src.getObject("Core").Shape.copy()
    rotor = src.getObject("RotorCore").Shape.copy()
    coils = [s.copy() for s in [o for o in src.Objects if o.Label == "Coils"][0].Shape.Solids]
    mags = [s.copy() for s in [o for o in src.Objects if o.Label == "MagnetArray"][0].Shape.Solids]
    mech = mechanics.solids(src)
    board = electronics.board_shape(src) if electronics.has_board(src) else None
    allbb = [core.BoundBox, rotor.BoundBox] + [s.BoundBox for s in mech.values()] + ([board.BoundBox] if board else [])
    diag = math.sqrt(sum((max(getattr(b, a + "Max") for b in allbb) - min(getattr(b, a + "Min") for b in allbb)) ** 2 for a in "XYZ"))
    tol = min(0.2, max(0.01, 0.0006 * diag))
    asset = dict(motor_id=mid, die=meta.get("motor_die"), config=meta.get("motor_variant"), template=meta.get("motor_template"),
                 tessellation_mm=round(tol, 4))

    # ---------------------------------------------------------------- full model
    full = Collector(tol)
    full.add("Core", "Core", core, view.COLORS["Core"])
    for i, s in enumerate(coils):
        full.add("Coils", "Coil_%02d" % (i + 1), s, view.COLORS["Coil"], check=i < 2)
    full.add("Rotor", "RotorCore", rotor, view.COLORS["RotorCore"])
    for i, s in enumerate(mags):
        full.add("Magnets", "Magnet_%02d" % (i + 1), s, view.COLORS["Magnet"], check=i < 2)
    for name, s in mech.items():
        if name == "Shaft":
            full.add("Shaft", name, s, view.COLORS["Shaft"])
        elif name.startswith("Bearing_618_8_"):
            side = "front" if "_front_" in name else "back"
            col = view.COLORS["Bearing_ball"] if "_ball_" in name else view.COLORS["Bearing_outer_ring"]
            full.add("Bearings_" + side, name, s, col, check="_ball_" not in name)
        elif name == "Stator_holder":
            full.add("Holder_front", name, s, view.COLORS["Stator_holder"])
        elif name == "Stator_holder_back":
            full.add("Holder_back", name, s, view.COLORS["Stator_holder_back"])
        elif name in ("Ring_front", "Ring_back"):
            full.add(name, name, s, view.COLORS["Ring"])
        elif name == "Prop_fix":
            full.add("Prop_fix", name, s, view.COLORS["Prop_fix"])
        elif name.startswith("Screw_"):
            full.add("Screws", name, s, view.COLORS["Screw"], check=False)
    board_info = None
    if board is not None:
        parts, fr4 = classify_board(board)
        for gname, sols in parts.items():
            for i, s in enumerate(sols):
                full.add(gname, "%s_%03d" % (gname, i + 1), s, BOARD_COLOURS[gname], family="PhaseBoard", check=False)
        board_info = {k: len(v) for k, v in parts.items()}
    order = ["Core", "Coils", "Rotor", "Magnets", "Shaft", "Bearings_front", "Bearings_back", "Holder_front", "Holder_back",
             "Ring_front", "Ring_back", "Screws", "Prop_fix", "PhaseBoard_FR4", "PhaseBoard_copper", "PhaseBoard_other", "PhaseBoard_pins"]
    glb_full, st_full = full.glb(order, dict(asset, variant="full"))

    # ---------------------------------------------------------------- simplified envelope
    env_solids = {}
    housing, hparts = fused_housing(g, mech, tol)
    env_solids["Housing"] = housing
    env_solids["Shaft"] = mech["Shaft"]
    env_solids["Prop_fix"] = mech["Prop_fix"]
    if board is not None:
        env_solids["PhaseBoard_outline"] = board_outline(fr4)
        for i, p in enumerate(electronics.pins(board)):
            env_solids["PhaseBoard_pin_%d" % (i + 1)] = p.copy()
    env = Collector(tol)
    for name, s in env_solids.items():
        if name.startswith("PhaseBoard_pin"):
            env.add("PhaseBoard_pins", name, s, BOARD_COLOURS["PhaseBoard_pins"], family="PhaseBoard")
        elif name == "PhaseBoard_outline":
            env.add("PhaseBoard", name, s, BOARD_COLOURS["PhaseBoard_FR4"], family="PhaseBoard")
        else:
            env.add(name, name, s, ENVELOPE_COLOUR if name == "Housing" else view.COLORS[name], family=name)
    env.groups["Housing"].extras["explode"] = 0.0
    env.groups["Shaft"].extras["explode"] = 2.2
    env.groups["Prop_fix"].extras["explode"] = 3.2
    env.groups["PhaseBoard"].extras["explode"] = -2.2
    if "PhaseBoard_pins" in env.groups:
        env.groups["PhaseBoard_pins"].extras["explode"] = -2.2
    glb_env, st_env = env.glb(["Housing", "Shaft", "Prop_fix", "PhaseBoard", "PhaseBoard_pins"], dict(asset, variant="envelope"))

    # ---------------------------------------------------------------- envelope STEP (+ re-import check)
    ex = App.newDocument("envelope")
    root = ex.addObject("App::Part", mid + "_envelope")

    def part(parent, name):
        p = ex.addObject("App::Part", name); p.Label = name; parent.addObject(p); return p

    def solid(parent, name, shape):
        f = ex.addObject("Part::Feature", name); f.Label = name; f.Shape = shape; parent.addObject(f); return f

    solid(part(root, "Stator_outline"), "Housing", env_solids["Housing"])
    solid(part(root, "Mechanics"), "Shaft", env_solids["Shaft"])
    solid(ex.getObject("Mechanics"), "Prop_fix", env_solids["Prop_fix"])
    if board is not None:
        bp = part(root, "Electronics")
        solid(bp, "PhaseBoard_outline", env_solids["PhaseBoard_outline"])
        for n, s in env_solids.items():
            if n.startswith("PhaseBoard_pin"):
                solid(bp, n, s)
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
    env_bb = max((s.BoundBox for s in env_solids.values()), key=lambda b: b.DiagonalLength)
    secret = [n for n in env.groups if n in ("Core", "Coils", "Rotor", "Magnets", "Screws", "Bearings_front", "Bearings_back")]
    ok = step_ok and worst < VOL_TOL and not secret
    rep = dict(motor_id=mid, tessellation_mm=round(tol, 4), full=dict(glb=mid + "_full.glb", bytes=len(glb_full), **st_full),
               envelope=dict(glb=mid + "_envelope.glb", step=mid + "_envelope.step", glb_bytes=len(glb_env),
                             step_bytes=os.path.getsize(tmp_step), step_solids=len(rows), step_volume=round(got, 3),
                             expected_volume=round(expv, 3), solids={k: round(v.Volume, 3) for k, v in env_solids.items()},
                             housing_solids=len(housing.Solids), **st_env),
               board=board_info, worst_mesh_volume_error=round(worst, 5),
               mesh_checks=[dict(node=c[0], solid_volume=c[1], mesh_volume=c[2], rel_error=round(c[3], 5)) for c in full.checks + env.checks],
               freecad_version=".".join(str(x) for x in App.Version()[:3]), ok=ok)
    with open(os.environ["FC_REPORT"], "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1)
    print(json.dumps({k: v for k, v in rep.items() if k != "mesh_checks"}, indent=1))
    App.closeDocument(src.Name)
    if not ok:
        os.remove(tmp_step)
        return finish(False, "3D model check failed: step_ok=%s worst_mesh_volume_error=%.4f envelope_leak=%s" % (step_ok, worst, secret))
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
