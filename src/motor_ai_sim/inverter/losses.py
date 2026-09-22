"""Controller losses, junction temperatures and the two efficiencies.

WHAT IS COMPUTED, and on what
=============================
Everything is integrated over ONE electrical period on the modulator's own
time grid (``waveforms.ModulatorSetup.grid``) rather than quoted from a
closed-form duty-cycle integral.  The reason is the owner's topology
requirement: the same code has to answer for one three-phase bridge, for two
shifted bridges and for six independent H-bridges, and a numerical integral
over the switching functions does that without a new formula per case.

Per LEG, per fundamental period, with ``N`` devices in parallel per switch:

``conduction``       ``(1 - f_dt) * <i^2> * R_DS(on)(T_j) / N``.  With
                     synchronous rectification the leg current is ALWAYS in a
                     channel — high side or low side — so the conduction loss
                     of a leg does not depend on the duty at all; the duty only
                     decides which of the two switches carries it, and over a
                     full period the two share equally.
``third quadrant``   ``f_dt * <|i| * V_SD(|i|/N)>`` — the dead-time windows,
                     where both channels are off and the current is in a BODY
                     DIODE at ~4 V.  ``f_dt = 2 * t_d * f_sw`` (two windows per
                     carrier period).  The same fraction is taken OUT of the
                     conduction term, which is what ``(1 - f_dt)`` above is.
``switching``        ``f_sw * N * <E_on + E_off + E_fr>`` at ``|i|/N``, scaled
                     to the working bus by the card's rule.  One hard turn-on,
                     one hard turn-off and one body-diode recovery per carrier
                     period per leg (AN2025-10 section 6.1: "the reverse
                     recovery loss of the SiC MOSFET's body diode is added to
                     the turn-on energy, E_on, of the opposite switch").
``E_oss``            reported, and by default NOT added: the datasheet E_on is
                     measured in a hard-switching half-bridge, so the channel
                     discharge of C_oss is already inside it (AN2025-10
                     section 4.3.8.3 describes that very mechanism).  Adding it
                     again would double-count.  ``e_oss_policy: "added"``
                     adds it for anyone who wants the pessimistic bound.

THE JUNCTION TEMPERATURE closes the loop, because R_DS(on) nearly triples
between 25 and 175 degC and the switching energies grow ~30 %:

    T_j = T_coolant_mean + P_total * R_coldplate + P_device * (R_jc + R_TIM + R_spread)

iterated to ``TOL_K``.  ``R_coldplate`` comes from the liquid-channel
correlation in ``simulation/cooling_models.py`` — the SAME ladder the motor's
water jacket uses (laminar 3.66 / Gnielinski / Dittus-Boelter), so a coldplate
and a jacket in one report are not two different opinions about pipe flow.

ONE EFFICIENCY AT THE SHAFT stays one efficiency at the shaft.  The inverter
adds a SECOND, separately named number and the product is named in full:

    inverter efficiency      = P_ac_out / (P_ac_out + P_inverter)
    wall-to-shaft efficiency = inverter efficiency * shaft efficiency

and ``shaft efficiency`` is read from the duty's own coupled record, never
recomputed here.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from motor_ai_sim.inverter import waveforms as wf
from motor_ai_sim.inverter.devices import DeviceCard, CardError, get_device
from motor_ai_sim.inverter.topology import (Bridge, Coil, Topology,
                                            TopologyError, build_topology,
                                            coils_from_winding)

log = logging.getLogger(__name__)

__all__ = ["ControllerRefusal", "ColdPlate", "AirForcedCooling",
           "AirStillCooling", "COOLING_MODES", "solve_controller", "limit_rows",
           "DEFAULT_TIM_K_W", "TOL_K", "E_OSS_POLICIES", "SET_SPLITS",
           "V_DSS_WARN_FRACTION"]

#: Bus utilisation above this fraction of V_DSS is a DESIGN WARNING, not a
#: refusal: the device blocks it, but there is nothing left for the switching
#: overshoot a real commutation loop produces, and every application note in
#: the trade says so.  It is a convention of this module and it is printed as
#: one — the datasheet's own limit is V_DSS itself, which is the FAIL line.
V_DSS_WARN_FRACTION = 0.80


class ControllerRefusal(ValueError):
    """An input this module will not pretend to answer.

    Carries ``fields`` and ``code`` so the route can turn it into the project's
    usual 422 with the field named — a controller that quietly solved a
    machine nobody can wire is exactly the silent wrong answer the client-facing
    validation rule exists to prevent.
    """

    def __init__(self, message: str, fields: Optional[Sequence[str]] = None,
                 code: str = "bad_controller") -> None:
        super().__init__(message)
        self.fields = list(fields or [])
        self.code = code


#: Junction-temperature convergence band [K].  0.1 K is far inside the
#: uncertainty of the R_th and the digitised curves; it exists so the answer
#: does not depend on the starting guess.
TOL_K = 0.1
_MAX_ITER = 80

#: Thermal interface, per device [K/W].  A DEFAULT, not a measurement: a Q-DPAK
#: top-side tab (~9 x 7 mm of cooled area) through a 100 um, 5 W/m.K gap pad is
#: ~0.03 K/W; a phase-change film or sintered joint is several times better.
#: It is an INPUT and it is reported, because it is comparable with R_th(j-c)
#: itself (0.1 K/W) and therefore changes the answer.
DEFAULT_TIM_K_W = 0.03

E_OSS_POLICIES = ("included_in_eon", "added")

#: What a SECOND three-phase inverter on the same motor actually does depends on
#: how the coils are reconnected, and the two answers differ by a factor of two
#: in device current.  Both are offered and the response says which was taken.
SET_SPLITS = ("series_split", "power_split")


# ---------------------------------------------------------------------------
# The coldplate
# ---------------------------------------------------------------------------

@dataclass
class ColdPlate:
    """A liquid coldplate: n parallel channels of w x h under the devices.

    The defaults are a plain machined plate and every one of them is reported,
    because the film coefficient is the difference between a 20 K rise and a
    2 K rise and nobody should have to guess which one a number came from.
    """
    coolant: str = "water_glycol_50"
    flow_lpm: float = 8.0
    t_in_c: float = 65.0
    # A MICRO-CHANNEL / skived-fin plate, which is what a few-kW stack needs and
    # what a traction inverter actually uses.  The geometry is a default, not a
    # drawing, and every number of it is reported: with 50/50 glycol the flow
    # stays laminar whatever you do at 8 L/min, so the film coefficient is
    # Nu = 3.66 * k / D_h and the ONLY lever left is the hydraulic diameter —
    # which is why the default channels are 1 mm wide.  A plain 6 x 4 mm-channel
    # plate gives ~0.11 K/W and cooks this stack; this one gives ~0.008 K/W.
    n_channels: int = 40
    channel_w_mm: float = 1.0
    channel_h_mm: float = 5.0
    length_mm: float = 300.0
    #: Wetted-area multiplier for a finned/pin-fin plate (1.0 = plain channels).
    fin_area_factor: float = 1.0
    r_override_k_w: Optional[float] = None   # bypasses the correlation

    def resistance(self, t_wall_c: Optional[float] = None) -> Dict[str, Any]:
        """``{r_k_w, h_w_m2k, ...}`` — the plate-to-coolant thermal resistance.

        ``t_wall_c`` is accepted and ignored — the pipe-flow film here does
        not depend on the wall temperature, only ``AirStillCooling``'s does
        (natural convection), and :func:`solve_controller` calls every
        cooling object through the same signature so the thermal loop does
        not need to know which mode it is iterating.
        """
        if self.r_override_k_w is not None:
            return {"r_k_w": float(self.r_override_k_w),
                    "r_film_per_device_k_w": 0.0,
                    "basis": "given by the request (the correlation was not used)"}
        from motor_ai_sim.simulation import cooling_models as cm
        from motor_ai_sim.routes.thermal import _coolant_props
        props = cm.FluidProps(*_coolant_props(str(self.coolant)))
        w = max(float(self.channel_w_mm), 0.1) * 1e-3
        h = max(float(self.channel_h_mm), 0.1) * 1e-3
        n = max(int(self.n_channels), 1)
        L = max(float(self.length_mm), 1.0) * 1e-3
        d_h = cm.hydraulic_diameter_slot(w, h)
        a_c = w * h * n
        q = max(float(self.flow_lpm), 0.0) / 60000.0
        v = q / a_c if a_c > 0 else 0.0
        re = v * d_h / props.nu if v > 0 else 0.0
        nu, regime = cm.pipe_nusselt(re, props.pr)
        h_film = nu * props.k / d_h
        fin = max(float(self.fin_area_factor), 1.0)
        area = n * 2.0 * (w + h) * L * fin    # wetted perimeter x length x fins
        r = 1.0 / (h_film * area) if h_film * area > 0 else float("inf")
        return {"r_k_w": r, "r_film_per_device_k_w": 0.0,
                "h_w_m2k": h_film, "reynolds": re, "nusselt": nu,
                "regime": regime, "velocity_mps": v,
                "hydraulic_diameter_mm": d_h * 1e3, "wetted_area_m2": area,
                "m_dot_kg_s": props.rho * q, "cp_j_kgk": props.cp,
                "basis": (f"{regime} flow in {n} x {self.channel_w_mm:g} x "
                          f"{self.channel_h_mm:g} mm channels, {v:.2f} m/s"
                          + (f", wetted area x{fin:g} for fins" if fin > 1 else "")
                          + " (cooling_models.pipe_nusselt — the same ladder "
                            "the motor jacket uses)")}

    def as_dict(self) -> Dict[str, Any]:
        return {"mode": "liquid",
                "coolant": self.coolant, "flow_lpm": self.flow_lpm,
                "t_in_c": self.t_in_c, "n_channels": self.n_channels,
                "channel_w_mm": self.channel_w_mm,
                "channel_h_mm": self.channel_h_mm, "length_mm": self.length_mm,
                "fin_area_factor": self.fin_area_factor,
                "r_override_k_w": self.r_override_k_w}


# ---------------------------------------------------------------------------
# Air cooling — a heatsink or a PCB pad in an air stream, or in still air
# ---------------------------------------------------------------------------
# Owner, 2026-09-22 (screenshot of the Controller cooling selector offering
# only water / water_glycol_50 / ethylene_glycol / oil): *"надо добавить
# воздушное охлаждение и скорость ветра, как в термосимуляции"* — the same
# two air modes the motor's own Thermal tab already offers for the housing
# (``routes.thermal``'s ``cooling_mode`` = "air" / "robotics"), applied to
# the controller's devices instead of the housing.  NOTHING NEW IS INVENTED:
# both modes reuse the exact correlations ``simulation/cooling_models.py``
# already carries and the motor's own thermal solve already cites.
#
#   ``air_forced`` — a fan or a slipstream over a heatsink/plate:
#       ``cooling_models.outer_air`` (Churchill–Bernstein, cylinder in
#       cross-flow) — the SAME function ``routes.thermal._cooling_bc`` calls
#       for the housing's ``cooling_mode="air"``.  A heatsink is not a
#       cylinder, so a characteristic length has to stand in for the
#       diameter Churchill–Bernstein needs; see
#       ``AIR_DEVICE_CHAR_LENGTH_M`` for what it is and why.
#   ``air_still`` — no fan at all, natural convection + radiation:
#       ``cooling_models.end_face_still`` (Churchill–Chu VERTICAL-plate form
#       plus linearised radiation) — the same FLAT-FACE still-air
#       correlation the motor's "robotics" thermal mode uses for its end
#       turns and open core face, which is the better physical match for a
#       flat heatsink/PCB pad than the housing's own cylinder correlation
#       (``outer_still``) would be.
#
# THE THERMAL STACK (owner's own words): T_j = T_ambient + P * (R_th(j-c) +
# R_TIM + R_spread + R_film), R_film = 1/(h * A_eff * eta_fin).  Unlike the
# liquid coldplate (ONE plate under every device, so ONE shared resistance
# node between the total power and a common case temperature), a heatsink is
# normally PER DEVICE — each device has its own fins and its own air, they do
# not share a spreading plate — so by default R_film is folded into the
# PER-DEVICE chain (``r_film_per_device_k_w``) exactly as R_TIM and R_spread
# already are, and the module's usual SHARED node (``r_k_w``, what
# ``ColdPlate`` always returns) is zero.  A caller that states ``plate_area``
# instead of a per-device heatsink IS asking for a shared node — a PCB pad
# spreads the several devices bolted to it through its own copper, the same
# shape as a coldplate — and the two areas are mutually exclusive per mode.
#
# The sink is always ambient and never iterates an outlet: unlike a coolant
# loop, the room's air is an unbounded reservoir (the same reasoning
# ``cooling_models.outer_air``/``outer_still`` state for the housing) — so
# ``t_in_c`` below IS the ambient temperature and the existing rise/outlet
# arithmetic in the loop below collapses to zero automatically (no
# ``m_dot_kg_s``/``cp_j_kgk`` keys in the resistance dict).

COOLING_MODES = ("liquid", "air_forced", "air_still")

#: Characteristic length fed to the forced/still correlations above — NOT a
#: real cylinder or plate height, a stated assumption standing in for one.
#: Churchill–Bernstein and the flat-plate Churchill–Chu form both need ONE
#: length scale, and this is the footprint a small finned/pin-fin heatsink or
#: a PQFN/TO-247-class device's copper pour actually has (25-40 mm class).
#: The SAME value is used for both air modes so a report that quotes a
#: forced-air film and a still-air film for the same device is quoting one
#: assumed geometry, not two.
AIR_DEVICE_CHAR_LENGTH_M = 0.03

#: Wetted (finned) area of the default heatsink, ONE PER DEVICE, used when
#: the request states neither ``heatsink_area_cm2_per_device`` nor
#: ``plate_area_cm2`` — "a small finned heatsink", the assumption the owner's
#: brief names explicitly.  A TO-247/PQFN-class device on a compact
#: pin-fin/extruded heatsink commonly wets 30-50 cm^2; 40 cm^2 is the middle
#: of that band.  Reported on every solve that falls back to it.
DEFAULT_HEATSINK_AREA_CM2_PER_DEVICE = 40.0

#: Fin efficiency of that assumed heatsink — a STATED CONSTANT, not fitted,
#: reported on every air-mode solve.  0.75 is the middle of the band a short
#: aluminium pin/plate fin sits in at the h this module's correlations give
#: (a longer or thinner fin would be lower; a bare flat pad with no fins at
#: all should be sent as 1.0).
FIN_EFFICIENCY_DEFAULT = 0.75

#: Emissivity of the device heatsink/PCB pad in ``air_still`` mode, when the
#: request does not state one — the same default and the same band
#: (0.85-0.95 for anodised aluminium / bare FR4 solder mask; a bare polished
#: heatsink is far lower) ``cooling_models.EMISSIVITY_DEFAULT`` states for
#: the motor's own housing, repeated here rather than imported so this
#: module's constants stay self-contained and greppable.
AIR_STILL_EMISSIVITY_DEFAULT = 0.9


@dataclass
class AirForcedCooling:
    """A heatsink or PCB pad blown by a fan or a slipstream.

    ``air_speed_mps``/``t_ambient_c`` are the panel's own "wind speed, as in
    the thermal simulation" inputs.  Either ``heatsink_area_cm2_per_device``
    (a heatsink bolted to EACH device — the default topology) or
    ``plate_area_cm2`` (one PCB pad shared by every device on it) states the
    wetted area; sending both is not an error, but ``plate_area_cm2`` wins
    (see :meth:`resistance`).
    """
    air_speed_mps: float = 5.0
    t_ambient_c: float = 40.0
    heatsink_area_cm2_per_device: Optional[float] = None
    plate_area_cm2: Optional[float] = None
    fin_efficiency: float = FIN_EFFICIENCY_DEFAULT

    @property
    def t_in_c(self) -> float:
        return self.t_ambient_c

    def resistance(self, t_wall_c: Optional[float] = None) -> Dict[str, Any]:
        """``{r_k_w, r_film_per_device_k_w, h_w_m2k, ...}``.

        ``t_wall_c`` is accepted and ignored: Churchill–Bernstein forced
        convection is evaluated at the ambient film here exactly as
        ``cooling_models.outer_air`` states for the housing, so this film
        does not depend on how hot the device runs.
        """
        from motor_ai_sim.simulation import cooling_models as cm
        eta = min(max(float(self.fin_efficiency), 1e-3), 1.0)
        rep = cm.outer_air(air_speed_mps=float(self.air_speed_mps),
                           t_ambient_c=float(self.t_ambient_c),
                           d_housing_m=AIR_DEVICE_CHAR_LENGTH_M)
        h = float(rep["h_conv"])
        shared = self.plate_area_cm2 is not None
        a_cm2 = float(self.plate_area_cm2 if shared else
                     (self.heatsink_area_cm2_per_device
                      if self.heatsink_area_cm2_per_device is not None
                      else DEFAULT_HEATSINK_AREA_CM2_PER_DEVICE))
        a_m2 = max(a_cm2, 1e-6) * 1e-4
        r = 1.0 / (h * a_m2 * eta) if h * a_m2 * eta > 0 else float("inf")
        basis = (f"Churchill-Bernstein cross-flow at {self.air_speed_mps:g} m/s "
                f"over a {AIR_DEVICE_CHAR_LENGTH_M * 1e3:.0f} mm effective "
                "length (cooling_models.outer_air — the same correlation the "
                "motor's own housing uses in cooling_mode='air') -> h = "
                f"{h:.0f} W/m^2K, x {a_cm2:g} cm^2 "
                + ("shared PCB plate" if shared else "heatsink per device")
                + f" x fin efficiency {eta:.2f} (stated constant)")
        out = {"h_w_m2k": h, "area_m2": a_m2, "area_cm2": a_cm2,
              "fin_efficiency": eta, "regime": rep.get("regime"),
              "re": rep.get("re"), "nu": rep.get("nu"), "basis": basis}
        if shared:
            out.update({"r_k_w": r, "r_film_per_device_k_w": 0.0,
                       "topology": "shared_plate"})
        else:
            out.update({"r_k_w": 0.0, "r_film_per_device_k_w": r,
                       "topology": "per_device_heatsink"})
        return out

    def as_dict(self) -> Dict[str, Any]:
        return {"mode": "air_forced", "air_speed_mps": self.air_speed_mps,
                "t_ambient_c": self.t_ambient_c,
                "heatsink_area_cm2_per_device": self.heatsink_area_cm2_per_device,
                "plate_area_cm2": self.plate_area_cm2,
                "fin_efficiency": self.fin_efficiency}


@dataclass
class AirStillCooling:
    """A heatsink or PCB pad in still air — no fan, no slipstream.

    Same shape as :class:`AirForcedCooling`, minus the air speed and plus
    ``emissivity`` — natural convection + radiation, exactly the pair the
    motor's own "robotics" thermal mode applies to a joint with no fan
    (``cooling_models.end_face_still``, the FLAT-FACE member of that family:
    a heatsink/PCB pad is a plate, not a cylinder, so this is the better
    match of the two still-air correlations that module carries).
    """
    t_ambient_c: float = 40.0
    emissivity: float = AIR_STILL_EMISSIVITY_DEFAULT
    heatsink_area_cm2_per_device: Optional[float] = None
    plate_area_cm2: Optional[float] = None
    fin_efficiency: float = FIN_EFFICIENCY_DEFAULT

    @property
    def t_in_c(self) -> float:
        return self.t_ambient_c

    def resistance(self, t_wall_c: Optional[float] = None) -> Dict[str, Any]:
        """``{r_k_w, r_film_per_device_k_w, h_w_m2k, ...}``.

        Natural convection depends on the WALL temperature (``h`` grows with
        ΔT^~0.25) — the one air mode where that matters, like the motor's own
        ``outer_still``/``end_face_still`` — so :func:`solve_controller`
        passes its current best case-temperature ESTIMATE as ``t_wall_c`` on
        every pass of the junction-temperature loop, the same lagged
        fixed-point iteration the loop already runs for R_DS(on)(T_j).  The
        first pass (no estimate yet) assumes a 20 K rise over ambient.
        """
        from motor_ai_sim.simulation import cooling_models as cm
        eta = min(max(float(self.fin_efficiency), 1e-3), 1.0)
        eps = min(max(float(self.emissivity), 0.0), 1.0)
        wall = float(t_wall_c) if t_wall_c is not None else float(self.t_ambient_c) + 20.0
        shared = self.plate_area_cm2 is not None
        a_cm2 = float(self.plate_area_cm2 if shared else
                     (self.heatsink_area_cm2_per_device
                      if self.heatsink_area_cm2_per_device is not None
                      else DEFAULT_HEATSINK_AREA_CM2_PER_DEVICE))
        a_m2 = max(a_cm2, 1e-6) * 1e-4
        rep = cm.end_face_still(t_wall_c=wall, t_ambient_c=float(self.t_ambient_c),
                                area_m2=a_m2, char_len_m=AIR_DEVICE_CHAR_LENGTH_M,
                                emissivity=eps, orientation="vertical",
                                name=("shared PCB plate" if shared
                                     else "device heatsink"))
        h = float(rep["h_total"])
        r = 1.0 / (h * a_m2 * eta) if h * a_m2 * eta > 0 else float("inf")
        basis = (f"Churchill-Chu vertical-plate natural convection + "
                f"radiation at eps {eps:.2f} on a {AIR_DEVICE_CHAR_LENGTH_M * 1e3:.0f} mm "
                "effective height (cooling_models.end_face_still — the same "
                "still-air family the motor's robotics thermal mode uses) -> "
                f"h_conv {rep.get('h_conv', 0.0):.1f} + h_rad "
                f"{rep.get('h_rad', 0.0):.1f} = {h:.1f} W/m^2K, x {a_cm2:g} cm^2 "
                + ("shared PCB plate" if shared else "heatsink per device")
                + f" x fin efficiency {eta:.2f} (stated constant)")
        out = {"h_w_m2k": h, "h_conv": rep.get("h_conv"), "h_rad": rep.get("h_rad"),
              "area_m2": a_m2, "area_cm2": a_cm2, "fin_efficiency": eta,
              "emissivity": eps, "ra": rep.get("ra"), "nu": rep.get("nu"),
              "regime": rep.get("regime"), "t_wall_c": wall, "basis": basis}
        if shared:
            out.update({"r_k_w": r, "r_film_per_device_k_w": 0.0,
                       "topology": "shared_plate"})
        else:
            out.update({"r_k_w": 0.0, "r_film_per_device_k_w": r,
                       "topology": "per_device_heatsink"})
        return out

    def as_dict(self) -> Dict[str, Any]:
        return {"mode": "air_still", "t_ambient_c": self.t_ambient_c,
                "emissivity": self.emissivity,
                "heatsink_area_cm2_per_device": self.heatsink_area_cm2_per_device,
                "plate_area_cm2": self.plate_area_cm2,
                "fin_efficiency": self.fin_efficiency}


def _build_cooling(cooling_req: Dict[str, Any]
                   ) -> "ColdPlate | AirForcedCooling | AirStillCooling":
    """The cooling object for this solve, dispatched on ``cooling.mode``.

    ``mode`` absent (every configuration saved before 2026-09-22, and every
    request that never mentions it) means ``"liquid"`` — the ONLY mode that
    ever existed before this — so an old request reproduces bit-identical
    results through the unchanged :class:`ColdPlate` path.
    """
    spec = dict(cooling_req)
    mode = str(spec.pop("mode", None) or "liquid").strip().lower()
    if mode == "liquid":
        cls: Any = ColdPlate
    elif mode == "air_forced":
        cls = AirForcedCooling
    elif mode == "air_still":
        cls = AirStillCooling
    else:
        raise ControllerRefusal(
            "cooling.mode must be " + " or ".join(COOLING_MODES) + f"; got {mode!r}",
            ["cooling"], code="bad_cooling_mode")
    return cls(**{k: v for k, v in spec.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# One leg's integrals
# ---------------------------------------------------------------------------

def _leg_losses(*, card: DeviceCard, i_leg: np.ndarray, n_par: int,
                f_sw: float, t_j_c: float, v_dc: float, v_gs_on: float,
                v_gs_off: float, r_g: Optional[float], dead_time_s: float,
                e_oss_policy: str) -> Dict[str, Any]:
    """Conduction / third-quadrant / switching / E_oss of ONE leg, watts."""
    n = max(int(n_par), 1)
    r_tot = card.r_ds_on_ohm(t_j_c, v_gs_on)
    r_eff = r_tot / n
    a = np.abs(i_leg)
    i_dev = a / n
    f_dt = min(max(2.0 * float(dead_time_s) * float(f_sw), 0.0), 1.0)

    p_cond = (1.0 - f_dt) * float(np.mean(i_leg ** 2)) * r_eff

    # Sampled on a coarse current ladder and interpolated back, so a
    # 2 000-point grid does not mean 2 000 card look-ups per iteration.
    i_max = float(np.max(i_dev)) if i_dev.size else 0.0
    ladder = np.linspace(0.0, max(i_max, 1e-9), 33)

    if f_dt > 0.0:
        v_ladder = np.array([card.v_sd_V(float(x), t_j_c, v_gs_off)
                             for x in ladder])
        v_sd = np.interp(i_dev, ladder, v_ladder)
        p_3q = f_dt * float(np.mean(a * v_sd))
    else:
        p_3q = 0.0

    e_on, e_off, e_fr = [], [], []
    extrapolated = False
    notes: List[str] = []
    for x in ladder:
        e = card.e_switch(i_d_A=float(x), t_j_c=t_j_c, v_dc_V=v_dc,
                          v_gs_off_V=v_gs_off, r_g_ext_ohm=r_g,
                          v_gs_on_V=v_gs_on)
        e_on.append(e["e_on_J"]); e_off.append(e["e_off_J"]); e_fr.append(e["e_fr_J"])
        extrapolated = extrapolated or bool(e["extrapolated"])
        if not notes:
            notes = list(e["notes"])
    tot = np.array(e_on) + np.array(e_off) + np.array(e_fr)
    e_at = np.interp(i_dev, ladder, tot)
    p_sw = float(f_sw) * n * float(np.mean(e_at))
    p_on = float(f_sw) * n * float(np.mean(np.interp(i_dev, ladder, np.array(e_on))))
    p_off = float(f_sw) * n * float(np.mean(np.interp(i_dev, ladder, np.array(e_off))))
    p_fr = float(f_sw) * n * float(np.mean(np.interp(i_dev, ladder, np.array(e_fr))))

    e_oss = card.e_oss_J(v_dc)
    p_oss_ref = float(f_sw) * n * float(e_oss or 0.0)
    p_oss = p_oss_ref if e_oss_policy == "added" else 0.0

    return {
        "p_conduction_W": p_cond,
        "p_third_quadrant_W": p_3q,
        "p_switching_W": p_sw,
        "p_switching_on_W": p_on,
        "p_switching_off_W": p_off,
        "p_switching_recovery_W": p_fr,
        "p_e_oss_W": p_oss,
        "p_e_oss_reference_W": p_oss_ref,
        "p_total_W": p_cond + p_3q + p_sw + p_oss,
        "r_ds_on_mohm": r_tot * 1e3,
        "dead_time_fraction": f_dt,
        "i_leg_rms_A": float(np.sqrt(np.mean(i_leg ** 2))),
        # A SWITCH carries the leg current for about half the period (the duty
        # decides which half, not how much), so its own full-period rms is
        # I_leg/sqrt(2) — and that, divided by the parallel count, is the
        # number to hold against a continuous DC rating.  The conduction loss
        # above is unaffected: both switches together dissipate I_leg^2*R.
        "i_switch_rms_A": float(np.sqrt(np.mean(i_leg ** 2) / 2.0)),
        "i_device_rms_A": float(np.sqrt(np.mean(i_dev ** 2) / 2.0)),
        "i_device_peak_A": i_max,
        "extrapolated": extrapolated,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# THE DATASHEET LIMITS — every one of them, with its number, on every solve
# ---------------------------------------------------------------------------
# Owner, 2026-09-22: *«не забудь про паспортные лимиты MOSFET»*.  The junction
# temperature and the current rating were already refusals; this makes the
# WHOLE list explicit and always present, so a design is not "fine" merely
# because nobody printed the line that would have failed.
#
# Every row carries the measured number, the datasheet limit, where that limit
# comes from, and a verdict:
#   ``pass``        the number is inside the published limit
#   ``fail``        it is not — the solve is ``feasible: false``
#   ``warn``        inside the limit, but against this module's own stated
#                   convention (the only one is bus utilisation)
#   ``not_judged``  the card does not publish the limit, or this model does not
#                   compute the quantity.  NEVER silently a pass.

def limit_rows(*, card: DeviceCard, v_dc_V: float, t_case_c: float,
               t_j_c: float, i_device_rms_A: float, i_device_peak_A: float,
               i_device_reverse_peak_A: float,
               v_gs_on_V: float, v_gs_off_V: float,
               dead_time_us: float) -> List[Dict[str, Any]]:
    """The datasheet limit table for one solved controller."""
    rows: List[Dict[str, Any]] = []

    def row(name: str, value: Optional[float], limit: Optional[float],
            unit: str, source: str, note: str = "",
            higher_is_worse: bool = True) -> None:
        if value is None or limit is None:
            rows.append({"name": name, "value": value, "limit": limit,
                         "unit": unit, "margin": None, "utilisation_pct": None,
                         "verdict": "not_judged", "source": source,
                         "note": note or "the card does not publish this limit"})
            return
        ok = (value <= limit) if higher_is_worse else (value >= limit)
        rows.append({
            "name": name, "value": round(float(value), 2),
            "limit": round(float(limit), 2), "unit": unit,
            "margin": round(float(limit) - float(value), 2),
            "utilisation_pct": (round(100.0 * value / limit, 1)
                                if limit else None),
            "verdict": "pass" if ok else "fail",
            "source": source, "note": note})

    rating = card.i_d_rating(t_case_c)
    row("Continuous current per device", i_device_rms_A, rating.get("i_a"),
        "A rms",
        (rating.get("source") or "the card's ratings block")
        + " — " + str(rating.get("basis")),
        f"rms over the whole period at the solved case temperature "
        f"{t_case_c:.0f} degC")

    row("Peak current per device", i_device_peak_A, card.i_d_pulsed_A, "A",
        "the card's ratings block (I_DM)",
        "the datasheet states I_DM as limited by T_vj(max) rather than by a "
        "fixed pulse width, and here it recurs every fundamental period — the "
        "binding judge is the junction-temperature row below")

    row("Reverse peak current per device", i_device_reverse_peak_A,
        card.i_sm_A, "A", "the card's third_quadrant block (I_SM)",
        f"through the body diode during the {dead_time_us:g} us dead-time "
        f"windows" if dead_time_us > 0 else
        "no dead time in this solve, so the body diode never conducts")

    row("Junction temperature", t_j_c, card.t_j_max_c, "degC",
        "the card's ratings block (T_vj)",
        "the hottest device of the whole controller")

    rows.append(_bus_row(card, v_dc_V))

    lo_v, hi_v = card.v_gs_static_window()
    if lo_v is None or hi_v is None:
        rows.append({"name": "Gate voltage", "value": None, "limit": None,
                     "unit": "V", "margin": None, "utilisation_pct": None,
                     "verdict": "not_judged",
                     "source": "the card's gate block",
                     "note": "the card publishes no static V_GS window"})
    else:
        worst = max(abs(v_gs_on_V - hi_v), abs(lo_v - v_gs_off_V))
        inside = (lo_v <= v_gs_off_V <= hi_v) and (lo_v <= v_gs_on_V <= hi_v)
        rows.append({
            "name": "Gate voltage", "value": None, "limit": None, "unit": "V",
            "margin": round(min(hi_v - v_gs_on_V, v_gs_off_V - lo_v), 2),
            "utilisation_pct": None,
            "verdict": "pass" if inside else "fail",
            "source": "the card's gate block (static V_GS window)",
            "note": (f"driven {v_gs_on_V:g} / {v_gs_off_V:g} V against a "
                     f"{lo_v:g} … {hi_v:g} V window")})

    rat = card.doc.get("ratings") or {}
    has_av = rat.get("e_as_mJ") is not None
    rows.append({
        "name": "Avalanche energy", "value": None,
        "limit": (float(rat["e_as_mJ"]) if has_av else None), "unit": "mJ",
        "margin": None, "utilisation_pct": None, "verdict": "not_judged",
        "source": "the card's ratings block (E_AS/E_AR)" if has_av else "",
        "note": ("this model computes no avalanche event — a clamped "
                 "two-level bridge should have none, and the stray-inductance "
                 "spike that would cause one is not modelled"
                 if has_av else "the card publishes no avalanche rating")})
    rows.append({
        "name": "dv/dt", "value": None, "limit": None, "unit": "kV/us",
        "margin": None, "utilisation_pct": None, "verdict": "not_judged",
        "source": "", "note": "the card publishes no dv/dt rating (the "
                              "datasheet states a characterisation figure, "
                              "not a limit), and this model computes no slope"})
    return rows


def _bus_row(card: DeviceCard, v_dc_V: float) -> Dict[str, Any]:
    """The DC link against V_DSS — a fail above it, a warning near it."""
    v_dss = card.v_dss_V
    use = 100.0 * float(v_dc_V) / v_dss if v_dss else None
    if v_dc_V > v_dss:
        verdict = "fail"
    elif use is not None and use > 100.0 * V_DSS_WARN_FRACTION:
        verdict = "warn"
    else:
        verdict = "pass"
    return {
        "name": "DC link vs V_DSS", "value": round(float(v_dc_V), 1),
        "limit": round(float(v_dss), 1), "unit": "V",
        "margin": round(float(v_dss) - float(v_dc_V), 1),
        "utilisation_pct": None if use is None else round(use, 1),
        "verdict": verdict,
        "source": "the card's ratings block (V_DSS)",
        "note": (f"{use:.0f} % of the blocking voltage; above "
                 f"{100 * V_DSS_WARN_FRACTION:.0f} % there is nothing left for "
                 f"the commutation overshoot — a design warning of this "
                 f"module, not a datasheet limit" if verdict == "warn"
                 else f"{use:.0f} % of the blocking voltage"
                 if use is not None else "")}


# ---------------------------------------------------------------------------
# The solve
# ---------------------------------------------------------------------------

def _f(v: Any, name: str, *, default: Optional[float] = None,
       positive: bool = False) -> float:
    if v is None:
        if default is None:
            raise ControllerRefusal(f"{name} is required", [name],
                                    code="missing_field")
        return float(default)
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ControllerRefusal(f"{name} must be a number; got {v!r}", [name])
    if math.isnan(x) or math.isinf(x):
        raise ControllerRefusal(f"{name} must be a finite number; got {v!r}", [name])
    if positive and x <= 0.0:
        raise ControllerRefusal(f"{name} must be positive; got {x!r}", [name])
    return x


def solve_controller(req: Dict[str, Any]) -> Dict[str, Any]:
    """Solve one controller on one operating point.

    ``req`` (every key that is not obvious is spelled out in the response's
    ``inputs`` block, with where its value came from):

      machine   ``num_slots``, ``num_poles``, ``single_layer``, ``winding_layout``
      point     ``i_phase_rms_A`` (the MACHINE phase current), ``star_delta``,
                ``p_ac_W``, ``f_elec_hz``, ``rpm``, ``efficiency_shaft``
      bus       ``v_dc_V``, ``f_carrier_hz``, ``modulation_index`` (or
                ``power_factor``)
      devices   ``device``, ``devices_parallel``, ``r_g_ext_ohm``,
                ``v_gs_on_V``, ``v_gs_off_V``, ``dead_time_us``
      topology  ``topology``, ``mapping``, ``h_bridge_modulation``, ``set_split``
      cooling   ``cooling.mode`` -> ``"liquid"`` (default, :class:`ColdPlate`
                fields — coolant/flow/inlet/channels), ``"air_forced"``
                (:class:`AirForcedCooling` — air_speed_mps/t_ambient_c/
                heatsink or plate area/fin_efficiency) or ``"air_still"``
                (:class:`AirStillCooling` — same, plus emissivity, no fan);
                also ``r_tim_k_w``, ``r_spread_k_w`` (per-device, every mode)
    """
    # ── the machine and the map ────────────────────────────────────────────
    slots = int(_f(req.get("num_slots"), "num_slots", positive=True))
    poles = int(_f(req.get("num_poles"), "num_poles", positive=True))
    try:
        coils = coils_from_winding(
            slots, poles,
            single_layer=bool(req.get("single_layer", True)),
            layout_str=req.get("winding_layout") or None)
    except TopologyError as exc:
        raise ControllerRefusal(str(exc), ["num_slots", "num_poles"],
                                code="bad_winding")

    sd = str(req.get("star_delta") or "star").strip().lower()
    device = str(req.get("device") or "").strip()
    try:
        card = get_device(device)
    except CardError as exc:
        raise ControllerRefusal(str(exc), ["device"], code="unknown_device")

    n_par = int(_f(req.get("devices_parallel"), "devices_parallel", default=1,
                   positive=True))
    v_dc = _f(req.get("v_dc_V"), "v_dc_V", positive=True)
    if v_dc > card.v_dss_V:
        raise ControllerRefusal(
            f"the DC link is {v_dc:.0f} V and {card.part} blocks "
            f"{card.v_dss_V:.0f} V — this controller cannot be built",
            ["v_dc_V"], code="bus_over_vdss")

    preset = str(req.get("topology") or "one_3ph").strip().lower()
    set_split = str(req.get("set_split") or "series_split").strip().lower()
    if set_split not in SET_SPLITS:
        raise ControllerRefusal("set_split must be " + " or ".join(SET_SPLITS),
                                ["set_split"])
    try:
        topo = build_topology(
            preset=preset, coils=coils, star_delta=sd, device=card.part,
            devices_parallel=n_par, v_dc_V=v_dc,
            h_bridge_modulation=str(req.get("h_bridge_modulation") or "unipolar"),
            mapping=req.get("mapping"),
            devices_parallel_by_bridge=req.get("devices_parallel_by_bridge"))
    except TopologyError as exc:
        raise ControllerRefusal(str(exc), ["topology", "mapping",
                                           "devices_parallel_by_bridge"],
                                code="bad_topology")

    # ── the operating point ────────────────────────────────────────────────
    i_ph = _f(req.get("i_phase_rms_A"), "i_phase_rms_A", positive=True)
    p_ac = _f(req.get("p_ac_W"), "p_ac_W", positive=True)
    f_el = _f(req.get("f_elec_hz"), "f_elec_hz", positive=True)
    f_sw = _f(req.get("f_carrier_hz"), "f_carrier_hz", positive=True)
    eta_shaft = req.get("efficiency_shaft")
    eta_shaft = None if eta_shaft is None else _f(eta_shaft, "efficiency_shaft",
                                                  positive=True)

    root3 = math.sqrt(3.0)
    i_leg_3ph = i_ph * (root3 if sd == "delta" else 1.0)

    m_req = req.get("modulation_index")
    pf_req = req.get("power_factor")
    if m_req is not None:
        m = _f(m_req, "modulation_index", positive=True)
        s_total = 3.0 * (m * v_dc / (2.0 * math.sqrt(2.0))) * i_leg_3ph
        pf = p_ac / s_total if s_total > 0 else 0.0
    elif pf_req is not None:
        pf = _f(pf_req, "power_factor", positive=True)
        s_total = p_ac / pf
        m = s_total * 2.0 * math.sqrt(2.0) / (3.0 * v_dc * i_leg_3ph)
    else:
        raise ControllerRefusal(
            "send modulation_index (the duty's own inverter block carries it) "
            "or power_factor — the bridge's duty cycle cannot be guessed from "
            "the current alone", ["modulation_index", "power_factor"],
            code="missing_modulation")
    warnings: List[str] = []
    if pf > 1.0 + 1e-6:
        warnings.append(
            f"the stated modulation index {m:.3f} and the leg current "
            f"{i_leg_3ph:.0f} A give less apparent power than the "
            f"{p_ac / 1e3:.0f} kW this duty draws (power factor {pf:.3f} > 1): "
            "one of the three does not belong to this operating point")
        pf = 1.0
    if m > 1.0:
        warnings.append(
            f"modulation index {m:.3f} > 1 — the bridge is OVERMODULATED; the "
            "sine-triangle switching functions this model integrates are not "
            "what such a bridge does, and the losses below read low")
    phi_deg = math.degrees(math.acos(min(max(pf, -1.0), 1.0)))

    coils_per_phase = max(1, len(coils) // 3)
    v_machine_phase_rms = (p_ac / pf) / (3.0 * i_ph) if (pf > 0 and i_ph > 0) else 0.0
    v_coil_rms = v_machine_phase_rms / coils_per_phase

    # ── per-bridge leg currents and modulation ─────────────────────────────
    n_3ph = sum(1 for b in topo.bridges if b.kind == "three_phase_2l")
    legs_spec: Dict[Tuple[str, str], Dict[str, float]] = {}
    for b in topo.bridges:
        if b.kind == "three_phase_2l":
            if n_3ph >= 2 and set_split == "power_split":
                i_b, m_b = i_leg_3ph / n_3ph, m
            elif n_3ph >= 2:
                i_b, m_b = i_leg_3ph, m / n_3ph
            else:
                i_b, m_b = i_leg_3ph, m
            b.modulation = "sine_triangle"
            for k, lg in enumerate(b.legs):
                legs_spec[(b.id, lg.name)] = {
                    "i_peak_A": i_b * math.sqrt(2.0),
                    "i_phase_deg": -120.0 * k + b.phase_shift_deg,
                    "v_phase_deg": -120.0 * k + b.phase_shift_deg + phi_deg,
                    "m": m_b,
                }
        else:                                        # H-bridge
            m_b = (v_coil_rms * math.sqrt(2.0) / v_dc) if v_dc > 0 else 0.0
            base = b.phase_shift_deg
            for k, lg in enumerate(b.legs):
                legs_spec[(b.id, lg.name)] = {
                    "i_peak_A": i_ph * math.sqrt(2.0) * (1 if k == 0 else -1),
                    "i_phase_deg": base,
                    "v_phase_deg": base + phi_deg + (0.0 if k == 0 else 180.0),
                    "m": m_b,
                }
    m_by_bridge = {b.id: legs_spec[(b.id, b.legs[0].name)]["m"]
                   for b in topo.bridges}
    over = [b.id for b in topo.bridges if m_by_bridge[b.id] > 1.0]
    if over:
        warnings.append(
            "bridge(s) " + ", ".join(over) + " need a modulation index above 1 "
            f"on a {v_dc:.0f} V link — this topology cannot make the voltage "
            "this operating point needs without overmodulation or a higher bus")

    # ── the thermal loop ───────────────────────────────────────────────────
    plate = _build_cooling(req.get("cooling") or {})
    r_tim = _f((req.get("r_tim_k_w")), "r_tim_k_w", default=DEFAULT_TIM_K_W)
    r_spread = _f(req.get("r_spread_k_w"), "r_spread_k_w", default=0.0)
    r_jc = card.r_th_jc_k_w
    cooling_mode = str(plate.as_dict().get("mode") or "liquid")

    dead_us = _f(req.get("dead_time_us"), "dead_time_us", default=0.5)
    if dead_us < 0.0:
        raise ControllerRefusal("dead_time_us cannot be negative", ["dead_time_us"])
    dead_s = dead_us * 1e-6
    v_gs_on = _f(req.get("v_gs_on_V"), "v_gs_on_V", default=18.0)
    v_gs_off = _f(req.get("v_gs_off_V"), "v_gs_off_V", default=0.0)
    r_g = req.get("r_g_ext_ohm")
    r_g = None if r_g is None else _f(r_g, "r_g_ext_ohm")
    policy = str(req.get("e_oss_policy") or "included_in_eon").strip().lower()
    if policy not in E_OSS_POLICIES:
        raise ControllerRefusal("e_oss_policy must be "
                                + " or ".join(E_OSS_POLICIES), ["e_oss_policy"])

    setups: Dict[str, wf.ModulatorSetup] = {}
    for b in topo.bridges:
        setups[b.id] = wf.ModulatorSetup(
            f_elec_hz=f_el, f_carrier_hz=f_sw, v_dc_V=v_dc,
            modulation_index=m_by_bridge[b.id],
            samples_per_carrier=int(req.get("samples_per_carrier") or 24),
            dead_time_s=dead_s)
    u = setups[topo.bridges[0].id].grid()
    leg_current: Dict[Tuple[str, str], np.ndarray] = {}
    for (bid, lname), spec in legs_spec.items():
        leg_current[(bid, lname)] = spec["i_peak_A"] * np.sin(
            2.0 * math.pi * u + math.radians(spec["i_phase_deg"]))

    t_cap = card.t_j_curve_max_c()
    t_j = float(req.get("t_j_start_c") or (plate.t_in_c + 20.0))
    iters = 0
    clamped = False
    diverged = False
    per_leg: Dict[Tuple[str, str], Dict[str, Any]] = {}
    p_total = 0.0
    t_case = plate.t_in_c
    t_cool_mean = plate.t_in_c
    cp: Dict[str, Any] = {}
    r_dev = r_jc + r_tim + r_spread
    while True:
        iters += 1
        t_eval = min(t_j, t_cap)
        clamped = clamped or (t_j > t_cap + 1e-9)
        # ``t_case`` here is the PREVIOUS pass's case-temperature estimate
        # (seeded at the cooling object's own inlet/ambient) — only
        # ``AirStillCooling`` reads it (natural convection depends on the
        # wall), the liquid/forced-air films ignore it, and recomputing
        # ``cp`` on every pass for those two is harmless: they are
        # temperature-independent, so this reproduces the pre-2026-09-22
        # ColdPlate-only result bit-for-bit.
        cp = plate.resistance(t_wall_c=t_case)
        r_dev = r_jc + r_tim + r_spread + float(cp.get("r_film_per_device_k_w") or 0.0)
        per_leg = {}
        p_total = 0.0
        for b in topo.bridges:
            for lg in b.legs:
                key = (b.id, lg.name)
                res = _leg_losses(card=card, i_leg=leg_current[key],
                                  n_par=b.devices_parallel, f_sw=f_sw,
                                  t_j_c=t_eval, v_dc=v_dc, v_gs_on=v_gs_on,
                                  v_gs_off=v_gs_off, r_g=r_g,
                                  dead_time_s=dead_s, e_oss_policy=policy)
                per_leg[key] = res
                p_total += res["p_total_W"]
        m_dot = float(cp.get("m_dot_kg_s") or 0.0)
        cpj = float(cp.get("cp_j_kgk") or 1.0)
        rise = p_total / (m_dot * cpj) if m_dot * cpj > 0 else 0.0
        t_cool_mean = plate.t_in_c + 0.5 * rise
        t_case = t_cool_mean + p_total * float(cp["r_k_w"])
        p_dev_max = max((r["p_total_W"] / (2 * max(int(b.devices_parallel), 1))
                         for b in topo.bridges for lg in b.legs
                         for r in [per_leg[(b.id, lg.name)]]), default=0.0)
        t_new = t_case + p_dev_max * r_dev
        if t_new > card.t_j_max_c + 200.0:
            # Thermal runaway of the ITERATION, which is the model's way of
            # saying the design has no steady state: R_DS(on) rises faster with
            # T_j than the coldplate can take the heat away.  Stop, report the
            # last honest state, refuse.
            t_j, diverged = t_new, True
            break
        if abs(t_new - t_j) < TOL_K or iters >= _MAX_ITER:
            t_j = t_new
            break
        t_j = t_j + 0.6 * (t_new - t_j)          # damped, R(T) is convex
    converged = iters < _MAX_ITER and not diverged

    # ── per switch / per device / per bridge ───────────────────────────────
    bridges_out: List[Dict[str, Any]] = []
    split: Dict[str, float] = {"conduction_W": 0.0, "third_quadrant_W": 0.0,
                               "switching_W": 0.0, "e_oss_W": 0.0}
    t_j_max_seen = -1e9
    i_dev_max = 0.0
    extrapolated = False
    card_notes: List[str] = []
    for b in topo.bridges:
        legs_out = []
        pb = 0.0
        for lg in b.legs:
            r = per_leg[(b.id, lg.name)]
            npb = max(int(b.devices_parallel), 1)
            p_sw_each = r["p_total_W"] / 2.0          # the leg's two switches
            p_dev = p_sw_each / npb
            tj = t_case + p_dev * r_dev
            t_j_max_seen = max(t_j_max_seen, tj)
            i_dev_max = max(i_dev_max, r["i_device_rms_A"])
            extrapolated = extrapolated or r["extrapolated"]
            if not card_notes:
                card_notes = list(r["notes"])
            pb += r["p_total_W"]
            for k in ("conduction", "third_quadrant", "switching"):
                split[k + "_W"] += r["p_" + k + "_W"]
            split["e_oss_W"] += r["p_e_oss_W"]
            legs_out.append({
                "leg": lg.name, "coils": list(lg.coils),
                "i_leg_rms_A": round(r["i_leg_rms_A"], 1),
                "i_switch_rms_A": round(r["i_switch_rms_A"], 1),
                "i_device_rms_A": round(r["i_device_rms_A"], 1),
                "i_device_peak_A": round(r["i_device_peak_A"], 1),
                "p_conduction_W": round(r["p_conduction_W"], 1),
                "p_third_quadrant_W": round(r["p_third_quadrant_W"], 1),
                "p_switching_W": round(r["p_switching_W"], 1),
                "p_switching_on_W": round(r["p_switching_on_W"], 1),
                "p_switching_off_W": round(r["p_switching_off_W"], 1),
                "p_switching_recovery_W": round(r["p_switching_recovery_W"], 1),
                "p_e_oss_W": round(r["p_e_oss_W"], 1),
                "p_leg_W": round(r["p_total_W"], 1),
                "p_switch_W": round(p_sw_each, 1),
                "p_device_W": round(p_dev, 2),
                "t_j_c": round(tj, 1),
                "r_ds_on_mohm": round(r["r_ds_on_mohm"], 2),
            })
        d = b.as_dict()
        d.update({"legs": legs_out, "p_loss_W": round(pb, 1),
                  "modulation_index": round(m_by_bridge[b.id], 4)})
        bridges_out.append(d)

    # ── DC link ────────────────────────────────────────────────────────────
    states, currents = [], []
    for b in topo.bridges:
        st = setups[b.id]
        for lg in b.legs:
            spec = legs_spec[(b.id, lg.name)]
            states.append(wf.switch_states(st, u, spec["v_phase_deg"]))
            currents.append(leg_current[(b.id, lg.name)])
    dc = wf.dc_link_current(states, currents)

    # ── the datasheet limits, and the verdicts that follow from them ───────
    i_pk_dev = max((per_leg[(b.id, lg.name)]["i_device_peak_A"]
                    for b in topo.bridges for lg in b.legs), default=0.0)
    limits = limit_rows(
        card=card, v_dc_V=v_dc, t_case_c=min(t_case, t_cap),
        t_j_c=t_j_max_seen, i_device_rms_A=i_dev_max,
        i_device_peak_A=i_pk_dev,
        i_device_reverse_peak_A=(i_pk_dev if dead_us > 0 else 0.0),
        v_gs_on_V=v_gs_on, v_gs_off_V=v_gs_off, dead_time_us=dead_us)

    violations: List[str] = []
    for r in limits:
        if r["verdict"] == "fail":
            violations.append(
                f"{r['name']}: {r['value']} {r['unit']} against the "
                f"{card.part} limit of {r['limit']} {r['unit']}"
                + (f" — {r['note']}" if r["note"] else ""))
        elif r["verdict"] == "warn":
            warnings.append(f"{r['name']}: {r['note']}")
    if diverged:
        violations.append(
            "THERMAL RUNAWAY: the junction temperature has no steady state "
            "here — R_DS(on) rises with T_j faster than this coldplate removes "
            "the heat, so the loop diverges instead of settling")
    elif not converged:
        violations.append(f"the junction temperature did not settle within "
                          f"{_MAX_ITER} iterations")
    if clamped:
        warnings.append(
            f"the junction temperature ran past the hottest point the "
            f"{card.part} card tabulates ({t_cap:.0f} degC), so the device "
            f"characteristics were held at that value; the T_j reported is the "
            f"unclamped one and it is a LOWER bound")
    if extrapolated:
        warnings.append(
            f"each device carries more current than the {card.part} card's "
            "switching-energy curves cover — the energies above their last "
            "tabulated point were linearly extrapolated")

    # ── efficiency ─────────────────────────────────────────────────────────
    eta_inv = p_ac / (p_ac + p_total) if (p_ac + p_total) > 0 else None
    eta_wall = (eta_inv * eta_shaft) if (eta_inv and eta_shaft) else None

    # ── the waveform the motor sees ────────────────────────────────────────
    # The PICTURE gets its own, finer grid.  The loss integral does not need
    # the dead time resolved (it is integrated analytically from t_d), but the
    # chart is read for exactly one thing — the distortion at the current zero
    # crossings — and at the loss grid's resolution a 0.5 us window falls
    # between two samples and is invisible.  Four samples per dead-time window,
    # capped so the exported series stays small.
    base = setups[topo.bridges[0].id]
    spc = base.samples_per_carrier
    if dead_s > 0.0:
        spc = max(spc, min(400, int(math.ceil(4.0 / (dead_s * f_sw)))))
    st0 = wf.ModulatorSetup(
        f_elec_hz=base.f_elec_hz, f_carrier_hz=base.f_carrier_hz,
        v_dc_V=base.v_dc_V, modulation_index=base.modulation_index,
        samples_per_carrier=spc, dead_time_s=dead_s)
    t_draw = min(t_j_max_seen, t_cap)
    r_ds = card.r_ds_on_ohm(t_draw, v_gs_on)

    def _v_sd(arr: np.ndarray) -> np.ndarray:
        n0 = max(int(topo.bridges[0].devices_parallel), 1)
        return np.array([card.v_sd_V(float(x) / n0, t_draw, v_gs_off)
                         for x in arr])

    dead_n = wf.dead_samples_for(st0)
    if dead_us > 0 and dead_n < 1:
        warnings.append(
            f"the {dead_us:g} us dead time is shorter than one sample of the "
            "waveform grid, so it is not drawn on the chart; the LOSS it causes "
            "is integrated analytically and IS in the numbers")
    waves = wf.coil_waveforms(
        bridges=topo.bridges, setup=st0, legs=legs_spec, r_ds_ohm=r_ds / max(n_par, 1),
        v_sd=_v_sd, dead_samples=dead_n)
    waves["dead_time_error_V"] = round(
        float(dead_us * 1e-6 * st0.f_carrier_eff_hz
              * (v_dc + 2.0 * card.v_sd_V(i_ph * math.sqrt(2.0) / max(n_par, 1),
                                          t_draw, v_gs_off))), 2)

    # ── the R_th(j-a) cross-check, air-cooled modes only (owner's brief: a
    # PQFN/source-down part's path is pad -> PCB copper -> air; state clearly
    # which resistance the solve actually used) ────────────────────────────
    cooling_notes: List[str] = []
    if cooling_mode in ("air_forced", "air_still"):
        r_ja = card.r_th_ja_k_w
        if r_ja is not None:
            used = r_jc + r_tim + r_spread + float(cp.get("r_film_per_device_k_w") or 0.0)
            cooling_notes.append(
                f"{card.part} publishes R_th(j-a) = {r_ja:g} K/W as its own "
                "still-air limit"
                + (f" ({card.r_th_ja_note.strip()})" if card.r_th_ja_note else "")
                + f" — this solve does NOT use that number; it derates on "
                  f"R_th(j-c) {r_jc:g} + R_TIM {r_tim:g} + R_spread {r_spread:g} "
                  "(the PCB-copper-spreading path from the pad to THIS board, "
                  "a stated assumption, default 0 K/W unless given) + R_film "
                  f"from the chosen air correlation = {used:g} K/W per device; "
                  "the datasheet's R_th(j-a) is reported here only as a "
                  "cross-check, not an input")
        else:
            cooling_notes.append(
                f"{card.part} publishes no R_th(j-a) — no still-air "
                "cross-check is available for this device; the solve derates "
                "on R_th(j-c) + R_TIM + R_spread + R_film only")

    return {
        "ok": not violations,
        # ``feasible`` is the word the limit table answers: every published
        # limit of the chosen part is inside its number.  It is not the same
        # question as "did the solver converge" — that is `thermal.converged`.
        "feasible": not violations,
        "limits": limits,
        "limits_verdict": ("fail" if violations else
                           "warn" if any(r["verdict"] == "warn" for r in limits)
                           else "pass"),
        "device": card.part,
        "device_row": card.row(i_switch_rms_A=i_dev_max * max(n_par, 1)),
        "provenance": card.provenance(),
        "topology": topo.as_dict(),
        "set_split": set_split if n_3ph >= 2 else None,
        "bridges": bridges_out,
        "losses": {
            "conduction_W": round(split["conduction_W"], 1),
            "third_quadrant_W": round(split["third_quadrant_W"], 1),
            "switching_W": round(split["switching_W"], 1),
            "e_oss_W": round(split["e_oss_W"], 1),
            "e_oss_reference_W": round(
                sum(per_leg[(b.id, lg.name)]["p_e_oss_reference_W"]
                    for b in topo.bridges for lg in b.legs), 1),
            "total_W": round(p_total, 1),
            "e_oss_policy": policy,
            "switching_energy_source": card.switching_energy_source(),
        },
        "thermal": {
            "t_j_max_c": round(t_j_max_seen, 1),
            "t_j_limit_c": card.t_j_max_c,
            "margin_K": round(card.t_j_max_c - t_j_max_seen, 1),
            "t_case_c": round(t_case, 1),
            "t_coolant_mean_c": round(t_cool_mean, 1),
            "t_coolant_in_c": plate.t_in_c,
            "coolant_rise_K": round(2.0 * (t_cool_mean - plate.t_in_c), 2),
            "r_th_jc_k_w": r_jc,
            "r_th_jc_basis": card.r_th_jc_basis,
            "r_tim_k_w": r_tim,
            "r_spread_k_w": r_spread,
            "r_coldplate_k_w": round(float(cp["r_k_w"]), 5),
            "r_film_per_device_k_w": round(
                float(cp.get("r_film_per_device_k_w") or 0.0), 5),
            "cooling_mode": cooling_mode,
            # "coldplate" is the pre-2026-09-22 key name, kept for every
            # reader that already looks for it (the report, the tab); it now
            # holds whichever cooling object solved this controller — a
            # liquid coldplate, a forced-air heatsink/plate, or a still-air
            # one — and its own ``mode`` field says which.
            "coldplate": {**plate.as_dict(),
                          **{k: (round(v, 4) if isinstance(v, float) else v)
                             for k, v in cp.items()}},
            "iterations": iters, "converged": converged, "tol_K": TOL_K,
        },
        "dc_link": {k: round(v, 1) for k, v in dc.items()},
        "efficiency": {
            "inverter": None if eta_inv is None else round(eta_inv, 5),
            "shaft": None if eta_shaft is None else round(eta_shaft, 5),
            "wall_to_shaft": None if eta_wall is None else round(eta_wall, 5),
            # WHY it is absent, when it is: never a silent blank — the route
            # (``routes/controller.py::_duty_defaults``) hands back the one
            # reason it could not form even the electromagnetic shaft
            # efficiency (no bearings AND no rotor power/loss on this
            # record); a caller that skipped the route (a raw API call with
            # no ``efficiency_shaft``) gets the generic sentence instead.
            "note": ("wall-to-shaft = inverter efficiency x the ONE shaft "
                     "efficiency of this duty's record (report.shaft_view: "
                     "bearings + windage off the shaft where a bearing model "
                     "is assigned, the electromagnetic shaft efficiency "
                     "otherwise)" if eta_shaft is not None else
                     (req.get("_efficiency_shaft_note")
                      or "no shaft efficiency is known for this point — "
                         "wall-to-shaft could not be computed")),
        },
        "point": {
            "i_phase_rms_A": round(i_ph, 1),
            "i_leg_rms_3ph_A": round(i_leg_3ph, 1),
            # The connection is the MOTOR's, never this module's: it comes from
            # the duty's own record and the route reports where it came from
            # (owner 2026-09-22: «соединение звезда/треугольник у нас
            # определяется на моторе»).
            "connection_from": "the duty (star/delta is a property of the motor)",
            "i_switch_rms_max_A": round(
                max((per_leg[(b.id, lg.name)]["i_switch_rms_A"]
                     for b in topo.bridges for lg in b.legs), default=0.0), 1),
            "star_delta": sd,
            "p_ac_W": round(p_ac, 1),
            "f_elec_hz": round(f_el, 3),
            "f_carrier_hz": round(f_sw, 1),
            "f_carrier_eff_hz": round(st0.f_carrier_eff_hz, 1),
            "modulation_index": round(m, 4),
            "power_factor": round(pf, 4),
            "load_angle_deg": round(phi_deg, 2),
            "v_dc_V": round(v_dc, 2),
            "v_machine_phase_rms_V": round(v_machine_phase_rms, 1),
            "v_coil_rms_V": round(v_coil_rms, 1),
            "coils_per_phase": coils_per_phase,
            "rpm": req.get("rpm"),
        },
        "settings": {
            "devices_parallel": n_par,
            "dead_time_us": dead_us,
            "v_gs_on_V": v_gs_on, "v_gs_off_V": v_gs_off,
            "r_g_ext_ohm": r_g,
            "samples_per_carrier": st0.samples_per_carrier,
            "grid_points": int(u.size),
        },
        "waveforms": waves,
        "warnings": warnings,
        "violations": violations,
        "model_notes": card_notes + [
            f"switching-energy source for {card.part}: "
            f"{card.switching_energy_source().replace('_', ' ')} "
            + ("(the datasheet's own E_on/E_off table)"
               if card.switching_energy_source() == "curves" else
               "(first-order overlap-model estimate from switching times and "
               "gate charges — the card publishes no E_on/E_off table; treat "
               "as a lower bound)"),
            "conduction is integrated over the whole period because with "
            "synchronous rectification the leg current is always in a channel; "
            "the duty only decides WHICH switch carries it",
            "one hard turn-on, one hard turn-off and one body-diode recovery "
            "per carrier period per leg",
            ("E_oss is reported but NOT added: the datasheet E_on is a "
             "hard-switching half-bridge measurement and already contains it"
             if policy == "included_in_eon" else
             "E_oss is ADDED on top of the datasheet E_on (pessimistic bound; "
             "it is probably already inside the measured E_on)"),
            f"the two switches of a leg are taken to share its loss equally "
            f"(sinusoidal current, symmetric modulation over a full period)",
            f"{n_par} device(s) per switch share the current EQUALLY — a "
            f"layout-dependent assumption, and the usual reason a real stack "
            f"derates",
            "below the card's lowest measured current the switching-energy "
            "curves are extrapolated on the straight line through their two "
            "lowest points — that is where a sinusoidal current's zero "
            "crossings live",
        ] + cooling_notes,
    }
