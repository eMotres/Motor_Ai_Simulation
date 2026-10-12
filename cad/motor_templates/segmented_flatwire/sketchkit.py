"""Small helper to build fully constrained, expression-driven Sketcher sketches from Python.

Geometry is created at the CURRENT parameter values (so the solver starts at the solution); every dimensional
constraint is named and bound to an expression of the `Params` spreadsheet, so the owner can edit parameters (or the
constraints) in the FreeCAD GUI and the sketch re-solves like a Fusion sketch.
"""
import math
import FreeCAD as App
import Part
import Sketcher

V = App.Vector
ORIGIN = (-1, 1)      # root point
H_AXIS = -1
V_AXIS = -2


class Chain:
    """Closed loop builder. Segments are added in loop order; joints are coincident ('C') or tangent ('T')."""

    def __init__(self, sk):
        self.sk = sk
        self.segs = []          # dicts: geo, entry_pos, exit_pos
        self.by_geo = {}
        self.joints = []        # joint type between seg i and seg i+1 (last one closes the loop)

    def line(self, p_in, p_out, joint="C", construction=False, rev=False):
        """Line from p_in to p_out in loop order.  rev=True creates the geometry pointing the other way (start at p_out) so
        that two mirrored lines of a loop point the same way and an Angle between them is the included angle."""
        a, b = (p_out, p_in) if rev else (p_in, p_out)
        g = self.sk.addGeometry(Part.LineSegment(V(*a, 0), V(*b, 0)), construction)
        self.segs.append(dict(geo=g, entry=2 if rev else 1, exit=1 if rev else 2))
        self.by_geo[g] = self.segs[-1]
        self.joints.append(joint)
        return g

    def arc(self, center, r, p_in, p_out, ccw, joint="C", construction=False):
        """Arc from p_in to p_out around center; ccw=True if the loop runs counter-clockwise along it."""
        a_in = math.atan2(p_in[1] - center[1], p_in[0] - center[0])
        a_out = math.atan2(p_out[1] - center[1], p_out[0] - center[0])
        a1, a2 = (a_in, a_out) if ccw else (a_out, a_in)
        while a2 <= a1:
            a2 += 2 * math.pi
        circ = Part.Circle(V(center[0], center[1], 0), V(0, 0, 1), r)
        g = self.sk.addGeometry(Part.ArcOfCircle(circ, a1, a2), construction)
        self.segs.append(dict(geo=g, entry=1 if ccw else 2, exit=2 if ccw else 1))
        self.by_geo[g] = self.segs[-1]
        self.joints.append(joint)
        return g

    def close(self):
        n = len(self.segs)
        for i in range(n):
            a, b = self.segs[i], self.segs[(i + 1) % n]
            t = self.joints[i]
            typ = "Tangent" if t == "T" else "Coincident"
            self.sk.addConstraint(Sketcher.Constraint(typ, a["geo"], a["exit"], b["geo"], b["entry"]))


class SK:
    def __init__(self, sk):
        self.s = sk
        self.n = 0

    def mirror_chain(self, chain, right, left):
        """Make the left half of a loop the mirror image (about the sketch Y axis) of the right half with Symmetric
        constraints.  `right` / `left` are {name: geo id} of the same entities in the loop order of `chain` (the left half runs
        the opposite way).  Every inner node between two consecutive right entities gets ONE Symmetric constraint to the
        matching left node (the left entities are joined by the chain's coincident joints); every left arc gets Equal
        radius with its right twin (its two nodes alone leave the radius free).  The two nodes where the halves meet the
        central entities are not tied: they are constrained by the chain's joints and the central entity."""
        names = [n for n in right]                       # loop order of the right half
        for n in names[:-1]:
            r = chain.by_geo[right[n]]
            l = chain.by_geo[left[n]]
            self.con("Symmetric", right[n], r["exit"], left[n], l["entry"], V_AXIS)
        for n in names:
            if isinstance(self.s.Geometry[left[n]], Part.ArcOfCircle):
                self.con("Equal", right[n], left[n])

    def con(self, *args, name=None, expr=None):
        i = self.s.addConstraint(Sketcher.Constraint(*args))
        if name:
            self.s.renameConstraint(i, name)
        if expr:
            if not name:
                raise ValueError("expression needs a named constraint")
            self.s.setExpression("Constraints." + name, expr)
        return i

    def cline(self, p1, p2):
        return self.s.addGeometry(Part.LineSegment(V(*p1, 0), V(*p2, 0)), True)

    def ccircle(self, c, r):
        return self.s.addGeometry(Part.Circle(V(c[0], c[1], 0), V(0, 0, 1), r), True)

    def rect(self, x1, y1, x2, y2):
        """Axis-aligned rectangle; returns geo ids [bottom, right, top, left] with H/V + coincident constraints."""
        p = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
        g = [self.s.addGeometry(Part.LineSegment(V(*p[i], 0), V(*p[(i + 1) % 4], 0)), False) for i in range(4)]
        for i in range(4):
            self.s.addConstraint(Sketcher.Constraint("Coincident", g[i], 2, g[(i + 1) % 4], 1))
        self.s.addConstraint(Sketcher.Constraint("Horizontal", g[0]))
        self.s.addConstraint(Sketcher.Constraint("Horizontal", g[2]))
        self.s.addConstraint(Sketcher.Constraint("Vertical", g[1]))
        self.s.addConstraint(Sketcher.Constraint("Vertical", g[3]))
        return g


def polar(r, ang_deg):
    a = math.radians(ang_deg)
    return (r * math.cos(a), r * math.sin(a))


def check_sketch(sk):
    """Return (ok, info) - solver result and full-constraint state."""
    rc = sk.solve()
    fc = bool(getattr(sk, "FullyConstrained", False))
    return rc == 0 and fc, dict(solve=rc, fully_constrained=fc, malformed=list(getattr(sk, "MalformedConstraints", [])),
                                conflicting=list(getattr(sk, "Conflicting", [])), redundant=list(getattr(sk, "Redundant", [])))
