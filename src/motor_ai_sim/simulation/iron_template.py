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


def _ring_grid(angles: np.ndarray, r0: float, r1: float, n_r: int, tag: int):
    """Polar quad ring on EXACTLY the given sorted angle set (matches an
    existing boundary node ring 1:1) — verts, tris, tags."""
    na = len(angles)
    rs = np.linspace(r0, r1, n_r + 1)
    th = np.concatenate([angles, angles[:1]])      # closed
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
                         outer_air_factor: float = 1.2):
    """Full 360° stator half [bore..outer air] from rotated unit clones.
    Local template tags; the solver integration remaps to DOM_* and renumbers
    coils by centroid against the CadQuery coil list."""
    half_slots = int(p["num_slots"]) // 2
    unit = math.radians(360.0 / half_slots)
    Vu, Tu, Gu = mesh_blocks(stator_unit_blocks(p, density))
    Vs = []; Ts = []; Gs = []; off = 0
    for k in range(half_slots):
        Vs.append(_rotate(Vu, -k * unit))      # unit frame: +Y axis; clone CW
        Ts.append(Tu + off); Gs.append(Gu); off += len(Vu)
    V = np.concatenate(Vs); T = np.concatenate(Ts); G = np.concatenate(Gs)
    V, T, G = _weld(V, T, G)
    # outer air ring on the OD node angles
    R_o = float(p["stator_outer_radius"])
    on_od = np.where(np.abs(np.hypot(V[:, 0], V[:, 1]) - R_o) < 1e-6)[0]
    ang = np.unique(np.round(np.mod(np.arctan2(V[on_od, 0], V[on_od, 1]),
                                    2 * math.pi), 12))
    r_out = R_o * float(outer_air_factor)
    Vo, To, Go = _ring_grid(ang, R_o, r_out,
                            max(2, int(round((r_out - R_o) * density * 0.5))),
                            TAG_AIR + 100)     # marker: outer air
    V2 = np.concatenate([V, Vo]); T2 = np.concatenate([T, To + len(V)])
    G2 = np.concatenate([G, Go])
    return _weld(V2, T2, G2)


def assemble_rotor_half(p: Dict, density: float = 1.0):
    """Full 360° rotor half [shaft bore..rotor OD] from rotated pole clones
    plus the shaft ring."""
    n_p = int(p["num_poles"])
    unit = math.radians(360.0 / n_p)
    Vu, Tu, Gu = mesh_blocks(rotor_unit_blocks(p, density))
    Vs = []; Ts = []; Gs = []; off = 0
    for k in range(n_p):
        Vs.append(_rotate(Vu, -k * unit))
        Ts.append(Tu + off); Gs.append(Gu); off += len(Vu)
    V = np.concatenate(Vs); T = np.concatenate(Ts); G = np.concatenate(Gs)
    V, T, G = _weld(V, T, G)
    R_i = float(p["rotor_inner_radius"]); r_sh = float(p["shaft_inner_radius"])
    on_ir = np.where(np.abs(np.hypot(V[:, 0], V[:, 1]) - R_i) < 1e-6)[0]
    ang = np.unique(np.round(np.mod(np.arctan2(V[on_ir, 0], V[on_ir, 1]),
                                    2 * math.pi), 12))
    Vo, To, Go = _ring_grid(ang, r_sh, R_i,
                            max(1, int(round((R_i - r_sh) * density * 0.6))),
                            TAG_IRON + 100)    # marker: shaft
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
    a_vent = math.radians(pole * float(p["magnet_fill_up"]) * float(p["rotor_hole"]) / 2.0)
    half = math.radians(pole / 2.0)

    def n_of(length_mm, lo=1):
        return max(lo, int(round(abs(length_mm) * density)))

    # ── single WARPED TENSOR grid: θ-columns × r-rows, columns in the magnet
    # span follow the slanted wall (mapped mesh) → conforming by construction,
    # every domain boundary lies on grid lines.  No Coons, no T-junctions.
    n_ang = max(8, int(round(R_o * half * density)))
    tg = np.unique(np.concatenate([np.linspace(0.0, half, n_ang + 1),
                                   [a_vent, a_dn]]))
    j_dn = int(np.argmin(np.abs(tg - a_dn)))       # wall column index (exact)
    r_rows = np.unique(np.concatenate([
        np.linspace(R_i, r_mag0, max(1, n_of(house)) + 1),
        np.linspace(r_mag0, r_mag1, max(1, n_of(dn_h)) + 1),
        np.linspace(r_mag1, r_top, max(3, n_of(r_top - r_mag1)) + 1),
        np.linspace(r_top, R_o, max(1, n_of(up_gap)) + 1)]))

    def wall_ang(r):                                # magnet wall angle at r
        if r <= r_mag1:
            return a_dn
        if r >= r_top:
            return a_up
        return a_dn + (a_up - a_dn) * (r - r_mag1) / (r_top - r_mag1)

    def theta_row(r):
        w = wall_ang(r)
        th = tg.copy()
        inside = tg <= a_dn + 1e-15
        th[inside] = tg[inside] * (w / a_dn)        # squeeze magnet span
        th[~inside] = w + (tg[~inside] - a_dn) * (half - w) / (half - a_dn)
        return th

    nr = len(r_rows) - 1; na = len(tg) - 1
    V = np.empty(((nr + 1) * (na + 1), 2))
    for i, r in enumerate(r_rows):
        th = theta_row(r)
        V[i * (na + 1):(i + 1) * (na + 1), 0] = r * np.sin(th)
        V[i * (na + 1):(i + 1) * (na + 1), 1] = r * np.cos(th)
    idx = np.arange((nr + 1) * (na + 1)).reshape(nr + 1, na + 1)
    a_ = idx[:-1, :-1].ravel(); b_ = idx[:-1, 1:].ravel()
    c_ = idx[1:, 1:].ravel();   d_ = idx[1:, :-1].ravel()
    T = np.concatenate([np.stack([a_, b_, c_], 1), np.stack([a_, c_, d_], 1)])
    # tags per quad-cell → per triangle (2 tris/quad, same tag)
    rc = 0.5 * (r_rows[:-1] + r_rows[1:])[:, None] * np.ones((1, na))
    tc = 0.25 * (tg[:-1] + tg[1:])[None, :] * np.ones((nr, 1)) * 2.0
    tag = np.full((nr, na), TAG_IRON, np.int16)
    in_mag_r = (rc >= r_mag0 - 1e-9) & (rc <= r_top + 1e-9)
    tag[in_mag_r & (tc <= a_dn)] = TAG_MAGNET       # warped col ≤ wall
    in_vent = (rc >= r_top - 1e-9) & (tc <= a_vent + 1e-12)
    tag[in_vent] = TAG_AIR
    G = np.concatenate([tag.ravel(), tag.ravel()])
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
