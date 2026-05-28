from typing import Dict, Optional, Tuple

import numpy as np

from motor_ai_sim.config import get_config, get_geometry_params, clear_config_cache
from motor_ai_sim.geometry.motor_geometry import MotorGeometryParams

_current_geometry: Optional[MotorGeometryParams] = None

# ── Mesh cache ──────────────────────────────────────────────────────────────
# Stores the last built mesh data keyed by parameter hash.
# Invalidated automatically when geometry parameters change.
_mesh_cache: Optional[Tuple[str, Dict]] = None   # (param_hash, mesh_data)


def get_cached_mesh() -> Optional[Dict]:
    """Return the cached mesh if params haven't changed, else None."""
    global _mesh_cache
    if _mesh_cache is None:
        return None
    cached_hash, mesh_data = _mesh_cache
    current_hash = _geometry_hash()
    if cached_hash == current_hash:
        return mesh_data
    _mesh_cache = None
    return None


def store_mesh_cache(mesh_data: Dict) -> None:
    """Store mesh_data in the cache tagged with the current parameter hash."""
    global _mesh_cache
    _mesh_cache = (_geometry_hash(), mesh_data)


def invalidate_mesh_cache() -> None:
    """Call this after any geometry parameter update."""
    global _mesh_cache
    _mesh_cache = None


def _geometry_hash() -> str:
    import json, hashlib
    if _current_geometry is None:
        return ""
    return hashlib.sha256(
        json.dumps(_current_geometry.to_dict(), sort_keys=True).encode()
    ).hexdigest()[:16]

_DERIVED_PARAMS = frozenset([
    'stator_outer_radius', 'stator_inner_radius', 'rotor_outer_radius',
    'rotor_inner_radius', 'num_slots', 'num_poles', 'angle_slot', 'angle_pole',
    'slot_pitch', 'pole_pitch', 'slot_width', 'stator_slot_radius',
    'rotor_core_radius', 'shaft_radius',
])


def get_current_geometry(reload: bool = False) -> MotorGeometryParams:
    global _current_geometry
    if _current_geometry is None or reload:
        _current_geometry = get_geometry_params(reload=reload)
    return _current_geometry


def update_current_geometry(**kwargs) -> MotorGeometryParams:
    global _current_geometry
    current = get_current_geometry()

    update_dict = current.to_dict()
    for key, value in kwargs.items():
        if value is not None:
            update_dict[key] = value

    geometry_config = {
        k: v for k, v in update_dict.items()
        if not k.startswith('_') and k not in _DERIVED_PARAMS
    }
    _current_geometry = MotorGeometryParams(geometry_config, {})
    invalidate_mesh_cache()
    return _current_geometry


def reset_geometry() -> MotorGeometryParams:
    global _current_geometry
    clear_config_cache()
    _current_geometry = get_geometry_params(reload=True)
    invalidate_mesh_cache()
    return _current_geometry


def params_to_dict(params: MotorGeometryParams) -> Dict:
    return params.to_dict()


def generate_synthetic_pointcloud(params: MotorGeometryParams, n_points: int) -> Dict:
    """Fallback point cloud when NVIDIA Modulus is unavailable."""
    pointcloud: Dict = {}

    outer_r = params.stator_outer_radius
    inner_r = params.stator_inner_radius
    shaft_r = getattr(params, 'shaft_radius', 20)
    magnet_h = getattr(params, 'magnet_height', 5)
    magnet_outer = params.rotor_outer_radius
    magnet_inner = magnet_outer - max(magnet_h, 1e-3)
    rotor_outer = magnet_inner

    counts = {
        'stator_core': int(n_points * 0.40),
        'rotor_core':  int(n_points * 0.25),
        'shaft':       int(n_points * 0.05),
        'magnets':     int(n_points * 0.15),
        'coils':       int(n_points * 0.15),
    }

    def annulus(r_in: float, r_out: float, n: int):
        r = np.sqrt(np.random.uniform(r_in ** 2, r_out ** 2, n))
        theta = np.random.uniform(0, 2 * np.pi, n)
        return [[float(r[i] * np.cos(theta[i])), float(r[i] * np.sin(theta[i])), 0.0]
                for i in range(n)]

    material_map = {
        'stator_core': 'steel',
        'rotor_core':  'steel',
        'shaft':       'steel',
        'magnets':     'permanent_magnet',
        'coils':       'copper',
    }

    pointcloud['stator_core'] = {
        'points': annulus(inner_r, outer_r, counts['stator_core']),
        'material': material_map['stator_core'],
        'count': counts['stator_core'],
    }
    pointcloud['rotor_core'] = {
        'points': annulus(shaft_r, rotor_outer, counts['rotor_core']),
        'material': material_map['rotor_core'],
        'count': counts['rotor_core'],
    }
    pointcloud['shaft'] = {
        'points': annulus(0.0, shaft_r, counts['shaft']),
        'material': material_map['shaft'],
        'count': counts['shaft'],
    }
    pointcloud['magnets'] = {
        'points': annulus(magnet_inner, magnet_outer, counts['magnets']),
        'material': material_map['magnets'],
        'count': counts['magnets'],
    }

    slot_outer = outer_r - params.core_thickness
    pointcloud['coils'] = {
        'points': annulus(inner_r, slot_outer, counts['coils']),
        'material': material_map['coils'],
        'count': counts['coils'],
    }

    return pointcloud
