# D85 remaining actual-domain inventory — 2026-10-10

This is an evidence inventory and bounded planning note. It does not claim a
complete passport, voltage boundary, coupling qualification, or maximum-S1
result. Sources: `docs/d85-configure-coupling-progress-2026-10-08.md`,
`coordination/agent-run-journal-2026-10-07-d85.md`, and
`motor_ai_sim_wt_passdoc2/docs/PASSPORT_ALGORITHM.md` at
`bb3a74c4e63c8e96133328cb28a6deb1ba6c79426ceee03ecce5202caa2fa606`.

| Required domain | Actual evidence | Remaining / qualification boundary | Suggested next bounded source-compatible step |
|---|---|---|---|
| Cold 20 °C baseline | Rated 2-D cold point completed in 554.83 s, HTTP 200; Kt 0.217 N·m/A RMS, Km 0.5018 N·m/√W, Km/mass 1.2641; the r2 batch’s exact P00–P07 identifiers and statuses are retained in its manifest/terminal audit, with exit 0 and steady/eddy/demag/nonlinear flags where recorded. Raw rated response SHA `c8411c59582fa26824f8ca11b255543e6ba4745779a838a0b7b5c1399bb2b5f5`; P07 raw SHA `c62be2edcbc476dba1a48cf6886876c795186583ebda1d256458c4b78c95f538`. | No accepted full cold passport; cold Kv is Br-walked, magnet temperature readback is absent, and 2-D values are not the owner’s 3-D peak basis. | Preserve manifest IDs and exact raw paths; do not recolor or promote them as a complete card. |
| Static MTPA / current map | Passport assembly records actual current rows at 6.47, 12.94, 19.41, 25.88, and 38.82 A RMS; earlier phase02–06 records contribute static/MTPA rows. Phase08 exploratory gamma samples used angles −3.0495387°, 1.9504613°, and 6.9504613° and produced torques 8.169242, 8.225966, 8.207649 N·m. | No single accepted full static passport, state hash, or interpolation/error gate. Existing rows must be reconciled by their exact phase manifests and terminal audits; do not collapse them into one unqualified grid. | Preserve phase02–06 and assembled-passport row identities; do not infer 43.704 or 45.962 A results from the failed/missing Phase09 points. |
| FW voltage boundary | One accepted Phase08 FW2 subset at 25.88 A RMS, 1000 rpm, 20 °C: FW50/65/80 torques 3.574973, 2.304391, 0.924835 N·m; runtime root `618fa334e60c4ae2`, exit 0, 120 frames, no refinement. FW35 original `f383eaeb05054c4a` was rejected and is not rerun. | No cold negative-id boundary, v-limit margin, or full FW trajectory; gap-2 basis differs from earlier gap-1 data. | Treat FW50/65/80 as accepted partial evidence only; never interpolate a boundary or revive FW35. |
| Steady short circuit, dq + R | Algorithm requires both dq equations with R; phase07 has raw Ld/Lq 0.141066/0.144013 mH at four positions. The corrected Park analysis gives Kt 0.223365 and Km 0.516648 as an independently reviewed but explicitly unqualified candidate. | No accepted steady-SC pair, dynamic SC peak, or cold R-linked characteristic-current result. | Keep the candidate and its review status; do not relabel it as rejected math or as an accepted constant. |
| Dynamic short circuit | No actual dynamic-SC run in the reviewed records. | Entire domain remains open, including transient peak and decay. | Separate future request with explicit time horizon and current/thermal limits; no reuse of steady-SC labels. |
| Mesh / time / settle convergence | Accepted FW subset records Picard 120 frames and clean exit; phase07/phase08 retain raw operational evidence. | No mesh refinement, time-step convergence, tail bound, or independent torque/ripple convergence across D85 domains. | One minimal convergence witness at an already accepted operating point, with before/after hashes and no automatic refinement. |
| 3-D correction | Owner basis includes a 3-D peak Km 0.442; current D85 records are 2-D or scalar/provenance estimates. | No accepted 3-D kT/kL/flux or end-effect run. | Defer 3-D until 2-D cold/static/FW evidence is frozen; do not transfer the 3-D peak into 2-D rows. |
| Thermal / mechanical / S1 coupling | Coupling lifecycle and UI/source preparation exist; process-smoke evidence proves supervision only. | No closed thermal fixed point, heat-balance residual, mechanical-loss ledger, shaft efficiency, or maximum-S1 qualification. | One bounded thermal/mechanical closure package after physics rows are accepted; no live/API/FEM action from this note. |

## Current runtime and next-plan constraint

Phase09 attempt `73de1c4637def4cb` is terminal-failed, with cleanup complete
and no owned container remaining. The first point, 38.820 A RMS, is accepted
only as bounded two-layer raw evidence (raw SHA
`4a3e7f18069764897829d1d53b5f4e1992c79507901bc871b70ce04470c8814e`; positive
ψd `0.0018736427720977755 Wb`). The second point, 43.704 A RMS, is refused
for the two-layer series because automatic refinement changed the saved basis
to three layers (raw SHA
`11b4976822e1b2fc44018163a605e93aa29ddd73b3de69180ea02d5de0a427f3`). The
third point, 45.962 A RMS, was not launched. Qualification, S1, and
publication remain false.

The next possible package is a separate four-layer proposal for the missing
45.962 A point, but it is unfinished, not independently reviewed, and not
launched. It must not be treated as a running FEM job or as a reason to rerun
any prior point. Any physics next plan requires GPT-5.6 Sol review and Astra
final assessment.

### Provenance and status

- Accepted partial: cold r2 P00–P07 manifest rows and FW50/65/80 subset, with raw
  response/state/config evidence retained in the referenced continuation and
  journal.
- Diagnostic / rejected: original FW35; phase07 Kt/Km Park analysis is an
  independently reviewed but unqualified candidate, not rejected math.
- Planned only: full static MTPA/off-grid grid, voltage boundary, steady/dynamic
  SC, mesh/time convergence, 3-D, thermal/mechanical closure, and maximum S1.
- No values in this note are copied into Drive or treated as a card update.
