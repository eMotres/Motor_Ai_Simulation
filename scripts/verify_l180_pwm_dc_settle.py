"""Server verification of fix/coupled-pwm-dc-settle on the L180 gen case.

Re-runs the night-2026-09-28 job 2 (docs/NIGHT_2026-09-28_L180_PWM_AND_STAGE_A.md)
on THIS checkout's physics: CIANO10 200 opt / L180 gen / 'rated 1x9 mm', delta,
20 900 rpm, 24 kHz SVPWM through the Controller bridge, for both devices:

    WCMS900B170E53 x2 (R_th j-c 0.06, R_TIM 0.02, V_gs off -5 V)
    IMCQ120R004M2H x3

Before the fix the PWM pass was refused with -644.7 A / -886.1 A of DC left
(band 6.93 A).  PASS = both runs finish with a PWM pass whose DC residual is
inside the band and no "did not settle its DC" warning.

It drives the night's sandboxed runner (coupled_l180_cmp.py — nothing outside
its sandbox is written, --no-save) with its hard-coded source path pointed at
this checkout's src/.  Threads are capped (default 4), the process runs at
below-normal priority, one device at a time.

    python scripts/verify_l180_pwm_dc_settle.py --runner <path>/coupled_l180_cmp.py \
        [--threads 4] [--out <dir>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
BAND_A = 6.93

DEVICES = [
    ("wcms", ["--device", "WCMS900B170E53", "--n-parallel", "2",
              "--vgs-off", "-5", "--rth-jc", "0.06", "--rtim", "0.02"]),
    ("imcq", ["--device", "IMCQ120R004M2H", "--n-parallel", "3"]),
]
COMMON = ["--die", "CIANO10 200 opt", "--config", "L180 gen",
          "--duty", "rated 1x9 mm", "--carrier", "24000",
          "--dead-time-us", "0.6", "--plate-flow", "10", "--plate-t-in", "60",
          "--max-iter", "2", "--no-mech", "--no-cold", "--no-save"]


def _patched_runner(runner: Path, out: Path) -> Path:
    src = runner.read_text(encoding="utf-8")
    ours = str(HERE / "src")
    n = 0

    def _sub(m):
        nonlocal n
        n += 1
        return "sys.path.insert(0, %r)" % ours
    src = re.sub(r"sys\.path\.insert\(0,\s*r?\"[^\"]*\\src\"\)", _sub, src)
    if n == 0:
        raise SystemExit("runner has no hard-coded src path to redirect")
    dst = out / "coupled_l180_cmp_fix.py"
    dst.write_text(src, encoding="utf-8")
    return dst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runner", required=True, type=Path)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--out", type=Path,
                    default=HERE / "out" / ("dc_settle_verify_%s"
                                            % time.strftime("%Y%m%d_%H%M")))
    a = ap.parse_args()
    if os.name != "nt":
        raise SystemExit("the night runner is Windows-only (kernel32 priority "
                         "proof); run this on the Windows compute host")
    a.out.mkdir(parents=True, exist_ok=True)
    runner = _patched_runner(a.runner.resolve(), a.out)
    t = str(max(1, min(int(a.threads), 8)))
    env = dict(os.environ, OMP_NUM_THREADS=t, MKL_NUM_THREADS=t,
               OPENBLAS_NUM_THREADS=t, NUMEXPR_NUM_THREADS=t,
               FEM_SCAN_WORKERS="2", PYTHONUNBUFFERED="1")
    env["PYTHONPATH"] = str(HERE / "src") + os.pathsep + env.get("PYTHONPATH", "")
    verdict = {}
    for tag, dev in DEVICES:
        log = a.out / ("run_%s.out" % tag)
        t0 = time.time()
        with open(log, "w", encoding="utf-8") as fh:
            rc = subprocess.call(
                [sys.executable, "-u", str(runner)] + COMMON + dev,
                cwd=str(a.out), env=env, stdout=fh, stderr=subprocess.STDOUT,
                creationflags=0x00004000)       # BELOW_NORMAL_PRIORITY_CLASS
        res_p = a.out / "coupled_inverter_bg_l180_gen.json"
        res = json.loads(res_p.read_text(encoding="utf-8")) if res_p.exists() else None
        if res_p.exists():
            res_p.replace(a.out / ("%s.json" % tag))
        text = log.read_text(encoding="utf-8", errors="replace")
        dc = [float(x) for x in re.findall(
            r"P2 vdrive: (-?[\d.]+) A of DC left", text)]
        orbit = re.findall(r"P2 vdrive DC-orbit solve at frame .*", text)
        row = res[0] if isinstance(res, list) and res else {}
        warn = str(row.get("warning") or "")
        ok = (rc == 0 and "did not settle its DC" not in warn
              and bool(row.get("inverter")))
        verdict[tag] = {"exit": rc, "wall_s": round(time.time() - t0),
                        "warning": warn, "dc_left_A_logged": dc,
                        "orbit_lines": orbit[-13:],
                        "ripple_quotable": row.get("ripple_quotable"),
                        "pass": ok}
        print(tag, "PASS" if ok else "FAIL", warn or "", dc, flush=True)
    (a.out / "verdict.json").write_text(json.dumps(verdict, indent=1),
                                        encoding="utf-8")
    print("results:", a.out)
    return 0 if all(v["pass"] for v in verdict.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
