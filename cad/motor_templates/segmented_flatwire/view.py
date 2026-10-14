"""Ready-to-look-at models: object visibility, colours, an isometric camera fitted to the model (GuiDocument.xml).

A model written by freecadcmd has no GuiDocument.xml (no view providers, no camera); the owner then opens a document
that shows nothing.  Two steps, both done by the build:

  set_visibility(doc)      headless-safe (App only): saved into Document.xml of every build
  style_gui(doc, gdoc)     needs the FreeCAD GUI (run headless with QT_QPA_PLATFORM=offscreen or xvfb): colours, view
                           providers for the Draft arrays, camera; then doc.save() writes GuiDocument.xml
  check_visibility(doc)    the rules as a test (returns a list of violations)

Rules: the bodies Core, Coil, RotorCore, Magnet, their Parts (Stator, Winding, Rotor, Magnets) and the two arrays
(Coils, MagnetArray) are visible; a body shows only its Tip; sketches, intermediate features, Origins with their axes and
planes and the Params sheet are hidden.  Mechanics (stage 3): the bodies Shaft, Stator_holder, Stator_holder_back, Ring,
Prop_fix, Bearing_outer_ring, Bearing_inner_ring, the Parts Mechanics / Bearing_618_8, the arrays Balls / Screws and the
links Bearing_618_8_back / Ring_back are visible; the array bases Bearing_ball / Screw are hidden.  Electronics: the Part Electronics and the Part::Feature PhaseBoard (imported STEP, per-face colours from the STEP: mask green,
FR4, ENIG gold, pins grey) are visible.  Colours: steel shaft,
light bearing steel, aluminium holders / Prop_fix, GFRP ring, dark screws.
"""
import math

BODIES = ("Core", "Coil", "RotorCore", "Magnet",
          "Shaft", "Shaft_rear", "Stator_holder", "Stator_holder_back", "Ring", "Prop_fix", "Bearing_outer_ring", "Bearing_inner_ring", "Bearing_seal")
PARTS = ("Stator", "Winding", "Rotor", "Magnets", "Mechanics", "Bearing_618_8", "Electronics")
SHAPES = ("PhaseBoard",)                               # imported reference body (Part::Feature, per-face STEP colours)
ARRAYS = ("Coils", "MagnetArray", "Balls", "Screws")   # labels of the Draft link arrays
LINKS = ("Bearing_618_8_back", "Ring_back", "Bearing_seal_2")            # App::Link copies (Fusion occurrences)
# per-part colours (r, g, b) from THE palette (palette.py: one source for the GUI finish, the GLB exporter and the web viewer).
# The template does not know the magnet polarity per pocket: the FCStd array has one colour (N), the GLB / array elements
# alternate N / S by index (palette.magnet_colour).
from . import palette
COLORS = {n: palette.body_colour(n) for n in palette.BODY_ROLE}
# Fusion threads (shaft M5, back holder M3 / M2.5) are COSMETIC there (modeled = False): here the thread faces carry the host
# colour, a little darker; the geometry is the plain core / tap diameter.
THREAD_COLOURS = {bn: palette.thread_colour(COLORS[bn]) for bn in ("Shaft", "Stator_holder_back")}
COLOR_OF_ARRAY = {"Coils": "Coil", "MagnetArray": "Magnet", "Balls": "Bearing_ball", "Screws": "Screw"}
_ALL_BODIES = BODIES + ("Bearing_ball", "Screw")
# FreeCAD "isometric" view orientation (SbRotation quaternion x, y, z, w)
ISO_Q = (0.424707, 0.17592, 0.339851, 0.820473)


def _is_origin_feature(o):
    return o.TypeId in ("App::Origin", "App::Line", "App::Plane")


def set_visibility(doc):
    tips = set()
    for n in BODIES:
        b = doc.getObject(n)
        if b is not None and getattr(b, "Tip", None) is not None:
            tips.add(b.Tip.Name)
    for o in doc.Objects:
        if not hasattr(o, "Visibility"):
            continue
        if o.Name in BODIES or o.Name in PARTS or o.Label in ARRAYS or o.Name in tips or o.Name in LINKS or o.Name in SHAPES:
            o.Visibility = True
        else:
            o.Visibility = False                       # sketches, intermediate features, origins, axes, planes, Params
    for n in BODIES + PARTS:
        o = doc.getObject(n)
        if o is not None:
            o.Visibility = True


def check_visibility(doc):
    bad = []
    tips = {doc.getObject(n).Tip.Name for n in BODIES if doc.getObject(n) is not None and doc.getObject(n).Tip is not None}
    for o in doc.Objects:
        if not hasattr(o, "Visibility"):
            continue
        want = (o.Name in BODIES or o.Name in PARTS or o.Label in ARRAYS or o.Name in tips or o.Name in LINKS
                or o.Name in SHAPES)
        if bool(o.Visibility) != want:
            bad.append("%s (%s) Visibility=%s, expected %s" % (o.Name, o.TypeId, o.Visibility, want))
    return bad


def _axis_angle(q):
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z)
    return ((x / n, y / n, z / n), 2.0 * math.atan2(n, w)) if n > 1e-12 else ((0, 0, 1), 0.0)


def _rotate(q, v):
    x, y, z, w = q
    vx, vy, vz = v
    # v' = q * v * q^-1
    tx, ty, tz = 2 * (y * vz - z * vy), 2 * (z * vx - x * vz), 2 * (x * vy - y * vx)
    return (vx + w * tx + (y * tz - z * ty), vy + w * ty + (z * tx - x * tz), vz + w * tz + (x * ty - y * tx))


def camera_string(bbox):
    """Inventor ASCII orthographic camera, isometric, framing the bounding box (xmin, ymin, zmin, xmax, ymax, zmax)."""
    cx, cy, cz = [(bbox[i] + bbox[i + 3]) / 2 for i in range(3)]
    diag = math.sqrt(sum((bbox[i + 3] - bbox[i]) ** 2 for i in range(3)))
    dist = diag * 2.0
    px, py, pz = _rotate(ISO_Q, (0, 0, dist))
    (ax, ay, az), ang = _axis_angle(ISO_Q)
    return ("#Inventor V2.1 ascii\n\nOrthographicCamera {\n  viewportMapping ADJUST_CAMERA\n  position %.6f %.6f %.6f\n"
            "  orientation %.6f %.6f %.6f  %.6f\n  nearDistance %.3f\n  farDistance %.3f\n  aspectRatio 1\n"
            "  focalDistance %.3f\n  height %.3f\n\n}\n" % (cx + px, cy + py, cz + pz, ax, ay, az, ang,
                                                            dist - diag, dist + diag, dist, diag * 1.15))


def model_bbox(doc):
    boxes = [doc.getObject(n).Shape.BoundBox for n in BODIES if doc.getObject(n) is not None]
    boxes += [o.Shape.BoundBox for o in doc.Objects if o.Label in ARRAYS and o.Shape.BoundBox.isValid()]
    try:
        from . import mechanics
        boxes += [s.BoundBox for s in mechanics.solids(doc).values()]
    except Exception:
        pass
    try:
        from . import electronics
        if electronics.has_board(doc):
            boxes.append(electronics.board_shape(doc).BoundBox)
    except Exception:
        pass
    return (min(b.XMin for b in boxes), min(b.YMin for b in boxes), min(b.ZMin for b in boxes),
            max(b.XMax for b in boxes), max(b.YMax for b in boxes), max(b.ZMax for b in boxes))


def _attach_array_view(o):
    """Draft arrays made headless have no view provider proxy (they would show nothing in the GUI)."""
    vo = o.ViewObject
    if vo is None or getattr(vo, "Proxy", None) is not None:
        return "kept"
    try:
        from draftviewproviders.view_array import ViewProviderDraftArray
    except Exception:
        from DraftGui import ViewProviderDraftArray  # older layouts
    ViewProviderDraftArray(vo)
    return "attached"


def _face_key(f):
    c = f.CenterOfMass
    return (round(c.x, 3), round(c.y, 3), round(c.z, 3), round(f.Area, 3))


def colour_phase_board(doc, gdoc):
    """Per-face colours of the imported phase board: the STEP is read once more through the GUI importer (which keeps the
    STEP colours), the colour of every face is looked up by centre of mass + area and written as the DiffuseColor list of the
    PhaseBoard view object (saved in GuiDocument.xml, like the colours of the other bodies)."""
    import FreeCAD as App
    import FreeCADGui as Gui
    import ImportGui
    from . import electronics
    f, vo = doc.getObject("PhaseBoard"), gdoc.getObject("PhaseBoard")
    path = electronics.find_step()
    tmp = App.newDocument("_board_colours")
    table = {}
    try:
        ImportGui.insert(path, tmp.Name)
        tg = Gui.getDocument(tmp.Name)
        for o in tmp.Objects:
            v = tg.getObject(o.Name)
            if v is None or not hasattr(o, "Shape") or o.Shape.isNull() or not o.Shape.Faces or not hasattr(v, "DiffuseColor"):
                continue
            s = o.Shape.copy()
            s.Placement = o.getGlobalPlacement()
            dc = list(v.DiffuseColor)
            if len(dc) == 1:
                dc = dc * len(s.Faces)
            alpha = opaque_alpha()           # the STEP's translucent mask colours (alpha 0.83) make Coin3D show the triangulation of
            for face, c in zip(s.Faces, dc):   # large planar faces on / near the copper: the palette colour of the face family, opaque
                table[_face_key(face)] = palette.rgb(palette.pcb_role(c)) + (alpha,)
    finally:
        App.closeDocument(tmp.Name)
    default = palette.rgb("pcb_fr4") + (opaque_alpha(vo),)
    # a few faces differ in the importer's healing (no key match): they take the majority colour of their own solid
    import collections
    missing = 0
    for s in f.Shape.Solids:
        keys = [_face_key(face) for face in s.Faces]
        found = [table[k] for k in keys if k in table]
        fallback = collections.Counter(found).most_common(1)[0][0] if found else default
        for k in keys:
            if k not in table:
                table[k] = fallback
                missing += 1
    cols = [table.get(_face_key(face), default) for face in f.Shape.Faces]
    vo.DiffuseColor = cols
    return dict(faces=len(cols), missing_filled_from_solid=missing, distinct=sorted({tuple(round(x, 2) for x in c[:3]) for c in cols}))


_OPAQUE = []


def opaque_alpha(vo=None):
    """4th component of a DiffuseColor entry that means 'opaque' in the running FreeCAD.  It is an opacity in FreeCAD 1.1
    (1.0 = opaque; 0.0 = fully transparent, the bug that made Shaft / Stator_holder_back invisible) and was a transparency
    in 0.x / 1.0 (0.0 = opaque).  It is read from a fresh view object on a scratch document (never from the model's own
    view object: a model saved with the old bug carries alpha 0.0 there)."""
    if not _OPAQUE:
        import FreeCAD as App
        import FreeCADGui as Gui
        d = App.newDocument("_alpha_probe")
        try:
            d.addObject("Part::Box", "B")
            d.recompute()
            _OPAQUE.append(float(list(Gui.getDocument(d.Name).getObject("B").DiffuseColor)[0][3]))
        except Exception:
            _OPAQUE.append(1.0)
        finally:
            App.closeDocument(d.Name)
    return _OPAQUE[0]


def check_opaque(doc, gdoc):
    """Violations: a visible body / array / part with Transparency > 0 or with a DiffuseColor alpha that differs from the opaque
    default (nothing in this template is transparent on purpose).  Used by gui_finish (refuses) and check_gui."""
    bad = []
    for o in doc.Objects:
        vo = gdoc.getObject(o.Name)
        if vo is None or not getattr(vo, "Visibility", False) or not hasattr(vo, "Transparency"):
            continue
        if vo.Transparency > 0:
            bad.append("%s: Transparency %d (must be opaque)" % (o.Name, vo.Transparency))
        if hasattr(vo, "DiffuseColor") and hasattr(o, "Shape") and not o.Shape.isNull() and len(list(vo.DiffuseColor)) > 1:
            al = {round(float(c[3]), 3) for c in vo.DiffuseColor if len(c) > 3}
            if len(al) > 1 or (al and al != {round(opaque_alpha(), 3)}):
                bad.append("%s: per-face colours with alpha %s (not the opaque value)" % (o.Name, sorted(al)))
    return bad


def thread_face_indices(doc):
    """{body: [face indices]} of the cosmetic thread faces: the M5 section of the shaft, the M3 and M2 tap holes of the back
    holder (cylinders of exactly those radii)."""
    from . import params_sheet as ps
    if doc.getObject("Shaft") is None:             # active-part profile without mechanics
        return {}
    sh = doc.getObject("Params")
    g = lambda n: ps.pval(sh, n)
    want = {"Shaft": [g("shaft_thread_r")], "Stator_holder_back": [g("holder_tap_d") / 2, g("back_screw_tap_d") / 2]}
    out = {}
    for bn, radii in want.items():
        b = doc.getObject(bn)
        if b is None:
            continue
        out[bn] = [i for i, f in enumerate(b.Shape.Faces) if f.Surface.__class__.__name__ == "Cylinder"
                   and any(abs(f.Surface.Radius - r) < 1e-6 for r in radii)]
    return out


def colour_threads(doc, gdoc):
    rep = {}
    for bn, idx in thread_face_indices(doc).items():
        vo = gdoc.getObject(bn)
        n = len(doc.getObject(bn).Shape.Faces)
        alpha = opaque_alpha(vo)                  # NOT 0.0: in FreeCAD 1.1 the 4th component is the opacity
        base = tuple(COLORS[bn]) + (alpha,)
        cols = [base] * n
        for i in idx:
            cols[i] = tuple(THREAD_COLOURS[bn]) + (alpha,)
        vo.DiffuseColor = cols
        rep[bn] = len(idx)
    return rep


def apply_appearance(doc, gdoc):
    """ShapeAppearance (diffuse / specular / shininess) of the bodies whose palette role has its own material, so the GUI reads
    like the web viewer.  Per-face colours (thread bodies) keep their diffuse list."""
    import FreeCAD as App
    done = []
    for bn, role in palette.BODY_ROLE.items():
        if role not in palette.MATERIAL:
            continue
        for name in (bn, {"Screw": "Screws"}.get(bn)):
            o = doc.getObject(name) if name else None
            if o is None:
                for x in doc.Objects:
                    if x.Label == name:
                        o = x
            vo = gdoc.getObject(o.Name) if o is not None else None
            if vo is None or not hasattr(vo, "ShapeAppearance"):
                continue
            sp, sh = palette.specular_shininess(role)
            try:
                n = len(list(vo.DiffuseColor)) if hasattr(vo, "DiffuseColor") else 1
                dc = list(vo.DiffuseColor) if n > 1 else [COLORS[bn] + (opaque_alpha(vo),)]
                vo.ShapeAppearance = [App.Material(DiffuseColor=tuple(c), SpecularColor=sp, Shininess=sh) for c in dc]
                done.append(o.Name)
            except Exception:
                pass
    return done


def colour_magnet_array(doc, gdoc):
    """Alternating N / S colours on the link array elements (view property OverrideColorList of the Draft link array)."""
    for o in doc.Objects:
        if o.Label == "MagnetArray":
            vo = gdoc.getObject(o.Name)
            n = int(getattr(o, "NumberPolar", 0) or 0)
            try:
                vo.OverrideColorList = [palette.magnet_colour(i) + (opaque_alpha(vo),) for i in range(n)]
                return "alternating N/S, %d elements" % n
            except Exception as e:                      # not supported by this FreeCAD / array: single colour (N)
                return "single colour (%s)" % str(e)[:60]
    return "no array"


def style_gui(doc, gdoc):
    """Colours + view providers.  Returns a report dict."""
    rep = dict(colours={}, arrays={})
    for o in doc.Objects:
        if o.Label in ARRAYS:
            rep["arrays"][o.Label] = _attach_array_view(o)
    set_visibility(doc)
    owner = {f.Name: n for n in _ALL_BODIES if doc.getObject(n) is not None for f in doc.getObject(n).Group}
    for o in doc.Objects:
        vo = gdoc.getObject(o.Name)
        if vo is None:
            continue
        if hasattr(o, "Visibility"):
            vo.Visibility = bool(o.Visibility)
        key = COLOR_OF_ARRAY.get(o.Label) or (o.Name if o.Name in _ALL_BODIES else owner.get(o.Name))
        if key and hasattr(vo, "ShapeColor"):
            vo.ShapeColor = COLORS[key]
            if hasattr(vo, "OverrideMaterial") and o.Label in ARRAYS:
                vo.OverrideMaterial = True
            rep["colours"][o.Name] = key
    rep["thread_faces"] = colour_threads(doc, gdoc)
    rep["magnet_colours"] = colour_magnet_array(doc, gdoc)
    rep["appearance"] = apply_appearance(doc, gdoc)
    if doc.getObject("PhaseBoard") is not None:
        rep["phase_board"] = colour_phase_board(doc, gdoc)
    return rep
