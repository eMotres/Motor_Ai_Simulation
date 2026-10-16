"""Child process: ngspice shared library via ctypes, one netlist, then exit.

``python -m motor_ai_sim.inverter.spice._worker --dll <ngspice.dll>
--cir <file.cir> --out <file.data> --compat psa --vectors "v(a) i(vb)"``

Kept in its own process on purpose: the shared library holds global state,
a fatal model error in ngspice may call ``exit()``, and the parent must be
able to kill a runaway transient without taking itself down.
"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys
from ctypes import CFUNCTYPE, c_bool, c_char_p, c_int, c_void_p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dll", required=True)
    ap.add_argument("--cir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--compat", default="psa")
    ap.add_argument("--vectors", required=True)
    a = ap.parse_args(argv)

    d = os.path.dirname(a.dll)
    if hasattr(os, "add_dll_directory") and d:
        os.add_dll_directory(d)
    ng = ctypes.CDLL(a.dll)
    errors = []
    aborted = []
    exited = {"flag": False}

    SendChar = CFUNCTYPE(c_int, c_char_p, c_int, c_void_p)
    SendStat = CFUNCTYPE(c_int, c_char_p, c_int, c_void_p)
    ControlledExit = CFUNCTYPE(c_int, c_int, c_bool, c_bool, c_int, c_void_p)

    @SendChar
    def _char(s, _i, _p):
        txt = s.decode(errors="replace") if s else ""
        print(txt, flush=True)
        low = txt.lower()
        if "error" in low and "reference value" not in low:
            errors.append(txt)
        # an analysis that dies half-way ("Timestep too small ... run
        # simulation(s) aborted") still lets ``wrdata`` write the few points
        # it had — that must be a FAILURE, never a short waveform
        if "aborted" in low or "timestep too small" in low:
            aborted.append(txt)
        return 0

    @SendStat
    def _stat(_s, _i, _p):
        return 0

    @ControlledExit
    def _exit(status, _unload, _quit, _ident, _p):
        exited["flag"] = True
        print(f"ngspice requested exit ({status})", flush=True)
        return 0

    ng.ngSpice_Init(_char, _stat, _exit, None, None, None, None)
    cir = os.path.abspath(a.cir)
    os.chdir(os.path.dirname(cir))
    for cmd in (f"set ngbehavior={a.compat}", f"source {os.path.basename(cir)}",
                "run", "set wr_singlescale", "set wr_vecnames",
                f"wrdata {os.path.abspath(a.out)} {a.vectors}"):
        ng.ngSpice_Command(cmd.encode())
        if exited["flag"]:
            return 3
    if aborted:
        print("worker: analysis ABORTED: " + " | ".join(aborted[-3:]), flush=True)
        if os.path.exists(a.out):
            os.remove(a.out)
        return 4
    if not os.path.exists(a.out):
        print("worker: no data written; errors: " + " | ".join(errors[-5:]), flush=True)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
