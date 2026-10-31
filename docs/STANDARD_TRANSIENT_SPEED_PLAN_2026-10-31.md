# Standard (sine-current) FEM transient: where the time goes, and a plan

2026-10-31. Owner: "the standard transient first, no PWM for now; x1.28 from #522 is too little".
Scope of this note: profile, literature, plan. No large refactor is made before the owner picks items.

## 1. Measured profile (Erium L90, owner's point, server sandbox)

Point: the owner's run of 2026-10-30 07:36 (run ledger `b752201f...`), CIANO56 400_90, 48s56p, six-phase
2x3ph, 1/8 sector, 120 steps per period, coupled eddy + demag, cold start. Deployed code 9c128d53
(#519 one preparation stage and #522 per-frame speed-up included). One container, 3 CPUs, `nice 10`.
The server was at load 15-18 on 16 threads (five production sandboxes), so the absolute seconds are
pessimistic, but the proportions hold. cProfile was on (it adds roughly 10-20 % to Python-heavy parts).

| Stage | Frames | Wall [s] | Share |
|---|---:|---:|---:|
| mesh build + set-up | - | 14 | 1 % |
| eddy warm-up (march, 4 extension periods + TP-EEC DC correction) | 482 | 866 (together) | 67 % |
| demag pre-pass (4 periods, Br ratchet on the settled field) | 480 | (above) | |
| reported window (1 period) + solver post (rotor eddy harmonics 27 s) | 120 | 166 | 13 % |
| six-phase L_xy probe (full ring, 409 361 dofs, own mesh) | 4 | 149 | 11 % |
| route post-processing (summary, masses; CAD polygons) | - | ~104 | 8 % |
| **total** | **1082 solved / 120 reported** | **1298** | |

Inside the frame march (1044 s, 0.96 s per frame under the profiler):

| Item | Time [s] | Note |
|---|---:|---|
| PARDISO (numeric factorisation + solves) | 540 | 5711 calls; 4.1 Newton solves per frame |
| of which symbolic analysis (phase 11) | 127 | one per frame (the slip pattern changes every frame; the ordering cache hits 842/1087 but the analysis still runs) |
| scipy sparse bookkeeping (assembly scatter, P'(K+T)P reduction, csr add / matmul / tocsr / sort) | ~300 | `csr_plus_csr` 59, `csr_matmat` 70, `coo_tocsr` 41, `csr_scatter.assemble` 63, `tang` 84, `stiff` 43 (cumulative, overlapping) |
| skfem `CellBasis` rebuilt 3543 times | 48 | the same few bases |
| CAD `get_2d_polygons` called 115 times | 87 | shapely sanitising of the same geometry |

Main fact: **89 % of the solved frames are preparation** (962 of 1082). The per-frame cost is
~55 % linear algebra and ~30 % Python/scipy bookkeeping around it.

### TDM on the six-phase machine (one experiment, opt-in switch `SB_TDM_SIX_PHASE=1`)

The six-phase winding is refused by TDM today only as "not validated". The same point with TDM:
torque 1028.7512 -> 1028.7521 N*m (+0.0001 %), ripple 4.1884 -> 4.1890 % (+0.016 %), total loss
2938.9 -> 2939.0 W, core / magnet / shaft loss identical, demag loss 0.634 = 0.634 %, L_xy identical;
TDM report gate PASSED (torque 3.3e-8, ripple 2.6e-6 pp). Wall 962 s against 1298 s here and against
1025 s for the same march without the profiler (PR #522 measurement, 2026-10-30): **x1.07-1.35 only**.
TDM's own fixed costs on this machine (static start 92 s, re-solve 96 s, closure march 55 s, error
estimate 75 s) eat most of the 962 -> 240 frame saving, and its frame-parallel workers have no free
cores on the loaded server. TDM paid x3.5-6 on L155 because the march needed ~17 settling periods there (614 of 650 frames);
L90 settles in 4 + 4.

## 2. Literature, and what the code already has

| Method | What it is | Reported gain | Here |
|---|---|---|---|
| TP-EEC (time-periodic explicit error correction) | correct the slow decaying (DC-like) error of a march once per period | JMAG: steps to steady state cut to ~1/10 | **already in the march** for the solid shaft/sleeve (0th harmonic, `periodic_accel.dc_error_correction`); applied from extension period 2 on L90 |
| Time-periodic FEM / shooting-Newton (TDM) | solve one period with periodic closure, Newton on space-time, GMRES on the wrap state, 0th-harmonic coarse space (Kulchytska-Ruchka & Schoeps) | x3.5-6 on L155 | **exists** (default for 3-phase); six-phase validated above on one point, x1.07-1.35 on L90 |
| Periodic Parareal (PP-IC) | time-parallel periodic steady state | up to 28x on 80 cores (Kulchytska-Ruchka thesis 2021) | needs many free cores; our server is a shared 8-core box: **not now** |
| Harmonic balance FEM | frequency-domain unknowns, motion through moving-band matrices | competitive when the transient is long; a 2024 study found time stepping faster for short transients | a rewrite of the solver, harmonics fixed a priori, demag/eddy nonlinearity awkward: **not recommended** |
| Newton vs fixed point | damped Newton: global linear + local quadratic convergence (Egger, Engertsberger, Radu 2024); local quasi-Newton updates | 2-4x fewer iterations than fixed point | the march **already uses Newton** (4.1 solves per frame); its tangent is a one-sided difference with clamping, TDM has the analytic tangent (`time_periodic.analytic_tangent`) |

## 3. Plan (owner to choose; each item verified on L90 + one small motor, 0.1 % on torque, ripple, losses by part)

Gains are estimates for this L90 point unless marked "measured".

| # | Item | Expected gain | Risk / note |
|---|---|---|---|
| A1 | **Warm start of every coupled pass from the previous pass of the same machine** (the warm library of #506 is off for six-phase and delta; extend it. On this run the legacy slot's seed was "accepted" and the warm-up still started from the static field: to be understood first) | coupled pass 2..n: warm-up 482 -> ~120 frames (#506 measured 108 -> 24 frames on 30 mm) | eddy history only; Br from the same operating point only (the 2026-09-05 rule) |
| A2 | **Br continued from the previous coupled pass** + one verification pre-pass period (as sweeps do since 2026-09-06) | pre-pass 480 -> 120 frames on passes 2..n | **owner decision**: the reproducibility rule says Br only from the same operating point |
| A3 | coarse-time preparation: warm-up marched at 40 steps/period, then 1-2 fine periods (the PWM "mixed" idea) | prep 962 -> ~550 frames on a cold run | changes the start of the reported window: needs the 0.1 % check |
| A4 | TP-EEC applied from the first warm-up period and to more than the 0th harmonic | warm-up 4 -> 2-3 periods | small code; check the settle gauge |
| A5 | TDM for six-phase as default (switch exists on branch) | x1.07-1.35 measured; more with free cores | validated on one point only; validate on a second duty |
| B1 | analytic tangent in the march Newton (reuse TDM's) | 4.1 -> ~3 solves per frame, ~-20 % of frame time | converged result unchanged at the 1e-7 residual |
| B2 | precomputed reduction P'(K+T)P and assembly scatter on the frame's fixed pattern (no csr matmul/add per iteration) | ~-150 s of the ~300 s bookkeeping, ~-15 % | bit-identity is not guaranteed (summation order); 1e-12 level |
| B3 | skfem bases and CAD polygons cached per geometry | -48 s and -87 s per run | pure caching |
| B4 | PARDISO symbolic analysis reused across frames with the same slip pattern | up to -127 s (one period of patterns) | memory: one handle per pattern; needs a study |
| B5 | MKL GNU threading, 2 threads per solve, when the server has free cores | x1.3-1.4 on factorisation (fem-speed measurement) | deploy decision; no gain while the server is full |
| C1 | **L_xy probe reused** across passes of one machine/current (done on branch `claude/pwm-coupled-speed`) | -149 s on every pass after the first | tolerance 2 % current, 2 deg, 5 K magnet |
| C2 | coupled loop starts from the duty's last converged temperatures (not the 150/100 C seed) | -1 of 3-5 passes | the seed is a request field today |
| C3 | held current and temperatures updated together (one secant on both) | held loop 5 -> 3 passes | hold fix on the PWM branch is a prerequisite |

Estimated effect: **cold single run x1.8-2.2** (A3 + A4 + B1 + B2 + B3); **each coupled pass after the first
x3-4** (A1 + A2 + C1 + B1-B3); **a whole coupled loop of one duty x3-5** (plus C2/C3: fewer passes).

Recommended order (cheap and safe first): C1 + B3 (pure reuse/caching) -> B1 -> A1 -> A4 -> B2 -> A3;
A2 needs the owner's decision; A5 needs a second validation point; B4/B5 later.

## 4. Raw outputs

Server `/opt/motres/compute/jobs/std-speed/`: `prof_base.json` (march, cProfile top 40 by tottime and
cumulative), `prof_base.log` (timestamped stage log), `tdm6.json`/`tdm6.log` (TDM six-phase),
`cmp_march_tdm6.txt` (560 scalars compared).

## Sources

- TP-EEC: [JMAG W-SE-72](https://www.jmag-international.com/whitepapers/w-se-72/), [JMAG JFT042](https://www.jmag-international.com/tutorial/jft042_tp-eec), [Igarashi et al., IEEE Trans. Magn. 2012 (HUSCAP)](https://eprints.lib.hokudai.ac.jp/dspace/handle/2115/48389)
- Periodic Parareal: [Bast, Kulchytska-Ruchka, Schoeps, Rain, arXiv 1902.08277](https://arxiv.org/pdf/1902.08277), [Kulchytska-Ruchka, thesis TU Darmstadt 2021](https://tuprints.ulb.tu-darmstadt.de/19280), [Schoeps, Niyonzima, Clemens, arXiv 1706.05750](https://arxiv.org/abs/1706.05750)
- Harmonic balance: [Gyselinck et al., COMPEL 2003](https://www.emerald.com/insight/content/doi/10.1108/03321640310482977/full/html), [BLDC HBFEM vs time stepping, Arch. Electr. Eng. 2015](https://www.czasopisma.pan.pl//Content/85125/PDF/10_paper.pdf)
- Newton / fixed point: [Egger, Engertsberger, Radu, arXiv 2408.12283](https://arxiv.org/html/2408.12283v1), [arXiv 2403.18520](https://arxiv.org/html/2403.18520v1), [local quasi-Newton, arXiv 2409.01015](https://arxiv.org/html/2409.01015v2)
