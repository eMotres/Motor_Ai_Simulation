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
           color: str = "#5c4aa0", fontsize: float = 7.6,
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
           color: str = "#5c4aa0", fontsize: float = 7.6,
           text_side: str = "right") -> None:
    """The vertical counterpart of ``_dim_h`` — extension lines horizontal,
    arrow vertical.  For this module's roughly-90°-centred sector, "vertical"
    is "radial" to a good approximation over the modest angular span drawn,
    so this doubles as the radial dimension for in-sector features (the
    small full-ring inset uses true polar arrows for the coarse OD/ID/etc.
    dimensions instead — see ``_dim_radial``)."""
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
    ha = "left" if text_side == "right" else "right"
    ax.text(x_dim, (y0 + y1) / 2.0, "  " + label if text_side == "right" else label + "  ",
            fontsize=fontsize, family=_MONO, color="#222222", ha=ha, va="center",
            rotation=90, rotation_mode="anchor", zorder=7,
            bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.92))


def _dim_radial(ax, r0: float, r1: float, angle_deg: float, label: str, *,
                 color: str = "#5c4aa0", fontsize: float = 7.2) -> None:
    """A radial dimension arrow from ``r0`` to ``r1`` at ``angle_deg`` about
    the origin — the small full-ring inset's OD/ID/etc. dimensions, drawn
    with a true polar arrow rather than the sector's flat approximation."""
    a = math.radians(angle_deg)
    p0 = (r0 * math.cos(a), r0 * math.sin(a))
    p1 = (r1 * math.cos(a), r1 * math.sin(a))
    ax.annotate("", xy=p1, xytext=p0,
                arrowprops=dict(arrowstyle="<->", color=color, lw=0.9, shrinkA=0, shrinkB=0))
    mid = ((p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0)
    ax.text(mid[0], mid[1], label, fontsize=fontsize, family=_MONO, color="#222222",
            ha="center", va="center",
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
    x_min, x_max = -half_w * 1.20, half_w * 1.20
    y_min, y_max = -r_max * 0.04, r_max * 1.12
    BOX_ASPECT = 16.57 / 17.30   # sector column / full height — build_dimension_sheet's
                                 # gridspec, layout="sector" (update together if either changes)
    nat_x, nat_y = x_max - x_min, y_max - y_min
    if nat_x / nat_y > BOX_ASPECT:
        want_y = nat_x / BOX_ASPECT
        pad = (want_y - nat_y) / 2.0
        y_min -= pad; y_max += pad
    else:
        want_x = nat_y * BOX_ASPECT
        x_max += (want_x - nat_x)
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(y_min, y_max)
    ax.set_anchor("W")
    lane = half_w * 0.14     # one dimension-line "lane" width — successive
                             # parallel dimensions step out by this much, the
                             # standard drafting fix for several stacked
                             # dimensions sharing one side of a part.

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
    slot_hs_h = min(0.06 * (stator_span[1] - stator_span[0]), (cmaxy - cminy) * 0.18) or 0.5
    _dim_v(ax, y_bore_tip, y_bore_tip + slot_hs_h, x_dim=cminx - 2.2 * lane,
           label=_label("slot_hs", geo.get("slot_hs"), "mm", "float"),
           text_side="left", color="#1f8a5f")
    drawn.add("slot_hs")

    # slot_height — the slot's own (real, envelope) radial span.
    _dim_v(ax, cminy, cmaxy, x_dim=cminx - lane,
           label=_label("slot_height", geo.get("slot_height"), "mm", "float"))
    drawn.add("slot_height")

    # core_thickness — yoke material above the slot, on the tooth centreline.
    tooth_cx = (tooth_yoke[0] + tooth_yoke[1]) / 2.0 if tooth_yoke else 0.0
    _dim_v(ax, cmaxy, stator_span[1], x_dim=tooth_cx,
           label=_label("core_thickness", geo.get("core_thickness"), "mm", "float"))
    drawn.add("core_thickness")

    # ═══ 2. Air gap / sleeve / magnet — each its own lane so none of these
    # radial dimensions, all clustered around the same small angular wedge,
    # touches its neighbour ════════════════════════════════════════════════
    if gap_span:
        _dim_v(ax, gap_span[0], gap_span[1], x_dim=-3.4 * lane,
               label=_label("air_gap", geo.get("air_gap"), "mm", "float"), color="#c0392b")
        drawn.add("air_gap")

    sleeve_t = _num(geo.get("sleeve_thickness")) or 0.0
    sleeve_span = _radial_span(sleeve_c) if sleeve_c is not None else None
    if sleeve_span:
        _dim_v(ax, sleeve_span[0], sleeve_span[1], x_dim=4.6 * lane,
               label=_label("sleeve_thickness", sleeve_t, "mm", "float"))
        drawn.add("sleeve_thickness")

    if mag_poly is not None:
        b = _bounds(mag_poly)
        if b:
            mminx, mminy, mmaxx, mmaxy = b
            _dim_v(ax, mminy, mmaxy, x_dim=1.0 * lane,
                   label=_label("magnet_height", geo.get("magnet_height"), "mm", "float"))
            drawn.add("magnet_height")
            if rotor_span:
                _dim_v(ax, mmaxy, rotor_span[1], x_dim=2.6 * lane,
                       label=_label("magnet_up_gap", geo.get("magnet_up_gap"), "mm", "float"))
                drawn.add("magnet_up_gap")
                _dim_v(ax, rotor_span[0], mminy, x_dim=3.6 * lane,
                       label=_label("magnet_down_height", geo.get("magnet_down_height"), "mm", "float"))
                drawn.add("magnet_down_height")
                _dim_v(ax, rotor_span[0], mminy, x_dim=-1.3 * lane,
                       label=_label("rotor_house_height", geo.get("rotor_house_height"), "mm", "float"),
                       text_side="left")
                drawn.add("rotor_house_height")

    if shaft_span and rotor_span:
        _dim_v(ax, shaft_span[1], rotor_span[0], x_dim=-2.3 * lane,
               label=_label("shaft_height", geo.get("shaft_height"), "mm", "float"),
               text_side="left")
        drawn.add("shaft_height")

    # ═══ 3. num_seg — an arc + short label (a count, not a length: no
    # dimension line is possible for it) ════════════════════════════════════
    if geo.get("num_seg"):
        r_arc = stator_span[0] * 0.55
        import matplotlib.patches as mpatches
        a0, a1 = 90 - angle_slot / 2.0, 90 + angle_slot / 2.0
        ax.add_patch(mpatches.Arc((0, 0), 2 * r_arc, 2 * r_arc, angle=0,
                                   theta1=a0, theta2=a1, color="#333333", lw=1.2))
        _leader(ax, _pt(r_arc, a1), (r_arc * 0.25, r_arc * 0.55),
                 "num_seg" if not _SHOW_VALUES else _label("num_seg", geo.get("num_seg"), "", "int"),
                 color="#333333")
        drawn.add("num_seg")

    return coil    # the picked conductor — the wire inset zooms into THIS one


def _draw_ring_inset(ax, geo: Dict[str, Any], regions: Dict[str, Any], drawn: set) -> None:
    """Small full-ring diagram, corner inset — the coarse radii/diameters,
    each a TRUE polar dimension arrow from the axis (``_dim_radial``), at
    angles spread 40° apart so none crosses another."""
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title("radii (full ring)", fontsize=7.3, color="#555555", pad=2)

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
    if not stator_span:
        return
    r_out = stator_span[1]

    _dim_radial(ax, 0, stator_span[1], -60,
                _label("stator_diameter (radius shown)", geo.get("stator_diameter"), "mm", "float"))
    drawn.add("stator_diameter")
    _dim_radial(ax, 0, stator_span[0], -20,
                _label("stator_inner_radius", geo.get("stator_inner_radius"), "mm", "float"))
    drawn.add("stator_inner_radius")
    if rotor_span:
        _dim_radial(ax, 0, rotor_span[1], 160,
                    _label("rotor_outer_radius", geo.get("rotor_outer_radius"), "mm", "float"))
        drawn.add("rotor_outer_radius")
        _dim_radial(ax, 0, max(rotor_span[0], 1.0), 200,
                    _label("rotor_inner_radius", geo.get("rotor_inner_radius"), "mm", "float"))
        drawn.add("rotor_inner_radius")

    lim = r_out * 1.55
    ax.set_xlim(-lim, lim); ax.set_ylim(-lim, lim)


def _draw_wire_inset(ax, geo: Dict[str, Any], regions: Dict[str, Any], coil, drawn: set) -> None:
    """One conductor, zoomed — wire_width across it, wire_height along it."""
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title("one conductor (zoom)", fontsize=7.3, color="#555555", pad=2)

    if coil is None:
        # Fall back to the nearest-to-top coil in the FULL (unclipped) set —
        # still a real conductor, just not one inside the enlarged sector.
        coils = list(regions.get("coils") or [])
        coil = _nearest_to_angle(coils, 90.0)
    if coil is None:
        ax.text(0.5, 0.5, "(no winding)", transform=ax.transAxes, ha="center", fontsize=7)
        return

    for p in _poly_patches(coil, **_PART_STYLE["coil"]):
        ax.add_patch(p)
    b = _bounds(coil)
    if not b:
        return
    minx, miny, maxx, maxy = b
    w, h = maxx - minx, maxy - miny
    pad_w, pad_h = max(w, 1e-6) * 1.3, max(h, 1e-6) * 1.3

    _dim_h(ax, minx, maxx, maxy + 0.18 * pad_h,
           _label("wire_width", geo.get("wire_width"), "mm", "float"), fontsize=7.0)
    _dim_v(ax, miny, maxy, x_dim=maxx + 0.22 * pad_w,
           label=_label("wire_height", geo.get("wire_height"), "mm", "float"), fontsize=7.0)
    drawn.update({"wire_width", "wire_height"})

    cx, cy = (minx + maxx) / 2.0, (miny + maxy) / 2.0
    half = max(pad_w, pad_h) * 0.95
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
                 fontsize=7.3 if compact else 9.5, color="#555555" if compact else None, pad=2)

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
           fontsize=6.6 if compact else 8.5, text_side="below")
    drawn.add("motor_length")

    ax.set_xlim(-0.35 * L, 1.35 * L)
    ax.set_ylim(-r_out * (1.65 if compact else 1.9), r_out * (1.55 if compact else 1.9))


_GROUP_HEADER_COLOR = "#4a3f80"


def _draw_legend(ax, geo: Dict[str, Any], schema: Dict[str, dict],
                  groups: List[dict], drawn: set) -> None:
    """Every schema key, grouped, drawn or not — a COMPACT table: one line
    per parameter (key, a one-line meaning truncated to fit, drawn/legend
    flag). The long, multi-sentence description used to be printed in full
    and wrapped to 2-3 lines per row; that belongs in the Geometry tab's own
    HelpTip tooltip, not on this picture — here it only has to say which
    knob a name is, at a glance, to fit ~34 parameters in a narrow column
    beside a picture that is now the dominant thing on the page.
    """
    ax.axis("off")
    ax.set_title("Legend — every parameter (name · meaning · drawn?)",
                 fontsize=9.5, loc="left")

    order = {g.get("id"): g.get("order", 99) for g in groups}
    label_of = {g.get("id"): g.get("label", g.get("id")) for g in groups}
    by_group: Dict[str, List[str]] = {}
    for key in schema:
        grp = schema[key].get("group", "other")
        by_group.setdefault(grp, []).append(key)

    Row = Tuple[str, str, Any]     # kind, key/header text, extra (flag | one-line meaning)
    ONE_LINE = 34

    def _short(meta: dict) -> str:
        text = (meta.get("description") or meta.get("label") or "").strip()
        text = text.split(". ")[0].split(" — ")[0].split("(")[0].strip()
        return text if len(text) <= ONE_LINE else text[:ONE_LINE - 1].rstrip() + "…"

    group_rows: List[List[Row]] = []
    for grp in sorted(by_group, key=lambda g: order.get(g, 99)):
        rows: List[Row] = [("header", label_of.get(grp, grp), None)]
        for key in by_group[grp]:
            meta = schema[key]
            flag = "drawn" if key in drawn else "—"
            rows.append(("item", key, (flag, _short(meta))))
        group_rows.append(rows)

    # Greedy bin-packing: a WHOLE group onto whichever of 2 columns is
    # lighter — never split a header from its own items.
    n_cols = 2
    weight = {"header": 1.4, "item": 1.0}
    col_weights = [0.0] * n_cols
    columns: List[List[Row]] = [[] for _ in range(n_cols)]
    for rows in sorted(group_rows, key=lambda rs: -sum(weight[r[0]] for r in rows)):
        ci = min(range(n_cols), key=lambda i: col_weights[i])
        columns[ci].extend(rows)
        col_weights[ci] += sum(weight[r[0]] for r in rows)

    line_h = 0.0145
    col_x = [0.0, 0.52]
    key_w = 0.185       # fraction reserved for the key name
    flag_w = 0.05       # fraction reserved for the drawn/— flag, right edge
    for ci, col in enumerate(columns):
        x0 = col_x[ci]
        y = 0.97
        for kind, text, extra in col:
            if kind == "header":
                y -= line_h * 0.5
                ax.text(x0, y, text, transform=ax.transAxes, fontsize=8.6,
                        fontweight="bold", color=_GROUP_HEADER_COLOR,
                        va="top", ha="left")
                y -= line_h * 1.3
            else:
                flag, meaning = extra
                ax.text(x0, y, text, transform=ax.transAxes, fontsize=6.7,
                        family=_MONO, va="top", ha="left", color="#111111")
                ax.text(x0 + key_w, y, meaning, transform=ax.transAxes,
                        fontsize=6.3, va="top", ha="left", color="#555555")
                ax.text(x0 + 0.475, y, flag, transform=ax.transAxes,
                        fontsize=6.3, va="top", ha="right",
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
    picture): ONE big enlarged sector (bore to sleeve) — small features are
    actually legible at that scale, which the full ring was not.
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

        if layout == "sector":
            # Wide and tall enough that the sector column alone rasterises to
            # >=2400 px at 150 dpi (26in * 150 * 0.65 ~= 2535 px) — the sector
            # is THE picture, not one panel among several.  The three small
            # insets (radii, one conductor, axial view) are SIBLING subplots
            # in their own narrow column, not carved out of the sector axes'
            # own data range — nesting them inside fought `aspect="equal"`
            # for the same pixels and lost (see ``_draw_sector``'s comment).
            fig = plt.figure(figsize=(26.0, 18.5), facecolor="white")
            gs = fig.add_gridspec(3, 3, width_ratios=[0.65, 0.13, 0.22],
                                  height_ratios=[1.0, 1.0, 1.0],
                                  wspace=0.10, hspace=0.30,
                                  left=0.01, right=0.99, top=0.955, bottom=0.02)
            ax_sector = fig.add_subplot(gs[:, 0])
            ax_ring   = fig.add_subplot(gs[0, 1])
            ax_wire   = fig.add_subplot(gs[1, 1])
            ax_axial  = fig.add_subplot(gs[2, 1])
            ax_legend = fig.add_subplot(gs[:, 2])

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
            _draw_legend(ax_legend, geo, schema, groups, drawn)
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
            _draw_legend(ax_legend, geo, schema, groups, drawn)

        fig.suptitle(title, fontsize=13, fontweight="bold", x=0.02, ha="left")

        buf = io.BytesIO()
        save_kw: Dict[str, Any] = dict(format=fmt, facecolor="white")
        if fmt == "png":
            save_kw["dpi"] = 150
        fig.savefig(buf, **save_kw)
        plt.close(fig)
        return buf.getvalue()
    finally:
        _SHOW_VALUES = True
