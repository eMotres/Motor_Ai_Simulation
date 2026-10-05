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
    """One-node board model calibrated on the Si build (see module doc)."""

    def __init__(self, si_card, si_drv, *, build: str, v_dc: float, f_sw: float, dead_s: float):
        self.build = build
        rt = RATINGS[build]
        self.classes = {}
        p_fav = None
        for cls, W in rt["W"].items():
            I_r = rt["I_fav"] * W / rt["W"]["favourable"]
            inv = inverter_losses(si_card, si_drv, I_rms=I_r, f_sw=f_sw, dead_s=dead_s,
                                  v_dc=v_dc, t_j=T_J_AT_LIMIT_C)
            Q = inv["total"] + K_BOARD_CU * I_r ** 2
            self.classes[cls] = {"I_rating_si_A": I_r, "P_rating_W": W, "Q_budget_W": Q,
                                 "R_sys_K_per_W": (T_HS_LIMIT_C - T_AMB_C) / Q}
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
                                  v_dc=v_dc, t_j=t_j)
            Q = inv["total"] + K_BOARD_CU * I_rms ** 2
            t_hs = T_AMB_C + c["R_sys_K_per_W"] * Q
            t_new = t_hs + self.R_jhs * inv["per_switch_W"]
            if abs(t_new - t_j) < 0.05:
                t_j = t_new
                break
            t_j = t_new
        board_ok = Q <= c["Q_budget_W"] + 1e-9
        tj_ok = t_j <= T_J_DERATE_C
        basis = "steady state, board model (%s airflow)" % cls
        if not (board_ok and tj_ok):
            inv = inverter_losses(card, drv, I_rms=I_rms, f_sw=f_sw, dead_s=dead_s,
                                  v_dc=v_dc, t_j=T_J_AT_LIMIT_C)
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
                "K_board_cu_source": K_BOARD_CU_SOURCE, "classes": self.classes,
                "source": CTRL_SOURCE, "labels": ["estimate", "default pending owner"]}


# ─────────────────────────────────────────────────────────────────────────────
#  the operating points
# ─────────────────────────────────────────────────────────────────────────────

def operating_points(rec, snap, hm: PM.PsiMap) -> List[Dict[str, Any]]:
    """Rated and peak current at every loss-grid speed (+ the peak duty's own
    speed), at the trajectory bus and m of the card; infeasible ones kept."""
    lg = rec["loss_grid"]["plan"]
    R_hot, vlim = float(lg["R_hot_ohm"]), float(lg["v_phase_limit_V"])
    I0 = float(snap["rated_duty"]["current_arms"])
    n0 = float(snap["rated_duty"]["rpm"])
    I_pk = float(lg["I_peak_rms"])
    pk = snap.get("peak_duty")
    n_pk = float(pk["rpm"]) if pk else n0
    speeds = sorted(set(float(n) for n in lg["speeds"]) | {n_pk})
    out = []
    for n in speeds:
        for which, I in (("rated", I0), ("peak", I_pk)):
            g, how = hm.operating_gamma(I, n, R_hot, vlim)
            duty = None
            if which == "rated" and abs(n - n0) < 1e-6:
                duty = snap["rated_duty"].get("name") or "rated"
            if which == "peak" and abs(n - n_pk) < 1e-6:
                duty = (pk.get("name") if pk else None) or "peak (I_peak at rated speed)"
            out.append({"key": "%.0frpm_%s" % (n, which), "rpm": n, "I_A": I,
                        "row": which, "duty": duty, "gamma_deg": g, "gamma_mode": how})
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
#  variants
# ─────────────────────────────────────────────────────────────────────────────

def build_variants(*, machine: str, rec, snap, hm, rows_grid, pwm_fem: Mapping[str, Any],
                   variants: Sequence[Mapping[str, Any]], r_g_si: float, build: str,
                   si_part: str) -> Dict[str, Any]:
    """``pwm_variants`` + the controller / system-limit block of one machine.

    ``pwm_fem``: {class_id: {"rated": rec, "peak": rec}} from the PWM FEM task.
    ``variants``: [{id, device, carrier_hz, dead_time_s, fem_class}]."""
    from motor_ai_sim.inverter.devices import get_device
    bat = snap["battery"]
    v_dc = float(bat["v_nom"])
    m_max = float(rec["loss_grid"]["plan"]["m"]) if rec["loss_grid"]["plan"].get("m") else 0.89
    I0 = float(snap["rated_duty"]["current_arms"])
    si = get_device(si_part)
    si_drv = drive_for(si, r_g_si)
    board = Board(si, si_drv, build=build, v_dc=v_dc, f_sw=48e3, dead_s=100e-9)
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
    out_variants = []
    for v in variants:
        card = get_device(v["device"])
        drv = drive_for(card, r_g_si)
        if v_dc * 1.0 > card.v_dss_V or float(bat["v_max"]) > 0.9 * card.v_dss_V:
            out_variants.append({"id": v["id"], "device": card.part, "status":
                                 "refused: bus v_max %.1f V > 90 %% of V_DSS %g V" % (
                                     float(bat["v_max"]), card.v_dss_V)})
            continue
        fem = pwm_fem.get(v["fem_class"]) or {}
        f_sw, dead = float(v["carrier_hz"]), float(v["dead_time_s"])
        I_lim = {cls: board.i_limit(card, drv, f_sw=f_sw, dead_s=dead, v_dc=v_dc, cls=cls)
                 for cls in board.classes}
        points: Dict[str, Any] = {}
        cov = {"fem": [], "scaled": [], "infeasible": []}
        for p in pts:
            m_pt = mp.get(p["key"])
            if m_pt is None:
                cov["infeasible"].append(p["key"])
                points[p["key"]] = {"rpm": p["rpm"], "I_A": p["I_A"], "duty": p["duty"],
                                    "gamma_deg": None, "status": "infeasible at m = %g: %s"
                                    % (m_max, p["gamma_mode"])}
                continue
            anchor = fem.get(p["row"]) or {}
            dP, how = None, None
            if anchor.get("dP_harm_W") is not None:
                a_n = float(anchor["rpm"])
                m_a = float(anchor["m_index"])
                m_p = m_pt["V_phase_peak_V"] / (v_dc / 2.0)
                if abs(a_n - p["rpm"]) < 1e-6:
                    dP, how = float(anchor["dP_harm_W"]), "FEM"
                    cov["fem"].append(p["key"])
                else:
                    dP = float(anchor["dP_harm_W"]) * hdf_svpwm(m_p) / max(hdf_svpwm(m_a), 1e-12)
                    how = "FEM anchor at %.0f rpm × HDF(m %.3f)/HDF(m %.3f)" % (a_n, m_p, m_a)
                    cov["scaled"].append(p["key"])
            st = board.state(card, drv, I_rms=p["I_A"], f_sw=f_sw, dead_s=dead, v_dc=v_dc)
            inv = st["inv"]
            P_sh = m_pt["P_shaft_W"]
            P_em = float(m_pt["P_em_W"] or 0.0)
            P_motor = P_em + m_pt["P_mech_W"] + (dP or 0.0)
            P_board = K_BOARD_CU * p["I_A"] ** 2
            P_bat = P_sh + P_motor + inv["total"] + P_board
            I_cont = min(I_lim["favourable"], I0)
            P_cont_cls = {}
            for cls, Il in I_lim.items():
                Ic = min(Il, I0)
                gc, _ = hm.operating_gamma(Ic, p["rpm"],
                                           float(rec["loss_grid"]["plan"]["R_hot_ohm"]),
                                           float(rec["loss_grid"]["plan"]["v_phase_limit_V"]))
                P_cont_cls[cls] = (None if gc is None else motor_point(
                    rec, snap, hm, rows_grid, {"rpm": p["rpm"], "I_A": Ic,
                                                "gamma_deg": gc})["P_shaft_W"])
            P_cont = P_cont_cls.get("favourable")
            points[p["key"]] = {
                "rpm": p["rpm"], "I_A": p["I_A"], "duty": p["duty"],
                "gamma_deg": p["gamma_deg"], "gamma_mode": p["gamma_mode"],
                "motor_pwm_loss_W": dP, "motor_pwm_loss_basis": how,
                "inverter_loss_W": {"cond": inv["cond"], "sw": inv["sw"], "dead": inv["dead"]},
                "board_copper_W": P_board,
                "tj_C": st["t_j_C"], "t_heatsink_C": st["t_hs_C"],
                "eta_drive_pct": 100.0 * P_sh / P_bat if P_bat > 0 else None,
                "eta_shaft_pct": 100.0 * P_sh / (P_sh + P_motor) if P_sh > 0 else None,
                "p_cont_max_W": P_cont, "p_cont_max_W_by_cooling": P_cont_cls,
                "p_cont_max_basis": ("shaft power at min(board-limit current %.1f A "
                                     "[favourable airflow], motor rated %.2f A) at this speed"
                                     % (I_lim["favourable"], I0)),
                "P_shaft_W": P_sh, "T_op_Nm": m_pt["T_op_Nm"], "P_motor_sine_W": P_em,
                "P_mech_W": m_pt["P_mech_W"], "P_battery_W": P_bat,
                "board_ok": st["board_ok"], "tj_ok": st["tj_ok"],
                "continuous_ok": st["continuous_ok"], "tj_basis": st["tj_basis"],
            }
        out_variants.append({
            "id": v["id"], "device": card.part, "technology": drv["tech"],
            "carrier_hz": f_sw, "dead_time_s": dead, "modulation": "SVPWM centred",
            "m_max": m_max, "n_parallel": 1,
            "bus_v": {"min": float(bat["v_min"]), "nom": v_dc, "max": float(bat["v_max"])},
            "bus_evaluated": "nom",
            "build": fingerprint,
            "drive": {k: drv[k] for k in ("v_gs_on", "v_gs_off", "r_g", "r_g_off", "l_sigma")},
            "provenance": {
                "motor_pwm": ("2-D FEM, drive=inverter (centred SVPWM, device drop + dead time "
                              "in the circuit) vs sine reference at the extracted fundamental, "
                              "same mesh/steps (harm_ref); FEM class %s" % v["fem_class"]),
                "motor_pwm_fem_runs": {k: (fem.get(k) or {}).get("tag") for k in ("rated", "peak")},
                "inverter": drv["label"],
                "thermal": "board model (estimate, calibrated on the controller project's Si "
                           "ratings) — see controller.board",
                "motor_sine": "passport loss trajectory (m = %g) + analytic mech" % m_max,
                "labels": ["2-D", "PWM", "default pending owner"] + (
                    ["GaN dead time 20 ns: default pending owner"] if drv["tech"] == "GaN" else []),
            },
            "i_board_limit_A": I_lim,
            "coverage": {"points": len(points), "fem_points": cov["fem"],
                         "scaled_points": cov["scaled"], "infeasible": cov["infeasible"],
                         "statement": "rated and peak current at every loss-grid speed + the "
                                      "peak duty speed, nominal bus; motor PWM loss FEM at "
                                      "the rated and peak points, HDF-scaled elsewhere"},
            "points": points,
        })
    return {"pwm_variants": out_variants, "board": board.describe(),
            "points": pts, "motor_points": mp}
