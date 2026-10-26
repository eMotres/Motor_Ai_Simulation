"""The radial MAGNETIC PULL of the air-gap field on the rotor — a real load.

Written 2026-10-26 for the owner's decision: the pull of the air-gap field on
the rotor had been faked in a study by multiplying the rotor-iron density by
5.31 through a made-up material card ("20SW1200_pull_k5.31", p ~ 0.70 MPa).
That turns a SURFACE traction on the pole tops into a BODY force spread over
the whole pole, which loads the wrong place (see below), and the answer could
not be saved as the machine's.  This module makes the pull a load of the
mechanical solve in its own right.

PHYSICS
-------
The force the field exerts on everything inside a closed surface drawn in
non-magnetic, current-free material (air, the air gap, a carbon sleeve) is the
surface integral of the Maxwell stress tensor

    T_ij = (B_i B_j - 1/2 |B|^2 delta_ij) / mu0 ,    t = T . n

so on the rotor's gap-facing surface, with outward normal ``n`` and the field
taken just OUTSIDE the surface (in the air or the sleeve),

    t_n = (B_n^2 - B_t^2) / (2 mu0)     (outward, toward the stator)
    t_t =  B_n B_t / mu0                (tangential — this is the torque).

WHAT IS APPLIED — the radial component only (the tangential decision)
--------------------------------------------------------------------
The applied traction is the RADIAL component of the Maxwell traction,
``t_r = e_r . (T . n)``, along ``e_r``, on the rotor iron and magnet faces
the air gap SEES (``gap_facets`` + ``radial_visibility``: pole tops, chamfers,
notch floors and exposed magnet faces; never a pocket wall inside the rotor,
never the sleeve — a carbon band carries no magnetic force, what it feels is
passed to it by the parts under it).  On an OUTER rotor the gap is inside it
and the pull points inward (``gap_side``).  The tangential part is DROPPED,
for two reasons:

  * the net torque already has an authority: ``loads=torque|both`` applies the
    electromagnetic torque as the uniform air-gap shear of ``torque_load``, and
    its value is the Simulation tab's energy / flux-linkage torque.  Adding the
    field's own tangential traction on top would apply the torque twice;
  * the project's torque methodology rejects the Maxwell-stress torque — on a
    sampled air-gap field it over-reads (memory: "Maxwell band over-reads
    ~37 %").  The radial pull does not have that problem: it is a sum of
    squares, dominated by B_n, and it is checked below against an independent
    annulus integral of the same field.

A radial force has no moment about the axis, so the pull adds EXACTLY zero
torque and nothing is double counted.  What is lost is the non-torque part of
the tangential traction (the per-pole asymmetry of a pole top), which
integrates to zero over a symmetric pole; the field's net tangential moment is
still reported (``maxwell_torque_nm``) as a diagnostic beside the applied one.

The magnet–iron magnetic forces INSIDE the rotor (a magnet attracted onto its
pocket walls) are an internal, self-equilibrated pair and are not modelled —
only the field's pull on the rotor as a whole, acting where it acts: the gap
surface.

SOURCES
-------
``em``      the duty's own stored electromagnetic field (``duty_fields`` kind
            ``em``): the mesh, its domain tags and B per element.  Runs stored
            since 2026-10-26 carry ``bx_per_tri``/``by_per_tri`` (the solver's
            element-mean B); older records carry only |B| and A_z per node, and
            B is then rebuilt as curl A on the P1 vertex values (measured on the
            Ø200 L155 record: 0.7-0.9 % median from the stored |B| in the gap
            and the sleeve) and labelled so.
``uniform`` an ESTIMATE: a uniform outward pressure ``p`` (``pull_mpa``) or
            ``p = B_gap^2 / (2 mu0)`` from a stated gap flux density
            (``pull_b_gap_t``), applied as the radial component of ``p n``.

WHICH INSTANT
-------------
A duty's EM record holds ONE instant — the transient's last time step, in the
rotor frame (the sliding-band solver keeps the rotor mesh at its drawn angle).
In synchronous steady state the rotor-frame field is stationary apart from the
slot ripple, and at one instant the rotor's poles sit at DIFFERENT positions
relative to the slots, so the poles of that one instant sample the ripple the
rotor sweeps through in time.  The pull is therefore taken as an ENVELOPE over
the poles: the field over every pole is evaluated on the same pole geometry,
the pole with the largest total radial pull is chosen, and ITS distribution is
applied to every pole.  That is conservative, it is identical in a full and a
sector model (the cyclic ties need every pole loaded alike), and the spread
between the poles is reported so the size of the envelope is visible.

CHECK
-----
In a non-magnetic, current-free annulus ``div T = 0``, which in polar form with
``sigma_tt = -sigma_rr`` gives ``d/dr  integral(r^2 sigma_rr dtheta) = 0`` over
any period of the field.  So the total radial pull on the circle at the rotor
radius ``r_o`` is ``K / r_o`` with ``K = integral(r^2 sigma_rr dtheta)`` read
anywhere in the gap; it is evaluated as an area integral over the EM mesh's
gap elements (``2 pi sum(sigma_rr r A) / (sum(A/r) r_o)``) — a different
surface, different elements and no point location — and the surface sampling's
own pole-mean total is compared with it.  The sum of RADIAL COMPONENTS is not
a conserved force (it falls as 1/r across the gap), which is why the check is
taken at the rotor radius and not quoted as "the force in the gap".

Units: the mechanical mesh is in metres, the EM record in millimetres; every
pressure is Pa internally and MPa in the report.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field as dc_field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

MU0 = 4e-7 * math.pi

#: EM domain tags (``simulation.sb_domains``) that are magnetically AIR — no
#: magnetisation, no current, mu = mu0: air, the air gap, the motion band and
#: the carbon sleeve.  The Maxwell stress is only a force on what it encloses
#: when it is read in such a region.
NONMAGNETIC_TAGS: Tuple[int, ...] = (0, 3, 7, 11)
#: rotor iron and the magnets (100 + i) — the rotor's own solids on the EM mesh
EM_ROTOR_TAG = 5
EM_MAGNET_TAG_RANGE = (100, 200)
EM_STATOR_TAG = 1

#: Where the field is read: this far OUTSIDE the surface point, along its
#: outward normal, mm.  The first offset whose point lands in a non-magnetic EM
#: element is used.  The two meshes sample the same arcs with different chords
#: (sag ~ s^2 / 8r: 2 µm for a 1 mm chord at r = 62 mm), so the smallest offset
#: is a few times that, and the largest stays well inside any practical gap.
SAMPLE_OFFSETS_MM: Tuple[float, ...] = (0.01, 0.03, 0.08, 0.2)

#: Share of the loaded surface (by length) whose field could not be read before
#: the EM field is refused as "not this rotor".
MAX_UNMATCHED_FRAC = 0.05

#: How far the surface-sampled pull may sit from the annulus integral before
#: the check is reported as failed.  The two are different surfaces read from
#: different elements: the surface leaves out radial flanks (``SIDE_WALL_COS``)
#: and reads element-mean B a sample offset away from the iron, so a few per
#: cent apart is the expected agreement (Ø200 L155 record: 0.95), and this is
#: a diagnostic tolerance, not a refusal.
CHECK_TOL = 0.10

#: Bounds on the explicit inputs, so a typo cannot solve an impossible machine:
#: 5 MPa is B = 3.5 T in the gap — past any iron's saturation.
MAX_PULL_MPA = 5.0
MAX_B_GAP_T = 3.5

PULL_MODES: Tuple[str, ...] = ("none", "em", "uniform")


class PullFieldError(ValueError):
    """The EM field cannot be used as this rotor's pull, and why (-> 422)."""


# ---------------------------------------------------------------------------
# The stored EM field
# ---------------------------------------------------------------------------

def _as_2n(a: np.ndarray, rows: int) -> np.ndarray:
    a = np.asarray(a)
    if a.ndim != 2:
        raise PullFieldError("the EM record's mesh arrays are not 2-D")
    if a.shape[0] == rows and a.shape[1] != rows:
        return a
    if a.shape[1] == rows:
        return a.T
    return a


@dataclass
class EmField:
    """One stored instant of the electromagnetic field, ready to be sampled."""
    p: np.ndarray                 # (2, nv) metres
    t: np.ndarray                 # (3, ne)
    tags: np.ndarray              # (ne,)
    bx: np.ndarray                # (ne,) T, element-mean
    by: np.ndarray                # (ne,) T
    b_source: str                 # how B was obtained (stored / curl A)
    sector_rad: float = 2.0 * math.pi
    theta0: float = 0.0
    meta: Dict[str, Any] = dc_field(default_factory=dict)
    _tree: Any = None
    _t0: Optional[np.ndarray] = None
    _jinv: Optional[np.ndarray] = None
    _cen: Optional[np.ndarray] = None

    # -- geometry helpers ---------------------------------------------------
    def _prepare(self) -> None:
        if self._tree is not None:
            return
        from scipy.spatial import cKDTree
        P = self.p
        v0, v1, v2 = P[:, self.t[0]], P[:, self.t[1]], P[:, self.t[2]]
        J = np.stack([np.stack([v1[0] - v0[0], v2[0] - v0[0]], axis=-1),
                      np.stack([v1[1] - v0[1], v2[1] - v0[1]], axis=-1)],
                     axis=1)                                  # (ne, 2, 2)
        det = J[:, 0, 0] * J[:, 1, 1] - J[:, 0, 1] * J[:, 1, 0]
        det = np.where(np.abs(det) < 1e-30, 1e-30, det)
        jinv = np.empty_like(J)
        jinv[:, 0, 0] = J[:, 1, 1] / det
        jinv[:, 0, 1] = -J[:, 0, 1] / det
        jinv[:, 1, 0] = -J[:, 1, 0] / det
        jinv[:, 1, 1] = J[:, 0, 0] / det
        self._t0 = v0.T.copy()
        self._jinv = jinv
        self._cen = ((v0 + v1 + v2) / 3.0).T.copy()
        self._tree = cKDTree(self._cen)

    @property
    def centroids(self) -> np.ndarray:
        self._prepare()
        return self._cen

    @property
    def areas(self) -> np.ndarray:
        P = self.p
        v0, v1, v2 = P[:, self.t[0]], P[:, self.t[1]], P[:, self.t[2]]
        return 0.5 * np.abs((v1[0] - v0[0]) * (v2[1] - v0[1])
                            - (v2[0] - v0[0]) * (v1[1] - v0[1]))

    def locate(self, xy: np.ndarray, k: int = 16) -> np.ndarray:
        """Element containing each point of ``xy`` (n, 2), or -1."""
        self._prepare()
        xy = np.asarray(xy, dtype=float).reshape(-1, 2)
        if xy.shape[0] == 0:
            return np.zeros(0, dtype=np.int64)
        ne = self.t.shape[1]
        kk = int(min(k, ne))
        _d, cand = self._tree.query(xy, kk)
        cand = np.asarray(cand).reshape(xy.shape[0], kk)
        d = xy[:, None, :] - self._t0[cand]                    # (n, k, 2)
        lam = np.einsum("nkij,nkj->nki", self._jinv[cand], d)  # (n, k, 2)
        # barycentric slack: the record's vertices are float32 mm (~4e-6 mm
        # of round-off at r = 60 mm), so a point ON an edge must not fall
        # between the two elements that share it
        eps = 1e-6
        inside = ((lam[..., 0] >= -eps) & (lam[..., 1] >= -eps)
                  & (1.0 - lam[..., 0] - lam[..., 1] >= -eps))
        hit = inside.any(axis=1)
        first = np.argmax(inside, axis=1)
        out = np.where(hit, cand[np.arange(xy.shape[0]), first], -1)
        return out.astype(np.int64)

    def sample_b(self, xy: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(B (n, 2) in the frame of ``xy``, element (n,), tag (n,)).

        Points outside the stored sector are turned into it by whole sector
        angles, and the B found there is turned back.  The sign flip of an
        anti-periodic model is irrelevant: everything read off B here is
        quadratic in it.
        """
        xy = np.asarray(xy, dtype=float).reshape(-1, 2)
        if self.sector_rad < 2.0 * math.pi - 1e-9:
            th = np.arctan2(xy[:, 1], xy[:, 0])
            kq = np.floor((th - self.theta0) / self.sector_rad + 1e-12)
            ang = -kq * self.sector_rad
            xs = _rot(xy, ang)
        else:
            ang = np.zeros(xy.shape[0])
            xs = xy
        el = self.locate(xs)
        if self.sector_rad < 2.0 * math.pi - 1e-9 and (el < 0).any():
            # a point ON the model's cut ray can round to the wrong side of
            # it: try its image one sector either way
            for shift in (self.sector_rad, -self.sector_rad):
                miss = el < 0
                if not miss.any():
                    break
                a2 = ang[miss] + shift
                e2 = self.locate(_rot(xy[miss], a2))
                got = e2 >= 0
                idx = np.nonzero(miss)[0][got]
                el[idx] = e2[got]
                ang[idx] = a2[got]
        ok = el >= 0
        b = np.full((xy.shape[0], 2), np.nan)
        tag = np.full(xy.shape[0], -1, dtype=np.int64)
        e = el[ok]
        bxs, bys = self.bx[e], self.by[e]
        # turn the field back by +k*sector (the inverse of the point's turn)
        c, s = np.cos(-ang[ok]), np.sin(-ang[ok])
        b[ok, 0] = c * bxs - s * bys
        b[ok, 1] = s * bxs + c * bys
        tag[ok] = self.tags[e]
        return b, el, tag

    def rotor_radius_m(self) -> Optional[float]:
        """Largest vertex radius of the EM rotor's own solids (iron, magnets)."""
        m = (self.tags == EM_ROTOR_TAG) | ((self.tags >= EM_MAGNET_TAG_RANGE[0])
                                           & (self.tags < EM_MAGNET_TAG_RANGE[1]))
        if not m.any():
            return None
        v = np.unique(self.t[:, m])
        return float(np.hypot(self.p[0, v], self.p[1, v]).max())

    def stator_bore_m(self) -> Optional[float]:
        """Smallest stator-iron vertex radius (the bore of an inner rotor's
        stator)."""
        m = self.tags == EM_STATOR_TAG
        if not m.any():
            return None
        v = np.unique(self.t[:, m])
        return float(np.hypot(self.p[0, v], self.p[1, v]).min())

    def stator_od_m(self) -> Optional[float]:
        """Largest stator-iron vertex radius (what an OUTER rotor faces)."""
        m = self.tags == EM_STATOR_TAG
        if not m.any():
            return None
        v = np.unique(self.t[:, m])
        return float(np.hypot(self.p[0, v], self.p[1, v]).max())


def _sector_of(p: np.ndarray) -> Tuple[float, float]:
    """(sector angle, leading angle) of an EM mesh, read off its vertices.

    The largest empty angular gap between vertices is the part of the circle
    the model does not cover; under 90 degrees means a 360-degree model (a
    sector model leaves out at least half the circle).
    The sector is then snapped to 2*pi/k — the EM solver only ever solves an
    integer fraction of the machine — and refused if it is not one.
    """
    r = np.hypot(p[0], p[1])
    th = np.sort(np.arctan2(p[1][r > 1e-9], p[0][r > 1e-9]))
    if th.size < 3:
        raise PullFieldError("the EM record has no mesh to read the field from")
    gaps = np.diff(np.concatenate([th, [th[0] + 2.0 * math.pi]]))
    i = int(np.argmax(gaps))
    # a sector model leaves out at least half the circle (it is 1/k of it,
    # k >= 2); a full model's widest vertex gap is a few mesh angles
    if gaps[i] < 0.5 * math.pi:
        return 2.0 * math.pi, -math.pi
    span = 2.0 * math.pi - float(gaps[i])
    theta0 = float(th[(i + 1) % th.size])
    k = max(1, int(round(2.0 * math.pi / span)))
    sector = 2.0 * math.pi / k
    if abs(sector - span) > math.radians(0.5):
        raise PullFieldError(
            f"the EM record covers {math.degrees(span):.2f}° of the machine, "
            "which is not an integer fraction of 360° — its periodicity cannot "
            "be read, so the field cannot be extended to the whole rotor")
    return sector, theta0


def em_field_from_record(rec: Dict[str, Any]) -> EmField:
    """An ``EmField`` from a ``duty_fields.load(..., "em")`` answer.

    Uses the stored element B (``bx_per_tri``/``by_per_tri``) when the run
    stored it; otherwise rebuilds B = curl A_z from ``a_z_per_node`` on the P1
    vertex values (records written before 2026-10-26).  Refuses a record with
    neither — |B| alone has no direction, and the Maxwell stress needs it.
    """
    if not isinstance(rec, dict):
        raise PullFieldError("no electromagnetic field is stored for this duty")
    if rec.get("vertices") is None or rec.get("triangles") is None:
        raise PullFieldError("the stored EM field has no mesh")
    p = _as_2n(np.asarray(rec["vertices"], dtype=float), 2) * 1e-3   # mm -> m
    t = _as_2n(np.asarray(rec["triangles"], dtype=np.int64), 3)
    ne = t.shape[1]
    tags = rec.get("tags")
    if tags is None:
        raise PullFieldError(
            "the stored EM field carries no domain tags, so the air in which "
            "the Maxwell stress has to be read cannot be told from the iron")
    tags = np.asarray(tags, dtype=np.int64).reshape(-1)
    if tags.size != ne:
        raise PullFieldError("the stored EM field's tags do not match its mesh")
    bx, by = rec.get("bx_per_tri"), rec.get("by_per_tri")
    if bx is not None and by is not None:
        bx = np.asarray(bx, dtype=float).reshape(-1)
        by = np.asarray(by, dtype=float).reshape(-1)
        src = "Bx, By per element, stored with the run"
    elif rec.get("a_z_per_node") is not None:
        A = np.asarray(rec["a_z_per_node"], dtype=float).reshape(-1)
        if A.size != p.shape[1]:
            raise PullFieldError("the stored A_z does not match the EM mesh")
        x, y = p[0][t], p[1][t]
        det = (x[1] - x[0]) * (y[2] - y[0]) - (x[2] - x[0]) * (y[1] - y[0])
        det = np.where(np.abs(det) < 1e-30, 1e-30, det)
        a0, a1, a2 = A[t[0]], A[t[1]], A[t[2]]
        dadx = ((a1 - a0) * (y[2] - y[0]) - (a2 - a0) * (y[1] - y[0])) / det
        dady = ((a2 - a0) * (x[1] - x[0]) - (a1 - a0) * (x[2] - x[0])) / det
        bx, by = dady, -dadx
        src = ("B = curl A_z on the stored P1 vertex values (record written "
               "before Bx/By were stored; ~1 % from the solver's own |B|)")
    else:
        raise PullFieldError(
            "the stored EM field has no field direction (neither Bx/By nor "
            "A_z) — only |B|, which cannot give the Maxwell stress. Re-run the "
            "duty on the Electromagnetic tab so the field is stored again")
    if bx.size != ne or by.size != ne:
        raise PullFieldError("the stored B does not match the EM mesh")
    sector, theta0 = _sector_of(p)
    meta_raw = rec.get("meta")
    if isinstance(meta_raw, np.ndarray):          # a raw npz: 0-d JSON string
        try:
            import json
            meta_raw = json.loads(str(meta_raw))
        except Exception:                                   # noqa: BLE001
            meta_raw = {}
    meta = dict(meta_raw) if isinstance(meta_raw, dict) else {}
    return EmField(p=p, t=t, tags=tags, bx=bx, by=by, b_source=src,
                   sector_rad=sector, theta0=theta0, meta=meta)


# ---------------------------------------------------------------------------
# The Maxwell traction
# ---------------------------------------------------------------------------

def maxwell_traction(bx, by, nx, ny) -> Tuple[np.ndarray, np.ndarray]:
    """t = T . n  with  T = (B B - 1/2 |B|^2 I) / mu0, Pa (the force per area
    on what lies on the side ``n`` points AWAY from)."""
    bx, by, nx, ny = (np.asarray(a, dtype=float) for a in (bx, by, nx, ny))
    bn = bx * nx + by * ny
    b2 = bx * bx + by * by
    return (bx * bn - 0.5 * b2 * nx) / MU0, (by * bn - 0.5 * b2 * ny) / MU0


def traction_components(bx, by, nx, ny) -> Tuple[np.ndarray, np.ndarray]:
    """(t_n, t_t) = ((B_n^2 - B_t^2) / 2mu0, B_n B_t / mu0) on the normal n,
    with the tangent (-n_y, n_x)."""
    bx, by, nx, ny = (np.asarray(a, dtype=float) for a in (bx, by, nx, ny))
    bn = bx * nx + by * ny
    bt = -bx * ny + by * nx
    return (bn * bn - bt * bt) / (2.0 * MU0), bn * bt / MU0


def sample_surface_traction(fld: EmField, x: np.ndarray, n: np.ndarray,
                            offsets_mm: Sequence[float] = SAMPLE_OFFSETS_MM
                            ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(t (m, 2) Pa, b (m, 2) T, matched (m,) bool) at surface points ``x``.

    The field is read just OUTSIDE each point, along its outward normal, at the
    first offset that lands in a non-magnetic element; points with none come
    back NaN and ``matched = False``.
    """
    x = np.asarray(x, dtype=float).reshape(-1, 2)
    n = np.asarray(n, dtype=float).reshape(-1, 2)
    m = x.shape[0]
    b = np.full((m, 2), np.nan)
    todo = np.ones(m, dtype=bool)
    nonmag = np.asarray(NONMAGNETIC_TAGS, dtype=np.int64)
    for off in offsets_mm:
        if not todo.any():
            break
        idx = np.nonzero(todo)[0]
        xs = x[idx] + float(off) * 1e-3 * n[idx]
        bb, _el, tg = fld.sample_b(xs)
        good = np.isin(tg, nonmag) & np.isfinite(bb).all(axis=1)
        b[idx[good]] = bb[good]
        todo[idx[good]] = False
    tx, ty = maxwell_traction(b[:, 0], b[:, 1], n[:, 0], n[:, 1])
    return np.stack([tx, ty], axis=1), b, ~todo


def annulus_check(fld: EmField, r_surf_m: float, side: str = "outer",
                  r_other_m: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """The total radial pull on the ROTOR through the circle of radius
    ``r_surf_m`` (the rotor's gap radius), per metre of stack, from an AREA
    integral over the EM mesh's gap elements.

    See the module docstring: ``K = integral(r^2 sigma_rr dtheta)`` is constant
    across a source-free gap, so the radial pull through the circle at ``r``
    is ``K / r`` on what lies inside it — outward on an inner rotor, and the
    negative of it (inward) on an OUTER rotor, which lies outside the circle.
    The annulus runs from the rotor to the stator (``r_other_m``, read off the
    EM mesh when not given).  ``None`` when there are no gap elements.
    """
    if side == "outer":
        r_other_m = fld.stator_bore_m() if r_other_m is None else r_other_m
        lo, hi, sgn = r_surf_m, r_other_m, 1.0
    else:
        r_other_m = fld.stator_od_m() if r_other_m is None else r_other_m
        lo, hi, sgn = r_other_m, r_surf_m, -1.0
    if lo is None or hi is None or hi <= lo:
        return None
    cen = fld.centroids
    rc = np.hypot(cen[:, 0], cen[:, 1])
    m = (np.isin(fld.tags, np.asarray(NONMAGNETIC_TAGS)) & (rc > lo)
         & (rc < hi))
    if not m.any():
        return None
    th = np.arctan2(cen[m, 1], cen[m, 0])
    c, s = np.cos(th), np.sin(th)
    br = fld.bx[m] * c + fld.by[m] * s
    bt = -fld.bx[m] * s + fld.by[m] * c
    srr = (br * br - bt * bt) / (2.0 * MU0)
    A = fld.areas[m]
    r = rc[m]
    k_full = 2.0 * math.pi * float((srr * r * A).sum()) / float((A / r).sum())
    return {"total_n_per_m": sgn * k_full / r_surf_m,
            "r_from_mm": lo * 1e3, "r_to_mm": hi * 1e3,
            "n_elements": int(m.sum()),
            "sigma_rr_mean_mpa": float((srr * A).sum() / A.sum()) * 1e-6}


# ---------------------------------------------------------------------------
# The request
# ---------------------------------------------------------------------------

@dataclass
class PullSpec:
    """What pull to apply.  ``source`` is ``uniform`` or ``em``."""
    source: str
    pressure_pa: float = 0.0               # uniform
    b_gap_t: Optional[float] = None        # uniform, when given as B
    field: Optional[EmField] = None        # em
    label: str = ""                        # where it came from, for the report
    origin: Dict[str, Any] = dc_field(default_factory=dict)   # em provenance

    @property
    def active(self) -> bool:
        return self.source in ("uniform", "em")


def uniform_spec(pull_mpa: Optional[float] = None,
                 b_gap_t: Optional[float] = None) -> PullSpec:
    """The explicit ESTIMATE: one pressure, or one gap flux density."""
    if (pull_mpa is None) == (b_gap_t is None):
        raise ValueError("a uniform pull needs exactly one of pull_mpa (the "
                         "pressure, MPa) or pull_b_gap_t (the air-gap flux "
                         "density, T; p = B^2 / 2 mu0)")
    if b_gap_t is not None:
        b = float(b_gap_t)
        if not math.isfinite(b) or b <= 0.0 or b > MAX_B_GAP_T:
            raise ValueError(f"pull_b_gap_t = {b_gap_t!r} T is outside "
                             f"0 < B <= {MAX_B_GAP_T} T")
        p = b * b / (2.0 * MU0)
        return PullSpec("uniform", pressure_pa=p, b_gap_t=b,
                        label=(f"uniform estimate from B_gap = {b:g} T "
                               f"(p = B²/2μ0 = {p * 1e-6:.3f} MPa)"))
    p_mpa = float(pull_mpa)
    if not math.isfinite(p_mpa) or p_mpa <= 0.0 or p_mpa > MAX_PULL_MPA:
        raise ValueError(f"pull_mpa = {pull_mpa!r} is outside 0 < p <= "
                         f"{MAX_PULL_MPA} MPa")
    return PullSpec("uniform", pressure_pa=p_mpa * 1e6,
                    label=f"uniform estimate p = {p_mpa:g} MPa")


# ---------------------------------------------------------------------------
# The load
# ---------------------------------------------------------------------------

def _rot(xy: np.ndarray, ang) -> np.ndarray:
    c, s = np.cos(ang), np.sin(ang)
    return np.stack([c * xy[:, 0] - s * xy[:, 1], s * xy[:, 0] + c * xy[:, 1]],
                    axis=1)


def gap_side(polys: Optional[dict], r_rotor_min_m: float,
             r_rotor_max_m: float) -> str:
    """``"outer"`` when the air gap is OUTSIDE the rotor (an inner rotor, the
    usual case), ``"inner"`` for an OUTER rotor whose stator sits inside it —
    read off the drawn stator, never assumed.  No stator drawn: ``"outer"``."""
    st = (polys or {}).get("stator") if isinstance(polys, dict) else None
    if st is None:
        return "outer"
    try:
        geoms = list(st.geoms) if hasattr(st, "geoms") else [st]
        rmax = 0.0
        for g in geoms:
            xy = np.asarray(g.exterior.coords, dtype=float)
            if xy.size:
                rmax = max(rmax, float(np.hypot(xy[:, 0], xy[:, 1]).max()))
        if rmax > 0 and rmax * 1e-3 <= r_rotor_min_m + 1e-9:
            return "inner"
    except Exception:                                       # noqa: BLE001
        pass
    return "outer"


#: |n . e_r| below which a facet is a SIDE wall (a notch flank, a sector cut)
#: rather than a face the gap looks onto.  The radial projection of the pull
#: on such a facet is what the angle it subtends carries — next to nothing.
SIDE_WALL_COS = 0.1

#: Angular resolution of the radial-visibility skyline, rad (0.01° = 11 µm of
#: arc on a Ø125 rotor — far below any meshed feature).
SKYLINE_DTHETA = math.radians(0.01)

#: How far (m) inside the skyline a surface point may sit and still count as
#: seen from the gap: the chord sag of the coarsest boundary facet, with room.
SKYLINE_TOL_M = 2e-5


def gap_facets(mesh, part_tri: np.ndarray, side: str = "outer",
               band: Optional[float] = None,
               use_band: bool = True) -> Tuple[np.ndarray, float]:
    """(facet ids, gap radius m) — the CANDIDATE air-gap surface.

    The rotor iron / magnet boundary facets that face the gap (``n . e_r``
    beyond ``SIDE_WALL_COS``: outward for ``side = outer``, inward for an
    outer rotor), within ``band`` of the gap radius when ``use_band``.
    Which parts of them the gap actually SEES is ``radial_visibility``'s
    business — a pocket floor faces outward too.
    """
    from motor_ai_sim.simulation.mechanical.contact import (PART_MAGNET,
                                                            PART_ROTOR)
    from motor_ai_sim.simulation.mechanical.rotor_stress import (
        GAP_BAND, _boundary_facets)
    if band is None:
        band = GAP_BAND
    sgn = 1.0 if side == "outer" else -1.0
    bf, mid, n, _ln = _boundary_facets(mesh, part_tri, (PART_ROTOR, PART_MAGNET))
    if bf.size == 0:
        return np.zeros(0, np.int64), 0.0
    r = np.hypot(mid[:, 0], mid[:, 1])
    er = mid / np.maximum(r, 1e-30)[:, None]
    facing = sgn * np.einsum("ij,ij->i", n, er) > SIDE_WALL_COS
    if not facing.any():
        return np.zeros(0, np.int64), 0.0
    if side == "outer":
        r_g = float(r[facing].max())
        keep = facing & (r >= r_g * (1.0 - float(band))) if use_band else facing
    else:
        r_g = float(r[facing].min())
        keep = facing & (r <= r_g * (1.0 + float(band))) if use_band else facing
    return bf[keep], r_g


def radial_visibility(mesh, part_tri: np.ndarray, xq: np.ndarray,
                      side: str = "outer") -> np.ndarray:
    """True where a point ``xq`` (m, (n, 2)) on the rotor solids' boundary is
    SEEN from the air gap along its own radius.

    The gap looks at the rotor radially: a point is exposed when no rotor iron
    or magnet lies further out at its angle (further IN for an outer rotor).
    That keeps a pocket floor, a magnet top under an iron bridge and the floor
    of an open slot under its magnet out of the pull — their walls see the
    field of a cavity INSIDE the rotor, and the Maxwell stress there is an
    internal pair (iron and magnet attracting each other across a slit), not a
    pull toward the stator — and it keeps every pole top, chamfer, notch and
    exposed magnet face in.  Built as a skyline: the largest (smallest) radius
    of every rotor/magnet boundary facet, binned by angle at
    ``SKYLINE_DTHETA``, interpolated linearly in angle along each facet.
    """
    from motor_ai_sim.simulation.mechanical.contact import (PART_MAGNET,
                                                            PART_ROTOR)
    from motor_ai_sim.simulation.mechanical.rotor_stress import _boundary_facets
    xq = np.asarray(xq, dtype=float).reshape(-1, 2)
    bf, _mid, _n, _ln = _boundary_facets(mesh, part_tri,
                                         (PART_ROTOR, PART_MAGNET))
    if bf.size == 0 or xq.shape[0] == 0:
        return np.ones(xq.shape[0], dtype=bool)
    p = mesh.p
    a, b = p[:, mesh.facets[0, bf]], p[:, mesh.facets[1, bf]]
    ta, tb = np.arctan2(a[1], a[0]), np.arctan2(b[1], b[0])
    ra, rb = np.hypot(a[0], a[1]), np.hypot(b[0], b[1])
    d = np.mod(tb - ta + math.pi, 2.0 * math.pi) - math.pi
    tb = ta + d
    lo, hi = np.minimum(ta, tb), np.maximum(ta, tb)
    dth = SKYLINE_DTHETA
    nb = int(math.ceil(2.0 * math.pi / dth))
    k0 = np.ceil((lo + math.pi) / dth - 0.5).astype(np.int64)
    k1 = np.floor((hi + math.pi) / dth - 0.5).astype(np.int64)
    cnt = np.maximum(k1 - k0 + 1, 0)
    rows = np.repeat(np.arange(bf.size), cnt)
    offs = np.arange(int(cnt.sum())) - np.repeat(np.cumsum(cnt) - cnt, cnt)
    kk = k0[rows] + offs
    tc = -math.pi + (kk + 0.5) * dth
    # the radius where the ray at tc crosses the facet — exactly, not
    # interpolated in angle: a chord bows inward of its end radii by its sag
    # (21 µm for a 2.6 mm chord at r = 40 mm), which is the tolerance itself
    ux, uy = np.cos(tc), np.sin(tc)
    ax_, ay_ = a[0][rows], a[1][rows]
    dx_, dy_ = b[0][rows] - ax_, b[1][rows] - ay_
    den = ux * dy_ - uy * dx_                          # u x d
    num = -(ux * ay_ - uy * ax_)                       # -(u x a)
    ok_ = np.abs(den) > 1e-30
    s = np.clip(np.where(ok_, num / np.where(ok_, den, 1.0), 0.0), 0.0, 1.0)
    rr = np.where(ok_, ux * (ax_ + s * dx_) + uy * (ay_ + s * dy_),
                  np.maximum(ra[rows], rb[rows]))
    # the endpoints too, so a facet shorter than a bin still counts
    kk = np.concatenate([kk, np.floor((ta + math.pi) / dth).astype(np.int64),
                         np.floor((np.mod(tb + math.pi, 2.0 * math.pi))
                                  / dth).astype(np.int64)])
    rr = np.concatenate([rr, ra, rb])
    kk = np.mod(kk, nb)
    if side == "outer":
        sky = np.full(nb, -np.inf)
        np.maximum.at(sky, kk, rr)
    else:
        sky = np.full(nb, np.inf)
        np.minimum.at(sky, kk, rr)
    tq = np.arctan2(xq[:, 1], xq[:, 0])
    kq = np.mod(np.floor((tq + math.pi) / dth).astype(np.int64), nb)
    rq = np.hypot(xq[:, 0], xq[:, 1])
    if side == "outer":
        return rq >= sky[kq] - SKYLINE_TOL_M
    return rq <= sky[kq] + SKYLINE_TOL_M


def pull_load(mesh, part_tri: np.ndarray, spec: PullSpec, order: int = 2,
              period_n: int = 1, period_cut_rad: float = 0.0,
              band: Optional[float] = None, side: str = "outer"
              ) -> Tuple[np.ndarray, Dict[str, Any]]:
    """(load vector, raw report) of the pull on the SOLVED mesh, per metre of
    stack — the module's usual units.

    ``period_n`` / ``period_cut_rad`` are the rotor's rotational periodicity
    (``symmetry.SectorPlan``: the same cut the sector model uses): the EM
    pull is an envelope over the ``period_n`` poles (see the module
    docstring).  ``period_n = 1`` applies the field where it is.  ``side`` is
    where the air gap is (``gap_side``): ``outer`` for an inner rotor,
    ``inner`` for an outer rotor, whose pull points INWARD.

    The loaded surface is what the gap SEES (``radial_visibility``) of the
    gap-facing rotor iron and magnet faces: the EM field is read on all of
    it (a notch floor gets the notch's own weak field), while a uniform
    estimate keeps the torque's ``GAP_BAND`` too, because a uniform pressure
    on a deep notch would be invented.
    The report's numbers are those of the mesh solved; the caller scales the
    extensive ones to the machine.
    """
    from skfem import FacetBasis, LinearForm, asm

    from motor_ai_sim.simulation.mechanical.rotor_stress import make_basis

    if side not in ("outer", "inner"):
        raise ValueError(f"gap side must be 'outer' or 'inner', got {side!r}")
    facets, r_o = gap_facets(mesh, part_tri, side=side, band=band,
                             use_band=(spec.source != "em"))
    if facets.size == 0 or r_o <= 0:
        raise ValueError(
            "no air-gap-facing surface was found on the rotor solids, so there "
            "is nowhere for the magnetic pull to act — check that the "
            "cross-section has rotor iron or magnets facing the air gap")
    _b, elem = make_basis(mesh, order)
    fb = FacetBasis(mesh, elem, facets=facets, intorder=3 if order == 2 else 2)
    X = fb.global_coordinates().value                    # (2, nf, nq)
    N = np.asarray(fb.normals)                           # (2, nf, nq)
    w = np.asarray(fb.dx)                                # (nf, nq)
    nf, nq = w.shape
    x = np.stack([X[0].reshape(-1), X[1].reshape(-1)], axis=1)
    n = np.stack([N[0].reshape(-1), N[1].reshape(-1)], axis=1)
    r = np.hypot(x[:, 0], x[:, 1])
    er = x / np.maximum(r, 1e-30)[:, None]
    # OUTWARD of the solid, by the owning element's centroid — never assumed
    owner = mesh.f2t[0, facets]
    pt = mesh.p
    cen_o = ((pt[:, mesh.t[0, owner]] + pt[:, mesh.t[1, owner]]
              + pt[:, mesh.t[2, owner]]) / 3.0).T
    cen_q = np.repeat(cen_o, nq, axis=0)
    flip = np.einsum("ij,ij->i", n, x - cen_q) < 0
    n[flip] *= -1.0
    seen = radial_visibility(mesh, part_tri, x, side=side)
    # a hidden point carries no pull: its weight goes to zero
    wq = w.reshape(-1) * seen
    part_q = np.repeat(part_tri[owner], nq)

    rep: Dict[str, Any] = {"source": spec.source, "label": spec.label,
                           "gap_side": side,
                           "direction": ("outward, toward the stator"
                                         if side == "outer" else
                                         "inward, toward the stator inside "
                                         "the (outer) rotor"),
                           "r_gap_mm": r_o * 1e3,
                           "surface_mm": float(wq.sum()) * 1e3,
                           "n_facets": int(facets.size)}
    mt_npm = 0.0                                         # tangential (diag.)
    sgn = 1.0 if side == "outer" else -1.0               # the pull direction
    if spec.source == "uniform":
        tr = spec.pressure_pa * np.einsum("ij,ij->i", n, er)
        rep.update({"estimate": True, "b_gap_t": spec.b_gap_t,
                    "pressure_input_mpa": spec.pressure_pa * 1e-6,
                    "applied_as": ("radial component of a uniform pull p n "
                                   "(toward the stator) on the rotor solids' "
                                   "air-gap facets (iron pole tops and magnet "
                                   "tops)"),
                    "instant": None, "envelope": None})
    elif spec.source == "em":
        fld = spec.field
        if fld is None:
            raise ValueError("pull source 'em' needs a stored EM field")
        P = 2.0 * math.pi / max(int(period_n), 1)
        if period_n > 1:
            k = np.mod(np.floor((np.arctan2(x[:, 1], x[:, 0]) - period_cut_rad)
                                / P + 1e-12), period_n)
            x0, n0 = _rot(x, -k * P), _rot(n, -k * P)
        else:
            x0, n0 = x, n
        cols_r, cols_t, cols_ok = [], [], []
        for j in range(max(int(period_n), 1)):
            xj, nj = (_rot(x0, j * P), _rot(n0, j * P)) if j else (x0, n0)
            tv, _bv, ok = sample_surface_traction(fld, xj, nj)
            erj = xj / np.maximum(np.hypot(xj[:, 0], xj[:, 1]), 1e-30)[:, None]
            etj = np.stack([-erj[:, 1], erj[:, 0]], axis=1)
            cols_r.append(np.einsum("ij,ij->i", tv, erj))
            cols_t.append(np.einsum("ij,ij->i", tv, etj))
            cols_ok.append(ok)
        TR = np.stack(cols_r, axis=1)                    # (m, n_per)
        TT = np.stack(cols_t, axis=1)
        OK = np.stack(cols_ok, axis=1)
        unmatched = float((wq[:, None] * ~OK).sum(axis=0).max()
                          / max(float(wq.sum()), 1e-30))
        if unmatched > MAX_UNMATCHED_FRAC:
            raise PullFieldError(
                f"the stored EM field could not be read outside "
                f"{100.0 * unmatched:.0f} % of the rotor's air-gap surface — "
                "the field is not of this rotor (another geometry, or a rotor "
                "turned against the one meshed here)")
        TR = np.where(OK, TR, 0.0)
        TT = np.where(OK, TT, 0.0)
        # per pole-field total, as a magnitude IN the pull direction (outward
        # on an inner rotor, inward on an outer one)
        F = sgn * (TR * wq[:, None]).sum(axis=0)
        # the FIRST pole within round-off of the largest, so poles that are
        # identical (a pole-periodic field) are not picked by floating noise
        # and a full and a sector model name the same one
        fmax = float(F.max())
        jstar = int(np.nonzero(F >= fmax - 1e-7 * abs(fmax))[0][0])
        tr = TR[:, jstar]
        # the field's own tangential moment at THIS instant (each pole its own
        # field — the mean over the pole columns), a diagnostic beside the
        # applied energy-method torque; never applied
        mt_npm = float(np.mean((r[:, None] * TT * wq[:, None]).sum(axis=0)))
        fmean = float(F.mean())
        spread = ([float(F.min() / fmean - 1.0), float(F.max() / fmean - 1.0)]
                  if fmean > 0 else None)
        em_r = fld.rotor_radius_m()
        rep.update({
            "estimate": False,
            "b_source": fld.b_source,
            "em_sector_deg": math.degrees(fld.sector_rad),
            "em_rotor_r_mm": (em_r * 1e3 if em_r else None),
            "matched_fraction": 1.0 - unmatched,
            "applied_as": ("radial component of the Maxwell traction "
                           "t = (B B − ½|B|² I)/μ0 · n, read in the air / "
                           "sleeve just outside the rotor solids' air-gap "
                           "facets (iron pole tops and magnet tops); the "
                           "tangential part is not applied"),
            "instant": ("the stored instant of the EM run — its last time "
                        "step, rotor frame"),
            "envelope": {
                "n_poles": int(max(period_n, 1)),
                "worst_pole": jstar,
                "rule": ("every pole carries the distribution of the pole with "
                         "the largest radial pull at that instant (the poles "
                         "sample the slot ripple the rotor sweeps through)"
                         if period_n > 1 else
                         "none — the rotor is not periodic; the field is "
                         "applied where it is"),
                "pole_spread": spread,
            },
            "_pole_mean_total_n_per_m": sgn * fmean,
        })
    else:
        raise ValueError(f"unknown pull source {spec.source!r}")

    @LinearForm
    def _radial(v, w_):
        rr = np.sqrt(w_.x[0] ** 2 + w_.x[1] ** 2)
        return w_["tr"] * (w_.x[0] / rr * v[0] + w_.x[1] / rr * v[1])

    tr = np.where(seen, tr, 0.0)
    f = asm(_radial, fb, tr=tr.reshape(nf, nq))
    fr = tr * wq                                         # radial force per pt
    total = float(fr.sum())
    lw = float(wq.sum())
    # Pressures are MAGNITUDES IN THE PULL DIRECTION (toward the stator), so an
    # outer rotor's inward pull reads as a positive pressure too; the signed
    # radial forces below keep their sign (negative = inward).
    pq = sgn * tr
    rep.update({
        "pressure_peak_mpa": float(np.max(pq)) * 1e-6 if pq.size else 0.0,
        "pressure_min_mpa": float(np.min(pq)) * 1e-6 if pq.size else 0.0,
        "pressure_mean_mpa": (sgn * total / lw) * 1e-6 if lw > 0 else 0.0,
        "_sgn": sgn,
        "_total_n_per_m": total,
        "_net_n_per_m": [float((fr * er[:, 0]).sum()),
                         float((fr * er[:, 1]).sum())],
        "_by_part_n_per_m": {
            name: float(fr[part_q == pid].sum())
            for name, pid in _part_ids().items()
            if ((part_q == pid) & (wq > 0)).any()},
        "_maxwell_torque_n_m_per_m": mt_npm,
        "_r_o_m": r_o,
    })
    return f, rep


def _part_ids() -> Dict[str, int]:
    from motor_ai_sim.simulation.mechanical.contact import (PART_MAGNET,
                                                            PART_ROTOR)
    return {"rotor": PART_ROTOR, "magnet": PART_MAGNET}


def finish_report(rep: Dict[str, Any], spec: PullSpec, stack_length_m: float,
                  n_sym: int, n_poles: int) -> Dict[str, Any]:
    """The raw report, read as the MACHINE's: totals in N over the stack,
    scaled by the sector count, the per-pole force, and the check."""
    L = float(stack_length_m)
    Lf = L if L > 0 else float("nan")
    total_m = float(rep.pop("_total_n_per_m")) * n_sym
    net = rep.pop("_net_n_per_m")
    by_part = rep.pop("_by_part_n_per_m")
    mtq = float(rep.pop("_maxwell_torque_n_m_per_m")) * n_sym
    r_o = float(rep.pop("_r_o_m"))
    sgn = float(rep.pop("_sgn", 1.0))
    pole_mean = rep.pop("_pole_mean_total_n_per_m", None)
    npl = max(int(n_poles), 1)
    out = dict(rep)
    out.update({
        "stack_length_mm": L * 1e3,
        "n_sectors": int(n_sym),
        "n_poles": npl,
        # the applied total spread over the full circle at the gap radius —
        # comparable with p = B^2 / 2 mu0 of a stated gap flux density
        "pressure_nominal_mpa": sgn * total_m / (2.0 * math.pi * r_o) * 1e-6,
        "radial_force_total_n": total_m * Lf,
        "radial_force_per_pole_n": total_m * Lf / npl,
        "radial_force_by_part_n": {k: float(v) * n_sym * Lf
                                   for k, v in by_part.items()},
        # A sector's own vector sum is not zero (its neighbours balance it);
        # the machine's is, by the cyclic symmetry the sector imposes.
        "net_force_n": (math.hypot(*net) * Lf if n_sym == 1 else 0.0),
        "net_force_note": (None if n_sym == 1 else
                           "zero by the cyclic symmetry of the sector model"),
        "maxwell_torque_nm": (mtq * Lf if spec.source == "em" else None),
        "tangential": (
            "not applied: the net torque is the torque load's (energy / "
            "flux-linkage torque from the Simulation tab); a radial traction "
            "adds no torque, so nothing is counted twice. maxwell_torque_nm "
            "is the field's own tangential moment, a diagnostic only"),
        "coverage": None,
    })
    if n_sym > 1:
        out["surface_mm"] = float(out["surface_mm"]) * n_sym
        out["n_facets_sector"] = int(out["n_facets"])
    circ_load = 2.0 * math.pi * r_o
    if spec.source == "uniform":
        expect = sgn * spec.pressure_pa * circ_load
        out["check"] = {
            "what": ("applied total radial pull against p × 2π r L (a full "
                     "cylinder at the gap radius)"),
            "expected_total_n": expect * Lf,
            "ratio": (total_m / expect) if expect else None,
        }
        out["coverage"] = (total_m / expect) if expect else None
        out["b_equivalent_t"] = (spec.b_gap_t if spec.b_gap_t is not None
                                 else math.sqrt(2.0 * MU0 * spec.pressure_pa))
    else:
        fld = spec.field
        chk = (annulus_check(fld, r_o, side=str(rep.get("gap_side") or "outer"))
               if fld is not None else None)
        surf = (float(pole_mean) * n_sym) if pole_mean is not None else None
        if chk is not None and surf is not None:
            ratio = surf / chk["total_n_per_m"] if chk["total_n_per_m"] else None
            out["check"] = {
                "what": ("pole-mean surface pull against the annulus integral "
                         "of the same field (2π Σσ_rr r A / (Σ A/r · r_o) over "
                         f"{chk['n_elements']} gap elements, "
                         f"{chk['r_from_mm']:.3f}–{chk['r_to_mm']:.3f} mm)"),
                "surface_total_n": surf * Lf,
                "field_total_n": chk["total_n_per_m"] * Lf,
                "field_per_pole_n": chk["total_n_per_m"] * Lf / npl,
                "ratio": ratio,
                "within_tol": (bool(abs(ratio - 1.0) <= CHECK_TOL)
                               if ratio is not None else None),
                "tolerance": CHECK_TOL,
            }
        else:
            out["check"] = {"what": "no gap elements on the EM mesh to "
                                    "integrate over", "ratio": None,
                            "within_tol": None}
        mean_p = float(out["pressure_mean_mpa"]) * 1e6
        out["b_equivalent_t"] = (math.sqrt(2.0 * MU0 * mean_p)
                                 if mean_p > 0 else None)
        out["origin"] = dict(spec.origin or {})
    return out


def summary_text(rep: Optional[Dict[str, Any]]) -> str:
    """One clause for a verdict: 'magnetic pull 0.71 MPa peak, 1.23 kN per pole
    (EM field of duty …)'."""
    if not rep:
        return ""
    per = rep.get("radial_force_per_pole_n")
    per_s = (f", {abs(per) / 1e3:,.2f} kN per pole"
             if isinstance(per, (int, float)) and math.isfinite(per) else "")
    src = ("estimate" if rep.get("source") == "uniform" else "EM field")
    return (f"magnetic pull {float(rep.get('pressure_peak_mpa') or 0.0):.3f} MPa "
            f"peak{per_s} ({src}: {rep.get('label') or rep.get('source')})")
