# Saved motor waveform inventory for terminal flux-work diagnostic

No previously saved waveform located in the bounded artifact review was
eligible for `torque_energy_diagnostic`. The diagnostic requires raw per-branch phase
currents and flux linkages on matching samples, signed actual mechanical
angles, pole-pair count, parallel-strand count, and explicit evidence of a
settled periodic conservative-lossless run covering an integer number of
electrical periods. It reports terminal flux work per mechanical radian; it
does not independently validate torque or infer cogging.

The coordination note identifies
`solver-ripple-review/waveform-comparison/raw-waveform-comparison.json` as a
saved Maxwell waveform comparison. Its records have 72-element `all_samples_Nm`
and `actual_mechanical_angles_deg` arrays and saved Maxwell mean/convergence
metadata. The records contain no phase-current or flux-linkage arrays, no
`pp`/`n_parallel`, and no explicit eddy/demagnetization/settling-periodicity
eligibility evidence. It can compare Maxwell torque waveforms only; it cannot
be passed to the terminal-work diagnostic.

The focused `solver-torque-physics` and `solver-raw-torque` visualizations
contain no saved raw current/flux/angle waveform. `tests/check_raw_torque_native.py`
is a test driver that invokes `fem_transient_sliding_band`; it is not a saved
run output. The inspected `scratch_perf/noload_fe_sweep_36.json` and
`scratch_perf/noload_fe_sweep_72.json` store scalar power/torque summaries,
not waveform arrays. The prior `solver-ripple-review` raw spectra also concern
Maxwell torque, not terminal flux work.

The smallest useful next motor check is one isolated imposed-current P2 run on
the existing compact 30 mm physics-regression geometry, with eddy and
demagnetization disabled and `n_parallel=1`. Save raw branch currents,
fluxes, and signed actual mechanical angles for a settled full integer
electrical period, plus mesh/source hashes and the solver's convergence,
eddy, demagnetization, and period metadata. The diagnostic's 16-sample minimum
is only an input floor; retain enough native samples to check its prescribed
coarsening sensitivity without filtering or dropping any samples. Compare the
result to the same run's recorded hybrid/Maxwell mean only as a diagnostic
cross-check, and do not call it a validated torque or no-load cogging estimate.
At inventory time there was no runtime record for this probe; the later
isolated two-period run below took 36.67 process CPU seconds. That result does
not estimate a full mechanical-revolution check.

## Isolated two-period probe

After the inventory, one isolated probe was authorized. Its complete raw return
and extracted arrays are under
`scratchpad/torque_motor_probe_20260923_run02/`; the runner is
`scratchpad/torque_motor_probe_20260923.py`. The first attempt completed the
FEM solve but the harness raised `NameError: np is not defined` before saving
the result. The parent authorized one same-parameter retry after a synthetic
NumPy serialization/extraction check passed. That retry completed in 37.55 s
wall / 36.67 process CPU seconds, below the 120 s limit; its child exited and
was confirmed absent. No additional solve was run.

The retry used 24 samples per electrical period over two periods (48 samples),
15,000 rpm, 60 A RMS imposed current, manual d-axis 60°, seven pole pairs,
`n_parallel=1`, and the pinned 30 mm regression geometry. P2 was selected;
eddy, rotor eddy, and demagnetization were disabled. Mesh inputs were
1.4 mm/0.35 mm target sizes, two sectors, one gap layer, structured gap enabled,
and the solver reported `structured_gap_effective=true` and 144 slip nodes per
period. The explicit geometry, material assignments, and scratch config copy
are recorded in `metadata.json`; the source config SHA-256 was identical before
and after. The current solver commit was
`d943b1b1c4cf8de4e3e4afba5edffe858793f637`, and the solver-file SHA-256 is
`528fbe27ca0688f5d1ddeeacd8129f8fa058c1a0805fc2b7984c90dc3132e805`.

The two periods match closely in terminal samples: the maximum phase-current
repeat error was `1.42e-13 A`; the maximum phase-flux repeat error was
`1.74e-8 Wb` (`3.43e-5` relative); and the mechanical shift between periods
matched `360/7°` within `1.43e-14°`. All 48 P2 samples met the solver's
nonlinear convergence gate (`picard_converged=true`, maximum residual
`8.26e-8` against `1e-3`). The reported hybrid and Maxwell means were
`0.409666 Nm` and `0.408136 Nm`, respectively. These are same-run reference
outputs only; no terminal-work diagnostic result was produced.

Periodicity remains uncertified for the diagnostic. The probe did not save the
full field snapshots needed to compare rotor-local field/material degrees of
freedom under the physical symmetry permutation, and terminal `i/ψ` repeat
alone does not establish stored-field periodicity. The run also emitted a
rotor-half core-loss window warning about incommensurate slot passing; that is
not itself a flux-work result, but reinforces the need for a full-field
periodicity check before certification. The helper was therefore not called;
no eligibility flags were asserted. The audit hook rejected Python-visible
writes and filesystem mutations outside the unique output directory, disabled
bytecode writes and child-process creation, and redirected the workspace,
warm-cache, d-axis cache, and temporary directory to scratch. The shared
material library was read-only. The output contains `raw_result.json`,
`raw_waveforms.npz`, and provenance metadata for a later review.

## Uncertified path-work and periodicity assessment

Direct numerical processing of the saved samples (without calling the helper
or setting its eligibility flags) gives a periodic trapezoidal path-work of
`0.4050022866 N·m-equivalent` over the 48 samples. Using every other sample
gives `0.3907143578`, a `3.5279%` relative coarsening change, well above the
diagnostic's `0.1%` setting. These are numerical path-work values only, not
validated torque.

An all-harmonic spectral derivative of flux, with the real-grid Nyquist
derivative set to zero, gives `0.4096659527` on all 48 points and
`0.4091551187` on the 24-point even-sample grid. Their relative change is
`0.1247%`, still above `0.1%`. The raw Nyquist coefficient remains in the
waveform; assigning its derivative zero is the standard real-grid convention,
not evidence that the physical Nyquist sine quadrature is absent. Higher
harmonics can alias into retained bins, so the spectral estimate is also
uncertified. The imposed currents fit a sinusoid plus DC with maximum residual
below `2.4e-13 A`. The difference between line-trapezoid and spectral values is
consistent with the expected sinc factor for a sinusoid sampled at 24 points
per electrical period (and 12 points on the coarsened grid); it does not make
either value an accuracy certificate.

The continuous, memoryless magnetostatic setup has a plausible one-electrical-
period global symmetry: the 14 equal pole sectors alternate polarity, so the
magnet pattern repeats after two pole pitches (`360/7 = 51.42857°` mechanical);
the imposed sinusoidal source repeats after the same mechanical shift. The
existing `geometry_2d.validate_sector_symmetry` guard also accepted the
12-slot/14-pole, two-sector winding basis. Rotor-local material-point histories
do not have that same period: slot passing is not commensurate with one
electrical period and the run's core-loss postprocessor warned that its
rotor-half window did not close. With eddy and demagnetization disabled, those
local changes carry no explicit dynamic state, and a physical energy integral
could repeat under a cyclic permutation of identical rotor poles. The saved
probe does not establish that permutation for the production P2 mesh and its
sliding-band projection, nor compare the resulting discrete field energy.
Terminal current/flux repetition and the sector guard alone therefore do not
certify the discrete stored-energy periodicity needed by the diagnostic. No
helper result or torque claim is made; the path-work figures above remain
uncertified.

## 48-sample refinement

One isolated refinement used 48 samples per electrical period over two
periods (96 total), with the same explicit 30 mm geometry, material assignments,
1.4/0.35 mm mesh targets, two sectors, one gap layer, and structured gap as
run02. `SB_SLIP_PER_PERIOD=0` and the loaded override was zero; the solver again
reported 144 slip nodes per period. The source config remained hash-identical.
The run completed in 58.30 s wall / 56.27 s process CPU, exit code 0, within
the authorized 180 s limit. Its output is
`scratchpad/torque_motor_refinement_20260923_run03/`.

The solver returned and saved all 96 raw P2 field frames (`A`, `Bx`, `By`,
`P_mm`, step and angle), alongside raw phase waveforms and mesh arrays. These
are available for independent physical symmetry mapping. No rotor/material
permutation or nonlinear stored-energy quadrature comparison was performed,
so full-field periodicity remains uncertified and the diagnostic helper was
not called.

At the 48 common angles, currents and angles matched run02 exactly. Maximum
flux difference was `3.45e-11 Wb` (`6.83e-8` relative); Maxwell torque differed
by at most `2.38e-8 Nm`. Hybrid torque differed by at most `8.21e-5 Nm`
(`1.98e-4` relative). Mean hybrid torque was `0.4096483 Nm` versus
`0.4096660 Nm` in run02; mean Maxwell torque was `0.4082001 Nm` versus
`0.4081358 Nm`. The solver converged all samples. It again emitted the
rotor-half core-loss window warning for incommensurate slot passing, plus
measured-envelope extrapolation warnings preserved in `run.log`.

The run metadata's derived `uncertified_terminal_pathwork` values are marked
INVALID: the calculation divided by pole-pair count after already using the
mechanical-angle span (factor-of-seven error), and its spectral Nyquist
handling was incorrect. The original values remain under
`uncertified_terminal_pathwork_invalidated` for provenance. Raw phase data and
angles remain intact in `raw_waveforms.npz`. No torque conclusion is based on
those invalid derived values.

### Independent Sol recomputation

The corrected all-sample spectral terminal-work values are
0.4096659526814519 N m-equivalent (run02) and 0.4096482539716928
N m-equivalent (run03). The latter coarsened to every other sample is
0.4096659508753601. These agree algebraically with the hybrid mean for the
imposed sinusoidal currents; they are not an independent physical validation.

Doubling angular sampling changed the hybrid mean by -0.00432%, but raw
Maxwell peak-to-peak torque increased from 0.01305570035 to 0.01542096114
N m (+18.12%). Common-angle agreement indicates extra samples found missed
extrema. This is evidence that ripple amplitude needs further angular
convergence checks, not evidence of converged ripple.

Correction to the frame description above: saved A contains only 4597 vertex
values per frame, while Bx/By are element means. P2 edge degrees of freedom
and quadrature fields are absent. These visualization frames cannot establish
full discrete-state or nonlinear stored-energy periodicity. A separate isolated
full-P2 capture is required; production torque remains unchanged.

### Full-P2 observation (run04, Sol)

The isolated observer captured 17,299 P2 DOFs and B at six quadrature points
on each of 8,107 elements for frames 0, 1, 48, 49. Raw waveform NPZ is
byte-identical to run03, showing the observer did not change the output.
Selected Newton residuals are 3.94e-8 to 9.27e-8, all converged.
Artifacts: `scratchpad/torque_full_state_20260923_run04/`.

At the same stator indices, frames 0 and 48 differ by up to 2.14e-7 in A,
7.80e-4 T in Bx and 8.30e-4 T in By. Rotor-local indices require the physical
rotation/material permutation; their large direct differences cannot establish
nonperiodicity. The nonlinear stored-energy functional including PM sources
has not been evaluated. Full-state evidence is now saved, but energy-periodicity
certification and independent production-torque validation remain open.
