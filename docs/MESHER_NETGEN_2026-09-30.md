# Netgen as a CDT backend of the geometry mesher (2026-09-30, PAUSED)

Status: **work in progress, paused at the weekly usage limit** (coordinator,
2026-09-30 20:00). The backend and its tests are done; the three-way solve
comparison is done for L12 and L155, half done for L13, and the campaign has
not run. Next steps are at the end.

## Why

Triangle's licence bars commercial use. gmsh is GPL-2.0-or-later and its
exception list does not cover Intel MKL, so with MKL/PARDISO it may only run
out of process. Netgen (`netgen-mesher`, NGSolve project) is **LGPL-2.1-only**;
its OCC kernel (`netgen-occt`) is OpenCASCADE Technology, LGPL-2.1 with the
OCCT exception. Both are dynamically loaded Python packages, so netgen may run
in-process next to MKL under any project licence.

## What was built

`src/motor_ai_sim/simulation/geo_mesh_netgen.py`, selected with
`MOTOR_AI_SIM_GEO_CDT=netgen` (`auto` stays gmsh). It has the same contract as
the gmsh backend:

| Contract item | How it works on netgen | Evidence |
|---|---|---|
| Constrained PSLG in, triangles out; input vertices first, bit-exact | OCC vertices/edges built once per PSLG vertex/segment and shared by the faces; netgen nodes mapped back to input vertices (1-ulp OCC round trip snapped, tolerance 1e-9 x scale) | `test_contract_on_a_toy_pslg` |
| Fixed boundary nodes (slip ring, band rings, skin/wire patch rims, cut rays) | every boundary segment is an OCC edge with an **empty** `Edge.partition`: its end vertices are its only nodes. Checked after meshing: an unsplit boundary edge is a mesh edge, else `NetgenCDTError` | `test_boundary_next_to_a_fine_neighbour_is_not_split`, `test_moving_band_rings` (1/2 sectors), `test_shaft_skin_layers_structure` |
| Size field and grading | interfaces get a fixed uniform partition at the gmsh backend's segment size (min(length, 2.5 x local feature size)) capped by the adjacent faces' targets; each face carries its region target as `Face.maxh`; netgen grades the interior from the boundary with `grading` = 0.5 | `test_per_part_size_acts_on_netgen`, `test_wire_cell_factor_acts_on_netgen`, `test_sleeve_resolution_follows_the_layer_request` |
| Embedded segments and free vertices (wire lattice, iron grading at the shaft skin) | glued into their face (`occ.Glue`) as internal edges/vertices | `test_dangling_segment_and_free_vertex_are_kept`; L155/L12 rotor cells |
| Tags | unchanged shared `_tag_stator`/`_tag_rotor`; every CAD section kept | `test_every_region_keeps_its_cad_section`, `test_same_sections_as_gmsh` |
| Determinism | `parallel_meshing=False`, fixed build order, a process lock | `test_contract_on_a_toy_pslg`, `test_repeat_build_is_bit_identical`, `test_every_cell_is_the_same_mesh` |
| Provenance | `mesher_provenance` records `netgen` version; `NETGEN_VALIDATED = 6.2.2607` | `test_backend_selection_and_provenance` |
| Fail closed | netgen reports an unmeshed face on stdout only, so every face's meshed area is checked (1e-7 rel.); any failure raises `NetgenCDTError`, lock released, no fallback to another mesher; missing netgen is an actionable error; budget preflight before meshing | `test_netgen_failure_is_loud_and_releases_the_lock`, `test_tile_does_not_fall_back_on_a_netgen_failure`, `test_missing_netgen_is_an_actionable_error`, `test_budget_preflight_rejects_before_meshing`, `test_mandatory_sliver_fails_closed_or_meshes_correctly` |

`tests/test_geo_mesh_netgen.py`: 22 passed; `tests/test_geo_mesh_gmsh.py`
(not slow, not the subprocess suites): 26 passed, after the shared
`_rotor_bulk_gate` refactor (server container, netgen 6.2.2607, gmsh 4.15.2,
triangle 20250106).

### Netgen API findings (they decide whether netgen is usable at all)

1. **`netgen.geom2d` cannot keep a segment whole.** Its 1-D partition
   re-divides every spline by the graded local size, and a finer neighbour or
   a domain cap drives that below the segment length. On the 40 mm stator
   cell it split 54 of 278 boundary segments at grading 0.3 and still 36 at
   grading 10 (also with `segmentsperedge` 0.2 and without domain caps).
   geom2d is therefore not usable.
2. **The OCC 2-D path honours an explicit per-edge partition**
   (`Edge.partition`). The values are **normalized** curve parameters in
   (0, 1); absolute parameters (0..L) make the surface mesher overlap and give
   up ("meshed area > maximal area"). An empty array keeps the edge whole.
   With it, 0 of 278 / 106 boundary segments were split, and every captured
   duty PSLG (L12, L13, L155; 12 cells) meshed with exact areas.
3. A face with holes must be built as `Face(Face(outer), [outer, *inner])`;
   `Face(face, inner)` alone ignores the holes. The inner wire orientation is
   chosen by the face area check.
4. **Limitation:** netgen's advancing front cannot close a region where a long
   unsplittable boundary edge faces a much finer feature (the cells the
   boundary forces are far larger than the graded local size). Triangle and
   gmsh build slivers there; netgen leaves the face unmeshed, which the
   backend turns into `NetgenCDTError` (a 2 x 1 mm box with one segment per
   side around a 0.02 mm interface box: fails at 0.05 and 0.3 mm gaps; with
   0.25 mm side segments it meshes). None of the saved duties hit it; the
   campaign was meant to measure how often optimizer candidates do.

## Mesh-only comparison (production builds, before any solve)

The first two geometry-driven builds of each duty's solve (the second is the
solve mesh), 1 CPU. Elements = stator + rotor; angle/aspect = worst half.

| Duty | Backend | Build time [s] | Elements | Min angle [deg] | Aspect p99.9 |
|---|---|---:|---:|---:|---:|
| L12 | Triangle | 0.18 | 9 978 | 2.49 | 22.0 |
| L12 | gmsh | 1.38 | 13 938 | **1.15** | **47.1** |
| L12 | netgen 0.3 / **0.5** / 1.0 | 0.61 / **0.56** / 0.64 | 17 722 / **14 338** / 10 700 | 4.75 / **5.71** / 0.91 | 7.7 / **6.8** / 56.0 |
| L13 @ 0.61 mm | Triangle | 0.26 | 17 647 | 6.49 | 5.4 |
| L13 @ 0.61 mm | gmsh | 1.38 | 20 013 | 4.67 | 7.4 |
| L13 @ 0.61 mm | netgen 0.3 / **0.5** / 1.0 | 0.75 / **0.71** / 0.62 | 24 185 / **20 653** / 16 709 | 12.25 / **10.06** / 3.82 | 3.8 / **3.6** / 9.1 |
| L155 | Triangle | 0.25 | 20 248 | 4.81 | 7.1 |
| L155 | gmsh | 1.85 | 26 206 | 2.13 | 6.9 |
| L155 | netgen 0.3 / **0.5** / 1.0 | 0.89 / **0.87** / 0.92 | 32 544 / **26 822** / 20 266 | 3.16 / **3.16** / 2.84 | 8.6 / **8.6** / 12.4 |

Grading 0.5 was chosen: the same element count as gmsh (0.97-1.03x) and a
better worst angle; 1.0 makes rotor slivers (0.91 deg on L12). Netgen builds
in about half gmsh's time, Triangle is 3-5x faster than both.
gmsh's L12 rotor (1.15 deg, aspect 47) is outside the acceptance gate of
MESHER_TRANSITION (>= 1.5 deg, p99.9 <= 30); netgen's is inside.

## Solve comparison (new defaults: Coulomb torque, gap 1/side, march eddy)

Container `deploy-api` (Python 3.11.16, scikit-fem 12.0.1, pypardiso 0.4.7),
4 threads, nice 19 / ionice idle, one job at a time next to at most one other
compute container; solve wall times therefore carry about +-10 % load noise.
Reference = Triangle; owner criteria: Coulomb torque <= 1 %, ripple within
max(0.5 pp, 10 %), total loss <= 5 %.

| Quantity | L12 gmsh | L12 netgen | L155 gmsh | L155 netgen | L13 gmsh | L13 netgen |
|---|---:|---:|---:|---:|---:|---:|
| Coulomb mean torque | -0.038 % | -0.032 % | +0.017 % | +0.025 % | -0.018 % | not run |
| Ripple p-p / mean (Triangle 6.19 / 1.63 / 4.50 %) | +0.004 pp | +0.187 pp | +0.005 pp | +0.016 pp | +0.025 pp | not run |
| Coulomb self-check (rel. to ripple scale; Triangle 0.023 / 0.012) | 0.021 | 0.022 | 0.012 | 0.012 | | |
| Copper loss | -0.006 % | -0.004 % | -0.036 % | -0.036 % | | |
| Iron loss | +0.18 % | +0.56 % | +0.21 % | +0.57 % | | |
| Magnet eddy | +0.06 % | +0.005 % | +0.17 % | +0.22 % | | |
| Shaft eddy | +1.14 % | -1.15 % | -0.76 % | +0.43 % | | |
| Sleeve eddy | — | — | +0.025 % | +0.014 % | | |
| Total loss | +0.024 % | +0.075 % | +0.061 % | +0.199 % | -0.010 % | not run |
| Mesh build, all builds of the solve [s] (Triangle 0.31 / 0.39 / 0.40) | 2.56 | 1.09 | 3.59 | 1.56 | 2.58 | |
| Solve wall [s] (Triangle 163 / 639 / 206) | 169 (+3 %) | 175 (+7 %) | 667 (+4 %) | 603 (-6 %) | 270 (+31 %, load) | |
| Peak RSS [MB] (Triangle 428 / 677 / 535) | 489 (+14 %) | 498 (+16 %) | 792 (+17 %) | 819 (+21 %) | 591 (+10 %) | |
| Owner criteria | pass | pass | pass | pass | pass | not run |

Absolute values: L12 0.6167 N·m, 66.07 W; L155 184.56 N·m, 3 834.9 W; L13
5.338 N·m, 194.55 W (Triangle). Netgen's import adds about 52 MB RSS and
0.12 s (OCCT); a fresh Python that imports and initializes gmsh costs 0.08 s,
so an out-of-process gmsh adds little per build beyond moving the PSLG and
the mesh across the process boundary.

## Preliminary reading (not yet a recommendation)

- Both backends meet the owner's accuracy criteria on L12 and L155 with wide
  margins; netgen's iron loss reads +0.56 % (gmsh +0.2 %), still far inside.
- Mesh quality: netgen better than gmsh on L12 and L13; on L155 its worst
  angle is better (3.2 vs 2.1 deg) and its aspect p99.9 worse (8.6 vs 6.9).
- Speed: netgen meshes about 2x faster than gmsh; solve time and RSS are the
  same within load noise at the same element count.
- Robustness: the open question. Netgen fails closed where a long frozen
  segment faces a much finer feature; gmsh meshes it with slivers. The
  cusp/fillet campaign decides this.
- Licence and maintenance: netgen can run in-process with MKL; gmsh needs the
  out-of-process wrapper being built. Netgen's backend depends on two API
  details found by experiment (normalized `Edge.partition`, face-with-holes
  construction) that a netgen release could change; the post-mesh checks make
  any such change fail loudly.

## Next steps (after the limit resets, 2026-10-03)

1. L13 CIANO28 85 20SW1200 / L13 / rated at 0.61 mm: the netgen solve
   (Triangle and gmsh are done, numbers above; die from the shared tree,
   `mesh_size_mm` 0.61, `torque_method` coulomb).
2. The campaign subset c00, c04, c05, c10, c12, c15, c19, c21 on all three
   backends (`scripts/mesher_campaign.py plan --solve --threads 4 --backends
   triangle,gmsh,netgen --only ...`), counting netgen's fail-closed rejects,
   time and RSS tails.
3. Write the recommendation (gmsh out of process vs netgen in process) into
   this file and the PR.

Reproduction: a throwaway container from `deploy-api` with
`python -m venv --system-site-packages` + `pip install netgen-mesher==6.2.2607`
(use `PYTHONPATH=<venv>/lib/python3.11/site-packages` with the system Python so
pypardiso finds MKL; `pip --target` loses the OCCT libraries), then
`scripts/compare_mesher_triangle_vs_gmsh.py run <spec>` per mesher with
`MOTOR_AI_SIM_GEO_CDT=<mesher>` and `report --meshers triangle,gmsh,netgen`.
Raw results of this run: the owner's Downloads,
`netgen_eval_2026-09-30/netgen_eval_out.tgz` (the server sandbox is deleted).
