"""mesh-skfem — capability `mesh`: GeometryIR -> MeshIR.

Thin seam over build_mesh_from_polygons (gmsh + skfem). Emits a tagged MeshIR via
the contracts adapter. Lazy imports keep the registry (and /api/modules) cheap;
the real gmsh build runs only on demand.

Agent brief: own ONLY meshing. Input = geometry (params/live config today; a
GeometryIR directly in a later pass). Output = motor_ai_sim.contracts.MeshIR.
Must keep domain tags meaningful so solvers can pick regions by role.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from ..contracts import CONTRACTS_VERSION, MeshIR
from ..contracts.adapters import mesh_ir_from_skfem, stamp
from .base import ModuleManifest, UIContribution


class SkfemMesh2D:
    NAME = "mesh-skfem"
    CAPABILITY = "mesh"
    VERSION = "0.1.0"

    def manifest(self) -> ModuleManifest:
        return ModuleManifest(
            name=self.NAME, version=self.VERSION, capability=self.CAPABILITY, kind="compute",
            contracts_version=CONTRACTS_VERSION, depends_on=["geometry.2d"],
            summary="gmsh + skfem triangular mesh with domain tags -> MeshIR",
            ui=UIContribution(panel_id="mesh", title="Mesh",
                              frontend_module="components/mesh/MeshPanel", order=40))

    def build(self, params: Optional[Dict[str, Any]] = None, *, rotor_angle_deg: float = 0.0,
              mesh_size_mm: float = 2.0, min_size_mm: float = 0.4, n_sectors: int = 1) -> MeshIR:
        from motor_ai_sim.config import get_config
        from motor_ai_sim.cadquery_geometry import CadQueryMotor
        from motor_ai_sim.simulation import fem_solver_2d as fs

        cfg = get_config()
        geo = dict(cfg["geometry"])
        if params:
            geo.update(params)
        t0 = time.time()
        motor = CadQueryMotor(); motor.set_parameters(geo)
        polys = motor.get_2d_polygons(rotor_angle_deg)
        mesh, cell_tags, _ = fs.build_mesh_from_polygons(
            polys, rotor_angle_deg, float(mesh_size_mm),
            min_size_mm=float(min_size_mm), geo_cfg=geo, n_sectors=int(n_sectors))
        tag_names = {fs.DOM_AIR: "air", fs.DOM_STATOR: "stator", fs.DOM_COIL: "coil",
                     fs.DOM_AIRGAP: "air_gap", fs.DOM_MAG_N: "magnet_n", fs.DOM_ROTOR: "rotor",
                     fs.DOM_SHAFT: "shaft", fs.DOM_BAND: "band", fs.DOM_OUTER: "air_outer",
                     fs.DOM_MAG_S: "magnet_s"}
        return mesh_ir_from_skfem(
            mesh, cell_tags, tag_names=tag_names,
            slip_radius_mm=(float(polys["mid_r_mm"]) if polys.get("mid_r_mm") is not None else None),
            provenance=stamp(self.NAME, version=self.VERSION, elapsed_s=time.time() - t0))

    def run(self, payload: Optional[Dict[str, Any]] = None) -> MeshIR:
        p = payload or {}
        return self.build(p.get("params"), rotor_angle_deg=float(p.get("rotor_angle_deg", 0.0)),
                          mesh_size_mm=float(p.get("mesh_size_mm", 2.0)), n_sectors=int(p.get("n_sectors", 1)))
