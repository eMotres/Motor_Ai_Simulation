"""Post-processing of the sliding-band frame series: settling-window trimming,
the reported torque, and its harmonic spectrum.

Everything here runs AFTER the frame loop, on plain lists of per-frame numbers.
It has no view of the mesh, the field or the element order — which is exactly
why the P1 and P2 branches were able to grow their own copies of it. The hybrid
torque in particular was written twice, and the whole reason it exists is that
one of the two once reported a mean ~35 % above the other on the same machine.
One definition, used by both, is the only way that stays fixed.

Pure functions with explicit arguments, except ``drop_settling_frames``, which
trims lists in place because the frame loop's series ARE those lists.
"""
from __future__ import annotations

import math
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np


def drop_settling_frames(series: Iterable[list], n_skip: int,
                         time_series: Optional[list] = None) -> None:
    """Delete the first ``n_skip`` frames from every per-frame list, in place.

    Voltage drive and demagnetisation each prepend their own settling window and
    each strips its OWN prefix afterwards, in sequence — never double-counted.
    EVERY per-frame series has to be in ``series``: the circuit diagnostics were
    once left out, and their indices were then offset against I/psi/T, so the
    reported max residual was the SETTLING residual rather than the steady-state
    one.

    ``time_series`` is re-based to start at zero after the trim (pass the same
    list again — it must also appear in ``series`` to be trimmed).
    """
    for lst in series:
        if len(lst) > n_skip:
            del lst[:n_skip]
    if time_series:
        t0 = time_series[0]
        time_series[:] = [t - t0 for t in time_series]


def eddy_settle_resid(p_solid: Sequence[float], n_steps_per_period: int,
                      dt: float) -> Tuple[float, Optional[float]]:
    """How much START-UP TRANSIENT is LEFT at the end of an eddy warm-up march.

    ``p_solid`` is the solid-conductor sigma*E^2 power [W], one entry per warm-up
    frame, chronological; the last entry is the handoff frame (theta = -dtheta).
    Returns ``(residual, tau_s)`` — the residual as a FRACTION of the settled
    level, and the fitted decay time constant when the decay is clean enough to
    read one off (else ``None``).

    Two traps this exists to avoid:

    * "the last frame barely moved" is NOT settled.  A geometric decay with
      ratio q leaves q/(1-q) times the last step still to come: at q = 0.9 a
      2 % per-frame move means a 18 % transient is still running.  So the last
      three samples are Aitken-extrapolated to the geometric tail and what is
      REPORTED is that tail, not the step.
    * the solid loss RIPPLES with rotor position — 20 % frame to frame on a 30 mm
      machine that settled in one step, because a 3-phase machine's loss carries
      the 6th electrical harmonic.  Reading a per-frame difference as decay would
      call every machine un-settled forever.  So the samples are averaged in
      blocks of one ripple period (n_steps_per_period / 6) before the tail is
      fitted, and a "decay" whose two steps do not both point down, or whose
      ratio is not in (0, 1), is read as ripple (no decay detected) rather than
      extrapolated into a number.
    """
    p = [float(x) for x in p_solid]
    n = len(p)
    if n < 3:
        return float("inf"), None            # nothing to say yet
    w = max(1, min(int(round(n_steps_per_period / 6.0)), n // 3))
    m = [float(np.mean(p[n - (j + 1) * w: n - j * w])) for j in (2, 1, 0)]
    ref = max(abs(m[2]), 1e-30)
    d1 = m[1] - m[0]
    d2 = m[2] - m[1]
    if d1 < 0.0 and d2 < 0.0:
        q = d2 / d1
        if 0.0 < q < 1.0:
            tau = (-(w * float(dt)) / math.log(q)) if (dt and q > 0.0) else None
            # q is capped for the EXTRAPOLATION only: three block means cannot
            # tell q = 0.99 from ripple, and 1/(1-q) explodes there.  Capped, a
            # very slow decay still reports ~9x the last step, i.e. "not settled".
            qq = min(q, 0.9)
            return abs(d2) * qq / (1.0 - qq) / ref, tau
    return abs(d2) / ref, None


def hybrid_torque(psi_a: Sequence[float], psi_b: Sequence[float],
                  psi_c: Sequence[float], i_a: Sequence[float],
                  i_b: Sequence[float], i_c: Sequence[float],
                  t_maxwell: Sequence[float], pole_pairs: int,
                  n_parallel: int = 1) -> Tuple[List[float], str]:
    """Energy-consistent MEAN + Maxwell-stress RIPPLE.  Returns (T(t), method).

    ``psi_*`` and ``i_*`` are PER-BRANCH (one parallel path), which is how the
    solver carries them everywhere — ``_sc_psi2`` divides psi by n_parallel and
    the excitation's i_peak is ``I_phase / n_parallel``.  The virtual-work
    identity is in PHASE quantities, so ``n_parallel`` restores the phase
    current (n_parallel branches carry the phase current between them, each at
    the same flux linkage).  Omitting it reported ``T_true / n_parallel``; it
    was invisible while every config in the repo had one parallel path, and it
    surfaced the moment the per-request winding channel let a stored ``2S-2P``
    machine be evaluated on its own connection (F3).  ``P_elec_in`` in the
    solver already carried exactly this factor for exactly this reason.

    Two physical facts, each measured by the method that is right for it:
     • MEAN — the ANSYS energy method (virtual work) via the terminal flux
       linkages ψ and currents I:  <T> = (3/2)·p·<ψα·iβ − ψβ·iα>, the airgap
       power balance P=T·ω.  It never touches the gap field, so it is immune
       to the sliding-band DC contamination that makes the raw Maxwell mean
       radius-inconsistent (~+35 %); validated vs ω·ψ_pm back-EMF and ANSYS.
     • RIPPLE — the Maxwell-stress torque T(t).  The flux-linkage torque is
       winding-FILTERED, so it is smooth and CANNOT see cogging or the slot
       harmonics → it under-reports ripple (1.7 % vs the real ~5-6 %).  The
       Maxwell σ_rθ integral DOES resolve them (P2 noise floor →0 with mesh).
    So the reported T(t) = Maxwell AC (real ripple) re-centred on the energy
    mean (correct DC).  This is NOT tuning: the DC bias we remove is the
    measured slip-band contamination; the AC we keep is the physical ripple.
    No-load (I≈0) keeps the raw Maxwell cogging directly (energy torque = 0).
    """
    _pa = np.asarray(psi_a, float); _pb = np.asarray(psi_b, float)
    _pc = np.asarray(psi_c, float)
    _ea = np.asarray(i_a, float); _eb = np.asarray(i_b, float)
    _ec = np.asarray(i_c, float)
    _Ipk = (float(np.max(np.abs(np.concatenate([_ea, _eb, _ec]))))
            if _ea.size else 0.0)
    if _pa.size and _pa.size == _ea.size and _Ipk > 1.0:
        _s = 2.0 / 3.0; _kc = math.sqrt(3.0) / 2.0
        _psial = _s * (_pa - 0.5 * _pb - 0.5 * _pc); _psibe = _s * _kc * (_pb - _pc)
        _ial = _s * (_ea - 0.5 * _eb - 0.5 * _ec); _ibe = _s * _kc * (_eb - _ec)
        _Te = (1.5 * float(pole_pairs) * float(max(1, int(n_parallel)))
               * (_psial * _ibe - _psibe * _ial))
        _emean = float(_Te.mean())
        _mx = np.asarray(t_maxwell, float)     # raw Maxwell σ_rθ series
        # real Maxwell ripple, DC re-centred on the energy-consistent mean:
        return (_mx - _mx.mean() + _emean).tolist(), "energy_mean+maxwell_ripple"
    # no-load (or no terminal data) → keep the Maxwell cogging series
    return list(t_maxwell), "maxwell_stress"


def torque_harmonics(t_raw: Sequence[float], n_steps_per_period: int
                     ) -> Tuple[List[int], List[float]]:
    """Single-sided FFT of ONE electrical period of the RAW torque.

    The single most telling diagnostic for "is this periodic or chaotic": a
    clean ripple shows a few DISCRETE peaks (the cogging / 6·k 3-phase orders);
    broadband noise spreads across all orders.  Orders are multiples of the
    ELECTRICAL fundamental; amplitude is the single-sided FFT magnitude [N·m].
    Spectrum is ALWAYS the RAW per-frame torque (not the band-limited series),
    so the UI shows every order and the user can SEE which bars the 6·k filter
    keeps (orange) vs drops (the broadband slip-node noise).
    """
    if not t_raw:
        return [], []
    _per = max(1, int(round(n_steps_per_period)))
    _Tp = np.asarray(t_raw[:_per], float)
    if _Tp.size < 4:
        return [], []
    _F = np.abs(np.fft.rfft(_Tp - _Tp.mean())) / _Tp.size * 2.0
    _nh = min(_F.size - 1, 36)
    return (list(range(1, _nh + 1)),
            [round(float(_F[k]), 4) for k in range(1, _nh + 1)])
