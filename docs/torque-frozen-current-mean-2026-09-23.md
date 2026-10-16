# Frozen-current full-period discrete virtual-work checkpoint — 2026-09-23

Run16 is **not a transient**. It solved 48 adjacent rotor-position pairs on the unchanged run15 GEO30 P2 mesh. At center shift `3j` (j=0…47), the two equilibria use shifts `3j−1` and `3j+1` and both hold run15 frame-j branch currents fixed. The slip-cell displacement is `δ=0.006233318757 rad` (0.357142857°). A guarded callback persisted each selected full P2 `A_z`, quadrature B, Br, phase current/linkage, angle and convergence record before stopping after the 96th state; static mesh, nonlinear B-H, PM and winding sources were saved once. The run finished in 59.48 s; all 96 Newton frames converged and source hashes remained unchanged. No live API, config or production selector was changed.

`scripts/torque_frozen_mean_review.py` compares the saved source/material/mesh arrays with run15, checks all 96 pair angles and currents, constant Br, no eddy/demag/frozen permeability, and source–linkage reciprocity. It reconstructs the accepted pointwise P2 magnetic potential `U` from `∫H dB`, linear `½νB²` and PM `−Hc·B`. For each saved state it forms `W′=stack·sectors·Σ I_branch(A·f_coil)−U` and each pair's `ΔW′/(2δ)`. This uses branch current in the actual winding source without an extra parallel-path division. Reconstructed H differs from the production law by at most `4.66e−10 A/m`; the permeability floor is inactive at samples and integration-path knots. PM field/load pairing is within `2.22e−16 J`, and source/terminal linkage within `2.17e−19 Wb`.

| Diagnostic on common run15 first-period centers | Mean N·m |
|---|---:|
| Run16 mean of 48 frozen-current ±one-cell secants | **0.4095018981** |
| Run15 cubic/Gauss terminal path work | 0.4096487787 |
| Run15 hybrid mean | 0.4096471902 |
| Run15 raw Maxwell mean | 0.4081997845 |

The finite secant mean is **0.0001468806 N·m below** the cubic terminal path. Its 48 local secants range from 0.4021582 to 0.4174077 N·m (peak-to-peak 0.0152495 N·m). A pure fundamental's symmetric finite-displacement factor at this step is `sin(7δ)/(7δ)=0.9996827204`; higher spatial harmonics and saliency have different factors. That value is an interpretation aid, **not** a universal correction to the motor secants. The earlier center-only ±one/±three-cell study already showed 0.00120895 N·m local step sensitivity, and no full-period slip-step refinement or mesh-error bound is available.

**Status: uncertified discrete validation.** The agreement in scale and sign between independent winding-source virtual work and terminal path work is useful, but the 0.0001469 N·m gap has no justified bound. The integer-weld spatial derivative and integrated electromechanical port-energy identity remain unproved. A validated endpoint energy correction could in principle avoid requiring exact periodic closure; the current magnetic-potential difference is only an arithmetic comparison. No waveform sample was filtered and no replacement torque method was selected.

Read-only verification: `python scripts/torque_frozen_mean_review.py` completed in 2.03 s from saved states. `python -m pytest tests/test_torque_frozen_mean_review.py --noconftest -o addopts= -p no:cacheprovider -q` passed two synthetic tests. Codex Sol reviewed the physics; parent handles journal/dataset logging.
