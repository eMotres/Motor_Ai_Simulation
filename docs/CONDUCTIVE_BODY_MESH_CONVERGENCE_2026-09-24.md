# Conductive-body mesh convergence (2026-09-24/25)

Claude Opus 5.5 (`claude-opus-5-5`) did this as one sub-agent, no escalation, on branch
`pre-migration-freeze-2026-09-15`, base bc14972. The session was interrupted once; the
interrupted runs were re-run.

Owner's brief: «давай сначала разберёмся с валом — может ещё что-то всплывёт». Principle added
mid-task: «проблема больших моторов — это проблема большой сетки; все физические законы должны
работать одинаково на любых масштабах». So conductive-body meshes are sized by physical lengths
(the skin depth, the field's wavelength), never by the machine size or the global element size.

**Summary**

- **Which mesh was wrong.** The shaft was the only conductive body whose mesh was not converged.
  - The HEAD wall has 1-2 cells across 5 mm and 3.3 mm OD chords, sized by the AIR size.
  - The skin depth there is 0.14 mm.
  - Error in the reported shaft loss: L155 **+11.6 %**, L180 **+11.8 %**, L13 **+4.2 %**,
    Ø40 **+51 %**.
- **The fix, default ON.** A skin-depth-driven structured wall, plus an iron grading out of it
  (§3).
  - It converges the shaft loss on all four duties to ≤ 1 % of the finest meshes.
  - Cost: +13-14 % P2 unknowns on the L155/L180/L13 and +34 % on the Ø40. Paired run time: L155 +8 %, Ø40 +7 %, L13 +31 % (the L13 includes one more gauge period) (§3).
  - Every other reported value moves ≤ 0.25 %, except the ones explained in §9.
- **The other bodies were already converged**: magnets, sleeve and copper ≤ 0.2 % under 2-4×
  refinement (Ø40 magnets ≤ 0.9 %).
- **The 18× gap is not a mesh effect.** The frequency-domain cross-check (`P_shaft_honest_W`)
  bills a NON-magnetic shaft (μ_r = 1) behind a back iron given ONE mean μ_r ≈ 16.
  - With the solved per-element μ and the solver's own boundary conditions, the same route gives
    0.9-1.3 W, a linear estimate.
  - The coupled nonlinear value on the converged mesh is **3.93 W settled** and 5.10 W at the
    16-period cap.
- **The slow shaft settling is physical.** The time constant is unchanged on the converged mesh
  (τ ≈ 17-18 electrical periods). It is the wall's rotor-frame DC state acting through μ(B).
- **New finding, bigger than the mesh.**
  - All conductive-body losses carry a first-order time-step error: the eddy term is backward
    Euler.
  - At the default 36 steps/period the L155 magnet loss reads **−10.5 %** against the Δt → 0
    extrapolation, the sleeve −4 %, copper AC −2.5 %.
  - The same error on the Ø40 is −1.5 % (magnets) and −3 % (copper AC, iron).
  - The torque ripple at 36 steps is 25-32 % low on the L155 (§7).

## 1. How meshes are sized today (HEAD bc14972)

- **Mesher.** All four duties mesh with `geo_mesh.geo_mesh_halves`: the geometry-driven
  Triangle CDT, rotor pole cells tiled. The L155/L180 are forced there by the sleeve, the others
  by `mesh.geoMesh`.
- **Per-part sizes.** `component_mesh_mm` reaches the geo path for `stator`, `rotor`, `magnet`,
  `coil` (+ `coil_rel`, `outer`/`air`).
  - `shaft` is deliberately absent (`GEO_PART_KEYS`): its core sits between the `-Y`-frozen cut
    chains, so Triangle drops its area target.
  - b563b71 removed the Windings and Shaft UI fields for that reason and for the gmsh fallback
    they triggered.
- **The shaft tube wall** (`_mesh_rotor_sector`):
  - region area 0.433·(t/2)², i.e. two cells across;
  - cut-chain step t/2;
  - OD ring `n_sh = max(48, 2πr / max(0.35, air_mm))` with `air_mm = max(3, 2·iron_edge)`.
    On the L155 that is 50 nodes, 3.3 mm chords.
- **No skin-depth rule existed** for any conductor.
  - Sleeve: 2 layers.
  - Magnets: the global size (or `magnet`).
  - Copper: the structured winding patch at ½h/1h/2h of the wire.

**Meshed conductive bodies, L155 half model** (`MESHONLY` runs, `cmc/res/m_*.json`):

| body | HEAD tris / P2 dofs | median edge | smallest height | physics scale |
|---|---:|---:|---:|---|
| shaft wall (42CrMo4, 5 mm) | 205 / 470 | 2.61 mm | 0.86 mm | δ(2840 Hz, μ_r 1094) = 0.136 mm |
| sleeve (M40X, σ 100 S/m, 2.5 mm) | 2340 / 5783 | 0.86 mm | 0.12 mm | δ ≈ 0.9 m (resistance-limited) |
| magnets (N52UH, σ 0.556 MS/m) | 1380 / 3225 | 2.74 mm | 0.09 mm | δ(2840 Hz) ≈ 12 mm |
| copper (1×9 mm strips, ½h cells) | 4320 / 11100 | 0.71 mm | 0.35 mm | δ(f_e) 1.9 mm, δ(11 f_e) 0.58 mm |

Only the shaft is off its physics scale, by 6-20 δ per cell.

## 2. Why an unresolved skin reads wrong (1-D P2)

The model is 1-D P2 on −(1/μ)A'' + jωσA = 0 with the surface H imposed (`cmc/skin1d.py`; the
same model is `_p2_skin_loss` in the tests). Case: σ 4.4 MS/m, μ_r 1000, 2840 Hz,
δ = 0.142 mm. Uniform cells:

| h/δ | 3 | 2 | 1 | 0.5 |
|---|---:|---:|---:|---:|
| loss error | +15 % | +5.1 % | +0.3 % | +0.02 % |

- A graded mesh with h1 = δ and growth 1.5 is within +0.3 %.
- The HEAD wall (2 × 2.5 mm) returns 0.48 of the exact loss at 2840 Hz and 0.81 at 947 Hz.
- A mesh sized on δ(2840 Hz) holds within +1.4 % up to 24 kHz.

## 3. The rule (implemented, default ON)

`simulation/conductor_skin.py` computes the spec. It runs when `rotor_eddy` is on and the shaft
conducts and is not excluded. `geo_mesh` builds it.

- **Inputs**
  - f_ref is the rotor-frame slot-passing frequency N_s·n/60, or the PWM carrier when the drive
    chops.
  - μ_r,max is the largest secant μ of the assigned B-H curve, which gives the thinnest skin.
- **Wall.** A structured patch of concentric layers, built outside Triangle, whose quality
  refinement would cascade on thin layers.
  - **h1 = δ(f_ref, μ_r,max)**, capped at the chord;
  - **growth 1.5**;
  - layers stop growing at **4 chords**;
  - on a solid shaft, a max(10 δ, 4 chords) outer layer.
  - The patch is stitched to the CDT by node identity on its two arcs, like the winding
    patches. Triangle points found on an arc are fanned in.
  - Its radial sides lie on the cut rays with clone-identical radii, so the anti-periodic
    pairing and the cell-to-cell weld stay exact.
- **OD chord = λ/16**, where λ = 2πr/(N_s + p) is the field's shortest wavelength at the shaft
  surface. The ring count is a multiple of the copy/sector count.
- **Iron grading.** Free points in the rotor iron outward from the patch: rings at the patch
  chord growing ×1.5 until they reach the iron's own cell size.
  - They sit half a step inside the iron and half a step off the cut rays.
  - Without them a fine patch against coarse iron cells over-reads the Ø40 shaft by 2× (§4).
- **Switches and reporting**
  - `SB_SKIN_LAYER=0` reproduces the HEAD mesh bit-for-bit (the p2_eddy fixture repeats every
    pinned digit).
  - The result dict carries `shaft_skin_layer`, the spec used.
  - A non-geo build logs a warning and a trace note.

**Scale check (owner's principle).** The same rule on all four duties:

| duty | δ(f_ref) | h1/δ | layers inside 3 δ | chord/λ | N2 HEAD → rule | cost (paired timing) |
|---|---:|---:|---:|---:|---:|---:|
| L155 rated (2840 Hz) | 0.136 mm | 1.00 | 2 | 1/16 | 40 300 → 45 490 (+12.9 %) | 315 → 341 s (+8 %, 4-period cap, 218 frames each) |
| L180 gen rated (4180 Hz) | 0.112 mm | 1.00 | 2 | 1/16 | 41 716 → 47 468 (+13.8 %) | – |
| L13 rated (400 Hz) | 0.363 mm | 0.70 (chord cap) | 2 | 1/16 | 27 995 → 31 639 (+13.0 %) | 511 → 668 s (+31 %: +12 % per frame, and 242 → 282 frames) |
| Ø40 L12 rated (2600 Hz) | 0.142 mm | 0.73 (chord cap) | 2 | 1/16 | 18 710 → 25 020 (+33.7 %) | 321 → 343 s (+7 %: +25 % per frame, 254 → 218 frames) |

The resolution in δ and in λ is the same on the Ø40 and the Ø200. The Ø40 pays more because its
field wavelength at the shaft is 1.65 mm. `test_same_rule_same_resolution_on_every_scale` pins
this on a Ø150 and a Ø40 rotor.

## 4. Convergence per body per duty

**Method**
- Cold, solver-direct runs of the saved duties (`cmc/run_case.py`, derived from
  `nf/run_case.py`).
- HEAD = worktree of bc14972; the new code runs from snapshots of this tree.
  `motor_ai_sim.__file__` is asserted in every result.
- Every run has the same march length: the duty's default 16-period cap on the L155/L180, the
  settled verdict on the L13/Ø40. So the L155/L180 shaft values compare the mesh at the same
  point of the slow transient (§6 has the settled value).
- Walls come from a box running 4-8 jobs, so the cost is given as N2 (P2 unknowns) plus the
  paired timing in §3 (HEAD and rule run side by side, 4 threads each, same load).

### L155 rated (CIANO10 200 opt / L155 motor, 14 200 rpm, Δ, eddy + demag)

| shaft mesh | h1 | chord | N2 | P_shaft period 1 → 16 [W] | reported P_shaft [W] |
|---|---|---|---:|---|---:|
| HEAD | ~6.7 δ (0.91 mm) | 3.3 mm | 40 300 | 28.62 → 5.568 | **5.687** |
| 2δ, λ/8 | 0.27 mm | 1.18 mm | 41 888 | 26.78 → 4.954 | 5.050 |
| δ, λ/8 | 0.14 | 1.18 | 42 452 | 26.76 → 4.940 | 5.036 |
| δ, λ/16, layer cap = chord | 0.14 | 0.59 | 46 918 | 27.11 → 4.988 | 5.087 |
| δ, λ/16, cap 4 chords | 0.14 | 0.59 | 44 670 | – | 5.088 |
| **the rule (+ iron grading)** | 0.14 | 0.59 | 45 490 | 27.09 → 4.990 | **5.096** |
| δ/2, λ/32, growth 1.25 | 0.068 | 0.30 | 64 256 | 26.90 → 4.994 | 5.087 |
| δ/4, λ/48, growth 1.2 | 0.034 | 0.20 | 94 858 | 27.08 → 4.980 | 5.080 |
| rule + rotor iron 0.5 mm (whole rotor refined) | 0.14 | 0.59 | 92 410 | – | 5.052 (−0.85 %) |

The last two shaft refinements differ by 0.14 %; the rule is +0.3 % from the finest.

Other bodies (base δ, λ/16 mesh):
- magnets (2-D solid σE², before the segmentation factor): 3905.0 W; magnets at 2 mm 3904.9 W
  and at 1 mm 3904.6 W (**0.01 %**);
- sleeve: 9.799 W; 8 layers 9.797 W (**0.02 %**);
- copper AC: 600.63 W; 0.25 mm cells (against ½h = 0.5 mm) 601.64 W (**+0.17 %**).

### L180 gen rated (same die, 20 900 rpm, generator)

| mesh | reported P_shaft [W] | P_mag 2-D solid [W] | sleeve [W] | copper AC [W] |
|---|---:|---:|---:|---:|
| HEAD | **10.494** | 12 565.9 | 33.602 | 2063.06 |
| δ, λ/8 | 9.298 | 12 558.6 | 33.609 | 2063.09 |
| **the rule** | **9.383** | 12 557.2 | 33.612 | 2063.05 |
| δ/2, λ/32 | 9.378 | 12 561.8 | 33.611 | 2063.14 |
| δ, λ/8 + magnets 2 mm | 9.355 | 12 557.2 | 33.612 | 2062.87 |
| δ, λ/8 + copper 0.25 mm | 9.298 | 12 552.0 | 33.605 | 2065.47 (+0.12 %) |

The rule is within 0.05 % of δ/2, λ/32.

### L13 rated (CIANO28 85 20SW1200, 1000 rpm, 24s/28p, 2 mm wall)

| mesh | P_shaft [W] | P_mag [W] | copper AC [W] | T [N·m] |
|---|---:|---:|---:|---:|
| HEAD | **0.4405** | 0.4395 | 2.273 | 5.3957 |
| δ, λ/8 | 0.4546 | 0.4397 | 2.274 | 5.3961 |
| δ/2, λ/8 (h1 only) | 0.4540 | 0.4400 | 2.270 | – |
| δ, λ/32 (chord only) | 0.4150 | 0.4400 | 2.290 | – |
| δ, λ/16 | 0.4173 | 0.4397 | 2.278 | 5.3992 |
| **the rule** | **0.4227** | 0.4401 | 2.277 | 5.3989 |
| δ/2, λ/32 | 0.4146 | 0.4398 | 2.294 | 5.4160 |
| δ, λ/8 + rotor iron 0.25 mm | 0.4166 | 0.4408 | 2.256 | 5.3831 |
| rotor 0.25 + magnets 0.5 + δ/2, λ/16 | 0.4171 | 0.4406 | 2.253 | 5.3806 |
| rotor 0.15 + magnets 0.3 + δ/2, λ/32 (finest) | 0.4192 | 0.4423 | 2.246 | 5.3773 |
| magnets 0.5 mm (δ, λ/8) | 0.4493 | 0.4399 | 2.259 | 5.3845 |
| copper 0.2 mm (δ, λ/8) | 0.4538 | 0.4401 | 2.275 | 5.3981 |

- The chord drives this machine, not h1. The rule is +0.8 % from the finest.
- HEAD is +5.1 %. It sat closer than λ/8 only by accident.
- Magnets and copper are converged to ≤ 1 %.
- The torque moves ±0.4 % with the rotor-iron mesh near the shaft (§7).

### Ø40 L12 rated (CIANO14 40 new, 13 000 rpm, 12s/14p, 2 mm wall)

| mesh | P_shaft [mW] | P_mag [W] | copper AC [W] | N2 |
|---|---:|---:|---:|---:|
| HEAD | **13.13** | 3.0595 | 4.347 | 18 710 |
| δ, λ/8 | 13.72 | 3.0633 | 4.347 | 21 918 |
| δ/2, λ/8 (h1 only) | 13.73 | 3.0591 | – | – |
| δ, λ/16 | 16.99 | 3.0625 | – | 31 512 |
| δ, λ/16, cap 4 chords (no iron grading) | 17.04 | 3.0582 | 4.347 | 24 096 |
| δ, λ/32 | 17.70 | 3.0582 | – | – |
| δ, λ/64 | 9.56 | 3.0655 | – | 212 144 |
| δ, λ/8 + magnets 0.3 mm | 8.37 | 3.0876 | 4.339 | 28 722 |
| δ, λ/8 + rotor iron 0.1 mm | 8.47 | 3.0903 | 4.341 | 79 682 |
| rotor 0.1 + magnets 0.3 + δ/2, λ/16 | 8.57 | 3.0880 | 4.337 | 93 086 |
| rotor 0.07 + magnets 0.15 + δ/2, λ/32 (finest) | **8.65** | 3.0824 | 4.333 | 212 208 |
| **the rule (+ iron grading)** | **8.69** | 3.0776 | 4.344 | 25 020 |

- **The Ø40 shaft is set by the iron between it and the magnets.**
  - A fine skin patch against coarse iron cells over-reads 2× (17 mW).
  - Refining the iron (rotor 0.1 mm, or the λ/64 ring that forces Triangle to grade the iron)
    converges it to 8.5-9.6 mW.
  - Grading the iron out of the patch gets 8.69 mW (+0.5 % from the finest) at N2 +34 % instead
    of ×4-11.
- The magnet loss moves +0.6-1 % with the rotor mesh; copper ≤ 0.3 %.

## 5. The 18× gap

- On the converged mesh the coupled P_shaft is **3.93 W** settled (§6).
- The frequency-domain cross-check `honest_rotor_eddy` logs **82.4 W** on the HEAD mesh and
  48.7 W on the rule mesh.
- It was re-run offline from its own dumped inputs, changing one modelling choice at a time.
  Inputs: the rotor mesh, the rotor-frame A window, the converged per-element ν. Tools:
  snapshot patch `cmc/patch_hre_dump.py`, analysis `cmc/fr.py`. HEAD mesh / δ, λ/16 mesh:

| frequency-domain route (same field history) | P_shaft HEAD mesh [W] | δ, λ/16 [W] | P_mag 2-D [W] |
|---|---:|---:|---:|
| A. as coded: shaft μ_r = 1; back iron ONE μ_r = mean of its converged ν (≈ 16-18); Neumann cut; ∫J = 0 on the half shaft | **82.0** | 66.4 | 2826 / 2647 |
| B. per-element converged secant μ in iron and shaft (shaft median μ_r ≈ 1050) | 0.20 | 0.36 | 2574 / 2569 |
| C. B + the half shaft as a U = 0 body | 0.92 | 1.38 | 2575 / 2570 |
| D. C + anti-periodic cut (the coupled solve's BC) | **0.85** | **1.26** | 2972 / 2967 |
| E. code μ + U = 0 + anti-periodic | 148 | 120 | 3372 / 3136 |
| F. D with a non-magnetic shaft | 0.054 | 0.060 | |
| G. D with the differential μ at the rotor-frame DC \|B\| | 0.11 | 0.21 | 3800 / 3927 |
| in-solver route with shaft μ_r = 700 / 100 | 323 / – | 303 / 260 | |

**Why the frequency-domain estimate differed**

1. **μ.** `losses.rotor_mu_lookup` gives every tag other than magnets and rotor iron μ_r = 1
   (docstring: "shaft aluminium"). It gives the whole back iron ONE μ_r, the mean of the
   converged ν, which the saturated bridges pull to about 16.
   - The back iron then screens nothing, and the rotor-frame harmonics reach the shaft at full
     strength (A 82 W against B 0.2 W).
   - With the solved μ element by element, the magnetic back iron carries the harmonic flux and
     only a trace reaches the shaft.
2. **Current constraint of the cross-check.** It puts ∫J = 0 on the modelled HALF shaft and
   Neumann on the cuts. The machine's 2-D model is U = 0 with the anti-periodic image (C, D).
3. **Linear against nonlinear.** Corrected, the linear phasor model gives 0.1-1.3 W against the
   coupled 3.9 W.
   - In the coupled solve μ(B) moves with time: the wall's rotor-frame DC magnetisation
     modulates the permeance, and the saturating iron generates rotor-frame harmonics. A
     fixed-μ phasor model has neither.
   - Frozen permeability (`frozen_nu`, same duty) removes the slow shaft mode. That run is on a
     different operating point (T 202.7 N·m against 187.8 N·m, ν frozen at frame 0), so only its
     behaviour is used, not its watts.
   - **The coupled nonlinear value is the physics.** The cross-check as coded describes a
     non-magnetic shaft behind a transparent back iron.
4. **Mesh.** The resolved skin moves the coupled value by −10 % to −12 %, not 18×. The
   cross-check's own P1-on-vertices value is itself mesh-sensitive (−41 % on the rule mesh,
   −55 % on the finest).

### The 2-D current model of the shaft (verdict)

- **Coupled solver, half model.** The shaft and the sleeve are U = 0 cut bodies, with no row.
  This is **exact**: the anti-periodic image half carries −J, so the whole ring's net axial
  current is identically zero and its U is zero by symmetry. Full ring: one ∫J = 0 row per body.
  **Correct** for an infinitely long tube.
- **3-D.** The end closure is not modelled. Russell–Norsworthy factors for the L155
  (L = 155 mm, shaft r = 25.6 mm):
  - mechanical order ν = 1: 0.67;
  - ν = 5: 0.93;
  - ν = 7: 0.95;
  - ν = 17: 0.98.
  The shaft continues past the stack, which adds return area. So the 3-D loss is about 0.7-1.0×
  the 2-D value, depending on which orders carry it. No factor is applied (no model without a
  3-D solve).
- **Magnets.** Interior magnets get ∫J = 0 per body. Correct.
- **Latent defect in the magnet comment.** The comment says a magnet BISECTED by the sector cut
  is exact with U = 0 on both halves. It is **not**: the image of the 0⁺ half is the negative of
  the π⁻ half, not its mirror.
  - The physical magnet's net current is ∫J(0⁺) − ∫J(π⁻), which is not identically zero.
  - Exact model: one shared ±U and one row.
  - No machine here has a bisected magnet ("0 edge halves" on every duty). Flagged, not
    changed.
- **Frequency-domain cross-check.** ∫J = 0 on the half shaft with Neumann cuts is not the
  machine's model (item 2 above).

## 6. Settling on the converged mesh (L155)

Continuous 70-period march, δ, λ/16 mesh (`SB_EDDY_MAX_PERIODS=70`, 2594 frames, 5026 s
loaded):

| period | 1 | 5 | 10 | 16 | 20 | 30 | 40 | 50 | 60 | 70 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| P_shaft period mean [W] | 27.11 | 7.11 | 5.54 | 4.99 | 4.63 | 4.29 | 4.11 | 4.02 | 3.97 | 3.94 |

- Fit P∞ + B·e^(−t/τ) on rotor-period (5-period) block means:
  - from period 21: **P∞ = 3.93 W, τ = 16.9 periods**;
  - from period 36: 3.92 W, τ = 18.4 periods.
- The reported window at 70 periods is 3.956 W, gauge residual 5.3 %, still capped.
- HEAD mesh (EDDY_LOADED_STATIC_START): about 4.40 W, τ ≈ 18-27 periods.
- **The slow mode does not change with the resolved skin.** The wall's rotor-frame DC state
  acts through μ(B), and with μ frozen it is gone.
- The 16-period cap over-reads the settled value by 1.2 W: +29 % of the shaft item, 1e-4 of the
  losses. An accelerator is still needed.
- **Proposal (not implemented; owner's call as in EDDY_LOADED_STATIC_START §4):**
  - a periodic-orbit solve of the rotor-conductor state: MPE/RRE over 5-8 period states, or
    Newton–Krylov shooting on the period map restricted to the rotor-conductor dofs;
  - reported values only from ≥ 4 continuous periods after the last jump;
  - proven against this 70-period reference (P∞ 3.93 W).

## 7. Other findings

1. **The eddy time integration is first order (backward Euler).** It under-reads every
   conductive-body loss at the default 36 steps/period.

   | L155 rated (rule-class mesh) | 36 | 72 | 108 (144 snapped) | Δt → 0 (1st-order fit, 72/108) | 36 vs Δt → 0 |
   |---|---:|---:|---:|---:|---:|
   | magnets 2-D solid [W] | 3905.0 | 4132.4 | 4209.8 | ≈ 4365 | **−10.5 %** |
   | copper AC [W] | 600.6 | 608.6 | 611.1 | ≈ 616 | −2.5 % |
   | sleeve [W] | 9.80 | 10.05 | 10.10 | ≈ 10.2 | −4 % |
   | P_fe [W] | 1442.9 | 1453.5 | 1458.9 | ≈ 1470 | −1.8 % |
   | T mean [N·m] | 187.770 | 187.841 | 187.882 | | −0.08 % |
   | T ripple [%] | 1.42 | 1.78 | 1.88 | | −25 to −30 % |

   - The first-order fit through 72/108 predicts the 36-step value to 0.1 % (3900 against
     3905 W), so the error really is O(Δt).
   - Ø40 (36/72/144 steps):
     - magnets 3.058 / 3.086 / 3.095 W (36-step −1.5 %);
     - copper AC 4.347 / 4.418 / 4.452 W (−3 %);
     - P_fe 9.14 / 9.30 / 9.36 W (−3 %);
     - ripple 5.67 / 6.47 / 6.49 %.
   - L13 (36/72): magnets +0.5 %, copper AC +0.6 %, shaft +2.5 %.
   - **Fix options: §8, item 1.** This is a larger effect on the magnet watts than every mesh
     question here.
2. **Torque ripple.**
   - Mesh: the ripple moves within about ±4 % on the L155 (1.41-1.53 % over eleven shaft-region
     variants that leave the gap untouched; Triangle re-plans the rotor cell) and ±5 % on the
     L13.
   - Time step: dominated by the step count (item 1).
   - A ripple study (steps × rotor mesh) is its own item.
3. **Magnet segmentation factor.** It reads the magnet width off the mesh node cloud
   (`losses.char_width_m` on the body's nodes). Width 32.548 → 32.724 mm when Triangle re-plans
   the magnet cells gives factor −0.96 %. So the reported P_mag moves −0.9 % (L155) and −1.0 %
   (L180) while the 2-D solid loss moves +0.03 % / −0.07 %. Measuring the width on the CAD
   polygon would make it mesh-independent. Not changed.
4. **30 mm fixture.** With the resolved shaft the pinned ripple moves 0.38 → 0.93 % at 12 steps
   and 3.78 → 4.43 % at 36 steps. λ/8, λ/16 and λ/32 give the same 0.92-0.94 %, so this is the
   resolved shaft's eddy reaction, not noise. Re-pinned (§9).
5. **Sleeve (M40X, σ 100 S/m transverse).** Modelled conductive. Converged at its 2 layers
   (9.80 W L155, 33.6 W L180). Its axial current's end return runs along the fibres: a 3-D
   question, not a mesh one.
6. **Magnet eddy loss.** Mesh-converged: ≤ 0.1 % on the L155/L180, ≤ 1 % on the Ø40. The magnets
   are resistance-limited (δ ≥ 4 mm up to 24 kHz). The reported value is dominated by the
   Russell–Norsworthy segmentation factor (L155 × 0.022), a model.
7. **Ø50 L15** has no eddy record: no saved run settings or result in the duty. Not run.

## 8. Open decisions for the owner

1. **Second-order eddy time integration** (§7.1). Options:
   - BDF2 or Crank–Nicolson for the σ∂A/∂t term, with the circuit rows to match;
   - or a default of ≥ 72 steps for eddy duties (magnet error −5 %, 2× the cost).
   It would raise the L155 magnet loss about +10 %, copper AC about +2.5 % and the sleeve
   about +4 %. It needs its own A/B and a re-pin.
2. **Periodic-state accelerator** for the L155/L180 shaft (§6).
3. **Frequency-domain cross-check.** Either:
   - (a) give it the per-element converged μ and the solver's BC (U = 0 rings, anti-periodic
     cut), after which it reads 0.9-1.3 W, a linear estimate; or
   - (b) stop logging it as "honest" beside the coupled value on magnetic shafts.
4. **Magnet segmentation width from the CAD polygon**, not the mesh nodes (§7.3).
5. **Bisected-magnet constraint** made exact before any machine puts a magnet across the cut
   (§5).

## 9. What changed, A/B, commits

**Code:**
- `simulation/conductor_skin.py` (new): the rule;
- `geo_mesh.py`: `skin_layer_radii`, `_skin_patch`, `_stitch_skin_patch`, `_shaft_skin_plan`,
  `_iron_grade_points`, `_sleeve_layers`, wired into `_mesh_rotor_sector` and
  `_mesh_rotor_half`;
- `mesher._build_sliding_band_meshes(skin_layers=…)`;
- `fem_solver_2d`: `_shaft_skin_material`, the spec before the build, `shaft_skin_layer` in the
  result.

**Diagnostic knobs:** `SB_SKIN_LAYER=0`, `SB_SKIN_H1_FRAC`, `SB_SKIN_GROWTH`,
`SB_SKIN_CELLS_PER_WL`, `SB_SKIN_CHORD_MM`, `SB_SKIN_HMAX_CHORDS`, `SB_SKIN_IRON_GRADE`,
`SB_SLEEVE_LAYERS`.

**Tests:**
- `tests/test_conductor_skin_mesh.py` (28 + 2 skipped): rule arithmetic, 1-D P2 accuracy,
  layer radii, patch conformity, real-rotor builds (full ring and half, Ø150 and Ø40: tube area,
  first layer, no hanging node, clone-identical cut radii, other sections unchanged), iron
  grading, scale check;
- `test_mesh_shaft_region` and `test_sector_symmetry_guard`: 100 passed with the new file;
- eddy group (`test_eddy_settled_flag`, `test_eddy_density_history`,
  `test_voltage_eddy_fast_la`, `test_p2_eddy_current_normalization`,
  `test_harmonic_eddy_cache`, `test_eddy_period_gauge`, `test_solver_guards`): 64 passed;
- physics regression re-pinned, and `test_axial_slices…` passes against the new pins.

**A/B vs bc14972** (cold, saved duty, reported window, HEAD → rule):

| quantity | L155 rated | L180 gen rated | L13 rated | Ø40 L12 rated |
|---|---|---|---|---|
| T mean [N·m] | 187.774 → 187.788 (+0.01 %) | 238.876 → 238.868 | 5.3957 → 5.3989 (+0.06 %) | 0.621992 → 0.621778 (−0.03 %) |
| ripple [%] | 1.411 → 1.531 (+8.4 %) | 3.137 → 3.141 | 5.269 → 5.272 | 5.905 → 5.660 (−4.1 %) |
| V_peak [V] | 433.86 → 434.03 | 986.64 → 986.65 | 15.894 → 15.887 | 11.002 → 10.991 |
| V_LL peak [V] | 402.6 → 402.6 | 870.9 → 870.8 | 24.7 → 24.8 (+0.4 %) | 18.3 → 18.3 |
| P_mag reported [W] | 85.56 → 84.76 (−0.94 %) | 269.35 → 266.54 (−1.04 %) | 0.4395 → 0.4401 | 3.060 → 3.078 (+0.59 %) |
| **P_shaft [W]** | **5.687 → 5.096 (−10.4 %)** | **10.494 → 9.383 (−10.6 %)** | **0.4405 → 0.4227 (−4.0 %)** | **0.01313 → 0.00869 (−34 %)** |
| P_sleeve [W] | 9.795 → 9.796 | 33.602 → 33.612 | – | – |
| P_cu AC [W] | 600.67 → 600.68 | 2063.06 → 2063.05 | 2.273 → 2.277 (+0.2 %) | 4.347 → 4.344 |
| P_fe [W] | 1443.55 → 1443.64 | 3463.51 → 3463.18 | 2.472 → 2.478 (+0.2 %) | 9.148 → 9.142 |
| total loss [W] | 3804.9 → 3803.6 (−0.03 %) | 8374.7 → 8370.5 (−0.05 %) | 198.68 → 198.67 | 65.677 → 65.681 |
| P_mech [W] | 277 680 → 277 701 | 519 037 → 519 025 | 561.68 → 562.03 | 834.53 → 834.23 |
| frames solved | 650 → 650 (capped) | 650 → 650 (capped) | 242 → 282 | 254 → 218 |
| eddy settled (residual) | False (45 %) → False (39.5 %) | False (55 %) → False (47.5 %) | True (1.2 %) → True (0.6 %) | True (0.7 %) → True (1.5 %) |

Every move over 0.5 %:
- **P_shaft** (all four) is this change: the resolved skin (§4).
- **P_mag reported −0.94 % / −1.04 % (L155/L180)** is the segmentation factor's mesh-node width
  (§7.3). The 2-D solid loss moves +0.03 % / −0.07 %.
- **Ø40 P_mag +0.59 %** is the magnet loss converging with the iron grading. The finest rotor
  mesh reads +0.75 %.
- **Ripple +8.4 % (L155), −4.1 % (Ø40)** is inside the rotor-mesh band of the ripple (§7.2).
  The finest L155 mesh reads 1.522 %.
- **L13 frames 242 → 282** and **Ø40 254 → 218**: the whole-period gauge closes one period later
  on the L13 and one earlier on the Ø40, because the resolved shaft's transient differs.
- **L13 V_LL peak +0.4 %** is 0.1 V on a 1-decimal display.

**Commits:**

| commit | content |
|---|---|
| 191e9be | feat(mesh): skin-depth-driven structured shaft wall + tests |
| efc6ac0 | feat(mesh): iron grading out of the patch, rule defaults, test fixes |
| 022c1b7 | test(physics): re-pin (per-line reasons in the message) |
| (this doc) | docs |

No push, no deploy, no API restart. Worktree `cmc/wt_base` removed; snapshots and results stay
in the scratchpad `cmc/`.
