"""Validated helpers for conservative, image-aware FEM warm seeding.

Conservative helpers for the opt-in FEM warm cache. The solver supplies region,
tag, and material labels at each call site.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Mapping, Optional, Sequence

import numpy as np


SAFE_CACHE_SCHEMA = "sb_safe_warm_seed_v2"


def _canonical(value: Any) -> Any:
    """Convert supported identity data to deterministic JSON values."""
    if isinstance(value, np.ndarray):
        if (np.issubdtype(value.dtype, np.number)
                and not np.all(np.isfinite(value))):
            raise ValueError("non-finite array in warm-cache identity")
        return {"shape": list(value.shape), "dtype": str(value.dtype),
                "sha256": hashlib.sha256(np.ascontiguousarray(value).view(np.uint8)).hexdigest()}
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        if not math.isfinite(float(value)):
            raise ValueError("non-finite scalar in warm-cache identity")
        return float(value).hex()
    if isinstance(value, Mapping):
        return {str(k): _canonical(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if hasattr(value, "__dict__"):
        return {"class": value.__class__.__qualname__,
                "fields": _canonical(vars(value))}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if value is None or isinstance(value, str):
        return value
    raise TypeError("unsupported warm-cache identity value: %s" % type(value).__name__)


def identity_digest(payload: Mapping[str, Any]) -> str:
    """Hash complete caller-supplied machine, material, duty and mesh identity."""
    encoded = json.dumps(_canonical(payload), sort_keys=True,
                         separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def validate_current_history(value: Any, expected_size: Optional[int] = None):
    """Validate the last two net/imposed conductor-current vectors.

    Returns independent finite float arrays, or ``None`` for incomplete or
    malformed history. Constraint ordering is guaranteed by the caller's
    conductor fingerprint; this helper checks the payload shape and values.
    """
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        return None
    out = []
    for level in value:
        if not isinstance(level, (tuple, list)) or len(level) != 2:
            return None
        net, imposed = (np.asarray(item) for item in level)
        if (net.dtype.kind not in "iuf" or imposed.dtype.kind not in "iuf"):
            return None
        net, imposed = net.astype(float), imposed.astype(float)
        if (net.ndim != 1 or imposed.shape != net.shape
                or (expected_size is not None and net.shape != (int(expected_size),))
                or not np.all(np.isfinite(net)) or not np.all(np.isfinite(imposed))):
            return None
        out.append((net.copy(), imposed.copy()))
    if out[0][0].shape != out[1][0].shape:
        return None
    return tuple(out)


def magnet_material_identity(material: Any) -> str:
    """Hash magnet constitutive card data without its pole orientation."""
    values = (dict(material) if isinstance(material, Mapping)
              else dict(vars(material)) if hasattr(material, "__dict__")
              else material)
    if isinstance(values, Mapping):
        direction_fields = {"m", "mx", "my", "mz", "jz", "direction",
                            "magnetization", "magnetisation", "angle"}
        values = {k: v for k, v in values.items()
                  if (str(k).casefold() not in direction_fields
                      and not (str(k).casefold() == "name"
                               and re.fullmatch(r"mag_\d+_[ns]", str(v), re.I)))}
    return identity_digest({"constitutive_material": values})


def mesh_entity_digest(mesh: Any, cell_map: Mapping[int, np.ndarray],
                       selected_tags: Sequence[int]) -> str:
    """Hash selected triangle geometry independent of element ordering.

    Used for conductor/magnet submeshes while intentionally excluding the
    air-gap belt, whose layer count may change between otherwise compatible
    runs. Exact geometry is retained; no rounded coordinates are used.
    """
    p = np.asarray(mesh.p, float)
    t = np.asarray(mesh.t, int)
    pieces = []
    for tag in sorted(int(v) for v in selected_tags):
        e = np.asarray(cell_map.get(tag, ()), int)
        if e.size == 0:
            continue
        xyz = p[:, t[:, e]].transpose(2, 1, 0)
        order_v = np.lexsort((xyz[:, :, 1], xyz[:, :, 0]), axis=1)
        xyz = np.take_along_axis(xyz, order_v[:, :, None], axis=1)
        flat = xyz.reshape(xyz.shape[0], 6)
        order_e = np.lexsort(tuple(flat[:, i] for i in range(5, -1, -1)))
        canonical = np.ascontiguousarray(flat[order_e], dtype=np.float64)
        pieces.append((tag, canonical.shape,
                       hashlib.sha256(canonical.view(np.uint8)).hexdigest()))
    return identity_digest({"entities": pieces})


def strict_identity_check(cache: Optional[Mapping[str, Any]], expected: Mapping[str, Any]):
    """Return ``(accepted, reason)``; legacy/incomplete provenance is rejected."""
    if not isinstance(cache, Mapping):
        return False, "no cached state"
    if cache.get("schema") != SAFE_CACHE_SCHEMA:
        return False, "cache has unknown or legacy provenance"
    got = cache.get("identity")
    if not isinstance(got, str) or not got:
        return False, "cache identity is missing"
    want = identity_digest(expected)
    if got != want:
        return False, "machine, geometry, materials, duty or discretisation identity differs"
    return True, ""


def exact_region_transfer(source_xy: np.ndarray, source_value: np.ndarray,
                          source_region: np.ndarray, target_xy: np.ndarray,
                          target_region: np.ndarray, required_target: np.ndarray,
                          *, tolerance_m: float = 1e-12):
    """Transfer only uniquely matching same-region DOFs.

    Unmatched non-required target points remain NaN for the caller to fill from
    its cold/static solution.  Any required point that is missing or ambiguous
    rejects the whole seed.  Region labels prevent coincident stator/rotor
    sliding-band points from borrowing each other's state.
    """
    try:
        from scipy.spatial import cKDTree
    except Exception as exc:  # pragma: no cover - dependency failure path
        return None, {"reason": "SciPy unavailable: %s" % exc}
    src = np.asarray(source_xy, float)
    dst = np.asarray(target_xy, float)
    val = np.asarray(source_value, float)
    sr = np.asarray(source_region)
    tr = np.asarray(target_region)
    req = np.asarray(required_target, bool)
    if src.ndim != 2 or dst.ndim != 2 or src.shape[0] != 2 or dst.shape[0] != 2:
        return None, {"reason": "DOF coordinates must have shape (2, n)"}
    if val.ndim != 1 or val.size != src.shape[1] or sr.shape != (src.shape[1],):
        return None, {"reason": "source field/region lengths differ"}
    if tr.shape != (dst.shape[1],) or req.shape != (dst.shape[1],):
        return None, {"reason": "target region/required lengths differ"}
    out = np.full(dst.shape[1], np.nan, float)
    mapped = np.zeros(dst.shape[1], bool)
    max_distance = 0.0
    for region in np.unique(tr):
        si = np.flatnonzero(sr == region)
        ti = np.flatnonzero(tr == region)
        if ti.size == 0:
            continue
        if si.size == 0:
            if np.any(req[ti]):
                return None, {"reason": "required region %r is absent from source" % region}
            continue
        tree = cKDTree(src[:, si].T)
        d, j = tree.query(dst[:, ti].T, k=2 if si.size > 1 else 1)
        if np.ndim(d) == 1:
            d1, j1 = d, j
            ambiguous = np.zeros(ti.size, bool)
        else:
            d1, j1 = d[:, 0], j[:, 0]
            ambiguous = np.abs(d[:, 1] - d[:, 0]) <= tolerance_m
        max_distance = max(max_distance, float(np.max(d1, initial=0.0)))
        good = (d1 <= tolerance_m) & ~ambiguous
        bad_required = req[ti] & ~good
        if np.any(bad_required):
            return None, {"reason": "required same-region DOF match is missing or ambiguous",
                          "region": str(region),
                          "max_distance_m": max_distance,
                          "unmatched": int(np.count_nonzero(req[ti] & (d1 > tolerance_m))),
                          "ambiguous": int(np.count_nonzero(req[ti] & ambiguous))}
        out[ti[good]] = val[si[np.asarray(j1[good], int)]]
        mapped[ti[good]] = True
    if np.any(req & ~mapped):
        return None, {"reason": "not every required conductor DOF was mapped",
                      "unmapped_required": int(np.count_nonzero(req & ~mapped))}
    return (out, mapped), {"mapped": int(np.count_nonzero(mapped)),
                           "target": int(dst.shape[1]),
                           "max_distance_m": max_distance}


def rewind_rotor_dofs(values: np.ndarray, rotor_dofs: np.ndarray,
                      rotor_xy: np.ndarray, h_ref_m: float, *,
                      n_sectors: int, bc_sign: int, electrical_period_rad: float,
                      period_shift_map, window_periods: int = 1):
    """Rewind cached A/A2 by one electrical period on rotor material DOFs.

    The stator is already periodic and is left untouched. ``period_shift_map``
    is injected from ``simulation.rotor_window`` so this scratch helper follows
    the solver's established symmetry/sign convention.
    """
    out = np.asarray(values, float).copy()
    ids = np.asarray(rotor_dofs, int)
    pts = np.asarray(rotor_xy, float)
    if ids.ndim != 1 or pts.shape != (2, ids.size) or np.any(ids < 0) or np.any(ids >= out.size):
        return None, {"reason": "rotor DOF coordinates do not align with the field"}
    if int(window_periods) != window_periods or int(window_periods) < 1:
        return None, {"reason": "warm seed window must contain whole electrical periods"}
    image, info = period_shift_map(pts, float(h_ref_m), n_sectors=int(n_sectors),
                                   bc_sign=int(bc_sign),
                                   rot_rad=-float(electrical_period_rad))
    if image is None:
        return None, {"reason": "rotor pole-pair image map failed", "map": info}
    j, sign = image
    for _ in range(int(window_periods)):
        out[ids] = np.asarray(sign, float) * out[ids[np.asarray(j, int)]]
    return out, {"mapped_rotor_dofs": int(ids.size),
                 "rewound_periods": int(window_periods), "map": info}


def exact_tagged_br_transfer(source_xy: np.ndarray, source_tag: np.ndarray,
                             source_value: np.ndarray, target_xy: np.ndarray,
                             target_tag: np.ndarray, target_material: np.ndarray,
                             *, tolerance_m: float = 1e-12):
    """Match Br to current solver ordering by exact centroid and body tag.

    This separates physical magnet identity from element storage order, which
    often changes when only the air-gap belt is remeshed.
    """
    src = np.asarray(source_xy, float)
    dst = np.asarray(target_xy, float)
    st = np.asarray(source_tag, int)
    dt = np.asarray(target_tag, int)
    val = np.asarray(source_value, float)
    mat = np.asarray(target_material).astype(str)
    if (src.ndim != 2 or dst.ndim != 2 or src.shape[1] != 2
            or dst.shape[1] != 2 or st.shape != (len(src),)
            or dt.shape != (len(dst),) or val.shape != st.shape
            or mat.shape != dt.shape):
        return None, {"reason": "tagged Br transfer arrays have incompatible shapes"}
    out = np.full(len(dst), np.nan, float)
    for tag in np.unique(dt):
        si = np.flatnonzero(st == tag)
        ti = np.flatnonzero(dt == tag)
        if len(si) != len(ti):
            return None, {"reason": "magnet element count differs for tag %d" % tag}
        # Exact centroid matches must be one-to-one. Reject duplicates rather
        # than making a storage-order-dependent choice.
        source_keys = {}
        for i in si:
            key = tuple(np.asarray(src[i], dtype=np.float64).view(np.uint64))
            source_keys.setdefault(key, []).append(int(i))
        for i in ti:
            key = tuple(np.asarray(dst[i], dtype=np.float64).view(np.uint64))
            hits = source_keys.get(key, ())
            if len(hits) != 1:
                return None, {"reason": "magnet centroid is missing or ambiguous for tag %d" % tag}
            out[i] = val[hits[0]]
        if np.any(~np.isfinite(out[ti])):
            return None, {"reason": "Br transfer produced a non-finite value"}
    return out, {"mapped_magnets": len(out)}


def restore_br_bundle(original: np.ndarray, current: np.ndarray, *, dm_seeded: bool,
                      dm_seed_from: Any, mx_all: np.ndarray, my_all: np.ndarray,
                      nst: int, mx: np.ndarray, my: np.ndarray,
                      magnet_scale: float):
    """Rollback a staged Br seed in-place, preserving aliases held by demag."""
    current[:] = np.asarray(original, float)
    mx_all[nst:] = np.asarray(mx, float) * current
    my_all[nst:] = np.asarray(my, float) * current
    return False, None
