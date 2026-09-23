"""Netlists — the datasheet's own test circuits, as standard SPICE text.

**Double pulse** (Infineon datasheet Fig. F, "Dynamic test circuit"): a
half-bridge of two identical devices from the vendor library.  The device
under test (DUT) is the LOW side; the HIGH side is the "second device", its
gate held at V_GS(off) through its own R_G so that its BODY DIODE freewheels
the load — exactly the datasheet's "diode: body diode at V_GS = 0 V" (or
−5 V).  The load inductor L (with its parasitic C_σ) sits across the high
side; the power-loop stray inductance L_σ is split ½ in the + rail and ½ in
the − rail as the figure draws it.

```
 VDD ─ ½Lσ ─┬─────────┬──────────┐
            │         │ (VIDH)   │
           L ║ Cσ    XH drain    │
            │   gate ─ RG ─ V_GS(off) (to XH's own source/Kelvin)
    mid ────┴─────────┴──────────┤
            (VIDL)               │
           XL drain (DUT)        │
     gate ─ Lg ─ RG,on/RG,off ─ driver (V_GS(off) → V_GS(on) → …, ref. Kelvin)
           XL source ─ ½Lσ ─ 0 ──┘
```

The gate driver is ONE source with separate turn-on and turn-off external
resistors (a G-element ``I = V/R_on`` while charging, ``V/R_off`` while
discharging — the usual two-resistor + diode driver without the diode drop),
referenced to the Kelvin pin where the package has one.

Timing: first pulse long enough to ramp the load to the target current
(``t1 = L·I/V_DD``), off for ``t_gap`` (E_off is measured on this edge), then a
second pulse (E_on on this edge, E_fr in the high side's body diode).

Every netlist is plain SPICE with a ``.include`` of the vendor library by
absolute path and literal numbers (no ``.param``), so the same file opens in
KiCad's simulator (ngspice) — and in LTspice/PSpice, whose own compatibility
handles the ``VALUE=`` G-element — without edits.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .models import SpiceModel

__all__ = ["DoublePulse", "double_pulse_netlist", "static_netlist",
           "capacitance_netlist", "DP_VECTORS", "dp_vectors", "timing_for"]


def _g(x: float) -> str:
    """A SPICE number, compact and exact enough (7 significant digits)."""
    return f"{float(x):.7g}"


@dataclass
class DoublePulse:
    """One double-pulse experiment.  Units: V, A, degC, ohm, H, F, s."""
    v_dd: float
    i_target: float
    t_j: float = 25.0
    rg_on: float = 2.3               # EXTERNAL turn-on gate resistance
    rg_off: float = 2.3              # EXTERNAL turn-off gate resistance
    v_gs_on: float = 18.0
    v_gs_off: float = 0.0
    l_sigma: float = 15e-9           # total power-loop stray inductance
    l_gate: float = 2e-9             # gate-loop inductance (driver to pin)
    c_sigma: float = 20e-12          # parasitic capacitance across the load L
    # Timeline (2026-09-23, measured on IMCQ120R004M2H at 800 V / 185 A):
    # 1 us / 0.5 us / 0.4 us gives E_off and E_on within 2 % of 2 / 1 /
    # 0.6 us at the same step (and that 2 % is the switched current, which
    # moves by +0.5…2.6 % with the shorter ramp), in 136 s instead of 162 s;
    # the step
    # is NOT free — 4 ns instead of 1 ns moves E_on by -3 % and Q_fr by -9 %.
    t_first: float = 1e-6            # first-pulse length (sets L)
    t_gap: float = 0.5e-6
    t_second: float = 0.4e-6
    t_settle: float = 0.2e-6
    t_edge: float = 2e-9             # driver edge (10-90 of the source itself)
    t_max_step: float = 1e-9         # the edges are resolved by the LTE control
    reltol: float = 1e-3
    #: Load inductance; ``None`` = sized so the first pulse reaches i_target.
    l_load: Optional[float] = None
    #: End the transient just BEFORE the second turn-off.  Nothing is read
    #: from that edge; the harness retries a failed run this way (the
    #: OptiMOS model at 45 A / 75 degC aborted exactly there, 2026-09-23).
    stop_before_second_off: bool = False

    def load_inductance(self) -> float:
        if self.l_load:
            return float(self.l_load)
        return self.v_dd * self.t_first / max(self.i_target, 1e-6)

    def timeline(self) -> Dict[str, float]:
        t_on1 = self.t_settle
        t_off1 = t_on1 + self.t_first
        t_on2 = t_off1 + self.t_gap
        t_off2 = t_on2 + self.t_second
        t_stop = (t_off2 - 2.0 * self.t_max_step if self.stop_before_second_off
                  else t_off2 + 0.2e-6)
        return {"t_on1": t_on1, "t_off1": t_off1, "t_on2": t_on2,
                "t_off2": t_off2, "t_stop": t_stop}

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["l_load_H"] = self.load_inductance()
        return d


def timing_for(v_dss_V: float, r_g_max_ohm: Optional[float] = None) -> Dict[str, float]:
    """Double-pulse timing and small parasitics by voltage class.

    High-voltage parts: the :class:`DoublePulse` defaults (the Infineon
    CoolSiC test set: 2 nH gate loop, 20 pF across the load).  Low-voltage
    parts (V_DSS < 200 V): edges of a few ns, so a 0.2 ns step, a shorter
    pulse train, 1 nH gate loop, 5 pF across the load — ASSUMPTIONS for a
    PQFN test board, stated in the doc (the OptiMOS datasheet names no
    L_sigma for its switching-time circuit).  L_sigma itself is always the
    caller's (datasheet or set)."""
    if float(v_dss_V) < 200.0:
        out = dict(l_gate=1e-9, c_sigma=5e-12, t_first=0.5e-6, t_gap=0.3e-6,
                   t_second=0.3e-6, t_settle=0.1e-6, t_edge=1e-9, t_max_step=0.2e-9)
    else:
        d = DoublePulse(v_dd=1.0, i_target=1.0)
        out = dict(l_gate=d.l_gate, c_sigma=d.c_sigma, t_first=d.t_first, t_gap=d.t_gap,
                   t_second=d.t_second, t_settle=d.t_settle, t_edge=d.t_edge,
                   t_max_step=d.t_max_step)
    # A slow driver (R_G above 5 ohm) stretches t_d(off) + t_f and t_d(on) +
    # t_r roughly in proportion to the total gate resistance (datasheet
    # "t = f(R_G,ext)": ~0.45 us of t_d(off) at 20 ohm on IMCQ120R004M2H);
    # the gap and the second pulse grow with it so both edges finish inside
    # their windows.  At <= 5 ohm the timeline is untouched.
    if r_g_max_ohm is not None and r_g_max_ohm > 5.0:
        k = (float(r_g_max_ohm) + 2.3) / (5.0 + 2.3)
        out["t_gap"] *= k
        out["t_second"] *= k
    return out


#: The vectors the double-pulse runner writes, in this order.
DP_VECTORS = ["v(dl)", "v(sl)", "v(kl)", "v(gl)", "v(drv)", "i(vidl)",
              "v(dh)", "v(mid)", "v(kh)", "i(vidh)", "v(p1)"]


def dp_vectors(model: SpiceModel) -> List[str]:
    """:data:`DP_VECTORS` for this model (no Kelvin nodes without a Kelvin
    pin — ngspice refuses a ``wrdata`` of a node that does not exist)."""
    out = list(DP_VECTORS) if model.kelvin else [
        v for v in DP_VECTORS if v not in ("v(kl)", "v(kh)")]
    for j, p in enumerate(model.thermal_pins):
        if p.lower() == "tj":            # the DUT's free junction node (degC)
            out.append(f"v(thl{j})")
    return out


def _device(model: SpiceModel, name: str, d: str, g: str, s: str,
            k: Optional[str], t_j: float, *, hold_tj: bool = True) -> List[str]:
    """The instance line(s) of one device.  An L3 (thermal-pin) model gets
    its case pins (Tcase / Ttop / Tbottom) held at ``t_j`` by voltage
    sources — a thermal node's voltage is its temperature in degC — and,
    with ``hold_tj`` (static and AC runs), the junction pin as well: the
    source absorbs the self-heating power, so the device is isothermal at
    ``t_j`` exactly as an L1 model at ``.temp t_j``.

    In a double pulse (``hold_tj=False``) the junction is left to the
    model's own thermal network: holding it by a source there does not
    converge in ngspice (OptiMOS, 2026-09-23), and over a few µs its rise is
    a fraction of a kelvin (the energies are ~10 µJ into ~0.1 mJ/K of
    junction heat capacity).  Used for the OptiMOS parts, whose L1 wrapper
    does not converge at all — see their manifest."""
    if not model.thermal_pins:
        return [model.instance(name, d, g, s, kelvin=k)]
    nodes: List[str] = []
    extra: List[str] = []
    for j, p in enumerate(model.thermal_pins):
        n = f"th{name.lower()}{j}"
        nodes.append(n)
        if p.lower() != "tj" or hold_tj:
            extra.append(f"VT{name}{j} {n} 0 {_g(t_j)}")
    return [model.instance(name, d, g, s, kelvin=k, thermal=nodes)] + extra


def double_pulse_netlist(model: SpiceModel, dp: DoublePulse, *,
                         title: str = "") -> str:
    """The Fig. F circuit for ``model`` at ``dp`` — plain SPICE text."""
    tl = dp.timeline()
    L = dp.load_inductance()
    lh = dp.l_sigma / 2.0
    von, voff = dp.v_gs_on, dp.v_gs_off
    e = dp.t_edge
    pwl = [(0.0, voff), (tl["t_on1"], voff), (tl["t_on1"] + e, von),
           (tl["t_off1"], von), (tl["t_off1"] + e, voff),
           (tl["t_on2"], voff), (tl["t_on2"] + e, von),
           (tl["t_off2"], von), (tl["t_off2"] + e, voff)]
    pwl_s = " ".join(f"{_g(t)} {_g(v)}" for t, v in pwl)
    k_l = "kl" if model.kelvin else "sl"
    k_h = "kh" if model.kelvin else "mid"
    lines: List[str] = [
        f"* {title or 'Double-pulse test'} — {model.part} ({model.subckt})",
        "* Infineon datasheet Fig. F (dynamic test circuit): DUT = low side,",
        "* second device = high side, freewheeling in its own body diode.",
        f"* generated by motor_ai_sim.inverter.spice; model {model.basis}",
        f"* V_DD={_g(dp.v_dd)} V  I={_g(dp.i_target)} A  Tj={_g(dp.t_j)} C  "
        f"RG,on={_g(dp.rg_on)} ohm  RG,off={_g(dp.rg_off)} ohm  "
        f"V_GS={_g(voff)}/{_g(von)} V  Lsigma={_g(dp.l_sigma * 1e9)} nH  "
        f"Lg={_g(dp.l_gate * 1e9)} nH  Csigma={_g(dp.c_sigma * 1e12)} pF  "
        f"Lload={_g(L * 1e6)} uH",
        "* ngspice / KiCad: run with 'set ngbehavior=psa' (in .spiceinit) — the",
        "* vendor library is written in PSpice/SIMetrix syntax"
        + ("; the .include is the DDT()-translated copy (models.py)."
           if model.include_path != model.lib_path else "."),
        f"* LTspice / PSpice / SIMetrix: .include the vendor file itself: {model.lib_name}",
        f'.include "{model.include_path.as_posix()}"',
        "",
        "* DC link and the power loop (Lsigma split half/half, Fig. F)",
        f"VDD pbus 0 {_g(dp.v_dd)}",
        f"LS1 pbus p1 {_g(lh)}",
        f"LLOAD p1 mid {_g(L)}",
        f"CSIG p1 mid {_g(dp.c_sigma)}" if dp.c_sigma > 0 else "* (no Csigma)",
        "VIDH p1 dh 0",
        *_device(model, "H", "dh", "gh", "mid", "kh" if model.kelvin else None, dp.t_j,
                 hold_tj=False),
        "VIDL mid dl 0",
        *_device(model, "L", "dl", "gl", "sl", "kl" if model.kelvin else None, dp.t_j,
                 hold_tj=False),
        f"LS2 sl 0 {_g(lh)}",
        "",
        "* high side: held off through its own gate resistor",
        f"VGH ghd {k_h} {_g(voff)}",
        f"RGH ghd ghi {_g(max(dp.rg_off, 1e-3))}",
        f"LGH ghi gh {_g(dp.l_gate)}",
        "",
        "* DUT gate driver (referenced to the Kelvin source where there is one)",
        f"VDRV drv {k_l} PWL({pwl_s})",
    ]
    if abs(dp.rg_on - dp.rg_off) < 1e-12:
        lines.append(f"RGL drv gli {_g(max(dp.rg_on, 1e-3))}")
    else:
        lines.append(
            f"GRG drv gli VALUE={{IF(V(drv,gli)>0, V(drv,gli)/{_g(max(dp.rg_on, 1e-3))}, "
            f"V(drv,gli)/{_g(max(dp.rg_off, 1e-3))})}}")
        lines.append("RGLEAK drv gli 1e6")
    lines += [
        f"LGL gli gl {_g(dp.l_gate)}",
        "",
        # keep only what the extraction reads: without it ngspice holds every
        # internal node of both vendor subcircuits in memory (a run was
        # refused "memory required > memory available" on the owner's busy
        # workstation, 2026-09-23)
        f".save {' '.join(dp_vectors(model))}",
        f".temp {_g(dp.t_j)}",
        f".options reltol={_g(dp.reltol)} abstol=1e-9 vntol=1e-5 itl4=200",
        f".tran {_g(dp.t_max_step)} {_g(tl['t_stop'])} 0 {_g(dp.t_max_step)}",
        ".end",
    ]
    return "\n".join(lines) + "\n"


def capacitance_netlist(model: SpiceModel, *, v_ds: float, f_hz: float = 100e3,
                        drive: str = "drain", t_j: float = 25.0) -> str:
    """Small-signal capacitances at the datasheet's condition (Table 4:
    V_GS = 0 V, f = 100 kHz, V_DS = 800 V on the 1200 V family).

    ``drive="drain"`` — AC on the drain, gate AC-grounded to the (Kelvin)
    source: ``Im(I_VDS)/ω = C_oss``, ``Im(I_VG)/ω = C_rss``.
    ``drive="gate"`` — AC on the gate, drain AC-grounded:
    ``Im(I_VG)/ω = C_iss``.
    """
    k = "kl" if model.kelvin else "sl"
    ac_d = " AC 1" if drive == "drain" else ""
    ac_g = " AC 1" if drive == "gate" else ""
    lines = [
        f"* Capacitances ({drive} drive) — {model.part} ({model.subckt})",
        f"* generated by motor_ai_sim.inverter.spice; model {model.basis}",
        f'.include "{model.include_path.as_posix()}"',
        *_device(model, "L", "dl", "gl", "sl", "kl" if model.kelvin else None, t_j),
        "VSRC sl 0 0",
        f"VDS dl 0 DC {_g(v_ds)}{ac_d}",
        f"VG gl {k} DC 0{ac_g}",
        "RKS kl 0 1e9" if model.kelvin else "* (no Kelvin pin)",
        f".temp {_g(t_j)}",
        f".ac lin 1 {_g(f_hz)} {_g(f_hz)}",
        ".end",
    ]
    return "\n".join(lines) + "\n"


def static_netlist(model: SpiceModel, *, kind: str, t_j: float,
                   v_gs: float, i_max: float, i_step: float,
                   title: str = "", drive: str = "current",
                   v_max: float = 2.0, v_step: float = 0.005) -> str:
    """DC sweep of the device current at one T_j.

    ``kind="rds"`` — forward conduction, gate at ``v_gs`` (the datasheet's
    V_GS(on)); ``v(dl)-v(kl)`` over the sweep is V_DS measured Kelvin (to the
    source-sense pin, where the package has one) and ``v(dl)-v(sl)`` at the
    power source pin.
    ``kind="vsd"`` — third quadrant with the gate at ``v_gs`` (the datasheet's
    V_GS = 0 V or −5 V): reverse current, body diode.
    """
    if kind not in ("rds", "vsd"):
        raise ValueError("kind must be 'rds' or 'vsd'")
    k = "kl" if model.kelvin else "sl"
    if drive == "voltage":
        # ``drive="voltage"`` — the drain VOLTAGE is swept instead (0…v_max
        # forward, 0…-v_max third quadrant) and the current read from the
        # source: the OptiMOS models' DC operating point does not converge
        # under a pure current drive in ngspice ("timestep too small" at
        # the gate node), a voltage drive does.  Same circuit otherwise.
        vm = v_max if kind == "rds" else -v_max
        vs = v_step if kind == "rds" else -v_step
        return "\n".join([
            f"* {title or 'Static ' + kind} (voltage drive) — {model.part} ({model.subckt})",
            f"* generated by motor_ai_sim.inverter.spice; model {model.basis}",
            "* ngspice: 'set ngbehavior=psa' (vendor library in PSpice syntax)",
            f'.include "{model.include_path.as_posix()}"',
            *_device(model, "L", "dl", "gl", "sl", "kl" if model.kelvin else None, t_j),
            "VSRC sl 0 0",
            f"VG gl {k} {_g(v_gs)}",
            "RKS kl 0 1e9" if model.kelvin else "* (no Kelvin pin)",
            "VDS dl 0 DC 0",
            f".temp {_g(t_j)}",
            f".dc VDS 0 {_g(vm)} {_g(vs)}",
            ".end"]) + "\n"
    sign = "0 dl" if kind == "rds" else "dl 0"
    lines = [
        f"* {title or 'Static ' + kind} — {model.part} ({model.subckt})",
        f"* generated by motor_ai_sim.inverter.spice; model {model.basis}",
        "* ngspice: 'set ngbehavior=psa' (vendor library in PSpice syntax)",
        f'.include "{model.include_path.as_posix()}"',
        *_device(model, "L", "dl", "gl", "sl", "kl" if model.kelvin else None, t_j),
        "VSRC sl 0 0",
        f"VG gl {k} {_g(v_gs)}",
        "RKS kl 0 1e9" if model.kelvin else "* (no Kelvin pin)",
        f"ID {sign} 0",
        f".temp {_g(t_j)}",
        f".dc ID {_g(i_step)} {_g(i_max)} {_g(i_step)}",
        ".end",
    ]
    return "\n".join(lines) + "\n"
