# Eddy march from a loaded static start: L155 shaft slow mode (2026-09-24)

Claude Opus 5.5 (`claude-opus-5-5`) did this as one sub-agent, with no escalation, on branch
`pre-migration-freeze-2026-09-15` at e1b9c72. The owner approved the test («стартовать со статики — запускай»).
Hypothesis under test: the L155 shaft does not settle because the march starts
from a PM-only static field. On that view the armature-reaction DC has to soak into the steel shaft,
and a loaded static start would leave only the fast harmonic part to settle.

**Verdict: refuted as a remedy. The mechanism is partly right: the slow state is the rotor-frame DC
magnetisation of the steel shaft wall. No solver change is committed.** The evidence and the
honest options are below, and the owner has to choose one (task step 3: stop before a modelling or
method change).

## 1. Facts

- **The start was already loaded.** `fem_solver_2d` (the static start block, 3045465) solves
  `p2_drive.eddy_static_state` with the source's currents at the start angle, which are the
  operating stator currents. U_b = I_b/S_b in every wire. It uses the same nonlinear B-H and
  magnet state as the march. So it is PM plus armature reaction, not PM only. The log line
  "cold start from the STATIC field at frame -3" appears on every L155 run.
- **L155 shaft** (CIANO10 200 opt / L155 motor, 12s/10p, half model, anti-periodic, q = 5):
  - It is a hollow tube: r = 21.5 to 26.5 mm, so the wall is 5 mm, with an air bore.
  - The material is Steel_42CrMo4_QT: σ = 4.4 MS/m, and it has a B-H curve (μ_r up to about 1000).
  - Part state is `included`. `reference` would give identical physics.
  - It is a U = 0 cut body: the image half carries the opposite current.
  - It meshes to only 470 P2 dofs in the half model, which is 1 to 2 elements across the wall.
- **Rotor between the magnets and the shaft**: laminated B10AHV900M back iron (σ = 0) of 6 mm. The
  magnets are N52UH (σ = 0.556 MS/m, five ∫J = 0 bodies). There is an M40X carbon sleeve of
  2.5 mm.
- **Duty**: 14 200 rpm, f_e = 1183 Hz, T_e = 0.845 ms, 562 A line (Δ), γ = 15°, eddy and demag on.

### τ of the conducting rotor bodies (in electrical periods of the duty)

Wall diffusion slowest mode: τ = μ0·μ_diff·σ·d²/π² when the field sits at both faces, and 4× that
when it enters from one face only (the OD, with the bore side flux-free). μ_diff is taken from the
library B-H curve.

| machine / duty | T_e | shaft wall | τ_wall at B = 0.3 / 0.8 / 1.2 / 1.5 T (2-side … 1-side) | magnets μ0σh²/π² | sleeve |
|---|---:|---|---|---:|---|
| L155 rated (14 200 rpm) | 0.845 ms | 5 mm, 42CrMo4 | 20…79 / 16…66 / 4…16 / 0.7…2.6 | 0.08 | CF, ≪ 1 |
| L13 rated (1000 rpm, 24s/28p) | 4.29 ms | 2 mm, 42CrMo4 | 0.6…2.5 / 0.5…2.1 / 0.1…0.5 / 0.02…0.08 | 0.001 | none |
| Ø40 L12 rated (13 000 rpm) | 0.659 ms | 2 mm, 42CrMo4 | 4…16 / 3.4…13.5 / 0.8…3.2 / 0.1…0.5 | 0.004 | none |

The time constant measured on the L155 march fits the wall mode:
- The rotor-frame DC current loss falls ×0.47 every 10 periods. That is an amplitude τ of about 27 T_e (23 ms).
- The loss tail's τ is about 18 T_e.
- The magnets and the sleeve are fast. The L13 wall is fast. The Ø40 wall is intermediate, and it
  settles inside the four gauge periods at its milliwatt level.

## 2. What the solved fields say

Harness (scratchpad `ls/`):
- `patch_dump.py` adds an opt-in dump of the shaft and magnet dofs per frame to a SNAPSHOT copy
  of the solver. The repo is never touched.
- `decomp.py` does the decomposition.
- `dccmp.py` compares start states.
- Cases run through `nf/run_case.py` (saved duty, solver-direct, cold). `motor_ai_sim.__file__` is
  asserted in every result.

**Decomposition.** On the periodic orbit the march is T_e-periodic in its (remapped) dof labels. The
label map over q = 5 periods is the identity for 465 of 470 shaft dofs; the other five sit on a
sector line and are slaved. So
`J_DC = σ·(A(t0 + 5T_e) − A(t0))/(5T_e)` at each material point is the rotor-frame DC current, and it
is zero on the orbit. P_DC = ∫σ⁻¹J_DC², and P_AC = P_total − P_DC (exact for the discrete means).

**Reference: HEAD, one march of 70 periods** (`SB_EDDY_MAX_PERIODS=70`, 2594 frames, 2280 s on a
loaded box):

| 5-period window starting at period | 1 | 2 | 5 | 10 | 20 | 30 | 40 | 50 | 60 | 66 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| P_shaft total [W] | 14.11 | 9.87 | 7.28 | 6.11 | 5.26 | 4.90 | 4.70 | 4.59 | 4.52 | 4.49 |
| rotor-frame DC current P_DC [W] | 7.54 | 0.65 | 0.24 | 0.11 | 0.040 | 0.017 | 0.008 | 0.004 | 0.002 | 0.001 |
| harmonic P_AC [W] | 6.57 | 9.22 | 7.04 | 6.00 | 5.22 | 4.88 | 4.69 | 4.58 | 4.52 | 4.49 |

- Only the first rotor period carries a large DC-current transient (the snapshot relaxing). After
  that, the slow decay is in the **harmonic** loss. A slow DC-state error with almost no current of
  its own shifts the harmonic currents of the skin layer. Two couplings do this: the steel's μ(B),
  and the slot-modulated permeance seen from the rotor.
- At 70 periods the run was still not settled: shaft residual 6.15 %, P_shaft 4.48 W.
- The tail extrapolates to about 4.40 W. The 16-period capped value of 5.69 W therefore over-reads
  by about 1.3 W, which is 5·10⁻⁶ of the 280 kW. Every other reported value moved by ≤ 0.06 %
  (T 187.7697 vs 187.7744 N·m, P_mag 85.61 vs 85.56 W, P_cu,AC 600.74 vs 600.67 W).

**Start states against the converged orbit DC.** The orbit DC is the rotor-period mean of the last
period of the 70-period march; it drifted 0.15 % over the last 5 periods. Errors are in the
σ-weighted norm over the shaft:

| starting state of the shaft | error vs orbit DC |
|---|---:|
| one-angle loaded static (HEAD) | 50 % |
| rotor-frame mean of the loaded static field (36 angles × 5 pole-pair images) | 28 % |
| (the one-angle static against its own rotor-frame mean) | 44 % |

So the static snapshot holds a large rotor-frame harmonic content (44 % of the wall field) frozen
into the wall. Removing it is still not enough, because the orbit's DC is **not** the mean of static
fields. On the orbit the wall screens those harmonics, and that screening moves the DC through μ(B)
and through the slot-modulated permeance. That mean equals the static one only for a linear,
time-invariant system.

## 3. The remedy, implemented and measured, then withdrawn

Method: a rotor-frame-mean start.
- The loaded static field is solved at the march's 36 angles of one electrical period, each a
  Newton from the previous angle.
- It is averaged per dof, then over the q pole-pair images with the exact period map.
- It is written only into the history of the rotor-conductor dofs (magnets, shaft, sleeve).
- The same was done for voltage drive, with the phasor initialiser's (i_d, i_q).
- A pure helper was tested on a synthetic rotating field with ν = 1, 7 and backward harmonics. The
  image average reproduces the rotor-frame DC to 1e-12 and the period map's direction.
- Cost: 35 to 40 s of static solves on the L155.

| L155 rated, shaft period means [W] | p1 | p2 | p3 | p4 | p5 | … | first-window P_DC |
|---|---:|---:|---:|---:|---:|---|---:|
| HEAD one-angle start | 28.62 | 14.70 | 10.96 | 8.68 | 7.58 | 16 periods → 5.69, capped | 7.54 W |
| rotor-frame-mean start | 16.06 | 11.07 | 9.47 | 8.60 | 8.01 | same tail, stopped at 5 | – |
| linear shaft (μ_r 700), one-angle | 36.82 | 22.93 | 18.07 | 14.79 | 12.94 | p10 10.26 | 7.10 W |
| linear shaft (μ_r 700), rotor-frame mean | 23.75 | 17.66 | 15.33 | 13.97 | 13.01 | p9 10.97 | **0.47 W** |

- The start does what it promises for the DC part: the rotor-frame DC-current transient of the first
  rotor period falls ×15 (7.10 → 0.47 W on the linear-shaft diagnostic).
- It does not settle the shaft. From period 4 on, the tail is the same as from the old start, or
  even slightly above it.
- With the shaft made linear, the tail persists. So it is not the shaft B-H alone: the time-periodic
  coupling to the stator (and the saturated laminations) makes the orbit's DC state differ from any
  static construction.
- The target (settled, about 141 to 200 s) is out of reach this way. The solver change was reverted
  (not committed). The implementation is kept in the scratchpad (`ls/fem_solver_2d.dcstart.py`,
  `ls/rotor_window.dcstart.py`, `ls/test_eddy_rotor_dc_start.py`) in case it is wanted as one part
  of option 1 below.

Not a 2-D artefact in the sense the brief feared:
- The slow part is local diffusion through a 5 mm magnetic wall, with DC currents of milliwatts to
  0.6 W after the first rotor period. It is not a net shaft current that lacks an end return.
- The 3-D end closure would shorten τ only modestly for the p = 5 pattern (15 mm pole pitch at the
  shaft against a 155 mm stack).

## 4. Options for the owner (nothing implemented)

1. **Exact periodic-orbit solve of the rotor-conductor state.**
   - The slow subspace is low-dimensional. So: a vector extrapolation (MPE/RRE over about 5 to 8
     period states), or a Newton–Krylov shooting on the period map restricted to the 9153
     rotor-conductor dofs.
   - Then ≥ 4 continuous periods judged by the unchanged per-body gauge. The reported values come
     only from the march after the last jump, so it is an accelerator in the discarded prefix, not
     a filter.
   - It is the same class as the Aitken / DC anchor the owner reviewed in the no-filter §5. It
     needs approval and must be proven against the 70-period reference above (4.48 W,
     extrapolating to about 4.40 W).
2. **Gauge policy.**
   - Today each body is judged against its own level, floored at 1e-3 of the solid loss (3.9 W on
     the L155).
   - The shaft's remaining transient at the 16-period cap is about 1.3 W on 11.5 kW of losses
     (1e-4). A floor relative to the machine's total loss would call it settled.
   - This is a decision about what "settled" means, and the value stays the marched one. Owner
     only.
3. **Resolve the shaft skin layer first.** The wall has 1 to 2 P2 elements for a skin depth of
   0.3 to 0.6 mm at the rotor-frame frequencies. The coupled P_shaft (4.5 W) and the reaction-free
   frequency-domain value (82.6 W) differ ×18. Both the value and τ may move with a shaft component
   mesh (0.5 / 0.25 mm). A mesh study is advised before tuning the settling of a number the mesh
   does not resolve.
4. **Keep the cap and the flag.** The current behaviour is honest: 534 s, `eddy_settled` False,
   5.69 W.

## 5. Provenance

- Base: e1b9c72, snapshots `ls/snapH` (HEAD plus the dump hook) and `ls/snapN` (plus the start).
- Runs: `ls/res/d_l155_long.*`, `d_l155_dc.*`, `lin_H.*`, `lin_N.*`, with logs in `ls/logs`.
- Knobs used for diagnostics only: `SB_DIAG_DUMP`, `SB_DIAG_SHAFT_MU` (snapshot patches) and
  `SB_EDDY_MAX_PERIODS`.
- No A/B timing table: no solver change ships.
- The physics-regression pins are untouched. No push, no deploy, no API restart. Every process was
  stopped.
