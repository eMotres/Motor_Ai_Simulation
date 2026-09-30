# Physics-regression re-pin, 2026-09-29

`tests/test_physics_regression.py` failed 9 tests on unmodified
`pre-migration-freeze-2026-09-15` (8261d1f): all eight
`test_case_matches_baseline[p2_*]` cases and
`test_axial_slices_cut_the_magnet_eddy_loss_by_the_modelled_factor`.
`tests/physics_baseline.json` was pinned before the integration squash 6ed3641
(#57), which also replaced `config/materials_library.yaml` (PR #51: every value
traced to an independent source).

**Verdict: every drift is caused by the new material data. No code
regression. The baseline is re-pinned; no solver code changed.**

## Bisect (server sandbox, motres-api:test, 4 threads, nice 19)

| Run | Code | `materials_library.yaml` | Result against the OLD pins |
|---|---|---|---|
| A | 8261d1f (HEAD) | 53d6667 (before 6ed3641) | all 8 cases match, max relative difference 5e-6 (p2_voltage), else ≤ 1e-11; axial test passes (0.966 W = 1.495 W × 0.6462) |
| B | 53d6667 (before 6ed3641) | HEAD | exactly the 9 reported failures, the same digits as the report log |
| C | HEAD | HEAD, but F45SH_120C `bh_curve` reverted | demag cases back on the old pins (only P_mag and the Br-driven ripple_pp remain); p2_load identical to HEAD |
| E | HEAD | HEAD | the re-pin (`UPDATE_PHYSICS_BASELINE=1`); matches B to ≤ 2e-10 relative on every quantity above machine zero |
| F | HEAD + this change | HEAD | `pytest tests/test_physics_regression.py`: 14 passed (12 min 46 s) |

So none of the code in 6ed3641 touches this fixture's numbers: the band-ring
pinning, the #32 p2/eddy work, the always-included shaft (the fixture already
pins `shaft: included` and `Aluminium_6061`), and the new `dc_orbit` source
feedback (`G_ll=None` for a plain voltage source) all leave the pins unchanged.

## What changed in the fixture's materials

The fixture pins `F45SH_120C` (magnet), `B15AHV950M` (both cores) and
`Aluminium_6061` (shaft). The copper comes from the sandbox config (`copper`).

| Card | Field | Old | New | Reaches the fixture? |
|---|---|---|---|---|
| F45SH_120C | `bh_curve` | 7 points; knee −634 kA/m, HcJ 652 kA/m, top-segment recoil 1.083 | 17 points generated from Arnold N45SH: linear recoil (1.05) to the knee at 0.97 HcJ = −718 kA/m, HcJ 740 kA/m | yes, demag cases only (`simulation/demag.py` is the only reader) |
| F45SH_120C | `Br` | 1.19 T | 1.188 T (−0.17 %) | yes, every case |
| F45SH_120C | `Hc` | 901 878 A/m | 900 362 A/m (same μ_rec 1.05) | no change to μ_rec |
| F45SH_120C | `sigma` | 6.25e5 S/m | 5.556e5 S/m (−11.1 %) | yes, rotor-eddy cases |
| F45SH_120C | α_Br, β_HcJ, CTE | … | … | no (no magnet temperature is set) |
| Aluminium_6061 | `sigma` | 25.8 MS/m | 24.94 MS/m (−3.3 %) | yes, shaft loss (inside its 0.05 W floor) |
| B15AHV950M | yield/tensile | 350 / – MPa | 460 / 540 MPa | no (mechanical only) |
| copper | density, thermal_alpha, k | 8933, 0.0043, 400 | 8890, 0.00393, 391 | no pinned number moved |

## Old → new pins and the cause of each

Only lines outside the suite tolerance, plus the in-tolerance lines that move
by more than 0.1 %, are listed. Unlisted values are unchanged to ≤ 0.1 %.

**Causes:**
- **curve**: the new F45SH_120C `bh_curve` (HcJ 652 → 740 kA/m, softer knee).
- **Br**: the new Br, −0.17 %.
- **σ_mag**: the new magnet conductivity, −11.1 %.
- **σ_Al**: the new aluminium conductivity, −3.3 %.

Run C separates the curve from the scalar changes.

| Case | Quantity | Old | New | Δ | Cause |
|---|---|---|---|---|---|
| p2_demag | T_avg_Nm | 0.39064 | 0.400477 | +2.52 % | curve: less de-rating (C: 0.390577) |
| p2_demag | T_avg_maxwell_Nm | 0.389386 | 0.399821 | +2.68 % | curve |
| p2_demag | T_ripple_pct | 0.8035 | 0.2671 | −66.8 % | curve: see "The ripple drop" |
| p2_demag | T_ripple_pp_Nm | 0.00313884 | 0.00106948 | −65.9 % | curve (see below) |
| p2_demag | V_peak | 7.28951 | 7.33181 | +0.58 % | curve: stronger magnet → more EMF |
| p2_demag | P_fe_W | 4.41113 | 4.52992 | +2.69 % | curve: more flux in the iron |
| p2_demag | demag_br_mean | 0.806171 | 0.901049 | +11.8 % | curve: HcJ +13.5 % |
| p2_demag | demag_br_min | 0.123471 | 0.112651 | −8.8 % | curve: the softer (quadratic) collapse below 0.97 HcJ lets the single worst corner element fall a little further |
| p2_demag | P_cu_W | 110.257 | 110.388 | +0.12 % | curve (in tolerance) |
| p2_demag_eddy | T_avg_Nm | 0.392046 | 0.401796 | +2.49 % | curve |
| p2_demag_eddy | T_avg_maxwell_Nm | 0.387265 | 0.397605 | +2.67 % | curve |
| p2_demag_eddy | T_ripple_pct | 0.92797 | 0.65303 | −29.6 % | curve (see below) |
| p2_demag_eddy | T_ripple_pp_Nm | 0.00363807 | 0.00262385 | −27.9 % | curve |
| p2_demag_eddy | V_peak | 7.28219 | 7.32589 | +0.60 % | curve |
| p2_demag_eddy | P_fe_W | 4.42308 | 4.53992 | +2.64 % | curve |
| p2_demag_eddy | P_cu_ac_solve_W | 3.076 | 3.245 | +5.49 % | curve: stronger magnet field in the slots (C: 3.074) |
| p2_demag_eddy | P_mag_solve_W | 1.429 | 1.309 | −8.40 % | σ_mag −10.7 % (C: 1.276), curve +2.6 % (stronger field) |
| p2_demag_eddy | P_mag_linear_W | 1.063 | 0.956 | −10.1 % | σ_mag −10.8 %, curve +0.8 % |
| p2_demag_eddy | demag_br_mean | 0.808591 | 0.902516 | +11.6 % | curve |
| p2_demag_eddy | demag_br_min | 0.122516 | 0.109695 | −10.5 % | curve (as p2_demag) |
| p2_demag_eddy | P_shaft_solve_W | 0.016 | 0.014 | −0.002 W | σ_Al and curve; inside the 0.05 W floor |
| p2_eddy | P_mag_solve_W | 1.495 | 1.334 | −10.8 % | σ_mag: resistance-limited loss ∝ σ (−11.1 %) |
| p2_eddy | P_mag_linear_W | 1.104 | 0.984 | −10.9 % | σ_mag |
| p2_voltage_eddy_rotor | P_mag_solve_W | 1.588 | 1.416 | −10.8 % | σ_mag |
| p2_voltage_eddy_rotor | P_mag_linear_W | 1.138 | 1.014 | −10.9 % | σ_mag |
| p2_load | T_ripple_pp_Nm | 0.00165586 | 0.00163971 | −0.97 % | Br (C, which has the new Br, reproduces it exactly) |
| p2_load | T_ripple_pct | 0.404689 | 0.401075 | −0.89 % | Br (in tolerance: 0.05 floor) |
| p2_noload | T_avg_maxwell_Nm | 4.1482e-05 | 3.91688e-05 | −5.6 % | Br; this is the no-load Maxwell mean, a discretisation residual at 1e-4 of the loaded torque, so its relative sensitivity is not physical |
| p2_noload | P_cu_W | 1.57497 | 1.5678 | −0.46 % | Br: at I = 0 this is the AC copper term, ∝ B² |
| p2_voltage | I_A_thd_pct | 1.80743 | 1.79538 | −0.67 % | Br: EMF −0.17 % against a fixed 7.0 V shifts the current waveform |
| p2_voltage_eddy | I_A_thd_pct | 1.78781 | 1.77571 | −0.68 % | Br |
| p2_voltage, p2_voltage_eddy, p2_voltage_eddy_rotor | T_avg_Nm | … | … | −0.11…−0.14 % | Br (in tolerance) |

The machine-zero quantities also moved inside their absolute floors:
p2_noload `T_avg_Nm` ~1e-21 and the `v_circuit_resid_max_V` ~5e-15.

## The ripple drop

A 2.5 % torque change with a two-thirds ripple drop needed a check. It is
physical, and it is the curve.

The reported `T_ripple_pct` of a demag case is (max − min)/mean of the 12
frames in the reported window. In the demag cases part of that span is not
rotor-position ripple. It is the irreversible ratchet still de-rating the
magnet through the window, so the torque slides downward across the period.
The settling period (`_dmskip`) removes most of this but not all of it.

Per-frame energy torque, fitted with a straight line over the window:

| Run | p-p [N·m] | linear trend over the window [N·m] | detrended p-p |
|---|---|---|---|
| p2_demag, old curve (A) | 3.14e-3 | −2.30e-3 | 2.08e-3 (0.53 %) |
| p2_demag, new curve (B/E) | 1.07e-3 | −0.64e-3 | 0.58e-3 (0.15 %) |
| p2_demag_eddy, old curve | 3.64e-3 | −1.97e-3 | 2.52e-3 (0.64 %) |
| p2_demag_eddy, new curve | 2.62e-3 | −0.44e-3 | 2.63e-3 (0.65 %) |
| p2_load (no demag, periodic) | 1.66e-3 | +0.38e-3 | 1.96e-3 (0.48 %) |

- **The old curve's knee (−634 kA/m) sits inside the working range of this
  60 A fixture.** Its hard collapse to HcJ = 652 kA/m cost 19 % of the
  magnet's mean Br. It was still de-rating in the reported window: the −2.3e-3 N·m
  slide is 73 % of the old p-p.
- **The new curve's HcJ is 740 kA/m, 13.5 % higher.** It is the Arnold N45SH
  20 °C minimum moved to 120 °C with the datasheet β. With that curve the
  magnet de-rates less (Br kept 80.6 % → 90.1 %) and settles sooner. The
  in-window slide drops to −0.64e-3 N·m, and the p-p follows it.
- **p2_demag_eddy.** Its detrended ripple is unchanged (0.64 → 0.65 %). The
  whole −30 % there is the smaller decay.
- **p2_demag.** The detrended ripple also falls, from 0.53 % to 0.15 %.
  Partial demagnetisation of the pole corners changes the cogging/mutual
  harmonic balance. The old-curve map (Br ≤ 0.5 in 101 of 567 magnet
  elements) and the new one (46 of 567) are different magnets.

### Two-period check

Same fixture, run with `n_periods = 2`. Each frame is compared with the same
rotor position one period later.

| p2_demag | mean, period 1 | mean, period 2 | T[k+12] − T[k] | p-p period 1 → 2 | demag_br_mean after 1 → 2 periods |
|---|---|---|---|---|---|
| old curve | 0.390263 | 0.389121 (−1.14e-3) | −2.8e-3 … +0.5e-3 | 3.16e-3 → 1.60e-3 | 0.8074 → 0.8012 |
| new curve | 0.400388 | 0.400115 (−0.27e-3) | −1.0e-3 … +0.1e-3 | 1.07e-3 → 0.48e-3 | 0.9010 → 0.8992 |

- **The magnet is demonstrably still de-rating inside the reported window.**
  With the old curve the slide is four times larger. That slide is most of
  what the old 0.80 % "ripple" measured.
- **Once it has slid, the old-curve p-p (1.60e-3, 0.41 %) is the level of the
  non-demag p2_load (1.66e-3).** The new-curve p-p (0.48e-3, 0.12 %) is lower
  still, because its de-rated map differs.

### Conclusion and follow-up

The ripple drop follows from the higher HcJ and softer knee of the new curve.
It is not a regression in the integration.

It does expose a pre-existing property of the demag path: one settling period
(`_dmskip`) does not settle the ratchet on this fixture. So a demag case's
`T_ripple_pct` carries a decay term that depends on how close the working
point is to the knee.

That is out of scope here. Settling to a Br tolerance instead of a fixed
period would move these pins again. It is recorded as a follow-up, not
changed in this re-pin.

## Axial-slices test

The test compares a 5 mm-sliced run against the pinned **solid** `p2_eddy`
values times the modelled factor. On the old pins it expected
1.495 W × 0.6462 = 0.966 W and got 0.862 W = 1.334 W × 0.6462. That is the new
solid value times the same factor. The factor itself (0.6462 from the
measured 4.491 mm magnet width) did not change. The failure was only the stale
`p2_eddy` reference, and it passes after the re-pin.

## Test-harness fix in the same change

`UPDATE_PHYSICS_BASELINE=1 python -m pytest tests/test_physics_regression.py`,
the command in the module docstring, only skipped: nothing wrote the file. The
only working route was script mode, which runs without conftest's sandbox (live
config and materials). The update branch now writes each case's pins into
`tests/physics_baseline.json` (read-modify-write, so `-k` re-pins a subset) and
prints the old → new diff (`-s` to see it). This re-pin was produced that way.

## Reproduce

Server sandbox `/opt/motres/compute/physreg-20260929/` (code trees `new/`,
`old/`, material variants `mats/`, logs and JSON dumps in `out/`). Each run is
`pytest` in a throwaway `motres-api:test` container with the chosen
`materials_library.yaml` mounted over `config/`.
