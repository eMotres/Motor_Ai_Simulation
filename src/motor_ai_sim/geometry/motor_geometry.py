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

# Default config path — the SAME one motor_ai_sim.config resolves (it honours
# MOTOR_AI_SIM_CONFIG).  A second, independently derived copy of this path meant
# a redirected config was read here from the real file: the API wrote one file
# and reloaded another.
from motor_ai_sim.config import DEFAULT_CONFIG_PATH  # noqa: E402  (path, not logic)

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


#: Primary-by-type geometry keys the builder NEVER reads.  ``slot_hs`` is in
#: the schema and in every stored file, but ``cadquery_geometry`` does not use
#: it (owner 2026-09-14, again 2026-09-30: "not used at all"; the Fusion export
#: already leaves it out, ``routes.fusion.FUSION_EXCLUDED_NAMES``).  A
#: difference in it is therefore never a different machine: the duty-load
#: guard, the lock check and the report's same-machine test skip it.  It is
#: deliberately still hashed by ``routes.simulation._geometry_fingerprint``
#: (the passport / bench keys) — changing that print would orphan them.
UNUSED_GEOMETRY_KEYS = frozenset({"slot_hs"})


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

    # Tangential slot width = the WIRE PITCH the slot has to accept.  With
    # wire_split = N a turn is N strips of wire_width laid side by side with
    # 2·wire_spacing_x between them, so the column the slot has to accept is
    # N·wire_width + (N−1)·2·wire_spacing_x — the same `wire_col_w`
    # cadquery_geometry cuts the pocket with.
    ww, wsx, ins = (_num("wire_width"), _num("wire_spacing_x"),
                    _num("insulation_thickness"))
    if ww is not None and wsx is not None and ins is not None:
        from motor_ai_sim.winding import STRIP_GAP_FACTOR as _GF
        _nsp = _num("wire_split")
        _nsp = max(1, int(round(_nsp))) if _nsp else 1
        _col = ww if _nsp <= 1 else _nsp * ww + (_nsp - 1) * _GF * wsx
        out["slot_width"] = _col + 2.0 * wsx + 2.0 * ins

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


#: Relative tolerance under which a STORED derived value counts as equal to the
#: freshly derived one.  Stored values were written by the same formulas, so a
#: consistent file matches to the last bit; the tolerance only absorbs the
#: ``20`` vs ``20.0`` spelling and last-ulp float noise, and it is what keeps a
#: consistent document byte-identical after a refresh (see below).
_DERIVED_REL_TOL = 1e-9


def _as_num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _derived_equal(a: float, b: float) -> bool:
    return abs(a - b) <= _DERIVED_REL_TOL * max(1.0, abs(a), abs(b))


def fresh_derived_values(g: Dict[str, Any]) -> Dict[str, Any]:
    """Every derived field ``g``'s primaries define, INCLUDING the slot/pole
    counts in this class's tier (segment form: ``num_seg`` x ``*_per_segment``),
    and the angles/pitches computed on those counts.

    ``derived_geometry`` itself deliberately leaves the counts to its caller;
    a STORED geometry document (die.yaml, a configuration, motor_config.yaml)
    is exactly the caller whose explicit counts can be stale, so they are
    resolved here from the segment form, as ``MotorGeometryParams`` does."""
    calc = dict(g)
    counts: Dict[str, Any] = {}
    ns = _as_num(g.get("num_seg"))
    sps = _as_num(g.get("num_slots_per_segment"))
    pps = _as_num(g.get("num_poles_per_segment"))
    if ns and ns > 0 and sps and sps > 0:
        counts["num_slots"] = int(round(ns * sps))
    if ns and ns > 0 and pps and pps > 0:
        counts["num_poles"] = int(round(ns * pps))
    calc.update(counts)
    out: Dict[str, Any] = dict(counts)
    out.update(derived_geometry(calc))
    return out


def stale_derived_fields(g: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """``{key: {"stored": v, "fresh": v}}`` for every derived field ``g``
    CARRIES whose stored value is not what its own primaries give.  Empty for
    a consistent document.  Keys ``g`` does not carry are not reported."""
    out: Dict[str, Dict[str, Any]] = {}
    for k, fv in fresh_derived_values(g).items():
        if k not in g:
            continue
        sv = _as_num(g.get(k))
        if sv is None or not _derived_equal(sv, float(fv)):
            out[k] = {"stored": g.get(k), "fresh": fv}
    return out


def refresh_derived_geometry(g: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """A COPY of a stored geometry dict with every derived field it carries
    recomputed from its own primaries.

    THE rule for stored geometry (die.yaml, configuration yamls, the
    motor_config.yaml ``geometry:`` block): a derived field is a function of
    the primaries and the stored copy is never trusted — it went stale on most
    dies (2026-09-30: CIANO14 40 new stored bore radius 12.1 for inputs that
    give 12.0, CILN28 carried a 30 mm machine's 9.2/9/3.7 on a Ø160 die), and
    the duty-load guard then reported a "different geometry" for a duty whose
    inputs were identical.

    Two properties the callers rely on:

    * a key the dict does NOT carry is NOT added — a refresh never grows a
      document, so a die that never stored ``slot_pitch`` keeps not storing it;
    * a stored value that already equals the fresh one (within
      ``_DERIVED_REL_TOL``) is kept VERBATIM — ``20`` stays ``20``, not
      ``20.0`` — so refreshing a consistent document is byte-identical and
      every hash taken over it (``family._build_sig``,
      ``routes.simulation._geometry_fingerprint``) is unchanged.  Only a
      genuinely stale value moves.
    """
    out = dict(g or {})
    for k, v in stale_derived_fields(out).items():
        out[k] = v["fresh"]
    return out


def is_compared_geometry_input(key: str) -> bool:
    """True for a key that says which MACHINE this is: not derived (a function
    of the primaries, compared through them) and not unused (``slot_hs``).
    The one rule every "same machine?" comparison of two stored geometries
    uses, so a stale derived copy or an unread knob can never read as a
    different motor."""
    if key in UNUSED_GEOMETRY_KEYS:
        return False
    try:
        from motor_ai_sim.routes._validation import DERIVED_GEOMETRY_NAMES
        derived = DERIVED_GEOMETRY_NAMES
    except Exception:                                  # noqa: BLE001
        derived = frozenset(DERIVED_GEOMETRY_FIELDS) | {
            "num_slots", "num_poles", "stator_slot_radius",
            "rotor_core_radius", "shaft_radius"}
    return key not in derived


#: Environment switch for the WRITE side of the fix (see
#: ``fresh_derived_on_write``).  Off by default — deliberately.
FRESH_DERIVED_ENV = "MOTOR_AI_SIM_FRESH_DERIVED"


def fresh_derived_on_write() -> bool:
    """Whether stored geometry is refreshed where it is WRITTEN or SERVED for a
    write: the geometry PUT's ``motor_config.yaml`` block, the family
    ``/payload`` and every die/configuration save.

    Off by default because turning it on changes
    ``routes.simulation._geometry_fingerprint`` for every machine whose die
    still stores stale derived fields: that print hashes the raw
    ``motor_config.yaml`` geometry block, the ▶ load copies the die's stored
    derived values into it, so the Stage A passports (end_effect_passports.json)
    and the bench Ld/Lq records of those machines are KEYED on the stale
    numbers (measured 2026-09-30: the CIANO10 200 opt L155/L180 and the
    CIANO28 85 20SW1200 L13 passports).  The order is: run
    ``scripts/migrate_derived_geometry.py`` (dry-run, review, then --apply with
    the passport/bench re-key), THEN set ``MOTOR_AI_SIM_FRESH_DERIVED=1``.
    Every read-side consumer (comparisons, reports, exports) uses fresh values
    regardless of this switch — those never feed a stored key."""
    import os
    return os.environ.get(FRESH_DERIVED_ENV, "").strip().lower() in (
        "1", "true", "yes", "on")


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
            # Per CALL through the workspace resolver (migration Stage 1), not
            # the import-time constant: with no workspace set this IS
            # ``DEFAULT_CONFIG_PATH``, so the single-user path is unchanged.
            from motor_ai_sim.config import config_path as _resolve_cfg_path
            config_path = _resolve_cfg_path()
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
            # Convert to dict for dynamic access.  OmegaConf.to_container
            # REJECTS a plain dict, so the `.get(..., {})` fallback (a real
            # Python {}) cannot be handed to it — a config file with no
            # `derived_params` block used to crash here (regression caught by
            # test_load_from_yaml).  Pull the sub-config with OmegaConf's own
            # accessor and default to an empty DictConfig.
            _geo_cfg = config.get('geometry')
            _der_cfg = config.get('derived_params')
            geometry_config = (OmegaConf.to_container(_geo_cfg, resolve=True)
                               if _geo_cfg is not None else {})
            derived_config = (OmegaConf.to_container(_der_cfg, resolve=True)
                              if _der_cfg is not None else {})
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
