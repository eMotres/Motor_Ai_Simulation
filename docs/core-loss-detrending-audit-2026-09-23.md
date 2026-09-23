# Core-loss wrap detrending audit — 2026-09-23

Run15 reported ramp removal with a **76% mean correction weight**; this is
not a count of elements whose field was entirely removed.
This is the rotor-half open-window correction, not a change to the solved field.

In `simulation/losses.py::harmonic_amplitudes` (around lines 99–120), each
element's periodic-extension jump is estimated from the last three samples and
first sample, divided by peak-to-peak amplitude, then tapered with
`WRAP_TAPER=(0.15, 0.30)`. The resulting weighted linear ramp is subtracted
before the DFT. In `surface_loss_density`, this affects the harmonic amplitudes
used to evaluate measured P(B,f) surfaces. The source arrays are not modified:
the subtraction creates a new array, while the original per-frame field
histories remain intact in the solver until post-processing completes.

That adjustment changes the measured-surface core-loss total and its assigned
hysteresis/excess remainder; `iron_loss_series` keeps its classical eddy series
from the original B(t), except for its existing rescale-if-surface-below-eddy
case. The downstream `P_fe_ser2`, `P_fe_avg2`, and reported `P_fe_terms` reflect
that adjusted surface result. The run result reports `wrap_jump_frac` and
`wrap_guard_weight`, but does not preserve raw-vs-adjusted surface-loss totals
side by side. The solver's raw B histories are temporary and are not part of
the public result; selected state captures retain B only for selected frames.

Existing coverage is in `tests/test_core_loss_surface.py`:
`test_the_leakage_guard_does_nothing_to_a_closed_window` checks no correction
for periodic synthetic input, and
`test_the_leakage_guard_removes_an_open_window_step` asserts strong ramp
removal and reduced high-frequency tail for a synthetic 1.714-cycle window.
`tests/test_losses.py` covers the shared P1/P2 loss implementation, but no test
compares raw and corrected motor loss on a full commensurate rotor window.

The guard's own documentation describes the removed rotor drift as real flux
excursion and calls the corrected harmonic sum a lower bracket; an uncorrected
short-window DFT can also misassign that content across frequencies. Smallest
honest next step: expose raw and corrected surface-loss candidates with the
existing guard metrics, without selecting either as truth, then validate on a
slot-passing-commensurate rotor capture (the source identifies seven electrical
periods) before changing the reported model. No formula or tests were changed
for the initial audit; no FEM run was launched by that audit.

## Candidate visibility added

The measured-surface path now exposes `surface_raw_window_candidate_W` and
`surface_detrended_candidate_W` per steel half, along with
`surface_selected_candidate="detrended_legacy"`, `wrap_jump_frac`, and
`wrap_guard_weight`. The top-level result also reports
`P_fe_raw_window_candidate_avg_W` and `P_fe_detrended_candidate_avg_W`; raw
replaces only each measured-surface half's candidate total while keeping other
halves' selected models intact. Existing `P_fe_avg_W`, time series, and selected
formula still use the legacy detrended candidate. These are diagnostic
comparisons, not a validation or selector change. Candidate evaluation uses a
separate excursion context and leaves the selected surface's envelope logs
unchanged.
