"""Bounded startup-only benchmark: no config, API, geometry or FEM imports.

python -B scripts/bench_pardiso_startup.py --output <artifact.json> --repeats 3
Each child also solves the same 2x2 sparse system and releases native memory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src"))

CHILD = r'''
import json, time
started = time.perf_counter()
import pypardiso
import_seconds = time.perf_counter() - started
import numpy as np
from scipy.sparse import csr_matrix
solver = pypardiso.scipy_aliases.pypardiso_solver
try:
    A = csr_matrix([[4., 1.], [1., 3.]])
    b = np.array([1., 2.])
    x = pypardiso.spsolve(A, b)
    print("@@BENCH@@" + json.dumps(dict(import_seconds=import_seconds,
        library=solver.libmkl._name, solution=x.tolist(), residual=float(np.linalg.norm(A@x-b)))))
finally:
    solver.free_memory(everything=True)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 5:
        parser.error("repeats must be between 1 and 5")
    # The benchmark process and its children are explicitly single-threaded.
    for key in ("MKL_NUM_THREADS", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ[key] = "1"
    os.environ.pop("PYPARDISO_MKL_RT", None)
    os.environ.pop("SB_NO_PARDISO", None)
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    from motor_ai_sim.simulation.pardiso_runtime import pardiso_subprocess_env
    baseline_env = dict(os.environ)
    started = time.perf_counter()
    hinted_env = pardiso_subprocess_env(baseline_env)
    discovery_seconds = time.perf_counter()-started
    if "PYPARDISO_MKL_RT" not in hinted_env:
        raise RuntimeError("No absolute loaded runtime was available; child fallback remains unchanged")
    rows = []
    for repeat in range(args.repeats):
        # Alternate order to avoid systematically giving one side a colder OS
        # filesystem cache. This measures fresh interpreters, not a reboot.
        cases = [("normal-discovery", baseline_env), ("inherited-runtime", hinted_env)]
        if repeat % 2:
            cases.reverse()
        for label, env in cases:
            start = time.perf_counter()
            result = subprocess.run([sys.executable, "-B", "-c", CHILD], env=env,
                                    capture_output=True, text=True, check=True, timeout=45)
            payload = next(line[len("@@BENCH@@"):] for line in result.stdout.splitlines()
                           if line.startswith("@@BENCH@@"))
            row = json.loads(payload)
            row.update(mode=label, repeat=repeat, wall_seconds=time.perf_counter()-start)
            assert row["residual"] < 1e-12
            assert os.path.samefile(row["library"], hinted_env["PYPARDISO_MKL_RT"])
            rows.append(row)
    assert all(row["solution"] == rows[0]["solution"] for row in rows)
    medians = {label: statistics.median(row["import_seconds"] for row in rows if row["mode"] == label)
               for label in ("normal-discovery", "inherited-runtime")}
    report = dict(python=sys.version, executable=sys.executable, one_time_discovery_seconds=discovery_seconds,
                  runtime=hinted_env["PYPARDISO_MKL_RT"], rows=rows, median_import_seconds=medians,
                  import_speedup=medians["normal-discovery"]/medians["inherited-runtime"],
                  identical_solution=True, threads=1, no_FEM_or_config=True,
                  helper_sha256=hashlib.sha256((ROOT/"src/motor_ai_sim/simulation/pardiso_runtime.py").read_bytes()).hexdigest())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
