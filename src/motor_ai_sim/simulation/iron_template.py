"""Structured (template) iron mesh — deterministic tooth/pole triangulation.

The free gmsh Delaunay of the iron is the LAST ripple-noise source left after
the belt (structured gap) and the harmonic macroelement: rebuilding the mesh
with a 0.005 mm mesh-size nudge re-rolls the tooth triangulation and moves the
RAW ripple by ±2 pp (measured 14–18 % on 24s20p @ γ30/I100).  pole_copy makes
the copies bit-identical but the TEMPLATE itself is still whatever Delaunay
produced.  This module replaces the template with a deterministic mapped grid:

  * the mesh is a pure function of the geometry parameters + a density knob —
    no meshing heuristics, so identical inputs give identical meshes and a
    geometry optimiser sees smooth derivatives;
  * segment topology is FIXED by product decision (Vadim 2026-07-14): a stator
    segment is ALWAYS 6 slots (three 30° slot-pair units) and a rotor segment
    is 5 OR 7 spoke magnets — the block layout below hardcodes that topology;
  * boundary nodes on the stator bore / rotor OD land exactly on the slip
    angular grid, so the belt (structured gap) welds to the iron by identity.

Layout of one 30° STATOR UNIT (wound tooth axis at the unit centre, local
frame = tooth axis along +Y; x mirrored for the left half):

        y=OD ────────────────────────────────  outer arc (fillet_r corners)
        │           YOKE (core_thickness)
        y=slot_bottom ────────────────────────  straight chord
        │ tooth │ coil column │ slot air │ tooth2/2
        y=throat ─────────────────────────────  slot-opening ledge (fill_r2)
        │ tooth boot │  opening air  │ boot2
        y=bore ───────────────────────────────  bore arc (slip grid, fillet_r1)

Only Coons patches (transfinite quads split into triangles) are used, so the
node count per block is an explicit function of the density knob.
"""
from __future__ import annotations

import math
from typing import Dict, List, Tuple

import numpy as np

__all__ = ["stator_unit_frame", "coons_quad", "mesh_blocks", "stator_unit_blocks",
           "TAG_IRON", "TAG_COIL", "TAG_AIR", "TAG_LINER", "TAG_ENAMEL"]

# local block tags (mapped to solver DOM_* by the integration layer)
TAG_IRON, TAG_COIL, TAG_AIR, TAG_LINER, TAG_ENAMEL = 1, 2, 3, 9, 10


# ──────────────────────────────────────────────────────────────────────────────
# Coons patch: quad grid between four boundary polylines.
# ──────────────────────────────────────────────────────────────────────────────
def _resample_polyline(pts: np.ndarray, n: int) -> np.ndarray:
    """n+1 points uniformly by arc length along the polyline pts (k×2)."""
    pts = np.asarray(pts, float)
    if len(pts) == 2:  # straight segment — exact lerp
        t = np.linspace(0.0, 1.0, n + 1)[:, None]
        return pts[0] * (1 - t) + pts[1] * t
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    if s[-1] <= 0:
        return np.repeat(pts[:1], n + 1, axis=0)
    st = np.linspace(0.0, s[-1], n + 1)
    x = np.interp(st, s, pts[:, 0])
    y = np.interp(st, s, pts[:, 1])
    return np.stack([x, y], axis=1)


def coons_quad(south: np.ndarray, east: np.ndarray, north: np.ndarray,
               west: np.ndarray, nx: int, ny: int) -> Tuple[np.ndarray, np.ndarray]:
    """Mapped (transfinite) grid in the curvilinear quad S/E/N/W.

    south/north run WEST→EAST (ny-independent), west/east run SOUTH→NORTH.
    Corners must match: south[0]==west[0], south[-1]==east[0],
    north[0]==west[-1], north[-1]==east[-1].
    Returns (verts (nx+1)*(ny+1)×2 row-major [j*(nx+1)+i], tris m×3) with
    CCW triangles; the caller owns domain tagging.
    """
    S = _resample_polyline(south, nx); N = _resample_polyline(north, nx)
    W = _resample_polyline(west, ny);  E = _resample_polyline(east, ny)
    u = np.linspace(0.0, 1.0, nx + 1)[None, :, None]   # 1×(nx+1)×1
    v = np.linspace(0.0, 1.0, ny + 1)[:, None, None]   # (ny+1)×1×1
    Sb = S[None, :, :]; Nb = N[None, :, :]
    Wb = W[:, None, :]; Eb = E[:, None, :]
    P00 = S[0][None, None, :]; P10 = S[-1][None, None, :]
    P01 = N[0][None, None, :]; P11 = N[-1][None, None, :]
    grid = ((1 - v) * Sb + v * Nb + (1 - u) * Wb + u * Eb
            - ((1 - u) * (1 - v) * P00 + u * (1 - v) * P10
               + (1 - u) * v * P01 + u * v * P11))
    verts = grid.reshape(-1, 2)

    idx = np.arange((nx + 1) * (ny + 1)).reshape(ny + 1, nx + 1)
    a = idx[:-1, :-1].ravel(); b = idx[:-1, 1:].ravel()
    c = idx[1:, 1:].ravel();   d = idx[1:, :-1].ravel()
    # split each quad along the shorter diagonal (better angles on curved blocks)
    p = verts
    d1 = np.linalg.norm(p[a] - p[c], axis=1)
    d2 = np.linalg.norm(p[b] - p[d], axis=1)
    use1 = d1 <= d2
    t1 = np.where(use1[:, None], np.stack([a, b, c], 1), np.stack([a, b, d], 1))
    t2 = np.where(use1[:, None], np.stack([a, c, d], 1), np.stack([b, c, d], 1))
    tris = np.concatenate([t1, t2], axis=0)
    # enforce CCW
    v0 = p[tris[:, 1]] - p[tris[:, 0]]
    v1 = p[tris[:, 2]] - p[tris[:, 0]]
    cw = (v0[:, 0] * v1[:, 1] - v0[:, 1] * v1[:, 0]) < 0
    tris[cw] = tris[cw][:, ::-1]
    return verts, tris


def _arc(r: float, a0: float, a1: float, n: int = 32) -> np.ndarray:
    t = np.linspace(a0, a1, max(2, n))
    return np.stack([r * np.cos(t), r * np.sin(t)], axis=1)


# ──────────────────────────────────────────────────────────────────────────────
# Stator unit frame: all block-corner landmarks of the 30° slot-pair unit,
# derived from the SAME parameter algebra as cadquery_geometry (kept in sync
# by the coverage test against get_2d_polygons).
# ──────────────────────────────────────────────────────────────────────────────
def stator_unit_frame(p: Dict) -> Dict:
    """Landmark coordinates of one 30° unit in the LOCAL frame (tooth on +Y).

    Returns a dict of scalars/curves the block builder consumes; every value
    is a pure function of the geometry parameters (no meshing heuristics).
    """
    outer_r = float(p["stator_outer_radius"]); inner_r = float(p["stator_inner_radius"])
    tooth_w = float(p["tooth_width"]);        tooth2_w = float(p.get("tooth2_width", 4.5))
    cut_w   = float(p["cut_width"]);          core_h   = float(p["core_thickness"])
    wire_w  = float(p["wire_width"]);         ins_w    = float(p["insulation_thickness"])
    wire_dx = float(p["wire_spacing_x"]);     wire_dy  = float(p["wire_spacing_y"])
    wire_h  = float(p["wire_height"]);        n_wires  = int(p["num_wires_per_slot"])
    slot_h  = float(p["slot_height"])
    num_slots = int(p["num_slots"]); half_slots = num_slots // 2
    unit_deg = 360.0 / half_slots                      # 30° for 24 slots

    # winding feasibility clamp — MUST mirror cadquery_geometry/get_2d_polygons
    wh_max = (slot_h - 2.0 * ins_w) / max(1, n_wires) - wire_dy
    if wh_max > 1e-3 and wire_h > wh_max:
        wire_h = wh_max

    # vertical walls (local x, mirrored for the left half)
    x_tooth = tooth_w / 2.0                            # wound-tooth wall
    x_coil0 = x_tooth + ins_w + wire_dx / 2.0          # coil column left (right_x)
    x_coil1 = x_coil0 + wire_w                         # coil column right
    x_cut   = (tooth_w / 2.0 + ins_w * 2.0 + wire_w + wire_dx * 2.0 + tooth2_w)
    half_ang = math.radians(unit_deg / 2.0)
    # slot-opening ledge (fill_r2 circle centre) — same algebra as the cutter
    fill_r2 = ((inner_r + cut_w) * math.sin(half_ang) - x_cut) / (1.0 - math.sin(half_ang))
    rr = inner_r + cut_w + fill_r2                     # ledge circle-centre radius
    y_ledge = rr * math.cos(half_ang)                  # trapezoid shoulder y
    y_slot_bottom = outer_r - core_h                   # slot bottom chord (local y)
    # coil stack (top wire top edge at y_top_c + wire_dy/2 = liner inner face)
    y_wire_top = y_slot_bottom - ins_w - wire_dy / 2.0
    y_wire_bot = y_wire_top - (n_wires - 1) * (wire_h + wire_dy) - wire_h

    return {
        "unit_deg": unit_deg, "half_ang": half_ang,
        "outer_r": outer_r, "inner_r": inner_r,
        "x_tooth": x_tooth, "x_coil0": x_coil0, "x_coil1": x_coil1, "x_cut": x_cut,
        "fill_r2": fill_r2, "rr": rr, "y_ledge": y_ledge,
        "y_slot_bottom": y_slot_bottom,
        "y_wire_top": y_wire_top, "y_wire_bot": y_wire_bot,
        "wire_w": wire_w, "wire_h": wire_h, "wire_dx": wire_dx, "wire_dy": wire_dy,
        "ins_w": ins_w, "n_wires": (0 if wire_h <= 1e-3 else n_wires),
        "core_h": core_h, "cut_w": cut_w,
    }


def _seg(p0, p1) -> np.ndarray:
    return np.array([p0, p1], float)


def _arc_xy(r: float, x0: float, x1: float, n: int = 24, sign: float = 1.0) -> np.ndarray:
    """Arc of radius r sampled between the x-coordinates x0→x1 (upper half,
    y = +sqrt(r²−x²)); local tooth frame has the bore/OD arcs y-up."""
    xs = np.linspace(x0, x1, max(2, n))
    ys = sign * np.sqrt(np.maximum(r * r - xs * xs, 0.0))
    return np.stack([xs, ys], axis=1)


def stator_unit_blocks(p: Dict, density: float = 1.0) -> List[Tuple]:
    """Block list (S,E,N,W,nx,ny,tag) for ONE 30° unit, built as the half
    [tooth axis .. tooth2 axis] mirrored about the tooth2 axis (polar scans
    confirmed the unit is symmetric about it).  Local frame: wound-tooth axis
    along +Y.  Measured topology (24 slots, unit = 30°):

      * wound tooth: VERTICAL walls x = ±tooth_w/2, bore arc → slot bottom;
      * coil slot [x_t .. x_t+slot_w]: OPEN at the bore; liner U + enamel comb
        + wire stack pinned to the flat slot bottom (y = OD − core_thickness);
      * tooth2 on the 15° axis: CLOSED V-notch on its axis — linear wedge
        walls from a virtual apex (r_apex on the axis) opening to the OD, top
        blended into the OD arc by the stator_fillet_r fillet;
      * yoke: between the flat slot bottom chord and the OD arc.

    First pass ignores the sub-mm bore fillets (r1≈0.5) and the fill_r2 apex
    rounding; the coverage harness quantifies the residual (~1 % iron area).
    """
    f = stator_unit_frame(p)
    R_o, R_i = f["outer_r"], f["inner_r"]
    xt = f["x_tooth"]
    ww, wh, wdx, wdy = f["wire_w"], f["wire_h"], f["wire_dx"], f["wire_dy"]
    ins, nw = f["ins_w"], f["n_wires"]
    y_sb = f["y_slot_bottom"]
    y_wt, y_wb = f["y_wire_top"], f["y_wire_bot"]
    slot_w = ww + 2.0 * ins + wdx           # coil-slot width (vertical walls)
    xs2 = xt + slot_w                        # tooth2-side slot wall
    xc0 = xt + ins + wdx / 2.0              # wire column left
    xc1 = xc0 + ww                           # wire column right
    x_env0, x_env1 = xt + ins, xs2 - ins    # liner inner faces
    unit_half = math.radians(15.0)          # tooth2 axis angle
    ca, sa = math.cos(unit_half), math.sin(unit_half)

    # V-notch: its LEFT wall is simply the cutter's vertical edge x = x_cut
    # (the p1s→p2s side of the CadQuery trapezoid) continued to the OD; the
    # right wall is the mirrored vertical of the NEXT unit.  The two verticals
    # meet on the tooth2 axis at r_apex = x_cut / sin(15°) — the closed-V apex
    # (matches the polar scans: half-width(r) = x_cut·cos15° − sin15°·√(r²−x_cut²)).
    x_cut = f["x_cut"]
    apex = (x_cut, x_cut / math.tan(unit_half))
    r_apex = x_cut / sa
    fill_od = float(p.get("stator_fillet_r", 0.0) or 0.0)
    # OD fillet arc between the vertical wall and the OD circle (inside iron):
    # centre C is fill_od left of the wall AND fill_od inside the OD circle.
    if fill_od > 1e-6:
        cx_f = x_cut - fill_od
        cy_f = math.sqrt(max((R_o - fill_od) ** 2 - cx_f ** 2, 0.0))
        y_tan = cy_f                              # tangency on the wall (x=x_cut)
        od_tan = (cx_f * R_o / (R_o - fill_od),   # tangency on the OD circle
                  cy_f * R_o / (R_o - fill_od))
        phi_f0 = math.atan2(x_cut - cx_f, y_tan - cy_f)       # = 90° (wall side)
        phi_f1 = math.atan2(od_tan[0] - cx_f, od_tan[1] - cy_f)
        tf = np.linspace(phi_f0, phi_f1, 7)
        fillet_arc = np.stack([cx_f + fill_od * np.sin(tf),
                               cy_f + fill_od * np.cos(tf)], axis=1)
    else:
        y_tan = math.sqrt(R_o ** 2 - x_cut ** 2)
        od_tan = (x_cut, y_tan)
        fillet_arc = np.array([[x_cut, y_tan], [x_cut, y_tan]])
    phi_od_end = math.atan2(od_tan[0], od_tan[1])   # OD angle of the fillet end

    def n_of(length_mm, lo=1):
        return max(lo, int(round(abs(length_mm) * density)))

    blocks: List[Tuple] = []

    def quad(x0, x1, y0, y1, tag, nx=None, ny=None):
        nx = nx or n_of(x1 - x0); ny = ny or n_of(y1 - y0)
        blocks.append((_seg((x0, y0), (x1, y0)), _seg((x1, y0), (x1, y1)),
                       _seg((x0, y1), (x1, y1)), _seg((x0, y0), (x0, y1)),
                       nx, ny, tag))

    def ybore(x):
        return math.sqrt(max(R_i * R_i - x * x, 0.0))

    # ── 1) wound-tooth half [0..xt] ────────────────────────────────────────
    blocks.append((_arc_xy(R_i, 0.0, xt, n=n_of(xt, 3)),
                   _seg((xt, ybore(xt)), (xt, y_sb)),
                   _seg((0.0, y_sb), (xt, y_sb)),
                   _seg((0.0, R_i), (0.0, y_sb)),
                   n_of(xt, 2), n_of(y_sb - R_i, 4), TAG_IRON))

    # ── 2) coil slot column [xt..xs2] ──────────────────────────────────────
    y_env_bot = y_wb - wdy / 2.0
    blocks.append((_arc_xy(R_i, xt, xs2, n=n_of(slot_w, 3)),
                   _seg((xs2, ybore(xs2)), (xs2, y_env_bot)),
                   _seg((xt, y_env_bot), (xs2, y_env_bot)),
                   _seg((xt, ybore(xt)), (xt, y_env_bot)),
                   n_of(slot_w, 2), n_of(y_env_bot - ybore(xt), 3), TAG_AIR))
    quad(xt, x_env0, y_env_bot, y_sb - ins, TAG_LINER, nx=1)
    quad(x_env1, xs2, y_env_bot, y_sb - ins, TAG_LINER, nx=1)
    quad(xt, xs2, y_sb - ins, y_sb, TAG_LINER, ny=1)
    if nw > 0:
        quad(x_env0, xc0, y_env_bot, y_sb - ins, TAG_ENAMEL, nx=1)
        quad(xc1, x_env1, y_env_bot, y_sb - ins, TAG_ENAMEL, nx=1)
        quad(xc0, xc1, y_wt + wdy / 2.0, y_sb - ins, TAG_ENAMEL, ny=1)
        y = y_wt
        for k in range(nw):
            quad(xc0, xc1, y - wh, y, TAG_COIL,
                 ny=max(1, int(round(wh * density * 2))))
            y2 = y - wh - (wdy if k < nw - 1 else wdy / 2.0)
            quad(xc0, xc1, y2, y - wh, TAG_ENAMEL, ny=1)
            y -= wh + wdy
    else:
        quad(x_env0, x_env1, y_env_bot, y_sb - ins, TAG_AIR)

    # ── 3) tooth2 half + V half-notch (vertical wall x = x_cut) ────────────
    x_axis_bore = R_i * sa                    # unit-axis x at the bore
    y_wall_bore = ybore(x_cut) if x_cut < R_i else 0.0
    # 3a. body below the apex: bore arc → apex arc (radius r_apex)
    th0 = math.asin(min(xs2 / r_apex, 1.0))
    arcS = _arc_xy(R_i, xs2, x_axis_bore, n=n_of(x_axis_bore - xs2, 3))
    ths = np.linspace(th0, unit_half, max(3, n_of(r_apex * (unit_half - th0), 2) + 1))
    arcN_apex = np.stack([r_apex * np.sin(ths), r_apex * np.cos(ths)], axis=1)
    axis_lo = np.array([[x_axis_bore, R_i * ca],
                        [r_apex * sa, r_apex * ca]])
    blocks.append((arcS, axis_lo, arcN_apex,
                   _seg((xs2, ybore(xs2)), (xs2, r_apex * math.cos(th0))),
                   n_of(x_axis_bore - xs2, 3), n_of(r_apex - R_i, 2), TAG_IRON))
    # 3b. body above the apex: slot wall .. vertical V wall, apex arc .. slot bottom
    blocks.append((arcN_apex,
                   _seg(apex, (x_cut, y_sb)),
                   _seg((xs2, y_sb), (x_cut, y_sb)),
                   _seg((xs2, r_apex * math.cos(th0)), (xs2, y_sb)),
                   n_of(x_cut - xs2, 3), n_of(y_sb - apex[1], 3), TAG_IRON))
    # 3c. yoke: slot bottom chord → OD arc, east wall = vertical + OD fillet
    east_yoke = np.concatenate([np.array([[x_cut, y_sb]]), fillet_arc], axis=0)
    arcOD = _arc_xy(R_o, 0.0, od_tan[0], n=max(4, n_of(R_o * phi_od_end, 3)))
    blocks.append((_seg((0.0, y_sb), (x_cut, y_sb)),
                   east_yoke, arcOD,
                   _seg((0.0, y_sb), (0.0, R_o)),
                   n_of(x_cut, 4), n_of(R_o - y_sb, 3), TAG_IRON))
    # 3d. V half-notch air: apex → OD between the wall(+fillet) and the axis
    axis_hi = np.array([[r_apex * sa, r_apex * ca], [R_o * sa, R_o * ca]])
    vwall_full = np.concatenate(
        [np.array([[x_cut, apex[1]]]), fillet_arc], axis=0)
    arcOD_v = _arc_xy(R_o, od_tan[0], R_o * sa,
                      n=max(3, n_of(R_o * (unit_half - phi_od_end), 2)))
    blocks.append((np.array([apex, apex]), axis_hi, arcOD_v, vwall_full,
                   max(2, n_of(R_o * (unit_half - phi_od_end), 2)),
                   n_of(R_o - r_apex, 4), TAG_AIR))

    # ── mirror about the tooth2 axis to complete the 30° unit ─────────────
    a2 = math.radians(90.0 - 15.0)
    Rm = np.array([[math.cos(2 * a2), math.sin(2 * a2)],
                   [math.sin(2 * a2), -math.cos(2 * a2)]])
    mirrored = [(S @ Rm.T, E @ Rm.T, N @ Rm.T, W @ Rm.T, nx, ny, tag)
                for (S, E, N, W, nx, ny, tag) in blocks]
    return blocks + mirrored


def mesh_blocks(blocks: List[Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray,
                                   int, int, int]],
                weld_tol: float = 1e-7) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Mesh a list of Coons blocks (S,E,N,W,nx,ny,tag) and weld shared nodes.

    Returns (verts n×2, tris m×3, tag_per_tri m).  Welding is by coordinate
    rounding (weld_tol) — block boundaries are built from the SAME landmark
    curves, so shared edges coincide exactly by construction.
    """
    all_v: List[np.ndarray] = []; all_t: List[np.ndarray] = []; all_tag: List[np.ndarray] = []
    off = 0
    for (S, E, N, W, nx, ny, tag) in blocks:
        v, t = coons_quad(S, E, N, W, nx, ny)
        all_v.append(v); all_t.append(t + off); all_tag.append(np.full(len(t), tag, np.int16))
        off += len(v)
    V = np.concatenate(all_v); T = np.concatenate(all_t); G = np.concatenate(all_tag)
    key = np.round(V / weld_tol).astype(np.int64)
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    V2 = V[np.sort(first)]
    # map unique-order → sorted-first order
    order = np.argsort(first)
    rank = np.empty_like(order); rank[order] = np.arange(len(order))
    T2 = rank[inv][T]
    # drop degenerate tris (welded corners can collapse a sliver quad edge)
    p0, p1, p2 = V2[T2[:, 0]], V2[T2[:, 1]], V2[T2[:, 2]]
    area2 = (p1[:, 0] - p0[:, 0]) * (p2[:, 1] - p0[:, 1]) - \
            (p1[:, 1] - p0[:, 1]) * (p2[:, 0] - p0[:, 0])
    good = np.abs(area2) > 1e-12
    return V2, T2[good], G[good]
