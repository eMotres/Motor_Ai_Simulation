# The mesher hang on `magnet_up_gap = 0.05` (Ø50) — cause and fix, 2026-09-21

## Symptom

`simulation.mesher.build_mesh_from_polygons` never returned on CIANO14 50
edited / L15 with `rotor_hole = 1.0`, `magnet_up_gap = 0.05`: 540 s, one core at
100 %, no exception. The neighbouring recesses (0, 0.02, 0.07, 0.1, 0.2, 0.3)
meshed in ~4.6 s. Identical on the 2401d16 geometry, so not a regression of
1ed8f6b / 19a77e3.

## Where it hung (evidence, not inference)

`faulthandler.dump_traceback_later` (25 s, repeating) in a child process and
`py-spy dump --pid --native` both put the process inside

```
gmsh.py:2185 in generate  ->  gmsh::model::mesh::generate  (gmsh-4.15.dll)
```

with a deep recursive native stack — **not** Triangle, **not** `_blunt_cusps`,
**not** `_steiner_cap_truncated`, and not any Python loop. Re-running with
`General.Terminal = 1` and `General.Verbosity = 99` named it exactly:

```
[ 90%] Meshing surface 132 (Plane, Frontal-Delaunay)
[ 90%] :-( There are 4 intersections in the 1D mesh (curves 4592 4740 4590 4738)
[ 90%] 8-| Splitting those edges and trying again - level 0
                                             ... level 1 ... 2 ... 115 ...
```

gmsh's 2-D edge recovery splits the offending edges and retries, and the retry
has no effective bound at the default `Mesh.MaxRetries = 10`: it was still
climbing past level 115 when the process was killed. Each level re-triangulates
a larger point set, so the cost grows super-linearly — a hang, by construction.

The four curves are at the **stator bore**:

| curve | r (mm) | θ (deg) | what it is |
|---|---|---|---|
| 4590 | 15.1004 → 15.1000 | 37.553 → 37.969 | chord of the bore ring |
| 4592 | 15.1228 → 15.1060 | 37.794 → 37.681 | the slot wall beside it |

and 4738 / 4740 are their images at −142°. The bore ring itself doubles back:

```
[28] th 36.5625  r 15.1000      bore station
[29] th 37.96875 r 15.1000      bore station — 0.416 deg PAST the tooth corner
[30] th 37.5526  r 15.1004      tooth corner   <- the ring walks back to it
[31] th 37.6808  r 15.1060      slot wall
```

## Cause

The bore and OD circles are FIXED 256-gons (`_circle_points`), so their chords
sag ~1.1 µm inside the ideal circle. A slot or tooth corner placed on the
**exact** circle therefore sticks out of the discretised ring by ~1.9 µm, and
the Shapely union has to bulge out to the corner and come straight back. The
result is a needle a couple of microns wide — two flanks 5.9 µm apart, well
below anything gmsh's edge recovery can resolve. The 0.05 recess does not
create the needle; it only changes the global partition enough for gmsh to trip
over it at that one value. The same family of needles is on the Ø200 (see
below), where gmsh happened to recover after 80 complaints and left two
sub-degree slivers in the mesh instead of hanging.

## Fix

1. **`mesher._repair_needles`** — applied to every ring that reaches OCC
   (`_shapely_to_occ`) and to the mechanical rotor mesher
   (`mechanical/rotor_stress.add_ring`). Where a ring folds back on itself and
   the two flanks are closer than `SB_NEEDLE_TOL_MM` (default 0.01 mm), the
   corner is snapped ONTO the neighbouring chord and the overshooting vertex is
   dropped.
   The snap is what makes it **conformity-preserving**: the moved corner lies
   exactly on the edge the touching rings (air gap, outer band) still use, so
   OCC only splits that edge there. Deleting the shared station instead — the
   first attempt — desynchronises the rings and puts a 2·10⁻²⁴ mm² triangle in
   the mesh.
2. **`Mesh.MaxRetries` bounded** (`SB_MESH_MAX_RETRIES`, default 3) so
   `generate(2)` always returns, plus **gmsh's own log read back** afterwards:
   an "intersections in the 1D mesh" / "Could not recover" line becomes a
   `MeshingError` naming it, so the UI shows an error instead of freezing.
   `SB_MESH_STRICT=0` downgrades that to a log line.
   A 120 s watchdog (`SB_MESH_WATCHDOG_S`) logs an error while gmsh is still
   inside `generate`, and `_preflight_near_touching` WARNS with coordinates for
   every sub-tolerance curve pair. The pre-flight is deliberately not a
   refusal: four of the thirteen recess cases carry such a pair at the
   pocket-wall/fillet junction and mesh perfectly well.

## Measured

Ø50 CIANO14 50 edited / L15, `mesh_size 1.5`, magnetic mesh:

| rotor_hole | gap | s | elements | min angle (deg) | min area (mm²) | n(<1°) |
|---|---|---|---|---|---|---|
| 0.9 | 0.00 | — | validation refusal (8258ad0) | | | |
| 0.9 | 0.02 | 3.6 | 15 492 | 1.4149 | 9.69e-11 | 0 |
| 0.9 | 0.05 | 3.6 | 15 808 | 1.4149 | 5.55e-11 | 0 |
| 0.9 | 0.07 | 3.6 | 15 850 | 1.4149 | 9.79e-11 | 0 |
| 0.9 | 0.10 | 3.5 | 15 908 | 1.4149 | 1.34e-10 | 0 |
| 0.9 | 0.20 | 3.6 | 16 227 | 1.4149 | 1.34e-10 | 0 |
| 0.9 | 0.30 | 3.7 | 16 244 | 1.4149 | 1.34e-10 | 0 |
| 1.0 | 0.00 | 3.5 | 15 709 | 1.4149 | 1.34e-10 | 0 |
| 1.0 | 0.02 | 3.4 | 15 667 | 1.4149 | 1.34e-10 | 0 |
| 1.0 | **0.05** | 4.4 | 15 883 | 1.3910 | 1.34e-10 | 0 |
| 1.0 | 0.07 | 4.5 | 15 803 | 1.2885 | 1.34e-10 | 0 |
| 1.0 | 0.10 | 4.5 | 15 926 | 1.4149 | 1.34e-10 | 0 |
| 1.0 | 0.20 | 4.2 | 16 040 | 1.4149 | 1.34e-10 | 0 |
| 1.0 | 0.30 | 3.6 | 15 948 | 1.4149 | 1.34e-10 | 0 |

Before the fix the (1.0, 0.05) row never finished; (1.0, 0.10) meshed in 6.6 s
into 19 397 elements with a 0.2955° worst angle and 7 sub-degree elements — the
needles were driving ~1 700 elements of local refinement each.

Ø200 `CIANO10 200 opt` / L155 motor (rotor_hole 1, gap 0, sleeve):

| | s | elements | min angle | min area | n(<1°) |
|---|---|---|---|---|---|
| before | 12.7 | 149 827 | 0.1022° | 2.02e-11 | 2 |
| after | 7.7 | 149 708 | 1.8636° | 2.19e-09 | 0 |

−0.08 % elements, 18× better worst angle, 39 % less meshing time. With the
repair switched off (`SB_NEEDLE_TOL_MM=0`) the new log check refuses this mesh —
gmsh emitted 80 "intersections in the 1D mesh" lines on it and recovered only
by luck; those two sub-degree slivers are what it left behind.

## Mechanical mesh, per pocket (the residual-sliver question)

Measured on the same machine, rotor mesh `mesh_size 1.5 / min 0.25 / weld 0.02`,
all 28 wall–fillet junctions (14 poles × 2 sides):

| gap | rotor min angle | rotor min area | junctions with a sub-degree element | worst junction |
|---|---|---|---|---|
| 0.05 | 3.7790° | 2.50e-4 mm² | 0 of 28 | pole 0R / 6L / 7R / 13L, θ 17.1° |
| 0.10 | 3.9332° | 5.44e-4 mm² | 0 of 28 | pole 5L / 12L, **θ 317.2°** |

Also clean at `mesh_size 0.8` (4.6814°) and `0.5` (8.0663°). The worst junction
at gap 0.10 is exactly the θ ≈ 317° pole the owner pointed at, but it is a
3.93° element at a 159.7° re-entrant polygon corner, i.e. the geometry's own
stress raiser — **not** a degenerate element, and not reproducible as one here.

What IS a real defect at gap 0.05: the rotor polygon has a 0.000° fold-back at
r = 14.637 mm on two of the fourteen poles (θ 85.775° and 265.775°) — the same
rotation-dependent needle, one pole per segment. `_repair_needles` now removes
it, though on this machine the mechanical mesh is unchanged by it, because that
mesher already welds 10 µm slivers (`Geometry.ToleranceBoolean = 1e-2`).
