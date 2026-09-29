"""Server verification of fix/coupled-pwm-dc-settle on the L180 gen case.

Re-runs the night-2026-09-28 job 2 (docs/NIGHT_2026-09-28_L180_PWM_AND_STAGE_A.md)
on THIS checkout's physics: CIANO10 200 opt / L180 gen / 'rated 1x9 mm', delta,
20 900 rpm, 24 kHz SVPWM through the Controller bridge, for both devices, one
after the other:

    WCMS900B170E53 x2 (R_th j-c 0.06, R_TIM 0.02, V_gs off -5 V)
    IMCQ120R004M2H x3

Before the fix the PWM pass was refused with -644.7 A / -886.1 A of DC left
(band 6.93 A).  PASS = the run exits 0, no "did not settle its DC" warning, and
the result carries the controller PWM pass.

It drives scripts/coupled_inverter_sandbox_run.py (sandboxed: the config is
COPIED from --config-dir into a temp dir, --no-save, nothing outside the
sandbox is written).  POSIX: every child runs under nice -n 19 ionice -c3;
threads are capped (<= 6).

    nice -n 19 ionice -c3 python scripts/verify_l180_pwm_dc_settle.py \
        --config-dir <read-only copy of the workspace> [--threads 6] [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
RUNNER = HERE / "scripts" / "coupled_inverter_sandbox_run.py"

DEVICES = [
    ("wcms", ["--device", "WCMS900B170E53", "--n-parallel", "2",
              "--vgs-off", "-5", "--rth-jc", "0.06", "--rtim", "0.02"]),
    ("imcq", ["--device", "IMCQ120R004M2H", "--n-parallel", "3"]),
]
COMMON = ["--die", "CIANO10 200 opt", "--config", "L180 gen",
          "--duty", "rated 1x9 mm", "--carrier", "24000",
          "--dead-time-us", "0.6", "--plate-flow", "10", "--plate-t-in", "60",
          "--max-iter", "2", "--no-mech", "--no-cold", "--no-save"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config-dir", required=True, type=Path)
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--only", choices=[d[0] for d in DEVICES])
    ap.add_argument("--out", type=Path,
                    default=HERE / "out" / ("dc_settle_verify_%s"
                                            % time.strftime("%Y%m%d_%H%M")))
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    # the runner writes its log/json next to itself: give it its own copy
    runner = a.out / RUNNER.name
    shutil.copy2(RUNNER, runner)
    t = str(max(1, min(int(a.threads), 6)))
    env = dict(os.environ, OMP_NUM_THREADS=t, MKL_NUM_THREADS=t,
               OPENBLAS_NUM_THREADS=t, NUMEXPR_NUM_THREADS=t,
               FEM_SCAN_WORKERS="1", PYTHONUNBUFFERED="1",
               INV_BG_ROOT=str(HERE), INV_BG_SRC=str(HERE / "src"),
               INV_BG_REAL_CONFIG=str(a.config_dir.resolve()))
    env["PYTHONPATH"] = str(HERE / "src")
    pre = (["nice", "-n", "19", "ionice", "-c3"] if os.name != "nt" else [])
    verdict = {}
    for tag, dev in DEVICES:
        if a.only and tag != a.only:
            continue
        log = a.out / ("run_%s.out" % tag)
        t0 = time.time()
        with open(log, "w", encoding="utf-8") as fh:
            rc = subprocess.call(pre + [sys.executable, "-u", str(runner)]
                                 + COMMON + dev, cwd=str(a.out), env=env,
                                 stdout=fh, stderr=subprocess.STDOUT)
        res_p = a.out / "coupled_inverter_bg_l180_gen.json"
        res = (json.loads(res_p.read_text(encoding="utf-8"))
               if res_p.exists() else None)
        if res_p.exists():
            res_p.replace(a.out / ("%s.json" % tag))
        text = log.read_text(encoding="utf-8", errors="replace")
        dc = [float(x) for x in re.findall(
            r"P2 vdrive: (-?[\d.]+) A of DC left", text)]
        row = res[0] if isinstance(res, list) and res else {}
        warn = str(row.get("warning") or "")
        ok = (rc == 0 and "did not settle its DC" not in warn
              and bool(row.get("inverter")))
        verdict[tag] = {"exit": rc, "wall_s": round(time.time() - t0),
                        "warning": warn, "dc_left_A_logged": dc,
                        "ripple_quotable": row.get("ripple_quotable"),
                        "pass": ok}
        print(tag, "PASS" if ok else "FAIL", warn, dc, flush=True)
    (a.out / "verdict.json").write_text(json.dumps(verdict, indent=1),
                                        encoding="utf-8")
    print("results:", a.out)
    return 0 if verdict and all(v["pass"] for v in verdict.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
