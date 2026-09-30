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

## Licensing recommendation (2026-09-30, draft for legal review; revised
twice the same day -- after an independent review, then after an owner
architecture decision)

### Triangle's exact terms (not paraphrased)

J. R. Shewchuk's own page (https://www.cs.cmu.edu/~quake/triangle.html)
states: "although Triangle is freely available, it is copyrighted by
the author and may not be sold or included in commercial products
without a license." An earlier pass of this document paraphrased this
as a blanket "free for non-commercial use only," which overstates it:
the author's own wording restricts *selling Triangle or including it in
a commercial product*, not all commercial *use* of software that merely
calls it. The distinction matters for MOTRES (see below) and counsel
should read the author's terms directly rather than rely on either
paraphrase. The installed wrapper, `triangle==20250106`, wraps Triangle
1.6 and is itself LGPL-3.0 per its own `dist-info/METADATA` -- a
separate statement from the bundled C code's terms above, and not a
relicensing of them.

### Owner decision: Triangle is removed, not staged out

Originally this section offered two options (delete the Triangle code
path now at some future stage S5, vs. keep it as an optional,
never-distributed plugin as a bridge). The owner decided 2026-09-30 to
**remove Triangle outright now**: delete the CDT mesher in
`geo_mesh.py`/`geo_mesh_proto.py`, `requirements-triangle.txt`, the
`[triangle]` extra, the `WITH_TRIANGLE` Docker argument and compose
default, and the `requires_triangle` tests (this collapses stages S4-S5
above into one immediate step rather than waiting on S2/S3 gmsh
parity). A third option -- **obtaining a commercial licence from the
author for continued use, so the S2/S3 comparison work could keep using
Triangle as the reference mesher while gmsh is ported and validated** --
was not previously considered and is recorded here for completeness;
the owner did not choose it. Consequence of removal-now: gmsh becomes
the mesher immediately, with the ten Triangle-only features listed above
not yet ported (S2) and not yet validated against the reference machines
(S3). **This document does not recommend accepting the resulting
numerical drift as an acceptable production state.** Each configuration
that switches to gmsh needs the S3 acceptance-limit comparison run
against it (or an equivalent per-configuration check) before its results
are published or relied on; a configuration that has not been checked
should either be re-verified before its next use or have that
calculation suspended until it is. "The Docker build flag changed" is
not evidence that the two meshers agree for a given geometry -- S2/S3 is
the actual verification, and it is now urgent rather than a
background task, since Triangle is no longer available as a fallback
once removed.

### Gmsh and MKL in the same process (now scoped to the optional PARDISO backend only)

Separately from Triangle: while `pypardiso`/Intel MKL was still a
production dependency (`WITH_PARDISO=1`, before the 2026-09-30 decision
below), gmsh's own Python API (`import gmsh`) and pypardiso/MKL would
have loaded into the *same process* for any calculation that meshes with
gmsh and solves with PARDISO. Gmsh's own GPL exception
(https://gmsh.info/LICENSE.txt) names Netgen, METIS, OpenCASCADE and
ParaView -- not MKL -- so it does not itself authorise this combination;
see docs/LICENSE-EXCEPTION-NOTES.md Section 3. Because the 2026-09-30
architecture decision removes MKL/`pypardiso` from the default image and
default hosted service, **this is no longer a concern for the default
build** -- gmsh now runs with no MKL present. It remains relevant only
for an operator who deliberately enables the optional PARDISO backend
(`requirements-pardiso.txt`) alongside gmsh. For that optional
combination, the options are (marked for counsel, not decided here):

1. **Run gmsh out of process from pypardiso/MKL**, exchanging mesh data
   through files (or a subprocess boundary) rather than importing both
   in one Python interpreter -- the "mere aggregation" argument depends
   on the two remaining genuinely separate programs communicating through
   an arm's-length interface, not on merely being in different function
   calls of the same process; whether a file-exchange boundary is enough
   for that argument is a legal question, not a technical one this
   document resolves.
2. **Use an MKL-free solver in that configuration** (CHOLMOD or MUMPS,
   now the defaults anyway per the decision below) instead of PARDISO,
   removing the question entirely for that run. Per the coordinator's
   engineering context, CHOLMOD is reported at roughly 1.1-2.8x slower
   per solve than PARDISO (roughly +30-40% total run time) -- a figure
   supplied by the coordinator, not independently reproduced by this
   review; before this is relied on for a capacity or scheduling
   decision, re-measure it against the actual solver benchmark scripts
   in this repository (`bench_solvers.py`, `scratch_perf/bench_pardiso.py`)
   on the current default stack.
3. **Obtain Intel's explicit permission** for the specific gmsh+MKL
   same-process combination, in addition to (not instead of) the
   MOTRES-granted exception in LICENSE-EXCEPTION, if an operator wants
   to keep running both in one process.
This note no longer describes MOTRES's own default deployment; it is
retained for whoever enables the optional PARDISO backend.

### Owner architecture decision, 2026-09-30

Default server image and default distribution: Intel MKL/`pypardiso`
removed entirely; CHOLMOD (via `scikit-sparse`) and MUMPS become the
default direct-solver backends (licence character recorded in
THIRD_PARTY_NOTICES.md's "Planned default direct-solver stack" table;
package names/pins/implementation not yet done as of this revision).
Gmsh becomes the default mesher once the per-configuration verification
above passes. Triangle is removed outright (previous section).
`pypardiso`/MKL remains available only as an operator's own opt-in
install, covered by the narrowed, optional
[LICENSE-EXCEPTION](LICENSE-EXCEPTION) (**DRAFT**).
