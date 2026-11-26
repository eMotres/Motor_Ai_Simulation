"""Mesh telemetry: DIAGNOSTIC ONLY (mesh study 2026-11-17, TASK_mesh_gap_stage3.md item 4).

Off unless ``SB_MESH_TELEMETRY=1``; then a solve's result carries ``mesh_telemetry`` and nothing else about the solve changes (no value
is read back by the solver).  All lengths are in MILLIMETRES (the mesh coordinates are metres; they are converted here and every key
says ``_mm``).  Contents:

* ``belt``: one entry per belt (gap slice) welded into a half: ``half``, ``n_iron`` (nodes on the iron-side ring), ``n_slip`` (angular
  columns), ``K`` (rows), ``faces`` / ``m_per_face`` (angular cells per belt face), and what the belt ADDED: vertices, edges
  (unique, not counting edges that already existed on the iron ring), triangles, P2 DOFs (vertices + edges);
* ``regions``: per half and per region tag, the triangles and the P2 DOFs they touch (``incident``) and the DOFs only that region
  touches (``exclusive``); a P2 DOF is a vertex or an edge midpoint;
* ``gap_distance_bins_mm``: the triangles of each half binned by the distance of their centroid from the gap circle.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import numpy as np

ENV = "SB_MESH_TELEMETRY"
#: bin edges for the centroid distance from the gap circle [mm]; the last bin is open
BIN_EDGES_MM = (0.0, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0)


def enabled() -> bool:
    return (os.environ.get(ENV) or "").strip().lower() in ("1", "true", "yes", "on")


def _edges(t: np.ndarray) -> np.ndarray:
    """Unique undirected edges of a (3, nt) triangle array, as sorted (a, b) pairs, shape (ne, 2)."""
    e = np.concatenate([t[[0, 1]], t[[1, 2]], t[[2, 0]]], axis=1).T
    e.sort(axis=1)
    return np.unique(e, axis=0)


def belt_entry(half: str, n_iron: int, n_slip: int, K: int, ncell: int, n_sectors: int, p_before: int, belt_p: int,
               belt_tris: np.ndarray, iron_nodes: np.ndarray) -> Dict[str, Any]:
    """What one welded belt added.  ``belt_tris`` is (3, nt) in the final node numbering, ``iron_nodes`` the pre-existing nodes on the
    iron ring (edges between two of them already existed)."""
    e = _edges(belt_tris) if belt_tris.size else np.zeros((0, 2), dtype=np.int64)
    if e.size and iron_nodes.size:
        both = np.isin(e[:, 0], iron_nodes) & np.isin(e[:, 1], iron_nodes)
        e = e[~both]
    return {"half": half, "n_iron": int(n_iron), "n_slip": int(n_slip), "K": int(K), "faces": max(1, int(n_sectors)),
            "m_per_face": int(ncell), "added_vertices": int(belt_p), "added_edges": int(len(e)),
            "added_triangles": int(belt_tris.shape[1]), "added_p2_dofs": int(belt_p + len(e))}


def regions(mesh, tags: np.ndarray, names: Optional[Dict[int, str]] = None) -> Dict[str, Any]:
    """Per region tag: triangles, incident and exclusive P2 DOFs of this half-mesh."""
    t = np.asarray(mesh.t, dtype=np.int64)
    tags = np.asarray(tags)
    n_nodes = int(np.asarray(mesh.p).shape[1])
    edges_all = _edges(t)
    key = lambda e: e[:, 0].astype(np.int64) * (n_nodes + 1) + e[:, 1]          # noqa: E731
    edge_id = {int(k): i for i, k in enumerate(key(edges_all))}
    per: Dict[int, Dict[str, Any]] = {}
    owner_v = [set() for _ in range(n_nodes)]
    owner_e = [set() for _ in range(len(edges_all))]
    for tag in np.unique(tags):
        sel = t[:, tags == tag]
        verts = np.unique(sel)
        ek = key(_edges(sel))
        eids = [edge_id[int(k)] for k in ek]
        per[int(tag)] = {"triangles": int(sel.shape[1]), "_v": verts, "_e": eids}
        for v in verts:
            owner_v[int(v)].add(int(tag))
        for e in eids:
            owner_e[e].add(int(tag))
    out: Dict[str, Any] = {}
    for tag, d in per.items():
        inc = len(d["_v"]) + len(d["_e"])
        exc = sum(1 for v in d["_v"] if len(owner_v[int(v)]) == 1) + sum(1 for e in d["_e"] if len(owner_e[e]) == 1)
        out[(names or {}).get(tag, str(tag))] = {"triangles": d["triangles"], "incident_p2_dofs": int(inc), "exclusive_p2_dofs": int(exc)}
    out["_total_p2_dofs"] = int(n_nodes + len(edges_all))
    return out


def gap_distance_bins(mesh, r_gap_mm: float) -> Dict[str, int]:
    """Triangles binned by the distance of their centroid from the circle of radius ``r_gap_mm`` (mm)."""
    p = np.asarray(mesh.p, dtype=float) * 1e3
    t = np.asarray(mesh.t, dtype=np.int64)
    cx, cy = p[0, t].mean(axis=0), p[1, t].mean(axis=0)
    d = np.abs(np.hypot(cx, cy) - float(r_gap_mm))
    edges = list(BIN_EDGES_MM) + [np.inf]
    out: Dict[str, int] = {}
    for lo, hi in zip(edges[:-1], edges[1:]):
        label = "%g-%s" % (lo, "inf" if hi == np.inf else "%g" % hi)
        out[label] = int(((d >= lo) & (d < hi)).sum())
    return out


def collect(halves: List[Dict[str, Any]], belts: List[Dict[str, Any]], r_gap_mm: float) -> Dict[str, Any]:
    """The ``mesh_telemetry`` block: ``halves`` = ``[{"name", "mesh", "tags"}]``, ``belts`` = the entries recorded at the welds."""
    return {"units": "mm", "r_gap_mm": float(r_gap_mm), "belt": list(belts),
            "regions": {h["name"]: regions(h["mesh"], h["tags"]) for h in halves},
            "gap_distance_bins_mm": {h["name"]: gap_distance_bins(h["mesh"], r_gap_mm) for h in halves}}
