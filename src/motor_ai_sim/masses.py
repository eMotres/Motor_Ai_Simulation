"""Single source of truth for motor component masses.

EVERY mass computation in the app must go through ``compute_masses`` so the mass —
and therefore torque-density (N·m/kg) and the iron-loss base — is IDENTICAL in
Simulation, the parameter sweep, and all three optimizers (descent / surrogate /
scan all reach it via optimization.design_eval).

WHAT A COMPONENT WEIGHS = (its CAD cross-section) × (stack length) × (lamination
fill factor, laminated parts only) × (the density of the material ASSIGNED to it).

  * SECTION — measured on the same CadQuery polygons the mesher receives, i.e. the
    same iron/copper/magnet the field solve runs on.  The old parametric forms
    (annulus arithmetic) described a different machine: on the 150 mm 24s/28p they
    over-read the magnets by 69 % (an annulus × fill_down, where the CAD lays 28
    shaped magnets of 98.3 mm² each), under-read the rotor iron by 64 % (the
    formula billed the SHAFT RING as rotor iron and never counted the holder ribs
    between the magnets), and turned the hollow 3 mm shaft tube into a SOLID
    aluminium disc (4094 mm² instead of 709 mm² — 0.387 kg of metal that does not
    exist).  Parametric forms remain only as a fallback for a geometry the CAD
    cannot build, and the payload says when they were used (``area_source``).
  * LAMINATION — a laminated core is k_f steel and (1−k_f) insulation by volume,
    so its MASS carries the same k_f the magnetic model already uses
    (fem_solver_2d._stack_factor_for → the material's own ``stacking_factor``;
    B15AHV950M = 0.925).  Both read the material record, so they cannot drift.
  * DENSITY — from the material ASSIGNED to that part in config `materials:`
    (or the per-request material override), never a hard-coded number.  The
    constants below are the last-resort fallback when a part has no assignment.

Reference cross-check (150 mm 24s/28p, 35 mm stack, ANSYS Motres_CIANO281_150):
their active-mass expression is
    (A_magnet·Nm·7700 + A_wire·6·wire_N_h·ρ_Cu·res_add + (A_stator+A_rotor_holder)·7700)
    ·stator_w·N + mass_rest,   mass_rest = 0
— i.e. magnets + copper (with the end-winding factor, res_add, which is the SAME
formula as ``end_winding_factor`` here) + stator iron + rotor holder, and NO
shaft, NO lamination factor, 7700 kg/m³ for every iron.  Hence the split below:
``active`` (what ANSYS quotes) vs ``total`` (active + shaft), which is what
torque-per-mass has always divided by and still does.

Fallback densities (used only when a part carries no material assignment):
  silicon steel 7650   copper 8900   NdFeB 7500   Al 2700  [kg/m³]
"""
from __future__ import annotations
import math
from typing import Any, Dict, Optional, Tuple

RHO_STEEL = 7650.0
RHO_CU    = 8900.0
RHO_MAG   = 7500.0
RHO_AL    = 2700.0
#: Carbon-fibre / epoxy retaining sleeve, rule of mixtures at ~60 % fibre
#: volume.  Used ONLY when the sleeve carries no material assignment; the real
#: number comes from the assigned material like every other part's.
RHO_CFRP  = 1580.0


def end_winding_factor(p: Any, geo: Dict[str, Any] | None = None) -> float:
    """Canonical end-winding length factor  k_end = (active + end-turns) / active.

    SINGLE SOURCE — the copper MASS, phase RESISTANCE and copper LOSS must all scale
    the active (in-slot, = stack-length) copper by THIS factor, so they stay
    consistent.  ``fem_solver_2d.end_winding_factor_geom`` delegates here so the
    solver's loss/R use the exact same number.

    Tooth-coil (fractional-slot concentrated) winding: each axial end-turn is a
    half-loop around the wound tooth, and its CENTRELINE runs through the middle
    of the wire bundle sitting on each side of the tooth — so the loop radius is
    tooth_width/2 + bundle_width/2, and
        k_end = (π·(bundle_width/2 + tooth_width/2) + L_stack) / L_stack.
    (Per Vadim, 2026-08-04 — replaces the 2026-07-02 tooth-only span, which was
    a lower bound: on the 40 mm it read 1.406 while the Ansys model's measured
    factor is 1.76; with the wire term it reads 1.733.)  Grows with both
    tooth_width and the bundle, so wide teeth AND thick wire cost copper
    mass / R / loss in the torque-density sweep.

    The bundle is ``winding.winding_footprint_mm`` — the whole wire COLUMN, not
    one strip: with ``wire_split`` = N the end turn has to get around N strips
    and their insulation, so the loop is wider by exactly what the slot got
    wider by.  Identical to ``wire_width`` at N = 1."""
    L = float(p.stack_length)
    if L <= 0:
        return 1.0
    slot_w  = float(p.slot_width_m)
    tooth_w = float((geo or {}).get("tooth_width", 0.0)) * 1e-3      # geo is in mm
    if tooth_w <= 0.0:                                               # no geo → derive
        r_mid = p.r_stator_in + p.slot_height_m * 0.5
        tau   = 2.0 * math.pi * r_mid / max(int(p.num_slots), 1)
        tooth_w = max(tau - slot_w, 0.3 * tau)
    try:
        from motor_ai_sim.winding import winding_footprint_mm as _fp
        wire_w = float(_fp(geo or {})) * 1e-3                        # 0 if unknown
    except (ValueError, TypeError):        # unusable wire_split → the bare width
        wire_w = float((geo or {}).get("wire_width", 0.0)) * 1e-3
    # half-loop centreline diameter = tooth + one wire column (half each side)
    span = tooth_w + max(wire_w, 0.0)
    return (math.pi * span / 2.0 + L) / L


# ─────────────────────────────────────────────────────────────────────────────
# Material resolution — density + lamination fill factor of the material the
# config (or the per-request override) ASSIGNS to each part.  Mirrors
# fem_solver_2d.build_materials so mass and field describe the same machine.
# ─────────────────────────────────────────────────────────────────────────────

#: part key in config `materials:` → (library categories to search, fallback ρ,
#: is this part a lamination stack?)
_PARTS: Dict[str, Tuple[Tuple[str, ...], float, bool]] = {
    "stator_core": (("steel",),                 RHO_STEEL, True),
    "rotor_core":  (("steel",),                 RHO_STEEL, True),
    "magnet":      (("magnet",),                RHO_MAG,   False),
    "slot":        (("conductor",),             RHO_CU,    False),
    "shaft":       (("conductor", "steel"),     RHO_AL,    False),
    # Retaining ring on the rotor OD.  `insulator` is the library category for
    # the non-magnetic structural parts (materials.PART_CATEGORIES agrees).
    "sleeve":      (("insulator",),             RHO_CFRP,  False),
}


def _assignments(materials: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """part → material name: config `materials:`, then the per-request override,
    then an explicit ``materials`` argument (a caller evaluating a candidate with
    a different steel/magnet than the saved config — the optimizer does this)."""
    out: Dict[str, str] = {}
    try:
        from motor_ai_sim.config import get_material_assignments
        out.update({k: v for k, v in (get_material_assignments() or {}).items() if v})
    except Exception:      # noqa: BLE001 — no config is not a reason to have no mass
        pass
    try:
        from motor_ai_sim.material_context import get_request_materials
        ov = (get_request_materials() or {}).get("assignment") or {}
        out.update({k: v for k, v in ov.items() if v})
    except Exception:      # noqa: BLE001
        pass
    out.update({k: v for k, v in (materials or {}).items() if v})
    return out


def _part_states() -> Dict[str, str]:
    """``{part: 'reference'|'excluded'}`` — the parts that do NOT count toward
    the machine's mass and inertia.  Empty (today's behaviour) unless the
    config's ``parts:`` block or this request's material context says so; see
    ``motor_ai_sim.part_states``."""
    try:
        from motor_ai_sim.part_states import resolve as _rs
        return _rs()
    except Exception:      # noqa: BLE001 — no state map is the default machine
        return {}


def _resolve_material(name: str, categories: Tuple[str, ...]):
    """The material record for ``name``: per-request override props first (they
    travel with the request and never go through the library), then the library
    (which resolves the admin-managed global layer itself)."""
    try:
        from motor_ai_sim.material_context import get_request_materials
        ov_mats = (get_request_materials() or {}).get("materials") or {}
    except Exception:      # noqa: BLE001
        ov_mats = {}
    from motor_ai_sim import materials as _ml
    if name in ov_mats:
        props = ov_mats[name] or {}
        cat = props.get("category") or categories[0]
        try:
            return _ml.material_from_dict(cat, name, props)
        except Exception:  # noqa: BLE001
            pass
    for cat in categories:
        try:
            return _ml.get_material(cat, name)
        except Exception:  # noqa: BLE001
            continue
    return None


def part_material(part: str, materials: Optional[Dict[str, str]] = None,
                  ) -> Tuple[float, float, str]:
    """(density [kg/m³], lamination fill factor k_f, material name) of a part.

    k_f < 1 only for a laminated core, and only from the material's own
    ``stacking_factor`` — the SAME number fem_solver_2d folds into the B-H curve.
    A solid core (SMC: form 'solid') declares 1.0 and gets 1.0.  An unassigned or
    unresolvable part falls back to the module constant with k_f = 1.
    """
    cats, rho_default, laminated = _PARTS[part]
    name = (_assignments(materials).get(part) or "").strip()
    if not name:
        # A part introduced after the machines in the field were saved has no
        # `materials:` entry anywhere, so it would silently fall back to the
        # module constant.  Name the library record instead — see
        # materials.DEFAULT_PART_MATERIAL.
        try:
            from motor_ai_sim.materials import DEFAULT_PART_MATERIAL
            name = DEFAULT_PART_MATERIAL.get(part, "")
        except Exception:      # noqa: BLE001
            name = ""
    if not name:
        return rho_default, 1.0, ""
    m = _resolve_material(name, cats)
    if m is None:
        return rho_default, 1.0, name
    rho = float(getattr(m, "density", 0.0) or 0.0) or rho_default
    kf = 1.0
    if laminated and str(getattr(m, "form", "laminated")).lower() != "solid":
        k = float(getattr(m, "stacking_factor", 1.0) or 1.0)
        kf = k if 0.0 < k <= 1.0 else 1.0
    return rho, kf, name


# ─────────────────────────────────────────────────────────────────────────────
# Cross-sections, MEASURED on the CAD polygons the mesher receives
# ─────────────────────────────────────────────────────────────────────────────

#: Sections keyed by the geometry that produced them.  The CAD build is ~35 ms
#: and a Pareto search re-evaluates the same candidate at several operating
#: points, so the measurement is memoised rather than repeated.
_AREA_CACHE: Dict[Tuple[Tuple[str, Optional[float]], ...],
                  Optional[Dict[str, float]]] = {}

#: Every geometry key that can move a stator / rotor / magnet / conductor
#: polygon.  Anything outside this set cannot change a section, so it must not
#: split the cache (rpm/current are not here at all — a section is geometry).
_AREA_KEYS = (
    "num_seg", "num_slots_per_segment", "num_poles_per_segment",
    "num_slots", "num_poles",
    "stator_diameter", "core_thickness", "slot_height", "tooth_width",
    "tooth2_width", "cut_width", "slot_hs", "stator_fillet_r",
    "stator_fillet_r1", "wire_width", "wire_height", "wire_spacing_x",
    "wire_spacing_y", "wire_split", "wire_parallel", "insulation_thickness",
    "num_wires_per_slot", "motor_length", "air_gap",
    "magnet_height", "magnet_fill_up", "magnet_fill_down", "magnet_fill_radius",
    "magnet_up_gap", "magnet_down_height", "magnet_lamination_tan",
    "rotor_house_height", "rotor_hole", "rotor_fill_r", "shaft_height",
    # The retaining ring's own section, and it also moves the mechanical gap —
    # so it must split the cache like any other geometry knob.
    "sleeve_thickness",
)


def _area_key_val(v):
    """One cache-key component.  ``float()`` on every value was fine while every
    geometry knob was a number, and then `magnet_top: flat | arc` arrived on
    2026-09-06 and ``float("arc")`` raised a ValueError OUTSIDE the try below —
    i.e. it 500'd the caller instead of falling back to the parametric sections.
    That knob is gone (the top is always the arc now), but the guard stays: a
    word landing in a geometry dict must never be a 500."""
    if v is None:
        return None
    if isinstance(v, str):
        return v
    return float(v)


def cad_areas_m2(geo: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """Cross-sections [m²] of stator iron / rotor iron / magnets / copper / shaft,
    MEASURED on the CadQuery polygons handed to the mesher.

    This is the same measurement the solver's copper loss already uses for the
    conductors (``field_ops.coil_copper_area_total_m2`` — the UNION of the
    conductor polygons, because on some machines the CAD clips the wire stack to
    fit the slot and on others the rectangles interpenetrate), extended to the
    iron and the magnets, so mass and field describe one machine.

    Returns None when the CAD cannot build this geometry (a candidate the
    optimizer is entitled to propose): the caller falls back to the parametric
    sections and records that it did.
    """
    key = tuple((k, _area_key_val(geo.get(k))) for k in _AREA_KEYS)
    if key in _AREA_CACHE:
        return _AREA_CACHE[key]
    areas: Optional[Dict[str, float]] = None
    try:
        from motor_ai_sim.cadquery_geometry import CadQueryMotor
        from motor_ai_sim.simulation.field_ops import coil_copper_area_total_m2
        motor = CadQueryMotor()
        motor.set_parameters(dict(geo))
        polys = motor.get_2d_polygons(rotor_angle_deg=0.0)

        def _area(obj) -> float:
            if obj is None:
                return 0.0
            if isinstance(obj, (list, tuple)):
                return sum(_area(o) for o in obj)
            try:
                return float(obj.area) * 1e-6          # polygons are in mm²
            except Exception:                          # noqa: BLE001
                return 0.0

        a_mag = sum(_area(mp) for mp, _pol in (polys.get("magnets") or []))
        a = {
            "stator": _area(polys.get("stator")),
            "rotor":  _area(polys.get("rotor")),
            "shaft":  _area(polys.get("shaft")),
            "magnet": a_mag,
            "copper": float(coil_copper_area_total_m2(polys)),
            # 0.0 on every machine without a sleeve (polys["sleeve"] is None).
            "sleeve": _area(polys.get("sleeve")),
        }
        # An empty stator or no copper means the build did not really succeed —
        # a zero-mass machine must never reach a torque-density.
        if a["stator"] > 0.0 and a["copper"] > 0.0:
            areas = a
    except Exception:      # noqa: BLE001 — an unbuildable candidate is data, not an error
        areas = None
    if len(_AREA_CACHE) > 4096:            # a long search must not grow without bound
        _AREA_CACHE.clear()
    _AREA_CACHE[key] = areas
    return areas


_SLOT_CACHE: Dict[tuple, Optional[Dict[str, float]]] = {}


def slot_fill_from_cad(geo: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """Wire coating factor, MEASURED on the same polygons the mesher receives.

    The winding window is not a parameter of this machine — it is whatever the
    stamped tooth shape leaves between the teeth — so it is measured rather than
    assumed: take the radial band the conductors actually occupy, and subtract
    the stator iron inside that band.  What is left is the window the coils have
    to live in, and the fill factor is the measured conductor area over it.

    That is the number a winder cares about: how much of the available slot is
    copper, insulation and air.  Returns None when the CAD cannot build the
    cross-section (the caller then simply does not show a fill).
    """
    key = tuple((k, _area_key_val(geo.get(k))) for k in _AREA_KEYS)
    if key in _SLOT_CACHE:
        return _SLOT_CACHE[key]
    out: Optional[Dict[str, float]] = None
    try:
        from shapely.geometry import Point
        from shapely.ops import unary_union
        from motor_ai_sim.cadquery_geometry import CadQueryMotor
        from motor_ai_sim.simulation.field_ops import coil_copper_area_total_m2

        motor = CadQueryMotor()
        motor.set_parameters(dict(geo))
        polys = motor.get_2d_polygons(rotor_angle_deg=0.0)
        coils = [c for c in (polys.get("coils") or []) if c is not None]
        stator = polys.get("stator")
        if coils and stator is not None:
            cu = unary_union(coils)
            # the radial band the conductors span, as built
            r_in = min(Point(0, 0).distance(Point(x, y))
                       for g_ in (cu.geoms if hasattr(cu, "geoms") else [cu])
                       for x, y in g_.exterior.coords)
            r_out = max(Point(0, 0).distance(Point(x, y))
                        for g_ in (cu.geoms if hasattr(cu, "geoms") else [cu])
                        for x, y in g_.exterior.coords)
            band = (Point(0, 0).buffer(r_out, 256)
                    .difference(Point(0, 0).buffer(r_in, 256)))
            window_mm2 = float(band.difference(stator).area)
            cu_mm2 = float(coil_copper_area_total_m2(polys)) * 1e6
            if window_mm2 > 0 and cu_mm2 > 0:
                out = {"A_cu_mm2": cu_mm2, "A_slot_mm2": window_mm2,
                       "fill": cu_mm2 / window_mm2,
                       "r_in_mm": r_in, "r_out_mm": r_out}
    except Exception:      # noqa: BLE001 — an unbuildable candidate is data
        out = None
    if len(_SLOT_CACHE) > 4096:
        _SLOT_CACHE.clear()
    _SLOT_CACHE[key] = out
    return out


def parametric_areas_m2(p: Any, geo: Dict[str, Any]) -> Dict[str, float]:
    """Fallback sections [m²] from the radii, for a geometry the CAD cannot build.

    Deliberately laid out like the real machine rather than like the pre-2026-08
    formula: the magnet BAND is magnet_height deep (not the whole rotor annulus),
    whatever of that band is not magnet is rotor iron (the holder ribs), and the
    shaft is the HOLLOW tube between r_shaft_in and r_rotor_in — the CAD's shaft,
    not a solid disc.  Still an estimate: it cannot see fillets, slot openings or
    magnet pockets, so it is used only when the CAD build fails.
    """
    mm = 1e-3
    ns = int(p.num_slots)
    slot_h = float(p.slot_height_m)
    tooth_w = float(geo.get("tooth_width", 0.0)) * mm
    # CONDUCTORS, not wire rows: with wire_split = N each row is N strips of
    # wire_width, so the copper section is N times the row count's.  The CAD
    # path (`_AREA_CACHE`) measures the strips directly; this fallback has to
    # count them or an unbuildable split candidate would be weighed as if it
    # carried a single wire per row.
    from motor_ai_sim.winding import conductors_per_slot as _cond_slot
    try:
        n_wires = float(_cond_slot(geo) or geo.get("num_wires_per_slot", 14))
    except (ValueError, TypeError):        # unusable wire_split → the rows alone
        n_wires = float(geo.get("num_wires_per_slot", 14))
    wire_w = float(geo.get("wire_width", 5.0)) * mm
    wire_h = float(geo.get("wire_height", 0.6)) * mm
    mag_h = float(geo.get("magnet_height", 0.0)) * mm

    r_slot_bottom = p.r_stator_in + slot_h
    a_stator = max(math.pi * (p.r_stator_out ** 2 - r_slot_bottom ** 2)
                   + ns * tooth_w * slot_h, 0.0)
    r_mag_in = max(p.r_rotor_out - mag_h, p.r_rotor_in) if mag_h > 0 else p.r_rotor_in
    a_band = max(math.pi * (p.r_rotor_out ** 2 - r_mag_in ** 2), 0.0)
    a_magnet = a_band * float(p.magnet_fill_fraction)
    # the rest of the magnet band + the holder ring under it are rotor iron
    a_rotor = max(a_band - a_magnet, 0.0) + \
        max(math.pi * (r_mag_in ** 2 - p.r_rotor_in ** 2), 0.0)
    a_shaft = max(math.pi * (p.r_rotor_in ** 2 - p.r_shaft_in ** 2), 0.0)
    # The retaining ring is an exact annulus, so the "fallback" section is the
    # real one — nothing about it needs the CAD.
    t_sl = max(float(geo.get("sleeve_thickness", 0.0) or 0.0), 0.0) * mm
    a_sleeve = (max(math.pi * ((p.r_rotor_out + t_sl) ** 2 - p.r_rotor_out ** 2),
                    0.0) if t_sl > 0.0 else 0.0)
    return {"stator": a_stator, "rotor": a_rotor, "magnet": a_magnet,
            "copper": ns * wire_w * wire_h * n_wires, "shaft": a_shaft,
            "sleeve": a_sleeve}


# ─────────────────────────────────────────────────────────────────────────────
# Rotor inertia
# ─────────────────────────────────────────────────────────────────────────────

# geometry key → (Jp_rotor, Jp_mag, Jp_shaft, Jp_sleeve) [m⁴] or None (unbuildable)
_POLAR_CACHE: Dict[Tuple[Tuple[str, Optional[float]], ...],
                   Optional[Tuple[float, float, float, float]]] = {}


def _ring_polar_m4(coords) -> float:
    """∬ r² dA of ONE closed ring about the ORIGIN, by Green's theorem [m⁴].

    Standard polygon second moments in ABSOLUTE coordinates (the rotor is
    centred on the origin, so no parallel-axis transfer is needed):
      Ix + Iy = (1/12)·Σ (x_i·y_{i+1} − x_{i+1}·y_i)·
                (x_i² + x_i·x_{i+1} + x_{i+1}² + y_i² + y_i·y_{i+1} + y_{i+1}²)
    Sign follows ring orientation; the caller subtracts holes, so return the
    magnitude and let the exterior−interiors arithmetic carry the signs.
    Coordinates arrive in mm (the CAD's unit) → 1e-12 to m⁴.
    """
    pts = list(coords)
    if len(pts) < 4:                      # a ring closes on itself: ≥3 + repeat
        return 0.0
    s = 0.0
    for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
        cross = x0 * y1 - x1 * y0
        s += cross * (x0 * x0 + x0 * x1 + x1 * x1 + y0 * y0 + y0 * y1 + y1 * y1)
    return abs(s) / 12.0 * 1e-12


def _poly_polar_m4(obj) -> float:
    """∬ r² dA about the origin for a shapely Polygon / MultiPolygon / list."""
    if obj is None:
        return 0.0
    if isinstance(obj, (list, tuple)):
        return sum(_poly_polar_m4(o) for o in obj)
    geoms = getattr(obj, "geoms", None)
    if geoms is not None:                  # MultiPolygon / GeometryCollection
        return sum(_poly_polar_m4(g) for g in geoms)
    try:
        j = _ring_polar_m4(obj.exterior.coords)
        for hole in obj.interiors:
            j -= _ring_polar_m4(hole.coords)
        return max(j, 0.0)
    except Exception:                      # noqa: BLE001 — not a polygon
        return 0.0


def rotor_inertia_kg_m2(p: Any, geo: Dict[str, Any],
                        materials: Optional[Dict[str, str]] = None
                        ) -> Dict[str, Any]:
    """Rotor moment of inertia J about the shaft axis [kg·m²].

    Measured the same way the mass is: ∬ r² dA over the CAD polygons of every
    part that ROTATES — rotor iron (billed at its lamination k_f, like the
    mass), magnets, shaft/housing — times stack length and the assigned
    material's density.  The stator and copper do not rotate and carry none.
    Falls back to the parametric annuli (same decomposition as
    ``parametric_areas_m2``: the magnet band split by the fill fraction, the
    remainder of the band + the holder ring as rotor iron) when the CAD cannot
    build the geometry, and says so in ``source``.
    """
    L = float(p.stack_length)
    rho_rt, kf_rt, _ = part_material("rotor_core", materials)
    rho_mg, _, _ = part_material("magnet", materials)
    rho_sh, _, _ = part_material("shaft", materials)
    rho_sl, _, _ = part_material("sleeve", materials)

    jp_rotor = jp_mag = jp_shaft = None
    jp_sleeve = 0.0
    source = "CAD polygons"
    # Memoised by the same geometry key the AREA cache uses: the polar moments
    # are pure geometry (densities/k_f are applied after), and the summary now
    # rebuilds on cache hits — without this every hit would pay a CAD build.
    _jkey = tuple((k, _area_key_val(geo.get(k))) for k in _AREA_KEYS)
    if _jkey in _POLAR_CACHE:
        jp_rotor, jp_mag, jp_shaft, jp_sleeve = (
            _POLAR_CACHE[_jkey] or (None, None, None, 0.0))
    else:
        try:
            from motor_ai_sim.cadquery_geometry import CadQueryMotor
            motor = CadQueryMotor()
            motor.set_parameters(dict(geo))
            polys = motor.get_2d_polygons(rotor_angle_deg=0.0)
            jr = _poly_polar_m4(polys.get("rotor"))
            jm = sum(_poly_polar_m4(mp) for mp, _pol in (polys.get("magnets") or []))
            js = _poly_polar_m4(polys.get("shaft"))
            jsl = _poly_polar_m4(polys.get("sleeve"))   # 0.0 with no sleeve
            if jr > 0.0:                   # an empty rotor = the build failed
                jp_rotor, jp_mag, jp_shaft, jp_sleeve = jr, jm, js, jsl
        except Exception:      # noqa: BLE001 — unbuildable candidate is data
            pass
        if len(_POLAR_CACHE) > 4096:
            _POLAR_CACHE.clear()
        _POLAR_CACHE[_jkey] = ((jp_rotor, jp_mag, jp_shaft, jp_sleeve)
                               if jp_rotor is not None else None)
    if jp_rotor is None:
        source = "parametric annuli (CAD build failed)"
        # J_polar of an annulus r1→r2 is (π/2)(r2⁴ − r1⁴); the magnet band's
        # fill fraction applies to the AREA at unchanged radii, so it scales
        # the band's polar moment linearly.
        ann = lambda r1, r2: max(math.pi / 2.0 * (r2 ** 4 - r1 ** 4), 0.0)
        mag_h = float(geo.get("magnet_height", 0.0)) * 1e-3
        r_mag_in = (max(p.r_rotor_out - mag_h, p.r_rotor_in)
                    if mag_h > 0 else p.r_rotor_in)
        j_band = ann(r_mag_in, p.r_rotor_out)
        jp_mag = j_band * float(p.magnet_fill_fraction)
        jp_rotor = (j_band - jp_mag) + ann(p.r_rotor_in, r_mag_in)
        jp_shaft = ann(p.r_shaft_in, p.r_rotor_in)
        _t_sl = max(float(geo.get("sleeve_thickness", 0.0) or 0.0), 0.0) * 1e-3
        jp_sleeve = (ann(p.r_rotor_out, p.r_rotor_out + _t_sl)
                     if _t_sl > 0.0 else 0.0)

    j_rotor = jp_rotor * L * kf_rt * rho_rt
    j_mag = jp_mag * L * rho_mg
    j_shaft = jp_shaft * L * rho_sh
    # The sleeve is bonded to the rotor OD — the outermost rotating metal there
    # is, so per kilogram it is the most expensive inertia on the machine.
    j_sleeve = (jp_sleeve or 0.0) * L * rho_sl
    # A part that is not OURS does not spin OUR inertia: a `reference` shaft is
    # the customer's steel and an `excluded` one is not there at all, so both
    # leave J exactly as they leave the mass.  The per-part terms are still
    # reported (with `states`) so the card can say what was left out and by how
    # much, instead of a total that quietly shrank.
    _st = _part_states()
    j_phys = {"rotor_iron": j_rotor, "magnet": j_mag, "shaft": j_shaft,
              "sleeve": j_sleeve}
    if _st:
        if _st.get("rotor_core"):
            j_rotor = 0.0
        if _st.get("magnet"):
            j_mag = 0.0
        if _st.get("shaft"):
            j_shaft = 0.0
        if _st.get("sleeve"):
            j_sleeve = 0.0
    return {"total": j_rotor + j_mag + j_shaft + j_sleeve,
            "rotor_iron": j_rotor, "magnet": j_mag, "shaft": j_shaft,
            "sleeve": j_sleeve,
            "J_modelled": j_phys, "states": dict(_st),
            "source": source}


# ─────────────────────────────────────────────────────────────────────────────
# The mass
# ─────────────────────────────────────────────────────────────────────────────

def compute_masses(p: Any, geo: Dict[str, Any], k_end: float = 0.0,
                   materials: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Component masses [kg] from the resolved params ``p`` (metres) + the geometry
    config ``geo`` (mm).

    ``k_end`` (0 = auto) is the end-winding factor to bill the copper at — pass the
    value the loss/R path used so mass, resistance and copper loss agree.
    ``materials`` optionally overrides the part→material assignment (an optimizer
    scoring a candidate against a different steel/magnet than the saved config).

    Returns the per-component masses plus:
      ``active``  stator iron + rotor iron + copper + magnets — the EM-active mass,
                  the number ANSYS quotes (its expression carries no shaft term);
      ``total``   active + shaft — the historical basis of torque-per-mass, which
                  is unchanged so stored Compare points keep their meaning;
      the sections and volumes used, the densities and k_f per part, and
      ``area_source`` naming which section set produced the numbers.
    """
    L = float(p.stack_length)
    k_end_eff = float(k_end) if (k_end and k_end > 0) else end_winding_factor(p, geo)

    A = cad_areas_m2(geo)
    area_source = "CAD polygons"
    if not A:
        A = parametric_areas_m2(p, geo)
        area_source = "parametric radii (CAD build failed)"

    rho_st, kf_st, n_st = part_material("stator_core", materials)
    rho_rt, kf_rt, n_rt = part_material("rotor_core", materials)
    rho_mg, _, n_mg = part_material("magnet", materials)
    rho_cu, _, n_cu = part_material("slot", materials)
    rho_sh, _, n_sh = part_material("shaft", materials)
    rho_sl, _, n_sl = part_material("sleeve", materials)

    # Volumes are the volumes of the MATERIAL: a laminated core is k_f steel by
    # volume, so V·ρ is the mass with no hidden factor left over.
    V_stator = A["stator"] * L * kf_st
    V_rotor  = A["rotor"] * L * kf_rt
    V_mag    = A["magnet"] * L
    V_cu     = A["copper"] * L * k_end_eff
    V_shaft  = A["shaft"] * L
    # `A` can be a parametric fallback written before the sleeve existed, or a
    # cached CAD measurement from an older process — .get, so a missing key is
    # "no sleeve" rather than a KeyError three layers into a Pareto search.
    V_sleeve = A.get("sleeve", 0.0) * L

    m_stator = V_stator * rho_st
    m_rotor  = V_rotor * rho_rt
    m_mag    = V_mag * rho_mg
    m_cu     = V_cu * rho_cu
    m_shaft  = V_shaft * rho_sh
    m_sleeve = V_sleeve * rho_sl

    # ── Accounting state: what of this metal is OURS to weigh ────────────────
    # `reference` = the customer's part sitting in our field (a frameless
    # motor's shaft): real to the solve, absent from the mass, the inertia and
    # every N·m/kg.  `excluded` = not there at all.  The MODELLED masses are
    # kept alongside under `PHYS` so a card can say "0.061 kg of customer steel
    # was left out" instead of showing a total that silently shrank.
    _states = _part_states()
    _phys = {"stator": m_stator, "cu": m_cu, "mag": m_mag,
             "rotor": m_rotor, "shaft": m_shaft, "sleeve": m_sleeve}
    if _states:
        if _states.get("stator_core"):
            m_stator = 0.0
        if _states.get("rotor_core"):
            m_rotor = 0.0
        if _states.get("magnet"):
            m_mag = 0.0
        if _states.get("slot"):
            m_cu = 0.0
        if _states.get("shaft"):
            m_shaft = 0.0
        if _states.get("sleeve"):
            m_sleeve = 0.0

    # THE BAND IS ACTIVE MASS (user 2026-09-10: "давай не будем разделять их,
    # пусть будет одна активная масса вместе с бандажом, так будет проще, чтобы
    # не запутаться").
    #
    # It used to sit on the shaft side, on the argument that ANSYS's active-mass
    # expression has no term for a retaining ring and `active` had to stay
    # comparable with it.  That comparability is now given up on purpose: two
    # masses that differ by a quarter of a kilo, one of which silently excludes
    # a part the user can see in the 3-D view, cost more confusion than the
    # comparison was worth.  A band is bought, wound, shipped and spun; on this
    # project it counts.
    #
    # Consequence, stated so nobody rediscovers it: `active` is no longer the
    # same quantity ANSYS prints under that name.  `total` is unchanged — it
    # always contained the band — and so is every N·m/kg, which divides by it.
    # Zero-thickness = zero, so nothing moves on a machine without a band.
    m_active = m_stator + m_rotor + m_cu + m_mag + m_sleeve
    m_total  = m_active + m_shaft
    return {
        "stator": m_stator, "cu": m_cu, "mag": m_mag, "rotor": m_rotor,
        "shaft": m_shaft, "sleeve": m_sleeve,
        "active": m_active, "total": m_total,
        # part key (config `materials:` spelling) → 'reference' | 'excluded';
        # absent = included.  PHYS holds what each part WOULD weigh, so the
        # excluded metal is reportable without being billed.
        "STATE": dict(_states), "PHYS": _phys,
        "V_stator": V_stator, "V_cu": V_cu, "V_mag": V_mag,
        "V_rotor": V_rotor, "V_shaft": V_shaft, "V_sleeve": V_sleeve,
        "A_stator": A["stator"], "A_rotor": A["rotor"], "A_mag": A["magnet"],
        "A_cu": A["copper"], "A_shaft": A["shaft"],
        "A_sleeve": A.get("sleeve", 0.0),
        "k_end": k_end_eff, "k_f_stator": kf_st, "k_f_rotor": kf_rt,
        "area_source": area_source,
        "RHO": {"steel": rho_st, "steel_rotor": rho_rt, "cu": rho_cu,
                "mag": rho_mg, "al": rho_sh, "sleeve": rho_sl},
        "MAT": {"stator_core": n_st, "rotor_core": n_rt, "magnet": n_mg,
                "slot": n_cu, "shaft": n_sh, "sleeve": n_sl},
    }
