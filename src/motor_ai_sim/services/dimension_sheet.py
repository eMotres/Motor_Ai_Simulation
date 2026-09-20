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
import textwrap
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


def _draw_sector(ax, geo: Dict[str, Any], regions: Dict[str, Any], drawn: set) -> None:
    """ONE enlarged sector (a slot pitch / pole pair, centred at 12 o'clock) —
    every part from bore to sleeve in a single wide crop, so a small feature
    (a slot opening, a magnet corner) is actually legible, per the owner's
    addendum: the full-ring picture was too small to letter.

    The crop is a real boolean ``region.intersection(wedge)`` against each
    Shapely region ``get_2d_polygons()`` returned (see ``_wedge``/``_clip``)
    — a crop of the real section, not a redrawn approximation of one.
    """
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title("One sector, enlarged — bore to sleeve", fontsize=11)

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

    coil = _nearest_to_angle(coils_c, 90.0)
    magnet = _nearest_to_angle(magnets_c, 90.0, key=lambda mp: mp[0])
    mag_poly = magnet[0] if magnet is not None else None

    # ── LEFT column: stator-side leaders, stacked top to bottom ────────────
    x_left = -r_max * 0.62
    left_items: List[Tuple[Tuple[float, float], str]] = []
    if coil is not None:
        b = _bounds(coil)
        if b:
            minx, miny, maxx, maxy = b
            left_items += [
                ((minx, maxy), _label("tooth_width", geo.get("tooth_width"), "mm", "float")),
                ((maxx, miny), _label("tooth2_width", geo.get("tooth2_width"), "mm", "float")),
                ((minx, (miny + maxy) / 2.0), _label("cut_width (slot opening)",
                                                       geo.get("cut_width"), "mm", "float")),
                ((maxx, maxy), _label("slot_hs (opening height)", geo.get("slot_hs"), "mm", "float")),
                (((minx + maxx) / 2.0, miny), _label("slot_height", geo.get("slot_height"), "mm", "float")),
                (((minx + maxx) / 2.0, (miny + maxy) / 2.0),
                 _label("wire_width", geo.get("wire_width"), "mm", "float")),
                ((maxx, (miny + maxy) / 2.0), _label("wire_height", geo.get("wire_height"), "mm", "float")),
            ]
            drawn.update({"tooth_width", "tooth2_width", "cut_width", "slot_hs",
                          "slot_height", "wire_width", "wire_height"})
    if stator_span and rotor_span:
        left_items.append((_pt((stator_span[1] + rotor_span[1]) / 2.0, 90 + span * 0.32),
                            _label("core_thickness (yoke)", geo.get("core_thickness"), "mm", "float")))
        drawn.add("core_thickness")
    left_items.append((_pt(stator_span[1], 90 + span * 0.40),
                        _label("stator_diameter", geo.get("stator_diameter"), "mm", "float")))
    drawn.add("stator_diameter")
    left_items.append((_pt(stator_span[0], 90 + span * 0.40),
                        _label("stator_inner_radius (bore)", geo.get("stator_inner_radius"), "mm", "float")))

    # ── RIGHT column: rotor-side leaders, stacked top to bottom ────────────
    x_right = r_max * 0.62
    right_items: List[Tuple[Tuple[float, float], str]] = []
    if mag_poly is not None:
        b = _bounds(mag_poly)
        if b:
            minx, miny, maxx, maxy = b
            right_items += [
                (((minx + maxx) / 2.0, maxy), _label("magnet_up_gap", geo.get("magnet_up_gap"), "mm", "float")),
                (((minx + maxx) / 2.0, miny), _label("magnet_down_height", geo.get("magnet_down_height"),
                                                       "mm", "float")),
                ((maxx, (miny + maxy) / 2.0), _label("magnet_height", geo.get("magnet_height"), "mm", "float")),
            ]
            drawn.update({"magnet_up_gap", "magnet_down_height", "magnet_height"})
    if rotor_span:
        right_items.append((_pt(rotor_span[1], 90 - span * 0.40),
                             _label("rotor_outer_radius", geo.get("rotor_outer_radius"), "mm", "float")))
        r_ri = rotor_span[0] if rotor_span[0] > 1e-6 else 1.0
        right_items.append((_pt(r_ri, 90 - span * 0.40),
                             _label("rotor_inner_radius", geo.get("rotor_inner_radius"), "mm", "float")))
        if mag_poly is not None and rotor_span:
            right_items.append((_pt(rotor_span[0] + 0.3, 90 - span * 0.30),
                                 _label("rotor_house_height", geo.get("rotor_house_height"), "mm", "float")))
            drawn.add("rotor_house_height")
    if gap_span:
        mid = (gap_span[0] + gap_span[1]) / 2.0
        _dim_line(ax, _pt(gap_span[0], 90), _pt(gap_span[1], 90), "")
        right_items.append((_pt(mid, 90), _label("air_gap", geo.get("air_gap"), "mm", "float")))
        drawn.add("air_gap")
    sleeve_t = _num(geo.get("sleeve_thickness")) or 0.0
    sleeve_span = _radial_span(sleeve_c) if sleeve_c is not None else None
    if sleeve_span:
        right_items.append((_pt((sleeve_span[0] + sleeve_span[1]) / 2.0, 90 - span * 0.18),
                             _label("sleeve_thickness", sleeve_t, "mm", "float")))
        drawn.add("sleeve_thickness")
    if shaft_span:
        right_items.append((_pt((shaft_span[0] + shaft_span[1]) / 2.0, 90 - span * 0.12),
                             _label("shaft_height (rotor bore to shaft)", geo.get("shaft_height"),
                                    "mm", "float")))
        drawn.add("shaft_height")

    def _stack(items, x_col, ha):
        n = len(items)
        if n == 0:
            return
        # Highest anchor first — matches a top-to-bottom label stack to a
        # top-to-bottom feature order, so leaders fan out instead of
        # crossing each other on the way to their anchors.
        items = sorted(items, key=lambda it: -it[0][1])
        y_top, y_bot = r_max * 0.98, -r_max * 0.98
        for i, (anchor, text) in enumerate(items):
            ty = y_top - (y_top - y_bot) * (i + 0.5) / n
            _leader(ax, anchor, (x_col, ty), text, ha=ha, fontsize=7.6)

    _stack(left_items, x_left, "right")
    _stack(right_items, x_right, "left")

    # ── num_seg arc + count, drawn inside the sector near the bore ─────────
    if geo.get("num_seg"):
        r_arc = stator_span[0] * 0.62
        import matplotlib.patches as mpatches
        a0, a1 = 90 - angle_slot / 2.0, 90 + angle_slot / 2.0
        ax.add_patch(mpatches.Arc((0, 0), 2 * r_arc, 2 * r_arc, angle=0,
                                   theta1=a0, theta2=a1, color="#333333", lw=1.2))
        _leader(ax, _pt(r_arc, 90), (0, r_arc * 0.5),
                 "num_seg" if not _SHOW_VALUES else _label("num_seg", geo.get("num_seg"), "", "int"),
                 color="#333333", ha="center")
        drawn.add("num_seg")

    ax.set_xlim(x_left * 1.55, x_right * 1.55)
    ax.set_ylim(-r_max * 1.05, r_max * 1.05)


def _draw_axial_view(ax, geo: Dict[str, Any], regions: Dict[str, Any], drawn: set) -> None:
    """A schematic AXIAL (side) view — stack length is not a shape in the
    radial cross-section at all, so it gets its own small picture rather than
    a leader pointing at nothing.  Illustrative proportions (this is not a
    section of anything CadQuery built); the stator/rotor RADII are the real
    ones so the sketch is at least the right aspect, end turns are labelled as
    what they are — not a geometry-schema parameter, shown for orientation
    only."""
    import matplotlib.patches as mpatches
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title("Axial (side) view — stack length", fontsize=9.5)

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
    ax.text(L / 2.0, r_out * 1.55, "end turns (winding overhang — not a "
            "geometry-schema parameter)", fontsize=6.6, family=_MONO,
            ha="center", va="bottom", color="#8a7a2a")

    _dim_line(ax, (0, -r_out * 1.35), (L, -r_out * 1.35),
               _label("motor_length", geo.get("motor_length"), "mm", "float"),
               text_at=(L / 2.0, -r_out * 1.55))
    drawn.add("motor_length")

    ax.set_xlim(-0.35 * L, 1.35 * L)
    ax.set_ylim(-r_out * 1.9, r_out * 1.9)


_GROUP_HEADER_COLOR = "#4a3f80"


def _draw_legend(ax, geo: Dict[str, Any], schema: Dict[str, dict],
                  groups: List[dict], drawn: set) -> None:
    """Every schema key, grouped, drawn or not.

    Laid out in TWO columns from one flat, PRE-COMPUTED row list rather than
    a single column that runs off the bottom of the axes: a single column at
    a legible font does not fit ~34 parameters with descriptions on one page,
    and rows that silently scroll off the bottom are rows the picture no
    longer actually shows (caught by the "every schema key parses out of the
    SVG text" check — a single column dropped the whole back half of the
    schema this way once already).
    """
    ax.axis("off")
    ax.set_title("Legend — every geometry parameter", fontsize=10, loc="left")

    order = {g.get("id"): g.get("order", 99) for g in groups}
    label_of = {g.get("id"): g.get("label", g.get("id")) for g in groups}
    by_group: Dict[str, List[str]] = {}
    for key in schema:
        grp = schema[key].get("group", "other")
        by_group.setdefault(grp, []).append(key)

    # ── flatten into rows PER GROUP (header + its items), so whole groups —
    # never a header split from its own items — can be bin-packed onto
    # columns by actual rendered weight.
    Row = Tuple[str, str, Any]     # kind, text, extra (flag string | desc line-count | None)
    WRAP_WIDTH = 40
    weight = {"header": 1.6, "item": 1.7}

    def _row_weight(r: Row) -> float:
        kind, _, extra = r
        return weight.get(kind, 1.0) if kind != "desc" else 0.82 * (extra or 1)

    group_rows: List[List[Row]] = []
    for grp in sorted(by_group, key=lambda g: order.get(g, 99)):
        rows: List[Row] = [("header", label_of.get(grp, grp), None)]
        for key in by_group[grp]:
            meta = schema[key]
            value = geo.get(key)
            unit = meta.get("unit", "")
            ptype = meta.get("type", "float")
            flag = "drawn" if key in drawn else "legend only"
            rows.append(("item", _label(key, value, unit, ptype), flag))
            desc_raw = (meta.get("description") or meta.get("label") or "")
            if len(desc_raw) > 180:
                desc_raw = desc_raw[:179] + "…"
            if desc_raw:
                # Wrapped HERE, once, so the bin-packing below (and the draw
                # loop further down) both work off the real line count
                # instead of assuming every description is one line — a
                # description that wraps to 2-3 lines used to overlap
                # whatever row came after it.
                wrapped = textwrap.wrap(desc_raw, width=WRAP_WIDTH) or [desc_raw]
                rows.append(("desc", "\n".join(wrapped), len(wrapped)))
        group_rows.append(rows)

    # Greedy bin-packing: a WHOLE group (header + its items) goes onto
    # whichever column currently carries the least weight.  Splitting a
    # single row across a column boundary (the previous "switch after 55% of
    # a flat target" rule) reliably put one heavy group alone in column 1 and
    # everything else crammed — and overflowing — into column 2.
    n_cols = 3
    col_weights = [0.0] * n_cols
    columns: List[List[Row]] = [[] for _ in range(n_cols)]
    for rows in sorted(group_rows, key=lambda rs: -sum(_row_weight(r) for r in rows)):
        ci = min(range(n_cols), key=lambda i: col_weights[i])
        columns[ci].extend(rows)
        col_weights[ci] += sum(_row_weight(r) for r in rows)

    line_h = 0.021
    col_x = [0.0, 0.345, 0.69]
    col_width = 0.30
    for ci, col in enumerate(columns):
        x0 = col_x[ci]
        y = 0.985
        for kind, text, extra in col:
            if kind == "header":
                y -= line_h * 0.4
                ax.text(x0, y, text, transform=ax.transAxes, fontsize=9,
                        fontweight="bold", color=_GROUP_HEADER_COLOR,
                        va="top", ha="left")
                y -= line_h * 1.35
            elif kind == "item":
                # The flag goes on its OWN line under the key, not appended
                # after it on the same line at a fixed x — a long key
                # ("insulation_thickness", "num_slots_per_segment", …) ran
                # right into "legend only" when both shared a row and the
                # column had to stay this narrow to fit three of them.
                ax.text(x0, y, text, transform=ax.transAxes, fontsize=7.2,
                        family=_MONO, va="top", ha="left", color="#111111")
                y -= line_h * 0.85
                ax.text(x0 + col_width, y, extra, transform=ax.transAxes,
                        fontsize=6.2, va="top", ha="right",
                        color="#2f7a3a" if extra == "drawn" else "#8a8a8a")
                y -= line_h * 0.85
            else:   # desc — pre-wrapped above; `extra` is the line count, so
                     # the next row's y accounts for a 2-3 line description
                     # instead of overlapping it (matplotlib's `wrap=True`
                     # reflows the text but never reports how many lines it
                     # used, which is what caused that overlap originally).
                ax.text(x0 + 0.015, y, text, transform=ax.transAxes,
                        fontsize=6.1, va="top", ha="left", color="#666666",
                        style="italic", linespacing=1.35)
                y -= line_h * 0.95 * (extra or 1)


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
            fig = plt.figure(figsize=(17.5, 11.0), facecolor="white")
            gs = fig.add_gridspec(2, 2, width_ratios=[2.5, 1.5],
                                  height_ratios=[2.6, 1.0], wspace=0.22, hspace=0.28,
                                  left=0.015, right=0.99, top=0.95, bottom=0.03)
            ax_sector = fig.add_subplot(gs[0, 0])
            ax_axial = fig.add_subplot(gs[1, 0])
            ax_legend = fig.add_subplot(gs[:, 1])

            try:
                _draw_sector(ax_sector, geo, regions, drawn)
            except Exception:
                log.exception("dimension sheet: sector view failed")
            try:
                _draw_axial_view(ax_axial, geo, regions, drawn)
            except Exception:
                log.exception("dimension sheet: axial view failed")
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
