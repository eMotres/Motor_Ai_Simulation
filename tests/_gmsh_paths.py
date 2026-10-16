# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""One small representative case per gmsh path type, shared by the isolation
test (tests/test_gmsh_isolation.py, wrappers only) and the in-process vs
worker parity measurement (scripts/gmsh_worker_parity.py).

Every case is ``(name, public_wrapper, worker_impl, args, kwargs)``: the
wrapper is what the API process calls (it runs the impl in the gmsh worker);
the impl is the same body, callable in-process only by the parity script.

This module itself never imports gmsh.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

import numpy as np

_ROOT = Path(__file__).resolve().parents[1]

Case = Tuple[str, Callable, Callable, tuple, dict]

#: the 30 mm 12s/14p fixture of the physics regression (no sleeve)
from tests.test_physics_regression import GEO_30MM  # noqa: E402


def _polys_30mm():
    from motor_ai_sim.cadquery_geometry import CadQueryMotor
    m = CadQueryMotor()
    m.set_parameters(dict(GEO_30MM))
    return m.get_2d_polygons(rotor_angle_deg=0.0)


def _square_pslg():
    """10 x 10 box, 4 x 4 inner box (a material interface), a 1 x 1 hole."""
    def ring(x0, y0, x1, y1, n):
        e = []
        for (ax, ay), (bx, by) in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
                                   ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
            for k in range(n):
                e.append((ax + (bx - ax) * k / n, ay + (by - ay) * k / n))
        return e
    pts, segs = [], []
    for r in (ring(0, 0, 10, 10, 5), ring(3, 3, 7, 7, 4), ring(8, 8, 9, 9, 1)):
        i0 = len(pts)
        pts += r
        segs += [(i0 + k, i0 + (k + 1) % len(r)) for k in range(len(r))]
    return np.array(pts, float), np.array(segs, np.int64)


def _section_40mm():
    from motor_ai_sim.simulation.static3d.motor_geometry import load_motor_section
    with (_ROOT / "config" / "motor_presets.json").open(encoding="utf-8") as fh:
        geo = dict(json.load(fh)["my_40mm_last"]["geometry"])
    return load_motor_section(geo_override=geo)


def cases() -> List[Case]:
    from shapely.geometry import Polygon

    from motor_ai_sim.simulation import mesher as M
    from motor_ai_sim.simulation import geo_mesh as GM
    from motor_ai_sim.simulation import geo_mesh_gmsh as GG
    from motor_ai_sim.simulation.mechanical import modal as MO
    from motor_ai_sim.simulation.mechanical import rotor_stress as RS
    from motor_ai_sim.simulation.static3d import meshes as ME
    from motor_ai_sim.simulation.static3d import motor_mesh as MM

    polys = _polys_30mm()
    V, S = _square_pslg()
    square = Polygon([(0, 0), (5, 0), (5, 5), (0, 5)])
    section = _section_40mm()
    return [
        ("occ_2d_single_polygon", M._mesh_single_polygon,
         M._mesh_single_polygon_impl, (square, 1.0, 0.3), {}),
        ("occ_2d_full_section", M.build_mesh_from_polygons,
         M._build_mesh_from_polygons_impl, (polys,), dict(mesh_size_mm=1.4,
                                                          min_size_mm=0.35)),
        ("gmsh_cdt_backend", GG.triangulate_gmsh, GG._triangulate_gmsh_impl,
         (V, S, GM._cell_area(1.0)), dict(hole_pts=[[8.5, 8.5]])),
        ("mechanical_modal_stator", MO.build_stator_mesh,
         MO._build_stator_mesh_impl, (polys,), dict(mesh_size_mm=1.5)),
        ("mechanical_rotor_stress", RS._build_rotor_mesh,
         RS._build_rotor_mesh_impl, (polys,), dict(mesh_size_mm=1.0)),
        ("static3d_tet_sphere", ME.sphere_in_box, ME._sphere_in_box_impl,
         (), dict(radius=0.01, h_magnet=0.004)),
        ("static3d_section_2d", MM.build_section_mesh_2d,
         MM._build_section_mesh_2d_impl, (section,),
         dict(box_factor=2.0, h_gap=0.9, h_solid=2.0, grade_far=1.0)),
        ("static3d_band_pieces", _banded, _banded_in_process, (section,), {}),
    ]


_BAND_KW = dict(n_ring=168, box_factor=2.0, h_gap=0.9, h_solid=2.2)


def _banded(section):
    """The banded 3-D section: two ``band._mesh_piece`` worker calls."""
    from motor_ai_sim.simulation.static3d import band
    return band.build_banded_section(section, **_BAND_KW)


def _banded_in_process(section):
    """The same, with ``_mesh_piece`` running its body in THIS process."""
    from motor_ai_sim.simulation.static3d import band
    real = band._mesh_piece
    band._mesh_piece = band._mesh_piece_impl
    try:
        return band.build_banded_section(section, **_BAND_KW)
    finally:
        band._mesh_piece = real


_TIMING = re.compile(r"(^|[._])(build_s|t|s|time|elapsed|seconds|t_\w+|\w+_s)$")


def flatten(obj: Any, prefix: str = "", out: Dict[str, Any] = None,
            depth: int = 0) -> Dict[str, Any]:
    """Every array / number inside a result, keyed by its path (meshes by
    their p/t, dataclasses and objects by their fields).  Timing fields are
    skipped (they differ run to run by nature)."""
    out = {} if out is None else out
    if depth > 6:
        return out
    if isinstance(obj, np.ndarray):
        out[prefix] = obj
    elif isinstance(obj, (bool, int, float, np.number)):
        if not _TIMING.search(prefix):
            out[prefix] = obj
    elif isinstance(obj, str) or obj is None:
        return out
    elif isinstance(obj, dict):
        for k in sorted(obj, key=str):
            flatten(obj[k], "%s.%s" % (prefix, k), out, depth + 1)
    elif isinstance(obj, (list, tuple)):
        if obj and all(isinstance(x, (int, float)) for x in obj):
            out[prefix] = np.asarray(obj, float)
        else:
            for i, x in enumerate(obj):
                flatten(x, "%s[%d]" % (prefix, i), out, depth + 1)
    elif hasattr(obj, "p") and hasattr(obj, "t"):        # a scikit-fem mesh
        out[prefix + ".p"] = np.asarray(obj.p)
        out[prefix + ".t"] = np.asarray(obj.t)
    elif hasattr(obj, "__dict__"):
        flatten(vars(obj), prefix, out, depth + 1)
    elif callable(obj):
        return out
    return out


def compare(a: Any, b: Any) -> Dict[str, Any]:
    """Max abs deviation between two results; ``identical`` when every array
    is bit-identical and every number equal."""
    fa, fb = flatten(a), flatten(b)
    if set(fa) != set(fb):
        return {"identical": False, "max_abs_dev": float("inf"),
                "keys_only_in_one": sorted(set(fa) ^ set(fb))[:10]}
    dev, ident = 0.0, True
    for k in fa:
        x, y = np.asarray(fa[k]), np.asarray(fb[k])
        if x.shape != y.shape:
            return {"identical": False, "max_abs_dev": float("inf"),
                    "shape_mismatch": k}
        if x.size == 0:
            continue
        if x.dtype.kind in "biu" and y.dtype.kind in "biu":
            if not np.array_equal(x, y):
                ident = False
                dev = max(dev, float(np.max(np.abs(x.astype(np.int64) - y.astype(np.int64)))))
            continue
        d = float(np.max(np.abs(x.astype(float) - y.astype(float))))
        if not np.array_equal(x, y):
            ident = False
        dev = max(dev, d)
    return {"identical": ident, "max_abs_dev": dev, "n_fields": len(fa)}
