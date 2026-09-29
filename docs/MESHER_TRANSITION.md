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

`geo_mesh=False` keeps the tensor iron template (`iron_template.py`, no
Triangle). The 2-D view uses `mapbox-earcut`; its fallback
(`earcut_fallback.py`) uses Triangle when installed, and otherwise shapely's
(GEOS) constrained Delaunay.

## S2 as built (2026-09-29): a gmsh backend for the geometry mesher

The whole geometry-driven mesher had exactly ONE Triangle call:
`geo_mesh._triangulate(V, S, ...)`, the quality CDT of a planar straight-line
graph that `geo_mesh.py` builds itself (resampled slip-grid arcs, 1 um snap,
clone-identical cuts, densified outlines, skin/wire patches as holes).
`geo_mesh_gmsh.triangulate_gmsh` replaces that one call; everything else is
shared, so every feature below exists on both backends by construction:

| # | Feature | On the gmsh backend |
|---|---|---|
| 1, 2 | Shaft / sleeve skin layers | same structured patches (`_skin_patch`, `_sleeve_layers`), left as holes and stitched; the patch rims are domain boundary, never split |
| 3 | Per-part sizes | same region seeds; each face is capped by a gmsh `Constant` field at its region's target edge (last seed in a face wins, the `_seeds` order) |
| 4 | Wire cell `coil_rel` | same wire patches / lattice (free points are embedded) |
| 5 | Sliver / cusp guards | `_blunt_cusps` runs before either backend; gmsh has no refinement cascade; a rotor bulk over the aspect gate gets the same guarded smoothing |
| 6 | Rotation invariance | same cell tiling; gmsh runs single-threaded, repeat builds are bit-identical; domain-boundary segments (cut rays) are frozen (transfinite 2 nodes), so the cuts stay clone-identical |
| 7 | Exact tagging | same `_tag_stator` / `_tag_rotor` on the real polygons |
| 8 | Moving-band rings | R1/R2 are domain boundary, frozen on the slip grid; a shared bug that moved the cell-seam node off R2 (1002 of 1008 nodes, both backends) is fixed |
| 9 | Mesh budget | predicted count at the target sizes is checked before meshing, the actual count after; both raise `MeshBudgetExceeded` |

Sizing on gmsh follows the physical scales the PSLG producer resolved: every
input segment carries h = min(its length, 2.5 x its local feature size); the
segments are grouped in x1.5 classes, each a `Distance` field feeding
h0 + 2.0 x distance (`MathEval`); the background mesh is the `Min` of those and
the region caps. Interior interfaces may be subdivided (as Triangle's `-Y`
allowed); domain boundaries never. Default meshes: ~1.7x Triangle's element
count on the 40 mm preset at 1 mm, aspect max 11.6 (Triangle 9.0).
Tests: `tests/test_geo_mesh_gmsh.py` (and the skin and shaft-region suites,
which now run on whichever backend is active and are re-run forced onto gmsh).

## Features only the Triangle mesher had (from the code, before S2)

These were what stage S2 had to port or replace on the gmsh path:

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
no-load), sandboxed, `nice 19`, threads <= 6; usage in its docstring. Proposed
acceptance criteria (gmsh vs Triangle, same duty and settings) — **for the
owner to decide**:

| Quantity | Proposed limit |
|---|---|
| Mean torque (energy method) | <= 0.3 % |
| No-load EMF fundamental | <= 0.3 % |
| Torque ripple | <= 0.5 percentage points |
| Cogging torque peak-to-peak | <= 10 % |
| Copper and iron loss | <= 2 % each |
| Magnet eddy loss | <= 3 % |
| Shaft eddy loss | <= 5 % |
| Sleeve eddy loss (where present) | <= 5 % |
| Total loss | <= 2 % |
| Repeat build | bit-identical mesh hash |

A case outside a limit sends the relevant feature back to S2 (a mesh
convergence check on both meshers first, to separate a mesher bias from a
discretisation error).

**S4 — switch the default to gmsh.** After S3 passes: a geo-mesh request goes
to gmsh even with `triangle` installed; Triangle remains selectable for one
release for cross-checks; saved duties re-run once and the changes recorded.

**S5 — remove Triangle.** Delete the CDT code in `geo_mesh.py`,
`geo_mesh_proto.py`, the `[triangle]` extra, `requirements-triangle.txt`, the
`requires_triangle` tests and the Docker build argument; update
THIRD_PARTY_NOTICES.md.
