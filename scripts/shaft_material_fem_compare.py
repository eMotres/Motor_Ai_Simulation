# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Shaft material A/B on the passport's own solver jobs: what does another SHAFT STEEL do to a motor?

Owner 2026-10-15: the small motors get a stainless AISI 420 shaft - FERROMAGNETIC, unlike the aluminium 6061 shaft the
product default has - so the change is a PHYSICS change.  This script measures it before anything is applied: for one die /
configuration and a list of shaft materials it runs, on the SAME machine, the passport's solver-direct jobs
(``passport_v1.jobs``: one ``em_transient_eval`` call each, the canonical sliding-band invocation the Simulation route and
the optimizer use) and writes the deltas against a reference shaft:

  * ``nl20`` - no-load at 20 C (magnets and winding): PM flux linkage psi_PM (``psi_d_Wb``), back-EMF at 1000 rpm
    (``V_peak``) and the cogging torque (peak-to-peak);
  * ``rated`` - magnetostatic rated point (the duty's own current / angle / hot temperatures): mean torque, Kt = T / I,
    torque ripple (virgin magnets, no eddy);
  * ``peak`` - the same at the peak current (peak torque);
  * ``rated_loss`` / ``maxspd_loss`` - the settled coupled-eddy transient at the rated speed and at 1.5 x the rated speed
    (the passport's top loss-grid speed): shaft / magnet / iron (stator + rotor) / copper loss, operating torque, ripple
    and the efficiency  eta = T w / (T w + P_loss)  (air-gap torque; the bearing/windage loss does not depend on the shaft).

NOTHING is written to a die, a configuration or the live app: the machine is built from a READ-ONLY COPY of the inputs in a
sandbox work directory and every file stays there.  Run it on the SERVER sandbox only (owner rule: all computations on the
server), never against the live API and never on the owner's PC.

    python scripts/shaft_material_fem_compare.py run --inputs <dir> --subdir die40 --config L12 --work <dir> \
        --shafts Aluminium_6061 Stainless_420 [--peak-current A] [--cases nl20 rated ...]
    python scripts/shaft_material_fem_compare.py summary --work <dir> --config L12 --ref Aluminium_6061 Steel_42CrMo4_QT

``<dir>`` holds ``<subdir>/{die,<config>}.yaml``, ``ws_motor_config.yaml`` and the two libraries, exactly the passport's
``inputs/``.  The extra card ``Stainless_420_soft_bound`` is an IDEAL-SOFT BOUND (mu_initial 3000, Bs 1.95 T), not a real
steel: it caps how much any ferromagnetic shaft could move the numbers.  It lives in the work directory's copy of the
library only; the repository library cards named in ``LIB_CARDS`` are merged into that copy.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
MU0 = 4e-7 * math.pi
SOFT_BOUND = "Stainless_420_soft_bound"
# (section, card) merged from the repository library into the job's copy of the deployed library
LIB_CARDS = (("steel", "Stainless_420"), ("steel", "Stainless_420_annealed"), ("conductor", "Stainless_304"),
             ("conductor", "Stainless_316"), ("conductor", "Aluminium_6061"))
CASES = ("nl20", "rated", "peak", "rated_loss", "maxspd_loss")
MAX_SPEED_FACTOR = 1.5


def soft_bound_card(base: Dict[str, Any]) -> Dict[str, Any]:
    """A copy of the 420 card with an IDEAL-SOFT B-H curve (mu_initial 3000, Js 1.95 T) - a bound, not a steel."""
    c = copy.deepcopy(base)
    js, mu0max = 1.95, 3000.0
    h = js / (MU0 * mu0max)
    hs = [0, 20, 50, 100, 200, 400, 800, 1500, 3000, 6000, 12000, 25000, 60000, 120000, 250000]
    c["bh_curve"] = [[x, round(MU0 * x + js * x / (x + h), 5)] for x in hs]
    c["description"] = "IDEAL-SOFT BOUND (mu_initial 3000, Bs 1.95 T) - NOT a real steel; caps the effect of a ferromagnetic shaft"
    c.pop("prov", None)
    c.pop("sources", None)
    return c


def prepare(a: argparse.Namespace) -> Dict[str, Any]:
    import yaml
    inp, work = Path(a.inputs), Path(a.work)
    shared = work / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    lib = yaml.safe_load((inp / "materials_library.yaml").read_text(encoding="utf-8"))
    repo = yaml.safe_load((ROOT / "config" / "materials_library.yaml").read_text(encoding="utf-8"))
    for sec, name in LIB_CARDS:
        if name not in (lib.get(sec) or {}):
            lib.setdefault(sec, {})[name] = repo[sec][name]
    lib["steel"][SOFT_BOUND] = soft_bound_card(repo["steel"]["Stainless_420"])
    with open(shared / "materials_library.yaml", "w", encoding="utf-8") as fh:
        yaml.safe_dump(lib, fh, sort_keys=False, allow_unicode=True)
    for f in ("bearings_library.yaml", "cooling_options.yaml"):
        for src in (inp / f, ROOT / "config" / f):
            if src.exists():
                shutil.copy2(src, shared / f)
                break
    (work / "ws").mkdir(parents=True, exist_ok=True)
    shutil.copy2(inp / "ws_motor_config.yaml", work / "ws" / "motor_config.yaml")
    os.environ["SHARED_ROOT"] = str(shared)
    os.environ["MOTOR_AI_SIM_CONFIG"] = str(work / "ws" / "motor_config.yaml")
    sub = inp / a.subdir
    die = yaml.safe_load((sub / "die.yaml").read_text(encoding="utf-8"))
    cfg = yaml.safe_load((sub / f"{a.config}.yaml").read_text(encoding="utf-8"))
    ws = yaml.safe_load((inp / "ws_motor_config.yaml").read_text(encoding="utf-8"))
    from motor_ai_sim.passport_v1 import snapshot as S
    return S.machine_snapshot(
        tag=a.config, die=die, cfg=cfg, rated_duty=a.duty, peak_duty=(a.peak_duty or None), materials_lib=lib,
        fallback_parts=dict(ws.get("materials") or {}),
        fallback_source="owner workspace motor_config.yaml (left in place by a duty load) - as the passport",
        source_files={}, owner_inputs={"purpose": "shaft material A/B (owner 2026-10-15)"},
        hot_override=({"magnet_c": a.hot_magnet, "coil_c": a.hot_coil, "source": "command line (the passport spec's hot_override)",
                       "reason": "the rated duty has no thermal steady state; the passport's own hot set"}
                      if a.hot_magnet is not None and a.hot_coil is not None else None))


def jobs_for(snap: Dict[str, Any], cases: List[str], peak_current: Optional[float]) -> List[Dict[str, Any]]:
    from motor_ai_sim.passport_v1 import jobs as J
    base = J.base_kwargs(snap, daxis_deg=None)
    rd, t = snap["rated_duty"], snap["temperatures"]
    pk = snap.get("peak_duty") or {}
    out: List[Dict[str, Any]] = []
    for case in cases:
        if case == "nl20":
            # the passport's own cold no-load job: one full period, 144 steps, purpose "cogging_quality"
            out.append(J.static_job("nl20", base, I_rms=0.0, gamma_deg=0.0, magnet_temp_c=20.0, coil_temp_c=20.0, rpm=13000.0,
                                    n_periods=1.0, steps=J.STATIC_STEPS_PER_PERIOD, purpose="cogging_quality"))
        elif case == "rated":
            out.append(J.static_job("rated", base, I_rms=rd["current_arms"], gamma_deg=rd["gamma_deg"],
                                    magnet_temp_c=t["hot_magnet_c"], coil_temp_c=t["hot_coil_c"], rpm=1000.0))
        elif case == "peak":
            cur = peak_current or pk.get("current_arms")
            if not cur:
                continue
            out.append(J.static_job("peak", base, I_rms=float(cur), gamma_deg=pk.get("gamma_deg", rd["gamma_deg"]),
                                    magnet_temp_c=t["hot_magnet_c"], coil_temp_c=t["hot_coil_c"], rpm=1000.0))
        elif case in ("rated_loss", "maxspd_loss"):
            f = 1.0 if case == "rated_loss" else MAX_SPEED_FACTOR
            out.append(J.loss_job(case, base, I_rms=rd["current_arms"], gamma_deg=rd["gamma_deg"], rpm=rd["rpm"] * f,
                                  magnet_temp_c=t["hot_magnet_c"], coil_temp_c=t["hot_coil_c"], steps=rd["steps_per_period"],
                                  end_winding_factor=rd["end_winding_factor"]))
    return out


def cmd_run(a: argparse.Namespace) -> int:
    sys.path.insert(0, str(ROOT / "src"))
    os.environ.setdefault("SB_NO_WARM_CACHE", "1")
    snap = prepare(a)
    from motor_ai_sim.passport_v1 import jobs as J
    work = Path(a.work)
    out_dir = work / "out" / a.config
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "snapshot.json").write_text(json.dumps(snap, default=str, indent=1), encoding="utf-8")
    tag = a.tag or "main"
    res_path = out_dir / f"results_{tag}.jsonl"
    done = set()
    if res_path.exists():
        for ln in open(res_path, encoding="utf-8"):
            try:
                r = json.loads(ln)
                if r.get("ok"):
                    done.add(r["id"])
            except Exception:                                    # noqa: BLE001
                pass
    base_mats = {k: v["name"] for k, v in snap["materials"].items()}
    jobs = jobs_for(snap, list(a.cases), a.peak_current)
    print(f"{a.config}/{a.duty}: shafts {a.shafts}; passport shaft {base_mats['shaft']}; cases {[j['id'] for j in jobs]}", flush=True)
    for shaft in a.shafts:
        mats = dict(base_mats)
        mats["shaft"] = shaft
        J.worker_init(mats)
        for job in jobs:
            jid = f"{shaft}:{job['id']}"
            if jid in done:
                continue
            j = dict(job)
            j["id"] = jid.replace(":", "__")
            rec = J.run_job(j, str(out_dir))
            rec["id"] = jid
            rec["shaft"] = shaft
            rec["case"] = job["id"]
            with open(res_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, default=str) + "\n")
            r = rec.get("r") or {}
            print(time.strftime("%T"), jid, rec.get("ok"), "wall %.0fs" % rec.get("wall_s", 0),
                  {k: r.get(k) for k in ("psi_d_Wb", "T_avg_Nm", "T_ripple_pp_Nm", "P_shaft_W")},
                  (rec.get("error") or "")[:300], flush=True)
    return 0


# --- the table -------------------------------------------------------------------------------------------------------

def collect(work: Path, config: str) -> Dict[str, Dict[str, Dict[str, Any]]]:
    rows: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for p in sorted((work / "out" / config).glob("results_*.jsonl")):
        for ln in open(p, encoding="utf-8"):
            try:
                r = json.loads(ln)
            except Exception:                                    # noqa: BLE001
                continue
            if r.get("ok"):
                rows.setdefault(r["shaft"], {})[r["case"]] = r.get("r") or {}
    return rows


def metrics(case_rows: Dict[str, Dict[str, Any]], snap: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """The named quantities of one shaft from its raw case results."""
    rd = snap["rated_duty"]
    pk = snap.get("peak_duty") or {}
    nl, ra, pe = case_rows.get("nl20", {}), case_rows.get("rated", {}), case_rows.get("peak", {})
    m: Dict[str, Optional[float]] = {}
    m["psi_PM cold [mWb]"] = None if nl.get("psi_d_Wb") is None else 1e3 * abs(nl["psi_d_Wb"])
    m["back-EMF @1000 rpm [V pk]"] = nl.get("V_peak")
    m["cogging p-p cold [mN.m]"] = None if nl.get("T_ripple_pp_Nm") is None else 1e3 * nl["T_ripple_pp_Nm"]
    m["T rated static [N.m]"] = ra.get("T_avg_Nm")
    m["Kt rated [N.m/A rms]"] = None if ra.get("T_avg_Nm") is None else ra["T_avg_Nm"] / rd["current_arms"]
    m["ripple p-p rated static [mN.m]"] = None if ra.get("T_ripple_pp_Nm") is None else 1e3 * ra["T_ripple_pp_Nm"]
    m["T peak static [N.m]"] = pe.get("T_avg_Nm")
    for case, tag, rpm_f in (("rated_loss", "rated", 1.0), ("maxspd_loss", "1.5x", MAX_SPEED_FACTOR)):
        r = case_rows.get(case, {})
        if not r:
            continue
        m[f"T operating @{tag} [N.m]"] = r.get("T_avg_Nm")
        m[f"ripple p-p @{tag} [mN.m]"] = None if r.get("T_ripple_pp_Nm") is None else 1e3 * r["T_ripple_pp_Nm"]
        m[f"P_shaft eddy @{tag} [W]"] = r.get("P_shaft_W")
        m[f"P_magnet eddy @{tag} [W]"] = r.get("P_mag_W")
        m[f"P_iron stator @{tag} [W]"] = r.get("P_fe_stator_W")
        m[f"P_iron rotor @{tag} [W]"] = r.get("P_fe_rotor_W")
        m[f"P_copper @{tag} [W]"] = r.get("P_cu_W")
        m[f"P_loss total @{tag} [W]"] = r.get("P_loss_total_W")
        if r.get("T_avg_Nm") is not None and r.get("P_loss_total_W") is not None:
            pw = r["T_avg_Nm"] * 2 * math.pi * rd["rpm"] * rpm_f / 60.0
            m[f"eta @{tag} [%]"] = 100.0 * pw / (pw + r["P_loss_total_W"])
    return m


def cmd_summary(a: argparse.Namespace) -> int:
    work = Path(a.work)
    rows = collect(work, a.config)
    snap = json.loads((work / "out" / a.config / "snapshot.json").read_text(encoding="utf-8"))
    shafts = [s for s in a.order if s in rows] + [s for s in rows if s not in a.order]
    mm = {s: metrics(rows[s], snap) for s in shafts}
    names = []
    for s in shafts:
        for k in mm[s]:
            if k not in names:
                names.append(k)
    lines = [f"# {snap['die']} / {a.config}: shaft material A/B", "",
             f"Rated duty {snap['rated_duty']['current_arms']} A rms, {snap['rated_duty']['rpm']:.0f} rpm, gamma "
             f"{snap['rated_duty']['gamma_deg']} deg; hot magnet {snap['temperatures']['hot_magnet_c']} C / coil "
             f"{snap['temperatures']['hot_coil_c']} C; passport shaft = {snap['materials']['shaft']['name']}.", ""]
    for ref in a.ref:
        if ref not in mm:
            continue
        lines += [f"## values and % vs {ref}", "", "| quantity | " + " | ".join(shafts) + " |", "|---|" + "---:|" * len(shafts)]
        for k in names:
            cells = []
            v0 = mm[ref].get(k)
            for s in shafts:
                v = mm[s].get(k)
                if v is None:
                    cells.append("-")
                elif s == ref or not v0:
                    cells.append(f"{v:.5g}")
                else:
                    d = 100.0 * (v - v0) / abs(v0)
                    cells.append(f"{v:.5g} ({d:+.2f} %)")
            lines.append(f"| {k} | " + " | ".join(cells) + " |")
        lines.append("")
    text = "\n".join(lines)
    (work / "out" / a.config / "summary.md").write_text(text, encoding="utf-8")
    (work / "out" / a.config / "metrics.json").write_text(json.dumps(mm, indent=1), encoding="utf-8")
    print(text)
    return 0


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--inputs", required=True)
    r.add_argument("--subdir", required=True)
    r.add_argument("--config", required=True)
    r.add_argument("--duty", default="rated")
    r.add_argument("--peak-duty", default="peak")
    r.add_argument("--peak-current", type=float, default=None, help="peak current when the configuration has no peak duty")
    r.add_argument("--hot-magnet", type=float, default=None, help="hot magnet temperature override [C] (passport hot_override)")
    r.add_argument("--hot-coil", type=float, default=None, help="hot winding temperature override [C]")
    r.add_argument("--work", required=True)
    r.add_argument("--shafts", nargs="+", required=True)
    r.add_argument("--cases", nargs="+", default=list(CASES), choices=CASES)
    r.add_argument("--tag", default=None, help="results file tag (one per parallel process)")
    s = sub.add_parser("summary")
    s.add_argument("--work", required=True)
    s.add_argument("--config", required=True)
    s.add_argument("--ref", nargs="+", required=True)
    s.add_argument("--order", nargs="*", default=[])
    a = ap.parse_args(argv)
    return cmd_run(a) if a.cmd == "run" else cmd_summary(a)


if __name__ == "__main__":
    raise SystemExit(main())
