"""Annotated geometry "dimension sheet" for the Geometry tab's Help button.

Renders the CURRENT machine's real 2-D cross-section — the same Shapely
regions ``CadQueryMotor.get_2d_polygons()`` hands the field-map domain
classifier and the 2-D viewer, not a re-derived approximation — with a
dimension line or leader for every geometry-schema parameter that is visible
in the section, plus a legend table (grouped the way ``GET
/api/geometry/schema`` groups them) for every parameter, drawn or not.

Every dimension line's LABEL is the literal schema value
(``key = value unit``); where a line/leader is placed on the page is read off
the real polygons returned by ``get_2d_polygons()`` (outer/inner radii = the
max/min distance from the origin over a region's own boundary, a slot/magnet
"nearest 12 o'clock" = the one whose own centroid angle is closest to 90°) —
never a second, independent formula for the shape itself.

Two renders share one figure: ``ax_main`` (the full assembly, coarse
dimensions: stator OD/ID, rotor OD/ID, shaft, air gap) and ``ax_detail`` (one
slot + one tooth + one magnet, zoomed, for the fine ones: tooth width, slot
opening, magnet height, winding). A parameter that is not a shape in the
section at all (skew, stacking factor, wire counts, spacings too fine to
letter at this scale) is legend-only, flagged.
"""
from __future__ import annotations

import io
import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

#: The schema keys this module knows how to place a leader for, and where.
#: Everything else in the schema is legend-only (flagged "not a drawn
#: dimension" — usually because it is a fraction/spacing too fine to letter
#: at page scale, or is not a linear feature of the section at all).
_MAIN_KEYS = {
    "stator_diameter", "core_thickness", "air_gap",
}
_DETAIL_KEYS = {
    "tooth_width", "tooth2_width", "cut_width", "slot_hs", "slot_height",
    "magnet_height", "rotor_house_height", "wire_width", "wire_height",
    "shaft_height", "sleeve_thickness",
}
#: Rendered as a short text note (a count, not a length) near the drawing.
_COUNT_KEYS = {"num_seg", "num_slots_per_segment", "num_poles_per_segment"}
#: Rendered as a side note (axial, not visible in a cross-section).
_SIDE_NOTE_KEYS = {"motor_length"}

_MONO = "DejaVu Sans Mono"

#: Set for the duration of one ``build_dimension_sheet()`` call — False for
#: the static, model-agnostic Help picture (``scripts/geometry_help_sheet.py``
#: renders it ONCE, offline, so there is no concurrent-request risk in a
#: module-level flag here), True (the default) for the per-machine dev route,
#: which still prints the live value beside each name.
_SHOW_VALUES = True


def _num(v: Any) -> Optional[float]:
    try:
        if v is None or isinstance(v, bool):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _fmt_value(value: Any, ptype: str) -> str:
    v = _num(value)
    if v is None:
        return str(value) if value is not None else "?"
    if ptype == "int" or float(v).is_integer():
        return f"{v:g}"
    return f"{v:.3g}"


def _label(key: str, value: Any, unit: str, ptype: str) -> str:
    """``parameter_key = value unit`` for the per-machine dev route, or just
    ``parameter_key`` for the static, model-agnostic Help picture (
    ``_SHOW_VALUES = False`` — a value from ONE representative machine would
    be actively misleading printed on a picture every model shares)."""
    if not _SHOW_VALUES:
        return key
    unit_s = f" {unit}" if unit else ""
    return f"{key} = {_fmt_value(value, ptype)}{unit_s}"


# ── polygon helpers — every number below is read off the REAL region ────────

def _iter_coords(geom):
    if geom is None or geom.is_empty:
        return
    polys = list(geom.geoms) if geom.geom_type.startswith("Multi") else [geom]
    for poly in polys:
        yield from poly.exterior.coords
        for interior in poly.interiors:
            yield from interior.coords


def _radial_span(geom) -> Optional[Tuple[float, float]]:
    """(min, max) distance from the origin over every point of ``geom`` — the
    bore/tip and OD/outer radii a region actually has, not a formula's guess
    at them."""
    rs = [math.hypot(x, y) for x, y in _iter_coords(geom)]
    if not rs:
        return None
    return min(rs), max(rs)


def _centroid_angle_deg(geom) -> Optional[float]:
    if geom is None or geom.is_empty:
        return None
    c = geom.centroid
    return math.degrees(math.atan2(c.y, c.x)) % 360.0


def _nearest_to_angle(items: Sequence, angle_deg: float, key=lambda g: g):
    """The item of ``items`` whose centroid angle is closest to ``angle_deg``
    — "the slot/magnet nearest 12 o'clock", picked from the real regions
    instead of computed from the slot-pitch formula."""
    best, best_d = None, None
    for it in items:
        a = _centroid_angle_deg(key(it))
        if a is None:
            continue
        d = min(abs(a - angle_deg), 360.0 - abs(a - angle_deg))
        if best_d is None or d < best_d:
            best, best_d = it, d
    return best


def _bounds(geom):
    if geom is None or geom.is_empty:
        return None
    return geom.bounds   # (minx, miny, maxx, maxy)


def _wedge(r_max: float, deg0: float, deg1: float, r_min: float = 0.0):
    """A pie-slice (shapely Polygon) from ``deg0`` to ``deg1`` at radius
    [r_min, r_max] — the CLIP window for "one enlarged sector", built the
    same way every other arc/circle in this codebase is (a fine-stepped
    polyline), not a placeholder rectangle."""
    from shapely.geometry import Polygon as SPoly
    n = max(24, int(abs(deg1 - deg0)))
    outer = [(r_max * math.cos(math.radians(d)), r_max * math.sin(math.radians(d)))
             for d in _linspace(deg0, deg1, n)]
    if r_min > 1e-6:
        inner = [(r_min * math.cos(math.radians(d)), r_min * math.sin(math.radians(d)))
                 for d in _linspace(deg1, deg0, n)]
        pts = outer + inner
    else:
        pts = [(0.0, 0.0)] + outer
    return SPoly(pts)


def _linspace(a: float, b: float, n: int) -> List[float]:
    if n <= 1:
        return [a]
    step = (b - a) / (n - 1)
    return [a + i * step for i in range(n)]


def _clip(geom, wedge):
    """``geom`` intersected with the sector wedge — the SAME boolean op
    ``get_2d_polygons`` itself uses internally (shapely ``.intersection``),
    so this crops the real region instead of re-deriving its boundary."""
    if geom is None or geom.is_empty:
        return None
    try:
        out = geom.intersection(wedge)
    except Exception:      # noqa: BLE001 — a bad ring must not kill the page
        return None
    return None if out.is_empty else out


# ── matplotlib drawing helpers ───────────────────────────────────────────────

def _poly_patches(geom, **kw):
    import matplotlib.path as mpath
    import matplotlib.patches as mpatches
    if geom is None or geom.is_empty:
        return []
    polys = list(geom.geoms) if geom.geom_type.startswith("Multi") else [geom]
    out = []
    for poly in polys:
        verts: list = []
        codes: list = []

        def _ring(coords):
            coords = list(coords)
            if not coords:
                return
            verts.append(coords[0]); codes.append(mpath.Path.MOVETO)
            for c in coords[1:]:
                verts.append(c); codes.append(mpath.Path.LINETO)
            verts.append(coords[0]); codes.append(mpath.Path.CLOSEPOLY)

        _ring(poly.exterior.coords)
        for interior in poly.interiors:
            _ring(interior.coords)
        out.append(mpatches.PathPatch(mpath.Path(verts, codes), **kw))
    return out


def _dim_line(ax, p0, p1, text, *, offset=0.0, color="#7a5cff", fontsize=8.5,
              text_at: Optional[Tuple[float, float]] = None, ha="left", va="center"):
    """A double-headed dimension arrow p0→p1 with its label, muted colour,
    monospace label per the spec."""
    ax.annotate("", xy=p1, xytext=p0,
                arrowprops=dict(arrowstyle="<->", color=color, lw=0.9,
                                shrinkA=0, shrinkB=0))
    tx, ty = text_at if text_at is not None else (
        (p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0 + offset)
    ax.text(tx, ty, text, fontsize=fontsize, family=_MONO, color="#3a2e66",
             ha=ha, va=va,
             bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.85))


def _leader(ax, anchor, text_xy, text, *, color="#7a5cff", fontsize=8.5,
            ha="left", va="center"):
    ax.annotate(text, xy=anchor, xytext=text_xy, fontsize=fontsize,
                family=_MONO, color="#3a2e66", ha=ha, va=va,
                arrowprops=dict(arrowstyle="-", color=color, lw=0.8,
                                shrinkA=0, shrinkB=2,
                                connectionstyle="arc3,rad=0.05"),
                bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.85))


# ── PROPER dimension lines: two extension lines + a double-arrow BETWEEN the
# two edges being measured, the name ON the arrow — real drafting convention,
# and the fix for "a dozen leader lines converging on the bore area" (every
# other line in this module used to run from the feature to a distant,
# stacked label column; a dimension line instead stays local to the feature
# it measures, so it cannot cross unrelated geometry to get there).

def _dim_h(ax, x0: float, x1: float, y_dim: float, label: str, *,
           y0: Optional[float] = None, y1: Optional[float] = None,
           color: str = "#5c4aa0", fontsize: float = 22,
           text_side: str = "auto") -> None:
    """A horizontal dimension: extension lines from the feature's own edges
    (``y0``/``y1``, defaulting to ``y_dim`` — no extension needed when the
    edge already sits where the arrow does) up/down to ``y_dim``, then a
    double-arrow at ``y_dim`` from ``x0`` to ``x1`` with the label centred ON
    the arrow (a small perpendicular offset, ``text_side`` picks which)."""
    if x1 < x0:
        x0, x1 = x1, x0
    y0 = y_dim if y0 is None else y0
    y1 = y_dim if y1 is None else y1
    for x, yf in ((x0, y0), (x1, y1)):
        if abs(yf - y_dim) > 1e-9:
            ax.plot([x, x], [yf, y_dim], color=color, lw=0.55, alpha=0.75, zorder=5)
    ax.annotate("", xy=(x1, y_dim), xytext=(x0, y_dim), zorder=6,
                arrowprops=dict(arrowstyle="<->", color=color, lw=1.0,
                                shrinkA=0, shrinkB=0))
    va = "top" if text_side == "below" else "bottom"
    ax.text((x0 + x1) / 2.0, y_dim, label, fontsize=fontsize, family=_MONO,
            color="#222222", ha="center", va=va, zorder=7,
            bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.92))


def _dim_v(ax, y0: float, y1: float, x_dim: float, label: str, *,
           x0: Optional[float] = None, x1: Optional[float] = None,
           color: str = "#5c4aa0", fontsize: float = 22,
           text_side: str = "right", label_x: Optional[float] = None,
           label_dy: float = 0.0) -> None:
    """The vertical counterpart of ``_dim_h`` — extension lines horizontal,
    arrow vertical.  For this module's roughly-90°-centred sector, "vertical"
    is "radial" to a good approximation over the modest angular span drawn,
    so this doubles as the radial dimension for in-sector features (the
    small full-ring inset uses true polar arrows for the coarse OD/ID/etc.
    dimensions instead — see ``_dim_radial``).

    ``label_x`` — when given, the NAME is horizontal text at ``label_x``
    (outside the coloured body — the caller works out a point past the real
    section, see ``_esc_x`` in ``_draw_sector``), joined to the arrow by one
    short straight leader.  Without it (the wire-inset's small zoom, where
    there is no "outside" to escape to) the label prints rotated directly on
    the arrow, as before."""
    if y1 < y0:
        y0, y1 = y1, y0
    x0 = x_dim if x0 is None else x0
    x1 = x_dim if x1 is None else x1
    for y, xf in ((y0, x0), (y1, x1)):
        if abs(xf - x_dim) > 1e-9:
            ax.plot([xf, x_dim], [y, y], color=color, lw=0.55, alpha=0.75, zorder=5)
    ax.annotate("", xy=(x_dim, y1), xytext=(x_dim, y0), zorder=6,
                arrowprops=dict(arrowstyle="<->", color=color, lw=1.0,
                                shrinkA=0, shrinkB=0))
    mid_y = (y0 + y1) / 2.0
    if label_x is not None:
        label_y = mid_y + label_dy   # nudged off the arrow's own mid-height
                                      # only when another dimension's label
                                      # already lands there (label_dy) — the
                                      # leader is then a short DIAGONAL, still
                                      # one straight segment, never a curve.
        ax.plot([x_dim, label_x], [mid_y, label_y], color=color, lw=0.6,
                alpha=0.8, zorder=6)
        ha = "right" if label_x < x_dim else "left"
        pad = "  " if ha == "left" else ""
        ax.text(label_x, label_y, (pad + label) if ha == "left" else (label + pad),
                fontsize=fontsize, family=_MONO, color="#222222", ha=ha, va="center",
                zorder=7,
                bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.92))
        return
    ha = "left" if text_side == "right" else "right"
    ax.text(x_dim, mid_y, "  " + label if text_side == "right" else label + "  ",
            fontsize=fontsize, family=_MONO, color="#222222", ha=ha, va="center",
            rotation=90, rotation_mode="anchor", zorder=7,
            bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.92))


def _dim_radial(ax, r0: float, r1: float, angle_deg: float, label: str, *,
                 color: str = "#5c4aa0", fontsize: float = 22,
                 r_label: Optional[float] = None) -> None:
    """A radial dimension arrow from ``r0`` to ``r1`` at ``angle_deg`` about
    the origin — the small full-ring inset's OD/ID/etc. dimensions, drawn
    with a true polar arrow rather than the sector's flat approximation.

    ``r_label`` — when given, the arrow stops at ``r1`` (the real
    measurement) and a short straight leader continues, AT THE SAME ANGLE,
    out to ``r_label`` — outside the ring, staggered from its neighbours by
    ``angle_deg`` alone — where the NAME sits as horizontal text.  Without
    it, the label prints at the arrow's own midpoint (used where there is
    no ring to escape, e.g. none today, but kept for the simple case)."""
    a = math.radians(angle_deg)
    p0 = (r0 * math.cos(a), r0 * math.sin(a))
    p1 = (r1 * math.cos(a), r1 * math.sin(a))
    ax.annotate("", xy=p1, xytext=p0, zorder=6,
                arrowprops=dict(arrowstyle="<->", color=color, lw=0.9, shrinkA=0, shrinkB=0))
    if r_label is not None:
        p2 = (r_label * math.cos(a), r_label * math.sin(a))
        ax.plot([p1[0], p2[0]], [p1[1], p2[1]], color=color, lw=0.6, alpha=0.8, zorder=6)
        ha = "left" if math.cos(a) >= 0 else "right"
        ax.text(p2[0], p2[1], label, fontsize=fontsize, family=_MONO, color="#222222",
                ha=ha, va="center", zorder=7,
                bbox=dict(boxstyle="round,pad=0.1", fc="white", ec="none", alpha=0.92))
        return
    mid = ((p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0)
    ax.text(mid[0], mid[1], label, fontsize=fontsize, family=_MONO, color="#222222",
            ha="center", va="center", zorder=7,
            bbox=dict(boxstyle="round,pad=0.1", fc="white", ec="none", alpha=0.92))


def _solid_x_at_y(poly, y: float, x_lo: float, x_hi: float, near_x: float = 0.0
                   ) -> Optional[Tuple[float, float]]:
    """Where ``poly`` (the clipped STATOR region) is solid along the
    horizontal line ``y = y`` — the tooth's real material width at that
    radius, read off the actual polygon instead of guessed from a formula.
    Returns the solid interval closest to ``near_x`` (the tooth nearest the
    sector's own centreline), or None."""
    if poly is None or poly.is_empty:
        return None
    from shapely.geometry import LineString
    try:
        inter = poly.intersection(LineString([(x_lo, y), (x_hi, y)]))
    except Exception:      # noqa: BLE001
        return None
    if inter.is_empty:
        return None
    segs = list(inter.geoms) if inter.geom_type.startswith("Multi") else [inter]
    best, best_d = None, None
    for seg in segs:
        coords = list(getattr(seg, "coords", []))
        if len(coords) < 2:
            continue
        xs = [c[0] for c in coords]
        a, b = min(xs), max(xs)
        if b - a < 1e-6:
            continue
        c = (a + b) / 2.0
        d = abs(c - near_x)
        if best_d is None or d < best_d:
            best, best_d = (a, b), d
    return best


_PART_STYLE = {
    "stator":  dict(fc="#dbe6f6", ec="black", lw=0.8, zorder=2),
    "rotor":   dict(fc="#f7ddc4", ec="black", lw=0.8, zorder=2),
    "shaft":   dict(fc="#cfcfcf", ec="black", lw=0.8, zorder=3),
    "sleeve":  dict(fc="#e7e7e7", ec="black", lw=0.6, zorder=3.5),
    "coil":    dict(fc="#fcefa1", ec="#8a7a2a", lw=0.5, zorder=4),
    "magnet_n": dict(fc="#f2a6a6", ec="black", lw=0.6, zorder=4),
    "magnet_s": dict(fc="#a6c7f2", ec="black", lw=0.6, zorder=4),
    "air_gap": dict(fc="none", ec="#999999", lw=0.4, ls=(0, (2, 2)), zorder=1),
}


def _draw_regions(ax, regions: Dict[str, Any]) -> None:
    for key in ("air_gap", "stator", "rotor"):
        for p in _poly_patches(regions.get(key), **_PART_STYLE[key]):
            ax.add_patch(p)
    sleeve = regions.get("sleeve")
    if sleeve is not None:
        for p in _poly_patches(sleeve, **_PART_STYLE["sleeve"]):
            ax.add_patch(p)
    for p in _poly_patches(regions.get("shaft"), **_PART_STYLE["shaft"]):
        ax.add_patch(p)
    for coil in regions.get("coils") or []:
        for p in _poly_patches(coil, **_PART_STYLE["coil"]):
            ax.add_patch(p)
    for mag, polarity in regions.get("magnets") or []:
        style = _PART_STYLE["magnet_n"] if polarity >= 0 else _PART_STYLE["magnet_s"]
        for p in _poly_patches(mag, **style):
            ax.add_patch(p)


def _draw_main(ax, geo: Dict[str, Any], regions: Dict[str, Any], drawn: set) -> None:
    _draw_regions(ax, regions)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title("Full cross-section (current machine)", fontsize=10)

    stator_span = _radial_span(regions.get("stator"))
    rotor_span = _radial_span(regions.get("rotor"))
    shaft_span = _radial_span(regions.get("shaft"))
    gap_span = _radial_span(regions.get("air_gap"))

    def _pt(r, deg):
        a = math.radians(deg)
        return (r * math.cos(a), r * math.sin(a))

    # Every label lands on a FIXED ring outside the drawing, at its own
    # evenly-spaced angle — a leader from the real feature to that spot.  A
    # feature's own radius (the shaft bore is a few mm; the stator OD is the
    # whole page) never decides how far out its label sits, which is what
    # crowded every small-radius label into the same few pixels near the
    # centre before.
    label_r = (stator_span[1] if stator_span else 20.0) * 1.42
    colors = {"stator_diameter": "#5c4aa0", "stator_inner_radius": "#4a8f5c",
              "core_thickness": "#7a5cff", "rotor_outer_radius": "#a05c00",
              "rotor_inner_radius": "#a05c00", "air_gap": "#c0392b",
              "shaft_od": "#3a2e66", "shaft_height": "#3a2e66"}

    def _rim_leader(key: str, deg: float, anchor, text: str) -> None:
        _leader(ax, anchor, _pt(label_r, deg), text, color=colors.get(key, "#7a5cff"))

    # Exactly 45° apart (8 slots on the ring) — the earlier "close but not
    # equal" angles (e.g. -115° next to 240°) still collided; equal spacing
    # is the only way eight independent leaders never do.
    A_AIR_GAP, A_SHAFT_HEIGHT, A_SHAFT_OD, A_ROTOR_IR = 0, 45, 90, 135
    A_CORE_T, A_ROTOR_OR, A_STATOR_IR, A_STATOR_OD = 180, 225, 270, 315

    if stator_span:
        r_out, r_in = stator_span[1], stator_span[0]
        _rim_leader("stator_diameter", A_STATOR_OD, _pt(r_out, A_STATOR_OD),
                     _label("stator_diameter", geo.get("stator_diameter"), "mm", "float"))
        drawn.add("stator_diameter")
        _rim_leader("stator_inner_radius", A_STATOR_IR, _pt(r_in, A_STATOR_IR),
                     _label("stator_inner_radius (bore)", geo.get("stator_inner_radius"), "mm", "float"))
        if rotor_span:
            _rim_leader("core_thickness", A_CORE_T, _pt((r_out + rotor_span[1]) / 2.0, A_CORE_T),
                         _label("core_thickness (yoke)", geo.get("core_thickness"), "mm", "float"))
            drawn.add("core_thickness")

    if rotor_span:
        _rim_leader("rotor_outer_radius", A_ROTOR_OR, _pt(rotor_span[1], A_ROTOR_OR),
                     _label("rotor_outer_radius", geo.get("rotor_outer_radius"), "mm", "float"))
        r_ri = rotor_span[0] if rotor_span[0] > 1e-6 else 1.0
        _rim_leader("rotor_inner_radius", A_ROTOR_IR, _pt(r_ri, A_ROTOR_IR),
                     _label("rotor_inner_radius", geo.get("rotor_inner_radius"), "mm", "float"))

    if gap_span:
        mid = (gap_span[0] + gap_span[1]) / 2.0
        _dim_line(ax, _pt(gap_span[0], A_AIR_GAP), _pt(gap_span[1], A_AIR_GAP), "",
                   text_at=_pt(mid, A_AIR_GAP))
        _rim_leader("air_gap", A_AIR_GAP, _pt(mid, A_AIR_GAP),
                     _label("air_gap", geo.get("air_gap"), "mm", "float"))
        drawn.add("air_gap")

    if shaft_span:
        _rim_leader("shaft_od", A_SHAFT_OD, _pt(shaft_span[1], A_SHAFT_OD),
                     _label("shaft OD (2x the shaft region's own radius)",
                            2 * shaft_span[1], "mm", "float"))
        _rim_leader("shaft_height", A_SHAFT_HEIGHT, _pt((shaft_span[0] + shaft_span[1]) / 2.0, A_SHAFT_HEIGHT),
                     _label("shaft_height (rotor bore to shaft)", geo.get("shaft_height"), "mm", "float"))
        drawn.add("shaft_height")

    # Counts — an ARC over one segment (num_seg) plus the two per-segment
    # counts as text, near the drawing rather than as a length dimension.
    if stator_span and geo.get("num_seg"):
        try:
            n_seg = max(1, int(round(float(geo["num_seg"]))))
        except (TypeError, ValueError):
            n_seg = 1
        seg_deg = 360.0 / n_seg
        r_arc = stator_span[1] * 0.72
        import matplotlib.patches as mpatches
        ax.add_patch(mpatches.Arc((0, 0), 2 * r_arc, 2 * r_arc, angle=0,
                                   theta1=0, theta2=seg_deg, color="#333333", lw=1.3))
        ax.plot([0, r_arc], [0, 0], color="#333333", lw=0.6)
        a2 = math.radians(seg_deg)
        ax.plot([0, r_arc * math.cos(a2)], [0, r_arc * math.sin(a2)], color="#333333", lw=0.6)
        _leader(ax, _pt(r_arc, seg_deg / 2.0), _pt(label_r * 0.6, seg_deg / 2.0 + 15),
                 "num_seg" if not _SHOW_VALUES else _label("num_seg", geo.get("num_seg"), "", "int"),
                 color="#333333")
        drawn.add("num_seg")

    n_slots = geo.get("num_slots"); n_poles = geo.get("num_poles")
    n_seg = geo.get("num_seg"); nsps = geo.get("num_slots_per_segment")
    npps = geo.get("num_poles_per_segment")
    if _SHOW_VALUES:
        note_txt = (
            f"num_slots = {_fmt_value(n_slots, 'int')}  "
            f"(num_seg={_fmt_value(n_seg, 'int')} x num_slots_per_segment={_fmt_value(nsps, 'int')})\n"
            f"num_poles = {_fmt_value(n_poles, 'int')}  "
            f"(num_seg={_fmt_value(n_seg, 'int')} x num_poles_per_segment={_fmt_value(npps, 'int')})")
    else:
        note_txt = (
            "num_slots = num_seg x num_slots_per_segment\n"
            "num_poles = num_seg x num_poles_per_segment")
    ax.text(0.02, 0.02, note_txt, transform=ax.transAxes, fontsize=8,
            family=_MONO, va="bottom", ha="left", color="#333333",
            bbox=dict(boxstyle="round,pad=0.3", fc="#f6f6f6", ec="#dddddd"))
    drawn.update({"num_slots", "num_poles", "num_slots_per_segment",
                  "num_poles_per_segment"})

    if stator_span:
        lim = stator_span[1] * 1.75
        ax.set_xlim(-lim, lim); ax.set_ylim(-lim, lim)


def _zoom_to(ax, xs: List[float], ys: List[float], pad_x: float, pad_y: float,
             fallback: float = 10.0) -> None:
    """Tightly crop ``ax`` to (xs, ys) plus a fixed margin for leader labels —
    a per-axis pad, not a shared symmetric one, so a feature that is wide but
    short (a coil) doesn't drag a tall, near-empty square into view."""
    if xs and ys:
        cx = (min(xs) + max(xs)) / 2.0
        cy = (min(ys) + max(ys)) / 2.0
        half_x = (max(xs) - min(xs)) / 2.0 + pad_x
        half_y = (max(ys) - min(ys)) / 2.0 + pad_y
        ax.set_xlim(cx - half_x, cx + half_x)
        ax.set_ylim(cy - half_y, cy + half_y)
    else:
        ax.set_xlim(-fallback, fallback); ax.set_ylim(-fallback, fallback)


def _draw_stator_detail(ax, geo: Dict[str, Any], regions: Dict[str, Any], drawn: set) -> None:
    """One slot + its two flanking teeth, zoomed — tooth width(s), slot
    height/opening, one conductor's wire_width/wire_height."""
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title("Stator detail — one slot / tooth (zoomed)", fontsize=9.5)

    _draw_regions(ax, regions)

    coils = list(regions.get("coils") or [])
    coil = _nearest_to_angle(coils, 90.0)
    xs: List[float] = []; ys: List[float] = []

    if coil is not None:
        b = _bounds(coil)
        if b:
            minx, miny, maxx, maxy = b
            xs += [minx, maxx]; ys += [miny, maxy]
            w = maxx - minx; h = maxy - miny
            _dim_line(ax, (minx, maxy + 0.2 * h + 0.3), (maxx, maxy + 0.2 * h + 0.3),
                       _label("wire_width", geo.get("wire_width"), "mm", "float"),
                       text_at=((minx + maxx) / 2.0, maxy + 0.55 * h + 0.6),
                       fontsize=7.5)
            _leader(ax, (maxx, (miny + maxy) / 2.0),
                     (maxx + 1.6, (miny + maxy) / 2.0 + 1.2),
                     _label("wire_height", geo.get("wire_height"), "mm", "float"), fontsize=7.5)
            _leader(ax, ((minx + maxx) / 2.0, miny),
                     ((minx + maxx) / 2.0 - 2.0, miny - 2.2),
                     _label("slot_height", geo.get("slot_height"), "mm", "float"), fontsize=7.5)
            _leader(ax, (minx, (miny + maxy) / 2.0),
                     (minx - 2.4, (miny + maxy) / 2.0 - 0.6),
                     _label("cut_width (slot opening)", geo.get("cut_width"), "mm", "float"),
                     fontsize=7.5)
            _leader(ax, (maxx, maxy),
                     (maxx + 1.6, maxy + 1.6),
                     _label("slot_hs (opening height)", geo.get("slot_hs"), "mm", "float"),
                     fontsize=7.5)
            _leader(ax, (minx, maxy),
                     (minx - 2.2, maxy + 2.0),
                     _label("tooth_width", geo.get("tooth_width"), "mm", "float"), fontsize=7.5)
            _leader(ax, (maxx, miny),
                     (maxx + 2.2, miny - 2.0),
                     _label("tooth2_width", geo.get("tooth2_width"), "mm", "float"), fontsize=7.5)
            drawn.update({"wire_width", "wire_height", "slot_height", "cut_width",
                          "slot_hs", "tooth_width", "tooth2_width"})

    _zoom_to(ax, xs, ys, pad_x=5.0, pad_y=5.5)


def _draw_rotor_detail(ax, geo: Dict[str, Any], regions: Dict[str, Any], drawn: set) -> None:
    """One magnet pocket, zoomed — magnet height, rotor housing thickness,
    sleeve (if any), shaft."""
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title("Rotor detail — one magnet (zoomed)", fontsize=9.5)

    _draw_regions(ax, regions)

    magnets = list(regions.get("magnets") or [])
    magnet = _nearest_to_angle(magnets, 90.0, key=lambda mp: mp[0])
    mag_poly = magnet[0] if magnet is not None else None
    xs: List[float] = []; ys: List[float] = []

    if mag_poly is not None:
        b = _bounds(mag_poly)
        if b:
            minx, miny, maxx, maxy = b
            xs += [minx, maxx]; ys += [miny, maxy]
            _leader(ax, ((minx + maxx) / 2.0, miny),
                     ((minx + maxx) / 2.0 - 2.2, miny - 2.4),
                     _label("magnet_height", geo.get("magnet_height"), "mm", "float"),
                     fontsize=7.5)
            _leader(ax, (maxx, (miny + maxy) / 2.0),
                     (maxx + 2.4, (miny + maxy) / 2.0 + 1.0),
                     _label("rotor_house_height", geo.get("rotor_house_height"), "mm", "float"),
                     fontsize=7.5)
            drawn.update({"magnet_height", "rotor_house_height"})

    sleeve_t = _num(geo.get("sleeve_thickness")) or 0.0
    sleeve_span = _radial_span(regions.get("sleeve")) if regions.get("sleeve") is not None else None
    if sleeve_span:
        p0 = (sleeve_span[1] * math.cos(math.radians(70)), sleeve_span[1] * math.sin(math.radians(70)))
        xs.append(p0[0]); ys.append(p0[1])
        _leader(ax, p0, (p0[0] - 2.0, p0[1] + 2.0),
                 _label("sleeve_thickness", sleeve_t, "mm", "float"), fontsize=7.5)
        drawn.add("sleeve_thickness")

    if xs and ys:
        _zoom_to(ax, xs, ys, pad_x=5.5, pad_y=5.5)
    else:
        # No magnet region resolved (e.g. surface-less rotor): fall back to a
        # view of the rotor centre, still real geometry, just wider.
        rotor_span = _radial_span(regions.get("rotor"))
        lim = (rotor_span[1] if rotor_span else 10.0) * 0.6
        ax.set_xlim(-lim, lim); ax.set_ylim(-lim, lim)


def _draw_sector(ax, geo: Dict[str, Any], regions: Dict[str, Any], drawn: set):
    """ONE enlarged sector (a slot pitch / pole pair, centred at 12 o'clock) —
    THE picture (~65% of the sheet's width, ≥2400 px), every dimension a
    proper two-extension-line double arrow local to the feature it measures
    — never a leader crossing the drawing to reach a stacked label column,
    which is what made the first version illegible.

    The crop is a real boolean ``region.intersection(wedge)`` against each
    Shapely region ``get_2d_polygons()`` returned (see ``_wedge``/``_clip``)
    — a crop of the real section, not a redrawn approximation of one. The
    three small insets (coarse radii, one conductor, axial view) are drawn
    by the CALLER as sibling subplots — see ``build_dimension_sheet``.

    Returns the clipped coil (one conductor) nearest 12 o'clock, or None —
    the caller hands it to ``_draw_wire_inset`` so that inset zooms into the
    SAME strand this view's slot/tooth dimensions were measured against.
    """
    ax.set_aspect("equal")
    ax.axis("off")

    stator_span = _radial_span(regions.get("stator"))
    rotor_span = _radial_span(regions.get("rotor"))
    shaft_span = _radial_span(regions.get("shaft"))
    gap_span = _radial_span(regions.get("air_gap"))
    if not stator_span:
        ax.text(0.5, 0.5, "(no stator region)", transform=ax.transAxes, ha="center")
        return

    angle_slot = _num(geo.get("angle_slot")) or 30.0
    angle_pole = _num(geo.get("angle_pole")) or 30.0
    span = min(170.0, max(28.0, 2.3 * max(angle_slot, angle_pole)))
    deg0, deg1 = 90.0 - span / 2.0, 90.0 + span / 2.0
    r_max = stator_span[1] * 1.04
    wedge = _wedge(r_max, deg0, deg1, r_min=0.0)

    def _pt(r, deg):
        a = math.radians(deg)
        return (r * math.cos(a), r * math.sin(a))

    # ── clip every region to the wedge and draw it ──────────────────────────
    clipped = {k: _clip(regions.get(k), wedge) for k in ("air_gap", "stator", "rotor", "shaft")}
    sleeve_c = _clip(regions.get("sleeve"), wedge) if regions.get("sleeve") is not None else None
    coils_c = [c for c in (_clip(p, wedge) for p in (regions.get("coils") or [])) if c is not None]
    magnets_c = [(m, pol) for m, pol in
                 ((_clip(p, wedge), pol) for p, pol in (regions.get("magnets") or []))
                 if m is not None]

    for key in ("air_gap", "stator", "rotor"):
        for p in _poly_patches(clipped.get(key), **_PART_STYLE[key]):
            ax.add_patch(p)
    if sleeve_c is not None:
        for p in _poly_patches(sleeve_c, **_PART_STYLE["sleeve"]):
            ax.add_patch(p)
    for p in _poly_patches(clipped.get("shaft"), **_PART_STYLE["shaft"]):
        ax.add_patch(p)
    for c in coils_c:
        for p in _poly_patches(c, **_PART_STYLE["coil"]):
            ax.add_patch(p)
    for m, pol in magnets_c:
        style = _PART_STYLE["magnet_n"] if pol >= 0 else _PART_STYLE["magnet_s"]
        for p in _poly_patches(m, **style):
            ax.add_patch(p)

    coil = _nearest_to_angle(coils_c, 90.0)          # one wire, nearest 12 o'clock
    magnet = _nearest_to_angle(magnets_c, 90.0, key=lambda mp: mp[0])
    mag_poly = magnet[0] if magnet is not None else None
    stator_c = clipped.get("stator")

    # ── view window: TIGHT around the sector's own content. The three
    # insets used to be carved out of a reserved margin HERE, inside this
    # same axes — that fought `aspect="equal"` for the same pixels and lost
    # (whichever axis the reservation widened, the equal-aspect box just
    # shrank to compensate, so the reservation became a dead gap instead of
    # the insets landing there). They are now separate sibling subplots in
    # their own gridspec column instead — see ``build_dimension_sheet``,
    # layout="sector" — which only trades that bug for a smaller one: this
    # axes' OWN allotted box is not exactly the data's own aspect ratio
    # either, so `aspect="equal"` still shrinks it a little. Pad the shorter
    # axis up to match instead of leaving the shrink centred (which read as
    # the same "why is the drawing only using half the column" gap, just
    # smaller) and anchor left so any remainder sits on the insets' side.
    half_w = r_max * math.sin(math.radians(span / 2.0))

    # Escape points for the ESCAPED labels (see ``_esc_x`` below) sit just
    # past ``half_w`` — no region in this wedge can exceed it in x, by
    # construction (half_w IS the wedge's widest possible extent, at
    # r=r_max) — with TEXT_ROOM reserved beyond THAT for the label itself:
    # placing the escape point AT the axes' own xlim edge worked for the
    # ARROW/LEADER (a zero-width line) but clipped the NAME sitting there,
    # which has width and was growing further out, off the edge of the page.
    ESCAPE_MARGIN = half_w * 0.06
    TEXT_ROOM = half_w * 0.62      # bigger than before — the labels are now
                                    # 22pt, not 7.6pt, and need real room to
                                    # sit in without running off the page
    esc_left, esc_right = -(half_w + ESCAPE_MARGIN), half_w + ESCAPE_MARGIN
    x_min, x_max = esc_left - TEXT_ROOM, esc_right + TEXT_ROOM
    y_min, y_max = -r_max * 0.04, r_max * 1.12
    # Read back from THIS axes' own allotted box (gridspec position x figure
    # size) rather than a hand-copied constant — a magic number here had to
    # be kept in sync with build_dimension_sheet's gridspec by hand, and
    # fell out of sync the moment either one changed on its own.
    _bb = ax.get_position()
    _fw, _fh = ax.figure.get_size_inches()
    BOX_ASPECT = (_bb.width * _fw) / (_bb.height * _fh)
    nat_x, nat_y = x_max - x_min, y_max - y_min
    if nat_x / nat_y > BOX_ASPECT:
        want_y = nat_x / BOX_ASPECT
        pad = (want_y - nat_y) / 2.0
        y_min -= pad; y_max += pad
    else:
        want_x = nat_y * BOX_ASPECT
        pad = (want_x - nat_x) / 2.0
        x_min -= pad; x_max += pad
        esc_left -= pad; esc_right += pad
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(y_min, y_max)
    ax.set_anchor("W")
    lane = half_w * 0.16     # one dimension-line "lane" width — successive
                             # parallel dimensions step out by this much, the
                             # standard drafting fix for several stacked
                             # dimensions sharing one side of a part.
    tan_half_span = math.tan(math.radians(span / 2.0))

    def _esc_x(mid_y: float, side: str, push: float = 1.0) -> float:
        """Where an escaped label's short leader ends, outside the section —
        see the ESCAPE_MARGIN/TEXT_ROOM comment above for why this is the
        axes' own overall margin rather than a per-height computation: near
        the bore the teeth's shoes flare out enough that the section is
        solid across almost the whole wedge at that radius, so a "local,
        short" distance from THAT height's own material had nowhere clear to
        land — it was still well inside the wedge's own outer silhouette
        (the same flare reaches back out near the stator OD)."""
        return esc_left if side == "left" else esc_right

    # ═══ 1. Tooth / slot dimensions — sliced from the REAL stator polygon ═══
    # The SLOT's own envelope, not one wire's tiny bbox: the picked `coil` is
    # a single strand, and using its miny/maxy for "where is the slot"
    # measured barely any radial span at all — tooth_width and tooth2_width
    # ended up sampled a fraction of a mm apart and their dimension lines
    # sat on top of each other.  Every wire whose column shares the picked
    # one's tangential position is really the same slot; their union's bbox
    # is the slot's real radial reach.
    if coil is not None:
        cb = _bounds(coil)
    else:
        cb = None
    if cb is not None:
        pcx = (cb[0] + cb[2]) / 2.0
        pw = max(cb[2] - cb[0], 1e-6)
        same_slot = [c for c in coils_c if _bounds(c) is not None
                     and abs(((_bounds(c)[0] + _bounds(c)[2]) / 2.0) - pcx) < pw * 2.5]
        try:
            from shapely.ops import unary_union
            envelope = unary_union(same_slot) if same_slot else coil
        except Exception:      # noqa: BLE001
            envelope = coil
        eb = _bounds(envelope) or cb
        cminx, cminy, cmaxx, cmaxy = eb
    else:
        cminx = cmaxx = 0.0
        cminy, cmaxy = stator_span[0] + 0.05 * (stator_span[1] - stator_span[0]), \
            stator_span[1] - 0.05 * (stator_span[1] - stator_span[0])

    x_lo, x_hi = -half_w * 1.2, half_w * 1.2
    tooth_yoke = _solid_x_at_y(stator_c, cmaxy - 0.03 * (cmaxy - cminy), x_lo, x_hi)   # near the yoke
    tooth_bore = _solid_x_at_y(stator_c, cminy + 0.03 * (cmaxy - cminy), x_lo, x_hi)   # near the bore

    if tooth_yoke is not None:
        _dim_h(ax, tooth_yoke[0], tooth_yoke[1], cmaxy,
               _label("tooth_width", geo.get("tooth_width"), "mm", "float"))
        drawn.add("tooth_width")
    if tooth_bore is not None:
        _dim_h(ax, tooth_bore[0], tooth_bore[1], cminy,
               _label("tooth2_width", geo.get("tooth2_width"), "mm", "float"), text_side="below")
        drawn.add("tooth2_width")

    # cut_width — the slot opening's own width, right at the bore tip (the
    # slot envelope's own tangential span, at the radius nearest the bore).
    y_bore_tip = stator_span[0] + 0.015 * (stator_span[1] - stator_span[0])
    _dim_h(ax, cminx, cmaxx, y_bore_tip,
           _label("cut_width", geo.get("cut_width"), "mm", "float"),
           text_side="below", color="#1f8a5f")
    drawn.add("cut_width")

    # slot_hs — the opening's own (short) radial extent, right at the tip —
    # own lane, well clear of cut_width's horizontal arrow at the same spot.
    # Arrow anchored at the slot's own edge (not an abstract lane multiple,
    # which is what put the LABEL under the tooth's flared shoulder at low
    # radius); the NAME itself escapes further out, horizontal.
    slot_hs_h = min(0.06 * (stator_span[1] - stator_span[0]), (cmaxy - cminy) * 0.18) or 0.5
    slot_hs_x = cminx - 0.35 * max(cmaxx - cminx, 0.5)
    slot_hs_mid = y_bore_tip + slot_hs_h / 2.0
    slot_hs_label_y_nudge = r_max * 0.09    # air_gap's own label lands at
                                             # almost the same height (both
                                             # near the bore) — nudge clear.
    # The leader is a short DIAGONAL once nudged (start and end at different
    # y), so the escape point has to clear the real section at BOTH ends,
    # not just where the arrow itself is — the far side (whichever needs
    # more room) decides how far out it goes.
    slot_hs_esc = min(_esc_x(slot_hs_mid, "left"),
                       _esc_x(slot_hs_mid + slot_hs_label_y_nudge, "left"))
    _dim_v(ax, y_bore_tip, y_bore_tip + slot_hs_h, x_dim=slot_hs_x,
           label=_label("slot_hs", geo.get("slot_hs"), "mm", "float"), color="#1f8a5f",
           label_x=slot_hs_esc, label_dy=slot_hs_label_y_nudge)
    drawn.add("slot_hs")

    # slot_height — the slot's own (real, envelope) radial span. Escapes
    # LEFT, same as every other name now (detaches it from tooth_width's
    # label, which sits right above this arrow's top end at cmaxy — with
    # the bigger fonts the two rotated/horizontal labels started touching).
    _dim_v(ax, cminy, cmaxy, x_dim=cminx - lane,
           label=_label("slot_height", geo.get("slot_height"), "mm", "float"),
           label_x=_esc_x((cminy + cmaxy) / 2.0, "left"))
    drawn.add("slot_height")

    # core_thickness — yoke material above the slot. Offset toward the
    # tooth's own edge, NOT its centreline — tooth_width's label sits
    # centred at y=cmaxy, exactly where this arrow starts, and a vertical
    # line dead-centre ran straight through that label's text box.
    tooth_cx = (tooth_yoke[0] + tooth_yoke[1]) / 2.0 if tooth_yoke else 0.0
    core_t_x = (tooth_yoke[1] - 0.18 * (tooth_yoke[1] - tooth_yoke[0])
                if tooth_yoke else tooth_cx)
    _dim_v(ax, cmaxy, stator_span[1], x_dim=core_t_x,
           label=_label("core_thickness", geo.get("core_thickness"), "mm", "float"),
           label_x=_esc_x((cmaxy + stator_span[1]) / 2.0, "right"))
    drawn.add("core_thickness")

    # ═══ 2. Air gap / sleeve / magnet — the ARROW sits on the real feature
    # (centreline for the full rings — air_gap, sleeve — which span the
    # whole tangential width at their radius anyway; the magnet's own edge
    # for anything measured off the magnet, not an abstract lane multiple
    # that does not shrink with radius the way the wedge itself does and so
    # went missing past the wedge's real, narrower edge at low radius — the
    # "floats in empty space" bug). The NAME always escapes to a short
    # horizontal leader outside the section, never rotated over the fill.
    if gap_span:
        mid = (gap_span[0] + gap_span[1]) / 2.0
        _dim_v(ax, gap_span[0], gap_span[1], x_dim=0.0,
               label=_label("air_gap", geo.get("air_gap"), "mm", "float"), color="#c0392b",
               label_x=_esc_x(mid, "left"))
        drawn.add("air_gap")

    sleeve_t = _num(geo.get("sleeve_thickness")) or 0.0
    sleeve_span = _radial_span(sleeve_c) if sleeve_c is not None else None
    if sleeve_span:
        mid = (sleeve_span[0] + sleeve_span[1]) / 2.0
        # Nudged UP, clear of magnet_up_gap's own label just below it — the
        # sleeve sits right where the air gap starts, i.e. right next to
        # where magnet_up_gap ends (rotor_span[1]), so the two escaped
        # labels land almost at the same height on the same (right) side.
        _dim_v(ax, sleeve_span[0], sleeve_span[1], x_dim=0.0,
               label=_label("sleeve_thickness", sleeve_t, "mm", "float"),
               label_x=_esc_x(mid, "right"), label_dy=r_max * 0.09)
        drawn.add("sleeve_thickness")

    if mag_poly is not None and rotor_span:
        b = _bounds(mag_poly)
        if b:
            mminx, mminy, mmaxx, mmaxy = b
            mw = max(mmaxx - mminx, 0.5)

            # Reference radii, computed the SAME way
            # cadquery_geometry.py's magnet-pocket builder does (the
            # "Magnet local polygon" block in get_2d_polygons /
            # get_2d_mesh_data — mp1..mp6): magnet_r = rotor_inner_radius +
            # rotor_house_height (the magnet's own bottom corners, mp1/mp6);
            # magnet_r + magnet_down_height is the RADIAL "wing" edge above
            # that (mp2/mp5); the magnet's real top is rotor_or -
            # magnet_up_gap (the arc, mp3/mp4). NOT re-derived — these are
            # the exact expressions that file uses, evaluated here instead
            # of guessed at from the rendered polygon's own bounding box
            # (which is how magnet_down_height ended up drawn identically to
            # rotor_house_height before: both used (rotor_span[0], mminy)).
            rotor_hh = _num(geo.get("rotor_house_height")) or 0.0
            mag_down_h = _num(geo.get("magnet_down_height")) or 0.0
            mag_up_gap = _num(geo.get("magnet_up_gap")) or 0.0
            magnet_r = rotor_span[0] + rotor_hh
            magnet_r2 = magnet_r + mag_down_h
            magnet_top_r = rotor_span[1] - mag_up_gap

            # magnet_height — the NOMINAL envelope, magnet_r to rotor_or:
            # rotor_inner_radius is DERIVED as
            # rotor_or - magnet_height - rotor_house_height (motor_geometry.
            # py), so magnet_height itself algebraically equals
            # rotor_or - magnet_r exactly — NOT the magnet's own physical
            # top, which falls magnet_up_gap short of rotor_or whenever that
            # gap is nonzero.
            _dim_v(ax, magnet_r, rotor_span[1], x_dim=mminx - 0.15 * mw,
                   label=_label("magnet_height", geo.get("magnet_height"), "mm", "float"),
                   label_x=_esc_x((magnet_r + rotor_span[1]) / 2.0, "left"))
            drawn.add("magnet_height")

            # magnet_up_gap — rotor_or down to the magnet's REAL top.
            mid_up = (magnet_top_r + rotor_span[1]) / 2.0
            _dim_v(ax, magnet_top_r, rotor_span[1], x_dim=mmaxx + 0.15 * mw,
                   label=_label("magnet_up_gap", geo.get("magnet_up_gap"), "mm", "float"),
                   label_x=_esc_x(mid_up, "right"))
            drawn.add("magnet_up_gap")

            # magnet_down_height — magnet_r to magnet_r + magnet_down_height:
            # the magnet's own bottom-corner radial "wing" edge (mp1->mp2 in
            # the CAD code) — a short segment near the magnet's bottom, not
            # rotor_house_height's span (rotor_ir to magnet_r).
            mid_dh = (magnet_r + magnet_r2) / 2.0
            _dim_v(ax, magnet_r, magnet_r2, x_dim=mmaxx + 0.15 * mw,
                   label=_label("magnet_down_height", geo.get("magnet_down_height"), "mm", "float"),
                   label_x=_esc_x(mid_dh, "right"))
            drawn.add("magnet_down_height")

            # rotor_house_height — rotor_ir up to the magnet's own bottom.
            mid_rh = (rotor_span[0] + magnet_r) / 2.0
            _dim_v(ax, rotor_span[0], magnet_r, x_dim=mminx - 0.15 * mw,
                   label=_label("rotor_house_height", geo.get("rotor_house_height"), "mm", "float"),
                   label_x=_esc_x(mid_rh, "left"))
            drawn.add("rotor_house_height")

    # shaft_height — the shaft is only a thin grey sliver in this sector
    # (r_min=0 wedge), nothing recognisable to hang a dimension off; it is
    # drawn on the small full-ring inset instead, where the shaft is an
    # actual visible ring (see ``_draw_ring_inset``).

    # ═══ 3. num_seg — an arc + short label (a count, not a length: no
    # dimension line is possible for it) ════════════════════════════════════
    if geo.get("num_seg"):
        r_arc = stator_span[0] * 0.55
        import matplotlib.patches as mpatches
        a0, a1 = 90 - angle_slot / 2.0, 90 + angle_slot / 2.0
        ax.add_patch(mpatches.Arc((0, 0), 2 * r_arc, 2 * r_arc, angle=0,
                                   theta1=a0, theta2=a1, color="#333333", lw=1.2))
        # A short STRAIGHT leader straight down to the bottom margin (below
        # y=0, outside every region in this wedge — see _esc_x's comment on
        # why r_min=0 guarantees that) instead of a label sitting inside the
        # rotor core, which is what the arc's own midpoint used to do.
        anchor = _pt(r_arc, 90)
        label_y = -r_max * 0.025      # just past y=0 — every point IN the
                                       # wedge has y >= 0 (r_min=0, apex at
                                       # the origin), so this is guaranteed
                                       # clear without a long leader down to
                                       # the (possibly padded) axes edge.
        ax.plot([anchor[0], 0.0], [anchor[1], label_y], color="#333333", lw=0.7,
                alpha=0.85, zorder=6)
        ax.text(0.0, label_y,
                "num_seg" if not _SHOW_VALUES else _label("num_seg", geo.get("num_seg"), "", "int"),
                fontsize=22, family=_MONO, color="#222222", ha="center", va="top", zorder=7,
                bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.92))
        drawn.add("num_seg")

    return coil    # the picked conductor — the wire inset zooms into THIS one


def _draw_ring_inset(ax, geo: Dict[str, Any], regions: Dict[str, Any], drawn: set, *,
                      big: bool = False) -> None:
    """Full-ring diagram — the coarse radii/diameters AND shaft_height (the
    sector has only a sliver of the shaft to hang a dimension off; the full
    ring has the real thing), each a TRUE polar dimension arrow from the
    axis (``_dim_radial``) with its name OUTSIDE the ring on a short
    staggered leader — 5 dimensions at 5 angles 72° apart, so none crosses
    the ring or another leader. ``big`` — the standalone Radii page's own
    dominant picture, not the sector page's small corner inset: bigger
    fonts, more room reserved for them."""
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title("Every diameter / radius" if big else "radii (full ring)",
                 fontsize=32 if big else 18, color="#333333" if big else "#555555",
                 pad=14 if big else 6, fontweight="bold" if big else "normal")
    label_fontsize = 26 if big else 22

    for key in ("air_gap", "stator", "rotor"):
        for p in _poly_patches(regions.get(key), **_PART_STYLE[key]):
            ax.add_patch(p)
    for p in _poly_patches(regions.get("shaft"), **_PART_STYLE["shaft"]):
        ax.add_patch(p)
    if regions.get("sleeve") is not None:
        for p in _poly_patches(regions.get("sleeve"), **_PART_STYLE["sleeve"]):
            ax.add_patch(p)

    stator_span = _radial_span(regions.get("stator"))
    rotor_span = _radial_span(regions.get("rotor"))
    shaft_span = _radial_span(regions.get("shaft"))
    if not stator_span:
        return
    r_out = stator_span[1]
    r_lbl = r_out * 1.55       # where every name lands — one common ring of
                                # labels, outside the drawing, so nothing
                                # written there can ever overlap the section.
                                # Bigger than before — separation between two
                                # neighbouring labels scales with r_lbl at a
                                # fixed angular spacing, and the labels
                                # themselves are now 22pt, ~3x the size.

    # Exactly 5 slots, 72 degrees apart — the previous set (-72, -8, 64, 136,
    # then a "5th slot" typed as -216) was NOT evenly spaced: -216 reduces to
    # 144 degrees, only 8 degrees from the 136-degree slot right next to it,
    # so shaft_height's label sat on top of rotor_inner_radius's.
    A_OD, A_ID, A_ROR, A_RIR, A_SHAFT = -72, 0, 72, 144, 216

    _dim_radial(ax, 0, stator_span[1], A_OD,
                _label("stator_diameter", geo.get("stator_diameter"), "mm", "float"),
                r_label=r_lbl, fontsize=label_fontsize)
    drawn.add("stator_diameter")
    _dim_radial(ax, 0, stator_span[0], A_ID,
                _label("stator_inner_radius", geo.get("stator_inner_radius"), "mm", "float"),
                r_label=r_lbl, fontsize=label_fontsize)
    drawn.add("stator_inner_radius")
    if rotor_span:
        _dim_radial(ax, 0, rotor_span[1], A_ROR,
                    _label("rotor_outer_radius", geo.get("rotor_outer_radius"), "mm", "float"),
                    r_label=r_lbl, fontsize=label_fontsize)
        drawn.add("rotor_outer_radius")
        _dim_radial(ax, 0, max(rotor_span[0], 1.0), A_RIR,
                    _label("rotor_inner_radius", geo.get("rotor_inner_radius"), "mm", "float"),
                    r_label=r_lbl, fontsize=label_fontsize)
        drawn.add("rotor_inner_radius")
    if shaft_span:
        _dim_radial(ax, 0, shaft_span[1], A_SHAFT,
                    _label("shaft_height", geo.get("shaft_height"), "mm", "float"),
                    r_label=r_lbl, fontsize=label_fontsize)
        drawn.add("shaft_height")

    # Generous — a near-horizontal label grows AWAY from its anchor with
    # real, non-zero text width; too little margin here let it run past this
    # axes' own right edge and, since text is not clipped by default, bleed
    # into the legend column next door.
    lim = r_lbl * (1.7 if big else 2.0)
    ax.set_xlim(-lim, lim); ax.set_ylim(-lim, lim)


def _draw_gap_callout(ax, geo: Dict[str, Any], regions: Dict[str, Any], drawn: set) -> None:
    """A magnified wedge of the air-gap zone — bore, sleeve OD, rotor OD,
    magnet OD as concentric arcs, with air_gap / sleeve_thickness /
    magnet_up_gap as radial dimension arrows between them (names outside, on
    staggered leaders, same convention as everywhere else). The owner's
    "second, enlarged half-ring/quadrant of the gap zone" — the small
    full-ring picture is too small to letter these three, which are all a
    couple of mm at most next to a 100+ mm stator OD."""
    import matplotlib.patches as mpatches
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title("Air gap zone, enlarged", fontsize=28, color="#333333",
                 fontweight="bold", pad=14)

    stator_span = _radial_span(regions.get("stator"))
    rotor_span = _radial_span(regions.get("rotor"))
    sleeve_span = _radial_span(regions.get("sleeve")) if regions.get("sleeve") is not None else None
    if not (stator_span and rotor_span):
        ax.text(0.5, 0.5, "(no air-gap region)", transform=ax.transAxes, ha="center")
        return

    mag_up_gap = _num(geo.get("magnet_up_gap")) or 0.0
    magnet_top_r = rotor_span[1] - mag_up_gap
    sleeve_t = _num(geo.get("sleeve_thickness")) or 0.0
    sleeve_or = sleeve_span[1] if sleeve_span else rotor_span[1]
    bore_r = stator_span[0]

    a0, a1 = 55.0, 125.0   # a 70-degree wedge — wide enough to read arcs and
                            # dimension arrows apart, narrow enough that the
                            # radial features fill it rather than a sliver
    r_in = magnet_top_r * 0.90
    r_out = bore_r * 1.10

    # Coloured bands — the SAME styling as the sector/ring, purely for
    # orientation (which band is rotor iron, which is the sleeve, which is
    # open air gap); the DIMENSIONS below are what actually carries the
    # numbers/names.
    ax.add_patch(mpatches.Wedge((0, 0), rotor_span[1], a0, a1,
                                 width=rotor_span[1] - r_in, **{k: v for k, v in _PART_STYLE["rotor"].items() if k != "zorder"}))
    if sleeve_span:
        ax.add_patch(mpatches.Wedge((0, 0), sleeve_or, a0, a1,
                                     width=sleeve_or - rotor_span[1],
                                     **{k: v for k, v in _PART_STYLE["sleeve"].items() if k != "zorder"}))
    ax.add_patch(mpatches.Wedge((0, 0), bore_r, a0, a1, width=bore_r - sleeve_or,
                                 fc="none", ec="#999999", lw=1.2, ls=(0, (3, 3))))
    ax.add_patch(mpatches.Wedge((0, 0), r_out, a0, a1, width=r_out - bore_r,
                                 **{k: v for k, v in _PART_STYLE["stator"].items() if k != "zorder"}))
    for r in (magnet_top_r, rotor_span[1], sleeve_or, bore_r):
        ax.add_patch(mpatches.Arc((0, 0), 2 * r, 2 * r, angle=0, theta1=a0, theta2=a1,
                                   color="black", lw=1.3, zorder=5))

    r_lbl = r_out * 1.55
    # Three angles across the wedge for the three dimensions, so none of
    # their radial arrows or escaped labels sit on top of another.
    a_up, a_sl, a_gap = a0 + 10, (a0 + a1) / 2.0, a1 - 10

    _dim_radial(ax, magnet_top_r, rotor_span[1], a_up,
                _label("magnet_up_gap", geo.get("magnet_up_gap"), "mm", "float"),
                r_label=r_lbl, fontsize=24)
    drawn.add("magnet_up_gap")
    if sleeve_span:
        _dim_radial(ax, rotor_span[1], sleeve_or, a_sl,
                    _label("sleeve_thickness", sleeve_t, "mm", "float"),
                    r_label=r_lbl, fontsize=24)
        drawn.add("sleeve_thickness")
    _dim_radial(ax, sleeve_or, bore_r, a_gap,
                _label("air_gap", geo.get("air_gap"), "mm", "float"),
                r_label=r_lbl, fontsize=24)
    drawn.add("air_gap")

    # Which circle is which — short plain leaders, no schema key (these are
    # real geometry radii, not separate dimension arrows of their own; each
    # IS one of the 5 radii on the main radii picture).
    for r, name, ang in ((magnet_top_r, "magnet OD", a0 - 22),
                         (sleeve_or if sleeve_span else rotor_span[1],
                          "sleeve OD" if sleeve_span else "rotor OD", a0 - 6),
                         (bore_r, "bore (stator_inner_radius)", a1 + 14)):
        p = (r * math.cos(math.radians(ang)), r * math.sin(math.radians(ang)))
        out = (r_out * 1.15 * math.cos(math.radians(ang)), r_out * 1.15 * math.sin(math.radians(ang)))
        ha = "left" if math.cos(math.radians(ang)) >= 0 else "right"
        ax.annotate(name, xy=p, xytext=out, fontsize=16, family=_MONO,
                    color="#333333", ha=ha, va="center",
                    arrowprops=dict(arrowstyle="-", color="#888888", lw=0.7, shrinkA=0, shrinkB=2),
                    bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.9))

    lim = r_lbl * 1.5
    ax.set_xlim(-lim * 0.75, lim); ax.set_ylim(-lim * 0.15, lim)


def _draw_wire_inset(ax, geo: Dict[str, Any], regions: Dict[str, Any], coil, drawn: set) -> None:
    """One conductor, zoomed — wire_width across it, wire_height along it."""
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title("one conductor (zoom)", fontsize=18, color="#555555", pad=6)

    if coil is None:
        # Fall back to the nearest-to-top coil in the FULL (unclipped) set —
        # still a real conductor, just not one inside the enlarged sector.
        coils = list(regions.get("coils") or [])
        coil = _nearest_to_angle(coils, 90.0)
    if coil is None:
        ax.text(0.5, 0.5, "(no winding)", transform=ax.transAxes, ha="center", fontsize=18)
        return

    for p in _poly_patches(coil, **_PART_STYLE["coil"]):
        ax.add_patch(p)
    b = _bounds(coil)
    if not b:
        return
    minx, miny, maxx, maxy = b
    w, h = maxx - minx, maxy - miny
    pad_w, pad_h = max(w, 1e-6) * 1.3, max(h, 1e-6) * 1.3

    # The wire itself is tiny next to a 22pt label — pushed much further out
    # than the offsets below implied at the old (7pt) size, or the two
    # labels' own text (one horizontal above, one rotated to the right)
    # overlapped in the middle.
    _dim_h(ax, minx, maxx, maxy + 0.55 * pad_h,
           _label("wire_width", geo.get("wire_width"), "mm", "float"))
    _dim_v(ax, miny, maxy, x_dim=maxx + 0.75 * pad_w,
           label=_label("wire_height", geo.get("wire_height"), "mm", "float"))
    drawn.update({"wire_width", "wire_height"})

    cx, cy = (minx + maxx) / 2.0, (miny + maxy) / 2.0
    half = max(pad_w, pad_h) * 3.2   # more headroom for the now much bigger
                                      # (22pt) labels in this small inset
    ax.set_xlim(cx - half, cx + half); ax.set_ylim(cy - half, cy + half)


def _draw_axial_view(ax, geo: Dict[str, Any], regions: Dict[str, Any], drawn: set,
                      compact: bool = False) -> None:
    """A schematic AXIAL (side) view — stack length is not a shape in the
    radial cross-section at all, so it gets its own small picture rather than
    a leader pointing at nothing.  Illustrative proportions (this is not a
    section of anything CadQuery built); the stator/rotor RADII are the real
    ones so the sketch is at least the right aspect, end turns are labelled as
    what they are — not a geometry-schema parameter, shown for orientation
    only.  ``compact`` — smaller title/fonts/margins, for use as a corner
    inset inside the sector figure rather than its own full panel."""
    import matplotlib.patches as mpatches
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title("axial (side) view" if compact else "Axial (side) view — stack length",
                 fontsize=18 if compact else 9.5, color="#555555" if compact else None,
                 pad=6 if compact else 2)

    stator_span = _radial_span(regions.get("stator"))
    r_out = stator_span[1] if stator_span else 25.0
    r_in = stator_span[0] if stator_span else 15.0
    L = 1.6 * r_out            # illustrative stack length, drawn to scale of r_out

    # Stator ring, side-on: two parallel bars (top/bottom half of the ring).
    for sign in (1, -1):
        y0, y1 = sign * r_in, sign * r_out
        ax.add_patch(mpatches.Rectangle((0, min(y0, y1)), L, abs(y1 - y0),
                                         fc="#dbe6f6", ec="black", lw=0.8))
    # Rotor, side-on, centred.
    ax.add_patch(mpatches.Rectangle((0, -r_in), L, 2 * r_in, fc="#f7ddc4", ec="black", lw=0.7))
    # End-turn overhang — winding, not geometry-schema; drawn for orientation.
    for x0, sign in ((0, -1), (L, 1)):
        ax.add_patch(mpatches.Ellipse((x0 + sign * 0.12 * L, 0), 0.24 * L, 1.9 * r_out,
                                       fc="#fcefa1", ec="#8a7a2a", lw=0.5, alpha=0.7))
    if not compact:
        ax.text(L / 2.0, r_out * 1.55, "end turns (winding overhang — not a "
                "geometry-schema parameter)", fontsize=6.6, family=_MONO,
                ha="center", va="bottom", color="#8a7a2a")

    _dim_h(ax, 0, L, -r_out * 1.35,
           _label("motor_length", geo.get("motor_length"), "mm", "float"),
           fontsize=22 if compact else 8.5, text_side="below")
    drawn.add("motor_length")

    ax.set_xlim(-0.35 * L, 1.35 * L)
    ax.set_ylim(-r_out * (2.0 if compact else 1.9), r_out * (1.55 if compact else 1.9))


_GROUP_HEADER_COLOR = "#4a3f80"


def _draw_legend(ax, geo: Dict[str, Any], schema: Dict[str, dict],
                  groups: List[dict], drawn: set, *, n_cols: int = 2,
                  key_font: float = 16, meaning_font: float = 16,
                  header_font: float = 20, title_font: float = 20) -> None:
    """Every schema key, grouped, drawn or not — a COMPACT table: one line
    per parameter (key, a one-line meaning truncated to fit, drawn/legend
    flag). The long, multi-sentence description used to be printed in full
    and wrapped to 2-3 lines per row; that belongs in the Geometry tab's own
    HelpTip tooltip, not on this picture — here it only has to say which
    knob a name is, at a glance.
    """
    ax.axis("off")
    ax.set_title("Legend — every parameter (name · meaning · drawn?)",
                 fontsize=title_font, loc="left", pad=10)

    order = {g.get("id"): g.get("order", 99) for g in groups}
    label_of = {g.get("id"): g.get("label", g.get("id")) for g in groups}
    by_group: Dict[str, List[str]] = {}
    for key in schema:
        grp = schema[key].get("group", "other")
        by_group.setdefault(grp, []).append(key)

    Row = Tuple[str, str, Any]     # kind, key/header text, extra (flag, raw description) | None
    KEY_FONT, MEANING_FONT = key_font, meaning_font
    # Sized from the ACTUAL rendered glyph widths (a renderer, from the
    # already-built figure — this runs after the sector/insets are drawn),
    # not an assumed points-per-character constant: two guesses at that
    # constant (0.60 and then a wider one) both still let "meaning" run into
    # the flag column for some rows, because the real (proportional,
    # non-monospace) font is simply not that predictable at a few points'
    # size. Measuring is the only way to be sure it fits.
    renderer = ax.figure.canvas.get_renderer()

    def _text_w_axesfrac(s: str, fontsize: float, family: Optional[str] = None) -> float:
        t = ax.text(0, 0, s, transform=ax.transAxes, fontsize=fontsize,
                     family=family, alpha=0)
        w_px = t.get_window_extent(renderer=renderer).width
        t.remove()
        (x0_fig, _), (x1_fig, _) = ax.transAxes.inverted().transform(
            [(0, 0), (w_px, 0)])
        return x1_fig - x0_fig

    def _fit_meaning(desc: str, avail_frac: float) -> str:
        if avail_frac <= 0:
            return ""
        if _text_w_axesfrac(desc, MEANING_FONT) <= avail_frac:
            return desc
        lo, hi = 0, len(desc)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            cand = desc[:mid].rstrip() + "…"
            if _text_w_axesfrac(cand, MEANING_FONT) <= avail_frac:
                lo = mid
            else:
                hi = mid - 1
        return (desc[:lo].rstrip() + "…") if lo > 0 else ""

    group_rows: List[List[Row]] = []
    for grp in sorted(by_group, key=lambda g: order.get(g, 99)):
        rows: List[Row] = [("header", label_of.get(grp, grp), None)]
        for key in by_group[grp]:
            meta = schema[key]
            flag = "drawn" if key in drawn else "—"
            desc = (meta.get("description") or meta.get("label") or "").strip()
            desc = desc.split(". ")[0].split(" — ")[0].split("(")[0].strip()
            rows.append(("item", key, (flag, desc)))
        group_rows.append(rows)

    # Greedy bin-packing: a WHOLE group onto whichever column is currently
    # lighter — never split a header from its own items.
    weight = {"header": 1.4, "item": 1.0}
    col_weights = [0.0] * n_cols
    columns: List[List[Row]] = [[] for _ in range(n_cols)]
    for rows in sorted(group_rows, key=lambda rs: -sum(weight[r[0]] for r in rows)):
        ci = min(range(n_cols), key=lambda i: col_weights[i])
        columns[ci].extend(rows)
        col_weights[ci] += sum(weight[r[0]] for r in rows)

    col_w = 1.0 / n_cols
    col_x = [i * col_w for i in range(n_cols)]
    line_h = (key_font / 72.0) * 2.2 / max(ax.get_position().height
                                            * ax.figure.get_size_inches()[1], 1e-6)
    key_gap = 0.010 * (2.0 / n_cols)         # fixed gap between the key and its meaning
    flag_x = col_w * 0.93                     # flag sits here, right-aligned — FIXED,
                                               # so the meaning lane's available width
                                               # is whatever is left after this row's
                                               # own (variable-width) key
    flag_gap = 0.008 * (2.0 / n_cols)
    for ci, col in enumerate(columns):
        x0 = col_x[ci]
        y = 0.955
        for kind, text, extra in col:
            if kind == "header":
                y -= line_h * 0.5
                ax.text(x0, y, text, transform=ax.transAxes, fontsize=header_font,
                        fontweight="bold", color=_GROUP_HEADER_COLOR,
                        va="top", ha="left")
                y -= line_h * 1.3
            else:
                flag, desc = extra
                ax.text(x0, y, text, transform=ax.transAxes, fontsize=KEY_FONT,
                        family=_MONO, va="top", ha="left", color="#111111")
                meaning_x0 = x0 + _text_w_axesfrac(text, KEY_FONT, _MONO) + key_gap
                # The boundary is flag_x MINUS the flag's OWN width (it is
                # right-aligned there, so "drawn" — much wider than "—" —
                # actually starts well before flag_x; reserving a fixed gap
                # from flag_x itself still let meaning run under "drawn"'s
                # own glyphs, which is exactly the rows that were touching).
                # The 0.92x on top is slack for the exact-fit binary search's
                # own zero-clearance-by-construction result.
                flag_w = _text_w_axesfrac(flag, KEY_FONT)
                avail = ((x0 + flag_x - flag_w - flag_gap) - meaning_x0) * 0.92
                meaning = _fit_meaning(desc, avail)
                ax.text(meaning_x0, y, meaning, transform=ax.transAxes,
                        fontsize=MEANING_FONT, va="top", ha="left", color="#555555")
                ax.text(x0 + flag_x, y, flag, transform=ax.transAxes,
                        fontsize=KEY_FONT, va="top", ha="right",
                        color="#2f7a3a" if flag == "drawn" else "#b0b0b0")
                y -= line_h


def build_dimension_sheet(geo: Dict[str, Any], schema: Dict[str, dict],
                           groups: List[dict], *, fmt: str = "svg",
                           show_values: bool = True, layout: str = "full",
                           title: str = "Geometry dimension sheet") -> bytes:
    """Render the annotated dimension sheet for ``geo`` as SVG or PNG bytes.

    ``geo`` — a FULL geometry dict (primaries + derived, e.g.
    ``get_current_geometry().to_dict()``) — the exact shape every other
    ``/api/geometry/mesh*`` route already consumes.
    ``schema`` — ``geometry_schema_meta()`` (name -> {label, unit, type,
    group, description, ...}); ``groups`` — the ``parameter_groups`` list, as
    ``GET /api/geometry/schema`` serves both.
    ``layout`` — ``"full"`` (default; the per-machine dev route): the whole
    ring plus two small zoomed details.  ``"sector"`` (the static Help
    picture's first tab): ONE big enlarged sector (bore to sleeve) — small
    features are actually legible at that scale, which the full ring was
    not.  ``"radii"`` (the Help picture's second tab): every diameter/radius
    on one big ring, plus a magnified callout of the air-gap zone
    (air_gap/magnet_up_gap/sleeve_thickness, a couple of mm next to a
    100+ mm OD — illegible at the sector's own scale).
    ``show_values`` — True (the per-machine dev route) prints
    ``key = value unit`` beside every dimension and legend row; False (the
    static Help picture, one for every model) prints just ``key`` — a number
    from ONE representative machine has no business on a page every machine
    shares.
    """
    fmt = (fmt or "svg").lower()
    if fmt not in ("svg", "png"):
        raise ValueError(f"unsupported format: {fmt!r} — use 'svg' or 'png'")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if fmt == "svg":
        # Keep the label TEXT selectable/searchable rather than converted to
        # vector outlines — the whole point of shipping SVG here.
        matplotlib.rcParams["svg.fonttype"] = "none"

    global _SHOW_VALUES
    _SHOW_VALUES = bool(show_values)
    try:
        from motor_ai_sim.cadquery_geometry import CadQueryMotor
        motor = CadQueryMotor()
        motor.set_parameters(dict(geo))
        regions = motor.get_2d_polygons()

        drawn: set = set()

        if layout == "radii":
            # The second Help-window tab: ALL diameters/radii on one big
            # ring (own page — the sector page's corner inset was too small
            # to letter, per the owner), plus a magnified callout of the
            # air-gap zone (air_gap/magnet_up_gap/sleeve_thickness are a
            # couple of mm next to a 100+ mm OD, illegible on the same
            # scale as the ring itself) — linked to it by two thin lines,
            # the usual "detail view" drafting convention.
            fig = plt.figure(figsize=(30.0, 20.0), facecolor="white")
            gs = fig.add_gridspec(1, 2, width_ratios=[0.56, 0.44], wspace=0.08,
                                  left=0.02, right=0.98, top=0.93, bottom=0.03)
            ax_ring = fig.add_subplot(gs[0, 0])
            ax_gap = fig.add_subplot(gs[0, 1])

            try:
                _draw_ring_inset(ax_ring, geo, regions, drawn, big=True)
            except Exception:
                log.exception("dimension sheet: radii ring failed")
            try:
                _draw_gap_callout(ax_gap, geo, regions, drawn)
            except Exception:
                log.exception("dimension sheet: gap callout failed")

            # Two thin connector lines, ring -> callout — a small marker
            # circle on the ring at the gap zone, joined at both ends to the
            # callout axes' own left edge (ConnectionPatch spans the two
            # SEPARATE axes directly, in DATA/axes coordinates each, which is
            # exactly the "linked by two thin lines" ask).
            try:
                import matplotlib.patches as mpatches
                stator_span_r = _radial_span(regions.get("stator"))
                rotor_span_r = _radial_span(regions.get("rotor"))
                if stator_span_r and rotor_span_r:
                    mark_r = (stator_span_r[0] + rotor_span_r[1]) / 2.0
                    a_lo, a_hi = math.radians(50), math.radians(130)
                    p_lo = (mark_r * math.cos(a_lo), mark_r * math.sin(a_lo))
                    p_hi = (mark_r * math.cos(a_hi), mark_r * math.sin(a_hi))
                    ax_ring.add_patch(mpatches.Circle((0, 0), mark_r, fill=False,
                                                       ec="#aaaaaa", lw=1.0,
                                                       ls=(0, (2, 2)), zorder=4))
                    for p_end, corner in ((p_lo, (0.0, 0.0)), (p_hi, (0.0, 1.0))):
                        con = mpatches.ConnectionPatch(
                            xyA=p_end, coordsA=ax_ring.transData,
                            xyB=corner, coordsB=ax_gap.transAxes,
                            color="#aaaaaa", lw=1.0, ls=(0, (2, 2)), zorder=1)
                        fig.add_artist(con)
            except Exception:
                log.exception("dimension sheet: ring-to-callout connector failed")
        elif layout == "sector":
            # Wide/tall enough that the sector alone rasterises well past
            # 2400 px at 150 dpi and fills ~70% of the sheet's WIDth and
            # most of its height — the sector is THE picture. The three
            # small insets are SIBLING subplots in a narrower right column
            # (not carved out of the sector axes' own data range — nesting
            # them inside fought `aspect="equal"` for the same pixels and
            # lost, see ``_draw_sector``'s comment); the legend moves to its
            # own full-width row below both, since it needed far more room
            # once its own text grew to a legible size.
            fig = plt.figure(figsize=(34.0, 34.0), facecolor="white")
            gs = fig.add_gridspec(4, 2, width_ratios=[0.70, 0.30],
                                  height_ratios=[1.0, 1.0, 1.0, 1.35],
                                  wspace=0.05, hspace=0.26,
                                  left=0.01, right=0.99, top=0.965, bottom=0.012)
            ax_sector = fig.add_subplot(gs[0:3, 0])
            ax_ring   = fig.add_subplot(gs[0, 1])
            ax_wire   = fig.add_subplot(gs[1, 1])
            ax_axial  = fig.add_subplot(gs[2, 1])
            ax_legend = fig.add_subplot(gs[3, :])

            coil = None
            try:
                coil = _draw_sector(ax_sector, geo, regions, drawn)
            except Exception:
                log.exception("dimension sheet: sector view failed")
            try:
                _draw_ring_inset(ax_ring, geo, regions, drawn)
                _draw_wire_inset(ax_wire, geo, regions, coil, drawn)
                _draw_axial_view(ax_axial, geo, regions, drawn, compact=True)
            except Exception:
                log.exception("dimension sheet: sector insets failed")
            _draw_legend(ax_legend, geo, schema, groups, drawn, n_cols=4,
                         key_font=16, meaning_font=16, header_font=20, title_font=20)
        else:
            fig = plt.figure(figsize=(16.0, 10.5), facecolor="white")
            gs = fig.add_gridspec(3, 3, width_ratios=[2.3, 1.35, 1.55],
                                  height_ratios=[1.0, 1.0, 0.55], wspace=0.32, hspace=0.4,
                                  left=0.02, right=0.98, top=0.94, bottom=0.03)
            ax_main = fig.add_subplot(gs[0:2, 0])
            ax_stator_detail = fig.add_subplot(gs[0, 1])
            ax_rotor_detail = fig.add_subplot(gs[1, 1])
            ax_axial = fig.add_subplot(gs[2, 0:2])
            ax_legend = fig.add_subplot(gs[:, 2])

            try:
                _draw_main(ax_main, geo, regions, drawn)
            except Exception:
                log.exception("dimension sheet: main view failed")
            try:
                _draw_stator_detail(ax_stator_detail, geo, regions, drawn)
            except Exception:
                log.exception("dimension sheet: stator detail view failed")
            try:
                _draw_rotor_detail(ax_rotor_detail, geo, regions, drawn)
            except Exception:
                log.exception("dimension sheet: rotor detail view failed")
            try:
                _draw_axial_view(ax_axial, geo, regions, drawn)
            except Exception:
                log.exception("dimension sheet: axial view failed")
            _draw_legend(ax_legend, geo, schema, groups, drawn, n_cols=2,
                         key_font=6.5, meaning_font=6.1, header_font=8.6, title_font=9.5)

        fig.suptitle(title, fontsize=30 if layout in ("sector", "radii") else 13,
                     fontweight="bold", x=0.02, ha="left")

        buf = io.BytesIO()
        save_kw: Dict[str, Any] = dict(format=fmt, facecolor="white")
        if fmt == "png":
            save_kw["dpi"] = 150
        fig.savefig(buf, **save_kw)
        plt.close(fig)
        return buf.getvalue()
    finally:
        _SHOW_VALUES = True
