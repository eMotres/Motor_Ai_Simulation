# Full disk (n_sectors=1) vs sector — precise diagnosis (replaces DEBUG_FINDINGS_sector_vs_full.md)

**Date:** June 2026. All experiments are in scratch scripts `_diag_*.py` (untracked), production not changed.

## Summary
- **The sector is correct and valid.** ns=4 gap|B|=1.316 T, ns=2 gap|B|=1.313 T — consistent, scale (tris ×1, ×2), torque 59.6 N·m.
- **ONLY ns=1 is broken** — the only path without `_clip_polys_to_sector` (line 1244, clip only fires for n_sectors>1).
- The old `DEBUG_FINDINGS_sector_vs_full.md` was WRONG: "mesh crack at mid_r" is not the cause (welding was applied, coincident_pairs=0, did not help).

## What was RULED OUT by direct testing (NOT the causes)
| Hypothesis | Test | Result |
|---|---|---|
| Magnetization M | polarity vs angle | `-+-+...` perfect in both |
| Boundary conditions | outer Dirichlet BC | 130 mm in both, far field present |
| Mesh connectivity | connected_components | 1 component in both (no gap) |
| Quality (slivers) | min-angle quality | ns=1 even better: 1.2% slivers vs 3.5% |
| Polygon vertices | count | scales ×4 exactly (stator 369->1466) |
| Sliding ring | _N_SLIP 1008->120 | gap|B| 0.377->0.413, tris ~140k (did not help) |
| Air merge | legacy continuous-air | gap|B|=0.082 (worse!) |
| Demag | demag OFF | ns=1 still 0.377 vs 1.316 (demag is secondary) |

## Confirmed defects at ns=1
1. **Mesh over-refinement** (from gmsh size fields/curvature on the full 360°, NOT from vertices):
   tris=170848 (×12 instead of ×4). Stator 1754->65070 (×37), outer 5416->64544 (×12), airgap 4092->29578 (×7). Magnets/coils/rotor — correctly ×4.
2. **Shaft domain lost**: shaft 246->0 cells (went into rotor: 1790->8214). Classification defect in `_classify`/`frag_to_doms` on the full 360°.
3. **The nonlinear saturation iteration converges to the WRONG point**: 1st iter -> converged: ns=4 gap|B| 0.081->1.316 (INCREASES, correct), ns=1 0.494->0.377 (DECREASES, wrong). On the full disk the iron's mu_r is iteratively underestimated -> the iron does not conduct flux -> the field collapses ×3.5.

## Conclusion for the fix
The root cause is full-360° mesh generation (`build_mesh_from_polygons` size fields + classification) plus the nonlinear iteration's behavior on it. This is **not a one-liner**: the fix touches validated code shared with the working ns=2/4 paths. It needs a careful, step-by-step rework with a regression check against ns=2/4 at every step.

**Safe alternative:** compute the "Full" view via ns=4 and replicate the field ×4 (anti-periodically) — 1/1 matches 1/4 by construction, physically correct, zero risk to the solver.

---

## Deep diagnosis (round 2) — mechanism localized, fix not found yet

**KEY FINDING:** the field in the full-disk gap is **INCOHERENT** (oscillates cell to cell). B_r in the gap, averaged over 1° bins: ns=4 binned-peak=2.53 (cell peak 2.93, coherent); ns=1 binned-peak=0.023 with a cell peak of 1.46, 106 sign changes (28 expected). Coherent flux is destroyed by the oscillation -> a uniformly weak field.

**Reproduced in the SIMPLEST case:** mu_r=1 EVERYWHERE (no iron, only magnets in free space, a Poisson problem) — ns=4 coherence 0.89, ns=1 **0.08**. This DEFINITIVELY rules out iron/saturation/demag.

**Tested and DISCARDED as the cause (round 2):**
- Linear solve (mu=5000 fixed, no saturation): ns=1 gap=0.494 vs ns=4 gap=0.068 — they already diverge linearly (bug in the linear system, not in the iteration).
- Area-weighted saturation percentile: ns=1 0.377->0.472 (negligible), ns=4 1.316->1.341 (ok). Density dependence is a small contribution.
- Jump at the mid_r sliding surface: NO — the inner (0.042) and outer (0.002) halves of the gap are each individually incoherent.
- gap_layers 1..5: almost no effect (0.186->0.098).
- Shaft as rotor (mu 1->5000): the sector only moves 1.316->1.264 (4%). Not the cause.
- Duplicate triangles: ns=1 has 3158 duplicates, BUT these are degenerate zero-area ones (6284 total); removing them does NOT change the field. Not the cause.
- Mesh connectivity: 1 component. Duplicate nodes: 0.

**Bottom line:** the linear system on the full 360° mesh yields a weak, incoherent field; the ns=2/4 clipped mesh does not.

**Round 3 — source-vs-matrix split (done):**
- The magnet RHS (source) at mu=1 is **CLEAN and scales ×4** (ns=1: 1120 nonzero nodes = 4×280, same max=3421). -> the bug is NOT in the source.
- Degenerate cells (6284 zero-area) are **ruled out**: there are 0 of them in the radial gap, and excluding them from the measurement changes neither |B| nor the coherence.
- The gap band [r_ro,r_si] for ns=1 has 80356 cells vs 5761 for ns=4 — ×14 (not ×4), i.e. the gap is over-refined by ~×3.5: gmsh refines down to ~0.06mm even though the MathEval formula calls for 0.217mm (the source of the over-refinement was never caught: not the _N_SLIP rings, not curvature/points, not gap_layers).

**CONCLUSION:** the source is clean -> the defect is in the mesh.

## ROUND 4 — ROOT CAUSE FOUND (final)

**Linear-system residual:** ns=1 ‖KA-f‖/‖f‖ = 3.7e-15 (solved EXACTLY). So the solver is fine, A is the exact solution of the ns=1 system.

**One magnet + iron (mu=5000):** ns=4 rotor|B|max=26 (flux ENTERS the iron), ns=1 rotor|B|max=**1.2** (does NOT enter). Even a single magnet fails to transfer flux into the pole shoe on the full disk -> not an interaction among the 28 magnets, but a fundamental magnet-to-iron coupling defect.

**EDGE MANIFOLDNESS (decisive):** number of edges belonging to >2 triangles:
- ns=4: **0** (valid manifold mesh) -> gap|B|=1.316, correct
- ns=1: **12279** -> gap|B|=0.377, wrong

An edge cannot belong to >2 triangles — these are **OVERLAPPING triangles**. **The OCC boolean fragment does NOT produce a clean (manifold) partition for the full 360° geometry** — the mesh covers some regions twice in places (stacked triangles) -> the K/source assembly runs over overlapping elements -> the magnetic flux does not take the correct path (magnet -> pole -> gap) -> a uniformly weak field. The sector (ns=2/4) is clean (0) because `_clip_polys_to_sector` re-resolves the geometry through a Shapely intersection into a simple one that OCC fragments correctly.

A specific area overlap was also found: `out_band` ∩ `coils` = 1872 mm² (the outer air only subtracts the stator, not the coils in the slots).

**Fixes tested and FOUND INSUFFICIENT (the overlap is partial, not fixed trivially):**
- Post-mesh removal of exact duplicates/zero-area cells: >2edges 12279->7082, field 0.377 (no change).
- `shapely.set_precision` (snap to a 1e-3/1e-2 mm grid): >2edges->10936, field->0.453 (negligible).
- legacy air: >2edges=5968, field 0.082 (worse).
- Subtracting coils from out_band: does not fix it (the remaining overlaps stay).

## THE REAL FIX (requires reworking the mesh pipeline for ns=1)
The OCC fragment is unreliable on the full 360°. Options:
1. **Planar partition via Shapely (recommended):** union the BOUNDARIES of all polygons (`unary_union` of lines) -> `polygonize` into non-overlapping faces -> assign a domain to each face (point-in-polygon) -> mesh. Guarantees a manifold partition, independent of OCC booleans.
2. **`gmsh occ.removeAllDuplicates()` before the fragment + reclassify the fragments by centroid/point-in-polygon** (preserving per-magnet/coil tags). Less reliable, breaks the current classification via out_map.
3. **Safe workaround:** Full = the ns=4 solution, replicated ×4 anti-periodically (1/1≡1/4 by construction, correct, zero risk).

Validate any fix by regression against ns=2/4 (must stay at 1.313/1.316). Production NOT changed (all edits/instrumentation reverted; ns=4=T_em -0.98/Bmax 76.6). Diagnostics: `_diag_*.py`, `_plot_flux.py`, `_plot_zoom.py`.

**Production NOT changed** (all edits reverted; ns=4 = T_em -0.98 / Bmax 76.6). Diagnostic scripts: `_diag_*.py`, `_plot_flux.py`.
