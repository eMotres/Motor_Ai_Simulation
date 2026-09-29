"""What the static3d model LOOKS like — geometry, mesh surfaces and fields, in
payloads small enough for a browser.

The rest of this package answers questions with numbers.  This module exists so
those numbers can be LOOKED at: the sector that was actually meshed, the tets
that were actually solved, the |B| that came out.  It computes nothing new — it
reads ``MotorSection``, ``TaggedTetMesh`` and ``Solution`` and turns them into
triangles and floats.

Three rules it does not bend:

* **Nothing is invented.**  Every triangle is a face of a real tet; every colour
  value is that tet's own element value.  Where the model has no answer (air,
  the region outside the mesh) nothing is drawn at all, rather than drawn in a
  neutral colour that reads as "zero".
* **Nothing is silently reduced.**  A payload is bounded by ``max_tris``, and
  when the bound bites, the payload SAYS so and reports how many faces exist
  against how many are drawn.  A viewer that quietly shows a tenth of the mesh
  is worse than one that refuses.
* **The half model stays a half model here.**  The solved domain is one
  anti-periodic sector of the ``z >= 0`` half; mirroring and repeating it into a
  whole machine is a VIEWING choice, made (and labelled) in the browser, not a
  quiet doubling of the payload.

Units: the mesh is metres (``extrude_section`` converts); everything this module
emits is MILLIMETRES, because that is what the rest of the web viewer draws in.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from motor_ai_sim.simulation.static3d.motor_geometry import MM, MotorSection
from motor_ai_sim.simulation.static3d.solver import MU0

# --------------------------------------------------------------------------
# fidelities
# --------------------------------------------------------------------------
# One named rung per honest cost.  The knobs are the ones ``build_motor_mesh``
# and ``solve_sector`` take; the wall times are MEASURED on this machine
# (my_40mm_last, 12 slots / 14 poles, 40 mm OD) and are quoted to the user
# before a solve starts, because the project's rule is that a run whose cost is
# not stated is a run the user did not agree to.

#
# Every rung resolves the AIR GAP (``h_gap`` 0.35 mm against a 0.2 mm gap).  An
# earlier draft had a 0.9 mm "cheap" rung and it made no difference at all to
# the answer — measured, h_gap 0.9 / 0.55 / 0.35 all returned the same gap
# fundamental, because what was wrong was never the mesh.  See ``iron`` below.

FIDELITY: Dict[str, dict] = {
    "coarse": dict(
        box_factor=2.0, h_gap=0.35, h_solid=2.0, n_stack=3, n_cap=3, end_bias=1.0, order=1,
        tol=3e-3, max_iter=90, damping=0.35,
        label="coarse",
        note="P1, 3 axial layers — 77 k tets, 15 k dofs, and it converges. "
             "MEASURED against the passport: gap fundamental 1.4410 T vs "
             "1.50775 T (4.4 % low, which is P1), k_flux_self 0.9616 vs "
             "0.95944 (0.2 %). The END EFFECT is right at this price; the "
             "absolute field is not",
    ),
    "medium": dict(
        box_factor=2.0, h_gap=0.35, h_solid=1.4, n_stack=4, n_cap=4, end_bias=1.0, order=2,
        tol=3e-3, max_iter=90, damping=0.35,
        label="medium",
        note="P2 — 107 k tets, 152 k dofs. MEASURED against the passport: gap "
             "fundamental 1.5063 T vs 1.50775 T (0.1 %), k_flux_self 0.9566 vs "
             "0.95944, on a 40 mm box instead of the passport's 160 mm",
    ),
    "passport": dict(
        box_factor=8.0, h_gap=0.35, h_solid=1.2, n_stack=8, n_cap=10, order=2,
        tol=3e-3, max_iter=90, damping=0.35,
        label="passport",
        note="the Stage A mesh itself (config/end_effect_3d.json::model) — "
             "241 k tets, 334 k dofs, and the 74-minute solve that goes with it",
    ),
}

#: Wall time in seconds for ``build mesh + solve``, and where the number came
#: from.  Quoted to the user BEFORE a run starts, because a run whose cost was
#: not stated is a run nobody agreed to.  ``measured`` says whether the number
#: was timed on this machine or extrapolated — an extrapolated quote that does
#: not admit it is a guess dressed as a fact.
COST_S: Dict[Tuple[str, bool], float] = {
    ("coarse", True): 15.0,
    ("coarse", False): 60.0,
    ("medium", True): 50.0,
    ("medium", False): 900.0,
    ("passport", True): 200.0,
    ("passport", False): 4475.0,
}

COST_BASIS: Dict[Tuple[str, bool], str] = {
    ("coarse", True): "measured: 1 s mesh + 4 s solve",
    ("coarse", False): "measured END TO END, 47 s: mesh, 55 Picard sweeps "
                       "CONVERGED at 2.9e-3 against tol 3e-3, then the gap "
                       "profile and the demag slices (which are a third of it)",
    ("medium", True): "measured: 2 s mesh + 41 s solve",
    ("medium", False): "measured: 660 s over 45 Picard sweeps (residual 7.4e-3 "
                       "against tol 3e-3 — the loop is allowed 90)",
    ("passport", True): "ESTIMATED by dof scaling from the medium rung",
    ("passport", False): "config/end_effect_3d.json::solves[0].wall_s — the "
                         "passport's own reference solve, 58 sweeps, converged",
}

#: Default ceiling on triangles in one surface payload.  ~35 k triangles is
#: about 1.5 MB of JSON at the precision below and renders instantly; the cap
#: exists so a fine mesh cannot wedge the browser, not to make things pretty.
MAX_TRIS_DEFAULT = 35000

#: Coordinates are emitted rounded to this many decimals in mm.  1e-4 mm is
#: 100 nm — four orders below the smallest real feature (the 0.2 mm air gap),
#: so the rounding cannot move a surface anywhere a viewer could see.
_ROUND = 4


# --------------------------------------------------------------------------
# element selection: cut planes
# --------------------------------------------------------------------------

def element_centroids(mesh) -> np.ndarray:
    """(3, nelem) centroid of every tet, in METRES (the mesh's own units)."""
    return np.asarray(mesh.p)[:, np.asarray(mesh.t)].mean(axis=1)


def cut_mask(mesh,
             cut_z_mm: Optional[float] = None,
             cut_theta_deg: Optional[float] = None,
             r_max_mm: Optional[float] = None) -> np.ndarray:
    """Which elements survive the cut planes — (nelem,) bool.

    A cut is a SELECTION, not a new geometry: the tets on the far side are
    dropped whole, and the faces that were interior become surface, which is
    what lets the user see the inside of a solved mesh without anything being
    re-meshed or interpolated.  The plane therefore lands on element centroids,
    never mid-tet, and the drawn surface is jagged at the cut by exactly one
    element — honest, and visibly so.
    """
    c = element_centroids(mesh)
    keep = np.ones(c.shape[1], dtype=bool)
    if cut_z_mm is not None:
        keep &= c[2] <= float(cut_z_mm) * MM + 1e-12
    if cut_theta_deg is not None:
        th = np.degrees(np.arctan2(c[1], c[0]))
        th = np.where(th < -1e-9, th + 360.0, th)
        keep &= th <= float(cut_theta_deg) + 1e-9
    if r_max_mm is not None:
        keep &= np.hypot(c[0], c[1]) <= float(r_max_mm) * MM + 1e-12
    return keep


# --------------------------------------------------------------------------
# surface extraction
# --------------------------------------------------------------------------

# The four faces of a tet, and the vertex each one is opposite to.  The opposite
# vertex is what orients the face OUTWARD without any global flood fill: the
# outward normal is the one pointing away from the tet's own fourth corner.
_FACE_NODES = ((0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3))
_FACE_OPP = (3, 2, 1, 0)


def _face_table(t: np.ndarray, n_nodes: int
                ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Every (element, face) pair: nodes, opposite node, owner, sorted key."""
    ne = t.shape[1]
    tri = np.empty((3, 4 * ne), dtype=np.int64)
    opp = np.empty(4 * ne, dtype=np.int64)
    for k, (a, b, c) in enumerate(_FACE_NODES):
        tri[:, k * ne:(k + 1) * ne] = t[[a, b, c]]
        opp[k * ne:(k + 1) * ne] = t[_FACE_OPP[k]]
    owner = np.tile(np.arange(ne, dtype=np.int64), 4)
    s = np.sort(tri, axis=0)
    nn = np.int64(n_nodes)
    key = (s[0] * nn + s[1]) * nn + s[2]
    return tri, opp, owner, key


def surface_faces(mesh,
                  cell_region: np.ndarray,
                  keep: Optional[np.ndarray] = None,
                  air_id: int = 0,
                  draw_air: bool = False
                  ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The visible skin of the kept, non-air elements.

    Returns ``(tri, owner, opp)`` — triangle node ids (3, nface), the element
    each triangle belongs to, and that element's opposite node (for orienting).

    A face is drawn when its owning element is kept and solid, and the element
    on the other side is either absent (mesh boundary), a DIFFERENT region (a
    material interface — the surface of the iron against air is exactly this),
    or cut away.  Interior faces between two kept tets of the same region are
    never drawn, which is what turns 56 000 tets into a few thousand triangles
    without dropping anything the eye would have seen.
    """
    t = np.asarray(mesh.t)
    ne = t.shape[1]
    n_nodes = np.asarray(mesh.p).shape[1]
    reg = np.asarray(cell_region, dtype=np.int64)
    if keep is None:
        keep = np.ones(ne, dtype=bool)
    keep = np.asarray(keep, dtype=bool)

    tri, opp, owner, key = _face_table(t, n_nodes)

    # Partner lookup: sort by key, adjacent equal keys are the two sides of one
    # interior face.  A tet mesh is conformal, so a key appears once or twice.
    order = np.argsort(key, kind="stable")
    ks = key[order]
    partner = np.full(key.size, -1, dtype=np.int64)
    same = np.flatnonzero(ks[:-1] == ks[1:])
    a, b = order[same], order[same + 1]
    partner[a] = b
    partner[b] = a

    own_reg = reg[owner]
    own_keep = keep[owner]
    solid = own_reg != air_id if not draw_air else np.ones(ne * 4, dtype=bool)

    has_p = partner >= 0
    other = np.where(has_p, owner[np.clip(partner, 0, None)], -1)
    other_reg = np.where(has_p, reg[np.clip(other, 0, None)], -1)
    other_keep = np.where(has_p, keep[np.clip(other, 0, None)], False)

    visible = own_keep & solid & (
        (~has_p) | (~other_keep) | (other_reg != own_reg))
    idx = np.flatnonzero(visible)
    return tri[:, idx], owner[idx], opp[idx]


def _orient(p: np.ndarray, tri: np.ndarray, opp: np.ndarray) -> np.ndarray:
    """Flip each triangle so its normal points away from the tet's 4th corner."""
    a = p[:, tri[0]]
    n = np.cross((p[:, tri[1]] - a).T, (p[:, tri[2]] - a).T).T
    inward = (n * (p[:, opp] - a)).sum(axis=0) > 0.0
    out = tri.copy()
    if inward.any():
        out[1, inward], out[2, inward] = tri[2, inward], tri[1, inward]
    return out


def _decimate(n: int, max_n: int) -> Optional[np.ndarray]:
    """Indices of an evenly-spread subset of ``n`` faces, or None if it fits.

    Evenly spread, not random: a stride keeps the surface's structure legible
    (you can still see the teeth) where a random sample turns it into confetti.
    It is still a HOLED surface, and the payload says so — this is a last-resort
    bound on bytes, not a level-of-detail scheme.
    """
    if n <= max_n:
        return None
    return np.unique(np.linspace(0, n - 1, max_n).astype(np.int64))


# --------------------------------------------------------------------------
# payloads
# --------------------------------------------------------------------------

#: The scale bound: a drawn triangle lives at the mesh's element size, so its
#: edges may not be a multiple of the extracted faces' own edges.  Loose on
#: purpose — this is the bound that would still hold if the emitted surface
#: were legitimately re-tessellated one day.
_EDGE_MEDIAN_FACTOR = 3.0
_EDGE_MAX_FACTOR = 4.0

#: The identity bound: today the emitted surface is a pure RELABELLING of the
#: extracted faces, so its edge lengths are the same numbers, and anything past
#: a micrometre is a remap error.  Tight on purpose — the scale bound alone
#: misses a scrambled small region (a magnet 12 mm across scrambles to only
#: ~2.3x its own median, which slips under any honest factor).
_EDGE_IDENTITY_MM = 1e-3


def _edge_stats(verts: np.ndarray, faces: np.ndarray) -> Dict[str, float]:
    """min / median / max triangle edge length for (nv, 3) verts, (nf, 3) faces.

    The one number that tells you whether a surface is a surface: a triangle on
    a body meshed at h has edges of order h, and a triangle whose edge is the
    size of the whole machine is not a triangle of that mesh at all.
    """
    if faces.size == 0:
        return {"min": 0.0, "median": 0.0, "max": 0.0}
    a, b, c = verts[faces[:, 0]], verts[faces[:, 1]], verts[faces[:, 2]]
    e = np.concatenate([np.linalg.norm(b - a, axis=1),
                        np.linalg.norm(c - b, axis=1),
                        np.linalg.norm(a - c, axis=1)])
    return {"min": float(e.min()), "median": float(np.median(e)),
            "max": float(e.max())}


def check_edge_scale(name: str, src: Dict[str, float], got: Dict[str, float],
                     n_faces: int) -> None:
    """Raise unless the drawn triangles are still the triangles they came from.

    ``src`` are the edge lengths of the extracted faces read through the GLOBAL
    node array; ``got`` are the edge lengths of what the payload actually
    carries.  Re-indexing is a relabelling, so these must agree; a drawn median
    twenty times the source's is a surface that spans the machine.
    """
    why = None
    if (got["median"] > _EDGE_MEDIAN_FACTOR * src["median"] + 1e-6
            or got["max"] > _EDGE_MAX_FACTOR * src["max"] + 1e-6):
        why = ("drawn triangles are far larger than the mesh's own elements")
    elif (abs(got["median"] - src["median"]) > _EDGE_IDENTITY_MM
            or abs(got["max"] - src["max"]) > _EDGE_IDENTITY_MM
            or abs(got["min"] - src["min"]) > _EDGE_IDENTITY_MM):
        why = ("drawn triangles are not the extracted faces relabelled")
    if why is None:
        return
    raise ValueError(
        f"surface payload for region {name!r} is not the surface it was built "
        f"from — {why}: emitted edge min {got['min']:.3f} / median "
        f"{got['median']:.3f} / max {got['max']:.3f} mm against source "
        f"{src['min']:.3f} / {src['median']:.3f} / {src['max']:.3f} mm over "
        f"{n_faces} faces — the vertex indices do not match the positions")


def _nodal_average(verts: np.ndarray, idx: np.ndarray,
                   tri_values: np.ndarray) -> np.ndarray:
    """Element values averaged onto the shared vertices, weighted by triangle
    area — the "nodal solution" every FEA post-processor draws.

    It is an INTERPOLATION, not a solve: the field is piecewise constant per
    tet, and averaging it at a node invents the gradient between two elements.
    That is exactly what ANSYS's nodal plot does and why ANSYS keeps the
    element plot beside it — the jump between neighbours is the discretisation
    error, and smoothing hides it.  So this is offered ALONGSIDE the per-face
    values, never instead of them, and the viewer says which one is on screen.

    Area weighting rather than a plain count: a sliver triangle at a corner
    carries as little of the average as it carries of the surface.
    """
    a = verts[:, idx[:, 0]]
    b = verts[:, idx[:, 1]]
    c = verts[:, idx[:, 2]]
    area = 0.5 * np.linalg.norm(np.cross((b - a).T, (c - a).T), axis=1)
    n_v = verts.shape[1]
    num = np.zeros(n_v)
    den = np.zeros(n_v)
    finite = np.isfinite(tri_values)
    w = np.where(finite, area, 0.0)
    v = np.where(finite, tri_values, 0.0)
    for k in range(3):
        np.add.at(num, idx[:, k], w * v)
        np.add.at(den, idx[:, k], w)
    out = np.full(n_v, np.nan)
    nz = den > 0
    out[nz] = num[nz] / den[nz]
    return out


def _emit_region(p_mm: np.ndarray, tri: np.ndarray,
                 tri_values: Optional[np.ndarray],
                 name: str = "?") -> dict:
    """Indexed positions + triangle list for one region, ready for JSON.

    ``tri`` is (3, nface) — one COLUMN per triangle — and the wire format is a
    flat list of consecutive triples, so the flattening has to run down the
    columns.  Flattening in C order instead builds each emitted triangle from
    the first corner of three DIFFERENT faces, which draws a web of strands
    across the interior of the model; that shipped once and is what the
    invariant below exists to stop.
    """
    used, inv = np.unique(tri.T.reshape(-1), return_inverse=True)
    verts = p_mm[:, used]
    idx = inv.astype(np.int32).reshape(-1, 3)

    # INVARIANT: the emitted surface is a re-indexing of the same triangles, so
    # its edge lengths must be the source faces' edge lengths.  Cheap (three
    # norms per triangle) and it catches every remap error there is: a wrong
    # order, a wrong compaction, positions and indices written out of step.
    src = _edge_stats(p_mm.T, tri.T)
    got = _edge_stats(verts.T, idx)
    check_edge_scale(name, src, got, int(tri.shape[1]))

    return {
        "positions": np.round(verts.T.reshape(-1), _ROUND).tolist(),
        "indices": idx.reshape(-1).tolist(),
        "tri_count": int(tri.shape[1]),
        "vertex_count": int(used.size),
        "edge_mm": {k: round(v, 6) for k, v in got.items()},
        "edge_src_mm": {k: round(v, 6) for k, v in src.items()},
        "values": (None if tri_values is None
                   else np.round(tri_values, 6).tolist()),
        # The same field read the other way: one value per VERTEX, so the
        # renderer can interpolate across the face (the smooth/"nodal" plot).
        # Both travel together — the viewer chooses, and says which it drew.
        "values_node": (None if tri_values is None else
                        np.round(_nodal_average(verts, idx,
                                                np.asarray(tri_values, float)),
                                 6).tolist()),
    }


def surface_payload(tm,
                    section: MotorSection,
                    *,
                    cut_z_mm: Optional[float] = None,
                    cut_theta_deg: Optional[float] = None,
                    draw_air: bool = False,
                    air_r_max_mm: Optional[float] = None,
                    values_el: Optional[np.ndarray] = None,
                    max_tris: int = MAX_TRIS_DEFAULT) -> dict:
    """Per-region surface triangles of a tagged tet mesh, in mm.

    ``values_el`` (nelem,) is carried through per TRIANGLE — the owning
    element's own value, piecewise constant, because that is what the element
    actually holds.  Each region ALSO carries ``values_node``: the same numbers
    area-averaged onto the shared vertices, which is what a smooth (nodal) plot
    needs and what ANSYS shows by default.  Both are emitted because they say
    different things — the per-face one is the solved field, the nodal one is an
    interpolation of it — and the viewer labels whichever it draws.
    """
    mesh = tm.mesh
    p_mm = np.asarray(mesh.p) / MM
    reg = np.asarray(tm.cell_region, dtype=np.int64)
    names = dict(tm.names)
    air_id = int(names.get("air", 0))
    kind_of = {r.name: r.kind for r in section.regions}
    mat_of = _region_materials(section)
    pol_of = magnet_polarity(section)

    keep = cut_mask(mesh, cut_z_mm, cut_theta_deg)
    if draw_air and air_r_max_mm is not None:
        # The air box is 40–160 mm of nothing.  Drawn at all, it is drawn only
        # near the machine, or the "mesh" the user sees is one enormous cube.
        c = element_centroids(mesh)
        near = np.hypot(c[0], c[1]) <= float(air_r_max_mm) * MM
        keep = keep & (near | (reg != air_id))

    tri, owner, opp = surface_faces(mesh, reg, keep=keep, air_id=air_id,
                                    draw_air=draw_air)
    tri = _orient(p_mm, tri, opp)

    total = int(tri.shape[1])
    regions_out: List[dict] = []
    shown = 0
    decimated = False
    id_to_name = {v: k for k, v in names.items()}
    for rid in np.unique(reg[owner]) if owner.size else []:
        sel = np.flatnonzero(reg[owner] == rid)
        name = id_to_name.get(int(rid), f"region_{int(rid)}")
        # Budget is shared out in proportion to size, so one big region cannot
        # starve the six small ones (or the magnets vanish and the iron stays).
        budget = max(64, int(round(max_tris * sel.size / max(total, 1))))
        pick = _decimate(sel.size, budget)
        if pick is not None:
            decimated = True
            sel = sel[pick]
        vals = None if values_el is None else np.asarray(values_el)[owner[sel]]
        d = _emit_region(p_mm, tri[:, sel], vals, name=name)
        d["name"] = name
        d["kind"] = kind_of.get(name, "air" if name == "air" else "unknown")
        d["material"] = mat_of.get(name)
        d["polarity"] = pol_of.get(name)
        regions_out.append(d)
        shown += d["tri_count"]

    return {
        "units": "mm",
        "regions": regions_out,
        "faces_total": total,
        "faces_shown": int(shown),
        "edge_mm": {
            "median": (round(float(np.median(
                [r["edge_mm"]["median"] for r in regions_out])), 6)
                if regions_out else 0.0),
            "max": (round(max(r["edge_mm"]["max"] for r in regions_out), 6)
                    if regions_out else 0.0),
            "note": ("triangle edge lengths of the DRAWN surface — they are the "
                     "mesh's own element size, and a payload where they are not "
                     "is refused rather than served"),
        },
        "decimated": bool(decimated),
        "max_tris": int(max_tris),
        "cut": {"z_mm": cut_z_mm, "theta_deg": cut_theta_deg,
                "air": bool(draw_air)},
        "counts": mesh_counts(tm),
    }


def magnet_polarity(section: MotorSection) -> Dict[str, int]:
    """+1 / -1 per magnet, from its OWN M against the local tangential direction.

    Not from the index parity.  The sector holds an ODD number of poles, so an
    index-parity colouring is right inside one sector and wrong in the next —
    which is exactly the picture a repeated view would show, with two same-poled
    magnets meeting at the seam.  Read off M, the polarity survives the repeat
    (the browser flips it per copy, because anti-periodic is what the sector
    is).
    """
    out: Dict[str, int] = {}
    for r in section.magnet_regions():
        M = np.asarray(r.M, dtype=float)
        try:
            c = r.polygon.centroid
            th = math.atan2(float(c.y), float(c.x))
        except Exception:
            continue
        t = np.array([-math.sin(th), math.cos(th), 0.0])
        out[r.name] = 1 if float(M @ t) >= 0.0 else -1
    return out


def _region_materials(section: MotorSection) -> Dict[str, Optional[str]]:
    """Region name -> the library material actually assigned to it."""
    m = dict(section.materials or {})
    out: Dict[str, Optional[str]] = {}
    for r in section.regions:
        if r.kind == "magnet":
            out[r.name] = m.get("magnet")
        elif r.name == "stator":
            out[r.name] = m.get("stator_core")
        elif r.name == "rotor":
            out[r.name] = m.get("rotor_core")
        elif r.name == "shaft":
            out[r.name] = m.get("shaft")
        else:
            out[r.name] = None
    return out


def mesh_counts(tm) -> dict:
    """The REAL size of the volume mesh, whatever the surface payload shows."""
    meta = dict(getattr(tm, "meta", {}) or {})
    reg = np.asarray(tm.cell_region)
    names = dict(tm.names)
    air_id = int(names.get("air", 0))
    per = {n: int((reg == i).sum()) for n, i in names.items()}
    return {
        "tets": int(tm.n_elements),
        "nodes": int(tm.n_vertices),
        "tets_solid": int((reg != air_id).sum()),
        "tets_air": int((reg == air_id).sum()),
        "tets_per_region": per,
        "n_tri_2d": meta.get("n_tri_2d"),
        "n_nodes_2d": meta.get("n_nodes_2d"),
        "axial_layers": meta.get("n_layers"),
        "z_levels_mm": meta.get("z_levels_mm"),
        "stack_half_mm": meta.get("stack_half_mm"),
        "r_box_mm": meta.get("r_box_mm"),
        "z_box_mm": meta.get("z_box_mm"),
        "sector_deg": meta.get("sector_deg"),
        "antiperiodic": meta.get("antiperiodic"),
    }


# --------------------------------------------------------------------------
# geometry (no mesh, no solve — the solid bodies as the CAD gave them)
# --------------------------------------------------------------------------

def _rings(poly) -> List[dict]:
    """A shapely polygon as ``[{outer, holes}]`` in mm.

    The nesting is kept rather than flattened: a stator with a slot pocket and
    a MultiPolygon of two disjoint pieces both come back as a flat ring list,
    and the browser cannot tell "this ring is a hole in that one" from "this
    ring is a second body".  Get that wrong and the tooth pockets render as
    solid metal.
    """
    from motor_ai_sim.simulation.static3d.motor_mesh import (_poly_parts,
                                                             _shapely_rings)

    def _pts(ring):
        return [[round(float(x), _ROUND), round(float(y), _ROUND)]
                for x, y in ring]

    out: List[dict] = []
    for part in _poly_parts(poly):
        rings = _shapely_rings(part)
        if not rings:
            continue
        out.append({"outer": _pts(rings[0]),
                    "holes": [_pts(r) for r in rings[1:]]})
    return out


def geometry_payload(section: MotorSection,
                     *,
                     h_ew_mm: Optional[float] = None) -> dict:
    """The sector's solid bodies as polygons + the extrusion that makes them 3D.

    This is the ONLY view in the tab that needs no mesh and no solve: it is the
    machine as ``load_motor_section`` read it out of the 2D pipeline, which is
    the same cross-section the 2D solver meshes.  Drawing it first is how the
    user checks that the 3D model is looking at their motor at all, before
    spending a minute on a solve to find out it was not.
    """
    stack_half = 0.5 * float(section.stack_mm)
    geo = dict(section.geo or {})
    if h_ew_mm is None:
        h_ew_mm = 0.5 * float(geo.get("tooth_width", 0.0)) + \
            0.5 * float(geo.get("wire_width", 0.0))

    regions = []
    mat_of = _region_materials(section)
    pol = magnet_polarity(section)
    for r in section.regions:
        M = np.asarray(r.M, dtype=float)
        mag = float(np.linalg.norm(M))
        try:
            c = r.polygon.centroid
            centroid = [round(float(c.x), _ROUND), round(float(c.y), _ROUND)]
        except Exception:
            centroid = None
        regions.append({
            "centroid_mm": centroid,
            "name": r.name,
            "kind": r.kind,
            "material": mat_of.get(r.name),
            "polarity": pol.get(r.name),
            "parts": _rings(r.polygon),
            "area_mm2": round(float(r.area_mm2), 6),
            "mu_r": float(r.mu_r),
            "stack_kf": float(r.stack_kf),
            "nonlinear": bool(r.bh_curve),
            "M_A_per_m": [float(v) for v in M],
            "M_dir_deg": (None if mag <= 0
                          else round(math.degrees(math.atan2(M[1], M[0])), 3)),
            "M_mag_A_per_m": mag,
        })

    coils = _coil_payload(section)
    return {
        "units": "mm",
        "regions": regions,
        "coils": coils,
        "extrusion": {
            "stack_mm": float(section.stack_mm),
            "z_lo_mm": -stack_half,
            "z_hi_mm": stack_half,
            "modelled_z_lo_mm": 0.0,
            "modelled_z_hi_mm": stack_half,
            "mirror_plane_z0": True,
            "end_winding_h_mm": float(h_ew_mm),
        },
        "sector": {
            "sector_deg": float(section.sector_deg),
            "n_sectors": int(section.n_sectors),
            "antiperiodic": bool(section.antiperiodic),
        },
        "machine": machine_summary(section),
    }


def _coil_payload(section: MotorSection) -> dict:
    """The winding: coil-side cross-sections plus the end-turn band.

    ``winding3d`` does NOT carry the coil as a swept solid — it carries a
    sampled scalar ``Psi`` whose level sets are the current streamlines, and an
    axial profile ``beta(z)`` that hands the current over to the end turn.  So
    what is drawable, honestly, is: the copper cross-sections the CAD gives
    (extruded over the stack), and the BAND the end turns occupy, whose height
    is the same ``h_ew`` the 2D copper-loss model has always been charging for.
    Drawing a plausible bent-wire bundle instead would be an illustration, not
    the model.
    """
    polys = section.polys_full or {}
    raw = list(polys.get("coils") or [])
    wedge = _sector_wedge(section)
    out = []
    for i, poly in enumerate(raw):
        try:
            clipped = poly if wedge is None else poly.intersection(wedge)
            if clipped.is_empty or clipped.area <= 1e-9:
                continue
            parts = _rings(clipped)
        except Exception:
            continue
        if not parts:
            continue
        out.append({"index": i, "parts": parts})
    geo = dict(section.geo or {})
    h_ew = 0.5 * float(geo.get("tooth_width", 0.0)) + \
        0.5 * float(geo.get("wire_width", 0.0))
    return {
        "sides": out,
        "n_sides_sector": len(out),
        "n_sides_full_ring": len(raw),
        "end_turn_band_mm": float(h_ew),
        "note": ("copper cross-sections CLIPPED TO THE SECTOR — the iron beside "
                 "them is clipped too, and a full ring of copper around half a "
                 "stator is the picture this avoids. Extruded over the stack; "
                 "the end turn is the band of height h_ew above each end, not a "
                 "bent bundle (the model does not have one)."),
    }


def _sector_wedge(section: MotorSection):
    """The sector as a shapely polygon, or None for a full-ring model.

    Built the same way ``motor_geometry`` clips the iron: a fan from the origin
    over ``sector_deg``, out past the outer boundary.  The copper has to be cut
    by the SAME wedge the iron was, or the two disagree on where the machine
    ends.
    """
    if int(section.n_sectors) <= 1:
        return None
    try:
        from shapely.geometry import Polygon as _P
    except Exception:                                     # pragma: no cover
        return None
    a = math.radians(float(section.sector_deg))
    r = 4.0 * float(section.r_stator_out_mm)
    n = max(int(math.degrees(a)) * 2, 8)
    pts = [(0.0, 0.0)] + [(r * math.cos(a * k / n), r * math.sin(a * k / n))
                          for k in range(n + 1)]
    return _P(pts)


def machine_summary(section: MotorSection) -> dict:
    """The machine in the same words the passport uses, so the two compare."""
    return {
        "num_slots": int(section.num_slots),
        "num_poles": int(section.num_poles),
        "pole_pairs": int(section.pole_pairs),
        "stator_od_mm": 2.0 * float(section.r_stator_out_mm),
        "stator_bore_mm": 2.0 * float(section.r_stator_in_mm),
        "rotor_od_mm": 2.0 * float(section.r_rotor_out_mm),
        "air_gap_mm": float(section.r_stator_in_mm - section.r_rotor_out_mm),
        "mid_gap_r_mm": float(section.mid_r_mm),
        "stack_mm": float(section.stack_mm),
        "Br_T": float(section.Br_T),
        "mu_rec": float(section.mu_rec),
        "materials": dict(section.materials or {}),
        "sector_deg": float(section.sector_deg),
        "n_sectors": int(section.n_sectors),
        "antiperiodic": bool(section.antiperiodic),
        "topology": "spoke PM (tangential magnetisation)",
    }


# --------------------------------------------------------------------------
# fields
# --------------------------------------------------------------------------

def field_arrays(sol, tm, section: MotorSection) -> Dict[str, np.ndarray]:
    """The per-element quantities the viewer can colour by.

    ``Bmag`` everywhere; ``demag`` only inside the magnets (H . M_hat, negative
    is demagnetising — the same definition ``end_effect.demag_exposure``
    integrates, so the map and the passport's numbers cannot disagree).  Air is
    deliberately present in the arrays and excluded at DRAW time, not here: a
    field view that filters the physics rather than the picture ends up quietly
    excluding solid regions too.
    """
    B = np.asarray(sol.B_elementwise())
    Bmag = np.linalg.norm(B, axis=0)
    H = (B - MU0 * np.asarray(sol.M_el)) / np.asarray(sol.mu_diag())

    demag = np.full(Bmag.shape, np.nan)
    for r in section.magnet_regions():
        el = tm.elements(r.name)
        if el.size == 0:
            continue
        m = np.asarray(r.M, dtype=float)
        mhat = m / max(float(np.linalg.norm(m)), 1e-300)
        demag[el] = (H[:, el] * mhat[:, None]).sum(axis=0)
    return {"B": B, "Bmag": Bmag, "H": H, "demag": demag}


def vector_samples(sol, tm, section: MotorSection, *,
                   cut_z_mm: Optional[float] = None,
                   cut_theta_deg: Optional[float] = None,
                   max_n: int = 2500,
                   solid_only: bool = True) -> dict:
    """A decimated set of B vectors at element centroids, mm and tesla.

    Decimated by an even stride over the surviving elements and LABELLED with
    how many were dropped — the arrow field is a sketch of direction, never a
    count of anything.
    """
    mesh = tm.mesh
    reg = np.asarray(tm.cell_region)
    air_id = int(dict(tm.names).get("air", 0))
    keep = cut_mask(mesh, cut_z_mm, cut_theta_deg)
    if solid_only:
        keep &= reg != air_id
    idx = np.flatnonzero(keep)
    total = int(idx.size)
    if total == 0:
        return {"points": [], "vectors": [], "shown": 0, "total": 0,
                "decimated": False}
    pick = _decimate(total, max_n)
    if pick is not None:
        idx = idx[pick]
    c = element_centroids(mesh)[:, idx] / MM
    B = np.asarray(sol.B_elementwise())[:, idx]
    return {
        "points": np.round(c.T.reshape(-1), _ROUND).tolist(),
        "vectors": np.round(B.T.reshape(-1), 6).tolist(),
        "shown": int(idx.size),
        "total": total,
        "decimated": bool(pick is not None),
        "units": {"points": "mm", "vectors": "T"},
    }


def percentile_range(values: np.ndarray, lo: float = 1.0, hi: float = 99.0
                     ) -> Tuple[float, float]:
    """Robust colour range: percentiles, not min/max.

    Linear iron puts |B| above 25 T in a single re-entrant corner of this
    machine — a genuine feature of the DISCRETE problem, not of the motor. Range
    on min/max and the whole picture is one colour with a bright dot. The 2D
    loss map settled this the same way, and the legend says which percentiles
    it used so the clipped tail is visible rather than hidden.
    """
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return 0.0, 1.0
    a, b = float(np.percentile(v, lo)), float(np.percentile(v, hi))
    if not (b > a):
        b = a + max(abs(a) * 1e-6, 1e-12)
    return a, b
