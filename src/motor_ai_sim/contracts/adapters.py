"""Adapters: today's monolith data structures  ->  contract IR.

These let us route the EXISTING geometry/mesh/solver outputs through the new
contracts WITHOUT rewriting the producers yet (Phase 0). In Phase 1 each module
will emit IR directly and these become thin shims / disappear.

Depends only on numpy + (optionally) shapely geometry objects passed in — never
on the rest of motor_ai_sim, so contracts stays the dependency leaf.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from . import CONTRACTS_VERSION
from .common import Provenance
from .geometry_ir import GeometryIR, Region, RegionRole, Ring, Symmetry
from .mesh_ir import MeshIR
from .result_ir import FieldArray, FieldResults, ResultIR, ScalarResults, SeriesResults


# ── geometry: polys dict (get_2d_polygons) -> GeometryIR ─────────────────────
def _poly_pieces(geom: Any) -> List[Tuple[Ring, List[Ring]]]:
    """Yield (exterior, holes) for a shapely Polygon or every part of a MultiPolygon."""
    if geom is None or getattr(geom, "is_empty", True):
        return []
    gtype = getattr(geom, "geom_type", None)
    parts = [geom] if gtype == "Polygon" else (list(geom.geoms) if gtype == "MultiPolygon" else [])
    out: List[Tuple[Ring, List[Ring]]] = []
    for p in parts:
        ext = Ring(points=[[float(x), float(y)] for x, y in p.exterior.coords])
        holes = [Ring(points=[[float(x), float(y)] for x, y in r.coords]) for r in p.interiors]
        out.append((ext, holes))
    return out


def geometry_ir_from_polys(
    polys: Dict[str, Any],
    *,
    passport: Optional[Dict[str, Any]] = None,
    parameters: Optional[Dict[str, Any]] = None,
    materials: Optional[Dict[str, str]] = None,   # role name -> library material name
    provenance: Optional[Provenance] = None,
) -> GeometryIR:
    """Convert the shapely polygon dict from CadQueryMotor.get_2d_polygons() to GeometryIR."""
    mats = materials or {}
    regions: List[Region] = []

    def add(key: str, role: RegionRole, material: Optional[str]) -> None:
        for i, (ext, holes) in enumerate(_poly_pieces(polys.get(key))):
            rid = key if i == 0 else f"{key}_{i}"
            regions.append(Region(id=rid, role=role, exterior=ext, holes=holes, material=material))

    add("stator", RegionRole.STATOR, mats.get("stator"))
    add("rotor", RegionRole.ROTOR, mats.get("rotor"))
    add("shaft", RegionRole.SHAFT, mats.get("shaft"))
    add("in_band", RegionRole.AIR_GAP, None)
    add("out_band", RegionRole.AIR_OUTER, None)

    # magnets: list of (Polygon, polarity)
    for i, item in enumerate(polys.get("magnets", []) or []):
        mp, pol = item if isinstance(item, (tuple, list)) else (item, None)
        for j, (ext, holes) in enumerate(_poly_pieces(mp)):
            rid = f"magnet_{i}" if j == 0 else f"magnet_{i}_{j}"
            regions.append(Region(id=rid, role=RegionRole.MAGNET, exterior=ext, holes=holes,
                                  material=mats.get("magnet"),
                                  polarity_deg=(float(pol) if pol is not None else None)))

    # coils: list of Polygon (electrical phase map is applied by the EM module)
    for i, cp in enumerate(polys.get("coils", []) or []):
        for j, (ext, holes) in enumerate(_poly_pieces(cp)):
            rid = f"coil_{i}" if j == 0 else f"coil_{i}_{j}"
            regions.append(Region(id=rid, role=RegionRole.COIL, exterior=ext, holes=holes))

    # slot liner + wire enamel: lists of Polygon (EM-inert; thermal/cost domains)
    for key, role in (("slot_insulation", RegionRole.SLOT_INSULATION),
                      ("wire_insulation", RegionRole.WIRE_INSULATION)):
        for i, ip in enumerate(polys.get(key, []) or []):
            for j, (ext, holes) in enumerate(_poly_pieces(ip)):
                rid = f"{key}_{i}" if j == 0 else f"{key}_{i}_{j}"
                regions.append(Region(id=rid, role=role, exterior=ext, holes=holes,
                                      material=mats.get(key)))

    sym = Symmetry(
        n_sectors=int((parameters or {}).get("n_sectors", 1) or 1),
        slip_radius_mm=(float(polys["mid_r_mm"]) if polys.get("mid_r_mm") is not None else None),
        outer_bc_radius_mm=(float(polys["r_outer_boundary_mm"])
                            if polys.get("r_outer_boundary_mm") is not None else None),
    )
    return GeometryIR(regions=regions, symmetry=sym,
                      passport=passport or {}, parameters=parameters or {},
                      provenance=provenance)


# ── geometry: GeometryIR -> polys dict (inverse, for the existing mesher) ────
def polys_from_geometry_ir(gir: Any) -> Dict[str, Any]:
    """Inverse of geometry_ir_from_polys: rebuild the shapely polys dict the
    existing mesher consumes. This is the geometry.2d -> mesh handoff bridge until
    the mesher consumes GeometryIR natively."""
    from shapely.geometry import Polygon

    def poly(region: Any):
        ext = [(float(p[0]), float(p[1])) for p in region.exterior.points]
        holes = [[(float(p[0]), float(p[1])) for p in h.points] for h in region.holes]
        return Polygon(ext, holes)

    out: Dict[str, Any] = {"magnets": [], "coils": [], "slot_insulation": [], "wire_insulation": []}
    for r in gir.regions:
        role = getattr(r.role, "value", r.role)
        if role == "stator":
            out["stator"] = poly(r)
        elif role == "rotor":
            out["rotor"] = poly(r)
        elif role == "shaft":
            out["shaft"] = poly(r)
        elif role == "air_gap":
            out["in_band"] = poly(r)
        elif role == "air_outer":
            out["out_band"] = poly(r)
        elif role == "magnet":
            out["magnets"].append((poly(r), r.polarity_deg))
        elif role == "coil":
            out["coils"].append(poly(r))
        elif role == "slot_insulation":
            out["slot_insulation"].append(poly(r))
        elif role == "wire_insulation":
            out["wire_insulation"].append(poly(r))
    if gir.symmetry.slip_radius_mm is not None:
        out["mid_r_mm"] = float(gir.symmetry.slip_radius_mm)
    if gir.symmetry.outer_bc_radius_mm is not None:
        out["r_outer_boundary_mm"] = float(gir.symmetry.outer_bc_radius_mm)
    return out


# ── mesh: skfem MeshTri + tags -> MeshIR ─────────────────────────────────────
def mesh_ir_from_skfem(
    mesh: Any,                       # skfem MeshTri: mesh.p (2,N), mesh.t (3,M)
    cell_tags: Any,                  # int array (M,)
    *,
    tag_names: Optional[Dict[int, str]] = None,
    slip_radius_mm: Optional[float] = None,
    provenance: Optional[Provenance] = None,
) -> MeshIR:
    return MeshIR(
        vertices=np.asarray(mesh.p).T.astype(float),
        triangles=np.asarray(mesh.t).T.astype(int),
        cell_tags=np.asarray(cell_tags).astype(int).ravel(),
        tag_names=dict(tag_names or {}),
        slip_radius_mm=slip_radius_mm,
        provenance=provenance,
    )


def skfem_from_mesh_ir(mesh_ir: Any) -> Tuple[Any, "np.ndarray"]:
    """Inverse of mesh_ir_from_skfem: rebuild a skfem MeshTri + cell_tags from a
    MeshIR. This is the mesh -> solver handoff: a solver consumes the `mesh`
    module's IR as its computational mesh instead of meshing again. Lazy skfem
    import keeps contracts the dependency leaf (numpy-only at import time)."""
    from skfem import MeshTri

    p = np.asarray(mesh_ir.vertices, dtype=float).T          # (2, N)
    t = np.ascontiguousarray(np.asarray(mesh_ir.triangles).T.astype(np.int64))  # (3, M)
    cell_tags = np.asarray(mesh_ir.cell_tags).astype(int)
    return MeshTri(p, t), cell_tags


# ── result: transient solver dict (sbres) -> ResultIR ────────────────────────
def result_ir_from_transient(sbres: Dict[str, Any], *, provenance: Optional[Provenance] = None) -> ResultIR:
    """Map the sliding-band transient result dict (and its 'summary') to ResultIR."""
    s = sbres.get("summary") or {}

    def f(*keys: str) -> Optional[float]:
        # Prefer the 'summary' (scalars) over the top-level dict, and accept only
        # numeric scalars — several keys (P_cu_W, …) also exist as time-series lists.
        for src in (s, sbres):
            for k in keys:
                v = src.get(k)
                if isinstance(v, bool):
                    continue
                if isinstance(v, (int, float)):
                    return float(v)
        return None

    scalars = ScalarResults(
        torque_Nm=f("T_avg_Nm", "T_em_avg_Nm"),
        torque_ripple_pct=f("T_ripple_pct", "T_ripple_filt_pct"),
        p_copper_W=f("P_stranded_W", "P_cu_W"),
        p_iron_W=f("P_core_W", "P_fe_W"),
        p_magnet_eddy_W=f("P_mag_eddy_W"),
        p_shaft_eddy_W=f("P_shaft_eddy_W"),
        p_loss_total_W=f("P_loss_total_W"),
        p_mech_W=f("P_mech_W"),
        efficiency=f("efficiency"),
        v_phase_peak_V=f("V_phase_peak_V", "V_peak"),
        mass_kg=f("mass_total_kg", "mass_kg"),
    )

    series: Optional[SeriesResults] = None
    if sbres.get("time_s"):
        extra: Dict[str, List[float]] = {}
        for k in ("P_cu_W", "P_fe_W", "P_mag_eddy_W", "P_shaft_eddy_W",
                  "P_loss_total_W", "I_A", "I_B", "I_C"):
            if sbres.get(k):
                extra[k] = [float(x) for x in sbres[k]]
        series = SeriesResults(
            time_s=[float(x) for x in sbres["time_s"]],
            torque_Nm=[float(x) for x in (sbres.get("T_em_filt_Nm") or sbres.get("T_em_Nm") or [])],
            extra=extra,
        )

    # Carry the full payload (minus heavy per-frame fields) so a UI can migrate
    # onto the kernel byte-identically; the typed scalars/series stay the contract.
    raw = {k: v for k, v in sbres.items() if k != "frames"}
    return ResultIR(physics="em_transient", scalars=scalars, series=series, raw=raw, provenance=provenance)


def stamp(module: str, *, version: str = "0.1.0", input_hash: Optional[str] = None,
          elapsed_s: Optional[float] = None) -> Provenance:
    """Convenience Provenance builder for a producing module."""
    return Provenance(module=module, module_version=version,
                      contracts_version=CONTRACTS_VERSION,
                      input_hash=input_hash, elapsed_s=elapsed_s)
