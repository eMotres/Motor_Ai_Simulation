"""Cooling-system correlations — one surface at a time, as pure functions.

WHY THIS MODULE EXISTS
======================
Until 2026-09-07 the whole cooling model was a single ``_cooling_bc`` buried in
``routes.thermal``: it knew one surface (the housing), it could not be unit
tested without a FastAPI app, and its liquid branch was INVERTED — the engineer
typed the outlet temperature and the model handed back the flow rate it would
take.  That is backwards for anybody with a pump: you know the pump's L/min and
the inlet temperature, and the outlet is what the machine DOES to the coolant.

The user's ask (2026-09-07) is two cooled surfaces, not one:

    "Ротор придётся охлаждать в основном через вал" — the rotor's heat leaves
    mainly through the SHAFT, not across the air gap.

So the model needs the rotor bore as a first-class cooled surface with its own
fluid, its own flow and its own outlet temperature, and the caller needs to be
told how many watts left through each.  That is three correlation families
(external cross-flow, internal pipe flow, an annular jacket channel) plus an
energy balance, and none of them has any business being inside a route.

Later the same day the user named the ONE axial path that is real (*"торцы и
лобовые части — только для вала, всё остальное вращается внутри мотора"*): the
rotor's end faces and the end windings spin inside a closed housing and have
nowhere else to send their heat, but the SHAFT comes out through the bearings
and its exposed length loses heat to the room.  That is a fourth family — a
cylinder spinning in still air — and a FIN, because a 100 mm stub does not
remove five times what a 20 mm one does.  See ``shaft_ends_path``.

THE MACHINE THAT IS NOT COOLED AT ALL (2026-09-14)
--------------------------------------------------
A robot joint has no fan, no jacket and no slipstream: it is bolted to an arm and
it sits in a room.  Until now the nearest mode was ``outer_air`` at v = 0, which
answers with the flat ``NATURAL_CONVECTION_H`` = 7 W/m²·K floor — one number for
every machine, every ΔT and every surface finish.  Four things replace it, and
the first two are comparable in size on a small machine:

  * ``outer_still`` — Churchill–Chu natural convection on the housing PLUS
    ``radiation_h``.  On the Ø85 joint at ΔT 60 K over 40 °C air that is
    h_conv ≈ 6 and h_rad ≈ 8.3: more than half of what leaves the housing leaves
    it as light, so the housing's emissivity is a design input, not a detail;
  * ``end_face_still`` — the same pair on a FLAT face, returned as a lumped
    conductance for ``volume_sinks``, because this machine's coils stand proud of
    the core on both sides and its end faces are largely uncovered (the 2026-09-07
    "everything but the shaft turns inside the housing" ruling does not hold
    here).  ``end_winding_area`` is the geometry, shared with the open frame's
    forced-air path below so the two cannot disagree about how much copper is out
    there;
  * ``bore_still`` — the same again down an unventilated bore, with Nu floored at
    conduction across the hole;
  * ``mount_path`` — the bolted flange, which on this machine is not one of four
    paths but THE path: the housing hands the room ~3 W of the 64 W the joint
    makes, and everything else goes out through the bolts.

Every one of those coefficients depends on the WALL temperature, which no other
film in this module does — h ∝ ΔT^(1/4) and radiation re-linearises about the
wall — so the caller iterates them against the conduction solve, reading each
surface's own ``t_mean_c`` back out of ``thermal_solver_2d`` (added the same day
for exactly this) instead of guessing the wall from the global maximum.

EVERYTHING HERE IS PURE
-----------------------
No config, no materials library, no FEM.  Fluid properties arrive as a
``FluidProps`` tuple, geometry as metres, and every function returns numbers (or
a plain reportable dict).  That is what makes ``tests/test_thermal_cooling_models.py``
able to assert monotonicity in flow, the laminar floor and the energy balance in
milliseconds instead of minutes.

THE ENERGY BALANCE IS A FIXED POINT
-----------------------------------
A coolant that carries P watts leaves hotter than it arrived::

    T_out = T_in + P / (ṁ · cp)

and the Robin sink the conduction solve should use is the MEAN film temperature
(T_in + T_out)/2 — but P is not known until the conduction solve has run, and
the conduction solve needs the sink.  The caller therefore iterates: guess P,
solve, read the surface's actual facet-integrated heat, update T_out, re-solve.
These functions are the "given P, what is the boundary condition" half of that
loop; they are deliberately stateless so the loop lives with the solver.
"""
from __future__ import annotations

import math
from typing import Any, Dict, NamedTuple, Optional, Tuple


class FluidProps(NamedTuple):
    """(rho kg/m³, cp J/kg·K, k W/m·K, nu m²/s, Pr) — the five numbers every
    convection correlation in this module needs, in the order the materials
    library's ``CoolantMaterial.props_tuple`` already serves them."""
    rho: float
    cp: float
    k: float
    nu: float
    pr: float


# ---------------------------------------------------------------------------
# Documented defaults — every one of these is a number an engineer may want to
# argue with, so it is named, commented and reported in the response rather than
# hidden inside an expression.
# ---------------------------------------------------------------------------

#: Height of the annular jacket channel wrapped round the housing [m].  A
#: water-jacket milled into a motor housing is typically 3–8 mm deep; 5 mm is the
#: middle of that band.  It only enters through the hydraulic diameter and the
#: channel velocity, both of which are reported, so a machine with a real jacket
#: drawing can be checked against it.
JACKET_CHANNEL_H_M = 0.005
#: Helical jacket groove width [m] — with the depth above, the one cross-section
#: the whole flow passes through.  A design input in waiting (no geometry field
#: for it yet); 10 × 5 mm is a common machined groove.
JACKET_CHANNEL_W_M = 0.010

#: Still-air natural-convection floor on a horizontal cylinder [W/m²·K].  A
#: housing in a closed cabinet still loses heat; a forced-convection correlation
#: evaluated at v = 0 does not know that and returns ~0.
#:
#: IT IS A FLOOR FOR CORRELATIONS THAT COLLAPSE, not a model.  ``outer_still``
#: (2026-09-14) does not use it: Churchill–Chu IS the still-air correlation, it
#: does not collapse at v = 0, and flooring it with this number would double-count
#: the very mechanism the number stands for.  See that function's docstring — at
#: ΔT → 0 the two agree to ~10 %, which is the check that the constant and the
#: model are talking about the same housing.
NATURAL_CONVECTION_H = 7.0

#: Stefan–Boltzmann constant σ [W/m²·K⁴] — the 2019 SI definition, exact.
STEFAN_BOLTZMANN = 5.670374419e-8

#: Total hemispherical emissivity of a motor housing, used when the caller does
#: not state one.  0.9 is the band anodised aluminium, painted steel, cast iron
#: and bare machined-and-oxidised surfaces all sit in (0.85–0.95); the outlier is
#: BARE POLISHED aluminium at ~0.05, which radiates essentially nothing — which
#: is why this is an input with a default and not a constant.  On a small housing
#: in still air radiation is not a correction: at ΔT 60 K it carries MORE heat
#: than the convection does (h_rad ≈ 8.3 vs h_conv ≈ 6 on an Ø85 mm cylinder), so
#: a polished housing is a genuinely different machine.
EMISSIVITY_DEFAULT = 0.9

#: Through-thickness (radial) conductivity of a UD CFRP retaining sleeve at
#: ~60 % fibre volume [W/m·K], used ONLY when the assigned sleeve material has no
#: ``thermal_conductivity`` field.  Matrix-dominated and contact-limited: this is
#: the number that makes a sleeve a thermal blanket on the rotor.
SLEEVE_K_RADIAL_DEFAULT = 0.7

#: In-plane (along-fibre = hoop) conductivity of the same laminate [W/m·K].
#: Roughly an order of magnitude above the radial value — heat spreads AROUND the
#: rotor far more easily than it gets OUT of it, which is the whole reason the
#: sleeve has to be modelled as a tensor and not as an average.
SLEEVE_K_FIBRE_DEFAULT = 5.0

# Dry air at 1 atm, referenced to 273.15 K.  Power laws rather than a table
# because the gap temperature is a continuously varying model output; the
# exponents are the standard kinetic-theory ones and they reproduce the
# materials library's own 300 K air card to better than 1 % (k 0.0262 vs 0.0263,
# nu 1.563e-5 vs 1.56e-5) — i.e. this is the library's air, extrapolated, not a
# second opinion about air.
_AIR_K_REF = 0.0242      # W/m·K at 273.15 K
_AIR_NU_REF = 1.33e-5    # m²/s  at 273.15 K, 1 atm
_AIR_RHO_REF = 1.293     # kg/m³ at 273.15 K, 1 atm
_AIR_CP = 1007.0         # J/kg·K (near-constant over the range that matters)
_AIR_PR = 0.707
_T0_K = 273.15


def air_properties(t_c: float) -> FluidProps:
    """Dry air at 1 atm and ``t_c`` °C.

    The air-gap conductivity and every air-cooled surface must state the
    temperature their properties were evaluated at — k rises ~13 % and nu ~30 %
    between 25 °C and 75 °C, which is the difference between a gap that conducts
    and one that does not.  Clamped at 100 K so a nonsense input cannot produce a
    negative viscosity.
    """
    tk = max(float(t_c) + 273.15, 100.0)
    ratio = tk / _T0_K
    return FluidProps(rho=_AIR_RHO_REF / ratio,
                      cp=_AIR_CP,
                      k=_AIR_K_REF * ratio ** 0.83,
                      nu=_AIR_NU_REF * ratio ** 1.75,
                      pr=_AIR_PR)


# ---------------------------------------------------------------------------
# The three correlation families
# ---------------------------------------------------------------------------

def churchill_bernstein_nu(re: float, pr: float) -> float:
    """Nusselt number for a CYLINDER IN CROSS-FLOW (Churchill–Bernstein, 1977).

    Valid over the whole practical range Re·Pr > 0.2, which is why it is used
    here in preference to the piecewise Hilpert/Zukauskas tables: a housing in a
    fan draught and the same housing in a 40 m/s slipstream must come out of one
    expression, or the h(v) curve has steps in it that no fan can produce.
    """
    re = max(float(re), 0.0)
    pr = max(float(pr), 1e-6)
    if re <= 1.0:
        return 0.3
    return (0.3 + (0.62 * re ** 0.5 * pr ** (1.0 / 3.0))
            / (1.0 + (0.4 / pr) ** (2.0 / 3.0)) ** 0.25
            * (1.0 + (re / 282000.0) ** (5.0 / 8.0)) ** (4.0 / 5.0))


def churchill_chu_nu(ra: float, pr: float) -> float:
    """Nusselt number for a HORIZONTAL CYLINDER IN STILL AIR (Churchill–Chu, 1975).

    The natural-convection twin of ``churchill_bernstein_nu``: there the air is
    blown across the cylinder and the driving group is the Reynolds number, here
    nothing blows and the cylinder's own buoyant plume does the work, so the
    driving group is the Rayleigh number::

        Nu = { 0.60 + 0.387·Ra^(1/6) / [1 + (0.559/Pr)^(9/16)]^(8/27) }²

    One expression over 10⁻⁵ < Ra < 10¹², laminar plume and turbulent alike, for
    the same reason the cross-flow family is one expression: a housing does not
    know where a correlation table's band edge is, and a step in h(ΔT) is a
    machine that gets cooler when it gets hotter.

    THE 0.60 IS THE QUIESCENT LIMIT AND IT IS NOT A FLOOR BOLTED ON.  At Ra → 0
    the expression tends to Nu = 0.36, i.e. h = 0.36·k/D — conduction from a
    cylinder into the unbounded air around it, which is what a housing at ambient
    temperature actually exchanges.  That is why ``outer_still`` needs no
    ``NATURAL_CONVECTION_H`` floor: unlike a forced correlation evaluated at
    v = 0, this one does not go to zero and so does not lie.

    Above Ra ≈ 10¹² the correlation is an extrapolation; the caller reports Ra so
    that is visible rather than implied.  (Ra on a Ø85 mm housing at ΔT 60 K is
    1.9·10⁶ — the middle of the measured band.)
    """
    ra = max(float(ra), 0.0)
    pr = max(float(pr), 1e-6)
    denom = (1.0 + (0.559 / pr) ** (9.0 / 16.0)) ** (8.0 / 27.0)
    return (0.60 + 0.387 * ra ** (1.0 / 6.0) / denom) ** 2


def radiation_h(t_wall_c: float, t_env_c: float,
                eps: float = EMISSIVITY_DEFAULT) -> float:
    """LINEARISED radiation coefficient [W/m²·K] between a wall and its room.

        h_rad = ε·σ·(T_w² + T_∞²)·(T_w + T_∞)          [T in KELVIN]

    which is an ALGEBRAIC IDENTITY, not an approximation::

        ε·σ·(T_w⁴ − T_∞⁴) = h_rad · (T_w − T_∞)

    so the linearised film carries exactly the watts the fourth-power law does,
    at any ΔT, and it can be added straight to a convective h and handed to a
    Robin boundary condition — which is the whole point: a conduction solve takes
    h·(T − T_sink), it cannot take T⁴.  What IS approximate is the h staying
    constant while the wall temperature moves, so the caller re-evaluates it as
    the wall converges (see ``outer_still``).

    THREE STATED APPROXIMATIONS, all of them the caller's to argue with:

      * the radiation environment is the AIR temperature.  A machine in a room
        radiates to walls near room temperature and this is right to a kelvin or
        two; a machine facing a hot manifold, or the sky, does not;
      * the view factor is 1 — the housing sees nothing but that environment.  A
        housing bolted inside a tight enclosure sees its own reflection and
        exchanges less;
      * ε is the TOTAL HEMISPHERICAL emissivity, one number for the whole
        surface (see ``EMISSIVITY_DEFAULT``).

    ``eps = 0`` returns exactly zero: a polished-aluminium housing radiates
    nothing worth counting, and the model must be able to say so rather than
    approach it.
    """
    e = min(max(float(eps), 0.0), 1.0)
    if e <= 0.0:
        return 0.0
    tw = float(t_wall_c) + _T0_K
    te = float(t_env_c) + _T0_K
    return e * STEFAN_BOLTZMANN * (tw * tw + te * te) * (tw + te)


def pipe_nusselt(re: float, pr: float) -> Tuple[float, str]:
    """(Nu, regime) for fully developed flow INSIDE a duct — the model behind
    both the jacket channel and the bore.

      * Re < 2300 — laminar.  Nu = 3.66, the constant-wall-temperature
        analytical value.  It is a FLOOR, not a correlation: a laminar duct's
        heat transfer does not fall to zero as the flow slows, it asymptotes
        here, and a turbulent correlation extrapolated down to Re = 200 would
        say it does.
      * 2300 ≤ Re < 10⁴ — transitional.  Gnielinski with the Petukhov friction
        factor, which is the only one of the three that is honest in this band
        (Dittus–Boelter is calibrated above 10⁴ and over-reads here).  Gnielinski
        itself is only validated from Re ≈ 3000, and at 2300 it lands ~4× above
        the laminar value — a genuine cliff, and one that puts a step in the h(Q)
        curve no pump can produce.  So the 2300–3000 strip is a linear BLEND from
        the laminar 3.66 to Gnielinski(3000): it is a bridge across a band where
        neither correlation is trustworthy anyway, and it keeps the ladder
        monotone, which is the property an engineer actually reads off it.
      * Re ≥ 10⁴ — Dittus–Boelter, heating exponent n = 0.4.  This seam needs no
        bridge: at Re = 10⁴, Pr = 7 the two agree to better than 0.2 %.

    Every branch is floored at the laminar 3.66: a duct's heat transfer
    asymptotes there, it does not fall through it.
    """
    re = max(float(re), 0.0)
    pr = max(float(pr), 1e-6)

    def _gnielinski(r):
        f = (0.79 * math.log(r) - 1.64) ** -2.0
        return ((f / 8.0) * (r - 1000.0) * pr
                / (1.0 + 12.7 * math.sqrt(f / 8.0) * (pr ** (2.0 / 3.0) - 1.0)))

    if re < 2300.0:
        return 3.66, "laminar"
    if re < 3000.0:
        w = (re - 2300.0) / 700.0
        return max((1.0 - w) * 3.66 + w * _gnielinski(3000.0), 3.66), "transitional"
    if re < 1.0e4:
        return max(_gnielinski(re), 3.66), "transitional"
    return max(0.023 * re ** 0.8 * pr ** 0.4, 3.66), "turbulent"


def hydraulic_diameter_slot(width_m: float, height_m: float) -> float:
    """D_h = 4A/P of a rectangular channel of ``width`` × ``height``.

    For a jacket the width is the housing circumference and the height the
    channel depth, so this collapses to ~2·height — but it is written out
    because a SMALL machine's jacket is not a wide slot (a 30 mm housing is
    94 mm round and 5 mm deep) and the 2·h approximation would be 5 % out there.
    """
    w = max(float(width_m), 1e-6)
    h = max(float(height_m), 1e-6)
    return 4.0 * (w * h) / (2.0 * (w + h))


def outlet_temperature(t_in_c: float, heat_w: float, m_dot_kg_s: float,
                       cp: float) -> float:
    """T_out = T_in + P/(ṁ·cp) — the first law on the coolant stream.

    A zero (or absurdly small) mass flow would send this to infinity, which is
    physically right and numerically useless: the caller has already refused a
    liquid surface with flow ≤ 0, so the guard here is for the air-through-bore
    case at v → 0, where "no flow" means "no cooling", i.e. the surface should
    simply stop removing heat rather than report a 10⁹ °C outlet.
    """
    mc = float(m_dot_kg_s) * float(cp)
    if mc <= 1e-9:
        return float(t_in_c)
    return float(t_in_c) + float(heat_w) / mc


# ---------------------------------------------------------------------------
# Surfaces — each returns the reportable dict the response carries verbatim
# ---------------------------------------------------------------------------
# One shape for every mode, so a client can render the outer and the bore panel
# with one component and a surface that is switched off is not a missing key:
#
#   {mode, h_conv, t_sink_c, t_in_c, t_out_c, flow_lpm, air_speed_mps,
#    re, nu, area_m2, heat_removed_W, note}

_SURFACE_KEYS = ("mode", "h_conv", "t_sink_c", "t_in_c", "t_out_c", "flow_lpm",
                 "air_speed_mps", "re", "nu", "area_m2", "heat_removed_W",
                 "note")


def _surface(mode: str, *, h_conv: float = 0.0, t_sink_c: float = 0.0,
             t_in_c: Optional[float] = None, t_out_c: Optional[float] = None,
             flow_lpm: Optional[float] = None,
             air_speed_mps: Optional[float] = None,
             re: Optional[float] = None, nu: Optional[float] = None,
             area_m2: float = 0.0, heat_removed_W: float = 0.0,
             note: str = "", **extra: Any) -> Dict[str, Any]:
    """Assemble one surface report with every documented key present."""
    out: Dict[str, Any] = {
        "mode": mode,
        "h_conv": round(float(h_conv), 2),
        "t_sink_c": round(float(t_sink_c), 2),
        "t_in_c": (None if t_in_c is None else round(float(t_in_c), 2)),
        "t_out_c": (None if t_out_c is None else round(float(t_out_c), 2)),
        "flow_lpm": (None if flow_lpm is None else round(float(flow_lpm), 3)),
        "air_speed_mps": (None if air_speed_mps is None
                          else round(float(air_speed_mps), 3)),
        "re": (None if re is None else round(float(re), 0)),
        "nu": (None if nu is None else round(float(nu), 2)),
        "area_m2": round(float(area_m2), 6),
        "heat_removed_W": round(float(heat_removed_W), 2),
        "note": note,
    }
    out.update(extra)
    return out


def surface_off(name: str = "surface") -> Dict[str, Any]:
    """A surface that is not cooled — adiabatic, and SAID so.

    Not ``None``: "there is no bore cooling" and "the bore key is missing
    because something went wrong" are different statements, and only a real
    payload can tell them apart.
    """
    return _surface("none", note=f"{name} is adiabatic — no cooling on this "
                                 f"surface (h = 0)")


def outer_manual(*, h_conv: float, t_ambient_c: float,
                 area_m2: float = 0.0,
                 heat_w: float = 0.0) -> Dict[str, Any]:
    """The caller's own h at ambient — the legacy contract, kept verbatim.

    Still the right mode for "I measured h on the bench" and for reproducing
    every result solved before the cooling models existed.
    """
    return _surface("manual", h_conv=h_conv, t_sink_c=t_ambient_c,
                    area_m2=area_m2, heat_removed_W=heat_w,
                    note="h supplied by the caller; sink held at ambient")


def outer_air(*, air_speed_mps: float, t_ambient_c: float, d_housing_m: float,
              area_m2: float = 0.0, heat_w: float = 0.0,
              props: Optional[FluidProps] = None) -> Dict[str, Any]:
    """Housing blown at ``air_speed_mps`` — a cylinder in cross-flow.

    The sink stays at AMBIENT and does not iterate: the air outside the machine
    is an unbounded reservoir, so unlike a duct it does not measurably heat up as
    it carries the losses away.  Properties are evaluated at the ambient
    temperature (film-temperature refinement is inside the correlation's own
    scatter here).
    """
    p = props or air_properties(t_ambient_c)
    d = max(float(d_housing_m), 1e-4)
    v = max(float(air_speed_mps), 0.0)
    re = v * d / p.nu if v > 0.0 else 0.0
    nu = churchill_bernstein_nu(re, p.pr) if re > 1.0 else 0.0
    h_forced = nu * p.k / d if nu else 0.0
    h = max(h_forced, NATURAL_CONVECTION_H)
    forced = h_forced > NATURAL_CONVECTION_H
    return _surface("air", h_conv=h, t_sink_c=t_ambient_c,
                    t_in_c=t_ambient_c, t_out_c=t_ambient_c,
                    air_speed_mps=v, re=re, nu=nu, area_m2=area_m2,
                    heat_removed_W=heat_w,
                    regime=("forced" if forced else "natural"),
                    note=("Churchill–Bernstein cross-flow over the housing "
                          f"(air at {t_ambient_c:.0f} °C)" if forced else
                          "still air — natural-convection floor "
                          f"h = {NATURAL_CONVECTION_H:.0f} W/m²·K"))


def _still_film(*, t_wall_c: float, t_ambient_c: float, d_m: float,
                nu_floor: float,
                props: Optional[FluidProps] = None) -> Dict[str, float]:
    """(Ra, Nu, h, film properties) for a cylinder sitting in still air.

    The shared half of ``outer_still`` and ``bore_still``: they differ only in
    which side of the cylinder the air is on, which is a floor on Nu, not a
    different correlation.

    Properties at the FILM temperature ½(T_wall + T_∞) — not at ambient, the way
    ``outer_air`` evaluates its forced film.  There the refinement is inside the
    correlation's own scatter; here it is not: ν rises ~30 % between 25 °C and
    75 °C and Ra ∝ 1/(ν·α), so a hot housing evaluated at ambient comes back
    roughly 1.6× too Rayleigh and ~8 % too cooled.  β = 1/T_film is the ideal-gas
    expansion coefficient at that same temperature.
    """
    d = max(float(d_m), 1e-4)
    t_film = 0.5 * (float(t_wall_c) + float(t_ambient_c))
    p = props or air_properties(t_film)
    beta = 1.0 / max(t_film + _T0_K, 1.0)
    alpha = p.nu / max(p.pr, 1e-6)
    dt = abs(float(t_wall_c) - float(t_ambient_c))
    ra = 9.81 * beta * dt * d ** 3 / max(p.nu * alpha, 1e-30)
    nu = max(churchill_chu_nu(ra, p.pr), float(nu_floor))
    return {"ra": ra, "nu": nu, "h": nu * p.k / d, "k_air": p.k,
            "t_film_c": t_film, "d_m": d, "pr": p.pr}


def outer_still(*, t_wall_c: float, t_ambient_c: float, d_housing_m: float,
                emissivity: float = EMISSIVITY_DEFAULT,
                area_m2: float = 0.0, heat_w: float = 0.0,
                props: Optional[FluidProps] = None) -> Dict[str, Any]:
    """The housing in a ROOM: natural convection + radiation, nothing blowing.

    The mode a robot joint actually runs in (user 2026-09-14) — no fan, no
    jacket, no slipstream, just a motor bolted to an arm in still air.  Until
    today the nearest thing was ``outer_air`` at v = 0, which answers with the
    ``NATURAL_CONVECTION_H`` = 7 W/m²·K flat floor: one number for every machine,
    every ΔT and every finish, and no way to tell a painted housing from a
    polished one.  Two mechanisms replace it, and on a small machine they are
    COMPARABLE — on the Ø85 mm joint at ΔT 60 K over 40 °C air, h_conv ≈ 6 and
    h_rad ≈ 8.3, i.e. more than half the heat that leaves the housing leaves it
    as light:

      * CONVECTION — Churchill–Chu on the housing diameter, film properties at
        ½(T_wall + T_∞).  h ∝ ΔT^(1/4) in the laminar band, so it is NOT a
        constant: a housing 20 K over ambient exchanges ~0.7× what one 60 K over
        does, per square metre;
      * RADIATION — ``radiation_h`` at the caller's emissivity, linearised
        exactly, to an environment at the AIR temperature with a view factor of 1
        (both stated there).

    THE COEFFICIENT DEPENDS ON THE WALL TEMPERATURE, which no other surface in
    this module does, and that makes this the first mode whose Robin condition
    has to be ITERATED against the conduction solve: guess T_wall, solve, read
    the surface's own area-mean temperature back (``thermal_solver_2d``'s
    ``surfaces[i]["t_mean_c"]``, added the same day for exactly this), re-evaluate
    here, repeat.  The dependence is weak — h_total ∝ roughly ΔT^0.15 once
    radiation is in it — so two or three passes land inside a tenth of a kelvin.

    NO ``NATURAL_CONVECTION_H`` FLOOR, deliberately, and it is the one place in
    this module where that constant is NOT applied.  Every other film here is a
    FORCED correlation evaluated at v → 0, where the answer collapses to zero and
    the floor is what stops the model claiming a perfectly insulated machine.
    Churchill–Chu is the still-air correlation itself: at ΔT → 0 it tends to
    Nu = 0.36, i.e. conduction into the surrounding air, and with the radiation
    term beside it the pair lands at ~6.4 W/m²·K on this housing in 40 °C air —
    within 10 % of the 7 the flat constant asserts, from below.  Flooring it at 7
    would double-count the mechanism the constant was standing in for and would
    also make ``emissivity = 0`` unfalsifiable: a polished housing has to come
    back with exactly the convective half, or the ε input means nothing.

    ``heat_w`` is what this surface is currently believed to remove; it is split
    between ``convection_W`` and ``radiation_W`` in the ratio of the two films,
    which is exact — both act on the same area and the same ΔT.

    THE ROBIN COEFFICIENT OF THIS MODE IS ``h_total``, NOT ``h_conv``.  This is
    the only surface in the module that reports two films, and ``h_conv`` keeps
    its plain meaning — the convective half — so that the panel, the report and
    ``emissivity = 0`` all mean what they say.  A caller that hands the
    conduction solve ``h_conv`` here has silently switched the radiation off, and
    on this housing that is more than half the cooling.
    """
    eps = min(max(float(emissivity), 0.0), 1.0)
    film = _still_film(t_wall_c=t_wall_c, t_ambient_c=t_ambient_c,
                       d_m=d_housing_m, nu_floor=0.0, props=props)
    h_conv = float(film["h"])
    h_rad = radiation_h(t_wall_c, t_ambient_c, eps)
    h_tot = h_conv + h_rad
    frac = (h_conv / h_tot) if h_tot > 0.0 else 1.0
    return _surface("still", h_conv=h_conv, t_sink_c=t_ambient_c,
                    t_in_c=t_ambient_c, t_out_c=t_ambient_c,
                    air_speed_mps=0.0, nu=film["nu"], area_m2=area_m2,
                    heat_removed_W=heat_w,
                    h_rad=round(h_rad, 3),
                    h_total=round(h_tot, 3),
                    ra=film["ra"],
                    t_wall_c=round(float(t_wall_c), 2),
                    t_film_c=round(float(film["t_film_c"]), 2),
                    emissivity=eps,
                    convection_W=round(float(heat_w) * frac, 2),
                    radiation_W=round(float(heat_w) * (1.0 - frac), 2),
                    regime=("still air" if film["ra"] <= 1.0e12
                            else "still air (Ra > 1e12 — extrapolated)"),
                    note=(f"still air at {float(t_ambient_c):.0f} °C on a "
                          f"Ø{float(d_housing_m) * 1e3:.0f} mm housing at "
                          f"{float(t_wall_c):.0f} °C: Churchill–Chu "
                          f"(Ra {film['ra']:.2e}, Nu {film['nu']:.1f}) → h_conv "
                          f"{h_conv:.1f} + radiation at ε {eps:.2f} → h_rad "
                          f"{h_rad:.1f} = {h_tot:.1f} W/m²·K; sink = ambient, "
                          f"radiation environment = the air, view factor 1"))


def outer_liquid(*, props: FluidProps, fluid: str, t_in_c: float,
                 flow_lpm: float, r_housing_m: float, length_m: float,
                 heat_w: float, area_m2: float = 0.0,
                 channel_h_m: float = JACKET_CHANNEL_H_M,
                 channel_w_m: float = JACKET_CHANNEL_W_M) -> Dict[str, Any]:
    """Liquid jacket round the housing: inlet temperature + flow in, outlet OUT.

    The channel is a HELICAL groove of width ``channel_w_m`` × depth
    ``channel_h_m`` wound round the housing, so the whole flow runs through one
    w×h cross-section.  That is how jackets are actually machined, and it is the
    difference between a jacket and a bath: modelled as one annular slot (the
    first cut of this function) the same 8 L/min of water crept round a Ø200
    housing at 0.04 m/s, Re 420, laminar, h ≈ 220 W/m²·K — and a 6 kW machine
    "water-cooled" that way sat at 330 °C.  In a 10 × 5 mm groove the same flow
    is 2.7 m/s, Re ≈ 2·10⁴, turbulent, h ≈ 10⁴ W/m²·K, which is what a jacket is
    for.  Velocity, Re and D_h are reported, so a jacket drawing can be checked
    against them; the coefficient acts on the full housing area (the land
    between turns conducts to the groove through a wall far thinner than the
    groove pitch).

    ``heat_w`` is the heat this surface is currently believed to remove; it sets
    the outlet temperature and therefore the sink, which is why the caller has to
    iterate the conduction solve around this function.
    """
    q_m3s = max(float(flow_lpm), 0.0) / 60000.0
    w = max(float(channel_w_m), 1e-4)
    hgt = max(float(channel_h_m), 1e-4)
    d_h = hydraulic_diameter_slot(w, hgt)
    a_c = w * hgt
    v = q_m3s / a_c if a_c > 0.0 else 0.0
    re = v * d_h / props.nu if v > 0.0 else 0.0
    nu, regime = pipe_nusselt(re, props.pr)
    h = nu * props.k / d_h
    m_dot = props.rho * q_m3s
    t_out = outlet_temperature(t_in_c, heat_w, m_dot, props.cp)
    t_sink = 0.5 * (float(t_in_c) + t_out)
    return _surface("liquid", h_conv=h, t_sink_c=t_sink, t_in_c=t_in_c,
                    t_out_c=t_out, flow_lpm=flow_lpm, re=re, nu=nu,
                    area_m2=area_m2, heat_removed_W=heat_w,
                    fluid=str(fluid), regime=regime,
                    m_dot_kg_s=round(m_dot, 5),
                    channel_velocity_mps=round(v, 3),
                    channel_width_mm=round(w * 1e3, 2),
                    channel_height_mm=round(hgt * 1e3, 2),
                    hydraulic_diameter_mm=round(d_h * 1e3, 2),
                    note=(f"{regime} helical jacket groove {w * 1e3:.0f} × "
                          f"{hgt * 1e3:.0f} mm, {v:.2f} m/s; sink = mean "
                          f"coolant temperature, outlet from ṁ·cp"))

#: Volumetric thermal expansion coefficient β [1/K] of the liquids the bore may
#: carry (the air's is 1/T_film).  What drives centrifugal buoyancy: hot fluid
#: at the wall is lighter and falls INWARD in the ω²r field.
_BETA_LIQUID = {"water": 3.0e-4, "water_glycol_50": 4.5e-4,
                "ethylene_glycol": 6.5e-4, "oil": 7.0e-4}


def centrifugal_convection(*, props: FluidProps, beta_per_k: float, rpm: float,
                           r_bore_m: float, wall_dt_k: float) -> Dict[str, float]:
    """Natural convection in the CENTRIFUGAL field of a spinning bore.

    The bore wall turns at ω; the fluid in it spins up with the wall, and in
    that frame the wall sits in an acceleration field g_eff = ω²·r that points
    OUTWARD — 20 000 rpm on a 45 mm bore is ~10⁵ m/s², ten thousand g.  Fluid
    heated at the wall is lighter and falls inward, cold core fluid takes its
    place: a natural-convection loop driven by rotation, not by the Earth.  It
    is what the ½·ω·r "swirl velocity" guess this replaced was standing in for,
    with the wrong scaling (user 2026-09-07: "давай автоматически рассчитывать
    скорость вращения внутри ротора — чем быстрее, тем лучше теплообмен").

    Ra = g_eff·β·ΔT·D³ / (ν·α), Nu = 0.13·Ra^(1/3) for the turbulent branch
    (McAdams' turbulent plate coefficient; Ra > ~10⁹ at any useful speed),
    0.54·Ra^(1/4) below it.  Nu ∝ ω^(2/3): doubling the speed gives 1.59× the
    coefficient.  ΔT is the wall-minus-fluid difference of the previous
    conduction pass (h ∝ ΔT^(1/3), so a rough number costs little).
    """
    r = max(float(r_bore_m), 1e-5)
    d = 2.0 * r
    omega = abs(float(rpm)) * 2.0 * math.pi / 60.0
    g_eff = omega * omega * r
    dt = max(float(wall_dt_k), 1.0)
    alpha = props.nu / max(props.pr, 1e-6)
    ra = g_eff * float(beta_per_k) * dt * d ** 3 / max(props.nu * alpha, 1e-30)
    if ra <= 0.0:
        return {"nu": 0.0, "ra": 0.0, "g_eff": g_eff, "h": 0.0, "regime": "at rest"}
    if ra > 1.0e9:
        nu = 0.13 * ra ** (1.0 / 3.0); regime = "turbulent centrifugal convection"
    else:
        nu = 0.54 * ra ** 0.25; regime = "laminar centrifugal convection"
    return {"nu": nu, "ra": ra, "g_eff": g_eff, "h": nu * props.k / d, "regime": regime}


def _mixed_nusselt(nu_forced: float, nu_natural: float) -> float:
    """Churchill's cube rule for combined forced + natural convection: the
    stronger mechanism dominates, neither is double-counted."""
    return (max(nu_forced, 0.0) ** 3 + max(nu_natural, 0.0) ** 3) ** (1.0 / 3.0)


def bore_air(*, air_speed_mps: float, t_ambient_c: float, r_bore_m: float,
             rpm: float, heat_w: float, area_m2: float = 0.0,
             props: Optional[FluidProps] = None,
             wall_dt_k: float = 40.0) -> Dict[str, Any]:
    """Air blown AXIALLY through the spinning rotor bore.

    Two mechanisms, computed separately and blended (Churchill cube rule):

      * FORCED convection of the axial through-flow — smooth duct of
        D = 2·r_bore, pipe Nusselt ladder on the axial Reynolds number;
      * CENTRIFUGAL natural convection driven by the rotation itself
        (``centrifugal_convection``): ω²r at the wall, Ra on that field.  The
        faster the rotor, the stronger it is — at 20 000 rpm on a 45 mm bore it
        is the larger of the two up to ~30 m/s of axial flow.

    Only the AXIAL flow carries mass: the air heats up along the bore, sink =
    mean of inlet and outlet, and without through-flow the stirred air cannot
    leave the machine — h is real, net removal is zero, and the note says so.
    """
    p = props or air_properties(t_ambient_c)
    r = max(float(r_bore_m), 1e-5)
    d = 2.0 * r
    v = max(float(air_speed_mps), 0.0)
    re = v * d / p.nu
    nu_f, regime_f = pipe_nusselt(re, p.pr) if v > 0.0 else (0.0, "no axial flow")
    t_film_k = 273.15 + float(t_ambient_c) + 0.5 * float(wall_dt_k)
    cen = centrifugal_convection(props=p, beta_per_k=1.0 / max(t_film_k, 1.0), rpm=rpm,
                                 r_bore_m=r, wall_dt_k=wall_dt_k)
    nu = _mixed_nusselt(nu_f, cen["nu"])
    h = nu * p.k / d
    h_forced = nu_f * p.k / d
    m_dot = p.rho * v * math.pi * r * r          # AXIAL flow only carries mass
    t_out = outlet_temperature(t_ambient_c, heat_w, m_dot, p.cp)
    t_sink = 0.5 * (float(t_ambient_c) + t_out)
    regime = (f"{regime_f} + {cen['regime']}" if cen["nu"] > 0 else regime_f)
    if m_dot <= 1e-9:
        note = ("bore air is stirred by rotation but not renewed "
                "(air_speed_mps = 0): no mass flow, no net heat removal")
    else:
        note = (f"axial flow {v:.1f} m/s (Re {re:.0f}, h {h_forced:.0f}) + centrifugal "
                f"convection at {abs(float(rpm)):.0f} rpm (ω²r = {cen['g_eff']:.2e} m/s², "
                f"Ra {cen['ra']:.1e}, h {cen['h']:.0f}) → h {h:.0f} W/m²·K; the air "
                f"heats up along the bore, sink = mean")
    return _surface("air", h_conv=h, t_sink_c=t_sink, t_in_c=t_ambient_c,
                    t_out_c=t_out, air_speed_mps=v, re=re, nu=nu,
                    area_m2=area_m2, heat_removed_W=heat_w,
                    regime=regime, m_dot_kg_s=round(m_dot, 6),
                    h_forced=round(h_forced, 1), h_rotation=round(cen["h"], 1),
                    g_eff_mps2=round(cen["g_eff"], 0), ra_rotation=cen["ra"],
                    bore_diameter_mm=round(d * 1e3, 2), note=note)


def bore_still(*, t_wall_c: float, t_ambient_c: float, r_bore_m: float,
               emissivity: float = EMISSIVITY_DEFAULT,
               area_m2: float = 0.0, heat_w: float = 0.0,
               props: Optional[FluidProps] = None) -> Dict[str, Any]:
    """The rotor bore with nothing pumped through it — the housing's twin.

    A robot joint's bore is not blown and not plumbed: it is a hole with the
    shaft's cable in it, open to the room at both ends.  So the bore wall
    exchanges with the air inside it by natural convection and radiates down the
    hole to the room, and both are small — which is the answer, not a failure of
    the model.  ``bore_air`` at zero speed says the right thing for a DUCT ("the
    air is stirred but not renewed, no mass flow, no heat removal") and nothing
    at all about the standing case.

    TWO DIFFERENCES FROM ``outer_still``, and they are the whole of it:

      * Nu IS FLOORED AT 1.  The bore is a CAVITY, not a body in an unbounded
        room: the lower limit of a cavity's exchange is conduction straight
        across it (Nu = 1, h = k/D), which is what an enclosure correlation must
        return when the buoyant loop is too weak to turn over.  Churchill–Chu's
        own 0.36 quiescent limit is the unbounded-medium answer and is BELOW
        conduction across the hole, i.e. wrong in the direction that matters.
      * THE RADIATION LEAVES ALONG THE AXIS.  A bore wall mostly sees the
        opposite bore wall at nearly its own temperature — that exchange is
        net-zero — and what escapes goes out of the two ends.  Taking view factor
        1 to the room is therefore an OVER-read of this path, the opposite
        direction to the rest of this module, and it is stated here rather than
        hidden: on a sealed joint, or one with the bore full of cable, set
        ``emissivity`` to 0 and the term goes away exactly.

    The sink is ambient: the bore air is the room's air, reaching the hole from
    the ends.  Nothing is metered through it, so unlike ``bore_air`` there is no
    outlet temperature to iterate — only the wall coefficient, which the caller
    re-evaluates as the wall converges, exactly as for the housing.
    """
    eps = min(max(float(emissivity), 0.0), 1.0)
    d = 2.0 * max(float(r_bore_m), 1e-5)
    film = _still_film(t_wall_c=t_wall_c, t_ambient_c=t_ambient_c, d_m=d,
                       nu_floor=1.0, props=props)
    h_conv = float(film["h"])
    h_rad = radiation_h(t_wall_c, t_ambient_c, eps)
    h_tot = h_conv + h_rad
    frac = (h_conv / h_tot) if h_tot > 0.0 else 1.0
    conduction = film["nu"] <= 1.0 + 1e-12
    return _surface("still", h_conv=h_conv, t_sink_c=t_ambient_c,
                    t_in_c=t_ambient_c, t_out_c=t_ambient_c,
                    air_speed_mps=0.0, nu=film["nu"], area_m2=area_m2,
                    heat_removed_W=heat_w,
                    h_rad=round(h_rad, 3),
                    h_total=round(h_tot, 3),
                    ra=film["ra"],
                    t_wall_c=round(float(t_wall_c), 2),
                    t_film_c=round(float(film["t_film_c"]), 2),
                    emissivity=eps,
                    convection_W=round(float(heat_w) * frac, 2),
                    radiation_W=round(float(heat_w) * (1.0 - frac), 2),
                    bore_diameter_mm=round(d * 1e3, 2),
                    m_dot_kg_s=0.0,
                    regime=("conduction across the bore" if conduction
                            else "still air in the bore"),
                    note=(f"unventilated Ø{d * 1e3:.1f} mm bore at "
                          f"{float(t_wall_c):.0f} °C in {float(t_ambient_c):.0f} °C "
                          f"air: Churchill–Chu floored at Nu = 1 "
                          f"(Ra {film['ra']:.2e}, Nu {film['nu']:.2f}) → h_conv "
                          f"{h_conv:.1f} + radiation out of the ends at ε "
                          f"{eps:.2f} → h_rad {h_rad:.1f} = {h_tot:.1f} W/m²·K; "
                          f"nothing flows, so there is no outlet to iterate"))


def bore_liquid(*, props: FluidProps, fluid: str, t_in_c: float,
                flow_lpm: float, r_bore_m: float, heat_w: float,
                area_m2: float = 0.0, rpm: float = 0.0,
                wall_dt_k: float = 20.0) -> Dict[str, Any]:
    """Coolant pumped through the hollow shaft, spinning with it.

    Forced pipe flow on the metered volumetric flow PLUS the centrifugal
    natural convection of the rotation (``centrifugal_convection`` with the
    liquid's β), blended by the cube rule.  A liquid's Ra in a 10⁵ m/s² field is
    enormous — a laminar 4 L/min stream that would give ~50 W/m²·K on its own
    exchanges thousands once the bore turns at speed, which is why rotor
    designers put coolant in the shaft at all.
    """
    r = max(float(r_bore_m), 1e-5)
    d = 2.0 * r
    a_c = math.pi * r * r
    q_m3s = max(float(flow_lpm), 0.0) / 60000.0
    v = q_m3s / a_c
    re = v * d / props.nu if v > 0.0 else 0.0
    nu_f, regime_f = pipe_nusselt(re, props.pr)
    beta = _BETA_LIQUID.get(str(fluid), 3.0e-4)
    cen = centrifugal_convection(props=props, beta_per_k=beta, rpm=rpm,
                                 r_bore_m=r, wall_dt_k=wall_dt_k)
    nu = _mixed_nusselt(nu_f, cen["nu"])
    h = nu * props.k / d
    h_forced = nu_f * props.k / d
    m_dot = props.rho * q_m3s
    t_out = outlet_temperature(t_in_c, heat_w, m_dot, props.cp)
    t_sink = 0.5 * (float(t_in_c) + t_out)
    regime = (f"{regime_f} + {cen['regime']}" if cen["nu"] > 0 else regime_f)
    return _surface("liquid", h_conv=h, t_sink_c=t_sink, t_in_c=t_in_c,
                    t_out_c=t_out, flow_lpm=flow_lpm, re=re, nu=nu,
                    area_m2=area_m2, heat_removed_W=heat_w,
                    fluid=str(fluid), regime=regime,
                    m_dot_kg_s=round(m_dot, 5),
                    bore_velocity_mps=round(v, 3),
                    h_forced=round(h_forced, 1), h_rotation=round(cen["h"], 1),
                    g_eff_mps2=round(cen["g_eff"], 0), ra_rotation=cen["ra"],
                    bore_diameter_mm=round(d * 1e3, 2),
                    note=(f"pipe flow {v:.2f} m/s (Re {re:.0f}, h {h_forced:.0f}) + "
                          f"centrifugal convection at {abs(float(rpm)):.0f} rpm "
                          f"(ω²r = {cen['g_eff']:.2e} m/s², Ra {cen['ra']:.1e}, "
                          f"h {cen['h']:.0f}) → h {h:.0f} W/m²·K; sink = mean coolant "
                          f"temperature, outlet from ṁ·cp"))


# ---------------------------------------------------------------------------
# The shaft that sticks OUT of the housing
# ---------------------------------------------------------------------------
# User, 2026-09-07: *"торцы и лобовые части — только для вала, всё остальное
# вращается внутри мотора"*.  The rotor's end faces and the end windings live
# inside a CLOSED housing, spinning in their own air: whatever they hand to that
# air comes back through the housing, so there is no extra path to model there
# and inventing one would flatter every design.  The SHAFT is the exception —
# it comes out through the bearings on both sides, and those exposed lengths sit
# in the room's air, spinning.  That is a real, separate heat path off the rotor
# and it is the only one this section models.
#
# It is modelled as a FIN, not as a surface: the exposed shaft is not isothermal
# with the stack face — it cools along its length, which is the whole reason a
# 100 mm stub does not remove five times what a 20 mm stub does.  Two numbers
# decide it: the convection coefficient on a cylinder spinning in still air, and
# the fin conductance that follows from it.

#: Rotational Reynolds number above which the rotating-cylinder correlation
#: below is inside its measured band.  Under it the expression is an
#: extrapolation — kept (it is monotone and it meets the natural-convection floor
#: smoothly) but NAMED in ``regime``, because a shaft turning at 200 rpm is not a
#: case anybody measured.
ROTATING_RE_OMEGA_MIN = 1.0e4


def rotating_cylinder_h(*, rpm: float, d_m: float, t_air_c: float,
                        props: Optional[FluidProps] = None) -> Dict[str, Any]:
    """Convection from a CYLINDER SPINNING IN STILL AIR about its own axis.

    Not the same problem as ``outer_air``: there the air is blown ACROSS a
    stationary cylinder (Churchill–Bernstein on a cross-flow Reynolds number),
    here the air is still and the cylinder drags it round.  The driving group is
    the ROTATIONAL Reynolds number built on the surface speed U = ω·d/2::

        Re_ω = U·d/ν = ω·d² / (2·ν)
        Nu   = 0.133 · Re_ω^(2/3) · Pr^(1/3)          (Re_ω ≳ 10⁴)

    the standard rotating-cylinder-in-air correlation (Dropkin & Carmi 1957;
    Etemad).  Nu ∝ ω^(2/3), so doubling the speed buys 1.59× the coefficient —
    the same exponent the centrifugal-convection model in this file has, and for
    the same reason (both are momentum-driven, neither is a pumped flow).

    THE FLOOR IS NOT COSMETIC.  A shaft stub at rest still loses heat by natural
    convection; the correlation evaluated at ω → 0 goes to zero and would report
    a stationary machine's shaft as perfectly insulated.  ``h`` is therefore
    ``max(correlation, NATURAL_CONVECTION_H)``, which also makes h(rpm) monotone
    and continuous — no cliff at the correlation's lower validity bound, which a
    hard branch at Re_ω = 10⁴ would put there (on a 20 mm shaft the correlation
    is already ~70 W/m²K at that Re_ω, i.e. ten times the floor).  ``regime``
    says which of the three cases produced the number, and ``re_omega`` comes
    back so the extrapolated band is visible rather than implied.

    Radiation is deliberately absent: a bare steel stub at 80 °C in a 25 °C room
    radiates ~5 W/m²K, i.e. it would ADD to this — leaving it out under-reads the
    path, which is the safe direction for a cooling claim.
    """
    p = props or air_properties(t_air_c)
    d = max(float(d_m), 1e-4)
    omega = abs(float(rpm)) * 2.0 * math.pi / 60.0
    re_w = omega * d * d / (2.0 * max(p.nu, 1e-12))
    nu = (0.133 * re_w ** (2.0 / 3.0) * p.pr ** (1.0 / 3.0)) if re_w > 0.0 else 0.0
    h_rot = nu * p.k / d
    h = max(h_rot, NATURAL_CONVECTION_H)
    if omega <= 0.0:
        regime = "at rest"
    elif h_rot <= NATURAL_CONVECTION_H:
        regime = "natural"
    elif re_w >= ROTATING_RE_OMEGA_MIN:
        regime = "rotating"
    else:
        regime = "rotating (extrapolated)"
    return {
        "h": float(h),
        "re_omega": float(re_w),
        "nu": float(nu),
        "regime": regime,
        "k_air": float(p.k),
        "d_m": float(d),
        "t_air_c": float(t_air_c),
        "note": ("still-air natural convection floor "
                 f"h = {NATURAL_CONVECTION_H:.0f} W/m²·K"
                 if regime in ("at rest", "natural") else
                 f"cylinder spinning in still air at {abs(float(rpm)):.0f} rpm "
                 f"(Re_ω {re_w:.2e}, Nu {nu:.1f}) → h {h:.0f} W/m²·K"
                 + ("" if re_w >= ROTATING_RE_OMEGA_MIN else
                    "; below Re_ω = 1e4 the correlation is extrapolated")),
    }


def shaft_fin_conductance(*, h: float, d_out_m: float, d_in_m: float,
                          k_shaft: float, length_m: float) -> Dict[str, float]:
    """The exposed shaft length as a FIN rooted at the stack face [W/K].

    Standard uniform-cross-section fin with an ADIABATIC TIP::

        P = π·d_out                       wetted perimeter [m]
        A = π(d_out² − d_in²)/4           conducting cross-section [m²]
        m = √(h·P / (k·A))                [1/m]
        G = √(h·P·k·A) · tanh(m·L)        [W/K]
        η = tanh(m·L) / (m·L)             fin efficiency

    WHY A FIN AND NOT A SURFACE.  ``h·P·L`` — the exposed area times the film —
    is what a "wetted area" model would give, and it is the m·L → 0 limit of the
    expression above.  A real shaft is not isothermal along its length: heat has
    to be CONDUCTED out along the steel before it can be convected away, and past
    m·L ≈ 2.5 (tanh ≈ 0.99) another millimetre of stub adds nothing at all.  On a
    20 mm steel shaft in still air m ≈ 12 /m, so 100 mm of it is m·L ≈ 1.2 and
    η ≈ 0.70 — a wetted-area model would over-read that path by 40 %.

    THE BORE IS NOT WETTED.  ``d_in_m`` removes the hollow shaft's own bore from
    the CROSS-SECTION (less steel to conduct through) but not from the PERIMETER:
    the air inside a bore that is closed at the far end, or plumbed to a rotary
    union, is not the room's air.  A bore that IS blown through is already the
    ``bore`` surface of the conduction solve; counting it twice here would be the
    same watts claimed on two paths.

    An adiabatic tip rather than a convecting one for the same reason the
    radiation term is missing from ``rotating_cylinder_h``: the tip is a disc of
    πd²/4 against a lateral πdL that is an order larger, and under-reading a
    cooling path is the safe direction.
    """
    d_o = max(float(d_out_m), 1e-6)
    d_i = min(max(float(d_in_m), 0.0), d_o * (1.0 - 1e-9))
    hh = max(float(h), 0.0)
    k = max(float(k_shaft), 1e-9)
    L = max(float(length_m), 0.0)
    per = math.pi * d_o
    area = math.pi * (d_o * d_o - d_i * d_i) / 4.0
    if area <= 0.0 or hh <= 0.0 or L <= 0.0:
        return {"G_W_per_K": 0.0, "m_per_m": 0.0, "mL": 0.0, "efficiency": 1.0,
                "perimeter_m": per, "area_m2": max(area, 0.0)}
    m = math.sqrt(hh * per / (k * area))
    ml = m * L
    # tanh saturates in double precision around 20; math.tanh handles it, but the
    # efficiency division does not — take the limit explicitly.
    th = math.tanh(ml)
    return {
        "G_W_per_K": math.sqrt(hh * per * k * area) * th,
        "m_per_m": m,
        "mL": ml,
        "efficiency": (th / ml) if ml > 1e-9 else 1.0,
        "perimeter_m": per,
        "area_m2": area,
    }


def shaft_ends_path(*, rpm: float, t_ambient_c: float, d_out_m: float,
                    d_in_m: float, k_shaft: float, length_each_side_m: float,
                    n_sides: int = 2) -> Dict[str, Any]:
    """The whole extra heat path the exposed shaft ends give the ROTOR.

    ``rotating_cylinder_h`` for the film, ``shaft_fin_conductance`` for the fin,
    times the number of sides that actually stick out (2 on a through-shaft, 1 on
    a machine whose non-drive end is capped).  The result is ONE lumped
    conductance in W/K between the shaft's mean temperature and the ambient — the
    shape a conduction solve can take as a volume sink on the shaft elements
    without pretending the 2-D cross-section has a third dimension.

    IT IS A WHOLE-MACHINE NUMBER.  The lengths and the diameters are the real
    machine's, not a symmetry wedge's, so a solver that runs on a wedge has to
    divide it down (see ``thermal_solver_2d.solve_steady_thermal``'s
    ``volume_sinks``, which takes the machine value and a ``symmetry_mult``).

    ``length_each_side_m`` ≤ 0 turns the path OFF and says so, rather than
    returning a conductance of zero that reads like a shaft with no heat path —
    "there is nothing sticking out" and "the stub removes nothing" are different
    statements about a design.
    """
    n = max(0, min(int(n_sides), 2))
    L = max(float(length_each_side_m), 0.0)
    conv = rotating_cylinder_h(rpm=rpm, d_m=d_out_m, t_air_c=t_ambient_c)
    if L <= 0.0 or n <= 0:
        return {"G_W_per_K": 0.0, "h": float(conv["h"]),
                "re_omega": float(conv["re_omega"]), "nu": float(conv["nu"]),
                "regime": conv["regime"], "n_sides": n,
                "t_sink_c": float(t_ambient_c),
                "per_side": shaft_fin_conductance(
                    h=conv["h"], d_out_m=d_out_m, d_in_m=d_in_m,
                    k_shaft=k_shaft, length_m=0.0),
                "note": ("no shaft length outside the housing — this path is "
                         "off (the rotor's end faces and the end windings turn "
                         "inside the closed housing and have nowhere else to "
                         "send their heat)")}
    fin = shaft_fin_conductance(h=conv["h"], d_out_m=d_out_m, d_in_m=d_in_m,
                                k_shaft=k_shaft, length_m=L)
    return {
        "G_W_per_K": float(fin["G_W_per_K"]) * n,
        "h": float(conv["h"]),
        "re_omega": float(conv["re_omega"]),
        "nu": float(conv["nu"]),
        "regime": conv["regime"],
        "n_sides": n,
        "t_sink_c": float(t_ambient_c),
        "per_side": fin,
        "note": (f"{n} × {L * 1e3:.0f} mm of Ø{float(d_out_m) * 1e3:.1f} mm shaft "
                 f"outside the housing, as a fin (k {float(k_shaft):.0f} W/m·K, "
                 f"mL {fin['mL']:.2f}, η {fin['efficiency'] * 100:.0f} %) in "
                 f"{float(t_ambient_c):.0f} °C air at h {conv['h']:.0f} W/m²·K "
                 f"({conv['regime']}) → {float(fin['G_W_per_K']) * n:.3f} W/K"),
    }


# ---------------------------------------------------------------------------
# The MOUNT — the flange the machine is bolted to
# ---------------------------------------------------------------------------
# User, 2026-09-14: a robot joint in still air.  Add up what the housing can hand
# to the room on the Ø85 mm machine — ~3 W of the 64 W it makes at the rated
# point — and the answer is that the AIR IS NOT THE COOLING SYSTEM.  The heat
# leaves through the bolts: the flange, the arm casting behind it, and whatever
# that casting is attached to.  That path is a contact conductance, not a film,
# and nothing in this module can predict it — it depends on the bolt pattern, the
# contact area, the interface (dry / thermal pad / grease) and the flatness of
# two machined faces.  So it is an INPUT, in W/K, and the model's job is to be
# honest about which of the two numbers the machine's temperature actually hangs
# on.


def mount_path(*, g_w_per_k: float,
               t_mount_c: float) -> Dict[str, Any]:
    """The bolted mount as ONE lumped conductance to a held temperature [W/K].

    Same shape and the same role as ``shaft_ends_path``: a whole-machine
    conductance for ``thermal_solver_2d``'s ``volume_sinks``, attached to the
    stator/housing elements, because a 2-D cross-section has no face out along
    the axis for the flange to sit on.  The solver's identity then makes it
    checkable from the payload rather than trusted::

        mount_W == G · (t_housing_mean_c − t_mount_c)

    THE MOUNT IS AN INFINITE SINK.  ``t_mount_c`` is HELD: the arm behind the
    flange is assumed massive enough that the joint's watts do not warm it.  On a
    duty cycle that is the optimistic end — a real arm rises — and it is stated
    rather than modelled, because modelling it needs the arm's mass and its own
    path to the room, neither of which is in this machine's file.

    ``g_w_per_k`` ≤ 0 turns the path OFF and SAYS so, the same way
    ``shaft_ends_path`` refuses to return a bare zero: "this machine is not
    conducting into its mount" and "nobody has typed the mount conductance yet"
    are different statements about a design, and on a still-air machine the
    difference is the difference between 120 °C and a refusal.

    For scale, on the Ø85 mm joint at its 64 W rated point: 0.5 W/K is a dry
    interface through a few M4 bolts and leaves the housing ~120 K over the arm;
    2 W/K is a machined face with thermal compound and leaves it ~30 K over; 10
    W/K is a joint whose housing is effectively part of the arm casting.
    """
    g = float(g_w_per_k)
    if not math.isfinite(g) or g <= 0.0:
        return {"mode": "off", "G_W_per_K": 0.0,
                "t_sink_c": float(t_mount_c), "heat_removed_W": 0.0,
                "note": ("no mount conductance given — the machine is modelled "
                         "as bolted to NOTHING, and every watt it makes has to "
                         "leave through the air")}
    return {"mode": "conduction", "G_W_per_K": g,
            "t_sink_c": float(t_mount_c), "heat_removed_W": 0.0,
            "note": (f"{g:.3f} W/K from the housing into a mount held at "
                     f"{float(t_mount_c):.0f} °C (bolted flange + arm; contact "
                     f"conductance is an INPUT — it depends on the bolt pattern, "
                     f"the contact area and the interface, none of which is in "
                     f"the machine's file).  The mount is an infinite sink: its "
                     f"temperature does not rise with the machine's heat")}


# ---------------------------------------------------------------------------
# The ROBOT LINK — the arm itself heats up (2026-09-26)
# ---------------------------------------------------------------------------
# ``mount_path`` above is the machine bolted to an INFINITE sink: correct for a
# machined test bench, wrong for a robot joint, whose "sink" is a few hundred
# grams of aluminium (or steel, or moulded plastic) that itself only loses heat
# to the room by natural convection and radiation off its own skin.  On a small
# machine (e.g. a 25 g Ø12 finger motor at ~200 W into the mount) that arm does
# not sit at ambient — it climbs until ITS OWN surface sheds what the motor is
# handing it, and a person's finger on it is limited by IEC 60335's 70 °C touch
# rule, not by the winding's insulation class.
#
# The model adds exactly ONE extra node: the link is a solid cylinder (one of
# three fixed presets), still air + radiation off its own surface (the same
# Churchill–Chu + linearised-radiation pair ``outer_still`` uses, on a smaller
# diameter), and its own Σm·c_p for a duty cycle's transient.  The bolted
# contact conductance (``mount_g_w_per_k``) is UNCHANGED — it is still an INPUT
# nobody here can predict (bolt pattern, contact area, interface) — this
# section only answers "how hot does the arm get, and how fast does it move".

#: Three sizes, as one solid cylinder standing in for the arm segment the motor
#: is bolted to.  Deliberately a crude shape (a real link is a hollow, ribbed
#: casting with a far larger cooling surface per kilogram) — stated in the
#: HelpTip so a chosen preset is never mistaken for a CAD measurement:
#:   finger — 60 mm long,  16 mm across (a small end-effector segment)
#:   wrist  — 120 mm long, 40 mm across (a wrist/forearm link)
#:   arm    — 250 mm long, 80 mm across (an upper-arm link)
LINK_PRESETS: Dict[str, Dict[str, float]] = {
    "finger": {"length_m": 0.060, "diameter_m": 0.016},
    "wrist":  {"length_m": 0.120, "diameter_m": 0.040},
    "arm":    {"length_m": 0.250, "diameter_m": 0.080},
}

#: {material: (density kg/m³, specific heat J/kg·K)} — textbook room-temperature
#: figures, the same standard this project already keeps in
#: ``thermal_capacities.CP_DEFAULT`` for the machine's own parts.  Only mass
#: (hence heat capacity) is material-specific here: the link's OWN surface
#: film (still air + radiation) does not know what is inside the skin.
LINK_MATERIALS: Dict[str, Dict[str, float]] = {
    "aluminium": {"density_kg_m3": 2700.0, "cp_j_per_kgk": 896.0},
    "steel":     {"density_kg_m3": 7850.0, "cp_j_per_kgk": 480.0},
    "plastic":   {"density_kg_m3": 1200.0, "cp_j_per_kgk": 1500.0},
}

#: IEC 60335-1 Annex, metal handle touched briefly in normal use — the fixed
#: limit this joint is judged against, beside the winding's insulation class
#: and the magnet's card.  Not a setting: the owner's ask is a machine that
#: cannot be rated hot enough to burn whoever picks up the arm.
LINK_TOUCH_LIMIT_C = 70.0


def robot_link_geometry(preset: str, material: str) -> Dict[str, Any]:
    """The preset's fixed numbers, resolved — dimensions, area, mass, capacity.

    An unknown preset/material name falls back to the middle of each table
    ("wrist" / "aluminium") rather than raising: this is a UI default, not a
    request the router refuses, so it degrades gracefully to a debug tool
    (unit test, curl, stale query string) that types nothing at all.
    """
    preset_key = str(preset or "").strip().lower()
    material_key = str(material or "").strip().lower()
    if preset_key not in LINK_PRESETS:
        preset_key = "wrist"
    if material_key not in LINK_MATERIALS:
        material_key = "aluminium"
    p = LINK_PRESETS[preset_key]
    m = LINK_MATERIALS[material_key]
    length_m = float(p["length_m"])
    diameter_m = float(p["diameter_m"])
    # Cylinder, both round ends included: the whole skin the film acts on.
    area_m2 = (math.pi * diameter_m * length_m
              + 2.0 * math.pi * (diameter_m / 2.0) ** 2)
    volume_m3 = math.pi * (diameter_m / 2.0) ** 2 * length_m
    mass_kg = volume_m3 * float(m["density_kg_m3"])
    return {
        "preset": preset_key,
        "material": material_key,
        "length_mm": round(length_m * 1e3, 1),
        "diameter_mm": round(diameter_m * 1e3, 1),
        "area_m2": area_m2,
        "volume_m3": volume_m3,
        "mass_kg": mass_kg,
        "cp_J_per_kgK": float(m["cp_j_per_kgk"]),
        "C_J_per_K": mass_kg * float(m["cp_j_per_kgk"]),
    }


def robot_link_path(*, q_w: float, t_ambient_c: float,
                    preset: str = "wrist", material: str = "aluminium",
                    emissivity: float = EMISSIVITY_DEFAULT,
                    props: Optional[FluidProps] = None) -> Dict[str, Any]:
    """The link, as a Robin sink to ambient — ``t_link_c`` solved so that its
    own still-air + radiation film carries exactly ``q_w`` off its surface.

    Same fixed-point shape as ``outer_still``/``_still_film`` (Nu depends on
    ΔT, so ``h`` is only known once ``t_link_c`` is), but solved HERE rather
    than left to the caller's own pass loop: the link is not a meshed surface,
    it is one extra lumped node, and closing this one small loop locally is
    simpler than threading a fifth wall into ``routes.thermal``'s iteration.
    Five picard passes land within a hundredth of a kelvin on every case this
    module's own tests run (h moves as ΔT^0.15 once radiation is in it, the
    same weak dependence ``outer_still`` documents).

    ``q_w`` ≤ 0 is a link nobody is heating — it sits at ambient and the touch
    limit cannot bind.
    """
    geo = robot_link_geometry(preset, material)
    area = max(float(geo["area_m2"]), 1e-6)
    d = max(float(geo["diameter_mm"]) * 1e-3, 1e-4)
    amb = float(t_ambient_c)
    q = max(float(q_w), 0.0)
    if q <= 0.0:
        t_link = amb
        h_conv = h_rad = 0.0
    else:
        t_link = amb + 10.0
        for _ in range(25):
            film = _still_film(t_wall_c=t_link, t_ambient_c=amb, d_m=d,
                               nu_floor=0.36, props=props)
            h_conv = float(film["h"])
            h_rad = radiation_h(t_link, amb, emissivity)
            h_tot = max(h_conv + h_rad, 1e-9)
            t_new = amb + q / (h_tot * area)
            if abs(t_new - t_link) < 1e-4:
                t_link = t_new
                break
            t_link = 0.5 * (t_link + t_new)
    g_eff = q / max(t_link - amb, 1e-9) if q > 0.0 else 0.0
    over_k = t_link - LINK_TOUCH_LIMIT_C
    return dict(geo, t_link_c=t_link, h_conv=h_conv, h_rad=h_rad,
               G_W_per_K=g_eff, heat_removed_W=q,
               touch_limit_c=LINK_TOUCH_LIMIT_C, over_touch_K=over_k,
               binds_touch_limit=bool(over_k > 0.0),
               note=(f"{geo['preset']} link ({geo['material']}): "
                     f"{geo['area_m2'] * 1e4:.0f} cm², {geo['mass_kg'] * 1e3:.0f} g "
                     f"-> {t_link:.1f} °C carrying {q:.1f} W in {amb:.0f} °C air "
                     f"(touch limit {LINK_TOUCH_LIMIT_C:.0f} °C)"))


# ---------------------------------------------------------------------------
# The AXIAL FACES — what a still-air machine loses off its two ends
# ---------------------------------------------------------------------------
# User, 2026-09-14: on this joint the 24 coils stand PROUD of the core on both
# sides and the core's own end faces are largely uncovered.  That breaks the
# 2026-09-07 ruling this file's shaft section is built on (*"торцы и лобовые
# части — только для вала"*) for this machine: there the end turns spin inside a
# closed housing and have nowhere to send their heat, here they are the most
# exposed copper in the machine and they face the room directly.
#
# A 2-D cross-section has no facets out along the axis, so — exactly like the
# shaft stubs, the open frame's end turns and the mount — these paths cannot be
# Robin surfaces.  They are whole-machine LUMPED CONDUCTANCES in W/K for
# ``thermal_solver_2d``'s ``volume_sinks``: G = h_total(ΔT)·A, with h_total the
# same natural-convection + radiation pair ``outer_still`` uses, on a FLAT face
# instead of a cylinder.
#
# THE CONDUCTANCE DEPENDS ON THE WALL TEMPERATURE, so the caller iterates it the
# same way it iterates the housing film: h ∝ ΔT^(1/4) on the convective half,
# radiation is re-linearised about the new wall, and the sink's own reported
# ``t_mean_c`` is what closes the loop.


def flat_plate_nu(ra: float, pr: float,
                  orientation: str = "vertical") -> Tuple[float, str]:
    """(Nu, regime) for natural convection on a FLAT FACE in still air.

      * ``vertical`` — Churchill–Chu's vertical-plate form, the twin of the
        horizontal-cylinder one this module already uses::

            Nu = { 0.825 + 0.387·Ra^(1/6) / [1 + (0.492/Pr)^(9/16)]^(8/27) }²

        One expression over the whole Ra range, laminar plume and turbulent
        alike, for the same reason every other ladder here is one expression.
        The characteristic length is the face's HEIGHT (the distance the plume
        climbs), not its area.
      * ``horizontal_up`` — a hot face looking UP, the buoyant plume free to
        leave: McAdams 0.54·Ra^(1/4) below Ra 10⁷, 0.15·Ra^(1/3) above it, on the
        characteristic length A/P.
      * ``horizontal_down`` — a hot face looking DOWN, the hot air trapped
        against it and forced to creep out sideways: 0.27·Ra^(1/4), i.e. HALF the
        face-up value.  A machine's orientation is worth a factor of two on this
        path, which is why it is an input and not an assumption.

    Every branch is floored at the vertical form's own quiescent limit (Nu →
    0.68 as Ra → 0): a face at ambient still conducts into the air touching it,
    and the McAdams power laws — fits over a measured band, not limits — go to
    zero there and would report an adiabatic end face.
    """
    ra = max(float(ra), 0.0)
    pr = max(float(pr), 1e-6)
    o = str(orientation or "vertical").strip().lower()
    denom = (1.0 + (0.492 / pr) ** (9.0 / 16.0)) ** (8.0 / 27.0)
    nu_vert = (0.825 + 0.387 * ra ** (1.0 / 6.0) / denom) ** 2
    #: the Ra → 0 limit of the vertical form — conduction into the air in contact
    floor = 0.825 ** 2
    if o in ("vertical", "side", "end"):
        return nu_vert, "vertical plate"
    if o in ("horizontal_up", "up", "face_up"):
        if ra < 1.0e7:
            return max(0.54 * ra ** 0.25, floor), "horizontal plate, hot face up"
        return max(0.15 * ra ** (1.0 / 3.0), floor), \
            "horizontal plate, hot face up (turbulent)"
    if o in ("horizontal_down", "down", "face_down"):
        return max(0.27 * ra ** 0.25, floor), "horizontal plate, hot face down"
    raise ValueError(
        "orientation must be 'vertical', 'horizontal_up' or 'horizontal_down', "
        "got %r — a face's orientation is worth a factor of two on this path, so "
        "it is not guessed at" % (orientation,))


def end_face_still(*, t_wall_c: float, t_ambient_c: float, area_m2: float,
                   char_len_m: float,
                   emissivity: float = EMISSIVITY_DEFAULT,
                   orientation: str = "vertical", n_faces: int = 1,
                   name: str = "end face",
                   props: Optional[FluidProps] = None) -> Dict[str, Any]:
    """An exposed axial face in still air, as a lumped conductance [W/K].

    ``area_m2`` is the exposed area of ONE face — the annulus of core end that
    nothing covers, or the wetted area of the end turns standing proud of it (see
    ``end_winding_area``) — and ``n_faces`` says how many of them the machine
    has (2 on a joint open at both ends, 1 when one end is against the gearbox).
    ``char_len_m`` is the length the correlation runs on: the face's HEIGHT when
    it is vertical, A/P when it lies flat.

    The output is ``G_W_per_K = h_total · area · n_faces`` and the watts that
    conductance carries AT THE STATED WALL TEMPERATURE, so the caller can put the
    G into ``volume_sinks`` and check the solver's answer against
    ``G · (t_mean_c − t_ambient_c)`` afterwards.

    NO FIN, AND THAT IS A CHOICE.  The face is taken as isothermal at
    ``t_wall_c`` — the temperature the conduction solve will hand back for the
    elements this sink is spread over.  For the core's end annulus that is nearly
    true (steel, a few millimetres of path, h ≈ 10).  For the end TURNS it is the
    same judgement ``end_windings_path`` states at length: copper at 400 W/m·K, a
    few millimetres thick, generating its heat along its whole length rather than
    being fed at one end — m·L ≈ 0.3, η ≈ 0.96, inside the correlation's own
    scatter.

    The radiation half carries the three approximations ``radiation_h`` states,
    and one more of its own: an end face bolted close to an arm sees that arm,
    not the room, and a view factor of 1 over-reads it.  ``emissivity = 0``
    removes the term exactly, which is how a buried face is modelled.
    """
    eps = min(max(float(emissivity), 0.0), 1.0)
    a = max(float(area_m2), 0.0)
    n = max(int(n_faces), 0)
    lc = max(float(char_len_m), 1e-4)
    t_film = 0.5 * (float(t_wall_c) + float(t_ambient_c))
    p = props or air_properties(t_film)
    beta = 1.0 / max(t_film + _T0_K, 1.0)
    alpha = p.nu / max(p.pr, 1e-6)
    dt = float(t_wall_c) - float(t_ambient_c)
    ra = 9.81 * beta * abs(dt) * lc ** 3 / max(p.nu * alpha, 1e-30)
    nu, regime = flat_plate_nu(ra, p.pr, orientation)
    h_conv = nu * p.k / lc
    h_rad = radiation_h(t_wall_c, t_ambient_c, eps)
    h_tot = h_conv + h_rad
    g = h_tot * a * n
    if a <= 0.0 or n <= 0:
        note = (f"no exposed {name} area — this path is off (the face is "
                f"covered, or it is not modelled as exposed)")
    else:
        note = (f"{n} × {a * 1e4:.1f} cm² of {name} at {float(t_wall_c):.0f} °C "
                f"in {float(t_ambient_c):.0f} °C still air, {regime} on "
                f"{lc * 1e3:.0f} mm (Ra {ra:.2e}, Nu {nu:.1f}) → h_conv "
                f"{h_conv:.1f} + radiation at ε {eps:.2f} → h_rad {h_rad:.1f} "
                f"= {h_tot:.1f} W/m²·K → {g:.3f} W/K")
    return {
        "mode": ("still" if (a > 0.0 and n > 0) else "off"),
        "name": str(name),
        "G_W_per_K": float(g),
        "h_conv": float(h_conv),
        "h_rad": float(h_rad),
        "h_total": float(h_tot),
        "ra": float(ra),
        "nu": float(nu),
        "regime": regime,
        "orientation": str(orientation),
        "area_m2": float(a),
        "area_total_m2": float(a * n),
        "char_len_m": float(lc),
        "n_faces": n,
        "emissivity": eps,
        "t_wall_c": float(t_wall_c),
        "t_film_c": float(t_film),
        "t_sink_c": float(t_ambient_c),
        "heat_removed_W": float(g * dt),
        "convection_W": float(h_conv * a * n * dt),
        "radiation_W": float(h_rad * a * n * dt),
        "note": note,
    }


# ---------------------------------------------------------------------------
# The OPEN frame — a machine that has no housing at all
# ---------------------------------------------------------------------------
# User, 2026-09-09: the 40 mm "CIANO14 40 new" has NO HOUSING.  The stator tooth
# blocks with their coils are held between two end plates by standoff pins, and
# the coil END WINDINGS and the axial CHANNELS between neighbouring coils sit
# directly in the propeller wash (~10-12 m/s).
#
# That breaks the assumption the whole ``shaft_ends_path`` section above is built
# on — *"торцы и лобовые части — только для вала, всё остальное вращается внутри
# мотора"* (2026-09-07) — but only for THIS kind of machine, which is why the
# two models live side by side and the caller picks one by name (``frame`` =
# housed | open).  On a housed motor the end turns really do have nowhere to send
# their heat; on an open one they are the best-cooled copper in the machine, and
# on this design they are 76 % of the copper LENGTH (k_end = 1.759 on the L12),
# so leaving them out is not a conservative simplification — it is most of the
# winding missing from the model.
#
# BOTH PATHS ARE THE SAME SHAPE AS ``shaft_ends_path``: a whole-machine lumped
# conductance in W/K against ambient, for ``thermal_solver_2d``'s
# ``volume_sinks``.  A 2-D cross-section has no facets out along the axis, and
# the slot channel's own walls are inside the mesh, not on its boundary — so
# neither of them can be a Robin surface, and pretending otherwise would put the
# film on the wrong side of the insulation.


def end_winding_area(*, n_coils: int, bar_thickness_m: float,
                     bar_width_m: float, end_turn_length_m: float,
                     n_sides: int = 2) -> Dict[str, Any]:
    """The exposed area of the end turns [m²], and the bar's equivalent diameter.

    Split out of ``end_windings_path`` on 2026-09-14 so the STILL-air machine can
    have the geometry without the forced-air film: a robot joint's 24 coils stand
    proud of the core on both sides exactly as an open propeller motor's do, but
    there is no wash — the film comes from ``end_face_still`` instead.  One
    derivation, two h's, so the two machines cannot disagree about how much
    copper is out there.

        A_ew = n_coils · n_sides · P_exposed · ℓ_end
        P_exposed = 2·thickness + width        (the tooth-facing face is shielded)
        D_e = 4·A_bar / P_bar                  (the bar as an equivalent cylinder)

    ``end_turn_length_m`` is ℓ_end = (k_end − 1)·L_stack / 2 — derived from the
    SAME end-winding factor the electromagnetic run billed its copper loss at, so
    the area that cools the end turns and the length that heats them are one
    number, not two guesses.  ``bar_thickness_m`` is the wire stack's depth
    (``num_wires_per_slot × (wire_height + wire_spacing_y)``) and
    ``bar_width_m`` the whole wire column (``winding_footprint_mm``).

    ``P_exposed = 2·t + w`` rather than the full 2(t + w) is the judgement
    ``end_windings_path`` makes and states: one wide face looks at the tooth it
    is wrapped around and at the stack's end face, a millimetre off iron.  On a
    6-row × 2 mm bundle that is 22 % less area than the full perimeter — the
    under-reading direction, like everything else in this module.
    """
    t = max(float(bar_thickness_m), 0.0)
    w = max(float(bar_width_m), 0.0)
    ell = max(float(end_turn_length_m), 0.0)
    n_c = max(int(n_coils), 0)
    n_s = max(0, min(int(n_sides), 2))
    per = 2.0 * t + w                       # the tooth-facing width is shielded
    return {
        "area_m2": float(n_c) * float(n_s) * per * ell,
        "area_per_side_m2": float(n_c) * per * ell,
        "perimeter_m": float(per),
        "d_equiv_m": (hydraulic_diameter_slot(w, t)
                      if (w > 0.0 and t > 0.0) else 0.0),
        "n_coils": n_c,
        "n_sides": n_s,
        "bar_thickness_mm": round(t * 1e3, 3),
        "bar_width_mm": round(w * 1e3, 3),
        "end_turn_length_mm": round(ell * 1e3, 3),
    }


def end_windings_path(*, air_speed_mps: float, t_ambient_c: float,
                      n_coils: int, bar_thickness_m: float, bar_width_m: float,
                      end_turn_length_m: float, n_sides: int = 2,
                      props: Optional[FluidProps] = None) -> Dict[str, Any]:
    """The end-turn bundles of an OPEN machine, in the airflow [W/K].

    The end turn of a tooth coil is a bar of copper arcing over the tooth: its
    cross-section is the wire stack itself — ``bar_thickness_m`` rows deep
    (``num_wires_per_slot × (wire_height + wire_spacing_y)``) by
    ``bar_width_m`` across (the whole wire COLUMN, i.e.
    ``winding.winding_footprint_mm``, which is ``wire_width`` on an unsplit
    machine) — and its length per side is ``end_turn_length_m``, which the caller
    derives from the SAME end-winding factor the electromagnetic run used:
    ``ℓ_end = (k_end − 1)·L_stack / 2``.

        A_ew = n_coils · n_sides · P_exposed · ℓ_end
        D_e  = 4·A_bar / P_bar          (the bar as an equivalent cylinder)
        Re   = v·D_e/ν,  Nu = Churchill-Bernstein(Re, Pr),  h = Nu·k/D_e
        G_ew = h_ew · A_ew

    THREE JUDGEMENT CALLS, all in the direction of under-reading the path — the
    same rule ``rotating_cylinder_h`` states for its missing radiation term:

      * ``P_exposed = 2·thickness + width``, NOT the full ``2(t + w)``.  One of
        the two wide faces looks at the tooth it is wrapped around and at the
        stack's end face; it is a millimetre off iron, not in the wash.  On a
        6-row × 2 mm bundle that is 22 % less area than the full perimeter.
      * FIN EFFICIENCY IS TAKEN AS 1 and no fin is solved.  Unlike the shaft
        stub, the end turn is not a cooled extension of a hot root: it is the
        SAME conductor that runs through the slot, k ≈ 400 W/m·K, a few
        millimetres thick and tens of millimetres long, with the heat generated
        along its whole length rather than fed in at one end.  m·L on a 3.6 × 2 mm
        copper bar at h = 60 W/m²·K is ≈ 0.35, i.e. η ≈ 0.96 — inside the
        correlation's own scatter, and adding a fin would need a root
        temperature this 2-D model does not have.
      * CROSS-FLOW, not axial: the wash comes down the machine's axis and the end
        turn arcs across it, so Churchill-Bernstein on an equivalent cylinder is
        the right family (it is what ``outer_air`` uses on the housing).  The
        bundle is not round; the equivalent diameter is 4A/P, which is the
        standard substitution and is reported so it can be argued with.

    Below the forced value sits the same still-air floor every film in this
    module has: an open machine at rest still loses heat off its end turns, and
    a correlation evaluated at v = 0 does not know that.
    """
    p = props or air_properties(t_ambient_c)
    t = max(float(bar_thickness_m), 0.0)
    w = max(float(bar_width_m), 0.0)
    ell = max(float(end_turn_length_m), 0.0)
    v = max(float(air_speed_mps), 0.0)
    # The geometry is `end_winding_area`'s (2026-09-14) — the same derivation the
    # still-air machine uses, so the two frames cannot disagree about how much
    # copper is standing out of the core.  Only the FILM differs between them.
    geom = end_winding_area(n_coils=n_coils, bar_thickness_m=t, bar_width_m=w,
                            end_turn_length_m=ell, n_sides=n_sides)
    n_c, n_s = geom["n_coils"], geom["n_sides"]
    # The bar as an equivalent cylinder — 4A/P of the FULL rectangle, because
    # that is the body the flow sees; the shielded face is removed from the
    # AREA below, not from the shape.
    d_e = float(geom["d_equiv_m"])
    re = (v * d_e / p.nu) if (v > 0.0 and d_e > 0.0) else 0.0
    nu = churchill_bernstein_nu(re, p.pr) if re > 1.0 else 0.0
    h_forced = (nu * p.k / d_e) if (nu and d_e > 0.0) else 0.0
    h = max(h_forced, NATURAL_CONVECTION_H)
    per = float(geom["perimeter_m"])
    area = float(geom["area_m2"])
    g = h * area
    forced = h_forced > NATURAL_CONVECTION_H
    return {
        "G_W_per_K": float(g),
        "h": float(h),
        "h_forced": float(h_forced),
        "re": float(re),
        "nu": float(nu),
        "regime": ("forced" if forced else ("at rest" if v <= 0.0 else "natural")),
        "area_m2": float(area),
        "perimeter_m": float(per),
        "d_equiv_m": float(d_e),
        "n_coils": n_c,
        "n_sides": n_s,
        "bar_thickness_mm": round(t * 1e3, 3),
        "bar_width_mm": round(w * 1e3, 3),
        "end_turn_length_mm": round(ell * 1e3, 3),
        "air_speed_mps": float(v),
        "t_sink_c": float(t_ambient_c),
        "fin_efficiency": 1.0,
        "note": (
            f"{n_c} coils × {n_s} side(s) of {ell * 1e3:.1f} mm end turn, bundle "
            f"{t * 1e3:.2f} × {w * 1e3:.2f} mm, exposed perimeter 2·t + w "
            f"(the tooth-facing face is not in the wash) → A_ew "
            f"{area * 1e4:.1f} cm²; "
            + (f"Churchill-Bernstein cross-flow at {v:.1f} m/s on the bundle's "
               f"equivalent Ø{d_e * 1e3:.2f} mm (Re {re:.0f}, Nu {nu:.1f}) → "
               f"h {h:.0f} W/m²·K" if forced else
               f"still air — natural-convection floor h = "
               f"{NATURAL_CONVECTION_H:.0f} W/m²·K")
            + f" → {g:.3f} W/K.  Copper fin efficiency taken as 1 (k ≈ 400 W/m·K, "
              "m·L ≈ 0.3 on a bundle this size)."),
    }


def slot_channels_path(*, air_speed_mps: float, t_ambient_c: float,
                       wetted_perimeter_m: float, cross_section_m2: float,
                       length_m: float, n_channels: int = 0,
                       props: Optional[FluidProps] = None) -> Dict[str, Any]:
    """The ventilated axial channels between neighbouring coils [W/K].

    On an open machine the slot is not a closed pocket: it is a duct open at both
    ends, and the same wash that cools the end turns goes straight through it.
    The geometry comes from the MESH — the caller measures the slot-air domain's
    own cross-section and the length of its boundary against the solids (copper,
    enamel, liner, tooth iron), both summed over the whole machine — because a
    slot's free area after the wires are in it is not a geometry field anybody
    types.

        D_h  = 4·A_cs / P_wet                   (per channel, and in total: both
                                                 scale with the channel count)
        Re   = v·D_h/ν,  Nu = pipe ladder(Re, Pr),  h_ch = Nu·k/D_h
        G_ch = h_ch · P_wet · L_stack

    FULLY-DEVELOPED Nusselt on a duct that is anything but: L/D_h on a 40 mm
    machine is ≈ 12/2.5 ≈ 5, deep inside the entrance region where the local
    coefficient is 1.5-3× the developed one.  Using the developed value is
    therefore a deliberate UNDER-read (the same direction as everything else
    here); a Hausen/Sieder-Tate entrance correction would be the refinement, and
    it needs a wall condition this lumped model does not carry.

    The still-air floor applies for the same reason it does everywhere else in
    this module, and it is what a 0 m/s request gets: ``pipe_nusselt`` returns
    its laminar 3.66 at any Reynolds number including zero, and 3.66·k/D_h on a
    2 mm channel is ~40 W/m²·K — a duct with nothing moving in it does not
    exchange that.  So the forced branch is only consulted when there IS flow.
    """
    p = props or air_properties(t_ambient_c)
    per = max(float(wetted_perimeter_m), 0.0)
    a_cs = max(float(cross_section_m2), 0.0)
    L = max(float(length_m), 0.0)
    v = max(float(air_speed_mps), 0.0)
    d_h = (4.0 * a_cs / per) if per > 0.0 else 0.0
    re = (v * d_h / p.nu) if (v > 0.0 and d_h > 0.0) else 0.0
    if v > 0.0 and d_h > 0.0:
        nu, regime = pipe_nusselt(re, p.pr)
        h_forced = nu * p.k / d_h
    else:
        nu, regime, h_forced = 0.0, "no axial flow", 0.0
    h = max(h_forced, NATURAL_CONVECTION_H)
    area = per * L
    g = h * area
    forced = h_forced > NATURAL_CONVECTION_H
    return {
        "G_W_per_K": float(g),
        "h": float(h),
        "h_forced": float(h_forced),
        "re": float(re),
        "nu": float(nu),
        "regime": (regime if forced else
                   ("at rest" if v <= 0.0 else "natural")),
        "area_m2": float(area),
        "wetted_perimeter_m": float(per),
        "cross_section_m2": float(a_cs),
        "hydraulic_diameter_m": float(d_h),
        "n_channels": int(n_channels),
        "length_m": float(L),
        "air_speed_mps": float(v),
        "t_sink_c": float(t_ambient_c),
        "note": (
            f"{int(n_channels)} axial channel(s), wetted perimeter "
            f"{per * 1e3:.1f} mm × {L * 1e3:.1f} mm of stack → A "
            f"{area * 1e4:.1f} cm², D_h {d_h * 1e3:.2f} mm; "
            + (f"{regime} duct flow at {v:.1f} m/s (Re {re:.0f}, Nu {nu:.2f}) → "
               f"h {h:.0f} W/m²·K, fully-developed value on a short duct "
               f"(L/D_h {(L / d_h if d_h > 0 else 0.0):.1f}) — an under-read"
               if forced else
               f"no through-flow — natural-convection floor h = "
               f"{NATURAL_CONVECTION_H:.0f} W/m²·K")
            + f" → {g:.3f} W/K"),
    }


# ---------------------------------------------------------------------------
# The OPEN frame, part two: the ROTOR is in the wash as well (2026-09-21)
# ---------------------------------------------------------------------------
# User, 2026-09-21, with his thermal photographs of the Ø50 drone motor in
# front of him: *«по термофотографиям катушки греются всегда значительно больше
# магнитов; конструкция полностью открыта, магниты обдуваются со всех сторон, и
# воздух ещё продувает зазор»* — the coils are ALWAYS much hotter than the
# magnets, the build is open on every side, the magnets are washed all round and
# the air blows through the gap as well.  The model said the opposite: on the
# CIANO14 50 edited / L15 record the winding came out at 251 °C and the magnets
# at 240 °C, i.e. the rotor sat 11 K under the copper because its ONLY ways out
# of the 2-D section were the 0.2 mm air gap and a 10 m/s bore.
#
# The 2026-09-09 open frame put the STATOR side in the wash (end turns, slot
# channels) and left the rotor where the housed model had it.  These two
# functions put the rotor in the same air:
#
#   * ``rotor_end_faces_open`` — the rotor's axial faces (iron + magnet ends) in
#     forced convection.  They are a disc spinning at the machine's own speed
#     INSIDE an axial stream, so the film is the larger of the rotating-disc and
#     the flat-plate cross-flow value — the two mechanisms do not add, and
#     taking the larger is the standard conservative reading of a mixed film;
#   * ``gap_axial_flow`` — the clearance as a short annular DUCT the wash blows
#     through, with the through-velocity SOLVED from the pressure balance rather
#     than assumed, and a mass flow that fixes how many watts the gap air can
#     carry away axially.
#
# NEITHER HAS BEEN CALIBRATED.  The owner has the thermal photographs and has
# not given numbers yet (2026-09-21), so the two multipliers below are 1.0 and
# say what they are waiting for.  Nothing here invents a measurement.

#: Multiplier on the SOLVED gap through-velocity (``gap_axial_flow``).
#:
#: The velocity itself is a pressure balance and not a guess: the wash's dynamic
#: head ½ρv² drives the gap against an entrance loss, an exit loss and the
#: channel friction over the stack (see that function).  Two effects the balance
#: does NOT carry pull in opposite directions and are both stated there — the
#: full free-stream head is assumed available at the inlet (optimistic), while
#: the rotor's own disc pumping, which adds flow, is not counted (pessimistic).
#:
#: 1.0 = the balance as solved.  IT AWAITS CALIBRATION against the owner's
#: thermal photographs of the open Ø50 machine (magnet surface temperature at a
#: known wash speed and current); until he gives a number this multiplier is not
#: a measured value and must not be quoted as one.
OPEN_GAP_FLOW_CALIBRATION = 1.0

#: Multiplier on the rotor end-face film (``rotor_end_faces_open``).
#:
#: Same status and the same reason: the rotating-disc and flat-plate
#: correlations are textbook averages for clean isolated geometries, and a rotor
#: face a millimetre from an end plate with standoff pins across the stream is
#: neither.  1.0 = the correlations as published; AWAITING CALIBRATION against
#: the owner's measurements.
OPEN_ROTOR_FACE_H_CALIBRATION = 1.0

#: Entrance loss coefficient of the gap's inlet [-] — a sharp-edged annular
#: opening, the standard K = 0.5.
GAP_ENTRANCE_K = 0.5
#: Exit loss coefficient [-] — the stream discharges its whole velocity head
#: into the room, K = 1.0.
GAP_EXIT_K = 1.0

#: Where the free disc goes turbulent: Re_ω = ω·R²/ν (Cobb & Saunders).
DISC_RE_OMEGA_TURBULENT = 2.4e5


def rotating_disc_nu(re_omega: float, pr: float = _AIR_PR) -> Tuple[float, str]:
    """(Nu, regime) for a disc ROTATING in otherwise still air, on its radius.

    The free-disc correlations, average over the face::

        Re_ω < 2.4e5   Nu = 0.36 · Re_ω^(1/2)        (laminar, Cobb & Saunders)
        Re_ω ≥ 2.4e5   Nu = 0.015 · Re_ω^(4/5)       (turbulent, Dorfman)

    with ``Re_ω = ω·R²/ν`` and ``Nu = h·R/k`` — both on the disc RADIUS, which
    is the length the boundary layer grows along.  Pr enters only through the
    fluid; for air over the range that matters the two forms above are quoted at
    Pr ≈ 0.7 and carrying an explicit Pr^(1/3) would be a precision the
    correlations do not have, so ``pr`` is accepted, reported and not applied.

    NO RADIATION, the same omission ``rotating_cylinder_h`` states: an open
    rotor face sees the end plate, the propeller and whatever the machine is
    bolted to, and a view factor of 1 to the room would over-read it.
    """
    re = max(float(re_omega), 0.0)
    if re <= 0.0:
        return 0.0, "at rest"
    if re < DISC_RE_OMEGA_TURBULENT:
        return 0.36 * re ** 0.5, "rotating disc (laminar)"
    return 0.015 * re ** 0.8, "rotating disc (turbulent)"


def flat_plate_forced_nu(re_l: float, pr: float) -> Tuple[float, str]:
    """(Nu, regime) for a FLAT PLATE in parallel forced flow, average over L.

        Re_L < 5e5    Nu = 0.664 · Re_L^(1/2) · Pr^(1/3)          (Blasius)
        Re_L ≥ 5e5    Nu = (0.037 · Re_L^(4/5) − 871) · Pr^(1/3)  (mixed)

    The characteristic length is the distance the flow runs along the face — for
    a rotor end face in an axial stream that is the face's DIAMETER, which is
    what the caller passes.  The mixed form's −871 is the laminar leading edge
    subtracted off, so the two branches meet at Re 5e5 rather than stepping.
    """
    re = max(float(re_l), 0.0)
    p = max(float(pr), 1e-6)
    if re <= 1.0:
        return 0.0, "no flow"
    if re < 5.0e5:
        return 0.664 * re ** 0.5 * p ** (1.0 / 3.0), "flat plate (laminar)"
    return max(0.037 * re ** 0.8 - 871.0, 0.0) * p ** (1.0 / 3.0), \
        "flat plate (mixed laminar/turbulent)"


def rotor_end_faces_open(*, air_speed_mps: float, rpm: float, t_wall_c: float,
                         t_ambient_c: float, area_m2: float, radius_m: float,
                         n_faces: int = 2, name: str = "rotor end face",
                         props: Optional[FluidProps] = None) -> Dict[str, Any]:
    """A rotor axial face of an OPEN machine, as a lumped conductance [W/K].

    ``area_m2`` is ONE face's exposed area (measured on the cross-section by the
    caller — the rotor iron's annulus, or the magnets' own end sections),
    ``radius_m`` the rotor's outside radius and ``n_faces`` how many ends are in
    the stream.  The output is the same shape ``end_face_still`` returns, so the
    caller's sink bookkeeping and its payload block do not have to know which of
    the two films it asked for.

    TWO MECHANISMS, and the LARGER is taken:

      * the face is a DISC spinning at the machine's speed — ``rotating_disc_nu``
        on ``Re_ω = ω·R²/ν``, ``h = Nu·k/R``.  This one is there whenever the
        machine turns, wash or no wash, and it is why an open rotor is never at
        the still-air floor;
      * the face is a PLATE in the propeller stream — ``flat_plate_forced_nu`` on
        ``Re_D = v·2R/ν``, ``h = Nu·k/(2R)``.

    Taking ``max`` rather than a sum is the deliberate under-read this module
    takes everywhere: the two boundary layers occupy the same face and the
    superposition of a rotating and a cross-flow layer is weaker than the sum of
    the two (the usual mixed-convection blend, ``(h₁ⁿ + h₂ⁿ)^(1/n)``, sits
    between them).  Below both sits ``NATURAL_CONVECTION_H``, the same floor the
    other two open-frame paths carry.

    NO RADIATION — see ``rotating_disc_nu``.  ``OPEN_ROTOR_FACE_H_CALIBRATION``
    multiplies the result and is 1.0 AWAITING the owner's measurements.
    """
    a = max(float(area_m2), 0.0)
    n = max(int(n_faces), 0)
    r = max(float(radius_m), 1e-4)
    t_film = 0.5 * (float(t_wall_c) + float(t_ambient_c))
    p = props or air_properties(t_film)
    v = max(float(air_speed_mps), 0.0)
    omega = abs(float(rpm)) * 2.0 * math.pi / 60.0
    re_omega = omega * r * r / max(p.nu, 1e-12)
    nu_d, reg_d = rotating_disc_nu(re_omega, p.pr)
    h_disc = nu_d * p.k / r
    re_l = v * 2.0 * r / max(p.nu, 1e-12)
    nu_p, reg_p = flat_plate_forced_nu(re_l, p.pr)
    h_plate = nu_p * p.k / (2.0 * r)
    if h_disc >= h_plate:
        h_forced, regime, nu, re = h_disc, reg_d, nu_d, re_omega
    else:
        h_forced, regime, nu, re = h_plate, reg_p, nu_p, re_l
    h_forced *= max(float(OPEN_ROTOR_FACE_H_CALIBRATION), 0.0)
    h = max(h_forced, NATURAL_CONVECTION_H)
    if h_forced <= NATURAL_CONVECTION_H:
        regime = "at rest" if (v <= 0.0 and omega <= 0.0) else "natural (floor)"
    g = h * a * n
    dt = float(t_wall_c) - float(t_ambient_c)
    if a <= 0.0 or n <= 0:
        note = (f"no exposed {name} area — this path is off (the part is not in "
                f"this cross-section, or its ends are covered)")
    else:
        note = (f"{n} × {a * 1e4:.1f} cm² of {name} in the wash: rotating disc "
                f"at {abs(float(rpm)):.0f} rpm (Re_ω {re_omega:.2e}, h "
                f"{h_disc:.0f}) vs flat plate at {v:.1f} m/s (Re_D "
                f"{re_l:.2e}, h {h_plate:.0f}) → {regime}, h {h:.0f} W/m²·K → "
                f"{g:.3f} W/K.  No radiation (the face sees the end plate and "
                f"the propeller, not the room).  Films AWAIT CALIBRATION "
                f"against the owner's thermal photographs.")
    return {
        "mode": ("forced" if (a > 0.0 and n > 0) else "off"),
        "film_kind": "forced",
        "name": str(name),
        "G_W_per_K": float(g),
        "h_conv": float(h),
        "h_rad": 0.0,
        "h_total": float(h),
        "h_disc": float(h_disc),
        "h_plate": float(h_plate),
        "re": float(re),
        "re_omega": float(re_omega),
        "re_plate": float(re_l),
        "nu": float(nu),
        "regime": regime,
        "orientation": "axial face in cross-flow",
        "area_m2": float(a),
        "area_total_m2": float(a * n),
        "char_len_m": float(2.0 * r),
        "n_faces": n,
        "emissivity": 0.0,
        "air_speed_mps": float(v),
        "rpm": float(rpm),
        "t_wall_c": float(t_wall_c),
        "t_film_c": float(t_film),
        "t_sink_c": float(t_ambient_c),
        "heat_removed_W": float(g * dt),
        "convection_W": float(g * dt),
        "radiation_W": 0.0,
        "calibration": float(OPEN_ROTOR_FACE_H_CALIBRATION),
        "note": note,
    }


def gap_axial_flow(*, air_speed_mps: float, r_rotor_m: float, r_bore_m: float,
                   length_m: float, t_air_c: float, t_ambient_c: float,
                   rpm: float = 0.0, extra_area_m2: float = 0.0,
                   props: Optional[FluidProps] = None) -> Dict[str, Any]:
    """The air gap of an OPEN machine as a short annular DUCT the wash blows
    through — the through-velocity, the mass flow and what it can carry.

    THE VELOCITY IS SOLVED, NOT ASSUMED.  The free stream arrives at the gap
    mouth with a dynamic head ½ρv_wash², and the gap spends it on an entrance
    loss, the channel friction over the stack and the exit::

        ½ρv_wash² = (K_in + K_out + f_D·L/D_h) · ½ρv_gap²
        v_gap     = v_wash / sqrt(K_in + K_out + f_D·L/D_h)

    with ``D_h = 2δ`` (the annulus's hydraulic diameter, δ = r_bore − r_rotor),
    ``f_D = 64/Re`` laminar and ``0.316·Re^(-1/4)`` turbulent, and ``Re =
    v_gap·D_h/ν`` — so the balance is implicit and is iterated to a per-mille.
    On the Ø50 machine (δ 0.25 mm, 15 mm of stack, 40 m/s wash) that is L/D_h
    ≈ 30 and a solved v_gap of a few m/s, NOT the free-stream speed — which is
    exactly why it is solved.

    TWO EFFECTS THE BALANCE DOES NOT CARRY, and they pull opposite ways:

      * the FULL free-stream head is taken as available at the inlet, i.e. the
        gap mouth is treated as a stagnation region discharging to static
        ambient.  That is the optimistic end;
      * the rotor's own DISC PUMPING — a spinning rotor drags air through its
        own clearance and adds to this flow — is not counted at all.  That is
        the pessimistic end.

    ``OPEN_GAP_FLOW_CALIBRATION`` multiplies the solved velocity and is 1.0
    AWAITING the owner's measurements; neither of the two effects above is
    tuned into it.

    WHAT IT RETURNS IS AN ENTHALPY CONDUCTANCE.  Fresh air enters at ambient and
    leaves at T_out, so in steady state the channel removes ``ṁ·cp·(T_out −
    T_in)``; taking the air's MEAN temperature as (T_in + T_out)/2 — a linear
    rise along a channel with a roughly constant wall — that is

        Q = 2·ṁ·cp·(T_mean − T_ambient)     →    G = 2·ṁ·cp

    and ``G_W_per_K`` is that number, to be applied to the gap AIR's own mean
    temperature.  The wall→air film is NOT in it: in a 0.25 mm clearance the
    air is a quarter of a millimetre from both walls and the meshed gap air
    already carries that resistance by conduction, so folding a film in here
    would count it twice.  ``k_eff_axial`` is the transverse conductivity the
    axial flow is worth (Nu_D on D_h, expressed over the clearance as
    ``Nu_D·k/2``) so the caller can take the larger of it and the
    Taylor–Couette value instead of adding the two.

    ``extra_area_m2`` is free flow cross-section BESIDE the clearance that the
    same stream passes through — on a rotor whose magnet pockets are open to the
    OD (``rotor_hole`` > 0) the recess above each magnet (``magnet_up_gap``) is
    part of this channel, and the caller measures it on the mesh.
    """
    delta = max(float(r_bore_m) - float(r_rotor_m), 1e-6)
    d_h = 2.0 * delta
    L = max(float(length_m), 1e-6)
    v_wash = max(float(air_speed_mps), 0.0)
    p = props or air_properties(t_air_c)
    a_cs = max(math.pi * (float(r_bore_m) ** 2 - float(r_rotor_m) ** 2), 0.0)
    a_cs += max(float(extra_area_m2), 0.0)

    v_gap, re, f_d, regime = 0.0, 0.0, 0.0, "no through-flow"
    if v_wash > 0.0 and a_cs > 0.0:
        v_gap = v_wash                      # seed: the frictionless limit
        for _ in range(60):
            re = max(v_gap * d_h / max(p.nu, 1e-12), 1e-6)
            if re < 2300.0:
                f_d, regime = 64.0 / re, "laminar duct"
            else:
                f_d, regime = 0.316 * re ** -0.25, "turbulent duct"
            sigma = GAP_ENTRANCE_K + GAP_EXIT_K + f_d * L / d_h
            v_new = v_wash / math.sqrt(max(sigma, 1e-9))
            if abs(v_new - v_gap) <= 1e-3 * max(v_new, 1e-9):
                v_gap = v_new
                break
            v_gap = 0.5 * (v_gap + v_new)   # damped: the balance is stiff at
        re = max(v_gap * d_h / max(p.nu, 1e-12), 0.0)   # small L/D_h
        v_gap *= max(float(OPEN_GAP_FLOW_CALIBRATION), 0.0)

    m_dot = p.rho * a_cs * v_gap
    g = 2.0 * m_dot * p.cp
    nu_d, nu_regime = (pipe_nusselt(re, p.pr) if (v_gap > 0.0 and re > 0.0)
                       else (0.0, "no through-flow"))
    return {
        "mode": ("through-flow" if g > 0.0 else "off"),
        "film_kind": "forced",
        "G_W_per_K": float(g),
        "m_dot_kg_s": float(m_dot),
        "air_speed_mps": float(v_wash),
        "gap_speed_mps": float(v_gap),
        "speed_fraction": float(v_gap / v_wash) if v_wash > 0.0 else 0.0,
        "re": float(re),
        "friction_factor": float(f_d),
        "regime": regime,
        "nu_duct": float(nu_d),
        "nu_regime": nu_regime,
        "k_eff_axial": float(nu_d * p.k / 2.0),
        "hydraulic_diameter_m": float(d_h),
        "clearance_m": float(delta),
        "cross_section_m2": float(a_cs),
        "extra_area_m2": float(max(float(extra_area_m2), 0.0)),
        "length_m": float(L),
        "L_over_Dh": float(L / d_h),
        "entrance_k": float(GAP_ENTRANCE_K),
        "exit_k": float(GAP_EXIT_K),
        "t_air_c": float(t_air_c),
        "t_sink_c": float(t_ambient_c),
        "rpm": float(rpm),
        "calibration": float(OPEN_GAP_FLOW_CALIBRATION),
        "note": (
            f"the clearance as a {L * 1e3:.1f} mm annular duct, δ "
            f"{delta * 1e3:.3f} mm (D_h {d_h * 1e3:.3f} mm, L/D_h "
            f"{L / d_h:.1f}), free area {a_cs * 1e6:.2f} mm²"
            + (f" (of which {max(float(extra_area_m2), 0.0) * 1e6:.2f} mm² is "
               f"the open magnet recess)" if extra_area_m2 > 0.0 else "")
            + "; "
            + (f"{v_wash:.1f} m/s of wash spends its head on K_in "
               f"{GAP_ENTRANCE_K} + K_out {GAP_EXIT_K} + {regime} friction "
               f"f {f_d:.3f} → v_gap {v_gap:.2f} m/s (Re {re:.0f}), "
               f"ṁ {m_dot * 1e3:.3f} g/s → G = 2·ṁ·cp = {g:.4f} W/K on the "
               f"gap air's MEAN temperature.  The full free-stream head is "
               f"assumed at the inlet and the rotor's own disc pumping is NOT "
               f"counted; the velocity AWAITS CALIBRATION."
               if g > 0.0 else
               "no wash, so nothing is blown through the clearance and the gap "
               "is the closed Taylor-Couette conductor it has always been")),
    }


# ---------------------------------------------------------------------------
# The air gap
# ---------------------------------------------------------------------------

def taylor_couette_gap(*, rpm: float, r_rotor_m: float, r_bore_m: float,
                       t_gap_c: float) -> Dict[str, Any]:
    """Effective cross-gap conductivity of the rotating air gap (Becker–Kaye).

    At rest the gap is still-air conduction.  As the rotor spins, Taylor
    vortices stir it and the effective radial conductivity rises; the Taylor
    number Ta = ω²·r̄·δ³/ν² decides which of the three regimes the gap is in.

    TWO THINGS THIS FUNCTION IS STRICT ABOUT, both of which were wrong before:

      * ``r_rotor_m`` is the TRUE rotor outside diameter — INCLUDING a retaining
        sleeve.  δ is the mechanical clearance the rotor actually turns in
        (air_gap − sleeve_thickness), not the iron-to-bore distance.  On the
        200 mm machine that is 3.6 mm vs 0.6 mm of real clearance, and δ³ means
        the old reading was 200× out.
      * the air properties are evaluated at a STATED gap temperature rather than
        at a hardcoded 75 °C, and ``T_gap_c`` comes back in the payload so the
        number can be argued with.
    """
    props = air_properties(t_gap_c)
    delta = max(float(r_bore_m) - float(r_rotor_m), 1e-6)
    r_mean = max(0.5 * (float(r_bore_m) + float(r_rotor_m)), 1e-4)
    omega = abs(float(rpm)) * 2.0 * math.pi / 60.0
    ta = (omega ** 2) * r_mean * (delta ** 3) / (props.nu ** 2)
    if ta < 1700.0:
        nu_g, regime = 1.0, "conduction"
    elif ta <= 1.0e4:
        nu_g, regime = 0.128 * ta ** 0.367, "transitional"
    else:
        nu_g, regime = 0.409 * ta ** 0.241, "turbulent"
    k_eff = max(nu_g * props.k, props.k)
    return {
        "k_eff": round(float(k_eff), 4),
        "k_air": round(float(props.k), 4),
        "Ta": round(float(ta), 0),
        "Nu": round(float(nu_g), 3),
        "delta_mm": round(delta * 1e3, 4),
        "r_mean_mm": round(r_mean * 1e3, 3),
        "regime": regime,
        "T_gap_c": round(float(t_gap_c), 1),
        "note": ("Becker–Kaye Taylor–Couette; δ is the MECHANICAL clearance "
                 "(stator bore − rotor OD including any retaining sleeve), air "
                 f"properties at {t_gap_c:.0f} °C"),
    }
