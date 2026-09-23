# Torque diagnostic eligibility from current solver metadata

This is a source-only eligibility audit. It does not change the selected
torque series, run FEM, or certify any saved motor result.

## Existing diagnostic and integration status

`simulation/torque_energy_diagnostic.py:24-122` computes periodic terminal
flux work per mechanical radian, provided the caller supplies booleans for
`periodic_window_certified`, `settled`, and
`conservative_lossless_certified`. It rejects non-current drive, eddy,
rotor-eddy, demag, zero current/speed, noninteger period count, malformed or
nonuniform angle series, and excessive even-sample coarsening sensitivity.
The coarsening comparison is not a convergence proof. There are no solver
callers today; tests in `tests/test_torque_energy_diagnostic.py` exercise the
helper directly with synthetic analytic states and caller-asserted booleans.

The production `fem_solver_2d.py:7345` calls `sb_postproc.hybrid_torque` and
now also computes the additive `torque_method_diagnostics` block from those
same phase flux/current arrays and the **raw** Maxwell series. The result
continues to return the selected waveform/mean, raw Maxwell waveform/mean,
and method (`fem_solver_2d.py:7833-7847`). The separate periodic terminal-work
helper remains uncalled; no periodic-energy eligibility verdict is produced.

## Regime policy and evidence

| Regime | Existing result fields / what they establish | Eligibility conclusion |
|---|---|---|
| Imposed-current, conservative, settled, full integer-period window | `drive`, `rpm`, `n_periods`, `f_elec_Hz`, `rotor_angle_deg`, `I_A/B/C`, `psi_A/B/C_Wb`, `n_parallel_eff`, `picard_converged`, `eddy_coupled`, `rotor_eddy_solved`, demag summary/map outputs (see solver lines 7801-8032, 7833-7873, 7926). Arrays and configuration flags are useful inputs; Picard convergence only says the numerical iteration converged. | **Potentially eligible in principle** for periodic terminal flux work if actual signed angle samples are uniform over an integer number of periods, the entire magnetic/material state is periodic and settled, and storage/loss closure is independently certified. The returned fields do not certify those last three conditions. The diagnostic's `True` booleans cannot safely be inferred from these metadata alone. |
| Sinusoidal imposed-current / candidate fundamental dq mean | `drive == "current"`, phase `I_*`, `psi_*`, and gamma/d-axis metadata are available. `hybrid_torque` always has phase arrays, but uses the mean only for branch peak >1 A. | Sinusoidal current alone does not prove rotationally covariant sinusoidal winding/coenergy or absence of rotor-angle-dependent coenergy. The analytic fifth spatial-harmonic fixture in `tests/test_torque_energy_validation.py` gives 2.31 N·m by fixed-current coenergy derivative versus 2.058 N·m from the helper. Do not certify the dq candidate using drive name, current shape, magnitude, or residual alone. |
| Voltage, PWM voltage, custom-current, BLDC, or other non-sinusoidal source | `drive`, nested `excitation.kind/series`, `pwm`, `custom_current`, and `bldc` describe the applied source; solved phase `I_*` is still returned. | The existing diagnostic accepts only `drive_mode="current"`; voltage/PWM are explicitly ineligible. Custom-current and BLDC can be imposed current physically, but their allowed use would need a deliberate diagnostic-contract extension plus actual full-period/state certification. Their presence as current arrays does not justify the dq identity. |
| Zero current / possible PM cogging | `I_A/B/C`, `T_em_maxwell_Nm`, and `T_avg_maxwell_Nm` exist. | Terminal flux work from current is zero and cannot diagnose no-load cogging. The Maxwell output can retain a mean/ripple at zero current. Keep the raw result; label terminal-work diagnostic ineligible. Do not substitute zero, dq, or terminal work for cogging torque. |
| Coupled stator/rotor eddy | `eddy_coupled`, `rotor_eddy_solved`, `eddy_settled`, `eddy_capped`, residual/tolerance and loss series are returned (solver lines 7865-7873, 7904-7958). | Reject for the current conservative lossless diagnostic. Eddy-settled only concerns the solver's warm-up criterion; it does not provide periodic stored-energy closure or total loss closure. A future energy balance would need the relevant dissipative terms and internal state. |
| Demagnetization/evolving material state | `demag_coef_per_tri`, `demag_report`, `demag_summary`, `demag_seeded`, `demag_seed_from` are exposed (lines 7801-7805, 7893-7900). | Reject if demag was enabled or the material state changed. There is no single top-level `demag_enabled` or beginning/end state-equality certificate in the result. A null report/map should not be promoted to a general proof of conservative, unchanged material without a result contract that states why it is null. |
| Noninteger or nonuniform reported window | `n_periods`, `n_steps_per_period`, `n_total`, `dt_s`, and a `rotor_angle_deg` list are present (lines 7585-7592, 7833-7835). | The helper can validate period count and its supplied angle series. However, top-level `pole_pairs` is not emitted (the helper needs it); geometry/request metadata would have to supply a provenance-bound value. Solver-produced timing/angle series must be checked for actual uniform signed increments after settling trim; requested cadence, `n_steps_per_period`, or endpoint resemblance is not certification. |
| Unproven stored-energy or loss closure | Loss series (`P_*`) and selected/raw torque are returned, but not magnetic coenergy at the window endpoints, a full discrete energy residual, or a certificate that every relevant loss/state variable is included. | Ineligible for general torque interpretation. For an ideal conservative periodic system, the cycle integral of terminal `i dψ` equals electromagnetic mechanical work only when the full stored state closes over the cycle and all relevant ports are represented. With eddy/hysteresis/demag losses, that identity requires storage and dissipative terms. The present helper is explicitly conservative-lossless and does not estimate shaft torque after mechanical losses. |

### Criteria for the two candidates are different

For a **periodic terminal-work diagnostic**, a conservative sufficient gate is:
imposed current; finite nonzero signed speed; exact positive pole-pair count;
uniform actual mechanical-angle increments covering an integer electrical
period count; finite matching phase currents/linkages and positive parallel
branch count; verified settled periodicity of the complete magnetic/material
state (not just phase current or terminal flux samples); unchanged material
state; no coupled eddy or other unaccounted dissipation; and demonstrated
closure of the applicable stored-energy/port-work balance. The existing helper
checks array shape and cadence plus booleans for some physical premises, but
does not provide the missing solver-state evidence. Its sampling comparison
only flags coarsening sensitivity and cannot rule out aliasing.

For the **fundamental space-vector mean**, measured sinusoidal current is
insufficient. Eligibility additionally requires evidence that the relevant
coenergy has the rotational covariance and winding distribution assumed by
the dq identity, with no omitted position-dependent harmonic/cogging terms or
loss/state dynamics invalidating the mean relation. The analytic harmonic
counterexample establishes that an apparently sinusoidal fundamental input is
not a universal authorization. Existing result fields do not provide this
certificate. The 1 A branch-current threshold is not evidence for either
candidate's eligibility.

## Implemented diagnostics-only change

`src/motor_ai_sim/simulation/sb_postproc.py` now contains the pure helper
`torque_method_diagnostics`; `fem_solver_2d.py` calls it inside a guarded
diagnostic-only block. It reports:

- raw Maxwell mean and space-vector candidate mean (even where the legacy
  selector does not select the latter);
- diagnostic status `not_certified` by default, plus explicit reason codes for
  known exclusions (non-current drive, zero current, eddy, demag, invalid
  angle window, missing period metadata);
- `validation_status="uncertified"` and a reason, regardless of which
  candidate the legacy selector chose; `certified_energy_balance_Nm` remains
  null. It does not call the periodic-work helper or create a physics gate.

Malformed, mismatched, empty, nonfinite, or finite-but-overflowing inputs yield
null candidate values and an input reason; JSON serialization rejects NaN and
infinity in the focused test. An outer guard also keeps diagnostic failures
from interrupting a completed solve. The helper's `legacy_selector_would_use_...`
field reports the historical >1 A branch condition only; it is expressly not
an eligibility signal. The `tests/test_torque_method_diagnostics.py` standalone
test checks boundary reporting, parallel scaling, zero current, malformed
inputs/overflow, P2 wiring to raw Maxwell plus actual method, and that the
existing selector's output remains unchanged. All five focused tests pass.

This is additive metadata only; no formula switch, waveform filtering, or
discarding of samples. The next step for any formula change remains independent
virtual-work/energy validation. Do not manufacture periodicity or conservative
energy certifications from `eddy_settled`, `picard_converged`, integer
`n_periods`, or small current/flux residuals alone.

Model: Codex Luna; no escalation. Standalone diagnostic tests only; no FEM,
API, configuration, browser, or physics-eligibility claim.
