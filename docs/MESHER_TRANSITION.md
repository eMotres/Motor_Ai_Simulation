# Mesher transition: Triangle CDT to gmsh (2026-09-29)

## Why

The geometry-driven mesher (`src/motor_ai_sim/simulation/geo_mesh.py`) is built
on J. R. Shewchuk's Triangle (`triangle` package). Its licence permits only
non-commercial use without the author's permission, a restriction the GNU
AGPL-3.0-or-later does not allow. It therefore cannot be a dependency of the
AGPL distribution. Owner decision 2026-09-29: move to gmsh step by step, with
every number checked on the reference machines, instead of removing Triangle
at once.

## Where each build goes today

`mesher._build_sliding_band_meshes` with the saved-duty defaults
(`iron_template=True`, `geo_mesh=True`):

| `triangle` installed | `MOTOR_AI_SIM_GEO_CDT` | Build |
|---|---|---|
| yes | `auto` (default) or `triangle` | geometry-driven mesher (`geo_mesh_halves`), Triangle CDT, exactly as before |
| yes or no | `gmsh` | geometry-driven mesher, gmsh CDT backend (since S2) |
| no | `auto` | geometry-driven mesher, gmsh CDT backend (since S2; S1 fell back to the plain gmsh build) |
| yes or no | `netgen` | geometry-driven mesher, netgen CDT backend (evaluation, `geo_mesh_netgen.py`, optional extra `[netgen]`; docs/MESHER_NETGEN_2026-09-30.md) |

Since S4 (`f4acc6a`) `auto` means gmsh also where Triangle is installed;
Triangle and netgen are used only when selected.

`geo_mesh=False` keeps the tensor iron template (`iron_template.py`, no
Triangle). The 2-D view uses `mapbox-earcut`; its fallback
(`earcut_fallback.py`) uses Triangle when installed, and otherwise shapely's
(GEOS) constrained Delaunay.

## S2 as built (2026-09-29): a gmsh backend for the geometry mesher

The whole geometry-driven mesher had exactly ONE Triangle call:
`geo_mesh._triangulate(V, S, ...)`, the quality CDT of a planar straight-line
graph that `geo_mesh.py` builds itself (resampled slip-grid arcs, 1 um snap,
clone-identical cuts, densified outlines, skin/wire patches as holes).
`geo_mesh_gmsh.triangulate_gmsh` replaces that one call. Every other step is
shared code, so a feature exists on gmsh either because it is verified by a
test on the gmsh backend (**tested**) or only because the code that implements
it runs unchanged before/after the triangulation (**inferred**):

| # | Feature | How it works on the gmsh backend | Evidence |
|---|---|---|---|
| 1 | Shaft skin layers | the same structured patch (`_skin_patch`) is a hole in the PSLG and stitched afterwards; its rim is domain boundary, never split | **tested**: planned rings present, first cell h1, growth 1.5 to the cap, no hanging node, identical rings on both backends (`test_shaft_skin_layers_structure`, `test_shaft_skin_patch_identical_on_both_backends`, the whole `test_conductor_skin_mesh.py` re-run on gmsh) |
| 2 | Sleeve layers | NOT a structured patch on either backend: the ring is CDT-meshed to the target cell 0.433 (t/n)^2 of `n` layers (`_sleeve_layers`) | **tested**: mean cell edge <= 1.25 t/n, refines with n, CAD section kept (`test_sleeve_resolution_follows_the_layer_request`); loss convergence: server study in MESHER_COMPARISON |
| 3 | Per-part sizes | the same region seeds; each face is capped by a `Constant` field at its region's target edge (last seed in a face wins, the `_seeds` order) | **tested** (`test_per_part_size_acts_on_gmsh`) |
| 4 | Wire cell `coil_rel` | the same structured wire patches / lattice (lattice points are embedded) | **tested** (`test_wire_cell_factor_acts_on_gmsh`) |
| 5 | Sliver / cusp guards | `_blunt_cusps` before either backend; gmsh has no refinement cascade; frozen-boundary-aware sizing; a rotor bulk over the aspect gate gets the same guarded smoothing | **tested** (`test_cusp_guard_on_the_gmsh_path`, `test_thin_pocket_is_not_a_sliver`); campaign quality distribution in MESHER_COMPARISON |
| 6 | Rotation invariance / hashes | the same cell tiling; domain-boundary segments frozen (transfinite, 2 nodes) so cuts stay clone-identical; one gmsh thread | **tested**: every cell maps onto the next (`test_every_cell_is_the_same_mesh`); repeat build bit-identical in ONE environment (`test_repeat_build_is_bit_identical`); cross-version = semantic fingerprint (`test_semantic_fingerprint`), not bitwise |
| 7 | Exact tagging | the same `_tag_stator` / `_tag_rotor` | **tested**: CAD sections kept, same per-tag areas as Triangle |
| 8 | Moving-band rings | R1/R2 frozen on the slip grid; band rings pinned in `_symmetrize_cuts` (the fix for 1002/1008 seam nodes, which affected both backends) | **tested** on forced Triangle and gmsh, full ring and sector (`test_moving_band_rings`); macro solve `test_moving_band_macro_solve` (slow, server) |
| 9 | Mesh budget | preflight: faces' area at their target size > 2x budget rejects before gmsh runs; the built count is checked after | **tested** (`test_mesh_budget_on_gmsh`, `test_budget_preflight_rejects_before_meshing`) |
| 10 | Fail closed | an unloadable gmsh raises with the install command and the Triangle alternative; a meshing failure raises `GmshCDTError` after finalizing gmsh and releasing the lock; no path retries with another mesher | **tested** (`test_gmsh_failure_is_loud_and_cleans_up`, `test_tile_does_not_fall_back_on_a_gmsh_failure`, `test_missing_gmsh_is_an_actionable_error`) |

Sizing on gmsh follows the physical scales the PSLG producer resolved: every
input segment carries h = min(its length, 2.5 x its local feature size); a
frozen (boundary) segment's size is its length, and an interface facing a
frozen segment across a thin feature is not cut finer than that segment. The
segments are grouped in x1.5 classes, each a `Distance` field feeding
h0 + 2.0 x distance (`MathEval`); the background mesh is the `Min` of those and
the region caps. Provenance: every gmsh-backend solve notes the gmsh version
(`geo_mesh.cdt_provenance`, validated release `GMSH_VALIDATED` = 4.15.2, the
`requirements.txt` pin).
## Historical: the S1 gap list (superseded)

Written at S1, BEFORE the gmsh backend existed. The statements below that say
gmsh lacks a feature ("gmsh has no wire grid", "no equivalent fence", "the
shaft wall is meshed without it") describe the plain gmsh build that S1 fell
back to, not the S2 backend; the current state is the table above.

1. **Shaft skin-depth layers** (`_shaft_skin_plan`, `_skin_patch`,
   `_stitch_skin_patch`, `_iron_grade_points`; sized by
   `conductor_skin.shaft_skin_spec`): a structured wall patch with
   h1 = SKIN_H1_FRAC x delta, geometric growth, graded iron transition. On
   the gmsh path `_skin_check` warns that the shaft wall is meshed without it.
2. **Sleeve skin layers** (`skin_layers["sleeve"]`, `_sleeve_layers`,
   `_sleeve_radii`): layered retaining-ring mesh.
3. **Per-part element sizing through CDT region seeds** (`GEO_PART_KEYS`,
   `_part_areas`, `_seeds`): stator / rotor / magnet / coil / outer-air sizes.
   gmsh honours component sizes through size fields, but the key set and the
   resulting densities differ; a like-for-like mapping is needed.
4. **Wire-cell factor `coil_rel`** (1/2 h, 1 h, 2 h; `snap_coil_rel`,
   `_wire_template`, `_wire_stacks`, `_stack_patch`, `_coil_pslg`,
   `_stitch_patches`): structured per-wire copper patches pitched on the
   wire height. gmsh has no wire grid.
5. **Sliver and cusp guards** (`_repair_slivers`, `_collapse_slivers`,
   `_min_input_angle_deg`, `_blunt_cusps`, `_split_at_t_junctions`,
   `_defeature_iron` with `_ROTOR_DEFEATURE_MM`, aspect gates
   `_ROTOR_AR_GATE` / `_ROTOR_AR_BULK`): the crash and quality protections
   found on real sweeps (access violation at magnet-fillet cusps, rotor-bridge
   slivers).
6. **Rotation-invariant, bit-identical construction**: slot/pole cell tiling
   (`_tile_cells`, `SB_GEO_TILE`), clone-identical radial cuts
   (`_symmetrize_cuts`, Triangle `-Y`), resampling of every gap-facing arc onto
   the uniform slip grid (`_resample`, `_grid_arc`), 1 um coordinate snapping
   (`_SNAP`), deterministic PSLG — every pocket and tooth identical, repeated
   builds with identical hashes.
7. **Exact domain tagging** by centroid point-in-polygon on the real CAD
   polygons (`_tag_stator`, `_tag_rotor`), including the hollow-shaft bore as
   air (gmsh relies on `mesher._retag_shaft_bore_as_air`).
8. **Moving-band macro support**: the halves end on the uniform R1/R2 rings
   (`r1_band`, `r2_band`); the tensor template cannot, gmsh must be checked.
9. **Optimizer mesh budget**: `set_tri_budget` / `MeshBudgetExceeded` caps
   Triangle's Steiner points so a pathological candidate is rejected in
   seconds (`_steiner_cap_truncated`). gmsh has no equivalent fence yet.
10. **Winding homogenisation choices** (one copper block per slot vs the wire
    grid, `_WIRE_GRID`, `_WIRE_PATCH`) and per-region air coarsening
    (`air_mesh_mm`).

## Stages

**S1 — now (this change).** Both meshers. `triangle` is an optional extra
(`requirements-triangle.txt`, `pip install ".[triangle]"`, Docker
`--build-arg WITH_TRIANGLE=1`), not part of the AGPL distribution. Where it is
installed (our workstation and server, non-commercial use) the Triangle mesher
stays the default and every number is identical to before. Without it: gmsh,
one log line. Tests that drive Triangle are marked `requires_triangle` and
skipped when it is absent.

**S2 — port the missing features to the gmsh path**, in the order of the list
above that affects numbers most: shaft and sleeve skin layers (1, 2),
per-part sizing and `coil_rel` (3, 4), sliver/cusp guards (5), rotation
invariance and bit-identical hashes (6), tagging (7), moving band (8), mesh
budget (9). Each port lands with its own test against the Triangle result on
the same geometry.

**S3 — server comparison.** `scripts/compare_mesher_triangle_vs_gmsh.py` on
L155 motor rated 1x9 mm, L180 gen rated 1x9 mm and L13 rated (loaded and
no-load), plus the optimizer-style campaign `scripts/mesher_campaign.py`;
sandboxed, `nice 19`, threads <= 6. Results and evidence:
`docs/MESHER_COMPARISON_2026-09-29.md`.

### Acceptance rules (gmsh vs Triangle, same duty and settings)

Conditions under which the rules are stated: gmsh 4.15.2 (single thread),
triangle 20250106, scikit-fem 12.0.1, pypardiso 0.4.7, solver threads 6,
P2 merged structured belt, nonlinear residual <= 1e-7, eddy warm-up
tolerance 2 % per period, the duty's own settings (steps, demag, magnet
temperature, mesh block).

**Field and loss quantities.** A quantity passes when
|gmsh - Triangle| <= max(relative limit x |Triangle|, absolute floor).
The floors keep near-zero quantities from failing on noise:

| Quantity | Relative limit | Absolute floor |
|---|---|---|
| Mean torque (energy method) | 0.3 % | 0.05 % of the duty's rated torque |
| No-load EMF fundamental | 0.3 % | 0.1 % of the rated phase voltage |
| Torque ripple | 0.5 percentage points | — |
| Cogging torque peak-to-peak | 10 % | 0.1 % of the rated torque |
| Copper loss, iron loss (each) | 2 % | 0.1 % of the total loss |
| Magnet eddy loss | 3 % | 0.1 % of the total loss |
| Shaft eddy loss, sleeve eddy loss | 5 % | 0.1 % of the total loss |
| Total loss | 2 % | — |
| Electrical input power | 0.3 % | — |

**Solver balance.** The field power-balance residual
(`power_balance.residual_rel`) of the two meshers differs by <= 0.1
percentage points of the input power. The residual itself (-2 to -4 % on
these duties, post-processed iron loss sits outside the field balance) is a
solver property and is not judged here.

**Convergence of the reference.** A failed quantity is re-checked on a finer
mesh on both meshers before a verdict. The mesh-size knob of the geometry
mesher floors the cell area at 0.12 mm^2 (0.53 mm edge) and does not refine
the slip grid, so levels are not geometric and Richardson extrapolation is
not applied. The finest attainable level is the reference.

**Mesh quality (per half, gmsh backend).** Minimum angle >= 1.5 degrees;
aspect ratio (longest edge / (2 sqrt 3 inradius)) p99.9 <= 30. Campaign
worst: 2.05 degrees, 22.7.

**Resources.** Median over the runs: solve wall <= 1.25x Triangle and peak
RSS <= 1.35x Triangle; any single run <= 2x on both, and <= 2 GB RSS.

**Reproducibility.** In one environment (same gmsh build, one gmsh thread):
the mesh is bit-identical (`test_repeat_build_is_bit_identical`), and a solve
on an identical mesh repeats to <= 1e-4 relative (measured 5e-12 to 7.8e-5:
the eddy warm-up with extensions is the source). Across gmsh releases or
platforms bit identity is NOT required. Compatibility is the semantic
fingerprint (`test_semantic_fingerprint`): triangle counts within 5 %,
gap-ring node counts exact, every tag's area within 0.1 %, quality no worse
than 1.3x the gmsh 4.15.2 reference. A solve records the gmsh version in its
mesh-build notes (`geo_mesh.cdt_provenance`); production pins gmsh in
`requirements.txt`.

**Fail closed.** A selected backend that cannot load raises with the install
command and the Triangle alternative. A gmsh meshing failure raises
`GmshCDTError` after finalizing gmsh and releasing the process lock. The
budget preflight rejects a cross-section before gmsh runs, and the built
count is checked after. No path answers a gmsh failure with a different
mesher (not the whole-wedge build, not the plain gmsh build). Operators see
the error in the solve response and the log; the remedy is to fix the
geometry or, where licensed, select Triangle explicitly.

**S4 — switch the default to gmsh.** After S3 passes: a geo-mesh request goes
to gmsh even with `triangle` installed; Triangle remains selectable for one
release for cross-checks; saved duties re-run once and the changes recorded.

**S5 — remove Triangle.** Delete the CDT code in `geo_mesh.py`,
`geo_mesh_proto.py`, the `[triangle]` extra, `requirements-triangle.txt`, the
`requires_triangle` tests and the Docker build argument; update
THIRD_PARTY_NOTICES.md.
