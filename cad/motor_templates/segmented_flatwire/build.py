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

ACTIVE_BODIES = ("Core", "Coil", "RotorCore", "Magnet")       # profile.fusion_parts.files may name them (A.7.8)


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
        fp = prof.get("fusion_parts") or {}
        if fp.get("bom_mode"):                                                   # A.7.9: the passive parts are Fusion extras; the BOM reads them (bom_fusion.py)
            meta["motor_bom_mode"] = str(fp["bom_mode"])
            meta["motor_fusion_document"] = "%s v%s" % ((fp.get("document") or {}).get("name", "Fusion"), (fp.get("document") or {}).get("version", "?"))
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
    fp = prof.get("fusion_parts") or {}
    fus = {k: v for k, v in (fp.get("files") or {}).items() if k in ACTIVE_BODIES}     # A.7.8: active parts = the Fusion bodies
    if fus and abs(pval_(sh, "motor_length") - float(fp["ref_motor_length"])) > 1e-9:
        raise RuntimeError("profile.fusion_parts makes %s the Fusion bodies drawn at motor_length %g; this build has motor_length %g. The active "
                           "parts cannot be moved or stretched: export the Fusion design at this length" % (
                               ", ".join(sorted(fus)), float(fp["ref_motor_length"]), pval_(sh, "motor_length")))

    def active(name, parent, builder):
        b = doc.addObject("PartDesign::Body", name); b.Label = name; parent.addObject(b)
        if name in fus:
            from . import fusion_parts as FP
            report.setdefault("fusion_active", {})[name] = FP.import_fusion_part(doc, b, name, fus[name], fp, 0.0, parent)
            return b, dict(sketch=None, sketches=[])
        return b, builder(doc, sh, b)

    core, r_core = active("Core", stator, build_core)
    coil, r_coil = active("Coil", winding, build_coil)
    rot, r_rot = active("RotorCore", rotor, build_rotor)
    mag, r_mag = active("Magnet", magnets, build_magnet)
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
            if sk is not None:
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
        report["phase_board_import"] = electronics.build_electronics(doc, sh, prof.get("phase_board"), fusion=None if kit else prof.get("fusion_parts"))
        doc.recompute()
    if (prof.get("fusion_parts") or {}).get("extras") and not kit:       # A.7.8: Fusion parts the template has no geometry for (fan plates, ...)
        from . import fusion_extras
        report["fusion_extras"] = fusion_extras.build_extras(doc, prof["fusion_parts"])
    if kit:
        report["mounting_datums"] = frameless.build_mounting_datums(doc, sh)
    return report


def build_document(params_path_or_profile, doc_name=None):
    """Compatibility wrapper: profile path or dict -> (doc, report).  No pre-check / gate here."""
    pj = params_path_or_profile if isinstance(params_path_or_profile, dict) else ps.load_params_json(params_path_or_profile)
    doc, sh = prepare_document(pj, doc_name)
    report = build_bodies(doc, sh, pj)
    return doc, report
