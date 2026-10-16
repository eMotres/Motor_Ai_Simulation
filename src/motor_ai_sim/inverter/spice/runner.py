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

* ``ltspice`` — Analog Devices LTspice in batch mode (``LTspice.exe -b``),
  for vendor libraries ENCRYPTED for LTspice (the Infineon CoolSiC 750 V G2
  ``_LTSpice.lib``): the same ``.cir`` text, the binary ``.raw`` read back by
  :func:`parse_ltspice_raw`.  Chosen automatically for a model whose
  manifest says ``status: usable_ltspice`` (``compat == "ltspice"``);
  ``$MOTOR_AI_SIM_LTSPICE`` overrides the install path.

Resolution order (ngspice): ``$MOTOR_AI_SIM_NGSPICE`` (a path to either an executable
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

__all__ = ["NgspiceError", "Backend", "find_backend", "find_ltspice",
           "backend_for_compat", "run_netlist", "parse_wrdata",
           "parse_ltspice_raw"]


class NgspiceError(RuntimeError):
    pass


@dataclass
class Backend:
    kind: str            # "dll" | "cli" | "ltspice"
    path: str
    version: str = ""

    def describe(self) -> str:
        if self.kind == "ltspice":
            return f"LTspice (batch -b) {self.path}" + (f" [{self.version}]" if self.version else "")
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


def find_ltspice() -> Backend:
    """LTspice's executable: ``$MOTOR_AI_SIM_LTSPICE``, the per-user install
    (``%LOCALAPPDATA%\\Programs\\ADI\\LTspice``), then Program Files."""
    env = os.environ.get("MOTOR_AI_SIM_LTSPICE")
    cands = [env] if env else []
    la = os.environ.get("LOCALAPPDATA")
    if la:
        cands.append(os.path.join(la, "Programs", "ADI", "LTspice", "LTspice.exe"))
    for root in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramW6432", r"C:\Program Files")):
        cands.append(os.path.join(root, "ADI", "LTspice", "LTspice.exe"))
    for c in cands:
        if c and os.path.isfile(c):
            return Backend("ltspice", c)
    raise NgspiceError(
        "no LTspice found: install LTspice (free, Analog Devices) or set "
        "MOTOR_AI_SIM_LTSPICE to LTspice.exe")


def backend_for_compat(compat: str, backend: Optional[Backend] = None) -> Backend:
    """The backend a model needs: LTspice for an LTspice-encrypted library
    (``compat == "ltspice"``), else ``backend`` or :func:`find_backend` — so a
    caller holding the ngspice backend can pass it for every part."""
    if str(compat).lower() == "ltspice":
        return backend if (backend is not None and backend.kind == "ltspice") else find_ltspice()
    if backend is not None and backend.kind == "ltspice":
        return find_backend()
    return backend or find_backend()


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


def _read_log(path: Path) -> str:
    """LTspice writes its .log in UTF-16 LE on some versions, UTF-8 on others."""
    if not path.is_file():
        return ""
    b = path.read_bytes()
    if b[:2] == b"\xff\xfe" or (len(b) > 3 and b[1:2] == b"\x00" and b[3:4] == b"\x00"):
        return b.decode("utf-16-le", errors="replace").lstrip("\ufeff")
    return b.decode("utf-8", errors="replace")


def parse_ltspice_raw(path: Path) -> Dict[str, np.ndarray]:
    """Read an LTspice ``.raw`` (binary or ASCII; UTF-16 LE or 8-bit header).

    Returns ``{"scale": …, <lower-case vector name>: …}``.  Real binary data:
    the scale is float64 and the other vectors float32 unless the flags say
    ``double``; complex data (``.ac``): complex128 throughout, returned under
    the name plus ``real(<name>)`` / ``imag(<name>)``.  A transient scale is
    ``abs(time)`` (LTspice flags some points with a negative sign).
    Waveform compression must be off in the netlist (``.options
    plotwinsize=0``) — this reader does not interpolate.
    """
    b = Path(path).read_bytes()
    utf16 = b[1:2] == b"\x00"
    enc = "utf-16-le" if utf16 else "latin-1"
    for mark in ("Binary:\n", "Values:\n"):
        mb = mark.encode(enc)
        k = b.find(mb)
        if k >= 0:
            header = b[:k].decode(enc, errors="replace")
            data = b[k + len(mb):]
            binary = mark.startswith("Binary")
            break
    else:
        raise NgspiceError(f"{path}: not an LTspice raw file")
    hdr: Dict[str, str] = {}
    names: List[str] = []
    in_vars = False
    for ln in header.replace("\r", "").split("\n"):
        if in_vars:
            parts = ln.split()
            if len(parts) >= 3 and parts[0].isdigit():
                names.append(parts[1].lower())
                continue
            in_vars = False
        if ln.startswith("Variables:"):
            in_vars = True
            continue
        if ":" in ln:
            key, _, val = ln.partition(":")
            hdr[key.strip().lower()] = val.strip()
    nvar = int(hdr.get("no. variables", len(names)))
    npts = int(hdr.get("no. points", "0"))
    flags = hdr.get("flags", "").lower()
    cplx = "complex" in flags
    dbl = "double" in flags
    if len(names) != nvar or nvar < 1:
        raise NgspiceError(f"{path}: header lists {len(names)} of {nvar} variables")
    if binary:
        if cplx:
            arr = np.frombuffer(data[:npts * nvar * 16], dtype="<c16").reshape(-1, nvar)
            cols = [arr[:, j] for j in range(nvar)]
        elif dbl:
            arr = np.frombuffer(data[:npts * nvar * 8], dtype="<f8").reshape(-1, nvar)
            cols = [arr[:, j] for j in range(nvar)]
        else:
            rec = np.dtype([("s", "<f8")] + [(f"v{j}", "<f4") for j in range(1, nvar)])
            arr = np.frombuffer(data[:npts * rec.itemsize], dtype=rec)
            cols = [arr["s"].astype(float)] + [arr[f"v{j}"].astype(float)
                                               for j in range(1, nvar)]
    else:
        toks = data.decode(enc, errors="replace").split()
        vals: List[complex] = []
        j = 0
        while j < len(toks) and len(vals) < npts * nvar:
            if len(vals) % nvar == 0:
                j += 1                      # the point index
            t = toks[j]
            if "," in t:
                re_, im_ = t.split(",")[:2]
                vals.append(complex(float(re_), float(im_)))
            else:
                vals.append(complex(float(t), 0.0))
            j += 1
        a = np.array(vals).reshape(-1, nvar)
        cols = [a[:, k] if cplx else a[:, k].real for k in range(nvar)]
    if len(cols[0]) < npts:
        raise NgspiceError(f"{path}: truncated ({len(cols[0])} of {npts} points)")
    out: Dict[str, np.ndarray] = {}
    sc = cols[0]
    if cplx:
        out["scale"] = np.abs(np.real(sc))
    else:
        sc = np.asarray(sc, float)
        out["scale"] = np.abs(sc) if names[0] == "time" else sc
    for nm, c in zip(names[1:], cols[1:]):
        if cplx:
            out[nm] = c
            out[f"real({nm})"] = np.real(c)
            out[f"imag({nm})"] = np.imag(c)
        else:
            out[nm] = np.asarray(c, float)
    return out


#: LTspice log lines that mean the run is not data (it may still write a
#: partial .raw and exit 0)
_LT_FAIL = ("timestep too small", "singular matrix", "simulation aborted",
            "fatal error", "unknown subcircuit", "could not open include",
            "unknown parameter", "error:")


def _run_ltspice(cir: Path, vectors: Sequence[str], be: Backend, timeout_s: float,
                 env: Dict[str, str]) -> Dict[str, np.ndarray]:
    raw = cir.with_suffix(".raw")
    log = cir.with_suffix(".log")          # LTspice's own log (same stem)
    for f in (raw, log, cir.with_suffix(".op.raw")):
        if f.exists():
            f.unlink()
    t0 = time.time()
    proc = subprocess.Popen([be.path, "-b", cir.name], cwd=str(cir.parent),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            env=env, **_low_priority_kwargs())  # type: ignore[arg-type]
    try:
        rc = proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise NgspiceError(f"{cir.name}: LTspice timed out after {timeout_s:.0f} s")
    dt = time.time() - t0
    txt = _read_log(log)
    low = txt.lower()
    bad = [m for m in _LT_FAIL if m in low]
    if rc != 0 or bad or not raw.is_file():
        tail = txt.splitlines()[-25:]
        raise NgspiceError(f"{cir.name}: LTspice failed (rc={rc}, {dt:.1f} s, {bad})\n"
                           + "\n".join(tail))
    r = parse_ltspice_raw(raw)
    out: Dict[str, np.ndarray] = {"scale": r["scale"]}
    for nm in vectors:
        k = nm.lower()
        if k not in r:
            raise NgspiceError(f"{raw.name}: vector {nm} missing (have {sorted(r)[:24]})")
        out[nm] = r[k]
    out["_elapsed_s"] = np.array([dt])
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
    be = backend_for_compat(compat, backend)
    env = dict(os.environ)
    env["OMP_NUM_THREADS"] = "1"
    if be.kind == "ltspice":
        return _run_ltspice(cir, vectors, be, timeout_s, env)
    data = cir.with_suffix(".data")
    log = cir.with_suffix(".log")
    for f in (data,):
        if f.exists():
            f.unlink()
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
