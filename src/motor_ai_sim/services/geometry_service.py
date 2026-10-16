from typing import Dict, Optional, Tuple

import numpy as np

from motor_ai_sim import workspace as _WS
from motor_ai_sim.config import get_config, get_geometry_params, clear_config_cache
from motor_ai_sim.geometry.motor_geometry import MotorGeometryParams

# ── The geometry singleton, per WORKSPACE (migration Stage 3) ───────────────
# `_current_geometry` was THE single-user object: one machine's cross-section,
# held for the whole process, answering every caller.  With two accounts on one
# server it is the shortest possible path from "A edits a slot" to "B's next
# solve is about A's motor".
#
# It is not a cache and has no key, so it lives in the workspace's flag slots
# (cap: one geometry per workspace) rather than in a keyed store.  The NAME
# survives as a module attribute — `tests/test_geometry_validation.py` and
# `tests/test_route_input_validation.py` reset it by assignment — and an
# assignment from outside wins over the workspace slot, which is exactly the
# process-global behaviour those tests expect.  It wins IN THE PROCESS
# WORKSPACE ONLY (`workspace.module_override_applies`): a module attribute
# cannot be un-created, so an unscoped override would let the first test that
# assigns this name pin every account on the server to one geometry for the
# rest of the process — the very singleton this stage removes.
_SLOT_GEOM = "geometry_service.current_geometry"
_SLOT_MESH = "geometry_service.mesh_cache"

#: One mesh per workspace, keyed by the geometry hash it was built for — the
#: single slot this replaces, bounded and now workspace-stamped.
_MESH_CACHE_MAX = 1

_UNSET = object()


def _geom_get() -> Optional[MotorGeometryParams]:
    ov = globals().get("_current_geometry", _UNSET)
    if ov is not _UNSET and _WS.module_override_applies():
        return ov
    return _WS.state().flag(_SLOT_GEOM)


def _geom_set(value: Optional[MotorGeometryParams]) -> None:
    if "_current_geometry" in globals() and _WS.module_override_applies():
        globals()["_current_geometry"] = value
    else:
        _WS.state().set_flag(_SLOT_GEOM, value)


#: Per-workspace mesh store.  Not a module constant: a plain name here would be
#: the very global this stage removes.
_mesh_store = _WS.ws_map(_SLOT_MESH, _MESH_CACHE_MAX)


def get_cached_mesh() -> Optional[Dict]:
    """Return the cached mesh if params haven't changed, else None."""
    h = _geometry_hash()
    hit = _mesh_store.get(h)
    if hit is None:
        # A changed hash is a changed machine: drop what is there rather than
        # keep paying memory for a cross-section nobody will ask for again.
        _mesh_store.clear()
    return hit


def store_mesh_cache(mesh_data: Dict) -> None:
    """Store mesh_data in the cache tagged with the current parameter hash."""
    _mesh_store.clear()
    _mesh_store[_geometry_hash()] = mesh_data


def invalidate_mesh_cache() -> None:
    """Call this after any geometry parameter update."""
    _mesh_store.clear()


def _geometry_hash() -> str:
    import json, hashlib
    geom = _geom_get()
    if geom is None:
        return ""
    return hashlib.sha256(
        json.dumps(geom.to_dict(), sort_keys=True).encode()
    ).hexdigest()[:16]

_DERIVED_PARAMS = frozenset([
    'stator_outer_radius', 'stator_inner_radius', 'rotor_outer_radius',
    'rotor_inner_radius', 'num_slots', 'num_poles', 'angle_slot', 'angle_pole',
    'slot_pitch', 'pole_pitch', 'slot_width', 'stator_slot_radius',
    'rotor_core_radius', 'shaft_radius',
])


def get_current_geometry(reload: bool = False) -> MotorGeometryParams:
    current = _geom_get()
    if current is None or reload:
        current = get_geometry_params(reload=reload)
        _geom_set(current)
    return current


def update_current_geometry(**kwargs) -> MotorGeometryParams:
    current = get_current_geometry()

    update_dict = current.to_dict()
    for key, value in kwargs.items():
        if value is not None:
            update_dict[key] = value

    geometry_config = {
        k: v for k, v in update_dict.items()
        if not k.startswith('_') and k not in _DERIVED_PARAMS
    }
    new = MotorGeometryParams(geometry_config, {})
    _geom_set(new)
    invalidate_mesh_cache()
    return new


def reset_geometry() -> MotorGeometryParams:
    clear_config_cache()
    new = get_geometry_params(reload=True)
    _geom_set(new)
    invalidate_mesh_cache()
    return new


def __getattr__(name):
    """``_current_geometry`` / ``_mesh_cache`` survive as NAMES.

    Both are per-workspace now; read from outside they answer for the caller's
    workspace, in the shapes they always had — ``None`` or the geometry, and
    ``None`` or ``(param_hash, mesh_data)``.  Assigning to either from outside
    creates a real module attribute, which shadows this for the rest of the
    process: that is the process-global behaviour the two tests that reset
    ``_current_geometry`` by assignment are relying on.
    """
    if name == "_current_geometry":
        return _WS.state().flag(_SLOT_GEOM)
    if name == "_mesh_cache":
        for k, v in _mesh_store.items():
            return (k, v)
        return None
    raise AttributeError(name)


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
