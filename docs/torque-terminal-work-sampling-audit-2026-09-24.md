# Terminal-work torque sampling audit (2026-09-24)

This offline review applies the production `sb_postproc.terminal_work_mean`
implementation directly to archived run13 even shifts and run14 odd shifts.
It reuses the strict two-archive provenance/frame/merge validator in
`scripts/torque_angular_sampling_review.py`, then independently validates the
saved phase flux-linkage vectors `psi_Wb` against every raw frame and validates
the phase current law. No FEM, API, config, solver, or source archive was
modified.

The merged trajectory has exactly shifts 0..119 and endpoint-excluded signed
mechanical angles `shift * (2π/1680) rad`. It is one electrical period for 14
pole pairs. The recorded current is 60 A RMS / 84.8528137424 A peak per phase,
with `phase=14*theta_mech_rad+60°`; `n_parallel=1`, current drive, connection
2S, 15,000 rpm, and `daxis=60°` agree in both run logs. Flux linkage is Wb per
phase. Production computes `n_parallel * mean(sum(i_phase * dpsi/dtheta_mech))`
with mechanical radians directly; no extra pole-pair division is applied.
Both runs used the analytic/template path (`geo_mesh=false`), 4.0 mm mesh size,
0.3 mm minimum, one structured gap layer, and matching geometry/material inputs.
Recorded solver, config, and material-library SHA-256 values are respectively
`67b714f726e89a696014d25a59aa593aa41fffc70f04422b19629446548c12e7`,
`f86e751dd57ca0cfa8e9d8dac46620dc723d11e446a4ce962ea5d4be4c6caba8`, and
`80b269b6ab344458409fd7c56ee9e0e84c58fed0af4211f4af14f022d9b80136`.

For N=20/30/40/60/120, the script evaluates strides 6/4/3/2/1 and every
phase offset 0..stride−1. Each phase row exports selected indices/shifts,
angles, raw phase currents and linkages, production spectral derivative
samples, and every rFFT bin for current, linkage, and derivative in all phases.
The production helper and exported derivative-dot-current calculation agree
to floating-point roundoff. Each offset selects its stated subset of the 120
source positions; every selected raw value and DFT bin is retained, without
demeaning or filtering. The
single-sided spectrum leaves DC and Nyquist undoubled and doubles only interior
bins. Maxwell mean is calculated on the same selected torque indices but is
labelled diagnostic only and never participates in the terminal-work gate.

## Results

The 120-position terminal-work value is **40.5125048341 Nm**; its diagnostic
raw Maxwell sample mean is **40.2474570472 Nm**. The difference is not evidence
that either method is physically correct. It is shown to make the independent
mean comparison visible.

| N | Phase offsets | Terminal-work mean range (Nm) | Span across offsets (Nm) | Maximum absolute delta vs 120 (Nm) | Raw Maxwell mean span, diagnostic only (Nm) |
|---:|---:|---:|---:|---:|---:|
| 20 | 6 | 40.511971–40.513177 | 0.001206 | 0.000673 | 0.008511 |
| 30 | 4 | 40.510634–40.514582 | 0.003947 | 0.002077 | 0.005785 |
| 40 | 3 | 40.512336–40.512705 | 0.000370 | 0.000201 | 0.001548 |
| 60 | 2 | 40.512402–40.512608 | 0.000206 | 0.000103 | 0.005344 |
| 120 | 1 | 40.512505 | 0 | 0 | 0 |

For the raw Maxwell mean diagnostic only, the maximum absolute phase-offset
difference from its 120-point mean is 0.004894 Nm (N=20), 0.002929 Nm
(N=30), 0.000910 Nm (N=40), and 0.002672 Nm (N=60). These values are not
used to accept the terminal-work result.

All coarse terminal-work phase offsets lie within the **provisional** 0.01 Nm
sampling-reproducibility threshold; the CLI exits 0 for this narrow numerical
gate. The 120-position trajectory is the finest archived integer-shift grid,
not a true or continuum-converged solution. This result checks sampling
reproducibility only; it does not certify stored-energy closure, a conservative
periodic field state, the production geometry's virtual-work identity, or
continuum correctness. No ripple or Maxwell agreement is implied.

The derivative implementation mirrors production's `fftfreq`/FFT operation.
For even N, the Nyquist component has zero real derivative under the helper's
sampled convention; it is retained in the evidence spectrum and is not
discarded. Synthetic tests also exercise reversed signed angle order and verify
the exact helper behavior.

Archive provenance matches for geometry, mesh counts (28,661 DOFs, 13,734
elements), effective NS=4/BC sign −1, and solver/config/material hashes. The
source hashes are recorded in the JSON result produced by
`scripts/torque_terminal_work_sampling_review.py`; run13/run14 both used the
same archived solver, config, and material-library digests.
