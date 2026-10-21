"""Local physical sizing of long rotor material interfaces."""
import math

import numpy as np
from shapely.geometry import LineString, Point

from motor_ai_sim.simulation import geo_mesh_gmsh as gg


def plan_rotor_interfaces(V, S, area, regions=None, holes=()):
    """Split kept two-face chords from their *local* clearance and face caps.

    Geometry and every original vertex/segment chord are retained exactly;
    only collinear vertices are inserted.  Returns the new ``V``/``S``, the
    planned child edges that both backends must keep whole, and an explicit
    size row for every output segment.  The latter preserves the original
    PSLG sizing of untouched segments, so inserted siblings cannot become
    artificial features when a backend normalizes the graph.
    """
    vv = np.asarray(V, float).reshape(-1, 2)
    ss = np.asarray(S, np.int64).reshape(-1, 2)
    faces = gg._faces(vv, ss)
    hole_pts = [Point(p) for p in np.asarray(holes, float).reshape(-1, 2)]
    keep = [not any(f.contains(p) for p in hole_pts) for f in faces]
    key = {(float(x), float(y)): i for i, (x, y) in reversed(list(enumerate(vv)))}
    sid = {(min(int(a), int(b)), max(int(a), int(b))): i
           for i, (a, b) in enumerate(ss)}
    uses = gg._ring_use_counts(ss, faces, keep, key, sid)
    adj = [[] for _ in ss]
    for fi, face in enumerate(faces):
        if not keep[fi]:
            continue
        for ring in [face.exterior] + list(face.interiors):
            c = list(ring.coords)
            for ca, cb in zip(c[:-1], c[1:]):
                a, b = key.get(tuple(ca)), key.get(tuple(cb))
                si = sid.get((min(a, b), max(a, b))) if a is not None and b is not None else None
                if si is not None:
                    adj[si].append(fi)
    targets = ([gg._edge_of_area(area)] * len(faces)
               if (regions is None or not len(regions)) and area and area > 0
               else [None] * len(faces))
    if regions is not None and len(regions):
        for row in np.asarray(regions, float).reshape(-1, 4):
            pt = Point(row[:2])
            for fi, face in enumerate(faces):
                if keep[fi] and face.contains(pt):
                    targets[fi] = gg._edge_of_area(row[3]) if row[3] > 0 else None
                    break

    lines = [LineString([vv[a], vv[b]]) for a, b in ss]
    import shapely
    tree = shapely.STRtree(lines)
    lengths = np.linalg.norm(vv[ss[:, 1]] - vv[ss[:, 0]], axis=1)
    frozen = uses != 2
    original_lfs, original_near = gg._feature_sizes(vv, ss)
    K = gg._env_float("SB_GMSH_CDT_LFS_K", gg._LFS_K)
    original_h = gg._segment_sizes(
        lengths, original_lfs, original_near, frozen, K)
    from motor_ai_sim.simulation import geo_mesh_netgen as ng
    growth = ng._env_float("SB_NETGEN_CDT_GRADING", ng._GRADING)
    # Resource fence only: this does not estimate triangles or alter physical
    # sizes.  It rejects pathological planner work before allocating a huge
    # cut list; the backend triangle budget remains an independent gate.
    max_evals = int(gg._env_float(
        "SB_ROTOR_INTERFACE_MAX_DISTANCE_EVALS", 2_000_000.0))
    max_children = int(gg._env_float(
        "SB_ROTOR_INTERFACE_MAX_CHILDREN", 200_000.0))
    n_evals = 0
    verts, segs, planned, sizes = vv.tolist(), [], [], []
    incidence = [set() for _ in range(len(vv))]
    for si, (a, b) in enumerate(ss):
        incidence[int(a)].add(si)
        incidence[int(b)].add(si)
    inserted = 0
    detail = []
    for si, (a, b) in enumerate(ss):
        caps = [targets[fi] for fi in adj[si] if targets[fi] is not None]
        L = float(lengths[si])
        if uses[si] != 2 or not caps or L <= min(caps) * (1.0 + 1e-12):
            segs.append((int(a), int(b)))
            planned.append(False)
            sizes.append(float(original_h[si]))
            continue
        cap = min(caps)
        incident = (incidence[int(a)] | incidence[int(b)]) - {si}
        # Outside cap/K no segment can reduce the face cap.  Query once from
        # the original chord; local evaluations then stay independent of the
        # appended collinear pieces and scale to a full rotor PSLG.
        candidates = [int(j) for j in tree.query(lines[si].buffer(cap / K))
                      if int(j) != si and int(j) not in incident]
        candidates.sort()

        def local_h(x):
            nonlocal n_evals
            n_evals += max(1, len(candidates))
            if n_evals > max_evals:
                from motor_ai_sim.simulation.geo_mesh import MeshBudgetExceeded
                raise MeshBudgetExceeded(
                    "mesh budget: rotor interface planner exceeded its %d "
                    "distance-evaluation resource limit" % max_evals)
            p = Point(vv[a] + (vv[b] - vv[a]) * (x / L))
            best_d, best_j = math.inf, -1
            for j in candidates:
                line = lines[j]
                d = p.distance(line)
                if d < best_d:
                    best_d, best_j = d, j
            h = min(cap, K * best_d)
            if best_j >= 0 and frozen[best_j]:
                h = min(cap, max(h, float(lengths[best_j])))
            return max(h, 1e-4)

        # Adaptive physical samples: resolve an interval until its chord is no
        # longer than the local size at either end and its midpoint.
        cuts = [0.0, L]
        changed = True
        while changed:
            changed = False
            nxt = [cuts[0]]
            for x0, x1 in zip(cuts[:-1], cuts[1:]):
                xm = 0.5 * (x0 + x1)
                if x1 - x0 > min(local_h(x0), local_h(xm), local_h(x1)) * (1 + 1e-12):
                    if inserted + len(nxt) + 2 > max_children:
                        from motor_ai_sim.simulation.geo_mesh import MeshBudgetExceeded
                        raise MeshBudgetExceeded(
                            "mesh budget: rotor interface planner exceeded its "
                            "%d-child resource limit" % max_children)
                    nxt.extend((xm, x1))
                    changed = True
                else:
                    nxt.append(x1)
            cuts = nxt
        hs = np.asarray([local_h(x) for x in cuts])
        # Enforce the existing interior grading in both directions.
        for i in range(1, len(hs)):
            hs[i] = min(hs[i], hs[i - 1] + growth * (cuts[i] - cuts[i - 1]))
        for i in range(len(hs) - 2, -1, -1):
            hs[i] = min(hs[i], hs[i + 1] + growth * (cuts[i + 1] - cuts[i]))
        # Equal spacing in the size metric integral ds/h(s).  This balances
        # both ends and cannot leave the tiny terminal remainder produced by
        # a one-way march.
        cx = np.asarray(cuts, float)
        metric = np.zeros(len(cx), float)
        metric[1:] = np.cumsum(np.diff(cx) * 0.5 * (1.0 / hs[:-1] + 1.0 / hs[1:]))
        n_piece = max(1, int(math.ceil(metric[-1] - 1e-12)))
        while True:
            if inserted + n_piece > max_children:
                from motor_ai_sim.simulation.geo_mesh import MeshBudgetExceeded
                raise MeshBudgetExceeded(
                    "mesh budget: rotor interface planner exceeded its "
                    "%d-child resource limit" % max_children)
            out = np.interp(np.linspace(0.0, metric[-1], n_piece + 1), metric, cx)
            mids = 0.5 * (out[:-1] + out[1:])
            allowed = np.minimum.reduce((np.interp(out[:-1], cx, hs),
                                         np.interp(mids, cx, hs),
                                         np.interp(out[1:], cx, hs)))
            if np.all(np.diff(out) <= allowed * (1.0 + 1e-12)):
                break
            n_piece = max(n_piece + 1, int(math.ceil(
                n_piece * float(np.max(np.diff(out) / allowed)))))
        out = out.tolist()
        chain = [int(a)]
        for x in out[1:-1]:
            verts.append((vv[a] + (vv[b] - vv[a]) * (x / L)).tolist())
            chain.append(len(verts) - 1)
        chain.append(int(b))
        segs.extend(zip(chain[:-1], chain[1:]))
        planned.extend([True] * (len(chain) - 1))
        sizes.extend(np.diff(out).tolist())
        inserted += len(chain) - 2
    segs = np.asarray(segs, np.int64)
    planned = np.asarray(planned, bool)
    size_rows = np.column_stack((segs, np.asarray(sizes, float)))
    return (np.asarray(verts, float), segs, segs[planned].copy(), size_rows)
