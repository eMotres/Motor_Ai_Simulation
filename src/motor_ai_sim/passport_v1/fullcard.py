"""The full Ø40 passport card: stage 1 record + 3-D factors + mechanics +
coupled thermal + demag current limit + PWM / controller (stage 3), and the
HTML cards.  Pure post-processing of solved results (no FEM here)."""
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

def stage2_block(full_dir: Path, repo: Path, L_mm: float) -> Dict[str, Any]:
    sa = _load(full_dir / "stage_a.json")
    ee = _load(repo / "config" / "end_effect_3d.json") or {}
    sb = ee.get("stage_b") or {}
    lsf = ((sb.get("long_stack_honesty_test") or {}).get("the_inductive_end_effect_factor") or {})
    a, b = lsf.get("fit_a_uH_per_mm"), lsf.get("fit_b_uH")
    kT12 = ((sb.get("torque") or {}).get("k_T"))
    out: Dict[str, Any] = {"L_mm": L_mm}
    curve = []
    if sa:
        for r in sa.get("l_stack_curve") or []:
            curve.append({"stack_mm": r.get("stack_mm"), "k_flux": r.get("k_flux"),
                          "k_flux_self": r.get("k_flux_self"),
                          "picard_converged": r.get("picard_converged")})
        curve.sort(key=lambda r: r["stack_mm"])
    out["k_psi_curve"] = curve
    kpsi = None
    if curve:
        xs = [r["stack_mm"] for r in curve if r["k_flux"] is not None]
        ys = [r["k_flux"] for r in curve if r["k_flux"] is not None]
        if xs:
            kpsi = float(np.interp(L_mm, xs, ys))
    out["k_psi"] = {"value": kpsi, "method": "3-D magnetostatic Stage A (I = 0): axial mean of "
                    "the gap fundamental / the 2-D leg, interpolated in L; today's die "
                    "cross-section, n_stack 4 (quick fidelity)",
                    "labels": ["3-D", "no-load", "quick fidelity"],
                    "source": "out/full/stage_a.json" if sa else "NOT AVAILABLE",
                    "wall_s": ((sa or {}).get("pilot") or {}).get("wall_s"),
                    "converged_all": (all(r["picard_converged"] for r in curve)
                                      if curve else None)}
    kT = None
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

def pwm_classes(full_dir: Path, M: str, v_dc: float) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
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
        out.setdefault(cls, {})[d.get("point")] = row
    return out


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
#  the full record
# ─────────────────────────────────────────────────────────────────────────────

def build_full(work: Path, repo: Path, M: str, machine_meta: Mapping[str, Any]) -> Dict[str, Any]:
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
    s2 = stage2_block(full, repo, L)
    rec["stage2_3d"] = s2
    rec["mechanical"] = mech_block(full, M)
    cp = _load(full / f"coupled_{M}.json")
    rec["coupled_thermal"] = ({"converged": (cp.get("coupling") or {}).get("converged"),
                               "coupling": cp.get("coupling"), "wall_s": cp.get("wall_s"),
                               "thermal_settings_source": cp.get("thermal_settings_source"),
                               "used_for_card": bool((cp.get("coupling") or {}).get("converged"))}
                              if cp else {"status": "not run for this machine — hot temperatures "
                                          "from the rated duty, labelled"})
    rec["demag_limit"] = demag_limit(st, full, M, I0)
    s3 = S3.build_variants(machine=M, rec=rec, snap=snap, hm=hm, rows_grid=rows_grid,
                           pwm_fem=pwm_classes(full, M, v_dc), variants=VARIANTS[M],
                           r_g_si=CTRL[M]["r_g"], build=CTRL[M]["build"], si_part=CTRL[M]["si"])
    rec["pwm_variants"] = s3["pwm_variants"] + NOT_COMPUTED_VARIANTS.get(M, [])
    rec["stage3_pwm_controller"] = {"board": s3["board"],
                                    "status": "computed (see pwm_variants)",
                                    "controller_source": S3.CTRL_SOURCE}
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
        nmax = hm.max_speed(R_hot, vl_eff, 0.02 * I_pk)
        pts = []
        for n in np.linspace(max(0.05 * n0, 200.0), min(nmax, 4.0 * n0), 36):
            mt = hm.max_torque_at(float(n), R_hot, vl_eff, I_pk)
            if mt.get("T") is None:
                continue
            k = S3.k_state_at(rec["loss_grid"]["points"], float(n), mt["I"])
            pts.append({"rpm": float(n), "T_map": mt["T"], "I": mt["I"], "gamma": mt["gamma"],
                        "T_card": mt["T"] * (k or 1.0) * kT, "k_state": k})
        cont = []
        for n in np.linspace(max(0.05 * n0, 200.0), min(nmax, 4.0 * n0), 36):
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


def machine_html(rec: Mapping[str, Any], M: str) -> str:
    c = rec["card"]
    rp, pk = c["rated_point"], c["peak_point"]
    s2 = rec["stage2_3d"]
    mech = rec["mechanical"]
    dl = rec["demag_limit"]
    inp = rec["inputs"]
    t = inp["temperatures"]
    bat = inp["battery"]
    o = []
    o.append(f"<h1>{Hh.esc(rec['machine']['configuration'])} — {M} passport</h1>")
    o.append(f'<p class="sub">Die {Hh.esc(rec["machine"]["die"])} · bus {bat["v_min"]:g}/'
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
            rows.append([v["id"], v.get("device"), "—", "—", "—", "—", H_(v.get("status", ""))])
            continue
        pr = [p for p in v["points"].values() if p.get("duty") and abs(p["rpm"] - rp["rpm"]) < 1e-6
              and abs(p["I_A"] - rp["I_rms"]) < 1e-6]
        pr = pr[0] if pr else {}
        bc = pr.get("p_cont_max_W_by_cooling") or {}
        rows.append([v["id"], v["device"], v["i_board_limit_A"]["favourable"],
                     bc.get("favourable"), bc.get("weak"), bc.get("hot_motor"),
                     Hh.H(Hh.labels(["estimate", PEND]))])
    o.append(Hh.table(["variant", "device", "board-limit I (fav.) A", "P cont. fav. W",
                       "weak W", "hot motor W", "basis"], rows, left_cols=2))
    o.append(f'<p class="note">Motor rated {rp["I_rms"]:.2f} A / {Hh.fmt(_v(rp["P_shaft_W"]))} W '
             f'shaft at {rp["rpm"]:.0f} rpm. Controller ratings (Si, README): '
             f'{Hh.esc(S3.RATINGS[CTRL[M]["build"]]["W"])} W. System limit = the lower.</p>')
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
    rows = [[r["stack_mm"], r["k_flux"], r["k_flux_self"], r["picard_converged"]]
            for r in s2["k_psi_curve"]]
    o.append(Hh.table(["stack mm", "k_ψ (3-D/2-D)", "k_flux_self", "converged"], rows))
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
                         p["tj_C"], p["eta_drive_pct"], p.get("p_cont_max_W"),
                         Hh.H(Hh.labels(v["provenance"]["labels"]))])
    o.append(Hh.table(["variant", "device", "rpm / A", "motor PWM W", "cond W", "sw W",
                       "dead W", "T_j °C", "η drive %", "P cont. W", "labels"], rows,
                      left_cols=3))
    o.append('<p class="note">η drive = battery → shaft incl. motor (sine + PWM extra + '
             'mech), inverter and board copper. T_j and P cont. from the board model '
             '(favourable airflow, T_amb 45 °C) — estimate.</p>')
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
    o.append(f"<footer>Pending owner decisions: {Hh.esc(json.dumps(rec.get('pending_owner_defaults'))[:600])}"
             f"<br>Snapshot {Hh.esc(rec['provenance']['snapshot_sha256'])} · runs ok "
             f"{rec['provenance']['runs_ok']} · schema {Hh.esc(rec['schema'])}</footer>")
    return Hh.page(f"{M} passport", "".join(o), f"Ø40 motor passport {M}")


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
                         inv["cond"] + inv["sw"] + inv["dead"], p["tj_C"], p["eta_drive_pct"],
                         v["i_board_limit_A"]["favourable"], p.get("p_cont_max_W"),
                         Hh.H(Hh.labels(v["provenance"]["labels"]))])
    o.append(Hh.table(["machine", "variant", "device", "motor PWM W", "inverter W", "T_j °C",
                       "η drive %", "board I A", "P cont. W", "labels"], rows, left_cols=3))
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
