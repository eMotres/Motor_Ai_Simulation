"""Build the uniform SPICE tables for EVERY device whose vendor model runs in
ngspice (owner 2026-09-27: «все моторы должны работать на SPICE одинаково»).

    python scripts/spice_build_all_tables.py [--jobs 8] [--parts A,B] [--write] [--static-only]

Per part, one grid, the same rules for every part:

* switching (double pulse, datasheet Fig. F): V_dc x T_j x I x driver set.
  CoolSiC 1200 V: V_dc 375 / 750.4 V (800 V and above: the power law through these two, flagged), T_j 25 / 125 / 175 degC,
  I = 0.12 / 0.5 / 1.0 / 1.5 x the datasheet test current (IMCQ120R004M2H:
  the 2026-09-23 currents 20 / 60 / 120 / 185.2 / 270 A), sets
  R_G,ext = the datasheet's 2.3 ohm and 10 ohm (both edges), L_sigma 15 nH
  (the datasheet's), V_GS 0/18 V.  OptiMOS IQE050N08NM5SC: V_dc 22.2 / 44.4 V,
  T_j 25 / 75 / 125 / 175, I 5 / 12 / 20 / 31 / 44 / 60 A, R_G 1.6 / 4.7 ohm,
  L_sigma 2 nH, V_GS 0/10 V.  Earlier sets already simulated (R004 4.7 ohm
  10/20 nH, IQE 5 nH) are re-read from the run cache and kept.
* static (DC sweeps): V_DS(I) forward at V_GS(on) and V_SD(I) third quadrant
  at V_GS(off), T_j 25 / 75 / 125 / 150 / 175, both the Kelvin and the power
  source-pin voltage.

Runs go through :func:`harness.run_double_pulse` (one ngspice child per run,
BELOW-NORMAL priority), at most ``--jobs`` at once (default 8).  Finished runs
are reused (the netlist is the key).  ``--write`` puts ``switching_table`` and
``static_table`` into each card between the generator's marker comments and
sets ``switching_source: spice``.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import sys
import time
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from motor_ai_sim.inverter.spice.harness import run_double_pulse, run_static  # noqa: E402
from motor_ai_sim.inverter.spice.models import model_for  # noqa: E402
from motor_ai_sim.inverter.spice.netlist import DoublePulse, timing_for  # noqa: E402
from motor_ai_sim.inverter.spice.runner import find_backend  # noqa: E402
from motor_ai_sim.inverter.spice.table import (make_block, make_set,  # noqa: E402
                                               make_static_block, write_block)

SIC = ["IMCQ120R004M2H", "IMCQ120R005M2H", "IMCQ120R007M2H", "IMCQ120R010M2H",
       "IMCQ120R017M2H", "IMCQ120R034M2H", "IMCQ120R078M2H"]
LV = ["IQE050N08NM5SC"]
STATIC_T = [25.0, 75.0, 125.0, 150.0, 175.0]


def grid(part: str, doc: dict) -> dict:
    sw = doc.get("switching") or {}
    i_ref = float(sw.get("i_d_ref_A") or 20.0)
    if part in LV:
        return {"v": [22.2, 44.4], "t": [25.0, 75.0, 125.0, 175.0],
                "i": [5.0, 12.0, 20.0, 31.0, 44.0, 60.0],
                "sets": [(1.6, 1.6, 2.0, 0.0, 10.0), (4.7, 4.7, 2.0, 0.0, 10.0)],
                "extra": [((1.6, 1.6, 5.0, 0.0, 10.0), [22.2], [25.0, 75.0],
                           [5.0, 12.0, 20.0, 31.0, 44.0])],
                "v_gs_on": 10.0, "v_gs_off": [0.0], "i_static": 80.0}
    if part == "IMCQ120R004M2H":
        cur = [20.0, 60.0, 120.0, 185.2, 270.0]
        extra = [((4.7, 4.7, 10.0, 0.0, 18.0), [750.4], [25.0, 125.0, 175.0], cur),
                 ((4.7, 4.7, 20.0, 0.0, 18.0), [750.4], [25.0, 125.0, 175.0], cur)]
    else:
        cur = [round(i_ref * k, 2) for k in (0.12, 0.5, 1.0, 1.5)]
        extra = []
    return {"v": [375.0, 750.4], "t": [25.0, 125.0, 175.0], "i": cur,
            "sets": [(2.3, 2.3, 15.0, 0.0, 18.0), (10.0, 10.0, 15.0, 0.0, 18.0)],
            "extra": extra, "v_gs_on": 18.0, "v_gs_off": [0.0, -5.0],
            "i_static": 1.6 * i_ref}


def jobs_for(part: str, doc: dict):
    g = grid(part, doc)
    v_dss = float((doc.get("ratings") or {}).get("v_dss_V") or 1200)
    out = []
    specs = [(s, g["v"], g["t"], g["i"]) for s in g["sets"]] + list(g["extra"])
    for (rg_on, rg_off, ls, voff, von), vs, ts, cs in specs:
        tim = timing_for(v_dss, max(rg_on, rg_off))
        for v in vs:
            for t in ts:
                for i in cs:
                    out.append(((rg_on, rg_off, ls, voff, von),
                                DoublePulse(v_dd=v, i_target=i, t_j=t, rg_on=rg_on,
                                            rg_off=rg_off, v_gs_on=von, v_gs_off=voff,
                                            l_sigma=ls * 1e-9, **tim)))
    return out, g


def run_one(part: str, dp: DoublePulse, be):
    m = model_for(part)
    t0 = time.time()
    try:
        r = run_double_pulse(m, dp, backend=be, correct_current=False)
        return r, None, time.time() - t0
    except Exception as exc:  # noqa: BLE001 — a failed point is logged, not fatal
        return None, f"{type(exc).__name__}: {str(exc)[:300]}", time.time() - t0


def static_rows(part: str, g: dict, be) -> dict:
    m = model_for(part)
    rows_f, rows_r, notes = [], [], []
    i_max = g["i_static"]
    for t in STATIC_T:
        r = run_static(m, kind="rds", t_j=t, v_gs=g["v_gs_on"], i_max=i_max,
                       i_step=i_max / 16, backend=be, v_max=2.0 if part in LV else 5.0)
        i = np.asarray(r["i_A"], float); o = np.argsort(i)
        for x in np.linspace(0, i_max, 17)[1:]:
            rows_f.append([g["v_gs_on"], t, round(float(x), 3),
                           round(float(np.interp(x, i[o], np.asarray(r["v_kelvin_V"])[o])), 6),
                           round(float(np.interp(x, i[o], np.asarray(r["v_pin_V"])[o])), 6)])
        for voff in g["v_gs_off"]:
            r = run_static(m, kind="vsd", t_j=t, v_gs=voff, i_max=i_max,
                           i_step=i_max / 16, backend=be, v_max=6.0 if part not in LV else 1.5)
            i = np.asarray(r["i_A"], float); o = np.argsort(i)
            if float(i[o][-1]) < 0.95 * i_max:
                notes.append(f"vsd T{t:g} V_GS {voff:g}: sweep reached {float(i[o][-1]):.1f} A")
            for x in np.linspace(0, i_max, 17)[1:]:
                rows_r.append([voff, t, round(float(x), 3),
                               round(float(np.interp(x, i[o], np.asarray(r["v_kelvin_V"])[o])), 6),
                               round(float(np.interp(x, i[o], np.asarray(r["v_pin_V"])[o])), 6)])
    return {"forward": rows_f, "third_quadrant": rows_r, "notes": notes}


def validation_summary(part: str) -> dict:
    """The model-vs-datasheet verdict of ``spice_validate_devices.py``
    (``_runs/validation.json``) as a small card field: the worst signed
    deviation over E_on / E_off / E_tot (E_tot = E_on + E_off + E_fr, Table 4
    footnote 1) at the datasheet's own test points; > 15 % = "deviates"."""
    p = ROOT / "config" / "devices" / "spice" / "_runs" / "validation.json"
    try:
        v = json.loads(p.read_text(encoding="utf-8")).get(part) or {}
    except (OSError, ValueError):
        return {"status": "not_validated", "line": "SPICE model not validated against the datasheet"}
    rows, worst = [], None
    for key, dyn in v.items():
        if not key.startswith("dynamic_vgs") or not isinstance(dyn, dict):
            continue
        voff = key.replace("dynamic_vgs", "")
        for t, r in dyn.items():
            ds, sp = r.get("datasheet_uJ") or {}, r.get("spice_uJ") or {}
            if not ds or ds.get("e_on") is None:
                continue
            ds_tot = sum(float(ds.get(k) or 0) for k in ("e_on", "e_off", "e_fr"))
            sp_tot = sum(float(sp.get(k) or 0) for k in ("e_on", "e_off", "e_fr"))
            for nm, a, b in (("E_on", sp.get("e_on"), ds.get("e_on")),
                             ("E_off", sp.get("e_off"), ds.get("e_off")),
                             ("E_tot", sp_tot, ds_tot)):
                if a is None or not b:
                    continue
                d = 100.0 * (float(a) / float(b) - 1.0)
                row = {"energy": nm, "t_j_c": float(t), "v_gs_off_V": float(voff),
                       "dev_pct": round(d, 1)}
                rows.append(row)
                if worst is None or abs(d) > abs(worst["dev_pct"]):
                    worst = row
    if worst is None:
        return {"status": "unvalidated", "limit_pct": 15.0, "deviates": True,
                "line": ("SPICE switching energies unvalidated — the datasheet "
                         "publishes no E_on/E_off to check the model against"),
                "rows": []}
    dev = abs(worst["dev_pct"]) > 15.0
    return {"status": "deviates" if dev else "within_15pct", "limit_pct": 15.0,
            "deviates": dev, "worst_pct": worst["dev_pct"],
            "worst": (f"{worst['energy']} {worst['dev_pct']:+.0f} % at {worst['t_j_c']:g} degC, "
                      f"V_GS(off) {worst['v_gs_off_V']:g} V"),
            "line": (f"model deviates from datasheet by {worst['dev_pct']:+.0f} % "
                     f"({worst['energy']}, {worst['t_j_c']:g} degC)" if dev else
                     f"model within {abs(worst['dev_pct']):.0f} % of the datasheet energies"),
            "rows": rows,
            "source": "scripts/spice_validate_devices.py -> _runs/validation.json (2026-09-23)"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--parts", default=",".join(LV + SIC))
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--static-only", action="store_true")
    a = ap.parse_args()
    be = find_backend()
    parts = [p for p in a.parts.split(",") if p]
    docs = {p: yaml.safe_load((ROOT / "config" / "devices" / f"{p}.yaml").read_text(encoding="utf-8"))
            for p in parts}
    plan = {p: jobs_for(p, docs[p]) for p in parts}
    results = {p: [] for p in parts}
    failed = {p: [] for p in parts}
    t_all = time.time()
    if not a.static_only:
        allj = [(p, s, dp) for p in parts for s, dp in plan[p][0]]
        print(f"{len(allj)} double pulses, {a.jobs} at once", flush=True)
        with cf.ThreadPoolExecutor(max_workers=a.jobs) as ex:
            futs = {ex.submit(run_one, p, dp, be): (p, s, dp) for p, s, dp in allj}
            done = 0
            for f in cf.as_completed(futs):
                p, s, dp = futs[f]
                r, err, dt = f.result()
                done += 1
                if r is None:
                    failed[p].append({"set": s, "v": dp.v_dd, "t": dp.t_j, "i": dp.i_target, "error": err})
                    print(f"[{done}/{len(allj)}] {p} {s} V{dp.v_dd:g} T{dp.t_j:g} I{dp.i_target:g} FAILED {err[:120]}", flush=True)
                    continue
                results[p].append((s, r))
                mm = r["metrics"]
                print(f"[{done}/{len(allj)}] {p} {s} V{dp.v_dd:g} T{dp.t_j:g} I{dp.i_target:g}: "
                      f"Ioff {mm['i_off_A']:.1f} Eoff {(mm['off']['e_J'] or 0)*1e6:.1f} "
                      f"Eon {(mm['on']['e_J'] or 0)*1e6:.1f} Efr {((mm.get('fr') or {}).get('e_fr_J') or 0)*1e6:.1f} uJ "
                      f"({dt:.0f} s, {time.time()-t_all:.0f} s total)", flush=True)
    for p in parts:
        m = model_for(p)
        g = plan[p][1]
        tim = timing_for(float((docs[p].get("ratings") or {}).get("v_dss_V") or 1200))
        out_dir = ROOT / "config" / "devices" / "spice" / "_runs" / p
        st = static_rows(p, g, be)
        sets = []
        for s in sorted({s for s, _ in results[p]}, key=lambda s: (s[2], s[0])):
            rg_on, rg_off, ls, voff, von = s
            sets.append(make_set(v_gs_on=von, v_gs_off=voff, r_g_on=rg_on, r_g_off=rg_off,
                                 l_sigma_nH=ls, l_gate_nH=tim["l_gate"] * 1e9,
                                 c_sigma_pF=tim["c_sigma"] * 1e12,
                                 runs=[r for ss, r in results[p] if ss == s]))
        sim = (f"{be.describe()}, ngbehavior={m.compat}"
               + (", DDT() translated to an implicit capacitor sense"
                  if m.include_path != m.lib_path else ""))
        (out_dir / "uniform_2026-09-27.json").write_text(json.dumps(
            {"sets": sets, "failed": failed[p], "static": st}, indent=1, default=float),
            encoding="utf-8")
        print(f"{p}: {sum(len(s['rows']) for s in sets)} rows, {len(failed[p])} failed, "
              f"static {len(st['forward'])}+{len(st['third_quadrant'])} rows", flush=True)
        if a.write and sets:
            block = make_block(model=m, sets=sets, simulator=sim,
                               note=("THE switching basis of this device (owner 2026-09-27: "
                                     "every motor on SPICE, uniformly); the datasheet path is "
                                     "the labelled fallback only"))
            block["failed_points"] = len(failed[p])
            block["l_sigma_default_nH"] = 2.0 if p in LV else 15.0
            block["validation"] = validation_summary(p)
            sblock = make_static_block(model=m, simulator=sim, static=st)
            write_block(ROOT / "config" / "devices" / f"{p}.yaml", block,
                        switching_source="spice", static_block=sblock)
            print(f"{p}: written to the card", flush=True)
    print(f"total {time.time() - t_all:.0f} s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
