"""MEASURED PWM block of a motor passport.

The 2-D passport (``passport.py``) characterises the machine under a clean
sinusoidal current: torque over a current sweep, iron/magnet loss over an
I x rpm grid, bench Ld/Lq, 3-D end effect.  A real machine is not fed a sine —
it is fed pulses, and the carrier ripple adds watts the sine run cannot see
(magnet eddy, iron, AC copper) and multiplies the torque ripple.

This module measures THOSE DELTAS on a small grid so the Configure tab can add
them analytically, the same way it already adds the 2-D losses:

    for each operating point:  a cheap sine SEED solve (the source of the
                               V1 / delta the modulator must apply to
                               reproduce that very point)
                               then per f_switch:  the PWM solve
                                                   + a sine BASELINE solved at
                                                     the PWM run's own resolved
                                                     step count

    delta = PWM - sine, at the SAME point, the SAME mesh, the SAME time step.

WHY THE BASELINE IS RESOLUTION-MATCHED, AND NOT REUSED FROM THE LOSS GRID.
The grid is solved at a coarser mesh and a coarser step count, and a delta
between two runs that do not share a mesh is a mesh study wearing a PWM label.
The step count matters just as much: MEASURED on the CIANO14 40 mm at its rated
point, the sine run alone moves from Pfe 9.20 W / Pmag 4.00 W at 40 steps per
period to 10.10 W / 3.80 W at 240 — so a 40-step baseline against a 240-step
PWM run reports an iron delta of 1.80 W where the honest one is 0.90 W.  Half
the "switching loss" was the time step.  The PWM run goes first and the
baseline is then solved at the count the solver actually ran (the sliding-band
snap can move a request), so the two share it exactly.

WHAT MOVES THE DELTAS.  The carrier ripple current is

    I_ripple ~ V_bus / (f_switch * L_phase),   L_phase ~ N^2 * L_stack * nS^2

so at a fixed operating point the ONLY knob that moves it is the carrier (and
the bus).  That is why the frequencies are the measurement axis: two carriers
at the rated point give the exponent of each delta against the ripple current,
and the rpm axis (three grid speeds) gives the frequency dependence that the
ripple-current law does not contain.  Nothing here is assumed to be quadratic —
``fit`` reports what was measured, and how.

I_ripple is measured, not modelled:

    I_ripple_rms = sqrt( I_phase_rms_solved^2 - I1_winding_rms^2 )

i.e. everything in the solved phase current that is not the fundamental.  (The
summary's THD_I is truncated at the 25th harmonic and therefore misses the
sidebands of a low pulse ratio — it is reported alongside, never used for the
fit.)

BOTH TERMS ARE WINDING (PHASE) CURRENTS, and that is not free.  The summary's
``I1_A`` is the amplitude of ONE PARALLEL BRANCH (routes/simulation.py: "branch
amplitude, as I_A series"), while ``I_phase_rms_solved_A`` is the rms of the
whole winding (branch rms x n_parallel_eff).  Subtracting one from the other as
they stand mixes units and, on every machine with parallel paths, reports most
of the FUNDAMENTAL as ripple: measured on the O200 L155 at 2 parallel paths,
the mixed formula gives 384 A where the honest ripple is 88.7 A.  So the
fundamental is lifted to the winding first,

    I1_winding_rms = I1_A / sqrt2 * n_parallel_eff

— the same expression ``postproc.fundamental_current`` uses for
``harm_ref.I1_phase_rms_A``, which is cross-checked against it here whenever the
run carried a harmonic reference (measured agreement: 0.001 %).

EVERY FITTED EXPONENT HANGS ON THIS ABSCISSA.  ``n_mag``, ``n_fe``, ``n_cu``,
``n_ripple`` and ``n_dc_ripple`` are log-log slopes against the ripple current,
so a PWM block measured before this fix on a machine with n_parallel_eff > 1 is
fitted to the wrong x-axis and has to be re-measured, not re-read.

COST.  ``quick`` (the default) is 3 seeds + 4 PWM + up to 4 matched baselines:
the rated point at two carriers, its two rpm neighbours at the reference
carrier.  Two carriers at one point is the minimum that makes the exponent
MEASURED instead of assumed — a single carrier would leave the tuner's scaling
law guessing, which is the one thing a passport must not do.  ``full`` adds the
0.5 x current row and the second carrier at every point.  Baselines are cached
per (rpm, current, step count), so carriers that resolve to the same step count
pay for one.

FIDELITY.  Under 4 time steps per switching period the solver refuses the run
(the ripple is at its Nyquist edge); 4..8 is a declared COARSE pass whose known
bias is measured and recorded here with the numbers, not hidden.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

# ── Controller classes ───────────────────────────────────────────────────────
# The same lists the Simulation tab offers (web/src/components/simulation/
# SimulationPanel.tsx FSW_GROUPS) — the settings an engineer finds in a real
# drive's own menu, not arbitrary kHz.  Picked by the machine's pack voltage,
# because that is what decides the power stage.
CONTROLLER_CLASSES: List[Dict[str, Any]] = [
    {"id": "lv_esc", "label": "LV MOSFET ESC · < 100 V",
     "v_max": 100.0, "f_sw_Hz": [24000, 48000, 64000, 96000]},
    {"id": "sic", "label": "SiC MOSFET · 400–800 V",
     "v_max": 800.0, "f_sw_Hz": [16000, 24000, 32000, 48000]},
    {"id": "igbt", "label": "IGBT · high voltage",
     "v_max": float("inf"), "f_sw_Hz": [4000, 8000, 12000, 16000]},
]

# Measured bias of a COARSE pass (4..8 time steps per switching period) against
# a fully resolved 16+/carrier run.  Solver calibration, 40 mm at 16.7 kHz,
# 4.4/carrier vs 21.8 — the same table the solver's own guard logs.  Quoted on
# the passport so a coarse number is never read as a resolved one.
COARSE_BIAS = {
    "torque_pct": -0.1, "torque_ripple_pct": -15.0,
    "copper_pct": -4.0, "iron_pct": -20.0,
    "source": ("solver guard calibration, 40 mm at 16.7 kHz: 4.4 samples per "
               "carrier against 21.8 — the coarse pass reads LOW on every "
               "ripple-driven number, never high"),
}


def controller_class(v_nom: Optional[float]) -> Dict[str, Any]:
    """The power stage a pack of this voltage is switched by.

    No battery (or no usable nominal) → the LV class, because a machine with no
    declared pack in this project is a small one; the choice is REPORTED on the
    passport either way, so a wrong guess is visible rather than silent.
    """
    v = float(v_nom or 0.0)
    for c in CONTROLLER_CLASSES:
        if v <= c["v_max"]:
            return c
    return CONTROLLER_CLASSES[-1]


def _largest_divisor_leq(n: int, cap: int) -> int:
    """Largest divisor of n that is <= cap (the solver's coarse-settle rule)."""
    n = max(1, int(n))
    if n <= cap:
        return n
    best = 1
    for d in range(1, int(math.isqrt(n)) + 1):
        if n % d:
            continue
        if d <= cap:
            best = max(best, d)
        q = n // d
        if q <= cap:
            best = max(best, q)
    return best


def pwm_steps(carriers: int, per_carrier: float, *,
              min_coarse_settle: int = 32, cap: int = 1400) -> int:
    """Steps per electrical period for a PWM solve at this pulse ratio.

    Two constraints, both measured rather than aesthetic:

    * at least ``per_carrier`` time steps per switching period — under 4 the
      solver refuses the run outright;
    * the count must have a DIVISOR near 40, because the mixed-resolution
      settle marches the fundamental at the largest divisor <= 40 and a prime-ish
      count collapses it to 4 (measured on the 85 mm: 480 gives a 40-step
      settle, 412 gives 4 — same wall time, a settle that never settles).

    So the smallest count at or above the requirement whose coarse settle is
    still worth the name.  Returns 0 when nothing under ``cap`` qualifies —
    the caller then records the point as skipped instead of quoting a run it
    could not afford.
    """
    want = max(4, int(math.ceil(float(per_carrier) * max(1, int(carriers)))))
    for n in range(want, min(int(cap), want + 400) + 1):
        if _largest_divisor_leq(n, 40) >= int(min_coarse_settle):
            return n
    return 0


def _n_parallel_eff(summary: Dict[str, Any]) -> float:
    """Parallel paths the WINDING current of this run is split between.

    ``n_parallel_eff`` (paths x strands in hand) is the divider the solver's
    own branch series carry; ``n_parallel`` is the fallback for a summary
    written before the strands existed.  Never below 1 — a missing key means
    one path, not no path.
    """
    for key in ("n_parallel_eff", "n_parallel"):
        try:
            v = float(summary.get(key) or 0.0)
        except (TypeError, ValueError):
            continue
        if v >= 1.0:
            return v
    return 1.0


def _I1_winding_rms_A(summary: Dict[str, Any], *,
                      result: Optional[Dict[str, Any]] = None
                      ) -> Optional[float]:
    """Fundamental of the WINDING (phase) current [A rms].

    ``I1_A`` is the fundamental amplitude of ONE PARALLEL BRANCH, so the
    winding's fundamental is ``I1_A / sqrt2 * n_parallel_eff`` — exactly what
    ``postproc.fundamental_current`` reports as ``harm_ref.I1_phase_rms_A``.
    When the run carried a harmonic reference that number is used as the
    cross-check (they agree to 0.001 % on the machines measured so far) and a
    disagreement is LOGGED rather than silently averaged away: the two can only
    differ if the branch/winding convention moved under us, and that is the very
    bug this function exists to prevent.
    """
    try:
        i1_branch_pk = float(summary.get("I1_A") or 0.0)
    except (TypeError, ValueError):
        return None
    npar = _n_parallel_eff(summary)
    i1 = (i1_branch_pk / math.sqrt(2.0)) * npar if i1_branch_pk > 0.0 else 0.0
    href = ((result or {}).get("harm_ref") if isinstance(result, dict) else None)
    if not isinstance(href, dict):
        href = summary.get("harm_ref")
    try:
        i1_ref = float((href or {}).get("I1_phase_rms_A") or 0.0
                       if isinstance(href, dict) else 0.0)
    except (TypeError, ValueError):
        i1_ref = 0.0
    if i1_ref > 0.0:
        if i1 <= 0.0:
            return i1_ref
        if abs(i1_ref - i1) > 0.01 * i1_ref:
            log.warning(
                "passport PWM: winding fundamental disagrees with the run's own "
                "harmonic reference — I1_A/sqrt2 x n_parallel_eff(%.3g) = "
                "%.3f A rms against harm_ref.I1_phase_rms_A = %.3f A rms "
                "(%.2f %%); using the reference", npar, i1, i1_ref,
                100.0 * (i1 - i1_ref) / i1_ref)
            return i1_ref
    return i1 if i1 > 0.0 else None


def _ripple_current(summary: Dict[str, Any], *,
                    result: Optional[Dict[str, Any]] = None
                    ) -> Dict[str, Any]:
    """Non-fundamental WINDING current [A rms] of a solved run, and its terms.

    Everything the solved waveform holds beyond its own fundamental — the
    carrier ripple on a voltage-driven run.  A CURRENT-driven run has none by
    construction (the source imposes a sinusoid, which is why THD_I ~ 0 is the
    drive's own self-check), and the solver leaves ``I_phase_rms_solved_A``
    unset there because there was nothing to solve for; that case returns
    exactly 0.0, not None — "the baseline has no ripple" is a measurement, not
    a missing one.

    Both terms are winding currents (see the module docstring): the rms is the
    whole winding's, so the fundamental is lifted out of the branch series with
    ``n_parallel_eff`` before the quadrature difference.  The difference is
    CLAMPED at zero and the clamp is FLAGGED — a negative radicand means the
    two numbers no longer describe the same current, which is a defect to see
    rather than a small number to quote.
    """
    out: Dict[str, Any] = {
        "I_nonfund_A": None, "I1_winding_rms_A": None, "I_rms_A": None,
        "n_parallel_eff": None, "clamped": False, "note": None,
    }
    try:
        i_rms = float(summary.get("I_phase_rms_solved_A") or 0.0)
    except (TypeError, ValueError):
        return out
    if str(summary.get("drive") or "") == "current":
        out["I_nonfund_A"] = 0.0
        return out
    i1 = _I1_winding_rms_A(summary, result=result)
    if i_rms <= 0.0 or not i1 or i1 <= 0.0:
        return out
    out["I_rms_A"] = i_rms
    out["I1_winding_rms_A"] = i1
    out["n_parallel_eff"] = _n_parallel_eff(summary)
    d = i_rms * i_rms - i1 * i1
    if d < 0.0:
        out["clamped"] = True
        out["note"] = (
            "the solved rms (%.3f A) sits BELOW its own fundamental (%.3f A): "
            "the ripple is clamped to 0 and this point must not be fitted "
            "against" % (i_rms, i1))
        log.warning("passport PWM: %s", out["note"])
        d = 0.0
    out["I_nonfund_A"] = math.sqrt(d)
    return out


def _ripple_current_A(summary: Dict[str, Any], *,
                      result: Optional[Dict[str, Any]] = None
                      ) -> Optional[float]:
    """The ripple current alone — see :func:`_ripple_current` for the terms."""
    return _ripple_current(summary, result=result)["I_nonfund_A"]


def _losses(summary: Dict[str, Any]) -> Dict[str, float]:
    """The three loss terms a PWM run moves, from a finished summary."""
    def f(k: str) -> float:
        v = summary.get(k)
        try:
            return float(v or 0.0)
        except (TypeError, ValueError):
            return 0.0
    return {"P_cu_W": f("P_stranded_W"), "P_fe_W": f("P_core_W"),
            "P_mag_W": f("P_solid_W")}


def _fit_exponent(x0: float, y0: float, x1: float, y1: float) -> Optional[float]:
    """Log-log slope through two measured points, or None when it cannot be
    formed (a non-positive delta, or two carriers that produced the same ripple
    current).  None is an honest answer — the caller then falls back to the
    physical exponent and SAYS it fell back."""
    if not (x0 > 0 and x1 > 0 and y0 > 0 and y1 > 0):
        return None
    if abs(math.log(x1 / x0)) < 1e-3:
        return None
    return math.log(y1 / y0) / math.log(x1 / x0)


def measure_pwm(
    *,
    solve: Callable[..., Dict[str, Any]],
    I0_A: float,
    rpm0: float,
    rpms: Sequence[float],
    pole_pairs: int,
    v_bus_V: float,
    v_nom_V: Optional[float] = None,
    fidelity: str = "quick",
    sine_steps: int = 40,
    f_sw_Hz: Optional[Sequence[float]] = None,
    skipped_out: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """Measure the PWM deltas of one machine.  Never raises.

    ``solve(**kw)`` must run ONE transient of the machine under test and return
    the route's result dict; the caller bakes in geometry / materials /
    connection / mesh / mode, exactly as the 2-D passport does.  Everything
    this function passes is the operating point and the excitation.

    Returns the ``pwm`` block, or None when the machine cannot be measured
    (no bus, no speed, every point unaffordable) — and a None is a passport
    that simply has no PWM block, never a passport that lost its 2-D data.

    ``skipped_out``, when given, is filled with the machine-readable skip
    records (each carries a ``code``) even in the None case — so "this machine
    has no PWM block" can be printed WITH its reason instead of as a silence.
    """
    try:
        return _measure_pwm(
            solve=solve, I0_A=float(I0_A), rpm0=float(rpm0), rpms=list(rpms),
            pole_pairs=int(pole_pairs), v_bus_V=float(v_bus_V or 0.0),
            v_nom_V=v_nom_V, fidelity=str(fidelity or "quick"),
            sine_steps=int(sine_steps), f_sw_Hz=f_sw_Hz,
            skipped_out=skipped_out)
    except Exception:      # noqa: BLE001 — the 2-D passport must survive this
        log.exception("passport PWM block failed — the passport ships without "
                      "it (Configure shows the sine numbers, PWM toggle off)")
        return None


def _measure_pwm(*, solve, I0_A, rpm0, rpms, pole_pairs, v_bus_V, v_nom_V,
                 fidelity, sine_steps, f_sw_Hz,
                 skipped_out=None) -> Optional[Dict[str, Any]]:
    from motor_ai_sim.simulation.pwm import carriers_per_period
    try:
        from motor_ai_sim.simulation.pwm import (MAX_MODULATION_INDEX
                                                 as _M_MAX)
    except ImportError:      # pragma: no cover — the source defines it
        _M_MAX = 1.15

    if not (v_bus_V > 0.0):
        log.warning("passport PWM: no DC bus voltage for this machine (no "
                    "battery on the configuration) — no PWM block")
        if skipped_out is not None:
            skipped_out.append({"code": "no_bus",
                                "reason": ("no DC bus voltage on this "
                                           "configuration (no battery)")})
        return None
    if not (rpm0 > 0.0 and pole_pairs > 0):
        log.warning("passport PWM: needs a non-zero rated speed; got %r rpm",
                    rpm0)
        if skipped_out is not None:
            skipped_out.append({"code": "no_speed", "rpm": rpm0,
                                "reason": ("the passport has no non-zero rated "
                                           "speed to switch at")})
        return None

    cls = controller_class(v_nom_V if v_nom_V is not None else v_bus_V)
    # The class's SECOND and FOURTH entries: the second is what a drive of that
    # class is normally run at, the fourth is its top setting — far enough
    # apart (2x on every class) that the exponent between them is a slope and
    # not two readings of the same point.
    fs = list(f_sw_Hz) if f_sw_Hz else [cls["f_sw_Hz"][1], cls["f_sw_Hz"][3]]
    full = str(fidelity).strip().lower() == "full"

    # ── the operating grid ───────────────────────────────────────────────
    # The rated speed plus its two neighbours ON THE PASSPORT'S OWN rpm grid,
    # so the tuner interpolates PWM deltas over exactly the axis it already
    # interpolates the 2-D losses over.
    grid = sorted({float(r) for r in rpms if float(r) > 0.0})
    if rpm0 not in grid:
        grid.append(float(rpm0))
        grid = sorted(set(grid))
    i0 = min(range(len(grid)), key=lambda i: abs(grid[i] - rpm0))
    lo = max(0, min(i0 - 1, len(grid) - 3))
    speeds = grid[lo:lo + 3] or [float(rpm0)]
    currents = [float(I0_A)] + ([0.5 * float(I0_A)] if full else [])

    points: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = (skipped_out if skipped_out is not None
                                     else [])
    # Matched baselines are cached per (rpm, current, resolved step count):
    # two carriers whose requests land on the same ring divisor share one.
    base_cache: Dict[Tuple[float, float, int], Dict[str, Any]] = {}

    def _baseline(rpm: float, I: float, nspp: int,
                  seed: Dict[str, Any]) -> Dict[str, Any]:
        """The sine run this PWM point is differenced against, at the PWM
        run's OWN step count.  The seed is reused when it already ran at that
        count (nothing to gain from solving it twice)."""
        key = (round(rpm, 3), round(I, 4), int(nspp))
        if key in base_cache:
            return base_cache[key]
        if int(seed.get("n_steps_per_period") or 0) == int(nspp):
            base_cache[key] = seed
            return seed
        r = solve(drive="current", I_phase_rms=I, rpm=rpm,
                  n_steps_per_period=int(nspp))
        s = r.get("summary") or {}
        base_cache[key] = s
        return s

    for I in currents:
        for rpm in speeds:
            rated = (abs(rpm - rpm0) < 1e-6 and abs(I - I0_A) < 1e-6)
            # both carriers at the rated point (that pair IS the fit); the
            # reference carrier everywhere else — unless `full`, which measures
            # the whole grid at both.
            here = fs if (rated or full) else fs[:1]
            f_elec = rpm * pole_pairs / 60.0
            # ── SEED: what fundamental the modulator has to apply here ────
            # Cheap (coarse), because only V1 and its angle are read from it.
            # The loss baseline is a different, resolution-matched solve below.
            try:
                sres = solve(drive="current", I_phase_rms=I, rpm=rpm,
                             n_steps_per_period=int(sine_steps))
            except Exception as e:      # noqa: BLE001
                skipped.append({"code": "seed_failed",
                                "rpm": rpm, "I_A": round(I, 3),
                                "reason": "sine seed failed: %s" % (e,)})
                continue
            seed = sres.get("summary") or {}
            base_cache[(round(rpm, 3), round(I, 4),
                        int(seed.get("n_steps_per_period") or sine_steps))] = seed
            v1 = float(seed.get("V1_seed_peak_V") or 0.0)
            dl = float(seed.get("V1_seed_delta_deg") or 0.0)
            if v1 <= 0.0:
                skipped.append({"code": "no_v1_seed",
                                "rpm": rpm, "I_A": round(I, 3),
                                "reason": "the sine run carries no V1 seed — "
                                          "nothing to drive the modulator with"})
                continue
            # ── THE MODULATION GATE, IN THE RIGHT UNITS ──────────────────
            # m = 2·V_phase_peak/V_bus is a statement about the inverter's POLE
            # voltage, referred to the DC mid-point — a PER-PHASE quantity.  The
            # V₁ seed is the amplitude of what is applied across ONE WINDING,
            # and in delta that winding IS the line-to-line pair: it is √3 times
            # the per-phase equivalent.  Feeding it in raw refuses every healthy
            # delta point (measured on the Ø200 L155: m read 1.525 where the
            # real bridge sits at 0.880) and left every delta machine with an
            # empty PWM block, which is what the datasheet printed as "No PWM
            # block on this passport".  The bus is always the REAL one — the
            # star-equivalent model bus (√3·V_dc) is the solver's internal
            # device and must never reach a message or a stored number.
            _sd = str(seed.get("star_delta") or "star").strip().lower()
            is_delta = _sd.startswith("d")
            v1_ph = v1 / math.sqrt(3.0) if is_delta else v1
            m_idx = 2.0 * v1_ph / v_bus_V
            if m_idx > _M_MAX:
                skipped.append({
                    "code": "overmodulation",
                    "rpm": rpm, "I_A": round(I, 3),
                    "star_delta": ("delta" if is_delta else "star"),
                    "modulation_index": round(m_idx, 4),
                    "modulation_limit": float(_M_MAX),
                    "v_bus_V": round(float(v_bus_V), 2),
                    "V1_peak_V": round(v1, 3),
                    "V1_phase_peak_V": round(v1_ph, 3),
                    "v_bus_min_V": round(2.0 * v1_ph / _M_MAX, 1),
                    "reason": ("this point needs m = %.3f on the real %.1f V "
                               "bus (%.1f V per-phase fundamental%s) — past "
                               "the %.2f linear-modulation limit; the inverter "
                               "cannot make it, so there is no PWM answer to "
                               "measure.  It needs a bus of at least %.0f V"
                               % (m_idx, float(v_bus_V), v1_ph,
                                  (", = V₁ %.1f V across the delta winding / "
                                   "√3" % v1) if is_delta else "",
                                  float(_M_MAX),
                                  math.ceil(2.0 * v1_ph / _M_MAX)))})
                continue
            for f_sw in here:
                nc = carriers_per_period(f_sw, f_elec)
                # coarse first (4.5 samples/carrier); `full` resolves it (16)
                # and falls back to coarse when the step count is unaffordable.
                steps = pwm_steps(nc, 16.0 if full else 4.5)
                if not steps:
                    steps = pwm_steps(nc, 4.5)
                if not steps:
                    skipped.append({
                        "code": "step_budget",
                        "rpm": rpm, "I_A": round(I, 3), "f_sw_Hz": f_sw,
                        "carriers_per_period": nc,
                        "reason": ("%d carriers per electrical period needs "
                                   "more steps than the passport budget "
                                   "allows — measure this carrier on the "
                                   "Simulation tab instead" % nc)})
                    continue
                try:
                    pres = solve(drive="pwm_voltage", I_phase_rms=I, rpm=rpm,
                                 n_steps_per_period=int(steps),
                                 v_phase_peak=v1, v_delta_deg=dl,
                                 v_bus=float(v_bus_V), f_switch=float(f_sw),
                                 harm_ref=False)
                except Exception as e:      # noqa: BLE001
                    skipped.append({"code": "pwm_solve_failed",
                                    "rpm": rpm, "I_A": round(I, 3),
                                    "f_sw_Hz": f_sw,
                                    "n_steps_per_period": int(steps),
                                    "reason": "PWM solve failed: %s" % (e,)})
                    continue
                ps = pres.get("summary") or {}
                pw = pres.get("pwm") or {}
                dcl = (pw.get("dc_link") or {}) if isinstance(pw, dict) else {}
                pl = _losses(ps)
                nspp_eff = int(ps.get("n_steps_per_period") or steps)
                per_c_eff = nspp_eff / max(1, nc)
                # ── the RESOLUTION-MATCHED baseline ───────────────────────
                # Solved after the PWM run, at the step count the solver
                # actually ran (the sliding-band snap moves requests), so the
                # difference below is switching physics and not time-step
                # physics.  See the module docstring for the measurement that
                # made this non-negotiable.
                try:
                    ss = _baseline(rpm, I, nspp_eff, seed)
                except Exception as e:      # noqa: BLE001
                    skipped.append({"code": "baseline_failed",
                                    "rpm": rpm, "I_A": round(I, 3),
                                    "f_sw_Hz": f_sw,
                                    "n_steps_per_period": nspp_eff,
                                    "reason": ("matched sine baseline failed: "
                                               "%s" % (e,))})
                    continue
                sl = _losses(ss)
                s_rc = _ripple_current(ss)
                s_rip = s_rc["I_nonfund_A"]
                base = {
                    "T_Nm": round(abs(float(ss.get("T_em_avg_Nm") or 0.0)), 4),
                    "ripple_pct": (round(float(ss["T_ripple_pct"]), 2)
                                   if ss.get("T_ripple_pct") is not None
                                   else None),
                    "P_cu_W": round(sl["P_cu_W"], 2),
                    "P_fe_W": round(sl["P_fe_W"], 2),
                    "P_mag_W": round(sl["P_mag_W"], 2),
                    "I_nonfund_A": (round(s_rip, 4)
                                    if s_rip is not None else None),
                    "n_steps_per_period": ss.get("n_steps_per_period"),
                    "resolution_matched": (int(ss.get("n_steps_per_period")
                                               or 0) == nspp_eff),
                }
                p_rc = _ripple_current(ps, result=pres)
                p_rip = p_rc["I_nonfund_A"]
                # The switching ripple ONLY: the sine run's own non-fundamental
                # content (slotting, EMF harmonics) is in both runs and is not
                # switching physics, so it comes off in quadrature.
                i_sw = None
                if p_rip is not None:
                    i_sw = math.sqrt(max(0.0, p_rip ** 2
                                         - ((s_rip or 0.0) ** 2)))
                points.append({
                    "rpm": round(rpm, 1), "I_A": round(I, 3),
                    "rated": bool(rated),
                    "f_sw_Hz": float(f_sw),
                    "f_sw_eff_Hz": pw.get("f_switch_eff_Hz"),
                    "f_elec_Hz": round(f_elec, 3),
                    "carriers_per_period": int(nc),
                    "n_steps_per_period": int(nspp_eff),
                    "samples_per_carrier": round(per_c_eff, 2),
                    "resolution": ("resolved" if per_c_eff >= 16.0
                                   else "partial" if per_c_eff >= 8.0
                                   else "coarse"),
                    "V1_peak_V": round(v1, 3),
                    "V1_delta_deg": round(dl, 3),
                    # WHAT V₁ MEANS HERE: the amplitude applied across one
                    # WINDING (= line-to-line in delta), and beside it the
                    # per-phase equivalent the modulation index is formed from.
                    # Equal in star; √3 apart in delta.
                    "star_delta": ("delta" if is_delta else "star"),
                    "V1_phase_peak_V": round(v1_ph, 3),
                    "v_bus_V": round(float(v_bus_V), 2),
                    # The solver's own m, with the seed's as the fallback (and
                    # always as the cross-check): they must agree, because both
                    # are the REAL bridge's modulation index.
                    "modulation_index": (pw.get("modulation_index")
                                         if pw.get("modulation_index")
                                         is not None else round(m_idx, 4)),
                    "modulation_index_seed": round(m_idx, 4),
                    "sine": base,
                    "pwm": {
                        "T_Nm": round(abs(float(ps.get("T_em_avg_Nm") or 0.0)), 4),
                        "ripple_pct": (round(float(ps["T_ripple_pct"]), 2)
                                       if ps.get("T_ripple_pct") is not None
                                       else None),
                        "P_cu_W": round(pl["P_cu_W"], 2),
                        "P_fe_W": round(pl["P_fe_W"], 2),
                        "P_mag_W": round(pl["P_mag_W"], 2),
                        "I_nonfund_A": (round(p_rip, 4)
                                        if p_rip is not None else None),
                        # The two terms the number above is the difference of,
                        # both at the WINDING, so the abscissa of every fitted
                        # exponent can be re-derived from the stored passport
                        # instead of being taken on faith.
                        "I_rms_A": (round(p_rc["I_rms_A"], 4)
                                    if p_rc["I_rms_A"] is not None else None),
                        "I1_winding_rms_A": (
                            round(p_rc["I1_winding_rms_A"], 4)
                            if p_rc["I1_winding_rms_A"] is not None else None),
                        "n_parallel_eff": p_rc["n_parallel_eff"],
                        "THD_I_pct": ps.get("THD_I_pct"),
                        "THD_I_note": ("truncated at the 25th harmonic — it "
                                       "misses the carrier sidebands and "
                                       "under-reads the ripple; I_nonfund_A is "
                                       "the rms-difference number to quote"),
                    },
                    # ── THE DELTAS — what the tuner adds ──────────────────
                    "dP_mag_W": round(pl["P_mag_W"] - sl["P_mag_W"], 2),
                    "dP_fe_W": round(pl["P_fe_W"] - sl["P_fe_W"], 2),
                    "dP_cu_ac_W": round(pl["P_cu_W"] - sl["P_cu_W"], 2),
                    "dT_pct": (round(100.0 * (abs(float(ps.get("T_em_avg_Nm") or 0.0))
                                              - base["T_Nm"])
                                     / max(1e-9, base["T_Nm"]), 3)),
                    "ripple_sine_pct": base["ripple_pct"],
                    "ripple_pwm_pct": (round(float(ps["T_ripple_pct"]), 2)
                                       if ps.get("T_ripple_pct") is not None
                                       else None),
                    # The ripple CURRENT the deltas are fitted against — WINDING
                    # rms, fundamental lifted out of the branch series with
                    # n_parallel_eff (see the module docstring).
                    "I_ripple_A": (round(i_sw, 4) if i_sw is not None else None),
                    # True = the quadrature difference went negative and was
                    # clamped; the point is kept (its LOSSES are still a
                    # measurement) but it must not carry a fitted exponent.
                    "I_ripple_clamped": bool(p_rc["clamped"]
                                             or s_rc["clamped"]),
                    **({} if not (p_rc["note"] or s_rc["note"]) else {
                        "I_ripple_note": (p_rc["note"] or s_rc["note"])}),
                    # DC link — what the pack/capacitor actually sees.
                    "I_dc_mean_A": dcl.get("I_dc_mean_A"),
                    "I_dc_rms_A": dcl.get("I_dc_rms_A"),
                    "I_dc_ripple_pp_A": dcl.get("I_dc_ripple_pp_A"),
                })

    if not points:
        log.warning("passport PWM: no point could be measured (%d skipped)",
                    len(skipped))
        return None

    block: Dict[str, Any] = {
        "fidelity": ("full" if full else "quick"),
        "controller_class": cls["label"],
        "controller_class_id": cls["id"],
        "f_sw_Hz": [float(x) for x in fs],
        # Every carrier this power stage offers — the tuner's picker shows all
        # of them and FLAGS the ones outside the measured pair, rather than
        # pretending the machine was only ever meant to run at two.
        "f_sw_class_Hz": [float(x) for x in cls["f_sw_Hz"]],
        "f_sw_ref_Hz": float(fs[0]),
        # THE REAL BUS, always.  A delta machine may be SOLVED on the
        # star-equivalent circuit (model bus √3·V_dc); that substitution belongs
        # to the solver and never to a stored number or a printed message.
        "v_bus_V": round(float(v_bus_V), 2),
        "v_bus_source": ("battery v_nom" if v_nom_V else "caller-supplied bus"),
        "star_delta": str(points[0].get("star_delta") or "star"),
        "modulation_limit": float(_M_MAX),
        "modulation_convention": (
            "m = 2·V_phase_peak/V_bus on the REAL bus, V_phase_peak being the "
            "PER-PHASE fundamental — in delta that is the winding's own V₁/√3, "
            "because a delta winding sits across the line-to-line pair"),
        "I_ripple_convention": (
            "WINDING (phase) rms: sqrt(I_phase_rms_solved² − I1_winding_rms²) "
            "with I1_winding_rms = I1_A/√2 × n_parallel_eff — I1_A alone is one "
            "parallel BRANCH"),
        "I0_A": round(float(I0_A), 3),
        "rpm0": round(float(rpm0)),
        "rpm_grid": [round(float(r), 1) for r in speeds],
        "bridge_model": ("ideal two-level bridge — no dead time, no device "
                         "conduction or switching loss; a real inverter adds "
                         "its own watts on top of these"),
        "coarse_bias": COARSE_BIAS,
        "caveats": [
            ("the deltas are PWM minus a sine baseline solved at the SAME step "
             "count — an unmatched baseline reported roughly double the iron "
             "delta on the CIANO14 (1.80 W against 0.90 W)"),
            ("the solver's mixed-resolution PWM settling is on: the settle is "
             "marched on the fundamental at a coarse step and only its last "
             "two periods run the modulator.  Since B5 (2026-09-13) the DC the "
             "turn-on leaves is measured over those whole periods and removed, "
             "and every run states what was left as pwm_dc_residual_A"),
            ("ideal bridge: no dead time, no device conduction or switching "
             "loss — an inverter's own watts are not in these numbers"),
        ],
        "points": points,
        "skipped": skipped or None,
    }
    block["fit"] = _fit_block(points)
    # The measured envelope the tuner may interpolate inside, and outside of
    # which it must FLAG rather than quietly extrapolate a power law.
    rip = [p["I_ripple_A"] for p in points
           if p.get("I_ripple_A") and not p.get("I_ripple_clamped")]
    block["envelope"] = {
        "I_ripple_min_A": round(min(rip), 4) if rip else None,
        "I_ripple_max_A": round(max(rip), 4) if rip else None,
        "f_sw_min_Hz": min(float(x) for x in fs),
        "f_sw_max_Hz": max(float(x) for x in fs),
        "rpm_min": min(p["rpm"] for p in points),
        "rpm_max": max(p["rpm"] for p in points),
        "note": ("outside this box the deltas are an extrapolation of a "
                 "two-point power law — the tuner clamps to the edge and says "
                 "so rather than quoting the extrapolation"),
    }
    return block


def _fit_block(points: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Exponent of each delta against the ripple current, from the RATED
    point's two carriers.

    Physics says the eddy-type terms (magnet, iron) go as the square of the
    ripple and the torque ripple as its first power, so those are the
    fallbacks — but a fallback is labelled ``assumed`` and a fit is labelled
    ``measured``, and the tile can tell the two apart.
    """
    # A point whose ripple had to be clamped is NOT an abscissa — it is kept in
    # `points` (its losses are still a measurement) and excluded here, so a
    # fitted exponent is never formed against a number that could not be
    # measured.
    rated = [p for p in points if p.get("rated") and p.get("I_ripple_A")
             and not p.get("I_ripple_clamped")]
    rated.sort(key=lambda p: float(p["f_sw_Hz"]))
    out: Dict[str, Any] = {
        "method": ("log-log slope of each delta against the MEASURED ripple "
                   "current, from the rated point solved at two carriers"),
        "abscissa": ("WINDING non-fundamental rms, I1_winding_rms = I1_A/√2 × "
                     "n_parallel_eff taken off the solved rms in quadrature "
                     "(the branch amplitude alone would count most of the "
                     "fundamental as ripple on a machine with parallel paths)"),
        "n_points": len(rated),
    }
    keys: List[Tuple[str, str, float]] = [
        ("dP_mag_W", "n_mag", 2.0),
        ("dP_fe_W", "n_fe", 2.0),
        ("dP_cu_ac_W", "n_cu", 2.0),
    ]
    if len(rated) >= 2:
        a, b = rated[0], rated[-1]
        out["ref"] = {"f_sw_Hz": a["f_sw_Hz"], "rpm": a["rpm"],
                      "I_A": a["I_A"], "I_ripple_A": a["I_ripple_A"]}
        out["pair"] = [{"f_sw_Hz": p["f_sw_Hz"], "I_ripple_A": p["I_ripple_A"],
                        "dP_mag_W": p["dP_mag_W"], "dP_fe_W": p["dP_fe_W"],
                        "dP_cu_ac_W": p["dP_cu_ac_W"],
                        "ripple_pwm_pct": p["ripple_pwm_pct"]}
                       for p in (a, b)]
        # the ripple-current law itself, checked rather than trusted:
        # I_ripple ∝ 1/f_sw predicts this ratio; the measured one is quoted so
        # a machine that disagrees is visible on its own passport.
        out["I_ripple_law_check"] = {
            "predicted_ratio": round(float(a["f_sw_Hz"]) / float(b["f_sw_Hz"]), 4),
            "measured_ratio": round(float(b["I_ripple_A"])
                                    / max(1e-9, float(a["I_ripple_A"])), 4),
            "note": ("I_ripple ∝ V_bus/(f_sw·L): halving the carrier should "
                     "double the ripple current.  A measured ratio far from "
                     "the prediction means the coarse pass is averaging the "
                     "ripple away at the higher carrier"),
        }
        for key, name, fallback in keys:
            n = _fit_exponent(float(a["I_ripple_A"]), float(a[key]),
                              float(b["I_ripple_A"]), float(b[key]))
            out[name] = round(n, 3) if n is not None else fallback
            out[name + "_source"] = "measured" if n is not None else "assumed"
        n_rip = None
        if a.get("ripple_pwm_pct") and b.get("ripple_pwm_pct"):
            n_rip = _fit_exponent(
                float(a["I_ripple_A"]),
                max(1e-9, float(a["ripple_pwm_pct"]) - float(a["ripple_sine_pct"] or 0.0)),
                float(b["I_ripple_A"]),
                max(1e-9, float(b["ripple_pwm_pct"]) - float(b["ripple_sine_pct"] or 0.0)))
        out["n_ripple"] = round(n_rip, 3) if n_rip is not None else 1.0
        out["n_ripple_source"] = "measured" if n_rip is not None else "assumed"
        n_dc = None
        if a.get("I_dc_ripple_pp_A") and b.get("I_dc_ripple_pp_A"):
            n_dc = _fit_exponent(float(a["I_ripple_A"]),
                                 float(a["I_dc_ripple_pp_A"]),
                                 float(b["I_ripple_A"]),
                                 float(b["I_dc_ripple_pp_A"]))
        out["n_dc_ripple"] = round(n_dc, 3) if n_dc is not None else 1.0
        out["n_dc_ripple_source"] = "measured" if n_dc is not None else "assumed"
        # HOW COMPARABLE THE PAIR IS.  Both members are snapped to a divisor of
        # the sliding-band ring, so two carriers can land at quite different
        # samples-per-carrier (measured on the CIANO14: 7.5 at 48 kHz against
        # 4.6 at 96 kHz).  The coarser member reads its ripple-driven numbers
        # LOW, which tilts the slope — so the mismatch is REPORTED next to the
        # exponents it biases instead of being left for someone to discover.
        sa = float(a.get("samples_per_carrier") or 0.0)
        sb = float(b.get("samples_per_carrier") or 0.0)
        ratio = (max(sa, sb) / sb) if min(sa, sb) > 0 else 0.0
        if min(sa, sb) > 0:
            ratio = max(sa, sb) / min(sa, sb)
        out["pair_resolution"] = {
            "samples_per_carrier": [sa, sb], "ratio": round(ratio, 2),
            "comparable": bool(ratio <= 1.25),
            "note": ("the two carriers were solved at %.1f and %.1f samples "
                     "per carrier; the coarser one under-reads its ripple-"
                     "driven losses, so an exponent fitted across a large "
                     "mismatch is biased LOW" % (sa, sb)),
        }
        if ratio > 1.25:
            for _k, name, _f in keys:
                if out.get(name + "_source") == "measured":
                    out[name + "_source"] = "measured (resolution-mismatched pair)"
    else:
        ref = rated[0] if rated else (points[0] if points else {})
        out["ref"] = {"f_sw_Hz": ref.get("f_sw_Hz"), "rpm": ref.get("rpm"),
                      "I_A": ref.get("I_A"),
                      "I_ripple_A": ref.get("I_ripple_A")}
        for _key, name, fallback in keys:
            out[name] = fallback
            out[name + "_source"] = "assumed"
        out["n_ripple"], out["n_ripple_source"] = 1.0, "assumed"
        out["n_dc_ripple"], out["n_dc_ripple_source"] = 1.0, "assumed"
        out["note"] = ("only one carrier was measured, so every exponent below "
                       "is the physical expectation (eddy-type ~2, torque "
                       "ripple ~1), not a measurement of this machine")
    return out
