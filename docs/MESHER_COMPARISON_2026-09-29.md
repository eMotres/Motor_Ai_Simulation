# Mesher S3: Triangle CDT vs the gmsh backend (2026-09-29/30)

PR #49 (`feat/gmsh-parity`, builds on #40). This revision answers the
independent review of 2026-09-29 ("Required before S4" items 1-7). Every number
below comes from the second server run. All gmsh results from the first S3
table are superseded: after the frozen-boundary sizing fix (`9b3dbb6`) every
duty was re-run on both meshers.

## Provenance and run conditions

| Item | Value |
|---|---|
| Code | branch `feat/gmsh-parity` at `9b3dbb6` + compare-script commits; "before" = `origin/chore/license-agpl` (#40) |
| Libraries (container `deploy-api`) | Python 3.11.16, gmsh 4.15.2 (validated release, `requirements.txt` pin), triangle 20250106, numpy 2.4.4, scipy 1.17.1, shapely 2.1.2, scikit-fem 12.0.1, pypardiso 0.4.7 |
| Threads | gmsh 1 (deterministic); solver 6 (OMP/MKL/OpenBLAS) |
| Solver | P2 sliding belt (merged structured gap); nonlinear residual reported at 1e-7 to 1e-8; eddy warm-up tolerance 2 % per period; demag as saved in the duty unless stated |
| Isolation | throwaway container per run, `--network none`, `--cpus 6`, `nice 19`, `ionice` idle, one run at a time; a job starts only while API health < 2 s and the 1-min load < 11 (queue 1) / < 5 (queue 2, another agent was profiling) |
| Data | read-only copies of the owner's workspace dies; L180 gen "rated 1x9 mm" from the owner's PC (the server's L180 gen has only 0.5x9 mm duties); die.yaml identical |
| API health | HTTP 200 on every sample except one 502 at 19:48 during an external API container restart (not load); worst response 0.146 s |
| Sandbox | `/opt/motres/compute/mesher-20260929b`, deleted afterwards |

## 1. Saved duties at the shipped settings (gmsh vs Triangle)

Deltas are relative except ripple/THD (percentage points). A quantity passes
if |delta| <= max(relative limit x |Triangle|, absolute floor); limits and
floors are defined in `docs/MESHER_TRANSITION.md`.

| Quantity | L155 motor rated 1x9 | L180 gen rated 1x9 | L13 rated @ 1.22 mm (shipped) | L13 rated @ 0.61 mm (proposed default) |
|---|---:|---:|---:|---:|
| Torque (limit 0.3 %) | +0.019 % | +0.012 % | **-0.313 %** | -0.246 % |
| Ripple (0.5 pp) | +0.005 pp | -0.003 pp | +0.106 pp | -0.171 pp |
| Copper loss (2 %) | -0.037 % | -0.056 % | -0.001 % | 0.000 % |
| Iron loss (2 %) | +0.206 % | +0.193 % | +0.202 % | -0.003 % |
| Magnet eddy (3 %) | +0.153 % | +0.136 % | -0.103 % | -0.057 % |
| Shaft eddy (5 %) | -0.730 % | -1.708 % | +1.723 % | -0.205 % |
| Sleeve eddy (5 %) | +0.026 % | -0.050 % | (none) | (none) |
| Total loss (2 %) | +0.059 % | +0.051 % | +0.078 % | -0.010 % |
| Electrical input power (0.3 %) | +0.019 % | +0.012 % | -0.041 % | -0.059 % |
| Power-balance residual (0.1 pp) | -2.237 / -2.238 % | -3.516 / -3.519 % | -3.561 / -3.578 % | -3.614 / -3.601 % |
| Terminal voltage peak | +0.029 % | -0.011 % | -0.044 % | -0.024 % |
| No-load EMF fundamental (0.3 %) | +0.011 % | -0.011 % | **+0.676 %** | -0.095 % |
| No-load EMF THD | -0.018 pp | -0.033 pp | -0.015 pp | +0.006 pp |
| Cogging p-p (10 %) | +0.490 % | -0.937 % | +1.052 % | +1.157 % |
| Elements (stator + rotor) | 19 968 / 25 926 (+30 %) | 20 956 / 26 908 (+28 %) | 13 519 / 16 705 (+24 %) | 17 647 / 20 013 (+13 %) |
| Solve wall, loaded | 626 / 631 s (+1 %) | 904 / 863 s (-5 %) | 496 / 454 s (-9 %) | 775 / 478 s |
| Peak RSS | 567 / 689 MB (+22 %) | 579 / 715 MB (+24 %) | 518 / 651 MB (+26 %) | 648 / 762 MB (+18 %) |
| **Verdict** | **pass** | **pass** | **fail (torque, EMF)** | **pass** |

**Verdict at the shipped defaults:** L155 and L180 pass every limit. L13 at its
shipped 1.22 mm fails torque by 0.013 pp over the limit (-0.313 %) and EMF
(+0.676 %). **At the proposed L13 default of 0.61 mm every limit passes**
(section 2).

## 2. L13: three mesh levels, both meshers, loaded and no-load

Levels requested: 1.22 (duty), 0.61, 0.305 mm.

**What the levels really are.** The geometry mesher floors the cell area at
0.12 mm^2 (a 0.53 mm edge) and keeps the boundary discretisation fixed: the
slip grid of 1680 nodes, set by the time step, and the outline densification.
So "0.305 mm" meshes at 0.53 mm, only the interior refines, and element counts
grow 1.00 : 1.31 : 1.47 (Triangle) instead of the ~4x per level a geometric
sequence would give. A Richardson extrapolation needs a geometric refinement
ratio. It is therefore **not valid** on this sequence: the formal observed
orders scatter from -2.5 to +4.3, and are negative or undefined for torque and
EMF. The finest attainable level (0.53 mm effective) is used as the reference
instead. A true three-level study would also refine the slip grid, which is
tied to the time step; that is recorded as an open item.

### Demag as saved (magnets at 210.7 °C), the shipped configuration

| Quantity | Triangle 1.22 / 0.61 / 0.53 | gmsh 1.22 / 0.61 / 0.53 | gmsh - Triangle per level |
|---|---|---|---|
| Torque [N·m] | 0.92323 / 0.92145 / 0.92053 | 0.92034 / 0.91918 / 0.91939 | -0.31 / -0.25 / -0.12 % |
| Ripple [%] | 26.52 / 26.78 / 26.51 | 26.63 / 26.61 / 26.52 | +0.11 / -0.17 / +0.01 pp |
| EMF h1 [V] | 2.4055 / 2.4201 / 2.4287 | 2.4217 / 2.4178 / 2.4299 | +0.68 / -0.10 / +0.05 % |
| Cogging p-p [N·m] | 0.04450 / 0.04474 / 0.04433 | 0.04497 / 0.04526 / 0.04391 | +1.05 / +1.16 / -0.95 % |
| Shaft eddy [W] | 12.386 / 12.699 / 12.728 | 12.600 / 12.673 / 12.701 | +1.72 / -0.21 / -0.21 % |
| Magnet eddy [W] | 0.50995 / 0.51011 / 0.51101 | 0.50943 / 0.50983 / 0.51054 | -0.10 / -0.06 / -0.09 % |
| Iron loss [W] | 2.0822 / 2.0890 / 2.0887 | 2.0864 / 2.0890 / 2.0898 | +0.20 / 0.00 / +0.05 % |

Error of each level against the finest (0.53 mm) level of the same mesher:

| Quantity | Triangle 1.22 / 0.61 | gmsh 1.22 / 0.61 |
|---|---|---|
| Torque | +0.29 / +0.10 % | +0.10 / -0.02 % |
| EMF h1 | -0.96 / -0.35 % | -0.34 / -0.50 % |
| Ripple | +0.01 / +0.27 pp | +0.11 / +0.09 pp |
| Shaft eddy | -2.69 / -0.23 % | -0.80 / -0.22 % |

### Demag off (same duty, `demag = False`): separates the field discretisation from the demag pattern

| Quantity | Triangle 1.22 / 0.61 / 0.53 | gmsh 1.22 / 0.61 / 0.53 | gmsh - Triangle per level |
|---|---|---|---|
| EMF h1 [V] | 10.0272 / 10.0334 / 10.0455 | 10.0314 / 10.0312 / 10.0437 | **+0.04 / -0.02 / -0.02 %** |
| Torque [N·m] | 4.97818 / 4.98071 / 4.98326 | 4.97983 / 4.98017 / 4.98299 | +0.03 / -0.01 / -0.01 % |
| Ripple [%] | 5.390 / 5.347 / 5.925 | 5.379 / 5.373 / 5.963 | -0.01 / +0.03 / +0.04 pp |
| Cogging p-p [N·m] | 0.14663 / 0.14629 / 0.18985 | 0.14589 / 0.14659 / 0.18977 | -0.50 / +0.21 / -0.04 % |
| Shaft eddy [W] | 0.6124 / 0.6223 / 0.6025 | 0.6154 / 0.6109 / 0.6056 | +0.49 / -1.82 / +0.51 % |

**Conclusions from the L13 study:**

1. **There is no mesher bias in the field.** With demag off the two meshers
   agree on EMF within 0.04 % and on torque within 0.03 % at every level,
   including the shipped 1.22 mm.
2. **The shipped EMF failure is caused by demag.** The saved duty's magnets
   at 210.7 °C lose about 76 % of their EMF (2.41 V vs 10.03 V), and the
   remaining EMF depends on the per-element de-rating pattern. That pattern
   moves with any change of the rotor mesh, on either mesher: ±0.5 % between
   levels, non-monotone.
3. **Default recommendation: L13 `mesh.meshSize` = 0.61 mm** (area 0.161
   mm^2, above the 0.12 mm^2 floor). It is the coarsest level at which all
   gmsh-vs-Triangle limits pass with the shipped demag. Against the finest
   attainable mesh, torque, ripple, cogging and losses are within limits. EMF
   with demag stays within 0.35 % (Triangle) / 0.50 % (gmsh) of the finest
   level, which is demag-pattern sensitivity; with demag off, EMF is within
   0.12 % on both. Cost: 0.61 mm is +31 % elements over 1.22 mm; solve time
   was +56 % (Triangle) and +5 % (gmsh) loaded.
4. **Cogging is not converged at 0.61 mm, on either mesher.** With demag
   off, cogging jumps +30 % between 0.61 and 0.53 mm, identically on both
   meshers (shared code, not a mesher issue). This is an open item for the
   L13 model, independent of S4.

**How to apply the default.** The mesh size is per duty, in the duty's `mesh`
block of the die configuration (`dies/CIANO28 85 20SW1200/L13.yaml`,
`duties[rated|peak].mesh["mesh.meshSize"]`). The browser syncs it into the
server mesh config (`routes/family.py: duty_mesh_patch`). The L13 die is not in
the repository and the live file was not edited. A proposed copy with only
those two lines changed (1.22 -> 0.61) is in the owner's Downloads
(`L13.proposed.yaml`, with `L13.proposed.diff`). Or set "Max element size" to
0.61 in the Mesh tab for both duties and save.

## 3. Band-ring pinning: before/after on Triangle

The fix (`_symmetrize_cuts` pinned radii, `keep_r` of `_collapse_slivers`)
runs before either backend. It acts only when `r1_band`/`r2_band` > 0, i.e.
on the harmonic-macro (moving-band) gap.

| Duty | Mesh builds in the solve | Hashes #40 vs branch | Max relative output difference | r1/r2 in the builds |
|---|---:|---|---:|---|
| L155 motor rated 1x9 | 2 | identical | 4.7e-12 | 0 |
| L180 gen rated 1x9 | 2 | identical | 5.3e-12 | 0 |
| L13 rated | 2 | identical | 7.8e-05 | 0 |

**The saved duties' meshes are bit-identical before and after the fix, so
their numbers are unchanged.** The 7.8e-5 on L13 comes from the solver
repeating a solve on an identical mesh (the eddy warm-up with extensions), not
from the mesh. It sets the repeatability tolerance at 1e-4 relative.

**Where the fix matters:** the ring tests on both backends, full ring and
sector (`test_moving_band_rings`): 1008/1008 grid nodes, one per grid angle,
conforming. The end-to-end macro solve **cannot be run**: the P2 solver
refuses the moving-band / harmonic-macro gap with `NotImplementedError`
before solving (server run). That refusal is now a test
(`test_moving_band_solve_is_refused_loudly`). The production sliding-band
path is the merged structured belt, and every duty in section 1 is an
end-to-end solve on it with both backends.

## 4. Skin and sleeve layers: refinement on L155 (sleeve + hollow shaft)

Loaded L155 rated 1x9 mm. Level k = 1 (shipped), 2, 4 means shaft first layer
h1 = delta/k, cells per wavelength 16k, sleeve layers 2k
(`SB_SKIN_H1_FRAC`, `SB_SKIN_CELLS_PER_WL`, `SB_SLEEVE_LAYERS`).

| Quantity | Triangle k = 1 / 2 / 4 | gmsh k = 1 / 2 / 4 | gmsh - Triangle at k = 4 |
|---|---|---|---:|
| Shaft eddy [W] | 4.1309 / 4.1311 / 4.1355 | 4.1007 / 4.1251 / 4.1188 | -0.40 % |
| Sleeve eddy [W] | 9.9304 / 9.9291 / 9.9285 | 9.9330 / 9.9327 / 9.9337 | +0.05 % |
| Magnet eddy [W] | 92.312 / 92.317 / 92.312 | 92.453 / 92.482 / 92.431 | +0.13 % |
| Torque [N·m] | 187.934 / 187.934 / 187.933 | 187.969 / 187.967 / 187.968 | +0.02 % |
| Elements | 19 968 / 26 728 / 53 118 | 25 926 / 31 986 / 52 696 | — |

The shipped skin/sleeve resolution is converged on both backends: 4x finer
layers move shaft loss by +0.11 % (Triangle) and +0.44 % (gmsh), and sleeve
loss by <= 0.02 %. The mesher difference (-0.4 % shaft, +0.05 % sleeve) is
well inside the 5 % limit. Structure is asserted by tests on both backends:
rings, first cell = h1, growth ratio, no hanging node, identical shaft rings,
sleeve cell <= 1.25 t/n.

## 5. Optimizer-style mini-campaign (24 cusp/fillet-heavy candidates)

`scripts/mesher_campaign.py`: perturbations of the 40 mm 12s/14p preset
(rotor_fill_r 0-0.2 mm, rotor_hole 0.6/1.0 i.e. a tangent magnet fillet,
magnet_fill_radius x0.2-1.5, stator_fillet_r1 0-0.3 mm, slot_hs x0.25-1,
magnet_up_gap x0.1-1). Settings: optimizer budget armed (400 000 tris), 1 mm
mesh, then a 24-step transient solve at 10 A / 3000 rpm.

| | Triangle | gmsh |
|---|---|---|
| Built / solved / budget rejects / crashes | 24 / 24 / 0 / 0 | 24 / 24 / 0 / 0 |
| Min angle, worst / median of halves | 4.79° / 7.10° | 2.05° / 5.99° |
| Aspect p99, worst | 5.5 | 7.5 |
| Aspect p99.9, worst / median | 8.3 / 4.9 | 22.7 / 6.0 |
| Mesh build time, median / max | 0.40 / 0.44 s | 1.41 / 2.25 s |
| Solve wall, gmsh/Triangle ratio (min / median / max) | — | 0.74 / 1.09 / 2.08 |
| Peak RSS, gmsh/Triangle ratio (min / median / max) | — | 1.04 / 1.25 / 1.96 (max 1206 MB) |
| Elements, gmsh/Triangle ratio (min / median / max) | — | 1.20 / 1.71 / 2.99 |

Solver outputs, gmsh vs Triangle, over 24 candidates:
- **Torque:** within ±0.06 %; voltage within ±0.26 %; iron loss within +0.33 %.
- **Magnet eddy:** within ±0.3 % on 23 candidates. **c04** (slot_hs
  0.067 mm, magnet_up_gap 0.02 mm, rotor_fill_r 0.02 mm) reads -12.4 %.
- **Ripple:** gmsh reads higher on c04 (+4.5 pp), c19 (+2.4 pp) and c10
  (+1.2 pp).

**Refinement check on c04 (0.5 mm):**
- **Ripple:** gmsh 28.4 -> 23.2 %, Triangle 23.9 -> 24.1 %, so the 1 mm gmsh
  ripple was under-resolved and the two meshers now agree within 0.9 pp.
- **Magnet eddy:** stays at -12 %, stable on both meshers under interior
  refinement (Triangle 0.204 -> 0.203 W, gmsh 0.179 -> 0.179 W). The absolute
  difference is 0.025 W. It sits in the 0.067 mm slot opening, whose boundary
  discretisation the mesh-size knob does not refine; gmsh meshes that stator
  at 1.8x Triangle's elements. **Open:** a boundary-refinement check (slip
  grid / outline density) is needed to tell which mesher is right.

**Operational findings:**
- The gmsh tails come from sub-0.05 mm magnet fillets (c10, c19: rotor 3x
  elements) and the thin slot opening (c04). Linear-growth grading
  (slope 2.0) grades out more slowly than Triangle's q20 there.
- No gmsh run failed, and none tripped the budget. The failure path itself is
  covered by tests: preflight reject before gmsh runs, and cleanup after a
  gmsh exception.

## 6. Pass/fail against the acceptance rules (`docs/MESHER_TRANSITION.md`)

| Rule | Evidence | Result |
|---|---|---|
| Field and loss quantities, shipped defaults | section 1 | L155 pass, L180 pass, L13 @ 1.22 **fail**, L13 @ 0.61 pass |
| Power balance (residual difference <= 0.1 pp) | L155 0.002, L180 0.003, L13 0.017 (0.013 at 0.61 mm) pp | pass |
| Skin/sleeve resolution converged (4x refinement moves shaft/sleeve loss <= 1 %) | section 4 | pass |
| Same-environment repeatability (mesh bit-identical; outputs <= 1e-4 rel.) | section 3; hash tests | pass |
| Cross-version: semantic fingerprint | `test_semantic_fingerprint` (gmsh 4.15.2 reference) | defined; pass on 4.15.2 |
| Quality gate (min angle >= 1.5°, aspect p99.9 <= 30 per half) | campaign worst 2.05°, 22.7 | pass |
| RSS <= 1.35x Triangle (median of runs), <= 2 GB per solve | duties +18...26 %, campaign median 1.25x, max 1.2 GB | pass |
| Solve wall <= 1.25x Triangle (median) | duties -9...+8 %, campaign median 1.09x | pass |
| Tails: RSS/wall <= 2x on any candidate | campaign max 1.96x / 2.08x | **fail**: one candidate at 2.08x wall (RSS max 1.96x) |
| Magnet eddy on the campaign (3 %, floor 0.1 % of total loss) | c04 -12 % (0.025 W) | **open** (needs boundary refinement) |

## 7. Recommendation for S4

Not yet. Two items remain, both narrow:

1. **Apply the L13 default of 0.61 mm** (owner action, section 2) and
   re-run L13 once. With it, every saved duty passes at its shipped settings.
2. **Resolve c04's magnet-eddy difference** with a boundary-refinement check.
   While doing it, reduce gmsh's over-refinement at sub-0.05 mm fillets: a
   larger growth slope for size classes far below the region target, to be
   re-checked on the campaign.

After those, switch `auto` to gmsh, keeping `MOTOR_AI_SIM_GEO_CDT=triangle`
for one release of cross-checks.

**Also found (independent of the mesher):**
- L13 cogging is not mesh-converged at 0.61 mm (+30 % at 0.53 mm on both
  meshers, demag off).
- L13's EMF with demag at 210.7 °C varies ±0.5 % with any rotor-mesh change.
- The mesh-size knob cannot refine the geometry mesher below a 0.53 mm edge,
  nor refine the slip grid.
