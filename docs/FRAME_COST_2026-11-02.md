# Cost of one transient frame (2026-11-02)

Goal: make one frame of the 2-D sliding-band transient cheaper without changing results. Follows
`STANDARD_TRANSIENT_SPEED_PLAN_2026-10-31.md`, whose items B2 (PARDISO symbolic reuse) and B4 (assembly) this is.

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

## 3. Raw outputs

Server `/opt/motres/compute/jobs/frame-cost/`: `p0_base` (first profile), `p2_b4` / `p3_base` (the table above),
`sym_bench*.py` (symbolic-analysis study on 24 dumped L90 matrices), `t_unit.log` (tests).
