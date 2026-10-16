# Netgen as a CDT backend of the geometry mesher (2026-09-30)

Status: **evaluation complete.** The backend and its tests are done; the
three-way solve comparison is done for L12, L155 and L13; the cusp/fillet
campaign ran on all three backends. **Recommendation: netgen as the default
CDT backend** (section "Recommendation" at the end).

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
| Coulomb mean torque | -0.038 % | -0.032 % | +0.017 % | +0.025 % | -0.260 % | -0.441 % |
| Ripple p-p / mean (Triangle 6.19 / 1.63 / 31.82 %) | +0.004 pp | +0.187 pp | +0.005 pp | +0.016 pp | -0.198 pp | -0.021 pp |
| Coulomb self-check (rel. to ripple scale; Triangle 0.023 / 0.012 / 0.012) | 0.021 | 0.022 | 0.012 | 0.012 | 0.013 | 0.012 |
| Copper loss | -0.006 % | -0.004 % | -0.036 % | -0.036 % | -0.000 % | -0.000 % |
| Iron loss | +0.18 % | +0.56 % | +0.21 % | +0.57 % | -0.004 % | -0.020 % |
| Magnet eddy | +0.06 % | +0.005 % | +0.17 % | +0.22 % | -0.058 % | -0.002 % |
| Shaft eddy | +1.14 % | -1.15 % | -0.76 % | +0.43 % | -0.203 % | -0.012 % |
| Sleeve eddy | — | — | +0.025 % | +0.014 % | — | — |
| Total loss | +0.024 % | +0.075 % | +0.061 % | +0.199 % | -0.010 % | -0.001 % |
| Mesh build, all builds of the solve [s] (Triangle 0.31 / 0.39 / 0.39) | 2.56 | 1.09 | 3.59 | 1.56 | 2.57 | 1.23 |
| Elements, solve mesh (Triangle 9 978 / 20 248 / 17 647) | 13 938 | 14 338 | 26 206 | 26 822 | 20 013 | 20 653 |
| Min angle / aspect p99.9, worst half (Triangle 2.49/22.0, 4.81/7.1, 6.49/5.4) | **1.15 / 47.1** | 5.71 / 6.8 | 2.13 / 6.9 | 3.16 / 8.6 | 4.67 / 7.4 | 10.06 / 3.6 |
| Solve wall [s] (Triangle 163 / 639 / 430) | 169 (+3 %) | 175 (+7 %) | 667 (+4 %) | 603 (-6 %) | 490 (+14 %) | 513 (+19 %) |
| Peak RSS [MB] (Triangle 428 / 677 / 645) | 489 (+14 %) | 498 (+16 %) | 792 (+17 %) | 819 (+21 %) | 728 (+13 %) | 687 (+6 %) |
| Owner criteria | pass | pass | pass | pass | pass | pass |

Absolute values (Triangle): L12 0.6167 N·m, 66.07 W; L155 184.56 N·m,
3 834.9 W; L13 0.7823 N·m, ripple 31.82 %, 275.20 W.

**L13 run (2026-09-30 evening), all three backends re-run together.** The L13
columns come from one local three-way run, not from the paused server run:
the inputs are the committed copies in `scripts/gap_layers_study/inputs/l13`
(die + L13 duty `rated`, magnets 210.7 °C with demag, the 2026-09-16 shared
materials library) at `mesh_size_mm` 0.61, `torque_method` coulomb,
`gap_layers` 1 per side, march warm-up (settled on all three), 120 steps per
period, 4 sectors. The solve meshes are **identical in element count to the
server builds** (17 647 / 20 013 / 20 653), so the meshes are the same; the
absolute torque differs from the server's 5.338 N·m because the committed
materials library demagnetizes the magnets much more at 210.7 °C (the same
regime as MESHER_COMPARISON section 2, 0.92 N·m). In that regime the result
follows the per-element demag pattern, which MESHER_COMPARISON measured at
±0.5 % for any rotor-mesh change on either mesher; netgen's -0.44 % torque sits
inside that band and inside the 1 % criterion. Losses agree within 0.02 %.
Environment of this run: WSL2 Ubuntu on the owner's workstation, Python
3.11.15, gmsh 4.15.2, netgen-mesher 6.2.2607 (netgen-occt 7.8.1), triangle
20250106, numpy 2.4.4, scipy 1.17.1, scikit-fem 12.0.1, pypardiso 0.4.7 with
mkl 2026.1.0 installed `--no-deps` (no intel-openmp),
`MKL_THREADING_LAYER=SEQUENTIAL`, 4 threads, `nice 19`, one solve at a time.
PARDISO was available (checked), so no run used the SciPy fallback. The wall
times carry desktop load noise (about ±10 %); gmsh and netgen are within it
of each other.

**Windows note.** On the owner's Windows 11 workstation, native netgen does
not import: Windows App Control blocks netgen's unsigned OCCT DLLs
(`WinError 4551`), the same policy that blocks earcut/OCP `.pyd` files. The
runs therefore used WSL2. The Linux server and containers are unaffected;
a native Windows solve node would need the DLLs allowed by policy.

Netgen's import adds about 52 MB RSS and
0.12 s (OCCT); a fresh Python that imports and initializes gmsh costs 0.08 s,
so an out-of-process gmsh adds little per build beyond moving the PSLG and
the mesh across the process boundary.

## Robustness mini-campaign (8 cusp/fillet candidates, three backends)

`scripts/mesher_campaign.py plan --solve --threads 4 --backends
triangle,gmsh,netgen --only c00,c04,c05,c10,c12,c15,c19,c21`: the candidates
of MESHER_COMPARISON section 5 (same seed), optimizer budget armed (400 000
tris), 1 mm mesh, then a 24-step transient at 10 A / 3000 rpm, each run in its
own child process. Same environment as the L13 run above. The campaign now also
records Coulomb torque/ripple and total loss, and its report states the owner's
criteria and the quality gate per candidate.

**These candidates are outside the optimizer's envelope.** Every one has
`rotor_fill_r` below the optimizer's 0.15 mm fillet floor (0 to 0.1 mm), and
c04/c10/c15/c19 also have a 0.04 to 0.2 mm magnet fillet, a 0.067 mm slot
opening or a 0.02 mm magnet recess. They are a stress test of the meshers, not
designs the optimizer would propose.

| | Triangle | gmsh | netgen |
|---|---:|---:|---:|
| Meshed / solved | 8 / 8 | 8 / 7 | 8 / 8 |
| Fail-closed rejects | 0 | **1** (c04: `GmshCDTError`, 52 zero-area triangles in the solve's mesh build) | **0** |
| Budget rejects / crashes | 0 / 0 | 0 / 0 | 0 / 0 |
| Quality gate (>= 1.5 deg, p99.9 <= 30), worst half | pass (4.79 deg, 8.3) | pass (4.79 deg, 8.4) | pass (3.89 deg, 9.1) |
| Elements (min / median / max) | 13 388 / 13 668 / 14 388 | 16 988 / 24 934 / 29 472 | 19 188 / 23 760 / 26 260 |
| Mesh build (median / max) [s] | 0.24 / 0.26 | 1.03 / 1.60 | 0.60 / 0.71 |
| Solve wall vs Triangle (min / median / max) | 1 | 0.99 / 1.41 / **1.99** | 0.73 / 1.24 / 1.29 |
| Peak RSS vs Triangle (min / median / max) | 1 (493-543 MB) | 1.12 / 1.32 / 1.43 (max 754 MB) | 0.97 / 1.26 / 1.34 (max 721 MB) |
| Coulomb torque vs Triangle, worst | — | -0.17 % | +0.61 % (c04) |
| Total loss vs Triangle, worst | — | +1.13 % | -1.75 % (c04) |
| Ripple within max(0.5 pp, 10 %) | — | 6 / 7 (c19 +4.84 pp) | 6 / 8 (c04 +10.5 pp, c19 +4.79 pp) |

Per candidate (Coulomb torque / ripple / total loss vs Triangle; Triangle
ripple in brackets):

| id | gmsh | netgen |
|---|---|---|
| c00 (32.3 %) | -0.11 % / +1.24 pp / +0.13 % | -0.04 % / +1.26 pp / +0.38 % |
| c04 (41.6 %) | reject (fail closed) | +0.61 % / **+10.50 pp** / -1.75 % |
| c05 (15.7 %) | +0.05 % / -0.14 pp / +0.05 % | +0.11 % / -0.18 pp / +0.32 % |
| c10 (24.6 %) | -0.17 % / +2.12 pp / +1.13 % | +0.06 % / +2.33 pp / +1.41 % |
| c12 (32.0 %) | -0.07 % / +0.51 pp / +0.20 % | -0.04 % / -0.13 pp / -0.72 % |
| c15 (24.5 %) | -0.07 % / +1.67 pp / +0.05 % | +0.09 % / +2.01 pp / +0.31 % |
| c19 (30.7 %) | -0.16 % / **+4.84 pp** / +0.03 % | -0.08 % / **+4.79 pp** / +0.26 % |
| c21 (16.4 %) | -0.16 % / +0.95 pp / +0.20 % | -0.02 % / +0.67 pp / +0.39 % |

Torque (<= 1 %) and total loss (<= 5 %) pass on every solved candidate on both
backends. The two ripple misses were checked at 0.5 mm (same candidates, all
three backends, `CAMPAIGN_MESH_MM=0.5`):

| id @ 0.5 mm | Triangle ripple | gmsh | netgen |
|---|---:|---|---|
| c19 | 34.96 % (was 30.73 % at 1 mm) | -0.05 % / +0.45 pp / -0.06 % | +0.03 % / +0.56 pp / +0.08 % |
| c04 | 42.67 % | +0.40 % / +13.3 pp / -2.05 %, magnet eddy -13.2 % | -0.08 % / +9.79 pp / -2.16 %, magnet eddy -12.4 % |

- **c19:** at 1 mm it was Triangle that was under-resolved (its ripple moves
  +4.2 pp on refinement, gmsh/netgen move 0.2 pp). At 0.5 mm all three agree
  within 0.6 pp: pass on both.
- **c04:** gmsh (which meshes it at 0.5 mm) and netgen agree with each other
  (ripple 56.0 / 52.5 %, magnet eddy -13.2 / -12.4 %) and both disagree with
  Triangle. This is the c04 difference already open in MESHER_COMPARISON
  (magnet eddy -12 % in the 0.067 mm slot opening, which the mesh-size knob
  does not refine), now seen on both gmsh and netgen. It is not a netgen
  defect; which mesher is right needs the boundary-refinement check listed
  there.
- **gmsh's c04 reject at 1 mm** is new on this platform (the server run of
  2026-09-29 solved it): the campaign mesh built, the solve's own build
  (different slip grid) produced zero-area triangles and the backend failed
  closed as designed. It meshes at 0.5 mm.
- **Netgen's known limitation** (a long frozen segment facing a much finer
  feature) did not trigger on any of the 8 + 2 runs, although all of them are
  below the optimizer's fillet floor.

## Recommendation: netgen as the default CDT backend

| Criterion | gmsh | netgen | Better |
|---|---|---|---|
| Accuracy, saved duties (owner criteria) | pass L12, L155, L13 | pass L12, L155, L13 | equal |
| Accuracy, campaign | torque/loss pass; ripple misses only where Triangle or the geometry is under-resolved | same, same candidates | equal |
| Mesh speed | 2.6-3.6 s per solve; campaign median 1.03 s | 1.1-1.6 s; campaign median 0.60 s | netgen (about 2x) |
| Mesh quality gate | **fails on L12** (1.15 deg, p99.9 47.1) | passes everywhere (worst 3.16 deg / 9.1) | netgen |
| Robustness | 1 fail-closed reject in 8 (c04 at 1 mm) | 0 in 8 (+2 at 0.5 mm) | netgen |
| Tails (<= 2x Triangle) | solve wall max 1.99x, RSS max 1.43x | solve wall max 1.29x, RSS max 1.34x | netgen |
| Licence | GPL-2.0+, no MKL exception: must run out of process next to PARDISO (wrapper still to build, PSLG and mesh cross a process boundary) | LGPL-2.1 (+ OCCT LGPL with exception): in process next to MKL, dynamically loaded | netgen |
| Maintenance risk | mature API | backend relies on two API details found by experiment (normalized `Edge.partition`, face-with-holes construction); a known unmeshable configuration | gmsh |
| Platform | pip wheel, runs on Windows | OCCT DLLs blocked by Windows App Control on the owner's PC (Linux fine) | gmsh |

**Recommendation: make netgen the default (`auto` -> netgen), keep gmsh
selectable (`MOTOR_AI_SIM_GEO_CDT=gmsh`) as the cross-check backend, and keep
Triangle only as the optional non-commercial reference.** Netgen matches gmsh
on accuracy everywhere measured, meshes about twice as fast, is the only one of
the two that passes the mesh-quality gate on every saved duty, had no
fail-closed reject on the stress candidates, has the smaller solve-time and RSS
tails, and needs no out-of-process wrapper to live next to MKL. Its risks are
contained: the post-mesh checks (area, unsplit boundary edges) make any netgen
API change or the known unmeshable configuration fail loudly, never silently.

Conditions for the switch:
1. Pin `netgen-mesher==6.2.2607` (it is `NETGEN_VALIDATED`) in the solve image;
   bump only with `tests/test_geo_mesh_netgen.py` and a re-run of this
   campaign.
2. No automatic fallback to gmsh on a `NetgenCDTError` (fail closed stays);
   the optimizer counts such a candidate as a mesh reject, like a budget
   reject.
3. Native Windows solve nodes need the netgen OCCT DLLs allowed by App
   Control, or run under WSL2/Linux.
4. Still open, independent of the choice: c04's Triangle-vs-both difference
   (boundary-refinement check, MESHER_COMPARISON section 7 item 2).

Raw results of the local runs (L13 three-way, campaign, 0.5 mm check): the
owner's Downloads, `netgen_eval_2026-09-30/netgen_eval_local_out.tgz`.

Reproduction of the local runs: WSL2 Ubuntu, `uv venv -p 3.11`, `pip install
-r requirements.txt netgen-mesher==6.2.2607 triangle==20250106`, then `pip
install --no-deps mkl pypardiso==0.4.7` (never intel-openmp) and
`MKL_THREADING_LAYER=SEQUENTIAL`; gmsh needs `libglu1-mesa libxcursor1 libxft2
libxinerama1`. L13: a dies copy holding `CIANO28 85 20SW1200/{die,L13}.yaml`
and a config copy with `materials_library_shared.yaml` as
`materials_library.yaml`, one `compare_mesher_triangle_vs_gmsh.py run` per
mesher with the spec keys `"mesh_size_mm": 0.61` and `"kw_override":
{"torque_method": "coulomb", "gap_layers": 1}`, then `report --meshers
triangle,gmsh,netgen`.

Reproduction of the earlier server runs: a throwaway container from `deploy-api` with
`python -m venv --system-site-packages` + `pip install netgen-mesher==6.2.2607`
(use `PYTHONPATH=<venv>/lib/python3.11/site-packages` with the system Python so
pypardiso finds MKL; `pip --target` loses the OCCT libraries), then
`scripts/compare_mesher_triangle_vs_gmsh.py run <spec>` per mesher with
`MOTOR_AI_SIM_GEO_CDT=<mesher>` and `report --meshers triangle,gmsh,netgen`.
Raw results of this run: the owner's Downloads,
`netgen_eval_2026-09-30/netgen_eval_out.tgz` (the server sandbox is deleted).
