"""Ready-to-look-at models: object visibility, colours, an isometric camera fitted to the model (GuiDocument.xml).

A model written by freecadcmd has no GuiDocument.xml (no view providers, no camera); the owner then opens a document
that shows nothing.  Two steps, both done by the build:

  set_visibility(doc)      headless-safe (App only): saved into Document.xml of every build
  style_gui(doc, gdoc)     needs the FreeCAD GUI (run headless with QT_QPA_PLATFORM=offscreen or xvfb): colours, view
                           providers for the Draft arrays, camera; then doc.save() writes GuiDocument.xml
  check_visibility(doc)    the rules as a test (returns a list of violations)

Rules: the bodies Core, Coil, RotorCore, Magnet, their Parts (Stator, Winding, Rotor, Magnets) and the two arrays
(Coils, MagnetArray) are visible; a body shows only its Tip; sketches, intermediate features, Origins with their axes and
planes and the Params sheet are hidden.
"""
import math

BODIES = ("Core", "Coil", "RotorCore", "Magnet")
PARTS = ("Stator", "Winding", "Rotor", "Magnets")
ARRAYS = ("Coils", "MagnetArray")                      # labels of the Draft link arrays
# per-part colours (r, g, b).  The template does not know the magnet polarity (all pockets are identical), so one colour.
COLORS = {"Core": (0.62, 0.64, 0.67), "Coil": (0.80, 0.47, 0.22), "RotorCore": (0.27, 0.29, 0.33),
          "Magnet": (0.20, 0.42, 0.78)}
COLOR_OF_ARRAY = {"Coils": "Coil", "MagnetArray": "Magnet"}
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
        if o.Name in BODIES or o.Name in PARTS or o.Label in ARRAYS or o.Name in tips:
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
        want = o.Name in BODIES or o.Name in PARTS or o.Label in ARRAYS or o.Name in tips
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
    boxes = [doc.getObject(n).Shape.BoundBox for n in ("Core", "RotorCore")]
    boxes += [o.Shape.BoundBox for o in doc.Objects if o.Label in ARRAYS and o.Shape.BoundBox.isValid()]
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


def style_gui(doc, gdoc):
    """Colours + view providers.  Returns a report dict."""
    rep = dict(colours={}, arrays={})
    for o in doc.Objects:
        if o.Label in ARRAYS:
            rep["arrays"][o.Label] = _attach_array_view(o)
    set_visibility(doc)
    owner = {f.Name: n for n in BODIES if doc.getObject(n) is not None for f in doc.getObject(n).Group}
    for o in doc.Objects:
        vo = gdoc.getObject(o.Name)
        if vo is None:
            continue
        if hasattr(o, "Visibility"):
            vo.Visibility = bool(o.Visibility)
        key = COLOR_OF_ARRAY.get(o.Label) or (o.Name if o.Name in BODIES else owner.get(o.Name))
        if key and hasattr(vo, "ShapeColor"):
            vo.ShapeColor = COLORS[key]
            if hasattr(vo, "OverrideMaterial") and o.Label in ARRAYS:
                vo.OverrideMaterial = True
            rep["colours"][o.Name] = key
    return rep
