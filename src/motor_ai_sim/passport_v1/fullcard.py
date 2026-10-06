"""The full passport card: stage 1 record + 3-D factors + mechanics +
coupled thermal + per-cooling thermal limits + demag current limit + PWM /
controller (stage 3), and the HTML cards.  Machine-agnostic (the spec names
the controller, variants, k_T rule and cooling studies).  Pure
post-processing of solved results (no FEM here)."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

import numpy as np

from motor_ai_sim.passport_v1 import card as C
from motor_ai_sim.passport_v1 import html as Hh
from motor_ai_sim.passport_v1 import losses as LS
from motor_ai_sim.passport_v1 import psimap as PM
from motor_ai_sim.passport_v1 import stage3 as S3

PEND = "default pending owner"


def _load(p: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  stage 2 — 3-D factors
# ─────────────────────────────────────────────────────────────────────────────

def stage2_block(full_dir: Path, repo: Path, L_mm: float,
                 k_T_rule: str = "stage_b_inherited") -> Dict[str, Any]:
    import re
    ee = _load(repo / "config" / "end_effect_3d.json") or {}
    sb = ee.get("stage_b") or {}
    lsf = ((sb.get("long_stack_honesty_test") or {}).get("the_inductive_end_effect_factor") or {})
    a, b = lsf.get("fit_a_uH_per_mm"), lsf.get("fit_b_uH")
    kT12 = ((sb.get("torque") or {}).get("k_T"))
    out: Dict[str, Any] = {"L_mm": L_mm}
    curve = []
    B1_2d = None
    # (1) every single-length run (JSON) with its 2-D leg
    for f in sorted(full_dir.glob("stage_a_L*.json")):
        sx = _load(f) or {}
        try:
            Lr = float(f.stem.split("_L", 1)[1])
        except ValueError:
            continue
        b2 = (sx.get("two_d") or {}).get("B1_T")
        if abs(Lr - L_mm) < 1e-6 or B1_2d is None:
            B1_2d = b2 if b2 else B1_2d
        b1m = (sx.get("spill_profile") or {}).get("B1_mid_T") or sx.get("B1_mid_T")
        # the run's own 2-D leg decides whether k_flux (axial mean / 2-D) is usable:
        # when the 3-D mid-plane and the 2-D field disagree beyond its tolerance it
        # says so (k_flux_usable False) and the pure end effect k_flux_self is used
        usable = (sx.get("two_d") or {}).get("k_flux_usable", True) is not False
        curve.append({"stack_mm": Lr, "k_flux_self": sx.get("k_flux_self"),
                      "B1_mid_T": b1m, "k_flux_raw": sx.get("k_flux"),
                      "k_flux_usable": usable,
                      "k_psi": sx.get("k_flux") if usable else sx.get("k_flux_self"),
                      "picard_converged":
                          ((sx.get("solves") or [{}])[0]).get("picard_converged"),
                      "two_d_verdict": (sx.get("two_d") or {}).get("verdict"),
                      "ratio_3d_mid_over_2d": (sx.get("two_d") or {}).get("ratio_3d_mid_over_2d"),
                      "wall_s": (sx.get("pilot") or {}).get("wall_s"),
                      "source": "out/full/%s (own cold run + 2-D leg)" % f.name})
    # (2) Ø40 only: the stopped 7-point sweep (log)
    logp = full_dir / "stage_a_sweep_killed_log.txt"
    if logp.exists():
        txt = logp.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"B1\(mid\) = ([0-9.]+) T, k_flux_self = ([0-9.]+)", txt)
        if m:
            B1m, ks = float(m.group(1)), float(m.group(2))
            curve.append({"stack_mm": 12.0, "k_flux_self": ks, "B1_mid_T": B1m,
                          "k_psi": (ks * B1m / B1_2d) if B1_2d else None,
                          "picard_converged": True,
                          "source": "stopped sweep, reference stack (log; Picard 2.2e-3 "
                                    "< 3e-3 at iteration 44)"})
        for mm in re.finditer(r"L =\s+([0-9.]+) mm\s+k_self = ([0-9.]+)", txt):
            curve.append({"stack_mm": float(mm.group(1)), "k_flux_self": float(mm.group(2)),
                          "B1_mid_T": None, "k_psi": None, "picard_converged": None,
                          "source": "stopped sweep, warm-started leg (log; self-referenced "
                                    "only — mid-plane B1 not printed)"})
    curve.sort(key=lambda r: r["stack_mm"])
    out["k_psi_curve"] = curve
    kpsi = next((r["k_psi"] for r in curve if abs(r["stack_mm"] - L_mm) < 1e-6
                 and r["k_psi"] is not None), None)
    own = next((r for r in curve if abs(r["stack_mm"] - L_mm) < 1e-6), {})
    out["k_psi"] = {"value": kpsi, "method": "3-D magnetostatic Stage A (I = 0): axial mean "
                    "of the gap fundamental over the stack / the 2-D gap fundamental "
                    "(k_flux_self × B1_mid,3D / B1_2D), at this machine's own stack; "
                    "today's die cross-section, n_stack 4 (quick fidelity)",
                    "B1_2d_T": B1_2d,
                    "labels": ["3-D", "no-load", "quick fidelity"]}
    if own and own.get("k_flux_usable") is False:
        out["k_psi"]["method"] = ("3-D magnetostatic Stage A (I = 0) at this machine's own stack: "
                                  "k_flux_self (axial mean of the gap fundamental / its own "
                                  "mid-plane value, the pure end effect) — the run's 2-D check "
                                  "FAILED (3-D mid-plane / 2-D = %.4f, tolerance 2 %%), so "
                                  "k_flux = %.4f is not used, as the run itself instructs"
                                  % (float(((_load(full_dir / ("stage_a_L%g.json" % L_mm))
                                             or {}).get("two_d") or {}).get(
                                                 "ratio_3d_mid_over_2d") or float("nan")),
                                     float(own.get("k_flux_raw") or float("nan"))))
        out["k_psi"]["labels"] = ["3-D", "no-load", "quick fidelity", "k_flux_self (2-D check failed)"]
    if logp.exists():
        out["k_psi"]["sweep_note"] = ("7-point L-sweep stopped after 6 mm (69 min per "
                                      "warm-started length under load); 12 and 20 mm solved, "
                                      "6 mm self-ratio only")
    kT = None
    if k_T_rule == "stage_b_inherited":
        if kT12 is not None:
            kT = 1.0 - (1.0 - float(kT12)) * 12.0 / float(L_mm)
        out["k_T"] = {"value": kT, "k_T_at_12mm": kT12,
                      "method": "INHERITED Stage B (co-energy, matched 2-D window) k_T = %.5f at "
                                "12 mm on the 2026-08 geometry %s (B15AHV950M / F45SH), scaled "
                                "by the end-region law 1 − (1 − k_T12)·12/L — not recomputed "
                                "(Stage B/D cost ~2 660 s per rotor position)" % (
                                    float(kT12 or 0), ee.get("geometry_fingerprint")),
                      "labels": ["3-D (inherited)", "older geometry", PEND]}
        kL = None
        if a and b:
            kL = (float(a) * L_mm + float(b)) / (float(a) * L_mm)
        out["k_L"] = {"value": kL, "fit_a_uH_per_mm": a, "fit_b_uH": b,
                      "method": "Stage B long-stack test: L(L) = a·L + b (stack incl. end "
                                "region); k_L = L/(a·L) = 1 + b/(a·L)",
                      "labels": ["3-D (inherited)", "older geometry"]}
    else:
        # No loaded 3-D torque run exists for this die (Stage B/D: ~2 660 s per rotor
        # position on the Ø40, more here).  The app's own rule — the 2-D torque × the
        # no-load flux factor (``end3d_k`` on every duty run) — is used and said so.
        out["k_T"] = {"value": kpsi, "method": "k_T = k_ψ (Stage A, this run): the app's own "
                      "3-D torque rule (end3d_k on every duty); no loaded 3-D torque run "
                      "(Stage B/D) for this die — on the Ø40 the loaded k_T was 0.979 vs "
                      "k_ψ 0.947, so this is likely CONSERVATIVE by up to ~(1−k_ψ)·0.6",
                      "labels": ["3-D (Stage A k_ψ as k_T)", PEND]}
        out["k_L"] = {"value": None, "method": "not computed: no Stage B long-stack test for "
                      "this die; Ld/Lq are 2-D (no end-winding inductance)",
                      "labels": ["not computed"]}
    out["magnet_segmentation"] = {
        "note": "magnets are one axial piece; rotor/magnet eddy loss is a 2-D (infinitely "
                "long, no axial return path) value — an axially unsegmented magnet of "
                "length L carries its eddy current along L and returns at the ends, so the "
                "2-D value is an UPPER bound; segmentation is not modelled",
        "labels": ["2-D", "upper bound"]}
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  mechanics, coupled thermal, demag limit
# ─────────────────────────────────────────────────────────────────────────────

def mech_block(full_dir: Path, M: str) -> Dict[str, Any]:
    m = _load(full_dir / f"mech_{M}.json") or {}
    ls = (m.get("limit_speed") or {}).get("limit_speed") or {}
    cs = m.get("critical_speeds") or {}
    out = {"limit_speed_sf1_rpm": ls.get("rpm_sf1"),
           "limit_speed_bracket_rpm": ls.get("bracket"),
           "limiting_part": ls.get("limiting_part"),
           "sf_at_rated": ls.get("sf_at_rpm0"),
           "critical": {"critical_speeds": cs.get("critical_speeds"),
                        "verdict": cs.get("verdict"),
                        "search_max_rpm": cs.get("rpm_hunt_max"),
                        "natural_hz_at_rest": cs.get("natural_hz_at_rest")},
           "beam_used": m.get("beam_used"), "panel_used": m.get("panel_used"),
           "errors": {k: m.get(k) for k in ("limit_speed_error", "critical_speeds_error")
                      if m.get(k)},
           "labels": ["2-D plane-stress rotor (Mechanical tab model)", "averaged stress",
                      "beam: " + PEND]}
    return out


def demag_limit(st: Mapping[str, Any], full_dir: Path, M: str, I0: float) -> Dict[str, Any]:
    dk = st.get("demag_knee") or {}
    rows = sorted(dk.get("mtpa_retention") or [])
    curve = {}
    for thr in (99.5, 99.0, 98.0):
        prev = (0.0, 100.0)
        val = None
        for I, k in rows:
            if k < thr:
                Ia, ka = prev
                val = Ia + (thr - ka) * (I - Ia) / (k - ka) if k != ka else I
                break
            prev = (I, k)
        curve[str(thr)] = {"I_rms": val, "beyond_last_probe": val is None,
                           "last_probe_I": rows[-1][0] if rows else None}
    seq = []
    for p in sorted(full_dir.glob(f"demagseq_{M}_*.json")):
        d = _load(p) or {}
        seq.append({"fI": d.get("fI"), "I_over": d.get("I_over"),
                    "torque_drop_pct": d.get("torque_drop_pct"), "valid": d.get("valid"),
                    "br_kept_after_overload_pct": (d.get("overload") or {}).get("br_kept_vol_pct"),
                    "T_rated_virgin": (d.get("rated_virgin") or {}).get("T_Nm"),
                    "T_rated_after": (d.get("rated_after") or {}).get("T_Nm"),
                    "seeded": (d.get("rated_after") or {}).get("demag_seeded")})
    seq.sort(key=lambda r: r["I_over"] or 0)
    lim, how = None, None
    good = [r for r in seq if r["valid"] and r["torque_drop_pct"] is not None]
    if good:
        prev = None
        for r in good:
            if r["torque_drop_pct"] > 1.0:
                if prev is None:
                    lim, how = None, "first probe already drops > 1 %"
                else:
                    a, b = prev, r
                    lim = a["I_over"] + (1.0 - a["torque_drop_pct"]) * (b["I_over"] - a["I_over"]) / (
                        b["torque_drop_pct"] - a["torque_drop_pct"])
                    how = "linear between overload probes %.1f and %.1f A" % (a["I_over"], b["I_over"])
                break
            prev = r
        else:
            lim, how = good[-1]["I_over"], "no probe drops > 1 % — limit ≥ the last probe"
    return {"rule": "highest overload current after which the rated-point torque drops "
                    "<= 1 % (owner default 2, " + PEND + ")",
            "I_limit_rms": lim, "I_limit_over_I0": (lim / I0 if lim else None),
            "how": how, "sequence": seq, "retention_curve": curve,
            "retention_rows": rows,
            "method": "overload on the hot MTPA line at rated speed (TDM + demag pre-pass), "
                      "then the rated point with the Br ratchet continued (solver sweep seed); "
                      "retention = Br-volume kept on the hot MTPA line (stage-1 probes)",
            "labels": ["2-D", "hot", PEND]}


# ─────────────────────────────────────────────────────────────────────────────
#  PWM FEM anchors
# ─────────────────────────────────────────────────────────────────────────────

def pwm_classes(full_dir: Path, M: str, v_dc: float) -> Dict[str, List[Dict[str, Any]]]:
    """{carrier class: [FEM anchor, ...]} from the PWM task outputs."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for p in sorted(full_dir.glob(f"pwm_{M}_*.json")):
        d = _load(p) or {}
        if d.get("dP_harm_W") is None:
            continue
        c = d.get("controller") or {}
        f = float(c.get("f_carrier_hz") or 48e3)
        dead = float(c.get("dead_time_us") or 0.1)
        tech = "gan" if str(c.get("device", "")).startswith("IG") else "si"
        cls = "%s%d_%dns" % (tech, round(f / 1000), round(dead * 1000))
        ss = d.get("sine_36") or {}
        v1 = ss.get("V1_seed_peak_V")
        row = {"tag": p.stem, "rpm": d.get("rpm"), "I": d.get("I"), "gamma": d.get("gamma"),
               "dP_harm_W": d.get("dP_harm_W"), "harm_ref": d.get("harm_ref"),
               "m_index": (float(v1) / (v_dc / 2.0)) if v1 else None,
               "device": c.get("device"), "temps": d.get("temps"),
               "steps": d.get("steps_pwm"), "wall_s": d.get("pwm_wall_s"),
               "mesh_note": "PWM step count raises the slip ring (640+ nodes/period); the "
                            "iron template falls back to the gmsh build (solver-flagged "
                            "DEGRADED, shaft skin layer not resolved) — PWM and its sine "
                            "reference share that mesh"}
        row["point"] = d.get("point")
        out.setdefault(cls, []).append(row)
    return out


#: Legacy Ø40 constants — the spec (config/passport_specs/) supersedes them;
#: kept so an old work dir assembles without a spec.
VARIANTS = {
    "L12": [
        {"id": "si_48k", "device": "IQE018N06NM6SC", "carrier_hz": 48e3, "dead_time_s": 100e-9,
         "fem_class": "si48_100ns"},
        {"id": "gan_48k_IGC019S06S1", "device": "IGC019S06S1", "carrier_hz": 48e3,
         "dead_time_s": 20e-9, "fem_class": "gan48_20ns"},
        {"id": "gan_48k_IGC016K10S2", "device": "IGC016K10S2", "carrier_hz": 48e3,
         "dead_time_s": 20e-9, "fem_class": "gan48_20ns"},
        {"id": "gan_100k_IGC019S06S1", "device": "IGC019S06S1", "carrier_hz": 100e3,
         "dead_time_s": 20e-9, "fem_class": "gan100_20ns"},
        {"id": "gan_100k_IGC016K10S2", "device": "IGC016K10S2", "carrier_hz": 100e3,
         "dead_time_s": 20e-9, "fem_class": "gan100_20ns"},
    ],
    "L20": [
        {"id": "si_48k", "device": "IQE036N08NM6SC", "carrier_hz": 48e3, "dead_time_s": 100e-9,
         "fem_class": "si48_100ns"},
        {"id": "gan_48k_IGC016K10S2", "device": "IGC016K10S2", "carrier_hz": 48e3,
         "dead_time_s": 20e-9, "fem_class": "gan48_20ns"},
        {"id": "gan_100k_IGC016K10S2", "device": "IGC016K10S2", "carrier_hz": 100e3,
         "dead_time_s": 20e-9, "fem_class": "gan100_20ns"},
    ],
}
NOT_COMPUTED_VARIANTS = {
    "L20": [{"id": "gan_*_IGD015S10S1", "device": "IGD015S10S1",
             "status": "not computed: no public datasheet (Infineon lists no document for "
                       "the part) — no device card"}],
}
CTRL = {"L12": {"build": "6S", "si": "IQE018N06NM6SC", "r_g": 10.0},
        "L20": {"build": "12S", "si": "IQE036N08NM6SC", "r_g": 12.0}}


# ─────────────────────────────────────────────────────────────────────────────
#  thermal limits per cooling option (spec cooling_studies)
# ─────────────────────────────────────────────────────────────────────────────

def cooling_block(full_dir: Path, M: str, studies: Sequence[Mapping[str, Any]], *,
                  rec, snap, hm, rows_grid, kT: float) -> Dict[str, Any]:
    """The coupled-loop answers per cooling option, side by side.

    S1 (``continuous``): the continuous current at the study speed, the part that
    limits it and every part's temperature (``coupled_continuous_rating``, verified
    by an EM pass at that current when the loop could); the card's torque and
    shaft power at that current are read from the passport (hot map × k_state ×
    k_T at the operating angle) — not the rating's linear-in-current estimate,
    which is kept beside it.  ``limits``: the time from cold to the first part
    limit at the study current (the peak duty)."""
    lg = rec["loss_grid"]["plan"]
    R_hot, vlim = float(lg["R_hot_ohm"]), float(lg["v_phase_limit_V"])
    rows = []
    for sd in studies:
        d = _load(full_dir / f"cooling_{M}_{sd['id']}.json")
        row: Dict[str, Any] = {"id": sd["id"], "cooling": sd["cooling"],
                               "solve_to": sd["solve_to"], "rpm": float(sd["rpm"]),
                               "current": sd.get("current"),
                               "propeller": sd.get("propeller")}
        if not d:
            row["status"] = "not run"
            rows.append(row)
            continue
        cp = d.get("coupling") or {}
        pop = d.get("propeller_operating_point") or {}
        row.update({"wall_s": d.get("wall_s"), "error": d.get("error"),
                    "I_study_A": (d.get("point") or {}).get("I_rms"),
                    "ambient_c": d.get("ambient_c"),
                    "air_speed_ms": pop.get("air_speed_mps"),
                    "propeller_point": ({k: pop.get(k) for k in (
                        "propeller_id", "torque_Nm", "thrust_N", "shaft_power_W",
                        "air_speed_ms", "slipstream_factor", "extrapolated")} if pop else None),
                    "thermal_settings": d.get("thermal_settings"),
                    "loop": {k: cp.get(k) for k in ("mode", "converged", "runaway",
                                                     "coil_temp_c", "magnet_temp_c",
                                                     "magnet_temp_max_c", "iterations",
                                                     "warning", "warning_code")}})
        cr = cp.get("continuous_rating") or {}
        if sd["solve_to"] == "continuous":
            Ic = cr.get("I_cont_A_rms")
            row["s1"] = {k: cr.get(k) for k in ("ok", "feasible", "I_cont_A_rms",
                                                 "limiting_part", "temperatures_c",
                                                 "limits_c", "verified", "trustworthy",
                                                 "converged", "capped", "note",
                                                 "record_is_s1", "I_estimated_A_rms")}
            row["s1"]["power_linear"] = {k: (cr.get("power") or {}).get(k)
                                         for k in ("T_em_Nm", "P_shaft_W", "eta_shaft")}
            if Ic:
                n = float(sd["rpm"])
                w = 2.0 * math.pi * n / 60.0
                pw = cr.get("power") or {}
                Tem = pw.get("T_em_Nm")
                Pm = (S3.motor_point(rec, snap, hm, rows_grid, {"rpm": n, "I_A": float(Ic),
                                                                 "gamma_deg": 0.0})["P_mech_W"])
                if Tem is not None:
                    # the S1 state's OWN electromagnetic pass (TDM + demag at the S1
                    # temperatures) — the HOT map is at the 150 °C reference, which is
                    # not this state's magnet temperature
                    T = float(Tem) * kT
                    row["s1"]["card"] = {"T_Nm": T, "P_shaft_W": T * w - (Pm or 0.0),
                                         "P_mech_W": Pm,
                                         "basis": "the S1 rating's own electromagnetic pass at "
                                                  "I_cont and the S1 temperatures (TDM + demag) "
                                                  "× k_T (3-D)" + ("" if Pm else
                                                                   "; mechanical loss not "
                                                                   "included (no bearings named)")}
                g, how = hm.operating_gamma(float(Ic), n, R_hot, vlim)
                if g is not None:
                    mp = S3.motor_point(rec, snap, hm, rows_grid,
                                        {"rpm": n, "I_A": float(Ic), "gamma_deg": g})
                    blk = {"T_Nm": mp["T_op_Nm"] * kT,
                           "P_shaft_W": mp["T_op_Nm"] * kT * w - mp["P_mech_W"],
                           "gamma_deg": g, "gamma_mode": how, "P_loss_em_W": mp["P_em_W"],
                           "basis": "passport HOT map (limit-temperature reference) × k_state × "
                                    "k_T at the S1 current — a lower bound when the S1 magnet "
                                    "is cooler than the reference"}
                    row["s1"]["card_at_hot_reference"] = blk
                    row["s1"].setdefault("card", blk)
        else:
            lim = cp.get("limited") or {}
            ttl = cp.get("time_to_limit") or {}
            row["limits"] = {"limiting_part": lim.get("part") or ttl.get("limiting_part"),
                             "t_cold_s": lim.get("t_cold_s") or ttl.get("time_to_limit_s"),
                             "at_limit_c": lim.get("at_limit_c"),
                             "steady_state_would_be_c": lim.get("steady_state_would_be")
                             or ttl.get("at_point_c"),
                             "limits_c": ttl.get("limits_c"),
                             "within_limits": ttl.get("within_limits")}
        rows.append(row)
    return {"studies": rows,
            "method": "coupled EM-thermal loop (routes.coupled.run, TDM eddy + demag, the "
                      "duty's mesh) per cooling option; S1 = coupled_continuous_rating "
                      "(network fitted to the 2-D thermal map, re-solved until s* settles, "
                      "verified by an EM pass at I_cont); limits = time from cold to the "
                      "first part limit; part limits from the machine's cards (winding: the "
                      "loop's class-N 200 °C assumption; magnet: card max working "
                      "temperature; housing: the loop's touch limit)",
            "labels": ["2-D thermal FEM + 4-node network", "ambient 40 °C",
                       "propeller slipstream factor 0.4 (behind hub) — to be calibrated",
                       PEND]}


# ── Configure's lumped estimate (web/src/lib/thermalEstimate.ts), ported 1:1 ──────────────
_K_IRON, _K_SLOT, _K_INS, _K_GAP = 25.0, 1.4, 0.2, 0.06


def _lumped(g: Mapping[str, float], P_cu: float, P_fe: float, P_mag: float,
            h: float, amb: float) -> Dict[str, float]:
    """``estimateThermal`` of the web, in Python (same constants, same paths): all
    loss leaves the lateral housing + both end faces at film h; the winding and
    magnet sit above the housing by lumped conduction resistances."""
    mm = lambda x: max(0.0, float(x or 0.0)) / 1000.0            # noqa: E731
    D, L = mm(g["statorOD_mm"]) or 0.1, mm(g["stackLength_mm"]) or 0.03
    P_tot = max(P_cu, 0) + max(P_fe, 0) + max(P_mag, 0)
    A_cyl = math.pi * D * L
    A_s = A_cyl + 2 * math.pi * (D / 2) ** 2
    T_h = amb + P_tot / (max(1.0, h) * A_s)
    A_slot = max(1e-4, g["numSlots"] * 2 * mm(g["slotHeight_mm"]) * L)
    R_w = (mm(g["slotWidth_mm"]) * 0.5) / (_K_SLOT * A_slot) + mm(g["insulation_mm"]) / (
        _K_INS * A_slot) + mm(g["coreThickness_mm"]) / (_K_IRON * max(1e-4, A_cyl))
    A_gap = max(1e-4, math.pi * mm(g["magnetOD_mm"]) * L)
    R_g = mm(g["airGap_mm"]) / (_K_GAP * A_gap)
    return {"T_housing_C": T_h, "T_winding_C": T_h + max(P_cu, 0) * R_w,
            "T_magnet_C": T_h + max(P_mag, 0) * R_g, "A_surface_m2": A_s, "h_W_m2K": h}


def lumped_cooling(rec, snap, hm, rows_grid, kT: float, spec_m: Mapping[str, Any]
                   ) -> Dict[str, Any]:
    """What CONFIGURE shows for this machine (its lumped model, the same films):
    the continuous current per cooling and speed — winding at Configure's class-H
    180 °C or magnet at the card's 150 °C, whichever first — with the copper at
    its own temperature (R(T)) and the iron / magnet / shaft groups of the passport
    loss trajectory.  Robotics = still air + radiation on the housing
    (cooling_models.outer_still, iterated at the housing temperature, ε 0.9);
    propeller = cross-flow at the slipstream speed (cooling_models.outer_air, the
    film Configure reads from /api/propellers/.../series)."""
    from motor_ai_sim import propeller as PP
    from motor_ai_sim.simulation import cooling_models as CM
    geo = snap["geometry"]
    D = float(geo["stator_diameter"])
    bore = D - 2.0 * (float(geo["core_thickness"]) + float(geo["slot_height"]))
    ns = int(round(float(geo.get("num_slots") or 24)))
    g = {"statorOD_mm": D, "stackLength_mm": float(geo["motor_length"]), "numSlots": ns,
         "slotHeight_mm": float(geo["slot_height"]),
         "slotWidth_mm": math.pi * (bore + float(geo["slot_height"])) / ns
         - float(geo["tooth_width"]),
         "insulation_mm": float(geo.get("insulation_thickness") or 0.05),
         "coreThickness_mm": float(geo["core_thickness"]), "airGap_mm": float(geo["air_gap"]),
         "magnetOD_mm": bore - 2.0 * float(geo["air_gap"])}
    lg = rec["loss_grid"]["plan"]
    R_hot = float(lg["R_hot_ohm"])
    T_hot_c = float(snap["temperatures"]["hot_coil_c"])
    alpha = C._cu_alpha(snap)
    R20 = R_hot / (1.0 + alpha * (T_hot_c - 20.0))
    W_LIM, M_LIM = 180.0, 150.0
    amb = 40.0
    D_m = D / 1000.0

    I_floor = min(rows_grid)

    def state(n, I, cooling, prop):
        # below the lowest trajectory current the non-copper groups are held at that
        # row's values (an upper bound: iron / magnet / shaft fall with the current)
        il = LS.interp_loss(n, max(I, I_floor), rows_grid, R_hot)
        if il.get("P_total_W") is None:
            return None
        gr = il["groups"]
        P_fe = float(gr["P_fe_stator_W"] or 0) + float(gr["P_fe_rotor_W"] or 0)
        P_mag = float(gr["P_mag_W"] or 0) + float(gr["P_shaft_W"] or 0) + float(gr["P_sleeve_W"] or 0)
        P_ac = float(gr["P_cu_ac_W"] or 0)
        Tw = T_hot_c
        out = None
        for _ in range(60):
            P_cu = 3.0 * I * I * R20 * (1.0 + alpha * (Tw - 20.0)) + P_ac
            if cooling == "propeller_air":
                v = PP.air_speed_for_thermal(prop, n, ambient_c=amb)["air_speed_mps"]
                h = float(CM.outer_air(air_speed_mps=v, t_ambient_c=amb, d_housing_m=D_m)["h_conv"])
                out = _lumped(g, P_cu, P_fe, P_mag, h, amb)
                out["air_speed_ms"] = v
            else:
                tw_h = amb + 30.0
                for _k in range(40):
                    h = float(CM.outer_still(t_wall_c=tw_h, t_ambient_c=amb, d_housing_m=D_m,
                                             emissivity=0.9)["h_total"])
                    out = _lumped(g, P_cu, P_fe, P_mag, h, amb)
                    nx = 0.5 * tw_h + 0.5 * out["T_housing_C"]
                    if abs(nx - tw_h) < 0.01:
                        break
                    tw_h = nx
                out["air_speed_ms"] = 0.0
            if abs(out["T_winding_C"] - Tw) < 0.01:
                break
            Tw = 0.5 * Tw + 0.5 * min(out["T_winding_C"], 400.0)
        out["P_cu_W"] = P_cu
        return out

    def i_cont(n, cooling, prop):
        Is = sorted(rows_grid)
        lo, hi = 0.0, Is[-1]
        s_lo = state(n, lo, cooling, prop)
        if s_lo is None:
            return None, "outside the loss trajectory at this speed"
        if s_lo["T_winding_C"] > W_LIM or s_lo["T_magnet_C"] > M_LIM:
            return None, "over a limit already at zero current"
        s_hi = state(n, hi, cooling, prop)
        if s_hi and s_hi["T_winding_C"] <= W_LIM and s_hi["T_magnet_C"] <= M_LIM:
            return hi, "inside both limits up to the top grid current"
        for _ in range(40):
            mid = 0.5 * (lo + hi)
            sm = state(n, mid, cooling, prop)
            if sm and sm["T_winding_C"] <= W_LIM and sm["T_magnet_C"] <= M_LIM:
                lo = mid
            else:
                hi = mid
        return lo, ("bisection on winding 180 °C / magnet 150 °C" + (
            "; below the lowest trajectory current (%.2f A) the non-copper losses are held at "
            "that row" % I_floor if lo < I_floor else ""))

    studies = [(c, float(n)) for c, n in (("robotics", 1000.0), ("propeller_air", 1000.0),
                                          ("propeller_air", 1500.0), ("propeller_air", 2000.0))]
    prop = next((s.get("propeller") for s in spec_m.get("cooling_studies") or []
                 if s.get("propeller")), None)
    vlim = float(lg["v_phase_limit_V"])
    rows = []
    for cooling, n in studies:
        Ic, how = i_cont(n, cooling, prop)
        row = {"cooling": cooling, "rpm": n, "propeller": prop if cooling == "propeller_air" else None,
               "I_cont_A_rms": Ic, "how": how}
        if Ic:
            stt = state(n, Ic, cooling, prop)
            gg, gm = hm.operating_gamma(Ic, n, R_hot, vlim)
            if gg is not None:
                mp = S3.motor_point(rec, snap, hm, rows_grid, {"rpm": n, "I_A": Ic, "gamma_deg": gg})
                # k_state of the nearest trajectory row (held below the lowest)
                T = hm.at_Ig(Ic, gg)["T"] * (S3.k_state_at(rec["loss_grid"]["points"], n, Ic)
                                             or 1.0) * kT
                row.update({"T_Nm": T, "P_shaft_W": T * 2 * math.pi * n / 60.0 - (mp["P_mech_W"] or 0.0)})
            row.update({k: stt[k] for k in ("T_winding_C", "T_magnet_C", "T_housing_C", "h_W_m2K",
                                            "air_speed_ms")})
            row["limiting"] = ("winding" if stt["T_winding_C"] >= W_LIM - 0.5 else
                               "magnet" if stt["T_magnet_C"] >= M_LIM - 0.5 else "top of the grid")
        rows.append(row)
    return {"rows": rows, "geometry": g, "limits_c": {"winding": W_LIM, "magnet": M_LIM},
            "ambient_c": amb,
            "method": "Configure's lumped model (web/src/lib/thermalEstimate.ts, ported): lateral "
                      "housing + both end faces at one film; robotics film = still air + radiation "
                      "(outer_still, eps 0.9) at the housing temperature, NO mount / heat path; "
                      "propeller film = cross-flow at the slipstream (outer_air); losses = the "
                      "passport trajectory (copper at its own temperature); torque = hot map × "
                      "k_state × k_T",
            "labels": ["lumped estimate (Configure)", "ambient 40 °C", PEND]}


def propeller_match(rec, snap, hm, rows_grid, kT: float, prop_ids: Sequence[str],
                    cool: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Each propeller against the motor: the motor's max (card) torque at v_nom
    and the propeller-air S1 card torque at the studied speeds vs the prop
    torque; the highest speed the motor holds continuously with that prop and
    the highest it reaches at all (peak current, v_nom, m)."""
    from motor_ai_sim import propeller as PP
    lg = rec["loss_grid"]["plan"]
    R_hot, vlim = float(lg["R_hot_ohm"]), float(lg["v_phase_limit_V"])
    I_pk = float(lg["I_peak_rms"])
    s1 = sorted(((r["rpm"], ((r.get("s1") or {}).get("card") or {}).get("T_Nm"))
                 for r in cool.get("studies", []) if r.get("cooling") == "propeller_air"
                 and r.get("solve_to") == "continuous"
                 and ((r.get("s1") or {}).get("card") or {}).get("T_Nm")),
                key=lambda x: x[0])
    s1_l = sorted(((r["rpm"], r.get("T_Nm")) for r in (cool.get("lumped") or {}).get("rows", [])
                   if r.get("cooling") == "propeller_air" and r.get("T_Nm")), key=lambda x: x[0])

    def n_cont_of(pr, pts):
        if len(pts) < 2:
            return None
        ns, ts = [x[0] for x in pts], [x[1] for x in pts]
        nc = None
        for n in np.arange(ns[0], ns[-1] + 1e-9, 10.0):
            if PP.torque_Nm(pr, float(n)) <= float(np.interp(n, ns, ts)):
                nc = float(n)
        return nc
    out = []
    for pid in prop_ids:
        try:
            pr = PP.get_propeller(pid)
        except Exception as e:                       # noqa: BLE001
            out.append({"id": pid, "status": "not in the catalogue: %s" % e})
            continue
        pts = []
        n_reach = None
        for n in np.arange(250.0, 4001.0, 50.0):
            tq = PP.torque_Nm(pr, float(n))
            mt = hm.max_torque_at(float(n), R_hot, vlim, I_pk)
            if mt.get("T") is None:
                break
            k = S3.k_state_at(rec["loss_grid"]["points"], float(n), mt["I"]) or 1.0
            if mt["T"] * k * kT >= tq:
                n_reach = float(n)
            pts.append({"rpm": float(n), "T_prop_Nm": tq, "T_max_Nm": mt["T"] * k * kT})
        n_cont = n_cont_of(pr, s1)
        n_cont_l = n_cont_of(pr, s1_l)
        op_c = PP.operating_point(pr, n_cont) if n_cont else None
        op_l = PP.operating_point(pr, n_cont_l) if n_cont_l else None
        op_r = PP.operating_point(pr, n_reach) if n_reach else None
        geo = getattr(pr, "geometry", None) or {}
        out.append({"id": pid, "model": pr.model,
                    "diameter_in": (geo.get("diameter_in") if isinstance(geo, dict) else None),
                    "rpm_range_tested": list(pr.rpm_range) if pr.rpm_range else None,
                    "T_prop_at_1000_Nm": PP.torque_Nm(pr, 1000.0),
                    "T_prop_at_2000_Nm": PP.torque_Nm(pr, 2000.0),
                    "n_cont_rpm": n_cont,
                    "n_cont_basis": ("highest speed where the prop torque ≤ the propeller-air "
                                     "S1 card torque (S1 at %s rpm, linear between; none "
                                     "below the first S1 speed means the prop is lighter "
                                     "than S1 there)" % "/".join("%.0f" % x[0] for x in s1))
                    if s1 else "no propeller S1 runs",
                    "cont_thrust_N": op_c["thrust_N"] if op_c else None,
                    "n_cont_lumped_rpm": n_cont_l,
                    "cont_thrust_lumped_N": op_l["thrust_N"] if op_l else None,
                    "cont_shaft_W": op_c["shaft_power_W"] if op_c else None,
                    "n_reach_rpm": n_reach,
                    "reach_thrust_N": op_r["thrust_N"] if op_r else None,
                    "reach_torque_Nm": op_r["torque_Nm"] if op_r else None,
                    "curve": pts})
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  the full record
# ─────────────────────────────────────────────────────────────────────────────

def build_full(work: Path, repo: Path, M: str, machine_meta: Mapping[str, Any],
               spec_m: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    od = work / "out" / M
    full = work / "out" / "full"
    snap = json.loads((od / "snapshot.json").read_text(encoding="utf-8"))
    st = json.loads((od / "state.json").read_text(encoding="utf-8"))
    rec = C.build_record(od, snap, st, machine_meta=machine_meta)
    recs = C.load_records(od)
    recs.pop("__failed__", None)
    pp = int(round(float(snap["geometry"]["num_poles"]))) // 2
    _, hm = C._map(recs, st, "hot", "hot_mtpa", pp)
    rows_grid = C.loss_rows(recs, st)
    I0 = float(snap["rated_duty"]["current_arms"])
    n0 = float(snap["rated_duty"]["rpm"])
    L = float(snap["geometry"]["motor_length"])
    v_dc = float(snap["battery"]["v_nom"])
    spec_m = dict(spec_m or {})
    s2 = stage2_block(full, repo, L, str((spec_m.get("k3d") or {}).get("k_T")
                                         or "stage_b_inherited"))
    rec["stage2_3d"] = s2
    rec["mechanical"] = mech_block(full, M)
    cp = _load(full / f"coupled_{M}.json")
    rec["coupled_thermal"] = ({"converged": (cp.get("coupling") or {}).get("converged"),
                               "coupling": cp.get("coupling"), "wall_s": cp.get("wall_s"),
                               "thermal_settings_source": cp.get("thermal_settings_source"),
                               "used_for_card": bool((cp.get("coupling") or {}).get("converged"))}
                              if cp else {"status": "not run for this machine — hot temperatures "
                                          "from the rated duty, labelled"})
    t_in = snap.get("temperatures") or {}
    if t_in.get("hot_override_reason"):
        stored = _load(work / "inputs" / ("duty_results_%s.json" % snap["configuration"])) or {}
        sc = ((stored.get(snap["rated_duty"]["name"]) or {}).get("coupled") or {})
        rec["coupled_thermal"] = {
            "status": "rated duty has no steady state inside the magnet card — hot map at the "
                      "labelled limit-temperature reference (owner decision pending)",
            "used_for_card": False, "reason": t_in["hot_override_reason"],
            "stored_rated_coupled": {k: sc.get(k) for k in (
                "computed_at", "solve_to", "converged", "runaway", "coil_temp_c",
                "magnet_temp_c", "magnet_temp_max_c", "residual_coil_K",
                "residual_magnet_K", "warning", "warning_code", "iterations")},
            "hot_reference": {"magnet_c": t_in.get("hot_magnet_c"),
                              "coil_c": t_in.get("hot_coil_c"),
                              "source": t_in.get("hot_magnet_source")}}
    rec["demag_limit"] = demag_limit(st, full, M, I0)
    dvt = []
    for f_ in sorted(full.glob(f"demagtemp_{M}_*.json")):
        d_ = _load(f_) or {}
        dvt.append({"magnet_temp_c": d_.get("magnet_temp_c"), "I_rms": d_.get("I"),
                    "gamma": d_.get("gamma"), "rpm": d_.get("rpm"),
                    "T_demag_Nm": (d_.get("demag") or {}).get("T_Nm"),
                    "T_nodemag_Nm": (d_.get("nodemag") or {}).get("T_Nm"),
                    "br_kept_vol_pct": (d_.get("demag") or {}).get("br_kept_vol_pct"),
                    "torque_drop_pct": d_.get("demag_torque_drop_pct"),
                    "method": d_.get("method")})
    ks0 = rec["card"]["rated_point"].get("k_state_split") or {}
    if dvt and ks0.get("demag_share") is not None:
        dvt.append({"magnet_temp_c": snap["temperatures"]["hot_magnet_c"], "I_rms": I0,
                    "torque_drop_pct": 100.0 * (1.0 - float(ks0["demag_share"])),
                    "br_kept_vol_pct": ((rec.get("demag") or {}).get("mtpa_retention") or [[None, None]])[0][1],
                    "method": "the card's rated point (HOT reference): demag share of k_state"})
    if dvt:
        rec["demag_vs_temperature"] = {
            "rows": sorted(dvt, key=lambda r: float(r["magnet_temp_c"] or 0)),
            "purpose": "owner decision aid (magnet card / temperature): the rated point's torque "
                       "lost to irreversible demagnetisation vs the magnet temperature",
            "labels": ["2-D", "TDM + full demag pre-pass", "rated speed, hot-MTPA angle"]}
    # The owner's rule compares the rated point after an overload with the rated
    # point from virgin magnets — it presumes the rated point itself does not
    # demagnetise.  When it does (Ø85 at the 150 °C reference: demag share 0.853),
    # the rule has no reference and is not quoted as a limit.
    ks_ = (rec["card"]["rated_point"].get("k_state_split") or {})
    dsh = ks_.get("demag_share")
    if dsh is not None and dsh < 0.95:      # Ø40: 0.987-0.992, unchanged
        dl_ = rec["demag_limit"]
        dl_["I_limit_rule_value_rms"] = dl_.get("I_limit_rms")
        dl_["I_limit_rms"] = None
        dl_["I_limit_over_I0"] = None
        dl_["how"] = ("not defined at this HOT reference: the rated point itself loses %.1f %% "
                      "of its torque to demagnetisation from virgin magnets (demag share %.3f), so "
                      "the rule's reference is already damaged; overloads up to the probes left the "
                      "rated torque within %s %% of that damaged value. The safe current at this "
                      "magnet temperature is below 0.25·I0 (Br-retention knee %s A)."
                      % (100.0 * (1.0 - dsh), dsh,
                         ", ".join("%+.2f" % r["torque_drop_pct"] for r in dl_.get("sequence") or []
                                   if r.get("torque_drop_pct") is not None) or "—",
                         ("%.1f" % ((dl_.get("retention_curve") or {}).get("99.5") or {}).get("I_rms"))
                         if ((dl_.get("retention_curve") or {}).get("99.5") or {}).get("I_rms")
                         else "—"))
    ctrl = spec_m.get("controller") if spec_m else (
        {"status": "set", "device": CTRL[M]["si"], "r_g_ohm": CTRL[M]["r_g"],
         "build": CTRL[M]["build"], "n_parallel": 1, "board_scale": 1})
    variants = (spec_m.get("pwm_variants") if spec_m else VARIANTS.get(M)) or []
    if ctrl and ctrl.get("status") == "set" and variants:
        s3 = S3.build_variants(machine=M, rec=rec, snap=snap, hm=hm, rows_grid=rows_grid,
                               pwm_fem=pwm_classes(full, M, v_dc), variants=variants,
                               r_g_si=float(ctrl["r_g_ohm"]), build=str(ctrl["build"]),
                               si_part=str(ctrl["device"]),
                               n_par=int(ctrl.get("n_parallel") or 1),
                               board_scale=float(ctrl.get("board_scale") or 1))
        rec["pwm_variants"] = s3["pwm_variants"] + list(
            (spec_m.get("not_computed_variants") if spec_m else NOT_COMPUTED_VARIANTS.get(M))
            or [])
        rec["stage3_pwm_controller"] = {"board": s3["board"],
                                        "status": "computed (see pwm_variants)",
                                        "controller": ctrl,
                                        "controller_source": ctrl.get("source") or S3.CTRL_SOURCE}
    else:
        rec["pwm_variants"] = []
        rec["stage3_pwm_controller"] = {"status": "no controller set",
                                        "note": "PWM variants are computed only for a machine "
                                                "with a controller; Configure shows PWM "
                                                "disabled (request calculation)"}
    # a configuration that names no bearings has no mechanical loss model: every
    # shaft number then EXCLUDES the bearings / windage — said on the value, never 0
    for blk_ in (rec["card"]["rated_point"], rec["card"]["peak_point"]):
        pm_ = blk_.get("P_mech_W") or {}
        if isinstance(pm_, dict) and pm_.get("value") is None:
            for k_ in ("P_shaft_W", "eta_shaft", "P_mech_W"):
                if isinstance(blk_.get(k_), dict):
                    blk_[k_]["labels"] = list(blk_[k_].get("labels") or []) + [
                        "mechanical loss not included: the configuration names no bearings"]
    # ── headline with 3-D factors ───────────────────────────────────────────
    kT = s2["k_T"]["value"] or 1.0
    kpsi = s2["k_psi"]["value"]
    rp = rec["card"]["rated_point"]
    T_op = (rp["T_operating_Nm"] or {}).get("value")
    rec["card"]["rated_point"]["T_card_Nm"] = C._val(
        T_op * kT if T_op else None, "N·m",
        "operating torque (settled TDM, demag) × k_T(3-D)", rp["T_operating_Nm"]["runs"],
        ["2-D × k_T 3-D (inherited)", PEND + " (card torque = operating)"])
    pk = rec["card"]["peak_point"]
    T_pk = (pk["T_operating_Nm"] or {}).get("value")
    rec["card"]["peak_point"]["T_card_Nm"] = C._val(
        T_pk * kT if T_pk else None, "N·m", "map × k_state × k_T(3-D)", [],
        ["2-D × k_T 3-D (inherited)", PEND])
    studies = list(spec_m.get("cooling_studies") or []) if spec_m else []
    if studies:
        rec["cooling"] = cooling_block(full, M, studies, rec=rec, snap=snap, hm=hm,
                                       rows_grid=rows_grid, kT=kT)
        try:
            rec["cooling"]["lumped"] = lumped_cooling(rec, snap, hm, rows_grid, kT, spec_m)
        except Exception as e:                                  # noqa: BLE001
            rec["cooling"]["lumped"] = {"error": "%s: %s" % (type(e).__name__, e)}
        props = list(spec_m.get("propellers") or [])
        if props:
            rec["propeller_match"] = propeller_match(rec, snap, hm, rows_grid, kT, props,
                                                     rec["cooling"])
    kv = rec["card"]["constants_cold"]["Kv_rpm_per_V_line"]["value"]
    rec["card"]["constants_cold"]["Kv_3d_rpm_per_V_line"] = C._val(
        kv / kpsi if (kv and kpsi) else None, "rpm/V (line peak)", "2-D Kv ÷ k_psi(3-D)",
        ["cold_noload"], ["3-D k_psi (Stage A)"])
    # envelope with k_psi and k_state/k_T (operating torque)
    lg = rec["loss_grid"]["plan"]
    R_hot = float(lg["R_hot_ohm"])
    I_pk = float(lg["I_peak_rms"])
    env3 = {}
    for which in ("min", "nom", "max"):
        vb = float(snap["battery"]["v_" + which])
        vl = PM.v_phase_limit(vb, float(lg["m"]))
        vl_eff = vl / (kpsi or 1.0)
        nmax = min(hm.max_speed(R_hot, vl_eff, I_pk),
                   float(lg.get("n_mech_limit_rpm") or float("inf")),
                   float(rec["mechanical"].get("limit_speed_sf1_rpm") or float("inf")))
        pts = []
        emax = float(((snap.get("plan") or {}).get("envelope_max_factor")) or 4.0)
        for n in np.linspace(max(0.05 * n0, 50.0), min(nmax, emax * n0), 36):
            mt = hm.max_torque_at(float(n), R_hot, vl_eff, I_pk)
            if mt.get("T") is None:
                continue
            k = S3.k_state_at(rec["loss_grid"]["points"], float(n), mt["I"])
            pts.append({"rpm": float(n), "T_map": mt["T"], "I": mt["I"], "gamma": mt["gamma"],
                        "T_card": mt["T"] * (k or 1.0) * kT, "k_state": k})
        cont = []
        for n in np.linspace(max(0.05 * n0, 50.0), min(nmax, emax * n0), 36):
            g, _ = hm.operating_gamma(I0, float(n), R_hot, vl_eff)
            if g is None:
                continue
            k = S3.k_state_at(rec["loss_grid"]["points"], float(n), I0)
            cont.append({"rpm": float(n), "T_card": hm.at_Ig(I0, g)["T"] * (k or 1.0) * kT})
        env3[which] = {"v_dc": vb, "v_phase_limit_V": vl, "v_limit_3d_V": vl_eff,
                       "peak": pts, "rated_current": cont}
    rec["envelope_card"] = {"curves": env3, "method": "hot psi-map, I ≤ I_peak (peak curve) / "
                            "I = I0 (rated-current curve), V ≤ m·V_dc/√3 with ψ×k_psi(3-D) "
                            "(applied as V_lim/k_psi); torque = map × k_state × k_T",
                            "labels": ["2-D map", "3-D k_psi, k_T", "m = %g %s" % (lg["m"], PEND)]}
    # efficiency map at the shaft (sine drive) on the characterised region
    speeds = list(np.linspace(min(lg["speeds"]), max(lg["speeds"]), 24))
    cur = list(np.linspace(min(lg["currents"]), max(lg["currents"]), 14))
    T_ax, eta = [], []
    vlim = float(lg["v_phase_limit_V"])
    for n in speeds:
        col = []
        for I in cur:
            g, _ = hm.operating_gamma(I, n, R_hot, vlim)
            if g is None:
                col.append(None)
                continue
            mp = S3.motor_point(rec, snap, hm, rows_grid, {"rpm": n, "I_A": I, "gamma_deg": g})
            if mp["P_em_W"] is None:
                col.append(None)
                continue
            Tc = mp["T_op_Nm"] * kT
            Psh = Tc * 2 * math.pi * n / 60.0 - mp["P_mech_W"]
            col.append((Tc, 100.0 * Psh / (Psh + mp["P_em_W"] + mp["P_mech_W"])))
        eta.append(col)
    rec["efficiency_map"] = {"rpm": speeds, "I_rms": cur, "cells": eta,
                             "method": "loss trajectory (m %g) interpolation + analytic mech, "
                                       "torque map × k_state × k_T; characterised region only "
                                       "(grid speeds × 0.5·I0…I_peak); sine drive" % lg["m"],
                             "labels": ["2-D", "sine drive", "3-D k_T"]}
    # loss split vs speed at rated current
    split = []
    for n in lg["speeds"]:
        g, _ = hm.operating_gamma(I0, n, R_hot, vlim)
        if g is None:
            continue
        il = LS.interp_loss(n, I0, rows_grid, R_hot)
        mech = LS.mech_losses(rpm=n, bearings=snap["bearings"], geometry=snap["geometry"],
                              temp_c=None)
        si = [v for v in rec["pwm_variants"] if v.get("id") == "si_48k"]
        pwm = inv = None
        if si and si[0].get("points"):
            key = min(si[0]["points"], key=lambda k: abs(si[0]["points"][k]["rpm"] - n)
                      + 1e6 * abs(si[0]["points"][k]["I_A"] - I0))
            pt = si[0]["points"][key]
            pwm = pt.get("motor_pwm_loss_W")
            inv = sum((pt.get("inverter_loss_W") or {}).values()) if pt.get("inverter_loss_W") else None
        split.append({"rpm": n, **il["groups"], "P_mech_W": mech.get("P_W"),
                      "P_pwm_extra_W": pwm, "P_inverter_W": inv})
    rec["loss_split_rated_current"] = split
    rec["labels"] = ["2-D FEM (stage 1 grid)", "3-D factors (Stage A k_psi; k_T, k_L inherited)",
                     "sine + PWM (FEM anchors)", "controller losses (cards + SPICE)", PEND]
    rec["schema"] = "passport-v1-full-1"
    return rec


# ─────────────────────────────────────────────────────────────────────────────
#  HTML
# ─────────────────────────────────────────────────────────────────────────────

def _v(d, key="value"):
    return (d or {}).get(key) if isinstance(d, dict) else d


def _cooling_html(rec: Mapping[str, Any]) -> str:
    """Thermal limits per cooling option, side by side (+ the propeller match)."""
    cool = rec.get("cooling")
    if not cool:
        return ""
    o = ["<h2>Thermal limits — robotics vs propeller cooling</h2>"]
    st = cool["studies"]
    s1 = [r for r in st if r["solve_to"] == "continuous"]
    lim = [r for r in st if r["solve_to"] != "continuous"]
    rows = []
    for r in s1:
        c = r.get("s1") or {}
        cd = c.get("card") or {}
        t = c.get("temperatures_c") or {}
        rows.append([r["cooling"].replace("_", " "), "%.0f" % r["rpm"],
                     Hh.fmt(r.get("air_speed_ms")) if r.get("air_speed_ms") else "still air",
                     c.get("I_cont_A_rms") if c.get("I_cont_A_rms") is not None
                     else H_(r.get("status") or r.get("error") or "—"),
                     cd.get("T_Nm"), cd.get("P_shaft_W"),
                     c.get("limiting_part") or "—",
                     t.get("winding"), t.get("magnet"), t.get("housing"),
                     "yes" if c.get("verified") else ("no" if c else "—")])
    o.append(Hh.table(["cooling", "rpm", "air m/s", "I cont. A rms", "T cont. N·m",
                       "P shaft W", "limited by", "winding °C", "magnet °C", "housing °C",
                       "verified"], rows, left_cols=1))
    if lim:
        rows = []
        for r in lim:
            L = r.get("limits") or {}
            rows.append([r["cooling"].replace("_", " "), "%.0f" % r["rpm"],
                         Hh.fmt(r.get("I_study_A")),
                         Hh.fmt(L.get("t_cold_s")) if L.get("t_cold_s") else
                         ("within limits" if L.get("within_limits") else "—"),
                         L.get("limiting_part") or "—",
                         H_(", ".join("%s %s" % (k, Hh.fmt(v)) for k, v in
                                      (L.get("steady_state_would_be_c") or {}).items()))])
        o.append(Hh.table(["cooling", "rpm", "I A rms", "s from cold to limit", "first limit",
                           "steady state would be, °C"], rows, left_cols=1))
    o.append('<p class="note">FEM rows: %s</p>' % Hh.esc(cool["method"]))
    lp = cool.get("lumped") or {}
    if lp.get("rows"):
        o.append("<h3>The same coolings in Configure's lumped model</h3>")
        rows = [[r["cooling"].replace("_", " "), "%.0f" % r["rpm"],
                 Hh.fmt(r.get("air_speed_ms")) if r.get("air_speed_ms") else "still air",
                 Hh.fmt(r.get("h_W_m2K")), r.get("I_cont_A_rms") if r.get("I_cont_A_rms") is not None
                 else H_(r.get("how") or "—"), r.get("T_Nm"), r.get("P_shaft_W"),
                 r.get("limiting") or "—", r.get("T_winding_C"), r.get("T_magnet_C"),
                 r.get("T_housing_C")] for r in lp["rows"]]
        o.append(Hh.table(["cooling", "rpm", "air m/s", "film h W/m²K", "I cont. A rms",
                           "T cont. N·m", "P shaft W", "limited by", "winding °C", "magnet °C",
                           "housing °C"], rows, left_cols=1))
        o.append('<p class="note">%s</p>' % Hh.esc(lp["method"]))
    pm = rec.get("propeller_match") or []
    if pm:
        o.append("<h3>Propellers against this motor</h3>")
        rows = [[r.get("model") or r["id"], Hh.fmt(r.get("T_prop_at_1000_Nm")),
                 Hh.fmt(r.get("T_prop_at_2000_Nm")),
                 Hh.fmt(r.get("n_cont_rpm"), 0) if r.get("n_cont_rpm") else "—",
                 Hh.fmt(r.get("cont_thrust_N")) if r.get("cont_thrust_N") else "—",
                 Hh.fmt(r.get("n_cont_lumped_rpm"), 0) if r.get("n_cont_lumped_rpm") else "—",
                 Hh.fmt(r.get("cont_thrust_lumped_N")) if r.get("cont_thrust_lumped_N") else "—",
                 Hh.fmt(r.get("n_reach_rpm"), 0) if r.get("n_reach_rpm") else "—",
                 Hh.fmt(r.get("reach_thrust_N")) if r.get("reach_thrust_N") else "—",
                 "%s–%s" % tuple(int(x) for x in r["rpm_range_tested"])
                 if r.get("rpm_range_tested") else "—"]
                for r in pm if "curve" in r]
        o.append(Hh.table(["propeller", "τ @1000 N·m", "τ @2000 N·m", "cont. rpm (FEM)",
                           "cont. thrust N (FEM)", "cont. rpm (lumped)", "cont. thrust N (lumped)",
                           "max rpm (peak I)", "max thrust N", "tested rpm"], rows, left_cols=1))
        ser = []
        for i, r in enumerate([r for r in pm if r.get("curve")]):
            ser.append({"name": r.get("model") or r["id"],
                        "x": [q["rpm"] for q in r["curve"]],
                        "y": [q["T_prop_Nm"] for q in r["curve"]],
                        "color": Hh.PALETTE[(i + 2) % len(Hh.PALETTE)], "dash": True})
        cur = next((r["curve"] for r in pm if r.get("curve")), None)
        if cur:
            ser.append({"name": "motor max (peak I, v_nom)", "x": [q["rpm"] for q in cur],
                        "y": [q["T_max_Nm"] for q in cur], "color": "#000"})
        s1p = sorted((r["rpm"], ((r.get("s1") or {}).get("card") or {}).get("T_Nm"))
                     for r in s1 if r["cooling"] == "propeller_air"
                     and ((r.get("s1") or {}).get("card") or {}).get("T_Nm"))
        if s1p:
            ser.append({"name": "S1, propeller air", "x": [x[0] for x in s1p],
                        "y": [x[1] for x in s1p], "color": Hh.PALETTE[0], "marker": True})
        s1r = sorted((r["rpm"], ((r.get("s1") or {}).get("card") or {}).get("T_Nm"))
                     for r in s1 if r["cooling"] == "robotics"
                     and ((r.get("s1") or {}).get("card") or {}).get("T_Nm"))
        if s1r:
            ser.append({"name": "S1, robotics", "x": [x[0] for x in s1r],
                        "y": [x[1] for x in s1r], "color": Hh.PALETTE[1], "marker": True})
        o.append('<div class="charts">' + Hh.figure(
            Hh.line_chart(ser, xlab="speed, rpm", ylab="torque, N·m", ymin=0),
            "Propeller torque (dashed, T-Motor bench data, static) vs the motor's maximum "
            "card torque and its continuous (S1) torque per cooling.") + "</div>")
    return "".join(o)


def machine_html(rec: Mapping[str, Any], M: str, title: str = "Ø40") -> str:
    c = rec["card"]
    rp, pk = c["rated_point"], c["peak_point"]
    s2 = rec["stage2_3d"]
    mech = rec["mechanical"]
    dl = rec["demag_limit"]
    inp = rec["inputs"]
    t = inp["temperatures"]
    bat = inp["battery"]
    o = []
    o.append(f"<h1>{Hh.esc(rec['machine']['die'])} · {M} — motor passport</h1>")
    ct0 = rec.get("coupled_thermal") or {}
    if ct0.get("hot_reference"):
        o.append('<p class="note bad">HOT = limit-temperature reference (magnet %s °C, '
                 'winding %s °C), not the rated duty: %s</p>' % (
                     Hh.fmt(ct0["hot_reference"]["magnet_c"]), Hh.fmt(ct0["hot_reference"]["coil_c"]),
                     Hh.esc(ct0.get("reason") or "")))
    o.append(f'<p class="sub">Stack {float(rec["stage2_3d"]["L_mm"]):g} mm · bus {bat["v_min"]:g}/'
             f'{bat["v_nom"]:g}/{bat["v_max"]:g} V ({bat.get("cells")}S) · hot magnets '
             f'{t["hot_magnet_c"]:g} °C / winding {t["hot_coil_c"]:g} °C · m = '
             f'{rec["loss_grid"]["plan"]["m"]:g} · {Hh.labels(rec["labels"])}</p>')
    # tiles
    o.append('<div class="grid">')
    o.append(Hh.tile("Rated torque (card)", f"{Hh.fmt(_v(rp.get('T_card_Nm')))} N·m",
                     f"{rp['rpm']:.0f} rpm, {rp['I_rms']:.2f} A · operating × k_T"))
    o.append(Hh.tile("Rated shaft power", f"{Hh.fmt(_v(rp['P_shaft_W']))} W",
                     "2-D operating torque · sine"))
    o.append(Hh.tile("η shaft (rated, sine)", f"{Hh.fmt(100 * (_v(rp['eta_shaft']) or 0))} %",
                     "one efficiency, at the shaft"))
    o.append(Hh.tile("Peak torque (card)", f"{Hh.fmt(_v(pk.get('T_card_Nm')))} N·m",
                     f"{pk['rpm']:.0f} rpm, {pk['I_rms']:.2f} A"))
    o.append(Hh.tile("k_ψ / k_T / k_L (3-D)",
                     f"{Hh.fmt(_v(s2['k_psi']))} / {Hh.fmt(_v(s2['k_T']))} / {Hh.fmt(_v(s2['k_L']))}",
                     "Stage A today · Stage B inherited"))
    o.append(Hh.tile("Demag current limit", f"{Hh.fmt(dl.get('I_limit_rms'))} A",
                     "≤ 1 % rated-torque drop · " + PEND))
    o.append(Hh.tile("Rotor stress limit (SF 1)", f"{Hh.fmt(mech.get('limit_speed_sf1_rpm'))} rpm",
                     "averaged stress"))
    for r in (rec.get("cooling") or {}).get("studies", []):
        cd = ((r.get("s1") or {}).get("card") or {})
        if r["solve_to"] == "continuous" and abs(r["rpm"] - rp["rpm"]) < 1e-6 and cd.get("T_Nm"):
            o.append(Hh.tile("Continuous, %s" % r["cooling"].replace("_", " "),
                             f"{Hh.fmt(cd['T_Nm'])} N·m",
                             f"{Hh.fmt(r['s1'].get('I_cont_A_rms'))} A at {r['rpm']:.0f} rpm · "
                             f"limited by {r['s1'].get('limiting_part')}"))
    si = [v for v in rec["pwm_variants"] if v.get("id") == "si_48k"]
    if si and si[0].get("i_board_limit_A"):
        o.append(Hh.tile("Controller continuous (Si)", f"{Hh.fmt(si[0]['i_board_limit_A']['favourable'])} A",
                         "board 110 °C, favourable airflow · estimate"))
    o.append("</div>")
    # system limit
    o.append("<h2>System limit — motor vs controller</h2>")
    rows = []
    rated_key = [k for k, p in (si[0]["points"] if si else {}).items()
                 if p.get("duty") and "peak" not in str(p.get("duty")) and p["I_A"] == rp["I_rms"]]
    for v in rec["pwm_variants"]:
        if not v.get("points"):
            rows.append([v["id"], v.get("device"), "—", "—", "—", "—", "—",
                         H_(v.get("status", ""))])
            continue
        pr = [p for p in v["points"].values() if p.get("duty") and abs(p["rpm"] - rp["rpm"]) < 1e-6
              and abs(p["I_A"] - rp["I_rms"]) < 1e-6]
        pr = pr[0] if pr else {}
        bc = pr.get("p_cont_max_W_by_cooling") or {}
        best = max(((p.get("p_cont_max_W") or 0.0, p["rpm"]) for p in v["points"].values()),
                   default=(None, None))
        rows.append([v["id"], v["device"], v["i_board_limit_A"]["favourable"],
                     bc.get("favourable"), bc.get("weak"), bc.get("hot_motor"),
                     f"{Hh.fmt(best[0])} @ {best[1]:.0f}" if best[0] else "—",
                     Hh.H(Hh.labels(["estimate", PEND]))])
    o.append(Hh.table(["variant", "device", "board-limit I (fav.) A",
                       "P cont. @ rated rpm, fav. W", "weak W", "hot motor W",
                       "best P cont. W @ rpm", "basis"], rows, left_cols=2))
    _ct = (rec.get("stage3_pwm_controller") or {}).get("controller") or {}
    _bd = (rec.get("stage3_pwm_controller") or {}).get("board") or {}
    if _ct.get("build") in S3.RATINGS:
        o.append(f'<p class="note">Motor rated {rp["I_rms"]:.2f} A / {Hh.fmt(_v(rp["P_shaft_W"]))} W '
                 f'shaft at {rp["rpm"]:.0f} rpm. Controller: {Hh.esc(_ct.get("device"))} × '
                 f'{_ct.get("n_parallel", 1)} per switch, {Hh.fmt(_ct.get("f_carrier_hz", 0) / 1e3, 0)} kHz, '
                 f'm = {_ct.get("m")}. Board ratings of the calibrated one-device board (Si): '
                 + " / ".join("%g" % x for x in S3.RATINGS[_ct["build"]]["W"].values())
                 + ' W (favourable / weak airflow / next to a hot motor)'
                 + ((" — " + Hh.esc(_bd.get("scaling"))) if _bd.get("scaling") else "")
                 + '. System limit = the lower of motor and board.</p>')
    else:
        o.append('<p class="note">No controller set for this machine — PWM not computed.</p>')
    o.append(_cooling_html(rec))
    # charts
    o.append("<h2>Charts</h2><div class=\"charts\">")
    env = rec["envelope_card"]["curves"]
    ser = []
    for i, w in enumerate(("min", "nom", "max")):
        e = env[w]
        ser.append({"name": f"peak, {e['v_dc']:g} V", "x": [p["rpm"] for p in e["peak"]],
                    "y": [p["T_card"] for p in e["peak"]], "color": Hh.PALETTE[i]})
        ser.append({"name": f"I0, {e['v_dc']:g} V", "x": [p["rpm"] for p in e["rated_current"]],
                    "y": [p["T_card"] for p in e["rated_current"]], "color": Hh.PALETTE[i],
                    "dash": True})
    o.append(Hh.figure(Hh.line_chart(ser, xlab="speed, rpm", ylab="torque, N·m", ymin=0),
                       "Torque–speed envelope at v_min / v_nom / v_max: peak current (solid), "
                       "rated current (dashed); map × k_state × k_T, V limit with k_ψ, m = %g." %
                       rec["loss_grid"]["plan"]["m"]))
    em = rec["efficiency_map"]
    xs = em["rpm"]
    Ts = sorted({round(c[0], 4) for col in em["cells"] for c in col if c})
    if Ts:
        ty = list(np.linspace(0, max(Ts), 16))
        z = []
        for j, Tt in enumerate(ty):
            row = []
            for i in range(len(xs)):
                col = [c for c in em["cells"][i] if c]
                if not col:
                    row.append(None)
                    continue
                Tc = [c[0] for c in col]
                if Tt < min(Tc) or Tt > max(Tc):
                    row.append(None)
                    continue
                row.append(float(np.interp(Tt, Tc, [c[1] for c in col])))
            z.append(row)
        zz = [v for r in z for v in r if v is not None]
        nom = env["nom"]
        o.append(Hh.figure(Hh.heat_chart(xs, ty, z, xlab="speed, rpm", ylab="torque, N·m",
                                         zlab="η %", zmin=max(min(zz), 60.0) if zz else 60,
                                         zmax=max(zz) if zz else 100,
                                         overlay=[{"name": "peak (v_nom)",
                                                   "x": [p["rpm"] for p in nom["peak"]],
                                                   "y": [p["T_card"] for p in nom["peak"]],
                                                   "color": "#000"}]),
                           "Efficiency at the shaft (sine drive, hot), characterised region "
                           "only: grid speeds × 0.5·I0…I_peak on the m-limited trajectory."))
    sp = rec["loss_split_rated_current"]
    if sp:
        groups = [("Cu DC", "P_cu_dc_W"), ("Cu AC", "P_cu_ac_W"), ("Fe stator", "P_fe_stator_W"),
                  ("Fe rotor", "P_fe_rotor_W"), ("magnet", "P_mag_W"), ("shaft", "P_shaft_W"),
                  ("mech", "P_mech_W"), ("PWM extra", "P_pwm_extra_W")]
        o.append(Hh.figure(Hh.bar_chart([f"{r['rpm']:.0f}" for r in sp],
                                        [{"name": n, "values": [r.get(k) for r in sp]}
                                         for n, k in groups], ylab="loss, W"),
                           "Motor loss split vs speed at rated current (rpm on the axis); PWM "
                           "extra = Si 48 kHz variant (FEM anchor at rated speed, HDF-scaled)."))
    vv = [v for v in rec["pwm_variants"] if v.get("points")]
    if vv:
        cats, st_ = [], {"motor PWM extra": [], "inv. conduction": [], "inv. switching": [],
                         "inv. dead time": [], "board copper": []}
        for v in vv:
            for which in ("rated", "peak"):
                pts = [p for p in v["points"].values() if p.get("duty")
                       and (("peak" in str(p["duty"])) == (which == "peak"))]
                if not pts or pts[0].get("inverter_loss_W") is None:
                    continue
                p = pts[0]
                cats.append(f"{v['id']} {which}")
                st_["motor PWM extra"].append(p.get("motor_pwm_loss_W"))
                st_["inv. conduction"].append(p["inverter_loss_W"]["cond"])
                st_["inv. switching"].append(p["inverter_loss_W"]["sw"])
                st_["inv. dead time"].append(p["inverter_loss_W"]["dead"])
                st_["board copper"].append(p.get("board_copper_W"))
        o.append(Hh.figure(Hh.bar_chart(cats, [{"name": k, "values": v} for k, v in st_.items()],
                                        ylab="loss, W", h=330),
                           "PWM and controller losses at the rated and peak duty points per "
                           "variant (motor extra from FEM; inverter from the device cards / "
                           "SPICE tables; board copper from the controller R30 study)."))
    rr = dl.get("retention_rows") or []
    if rr:
        ser = [{"name": "Br kept, hot MTPA", "x": [r[0] for r in rr], "y": [r[1] for r in rr],
                "marker": True}]
        seq = [r for r in dl.get("sequence") or [] if r.get("torque_drop_pct") is not None]
        if seq:
            ser.append({"name": "rated-torque kept after overload",
                        "x": [r["I_over"] for r in seq],
                        "y": [100.0 - r["torque_drop_pct"] for r in seq], "marker": True})
        o.append(Hh.figure(Hh.line_chart(ser, xlab="overload current, A rms", ylab="%",
                                         hlines=[(99.5, "99.5"), (99.0, "99 (limit rule)"),
                                                 (98.0, "98")]),
                           "Demag retention vs current on the hot MTPA line at rated speed and "
                           "the rated-point torque kept after the overload (" + PEND + ")."))
    o.append("</div>")
    # tables
    o.append("<h2>Card values</h2>")
    rows = []

    def add(name, d, unit=None):
        if d is None:
            return
        rows.append([name, d.get("value"), unit or d.get("unit"), Hh.H(Hh.esc(d.get("method", "")) +
                                                                     "<br>" + Hh.labels(d.get("labels")))])
    add("Rated torque, card", rp.get("T_card_Nm"))
    add("Rated torque, operating 2-D", rp["T_operating_Nm"])
    add("Rated torque, static map (virgin)", rp["T_map_virgin_Nm"])
    ks = rp.get("k_state_split")
    if ks:
        rows.append(["k_state (operating / map)", ks["k_state"], "-",
                     Hh.H(Hh.esc(ks["method"]))])
    add("Rated EM loss", rp["P_loss_em_W"])
    add("Rated mech loss", rp["P_mech_W"])
    add("η shaft rated", rp["eta_shaft"])
    add("Peak torque, card", pk.get("T_card_Nm"))
    add("Peak k_state", pk["k_state"])
    add("Peak EM loss", pk["P_loss_em_W"])
    for k, d in c["constants_cold"].items():
        add(k + " (cold)", d)
    for k, d in c["constants_hot"].items():
        add(k + " (hot)", d)
    o.append(Hh.table(["quantity", "value", "unit", "basis"], rows, left_cols=1))
    o.append("<h2>3-D factors</h2>")
    rows = [[r["stack_mm"], r.get("k_psi"), r.get("k_flux_self"), r.get("B1_mid_T"),
             r.get("picard_converged"), Hh.H(Hh.esc(r.get("source", "")))]
            for r in s2["k_psi_curve"]]
    o.append(Hh.table(["stack mm", "k_ψ (3-D/2-D)", "k_flux_self", "B1 mid T", "converged",
                       "source"], rows))
    o.append(Hh.table(["factor", "value at this stack", "basis"],
                      [["k_ψ", _v(s2["k_psi"]), Hh.H(Hh.esc(s2["k_psi"]["method"]) + Hh.labels(s2["k_psi"]["labels"]))],
                       ["k_T", _v(s2["k_T"]), Hh.H(Hh.esc(s2["k_T"]["method"]) + Hh.labels(s2["k_T"]["labels"]))],
                       ["k_L", _v(s2["k_L"]), Hh.H(Hh.esc(s2["k_L"]["method"]) + Hh.labels(s2["k_L"]["labels"]))],
                       ["magnet segmentation", "—", Hh.H(Hh.esc(s2["magnet_segmentation"]["note"]))]]))
    o.append("<h2>Si vs GaN</h2>")
    rows = []
    for v in rec["pwm_variants"]:
        if not v.get("points"):
            rows.append([v["id"], v.get("device"), "—", "—", "—", "—", "—", "—", "—", "—",
                         Hh.H(Hh.esc(v.get("status", "")))])
            continue
        for p in v["points"].values():
            if not p.get("duty") or p.get("inverter_loss_W") is None:
                continue
            inv = p["inverter_loss_W"]
            rows.append([v["id"], v["device"], f"{p['rpm']:.0f} / {p['I_A']:.1f}",
                         p.get("motor_pwm_loss_W"), inv["cond"], inv["sw"], inv["dead"],
                         p["tj_C"] if p.get("continuous_ok") else "(110)",
                         "yes" if p.get("continuous_ok") else "no",
                         p["eta_drive_pct"], p.get("p_cont_max_W"),
                         Hh.H(Hh.labels(v["provenance"]["labels"]))])
    o.append(Hh.table(["variant", "device", "rpm / A", "motor PWM W", "cond W", "sw W",
                       "dead W", "T_j °C", "continuous", "η drive %", "P cont. W", "labels"],
                      rows, left_cols=3))
    o.append('<p class="note">η drive = battery → shaft incl. motor (sine + PWM extra + '
             'mech), inverter and board copper. T_j and P cont. from the board model '
             '(favourable airflow, T_amb 45 °C) — estimate; "(110)": the point is beyond the '
             'board\'s continuous limit, losses at the controller\'s design T_j 110 °C.</p>')
    rows = []
    for v in rec["pwm_variants"]:
        cv = v.get("coverage")
        if not cv:
            continue
        rows.append([v["id"], cv["points"], len(cv["fem_points"]), len(cv["scaled_points"]),
                     len(cv["infeasible"]), len(cv.get("no_continuous") or []),
                     Hh.H(Hh.esc(", ".join(x for x in (v["provenance"].get("motor_pwm_fem_runs")
                                                       or []) if x)))])
    if rows:
        gx = next((v.get("grid") for v in rec["pwm_variants"] if v.get("grid")), {}) or {}
        o.append("<h3>Drive-variant grid (what Configure reads)</h3>")
        o.append('<p class="note">rpm %s × I %s A rms. Infeasible nodes carry a status '
                 '(voltage limit beyond γ = 80°); "no cont." = no continuous operation at the '
                 'board-limit current at that speed.</p>' % (
                     " / ".join("%.0f" % x for x in gx.get("rpm", [])),
                     " / ".join("%.1f" % x for x in gx.get("I_A", []))))
        o.append(Hh.table(["variant", "points", "PWM FEM", "PWM scaled", "infeasible",
                           "no cont.", "PWM FEM runs"], rows))
    o.append("<h2>Loss grid (m = %g)</h2>" % rec["loss_grid"]["plan"]["m"])
    rows = []
    for g in rec["loss_grid"]["points"]:
        if "T_Nm" not in g:
            rows.append([g["plan"]["rpm"], g["plan"]["I"], "—", "—", "—", "—", "—",
                         Hh.H(Hh.esc(g.get("status", "")))])
            continue
        rows.append([g["rpm"], g["I_rms"], g["gamma"], g["T_Nm"], g.get("k_state"),
                     g["P_loss_em_W"], (g.get("efficiency") or {}).get("eta_shaft"),
                     Hh.H(Hh.esc(g["id"]))])
    o.append(Hh.table(["rpm", "I A", "γ °", "T N·m", "k_state", "P_em W", "η shaft", "run"],
                      rows))
    o.append("<h2>Checks</h2>")
    so = rec["checks"]["static_offgrid"]
    lo = rec["checks"]["loss_offgrid"]
    o.append(f'<p class="note">Static off-grid: {sum(1 for r in so if r.get("pass"))}/{len(so)} '
             f'pass (|Δψ| ≤ 0.5 %, |ΔT| ≤ 1 %). Loss off-grid: '
             f'{sum(1 for r in lo if r.get("pass"))}/{sum(1 for r in lo if r.get("pass") is not None)} '
             f'pass (≤ 5 %).</p>')
    rows = [[r["id"], r.get("I_rms"), r.get("gamma"), r.get("err_psi_d_pct_of_abs_psi"),
             r.get("err_psi_q_pct_of_abs_psi"), r.get("err_T_pct"),
             Hh.H('<span class="%s">%s</span>' % ("ok" if r.get("pass") else "bad",
                                                   "pass" if r.get("pass") else "fail"))]
            for r in so if "I_rms" in r]
    o.append(Hh.table(["check", "I A", "γ °", "Δψd %", "Δψq %", "ΔT %", ""], rows))
    rows = [[r["id"], r.get("rpm"), r.get("I"), r.get("P_fem_W"), r.get("P_int_W"),
             r.get("err_total_coil_corrected_pct", r.get("err_total_pct")),
             Hh.H('<span class="%s">%s</span>' % ("ok" if r.get("pass") else "bad",
                                                   {True: "pass", False: "fail", None: "—"}[r.get("pass")]))]
            for r in lo if "P_fem_W" in r]
    o.append(Hh.table(["loss check", "rpm", "I A", "FEM W", "interp W", "Δ %", ""], rows))
    o.append("<h2>Mechanics, thermal, demag</h2>")
    ct = rec["coupled_thermal"]
    cpl = ct.get("coupling") or {}
    o.append(Hh.table(["item", "value", "basis"], [
        ["rotor stress SF = 1 speed, rpm", mech.get("limit_speed_sf1_rpm"),
         Hh.H(Hh.labels(mech["labels"]))],
        ["SF at rated speed", mech.get("sf_at_rated"), Hh.H(Hh.labels(mech["labels"]))],
        ["critical speeds", Hh.H(Hh.esc(json.dumps(mech.get("critical"))[:300])),
         Hh.H(Hh.labels(["beam " + PEND]))],
        ["coupled EM-thermal (rated)", Hh.H("converged: %s · coil %s °C · magnet %s °C" % (
            cpl.get("converged"), Hh.fmt(cpl.get("coil_temp_c")), Hh.fmt(cpl.get("magnet_temp_c")))),
         Hh.H(Hh.esc(ct.get("thermal_settings_source") or ct.get("status") or ""))],
        ["demag limit (≤ 1 % torque drop)", dl.get("I_limit_rms"), Hh.H(Hh.esc(dl.get("how") or "")
                                                                      + Hh.labels(dl["labels"]))],
    ] + [["retention %s %% at" % k, v.get("I_rms"),
          "beyond last probe (%.1f A)" % v["last_probe_I"] if v.get("beyond_last_probe") and v.get("last_probe_I") else "Br-volume, hot MTPA"]
         for k, v in dl["retention_curve"].items()]))
    dv = (rec.get("demag_vs_temperature") or {}).get("rows") or []
    if dv:
        o.append("<h3>Rated point vs magnet temperature (owner decision aid)</h3>")
        o.append(Hh.table(["magnet °C", "I A rms", "torque lost to demag %", "Br kept %", "basis"],
                          [[r.get("magnet_temp_c"), r.get("I_rms"), r.get("torque_drop_pct"),
                            r.get("br_kept_vol_pct"), H_(r.get("method") or "")] for r in dv]))
    o.append(f"<footer>Pending owner decisions: {Hh.esc(json.dumps(rec.get('pending_owner_defaults'))[:600])}"
             f"<br>Snapshot {Hh.esc(rec['provenance']['snapshot_sha256'])} · runs ok "
             f"{rec['provenance']['runs_ok']} · schema {Hh.esc(rec['schema'])}</footer>")
    return Hh.page(f"{M} passport", "".join(o), f"{title} motor passport {M}")


def H_(s):
    return Hh.H(Hh.esc(s))


def compare_html(recs: Mapping[str, Mapping[str, Any]]) -> str:
    Ms = list(recs)
    o = ["<h1>Ø40 passport — L12 vs L20</h1>",
         '<p class="sub">Same die, 12 / 20 mm stack, 6S / 12S. Card torque = operating × k_T '
         '(' + PEND + ').</p>']

    def g(rec, path):
        x = rec
        for k in path:
            x = (x or {}).get(k) if isinstance(x, dict) else None
        return x
    rows = []
    items = [("bus V (min/nom/max)", lambda r: "%g/%g/%g" % (r["inputs"]["battery"]["v_min"],
                                                             r["inputs"]["battery"]["v_nom"],
                                                             r["inputs"]["battery"]["v_max"])),
             ("hot magnet / winding °C", lambda r: "%g / %g" % (r["inputs"]["temperatures"]["hot_magnet_c"],
                                                                r["inputs"]["temperatures"]["hot_coil_c"])),
             ("rated rpm / A", lambda r: "%g / %.2f" % (r["card"]["rated_point"]["rpm"], r["card"]["rated_point"]["I_rms"])),
             ("rated torque card N·m", lambda r: g(r, ["card", "rated_point", "T_card_Nm", "value"])),
             ("rated torque map (virgin) N·m", lambda r: g(r, ["card", "rated_point", "T_map_virgin_Nm", "value"])),
             ("rated shaft power W", lambda r: g(r, ["card", "rated_point", "P_shaft_W", "value"])),
             ("rated η shaft", lambda r: g(r, ["card", "rated_point", "eta_shaft", "value"])),
             ("peak rpm / A", lambda r: "%g / %.2f" % (r["card"]["peak_point"]["rpm"], r["card"]["peak_point"]["I_rms"])),
             ("peak torque card N·m", lambda r: g(r, ["card", "peak_point", "T_card_Nm", "value"])),
             ("Kv cold 2-D rpm/V", lambda r: g(r, ["card", "constants_cold", "Kv_rpm_per_V_line", "value"])),
             ("Kv cold 3-D rpm/V", lambda r: g(r, ["card", "constants_cold", "Kv_3d_rpm_per_V_line", "value"])),
             ("R phase 20 °C Ω", lambda r: g(r, ["card", "constants_cold", "R_phase_ohm", "value"])),
             ("Ld / Lq small-signal hot µH", lambda r: "%s / %s" % (
                 Hh.fmt(1e3 * (g(r, ["card", "constants_hot", "Ld_small_signal_mH", "value"]) or 0)),
                 Hh.fmt(1e3 * (g(r, ["card", "constants_hot", "Lq_small_signal_mH", "value"]) or 0)))),
             ("k_ψ / k_T / k_L", lambda r: "%s / %s / %s" % (Hh.fmt(g(r, ["stage2_3d", "k_psi", "value"])),
                                                         Hh.fmt(g(r, ["stage2_3d", "k_T", "value"])),
                                                         Hh.fmt(g(r, ["stage2_3d", "k_L", "value"])))),
             ("demag limit A (≤1 % drop)", lambda r: g(r, ["demag_limit", "I_limit_rms"])),
             ("rotor stress SF 1 rpm", lambda r: g(r, ["mechanical", "limit_speed_sf1_rpm"])),
             ]
    for name, f in items:
        rows.append([name] + [f(recs[M]) for M in Ms])
    o.append(Hh.table(["quantity"] + Ms, rows))
    o.append("<h2>System limit and Si vs GaN (rated duty point)</h2>")
    rows = []
    for M in Ms:
        rp = recs[M]["card"]["rated_point"]
        for v in recs[M]["pwm_variants"]:
            if not v.get("points"):
                rows.append([M, v["id"], v.get("device"), "—", "—", "—", "—", "—", "—",
                             Hh.H(Hh.esc(v.get("status", "")))])
                continue
            pr = [p for p in v["points"].values() if p.get("duty") and abs(p["rpm"] - rp["rpm"]) < 1e-6
                  and abs(p["I_A"] - rp["I_rms"]) < 1e-6]
            if not pr:
                continue
            p = pr[0]
            inv = p["inverter_loss_W"]
            rows.append([M, v["id"], v["device"], p.get("motor_pwm_loss_W"),
                         inv["cond"] + inv["sw"] + inv["dead"],
                         p["tj_C"] if p.get("continuous_ok") else "(110) not cont.",
                         p["eta_drive_pct"],
                         v["i_board_limit_A"]["favourable"], p.get("p_cont_max_W"),
                         Hh.H(Hh.labels(v["provenance"]["labels"]))])
    o.append(Hh.table(["machine", "variant", "device", "motor PWM W", "inverter W", "T_j °C",
                       "η drive %", "board I A", "P cont. W", "labels"], rows, left_cols=3))
    o.append('<p class="note">Rated duty point, nominal bus. The motor\'s rated current is above '
             'the board\'s continuous limit for every device: "(110) not cont." = no steady state '
             'on this board, inverter losses at the design T_j 110 °C. P cont. = shaft power at '
             'the board-limit current (favourable airflow) at rated speed — the system limit. '
             'GaN 100 kHz motor PWM loss = estimate from the 48 kHz FEM (no FEM at 100 kHz).</p>')
    ser = []
    for i, M in enumerate(Ms):
        e = recs[M]["envelope_card"]["curves"]["nom"]
        ser.append({"name": f"{M} peak", "x": [p["rpm"] for p in e["peak"]],
                    "y": [p["T_card"] for p in e["peak"]], "color": Hh.PALETTE[i]})
        ser.append({"name": f"{M} I0", "x": [p["rpm"] for p in e["rated_current"]],
                    "y": [p["T_card"] for p in e["rated_current"]], "color": Hh.PALETTE[i],
                    "dash": True})
    o.append('<div class="charts">' + Hh.figure(Hh.line_chart(ser, xlab="speed, rpm",
                                                               ylab="torque, N·m", ymin=0),
                                               "Envelopes at the nominal bus (card torque).")
             + "</div>")
    return Hh.page("Ø40 passport compare", "".join(o), "L12 vs L20 passport comparison")
