"""Magnetic material definitions for electric motor simulation.

This module provides material properties for electromagnetic simulations:
- Magnetic permeability (mu_r)
- Electrical conductivity (sigma)
- Saturation flux density (B_sat)
- Remanence (Br) for permanent magnets
- Coercivity (Hc) for permanent magnets
"""

from dataclasses import dataclass, field
from typing import Dict, Optional

import numpy as np
import torch


@dataclass
class MagneticMaterial:
    """Magnetic properties of a material.

    Attributes:
        name: Human-readable material name
        mu_r: Relative magnetic permeability (dimensionless)
        sigma: Electrical conductivity [S/m]
        B_sat: Saturation flux density [T] (for ferromagnetic materials)
        Br: Remanence / residual flux density [T] (for permanent magnets)
        Hc: Coercivity [A/m] (for permanent magnets)
        density: Material density [kg/m³]
        color: RGB color tuple for visualization
    """

    name: str
    mu_r: float = 1.0
    sigma: float = 0.0
    B_sat: Optional[float] = None
    Br: Optional[float] = None
    Hc: Optional[float] = None
    density: float = 7800.0
    color: tuple[float, float, float] = field(default_factory=lambda: (0.5, 0.5, 0.5))

    @property
    def mu_0(self) -> float:
        """Magnetic constant (permeability of free space) [H/m]."""
        return 4 * np.pi * 1e-7

    @property
    def mu(self) -> float:
        """Absolute magnetic permeability [H/m]."""
        return self.mu_r * self.mu_0

    @property
    def is_ferromagnetic(self) -> bool:
        """Check if material is ferromagnetic (high permeability)."""
        return self.mu_r > 10

    @property
    def is_permanent_magnet(self) -> bool:
        """Check if material is a permanent magnet."""
        return self.Br is not None and self.Hc is not None

    @property
    def is_conductor(self) -> bool:
        """Check if material is electrically conductive."""
        return self.sigma > 1e4

    def B_H_curve(self, H: torch.Tensor) -> torch.Tensor:
        """Compute magnetic flux density B from field intensity H.

        For linear materials: B = μH
        For ferromagnetic materials with saturation: uses simplified model

        Args:
            H: Magnetic field intensity [A/m]

        Returns:
            Magnetic flux density [T]
        """
        if self.B_sat is not None and self.is_ferromagnetic:
            # Simplified saturation model using tanh
            B_linear = self.mu * H
            return self.B_sat * torch.tanh(B_linear / self.B_sat)
        else:
            return self.mu * H

    def get_magnetization(self) -> torch.Tensor:
        """Get magnetization vector for permanent magnets.

        Returns:
            Magnetization magnitude [A/m]
        """
        if self.is_permanent_magnet:
            # M = Br / μ0
            return self.Br / self.mu_0
        return torch.tensor(0.0)

    def __repr__(self) -> str:
        return (
            f"MagneticMaterial('{self.name}', "
            f"μr={self.mu_r:.1f}, σ={self.sigma:.2e} S/m)"
        )


class MaterialRegistry:
    """Registry of predefined magnetic materials.

    Provides common materials used in electric motor design:
    - Air/vacuum
    - Copper (windings)
    - Electrical steels (cores)
    - Permanent magnets (NdFeB, SmCo, Ferrite)
    - Shaft steel
    """

    MATERIALS: Dict[str, MagneticMaterial] = {
        # Non-magnetic materials
        "air": MagneticMaterial(
            name="Air",
            mu_r=1.0,
            sigma=0.0,
            density=1.225,
            color=(0.9, 0.9, 1.0),
        ),
        "vacuum": MagneticMaterial(
            name="Vacuum",
            mu_r=1.0,
            sigma=0.0,
            density=0.0,
            color=(1.0, 1.0, 1.0),
        ),
        # Conductors
        "copper": MagneticMaterial(
            name="Copper",
            mu_r=0.999994,  # Slightly diamagnetic
            sigma=5.8e7,
            density=8960.0,
            color=(0.72, 0.45, 0.20),  # Copper color
        ),
        "aluminum": MagneticMaterial(
            name="Aluminum",
            mu_r=1.000022,  # Slightly paramagnetic
            sigma=3.5e7,
            density=2700.0,
            color=(0.75, 0.75, 0.75),
        ),
        # Electrical steels (ferromagnetic)
        "m27_silicon_steel": MagneticMaterial(
            name="M-27 Silicon Steel",
            mu_r=2000,
            sigma=2.0e6,
            B_sat=1.8,
            density=7650.0,
            color=(0.4, 0.4, 0.5),
        ),
        "m19_silicon_steel": MagneticMaterial(
            name="M-19 Silicon Steel",
            mu_r=5000,
            sigma=1.8e6,
            B_sat=1.9,
            density=7650.0,
            color=(0.45, 0.45, 0.55),
        ),
        "m15_silicon_steel": MagneticMaterial(
            name="M-15 Silicon Steel",
            mu_r=8000,
            sigma=1.5e6,
            B_sat=2.0,
            density=7650.0,
            color=(0.5, 0.5, 0.6),
        ),
        "35ww300": MagneticMaterial(
            name="35WW300 Electrical Steel",
            mu_r=3000,
            sigma=2.2e6,
            B_sat=1.85,
            density=7600.0,
            color=(0.42, 0.42, 0.52),
        ),
        # Permanent magnets
        "ndfeb_n35": MagneticMaterial(
            name="NdFeB N35",
            mu_r=1.05,
            sigma=6.67e5,
            Br=1.17,
            Hc=868000,
            density=7500.0,
            color=(0.8, 0.2, 0.2),  # Red for magnets
        ),
        "ndfeb_n42": MagneticMaterial(
            name="NdFeB N42",
            mu_r=1.05,
            sigma=6.67e5,
            Br=1.28,
            Hc=923000,
            density=7500.0,
            color=(0.85, 0.25, 0.25),
        ),
        "ndfeb_n52": MagneticMaterial(
            name="NdFeB N52",
            mu_r=1.05,
            sigma=6.67e5,
            Br=1.45,
            Hc=876000,
            density=7500.0,
            color=(0.9, 0.3, 0.3),
        ),
        "ndfeb_n42sh": MagneticMaterial(
            name="NdFeB N42SH (High Temp)",
            mu_r=1.05,
            sigma=6.67e5,
            Br=1.30,
            Hc=1592000,  # High coercivity for high temperature
            density=7500.0,
            color=(0.85, 0.2, 0.3),
        ),
        "smco": MagneticMaterial(
            name="SmCo",
            mu_r=1.05,
            sigma=1.11e6,
            Br=0.85,
            Hc=600000,
            density=8400.0,
            color=(0.6, 0.3, 0.6),
        ),
        "ferrite_magnet": MagneticMaterial(
            name="Ferrite Magnet (Ceramic)",
            mu_r=1.05,
            sigma=1.0e-6,  # Insulator
            Br=0.4,
            Hc=280000,
            density=5000.0,
            color=(0.3, 0.3, 0.3),
        ),
        # Structural materials
        "carbon_steel": MagneticMaterial(
            name="Carbon Steel (Shaft)",
            mu_r=100,
            sigma=5.0e6,
            B_sat=1.5,
            density=7850.0,
            color=(0.3, 0.3, 0.35),
        ),
        "stainless_304": MagneticMaterial(
            name="Stainless Steel 304",
            mu_r=1.02,  # Nearly non-magnetic
            sigma=1.45e6,
            density=8000.0,
            color=(0.6, 0.6, 0.65),
        ),
        # Insulation
        "insulation": MagneticMaterial(
            name="Insulation",
            mu_r=1.0,
            sigma=1e-15,
            density=1200.0,
            color=(0.9, 0.85, 0.7),
        ),
    }

    @classmethod
    def get(cls, name: str) -> MagneticMaterial:
        """Get a material by name.

        Args:
            name: Material identifier (e.g., 'copper', 'ndfeb_n42')

        Returns:
            MagneticMaterial instance

        Raises:
            ValueError: If material name is not found
        """
        name_lower = name.lower().replace("-", "_").replace(" ", "_")
        if name_lower not in cls.MATERIALS:
            available = ", ".join(sorted(cls.MATERIALS.keys()))
            raise ValueError(
                f"Unknown material '{name}'. Available materials: {available}"
            )
        return cls.MATERIALS[name_lower]

    @classmethod
    def register(cls, name: str, material: MagneticMaterial) -> None:
        """Register a new material.

        Args:
            name: Material identifier
            material: MagneticMaterial instance
        """
        name_lower = name.lower().replace("-", "_").replace(" ", "_")
        cls.MATERIALS[name_lower] = material

    @classmethod
    def list_materials(cls) -> list[str]:
        """List all available material names.

        Returns:
            Sorted list of material identifiers
        """
        return sorted(cls.MATERIALS.keys())

    @classmethod
    def get_by_category(cls) -> Dict[str, list[str]]:
        """Get materials organized by category.

        Returns:
            Dictionary mapping category names to material lists
        """
        return {
            "non_magnetic": ["air", "vacuum"],
            "conductors": ["copper", "aluminum"],
            "electrical_steels": [
                "m27_silicon_steel",
                "m19_silicon_steel",
                "m15_silicon_steel",
                "35ww300",
            ],
            "permanent_magnets": [
                "ndfeb_n35",
                "ndfeb_n42",
                "ndfeb_n52",
                "ndfeb_n42sh",
                "smco",
                "ferrite_magnet",
            ],
            "structural": ["carbon_steel", "stainless_304"],
            "insulation": ["insulation"],
        }


def get_material_id(name: str) -> int:
    """Get numeric ID for a material name.

    Args:
        name: Material identifier

    Returns:
        Integer material ID
    """
    material_ids = {
        "air": 0,
        "vacuum": 0,
        "copper": 1,
        "aluminum": 2,
        "m27_silicon_steel": 10,
        "m19_silicon_steel": 11,
        "m15_silicon_steel": 12,
        "35ww300": 13,
        "ndfeb_n35": 20,
        "ndfeb_n42": 21,
        "ndfeb_n52": 22,
        "ndfeb_n42sh": 23,
        "smco": 24,
        "ferrite_magnet": 25,
        "carbon_steel": 30,
        "stainless_304": 31,
        "insulation": 40,
    }
    name_lower = name.lower().replace("-", "_").replace(" ", "_")
    return material_ids.get(name_lower, 0)
