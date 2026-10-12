"""Write a propeller-S1 motor model (``config/propeller_s1/<die>/<config>.json``).

The S1 solver (``motor_ai_sim.propeller_s1``) needs, per configuration, a torque
map and a loss grid over (rpm, current) with the temperatures they hold at, the
copper resistance, the flux-vs-magnet-temperature slope, the current and speed
limits, the lumped thermal geometry and (optionally) the drive variant's
controller losses.  The full v1 passports carry all of it but live in
``docs/data`` (not in the API image), and the server's passport store keeps only
the drive variants - so this script extracts the compact model once.

Two sources::

    # a full v1 passport (Ø40 L12 / L20): everything from the passport itself
    python scripts/export_propeller_s1_model.py full docs/data/passport_pilot_d40/full/passport_L12.json \
        --store <store L12.json> --catalog <motor_catalog.json> --card cat_ciano14_40_new --magnet-limit 150

    # a passport-store record only (Ø85 L13: the full record is not kept): torque and
    # total motor loss from the drive variant's points, copper split by R at the
    # temperature the record was computed at, flux slope from the magnet card
    python scripts/export_propeller_s1_model.py store <store L13.json> --catalog ... --card cat_ciano28_85 \
        --r-ref 0.093962 --r-temp 149.3 --coil-c 180 --magnet-c 150 --flux-coeff -0.001199 --magnet-limit 150

Writes to ``config/propeller_s1/<die>/<config>.json`` (``--out`` overrides).

Schema 2 (2026-10-11 review fixes) also carries what the S1 limit needs to be a
SYSTEM rating, not a motor-only one: every computed drive variant of the store
record (``drives``: its own controller-loss and motor-PWM-extra grids, the
board's continuous current per cooling class, the bus range), the die's
controller block for a sine drive (``sine_controller``), the passport battery,
the hot psi-map on a regular (I, gamma) table with the loss grid's operating
angle (``psi_map``, ``grid.gamma_deg``: the voltage re-check at the minimum pack
voltage and the operating magnet temperature), the demagnetisation current
limit with the magnet card's Br/HcJ temperature laws (``demag``), and the
status of every loss group (``loss_groups``: a missing cell is ``null``, never 0).
``--geom-from`` takes ``thermal_geom`` from an existing model when the catalogue
card is not at hand.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
ALPHA_CU = 0.00393
SCHEMA = "propeller-s1-model-2"
MU0 = 4e-7 * math.pi

#: model loss group <- the passport's groups_W keys: the REQUIRED term(s) (a point
#: without one has no value for the group: null, never 0) and the optional
#: sub-terms that are 0 when a machine has no such part (no sleeve, no parallel paths)
LOSS_TERMS = {"cu_ac": (("P_cu_ac_W",), ("P_cu_circulating_W",)),
              "fe_stator": (("P_fe_stator_W",), ()),
              "fe_rotor": (("P_fe_rotor_W",), ()),
              "rotor_eddy": (("P_mag_W",), ("P_shaft_W", "P_sleeve_W"))}

#: the permeance coefficient behind a magnet grade's maximum working temperature
#: (L/D = 0.7 convention): the self-demagnetising field the demag scaling assumes
DEMAG_PERMEANCE_COEFF = 2.0
#: the generated magnet cards put the knee at this fraction of HcJ (materials_library.yaml)
KNEE_FRACTION = 0.97


def _load(p: str) -> Dict[str, Any]:
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _val(d: Any) -> Optional[float]:
    if isinstance(d, dict):
        d = d.get("value")
    try:
        x = float(d)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _key(rpm: float, i: float) -> tuple:
    return (round(float(rpm), 1), round(float(i), 2))


def _variant(store: Optional[Dict[str, Any]], vid: Optional[str]) -> Optional[Dict[str, Any]]:
    if not store:
        return None
    vs = [v for v in store.get("pwm_variants") or [] if isinstance(v, dict) and v.get("points")]
    if vid:
        vs = [v for v in vs if v.get("id") == vid]
    return vs[0] if vs else None


def _variant_tables(v: Dict[str, Any]) -> Dict[str, Any]:
    rpm = [float(x) for x in v["grid"]["rpm"]]
    cur = [float(x) for x in v["grid"]["I_A"]]
    by = {_key(p["rpm"], p["I_A"]): p for p in v["points"].values() if isinstance(p, dict) and "rpm" in p}

    def table(fn) -> List[List[Optional[float]]]:
        out = []
        for r in rpm:
            row = []
            for i in cur:
                p = by.get(_key(r, i))
                row.append(None if (p is None or p.get("status")) else fn(p))
            out.append(row)
        return out

    def ctrl(p):
        inv = p.get("inverter_loss_W") or {}
        if not inv:
            return None
        return float(sum(float(x or 0) for x in inv.values())) + float(p.get("board_copper_W") or 0.0)

    return {"rpm": rpm, "I_A": cur, "table": table, "ctrl": table(ctrl)}


def _thermal_geom(catalog: str, card_id: str, stack_mm: float) -> Dict[str, float]:
    cat = _load(catalog)
    card = next((m for m in cat.get("motors", []) if m.get("id") == card_id), None)
    if card is None:
        raise SystemExit(f"card {card_id!r} not in {catalog}")
    sp = card["passport"]
    geo, fit = sp["geo"], sp["fit"]
    # the same derivation as ConfiguratorPanel.tsx thermalGeom
    return {"statorOD_mm": 2 * geo["statorOR_mm"], "stackLength_mm": float(stack_mm),
            "numSlots": geo["numSlots"], "slotHeight_mm": fit["slotHeight_mm"],
            "slotWidth_mm": fit["slotWidth_mm"], "insulation_mm": fit["insulation_mm"],
            "coreThickness_mm": max(0.0, geo["statorOR_mm"] - geo["statorIR_mm"] - fit["slotHeight_mm"]),
            "airGap_mm": max(0.0, geo["statorIR_mm"] - geo["rotorOR_mm"]),
            "magnetOD_mm": 2 * geo["rotorOR_mm"], "source": f"catalogue card {card_id} (passport fit/geo)"}


def _drive(v: Dict[str, Any]) -> Dict[str, Any]:
    """One computed drive variant of the store record, on its OWN (rpm, I) grid:
    controller losses, the motor's PWM extra loss, the passport's per-point
    continuous / board / T_j verdicts (1 / 0 / null) and the board's continuous
    current per cooling class (the binding controller envelope)."""
    t = _variant_tables(v)
    flag = lambda k: t["table"](lambda p: None if p.get(k) is None else (1 if p.get(k) else 0))  # noqa: E731
    pwm = t["table"](lambda p: _val(p.get("motor_pwm_loss_W")))
    prov = v.get("provenance") or {}
    lim = {k: float(x) for k, x in (v.get("i_board_limit_A") or {}).items() if _val(x) is not None}
    return {"id": v.get("id"), "device": v.get("device"), "technology": v.get("technology"),
            "carrier_hz": _val(v.get("carrier_hz")), "n_parallel": v.get("n_parallel"),
            "dead_time_s": v.get("dead_time_s"), "m_max": _val(v.get("m_max")),
            "bus_v": v.get("bus_v"), "bus_evaluated": v.get("bus_evaluated"),
            "i_board_limit_A": lim or None,
            "i_board_basis": ("board continuous current of this build (all devices in parallel) per board "
                              "cooling class: %s" % (prov.get("thermal") or "passport board model")),
            "rpm": t["rpm"], "I_A": t["I_A"],
            "controller_loss_W": t["ctrl"],
            "pwm_extra_W": pwm if any(x is not None for r in pwm for x in r) else None,
            "continuous_ok": flag("continuous_ok"), "board_ok": flag("board_ok"), "tj_ok": flag("tj_ok"),
            "basis": "inverter conduction + switching + dead time + board copper; motor PWM extra loss "
                     "(%s), passport drive variant" % (prov.get("motor_pwm") or "passport")}


def _drives(store: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not store:
        return {}
    return {v["id"]: _drive(v) for v in store.get("pwm_variants") or []
            if isinstance(v, dict) and v.get("points") and v.get("id")}


def _sine_controller(d: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The die's controller block (``stage3_pwm_controller.board``): the board's
    continuous current per cooling class - what a sine-drive S1 must also respect."""
    b = ((d.get("stage3_pwm_controller") or {}).get("board") or {})
    cl = {k: float(c["I_rating_si_A"]) for k, c in (b.get("classes") or {}).items()
          if _val((c or {}).get("I_rating_si_A")) is not None}
    if not cl:
        return None
    bat = d["inputs"].get("battery") or {}
    return {"i_board_limit_A": cl,
            "i_board_basis": "the die's controller board, continuous current of its Si build per cooling class "
                             "(%s)" % (b.get("model") or "passport board model"),
            # the ratings were made for the passport's own pack
            "bus_v": ({"min": bat.get("v_min"), "nom": bat.get("v_nom"), "max": bat.get("v_max")}
                      if _val(bat.get("v_min")) and _val(bat.get("v_max")) else None),
            "source": b.get("source"), "T_amb_C": _val(b.get("T_amb_C")), "labels": b.get("labels") or []}


def _pole_pairs(pts: List[Dict[str, Any]]) -> int:
    """From the map's own torque identity T = 3/2·p·(psi_d·i_q - psi_q·i_d)."""
    ps = []
    for p in pts:
        den = 1.5 * (float(p["psi_d"]) * float(p["i_q"]) - float(p["psi_q"]) * float(p["i_d"]))
        if abs(den) > 1e-9 and abs(float(p["T"])) > 1e-6:
            ps.append(float(p["T"]) / den)
    if not ps:
        raise SystemExit("hot_map: cannot infer the pole pairs")
    ps.sort()
    return int(round(ps[len(ps) // 2]))


def _psi_map(d: Dict[str, Any], gamma_max: float = 80.0) -> Dict[str, Any]:
    """The passport's hot psi-map (Clough-Tocher in (i_d, i_q), the passport's own
    interpolant) sampled on a regular (I_rms, gamma) table, so the S1 solver can
    re-check the phase voltage with plain bilinear interpolation."""
    sys.path.insert(0, str(ROOT / "src"))
    from motor_ai_sim.passport_v1 import psimap as PM
    pts = [p for p in d["hot_map"]["points"] if all(k in p for k in ("i_d", "i_q", "psi_d", "psi_q", "T"))]
    p_pairs = _pole_pairs(pts)
    pm = PM.PsiMap.build(pts, p_pairs)
    levels = sorted({round(float(p["I"]), 4) for p in pts if float(p["I"]) > 0})
    top = levels[-1]
    cur = [0.0]
    for a, b in zip([0.0] + levels, levels):
        cur += [round(a + (b - a) / 2, 4), b]
    gam = [round(-5.0 + 2.5 * k, 3) for k in range(int((gamma_max + 5.0) / 2.5) + 1)]
    # the top level lies on the hull's chords between its sampled angles: step just inside
    for f in (0.999, 0.995, 0.99, 0.985, 0.98):
        cur[-1] = round(top * f, 4)
        if all(math.isfinite(pm.at_Ig(cur[-1], g)["psi_d"]) for g in gam):
            break

    def tab(key):
        out = []
        for I in cur:
            row = []
            for g in gam:
                o = pm.at_Ig(I, g) if I > 0 else pm.at(0.0, 0.0)
                v = o.get(key)
                row.append(None if v is None or not math.isfinite(v) else float(v))
            out.append(row)
        return out
    psi_pm = _val(pm.at(0.0, 0.0)["psi_d"])
    return {"I_A": cur, "gamma_deg": gam, "psi_d_Wb": tab("psi_d"), "psi_q_Wb": tab("psi_q"),
            "T_Nm": tab("T"), "pole_pairs": p_pairs, "psi_pm_Wb": psi_pm,
            "magnet_C": float(d["inputs"]["temperatures"]["hot_magnet_c"]), "gamma_max_deg": gamma_max,
            "basis": "passport hot psi-map (2-D, virgin magnets at %g C, %d FEM points), sampled on a regular "
                     "(I, gamma) table; amplitude-invariant peak dq, gamma from the q-axis"
                     % (float(d["inputs"]["temperatures"]["hot_magnet_c"]), len(pts))}


def _magnet_card(name: str) -> Dict[str, Any]:
    import yaml
    lib = yaml.safe_load((ROOT / "config" / "materials_library.yaml").read_text(encoding="utf-8"))
    card = None
    for sec in lib.values() if isinstance(lib, dict) else []:
        if isinstance(sec, dict) and isinstance(sec.get(name), dict):
            card = sec[name]
    if card is None:
        raise SystemExit(f"magnet card {name!r} not in materials_library.yaml")
    bh = card.get("bh_curve") or []
    # HcJ = the intrinsic coercivity: the B-H point where J = B + mu0*H crosses 0
    hcj = None
    for (h0, b0), (h1, b1) in zip(bh, bh[1:]):
        j0, j1 = b0 - MU0 * h0, b1 - MU0 * h1
        if j0 <= 0 <= j1 and j1 != j0:
            hcj = -(h0 + (h1 - h0) * (0 - j0) / (j1 - j0))
            break
    if hcj is None:
        raise SystemExit(f"magnet card {name!r}: no J = 0 crossing in bh_curve (HcJ)")
    return {"name": name, "T_ref_C": float(card["temperature_c"]), "Br_ref_T": float(card["Br"]),
            "alpha_br_pct_per_K": float(card["alpha_br_pct_per_k"]), "Hcj_ref_A_m": float(hcj),
            "beta_hcj_pct_per_K": float(card["beta_hcj_pct_per_k"]), "mu_rec": float(card.get("mu_rec") or 1.05),
            "knee_fraction": KNEE_FRACTION, "max_working_temp_C": _val(card.get("max_working_temp_c")),
            "source": "materials_library.yaml card %s (HcJ = the J = 0 crossing of its bh_curve)" % name}


def _demag(d: Dict[str, Any], t_mag: float) -> Dict[str, Any]:
    dl = d["demag_limit"]
    out = {"I_limit_A": _val(dl["I_limit_rms"]), "ref_magnet_C": t_mag,
           "rule": dl.get("rule"), "how": dl.get("how"),
           # the passport probed demagnetisation at ONE magnet temperature (its hot state)
           "temperature_data": "reference temperature only",
           "permeance_coeff": DEMAG_PERMEANCE_COEFF}
    name = ((d["inputs"].get("materials") or {}).get("magnet") or {}).get("name")
    if name:
        out["magnet"] = _magnet_card(name)
    return out


def _floors(args, rpm: List[float], labels: List[str]) -> Dict[str, Any]:
    """``{"I_floor_A": [...]}`` from ``--floors`` (JSON ``{rpm: [...], I_floor_A: [...],
    basis}``: the smallest current that holds the voltage at each speed), or {}."""
    if not getattr(args, "floors", None):
        return {}
    f = _load(args.floors)
    by = {round(float(r), 1): float(i or 0.0) for r, i in zip(f["rpm"], f["I_floor_A"])}
    missing = [r for r in rpm if round(r, 1) not in by]
    if missing:
        raise SystemExit(f"--floors has no value for {missing} rpm")
    labels.append("field-weakening floor current per speed (%s)" % f.get("basis", "floors file"))
    return {"I_floor_A": [by[round(r, 1)] for r in rpm]}


def from_full(args) -> Dict[str, Any]:
    d = _load(args.source)
    store = _load(args.store) if args.store else None
    v = _variant(store, args.variant)
    temps = d["inputs"]["temperatures"]
    plan = d["loss_grid"]["plan"]
    t_coil, t_mag, t_cold = float(temps["hot_coil_c"]), float(temps["hot_magnet_c"]), float(temps["cold_c"])
    r_hot = float(plan["R_hot_ohm"])
    ch, cc = d["card"]["constants_hot"], d["card"]["constants_cold"]
    if abs(_val(ch["R_phase_ohm"]) - r_hot) > 1e-9:
        raise SystemExit("R_hot of the loss grid differs from constants_hot.R_phase_ohm")
    psi_h, psi_c = _val(ch["psi_PM_Wb"]), _val(cc["psi_PM_Wb"])
    # the solver applies k = 1 + c·(T - T_hot) around the HOT flux, so the slope is
    # normalised to the hot flux: k(T_hot) = 1 and psi_h·k(T_cold) = psi_c exactly
    c_psi = flux_slope(psi_h, t_mag, psi_c, t_cold)
    pts = [p for p in d["loss_grid"]["points"] if p.get("rpm") is not None]
    extra: List[Dict[str, Any]] = []
    for f in args.extra_points or []:
        extra += [p for p in _load(f)["points"] if p.get("rpm") is not None]
    rpm = sorted({float(x) for x in plan["speeds"]} | {float(p["rpm"]) for p in extra})
    cur = [float(x) for x in plan["currents"]]
    by = {_key(p["rpm"], p["I_rms"]): p for p in pts + extra}

    def table(fn):
        out = []
        for r in rpm:
            row = []
            for i in cur:
                p = by.get(_key(r, i))
                row.append(None if p is None else fn(p))
            out.append(row)
        return out

    loss = {k: table(lambda p, req=req, opt=opt: loss_group(p, req, opt))
            for k, (req, opt) in LOSS_TERMS.items()}
    missing = {k: sum(1 for a, row in enumerate(t) for b, x in enumerate(row)
                      if x is None and by.get(_key(rpm[a], cur[b])) is not None) for k, t in loss.items()}
    labels = ["2-D FEM loss grid (settled TDM, coupled eddy + demag) of the full v1 passport",
              f"torque x k_T(3-D) {(_val(d['stage2_3d']['k_T']) or 1.0):.4f}"]
    drives = _drives(store)
    if drives:
        labels.append("drive variants %s: controller loss, motor PWM extra loss and board continuous current "
                      "on each variant's own grid" % ", ".join(drives))
    if extra:
        labels.append("loss grid extended to %s rpm by %d settled FEM points (passport stage loss4, "
                      "same hot temperatures / steps / operating-angle rule)"
                      % ("/".join("%g" % r for r in sorted({float(p["rpm"]) for p in extra})), len(extra)))
    mech = d["loss_grid"].get("mech") or {}
    mech_pts = sorted((float(k), float(x["P_W"])) for k, x in mech.items()
                      if _val(x.get("P_W")) is not None)
    if not mech_pts:
        labels.append("mechanical loss UNKNOWN (no bearings named in the passport) — not included")
    stack = (v or {}).get("build", {}).get("length_mm") if v else None
    stack = float(args.stack_mm or stack or d["inputs"]["mesh"].get("motor_length") or 0)
    if not stack:
        raise SystemExit("stack length unknown: pass --stack-mm")
    mech_W = ({"rpm": [x[0] for x in mech_pts], "W": [x[1] for x in mech_pts],
               "basis": "bearings (SKF model) + windage, passport"} if len(mech_pts) >= 2 else None)
    groups = {k: {"status": "grid", "missing_cells": missing[k],
                  "terms": list(LOSS_TERMS[k][0]) + list(LOSS_TERMS[k][1])} for k in loss}
    groups["pwm_extra"] = {"status": "per drive", "note": "0 for a sine drive; each PWM variant's own grid"}
    groups["mech"] = ({"status": "grid", "note": mech_W["basis"]} if mech_W
                      else {"status": "omitted", "note": "no bearings named in the passport"})
    bat = d["inputs"].get("battery") or {}
    m = {
        "schema": SCHEMA,
        "die": d["machine"]["die"], "config": d["machine"]["configuration"],
        "source": {"kind": "full v1 passport", "file": Path(args.source).as_posix().split("/docs/")[-1]
                   if "/docs/" in Path(args.source).as_posix() else Path(args.source).name,
                   "snapshot_sha256": d["provenance"].get("snapshot_sha256"),
                   "store_variant": (v or {}).get("id"),
                   **({"extra_points": [Path(f).name for f in args.extra_points]} if extra else {}),
                   **({"floors": Path(args.floors).name} if getattr(args, "floors", None) else {})},
        "R_phase_ohm": r_hot, "R_temp_C": t_coil,
        "magnet_ref_temp_C": t_mag,
        "flux_temp_coeff_per_K": c_psi,
        "flux_coeff_basis": (f"passport psi_PM hot {psi_h:.6g} Wb @ {t_mag:g} C vs cold {psi_c:.6g} Wb @ {t_cold:g} C, "
                             "(psi_h - psi_c) / (psi_h (T_h - T_c)): normalised to the hot reference"),
        "torque_factor": _val(d["stage2_3d"]["k_T"]) or 1.0,
        "grid": {"rpm": rpm, "I_A": cur, "T_Nm": table(lambda p: float(p["T_Nm"])), "loss_W": loss,
                 "gamma_deg": table(lambda p: _val(p.get("gamma"))),
                 **_floors(args, rpm, labels)},
        "loss_groups": groups,
        "mech_W": mech_W,
        "drives": drives,
        "default_drive": (v or {}).get("id"),
        "sine_controller": _sine_controller(d),
        "battery": ({k: bat.get(k) for k in ("chemistry", "cells", "v_min", "v_nom", "v_max")}
                    if _val(bat.get("v_min")) and _val(bat.get("v_max")) else None),
        "voltage": {"m": _val(plan.get("m")), "v_dc_grid": _val(plan.get("v_dc")),
                    "basis": "linear SVPWM phase-voltage peak m·V_dc/sqrt3 (the passport's rule); the loss grid's "
                             "operating angles hold at V_dc %s V" % plan.get("v_dc")},
        "psi_map": _psi_map(d),
        "demag": _demag(d, t_mag),
        "limits": {"I_max_A": _val(d["demag_limit"]["I_limit_rms"]),
                   "I_max_basis": "passport demagnetisation current limit at the hot magnet temperature "
                                  f"({t_mag:g} C): {d['demag_limit'].get('how') or d['demag_limit'].get('rule')}",
                   "rpm_max": float(plan["n_max_rpm"]),
                   "rpm_max_basis": f"passport speed limit at v_nom {plan.get('v_dc')} V, m = {plan.get('m')} "
                                    "(voltage, characterised field weakening)",
                   "magnet_C": float(args.magnet_limit),
                   "magnet_basis": args.magnet_basis},
        "thermal_geom": _geom(args, stack),
        "labels": labels,
    }
    return m


def flux_slope(psi_h: float, t_h: float, psi_c: float, t_c: float) -> float:
    """``(psi_h - psi_c) / (psi_h (T_h - T_c))``: the relative flux slope about the
    HOT reference the S1 solver applies it around (``propeller_s1.flux_factor``)."""
    return (psi_h - psi_c) / (psi_h * (t_h - t_c))


def loss_group(p: Dict[str, Any], req, opt) -> Optional[float]:
    """One model loss group at a passport point: None when a required term is absent
    (unavailable - the solver refuses it), optional sub-terms 0 when absent."""
    gw = p.get("groups_W") or {}
    vals = [_val(gw.get(k)) for k in req]
    if any(x is None for x in vals):
        return None
    return float(sum(vals) + sum(_val(gw.get(k)) or 0.0 for k in opt))


def _geom(args, stack: float) -> Dict[str, Any]:
    if getattr(args, "geom_from", None):
        g = dict(_load(args.geom_from)["thermal_geom"])
        g["stackLength_mm"] = float(stack)
        return g
    if not (args.catalog and args.card):
        raise SystemExit("thermal geometry: pass --catalog and --card, or --geom-from <model>")
    return _thermal_geom(args.catalog, args.card, stack)


def from_store(args) -> Dict[str, Any]:
    s = _load(args.source)
    v = _variant(s, args.variant)
    if v is None:
        raise SystemExit("store record has no usable drive variant")
    t = _variant_tables(v)
    r_ref, t_ref, t_coil = float(args.r_ref), float(args.r_temp), float(args.coil_c)
    r_coil = r_ref * (1 + ALPHA_CU * (t_coil - 20)) / (1 + ALPHA_CU * (t_ref - 20))

    def rest(p):
        I = float(p["I_A"])
        x = float(p["P_motor_sine_W"]) - float(p.get("P_mech_W") or 0.0) - 3 * I * I * r_coil
        if x < -0.02 * float(p["P_motor_sine_W"]):
            raise SystemExit(f"negative non-copper loss {x:.2f} W at {p['rpm']} rpm / {I} A: the coil "
                             f"temperature {t_coil} C does not match the record")
        return max(x, 0.0)

    mech = {}
    for p in v["points"].values():
        if isinstance(p, dict) and p.get("P_mech_W") is not None:
            mech[float(p["rpm"])] = float(p["P_mech_W"])
    mech_pts = sorted(mech.items())
    stack = float(args.stack_mm or v.get("build", {}).get("length_mm") or 0)
    lumped = {"status": "lumped", "note": "inside fe_stator: the record's total sine loss is not split"}
    return {
        "schema": SCHEMA,
        "die": s["machine"]["die"], "config": s["machine"]["configuration"],
        "source": {"kind": "passport store record (drive variant points)", "file": args.source_name or Path(args.source).name,
                   "store_variant": v.get("id"), "record_source": s.get("source")},
        "R_phase_ohm": r_ref, "R_temp_C": t_ref,
        "magnet_ref_temp_C": float(args.magnet_c),
        "flux_temp_coeff_per_K": float(args.flux_coeff),
        "flux_coeff_basis": args.flux_basis,
        "torque_factor": 1.0,
        "grid": {"rpm": t["rpm"], "I_A": t["I_A"], "T_Nm": t["table"](lambda p: float(p["T_op_Nm"])),
                 "loss_W": {"fe_stator": t["table"](rest)}},
        "mech_W": ({"rpm": [x[0] for x in mech_pts], "W": [x[1] for x in mech_pts], "basis": "passport store"}
                   if any(x[1] > 0 for x in mech_pts) else None),
        "loss_groups": {"fe_stator": {"status": "grid", "note": "total sine motor loss minus DC copper and mech"},
                        "cu_ac": lumped, "fe_rotor": lumped, "rotor_eddy": lumped,
                        "pwm_extra": {"status": "per drive"},
                        "mech": ({"status": "grid"} if any(x[1] > 0 for x in mech_pts)
                                 else {"status": "omitted", "note": "not in the store record"})},
        "drives": _drives(s), "default_drive": v.get("id"),
        "limits": {"I_max_A": float(t["I_A"][-1]),
                   "I_max_basis": args.imax_basis,
                   "rpm_max": float(t["rpm"][-1]),
                   "rpm_max_basis": "top of the passport store speed grid (no voltage envelope in the record)",
                   "magnet_C": float(args.magnet_limit), "magnet_basis": args.magnet_basis},
        "thermal_geom": _geom(args, stack),
        "labels": [f"torque = operating torque T_op of drive variant {v.get('id')} (store record)",
                   f"losses: total sine motor loss of the record at coil {t_coil:g} C / magnet {args.magnet_c} C; "
                   "DC copper split off with R(T), the rest (iron + AC + eddy, not split in the record) put on the stator",
                   "flux vs magnet temperature from the magnet card's Br coefficient (no cold/hot pair in the record)"],
    }


def _anchor_row(path: str, model: Dict[str, Any]) -> Dict[str, Any]:
    """One coupled FEM anchor (``passport_full.py propanchor``): its speed, the
    losses the thermal map carried and the steady temperatures it reached."""
    sys.path.insert(0, str(ROOT / "src"))
    from motor_ai_sim import propeller as PP
    from motor_ai_sim import propeller_s1 as S1
    a = _load(path)
    ok = [p for p in a["passes"] if not p.get("error") and p.get("components")]
    if not ok:
        raise SystemExit(f"{path}: no finished pass")
    p = ok[-1]
    n, amb = float(a["rpm"]), float(a["ambient_c"])
    prop = PP.get_propeller(a["propeller"])
    op = PP.operating_point(prop, n, PP.air_density(temp_c=amb))
    if a.get("air_override"):
        # a run with another slipstream factor / the bench thrust: the air speed it solved with
        op = {**op, "air_speed_ms": float(a["air_override"]["air_speed_ms"])}
    h = S1.housing_film(op["air_speed_ms"], amb, float(model["thermal_geom"]["statorOD_mm"]) / 1000.0)
    hb = (p.get("cooling") or {}).get("heat_budget") or {}
    em = (p.get("coupling") or {}).get("em") or {}
    comps = p["components"]
    return {"file": Path(path).name, "propeller": a["propeller"],
            "rpm": n, "I_rms": float(p["I_rms"]), "ambient_c": amb,
            "air_speed_ms": op["air_speed_ms"], "h_W_m2K": h,
            "p_cu_W": float(em["P_stranded_W"]),
            "p_rotor_W": float(hb["rotor_heat_split"]["rotor_W"]),
            "p_total_W": float(hb["losses_W"]),
            "torque_miss": p.get("torque_miss"),
            "fem_C": {"winding": float(comps["winding"]["max"]), "magnet": float(comps["magnet"]["max"]),
                      "housing": float(comps["stator"]["avg"]),
                      "winding_avg": float(comps["winding"]["avg"]), "magnet_avg": float(comps["magnet"]["avg"])}}


#: Residual weights of the calibration fit: the two limited parts carry it, the
#: stator (the lumped "housing") only steers the split.
FIT_WEIGHTS = {"winding": 1.0, "magnet": 1.0, "housing": 0.5}


def _resid_after(r: Dict[str, Any], cal: Dict[str, float], temps) -> List[float]:
    """Calibrated network minus FEM at one anchor: winding hot spot and hottest magnet [K]."""
    t = temps(r, cal)
    return [t[k] - r["fem_C"][k] for k in ("winding", "magnet")]


def calibrate(args) -> Dict[str, Any]:
    """Fit the S1 model's thermal calibration to coupled FEM anchors.

    The lumped network is fed the anchors' OWN losses (copper, rotor, total, as
    the thermal map carried them), so only the network is fitted, not the loss
    model.  Unknowns: the housing film factor and the end-turn and rotor
    air-path areas (``propeller_s1.temperatures``), least squares on the winding
    hot spot, the hottest magnet element and the stator mean (weights
    ``FIT_WEIGHTS``), all >= 0."""
    sys.path.insert(0, str(ROOT / "src"))
    import numpy as np
    from scipy.optimize import least_squares
    from motor_ai_sim import propeller_s1 as S1
    m = _load(args.source)
    rows = [_anchor_row(f, m) for f in args.anchor]
    # open frame (Ø40): housing film factor + end-turn and rotor air areas;
    # housed frame (Ø85): no rotor-to-air path, the gap resistance factor instead
    form = getattr(args, "form", None) or "open"
    if form == "housed":
        keys = ("housing_film_factor", "winding_air_area_m2", "gap_resistance_factor")
        scale = (1.0, 1e-3, 1.0)
        x0, lo, hi = [1.0, 0.5, 1.0], [0.05, 0.0, 0.1], [20.0, 100.0, 50.0]
    else:
        keys = ("housing_film_factor", "winding_air_area_m2", "rotor_air_area_m2")
        scale = (1.0, 1e-3, 1e-3)
        x0, lo, hi = [1.0, 2.0, 1.0], [0.05, 0.0, 0.0], [20.0, 100.0, 100.0]

    def temps(r, cal):
        return S1.temperatures(m["thermal_geom"], r["p_cu_W"], r["p_rotor_W"], r["p_total_W"],
                               r["h_W_m2K"], r["ambient_c"], cal)

    def cal_of(x):
        return {k: float(v) * f for k, v, f in zip(keys, x, scale)}

    def resid(x):
        out = []
        for r in rows:
            t = temps(r, cal_of(x))
            out += [FIT_WEIGHTS[k] * (t[k] - r["fem_C"][k]) for k in FIT_WEIGHTS]
        return np.array(out)

    sol = least_squares(resid, x0=x0, bounds=(lo, hi))
    cal = cal_of(sol.x)
    checks = [_anchor_row(f, m) for f in (getattr(args, "check", None) or [])]
    table = []
    for r, role in [(r, "fit") for r in rows] + [(r, "check") for r in checks]:
        b, a_ = temps(r, None), temps(r, cal)
        table.append({**{k: r[k] for k in ("file", "rpm", "I_rms", "air_speed_ms", "h_W_m2K", "p_cu_W",
                                           "p_rotor_W", "p_total_W", "torque_miss")},
                      "role": role, "propeller": r.get("propeller"),
                      "fem_C": r["fem_C"],
                      "lumped_before_C": {k: b[k] for k in ("winding", "magnet", "housing")},
                      "lumped_after_C": {k: a_[k] for k in ("winding", "magnet", "housing")}})
    v_fit = [r["air_speed_ms"] for r in rows]

    def domain(rs):
        if not rs:
            return None
        v, n = [r["air_speed_ms"] for r in rs], [r["rpm"] for r in rs]
        return {"air_speed_ms": [round(min(v), 4), round(max(v), 4)], "rpm": [min(n), max(n)],
                "propellers": sorted({str(r.get("propeller")) for r in rs}), "n": len(rs),
                "max_residual_K": round(max(abs(a) for r in rs for a in _resid_after(r, cal, temps)), 2)}
    m["thermal_calibration"] = {
        **{k: round(v, 6) for k, v in cal.items()},
        # the slipstream speeds the fitted anchors ran at (propeller_s1.calibration_covers)
        "anchor_air_speed_ms": [round(min(v_fit), 4), round(max(v_fit), 4)],
        "anchor_rpm": [r["rpm"] for r in rows],
        # where the network was FITTED and where held-out anchors CHECKED it
        # (propeller_s1.thermal_domain: outside both = thermal extrapolation)
        "fit_domain": domain(rows),
        "validation_domain": domain(checks),
        "form": form,
        "basis": ("fitted %s to %d coupled EM-thermal FEM anchors (propeller as load and cooling, "
                  "air_speed_source propeller, slipstream factor %s, the machine's own frame and "
                  "Simulation settings, %g C air): the lumped network fed each anchor's own losses, "
                  "least squares on the winding hot spot, hottest magnet and stator mean (weights %s)"
                  % (args.date, len(rows), args.slipstream_factor, rows[0]["ambient_c"], FIT_WEIGHTS)),
        "slipstream_factor_basis": args.slipstream_basis,
        "anchors": table,
    }
    bench = _load(args.bench) if getattr(args, "bench", None) else None
    if bench:
        # the slipstream factor fitted to (or transferred from) bench runs: the S1 uses it
        # when the caller names no factor (propeller_s1.model_slipstream)
        tc = m["thermal_calibration"]
        tc["slipstream_factor"] = float(bench["slipstream_factor"])
        tc["slipstream_factor_status"] = str(bench.get("status") or "bench")
        tc["slipstream_factor_basis"] = str(bench["basis"])
        if bench.get("bench") is not None:
            tc["bench"] = bench["bench"]
    if getattr(args, "external_esc", None):
        m["external_esc"] = _load(args.external_esc)
    labels = [x for x in (m.get("labels") or []) if not x.startswith("thermal network calibrated")]
    labels.append("thermal network calibrated to coupled FEM anchors (%s)" % args.date)
    m["labels"] = labels
    return m


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kind", choices=["full", "store", "calibrate"])
    ap.add_argument("source", help="passport record / store record, or (calibrate) the S1 model JSON")
    ap.add_argument("--anchor", action="append", help="calibrate: a propanchor JSON (repeat)")
    ap.add_argument("--form", choices=["open", "housed"], default="open",
                    help="calibrate: open frame (rotor air area) or housed (gap resistance factor)")
    ap.add_argument("--check", action="append",
                    help="calibrate: a propanchor JSON reported against the fit, not fitted (repeat)")
    ap.add_argument("--date", default="2026-10-11")
    ap.add_argument("--slipstream-factor", default="0.4 (behind_hub)")
    ap.add_argument("--slipstream-basis", default="kept at 0.4 (behind_hub): the anchors use the same "
                                                  "slipstream model, so they cannot constrain it")
    ap.add_argument("--bench", help="calibrate: JSON {slipstream_factor, status bench|transferred, basis, bench} "
                                    "- the factor fitted to bench runs (the S1's default air)")
    ap.add_argument("--external-esc", help="calibrate: JSON {name, i_bus_cont_A, i_phase_cont_A, basis, bus_v} "
                                           "- the ESC the configuration ran on the bench")
    ap.add_argument("--store", help="passport store record (drive variants) for the full kind")
    ap.add_argument("--variant", help="drive variant id (default: the first, Configure's default)")
    ap.add_argument("--catalog")
    ap.add_argument("--card")
    ap.add_argument("--geom-from", help="full / store: take thermal_geom from this existing S1 model JSON")
    ap.add_argument("--stack-mm", type=float)
    ap.add_argument("--magnet-limit", type=float)
    ap.add_argument("--magnet-basis", default="magnet card max_working_temp_c (F52SH_120C, SH class 150 C)")
    ap.add_argument("--r-ref", type=float)
    ap.add_argument("--r-temp", type=float)
    ap.add_argument("--coil-c", type=float)
    ap.add_argument("--magnet-c", type=float)
    ap.add_argument("--flux-coeff", type=float)
    ap.add_argument("--flux-basis", default="magnet card alpha(Br) -0.1199 %/K (F52SH_120C)")
    ap.add_argument("--imax-basis", default="top of the passport store current grid (the peak duty current)")
    ap.add_argument("--source-name")
    ap.add_argument("--extra-points", action="append",
                    help="full kind: JSON {points: [...]} of extra loss-grid points (record format)")
    ap.add_argument("--floors", help="full kind: JSON {rpm, I_floor_A, basis} — the field-weakening floor")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    if a.kind == "store" and None in (a.r_ref, a.r_temp, a.coil_c, a.magnet_c, a.flux_coeff):
        ap.error("store needs --r-ref --r-temp --coil-c --magnet-c --flux-coeff")
    if a.kind == "calibrate":
        if not a.anchor:
            ap.error("calibrate needs --anchor (one or more)")
        m = calibrate(a)
        out = Path(a.out) if a.out else Path(a.source)
        out.write_text(json.dumps(m, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        for r in m["thermal_calibration"]["anchors"]:
            print("%-5s %6.0f rpm %5.1f A  FEM W/M/H %s  before %s  after %s" % (
                r.get("role", "fit"), r["rpm"], r["I_rms"],
                [round(r["fem_C"][k], 1) for k in ("winding", "magnet", "housing")],
                [round(r["lumped_before_C"][k], 1) for k in ("winding", "magnet", "housing")],
                [round(r["lumped_after_C"][k], 1) for k in ("winding", "magnet", "housing")]))
        print({k: v for k, v in m["thermal_calibration"].items() if k != "anchors"})
        print(f"wrote {out}")
        return 0
    if not (((a.catalog and a.card) or a.geom_from) and a.magnet_limit is not None):
        ap.error("full / store need --catalog --card (or --geom-from) and --magnet-limit")
    m = from_full(a) if a.kind == "full" else from_store(a)
    out = Path(a.out) if a.out else ROOT / "config" / "propeller_s1" / m["die"] / f"{m['config']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(m, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
