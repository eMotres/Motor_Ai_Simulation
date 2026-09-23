"""Validate each vendor SPICE model against ITS OWN datasheet (task D).

    python scripts/spice_validate_devices.py [PART ...] [--dynamic] [--vgs-off -5]
                                             [--temps 25,175] [--report]

Static (always): R_DS(on) at every TABLE point of the card (T_j, V_GS(on)) at
the datasheet's I_D, both Kelvin (drain to source-sense, the datasheet's
convention on the Q-DPAK) and at the power source pin; V_SD at the card's
table current, V_GS = 0 V; C_iss/C_oss/C_rss at the datasheet's V_DS.
Dynamic (``--dynamic``): one double pulse per T_j at the datasheet's test
conditions (V_DD, I_D, R_G,ext, V_GS, L_sigma from the card): E_on, E_off,
E_fr, Q_fr, I_frm, t_d/t_r/t_f — extracted on the datasheet's own windows.

``--report`` prints the markdown validation table from validation.json
(no simulation).  Every datasheet number is the card's TABLE anchor.
Results go to ``config/devices/spice/_runs/validation.json`` (git-ignored);
the netlist of each datasheet-condition double pulse is also copied next to
the part's manifest (``double_pulse_datasheet_T<T>.cir``, committed — it is
our circuit, the vendor library is only ``.include``d).  ngspice runs one at
a time at BelowNormal priority.

The judgement (owner 2026-09-23): a switching energy more than 15 % off its
datasheet value marks the model "not trusted" for that quantity, with the
reason.  A vendor model is NEVER tuned here.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from motor_ai_sim.inverter.spice.harness import (run_capacitances, run_double_pulse,  # noqa: E402
                                                 run_static, value_at)
from motor_ai_sim.inverter.spice.models import SpiceModelError, model_for, spice_dir  # noqa: E402
from motor_ai_sim.inverter.spice.netlist import DoublePulse, timing_for  # noqa: E402

PARTS = ["IMCQ120R004M2H", "IMCQ120R005M2H", "IMCQ120R007M2H", "IMCQ120R010M2H",
         "IMCQ120R017M2H", "IMCQ120R034M2H", "IMCQ120R078M2H",
         "IMDQ75R004M2H", "IMDQ75R007M2H", "AIMDQ75R016M2H", "IQE050N08NM5SC"]

TRUST_PCT = 15.0

#: Low-voltage parts: the datasheet names no L_sigma for its switching-time
#: circuit; 2 nH is a stated ASSUMPTION for a PQFN test board (the finer
#: timing is netlist.timing_for's).
LV_L_SIGMA = 2e-9


def card(part):
    return yaml.safe_load((ROOT / "config" / "devices" / f"{part}.yaml").read_text(encoding="utf-8"))


def ds_anchor(doc, name, t_j, v_gs_off):
    for s in (doc.get("switching") or {}).get("curves") or []:
        if float(s.get("v_gs_off_V", 0)) == float(v_gs_off) and float(s.get("t_j_c")) == float(t_j):
            b = s.get(name) or {}
            ai = b.get("anchor_i_d_A")
            for i, e in b.get("points") or []:
                if ai is not None and abs(float(i) - float(ai)) < 1e-6:
                    return float(e)
    return None


def rds_table_points(doc):
    """[(v_gs_on, t_j, r_mohm)] — the card's TABLE points only."""
    out = []
    for c in (doc.get("r_ds_on") or {}).get("curves") or []:
        for p in c.get("points") or []:
            if p.get("basis") == "table":
                out.append((float(c["v_gs_on_V"]), float(p["t_j_c"]), float(p["r_mohm"])))
    return out


def vsd_table(doc):
    """(I_SD, [(t_j, V)]) from the card's ``v_sd_at_<I>A`` table block."""
    tq = doc.get("third_quadrant") or {}
    for k, v in tq.items():
        m = re.match(r"v_sd_at_([0-9.]+)A$", k)
        if m and isinstance(v, list):
            return float(m.group(1)), [(float(p["t_j_c"]), float(p["v"])) for p in v
                                       if p.get("basis", "table") == "table"]
    return None, []


def times_ds(doc, t_j):
    tn = (doc.get("switching") or {}).get("times_ns") or {}
    out = {}
    for k in ("t_d_on", "t_r", "t_d_off", "t_f"):
        v = (tn.get(k) or {}).get(f"t_j_{t_j:g}")
        out[k] = float(v) if v is not None else None
    return out


def recovery_ds(doc, t_j):
    tq = doc.get("third_quadrant") or {}
    q = (tq.get("q_fr_uC") or {}).get(f"t_j_{t_j:g}")
    i = (tq.get("i_frm_A") or {}).get(f"t_j_{t_j:g}")
    rr = tq.get("reverse_recovery") or {}
    if q is None and rr and float(t_j) == 25.0:
        q = rr.get("q_rr_nC_typ")
        q = None if q is None else float(q) / 1000.0
    return (None if q is None else float(q)), (None if i is None else float(i))


def pct(a, b):
    return None if (a is None or b in (None, 0)) else 100.0 * (a - b) / b


def dp_for(doc, m, t, v_gs_off):
    sw = doc.get("switching") or {}
    v_on = float(sw.get("v_gs_ref_V") or 18.0)
    v_dss = float((doc.get("ratings") or {}).get("v_dss_V") or 1200)
    extra = timing_for(v_dss)
    ls = (doc.get("gate") or {}).get("l_sigma_nH")
    extra["l_sigma"] = (float(ls) * 1e-9 if ls is not None
                        else (LV_L_SIGMA if v_dss < 200 else 15e-9))
    rg = float(sw.get("r_g_ext_ref_ohm"))
    return DoublePulse(v_dd=float(sw.get("v_dd_ref_V")), i_target=float(sw.get("i_d_ref_A")),
                       t_j=t, rg_on=rg, rg_off=rg, v_gs_on=v_on, v_gs_off=v_gs_off, **extra)


def copy_reference_cir(part, cir_path, t):
    """The datasheet double pulse, next to the manifest, with the include made
    relative (so the file works from a fresh checkout that has the vendor lib)."""
    src = Path(cir_path)
    txt = src.read_text(encoding="utf-8")
    sd = spice_dir().as_posix()
    txt = txt.replace(f'"{sd}/', '"../')
    dst = spice_dir() / part / f"double_pulse_datasheet_T{t:g}.cir"
    dst.write_text(txt, encoding="utf-8")
    return dst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("parts", nargs="*", default=PARTS)
    ap.add_argument("--dynamic", action="store_true")
    ap.add_argument("--no-static", action="store_true")
    ap.add_argument("--vgs-off", type=float, default=0.0)
    ap.add_argument("--temps", default=None, help="default: the card's switching curve temperatures")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    out_p = spice_dir() / "_runs" / "validation.json"
    out_p.parent.mkdir(parents=True, exist_ok=True)
    res = json.loads(out_p.read_text(encoding="utf-8")) if out_p.is_file() else {}
    if a.report:
        print(report(res))
        return 0
    for part in a.parts:
        doc = card(part)
        row = res.setdefault(part, {})
        try:
            m = model_for(part)
        except SpiceModelError as exc:
            row["status"] = str(exc)
            print(part, "->", exc)
            out_p.write_text(json.dumps(res, indent=1, default=float), encoding="utf-8")
            continue
        row["status"] = "usable_ngspice"
        row["basis"] = m.basis
        i_ref = float((doc.get("r_ds_on") or {}).get("measured_at_i_d_A"))
        if not a.no_static:
            st = row["static"] = {}
            for v_gs, t, r_ds in rds_table_points(doc):
                r = run_static(m, kind="rds", t_j=t, v_gs=v_gs, i_max=i_ref, i_step=i_ref / 4,
                               v_max=max(0.2, 4.0 * i_ref * r_ds * 1e-3))
                st[f"rds_T{t:g}_Vgs{v_gs:g}"] = {
                    "datasheet_mohm": r_ds,
                    "spice_kelvin_mohm": value_at(r, i_ref, "v_kelvin_V") / i_ref * 1e3,
                    "spice_pin_mohm": value_at(r, i_ref, "v_pin_V") / i_ref * 1e3,
                    "i_A": i_ref, "v_gs": v_gs, "t_j": t, "cir": r["cir"], "drive": r["drive"]}
            i_sd, vsd = vsd_table(doc)
            for t, v_ds in vsd:
                r = run_static(m, kind="vsd", t_j=t, v_gs=0.0, i_max=i_sd, i_step=i_sd / 4,
                               v_max=max(1.5, 1.8 * v_ds))
                st[f"vsd_T{t:g}"] = {"datasheet_V": v_ds, "t_j": t,
                                     "spice_kelvin_V": value_at(r, i_sd, "v_kelvin_V"),
                                     "spice_pin_V": value_at(r, i_sd, "v_pin_V"), "i_A": i_sd,
                                     "cir": r["cir"], "drive": r["drive"]}
            cap = doc.get("capacitance") or {}
            if cap.get("at_v_ds_V"):
                lv = float((doc.get("ratings") or {}).get("v_dss_V") or 1200) < 200
                try:
                    c = run_capacitances(m, v_ds=float(cap["at_v_ds_V"]),
                                         f_hz=1e6 if lv else 100e3)
                    st["capacitance"] = {**c, "datasheet_pF": {k: cap.get(k) for k in
                                                               ("c_iss_pF", "c_oss_pF", "c_rss_pF")}}
                except Exception as exc:  # an AC operating point that does not converge is reported
                    st["capacitance"] = {"error": str(exc)[-400:]}
            out_p.write_text(json.dumps(res, indent=1, default=float), encoding="utf-8")
            print(part, "static", json.dumps(st, default=float)[:400], flush=True)
        if not a.dynamic:
            continue
        temps = ([float(x) for x in a.temps.split(",")] if a.temps else
                 sorted({float(s["t_j_c"]) for s in (doc.get("switching") or {}).get("curves") or []
                         if float(s.get("v_gs_off_V", 0)) == a.vgs_off}) or [25.0])
        dyn = row.setdefault(f"dynamic_vgs{a.vgs_off:g}", {})
        for t in temps:
            dp = dp_for(doc, m, t, a.vgs_off)
            r = run_double_pulse(m, dp)
            mm = r["metrics"]
            ref = copy_reference_cir(part, r["cir"], t) if a.vgs_off == 0 else None
            q_ds, i_frm_ds = recovery_ds(doc, t)
            sp = {"e_on": (mm["on"]["e_J"] or 0) * 1e6, "e_off": (mm["off"]["e_J"] or 0) * 1e6,
                  "e_fr": (mm["fr"]["e_fr_J"] or 0) * 1e6}
            ds = {"e_on": ds_anchor(doc, "e_on_uJ", t, a.vgs_off),
                  "e_off": ds_anchor(doc, "e_off_uJ", t, a.vgs_off),
                  "e_fr": ds_anchor(doc, "e_fr_uJ", t, a.vgs_off)}
            dyn[f"{t:g}"] = {
                "cir": r["cir"], "reference_cir": str(ref) if ref else None,
                "elapsed_s": r["elapsed_s"], "conditions": r["conditions"],
                "i_off_A": mm["i_off_A"], "i_on_A": mm["i_on_A"],
                "spice_uJ": sp, "datasheet_uJ": ds,
                "q_fr_uC": (mm["fr"]["q_fr_C"] or 0) * 1e6, "q_fr_uC_datasheet": q_ds,
                "i_frm_A": mm["fr"]["i_frm_A"], "i_frm_A_datasheet": i_frm_ds,
                "t_fr_ns": (mm["fr"]["t_fr_s"] or 0) * 1e9,
                "t_ns": {k: (v * 1e9 if v is not None else None) for k, v in
                         (("t_d_on", mm["on"]["t_d_on_s"]), ("t_r", mm["on"]["t_r_s"]),
                          ("t_d_off", mm["off"]["t_d_off_s"]), ("t_f", mm["off"]["t_f_s"]))},
                "t_ns_datasheet": times_ds(doc, t),
                "dv_dt_on_V_ns": (mm["on"]["dv_dt_V_per_s"] or 0) * 1e-9,
                "dv_dt_off_V_ns": (mm["off"]["dv_dt_V_per_s"] or 0) * 1e-9,
                "di_dt_on_A_ns": (mm["on"]["di_dt_A_per_s"] or 0) * 1e-9,
                "di_dt_off_A_ns": (mm["off"]["di_dt_A_per_s"] or 0) * 1e-9,
                "v_peak_off_V": mm["off"]["v_peak_V"], "i_peak_on_A": mm["on"]["i_peak_A"]}
            out_p.write_text(json.dumps(res, indent=1, default=float), encoding="utf-8")
            print(part, t, json.dumps(sp), json.dumps(ds), f"{r['elapsed_s']:.0f}s", flush=True)
    return 0


def _f(x, nd=0):
    return "—" if x is None else f"{x:.{nd}f}"


def _d(x):
    return "—" if x is None else f"{x:+.0f} %"


def verdict(sp, ds):
    """(trusted?, reason) on E_on, E_off and E_tot (= E_on + E_off + E_fr,
    the datasheet's own sum, footnote 1 of Table 4)."""
    bad = []
    for k in ("e_on", "e_off"):
        d = pct(sp.get(k), ds.get(k))
        if d is not None and abs(d) > TRUST_PCT:
            bad.append(f"{k.replace('e_', 'E_')} {d:+.0f} %")
    if all(ds.get(k) is not None for k in ("e_on", "e_off", "e_fr")):
        d = pct(sum(sp[k] for k in ("e_on", "e_off", "e_fr")),
                sum(ds[k] for k in ("e_on", "e_off", "e_fr")))
        if d is not None and abs(d) > TRUST_PCT:
            bad.append(f"E_tot {d:+.0f} %")
    if all(ds.get(k) is None for k in ("e_on", "e_off")):
        return None, "datasheet publishes no switching energy — energies unvalidated"
    return (not bad), ("; ".join(bad) if bad else f"E_on, E_off, E_tot within ±{TRUST_PCT:g} %")


def report(res) -> str:
    L = ["| part | R_DS(on) 25 °C ds / SPICE (Kelvin) mΩ | R_DS(on) 175 °C ds / SPICE | "
         "V_SD 25 °C ds / SPICE V | C_oss ds / SPICE pF |",
         "|---|---|---|---|---|"]
    for part, row in res.items():
        st = row.get("static") or {}
        if not st:
            L.append(f"| {part} | {row.get('status', '—')[:90]} | | | |")
            continue
        def rd(t):
            for k, v in st.items():
                if k.startswith(f"rds_T{t}_") and v.get("v_gs") in (18.0, 10.0):
                    return v
            return None
        r25, r175 = rd(25), rd(175)
        v25 = st.get("vsd_T25")
        c = st.get("capacitance") or {}
        cd = (c.get("datasheet_pF") or {})
        L.append(f"| {part} | {_f(r25 and r25['datasheet_mohm'], 2)} / {_f(r25 and r25['spice_kelvin_mohm'], 2)} "
                 f"({_d(pct(r25 and r25['spice_kelvin_mohm'], r25 and r25['datasheet_mohm']))}) | "
                 f"{_f(r175 and r175['datasheet_mohm'], 2)} / {_f(r175 and r175['spice_kelvin_mohm'], 2)} "
                 f"({_d(pct(r175 and r175['spice_kelvin_mohm'], r175 and r175['datasheet_mohm']))}) | "
                 f"{_f(v25 and v25['datasheet_V'], 2)} / {_f(v25 and v25['spice_kelvin_V'], 2)} | "
                 f"{_f(cd.get('c_oss_pF'))} / {_f(c.get('c_oss_pF'))} |")
    L += ["", "| part | T_j | E_on ds / SPICE µJ | E_off ds / SPICE | E_fr ds / SPICE | "
          "Q_fr ds / SPICE µC | t_r / t_f ds vs SPICE ns | verdict |",
          "|---|---|---|---|---|---|---|---|"]
    for part, row in res.items():
        for key, dyn in row.items():
            if not key.startswith("dynamic_vgs"):
                continue
            for t, d in dyn.items():
                sp, ds = d["spice_uJ"], d["datasheet_uJ"]
                ok, why = verdict(sp, ds)
                tn, td = d["t_ns"], d["t_ns_datasheet"]
                L.append(
                    f"| {part} ({key[11:]} V off) | {t} °C | {_f(ds['e_on'])} / {_f(sp['e_on'])} ({_d(pct(sp['e_on'], ds['e_on']))}) | "
                    f"{_f(ds['e_off'])} / {_f(sp['e_off'])} ({_d(pct(sp['e_off'], ds['e_off']))}) | "
                    f"{_f(ds['e_fr'])} / {_f(sp['e_fr'])} ({_d(pct(sp['e_fr'], ds['e_fr']))}) | "
                    f"{_f(d.get('q_fr_uC_datasheet'), 2)} / {_f(d['q_fr_uC'], 2)} | "
                    f"{_f(td.get('t_r'), 1)}/{_f(td.get('t_f'), 1)} vs {_f(tn.get('t_r'), 1)}/{_f(tn.get('t_f'), 1)} | "
                    f"{'trusted' if ok else ('—' if ok is None else '**not trusted**')}: {why} |")
    return "\n".join(L)


if __name__ == "__main__":
    sys.exit(main())

