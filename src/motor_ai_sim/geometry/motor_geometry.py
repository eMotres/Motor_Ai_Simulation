"""Parametric geometry for electric motor cross-section.

This module provides:
- MotorGeometryParams: Parameters defining motor geometry
- MotorGeometry2D: Generate 2D geometry regions (points, boundaries)

The geometry defines regions and their boundaries without meshing.
Use MotorMeshGenerator from motor_mesh.py to create triangular meshes.

Units:
- All linear dimensions are in millimeters [mm]
- All angles are in degrees [deg]
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple, Union

import numpy as np

# Try to import omegaconf for YAML config loading
try:
    from omegaconf import OmegaConf
    HAS_OMEGACONF = True
except ImportError:
    HAS_OMEGACONF = False


@dataclass
class MotorGeometryParams:
    """Parameters defining the motor geometry.

    All linear dimensions are in millimeters [mm].
    All angles are in degrees [deg].

    Parameters are synchronized with config/motor_config.yaml.
    Use get_geometry_params() from motor_ai_sim.config to load from YAML.

    Attributes:
        stator_diameter: Outer diameter of stator [mm]
        slot_height: Height of stator slots [mm]
        core_thickness: Thickness of stator core (back iron) [mm]
        num_seg: Number of segments in stator
        num_slots_per_segment: Number of slots per segment
        num_poles_per_segment: Number of poles per segment
        stator_width: Axial length of stator (for 3D) [mm]
        air_gap: Air gap between stator and rotor [mm]
        tooth_width: Tooth width at the stator outer radius [mm]
        insulation_thickness: Insulation thickness between stator core and slot [mm]
        wire_width: Width of the wire in the slot [mm]
        wire_height: Height of the wire in the slot [mm]
        wire_spacing_x: Spacing between wires in the slot (x direction) [mm]
        wire_spacing_y: Spacing between wires in the slot (y direction) [mm]
        num_wires_per_slot: Number of wires per slot
        magnet_height: Height/thickness of permanent magnets [mm]
        rotor_house_height: Thickness of rotor housing [mm]
    """

    # ============================================
    # Stator parameters (synchronized with motor_config.yaml)
    # ============================================
    stator_diameter: float = 200.0  # outer diameter of stator [mm]
    slot_height: float = 16.0  # stator slot height [mm]
    core_thickness: float = 3.8  # stator core thickness [mm]
    num_seg: int = 6  # Number of segments in stator
    num_slots_per_segment: int = 6  # Number of slots per segment
    num_poles_per_segment: int = 7  # Number of poles per segment
    stator_width: float = 30.0  # stator width (axial length) [mm]
    air_gap: float = 0.65  # air gap [mm]
    
    # Slot details (synchronized with motor_config.yaml)
    tooth_width: float = 8.6  # tooth width at the stator outer radius [mm]
    insulation_thickness: float = 0.15  # insulation thickness [mm]
    wire_width: float = 4.0  # width of the wire in the slot [mm]
    wire_height: float = 0.6  # height of the wire in the slot [mm]
    wire_spacing_x: float = 0.1  # spacing between wires in x [mm]
    wire_spacing_y: float = 0.13  # spacing between wires in y [mm]
    num_wires_per_slot: int = 15  # number of wires per slot

    # ============================================
    # Rotor parameters (synchronized with motor_config.yaml)
    # ============================================
    magnet_height: float = 13.8  # magnet height [mm]
    rotor_house_height: float = 1.2  # rotor housing thickness [mm]

    # ============================================
    # Derived parameters (computed in __post_init__)
    # ============================================
    stator_outer_radius: float = field(init=False)
    stator_inner_radius: float = field(init=False)
    rotor_outer_radius: float = field(init=False)
    rotor_inner_radius: float = field(init=False)
    num_slots: int = field(init=False)
    num_poles: int = field(init=False)
    angle_slot: float = field(init=False)  # degrees
    angle_pole: float = field(init=False)  # degrees
    slot_pitch: float = field(init=False)  # radians
    pole_pitch: float = field(init=False)  # radians
    slot_width: float = field(init=False)  # computed slot width [mm]

    @classmethod
    def from_yaml(cls, config_path: Union[str, Path]) -> "MotorGeometryParams":
        """Load parameters from YAML configuration file.

        Args:
            config_path: Path to the YAML configuration file

        Returns:
            MotorGeometryParams instance with values from config

        Example:
            >>> params = MotorGeometryParams.from_yaml("config/motor_config.yaml")
            >>> print(params.stator_diameter)
            200.0
        """
        config_path = Path(config_path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        if HAS_OMEGACONF:
            # Use OmegaConf for YAML loading (supports expressions)
            config = OmegaConf.load(config_path)
            # Resolve any interpolations
            OmegaConf.resolve(config)
            geometry = config.get('geometry', config)
        else:
            # Fallback to standard yaml
            import yaml
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
            geometry = config.get('geometry', config)

        # Extract all parameters from config (synchronized with motor_config.yaml)
        return cls(
            # Stator parameters
            stator_diameter=float(geometry.get('stator_diameter', 200.0)),
            slot_height=float(geometry.get('slot_height', 16.0)),
            core_thickness=float(geometry.get('core_thickness', 3.8)),
            num_seg=int(geometry.get('num_seg', 6)),
            num_slots_per_segment=int(geometry.get('num_slots_per_segment', 6)),
            num_poles_per_segment=int(geometry.get('num_poles_per_segment', 7)),
            stator_width=float(geometry.get('stator_width', 30.0)),
            air_gap=float(geometry.get('air_gap', 0.65)),
            # Slot details
            tooth_width=float(geometry.get('tooth_width', 8.6)),
            insulation_thickness=float(geometry.get('insulation_thickness', 0.15)),
            wire_width=float(geometry.get('wire_width', 4.0)),
            wire_height=float(geometry.get('wire_height', 0.6)),
            wire_spacing_x=float(geometry.get('wire_spacing_x', 0.1)),
            wire_spacing_y=float(geometry.get('wire_spacing_y', 0.13)),
            num_wires_per_slot=int(geometry.get('num_wires_per_slot', 15)),
            # Rotor parameters
            magnet_height=float(geometry.get('magnet_height', 13.8)),
            rotor_house_height=float(geometry.get('rotor_house_height', 1.2)),
        )

    def __post_init__(self) -> None:
        """Compute derived parameters and validate."""
        # Stator radii
        self.stator_outer_radius = self.stator_diameter / 2.0
        self.stator_inner_radius = (
            self.stator_outer_radius - self.core_thickness - self.slot_height
        )

        # Slot and pole counts
        self.num_slots = self.num_seg * self.num_slots_per_segment
        self.num_poles = self.num_seg * self.num_poles_per_segment

        # Angles in degrees
        self.angle_slot = 360.0 / self.num_slots
        self.angle_pole = 360.0 / self.num_poles

        # Angular pitches in radians (for internal calculations)
        self.slot_pitch = 2 * np.pi / self.num_slots
        self.pole_pitch = 2 * np.pi / self.num_poles

        # Rotor radii
        self.rotor_outer_radius = (
            self.stator_outer_radius - self.core_thickness - self.slot_height - self.air_gap
        )
        self.rotor_inner_radius = (
            self.rotor_outer_radius - self.magnet_height - self.rotor_house_height
        )

        # Slot width (computed from wire dimensions)
        self.slot_width = (
            self.wire_width + 2 * self.wire_spacing_x + 2 * self.insulation_thickness
        )

        # Validate
        self._validate()

    def _validate(self) -> None:
        """Validate geometric parameters."""
        if self.stator_outer_radius <= self.stator_inner_radius:
            raise ValueError(
                f"stator_outer_radius ({self.stator_outer_radius}) must be > "
                f"stator_inner_radius ({self.stator_inner_radius})"
            )

        if self.rotor_outer_radius <= self.rotor_inner_radius:
            raise ValueError(
                f"rotor_outer_radius ({self.rotor_outer_radius}) must be > "
                f"rotor_inner_radius ({self.rotor_inner_radius})"
            )

        if self.num_slots < 3:
            raise ValueError(f"num_slots ({self.num_slots}) must be >= 3")

        if self.num_poles < 2:
            raise ValueError(f"num_poles ({self.num_poles}) must be >= 2")

        if self.air_gap <= 0:
            raise ValueError(f"air_gap ({self.air_gap}) must be > 0")

        if self.magnet_height > self.rotor_outer_radius - self.rotor_inner_radius:
            raise ValueError(
                f"magnet_height ({self.magnet_height}) too large for rotor dimensions"
            )

    @property
    def stator_slot_radius(self) -> float:
        """Radius at bottom of stator slots [mm]."""
        return self.stator_inner_radius + self.slot_height

    @property
    def rotor_core_radius(self) -> float:
        """Outer radius of rotor core (under magnets) [mm]."""
        return self.rotor_outer_radius - self.magnet_height

    @property
    def shaft_radius(self) -> float:
        """Shaft radius [mm]."""
        return self.rotor_inner_radius

    @staticmethod
    def deg_to_rad(degrees: float) -> float:
        """Convert degrees to radians."""
        return degrees * np.pi / 180.0

    @staticmethod
    def rad_to_deg(radians: float) -> float:
        """Convert radians to degrees."""
        return radians * 180.0 / np.pi


@dataclass
class GeometryRegion:
    """Defines a geometric region with its boundaries.

    Attributes:
        name: Region name (e.g., 'stator_core', 'slot_0')
        region_type: Type of region ('annulus', 'sector', 'disk')
        r_inner: Inner radius [mm]
        r_outer: Outer radius [mm]
        theta_start: Starting angle [rad] (for sectors)
        theta_end: Ending angle [rad] (for sectors)
        magnetization_dir: Magnetization direction for magnets
        pole_index: Pole index for magnets
    """
    name: str
    region_type: str  # 'annulus', 'sector', 'disk'
    r_inner: float = 0.0
    r_outer: float = 0.0
    theta_start: float = 0.0
    theta_end: float = 2 * np.pi
    magnetization_dir: np.ndarray = None
    pole_index: int = None


class MotorGeometry2D:
    """Generate 2D cross-section geometry of an electric motor.

    This class defines the geometry regions and their boundaries.
    It does NOT create meshes - use MotorMeshGenerator for that.

    Regions defined:
    - Stator core (annulus)
    - Stator slots (sectors)
    - Air gap (annulus)
    - Rotor core (annulus)
    - Permanent magnets (sectors)
    - Shaft (disk)

    All dimensions are in millimeters [mm].

    Example:
        >>> params = MotorGeometryParams(num_seg=6, num_slots_per_segment=6)
        >>> geometry = MotorGeometry2D(params)
        >>> regions = geometry.get_regions()
        >>> print(regions.keys())
        dict_keys(['stator_core', 'slot_0', ..., 'air_gap', 'rotor_core', ...])
    """

    def __init__(self, params: MotorGeometryParams):
        """Initialize geometry generator.

        Args:
            params: Motor geometry parameters
        """
        self.params = params

    def get_regions(self) -> Dict[str, GeometryRegion]:
        """Get all geometry regions.

        Returns:
            Dictionary mapping region names to GeometryRegion objects
        """
        regions = {}

        # 1. Stator core (annulus)
        regions["stator_core"] = GeometryRegion(
            name="stator_core",
            region_type="annulus",
            r_inner=self.params.stator_slot_radius,
            r_outer=self.params.stator_outer_radius,
        )

        # 2. Stator slots (sectors)
        for i in range(self.params.num_slots):
            regions[f"slot_{i}"] = self._get_slot_region(i)

        # 3. Air gap (annulus)
        regions["air_gap"] = GeometryRegion(
            name="air_gap",
            region_type="annulus",
            r_inner=self.params.rotor_outer_radius,
            r_outer=self.params.stator_inner_radius,
        )

        # 4. Rotor core (annulus)
        regions["rotor_core"] = GeometryRegion(
            name="rotor_core",
            region_type="annulus",
            r_inner=self.params.shaft_radius,
            r_outer=self.params.rotor_core_radius,
        )

        # 5. Permanent magnets (sectors)
        for i in range(self.params.num_poles):
            regions[f"magnet_{i}"] = self._get_magnet_region(i)

        # 6. Shaft (disk)
        regions["shaft"] = GeometryRegion(
            name="shaft",
            region_type="disk",
            r_inner=0.0,
            r_outer=self.params.shaft_radius,
        )

        return regions

    def _get_slot_region(self, slot_idx: int) -> GeometryRegion:
        """Get geometry region for a stator slot.

        Args:
            slot_idx: Slot index

        Returns:
            GeometryRegion for the slot
        """
        # Angular position of slot center (in radians)
        theta_center = slot_idx * self.params.slot_pitch

        # Slot angular extent (convert from degrees to radians)
        half_width_rad = MotorGeometryParams.deg_to_rad(self.params.angle_slot) / 2
        theta_start = theta_center - half_width_rad
        theta_end = theta_center + half_width_rad

        return GeometryRegion(
            name=f"slot_{slot_idx}",
            region_type="sector",
            r_inner=self.params.stator_inner_radius,
            r_outer=self.params.stator_slot_radius,
            theta_start=theta_start,
            theta_end=theta_end,
        )

    def _get_magnet_region(self, pole_idx: int) -> GeometryRegion:
        """Get geometry region for a permanent magnet.

        Args:
            pole_idx: Pole index

        Returns:
            GeometryRegion for the magnet
        """
        # Angular position of magnet center (in radians)
        theta_center = pole_idx * self.params.pole_pitch

        # Magnet angular extent (convert from degrees to radians)
        half_width_rad = MotorGeometryParams.deg_to_rad(self.params.angle_pole) / 2
        theta_start = theta_center - half_width_rad
        theta_end = theta_center + half_width_rad

        # Magnetization direction (alternating)
        # Radial direction: pointing outward for even poles, inward for odd
        sign = 1.0 if pole_idx % 2 == 0 else -1.0
        magnetization_dir = sign * np.array([
            np.cos(theta_center),
            np.sin(theta_center),
        ])

        return GeometryRegion(
            name=f"magnet_{pole_idx}",
            region_type="sector",
            r_inner=self.params.rotor_core_radius,
            r_outer=self.params.rotor_outer_radius,
            theta_start=theta_start,
            theta_end=theta_end,
            magnetization_dir=magnetization_dir,
            pole_index=pole_idx,
        )

    def get_region_boundaries(self) -> Dict[str, List[str]]:
        """Get boundary information for each region.

        Returns:
            Dictionary mapping region names to their boundary regions
        """
        return {
            "stator_core": ["slot_*", "air_gap"],
            "slot_*": ["stator_core", "air_gap"],
            "air_gap": ["stator_core", "stator_inner", "rotor_outer", "magnet_*"],
            "rotor_core": ["magnet_*", "shaft"],
            "magnet_*": ["rotor_core", "air_gap"],
            "shaft": ["rotor_core"],
        }

    def get_summary(self) -> Dict[str, float]:
        """Get geometry summary.

        Returns:
            Dictionary with key geometry dimensions
        """
        return {
            "stator_outer_radius": self.params.stator_outer_radius,
            "stator_inner_radius": self.params.stator_inner_radius,
            "rotor_outer_radius": self.params.rotor_outer_radius,
            "rotor_inner_radius": self.params.rotor_inner_radius,
            "air_gap": self.params.air_gap,
            "num_slots": self.params.num_slots,
            "num_poles": self.params.num_poles,
        }
