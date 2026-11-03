"""IGBT modules — Si IGBT + anti-parallel diode (FWD) — in the Controller.

WHY A SECOND LOSS MODEL
=======================
The MOSFET model (:mod:`motor_ai_sim.inverter.losses`) is synchronous-
rectifying: the reverse current is in the CHANNEL, so a leg's conduction is
``<i^2>*R`` whatever the duty.  An IGBT cannot conduct backwards: in every
carrier period the leg current is either in an IGBT (forward) or in the
OTHER position's diode, and the duty decides which.  So the conduction is
split by the direction of the current, per sample of the fundamental period:

    i > 0 (leaving the leg):  upper IGBT for d,      lower FWD for 1 - d
    i < 0 (entering the leg): lower IGBT for 1 - d,  upper FWD for d

with ``d`` the high-side duty of the leg's own modulator
(:func:`waveforms.leg_duty`: sine, SVPWM or third-harmonic zero sequence) at
the leg's own voltage phase.  The dead time moves ``t_d*f_sw`` of every
carrier period from the IGBT that is about to turn on to the diode that is
still carrying the current (ONE window per period — at the other edge the
diode would conduct anyway).  A generator (current lagging the voltage by
180 - phi) therefore puts most of the conduction in the diodes, which is the
point of the split.

    P_cond,IGBT = N * < w_T * i_dev * V_CE(i_dev, T_j,IGBT) >
    P_cond,FWD  = N * < w_D * i_dev * V_F (i_dev, T_j,FWD)  >
    P_lead      = <i^2> * R_lead / N          (module terminals, see card)
    P_sw        = f_sw * N * < E_on + E_off >(i_dev, T_j,IGBT)
    P_rr        = f_sw * N * < E_rr >(i_dev, T_j,FWD)

V_CE(I, T_j) and V_F(I, T_j) are the card's digitised curves, linear in I on
each temperature's curve, then linear in T_j between the 25/125/150/175 degC
curves (clamped to the tabulated span).  E_on/E_off/E_rr likewise, scaled to
the working bus by ``(V_dc/V_ref)^k_v`` (k_v = 1.35 for E_on/E_off, 0.6 for
E_rr — card fields, stated) and to the working gate resistance by the ratio
of the card's E vs R_G curves.

THE TWO JUNCTION TEMPERATURES.  Per switch position (one IGBT + its FWD on
one baseplate area):

    T_case,mod = T_sink + (P_T + P_D + P_lead) * (R_th(c-s) + R_spread + R_film)
    T_j,IGBT   = T_case,mod + P_T * R_th(j-c),IGBT
    T_j,FWD    = T_case,mod + P_D * R_th(j-c),FWD

``T_sink`` is the cooling node the MOSFET model calls ``t_case`` (the
coldplate/heatsink surface), solved by the SAME cooling objects.  R_th(c-s)
is the card's (datasheet, per 1 IGBT + 1 FWD, with grease) unless the request
states ``r_tim_k_w``.  Both temperatures are iterated together.

The linear form ``V = V_0 + r*i`` of both devices (least squares over
0.1..1 x the peak device current) is reported, and it is what the coupled
PWM drive puts in the circuit (:func:`motor_ai_sim.inverter.coupling.fit_device_drop`).
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from motor_ai_sim.inverter import waveforms as wf
from motor_ai_sim.inverter.devices import (CardError, DeviceCard, _num,
                                           interp)

log = logging.getLogger(__name__)

__all__ = ["IGBT_TECHNOLOGIES", "IgbtModuleCard", "validate_igbt_card",
           "is_igbt_doc", "igbt_leg_losses", "solve_igbt_controller",
           "closed_form_spwm_conduction", "DEFAULT_DEAD_TIME_US"]

#: The technologies this module answers for.
IGBT_TECHNOLOGIES = ("igbt_module",)

#: Dead time used when the request states none [us].  A STATED default for a
#: 1200 V / several-hundred-amp IGBT module (gate drivers of this class run
#: 1.5-3 us); the MOSFET default (0.5 us) would be too short to be real here.
DEFAULT_DEAD_TIME_US = 2.0

#: Bus utilisation above this fraction of V_CES is a design WARNING (the same
#: convention the MOSFET model applies to V_DSS).
V_CES_WARN_FRACTION = 0.80

_TOL_K = 0.1
_MAX_ITER = 80


def is_igbt_doc(doc: Any) -> bool:
    return (isinstance(doc, dict)
            and str(doc.get("technology") or "").strip().lower() in IGBT_TECHNOLOGIES)


# ---------------------------------------------------------------------------
# The card
# ---------------------------------------------------------------------------

def _xy_pairs(raw: Any) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            x, y = _num(item[0]), _num(item[1])
            if x is not None and y is not None:
                out.append((x, y))
    return sorted(out)


def _curves(block: Any) -> List[Tuple[float, List[Tuple[float, float]]]]:
    """``[{t_j_c, points: [[x, y], ...]}]`` -> sorted ``[(t, [(x, y)])]``."""
    out = []
    if not isinstance(block, dict):
        return out
    for c in block.get("curves") or []:
        if not isinstance(c, dict):
            continue
        t = _num(c.get("t_j_c"))
        pts = _xy_pairs(c.get("points"))
        if t is not None and len(pts) >= 2:
            out.append((t, pts))
    return sorted(out, key=lambda tc: tc[0])


def validate_igbt_card(doc: Any) -> List[str]:
    """Everything wrong with an ``igbt_module`` card (empty = fine)."""
    from motor_ai_sim.inverter.devices import _PART_RE, validate_footprint
    bad: List[str] = []
    if not isinstance(doc, dict):
        return ["the card must be a YAML mapping"]
    for key in ("part", "package", "ratings", "igbt", "diode", "switching",
                "thermal"):
        if doc.get(key) in (None, "", {}):
            bad.append(f"{key} is missing — it is required for an igbt_module card")
    part = str(doc.get("part") or "")
    if part and not _PART_RE.match(part):
        bad.append(f"part {part!r} must be letters, digits, dot, dash, plus or "
                   "underscore (it is the file name)")
    if str(doc.get("topology") or "half_bridge") != "half_bridge":
        bad.append("topology: only half_bridge (2-in-1) IGBT modules are modelled")
    rat = doc.get("ratings") or {}
    if isinstance(rat, dict):
        if not _num(rat.get("v_ces_V")):
            bad.append("ratings.v_ces_V is required (blocking voltage)")
        if not (_num(rat.get("t_vj_op_max_c")) or _num(rat.get("t_vj_max_c"))):
            bad.append("ratings.t_vj_op_max_c (or t_vj_max_c) is required")
        pts = [(_num((p or {}).get("t_case_c")), _num((p or {}).get("i_a")))
               for p in (rat.get("i_c_continuous") or []) if isinstance(p, dict)]
        if not any(a is not None and b is not None for a, b in pts):
            bad.append("ratings.i_c_continuous needs at least one {t_case_c, i_a} point")
    if len(_curves((doc.get("igbt") or {}).get("v_ce_sat"))) < 1:
        bad.append("igbt.v_ce_sat.curves needs at least one {t_j_c, points} curve")
    if len(_curves((doc.get("diode") or {}).get("v_f"))) < 1:
        bad.append("diode.v_f.curves needs at least one {t_j_c, points} curve")
    sw = doc.get("switching") or {}
    if isinstance(sw, dict):
        if not _num(sw.get("v_cc_ref_V")):
            bad.append("switching.v_cc_ref_V is required (the bus the energies were measured at)")
        for k in ("e_on", "e_off", "e_rr"):
            if not _curves(sw.get(k)):
                bad.append(f"switching.{k}.curves needs at least one {{t_j_c, points}} curve")
    th = doc.get("thermal") or {}
    if isinstance(th, dict):
        for k in ("r_th_jc_igbt_k_w", "r_th_jc_diode_k_w"):
            r = th.get(k)
            if not (isinstance(r, dict) and (_num(r.get("max")) or _num(r.get("typ")))):
                bad.append(f"thermal.{k} needs typ or max [K/W]")
    if "footprint" in doc:
        bad.extend(validate_footprint(doc))
    return bad


class IgbtModuleCard(DeviceCard):
    """A half-bridge IGBT module card: the accessors the IGBT model needs,
    plus the generic ones (ratings, row, provenance) the catalogue reads.

    Created by :class:`DeviceCard` itself when the card's technology is
    ``igbt_module`` — every caller that builds a ``DeviceCard`` gets this.
    """

    def __post_init__(self) -> None:
        bad = validate_igbt_card(self.doc)
        if bad:
            raise CardError(f"device card {self.doc.get('part') or self.source_file}: "
                            + "; ".join(bad))
        self._cache: Dict[str, Any] = {}

    # ── identity / ratings ──────────────────────────────────────────────────
    @property
    def is_igbt(self) -> bool:
        return True

    @property
    def v_dss_V(self) -> float:            # the generic name: blocking voltage
        return float(self.doc["ratings"]["v_ces_V"])

    @property
    def v_ces_V(self) -> float:
        return self.v_dss_V

    @property
    def t_j_max_c(self) -> float:
        rat = self.doc["ratings"]
        v = _num(rat.get("t_vj_op_max_c"))
        return float(v if v is not None else rat.get("t_vj_max_c"))

    def _rth(self, key: str) -> Optional[float]:
        r = (self.doc.get("thermal") or {}).get(key) or {}
        v = _num(r.get("max"))
        return v if v is not None else _num(r.get("typ"))

    @property
    def r_th_jc_k_w(self) -> Optional[float]:     # generic = the IGBT's
        return self._rth("r_th_jc_igbt_k_w")

    @property
    def r_th_jc_igbt_k_w(self) -> float:
        return float(self._rth("r_th_jc_igbt_k_w"))

    @property
    def r_th_jc_diode_k_w(self) -> float:
        return float(self._rth("r_th_jc_diode_k_w"))

    @property
    def r_th_cs_k_w(self) -> Optional[float]:
        return self._rth("r_th_cs_k_w")

    @property
    def r_th_jc_unpublished(self) -> Optional[str]:
        return None

    @property
    def r_th_jc_basis(self) -> str:
        r = (self.doc.get("thermal") or {}).get("r_th_jc_igbt_k_w") or {}
        return "max" if _num(r.get("max")) is not None else "typ"

    @property
    def r_th_ja_k_w(self) -> Optional[float]:
        return None

    @property
    def r_lead_ohm(self) -> float:
        v = _num((self.doc.get("terminals") or {}).get("r_lead_mohm"))
        return 0.0 if v is None else float(v) * 1e-3

    @property
    def i_d_pulsed_A(self) -> Optional[float]:
        return _num(self.doc["ratings"].get("i_crm_A"))

    @property
    def i_sm_A(self) -> Optional[float]:
        return _num(self.doc["ratings"].get("i_frm_A"))

    @property
    def i_f_A(self) -> Optional[float]:
        return _num(self.doc["ratings"].get("i_f_A"))

    @property
    def t_case_max_c(self) -> Optional[float]:
        return _num(self.doc["ratings"].get("t_case_max_c"))

    def v_gs_static_window(self) -> Tuple[Optional[float], Optional[float]]:
        g = (self.doc.get("gate") or {}).get("v_ge_static_V")
        if isinstance(g, dict):
            lo, hi = _num(g.get("min")), _num(g.get("max"))
            if lo is not None and hi is not None:
                return lo, hi
        v = _num(self.doc["ratings"].get("v_ges_V"))
        return (-v, v) if v is not None else (None, None)

    def gate_reference(self) -> Tuple[float, float]:
        sw = self.doc.get("switching") or {}
        return (float(_num(sw.get("v_ge_on_V")) or 15.0),
                float(_num(sw.get("v_ge_off_V")) if _num(sw.get("v_ge_off_V")) is not None
                      else -15.0))

    @property
    def r_g_ref_ohm(self) -> Optional[float]:
        return _num((self.doc.get("switching") or {}).get("r_g_ref_ohm"))

    def t_j_curve_max_c(self) -> float:
        hottest = self.t_j_max_c
        for blk in (self._vce(), self._vf()):
            if blk:
                hottest = max(hottest, blk[-1][0])
        return hottest

    def t_j_curve_min_c(self) -> float:
        return min(c[0] for c in self._vce())

    def i_d_continuous(self, t_case_c: float) -> Optional[float]:
        pts = sorted((float(p["t_case_c"]), float(p["i_a"]))
                     for p in self.doc["ratings"].get("i_c_continuous") or []
                     if isinstance(p, dict) and _num(p.get("t_case_c")) is not None
                     and _num(p.get("i_a")) is not None)
        return interp(pts, float(t_case_c)) if pts else None

    def i_d_rating(self, t_case_c: float) -> Dict[str, Any]:
        """``{i_a, basis, source}``: I_C at this case temperature.

        At or below the published case temperature the nominal I_C (800 A at
        T_c = 100 degC on this card) answers.  Above it, the datasheet's own
        limiting mechanism: the DC current whose CHIP loss I*V_CE(I, T_vj,max)
        just reaches (T_vj,max - T_c)/R_th(j-c) — capped at the nominal value.
        """
        pts = sorted((float(p["t_case_c"]), float(p["i_a"]))
                     for p in self.doc["ratings"].get("i_c_continuous") or []
                     if isinstance(p, dict))
        src = str(self.doc["ratings"].get("source") or "").strip()
        t = float(t_case_c)
        if not pts:
            return {"i_a": None, "basis": "the card publishes no I_C rating", "source": None}
        t_pub, i_nom = pts[-1]
        if t <= t_pub:
            return {"i_a": interp(pts, t) if len(pts) > 1 and t >= pts[0][0] else i_nom,
                    "basis": f"the card's nominal I_C (T_c <= {t_pub:.0f} degC)",
                    "source": src}
        head = self.t_j_max_c - t
        r_th = self.r_th_jc_igbt_k_w
        if head <= 0 or r_th <= 0:
            return {"i_a": 0.0, "basis": f"the case is at or above T_vj,max "
                                         f"({self.t_j_max_c:.0f} degC)", "source": src}
        p_max = head / r_th
        lo, hi = 0.0, 4.0 * i_nom
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            if mid * self.v_ce_V(mid, self.t_j_max_c) > p_max:
                hi = mid
            else:
                lo = mid
        i = min(lo, i_nom)
        return {"i_a": i,
                "basis": (f"above the published {t_pub:.0f} degC: I*V_CE(I, "
                          f"{self.t_j_max_c:.0f} degC) = (T_vj,max - T_c)/R_th(j-c) "
                          f"at T_c = {t:.0f} degC, capped at the nominal {i_nom:.0f} A"),
                "source": src}

    # ── on-state characteristics ────────────────────────────────────────────
    def _vce(self):
        if "vce" not in self._cache:
            self._cache["vce"] = _curves((self.doc.get("igbt") or {}).get("v_ce_sat"))
        return self._cache["vce"]

    def _vf(self):
        if "vf" not in self._cache:
            self._cache["vf"] = _curves((self.doc.get("diode") or {}).get("v_f"))
        return self._cache["vf"]

    @staticmethod
    def _iv_at(curves, x: float, t: float) -> float:
        """Linear in x on every temperature's curve (extrapolated in x), then
        linear in T between the curves, CLAMPED to the tabulated T span."""
        by_t = [(tc, interp(pts, float(x))) for tc, pts in curves]
        if len(by_t) == 1:
            return max(float(by_t[0][1] or 0.0), 0.0)
        tt = min(max(float(t), by_t[0][0]), by_t[-1][0])
        return max(float(interp(by_t, tt) or 0.0), 0.0)

    def v_ce_V(self, i_c_A: float, t_j_c: float) -> float:
        """IGBT chip V_CE(sat) at V_GE = 15 V [V] (one module)."""
        return self._iv_at(self._vce(), max(float(i_c_A), 0.0), t_j_c)

    def v_f_V(self, i_f_A: float, t_j_c: float) -> float:
        """FWD chip V_F [V] (one module)."""
        return self._iv_at(self._vf(), max(float(i_f_A), 0.0), t_j_c)

    def v_sd_V(self, i_sd_A: float, t_j_c: float, v_gs_off_V: float = 0.0) -> float:
        """The REVERSE device of a switch position — here the FWD."""
        return self.v_f_V(i_sd_A, t_j_c)

    def r_ds_on_ohm(self, t_j_c: float, v_gs_on_V: float) -> float:
        raise CardError(f"{self.part} is an IGBT module: it has no R_DS(on) — "
                        "use v_ce_V / linear_fit('igbt', ...)")

    def linear_fit(self, which: str, t_j_c: float, i_hi_A: float,
                   i_lo_A: Optional[float] = None) -> Dict[str, float]:
        """``{v0_V, r_ohm, max_err_V, i_lo_A, i_hi_A}``: least-squares
        ``V = V0 + r*I`` of the IGBT (``which="igbt"``) or the FWD over
        ``i_lo .. i_hi`` (default 0.1 x i_hi), one device."""
        f = self.v_ce_V if which == "igbt" else self.v_f_V
        hi = max(float(i_hi_A), 1.0)
        lo = 0.1 * hi if i_lo_A is None else float(i_lo_A)
        x = np.linspace(lo, hi, 17)
        v = np.array([f(float(a), t_j_c) for a in x])
        a = np.vstack([np.ones_like(x), x]).T
        (v0, r), *_ = np.linalg.lstsq(a, v, rcond=None)
        err = float(np.max(np.abs(a @ np.array([v0, r]) - v)))
        return {"v0_V": float(v0), "r_ohm": float(r), "max_err_V": err,
                "i_lo_A": float(lo), "i_hi_A": float(hi)}

    # ── switching energies ──────────────────────────────────────────────────
    def _e_curves(self, name: str):
        key = "e_" + name
        if key not in self._cache:
            self._cache[key] = _curves((self.doc.get("switching") or {}).get(name))
        return self._cache[key]

    def _rg_ratio(self, name: str, r_g: Optional[float], t_j_c: float
                  ) -> Tuple[float, Optional[str]]:
        """E(R_G)/E(R_G,ref) at this T_j from the card's E vs R_G figures."""
        sw = self.doc.get("switching") or {}
        ref = _num(sw.get("r_g_ref_ohm"))
        if r_g is None or ref is None or abs(float(r_g) - ref) < 1e-12:
            return 1.0, None
        blk = (sw.get("r_g") or {})
        xs = [float(x) for x in (blk.get("r_g_ohm") or [])]
        rows = blk.get(name) or []
        if len(xs) < 2 or not rows or float(r_g) <= 0:
            return 1.0, (f"R_G {float(r_g):g} ohm: the card publishes no E_{name[2:]} "
                         f"vs R_G curve — NOT rescaled from {ref:g} ohm")
        lx = [math.log10(x) for x in xs]
        by_t = []
        for row in rows:
            t = _num(row.get("t_j_c"))
            e = [float(v) for v in (row.get("e_mJ") or [])]
            if t is None or len(e) != len(xs):
                continue
            pts = list(zip(lx, e))
            e_r = interp(pts, math.log10(float(r_g)))
            e_ref = interp(pts, math.log10(ref))
            if e_r is not None and e_ref:
                by_t.append((t, max(e_r, 0.0) / e_ref))
        if not by_t:
            return 1.0, None
        by_t.sort()
        tt = min(max(float(t_j_c), by_t[0][0]), by_t[-1][0])
        k = float(interp(by_t, tt))
        out_of = float(r_g) < xs[0] or float(r_g) > xs[-1]
        return k, (f"R_G {float(r_g):g} ohm: x{k:.3f} on {name} (E vs R_G figure, "
                   f"ratio to {ref:g} ohm)" + (" — EXTRAPOLATED outside the figure's "
                                              f"{xs[0]:g}..{xs[-1]:g} ohm" if out_of else ""))

    def energies(self, *, i_A: float, t_j_igbt_c: float, t_j_diode_c: float,
                 v_dc_V: float, r_g_on_ohm: Optional[float] = None,
                 r_g_off_ohm: Optional[float] = None) -> Dict[str, Any]:
        """``{e_on_J, e_off_J, e_rr_J, extrapolated, notes}`` at one device current."""
        sw = self.doc.get("switching") or {}
        sc = sw.get("scaling") or {}
        v_ref = float(sw["v_cc_ref_V"])
        k_v = float(_num(sc.get("voltage_exponent")) or 1.35)
        k_rr = float(_num(sc.get("voltage_exponent_rr")) or 0.6)
        vr = max(float(v_dc_V), 0.0) / v_ref
        i = max(float(i_A), 0.0)
        out: Dict[str, Any] = {"notes": []}
        above = False
        for name, tj, kv, rg in (("e_on", t_j_igbt_c, vr ** k_v, r_g_on_ohm),
                                 ("e_off", t_j_igbt_c, vr ** k_v,
                                  r_g_off_ohm if r_g_off_ohm is not None else r_g_on_ohm),
                                 ("e_rr", t_j_diode_c, vr ** k_rr, r_g_on_ohm)):
            cur = self._e_curves(name)
            above = above or i > min(pts[-1][0] for _, pts in cur)
            e_mj = self._iv_at(cur, i, tj)
            k_rg, note = self._rg_ratio(name, rg, tj)
            if note and note not in out["notes"]:
                out["notes"].append(note)
            out[name + "_J"] = max(e_mj, 0.0) * 1e-3 * kv * k_rg
        out["extrapolated"] = above
        out["notes"].insert(0, f"bus scaling (V_dc/{v_ref:g} V)^{k_v:g} = {vr ** k_v:.4f} "
                               f"on E_on/E_off, ^{k_rr:g} = {vr ** k_rr:.4f} on E_rr "
                               f"[{sc.get('voltage_rule') or 'card rule'}]")
        return out

    def e_switch(self, *, i_d_A: float, t_j_c: float, v_dc_V: float,
                 v_gs_off_V: float = 0.0, r_g_ext_ohm: Optional[float] = None,
                 v_gs_on_V: float = 15.0, source: Optional[str] = None,
                 r_g_off_ext_ohm: Optional[float] = None,
                 l_sigma_nH: Optional[float] = None) -> Dict[str, Any]:
        """The MOSFET card's signature, for generic callers: E_on/E_off of the
        IGBT and E_rr of the FWD (as ``e_fr_J``), all at ``t_j_c``."""
        e = self.energies(i_A=i_d_A, t_j_igbt_c=t_j_c, t_j_diode_c=t_j_c,
                          v_dc_V=v_dc_V, r_g_on_ohm=r_g_ext_ohm,
                          r_g_off_ohm=r_g_off_ext_ohm)
        return {"e_on_J": e["e_on_J"], "e_off_J": e["e_off_J"], "e_fr_J": e["e_rr_J"],
                "extrapolated": e["extrapolated"], "extrapolated_below": False,
                "notes": e["notes"], "switching_energy_source": "curves"}

    def e_oss_J(self, v_dc_V: float) -> Optional[float]:
        return None

    def switching_energy_source(self) -> str:
        return "curves"

    def switching_table(self) -> Optional[Dict[str, Any]]:
        return None

    def switching_source_default(self) -> str:
        return "datasheet"

    def static_table(self) -> Optional[Dict[str, Any]]:
        return None

    def spice_availability(self) -> Dict[str, Any]:
        return {"has_table": False, "has_static": False, "model_status": "no_model",
                "model_status_reason": "IGBT module: datasheet curves only",
                "needs": None, "basis": None, "l_sigma_default_nH": None,
                "r_g_sets_ohm": [], "validation": None, "deviation_line": None}

    # ── what the report and the catalogue read ──────────────────────────────
    def provenance(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "datasheet": self.doc.get("datasheet_url") or self.doc.get("datasheet_file"),
            "datasheet_revision": self.doc.get("datasheet_revision"),
        }
        for blk, sub in (("ratings", None), ("gate", None), ("igbt", "v_ce_sat"),
                         ("diode", "v_f"), ("terminals", None), ("switching", None),
                         ("thermal", None)):
            src = self.doc.get(blk) or {}
            if sub:
                src = src.get(sub) or {}
            if isinstance(src, dict) and src.get("source"):
                out[blk if not sub else f"{blk}.{sub}"] = " ".join(str(src["source"]).split())
        try:
            from motor_ai_sim import catalog_sources as CS
            out["catalog_ref"] = CS.device_ref(self.part)
        except Exception:                                  # noqa: BLE001
            log.debug("device catalog_ref unavailable", exc_info=True)
        return out

    def row(self, *, i_switch_rms_A: Optional[float] = None) -> Dict[str, Any]:
        rat = self.doc.get("ratings") or {}
        v800 = lambda f, t: round(f(800.0, t), 3)           # noqa: E731
        i_hot = self.i_d_continuous(100.0)
        return {
            "part": self.part,
            "manufacturer": self.doc.get("manufacturer"),
            "family": self.doc.get("family"),
            "technology": self.doc.get("technology"),
            "topology": self.doc.get("topology"),
            "switching_energy_source": "curves",
            "basis_default": "datasheet",
            "spice": {k: v for k, v in self.spice_availability().items()
                      if k in ("has_table", "has_static", "model_status", "needs",
                               "basis", "l_sigma_default_nH", "r_g_sets_ohm",
                               "deviation_line")},
            "package": self.doc.get("package"),
            "package_common_name": self.doc.get("package_common_name"),
            "cooling": self.doc.get("cooling"),
            "v_dss_V": _num(rat.get("v_ces_V")),
            "v_ces_V": _num(rat.get("v_ces_V")),
            "i_d_100c_A": None if i_hot is None else round(i_hot, 1),
            "i_d_25c_A": None,
            "t_j_max_c": self.t_j_max_c,
            "r_ds_on_25c_mohm": None,
            "r_ds_on_175c_mohm": None,
            "v_ce_sat_800A_V": {"25": v800(self.v_ce_V, 25.0), "125": v800(self.v_ce_V, 125.0),
                                "175": v800(self.v_ce_V, 175.0)},
            "v_f_800A_V": {"25": v800(self.v_f_V, 25.0), "125": v800(self.v_f_V, 125.0),
                           "175": v800(self.v_f_V, 175.0)},
            "r_th_jc_k_w": None,
            "r_th_jc_max_k_w": self.r_th_jc_igbt_k_w,
            "r_th_jc_diode_max_k_w": self.r_th_jc_diode_k_w,
            "r_th_cs_k_w": self.r_th_cs_k_w,
            "package_size_mm": self.package_size(),
            "weight_g": _num(self.doc.get("weight_g")),
            "price": self.unit_price(),
            "image": self.doc.get("image"),
            "package_svg": self.package_outline_svg(),
            "footprint": _footprint(self.doc),
            "suggested_parallel": (None if i_switch_rms_A is None else
                                   self.suggested_parallel(i_switch_rms_A)),
            "datasheet_url": self.doc.get("datasheet_url"),
            "datasheet_revision": self.doc.get("datasheet_revision"),
        }


def _footprint(doc):
    from motor_ai_sim.inverter.devices import _footprint_of
    return _footprint_of(doc)


# ---------------------------------------------------------------------------
# One leg
# ---------------------------------------------------------------------------

def igbt_leg_losses(*, card: IgbtModuleCard, i_leg: np.ndarray, duty: np.ndarray,
                    n_par: int, f_sw: float, t_j_igbt_c: float, t_j_diode_c: float,
                    v_dc: float, r_g_on: Optional[float] = None,
                    r_g_off: Optional[float] = None, dead_time_s: float = 0.0,
                    include_lead: bool = True) -> Dict[str, Any]:
    """Losses of ONE leg (one module position pair, ``n_par`` modules in
    parallel) over one fundamental period, watts.  ``duty`` is the leg's
    high-side duty on the same grid as ``i_leg`` (positive = leaving)."""
    n = max(int(n_par), 1)
    i_leg = np.asarray(i_leg, dtype=float)
    d = np.clip(np.asarray(duty, dtype=float), 0.0, 1.0)
    a = np.abs(i_leg)
    i_dev = a / n
    f_td = min(max(float(dead_time_s) * float(f_sw), 0.0), 1.0)
    w_t = np.clip(np.where(i_leg > 0.0, d, 1.0 - d) - f_td, 0.0, 1.0)
    w_d = 1.0 - w_t
    i_max = float(np.max(i_dev)) if i_dev.size else 0.0
    ladder = np.linspace(0.0, max(i_max, 1e-9), 33)
    v_t = np.array([card.v_ce_V(float(x), t_j_igbt_c) for x in ladder])
    v_d = np.array([card.v_f_V(float(x), t_j_diode_c) for x in ladder])
    p_cond_t = n * float(np.mean(w_t * i_dev * np.interp(i_dev, ladder, v_t)))
    p_cond_d = n * float(np.mean(w_d * i_dev * np.interp(i_dev, ladder, v_d)))
    p_lead = (float(np.mean(i_leg ** 2)) * card.r_lead_ohm / n) if include_lead else 0.0

    e_on, e_off, e_rr = [], [], []
    extrapolated = False
    notes: List[str] = []
    for x in ladder:
        e = card.energies(i_A=float(x), t_j_igbt_c=t_j_igbt_c,
                          t_j_diode_c=t_j_diode_c, v_dc_V=v_dc,
                          r_g_on_ohm=r_g_on, r_g_off_ohm=r_g_off)
        e_on.append(e["e_on_J"]); e_off.append(e["e_off_J"]); e_rr.append(e["e_rr_J"])
        extrapolated = extrapolated or bool(e["extrapolated"])
        if not notes:
            notes = list(e["notes"])
    k = float(f_sw) * n
    p_on = k * float(np.mean(np.interp(i_dev, ladder, np.array(e_on))))
    p_off = k * float(np.mean(np.interp(i_dev, ladder, np.array(e_off))))
    p_rr = k * float(np.mean(np.interp(i_dev, ladder, np.array(e_rr))))

    i2 = float(np.mean(i_dev ** 2))
    p_igbt = p_cond_t + p_on + p_off
    p_diode = p_cond_d + p_rr
    fwd_share = (float(np.mean(w_t * i_leg ** 2)) / float(np.mean(i_leg ** 2))
                 if float(np.mean(i_leg ** 2)) > 0 else 0.0)
    return {
        "p_igbt_conduction_W": p_cond_t,
        "p_diode_conduction_W": p_cond_d,
        "p_lead_W": p_lead,
        "p_on_W": p_on, "p_off_W": p_off, "p_rr_W": p_rr,
        "p_igbt_W": p_igbt, "p_diode_W": p_diode,
        "p_total_W": p_igbt + p_diode + p_lead,
        "forward_share": fwd_share,
        "i_leg_rms_A": float(np.sqrt(np.mean(i_leg ** 2))),
        "i_switch_rms_A": float(np.sqrt(np.mean(i_leg ** 2) / 2.0)),
        "i_device_rms_A": float(np.sqrt(i2 / 2.0)),
        "i_igbt_rms_A": float(np.sqrt(np.mean(w_t * i_dev ** 2) / 2.0)),
        "i_diode_rms_A": float(np.sqrt(np.mean(w_d * i_dev ** 2) / 2.0)),
        "i_igbt_avg_A": float(np.mean(w_t * i_dev) / 2.0),
        "i_diode_avg_A": float(np.mean(w_d * i_dev) / 2.0),
        "i_device_peak_A": i_max,
        "dead_time_fraction": f_td,
        "extrapolated": extrapolated,
        "notes": notes,
    }


def closed_form_spwm_conduction(*, v0_V: float, r_ohm: float, i_peak_A: float,
                                m: float, cos_phi: float, device: str) -> float:
    """The textbook sine-triangle conduction loss of ONE IGBT or ONE diode
    for a sinusoidal current (e.g. Semikron Application Manual 2015, eq. 5.4/5.6):

        IGBT:  V0*I/(2 pi)*(1 + pi m cos(phi)/4) + r*I^2*(1/8 + m cos(phi)/(3 pi))
        diode: V0*I/(2 pi)*(1 - pi m cos(phi)/4) + r*I^2*(1/8 - m cos(phi)/(3 pi))

    with phi the angle by which the current LAGS the voltage (consumer
    arrows): cos(phi) < 0 is a generator."""
    s = 1.0 if device == "igbt" else -1.0
    ip = float(i_peak_A)
    return (v0_V * ip / (2.0 * math.pi) * (1.0 + s * math.pi * m * cos_phi / 4.0)
            + r_ohm * ip ** 2 * (1.0 / 8.0 + s * m * cos_phi / (3.0 * math.pi)))


# ---------------------------------------------------------------------------
# The limits
# ---------------------------------------------------------------------------

def _limit_rows(*, card: IgbtModuleCard, v_dc_V: float, t_case_mod_c: float,
                t_j_igbt_c: float, t_j_diode_c: float, i_igbt_rms_A: float,
                i_diode_rms_A: float, i_peak_A: float, v_ge_on: float,
                v_ge_off: float) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    def row(name, value, limit, unit, source, note=""):
        if value is None or limit is None:
            rows.append({"name": name, "value": value, "limit": limit, "unit": unit,
                         "margin": None, "utilisation_pct": None,
                         "verdict": "not_judged", "source": source,
                         "note": note or "the card does not publish this limit"})
            return
        rows.append({"name": name, "value": round(float(value), 2),
                     "limit": round(float(limit), 2), "unit": unit,
                     "margin": round(float(limit) - float(value), 2),
                     "utilisation_pct": round(100.0 * value / limit, 1) if limit else None,
                     "verdict": "pass" if value <= limit else "fail",
                     "source": source, "note": note})

    rating = card.i_d_rating(t_case_mod_c)
    row("IGBT continuous current per device", i_igbt_rms_A, rating.get("i_a"), "A rms",
        (rating.get("source") or "ratings") + " — " + str(rating.get("basis")),
        f"rms over the period at the solved module case {t_case_mod_c:.0f} degC "
        "against the DC rating I_C")
    row("FWD continuous current per device", i_diode_rms_A, card.i_f_A, "A rms",
        "the card's ratings block (I_F)", "rms over the period against the DC rating I_F")
    row("Peak collector current per device", i_peak_A, card.i_d_pulsed_A, "A",
        "the card's ratings block (I_CRM, 1 ms; RBSOA 1600 A at 175 degC)",
        "recurs every fundamental period — the junction rows are the binding judge")
    row("Peak forward current per diode", i_peak_A, card.i_sm_A, "A",
        "the card's ratings block (I_FRM, 1 ms)")
    row("Junction temperature, IGBT", t_j_igbt_c, card.t_j_max_c, "degC",
        "the card's ratings block (T_vjop)", "the hottest IGBT of the controller")
    row("Junction temperature, FWD", t_j_diode_c, card.t_j_max_c, "degC",
        "the card's ratings block (T_vjop)", "the hottest diode of the controller")
    row("Case temperature", t_case_mod_c, card.t_case_max_c, "degC",
        "the card's ratings block (T_c)", "module baseplate under the hottest position")
    v_ces = card.v_ces_V
    use = 100.0 * float(v_dc_V) / v_ces
    verdict = ("fail" if v_dc_V > v_ces else
               "warn" if use > 100.0 * V_CES_WARN_FRACTION else "pass")
    rows.append({"name": "DC link vs V_CES", "value": round(float(v_dc_V), 1),
                 "limit": round(v_ces, 1), "unit": "V",
                 "margin": round(v_ces - float(v_dc_V), 1),
                 "utilisation_pct": round(use, 1), "verdict": verdict,
                 "source": "the card's ratings block (V_CES)",
                 "note": (f"{use:.0f} % of the blocking voltage; above "
                          f"{100 * V_CES_WARN_FRACTION:.0f} % there is little left for "
                          "the turn-off overshoot (a design warning of this module)"
                          if verdict == "warn" else f"{use:.0f} % of the blocking voltage")})
    lo, hi = card.v_gs_static_window()
    if lo is None or hi is None:
        rows.append({"name": "Gate voltage", "value": None, "limit": None, "unit": "V",
                     "margin": None, "utilisation_pct": None, "verdict": "not_judged",
                     "source": "the card's gate block", "note": "no V_GES published"})
    else:
        inside = lo <= v_ge_off <= hi and lo <= v_ge_on <= hi
        rows.append({"name": "Gate voltage", "value": None, "limit": None, "unit": "V",
                     "margin": round(min(hi - v_ge_on, v_ge_off - lo), 2),
                     "utilisation_pct": None, "verdict": "pass" if inside else "fail",
                     "source": "the card's ratings block (V_GES)",
                     "note": f"driven {v_ge_on:g} / {v_ge_off:g} V against {lo:g} … {hi:g} V"})
    rows.append({"name": "Short-circuit / RBSOA", "value": None, "limit": None, "unit": "",
                 "margin": None, "utilisation_pct": None, "verdict": "not_judged",
                 "source": "the card's RBSOA figure (p.7)",
                 "note": "no stray inductance or overshoot is modelled — the RBSOA "
                         "(1600 A up to 1200 V) is not judged beyond the bus row"})
    return rows


# ---------------------------------------------------------------------------
# The solve — called by losses.solve_controller after its common set-up
# ---------------------------------------------------------------------------

def _f(v, name, *, default=None, positive=False):
    from motor_ai_sim.inverter.losses import _f as f_
    return f_(v, name, default=default, positive=positive)


def solve_igbt_controller(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """The IGBT/diode half of :func:`losses.solve_controller`.

    ``ctx`` carries everything the common set-up resolved (topology, the
    operating point, the per-leg currents and modulation, the warnings so
    far); this function owns the device model, the two-temperature thermal
    loop and the response, which keeps the MOSFET path untouched.
    """
    from motor_ai_sim.inverter import losses as L

    req = ctx["req"]
    card: IgbtModuleCard = ctx["card"]
    topo = ctx["topo"]
    legs_spec = ctx["legs_spec"]
    m_by_bridge = ctx["m_by_bridge"]
    warnings: List[str] = ctx["warnings"]
    v_dc, f_el, f_sw = ctx["v_dc"], ctx["f_el"], ctx["f_sw"]
    p_ac, pmod, direction = ctx["p_ac"], ctx["pmod"], ctx["direction"]
    n_par = ctx["n_par"]

    plate = L._build_cooling(req.get("cooling") or {})
    r_cs_card = card.r_th_cs_k_w
    if req.get("r_tim_k_w") is not None:
        r_cs = _f(req.get("r_tim_k_w"), "r_tim_k_w")
        r_cs_basis = "the request (r_tim_k_w)"
    elif r_cs_card is not None:
        r_cs = float(r_cs_card)
        r_cs_basis = "the card: R_th(c-s) per 1 IGBT + 1 FWD with 1 W/(m.K) grease"
    else:
        r_cs = L.DEFAULT_TIM_K_W
        r_cs_basis = "this module's default TIM (the card publishes no R_th(c-s))"
    r_spread = _f(req.get("r_spread_k_w"), "r_spread_k_w", default=0.0)
    r_jc_t, r_jc_d = card.r_th_jc_igbt_k_w, card.r_th_jc_diode_k_w
    cooling_mode = str(plate.as_dict().get("mode") or "liquid")

    dead_given = req.get("dead_time_us")
    dead_us = _f(dead_given, "dead_time_us", default=DEFAULT_DEAD_TIME_US)
    if dead_us < 0.0:
        raise L.ControllerRefusal("dead_time_us cannot be negative", ["dead_time_us"])
    dead_s = dead_us * 1e-6
    v_ge_on_ref, v_ge_off_ref = card.gate_reference()
    v_ge_on = _f(req.get("v_gs_on_V"), "v_gs_on_V", default=v_ge_on_ref)
    v_ge_off = _f(req.get("v_gs_off_V"), "v_gs_off_V", default=v_ge_off_ref)
    if abs(v_ge_on - v_ge_on_ref) > 1e-9 or abs(v_ge_off - v_ge_off_ref) > 1e-9:
        warnings.append(
            f"{card.part}: the card's curves are at V_GE = {v_ge_on_ref:+g}/{v_ge_off_ref:+g} V; "
            f"the requested {v_ge_on:+g}/{v_ge_off:+g} V is judged against V_GES but the "
            "on-state and switching curves are NOT rescaled to it")
    r_g = req.get("r_g_ext_ohm")
    r_g = card.r_g_ref_ohm if r_g is None else _f(r_g, "r_g_ext_ohm", positive=True)
    r_g_off = req.get("r_g_off_ext_ohm")
    r_g_off = r_g if r_g_off is None else _f(r_g_off, "r_g_off_ext_ohm", positive=True)
    include_lead = bool(req.get("include_lead_resistance", True))
    if str(req.get("switching_source") or "datasheet").strip().lower() != "datasheet":
        raise L.ControllerRefusal(
            f"{card.part} is an IGBT module: only the datasheet curves exist "
            "(no SPICE table)", ["switching_source"], code="no_spice_table")

    setups: Dict[str, wf.ModulatorSetup] = {}
    for b in topo.bridges:
        setups[b.id] = wf.ModulatorSetup(
            f_elec_hz=f_el, f_carrier_hz=f_sw, v_dc_V=v_dc,
            modulation_index=m_by_bridge[b.id],
            samples_per_carrier=int(req.get("samples_per_carrier") or 24),
            dead_time_s=dead_s,
            modulation=(pmod if b.kind == "three_phase_2l" else "sine"))
    u = setups[topo.bridges[0].id].grid()
    leg_current: Dict[Tuple[str, str], np.ndarray] = {}
    leg_duty: Dict[Tuple[str, str], np.ndarray] = {}
    for b in topo.bridges:
        for lg in b.legs:
            key = (b.id, lg.name)
            spec = legs_spec[key]
            leg_current[key] = spec["i_peak_A"] * np.sin(
                2.0 * math.pi * u + math.radians(spec["i_phase_deg"]))
            leg_duty[key] = wf.leg_duty(setups[b.id], u, spec["v_phase_deg"])

    t_cap = card.t_j_curve_max_c()
    t0 = float(req.get("t_j_start_c") or (plate.t_in_c + 20.0))
    tj_t = tj_d = t0
    iters, clamped, diverged = 0, False, False
    per_leg: Dict[Tuple[str, str], Dict[str, Any]] = {}
    p_total = 0.0
    t_case = plate.t_in_c
    t_cool_mean = plate.t_in_c
    ct: Optional[Dict[str, float]] = None
    cp: Dict[str, Any] = {}
    r_pos = r_cs + r_spread
    t_mod = t_case
    while True:
        iters += 1
        et, ed = min(tj_t, t_cap), min(tj_d, t_cap)
        clamped = clamped or tj_t > t_cap + 1e-9 or tj_d > t_cap + 1e-9
        cp = plate.resistance(t_wall_c=t_case)
        r_pos = r_cs + r_spread + float(cp.get("r_film_per_device_k_w") or 0.0)
        per_leg, p_total = {}, 0.0
        for b in topo.bridges:
            for lg in b.legs:
                key = (b.id, lg.name)
                res = igbt_leg_losses(card=card, i_leg=leg_current[key],
                                      duty=leg_duty[key], n_par=b.devices_parallel,
                                      f_sw=f_sw, t_j_igbt_c=et, t_j_diode_c=ed,
                                      v_dc=v_dc, r_g_on=r_g, r_g_off=r_g_off,
                                      dead_time_s=dead_s, include_lead=include_lead)
                per_leg[key] = res
                p_total += res["p_total_W"]
        if getattr(plate, "plumbing", "shared") in ("parallel", "series"):
            ct = plate.case_temperature(p_total, cp)
            t_cool_mean, t_case = ct["t_coolant_mean_c"], ct["t_case_c"]
        else:
            m_dot = float(cp.get("m_dot_kg_s") or 0.0)
            cpj = float(cp.get("cp_j_kgk") or 1.0)
            rise = p_total / (m_dot * cpj) if m_dot * cpj > 0 else 0.0
            t_cool_mean = plate.t_in_c + 0.5 * rise
            t_case = t_cool_mean + p_total * float(cp["r_k_w"])
            ct = None
        new_t = new_d = t_mod = -1e9
        for b in topo.bridges:
            npb = max(int(b.devices_parallel), 1)
            for lg in b.legs:
                r = per_leg[(b.id, lg.name)]
                pt, pd, pl = (r["p_igbt_W"] / 2 / npb, r["p_diode_W"] / 2 / npb,
                              r["p_lead_W"] / 2 / npb)
                tm = t_case + (pt + pd + pl) * r_pos
                t_mod = max(t_mod, tm)
                new_t = max(new_t, tm + pt * r_jc_t)
                new_d = max(new_d, tm + pd * r_jc_d)
        if max(new_t, new_d) > card.t_j_max_c + 200.0:
            tj_t, tj_d, diverged = new_t, new_d, True
            break
        if (abs(new_t - tj_t) < _TOL_K and abs(new_d - tj_d) < _TOL_K) or iters >= _MAX_ITER:
            tj_t, tj_d = new_t, new_d
            break
        tj_t += 0.6 * (new_t - tj_t)
        tj_d += 0.6 * (new_d - tj_d)
    converged = iters < _MAX_ITER and not diverged

    # ── per leg / per bridge ───────────────────────────────────────────────
    split = {k: 0.0 for k in ("igbt_conduction_W", "diode_conduction_W", "lead_W",
                              "on_W", "off_W", "rr_W")}
    bridges_out: List[Dict[str, Any]] = []
    i_t_rms = i_d_rms = i_pk = 0.0
    extrapolated = False
    card_notes: List[str] = []
    fwd_num = fwd_den = 0.0
    for b in topo.bridges:
        npb = max(int(b.devices_parallel), 1)
        legs_out, pb = [], 0.0
        for lg in b.legs:
            r = per_leg[(b.id, lg.name)]
            pt, pd, pl = r["p_igbt_W"] / 2 / npb, r["p_diode_W"] / 2 / npb, r["p_lead_W"] / 2 / npb
            tm = t_case + (pt + pd + pl) * r_pos
            for k_out, k_in in (("igbt_conduction_W", "p_igbt_conduction_W"),
                                ("diode_conduction_W", "p_diode_conduction_W"),
                                ("lead_W", "p_lead_W"), ("on_W", "p_on_W"),
                                ("off_W", "p_off_W"), ("rr_W", "p_rr_W")):
                split[k_out] += r[k_in]
            i_t_rms = max(i_t_rms, r["i_igbt_rms_A"])
            i_d_rms = max(i_d_rms, r["i_diode_rms_A"])
            i_pk = max(i_pk, r["i_device_peak_A"])
            extrapolated = extrapolated or r["extrapolated"]
            fwd_num += r["forward_share"] * r["i_leg_rms_A"] ** 2
            fwd_den += r["i_leg_rms_A"] ** 2
            if not card_notes:
                card_notes = list(r["notes"])
            pb += r["p_total_W"]
            legs_out.append({
                "leg": lg.name, "coils": list(lg.coils),
                "i_leg_rms_A": round(r["i_leg_rms_A"], 1),
                "i_switch_rms_A": round(r["i_switch_rms_A"], 1),
                "i_device_rms_A": round(r["i_device_rms_A"], 1),
                "i_device_peak_A": round(r["i_device_peak_A"], 1),
                "i_igbt_rms_A": round(r["i_igbt_rms_A"], 1),
                "i_diode_rms_A": round(r["i_diode_rms_A"], 1),
                "p_conduction_W": round(r["p_igbt_conduction_W"] + r["p_diode_conduction_W"], 1),
                "p_igbt_conduction_W": round(r["p_igbt_conduction_W"], 1),
                "p_diode_conduction_W": round(r["p_diode_conduction_W"], 1),
                "p_lead_W": round(r["p_lead_W"], 1),
                "p_third_quadrant_W": 0.0,
                "p_switching_W": round(r["p_on_W"] + r["p_off_W"] + r["p_rr_W"], 1),
                "p_switching_on_W": round(r["p_on_W"], 1),
                "p_switching_off_W": round(r["p_off_W"], 1),
                "p_switching_recovery_W": round(r["p_rr_W"], 1),
                "p_e_oss_W": 0.0,
                "p_leg_W": round(r["p_total_W"], 1),
                "p_switch_W": round(r["p_total_W"] / 2.0, 1),
                "p_device_W": round((r["p_total_W"] / 2.0) / npb, 2),
                "p_igbt_per_device_W": round(pt, 2),
                "p_diode_per_device_W": round(pd, 2),
                "t_case_module_c": round(tm, 1),
                "t_j_c": round(max(tm + pt * r_jc_t, tm + pd * r_jc_d), 1),
                "t_j_igbt_c": round(tm + pt * r_jc_t, 1),
                "t_j_diode_c": round(tm + pd * r_jc_d, 1),
                "r_ds_on_mohm": None,
            })
        d = b.as_dict()
        d.update({"legs": legs_out, "p_loss_W": round(pb, 1),
                  "modulation_index": round(m_by_bridge[b.id], 4)})
        bridges_out.append(d)
    fwd_share = fwd_num / fwd_den if fwd_den > 0 else 0.0
    t_j_max = max(tj_t, tj_d)

    # ── DC link ────────────────────────────────────────────────────────────
    states, currents = [], []
    for b in topo.bridges:
        for lg in b.legs:
            spec = legs_spec[(b.id, lg.name)]
            states.append(wf.switch_states(setups[b.id], u, spec["v_phase_deg"]))
            currents.append(leg_current[(b.id, lg.name)])
    dc = wf.dc_link_current(states, currents)

    # ── limits ─────────────────────────────────────────────────────────────
    limits = _limit_rows(card=card, v_dc_V=v_dc, t_case_mod_c=t_mod,
                         t_j_igbt_c=tj_t, t_j_diode_c=tj_d, i_igbt_rms_A=i_t_rms,
                         i_diode_rms_A=i_d_rms, i_peak_A=i_pk,
                         v_ge_on=v_ge_on, v_ge_off=v_ge_off)
    violations: List[str] = []
    for r in limits:
        if r["verdict"] == "fail":
            violations.append(f"{r['name']}: {r['value']} {r['unit']} against the "
                              f"{card.part} limit of {r['limit']} {r['unit']}"
                              + (f" — {r['note']}" if r["note"] else ""))
        elif r["verdict"] == "warn":
            warnings.append(f"{r['name']}: {r['note']}")
    if diverged:
        violations.append("THERMAL RUNAWAY: the junction temperatures have no steady "
                          "state with this cooling")
    elif not converged:
        violations.append(f"the junction temperatures did not settle within {_MAX_ITER} iterations")
    if clamped:
        warnings.append(f"a junction ran past the hottest {card.part} curve "
                        f"({t_cap:.0f} degC): the characteristics were held there; the "
                        "T_j reported is the unclamped one (a LOWER bound)")
    if extrapolated:
        warnings.append(f"each device carries more current than the {card.part} "
                        "switching-energy curves cover — linearly extrapolated")
    if dead_given is None:
        warnings.append(f"dead time {DEFAULT_DEAD_TIME_US:g} us is this module's stated "
                        "IGBT default (none was given)")

    # ── efficiency ─────────────────────────────────────────────────────────
    eta_shaft = ctx["eta_shaft"]
    if direction == "generator":
        eta_inv = (p_ac - p_total) / p_ac if p_ac > 0 else None
    else:
        eta_inv = p_ac / (p_ac + p_total) if (p_ac + p_total) > 0 else None
    eta_wall = (eta_inv * eta_shaft) if (eta_inv and eta_shaft) else None

    ripple_out = L._ripple_block(req, topo=topo, legs_spec=legs_spec,
                                 m_by_bridge=m_by_bridge, m=ctx["m"],
                                 i_leg_3ph=ctx["i_leg_3ph"], v_dc=v_dc, f_el=f_el,
                                 f_sw=f_sw, pmod=pmod, lag_deg=ctx["lag_deg"],
                                 sd=ctx["sd"], warnings=warnings)

    # ── the linear forms, at the solved temperatures ───────────────────────
    i_fit = max(i_pk, 1.0)
    fit_t = card.linear_fit("igbt", min(tj_t, t_cap), i_fit)
    fit_d = card.linear_fit("diode", min(tj_d, t_cap), i_fit)

    # ── the waveform the motor sees (picture only) ─────────────────────────
    base = setups[topo.bridges[0].id]
    spc = base.samples_per_carrier
    if dead_s > 0.0:
        spc = max(spc, min(400, int(math.ceil(4.0 / (dead_s * f_sw)))))
    st0 = wf.ModulatorSetup(f_elec_hz=base.f_elec_hz, f_carrier_hz=base.f_carrier_hz,
                            v_dc_V=base.v_dc_V, modulation_index=base.modulation_index,
                            samples_per_carrier=spc, dead_time_s=dead_s,
                            modulation=base.modulation)
    n0 = max(int(topo.bridges[0].devices_parallel), 1)

    def _v_sd(arr: np.ndarray) -> np.ndarray:
        return np.array([card.v_f_V(float(x) / n0, min(tj_d, t_cap)) for x in arr])

    waves = wf.coil_waveforms(bridges=topo.bridges, setup=st0, legs=legs_spec,
                              r_ds_ohm=(0.5 * (fit_t["r_ohm"] + fit_d["r_ohm"])
                                        + card.r_lead_ohm * include_lead) / max(n_par, 1),
                              v_sd=_v_sd, dead_samples=wf.dead_samples_for(st0))
    waves["device_drop_note"] = (
        "picture only: the conducting device is drawn as a resistance (the mean "
        "IGBT/FWD slope + lead); the thresholds V_CE0/V_F0 are in the loss "
        "numbers and in the coupled drive, not in this chart")

    i_leg_3ph = ctx["i_leg_3ph"]
    out = {
        "ok": not violations,
        "feasible": not violations,
        "limits": limits,
        "limits_verdict": ("fail" if violations else
                           "warn" if any(r["verdict"] == "warn" for r in limits) else "pass"),
        "device": card.part,
        "device_technology": "igbt_module",
        "device_row": card.row(i_switch_rms_A=max(i_t_rms, i_d_rms) * max(n_par, 1)),
        "provenance": card.provenance(),
        "topology": topo.as_dict(),
        "set_split": ctx["set_split_out"],
        "bridges": bridges_out,
        "losses": {
            "conduction_W": round(split["igbt_conduction_W"] + split["diode_conduction_W"]
                                  + split["lead_W"], 1),
            "igbt_conduction_W": round(split["igbt_conduction_W"], 1),
            "diode_conduction_W": round(split["diode_conduction_W"], 1),
            "lead_W": round(split["lead_W"], 1),
            "third_quadrant_W": 0.0,
            "switching_W": round(split["on_W"] + split["off_W"] + split["rr_W"], 1),
            "igbt_switching_W": round(split["on_W"] + split["off_W"], 1),
            "e_on_W": round(split["on_W"], 1),
            "e_off_W": round(split["off_W"], 1),
            "diode_recovery_W": round(split["rr_W"], 1),
            "igbt_W": round(split["igbt_conduction_W"] + split["on_W"] + split["off_W"], 1),
            "diode_W": round(split["diode_conduction_W"] + split["rr_W"], 1),
            "e_oss_W": 0.0, "e_oss_reference_W": 0.0,
            "total_W": round(p_total, 1),
            "e_oss_policy": "not_applicable",
            "switching_energy_source": "curves",
            "switching_source": "datasheet",
            "switching_basis": "datasheet",
            "conduction_source": "datasheet",
            "basis_label": "datasheet curves (IGBT module: V_CE(sat), V_F, E_on/E_off/E_rr)",
            "spice_deviation_line": None, "spice_validation": None,
            "include_lead_resistance": include_lead,
        },
        "thermal": {
            "t_j_max_c": round(t_j_max, 1),
            "t_j_igbt_max_c": round(tj_t, 1),
            "t_j_diode_max_c": round(tj_d, 1),
            "t_j_limit_c": card.t_j_max_c,
            "margin_K": round(card.t_j_max_c - t_j_max, 1),
            "t_case_c": round(t_case, 1),
            "t_case_module_c": round(t_mod, 1),
            "t_coolant_mean_c": round(t_cool_mean, 1),
            "t_coolant_in_c": plate.t_in_c,
            "coolant_rise_K": (round(ct["rise_total_K"], 2) if ct else
                               round(2.0 * (t_cool_mean - plate.t_in_c), 2)),
            "plumbing": ({k: round(v, 3) for k, v in ct.items()}
                         | {"mode": plate.plumbing, "n_plates": plate.n_plates}
                         if ct else None),
            "r_th_jc_k_w": r_jc_t,
            "r_th_jc_igbt_k_w": r_jc_t,
            "r_th_jc_diode_k_w": r_jc_d,
            "r_th_jc_basis": card.r_th_jc_basis,
            "r_th_cs_k_w": r_cs, "r_th_cs_basis": r_cs_basis,
            "r_tim_k_w": r_cs,
            "r_spread_k_w": r_spread,
            "r_coldplate_k_w": round(float(cp["r_k_w"]), 5),
            "r_film_per_device_k_w": round(float(cp.get("r_film_per_device_k_w") or 0.0), 5),
            "cooling_mode": cooling_mode,
            "coldplate": {**plate.as_dict(),
                          **{k: (round(v, 4) if isinstance(v, float) else v)
                             for k, v in cp.items()}},
            "node_note": ("t_case_c is the cooling surface under the modules (the "
                          "MOSFET model's name); t_case_module_c is the module "
                          "baseplate of the hottest switch position = t_case_c + "
                          "(P_IGBT + P_FWD + P_lead) x (R_th(c-s) + R_spread + R_film)"),
            "iterations": iters, "converged": converged, "tol_K": _TOL_K,
        },
        "dc_link": {**{k: round(v, 1) for k, v in dc.items()},
                    "power_flow": ("link -> machine" if direction == "motor"
                                   else "machine -> link")},
        "power_direction": direction,
        "conduction_direction": {
            "reverse_share": round(1.0 - fwd_share, 4),
            "forward_share": round(fwd_share, 4),
            "note": ("share of the leg current's I^2 carried by the IGBTs (forward) "
                     "and by the FWDs (reverse) — the IGBT/diode split of this "
                     "operating point"),
        },
        "device_linear_model": {
            "igbt": {"v0_V": round(fit_t["v0_V"], 4), "r_mohm": round(fit_t["r_ohm"] * 1e3, 4),
                     "t_j_c": round(min(tj_t, t_cap), 1),
                     "fit_span_A": [round(fit_t["i_lo_A"], 1), round(fit_t["i_hi_A"], 1)],
                     "max_err_V": round(fit_t["max_err_V"], 4)},
            "diode": {"v0_V": round(fit_d["v0_V"], 4), "r_mohm": round(fit_d["r_ohm"] * 1e3, 4),
                      "t_j_c": round(min(tj_d, t_cap), 1),
                      "fit_span_A": [round(fit_d["i_lo_A"], 1), round(fit_d["i_hi_A"], 1)],
                      "max_err_V": round(fit_d["max_err_V"], 4)},
            "r_lead_mohm": round(card.r_lead_ohm * 1e3, 4),
        },
        "ripple": ripple_out,
        "efficiency": {
            "inverter": None if eta_inv is None else round(eta_inv, 5),
            "inverter_definition": ("(P_ac - P_loss) / P_ac — generator: the machine's "
                                    "AC power in, the link's DC out"
                                    if direction == "generator" else
                                    "P_ac / (P_ac + P_loss) — motor: DC in, AC out"),
            "shaft": None if eta_shaft is None else round(eta_shaft, 5),
            "wall_to_shaft": None if eta_wall is None else round(eta_wall, 5),
            "wall_to_shaft_direction": ("shaft -> DC link" if direction == "generator"
                                        else "DC link -> shaft"),
            **({"shaft_to_dc": None if eta_wall is None else round(eta_wall, 5)}
               if direction == "generator" else {}),
            "note": ("inverter efficiency x the duty's ONE shaft efficiency"
                     if eta_shaft is not None else
                     (req.get("_efficiency_shaft_note")
                      or "no shaft efficiency is known for this point")),
        },
        "point": {
            "i_phase_rms_A": round(ctx["i_ph"], 1),
            "i_leg_rms_3ph_A": round(i_leg_3ph, 1),
            "connection_from": ("standalone run: each inverter a star-connected 3-phase "
                                "load, typed current per phase" if ctx["standalone"] else
                                "the duty (star/delta is a property of the motor)"),
            "i_switch_rms_max_A": round(max((r["i_switch_rms_A"] for r in per_leg.values()),
                                            default=0.0), 1),
            "star_delta": ctx["sd"],
            "p_ac_W": round(p_ac, 1),
            "f_elec_hz": round(f_el, 3),
            "f_carrier_hz": round(f_sw, 1),
            "f_carrier_eff_hz": round(st0.f_carrier_eff_hz, 1),
            "modulation_index": round(ctx["m"], 4),
            "pwm_modulation": pmod,
            "modulation_linear_limit": round(ctx["m_lin"], 4),
            "power_factor": round(ctx["pf"], 4),
            "load_angle_deg": round(ctx["phi_deg"], 2),
            "power_direction": direction,
            "current_lag_deg": round(ctx["lag_deg"], 2),
            "cos_current_lag": round(math.cos(math.radians(ctx["lag_deg"])), 4),
            "angle_convention": ("consumer (motor) reference arrows: the fundamental "
                                 "current lags the phase voltage by phi (motor) or "
                                 "180 - phi (generator)"),
            "v_dc_V": round(v_dc, 2),
            "v_machine_phase_rms_V": round(ctx["v_machine_phase_rms"], 1),
            "v_coil_rms_V": round(ctx["v_coil_rms"], 1),
            "coils_per_phase": ctx["coils_per_phase"],
            "winding_n_parallel": ctx["a_par"],
            "i_coil_rms_A": round(ctx["i_coil_rms"], 2),
            "bridge_wiring": ctx["wiring_notes"],
            "rpm": req.get("rpm"),
            "standalone": ctx["standalone"],
            "n_inverters": len(topo.bridges) if ctx["standalone"] else None,
            "modulation_scheme": ctx["scheme"],
            "m_linear_limit": round(ctx["m_linear"], 4),
        },
        "settings": {
            "devices_parallel": n_par,
            "dead_time_us": dead_us,
            "v_gs_on_V": v_ge_on, "v_gs_off_V": v_ge_off,
            "r_g_ext_ohm": r_g, "r_g_off_ext_ohm": r_g_off,
            "l_sigma_nH": None,
            "switching_source": "datasheet",
            "pwm_modulation": pmod,
            "samples_per_carrier": st0.samples_per_carrier,
            "grid_points": int(u.size),
            "include_lead_resistance": include_lead,
        },
        "waveforms": waves,
        "warnings": warnings,
        "violations": violations,
        "model_notes": card_notes + [
            "IGBT module: conduction split per sample by the current's direction and "
            "the leg's own duty (i > 0: upper IGBT for d, lower FWD for 1 - d; i < 0: "
            "lower IGBT for 1 - d, upper FWD for d)",
            f"dead time {dead_us:g} us: t_d*f_sw = {dead_s * f_sw:.4f} of every carrier "
            "period moves from the IGBT about to turn on to the conducting FWD",
            "V_CE(sat)/V_F: the card's CHIP curves (V_GE = 15 V), linear in I, linear in "
            "T_j between the 25/125/150/175 degC curves",
            (f"module lead resistance {card.r_lead_ohm * 1e3:g} mOhm per switch position "
             "(terminal - chip V at 800 A, p.3) adds <i^2>*R_lead per leg — heats the "
             "case node, not the junctions" if include_lead else
             "module lead resistance NOT included (include_lead_resistance = false)"),
            "one E_on + E_off of the conducting IGBT and one E_rr of the opposite FWD per "
            "carrier period per leg; E_on/E_off at T_j,IGBT, E_rr at T_j,FWD",
            f"two junction temperatures per switch position: T_j = T_case,module + P x "
            f"R_th(j-c) (IGBT {r_jc_t:g}, FWD {r_jc_d:g} K/W); T_case,module = cooling "
            f"surface + (P_IGBT + P_FWD + P_lead) x R_th(c-s) {r_cs:g} K/W ({r_cs_basis})",
            f"{n_par} module(s) per leg share the current EQUALLY",
        ] + ([f"{pmod}: the zero sequence is in the duty, so it moves conduction between "
              "IGBT and FWD — unlike the MOSFET model, it is NOT loss-neutral here"]
             if pmod != "sine" else []),
    }
    return out
