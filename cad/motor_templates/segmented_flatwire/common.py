"""Shared helpers for the segmented_flatwire template builders."""
import math

P = "Params."


def M(p):
    """Mirror a 2-D point about the sketch Y axis."""
    return (-p[0], p[1])


def pad_symmetric(pad):
    """Make a Pad extrude symmetrically about its sketch plane in both FreeCAD 1.0.x and 1.1.
    1.1 replaced Pad.Midplane by Pad.SideType (a 1.0.2-saved file opened in 1.1 is migrated to
    SideType='Symmetric' correctly, but prints a deprecation warning); 1.0.x has only Midplane."""
    if "SideType" in pad.PropertiesList:
        pad.SideType = "Symmetric"
    else:
        pad.Midplane = True


def origin_feature(body, role):
    for f in body.Origin.OriginFeatures:
        if f.Role == role:
            return f
    raise KeyError(role)


def xy_sketch(body, name, z_expr=None):
    """Sketch on the body XY plane, optionally offset along Z by an expression."""
    sk = body.newObject("Sketcher::SketchObject", name)
    sk.AttachmentSupport = [(origin_feature(body, "XY_Plane"), "")]
    sk.MapMode = "FlatFace"
    if z_expr:
        sk.setExpression(".AttachmentOffset.Base.z", z_expr)
    return sk


def line_offset(p, q, d):
    """Offset segment p->q by d to the right of its direction."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    L = math.hypot(dx, dy)
    nx, ny = dy / L, -dx / L
    return (p[0] + nx * d, p[1] + ny * d), (q[0] + nx * d, q[1] + ny * d)


def intersect(p1, p2, p3, p4):
    """Intersection of the infinite lines p1p2 and p3p4."""
    x1, y1 = p1; x2, y2 = p2; x3, y3 = p3; x4, y4 = p4
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    px = ((x1 * y2 - y1 * x2) * (x3 - x4) - (x1 - x2) * (x3 * y4 - y3 * x4)) / den
    py = ((x1 * y2 - y1 * x2) * (y3 - y4) - (y1 - y2) * (x3 * y4 - y3 * x4)) / den
    return (px, py)


def fillet_center_circle_line(Rc, p, q, r):
    """Centre (at distance Rc from the origin) of a circle of radius r tangent to line p-q, on the left of p->q."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    L = math.hypot(dx, dy)
    nx, ny = -dy / L, dx / L
    ox, oy = p[0] + r * nx, p[1] + r * ny
    ux, uy = dx / L, dy / L
    b = 2 * (ox * ux + oy * uy)
    cc = ox * ox + oy * oy - Rc * Rc
    t = (-b + math.sqrt(b * b - 4 * cc)) / 2
    return (ox + t * ux, oy + t * uy)


def foot_point(c, p, q):
    """Foot of the perpendicular from c onto line p-q."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    L = math.hypot(dx, dy); ux, uy = dx / L, dy / L
    t = (c[0] - p[0]) * ux + (c[1] - p[1]) * uy
    return (p[0] + t * ux, p[1] + t * uy)


def corner_center(p0, p1, p2, r):
    """Centre of a fillet of radius r in corner p1 between rays p1->p0 and p1->p2."""
    a = (p0[0] - p1[0], p0[1] - p1[1]); b = (p2[0] - p1[0], p2[1] - p1[1])
    la, lb = math.hypot(*a), math.hypot(*b)
    a = (a[0] / la, a[1] / la); b = (b[0] / lb, b[1] / lb)
    bis = (a[0] + b[0], a[1] + b[1]); lbis = math.hypot(*bis); bis = (bis[0] / lbis, bis[1] / lbis)
    half = math.acos(max(-1, min(1, a[0] * b[0] + a[1] * b[1]))) / 2
    d = r / math.sin(half)
    return (p1[0] + bis[0] * d, p1[1] + bis[1] * d)


def rotor_numbers(g):
    """Rotor / magnet geometry at the current parameter values (initial sketch positions)."""
    a = g("angle_mag") / 2
    fd = g("magnet_fill_down") * g("angle_mag") / 2
    fu = g("magnet_fill_up") * g("angle_mag") / 2
    h = g("magnet_fill_up") * g("angle_mag") * g("rotor_hole") / 2
    Rup, Rdn = g("rotor_up_r"), g("rotor_down_r")
    Rhub = Rdn - g("rotor_house_height")
    Rtop = Rup - g("magnet_up_gap")
    mfr, off, mdh = g("magnet_fill_radius"), g("offset_rotor"), g("magnet_down_height")
    sa, ca = math.sin(math.radians(a)), math.cos(math.radians(a))
    P4 = (Rdn * math.sin(math.radians(fd)), Rdn * math.cos(math.radians(fd)))
    K = (P4[0] + mdh * sa, P4[1] + mdh * ca)
    U = (Rup * math.sin(math.radians(fu)), Rup * math.cos(math.radians(fu)))
    return dict(a=a, fd=fd, fu=fu, h=h, Rup=Rup, Rdn=Rdn, Rhub=Rhub, Rtop=Rtop, mfr=mfr, off=off, mdh=mdh,
                sa=sa, ca=ca, P4=P4, K=K, U=U)
