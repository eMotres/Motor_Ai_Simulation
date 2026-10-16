"""EXAMPLE external geometry plugin — a surface-PM (SPM) motor, built FROM SCRATCH
(no CadQuery), to prove the geometry Plugin-SDK.

This is a DIFFERENT topology from the built-in AeroStator (spoke PM): here the
magnets sit on the rotor SURFACE. Yet it emits the same `GeometryIR` contract, so
the unchanged downstream pipeline (mesh -> solver -> cost) accepts it.

It is also the reference others copy. To add your own geometry:
  1. Write a class with CAPABILITY = "geometry.2d.<your_id>", a manifest() and a
     run(payload) -> GeometryIR (build polys, then geometry_ir_from_polys()).
  2. Make it pass motor_ai_sim.contracts.conformance.assert_geometry_provider.
  3. Drop the file in this folder — discovery registers it, no core edit.
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, Optional

from ...contracts import CONTRACTS_VERSION, GeometryIR
from ...contracts.adapters import geometry_ir_from_polys, stamp
from ..base import ModuleManifest, UIContribution

# Default SPM cross-section (mm). All radii concentric; regions don't overlap and
# leave a real air gap — so it both conforms AND meshes.
DEFAULTS: Dict[str, float] = dict(
    num_slots=12, num_poles=8,
    r_shaft=8.0,            # shaft disk
    r_magnet_in=22.0,       # rotor iron outer = magnet inner
    r_magnet_out=26.0,      # magnet (surface) outer = rotor outer
    r_stator_bore=27.0,     # stator inner (1 mm air gap)
    r_coil_in=29.0,         # coils start just below the tooth shoe
    r_coil_out=42.0,        # coils end below the yoke
    r_stator_out=50.0,      # stator outer
    pole_fill=0.82,         # magnet angular coverage per pole
    slot_fill=0.5,          # coil angular coverage per slot
    motor_length=30.0,      # stack length (mm) — used by the cost module
)


def _circle(r: float, n: int = 96):
    from shapely.geometry import Polygon
    return Polygon([(r * math.cos(2 * math.pi * i / n), r * math.sin(2 * math.pi * i / n)) for i in range(n)])


def _annulus(ri: float, ro: float, n: int = 96):
    return _circle(ro, n).difference(_circle(ri, n))


def _sector(r_in: float, r_out: float, a_c: float, span: float, n: int = 18):
    """Annular sector centred at angle a_c (rad), angular width `span`."""
    from shapely.geometry import Polygon
    a0, a1 = a_c - span / 2.0, a_c + span / 2.0
    angs = [a0 + (a1 - a0) * i / (n - 1) for i in range(n)]
    outer = [(r_out * math.cos(a), r_out * math.sin(a)) for a in angs]
    inner = [(r_in * math.cos(a), r_in * math.sin(a)) for a in reversed(angs)]
    return Polygon(outer + inner)


class RingSurfacePMGeometry2D:
    """Example plugin: a 12-slot / 8-pole surface-PM 2D cross-section."""

    NAME = "geometry-2d-ring-spm"
    CAPABILITY = "geometry.2d.ring_spm"   # namespaced so it coexists with geometry.2d
    VERSION = "0.1.0"

    def manifest(self) -> ModuleManifest:
        return ModuleManifest(
            name=self.NAME, version=self.VERSION, capability=self.CAPABILITY, kind="compute",
            contracts_version=CONTRACTS_VERSION, inputs=["ParameterSet"], outputs=["GeometryIR"],
            summary="EXAMPLE plugin: surface-PM (SPM) 2D cross-section -> GeometryIR(dim=2)",
            ui=UIContribution(panel_id="geometry", title="Geometry (SPM example)",
                              frontend_module="components/geometry/GeometryPanel", order=21, as_tab=False))

    def build(self, params: Optional[Dict[str, Any]] = None) -> GeometryIR:
        from shapely.ops import unary_union

        p: Dict[str, Any] = dict(DEFAULTS)
        if params:
            p.update(params)
        S, P = int(p["num_slots"]), int(p["num_poles"])
        r_mag_in, r_mag_out = float(p["r_magnet_in"]), float(p["r_magnet_out"])
        r_bore, r_so = float(p["r_stator_bore"]), float(p["r_stator_out"])
        r_ci, r_co = float(p["r_coil_in"]), float(p["r_coil_out"])

        magnets = [
            (_sector(r_mag_in, r_mag_out, 2 * math.pi * k / P, (2 * math.pi / P) * float(p["pole_fill"])),
             1.0 if k % 2 == 0 else -1.0)
            for k in range(P)
        ]
        coils = [
            _sector(r_ci, r_co, 2 * math.pi * k / S + math.pi / S, (2 * math.pi / S) * float(p["slot_fill"]))
            for k in range(S)
        ]
        polys: Dict[str, Any] = {
            "shaft": _circle(float(p["r_shaft"])),
            "rotor": _annulus(float(p["r_shaft"]), r_mag_in),
            "in_band": _annulus(r_mag_out, r_bore),                 # air gap
            "out_band": _annulus(r_so, r_so + 2.0),                 # thin outer air ring
            "magnets": magnets,
            "coils": coils,
            "stator": _annulus(r_bore, r_so).difference(unary_union(coils)),
            "mid_r_mm": (r_mag_out + r_bore) / 2.0,                 # slip radius (mid air-gap)
            "r_outer_boundary_mm": r_so + 2.0,
        }
        return geometry_ir_from_polys(
            polys,
            parameters={**p, "n_sectors": 1, "num_slots": S, "num_poles": P},
            materials={"stator": "M19_29Ga", "rotor": "M19_29Ga", "magnet": "N42"},
            provenance=stamp(self.NAME, version=self.VERSION, elapsed_s=0.0),
        )

    def run(self, payload: Optional[Dict[str, Any]] = None) -> GeometryIR:
        t0 = time.time()
        gir = self.build((payload or {}).get("params"))
        if gir.provenance:
            gir.provenance.elapsed_s = time.time() - t0
        return gir
