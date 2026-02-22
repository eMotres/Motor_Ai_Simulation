"""Parametric geometry for electric motor cross-section using NVIDIA Modulus CSG.

This module provides:
- MotorGeometryParams: Parameters defining motor geometry
- MotorGeometry2D: Generate 2D geometry using NVIDIA Modulus CSG primitives

The geometry uses Constructive Solid Geometry (CSG) with boolean operations
to create complex motor geometries from simple primitives (Circle, Rectangle).

Use the returned geometry objects with .sample_interior() for PINN training.

Units:
- All linear dimensions are in millimeters [mm]
- All angles are in degrees [deg]

Dependencies:
- NVIDIA Modulus (physicsnemo or modulus)
- NumPy
- OmegaConf (optional, for YAML config loading)
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import numpy as np

# Try to import omegaconf for YAML config loading
try:
    from omegaconf import OmegaConf
    HAS_OMEGACONF = True
except ImportError:
    HAS_OMEGACONF = False

# Default config path - go up from geometry/ to project root
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "motor_config.yaml"

# Try to import NVIDIA Modulus 2D primitives
try:
    from modulus.geometry.primitives_2d import Circle, Rectangle, Polygon
    from modulus.geometry import csg
    HAS_MODULUS = True
except ImportError:
    try:
        # Try physicsnemo package name (newer versions)
        from physicsnemo.geometry.primitives_2d import Circle, Rectangle, Polygon
        from physicsnemo.geometry import csg
        HAS_MODULUS = True
    except ImportError:
        HAS_MODULUS = False
        # Create placeholder classes for type hints
        class Circle:  # type: ignore
            def __init__(self, *args, **kwargs): pass
        class Rectangle:  # type: ignore
            def __init__(self, *args, **kwargs): pass
        class Polygon:  # type: ignore
            def __init__(self, *args, **kwargs): pass


class MotorGeometryParams:
    """Parameters defining the motor geometry.
    
    This class dynamically loads ALL parameters from motor_config.yaml.
    No hardcoded field names - everything comes from the YAML file.
    
    When you add a new parameter to motor_config.yaml geometry section,
    it automatically becomes available as params.parameter_name.
    
    All linear dimensions are in millimeters [mm].
    All angles are in degrees [deg].

    Example:
        >>> params = MotorGeometryParams.from_yaml("config/motor_config.yaml")
        >>> print(params.stator_diameter)  # Directly from YAML
        200.0
        >>> print(params.new_param)  # Any new param added to YAML
        1.0
    """

    def __init__(self, geometry_config: Dict, derived_config: Optional[Dict] = None):
        """Initialize with geometry parameters from config.
        
        Args:
            geometry_config: Dictionary of geometry parameters from YAML
            derived_config: Dictionary of derived parameter formulas from YAML
        """
        # Store all geometry parameters as attributes (dynamic!)
        for key, value in geometry_config.items():
            setattr(self, key, float(value) if value is not None else 0.0)
        
        # Store derived parameter formulas
        self._derived_formulas = derived_config or {}
        
        # Compute derived parameters
        self._compute_derived()

    @classmethod
    def from_yaml(cls, config_path: Optional[Union[str, Path]] = None) -> "MotorGeometryParams":
        """Load parameters from YAML configuration file.
        
        Dynamically reads ALL parameters from motor_config.yaml.
        No need to update this code when adding new parameters to YAML.

        Args:
            config_path: Path to the YAML configuration file. Uses default if None.

        Returns:
            MotorGeometryParams instance with values from config
        """
        if config_path is None:
            config_path = DEFAULT_CONFIG_PATH
        else:
            config_path = Path(config_path)
        
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        if HAS_OMEGACONF:
            # Use OmegaConf for YAML loading (supports expressions)
            config = OmegaConf.load(config_path)
            # Resolve any interpolations
            OmegaConf.resolve(config)
            # Convert to dict for dynamic access
            geometry_config = OmegaConf.to_container(config.get('geometry', {}), resolve=True)
            derived_config = OmegaConf.to_container(config.get('derived_params', {}), resolve=True)
        else:
            # Fallback to standard yaml
            import yaml
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
            geometry_config = config.get('geometry', {})
            derived_config = config.get('derived_params', {})

        return cls(geometry_config, derived_config)
    
    def _compute_derived(self) -> None:
        """Compute derived parameters from formulas in config."""
        # Standard derived parameters (computed from geometry)
        self.stator_outer_radius = self.stator_diameter / 2.0
        self.stator_inner_radius = (
            self.stator_outer_radius - self.core_thickness - self.slot_height
        )
        
        # Slot and pole counts
        self.num_slots = int(self.num_seg * self.num_slots_per_segment)
        self.num_poles = int(self.num_seg * self.num_poles_per_segment)
        
        # Angles in degrees
        self.angle_slot = 360.0 / self.num_slots
        self.angle_pole = 360.0 / self.num_poles
        
        # Angular pitches in radians
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
    
    def to_dict(self) -> Dict:
        """Convert all parameters to dictionary."""
        result = {}
        for key, value in self.__dict__.items():
            if not key.startswith('_'):
                result[key] = value
        return result
    
    def get_param_names(self) -> List[str]:
        """Get list of all parameter names (from YAML + derived)."""
        return [k for k in self.__dict__.keys() if not k.startswith('_')]

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


class MotorGeometry2D:
    """Generate 2D cross-section geometry of an electric motor using NVIDIA Modulus CSG.

    This class creates actual Modulus geometry objects using Constructive Solid Geometry
    (CSG) with boolean operations. The resulting geometries can be used for PINN training
    by calling .sample_interior() on each geometry object.

    CSG Operations:
    - Union (+): Combine geometries
    - Difference (-): Subtract one geometry from another
    - Intersection (&): Keep only overlapping regions

    Geometry Construction:
    - Stator Core: Circle (outer) - Circle (inner) - Slots
    - Slots: Rectangle positioned at correct radius, repeated around center
    - Shaft: Circle at center
    - Rotor Core: Circle (rotor_core_radius) - Shaft
    - Air Gap: Circle (stator_inner) - Circle (rotor_outer)
    - Magnets: Sector shapes on rotor, repeated for each pole

    All dimensions are in millimeters [mm].

    Example:
        >>> params = MotorGeometryParams.from_yaml("config/motor_config.yaml")
        >>> geometry = MotorGeometry2D(params)
        >>> geometries = geometry.get_modulus_geometries()
        >>> # Sample points for PINN training
        >>> stator_points = geometries['stator_core'].sample_interior(1000)
        >>> rotor_points = geometries['rotor_core'].sample_interior(500)
    """

    def __init__(self, params: MotorGeometryParams):
        """Initialize geometry generator.

        Args:
            params: Motor geometry parameters

        Raises:
            ImportError: If NVIDIA Modulus is not installed
        """
        if not HAS_MODULUS:
            raise ImportError(
                "NVIDIA Modulus is required for CSG geometry generation. "
                "Install with: pip install modulus || pip install physicsnemo"
            )
        self.params = params

    def get_modulus_geometries(self) -> Dict[str, Union[Circle, "csg.CSGObject"]]:
        """Get all motor geometry regions as Modulus CSG objects.

        Returns:
            Dictionary mapping region names to Modulus geometry objects:
            - 'stator_core': Stator iron (annulus with slots removed)
            - 'slots': Combined slot regions (negative, for subtraction)
            - 'coils': Copper windings that fill the slots (positive geometry)
            - 'air_gap': Air gap between stator and rotor
            - 'rotor_core': Rotor iron core
            - 'magnets': Permanent magnets on rotor
            - 'shaft': Motor shaft

        Example:
            >>> geometries = motor.get_modulus_geometries()
            >>> stator_points = geometries['stator_core'].sample_interior(1000)
        """
        geometries = {}

        # 1. Shaft (simple circle at center)
        geometries['shaft'] = Circle(
            center=(0.0, 0.0),
            radius=self.params.shaft_radius
        )

        # 2. Rotor Core (annulus: circle - shaft)
        rotor_outer = Circle(
            center=(0.0, 0.0),
            radius=self.params.rotor_core_radius
        )
        geometries['rotor_core'] = rotor_outer - geometries['shaft']

        # 3. Magnets (sectors on rotor surface)
        geometries['magnets'] = self._create_magnets()

        # 4. Air Gap (annulus between stator and rotor)
        air_gap_outer = Circle(
            center=(0.0, 0.0),
            radius=self.params.stator_inner_radius
        )
        air_gap_inner = Circle(
            center=(0.0, 0.0),
            radius=self.params.rotor_outer_radius
        )
        geometries['air_gap'] = air_gap_outer - air_gap_inner

        # 5. Slots (rectangular slots in stator) - negative geometry for subtraction
        geometries['slots'] = self._create_slots()

        # 6. Coils/Windings - positive geometry that fills the slots (copper)
        geometries['coils'] = self._create_coils()

        # 7. Stator Core (annulus with slots removed)
        stator_outer = Circle(
            center=(0.0, 0.0),
            radius=self.params.stator_outer_radius
        )
        stator_inner = Circle(
            center=(0.0, 0.0),
            radius=self.params.stator_inner_radius
        )
        # Stator core = outer circle - inner circle - slots
        geometries['stator_core'] = stator_outer - stator_inner - geometries['slots']

        return geometries

    def _create_slots(self) -> "csg.CSGObject":
        """Create combined slot geometry using CSG.

        Creates a single slot as a rectangle positioned at the correct radius,
        then repeats it around the center for all slots.

        Returns:
            Combined CSG object representing all slots
        """
        # Create a single slot as a rectangle

        # Create slot rectangle centered at (slot_center_x, 0)
        # Rectangle is defined by its bounds (x_min, y_min, x_max, y_max)
        single_slot_r = Rectangle(
            point_1=(self.params.tooth_width,self.params.stator_outer_radius-self.params.core_thickness),
            point_2=(self.params.tooth_width + self.params.slot_width, 0)
        )
        single_slot_l = Rectangle(
            point_1=(-self.params.tooth_width,self.params.stator_outer_radius-self.params.core_thickness),
            point_2=(-self.params.tooth_width - self.params.slot_width, 0)
        )

        # Repeat the slot around the center for all slots
        # Using rotate_repeat: repeat num_slots times with rotation
        all_slots = single_slot_r.repeat(
            n=self.params.num_slots,
            angle=self.params.angle_slot,  # degrees between slots
            center=(0.0, 0.0),
            mode="rotate"
        )
        all_slots = all_slots + single_slot_l.repeat(
            n=self.params.num_slots,
            angle=self.params.angle_slot,  # degrees between slots
            center=(0.0, 0.0),
            mode="rotate"
        )

        return all_slots

    def _create_coils(self) -> "csg.CSGObject":
        """Create combined coil/winding geometry using CSG.

        Creates copper coils that fill the slots. Each slot contains windings
        that are slightly smaller than the slot to account for insulation.

        Returns:
            Combined CSG object representing all coils/windings
        """
        # Coil dimensions are slightly smaller than slot to account for insulation
        insulation = self.params.insulation_thickness
        coil_width = self.params.slot_width - 2 * insulation
        coil_height = self.params.slot_height - insulation  # insulation at bottom only

        # Create a single coil as a rectangle (slightly smaller than slot)
        # Position similar to slots but with insulation offset
        single_coil_r = Rectangle(
            point_1=(
                self.params.tooth_width + insulation,
                self.params.stator_outer_radius - self.params.core_thickness - insulation
            ),
            point_2=(
                self.params.tooth_width + insulation + coil_width,
                self.params.stator_outer_radius - self.params.core_thickness - coil_height - insulation
            )
        )
        single_coil_l = Rectangle(
            point_1=(
                -self.params.tooth_width - insulation - coil_width,
                self.params.stator_outer_radius - self.params.core_thickness - insulation
            ),
            point_2=(
                -self.params.tooth_width - insulation,
                self.params.stator_outer_radius - self.params.core_thickness - coil_height - insulation
            )
        )

        # Repeat the coils around the center for all slots
        all_coils = single_coil_r.repeat(
            n=self.params.num_slots,
            angle=self.params.angle_slot,
            center=(0.0, 0.0),
            mode="rotate"
        )
        all_coils = all_coils + single_coil_l.repeat(
            n=self.params.num_slots,
            angle=self.params.angle_slot,
            center=(0.0, 0.0),
            mode="rotate"
        )

        return all_coils

    def _create_magnets(self) -> "csg.CSGObject":
        """Create combined magnet geometry using CSG.

        Creates a single magnet as a sector (approximated with polygon),
        then repeats it around the center for all poles.

        Returns:
            Combined CSG object representing all magnets
        """
        # Create a single magnet sector
        # Magnet spans from rotor_core_radius to rotor_outer_radius
        # Angular width is angle_pole degrees

        # Create magnet as a polygon (sector approximation)
        single_magnet = self._create_magnet_sector()

        # Repeat the magnet around the center for all poles
        all_magnets = single_magnet.repeat(
            n=self.params.num_poles,
            angle=self.params.angle_pole,  # degrees between poles
            center=(0.0, 0.0),
            mode="rotate"
        )

        return all_magnets

    def _create_magnet_sector(self) -> "csg.CSGObject":
        """Create a single magnet sector using CSG.

        Creates a sector shape (pie slice) for one magnet.
        Uses Circle - Circle - angular clipping rectangles.

        Returns:
            CSG object representing a single magnet sector
        """
        r_inner = self.params.rotor_core_radius
        r_outer = self.params.rotor_outer_radius
        half_angle_rad = np.radians(self.params.angle_pole / 2)

        # Create annulus for magnet radial extent
        outer_circle = Circle(center=(0.0, 0.0), radius=r_outer)
        inner_circle = Circle(center=(0.0, 0.0), radius=r_inner)
        magnet_annulus = outer_circle - inner_circle

        # Create angular sector by intersecting with half-planes
        # We use large rectangles to clip the annulus to the desired angle
        # Half-plane 1: y > x * tan(-half_angle) (right side of -half_angle line)
        # Half-plane 2: y < x * tan(+half_angle) (left side of +half_angle line)

        # Create clipping rectangles (large enough to cover the magnet area)
        clip_size = r_outer * 3  # Large enough to cover entire motor

        # For angle clipping, we create two half-planes using rectangles
        # positioned to clip the annulus to the sector

        # First clip: keep points with angle >= -half_angle
        # Rectangle positioned to the right of line at -half_angle
        angle_1 = -half_angle_rad
        # Normal vector pointing "inside" the sector
        normal_1 = (np.sin(angle_1), -np.cos(angle_1))

        # Second clip: keep points with angle <= +half_angle
        angle_2 = half_angle_rad
        normal_2 = (-np.sin(angle_2), np.cos(angle_2))

        # Create clipping rectangles as half-planes
        # Rectangle defined by two corner points
        # We position rectangles to act as half-plane clippers

        # Clip 1: Right half-plane for -half_angle boundary
        rect1 = Rectangle(
            point_1=(-clip_size, -clip_size),
            point_2=(clip_size * normal_1[0] + clip_size, clip_size * normal_1[1] + clip_size)
        )

        # Clip 2: Left half-plane for +half_angle boundary
        rect2 = Rectangle(
            point_1=(-clip_size, -clip_size),
            point_2=(clip_size * normal_2[0] + clip_size, clip_size * normal_2[1] + clip_size)
        )

        # Intersect annulus with both half-planes to get sector
        magnet_sector = magnet_annulus & rect1 & rect2

        return magnet_sector

    def get_individual_magnet_geometries(self) -> Dict[int, "csg.CSGObject"]:
        """Get individual magnet geometries with magnetization direction.

        Returns:
            Dictionary mapping pole index to magnet CSG object
        """
        magnets = {}
        r_inner = self.params.rotor_core_radius
        r_outer = self.params.rotor_outer_radius

        for pole_idx in range(self.params.num_poles):
            # Angular position of magnet center
            theta_center = pole_idx * self.params.pole_pitch

            # Create magnet sector at this angle
            # (similar to _create_magnet_sector but rotated to specific position)
            magnet = self._create_magnet_at_angle(theta_center)
            magnets[pole_idx] = magnet

        return magnets

    def _create_magnet_at_angle(self, theta_center: float) -> "csg.CSGObject":
        """Create a single magnet at a specific angular position.

        Args:
            theta_center: Center angle of the magnet [radians]

        Returns:
            CSG object for the magnet
        """
        r_inner = self.params.rotor_core_radius
        r_outer = self.params.rotor_outer_radius
        half_angle_rad = np.radians(self.params.angle_pole / 2)

        # Create annulus
        outer_circle = Circle(center=(0.0, 0.0), radius=r_outer)
        inner_circle = Circle(center=(0.0, 0.0), radius=r_inner)
        magnet_annulus = outer_circle - inner_circle

        # Angular bounds
        theta_start = theta_center - half_angle_rad
        theta_end = theta_center + half_angle_rad

        # Create clipping using rotated rectangles
        clip_size = r_outer * 3

        # Create sector by clipping with angular boundaries
        # This is a simplified approach - in practice you might need
        # to use Polygon for more precise sector shapes

        # Use the repeat method's underlying rotation
        base_sector = self._create_magnet_sector()

        # Rotate to desired position
        rotation_angle = np.degrees(theta_center)
        rotated_magnet = base_sector.rotate(rotation_angle, center=(0.0, 0.0))

        return rotated_magnet

    def get_individual_slot_geometries(self) -> Dict[int, "csg.CSGObject"]:
        """Get individual slot geometries.

        Returns:
            Dictionary mapping slot index to slot CSG object
        """
        slots = {}
        slot_center_x = self.params.stator_inner_radius + self.params.slot_height / 2
        slot_half_width = self.params.slot_width / 2
        slot_half_height = self.params.slot_height / 2

        for slot_idx in range(self.params.num_slots):
            # Angular position
            theta = slot_idx * self.params.slot_pitch

            # Create base slot rectangle
            base_slot = Rectangle(
                point_1=(slot_center_x - slot_half_height, -slot_half_width),
                point_2=(slot_center_x + slot_half_height, slot_half_width)
            )

            # Rotate to correct position
            rotation_angle = np.degrees(theta)
            rotated_slot = base_slot.rotate(rotation_angle, center=(0.0, 0.0))
            slots[slot_idx] = rotated_slot

        return slots

    def get_magnetization_directions(self) -> Dict[int, np.ndarray]:
        """Get magnetization direction vectors for each magnet.

        Magnetization alternates between outward (N) and inward (S) poles.

        Returns:
            Dictionary mapping pole index to magnetization unit vector
        """
        directions = {}
        for pole_idx in range(self.params.num_poles):
            theta_center = pole_idx * self.params.pole_pitch

            # Alternating magnetization: outward for even, inward for odd
            sign = 1.0 if pole_idx % 2 == 0 else -1.0

            # Radial direction at this angle
            mag_dir = sign * np.array([
                np.cos(theta_center),
                np.sin(theta_center),
            ])
            directions[pole_idx] = mag_dir

        return directions

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

    def sample_all_regions(
        self,
        n_points: int = 1000,
        bounds: Optional[Dict[str, float]] = None
    ) -> Dict[str, dict]:
        """Sample interior points from all geometry regions.

        This is a convenience method for PINN training data generation.

        Args:
            n_points: Number of points to sample per region
            bounds: Optional bounds for sampling (not used for CSG)

        Returns:
            Dictionary mapping region names to sampled point dictionaries
        """
        geometries = self.get_modulus_geometries()
        samples = {}

        for name, geo in geometries.items():
            try:
                samples[name] = geo.sample_interior(n_points)
            except Exception as e:
                # Log warning but continue
                samples[name] = {"error": str(e)}

        return samples


# Backward compatibility: Keep GeometryRegion as deprecated alias
@dataclass
class GeometryRegion:
    """DEPRECATED: Use MotorGeometry2D.get_modulus_geometries() instead.

    This class is kept for backward compatibility only.
    It will be removed in a future version.
    """
    name: str
    region_type: str  # 'annulus', 'sector', 'disk'
    r_inner: float = 0.0
    r_outer: float = 0.0
    theta_start: float = 0.0
    theta_end: float = 2 * np.pi
    magnetization_dir: np.ndarray = None
    pole_index: int = None

    def __post_init__(self):
        import warnings
        warnings.warn(
            "GeometryRegion is deprecated. Use MotorGeometry2D.get_modulus_geometries() "
            "to get actual Modulus CSG geometry objects.",
            DeprecationWarning,
            stacklevel=2
        )
