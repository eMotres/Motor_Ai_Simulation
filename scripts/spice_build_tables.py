"""Build a card's SPICE ``switching_table`` (task C) — a grid of double pulses.

    python scripts/spice_build_tables.py IMCQ120R004M2H \
        --v 750.4 --t 25,125,175 --i 20,60,120,185.2,270 \
        --set 2.3,2.3,15,0,18@750.4/800 --set 4.7,4.7,10,0,18 --set 4.7,4.7,20,0,18 --write

``--set R_G,on,R_G,off,L_sigma_nH,V_GS(off),V_GS(on)[@V1/V2...]`` (EXTERNAL
gate resistances) — one driver/layout set per flag; the optional ``@`` list
replaces ``--v`` for that set.  Every grid point is one double pulse
(datasheet Fig. F), run sequentially at BelowNormal priority; finished
points are re-used (keyed on the netlist text), so an interrupted build
resumes and a point shared with the validation is not simulated twice.

``--write`` appends/replaces the generated block in
``config/devices/<PART>.yaml`` between marker comments — the hand-written
card is not touched and ``switching_source`` is NOT changed (the default
stays ``datasheet``).  Low-voltage parts (V_DSS < 200 V) get the finer
timing of :func:`motor_ai_sim.inverter.spice.netlist.timing_for`.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from motor_ai_sim.inverter.spice.harness import run_double_pulse  # noqa: E402
from motor_ai_sim.inverter.spice.models import model_for  # noqa: E402
from motor_ai_sim.inverter.spice.netlist import DoublePulse, timing_for  # noqa: E402
from motor_ai_sim.inverter.spice.runner import find_backend  # noqa: E402
from motor_ai_sim.inverter.spice.table import make_block, make_set, write_block  # noqa: E402


def floats(s: str, sep: str = ","):
    return [float(x) for x in s.split(sep) if x.strip()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("part")
    ap.add_argument("--v", required=True)
    ap.add_argument("--t", required=True)
    ap.add_argument("--i", required=True)
    ap.add_argument("--set", action="append", required=True)
    ap.add_argument("--l-gate-nH", type=float, default=None)
    ap.add_argument("--c-sigma-pF", type=float, default=None)
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    m = model_for(a.part)
    doc = yaml.safe_load((ROOT / "config" / "devices" / f"{a.part}.yaml").read_text(encoding="utf-8"))
    tim = timing_for(float((doc.get("ratings") or {}).get("v_dss_V") or 1200))
    if a.l_gate_nH is not None:
        tim["l_gate"] = a.l_gate_nH * 1e-9
    if a.c_sigma_pF is not None:
        tim["c_sigma"] = a.c_sigma_pF * 1e-12
    be = find_backend()
    sets = []
    t_all = time.time()
    v_dss = float((doc.get("ratings") or {}).get("v_dss_V") or 1200)
    for spec in a.set:
        head, _, vs = spec.partition("@")
        rg_on, rg_off, ls, voff, von = floats(head)
        tim = {**tim, **{k: v for k, v in timing_for(v_dss, max(rg_on, rg_off)).items()
                         if k in ("t_gap", "t_second")}}
        volts = floats(vs, "/") if vs else floats(a.v)
        runs = []
        for v in volts:
            for t in floats(a.t):
                for i in floats(a.i):
                    dp = DoublePulse(v_dd=v, i_target=i, t_j=t, rg_on=rg_on,
                                     rg_off=rg_off, v_gs_on=von, v_gs_off=voff,
                                     l_sigma=ls * 1e-9, **tim)
                    t0 = time.time()
                    # no re-timing: every row carries the current actually
                    # switched and the table interpolates on it, so a grid
                    # point a few % off its nominal current biases nothing
                    r = run_double_pulse(m, dp, backend=be, correct_current=False)
                    mm = r["metrics"]
                    print(f"{a.part} set {spec} V={v:g} T={t:g} I={i:g}: "
                          f"I_off {mm['i_off_A']:.1f} E_off {(mm['off']['e_J'] or 0) * 1e6:.1f} uJ | "
                          f"I_on {mm['i_on_A']:.1f} E_on {(mm['on']['e_J'] or 0) * 1e6:.1f} uJ "
                          f"E_fr {(mm['fr']['e_fr_J'] or 0) * 1e6:.1f} uJ  ({time.time() - t0:.0f} s)",
                          flush=True)
                    runs.append(r)
        sets.append(make_set(v_gs_on=von, v_gs_off=voff, r_g_on=rg_on, r_g_off=rg_off,
                             l_sigma_nH=ls, l_gate_nH=tim["l_gate"] * 1e9,
                             c_sigma_pF=tim["c_sigma"] * 1e12, runs=runs))
    block = make_block(model=m, sets=sets,
                       simulator=f"{be.describe()}, ngbehavior={m.compat}"
                                 + (", DDT() translated to an implicit capacitor sense"
                                    if m.include_path != m.lib_path else ""))
    print(f"total {time.time() - t_all:.0f} s", flush=True)
    if a.write:
        write_block(ROOT / "config" / "devices" / f"{a.part}.yaml", block)
        print("written to the card")
    else:
        print(yaml.safe_dump({"switching_table": block}, sort_keys=False)[:3000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
