# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Stored geometry values: the pre-2026-10-30 definitions -> Fusion's.

Until 2026-10-30 ``cadquery_geometry`` measured three keys differently from
Fusion 360 (and the FreeCAD template that reproduces it), so the same numbers
drew a different outline (owner: «почему у нас геометрия с Fusion не
совпадает»).  The builder now uses Fusion's construction
(``cadquery_geometry._stator_leg_outer_x`` / ``_magnet_side_local``); every value
STORED under the old definition has to be translated, or the machine it
describes changes shape.  This module is that translation, for the migration
script (``scripts/migrate_fusion_definitions.py``) and the tests.

========================  ==========================================  ==========================================
key                       old builder (until 2026-10-30)              Fusion (now)
========================  ==========================================  ==========================================
tooth2_width              leg outer x = tooth/2 + 2·ins + column      leg outer x = tooth/2 + coil_w + tooth2,
                          + 2·dx + tooth2 (one dx more)               coil_w = column + 2·ins + dx
magnet_fill_down          knee on the RADIAL line of the bottom       knee = bottom corner + down_height along
(+ magnet_down_height)    corner (r0 + down_height, same angle)       the HALF-POLE line (angle pitch/2)
magnet_fill_up            TOP CORNER at fill_up·pitch/2 on the top    upper side aims at the rotor OD point at
                          arc r = OD − up_gap                         fill_up·pitch/2, corner = where it meets
                                                                      the top arc
rotor_hole                lip half-angle fill_up·rotor_hole·pitch/2   same formula — but it multiplies fill_up,
                                                                      so its VALUE follows the fill_up change
========================  ==========================================  ==========================================

``tooth2_width`` translates exactly (+ wire_spacing_x).  The magnet does not
always: the old knee sat on the radial line, the new one on a line parallel to
the half-pole line, a shape difference of ≈ down_height·(pitch/2 −
fill_down·pitch/2) in the foot.  ``fill_down`` is chosen for the least
symmetric-difference area against the old magnet; ``fill_up`` then puts the top
corner exactly where it was, and ``rotor_hole`` keeps the opening's lips.  With
magnet_down_height = 0 (or fill_down = 1) the translation is exact; otherwise
the residual is reported (max deviation and magnet area change).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

Pt = Tuple[float, float]

#: the stored keys whose VALUE the translation may change
TRANSLATED_KEYS = ("tooth2_width", "magnet_fill_down", "magnet_fill_up", "rotor_hole")

#: every key the translation READS — a configuration that overrides one of them
#: describes a different outline than its die and is translated on its own
INPUT_KEYS = TRANSLATED_KEYS + (
    "wire_spacing_x", "magnet_down_height", "magnet_up_gap", "magnet_height",
    "rotor_house_height", "stator_diameter", "core_thickness", "slot_height",
    "air_gap", "num_seg", "num_poles_per_segment", "num_poles")

#: below this the outline is "the same" (well under the builder's weld tolerance
#: of diameter/4000 on the smallest machine, Ø12 → 3 µm)
SAME_OUTLINE_MM = 1e-6


def _num(g: Dict[str, Any], k: str, default: Optional[float] = None) -> Optional[float]:
    v = g.get(k, default)
    if v is None or isinstance(v, bool):
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def rotor_frame(g: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """The radii and pitch the magnet is built in, from the stored primaries
    (the derived copies a file may carry are not trusted).  None if unusable."""
    od, ct, sh = _num(g, "stator_diameter"), _num(g, "core_thickness"), _num(g, "slot_height")
    gap, mh, rh = _num(g, "air_gap"), _num(g, "magnet_height"), _num(g, "rotor_house_height")
    seg, pps = _num(g, "num_seg"), _num(g, "num_poles_per_segment")
    n = seg * pps if (seg and pps) else _num(g, "num_poles")
    if None in (od, ct, sh, gap, mh, rh) or not n or n < 2:
        return None
    r_or = od / 2.0 - ct - sh - gap
    r0 = r_or - mh                          # rotor_inner_radius + rotor_house_height
    return {"rotor_or": r_or, "magnet_r": r0, "num_poles": int(round(n)),
            "pitch": 2.0 * math.pi / int(round(n))}


def legacy_magnet_side(rotor_or: float, magnet_r: float, num_poles: int, fill_down: float,
                       fill_up: float, down_h: float, up_gap: float) -> Tuple[Pt, Pt, Pt]:
    """The OLD construction, frozen (cadquery_geometry before 2026-10-30): the +x
    side bottom corner, knee and top corner in the pole frame."""
    pitch = 2.0 * math.pi / num_poles
    a_dn, a_up = pitch * fill_down / 2.0, pitch * fill_up / 2.0
    r_top = rotor_or - up_gap
    return ((magnet_r * math.sin(a_dn), magnet_r * math.cos(a_dn)),
            ((magnet_r + down_h) * math.sin(a_dn), (magnet_r + down_h) * math.cos(a_dn)),
            (r_top * math.sin(a_up), r_top * math.cos(a_up)))


def fusion_magnet_side(rotor_or: float, magnet_r: float, num_poles: int, fill_down: float,
                       fill_up: float, down_h: float, up_gap: float) -> Tuple[Pt, Pt, Pt]:
    """The builder's construction (Fusion's); see cadquery_geometry._magnet_side_local."""
    from motor_ai_sim.cadquery_geometry import _magnet_side_local
    return _magnet_side_local(rotor_or, magnet_r, num_poles, fill_down, fill_up, down_h, up_gap)


def _ray_to_circle(a: Pt, b: Pt, r: float) -> Optional[Pt]:
    """a + t·(b − a), t > 0, on the circle r (|a| < r)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    aa = dx * dx + dy * dy
    if aa <= 0.0:
        return None
    bb = 2.0 * (a[0] * dx + a[1] * dy)
    cc = a[0] * a[0] + a[1] * a[1] - r * r
    disc = bb * bb - 4.0 * aa * cc
    if disc < 0.0:
        return None
    t = (-bb + math.sqrt(disc)) / (2.0 * aa)
    return (a[0] + t * dx, a[1] + t * dy)


def _dist(p: Pt, q: Pt) -> float:
    return math.hypot(p[0] - q[0], p[1] - q[1])


def _seg_dist(p: Pt, a: Pt, b: Pt) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 <= 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def half_magnet_polygon(s, n_arc: int = 64):
    """The sharp half magnet (axis → bottom chord → foot → upper side → top
    arc back to the axis) as a shapely Polygon, from ``(bottom, knee, top)``."""
    from shapely.geometry import Polygon
    p4, k, c = s
    r = math.hypot(*c)
    a_c = math.atan2(c[0], c[1])
    arc = [(r * math.sin(a_c * (1.0 - i / n_arc)), r * math.cos(a_c * (1.0 - i / n_arc)))
           for i in range(1, n_arc + 1)]
    return Polygon([(0.0, p4[1]), p4, k, c] + arc)


def side_deviation(s1, s2) -> float:
    """Hausdorff distance [mm] between two sharp magnet outlines given by their
    +x side ``(bottom corner, knee, top corner)`` — the half outline bottom
    chord → foot → upper side → top arc to the axis (the −x half is its mirror
    image).  The corner fillets are laid on the same corners afterwards, so
    this bounds the filleted outline too."""
    def half(s):
        p4, k, c = s
        r_top = math.hypot(*c)
        segs = [((0.0, p4[1]), p4), (p4, k), (k, c)]
        return segs, r_top, math.atan2(c[0], c[1])

    def samples(s, n=64):
        segs, r_top, a_c = half(s)
        out = []
        for a, b in segs:
            out += [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(n + 1)]
        out += [(r_top * math.sin(a_c * i / n), r_top * math.cos(a_c * i / n)) for i in range(n + 1)]
        return out

    def dist_to(p, s):
        segs, r_top, a_c = half(s)
        d = min(_seg_dist(p, a, b) for a, b in segs)
        ang = math.atan2(p[0], p[1])
        if 0.0 <= ang <= a_c:
            d = min(d, abs(math.hypot(*p) - r_top))
        else:
            d = min(d, _dist(p, s[2]), _dist(p, (0.0, r_top)))
        return d

    return max(max(dist_to(p, s2) for p in samples(s1)),
               max(dist_to(p, s1) for p in samples(s2)))


def translate_magnet(g: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """``{"fill_down", "fill_up", "rotor_hole", "dev_mm", "dev_untranslated_mm",
    "area_change_pct", "problem"}`` for the stored (old-definition) magnet of
    ``g``; None if ``g`` has no usable magnet."""
    fr = rotor_frame(g)
    fd, fu = _num(g, "magnet_fill_down"), _num(g, "magnet_fill_up")
    if fr is None or fd is None or fu is None:
        return None
    dh = _num(g, "magnet_down_height", 0.0) or 0.0
    ug = _num(g, "magnet_up_gap", 0.0) or 0.0
    hole = _num(g, "rotor_hole")
    R, r0, n, pitch = fr["rotor_or"], fr["magnet_r"], fr["num_poles"], fr["pitch"]
    half = pitch / 2.0
    old = legacy_magnet_side(R, r0, n, fd, fu, dh, ug)
    out: Dict[str, Any] = {"fill_down": fd, "fill_up": fu, "rotor_hole": hole,
                           "dev_mm": 0.0, "dev_untranslated_mm": None,
                           "area_change_pct": 0.0, "problem": None}
    try:
        out["dev_untranslated_mm"] = side_deviation(old, fusion_magnet_side(R, r0, n, fd, fu, dh, ug))
    except ValueError as e:
        out["dev_untranslated_mm"] = float("nan")
        out["problem"] = "the stored values do not build in the new construction: %s" % e
    r_top = R - ug
    if math.hypot(*old[1]) >= r_top or not (0.0 < r0 < r_top):
        out["problem"] = ("old knee r = %.4f mm is not below the magnet top r = %.4f mm: "
                          "translate by hand" % (math.hypot(*old[1]), r_top))
        return out
    c_old = old[2]

    def build(fd_new: float):
        a = pitch * fd_new / 2.0
        p4 = (r0 * math.sin(a), r0 * math.cos(a))
        k = (p4[0] + dh * math.sin(half), p4[1] + dh * math.cos(half))
        if math.hypot(*k) >= r_top:
            return None
        u = _ray_to_circle(k, c_old, R) if ug > 0 else c_old
        if u is None:
            return None
        return p4, k, u

    old_half = half_magnet_polygon(old)

    def cost(fd_new: float) -> float:
        b = build(fd_new)
        if b is None:
            return 1e9
        return old_half.symmetric_difference(half_magnet_polygon(b)).area

    # least symmetric-difference area over fd (unimodal near fd); the top
    # corner, the upper side's direction at it and the lips stay where they were
    lo, hi = max(1e-6, fd - 0.2), min(1.0, fd + 0.2)          # a fraction (validator: 0..1)
    for _ in range(100):
        m1, m2 = lo + (hi - lo) / 3.0, hi - (hi - lo) / 3.0
        if cost(m1) <= cost(m2):
            hi = m2
        else:
            lo = m1
    fd_new = 0.5 * (lo + hi) if dh > 0.0 else fd
    b = build(fd_new)
    if b is None:
        out["problem"] = "no fill_down puts the knee below the magnet top: translate by hand"
        return out
    p4, k, u = b
    fu_new = 2.0 * math.atan2(u[0], u[1]) / pitch
    new = fusion_magnet_side(R, r0, n, fd_new, fu_new, dh, ug)
    nh = half_magnet_polygon(new)
    out.update(fill_down=fd_new, fill_up=fu_new, dev_mm=side_deviation(old, new),
               area_change_pct=100.0 * (nh.area - old_half.area) / old_half.area)
    if hole is not None and hole < 1.0 - 1e-9:
        h_new = fu * hole / fu_new
        if h_new >= 1.0 - 1e-9:
            out["problem"] = ("rotor_hole %.6g would become %.6g >= 1, which switches the "
                              "pocket to straight sides: translate by hand" % (hole, h_new))
        out["rotor_hole"] = h_new
    return out


def translate_geometry(g: Dict[str, Any]) -> Dict[str, Any]:
    """The Fusion-definition values of a stored (old-definition) geometry.

    ``{"values": {key: new}, "dev_mm": {"stator_leg": x, "magnet": y},
    "dev_untranslated_mm": {...}, "area_change_pct": {"magnet": z},
    "problems": [...]}`` — ``values`` holds only
    the keys of ``TRANSLATED_KEYS`` that ``g`` carries (rounded to 1e-9);
    ``dev_mm`` is how far the new outline lies from the old one after the
    translation, ``dev_untranslated_mm`` how far it would lie if the stored
    values were read in the new definition unchanged."""
    vals: Dict[str, float] = {}
    dev: Dict[str, float] = {}
    dev0: Dict[str, float] = {}
    area: Dict[str, float] = {}
    problems: List[str] = []
    t2, dx = _num(g, "tooth2_width"), _num(g, "wire_spacing_x")
    if t2 is not None and dx is not None:
        vals["tooth2_width"] = t2 + dx
        dev["stator_leg"] = 0.0
        dev0["stator_leg"] = abs(dx)
    m = translate_magnet(g)
    if m is not None:
        if m["problem"]:
            problems.append(m["problem"])
        dev["magnet"] = m["dev_mm"]
        dev0["magnet"] = m["dev_untranslated_mm"]
        area["magnet"] = m["area_change_pct"]
        vals["magnet_fill_down"] = m["fill_down"]
        vals["magnet_fill_up"] = m["fill_up"]
        if m["rotor_hole"] is not None:
            vals["rotor_hole"] = m["rotor_hole"]
    vals = {k: round(float(v), 9) for k, v in vals.items() if k in g}
    return {"values": vals, "dev_mm": dev, "dev_untranslated_mm": dev0,
            "area_change_pct": area, "problems": problems}


def _lip_half_width(g: Dict[str, Any], fr: Dict[str, float]) -> Optional[float]:
    """Half-width [mm] of the rotor_hole < 1 opening, x = OD·sin(fill_up·hole·pitch/2)
    — the same formula in both definitions; None for a straight-sided pocket."""
    fu, hole = _num(g, "magnet_fill_up"), _num(g, "rotor_hole")
    if fu is None or hole is None or hole >= 1.0 - 1e-9:
        return None
    return fr["rotor_or"] * math.sin(fr["pitch"] * fu * hole / 2.0)


def _leg_x_old(g: Dict[str, Any]) -> Optional[float]:
    vals = [_num(g, k) for k in ("tooth_width", "insulation_thickness", "wire_width",
                                 "wire_spacing_x", "tooth2_width")]
    if None in vals:
        return None
    tw, ins, ww, dx, t2 = vals
    n = int(round(_num(g, "wire_split", 1.0) or 1.0))
    col = ww if n <= 1 else n * ww + (n - 1) * 2.0 * dx       # winding.STRIP_GAP_FACTOR = 2
    return tw / 2.0 + 2.0 * ins + col + 2.0 * dx + t2


def _leg_x_new(g: Dict[str, Any]) -> Optional[float]:
    x = _leg_x_old(g)
    return None if x is None else x - float(_num(g, "wire_spacing_x"))


def outline_change(old_geo: Dict[str, Any], new_geo: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """How far the outline the NEW builder draws from ``new_geo`` lies from the
    one the OLD builder drew from ``old_geo`` [mm]: ``stator_leg`` (the half
    leg's outer side), ``magnet`` (Hausdorff of the sharp magnet),
    ``magnet_area_pct`` and ``lip`` (the opening's half-width).  None where a
    part is missing.  Use it for "before" (``new_geo = old_geo``: deploy without
    the migration) and "after" (the translated or Fusion-pinned values)."""
    out: Dict[str, Optional[float]] = {"stator_leg": None, "magnet": None,
                                       "magnet_area_pct": None, "lip": None}
    xo, xn = _leg_x_old(old_geo), _leg_x_new(new_geo)
    if xo is not None and xn is not None:
        out["stator_leg"] = abs(xn - xo)
    fo, fn = rotor_frame(old_geo), rotor_frame(new_geo)
    if fo is None or fn is None:
        return out

    def side(g, fr, fn_side):
        return fn_side(fr["rotor_or"], fr["magnet_r"], fr["num_poles"],
                       float(_num(g, "magnet_fill_down")), float(_num(g, "magnet_fill_up")),
                       float(_num(g, "magnet_down_height", 0.0) or 0.0),
                       float(_num(g, "magnet_up_gap", 0.0) or 0.0))
    try:
        so = side(old_geo, fo, legacy_magnet_side)
        sn = side(new_geo, fn, fusion_magnet_side)
    except (TypeError, ValueError):
        return out
    out["magnet"] = side_deviation(so, sn)
    ao, an = half_magnet_polygon(so).area, half_magnet_polygon(sn).area
    out["magnet_area_pct"] = 100.0 * (an - ao) / ao if ao > 0 else None
    lo, ln = _lip_half_width(old_geo, fo), _lip_half_width(new_geo, fn)
    if lo is not None and ln is not None:
        out["lip"] = abs(ln - lo)
    return out
