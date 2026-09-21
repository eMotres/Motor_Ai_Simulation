"""Real geometry validation — does the cross-section the solver is about to mesh
describe a machine that can physically exist?

``geometry_constraints`` answers "is this ONE knob within its analytic bound?"
and CLAMPS it.  That is a scalar guard: it knows nothing about where the parts
actually end up.  This module answers the other question — "do the finished
regions overlap, escape their host, or collapse?" — by operating on the SAME
2-D Shapely polygons the mesher consumes
(:meth:`CadQueryMotor.get_2d_polygons`).  Sharing that one source is the whole
point: validation and reality cannot drift, because there is only one geometry.

Why it matters: CadQuery happily builds intersecting polygons, gmsh happily
meshes them (one region wins each triangle, the loser silently loses its
material and its current), and the FEM happily returns torque, losses and
efficiency for a machine nobody could build.  A wrong answer with no error
message is the worst failure mode this codebase has.

Severity
--------
``error``   the cross-section is not buildable — the numbers from a solve would
            be meaningless.  Solve paths refuse; the Geometry tab still SAVES
            (the user may be mid-edit) and shows the list in red.
``warning`` suspicious but solvable (e.g. the iron falls into disconnected
            islands).  Reported, never blocking.

Every violation is structured JSON — two named parts, the overlap area in mm²,
the worst (x, y) in mm, an engineer-readable sentence and the geometry
parameters most likely to be responsible — so the UI can render a list and the
user knows WHICH knob to turn.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from motor_ai_sim.winding import STRIP_GAP_FACTOR as _GAP_F

# ── tolerances ───────────────────────────────────────────────────────────────
# Neighbouring domains legitimately SHARE boundaries (magnet face against its
# rotor pocket, coil against its liner).  Meshed vertices land on those shared
# edges to within round-off, so an exactly-touching pair can produce a hairline
# intersection of non-zero area.  A pair only counts as overlapping when the
# intersection survives eroding by TOL_MM/2 — i.e. it is a real region, not a
# ~1 µm seam.
TOL_MM = 1e-3            # 1 µm — floor for the shared-boundary tolerance
# …but 1 µm is FINER THAN THE POLYGONS THEMSELVES.  `get_2d_polygons` ends with
# a ring sanitize whose point-weld may move a boundary by up to
# machine_diameter / _WELD_DIV (0.0375 mm on a 150 mm machine, 0.010 mm on a
# 40 mm one).  Two domains that came out of a shapely *difference* — the magnets
# and the rotor-side air band are exactly that, so their overlap is zero by
# construction — are welded SEPARATELY, and the few-µm disagreement that leaves
# behind is not a design defect: it is the resolution of the representation.
# Judged at 1 µm it looked like one, and a sweep threw away 10 of 32 CIANO28
# designs for ~0.03 mm² of it (≈1e-5 of the magnet area, ~4 µm deep).  So the
# default tolerance is the builder's own weld tolerance, and anything thinner
# than that is not geometry this pipeline can even express.  (The mesher then
# runs a 0.3 mm Douglas–Peucker on the same rings — 8x looser again.)
_WELD_DIV_FALLBACK = 4000.0
AREA_TOL_MM2 = 1e-4      # 0.0001 mm² — below this nothing is worth reporting
AREA_FLOOR_MM2 = 1e-3    # a solid part smaller than this is a degenerate sliver
# Conductor cross-section is PHYSICS: R_dc, J, the I²R loss and the AC/proximity
# loss all scale with it, and the winding source is normalised by the copper the
# MESH carries.  A conductor polygon is only flagged as short of its nominal
# rectangle when it misses by more than this — round-off and the ~1 µm seams the
# coil corners share with the liner are not a copper defect.
COPPER_TOL = 5e-3        # 0.5 % of the nominal conductor area

_SEV_ERROR = "error"
_SEV_WARNING = "warning"


# ─────────────────────────────────────────────────────────────────────────────
# Result types
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Violation:
    """One concrete geometric defect, ready for the UI."""
    code: str                       # machine id, e.g. "coil_overlaps_coil"
    severity: str                   # "error" | "warning"
    part_a: str                     # human name, e.g. "Magnet 3 (θ=25.7°)"
    part_b: str                     # "" for single-part violations
    message: str                    # full engineer-readable sentence
    overlap_area_mm2: float = 0.0   # 0 for non-overlap defects
    x_mm: Optional[float] = None    # worst location
    y_mm: Optional[float] = None
    likely_params: List[str] = field(default_factory=list)
    measured_mm: Optional[float] = None   # e.g. the negative air gap
    limit_mm: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "code": self.code,
            "severity": self.severity,
            "part_a": self.part_a,
            "part_b": self.part_b,
            "message": self.message,
            "overlap_area_mm2": round(float(self.overlap_area_mm2), 6),
            "likely_params": list(self.likely_params),
        }
        if self.x_mm is not None and self.y_mm is not None:
            d["location_mm"] = {"x": round(float(self.x_mm), 4),
                                "y": round(float(self.y_mm), 4)}
        if self.measured_mm is not None:
            d["measured_mm"] = round(float(self.measured_mm), 5)
        if self.limit_mm is not None:
            d["limit_mm"] = round(float(self.limit_mm), 5)
        return d


@dataclass
class ValidationResult:
    """Everything the validator found, plus the numbers worth reporting even
    when nothing is wrong (minimum air-gap clearance)."""
    violations: List[Violation] = field(default_factory=list)
    min_air_gap_mm: Optional[float] = None
    hidden: Dict[str, int] = field(default_factory=dict)   # code → n not listed
    checks_run: List[str] = field(default_factory=list)

    @property
    def errors(self) -> List[Violation]:
        return [v for v in self.violations if v.severity == _SEV_ERROR]

    @property
    def warnings(self) -> List[Violation]:
        return [v for v in self.violations if v.severity == _SEV_WARNING]

    @property
    def ok(self) -> bool:
        """True when nothing BLOCKS a solve (warnings do not block)."""
        return not self.errors

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "n_errors": len(self.errors),
            "n_warnings": len(self.warnings),
            "min_air_gap_mm": (None if self.min_air_gap_mm is None
                               else round(float(self.min_air_gap_mm), 4)),
            "violations": [v.to_dict() for v in self.violations],
            "hidden": dict(self.hidden),
            "checks_run": list(self.checks_run),
        }

    def summary(self, limit: int = 6) -> str:
        """One-line-per-violation text — for exception messages and logs."""
        errs = self.errors
        if not errs:
            return "geometry valid"
        head = ("{} geometry violation{} — the cross-section is not buildable, "
                "so a solve would describe a machine that does not exist:"
                .format(len(errs), "" if len(errs) == 1 else "s"))
        lines = ["  • " + v.message for v in errs[:limit]]
        if len(errs) > limit:
            lines.append("  • … and {} more".format(len(errs) - limit))
        return "\n".join([head] + lines)


class GeometryInvalid(ValueError):
    """Raised by the solve paths.  Carries the structured result so the route
    can turn it into an HTTP 422 with the full violation list."""

    def __init__(self, result: ValidationResult):
        super().__init__(result.summary())
        self.result = result

    def to_dict(self) -> Dict[str, Any]:
        return self.result.to_dict()


# ─────────────────────────────────────────────────────────────────────────────
# Geometry helpers (shapely — already a hard runtime dependency)
# ─────────────────────────────────────────────────────────────────────────────
def _parts(geom) -> List[Any]:
    """Flatten a Polygon / MultiPolygon / GeometryCollection to polygon parts."""
    if geom is None or getattr(geom, "is_empty", True):
        return []
    gt = getattr(geom, "geom_type", "")
    if gt == "Polygon":
        return [geom]
    if gt in ("MultiPolygon", "GeometryCollection"):
        return [g for g in geom.geoms if getattr(g, "geom_type", "") == "Polygon"]
    return []


def _all_coords(geom) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    for part in _parts(geom):
        out.extend(list(part.exterior.coords))
        for ring in part.interiors:
            out.extend(list(ring.coords))
    return out


def _radii(geom) -> Tuple[float, float]:
    """(min radius, max radius) over every vertex of ``geom``.  (inf, -inf) if
    the geometry is empty."""
    rs = [math.hypot(x, y) for x, y in _all_coords(geom)]
    return (min(rs), max(rs)) if rs else (math.inf, -math.inf)


def _theta_deg(geom) -> float:
    c = geom.centroid
    return math.degrees(math.atan2(c.y, c.x)) % 360.0


def _significant(inter, tol_mm: float) -> bool:
    """True when an intersection is a real region rather than a shared edge.

    Area alone is not enough: a 5 mm long, 1 µm wide seam has 5e-3 mm² and is
    still just a shared boundary.  Eroding by tol/2 removes anything thinner
    than the tolerance and keeps everything genuinely two-dimensional."""
    if inter is None or inter.is_empty or inter.area <= AREA_TOL_MM2:
        return False
    try:
        return not inter.buffer(-0.5 * tol_mm).is_empty
    except Exception:
        return True


def _worst_point(inter) -> Tuple[Optional[float], Optional[float]]:
    """A point inside the LARGEST piece of the intersection — 'where'."""
    ps = _parts(inter)
    if not ps:
        return None, None
    big = max(ps, key=lambda g: g.area)
    try:
        p = big.representative_point()
        return float(p.x), float(p.y)
    except Exception:
        c = big.centroid
        return float(c.x), float(c.y)


def _pairs_intersecting(geoms: Sequence[Any]) -> List[Tuple[int, int]]:
    """Index pairs (i < j) whose bounding geometries intersect.  Uses an STRtree
    so a 168-conductor winding costs a spatial query, not 14 000 boolean ops."""
    n = len(geoms)
    if n < 2:
        return []
    try:
        from shapely import STRtree
        tree = STRtree(list(geoms))
        res = tree.query(list(geoms), predicate="intersects")
        return sorted({(int(a), int(b)) for a, b in zip(res[0], res[1]) if int(a) < int(b)})
    except Exception:
        return [(i, j) for i in range(n) for j in range(i + 1, n)]


# ─────────────────────────────────────────────────────────────────────────────
# Part naming — an engineer reads "Magnet 3", not "magnets[2]"
# ─────────────────────────────────────────────────────────────────────────────
def _name_magnet(i: int, poly) -> str:
    return "Magnet {} (θ={:.1f}°)".format(i + 1, _theta_deg(poly))


def _name_coil(i: int, poly) -> str:
    c = poly.centroid
    return "Coil conductor {} (θ={:.1f}°, r={:.1f} mm)".format(
        i + 1, _theta_deg(poly), math.hypot(c.x, c.y))


def _name_liner(i: int, poly) -> str:
    return "Insulation {} (θ={:.1f}°)".format(i + 1, _theta_deg(poly))


_ROTOR = "the rotor core"
_STATOR = "the stator core"
_SHAFT = "the shaft"
_SLEEVE = "the retaining sleeve"
_AIR_IN = "the rotor-side air region (pockets + inner half of the air gap)"
_AIR_OUT = "the stator-side air region (slot openings + outer half of the air gap)"


# Which geometry parameters most plausibly cause each defect.  The polygons are
# built from named parameters, so this maps a defect straight back to the knob.
_CAUSES: Dict[str, List[str]] = {
    "magnet_overlaps_rotor": [
        "magnet_height", "magnet_fill_up", "magnet_fill_down",
        "magnet_down_height", "magnet_up_gap", "rotor_house_height",
        "magnet_fill_radius"],
    "magnet_overlaps_shaft": [
        "rotor_house_height", "magnet_down_height", "shaft_height",
        "magnet_height"],
    "magnet_overlaps_magnet": [
        "magnet_fill_down", "magnet_fill_up", "magnet_down_height",
        "num_poles_per_segment", "num_seg"],
    "magnet_overlaps_stator": [
        "air_gap", "magnet_up_gap", "magnet_height"],
    "magnet_outside_rotor": [
        "magnet_height", "magnet_up_gap", "rotor_house_height",
        "magnet_down_height"],
    "magnet_in_rotor_air": [
        "magnet_height", "magnet_fill_up", "rotor_hole"],
    "coil_overlaps_stator": [
        "wire_width", "wire_height", "num_wires_per_slot", "tooth_width",
        "slot_height", "insulation_thickness", "wire_spacing_x"],
    "coil_overlaps_liner": [
        "insulation_thickness", "wire_width", "wire_spacing_x",
        "wire_spacing_y"],
    "coil_overlaps_coil": [
        "wire_width", "wire_spacing_x", "tooth_width", "tooth2_width",
        "num_slots_per_segment", "num_seg"],
    "coil_outside_slot_bore": [
        "slot_height", "num_wires_per_slot", "wire_height", "wire_spacing_y",
        "insulation_thickness"],
    "coil_outside_stator_od": [
        "core_thickness", "slot_height", "stator_diameter"],
    "coil_in_stator_air": [
        "wire_width", "wire_height", "num_wires_per_slot", "slot_height"],
    "winding_does_not_fit": [
        "num_wires_per_slot", "wire_height", "wire_spacing_y", "slot_height",
        "insulation_thickness"],
    # The TANGENTIAL twin of winding_does_not_fit: the wire column (copper plus
    # the wire_split gaps) is wider than the tooth pitch leaves room for.
    "winding_split_does_not_fit": [
        "wire_split", "wire_width", "wire_spacing_x", "tooth_width",
        "tooth2_width", "insulation_thickness", "cut_width",
        "num_slots_per_segment", "num_seg"],
    "winding_clipped_by_slot": [
        "wire_width", "wire_height", "slot_hs", "num_wires_per_slot",
        "slot_height", "wire_spacing_y", "insulation_thickness"],
    "winding_copper_double_counted": [
        "wire_width", "wire_spacing_x", "tooth_width", "tooth2_width",
        "num_slots_per_segment", "num_seg"],
    "rotor_crosses_air_gap": [
        "air_gap", "magnet_up_gap", "rotor_fill_r", "sleeve_thickness"],
    "sleeve_fills_air_gap": ["sleeve_thickness", "air_gap"],
    "sleeve_overlaps_stator": ["sleeve_thickness", "air_gap"],
    "rotor_pocket_tabs_zero_thickness": ["rotor_hole", "magnet_up_gap"],
    "stator_crosses_air_gap": [
        "air_gap", "slot_height", "core_thickness", "stator_diameter"],
    "air_gap_not_positive": [
        "air_gap", "stator_diameter", "core_thickness", "slot_height"],
    "rotor_overlaps_shaft": ["shaft_height", "rotor_house_height",
                             "magnet_height"],
    "rotor_overlaps_stator": ["air_gap", "stator_diameter", "core_thickness",
                              "slot_height"],
    "self_intersection": [],
    "degenerate_area": [],
    "empty_domain": [],
    "iron_disconnected": ["magnet_up_gap", "magnet_height", "rotor_hole",
                          "magnet_fill_up"],
}


# ─────────────────────────────────────────────────────────────────────────────
# The validator
# ─────────────────────────────────────────────────────────────────────────────
class _Collector:
    """Accumulates violations, capping how many of the SAME kind are listed so a
    winding with 96 identical clashes does not flood the UI."""

    def __init__(self, max_per_code: int):
        self.max_per_code = int(max_per_code)
        self._count: Dict[str, int] = {}
        self.violations: List[Violation] = []
        self.hidden: Dict[str, int] = {}

    def add(self, v: Violation) -> None:
        n = self._count.get(v.code, 0)
        self._count[v.code] = n + 1
        if n < self.max_per_code:
            self.violations.append(v)
        else:
            self.hidden[v.code] = self.hidden.get(v.code, 0) + 1

    def sort(self) -> None:
        # errors first, then by overlap size (worst first) — the UI reads top-down
        self.violations.sort(
            key=lambda v: (v.severity != _SEV_ERROR, -float(v.overlap_area_mm2)))


def _overlap_violation(code: str, name_a: str, name_b: str, inter,
                       phrase: str, severity: str = _SEV_ERROR) -> Violation:
    x, y = _worst_point(inter)
    area = float(inter.area)
    loc = "" if x is None else " near ({:.2f}, {:.2f}) mm".format(x, y)
    return Violation(
        code=code, severity=severity, part_a=name_a, part_b=name_b,
        # "{a} and {b} overlap" reads correctly whether the parts are singular
        # ("Magnet 3") or a group ("The magnets") — "{a} overlaps {b}" does not.
        message="{} and {} overlap by {:.3f} mm²{}. {}".format(
            name_a, name_b, area, loc, phrase),
        overlap_area_mm2=area, x_mm=x, y_mm=y,
        likely_params=_CAUSES.get(code, []))


def sleeve_gap_error(geo: Dict[str, Any]) -> Optional[Tuple[str, str, str]]:
    """``(field, code, message)`` when the retaining sleeve does not fit the air
    gap, else ``None``.

    ONE wording, shared by the three places that refuse a sleeve — the Geometry
    tab's PUT (``validate_parameter_values``), the polygon validator that gates
    every solve (``validate_polygons``) and the Geometry tab's red line under
    the field (which renders whichever of the two reaches it).  A design
    rejected in three places must not be explained three different ways.

    Two rules, reported in the order an engineer hits them:

      A. ``sleeve_thickness < air_gap``, strictly.  A ring as thick as the gap
         reaches through it and rubs on the stator bore; a thicker one is not a
         machine at all.
      B. what is LEFT, ``air_gap − sleeve_thickness``, is the real mechanical
         gap, and the transient solver's sliding band has to be meshed inside
         it — see geometry_constraints.MIN_MECH_GAP_MM for where the number
         comes from.

    B implies A, but they are separate messages on purpose: "your ring is
    thicker than your gap" and "your ring leaves the solver nowhere to put the
    band" are different mistakes with different fixes.
    """
    from motor_ai_sim.geometry_constraints import MIN_MECH_GAP_MM
    t = _num(geo.get("sleeve_thickness"))
    gap = _num(geo.get("air_gap"))
    if t is None or gap is None or t <= 0.0 or gap <= 0.0:
        return None                       # no sleeve, or the gap is already an
                                          # error some other rule owns
    t_max = max(0.0, gap - MIN_MECH_GAP_MM)
    if t >= gap:
        return ("sleeve_thickness", "sleeve_fills_air_gap",
                "sleeve_thickness ({:g} mm) is not thinner than air_gap "
                "({:g} mm): the retaining sleeve sits ON the rotor OD, INSIDE "
                "the air gap, so it would reach across the whole gap and rub "
                "on the stator bore. The sleeve must leave at least {:g} mm of "
                "mechanical clearance for the rotor to turn and for the "
                "sliding band to be meshed, so sleeve_thickness must be "
                "{:.3f} mm or less here — or open air_gap to more than "
                "{:.3f} mm.".format(t, gap, MIN_MECH_GAP_MM, t_max,
                                    t + MIN_MECH_GAP_MM))
    if (gap - t) < MIN_MECH_GAP_MM:
        return ("sleeve_thickness", "sleeve_fills_air_gap",
                "sleeve_thickness ({:g} mm) leaves only {:.3f} mm of mechanical "
                "gap between the sleeve OD and the stator bore (air_gap "
                "{:g} mm). The transient solver's sliding band needs at least "
                "{:g} mm there, so sleeve_thickness must be {:.3f} mm or less "
                "here — or open air_gap to at least {:.3f} mm."
                .format(t, gap - t, gap, MIN_MECH_GAP_MM, t_max,
                        t + MIN_MECH_GAP_MM))
    return None


def rotor_hole_gap_error(geo: Dict[str, Any]) -> Optional[Tuple[str, str, str]]:
    """``(field, code, message)`` when the rotor pocket opening is narrower than
    the magnet while the magnet sits flush with the rotor OD, else ``None``.

    ``rotor_hole < 1`` cuts a RECTANGULAR opening narrower than the magnet
    (``cadquery_geometry._pocket_cut_depth`` / ``rec_w``); what is left of the
    pocket wall between the rectangle's edge and the magnet's own top corner is
    the retaining "tab" of iron over that corner.  With ``magnet_up_gap = 0``
    the magnet's top arc already sits ON the rotor OD, so that tab has ZERO
    radial thickness — there is no iron left between the narrower opening and
    the circle the magnet's corners sit on.

    Measured on the Ø50 fixture (``rotor_hole=0.9``, ``magnet_up_gap=0``,
    ``tests/test_pocket_straight_sides.py::GEO_50``): the ring sanitiser welds
    5.594e-4 mm² of the magnet into the rotor there — a real overlap, not a
    weld seam (pre-existing, `tests/test_pocket_straight_sides.py`
    ``HOLE_GAP_MATRIX``).  ``magnet_up_gap > 0`` gives the tab its thickness
    back (the magnet's top arc then sits ``magnet_up_gap`` short of the OD, so
    the opening's edge finds iron before it reaches the magnet), and
    ``rotor_hole >= 1`` has no rectangle to open at all
    (``cadquery_geometry._extended_pocket``) — so this fires on the one
    combination only.

    A SCALAR rule, deliberately (same reasoning as ``sleeve_gap_error``): it
    needs no polygon build to know the tab is gone, and it must gate every
    solve even where the polygon build itself still "succeeds" by welding the
    sliver away instead of raising.
    """
    hole = _num(geo.get("rotor_hole"))
    if hole is None or hole >= 1.0 - 1e-9:
        return None
    gap = _num(geo.get("magnet_up_gap"))
    gap = gap if gap is not None else 0.0
    if gap > 1e-9:
        return None
    return ("rotor_hole", "rotor_pocket_tabs_zero_thickness",
            "magnet_up_gap = 0 needs an opening at least as wide as the magnet: "
            "with rotor_hole = {:g} (< 1) the pocket opening is narrower than "
            "the magnet, and with magnet_up_gap = 0 the magnet's top arc "
            "already sits on the rotor OD, so the retaining tabs of iron over "
            "the magnet's corners would have ZERO radial thickness there. Set "
            "rotor_hole >= 1 (the straight-sided pocket, no rectangle) or give "
            "the bridge a thickness with magnet_up_gap > 0.".format(hole))


def slot_cut_limits(geo: Dict[str, Any]) -> Optional[Tuple[float, float, int]]:
    """``(cut_x, cut_x_max, wire_split)`` [mm] for this geometry, or ``None``.

    ``cut_x`` is where ``cadquery_geometry`` puts the slot's OUTER wall —
    ``tooth_width/2 + 2·insulation + wire_column + 2·wire_spacing_x +
    tooth2_width``, with ``wire_column = wire_split·wire_width +
    (wire_split−1)·2·wire_spacing_x`` — and
    ``cut_x_max = (stator_inner_radius + cut_width)·
    sin(slot_angle/2)`` is where the half-sector wedge is at the slot-bottom
    radius.  The builder's slot-mouth fillet is
    ``(cut_x_max − cut_x)/(1 − sin(slot_angle/2))``, so ``cut_x ≥ cut_x_max``
    is the point at which two neighbouring cutters meet and the tooth between
    them stops existing.

    ONE arithmetic, used by the parameter validator (which refuses the edit) and
    available to anything else that needs to ask "how much room is left across
    the slot".  ``None`` when a key it needs is missing or unusable — a partial
    edit is judged by the rules that CAN see their inputs.
    """
    # The PRIMARY inputs first: ``num_slots`` is derived from them, and on a
    # PUT that changes ``num_seg`` the dict still carries the OLD product when
    # this runs — judged on it, a 12 -> 24 slot edit was accepted and only the
    # NEXT edit of anything else was refused (tests/test_api.py, 2026-09-08).
    seg, sps = _num(geo.get("num_seg")), _num(geo.get("num_slots_per_segment"))
    ns = (seg * sps) if (seg and sps and seg > 0 and sps > 0) else None
    if not ns or ns <= 0:
        ns = _num(geo.get("num_slots"))
    od, ct, sh = (_num(geo.get("stator_diameter")), _num(geo.get("core_thickness")),
                  _num(geo.get("slot_height")))
    tw, t2w = _num(geo.get("tooth_width")), _num(geo.get("tooth2_width"))
    ww, dx = _num(geo.get("wire_width")), _num(geo.get("wire_spacing_x"))
    ins, cw = (_num(geo.get("insulation_thickness")), _num(geo.get("cut_width")))
    if None in (ns, od, ct, sh, tw, t2w, ww, dx, ins, cw) or ns < 2:
        return None
    inner_r = od / 2.0 - ct - sh
    if inner_r <= 0.0:
        return None                       # a bore rule already owns this
    n_split = int(round(_num(geo.get("wire_split")) or 1))
    n_split = n_split if n_split >= 1 else 1
    # SAME arithmetic as cadquery_geometry._strip_span — N strips of wire_width
    # with STRIP_GAP_FACTOR·wire_spacing_x between them.
    column = ww if n_split <= 1 else n_split * ww + (n_split - 1) * _GAP_F * dx
    cut_x = tw / 2.0 + 2.0 * ins + column + 2.0 * dx + t2w
    half_slots = max(1, int(round(ns)) // 2)
    cut_x_max = (inner_r + cw) * math.sin(math.radians(360.0 / half_slots / 2.0))
    return float(cut_x), float(cut_x_max), n_split


def split_width_error(geo: Dict[str, Any]) -> Optional[Tuple[str, str, float]]:
    """``(field, message, bound)`` when the winding no longer fits ACROSS the
    slot, else ``None``.

    The tangential counterpart of the radial ``winding_does_not_fit``.  Fires on
    ``wire_split`` when there is a split — its extra strips and their (N−1) gaps
    of 2·wire_spacing_x are what just widened the column, and the fix is a
    smaller N — and on ``wire_width`` otherwise.  The ``bound`` is the largest
    value that field may take with everything else held: an integer strip count,
    or a width in mm.
    """
    lim = slot_cut_limits(geo)
    if lim is None:
        return None
    cut_x, cut_x_max, n_split = lim
    if cut_x < cut_x_max - 1e-9:
        return None
    dx = _num(geo.get("wire_spacing_x")) or 0.0
    ww = _num(geo.get("wire_width")) or 0.0
    gap = _GAP_F * dx
    over = cut_x - cut_x_max
    if n_split >= 2 and (ww + gap) > 0.0:
        # Each strip removed gives back one strip width AND one gap, so the
        # largest N that still fits is N − over/(wire_width + gap).  n_max may
        # be 1 — "no split at all".
        n_max = max(1, int(math.floor(n_split - over / (ww + gap) - 1e-9)))
        # The wire_width that WOULD fit at this N — named first, because
        # splitting a bar in two means halving the wire (user 2026-09-08: "сделай
        # ширину провода 4,5 мм"), and "use wire_split = 1" is the fix that
        # undoes what the user just asked for.
        w_fit = max(0.0, ww - over / n_split)
        return ("wire_split",
                "wire_split = {:d} does not fit across the slot: {:d} strips of "
                "wire_width = {:g} mm need {:d} insulation gap(s) of "
                "2 x wire_spacing_x = {:g} mm between them, so the wire column "
                "is {:g} mm wide and the slot wall lands at {:.4f} mm — past "
                "the {:.4f} mm the tooth pitch leaves for it, by {:.4f} mm. The "
                "slot would cut into its neighbour and the tooth between them "
                "would be gone. Splitting a wire means NARROWING it: set "
                "wire_width to {:.4f} mm or less to keep {:d} strips (halving a "
                "bar is the usual intent). Otherwise use wire_split = {:d} or "
                "less here, or make room: a smaller tooth2_width or more slot "
                "pitch."
                .format(n_split, n_split, ww, n_split - 1, gap,
                        n_split * ww + (n_split - 1) * gap,
                        cut_x, cut_x_max, over, w_fit, n_split, n_max),
                float(n_max))
    w_max = max(0.0, ww - over / max(1, n_split))
    return ("wire_width",
            "wire_width = {:g} mm does not fit across the slot: the slot wall "
            "lands at {:.4f} mm and the tooth pitch leaves only {:.4f} mm — "
            "{:.4f} mm too far, so the slot would cut into its neighbour. "
            "wire_width must be {:.4f} mm or less here."
            .format(ww, cut_x, cut_x_max, over, w_max),
            float(w_max))


def weld_tol_mm(params: Optional[Dict[str, Any]] = None,
                polys: Optional[Dict[str, Any]] = None) -> float:
    """The tolerance the polygons were BUILT to — machine diameter / _WELD_DIV.

    Same number `cadquery_geometry._sanitize_geom` welds with, so validation
    judges the rings at the accuracy they actually carry.  Falls back to the
    stator OD read off the polygons, then to TOL_MM.
    """
    try:
        from motor_ai_sim.cadquery_geometry import _WELD_DIV as _div
    except Exception:
        _div = _WELD_DIV_FALLBACK
    scale = 0.0
    try:
        scale = 2.0 * float((params or {}).get("stator_outer_radius", 0.0) or 0.0)
        if scale <= 0.0:
            scale = float((params or {}).get("stator_diameter", 0.0) or 0.0)
        if scale <= 0.0 and polys is not None:
            st = polys.get("stator")
            if st is not None and not st.is_empty:
                scale = 2.0 * _radii(st)[1]
    except Exception:
        scale = 0.0
    return max(TOL_MM, scale / _div) if scale > 0.0 else TOL_MM


def validate_polygons(polys: Dict[str, Any],
                      params: Optional[Dict[str, Any]] = None,
                      tol_mm: Optional[float] = None,
                      max_per_code: int = 6) -> ValidationResult:
    """Validate the polygon dict returned by ``CadQueryMotor.get_2d_polygons``.

    ``params`` is the resolved geometry parameter dict (``motor.parameters``);
    it supplies the nominal radii used by the containment checks.  Absent, those
    radii are inferred from the polygons themselves.

    ``tol_mm`` defaults to the builder's weld tolerance (see ``TOL_MM``) — pass
    a number only to judge the rings finer or coarser than they were built.
    """
    p = dict(params or {})
    if tol_mm is None:
        tol_mm = weld_tol_mm(p, polys)
    col = _Collector(max_per_code)
    checks: List[str] = []

    stator = polys.get("stator")
    rotor = polys.get("rotor")
    shaft = polys.get("shaft")
    # None on every machine without a retaining ring, which is all of them but
    # the Ø200 — so every union below is unchanged there.
    sleeve = polys.get("sleeve")
    magnets = [mp for mp, _pol in (polys.get("magnets") or [])]
    coils = list(polys.get("coils") or [])
    liners = list(polys.get("slot_insulation") or [])
    in_band = polys.get("in_band")
    out_band = polys.get("out_band")

    from shapely.ops import unary_union

    def _u(geoms):
        gs = [g for g in geoms if g is not None and not g.is_empty]
        return unary_union(gs) if gs else None

    # ── 0. degenerate / invalid / empty domains ──────────────────────────────
    checks.append("polygon_sanity")
    named_solids: List[Tuple[str, Any]] = [
        (_STATOR.capitalize(), stator), (_ROTOR.capitalize(), rotor),
        (_SHAFT.capitalize(), shaft)]
    named_solids += [(_name_magnet(i, m), m) for i, m in enumerate(magnets)]
    named_solids += [(_name_coil(i, c), c) for i, c in enumerate(coils)]

    for name, g in named_solids:
        if g is None or g.is_empty:
            col.add(Violation(
                code="empty_domain", severity=_SEV_ERROR, part_a=name, part_b="",
                message="{} is empty — the parameters produced no material at "
                        "all, so the solver would mesh a hole where a part "
                        "should be.".format(name),
                likely_params=_CAUSES["empty_domain"]))
            continue
        if not g.is_valid:
            try:
                from shapely.validation import explain_validity
                why = explain_validity(g)
            except Exception:
                why = "self-intersecting boundary"
            c = g.centroid
            col.add(Violation(
                code="self_intersection", severity=_SEV_ERROR, part_a=name,
                part_b="",
                message="{} is not a simple region ({}). Its outline crosses "
                        "itself, so the mesher cannot tell inside from "
                        "outside.".format(name, why),
                x_mm=float(c.x), y_mm=float(c.y),
                likely_params=_CAUSES["self_intersection"]))
        for part in _parts(g):
            if part.area <= AREA_FLOOR_MM2:
                q = part.representative_point()
                # A fragment that has collapsed to literally zero area is a
                # boolean round-off artefact: the mesher drops it and the
                # physics is unaffected, so it is reported, not blocking.  A
                # fragment with real-but-tiny area is worse — it survives into
                # the mesh as degenerate elements.
                collapsed = part.area <= 1e-9
                col.add(Violation(
                    code="degenerate_area",
                    severity=_SEV_WARNING if collapsed else _SEV_ERROR,
                    part_a=name, part_b="",
                    message=(
                        "{} contains a fragment that has collapsed to zero area "
                        "near ({:.2f}, {:.2f}) mm — a feature (usually a "
                        "bridge or a pocket wall) has been cut down to nothing "
                        "there. The mesher drops it, so the solve is not "
                        "affected, but the shape is not what the parameters "
                        "describe.".format(name, q.x, q.y)
                        if collapsed else
                        "{} contains a degenerate fragment of {:.6f} mm² near "
                        "({:.2f}, {:.2f}) mm (floor {:g} mm²) — a sliver this "
                        "thin meshes as garbage elements.".format(
                            name, part.area, q.x, q.y, AREA_FLOOR_MM2)),
                    overlap_area_mm2=float(part.area),
                    x_mm=float(q.x), y_mm=float(q.y),
                    likely_params=_CAUSES["degenerate_area"]))

    if not magnets:
        col.add(Violation(
            code="empty_domain", severity=_SEV_ERROR, part_a="The magnets",
            part_b="",
            message="No magnet regions were produced — the rotor has no "
                    "excitation, so any torque the solver reports is not from "
                    "this machine.",
            likely_params=["magnet_height", "magnet_fill_up",
                           "magnet_fill_down"]))
    if not coils:
        col.add(Violation(
            code="empty_domain", severity=_SEV_ERROR, part_a="The winding",
            part_b="",
            message="No coil regions were produced — no conductor can carry the "
                    "phase current, so the solve has no armature field.",
            likely_params=["num_wires_per_slot", "wire_width", "wire_height",
                           "slot_height"]))

    # ── 1. winding that silently lost turns ──────────────────────────────────
    checks.append("winding_fit")
    n_req = int(polys.get("n_wires_requested") or 0)
    n_fit = int(polys.get("n_wires_fit") or 0)
    if polys.get("coils_overflow") and n_req > n_fit:
        col.add(Violation(
            code="winding_does_not_fit", severity=_SEV_ERROR,
            part_a="The winding", part_b="the slot",
            message="Only {} of the {} requested conductors per slot fit before "
                    "the stack would cross the stator bore; the remaining {} "
                    "were dropped, so the machine that gets solved has fewer "
                    "turns than the one you specified.".format(
                        n_fit, n_req, n_req - n_fit),
            measured_mm=None, likely_params=_CAUSES["winding_does_not_fit"]))

    # ── 1a. the wire COLUMN across the slot (wire_split's gaps) ──────────────
    # Same measurement the builder made when it cut the slot, carried out in the
    # polys dict so the two cannot drift.  ERROR, not a warning: past this bound
    # the slot cutter meets its neighbour, the mouth fillet radius goes negative
    # and the stator polygon is no longer a lamination.
    checks.append("winding_split_fit")
    _cut_x = float(polys.get("slot_cut_x_mm") or 0.0)
    _cut_x_max = float(polys.get("slot_cut_x_max_mm") or 0.0)
    _n_split = int(polys.get("wire_split") or 1)
    if _cut_x > 0.0 and _cut_x_max > 0.0 and _cut_x >= _cut_x_max - 1e-9:
        _column = float(polys.get("wire_column_mm") or 0.0)
        _strip = float(polys.get("strip_width_mm") or 0.0)
        _what = ("{:d} strips of {:g} mm plus {:d} gap(s) of 2 x wire_spacing_x"
                 .format(_n_split, _strip, _n_split - 1)
                 if _n_split > 1 else "one wire")
        _causes = list(_CAUSES["winding_split_does_not_fit"])
        if _n_split <= 1:
            _causes = [c for c in _causes if c != "wire_split"]
        col.add(Violation(
            code="winding_split_does_not_fit", severity=_SEV_ERROR,
            part_a="The winding", part_b="the slot",
            message="{}the wire column is {:.4f} mm wide ({}), which puts the "
                    "slot wall at {:.4f} mm — {:.4f} mm past the {:.4f} mm the "
                    "tooth pitch leaves for it. The slot cuts into its "
                    "neighbour and the tooth between them is gone, so the "
                    "stator is no longer a lamination.{}"
                    .format("wire_split = {:d}: ".format(_n_split)
                            if _n_split > 1 else "",
                            _column, _what, _cut_x,
                            _cut_x - _cut_x_max, _cut_x_max,
                            # Splitting a wire means NARROWING it, so the fix
                            # that keeps the split is named first and by number.
                            (" Set wire_width to {:.4f} mm or less to keep {:d} "
                             "strips.".format(
                                 max(0.0, _strip
                                     - (_cut_x - _cut_x_max) / _n_split),
                                 _n_split)
                             if _n_split > 1 and _strip > 0.0 else "")),
            measured_mm=float(_cut_x - _cut_x_max), likely_params=_causes))

    # ── 1b. conductor cross-section actually delivered ───────────────────────
    # ``winding_does_not_fit`` only catches a stack that would CROSS THE BORE —
    # i.e. turns dropped whole.  Two other ways to lose copper leave the turn
    # count intact and are invisible without measuring the area:
    #
    #   (a) the builder shrinks the section so the stack fits.  get_2d_polygons
    #       clamps wire_height to (slot_height − 2·insulation)/num_wires −
    #       wire_spacing_y, silently, with no report anywhere.  Measured:
    #       motor_40mm keeps 74.2 % of wire_width·wire_height, m200_20kw_lowripple
    #       74.8 % — every conductor, uniformly.
    #   (b) the conductor polygons INTERPENETRATE.  Their areas then sum to the
    #       nominal while the copper that exists is the UNION, which is less.
    #       Measured: the 37 mm 24s/28p design draws 221.760 mm² of rectangles
    #       covering 203.005 mm² of plane — 8.46 % of the nominal copper is the
    #       same plane counted twice.  The mesher gives each triangle to exactly
    #       one conductor, so the meshed copper is the union (verified to 0.02 %
    #       against the assembled stator mesh), and the winding source, R_2d and
    #       the AC loss all run on that — while copper_loss_W's DC arithmetic
    #       still uses num_wires·wire_width·wire_height.  The two disagree by
    #       exactly this factor.
    #
    # Both are WARNINGS: the cross-section is buildable and the solve is sound
    # for the machine that was BUILT — it is just not the machine the parameters
    # describe.  (b) always comes with the ``coil_overlaps_coil`` ERROR, which
    # names the individual pairs; this one carries the single number the physics
    # depends on, which a capped list of 6 out of 96 pairs cannot show.
    checks.append("winding_copper_area")
    coil_union = _u(coils)
    # NOMINAL section per CONDUCTOR.  With wire_split = N a conductor is one
    # STRIP, and a strip IS wire_width × wire_height — the split does not
    # subdivide the width, it adds N of them per wire row.  So the per-polygon
    # nominal is unchanged and the TOTAL (a_nom_1 × len(coils)) grows by N,
    # which is the whole point of the new rule: N times the copper in a slot
    # that grew to hold it.  Taking the strip width off the POLYS (the builder's
    # own number) rather than off `p` keeps the two from disagreeing when a
    # caller hands in a params dict that predates the split.
    _w_w = _num(polys.get("strip_width_mm")) or _num(p.get("wire_width"))
    _w_h = _num(p.get("wire_height"))
    if coils and _w_w and _w_h and _w_w > 0.0 and _w_h > 0.0:
        a_nom_1 = _w_w * _w_h
        a_nom = a_nom_1 * len(coils)
        a_poly = sum(float(c.area) for c in coils)
        a_real = float(coil_union.area) if coil_union is not None else a_poly
        if a_nom > AREA_TOL_MM2 and a_poly < a_nom * (1.0 - COPPER_TOL):
            worst = min(coils, key=lambda c: c.area)
            q = worst.representative_point()
            col.add(Violation(
                code="winding_clipped_by_slot", severity=_SEV_WARNING,
                part_a="The winding", part_b="the slot",
                message="Winding clipped by slot: kept {:.1f}% of nominal "
                        "conductor area. The {} conductor polygons handed to "
                        "the mesher total {:.3f} mm², against a nominal "
                        "wire_width × wire_height = {:.4f} mm² each "
                        "({:.3f} mm² in all); the smallest keeps {:.1f}% near "
                        "({:.2f}, {:.2f}) mm. Resistance, current density and "
                        "copper loss taken from the nominal rectangle are then "
                        "off by 1/{:.4f} = {:.3f}×."
                        .format(100.0 * a_poly / a_nom, len(coils), a_poly,
                                a_nom_1, a_nom,
                                100.0 * float(worst.area) / a_nom_1,
                                float(q.x), float(q.y),
                                a_poly / a_nom, a_nom / a_poly),
                overlap_area_mm2=a_nom - a_poly,
                x_mm=float(q.x), y_mm=float(q.y),
                likely_params=_CAUSES["winding_clipped_by_slot"]))
        if a_poly > AREA_TOL_MM2 and a_real < a_poly * (1.0 - COPPER_TOL):
            col.add(Violation(
                code="winding_copper_double_counted", severity=_SEV_WARNING,
                part_a="The winding", part_b="itself",
                message="Conductors interpenetrate: only {:.1f}% of the "
                        "{:.3f} mm² of conductor rectangles is distinct copper "
                        "({:.3f} mm² of plane) — {:.3f} mm² is the same area "
                        "counted twice. The mesh gives each element to one "
                        "conductor, so the winding is meshed, excited and "
                        "resisted as the {:.3f} mm² union, while the DC copper "
                        "arithmetic uses the {:.3f} mm² sum."
                        .format(100.0 * a_real / a_poly, a_poly, a_real,
                                a_poly - a_real, a_real, a_poly),
                overlap_area_mm2=a_poly - a_real,
                likely_params=_CAUSES["winding_copper_double_counted"]))

    # ── 2. pairwise disjointness between solid domains ───────────────────────
    checks.append("solid_overlaps")
    mag_union = _u(magnets)

    def _pair(code: str, name_a: str, ga, name_b: str, gb, phrase: str,
              severity: str = _SEV_ERROR) -> None:
        if ga is None or gb is None or ga.is_empty or gb.is_empty:
            return
        try:
            inter = ga.intersection(gb)
        except Exception:
            return
        if _significant(inter, tol_mm):
            col.add(_overlap_violation(code, name_a, name_b, inter, phrase,
                                       severity))

    _pair("magnet_overlaps_rotor", "The magnets", mag_union, _ROTOR, rotor,
          "A magnet and the iron it sits in cannot occupy the same space — "
          "whichever region wins the mesh, the other one loses its material.")
    _pair("magnet_overlaps_shaft", "The magnets", mag_union, _SHAFT, shaft,
          "The magnet stack has been pushed inward past the rotor bore into the "
          "shaft.")
    _pair("magnet_overlaps_stator", "The magnets", mag_union, _STATOR, stator,
          "A magnet reaches across the air gap into the stator — there is no "
          "gap left at that angle.")
    _pair("rotor_overlaps_shaft", _ROTOR.capitalize(), rotor, _SHAFT, shaft,
          "The rotor bore is smaller than the shaft it is mounted on.")
    _pair("rotor_overlaps_stator", _ROTOR.capitalize(), rotor, _STATOR, stator,
          "The rotor and the stator interfere — the air gap is closed.")
    _pair("sleeve_overlaps_stator", _SLEEVE.capitalize(), sleeve, _STATOR,
          stator,
          "The carbon-fibre retaining ring reaches across the air gap into the "
          "stator: sleeve_thickness has eaten the whole gap.")

    _pair("coil_overlaps_stator", "The winding", coil_union, _STATOR, stator,
          "Copper and stator lamination share space; the current density the "
          "solver applies there is applied to iron.")
    _pair("coil_overlaps_liner", "The winding", coil_union, "the insulation",
          _u(liners),
          "Copper and slot insulation share space — the liner is supposed to "
          "sit BETWEEN the conductors and the iron, not inside them.")

    # magnet ↔ magnet
    for i, j in _pairs_intersecting(magnets):
        inter = magnets[i].intersection(magnets[j])
        if _significant(inter, tol_mm):
            col.add(_overlap_violation(
                "magnet_overlaps_magnet", _name_magnet(i, magnets[i]),
                _name_magnet(j, magnets[j]), inter,
                "Adjacent poles have grown into each other; there is no rotor "
                "iron bridge left between them."))

    # coil ↔ coil
    for i, j in _pairs_intersecting(coils):
        inter = coils[i].intersection(coils[j])
        if _significant(inter, tol_mm):
            col.add(_overlap_violation(
                "coil_overlaps_coil", _name_coil(i, coils[i]),
                _name_coil(j, coils[j]), inter,
                "Two conductors occupy the same copper; the slot is too narrow "
                "for the winding placed in it, so the current in the shared "
                "area is counted for only one of them."))

    # ── 3. solids vs the air domains the mesher fills ────────────────────────
    # in_band  = disk r<mid_r minus the rotor bodies (rotates with the rotor)
    # out_band = annulus mid_r..r_outer minus the stator bodies (stationary)
    # A rotor solid reaching into out_band (or a stator solid into in_band) has
    # crossed the sliding-band slip surface — the moving mesh would shear it.
    checks.append("air_band_overlaps")
    _pair("magnet_in_rotor_air", "The magnets", mag_union, _AIR_IN, in_band,
          "A magnet sticks into the rotor air pocket that was cut for it.")
    _pair("rotor_crosses_air_gap", "The rotor assembly",
          _u([rotor, shaft, sleeve] + magnets), _AIR_OUT, out_band,
          "A rotating part crosses the mid-gap slip surface into the stationary "
          "air region; the sliding band would shear it every step.")
    _pair("coil_in_stator_air", "The winding", coil_union, _AIR_OUT, out_band,
          "A conductor sticks into the stator-side air region.")
    _pair("stator_crosses_air_gap", "The stator assembly",
          _u([stator] + coils), _AIR_IN, in_band,
          "A stationary part crosses the mid-gap slip surface into the rotating "
          "air region.")

    # ── 4. containment / radial sanity ───────────────────────────────────────
    checks.append("containment")
    rotor_or = float(p.get("rotor_outer_radius", 0.0) or 0.0)
    rotor_ir = float(p.get("rotor_inner_radius", 0.0) or 0.0)
    stator_ir = float(p.get("stator_inner_radius", 0.0) or 0.0)
    stator_or = float(p.get("stator_outer_radius", 0.0) or 0.0)
    if rotor_or <= 0.0 and rotor is not None:
        rotor_or = _radii(rotor)[1]
    if stator_ir <= 0.0 and stator is not None:
        stator_ir = _radii(stator)[0]

    for i, m in enumerate(magnets):
        r_min, r_max = _radii(m)
        if rotor_or > 0.0 and r_max > rotor_or + tol_mm:
            col.add(Violation(
                code="magnet_outside_rotor", severity=_SEV_ERROR,
                part_a=_name_magnet(i, m), part_b="the rotor disk",
                message="{} reaches r={:.3f} mm, past the rotor outer radius "
                        "{:.3f} mm — the magnet sticks out of the rotor into "
                        "the air gap.".format(
                            _name_magnet(i, m), r_max, rotor_or),
                measured_mm=r_max, limit_mm=rotor_or,
                likely_params=_CAUSES["magnet_outside_rotor"]))
        if rotor_ir > 0.0 and r_min < rotor_ir - tol_mm:
            col.add(Violation(
                code="magnet_outside_rotor", severity=_SEV_ERROR,
                part_a=_name_magnet(i, m), part_b="the rotor bore",
                message="{} reaches inward to r={:.3f} mm, inside the rotor "
                        "bore {:.3f} mm — the magnet extends past the rotor "
                        "into the shaft space.".format(
                            _name_magnet(i, m), r_min, rotor_ir),
                measured_mm=r_min, limit_mm=rotor_ir,
                likely_params=_CAUSES["magnet_outside_rotor"]))

    for i, c in enumerate(coils):
        r_min, r_max = _radii(c)
        if stator_ir > 0.0 and r_min < stator_ir - tol_mm:
            col.add(Violation(
                code="coil_outside_slot_bore", severity=_SEV_ERROR,
                part_a=_name_coil(i, c), part_b="its slot",
                message="{} reaches inward to r={:.3f} mm, {:.3f} mm past the "
                        "stator bore {:.3f} mm — the coil stack exceeds the "
                        "slot and hangs in the air gap.".format(
                            _name_coil(i, c), r_min, stator_ir - r_min,
                            stator_ir),
                measured_mm=r_min, limit_mm=stator_ir,
                likely_params=_CAUSES["coil_outside_slot_bore"]))
        if stator_or > 0.0 and r_max > stator_or + tol_mm:
            col.add(Violation(
                code="coil_outside_stator_od", severity=_SEV_ERROR,
                part_a=_name_coil(i, c), part_b="the stator outline",
                message="{} reaches r={:.3f} mm, past the stator outer radius "
                        "{:.3f} mm — the coil is outside the machine."
                        .format(_name_coil(i, c), r_max, stator_or),
                measured_mm=r_max, limit_mm=stator_or,
                likely_params=_CAUSES["coil_outside_stator_od"]))

    # ── 5. air gap: rotor OD must stay clear of the stator bore ──────────────
    checks.append("air_gap_clearance")
    # The sleeve is a ROTATING surface, and after it is fitted it is the
    # outermost one — so it, not the rotor OD, is what the stator bore has to
    # clear.  Leaving it out would report the magnetic gap as the mechanical
    # one and pass a machine that cannot turn.
    rot_solids = _u([rotor, shaft, sleeve] + magnets)
    stat_solids = _u([stator] + coils)
    if rot_solids is not None and stat_solids is not None:
        r_rot_max = _radii(rot_solids)[1]
        r_stat_min = _radii(stat_solids)[0]
        if math.isfinite(r_rot_max) and math.isfinite(r_stat_min):
            clearance = r_stat_min - r_rot_max
            # None of the checks above measure the gap itself; report it always.
            # (`_radii` is vertex-based, so on a discretised circle it is a hair
            #  pessimistic — that is the safe direction.)
            # A genuinely closed gap is reported by the intersection checks too;
            # this one names the number.
            if clearance <= tol_mm:
                col.add(Violation(
                    code="air_gap_not_positive", severity=_SEV_ERROR,
                    part_a="The rotor outer surface",
                    part_b="the stator bore",
                    message="The air gap is not positive everywhere: the "
                            "closest rotating surface sits at r={:.3f} mm and "
                            "the closest stationary surface at r={:.3f} mm, a "
                            "clearance of {:.4f} mm. The rotor cannot turn "
                            "inside this stator.".format(
                                r_rot_max, r_stat_min, clearance),
                    measured_mm=clearance, limit_mm=0.0,
                    likely_params=_CAUSES["air_gap_not_positive"]))
            else:
                # The gap the SOLVER sees is the polygon clearance, not the
                # `air_gap` number in the form.  Tooth-tip shaping (cut_width,
                # slot_hs, the bore fillet) can pull the iron surface away from
                # the nominal bore, and torque scales hard with the real gap —
                # so when the two disagree, say so instead of letting the user
                # believe the value they typed.
                # A retaining sleeve is DESIGNED to eat part of the gap, so the
                # number to compare against is the MECHANICAL gap the parameters
                # promise (air_gap − sleeve_thickness) — otherwise every sleeved
                # machine would carry a permanent "your gap is not what you
                # typed" warning that is simply the sleeve.
                _t_sl = _num(p.get("sleeve_thickness")) or 0.0
                _t_sl = _t_sl if _t_sl > 0.0 else 0.0
                nominal = _num(p.get("air_gap"))
                if nominal is not None:
                    nominal = nominal - _t_sl
                if nominal and nominal > 0.0:
                    slack = max(0.02, 0.10 * nominal)
                    if abs(clearance - nominal) > slack:
                        col.add(Violation(
                            code="air_gap_differs_from_parameter",
                            severity=_SEV_WARNING,
                            part_a="The modelled air gap",
                            part_b="the air_gap parameter",
                            message="The smallest rotor-to-stator clearance in "
                                    "the built cross-section is {:.3f} mm, but "
                                    "air_gap is set to {:.3f} mm{}. The solver "
                                    "uses the built geometry, so torque and "
                                    "flux follow the {:.3f} mm gap."
                                    .format(clearance, nominal,
                                            (" (air_gap {:g} mm minus the "
                                             "{:g} mm sleeve)".format(
                                                 _num(p.get("air_gap")) or 0.0,
                                                 _t_sl)) if _t_sl else "",
                                            clearance),
                            measured_mm=clearance, limit_mm=nominal,
                            likely_params=["air_gap", "cut_width", "slot_hs",
                                           "stator_fillet_r1", "tooth_width"]
                                          + (["sleeve_thickness"] if _t_sl
                                             else [])))
            min_gap = clearance
        else:
            min_gap = None
    else:
        min_gap = None

    # ── 5b. the retaining sleeve has to leave a mechanical gap ───────────────
    # A SCALAR rule in a polygon validator, deliberately: this is the gate every
    # solve passes through (routes/simulation refuses on `not result.ok`), and a
    # sleeve that fills its gap must never reach a mesher.  The polygon checks
    # above catch the gross case — a ring thicker than the gap intersects the
    # stator — but not the one that matters more: a ring that leaves 0.05 mm,
    # which is geometrically disjoint and still has nowhere to put the sliding
    # band.  Same sentence the Geometry tab's PUT refuses with.
    checks.append("sleeve_fits_air_gap")
    _sl_err = sleeve_gap_error(p)
    if _sl_err is not None:
        _fld, _code, _msg = _sl_err
        col.add(Violation(
            code=_code, severity=_SEV_ERROR,
            part_a="The retaining sleeve", part_b="the air gap",
            message=_msg,
            measured_mm=_num(p.get("sleeve_thickness")),
            limit_mm=_num(p.get("air_gap")),
            likely_params=_CAUSES.get(_code, ["sleeve_thickness", "air_gap"])))

    # ── 5c. a rotor_hole < 1 opening needs iron tabs of non-zero thickness ───
    # Another SCALAR rule, same reasoning as 5b: `rotor_hole_gap_error` needs no
    # polygon to know the retaining tab over each magnet corner is gone, and it
    # has to gate every solve even where the builder's ring-weld hides the
    # sliver overlap instead of raising (`tests/test_pocket_straight_sides.py`
    # ``HOLE_GAP_MATRIX``, (0.9, 0.0) — welds 5.594e-4 mm² of magnet into iron).
    checks.append("rotor_hole_pocket_tabs")
    _rh_err = rotor_hole_gap_error(p)
    if _rh_err is not None:
        _fld, _code, _msg = _rh_err
        col.add(Violation(
            code=_code, severity=_SEV_ERROR,
            part_a="The rotor pocket opening", part_b="the magnet",
            message=_msg,
            measured_mm=_num(p.get("magnet_up_gap")),
            limit_mm=_num(p.get("rotor_hole")),
            likely_params=_CAUSES.get(_code, ["rotor_hole", "magnet_up_gap"])))

    # ── 6. iron connectivity (warning — solvable, but rarely intended) ───────
    checks.append("iron_connectivity")
    for label, g, params_hint in (("rotor core", rotor, "rotor"),
                                  ("stator core", stator, "stator")):
        real = [q for q in _parts(g) if q.area > AREA_FLOOR_MM2]
        if len(real) > 1:
            small = min(real, key=lambda q: q.area)
            q = small.representative_point()
            col.add(Violation(
                code="iron_disconnected", severity=_SEV_WARNING,
                part_a="The " + label, part_b="",
                message="The {} is split into {} disconnected pieces (smallest "
                        "{:.2f} mm² near ({:.2f}, {:.2f}) mm) — a floating iron "
                        "island carries no flux back to the rest of the core."
                        .format(label, len(real), small.area, q.x, q.y),
                overlap_area_mm2=float(small.area),
                x_mm=float(q.x), y_mm=float(q.y),
                likely_params=(_CAUSES["iron_disconnected"]
                               if params_hint == "rotor" else
                               ["tooth_width", "tooth2_width", "cut_width",
                                "core_thickness", "slot_height"])))

    col.sort()
    return ValidationResult(violations=col.violations, min_air_gap_mm=min_gap,
                            hidden=col.hidden, checks_run=checks)


def validate_geometry(geo: Optional[Dict[str, Any]] = None,
                      rotor_angle_deg: float = 0.0,
                      tol_mm: Optional[float] = None,
                      max_per_code: int = 6) -> ValidationResult:
    """Build the 2-D polygons for ``geo`` (the active config when omitted) and
    validate them.

    This goes through ``CadQueryMotor.get_2d_polygons`` — the SAME call the
    mesher makes — so what is validated is exactly what gets meshed.
    """
    from motor_ai_sim.cadquery_geometry import CadQueryMotor
    motor = CadQueryMotor()
    if geo:
        motor.set_parameters(dict(geo))
    polys = motor.get_2d_polygons(rotor_angle_deg=float(rotor_angle_deg))
    return validate_polygons(polys, params=dict(motor.parameters),
                             tol_mm=tol_mm, max_per_code=max_per_code)


def assert_valid(geo: Optional[Dict[str, Any]] = None,
                 rotor_angle_deg: float = 0.0) -> ValidationResult:
    """Validate and RAISE :class:`GeometryInvalid` if the cross-section is not
    buildable.  Called by the solve paths — a solve of an impossible machine is
    never an answer."""
    res = validate_geometry(geo, rotor_angle_deg=rotor_angle_deg)
    if not res.ok:
        raise GeometryInvalid(res)
    return res


# ─────────────────────────────────────────────────────────────────────────────
# Scalar parameter sanity — the input guard in front of everything above
# ─────────────────────────────────────────────────────────────────────────────
# A negative / zero / absurd dimension does not produce an interesting overlap:
# it produces a crash three layers down in the mesher, or worse a mirrored
# polygon that meshes fine.  Reject it at the door, naming the field.
_ABSURD_MM = 10_000.0        # 10 m — no motor cross-section this codebase means

# key → (min, max, inclusive_min)  in mm unless noted
_POSITIVE_MM = (
    "stator_diameter", "slot_height", "core_thickness", "tooth_width",
    "wire_width", "wire_height", "magnet_height", "air_gap", "motor_length",
    "rotor_house_height", "shaft_height", "magnet_down_height", "cut_width",
    "tooth2_width",
)
_NON_NEGATIVE_MM = (
    "insulation_thickness", "wire_spacing_x", "wire_spacing_y",
    "magnet_up_gap", "stator_fillet_r", "stator_fillet_r1", "rotor_fill_r",
    "magnet_fill_radius", "magnet_lamination", "magnet_lamination_tan",
    # 0 = no retaining sleeve, which is the default and every machine but one.
    "sleeve_thickness",
)
_POSITIVE_INT = (
    "num_seg", "num_slots_per_segment", "num_poles_per_segment",
    "num_wires_per_slot", "wire_split", "wire_parallel",
)
#: Geometry keys that are FLAGS, not dimensions — none today.  Kept as the
#: named hook the numeric loop below already skips on: `isinstance(True, int)`
#: is True in Python, so a bool-valued knob would otherwise fall through the
#: numeric rules as a length of 1 mm.  ``wire_split_series`` was the only entry
#: and it lived for a few hours on 2026-09-08 (the strips of a wire_split row
#: are always SERIES turns now).
_BOOLEAN_KEYS: tuple = ()
_FRACTION_0_1 = (
    "magnet_fill_down", "magnet_fill_up", "rotor_hole", "slot_hs",
)

#: THE admissible slot/pole topology PER SEGMENT — one table, read by the
#: value validator below (→ ``PUT /api/geometry`` 422), served by
#: ``GET /api/geometry/schema`` as ``allowed_by`` on ``num_poles_per_segment``
#: (→ the Geometry table's select) and by nothing else.  Owner 2026-09-20:
#: "Poles per Segment у нас 5 или 7, других комбинаций пока не бывает" — after a
#: stray 7 → 8 (12s16p) turned the live machine into a lamination nobody
#: stamps.  A slots-per-segment count NOT in the table carries no rule (there
#: is no such family yet, and refusing it would refuse exploring one).
ADMISSIBLE_POLES_PER_SEGMENT: Dict[int, Tuple[int, ...]] = {
    6: (5, 7),           # 12s10p / 12s14p per two segments, 24s20p / 24s28p per four
}


def admissible_poles_per_segment(slots_per_segment) -> Optional[Tuple[int, ...]]:
    """The poles/segment values a slots/segment count admits, or None when
    the table says nothing about that count."""
    s = _num(slots_per_segment)
    if s is None:
        return None
    return ADMISSIBLE_POLES_PER_SEGMENT.get(int(round(s)))


def topology_error(geo: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The one record ``validate_parameter_values`` emits for an inadmissible
    (slots/segment, poles/segment) pair, or None.  Pure, so the route test and
    the validator test pin the same words."""
    allowed = admissible_poles_per_segment(geo.get("num_slots_per_segment"))
    p = _num(geo.get("num_poles_per_segment"))
    if allowed is None or p is None:
        return None
    pi = int(round(p))
    if pi in allowed:
        return None
    s = int(round(_num(geo.get("num_slots_per_segment"))))
    return {"field": "num_poles_per_segment", "value": geo.get("num_poles_per_segment"),
            "kind": "derived", "allowed": list(allowed),
            "message": ("num_poles_per_segment = {:d} is not a lamination this "
                        "product family stamps: with {:d} slots per segment the "
                        "admissible values are {} (got {:d})."
                        .format(pi, s, " or ".join(str(a) for a in allowed), pi))}


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def validate_parameter_values(geo: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Field-level sanity for a geometry parameter dict.

    Returns a list of ``{"field", "value", "kind", "message", "min"/"max"}``
    records — empty when every value is usable.  Only keys PRESENT in ``geo``
    are checked, so a partial update is judged on what it actually sets.

    ``kind`` is ``"field"`` for a single-value rule and ``"derived"`` for a rule
    that involves several parameters at once (a bore radius that comes out
    negative).  A caller validating a PARTIAL edit should keep every
    ``"derived"`` record — the combination is broken no matter which of the
    parameters was the one just typed — and only the ``"field"`` records whose
    field the request actually touched, so a pre-existing oddity elsewhere in
    the config cannot lock the user out of editing.
    """
    bad: List[Dict[str, Any]] = []
    _kind = {"k": "field"}

    def _err(field_name: str, value: Any, msg: str, **extra) -> None:
        # NaN / inf are not representable in strict JSON — the offending value
        # travels as text so the 422 body stays parseable by the browser.
        if isinstance(value, float) and not math.isfinite(value):
            value = repr(value)
        rec = {"field": field_name, "value": value, "kind": _kind["k"],
               "message": msg}
        rec.update(extra)
        bad.append(rec)

    for k, v in geo.items():
        if v is None or not isinstance(v, (int, float, str)):
            continue
        if isinstance(v, str):
            continue                      # string params (material names, …)
        if k in _BOOLEAN_KEYS:
            continue                      # a flag, not a dimension (none today)
        f = _num(v)
        if f is None:
            _err(k, v, "{} is not a finite number.".format(k))
            continue
        if abs(f) > _ABSURD_MM and k in (_POSITIVE_MM + _NON_NEGATIVE_MM):
            _err(k, v, "{} = {:g} mm is outside any plausible motor size "
                       "(limit {:g} mm).".format(k, f, _ABSURD_MM),
                 max=_ABSURD_MM)

    for k in _POSITIVE_MM:
        if k in geo and (f := _num(geo[k])) is not None and f <= 0.0:
            _err(k, geo[k], "{} must be greater than 0 mm (got {:g}). A zero or "
                            "negative dimension has no cross-section to mesh."
                            .format(k, f), min=0.0)
    for k in _NON_NEGATIVE_MM:
        if k in geo and (f := _num(geo[k])) is not None and f < 0.0:
            _err(k, geo[k], "{} cannot be negative (got {:g})."
                            .format(k, f), min=0.0)
    for k in _POSITIVE_INT:
        if k in geo and (f := _num(geo[k])) is not None and f < 1:
            _err(k, geo[k], "{} must be at least 1 (got {:g}).".format(k, f),
                 min=1)
    for k in _FRACTION_0_1:
        if k in geo and (f := _num(geo[k])) is not None and not (0.0 < f <= 1.0):
            _err(k, geo[k], "{} is a fraction of the pole pitch and must be in "
                            "(0, 1] (got {:g}).".format(k, f), min=0.0, max=1.0)

    # Every geometry knob is a NUMBER again.  `magnet_top: flat | arc` was the
    # one word-valued one, and it lived for a few hours on 2026-09-06 before the
    # user removed the flat top entirely ("уберём прямую вообще"); a stale key
    # from that window falls through the isinstance(v, str) skip above, which is
    # what "ignore it silently" means here.

    # ── the strands in hand must divide the wires in the slot ────────────────
    # DERIVED, not a field rule: it is the PAIR that is broken, and either half
    # is a legitimate thing to have just typed.  Rounding it would silently
    # build a different machine — 7 wires wound 2-in-hand is 3.5 turns per coil,
    # and 3.5 turns is not a winding.
    _kind["k"] = "derived"
    # ── the slot/pole topology has to be one the family stamps ───────────────
    # DERIVED: it is the PAIR (slots/segment, poles/segment) that is judged,
    # and either half is a legitimate thing to have just typed.
    _topo = topology_error(geo)
    if _topo is not None:
        bad.append(_topo)
    _wp = _num(geo.get("wire_parallel"))
    _nw = _num(geo.get("num_wires_per_slot"))
    if _wp is not None and _nw is not None and _wp >= 1 and _nw >= 1:
        _wpi, _nwi = int(round(_wp)), int(round(_nw))
        if _wpi >= 2 and _nwi % _wpi:
            _err("wire_parallel", geo.get("wire_parallel"),
                 "wire_parallel = {:d} does not divide num_wires_per_slot = "
                 "{:d}: {:d} wires wound {:d}-in-hand is {:g} turns per coil, "
                 "which is not a winding. Use a wire_parallel that divides "
                 "{:d}, or make num_wires_per_slot a multiple of {:d}."
                 .format(_wpi, _nwi, _nwi, _wpi, _nwi / _wpi, _nwi, _wpi))

    # ── the split strips + their gaps have to fit ACROSS the slot ────────────
    # DERIVED, and the tangential twin of `winding_does_not_fit` (which is the
    # RADIAL stack).  wire_split = N lays N strips of wire_width side by side
    # with 2·wire_spacing_x of enamel between them, so the wire COLUMN — and
    # with it the slot the CAD cuts for it — is N·wire_width +
    # (N−1)·2·wire_spacing_x.  The slot's outer wall has to stay inside the
    # half-sector wedge at the slot-bottom radius, or the neighbouring slot's
    # cutter meets it and the tooth between them is gone (cadquery_geometry's
    # fill_r2 turns negative and the stator polygon stops describing a machine).
    # Named on wire_split when there IS a split: its gaps are the only thing the
    # user just added, and the honest fix is a smaller N or a wider tooth pitch.
    # REFUSED only when there IS a split.  An unsplit wire that is wider than
    # the pitch is the pre-existing, reportable case: the CAD still builds it,
    # `coil_overlaps_coil` / `winding_split_does_not_fit` say so on the saved
    # design, and the SOLVE is what refuses — the contract tests/
    # test_geometry_validation.py::TestApiWiring pins ("a mid-edit design must
    # still save").  Refusing it here as well only ever passed those tests
    # because `slot_cut_limits` used to read the STALE derived slot count.
    _split_fit = split_width_error(geo)
    if _split_fit is not None and _split_fit[0] == "wire_split":
        _fld, _msg, _bound = _split_fit
        _err(_fld, geo.get(_fld), _msg, max=_bound)

    # ── the retaining sleeve has to fit inside the air gap ───────────────────
    # DERIVED: it is the PAIR (sleeve_thickness, air_gap) that is broken, and
    # either half is a legitimate thing to have just typed — so the rule applies
    # whichever one this request touched.
    _sl = sleeve_gap_error(geo)
    if _sl is not None:
        _fld, _code, _msg = _sl
        _gap_now = _num(geo.get("air_gap")) or 0.0
        from motor_ai_sim.geometry_constraints import MIN_MECH_GAP_MM as _MG
        _err(_fld, geo.get(_fld), _msg, min=0.0, max=max(0.0, _gap_now - _MG))

    # ── derived radii must survive the arithmetic ────────────────────────────
    od = _num(geo.get("stator_diameter"))
    core = _num(geo.get("core_thickness"))
    slot = _num(geo.get("slot_height"))
    if None not in (od, core, slot):
        bore = od / 2.0 - core - slot
        if bore <= 0.0:
            _err("slot_height", geo.get("slot_height"),
                 "core_thickness ({:g}) + slot_height ({:g}) leave a stator "
                 "bore radius of {:g} mm — the slots eat through the middle of "
                 "the machine.".format(core, slot, bore), min=0.0)
    gap = _num(geo.get("air_gap"))
    mag_h = _num(geo.get("magnet_height"))
    house = _num(geo.get("rotor_house_height"))
    if None not in (od, core, slot, gap, mag_h, house):
        rotor_or = od / 2.0 - core - slot - gap
        rotor_ir = rotor_or - mag_h - house
        if rotor_or <= 0.0:
            _err("air_gap", geo.get("air_gap"),
                 "The rotor outer radius works out to {:g} mm — nothing is left "
                 "for a rotor.".format(rotor_or), min=0.0)
        elif rotor_ir <= 0.0:
            _err("magnet_height", geo.get("magnet_height"),
                 "magnet_height ({:g}) + rotor_house_height ({:g}) exceed the "
                 "rotor radius {:g} mm — the magnets would reach through the "
                 "rotor centre.".format(mag_h, house, rotor_or), max=rotor_or)
    shaft_h = _num(geo.get("shaft_height"))
    if None not in (od, core, slot, gap, mag_h, house, shaft_h):
        rotor_ir = od / 2.0 - core - slot - gap - mag_h - house
        if rotor_ir > 0 and shaft_h >= rotor_ir:
            _err("shaft_height", geo.get("shaft_height"),
                 "shaft_height ({:g} mm) is not thinner than the rotor bore "
                 "radius {:g} mm — the shaft wall would swallow its own bore."
                 .format(shaft_h, rotor_ir), max=rotor_ir)
    return bad
