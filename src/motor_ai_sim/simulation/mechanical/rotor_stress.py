"""Rotor centrifugal stress & deformation — 2-D plane-stress linear elasticity.

Written 2026-09-05 for the user's request: "нам нужно сделать механический
модуль расчётов — начнём с расчёта центробежных сил ротора ... чтобы оценить
какой бандаж нужен для удержания магнитов и ротора, то есть рассчитывать все
напряжения и деформации".  In other words: size the retaining sleeve.

WHAT IS SOLVED
--------------
The ROTOR SOLIDS ONLY — rotor lamination core, every magnet, the carbon-fibre
retaining sleeve (when the machine has one) and the shaft tube.  No air, no
stator: air carries no stress and the stator is not spinning, so including
either would only add DOFs and a fake load path across the gap.

Governing equations: 2-D linear elasticity, PLANE STRESS, small strain,
isotropic per part except the sleeve (see below).  Loads:

  * centrifugal body force  f = rho * omega^2 * r  (radial, outward), and
  * an OPTIONAL sleeve interference fit, applied as a hoop EIGENSTRAIN
    eps_theta0 = -delta_r / r_sleeve_mean inside the sleeve elements, so that
    sigma = C:(eps - eps0).  A negative eigenstrain means the sleeve's
    stress-free radius is SMALLER than where it is meshed, i.e. it was stretched
    over the rotor — which is exactly what a shrink/press fit does: at
    standstill the sleeve is in hoop tension and it squeezes the rotor.

MODELLING ASSUMPTIONS — read these before trusting a number
-----------------------------------------------------------
* CONTACT interfaces (v2, 2026-09-05).  v1 welded every part into one body, so
  an interface transmitted TENSION and the magnets hung on the iron — which no
  pocket does.  The parts are now meshed conforming and then SPLIT along every
  interface, and each pair is joined by a contact whose type the caller picks:
  ``separation`` (Fusion's default and this module's: may open and slide, never
  penetrate, compression only), ``bonded`` (the v1 weld) or ``sliding`` (normal
  tied, tangent free), with an optional Coulomb mu.  The machinery, the active
  set and why the normal constraint is a stiff spring rather than a Lagrange tie
  are all in ``mechanical.contact``.
* NONLINEAR IN THE LOAD.  A contact that opens and closes destroys
  superposition: standstill, rated and overspeed are three separate solves, and
  the lift-off speed is a bisection instead of v1's closed form.
* PLANE STRESS (sigma_zz = 0).  Correct at the stack ends, slightly
  conservative (higher hoop stress) in the middle of a long stack where the
  real state approaches plane strain.  No axial pre-load, no end plates.
* The bore is FREE.  Only the three rigid-body modes are removed, with a
  bordered (Lagrange-multiplier) system — no node is pinned, so no artificial
  stress concentration appears anywhere.  The centrifugal load on a rotor is
  self-equilibrated, so this is well posed.
* CYCLIC SYMMETRY, optional (2026-09-09, ``symmetry="sector"``).  User:
  "нагрузка на все зубы должна быть одинакова … так используй периодичность,
  как я во Fusion".  One periodic sector of ``n`` is solved instead of the whole
  circle, with the two cut faces tied by ``u_B = R(2*pi/n) u_A`` — every pole
  then carries an identical load by construction and the matrix is ``n`` times
  smaller (28 on the G2-L40).  ``symmetry="full"`` is the default and is the
  360° model this module has always been.  The wedge, the periodic mesh, the
  ties and what is scaled back up on the way out are all in
  ``mechanical.symmetry``.
* The sleeve is ORTHOTROPIC in POLAR coordinates: a hoop-wound UD CFRP has its
  fibres along theta, so E_1 = E_hoop (stiff) lies tangentially and
  E_2 = E_transverse (soft, matrix dominated) lies radially.  The plane-stress
  stiffness is built in material axes and rotated per element by the element
  centroid angle.
* Lamination is ignored (the core is treated as solid steel in-plane), which is
  the standard 2-D assumption: the in-plane load path is inside each sheet.
* The material stays LINEAR ELASTIC.  On a rotor whose magnets are caught by a
  thin iron shoulder that is exactly the number to read carefully: the model
  will happily report an iron stress ten times its yield rather than
  redistribute it, and the safety factor is the honest way it says so.

Units: geometry arrives in mm and is converted to metres here; stresses are
returned in MPa, displacements in micrometres.
"""
from __future__ import annotations

import hashlib
import logging
import math
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class MissingMechanicalProperty(ValueError):
    """A part's assigned material carries no value for a property the structural
    solve needs.

    Raised — never defaulted around.  The project's standing rule is that a
    number must be traceable to a record the user can open; a silently invented
    Young's modulus would produce a plausible-looking sleeve size that nobody
    could check.  The route turns this into a 422 naming the part, the material
    and the missing key.
    """

    def __init__(self, part: str, material: str, key: str, hint: str = ""):
        self.part = part
        self.material = material
        self.key = key
        msg = (f"material {material!r} assigned to {part!r} has no {key!r} — "
               f"add it to config/materials_library.yaml")
        if hint:
            msg += f" ({hint})"
        super().__init__(msg)


#: A displacement past this fraction of the rotor's outer radius is not an
#: elastic answer.  A real rotor grows ~1e-3 of its radius at full speed (the
#: Ø200 at 23 000 rpm: 0.6 mm on 100 mm); a part that is held by nothing moves
#: kilometres.  Ten per cent sits between the two by three orders of magnitude
#: on either side.
RUNAWAY_DISPLACEMENT_FRAC = 0.1


class RotorRanAway(ValueError):
    """A solve in which a piece of the rotor was held by nothing.

    2026-09-09: the 40 mm spoke rotor (fourteen magnets between pole pieces, a
    0.1 mm gap above each magnet, parallel side walls) was solved with the
    Ø200's ``separation`` magnet–rotor contact.  The magnets slid outward, the
    active set cycled, the maximum displacement came out at 1.6e11 µm — and the
    route still returned SF 0.08 on the magnets and a stress map with one 2 GPa
    corner element, which read as a real result.  ``_torque_path`` had already
    written "a piece of the rotor is held by nothing and ran away" next to it;
    a solve that says that about itself is refused, not graded.  The route
    turns this into a 422 naming the contact pair to change.
    """

    def __init__(self, case: str, rpm: float, pair: Optional[str],
                 open_fraction: Optional[float], reasons: List[str]):
        self.case = case
        self.rpm = float(rpm)
        self.pair = pair
        self.open_fraction = open_fraction
        msg = (f"rotor stress at {case} ({rpm:,.0f} rpm) did not solve — a piece "
               f"of the rotor is held by nothing and ran away: "
               f"{'; '.join(reasons)}.")
        if pair:
            of = (f" and {open_fraction:.0%} of it is open"
                  if open_fraction is not None else "")
            msg += (f" The {pair} joint is 'separation'{of}: in this "
                    "small-displacement model the pocket does not retain the "
                    f"part. Make the {pair} contact bonded (a glued magnet), or "
                    "close the gap in the pocket so the faces touch at "
                    "standstill, and solve again.")
        super().__init__(msg)


def runaway_verdict(u_max_m: float, r_out_m: float, free_parts: List[str],
                    balance: Optional[float], ifaces: Dict[str, Any],
                    seating_capped: Optional[List[str]] = None
                    ) -> Optional[Dict[str, Any]]:
    """``None`` when the displacements are an elastic answer, else the reasons
    and the separation joint most likely responsible (the most open one).

    Four tripwires, any one of which is enough: a displacement past
    ``RUNAWAY_DISPLACEMENT_FRAC`` of the outer radius; a part the contact solve
    left with no load path at all; a part the SEATING step had to give up on
    because the surface it was falling towards is further away than
    ``contact.SEAT_TRAVEL_FRAC`` of the rotor's radius (2026-09-09 — a part that
    has to move that far has not been seated, it has escaped); the bore reacting
    a torque other than the one applied (``TORQUE_BALANCE_TOL`` — an identity on
    a healthy solve).  A contact loop that merely did not converge is NOT one of
    them: its answer is still graded, with ``contact.converged`` false beside it.
    """
    reasons: List[str] = []
    if r_out_m > 0.0 and u_max_m > RUNAWAY_DISPLACEMENT_FRAC * r_out_m:
        reasons.append(f"maximum displacement {u_max_m * 1e3:.3g} mm on a "
                       f"Ø{2e3 * r_out_m:.1f} mm rotor")
    if free_parts:
        reasons.append(f"{', '.join(free_parts)} float free")
    if seating_capped:
        reasons.append(f"{', '.join(seating_capped)} came loose and found no "
                       "surface within 5 % of the rotor radius to seat on")
    if balance is not None and abs(balance - 1.0) > TORQUE_BALANCE_TOL:
        reasons.append(f"the bore reacted {balance:.2f} of the applied torque")
    if not reasons:
        return None
    open_joints = [(lb, float(v.get("open_fraction") or 0.0))
                   for lb, v in (ifaces or {}).items()
                   if v and v.get("type") == "separation" and v.get("n_facets")]
    # WHICH joint is to blame.  When the solve NAMES the part that floated, the
    # joint is that part's own — not the widest-open one on the rotor
    # (2026-09-09).  The route bonds the joint named here and solves again, so
    # naming the wrong one glues a part that was never the problem: on the live
    # Ø200 the SHAFT floated (its hub contact is `separation` with no fit, so it
    # holds nothing), the widest-open joint was magnet↔iron, and the fallback
    # glued the MAGNETS — which hands their whole centrifugal load to the iron
    # and turns the answer from 421 µm / 1717 MPa in the band into 13 µm /
    # 220 MPa.  A design read as safe because the wrong joint was glued is the
    # one failure this fallback must never produce (user: "так у нас всё
    # раздельно").  A part with no separation joint of its own — nothing to
    # bond — leaves the choice to the open-fraction rule, and the refusal
    # stands.
    def _joint_of(part: str) -> Optional[tuple]:
        p = str(part).strip().lower()
        p = {"rotor": "rotor", "rotor_core": "rotor"}.get(p, p)
        hits = [(lb, of) for lb, of in open_joints if p in lb.lower()]
        return max(hits, key=lambda t: t[1]) if hits else None

    blamed = None
    for part in (free_parts or []) + list(seating_capped or []):
        blamed = _joint_of(part)
        if blamed:
            break
    pair, ofrac = (blamed if blamed else
                   max(open_joints, key=lambda t: t[1], default=(None, None)))
    return {"reasons": reasons, "pair": pair, "open_fraction": ofrac}


# ---------------------------------------------------------------------------
# Material properties
# ---------------------------------------------------------------------------

@dataclass
class PartMech:
    """The mechanical card of one rotor part, all of it read from the library."""
    part: str                       # 'rotor' | 'magnet' | 'sleeve' | 'shaft'
    material: str
    density: float                  # kg/m^3
    E: float                        # Pa   (hoop / fibre direction if orthotropic)
    nu: float                       # -
    #: Strength the part is checked against, and which one it is.  Steels and
    #: aluminium get their yield; magnets and CFRP are brittle, so they get the
    #: tensile strength — a magnet does not yield, it cracks.
    strength: float                 # Pa
    strength_kind: str              # 'yield' | 'tensile'
    compressive_strength: Optional[float] = None    # Pa, magnets only
    #: Strength ACROSS the fibres of an orthotropic sleeve (matrix dominated,
    #: ~50 MPa on a UD CFRP against 2500 MPa along the fibres).  Optional
    #: because no card in the library carries it yet: the safety-factor map
    #: skips the term rather than inventing a number for it.
    strength_transverse: Optional[float] = None     # Pa, sleeve only
    # Orthotropic extras (sleeve).  None -> isotropic.
    E_transverse: Optional[float] = None            # Pa, radial (across fibres)
    G: Optional[float] = None                       # Pa, in-plane shear
    #: THERMAL EXPANSION, 1/K — added 2026-09-07 for the user's "нужно
    #: универсально добавить температуру ротора, чтобы можно было задавать; для
    #: моторов без бандажа этот эффект вообще минимальный".  ``cte_1`` is the
    #: coefficient along MATERIAL AXIS 1 — the same axis ``part_C`` builds the
    #: stiffness in: the fibre = hoop direction of a wound sleeve, the
    #: MAGNETISATION direction of a magnet.  ``cte_2`` is the transverse one;
    #: ``None`` means the card is isotropic and ``cte_1`` applies to both.
    #: Both zero with ``cte_source == 'missing'`` is the honest "this material
    #: carries no CTE": the part simply does not expand and the response says
    #: so under ``thermal_notes`` — never a 422, because a rotor whose shaft
    #: card predates this field must still solve.
    cte_1: float = 0.0
    cte_2: Optional[float] = None
    cte_source: str = "missing"     # 'card' | 'missing'
    source_note: str = ""

    @property
    def orthotropic(self) -> bool:
        return self.E_transverse is not None and self.G is not None

    @property
    def cte_anisotropic(self) -> bool:
        return self.cte_2 is not None and self.cte_2 != self.cte_1

    def cte_pair(self) -> Tuple[float, float]:
        """(alpha_1, alpha_2) in 1/K — the transverse one defaults to axis 1."""
        return (self.cte_1, self.cte_1 if self.cte_2 is None else self.cte_2)


def _req(raw: dict, part: str, material: str, key: str, hint: str = "") -> float:
    v = raw.get(key)
    if v is None:
        raise MissingMechanicalProperty(part, material, key, hint)
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise MissingMechanicalProperty(part, material, key, "not a number")
    if not np.isfinite(f) or f <= 0:
        raise MissingMechanicalProperty(part, material, key, f"non-positive ({v})")
    return f


def _opt_number(raw: dict, key: str) -> Optional[float]:
    """A finite number from the card, or None — never an exception.

    Deliberately NOT ``_req``: that one rejects zero and negatives, and a
    carbon fibre's hoop CTE is NEGATIVE (-0.5 ppm/K — it contracts when
    heated).  Used only for the thermal-expansion keys, whose absence is a
    note in the response and not a refusal to solve (user 2026-09-07).
    """
    v = raw.get(key)
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def _read_cte(raw: dict) -> Tuple[float, Optional[float], str]:
    """(alpha_1, alpha_2 or None, source) in 1/K from a material card.

    Two spellings, because two kinds of material need them (2026-09-07):

      * ``cte_ppm_k``                       — isotropic (steels, aluminium, copper)
      * ``cte_ppm_k_1`` / ``cte_ppm_k_2``   — orthotropic, axis 1 = the same
        direction ``part_C``'s E1 uses: the fibre/hoop direction of a wound
        sleeve, the magnetisation direction of a sintered magnet.

    Stored in the yaml as ppm/K (12.0, not 1.2e-5) because that is how every
    datasheet quotes it and a stray exponent in a config file is invisible.
    """
    a1 = _opt_number(raw, "cte_ppm_k_1")
    a2 = _opt_number(raw, "cte_ppm_k_2")
    if a1 is not None:
        return a1 * 1e-6, (None if a2 is None else a2 * 1e-6), "card"
    iso = _opt_number(raw, "cte_ppm_k")
    if iso is not None:
        return iso * 1e-6, None, "card"
    return 0.0, None, "missing"


def _raw_material(category_hint: Sequence[str], name: str) -> Tuple[str, dict]:
    """Return (category, raw yaml dict) for a material.

    We go to the RAW record rather than the parsed dataclass because the
    mechanical keys are new and only some of the dataclasses carry them; the raw
    dict always does, and this keeps the source note (the yaml comment's intent)
    in one place.
    """
    from motor_ai_sim import materials as _mats

    lib = _mats._load()  # noqa: SLF001 - the library loader is the intended entry
    cats = list(category_hint) or ["steel", "magnet", "conductor", "insulator"]
    for cat in cats:
        raw = (lib.get(cat) or {}).get(name)
        if raw is not None:
            return cat, dict(raw)
    # Fall back to every category so a shaft assigned a steel still resolves.
    for cat in ("steel", "magnet", "conductor", "insulator"):
        raw = (lib.get(cat) or {}).get(name)
        if raw is not None:
            return cat, dict(raw)
    raise MissingMechanicalProperty(
        cats[0] if cats else "?", name, "record",
        "material not found in config/materials_library.yaml")


def part_mech(part: str, material_name: str,
              overrides: Optional[dict] = None) -> PartMech:
    """Build the mechanical card for one part.

    ``overrides`` is the per-request material payload (same shape the simulation
    routes accept as ``mat=``): a dict keyed by material name whose values are
    raw property dicts.  It wins over the library so a "what if I use 7075"
    request never has to touch the yaml.
    """
    from motor_ai_sim.materials import PART_CATEGORIES

    ov = (overrides or {}).get(material_name)
    if isinstance(ov, dict) and ov:
        # Same shape the simulation routes accept: {name: props} with an
        # optional 'category' key (material_context / _validation.parse_mat_override).
        cat = str(ov.get("category") or "")
        raw = dict(ov)
        if not cat:
            cat, _lib_raw = _raw_material(PART_CATEGORIES.get(part, ()), material_name)
    else:
        cat, raw = _raw_material(PART_CATEGORIES.get(part, ()), material_name)

    density = _req(raw, part, material_name, "density")
    E = _req(raw, part, material_name, "youngs_modulus_gpa") * 1e9
    nu = _req(raw, part, material_name, "poisson_ratio")

    if cat == "magnet":
        strength = _req(raw, part, material_name, "tensile_strength_mpa",
                        "NdFeB is brittle — it is checked in TENSION") * 1e6
        kind = "tensile"
        comp = raw.get("compressive_strength_mpa")
        comp = float(comp) * 1e6 if comp is not None else None
    elif cat == "insulator":
        # The CFRP sleeve: fibre-direction tensile strength is the burst limit.
        strength = _req(raw, part, material_name, "tensile_strength_mpa") * 1e6
        kind = "tensile"
        comp = None
    else:
        strength = _req(raw, part, material_name, "yield_strength_mpa") * 1e6
        kind = "yield"
        comp = None

    Et = raw.get("youngs_modulus_transverse_gpa")
    G = raw.get("shear_modulus_gpa")
    st = raw.get("tensile_strength_transverse_mpa")
    if st is None:
        st = raw.get("transverse_tensile_strength_mpa")
    cte1, cte2, cte_src = _read_cte(raw)
    return PartMech(
        part=part,
        material=material_name,
        density=density,
        E=E,
        nu=nu,
        strength=strength,
        strength_kind=kind,
        compressive_strength=comp,
        strength_transverse=float(st) * 1e6 if st is not None else None,
        E_transverse=float(Et) * 1e9 if Et is not None else None,
        G=float(G) * 1e9 if G is not None else None,
        cte_1=cte1,
        cte_2=cte2,
        cte_source=cte_src,
        source_note=str(raw.get("description") or ""),
    )


#: Which ``materials:`` assignment key feeds which structural part.
PART_ASSIGNMENT_KEY: Dict[str, str] = {
    "rotor": "rotor_core",
    "magnet": "magnet",
    "sleeve": "sleeve",
    "shaft": "shaft",
}

#: ``part_temps_c`` key -> the internal part name it sets the temperature of.
#: Deliberately the SAME vocabulary as ``PART_ASSIGNMENT_KEY`` (``rotor_core``,
#: not ``rotor``), so one machine part is named one way everywhere a caller can
#: see it — the materials map, the `/materials` route and now the temperatures.
PART_TEMP_KEY: Dict[str, str] = {v: k for k, v in PART_ASSIGNMENT_KEY.items()}


def resolve_part_materials(assignments: Optional[dict],
                           has_sleeve: bool,
                           overrides: Optional[dict] = None) -> Dict[str, PartMech]:
    """Mechanical cards for every rotor part present on this machine."""
    from motor_ai_sim.materials import DEFAULT_PART_MATERIAL

    a = dict(assignments or {})
    out: Dict[str, PartMech] = {}
    for part, key in PART_ASSIGNMENT_KEY.items():
        if part == "sleeve" and not has_sleeve:
            continue
        name = a.get(key) or DEFAULT_PART_MATERIAL.get(part)
        if not name:
            raise MissingMechanicalProperty(
                part, "(unassigned)", "material",
                f"no materials.{key} in the config")
        out[part] = part_mech(part, str(name), overrides)
    return out


# ---------------------------------------------------------------------------
# Plane-stress constitutive matrices (Voigt, engineering shear)
# ---------------------------------------------------------------------------
# Voigt order is [xx, yy, xy] with gamma_xy = 2*eps_xy.

def isotropic_C(E: float, nu: float) -> np.ndarray:
    """Plane-stress stiffness of an isotropic material."""
    f = E / (1.0 - nu * nu)
    return np.array([[f, f * nu, 0.0],
                     [f * nu, f, 0.0],
                     [0.0, 0.0, 0.5 * E / (1.0 + nu)]], dtype=float)


def orthotropic_C(E1: float, E2: float, nu12: float, G12: float) -> np.ndarray:
    """Plane-stress stiffness in the MATERIAL axes (1 = fibre, 2 = transverse)."""
    nu21 = nu12 * E2 / E1
    d = 1.0 - nu12 * nu21
    return np.array([[E1 / d, nu12 * E2 / d, 0.0],
                     [nu12 * E2 / d, E2 / d, 0.0],
                     [0.0, 0.0, G12]], dtype=float)


def rotate_C(C: np.ndarray, theta: np.ndarray) -> np.ndarray:
    """Rotate stiffness matrices from material axes to x-y.

    ``theta`` is the angle (rad) from +x to material axis 1, one per element.
    Classical lamination theory: Cbar = T^-1 C T^-T with the ENGINEERING-shear
    stress transformation T.  Returns (n, 3, 3).
    """
    c = np.cos(theta)
    s = np.sin(theta)
    n = theta.size
    T = np.zeros((n, 3, 3))
    T[:, 0, 0] = c * c
    T[:, 0, 1] = s * s
    T[:, 0, 2] = 2.0 * c * s
    T[:, 1, 0] = s * s
    T[:, 1, 1] = c * c
    T[:, 1, 2] = -2.0 * c * s
    T[:, 2, 0] = -c * s
    T[:, 2, 1] = c * s
    T[:, 2, 2] = c * c - s * s
    Tinv = np.linalg.inv(T)
    Cb = np.einsum("nij,jk,nlk->nil", Tinv, C, Tinv)
    return Cb


def part_C(pm: PartMech, centroid_angle: np.ndarray) -> np.ndarray:
    """(n, 3, 3) plane-stress stiffness for every element of one part."""
    if not pm.orthotropic:
        return np.broadcast_to(isotropic_C(pm.E, pm.nu),
                               (centroid_angle.size, 3, 3)).copy()
    Cm = orthotropic_C(pm.E, float(pm.E_transverse), pm.nu, float(pm.G))
    # Fibres run HOOP, so material axis 1 is the theta direction = phi + 90 deg.
    return rotate_C(Cm, centroid_angle + 0.5 * math.pi)


# ---------------------------------------------------------------------------
# Thermal strain, as an eigenstrain
# ---------------------------------------------------------------------------
# Added 2026-09-07.  User: "нужно универсально добавить температуру ротора,
# чтобы можно было задавать; для моторов без бандажа этот эффект вообще
# минимальный".
#
# WHY IT IS AN EIGENSTRAIN AND NOT A LOAD.  The solver already carries one
# stress-free strain — the sleeve's interference — through exactly the same
# channel: sigma = C:(eps - eps0), with the eigenstrain both driving the
# right-hand side (assemble_plane_stress) and subtracted again in
# recover_stress.  A uniform temperature rise is the SAME kind of quantity: a
# strain the material would take on if nothing held it.  Putting it here means
# it superposes with the interference for free, applies in every case
# (standstill / rated / overspeed / single) without a second code path, and — the
# check that matters — a FREE body heated uniformly comes back at zero stress,
# because the FE solution relaxes exactly into eps0 and the subtraction cancels.
#
# WHY THE SLEEVE IS THE WHOLE POINT.  A hoop-wound carbon band has alpha ~ 0
# along its fibres while the iron under it grows at 12 ppm/K.  Heat the rotor
# and the interference — and with it the sleeve hoop stress — GROWS.  Without a
# band a uniformly heated free rotor only carries the small iron/magnet
# CTE-mismatch stress, which is the user's "для моторов без бандажа этот эффект
# вообще минимальный".
#
# 2026-09-09 — AND IT IS THE ONLY POINT.  The per-part eigenstrain is no longer
# put into the main solve at all (``solve_rotor_stress(thermal_model=
# "band_fit")``): it is used to measure the free growth under the band and the
# band's own bore growth, whose difference becomes the band's interference at
# temperature, and everything else is solved as drawn.  See the docstring of
# ``solve_rotor_stress`` for the user's rule and the reason.

#: The temperature everything in the library is quoted at, i.e. the state in
#: which the geometry as drawn is stress-free.
REF_TEMP_C = 20.0


def thermal_eigenstrain(alpha_1: float, alpha_2: float, delta_t: float,
                        theta: np.ndarray) -> np.ndarray:
    """(n, 3) engineering Voigt thermal eigenstrain in the GLOBAL x-y frame.

    ``theta`` is the angle (rad) from +x to material axis 1, one per element —
    the same convention ``rotate_C`` takes, so a part's stiffness and its
    expansion are described in one frame and cannot drift apart.

    The local strain is diagonal, ``[alpha_1, alpha_2, 0] * delta_t``; rotating
    a diagonal strain tensor by ``theta`` gives

        eps_xx   = e1 cos^2 + e2 sin^2
        eps_yy   = e1 sin^2 + e2 cos^2
        gamma_xy = 2 (e1 - e2) sin cos

    which, with ``e2 = 0`` and ``theta = phi + 90 deg``, is term for term the
    hoop eigenstrain the interference fit already uses — the two were written
    against each other on purpose.
    """
    e1 = float(alpha_1) * float(delta_t)
    e2 = float(alpha_2) * float(delta_t)
    c, s = np.cos(theta), np.sin(theta)
    out = np.empty((theta.size, 3), dtype=float)
    out[:, 0] = e1 * c * c + e2 * s * s
    out[:, 1] = e1 * s * s + e2 * c * c
    out[:, 2] = 2.0 * (e1 - e2) * s * c
    return out


# ---------------------------------------------------------------------------
# The elasticity solve (mesh-agnostic — this is what the analytic tests drive)
# ---------------------------------------------------------------------------

@dataclass
class ElasticSolution:
    """One linear solve: nodal displacements plus recovered element stresses."""
    u_node: np.ndarray              # (n_vertices, 2) m — vertex displacements
    sigma_tri: np.ndarray           # (n_elements, 3) Pa — [xx, yy, xy] at centroid
    sigma_tri_max: np.ndarray       # (n_elements, 3) Pa — sample with max |vm|
    vm_tri: np.ndarray              # (n_elements,) Pa — max von Mises over samples
    #: |R^T f| / |f| — how far the load is from self-equilibrated.  Should be
    #: ~1e-3 or less on any real rotor; a big number means the geometry is not
    #: what it claims to be (a lopsided mesh, a part that fell out).
    rigid_residual: float = 0.0


def _voigt_grad(g):
    """Engineering Voigt strain from a vector field's gradient."""
    return g[0][0], g[1][1], g[0][1] + g[1][0]


def make_basis(mesh, order: int = 2):
    """The vector Lagrange basis every solve in this module uses."""
    from skfem import Basis, ElementTriP1, ElementTriP2, ElementVector

    elem = ElementVector(ElementTriP2() if order == 2 else ElementTriP1())
    return Basis(mesh, elem, intorder=3 if order == 2 else 2), elem


def assemble_plane_stress(mesh,
                          C_elem: np.ndarray,
                          rho_elem: np.ndarray,
                          eps0_elem: Optional[np.ndarray] = None,
                          order: int = 2):
    """(basis, K, f_rot_unit, f_eigen) for the given mesh and materials.

    Split out of ``solve_plane_stress`` for v2: the contact solve runs the same
    stiffness through many active-set iterations and three load cases, so
    assembling it once and scaling the centrifugal load by omega^2 turns the
    per-iteration cost into one linear solve.  ``f_rot_unit`` is the body force
    at omega = 1 rad/s (it scales exactly with omega^2); ``f_eigen`` is the
    interference-fit load and does not scale.
    """
    from skfem import BilinearForm, LinearForm, asm

    basis, _elem = make_basis(mesh, order)
    ne = mesh.t.shape[1]
    xq = basis.global_coordinates().value          # (2, ne, nq)
    nq = xq.shape[-1]

    def per_elem(a):
        return np.broadcast_to(np.asarray(a, dtype=float).reshape(ne, 1),
                               (ne, nq))

    kw = {f"C{i}{j}": per_elem(C_elem[:, i, j])
          for i in range(3) for j in range(3)}

    @BilinearForm
    def stiffness(u, v, w):
        eu = _voigt_grad(u.grad)
        ev = _voigt_grad(v.grad)
        out = 0.0
        for i in range(3):
            si = (w[f"C{i}0"] * eu[0] + w[f"C{i}1"] * eu[1] + w[f"C{i}2"] * eu[2])
            out = out + si * ev[i]
        return out

    K = asm(stiffness, basis, **kw)

    # ── body force: rho * omega^2 * (x, y) ──────────────────────────────────
    @LinearForm
    def body(v, w):
        return w["fx"] * v[0] + w["fy"] * v[1]

    rho_q = per_elem(rho_elem)
    f_rot = asm(body, basis, fx=rho_q * xq[0], fy=rho_q * xq[1])

    # ── eigenstrain: + integral of (C eps0) : eps(v) ────────────────────────
    f_eig = np.zeros_like(f_rot)
    if eps0_elem is not None and np.any(eps0_elem):
        s0 = np.einsum("nij,nj->ni", C_elem, eps0_elem)  # (ne, 3)

        @LinearForm
        def eigen(v, w):
            ev = _voigt_grad(v.grad)
            return w["s0"] * ev[0] + w["s1"] * ev[1] + w["s2"] * ev[2]

        f_eig = asm(eigen, basis,
                    s0=per_elem(s0[:, 0]), s1=per_elem(s0[:, 1]),
                    s2=per_elem(s0[:, 2]))
    return basis, K, f_rot, f_eig


def recover_stress(mesh, u: np.ndarray, C_elem: np.ndarray,
                   eps0_elem: Optional[np.ndarray] = None,
                   order: int = 2) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(sigma_centroid, sigma_at_peak_sample, von_Mises_max) per element.

    Sampled at the 3 corners AND the centroid of every element: the centroid
    value is what the field map shows, the max over the four is what the safety
    factors are quoted from (a P2 field's peak is at a corner).
    """
    from skfem import Basis as _Basis

    _b, elem = make_basis(mesh, order)
    ne = mesh.t.shape[1]
    samples = np.array([[1 / 3, 1.0, 0.0, 0.0],
                        [1 / 3, 0.0, 1.0, 0.0]])  # (x, y) reference coords
    sb = _Basis(mesh, elem, quadrature=(samples, np.ones(4) / 4.0))
    gx = sb.interpolate(u).grad  # (2, 2, ne, 4)
    exx, eyy, gxy = gx[0][0], gx[1][1], gx[0][1] + gx[1][0]
    eps = np.stack([exx, eyy, gxy], axis=0)         # (3, ne, 4)
    # sigma = C:(eps - eps0).  Subtracting the eigenstrain is not optional: a
    # FREE ring with a shrink eigenstrain relaxes into it and must come back
    # STRESS-FREE, and without this line it came back at -178 MPa — the exact
    # value of C:eps0 — which then flipped the sign of the whole interference
    # fit (the sleeve read as hoop COMPRESSION).
    if eps0_elem is not None:
        eps = eps - eps0_elem.T[:, :, None]
    sig = np.einsum("nij,jnk->ink", C_elem, eps)    # (3, ne, 4)
    sxx, syy, sxy = sig[0], sig[1], sig[2]
    vm = np.sqrt(np.maximum(sxx ** 2 - sxx * syy + syy ** 2 + 3.0 * sxy ** 2, 0.0))
    imax = np.argmax(vm, axis=1)
    idx = np.arange(ne)
    sigma_max = np.stack([sxx[idx, imax], syy[idx, imax], sxy[idx, imax]], axis=1)
    sigma_cen = np.stack([sxx[:, 0], syy[:, 0], sxy[:, 0]], axis=1)
    return sigma_cen, sigma_max, vm.max(axis=1)


def solve_plane_stress(mesh,
                       C_elem: np.ndarray,
                       rho_elem: np.ndarray,
                       omega: float,
                       eps0_elem: Optional[np.ndarray] = None,
                       order: int = 2,
                       cyclic=None) -> ElasticSolution:
    """Solve rho*omega^2*r body force + eigenstrain on a free (unsupported) body.

    Parameters
    ----------
    mesh : skfem.MeshTri with coordinates in METRES
    C_elem : (n_elements, 3, 3) plane-stress stiffness, Pa
    rho_elem : (n_elements,) density, kg/m^3
    omega : rad/s
    eps0_elem : (n_elements, 3) engineering Voigt eigenstrain, or None
    order : 1 or 2 (P1 / P2 vector Lagrange)

    The three rigid-body modes are removed by a bordered system
    ``[[K, R], [R^T, 0]]`` rather than by pinning nodes: pinning invents a
    reaction force and a stress concentration that would be read as a real
    result right where the engineer looks.

    This is the WELDED (perfectly bonded) solve of v1 — one body, no contact.
    It is what the analytic ring tests drive and what the contact solver is
    checked against with every pair set to ``bonded``.

    ``cyclic`` (2026-09-09) is a ``contact.CyclicTies`` — the two cut faces of a
    one-pole SECTOR, tied by ``u_B = R u_A``.  Its rows join the border, and the
    rigid modes then drop from three to ONE (the rotation): a translation is not
    a periodic field, so the ties have already removed the two of them, and
    bordering them again would make the constraint set rank deficient.  See
    ``simulation.mechanical.symmetry``.
    """
    import scipy.sparse as sp
    import scipy.sparse.linalg as spla

    basis, K, f_rot, f_eig = assemble_plane_stress(
        mesh, C_elem, rho_elem, eps0_elem, order=order)
    f = f_rot * (omega ** 2) + f_eig
    if cyclic is not None and cyclic.n_rows:
        return _solve_plane_stress_cyclic(mesh, basis, K, f, C_elem, eps0_elem,
                                          order, cyclic)

    # ── remove the 3 rigid-body modes ───────────────────────────────────────
    # K is singular with a 3-dimensional null space (2 translations + 1
    # rotation), because the rotor is held by nothing.  Rather than pinning a
    # node — which invents a reaction force and a stress concentration exactly
    # where an engineer would read a result — the null space is constrained by a
    # bordered system,  [[K, R], [R^T, 0]] [u; lam] = [f; 0],  i.e. R^T u = 0.
    # Because K is symmetric, range(K) is orthogonal to null(K) = range(R): the
    # multipliers absorb whatever part of f is NOT self-equilibrated and u comes
    # back as the pseudo-inverse solution of the rest.  (The regularised form
    # K + alpha R R^T would be SPD but R R^T is DENSE — 34 GB on this mesh.)
    #
    # R must cover EVERY dof, midside nodes included: a P2 rigid mode has
    # midside entries, and an R built from the vertex dofs alone left that part
    # of the null space live — the stresses were still right (a rigid mode is
    # strain-free) but the DISPLACEMENTS came back as hundreds of kilometres.
    ndof = K.shape[0]
    ix = np.arange(0, ndof, 2)          # ElementVector interleaves x, y
    iy = ix + 1
    px, py = basis.doflocs[0], basis.doflocs[1]
    R = np.zeros((ndof, 3))
    R[ix, 0] = 1.0                       # x translation
    R[iy, 1] = 1.0                       # y translation
    R[ix, 2] = -py[ix]                   # rotation about z
    R[iy, 2] = px[iy]
    R, _ = np.linalg.qr(R)               # orthonormal null-space basis
    # Residual rigid load: a rotor's centrifugal load is self-equilibrated by
    # symmetry, so this is rounding noise — but measure it rather than assume.
    rigid_residual = float(np.abs(R.T @ f).max()
                           / max(float(np.linalg.norm(f)), 1e-30))
    # Scale the border so the multiplier rows are not ~1e10 smaller than K's.
    Rs = sp.csr_matrix(R * math.sqrt(float(K.diagonal().mean())))
    A = sp.bmat([[K, Rs], [Rs.T, None]], format="csc")
    u = spla.spsolve(A, np.concatenate([f, np.zeros(3)]))[:ndof]

    sigma_cen, sigma_max, vm_max = recover_stress(mesh, u, C_elem, eps0_elem,
                                                  order=order)

    # vertex displacements (nodal_dofs[c] are the vertex dofs of component c)
    nd = basis.nodal_dofs
    u_node = np.stack([u[nd[0]], u[nd[1]]], axis=1)

    return ElasticSolution(
        u_node=u_node,
        sigma_tri=sigma_cen,
        sigma_tri_max=sigma_max,
        vm_tri=vm_max,
        rigid_residual=rigid_residual,
    )


def _solve_plane_stress_cyclic(mesh, basis, K, f, C_elem, eps0_elem, order,
                               cyclic) -> ElasticSolution:
    """``solve_plane_stress`` on a SECTOR: cyclic ties + the rotation border.

    Split out rather than branched inline so the full-rotor path above is
    literally the arithmetic it always ran (2026-09-09).  The bordered system is

        [ K      Rrot  Gcyc^T ] [ u    ]   [ f ]
        [ Rrot^T 0     0      ] [ lam  ] = [ 0 ]
        [ Gcyc   0     0      ] [ mu   ]   [ 0 ]

    with ``Gcyc`` the ``u_B - R u_A = 0`` rows.  ``rigid_residual`` is measured
    on the ROTATION only: a wedge's centrifugal load is deliberately not
    balanced in translation — the missing sectors are what balance it, and
    saying so is what the ties are for — so the three-mode question has no
    meaning here and asking it would report ~0.5 on a healthy model.
    """
    import scipy.sparse as sp
    import scipy.sparse.linalg as spla

    ndof = K.shape[0]
    ix = np.arange(0, ndof, 2)
    iy = ix + 1
    px, py = basis.doflocs[0], basis.doflocs[1]
    R = np.zeros((ndof, 1))
    R[ix, 0] = -py[ix]
    R[iy, 0] = px[iy]
    R, _ = np.linalg.qr(R)
    rigid_residual = float(np.abs(R.T @ f).max()
                           / max(float(np.linalg.norm(f)), 1e-30))

    m = int(cyclic.dofs_a.shape[0])
    Rm = np.asarray(cyclic.R, dtype=float)
    rows = np.repeat(np.arange(2 * m), 3)
    cols = np.concatenate([
        np.stack([cyclic.dofs_b[:, 0], cyclic.dofs_a[:, 0],
                  cyclic.dofs_a[:, 1]], axis=1),
        np.stack([cyclic.dofs_b[:, 1], cyclic.dofs_a[:, 0],
                  cyclic.dofs_a[:, 1]], axis=1)]).reshape(-1)
    vals = np.concatenate([
        np.tile([1.0, -Rm[0, 0], -Rm[0, 1]], (m, 1)),
        np.tile([1.0, -Rm[1, 0], -Rm[1, 1]], (m, 1))]).reshape(-1)
    s = math.sqrt(float(K.diagonal().mean()))
    G = sp.coo_matrix((vals * s, (rows, cols)), shape=(2 * m, ndof)).tocsr()
    C = sp.vstack([sp.csr_matrix(R * s).T, G], format="csr")
    A = sp.bmat([[K, C.T], [C, None]], format="csc")
    sol = spla.spsolve(A, np.concatenate([f, np.zeros(C.shape[0])]))
    u = sol[:ndof]

    sigma_cen, sigma_max, vm_max = recover_stress(mesh, u, C_elem, eps0_elem,
                                                  order=order)
    nd = basis.nodal_dofs
    return ElasticSolution(
        u_node=np.stack([u[nd[0]], u[nd[1]]], axis=1),
        sigma_tri=sigma_cen, sigma_tri_max=sigma_max, vm_tri=vm_max,
        rigid_residual=rigid_residual)


def free_radial_growth(mesh,
                       C_elem: np.ndarray,
                       eps0_elem: np.ndarray,
                       live_elem: np.ndarray,
                       node_mask: np.ndarray,
                       order: int = 2,
                       soft: float = 1e-4,
                       cyclic=None) -> float:
    """Mean radial displacement (m) of ``node_mask`` when only ``live_elem``
    is stiff and thermally loaded.

    This is how ``interference_effective_mm`` is COMPUTED rather than asserted
    (user 2026-09-07 asked for the effective fit at temperature).  The
    interference a sleeve actually feels is the FREE mismatch: how much the
    parts under it would have grown on their own, minus how much the band's own
    bore grew.  Measuring the two separately is the only honest way to get it —
    in the loaded solve the two are in contact and move together by
    construction, so their difference there is zero and says nothing.

    Rather than cut a sub-mesh (whose parts fall apart into free bodies once the
    interfaces are split), the parts that must not RESTRAIN are left in place
    with their stiffness scaled by ``soft``.  1e-4 leaves them ~0.01 % of the
    restraint — below the mesh error — while keeping K well conditioned; zeroing
    them outright makes it singular, and the bordered rigid-mode solve then
    returns kilometres rather than micrometres.

    ``cyclic`` (2026-09-09) carries the sector's ties into this measurement too.
    It is not optional there: "free" means free of the parts around it, never
    free of the rest of the ring, and a wedge of a hoop-wound band left
    genuinely free simply opens at the cut — the growth it then reports is a
    slit ring's, not a ring's.  With the ties in, the sector and the full rotor
    measure the same fit: 72.7554 against 72.7373 µm under the band and
    -3.30388 against -3.30386 µm at its bore, on the sleeved spoke fixture at
    150/80 °C.
    """
    C = C_elem.copy()
    C[~live_elem] = C_elem[~live_elem] * soft
    eps = np.zeros_like(eps0_elem)
    eps[live_elem] = eps0_elem[live_elem]
    sol = solve_plane_stress(mesh, C, np.zeros(C.shape[0], dtype=float), 0.0,
                             eps, order=order, cyclic=cyclic)
    ps = mesh.p.T
    r = np.hypot(ps[:, 0], ps[:, 1])
    ur = ((sol.u_node[:, 0] * ps[:, 0] + sol.u_node[:, 1] * ps[:, 1])
          / np.maximum(r, 1e-12))
    return float(ur[node_mask].mean())


# ---------------------------------------------------------------------------
# Rotor-solids mesh
# ---------------------------------------------------------------------------

#: Part ids in the returned tag array — defined in ``contact`` because the
#: contact pair table is keyed on them, re-exported here because this is the
#: module everything else imports.
from motor_ai_sim.simulation.mechanical.contact import (  # noqa: E402,F401
    PART_MAGNET, PART_NAMES, PART_ROTOR, PART_SHAFT, PART_SLEEVE,
    interface_facets)   # re-exported: callers have imported it from here since v1


@dataclass
class RotorMesh:
    mesh: Any                       # skfem MeshTri, coordinates in METRES
    part_tri: np.ndarray            # (n_elements,) PART_* id
    magnet_id_tri: np.ndarray       # (n_elements,) magnet index, -1 elsewhere
    outlines: List[List[List[float]]] = field(default_factory=list)  # mm
    r_sleeve_mean_m: float = 0.0
    r_rotor_od_m: float = 0.0
    #: seconds gmsh spent BUILDING this mesh — kept when the memo hands it out
    #: again, because "this mesh cost 3.2 s" stays true however often it is
    #: reused; ``from_memo`` is what says nothing was meshed just now
    build_s: float = 0.0
    #: True when this object is a copy of a memoised mesh, i.e. nothing was meshed
    from_memo: bool = False


# ---------------------------------------------------------------------------
# The mesh memo
# ---------------------------------------------------------------------------
# User 2026-09-06: "по поводу сетки — как я понял, она строится отдельно, и ей
# тоже нужно как-то управлять".  The panel now has a Build mesh button, so the
# same rotor gets meshed by the /mesh route and then again by the very next
# Solve — twice the gmsh seconds for one identical answer.  This memo keys the
# built mesh on the SOLIDS themselves (a hash of the polygon WKB) plus the three
# size settings, so it is correct without anyone having to remember to clear it:
# a different cross-section hashes differently and simply misses.
_MESH_MEMO: "OrderedDict[tuple, RotorMesh]" = OrderedDict()
_MESH_MEMO_MAX = 4


def _mesh_memo_key(polys: dict, mesh_size_mm: float, min_size_mm: float,
                   weld_tol_mm: float,
                   periodic: Optional[Tuple[float, float]] = None) -> Optional[tuple]:
    """A hash of exactly what the mesher reads, or None if it cannot be taken.

    Hashing the geometry rather than trusting a caller-supplied fingerprint is
    deliberate: this function is called from three routes and two solvers, and a
    memo that can be poisoned by a stale key would hand one machine's mesh to
    another.  A shapely object with no ``wkb`` (or any other surprise) returns
    None, which disables the memo for that call rather than guessing.
    """
    try:
        h = hashlib.blake2b(digest_size=16)
        for name in ("shaft", "rotor", "sleeve"):
            g = polys.get(name)
            h.update(b"\x00" + (bytes(g.wkb) if g is not None else b""))
        for mp, _pol in (polys.get("magnets") or []):
            h.update(b"\x01" + bytes(mp.wkb))
        sr = polys.get("sleeve_r_mm")
        h.update(repr(tuple(float(v) for v in sr) if sr else ()).encode())
        key = (h.hexdigest(), round(float(mesh_size_mm), 4),
               round(float(min_size_mm), 4), round(float(weld_tol_mm), 4))
        # The cut angles are part of the ANSWER, not of the request's phrasing:
        # a periodic mesh has face B's node distribution copied off face A and
        # is a different mesh from the free one of the same wedge.  Appended
        # only when asked for (2026-09-09), so every key already in the memo is
        # byte for byte the one it was filed under.
        if periodic is not None:
            key = key + (round(float(periodic[0]), 12),
                         round(float(periodic[1]), 12))
        return key
    except Exception:  # noqa: BLE001 - a memo that cannot key is simply skipped
        return None


def _memo_copy(rm: RotorMesh) -> RotorMesh:
    """A private view of a memoised mesh.

    The skfem ``MeshTri`` is treated as read-only by every caller, but the tag
    arrays are plain numpy and handing the same object to two solves is how a
    shared cache turns into a heisenbug.  Copying two int arrays is microseconds
    against seconds of gmsh.
    """
    return RotorMesh(mesh=rm.mesh, part_tri=rm.part_tri.copy(),
                     magnet_id_tri=rm.magnet_id_tri.copy(),
                     outlines=[list(o) for o in rm.outlines],
                     r_sleeve_mean_m=rm.r_sleeve_mean_m,
                     r_rotor_od_m=rm.r_rotor_od_m,
                     build_s=rm.build_s, from_memo=True)


def clear_mesh_memo() -> int:
    n = len(_MESH_MEMO)
    _MESH_MEMO.clear()
    return n


def build_rotor_mesh(polys: dict,
                     mesh_size_mm: float = 1.5,
                     min_size_mm: float = 0.25,
                     weld_tol_mm: float = 0.02,
                     progress=None,
                     periodic: Optional[Tuple[float, float]] = None) -> RotorMesh:
    """``_build_rotor_mesh`` with the process-level memo in front of it.

    Everything about the mesh is in the inner function; this one only answers
    "have we already meshed exactly these solids at exactly this size".

    ``progress`` (optional) follows the project's shared callback contract (see
    ``motor_ai_sim.progress``): ``progress(done, total, phase)`` with a local
    budget of ONE step.  It is worth reporting because gmsh is frequently the
    largest single block of a structural solve — on the 200 mm rotor it is tens
    of seconds during which nothing else has anything to say — and because a
    memo hit skips all of it, which the phase names so "mesh build" flashing past
    is not read as a mesh that was silently not built.

    ``periodic`` (2026-09-09) is ``(angle_A, angle_B)`` in radians: the two cut
    rays of a cyclic-symmetry SECTOR.  gmsh is then asked to copy face A's 1-D
    node distribution onto face B through the rotation between them, so the two
    faces carry matching nodes and ``symmetry.build_cyclic_ties`` can tie them
    one to one.  ``None`` is the full 360° rotor and nothing about the mesher
    changes.
    """
    key = _mesh_memo_key(polys, mesh_size_mm, min_size_mm, weld_tol_mm,
                         periodic)
    if key is not None:
        hit = _MESH_MEMO.get(key)
        if hit is not None:
            _MESH_MEMO.move_to_end(key)
            if progress is not None:
                progress(1, 1, "mesh build (reused)")
            return _memo_copy(hit)

    if progress is not None:
        progress(0, 1, "mesh build (gmsh)")
    t0 = time.perf_counter()
    rm = _build_rotor_mesh(polys, mesh_size_mm=mesh_size_mm,
                           min_size_mm=min_size_mm, weld_tol_mm=weld_tol_mm,
                           periodic=periodic)
    rm.build_s = round(time.perf_counter() - t0, 3)
    if key is not None:
        _MESH_MEMO[key] = _memo_copy(rm)
        while len(_MESH_MEMO) > _MESH_MEMO_MAX:
            _MESH_MEMO.popitem(last=False)
    if progress is not None:
        progress(1, 1, "mesh build (gmsh)")
    return rm


def _set_periodic_curves(gmsh, angle_a: float, angle_b: float,
                         tol_mm: float = 1e-6) -> int:
    """Tie the geometry curves of cut face B to those of face A, in gmsh.

    Called between ``fragment`` and ``generate(2)``: gmsh then meshes face A and
    COPIES its 1-D node distribution onto face B through the affine transform,
    which is the only way to get two faces whose nodes match to machine
    precision.  Returns how many curve pairs were tied.

    Written 2026-09-09 with the cyclic-symmetry sector (see
    ``simulation.mechanical.symmetry``).  The pairing is geometric: a curve is
    "on face A" when both of its endpoints AND its mid-parameter point lie on
    the ray (the mid-point test is what stops a chord that merely touches the
    ray at one end from being claimed), and its twin is the face-B curve whose
    endpoints are its own turned by the sector angle.  Anything ambiguous is a
    ValueError — a sector meshed without the tie looks perfectly healthy and is
    not a cyclic model at all.
    """
    th = angle_b - angle_a
    ca, sa = math.cos(angle_a), math.sin(angle_a)
    cb, sb = math.cos(angle_b), math.sin(angle_b)
    ct, st = math.cos(th), math.sin(th)

    def _pts(tag):
        out = []
        for _d, t in gmsh.model.getBoundary([(1, tag)], combined=False,
                                            oriented=False):
            xyz = gmsh.model.getValue(0, abs(int(t)), [])
            out.append((float(xyz[0]), float(xyz[1])))
        lo, hi = gmsh.model.getParametrizationBounds(1, tag)
        xyz = gmsh.model.getValue(1, tag, [0.5 * (float(lo[0]) + float(hi[0]))])
        out.append((float(xyz[0]), float(xyz[1])))
        return out

    def _on(pp, c, s):
        return all(abs(x * s - y * c) < tol_mm and (x * c + y * s) > tol_mm
                   for x, y in pp)

    face_a, face_b = {}, {}
    for _d, tag in gmsh.model.getEntities(1):
        pp = _pts(tag)
        if _on(pp, ca, sa):
            face_a[int(tag)] = pp
        elif _on(pp, cb, sb):
            face_b[int(tag)] = pp
    if not face_a and not face_b:
        return 0
    if len(face_a) != len(face_b):
        raise ValueError(
            f"the sector's cut faces carry {len(face_a)} and {len(face_b)} "
            "geometry curves — they are not congruent, so no periodic mesh can "
            "be built (the polygons were not snapped)")

    aff = [ct, -st, 0.0, 0.0,
           st, ct, 0.0, 0.0,
           0.0, 0.0, 1.0, 0.0,
           0.0, 0.0, 0.0, 1.0]
    n_tied = 0
    used = set()
    for tb, pb in face_b.items():
        rb = sorted(math.hypot(x, y) for x, y in pb)
        best, best_d = None, math.inf
        for ta, pa in face_a.items():
            if ta in used:
                continue
            ra = sorted(math.hypot(x, y) for x, y in pa)
            if len(ra) != len(rb):
                continue
            d = max(abs(u - v) for u, v in zip(ra, rb))
            if d < best_d:
                best, best_d = ta, d
        if best is None or best_d > tol_mm:
            raise ValueError(
                f"cut-face curve {tb} has no match on the other face "
                f"(closest is {best_d:.3g} mm out) — the wedge's two faces are "
                "not congruent and the sector cannot be meshed periodically")
        used.add(best)
        gmsh.model.mesh.setPeriodic(1, [tb], [best], aff)
        n_tied += 1
    return n_tied


def _build_rotor_mesh(polys: dict,
                      mesh_size_mm: float = 1.5,
                      min_size_mm: float = 0.25,
                      weld_tol_mm: float = 0.02,
                      periodic: Optional[Tuple[float, float]] = None) -> RotorMesh:
    """Conforming triangle mesh of the rotor SOLIDS from the CadQuery polygons.

    Same polygons the magnetic FEM meshes (``CadQueryMotor.get_2d_polygons``), so
    the structural answer is about the machine that was actually solved — every
    fillet, bridge and pocket included.  gmsh's OCC ``fragment`` welds coincident
    edges, which is what makes the magnet/iron and sleeve/rotor interfaces share
    nodes (= the bonded assumption in the module docstring).

    ``periodic=(angle_A, angle_B)`` (2026-09-09) meshes a cyclic-symmetry
    SECTOR: after the fragment, every curve lying on cut ray A is paired with
    the curve that is its image under the rotation ``angle_B - angle_A``, and
    ``gmsh.model.mesh.setPeriodic`` copies A's node distribution onto B.  The
    pairing is by the ROTATED endpoints, not by tag order, and a face-A curve
    with no image (or two) is a refusal rather than a silently free mesh — the
    whole method rests on the two faces carrying the same nodes.  The polygons
    must already be snapped congruent (``symmetry.sector_polys``); a mismatch
    of microns is enough to leave a curve unpaired here.
    """
    import gmsh
    from shapely.geometry import MultiPolygon
    from shapely.prepared import prep
    from skfem import MeshTri

    from motor_ai_sim.simulation.mesher import _repair_needles
    from motor_ai_sim.simulation.sb_domains import _GMSH_LOCK

    parts: List[Tuple[int, int, Any]] = []  # (PART_*, magnet index, polygon)
    if polys.get("shaft") is not None:
        parts.append((PART_SHAFT, -1, polys["shaft"]))
    if polys.get("rotor") is not None:
        parts.append((PART_ROTOR, -1, polys["rotor"]))
    for i, (mp, _pol) in enumerate(polys.get("magnets") or []):
        parts.append((PART_MAGNET, i, mp))
    if polys.get("sleeve") is not None:
        parts.append((PART_SLEEVE, -1, polys["sleeve"]))
    if not parts:
        raise ValueError("no rotor solids in the geometry — nothing to solve")

    _GMSH_LOCK.acquire()
    try:
        try:
            gmsh.initialize([], interruptible=False)
        except TypeError:
            gmsh.initialize()
        try:
            gmsh.option.setNumber("General.Terminal", 0)
            gmsh.option.setNumber("Mesh.MeshSizeMin", min_size_mm)
            gmsh.option.setNumber("Mesh.MeshSizeMax", mesh_size_mm)
            gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 45)
            gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
            gmsh.option.setNumber("Mesh.Algorithm", 6)
            gmsh.option.setNumber("Geometry.Tolerance", 1e-5)
            # 10 um: the same boolean tolerance the magnetic mesher uses to weld
            # CadQuery's few-micron cross-polygon slivers.
            gmsh.option.setNumber("Geometry.ToleranceBoolean", 1e-2)
            gmsh.model.add("rotor_mech")
            occ = gmsh.model.occ

            def add_ring(coords) -> int:
                # OCC's addPlaneSurface([outer, *holes]) only cuts the holes when
                # every loop runs the SAME way round.  Shapely hands out CCW
                # exteriors and CW interiors, so an unnormalised interior did not
                # cut: the rotor came back as a full DISK covering the shaft, the
                # sleeve as a disk covering the whole rotor, and the fragment
                # then split them into overlapping pieces.  Normalise to CCW.
                # Strip sub-tolerance fold-back needles first.  On the Ø50
                # straight pocket at magnet_up_gap = 0.05 the rotor ring walks
                # out to a 256-gon OD station and straight back to the pocket
                # corner at TWO of the fourteen poles (measured 2026-09-21:
                # interior angle 0.000°, r = 14.637 mm, poles at 85.775° and
                # 265.775°) — which pole it hits depends only on where the
                # fixed angular grid falls relative to that pole, so it is one
                # bad pocket out of fourteen and looks like magic.  The same
                # repair the magnetic mesher runs (mesher._repair_needles)
                # snaps the corner onto the chord instead of deleting a shared
                # station, so the rings still meet exactly.
                cs, _n_needle, _w_needle = _repair_needles(list(coords)[:-1])
                if _n_needle:
                    _log.info("rotor mesh: %d sub-tolerance fold-back(s) "
                              "removed from a ring (worst %.2f um)",
                              _n_needle, _w_needle * 1000.0)
                a2 = 0.0
                for i in range(len(cs)):
                    x0, y0 = cs[i]
                    x1, y1 = cs[(i + 1) % len(cs)]
                    a2 += x0 * y1 - x1 * y0
                if a2 < 0:
                    cs = cs[::-1]
                pts: List[int] = []
                prev = None
                for (x, y) in cs:
                    if prev is not None and abs(x - prev[0]) < 1e-4 and abs(y - prev[1]) < 1e-4:
                        continue
                    pts.append(occ.addPoint(float(x), float(y), 0.0))
                    prev = (x, y)
                lines = [occ.addLine(pts[i], pts[(i + 1) % len(pts)])
                         for i in range(len(pts))]
                return occ.addCurveLoop(lines)

            surfs = []
            for _pid, _mid, poly in parts:
                geoms = poly.geoms if isinstance(poly, MultiPolygon) else [poly]
                for g in geoms:
                    loops = [add_ring(g.exterior.coords)]
                    loops += [add_ring(r.coords) for r in g.interiors]
                    surfs.append((2, occ.addPlaneSurface(loops)))
            occ.synchronize()
            occ.fragment(surfs, [])
            occ.synchronize()
            if periodic is not None:
                _set_periodic_curves(gmsh, float(periodic[0]),
                                     float(periodic[1]))
            gmsh.model.mesh.generate(2)

            ntags, ncoord, _ = gmsh.model.mesh.getNodes()
            etypes, _etags, enodes = gmsh.model.mesh.getElements(2)
            coords = np.asarray(ncoord, dtype=float).reshape(-1, 3)[:, :2]
            order = np.argsort(np.asarray(ntags, dtype=np.int64))
            tag2idx = np.zeros(int(np.max(ntags)) + 1, dtype=np.int64)
            tag2idx[np.asarray(ntags, dtype=np.int64)[order]] = np.arange(len(ntags))
            pts = coords[order]
            tris = []
            for et, en in zip(etypes, enodes):
                if int(et) == 2:
                    tris.append(tag2idx[np.asarray(en, dtype=np.int64)].reshape(-1, 3))
            if not tris:
                raise RuntimeError("gmsh produced no triangles for the rotor solids")
            t = np.vstack(tris)
        finally:
            gmsh.finalize()
    finally:
        _GMSH_LOCK.release()

    # ── drop unused nodes ───────────────────────────────────────────────────
    used = np.unique(t)
    remap = -np.ones(pts.shape[0], dtype=np.int64)
    remap[used] = np.arange(used.size)
    pts = pts[used]
    t = remap[t]

    # ── WELD near-coincident nodes ──────────────────────────────────────────
    # The bonded assumption needs the parts to SHARE nodes, and gmsh's fragment
    # does not always deliver that: the rotor's bore and the shaft's OD describe
    # the same circle but CadQuery samples them at different angles, so the two
    # rings interleave and only some of the boundary welds.  Measured on the live
    # Ø124 rotor: the mesh came out in TWO islands (3028 shaft triangles floating
    # free), the free island had 3 extra rigid-body modes the null-space
    # constraint did not cover, and the displacements came back as 20 000 km.
    # The stresses were fine, which is exactly what makes this worth welding
    # explicitly instead of hoping: a rigid mode is strain-free, so a broken
    # model can look perfectly healthy in every stress number it prints.
    # 20 µm is far below the 0.25 mm minimum element and below any real flux
    # barrier, so nothing that is physically separate is joined by this.
    if weld_tol_mm > 0:
        q = np.floor(pts / weld_tol_mm).astype(np.int64)
        # Check the neighbouring buckets too, so a pair straddling a bucket edge
        # still welds — ALL EIGHT of them (2026-09-21).  The bucket key used to
        # be `round`, which puts a point in the cell its coordinate is NEAREST
        # to, while the scan only looked at the (0, -1) x (0, -1) corner; two
        # points 15 µm apart then landed in cells (696, 218) and (697, 217) and
        # were never compared.  That is not academic: on the owner's Ø50
        # straight pocket exactly such a pair — the magnet's fillet junction and
        # a gmsh node 15 µm along its own edge — survived the weld as a third
        # COLLINEAR node, and the triangle spanning the three came out with a
        # 0.00° angle and 2e-18 mm² of area.  Fifteen of them per rotor was
        # enough to freeze the contact solve (every pair closed to 2e-16 µm, the
        # rotor deforming 0.05 µm instead of 6, the bore reacting 0.5 % of the
        # applied torque) and the machine was refused as a runaway.  `floor` +
        # the full 3 x 3 neighbourhood is the correct pair of choices: with a
        # cell of exactly the tolerance, two points within it differ by at most
        # one cell on each axis, in either direction.
        first: Dict[Tuple[int, int], int] = {}
        rep = np.arange(pts.shape[0])
        # ── which node SURVIVES a weld (2026-09-09) ─────────────────────────
        # Whichever is seen first, normally — the two are 20 µm apart and it
        # cannot matter.  On a cyclic SECTOR it matters completely: measured on
        # the G2-L40, the wedge's cut ray crosses the shaft bore 0.2 µm from an
        # existing vertex of the sampled bore circle, the weld merged the two,
        # and the survivor was the circle's — which is NOT on the ray, so cut
        # face A came out one node short of face B and the model was not
        # periodic at all.  Visiting the on-ray nodes first makes THEM the
        # representatives, which moves nothing (the other node is inside the
        # tolerance by definition) and keeps the two faces congruent.
        visit = np.arange(pts.shape[0])
        if periodic is not None:
            on_ray = np.zeros(pts.shape[0], dtype=bool)
            for _ang in periodic:
                _c, _s = math.cos(float(_ang)), math.sin(float(_ang))
                on_ray |= ((np.abs(pts[:, 0] * _s - pts[:, 1] * _c) < 1e-9)
                           & ((pts[:, 0] * _c + pts[:, 1] * _s) > 0))
            visit = np.concatenate([np.nonzero(on_ray)[0],
                                    np.nonzero(~on_ray)[0]])
        for i in visit:
            hit = None
            for dx in (0, -1, 1):
                for dy in (0, -1, 1):
                    hit = first.get((int(q[i, 0]) + dx, int(q[i, 1]) + dy))
                    if hit is not None and \
                            abs(pts[hit, 0] - pts[i, 0]) <= weld_tol_mm and \
                            abs(pts[hit, 1] - pts[i, 1]) <= weld_tol_mm:
                        break
                    hit = None
                if hit is not None:
                    break
            if hit is None:
                first[(int(q[i, 0]), int(q[i, 1]))] = i
            else:
                rep[i] = hit
        if (rep != np.arange(pts.shape[0])).any():
            t = rep[t]
            # Welding can collapse a sliver into a line — drop those.
            keep = (t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])
            t = t[keep]
            used = np.unique(t)
            remap = -np.ones(pts.shape[0], dtype=np.int64)
            remap[used] = np.arange(used.size)
            pts = pts[used]
            t = remap[t]

    # ── drop COLLINEAR triangles (2026-09-21) ───────────────────────────────
    # A triangle whose three nodes sit on one straight line has no interior: it
    # is a node that landed ON an edge of its neighbour, not a piece of the
    # machine.  Its element stiffness matrix is singular, and a handful of them
    # is enough to make the whole answer nonsense — on the owner's Ø50 straight
    # pocket eight of these (2e-17 mm², 0.00°) froze the contact solve: every
    # pair closed to 2e-16 µm, the rotor deformed 0.05 µm instead of 6, the bore
    # reacted 0.5 % of the applied torque and the machine was refused as a
    # runaway (`RotorRanAway`).  They come out of OCC's `fragment`, whose
    # boolean tolerance (10 µm) is coarser than the tip of the air wedge between
    # a magnet's corner fillet and the straight pocket wall beside it, so the
    # two edges there are neither merged nor cleanly separated; the 20 µm node
    # weld above catches most and this catches the rest.
    #
    # The threshold is RELATIVE to the mesh's own elements — a millionth of the
    # median area, which on this rotor is 2.4e-7 mm² against a smallest honest
    # element of 1.8e-4 — so nothing a mesher meant to build is ever dropped.
    # Removing one cannot open a hole (it encloses no area) but it can in
    # principle unlink two regions, which the connectivity check below would
    # then refuse loudly rather than solve.
    if t.shape[0]:
        _v0, _v1, _v2 = pts[t[:, 0]], pts[t[:, 1]], pts[t[:, 2]]
        _a = 0.5 * np.abs((_v1[:, 0] - _v0[:, 0]) * (_v2[:, 1] - _v0[:, 1])
                          - (_v2[:, 0] - _v0[:, 0]) * (_v1[:, 1] - _v0[:, 1]))
        _floor = 1e-6 * float(np.median(_a)) if _a.size else 0.0
        _degen = _a <= _floor
        if _degen.any():
            _log.warning("rotor mesh: dropped %d collinear element(s) "
                        "(area <= %.3g mm2, smallest %.3g mm2) — a node on a "
                        "neighbour's edge, not a piece of the machine",
                        int(_degen.sum()), _floor, float(_a.min()))
            t = t[~_degen]
            used = np.unique(t)
            remap = -np.ones(pts.shape[0], dtype=np.int64)
            remap[used] = np.arange(used.size)
            pts = pts[used]
            t = remap[t]

    # ── orient CCW ──────────────────────────────────────────────────────────
    v0, v1, v2 = pts[t[:, 0]], pts[t[:, 1]], pts[t[:, 2]]
    area2 = ((v1[:, 0] - v0[:, 0]) * (v2[:, 1] - v0[:, 1])
             - (v2[:, 0] - v0[:, 0]) * (v1[:, 1] - v0[:, 1]))
    flip = area2 < 0
    t[flip] = t[flip][:, [0, 2, 1]]

    # ── tag every triangle by its centroid ──────────────────────────────────
    # Magnets / sleeve / shaft are tested BEFORE the rotor core: they are the
    # small inclusions, the core is what is left over.
    from shapely.geometry import Point
    cen = (pts[t[:, 0]] + pts[t[:, 1]] + pts[t[:, 2]]) / 3.0
    part_tri = -np.ones(t.shape[0], dtype=np.int8)
    magnet_id = -np.ones(t.shape[0], dtype=np.int32)
    order_test = [p for p in parts if p[0] != PART_ROTOR] + \
                 [p for p in parts if p[0] == PART_ROTOR]
    todo = np.ones(t.shape[0], dtype=bool)
    for pid, mid, poly in order_test:
        if not todo.any():
            break
        pr = prep(poly)
        idx = np.nonzero(todo)[0]
        # Cheap bbox reject first — shapely .contains on 19k points is the cost.
        x0, y0, x1, y1 = poly.bounds
        m = ((cen[idx, 0] >= x0 - 1e-9) & (cen[idx, 0] <= x1 + 1e-9)
             & (cen[idx, 1] >= y0 - 1e-9) & (cen[idx, 1] <= y1 + 1e-9))
        idx = idx[m]
        hit = np.array([pr.contains(Point(cen[i, 0], cen[i, 1])) for i in idx],
                       dtype=bool) if idx.size else np.zeros(0, dtype=bool)
        sel = idx[hit]
        part_tri[sel] = pid
        if mid >= 0:
            magnet_id[sel] = mid
        todo[sel] = False

    if (part_tri < 0).any():
        # Fragment can leave a hair-thin sliver whose centroid falls just outside
        # every polygon.  Give it to the nearest part instead of dropping it —
        # a hole in the mesh would disconnect a magnet.
        from shapely.geometry import Point as _P
        for i in np.nonzero(part_tri < 0)[0]:
            p = _P(cen[i, 0], cen[i, 1])
            pid, mid, _ = min(order_test, key=lambda pr_: pr_[2].distance(p))
            part_tri[i] = pid
            if mid >= 0:
                magnet_id[i] = mid

    # ── the model must be ONE body ──────────────────────────────────────────
    # A floating island carries 3 rigid-body modes the null-space constraint
    # does not cover, and its displacements come back as nonsense while its
    # stresses still look plausible.  Say so loudly instead of returning that.
    import scipy.sparse as _sp
    from scipy.sparse.csgraph import connected_components as _cc
    _n = pts.shape[0]
    _r = np.concatenate([t[:, 0], t[:, 1], t[:, 2]])
    _c = np.concatenate([t[:, 1], t[:, 2], t[:, 0]])
    n_comp, lab = _cc(_sp.coo_matrix((np.ones(len(_r)), (_r, _c)), shape=(_n, _n)),
                      directed=False)
    if n_comp > 1:
        sizes = np.bincount(lab[t[:, 0]], minlength=n_comp)
        main = int(np.argmax(sizes))
        loose = sorted({PART_NAMES.get(int(p), "?")
                        for p in part_tri[lab[t[:, 0]] != main]})
        raise ValueError(
            f"the rotor solids mesh in {n_comp} disconnected pieces — "
            f"{', '.join(loose) or 'unknown parts'} float free of the main body, "
            "so there is no load path to react their centrifugal force. Check "
            "that the shaft OD meets the rotor bore and that every magnet "
            "touches iron or the sleeve (welding tolerance "
            f"{weld_tol_mm} mm).")

    outlines: List[List[List[float]]] = []
    for _pid, _mid, poly in parts:
        geoms = poly.geoms if isinstance(poly, MultiPolygon) else [poly]
        for g in geoms:
            outlines.append([[float(x), float(y)] for x, y in g.exterior.coords])
            outlines += [[[float(x), float(y)] for x, y in r.coords]
                         for r in g.interiors]

    sleeve_r = polys.get("sleeve_r_mm")
    r_sleeve_mean = (0.5 * (float(sleeve_r[0]) + float(sleeve_r[1])) * 1e-3
                     if sleeve_r else 0.0)
    r_rotor_od = float(sleeve_r[0]) * 1e-3 if sleeve_r else \
        float(np.max(np.hypot(pts[:, 0], pts[:, 1])) * 1e-3)

    mesh = MeshTri(np.ascontiguousarray(pts.T * 1e-3),   # mm -> m
                   np.ascontiguousarray(t.T.astype(np.int64)))
    return RotorMesh(mesh=mesh, part_tri=part_tri, magnet_id_tri=magnet_id,
                     outlines=outlines, r_sleeve_mean_m=r_sleeve_mean,
                     r_rotor_od_m=r_rotor_od)


# ---------------------------------------------------------------------------
# Interfaces
# ---------------------------------------------------------------------------

def _normal_traction(sigma: np.ndarray, elems: np.ndarray,
                     n: np.ndarray) -> np.ndarray:
    """sigma_nn = n . sigma . n  on the given elements (Pa)."""
    sxx, syy, sxy = sigma[elems, 0], sigma[elems, 1], sigma[elems, 2]
    nx, ny = n[:, 0], n[:, 1]
    return sxx * nx * nx + 2.0 * sxy * nx * ny + syy * ny * ny


# ---------------------------------------------------------------------------
# The ELECTROMAGNETIC TORQUE, as an air-gap shear
# ---------------------------------------------------------------------------
# Added 2026-09-07 for the user's request: "добавь ещё и момент на ротор, пусть
# действуют все силы; сделай меню, чтобы можно было выбрать центробежную, момент
# и обе."  The design behind it is a spoke rotor whose iron bridges are there for
# assembly only — they yield on the first spin-up — after which each pole is held
# TANGENTIALLY by nothing but friction against the sleeve and the magnets.  So
# the question is not "does the sleeve hold the poles down" (the centrifugal
# answer) but "can the machine's torque get out of the poles at all".
#
# HOW IT IS APPLIED
#   The Maxwell shear in the air gap acts on the rotor's outermost SOLID surface
#   — the iron pole tops and, where the magnets reach the gap, the magnet tops.
#   NOT on the sleeve: a carbon band carries no electromagnetic force, it only
#   passes on what it is rubbed by, and loading it directly would be inventing
#   the very load path the model is meant to test.
#
#   The traction is uniform and purely tangential,
#       tau = T / (2 pi r_o^2 L)                              [Pa]
#   which is exact for a full circular surface at r_o.  A real pole top is not a
#   full circle (notches, chamfers, inter-pole gaps), so the traction that is
#   actually applied is renormalised onto the surface that IS there,
#       tau_applied = (T / L) / integral(r ds),
#   and both numbers are reported.  Renormalising rather than scaling the
#   nominal value is deliberate: the applied torque is then EXACTLY T by
#   construction, so the bore reaction check tests the solver and not the
#   arithmetic of the surface selection.
#
# SIGN
#   MOTORING, with the rotor turning counter-clockwise (+z): the gap shear
#   drives the rotor in +theta and the shaft bore reacts -theta.  Braking is the
#   same solve with a negative T.

#: How far below the outermost radius a boundary facet may sit and still count
#: as "facing the air gap", as a fraction of that radius.  3 % of a 63 mm rotor
#: is ~1.9 mm: it takes the pole tops and the chamfers, and leaves out a deep
#: inter-pole notch — which sees the gap through much more air and carries a
#: correspondingly smaller share of the shear.  The COVERAGE it produces is
#: reported so the selection is never silent.
GAP_BAND = 0.03

#: The load menu the user asked for on 2026-09-07.
LOAD_MODES: Tuple[str, ...] = ("centrifugal", "torque", "both")

#: Semi-smooth Newton iterations one load case is allowed.  Named (it used to be
#: a literal in ``_run``) because the progress budget is built from it: the bar's
#: total is "cases x this", so the cap and the count MUST be the same number.
MAX_CONTACT_ITER = 30

#: How many equal increments the TORQUE traction is ramped in, on top of a rotor
#: already seated under its centrifugal and thermal load (2026-09-09).  See the
#: load-path section of ``contact``'s module docstring: Coulomb friction has no
#: single answer at a given load, only a history, and applying the torque in one
#: go on a self-locking wedge let the active set pick a locked state carrying
#: seven times the load (hot G2-L40: rotor 211 MPa p99.5 against 23 for the same
#: rotor spun hot with no torque at all).
#:
#: Six is measured rather than chosen.  The wedge's traction has to grow by less
#: than the friction cone can absorb in one step or the same indeterminacy comes
#: back; below four the hot G2 still limit-cycles, and above eight nothing moves
#: except the run time (every increment is a full active-set solve, and the ones
#: after the first cost 2-4 iterations because they start from the previous
#: increment's state).
TORQUE_LOAD_STEPS = 6

#: …and how many the SPIN is walked on in when the one-shot solve does not
#: settle (2026-09-10).  Same medicine, same reason — see the retry in
#: ``solve_rotor_stress._run``; a converging machine never pays for it.
SPIN_LOAD_STEPS = 6

#: What the progress bar BUDGETS per contact solve: the iteration count the
#: previous solve in this process actually needed (blended, floored at 3),
#: not the 30-iteration cap.  The cap multiplied out — 30 per case plus 30 per
#: possible lift-off solve — opened the bar on 212 steps for a rotor that
#: converged in 20 and quoted an ETA ten times too long (user 2026-09-07:
#: "зачем столько шагов?").  A solve that needs more than the estimate GROWS
#: the bar (StepLedger.grow); one that needs fewer hands the rest back.
_LEARNED_CONTACT_ITER = {"iters": 8}


def _boundary_facets(mesh, part_tri: np.ndarray, parts: Sequence[int]):
    """(facet ids, midpoints, outward unit normals, lengths) of the boundary
    facets whose owning element belongs to one of ``parts``.

    On the SPLIT mesh an interface edge is a boundary facet on BOTH sides (the
    nodes were duplicated), which is exactly what makes "the rotor's outer
    surface under the sleeve" addressable at all.
    """
    p = mesh.p                                       # (2, nv)
    bf = np.asarray(mesh.boundary_facets(), dtype=np.int64)
    if bf.size == 0:
        return (np.zeros(0, np.int64), np.zeros((0, 2)), np.zeros((0, 2)),
                np.zeros(0))
    owner = mesh.f2t[0, bf]
    sel = np.isin(part_tri[owner], np.asarray(parts, dtype=part_tri.dtype))
    bf, owner = bf[sel], owner[sel]
    if bf.size == 0:
        return (np.zeros(0, np.int64), np.zeros((0, 2)), np.zeros((0, 2)),
                np.zeros(0))
    a, b = mesh.facets[0, bf], mesh.facets[1, bf]
    pa, pb = p[:, a].T, p[:, b].T
    mid = 0.5 * (pa + pb)
    d = pb - pa
    ln = np.linalg.norm(d, axis=1)
    n = np.stack([d[:, 1], -d[:, 0]], axis=1) / np.maximum(ln, 1e-30)[:, None]
    t = mesh.t
    cen = (p[:, t[0, owner]] + p[:, t[1, owner]] + p[:, t[2, owner]]).T / 3.0
    flip = np.einsum("ij,ij->i", n, mid - cen) < 0
    n[flip] *= -1.0
    return bf, mid, n, ln


def gap_surface(mesh, part_tri: np.ndarray, band: float = GAP_BAND):
    """The rotor solids' facets that face the air gap.

    Boundary facets of ROTOR or MAGNET elements whose outward normal points
    outward-radially and whose midpoint sits within ``band`` of the outermost
    such radius.  The sleeve is excluded on purpose (see the section comment).
    Returns (facet ids, reference radius r_o [m], arc length [m]).
    """
    bf, mid, n, ln = _boundary_facets(mesh, part_tri, (PART_ROTOR, PART_MAGNET))
    if bf.size == 0:
        return np.zeros(0, np.int64), 0.0, 0.0
    r = np.hypot(mid[:, 0], mid[:, 1])
    er = mid / np.maximum(r, 1e-30)[:, None]
    outward = np.einsum("ij,ij->i", n, er) > 0.5
    if not outward.any():
        return np.zeros(0, np.int64), 0.0, 0.0
    r_o = float(r[outward].max())
    keep = outward & (r >= r_o * (1.0 - float(band)))
    return bf[keep], r_o, float(ln[keep].sum())


def torque_load(mesh, part_tri: np.ndarray, torque_nm: float,
                stack_length_m: float, order: int = 2,
                band: float = GAP_BAND) -> Tuple[np.ndarray, Dict[str, Any]]:
    """(load vector, report) for a uniform tangential traction of total ``T``.

    The load vector is in the module's usual units — force per metre of stack —
    so its moment about z is ``T / L``.  ``report`` carries the applied and the
    nominal traction, the surface it was spread over and its coverage of the
    full circle, because a uniform gap shear on a notched pole top is an
    assumption that has to be visible.
    """
    from skfem import FacetBasis, Functional, LinearForm, asm

    L = float(stack_length_m)
    if not np.isfinite(L) or L <= 0:
        raise ValueError(
            "a torque load needs the stack length: the air-gap shear is "
            "T / (2*pi*r^2*L) and L = 0 makes it infinite. Set "
            "geometry.motor_length (the route passes it automatically).")
    facets, r_o, arc = gap_surface(mesh, part_tri, band=band)
    if facets.size == 0 or r_o <= 0:
        raise ValueError(
            "no air-gap-facing surface was found on the rotor solids, so there "
            "is nowhere to apply the electromagnetic torque — check that the "
            "cross-section actually has rotor iron or magnets at its outer "
            "radius.")

    _b, elem = make_basis(mesh, order)
    fb = FacetBasis(mesh, elem, facets=facets, intorder=3 if order == 2 else 2)

    # integral(r ds) over the loaded surface: the lever the traction acts on
    @Functional
    def _arm(w):
        return np.sqrt(w.x[0] ** 2 + w.x[1] ** 2)

    S = float(_arm.assemble(fb))
    if S <= 0:
        raise ValueError("the air-gap surface integrated to zero length")

    tau = (float(torque_nm) / L) / S                  # Pa, EXACT by construction
    tau_nominal = float(torque_nm) / (2.0 * math.pi * r_o * r_o * L)

    @LinearForm
    def _traction(v, w):
        r = np.sqrt(w.x[0] ** 2 + w.x[1] ** 2)
        # e_theta = (-y, x) / r — motoring, rotor turning +z
        return tau * (-w.x[1] / r * v[0] + w.x[0] / r * v[1])

    f = asm(_traction, fb)
    circ = 2.0 * math.pi * r_o
    return f, {
        "torque_nm": float(torque_nm),
        "r_gap_mm": r_o * 1e3,
        "traction_mpa": tau * MPA,
        "traction_nominal_mpa": tau_nominal * MPA,
        "surface_mm": arc * 1e3,
        "coverage": float(arc / circ) if circ > 0 else None,
        "n_facets": int(facets.size),
        "direction": "motoring: +θ on the rotor solids, bore reacts −θ",
    }


def bore_hold(mesh, part_tri: np.ndarray, order: int = 2,
              band: float = 0.05) -> Optional["Any"]:
    """The shaft bore, held TANGENTIALLY (radial free) — the torque reaction.

    User 2026-09-07 asked for the reaction to be the bore, which is what a
    spline or a keyed hub does: it takes the torque and lets the bore breathe
    radially.  The rows go on the innermost boundary of the shaft tube; on a
    frameless machine with no shaft solid the rotor core's own bore is used.

    Returns ``None`` when the cross-section has no inner boundary at all — the
    caller turns that into a 422 rather than reacting the torque against
    nothing.
    """
    from motor_ai_sim.simulation.mechanical.contact import HeldBoundary

    for parts, label in (((PART_SHAFT,), "shaft bore"),
                         ((PART_ROTOR,), "rotor bore")):
        bf, mid, n, _ln = _boundary_facets(mesh, part_tri, parts)
        if bf.size == 0:
            continue
        r = np.hypot(mid[:, 0], mid[:, 1])
        er = mid / np.maximum(r, 1e-30)[:, None]
        inward = np.einsum("ij,ij->i", n, er) < -0.5
        if not inward.any():
            continue
        r_i = float(r[inward].min())
        if r_i <= 1e-6:
            continue
        keep = bf[inward & (r <= r_i * (1.0 + float(band)))]
        if keep.size == 0:
            continue

        from skfem import Basis
        _b, elem = make_basis(mesh, order)
        basis = Basis(mesh, elem, intorder=1)
        nodal = basis.nodal_dofs
        fdofs = basis.facet_dofs

        verts = np.unique(mesh.facets[:, keep].reshape(-1))
        pv = mesh.p[:, verts].T
        rows_dofs = [np.stack([nodal[0][verts], nodal[1][verts]], axis=1)]
        rows_pos = [pv]
        if fdofs.shape[0] >= 2:
            # P2: the facet dof is the value at the edge midpoint, so holding it
            # too keeps the whole quadratic trace on the bore tangentially fixed
            # — otherwise the edge bulges circumferentially between its ends.
            rows_dofs.append(np.stack([fdofs[0][keep], fdofs[1][keep]], axis=1))
            rows_pos.append(0.5 * (mesh.p[:, mesh.facets[0, keep]]
                                   + mesh.p[:, mesh.facets[1, keep]]).T)
        dofs = np.vstack(rows_dofs).astype(np.int64)
        pos = np.vstack(rows_pos)
        arm = np.hypot(pos[:, 0], pos[:, 1])
        dirs = np.stack([-pos[:, 1], pos[:, 0]], axis=1) \
            / np.maximum(arm, 1e-30)[:, None]
        return HeldBoundary(dofs=dofs, dirs=dirs, arm=arm,
                            vertices=verts.astype(np.int64), label=label)
    return None


# ---------------------------------------------------------------------------
# Public solve
# ---------------------------------------------------------------------------

def _principals(sig: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    sxx, syy, sxy = sig[:, 0], sig[:, 1], sig[:, 2]
    m = 0.5 * (sxx + syy)
    r = np.sqrt(np.maximum(0.25 * (sxx - syy) ** 2 + sxy ** 2, 0.0))
    return m + r, m - r


def _polar(sig: np.ndarray, phi: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """(sigma_rr, sigma_thetatheta) from the Cartesian Voigt stress."""
    c, s = np.cos(phi), np.sin(phi)
    sxx, syy, sxy = sig[:, 0], sig[:, 1], sig[:, 2]
    srr = sxx * c * c + 2.0 * sxy * c * s + syy * s * s
    stt = sxx * s * s - 2.0 * sxy * c * s + syy * c * c
    return srr, stt


MPA = 1e-6


# ---------------------------------------------------------------------------
# Safety factor, per element, by each part's OWN failure criterion
# ---------------------------------------------------------------------------
#: An element carrying no stress has an infinite safety factor.  Infinity is not
#: JSON and not a colour, so the map is clamped here — 1000 is far above any
#: threshold anyone sets and reads as "unstressed" without pretending to a
#: number.  Kept as a module constant so the frontend legend and the tests can
#: both name it.
SF_CLAMP = 1e3


def _sf_term(strength: float, stress: np.ndarray) -> np.ndarray:
    """strength / stress where the stress is damaging, +inf where it is not.

    A negative (or zero) stress in the direction a criterion checks does NOT
    consume any of that strength, so the term drops out instead of turning into
    a negative "safety factor" — which would sort below 1 and paint a compressed
    magnet red.
    """
    s = np.maximum(np.asarray(stress, dtype=float), 0.0)
    out = np.full(s.shape, np.inf)
    nz = s > 0.0
    out[nz] = float(strength) / s[nz]
    return out


def element_safety_factor(pm: PartMech, part: str, vm: np.ndarray,
                          p1: np.ndarray, p2: np.ndarray,
                          srr: np.ndarray, stt: np.ndarray) -> np.ndarray:
    """Per-element safety factor of ONE part, in ITS OWN failure mode.

    The criteria are not interchangeable, which is the whole reason this lives
    on the backend rather than in the map component:

    * **rotor core, shaft** (and any metallic sleeve) — ductile, so
      ``yield / von Mises``.
    * **magnets** — sintered NdFeB does not yield, it cracks, and it is ~12x
      weaker in tension than in compression.  Both sides are checked and the
      worse one wins: ``min(tensile / max principal, compressive / |min
      principal|)``.  A magnet sitting in pure compression is therefore judged
      on its compressive strength, not written off as infinitely safe.
    * **a fibre sleeve** — the burst mode is the hoop-wound fibres letting go,
      so ``fibre tensile / sigma_theta``.  If the card carries a TRANSVERSE
      strength the matrix-dominated radial term is taken too; no card in the
      library does yet, and the term is skipped rather than guessed.

    Returns an array clamped to ``SF_CLAMP`` (never inf, never negative).
    """
    vm = np.asarray(vm, dtype=float)
    sf = np.full(vm.shape, np.inf)
    if part == "magnet":
        sf = np.minimum(sf, _sf_term(pm.strength, p1))
        if pm.compressive_strength:
            sf = np.minimum(sf, _sf_term(pm.compressive_strength,
                                         -np.asarray(p2, dtype=float)))
    elif part == "sleeve" and pm.strength_kind == "tensile":
        sf = np.minimum(sf, _sf_term(pm.strength, stt))
        if pm.strength_transverse:
            sf = np.minimum(sf, _sf_term(pm.strength_transverse,
                                         np.abs(np.asarray(srr, dtype=float))))
    else:
        sf = np.minimum(sf, _sf_term(pm.strength, vm))
    return np.minimum(sf, SF_CLAMP)


def sf_criterion_text(pm: PartMech, part: str) -> str:
    """One line naming the criterion, for the map's tooltip."""
    if part == "magnet":
        t = f"tensile {pm.strength * MPA:.0f} MPa / max principal tension"
        if pm.compressive_strength:
            t += (f", compressive {pm.compressive_strength * MPA:.0f} MPa "
                  "/ |min principal|")
        return t
    if part == "sleeve" and pm.strength_kind == "tensile":
        t = f"fibre tensile {pm.strength * MPA:.0f} MPa / hoop σθ"
        if pm.strength_transverse:
            t += (f", transverse {pm.strength_transverse * MPA:.0f} MPa "
                  "/ |σr|")
        return t
    return f"{pm.strength_kind} {pm.strength * MPA:.0f} MPa / von Mises"


#: How a landing surface reads in the retention verdict.  The pair label is the
#: honest identifier and rides in ``contact.seated``; the sentence on the panel
#: says what the surface IS (2026-09-09).
_SEAT_SURFACE = {"magnet_rotor": "the pocket tab",
                 "sleeve_magnet": "the sleeve",
                 "sleeve_rotor": "the sleeve",
                 "shaft_rotor": "the shaft"}


def _retention(cs, sol, forces: Dict[str, Any], present: Dict[str, np.ndarray],
               area: np.ndarray, rho_elem: np.ndarray, cen: np.ndarray,
               omega: float, seated: Optional[List[Dict[str, Any]]] = None
               ) -> Dict[str, Any]:
    """WHICH surface is holding the magnets down, and with how much of the load.

    Answers the question the user asked the module to answer: with the pockets
    modelled as separation contacts, is it the sleeve, the pocket side walls or
    the iron above the magnet that carries the 7.65 kg of spoke magnets?

    Every number is the INWARD radial reaction on the magnets, in kN per metre
    of stack, against their own centrifugal load.  The split of the magnet/iron
    interface into "radial faces" and "side walls" is by facet normal: a facet
    whose normal is within ~45 deg of radial can hold the magnet down directly
    (the pocket bottom, the shoulder under the outer bridge), one whose normal
    is circumferential can only do it by wedging.

    ``seated`` (2026-09-09) is the contact solve's seating record.  A magnet that
    came loose and TRAVELLED onto its lip is retained by that lip, and the
    verdict has to say so with the distance: "магнит должен сесть на язычок, как
    в Fusion" — the number the user compares with Fusion's 0.078 mm is the
    travel, and burying it in a nested contact block would leave the headline
    reading like an ordinary clamp.
    """
    mm = present.get("magnet")
    if mm is None or not mm.any():
        return {"magnet_centrifugal_kn_per_m": 0.0, "carried_kn_per_m": {},
                "share": {}, "verdict": "no magnets"}
    seat_mag = [s for s in (seated or []) if s.get("part") == "magnet"]

    def _with_seating(text: str) -> str:
        if not seat_mag:
            return text
        # The RELATIVE travel, not the absolute one (2026-09-09).  The absolute
        # is measured from the cold mesh and on a hot rotor is mostly the magnet
        # riding outward with the pocket that surrounds it — 105 µm on the
        # G2-L40, of which ~90 is the iron's own growth and no motion at all in
        # the machine.  What closed is the CTE mismatch, and that is the number
        # a sentence gets; the absolute one keeps its place in
        # ``contact.seated`` (and in the panel's tooltip) because it is the one
        # the stress map shows.
        tr = max(float(s["travel_rel_m"]) for s in seat_mag) * 1e6
        where = _SEAT_SURFACE.get(seat_mag[0].get("landed_on"),
                                  seat_mag[0].get("landed_on") or "a face")
        what = ("the magnet" if len(seat_mag) == 1
                else f"{len(seat_mag)} magnets")
        # A tenth under ten microns: the cold G2 closes 0.26 µm of clearance and
        # "travelled 0 µm" reads as a bug rather than as a tight pocket.
        num = f"{tr:,.1f}" if tr < 10.0 else f"{tr:,.0f}"
        return (f"{text} — {what} travelled {num} µm in the pocket and "
                f"seated on {where}")
    fc = float((area[mm] * rho_elem[mm] * (omega ** 2)
                * np.hypot(cen[mm, 0], cen[mm, 1])).sum())
    mr = forces.get("magnet_rotor", {})
    sm = forces.get("sleeve_magnet", {})
    carried = {
        # force ON the magnet (side A) is reported; inward (negative) is what
        # retains it, so flip the sign to get a positive "retention".
        "pocket_radial_faces": -float(mr.get("radial_on_radial_faces_n_per_m", 0.0)),
        "pocket_side_walls": -float(mr.get("radial_on_side_faces_n_per_m", 0.0)),
        # sleeve_magnet has the SLEEVE as side A, so the force on the magnet is
        # the negative of the reported one and the retention is +fr.
        "sleeve": float(sm.get("radial_n_per_m", 0.0)),
    }
    # At standstill there is no centrifugal load to carry, so there is no
    # retention question and a "share of zero" is a division by nothing.
    if fc <= 1.0:
        return {"magnet_centrifugal_kn_per_m": 0.0,
                "carried_kn_per_m": {k: v / 1e3 for k, v in carried.items()},
                "share": {k: None for k in carried},
                "verdict": _with_seating("not loaded (standstill)"),
                "seated": list(seat_mag)}
    share = {k: v / fc for k, v in carried.items()}
    best = max(carried, key=lambda kk: carried[kk])
    label = {"pocket_radial_faces": "pocket bottom / shoulder (iron)",
             "pocket_side_walls": "pocket side walls (wedge)",
             "sleeve": "the sleeve"}[best]
    if carried[best] <= 0.05 * fc:
        label = "nothing — the magnets are not retained"
    return {
        "magnet_centrifugal_kn_per_m": fc / 1e3,
        "carried_kn_per_m": {k: v / 1e3 for k, v in carried.items()},
        "share": share,
        "verdict": _with_seating(label),
        "seated": list(seat_mag),
    }


def _scale_retention(rep: Dict[str, Any], n_sym: int) -> Dict[str, Any]:
    """A sector's retention report, read as the MACHINE's (2026-09-09).

    ``_retention`` weighs the magnets' centrifugal load against the reactions
    that hold it, both integrated over whatever was solved.  On a one-pole
    sector those are one magnet's, so the SHARES — which is what the verdict is
    about — are already the machine's, and only the two absolute kN/m columns
    have to be multiplied by ``n``.  Doing it here rather than inside
    ``_retention`` keeps that function about the physics and this one about the
    bookkeeping.
    """
    if n_sym <= 1 or not rep:
        return rep
    out = dict(rep)
    out["magnet_centrifugal_kn_per_m"] = \
        float(rep.get("magnet_centrifugal_kn_per_m", 0.0)) * n_sym
    out["carried_kn_per_m"] = {k: float(v) * n_sym
                               for k, v in (rep.get("carried_kn_per_m") or {}).items()}
    out["n_sectors"] = int(n_sym)
    return out


#: How far the bore reaction may differ from the applied torque before the
#: torque path is declared unsolved.  It is a CONSTRAINT IDENTITY — what goes in
#: at the gap comes out at the bore — so on a healthy solve it holds to ~1e-9
#: (the analytic ring test pins it at 1e-6).  1 % is therefore not a tolerance
#: on the physics but a tripwire on the algebra: the only way to miss it is a
#: solve whose displacements have run away, which is what a rotor with a piece
#: held by nothing does.  Measured on the sandbox machine with mu = 0: a magnet
#: retained by a 1e-6 tangential spring moved 8.6 km, and the reaction came back
#: at 203 N·m against 40 applied.
TORQUE_BALANCE_TOL = 0.01

#: …and how far a solve that never settled may still be from the complementarity
#: conditions and be GRADED, as a fraction of the rotor's outer radius
#: (2026-09-09).  7 µm on the G2's Ø146.7, against the 20-100 µm of clearance its
#: hot pocket is working across.
#:
#: The old rule was "no verdict unless the active set repeated", and on the hot
#: G2 that threw away an answer whose residual was 1.7 µm — eleven pairs of two
#: thousand overlapping by under a micron with no force between them — while the
#: stress map beside it was published regardless.  What makes the trade an
#: honest one is the direction of the error: a separation pair's reported
#: pressure is CLAMPED at zero (``solve_contact``), so a pair the set has
#: mis-graded contributes nothing to ``mu * integral(p r dA)``.  An unsettled set
#: therefore understates the clamp rather than inflating it, and the verdict it
#: supports is a floor.  The residual is quoted in the verdict text either way —
#: the reader is never asked to take "held: yes" on trust.
TORQUE_PATH_RESIDUAL_FRAC = 1e-4


def _torque_path(ifaces: Dict[str, Any], torque_nm: float, sol,
                 balance: Optional[float] = None,
                 r_out_m: float = 0.0) -> Dict[str, Any]:
    """One line saying whether the poles are held, and by how much.

    User 2026-09-07: the spoke rotor's iron bridges are assembly features that
    yield on the first spin-up, so the poles are held tangentially by friction
    alone.  The verdict is therefore about SLIDING, not about opening:

      * the worst separation joint's ``slip_fraction`` is the headline — half a
        joint sliding is a joint that has stopped transmitting;
      * the Coulomb capacity ``mu * integral(p r dA)`` summed over the joints
        that are actually clamped is what the applied torque is compared with.

    Both are reported next to the verdict so nobody has to take the word for it.

    Two earlier versions of this line were wrong and are worth naming, because
    both read as reassurance:

      * "poles held: yes (slip 16 %)" on a joint that was 83 % OPEN.  An open
        facet transmits nothing either, so a slip fraction alone says nothing.
      * a verdict at all on a solve that had not converged and had two parts
        floating free.  That is the model saying the rotor came apart, not a
        result to grade — it now returns "no verdict" and says why.

    What is graded instead is the CAPACITY of the weakest clamped separation
    joint, ``mu * integral(p r dA)``, against the applied torque.  The weakest
    one is the right one: the torque has to cross every joint between the pole
    top and the hub, so the path is only as good as its worst cut.  Joints with
    no contact pressure at all are excluded — a joint that is fully open is not
    on the path, it is simply not there.
    """
    uni = {lb: v for lb, v in ifaces.items()
           if v and v["type"] == "separation" and v["n_facets"]}
    slip_um = max((v["slip_max_um"] for v in uni.values()), default=0.0)
    base = {"worst_pair": None, "slip_fraction": 0.0, "stick_fraction": 1.0,
            "capacity_nm": None, "applied_nm": float(torque_nm),
            "margin": None, "slip_max_um": slip_um, "held": None}
    if not uni:
        return {**base, "verdict": "no separation joint on the torque path"}
    off_balance = (balance is not None
                   and abs(balance - 1.0) > TORQUE_BALANCE_TOL)
    # "Settled" is no longer "the active set repeated" but "the answer being
    # reported is close enough to satisfying the complementarity conditions to
    # be graded" — see ``TORQUE_PATH_RESIDUAL_FRAC`` for why, and for why the
    # error can only go the safe way.  The other three refusals are untouched:
    # a part floating free, a rotor that solved as two bodies and a bore
    # reaction that does not match the applied torque are not questions of
    # tolerance.
    resid = float(getattr(sol, "residual", 0.0) or 0.0)
    settled = bool(sol.converged) or (
        r_out_m > 0.0 and resid <= TORQUE_PATH_RESIDUAL_FRAC * r_out_m)
    if not settled or sol.free_parts or sol.n_components > 1 or off_balance:
        # n_components > 1 is the sharpest of the three and the least obvious.
        # With the bore held, everything ON the torque path is one body; a
        # second body is a piece that has come loose, and its rigid-rotation
        # border then absorbs an arbitrary moment — which is exactly why
        # `torque_reaction_nm` stops matching the applied torque in that state.
        # (`free_parts` does not catch it: it asks whether the PART has any
        # element left on the main body, so one loose magnet out of ten is
        # invisible to it.)
        if not settled:
            why = (f"the contact did not settle — {resid * 1e6:,.2f} µm out"
                   if resid > 0 else "the contact did not settle")
        elif sol.free_parts:
            why = f"{', '.join(sol.free_parts)} float free"
        elif sol.n_components > 1:
            why = (f"the rotor solved as {sol.n_components} separate bodies — "
                   "something has come loose")
        else:
            why = (f"the bore reacted {balance * abs(torque_nm):,.0f} N·m "
                   f"against {abs(torque_nm):,.0f} applied — a piece of the "
                   "rotor is held by nothing and ran away")
        return {**base, "verdict": f"no verdict — {why}; the torque path is "
                                   "not solved"}
    clamped = {lb: v for lb, v in uni.items() if v["friction_capacity_nm"] > 0}
    if not clamped:
        # No FRICTION path.  Two different situations, told apart by µ
        # (2026-09-09, user: "можно же считать с нулевым трением?"):
        #   * µ = 0 on every joint that carries pressure — a spoke magnet's
        #     pocket walls are form-locked, the torque crosses them as NORMAL
        #     pressure, and there is simply no slip question to grade.  The
        #     solve above settled and balanced, so the poles ARE held; what is
        #     missing is a friction margin, and the line says that instead of
        #     "no".
        #   * µ > 0 but nothing is pressed — every joint is open, and "no" is
        #     the honest word.
        pressed = {lb: v for lb, v in uni.items()
                   if float(v.get("open_fraction", 1.0) or 0.0) < 1.0}
        frictionless = bool(pressed) and all(
            float(v.get("mu", 0.0) or 0.0) == 0.0 for v in pressed.values())
        if frictionless:
            worst = max(pressed, key=lambda k_: pressed[k_]["slip_fraction"])
            verdict = ("poles held by the pocket walls — µ = 0, so the torque "
                       "crosses the joints as normal pressure (form-locked) "
                       "and there is no friction margin to grade; slip "
                       f"{pressed[worst]['slip_fraction'] * 100:.0f} % on {worst}")
            if slip_um > 0:
                verdict += f", max {slip_um:,.0f} µm"
            if not sol.converged:
                # graded on a residual, not on a settled set — say so here as
                # the friction branch does below
                verdict += f" (contact residual {resid * 1e6:,.2f} µm)"
            return {**base, "held": None, "worst_pair": worst,
                    "slip_fraction": float(pressed[worst]["slip_fraction"]),
                    "stick_fraction": float(pressed[worst]["stick_fraction"]),
                    "verdict": verdict}
        return {**base, "held": False,
                "verdict": "poles held: no — every separation joint is open, "
                           "nothing is clamped"}
    worst = min(clamped, key=lambda k_: clamped[k_]["friction_capacity_nm"])
    cap = float(clamped[worst]["friction_capacity_nm"])
    margin = (cap / abs(torque_nm)) if torque_nm else None
    held = bool(margin is not None and margin >= 1.0)
    verdict = (f"poles held: {'yes' if held else 'no'} — weakest joint "
               f"{worst} passes {cap:,.0f} of {abs(torque_nm):,.0f} N·m"
               f" ({margin:.1f}×)" if margin is not None else "")
    verdict += f", slip {clamped[worst]['slip_fraction'] * 100:.0f} %"
    if slip_um > 0:
        verdict += f", max {slip_um:,.0f} µm"
    if not sol.converged:
        # Graded on an answer that never settled: say so, with the size of the
        # disagreement, right in the sentence (2026-09-09).
        verdict += f" (contact residual {resid * 1e6:,.2f} µm)"
    return {"verdict": verdict, "worst_pair": worst,
            "slip_fraction": float(clamped[worst]["slip_fraction"]),
            "stick_fraction": float(clamped[worst]["stick_fraction"]),
            "capacity_nm": cap, "applied_nm": float(torque_nm),
            "margin": margin, "slip_max_um": slip_um, "held": held}


def solve_rotor_stress(polys: dict,
                       assignments: Optional[dict],
                       rpm: float,
                       overspeed_factor: float = 1.2,
                       interference_mm: float = 0.0,
                       stack_length_mm: float = 0.0,
                       material_overrides: Optional[dict] = None,
                       mesh_size_mm: float = 1.5,
                       order: int = 2,
                       with_field: bool = True,
                       contacts: Optional[Dict[str, Any]] = None,
                       lift_off_solves: int = 6,
                       case_mode: str = "three",
                       loads: str = "centrifugal",
                       torque_nm: float = 0.0,
                       rotor_temp_c: float = REF_TEMP_C,
                       sleeve_temp_c: float = REF_TEMP_C,
                       ref_temp_c: float = REF_TEMP_C,
                       part_temps_c: Optional[Dict[str, float]] = None,
                       thermal_model: str = "band_fit",
                       symmetry: str = "full",
                       num_poles: Optional[int] = None,
                       progress=None) -> Dict[str, Any]:
    """Full rotor structural report at standstill / rated / overspeed.

    v2 (2026-09-05): the parts are joined by CONTACT, not by shared nodes —
    Fusion's *Separation* by default on magnet/iron, sleeve/iron and
    sleeve/magnet, *Bonded* on shaft/iron (a press-fit hub).  Because a
    unilateral contact is nonlinear in the load, superposition is gone: each
    case is solved on its own, warm-started from the previous one, and the
    lift-off speed is a short bisection instead of the v1 closed form.
    ``contacts`` is a ``{label: ContactSpec}`` map; ``None`` means the defaults.

    ``case_mode`` (2026-09-06) picks how many of them are actually solved.  User:
    "давай будем рассчитывать только на 23 000 оборотов — всё, что ниже, всяко
    выдержит, и проще будет считать только одну величину".  ``"single"`` solves
    ONE case, at ``rpm``, named by its speed ("23,000 rpm"); ``"three"`` is the
    original standstill / rated / overspeed and stays the default so every
    existing caller keeps its answer.  The response shape does not change — the
    ``cases`` dict simply has one entry — so the maps, the safety factors and the
    persisted last result read a single-speed answer without knowing about it.

    ``loads`` (2026-09-07) picks WHICH forces act.  User: "добавь ещё и момент на
    ротор, пусть действуют все силы; сделай меню, чтобы можно было выбрать
    центробежную, момент и обе."

      * ``centrifugal`` — rho*omega^2*r only, the model as it was.  The rotor
        floats: only the rigid-body modes are removed, no node is held.
      * ``torque``      — the electromagnetic torque only, applied as a uniform
        tangential traction on the rotor solids' air-gap surface (see
        ``torque_load``) and reacted at the shaft bore, which is held
        TANGENTIALLY (radial free).
      * ``both``        — the two together, which is the machine.

    Holding the bore is what makes the torque cases well posed, so it is applied
    for ``torque`` and ``both`` and NOT for ``centrifugal``.  On a symmetric
    rotor the centrifugal displacement is purely radial, so the hold is inactive
    and ``both`` is still the exact superposition of the two — which is what
    tests/test_mechanical_torque.py asserts on a bonded model.

    The torque is the machine's, not the speed's: the SAME ``torque_nm`` is
    applied in every case, including standstill.  Stalling a motor at zero rpm
    is a real duty point, and it is the WORST one for the friction path — there
    is no centrifugal clamp yet to press the poles into the sleeve.

    ``rotor_temp_c`` / ``sleeve_temp_c`` (2026-09-07) are the ROTOR TEMPERATURE.
    User: "нужно универсально добавить температуру ротора, чтобы можно было
    задавать; для моторов без бандажа этот эффект вообще минимальный".  The
    first covers the rotor core, the magnets and the shaft, the second the
    sleeve — they are given separately because on a real machine they are not
    the same number: the iron carries the loss, the carbon band is on the
    outside in the gap draught.  Both default to ``ref_temp_c`` = 20 °C, which
    is the state the geometry as drawn is stress-free in, so a request that
    says nothing about temperature solves exactly the machine it always did.

    HOW THE TEMPERATURES ACT — ``thermal_model`` (2026-09-09).  User: "нам нужно
    учитывать температуру только как изменение давления на бандаж, если он
    есть".  Under the default ``"band_fit"`` the rotor's temperature is NOT a
    load on its parts: the per-part free strains (``thermal_eigenstrain``) are
    used for one thing only — how much what sits under the band would grow on
    its own against how much the band's own bore grows — and that difference is
    carried into the solve as the band's interference AT TEMPERATURE
    (``interference_effective_mm``), with every part otherwise solved as drawn
    at ``ref_temp_c``.  A machine with no band is therefore solved cold whatever
    temperatures it is given, and the answer says so in ``thermal.notes``.  The
    reason is the built machine: the magnets sit in an epoxy bed with a 0.04 mm
    pocket clearance and an all-iron rotor expands freely, so the magnet/iron
    CTE mismatch that a per-part eigenstrain manufactures (magnets loose by
    microns re-seating in a self-locking wedge, hundreds of MPa in a bonded
    joint) is nothing the real rotor sees; the band — iron at 12 ppm/K under
    carbon at ~0 — is where the temperature IS the load.  ``"free_expansion"``
    keeps the 2026-09-07 per-part eigenstrain in every part; it is the solver's
    own verification path (the Lamé and seating suites drive it) and no route
    offers it.

    ``part_temps_c`` (2026-09-08) gives EACH solid its own temperature, keyed by
    ``PART_TEMP_KEY`` — ``rotor_core``, ``magnet``, ``shaft``, ``sleeve``, the
    same vocabulary the material assignment uses.  User: "в механический расчёт
    тоже нужно делать каплинг, чтобы температуры везде были одинаковы" — the
    Thermal solve already produces a temperature per part, and typing two of
    them back in by hand is how the two solvers drift apart.  A key that is
    absent keeps the OLD rule exactly (``rotor_temp_c`` for core / magnet /
    shaft, ``sleeve_temp_c`` for the band), so a request that does not use it
    solves bit for bit the machine it always did.

    It is worth having because the parts are not the same number: on a real
    machine the magnets run hotter than the iron they sit in, and the magnet is
    the part whose CTE disagrees with everything around it (+5 ppm/K along the
    magnetisation, −1.5 across, against steel's 12).  A magnet held in an iron
    pocket that is COOLER than it is presses harder into that pocket — the
    magnet_rotor contact closes — and reading both parts off one number cannot
    show that at all.

    ``symmetry`` (2026-09-09) picks the MODEL, not a view of it.  User:
    *"нагрузка на все зубы должна быть одинакова … так используй периодичность,
    как я во Fusion"*.

      * ``"full"``   — the whole 360° cross-section, exactly as before.  Every
        number, every cache key and every array is what it was; this is the
        default and it is meant to stay one.
      * ``"sector"`` — ONE periodic sector of ``n`` (``symmetry.n_sectors`` in
        the answer, read off the magnet set), with the two cut faces tied by
        ``u_B = R(2*pi/n) u_A``.  Every pole then carries an identical load by
        construction, and the stiffness matrix is ``n`` times smaller — the
        G2-L40 is 28 sectors.

    WHAT A SECTOR ANSWER MEANS.  The solve is a sector solve, so every EXTENSIVE
    quantity that comes out of it is ``1/n`` of the machine's: the centrifugal
    body force, the air-gap traction, the interface line integrals, the masses.
    They are scaled back up by ``n`` before they are reported, so a sector answer
    and a full answer are read off the same axes and ``torque_path`` grades the
    same capacity against the same applied torque.  The INTENSIVE ones —
    stresses, safety factors, the OD growth, open/slip fractions, contact
    pressures — are the same number in a sector and in the full rotor and are
    passed through untouched.  ``symmetry.scaled`` in the answer lists which
    fields were multiplied.  The ``field`` payload carries the sector's own mesh
    under ``field["sector"]`` AND the full rotor replicated ``n`` times
    (``field["replicated_from_sector"] = True``) so the existing map draws the
    whole cross-section.

    ``num_poles`` is only a FALLBACK for the periodicity, used when the polygons
    carry no magnets to read it from; whenever there are magnets they decide,
    because they are the features that are actually drawn.

    ``progress`` (2026-09-07) is the shared live-progress callback (see
    ``motor_ai_sim.progress``): ``progress(done, total, phase, composition)``.
    This solve is the slowest thing the Mechanical tab does — a gmsh build plus
    one nonlinear contact solve per case plus up to ``lift_off_solves`` more for
    the bisection — and it used to report NOTHING until it returned.  The total
    is counted from the request, not from the clock: mesh + assembly + (cases +
    lift-off budget) x the contact iteration cap, revised DOWN as each case
    converges early (which most do, in single figures out of thirty), so the bar
    reaches its end exactly when the work does.
    """
    from motor_ai_sim.progress import StepLedger
    from motor_ai_sim.simulation.mechanical import contact as ctc
    loads = str(loads or "centrifugal").strip().lower()
    if loads not in LOAD_MODES:
        raise ValueError("loads must be one of "
                         f"{', '.join(LOAD_MODES)}, got {loads!r}")
    torque_nm = float(torque_nm or 0.0)
    if not np.isfinite(torque_nm):
        raise ValueError(f"torque_nm must be a finite number, got {torque_nm!r}")
    use_centrifugal = loads in ("centrifugal", "both")
    use_torque = loads in ("torque", "both")
    if use_torque and torque_nm == 0.0:
        raise ValueError(
            f"loads={loads!r} was asked for but torque_nm is 0 — pass the "
            "electromagnetic torque, or select loads='centrifugal'")
    rpm = float(rpm)
    if not np.isfinite(rpm) or rpm < 0:
        raise ValueError(f"rpm must be a finite non-negative number, got {rpm!r}")
    case_mode = str(case_mode or "three").strip().lower()
    if case_mode not in ("three", "single"):
        raise ValueError("case_mode must be 'three' (standstill / rated / "
                         f"overspeed) or 'single' (one speed), got {case_mode!r}")
    if case_mode == "single":
        # Nothing is solved above `rpm` in this mode, so nothing may QUOTE a
        # speed above it: an overspeed factor left in the report would put an
        # unsolved rpm on the panel's lift-off comparison.
        overspeed_factor = 1.0
    overspeed_factor = float(overspeed_factor)
    if not np.isfinite(overspeed_factor) or overspeed_factor < 1.0:
        raise ValueError("overspeed_factor must be >= 1.0, got "
                         f"{overspeed_factor!r}")
    interference_mm = float(interference_mm or 0.0)
    if interference_mm < 0:
        raise ValueError("interference_mm must be >= 0 (a radial oversize)")

    # ── the temperatures (2026-09-07) ───────────────────────────────────────
    # Named and bounded rather than clamped: the project's client-facing rule is
    # that an impossible input is refused with the field named, never quietly
    # solved as something else.
    def _temp(v, field: str) -> float:
        f = float(v if v is not None else ref_temp_c)
        if not np.isfinite(f):
            raise ValueError(f"{field} must be a finite temperature in °C, "
                             f"got {v!r}")
        if f < -273.15 or f > 1000.0:
            raise ValueError(f"{field} = {f} °C is outside -273.15 … 1000 °C — "
                             "this is a temperature in degrees CELSIUS")
        return f

    ref_temp_c = _temp(ref_temp_c, "ref_temp_c")
    rotor_temp_c = _temp(rotor_temp_c, "rotor_temp_c")
    sleeve_temp_c = _temp(sleeve_temp_c, "sleeve_temp_c")
    # ONE temperature per solid (2026-09-08).  The two scalars above are the
    # FALLBACK, not the rule: whatever `part_temps_c` names wins, and whatever
    # it does not name keeps exactly the pairing this solver has always used —
    # so a caller that passes nothing gets the same floats through the same
    # subtractions, and the coupled caller gets the Thermal tab's own per-part
    # answer without a second code path.
    _fallback_temp = {"rotor": rotor_temp_c, "magnet": rotor_temp_c,
                      "shaft": rotor_temp_c, "sleeve": sleeve_temp_c}
    given_temps = dict(part_temps_c or {})
    unknown = [k for k in given_temps if k not in PART_TEMP_KEY]
    if unknown:
        # Named, never ignored: a typo'd part key silently dropped would solve a
        # cold magnet and report a hot one (the client-facing validation rule).
        raise ValueError(
            f"part_temps_c names unknown part(s) {', '.join(sorted(unknown))} — "
            f"the keys are {', '.join(sorted(PART_TEMP_KEY))}")
    part_temp_c: Dict[str, float] = {}
    for _key, _name in PART_TEMP_KEY.items():
        _v = given_temps.get(_key)
        part_temp_c[_name] = (_fallback_temp[_name] if _v is None
                              else _temp(_v, f"part_temps_c.{_key}"))
    dT_part = {name: t - ref_temp_c for name, t in part_temp_c.items()}

    # ── CYCLIC SYMMETRY: which cross-section is actually solved (2026-09-09) ─
    # Done before anything reads `polys`, because on a sector everything below
    # — the mesh, the materials map, the sleeve fit, the loads — is about the
    # WEDGE and not about the machine.  `polys` itself is left alone: the
    # replication at the end needs the full outlines.
    symmetry = str(symmetry or "full").strip().lower()
    if symmetry not in ("full", "sector"):
        raise ValueError("symmetry must be 'full' (the whole 360° rotor) or "
                         f"'sector' (one periodic sector), got {symmetry!r}")
    sector_plan = None
    n_sym = 1
    polys_solved = polys
    periodic = None
    if symmetry == "sector":
        from motor_ai_sim.simulation.mechanical import symmetry as sym
        sector_plan = sym.plan_sector(polys, num_poles=num_poles)
        n_sym = int(sector_plan.n_sectors)
        polys_solved = sym.sector_polys(polys, sector_plan)
        periodic = (sector_plan.cut_angle_rad, sector_plan.cut_angle_b_rad)
    # The FULL section as drawn, kept before the sector cut replaces
    # `polys`: the stator is not part of the rotor solve, but its bore is
    # what the air-gap clearance is measured against.
    polys_all = dict(polys)
    polys = polys_solved

    has_sleeve = polys.get("sleeve") is not None
    if interference_mm > 0 and not has_sleeve:
        raise ValueError("interference_mm > 0 but this machine has no sleeve "
                         "(sleeve_thickness = 0)")

    mech = resolve_part_materials(assignments, has_sleeve, material_overrides)

    # ── the step budget, built from the REQUEST ─────────────────────────────
    # Two fixed steps (mesh, assembly) plus one contact iteration cap per solve,
    # and every solve is either a load case or one of the lift-off bisection's
    # budgeted extras.  Counting it this way means the number on screen is the
    # work that was ASKED for; `give_back` below corrects it as the iteration
    # actually converges.
    _n_cases = 1 if case_mode == "single" else 3
    _lift_budget = max(int(lift_off_solves), 0)
    _expect = int(min(MAX_CONTACT_ITER, max(3, _LEARNED_CONTACT_ITER["iters"])))
    led = StepLedger(
        progress,
        total=2 + _n_cases * _expect,
        composition=(
            f"mesh + assembly + {_n_cases} case"
            f"{'s' if _n_cases > 1 else ''} x ~{_expect} contact iterations "
            f"(cap {MAX_CONTACT_ITER})"
            + (f"; up to {_lift_budget} lift-off solves added only if a joint "
               "opens" if _lift_budget else "")))

    rm = build_rotor_mesh(polys, mesh_size_mm=mesh_size_mm,
                          progress=lambda d, t, ph=None, c=None: led.at(d, ph),
                          periodic=periodic)
    mesh, part_tri = rm.mesh, rm.part_tri
    ne = mesh.t.shape[1]

    p = mesh.p.T                                    # (n, 2) metres
    cen = (p[mesh.t[0]] + p[mesh.t[1]] + p[mesh.t[2]]) / 3.0
    phi = np.arctan2(cen[:, 1], cen[:, 0])

    C_elem = np.zeros((ne, 3, 3))
    rho_elem = np.zeros(ne)
    present: Dict[str, np.ndarray] = {}
    for name, pid in (("rotor", PART_ROTOR), ("magnet", PART_MAGNET),
                      ("sleeve", PART_SLEEVE), ("shaft", PART_SHAFT)):
        m = part_tri == pid
        if not m.any():
            continue
        pm = mech.get(name)
        if pm is None:
            raise MissingMechanicalProperty(name, "(unassigned)", "material")
        C_elem[m] = part_C(pm, phi[m])
        rho_elem[m] = pm.density
        present[name] = m

    omega = rpm * 2.0 * math.pi / 60.0

    if thermal_model not in ("band_fit", "free_expansion"):
        raise ValueError("thermal_model must be 'band_fit' or 'free_expansion', "
                         f"got {thermal_model!r}")

    # ── the sleeve interference, as a hoop eigenstrain ──────────────────────
    def _band_eps0(delta_mm: float) -> Optional[np.ndarray]:
        """The band pre-stretched by ``delta_mm`` of radial oversize, as the
        hoop eigenstrain the solve carries; None when there is nothing to
        carry.  The sleeve's stress-free radius is delta_r smaller than where
        it sits, so it is pre-stretched."""
        m = present.get("sleeve")
        if delta_mm <= 0 or rm.r_sleeve_mean_m <= 0 or m is None or not m.any():
            return None
        e = np.zeros((ne, 3))
        e_t0 = -(delta_mm * 1e-3) / rm.r_sleeve_mean_m
        s, c = np.sin(phi[m]), np.cos(phi[m])
        e[m, 0] = e_t0 * s * s
        e[m, 1] = e_t0 * c * c
        e[m, 2] = -2.0 * e_t0 * s * c
        return e

    # ── the rotor TEMPERATURE: the per-part FREE strain ─────────────────────
    # 2026-09-07: one eigenstrain array per part (see ``thermal_eigenstrain``).
    # 2026-09-09: under the default ``band_fit`` model this array is NOT put
    # into the solve.  It is used to measure the fit at temperature (below),
    # and the solve carries that fit as the band's interference — the user's
    # rule, "температура только как изменение давления на бандаж, если он
    # есть".  ``free_expansion`` sums it onto the interference one as before.
    #
    # THE FRAME.  Axis 1 of every card is the same axis ``part_C`` builds the
    # stiffness in, so the two can never disagree:
    #   * sleeve — the hoop-wound fibre direction, phi + 90 deg per element;
    #   * magnet — the MAGNETISATION direction.  This is a spoke rotor: M is
    #     tangential, `M ∥ that magnet's bottom edge in the world frame`
    #     (simulation.sb_domains) = the CCW tangent at the MAGNET's centroid,
    #     which fem_solver_2d builds as (-cy, cx)/|c|.  So axis 1 is that
    #     magnet's own angle + 90 deg — ONE angle for the whole magnet body, not
    #     per element: a sintered block is aligned as a block.  NdFeB expands
    #     +5 ppm/K along that axis and CONTRACTS -1.5 ppm/K across it, so the
    #     anisotropy is real and it is worth carrying;
    #   * rotor / shaft — isotropic steel or aluminium, so the angle is
    #     irrelevant and the hoop convention is used for consistency.
    thermal_notes: List[str] = []
    thermal_parts: Dict[str, Any] = {}
    eps0_th: Optional[np.ndarray] = None
    magnet_axis1 = np.zeros(ne)      # rad, per element; only magnets use it
    if present.get("magnet") is not None and rm.magnet_id_tri is not None:
        # The magnet body's own centroid angle, from the MESH (not from the
        # polygon list): the mesh is what is being solved, and taking the angle
        # from it cannot be knocked out of step by a re-ordering upstream.
        for mid in np.unique(rm.magnet_id_tri[rm.magnet_id_tri >= 0]):
            mm = rm.magnet_id_tri == mid
            magnet_axis1[mm] = math.atan2(float(cen[mm, 1].mean()),
                                          float(cen[mm, 0].mean())) + 0.5 * math.pi
    strain_any = False        # some part WOULD strain freely (dT and a CTE)
    for name, m in present.items():
        pm = mech[name]
        dT = float(dT_part.get(name, 0.0))
        a1, a2 = pm.cte_pair()
        if (pm.cte_source == "missing" and dT != 0.0
                and (has_sleeve or thermal_model == "free_expansion")):
            # (only where the strain would be USED: under band_fit a
            # sleeveless rotor's CTE changes nothing, so no note either)
            # NEVER a 422: a shaft card written before this field existed must
            # still solve.  The part simply does not expand, and the response
            # says which one and why (user 2026-09-07 wanted the temperature
            # universally available, not a new way to fail).
            thermal_notes.append(
                f"{name}: material {pm.material!r} carries no thermal-expansion "
                f"coefficient (cte_ppm_k) — it was treated as 0, so its "
                f"{dT:+.0f} K is carrying no strain")
        theta = (magnet_axis1[m] if name == "magnet"
                 else phi[m] + 0.5 * math.pi)
        thermal_parts[name] = {
            "temp_c": part_temp_c[name],
            # WHERE this part's temperature came from, so a panel never has to
            # guess whether 163 °C was the Thermal solve's magnet or the rotor
            # field standing in for it (2026-09-08).
            "temp_source": ("part" if PART_ASSIGNMENT_KEY[name] in given_temps
                            else ("sleeve_temp_c" if name == "sleeve"
                                  else "rotor_temp_c")),
            "delta_t_c": dT,
            "cte_ppm_k_1": a1 * 1e6,
            "cte_ppm_k_2": a2 * 1e6,
            "cte_source": pm.cte_source,
            "anisotropic": bool(pm.cte_anisotropic),
            # What the part WOULD strain by if nothing held it, in ppm.  The
            # honest per-part number the user asked to see.
            "thermal_strain_ppm": a1 * dT * 1e6,
            "thermal_strain_ppm_1": a1 * dT * 1e6,
            "thermal_strain_ppm_2": a2 * dT * 1e6,
            "axis_1": ("magnetisation (tangential, spoke rotor)"
                       if name == "magnet" else
                       ("hoop (wound fibre direction)" if pm.orthotropic
                        else "hoop (isotropic — the angle does not matter)")),
        }
        if dT == 0.0 or (a1 == 0.0 and a2 == 0.0):
            continue
        strain_any = True
        if eps0_th is None:
            eps0_th = np.zeros((ne, 3))
        eps0_th[m] = thermal_eigenstrain(a1, a2, dT, theta)

    if part_temp_c["sleeve"] != ref_temp_c and not has_sleeve:
        thermal_notes.append(
            f"sleeve_temp_c = {part_temp_c['sleeve']:g} °C was given but this "
            "machine has no sleeve (sleeve_thickness = 0) — it changed nothing")

    # ── the fit AT TEMPERATURE (2026-09-07) ─────────────────────────────────
    # interference_effective_mm = the geometric oversize PLUS the CTE-mismatch
    # growth of what sits under the band, MINUS the band's own bore growth.  Two
    # extra linear solves, and only when a temperature was actually asked for —
    # a request that leaves everything at 20 °C pays nothing.  Measured on the
    # UNSPLIT mesh, before the contact system exists: the sleeve bore and the
    # rotor OD are still the same nodes here.
    interference_effective_mm = interference_mm
    thermal_fit: Dict[str, Any] = {}
    if has_sleeve and eps0_th is not None and rm.r_rotor_od_m > 0:
        pu = mesh.p.T
        r_pu = np.hypot(pu[:, 0], pu[:, 1])
        bore = np.abs(r_pu - rm.r_rotor_od_m) < 2e-5
        sleeve_m = present.get("sleeve")
        inner_m = np.zeros(ne, dtype=bool)
        for _nm in ("rotor", "magnet", "shaft"):
            _mm = present.get(_nm)
            if _mm is not None:
                inner_m |= _mm
        # Only the bore nodes that a real part actually reaches: the band bridges
        # the rotor's OD notches, and a node hanging over a notch would average
        # in a displacement no iron ever had.
        touch_inner = np.zeros(pu.shape[0], dtype=bool)
        touch_inner[mesh.t[:, inner_m].ravel()] = True
        touch_sleeve = np.zeros(pu.shape[0], dtype=bool)
        if sleeve_m is not None:
            touch_sleeve[mesh.t[:, sleeve_m].ravel()] = True
        m_under, m_band = bore & touch_inner, bore & touch_sleeve
        # On a SECTOR the free-growth measurement has to be cyclic too: a wedge
        # of an orthotropic band left genuinely free would simply open up at the
        # cut, and the "free growth" it then reports is the growth of a slit
        # ring and not of a ring (2026-09-09).  The ties are built on the
        # CONFORMING mesh — the interfaces are not split yet here, so a cut-face
        # node is one node and the match is by position alone.
        cyc_free = None
        if sector_plan is not None:
            from motor_ai_sim.simulation.mechanical import symmetry as sym
            _b_free, _e_free = make_basis(mesh, order)
            cyc_free = sym.build_cyclic_ties(_b_free, mesh, None, sector_plan)
        g_under = (free_radial_growth(mesh, C_elem, eps0_th, inner_m, m_under,
                                      order=order, cyclic=cyc_free)
                   if m_under.any() and np.any(eps0_th[inner_m]) else 0.0)
        g_band = (free_radial_growth(mesh, C_elem, eps0_th, sleeve_m, m_band,
                                     order=order, cyclic=cyc_free)
                  if (sleeve_m is not None and m_band.any()
                      and np.any(eps0_th[sleeve_m])) else 0.0)
        interference_effective_mm = interference_mm + (g_under - g_band) * 1e3
        thermal_fit = {
            "sleeve_bore_radius_mm": rm.r_rotor_od_m * 1e3,
            "free_growth_under_sleeve_um": g_under * 1e6,
            "free_growth_sleeve_bore_um": g_band * 1e6,
            "delta_interference_mm": (g_under - g_band) * 1e3,
            "n_bore_nodes_rotor": int(m_under.sum()),
            "n_bore_nodes_sleeve": int(m_band.sum()),
        }
        if interference_effective_mm < 0:
            thermal_notes.append(
                f"the fit has OPENED at temperature: the effective interference "
                f"is {interference_effective_mm:+.4f} mm, i.e. the band's bore "
                "has grown past the rotor. The sleeve is no longer clamping"
                + (" — solved with the band merely resting on the rotor (zero "
                   "interference); at speed the iron's own growth is what "
                   "closes it" if thermal_model == "band_fit" else "") + ".")

    # ── what the solve CARRIES (2026-09-09) ─────────────────────────────────
    thermal_active = False
    if thermal_model == "band_fit":
        # The user's rule: the temperature acts on the band's fit and nowhere
        # else.  Every part is solved as drawn; the band is pre-stretched by
        # the fit AT TEMPERATURE.  A fit that has opened cannot pull the rotor
        # outward, so it is carried as zero, and the note above says so.
        eps0 = _band_eps0(max(interference_effective_mm, 0.0))
        thermal_active = bool(has_sleeve and abs(interference_effective_mm
                                                 - interference_mm) > 1e-12)
        if thermal_active:
            thermal_applied_as = (
                f"band fit only: the {interference_mm:.4f} mm interference is "
                f"{interference_effective_mm:.4f} mm at temperature; every "
                f"part solved as drawn at {ref_temp_c:g} °C")
        elif strain_any and not has_sleeve:
            hot_map = ", ".join(
                f"{PART_ASSIGNMENT_KEY[n]} {part_temp_c[n]:.0f} °C"
                for n in present if abs(dT_part.get(n, 0.0)) > 0)
            thermal_applied_as = "none — no retaining band"
            thermal_notes.append(
                f"no retaining band: the temperatures ({hot_map}) are not a "
                "mechanical load — the rotor's temperature only changes a "
                "band's fit pressure (the magnets sit in an epoxy bed and an "
                "all-iron rotor expands freely), so the parts were solved as "
                f"drawn, at the {ref_temp_c:g} °C reference")
        elif strain_any:
            thermal_applied_as = ("none — nothing under or in the band grew "
                                  "against it")
        else:
            thermal_applied_as = "none — everything at the reference"
    else:
        # free_expansion: the 2026-09-07 model, every part carrying its own
        # eigenstrain, summed onto the band's DRAWN interference (the fit at
        # temperature then comes out of the solve itself).
        eps0 = _band_eps0(interference_mm)
        if eps0_th is not None:
            eps0 = eps0_th if eps0 is None else (eps0 + eps0_th)
        thermal_active = strain_any
        thermal_applied_as = ("per-part thermal eigenstrain in every part "
                              "(free_expansion — verification model)")

    # ── the contact system: split the interfaces, assemble ONCE ─────────────
    spec = dict(ctc.DEFAULT_CONTACTS)
    spec.update(contacts or {})
    led.phase("assembly (contact system, stiffness, loads)")
    cs = ctc.build_contact_system(mesh, part_tri, spec, order=order)
    smesh = cs.mesh                                 # interfaces duplicated
    basis, K, f_rot, f_eig = assemble_plane_stress(
        smesh, C_elem, rho_elem, eps0, order=order)
    # ── the cyclic-symmetry ties, on the mesh that is actually solved ───────
    # Built here and hung on the contact system, so ``solve_contact`` carries
    # them through every case, every lift-off solve and every load step without
    # a second code path (2026-09-09).  ``pair_no_row`` drops the trailing cut
    # face's bilateral rows, which the ties already imply exactly — see the
    # field's docstring in ``contact``.
    cyclic_ties = None
    if sector_plan is not None:
        from motor_ai_sim.simulation.mechanical import symmetry as sym
        cyclic_ties = sym.build_cyclic_ties(basis, smesh, part_tri, sector_plan)
        cs.cyclic = cyclic_ties
        cs.pair_no_row = sym.cut_face_pairs(cs, sector_plan) & ~cs.pair_unilateral
    _solver_name, solve_fn = ctc._solver()  # noqa: SLF001 - module-internal by design
    led.at(2, "assembly (contact system, stiffness, loads)")

    # ── the electromagnetic torque, and the bore that reacts it ─────────────
    L_stack = float(stack_length_mm or 0.0) * 1e-3
    f_torque = np.zeros_like(f_rot)
    torque_report: Optional[Dict[str, Any]] = None
    hold = None
    # On a SECTOR the wedge's own air-gap arc carries 1/n of the machine's
    # torque and its own bore arc reacts 1/n — the neighbouring sectors carry
    # the rest, which is exactly what the cyclic ties say (2026-09-09).  The
    # traction that results is IDENTICAL to the full model's: the arc is 1/n as
    # long and the torque is 1/n as big.
    torque_sector_nm = float(torque_nm) / n_sym
    if use_torque:
        f_torque, torque_report = torque_load(smesh, part_tri, torque_sector_nm,
                                              L_stack, order=order)
        hold = bore_hold(smesh, part_tri, order=order)
        if hold is not None and sector_plan is not None:
            from motor_ai_sim.simulation.mechanical import symmetry as sym
            hold = sym.drop_cut_face_hold(hold, sector_plan)
        if hold is None:
            raise ValueError(
                "the torque has nothing to react against: this cross-section "
                "has no bore (no inner boundary on the shaft tube or the rotor "
                "core), and the shaft bore held tangentially is what carries "
                "the reaction. Solve with loads='centrifugal', or give the "
                "rotor a shaft hole.")
        torque_report["reaction_at"] = hold.label
        torque_report["reaction_rows"] = int(hold.n_rows)
        if n_sym > 1:
            # The report is about the MACHINE — the traction and the gap radius
            # are the same in a sector and in the full rotor, the loaded arc and
            # its coverage are 1/n of it.  The sector's own share is kept beside
            # them rather than hidden.
            torque_report["torque_sector_nm"] = torque_sector_nm
            torque_report["torque_nm"] = float(torque_nm)
            torque_report["surface_mm"] = float(torque_report["surface_mm"]) * n_sym
            # T / (2 pi r^2 L) is the machine's nominal shear, so it takes the
            # machine's torque; `traction_mpa` is the traction that was actually
            # applied and is the SAME number in both models (1/n of the torque
            # over 1/n of the arc), so it is left alone.
            torque_report["traction_nominal_mpa"] = \
                float(torque_report["traction_nominal_mpa"]) * n_sym
            if torque_report.get("coverage") is not None:
                torque_report["coverage"] = float(torque_report["coverage"]) * n_sym
            torque_report["n_facets_sector"] = int(torque_report["n_facets"])
            torque_report["n_sectors"] = n_sym

    # The load cases, as multipliers on omega^2.  In single-speed mode there is
    # exactly one, and it is NAMED BY ITS SPEED ("23,000 rpm") rather than
    # "rated": the whole point of the mode is that this speed is the only one
    # the answer covers, and a column headed "rated" would invite reading it as
    # one of three (user 2026-09-06).
    if case_mode == "single":
        single_name = f"{rpm:,.0f} rpm"
        cases = {single_name: 1.0}
        case_rpm = {single_name: rpm}
        primary_case = single_name
    else:
        cases = {"standstill": 0.0, "rated": 1.0,
                 "overspeed": overspeed_factor ** 2}
        case_rpm = {"standstill": 0.0, "rated": rpm,
                    "overspeed": rpm * overspeed_factor}
        primary_case = "rated"

    #: An interface counts as LIFTED OFF when half its length has opened.  With
    #: a separation contact "any tension" is not a verdict — a real joint has a
    #: few open facets at every speed (the sleeve bridges the OD notches of this
    #: rotor and is 40 % open at standstill by geometry alone).  Half the arc
    #: gone is a joint that has stopped being a joint.
    open_verdict = 0.50

    # One nonlinear solve per case, warm-started from the previous one: the
    # active set of the rated case is close to the standstill one, which is what
    # keeps the iteration count in single figures.
    warm: Optional[np.ndarray] = None
    sols: Dict[str, Any] = {}
    of_cache: Dict[float, Dict[str, float]] = {}

    def _run(k: float, warm_set, phase: Optional[str] = None):
        # `use_centrifugal` is a multiplier, not a branch: loads='torque' is the
        # same solve with the body force switched off, so nothing about the
        # contact treatment differs between the three menu entries.
        #
        # THE LOAD IS SPLIT, not summed (2026-09-09).  The spin and the heat are
        # what the rotor is carrying before the drive is switched on, and they
        # are what a loose magnet seats under; the torque is then walked on in
        # ``TORQUE_LOAD_STEPS`` increments from that seated state.  Coulomb
        # friction has a history and not a value, and on the G2's self-locking
        # wedge applying the whole traction at once let the active set choose a
        # locked state carrying seven times the load (user: "получается, что от
        # момента больше деформации, чем от вращения?" — no; see the load-path
        # section of ``contact``).  With loads='centrifugal' there is no ramp and
        # the solve is one step, exactly the arithmetic it always ran.
        f = f_rot * (omega ** 2 * k * float(use_centrifugal)) + f_eig
        # The contact loop counts its OWN iterations from 1; this shifts them
        # onto the global bar and hands back whatever the budget did not need.
        base = led.done
        # Reserve the EXPECTED iterations for this solve (a lift-off solve was
        # not in the opening total; a case was), grow if the loop needs more,
        # hand back what it did not use.
        led.grow(max(0, (base + _expect) - led.total))

        def _iter(it, _mx):
            if base + it > led.total:
                led.grow(base + it - led.total)
            led.at(base + it, phase)

        sol = ctc.solve_contact(K, f, cs, basis, closed0=warm_set,
                                max_iter=MAX_CONTACT_ITER, solve_fn=solve_fn,
                                hold=hold, progress=_iter,
                                f_ramp=(f_torque if use_torque else None),
                                load_steps=TORQUE_LOAD_STEPS)
        # …and if the active set never settled, TWO RESCUES, in this order
        # (2026-09-10).  Coulomb friction has a history, not a value: on a rotor
        # whose magnets are held by nothing but the band, the one-shot solve can
        # sit in any of several states and report the least-violating one as if
        # it were the answer.  Measured on the live Ø200 at 22,900 rpm, mu 0.2,
        # loads = centrifugal: the loop limit-cycled with every magnet/iron pair
        # OPEN and quoted a band at 3,896 MPa with 266 µm of growth; walking the
        # spin on in steps limit-cycled the other way, every pair CLOSED, and
        # quoted 252 MPa with 13 µm — a rotor that looks safe because friction
        # was believed to hold the magnets.  The same case FRICTIONLESS
        # converges in eight iterations at 1,688 MPa and 419 µm, and the case
        # with the torque (which is ramped) converges at 1,717 and 421.  The
        # user caught the pair: "не может такого быть, чтобы при только
        # центробежной силе деформации были больше, чем ещё и при моменте".
        #
        #   1. walk the spin on in ``SPIN_LOAD_STEPS`` — kept ONLY if it
        #      actually converges, because a second unsettled state is not an
        #      improvement on the first;
        #   2. failing that, solve the same machine with the SEPARATION joints
        #      FRICTIONLESS.  That is the conservative bound for retention —
        #      friction can only ever help hold a magnet in, so a band sized on
        #      mu = 0 is never undersized — and it is the branch that converges.
        #      The answer says so in ``contact.friction_note`` rather than
        #      quoting a number whose friction state nobody chose.
        if not sol.converged and float(use_centrifugal) and omega > 0.0:
            f_spin = f_rot * (omega ** 2 * k * float(use_centrifugal))
            ramp = f_spin + (f_torque if use_torque else 0.0)
            _log.info("contact: the active set did not settle with the spin "
                      "applied at once — retrying with it walked on in %d steps",
                      SPIN_LOAD_STEPS)
            retry = ctc.solve_contact(K, f_eig, cs, basis, closed0=warm_set,
                                      max_iter=MAX_CONTACT_ITER,
                                      solve_fn=solve_fn, hold=hold,
                                      progress=_iter, f_ramp=ramp,
                                      load_steps=SPIN_LOAD_STEPS)
            if retry.converged:
                sol = retry
            elif any(sp.type == "separation" and float(sp.mu) > 0.0
                     for sp in spec.values()):
                _log.warning("contact: neither the one-shot nor the %d-step spin "
                             "ramp settled at %g rpm — re-solving the separation "
                             "joints FRICTIONLESS, which is the conservative "
                             "bound for retention", SPIN_LOAD_STEPS, rpm * k)
                # The SAME assembled system — only the per-pair friction
                # coefficient changes, and `solve_contact` reads that off
                # `cs.pair_mu` (built once from the specs), so the stiffness,
                # the mesh, the ties and the loads are untouched and the two
                # answers are the same problem minus the friction.
                mu_saved = cs.pair_mu.copy()
                try:
                    cs.pair_mu = np.zeros_like(mu_saved)
                    free_sol = ctc.solve_contact(
                        K, f, cs, basis, closed0=None,
                        max_iter=MAX_CONTACT_ITER, solve_fn=solve_fn, hold=hold,
                        progress=_iter,
                        f_ramp=(f_torque if use_torque else None),
                        load_steps=TORQUE_LOAD_STEPS)
                except Exception as _exc:                   # noqa: BLE001
                    _log.warning("contact: the frictionless rescue failed (%s) "
                                 "— the unsettled answer stands", _exc)
                    free_sol = None
                finally:
                    cs.pair_mu = mu_saved
                if free_sol is not None and free_sol.converged:
                    sol = free_sol
                    sol.friction_note = (
                        "the frictional contact never settled at this speed — "
                        "this answer is the same machine with the separation "
                        "joints FRICTIONLESS, which converged. Friction can only "
                        "help hold a part in, so the retention and the band "
                        "stress here are the conservative side; a torque path "
                        "that needs friction is NOT in this answer.")
        n_it = int(sol.iterations)
        led.at(base + n_it, phase)
        led.give_back(max(0, (base + _expect) - led.done))
        # …and learn: the next bar opens on what this machine actually needed.
        _LEARNED_CONTACT_ITER["iters"] = int(min(
            MAX_CONTACT_ITER, max(3, round(0.5 * _LEARNED_CONTACT_ITER["iters"] + 0.5 * n_it))))
        return sol

    def _open_fracs(sol) -> Dict[str, float]:
        rep = ctc.facet_report(cs, sol)
        out_: Dict[str, float] = {}
        for lb, d in rep.items():
            ll = d["length"]
            out_[lb] = float((d["open"] * ll).sum() / max(ll.sum(), 1e-30))
        return out_

    for _i, (case, k) in enumerate(cases.items()):
        sol = _run(k, warm, f"case {_i + 1}/{_n_cases}: {case} — "
                            "contact iterations")
        warm = sol.closed
        sols[case] = sol
        of_cache[k] = _open_fracs(sol)
    led.phase("post-processing (stress recovery, safety factors)")

    # Rotor OD nodes — the air-gap closure is their radial growth.
    ps = smesh.p.T
    r_node = np.hypot(ps[:, 0], ps[:, 1])
    od_r = rm.r_rotor_od_m if rm.r_rotor_od_m > 0 else float(r_node.max())
    od_mask = r_node > (od_r - 1e-5) if has_sleeve else r_node > (float(r_node.max()) - 1e-5)
    if not od_mask.any():
        od_mask = r_node > (float(r_node.max()) - 1e-5)

    # Element areas (m^2) for the mass report.
    v0, v1, v2 = p[mesh.t[0]], p[mesh.t[1]], p[mesh.t[2]]
    area = 0.5 * np.abs((v1[:, 0] - v0[:, 0]) * (v2[:, 1] - v0[:, 1])
                        - (v2[:, 0] - v0[:, 0]) * (v1[:, 1] - v0[:, 1]))
    L = float(stack_length_mm or 0.0) * 1e-3
    nodal = basis.nodal_dofs

    # ── percentiles are about the MACHINE, not about the wedge (2026-09-09) ─
    # A percentile is a rank in a POPULATION, and the population of a sector
    # solve is n times smaller than the machine's.  p99.5 of the G2's 355
    # rotor elements per pole is the 2nd-highest of that pole — deep inside the
    # singular corner the percentile exists to leave out — while p99.5 of the
    # full model's 9,940 is the 50th highest.  Measured on the G2-L40 at
    # mesh 1.0: 20.67 MPa the wedge's way against 27.34 full, and 28.70 when
    # asked of the same wedge's field repeated 28 times, which is what the
    # machine actually is.  So every reported percentile is taken on the
    # REPLICATED population; with n = 1 this is `np.percentile` on the array
    # itself, i.e. bit for bit the arithmetic this solver has always run.
    def _pct(a: np.ndarray, q: float) -> float:
        return float(np.percentile(np.tile(a, n_sym) if n_sym > 1 else a, q))

    # -- AVERAGED, the way every other FE tool reports a stress --------------
    #
    # User 2026-09-10: "как нам теперь объяснять пользователям эти две разные
    # цифры 1728 и 1426? нас не поймут, везде и в Ansys и Fusion полное
    # соответствие" — and they are right, so the convention is now theirs.
    #
    # ANSYS and Fusion report a stress on the NODES of an averaged plot: each
    # element's constant value is area-averaged onto the nodes it touches,
    # WITHIN its own body (never across a material boundary, where the stress
    # is genuinely discontinuous), and the number in the results table is the
    # maximum of that nodal field — the same number the colour bar ends at.
    # This solver used to report the raw element peak instead, which is what
    # those tools call an UNAVERAGED plot: on the live band 1717 MPa against
    # 1559 averaged, a 9 % gap no user could reconcile with their own run.
    #
    # Averaging is also what makes the peak mesh-independent enough to size a
    # part by: a re-entrant corner's element value keeps climbing as the mesh
    # is refined, while its nodal average is shared with the elements round it.
    #
    # Corner nodes only (P2 mid-side nodes carry no element-constant value), so
    # this is exactly the field the viewer draws and the report plots.
    _nn = int(p.shape[0])
    _tri = mesh.t

    # On a SECTOR the two cut faces are ONE line of the machine, so a node on
    # the leading face and its partner on the trailing face are one node with
    # elements on both sides of it.  Averaged separately they would each get a
    # one-sided average, and the wedge would report a different peak from the
    # full ring for no physical reason (measured while writing this: rotor von
    # Mises p99.5 223 MPa full against 200 MPa on the sector, -10 %).  So the
    # partners are folded onto one accumulator first.  The fold is geometric
    # and needs nothing from the tie builder: an angle taken modulo the sector
    # angle sends both faces to the same value, and the residue is quantised
    # CIRCULARLY so the wrap at the sector angle lands on 0 and not beside it.
    _group = np.arange(_nn)
    if n_sym > 1:
        _ang = 2.0 * np.pi / float(n_sym)
        _rr = np.hypot(p[:, 0], p[:, 1])
        _qa = np.mod(np.rint(np.mod(np.arctan2(p[:, 1], p[:, 0]), _ang)
                             / _ang * 1e6), 1e6).astype(np.int64)
        _qr = np.rint(_rr * 1e7).astype(np.int64)          # 0.1 um buckets
        _seen: Dict[tuple, int] = {}
        for _i in range(_nn):
            _k = (int(_qr[_i]), int(_qa[_i]))
            _g = _seen.setdefault(_k, _i)
            _group[_i] = _g
        _log.info("rotor stress: %d of %d nodes folded across the sector cut",
                  int(_nn - len(_seen)), _nn)

    def _nodal(a: np.ndarray, m: np.ndarray) -> np.ndarray:
        """`a` (per element) area-averaged onto the nodes of the part `m`."""
        w = area[m]
        av = a[m]
        num = np.zeros(_nn)
        den = np.zeros(_nn)
        for _r in range(3):
            idx = _group[_tri[_r][m]]
            np.add.at(num, idx, av * w)
            np.add.at(den, idx, w)
        hit = den > 0.0
        if not hit.any():
            return av
        return num[hit] / den[hit]

    # ── how much AIR GAP is left when the rotor has grown into it ───────────
    #
    # User 2026-09-10: *"не забудь добавить в отчёт, как меняется зазор"*.  The
    # clearance is the number that decides whether the machine rubs, and until
    # now the report carried only the growth — the reader had to subtract by
    # hand, on a machine where the gap and the band thickness are two different
    # geometry fields.
    #
    # Measured off the DRAWING, not off the geometry parameters: the bore is
    # the smallest radius anywhere on the stator polygon (a tooth tip, which is
    # what the rotor would touch first), and the rotor's own outer radius is the
    # mesh's largest.  A chamfer, a stepped pole or a wedge therefore counts,
    # and nothing has to be kept in step with a parameter name.
    r_bore_m = 0.0
    _stator = polys_all.get("stator") if isinstance(polys_all, dict) else None
    if _stator is not None:
        try:
            _rs_all = []
            _geoms = (list(_stator.geoms) if hasattr(_stator, "geoms")
                      else [_stator])
            for _g in _geoms:
                _rings = [_g.exterior] + list(_g.interiors)
                for _ring in _rings:
                    _xy = np.asarray(_ring.coords, dtype=float)
                    if _xy.size:
                        _rs_all.append(np.hypot(_xy[:, 0], _xy[:, 1]).min())
            if _rs_all:
                r_bore_m = float(min(_rs_all)) * 1e-3      # mm -> m
        except Exception as _e_b:                          # noqa: BLE001
            _log.debug("air-gap clearance: stator bore not measurable (%s)", _e_b)

    results: Dict[str, Any] = {}
    fields: Dict[str, Any] = {}
    for case, k in cases.items():
        sol = sols[case]
        _sig_c, sig, vm_max = recover_stress(smesh, sol.u, C_elem, eps0,
                                             order=order)
        u = np.stack([sol.u[nodal[0]], sol.u[nodal[1]]], axis=1)
        sxx, syy, sxy = sig[:, 0], sig[:, 1], sig[:, 2]
        vm = np.sqrt(np.maximum(sxx ** 2 - sxx * syy + syy ** 2 + 3 * sxy ** 2, 0))
        p1, p2 = _principals(sig)
        srr, stt = _polar(sig, phi)

        # ── the safety-factor map, one criterion per part ───────────────────
        # Done HERE and not in the browser: "strength / stress" is only a
        # safety factor once you know WHICH stress and WHICH strength, and that
        # answer is per material (a magnet is checked on both principals, the
        # CFRP sleeve on its hoop fibres, steel on von Mises).  Re-deriving that
        # in a map component is how the two halves of a project start
        # disagreeing about whether a rotor is safe.
        sf_tri = np.full(ne, SF_CLAMP)
        sf_per_part: Dict[str, Any] = {}

        parts_out: Dict[str, Any] = {}
        for name, m in present.items():
            pm = mech[name]
            sf_m = element_safety_factor(pm, name, vm[m], p1[m], p2[m],
                                         srr[m], stt[m])
            sf_tri[m] = sf_m
            # The SAME criterion on the AVERAGED field — the one number the
            # tiles, the tables, the report and the colour bar are all written
            # against since 2026-09-10.  Running the criterion function rather
            # than dividing by hand is what keeps "strength / the stress on the
            # tile" true for every part at once: steel is judged on von Mises
            # against yield, a magnet on its principals, and the band on its
            # HOOP fibres (its 2500 MPa is a fibre-direction strength, so
            # dividing it by an off-fibre principal would be the wrong sum).
            vm_n = _nodal(vm, m)
            p1_n = _nodal(p1, m)
            p2_n = _nodal(p2, m)
            srr_n = _nodal(srr, m)
            stt_n = _nodal(stt, m)
            sf_n = element_safety_factor(pm, name, vm_n, p1_n, p2_n,
                                         srr_n, stt_n)
            sf_avg = float(sf_n.min())
            sf_per_part[name] = {
                # THE safety factor: strength over the governing AVERAGED
                # stress, which is the stress printed beside it everywhere.
                "averaged": sf_avg,
                # Kept as diagnostics, not as verdicts: the raw element minimum
                # and its 5th percentile.  A re-entrant corner is a stress
                # SINGULARITY whose element safety factor keeps falling as the
                # mesh is refined; the gap between these and `averaged` is how
                # singular the part's worst corner is.
                "min": float(sf_m.min()),
                "p05": _pct(sf_m, 5.0),
                "criterion": sf_criterion_text(pm, name),
            }
            parts_out[name] = {
                "material": pm.material,
                "von_mises_max_mpa": float(vm_n.max()) * MPA,
                "von_mises_p995_mpa": _pct(vm_n, 99.5) * MPA,
                "principal_max_p995_mpa": _pct(p1_n, 99.5) * MPA,
                # The same percentiles on the ELEMENT field.  An averaged peak
                # at a re-entrant corner is shared with whatever elements touch
                # the node, so it moves with the mesh; the element percentile
                # drops the singular tail instead and is the number to compare
                # two DIFFERENT meshes of one rotor on (a sector against the
                # full ring, a mesh study).  Kept for that, not for sizing.
                "von_mises_p995_unaveraged_mpa": _pct(vm[m], 99.5) * MPA,
                "principal_max_p995_unaveraged_mpa": _pct(p1[m], 99.5) * MPA,
                "principal_max_mpa": float(p1_n.max()) * MPA,
                "principal_min_mpa": float(p2_n.min()) * MPA,
                "hoop_max_mpa": float(stt_n.max()) * MPA,
                "radial_min_mpa": float(srr_n.min()) * MPA,
                "radial_max_mpa": float(srr_n.max()) * MPA,
                # ...and the UNAVERAGED element peaks beside them, so nothing
                # is hidden: this is the other half of the same toggle ANSYS
                # offers, and the gap between them is the corner singularity.
                "von_mises_max_unaveraged_mpa": float(vm[m].max()) * MPA,
                "principal_max_unaveraged_mpa": float(p1[m].max()) * MPA,
                "hoop_max_unaveraged_mpa": float(stt[m].max()) * MPA,
                "radial_min_unaveraged_mpa": float(srr[m].min()) * MPA,
                "stress_convention": "averaged",
                "strength_mpa": pm.strength * MPA,
                "strength_kind": pm.strength_kind,
                # The stress the safety factor was actually divided into, so
                # strength / governing_stress_mpa == safety_factor exactly.
                "governing_stress_mpa": (float(pm.strength / sf_avg) * MPA
                                         if sf_avg > 0 and np.isfinite(sf_avg)
                                         else None),
                "safety_factor": (sf_avg if np.isfinite(sf_avg) else None),
                # EXTENSIVE: the sector holds 1/n of the part, so the machine's
                # mass is n times the wedge's (2026-09-09).  Every stress above
                # is intensive and is the same number either way.
                "mass_kg": (float((area[m] * pm.density).sum() * L) * n_sym
                            if L > 0 else None),
            }

        rep = ctc.facet_report(cs, sol)
        forces = ctc.interface_forces(cs, sol)
        ifaces: Dict[str, Any] = {}
        cpress: Dict[str, Any] = {}
        copen: Dict[str, Any] = {}
        for k_if, it in enumerate(cs.interfaces):
            label = it.label
            if it.n_facets == 0:
                ifaces[label] = None
                continue
            d = rep[label]
            ll = d["length"]
            ofrac = float((d["open"] * ll).sum() / max(ll.sum(), 1e-30))
            # The v1 number, kept: sigma_nn recovered from the ELEMENT stresses
            # on both sides of the facet.  It is the independent check on the
            # contact pressure (they must agree in sign) and it is the only
            # meaningful traction for a bonded pair, which cannot open.
            tn = 0.5 * (_normal_traction(sig, it.elem_a, it.normal)
                        + _normal_traction(sig, it.elem_b, it.normal))
            pr = d["pressure"]
            # ── the TANGENTIAL half of the joint (2026-09-07) ───────────────
            # The user's question is whether the poles can pass the torque on
            # when the bridges have yielded, so every pair reports what it is
            # doing tangentially, not only whether it is shut:
            #   slip_fraction  — length share that is closed and SLIDING
            #   stick_fraction — length share that is closed and STUCK
            #   torque_transmitted_nm — what the joint actually carried
            #   friction_capacity_nm  — mu * integral(p r dA), the most Coulomb
            #                           could ever pass at this clamp
            slipf = float((d["slip"] * ll).sum() / max(ll.sum(), 1e-30))
            stickf = float((d["stick"] * ll).sum() / max(ll.sum(), 1e-30))
            r_facet = np.hypot(it.seg[:, :, 0].mean(axis=1),
                               it.seg[:, :, 1].mean(axis=1))
            cap = float(it.spec.mu * (np.maximum(pr, 0.0) * ll * r_facet).sum()
                        * L_stack) if it.spec.mu > 0 else 0.0
            fpair = forces.get(label, {})
            pmask = cs.pair_iface == k_if
            slip_um = float(np.abs(sol.slip[pmask & sol.closed]).max()) * 1e6 \
                if (pmask & sol.closed).any() else 0.0
            # ── sector -> machine (2026-09-09) ──────────────────────────────
            # The LINE INTEGRALS of a joint are extensive: the wedge carries
            # 1/n of the arc, 1/n of the transmitted torque and 1/n of the
            # Coulomb capacity, and quoting those raw beside a full-model answer
            # would read as a joint eight times weaker than the same joint.  The
            # length-weighted MEANS and FRACTIONS beside them are intensive and
            # are the same number in both models.
            ifaces[label] = {
                "n_facets": int(it.n_facets),
                "type": it.spec.type,
                "mu": float(it.spec.mu),
                "length_mm": float(ll.sum()) * 1e3 * n_sym,
                "slip_fraction": slipf,
                "stick_fraction": stickf,
                "slip_max_um": slip_um,
                "tangential_force_kn_per_m":
                    float(fpair.get("tangential_n_per_m", 0.0)) / 1e3 * n_sym,
                "shear_mean_mpa":
                    float((d["shear"] * ll).sum() / max(ll.sum(), 1e-30)) * MPA,
                "torque_transmitted_nm":
                    float(fpair.get("torque_n_m_per_m", 0.0)) * L_stack * n_sym,
                "friction_capacity_nm": cap * n_sym,
                # Most COMPRESSIVE (the clamp) and most TENSILE (the lift-off).
                "normal_min_mpa": float(tn.min()) * MPA,
                "normal_max_mpa": float(tn.max()) * MPA,
                "normal_p95_mpa": _pct(tn, 95) * MPA,
                # The contact answer: pressure is >= 0 for a separation pair by
                # construction, and NEGATIVE for a bonded/sliding tie that is
                # holding tension.
                "pressure_min_mpa": float(pr.min()) * MPA,
                "pressure_max_mpa": float(pr.max()) * MPA,
                "pressure_mean_mpa": float((pr * ll).sum() / max(ll.sum(), 1e-30)) * MPA,
                "open_fraction": ofrac,
                "gap_max_um": float(d["gap"].max()) * 1e6,
                "radial_force_kn_per_m":
                    float(forces.get(label, {}).get("radial_n_per_m", 0.0))
                    / 1e3 * n_sym,
                # For a separation pair the verdict is geometric — how much of
                # the arc has opened.  For a bonded/sliding tie nothing CAN
                # open, so it stays the v1 test: is the tie holding tension?
                "lift_off": (bool(ofrac > open_verdict) if it.unilateral
                             else bool(_pct(tn, 95) > 0.0)),
            }
            if with_field:
                cpress[label] = np.round(pr * MPA, 3).tolist()
                copen[label] = np.round(d["open"], 3).tolist()

        # The worst part by THE safety factor (averaged, 2026-09-10),
        # not by the raw element minimum: a rotor is accepted or rejected
        # on the number its tiles and tables print, so the part named as
        # worst has to be the part that number belongs to.
        sf_min_part = min(sf_per_part,
                          key=lambda k_: sf_per_part[k_]["averaged"]) \
            if sf_per_part else None
        sf_p05_part = min(sf_per_part, key=lambda k_: sf_per_part[k_]["p05"]) \
            if sf_per_part else None

        umag = np.hypot(u[:, 0], u[:, 1])
        ur = (u[:, 0] * ps[:, 0] + u[:, 1] * ps[:, 1]) / np.maximum(r_node, 1e-12)
        # ── the OD growth is the ROTOR's, never a seated part's (2026-09-09) ─
        # `u` now carries the rigid travel of anything that was seated, which is
        # exactly right for the map and exactly wrong here: the air-gap closure
        # is what the surface facing the stator did, and a magnet that fell
        # 0.1 mm outward onto its lip is not the rotor growing.  The seated
        # nodes are the ones `u_seat` moved, so they are taken straight out —
        # and nothing is taken out on a solve where nothing was seated.
        od_case = od_mask
        # …and, separately, the OUTERMOST SURFACE itself.  `od_mask` on a
        # sleeved rotor starts at the ROTOR's OD, so it holds the band's whole
        # cross-section (1,733 nodes across 2.15 mm of carbon on the Ø200), and
        # a maximum taken over it is the largest travel ANYWHERE in the band —
        # which for a ring loaded from inside is its BORE, not its top.  The
        # number that closes the air gap is the top's, so it gets its own node
        # set: everything within 10 µm of the mesh's largest radius.
        top_mask = r_node > (float(r_node.max()) - 1e-5)
        top_case = top_mask
        if sol.u_seat.size == sol.u.size and np.any(sol.u_seat):
            us = np.stack([sol.u_seat[nodal[0]], sol.u_seat[nodal[1]]], axis=1)
            still = ~(np.abs(us) > 0).any(axis=1)
            if (od_mask & still).any():
                od_case = od_mask & still
            if (top_mask & still).any():
                top_case = top_mask & still
        # The bore reaction is the SECTOR's share; the balance is the ratio of
        # the two shares, which is the same number as the machine's ratio, and
        # the reported reaction is scaled to the machine so it sits beside the
        # applied torque on the same axis (2026-09-09).
        react_sector_nm = float(sol.hold_torque_n_m_per_m) * L_stack
        balance = ((react_sector_nm / torque_sector_nm)
                   if (use_torque and torque_sector_nm) else None)
        react_nm = react_sector_nm * n_sym
        torque_path = _torque_path(ifaces, torque_nm, sol, balance,
                                   r_out_m=float(r_node.max())) \
            if use_torque else None
        # ── did this solve mean anything? (2026-09-09) ──────────────────────
        # Refused, not graded: a stress map of a rotor whose piece ran away is
        # a picture of the algebra, not of the machine.
        ran = runaway_verdict(float(umag.max()), float(r_node.max()),
                              list(sol.free_parts), balance, ifaces,
                              list(sol.seating_capped))
        if ran is not None:
            raise RotorRanAway(case, case_rpm[case], ran["pair"],
                               ran["open_fraction"], ran["reasons"])
        results[case] = {
            "rpm": case_rpm[case],
            "parts": parts_out,
            "sf_min_per_part": sf_per_part,
            # Raw minimum over every element, and the same field with the
            # singular corner elements taken out.  Quote the percentile, keep
            # the raw one next to it — a big gap between the two IS the finding.
            "sf_min": (sf_per_part[sf_min_part]["averaged"]
                       if sf_min_part else None),
            # The raw element minimum ANYWHERE on the rotor beside it, as the
            # singularity gauge — its own worst part, which need not be the
            # part with the worst averaged factor.
            "sf_min_unaveraged": (min(r["min"] for r in sf_per_part.values())
                                  if sf_per_part else None),
            "sf_min_part": sf_min_part,
            "sf_min_p05": (sf_per_part[sf_p05_part]["p05"] if sf_p05_part
                           else None),
            "sf_min_p05_part": sf_p05_part,
            "interfaces": ifaces,
            "interface_open_frac": {lb: (v["open_fraction"] if v else None)
                                    for lb, v in ifaces.items()},
            # The retention question is "what carries the magnets' CENTRIFUGAL
            # load", so with loads='torque' — the body force switched off —
            # there is no load to carry and no share to quote.  Passing the
            # spin anyway divided a torque reaction by a centrifugal load that
            # was never applied, and read out as "the magnets are not retained"
            # on a case that never asked them to be (2026-09-09).
            "magnet_retention": _scale_retention(_retention(
                cs, sol, forces, present, area, rho_elem, cen,
                omega * math.sqrt(k) * float(use_centrifugal),
                seated=sol.seated), n_sym),
            "contact": {
                "converged": bool(sol.converged),
                "iterations": int(sol.iterations),
                "max_penetration_um": float(sol.max_penetration) * 1e6,
                # How far the reported iterate is from satisfying the
                # complementarity conditions, µm (2026-09-09).  0 when the active
                # set settled; otherwise the widest gap the set and the solve
                # still disagree about — a "not converged" flag with a size.
                "residual_um": float(sol.residual) * 1e6,
                # ── the load path (2026-09-09) ─────────────────────────────
                # How many increments the load was walked in (1 = applied in one
                # go, which is every centrifugal-only solve) and what each one's
                # own residual was.  A friction state is a history, so the walk
                # is part of the answer and is quoted with it.
                "load_steps": int(sol.load_steps),
                # …and whether this answer had to give the friction up to get an
                # active set that settles at all (2026-09-10) — see the rescue
                # in `_run`.  None on every solve that converged as asked.
                "friction_note": getattr(sol, "friction_note", None),
                "step_residuals_um": [float(x) * 1e6 for x in sol.step_residuals],
                "n_frozen_pairs": int(sol.n_frozen),
                "n_bodies": int(sol.n_components),
                "unretained_parts": list(sol.free_parts),
                # Parts that came loose and were TRAVELLED onto a surface
                # (2026-09-09).  Empty on every solve where nothing floats.
                "seated": [{"part": s["part"],
                            "component_id": int(s["component_id"]),
                            "travel_um": float(s["travel_m"]) * 1e6,
                            # How far it moved IN ITS POCKET (2026-09-09).  The
                            # absolute travel is measured from the cold mesh, so
                            # on a hot rotor most of it is the part riding with
                            # the iron around it: the G2-L40 reads 105 µm of
                            # which ~90 is the pocket's own growth.  This one is
                            # the clearance that actually closed, and it is what
                            # the verdict and the panel quote.
                            "travel_rel_um": float(s["travel_rel_m"]) * 1e6,
                            "direction": list(s["direction"]),
                            "landed_on": s["landed_on"],
                            "n_pairs_closed": int(s["n_pairs_closed"])}
                           for s in sol.seated],
            },
            "rotor_od_growth_um": float(ur[od_case].max()) * 1e6,
            # …and the same surface spelled out (user 2026-09-10: "нужно ещё
            # считать максимальное радиальное смещение верха бандажа как
            # отдельное число в таблице").  This is the surface that faces the
            # stator — the band's outside when there is one, the iron's when
            # there is not — so its radial motion is what eats the MECHANICAL
            # clearance (air gap minus the band).  The maximum is the number
            # that decides whether the rotor rubs; the mean says how much of it
            # is the whole ring growing and how much is one lobe, and the two
            # differ by 1.6x on a rotor whose magnets push the band out between
            # the poles.
            "od_growth": {
                "max_um": float(ur[top_case].max()) * 1e6,
                "mean_um": float(ur[top_case].mean()) * 1e6,
                "min_um": float(ur[top_case].min()) * 1e6,
                "r_mm": float(r_node[top_case].mean()) * 1e3,
                "part": ("sleeve" if has_sleeve else "rotor"),
                "n_nodes": int(top_case.sum()),
            },
            # …and what that leaves of the gap.  `clearance_um` is the COLD,
            # drawn clearance between the rotor's outermost surface and the
            # stator bore — the air gap minus the band, measured rather than
            # assumed.  `closed_um` is the top's own travel, `remaining_um`
            # what is left of the clearance at this speed, before any
            # manufacturing tolerance, bearing clearance or shaft whirl, none
            # of which this solve knows about.
            "air_gap": ({
                "clearance_um": (r_bore_m - float(r_node.max())) * 1e6,
                "closed_um": float(ur[top_case].max()) * 1e6,
                "remaining_um": ((r_bore_m - float(r_node.max()))
                                 - float(ur[top_case].max())) * 1e6,
                "closed_pct": (100.0 * float(ur[top_case].max())
                               / max(r_bore_m - float(r_node.max()), 1e-12)),
                "bore_r_mm": r_bore_m * 1e3,
                "rotor_r_mm": float(r_node.max()) * 1e3,
            } if r_bore_m > float(r_node.max()) else None),
            "max_displacement_um": float(umag.max()) * 1e6,
            # The torque that came back OUT at the bore.  It must equal the one
            # that went in — that is the only check that says the traction, the
            # contact and the reaction all agree (2026-09-07).
            "torque_reaction_nm": (react_nm if use_torque else None),
            "torque_balance": balance,
            "torque_path": torque_path,
        }
        if with_field:
            # Rounded on float64 so the JSON carries "115.923" and not the
            # 17-digit expansion of a float32 — a third of the payload size.
            fields[case] = {
                "vm_per_tri": np.round(vm * MPA, 3).tolist(),
                "s_hoop_per_tri": np.round(stt * MPA, 3).tolist(),
                "s_rad_per_tri": np.round(srr * MPA, 3).tolist(),
                "s_p1_per_tri": np.round(p1 * MPA, 3).tolist(),
                # Per-element safety factor, already divided by the right
                # strength for the part the element belongs to.
                "sf_per_tri": np.round(sf_tri, 3).tolist(),
                "u_per_node": np.round(u * 1e6, 3).tolist(),
                "u_mag_per_node": np.round(umag * 1e6, 3).tolist(),
                "contact_pressure_per_pair": cpress,
                "contact_open_per_pair": copen,
            }

    # (The fit AT TEMPERATURE — `interference_effective_mm`, `thermal_fit` — is
    # measured BEFORE the solve since 2026-09-09, because under the band_fit
    # model it is what the solve carries; see the thermal block above.)

    # ── lift-off speed: a short bisection, because contact is nonlinear ─────
    # v1 had a closed form (the interference part constant, the centrifugal part
    # scaling with omega^2).  Superposition is gone with a unilateral contact,
    # so the crossing is bracketed by the cases already solved and then bisected
    # with a hard budget of ``lift_off_solves`` extra solves.
    lift_off: Dict[str, Any] = {}
    budget = max(int(lift_off_solves), 0)

    def _of(k: float, label: str) -> float:
        nonlocal budget
        if k not in of_cache:
            if budget <= 0:
                return float("nan")
            budget -= 1
            of_cache[k] = _open_fracs(
                _run(k, warm, f"lift-off search: {label}"))
        return of_cache[k].get(label, 0.0)

    k_over = overspeed_factor ** 2
    for it in cs.interfaces:
        label = it.label
        if it.n_facets == 0 or rpm <= 0:
            lift_off[label] = None
            continue
        if not it.unilateral:
            # A bonded/sliding tie never opens — report the speed at which it
            # starts holding TENSION, which is the same warning v1 gave.
            # Over whatever cases were actually solved: in single-speed mode
            # that is one, and quoting a speed nobody solved would be a made-up
            # number (2026-09-06).
            tens = [k_ for cn, k_ in cases.items()
                    if results[cn]["interfaces"][label]["lift_off"]]
            lift_off[label] = (rpm * math.sqrt(min(tens)) if tens else None)
            continue
        if case_mode == "single":
            # Only k = 1 has been solved.  If the joint is still shut at the one
            # speed we were asked about, that is the honest answer — "not up to
            # this speed" — and nothing above it may be claimed.  If it HAS
            # opened, bracket downwards: k = 0 costs one of the budgeted solves,
            # and it is only spent on a joint that has actually let go.
            f1 = of_cache[1.0][label]
            if f1 <= open_verdict:
                lift_off[label] = None
                continue
            f0 = _of(0.0, label)
            if not np.isfinite(f0):
                lift_off[label] = None       # no budget left to bracket it
                continue
            if f0 > open_verdict:
                lift_off[label] = 0.0
                continue
            lo, hi = 0.0, 1.0
        else:
            f0, f1, f2 = (of_cache[0.0][label], of_cache[1.0][label],
                          of_cache[k_over][label])
            if f0 > open_verdict:
                lift_off[label] = 0.0
                continue
            lo, hi = (0.0, 1.0) if f1 > open_verdict else (
                (1.0, k_over) if f2 > open_verdict else (None, None))
            if lo is None:
                lift_off[label] = None       # beyond overspeed
                continue
        for _ in range(4):
            mid = 0.5 * (lo + hi)
            v = _of(mid, label)
            if not np.isfinite(v):
                break
            if v > open_verdict:
                hi = mid
            else:
                lo = mid
        lift_off[label] = rpm * math.sqrt(hi)

    # ── the one number a sector does NOT read the same way (2026-09-09) ──────
    # `rotor_od_growth_um` is the largest radial growth of the nodes within
    # 10 µm of the OUTERMOST radius the mesh happens to have.  With a sleeve
    # that radius is the band's bore and both models read the same ring.  With
    # no sleeve it is the mesh's own maximum, and on a SAMPLED outline that is a
    # lottery: measured on the G2-L40, four of the twenty-eight poles carry an
    # outline vertex 11 µm outside the nominal 73.35 mm, so the full model reads
    # its growth on those four spots (4.07 µm) while the sector reads the whole
    # pole top (4.52 µm).  Neither number is wrong about the field — over the
    # outer 50 µm the two agree to 0.15 % (4.526 against 4.519) — but the
    # comparison is not like for like, and saying so beats leaving an 11 % gap
    # for the reader to discover.
    symmetry_notes: List[str] = []
    if sector_plan is not None and not has_sleeve:
        symmetry_notes.append(
            "rotor_od_growth_um is read on the nodes within 10 µm of the mesh's "
            "outermost radius. This machine has no sleeve, so that radius comes "
            "from the sampled rotor outline and the sector's band need not sit "
            "at the same spot as the full model's — expect a few per cent "
            "between the two on this field alone; the stresses, the contact and "
            "the masses are like for like.")

    # Whatever the bisection did not spend is not work anybody is waiting for:
    # most rotors never open a joint at all and use none of it, and a bar that
    # stopped at 62 % on a finished solve is the bug this whole mechanism exists
    # to remove.
    # Lift-off solves were never pre-budgeted (each one grew the bar as it
    # ran), so nothing is left to hand back here; the bar sits at its end.
    led.give_back(max(0, led.total - led.done))
    led.phase("post-processing (report)")

    out: Dict[str, Any] = {
        "rpm": rpm,
        # Which case table this is, so a reader never has to guess whether the
        # single key in `cases` is a speed or a missing standstill.
        "case_mode": case_mode,
        "primary_case": primary_case,
        "overspeed_factor": overspeed_factor,
        "overspeed_rpm": rpm * overspeed_factor,
        "interference_mm": interference_mm,
        # The fit the sleeve ACTUALLY feels once the rotor is hot (2026-09-07):
        # the geometric oversize plus the CTE mismatch under the band.  Equal to
        # `interference_mm` whenever nothing is heated.
        "interference_effective_mm": interference_effective_mm,
        "has_sleeve": has_sleeve,
        "stack_length_mm": float(stack_length_mm or 0.0),
        # ── the rotor temperature (2026-09-07) ──────────────────────────────
        # User: "нужно универсально добавить температуру ротора, чтобы можно
        # было задавать".  `active` is False for a 20/20 °C request, which is
        # the machine as drawn and as every earlier answer solved it.
        "thermal": {
            "rotor_temp_c": rotor_temp_c,
            "sleeve_temp_c": sleeve_temp_c,
            "ref_temp_c": ref_temp_c,
            # The temperature EVERY solid was actually solved at, keyed the way
            # the materials map keys the same parts (2026-09-08).  Always all
            # four, whether they were named or fell back, so a reader never has
            # to re-derive the fallback rule to know what was applied — this is
            # the line the panel and the Compare row print.
            "part_temps_c": {k: part_temp_c[n] for k, n in PART_TEMP_KEY.items()},
            # …and only the ones the CALLER named, so "coupled to Thermal" and
            # "two fields typed by hand" are distinguishable after the fact.
            "part_temps_given": {k: float(v) for k, v in sorted(given_temps.items())},
            # `active`: the temperatures CHANGED the answer.  Under the default
            # band_fit model that means "there is a band and its fit moved";
            # a sleeveless rotor at 150 °C is `active: False` — see `applied_as`
            # and `notes` for the sentence (2026-09-09).
            "active": bool(thermal_active),
            "model": thermal_model,
            "applied_as": thermal_applied_as,
            "parts": thermal_parts,
            "fit": thermal_fit or None,
            "notes": thermal_notes,
        },
        # Also at the top level: a material with no CTE is a caveat on the whole
        # answer, not a detail of one part, and the panel prints it as one line.
        "thermal_notes": thermal_notes,
        # Which forces acted, and the torque that did (2026-09-07).
        "loads": loads,
        "torque_nm": (float(torque_nm) if use_torque else 0.0),
        "torque_load": torque_report,
        # ── which cross-section this answer is about (2026-09-09) ───────────
        # Always present, so a reader never has to infer it: `mode: "full"` is
        # the whole rotor and every number is the machine's directly.
        "symmetry": ({"mode": "full", "n_sectors": 1, "angle_deg": 360.0}
                     if sector_plan is None else
                     {**sector_plan.as_dict(),
                      "match_error_um": (float(getattr(cyclic_ties,
                                                       "match_error_m", 0.0))
                                         * 1e6),
                      "n_tied_points": (int(cyclic_ties.dofs_a.shape[0])
                                        if cyclic_ties is not None else 0),
                      "n_rows_dropped": int(cs.pair_no_row.sum()),
                      # What was multiplied by n on the way out, so nobody has
                      # to guess whether a number is the wedge's or the
                      # machine's.  Everything not listed is intensive and is
                      # the same in both models.
                      "scaled": ["parts.*.mass_kg", "interfaces.*.length_mm",
                                 "interfaces.*.torque_transmitted_nm",
                                 "interfaces.*.friction_capacity_nm",
                                 "interfaces.*.radial_force_kn_per_m",
                                 "interfaces.*.tangential_force_kn_per_m",
                                 "torque_reaction_nm",
                                 "magnet_retention.magnet_centrifugal_kn_per_m",
                                 "magnet_retention.carried_kn_per_m.*",
                                 "torque_load.surface_mm",
                                 "torque_load.coverage"],
                      "torque_sector_nm": (torque_sector_nm if use_torque
                                           else 0.0),
                      "notes": symmetry_notes}),
        "mesh": {"n_nodes": int(smesh.p.shape[1]), "n_triangles": int(ne),
                 "element_order": int(order), "mesh_size_mm": float(mesh_size_mm),
                 # The SECTOR's own mesh when one was solved — this is the
                 # matrix that was factorised, and the reason a sector solve is
                 # n times cheaper.
                 "n_sectors": int(n_sym),
                 # What this mesh cost to build, and whether THIS solve paid it:
                 # a Build mesh followed by a Solve must not mesh twice, and the
                 # panel says which of the two happened (user 2026-09-06).
                 "mesh_s": float(rm.build_s),
                 "mesh_reused": bool(rm.from_memo),
                 "n_contact_pairs": int(cs.n_pairs),
                 "n_contact_facets": int(cs.n_facets),
                 # |R^T f| / |f| of the load: how far it is from
                 # self-equilibrated.  ~1e-3 or below on a real rotor.
                 # The residual of the case the report is ABOUT: "rated" when
                 # three were solved, the single speed when one was.
                 "rigid_residual": float(sols[primary_case].rigid_residual)},
        "contacts": {it.label: {**it.spec.as_dict(), "n_facets": it.n_facets}
                     for it in cs.interfaces},
        "materials": {n: {"material": pm.material,
                          "density": pm.density,
                          "youngs_modulus_gpa": pm.E / 1e9,
                          "youngs_modulus_transverse_gpa":
                              (pm.E_transverse / 1e9 if pm.E_transverse else None),
                          "shear_modulus_gpa": (pm.G / 1e9 if pm.G else None),
                          "poisson_ratio": pm.nu,
                          # Thermal expansion, ppm/K, axis 1 = the same axis E1
                          # is quoted in (2026-09-07).  Both None when the card
                          # carries none — see `thermal_notes`.
                          "cte_ppm_k_1": (pm.cte_1 * 1e6
                                          if pm.cte_source == "card" else None),
                          "cte_ppm_k_2": (pm.cte_pair()[1] * 1e6
                                          if pm.cte_source == "card" else None),
                          "cte_anisotropic": bool(pm.cte_anisotropic),
                          "strength_mpa": pm.strength * MPA,
                          "strength_kind": pm.strength_kind,
                          "compressive_strength_mpa":
                              (pm.compressive_strength * MPA
                               if pm.compressive_strength else None),
                          "tensile_strength_transverse_mpa":
                              (pm.strength_transverse * MPA
                               if pm.strength_transverse else None),
                          # The sentence the safety-factor map is drawn from.
                          "sf_criterion": sf_criterion_text(pm, n),
                          "orthotropic": pm.orthotropic,
                          "note": pm.source_note}
                      for n, pm in mech.items()},
        "cases": results,
        "lift_off_rpm": lift_off,
    }
    if with_field:
        ext = np.abs(ps * 1e3).max()
        out["field"] = {
            # The SPLIT mesh: the interface nodes are duplicated, so the
            # triangles of two parts no longer share a vertex and the map can
            # draw the two sides of an open contact apart.
            "vertices": np.round(ps * 1e3, 5).astype(np.float32).tolist(),
            "triangles": smesh.t.T.astype(np.int32).tolist(),
            "domain_per_tri": part_tri.astype(np.int8).tolist(),
            "part_names": PART_NAMES,
            "outlines": rm.outlines,
            "extent": float(ext),
            "n_sectors": 1,
            "symmetry_mult": 1,
            # One line segment per contact facet, in mm, so the map can paint
            # the open stretches red and the closed ones green.
            "contact_segments_per_pair": {
                it.label: np.round(it.seg * 1e3, 4).reshape(-1, 4).tolist()
                for it in cs.interfaces if it.n_facets},
            "cases": fields,
        }
        if n_sym > 1:
            out["field"] = _replicate_field(out["field"], n_sym)
    return out


def _replicate_field(fld: Dict[str, Any], n: int) -> Dict[str, Any]:
    """The sector's field payload, turned into the whole rotor's.

    The map component draws whatever it is handed, so a sector answer would
    otherwise show one wedge floating in the corner of the canvas.  The
    replication is EXPLICIT and flagged (``replicated_from_sector``) rather than
    quietly done, and the sector's own mesh is kept under ``sector`` — it is
    what was solved, and it is what anybody checking the periodicity wants.

    What is rotated and what is tiled is the physics of the thing:

      * VERTICES and OUTLINES are points — turned by ``k * 2*pi/n``;
      * ``u_per_node`` is a VECTOR — turned by the same rotation, which is the
        cyclic-symmetry statement ``u(Rx) = R u(x)`` applied to a whole copy;
      * ``u_mag_per_node`` and every per-element stress (von Mises, hoop,
        radial, first principal, the safety factor) are INVARIANTS — tiled
        unchanged.  That every sector then reads the identical number is not an
        approximation of the answer, it IS the answer: the user asked for a
        model in which "нагрузка на все зубы должна быть одинакова".
    """
    from motor_ai_sim.simulation.mechanical import symmetry as sym

    v = np.asarray(fld["vertices"], dtype=float)
    t = np.asarray(fld["triangles"], dtype=np.int64)
    nv = v.shape[0]
    out = dict(fld)
    out["sector"] = {
        "vertices": fld["vertices"],
        "triangles": fld["triangles"],
        "domain_per_tri": fld["domain_per_tri"],
        "n_vertices": int(nv),
        "n_triangles": int(t.shape[0]),
    }
    vr = sym.replicate_vertices(v, n)
    out["vertices"] = np.round(vr, 5).astype(np.float32).tolist()
    # The canvas scales on `extent`, and a wedge's own bounding box is not the
    # rotor's: a sector lying across the diagonal reaches only R/sqrt(2) in each
    # coordinate while the machine reaches R.  Re-measured on the replicated
    # copy, which is what is being drawn.
    out["extent"] = float(np.abs(vr).max())
    out["triangles"] = sym.replicate_triangles(t, nv, n).astype(np.int32).tolist()
    out["domain_per_tri"] = list(fld["domain_per_tri"]) * n
    outlines = []
    for k in range(n):
        th = 2.0 * math.pi * k / n
        c, s = math.cos(th), math.sin(th)
        for o in fld["outlines"]:
            a = np.asarray(o, dtype=float)
            outlines.append([[float(c * x - s * y), float(s * x + c * y)]
                             for x, y in a])
    out["outlines"] = outlines
    out["n_sectors"] = int(n)
    out["symmetry_mult"] = int(n)
    out["replicated_from_sector"] = True
    out["contact_segments_per_pair"] = {
        lb: sym.replicate_vertices(
            np.asarray(seg, dtype=float).reshape(-1, 2), n
        ).reshape(-1, 4).round(4).tolist()
        for lb, seg in (fld.get("contact_segments_per_pair") or {}).items()}
    cases = {}
    for cname, cf in (fld.get("cases") or {}).items():
        nc = dict(cf)
        for key in ("vm_per_tri", "s_hoop_per_tri", "s_rad_per_tri",
                    "s_p1_per_tri", "sf_per_tri", "u_mag_per_node"):
            if key in cf:
                nc[key] = sym.replicate_scalars(cf[key], n)
        if "u_per_node" in cf:
            nc["u_per_node"] = np.round(
                sym.replicate_vectors(np.asarray(cf["u_per_node"], dtype=float),
                                      n), 3).tolist()
        for key in ("contact_pressure_per_pair", "contact_open_per_pair"):
            if key in cf:
                nc[key] = {lb: sym.replicate_scalars(vals, n)
                           for lb, vals in (cf[key] or {}).items()}
        cases[cname] = nc
    out["cases"] = cases
    return out


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
# Keyed by (geometry fingerprint, material assignment, rpm, overspeed,
# interference) so a Solve press that changes nothing is instant, and any Run on
# the Simulation tab (which calls clear_simulation_caches) drops it — the
# geometry it was computed on may no longer be the geometry on screen.

_CACHE: Dict[Any, Dict[str, Any]] = {}
_CACHE_MAX = 8


def cache_get(key) -> Optional[Dict[str, Any]]:
    return _CACHE.get(key)


def cache_put(key, value: Dict[str, Any]) -> None:
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)), None)
    _CACHE[key] = value


def clear_cache() -> int:
    n = len(_CACHE)
    _CACHE.clear()
    # The mesh memo keys on the geometry itself, so it can never serve the wrong
    # cross-section — this is about memory, not correctness.
    n += clear_mesh_memo()
    return n
