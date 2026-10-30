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
the demagnetising field at another one milder (the usual magnetic-circuit behaviour,
an ASSUMPTION, not proven here), monotone.  Then the march F^n(B0) converges to the
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

RESULTS_PLACEHOLDER
