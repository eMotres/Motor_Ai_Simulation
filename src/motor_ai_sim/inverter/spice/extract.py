"""Switching energies and times from a double-pulse transient — on the
DATASHEET'S OWN integration windows.

Infineon CoolSiC 1200 V G2 datasheet, section 6 "Testing conditions"
(IMCQ120R004M2H rev 1.10, p. 16; the same figures in every IMCQ120R0xxM2H
datasheet):

* **Fig. C — Definition of switching losses.**
  ``E_off = ∫ V_DS·I_D dt`` from t1 (V_DS has risen to 10 % V_DS) to t2
  (I_D has fallen to 10 % I_D);
  ``E_on = ∫ V_DS·I_D dt`` from t3 (I_D has risen to 10 % I_D) to t4
  (V_DS has fallen to 10 % V_DS).
* **Fig. A — Definition of switching times** (on V_DS, 10 %/90 %, with the
  gate at 10 %/90 % of its swing): t_d(on) = V_GS 10 % → V_DS 90 %,
  t_r = V_DS 90 % → 10 %; t_d(off) = V_GS 90 % → V_DS 10 %,
  t_f = V_DS 10 % → 90 %.
* **Fig. B — Definition of body diode switching characteristics.** The
  freewheeling device's current crosses zero, overshoots to −I_frm and
  returns; t_fr = t_a + t_b ends where the reverse current has decayed to
  10 % of I_frm; Q_fr = Q_a + Q_b is the reverse charge over t_fr ("Q_fr
  includes also Q_C", Table 6).  E_fr is taken as ∫ V_DS·I_D of the
  freewheeling device over the SAME t_fr window (the datasheet draws no
  separate E_fr window; stated).

V_DS is the DUT's drain-to-SOURCE-pin voltage (the power terminals; the stray
L_σ is outside it), I_D its drain current.  "10 % V_DS" means 10 % of the bus
V_DD; "10 % I_D" 10 % of the current being switched on that edge.

The functions here are pure (arrays in, numbers out) so the parser and the
windows are unit-tested without ngspice.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

import numpy as np

__all__ = ["crossing", "integrate", "edge_off", "edge_on", "recovery",
           "double_pulse_metrics"]


def crossing(t: np.ndarray, y: np.ndarray, level: float, *, t_from: float,
             t_to: Optional[float] = None, rising: bool = True) -> Optional[float]:
    """First time in ``[t_from, t_to]`` where ``y`` crosses ``level`` in the
    given direction, linearly interpolated between samples."""
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    i0 = int(np.searchsorted(t, t_from))
    i1 = len(t) if t_to is None else int(np.searchsorted(t, t_to, side="right"))
    if i1 - i0 < 2:
        return None
    ys = y[i0:i1] - level
    ts = t[i0:i1]
    if rising:
        idx = np.nonzero((ys[:-1] < 0) & (ys[1:] >= 0))[0]
    else:
        idx = np.nonzero((ys[:-1] > 0) & (ys[1:] <= 0))[0]
    if idx.size == 0:
        return None
    k = int(idx[0])
    y0, y1 = ys[k], ys[k + 1]
    if y1 == y0:
        return float(ts[k])
    return float(ts[k] + (ts[k + 1] - ts[k]) * (-y0) / (y1 - y0))


def integrate(t: np.ndarray, y: np.ndarray, a: float, b: float) -> float:
    """∫_a^b y dt, trapezoidal on the solver's own time points, with the two
    end points interpolated in."""
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    if b <= a:
        return 0.0
    m = (t > a) & (t < b)
    tt = np.concatenate([[a], t[m], [b]])
    yy = np.concatenate([[np.interp(a, t, y)], y[m], [np.interp(b, t, y)]])
    return float(np.sum(0.5 * (yy[1:] + yy[:-1]) * np.diff(tt)))


def _slope(t, y, lo, hi, t_from, t_to, rising):
    a = crossing(t, y, lo if rising else hi, t_from=t_from, t_to=t_to, rising=rising)
    b = crossing(t, y, hi if rising else lo, t_from=a if a is not None else t_from,
                 t_to=t_to, rising=rising)
    if a is None or b is None or b <= a:
        return None, a, b
    return (hi - lo) / (b - a) * (1 if rising else -1), a, b


def edge_off(t, vds, idr, vgs, *, v_dd: float, t_cmd: float,
             window: float = 1.0e-6, v_gs_on: float, v_gs_off: float
             ) -> Dict[str, Optional[float]]:
    """Turn-off edge at commanded time ``t_cmd`` (Fig. C, E_off; Fig. A).

    The switched current ``I_D`` is the drain current's maximum between the
    command and t1 (V_DS at 10 %): the load keeps ramping through the
    turn-off delay, so the current at the COMMAND is too low, and once V_DS
    moves the other device's C_oss already carries part of the load, so the
    drain current AT t1 is too low as well."""
    t_end = t_cmd + window
    t1 = crossing(t, vds, 0.1 * v_dd, t_from=t_cmd, t_to=t_end, rising=True)
    mk = (t >= t_cmd) & (t <= (t1 if t1 is not None else t_cmd + 50e-9))
    i_sw = float(np.max(idr[mk])) if mk.any() else float(np.interp(t_cmd, t, idr))
    t2 = crossing(t, idr, 0.1 * i_sw, t_from=t1 if t1 else t_cmd, t_to=t_end,
                  rising=False)
    e = integrate(t, vds * idr, t1, t2) if (t1 is not None and t2 is not None) else None
    g90 = v_gs_off + 0.9 * (v_gs_on - v_gs_off)
    tg = crossing(t, vgs, g90, t_from=t_cmd - 5e-9, t_to=t_end, rising=False)
    dv, a10, b90 = _slope(t, vds, 0.1 * v_dd, 0.9 * v_dd, t_cmd, t_end, True)
    di, _, _ = _slope(t, idr, 0.1 * i_sw, 0.9 * i_sw, t_cmd, t_end, False)
    m = (t >= t_cmd) & (t <= t_end)
    return {
        "i_A": i_sw, "e_J": e, "t1_s": t1, "t2_s": t2,
        "t_d_off_s": (a10 - tg) if (a10 is not None and tg is not None) else None,
        "t_f_s": (b90 - a10) if (a10 is not None and b90 is not None) else None,
        "dv_dt_V_per_s": dv, "di_dt_A_per_s": di,
        "v_peak_V": float(np.max(vds[m])) if m.any() else None,
    }


def edge_on(t, vds, idr, vgs, *, v_dd: float, t_cmd: float, i_load: float,
            window: float = 1.0e-6, v_gs_on: float, v_gs_off: float
            ) -> Dict[str, Optional[float]]:
    """Turn-on edge at ``t_cmd`` (Fig. C, E_on; Fig. A).  ``i_load`` is the
    load current being commutated (the freewheeling current just before)."""
    t_end = t_cmd + window
    t3 = crossing(t, idr, 0.1 * i_load, t_from=t_cmd, t_to=t_end, rising=True)
    t4 = crossing(t, vds, 0.1 * v_dd, t_from=t3 if t3 else t_cmd, t_to=t_end,
                  rising=False)
    e = integrate(t, vds * idr, t3, t4) if (t3 is not None and t4 is not None) else None
    g10 = v_gs_off + 0.1 * (v_gs_on - v_gs_off)
    tg = crossing(t, vgs, g10, t_from=t_cmd - 5e-9, t_to=t_end, rising=True)
    dv, a90, b10 = _slope(t, vds, 0.1 * v_dd, 0.9 * v_dd, t_cmd, t_end, False)
    di, _, _ = _slope(t, idr, 0.1 * i_load, 0.9 * i_load, t_cmd, t_end, True)
    m = (t >= t_cmd) & (t <= t_end)
    return {
        "i_A": i_load, "e_J": e, "t3_s": t3, "t4_s": t4,
        "t_d_on_s": (a90 - tg) if (a90 is not None and tg is not None) else None,
        "t_r_s": (b10 - a90) if (a90 is not None and b10 is not None) else None,
        "dv_dt_V_per_s": dv, "di_dt_A_per_s": di,
        "i_peak_A": float(np.max(idr[m])) if m.any() else None,
    }


def recovery(t, v_fw, i_fw, *, t_cmd: float, window: float = 1.0e-6
             ) -> Dict[str, Optional[float]]:
    """Body-diode recovery of the freewheeling device (Fig. B).

    ``i_fw`` is the freewheeling device's DRAIN current (negative while its
    body diode conducts the load, positive during the reverse-recovery
    overshoot); ``v_fw`` its V_DS.  Returns Q_fr, I_frm, t_fr, E_fr.
    """
    t_end = t_cmd + window
    t0 = crossing(t, i_fw, 0.0, t_from=t_cmd, t_to=t_end, rising=True)
    if t0 is None:
        return {"q_fr_C": None, "i_frm_A": None, "t_fr_s": None, "e_fr_J": None}
    m = (t >= t0) & (t <= t_end)
    if not m.any():
        return {"q_fr_C": None, "i_frm_A": None, "t_fr_s": None, "e_fr_J": None}
    k = int(np.argmax(np.where(m, i_fw, -np.inf)))
    i_frm = float(i_fw[k])
    t_pk = float(t[k])
    t_b = crossing(t, i_fw, 0.1 * i_frm, t_from=t_pk, t_to=t_end, rising=False)
    if t_b is None:
        t_b = t_end
    q = integrate(t, np.maximum(i_fw, 0.0), t0, t_b)
    e = integrate(t, v_fw * i_fw, t0, t_b)
    return {"q_fr_C": q, "i_frm_A": i_frm, "t_fr_s": t_b - t0, "e_fr_J": e,
            "t0_s": t0, "t_end_s": t_b}


def double_pulse_metrics(r: Dict[str, np.ndarray], *, v_dd: float,
                         timeline: Dict[str, float], v_gs_on: float,
                         v_gs_off: float) -> Dict[str, object]:
    """Everything the table and the validation need, from one run."""
    t = r["scale"]
    vds = r["v(dl)"] - r["v(sl)"]
    idr = r["i(vidl)"]
    vgs = r["v(gl)"] - r.get("v(kl)", r["v(sl)"])   # at the Kelvin pin, if any
    off = edge_off(t, vds, idr, vgs, v_dd=v_dd, t_cmd=timeline["t_off1"],
                   window=min(1.5e-6, 0.8 * (timeline["t_on2"] - timeline["t_off1"])),
                   v_gs_on=v_gs_on, v_gs_off=v_gs_off)
    # The LOAD current — what the first turn-off switched and the second
    # turn-on commutates — is the high side's freewheeling current in the
    # gap, time-averaged over its last 150 ns (the freewheel path rings with
    # C_sigma and C_oss after the turn-off; the inductor current itself does
    # not, and it barely decays through a body diode in 0.5 us).  The drain
    # current's peak before t1 (``off["i_A"]``, kept as ``i_off_peak_A``)
    # is lower on a big die (the load still ramps through t_d(off) + the
    # voltage rise: 186 vs 191 A on IMCQ120R004M2H) and higher on a small
    # one (Miller and C_oss displacement currents ride on it).
    t_a = max(timeline["t_on2"] - 150e-9, timeline["t_off1"] + 0.5 * (
        timeline["t_on2"] - timeline["t_off1"]))
    t_b = timeline["t_on2"] - 5e-9
    ih = np.asarray(r["i(vidh)"], float)
    i_fw_before = -integrate(t, ih, t_a, t_b) / (t_b - t_a)
    i_dut_gap = integrate(t, np.asarray(idr, float), t_a, t_b) / (t_b - t_a)
    load_src = "gap"
    if (off.get("t2_s") is None or off["t2_s"] > t_a
            or abs(i_dut_gap) > 0.05 * max(abs(i_fw_before), 1e-9)):
        # the turn-off had not finished when the averaging window opened
        # (a long gap-less pulse train for a slow driver): the freewheel
        # current is not the load current yet — fall back to the drain
        # current's peak before t1, and say so
        i_fw_before = float(off["i_A"])
        load_src = "peak_fallback"
    on = edge_on(t, vds, idr, vgs, v_dd=v_dd, t_cmd=timeline["t_on2"],
                 i_load=i_fw_before,
                 window=min(0.9e-6, 0.9 * (timeline["t_off2"] - timeline["t_on2"])),
                 v_gs_on=v_gs_on, v_gs_off=v_gs_off)
    v_fw = r["v(dh)"] - r["v(mid)"]
    rec = recovery(t, v_fw, r["i(vidh)"], t_cmd=timeline["t_on2"],
                   window=min(0.9e-6, 0.9 * (timeline["t_off2"] - timeline["t_on2"])))
    return {"off": off, "on": on, "fr": rec,
            "i_off_A": i_fw_before, "i_on_A": i_fw_before,
            "i_off_peak_A": off["i_A"], "i_load_source": load_src}
