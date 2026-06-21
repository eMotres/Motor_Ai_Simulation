"""GeometryIR — neutral, geometry-AGNOSTIC description of a machine cross-section.

Any geometry module emits this (AeroStator Core today; arbitrary geometries
later). The mesh module consumes it without knowing which geometry produced it.
That decoupling is the whole point: swap the geometry implementation, keep the
rest of the pipeline.
"""
from __future__ import annotations

from enum import Enum
from typing import List, Optional, Dict, Any

from pydantic import BaseModel, Field, ConfigDict

from .common import Provenance


class RegionRole(str, Enum):
    """What a region physically IS — drives meshing + which solver cares about it."""

    STATOR = "stator"
    ROTOR = "rotor"
    MAGNET = "magnet"
    COIL = "coil"
    SHAFT = "shaft"
    AIR_GAP = "air_gap"        # inner air disk that rotates with the rotor (in-band)
    AIR_OUTER = "air_outer"    # outer air to the far-field BC (stationary, out-band)
    BAND = "band"              # sliding / motion band inside the air gap


class Ring(BaseModel):
    """A closed polygon ring as (x, y) points in millimetres."""

    points: List[List[float]]  # [[x, y], ...]


class WindingTag(BaseModel):
    """Coil region's electrical identity (which phase, direction, turns)."""

    phase: str                 # "A" | "B" | "C"
    sign: int = 1              # +1 / -1 current direction
    turns: Optional[float] = None


class Region(BaseModel):
    """One homogeneous region of the cross-section."""

    model_config = ConfigDict(extra="forbid")

    id: str
    role: RegionRole
    exterior: Ring
    holes: List[Ring] = Field(default_factory=list)
    material: Optional[str] = None        # library material name (resolved by a module)
    polarity_deg: Optional[float] = None  # magnets: Br direction (deg); sign also ok
    winding: Optional[WindingTag] = None


class Symmetry(BaseModel):
    """Rotational symmetry + the radii the solvers need for BCs / sliding band."""

    n_sectors: int = 1                       # 1 = full ring; k = a 360/k periodic sector
    slip_radius_mm: Optional[float] = None   # sliding-band mid radius
    outer_bc_radius_mm: Optional[float] = None  # outer Dirichlet boundary radius


class GeometryIR(BaseModel):
    """Complete neutral geometry payload."""

    model_config = ConfigDict(extra="forbid")

    dim: int = 2                              # 2D today; 3D later (regions → solids)
    units: str = "mm"
    regions: List[Region]
    symmetry: Symmetry = Field(default_factory=Symmetry)
    # Derived scalars a UI / cost / report needs without re-deriving:
    # stator_outer_radius, stator_inner_radius, rotor_*_radius, num_slots, num_poles, …
    passport: Dict[str, Any] = Field(default_factory=dict)
    # The raw geometry parameter set that produced this (repro + surrogate inputs).
    parameters: Dict[str, Any] = Field(default_factory=dict)
    provenance: Optional[Provenance] = None

    @property
    def n_regions(self) -> int:
        return len(self.regions)

    def regions_with_role(self, role: RegionRole) -> List[Region]:
        return [r for r in self.regions if r.role == role]
