"""PWM current ripple and phase-current THD, from the modulator itself.

Owner 2026-09-28: the ripple is computed from the ACTUAL modulator — carrier,
modulation (sine / SVPWM / third harmonic), m, V_dc, f1, the synchronous or
asynchronous carrier ratio — by a time-domain simulation of the leg switching
functions over one or several fundamental periods, sampled finely enough for
the carrier, and applied to the machine inductances.  No filter, no clamp, no
smoothing: every number below is an integral of the switched voltage.

MODEL
=====
Pole voltages ``v_k = V_dc * (s_k - 1/2)`` (ideal switches, no dead time —
the dead-time error is a low-order, current-dependent distortion that needs
the closed current loop; it is NOT in these numbers and the result says so).

The n = 3 (one inverter) or n = 6 (two inverters) pole voltages are projected
on an ORTHONORMAL vector-space decomposition (VSD):

``alpha-beta``  ``sqrt(2/n) * (cos th_k, sin th_k)`` — the only plane that
                links the air-gap flux; its inductance is the machine's
                synchronous inductance, ``L_d``/``L_q`` in the rotor frame.
``zero``        one zero sequence per set (the set's own neutral).
``x-y``         (dual three-phase only) the orthogonal complement of the
                four above — a plane that links no air-gap flux, limited by
                ``L_xy`` alone (a REQUIRED input: nothing in a 2-D field solve
                measures it).

With an orthonormal basis the phase-equivalent inductance of a plane is its
eigenvalue, so the per-plane current follows from the per-plane voltage.

Neutrals: ``isolated`` — every set's zero-sequence current is zero (its
voltage is the neutral-point displacement).  ``common`` (dual three-phase,
the two neutrals tied): the SUM zero sequence still carries nothing, the
DIFFERENCE ``(z1 - z2)/sqrt 2`` circulates through ``L_0`` (a required
input then).

alpha-beta with saliency is solved in the ROTOR frame, exactly, bin by bin:

    V_d = j w L_d I_d - w_e L_q I_q
    V_q = j w L_q I_q + w_e L_d I_d

(``w`` the rotor-frame frequency of the bin, ``w_e`` the electrical speed;
the stator resistance is neglected — at carrier frequencies ``R << w L``).
The dq DC bin is the positive-sequence fundamental the machine is TOLD to
make — it is the fundamental current, not ripple, and it is the one bin
removed.  The two bins with ``|w| = w_e`` are a stationary DC voltage, which
only the resistance limits; they are zeroed and their size is reported.

The window is ``K`` whole fundamental periods, ``K`` the smallest count that
closes the carrier (synchronous: 1).  An asynchronous ratio that does not
close within ``max_periods`` is simulated over the best window and WARNED:
the real current then carries sub-harmonics of the carrier.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

import numpy as np

__all__ = ["RippleRefusal", "pwm_ripple", "PWM_RIPPLE_MODULATIONS",
           "RIPPLE_SAMPLING", "NEUTRALS"]

PWM_RIPPLE_MODULATIONS = ("sine", "svpwm", "third_harmonic")
RIPPLE_SAMPLING = ("natural", "regular_symmetric")
NEUTRALS = ("isolated", "common")

#: Largest simulated grid (samples x phases stay in memory as float64).
_MAX_SAMPLES = 1_500_000


class RippleRefusal(ValueError):
    def __init__(self, message: str, fields: Optional[List[str]] = None) -> None:
        super().__init__(message)
        self.fields = list(fields or [])


def _refs(m: float, ang: np.ndarray, phases_rad: List[float], mod: str) -> np.ndarray:
    """Normalised leg references (in [-1, 1] inside the linear range) of ONE set."""
    r = np.array([m * np.cos(ang - p) for p in phases_rad])
    if mod == "svpwm":
        r = r - 0.5 * (r.max(axis=0) + r.min(axis=0))
    elif mod == "third_harmonic":
        r = r + (m / 6.0) * np.cos(3.0 * (ang - phases_rad[0]))
    return r


def _window(ratio: float, max_periods: int) -> Dict[str, Any]:
    best_k, best_err = 1, 1.0
    for k in range(1, max(int(max_periods), 1) + 1):
        err = abs(k * ratio - round(k * ratio))
        if err < 1e-6:
            return {"K": k, "closure_error_carriers": 0.0, "synchronous": k == 1,
                    "closes": True}
        if err < best_err - 1e-12:
            best_k, best_err = k, err
    return {"K": best_k, "closure_error_carriers": best_err, "synchronous": False,
            "closes": False}


def pwm_ripple(*, v_dc_V: float, modulation_index: float, f1_hz: float,
               f_sw_hz: float, modulation: str = "svpwm", n_sets: int = 1,
               set_shift_deg: float = 30.0, carrier_interleave_deg: float = 0.0,
               l_d_H: float, l_q_H: Optional[float] = None,
               l_xy_H: Optional[float] = None, neutral: str = "isolated",
               l_zero_H: Optional[float] = None,
               i1_rms_A: Optional[float] = None, current_lag_deg: float = 0.0,
               voltage_angle_from_d_deg: float = 90.0,
               sampling: str = "natural", samples_per_carrier: int = 256,
               max_periods: int = 20, top_n: int = 12,
               thd_limit_pct: Optional[float] = None) -> Dict[str, Any]:
    """Ripple of the phase currents of one (``n_sets`` = 1) or two three-phase
    inverters on one machine.

    ``modulation_index`` m = V_phase_peak / (V_dc/2).  ``current_lag_deg`` —
    the fundamental phase current's lag behind the fundamental phase voltage
    in the CONSUMER (motor) reference: phi for a motor, 180 - phi for a
    generator (used for the DC-link current only).  ``voltage_angle_from_d_deg``
    — where the fundamental voltage vector sits relative to the rotor d-axis
    (only matters when L_d != L_q).
    """
    mod = str(modulation or "").strip().lower()
    if mod not in PWM_RIPPLE_MODULATIONS:
        raise RippleRefusal("modulation must be " + " or ".join(PWM_RIPPLE_MODULATIONS),
                            ["pwm_modulation"])
    smp = str(sampling or "natural").strip().lower()
    if smp not in RIPPLE_SAMPLING:
        raise RippleRefusal("ripple_sampling must be " + " or ".join(RIPPLE_SAMPLING),
                            ["ripple_sampling"])
    for name, v in (("v_dc_V", v_dc_V), ("modulation_index", modulation_index),
                    ("f_elec_hz", f1_hz), ("f_carrier_hz", f_sw_hz),
                    ("ripple_l_d_uH", l_d_H)):
        if v is None or not (float(v) > 0.0) or not math.isfinite(float(v)):
            raise RippleRefusal(f"{name} must be a positive number for the ripple; "
                                f"got {v!r}", [name])
    l_q = float(l_d_H if l_q_H is None else l_q_H)
    if not (l_q > 0.0):
        raise RippleRefusal("ripple_l_q_uH must be positive", ["ripple_l_q_uH"])
    if int(n_sets) not in (1, 2):
        raise RippleRefusal("the ripple model covers one or two three-phase sets",
                            ["n_inverters"])
    nsets = int(n_sets)
    neu = str(neutral or "isolated").strip().lower()
    if neu not in NEUTRALS:
        raise RippleRefusal("ripple_neutral must be isolated or common", ["ripple_neutral"])
    if nsets == 2:
        if l_xy_H is None or not (float(l_xy_H) > 0.0):
            raise RippleRefusal(
                "dual three-phase: L_xy is required — the x-y plane links no "
                "air-gap flux, so only its own (leakage) inductance limits that "
                "current, and nothing in the field solve measures it",
                ["ripple_l_xy_pct", "ripple_l_xy_uH"])
        if neu == "common" and (l_zero_H is None or not (float(l_zero_H) > 0.0)):
            raise RippleRefusal(
                "dual three-phase with the two neutrals tied: L_0 (zero-sequence "
                "inductance) is required for the circulating current",
                ["ripple_l_zero_uH"])
    elif neu == "common":
        raise RippleRefusal("one three-phase set has one neutral — 'common' "
                            "applies to two sets only", ["ripple_neutral"])
    if not (0.0 <= float(carrier_interleave_deg) <= 180.0):
        raise RippleRefusal("carrier interleaving must be 0-180 deg of the carrier",
                            ["carrier_interleave_deg"])
    spc = int(samples_per_carrier)
    if spc < 32:
        raise RippleRefusal("samples_per_carrier must be at least 32 to resolve the "
                            "carrier", ["ripple_samples_per_carrier"])
    m = float(modulation_index)
    v_dc, f1, fsw = float(v_dc_V), float(f1_hz), float(f_sw_hz)
    warnings: List[str] = []
    m_lin = 1.0 if mod == "sine" else 2.0 / math.sqrt(3.0)
    if m > m_lin + 1e-9:
        warnings.append(f"m = {m:.3f} is past the {mod} linear limit {m_lin:.4f}: "
                        "the legs clip and low-order harmonics appear — they ARE "
                        "in the numbers below (the modulator is simulated as is)")

    ratio = fsw / f1
    win = _window(ratio, max_periods)
    K = win["K"]
    n = int(round(K * ratio * spc))
    while n * 3 * nsets > _MAX_SAMPLES and K > 1:
        K -= 1
        n = int(round(K * ratio * spc))
    if n * 3 * nsets > _MAX_SAMPLES:
        spc_eff = max(32, int(_MAX_SAMPLES / (3 * nsets * K * ratio)))
        n = int(round(K * ratio * spc_eff))
    if abs(ratio - round(ratio)) > 1e-6:
        warnings.append(
            f"asynchronous carrier: f_sw/f1 = {ratio:.3f} is not an integer — the "
            "carrier drifts against the fundamental and the current carries "
            f"sub-harmonics; simulated over {K} fundamental period(s)"
            + ("" if win["closes"] else
               f" (the carrier does not close; residual {win['closure_error_carriers']:.3f} carrier)"))

    T = K / f1
    t = np.arange(n) * (T / n)
    w_e = 2.0 * math.pi * f1

    # ── phases and pole voltages ──────────────────────────────────────────
    set_phases: List[List[float]] = []
    for s in range(nsets):
        sh = math.radians(set_shift_deg) * s
        set_phases.append([sh, sh + 2.0 * math.pi / 3.0, sh + 4.0 * math.pi / 3.0])
    all_ph = [p for ps in set_phases for p in ps]
    nph = len(all_ph)
    # EXACT switching instants per carrier ramp (no grid quantisation: a point-
    # sampled comparison puts spurious low-frequency volts into the result, and
    # a pure inductance turns a 0.1 V error at f1 into amperes).  Every grid
    # sample is then the exact fraction of its interval the leg spent high.
    dt = T / n
    t_edges = np.arange(n + 1) * dt
    s_all = np.empty((nph, n))
    Ts = 1.0 / fsw
    for s in range(nsets):
        off = (float(carrier_interleave_deg) / 360.0) * s
        j = np.arange(math.floor(-off) - 1, math.ceil(fsw * T - off) + 2)
        t_v = (j - off) * Ts                               # carrier valleys
        t_p = t_v + 0.5 * Ts                               # carrier peaks
        for q in range(3):
            def r_at(tt: np.ndarray) -> np.ndarray:
                # the leg's reference within its OWN set (zero sequence needs all three)
                rr = _refs(m, w_e * tt, set_phases[s], mod)[q]
                return np.clip(rr, -1.0, 1.0)

            if smp == "regular_symmetric":
                r_rise = r_at(t_v)
                r_fall = r_rise
                t_rise = t_v + (r_rise + 1.0) * 0.25 * Ts
                t_fall = t_p + (1.0 - r_fall) * 0.25 * Ts
            else:
                t_rise = t_v + (r_at(t_v) + 1.0) * 0.25 * Ts
                t_fall = t_p + (1.0 - r_at(t_p)) * 0.25 * Ts
                for _ in range(8):                         # contraction ~ m w/(4 f_sw)
                    t_rise = t_v + (r_at(t_rise) + 1.0) * 0.25 * Ts
                    t_fall = t_p + (1.0 - r_at(t_fall)) * 0.25 * Ts
            # high on [t_v, t_rise] and [t_fall, t_v + Ts]
            bp = np.empty(4 * t_v.size)
            bp[0::4], bp[1::4], bp[2::4], bp[3::4] = t_v, t_rise, t_fall, t_v + Ts
            seg = np.empty_like(bp)
            seg[0::4] = 0.0
            seg[1::4] = t_rise - t_v
            seg[2::4] = 0.0
            seg[3::4] = t_v + Ts - t_fall
            hi = np.cumsum(seg)
            H = np.interp(t_edges, bp, hi)
            s_all[3 * s + q] = np.diff(H) / dt
    v_pole = v_dc * (s_all - 0.5)
    t = t + 0.5 * dt                                       # sample = interval mean

    # ── orthonormal VSD basis ─────────────────────────────────────────────
    th = np.array(all_ph)
    e_a = np.cos(th) * math.sqrt(2.0 / nph)
    e_b = np.sin(th) * math.sqrt(2.0 / nph)
    zs = []
    for s in range(nsets):
        z = np.zeros(nph); z[3 * s:3 * s + 3] = 1.0 / math.sqrt(3.0)
        zs.append(z)
    basis = [e_a, e_b] + zs
    Q, _ = np.linalg.qr(np.array(basis).T)            # span of ab + zero
    e_xy: List[np.ndarray] = []
    if nsets == 2:
        P = np.eye(nph) - Q @ Q.T                      # the complement
        u, sv, _ = np.linalg.svd(P)
        e_xy = [u[:, 0], u[:, 1]]

    v_a = e_a @ v_pole
    v_b = e_b @ v_pole

    # ── alpha-beta in the rotor frame, exact per bin ──────────────────────
    # Complex rotor-frame vector v = v_d + j v_q.  With psi = L_s i + L_D i*
    # (L_s = (L_d+L_q)/2, L_D = (L_d-L_q)/2) the bins w and -w couple:
    #   V(w)      =  j(w+w_e) [L_s I(w)  + L_D I(-w)*]
    #   V(-w)*    = -j(w_e-w) [L_s I(-w)* + L_D I(w)]
    # singular only at w = -w_e (stationary DC: resistance-limited, set to 0).
    ld = float(l_d_H)
    th_d = w_e * t - math.radians(voltage_angle_from_d_deg)
    rot = np.exp(-1j * th_d)
    v_dq = (v_a + 1j * v_b) * rot
    V = np.fft.fft(v_dq)
    k = np.fft.fftfreq(n, d=T / n) * 2.0 * math.pi       # rad/s per bin
    idx_neg = (-np.arange(n)) % n
    V2 = np.conj(V[idx_neg])
    l_s, l_D = 0.5 * (ld + l_q), 0.5 * (ld - l_q)
    a11 = 1j * (k + w_e) * l_s
    a12 = 1j * (k + w_e) * l_D
    a21 = -1j * (w_e - k) * l_D
    a22 = -1j * (w_e - k) * l_s
    det = a11 * a22 - a12 * a21
    tol = 1e-9 * w_e * w_e * ld * l_q
    X = np.zeros(n, dtype=complex)
    gen = np.abs(det) > tol
    X[gen] = (a22[gen] * V[gen] - a12[gen] * V2[gen]) / det[gen]
    plus = (~gen) & (k > 0)                              # w = +w_e: y (DC) = 0
    X[plus] = V[plus] / a11[plus]
    X[0] = 0.0                                           # the fundamental itself
    if n % 2 == 0:
        X[n // 2] = 0.0                                  # Nyquist has no partner
    minus = (~gen) & (k < 0)
    v_stat_dc = float(np.sum(np.abs(V[minus]))) / n
    if v_stat_dc > 1e-3 * v_dc:
        warnings.append(f"the modulator makes {v_stat_dc:.2f} V of stationary DC in "
                        "alpha-beta; only the stator resistance limits that current "
                        "and it is left out of the ripple")
    i_s = np.fft.ifft(X) * np.conj(rot)
    i_al, i_be = np.real(i_s), np.imag(i_s)
    i_ab_ph = np.outer(e_a, i_al) + np.outer(e_b, i_be)   # per phase

    def _through(vcomp: np.ndarray, L: float) -> np.ndarray:
        V = np.fft.fft(vcomp)
        I = np.where(k == 0, 0.0, V / np.where(k == 0, 1.0, 1j * k * L))
        return np.real(np.fft.ifft(I))

    i_xy_ph = np.zeros_like(i_ab_ph)
    if nsets == 2:
        for e in e_xy:
            i_xy_ph += np.outer(e, _through(e @ v_pole, float(l_xy_H)))
    i_z_ph = np.zeros_like(i_ab_ph)
    if nsets == 2 and neu == "common":
        ez = (zs[0] - zs[1]) / math.sqrt(2.0)
        i_z_ph = np.outer(ez, _through(ez @ v_pole, float(l_zero_H)))
    i_rip = i_ab_ph + i_xy_ph + i_z_ph

    def _rms(a: np.ndarray) -> float:
        return float(np.sqrt(np.mean(a ** 2)))

    rms_tot = _rms(i_rip)
    rms_ab = _rms(i_ab_ph)
    rms_xy = _rms(i_xy_ph)
    rms_z = _rms(i_z_ph)
    pp = float(np.max(np.max(i_rip, axis=1) - np.min(i_rip, axis=1)))

    # ── spectrum of phase a (set 1) ───────────────────────────────────────
    Ia = np.fft.rfft(i_rip[0]) / n
    amp = np.abs(Ia) * math.sqrt(2.0)
    amp[0] = 0.0
    freqs = np.fft.rfftfreq(n, d=T / n)
    order = np.argsort(amp)[::-1][:max(int(top_n), 1)]
    spectrum = []
    for j in sorted(order, key=lambda q: freqs[q]):
        if amp[j] <= 0:
            continue
        spectrum.append({"f_Hz": round(float(freqs[j]), 2),
                         "order": round(float(freqs[j] / f1), 3),
                         "i_rms_A": round(float(amp[j]), 3),
                         "pct_of_i1": (round(100.0 * float(amp[j]) / i1_rms_A, 3)
                                       if i1_rms_A else None)})

    out: Dict[str, Any] = {
        "ripple_rms_A": round(rms_tot, 3),
        "ripple_rms_ab_A": round(rms_ab, 3),
        "ripple_rms_xy_A": round(rms_xy, 3) if nsets == 2 else None,
        "ripple_rms_zero_A": round(rms_z, 3) if (nsets == 2 and neu == "common") else None,
        "ripple_pp_A": round(pp, 2),
        "spectrum": spectrum,
        "window_periods": K,
        "samples": int(n),
        "samples_per_carrier": int(round(n / (K * ratio))),
        "carrier_ratio": round(ratio, 4),
        "synchronous": abs(ratio - round(ratio)) <= 1e-6,
        "modulation": mod, "sampling": smp, "n_sets": nsets,
        "set_shift_deg": float(set_shift_deg) if nsets == 2 else None,
        "carrier_interleave_deg": float(carrier_interleave_deg) if nsets == 2 else None,
        "neutral": neu,
        "l_d_uH": round(ld * 1e6, 4), "l_q_uH": round(l_q * 1e6, 4),
        "l_xy_uH": round(float(l_xy_H) * 1e6, 4) if (nsets == 2 and l_xy_H) else None,
        "l_zero_uH": (round(float(l_zero_H) * 1e6, 4)
                      if (nsets == 2 and neu == "common") else None),
        "voltage_angle_from_d_deg": float(voltage_angle_from_d_deg),
        "warnings": warnings,
        "model": ("ideal switches, no dead time; resistance neglected at ripple "
                  "frequencies; ripple = the phase current minus its positive-"
                  "sequence fundamental"),
    }
    if i1_rms_A and i1_rms_A > 0:
        thd = 100.0 * rms_tot / float(i1_rms_A)
        out["i1_rms_A"] = round(float(i1_rms_A), 3)
        out["thd_pct"] = round(thd, 3)
        out["thd_ab_pct"] = round(100.0 * rms_ab / float(i1_rms_A), 3)
        if thd_limit_pct is not None:
            lim = float(thd_limit_pct)
            out["thd_limit_pct"] = lim
            out["thd_verdict"] = "pass" if thd <= lim else "fail"
        # ── DC link: the switching functions times the WHOLE leg currents ─
        ip = float(i1_rms_A) * math.sqrt(2.0)
        lag = math.radians(float(current_lag_deg))
        i_fund = np.array([ip * np.cos(w_e * t - p - lag) for p in all_ph])
        i_leg = i_fund + i_rip
        i_dc_sets = [np.sum(s_all[3 * s:3 * s + 3] * i_leg[3 * s:3 * s + 3], axis=0)
                     for s in range(nsets)]
        i_dc = np.sum(i_dc_sets, axis=0)
        mean = float(np.mean(i_dc))
        out["dc_link"] = {
            "i_dc_mean_A": round(mean, 2),
            "i_cap_rms_A": round(float(np.sqrt(max(np.mean(i_dc ** 2) - mean ** 2, 0.0))), 2),
            "i_cap_rms_per_inverter_A": [
                round(float(np.std(x)), 2) for x in i_dc_sets],
            "note": ("one shared DC link; i_dc > 0 = power drawn from the link "
                     "(motor), < 0 = power delivered into it (generator)"),
        }
    return out
