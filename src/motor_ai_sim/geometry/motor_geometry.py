"""Parametric geometry for electric motor cross-section.

This module provides:
- MotorGeometryParams: Parameters defining motor geometry

Units:
- All linear dimensions are in millimeters [mm]
- All angles are in degrees [deg]

Dependencies:
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

# mtime-keyed cache for from_yaml(): { resolved_path: (mtime_ns, geo_dict, derived_dict) }
# Auto-invalidates when the YAML is rewritten (geometry edit bumps mtime).
_FROM_YAML_CACHE: Dict[str, Any] = {}

HAS_MODULUS = False  # NVIDIA Modulus path removed


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
            # Only convert numeric values to float; keep strings as-is
            if isinstance(value, bool):
                setattr(self, key, value)
            elif isinstance(value, (int, float)):
                setattr(self, key, float(value) if value is not None else 0.0)
            elif isinstance(value, str):
                # Keep string values as-is (e.g., winding_type: "PMSM")
                setattr(self, key, value)
            else:
                setattr(self, key, value)
        
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

        # Parsing the YAML (OmegaConf load + resolve) costs ~0.13 s and was
        # being paid on EVERY call — twice per FEM frame.  Cache the parsed
        # geometry/derived dicts keyed by (path, mtime): a geometry edit
        # rewrites the YAML (PUT /api/geometry), bumping mtime, which
        # invalidates the cache automatically.  Deep-copy on the way out so
        # callers can't mutate the cached dicts.
        import copy as _copy
        key = str(config_path.resolve())
        try:
            mtime = config_path.stat().st_mtime_ns
        except OSError:
            mtime = None
        cached = _FROM_YAML_CACHE.get(key)
        if cached is not None and mtime is not None and cached[0] == mtime:
            geometry_config, derived_config = cached[1], cached[2]
            return cls(_copy.deepcopy(geometry_config),
                       _copy.deepcopy(derived_config))

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

        if mtime is not None:
            _FROM_YAML_CACHE[key] = (mtime, geometry_config, derived_config)

        return cls(_copy.deepcopy(geometry_config),
                   _copy.deepcopy(derived_config))
    
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


# Backward compatibility: Keep GeometryRegion as deprecated alias
@dataclass
class GeometryRegion:
    """DEPRECATED: legacy geometry-region descriptor.

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
            "GeometryRegion is deprecated and will be removed in a future version.",
            DeprecationWarning,
            stacklevel=2
        )
