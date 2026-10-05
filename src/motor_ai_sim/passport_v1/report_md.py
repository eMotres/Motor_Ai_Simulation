"""Markdown pilot report from the passport records — tables, one-line notes."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional


def f(v: Any, nd: int = 4) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int,)) and not isinstance(v, bool):
        return str(v)
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    if not math.isfinite(x):
        return "—"
    if x == 0:
        return "0"
    mag = math.floor(math.log10(abs(x)))
    dec = max(0, nd - 1 - mag)
    return ("%.*f" % (dec, x))


def pct(v: Any, nd: int = 2) -> str:
    if v is None:
        return "—"
    try:
        return "%+.*f %%" % (nd, float(v))
    except (TypeError, ValueError):
        return str(v)


def ok(b: Any) -> str:
    return "pass" if b else ("—" if b is None else "**FAIL**")


def table(head: List[str], rows: List[List[Any]]) -> str:
    out = ["| " + " | ".join(head) + " |",
           "|" + "|".join("---" if i == 0 else "---:" for i in range(len(head))) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(out)


def _v(blk: Mapping[str, Any], k: str):
    x = blk.get(k)
    return x.get("value") if isinstance(x, Mapping) else x


def render(recs: Mapping[str, Mapping[str, Any]], *, budget: Mapping[str, Any]) -> str:
    Ms = list(recs)
    L: List[str] = []
    a = L.append
    a("# Motor passport pilot — Ø40 CIANO14 40 new, stage 1 (2026-10-05)")
    a("")
    a("Stage 1 = plain 2-D, sine current drive, no 3-D end correction, no PWM, no "
      "controller losses; every value below carries these labels in the record.")
    a("")
    a("Records: `docs/data/passport_pilot_d40/passport_<M>.json` (full precision, "
      "method + run ids per value). Spec: `docs/PASSPORT_ALGORITHM.md` v1.1.")
    a("")
    # ── inputs ────────────────────────────────────────────────────────────
    a("## Machines and frozen inputs (M0)")
    a("")
    rows = []
    for M in Ms:
        r = recs[M]
        inp = r["inputs"]
        rd = inp["rated_duty"]
        pk = inp.get("peak_duty")
        t = inp["temperatures"]
        b = inp["battery"]
        cp = r["card"]["peak_point"]
        rows.append([M, r["machine"]["version"],
                     "%g / %g / %g V" % (b["v_min"], b["v_nom"], b["v_max"]),
                     "%g A @ %g rpm" % (rd["current_arms"], rd["rpm"]),
                     ("%g A @ %g rpm" % (pk["current_arms"], pk["rpm"]) if pk
                      else "%s A (assumed)" % f(cp["I_rms"])),
                     "%g / %g °C" % (t["hot_magnet_c"], t["hot_coil_c"]),
                     f(rd["end_winding_factor"]),
                     "%g mm / %g / %g sect." % (inp["mesh"]["mesh_size_mm"],
                                                inp["mesh"]["min_size_mm"],
                                                inp["mesh"]["n_sectors"]),
                     "%g / %g" % (r["provenance"]["gap_layers_static"], inp["mesh"]["gap_layers"]),
                     f(r["provenance"]["d_axis_deg"], 6),
                     "`%s`" % (r["provenance"]["snapshot_sha256"] or "")[:16]])
    a(table(["", "pack", "bus min/nom/max", "rated (I0, n0)", "peak", "hot magnet / coil",
             "k_end", "mesh / min / sectors", "gap layers map / loss", "d-axis °",
             "snapshot"], rows))
    a("")
    a("Materials: F52SH_120C, 20SW1200 stator + rotor, Steel_42CrMo4_QT shaft, copper, "
      "Nomex liner, polyimide enamel (deployed library; PR #51 not merged).")
    a("")
    # ── card ──────────────────────────────────────────────────────────────
    a("## Card values")
    a("")
    head = ["quantity", "unit"] + Ms + ["method"]
    rows = []

    def row(label, unit, getter, method):
        vals = []
        for M in Ms:
            try:
                vals.append(getter(recs[M]))
            except Exception:                # noqa: BLE001
                vals.append("—")
        rows.append([label, unit] + vals + [method])
    c = lambda M: recs[M]["card"]   # noqa: E731
    row("rated torque, operating (hot)", "N·m",
        lambda r: f(_v(r["card"]["rated_point"], "T_operating_Nm")),
        "settled TDM + demag FEM at n0, I0, operating γ")
    row("rated torque, map (hot, virgin)", "N·m",
        lambda r: f(_v(r["card"]["rated_point"], "T_map_virgin_Nm")),
        "static ψ-map, same (I0, γ)")
    row("rated k_state = demag × rest", "-",
        lambda r: (lambda s: "%s = %s × %s" % (f(s["k_state"], 5), f(s["demag_share"], 5),
                                               f(s["eddy_and_sampling_share"], 5))
                   if s else "—")(r["card"]["rated_point"].get("k_state_split")),
        "operating / map; demag-off TDM run splits it")
    row("rated γ / mode", "°",
        lambda r: "%s / %s" % (f(r["card"]["rated_point"]["gamma_op"], 3),
                               r["card"]["rated_point"]["gamma_mode"]), "v_nom, m = 0.95")
    row("rated shaft power", "W", lambda r: f(_v(r["card"]["rated_point"], "P_shaft_W")),
        "T·ω − P_mech")
    row("rated EM loss", "W", lambda r: f(_v(r["card"]["rated_point"], "P_loss_em_W")),
        "Cu DC+AC, iron, magnet, shaft")
    row("rated mech loss", "W", lambda r: f(_v(r["card"]["rated_point"], "P_mech_W")),
        "bearings + windage, analytic")
    row("rated η at the shaft", "%",
        lambda r: f(100 * _v(r["card"]["rated_point"], "eta_shaft")), "one efficiency")
    row("peak current", "A rms", lambda r: f(r["card"]["peak_point"]["I_rms"]),
        "owner duty (L12) / assumption (L20)")
    row("peak speed", "rpm", lambda r: f(r["card"]["peak_point"]["rpm"]), "")
    row("peak γ / mode", "°",
        lambda r: "%s / %s" % (f(r["card"]["peak_point"]["gamma_op"], 3),
                               r["card"]["peak_point"]["gamma_mode"]), "v_nom, m = 0.95")
    row("peak torque, map (hot, virgin)", "N·m",
        lambda r: f(_v(r["card"]["peak_point"], "T_map_virgin_Nm")), "map at operating γ")
    row("peak torque, operating (hot)", "N·m",
        lambda r: "%s (k_state %s)" % (f(_v(r["card"]["peak_point"], "T_operating_Nm")),
                                        f(_v(r["card"]["peak_point"], "k_state"), 5)),
        "map × loss-trajectory state factor")
    row("peak η at the shaft", "%",
        lambda r: f(100 * _v(r["card"]["peak_point"], "eta_shaft")),
        "map T + interpolated losses")
    for k, lab, u in (("psi_PM_Wb", "ψ_PM", "mWb"), ("Kv_rpm_per_V_line", "Kv (line pk)", "rpm/V")):
        row(lab + " cold / hot", u,
            lambda r, k=k: "%s / %s" % (
                f(_v(r["card"]["constants_cold"], k) * (1e3 if u == "mWb" else 1)),
                f(_v(r["card"]["constants_hot"], k) * (1e3 if u == "mWb" else 1))),
            "no-load, fundamental; ÷k_3d not applied")
    row("Kt cold 0.25·I0 / cold I0 / hot I0", "N·m/A",
        lambda r: "%s / %s / %s" % (f(_v(r["card"]["constants_cold"], "Kt_low_Nm_per_A")),
                                     f(_v(r["card"]["constants_cold"], "Kt_rated_Nm_per_A")),
                                     f(_v(r["card"]["constants_hot"], "Kt_rated_Nm_per_A"))),
        "MTPA vertices")
    row("Km cold", "N·m/√W", lambda r: f(_v(r["card"]["constants_cold"], "Km_Nm_per_sqrtW")),
        "T/√P_cu,DC at 20 °C")
    row("R phase 20 °C / hot", "mΩ",
        lambda r: "%s / %s" % (f(1e3 * _v(r["card"]["constants_cold"], "R_phase_ohm")),
                               f(1e3 * _v(r["card"]["constants_hot"], "R_phase_ohm"))),
        "incl. end windings (k_end)")
    row("Ld / Lq bench small-signal cold / hot", "µH",
        lambda r: "%s / %s ; %s / %s" % (
            f(1e3 * _v(r["card"]["constants_cold"], "Ld_bench_mH")),
            f(1e3 * _v(r["card"]["constants_cold"], "Lq_bench_mH")),
            f(1e3 * _v(r["card"]["constants_hot"], "Ld_small_signal_mH")),
            f(1e3 * _v(r["card"]["constants_hot"], "Lq_small_signal_mH"))),
        "±2 A pairs, Δψ/Δi (differential)")
    row("Ld / Lq solver inc_ldq at 2 A (cold)", "µH",
        lambda r: "%s / %s" % (f(1e3 * _v(r["card"]["constants_cold"], "Ld_frozen_2A_mH")),
                               f(1e3 * _v(r["card"]["constants_cold"], "Lq_frozen_2A_mH"))),
        "frozen secant ν — not the bench value")
    row("Ld / Lq differential (hot I0)", "µH",
        lambda r: "%s / %s" % (f(1e3 * _v(r["card"]["constants_hot"], "Ld_differential_rated_mH")),
                               f(1e3 * _v(r["card"]["constants_hot"], "Lq_differential_rated_mH"))),
        "∂ψ/∂i of the map")
    row("Ld / Lq incremental (hot I0)", "µH",
        lambda r: "%s / %s" % (f(1e3 * _v(r["card"]["constants_hot"], "Ld_incremental_rated_mH")),
                               f(1e3 * _v(r["card"]["constants_hot"], "Lq_incremental_rated_mH"))),
        "frozen permeability at the point")
    row("cogging p-p cold / hot", "mN·m",
        lambda r: "%s / %s" % (f(1e3 * _v(r["card"]["constants_cold"], "cogging_pp_Nm")),
                               f(1e3 * _v(r["card"]["constants_hot"], "cogging_pp_Nm"))),
        "no-load full period")
    row("characteristic current (cold)", "A rms",
        lambda r: f(_v(r["card"]["constants_cold"], "I_char_rms_A")), "ψd = 0 on the d-axis")
    row("steady SC current @ n0 (cold)", "A rms",
        lambda r: f(_v(r["card"]["constants_cold"], "I_sc_steady_rated_speed_rms_A")),
        "both dq equations with R")
    row("base speed @ I0, v_min / v_nom / v_max", "rpm",
        lambda r: " / ".join(f(r["card"]["speed_limits_hot"][w]["base_speed_rated_I_rpm"], 5)
                             for w in ("min", "nom", "max")), "MTPA voltage = limit")
    row("max speed @ I_peak, v_min / v_nom / v_max", "rpm",
        lambda r: " / ".join(f(r["card"]["speed_limits_hot"][w]["max_speed_Ipk_rpm"], 5)
                             for w in ("min", "nom", "max")), "FW to γ 80° (grid limit)")
    row("cold bus-crossing speed @ v_max", "rpm",
        lambda r: f(_v(r["card"]["constants_cold"], "bus_crossing_speed_vmax_rpm"), 5),
        "Kv_cold·v_max — not a mechanical runaway")
    row("mechanical speed limit", "rpm",
        lambda r: f(r["card"]["mechanical_speed_limit_rpm"]["value"], 5),
        "bearing rating only (critical speed / stress not in stage 1)")
    a(table(head, rows))
    a("")
    a("Hot = rated-duty magnet / winding temperature; cold = 20 °C. Map torque = virgin "
      "magnets; operating torque = demag steady state + rotor eddy reaction.")
    a("")
    # ── hot map ───────────────────────────────────────────────────────────
    a("## Hot ψ-map: MTPA line (FEM-confirmed vertices)")
    a("")
    for M in Ms:
        hm_ = recs[M]["hot_map"]
        rows = [[f(m["fI"], 3), f(m["I"]), f(m["gamma_mtpa"], 3), f(m["T_fem_vertex"], 6),
                 pct(m["vertex_vs_parabola_pct"], 4)] for m in hm_["mtpa"]]
        a("**%s** — %d map points; dq identity ≤ %s %%; window 60° el, 24 positions"
          % (M, len(hm_["points"]), f(hm_["dq_identity_check_max_pct"], 2)))
        a("")
        a(table(["I/I0", "I A", "γ_MTPA °", "T N·m", "vertex vs parabola"], rows))
        a("")
    # ── envelope ──────────────────────────────────────────────────────────
    a("## Operating envelope at the bus (hot map, I ≤ I_peak, FW to 80°, m = 0.95)")
    a("")
    for M in Ms:
        env = recs[M]["envelope"]
        rows = []
        for i, pt in enumerate(env["nom"]["torque_speed"]):
            cells = [f(pt["rpm"], 5)]
            for w in ("min", "nom", "max"):
                q = env[w]["torque_speed"][i]
                cells.append("—" if q.get("T") is None else "%s (%s)" % (
                    f(q["T"]), "MTPA" if q.get("mode") == "mtpa" else "FW %s°" % f(q["gamma"], 3)))
            rows.append(cells)
        a("**%s** — max virgin-map torque [N·m] vs speed (× k_state ≈ 0.98 for operating)" % M)
        a("")
        a(table(["n rpm", "v_min", "v_nom", "v_max"], rows))
        a("")
    # ── loss trajectory ──────────────────────────────────────────────────
    a("## Loss trajectory (hot, settled TDM + demag, v_nom, m = 0.95)")
    a("")
    for M in Ms:
        rows = []
        for g in recs[M]["loss_grid"]["points"]:
            p = g["plan"]
            if "status" in g:
                rows.append([f(p["rpm"], 5), f(p["I"]), "—", "—", "—", "—", "—", "—", "—",
                             "—", "infeasible"])
                continue
            gw = g["groups_W"]
            rows.append([f(p["rpm"], 5), f(p["I"]), f(g["gamma"], 3), f(g["T_Nm"]),
                         f(g.get("k_state"), 5),
                         f((gw.get("P_cu_dc_W") or 0) + (gw.get("P_cu_ac_W") or 0)),
                         f((gw.get("P_fe_stator_W") or 0) + (gw.get("P_fe_rotor_W") or 0)),
                         f((gw.get("P_mag_W") or 0) + (gw.get("P_shaft_W") or 0)),
                         f(g["P_loss_em_W"]), f(100 * (g["efficiency"]["eta_shaft"] or 0)),
                         "%s %%Br%s" % (f(g["demag"]["br_kept_vol_pct"], 5),
                                        "" if g["settle"]["steady_state"] else ", NOT steady")])
        lp = recs[M]["loss_grid"]["plan"]
        a("**%s** — n_max %s rpm (%s); I_peak %s A (%s)" % (
            M, f(lp["n_max_rpm"], 5),
            "electrical, v_nom" if lp["n_elec_limit_rpm"] <= lp["n_mech_limit_rpm"]
            else "bearing", f(lp["I_peak_rms"]), lp["I_peak_source"]))
        a("")
        a(table(["n rpm", "I A", "γ °", "T N·m", "k_state", "Cu W", "iron W", "magnet+shaft W",
                 "total W", "η shaft %", "demag"], rows))
        a("")
    a("k_state = settled operating torque / virgin static map at the same (I, γ).")
    a("")
    # ── checks ────────────────────────────────────────────────────────────
    a("## Off-grid checks — static map (interpolated vs direct FEM)")
    a("")
    for M in Ms:
        rows = []
        for c in recs[M]["checks"]["static_offgrid"]:
            if "err_T_pct" not in c:
                rows.append([c["id"], "—", "—", "—", "—", "—", c.get("status", "")])
                continue
            rows.append([c["id"], "%s A, %s°" % (f(c["I_rms"]), f(c["gamma"], 3)),
                         pct(max(abs(c["err_psi_d_pct_of_abs_psi"]),
                                 abs(c["err_psi_q_pct_of_abs_psi"])), 3),
                         pct(c["err_T_pct"], 3), pct(c["err_T_psi_pct"], 3),
                         "%s / %s" % (f(c["ripple_int_pct"], 3), f(c["ripple_fem_pct"], 3)),
                         ok(c["pass"]) + ("" if c["ripple_ok"] else " (ripple ✘)")])
        a("**%s** — targets ψ ≤ 0.5 %%, T ≤ 1 %%, ripple ≤ max(0.5 pp, 10 %%)" % M)
        a("")
        a(table(["point", "I, γ", "max ψ err", "T err (T-map)", "T err (from ψ)",
                 "ripple int / FEM %", "verdict"], rows))
        a("")
    a("## Off-grid checks — losses (trajectory interpolation vs direct FEM)")
    a("")
    for M in Ms:
        rows = []
        for c in recs[M]["checks"]["loss_offgrid"]:
            if "P_fem_W" not in c:
                rows.append([c["id"], "—", "—", "—", "—", c.get("status", "")])
                continue
            if c.get("refused"):
                verdict = "refused (outside the trajectory domain)"
            else:
                verdict = ok(c.get("pass"))
            err = pct(c.get("err_total_pct"))
            if c.get("err_total_coil_corrected_pct") is not None:
                err += " → %s Cu-T corr." % pct(c["err_total_coil_corrected_pct"])
            rows.append([c["id"], "%s rpm, %s A, %s°" % (f(c["rpm"], 5), f(c["I"]),
                                                         f(c["gamma_fem"], 3)),
                         f(c["P_fem_W"]), f(c.get("P_int_W")), err, verdict])
        a("**%s** — target total loss ≤ 5 %%" % M)
        a("")
        a(table(["point", "n, I, γ (FEM)", "FEM W", "passport W", "error", "verdict"], rows))
        a("")
        notes = [c["id"] + ": " + c["temperature_note"] for c in recs[M]["checks"]["loss_offgrid"]
                 if c.get("temperature_note")]
        for n_ in notes:
            a("- " + n_)
        if notes:
            a("")
    a("Audit / duty rows sit at γ = 10° (off the MTPA trajectory): their error includes "
      "the trajectory-reuse limit (spec P10).")
    a("")
    a("## Independent torque (B1), static window (P05), time step (B4)")
    a("")
    rows = []
    for M in Ms:
        for k, d in (recs[M]["checks"]["independent_torque_B1_window_P05"] or {}).items():
            rows.append([M, k, f(d["T_coulomb_full_Nm"], 6), f(d["T_terminal_work_full_Nm"], 6),
                         pct(d["err_coulomb_vs_terminal_pct"], 3),
                         pct(d["err_window60_vs_full_pct"], 3),
                         pct(d["psi_d_window60_vs_full_pct"], 3),
                         "%s / %s" % (f(d["ripple_window60_pct"], 3), f(d["ripple_full_pct"], 3))])
    a(table(["", "point", "T Coulomb (full period)", "T terminal work", "Coulomb vs work",
             "60° window vs full", "ψd 60° vs full", "ripple 60° / full %"], rows))
    a("")
    rows = []
    for M in Ms:
        ts = recs[M]["checks"]["time_step_B4"]
        if ts:
            rows.append([M, "%s vs %s" % tuple(ts["steps"]), pct(ts["T_pct"], 3),
                         pct(ts["P_total_pct"]),
                         " · ".join("%s %s" % (k.replace("P_", "").replace("_W", ""), pct(v))
                                    for k, v in ts["groups_pct"].items())])
    a(table(["", "steps/period", "T", "total loss", "groups"], rows))
    a("")
    a("## Saved duties re-solved today vs the stored duty result")
    a("")
    rows = []
    for M in Ms:
        for k, d in recs[M]["checks"]["duties"].items():
            rows.append([M, k, f(d["today"]["T_Nm"]), f(d["stored"].get("T_em_avg_Nm")),
                         pct(d["T_pct"]), f(d["today"]["P_loss_W"]),
                         f(d["stored"].get("P_loss_total_W")), pct(d["P_loss_pct"]),
                         d.get("stored_saved_at")])
    a(table(["", "duty", "T today", "T stored", "ΔT", "loss today W", "loss stored W",
             "Δloss", "stored at"], rows))
    a("")
    a("Stored duties were solved by older baselines (torque method, mesher, eddy settle); "
      "the passport uses today's.")
    a("")
    if any(recs[M]["checks"].get("audit") for M in Ms):
        a("## Audit 2026-09-30 comparison (L12)")
        a("")
        au = recs["L12"]["checks"]["audit"]
        rows = [[f(x["I"]), f(x["gamma_mtpa_audit"], 3), f(x["gamma_mtpa_passport"], 3),
                 f(x["T_audit"]), f(x["T_passport"]), pct(x["T_pct"])] for x in au["static"]]
        a(table(["I A", "γ_MTPA audit", "γ_MTPA passport", "T audit", "T passport",
                 "ΔT"], rows))
        a("")
        a("Audit static runs: card magnet temperature (120 °C), older solver (Maxwell-based mean, "
          "Triangle mesher); passport: %g °C magnets, Coulomb, Netgen." %
          recs["L12"]["inputs"]["temperatures"]["hot_magnet_c"])
        a("")
    # ── demag ─────────────────────────────────────────────────────────────
    a("## Demag safe surface (TDM, rated speed, owner steadiness rule)")
    a("")
    rows = []
    for M in Ms:
        dm = recs[M].get("demag") or {}
        for k, p in (dm.get("probes") or {}).items():
            d = p.get("demag") or {}
            st_ = ("pass (T≈0, §1 floor)" if p.get("settled_under_floor")
                   else ok(p.get("settled")))
            rows.append([M, k, f(p["fI"], 3), f(p["gamma"], 3), f(d.get("br_kept_vol_pct"), 5),
                         f(d.get("per_magnet_spread_pct"), 3), st_])
    a(table(["", "probe", "I/I0", "γ", "Br kept %", "per-magnet spread %", "steady"], rows))
    a("")
    for M in Ms:
        dm = recs[M].get("demag") or {}
        a("%s: 99.5 %% Br knee on the MTPA line at %s A rms%s." % (
            M, f(dm.get("I_knee_rms")),
            " — already below the first probe (I0)" if dm.get("below_first_probe") else ""))
    a("")
    # ── budget ────────────────────────────────────────────────────────────
    a("## Budget")
    a("")
    rows = [[M, budget[M]["runs"], budget[M]["failed"], f(budget[M]["container_wall_h"], 3),
             f(budget[M]["fem_cpu_h"], 3)] for M in Ms]
    a(table(["", "FEM runs (ok)", "failed", "container wall h", "Σ single-thread FEM h"], rows))
    a("")
    a("Server sandbox, deployed image `deploy-api` (3ba0f9b), 6 single-thread workers in one "
      "container (`--cpus 8`, nice 19, ionice idle), one container at a time.")
    a("")
    return "\n".join(L)
