"""The experiments: one double pulse, one static sweep — netlist, run, extract.

Every experiment writes its netlist as a standalone ``.cir`` in ``workdir``
(named after the conditions) BEFORE running it, so the exact circuit this
module measured can be opened in KiCad / LTspice by hand.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from .extract import double_pulse_metrics
from .models import SpiceModel, spice_dir
from .netlist import DoublePulse, double_pulse_netlist, dp_vectors, static_netlist
from .runner import Backend, NgspiceError, run_netlist

__all__ = ["run_double_pulse", "run_static", "default_workdir"]


def default_workdir(part: str) -> Path:
    d = spice_dir() / "_runs" / part
    d.mkdir(parents=True, exist_ok=True)
    return d


def _key(name: str) -> str:
    """``v(dl)`` -> ``v__dl__`` (an npz member name)."""
    return name.replace("(", "__").replace(")", "__")


def _unkey(name: str) -> str:
    if name.endswith("__") and "__" in name[:-2]:
        a, b = name[:-2].split("__", 1)
        return f"{a}({b})"
    return name


def _tag(x: float) -> str:
    return re.sub(r"[^0-9a-zA-Z.-]", "_", f"{x:g}")


def _same_circuit(a: str, b: str) -> bool:
    """Two netlists describe the same simulation: equal once the ``.save``
    line (which vectors are KEPT, not what is solved) is set aside."""
    def core(s: str) -> str:
        return "\n".join(ln for ln in s.splitlines()
                         if not ln.lower().startswith(".save"))
    return core(a) == core(b)


def dp_name(dp: DoublePulse) -> str:
    """File stem of one run.  A re-timed first pulse (``l_load`` fixed) and a
    transient cut before the second turn-off get their own stems, so every
    variant keeps its own stored waveform and none overwrites another."""
    s = (f"dp_V{_tag(dp.v_dd)}_I{_tag(dp.i_target)}_T{_tag(dp.t_j)}"
         f"_Rg{_tag(dp.rg_on)}-{_tag(dp.rg_off)}_Vgs{_tag(dp.v_gs_off)}-{_tag(dp.v_gs_on)}"
         f"_Ls{_tag(dp.l_sigma * 1e9)}")
    if dp.l_load:
        s += f"_t1-{_tag(round(dp.t_first * 1e9, 1))}ns"
    if dp.stop_before_second_off:
        s += "_cut"
    return s


def run_double_pulse(model: SpiceModel, dp: DoublePulse, *,
                     workdir: Optional[Path] = None,
                     backend: Optional[Backend] = None,
                     reuse: bool = True, timeout_s: float = 1800.0,
                     correct_current: bool = True) -> Dict[str, Any]:
    """Run (or re-read) one double pulse and return its metrics.

    ``reuse`` — a finished run with the same netlist text is read back from
    its ``.json`` instead of re-simulated (the netlist is the key, so any
    change of circuit or library re-runs).

    ``correct_current`` — if the current switched off misses the target by
    more than 1.5 %, the first pulse is re-timed once and the run repeated.
    """
    wd = Path(workdir) if workdir else default_workdir(model.part)
    wd.mkdir(parents=True, exist_ok=True)
    for attempt in range(2):
        name = dp_name(dp)
        if reuse and not dp.stop_before_second_off and not (wd / f"{name}.json").is_file():
            cut = DoublePulse(**{**dp.__dict__, "stop_before_second_off": True})
            if (wd / f"{dp_name(cut)}.json").is_file():
                dp, name = cut, dp_name(cut)     # this point only ever ran cut
        cir = wd / f"{name}.cir"
        text = double_pulse_netlist(model, dp)
        res_p = wd / f"{name}.json"
        npz_p = wd / f"{name}.npz"
        if (reuse and res_p.is_file() and npz_p.is_file() and cir.is_file()
                and _same_circuit(cir.read_text(encoding="utf-8"), text)):
            # same circuit, same library: re-extract from the stored waveforms
            # (so a change of the extraction never needs a re-simulation)
            res = json.loads(res_p.read_text(encoding="utf-8"))
            with np.load(npz_p) as z:
                r = {_unkey(k): z[k] for k in z.files}
            res["metrics"] = double_pulse_metrics(
                r, v_dd=dp.v_dd, timeline=dp.timeline(),
                v_gs_on=dp.v_gs_on, v_gs_off=dp.v_gs_off)
            res_p.write_text(json.dumps(res, indent=1, default=float), encoding="utf-8")
        else:
            cir.write_text(text, encoding="utf-8")
            try:
                r = run_netlist(cir, dp_vectors(model), compat=model.compat,
                                backend=backend, timeout_s=timeout_s)
            except NgspiceError:
                if dp.stop_before_second_off:
                    raise
                # nothing is read from the second turn-off: run once more
                # without it (a failure there, or a transient refusal such as
                # ngspice's memory check, must not lose the point)
                dp = DoublePulse(**{**dp.__dict__, "stop_before_second_off": True})
                name = dp_name(dp)
                cir, res_p, npz_p = (wd / f"{name}.cir", wd / f"{name}.json",
                                     wd / f"{name}.npz")
                text = double_pulse_netlist(model, dp)
                if (reuse and res_p.is_file() and npz_p.is_file() and cir.is_file()
                        and _same_circuit(cir.read_text(encoding="utf-8"), text)):
                    return run_double_pulse(model, dp, workdir=wd, backend=backend,
                                            reuse=True, timeout_s=timeout_s,
                                            correct_current=correct_current)
                cir.write_text(text, encoding="utf-8")
                r = run_netlist(cir, dp_vectors(model), compat=model.compat,
                                backend=backend, timeout_s=timeout_s)
            t_end = float(r["scale"][-1])
            if t_end < 0.999 * dp.timeline()["t_stop"]:
                raise NgspiceError(f"{cir.name}: transient stopped at {t_end:.3e} s "
                                   f"of {dp.timeline()['t_stop']:.3e} s")
            met = double_pulse_metrics(r, v_dd=dp.v_dd, timeline=dp.timeline(),
                                       v_gs_on=dp.v_gs_on, v_gs_off=dp.v_gs_off)
            res = {"part": model.part, "basis": model.basis,
                   "lib_sha256": model.lib_sha256, "cir": str(cir),
                   "conditions": dp.as_dict(), "metrics": met,
                   "elapsed_s": float(r["_elapsed_s"][0]),
                   "points": int(len(r["scale"]))}
            res_p.write_text(json.dumps(res, indent=1, default=float), encoding="utf-8")
            np.savez_compressed(npz_p, **{_key(k): v for k, v in r.items()
                                          if not k.startswith("_")})
        i_off = res["metrics"]["i_off_A"]
        err = (i_off - dp.i_target) / dp.i_target if dp.i_target else 0.0
        if not correct_current or attempt == 1 or abs(err) <= 0.015:
            return res
        if abs(err) > 0.3:
            # a re-time by more than 30 % means the measurement, not the
            # ramp, is off (e.g. a turn-off still running in the gap) —
            # never chase it with a 50x longer first pulse
            res["current_note"] = f"switched current {i_off:.1f} A vs target {dp.i_target:g} A — not re-timed"
            return res
        # re-time the first pulse on the measured ramp, keep L fixed
        L = dp.load_inductance()
        dp = DoublePulse(**{**{k: v for k, v in dp.__dict__.items()},
                            "l_load": L,
                            "t_first": dp.t_first * dp.i_target / max(i_off, 1e-9)})
    return res


def run_capacitances(model: SpiceModel, *, v_ds: float, f_hz: float = 100e3,
                     t_j: float = 25.0, workdir: Optional[Path] = None,
                     backend: Optional[Backend] = None) -> Dict[str, Any]:
    """C_iss, C_oss, C_rss [pF] at ``v_ds`` (V_GS = 0 V) — an AC check that
    the vendor capacitances (and the DDT translation carrying them) are what
    the datasheet's Table 4 says."""
    from .netlist import capacitance_netlist
    wd = Path(workdir) if workdir else default_workdir(model.part)
    w = 2.0 * np.pi * f_hz
    out: Dict[str, Any] = {"v_ds": v_ds, "f_hz": f_hz, "t_j": t_j}
    for drive in ("drain", "gate"):
        cir = wd / f"cap_{drive}_V{_tag(v_ds)}.cir"
        cir.write_text(capacitance_netlist(model, v_ds=v_ds, f_hz=f_hz, drive=drive,
                                           t_j=t_j), encoding="utf-8")
        r = run_netlist(cir, ["imag(i(vds))", "imag(i(vg))"], compat=model.compat,
                        backend=backend, timeout_s=600)
        ids, ig = float(r["imag(i(vds))"][0]), float(r["imag(i(vg))"][0])
        if drive == "drain":
            out["c_oss_pF"] = abs(ids) / w * 1e12
            out["c_rss_pF"] = abs(ig) / w * 1e12
        else:
            out["c_iss_pF"] = abs(ig) / w * 1e12
        out[f"cir_{drive}"] = str(cir)
    return out


def run_static(model: SpiceModel, *, kind: str, t_j: float, v_gs: float,
               i_max: float, i_step: float, workdir: Optional[Path] = None,
               backend: Optional[Backend] = None, v_max: float = 2.0) -> Dict[str, Any]:
    """One DC sweep (see :func:`netlist.static_netlist`): returns the current
    and both V_DS readings — Kelvin (to the source-sense pin) and at the
    power source pin — as arrays, plus the ``.cir`` path."""
    wd = Path(workdir) if workdir else default_workdir(model.part)
    name = f"static_{kind}_T{_tag(t_j)}_Vgs{_tag(v_gs)}"
    cir = wd / f"{name}.cir"
    cir.write_text(static_netlist(model, kind=kind, t_j=t_j, v_gs=v_gs,
                                  i_max=i_max, i_step=i_step), encoding="utf-8")
    vecs = ["v(dl)", "v(kl)"] if model.kelvin else ["v(dl)"]
    try:
        r = run_netlist(cir, vecs, compat=model.compat, backend=backend, timeout_s=600)
        i = r["scale"]
        v_pin = r["v(dl)"]
        v_k = r["v(dl)"] - r["v(kl)"] if model.kelvin else v_pin
        drive = "current"
    except NgspiceError:
        # current drive did not converge: sweep the drain voltage instead and
        # read the current (see netlist.static_netlist, drive="voltage")
        cir = wd / f"{name}_vdrive.cir"
        cir.write_text(static_netlist(model, kind=kind, t_j=t_j, v_gs=v_gs,
                                      i_max=i_max, i_step=i_step, drive="voltage",
                                      v_max=v_max, v_step=v_max / 400.0), encoding="utf-8")
        vecs = ["i(vds)", "v(kl)"] if model.kelvin else ["i(vds)"]
        r = run_netlist(cir, vecs, compat=model.compat, backend=backend, timeout_s=600)
        v_pin = r["scale"]
        i = -r["i(vds)"]
        v_k = v_pin - r["v(kl)"] if model.kelvin else v_pin
        drive = "voltage"
    if kind == "vsd":
        # third quadrant: report V_SD and I_SD as positive numbers (the
        # current drive already sweeps I_SD > 0; the voltage drive reads a
        # negative drain current)
        v_pin, v_k = -v_pin, -v_k
        if drive == "voltage":
            i = -i
    return {"cir": str(cir), "i_A": i, "v_pin_V": v_pin, "v_kelvin_V": v_k,
            "t_j": t_j, "v_gs": v_gs, "kind": kind, "drive": drive}


def value_at(res: Dict[str, Any], i_A: float, which: str = "v_kelvin_V") -> float:
    """``which`` (a voltage of :func:`run_static`) at current ``i_A``,
    linearly interpolated on the sweep (sorted on current)."""
    i = np.asarray(res["i_A"], float)
    v = np.asarray(res[which], float)
    o = np.argsort(i)
    return float(np.interp(i_A, i[o], v[o]))
