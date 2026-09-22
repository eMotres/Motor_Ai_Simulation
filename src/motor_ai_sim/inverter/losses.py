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

__all__ = ["ControllerRefusal", "ColdPlate", "solve_controller",
           "DEFAULT_TIM_K_W", "TOL_K", "E_OSS_POLICIES", "SET_SPLITS"]


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

    def resistance(self) -> Dict[str, Any]:
        """``{r_k_w, h_w_m2k, ...}`` — the plate-to-coolant thermal resistance."""
        if self.r_override_k_w is not None:
            return {"r_k_w": float(self.r_override_k_w),
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
        return {"r_k_w": r, "h_w_m2k": h_film, "reynolds": re, "nusselt": nu,
                "regime": regime, "velocity_mps": v,
                "hydraulic_diameter_mm": d_h * 1e3, "wetted_area_m2": area,
                "m_dot_kg_s": props.rho * q, "cp_j_kgk": props.cp,
                "basis": (f"{regime} flow in {n} x {self.channel_w_mm:g} x "
                          f"{self.channel_h_mm:g} mm channels, {v:.2f} m/s"
                          + (f", wetted area x{fin:g} for fins" if fin > 1 else "")
                          + " (cooling_models.pipe_nusselt — the same ladder "
                            "the motor jacket uses)")}

    def as_dict(self) -> Dict[str, Any]:
        return {"coolant": self.coolant, "flow_lpm": self.flow_lpm,
                "t_in_c": self.t_in_c, "n_channels": self.n_channels,
                "channel_w_mm": self.channel_w_mm,
                "channel_h_mm": self.channel_h_mm, "length_mm": self.length_mm,
                "fin_area_factor": self.fin_area_factor,
                "r_override_k_w": self.r_override_k_w}


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
                          v_gs_off_V=v_gs_off, r_g_ext_ohm=r_g)
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
      cooling   ``cooling`` -> :class:`ColdPlate` fields, ``r_tim_k_w``,
                ``r_spread_k_w``
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
    plate = ColdPlate(
        **{k: v for k, v in (req.get("cooling") or {}).items()
           if k in ColdPlate.__dataclass_fields__})
    cp = plate.resistance()
    r_tim = _f((req.get("r_tim_k_w")), "r_tim_k_w", default=DEFAULT_TIM_K_W)
    r_spread = _f(req.get("r_spread_k_w"), "r_spread_k_w", default=0.0)
    r_jc = card.r_th_jc_k_w
    r_dev = r_jc + r_tim + r_spread

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
    while True:
        iters += 1
        t_eval = min(t_j, t_cap)
        clamped = clamped or (t_j > t_cap + 1e-9)
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

    # ── verdicts ───────────────────────────────────────────────────────────
    violations: List[str] = []
    if t_j_max_seen > card.t_j_max_c:
        violations.append(
            f"junction temperature {t_j_max_seen:.0f} degC is above the "
            f"{card.part} limit of {card.t_j_max_c:.0f} degC — this controller "
            f"does not run at this point (more devices in parallel, a colder "
            f"coolant or a lower carrier)")
    i_rating = card.i_d_continuous(min(t_case, t_cap))
    if i_rating is not None and i_dev_max > i_rating:
        violations.append(
            f"each device carries {i_dev_max:.0f} A rms and {card.part} is rated "
            f"{i_rating:.0f} A continuous at a {t_case:.0f} degC case")
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

    return {
        "ok": not violations,
        "device": card.part,
        "device_row": card.row(),
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
            "note": "wall-to-shaft = inverter efficiency x the ONE shaft "
                    "efficiency of this duty's coupled record",
        },
        "point": {
            "i_phase_rms_A": round(i_ph, 1),
            "i_leg_rms_3ph_A": round(i_leg_3ph, 1),
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
        ],
    }
