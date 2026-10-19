"""freecadcmd script (FreeCAD 1.0.2 / 1.1): the SIMPLIFIED model of a built motor.  Started by motor_ai_sim.cad.build after the
STEP and the viewer model, for EVERY config (full and frameless alike).

  environment: FC_CAD_ROOT (cad/ dir), FC_MODEL (FCStd), FC_OUT_DIR (directory), FC_MOTOR_ID, FC_REPORT (JSON), FC_STATUS, FC_SET

Writes into FC_OUT_DIR (under temporary names first, moved only after every check passed):
  <motor_id>_simplified.step   envelope-only STEP (stamped at download: "SIMPLIFIED - envelope only")
  <motor_id>_simplified.glb    the same for the web tools
  <motor_id>_simplified.json   tiny: envelope, mounting interface, board terminals, solids (the generator adds the mass)

The simplified model (owner 2026-10-15; patent hygiene in motor_templates/segmented_flatwire/simplified.py): stator + rotor + magnets are ONE
monolithic solid (outer envelope, rotor bore with keys, end faces exactly; everything inside filled), every coil is ONE thick turn, every
bearing ONE solid ring d x D x B; shaft / carrier, holders (outer shape, mounting holes), Prop_fix, screws stay as built (the GFRP rings
Ring_front / Ring_back are full-model only and omitted, simplified.OMITTED_MECH); the phase board is ONE plain piece (its outline
extruded to the board thickness, with its through holes: no pins, components, copper, pads; the terminal positions go into the JSON).  The model is opened, recomputed and must pass health.gate(); simplified.check() refuses the files when
internal detail would be left.
"""
import json, math, os, sys, traceback

ROOT = os.environ["FC_CAD_ROOT"]
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "fc_scripts"))
import FreeCAD as App  # noqa: E402
import Import  # noqa: E402
import Part  # noqa: E402
import glb_writer as G  # noqa: E402
from motor_templates.segmented_flatwire import params_sheet as ps, health, mechanics, electronics, view, palette, frameless  # noqa: E402
from motor_templates.segmented_flatwire import simplified as S  # noqa: E402
from motor_templates.segmented_flatwire.runstatus import finish  # noqa: E402

VOL_TOL = 0.03
ANG = 0.3
EXPLODE = {"Monolith": 0.0, "Coils": -0.35, "Shaft": 2.2, "Holder_back": -1.15, "Bearings_back": -1.55,
           "PhaseBoard": -2.2, "Holder_front": 1.35, "Bearings_front": 1.75, "Screws": 2.6, "Prop_fix": 3.2}
BOARD_COLOURS = {"PhaseBoard_FR4": palette.rgb("pcb_mask")}
ORDER = ["Monolith", "Coils", "Shaft", "Shaft_rear", "Bearings_front", "Bearings_back", "Holder_front", "Holder_back",
         "Screws", "Prop_fix", "PhaseBoard_FR4"]


def r6(v):
    return round(float(v), 6)


def tessellate(shape, tol):
    """-> ([G.Mesh per face], mesh volume).  One patch per face: hard edges between faces."""
    import MeshPart
    patches, vol = [], 0.0
    for sol in shape.Solids:
        MeshPart.meshFromShape(Shape=sol, LinearDeflection=tol, AngularDeflection=ANG)
        sv, sp = 0.0, []
        for f in sol.Faces:
            verts, tris = f.tessellate(tol)
            if not tris:
                continue
            v = [(p.x, p.y, p.z) for p in verts]
            sp.append(G.Mesh(v, [tuple(t) for t in tris]))
            sv += G.signed_volume(v, tris)
        if sv < 0:
            for m in sp:
                m.triangles = [(a, c, b) for a, b, c in m.triangles]
            sv = -sv
        patches += sp
        vol += sv
    return patches, vol


def board_parts(doc):
    """The phase board's raw parts: dict(body, pins, thickness_solids) - the plate board (ring plate with its lead slots; the lead sleeves
    are its "pins"), or an imported board (the FR4 body, the pins; mask / copper / walls only count for the thickness) - or None."""
    if not electronics.has_board(doc):
        return None
    bs = electronics.board_shape(doc)
    if electronics.is_plate(doc):
        plate, sleeves = bs.Solids[0], list(bs.Solids[1:])
        return dict(body=plate, pins=sleeves, thickness_solids=[plate])
    pins = electronics.pins(bs, electronics.board_cfg(doc)[0])
    rest = [s for s in bs.Solids if all(not s.isSame(p) for p in pins)]
    return dict(body=max(rest, key=lambda s: s.Volume), pins=list(pins), thickness_solids=rest)


def pick(g, sh, names):
    """{name: value} of the Params rows a profile has."""
    return {n: r6(g(n)) for n in names if ps.has_param(sh, n)}


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
        return finish(False, "health gate failed - NO simplified model written: " + " | ".join(gt["errors"][:8]), gate=gt["errors"])
    g = lambda n: ps.pval(sh, n)
    kit = frameless.is_frameless(src)
    meta = dict(src.Meta or {})
    core, rotor = src.getObject("Core").Shape.copy(), src.getObject("RotorCore").Shape.copy()
    mags = [s.copy() for s in [o for o in src.Objects if o.Label == "MagnetArray"][0].Shape.Solids]
    coil_base = src.getObject("Coil").Shape.copy()
    ncoil = int(round(g("coil_count"))) if ps.has_param(sh, "coil_count") else len([o for o in src.Objects if o.Label == "Coils"][0].Shape.Solids)
    mech = mechanics.solids(src)
    braw = board_parts(src)

    monolith, rep = S.build_monolith(core, rotor, mags, g("stator_up_r"), g("rotor_up_r"))
    ytop = g("stator_up_r") - g("core_thickness") - g("insulation_thickness") - g("wire_spacing_y") / 2
    n_layers = int(round(g("num_wires_per_slot")))
    pitch = g("wire_height") + g("wire_spacing_y")
    block = S.coil_block(coil_base, ytop, g("wire_height"), pitch, n_layers)
    coil_ref = dict(coil=coil_base, ytop=ytop, wire_h=g("wire_height"), pitch=pitch, n=n_layers)       # the REAL coil: the closure / containment test
    coil_blocks = []
    for i in range(ncoil):
        c = block.copy()
        c.rotate(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 360.0 * i / ncoil)
        coil_blocks.append(c)

    parts = {}                                                    # name -> shape, in the order of the STEP tree
    keep = {}
    for name, s in mech.items():
        if name.startswith("Bearing_618_8_"):
            continue                                              # replaced below: one ring per bearing
        if not S.keeps_mech(name):
            continue                                              # GFRP rings: full model only (template A.7.4)
        keep[name] = s
    for side in ("front", "back"):
        outer = mech.get("Bearing_618_8_%s_outer_ring" % side)
        if outer is not None:
            keep["Bearing_%s" % side] = S.bearing_ring(outer, g("bearing_d"))
    board, brep = S.board_plate(braw["body"], braw["thickness_solids"]) if braw else (None, None)
    bad = S.check(monolith, rep, coil_blocks, board, brep, coil_ref)
    for name, s in keep.items():
        if not s.isValid() or len(s.Solids) != 1:
            bad.append("%s is not one valid solid" % name)
    if bad:
        for b in bad:
            print("  SIMPLIFIED CHECK:", b)
        App.closeDocument(src.Name)
        return finish(False, "simplified model refused - internal detail would be left: " + " | ".join(bad[:6]), checks=bad)

    # ---- JSON: envelope, mounting interface, terminals ----------------------------------------------------------
    allsol = [monolith] + coil_blocks + list(keep.values()) + ([board] if board is not None else [])
    bb = [min(getattr(s.BoundBox, a + "Min") for s in allsol) for a in "XYZ"] + [max(getattr(s.BoundBox, a + "Max") for s in allsol) for a in "XYZ"]
    mounting = dict(
        stator=dict(outer_diameter=r6(2 * g("stator_up_r")), z=[r6(core.BoundBox.ZMin), r6(core.BoundBox.ZMax)]),
        rotor=dict(outer_diameter=r6(2 * g("rotor_up_r")), z=[r6(rotor.BoundBox.ZMin), r6(rotor.BoundBox.ZMax)],
                   bore_diameter=r6(2 * (g("rotor_down_r") - g("rotor_house_height"))),
                   keys=pick(g, sh, ("rotor_key_count", "rotor_key_w", "rotor_key_h")),
                   note="the bore (with its keys, when the rotor has them) is the shaft / hub interface"))
    if mech:
        mounting["holders"] = pick(g, sh, ("holder_inner_r", "holder_hub_r", "holder_bolt_r", "holder_bolt_count", "bolt_d", "holder_tap_d",
                                           "holder_face_z", "holder_flange_z", "front_holder_end_z", "back_holder_end_z", "back_holder_outer_r",
                                           "back_screw_r", "back_screw_tap_d", "holder_screw_l"))
        mounting["bearings"] = dict(pick(g, sh, ("bearing_d", "bearing_D", "bearing_B", "bearing_od_r")),
                                    z={k: [r6(s.BoundBox.ZMin), r6(s.BoundBox.ZMax)] for k, s in keep.items() if k.startswith("Bearing_")})
        mounting["shaft"] = pick(g, sh, ("shaft_journal_r", "shaft_seat_r", "shaft_bore_r", "shaft_front_end_z", "carrier_bore_r", "carrier_flange_r"))
        for k in ("Shaft", "Shaft_rear"):
            if k in keep:
                mounting["shaft"][k + "_z"] = [r6(keep[k].BoundBox.ZMin), r6(keep[k].BoundBox.ZMax)]
    boardinfo = None
    if braw:
        bb_ = board.BoundBox
        boardinfo = dict(outline_bbox=[r6(bb_.XMin), r6(bb_.YMin), r6(bb_.ZMin), r6(bb_.XMax), r6(bb_.YMax), r6(bb_.ZMax)],
                         thickness=r6(brep["z"][1] - brep["z"][0]), through_holes=brep["holes"],
                         terminals=[[r6(p.BoundBox.Center.x), r6(p.BoundBox.Center.y), r6(p.BoundBox.ZMax)] for p in braw["pins"]],
                         kind="one plain piece: outline extruded to the board thickness with its through holes (no pins, components, copper, pads)",
                         **pick(g, sh, ("board_outer_r", "board_inner_r", "board_top_z", "board_bottom_z", "phase_board_z")))
    info = dict(schema="motres-cad-simplified/1", motor_id=mid, die=meta.get("motor_die"), config=meta.get("motor_variant"),
                cad_variant="frameless" if kit else "full", kind="SIMPLIFIED - envelope only",
                units="mm", frame="+Z = motor axis (+z = propeller / front side), origin at the axial centre of the stator stack",
                envelope=dict(bbox=[r6(x) for x in bb], size=[r6(bb[3] - bb[0]), r6(bb[4] - bb[1]), r6(bb[5] - bb[2])]),
                mounting=mounting, board=boardinfo,
                solids=dict(monolith=1, coils=len(coil_blocks), mechanics=len(keep), board=1 if board is not None else 0),
                omitted=sorted(n for n in mech if not S.keeps_mech(n)),
                monolith=dict(volume_mm3=r6(monolith.Volume), faces=len(monolith.Faces)), mass=None,
                note="Envelope model for integration: stator, rotor and magnets are one filled solid, every coil is one thick turn, every "
                     "bearing one ring. The coil blocks lie inside the filled slots (the solids overlap by design). It carries no "
                     "electromagnetic construction detail.")

    # ---- STEP ---------------------------------------------------------------------------------------------------
    ex = App.newDocument("export")
    root = ex.addObject("App::Part", mid + "_simplified")

    def part(parent, name):
        p = ex.addObject("App::Part", name)
        p.Label = name
        parent.addObject(p)
        return p

    def solid(parent, name, shape):
        f = ex.addObject("Part::Feature", name)
        f.Label = name
        f.Shape = shape
        parent.addObject(f)
        return f

    solid(part(root, "Stator_rotor"), "Monolith", monolith)
    wg = part(part(root, "Winding"), "Coils")
    for i, s in enumerate(coil_blocks):
        solid(wg, "Coil_%d" % (i + 1), s)
    if board is not None:
        solid(part(root, "Electronics"), "PhaseBoard", board)
    if keep:
        mp = part(root, "Mechanics")
        subs = {}
        for name, grp in mechanics.groups(list(keep)).items():
            parent = mp
            if grp:
                subs.setdefault(grp, part(mp, grp))
                parent = subs[grp]
            solid(parent, name, keep[name])
    ex.recompute()
    step = os.path.join(out_dir, mid + "_simplified.step")
    tmp = os.path.splitext(step)[0] + ".partial.step"
    os.makedirs(out_dir, exist_ok=True)
    Import.export([root], tmp)
    App.closeDocument(ex.Name)
    chk = App.newDocument("check")
    Import.insert(tmp, chk.Name)
    rows = [o for o in chk.Objects if o.TypeId == "Part::Feature" and not o.Shape.isNull() and o.Shape.Solids]
    n_back, vol_back = len(rows), sum(o.Shape.Volume for o in rows)
    App.closeDocument(chk.Name)
    exp_n = 1 + len(coil_blocks) + (1 if board is not None else 0) + len(keep)
    exp_v = monolith.Volume + sum(s.Volume for s in coil_blocks) + (board.Volume if board is not None else 0.0) + sum(s.Volume for s in keep.values())
    step_ok = n_back == exp_n and abs(vol_back - exp_v) < 1e-6 * exp_v + 1e-3

    # ---- GLB ----------------------------------------------------------------------------------------------------
    diag = math.sqrt(sum((bb[i + 3] - bb[i]) ** 2 for i in range(3)))
    tol = min(0.2, max(0.01, 0.0006 * diag))
    groups, checks = {}, []

    def add(gname, node, shape, colour, family=None):
        gr = groups.get(gname)
        if gr is None:
            metallic, roughness = palette.group_material(gname)
            gr = groups[gname] = G.Group(gname, colour, extras=dict(family=family or gname, explode=EXPLODE.get(family or gname, 0.0)),
                                         metallic=metallic, roughness=roughness)
        patches, vol = tessellate(shape, tol)
        gr.add(node, patches)
        if shape.Volume > 0:
            checks.append((node, abs(vol - shape.Volume) / shape.Volume))

    add("Monolith", "Monolith", monolith, view.COLORS["Core"])
    for i, s in enumerate(coil_blocks):
        add("Coils", "Coil_%02d" % (i + 1), s, view.COLORS["Coil"])
    for name, s in keep.items():
        if name in ("Shaft", "Shaft_rear"):
            add(name, name, s, view.COLORS[name])
        elif name.startswith("Bearing_"):
            side = name.split("_")[1]
            add("Bearings_" + side, name, s, view.COLORS["Bearing_outer_ring"], family="Bearings_" + side)
        elif name == "Stator_holder":
            add("Holder_front", name, s, view.COLORS["Stator_holder"])
        elif name == "Stator_holder_back":
            add("Holder_back", name, s, view.COLORS["Stator_holder_back"])
        elif name == "Prop_fix":
            add(name, name, s, view.COLORS["Prop_fix"])
        elif name.startswith("Screw_"):
            add("Screws", name, s, view.COLORS["Screw"])
    if board is not None:
        add("PhaseBoard_FR4", "PhaseBoard", board, BOARD_COLOURS["PhaseBoard_FR4"], family="PhaseBoard")
    asset = dict(motor_id=mid, die=meta.get("motor_die"), config=meta.get("motor_variant"), cad_variant=info["cad_variant"],
                 kind="SIMPLIFIED - envelope only", tessellation_mm=round(tol, 4))
    glb, st = G.build_glb([groups[n] for n in ORDER if n in groups], asset)
    worst = max([c[1] for c in checks] or [0.0])
    App.closeDocument(src.Name)
    ok = step_ok and worst < VOL_TOL
    rep_out = dict(motor_id=mid, ok=ok, step_ok=step_ok, step_solids=n_back, step_solids_expected=exp_n, glb_bytes=len(glb),
                   worst_mesh_volume_error=round(worst, 5), monolith=rep, triangles=st["triangles"], groups=st["groups"],
                   freecad_version=".".join(str(x) for x in App.Version()[:3]))
    with open(os.environ["FC_REPORT"], "w", encoding="utf-8") as fh:
        json.dump(rep_out, fh, indent=1)
    print(json.dumps({k: v for k, v in rep_out.items()}, indent=1))
    if not ok:
        os.path.exists(tmp) and os.remove(tmp)
        return finish(False, "simplified model check failed: step_ok=%s worst_mesh_volume_error=%.4f" % (step_ok, worst))
    os.replace(tmp, step)
    with open(os.path.join(out_dir, mid + "_simplified.glb"), "wb") as fh:
        fh.write(glb)
    with open(os.path.join(out_dir, mid + "_simplified.json"), "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=1)
    return finish(True, "simplified model written: %d solids, %d triangles, monolith %d faces" % (exp_n, st["triangles"], len(monolith.Faces)))


try:
    main()
except Exception:
    traceback.print_exc()
    finish(False, "exception: " + traceback.format_exc().splitlines()[-1])
