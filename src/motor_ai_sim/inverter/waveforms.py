"""The signal the motor actually sees — PWM with dead time and device drops.

The coupled loop's present modulator is, by its own record, *"ideal two-level,
synchronous regular-sampled centre-aligned sine-triangle; no dead time, no
device drops, ideal bus"*.  This module is the non-ideal one: the same carrier,
but the leg terminal follows the CURRENT during the dead-time windows and every
conducting device drops its own volts.

Two things come out of it and both matter downstream:

1. ``switch_states`` — the switching functions of every leg on a fine time
   grid.  The loss model integrates the device currents on this grid (rather
   than quoting a closed-form duty-cycle integral), and the DC-link ripple is
   just ``sum(state * i_leg)`` on the same grid, which is exact for the ideal
   modulator and works unchanged for one bridge, two shifted bridges or six
   H-bridges.
2. ``coil_waveforms`` — per COIL voltage and current, the interface Stage 2
   will hand to the electromagnetic transient (``drive: "inverter"``).  Per
   coil, not per phase, deliberately: the H-bridge topology drives every coil
   independently and a phase-level interface could not express it; a
   three-phase topology simply gives all coils of a phase the same series
   current.

DEAD-TIME DISTORTION is the visible physics here, and the test in
``tests/test_controller.py`` is written on it: with both switches off the leg
terminal is pulled by the CURRENT, so the average output voltage is pushed
DOWN when the current leaves the leg and UP when it enters.  Around a current
zero crossing the error flips sign within one carrier period, which is the
classic flat spot; the error magnitude is ``(2*t_d*f_sw) * (V_dc + 2*V_SD)``
per half, and that is what the shape test measures.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

__all__ = ["ModulatorSetup", "carrier_reference", "leg_duty", "switch_states",
           "leg_terminal_voltage", "dc_link_current", "dead_time_error_V",
           "coil_waveforms", "dead_samples_for"]


@dataclass
class ModulatorSetup:
    """Everything the modulator needs, resolved and stated."""
    f_elec_hz: float
    f_carrier_hz: float
    v_dc_V: float
    modulation_index: float          # m = V_phase_peak / (V_dc/2)
    samples_per_carrier: int = 40
    dead_time_s: float = 0.0
    phase_shift_deg: float = 0.0     # of this bridge's fundamental

    @property
    def carriers_per_period(self) -> float:
        return self.f_carrier_hz / max(self.f_elec_hz, 1e-9)

    def grid(self) -> np.ndarray:
        """One electrical period, an integer number of carrier sub-steps.

        The carrier count is ROUNDED to a whole number of carriers per
        electrical period (synchronous modulation, exactly as the coupled PWM
        loop does it) so the period closes and an rms over it is a true rms.
        The effective carrier is reported by :meth:`f_carrier_eff_hz`.
        """
        n_car = max(int(round(self.carriers_per_period)), 1)
        n = n_car * max(int(self.samples_per_carrier), 8)
        return np.arange(n, dtype=float) / n

    @property
    def f_carrier_eff_hz(self) -> float:
        return max(int(round(self.carriers_per_period)), 1) * self.f_elec_hz


def carrier_reference(setup: ModulatorSetup, u: np.ndarray) -> np.ndarray:
    """Triangular carrier in [-1, 1], centre-aligned, on normalised time ``u``."""
    n_car = max(int(round(setup.carriers_per_period)), 1)
    x = (u * n_car) % 1.0
    return 1.0 - 4.0 * np.abs(x - 0.5)


def leg_duty(setup: ModulatorSetup, u: np.ndarray, phase_deg: float,
             third_harmonic: float = 0.0) -> np.ndarray:
    """High-side duty of one leg: ``0.5*(1 + m*sin(theta + phi))``, clipped."""
    th = 2.0 * math.pi * u + math.radians(phase_deg + setup.phase_shift_deg)
    ref = setup.modulation_index * np.sin(th)
    if third_harmonic:
        ref = ref + third_harmonic * setup.modulation_index * np.sin(3.0 * th)
    return np.clip(0.5 * (1.0 + ref), 0.0, 1.0)


def switch_states(setup: ModulatorSetup, u: np.ndarray, phase_deg: float,
                  third_harmonic: float = 0.0) -> np.ndarray:
    """1 while the HIGH-side switch is commanded on, 0 while the low side is.

    Regular-sampled sine-triangle: the reference is compared with the carrier
    sample by sample.  Dead time is NOT in this array — it is a command, and
    the two commands are complementary by construction; where the dead time
    changes the OUTPUT is :func:`leg_terminal_voltage`.
    """
    ref = 2.0 * leg_duty(setup, u, phase_deg, third_harmonic) - 1.0
    return (ref > carrier_reference(setup, u)).astype(float)


def dead_samples_for(setup: ModulatorSetup) -> int:
    """How many grid samples one dead-time window occupies.

    Built in SAMPLES because that is the resolution the grid has.  A dead time
    shorter than one sample rounds to ZERO here and the caller reports it as
    unresolved rather than silently applying a distortion it cannot draw; the
    LOSS model never uses this number — it integrates the dead-time energy
    analytically from ``t_d`` itself.
    """
    n_car = max(int(round(setup.carriers_per_period)), 1)
    spc = max(int(setup.samples_per_carrier), 8)
    dt_sample = 1.0 / (setup.f_carrier_eff_hz * spc)
    return int(round(max(setup.dead_time_s, 0.0) / dt_sample))


def leg_terminal_voltage(setup: ModulatorSetup, u: np.ndarray,
                         state: np.ndarray, i_leg: np.ndarray, *,
                         r_ds_ohm: float, v_sd: Callable[[np.ndarray], np.ndarray],
                         dead_samples: int = 0) -> np.ndarray:
    """Leg terminal voltage w.r.t. the DC-link MIDPOINT, volts.

    Four cases, and the sign of the current decides two of them:

    ``HS on,  i > 0``  +V/2 - i*R_ds      (channel, first quadrant)
    ``HS on,  i < 0``  +V/2 + |i|*R_ds    (channel, third quadrant)
    ``LS on,  i > 0``  -V/2 - i*R_ds
    ``LS on,  i < 0``  -V/2 + |i|*R_ds
    ``dead,   i > 0``  -V/2 - V_SD(i)     (low-side BODY DIODE)
    ``dead,   i < 0``  +V/2 + V_SD(|i|)   (high-side body diode)
    """
    half = 0.5 * float(setup.v_dc_V)
    a = np.abs(i_leg)
    v = np.where(state > 0.5, half, -half) - np.sign(i_leg) * a * float(r_ds_ohm)
    if dead_samples > 0:
        dead = np.zeros(u.size, dtype=bool)
        change = np.flatnonzero(np.diff(state, prepend=state[-1]) != 0.0)
        for c in change:
            dead[c:c + dead_samples] = True
        vd = v_sd(a)
        v = np.where(dead, np.where(i_leg >= 0.0, -half - vd, half + vd), v)
    return v


def dead_time_error_V(setup: ModulatorSetup, i_leg: np.ndarray,
                      v_sd_at: float) -> np.ndarray:
    """The classic dead-time voltage error, for the shape test and the note.

    ``dV = sign(i) * t_d * f_sw * (V_dc + 2*V_SD)`` — a square wave in the sign
    of the current, i.e. a step at every zero crossing.
    """
    return (-np.sign(i_leg) * setup.dead_time_s * setup.f_carrier_eff_hz
            * (float(setup.v_dc_V) + 2.0 * float(v_sd_at)))


def dc_link_current(states: Sequence[np.ndarray],
                    currents: Sequence[np.ndarray]) -> Dict[str, float]:
    """DC-link current from the switching functions — mean, rms, capacitor rms.

    ``i_dc = sum(s_leg * i_leg)`` is exact for an ideal two-level bridge: the
    high-side switch either connects the leg to the positive rail or it does
    not.  The capacitor carries whatever the bus does not, so
    ``I_cap,rms = sqrt(I_dc,rms^2 - I_dc,mean^2)``.
    """
    tot = None
    for s, i in zip(states, currents):
        term = s * i
        tot = term if tot is None else tot + term
    if tot is None:
        return {"i_dc_mean_A": 0.0, "i_dc_rms_A": 0.0, "i_cap_rms_A": 0.0,
                "i_dc_pp_A": 0.0}
    mean = float(np.mean(tot))
    rms = float(np.sqrt(np.mean(tot ** 2)))
    return {"i_dc_mean_A": mean, "i_dc_rms_A": rms,
            "i_cap_rms_A": float(math.sqrt(max(rms ** 2 - mean ** 2, 0.0))),
            "i_dc_pp_A": float(np.max(tot) - np.min(tot))}


def coil_waveforms(*, bridges: Sequence[Any], setup: ModulatorSetup,
                   legs: Dict[Tuple[str, str], Dict[str, float]],
                   r_ds_ohm: float, v_sd: Callable[[np.ndarray], np.ndarray],
                   dead_samples: int, cap: int = 4000) -> Dict[str, Any]:
    """One electrical period of coil voltage and current, per coil.

    THE STAGE-2 INTERFACE.  Shape, once, so Stage 2 adds a consumer and not a
    format:

    ``{"t_s": [...], "f_elec_hz": .., "coils": {"<index>": {"bridge": id,
    "v_V": [...], "i_A": [...], "v_mean_V": .., "v_rms_V": ..}}, ...}``

    The voltage of a coil on a three-phase leg is that LEG's terminal voltage
    (the coil sees it through the winding); on an H-bridge it is the DIFFERENCE
    of the bridge's two legs, which is what makes unipolar modulation visible
    as a three-level waveform at the coil.
    """
    u = setup.grid()
    t = u / max(setup.f_elec_hz, 1e-9)
    leg_v: Dict[Tuple[str, str], np.ndarray] = {}
    leg_i: Dict[Tuple[str, str], np.ndarray] = {}
    for b in bridges:
        for lg in b.legs:
            key = (b.id, lg.name)
            spec = legs.get(key) or {}
            ip = float(spec.get("i_peak_A", 0.0))
            i_ph = float(spec.get("i_phase_deg", 0.0))
            v_ph = float(spec.get("v_phase_deg", i_ph))
            i = ip * np.sin(2.0 * math.pi * u + math.radians(i_ph))
            s = switch_states(setup, u, v_ph)
            leg_i[key] = i
            leg_v[key] = leg_terminal_voltage(setup, u, s, i,
                                              r_ds_ohm=r_ds_ohm, v_sd=v_sd,
                                              dead_samples=dead_samples)
    step = max(1, int(math.ceil(u.size / max(int(cap), 100))))
    out: Dict[str, Any] = {
        "f_elec_hz": round(float(setup.f_elec_hz), 4),
        "f_carrier_eff_hz": round(float(setup.f_carrier_eff_hz), 2),
        "dead_time_us": round(setup.dead_time_s * 1e6, 3),
        "samples": int(u.size),
        "t_s": [round(float(x), 9) for x in t[::step]],
        "coils": {},
    }
    for b in bridges:
        for c in b.coils:
            if b.kind == "h_bridge" and len(b.legs) == 2:
                kp = (b.id, b.legs[0].name)
                kn = (b.id, b.legs[1].name)
                v = leg_v[kp] - leg_v[kn]
                i = leg_i[kp]
            else:
                lg = next((l for l in b.legs if c in l.coils), b.legs[0])
                key = (b.id, lg.name)
                v = leg_v[key]
                i = leg_i[key]
            out["coils"][str(c)] = {
                "bridge": b.id,
                "v_V": [round(float(x), 3) for x in v[::step]],
                "i_A": [round(float(x), 3) for x in i[::step]],
                "v_mean_V": round(float(np.mean(v)), 3),
                "v_rms_V": round(float(np.sqrt(np.mean(v ** 2))), 3),
                "i_rms_A": round(float(np.sqrt(np.mean(i ** 2))), 3),
            }
    return out


