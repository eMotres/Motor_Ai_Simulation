"""Build the parametric active-part document of a segmented flat-wire motor from a motor profile JSON.

Tree (FreeCAD 1.0.2):
  Params                (Spreadsheet, aliases = parameter names, row colour = role; edit here like Fusion parameters)
  Stator   (App::Part)  -> Core     (PartDesign Body)
  Winding  (App::Part)  -> Coil     (PartDesign Body, hidden base) + Coils   (Draft link polar array, num_seg*3)
  Rotor    (App::Part)  -> RotorCore(PartDesign Body)
  Magnets  (App::Part)  -> Magnet   (PartDesign Body, hidden base) + MagnetArray (Draft link polar array,
                                                                              num_seg*num_poles_per_segment)
  Mechanics (App::Part) -> Shaft, Stator_holder, Stator_holder_back, Ring (+ Ring_back link), Prop_fix, Screw (hidden
                           base) + Screws (array), Bearing_618_8 (Part: outer / inner ring, Bearing_ball + Balls array)
                           + Bearing_618_8_back (link)                    [only when the profile has mechanics rows]
cad_variant = frameless (profile.cad_variant, frameless.py) omits Mechanics as a whole (housing, bearings, shaft, screws, rings, Prop_fix)
and adds the App::DocumentObjectGroup Mounting_datums; stator, coils, phase board, rotor and magnets are built exactly as in the full model.
Motor-specific metadata (motor id, variant, revision, materials, document name) comes from the profile (N02).
Use prepare_document() + precheck before build_bodies(); call health.gate() before saving/exporting (B01).
"""
import FreeCAD as App
from . import params_sheet as ps
from .core import build_core
from .rotor import build_rotor
from .magnet import build_magnet
from .coil import build_coil
from .sketchkit import check_sketch
from .params_sheet import pval as pval_
from . import mechanics, electronics, frameless


def _z_array(doc, base, count_expr, pitch_expr, name):
    """Draft link ortho array along z (magnet slices): count and pitch from Params."""
    import Draft
    arr = Draft.make_ortho_array(base, v_x=App.Vector(10, 0, 0), v_y=App.Vector(0, 10, 0), v_z=App.Vector(0, 0, 10),
                                 n_x=1, n_y=1, n_z=2, use_link=True)
    arr.Label = name
    arr.setExpression("NumberZ", count_expr)
    arr.setExpression("IntervalZ.z", pitch_expr)
    base.Visibility = False
    return arr


def _polar_array(doc, base, count_expr, name):
    import Draft
    arr = Draft.make_polar_array(base, number=2, angle=360, center=App.Vector(0, 0, 0), use_link=True)
    arr.Label = name
    arr.setExpression("NumberPolar", count_expr)
    base.Visibility = False
    return arr


def prepare_document(pj, doc_name=None):
    """New document with the Params sheet only (cheap) - used for the pre-check before any geometry."""
    prof = pj.get("profile", {})
    doc = App.newDocument(doc_name or prof.get("doc_name", "motor"))
    sh = ps.make_params_sheet(doc, pj)
    doc.recompute()
    try:
        meta = dict(doc.Meta)
        meta.update({"motor_" + k: str(prof.get(k, "")) for k in ("motor_id", "variant", "revision")})
        meta["motor_cad_variant"] = frameless.variant_of_profile(pj)
        doc.Meta = meta
    except Exception:
        pass
    return doc, sh


def build_bodies(doc, sh, pj):
    prof = pj.get("profile", {})
    kit = frameless.variant_of_profile(pj) == "frameless"
    report = dict(param_mismatches=ps.compare_with_json(sh, pj), sketches={}, cad_variant="frameless" if kit else "full")

    def part(name):
        p = doc.addObject("App::Part", name)
        p.Label = name
        return p

    stator, winding, rotor, magnets = part("Stator"), part("Winding"), part("Rotor"), part("Magnets")
    core = doc.addObject("PartDesign::Body", "Core"); stator.addObject(core)
    r_core = build_core(doc, sh, core)
    coil = doc.addObject("PartDesign::Body", "Coil"); winding.addObject(coil)
    r_coil = build_coil(doc, sh, coil)
    rot = doc.addObject("PartDesign::Body", "RotorCore"); rot.Label = "RotorCore"; rotor.addObject(rot)
    r_rot = build_rotor(doc, sh, rot)
    mag = doc.addObject("PartDesign::Body", "Magnet"); magnets.addObject(mag)
    r_mag = build_magnet(doc, sh, mag)
    doc.recompute()
    coils = _polar_array(doc, coil, "Params.coil_count", "Coils"); winding.addObject(coils)
    if ps.has_param(sh, "magnet_slices") and round(pval_(sh, "magnet_slices")) > 1:      # axially sliced magnets (CIANO28 150: 7 x 5 mm)
        stack = _z_array(doc, mag, "Params.magnet_slices", "Params.mag_step", "MagnetStack"); magnets.addObject(stack)
        marr = _polar_array(doc, stack, "Params.magnet_count", "MagnetArray"); magnets.addObject(marr)
    else:
        marr = _polar_array(doc, mag, "Params.magnet_count", "MagnetArray"); magnets.addObject(marr)
    mats = prof.get("materials", {})
    for b in (core, coil, rot, mag):
        if b.Name in mats:
            b.addProperty("App::PropertyString", "MaterialName", "Motor", "material from the motor profile")
            b.MaterialName = mats[b.Name]
    doc.recompute()
    for r in (r_core, r_rot, r_mag):
        for sk in [r["sketch"]] + list(r.get("extra_sketches", [])):
            report["sketches"][sk.Name] = check_sketch(sk)[1]
    for sk in r_coil["sketches"]:
        report["sketches"][sk.Name] = check_sketch(sk)[1]
    if pj.get("named_parameters_mechanics") and not kit:
        rep = mechanics.build_mechanics(doc, sh, _polar_array, prof.get("fusion_parts"))
        for b in mechanics.MECH_BODIES + mechanics.ARRAY_BASES:
            o = doc.getObject(b)
            if o is not None and b in mats:
                o.addProperty("App::PropertyString", "MaterialName", "Motor", "material from the motor profile")
                o.MaterialName = mats[b]
        doc.recompute()
        for part in rep.values():
            for sk in part["sketches"]:
                report["sketches"][sk.Name] = check_sketch(sk)[1]
    if any(p["name"] in ("phase_board_z", "board_outer_r") for _, p in ps.all_params(pj)):
        report["phase_board_import"] = electronics.build_electronics(doc, sh, prof.get("phase_board"))
        doc.recompute()
    if kit:
        report["mounting_datums"] = frameless.build_mounting_datums(doc, sh)
    return report


def build_document(params_path_or_profile, doc_name=None):
    """Compatibility wrapper: profile path or dict -> (doc, report).  No pre-check / gate here."""
    pj = params_path_or_profile if isinstance(params_path_or_profile, dict) else ps.load_params_json(params_path_or_profile)
    doc, sh = prepare_document(pj, doc_name)
    report = build_bodies(doc, sh, pj)
    return doc, report
