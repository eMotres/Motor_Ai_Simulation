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

# ── tolerances ───────────────────────────────────────────────────────────────
# Neighbouring domains legitimately SHARE boundaries (magnet face against its
# rotor pocket, coil against its liner).  Meshed vertices land on those shared
# edges to within round-off, so an exactly-touching pair can produce a hairline
# intersection of non-zero area.  A pair only counts as overlapping when the
# intersection survives eroding by TOL_MM/2 — i.e. it is a real region, not a
# ~1 µm seam.
TOL_MM = 1e-3            # 1 µm — shared-boundary tolerance
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
    return "Slot liner {} (θ={:.1f}°)".format(i + 1, _theta_deg(poly))


_ROTOR = "the rotor core"
_STATOR = "the stator core"
_SHAFT = "the shaft"
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
    "winding_clipped_by_slot": [
        "wire_width", "wire_height", "slot_hs", "num_wires_per_slot",
        "slot_height", "wire_spacing_y", "insulation_thickness"],
    "winding_copper_double_counted": [
        "wire_width", "wire_spacing_x", "tooth_width", "tooth2_width",
        "num_slots_per_segment", "num_seg"],
    "rotor_crosses_air_gap": [
        "air_gap", "magnet_up_gap", "rotor_fill_r"],
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


def validate_polygons(polys: Dict[str, Any],
                      params: Optional[Dict[str, Any]] = None,
                      tol_mm: float = TOL_MM,
                      max_per_code: int = 6) -> ValidationResult:
    """Validate the polygon dict returned by ``CadQueryMotor.get_2d_polygons``.

    ``params`` is the resolved geometry parameter dict (``motor.parameters``);
    it supplies the nominal radii used by the containment checks.  Absent, those
    radii are inferred from the polygons themselves.
    """
    p = dict(params or {})
    col = _Collector(max_per_code)
    checks: List[str] = []

    stator = polys.get("stator")
    rotor = polys.get("rotor")
    shaft = polys.get("shaft")
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
    _w_w = _num(p.get("wire_width"))
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

    _pair("coil_overlaps_stator", "The winding", coil_union, _STATOR, stator,
          "Copper and stator lamination share space; the current density the "
          "solver applies there is applied to iron.")
    _pair("coil_overlaps_liner", "The winding", coil_union, "the slot liner",
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
          _u([rotor, shaft] + magnets), _AIR_OUT, out_band,
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
    rot_solids = _u([rotor, shaft] + magnets)
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
                nominal = _num(p.get("air_gap"))
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
                                    "air_gap is set to {:.3f} mm. The solver "
                                    "uses the built geometry, so torque and "
                                    "flux follow the {:.3f} mm gap."
                                    .format(clearance, nominal, clearance),
                            measured_mm=clearance, limit_mm=nominal,
                            likely_params=["air_gap", "cut_width", "slot_hs",
                                           "stator_fillet_r1", "tooth_width"]))
            min_gap = clearance
        else:
            min_gap = None
    else:
        min_gap = None

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
                      tol_mm: float = TOL_MM,
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
)
_POSITIVE_INT = (
    "num_seg", "num_slots_per_segment", "num_poles_per_segment",
    "num_wires_per_slot", "wire_split",
)
_FRACTION_0_1 = (
    "magnet_fill_down", "magnet_fill_up", "rotor_hole", "slot_hs",
)


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

    # ── derived radii must survive the arithmetic ────────────────────────────
    _kind["k"] = "derived"
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
