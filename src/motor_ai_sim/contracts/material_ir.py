"""MaterialIR — the resolved material set for a design.

The materials library (steel / magnet / conductor / insulator / coolant) and the
per-region assignment (region/role -> material name) are an integration point:
geometry/mesh carry only a `MaterialRef` (name + category), and a module resolves
it to real physical properties (Bertotti loss / B-H / k / rho / sigma / fluid
props). This IR is that resolved view — everything a solver or the analytical path
needs, with no direct YAML reads downstream.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from .common import Provenance


class MaterialProps(BaseModel):
    """Resolved physical properties of ONE material.

    A superset across the five categories; only the fields meaningful for the
    material's category are populated (the rest stay None). Loss coefficients are
    Bertotti on a W/m^3 basis; fluid props feed the cooling boundary condition.
    """
    model_config = ConfigDict(extra="forbid")

    name: str
    category: str                                 # steel|magnet|conductor|insulator|coolant
    description: str = ""

    # shared
    density: Optional[float] = None               # kg/m^3
    sigma: Optional[float] = None                 # S/m (electrical conductivity)
    thermal_conductivity: Optional[float] = None  # W/(m.K)
    specific_heat: Optional[float] = None         # J/(kg.K)
    mu_r: Optional[float] = None                  # relative permeability (insulator/coolant ~1; steel is BH-defined)

    # steel (electrical / SMC core)
    stacking_factor: Optional[float] = None
    core_loss_kh: Optional[float] = None          # W/(m^3.Hz.T^2)
    core_loss_kc: Optional[float] = None          # W/(m^3.Hz^2.T^2)
    core_loss_ke: Optional[float] = None          # W/(m^3.Hz^1.5.T^1.5)

    # magnet
    Br: Optional[float] = None                    # remanence [T]
    Hc: Optional[float] = None                    # coercivity [A/m]
    mu_rec: Optional[float] = None                # recoil permeability

    # coolant (working fluid)
    phase: Optional[str] = None                   # 'liquid' | 'gas'
    kinematic_viscosity: Optional[float] = None   # m^2/s
    prandtl: Optional[float] = None               # dimensionless


class MaterialIR(BaseModel):
    """Resolved materials for a design.

    `assignment` is the active region/role -> material-name map; `materials` holds
    the resolved properties of every material actually in use (normalized by name);
    `library` is the full catalogue (category -> names) for selection UIs.
    """
    model_config = ConfigDict(extra="forbid")

    contracts_version: str
    assignment: Dict[str, str] = Field(default_factory=dict)            # region/role -> material name
    materials: Dict[str, MaterialProps] = Field(default_factory=dict)  # name -> resolved props (in use)
    library: Dict[str, List[str]] = Field(default_factory=dict)        # category -> [names] (catalogue)
    provenance: Optional[Provenance] = None
