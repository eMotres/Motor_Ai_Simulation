# Mesher transition S2+S3: Triangle CDT vs gmsh (2026-09-29)

Branch `feat/gmsh-parity` (built on #40), PR https://github.com/eMotres/Motor_Ai_Simulation/pull/49 into `pre-migration-freeze-2026-09-15`.

## What S2 built

The geometry-driven mesher (`geo_mesh.py`) made exactly one Triangle call: `_triangulate`, the quality CDT of a planar straight-line graph that `geo_mesh.py` builds itself. A new module, `geo_mesh_gmsh.py`, replaces that call with gmsh. Every other step is shared by both backends, so every feature on the MESHER_TRANSITION list exists on the gmsh path by construction:

- skin layers on the shaft and sleeve
- per-part sizes and the `coil_rel` wire cell
- cusp and sliver guards
- slot/pole tiling (bit-identical cells)
- exact tagging
- the R1/R2 moving-band rings
- the optimizer mesh budget

How gmsh sizes the mesh (gmsh features, driven by the geometry and not by machine size):

- **Local feature size:** each segment gets h = min(its length, 2.5 × its local feature size).
- **Growth:** Distance + MathEval fields grow the size away from each feature at slope 2.0.
- **Per-region caps:** a Constant field caps each face at its region's target size; the background field is the Min of all of these.
- **Frozen boundaries:** domain-boundary segments (cut rays, gap rings, patch rims) are frozen, so the cuts stay clone-identical and the welds stay exact.
- **Interior interfaces** may be subdivided, as Triangle's `-Y` allowed.
- **Determinism:** gmsh runs single-threaded, so repeat builds are bit-identical.

Backend selection: `MOTOR_AI_SIM_GEO_CDT=auto|triangle|gmsh`.
- `auto` uses Triangle wherever it is installed. There are no behaviour changes: the Triangle meshes are verified bit-identical to #40.
- Without Triangle, `auto` now uses the gmsh backend instead of the old plain gmsh build.

Bug fixed on both backends: the moving-band ring R2 lost its cell-seam node to the cut-ray clustering, leaving 1002 of 1008 slip nodes. The band rings are now pinned.

Tests: `tests/test_geo_mesh_gmsh.py` (15 tests, about 1 min, 40 mm preset). The skin-layer and shaft-region suites now run on whichever backend is active; the new module also re-runs them forced onto gmsh.

## S3 setup

- **Where:** the server, in the sandbox `/opt/motres/compute/mesher-20260929`, now deleted.
- **Code:** a git archive of the branch; one tree, with the backend switched per run.
- **Container:** a throwaway container from the `deploy-api` image (triangle + pypardiso + gmsh), no network, `--cpus 6`, `nice 19`, `ionice` idle, 6 threads, one run at a time.
- **Data:** read-only copies of the owner's workspace dies and config.
- **L180 gen "rated 1x9 mm":** the server's L180 gen only has 0.5x9 mm duties, so this duty was taken read-only from the owner's PC (`motor_ai_sim/config/dies/CIANO10 200 opt/L180 gen.yaml`). Its `die.yaml` is identical to the server's.
- **Settings:** every setting comes from the saved duty.
- **API health:** HTTP 200 throughout; worst response 0.136 s (typically 0.015 s).

## Results (gmsh vs Triangle)

Ripple limits are in percentage points; the other deltas are relative.

### L155 motor, rated 1x9 mm (sleeve, 2 sectors, mesh 4 mm)

| Quantity | Triangle | gmsh | Delta | Limit | Result |
|---|---:|---:|---:|---:|---|
| Torque [N·m] | 187.9 | 188.0 | +0.01 % | 0.3 % | pass |
| Ripple [%] | 1.564 | 1.572 | +0.01 pp | 0.5 pp | pass |
| Copper loss [W] | 2269 | 2269 | −0.03 % | 2 % | pass |
| Iron loss [W] | 1445 | 1447 | +0.16 % | 2 % | pass |
| Magnet eddy loss [W] | 92.31 | 92.33 | +0.02 % | 3 % | pass |
| Shaft eddy loss [W] | 4.131 | 4.096 | −0.85 % | 5 % | pass |
| Sleeve eddy loss [W] | 9.93 | 9.937 | +0.07 % | 5 % | pass |
| Total loss [W] | 3820 | 3822 | +0.04 % | 2 % | pass |
| Terminal voltage peak [V] | 436.7 | 436.8 | +0.02 % | — | — |
| EMF fundamental [V] | 421.4 | 421.4 | +0.01 % | 0.3 % | pass |
| Cogging p-p [N·m] | 8.386 | 8.414 | +0.34 % | 10 % | pass |
| Elements | 24 880 | 26 760 | +7.6 % | — | — |
| Solve time, load / no-load [s] | 582 / 193 | 608 / 196 | +4 % / +2 % | — | — |
| Peak RSS [MB] | 562 | 713 | +27 % | — | — |

### L180 gen, rated 1x9 mm (duty from the owner's PC)

| Quantity | Triangle | gmsh | Delta | Limit | Result |
|---|---:|---:|---:|---:|---|
| Torque [N·m] | 239.2 | 239.2 | 0.00 % | 0.3 % | pass |
| Ripple [%] | 3.290 | 3.297 | +0.01 pp | 0.5 pp | pass |
| Copper loss [W] | 4638 | 4636 | −0.06 % | 2 % | pass |
| Iron loss [W] | 3469 | 3474 | +0.16 % | 2 % | pass |
| Magnet eddy loss [W] | 292.2 | 292.2 | +0.01 % | 3 % | pass |
| Shaft eddy loss [W] | 7.415 | 7.359 | −0.76 % | 5 % | pass |
| Sleeve eddy loss [W] | 34.18 | 34.18 | +0.01 % | 5 % | pass |
| Total loss [W] | 8441 | 8443 | +0.03 % | 2 % | pass |
| EMF fundamental [V] | 762.3 | 762.2 | −0.02 % | 0.3 % | pass |
| Cogging p-p [N·m] | 8.973 | 8.879 | −1.04 % | 10 % | pass |
| Elements | 25 090 | 26 920 | +7.3 % | — | — |
| Solve time, load / no-load [s] | 788 / 200 | 807 / 216 | +2 % / +8 % | — | — |
| Peak RSS [MB] | 578 | 725 | +25 % | — | — |

### L13 rated (CIANO28 85 20SW1200; 4 sectors, mesh 1.22 mm, 120 steps)

| Quantity | Triangle | gmsh | Delta | Limit | Result |
|---|---:|---:|---:|---:|---|
| Torque [N·m] | 0.9232 | 0.9245 | +0.13 % | 0.3 % | pass |
| Ripple [%] | 26.52 | 26.31 | −0.21 pp | 0.5 pp | pass |
| Copper loss [W] | 259.9 | 259.9 | 0.00 % | 2 % | pass |
| Iron loss [W] | 2.082 | 2.084 | +0.08 % | 2 % | pass |
| Magnet eddy loss [W] | 0.5099 | 0.5092 | −0.14 % | 3 % | pass |
| Shaft eddy loss [W] | 12.39 | 12.64 | +2.01 % | 5 % | pass |
| Total loss [W] | 274.9 | 275.1 | +0.09 % | 2 % | pass |
| EMF fundamental [V] | 2.405 | 2.433 | +1.13 % | 0.3 % | **fail at this mesh; see below** |
| Cogging p-p [N·m] | 0.04450 | 0.04476 | +0.58 % | 10 % | pass |
| Elements | 13 690 | 17 180 | +25.5 % | — | — |
| Solve time, load / no-load [s] | 432 / 345 | 375 / 315 | −13 % / −9 % | — | — |
| Peak RSS [MB] | 520 | 652 | +25 % | — | — |

**Convergence check on the L13 EMF**, run as the stage plan asks for a case outside a limit. The mesh was halved to 0.61 mm on both meshers:

| Mesher | EMF fundamental at 0.61 mm |
|---|---:|
| Triangle | 2.4167 V |
| gmsh | 2.4201 V |
| Delta | +0.14 % (pass) |

Taking ~2.418 V as the converged value, at the duty's 1.22 mm mesh Triangle reads about −0.5 % low and gmsh about +0.6 % high. The 1.13 % gap is therefore discretisation error of a coarse mesh on this small machine, not a gmsh bias. Both meshers are equally far from the converged answer.

## What we lose and what we gain

**What we lose:**
- About 25 % more peak memory (gmsh model plus shapely face build).
- More elements for the same boundary: +7 % on the Ø200 machines, +25 % on L13.
- Mesh build is about 2–3× slower (seconds, negligible against the solve). Solve time is within −13 % to +8 %.
- Aspect max about 11.6 vs 9.0 on the 40 mm preset. Triangle's q20 angle guarantee has no exact gmsh equivalent; a feature-size rule approximates it.

**What we gain:**
- AGPL-clean meshing with the same feature set.
- No refinement cascade: the budget is a predicted count plus a post-check.
- Deterministic builds.
- The R2 ring bug found and fixed.

## Remaining gaps

- The L13 EMF at the duty mesh size is limited by mesh resolution on both meshers. Before S4, decide either to judge EMF at a converged mesh, or to refine L13's default size.
- Coverage is the saved-duty machines only. The optimizer sweep path (cusp-heavy candidates, `rotor_hole = 1` fillets) has not been exercised on gmsh at scale.
- Tuning knobs (`SB_GMSH_CDT_GROWTH`, `SB_GMSH_CDT_LFS_K`, `SB_GMSH_CDT_ALGO`) are env-only.
- The 2-D view's `earcut_fallback` still uses Triangle when installed. That is unchanged and outside the solver.

## Recommendation for S4

Switch the default to gmsh (`auto` → gmsh), keeping `MOTOR_AI_SIM_GEO_CDT=triangle` for one release of cross-checks:
- Every limit passes on L155 and L180.
- L13 passes everything except an EMF difference that the convergence check shows is discretisation, not a mesher bias.

Before switching:
1. Run one optimizer mini-campaign on gmsh (budget and cusp paths).
2. Re-run the saved duties once and record the changes, as the plan says.
