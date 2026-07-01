# Mapped (transfinite) air-gap band — design + proven technique

**Goal (Vadim):** the `structured_gap` mesh must give EXACTLY the ring count the
Air-gap-fidelity slider asks for:

- 1/side → 2 rings, each width Gap/2
- 2/side → 4 rings, each Gap/4
- 3/side → 6 rings, each Gap/6

(K per half-gap → 2K total uniform rings across the gap.)  Behind the existing
`structured_gap` flag; **free mode must stay byte-for-byte unchanged.**

## Why the current approach fails (measured)

The current `structured_gap` partitions the gap into thin annular polygons + sets
their ring curves transfinite, then lets gmsh free-mesh the surface.  Two blockers:

1. **gmsh/OCC merge away thin intermediate rings** — a Gap/4 ≈ 0.04 mm annulus is
   below OCC's merge tolerance, so the intermediate ring simply isn't in the mesh.
2. **Geometry simplify mangles the survivors** — extracted ring node counts came
   back 86 / 6 / 256, not a uniform N.  So "re-triangulate between the rings" is
   impossible — there are no clean rings.

Also measured: at 1/side the gap DOES average Gap/2 per side (median radial edge
0.10 mm), but gmsh fills the thick rows unevenly (0.04–0.26 mm) → looks ragged.
At 3/side gmsh already grids the thin rows cleanly (exact 6 rings).

## PROVEN technique — transfinite SURFACE on a sector-split annulus

`_tf_annulus_test.py` (in the worktree root) proves gmsh gives EXACTLY K uniform
radial layers when the annulus is split into S sectors and each sector surface is
set transfinite (high-aspect elements are allowed under transfinite, so the fine
slip ring no longer forces subdivision):

```
K=1: radial levels [12.1, 12.3]                 → 480 tris = 12·20·1·2  EXACT
K=2: radial levels [12.1, 12.2, 12.3]           → 960 tris             EXACT
K=3: radial levels [12.1, 12.167, 12.233, 12.3] → 1440 tris            EXACT
```

Core loop (per sector s of S, M angular divisions, K radial layers):
```python
ia = geo.addCircleArc(inner[s], c, inner[s2]);  oa = geo.addCircleArc(outer[s], c, outer[s2])
r1 = geo.addLine(inner[s], outer[s]);           r2 = geo.addLine(inner[s2], outer[s2])
surf = geo.addPlaneSurface([geo.addCurveLoop([ia, r2, -oa, -r1])])
geo.mesh.setTransfiniteCurve(ia, M+1); geo.mesh.setTransfiniteCurve(oa, M+1)
geo.mesh.setTransfiniteCurve(r1, K+1); geo.mesh.setTransfiniteCurve(r2, K+1)
geo.mesh.setTransfiniteSurface(surf)
```

## Integration plan (the substantial part)

The sliding-band solver (`fem_solver_2d.py::_build_sliding_band_meshes` ~943) builds
a **rotor half** and a **stator half** via `build_mesh_from_polygons`, split at the
slip radius `mid_r`.  The gap is split: rotor half owns `r_ro → mid`, stator half
owns `mid → r_si`.

For `structured_gap`, replace the free gmsh gap of each half with a transfinite
sector annulus:

- **Rotor half:** transfinite annulus `r_ro → mid`, K layers, conforming to the
  rotor iron at `r_ro` and providing the uniform slip ring at `mid`.
- **Stator half:** transfinite annulus `mid → r_si`, K layers, conforming to the
  stator iron/teeth at `r_si` and the slip ring at `mid`.

**Conformity is the key requirement.** Two viable routes:

- **(A) One gmsh model, shared boundary circles.** Build the iron surfaces AND the
  gap sectors in the same gmsh model so they share the `r_ro` / `mid` / `r_si`
  circle curves → gmsh meshes conformingly, no manual welding.  Cleanest but needs
  the iron built with those circles as explicit shared curves.
- **(B) Separate meshes + weld.** Build the transfinite gap alone (exact rings) and
  the iron alone, then merge by node identity at the shared ring.  Requires forcing
  the iron's gap boundary onto the SAME uniform ring (S·M nodes) as the gap sector —
  i.e. the iron boundary curve must be the same transfinite ring.

**Invariants that must hold:**
- The **slip ring at `mid`** must remain a uniform ring on the angular grid
  (`2πj/N_slip`) so the existing sliding coupling (`_ring()` node identification,
  master–slave pairing) still works.  Set S·M = N_slip (or a divisor-consistent
  count) and place sector seams on grid angles.
- Sector seams live INSIDE each half (they must NOT cross `mid`), so the slip ring
  stays continuous for the sliding.
- Free mode (`structured_gap=False`) unchanged.
- `cadquery_geometry.py` is PROTECTED — do not touch geometry generation.

## Test criteria (must pass before merge)

1. Viewer/mesh at gap_layers 1/2/3 → EXACTLY 2/4/6 uniform rings (count radial
   levels in the gap; spacing Gap/(2K)).
2. Mesh conforming — no holes/overlaps, slip ring uniform, all triangles valid.
3. Transient solver runs; mean torque ≈ free mode (physics unchanged); losses sane.
4. `npx tsc --noEmit` clean; backend imports.

## Status
- [x] Core technique proven (`_tf_annulus_test.py`).
- [ ] Integration into `_build_sliding_band_meshes` (route A or B).
- [ ] Verify exact rings + solver.
