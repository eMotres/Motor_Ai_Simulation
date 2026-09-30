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

| `triangle` installed | Build |
|---|---|
| yes | geometry-driven CDT mesher (`geo_mesh_halves`), exactly as before |
| no | gmsh build, one log line: "optional 'triangle' not installed — geometry-driven mesh unavailable, using the gmsh build" |

`geo_mesh=False` keeps the tensor iron template (`iron_template.py`, no
Triangle). The 2-D view uses `mapbox-earcut`; its fallback
(`earcut_fallback.py`) uses Triangle when installed, and otherwise shapely's
(GEOS) constrained Delaunay.

## Features only the Triangle mesher has (from the code)

These are what stage S2 must port or replace on the gmsh path:

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

## Licensing recommendation (2026-09-30, draft for legal review)

Two options for what happens to the Triangle code path, requested for the
licensing pack alongside the Intel MKL exception (see repo root
LICENSE-EXCEPTION):

**Option A — delete the Triangle code path** (the CDT mesher in
`geo_mesh.py`/`geo_mesh_proto.py`, `requirements-triangle.txt`, the
`[triangle]` extra, the `WITH_TRIANGLE` Docker arg, the `requires_triangle`
tests). This is already stage S5 of the plan above. Consequence: the
licensing question disappears entirely — nothing non-commercial-only is in
the repository or any image MOTRES builds, so no AGPL-combination analysis
and no reliance on "MOTRES's use is non-commercial" is needed for meshing at
all. Cost: loses the ten Triangle-only features listed above
until stage S2 ports them to gmsh and S3 confirms the numbers on the
reference machines; deleting before S3 passes would change published
results with no controlled comparison.

**Option B — keep Triangle as an optional, user-installed plugin that MOTRES
never distributes.** The code path stays, gated behind `requirements-triangle.txt`
/ the `[triangle]` extra, exactly as CONTRIBUTING.md already requires for any
non-AGPL-compatible dependency; the difference from today is that **MOTRES
stops building it into any image it runs as a service or hands to anyone
else** (`--build-arg WITH_TRIANGLE=1` is never used again for a server or
distributed build) and only a developer's own local, personal, genuinely
non-commercial workstation build may install it, at that person's own risk
under Triangle's licence. Consequence: keeps the reference mesher available
for the S2/S3 comparison work without redistributing it. Cost: depends
indefinitely on a "genuinely non-commercial, never distributed" boundary
being maintained correctly by every engineer who builds the image, which is
an ongoing audit burden, not a one-time fix.

**Recommendation: Option B immediately, Option A at S5.** The production
server currently ships with `--build-arg WITH_TRIANGLE=1`
(`triangle==20250106`, confirmed by `pip list --format=freeze` inside the
running `deploy-api-1` container, 2026-09-30). THIRD_PARTY_NOTICES.md's
justification for that build is "MOTRES currently uses the project
non-commercially" — but other MOTRES material (`eMotres/AGENTS.md`) refers
to "the aerostator.com site / shop", which is at minimum a strong signal
that this premise needs an explicit, current yes/no from the owner and
counsel before it is relied on any further; Triangle's non-commercial
restriction is a real compliance risk on the live server today, independent
of anything AGPL. Until that is confirmed either way:

1. Stop building the production/deployed image with `WITH_TRIANGLE=1`
   immediately (do not wait for S2/S3 gmsh parity) — fall back to gmsh in
   production, accepting the S2 feature gaps and any numeric drift on the
   server until they are ported and validated.
2. Triangle may stay installed on the owner's own local workstation for the
   S2/S3 comparison work (`scripts/compare_mesher_triangle_vs_gmsh.py`) —
   that is the plausible non-commercial use, not the production server.
3. Proceed with S2 (port the ten Triangle-only features to gmsh) and S3
   (the acceptance-limit comparison already proposed above) on that
   timeline; once S3 passes, do Option A (S4 switch default, S5 delete) as
   already planned, closing the question for good rather than leaving a
   permanent "optional, trust the operator" carve-out.
