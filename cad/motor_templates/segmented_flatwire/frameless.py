"""Frameless assembly variant (template A.6.0, owner decision 2026-10-15).

ONE parametric model, two assemblies.  `cad_variant = full` (default) builds everything.  `cad_variant = frameless` builds the
kit a robot builder integrates into his own frame:

    stator lamination stack + coils + phase board        (Stator, Winding, Electronics)
    rotor lamination stack + magnets                     (Rotor, Magnets)

and OMITS the housing and the rotating mechanics: Stator_holder, Stator_holder_back, Prop_fix, Screws, bearings,
shaft / rotor carrier (`Mechanics` part as a whole).  The omitted parts are not a second hand-made model: the same Params
sheet drives the kept parts, the mechanical rows simply stay in the sheet (the customer may still read them).  The rotor's
inner interface (bore and keys) is built exactly as in the full model, so a customer's hub can be designed to it.

Mounting datums (App::DocumentObjectGroup `Mounting_datums`, every value bound to Params by an expression): axial planes
(stator and rotor end faces, mid plane, coil lead end, phase board faces), the motor axis, and the radial / length facts a
customer needs as properties of the group (stator outer diameter, rotor bore diameter, air gap, key count ...).  They are CAD
reference geometry only: they carry no solids, are hidden by default and are listed in the datums JSON (`frameless` block).

The visible rule of the variant (gate): none of the housing objects may exist in a frameless model.
"""
VARIANTS = ("full", "frameless")
DEFAULT_VARIANT = "full"
DATUMS_GROUP = "Mounting_datums"
AXIS = "Datum_Motor_axis"

#: App::Part / bodies / links that must NOT exist in a frameless model (the housing and the rotating mechanics)
OMITTED_OBJECTS = ("Mechanics", "Shaft", "Shaft_rear", "Stator_holder", "Stator_holder_back", "Prop_fix",
                   "Screw", "Bearing_618_8", "Bearing_618_8_back", "Bearing_outer_ring", "Bearing_inner_ring", "Bearing_ball",
                   "Bearing_seal", "Bearing_seal_2")
OMITTED_LABELS = ("Screws", "Balls")

#: what the frameless kit contains, for the manifest / README / BOM text
KIT_PARTS = ("Stator lamination stack", "Coils (with insulation / enamel)", "Phase board (with pins / lead sleeves)",
             "Rotor lamination stack (bore and keys as built)", "Magnets (and the sleeve, when the rotor has one)")
KIT_OMITS = ("housing / stator holders", "rotor carriers / hub", "bearings", "shaft", "screws", "propeller plate")

#: axial planes: (object name, Params expression of z, description); a row is created only when all its Params exist
_PLANES = (
    ("Datum_Stator_front_face", "Params.motor_length / 2", ("motor_length",), "front end face of the stator stack (+z, propeller side)"),
    ("Datum_Stator_back_face", "-Params.motor_length / 2", ("motor_length",), "back end face of the stator stack"),
    ("Datum_Mid_plane", None, (), "axial centre of the stator and the rotor stack (z = 0)"),
    ("Datum_Rotor_front_face", "Params.rotor_w / 2", ("rotor_w",), "front end face of the rotor stack"),
    ("Datum_Rotor_back_face", "-Params.rotor_w / 2", ("rotor_w",), "back end face of the rotor stack"),
    ("Datum_Coil_lead_end", "-(Params.coil_leg_length / 2 + Params.coil_lead_total)", ("coil_leg_length", "coil_lead_total"),
     "end faces of the coil leads (the phase board sits behind them)"),
    ("Datum_Phase_board_top", "Params.board_top_z", ("board_top_z",), "phase board face toward the motor (plate board)"),
    ("Datum_Phase_board_bottom", "Params.board_bottom_z", ("board_bottom_z",), "phase board face away from the motor (plate board)"),
    ("Datum_Phase_board_origin", "Params.phase_board_z", ("phase_board_z",), "origin of the imported phase board (N-side copper at the lead end + offset)"),
)
#: properties of the datum group: (name, type, Params expression, Params names needed, description)
_PROPS = (
    ("Stator_outer_diameter", "App::PropertyLength", "Params.stator_up_r * 2", ("stator_up_r",), "outer diameter of the stator stack"),
    ("Stator_bore_diameter", "App::PropertyLength", "Params.stator_down_r * 2", ("stator_down_r",), "stator bore (tooth tips)"),
    ("Rotor_outer_diameter", "App::PropertyLength", "Params.rotor_up_r * 2", ("rotor_up_r",), "outer diameter of the rotor stack"),
    ("Rotor_bore_diameter", "App::PropertyLength", "(Params.rotor_down_r - Params.rotor_house_height) * 2",
     ("rotor_down_r", "rotor_house_height"), "bore of the rotor stack (the customer's hub seat); keys excluded"),
    ("Air_gap", "App::PropertyLength", "Params.air_gap", ("air_gap",), "radial air gap"),
    ("Stator_stack_length", "App::PropertyLength", "Params.motor_length", ("motor_length",), "axial length of the stator stack"),
    ("Rotor_stack_length", "App::PropertyLength", "Params.rotor_w", ("rotor_w",), "axial length of the rotor stack"),
    ("Rotor_key_count", "App::PropertyInteger", "Params.rotor_key_count", ("rotor_key_count",), "keys in the rotor bore"),
    ("Rotor_key_width", "App::PropertyLength", "Params.rotor_key_w", ("rotor_key_w",), "width of a rotor key"),
    ("Rotor_key_height", "App::PropertyLength", "Params.rotor_key_h", ("rotor_key_h",), "radial height of a rotor key (toward the axis)"),
)


def variant_of_profile(pj):
    """'full' | 'frameless' of a motor profile dict (profile.cad_variant); anything else is an error in words."""
    v = (pj.get("profile", {}) or {}).get("cad_variant") or DEFAULT_VARIANT
    if v not in VARIANTS:
        raise ValueError("cad_variant = %r is not one of %s" % (v, ", ".join(VARIANTS)))
    return v


def variant_of_doc(doc):
    """The variant a built document was made as (Meta motor_cad_variant); older models have none = full."""
    try:
        v = dict(doc.Meta).get("motor_cad_variant") or DEFAULT_VARIANT
    except Exception:
        v = DEFAULT_VARIANT
    return v if v in VARIANTS else DEFAULT_VARIANT


def is_frameless(doc):
    return variant_of_doc(doc) == "frameless"


def _has(sh, names):
    from .params_sheet import has_param
    return all(has_param(sh, n) for n in names)


def build_mounting_datums(doc, sh):
    """Adds the `Mounting_datums` group: App::Plane per axial datum (z bound to Params), the motor axis (App::Line) and the
    radial / length properties.  Returns a small report (names)."""
    import FreeCAD as App
    grp = doc.addObject("App::DocumentObjectGroup", DATUMS_GROUP)
    grp.Label = DATUMS_GROUP
    planes, props = [], []
    for name, expr, need, text in _PLANES:
        if not _has(sh, need):
            continue
        pl = doc.addObject("App::Plane", name)
        pl.Label = name
        if expr:
            pl.setExpression(".Placement.Base.z", expr)
        grp.addObject(pl)
        planes.append(name)
    ax = doc.addObject("App::Line", AXIS)
    ax.Label = AXIS
    ax.Placement = App.Placement(App.Vector(0, 0, 0), App.Rotation(App.Vector(0, 1, 0), -90))   # App::Line runs along X: turn it onto +Z
    grp.addObject(ax)
    for name, typ, expr, need, text in _PROPS:
        if not _has(sh, need):
            continue
        grp.addProperty(typ, name, "Datums", text)
        grp.setExpression(name, expr)
        props.append(name)
    doc.recompute()
    return dict(group=DATUMS_GROUP, planes=planes, axis=AXIS, properties=props)


def gate(doc, g):
    """Errors of the frameless model: no housing object, the datums exist and sit where the Params say."""
    errors = []
    for n in OMITTED_OBJECTS:
        if doc.getObject(n) is not None:
            errors.append("frameless model contains the housing object %s (the frameless kit has no housing, bearings, shaft or screws)" % n)
    for o in doc.Objects:
        if o.Label in OMITTED_LABELS:
            errors.append("frameless model contains %s (array of housing / bearing parts)" % o.Label)
    grp = doc.getObject(DATUMS_GROUP)
    if grp is None:
        return errors + ["frameless model has no %s group" % DATUMS_GROUP]
    sh = doc.getObject("Params")
    for name, expr, need, text in _PLANES:
        if not _has(sh, need):
            continue
        o = doc.getObject(name)
        if o is None:
            errors.append("datum plane %s missing" % name)
            continue
        want = _eval_plane(expr, g)
        got = o.Placement.Base.z
        if abs(got - want) > 1e-6:
            errors.append("datum %s at z %.6f, Params require %.6f" % (name, got, want))
    ax = doc.getObject(AXIS)
    if ax is None:
        errors.append("datum %s missing" % AXIS)
    else:
        import FreeCAD as App
        d = ax.Placement.Rotation.multVec(App.Vector(1, 0, 0))
        if abs(abs(d.z) - 1.0) > 1e-9:
            errors.append("datum %s is not along the Z axis" % AXIS)
    for name, typ, expr, need, text in _PROPS:
        if not _has(sh, need):
            continue
        if name not in grp.PropertiesList:
            errors.append("datum property %s missing" % name)
            continue
        got = getattr(grp, name)
        got = float(got.Value) if hasattr(got, "Value") else float(got)
        want = _eval_prop(expr, g)
        if abs(got - want) > 1e-6:
            errors.append("datum property %s = %.6f, Params require %.6f" % (name, got, want))
    return errors


def _eval_expr(expr, g):
    import re
    py = re.sub(r"Params\.([A-Za-z_][A-Za-z0-9_]*)", lambda m: repr(float(g(m.group(1)))), expr)
    return eval(py, {"__builtins__": {}}, {})


def _eval_plane(expr, g):
    return 0.0 if expr is None else _eval_expr(expr, g)


def _eval_prop(expr, g):
    return _eval_expr(expr, g)


def datums_block(doc, g):
    """The `frameless` block of the datums JSON: what the kit contains and omits, every mounting datum with its value."""
    sh = doc.getObject("Params")
    planes = {}
    for name, expr, need, text in _PLANES:
        if _has(sh, need):
            planes[name] = dict(z=round(_eval_plane(expr, g), 6), what=text)
    grp = doc.getObject(DATUMS_GROUP)
    props = {}
    for name, typ, expr, need, text in _PROPS:
        if _has(sh, need) and grp is not None and name in grp.PropertiesList:
            props[name] = dict(value=round(_eval_prop(expr, g), 6), what=text)
    return dict(
        what="frameless kit for robots: stator with coils and the phase board + rotor with magnets",
        contains=list(KIT_PARTS), omitted=list(KIT_OMITS),
        frame="+Z = motor axis (+z = propeller / front side), origin at the axial centre of the stator stack",
        mounting=dict(axis=dict(object=AXIS, direction=[0, 0, 1], through=[0, 0, 0]), planes=planes, properties=props),
        note="CAD reference geometry for integrating the kit: the stator is located by its outer diameter and end faces, the rotor "
             "by its bore (keys as built) and end faces; the customer's housing, hub, shaft and bearings are his own")
