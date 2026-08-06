"""Central configuration module for electric motor simulator.

This module provides a single point of access to all motor configuration
parameters from config/motor_config.yaml.

Example:
    >>> from motor_ai_sim.config import get_config, get_geometry_params
    >>> config = get_config()  # Get full config
    >>> params = get_geometry_params()  # Get geometry parameters
"""

import os
from pathlib import Path
from typing import Optional, Union

# Try to import omegaconf for YAML config loading
try:
    from omegaconf import OmegaConf, DictConfig
    HAS_OMEGACONF = True
except ImportError:
    HAS_OMEGACONF = False
    DictConfig = dict  # type: ignore

# Default config file path (relative to project root).
#
# MOTOR_AI_SIM_CONFIG redirects it.  This exists because the test suite drives
# the REAL API (`client.put("/api/geometry", ...)`) and every one of those calls
# wrote the live design: on 2026-08-06 a test run replaced the user's 150 mm
# CIANO28 with the 30 mm fixture WHILE THEY WERE WORKING, and the next preset
# save stored the fixture under their motor's name.  A test must never be able
# to reach the machine the user has loaded — tests/conftest.py points this at a
# throwaway copy before anything is imported.
_ENV_CONFIG = os.environ.get("MOTOR_AI_SIM_CONFIG", "").strip()
DEFAULT_CONFIG_PATH = (Path(_ENV_CONFIG).expanduser().resolve() if _ENV_CONFIG
                       else Path(__file__).parent.parent.parent / "config" / "motor_config.yaml")

# Global config cache
_cached_config: Optional[Union[DictConfig, dict]] = None
_cached_config_path: Optional[Path] = None
# The cache FOLLOWS the file: mtime of the copy we hold, and when we last looked.
# Without this the cache was write-once per process and every out-of-process edit
# — an optimizer script, a text editor, `git checkout` — stayed invisible until a
# restart. That is the mechanism behind two recurring complaints: "the load angle
# is never remembered" (the API served a cached 0 while the YAML said 10) and "I
# changed the material and nothing happened". Same fix the materials library got.
_cached_config_mtime: Optional[float] = None
_cached_config_checked: float = 0.0
# Probe the file at most this often — a stat() per call would be wasteful inside
# the FEM loops, and a second of staleness has never mattered here.
_MTIME_PROBE_S = 1.0


def load_config(config_path: Optional[Union[str, Path]] = None, 
                reload: bool = False) -> Union[DictConfig, dict]:
    """Load configuration from YAML file.

    Args:
        config_path: Path to config file. If None, uses default path.
        reload: Force reload even if cached.

    Returns:
        Configuration dictionary (OmegaConf DictConfig if available)
    """
    global _cached_config, _cached_config_path
    global _cached_config_mtime, _cached_config_checked

    if config_path is None:
        config_path = DEFAULT_CONFIG_PATH
    else:
        config_path = Path(config_path)

    # Serve the cache only while it still matches the file on disk.
    if not reload and _cached_config is not None and _cached_config_path == config_path:
        import time as _t
        _now = _t.time()
        if _now - _cached_config_checked < _MTIME_PROBE_S:
            return _cached_config
        _cached_config_checked = _now
        try:
            if config_path.stat().st_mtime == _cached_config_mtime:
                return _cached_config
        except OSError:
            return _cached_config      # unreadable right now → keep what we have

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    if HAS_OMEGACONF:
        config = OmegaConf.load(config_path)
        # Resolve any interpolations
        OmegaConf.resolve(config)
    else:
        import yaml
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)

    _cached_config = config
    _cached_config_path = config_path
    try:
        _cached_config_mtime = config_path.stat().st_mtime
    except OSError:
        _cached_config_mtime = None
    import time as _t
    _cached_config_checked = _t.time()

    return config


def get_config(config_path: Optional[Union[str, Path]] = None,
               reload: bool = False) -> Union[DictConfig, dict]:
    """Get the full configuration.

    This is the main entry point for accessing configuration.
    The config is cached after first load.

    Args:
        config_path: Path to config file. If None, uses default path.
        reload: Force reload even if cached.

    Returns:
        Configuration dictionary

    Example:
        >>> config = get_config()
        >>> print(config.geometry.stator_diameter)
        200.0
    """
    return load_config(config_path, reload)


def get_geometry_params(config_path: Optional[Union[str, Path]] = None,
                        reload: bool = False) -> "MotorGeometryParams":
    """Get geometry parameters as a MotorGeometryParams instance.

    This is a convenience function that creates a MotorGeometryParams
    instance from the configuration file.

    All parameters are synchronized with config/motor_config.yaml.

    Args:
        config_path: Path to config file. If None, uses default path.
        reload: Force reload even if cached.

    Returns:
        MotorGeometryParams instance

    Example:
        >>> params = get_geometry_params()
        >>> print(params.stator_diameter)
        200.0
    """
    from motor_ai_sim.geometry.motor_geometry import MotorGeometryParams

    # Clear config cache if reload is requested
    if reload:
        clear_config_cache()

    # Use dynamic loading from YAML - all parameters come from motor_config.yaml
    return MotorGeometryParams.from_yaml(config_path)


def get_mesh_params(config_path: Optional[Union[str, Path]] = None,
                    reload: bool = False) -> dict:
    """Get mesh generation parameters from config.

    Args:
        config_path: Path to config file. If None, uses default path.
        reload: Force reload even if cached.

    Returns:
        Dictionary with mesh parameters
    """
    config = get_config(config_path, reload)
    mesh = config.get('mesh', {})
    
    return {
        'n_radial': int(mesh.get('n_radial', 10)),
        'n_angular': int(mesh.get('n_angular', 64)),
        'n_angular_slots': int(mesh.get('n_angular_slots', 8)),
    }


def get_material_assignments(config_path: Optional[Union[str, Path]] = None,
                             reload: bool = False) -> dict:
    """Get material assignments from config.

    Args:
        config_path: Path to config file. If None, uses default path.
        reload: Force reload even if cached.

    Returns:
        Dictionary mapping region names to material names
    """
    config = get_config(config_path, reload)
    materials = config.get('materials', {})
    
    # Convert to regular dict if OmegaConf
    if HAS_OMEGACONF and isinstance(materials, DictConfig):
        return OmegaConf.to_container(materials, resolve=True)
    
    return dict(materials)


def get_simulation_params(config_path: Optional[Union[str, Path]] = None,
                          reload: bool = False) -> dict:
    """Get simulation parameters from config.

    Args:
        config_path: Path to config file. If None, uses default path.
        reload: Force reload even if cached.

    Returns:
        Dictionary with simulation parameters
    """
    config = get_config(config_path, reload)
    simulation = config.get('simulation', {})
    
    return {
        'max_current': float(simulation.get('max_current', 10.0)),
        'frequency': float(simulation.get('frequency', 50.0)),
        'rpm': float(simulation.get('rpm', 2000)),
    }


def clear_config_cache():
    """Clear the cached configuration.

    This is useful if the config file has been modified and you want
    to reload it.
    """
    global _cached_config, _cached_config_path
    global _cached_config_mtime, _cached_config_checked
    _cached_config = None
    _cached_config_path = None
    _cached_config_mtime = None
    _cached_config_checked = 0.0
