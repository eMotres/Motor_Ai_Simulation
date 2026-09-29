"""CYCLIC SYMMETRY — solving ONE pole sector of the rotor, as in Fusion 360.

Written 2026-09-09 for the user's request:

    "нагрузка на все зубы должна быть одинакова … так используй периодичность,
     как я во Fusion"

In Fusion he meshes a single pole sector of the rotor and ties its two cut
faces with a cyclic-symmetry boundary condition.  The wedge then behaves as if
the other ``n - 1`` sectors were there, every pole carries an identical load BY
CONSTRUCTION rather than by luck of the mesh, and the model is ``n`` times
smaller.  On the G2-L40 (28 magnets) that is a 28× smaller stiffness matrix.

WHAT THE CONSTRAINT IS
----------------------
Let ``theta = 2*pi/n`` and ``R = R(theta)``.  For every point ``x`` on the
leading cut face A there is a matching point ``R x`` on the trailing face B,
and the displacement field of the full rotor satisfies

    u(R x) = R u(x)                         (a VECTOR rotation, not u_B = u_A)

That is the whole model.  It is imposed as ``2`` bilateral Lagrange rows per
matched point, appended to the same bordered system that already removes the
rigid-body modes and carries a ``HeldBoundary`` (see ``contact.CyclicTies`` and
``contact.solve_contact``).

Three consequences the code has to respect, and does:

  * a TRANSLATION is not periodic — ``u_B - R u_A = (I - R) c`` is zero only for
    ``c = 0`` — so the ties have already removed the two translations and the
    rigid-body border must pin only the ROTATION, or the constraint set is rank
    deficient and the saddle matrix singular (``contact._rigid_basis``);
  * the ROTATION is periodic (``u = omega * z_hat x x`` gives ``u(Rx) = R u(x)``
    exactly), so it survives the ties and still has to be pinned;
  * a bilateral CONTACT row sitting on face B is implied exactly by its face-A
    twin plus the two ties (the normal rotates with the point, so
    ``(u_sB - u_rB).n_B == (u_sA - u_rA).n_A``).  Those rows are dropped —
    ``ContactSystem.pair_no_row`` — and nothing is released by dropping them.
    Unilateral pairs are penalty springs and not rows, so they are left alone.

WHY THE GEOMETRY HAS TO BE SNAPPED FIRST
----------------------------------------
The cross-section arrives as SAMPLED polylines: an arc of radius 73.35 mm is a
few hundred chords.  Clipping it with a wedge therefore lands the two cut faces
on different chords, and the same physical corner comes out at 73.350000 mm on
face A and 73.347295 mm on face B — measured on the G2-L40, 2.7 µm apart.  gmsh
will happily mesh that, and the "matching" nodes then miss each other by
microns, which is not a cyclic model at all.  ``sector_polys`` snaps every
face-B vertex onto ``R`` times its face-A twin (rank-matched by radius, refused
if the counts differ or the twin is further than ``SNAP_TOL_MM``), after which
gmsh's own periodic-curve mesher (``gmsh.model.mesh.setPeriodic`` with the
rotation as the affine transform) copies face A's 1-D node distribution onto
face B EXACTLY.  Measured on the G2-L40: ``max |R x_A - x_B|`` is 1.1e-15 m on
the conforming mesh and 4.8e-13 m on the split one the solve runs on.
``build_cyclic_ties`` re-checks that against ``MATCH_TOL_M`` and refuses rather
than tie nodes that do not actually match.

WHAT THE SECTOR REPORTS
-----------------------
The solve is a sector solve: the centrifugal body force, the air-gap traction
and every interface integral are ``1/n`` of the machine's.  ``rotor_stress``
scales the EXTENSIVE numbers back up by ``n`` before reporting them (masses,
transmitted torques, friction capacities, the bore reaction) so that a sector
answer and a full answer are read off the same axes; the intensive ones
(stresses, growths, open fractions, safety factors) are the same number in both
and are passed through untouched.  Which is which is stated at each site.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

#: How far a face-B vertex may be from ``R`` times its face-A twin and still be
#: snapped onto it, mm.  0.05 mm is the geometry weld tolerance the CadQuery
#: sanitiser already uses, and it is far below the 0.25 mm minimum element — a
#: bigger gap than that is not polyline sampling, it is a rotor that is not
#: periodic, and it is refused by name.
SNAP_TOL_MM = 0.05

#: …and how well the MESH nodes then have to match, metres.  Nothing but a
#: geometric identity is accepted: gmsh's periodic mesher reproduces face A's
#: node distribution on face B to machine precision (1e-15 m measured), so 1e-9
#: is four orders of magnitude of headroom and still catches a mesher that
#: silently ignored the request.
MATCH_TOL_M = 1e-9

#: How far off a ray a point may sit and still count as "on the cut face", in
#: metres for the mesh and millimetres for the geometry.  The cut faces are
#: exact straight lines through the origin in both, so this only has to survive
#: round-off.
RAY_TOL_M = 1e-9
RAY_TOL_MM = 1e-6

#: How far a turned magnet centroid may land from the original one and still
#: count as "the same magnet", as a fraction of the CLOSEST spacing between two
#: magnet centroids.  Measured on the G2-L40 catalog cross-section: turning its
#: 28 magnets by 360/28° moves the worst centroid 6.3 µm — the magnets are the
#: same solid rotated, but their outlines are SAMPLED polylines and the samples
#: do not land on the same places, so the centroid of the sampled shape wobbles
#: by microns.  0.05 of a 13.9 mm pole pitch is 0.7 mm: twenty times too tight
#: to confuse one pole with its neighbour, and a hundred times looser than the
#: sampling noise.
CENTROID_TOL_FRAC = 0.05

#: …and how much of the rotor CORE may fail to map onto itself under the same
#: turn, as a fraction of its area.  Measured on the G2-L40: 6.0e-4 for the
#: 28-fold turn of a core that is 28-fold periodic by construction — again the
#: sampling, this time of a 782-vertex outline.  3e-3 leaves 5x of headroom and
#: still catches a real asymmetry down to ~0.3 % of the core (a 3.4 mm hole on
#: that machine), which is the case this check exists for: a symmetric magnet
#: set inside an asymmetric core is a rotor whose poles are NOT identical.
CORE_SYMDIFF_FRAC = 3e-3


class NotPeriodic(ValueError):
    """This cross-section cannot be reduced to a sector, and why.

    A ValueError so the route turns it into a 422 that names the reason — the
    project's client-facing rule: never quietly solve something else.
    """


@dataclass(frozen=True)
class SectorPlan:
    """The wedge: how many sectors, where it is cut, and by what rotation."""
    #: number of identical sectors the full rotor is made of
    n_sectors: int
    #: the sector angle, rad = 2*pi/n_sectors
    angle_rad: float
    #: the LEADING cut face, rad (the trailing one is this + angle_rad)
    cut_angle_rad: float
    #: where the periodicity was read from — "magnets" or "num_poles"
    source: str = "magnets"

    @property
    def cut_angle_b_rad(self) -> float:
        return self.cut_angle_rad + self.angle_rad

    @property
    def mid_angle_rad(self) -> float:
        return self.cut_angle_rad + 0.5 * self.angle_rad

    @property
    def R(self) -> np.ndarray:
        c, s = math.cos(self.angle_rad), math.sin(self.angle_rad)
        return np.array([[c, -s], [s, c]], dtype=float)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "mode": "sector",
            "n_sectors": int(self.n_sectors),
            "angle_deg": math.degrees(self.angle_rad),
            "cut_angle_deg": math.degrees(self.cut_angle_rad),
            "cut_angle_b_deg": math.degrees(self.cut_angle_b_rad),
            "periodicity_from": self.source,
            "note": ("one pole sector solved with cyclic-symmetry ties; every "
                     "pole is identical by construction"),
        }


# ---------------------------------------------------------------------------
# 1. How periodic is this rotor?
# ---------------------------------------------------------------------------

def detect_periodicity(polys: dict, num_poles: Optional[int] = None
                       ) -> Tuple[int, str]:
    """(n_sectors, where it came from) — the rotor's ROTATIONAL periodicity.

    The magnets are the honest source: they are the features that make a rotor
    a rotor, they are the only ones whose count is unambiguous in the polygon
    payload, and a magnet set that is not periodic is exactly the case that must
    be refused rather than averaged.  ``n`` is the LARGEST rotation count whose
    turn maps the magnet CENTROID set onto itself — the largest, because the
    valid counts are closed under division (if turning by ``2*pi/n`` maps the
    set onto itself then so does turning by ``2*pi/d`` for every divisor ``d``),
    so the smallest useful sector is the biggest ``n``.

    The rotor iron is then checked too: a symmetric magnet set inside an
    asymmetric core (a balancing flat, one enlarged vent hole) is a rotor whose
    poles are NOT identical, and solving one of its sectors would answer a
    question about a machine that was not drawn.  The check is the symmetric
    difference of the core with its own turned copy, as a fraction of its area.

    Returns ``(1, ...)`` when nothing periodic was found; the caller decides
    whether that is a refusal (it is, for ``symmetry="sector"``).
    """
    mags = [mp for mp, _pol in (polys.get("magnets") or [])]
    if mags:
        cen = np.array([[float(g.centroid.x), float(g.centroid.y)]
                        for g in mags], dtype=float)
        cands = sorted((k for k in range(len(mags), 1, -1)
                        if len(mags) % k == 0), reverse=True)
        source = "magnets"
    else:
        # No magnets in the payload at all (a solid-iron or induction rotor, or
        # a caller that passed only the core).  Fall back to what the machine
        # SAYS its pole count is — the user's own number — and lean entirely on
        # the iron check below to confirm it (2026-09-09).
        cen = np.zeros((0, 2))
        n0 = int(num_poles or 0)
        if n0 < 2:
            raise NotPeriodic(
                "symmetry='sector' needs to know how many identical sectors "
                "the rotor has, and this cross-section carries no magnets to "
                "read it from — pass num_poles, or solve symmetry='full'")
        cands = [n0]
        source = "num_poles"

    core = polys.get("rotor")
    # The tolerance scales with the machine, not with an absolute guess: the
    # closest two magnet centroids are one pole pitch apart, and a fraction of
    # that can never confuse one pole with its neighbour however big or small
    # the rotor is.  See ``CENTROID_TOL_FRAC``.
    cen_tol = 1e-9
    if cen.shape[0] > 1:
        dd = np.hypot(cen[:, None, 0] - cen[None, :, 0],
                      cen[:, None, 1] - cen[None, :, 1])
        np.fill_diagonal(dd, np.inf)
        cen_tol = CENTROID_TOL_FRAC * float(dd.min())

    def _maps_onto_itself(n: int) -> bool:
        th = 2.0 * math.pi / n
        if cen.shape[0]:
            c, s = math.cos(th), math.sin(th)
            rot = cen @ np.array([[c, s], [-s, c]])      # (R @ cen.T).T
            # every turned centroid must land on an original one
            d = np.hypot(rot[:, None, 0] - cen[None, :, 0],
                         rot[:, None, 1] - cen[None, :, 1])
            if not bool((d.min(axis=1) < cen_tol).all()):
                return False
        if core is not None and not core.is_empty:
            from shapely import affinity
            turned = affinity.rotate(core, math.degrees(th), origin=(0.0, 0.0))
            try:
                bad = float(turned.symmetric_difference(core).area)
            except Exception:  # noqa: BLE001 - a topology error is a refusal
                return False
            if bad > CORE_SYMDIFF_FRAC * max(float(core.area), 1e-12):
                return False
        return True

    for n in cands:
        if _maps_onto_itself(n):
            return int(n), source
    return 1, source


# ---------------------------------------------------------------------------
# 2. The wedge
# ---------------------------------------------------------------------------

def plan_sector(polys: dict, num_poles: Optional[int] = None,
                n_sectors: Optional[int] = None) -> SectorPlan:
    """Decide the sector: how many, and WHERE the two cuts fall.

    The cuts must not pass through a magnet — a magnet halved by a cut face is
    a magnet whose two halves are tied to each other through the periodicity,
    which is correct algebra and a nightmare to read on a stress map, and which
    puts a contact interface on the cut where the ties and the interface rows
    fight over the same constraint.  So the wedge is turned so that a MAGNET
    CENTROID sits at its mid-angle: the cuts then fall in the iron half-way
    between two poles, which is the furthest they can be from any magnet.
    """
    n, source = ((int(n_sectors), "given") if n_sectors
                 else detect_periodicity(polys, num_poles))
    if n < 2:
        mags = polys.get("magnets") or []
        raise NotPeriodic(
            "this rotor is not rotationally periodic, so it has no sector to "
            f"solve: turning its {len(mags)} magnet(s) by any 2*pi/n does not "
            "map the set onto itself (or the core is not symmetric under the "
            "same turn). Solve symmetry='full'.")

    angle = 2.0 * math.pi / n
    mags = [mp for mp, _pol in (polys.get("magnets") or [])]
    if not mags:
        # No magnet to centre on: the cut can go anywhere, so it goes at 0.
        return SectorPlan(n_sectors=n, angle_rad=angle,
                          cut_angle_rad=-0.5 * angle, source=source)

    # Candidate mid-angles, best first: each magnet's own centre (the cuts then
    # fall as far from that magnet as the sector allows), then the gaps between
    # neighbouring magnets.  The first candidate whose two cuts cross no magnet
    # wins; a rotor where none does has no iron between its poles to cut
    # through, and is refused by name.
    ang = sorted(math.atan2(float(g.centroid.y), float(g.centroid.x))
                 for g in mags)
    cands = list(ang)
    cands += [0.5 * (ang[i] + ang[(i + 1) % len(ang)]
                     + (2.0 * math.pi if i == len(ang) - 1 else 0.0))
              for i in range(len(ang))]
    tried: List[str] = []
    for mid in cands:
        plan = SectorPlan(n_sectors=n, angle_rad=angle,
                          cut_angle_rad=mid - 0.5 * angle, source=source)
        hit = None
        for k, g in enumerate(mags):
            for a in (plan.cut_angle_rad, plan.cut_angle_b_rad):
                if _ray_crosses(g, a):
                    hit = (k, a)
                    break
            if hit:
                break
        if hit is None:
            return plan
        tried.append(f"{math.degrees(hit[1]):.2f}° through magnet {hit[0]}")
    raise NotPeriodic(
        f"no {n}-sector cut misses every magnet — tried {len(tried)} "
        f"placements ({tried[0]}, …). With the magnets this wide there is no "
        "iron between two poles to cut through; solve symmetry='full'.")


def _ray_crosses(geom, angle_rad: float, tol_mm: float = RAY_TOL_MM) -> bool:
    """Does the ray from the origin at ``angle_rad`` pass through ``geom``?"""
    from shapely.geometry import LineString

    x0, y0, x1, y1 = geom.bounds
    rmax = 1.5 * max(abs(x0), abs(x1), abs(y0), abs(y1), 1.0)
    ray = LineString([(0.0, 0.0),
                      (rmax * math.cos(angle_rad), rmax * math.sin(angle_rad))])
    # ``intersection`` and not ``intersects``: a magnet whose corner merely
    # touches the ray is not crossed by it, and refusing that would refuse a
    # perfectly solvable rotor.
    try:
        inter = ray.intersection(geom)
    except Exception:  # noqa: BLE001
        return True
    return (not inter.is_empty) and float(inter.length) > tol_mm


# ---------------------------------------------------------------------------
# 3. The sector geometry
# ---------------------------------------------------------------------------

def sector_polys(polys: dict, plan: SectorPlan) -> dict:
    """The rotor solids clipped to the wedge, with face B snapped onto face A.

    Returns a ``polys``-shaped dict the ordinary mesher understands.  Only the
    ROTOR solids are clipped — ``shaft``, ``rotor``, ``magnets``, ``sleeve``;
    everything else (the stator, the bands, ``sleeve_r_mm``) is carried through
    untouched because the structural solve never looks at it, and
    ``sleeve_r_mm`` in particular must stay the machine's radius so the sleeve
    eigenstrain and the OD-growth mask read the same numbers they always did.

    THE SNAP is the point of this function; see the module docstring.  Without
    it the two cut faces are congruent only to the polygon's sampling error
    (2.7 µm on the G2-L40) and no mesher can produce matching nodes.
    """
    from shapely.geometry import MultiPolygon

    wedge = _wedge(polys, plan)
    out = dict(polys)

    def _clip(g):
        if g is None or g.is_empty:
            return None
        r = g.intersection(wedge)
        if r.is_empty or float(r.area) <= 0.0:
            return None
        # A clip can leave a GeometryCollection (a stray line where the ray
        # grazes a corner); keep only the polygons, which are the solid.
        if r.geom_type == "GeometryCollection":
            polys_only = [p for p in r.geoms if p.geom_type == "Polygon"]
            if not polys_only:
                return None
            r = polys_only[0] if len(polys_only) == 1 else MultiPolygon(polys_only)
        return _snap_cut_faces(r, plan)

    for name in ("shaft", "rotor", "sleeve"):
        out[name] = _clip(polys.get(name))
    mags: List[Tuple[Any, Any]] = []
    for mp, pol in (polys.get("magnets") or []):
        c = _clip(mp)
        if c is not None:
            mags.append((c, pol))
    out["magnets"] = mags
    if out.get("rotor") is None and not mags and out.get("shaft") is None:
        raise NotPeriodic(
            "the sector wedge came out empty — no rotor solid falls between "
            f"{math.degrees(plan.cut_angle_rad):.3f}° and "
            f"{math.degrees(plan.cut_angle_b_rad):.3f}°")
    return out


def _wedge(polys: dict, plan: SectorPlan):
    """A fan polygon covering the wedge out past every solid.

    Built as an apex plus an ARC of straight chords rather than two long rays,
    because a single triangle's outer edge cuts the corner off any sector wider
    than the chord's sagitta allows.  The radius is 3x the outermost point and
    the chords are at most 20°, so the fan's boundary sits at least 2.9 x r_max
    out — nothing of the rotor is ever clipped by it.
    """
    from shapely.geometry import Polygon

    rmax = 1.0
    for name in ("shaft", "rotor", "sleeve"):
        g = polys.get(name)
        if g is not None and not g.is_empty:
            x0, y0, x1, y1 = g.bounds
            rmax = max(rmax, abs(x0), abs(x1), abs(y0), abs(y1))
    for mp, _pol in (polys.get("magnets") or []):
        x0, y0, x1, y1 = mp.bounds
        rmax = max(rmax, abs(x0), abs(x1), abs(y0), abs(y1))
    big = 3.0 * rmax * math.sqrt(2.0)
    a0, a1 = plan.cut_angle_rad, plan.cut_angle_b_rad
    k = max(2, int(math.ceil((a1 - a0) / math.radians(20.0))))
    pts = [(0.0, 0.0)]
    for i in range(k + 1):
        t = a0 + (a1 - a0) * i / k
        pts.append((big * math.cos(t), big * math.sin(t)))
    return Polygon(pts)


def _snap_cut_faces(geom, plan: SectorPlan):
    """Move every face-B vertex onto ``R`` times its face-A twin.

    The twins are matched by RADIUS along the ray, which is the only invariant
    a rotation leaves: the two faces are the same radial line seen from two
    angles, so the k-th material boundary out from the axis on face A is the
    k-th on face B.  A count mismatch, or a twin further than ``SNAP_TOL_MM``,
    means the wedge is not cutting congruent faces and is refused by name — the
    alternative is a mesh whose "periodic" nodes are microns apart, which reads
    as a converged answer and is not one.
    """
    from shapely.geometry import MultiPolygon, Polygon

    a0 = plan.cut_angle_rad
    th = plan.angle_rad
    ca, sa = math.cos(a0), math.sin(a0)
    cb, sb = math.cos(a0 + th), math.sin(a0 + th)
    ct, st = math.cos(th), math.sin(th)

    geoms = list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]
    rings: List[List[Tuple[float, float]]] = []
    shape: List[int] = []                    # how many interiors per polygon
    for g in geoms:
        rings.append([tuple(map(float, p)) for p in g.exterior.coords])
        shape.append(len(g.interiors))
        for r in g.interiors:
            rings.append([tuple(map(float, p)) for p in r.coords])

    a_r: List[float] = []
    b_at: List[Tuple[int, int, float]] = []
    for ri, pts in enumerate(rings):
        for i, (x, y) in enumerate(pts[:-1]):     # the ring repeats its first
            r = math.hypot(x, y)
            if r < RAY_TOL_MM:
                continue
            if abs(x * sa - y * ca) < RAY_TOL_MM and (x * ca + y * sa) > 0:
                a_r.append(r)
            elif abs(x * sb - y * cb) < RAY_TOL_MM and (x * cb + y * sb) > 0:
                b_at.append((ri, i, r))

    ar = sorted(set(round(v, 9) for v in a_r))
    bs = sorted(b_at, key=lambda t: t[2])
    if len(ar) != len(bs):
        raise NotPeriodic(
            f"the two cut faces of the {plan.n_sectors}-sector wedge are not "
            f"congruent: face A has {len(ar)} material boundaries along the "
            f"ray and face B has {len(bs)}. That is a rotor whose poles are "
            "not identical — solve symmetry='full'.")
    for (ri, i, rb), ra in zip(bs, ar):
        if abs(ra - rb) > SNAP_TOL_MM:
            raise NotPeriodic(
                f"the {plan.n_sectors}-sector cut faces disagree by "
                f"{abs(ra - rb):.4f} mm at radius {ra:.3f} mm — more than the "
                f"{SNAP_TOL_MM} mm sampling tolerance, so this is a real "
                "asymmetry and not a polyline artefact. Solve symmetry='full'.")
        x, y = ra * ca, ra * sa
        nx, ny = ct * x - st * y, st * x + ct * y
        rings[ri][i] = (nx, ny)
        if i == 0:
            rings[ri][-1] = (nx, ny)

    out: List[Any] = []
    k = 0
    for j, g in enumerate(geoms):
        ext = rings[k]
        k += 1
        ints = []
        for _ in range(shape[j]):
            ints.append(rings[k])
            k += 1
        out.append(Polygon(ext, ints))
    return out[0] if len(out) == 1 else MultiPolygon(out)


# ---------------------------------------------------------------------------
# 4. The ties, on the mesh that was built
# ---------------------------------------------------------------------------

def build_cyclic_ties(basis, mesh, part_tri: Optional[np.ndarray],
                      plan: SectorPlan):
    """``CyclicTies`` for the SPLIT mesh, matched dof by dof.

    Matched by POSITION and by PART, and by nothing else.  Position alone is not
    enough: the interface split gave the sleeve and the rotor their own copy of
    the node where their boundary meets a cut face, and the two copies sit at
    exactly the same point — tying face B's sleeve copy to face A's rotor copy
    would weld two parts together through the periodicity.  After the split
    every dof belongs to exactly one part, so the part tag disambiguates them.

    ``part_tri=None`` says the mesh is the CONFORMING one (nothing split yet),
    where every point is one node and the position alone is unique — that is
    the path the thermal free-growth measurement takes, before the contact
    system exists.

    Both the nodal dofs (at vertices) and, for P2, the facet dofs (at edge
    midpoints) are tied: a quadratic trace tied only at its ends is not tied,
    and the cut faces would bulge apart between their nodes.

    The match is verified to ``MATCH_TOL_M`` and refused otherwise — the whole
    method rests on the two faces carrying the SAME nodes, and a mesher that
    quietly ignored the periodic request must not produce a "cyclic" answer.
    """
    from motor_ai_sim.simulation.mechanical.contact import CyclicTies

    ndof = int(basis.N)
    px, py = np.asarray(basis.doflocs[0]), np.asarray(basis.doflocs[1])

    # ── which part owns every dof ───────────────────────────────────────────
    part_of = np.zeros(ndof, dtype=np.int32)
    if part_tri is not None:
        ed = np.asarray(basis.element_dofs)          # (n_local_dofs, n_elem)
        for e in range(ed.shape[0]):
            part_of[ed[e]] = np.asarray(part_tri, dtype=np.int32)

    a0 = plan.cut_angle_rad
    ca, sa = math.cos(a0), math.sin(a0)
    cb, sb = math.cos(a0 + plan.angle_rad), math.sin(a0 + plan.angle_rad)
    R = plan.R

    r = np.hypot(px, py)
    on_a = (np.abs(px * sa - py * ca) < RAY_TOL_M) & ((px * ca + py * sa) > 0) \
        & (r > RAY_TOL_M)
    on_b = (np.abs(px * sb - py * cb) < RAY_TOL_M) & ((px * cb + py * sb) > 0) \
        & (r > RAY_TOL_M)
    ia = np.nonzero(on_a)[0]
    ib = np.nonzero(on_b)[0]
    if ia.size == 0 or ib.size == 0:
        raise NotPeriodic(
            "the sector mesh has no nodes on one of its cut faces "
            f"(A: {ia.size} dofs, B: {ib.size}) — the wedge and the mesh "
            "disagree about where the cuts are")
    if ia.size != ib.size:
        raise NotPeriodic(
            f"the sector mesh is not periodic: {ia.size} dofs on cut face A "
            f"against {ib.size} on face B. gmsh did not copy the face-A node "
            "distribution onto face B (Mesh.setPeriodic).")

    # x-dofs only: the ElementVector interleaves (x, y) at the same point, so
    # one entry per POINT is matched and the y-dof follows from +1.
    xa = ia[ia % 2 == 0]
    xb = ib[ib % 2 == 0]
    # rotate every face-A point and look for its face-B twin, part by part
    pa = np.stack([px[xa], py[xa]], axis=1) @ R.T
    pb = np.stack([px[xb], py[xb]], axis=1)
    dofs_a: List[Tuple[int, int]] = []
    dofs_b: List[Tuple[int, int]] = []
    worst = 0.0
    used = np.zeros(xb.size, dtype=bool)
    for pid in np.unique(part_of[xa]):
        sa_ = np.nonzero(part_of[xa] == pid)[0]
        sb_ = np.nonzero(part_of[xb] == pid)[0]
        if sa_.size != sb_.size:
            from motor_ai_sim.simulation.mechanical.contact import PART_NAMES
            raise NotPeriodic(
                f"cut face A carries {sa_.size} {PART_NAMES.get(int(pid), pid)} "
                f"nodes and face B carries {sb_.size} — the sector mesh is not "
                "periodic on that part")
        # nearest twin, one to one
        d = np.hypot(pa[sa_][:, None, 0] - pb[sb_][None, :, 0],
                     pa[sa_][:, None, 1] - pb[sb_][None, :, 1])
        j = np.argmin(d, axis=1)
        dmin = d[np.arange(sa_.size), j]
        worst = max(worst, float(dmin.max()) if dmin.size else 0.0)
        if dmin.size and float(dmin.max()) > MATCH_TOL_M:
            raise NotPeriodic(
                "the sector mesh nodes do not match across the cut: the worst "
                f"|R x_A - x_B| is {float(dmin.max()) * 1e6:.3f} µm, against a "
                f"{MATCH_TOL_M * 1e6:.6f} µm tolerance. A cyclic model needs "
                "the two faces to carry the same nodes.")
        if np.unique(j).size != j.size:
            raise NotPeriodic(
                "two nodes of cut face A matched the same node of face B — "
                "the sector mesh is degenerate at the cut")
        used[sb_[j]] = True
        for k, jj in zip(sa_, j):
            dofs_a.append((int(xa[k]), int(xa[k]) + 1))
            dofs_b.append((int(xb[sb_[jj]]), int(xb[sb_[jj]]) + 1))
    if not used.all():
        raise NotPeriodic(
            f"{int((~used).sum())} node(s) of cut face B were left untied — "
            "the two faces do not carry the same parts")

    da = np.asarray(dofs_a, dtype=np.int64)
    db = np.asarray(dofs_b, dtype=np.int64)
    # the VERTICES the rows touch, for the rigid-body border
    nodal = np.asarray(basis.nodal_dofs)             # (2, n_vertices)
    vmap = -np.ones(ndof, dtype=np.int64)
    vmap[nodal[0]] = np.arange(nodal.shape[1])
    verts = np.unique(np.concatenate([vmap[da[:, 0]], vmap[db[:, 0]]]))
    verts = verts[verts >= 0]
    return CyclicTies(dofs_a=da, dofs_b=db, R=R, vertices=verts,
                      n_sectors=plan.n_sectors, angle_rad=plan.angle_rad,
                      match_error_m=float(worst))


def cut_face_pairs(cs, plan: SectorPlan) -> np.ndarray:
    """Contact pairs sitting on the TRAILING cut face — their rows are dropped.

    See ``ContactSystem.pair_no_row``: on face B a bilateral row is implied
    exactly by its face-A twin plus the two ties, and a redundant row makes the
    saddle matrix singular.  The pair is still there, still reported, and still
    a penalty spring if it is unilateral — only its Lagrange row goes.
    """
    pos = np.asarray(cs.pair_pos, dtype=float)
    if pos.size == 0:
        return np.zeros(0, dtype=bool)
    b = plan.cut_angle_b_rad
    cb, sb = math.cos(b), math.sin(b)
    r = np.hypot(pos[:, 0], pos[:, 1])
    return ((np.abs(pos[:, 0] * sb - pos[:, 1] * cb) < RAY_TOL_M)
            & ((pos[:, 0] * cb + pos[:, 1] * sb) > 0) & (r > RAY_TOL_M))


def drop_cut_face_hold(hold, plan: SectorPlan):
    """The same idea for the ``HeldBoundary``: drop its TRAILING-face rows.

    The bore is held tangentially at every node of its arc, and the arc's two
    ends sit on the two cut faces.  ``u_B . t_B = 0`` is implied by
    ``u_A . t_A = 0`` and the tie (``t_B = R t_A``, so
    ``(R u_A).(R t_A) = u_A . t_A``), so keeping both is a rank-deficient
    constraint set.  Returns the hold with the face-B rows removed, or None if
    that empties it.
    """
    from motor_ai_sim.simulation.mechanical.contact import HeldBoundary

    if hold is None or hold.n_rows == 0:
        return hold
    b = plan.cut_angle_b_rad
    cb, sb = math.cos(b), math.sin(b)
    # The row positions are recoverable from arm + dirs: dirs = (-y, x) / r.
    y = -hold.dirs[:, 0] * hold.arm
    x = hold.dirs[:, 1] * hold.arm
    on_b = (np.abs(x * sb - y * cb) < RAY_TOL_M) & ((x * cb + y * sb) > 0) \
        & (hold.arm > RAY_TOL_M)
    if not on_b.any():
        return hold
    keep = ~on_b
    if not keep.any():
        return None
    return HeldBoundary(dofs=hold.dofs[keep], dirs=hold.dirs[keep],
                        arm=hold.arm[keep], vertices=hold.vertices,
                        label=hold.label)


# ---------------------------------------------------------------------------
# 5. Putting the rotor back together for the maps
# ---------------------------------------------------------------------------

def replicate_vertices(v_mm: np.ndarray, n: int) -> np.ndarray:
    """The sector's vertices, turned into all ``n`` copies (mm, (n*nv, 2))."""
    v = np.asarray(v_mm, dtype=float)
    out = np.empty((n * v.shape[0], 2), dtype=float)
    for k in range(n):
        th = 2.0 * math.pi * k / n
        c, s = math.cos(th), math.sin(th)
        out[k * v.shape[0]:(k + 1) * v.shape[0], 0] = c * v[:, 0] - s * v[:, 1]
        out[k * v.shape[0]:(k + 1) * v.shape[0], 1] = s * v[:, 0] + c * v[:, 1]
    return out


def replicate_triangles(t: np.ndarray, nv: int, n: int) -> np.ndarray:
    t = np.asarray(t)
    return np.concatenate([t + k * nv for k in range(n)], axis=0)


def replicate_vectors(u: np.ndarray, n: int) -> np.ndarray:
    """A per-node VECTOR field replicated: the copy is turned with the sector.

    This is the one array that is not simply tiled.  A displacement is a vector,
    so sector k's is ``R(k*theta)`` times the sector's own — the same rotation
    the tie imposes on the cut faces, applied to the whole copy.
    """
    return replicate_vertices(u, n)


def replicate_scalars(a: Sequence[float], n: int) -> List[float]:
    """A per-element SCALAR field replicated: invariant, so simply tiled.

    von Mises, hoop, radial, principal and the safety factor are all invariants
    of the stress tensor under a rotation of both the tensor and the frame, so
    every sector reads the same number.  That is not an approximation — it IS
    the cyclic-symmetry statement, and it is what makes "every pole carries the
    same load" true by construction rather than by measurement.
    """
    return list(a) * int(n)
