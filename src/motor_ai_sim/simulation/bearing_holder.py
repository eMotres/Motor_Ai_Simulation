"""The rotor's AXIAL heat path: shaft stub → bearing → holder → air / stator.

WHY THIS MODULE EXISTS (owner, 2026-10-19: «подшипники не греются так же как
обмотка» — the bearings do not run as hot as the winding)
=========================================================================
Until today a coupled run reported the bearing at the ROTOR's temperature: the
"seat" was the shaft's mean over the 2-D cross-section of the active stack, with
a 0 K drop to the bearing, and on the open-frame drone motors (shaftExtMm = 0,
no bore cooling) the shaft had no axial path at all — so the shaft simply WAS
the rotor, and the 618/8-2Z of the CIANO14 40 read 113-146 °C.

The real machine puts the bearings in ALUMINIUM HOLDERS a few millimetres off
the stack: the inner ring sits on a thin shaft journal, the outer ring in the
holder, and the holder is screwed to the stator core and stands in the
propeller wash.  This module turns that into a small THERMAL NETWORK the 2-D
conduction solve carries as lumped nodes (``thermal_solver_2d``'s
``lumped_nodes`` / ``node_links``), so the bearing temperatures are SOLVED, not
read off the rotor, and the rotor gains the axial door it physically has.

Per bearing end (A, B), in series from the 2-D shaft to the room::

    shaft (2-D, mean)  ─R_shaft─  inner ring ─R_ir─ balls (q) ─R_or─ outer ring
         ─R_fit,o─  holder ─┬─ film (wash / still air) ─ ambient
                            └─ arms + pad contact ─ stator core (2-D, mean)

plus the ring-to-ring path through the air in the bearing (``R_annulus``).

EVERY RESISTANCE CARRIES ITS SOURCE (``network(...)["resistances"]``), and
nothing here is tuned to a measurement:

  * ``R_shaft`` — 1-D conduction ``Σ L_i/(k·A_i)`` along the shaft from the
    stack's mean-temperature point (stack/6 into the stack, the uniform-heating
    half-stack centroid — the convention ``cooling_models.bearing_path``
    already uses) to the bearing centre.  Lengths and sections from the CAD
    when the configuration carries them (``bearings.thermal_path``), else one
    journal-section segment of ``BEARING_STANDOFF_M`` + B/2.  k from the
    shaft's materials-library card.
  * ``R_fit`` (inner ring on the journal, outer ring in the holder) — a
    contact conductance ``1/R''`` over the seat area π·d·B / π·D·B with
    ``R'' = 2.75e-4 m²K/W``: Incropera, DeWitt, Bergman & Lavine,
    *Fundamentals of Heat and Mass Transfer*, 6th ed., Table 3.2 (metallic
    interface, 10 µm roughness, 10⁵ N/m², air in the interstices).
  * ``R_ir``, ``R_or`` (ring ↔ balls) — the circuit of Hoffmann, Silys &
    Doppelbauer, "Transient Thermal Model for Ball Bearings in Electrical
    Machines", ICEM 2020, pp. 1018-1024 (contacts + ball + internal convection;
    friction heat 50 % into the balls, 25 % into each contact region).  Each
    ball-race contact is the Hertzian constriction (Yovanovich's elliptical
    contact, equivalent radius √(a·b), ``R = 1/(2·k·√(a·b))`` for two
    half-spaces of equal k; the contact ellipse from Hamrock & Brewe,
    "Simplified solution for stresses and deformations", J. Lubr. Technol.
    105 (1983) 171-177) IN PARALLEL with conduction through the lubricant /
    air wedge around it, ``∫∫ k/h dA`` over the gap
    ``h = h0 + x²/2R_x + y²/2R_y`` of the conforming groove (curvature sums and
    the typical conformities f_i 0.52 / f_o 0.53 from Harris & Kotzalas,
    *Rolling Bearing Analysis*, 5th ed., 2007, ch. 2-6).  On a LIGHTLY loaded
    miniature bearing (the 40 mm rotor weighs 0.36 N) the Hertz spot is a few
    µm and the wedge carries almost all of it — which is why the lubricant
    meniscus, not the load, sets the number.  The meniscus extent
    (``WEDGE_MENISCUS_M``) and the gap floor (``WEDGE_H0_M``) are stated
    assumptions; the answer moves with their LOGARITHM.
  * ``R_ball`` — conduction across the ball body between its two contact
    zones, ``1/(k·D_w)`` (a sphere between two broad polar caps, shape factor
    of order one; Hoffmann's FEA puts the STATIC ball resistance in the same
    range and shows rotation LOWERS it, so the static value is the
    conservative one).
  * ``R_annulus`` — the air between the ring lands, a Taylor–Couette annulus
    (``cooling_models.taylor_couette_gap``) over the bearing width.
  * holder conduction — 1-D ``Σ L/(k·A)`` along the arms (CAD sections), then
    the pad contact on the core end face (same Incropera R'').
  * holder film — forced convection in the propeller wash
    (``cooling_models.flat_plate_forced_nu`` on the holder's envelope, the
    conservative length) or the still-air + radiation pair
    (``cooling_models.end_face_still``) in the still / robotics modes.

THE BEARING IS ADVISORY.  Its temperature is reported, it drives the grease
viscosity in the friction model, and ``time_to_limit.advisories`` flags it over
the lubricant's limit; it never limits rated (owner rule, 2026-10-19).

Everything here is PURE: numbers in, numbers out, no config, no FEM.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from motor_ai_sim.simulation import cooling_models as cm

#: Thermal contact resistance of a dry metallic joint with air in the
#: interstices [m²·K/W]: Incropera et al., Fundamentals of Heat and Mass
#: Transfer, 6th ed., Table 3.2 (10 µm roughness, 10⁵ N/m², air).  Used for the
#: two ring fits and the holder's pads on the core.
CONTACT_R_M2K_W = 2.75e-4

#: Bearing steel (100Cr6 / 52100) [W/m·K] and its elastic constants (Harris &
#: Kotzalas, Table 5.1): E = 208 GPa, ν = 0.3.
K_BEARING_STEEL = 42.0
E_BEARING_STEEL = 2.08e11
NU_BEARING_STEEL = 0.30

#: Groove conformities r_groove / D_w of a deep-groove ball bearing — the
#: typical values Harris & Kotzalas quote (inner 0.515-0.52, outer
#: 0.525-0.53); the ball-race wedge is set by them.
CONFORMITY_INNER = 0.52
CONFORMITY_OUTER = 0.53

#: Thermal conductivity of the base oil in the meniscus around a running
#: contact [W/m·K] — mineral / ester base oils 0.13-0.15 (MT47's base oil is
#: mineral); the grease reservoir itself is pushed aside by the balls.
K_OIL = 0.14

#: The wedge model's two stated assumptions.  The gap floor ``WEDGE_H0_M`` is
#: the composite roughness + film at the rim of the Hertz zone (1 µm); the oil
#: meniscus wets the wedge out to a gap of ``WEDGE_MENISCUS_M`` (50 µm), and
#: beyond it the wedge is air.  G ∝ ln(h_men/h0): halving or doubling either
#: moves the bearing resistance by roughly ±15 %.
WEDGE_H0_M = 1.0e-6
WEDGE_MENISCUS_M = 50.0e-6

#: The groove's half-width across the rolling direction, as a fraction of the
#: ball diameter (shoulder depth ≈ 0.2·D_w on a deep-groove ring with the
#: conformities above gives √(f²−(f−0.2)²)·D_w ≈ 0.41·D_w).
GROOVE_HALF_WIDTH_FRAC = 0.41

#: Ball count and diameter when the card does not state them: D_w ≈ 0.3·(D−d)
#: (Harris & Kotzalas' deep-groove proportion) and z = floor(0.75·π·d_m/D_w)
#: (cage pitch ≈ 1.33·D_w).  An ESTIMATE, flagged in the payload; a card that
#: knows its balls says so (``n_balls``, ``ball_d``).
BALL_D_FRAC = 0.30
BALL_PITCH_FILL = 0.75

#: Stack-end to bearing-centre stand-off when no CAD says (same 5 mm
#: ``cooling_models.bearing_path`` has always used).
BEARING_STANDOFF_M = cm.BEARING_STANDOFF_M

#: Thermal conductivity of a holder by its material family [W/m·K], used when
#: the holder's material has no thermal card.  Aluminium 6061-T6: 167.
K_ALUMINIUM_6061 = 167.0


# ---------------------------------------------------------------------------
# One ball-race contact
# ---------------------------------------------------------------------------

def _hertz_ellipse(f_n: float, r_x: float, r_y: float,
                   e_prime: float) -> Tuple[float, float]:
    """Semi-axes (a across, b along the rolling direction) [m] of the Hertz
    ellipse — Hamrock & Brewe (1983) simplified forms."""
    if f_n <= 0.0:
        return 0.0, 0.0
    r_eq = 1.0 / (1.0 / r_x + 1.0 / r_y)
    alpha = max(r_y / r_x, 1.0)
    k_ell = 1.0339 * alpha ** 0.6360
    e_int = 1.0003 + 0.5968 / alpha
    a = (6.0 * k_ell ** 2 * e_int * f_n * r_eq / (math.pi * e_prime)) ** (1.0 / 3.0)
    b = (6.0 * e_int * f_n * r_eq / (math.pi * k_ell * e_prime)) ** (1.0 / 3.0)
    return a, b


def wedge_conductance(*, r_x: float, r_y: float, y_max: float,
                      x_max: float, k_air: float,
                      k_oil: Optional[float] = None,
                      h0: Optional[float] = None,
                      h_men: Optional[float] = None,
                      n: int = 160) -> float:
    """``∫∫ k(h)/h dA`` [W/K] over the gap ``h = h0 + x²/2R_x + y²/2R_y``.

    The gap is conducted STRAIGHT across (each column of fluid on its own — the
    usual gap-conductance integral for a sphere near a surface), oil out to
    ``h_men`` and air beyond, over |y| ≤ ``y_max`` (the groove) and |x| ≤
    ``x_max``.  A midpoint rule on a stretched grid; the integrand is smooth
    and the answer converges to 0.1 % at n = 160.
    """
    k_oil = K_OIL if k_oil is None else float(k_oil)
    h0 = WEDGE_H0_M if h0 is None else float(h0)
    h_men = WEDGE_MENISCUS_M if h_men is None else float(h_men)
    tot = 0.0
    # stretch towards the contact: u in (0,1] → x = x_max·u²
    for i in range(n):
        ux0, ux1 = i / n, (i + 1) / n
        x0, x1 = x_max * ux0 * ux0, x_max * ux1 * ux1
        xm, dx = 0.5 * (x0 + x1), x1 - x0
        for j in range(n):
            uy0, uy1 = j / n, (j + 1) / n
            y0, y1 = y_max * uy0 * uy0, y_max * uy1 * uy1
            ym, dy = 0.5 * (y0 + y1), y1 - y0
            h = h0 + xm * xm / (2.0 * r_x) + ym * ym / (2.0 * r_y)
            k = k_oil if h <= h_men else k_air
            tot += k / h * dx * dy
    return 4.0 * tot                      # four quadrants


def _contact(*, kind: str, d_w: float, d_m: float, f_n: float,
             k_s: float, k_air: float, conformity: float,
             g_wedge: Optional[float] = None) -> Dict[str, Any]:
    """One ball on one race: Hertz constriction ∥ lubricant/air wedge.

    ``g_wedge`` may be passed in: it depends on the geometry only, so a caller
    walking all the balls computes it once."""
    r_ball = 0.5 * d_w
    if kind == "inner":
        r_race = 0.5 * (d_m - d_w)
        inv_rx = 1.0 / r_ball + 1.0 / r_race
    else:
        r_race = 0.5 * (d_m + d_w)
        inv_rx = 1.0 / r_ball - 1.0 / r_race
    inv_ry = 1.0 / r_ball - 1.0 / (conformity * d_w)
    r_x, r_y = 1.0 / inv_rx, 1.0 / inv_ry
    e_prime = E_BEARING_STEEL / (1.0 - NU_BEARING_STEEL ** 2)
    a, b = _hertz_ellipse(f_n, r_x, r_y, e_prime)
    g_hertz = 2.0 * k_s * math.sqrt(a * b) if a > 0.0 else 0.0
    if g_wedge is None:
        g_wedge = wedge_conductance(
            r_x=r_x, r_y=r_y, y_max=GROOVE_HALF_WIDTH_FRAC * d_w,
            x_max=0.5 * d_w, k_air=k_air)
    return {"r_x_m": r_x, "r_y_m": r_y, "a_m": a, "b_m": b, "F_N": f_n,
            "G_hertz_W_per_K": g_hertz, "G_wedge_W_per_K": g_wedge,
            "G_W_per_K": g_hertz + g_wedge}


# ---------------------------------------------------------------------------
# One bearing
# ---------------------------------------------------------------------------

def ball_set(card: Mapping[str, Any]) -> Dict[str, Any]:
    """``{n_balls, ball_d_mm, source}`` — the card's own, or the stated estimate."""
    d = float(card.get("d") or 0.0)
    D = float(card.get("D") or 0.0)
    dm = float(card.get("d_m") or 0.5 * (d + D))
    nb, bd = card.get("n_balls"), card.get("ball_d")
    if nb and bd:
        return {"n_balls": int(nb), "ball_d_mm": float(bd), "d_m_mm": dm,
                "source": "bearing card"}
    bd_e = BALL_D_FRAC * (D - d)
    nb_e = max(int(math.floor(BALL_PITCH_FILL * math.pi * dm / max(bd_e, 1e-6))), 6)
    return {"n_balls": int(nb or nb_e), "ball_d_mm": float(bd or bd_e),
            "d_m_mm": dm,
            "source": ("ESTIMATE — the card states no ball set: D_w = %.2f·(D−d), "
                       "z = ⌊%.2f·π·d_m/D_w⌋" % (BALL_D_FRAC, BALL_PITCH_FILL))}


def bearing_internal(card: Mapping[str, Any], *, rpm: float, f_r_n: float,
                     f_a_n: float = 0.0, t_c: float = 80.0) -> Dict[str, Any]:
    """Inner ring ↔ balls ↔ outer ring of ONE bearing, as conductances [W/K].

    Returns ``G_inner_balls``, ``G_balls_outer`` (all balls in parallel; each
    ball's contact = Hertz ∥ wedge, in series with half the ball body), the
    ring-to-ring ``G_annulus`` and every intermediate.  Radial load per ball
    from the Stribeck distribution F_ψ = F_max·cos^(3/2)ψ, F_max = 5·F_r/z
    (Harris & Kotzalas); an axial load is shared by all balls; the balls'
    centrifugal force loads the outer race on top.
    """
    bs = ball_set(card)
    z = int(bs["n_balls"])
    d_w = float(bs["ball_d_mm"]) * 1e-3
    d_m = float(bs["d_m_mm"]) * 1e-3
    d_bore = float(card.get("d") or 0.0) * 1e-3
    D_out = float(card.get("D") or 0.0) * 1e-3
    B = float(card.get("B") or 0.0) * 1e-3
    air = cm.air_properties(float(t_c))
    k_s = K_BEARING_STEEL
    # loads
    f_max = 5.0 * max(float(f_r_n), 0.0) / max(z, 1)
    f_ax = max(float(f_a_n), 0.0) / max(z, 1)
    omega = abs(float(rpm)) * 2.0 * math.pi / 60.0
    omega_cage = 0.5 * omega * (1.0 - d_w / d_m)
    m_ball = 7800.0 * math.pi / 6.0 * d_w ** 3
    f_cf = m_ball * omega_cage ** 2 * 0.5 * d_m
    g_in = g_out = 0.0
    g_in_h = g_out_h = g_in_w = g_out_w = 0.0
    r_ball_body = 1.0 / (k_s * d_w)
    per_ball: List[Dict[str, Any]] = []
    gw_i = _contact(kind="inner", d_w=d_w, d_m=d_m, f_n=0.0, k_s=k_s,
                    k_air=air.k, conformity=CONFORMITY_INNER)["G_wedge_W_per_K"]
    gw_o = _contact(kind="outer", d_w=d_w, d_m=d_m, f_n=0.0, k_s=k_s,
                    k_air=air.k, conformity=CONFORMITY_OUTER)["G_wedge_W_per_K"]
    for j in range(z):
        psi = 2.0 * math.pi * j / z
        c = math.cos(psi)
        f_rad = f_max * c ** 1.5 if c > 0.0 else 0.0
        f_i = f_rad + f_ax
        f_o = f_rad + f_ax + f_cf
        ci = _contact(kind="inner", d_w=d_w, d_m=d_m, f_n=f_i, k_s=k_s,
                      k_air=air.k, conformity=CONFORMITY_INNER, g_wedge=gw_i)
        co = _contact(kind="outer", d_w=d_w, d_m=d_m, f_n=f_o, k_s=k_s,
                      k_air=air.k, conformity=CONFORMITY_OUTER, g_wedge=gw_o)
        # inner ring → ball centre: contact + half the ball body
        gi = 1.0 / (1.0 / ci["G_W_per_K"] + 0.5 * r_ball_body)
        go = 1.0 / (1.0 / co["G_W_per_K"] + 0.5 * r_ball_body)
        g_in += gi
        g_out += go
        g_in_h += ci["G_hertz_W_per_K"]
        g_out_h += co["G_hertz_W_per_K"]
        g_in_w += ci["G_wedge_W_per_K"]
        g_out_w += co["G_wedge_W_per_K"]
        if j == 0:
            per_ball = [ci, co]
    # the ring lands: inner land ≈ raceway + 0.3·D_w, outer land ≈ raceway − 0.3·D_w
    r_land_i = 0.5 * (d_m - d_w) + 0.3 * d_w
    r_land_o = 0.5 * (d_m + d_w) - 0.3 * d_w
    g_ann = 0.0
    tc = None
    if r_land_o > r_land_i and B > 0.0:
        tc = cm.taylor_couette_gap(rpm=float(rpm), r_rotor_m=r_land_i,
                                   r_bore_m=r_land_o, t_gap_c=float(t_c))
        k_eff = float(tc.get("k_eff") or air.k)
        g_ann = 2.0 * math.pi * k_eff * B / math.log(r_land_o / r_land_i)
    g_total = 1.0 / (1.0 / g_in + 1.0 / g_out) + g_ann
    return {
        "card": card.get("name"),
        "n_balls": z, "ball_d_mm": round(d_w * 1e3, 3),
        "ball_set_source": bs["source"],
        "d_mm": round(d_bore * 1e3, 3), "D_mm": round(D_out * 1e3, 3),
        "B_mm": round(B * 1e3, 3), "d_m_mm": round(d_m * 1e3, 3),
        "F_r_N": float(f_r_n), "F_a_N": float(f_a_n),
        "F_ball_max_N": round(f_max, 5), "F_centrifugal_N": round(f_cf, 5),
        "G_inner_balls_W_per_K": g_in, "G_balls_outer_W_per_K": g_out,
        "G_hertz_inner_W_per_K": g_in_h, "G_hertz_outer_W_per_K": g_out_h,
        "G_wedge_inner_W_per_K": g_in_w, "G_wedge_outer_W_per_K": g_out_w,
        "R_ball_body_K_per_W_each": r_ball_body,
        "G_annulus_W_per_K": g_ann,
        "annulus_k_eff": (None if tc is None else tc.get("k_eff")),
        "R_ring_to_ring_K_per_W": 1.0 / g_total if g_total > 0 else None,
        "contact_example": per_ball,
        "model": ("Hoffmann et al. ICEM 2020 circuit; Hertz (Hamrock & Brewe "
                  "1983) constriction 1/(2k√ab) ∥ oil/air wedge ∫k/h dA over the "
                  "conforming groove (f_i %.2f, f_o %.2f; h0 %.1f µm, oil "
                  "meniscus to %.0f µm); ball body 1/(k·D_w); land annulus "
                  "Taylor–Couette" % (CONFORMITY_INNER, CONFORMITY_OUTER,
                                      WEDGE_H0_M * 1e6, WEDGE_MENISCUS_M * 1e6)),
    }


# ---------------------------------------------------------------------------
# Shaft, fits, holder
# ---------------------------------------------------------------------------

def series_conduction(segments: Sequence[Sequence[float]], k: float) -> float:
    """``Σ L_i/(k·A_i)`` [K/W] over ``[(L_m, A_m2), ...]``."""
    r = 0.0
    for seg in segments:
        L, A = float(seg[0]), float(seg[1])
        if L <= 0.0:
            continue
        if A <= 0.0:
            raise ValueError("a conduction segment of %.3g m has no section" % L)
        r += L / (max(float(k), 1e-9) * A)
    return r


def fit_conductance(area_m2: float, r_contact: float = CONTACT_R_M2K_W) -> float:
    """A press / slip fit or a bolted pad as a contact conductance [W/K]."""
    return max(float(area_m2), 0.0) / max(float(r_contact), 1e-12)


def holder_film(*, mode: str, air_speed_mps: float, t_wall_c: float,
                t_ambient_c: float, area_m2: float, char_len_m: float,
                emissivity: float = cm.EMISSIVITY_DEFAULT) -> Dict[str, Any]:
    """The holder's own film to the cooling air [W/K].

    ``mode`` ``"wash"``: forced convection on the holder's wetted area at the
    air speed, flat-plate average (Blasius / mixed) on the holder's ENVELOPE —
    the longest run the flow can have over it, so the lowest coefficient the
    shape allows (its arms are a few mm wide and really run hotter films);
    no radiation (the holder sees the propeller and the machine, not the room).
    ``"still"``: natural convection + radiation on a vertical face
    (``cooling_models.end_face_still``).  Never below
    ``NATURAL_CONVECTION_H``.
    """
    a = max(float(area_m2), 0.0)
    lc = max(float(char_len_m), 1e-4)
    if mode == "wash" and float(air_speed_mps) > 0.0:
        p = cm.air_properties(0.5 * (float(t_wall_c) + float(t_ambient_c)))
        re = float(air_speed_mps) * lc / p.nu
        nu, regime = cm.flat_plate_forced_nu(re, p.pr)
        h = max(nu * p.k / lc, cm.NATURAL_CONVECTION_H)
        return {"mode": "wash", "h": h, "re": re, "nu": nu, "regime": regime,
                "G_W_per_K": h * a, "area_m2": a, "char_len_m": lc,
                "air_speed_mps": float(air_speed_mps),
                "source": ("flat plate in the wash at %.1f m/s on the %.0f mm "
                           "envelope (Re %.0f, %s) → h %.0f W/m²K × %.1f cm²"
                           % (float(air_speed_mps), lc * 1e3, re, regime, h,
                              a * 1e4))}
    f = cm.end_face_still(t_wall_c=float(t_wall_c), t_ambient_c=float(t_ambient_c),
                          area_m2=a, char_len_m=lc, emissivity=emissivity,
                          n_faces=1, name="bearing holder")
    return {"mode": "still", "h": float(f["h_total"]), "re": 0.0,
            "nu": float(f["nu"]), "regime": "still air + radiation",
            "G_W_per_K": float(f["G_W_per_K"]), "area_m2": a,
            "char_len_m": lc, "air_speed_mps": 0.0,
            "source": "still air + radiation (end_face_still): " + str(f["note"])}


# ---------------------------------------------------------------------------
# The geometry of ONE end, from the configuration's CAD block or the defaults
# ---------------------------------------------------------------------------

def end_geometry(*, end: str, card: Mapping[str, Any],
                 cad: Optional[Mapping[str, Any]], stack_m: float,
                 shaft_area_stack_m2: float, shaft_bore_d_m: float,
                 stator_od_m: float, stator_bore_m: float) -> Dict[str, Any]:
    """Shaft segments, holder sections and areas for one end [SI].

    ``cad`` is the configuration's ``bearings.thermal_path.<end>`` block
    (millimetres), measured on the assembly; absent keys fall back to the
    stated defaults below and the payload says which ones did.
    """
    cad = dict(cad or {})
    d = float(card.get("d") or 0.0) * 1e-3
    D = float(card.get("D") or 0.0) * 1e-3
    B = float(card.get("B") or 0.0) * 1e-3
    a_journal = max(0.25 * math.pi * (d * d - min(shaft_bore_d_m, 0.95 * d) ** 2),
                    1e-9)
    src: Dict[str, str] = {}
    # the shaft, from the stack's mean point to the bearing centre
    segs = [(stack_m / 6.0, shaft_area_stack_m2)]
    if cad.get("shaft_segments_mm"):
        segs += [(float(L) * 1e-3, float(A) * 1e-6)
                 for L, A in cad["shaft_segments_mm"]]
        src["shaft"] = "CAD: stack/6 in the stack + %d measured segment(s)" % (
            len(cad["shaft_segments_mm"]))
    else:
        segs += [(BEARING_STANDOFF_M + 0.5 * B, a_journal)]
        src["shaft"] = ("DEFAULT: stack/6 in the stack + %.1f mm stand-off + B/2 "
                        "at the journal section π/4·(d² − bore²)"
                        % (BEARING_STANDOFF_M * 1e3))
    # the holder
    h = dict(cad.get("holder") or {})
    od = stator_od_m
    if h:
        contact = float(h.get("contact_core_mm2") or 0.0) * 1e-6
        arms = [(float(L) * 1e-3, float(A) * 1e-6)
                for L, A in (h.get("arm_segments_mm") or [])]
        film_a = float(h.get("film_area_mm2") or 0.0) * 1e-6
        lc = float(h.get("char_len_mm") or od * 1e3) * 1e-3
        src["holder"] = "CAD: " + str(h.get("source") or "measured on the assembly")
    else:
        # DEFAULT holder: a solid aluminium end plate, thickness B, from the
        # bearing seat to the stator OD, pressed on the stator yoke over a
        # tenth of the yoke annulus.  A placeholder that is SAID to be one.
        r_seat = 0.5 * D
        r_od = 0.5 * od
        # the pads sit on the outer half of the stator's end face: from the
        # mid radius between the bore and the OD (both given as DIAMETERS)
        r_yoke = (0.25 * (stator_bore_m + od) if stator_bore_m > 0
                  else 0.75 * r_od)
        contact = 0.1 * math.pi * max(r_od ** 2 - r_yoke ** 2, 0.0)
        r_mid = 0.5 * (r_seat + r_yoke)
        arms = [(max(r_yoke - r_seat, 1e-3), 2.0 * math.pi * r_mid * max(B, 1e-3))]
        film_a = 2.0 * math.pi * max(r_od ** 2 - r_seat ** 2, 0.0)
        lc = od
        src["holder"] = ("DEFAULT (no CAD holder on this configuration): solid "
                         "aluminium end plate B thick from the bearing seat to "
                         "the stator OD, both faces wetted, pads on 10 % of the "
                         "yoke annulus")
    k_h = float(h.get("k_W_per_mK") or K_ALUMINIUM_6061)
    return {
        "end": end,
        "shaft_segments_m": segs,
        "journal_section_m2": a_journal,
        "inner_fit_area_m2": math.pi * d * B,
        "outer_fit_area_m2": math.pi * D * B,
        "holder_contact_m2": contact,
        "holder_arm_segments_m": arms,
        "holder_k": k_h,
        "holder_film_area_m2": film_a,
        "holder_char_len_m": lc,
        "sources": src,
    }


# ---------------------------------------------------------------------------
# The friction's equivalent sources (re-audit R2, 2026-10-20)
# ---------------------------------------------------------------------------

#: The share of a bearing's friction injected into each internal node
#: (Hoffmann et al. 2020): inner ring, balls, outer ring.
FRICTION_SPLIT = (0.25, 0.50, 0.25)


def source_split(p_w: float, *, g_shaft_in: float, g_inner_balls: float,
                 g_balls_outer: float, g_annulus: float, g_fit_outer: float,
                 g_holder_stator: float, g_holder_air: float) -> Dict[str, float]:
    """Where ONE bearing's friction ends up when the rotor, the stator and the
    air are all held at the same temperature [machine W].

    The bearing network is a linear network between three outside nodes
    (rotor, stator, air) with four inside nodes (inner ring, balls, outer
    ring, holder) and the friction injected inside it.  Eliminating the inside
    nodes (Kron reduction) gives the three delta conductances
    (``delta.G_*``) AND three equivalent sources: the watts the friction
    delivers to each outside node with every outside temperature equal.  A
    lumped model that carries the delta must carry these too, or the friction
    is simply lost (re-audit R2: 10 W copper + 2 W friction transient-summed to
    10 W).  The three watts add up to ``p_w``; the ``air`` share leaves through
    the holder's film without heating any machine node.

    Inside the holder star: shaft → inner ring (``g_shaft_in``), inner ring →
    balls → outer ring with the ring-to-ring annulus in parallel, outer ring →
    holder (``g_fit_outer``), holder → stator and holder → air.
    """
    p = max(float(p_w or 0.0), 0.0)
    if p <= 0.0:
        return {"P_to_rotor_W": 0.0, "P_to_stator_W": 0.0, "P_to_air_W": 0.0}
    g_si = max(float(g_shaft_in), 0.0)
    g_ib = max(float(g_inner_balls), 0.0)
    g_bo = max(float(g_balls_outer), 0.0)
    g_an = max(float(g_annulus), 0.0)
    g_fo = max(float(g_fit_outer), 0.0)
    g_hs = max(float(g_holder_stator), 0.0)
    g_ha = max(float(g_holder_air), 0.0)
    g_h = g_hs + g_ha
    # outer ring → holder → (stator ∥ air), all outside nodes at 0
    g_oh = (g_fo * g_h / (g_fo + g_h)) if (g_fo > 0.0 and g_h > 0.0) else 0.0
    # the three ring / ball nodes, sources 25 / 50 / 25 %
    a = [[g_si + g_ib + g_an, -g_ib, -g_an],
         [-g_ib, g_ib + g_bo, -g_bo],
         [-g_an, -g_bo, g_bo + g_an + g_oh]]
    q = [FRICTION_SPLIT[0] * p, FRICTION_SPLIT[1] * p, FRICTION_SPLIT[2] * p]
    try:
        import numpy as _np
        t_in, _t_ba, t_out = (float(v) for v in _np.linalg.solve(
            _np.array(a, dtype=float), _np.array(q, dtype=float)))
    except Exception:  # noqa: BLE001 — a singular star (no path out at all)
        # nothing leaves but through the shaft: everything to the rotor
        return {"P_to_rotor_W": p, "P_to_stator_W": 0.0, "P_to_air_W": 0.0}
    j_r = g_si * t_in
    j_h = g_oh * t_out
    j_s = j_h * g_hs / g_h if g_h > 0.0 else 0.0
    j_a = j_h * g_ha / g_h if g_h > 0.0 else 0.0
    return {"P_to_rotor_W": j_r, "P_to_stator_W": j_s, "P_to_air_W": j_a}


def node_sources(delta: Optional[Mapping[str, Any]],
                 friction_w: float) -> Dict[str, float]:
    """The friction ``friction_w`` made in the solved bearing nodes, split the
    way the network's own reduction splits it (``delta.P_to_*_W``, summed over
    the ends): ``{to_rotor_W, to_stator_W, to_air_W}``, which add up to
    ``friction_w`` (re-audit #2 R8, the route's whole-system budget).  A delta
    without the split (a map solved before 2026-10-20) puts all of it on the
    rotor, as ``thermal_duty_cycle.losses_by_node`` does."""
    d = dict(delta or {})
    p = max(float(friction_w or 0.0), 0.0)
    js = [max(float(d.get(k) or 0.0), 0.0)
          for k in ("P_to_rotor_W", "P_to_stator_W", "P_to_air_W")]
    tot = sum(js)
    if tot <= 1e-12:
        js, tot = [1.0, 0.0, 0.0], 1.0
    f = p / tot
    return {"to_rotor_W": round(js[0] * f, 4), "to_stator_W": round(js[1] * f, 4),
            "to_air_W": round(js[2] * f, 4)}


# ---------------------------------------------------------------------------
# THE network
# ---------------------------------------------------------------------------

def network(*, ends: Sequence[Mapping[str, Any]], k_shaft: float, rpm: float,
            t_ambient_c: float, air_mode: str, air_speed_mps: float,
            holder_wall_c: Mapping[str, float], emissivity: float,
            symmetry_mult: int, shaft_tags: Sequence[int],
            stator_tags: Sequence[int],
            holder_to: str = "stator") -> Dict[str, Any]:
    """The lumped nodes, links and volume sinks for ``solve_steady_thermal``.

    ``ends`` — one dict per bearing end: ``{"end", "card" (dict), "geom"
    (``end_geometry``), "f_r_n", "f_a_n", "p_w" (that bearing's friction W),
    "t_c" (the bearing temperature the previous pass solved, for the air
    properties)}``.  ``holder_to`` is ``"stator"`` (the holder screwed to the
    core: a sink on the stator elements attached to the holder node) or the
    name of a lumped node (a robotics housing body) the holder links into.

    Returns ``{"nodes", "links", "sinks", "ends": per-end report}``.  The
    friction of each bearing is injected 50 % into its balls node and 25 % into
    each ring (Hoffmann et al. 2020).
    """
    nodes: List[Dict[str, Any]] = []
    links: List[Dict[str, Any]] = []
    sinks: List[Dict[str, Any]] = []
    reports: List[Dict[str, Any]] = []
    sym = max(int(symmetry_mult), 1)
    for e in ends:
        nm = str(e["end"])
        g = e["geom"]
        card = e["card"]
        p_w = max(float(e.get("p_w") or 0.0), 0.0)
        brg = bearing_internal(card, rpm=rpm, f_r_n=float(e.get("f_r_n") or 0.0),
                               f_a_n=float(e.get("f_a_n") or 0.0),
                               t_c=float(e.get("t_c") or 80.0))
        r_shaft = series_conduction(g["shaft_segments_m"], k_shaft)
        g_fit_i = fit_conductance(g["inner_fit_area_m2"])
        g_fit_o = fit_conductance(g["outer_fit_area_m2"])
        r_arms = series_conduction(g["holder_arm_segments_m"], g["holder_k"])
        g_pad = fit_conductance(g["holder_contact_m2"])
        r_hold_stator = r_arms + (1.0 / g_pad if g_pad > 0 else float("inf"))
        film = holder_film(mode=air_mode, air_speed_mps=air_speed_mps,
                           t_wall_c=float(holder_wall_c.get(nm, t_ambient_c)),
                           t_ambient_c=t_ambient_c,
                           area_m2=g["holder_film_area_m2"],
                           char_len_m=g["holder_char_len_m"],
                           emissivity=emissivity)
        n_in, n_ba, n_out, n_h = ("brg%s_inner" % nm, "brg%s_balls" % nm,
                                  "brg%s_outer" % nm, "brg%s_holder" % nm)
        nodes += [
            {"name": n_in, "G_W_per_K": 0.0, "t_sink_c": t_ambient_c,
             "q_W": 0.25 * p_w, "symmetry_mult": sym},
            {"name": n_ba, "G_W_per_K": 0.0, "t_sink_c": t_ambient_c,
             "q_W": 0.50 * p_w, "symmetry_mult": sym},
            {"name": n_out, "G_W_per_K": 0.0, "t_sink_c": t_ambient_c,
             "q_W": 0.25 * p_w, "symmetry_mult": sym},
            {"name": n_h, "G_W_per_K": float(film["G_W_per_K"]),
             "t_sink_c": t_ambient_c, "symmetry_mult": sym},
        ]
        # shaft (2-D) → inner ring: stub conduction + the journal fit, in series
        g_shaft_in = 1.0 / (r_shaft + 1.0 / g_fit_i)
        sinks.append({"name": "bearing_%s_shaft" % nm, "tags": list(shaft_tags),
                      "G_W_per_K": g_shaft_in, "t_sink_c": t_ambient_c,
                      "node": n_in, "symmetry_mult": sym})
        links += [
            {"name": "brg%s_inner_balls" % nm, "a": n_in, "b": n_ba,
             "G_W_per_K": brg["G_inner_balls_W_per_K"]},
            {"name": "brg%s_balls_outer" % nm, "a": n_ba, "b": n_out,
             "G_W_per_K": brg["G_balls_outer_W_per_K"]},
            {"name": "brg%s_outer_holder" % nm, "a": n_out, "b": n_h,
             "G_W_per_K": g_fit_o},
        ]
        if brg["G_annulus_W_per_K"] > 0.0:
            links.append({"name": "brg%s_annulus" % nm, "a": n_in, "b": n_out,
                          "G_W_per_K": brg["G_annulus_W_per_K"]})
        g_hs = (1.0 / r_hold_stator) if math.isfinite(r_hold_stator) else 0.0
        if g_hs > 0.0:
            if holder_to == "stator":
                sinks.append({"name": "bearing_%s_holder_stator" % nm,
                              "tags": list(stator_tags), "G_W_per_K": g_hs,
                              "t_sink_c": t_ambient_c, "node": n_h,
                              "symmetry_mult": sym})
            else:
                links.append({"name": "brg%s_holder_body" % nm, "a": n_h,
                              "b": str(holder_to), "G_W_per_K": g_hs})
        res = [
            {"path": "shaft (2-D mean) → bearing seat", "R_K_per_W": r_shaft,
             "source": "1-D conduction Σ L/(k·A), k_shaft %.1f W/m·K; %s"
                       % (k_shaft, g["sources"]["shaft"]),
             "segments_mm": [[round(L * 1e3, 3), round(A * 1e6, 3)]
                             for L, A in g["shaft_segments_m"]]},
            {"path": "journal → inner ring (fit)", "R_K_per_W": 1.0 / g_fit_i,
             "source": "contact R'' %.2e m²K/W (Incropera Table 3.2) over "
                       "π·d·B = %.1f mm²" % (CONTACT_R_M2K_W,
                                             g["inner_fit_area_m2"] * 1e6)},
            {"path": "inner ring → balls", "R_K_per_W":
             1.0 / brg["G_inner_balls_W_per_K"], "source": brg["model"]},
            {"path": "balls → outer ring", "R_K_per_W":
             1.0 / brg["G_balls_outer_W_per_K"], "source": brg["model"]},
            {"path": "inner ring → outer ring (air between the lands)",
             "R_K_per_W": (1.0 / brg["G_annulus_W_per_K"]
                           if brg["G_annulus_W_per_K"] > 0 else None),
             "source": "Taylor–Couette annulus over the bearing width, "
                       "k_eff %s W/m·K" % (None if brg["annulus_k_eff"] is None
                                          else round(brg["annulus_k_eff"], 4))},
            {"path": "outer ring → holder (fit)", "R_K_per_W": 1.0 / g_fit_o,
             "source": "contact R'' %.2e m²K/W (Incropera Table 3.2) over "
                       "π·D·B = %.1f mm²" % (CONTACT_R_M2K_W,
                                             g["outer_fit_area_m2"] * 1e6)},
            {"path": "holder → air", "R_K_per_W":
             (1.0 / film["G_W_per_K"] if film["G_W_per_K"] > 0 else None),
             "source": film["source"]},
            {"path": "holder → %s" % ("stator core" if holder_to == "stator"
                                      else str(holder_to)),
             "R_K_per_W": (r_hold_stator if math.isfinite(r_hold_stator)
                           else None),
             "source": "arms Σ L/(k·A) (k %.0f W/m·K) %.2f K/W + pads %.1f mm² "
                       "at R'' %.2e m²K/W %.2f K/W; %s"
                       % (g["holder_k"], r_arms, g["holder_contact_m2"] * 1e6,
                          CONTACT_R_M2K_W,
                          (1.0 / g_pad if g_pad > 0 else float("inf")),
                          g["sources"]["holder"])},
        ]
        # THE SAME NETWORK SEEN FROM THE 2-D NODES: the holder is a star
        # between the rotor side (shaft → … → outer ring), the stator and the
        # air; its delta (exact for the linear network, friction aside) is what
        # a lumped transient model carries as three plain conductances.
        g_rr = (1.0 / (1.0 / brg["G_inner_balls_W_per_K"]
                       + 1.0 / brg["G_balls_outer_W_per_K"])
                + brg["G_annulus_W_per_K"])
        g_r = 1.0 / (1.0 / g_shaft_in + 1.0 / g_rr + 1.0 / g_fit_o)
        g_a = float(film["G_W_per_K"])
        g_sig = max(g_r + g_hs + g_a, 1e-12)
        delta = {"G_rotor_air_W_per_K": g_r * g_a / g_sig,
                 "G_rotor_stator_W_per_K": g_r * g_hs / g_sig,
                 "G_stator_air_W_per_K": g_hs * g_a / g_sig,
                 "G_rotor_holder_W_per_K": g_r,
                 # ...and the friction's EQUIVALENT SOURCES (re-audit R2,
                 # 2026-10-20): the delta alone carries the conductances, and a
                 # lumped model that took only them lost the friction made
                 # inside the star.  These are the Norton injections of the
                 # same reduction — where the friction goes with the rotor,
                 # the stator and the air all at one temperature — so the
                 # delta + these three watts reproduce the solved network
                 # exactly (linear network, films frozen at this pass).
                 **source_split(p_w, g_shaft_in=g_shaft_in,
                                g_inner_balls=brg["G_inner_balls_W_per_K"],
                                g_balls_outer=brg["G_balls_outer_W_per_K"],
                                g_annulus=brg["G_annulus_W_per_K"],
                                g_fit_outer=g_fit_o, g_holder_stator=g_hs,
                                g_holder_air=g_a)}
        reports.append({"end": nm, "card": card.get("name"), "P_friction_W": p_w,
                        "delta": delta,
                        "bearing": brg, "film": film,
                        "G_shaft_to_inner_W_per_K": g_shaft_in,
                        "G_holder_to_stator_W_per_K": g_hs,
                        "nodes": [n_in, n_ba, n_out, n_h],
                        "resistances": res})
    return {"nodes": nodes, "links": links, "sinks": sinks, "ends": reports}


def summarize(solved: Mapping[str, Any], net: Mapping[str, Any],
              *, t_ambient_c: float) -> Dict[str, Any]:
    """The payload block from a solve: node temperatures and the flows.

    ``solved`` is ``solve_steady_thermal``'s result; every watt here is in
    MACHINE watts (the solver's wedge watts × symmetry_mult).
    """
    nodes = {n["name"]: n for n in (solved.get("nodes") or [])}
    sinks = {s["name"]: s for s in (solved.get("sinks") or [])}
    lks = {l["name"]: l for l in (solved.get("node_links") or [])}
    out_ends = []
    t_max = None
    for rep in net.get("ends") or []:
        nm = rep["end"]
        n_in, n_ba, n_out, n_h = rep["nodes"]

        def _t(n):
            v = nodes.get(n, {}).get("t_c")
            return None if v is None else round(float(v), 2)
        sym = int(nodes.get(n_h, {}).get("symmetry_mult") or 1)
        s_sh = sinks.get("bearing_%s_shaft" % nm) or {}
        s_hs = sinks.get("bearing_%s_holder_stator" % nm) or {}
        film_w = float(nodes.get(n_h, {}).get("heat_removed_W") or 0.0) * sym
        temps = {"inner_ring_c": _t(n_in), "balls_c": _t(n_ba),
                 "outer_ring_c": _t(n_out), "holder_c": _t(n_h)}
        # the BEARING's temperature: its rings and balls (where the grease
        # is), not the holder it sits in
        for k_t in ("inner_ring_c", "balls_c", "outer_ring_c"):
            v = temps[k_t]
            if v is not None:
                t_max = v if t_max is None else max(t_max, v)
        out_ends.append({
            "end": nm, "card": rep.get("card"), **temps,
            "shaft_seat_mean_c": (None if s_sh.get("t_mean_c") is None
                                  else round(float(s_sh["t_mean_c"]), 2)),
            "shaft_to_bearing_W": round(float(s_sh.get("heat_removed_W") or 0.0) * sym, 4),
            # the sink's own watts: what LEAVES the stator mesh into the holder
            # (negative when a holder feeds the stator)
            "stator_to_holder_W": round(float(s_hs.get("heat_removed_W") or 0.0) * sym, 4),
            "holder_to_air_W": round(film_w, 4),
            "friction_W": round(float(rep.get("P_friction_W") or 0.0), 4),
            "links_W": {k: round(float(v["heat_W"]) * sym, 4)
                        for k, v in lks.items() if k.startswith("brg%s_" % nm)},
            "resistances": [{**r, "R_K_per_W": (None if r["R_K_per_W"] is None
                                                else round(float(r["R_K_per_W"]), 3))}
                            for r in rep["resistances"]],
            "bearing": {k: v for k, v in rep["bearing"].items()
                        if k != "contact_example"},
            "film": rep["film"],
            "delta": rep.get("delta") or {},
        })
    _dl = [e.get("delta") or {} for e in out_ends]
    return {"ends": out_ends, "t_bearing_max_c": t_max,
            "delta": {k: round(sum(float(d.get(k) or 0.0) for d in _dl), 6)
                      for k in ("G_rotor_air_W_per_K", "G_rotor_stator_W_per_K",
                                "G_stator_air_W_per_K", "P_to_rotor_W",
                                "P_to_stator_W", "P_to_air_W")},
            "shaft_to_bearings_W": round(sum(e["shaft_to_bearing_W"] for e in out_ends), 4),
            "stator_to_holders_W": round(sum(e["stator_to_holder_W"] for e in out_ends), 4),
            "holders_to_air_W": round(sum(e["holder_to_air_W"] for e in out_ends), 4),
            "friction_W": round(sum(e["friction_W"] for e in out_ends), 4),
            "t_ambient_c": float(t_ambient_c)}
