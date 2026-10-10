"""Stage 3 of the passport — PWM motor losses, inverter losses, the system
limit and the ``pwm_variants`` block (2026-10-05, Ø40 full card).

Sources, each one labelled on every number it produces:

* **motor PWM extra loss** — 2-D FEM anchors: the Controller's bridge
  (``drive="inverter"``, centred SVPWM, the device's conduction drop and dead
  time in the circuit) against a sine-current reference at the extracted
  fundamental on the SAME mesh and time step (route ``harm_ref``:
  ``dP_harm = P_loss(PWM) − P_loss(sine)``), at the rated and the peak point,
  per carrier / dead-time class.  Other loss-grid speeds: the anchor of the
  same current row scaled by the SVPWM harmonic-distortion factor
  HDF(m) (Holtz) at the point's modulation index — ripple current² at a fixed
  carrier, bus and inductance; stated as a scaling, not a FEM value.
* **inverter loss** — :func:`motor_ai_sim.inverter.losses._leg_losses` per leg
  (the Controller tab's own model): conduction (I²·R_DS(on)(T_j) or the
  vendor model's V_DS(I) on the SPICE basis), third-quadrant conduction in
  the dead time, switching energies from the card's SPICE double-pulse table
  (Si) or the times-and-charges overlap + E_oss (GaN, no published E_on/E_off
  and no public SPICE model).
* **board thermal limit** — the controller project's own continuous ratings
  (README m = 0.89: 6S 619 W / 12S 959 W favourable airflow, 542 / 825 W weak,
  ≈ 390 / 560 W next to a hot motor; R30 study: 26.1 / 20.2 A phase rms at the
  favourable rating; limit = board 110 °C with heatsink ≈ 95 °C and
  T_j ≈ 110 °C) define, per cooling class, the board heat budget
  Q* = P_inverter,Si + P_board copper at the Si rating current.  Another
  device on the same board is allowed the same Q* (one-node board model,
  T_ambient 45 °C, R_j-hs calibrated on the Si device at its rating).  An
  ESTIMATE on the controller project's numbers, labelled as such.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from motor_ai_sim.passport_v1 import losses as LS
from motor_ai_sim.passport_v1 import psimap as PM

SQ2 = math.sqrt(2.0)

# ─────────────────────────────────────────────────────────────────────────────
#  the controller (Controller_CIANO14_40_60V/controller_24V_FOC)
# ─────────────────────────────────────────────────────────────────────────────

CTRL_SOURCE = ("Controller_CIANO14_40_60V/controller_24V_FOC: README (ratings at m = 0.89, "
               "board limit 110 °C), docs/calc_notes.md §1-2 (UCC27289 12 V, R_G 10/12 ohm, "
               "driver 1.3/0.85 ohm, L_loop <= 3 nH), checks/r30_power_losses_2026-10-04 "
               "(26.1/20.2 A continuous, board copper 0.00551·I² W at 20 °C)")

#: Continuous ratings per cooling class (README), W, and the Si rating current
#: of the favourable class (R30 INPUTS_AND_SCOPE), A rms.
RATINGS = {
    "6S": {"W": {"favourable": 619.0, "weak": 542.0, "hot_motor": 390.0}, "I_fav": 26.1},
    "12S": {"W": {"favourable": 959.0, "weak": 825.0, "hot_motor": 560.0}, "I_fav": 20.2},
}
T_AMB_C = 45.0                 # README: "from cold (45 °C)" — stated assumption
T_HS_LIMIT_C = 95.0            # README: heatsink ≈ 95 °C at the board limit
T_J_AT_LIMIT_C = 110.0         # README: T_j ≈ 110 °C at the board limit
T_J_DERATE_C = 140.0           # README sensors: T_j (RT1 + model) 140 -> 160 °C
#: board copper (R30 MOS-terminal scenario at a uniform 100 °C) + coil links
#: (README: 8.3 W at 39.5 A, ∝ I²), W per A_rms²
K_BOARD_CU = 0.00550864 * 1.3144 + 8.3 / 39.5 ** 2
K_BOARD_CU_SOURCE = ("R30: 0.00551·I² W at 20 °C ×1.3144 (100 °C) + coil links 8.3 W at "
                     "39.5 A (README) — board copper, labelled scenario")

SI_DRIVE = {"v_gs_on_V": 12.0, "v_gs_off_V": 0.0, "pull_up_ohm": 1.3, "pull_down_ohm": 0.85,
            "l_sigma_nH": 3.0, "driver": "UCC27289 (12 V)"}
GAN_DRIVE = {"v_gs_on_V": 5.0, "v_gs_off_V": 0.0, "r_g_ohm": 5.1, "l_sigma_nH": 3.0,
             "driver": "5 V gate driver (reference board UG074356: 2EDL5014AA, R_G,on 5.1 ohm)",
             "dead_time_source": "default pending owner: 20 ns, a typical CoolGaN half-bridge "
                                 "dead time; UG074356 pages read do not state it"}


def hdf_svpwm(m: float) -> float:
    """SVPWM harmonic-distortion factor (Hava, Kerkman & Lipo, IEEE TPEL 1999;
    Holtz 1994) in their modulation index M = V1 / (2·V_dc/π) = (π/4)·m, with
    m = V1_peak / (V_dc/2) as used here; ripple current rms² ∝ HDF·(V_dc/(L·f_sw))².
    Only ratios of HDF are used (same carrier, bus and inductance)."""
    M = 0.25 * math.pi * max(float(m), 0.0)
    return 1.5 * M ** 2 - (4.0 * math.sqrt(3.0) / math.pi) * M ** 3 \
        + (27.0 / 16.0 - 81.0 * math.sqrt(3.0) / (64.0 * math.pi)) * M ** 4


def drive_for(card, r_g_si: Optional[float]) -> Dict[str, Any]:
    """Gate-drive request for a card: Si = the controller's UCC27289 build
    (SPICE set R_G + driver resistance), GaN = the reference-board drive."""
    tech = str(card.doc.get("technology") or "").lower()
    if tech.startswith("gan"):
        return {"tech": "GaN", "v_gs_on": GAN_DRIVE["v_gs_on_V"], "v_gs_off": 0.0,
                "r_g": GAN_DRIVE["r_g_ohm"], "r_g_off": None, "l_sigma": None,
                "source": "datasheet", "label": "GaN: datasheet times-and-charges + E_oss "
                "(no E_on/E_off table, no public SPICE model)"}
    rg = float(r_g_si)
    src = "spice" if card.switching_table() is not None else "datasheet"
    return {"tech": "Si", "v_gs_on": SI_DRIVE["v_gs_on_V"], "v_gs_off": 0.0,
            "r_g": rg + SI_DRIVE["pull_up_ohm"], "r_g_off": rg + SI_DRIVE["pull_down_ohm"],
            "l_sigma": SI_DRIVE["l_sigma_nH"], "source": src,
            "label": ("Si: vendor SPICE double-pulse table (ngspice, our driver set)"
                      if src == "spice" else "Si: datasheet times-and-charges (no SPICE table)")}


def inverter_losses(card, drv: Mapping[str, Any], *, I_rms: float, f_sw: float,
                    dead_s: float, v_dc: float, t_j: float, n_par: int = 1) -> Dict[str, Any]:
    """Three-leg bridge losses at one current and junction temperature."""
    from motor_ai_sim.inverter.losses import _leg_losses
    th = np.linspace(0.0, 2.0 * math.pi, 720, endpoint=False)
    i = SQ2 * float(I_rms) * np.sin(th)
    r = _leg_losses(card=card, i_leg=i, n_par=n_par, f_sw=f_sw, t_j_c=t_j, v_dc=v_dc,
                    v_gs_on=drv["v_gs_on"], v_gs_off=drv["v_gs_off"], r_g=drv["r_g"],
                    dead_time_s=dead_s, e_oss_policy="included_in_eon",
                    switching_source=drv["source"], r_g_off=drv["r_g_off"],
                    l_sigma_nH=drv["l_sigma"])
    cond = 3.0 * r["p_conduction_W"]
    sw = 3.0 * (r["p_switching_W"] + r["p_e_oss_W"])
    dead = 3.0 * r["p_third_quadrant_W"]
    return {"cond": cond, "sw": sw, "dead": dead, "total": cond + sw + dead,
            "per_switch_W": r["p_total_W"] / 2.0, "notes": r["notes"],
            "switching_basis": r.get("conduction_source"), "extrapolated": r["extrapolated"]}


class Board:
    """One-node board model calibrated on the Si build (see module doc).

    ``n_par`` devices per switch on a board ``scale`` times the calibrated
    one-device board (Ø85: the Ø40 12S board scaled ×2 for two devices in
    parallel — an ESTIMATE, no such board exists): the heat budget Q* scales
    by ``scale`` (R_sys by 1/scale, the same heatsink temperature rise at
    scale × the heat), each device keeps its own R_j-hs, the board copper law
    is unchanged (stated)."""

    def __init__(self, si_card, si_drv, *, build: str, v_dc: float, f_sw: float, dead_s: float,
                 n_par: int = 1, scale: float = 1.0):
        self.build = build
        self.n_par = int(n_par)
        self.scale = float(scale)
        rt = RATINGS[build]
        self.classes = {}
        p_fav = None
        for cls, W in rt["W"].items():
            I_r = rt["I_fav"] * W / rt["W"]["favourable"]
            inv = inverter_losses(si_card, si_drv, I_rms=I_r, f_sw=f_sw, dead_s=dead_s,
                                  v_dc=v_dc, t_j=T_J_AT_LIMIT_C)
            Q = (inv["total"] + K_BOARD_CU * I_r ** 2) * self.scale
            self.classes[cls] = {"I_rating_si_A": I_r, "P_rating_W": W, "Q_budget_W": Q,
                                 "R_sys_K_per_W": (T_HS_LIMIT_C - T_AMB_C) / Q,
                                 "calibration": "one-device Si board at %.2f A" % I_r
                                 + (" × %g (scaled)" % self.scale if self.scale != 1 else "")}
            if cls == "favourable":
                p_fav = inv["per_switch_W"]
        self.R_jhs = (T_J_AT_LIMIT_C - T_HS_LIMIT_C) / max(p_fav, 1e-9)

    def state(self, card, drv, *, I_rms, f_sw, dead_s, v_dc, cls="favourable"):
        """Steady state at one current.  A current beyond the board's
        continuous limit has no steady state on this board (the one-node
        model runs away): its losses are then evaluated at the controller's
        design T_j (110 °C, the board-limit junction temperature) and the
        point is flagged ``continuous_ok: false``."""
        c = self.classes[cls]
        t_j = T_J_AT_LIMIT_C
        for _ in range(8):
            inv = inverter_losses(card, drv, I_rms=I_rms, f_sw=f_sw, dead_s=dead_s,
                                  v_dc=v_dc, t_j=t_j, n_par=self.n_par)
            Q = inv["total"] + K_BOARD_CU * I_rms ** 2
            t_hs = T_AMB_C + c["R_sys_K_per_W"] * Q
            t_new = t_hs + self.R_jhs * inv["per_switch_W"] / self.n_par
            if abs(t_new - t_j) < 0.05:
                t_j = t_new
                break
            t_j = t_new
        board_ok = Q <= c["Q_budget_W"] + 1e-9
        tj_ok = t_j <= T_J_DERATE_C
        basis = "steady state, board model (%s airflow)" % cls
        if not (board_ok and tj_ok):
            inv = inverter_losses(card, drv, I_rms=I_rms, f_sw=f_sw, dead_s=dead_s,
                                  v_dc=v_dc, t_j=T_J_AT_LIMIT_C, n_par=self.n_par)
            Q = inv["total"] + K_BOARD_CU * I_rms ** 2
            t_j, t_hs = T_J_AT_LIMIT_C, None
            basis = ("beyond the board's continuous limit (no steady state): losses at the "
                     "controller's design T_j %.0f °C" % T_J_AT_LIMIT_C)
        return {"inv": inv, "Q_W": Q, "t_hs_C": t_hs, "t_j_C": t_j, "tj_basis": basis,
                "board_ok": board_ok, "tj_ok": tj_ok, "continuous_ok": board_ok and tj_ok}

    def i_limit(self, card, drv, *, f_sw, dead_s, v_dc, cls="favourable", I_hi=200.0):
        lo, hi = 0.0, float(I_hi)
        for _ in range(40):
            mid = 0.5 * (lo + hi)
            s = self.state(card, drv, I_rms=mid, f_sw=f_sw, dead_s=dead_s, v_dc=v_dc, cls=cls)
            if s["board_ok"] and s["tj_ok"]:
                lo = mid
            else:
                hi = mid
        return lo

    def describe(self) -> Dict[str, Any]:
        return {"model": "one-node board: T_hs = T_amb + R_sys·(P_inverter + P_board_cu); "
                         "T_j = T_hs + R_j-hs·P_switch; board limit = the Si build's heat "
                         "budget at its rating (per cooling class)",
                "T_amb_C": T_AMB_C, "T_hs_limit_C": T_HS_LIMIT_C,
                "T_j_at_limit_C": T_J_AT_LIMIT_C, "T_j_derate_C": T_J_DERATE_C,
                "R_jhs_K_per_W": self.R_jhs, "K_board_cu_W_per_A2": K_BOARD_CU,
                "devices_per_switch": self.n_par, "board_scale": self.scale,
                "scaling": (None if self.scale == 1 else
                            "Ø40 controller board model scaled ×%g devices — estimate, no "
                            "board of this size exists: heat budget ×%g, R_sys ÷%g, R_j-hs "
                            "per device, board copper law unchanged" % (
                                self.scale, self.scale, self.scale)),
                "K_board_cu_source": K_BOARD_CU_SOURCE, "classes": self.classes,
                "source": CTRL_SOURCE, "labels": ["estimate", "default pending owner"]}


# ─────────────────────────────────────────────────────────────────────────────
#  the operating points
# ─────────────────────────────────────────────────────────────────────────────

def grid_axes(rec, snap) -> Dict[str, Any]:
    """The (speed, current) grid every variant is computed on: the loss-grid
    speeds + the peak duty's speed, × the loss-grid currents (the static map's
    levels up to the demag limit, incl. the peak row).  A full grid — Configure
    reads it bilinearly and refuses a gap."""
    lg = rec["loss_grid"]["plan"]
    n0 = float(snap["rated_duty"]["rpm"])
    pk = snap.get("peak_duty")
    n_pk = float(pk["rpm"]) if pk else n0
    speeds = sorted(set(float(n) for n in lg["speeds"]) | {n_pk})
    currents = sorted(set(round(float(I), 6) for I in lg["currents"]))
    return {"speeds": speeds, "currents": currents, "n_pk": n_pk}


def _fw_reason(mode: str) -> str:
    return ("this current cannot meet the voltage limit (m·V_dc/√3, v_nom) at this speed "
            "inside the characterised field weakening (γ ≤ 80°): " + str(mode))


def operating_points(rec, snap, hm: PM.PsiMap) -> List[Dict[str, Any]]:
    """Every grid node: (n, I) at its operating angle on the m-limited
    trajectory; infeasible nodes are kept with the reason."""
    lg = rec["loss_grid"]["plan"]
    R_hot, vlim = float(lg["R_hot_ohm"]), float(lg["v_phase_limit_V"])
    I0 = float(snap["rated_duty"]["current_arms"])
    n0 = float(snap["rated_duty"]["rpm"])
    I_pk = float(lg["I_peak_rms"])
    ax = grid_axes(rec, snap)
    pk = snap.get("peak_duty")
    out = []
    for n in ax["speeds"]:
        for I in ax["currents"]:
            g, how = hm.operating_gamma(I, n, R_hot, vlim)
            duty = None
            if abs(I - I0) < 1e-6 and abs(n - n0) < 1e-6:
                duty = snap["rated_duty"].get("name") or "rated"
            if abs(I - I_pk) < 1e-6 and abs(n - ax["n_pk"]) < 1e-6:
                duty = (pk.get("name") if pk else None) or "peak (I_peak at rated speed)"
            out.append({"key": "%.0frpm_%.4gA" % (n, I), "rpm": n, "I_A": I,
                        "duty": duty, "gamma_deg": g, "gamma_mode": how})
    return out


def k_state_at(grid: Sequence[Mapping[str, Any]], n: float, I: float) -> Optional[float]:
    pts = [g for g in grid if g.get("k_state") is not None]
    if not pts:
        return None
    Irow = min({g["I_rms"] for g in pts}, key=lambda x: abs(x - I))
    row = sorted((g["rpm"], g["k_state"]) for g in pts if abs(g["I_rms"] - Irow) < 1e-9)
    return float(np.interp(n, [r[0] for r in row], [r[1] for r in row]))


def motor_point(rec, snap, hm, rows_grid, p) -> Dict[str, Any]:
    """Sine-drive motor numbers at one point (operating torque, losses)."""
    lg = rec["loss_grid"]
    R_hot = float(lg["plan"]["R_hot_ohm"])
    n, I, g = p["rpm"], p["I_A"], p["gamma_deg"]
    o = hm.at_Ig(I, g)
    k = k_state_at(lg["points"], n, I)
    T = o["T"] * (k or 1.0)
    il = LS.interp_loss(n, I, rows_grid, R_hot)
    mech = LS.mech_losses(rpm=n, bearings=snap["bearings"], geometry=snap["geometry"],
                          temp_c=None)
    Pm = float(mech.get("P_W") or 0.0)
    w = 2.0 * math.pi * n / 60.0
    v = hm.v_phase(I, g, n, R_hot)
    return {"T_map_Nm": o["T"], "k_state": k, "T_op_Nm": T, "P_em_W": il.get("P_total_W"),
            "P_mech_W": Pm, "P_shaft_W": T * w - Pm, "V_phase_peak_V": v,
            "loss_notes": il.get("notes")}


# ─────────────────────────────────────────────────────────────────────────────
#  the motor PWM extra loss from the FEM anchors
# ─────────────────────────────────────────────────────────────────────────────

class PwmModel:
    """dP_harm(n, I) from a carrier class's FEM anchors.

    Each anchor gives k_a = dP_a / HDF(m_a) — the ripple loss per unit SVPWM
    harmonic-distortion factor at its own modulation index m_a.  The FEM shows
    k depends on m, not on the current (Ø40, 13 000 rpm, MTPA: 5.3 W at 0.5·I0
    and at I0, m 0.90 / 0.93), and rises at low m where the dead-time
    distortion is not in HDF (2.5·I0 at γ = 80°, m 0.30: k ×2.3).  So k is
    linear in m between the anchors (held outside, flagged) and
    dP(n, I) = k(m) · HDF(m(n, I)).  Exact at every anchor."""

    def __init__(self, anchors: Sequence[Mapping[str, Any]], scale: float = 1.0,
                 derived: Optional[str] = None):
        a = [x for x in anchors if x.get("dP_harm_W") is not None and x.get("m_index")]
        self.anchors = sorted(a, key=lambda x: float(x["m_index"]))
        self.scale = float(scale)
        self.derived = derived
        km: Dict[float, List[float]] = {}
        for x in self.anchors:          # anchors at the same m are averaged
            m = round(float(x["m_index"]), 3)
            km.setdefault(m, []).append(float(x["dP_harm_W"]) * self.scale
                                        / max(hdf_svpwm(float(x["m_index"])), 1e-12))
        self.k = sorted((m, sum(v) / len(v)) for m, v in km.items())

    def __bool__(self) -> bool:
        return bool(self.anchors)

    def at(self, n: float, I: float, m_p: float):
        for x in self.anchors:
            if abs(float(x["rpm"]) - n) < 1e-6 and abs(float(x["I"]) - I) < 1e-6:
                v = float(x["dP_harm_W"]) * self.scale
                return v, (self.derived or "FEM") + " (%s)" % x.get("tag"), not self.derived
        ms = [k[0] for k in self.k]
        ks = [k[1] for k in self.k]
        held = m_p < ms[0] - 1e-6 or m_p > ms[-1] + 1e-6
        km = float(np.interp(m_p, ms, ks))
        how = ("%sFEM anchors (%s A, m %s): k = dP/HDF(m) linear in m%s, × HDF(m %.3f)" % (
            (self.derived + "; ") if self.derived else "",
            "/".join("%.1f" % float(x["I"]) for x in self.anchors),
            "/".join("%.3f" % m for m in ms),
            " (HELD at the nearest anchor m — outside the FEM range)" if held else "",
            m_p))
        return km * hdf_svpwm(m_p), how, False


# ─────────────────────────────────────────────────────────────────────────────
#  variants
# ─────────────────────────────────────────────────────────────────────────────

def build_variants(*, machine: str, rec, snap, hm, rows_grid, pwm_fem: Mapping[str, Any],
                   variants: Sequence[Mapping[str, Any]], r_g_si: float, build: str,
                   si_part: str, n_par: int = 1, board_scale: float = 1.0) -> Dict[str, Any]:
    """``pwm_variants`` + the controller / system-limit block of one machine.

    ``pwm_fem``: {class_id: [anchor, ...]} from the PWM FEM task (anchor =
    {tag, rpm, I, dP_harm_W, m_index}).
    ``variants``: [{id, device, carrier_hz, dead_time_s, fem_class}]."""
    from motor_ai_sim.inverter.devices import get_device
    bat = snap["battery"]
    v_dc = float(bat["v_nom"])
    lg = rec["loss_grid"]["plan"]
    m_max = float(lg.get("m") or 0.89)
    R_hot, vlim = float(lg["R_hot_ohm"]), float(lg["v_phase_limit_V"])
    I0 = float(snap["rated_duty"]["current_arms"])
    si = get_device(si_part)
    si_drv = drive_for(si, r_g_si)
    board = Board(si, si_drv, build=build, v_dc=v_dc, f_sw=48e3, dead_s=100e-9,
                  n_par=n_par, scale=board_scale)
    pts = operating_points(rec, snap, hm)
    mp = {p["key"]: (motor_point(rec, snap, hm, rows_grid, p) if p["gamma_deg"] is not None
                     else None) for p in pts}
    g = snap["geometry"]
    w = snap["winding"]
    ss = (snap["rated_duty"].get("stored_summary") or {})
    fingerprint = {
        "turns_per_coil": ss.get("turns_per_coil"),
        "wires_per_slot": g.get("num_wires_per_slot"),
        "coils_per_phase": w.get("n_coils_per_phase"),
        "length_mm": float(g["motor_length"]),
        "wire": {"width_mm": g.get("wire_width"), "height_mm": g.get("wire_height"),
                 "split": g.get("wire_split"), "parallel": g.get("wire_parallel")},
        "connection": "%s %s" % (w.get("connection"), w.get("star_delta")),
        "geometry_sig": (snap.get("signatures") or {}).get("geometry_sig"),
        "snapshot_sha256": snap.get("snapshot_sha256"),
    }
    cont_cache: Dict[Any, Any] = {}

    def p_cont(n: float, I_cap: float):
        key = (round(n, 3), round(I_cap, 4))
        if key not in cont_cache:
            mt = hm.max_torque_at(n, R_hot, vlim, I_cap)
            if mt.get("T") is None or mt["T"] <= 0:
                cont_cache[key] = (None, "no continuous operation at this speed: at ≤ %.1f A "
                                         "the voltage limit (v_nom, m %g) needs γ > 80° — "
                                         "beyond the characterised field weakening"
                                   % (I_cap, m_max))
            else:
                mpc = motor_point(rec, snap, hm, rows_grid,
                                  {"rpm": n, "I_A": mt["I"], "gamma_deg": mt["gamma"]})
                cont_cache[key] = (mpc["P_shaft_W"], "max shaft power at I ≤ %.1f A on the "
                                   "m-limited trajectory (%.1f A, γ %.1f°)"
                                   % (I_cap, mt["I"], mt["gamma"]))
        return cont_cache[key]

    out_variants = []
    for v in variants:
        card = get_device(v["device"])
        drv = drive_for(card, r_g_si)
        if v_dc * 1.0 > card.v_dss_V or float(bat["v_max"]) > 0.9 * card.v_dss_V:
            out_variants.append({"id": v["id"], "device": card.part, "status":
                                 "refused: bus v_max %.1f V > 90 %% of V_DSS %g V" % (
                                     float(bat["v_max"]), card.v_dss_V)})
            continue
        f_sw, dead = float(v["carrier_hz"]), float(v["dead_time_s"])
        model = PwmModel(pwm_fem.get(v["fem_class"]) or [])
        if not model and abs(f_sw - 48e3) < 480 and pwm_fem.get("si48_100ns")                 and v["fem_class"] != "si48_100ns":
            # Same carrier, bus and modulation; only the dead time differs (20 vs 100 ns).
            # Ø40 FEM: GaN 48 kHz 20 ns and Si 48 kHz 100 ns gave the same motor PWM
            # loss (5.3 / 5.3 W L12, 12.3 / 12.3 W L20) — borrowed, labelled.
            model = PwmModel(pwm_fem["si48_100ns"], scale=1.0, derived=(
                "BORROWED, not FEM at this dead time: the Si 48 kHz 100 ns FEM anchors "
                "(same carrier, bus, modulation; Ø40 FEM showed 20 vs 100 ns dead time "
                "changes the motor PWM loss by < 0.1 W)"))
        if not model and f_sw > 48e3 * 1.01:
            # No FEM at this carrier (100 kHz: ~(steps/period)² cost, 5–7 h per
            # run — see docs/BUG_PWM_100K_COST_2026-10-05.md).
            # Ripple current ∝ 1/f_sw; the ripple-driven loss falls between
            # ∝ 1/f² and ∝ 1/f: the card takes (48k/f)^1.5 of the same
            # technology's 48 kHz FEM anchors and states the range.
            base_cls = next((c for c in ("gan48_20ns", "si48_100ns") if pwm_fem.get(c)), None)
            if base_cls:
                s = (48e3 / f_sw) ** 1.5
                model = PwmModel(pwm_fem[base_cls], scale=s, derived=(
                    "ESTIMATE, not FEM: %s × (48 kHz/%.0f kHz)^1.5 = ×%.3f (range ×%.3f…×%.3f)"
                    % (base_cls, f_sw / 1e3, s, (48e3 / f_sw) ** 2, 48e3 / f_sw)))
        I_lim = {cls: board.i_limit(card, drv, f_sw=f_sw, dead_s=dead, v_dc=v_dc, cls=cls)
                 for cls in board.classes}
        points: Dict[str, Any] = {}
        cov = {"fem": [], "scaled": [], "infeasible": [], "no_cont": []}
        for p in pts:
            m_pt = mp.get(p["key"])
            base = {"rpm": p["rpm"], "I_A": p["I_A"], "duty": p["duty"],
                    "gamma_deg": p["gamma_deg"]}
            if m_pt is None:
                cov["infeasible"].append(p["key"])
                points[p["key"]] = dict(base, status="infeasible at m = %g: %s"
                                        % (m_max, _fw_reason(p["gamma_mode"])))
                continue
            if m_pt["P_em_W"] is None:
                cov["infeasible"].append(p["key"])
                points[p["key"]] = dict(base, gamma_mode=p["gamma_mode"],
                                        status="motor loss not available: "
                                        + "; ".join(m_pt.get("loss_notes") or []))
                continue
            m_p = m_pt["V_phase_peak_V"] / (v_dc / 2.0)
            dP, how = None, None
            if model:
                dP, how, is_fem = model.at(p["rpm"], p["I_A"], m_p)
                (cov["fem"] if is_fem else cov["scaled"]).append(p["key"])
            st = board.state(card, drv, I_rms=p["I_A"], f_sw=f_sw, dead_s=dead, v_dc=v_dc)
            inv = st["inv"]
            P_sh = m_pt["P_shaft_W"]
            P_em = float(m_pt["P_em_W"])
            P_motor = P_em + m_pt["P_mech_W"] + (dP or 0.0)
            P_board = K_BOARD_CU * p["I_A"] ** 2
            P_bat = P_sh + P_motor + inv["total"] + P_board
            P_cont_cls, cont_basis = {}, {}
            for cls, Il in I_lim.items():
                P_cont_cls[cls], cont_basis[cls] = p_cont(p["rpm"], min(Il, I0))
            P_cont = P_cont_cls.get("favourable")
            row = dict(base, **{
                "gamma_mode": p["gamma_mode"],
                "motor_pwm_loss_W": dP, "motor_pwm_loss_basis": how,
                "inverter_loss_W": {"cond": inv["cond"], "sw": inv["sw"], "dead": inv["dead"]},
                "board_copper_W": P_board,
                "tj_C": st["t_j_C"], "t_heatsink_C": st["t_hs_C"],
                "eta_drive_pct": 100.0 * P_sh / P_bat if P_bat > 0 and P_sh > 0 else None,
                "eta_shaft_pct": 100.0 * P_sh / (P_sh + P_motor) if P_sh > 0 else None,
                "p_cont_max_W": P_cont, "p_cont_max_W_by_cooling": P_cont_cls,
                "p_cont_max_basis": ("min(board-limit current %.1f A [favourable airflow], "
                                     "motor rated %.2f A): %s"
                                     % (I_lim["favourable"], I0, cont_basis["favourable"])),
                "P_shaft_W": P_sh, "T_op_Nm": m_pt["T_op_Nm"], "P_motor_sine_W": P_em,
                "P_mech_W": m_pt["P_mech_W"], "P_battery_W": P_bat,
                "board_ok": st["board_ok"], "tj_ok": st["tj_ok"],
                "continuous_ok": st["continuous_ok"], "tj_basis": st["tj_basis"],
            })
            if P_cont is None:
                row["p_cont_max_status"] = cont_basis["favourable"]
                cov["no_cont"].append(p["key"])
            points[p["key"]] = row
        ax = grid_axes(rec, snap)
        out_variants.append({
            "id": v["id"], "device": card.part, "technology": drv["tech"],
            "carrier_hz": f_sw, "dead_time_s": dead, "modulation": "SVPWM centred",
            "m_max": m_max, "n_parallel": int(n_par),
            "bus_v": {"min": float(bat["v_min"]), "nom": v_dc, "max": float(bat["v_max"])},
            "bus_evaluated": "nom",
            "build": fingerprint,
            "drive": {k: drv[k] for k in ("v_gs_on", "v_gs_off", "r_g", "r_g_off", "l_sigma")},
            "grid": {"rpm": ax["speeds"], "I_A": ax["currents"],
                     "note": "full (rpm × I_A) grid; infeasible nodes carry `status`"},
            "provenance": {
                "motor_pwm": ("2-D FEM, drive=inverter (centred SVPWM, device drop + dead time "
                              "in the circuit) vs sine reference at the extracted fundamental, "
                              "same mesh/steps, both marched (harm_ref); FEM class %s"
                              % v["fem_class"]),
                "motor_pwm_fem_runs": [x.get("tag") for x in model.anchors] if model else [],
                "inverter": drv["label"],
                "thermal": "board model (estimate, calibrated on the controller project's Si "
                           "ratings) — see controller.board",
                "motor_sine": "passport loss trajectory (m = %g) + analytic mech" % m_max,
                "labels": ["2-D", "PWM", "default pending owner"] + (
                    ["GaN dead time 20 ns: default pending owner"] if drv["tech"] == "GaN" else []) + (
                    ["motor PWM loss: ESTIMATE from the 48 kHz FEM (no FEM at this carrier)"]
                    if model and model.derived else []) + (
                    ["motor PWM extra loss NOT included: no motor-side PWM FEM anchor for this "
                     "machine (motor_pwm_loss_W is null; drive efficiency = sine motor + "
                     "inverter + board copper)"] if not model else []) + (
                    ["GaN switching: datasheet model (no public SPICE model)"]
                    if drv["tech"] == "GaN" else ["Si switching: vendor SPICE table"]),
            },
            "i_board_limit_A": I_lim,
            "coverage": {"points": len(points), "fem_points": cov["fem"],
                         "scaled_points": cov["scaled"], "infeasible": cov["infeasible"],
                         "no_continuous": cov["no_cont"],
                         "statement": "every loss-grid speed + the peak duty speed × every "
                                      "loss-grid current (static levels up to the demag limit "
                                      "+ peak), nominal bus; motor PWM loss FEM at the anchor "
                                      "points, k = dP/HDF(m) interpolated in I and HDF-scaled "
                                      "elsewhere"},
            "points": points,
        })
    return {"pwm_variants": out_variants, "board": board.describe(),
            "points": pts, "motor_points": mp}
