"""The pilot's computation stages (driven by scripts/passport_pilot_d40.py).

Each stage plans jobs from the frozen snapshot and the state of the previous
stages, runs them through the runner's process pool and records what it
decided (angles, levels, limits) in the machine state, so the record can say
WHY each point exists.  Every decision rule is the spec's, named inline.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Tuple

from motor_ai_sim.passport_v1 import jobs as J

#: Current levels of the static grid, I/I0 (spec 3.1).
LEVELS = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 2.5)
#: Field-weakening arm (spec 3.1 item 2) + the d-axis anchor (P04).
FW_ARM = (35.0, 50.0, 65.0, 80.0)
D_AXIS_ANCHOR = 90.0
#: MTPA bracket step (spec 3.1 item 1).
MTPA_STEP = 5.0
#: Cold MTPA line levels (spec 8.1: 4 levels × 3 gamma) and cold FW checks (P21).
COLD_LEVELS = (0.25, 0.5, 1.0, 1.5)
COLD_FW = (50.0, 80.0)
#: Retention threshold of the safe surface: the existing tuner's knee rule
#: (web/src/lib/motorScaling.ts maxCurrent(): first 99.5 % crossing).
RETENTION_KNEE_PCT = 99.5


def _hot(snap):
    t = snap["temperatures"]
    return t["hot_magnet_c"], t["hot_coil_c"]


def _I0(snap) -> float:
    return float(snap["rated_duty"]["current_arms"])


def _n0(snap) -> float:
    return float(snap["rated_duty"]["rpm"])


def _base(snap, st, static: bool = True):
    b = J.base_kwargs(snap, daxis_deg=st.get("daxis_deg"))
    if static and st.get("gap_layers_static") is not None:
        # The deployed gap rule's level for the STATIC sampling of THIS
        # machine (see stage_probe): one level for the whole map, so no two
        # map points sit on different gap meshes.  The rule stays on.
        b["gap_layers"] = float(st["gap_layers_static"])
    return b


def _g(x: float) -> str:
    return ("%+.3f" % x).rstrip("0").rstrip(".")


# ─────────────────────────────────────────────────────────────────────────────
#  calib / probe
# ─────────────────────────────────────────────────────────────────────────────

def stage_calib(R, snap, st) -> None:
    """Hot no-load, one full period at cogging-quality sampling.  The first
    solve of the machine: it MEASURES the d-axis (daxis_deg=None), which is
    then pinned for every other job (one frame for hot and cold)."""
    Tm, Tc = _hot(snap)
    b = J.base_kwargs(snap, daxis_deg=None)
    j = J.static_job("hot_noload", b, I_rms=0.0, gamma_deg=0.0, magnet_temp_c=Tm,
                     coil_temp_c=Tc, rpm=_n0(snap), n_periods=1.0,
                     steps=J.STATIC_STEPS_PER_PERIOD, purpose="cogging_quality",
                     meta={"set": "hot", "role": "no-load anchor + d-axis calibration"})
    r = R.run([j])["hot_noload"]["r"]
    st["daxis_deg"] = float(r["daxis_deg"])
    st["daxis_source"] = r.get("daxis_source")
    print(f"  d-axis {st['daxis_deg']} ({st['daxis_source']}); psi_d {r['psi_d_Wb']}")


def stage_probe(R, snap, st) -> None:
    """Timings and the gap rule at the rated point before the grid."""
    Tm, Tc = _hot(snap)
    b = _base(snap, st)
    I0, n0 = _I0(snap), _n0(snap)
    steps = int(snap["rated_duty"]["steps_per_period"])
    jl = [
        J.static_job("probe_static60", b, I_rms=I0, gamma_deg=5.0, magnet_temp_c=Tm,
                     coil_temp_c=Tc, rpm=n0, meta={"set": "probe"}),
        J.static_job("probe_static_full", b, I_rms=I0, gamma_deg=5.0, magnet_temp_c=Tm,
                     coil_temp_c=Tc, rpm=n0, n_periods=1.0, meta={"set": "probe"}),
        J.loss_job("probe_loss", b, I_rms=I0, gamma_deg=5.0, rpm=n0, magnet_temp_c=Tm,
                   coil_temp_c=Tc, steps=steps, meta={"set": "probe"}),
    ]
    out = R.run(jl)
    st["probe"] = {k: {"wall_s": v.get("wall_s"),
                       "T": v["r"].get("T_avg_Nm"),
                       "gap_layers": v["r"].get("gap_layers_effective"),
                       "gap_refinement": v["r"].get("gap_refinement"),
                       "eddy_method": v["r"].get("eddy_method"),
                       "P_loss": v["r"].get("P_loss_total_W")}
                   for k, v in out.items()}
    for k, v in st["probe"].items():
        print("  ", k, v)
    # ONE gap level for the whole STATIC map: the level the deployed gap rule
    # (fem_solver_2d._solve_with_gap_refinement, owner 2026-09-30) needed on
    # the full-period static solve at the map's own angular sampling (144
    # steps/period) — the 60° window alone sees too few positions to trip
    # it.  Loss runs keep the duty's mesh.gapLayers with the rule on (each
    # records the level it ended at).
    duty_gl = float(snap["mesh"]["gap_layers"])
    gl_full = float(out["probe_static_full"]["r"].get("gap_layers_effective") or duty_gl)
    gl_60 = float(out["probe_static60"]["r"].get("gap_layers_effective") or duty_gl)
    st["gap_layers_static"] = max(duty_gl, gl_full, gl_60)
    st["gap_layers_reason"] = (
        "duty mesh.gapLayers = %g/side.  Static map: %g/side — the deployed gap "
        "rule refined the full-period static probe (144 steps/period) to %g/side "
        "(Coulomb ring self-check gate 5 %% of the ripple scale); the 60° window "
        "passed at %g.  Loss runs: duty %g/side, rule on (the TDM rated probe "
        "ended at %g)." % (duty_gl, st["gap_layers_static"], gl_full, gl_60, duty_gl,
                           float(out["probe_loss"]["r"].get("gap_layers_effective")
                                 or duty_gl)))
    print("  gap layers:", st["gap_layers_reason"])


# ─────────────────────────────────────────────────────────────────────────────
#  hot static grid (spec 3.1–3.3, P04)
# ─────────────────────────────────────────────────────────────────────────────


def stage_hot(R, snap, st) -> None:
    """Hot static grid at the design magnet temperature (spec 8.1 HOT):
    7 levels x (MTPA bracket + FEM-confirmed vertex) + FW arm {35,50,65,80}
    + the d-axis anchor (90°) + gamma = -5° at the two highest levels; the
    hot no-load point is the zero-current anchor."""
    from motor_ai_sim.passport_v1 import psimap as PM
    Tm, Tc = _hot(snap)
    b = _base(snap, st)
    I0, n0 = _I0(snap), _n0(snap)

    def job(I, g, role):
        jid = "hot_I%.4g_g%s" % (I / I0, _g(g))
        return J.static_job(jid, b, I_rms=I, gamma_deg=g, magnet_temp_c=Tm,
                            coil_temp_c=Tc, rpm=n0,
                            meta={"set": "hot", "fI": I / I0, "gamma": g, "role": role})

    # zero-current anchor at the grid's gap level (the calibration run may
    # differ only in gap layers; re-solved so the map is one mesh)
    fixed: List[Dict[str, Any]] = [
        J.static_job("hot_I0_anchor", b, I_rms=0.0, gamma_deg=0.0, magnet_temp_c=Tm,
                     coil_temp_c=Tc, rpm=n0,
                     meta={"set": "hot", "fI": 0.0, "gamma": 0.0, "role": "anchor"})]
    for fI in LEVELS:
        I = fI * I0
        for g in FW_ARM:
            fixed.append(job(I, g, "fw"))
        fixed.append(job(I, D_AXIS_ANCHOR, "d-axis anchor"))
    for fI in LEVELS[-2:]:
        fixed.append(job(fI * I0, -5.0, "gamma=-5 (saturation ridge shift)"))
    mtpa_rec = []
    g_hat = 0.0
    pending = list(fixed)
    for k, fI in enumerate(LEVELS):
        I = fI * I0
        gs = [round(g_hat - MTPA_STEP, 3), round(g_hat, 3), round(g_hat + MTPA_STEP, 3)]
        jl = [job(I, g, "mtpa bracket") for g in gs]
        out = R.run(pending + jl)
        pending = []
        while True:
            # bracket samples only (the FW arm is too coarse for the ridge)
            near = {round(float(v["meta"]["gamma"]), 3): float(v["r"]["T_avg_Nm"])
                    for v in R.done.values()
                    if v["meta"].get("set") == "hot"
                    and abs(v["meta"].get("fI", -1) - fI) < 1e-9
                    and v["meta"].get("role", "").startswith(("mtpa bracket", "gamma=-5"))}
            nxt = PM.bracket_next(list(near), list(near.values()), MTPA_STEP)
            if nxt is None:
                break
            gs.append(nxt)
            R.run([job(I, nxt, "mtpa bracket extension")])
        gv, Tv, ok = PM.parabola_vertex(list(near), list(near.values()))
        gv = round(gv, 2)
        conf = job(I, gv, "mtpa vertex (FEM-confirmed)")
        # the vertex is confirmed in the same batch as the next level's bracket
        pending = [conf]
        mtpa_rec.append({"fI": fI, "I": I, "gamma_mtpa": gv, "T_parabola": Tv,
                         "conf_id": conf["id"], "bracket": sorted(near),
                         "T_bracket": [near[g] for g in sorted(near)],
                         "parabola_bracketed": ok})
        g_hat = gv
    R.run(pending)
    for m in mtpa_rec:
        T_conf = float(R.done[m["conf_id"]]["r"]["T_avg_Nm"])
        m["T_fem_vertex"] = T_conf
        m["vertex_vs_parabola_pct"] = 100.0 * (T_conf - m["T_parabola"]) / m["T_parabola"]
        m["vertex_ge_samples"] = bool(T_conf >= max(m["T_bracket"]) - 1e-12)
        print("  level %.2f I0: gamma_MTPA %.2f°, T %.6g (parabola %.6g)"
              % (m["fI"], m["gamma_mtpa"], T_conf, m["T_parabola"]), flush=True)
    st["hot_mtpa"] = mtpa_rec


# ─────────────────────────────────────────────────────────────────────────────
#  helpers shared by the later stages
# ─────────────────────────────────────────────────────────────────────────────

def pole_pairs(snap) -> int:
    return int(round(float(snap["geometry"]["num_poles"]))) // 2


def hot_map(R, snap, st):
    from motor_ai_sim.passport_v1 import psimap as PM
    pts = PM.points_from_records(R.done.values(), "hot")
    return PM.PsiMap.build(pts, pole_pairs(snap), mtpa=PM.mtpa_table(st["hot_mtpa"]))


def R_hot(R, st) -> float:
    m = st["hot_mtpa"][LEVELS.index(1.0)]
    return float(R.done[m["conf_id"]]["r"]["R_phase_ohm"])


def bus(snap, which: str) -> float:
    return float(snap["battery"]["v_" + which])


M_MARGIN = 0.95          # placeholder (spec P24) — see passport_v1.PENDING_DEFAULTS
TRAJECTORY_BUS = "nom"   # the loss trajectory runs at v_nom ("typical map", spec 3.5)


# ─────────────────────────────────────────────────────────────────────────────
#  demag retention surface (B7)
# ─────────────────────────────────────────────────────────────────────────────

#: Demag safe-surface probes (I/I0, gamma or "mtpa"), solved on the TDM path
#: at rated speed — the path that implements the owner's demag steadiness
#: rule (per-magnet mean + observable drift + full rotor-image cycle).  The
#: magnetostatic demag path stops after one pre-pass with an incomplete image
#: history (measured on the L12 probe: "demag NOT settled ... 0 of 7
#: pre-pass periods"), so it is not used for the safe surface.
DEMAG_PROBES = ((1.0, "mtpa"), (1.5, "mtpa"), (2.0, "mtpa"), (2.5, "mtpa"),
                (1.5, 80.0), (2.5, 80.0), (1.0, 90.0))


def stage_demag(R, snap, st) -> None:
    """Retention (Br volume, worst-element corner diagnostic, steadiness) at
    the safe-surface probes; the knee on the MTPA line."""
    Tm, Tc = _hot(snap)
    b = _base(snap, st, static=False)
    I0, n0 = _I0(snap), _n0(snap)
    steps = int(snap["rated_duty"]["steps_per_period"])
    jl = []
    mt = {round(m["fI"], 6): m for m in st["hot_mtpa"]}
    for fI, g in DEMAG_PROBES:
        gg = mt[round(fI, 6)]["gamma_mtpa"] if g == "mtpa" else float(g)
        jid = "dm_I%.4g_g%s" % (fI, "mtpa" if g == "mtpa" else _g(gg))
        jl.append(J.loss_job(jid, b, I_rms=fI * I0, gamma_deg=gg, rpm=n0,
                             magnet_temp_c=Tm, coil_temp_c=Tc, steps=steps,
                             meta={"set": "demag", "fI": fI, "gamma": gg,
                                   "on_mtpa": g == "mtpa",
                                   "role": "demag safe-surface probe (TDM, rated speed)"}))
    out = R.run(jl)
    rows = []
    for j in jl:
        if not j["meta"]["on_mtpa"] or j["id"] not in out:
            continue
        ds = out[j["id"]]["r"].get("demag_summary") or {}
        rows.append((j["meta"]["fI"] * I0, float(ds.get("br_kept_vol_pct", 100.0))))
    rows.sort()
    knee = None
    prev = (0.0, 100.0)
    for I, k in rows:
        if k < RETENTION_KNEE_PCT:
            I_a, k_a = prev
            knee = I_a + (RETENTION_KNEE_PCT - k_a) * (I - I_a) / (k - k_a) if k != k_a else I
            break
        prev = (I, k)
    st["demag_knee"] = {"threshold_br_kept_pct": RETENTION_KNEE_PCT,
                        "rule": "first 99.5 % Br-retention crossing on the hot MTPA line "
                                "(the existing tuner's knee rule)",
                        "mtpa_retention": rows,
                        "I_knee_rms": knee,
                        "below_first_probe": bool(rows and rows[0][1] < RETENTION_KNEE_PCT),
                        "probes": {j["id"]: {"fI": j["meta"]["fI"], "gamma": j["meta"]["gamma"],
                                             "demag": (out.get(j["id"]) or {}).get("r", {})
                                             .get("demag_summary"),
                                             "settled": (out.get(j["id"]) or {}).get("r", {})
                                             .get("demag_settled")}
                                   for j in jl}}
    print("  demag knee:", st["demag_knee"])


# ─────────────────────────────────────────────────────────────────────────────
#  cold 20 °C set (spec 8.1, 8.2, 8.7, P21)
# ─────────────────────────────────────────────────────────────────────────────

def stage_cold(R, snap, st) -> None:
    from motor_ai_sim.passport_v1 import psimap as PM
    T20 = float(snap["temperatures"]["cold_c"])
    b = _base(snap, st)
    I0, n0 = _I0(snap), _n0(snap)
    hm = hot_map(R, snap, st)

    def job(I, g, role, **kw):
        jid = "cold_I%.4g_g%s" % (I / I0, _g(g))
        return J.static_job(jid, b, I_rms=I, gamma_deg=g, magnet_temp_c=T20,
                            coil_temp_c=T20, rpm=n0,
                            meta={"set": "cold", "fI": I / I0, "gamma": g, "role": role},
                            **kw)
    fixed = [
        J.static_job("cold_noload", b, I_rms=0.0, gamma_deg=0.0, magnet_temp_c=T20,
                     coil_temp_c=T20, rpm=n0, n_periods=1.0, purpose="cogging_quality",
                     meta={"set": "cold", "fI": 0.0, "gamma": 0.0,
                           "role": "anchor (no-load, full period, cogging quality)"}),
        J.static_job("cold_probe2A", b, I_rms=2.0, gamma_deg=0.0, magnet_temp_c=T20,
                     coil_temp_c=T20, rpm=n0,
                     meta={"set": "cold_small", "fI": 2.0 / I0, "gamma": 0.0,
                           "role": "small-signal (bench) probe, I = 2 A"}),
    ]
    for g in COLD_FW:
        fixed.append(job(I0, g, "cold fw check (P21)"))
    brk = []
    for fI in COLD_LEVELS:
        gh = round(hm.gamma_mtpa(fI * I0), 2)
        for g in (gh - MTPA_STEP, gh, gh + MTPA_STEP):
            brk.append(job(fI * I0, round(g, 2), "mtpa bracket"))
    R.run(fixed + brk)
    cold = []
    pend = []
    for fI in COLD_LEVELS:
        while True:
            near = {round(float(v["meta"]["gamma"]), 3): float(v["r"]["T_avg_Nm"])
                    for v in R.done.values() if v["meta"].get("set") == "cold"
                    and abs(v["meta"].get("fI", -1) - fI) < 1e-9
                    and v["meta"].get("role", "").startswith("mtpa bracket")}
            nxt = PM.bracket_next(list(near), list(near.values()), MTPA_STEP)
            if nxt is None:
                break
            R.run([job(fI * I0, nxt, "mtpa bracket extension")])
        gv, Tv, ok = PM.parabola_vertex(list(near), list(near.values()))
        gv = round(gv, 2)
        cj = job(fI * I0, gv, "mtpa vertex (FEM-confirmed)")
        pend.append(cj)
        cold.append({"fI": fI, "I": fI * I0, "gamma_mtpa": gv, "T_parabola": Tv,
                     "conf_id": cj["id"], "bracket": sorted(near),
                     "T_bracket": [near[g] for g in sorted(near)]})
    # short-circuit / characteristic current: psi_d = 0 on the d-axis.
    # Prediction from the hot d-axis anchors (chord Ld) and the cold psi_PM.
    nl = R.run([fixed[0]])["cold_noload"]["r"]
    psi_pm_c = float(nl["psi_d_Wb"])
    hot_axis = sorted((p for p in PM.points_from_records(R.done.values(), "hot")
                       if abs(p["gamma"] - 90.0) < 1e-6), key=lambda p: p["I"])
    hot_nl = R.done.get("hot_I0_anchor") or R.done.get("hot_noload")
    psi_pm_h = float(hot_nl["r"]["psi_d_Wb"])
    top = hot_axis[-1]
    Ld_chord = (top["psi_d"] - psi_pm_h) / top["i_d"]
    I_pred = abs(psi_pm_c / Ld_chord) / PM.SQ2
    sc = [J.static_job("cold_sc_%d" % k, b, I_rms=I_pred * f, gamma_deg=90.0,
                       magnet_temp_c=T20, coil_temp_c=T20, rpm=n0,
                       meta={"set": "cold_sc", "fI": I_pred * f / I0, "gamma": 90.0,
                             "role": "short-circuit characteristic current"})
          for k, f in enumerate((0.85, 1.0, 1.15))]
    R.run(pend + sc)
    for c in cold:
        c["T_fem_vertex"] = float(R.done[c["conf_id"]]["r"]["T_avg_Nm"])
        c["vertex_ge_samples"] = bool(c["T_fem_vertex"] >= max(c["T_bracket"]) - 1e-12)
    st["cold_mtpa"] = cold
    st["cold_sc_plan"] = {"psi_pm_cold": psi_pm_c, "Ld_chord_hot_axis_H": Ld_chord,
                          "I_pred_rms": I_pred}
    print("  cold MTPA:", [(c["fI"], c["gamma_mtpa"], c["T_fem_vertex"]) for c in cold])


#: Small-signal (bench / LCR-equivalent) probe current [A rms] — the same
#: 2 A the solver's bench Ld/Lq probe uses (routes.simulation bench_ldq).
SMALL_SIGNAL_A = 2.0


def stage_extra(R, snap, st) -> None:
    """Small-signal differential Ld / Lq at I ≈ 0, hot and cold (spec 8.7
    "bench small-signal at I≈0", P20): symmetric ±d and ±q pairs at 2 A, so
    L = Δpsi/Δi with psi_PM cancelled.  Added after the L12 pilot showed the
    solver's frozen-permeability ``inc_ldq`` (secant ν) at ~2× the map's
    true differential — it is not the bench value."""
    b = _base(snap, st)
    I0, n0 = _I0(snap), _n0(snap)
    Tm, Tc = _hot(snap)
    T20 = float(snap["temperatures"]["cold_c"])
    jl = []
    for tag, mt, ct in (("hot", Tm, Tc), ("cold", T20, T20)):
        for g in (0.0, 180.0, 90.0, -90.0):
            jl.append(J.static_job("%s_ss_g%s" % (tag, _g(g)), b, I_rms=SMALL_SIGNAL_A,
                                   gamma_deg=g, magnet_temp_c=mt, coil_temp_c=ct, rpm=n0,
                                   meta={"set": tag + "_ss", "fI": SMALL_SIGNAL_A / I0,
                                         "gamma": g,
                                         "role": "small-signal ±d/±q pair (bench equivalent)"}))
    # k_state decomposition at the rated trajectory point: the same settled
    # TDM run with the demag model OFF isolates the demag share of
    # (operating torque / virgin map); the remainder is the coupled eddy /
    # rotor-reaction share.
    lp = st["loss_plan"]
    rp = [p for p in lp["points"] if p.get("id") and abs(p["rpm"] - n0) < 1e-6
          and abs(p["I"] - I0) < 1e-6][0]
    jl.append(J.loss_job("xs_rated_nodemag", _base(snap, st, static=False), I_rms=I0,
                         gamma_deg=round(rp["gamma"], 3), rpm=n0, magnet_temp_c=Tm,
                         coil_temp_c=Tc, steps=int(snap["rated_duty"]["steps_per_period"]),
                         demag=False,
                         meta={"set": "state_split", "rpm": n0, "I": I0,
                               "gamma": round(rp["gamma"], 3),
                               "role": "rated trajectory point, demag OFF (k_state split)"}))
    out = R.run(jl)
    ss = {}
    for tag in ("hot", "cold"):
        q1, q2 = out["%s_ss_g+0" % tag]["r"], out["%s_ss_g+180" % tag]["r"]
        d1, d2 = out["%s_ss_g+90" % tag]["r"], out["%s_ss_g-90" % tag]["r"]
        ss[tag] = {
            "Ld_H": (d1["psi_d_Wb"] - d2["psi_d_Wb"]) / (d1["i_d_A"] - d2["i_d_A"]),
            "Lq_H": (q1["psi_q_Wb"] - q2["psi_q_Wb"]) / (q1["i_q_A"] - q2["i_q_A"]),
            "I_rms": SMALL_SIGNAL_A,
            "method": "symmetric ±2 A pairs on the d and q axes, Δpsi/Δi (true small-"
                      "signal differential at I ≈ 0; bench/LCR equivalent)",
            "runs": [j["id"] for j in jl if j["id"].startswith(tag)]}
    st["small_signal"] = ss
    print("  small-signal:", {k: (1e6 * v["Ld_H"], 1e6 * v["Lq_H"]) for k, v in ss.items()})


# ─────────────────────────────────────────────────────────────────────────────
#  loss trajectory (spec §4) + mechanical losses (§5.6)
# ─────────────────────────────────────────────────────────────────────────────

def _peak_current(snap, st) -> Tuple[float, str]:
    pk = snap.get("peak_duty")
    if pk:
        return float(pk["current_arms"]), "owner peak duty"
    rule = (snap.get("owner_inputs") or {}).get("peak_rule")
    if not rule:
        raise ValueError("no peak duty and no peak rule for %s" % snap["tag"])
    return (float(rule["ratio"]) * _I0(snap),
            "ASSUMPTION — no owner peak duty: %s" % rule["source"])


def stage_loss(R, snap, st) -> None:
    from motor_ai_sim.passport_v1 import psimap as PM
    from motor_ai_sim.passport_v1 import losses as LS
    Tm, Tc = _hot(snap)
    b = _base(snap, st, static=False)
    I0, n0 = _I0(snap), _n0(snap)
    steps = int(snap["rated_duty"]["steps_per_period"])
    hm = hot_map(R, snap, st)
    Rh = R_hot(R, st)
    vlim = PM.v_phase_limit(bus(snap, TRAJECTORY_BUS), M_MARGIN)
    I_pk, I_pk_src = _peak_current(snap, st)
    mech0 = LS.mech_losses(rpm=n0, bearings=snap["bearings"], geometry=snap["geometry"],
                           temp_c=None)
    n_mech = float(mech0.get("speed_limit_rpm") or float("inf"))
    n_elec = hm.max_speed(Rh, vlim, I_pk)
    n_max = min(n_mech, n_elec)
    speeds = sorted({round(f * n0, 3) for f in (0.25, 0.5, 1.0, 1.5)} | {round(n_max, 0)})
    speeds = [n for n in speeds if n <= n_max + 1e-6]
    currents = sorted({round(0.5 * I0, 6), round(I0, 6), round(I_pk, 6)})
    plan = []
    jl = []
    for n in speeds:
        for I in currents:
            g, how = hm.operating_gamma(I, n, Rh, vlim)
            row = {"rpm": n, "I": I, "gamma": g, "mode": how}
            if g is not None:
                jid = "loss_n%.0f_I%.4g" % (n, I / I0)
                row["id"] = jid
                jl.append(J.loss_job(jid, b, I_rms=I, gamma_deg=round(g, 3), rpm=n,
                                     magnet_temp_c=Tm, coil_temp_c=Tc, steps=steps,
                                     meta={"set": "loss", "rpm": n, "I": I,
                                           "gamma": round(g, 3), "mode": how}))
            plan.append(row)
    st["loss_plan"] = {
        "bus": TRAJECTORY_BUS, "v_dc": bus(snap, TRAJECTORY_BUS), "m": M_MARGIN,
        "v_phase_limit_V": vlim, "R_hot_ohm": Rh,
        "I_peak_rms": I_pk, "I_peak_source": I_pk_src,
        "n_mech_limit_rpm": n_mech, "n_mech_source":
            "bearing speed limit (mech_losses / bearings library); critical speed and "
            "rotor stress not computed in stage 1",
        "n_elec_limit_rpm": n_elec, "n_max_rpm": n_max,
        "speeds": speeds, "currents": currents, "points": plan}
    st["mech"] = {str(n): LS.mech_losses(rpm=n, bearings=snap["bearings"],
                                         geometry=snap["geometry"], temp_c=None)
                  for n in speeds}
    print("  loss plan:", [(p["rpm"], round(p["I"], 2), p["gamma"] and round(p["gamma"], 2),
                            p["mode"][:12]) for p in plan], flush=True)
    R.run(jl)


# ─────────────────────────────────────────────────────────────────────────────
#  independent checks (spec §9, B1, P05, B4) and duty / audit comparisons
# ─────────────────────────────────────────────────────────────────────────────

#: Audit 2026-09-30 Ø40 base (CIANO14 40 new / L12, old preset): I0, gamma.
AUDIT_I0 = 40.659
AUDIT_GAMMA = 10.0


def stage_checks(R, snap, st) -> None:
    from motor_ai_sim.passport_v1 import psimap as PM
    from motor_ai_sim.passport_v1 import losses as LS
    Tm, Tc = _hot(snap)
    b = _base(snap, st)
    bl = _base(snap, st, static=False)
    I0, n0 = _I0(snap), _n0(snap)
    steps = int(snap["rated_duty"]["steps_per_period"])
    hm = hot_map(R, snap, st)
    lp = st["loss_plan"]
    Rh, vlim, I_pk = lp["R_hot_ohm"], lp["v_phase_limit_V"], lp["I_peak_rms"]

    def sj(jid, I, g, role, **kw):
        return J.static_job(jid, b, I_rms=I, gamma_deg=round(g, 3), magnet_temp_c=Tm,
                            coil_temp_c=Tc, rpm=n0,
                            meta={"set": "check", "fI": I / I0, "gamma": round(g, 3),
                                  "role": role}, **kw)
    # (a) static off-grid checks (spec §9: 4 near the ridge, 2 deep FW, 2 top)
    st_pts = [
        ("chk_ridge_0.375", 0.375 * I0, hm.gamma_mtpa(0.375 * I0), "MTPA ridge between levels"),
        ("chk_ridge_0.875", 0.875 * I0, hm.gamma_mtpa(0.875 * I0), "MTPA ridge between levels"),
        ("chk_ridge_1.25", 1.25 * I0, hm.gamma_mtpa(1.25 * I0), "MTPA ridge between levels"),
        ("chk_ridge_1.75", 1.75 * I0, hm.gamma_mtpa(1.75 * I0), "MTPA ridge between levels"),
        ("chk_fw_1.25_72.5", 1.25 * I0, 72.5, "deep field weakening"),
        ("chk_fw_2.25_57.5", 2.25 * I0, 57.5, "deep field weakening"),
        ("chk_top_2.4_20", 2.4 * I0, 20.0, "highest current"),
        ("chk_top_2.25_42.5", 2.25 * I0, 42.5, "highest current"),
    ]
    pk = snap.get("peak_duty")
    n_pk = float(pk["rpm"]) if pk else n0
    g_pk, how_pk = hm.operating_gamma(I_pk, n_pk, Rh, vlim)
    st_pts.append(("chk_peak", I_pk, g_pk if g_pk is not None else hm.gamma_mtpa(I_pk),
                   "peak current at its operating angle (%s)" % how_pk))
    rd = snap["rated_duty"]
    st_pts.append(("chk_duty_rated_g", rd["current_arms"], rd["gamma_deg"],
                   "rated duty current at the duty's gamma"))
    if snap["tag"] == "L12":
        for f in (0.5, 1.0, 1.3):
            st_pts.append(("chk_audit_I%.1f" % f, AUDIT_I0 * f, AUDIT_GAMMA,
                           "audit 2026-09-30 corner I x%.1f (gamma 10°)" % f))
    jl = [sj(j, I, g, role) for j, I, g, role in st_pts]
    # (b) full-period reference at the rated MTPA vertex and at peak: P05
    #     (60° window vs full period) and B1 (terminal-work mean vs Coulomb)
    m1 = st["hot_mtpa"][LEVELS.index(1.0)]
    jl.append(sj("full_rated_mtpa", I0, m1["gamma_mtpa"], "full period @ rated MTPA vertex",
                 n_periods=1.0))
    jl.append(sj("full_peak", I_pk, g_pk if g_pk is not None else hm.gamma_mtpa(I_pk),
                 "full period @ peak", n_periods=1.0))
    # (c) loss off-grid checks on the trajectory (spec §4 validation)
    lchk = [("lchk_0.75n_0.75I", 0.75 * n0, 0.75 * I0, "mid loss grid"),
            ("lchk_0.35n_peak", 0.35 * n0, I_pk, "low speed, peak current"),
            ("lchk_1.25n_mid", 1.25 * n0, 0.5 * (I0 + I_pk), "between speed and current rows")]
    lplan = []
    for jid, n, I, role in lchk:
        g, how = hm.operating_gamma(I, n, Rh, vlim)
        lplan.append({"id": jid, "rpm": n, "I": I, "gamma": g, "mode": how, "role": role})
        if g is not None:
            jl.append(J.loss_job(jid, bl, I_rms=I, gamma_deg=round(g, 3), rpm=n,
                                 magnet_temp_c=Tm, coil_temp_c=Tc, steps=steps,
                                 meta={"set": "loss_check", "rpm": n, "I": I,
                                       "gamma": round(g, 3), "mode": how, "role": role}))
    # (d) time step (B4): the rated trajectory point at 72 steps
    g1, how1 = hm.operating_gamma(I0, n0, Rh, vlim)
    jl.append(J.loss_job("lchk_rated_72steps", bl, I_rms=I0, gamma_deg=round(g1, 3), rpm=n0,
                         magnet_temp_c=Tm, coil_temp_c=Tc, steps=72,
                         meta={"set": "loss_check", "rpm": n0, "I": I0,
                               "gamma": round(g1, 3), "mode": how1,
                               "role": "time-step check (72 vs %d steps)" % steps}))
    # (e) the saved duties, re-solved today with their own settings
    duties = [("duty_rated", snap["rated_duty"])]
    if snap.get("peak_duty"):
        duties.append(("duty_peak", snap["peak_duty"]))
    for jid, du in duties:
        bd = dict(bl)
        bd["component_mesh_mm"] = dict(du["mesh"].get("component_mesh_mm") or {})
        jl.append(J.loss_job(jid, bd, I_rms=du["current_arms"], gamma_deg=du["gamma_deg"],
                             rpm=du["rpm"], magnet_temp_c=du["magnet_temp_c"],
                             coil_temp_c=du["coil_temp_c"], steps=du["steps_per_period"],
                             end_winding_factor=du["end_winding_factor"],
                             meta={"set": "duty", "rpm": du["rpm"], "I": du["current_arms"],
                                   "gamma": du["gamma_deg"], "role": "saved duty re-solved"}))
    # (f) audit loss corners (L12 only; gamma 10°, off the MTPA trajectory)
    if snap["tag"] == "L12":
        for jid, n, I in (("audit_base", n0, AUDIT_I0), ("audit_rpm0.5", 0.5 * n0, AUDIT_I0),
                          ("audit_rpm1.5", 1.5 * n0, AUDIT_I0),
                          ("audit_I0.5", n0, 0.5 * AUDIT_I0), ("audit_I1.3", n0, 1.3 * AUDIT_I0)):
            jl.append(J.loss_job(jid, bl, I_rms=I, gamma_deg=AUDIT_GAMMA, rpm=n,
                                 magnet_temp_c=Tm, coil_temp_c=Tc, steps=steps,
                                 meta={"set": "audit", "rpm": n, "I": I,
                                       "gamma": AUDIT_GAMMA, "role": "audit corner"}))
    st["checks_plan"] = {"static": [{"id": j, "I": I, "gamma": g, "role": r}
                                    for j, I, g, r in st_pts],
                         "loss": lplan}
    for n in sorted({float(p["rpm"]) for p in lplan} | {n0, 0.5 * n0, 1.5 * n0, n_pk}):
        st["mech"].setdefault(str(n), LS.mech_losses(rpm=n, bearings=snap["bearings"],
                                                     geometry=snap["geometry"], temp_c=None))
    R.run(jl)
