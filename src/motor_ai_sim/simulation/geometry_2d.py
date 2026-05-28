"""2-D motor domains for Modulus PINN training.

Builds Modulus CSG (Constructive Solid Geometry) objects for each material
sub-domain of the motor cross-section.  Each domain is a closed 2-D region
that will be used to create a PointwiseInteriorConstraint.

Domain map
----------
  ┌─ stator_core   — outer steel ring (back-iron + teeth)
  ├─ slot_A / slot_B — winding sub-domains (coil-side A, −B, ...)
  ├─ air_gap       — thin air ring between stator and rotor
  ├─ magnet_N / magnet_S — alternating pole magnets
  ├─ rotor_core    — rotor back-iron ring
  └─ shaft          — innermost cylinder

The geometry is fully parametric — all radii and arc angles come from
MotorGeometryParams (read from motor_config.yaml).

All coordinates are in metres (Modulus default SI).  Motor config uses mm,
so we divide by 1000 throughout.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

# ── Modulus CSG primitives ────────────────────────────────────────────────────
try:
    from modulus.sym.geometry.primitives_2d import Circle, Rectangle
    from modulus.sym.geometry.csg import CSGUnion, CSGDifference, CSGIntersection
    HAS_MODULUS = True
except ImportError:
    try:
        from physicsnemo.geometry.primitives_2d import Circle, Rectangle
        from physicsnemo.geometry.csg import CSGUnion, CSGDifference, CSGIntersection
        HAS_MODULUS = True
    except ImportError:
        HAS_MODULUS = False

        # ── minimal stubs so the module imports without Modulus ───────────────
        class _GeoStub:
            def __init__(self, *a, **kw):
                self._name = kw.get("name", "stub")
            def __sub__(self, other): return self
            def __add__(self, other): return self
            def __and__(self, other): return self
            def sample_interior(self, n, **kw):
                return {"x": np.zeros((n, 1)), "y": np.zeros((n, 1))}
            def sample_boundary(self, n, **kw):
                return {"x": np.zeros((n, 1)), "y": np.zeros((n, 1))}

        class Circle(_GeoStub):       # type: ignore
            def __init__(self, center=(0, 0), radius=1.0, **kw): super().__init__(**kw)
        class Rectangle(_GeoStub):    # type: ignore
            def __init__(self, point1=(0, 0), point2=(1, 1), **kw): super().__init__(**kw)
        CSGUnion = CSGDifference = CSGIntersection = _GeoStub


# ─────────────────────────────────────────────────────────────────────────────
# 1.  Parameter loading
# ─────────────────────────────────────────────────────────────────────────────
_CFG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "motor_config.yaml"


@dataclass
class MotorDomainParams:
    """Precomputed radii and angles for all motor sub-domains [in metres]."""
    # Radii
    r_stator_out: float = 0.075       # outer stator radius
    r_stator_in: float  = 0.057       # stator inner radius (slot bottom)
    r_rotor_out: float  = 0.0565      # rotor outer radius  (= r_stator_in − air_gap)
    r_rotor_in: float   = 0.042       # rotor inner radius  (below magnets)
    r_shaft_in: float   = 0.039       # shaft inner radius
    r_air_out: float    = 0.057       # air-gap outer = r_stator_in
    r_air_in: float     = 0.0565      # air-gap inner = r_rotor_out

    # Number of poles / slots
    num_poles: int = 28               # = num_seg × num_poles_per_segment
    num_slots: int = 24               # = num_seg × num_slots_per_segment

    # Angular extent of one magnet pole [radians]
    magnet_fill_fraction: float = 0.9  # magnet arc / pole pitch

    # Stack length [m] (used only for torque calculation)
    stack_length: float = 0.030


def params_from_config(cfg_path: Path = _CFG_PATH) -> MotorDomainParams:
    """Load MotorDomainParams from motor_config.yaml."""
    import yaml

    with cfg_path.open() as f:
        cfg = yaml.safe_load(f)

    g = cfg["geometry"]
    mm = 1e-3          # mm → m

    r_so = g["stator_diameter"] / 2 * mm
    r_si = r_so - g["core_thickness"] * mm - g["slot_height"] * mm
    r_ro = r_si - g["air_gap"] * mm
    r_ri = r_ro - g["magnet_height"] * mm - g["rotor_house_height"] * mm
    r_sh = r_ri - g["shaft_height"] * mm

    num_slots = g["num_seg"] * g["num_slots_per_segment"]
    num_poles = g["num_seg"] * g["num_poles_per_segment"]

    return MotorDomainParams(
        r_stator_out=r_so,
        r_stator_in=r_si,
        r_rotor_out=r_ro,
        r_rotor_in=r_ri,
        r_shaft_in=r_sh,
        r_air_out=r_si,
        r_air_in=r_ro,
        num_poles=num_poles,
        num_slots=num_slots,
        magnet_fill_fraction=g.get("magnet_fill_down", 0.9),
        stack_length=g.get("motor_length", 30) * mm,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2.  CSG domain builder
# ─────────────────────────────────────────────────────────────────────────────

class MotorDomains2D:
    """CSG geometry domains for a 2-D motor cross-section.

    After construction, access each domain via self.domains dict.
    The keys match the material names used in motor_config.yaml.

    Usage
    -----
    >>> domains = MotorDomains2D.from_config()
    >>> stator_geo = domains["stator_core"]
    >>> pts = stator_geo.sample_interior(n=10_000)
    """

    def __init__(self, p: MotorDomainParams):
        self.p = p
        self.domains: Dict[str, object] = {}
        self._build()

    # ── public constructor ────────────────────────────────────────────────────
    @classmethod
    def from_config(cls, cfg_path: Path = _CFG_PATH) -> "MotorDomains2D":
        return cls(params_from_config(cfg_path))

    # ── domain construction ───────────────────────────────────────────────────
    def _build(self) -> None:
        p = self.p

        # Helper: annular ring between two radii
        def annulus(r_out: float, r_in: float) -> object:
            outer = Circle((0, 0), r_out)
            inner = Circle((0, 0), r_in)
            return outer - inner             # CSG difference

        # ── 1. Stator back-iron + teeth (simplified as full annulus)
        #       Real slot geometry is complex; we use the full ring here.
        #       TODO: subtract slot openings using Polygon primitives.
        self.domains["stator_core"] = annulus(p.r_stator_out, p.r_stator_in)

        # ── 2. Air gap
        self.domains["air_gap"] = annulus(p.r_air_out, p.r_air_in)

        # ── 3. Rotor core ring (below magnets)
        self.domains["rotor_core"] = annulus(p.r_rotor_in, p.r_shaft_in)

        # ── 4. Shaft
        self.domains["shaft"] = Circle((0, 0), p.r_shaft_in)

        # ── 5. Magnets (alternating N/S poles as annular sectors)
        #       We create one unified "magnet" domain for the full ring at
        #       this stage; pole polarity is encoded via M_x/M_y in the PDE.
        self.domains["magnet"] = annulus(p.r_rotor_out, p.r_rotor_in)

        # ── 6. Winding domain (simplified: thin ring at stator inner)
        #       A more accurate geometry would use individual slot polygons.
        slot_r_out = p.r_stator_in + (p.r_stator_out - p.r_stator_in) * 0.85
        slot_r_in  = p.r_stator_in + (p.r_stator_out - p.r_stator_in) * 0.15
        self.domains["slot"] = annulus(slot_r_out, slot_r_in)

        # ── 7. Full computational domain (union of all regions)
        self.domains["full"] = Circle((0, 0), p.r_stator_out)

    # ── convenience ───────────────────────────────────────────────────────────
    def __getitem__(self, key: str) -> object:
        return self.domains[key]

    def keys(self) -> List[str]:
        return list(self.domains.keys())

    def summary(self) -> str:
        """Return a human-readable summary of all domains."""
        p = self.p
        lines = [
            "Motor 2D Domains",
            f"  Stator outer radius : {p.r_stator_out*1e3:.2f} mm",
            f"  Stator inner radius : {p.r_stator_in*1e3:.2f} mm",
            f"  Air-gap thickness   : {(p.r_air_out - p.r_air_in)*1e3:.2f} mm",
            f"  Rotor outer radius  : {p.r_rotor_out*1e3:.2f} mm",
            f"  Rotor inner radius  : {p.r_rotor_in*1e3:.2f} mm",
            f"  Shaft radius        : {p.r_shaft_in*1e3:.2f} mm",
            f"  Num poles           : {p.num_poles}",
            f"  Num slots           : {p.num_slots}",
            f"  Stack length        : {p.stack_length*1e3:.1f} mm",
            "",
            "  Domains: " + ", ".join(self.domains.keys()),
        ]
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Current-density helper
# ─────────────────────────────────────────────────────────────────────────────

def winding_current_density(
    I_peak: float,
    n_turns: int,
    slot_area_m2: float,
    fill_factor: float = 0.6,
    phase_angle_rad: float = 0.0,
) -> Tuple[float, float]:
    """Return (J_pos, J_neg) current densities for a coil pair [A/m²].

    J_pos  used for conductors carrying current +I·sin(φ)
    J_neg  = −J_pos  for the return path
    """
    J_peak = I_peak * n_turns / (slot_area_m2 * fill_factor)
    J = J_peak * math.sin(phase_angle_rad)
    return J, -J


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Quick sanity print
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    domains = MotorDomains2D.from_config()
    print(domains.summary())
