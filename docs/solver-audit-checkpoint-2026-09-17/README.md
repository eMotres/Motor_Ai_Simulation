# Solver audit checkpoint, 2026-09-17

This directory preserves pending work in Git without activating it in the
application. `pending-cad-p2.patch` contains the complete two-source/three-test
candidate: canonical stator-mouth construction/cleanup and complete P2 radial-
cut midpoint constraints. Check target hashes against the manifest before
applying. Do not apply over another agent's changes.

The patch is NOT integrated and its regression baseline is NOT promoted.
Combined isolated regression: 7 passed, 1 failed, 584.89 s. The sole failure is
`p2_noload`: mean 4.1481998567548255e-5 -> 3.921709386350385e-5 N m and ripple
128.72201908278132 -> 154.01844415169822%. It exactly reproduces the CAD-only
effect. Tolerances remain unchanged; full per-case metrics are in regression/.

The 100 mm, 24-slot/28-pole, 15 mm stack fixture has matching raw full/half/
quarter curves at 0 and 46 A after the candidate changes, to about 1.4e-12 N m
over all 72 samples. The full-circle reference uses a temporary common outer-
air mesh override, not the normal graded full-domain mesh. All samples and
spectral bins are preserved in raw-waveform-comparison.json. Missing historical
CAD-only loaded comparisons are explicitly marked pending; the final loaded
full/half/quarter comparison is complete. This is sector parity, not proof of
physical mesh convergence. Cold-field evidence records the previously missing
two of 55 radial-cut midpoint constraints and their repaired continuity.

`analyze_raw_waveforms.py` is an archived analysis script; it expects the
original sandbox tree and is not a standalone reproduction runner. That tree
remains at C:/Users/vadim/.codex/visualizations/2026/09/16/
01a0aaf0-a84f-7d23-9c5b-02df5147e1d6. Large meshes and full sandbox copies are
kept there, outside Git. Artifact hashes describe the preserved evidence bytes.

Next steps: review intentional CAD regression drift, integrate the pending
patch with explicitly justified pins, then complete physical mesh convergence.
The general torque formula, 1 A selector, and separate iron-loss leakage-ramp
removal remain unresolved. See ../solver-torque-validation-plan.md.

Current application changes are separately covered by the previous isolated
124-test combined gate, 89 cache tests, 58 zipper tests, native checks and
three web tests documented in ../../coordination/codex.md. Known TypeScript
baseline diagnostics remain; no new diagnostics were introduced. Only the
already justified Maxwell output-precision baseline value changes in the
application batch. No deployment, restart or push is part of this checkpoint.
