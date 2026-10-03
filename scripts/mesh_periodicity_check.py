# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) MOTRES d.o.o. and contributors
"""Is the ROTOR mesh of a CDT backend pole-pair periodic (what TDM needs)?

TDM (src/motor_ai_sim/simulation/time_periodic.py, fem_solver_2d.py ``_td_map``)
maps rotor dofs through ``rotor_window.period_shift_map``: every rotor point,
rotated by one electrical period (= one pole pair, 360/pole_pairs deg) or half
of it, folded into the modelled sector, must have an image node within
``MATCH_REL_TOL`` (1e-6) x h of its own, h = sqrt(smallest rotor triangle
area).  Otherwise TDM refuses (full period) or drops to the full period (half).

This script builds the REAL rotor mesh of a saved duty (the geometry-driven
mesh of the solve, via ``geo_mesh_halves`` captured from the unchanged solver
entry point; the solve itself is aborted right after the mesh), per backend,
and reports for the full-period and half-period rotations:

  * max nodal distance to the best image node [m], and as a multiple of the
    TDM tolerance 1e-6 h (> 1 means TDM refuses);
  * number of nodes without an image inside the tolerance;
  * element centroids: unmatched count and non-bijective count;
  * element connectivity: triangles whose mapped node triple is not a triangle
    of the mesh (nodes mapped by nearest image, however far).

    python scripts/mesh_periodicity_check.py run  --dies D --config C --workdir W \
        [--cases d40,l13,l155] [--backends netgen,gmsh]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
#: tag -> (die dir, config file stem, duty name) inside --dies
CASES = {
    "d40": ("d40", "L12", "rated"),
    "l13": ("l13", "L13", "rated"),
    "l155": ("l155", "L155 motor", "rated 1x9 mm"),
}


class _Captured(BaseException):     # BaseException: the solver's own
    pass                              # `except Exception` fallbacks must not eat it


def child(spec_path, out_path):
    sys.path.insert(0, HERE)
    import numpy as np
    import compare_mesher_triangle_vs_gmsh as cmp
    from motor_ai_sim.simulation import geo_mesh as gm

    spec = json.load(open(spec_path, encoding="utf-8"))
    y, geo, duty, mats, st = cmp._compose(spec["dies"], *spec["case"])
    st = dict(st, **(spec.get("settings") or {}))
    nsec = int(st.get("mesh.nSectors", 2) or 1)
    nsec = nsec if nsec > 1 else 1
    num_poles = int(geo["num_poles"])
    bc_sign = -1 if ((num_poles // nsec) % 2 == 1) else 1
    cap = {}
    orig = gm.geo_mesh_halves
    calls = []

    def spy(p, polys, **k):
        out = orig(p, polys, **k)
        ms, ts, _cs, mr, tr, _cr = out
        calls.append((np.array(mr.p), np.array(mr.t), np.array(tr)))
        if len(calls) >= 2:           # the second build is the solve mesh (builds: stator+rotor halves)
            raise _Captured()
        return out

    gm.geo_mesh_halves = spy
    try:
        cmp.run(spec_path, out_path + ".solve.json")
    except _Captured:
        pass
    finally:
        gm.geo_mesh_halves = orig
    P, T, tags = calls[-1]
    np.savez(out_path, P=P, T=T, tags=tags,
             meta=json.dumps({"n_sectors": nsec, "bc_sign": bc_sign,
                              "num_poles": num_poles, "builds": len(calls),
                              "backend": spec["mesher"]}))


def analyse(npz_path):
    import numpy as np
    from scipy.spatial import cKDTree
    d = np.load(npz_path, allow_pickle=False)
    P, T = d["P"], d["T"]
    meta = json.loads(str(d["meta"]))
    NS, bc, npole = meta["n_sectors"], meta["bc_sign"], meta["num_poles"]
    from motor_ai_sim.simulation.rotor_window import MATCH_REL_TOL
    a = P[:, T[0]]; b = P[:, T[1]]; c = P[:, T[2]]
    area = 0.5 * np.abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1]))
    h = float(np.sqrt(area.min()))
    tol = MATCH_REL_TOL * h
    used = np.unique(T)
    pts = P[:, used]
    cen = (a + b + c) / 3.0
    pp = npole // 2
    period = 360.0 / pp                      # one electrical period [deg mech]
    phi = 2.0 * math.pi / NS
    tree_n = cKDTree(pts.T)
    tree_c = cKDTree(cen.T)

    def best_image(X, tree, rot):
        """Nearest image over the folds of the sector for a rotation `rot`."""
        c0, s0 = math.cos(rot), math.sin(rot)
        xr = np.vstack([c0 * X[0] - s0 * X[1], s0 * X[0] + c0 * X[1]])
        bd = np.full(X.shape[1], np.inf); bj = np.zeros(X.shape[1], int)
        bf = np.zeros(X.shape[1], int)
        for f in range(NS):
            cb, sb = math.cos(-f * phi), math.sin(-f * phi)
            z = np.vstack([cb * xr[0] - sb * xr[1], sb * xr[0] + cb * xr[1]])
            dd, jj = tree.query(z.T)
            m = dd < bd
            bd[m] = dd[m]; bj[m] = jj[m]; bf[m] = f
        return bd, bj, bf

    # element connectivity under the nearest-node map
    out = {"backend": meta["backend"], "n_nodes": int(used.size),
           "n_tri": int(T.shape[1]), "h_m": h, "tol_m": tol,
           "n_sectors": NS, "bc_sign": bc, "num_poles": npole, "rot": {}}
    for label, deg in (("full", period), ("half", 0.5 * period)):
        for sgn in (+1, -1):
            rot = sgn * math.radians(deg)
            dn, jn, _ = best_image(pts, tree_n, rot)
            dc, jc, fc = best_image(cen, tree_c, rot)
            # connectivity by POSITION: rotate the 3 vertices of every element
            # with its own centroid's rotation+fold; they must coincide (as a
            # set, tolerance tol) with the vertices of its image element.
            bad_el = 0
            c0, s0 = math.cos(rot), math.sin(rot)
            for e in range(T.shape[1]):
                V = np.stack([a[:, e], b[:, e], c[:, e]], 1)            # (2,3)
                xr = np.vstack([c0 * V[0] - s0 * V[1], s0 * V[0] + c0 * V[1]])
                cb, sb = math.cos(-fc[e] * phi), math.sin(-fc[e] * phi)
                z = np.vstack([cb * xr[0] - sb * xr[1], sb * xr[0] + cb * xr[1]])
                j = jc[e]
                W = np.stack([a[:, j], b[:, j], c[:, j]], 1)
                dm = np.hypot(z[0][:, None] - W[0][None, :], z[1][:, None] - W[1][None, :])
                if not (np.all(dm.min(1) < tol) and np.all(dm.min(0) < tol)):
                    bad_el += 1
            out["rot"]["%s%+d" % (label, sgn)] = {
                "node_max_dist_m": float(dn.max()),
                "node_max_dist_over_tol": float(dn.max() / tol),
                "nodes_unmatched": int(np.sum(dn >= tol)),
                "centroid_max_dist_m": float(dc.max()),
                "centroids_unmatched": int(np.sum(dc >= tol)),
                "centroid_nonbijective": int(T.shape[1] - np.unique(jc).size),
                "elements_bad_connectivity": int(bad_el),
            }
    return out


def run(a):
    os.makedirs(a.workdir, exist_ok=True)
    results = []
    for tag in a.cases.split(","):
        for be in a.backends.split(","):
            name = "%s_%s" % (tag, be)
            spec = {"tag": tag, "case": list(CASES[tag]), "mesher": be,
                    "noload": False, "dies": os.path.abspath(a.dies),
                    "live_config": os.path.abspath(a.config)}
            sp = os.path.join(a.workdir, name + ".spec.json")
            json.dump(spec, open(sp, "w"), indent=1)
            cd = os.path.join(a.workdir, name + ".cfg")
            os.makedirs(cd, exist_ok=True)
            import shutil
            for fn in ("motor_config.yaml", "materials_library.yaml",
                       "wire_stock.yaml", "end_effect_3d.json"):
                p = os.path.join(a.config, fn)
                if os.path.exists(p):
                    shutil.copy(p, cd)
            env = dict(os.environ, MOTOR_AI_SIM_CONFIG=os.path.join(cd, "motor_config.yaml"),
                       MOTOR_AI_SIM_GEO_CDT=be, SB_NO_WARM_CACHE="1", SB_GEO_MESH="1")
            out = os.path.join(a.workdir, name + ".npz")
            r = subprocess.call([sys.executable, os.path.abspath(__file__), "child", sp, out],
                                env=env, cwd=cd,
                                stdout=open(os.path.join(a.workdir, name + ".out"), "w"),
                                stderr=open(os.path.join(a.workdir, name + ".err"), "w"))
            if r != 0 or not os.path.exists(out):
                results.append({"case": tag, "backend": be, "error": "child exit %s" % r})
                continue
            res = analyse(out); res["case"] = tag
            results.append(res)
            print(json.dumps(res), flush=True)
    json.dump(results, open(os.path.join(a.workdir, "periodicity.json"), "w"), indent=1)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "child":
        child(sys.argv[2], sys.argv[3])
    else:
        ap = argparse.ArgumentParser()
        ap.add_argument("cmd", choices=["run"])
        ap.add_argument("--dies", required=True)
        ap.add_argument("--config", required=True)
        ap.add_argument("--workdir", required=True)
        ap.add_argument("--cases", default="d40,l13,l155")
        ap.add_argument("--backends", default="netgen,gmsh")
        run(ap.parse_args())
