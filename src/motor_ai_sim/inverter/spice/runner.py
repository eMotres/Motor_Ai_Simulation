"""Run a netlist in ngspice — in a SEPARATE, low-priority process.

Two back-ends, one contract (a ``.cir`` in, a ``wrdata`` table out):

* ``dll`` — ngspice as a shared library (``ngspice.dll`` / ``libngspice.so``)
  loaded with :mod:`ctypes` in a child Python process
  (:mod:`motor_ai_sim.inverter.spice._worker`).  On this workstation that is
  the copy KiCad itself ships (``C:\\Program Files\\KiCad\\<ver>\\bin``), i.e.
  literally the engine KiCad's simulator uses.
* ``cli`` — the ngspice console program in batch mode (``ngspice -b``), e.g.
  ``apt-get install ngspice`` on the server; the compatibility mode goes into
  a ``.spiceinit`` beside the netlist.

Resolution order: ``$MOTOR_AI_SIM_NGSPICE`` (a path to either an executable
or a shared library), then KiCad's bundled DLL, then ``ngspice`` on PATH.

The child always runs at BELOW-NORMAL priority (Windows) / ``nice 19``
(POSIX), with ``OMP_NUM_THREADS=1``, one at a time, and is killed on timeout.
The owner's workstation is his solver; this never runs anything in parallel.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np

__all__ = ["NgspiceError", "Backend", "find_backend", "run_netlist",
           "parse_wrdata"]


class NgspiceError(RuntimeError):
    pass


@dataclass
class Backend:
    kind: str            # "dll" | "cli"
    path: str
    version: str = ""

    def describe(self) -> str:
        return f"ngspice ({self.kind}) {self.path}" + (f" [{self.version}]" if self.version else "")


def find_backend() -> Backend:
    env = os.environ.get("MOTOR_AI_SIM_NGSPICE")
    if env:
        low = env.lower()
        if low.endswith((".dll", ".so", ".dylib")) or ".so." in low:
            return Backend("dll", env)
        return Backend("cli", env)
    cands: List[str] = []
    for root in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramW6432", r"C:\Program Files")):
        cands += sorted(glob.glob(os.path.join(root, "KiCad", "*", "bin", "ngspice.dll")),
                        reverse=True)
    for c in cands:
        if os.path.isfile(c):
            return Backend("dll", c)
    exe = shutil.which("ngspice") or shutil.which("ngspice_con")
    if exe:
        return Backend("cli", exe)
    for so in ("/usr/lib/x86_64-linux-gnu/libngspice.so.0", "/usr/lib/libngspice.so"):
        if os.path.isfile(so):
            return Backend("dll", so)
    raise NgspiceError(
        "no ngspice found: install KiCad (bundles ngspice.dll) or ngspice, or "
        "set MOTOR_AI_SIM_NGSPICE to the executable / shared library")


def _low_priority_kwargs() -> Dict[str, object]:
    if os.name == "nt":
        return {"creationflags": 0x00004000 | 0x08000000}  # BELOW_NORMAL | NO_WINDOW
    return {"preexec_fn": lambda: os.nice(19)}               # type: ignore[attr-defined]


def parse_wrdata(path: Path, names: Sequence[str]) -> Dict[str, np.ndarray]:
    """Read an ngspice ``wrdata`` file written with ``wr_singlescale`` and
    ``wr_vecnames`` set: one header line, then ``scale v1 v2 …`` columns.

    Returns ``{"scale": …, <name>: …}`` with the names the caller asked for
    (case-insensitive match against the header; the header wins on order).
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    rows: List[List[float]] = []
    header: Optional[List[str]] = None
    for ln in text:
        s = ln.strip()
        if not s:
            continue
        parts = s.split()
        try:
            rows.append([float(x) for x in parts])
        except ValueError:
            if header is None:
                header = parts
            continue
    if not rows:
        raise NgspiceError(f"{path}: no data rows")
    arr = np.array(rows, dtype=float)
    out: Dict[str, np.ndarray] = {"scale": arr[:, 0]}
    cols = header[1:] if header and len(header) == arr.shape[1] else None
    for k, nm in enumerate(names):
        j = k + 1
        if cols:
            low = [c.lower() for c in cols]
            if nm.lower() in low:
                j = low.index(nm.lower()) + 1
        if j >= arr.shape[1]:
            raise NgspiceError(f"{path}: vector {nm} missing")
        out[nm] = arr[:, j]
    return out


def run_netlist(cir: Path, vectors: Sequence[str], *, compat: str = "psa",
                timeout_s: float = 900.0, backend: Optional[Backend] = None,
                keep_log: bool = True) -> Dict[str, np.ndarray]:
    """Run ``cir`` (its own analysis line decides what), write ``vectors``.

    The data file and the log land beside the netlist
    (``<stem>.data`` / ``<stem>.log``).  Raises :class:`NgspiceError` with the
    log's tail when ngspice fails or writes nothing.
    """
    cir = Path(cir).resolve()
    be = backend or find_backend()
    data = cir.with_suffix(".data")
    log = cir.with_suffix(".log")
    for f in (data,):
        if f.exists():
            f.unlink()
    env = dict(os.environ)
    env["OMP_NUM_THREADS"] = "1"
    vec = " ".join(vectors)
    if be.kind == "dll":
        cmd = [sys.executable, "-m", "motor_ai_sim.inverter.spice._worker",
               "--dll", be.path, "--cir", str(cir), "--out", str(data),
               "--compat", compat, "--vectors", vec]
        src = str(Path(__file__).resolve().parents[3])
        env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        cwd = str(cir.parent)
    else:
        ctl = cir.with_suffix(".ctl.cir")
        ctl.write_text(
            "* control wrapper\n.control\n"
            f"source {cir.name}\nrun\nset wr_singlescale\nset wr_vecnames\n"
            f"wrdata {data.name} {vec}\nquit\n.endc\n.end\n", encoding="utf-8")
        (cir.parent / ".spiceinit").write_text(f"set ngbehavior={compat}\n",
                                               encoding="utf-8")
        cmd = [be.path, "-b", ctl.name]
        cwd = str(cir.parent)
    t0 = time.time()
    with log.open("w", encoding="utf-8", errors="replace") as lf:
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=lf, stderr=subprocess.STDOUT,
                                env=env, **_low_priority_kwargs())  # type: ignore[arg-type]
        try:
            rc = proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise NgspiceError(f"{cir.name}: ngspice timed out after {timeout_s:.0f} s")
    dt = time.time() - t0
    if be.kind == "cli" and data.exists():
        low = log.read_text(encoding="utf-8", errors="replace").lower()
        if "aborted" in low or "timestep too small" in low:
            data.unlink()          # a half-run waveform is a failure, not data
    if rc != 0 or not data.exists():
        tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-25:]
        raise NgspiceError(f"{cir.name}: ngspice failed (rc={rc}, {dt:.1f} s)\n"
                           + "\n".join(tail))
    out = parse_wrdata(data, vectors)
    out["_elapsed_s"] = np.array([dt])
    if not keep_log:
        log.unlink(missing_ok=True)
    return out
