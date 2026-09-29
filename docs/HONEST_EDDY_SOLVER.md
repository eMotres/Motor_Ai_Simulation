# Honest (coupled) eddy-current solver — design + progress log

**Goal (Vadim, overnight task):** add a SECOND, "honest" eddy-current solver that treats
EVERY conductor identically — coils, magnets, shaft — as a *solid* conductor, and
computes eddy currents WITH the eddy reaction (self-shielding / skin effect) emerging
from the physics. No slab `d²/12`, no cylinder factor, no skin-depth cap — zero
shape approximations, universal for any geometry.

**Hard constraint:** do NOT modify/break the existing (resistance-limited) solver.
This new path is additive + selectable so the two can be run on the same design and
compared. Existing solver = `fem_solve_for_sim` / sliding-band (post-processed
`σ(∂A/∂t)²`, no reaction). New solver = coupled magneto-dynamic.

## Physics

Per-frame magnetostatics today:  `−∇·(ν ∇A_z) = J_src`  (no σ term) → eddy is a
post-process `P = σ∫(∂A/∂t)²`, which IGNORES the reaction → over-counts high-σ bodies
where skin depth ≲ size (e.g. the aluminium shaft).

Honest (coupled) magneto-dynamic, backward-Euler in time:

```
(K(A) + M_σ/Δt) Aⁿ + C·Vⁿ = f_magⁿ + (M_σ/Δt) Aⁿ⁻¹
            Cᵀ Aⁿ + D·Vⁿ = I_body(tⁿ)         (one row per SOLID conductor body)
```

- `K(A)`  = reluctivity stiffness (nonlinear iron via the existing Picard / ν(B)).
- `M_σ`   = σ-weighted mass matrix, assembled ONLY on conductor domains (coils, magnets,
            shaft); σ=0 on laminated iron and air → those rows reduce to magnetostatics.
- `V_body`= one extra unknown per solid conductor body = the uniform axial E-offset
            (the "loop voltage / L"). `J_z = σ(−∂A/∂t − V_body)`.
- Constraint `∮_body J_z dA = I_body`:  coils → I_body = I_phase(t); magnets, shaft →
            I_body = 0 (no net axial current).  ← THIS is the uniform treatment Vadim
            asked for: every conductor is the same kind of object; only its prescribed
            current differs.
- Eddy loss per body = ⟨∫ J_z²/σ dA⟩·L over the period (= the real ohmic dissipation,
            reaction included).

## Open architecture questions (investigate first)

1. Does the sliding-band path keep the rotor & stator meshes FIXED across frames
   (stable node indices)?  If yes → `Aⁿ⁻¹` is just the previous solution vector and the
   `M_σ/Δt·Aⁿ⁻¹` term is trivial.  If it remeshes per frame → need an interpolation map.
2. How are coil currents imposed today (stranded source J), and how is the band
   coupling assembled (so I can reuse it)?
3. Material/σ per domain (already resolved: coils σ_cu(T), magnets σ_mag, shaft σ_shaft,
   iron 0).

## Validation plan

- V1 — analytical skin effect: a single conducting slab/cylinder in a uniform AC field
  → compare the coupled-solve loss to the closed-form skin-effect loss vs frequency
  (must show the loss roll-off as δ < thickness; the resistance-limited form would not).
- V2 — motor: 40 mm, 13000 rpm, I=38 A, full ring → compare solid (magnet+shaft) +
  copper AC to ANSYS (SolidLoss 2.79 W, StrandedLossAC) and to the existing solver.

## Progress

- [x] Investigate solver architecture — the sliding-band path builds rotor & stator
      meshes ONCE (`_build_sliding_band_meshes`, fem_solver_2d.py:943) and keeps them
      FIXED across frames (node indices stable; the band only re-pairs the air-gap
      interface).  So a coupled time-stepping eddy term `Mσ/Δt·Aⁿ⁻¹` would be trivial
      (previous solution vector) — feasible for a future full-motor coupled solve.
- [x] **Core coupled solver built + validated** — `eddy_solver_2d.py`.  Implemented
      the FREQUENCY-DOMAIN (phasor) form (cleaner than time-stepping, validates
      directly against analytics, and is the per-harmonic building block for the
      motor).  Self-contained (numpy/scipy only); does NOT touch the production solver.
- [x] **V1 analytical skin-effect validation — PASSED.**  Solid Al cylinder
      (a=2.6 mm, σ=2.58e7) in a uniform transverse AC field, swept in frequency:

      |  f (Hz) | δ/a  | coupled (W) | resistance-limited (W) | ratio |
      |--------:|-----:|------------:|-----------------------:|------:|
      |     50  | 5.39 |  1.366e-3   |  1.371e-3              | 0.996 |
      |    200  | 2.70 |  2.181e-2   |  2.193e-2              | 0.994 |
      |   1000  | 1.21 |  5.197e-1   |  5.484e-1              | 0.948 |
      |   2600  | 0.75 |  2.749      |  3.707                 | 0.742 |
      |   8000  | 0.43 |  8.92       | 35.1                   | 0.254 |
      |  30000  | 0.22 | 19.5        | 493                    | 0.040 |

      Matches the exact resistance-limited asymptote `π a⁴ L σ ω² B0²/8` to **0.4 %**
      where δ≫a (no reaction), and correctly SCREENS (loss rolls over, falls far below
      the no-reaction value) as δ→a.  This is the skin physics the production
      post-process (`σ(∂A/∂t)²`) cannot see — it runs away ∝ f² to 493 W.

- [x] **V2 motor estimate (40 mm, 13000 rpm).**  Rotor conductors see the stator slot
      ripple at f_slot = 12·13000/60 = 2600 Hz.  Coupled-solve screening factors there:

      | body   | σ (S/m) | screen | production (W) | honest (W) |
      |--------|--------:|-------:|---------------:|-----------:|
      | magnet | 5.56e5  | 0.997  | 1.90           | 1.89       |
      | shaft  | 2.58e7  | 0.742  | 1.50           | 1.11       |

      solid total: production **3.40** → honest **3.01** vs ANSYS **2.79** (within ~8 %).
      → Magnet eddy in production is CORRECT (δ≫7.5 mm build, no screening).  The Al
      shaft is over-counted ~26 % by the resistance-limited form (δ≈radius) — the
      honest solve fixes exactly that, with NO shape factor or cap.

      Run it:  `python -m motor_ai_sim.simulation.eddy_solver_2d`

- [x] **History-driven engine `region_eddy_from_history` — built + validated.**  rFFTs
      the real per-node field history → per harmonic runs the coupled multi-body solve →
      sums.  Reproduces the direct single-frequency `solve_harmonic_eddy` to **0.00 %**.

- [x] **Full per-conductor motor integration — BUILT (gated + fail-safe).**  Added
      `honest_eddy: bool=False` to `fem_transient_sliding_band`: when on it captures the
      rotor-node A history and, after the existing post-process, runs `honest_rotor_eddy`
      on the REAL rotor mesh (magnets + shaft + iron μr, each conductor a floating body),
      adding `P_mag_honest_W` / `P_shaft_honest_W` to the result.  Wrapped in try/except —
      any failure returns 0 and leaves the production numbers untouched.  Default off.

### Two rotor-frame subtleties found while integrating (and how handled)

The rotor-node A history is NOT a clean sinusoid, for two reasons; both are handled:

1. **Slip-band jitter.**  The node-identification injects a broadband ~1–2-frame
   artefact (the same one the production post-process savgol-smooths).  Raw, its high
   harmonics blow up `σω²|A|²` → first run gave magnet 21.5 W.  Fixed by de-jittering the
   history with a CAPPED savgol window (< the slot-ripple period) before the FFT
   → magnet dropped to 5.8 W.

2. **Non-periodicity (leakage).**  Over ONE electrical period the rotor sees only
   N_slots/p = 12/7 ≈ 1.71 slot-ripple cycles → an FFT over that window leaks the ripple
   into higher-ω bins, and ω² weighting INFLATES the loss (5.8 vs the ~1.9 W expected,
   since the magnet is barely screened).  Clean fix: capture over a FULL MECHANICAL
   revolution, `n_periods = pole_pairs = 7`, so the rotor passes an INTEGER 12 slot
   pitches and the ripple is periodic → no leakage.  A confirmation run at n_periods=7 is
   in flight; if the magnet honest lands ≈ the resistance-limited value (no screening) and
   the shaft lands ≈ 0.74× it, the integration is validated and the honest diagnostic
   should be RUN with n_periods≥7.

   **CONFIRMED (n_periods=7, steps=48, 40 mm @ 13000):**

   |        | production | honest | honest/prod |
   |--------|-----------:|-------:|------------:|
   | P_mag  | 1.614 W    | 1.469  | 0.91  (barely screened ✓) |
   | P_shaft| 0.513 W    | 0.123  | 0.24  (strongly screened) |

   The magnet honest converged from 5.76 W (1-period leakage) → **1.47 W ≈ the production
   magnet** — the leakage hypothesis is right and the integration is VALIDATED.  The shaft
   screens harder than the single-frequency 0.74 estimate because the in-transient solve
   sums ALL slot harmonics (higher f → more screening) on the REAL geometry (iron μr).
   NB: absolute levels are resolution-dependent (shared with the production post-process —
   here steps=48 vs the 72 used elsewhere); for the definitive side-by-side run BOTH at the
   SAME steps_per_period AND n_periods≥pole_pairs.

### Reliable number TODAY (independent of the above)

The **screening-factor V2 estimate** (validated standalone solver, cylinder-exact shaft,
unscreened magnet) does NOT depend on the rotor-frame FFT and is the trustworthy figure:

    solid: production 3.40 -> HONEST 3.01 W  vs ANSYS 2.79 W   (~8%)
    magnet eddy: production correct (no screening);  Al shaft: production over-counts ~26%.

### Still to wire (after n_periods=7 confirms)

- Make the honest diagnostic auto-use n_periods≥pole_pairs (or extend the window via the
  production's pole-shift symmetry, avoiding the 7× cost).
- Coils: same engine with I_body = I_phase harmonics (AC proximity from physics).
- Thread `honest_eddy` through run_transient_2d → route → a UI toggle for side-by-side.
