# Low-current torque selector checkpoint, 23 September 2026

The saved GEO30, 12-slot/14-pole, NS2 cases use the same 1.4/0.35 mm spatial mesh, 144 slip cells per electrical period, 48 endpoint-excluded angular samples, and 15,000 rpm imposed synchronous current. Eddy, demagnetization, incremental permeability, and torque filtering were off. All four cases completed with converged fields. Case 4 (2 A peak per branch) has not run.

| Peak current per branch (A) | Raw Maxwell mean (Nm) | Space-vector candidate (Nm) | Spectral terminal-work candidate (Nm) | Selected mean (Nm) |
|---:|---:|---:|---:|---:|
| 0 | 0.000106686089 | 0 | 0 | 0.000106686089 |
| 0.5 | 0.002666779444 | 0.002570250688 | 0.002570250688 | 0.002666779444 |
| 1.0 | 0.005226917746 | 0.005140465326 | 0.005140465326 | 0.005226917746 |
| 1.001 | 0.005232037675 | 0.005145605691 | 0.005145605691 | 0.005145605691 |

The 1.000 to 1.001 A selected mean falls by **0.000081312055 Nm**, while the space-vector candidate rises by **0.000005140365 Nm** and raw Maxwell rises by **0.000005119930 Nm**. This is a selector discontinuity, not evidence that either candidate is the physical mean. The zero-current raw Maxwell mean of 0.000106686089 Nm is a possible mesh/stress bias; these data do not isolate physical cogging.

The independent offline [review script](../scripts/torque_low_current_review.py) computes mean terminal work using all DFT bins of raw current and corrected reciprocal flux linkage, with frequency in **mechanical radians** and no extra division by pole pairs. For these nearly fundamental waveforms it agrees with the space-vector candidate to numerical precision. Its validity depends on periodic continuation of the sampled terminal state; the cases did not capture endpoint magnetic energy or low-current virtual work. A 48-point spectral result alone cannot approve replacement of the selector, and zero-current terminal work is necessarily zero even if cogging exists. [Analytic tests](../tests/test_torque_low_current_review.py) verify fundamental plus fifth-harmonic work, signed angle reversal, current sign, and parallel scaling.

Provenance: cases 0–2 retained unchanged source hashes. Case 3's pre-run source hashes match cases 0–2 and its raw outputs and convergence checks were saved. An unrelated agent changed `sb_postproc.py` on disk during that run, after the process imported the prior version; the parent's diff audit found snapshot/retention metadata changes, not torque-method changes. Its end-of-run source-hash postcheck therefore failed. Case 3 was retained and was **not rerun**. This is a qualified provenance finding, not a claim that the disk source remained unchanged.

The next bounded physics discriminator is a **1 A per-branch full-period frozen-current virtual-work mean** on this same mesh: 48 center currents, each held fixed at one slip-cell negative/positive equilibrium displacement (96 states), with raw full-field energy/source data saved before validation. Compare its paired-secant mean and phase pattern with Maxwell and terminal candidates, and quantify the finite displacement bias. This requires a separately authorized solve; none was performed for this review. Noninteger windows, eddy/demagnetization modes, other symmetries, and no-load cogging remain outside this gate. The selector remains unchanged.

Verification: `python -m pytest tests/test_torque_low_current_review.py --noconftest -o addopts= -p no:cacheprovider -q` passed (5 tests). The read-only review command is `python scripts/torque_low_current_review.py`.
