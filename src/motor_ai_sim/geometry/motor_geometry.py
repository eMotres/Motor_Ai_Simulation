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


#: The geometry names this module DERIVES from primaries, in the form they are
#: stored in a geometry dict (motor_config.yaml `geometry:` carries all nine).
#: Mirrored by ``routes._validation.DERIVED_GEOMETRY_NAMES`` and
#: ``services.geometry_service._DERIVED_PARAMS``, which additionally list
#: num_slots / num_poles and the read-only radius PROPERTIES; those are not here
#: on purpose — see ``derived_geometry``.
DERIVED_GEOMETRY_FIELDS = (
    "stator_outer_radius", "stator_inner_radius",
    "rotor_outer_radius", "rotor_inner_radius",
    "angle_slot", "angle_pole", "slot_pitch", "pole_pitch",
    "slot_width",
)


def derived_geometry(g: Dict[str, Any]) -> Dict[str, float]:
    """Every derived geometry field, computed from the PRIMARIES in ``g``.

    THE one implementation.  A derived field is a function of the primaries and
    must never be *read back* from wherever it happens to be stored, because the
    place it is stored is the SHARED global config: ``motor_config.yaml`` carries
    all nine of these, the app rewrites them whenever the user edits a geometry,
    and a per-request ``geo_override`` supplies primaries only.  Reading one back
    on an override path therefore pairs one motor's derived value with another
    motor's geometry — the config-leak family (rpm, winding connection, and this).

    It bit for real: ``fem_transient_sliding_band`` sized its mesh from
    ``geo["slot_width"]``, so the 30 mm regression machine was meshed at
    slot_width/2 = 1.25 mm while the user's config held the 40 mm design (2.5 mm)
    and at 1.15 mm after it moved to the 30 mm one (2.3 mm) — the same request,
    two different meshes, every pinned physics case red with nothing wrong in the
    code.

    Contract: compute a field only when every primary it needs is present and
    numeric, so a PARTIAL dict (a test fixture, a half-built override) yields a
    partial answer instead of raising.  Callers decide what to do with the
    result: ``_compute_derived`` assigns all of it, ``merge_geo_override`` and
    ``CadQueryMotor._map_api_to_cadquery`` refresh only the keys their dict
    already carries.

    Deliberately NOT computed here:

    * ``num_slots`` / ``num_poles`` — derived in three different tiers (config =
      segment form; override = explicit counts first; CadQuery = the override's
      counts, then the override's segment form, then the config's), and each
      caller owns its tier.  Every caller resolves them BEFORE calling this, so
      the angles and pitches below are computed on the resolved counts.
    * ``shaft_radius`` — the codebase holds two conflicting definitions
      (``MotorGeometryParams.shaft_radius`` = rotor_inner_radius; CadQuery's
      ``shaft_radius`` = rotor_inner_radius − shaft_height).  Unifying them
      moves CAD geometry and is not this fix.
    * ``stator_slot_radius`` / ``rotor_core_radius`` — read-only properties, never
      dict fields, so there is nothing to leak.
    """
    def _num(key: str) -> Optional[float]:
        v = g.get(key)
        if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        return float(v)

    out: Dict[str, float] = {}

    sd = _num("stator_diameter")
    if sd is not None:
        r_so = sd / 2.0
        out["stator_outer_radius"] = r_so
        ct, sh = _num("core_thickness"), _num("slot_height")
        if ct is not None and sh is not None:
            r_si = r_so - ct - sh
            out["stator_inner_radius"] = r_si
            ag = _num("air_gap")
            if ag is not None:
                r_ro = r_si - ag
                out["rotor_outer_radius"] = r_ro
                mh, rh = _num("magnet_height"), _num("rotor_house_height")
                if mh is not None and rh is not None:
                    out["rotor_inner_radius"] = r_ro - mh - rh

    # Tangential slot width = the WIRE PITCH the slot has to accept.
    ww, wsx, ins = (_num("wire_width"), _num("wire_spacing_x"),
                    _num("insulation_thickness"))
    if ww is not None and wsx is not None and ins is not None:
        out["slot_width"] = ww + 2.0 * wsx + 2.0 * ins

    # Angles / pitches from the RESOLVED counts (see the contract above).
    n_slots = _num("num_slots")
    if n_slots and n_slots > 0:
        out["angle_slot"] = 360.0 / n_slots
        out["slot_pitch"] = 2 * np.pi / n_slots
    n_poles = _num("num_poles")
    if n_poles and n_poles > 0:
        out["angle_pole"] = 360.0 / n_poles
        out["pole_pitch"] = 2 * np.pi / n_poles

    return out


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
        # Slot and pole counts FIRST — this class's tier is the SEGMENT form
        # (num_seg x *_per_segment), which the CAD meshes; explicit counts in the
        # config can be stale leftovers of a half-applied preset.  The angles and
        # pitches below are then computed on these resolved counts.
        self.num_slots = int(self.num_seg * self.num_slots_per_segment)
        self.num_poles = int(self.num_seg * self.num_poles_per_segment)

        # Everything else comes from the ONE derivation (module-level
        # `derived_geometry`), shared with simulation.geometry_2d.
        # merge_geo_override and CadQueryMotor._map_api_to_cadquery so a
        # per-request geometry can never be paired with the config's derived
        # values.  Radii, angles, pitches and slot_width, unchanged formulas.
        for key, value in derived_geometry(self.__dict__).items():
            setattr(self, key, value)

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
