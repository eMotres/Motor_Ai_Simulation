"""The REAL machine, pulled READ-ONLY out of the 2D pipeline's own source of truth.

Nothing in this module invents geometry or material data.  Every number comes
from the same two calls the 2D solver makes:

  * ``CadQueryMotor.get_2d_polygons(0.0)`` — the shapely cross-section in mm
    (``cadquery_geometry.py``), which is what ``mesher.build_mesh_from_polygons``
    consumes, so the 3D extrusion meshes the SAME cross-section the 2D solve does;
  * ``fem_solver_2d.build_materials({A:0,B:0,C:0}, ...)`` — the per-domain
    ``FEMMaterial`` map, which is where the assigned library material's laminated
    B-H curve, the magnet's Br and recoil mu_r, and the per-magnet magnetisation
    VECTOR already live.

Re-deriving any of that here (a private B-H lookup, a private "magnets are
radial" assumption) is exactly how a 3D model ends up answering a different
question than the 2D model it is supposed to correct.  In particular this
machine is a SPOKE-PM rotor: the magnetisation is TANGENTIAL, alternating, and
each magnet's direction comes from its own polygon centroid — see
``fem_solver_2d.build_materials`` around the ``DOM_MAG_BASE`` loop.

Sector
------
12 slots / 14 poles.  The stator repeats every 30 deg, the rotor every
360/14 deg; the common period is 360/gcd(12,14) = 180 deg — the same sector
``mesh.n_sectors = 2`` in ``config/motor_config.yaml`` gives the 2D solver.
Half a ring is 6 slots + 7 poles, and 7 is ODD, so a 180 deg rotation maps the
machine onto itself with every magnet reversed.  In the total scalar potential
that is

    phi(r, theta + pi, z) = -phi(r, theta, z)

i.e. ANTI-periodic, exactly like the 2D solver's ``A_z(r, pi) = -A_z(r, 0)``
(``fem_solver_2d._solve_with_bc`` -> ``mesher._apply_anti_periodic``, sign -1
when the wedge holds an odd number of poles).

Units: the polygons and this module's ``*_mm`` fields are millimetres, because
that is the scale gmsh's OCC boolean tolerances in this project are tuned for.
Everything handed to the solver is METRES, per the static3d package convention.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

MM = 1e-3


@dataclass
class RegionSpec:
    """One meshed 2D region of the cross-section and the material it carries.

    ``polygon`` is shapely, in mm, already clipped to the sector.
    ``M`` is the magnetisation VECTOR in A/m (world frame, z-component always 0
    for this topology); ``bh_curve`` non-empty makes the region nonlinear iron.
    """
    name: str
    polygon: Any
    mu_r: float = 1.0
    M: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    bh_curve: Optional[List[Tuple[float, float]]] = None
    kind: str = "air"            # "iron" | "magnet" | "shaft" | "air"
    stack_kf: float = 1.0        # lamination fill factor along z (1 = solid)

    @property
    def area_mm2(self) -> float:
        return float(self.polygon.area) if self.polygon is not None else 0.0


@dataclass
class MotorSection:
    """Everything Stage A needs about the machine, in one read-only object."""
    geo: Dict[str, Any]                       # merged geometry block (mm)
    regions: List[RegionSpec]
    n_sectors: int
    sector_deg: float
    antiperiodic: bool
    # radii, mm
    r_stator_out_mm: float
    r_stator_in_mm: float
    r_rotor_out_mm: float
    r_rotor_in_mm: float
    r_shaft_in_mm: float
    mid_r_mm: float
    stack_mm: float
    num_slots: int
    num_poles: int
    pole_pairs: int
    Br_T: float
    mu_rec: float
    fingerprint: str = ""
    materials: Dict[str, str] = field(default_factory=dict)
    pocket_sliver_mm2: float = 0.0
    polys_full: Optional[dict] = None

    # -- convenience in metres --------------------------------------------
    @property
    def mid_r_m(self) -> float:
        return self.mid_r_mm * MM

    @property
    def stack_m(self) -> float:
        return self.stack_mm * MM

    @property
    def r_out_m(self) -> float:
        return self.r_stator_out_mm * MM

    def magnet_regions(self) -> List[RegionSpec]:
        return [r for r in self.regions if r.kind == "magnet"]

    def iron_regions(self) -> List[RegionSpec]:
        return [r for r in self.regions if r.kind == "iron"]

    def summary(self) -> str:
        L = [f"{self.num_slots}s/{self.num_poles}p spoke-PM, OD "
             f"{2*self.r_stator_out_mm:.1f} mm, stack {self.stack_mm:.1f} mm",
             f"  sector {self.sector_deg:.1f} deg "
             f"({'anti-' if self.antiperiodic else ''}periodic), "
             f"{len(self.magnet_regions())} magnets in sector",
             f"  gap {self.r_stator_in_mm - self.r_rotor_out_mm:.3f} mm, "
             f"mid-gap r = {self.mid_r_mm:.3f} mm",
             f"  Br = {self.Br_T:.3f} T, mu_rec = {self.mu_rec:.3f}",
             f"  materials {self.materials}"]
        for r in self.regions:
            L.append(f"    {r.name:<12s} A = {r.area_mm2:9.4f} mm^2  "
                     f"mu_r {r.mu_r:8.1f}  |M| "
                     f"{math.hypot(r.M[0], r.M[1]):10.1f}  "
                     f"{'BH' if r.bh_curve else '--'}")
        return "\n".join(L)


# --------------------------------------------------------------------------
# sector logic — the same rule the 2D solver uses
# --------------------------------------------------------------------------

def sector_choice(num_slots: int, num_poles: int,
                  n_sectors_request: Optional[int] = None
                  ) -> Tuple[int, bool]:
    """(n_sectors, antiperiodic) for this machine.

    The largest legal sector count is gcd(slots, poles) — below that the
    cross-section does not repeat.  The wedge is ANTI-periodic when it holds an
    odd number of poles (a rotation by the sector angle swaps N and S), plain
    periodic when it holds an even number.  Identical rule to
    ``fem_solver_2d.fem_transient_sliding_band``'s ``_bc_sign``.
    """
    g = math.gcd(int(num_slots), int(num_poles))
    n = int(n_sectors_request) if n_sectors_request else g
    if n < 1:
        n = 1
    if g % n != 0:
        raise ValueError(
            f"n_sectors={n} does not divide gcd({num_slots},{num_poles})={g}: "
            "the cross-section does not repeat at that angle, so the sector "
            "model would solve a machine that does not exist")
    poles_per_sector = int(num_poles) // n
    return n, bool(poles_per_sector % 2 == 1)


# --------------------------------------------------------------------------
# loader
# --------------------------------------------------------------------------

CUT_SNAP_MM = 0.01          # 10 um — see _snap_cut_vertices
POCKET_SLIVER_MM2 = 0.01    # see _recut_rotor_pockets


def _stack_factor(part_key: str, built_mu_r: float) -> float:
    """The lamination fill factor of a laminated core, two ways, cross-checked.

    ``fem_solver_2d.build_materials`` has already used k_f twice: it folded it
    into the B-H curve (``_laminate``) and into the linear permeability
    (``mu_r = k_f*mu_r_steel + (1-k_f)``).  The second is invertible, so the k_f
    the 2D materials were ACTUALLY built with can be recovered from the material
    object this module was handed, and compared with the one the library says.
    They can only differ if the two modules resolved different materials, which
    is exactly the silent mismatch worth catching: the recovered value wins,
    because it is the one the iron in the solve really has.
    """
    kf_lib = None
    mu_raw = None
    try:
        from motor_ai_sim.config import get_material_assignments
        from motor_ai_sim.materials import get_material
        name = (get_material_assignments() or {}).get(part_key)
        if name:
            m = get_material("steel", name)
            kf = float(getattr(m, "stacking_factor", 1.0) or 1.0)
            kf_lib = kf if 0.0 < kf <= 1.0 else 1.0
            mu_raw = float(getattr(m, "mu_r", 0.0) or 0.0)
    except Exception:
        pass
    kf_rec = None
    if mu_raw and mu_raw > 1.5 and built_mu_r > 1.0:
        kf_rec = (float(built_mu_r) - 1.0) / (mu_raw - 1.0)
        if not (0.0 < kf_rec <= 1.0 + 1e-9):
            kf_rec = None
    if kf_rec is not None and kf_lib is not None and abs(kf_rec - kf_lib) > 1e-6:
        import logging
        logging.getLogger(__name__).warning(
            "lamination k_f mismatch for %s: the 2D materials were built with "
            "%.4f, the library says %.4f — using the built one", part_key,
            kf_rec, kf_lib)
    if kf_rec is not None:
        return float(min(max(kf_rec, 1e-3), 1.0))
    return float(kf_lib if kf_lib is not None else 1.0)


def _recut_rotor_pockets(polys: dict, tol_mm2: float = POCKET_SLIVER_MM2
                         ) -> Tuple[dict, float]:
    """Subtract the magnets out of the rotor iron, and check how much came off.

    Returns the repaired polygon dict and the removed area in mm^2.  Above
    ``tol_mm2`` (and above 0.1 % of the rotor) the overlap is a real design
    error, not a discretisation sliver, and it raises.
    """
    from shapely.ops import unary_union

    rotor = polys.get("rotor")
    mags = [mp for mp, _pol in polys.get("magnets", []) if mp is not None]
    if rotor is None or rotor.is_empty or not mags:
        return polys, 0.0
    cut = rotor.difference(unary_union(mags))
    if not cut.is_valid:
        cut = cut.buffer(0)
    removed = float(rotor.area - cut.area)
    if removed <= 1e-12:
        return polys, 0.0
    if removed > tol_mm2 or removed > 1e-3 * rotor.area:
        raise ValueError(
            f"magnets and rotor iron overlap by {removed:.4f} mm^2 "
            f"({100*removed/rotor.area:.3f} % of the rotor) — that is a real "
            "overlap, not a CAD discretisation sliver; the cross-section is "
            "not buildable")
    out = dict(polys)
    out["rotor"] = cut
    return out, removed


def _snap_cut_vertices(poly, a_sec: float, tol: float = CUT_SNAP_MM):
    """Put every vertex ON a sector cut line exactly on it, at a QUANTISED radius.

    The sector model needs the two radial cuts to be each other's rotation
    EXACTLY, because gmsh pairs them curve by curve before meshing.  The
    cross-section builder does not guarantee that to the last nanometre: its
    coincident-point weld picks one representative per cluster, and for two
    features related by the sector rotation it can pick differently.  Measured
    on this machine: one cut carried a breakpoint at r = 12.600783 mm and the
    other the same breakpoint at 12.600000 mm — 783 nm, enough for gmsh to
    refuse to pair the curves, and (if the pairing is loosened instead) enough
    to leave the two cut lines with different node counts, which silently
    destroys the symmetry the whole sector solve rests on.

    So vertices within ``tol`` of a cut line are snapped onto it and their
    radius quantised to ``tol``.  The largest move is half a quantum, 5 um,
    along a straight radial line, against a smallest real feature of 200 um (the
    air gap).  It cannot change the machine; it can only make the two cuts agree.
    """
    from shapely.geometry import Polygon as _SPoly

    ca, sa = math.cos(a_sec), math.sin(a_sec)

    def _fix(coords):
        out = []
        for x, y in coords:
            r = math.hypot(x, y)
            if r > 1e-12:
                if abs(y) <= tol and x > 0:                    # theta = 0 cut
                    out.append((round(r / tol) * tol, 0.0))
                    continue
                if abs(x * sa - y * ca) <= tol and (x * ca + y * sa) > 0:
                    rq = round(r / tol) * tol
                    out.append((rq * ca, rq * sa))             # theta = a_sec
                    continue
            out.append((x, y))
        return out

    parts = list(poly.geoms) if hasattr(poly, "geoms") else [poly]
    fixed = []
    for p in parts:
        g = _SPoly(_fix(list(p.exterior.coords)[:-1]),
                   [_fix(list(h.coords)[:-1]) for h in p.interiors])
        if not g.is_valid:
            g = g.buffer(0)
        if not g.is_empty and g.area > 1e-9:
            fixed.append(g)
    if not fixed:
        return poly
    if len(fixed) == 1:
        return fixed[0]
    from shapely.geometry import MultiPolygon as _SMPoly
    return _SMPoly(fixed)


def _clip_to_sector(polys: dict, n_sectors: int) -> dict:
    """Clip via the 2D mesher's OWN clipper (read-only import) so the sector the
    3D model meshes is the sector the 2D model meshes, then snap the cut-line
    vertices so the two cuts are exact rotations of each other."""
    if n_sectors <= 1:
        return polys
    from motor_ai_sim.simulation.mesher import _clip_polys_to_sector
    out = dict(_clip_polys_to_sector(polys, n_sectors))
    a = 2.0 * math.pi / n_sectors
    for k in ("stator", "rotor", "shaft"):
        if out.get(k) is not None and not out[k].is_empty:
            out[k] = _snap_cut_vertices(out[k], a)
    out["magnets"] = [(_snap_cut_vertices(mp, a), pol)
                      for mp, pol in out.get("magnets", [])]
    return out


def load_motor_section(geo_override: Optional[dict] = None,
                       n_sectors: Optional[int] = None,
                       stack_mm: Optional[float] = None) -> MotorSection:
    """Build the :class:`MotorSection` for the ACTIVE config (or an override).

    Read-only: no file is written, no cache is touched, the global config is
    never mutated.  ``stack_mm`` overrides ``motor_length`` for the L-sweep
    WITHOUT touching the cross-section (the cross-section does not depend on it).
    """
    from motor_ai_sim.cadquery_geometry import CadQueryMotor
    from motor_ai_sim.simulation.fem_solver_2d import (
        DOM_MAG_BASE, DOM_ROTOR, DOM_SHAFT, DOM_STATOR, build_materials)
    from motor_ai_sim.simulation.geometry_2d import build_winding_layout

    motor = CadQueryMotor()
    if geo_override:
        motor.set_parameters(dict(geo_override))
    p = dict(motor.parameters)
    polys = motor.get_2d_polygons(rotor_angle_deg=0.0)

    # A magnet lives in a rotor POCKET, so the two must be disjoint.  The CAD
    # builder's arc discretisation leaves slivers of overlap at the pocket walls
    # — measured on this machine, 0.001 mm^2 against a 13.88 mm^2 magnet, i.e.
    # 7e-5 of it.  Meshing that is not possible (whichever region wins, the
    # other loses material), so the pocket is re-cut here.  How much was cut is
    # CHECKED, not assumed: anything above a tolerance is a real overlap in the
    # design and still raises, because a solve of an impossible machine is never
    # an answer.
    polys, sliver = _recut_rotor_pockets(polys)

    # ...and then the cross-section is validated as usual.
    from motor_ai_sim.geometry_validation import validate_polygons, GeometryInvalid
    vres = validate_polygons(polys, params=p)
    if not vres.ok:
        raise GeometryInvalid(vres)

    num_slots = int(p["num_slots"])
    num_poles = int(p["num_poles"])
    n_sec, anti = sector_choice(num_slots, num_poles, n_sectors)

    # Materials + per-magnet magnetisation from the 2D solver's own builder.
    layout = build_winding_layout(num_slots, num_poles // 2)
    mats = build_materials({"A": 0.0, "B": 0.0, "C": 0.0}, layout, polys,
                           0.0, 1e-5, int(round(p["num_wires_per_slot"])))
    m_stator, m_rotor, m_shaft = mats[DOM_STATOR], mats[DOM_ROTOR], mats[DOM_SHAFT]

    # Magnet directions come from the FULL-ring polygons: clipping can move a
    # centroid, and the direction must be the machine's, not the wedge's.
    mag_dirs: List[Tuple[float, float, float, float]] = []   # cx, cy, Mx, My
    for i, (mp, _pol) in enumerate(polys.get("magnets", [])):
        fm = mats.get(DOM_MAG_BASE + i)
        if fm is None or mp is None or mp.is_empty:
            continue
        mag_dirs.append((mp.centroid.x, mp.centroid.y,
                         float(fm.Mx), float(fm.My)))
    if not mag_dirs:
        raise ValueError("no magnets in the cross-section — nothing to solve")
    Br = float(math.hypot(mag_dirs[0][2], mag_dirs[0][3]) * 4e-7 * math.pi)
    mu_rec = float(mats[DOM_MAG_BASE].mu_r)

    clipped = _clip_to_sector(polys, n_sec)

    regions: List[RegionSpec] = []

    def _add(name, poly, **kw):
        if poly is None or poly.is_empty or poly.area <= 1e-9:
            return
        regions.append(RegionSpec(name=name, polygon=poly, **kw))

    # The stacking factor the 2D magnetic model already folded INTO the curve
    # above (fem_solver_2d.build_materials._laminate: B_geom(H) = k_f B_steel(H)
    # + (1-k_f) mu0 H).  That homogenisation is the IN-PLANE one and it is all a
    # 2D model can have; in 3D the same k_f also fixes the permeability ACROSS
    # the stack, which is a different (series) average — see solver.axial_mu.
    # Same material, same number, one place: whatever k_f the 2D curve was built
    # with is the k_f the 3D anisotropy uses.
    kf_stator = _stack_factor("stator_core", float(m_stator.mu_r))
    kf_rotor = _stack_factor("rotor_core", float(m_rotor.mu_r))
    _add("stator", clipped.get("stator"), mu_r=float(m_stator.mu_r),
         bh_curve=list(m_stator.bh_curve or []) or None, kind="iron",
         stack_kf=kf_stator)
    _add("rotor", clipped.get("rotor"), mu_r=float(m_rotor.mu_r),
         bh_curve=list(m_rotor.bh_curve or []) or None, kind="iron",
         stack_kf=kf_rotor)
    _add("shaft", clipped.get("shaft"), mu_r=float(m_shaft.mu_r),
         bh_curve=list(m_shaft.bh_curve or []) or None,
         kind="iron" if m_shaft.bh_curve else "shaft")

    # Clipped magnets keep the FULL magnet's direction (matched by centroid).
    for j, (mp, _pol) in enumerate(clipped.get("magnets", [])):
        cx, cy = mp.centroid.x, mp.centroid.y
        k = int(np.argmin([(cx - a) ** 2 + (cy - b) ** 2
                           for a, b, _, _ in mag_dirs]))
        _, _, Mx, My = mag_dirs[k]
        _add(f"magnet_{j}", mp, mu_r=mu_rec, M=(Mx, My, 0.0), kind="magnet")

    # Slots / copper / insulation are NON-MAGNETIC and carry no current at
    # I = 0, so they are air and are not meshed as separate regions at all.
    # (Meshing them would only add dofs that solve the same air problem.)

    try:
        from motor_ai_sim.routes.simulation import _geometry_fingerprint
        fp = _geometry_fingerprint(geo_override)
    except Exception:
        fp = ""
    try:
        from motor_ai_sim.config import get_material_assignments
        assigns = dict(get_material_assignments() or {})
    except Exception:
        assigns = {}

    return MotorSection(
        geo=p, regions=regions, n_sectors=n_sec,
        sector_deg=360.0 / n_sec, antiperiodic=anti,
        r_stator_out_mm=float(p["stator_outer_radius"]),
        r_stator_in_mm=float(p["stator_inner_radius"]),
        r_rotor_out_mm=float(p["rotor_outer_radius"]),
        r_rotor_in_mm=float(p["rotor_inner_radius"]),
        r_shaft_in_mm=float(p["shaft_inner_radius"]),
        mid_r_mm=float(polys["mid_r_mm"]),
        stack_mm=float(stack_mm if stack_mm else p["motor_length"]),
        num_slots=num_slots, num_poles=num_poles, pole_pairs=num_poles // 2,
        Br_T=Br, mu_rec=mu_rec, fingerprint=fp,
        materials={k: v for k, v in assigns.items()
                   if k in ("stator_core", "rotor_core", "magnet", "shaft")},
        pocket_sliver_mm2=float(sliver),
        polys_full=polys)
