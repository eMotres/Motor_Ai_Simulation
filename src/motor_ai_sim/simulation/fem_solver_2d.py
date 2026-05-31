"""2-D finite-element magnetostatics solver — pure Python (scikit-fem + gmsh).

Solves   ∇·(ν ∇A_z) = -J_z + ∇·(M × ẑ)    on a triangle mesh of the motor
cross-section.  Used as a real-FEM alternative to the analytical Green's
function solver in routes/simulation.py.

Domain-specific data (matches the CadQuery polygon classes):
    air-gap        ν = 1/μ₀                     (free space)
    stator steel   ν = 1/(μ₀·5000)              (silicon steel, linear)
    rotor steel    ν = 1/(μ₀·5000)
    shaft          ν = 1/(μ₀·1000)              (aluminium-ish)
    magnets        ν = 1/(μ₀·1.05),  M = ±Br·φ̂   (tangential, alternating)
    coils          ν = 1/μ₀,  J_z = direction · I_phase · n_wires / area

Boundary: A_z = 0 on the outer stator circle.

Method: linear FE on a P1-triangle mesh; gmsh builds a conforming mesh from
the real CadQuery exterior+interior contours so the same geometry the
canvas displays is the geometry we solve on.

Time budget for one solve at the default mesh density (~25k triangles):
roughly 1-2 s on a modern CPU.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

import numpy as np

log = logging.getLogger(__name__)

MU0 = 4e-7 * math.pi

# Domain ids (must match _DOMAIN_ID in the API rasterisation for consistency)
DOM_AIR     = 0
DOM_STATOR  = 1
DOM_COIL    = 2
DOM_AIRGAP  = 3
DOM_MAG_N   = 4   # N pole
DOM_ROTOR   = 5
DOM_SHAFT   = 6
DOM_BAND    = 7   # motion / slip band inside the air gap (transient solver)
DOM_OUTER   = 8   # outer air ring (far-field boundary, beyond stator OD)
DOM_MAG_S   = 44  # S pole


@dataclass
class FEMMaterial:
    name:  str
    mu_r:  float
    J_z:   float = 0.0   # [A/m²]  external current density
    Mx:    float = 0.0   # [A/m]   magnetization x-component
    My:    float = 0.0   # [A/m]   magnetization y-component


@dataclass
class FEMResult:
    """Sampled result on a regular Cartesian grid."""
    grid_size:   int
    extent:      Tuple[float, float, float, float]   # xmin xmax ymin ymax [m]
    A_z:         np.ndarray   # (gs, gs)
    B_x:         np.ndarray
    B_y:         np.ndarray
    B_mag:       np.ndarray
    J_z:         np.ndarray   # source J_z on grid
    domain:      np.ndarray   # int8, domain ids on grid
    n_triangles: int
    n_nodes:     int
    solve_time_s: float


# ─────────────────────────────────────────────────────────────────────────────
# 1.  Build a triangle mesh from CadQuery Shapely polygons (using gmsh)
# ─────────────────────────────────────────────────────────────────────────────

def _fillet_polygon(poly, r_convex: float = 0.6, r_concave: float = 0.6):
    """Round sharp corners of a Shapely (Multi)Polygon with two buffer passes.

    Algorithm:
      1.  poly.buffer(+r_convex, join_style=round).buffer(-r_convex, join_style=round)
          → rounds the CONVEX corners of the exterior (and concave corners of
          interiors, which are convex from inside the iron).
      2.  poly.buffer(-r_concave, join_style=round).buffer(+r_concave, join_style=round)
          → rounds the CONCAVE corners of the exterior (slot-mouth inside
          corners, the ones the user circled in red).

    Equivalent to a Minkowski-sum + erosion pipeline that produces visually
    indistinguishable fillets up to the chord tolerance.
    """
    try:
        # join_style 1 = round (Shapely 2.x; some versions use the
        # enum JOIN_STYLE.round = 1).  Cap style is irrelevant for closed
        # polygons.
        p1 = poly.buffer(+r_convex,  join_style=1, mitre_limit=4).buffer(
                          -r_convex, join_style=1, mitre_limit=4)
        p2 = p1.buffer(  -r_concave, join_style=1, mitre_limit=4).buffer(
                         +r_concave, join_style=1, mitre_limit=4)
        if p2.is_valid and not p2.is_empty:
            return p2
    except Exception as e:
        log.warning("_fillet_polygon failed (%s) — keeping original", e)
    return poly


def _simplify_polys(polys: dict, tol_mm: float = 0.005,
                     stator_fillet_mm: float = 0.8) -> dict:
    """Drop near-collinear vertices below chord tolerance `tol_mm`.

    Default 0.005 mm matches Ansys Maxwell's "Surface Deviation = 0.01 mm"
    — small enough that every fillet arc point (chord deviation ~0.01-0.05
    mm depending on radius) survives the cleanup. Long straight runs still
    collapse to two endpoints, so the mesh density is driven entirely by
    point clustering on curved boundaries.

    Parameters
    ----------
    stator_fillet_mm : float, default 0.8
        Round all sharp corners on the stator polygon with this radius
        (a Shapely buffer-out/in pipeline).  The CadQuery slot cutter only
        rounds ONE corner per slot pair; this post-pass adds fillets at the
        other slot-mouth corners (top-of-wedge + slot-bottom) so the mesh
        boundary matches the physical iron lamination.
    """
    out = dict(polys)
    for k in ("stator", "rotor", "shaft", "air_gap"):
        if polys.get(k) is not None:
            try:
                out[k] = polys[k].simplify(tol_mm, preserve_topology=True)
            except Exception:
                out[k] = polys[k]
    # ── Round all sharp slot-mouth corners on the stator ──
    if out.get("stator") is not None and stator_fillet_mm > 0:
        out["stator"] = _fillet_polygon(out["stator"],
                                         r_convex=stator_fillet_mm,
                                         r_concave=stator_fillet_mm)
    out["magnets"] = [
        ((m.simplify(tol_mm, preserve_topology=True) if m is not None else m), p)
        for m, p in polys.get("magnets", [])
    ]
    out["coils"] = list(polys.get("coils", []))
    return out


def _add_background_air(polys: dict, outer_air_factor: float = 1.0) -> dict:
    """Compute the explicit "background air" polygon and add it to `polys`.

    Background air  =  disk(r = stator_outer)  −  ∪(all material polygons).

    This catches every region NOT covered by an explicit material:
      • the thin rectangle in each rotor pocket between magnet top and rotor
        outer radius (was a hole in the rotor polygon, not filled by any
        magnet → previously meshed as "rotor" or left bare),
      • the air inside each stator slot around the wire bundle (slot was a
        hole in the stator polygon, only partly filled by the coil bundle
        → previously left bare or covered by overlay templates).

    With this air polygon registered as a separate gmsh OCC surface, the
    fragment operation produces a clean, NON-OVERLAPPING partition of the
    entire motor cross-section.  Every triangle then maps to exactly one
    source polygon, eliminating the "two meshes overlapping" artefacts.

    Parameters
    ----------
    outer_air_factor : float, default 1.0
        Multiplier for the outer disk radius.  Values > 1 add an outer air
        ring beyond the stator OD — this is where the Dirichlet A_z = 0
        far-field boundary condition is applied, instead of directly on
        the stator iron (which would suppress flux at the iron→air boundary).
        Tagged with DOM_OUTER so it can be rendered in a distinct color.
    """
    from shapely.geometry import Point as _Pt
    from shapely.ops import unary_union as _uu

    out = dict(polys)
    stator = polys.get("stator")
    if stator is None:
        return out

    try:
        # Outer envelope radius
        if hasattr(stator, "exterior"):
            r_max = max(math.hypot(x, y) for x, y in list(stator.exterior.coords))
        else:
            # MultiPolygon
            r_max = max(math.hypot(x, y)
                        for g in stator.geoms
                        for x, y in list(g.exterior.coords))
    except Exception:
        return out

    inner_disk = _Pt(0.0, 0.0).buffer(r_max + 1e-4, resolution=256)

    materials: list = []
    for k in ("stator", "rotor", "shaft", "air_gap", "airgap_band"):
        g = polys.get(k)
        if g is not None and not g.is_empty:
            materials.append(g)
    for mp, _pol in polys.get("magnets", []):
        if mp is not None and not mp.is_empty:
            materials.append(mp)
    for cp in polys.get("coils", []):
        if cp is not None and not cp.is_empty:
            materials.append(cp)

    if materials:
        try:
            mat_union = _uu(materials)
            air = inner_disk.difference(mat_union)
            if not air.is_empty:
                out["air_background"] = air
        except Exception as e:
            log.warning("background air computation failed: %s — falling back to disk", e)
            out["air_background"] = inner_disk
    else:
        out["air_background"] = inner_disk

    # ── Outer air ring (far-field boundary) ──────────────────────────────
    # Adds disk(R_far) − disk(R_stator) as DOM_OUTER, so the Dirichlet
    # A_z = 0 condition is applied on the artificial far-field boundary,
    # not on the iron.  Typical R_far / R_stator = 1.3–1.5 (Ansys default
    # is "Region Padding" ~25% which corresponds to factor 1.25).
    if outer_air_factor > 1.001:
        try:
            r_far = r_max * float(outer_air_factor)
            outer_disk = _Pt(0.0, 0.0).buffer(r_far, resolution=256)
            outer_ring = outer_disk.difference(inner_disk)
            if not outer_ring.is_empty:
                out["air_outer"] = outer_ring
        except Exception as e:
            log.warning("outer air ring construction failed: %s", e)

    return out


def _add_motion_band(polys: dict, motion_band: bool, band_thickness_mm: float = 0.4
                      ) -> dict:
    """Add a thin annular "motion band" inside the air gap.

    The band is a slip surface that lets the rotor sweep through angles
    without re-meshing the surrounding regions — the rotor-side air-gap
    rotates with the rotor; the band itself re-meshes cheaply per step;
    the stator-side air-gap is stationary.

    For static FEM the band is just an extra air ring; its real value
    surfaces in the transient solver (eddy-current / cogging vs time).

    The band is centred at the air-gap midline (r = (r_rotor_out +
    r_stator_in) / 2) with the requested thickness.  The original
    air_gap polygon is split into THREE rings:
        airgap_rotor_side  : rotor_out → band_inner
        airgap_band        : band_inner → band_outer      ← DOM_BAND
        airgap_stator_side : band_outer → stator_in
    """
    if not motion_band:
        return polys
    from shapely.geometry import Point as _Pt

    airgap = polys.get("air_gap")
    rotor  = polys.get("rotor")
    stator = polys.get("stator")
    if airgap is None or airgap.is_empty:
        return polys

    try:
        # Air-gap inner/outer radii
        ag_inner = min(math.hypot(x, y) for x, y in list(airgap.exterior.coords))
        ag_outer = max(math.hypot(x, y) for x, y in list(airgap.exterior.coords))
        for h in airgap.interiors:
            ag_inner = min(ag_inner, min(math.hypot(x, y) for x, y in list(h.coords)))
        # air_gap = annulus from rotor_out (= ag_inner via hole) to stator_in (= ag_outer via exterior)
        # In the cadquery construction air_gap = SPoly(_circle(inner_r), [_circle(rotor_or)]),
        # so exterior r ≈ stator_in (outer), interior r ≈ rotor_out (inner).
        r_band_center = 0.5 * (ag_inner + ag_outer)
        r_band_in  = r_band_center - 0.5 * band_thickness_mm
        r_band_out = r_band_center + 0.5 * band_thickness_mm
        band = _Pt(0, 0).buffer(r_band_out, resolution=256).difference(
               _Pt(0, 0).buffer(r_band_in,  resolution=256))
        if band.is_empty:
            return polys
        # Slice air_gap into three rings
        out = dict(polys)
        out["airgap_band"] = band
        # Rotor-side: airgap ∩ (r ≤ band_in)  — actually airgap.difference(disk(r_band_in)→out)
        inner_band_disk = _Pt(0, 0).buffer(r_band_in, resolution=256)
        outer_band_disk = _Pt(0, 0).buffer(r_band_out, resolution=256)
        # rotor side = airgap ∩ disk(r_band_in)
        rotor_side  = airgap.intersection(inner_band_disk)
        stator_side = airgap.difference(outer_band_disk)
        out["air_gap"] = rotor_side.union(stator_side) if (not rotor_side.is_empty and not stator_side.is_empty) else rotor_side if not rotor_side.is_empty else stator_side
        return out
    except Exception as e:
        log.warning("motion band construction failed: %s", e)
        return polys


def _clip_polys_to_sector(polys: dict, n_sectors: int) -> dict:
    """Clip every polygon to a 360°/n_sectors wedge starting at θ = 0.

    Used for symmetry reduction:  e.g.  n_sectors=4 with 24 slots + 28 poles
    gives  6 slots + 7 poles per sector  (since GCD(24, 28) = 4).
    Anti-periodic BC on the two radial cuts must be applied by the solver
    when the # of pole pairs per sector is fractional (7 poles → 3.5 pp).
    """
    if n_sectors <= 1:
        return polys
    from shapely.geometry import Polygon as _SPoly
    from shapely.geometry import MultiPolygon as _SMPoly

    sector_angle = 2 * math.pi / n_sectors
    # Big-enough wedge: extends to 10x the stator radius (always overshoots).
    R = 10_000.0
    n_arc = max(64, int(360 / n_sectors))
    pts = [(0.0, 0.0)]
    for i in range(n_arc + 1):
        a = sector_angle * i / n_arc
        pts.append((R * math.cos(a), R * math.sin(a)))
    wedge = _SPoly(pts)

    def _clip(g):
        if g is None or g.is_empty:
            return g
        try:
            return g.intersection(wedge)
        except Exception:
            return g

    out = dict(polys)
    for k in ("stator", "rotor", "shaft", "air_gap", "airgap_band",
              "air_background", "air_outer"):
        if polys.get(k) is not None:
            out[k] = _clip(polys[k])
    out["magnets"] = []
    for mp, pol in polys.get("magnets", []):
        clipped = _clip(mp)
        if clipped is not None and not clipped.is_empty and clipped.area > 1e-6:
            # An intersection may be a MultiPolygon: keep all parts with same polarity
            if isinstance(clipped, _SMPoly):
                for g in clipped.geoms:
                    if g.area > 1e-6:
                        out["magnets"].append((g, pol))
            else:
                out["magnets"].append((clipped, pol))
    out["coils"] = []
    for cp in polys.get("coils", []):
        clipped = _clip(cp)
        if clipped is not None and not clipped.is_empty and clipped.area > 1e-6:
            if isinstance(clipped, _SMPoly):
                for g in clipped.geoms:
                    if g.area > 1e-6:
                        out["coils"].append(g)
            else:
                out["coils"].append(clipped)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Periodic coil meshing — one wire → all wires → all coils
# ─────────────────────────────────────────────────────────────────────────────

def _structured_rect_mesh(w_mm: float, h_mm: float, target_mm: float
                          ) -> Tuple[np.ndarray, np.ndarray]:
    """Structured triangular mesh of (0,0)→(w,h). Returns (verts (2,N), tris (3,M))."""
    nx = max(2, int(round(w_mm / max(target_mm, 0.05))) + 1)
    ny = max(2, int(round(h_mm / max(target_mm, 0.05))) + 1)
    xs = np.linspace(0.0, w_mm, nx)
    ys = np.linspace(0.0, h_mm, ny)
    XX, YY = np.meshgrid(xs, ys)
    verts = np.column_stack([XX.ravel(), YY.ravel()]).T   # (2, nx*ny)
    tris: List[List[int]] = []
    for j in range(ny - 1):
        for i in range(nx - 1):
            a = j * nx + i
            b = a + 1
            c = a + nx
            d = c + 1
            # Two triangles per cell, alternating diagonal for nicer quality
            if (i + j) % 2 == 0:
                tris.append([a, b, d]); tris.append([a, d, c])
            else:
                tris.append([a, b, c]); tris.append([b, d, c])
    return verts, np.array(tris, dtype=np.int64).T


def _mesh_single_polygon(poly, mesh_size_mm: float, min_size_mm: float
                          ) -> Tuple[np.ndarray, np.ndarray]:
    """Mesh ONE Shapely polygon via gmsh. Returns (verts (2,N) in mm, tris (3,M))."""
    import gmsh

    try:
        gmsh.initialize([], interruptible=False)
    except TypeError:
        gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setNumber("Mesh.MeshSizeMin", min_size_mm)
        gmsh.option.setNumber("Mesh.MeshSizeMax", mesh_size_mm)
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 60)
        gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 1)
        gmsh.option.setNumber("Mesh.Algorithm", 6)
        gmsh.model.add("single_poly")
        occ = gmsh.model.occ

        ext = list(poly.exterior.coords)[:-1]
        # Drop consecutive near-duplicate points
        clean: List[Tuple[float, float]] = []
        for x, y in ext:
            if not clean or (abs(x - clean[-1][0]) > 1e-3 or abs(y - clean[-1][1]) > 1e-3):
                clean.append((x, y))
        if len(clean) >= 2 and abs(clean[0][0] - clean[-1][0]) < 1e-3 \
                and abs(clean[0][1] - clean[-1][1]) < 1e-3:
            clean.pop()
        if len(clean) < 3:
            return np.empty((2, 0)), np.empty((3, 0), dtype=np.int64)

        pt_tags = [occ.addPoint(x, y, 0) for x, y in clean]
        line_tags = []
        for i in range(len(pt_tags)):
            a = pt_tags[i]; b = pt_tags[(i + 1) % len(pt_tags)]
            if a != b:
                line_tags.append(occ.addLine(a, b))
        loop = occ.addCurveLoop(line_tags)
        surf = occ.addPlaneSurface([loop])
        occ.synchronize()
        gmsh.model.mesh.generate(2)

        node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
        node_id_to_idx = {int(t): i for i, t in enumerate(node_tags)}
        points = np.array(node_coords, dtype=float).reshape(-1, 3)[:, :2]

        el_types, _, node_lists = gmsh.model.mesh.getElements(2, surf)
        tris: List[Tuple[int, int, int]] = []
        for et_idx, et in enumerate(el_types):
            if int(et) != 2:
                continue
            flat = node_lists[et_idx]
            for k in range(len(flat) // 3):
                a = node_id_to_idx[int(flat[3 * k + 0])]
                b = node_id_to_idx[int(flat[3 * k + 1])]
                c = node_id_to_idx[int(flat[3 * k + 2])]
                tris.append((a, b, c))
    finally:
        gmsh.finalize()

    return points.T, np.array(tris, dtype=np.int64).T


def build_periodic_magnet_mesh(polys: dict, geo_cfg: dict, rotor_angle_deg: float,
                                 mesh_size_mm: float, min_size_mm: float,
                                 ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build deterministic mesh for ALL magnets by template rotation.

    Strategy:
      1. Take magnet #0 polygon (already at its position) and de-rotate to
         angle = 0  (canonical center on +X axis)
      2. Mesh it ONCE with gmsh
      3. Rotate the resulting (verts, tris) to each of `num_poles` positions
      4. Tag every triangle with DOM_MAG_N or DOM_MAG_S by polarity

    Returns (verts_m (2,N), tris (3,M), cell_tags (M,))
    """
    from shapely.affinity import rotate as _shapely_rotate

    magnet_list = polys.get("magnets", [])
    num_poles = len(magnet_list)
    if num_poles == 0:
        return np.empty((2, 0)), np.empty((3, 0), dtype=np.int64), np.array([], dtype=np.int16)

    pole_pitch_deg = 360.0 / num_poles
    # Magnet #0 is centered at (0 + 0.5) * pole_pitch_deg + rotor_angle_deg
    first_angle_deg = 0.5 * pole_pitch_deg + rotor_angle_deg
    first_poly, _ = magnet_list[0]

    # De-rotate to canonical angle 0. Inflate slightly so the rendered template
    # always covers the gmsh-rotor's discretisation of the same boundary (the
    # two independent gmsh runs produce slightly different boundary vertices,
    # leaving thin "cracks" without this buffer).
    canonical_exact = _shapely_rotate(first_poly, -first_angle_deg, origin=(0.0, 0.0))
    try:
        canonical = canonical_exact.buffer(0.05, join_style=2)  # 0.05 mm outward
    except Exception:
        canonical = canonical_exact

    v_tpl_mm, t_tpl = _mesh_single_polygon(canonical, mesh_size_mm, min_size_mm)
    if v_tpl_mm.shape[1] == 0:
        return np.empty((2, 0)), np.empty((3, 0), dtype=np.int64), np.array([], dtype=np.int16)

    n_per = v_tpl_mm.shape[1]
    all_v: List[np.ndarray] = []
    all_t: List[np.ndarray] = []
    all_tags: List[int] = []
    offset = 0
    for i, (_, polarity) in enumerate(magnet_list):
        ang = math.radians((i + 0.5) * pole_pitch_deg + rotor_angle_deg)
        c, s = math.cos(ang), math.sin(ang)
        rx = v_tpl_mm[0] * c - v_tpl_mm[1] * s
        ry = v_tpl_mm[0] * s + v_tpl_mm[1] * c
        all_v.append(np.vstack([rx, ry]))
        all_t.append(t_tpl + offset)
        dom = DOM_MAG_N if polarity > 0 else DOM_MAG_S
        all_tags.extend([dom] * t_tpl.shape[1])
        offset += n_per

    verts_mm = np.hstack(all_v)
    tris = np.hstack(all_t)
    tags = np.array(all_tags, dtype=np.int16)
    return verts_mm * 1e-3, tris, tags


def build_periodic_coil_mesh(geo_cfg: dict, num_slots: int,
                              outer_r_mm: float, wire_target_mm: float = 0.2,
                              ) -> Tuple[np.ndarray, np.ndarray]:
    """Build a deterministic mesh of ALL copper wires by template replication.

    Strategy (user-requested):
      1. Mesh ONE wire (template) at a canonical position
      2. Translate to every wire position inside ONE slot pair (14 × 2 = 28 wires)
      3. Rotate that slot's wire block to every slot angular position
      4. Concatenate everything → deterministic, identical mesh for every wire

    Returns (verts_m (2,N), tris (3,M)) — coordinates in metres ready for
    appending to the global mesh.
    """
    wire_w  = float(geo_cfg.get("wire_width", 5.0))
    wire_h  = float(geo_cfg.get("wire_height", 0.6))
    wire_dx = float(geo_cfg.get("wire_spacing_x", 0.1))
    wire_dy = float(geo_cfg.get("wire_spacing_y", 0.13))
    n_wires = int(geo_cfg.get("num_wires_per_slot", 14))
    ins_w   = float(geo_cfg.get("insulation_thickness", 0.15))
    tooth_w = float(geo_cfg.get("tooth_width", 9.2))
    core_h  = float(geo_cfg.get("core_thickness", 4.2))

    # Match cadquery_geometry._create_coils geometry exactly
    right_x = tooth_w / 2.0 + ins_w + wire_dx / 2.0
    top_y   = outer_r_mm - core_h - ins_w - wire_dy / 2.0
    half_slots = num_slots // 2
    slot_pitch = 2 * math.pi / half_slots

    # Slight buffer (0.03 mm on all sides) so the wire rectangle visually
    # overlaps the surrounding slot-air mesh, covering any boundary cracks
    # between the template's discretisation and gmsh's discretisation.
    BUF = 0.03
    tpl_v, tpl_t = _structured_rect_mesh(wire_w + 2 * BUF, wire_h + 2 * BUF, wire_target_mm)
    tpl_v -= np.array([[BUF], [BUF]])    # shift so origin still at (0,0)
    n_per_wire = tpl_v.shape[1]

    # Each wire's (origin x, origin y) inside the slot at angle=0
    wire_origins: List[Tuple[float, float]] = []
    for step in range(n_wires):
        y_top = top_y - step * (wire_h + wire_dy)
        y_bot = y_top - wire_h
        # right side of slot
        wire_origins.append((right_x, y_bot))
        # left side of slot (mirror)
        wire_origins.append((-right_x - wire_w, y_bot))

    all_v_chunks: List[np.ndarray] = []
    all_t_chunks: List[np.ndarray] = []
    offset = 0
    for slot_i in range(half_slots):
        ang = slot_i * slot_pitch
        c_ang, s_ang = math.cos(ang), math.sin(ang)
        for ox, oy in wire_origins:
            # Translate template
            vx = tpl_v[0] + ox
            vy = tpl_v[1] + oy
            # Rotate by slot angle
            rx = vx * c_ang - vy * s_ang
            ry = vx * s_ang + vy * c_ang
            all_v_chunks.append(np.vstack([rx, ry]))
            all_t_chunks.append(tpl_t + offset)
            offset += n_per_wire

    verts_mm = np.hstack(all_v_chunks)
    tris     = np.hstack(all_t_chunks)
    return verts_mm * 1e-3, tris   # mm → m


def build_mesh_from_polygons(polys: dict,
                             rotor_angle_deg: float = 0.0,
                             mesh_size_mm: float = 1.5,
                             min_size_mm: float = 0.3,
                             normal_deviation_deg: float = 6.0,
                             aspect_ratio: float = 10.0,
                             periodic_coils: bool = False,
                             geo_cfg: Optional[dict] = None,
                             outer_air_factor: float = 1.0,
                             motion_band: bool = False,
                             band_thickness_mm: float = 0.4,
                             n_sectors: int = 1,
                             ) -> Tuple["MeshTri", np.ndarray]:
    """Construct a conforming triangle mesh from the CadQuery polygon dict.

    Uses gmsh OCC kernel + boolean fragments so that overlapping surfaces
    (magnets sitting in rotor pockets, coils in stator slots) get clean
    conforming interfaces automatically.

    Returns
    -------
    mesh        : scikit-fem MeshTri
    cell_tags   : (n_triangles,) int8 array of domain ids
    """
    import gmsh
    from skfem.io.meshio import from_meshio
    import meshio as _mio
    from shapely.geometry import Polygon as SPoly, MultiPolygon as SMPoly

    # interruptible=False skips signal handler install (only main-thread-safe)
    try:
        gmsh.initialize([], interruptible=False)
    except TypeError:
        gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        # Ansys-style "Curved Surface Meshing":
        #   - polygon vertex density is the refinement signal (dense points on
        #     fillet arcs → fine mesh there, sparse on straight runs → coarse)
        #   - MeshSizeFromCurvature ≈ 60 means ~6° per segment (= Ansys Normal
        #     Deviation = 3° doubled to keep triangle count reasonable)
        #   - MeshSizeMin bounds the minimum element so a 0.5 mm fillet doesn't
        #     spawn thousands of triangles
        gmsh.option.setNumber("Mesh.MeshSizeMin", min_size_mm)
        gmsh.option.setNumber("Mesh.MeshSizeMax", mesh_size_mm)
        # 360° / normal_deviation = points per 2π
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature",
                               max(8, 360.0 / max(0.5, normal_deviation_deg)))
        gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
        gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 1)
        gmsh.option.setNumber("Mesh.AnisoMax", aspect_ratio)
        gmsh.option.setNumber("Mesh.CharacteristicLengthFactor", 1.0)
        gmsh.option.setNumber("Mesh.Algorithm", 6)
        gmsh.option.setNumber("Geometry.Tolerance", 1e-5)
        gmsh.option.setNumber("Geometry.ToleranceBoolean", 1e-3)
        gmsh.model.add("motor2d")
        occ = gmsh.model.occ

        def _dedupe(pts: List[Tuple[float, float]], tol: float = 1e-3) -> List[Tuple[float, float]]:
            """Drop consecutive points closer than `tol` (mm). Keeps the loop closed implicitly."""
            out: List[Tuple[float, float]] = []
            for p in pts:
                if not out or (abs(p[0] - out[-1][0]) > tol or abs(p[1] - out[-1][1]) > tol):
                    out.append(p)
            # Also check closing edge
            if len(out) >= 2 and abs(out[0][0] - out[-1][0]) < tol and abs(out[0][1] - out[-1][1]) < tol:
                out.pop()
            return out

        def _shapely_to_occ(geom) -> List[int]:
            """Build OCC plane surfaces from a Shapely (Multi)Polygon.
            Uses addPolyline-like construction via dedup'd point loops.
            Returns the list of OCC surface tags created."""
            if geom is None or geom.is_empty:
                return []
            geoms = list(geom.geoms) if isinstance(geom, SMPoly) else [geom]
            tags: List[int] = []
            for g in geoms:
                if g.is_empty or g.area < 1e-6:
                    continue
                ext = _dedupe(list(g.exterior.coords)[:-1])
                if len(ext) < 3:
                    continue
                pt_tags = [occ.addPoint(x, y, 0) for x, y in ext]
                line_tags = []
                for i in range(len(pt_tags)):
                    a = pt_tags[i]; b = pt_tags[(i + 1) % len(pt_tags)]
                    if a == b:
                        continue
                    try:
                        line_tags.append(occ.addLine(a, b))
                    except Exception:
                        # Two points at identical location — skip
                        continue
                if len(line_tags) < 3:
                    continue
                outer_wire = occ.addCurveLoop(line_tags)
                hole_wires: List[int] = []
                for hole in g.interiors:
                    hext = _dedupe(list(hole.coords)[:-1])
                    if len(hext) < 3:
                        continue
                    hpts = [occ.addPoint(x, y, 0) for x, y in hext]
                    hlines = []
                    for i in range(len(hpts)):
                        a = hpts[i]; b = hpts[(i + 1) % len(hpts)]
                        if a == b:
                            continue
                        try:
                            hlines.append(occ.addLine(a, b))
                        except Exception:
                            continue
                    if len(hlines) >= 3:
                        hole_wires.append(occ.addCurveLoop(hlines))
                try:
                    surf = occ.addPlaneSurface([outer_wire, *hole_wires])
                    tags.append(surf)
                except Exception as e:
                    log.warning("addPlaneSurface failed: %s", e)
            return tags

        # 1) Motion band: split air_gap into rotor-side + band + stator-side
        polys = _add_motion_band(polys, motion_band=motion_band,
                                  band_thickness_mm=band_thickness_mm)
        # 2) Background air + (optional) outer far-field air ring
        polys = _add_background_air(polys, outer_air_factor=outer_air_factor)
        # 3) Symmetry: clip ALL polygons to a 360°/n_sectors wedge
        if n_sectors > 1:
            polys = _clip_polys_to_sector(polys, n_sectors=n_sectors)

        # Build OCC surfaces, keeping track of which dom each surface represents.
        # We list them in order from outer to inner; OCC `fragment` will produce
        # the boolean partition where smaller surfaces "punch out" the larger
        # ones automatically.
        domain_surfaces: List[Tuple[int, int]] = []   # (surf_tag, domain_id)

        for surf in _shapely_to_occ(polys.get("air_outer")):
            domain_surfaces.append((surf, DOM_OUTER))
        for surf in _shapely_to_occ(polys.get("air_background")):
            domain_surfaces.append((surf, DOM_AIR))
        for surf in _shapely_to_occ(polys.get("stator")):
            domain_surfaces.append((surf, DOM_STATOR))
        for surf in _shapely_to_occ(polys.get("rotor")):
            domain_surfaces.append((surf, DOM_ROTOR))
        for surf in _shapely_to_occ(polys.get("shaft")):
            domain_surfaces.append((surf, DOM_SHAFT))
        for surf in _shapely_to_occ(polys.get("air_gap")):
            domain_surfaces.append((surf, DOM_AIRGAP))
        for surf in _shapely_to_occ(polys.get("airgap_band")):
            domain_surfaces.append((surf, DOM_BAND))
        for mag_poly, polarity in polys.get("magnets", []):
            for surf in _shapely_to_occ(mag_poly):
                domain_surfaces.append((surf, DOM_MAG_N if polarity > 0 else DOM_MAG_S))
        for coil_poly in polys.get("coils", []):
            for surf in _shapely_to_occ(coil_poly):
                domain_surfaces.append((surf, DOM_COIL))

        occ.synchronize()

        # Capture each surface's centroid BEFORE fragmenting so we can re-classify
        # fragments by point-in-polygon vs the originals.
        original_centroids: List[Tuple[Tuple[float, float], int]] = []
        for surf, dom_id in domain_surfaces:
            try:
                com = occ.getCenterOfMass(2, surf)
                original_centroids.append(((com[0], com[1]), dom_id))
            except Exception:
                pass

        # Boolean fragment — produces a clean non-overlapping partition.
        # outDimTagsMap[i] = list of output (dim,tag) created from input i.
        dim_tags = [(2, s) for s, _ in domain_surfaces]
        try:
            fragment_out, out_map = occ.fragment(dim_tags, [])
        except Exception as e:
            log.warning("OCC fragment failed (%s) — falling back to original surfaces", e)
            fragment_out = dim_tags
            out_map = [[dt] for dt in dim_tags]

        occ.synchronize()

        # Classify each fragment: first by polygon membership for the small
        # features (coils, magnets), then fall back to radial annulus for the
        # large bulk (shaft, rotor, airgap, stator). This avoids issues with
        # the rotor/stator polygons having very wavy exteriors after slot/
        # magnet-pocket subtraction.
        from shapely.geometry import Point as _Pt
        import math as _m

        # Radial bounds (mm — same units as polygon coords). Handles both
        # single Polygon and MultiPolygon (after sector clip).
        def _iter_geoms(g):
            if g is None or g.is_empty:
                return []
            if hasattr(g, "geoms"):  # Multi*
                return [sub for sub in g.geoms if not sub.is_empty]
            return [g]

        def _all_ext_r(g):
            return [_m.hypot(x, y) for sub in _iter_geoms(g)
                    for x, y in list(sub.exterior.coords)]

        def _all_int_r(g):
            out = []
            for sub in _iter_geoms(g):
                for h in sub.interiors:
                    out.extend(_m.hypot(x, y) for x, y in list(h.coords))
            return out

        def _bounds_radial():
            r_shaft_in = 0.0
            r_shaft_out = 0.0
            r_rotor_out = 0.0
            r_stator_in = 0.0
            r_stator_out = 0.0
            if polys.get("shaft") is not None:
                ext_r = _all_ext_r(polys["shaft"])
                if ext_r:
                    r_shaft_out = max(ext_r)
                int_r = _all_int_r(polys["shaft"])
                if int_r:
                    r_shaft_in = min(int_r)
            if polys.get("rotor") is not None:
                ext_r = _all_ext_r(polys["rotor"])
                if ext_r:
                    r_rotor_out = max(ext_r)
            if polys.get("stator") is not None:
                ext_r = _all_ext_r(polys["stator"])
                if ext_r:
                    r_stator_out = max(ext_r)
                int_r = _all_int_r(polys["stator"])
                if int_r:
                    r_stator_in = min(int_r)
            if r_stator_in == 0 and polys.get("air_gap") is not None:
                ext_r = _all_ext_r(polys["air_gap"])
                if ext_r:
                    r_stator_in = max(ext_r)
            return r_shaft_in, r_shaft_out, r_rotor_out, r_stator_in, r_stator_out

        r_shaft_in, r_shaft_out, r_rotor_out, r_stator_in, r_stator_out = _bounds_radial()
        log.info("FEM radial bounds (mm): shaft_in=%.2f shaft_out=%.2f rotor_out=%.2f stator_in=%.2f stator_out=%.2f",
                 r_shaft_in, r_shaft_out, r_rotor_out, r_stator_in, r_stator_out)

        # Small-feature polygons (high priority)
        small_polys: List[Tuple[object, int]] = []
        for coil_poly in polys.get("coils", []):
            if coil_poly is not None:
                small_polys.append((coil_poly, DOM_COIL))
        for mag_poly, polarity in polys.get("magnets", []):
            if mag_poly is not None:
                small_polys.append((mag_poly, DOM_MAG_N if polarity > 0 else DOM_MAG_S))

        def _classify(x: float, y: float) -> int:
            p = _Pt(x, y)
            # 1) Small feature override (coils, magnets)
            for poly, d in small_polys:
                try:
                    if poly.contains(p):
                        return d
                except Exception:
                    continue
            # 2) Radial annulus for the bulk regions
            r = _m.hypot(x, y)
            if r < r_shaft_in:
                return DOM_AIR
            if r < r_shaft_out:
                return DOM_SHAFT
            if r < r_rotor_out:
                return DOM_ROTOR
            if r < r_stator_in:
                return DOM_AIRGAP
            if r <= r_stator_out + 0.1:
                return DOM_STATOR
            return DOM_AIR

        # Domain "specificity" — when a fragment came from multiple input
        # surfaces (e.g. an OCC overlap), pick the most specific domain
        # (coil > magnet > airgap > shaft > rotor > stator > air).
        # Air is the lowest priority: if anything else overlaps a sliver of
        # background-air, that material wins (prevents material→air bleed at
        # OCC tolerance boundaries).
        specificity = {
            DOM_COIL:    9,
            DOM_MAG_N:   8,
            DOM_MAG_S:   8,
            DOM_BAND:    7,
            DOM_AIRGAP:  6,
            DOM_SHAFT:   5,
            DOM_ROTOR:   4,
            DOM_STATOR:  3,
            DOM_AIR:     2,
            DOM_OUTER:   1,   # outer ring loses to everything else
        }

        # Map each output fragment tag → set of source domain ids (via out_map)
        frag_to_doms: Dict[int, List[int]] = {}
        for in_idx, in_dim_tag in enumerate(dim_tags):
            _, dom_id = domain_surfaces[in_idx]
            out_list = out_map[in_idx] if in_idx < len(out_map) else [in_dim_tag]
            for out_dim, out_tag in out_list:
                if out_dim != 2:
                    continue
                frag_to_doms.setdefault(int(out_tag), []).append(int(dom_id))

        # Classify each output fragment by its most-specific input source.
        frag_surfaces: List[Tuple[int, int]] = []
        for dim, tag in fragment_out:
            if dim != 2:
                continue
            sources = frag_to_doms.get(int(tag), [])
            if sources:
                dom_id = max(sources, key=lambda d: specificity.get(d, 0))
            else:
                # Fallback: centroid-based radial classifier (rare path)
                try:
                    com = occ.getCenterOfMass(2, tag)
                    dom_id = _classify(com[0], com[1])
                except Exception:
                    dom_id = DOM_AIR
            frag_surfaces.append((int(tag), int(dom_id)))

        # Group surfaces by domain id — one physical group per domain
        from collections import defaultdict
        by_dom: Dict[int, List[int]] = defaultdict(list)
        for tag, dom_id in frag_surfaces:
            by_dom[dom_id].append(tag)
        phys_to_dom: Dict[int, int] = {}
        for dom_id, tags in by_dom.items():
            phys_tag = int(dom_id) + 1   # avoid the reserved 0
            gmsh.model.addPhysicalGroup(2, tags, tag=phys_tag)
            gmsh.model.setPhysicalName(2, phys_tag, f"dom_{dom_id}")
            phys_to_dom[phys_tag] = int(dom_id)

        # No background field — refinement is driven by:
        #  (a) polygon vertex density (MeshSizeFromPoints = 1)
        #  (b) curvature inferred from the same vertex chain
        #     (MeshSizeFromCurvature = 60)
        # This matches Ansys: fine where the boundary actually curves, coarse
        # along long straight edges.

        gmsh.model.mesh.generate(2)

        # Pre-compute the (gmsh_element_tag → domain id) map using the physical
        # groups — this lets us recover correct cell tags AFTER the meshio
        # round-trip even though meshio drops tag metadata.
        elem_tag_to_dom: Dict[int, int] = {}
        for phys_tag, dom_id in phys_to_dom.items():
            ent_tags = gmsh.model.getEntitiesForPhysicalGroup(2, phys_tag)
            for surf_tag in ent_tags:
                el_types, el_tags_per_type, _ = gmsh.model.mesh.getElements(2, int(surf_tag))
                for et_idx, et in enumerate(el_types):
                    if int(et) != 2:
                        continue
                    for elem_tag in el_tags_per_type[et_idx]:
                        elem_tag_to_dom[int(elem_tag)] = int(dom_id)

        # Export through meshio (the known-working path) and read tags back via
        # the gmsh-element-tag map we just built.
        import tempfile, os
        tmp = tempfile.NamedTemporaryFile(suffix=".msh", delete=False)
        tmp.close()
        gmsh.write(tmp.name)
        mesh_io = _mio.read(tmp.name)
        os.unlink(tmp.name)

        # Read meshio's gmsh:geometrical to recover the per-cell element tag,
        # then look up the domain via our pre-built map.
        cell_tags_list: List[int] = []
        cell_data = mesh_io.cell_data or {}
        # meshio puts gmsh element ids in 'gmsh:physical' (if there's a phys group)
        # OR they can be in 'gmsh:dim_tags'. We use phys-IDs (= dom_id + 1):
        for idx, cell_block in enumerate(mesh_io.cells):
            if cell_block.type != "triangle":
                continue
            n_here = len(cell_block.data)
            phys_per = cell_data.get("gmsh:physical", [])
            if idx < len(phys_per) and len(phys_per[idx]) == n_here:
                for t in phys_per[idx]:
                    cell_tags_list.append(max(0, int(t) - 1))
            else:
                cell_tags_list.extend([DOM_AIR] * n_here)
    finally:
        gmsh.finalize()

    # Convert mm → m
    mesh_io.points = mesh_io.points * 1e-3
    mesh = from_meshio(mesh_io)
    cell_tags = np.array(cell_tags_list, dtype=np.int16)

    # ── PERIODIC COIL SUBSTITUTION ───────────────────────────────────────
    if periodic_coils and geo_cfg is not None:
        try:
            num_slots = sum(
                1 for _ in polys.get("coils", [])
            ) * 2 or int(geo_cfg.get("num_slots", 24))
            outer_r_mm = float(geo_cfg.get("stator_outer_radius",
                                            geo_cfg.get("stator_diameter", 150) / 2))
            wire_target_mm = max(0.15, min_size_mm)
            v_coil_m, t_coil = build_periodic_coil_mesh(
                geo_cfg, num_slots, outer_r_mm, wire_target_mm,
            )
            keep = cell_tags != DOM_COIL
            kept_t = mesh.t[:, keep]
            kept_tags = cell_tags[keep]
            n_old = mesh.p.shape[1]
            new_p = np.hstack([mesh.p, v_coil_m])
            new_t = np.hstack([kept_t, t_coil + n_old])
            new_tags = np.concatenate([
                kept_tags,
                np.full(t_coil.shape[1], DOM_COIL, dtype=np.int16),
            ])
            from skfem import MeshTri
            mesh = MeshTri(doflocs=new_p.astype(np.float64),
                           t=new_t.astype(np.int64))
            cell_tags = new_tags
            log.info("FEM: periodic coil substitution — added %d wire tris",
                     t_coil.shape[1])
        except Exception as e:
            log.warning("periodic coil substitution failed: %s", e)

    # NOTE: periodic-magnet template overlay (build_periodic_magnet_mesh) was
    # intentionally REMOVED.  The disjoint air_background polygon now ensures
    # gmsh's single fragment pass meshes every region (including the rotor-
    # pocket air above each magnet) without overlap, so no overlay is needed.

    # Attach the (possibly modified) polys dict to the classify_fn so the API
    # can render outlines that match the actual meshed geometry.
    try:
        _classify.polys = polys                  # type: ignore[attr-defined]
    except Exception:
        pass

    return mesh, cell_tags, _classify


def _read_cell_tags_by_dom(mesh_io) -> np.ndarray:
    """Map meshio physical-group ids → domain ids per triangle.

    With our new convention each physical group's tag equals the domain id
    plus one (to avoid the reserved 0).  Returns one int8 per triangle.
    """
    all_tags: List[int] = []
    phys_data = mesh_io.cell_data.get("gmsh:physical", [])
    for idx, cell_block in enumerate(mesh_io.cells):
        if cell_block.type != "triangle":
            continue
        n_tri_block = len(cell_block.data)
        if idx < len(phys_data) and len(phys_data[idx]) == n_tri_block:
            for t in phys_data[idx]:
                # phys group tag = dom_id + 1
                all_tags.append(max(0, int(t) - 1))
        else:
            all_tags.extend([DOM_AIR] * n_tri_block)
    return np.array(all_tags, dtype=np.int8)


# ─────────────────────────────────────────────────────────────────────────────
# 2.  Assemble + solve the linear magnetostatics problem
# ─────────────────────────────────────────────────────────────────────────────

def solve_magnetostatics(
    mesh,
    cell_tags: np.ndarray,
    materials: Dict[int, FEMMaterial],
) -> np.ndarray:
    """Linear 2-D magnetostatics solve.

    Returns the nodal A_z vector (shape n_nodes,) for the P1 basis.

    Equation:   ∫ ν ∇A_z·∇v  dΩ  =  ∫ J_z v dΩ  +  ∫ (Mx ∂v/∂y − My ∂v/∂x) dΩ

    Strategy: assemble one bilinear/linear form per material (piecewise-constant
    ν, J, M) and sum them.  Avoids the scikit-fem per-cell interpolation pitfall.
    """
    import time as _t
    from skfem import (
        Basis, ElementTriP1, BilinearForm, LinearForm,
        asm, condense, solve,
    )
    from skfem.helpers import dot, grad

    basis = Basis(mesh, ElementTriP1())

    @BilinearForm
    def stiffness(u, v, w):
        return dot(grad(u), grad(v))

    @LinearForm
    def rhs_unit(v, w):
        return 1.0 * v

    @LinearForm
    def rhs_dvdy(v, w):
        return grad(v)[1]   # ∂v/∂y

    @LinearForm
    def rhs_dvdx(v, w):
        return grad(v)[0]   # ∂v/∂x

    t0 = _t.time()
    n = basis.N
    from scipy.sparse import csr_matrix
    K_total = csr_matrix((n, n))
    f_total = np.zeros(n)

    # Unique cell tags actually present in the mesh
    unique_tags = np.unique(cell_tags)
    for tag in unique_tags:
        mat = materials.get(int(tag))
        if mat is None:
            continue
        cells_mask = (cell_tags == tag)
        cells_idx  = np.where(cells_mask)[0]
        if cells_idx.size == 0:
            continue
        sub_basis = Basis(mesh, ElementTriP1(), elements=cells_idx)

        nu = 1.0 / (MU0 * mat.mu_r)
        K_dom = asm(stiffness, sub_basis) * nu
        K_total = K_total + K_dom

        if mat.J_z != 0.0:
            f_total += asm(rhs_unit, sub_basis) * mat.J_z
        if mat.Mx != 0.0:
            f_total += asm(rhs_dvdy, sub_basis) * mat.Mx
        if mat.My != 0.0:
            f_total -= asm(rhs_dvdx, sub_basis) * mat.My

    # Dirichlet A_z = 0 on outer boundary nodes
    outer_nodes = _outer_boundary_nodes(mesh)
    A = solve(*condense(K_total.tocsr(), f_total, D=outer_nodes))
    log.info("FEM solve: %d nodes, %d triangles, %.2fs",
             basis.N, mesh.t.shape[1], _t.time() - t0)
    return A


def _outer_boundary_nodes(mesh) -> np.ndarray:
    """Return node ids on the outermost circular boundary (highest r)."""
    coords = mesh.p
    r = np.sqrt(coords[0] ** 2 + coords[1] ** 2)
    r_max = r.max()
    # Outer boundary = nodes within 0.5 mm of r_max
    return np.where(r >= r_max - 5e-4)[0]


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Sample A_z and derived B onto a regular grid (for canvas rendering)
# ─────────────────────────────────────────────────────────────────────────────

def sample_to_grid(
    mesh,
    A_nodal: np.ndarray,
    cell_tags: np.ndarray,
    materials: Dict[int, FEMMaterial],
    grid_size: int,
    extent_m: Tuple[float, float, float, float],
    classify_fn=None,   # optional (x_mm, y_mm) → domain_id
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Interpolate (A_z) onto a regular gs×gs grid; derive B = curl(A);
    reclassify domain on grid directly via classify_fn (recommended) or
    nearest-neighbour from mesh cells."""
    from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator

    xmin, xmax, ymin, ymax = extent_m
    xs = np.linspace(xmin, xmax, grid_size)
    ys = np.linspace(ymin, ymax, grid_size)
    XX, YY = np.meshgrid(xs, ys)

    pts = mesh.p.T              # (n_nodes, 2)
    interp_A = LinearNDInterpolator(pts, A_nodal, fill_value=0.0)
    A_z = interp_A(XX, YY)

    dy = ys[1] - ys[0]
    dx = xs[1] - xs[0]
    B_x =  np.gradient(A_z, dy, axis=0)
    B_y = -np.gradient(A_z, dx, axis=1)
    B_mag = np.sqrt(B_x ** 2 + B_y ** 2)

    if classify_fn is not None:
        # Grid-level classification (mm coordinates)
        domain = np.zeros((grid_size, grid_size), dtype=np.int8)
        # Vectorise: vectorize the per-pixel classifier
        flat_x = (XX * 1e3).ravel()    # m → mm
        flat_y = (YY * 1e3).ravel()
        out = np.array([classify_fn(fx, fy) for fx, fy in zip(flat_x, flat_y)],
                       dtype=np.int8)
        domain = out.reshape(grid_size, grid_size)
    else:
        cell_centroids = mesh.p[:, mesh.t].mean(axis=1).T
        dom_interp = NearestNDInterpolator(cell_centroids, cell_tags.astype(np.int32))
        domain = dom_interp(XX, YY).astype(np.int8)

    # J_z grid: directly from the material map by domain id
    J_z = np.zeros_like(A_z)
    for d in np.unique(domain):
        if int(d) in materials:
            J_z[domain == d] = materials[int(d)].J_z

    return A_z, B_x, B_y, B_mag, J_z, domain


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Top-level convenience: build materials dict, solve, sample
# ─────────────────────────────────────────────────────────────────────────────

def build_materials(
    I_ph: Dict[str, float],
    winding_layout: List[Tuple[str, int]],
    polys: dict,
    rotor_angle_deg: float,
    slot_area_m2: float,
    n_wires: int,
    Br: float = 1.19,
    mu_r_steel: float = 5000.0,
) -> Dict[int, FEMMaterial]:
    """Build the per-domain material map for the FEM solve.

    Coils share a single domain id (DOM_COIL) — but each magnet pair (DOM_MAG_N /
    DOM_MAG_S) has only ±polarity.  In practice the assembly is per-triangle, so
    we treat slots/coils and magnets uniformly with whichever cell tag falls on
    their triangles; J_z and M are averaged per pole.
    """
    # Average J_z over all slots (used as the coil region's representative source)
    # NOTE: a better implementation would tag each slot with its own id and apply
    # the per-slot current.  For phase-1 validation the average is sufficient.
    n_slot = len(winding_layout)
    total_J = 0.0
    for ph, direction in winding_layout:
        total_J += direction * I_ph[ph] * n_wires / slot_area_m2
    J_avg = total_J / max(n_slot, 1)

    # Tangential M magnitude (alternating per pole)
    M_mag = Br / MU0

    mats: Dict[int, FEMMaterial] = {
        DOM_AIR:    FEMMaterial("air",    mu_r=1.0),
        DOM_AIRGAP: FEMMaterial("airgap", mu_r=1.0),
        DOM_BAND:   FEMMaterial("band",   mu_r=1.0),    # motion-band air slip
        DOM_OUTER:  FEMMaterial("outer",  mu_r=1.0),    # far-field air ring
        DOM_STATOR: FEMMaterial("stator", mu_r=mu_r_steel),
        DOM_ROTOR:  FEMMaterial("rotor",  mu_r=mu_r_steel),
        DOM_SHAFT:  FEMMaterial("shaft",  mu_r=1000.0),
        DOM_COIL:   FEMMaterial("coil",   mu_r=1.0, J_z=J_avg),
        # Both magnet domains carry the tangential magnetization; the sign flips
        # via Mx/My computed downstream per-magnet (we'll override below).
        DOM_MAG_N:  FEMMaterial("mag_N", mu_r=1.05, Mx=0.0, My=M_mag),  # tangent at φ=0
        DOM_MAG_S:  FEMMaterial("mag_S", mu_r=1.05, Mx=0.0, My=-M_mag),
    }
    return mats


def fem_field2d(
    rotor_angle_deg: float = 0.0,
    gamma_deg: float = 0.0,
    grid_size: int = 150,
    mesh_size_mm: float = 1.6,
) -> FEMResult:
    """Top-level: build mesh + assemble + solve + sample.

    Mirrors the signature of routes/simulation.py::get_field2d so the FEM
    endpoint can drop in as a swap.
    """
    import time as _t
    from motor_ai_sim.cadquery_geometry import CadQueryMotor
    from motor_ai_sim.simulation.geometry_2d import (
        params_from_config, MotorDomains2D,
    )
    from motor_ai_sim.config import get_config

    t_start = _t.time()
    cfg  = get_config()
    sim  = cfg.get("simulation", {})
    geo  = cfg.get("geometry",   {})
    wind = cfg.get("winding",    {})

    p = params_from_config()
    d = MotorDomains2D(p)

    # ── Operating-point currents (γ=0 → q-axis, +π/2 shift) ──────────────
    I_phase_rms  = sim.get("max_current", 85.0)
    n_parallel   = wind.get("n_parallel", 2)
    n_wires      = geo.get("num_wires_per_slot", 14)
    pole_pairs   = p.num_poles // 2
    I_coil_peak  = I_phase_rms / n_parallel * math.sqrt(2)
    theta_e      = math.radians(rotor_angle_deg * pole_pairs + gamma_deg + 90.0)
    I_ph = {
        'A': I_coil_peak * math.cos(theta_e),
        'B': I_coil_peak * math.cos(theta_e - 2 * math.pi / 3),
        'C': I_coil_peak * math.cos(theta_e + 2 * math.pi / 3),
    }

    # ── Real CadQuery polygons at this rotor angle ────────────────────────
    motor = CadQueryMotor()
    polys = motor.get_2d_polygons(rotor_angle_deg=rotor_angle_deg)

    log.info("FEM: building triangle mesh (h=%.2f mm)…", mesh_size_mm)
    polys = _simplify_polys(polys, tol_mm=0.3)
    mesh, cell_tags, classify_fn = build_mesh_from_polygons(polys, rotor_angle_deg, mesh_size_mm)

    # Reclassify each triangle by its centroid (mm) — robust against gmsh tag loss
    tri_centroids_m = mesh.p[:, mesh.t].mean(axis=1)   # (2, n_tri) in metres
    cell_tags = np.array(
        [classify_fn(tri_centroids_m[0, i] * 1e3, tri_centroids_m[1, i] * 1e3)
         for i in range(tri_centroids_m.shape[1])],
        dtype=np.int8,
    )
    log.info("FEM: reclassified cells — %s", dict(zip(*np.unique(cell_tags, return_counts=True))))
    log.info("FEM: mesh has %d nodes, %d triangles", mesh.p.shape[1], mesh.t.shape[1])

    slot_area = p.slot_width_m * p.slot_height_m * p.fill_factor
    mats = build_materials(I_ph, d.winding_layout, polys, rotor_angle_deg,
                           slot_area, n_wires)

    # Solve
    A = solve_magnetostatics(mesh, cell_tags, mats)

    # Sample onto regular grid for canvas
    R = p.r_stator_out * 1.02
    extent = (-R, R, -R, R)
    A_g, Bx_g, By_g, Bmag_g, Jz_g, dom_g = sample_to_grid(
        mesh, A, cell_tags, mats, grid_size, extent, classify_fn=classify_fn,
    )

    return FEMResult(
        grid_size=grid_size,
        extent=extent,
        A_z=A_g, B_x=Bx_g, B_y=By_g, B_mag=Bmag_g,
        J_z=Jz_g, domain=dom_g,
        n_triangles=mesh.t.shape[1],
        n_nodes=mesh.p.shape[1],
        solve_time_s=_t.time() - t_start,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 5.  Full FEM pipeline with torque + losses (Simulation tab endpoint)
# ─────────────────────────────────────────────────────────────────────────────

def _per_triangle_B(mesh, A_nodal: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Compute B = (B_x, B_y) per triangle from P1 nodal A_z.
    Returns (B_x_per_tri, B_y_per_tri) — both shape (n_tri,).

    For a P1 element with vertices p0, p1, p2 and nodal A values A0, A1, A2:
        ∇A = Σ_i A_i ∇φ_i
        where ∇φ_0 = (y1 - y2, x2 - x1) / (2·area)  (and cyclic)
    Then B_x =  ∂A/∂y, B_y = -∂A/∂x  (2-D out-of-plane A_z convention).
    """
    p = mesh.p          # (2, n_nodes)
    t = mesh.t          # (3, n_tri)
    x0 = p[0, t[0]]; y0 = p[1, t[0]]
    x1 = p[0, t[1]]; y1 = p[1, t[1]]
    x2 = p[0, t[2]]; y2 = p[1, t[2]]
    A0 = A_nodal[t[0]]
    A1 = A_nodal[t[1]]
    A2 = A_nodal[t[2]]
    two_area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
    safe = np.where(np.abs(two_area) > 1e-18, two_area, 1e-18)
    dA_dx = (A0 * (y1 - y2) + A1 * (y2 - y0) + A2 * (y0 - y1)) / safe
    dA_dy = (A0 * (x2 - x1) + A1 * (x0 - x2) + A2 * (x1 - x0)) / safe
    return dA_dy, -dA_dx           # B_x, B_y


def _triangle_areas(mesh) -> np.ndarray:
    p = mesh.p; t = mesh.t
    x0 = p[0, t[0]]; y0 = p[1, t[0]]
    x1 = p[0, t[1]]; y1 = p[1, t[1]]
    x2 = p[0, t[2]]; y2 = p[1, t[2]]
    return 0.5 * np.abs((x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0))


def _maxwell_stress_torque(mesh, A_nodal: np.ndarray, r_ag_m: float,
                            stack_length_m: float,
                            theta_start: float = 0.0,
                            theta_end: float   = 2 * math.pi,
                            n_samples: int     = 720) -> float:
    """Maxwell stress tensor torque integrated on an air-gap circle arc.

    T = (L / μ₀) · r² · ∫ B_r · B_φ dφ   (over [theta_start, theta_end])

    Samples B at n_samples points on the arc r = r_ag, finds the host
    triangle for each, evaluates the constant-gradient B.  Returns torque
    in N·m for the integrated arc — caller multiplies by n_sectors when
    only a sector is meshed.
    """
    from matplotlib.tri import Triangulation
    tri = Triangulation(mesh.p[0], mesh.p[1], mesh.t.T)
    finder = tri.get_trifinder()

    phis = np.linspace(theta_start, theta_end, n_samples, endpoint=False)
    xs = r_ag_m * np.cos(phis)
    ys = r_ag_m * np.sin(phis)
    tri_idx = finder(xs, ys)

    Bx_per_tri, By_per_tri = _per_triangle_B(mesh, A_nodal)

    valid = tri_idx >= 0
    Bx = np.where(valid, Bx_per_tri[np.clip(tri_idx, 0, None)], 0.0)
    By = np.where(valid, By_per_tri[np.clip(tri_idx, 0, None)], 0.0)
    # Polar components at each sample point
    cos_p = np.cos(phis); sin_p = np.sin(phis)
    B_r   = Bx * cos_p + By * sin_p
    B_phi = -Bx * sin_p + By * cos_p

    dphi = (theta_end - theta_start) / n_samples
    return (stack_length_m / MU0) * r_ag_m ** 2 * float(np.sum(B_r * B_phi)) * dphi


def fem_solve_for_sim(
    rotor_angle_deg: float = 0.0,
    gamma_deg:       float = 0.0,
    mesh_size_mm:    float = 3.0,
    min_size_mm:     float = 0.3,
    outer_air_factor:float = 1.3,
    motion_band:     bool  = True,
    band_thickness_mm: float = 0.4,
    n_sectors:       int   = 4,
    stator_fillet_mm:float = 0.0,
) -> dict:
    """End-to-end FEM solve: build mesh on (possibly clipped) geometry,
    solve magnetostatics, compute Maxwell-stress torque and Steinmetz iron
    losses + I²R copper losses, return everything the Simulation tab needs.

    Multiplies INTEGRAL quantities (torque + iron + magnet eddy losses)
    by n_sectors so the values represent the full motor.  Copper loss is
    derived from phase currents directly (no mesh integration) so it's
    already a full-motor number.
    """
    import time as _t
    from motor_ai_sim.cadquery_geometry import CadQueryMotor
    from motor_ai_sim.simulation.geometry_2d import (
        params_from_config, MotorDomains2D,
    )
    from motor_ai_sim.config import get_config

    t_start = _t.time()
    cfg  = get_config()
    sim  = cfg.get("simulation", {})
    geo  = cfg.get("geometry",   {})
    wind = cfg.get("winding",    {})

    p = params_from_config()
    d = MotorDomains2D(p)
    pole_pairs   = p.num_poles // 2
    I_phase_rms  = sim.get("max_current", 85.0)
    n_parallel   = wind.get("n_parallel", 2)
    n_wires      = int(geo.get("num_wires_per_slot", 14))
    I_coil_peak  = I_phase_rms / n_parallel * math.sqrt(2)
    theta_e      = math.radians(rotor_angle_deg * pole_pairs + gamma_deg + 90.0)
    I_ph = {
        'A': I_coil_peak * math.cos(theta_e),
        'B': I_coil_peak * math.cos(theta_e - 2 * math.pi / 3),
        'C': I_coil_peak * math.cos(theta_e + 2 * math.pi / 3),
    }

    motor = CadQueryMotor()
    polys = motor.get_2d_polygons(rotor_angle_deg=rotor_angle_deg)
    polys = _simplify_polys(polys, tol_mm=0.005,
                             stator_fillet_mm=stator_fillet_mm)

    log.info("FEM-sim: building mesh (h=%.2f, n_sectors=%d, outer×%.2f, band=%s)",
             mesh_size_mm, n_sectors, outer_air_factor, motion_band)
    mesh, cell_tags, classify_fn = build_mesh_from_polygons(
        polys, rotor_angle_deg, mesh_size_mm,
        min_size_mm=min_size_mm,
        outer_air_factor=outer_air_factor,
        motion_band=motion_band,
        band_thickness_mm=band_thickness_mm,
        n_sectors=n_sectors,
        geo_cfg=motor.parameters,
    )
    cell_tags = cell_tags.astype(np.int8)

    slot_area = p.slot_width_m * p.slot_height_m * p.fill_factor
    mats = build_materials(I_ph, d.winding_layout, polys, rotor_angle_deg,
                            slot_area, n_wires)

    t_solve_start = _t.time()
    A = solve_magnetostatics(mesh, cell_tags, mats)
    t_solve = _t.time() - t_solve_start
    # Sanitize any NaN/Inf so the response stays JSON-compliant.  Bad nodes
    # become zero — they show up as background-coloured spots in the canvas
    # but don't crash the whole render.
    A = np.nan_to_num(A, nan=0.0, posinf=0.0, neginf=0.0)
    n_bad = int(np.sum(~np.isfinite(A))) if A.size else 0
    if n_bad:
        log.warning("FEM: %d non-finite A values clamped to 0", n_bad)

    # ── Per-triangle B, |B| ───────────────────────────────────────────────
    Bx_tri, By_tri = _per_triangle_B(mesh, A)
    Bmag_tri = np.sqrt(Bx_tri ** 2 + By_tri ** 2)
    Bx_tri = np.nan_to_num(Bx_tri, nan=0.0, posinf=0.0, neginf=0.0)
    By_tri = np.nan_to_num(By_tri, nan=0.0, posinf=0.0, neginf=0.0)
    Bmag_tri = np.nan_to_num(Bmag_tri, nan=0.0, posinf=0.0, neginf=0.0)
    areas    = _triangle_areas(mesh)               # m² for unit stack

    # ── Torque via Maxwell stress on air-gap circle (sector arc × n) ──────
    r_ag_m = 0.5 * (p.r_rotor_out + p.r_stator_in)      # mid-air-gap
    theta_end = 2 * math.pi if n_sectors <= 1 else (2 * math.pi / n_sectors)
    T_sector = _maxwell_stress_torque(
        mesh, A, r_ag_m, p.stack_length,
        theta_start=0.0, theta_end=theta_end, n_samples=720,
    )
    T_em_Nm = T_sector * (n_sectors if n_sectors > 1 else 1)

    # ── Losses ────────────────────────────────────────────────────────────
    # Steinmetz-style iron loss density:  P/V  =  k_iron · f^α · B^β   [W/m³]
    # Copper:  3-phase I²R  with R_phase from config (≈ analytical solver).
    freq = sim.get("rpm", 3950) / 60 * pole_pairs       # electrical Hz

    # Per-domain mass integration
    rho_steel  = float(cfg.get("materials", {}).get("M19_29G", {})
                         .get("density_kg_m3", 7650.0))
    rho_magnet = float(cfg.get("materials", {}).get("N42", {})
                         .get("density_kg_m3", 7500.0))
    # Specific-loss coefficients (W/kg at 1 T, 50 Hz) ≈ analytical solver
    k_iron_W_kg_1T_50Hz = 1.5
    alpha_f             = 1.6     # frequency exponent (>1 to penalise harmonics)
    beta_B              = 2.0     # flux-density exponent

    # Volume per triangle = area × stack_length [m³]
    vol = areas * p.stack_length

    def _domain_iron_loss(tag: int, k: float, rho: float) -> float:
        mask = cell_tags == tag
        if not np.any(mask):
            return 0.0
        # ⟨B^β⟩ weighted by volume
        Bp = Bmag_tri[mask] ** beta_B
        m  = vol[mask] * rho            # mass per triangle [kg]
        return k * (freq / 50.0) ** alpha_f * float(np.sum(Bp * m))

    P_fe_stator = _domain_iron_loss(DOM_STATOR, k_iron_W_kg_1T_50Hz, rho_steel)
    P_fe_rotor  = _domain_iron_loss(DOM_ROTOR,  k_iron_W_kg_1T_50Hz, rho_steel)
    P_mag_eddy  = _domain_iron_loss(DOM_MAG_N,  0.3, rho_magnet) \
                + _domain_iron_loss(DOM_MAG_S,  0.3, rho_magnet)

    mult = n_sectors if n_sectors > 1 else 1
    P_fe_total = (P_fe_stator + P_fe_rotor) * mult
    P_mag_total = P_mag_eddy * mult

    # Copper loss — phase currents × R_phase  (×3 phases)
    R_phase = float(wind.get("phase_resistance_ohm", 0.018))
    P_cu = 3 * I_phase_rms ** 2 * R_phase

    P_loss_total = P_fe_total + P_mag_total + P_cu
    rpm = sim.get("rpm", 3950)
    P_mech = T_em_Nm * 2 * math.pi * rpm / 60
    eff = P_mech / max(P_mech + P_loss_total, 1e-6) if P_mech > 0 else 0.0

    # ── Outlines (for the renderer; matches /mesh/build2d format) ─────────
    polys_for_outlines = getattr(classify_fn, "polys", polys)

    return {
        "ok": True,
        "rotor_angle_deg": rotor_angle_deg,
        "gamma_deg":       gamma_deg,
        "n_sectors":       n_sectors,
        "symmetry_mult":   mult,
        "n_vertices":      int(mesh.p.shape[1]),
        "n_triangles":     int(mesh.t.shape[1]),
        "vertices":        mesh.p.T.tolist(),       # metres
        "triangles":       mesh.t.T.tolist(),
        "domain_per_tri":  cell_tags.tolist(),
        "A_z_per_node":    A.tolist(),               # Wb/m
        "Bmag_per_tri":    Bmag_tri.tolist(),
        "extent": [
            float(mesh.p[0].min()), float(mesh.p[0].max()),
            float(mesh.p[1].min()), float(mesh.p[1].max()),
        ],
        "polys_for_outlines": polys_for_outlines,
        # ── Physics quantities (with n_sectors multiplier already applied) ──
        "T_em_Nm":       round(T_em_Nm, 4),
        "P_cu_W":        round(P_cu, 1),
        "P_fe_W":        round(P_fe_total, 1),
        "P_mag_eddy_W":  round(P_mag_total, 1),
        "P_loss_total_W":round(P_loss_total, 1),
        "P_mech_W":      round(P_mech, 1),
        "efficiency":    round(eff, 4),
        "freq_Hz":       round(freq, 2),
        "rpm":           rpm,
        "solve_time_s":  round(t_solve, 2),
        "total_time_s":  round(_t.time() - t_start, 2),
    }
