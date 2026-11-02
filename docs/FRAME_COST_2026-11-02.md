# Cost of one transient frame (2026-11-02)

Goal: make one frame of the 2-D sliding-band transient cheaper without changing results. Follows
`STANDARD_TRANSIENT_SPEED_PLAN_2026-10-31.md`, whose items B2 (PARDISO symbolic reuse, section 3) and B4 (assembly, section 2) this is.

Point: the owner's Erium L90 run (ledger `b752201f...`), CIANO56 400_90, 48s56p six-phase, 1/8 sector,
108 steps per period, coupled eddy + demag, **cold** (`SB_NO_WARM_CACHE=1 SB_WARM_LIB=0 SB_WARM_CATALOG=0`),
deployed code 7eb8f5ac. One sandbox, 4 CPUs, cpu-shares 256, `nice 10`, MKL sequential (as in production),
frozen copy of the owner workspace, shared catalog read-only. Timers: `scripts/bench/frame_cost_profile.py`
(wall-clock wrappers, ~2 µs per call; PARDISO phase 23 issued as 22 + 33 to time them apart).

## 1. Profile of the deployed code (idle server)

Wall 375 s, march 288 s, 650 frames, 2 889 PARDISO factorizations (4.4 per frame), 660 symbolic analyses
(434 of them with a cached ordering).

| Item | s | per frame |
|---|---:|---:|
| PARDISO numeric factorization (phase 22) | 99 | 152 ms |
| PARDISO symbolic analysis (phase 11) | 57 | 88 ms |
| PARDISO back-solves (phase 33) | 38 | 58 ms |
| sparse bookkeeping: Pfᵀ(K+T)Pf products, residual products, bmat, the Cholesky checks, the per-solve upper triangle, COO fallback of the tangent scatter, N-sized CSR adds | ~68 | 105 ms |
| element kernels: secant stiffness and tangent data, ∇A at quadrature points, B-H curve | ~35 | 54 ms |
| per-frame post-processing (torque, losses, histories) and the L_xy probe call | 49 | |

## 2. B4 — the reduced system assembled in place (`simulation/p2_frame_plan.py`)

Per run, every global entry that does not touch a rotor-side slip dof is mapped once to its position in the
upper triangle of the reduced bordered matrix `[[Pfᵀ(K+T+Msd)Pf, −PfᵀG], [·, S·Δt]]`; per frame only the
entries on the slip ring are re-mapped (the union-find roots are stator dofs, so nothing else moves; checked
every frame). A Newton iteration is then one `bincount` of the iron element data straight into the CSR that
PARDISO reads (mtype 2), and the residual is an element-level product instead of a fresh `K`.
The same sums of the same signed entries as before, added in a different order: equal to round-off, not bit
for bit. Any doubt (a dof outside the slip set moving, a non-positive diagonal, a Cholesky failure) takes
the SciPy path for the rest of the run, loudly. `SB_NO_FRAME_PLAN=1` switches it off.
Scope: the coupled-eddy Newton under current drive (`P2Drive.eddy_solve`, no series strand paths),
which is every eddy frame of a standard transient; the voltage-drive and magnetostatic Newtons are unchanged.

Same point, same server load (another sandbox running), back to back:

| | base 7eb8f5ac | B4 |
|---|---:|---:|
| frames | 650 | 650 |
| wall | 417.6 s | **352.0 s** (−15.7 %) |
| march (solve) | 330.4 s | 265.8 s |
| per frame (wall / frames) | 642 ms | 542 ms |
| PARDISO 22 / 11 / 33 | 110 / 65 / 42 s | 111 / 65 / 42 s |
| eddy Newton outside PARDISO | ~120 s | ~52 s |
| Cholesky solves through the plan | 0 | 2 720 of 2 751 |

Accuracy: 574 physics scalars compared, 458 bit-identical, worst 7.5e-9 relative
(`demag_settle.drift.ripple_pp`). Torque 1029.3885 N·m, ripple 4.0769 %, total loss 2942.853 W, iron 526.33 W,
magnets 20.345 W, shaft 114.838 W, AC copper 440.845 W: identical to the printed digit.

Tried and dropped: batched-matmul element kernels (tangent 3.2 → 2.8 ms, stiffness slower) — not worth a
second code path.

## 3. B2 — one PARDISO analysis per window of frames

The brief assumed the pattern does not change between frames. It does: the slip weld moves the rotor-side
ring couplings every frame (L90: 14 298 of 400 752 upper-triangle entries differ frame to frame), so the
analysis was already reused only inside a frame. Measured on 24 consecutive L90 matrices (n = 61 395):

| | ms |
|---|---:|
| phase 11, METIS ordering | 230 |
| phase 11, ordering from the cache | 45 |
| phase 22 (numeric) / 33 (solve) | 35 / 13 |

Rejected: frame 0's ordering on frame 1 costs +42 % factor fill (+120 % after 23 frames); one PARDISO handle
per pattern holds ~24 MB symbolic + ~23 MB numeric × ~380 patterns; AMD ordering (58 ms) costs +8 % fill.

Done: the frame plan builds one pattern for a window of W consecutive rotor shifts (the union of their
slip-ring entries; a frame's absent entries are explicit zeros, so each factorization is exact) and every frame
of the window reuses that analysis. Windows are aligned on the absolute shift (`window_shifts`), so a later pass
over the same rotor positions meets the same union pattern and its ordering in the cache. The frame loop passes
`shift_hint` (Pro, shift, step, projection); without it a window is one frame. `SB_PLAN_WINDOW` (default 2).

L90 cold, 650 frames, back to back under the same load:

| | wall s | march s | analyses | phase 11 s | phase 22 s | worst rel. dev. |
|---|---:|---:|---:|---:|---:|---:|
| base 7eb8f5ac | 419.7 | 330.5 | 659 | 64.3 | 109.4 | — |
| B4 (W = 1) | 350.1 | 265.2 | 659 | 64.7 | 109.0 | 7.5e-9 |
| **W = 2 (default)** | **325.3** | **238.9** | 342 | 36.5 | 113.3 | 2.5e-9 |
| W = 3 | 324.5 | 236.9 | 237 | 27.9 | 118.9 | 5.3e-9 |
| W = 4 | 322.2 | 235.4 | 182 | 22.7 | 121.7 | 1.3e-8 |

W = 2..4 are equal on a cold run; on a repeat pass (orderings cached) only the extra fill remains, which W = 2
pays least (per frame ~227 ms against 241 / 236 ms from the matrix study). Base → B4 + B2: wall −22.5 %,
s per frame 0.646 → 0.500. Torque 1029.3885 N·m, ripple 4.0769 %, every loss part, Br kept 99.365 %,
V_LL 546.8 V peak / 374 V rms: identical to the printed digit.

## 4. Next levers (not done here)

- The numeric factorization is now the largest item (113 s of 325 s): 4.4 factorizations per frame. Reusing a
  frame's factor for the later Newton iterations (a chord step, refactor only on slow convergence) would cut
  most of them; the converged result stays at the same 1e-7 residual.
- The magnetostatic and voltage-drive Newtons can use the same plan (no border / a circuit border).
- MKL with 2 threads per solve (B5 of the plan): ×1.3-1.4 on factorization when the server has free cores.

## 5. Raw outputs

Server `/opt/motres/compute/jobs/frame-cost/`: `p0_base` (first profile), `p2_b4` / `p3_base` (section 2), `w1`..`w4` / `base2` (section 3),
`sym_bench*.py` (symbolic-analysis study on 24 dumped L90 matrices), `t_unit.log` (tests).
