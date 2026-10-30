# One preparation stage: d-axis calibration + eddy warm-up + demag pre-pass (2026-10-30)

Owner, 2026-10-30, about the stage strip of a Simulation run: «давай объединять первые
три процесса в один, пусть сразу всё делается вместе, не надо тратить дополнительно время
на разные операции, а то не дождёшься» ("merge the first three processes into one, do it
all together, don't spend extra time on separate operations, or we will never get there").

His run (CIANO56 400_80, L90, 48s56p, 152.1 A, 850 rpm, 108 -> 120 steps, coupled eddy +
demag, current drive; ledger `b752201f7b87a1db-172386a4…`, 2026-10-30 07:36) solved
**1682 frames in 1281.7 s** for a **120-frame** answer:

| stage | frames | why |
|---|---|---|
| d-axis calibration | 24 | new cross-section (cached per geometry afterwards) |
| eddy warm-up | 722 | probe + 6 extension periods; the shaft (Al, slow body) needed a DC correction and its verification periods |
| demag pre-pass | 840 (7 periods) | Br stopped moving after period 2 (2.0e-5 vs tol 1e-3), but the rotor-image rule demands L = 7 periods |
| reported window | 120 | |

## Why the pre-pass took 7 periods, not 1-2

Since the fourth Codex review (2026-10-04) a demag period is settled only once the
ROTOR-IMAGE HISTORY is complete: on a fractional-slot rotor each magnet meets, in one
electrical period, only the rotor positions of 1/L of the history its pole-pair images
share (L = order of the period relabelling, 7 on a 6s7p sector), so the rule ran L whole
periods.  The server log of the owner's run:

```
pre-pass period 1 moved Br by 0.00634  image gap 2.2e-05 / 7.6e-04   complete False (1 of 7)
pre-pass period 2 moved Br by 2.02e-05 image gap 4.6e-06              complete False (2 of 7)
pre-pass period 3 moved Br by 1.14e-06                                 complete False (3 of 7)
... periods 4-7 move Br by < 1e-6 each
```

Five of the seven periods (600 frames, ~7 minutes) moved nothing.

## What changed

### 1. The image history is TRANSFERRED, not marched (`time_periodic.cycle_min_br`)

One electrical period later the rotor has turned one pole pair and the stator currents
are where they were, so element `e` meets EXACTLY the field element `image(e)` met this
period, at the same discrete instants (the march already relabels Br by this map between
pre-pass periods).  Over L periods it therefore meets the fields of its whole relabelling
cycle, and the irreversible ratchet — a running minimum — ends at the minimum over that
cycle.  Marching the L periods with the field held fixed is thus *exactly* "each element
takes the minimum Br of its cycle" (pinned by
`tests/test_time_periodic.py::test_the_image_transfer_is_the_l_period_march_without_feedback`).

What the march adds is only the FEEDBACK (a weaker magnet changes the field).  That is
what the periods after the transfer measure, with the unchanged rules: the period's Br
change (<= 1e-3 worst-magnet area mean), the observable drift between the last two
periods (torque 0.1 %, ripple max(0.05 pp, 1 %) x the safety fraction), and now also
the transfer's own move (<= 1e-3, else the next window would open on a magnet no
measured period had).  The image history counts as complete once a transfer has been
applied and at least two periods exist (one to burn in, one to verify the feedback).

Why both iterations stop at the same state: the period map F (march one period +
relabel) is deflationary (Br never rises) and, as long as a weaker magnet never makes
the demagnetising field at another one milder, monotone — an ASSUMPTION, and the 30 mm
case below shows it does not always hold (there the transfer lands 0.13 pp Br on the
conservative side).  Then the march F^n(B0) converges to the
greatest fixed point B* below B0; the transfer T (cycle minimum) gives T(F(B)) >=
F^L(B) — it is the feedback-free L-period result, and feedback only lowers Br further —
so the accelerated sequence stays >= B*, and its limit is a fixed point of F (the stop
rules require that the march and the transfer both move nothing there) below B0, hence
<= B*: it is B*.  Without the assumption the stop rules still guarantee that the
reported window opens on a fixed point of the march; that it is the march's OWN one is
what the A/B below measures.

`SB_DEMAG_IMAGE_TRANSFER=0` restores the L-period rule (A/B only).  Same rule in the
TDM full pre-pass.  The optimizer-only TDM *shortcut* (1/6-period windows of one worst
magnet) is untouched and still not qualified for reported numbers; the transfer is not
that shortcut — every magnet marches whole periods, only the image history is
propagated.

### 2. The ratchet switches on inside the eddy verification (`_prep_overlap`)

The 2026-09-05 rule stays: the ratchet may not see the σ·∂A/∂t START-UP transient
(cold vs warm-cache starts burned in different worst fields, 3 % torque / 8 pp Br).  But
it no longer waits for the eddy VERIFICATION: when the eddy gauge itself passes
(whole-period means per conductor group within 2 %) and the march extends only to
verify it (the accelerator's MIN_VERIFY periods after a DC correction / RRE jump, its
slow-mode checks), the ratchet is switched on and those periods are also the first demag
pre-pass periods — eddy and Br judged together, one record of each per period, the Br
map relabelled at each eddy splice exactly like the eddy state.  The ratchet-active
frames re-solve on a trip like pre-pass frames, and a Br move inside them no longer
counts as an eddy "not quiet" (`_dm_moved_in_warm`) — the demag fixed point judges it.

On the owner's run the gauge passed at 482 frames (shaft 1.04 %); the handoff came at
722.  `SB_PREP_OVERLAP=0` restores the separate pre-pass behind the handoff.

### 3. One stage on the strip (`progress_stages.prepare`)

`calibration`, `warm_up` and `demag_prepass` are one stage `prepare` ("Preparation"):
calibration frames + the march's one negative-frame counter, monotonic, adaptive (the
total grows, shown "n / N+"), the breakdown in the tooltip ("d-axis cal. 24 + eddy
warm-up 482 + eddy + demag 240 + demag pre-pass 120").  The solver's total adds the
pre-pass periods that are certain but not yet queued.  The ETA is now the WHOLE run's
(`progress_stages.eta_s`): every frame still to come in every stage at the rate the
running stage solves them — before, it was the current stage's counter only, so it
ended with the stage.

### d-axis calibration: unchanged

It is cached per cross-section (fingerprint without the inert keys) in memory and on
disk, so a repeated run of the same machine pays nothing; the owner's 24 frames were a
new cross-section.  It cannot be derived from the warm-up frames: those carry the load
current, and armature reaction moves the ψ_A peak by the load angle — the calibration
is a no-load measurement by definition.  Data point for a later decision: the 17 cached
cross-sections of this 48s56p topology span 59.953-60.004° el (the symmetry argument of
`_DAXIS_SYMMETRIC_KEYS`), so a topology-level reuse for interactive runs would be
numerically safe; it is a policy change (2026-09 "a cache may not answer for a machine
it never measured") and is not made here.

## Measured

### The owner's point, old vs new (server sandboxes, 4 CPUs each, run side by side)

The owner's exact request (route `get_fem_transient`, `ledger_probe` matched his stored
key), cold (`SB_NO_WARM_CACHE=1`, which also keeps the warm library out — his run was a
cold start), d-axis from the cache (60.0039° both).  "old" = the branch head
`e51ab59d` (the deployed image); it reproduced his stored result bit for bit.

| | old | new | Δ |
|---|---|---|---|
| frames solved | 1682 | **1082** | −36 % |
| ratchet frozen (eddy warm-up) | 722 | 482 | |
| ratchet active | 840 (7 periods, after the handoff) | 480 (4 periods, all inside the eddy verification) | |
| solver wall | 1327 s | **898 s** | −32 % |
| route wall (incl. post-processing) | 1610 s | 1091 s | |
| T_avg | 1028.75149 N·m | 1028.75123 N·m | −2.5e-7 |
| ripple | 4.188364 % (43.0879 N·m) | 4.188388 % (43.0881 N·m) | +0.00002 pp |
| Br kept (vol) / BH kept | 99.366 / 98.736 % | 99.366 / 98.736 % | 0 |
| per-magnet spread | 0.001 % | 0.000 % | |
| P_mag / P_shaft / P_cu / P_fe | 20.227 / 114.617 / 1740.24 / 524.991 W | 20.227 / 114.617 / 1740.239 / 524.990 W | ≤ 1e-6 |
| steady_state, demag_settled | True, True | True, True | |

New run's pre-pass records: period 1 moved Br 6.3e-3 (transfer 2.2e-5), period 2
6.4e-9 (torque drift 1.8e-5, ripple 0.003 pp: settled), periods 3-4 < 3e-10.  The demag
was settled at 722 frames; the eddy gauge then took two more periods to re-verify the
shaft after the Br drop — which the separate scheme never re-checked (its pre-pass
followed the eddy handoff), so the merged stage is also the more careful one.

### The pinned eddy+demag case (`p2_demag_eddy`, 30 mm 12s14p, 12 steps, 60 A), cold

The physics gate is already red on this branch for unrelated reasons (environment drift
since 10-05, a demag drift between 10-18 and 10-30), so the comparison is against the
branch head, not the stored pins.  Every other regression case is untouched by
construction (the transfer acts only at the end of a ratchet period; the overlap only
in a coupled-eddy + demag current-drive march).

| | old TDM | new TDM | old march | new march |
|---|---|---|---|---|
| pre-pass periods | 8 (cap) | 4 | 8 (cap) | 4 |
| frames solved | 108 | 60 | 134 | 86 |
| wall | 400 s | 163 s | 430 s | 251 s |
| demag_settled / steady_state | **False / False** | True / True | **False / False** | True / True |
| per-magnet spread | 0.37 % | 0.00 % | 0.37 % | 0.00 % |
| Br kept (vol) | 96.924 % | 96.790 % | 96.924 % | 96.790 % |
| T_avg | 0.385175 N·m | 0.384466 N·m (−0.18 %) | 0.385175 | 0.384466 (−0.18 %) |
| ripple | 0.7005 % | 0.8182 % (+0.12 pp) | 0.7006 % | 0.8182 % |
| P_cu / P_fe / P_mag | 110.235 / 4.4206 / 1.422 W | 110.221 / 4.4159 / 1.419 W | | |

Here the branch head never reached its own fixed point: at the 8-period cap its run is
labelled `demag_settled False`, `steady_state False`, the magnets still disagree by
0.37 %.  The new run reaches an image-symmetric, settled state in 4 periods.

Where the old march itself goes when it is given room (`SB_DEMAG_PREPASS_MAX=40`, branch
head, march): settled after **11 periods** (170 frames, 410 s) at **T 0.385094 N·m,
ripple 0.8046 %, Br kept 96.923 %, per-magnet spread still 0.371 %**.  Its per-period
ripple wanders 0.59-0.93 % while Br creeps (period changes 2.7e-2, 8.3e-3, 1.7e-3,
6.6e-4, 2.5e-4, 6.6e-5 … 4.2e-6), which is why the capped run's 0.70 % was one
sample of that wander.  Against this converged branch-head answer the new one is:

| | branch head, own fixed point | new | Δ |
|---|---|---|---|
| T_avg | 0.385094 N·m | 0.384466 N·m | −0.163 % |
| ripple | 0.8046 % | 0.8182 % | +0.014 pp |
| Br kept (vol) | 96.923 % | 96.790 % | −0.133 pp |
| per-magnet spread | 0.371 % | 0.000 % | |
| periods / frames / wall | 11 / 170 / 410 s | 4 / 86 / 251 s | |

The −0.13 pp is not noise but the one place the monotonicity assumption above does
not hold on this heavily demagnetised case (Br min 0.10 of Br0): a magnet that meets a
rotor position LATE, when its neighbours are already weaker, sees a milder field than
the magnet that met it in the first ratchet period (the solver's own note: "the
stronger magnet drives the stronger demagnetising field").  The march keeps that
order-of-visit memory as a permanent 0.37 % magnet-to-magnet spread — the artefact the
2026-09-05 work calls "the spread WAS the bug, not the machine"
(`test_the_magnets_end_up_identical`).  The transfer gives every magnet the harshest
exposure any of its images had, so the magnets are identical and the answer is on the
conservative side by 0.13 pp Br / 0.16 % torque.  On the owner's machine (Br kept
99.37 %, period-2 change 2e-5) the two agree to 2.5e-7.  `SB_DEMAG_IMAGE_TRANSFER=0`
gives the old rule for anyone who wants the march's order-dependent answer.

### Reproducibility (new code, server sandbox): 24 passed

`tests/test_demag_reproducible.py` (cold / warm-cache-seeded / `SB_NO_WARM_CACHE`,
0.3 % torque / 0.3 pp Br, per-magnet spread <= 0.8 %, the pre-pass a whole number of
periods, the warm-library pair) and `tests/test_warm_seed.py` (the published Br map,
`SB_SEED_FROM_PREVIOUS` skipping the pre-pass, the interactive path never continuing
another run's magnet): **24 passed** in 22.5 min.  Warm-seed legs: cold / seeded /
flag-off all T_avg 0.384466 N·m, Br kept 96.790 %; the seeded sweep eval discards 26
frames instead of 74.  Locally (no FEM): `tests/test_progress_stages.py` 20 passed,
`tests/test_time_periodic.py -k image_transfer` passed, `node --test
progressStages.test.mjs` 13 passed.
