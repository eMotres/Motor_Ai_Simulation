"""The passport record (JSON, full precision) and its derived card values.

Built from the frozen snapshot (M0), the run records (``results.jsonl``) and
the stage state.  Every value carries its method, its provenance (the run ids
it comes from) and the stage-1 labels.  Blocks that later stages fill (3-D
factors, PWM, controller losses) are present and say "not computed in
stage 1".
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from motor_ai_sim.passport_v1 import (CONVENTION_REV, NOT_COMPUTED_STAGE1,
                                      PENDING_DEFAULTS, SCHEMA_VERSION,
                                      STAGE1_LABELS)
from motor_ai_sim.passport_v1 import losses as LS
from motor_ai_sim.passport_v1 import psimap as PM

#: Accuracy targets (owner 2026-09-30 / spec §1).
TOL_PSI_PCT = 0.5
TOL_T_PCT = 1.0
TOL_LOSS_PCT = 5.0
TOL_RIPPLE_PP = 0.5          # percentage points
TOL_RIPPLE_REL = 0.10

#: Audit 2026-09-30 numbers (C:\\Users\\vadim\\Downloads\\configure_audit_2026-09-30.md)
#: — the Ø40 base FEM at the OLD passport settings (I0 40.659 A, gamma 10°,
#: mesh 5 mm, 24 steps, coil 120 °C, card magnet temperature, k_end 2.19,
#: rotor eddy post-processed) and its duty-grade coupled AC matrix rows.
AUDIT = {
    "settings": "I 40.659 A, gamma 10°, 13 000 rpm, mesh 5 mm, 24 steps, coil 120 °C, "
                "magnet at card temperature (120 °C), k_end 2.19, rotor eddy post-processed",
    "base": {"T_Nm": 0.584, "EMF_ph_pk_V": 10.14, "V_line_V": 18.3, "P_cu_W": 62.5,
             "P_cu_dc_W": 58.4, "P_cu_ac_W": 4.0, "P_fe_W": 8.76, "P_mag_W": 2.08,
             "P_total_W": 73.3, "eta_pct": 91.6},
    "duty_grade": {  # coupled eddy, mesh 1 mm, 36 steps, demag on, coil 120 °C
        "base_13000": {"P_cu_ac_W": 3.88, "P_total_W": 74.3},
        "rpm19500": {"P_cu_ac_W": 8.49, "P_total_W": 90.9}},
    "mtpa_static": {  # static, demag off, card magnet temperature, I0 40.659 A
        0.5: (2.6, 0.2993), 1.0: (5.4, 0.5902), 1.5: (7.9, 0.8635), 2.0: (10.7, 1.104)},
}


def load_records(od: Path) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    fails: List[Dict[str, Any]] = []
    for ln in open(od / "results.jsonl", encoding="utf-8"):
        try:
            r = json.loads(ln)
        except Exception:                    # noqa: BLE001
            continue
        if r.get("ok"):
            out[r["id"]] = r
        else:
            fails.append({"id": r.get("id"), "error": r.get("error")})
    out["__failed__"] = {"list": fails}
    return out


def _r(recs, jid) -> Dict[str, Any]:
    return recs[jid]["r"]


def _pct(a: float, b: float) -> float:
    return 100.0 * (a - b) / b if b else float("nan")


def kv_rpm_per_V_line(psi_pm: float, pole_pairs: int) -> float:
    """No-load Kv per LINE peak volt from the FUNDAMENTAL flux (spec 8.7;
    PR #81: EMF via omega·|psi1|, not the sampled waveform peak)."""
    return 60.0 / (2.0 * math.pi * pole_pairs * PM.SQ3 * abs(psi_pm))


def _val(v, unit, method, src, labels=STAGE1_LABELS, **extra):
    d = {"value": v, "unit": unit, "method": method, "runs": src, "labels": list(labels)}
    d.update(extra)
    return d


# ─────────────────────────────────────────────────────────────────────────────
#  map + checks
# ─────────────────────────────────────────────────────────────────────────────

def _map(recs, st, set_name, mtpa_key, pp):
    # the calibration run (full period, cogging sampling) is not a map point:
    # the zero-current anchor of the map is the 60°-window `hot_I0_anchor`
    pts = PM.points_from_records([v for k, v in recs.items()
                                  if k not in ("__failed__", "hot_noload")], set_name)
    return pts, PM.PsiMap.build(pts, pp, mtpa=PM.mtpa_table(st[mtpa_key]))


def static_check_rows(recs, st, hm: PM.PsiMap, I0: float) -> List[Dict[str, Any]]:
    rows = []
    T_rated = max(m[2] for m in hm.mtpa if abs(m[0] - I0) < 1e-6) if hm.mtpa else 1.0
    for c in st["checks_plan"]["static"]:
        rec = recs.get(c["id"])
        if rec is None:
            rows.append({"id": c["id"], "status": "MISSING"})
            continue
        r = rec["r"]
        d, q = float(r["i_d_A"]), float(r["i_q_A"])
        p = hm.at(d, q)
        psi = math.hypot(float(r["psi_d_Wb"]), float(r["psi_q_Wb"]))
        e_pd = 100.0 * (p["psi_d"] - float(r["psi_d_Wb"])) / psi
        e_pq = 100.0 * (p["psi_q"] - float(r["psi_q_Wb"])) / psi
        T = float(r["T_avg_Nm"])
        floor = 0.0005 * T_rated
        e_T = 100.0 * (p["T"] - T) / T if abs(T) > floor else 0.0
        e_Tpsi = 100.0 * (p["T_psi"] - T) / T if abs(T) > floor else 0.0
        rip_fem = 100.0 * float(r.get("T_ripple_pp_Nm") or 0.0) / abs(T)
        rip_int = 100.0 * float(p["ripple_pp"]) / abs(p["T"]) if p["inside"] else float("nan")
        rip_tol = max(TOL_RIPPLE_PP, TOL_RIPPLE_REL * rip_fem)
        ok = (p["inside"] and max(abs(e_pd), abs(e_pq)) <= TOL_PSI_PCT
              and abs(e_T) <= TOL_T_PCT)
        rows.append({
            "id": c["id"], "role": c["role"], "I_rms": float(rec["kw"]["I_phase_rms"]),
            "gamma": float(rec["kw"]["gamma_deg"]), "i_d": d, "i_q": q,
            "inside_hull": p["inside"],
            "psi_d_fem": float(r["psi_d_Wb"]), "psi_d_int": p["psi_d"],
            "psi_q_fem": float(r["psi_q_Wb"]), "psi_q_int": p["psi_q"],
            "err_psi_d_pct_of_abs_psi": e_pd, "err_psi_q_pct_of_abs_psi": e_pq,
            "T_fem": T, "T_int": p["T"], "T_psi_int": p["T_psi"],
            "err_T_pct": e_T, "err_T_psi_pct": e_Tpsi,
            "ripple_fem_pct": rip_fem, "ripple_int_pct": rip_int,
            "err_ripple_pp": rip_int - rip_fem, "ripple_tol_pp": rip_tol,
            "ripple_ok": bool(abs(rip_int - rip_fem) <= rip_tol),
            "pass": bool(ok),
        })
    return rows


def _lp(st) -> Dict[str, Any]:
    """The loss trajectory the card uses: the controller-margin re-plan
    (``loss_plan2``, m = 0.89) when it exists, else the stage-1 plan."""
    return st.get("loss_plan2") or st["loss_plan"]


def loss_rows(recs, st) -> Dict[float, List[Dict[str, Any]]]:
    rows: Dict[float, List[Dict[str, Any]]] = {}
    for p in _lp(st)["points"]:
        if not p.get("id") or p["id"] not in recs:
            continue
        r = recs[p["id"]]["r"]
        row = {"rpm": float(p["rpm"]), "I": float(p["I"]), "gamma": p["gamma"]}
        for g in ("P_cu_dc_W",) + LS.GROUPS:
            row[g] = r.get(g)
        rows.setdefault(round(float(p["I"]), 6), []).append(row)
    return rows


def loss_point_block(recs, jid, mech: Mapping[str, Any]) -> Dict[str, Any]:
    rec = recs[jid]
    r = rec["r"]
    rpm = float(rec["kw"]["rpm"])
    P_em = float(r["P_loss_total_W"])
    Pm = mech.get("P_W") if mech else None
    eff = LS.efficiency_shaft(float(r["T_avg_Nm"]), rpm, P_em, float(Pm or 0.0))
    ds = r.get("demag_summary") or {}
    return {
        "id": jid, "rpm": rpm, "I_rms": float(rec["kw"]["I_phase_rms"]),
        "gamma": float(rec["kw"]["gamma_deg"]), "i_d": r.get("i_d_A"), "i_q": r.get("i_q_A"),
        "T_Nm": r.get("T_avg_Nm"), "T_ripple_pct": r.get("T_ripple_pct"),
        "torque_method": r.get("torque_method"),
        "groups_W": {g: r.get(g) for g in ("P_cu_dc_W", "P_cu_ac_W", "P_fe_stator_W",
                                           "P_fe_rotor_W", "P_mag_W", "P_shaft_W",
                                           "P_sleeve_W", "P_cu_circulating_W")},
        "P_fe_terms": r.get("P_fe_terms"),
        "P_loss_em_W": P_em, "P_mech_W": Pm, "efficiency": eff,
        "demag": {k: ds.get(k) for k in ("br_kept_vol_pct", "loss_pct", "bh_loss_pct",
                                         "per_magnet_spread_pct", "area_derated_pct")},
        "demag_corner": (ds.get("br_corner") or {}).get("br_pct"),
        "settle": {"eddy_method": r.get("eddy_method"), "eddy_settled": r.get("eddy_settled"),
                   "demag_settled": r.get("demag_settled"),
                   "steady_state": r.get("steady_state"), "qualified": r.get("qualified"),
                   "tdm_stop": r.get("tdm_stop"),
                   "tdm_orbit_error_estimate": r.get("tdm_orbit_error_estimate"),
                   "rule": "TDM periodic orbit (state residual), demag full pre-pass "
                           "with the owner's steadiness rule; spec 3.0.1 tail-bound rule "
                           "= proposal (owner decision pending)"},
        "gap_layers": r.get("gap_layers_effective"),
        "n_steps_per_period": r.get("n_steps_per_period"),
        "wall_s": rec.get("wall_s"),
    }


def _cu_alpha(snap) -> float:
    c = ((snap.get("material_cards") or {}).get("copper") or {}).get("card") or {}
    return float(c.get("thermal_alpha") or 0.0043)


def loss_check_rows(recs, st, R_hot: float, snap) -> List[Dict[str, Any]]:
    """Trajectory interpolation vs direct FEM.  A check solved at another
    winding temperature (a saved duty) is also compared after the analytic
    copper correction R(T) = R20·(1 + α(T − 20)) (spec 8.4): DC copper ×k,
    resistance-limited AC copper ×1/k; the magnet temperature is not
    corrected (stated)."""
    rows_grid = loss_rows(recs, st)
    Tc_hot = float(snap["temperatures"]["hot_coil_c"])
    Tm_hot = snap["temperatures"]["hot_magnet_c"]
    alpha = _cu_alpha(snap)
    out = []
    lp2 = st.get("loss_plan2")
    plan = list(lp2["checks"]) if (lp2 and lp2.get("checks")) else list(st["checks_plan"]["loss"])
    for jid in [k for k in recs if k.startswith(("audit_", "duty_"))] + ["lchk_rated_72steps"]:
        if jid in recs:
            m = recs[jid]["meta"]
            plan.append({"id": jid, "rpm": float(m["rpm"]), "I": float(m["I"]),
                         "gamma": float(m["gamma"]), "role": m.get("role")})
    for p in plan:
        if p["id"] not in recs:
            out.append({"id": p["id"], "status": "not solved (%s)" % p.get("mode")})
            continue
        r = recs[p["id"]]["r"]
        it = LS.interp_loss(float(p["rpm"]), float(p["I"]), rows_grid, R_hot)
        P_fem = float(r["P_loss_total_W"])
        row = {"id": p["id"], "role": p.get("role"), "rpm": p["rpm"], "I": p["I"],
               "gamma_fem": float(recs[p["id"]]["kw"]["gamma_deg"]),
               "P_fem_W": P_fem, "P_int_W": it.get("P_total_W"),
               "groups_fem": {g: r.get(g) for g in ("P_cu_dc_W",) + LS.GROUPS},
               "groups_int": it["groups"], "notes": it["notes"],
               "T_fem": r.get("T_avg_Nm")}
        kw = recs[p["id"]]["kw"]
        Tc = float(kw.get("coil_temp_c") or Tc_hot)
        Tm = kw.get("magnet_temp_c")
        row["coil_temp_c"], row["magnet_temp_c"] = Tc, Tm
        if it.get("P_total_W"):
            row["err_total_pct"] = _pct(float(it["P_total_W"]), P_fem)
            row["pass"] = bool(abs(row["err_total_pct"]) <= TOL_LOSS_PCT)
            if abs(Tc - Tc_hot) > 0.5:
                k = (1 + alpha * (Tc - 20.0)) / (1 + alpha * (Tc_hot - 20.0))
                g = dict(it["groups"])
                corr = (float(it["P_total_W"]) + g["P_cu_dc_W"] * (k - 1.0)
                        + float(g["P_cu_ac_W"] or 0.0) * (1.0 / k - 1.0))
                row["P_int_coil_corrected_W"] = corr
                row["err_total_coil_corrected_pct"] = _pct(corr, P_fem)
                row["pass"] = bool(abs(row["err_total_coil_corrected_pct"]) <= TOL_LOSS_PCT)
                row["temperature_note"] = (
                    "FEM at coil %.1f °C / magnet %s vs passport hot coil %.1f °C / magnet "
                    "%s: copper corrected analytically (k_R = %.4f), magnet temperature not "
                    "corrected" % (Tc, "card" if Tm is None else "%.1f °C" % Tm, Tc_hot,
                                   "%.1f °C" % Tm_hot, k))
        else:
            row["refused"] = True
            row["pass"] = None
        out.append(row)
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  the record
# ─────────────────────────────────────────────────────────────────────────────

def build_record(od: Path, snap: Mapping[str, Any], st: Mapping[str, Any], *,
                 machine_meta: Mapping[str, Any]) -> Dict[str, Any]:
    recs = load_records(od)
    pp = int(round(float(snap["geometry"]["num_poles"]))) // 2
    I0 = float(snap["rated_duty"]["current_arms"])
    n0 = float(snap["rated_duty"]["rpm"])
    T_hot_m, T_hot_c = snap["temperatures"]["hot_magnet_c"], snap["temperatures"]["hot_coil_c"]
    hot_pts, hm = _map(recs, st, "hot", "hot_mtpa", pp)
    lp = _lp(st)
    R_hot = float(lp["R_hot_ohm"])
    I_pk = float(lp["I_peak_rms"])

    # ── self-checks on the grid ───────────────────────────────────────────
    T_ref = max(abs(p["T"]) for p in hot_pts)
    dq_chk = [abs(float(recs[p["id"]]["r"].get("dq_torque_check_pct") or 0.0))
              for p in hot_pts if p["I"] > 0 and abs(p["T"]) > 0.01 * T_ref]
    recip = []
    for m in hm.mtpa:
        d, q = PM.id_iq(m[0], m[1] + 20.0)
        if hm.at(d, q)["inside"]:
            recip.append(hm.diff_L(d, q)["reciprocity_asym"])
    # ── cold block ────────────────────────────────────────────────────────
    cnl = _r(recs, "cold_noload")
    hnl = _r(recs, "hot_I0_anchor") if "hot_I0_anchor" in recs else _r(recs, "hot_noload")
    hnl_full = _r(recs, "hot_noload")
    psi_pm_c, psi_pm_h = float(cnl["psi_d_Wb"]), float(hnl["psi_d_Wb"])
    R20 = float(cnl["R_phase_ohm"])
    cold = {c["fI"]: c for c in st["cold_mtpa"]}
    c1 = cold[1.0]
    c025 = cold[0.25]
    rc1 = _r(recs, c1["conf_id"])
    T_c1 = float(rc1["T_avg_Nm"])
    T_c025 = float(_r(recs, c025["conf_id"])["T_avg_Nm"])
    small = _r(recs, "cold_probe2A")
    inc_small = small.get("inc_ldq") or {}
    ss = st.get("small_signal") or {}
    Ld_chord_c = ((float(rc1["psi_d_Wb"]) - psi_pm_c) / float(rc1["i_d_A"])
                  if abs(float(rc1["i_d_A"])) > 0.1 * math.hypot(float(rc1["i_d_A"]),
                                                                  float(rc1["i_q_A"]))
                  else None)
    Lq_chord_c = float(rc1["psi_q_Wb"]) / float(rc1["i_q_A"])
    # short circuit: psi_d(i_d) on the d-axis, quadratic fit -> root
    import numpy as np
    sc = sorted(((float(_r(recs, k)["i_d_A"]), float(_r(recs, k)["psi_d_Wb"]))
                 for k in ("cold_sc_0", "cold_sc_1", "cold_sc_2") if k in recs))
    I_char = None
    if len(sc) == 3:
        a2, a1, a0 = np.polyfit([s[0] for s in sc], [s[1] for s in sc], 2)
        roots = [x.real for x in np.roots([a2, a1, a0]) if abs(x.imag) < 1e-12]
        roots = [x for x in roots if min(s[0] for s in sc) * 1.3 <= x <= 0]
        if roots:
            I_char = abs(min(roots, key=lambda x: abs(x - sc[1][0]))) / PM.SQ2
    Ld_sc = ((sc[1][1] - psi_pm_c) / sc[1][0]) if sc else None
    sc_steady = (PM.short_circuit_steady(psi_pm_c, Ld_sc, Lq_chord_c, R20, n0, pp)
                 if Ld_sc else None)
    # cold factors per level (spec 8.1: k_psi on no-load and the MTPA line)
    k_levels = []
    for fI, c in sorted(cold.items()):
        hot_v = [m for m in st["hot_mtpa"] if abs(m["fI"] - fI) < 1e-9]
        T_h = (float(hot_v[0]["T_fem_vertex"]) if hot_v
               else hm.at_Ig(fI * I0, hm.gamma_mtpa(fI * I0))["T"])
        k_levels.append({"fI": fI, "gamma_cold": c["gamma_mtpa"],
                         "T_cold": float(_r(recs, c["conf_id"])["T_avg_Nm"]), "T_hot": T_h,
                         "k_T_cold_over_hot": float(_r(recs, c["conf_id"])["T_avg_Nm"]) / T_h})
    # ── hot constants ─────────────────────────────────────────────────────
    m1 = [m for m in st["hot_mtpa"] if abs(m["fI"] - 1.0) < 1e-9][0]
    rh1 = _r(recs, m1["conf_id"])
    inc_h = rh1.get("inc_ldq") or {}
    dl = hm.diff_L(float(rh1["i_d_A"]), float(rh1["i_q_A"]))
    # ── voltage / operating envelope (hot map) ────────────────────────────
    m = float(lp.get("m") or PENDING_DEFAULTS["voltage_margin_m"]["value"])
    env = {}
    for which in ("min", "nom", "max"):
        vdc = float(snap["battery"]["v_" + which])
        vl = PM.v_phase_limit(vdc, m)
        nb = hm.max_speed(R_hot, vl, I0, gamma_max=float(hm.gamma_mtpa(I0)) + 1e-9)
        env[which] = {
            "v_dc": vdc, "v_phase_limit_V": vl,
            "base_speed_rated_I_rpm": nb,
            "max_speed_I0_rpm": hm.max_speed(R_hot, vl, I0),
            "max_speed_Ipk_rpm": hm.max_speed(R_hot, vl, I_pk),
            "rated_point": dict(zip(("gamma", "mode"),
                                    hm.operating_gamma(I0, n0, R_hot, vl))),
            "torque_speed": [],
        }
        for f in (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0):
            n = f * n0
            mt = hm.max_torque_at(n, R_hot, vl, I_pk)
            env[which]["torque_speed"].append({"rpm": n, **mt})
    # ── loss grid ─────────────────────────────────────────────────────────
    mech = st.get("mech") or {}

    def mech_of(rpm):
        k = str(float(rpm)) if str(float(rpm)) in mech else None
        for kk in mech:
            if abs(float(kk) - float(rpm)) < 1e-6:
                k = kk
        if k is not None:
            return mech[k]
        return LS.mech_losses(rpm=rpm, bearings=snap["bearings"],
                              geometry=snap["geometry"], temp_c=None)
    grid = []
    for p in lp["points"]:
        if p.get("id") and p["id"] in recs:
            blk = loss_point_block(recs, p["id"], mech_of(p["rpm"]))
            # OPERATING-STATE torque factor: the settled loss-point torque
            # (demag steady state + rotor eddy reaction, TDM) over the virgin
            # static map at the same (I, gamma) — what a map reader must apply
            # to quote operating torque (not decomposed in stage 1).
            Tm_ = hm.at_Ig(blk["I_rms"], blk["gamma"])["T"]
            blk["T_map_virgin_Nm"] = Tm_
            blk["k_state"] = float(blk["T_Nm"]) / Tm_ if Tm_ else None
            grid.append({"plan": p, **blk})
        else:
            grid.append({"plan": p, "status": "infeasible: " + str(p.get("mode"))})

    def k_state_at(n, I):
        """k_state on the current row nearest I (rows are the grid currents),
        linear in n between the row's points, held at the row ends."""
        pts = [g for g in grid if g.get("k_state") is not None]
        if not pts:
            return None
        Irow = min({g["I_rms"] for g in pts}, key=lambda x: abs(x - I))
        row = sorted((g["rpm"], g["k_state"]) for g in pts if abs(g["I_rms"] - Irow) < 1e-9)
        ns = [r[0] for r in row]
        ks = [r[1] for r in row]
        return float(np.interp(n, ns, ks))
    # ── card values ───────────────────────────────────────────────────────
    rated_id = [p["id"] for p in lp["points"]
                if p.get("id") and abs(p["rpm"] - n0) < 1e-6 and abs(p["I"] - I0) < 1e-6]
    rated_blk = (loss_point_block(recs, rated_id[0], mech_of(n0))
                 if rated_id and rated_id[0] in recs else None)
    pk = snap.get("peak_duty")
    n_pk = float(pk["rpm"]) if pk else n0
    vl_t = float(lp["v_phase_limit_V"])
    g_pk, how_pk = hm.operating_gamma(I_pk, n_pk, R_hot, vl_t)
    T_pk_map = hm.at_Ig(I_pk, g_pk)["T"] if g_pk is not None else None
    rows_grid = loss_rows(recs, st)
    pk_loss = LS.interp_loss(n_pk, I_pk, rows_grid, R_hot)
    mech_pk = mech_of(n_pk)
    k_pk = k_state_at(n_pk, I_pk)
    T_pk_for_eta = (T_pk_map * k_pk) if (T_pk_map is not None and k_pk) else None
    eff_pk = (LS.efficiency_shaft(T_pk_for_eta, n_pk, pk_loss["P_total_W"],
                                  float(mech_pk.get("P_W") or 0.0))
              if (T_pk_for_eta is not None and pk_loss.get("P_total_W")) else None)
    T_rated_map = float(m1["T_fem_vertex"])
    g_r, how_r = hm.operating_gamma(I0, n0, R_hot, vl_t)
    T_rated_map_op = hm.at_Ig(I0, g_r)["T"] if g_r is not None else None
    kvc = kv_rpm_per_V_line(psi_pm_c, pp)
    kvh = kv_rpm_per_V_line(psi_pm_h, pp)
    v_max = float(snap["battery"]["v_max"])
    v_min = float(snap["battery"]["v_min"])
    cog_c = float(cnl.get("T_ripple_pp_Nm") or 0.0)
    cog_h = float(hnl_full.get("T_ripple_pp_Nm") or 0.0)
    labels_cold = list(STAGE1_LABELS) + ["COLD 20 °C magnets and winding"]
    labels_hot = list(STAGE1_LABELS) + ["HOT: magnets %.1f °C, winding %.1f °C" % (T_hot_m, T_hot_c)]
    card = {
        "rated_point": {
            "rpm": n0, "I_rms": I0, "gamma_op": g_r, "gamma_mode": how_r,
            "bus": "v_%s (trajectory bus), m = %g" % (lp["bus"], m),
            "T_map_virgin_Nm": _val(T_rated_map_op, "N·m",
                                    "hot psi-map (virgin magnets, static, Coulomb)",
                                    [m1["conf_id"]], labels_hot),
            "T_operating_Nm": _val(rated_blk and rated_blk["T_Nm"], "N·m",
                                   "settled loss-grid FEM: TDM coupled eddy + demag "
                                   "steady state (Coulomb)",
                                   rated_id, labels_hot),
            "P_shaft_W": _val(rated_blk and rated_blk["efficiency"]["P_shaft_W"], "W",
                              "T_operating·w − P_mech", rated_id, labels_hot),
            "P_loss_em_W": _val(rated_blk and rated_blk["P_loss_em_W"], "W",
                                "loss-grid FEM total (Cu DC+AC, iron, magnet, shaft)",
                                rated_id, labels_hot),
            "P_mech_W": _val(rated_blk and rated_blk["P_mech_W"], "W",
                             "SKF bearings + Couette/face windage, analytic", [], labels_hot),
            "eta_shaft": _val(rated_blk and rated_blk["efficiency"]["eta_shaft"], "-",
                              "one efficiency, at the shaft", rated_id, labels_hot),
            "k_state_split": (None if ("xs_rated_nodemag" not in recs or not rated_blk) else {
                "k_state": float(rated_blk["T_Nm"]) / float(T_rated_map_op),
                "eddy_and_sampling_share": float(recs["xs_rated_nodemag"]["r"]["T_avg_Nm"])
                / float(T_rated_map_op),
                "demag_share": float(rated_blk["T_Nm"])
                / float(recs["xs_rated_nodemag"]["r"]["T_avg_Nm"]),
                "runs": rated_id + ["xs_rated_nodemag"],
                "method": "same settled TDM point with demag OFF: demag share = "
                          "T(demag)/T(no demag); the rest (coupled eddy reaction, 36-step "
                          "sampling, duty gap layers) = T(no demag)/T_map"}),
        },
        "peak_point": {
            "rpm": n_pk, "I_rms": I_pk, "I_source": lp["I_peak_source"],
            "gamma_op": g_pk, "gamma_mode": how_pk,
            "T_map_virgin_Nm": _val(T_pk_map, "N·m", "hot psi-map interpolation", [],
                                    labels_hot),
            "k_state": _val(k_pk, "-", "operating/virgin torque factor of the loss "
                            "trajectory, nearest current row, linear in n", [], labels_hot),
            "T_operating_Nm": _val(T_pk_for_eta, "N·m", "map torque × k_state", [],
                                   labels_hot),
            "P_loss_em_W": _val(pk_loss.get("P_total_W"), "W",
                                "loss-trajectory interpolation (per-group n^k, linear in I)",
                                [], labels_hot, notes=pk_loss.get("notes")),
            "P_mech_W": _val(mech_pk.get("P_W"), "W", "analytic", [], labels_hot),
            "P_shaft_W": _val(eff_pk and eff_pk["P_shaft_W"], "W", "T_operating·w − P_mech", [],
                              labels_hot),
            "eta_shaft": _val(eff_pk and eff_pk["eta_shaft"], "-",
                              "operating torque + interpolated losses", [], labels_hot),
        },
        "constants_cold": {
            "psi_PM_Wb": _val(psi_pm_c, "Wb phase peak", "cold no-load psi_d (fundamental frame)",
                              ["cold_noload"], labels_cold),
            "Kv_rpm_per_V_line": _val(kvc, "rpm/V (line peak)",
                                      "60/(2π·p·√3·psi_PM), fundamental; ÷k_3d NOT applied",
                                      ["cold_noload"], labels_cold),
            "Kt_low_Nm_per_A": _val(T_c025 / (0.25 * I0), "N·m/A rms",
                                    "cold MTPA vertex at 0.25·I0", [c025["conf_id"]], labels_cold),
            "Kt_rated_Nm_per_A": _val(T_c1 / I0, "N·m/A rms", "cold MTPA vertex at I0",
                                      [c1["conf_id"]], labels_cold),
            "Km_Nm_per_sqrtW": _val(T_c1 / math.sqrt(3.0 * I0 * I0 * R20), "N·m/√W",
                                    "T_cold(I0)/√(3·I0²·R20), DC copper incl. end windings",
                                    [c1["conf_id"], "cold_noload"], labels_cold),
            "R_phase_ohm": _val(R20, "Ω", "solver R_phase at 20 °C incl. k_end",
                                ["cold_noload"], labels_cold),
            "R_line_line_ohm": _val(2.0 * R20, "Ω", "2·R_phase (star)", ["cold_noload"],
                                    labels_cold),
            "Ld_bench_mH": _val(ss.get("cold") and 1e3 * ss["cold"]["Ld_H"], "mH",
                                (ss.get("cold") or {}).get("method", "not measured"),
                                (ss.get("cold") or {}).get("runs", []), labels_cold),
            "Lq_bench_mH": _val(ss.get("cold") and 1e3 * ss["cold"]["Lq_H"], "mH",
                                (ss.get("cold") or {}).get("method", "not measured"),
                                (ss.get("cold") or {}).get("runs", []), labels_cold),
            "Ld_frozen_2A_mH": _val(inc_small.get("Ld_mH"), "mH",
                                    "solver inc_ldq: frozen-permeability (secant ν) at 2 A — "
                                    "NOT the differential (P20)", ["cold_probe2A"], labels_cold),
            "Lq_frozen_2A_mH": _val(inc_small.get("Lq_mH"), "mH",
                                    "solver inc_ldq at 2 A (frozen secant ν)", ["cold_probe2A"],
                                    labels_cold),
            "Ld_chord_rated_mH": _val(None if Ld_chord_c is None else 1e3 * Ld_chord_c, "mH",
                                      "(psi_d − psi_PM)/i_d at the cold rated MTPA vertex "
                                      "(withheld when |i_d| < 10 % of |i|)",
                                      [c1["conf_id"]], labels_cold),
            "Lq_chord_rated_mH": _val(1e3 * Lq_chord_c, "mH", "psi_q/i_q at the cold rated vertex",
                                      [c1["conf_id"]], labels_cold),
            "cogging_pp_Nm": _val(cog_c, "N·m", "cold no-load torque p-p, full period, "
                                  "cogging-quality sampling (Coulomb)", ["cold_noload"],
                                  labels_cold),
            "I_char_rms_A": _val(I_char, "A rms", "psi_d = 0 on the d-axis (3 static points, "
                                 "quadratic fit) — characteristic current, not the SC transient",
                                 ["cold_sc_0", "cold_sc_1", "cold_sc_2"], labels_cold),
            "I_sc_steady_rated_speed_rms_A": _val(sc_steady and sc_steady["I_rms_A"], "A rms",
                                                  "steady 3-ph short circuit at n0 from both dq "
                                                  "equations with R20, linearised cold map",
                                                  ["cold_noload", "cold_sc_1", c1["conf_id"]],
                                                  labels_cold),
            "bus_crossing_speed_vmax_rpm": _val(kvc * v_max, "rpm",
                                                "cold-magnet bus-crossing speed = Kv_cold × v_max "
                                                "(uncontrolled-generator threshold, NOT a "
                                                "mechanical runaway; no modulation margin)",
                                                ["cold_noload"], labels_cold),
            "bus_crossing_speed_vmin_rpm": _val(kvc * v_min, "rpm",
                                                "Kv_cold × v_min (report convention)",
                                                ["cold_noload"], labels_cold),
        },
        "constants_hot": {
            "psi_PM_Wb": _val(psi_pm_h, "Wb phase peak", "hot no-load psi_d",
                              ["hot_I0_anchor"], labels_hot),
            "Kv_rpm_per_V_line": _val(kvh, "rpm/V (line peak)", "fundamental", ["hot_I0_anchor"],
                                      labels_hot),
            "Kt_rated_Nm_per_A": _val(T_rated_map / I0, "N·m/A rms",
                                      "hot MTPA vertex at I0 (virgin map)", [m1["conf_id"]],
                                      labels_hot),
            "Kt_operating_Nm_per_A": _val(rated_blk and rated_blk["T_Nm"] / I0, "N·m/A rms",
                                          "rated loss-grid point (demag + eddy, operating angle)",
                                          rated_id, labels_hot),
            "R_phase_ohm": _val(R_hot, "Ω", "solver R_phase at the hot winding temperature",
                                [m1["conf_id"]], labels_hot),
            "Ld_small_signal_mH": _val(ss.get("hot") and 1e3 * ss["hot"]["Ld_H"], "mH",
                                       (ss.get("hot") or {}).get("method", "not measured"),
                                       (ss.get("hot") or {}).get("runs", []), labels_hot),
            "Lq_small_signal_mH": _val(ss.get("hot") and 1e3 * ss["hot"]["Lq_H"], "mH",
                                       (ss.get("hot") or {}).get("method", "not measured"),
                                       (ss.get("hot") or {}).get("runs", []), labels_hot),
            "Ld_incremental_rated_mH": _val(inc_h.get("Ld_mH"), "mH",
                                            "frozen-permeability incremental at the hot rated "
                                            "vertex (NOT the true differential, P20)",
                                            [m1["conf_id"]], labels_hot),
            "Lq_incremental_rated_mH": _val(inc_h.get("Lq_mH"), "mH",
                                            "frozen-permeability incremental at the hot rated vertex",
                                            [m1["conf_id"]], labels_hot),
            "Ld_differential_rated_mH": _val(1e3 * dl["Ld_diff_H"], "mH",
                                             "∂psi_d/∂i_d of the C¹ map (true differential incl. "
                                             "saturation; central difference h = 1 % of the map "
                                             "radius)", [], labels_hot),
            "Lq_differential_rated_mH": _val(1e3 * dl["Lq_diff_H"], "mH", "∂psi_q/∂i_q of the map",
                                             [], labels_hot),
            "cogging_pp_Nm": _val(cog_h, "N·m", "hot no-load p-p, full period", ["hot_noload"],
                                  labels_hot),
        },
        "speed_limits_hot": {
            w: {"base_speed_rated_I_rpm": env[w]["base_speed_rated_I_rpm"],
                "max_speed_I0_rpm": env[w]["max_speed_I0_rpm"],
                "max_speed_Ipk_rpm": env[w]["max_speed_Ipk_rpm"],
                "method": "hot psi-map, V_ph = |R·i + jw·psi| ≤ m·V_dc/√3, FW to gamma 80° "
                          "(grid limit), m = %g placeholder" % m}
            for w in env},
        "mechanical_speed_limit_rpm": {"value": lp["n_mech_limit_rpm"],
                                       "source": lp["n_mech_source"]},
    }
    # ── checks ────────────────────────────────────────────────────────────
    chk_static = static_check_rows(recs, st, hm, I0)
    pts0 = [p for p in hot_pts if not str(p.get("role") or "").startswith("fw refinement")]
    chk_static_first = (static_check_rows(recs, st, PM.PsiMap.build(pts0, pp, mtpa=hm.mtpa), I0)
                        if len(pts0) != len(hot_pts) else None)
    chk_loss = loss_check_rows(recs, st, R_hot, snap)
    indep = {}
    for jid, ref in (("full_rated_mtpa", m1["conf_id"]), ("full_peak", "chk_peak")):
        if jid in recs and ref in recs:
            f = recs[jid]["r"]
            w = recs[ref]["r"]
            indep[jid] = {
                "T_coulomb_full_Nm": f.get("T_avg_Nm"),
                "T_terminal_work_full_Nm": f.get("terminal_work_mean_candidate_Nm"),
                "terminal_work_eligible": f.get("terminal_work_eligible"),
                "err_coulomb_vs_terminal_pct": (_pct(float(f["T_avg_Nm"]),
                                                     float(f["terminal_work_mean_candidate_Nm"]))
                                                if f.get("terminal_work_mean_candidate_Nm")
                                                else None),
                "T_window60_Nm": w.get("T_avg_Nm"),
                "err_window60_vs_full_pct": _pct(float(w["T_avg_Nm"]), float(f["T_avg_Nm"])),
                "psi_d_window60_vs_full_pct": _pct(float(w["psi_d_Wb"]), float(f["psi_d_Wb"])),
                "ripple_window60_pct": 100.0 * float(w.get("T_ripple_pp_Nm") or 0) / float(w["T_avg_Nm"]),
                "ripple_full_pct": 100.0 * float(f.get("T_ripple_pp_Nm") or 0) / float(f["T_avg_Nm"]),
                "gap_layers": [f.get("gap_layers_effective"), w.get("gap_layers_effective")],
            }
    ts = None
    if "lchk_rated_72steps" in recs and rated_id and rated_id[0] in recs:
        a = recs[rated_id[0]]["r"]
        b = recs["lchk_rated_72steps"]["r"]
        ts = {"steps": [a.get("n_steps_per_period"), b.get("n_steps_per_period")],
              "T_pct": _pct(float(a["T_avg_Nm"]), float(b["T_avg_Nm"])),
              "P_total_pct": _pct(float(a["P_loss_total_W"]), float(b["P_loss_total_W"])),
              "groups_pct": {g: (_pct(float(a[g]), float(b[g])) if b.get(g) else None)
                             for g in ("P_cu_ac_W", "P_fe_W", "P_mag_W", "P_shaft_W")}}
    duties = {}
    for jid, du in (("duty_rated", snap["rated_duty"]), ("duty_peak", snap.get("peak_duty"))):
        if du and jid in recs:
            r = recs[jid]["r"]
            s = du.get("stored_summary") or {}
            duties[jid] = {
                "today": {"T_Nm": r.get("T_avg_Nm"), "P_loss_W": r.get("P_loss_total_W"),
                          "P_cu_W": r.get("P_cu_W"), "P_fe_W": r.get("P_fe_W"),
                          "P_mag_W": r.get("P_mag_W"), "ripple_pct": r.get("T_ripple_pct")},
                "stored": s, "stored_saved_at": du.get("saved_at"),
                "T_pct": _pct(float(r["T_avg_Nm"]), float(s["T_em_avg_Nm"]))
                if s.get("T_em_avg_Nm") else None,
                "P_loss_pct": _pct(float(r["P_loss_total_W"]), float(s["P_loss_total_W"]))
                if s.get("P_loss_total_W") else None,
                "map_T_at_duty_Nm": (hm.at_Ig(du["current_arms"], du["gamma_deg"])["T"]
                                     if du.get("magnet_temp_c") == T_hot_m else None),
                "note": ("stored duty solved with an older baseline and geometry "
                         "(stator_fillet_r1 0.1 vs die 0.15 today on L12)"),
            }
    audit = None
    if snap["tag"] == "L12":
        audit = {"reference": AUDIT, "static": [], "loss": []}
        for f, (g_a, T_a) in AUDIT["mtpa_static"].items():
            I = 40.659 * f
            g_p = hm.gamma_mtpa(I)
            audit["static"].append({"I": I, "gamma_mtpa_audit": g_a, "T_audit": T_a,
                                    "gamma_mtpa_passport": g_p,
                                    "T_passport": hm.at_Ig(I, g_p)["T"],
                                    "T_pct": _pct(hm.at_Ig(I, g_p)["T"], T_a)})
    demag = dict(st.get("demag_knee") or {})
    probes = {}
    for k, p in (demag.get("probes") or {}).items():
        p = dict(p)
        r = recs.get(k, {}).get("r", {})
        p["T_Nm"] = r.get("T_avg_Nm")
        p["steady_note"] = r.get("steady_state_note")
        if (p.get("settled") is False and r.get("T_avg_Nm") is not None
                and abs(float(r["T_avg_Nm"])) < 0.01 * T_rated_map):
            p["settled_under_floor"] = True
            p["floor_note"] = ("magnets settled; the solver's RELATIVE torque-drift test is "
                               "degenerate at T ≈ 0 (%.4g N·m) — settled under the spec §1 "
                               "absolute floor (0.05 %% of rated torque)" % float(r["T_avg_Nm"]))
        probes[k] = p
    demag["probes"] = probes
    failed = recs.pop("__failed__")["list"]
    total_wall = sum(float(v.get("wall_s") or 0.0) for v in recs.values())
    rec = {
        "schema": SCHEMA_VERSION, "convention_rev": CONVENTION_REV,
        "machine": {"die": snap["die"], "configuration": snap["configuration"],
                    "version": machine_meta.get("version"), "tag": snap["tag"]},
        "labels": list(STAGE1_LABELS),
        "pending_owner_defaults": PENDING_DEFAULTS,
        "provenance": {
            "snapshot_sha256": snap.get("snapshot_sha256"),
            "signatures": snap["signatures"],
            "baseline_runs": st.get("baseline_runs"),
            "baseline_assemble": st.get("baseline_assemble"),
            "d_axis_deg": st.get("daxis_deg"), "d_axis_source": st.get("daxis_source"),
            "gap_layers_static": st.get("gap_layers_static"),
            "gap_layers_reason": st.get("gap_layers_reason"),
            "static_window": "60° electrical, 144 steps/period (24 positions, 12 per cogging cycle)",
            "runs_ok": len(recs), "runs_failed": failed,
            "fem_wall_s_sum": total_wall,
            "stage_wall_s": st.get("stage_wall_s"),
        },
        "inputs": {k: snap[k] for k in ("temperatures", "materials", "winding", "mesh",
                                         "rated_duty", "peak_duty", "battery", "bearings",
                                         "part_states", "owner_inputs")},
        "card": card,
        "hot_map": {
            "fixed_retention_state": "virgin magnets at the HOT temperature (reversible Br(T) "
                                     "only); damage is a boundary (B7), see demag",
            "points": hot_pts, "mtpa": st["hot_mtpa"],
            "interpolation": "Clough–Tocher C¹ in (i_d, i_q), isotropic scale = max |i|",
            "dq_identity_check_max_pct": max(dq_chk) if dq_chk else None,
            "reciprocity_asym_max": max(recip) if recip else None,
            "refusal": "outside the convex hull or gamma > 80° (FW) → refused",
        },
        "cold": {"mtpa": st["cold_mtpa"], "k_levels": k_levels,
                 "k_psi_noload_cold_over_hot": psi_pm_c / psi_pm_h,
                 "sc_plan": st.get("cold_sc_plan"), "sc_points": sc,
                 "fw_checks": {k: {"T": recs[k]["r"]["T_avg_Nm"],
                                   "psi_d": recs[k]["r"]["psi_d_Wb"],
                                   "psi_q": recs[k]["r"]["psi_q_Wb"],
                                   "gamma": recs[k]["kw"]["gamma_deg"],
                                   "abs_psi_cold_over_hot_map": (
                                       math.hypot(recs[k]["r"]["psi_d_Wb"], recs[k]["r"]["psi_q_Wb"])
                                       / math.hypot(*(lambda o: (o["psi_d"], o["psi_q"]))(
                                           hm.at(recs[k]["r"]["i_d_A"], recs[k]["r"]["i_q_A"])))),
                                   "note": "cold/hot |psi| at the same (i_d, i_q): the cold "
                                           "voltage-limit / FW boundary factor (P21)"}
                               for k in recs
                               if str(recs[k]["meta"].get("role", "")).startswith("cold fw")}},
        "demag": demag,
        "envelope": env,
        "loss_grid": {"plan": {k: v for k, v in lp.items() if k != "points"},
                      "points": grid,
                      "interpolation": "per group P = a·n^k between neighbouring speed points "
                                       "of a current row, linear in I; DC copper analytic "
                                       "3·I²·R_hot; never extrapolated past the grid speeds",
                      "mech": mech},
        "checks": {"static_offgrid": chk_static,
                   "static_offgrid_first_pass": chk_static_first,
                   "refinement": st.get("refinement"),
                   "loss_offgrid": chk_loss,
                   "independent_torque_B1_window_P05": indep, "time_step_B4": ts,
                   "duties": duties, "audit": audit},
        "stage2_3d": {"status": NOT_COMPUTED_STAGE1,
                      "fields": ["k_flux(L)", "k_T(L)", "k_L(L)", "magnet segmentation factor"]},
        "stage3_pwm_controller": {"status": NOT_COMPUTED_STAGE1,
                                  "fields": ["PWM loss deltas (2 carriers)",
                                             "controller conduction/switching losses "
                                             "(PCB_CIANO14_40)", "drive efficiency"]},
        "not_in_stage1": ["harmonic spectrum of stator B in 3 probe regions (§4)",
                          "per-row slot field map for hybrid AC copper (§6.3)",
                          "thermal loop / I_thermal current limit (§8.4)",
                          "critical speed and rotor stress limits (§5.5)",
                          "generator quadrant of the map"],
    }
    return rec
