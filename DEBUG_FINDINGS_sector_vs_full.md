# Debug: «1/4 vs 1» — where they diverge and what the bug is

**Date:** overnight autonomous session. **Production source NOT changed** (all experiments are in scratch scripts `diag_*.py`, `find_*.py`, `only_phaseA*.py`, `physzero_highI.py`, `balanced_full360.py`, `why_offset.py`; they are untracked).

## TL;DR
- **Sector model (`n_sectors=4`, production) — CORRECT.** Confirmed by invariance across the sector count.
- **Full disk (`n_sectors=1`) — BROKEN** (unstitched mesh at mid_r + demag runaway). This path is not used in production (the transient forces `NS>=4`).
- **Consequence: the d-axis shift of 2.1° (30° elec) is REAL.** My earlier conclusion "a full 360° gives 0 -> no shift" was based on the broken path — it is invalid.

## Chain of evidence

### 1. Where they diverge (rotor=0, balanced current 250 A)
| | sector (1/4) | full (1) |
|---|---:|---:|
| ampere-turns in the slots | 1.03e4 | 4.12e4 (×4 correct — currents decomposed correctly) |
| \|B\| in the gap (average) | 1.229 T | 0.305 T (×4 weaker, wrong) |
| torque | 60.4 N·m | 1.3 N·m |

-> Currents are correct, **the FIELD diverges.**

### 2. No-load (I=0, magnets only): the divergence exists even without current
- sector: \|B\|gap=1.316, rotor=1.677 T
- full: \|B\|gap=0.110, rotor=0.083 T (approx 10-15x weaker)
-> Bug is in the magnetic field/iron/BCs, not in the currents. The magnet source scales correctly ×4 (28 magnets, tags 100-127, correct Mx,My).

### 3. Who is right — invariance across n_sectors (demag OFF, no-load)
| n_sectors | BCs | \|B\| gap |
|---:|---|---:|
| **1** | none | **0.376** <- OUTLIER |
| 2 | periodic | 1.313 |
| 4 | anti-periodic | 1.316 |
| 7 | periodic | 1.343 |
| 14 | periodic | 1.325 |
| 28 | anti-periodic | 1.441 |

-> All 5 sector variants (both periodic and anti-periodic) agree at ~1.3 T. The outlier is only `n_sectors=1`. **The field must not depend on the symmetry reduction -> the sector is right, the full disk is broken.** (back-EMF from the sector approx 53 V — physical.)

### 4. ROOT CAUSE — mesh crack at mid_r
| | duplicate nodes | on mid_r (+/-1mm) |
|---|---:|---:|
| sector (4) | 0 | 0 / 4554 |
| full (1) | **1002** | **626 / 60781** |

`mid_r=75.41 mm` — the sliding circle in the middle of the gap. In the full disk, `in_band` (disk up to mid_r) and `out_band` (ring mid_r..outer) **are not stitched** across the full mid_r circle: the gmsh OCC fragment creates 626 coincident but SEPARATE nodes = a ring crack -> the field is discontinuous -> flux through the gap is partly blocked -> -3.5x. In the sector, radial cuts give clean arc ends -> 0 duplicates -> continuous.

### 5. Secondary bug — demag runaway (present in production statics too!)
In `solve_magnetostatics` (lines ~2079-2110): when a magnet drops below the knee, `br_factor` is halved every Picard iteration (1->0.5->0.25->...) **without converging** -> the magnet turns off. In the full disk this is triggered by the crack (the magnets "see" an open circuit, H≈-1.7e6) and drives the field down from -3.5x to -12x.
**Important:** this code is in `fem_solve_for_sim -> solve_magnetostatics`, i.e. in the PRODUCTION statics path (field2d, daxis_sweep, torque). At high currents in the sector, magnets can legitimately go into demag -> runaway -> torque underestimate. Latent risk.

## Eddy solver
- The transient (`fem_transient_sliding_band`) forces `NS = 4 if n_sectors<=1` -> always a working sector mesh -> **the crack does NOT affect it, the field is correct.**
- Picard in the transient — only iron saturation (`_mu_r_from_bh_vec`), **no demag** -> runaway does not affect it.
- The real eddy problem (known): per-wire copper losses `P_cu_ac_solve = P_cu_total_solve - P_cu(DC)` — catastrophic cancellation (~28 kW, unreliable). Trust the slab estimate (~1.4 kW) instead.

## Recommended fixes (NOT applied — risk of breaking validated behavior; awaiting a decision)
1. **demag runaway (priority — touches production):** replace the unconditional halving with a converging update — bound the minimum `br_factor`, and do not trigger demag when B along M is physically positive (knee threshold in terms of B, not "-Mmag"). Safe for normal regimes (demag does not trigger there).
2. **mid_r crack (`n_sectors=1`):** before meshing, merge `in_band ∪ out_band` into ONE air region for the full disk (statics does not need sliding), or force-stitch the coincident nodes. Not needed on the production path — low priority (invariance across n_sectors already serves as an independent check).

## What this means for our disputes about the angle
- The d-axis shift of **2.1° mech (30° elec) is real** (from the correct sector model; confirmed by 4 methods: holding torque, no-load psi_A, Clarke, and now invariance across n_sectors).
- The full disk gave "0" because of a bug, not because of symmetry.
- The phase-A asymmetry relative to X is real (winding axis at 15° elec from slot 0, fractional q=2/7).
