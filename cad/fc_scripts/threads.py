"""Thread data of the 3D-viewer model (pure Python, no FreeCAD): ISO 261 coarse pitches and the glTF `extras.thread` record.

The CAD model keeps every thread SMOOTH (a plain cylinder: light STEP, owner decision 2026-10-15); the thread size lives as data.
export_models.py splits the threaded cylindrical face of a screw shank / tapped hole / shaft thread into its own glTF node and
puts this record into the node's `extras`; the web viewer (web/src/components/model3d/threadTexture.ts) draws the helical relief
with a procedural normal map, like the cosmetic thread of Fusion.

    {"thread": {"kind": "external" | "internal", "d_mm": 2.0, "pitch_mm": 0.4, "length_mm": 6.0, "standard": "ISO 261",
                "designation": "M2x0.4", "axis": [0, 0, 1], "origin": [x, y, z], "radius_mm": 1.0, "hand": "right"}}

`d_mm` is the nominal (major) diameter, `radius_mm` the radius of the modelled cylinder (the shank, or the tap-drill hole);
`axis` is a unit vector, `origin` a point on the axis where the threaded length starts, `length_mm` runs from there along `axis`.
"""
import math

STANDARD = "ISO 261"

# ISO 261 / ISO 262 coarse series: nominal diameter (mm) -> pitch (mm)
ISO_COARSE = {1.0: 0.25, 1.2: 0.25, 1.4: 0.3, 1.6: 0.35, 2.0: 0.4, 2.5: 0.45, 3.0: 0.5, 4.0: 0.7, 5.0: 0.8, 6.0: 1.0,
              8.0: 1.25, 10.0: 1.5, 12.0: 1.75}
H_FACTOR = 0.8660254037844386        # H = sqrt(3)/2 P (fundamental triangle)
MINOR_FACTOR = 1.0825317547305482    # D1 = d - 5/4 H = d - 1.0825 P (internal thread minor diameter)
MATCH_TOL = 0.08                     # a diameter matches a nominal size within 8 % of it


def coarse_pitch(d):
    """Coarse pitch of the nominal diameter `d` (exact table entry) or None."""
    for k, p in ISO_COARSE.items():
        if abs(k - d) < 1e-9:
            return p
    return None


def nominal_from_major(d):
    """Nearest ISO coarse nominal diameter to a modelled external diameter (the shank is modelled at or a little under the
    nominal size: 2.43 -> M2.5), within MATCH_TOL; None when no size fits."""
    best = min(ISO_COARSE, key=lambda k: abs(k - d))
    return best if abs(best - d) <= MATCH_TOL * best else None


def minor_diameter(d, pitch=None):
    """Internal-thread minor diameter D1 of the nominal size d (coarse pitch unless given)."""
    p = coarse_pitch(d) if pitch is None else pitch
    return d - MINOR_FACTOR * p


def nominal_from_minor(d1):
    """Nominal size whose tapping-hole (minor) diameter is nearest to `d1`: 1.623 -> M2 (D1 1.567); None when none is within
    MATCH_TOL of the nominal size."""
    best = min(ISO_COARSE, key=lambda k: abs(minor_diameter(k) - d1))
    return best if abs(minor_diameter(best) - d1) <= MATCH_TOL * best else None


def designation(d, pitch):
    return "M%gx%g" % (d, pitch)


def thread_for(kind, radius):
    """-> (nominal d, pitch) for a smooth cylinder of `radius` that carries a thread, or None.
    external: the radius is the screw shank / shaft; internal: the radius is the tapping hole."""
    d = 2.0 * radius
    nom = nominal_from_major(d) if kind == "external" else nominal_from_minor(d)
    return None if nom is None else (nom, ISO_COARSE[nom])


def _unit(v):
    n = math.sqrt(sum(c * c for c in v))
    return [c / n for c in v]


def thread_extras(kind, radius, length, axis, origin):
    """The `extras` dict of a thread node, or None when the radius matches no ISO coarse size.  `axis` is normalised and given the
    canonical sign (first non-zero component positive) so the record is deterministic; `origin` is the point of the axis where the
    threaded length starts, running `length` along the (canonical) axis."""
    t = thread_for(kind, radius)
    if t is None:
        return None
    d, p = t
    ax = [c + 0.0 for c in _unit(axis)]
    origin = list(origin)
    for c in ax:                                  # canonical sign: the first non-zero component positive
        if abs(c) > 1e-9:
            if c < 0:                             # flip the axis and start the length at the other end, so origin + t * axis,
                origin = [o + a * length for o, a in zip(origin, ax)]      # t in [0, length], still covers the same face
                ax = [-x for x in ax]
            break
    return {"thread": {"kind": kind, "d_mm": d, "pitch_mm": p, "length_mm": round(length, 4), "standard": STANDARD,
                       "designation": designation(d, p), "radius_mm": round(radius, 5), "axis": [round(x, 6) + 0.0 for x in ax],
                       "origin": [round(x, 5) + 0.0 for x in origin], "hand": "right"}}
