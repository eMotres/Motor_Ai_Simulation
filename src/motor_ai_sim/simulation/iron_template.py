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
    """ONE 30-deg stator unit as TWO warped tensor grids (premeshed), fully
    conforming by construction — the same treatment as the rotor unit:

      T1 [0 .. x_cut] x [bore arc .. OD arc]: x-columns anchored on every
         vertical wall (tooth, liner, wire column, tooth2 wall x_cut); rows
         blend bore arc -> exact envelope/wire levels -> slot bottom -> OD
         arc.  Domain tags per cell from the (column, row) intervals.
      T2 [x_cut .. 15-deg axis] x [apex .. OD arc]: the V-notch air; columns
         warp from the vertical wall (+ OD fillet arc above y_tan) to the
         unit axis; shares T1's row levels on the wall so the seam welds.

    Unit = half + mirror about the tooth2 axis.  Sub-mm bore fillets (r1)
    and the fill_r2 apex rounding stay deferred (coverage ~ -0.3% iron).
    """
    f = stator_unit_frame(p)
    R_o, R_i = f["outer_r"], f["inner_r"]
    xt = f["x_tooth"]
    ww, wh, wdx, wdy = f["wire_w"], f["wire_h"], f["wire_dx"], f["wire_dy"]
    ins, nw = f["ins_w"], f["n_wires"]
    y_sb = f["y_slot_bottom"]
    y_wt, y_wb = f["y_wire_top"], f["y_wire_bot"]
    slot_w = ww + 2.0 * ins + wdx
    xs2 = xt + slot_w
    xc0 = xt + ins + wdx / 2.0
    xc1 = xc0 + ww
    x_env0, x_env1 = xt + ins, xs2 - ins
    unit_half = math.radians(15.0)
    sa = math.sin(unit_half)
    x_cut = f["x_cut"]
    apex_y = x_cut / math.tan(unit_half)
    fill_od = float(p.get("stator_fillet_r", 0.0) or 0.0)
    if fill_od > 1e-6 and p.get("_od_r"):
        # fillet arc measured from the REAL CadQuery contour (circle fit) —
        # the tangent-circle formula below runs ~0.5 mm off along the OD
        fill_od = float(p["_od_r"])
        cx_f = float(p["_od_cx"]); cy_f = float(p["_od_cy"])
        y_tan = float(p.get("_od_y_tan", cy_f))
        od_tan_x = float(p.get("_od_tan_x", cx_f * R_o / (R_o - fill_od)))
    elif fill_od > 1e-6:
        cx_f = x_cut - fill_od
        cy_f = math.sqrt(max((R_o - fill_od) ** 2 - cx_f ** 2, 0.0))
        y_tan = cy_f
        od_tan_x = cx_f * R_o / (R_o - fill_od)
    else:
        y_tan = math.sqrt(R_o ** 2 - x_cut ** 2)
        od_tan_x = x_cut

    def n_of(L, lo=1):
        return max(lo, int(round(abs(L) * density)))

    def ybore(x):
        return math.sqrt(max(R_i * R_i - x * x, 0.0))

    def yod(x):
        return math.sqrt(max(R_o * R_o - x * x, 0.0))

    # ── column grid (x anchors + fill) ─────────────────────────────────────
    anchors = [0.0, xt, x_env0, xc0, xc1, x_env1, xs2, x_cut]
    xs = [0.0]
    for a, b in zip(anchors[:-1], anchors[1:]):
        k = n_of((b - a) * 1.2, 1)
        xs.extend(np.linspace(a, b, k + 1)[1:])
    xg = np.array(xs)

    # ── row levels (v-parameter bands) ─────────────────────────────────────
    # band A: bore(x) -> envelope bottom (warped rows)
    y_env_bot = y_wb - wdy / 2.0
    nA = max(2, n_of(y_env_bot - ybore(xs2), 2))
    # band B: exact envelope levels (liner/enamel/wires) — FIXED y levels
    levB = [y_env_bot]
    y = y_wb
    for k in range(nw):
        levB.append(y); levB.append(y + wh)
        y += wh + wdy
    levB.append(y_sb - ins)
    levB.append(y_sb)
    levB = sorted(set(round(v, 9) for v in levB))
    if apex_y > levB[0] and apex_y < y_sb:
        levB = sorted(set(levB + [round(apex_y, 9)]))
    # band C: slot bottom -> OD(x) (warped rows)
    nC = max(2, n_of(R_o - y_sb, 2))

    rows = []           # list of callables y(x)
    for i in range(nA):
        t = i / nA
        rows.append(lambda x, t=t: ybore(x) * (1 - t) + y_env_bot * t)
    for lv in levB[:-1]:
        rows.append(lambda x, lv=lv: lv)
    for i in range(nC + 1):
        t = i / nC
        rows.append(lambda x, t=t: y_sb * (1 - t) + yod(x) * t)
    n_rows = len(rows)

    def wall_x(y):
        # tooth2 outer wall: vertical x_cut, blended into the OD fillet arc
        if fill_od > 1e-6 and y > y_tan:
            return cx_f + math.sqrt(max(fill_od ** 2 - (y - cy_f) ** 2, 0.0))
        return x_cut

    V1 = np.empty((n_rows * len(xg), 2))
    iC0 = n_rows - (nC + 1)                   # first row of zone C (yoke blend)
    wall_pts: List[Tuple[float, float]] = []   # tooth2-wall node per row (shared with T2)
    y_top_wall = math.sqrt(max(R_o ** 2 - od_tan_x ** 2, 0.0))  # fillet end on OD
    for i, fy in enumerate(rows):
        ys = np.array([fy(x) for x in xg])
        xr = xg.copy()
        # last column follows the tooth2 wall + OD fillet; in zone C its row
        # levels blend to the FILLET END on the OD (not the vertical's OD).
        if i >= iC0:
            t = (i - iC0) / nC
            yw = y_sb * (1 - t) + y_top_wall * t
        else:
            yw = float(ys[-1])
        xr[-1] = wall_x(yw)
        ys[-1] = yw
        wall_pts.append((xr[-1], yw))
        V1[i * len(xg):(i + 1) * len(xg), 0] = xr
        V1[i * len(xg):(i + 1) * len(xg), 1] = ys
    idx = np.arange(n_rows * len(xg)).reshape(n_rows, len(xg))
    a_ = idx[:-1, :-1].ravel(); b_ = idx[:-1, 1:].ravel()
    c_ = idx[1:, 1:].ravel();   d_ = idx[1:, :-1].ravel()
    T1 = np.concatenate([np.stack([a_, b_, c_], 1), np.stack([a_, c_, d_], 1)])

    # cell tags
    xc_ = 0.5 * (xg[:-1] + xg[1:])
    nx_ = len(xg) - 1
    tag_rows = []
    yb_mid = [0.5 * (rows[i](0.0) + rows[i + 1](0.0)) for i in range(n_rows - 1)]
    for i in range(n_rows - 1):
        ymid_at = lambda x: 0.5 * (rows[i](x) + rows[i + 1](x))
        row_tags = np.empty(nx_, np.int16)
        for j, xm in enumerate(xc_):
            ym = ymid_at(xm)
            if ym >= y_sb - 1e-9:
                tg = TAG_IRON                       # yoke
            elif xm <= xt:
                tg = TAG_IRON                       # wound tooth
            elif xm >= xs2:
                # tooth2 body vs V apex region: above the line through apex it
                # is still iron up to the wall (T2 handles beyond x_cut)
                tg = TAG_IRON
            else:
                # slot interior
                if ym < y_env_bot - 1e-9:
                    tg = TAG_AIR
                elif ym >= y_sb - ins - 1e-9:
                    tg = TAG_LINER                  # liner top strip
                elif xm <= x_env0 or xm >= x_env1:
                    tg = TAG_LINER                  # liner side strips
                elif xm <= xc0 or xm >= xc1:
                    tg = TAG_ENAMEL                 # enamel margins
                else:
                    # wire stack: inside a wire level?
                    tg = TAG_ENAMEL
                    yy = y_wb
                    for k in range(nw):
                        if yy - 1e-9 <= ym <= yy + wh + 1e-9:
                            tg = TAG_COIL
                            break
                        yy += wh + wdy
            row_tags[j] = tg
        tag_rows.append(row_tags)
    G1 = np.concatenate([np.stack(tag_rows).ravel()] * 2)

    # ── T2: V half-notch air [x_cut .. axis] x [apex .. OD] ───────────────
    wallV = [(x_cut, apex_y)] + [(xw, yw) for (xw, yw) in wall_pts
                                 if yw > apex_y + 1e-9]
    nV = len(wallV)
    mcols = max(2, n_of((R_o * sa - x_cut) * 1.2, 2))
    V2 = np.empty((nV * (mcols + 1), 2))
    ca_u = math.cos(unit_half)
    for i, (xw, yw) in enumerate(wallV):
        if i == nV - 1:
            # TOP row: exactly on the OD arc, fillet end → unit axis
            ph0 = math.atan2(od_tan_x, math.sqrt(R_o ** 2 - od_tan_x ** 2))
            ph = np.linspace(ph0, unit_half, mcols + 1)
            V2[i * (mcols + 1):(i + 1) * (mcols + 1), 0] = R_o * np.sin(ph)
            V2[i * (mcols + 1):(i + 1) * (mcols + 1), 1] = R_o * np.cos(ph)
            continue
        # axis point at the same RADIUS as the wall point (keeps rows radial-ish)
        rr_ = math.hypot(xw, yw)
        ax_x, ax_y = rr_ * sa, rr_ * ca_u
        ts = np.linspace(0.0, 1.0, mcols + 1)
        V2[i * (mcols + 1):(i + 1) * (mcols + 1), 0] = xw * (1 - ts) + ax_x * ts
        V2[i * (mcols + 1):(i + 1) * (mcols + 1), 1] = yw * (1 - ts) + ax_y * ts
    idx2 = np.arange(nV * (mcols + 1)).reshape(nV, mcols + 1)
    a2 = idx2[:-1, :-1].ravel(); b2 = idx2[:-1, 1:].ravel()
    c2 = idx2[1:, 1:].ravel();   d2 = idx2[1:, :-1].ravel()
    T2b = np.concatenate([np.stack([a2, b2, c2], 1), np.stack([a2, c2, d2], 1)])
    G2 = np.full(len(T2b), TAG_AIR, np.int16)

    # ── T3: iron wedge UNDER the V apex [wall .. axis] × [bore .. r_apex] ──
    # polar tensor: theta warps from the wall line asin(x_cut/r) to the axis;
    # degenerates to the apex point at r_apex (fan) — conforming with the
    # mirror on the axis; the straight wall seam T-stitches against T1.
    r_apex_r = math.hypot(x_cut, apex_y)
    nw3 = max(2, n_of(r_apex_r - R_i, 2))
    m3 = max(2, mcols // 2)
    V3 = np.empty(((nw3 + 1) * (m3 + 1), 2))
    for i in range(nw3 + 1):
        r = R_i + (r_apex_r - R_i) * i / nw3
        th0 = math.asin(min(x_cut / r, 1.0))
        th = np.linspace(th0, unit_half, m3 + 1)
        V3[i * (m3 + 1):(i + 1) * (m3 + 1), 0] = r * np.sin(th)
        V3[i * (m3 + 1):(i + 1) * (m3 + 1), 1] = r * np.cos(th)
    idx3 = np.arange((nw3 + 1) * (m3 + 1)).reshape(nw3 + 1, m3 + 1)
    a3 = idx3[:-1, :-1].ravel(); b3 = idx3[:-1, 1:].ravel()
    c3 = idx3[1:, 1:].ravel();   d3 = idx3[1:, :-1].ravel()
    T3b = np.concatenate([np.stack([a3, b3, c3], 1), np.stack([a3, c3, d3], 1)])
    G3 = np.full(len(T3b), TAG_IRON, np.int16)

    V = np.concatenate([V1, V2, V3])
    T = np.concatenate([T1, T2b + len(V1), T3b + len(V1) + len(V2)])
    G = np.concatenate([G1, G2, G3])
    p0, p1, p2 = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    ar2 = (p1[:, 0] - p0[:, 0]) * (p2[:, 1] - p0[:, 1]) - \
          (p1[:, 1] - p0[:, 1]) * (p2[:, 0] - p0[:, 0])
    cw = ar2 < 0
    T[cw] = T[cw][:, ::-1]
    keep = np.abs(ar2) > 1e-12
    T = T[keep]; G = G[keep]

    # mirror about the tooth2 axis (15 deg)
    a2m = math.radians(90.0 - 15.0)
    Rm = np.array([[math.cos(2 * a2m), math.sin(2 * a2m)],
                   [math.sin(2 * a2m), -math.cos(2 * a2m)]])
    Vm = V @ Rm.T
    Tm = (T + len(V))[:, ::-1]
    return [("premeshed", np.concatenate([V, Vm]),
             np.concatenate([T, Tm]), np.concatenate([G, G]))]


TAG_MAGNET = 4


def _rotate(V: np.ndarray, ang: float) -> np.ndarray:
    c, s = math.cos(ang), math.sin(ang)
    return V @ np.array([[c, s], [-s, c]])   # CCW rotation of row-vectors


def _weld(V: np.ndarray, T: np.ndarray, G: np.ndarray, tol: float = 1e-6):
    """Merge nodes closer than tol — KDTree pair union (grid-quantisation
    keys split pairs straddling a cell boundary, so NOT round-based)."""
    from scipy.spatial import cKDTree
    parent = np.arange(len(V))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, j in cKDTree(V).query_pairs(tol):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)
    root = np.array([find(i) for i in range(len(V))])
    uniq, inv = np.unique(root, return_inverse=True)
    V2 = V[uniq]
    T2 = inv[T]
    p0, p1, p2 = V2[T2[:, 0]], V2[T2[:, 1]], V2[T2[:, 2]]
    a2 = (p1[:, 0] - p0[:, 0]) * (p2[:, 1] - p0[:, 1]) - \
         (p1[:, 1] - p0[:, 1]) * (p2[:, 0] - p0[:, 0])
    good = np.abs(a2) > 1e-12
    return V2, T2[good], G[good]


def _graded_outer_ring(angles: np.ndarray, r0: float, r1: float, tag: int,
                       closed: bool = True, min_count: int = 64):
    """Air annulus [r0..r1] whose INNER arc conforms 1:1 to `angles` (so it
    welds to the iron OD/bore nodes) but coarsens angularly by 2× each radial
    layer (2:1 graded) out to the boundary — no fine angular grid wasted in
    the far air.  ~6× fewer triangles than a full-resolution ring.  Full-ring
    only; a sector's outer air is already cheap, so it uses the plain ring."""
    ang = np.asarray(angles, float)
    N = len(ang)
    levels = 0
    while (N % (2 ** (levels + 1)) == 0
           and N // (2 ** (levels + 1)) >= min_count and levels < 3):
        levels += 1
    if levels == 0 or not closed:
        return _ring_grid(ang, r0, r1, 2, tag, closed)
    sets = [ang[::(2 ** k)] for k in range(levels + 1)]     # each = prev[::2]
    rs = np.linspace(r0, r1, levels + 1)
    Vs = []; base = [0]
    for k, s in enumerate(sets):
        Vs.append(np.stack([rs[k] * np.sin(s), rs[k] * np.cos(s)], axis=1))
        base.append(base[-1] + len(Vs[-1]))
    V = np.concatenate(Vs)
    tris = []
    for k in range(levels):
        nf = len(sets[k]); nc = len(sets[k + 1]); of = base[k]; oc = base[k + 1]
        for j in range(nc):
            a = of + (2 * j) % nf; m = of + (2 * j + 1) % nf; b = of + (2 * j + 2) % nf
            A = oc + j; B = oc + (j + 1) % nc
            tris += [[a, m, A], [m, b, B], [m, B, A]]
    T = np.array(tris, np.int64)
    p0, p1, p2 = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    cw = ((p1[:, 0] - p0[:, 0]) * (p2[:, 1] - p0[:, 1])
          - (p1[:, 1] - p0[:, 1]) * (p2[:, 0] - p0[:, 0])) < 0
    T[cw] = T[cw][:, ::-1]
    return V, T, np.full(len(T), tag, np.int16)


def _ring_grid(angles: np.ndarray, r0: float, r1: float, n_r: int, tag: int,
               closed: bool = True):
    """Polar quad ring on EXACTLY the given sorted angle set (matches an
    existing boundary node ring 1:1) — verts, tris, tags.  closed=False
    builds an OPEN fan [angles[0]..angles[-1]] (sector wedge)."""
    na = len(angles) if closed else len(angles) - 1
    rs = np.linspace(r0, r1, n_r + 1)
    th = np.concatenate([angles, angles[:1]]) if closed else angles
    V = np.stack([(rs[:, None] * np.sin(th[None, :])).ravel(),
                  (rs[:, None] * np.cos(th[None, :])).ravel()], axis=1)
    idx = np.arange((n_r + 1) * (na + 1)).reshape(n_r + 1, na + 1)
    a = idx[:-1, :-1].ravel(); b = idx[:-1, 1:].ravel()
    c = idx[1:, 1:].ravel();   d = idx[1:, :-1].ravel()
    T = np.concatenate([np.stack([a, b, c], 1), np.stack([a, c, d], 1)])
    p0, p1, p2 = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    cw = ((p1[:, 0] - p0[:, 0]) * (p2[:, 1] - p0[:, 1])
          - (p1[:, 1] - p0[:, 1]) * (p2[:, 0] - p0[:, 0])) < 0
    T[cw] = T[cw][:, ::-1]
    return V, T, np.full(len(T), tag, np.int16)


def _snap_to_contours(V: np.ndarray, T: np.ndarray, G: np.ndarray,
                      polys: Dict, kind: str,
                      protect_r=(), sector_rays=(), frac: float = 0.45) -> np.ndarray:
    """Fillets v2 step 2: project class-boundary nodes onto the REAL CadQuery
    contours (fillet arcs, pocket walls) so the staircase becomes a smooth
    polyline.  Only nodes whose incident cells are all in {IRON, AIR, MAGNET}
    move; nodes on protected circles (belt spec radii, shaft, outer border)
    and on the sector cut rays stay put.  A move is rolled back if it flips
    any incident triangle."""
    try:
        from shapely.ops import nearest_points
        from shapely.geometry import Point
    except Exception:
        return V
    iron_geom = polys.get("stator" if kind == "s" else "rotor")
    if iron_geom is None:
        return V
    boundary = iron_geom.boundary
    mag_bounds = []
    if kind == "r":
        mag_bounds = [mg.boundary for mg, _pol in polys.get("magnets") or []]

    # class-boundary edges among swap-able tags
    swap_tags = (TAG_IRON, TAG_AIR, TAG_MAGNET)
    edge_cls: Dict[Tuple[int, int], set] = {}
    node_tags: Dict[int, set] = {}
    for ti, t in enumerate(T):
        g = int(G[ti])
        for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            edge_cls.setdefault((min(a, b), max(a, b)), set()).add(g)
        for v in t:
            node_tags.setdefault(int(v), set()).add(g)
    cand = set()
    for (a, b), cls in edge_cls.items():
        if len(cls & set(swap_tags)) >= 2:
            cand.add(a); cand.add(b)
    cand = [v for v in cand if node_tags[v] <= set(swap_tags)]
    if not cand:
        return V

    # local scale = shortest incident edge per node
    scale = np.full(len(V), np.inf)
    for (a, b) in edge_cls:
        L = float(np.hypot(*(V[a] - V[b])))
        if L < scale[a]: scale[a] = L
        if L < scale[b]: scale[b] = L

    r_all = np.hypot(V[:, 0], V[:, 1])
    incident: Dict[int, List[int]] = {}
    for ti, t in enumerate(T):
        for v in t:
            incident.setdefault(int(v), []).append(ti)

    def tri_ok(ti, Vw):
        a, b, c = T[ti]
        return abs((Vw[b][0] - Vw[a][0]) * (Vw[c][1] - Vw[a][1])
                   - (Vw[b][1] - Vw[a][1]) * (Vw[c][0] - Vw[a][0])) > 1e-12

    V = V.copy()
    moved = 0
    for v in cand:
        rv = r_all[v]
        if any(abs(rv - pr) < 5e-4 for pr in protect_r):
            continue
        if sector_rays:
            x, y = V[v]
            on_ray = any(abs(x * math.sin(a) - y * math.cos(a)) < 1e-6
                         and (x * math.cos(a) + y * math.sin(a)) > 0
                         for a in sector_rays)
            if on_ray:
                continue
        pt = Point(V[v, 0], V[v, 1])
        if kind == "r" and TAG_MAGNET in node_tags[v]:
            # magnet-wall nodes snap ONLY to the magnet contour — the pocket
            # wall runs a fraction of a mm away and would steal them
            best, dd = None, np.inf
            for mb in mag_bounds:
                q = nearest_points(mb, pt)[0]
                if pt.distance(q) < dd:
                    best, dd = q, pt.distance(q)
            if best is None:
                continue
        else:
            best = nearest_points(boundary, pt)[0]
            dd = pt.distance(best)
        if dd < 1e-9 or dd > frac * scale[v]:
            continue
        old = V[v].copy()
        V[v, 0], V[v, 1] = best.x, best.y
        if all(tri_ok(ti, V) for ti in incident[v]):
            moved += 1
        else:
            V[v] = old
    return V


def _densify_fillets(V, T, G, contours, protect_r=(), sector_rays=(),
                     swap_tags=(TAG_IRON, TAG_AIR, TAG_MAGNET),
                     tol=0.10, min_edge=0.35, max_pass=3):
    """Round the tag-staircase into real fillet arcs: on every class-boundary
    edge (tags differ, both meshable) whose midpoint sits >tol off the nearest
    CadQuery contour — i.e. the contour BOWS (a fillet) — insert a node on the
    contour and split the two incident triangles.  Original corner nodes stay,
    so corner + inserted arc nodes trace the rounding.  Protected circle radii
    (belt spec, shaft, OD) and sector cut rays are never moved onto."""
    try:
        from shapely.geometry import Point
        from shapely.ops import nearest_points
    except Exception:
        return V, T, G
    V = V.astype(float).copy(); T = T.copy(); G = G.copy()
    for _ in range(max_pass):
        edge_tris: Dict[Tuple[int, int], List[int]] = {}
        for ti, t in enumerate(T):
            for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
                edge_tris.setdefault((min(a, b), max(a, b)), []).append(ti)
        inserts = []
        for (a, b), tis in edge_tris.items():
            if len(tis) != 2:
                continue
            g1, g2 = int(G[tis[0]]), int(G[tis[1]])
            if g1 == g2 or g1 not in swap_tags or g2 not in swap_tags:
                continue
            pa, pb = V[a], V[b]
            L = float(np.hypot(*(pb - pa)))
            if L < min_edge:
                continue
            mid = 0.5 * (pa + pb)
            best, dd = None, 1e18
            for cont in contours:
                q = nearest_points(cont, Point(mid[0], mid[1]))[0]
                d = float(np.hypot(q.x - mid[0], q.y - mid[1]))
                if d < dd:
                    dd, best = d, (q.x, q.y)
            if best is None or dd < tol or dd > 0.6 * L:
                continue
            rr = float(np.hypot(*best))
            if any(abs(rr - pr) < 0.02 for pr in protect_r):
                continue
            # a node landing on an endpoint makes a zero-area sliver — skip
            if (np.hypot(best[0] - pa[0], best[1] - pa[1]) < 0.2
                    or np.hypot(best[0] - pb[0], best[1] - pb[1]) < 0.2):
                continue
            if sector_rays:
                bx, by = best
                if any(abs(bx * math.sin(ar) - by * math.cos(ar)) < 1e-4
                       and (bx * math.cos(ar) + by * math.sin(ar)) > 0
                       for ar in sector_rays):
                    continue
            inserts.append((a, b, tis[0], tis[1], best))
        if not inserts:
            break
        drop = set(); new_tris = []; new_tags = []; newV = []
        base = len(V)
        for (a, b, t1, t2, pt) in inserts:
            if t1 in drop or t2 in drop:
                continue
            pidx = base + len(newV); newV.append(pt)
            for ti in (t1, t2):
                c = int([x for x in T[ti] if x != a and x != b][0])
                new_tris.append([a, pidx, c]); new_tags.append(int(G[ti]))
                new_tris.append([pidx, b, c]); new_tags.append(int(G[ti]))
                drop.add(ti)
        if not newV:
            break
        V = np.vstack([V, np.array(newV, float)])
        keep = np.array([i for i in range(len(T)) if i not in drop], int)
        T = np.vstack([T[keep], np.array(new_tris, T.dtype)])
        G = np.concatenate([G[keep], np.array(new_tags, G.dtype)])
        p0, p1, p2 = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
        ar2 = ((p1[:, 0] - p0[:, 0]) * (p2[:, 1] - p0[:, 1])
               - (p1[:, 1] - p0[:, 1]) * (p2[:, 0] - p0[:, 0]))
        cw = ar2 < 0
        T[cw] = T[cw][:, ::-1]
        good = np.abs(ar2) > 1e-10          # drop slivers the split may spawn
        T = T[good]; G = G[good]
    return V, T, G


def template_solver_halves(p: Dict, polys: Dict, outer_air_factor: float = 1.2,
                           density: float = 1.2, n_sectors: int = 1):
    """Solver-ready halves in DOM_* tags: (mesh_s, tags_s, cls_s,
    mesh_r, tags_r, cls_r) — full ring (n_sectors=1) or one [0..360/ns]
    wedge with clone-identical radial cuts.  Coil/magnet domains are
    renumbered by centroid against the CadQuery lists so the winding
    circuit and pole polarities stay identical to the gmsh build."""
    from skfem import MeshTri
    from scipy.spatial import cKDTree
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    DOM_AIR, DOM_STATOR, DOM_ROTOR, DOM_SHAFT, DOM_OUTER = 0, 1, 5, 6, 8
    DOM_LINER, DOM_ENAMEL = 9, 10
    DOM_MAG_BASE, DOM_COIL_BASE = 100, 200

    def _components(T, mask):
        """Connected components of the triangle subset (edge adjacency)."""
        idx = np.where(mask)[0]
        if not len(idx):
            return np.array([]), 0
        edge_map: Dict[Tuple[int, int], int] = {}
        rows = []; cols = []
        for k, ti in enumerate(idx):
            t = T[ti]
            for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
                e = (min(a, b), max(a, b))
                if e in edge_map:
                    rows.append(edge_map[e]); cols.append(k)
                else:
                    edge_map[e] = k
        n = len(idx)
        adj = coo_matrix((np.ones(len(rows)), (rows, cols)), shape=(n, n))
        ncomp, lab = connected_components(adj + adj.T, directed=False)
        return lab, ncomp

    def _centroids(V, T, idx):
        return (V[T[idx, 0]] + V[T[idx, 1]] + V[T[idx, 2]]) / 3.0

    def _refine_by_polys(V, T, G, kind):
        """Fillets v2: re-classify iron<->air (and magnet<->air) cells by the
        REAL CadQuery polygons, so pocket fillets, side gaps, the true vent
        contour, bore r1 and slot-bottom roundings all appear in the tags
        (staircase at the local cell size).  Wires/liner/enamel/markers keep
        their exact template tags."""
        try:
            from shapely import contains_xy
        except Exception:
            return G                          # old shapely: keep template tags
        iron_geom = polys.get("stator" if kind == "s" else "rotor")
        if iron_geom is None:
            return G
        G = G.copy()
        C = (V[T[:, 0]] + V[T[:, 1]] + V[T[:, 2]]) / 3.0
        swap = np.where(np.isin(G, (TAG_IRON, TAG_AIR, TAG_MAGNET)))[0]
        if not len(swap):
            return G
        cx, cy = C[swap, 0], C[swap, 1]
        in_iron = contains_xy(iron_geom, cx, cy)
        new = np.where(in_iron, TAG_IRON, TAG_AIR).astype(G.dtype)
        if kind == "r":
            in_mag = np.zeros(len(swap), bool)
            for mg, _pol in polys.get("magnets") or []:
                in_mag |= contains_xy(mg, cx, cy)
            new[in_mag] = TAG_MAGNET
        G[swap] = new
        return G

    def _finish(V, T, G, kind):
        G = _refine_by_polys(V, T, G, kind)
        tags = np.empty(len(T), np.int16)
        if kind == "s":
            tags[G == TAG_IRON] = DOM_STATOR
            tags[G == TAG_AIR] = DOM_AIR
            tags[G == TAG_AIR + 100] = DOM_OUTER
            tags[G == TAG_LINER] = DOM_LINER
            tags[G == TAG_ENAMEL] = DOM_ENAMEL
            # coils: per-triangle nearest CadQuery coil centroid (wires are
            # thicker than the enamel gaps, so centroids never cross over)
            mask = G == TAG_COIL
            idx = np.where(mask)[0]
            ref = np.array([[c.centroid.x, c.centroid.y] for c in polys["coils"]])
            _, j = cKDTree(ref).query(_centroids(V, T, idx))
            tags[idx] = (DOM_COIL_BASE + j).astype(np.int16)
        else:
            tags[G == TAG_IRON] = DOM_ROTOR
            tags[G == TAG_AIR] = DOM_AIR
            tags[G == TAG_IRON + 100] = DOM_SHAFT
            mask = G == TAG_MAGNET
            idx = np.where(mask)[0]
            ref = np.array([[mg.centroid.x, mg.centroid.y]
                            for mg, _pol in polys["magnets"]])
            _, j = cKDTree(ref).query(_centroids(V, T, idx))
            tags[idx] = (DOM_MAG_BASE + j).astype(np.int16)
        mesh = MeshTri(np.ascontiguousarray(V.T) * 1e-3,     # mm → m (solver units)
                       np.ascontiguousarray(T.T))
        def _cls(x, y, _k=kind):
            return DOM_STATOR if _k == "s" else DOM_ROTOR
        _cls.polys = polys
        return mesh, tags, _cls

    # measure rotor-magnet landmarks from the FIRST CadQuery magnet: the true
    # top radius and the wall angle at that radius (linear fit of the slanted
    # wall away from the corner fillets) — parametric guesses are ~0.1 mm off
    p = dict(p)
    try:
        mg0 = polys["magnets"][0][0]
        Vm = np.asarray(mg0.exterior.coords)
        rm = np.hypot(Vm[:, 0], Vm[:, 1])
        ax0 = math.atan2(mg0.centroid.y, mg0.centroid.x)
        dth = np.abs((np.arctan2(Vm[:, 1], Vm[:, 0]) - ax0 + math.pi)
                     % (2 * math.pi) - math.pi)
        r_top_m = float(rm.max())
        r_mag1_g = (float(p["rotor_inner_radius"]) + float(p["rotor_house_height"])
                    + float(p["magnet_down_height"]))
        wall = (rm > r_mag1_g + 0.8) & (rm < r_top_m - 2.0) & (dth > 0.01)
        if wall.sum() >= 4:
            cf = np.polyfit(rm[wall], dth[wall], 1)
            p["_mag_a_up"] = float(np.polyval(cf, r_top_m))
            p["_mag_a_dn"] = float(np.polyval(cf, r_mag1_g))  # slant base (wall foot)
        p["_mag_r_top"] = r_top_m
    except Exception:
        pass
    # measure the stator OD fillet from the REAL contour (unit k=0 frame ==
    # world): circle-fit of the arc between the V-notch wall and the OD —
    # the parametric tangent-circle guess is ~0.5 mm off along the OD
    try:
        f0 = stator_unit_frame(p)
        x_cut0, R_o0 = f0["x_cut"], f0["outer_r"]
        g = polys["stator"]
        pts = []
        for gg in getattr(g, "geoms", [g]):
            for ring in [gg.exterior] + list(gg.interiors):
                Vc = np.asarray(ring.coords)
                rr = np.hypot(Vc[:, 0], Vc[:, 1])
                sel = ((rr > R_o0 - 4.5) & (rr < R_o0 + 0.005)
                       & (Vc[:, 0] > x_cut0 - 4.6) & (Vc[:, 0] < x_cut0 + 0.3)
                       & (Vc[:, 1] > 0.6 * R_o0))
                pts.extend(Vc[sel].tolist())
        pts = np.asarray(pts)
        if len(pts) >= 6:
            A = np.c_[2 * pts[:, 0], 2 * pts[:, 1], np.ones(len(pts))]
            (cx, cy, c0), *_ = np.linalg.lstsq(A, (pts ** 2).sum(1), rcond=None)
            p["_od_cx"], p["_od_cy"] = float(cx), float(cy)
            p["_od_r"] = float(math.sqrt(c0 + cx * cx + cy * cy))
            on_od = pts[np.hypot(pts[:, 0], pts[:, 1]) > R_o0 - 0.01]
            on_wall = pts[np.abs(pts[:, 0] - x_cut0) < 0.05]
            if len(on_od):
                p["_od_tan_x"] = float(on_od[:, 0].min())
            if len(on_wall):
                p["_od_y_tan"] = float(on_wall[:, 1].min())
    except Exception:
        pass
    Vs, Ts, Gs = stitch_hanging(*assemble_stator_half(
        p, density=density, outer_air_factor=outer_air_factor,
        n_sectors=n_sectors))
    Vr, Tr, Gr = stitch_hanging(*assemble_rotor_half(
        p, density=density, n_sectors=n_sectors))
    rays = () if n_sectors <= 1 else (0.0, 2.0 * math.pi / n_sectors)
    pr_s = (float(p["stator_inner_radius"]), float(p["stator_outer_radius"]))
    pr_r = (float(p["rotor_outer_radius"]), float(p["rotor_inner_radius"]),
            float(p["shaft_inner_radius"]))
    # fillet arcs (visible + physical): real-poly tags → snap staircase onto
    # the contours → insert arc nodes where the contour bows (fillet) → snap
    # the fresh nodes.  Stator boundary = OD/V-notch/tooth; rotor = iron +
    # each magnet contour (corner fillets, pocket, vent).
    st_cont = list(getattr(polys["stator"], "geoms", [polys["stator"]]))
    st_bnd = [g.boundary for g in st_cont]
    ro_cont = list(getattr(polys["rotor"], "geoms", [polys["rotor"]]))
    ro_bnd = [g.boundary for g in ro_cont]
    mag_bnd = [mg.boundary for mg, _pol in (polys.get("magnets") or [])]

    Gs = _refine_by_polys(Vs, Ts, Gs, "s")
    Vs = _snap_to_contours(Vs, Ts, Gs, polys, "s", protect_r=pr_s, sector_rays=rays)
    Vs, Ts, Gs = _densify_fillets(Vs, Ts, Gs, st_bnd,
                                  protect_r=pr_s, sector_rays=rays)
    Vs = _snap_to_contours(Vs, Ts, Gs, polys, "s", protect_r=pr_s, sector_rays=rays)

    Gr = _refine_by_polys(Vr, Tr, Gr, "r")
    Vr = _snap_to_contours(Vr, Tr, Gr, polys, "r", protect_r=pr_r, sector_rays=rays)
    Vr, Tr, Gr = _densify_fillets(Vr, Tr, Gr, ro_bnd + mag_bnd,
                                  protect_r=pr_r, sector_rays=rays)
    Vr = _snap_to_contours(Vr, Tr, Gr, polys, "r", protect_r=pr_r, sector_rays=rays)

    ms, ts, cs = _finish(Vs, Ts, Gs, "s")
    mr, tr, cr = _finish(Vr, Tr, Gr, "r")
    return ms, ts, cs, mr, tr, cr


def stitch_hanging(V: np.ndarray, T: np.ndarray, G: np.ndarray,
                   tol: float = 1e-7, max_pass: int = 4):
    """Fix T-junctions: for every boundary edge that has other mesh nodes
    lying ON it, split the owning triangle into a fan through those nodes.
    Neighbouring Coons blocks discretise shared curves at their own density,
    so hanging nodes are expected — this makes the assembly conforming."""
    from scipy.spatial import cKDTree
    for _ in range(max_pass):
        edge_owner: Dict[Tuple[int, int], List[int]] = {}
        for ti, t in enumerate(T):
            for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
                edge_owner.setdefault((min(a, b), max(a, b)), []).append(ti)
        bnd = [e for e, o in edge_owner.items() if len(o) == 1]
        if not bnd:
            break
        tree = cKDTree(V)
        new_tris: List[np.ndarray] = []
        new_tags: List[int] = []
        drop = np.zeros(len(T), bool)
        n_fix = 0
        for (a, b) in bnd:
            ti = edge_owner[(a, b)][0]
            if drop[ti]:
                continue
            pa, pb = V[a], V[b]
            L = float(np.linalg.norm(pb - pa))
            if L < tol:
                continue
            cand = tree.query_ball_point((pa + pb) / 2.0, L / 2.0 + 10 * tol)
            d = (pb - pa) / L
            ra, rb = float(np.hypot(*pa)), float(np.hypot(*pb))
            arc_edge = abs(ra - rb) < 1e-6          # circular (r=const) edge
            on: List[Tuple[float, int]] = []
            for j in cand:
                if j == a or j == b:
                    continue
                v = V[j] - pa
                s = float(v @ d)
                if not (tol < s < L - tol):
                    continue
                if abs(v[0] * d[1] - v[1] * d[0]) < tol:
                    on.append((s, j))
                elif arc_edge and abs(float(np.hypot(*V[j])) - ra) < 1e-6:
                    on.append((s, j))                # node on the SAME circle
            if not on:
                continue
            on.sort()
            t = T[ti]
            c = [x for x in t if x != a and x != b][0]
            chain = [a] + [j for _, j in on] + [b]
            # orientation of the original triangle (keep it for the fan)
            e1 = V[t[1]] - V[t[0]]; e2 = V[t[2]] - V[t[0]]
            ccw = (e1[0] * e2[1] - e1[1] * e2[0]) > 0
            for u, w in zip(chain[:-1], chain[1:]):
                tri = [u, w, c]
                f1 = V[w] - V[u]; f2 = V[c] - V[u]
                if ((f1[0] * f2[1] - f1[1] * f2[0]) > 0) != ccw:
                    tri = [w, u, c]
                new_tris.append(np.array(tri)); new_tags.append(int(G[ti]))
            drop[ti] = True
            n_fix += 1
        if n_fix == 0:
            break
        T = np.concatenate([T[~drop]] + ([np.stack(new_tris)] if new_tris else []))
        G = np.concatenate([G[~drop], np.array(new_tags, np.int16)])
    return V, T, G


def assemble_stator_half(p: Dict, density: float = 1.0,
                         outer_air_factor: float = 1.2, n_sectors: int = 1):
    """Stator half [bore..outer air] from rotated unit clones — full 360°
    (n_sectors=1) or one [0..360/n_sectors] wedge.  Unit k covers the world
    span [(n-1-k)·unit .. (n-k)·unit] (tooth axes at 0°+30k), so the wedge is
    the SAME first clones as the full ring and its radial cuts fall exactly
    on wound-tooth axes with clone-identical node sets (solver master-slave
    pairs them by radius).  Local template tags; the solver integration
    remaps to DOM_* and renumbers coils by centroid."""
    ns = max(1, int(n_sectors))
    half_slots = int(p["num_slots"]) // 2
    if half_slots % ns:
        raise ValueError(f"stator: {half_slots} units not divisible by {ns} sectors")
    n_units = half_slots // ns
    unit = math.radians(360.0 / half_slots)
    Vu, Tu, Gu = mesh_blocks(stator_unit_blocks(p, density))
    Vs = []; Ts = []; Gs = []; off = 0
    for k in range(n_units):
        if ns == 1:
            rot = -k * unit                    # unit frame: +Y axis; clone CW
        else:
            # unit at rot=0 spans world [90°-unit .. 90°]; place clone k on
            # [k·unit .. (k+1)·unit] so the wedge is [0 .. 360/ns] exactly
            rot = (k + 1) * unit - math.pi / 2.0
        Vs.append(_rotate(Vu, rot))
        Ts.append(Tu + off); Gs.append(Gu); off += len(Vu)
    V = np.concatenate(Vs); T = np.concatenate(Ts); G = np.concatenate(Gs)
    V, T, G = _weld(V, T, G)
    # outer air ring on the OD node angles.  _ring_grid's angle convention is
    # "from +Y, clockwise" (x=r·sin, y=r·cos).  For a wedge that range is
    # continuous WITHOUT the 2π wrap — applying mod would split the arc at +Y
    # and the open fan would jump straight across the disk (degenerate tris).
    R_o = float(p["stator_outer_radius"])
    on_od = np.where(np.abs(np.hypot(V[:, 0], V[:, 1]) - R_o) < 1e-6)[0]
    raw = np.arctan2(V[on_od, 0], V[on_od, 1])
    ang = np.unique(np.round(np.mod(raw, 2 * math.pi) if ns == 1 else raw, 12))
    r_out = R_o * float(outer_air_factor)
    Vo, To, Go = _graded_outer_ring(ang, R_o, r_out, TAG_AIR + 100,
                                    closed=(ns == 1))
    V2 = np.concatenate([V, Vo]); T2 = np.concatenate([T, To + len(V)])
    G2 = np.concatenate([G, Go])
    return _weld(V2, T2, G2)


def assemble_rotor_half(p: Dict, density: float = 1.0, n_sectors: int = 1):
    """Rotor half [shaft bore..rotor OD] from rotated pole clones plus the
    shaft ring — full 360° (n_sectors=1) or one [0..360/n_sectors] wedge
    (cuts on inter-pole axes at 0°+pitch·k, clone-identical node sets)."""
    ns = max(1, int(n_sectors))
    n_p = int(p["num_poles"])
    if n_p % ns:
        raise ValueError(f"rotor: {n_p} poles not divisible by {ns} sectors")
    n_units = n_p // ns
    unit = math.radians(360.0 / n_p)
    # CadQuery zero-position: first magnet axis at 90deg + ZERO_OFFSET, i.e.
    # rotated by -(90 - pole_pitch/2) from the +Y axis (see get_2d_polygons).
    ang0 = -math.radians(90.0 - 360.0 / n_p / 2.0)
    Vu, Tu, Gu = mesh_blocks(rotor_unit_blocks(p, density))
    Vs = []; Ts = []; Gs = []; off = 0
    for k in range(n_units):
        if ns == 1:
            rot = -(k * unit) - ang0
        else:
            # wedge: unit m covers world [m·pitch .. (m+1)·pitch]
            rot = ang0 + k * unit
        Vs.append(_rotate(Vu, rot))
        Ts.append(Tu + off); Gs.append(Gu); off += len(Vu)
    V = np.concatenate(Vs); T = np.concatenate(Ts); G = np.concatenate(Gs)
    V, T, G = _weld(V, T, G)
    R_i = float(p["rotor_inner_radius"]); r_sh = float(p["shaft_inner_radius"])
    on_ir = np.where(np.abs(np.hypot(V[:, 0], V[:, 1]) - R_i) < 1e-6)[0]
    raw = np.arctan2(V[on_ir, 0], V[on_ir, 1])
    ang = np.unique(np.round(np.mod(raw, 2 * math.pi) if ns == 1 else raw, 12))
    Vo, To, Go = _ring_grid(ang, r_sh, R_i,
                            max(1, int(round((R_i - r_sh) * density * 0.6))),
                            TAG_IRON + 100, closed=(ns == 1))
    V2 = np.concatenate([V, Vo]); T2 = np.concatenate([T, To + len(V)])
    G2 = np.concatenate([G, Go])
    return _weld(V2, T2, G2)


def rotor_unit_blocks(p: Dict, density: float = 1.0) -> List[Tuple]:
    """Blocks for ONE rotor pole unit (spoke-PM), half [magnet axis..+pitch/2]
    mirrored about the magnet axis.  Local frame: magnet axis along +Y.

    Measured topology (polar scans): yoke ring → magnet lower bar (radial
    walls at ±pole·fill_down/2) → magnet trapezoid narrowing to
    ±pole·fill_up/2 at r_top = OD−up_gap → bridge band [r_top..OD] with the
    rectangular vent (air) of half-angle fu·hole/2 on the axis; inter-pole
    iron is the hourglass between neighbouring magnet walls.  The magnet
    corner fillet (magnet_fill_radius) and rotor_fill_r are deferred (v2).
    """
    R_o = float(p["rotor_outer_radius"]); R_i = float(p["rotor_inner_radius"])
    house = float(p["rotor_house_height"]); pole = 360.0 / int(p["num_poles"])
    r_mag0 = R_i + house                       # magnet bottom radius
    dn_h = float(p["magnet_down_height"]); r_mag1 = r_mag0 + dn_h
    up_gap = float(p["magnet_up_gap"]); r_top = R_o - up_gap
    a_dn = math.radians(pole * float(p["magnet_fill_down"]) / 2.0)
    a_up = math.radians(pole * float(p["magnet_fill_up"]) / 2.0)
    # landmarks measured from the REAL CadQuery magnet override the
    # parametric guesses (the CQ boolean chain shifts the magnet top by
    # ~0.1 mm vs R_o-up_gap — a systematic wall offset otherwise)
    if p.get("_mag_r_top"):
        r_top = float(p["_mag_r_top"])
    if p.get("_mag_a_up"):
        a_up = float(p["_mag_a_up"])
    a_vent = math.radians(pole * float(p["magnet_fill_up"]) * float(p["rotor_hole"]) / 2.0)
    half = math.radians(pole / 2.0)

    def n_of(length_mm, lo=1):
        return max(lo, int(round(abs(length_mm) * density)))

    # ── TWO warped tensor grids: the magnet wall constrains columns only up
    # to r_top, so the unit is split there.  Grid A [R_i..r_top] squeezes its
    # columns to follow the slanted wall; grid B [r_top..R_o] (bridge band with
    # the vent) keeps the UNIFORM columns — the vent sits at its true ±a_vent
    # and the inter-pole bridge gets full column coverage.  The seam on the
    # r_top arc has hanging nodes on both sides; the assembler's weld +
    # stitch_hanging (arc-aware fans) makes it conforming.
    n_ang = max(8, int(round(R_o * half * density)))
    tg_u = np.linspace(0.0, half, n_ang + 1)
    # drop interior uniform columns that nearly coincide with the landmark
    # angles — micron-wide twin columns become needle triangles downstream
    step = half / n_ang
    spec = np.array([a_vent, a_dn])
    keep = np.ones(len(tg_u), bool)
    keep[1:-1] = np.min(np.abs(tg_u[1:-1, None] - spec[None, :]), axis=1) > 0.35 * step
    tg = np.unique(np.concatenate([tg_u[keep], spec]))
    rows_A = np.unique(np.concatenate([
        np.linspace(R_i, r_mag0, max(1, n_of(house)) + 1),
        np.linspace(r_mag0, r_mag1, max(1, n_of(dn_h)) + 1),
        np.linspace(r_mag1, r_top, max(3, n_of(r_top - r_mag1)) + 1)]))
    rows_B = np.linspace(r_top, R_o, max(3, n_of(up_gap)) + 1)

    # the magnet slant wall is a STRAIGHT line in xy from (a_dn, r_mag1) to
    # (a_up, r_top) — NOT a linear angle-in-radius ramp.  Building it as the
    # true segment makes the tensor column coincide with the CadQuery wall, so
    # the poly re-tag no longer flips cells across it (that mismatch produced a
    # sawtooth on the magnet flank).  wall_ang(r) = angle where the segment
    # crosses radius r.
    _a_dn_wall = float(p.get("_mag_a_dn", a_dn))   # measured wall foot angle
    _Ax, _Ay = r_mag1 * math.sin(_a_dn_wall), r_mag1 * math.cos(_a_dn_wall)
    _Bx, _By = r_top * math.sin(a_up), r_top * math.cos(a_up)
    _Dx, _Dy = _Bx - _Ax, _By - _Ay
    _AA = _Dx * _Dx + _Dy * _Dy
    _BB = 2.0 * (_Ax * _Dx + _Ay * _Dy)

    def wall_ang(r):                                # magnet wall angle at r
        if r <= r_mag1:
            return a_dn
        if r >= r_top:
            return a_up
        cc = _Ax * _Ax + _Ay * _Ay - r * r
        disc = max(_BB * _BB - 4.0 * _AA * cc, 0.0)
        t = (-_BB + math.sqrt(disc)) / (2.0 * _AA)
        return math.atan2(_Ax + t * _Dx, _Ay + t * _Dy)

    def theta_row(r):
        w = wall_ang(r)
        th = tg.copy()
        inside = tg <= a_dn + 1e-15
        th[inside] = tg[inside] * (w / a_dn)        # squeeze magnet span
        th[~inside] = w + (tg[~inside] - a_dn) * (half - w) / (half - a_dn)
        return th

    def tensor(r_rows, tgrid, warp):
        nr = len(r_rows) - 1; na = len(tgrid) - 1
        V = np.empty(((nr + 1) * (na + 1), 2))
        for i, r in enumerate(r_rows):
            th = theta_row(r) if warp else tgrid
            V[i * (na + 1):(i + 1) * (na + 1), 0] = r * np.sin(th)
            V[i * (na + 1):(i + 1) * (na + 1), 1] = r * np.cos(th)
        idx = np.arange((nr + 1) * (na + 1)).reshape(nr + 1, na + 1)
        a_ = idx[:-1, :-1].ravel(); b_ = idx[:-1, 1:].ravel()
        c_ = idx[1:, 1:].ravel();   d_ = idx[1:, :-1].ravel()
        T = np.concatenate([np.stack([a_, b_, c_], 1), np.stack([a_, c_, d_], 1)])
        rc = 0.5 * (r_rows[:-1] + r_rows[1:])[:, None] * np.ones((1, na))
        tc = 0.25 * (tgrid[:-1] + tgrid[1:])[None, :] * np.ones((nr, 1)) * 2.0
        return V, T, rc, tc

    # grid A: yoke + magnet span (wall-warped columns)
    V_A, T_A, rc, tc = tensor(rows_A, tg, warp=True)
    tag = np.full(rc.shape, TAG_IRON, np.int16)
    tag[(rc >= r_mag0 - 1e-9) & (tc <= a_dn + 1e-12)] = TAG_MAGNET
    G_A = np.concatenate([tag.ravel(), tag.ravel()])
    # grid B: bridge band with the rectangular vent (uniform columns).  Free
    # (non-landmark) columns that land within 0.35 steps of a squeezed grid-A
    # node on the r_top seam SNAP to it — the weld then merges the pair
    # exactly, instead of stitch fans building micron-base needle triangles.
    th_top = theta_row(r_top)
    tg_B = tg.copy()
    locked = {0.0, float(a_vent), float(half)}
    for j in range(1, len(tg_B) - 1):
        if float(tg_B[j]) in locked:
            continue
        k = int(np.argmin(np.abs(th_top - tg_B[j])))
        if abs(float(th_top[k] - tg_B[j])) < 0.35 * step:
            tg_B[j] = th_top[k]
    tg_B = np.unique(tg_B)
    V_B, T_B, rc, tc = tensor(rows_B, tg_B, warp=False)
    tag = np.full(rc.shape, TAG_IRON, np.int16)
    tag[tc <= a_vent + 1e-12] = TAG_AIR
    G_B = np.concatenate([tag.ravel(), tag.ravel()])

    V = np.concatenate([V_A, V_B])
    T = np.concatenate([T_A, T_B + len(V_A)])
    G = np.concatenate([G_A, G_B])
    p0, p1, p2 = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    cw = ((p1[:, 0] - p0[:, 0]) * (p2[:, 1] - p0[:, 1])
          - (p1[:, 1] - p0[:, 1]) * (p2[:, 0] - p0[:, 0])) < 0
    T[cw] = T[cw][:, ::-1]
    # mirror about the axis and return as ONE pre-meshed "block" pair via a
    # sentinel: the assembler detects ndarray triples.
    M = np.array([[-1.0, 0.0], [0.0, 1.0]])
    V2 = np.concatenate([V, V @ M])
    T2 = np.concatenate([T, (T + len(V))[:, ::-1]])
    G2 = np.concatenate([G, G])
    return [("premeshed", V2, T2, G2)]

    def P(a, r):                               # polar → local xy (axis = +Y)
        return (r * math.sin(a), r * math.cos(a))

    def arc(r, a0, a1, n=None):
        t = np.linspace(a0, a1, max(2, n))
        return np.stack([r * np.sin(t), r * np.cos(t)], axis=1)

    def rad(a, r0, r1):
        return np.array([P(a, r0), P(a, r1)])

    blocks: List[Tuple] = []

    def polarq(a0, a1, r0, r1, tag, na=None, nr=None):
        na = na or n_of((a1 - a0) * (r0 + r1) / 2, 2)
        nr = nr or n_of(r1 - r0, 1)
        blocks.append((arc(r0, a0, a1, na + 1), rad(a1, r0, r1),
                       arc(r1, a0, a1, na + 1), rad(a0, r0, r1),
                       na, nr, tag))

    # 1) yoke ring
    polarq(0.0, half, R_i, r_mag0, TAG_IRON, nr=n_of(house, 1))
    # 2) magnet lower bar + inter-pole iron beside it
    polarq(0.0, a_dn, r_mag0, r_mag1, TAG_MAGNET)
    polarq(a_dn, half, r_mag0, r_mag1, TAG_IRON)
    # 3) magnet trapezoid: straight wall from (a_dn, r_mag1) to (a_up, r_top)
    w0 = P(a_dn, r_mag1); w1 = P(a_up, r_top)
    nwall = n_of(math.dist(w0, w1), 3)
    wall = np.stack([np.linspace(w0[0], w1[0], nwall + 1),
                     np.linspace(w0[1], w1[1], nwall + 1)], axis=1)
    na_m = n_of(a_dn * (r_mag1 + r_top) / 2, 2)
    blocks.append((arc(r_mag1, 0.0, a_dn, na_m + 1), wall,
                   arc(r_top, 0.0, a_up, na_m + 1), rad(0.0, r_mag1, r_top),
                   na_m, nwall, TAG_MAGNET))
    # 4) inter-pole hourglass iron: wall .. unit edge
    blocks.append((arc(r_mag1, a_dn, half, na_m + 1), rad(half, r_mag1, r_top),
                   arc(r_top, a_up, half, na_m + 1), wall,
                   na_m, nwall, TAG_IRON))
    # 5) bridge band with the vent
    polarq(0.0, a_vent, r_top, R_o, TAG_AIR, nr=max(1, n_of(up_gap, 1)))
    polarq(a_vent, half, r_top, R_o, TAG_IRON, nr=max(1, n_of(up_gap, 1)))

    # mirror about the magnet axis (x → −x)
    M = np.array([[-1.0, 0.0], [0.0, 1.0]])
    mirrored = [(S @ M, E @ M, N @ M, W @ M, nx, ny, tag)
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
    for blk in blocks:
        if len(blk) == 4 and isinstance(blk[0], str) and blk[0] == "premeshed":
            _, v, t, g = blk                     # tensor-meshed unit (rotor)
            all_v.append(v); all_t.append(t + off); all_tag.append(np.asarray(g, np.int16))
            off += len(v)
            continue
        (S, E, N, W, nx, ny, tag) = blk
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
