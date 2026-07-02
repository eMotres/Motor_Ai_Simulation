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
- [x] Integration into `_build_sliding_band_meshes` (route A).
- [x] Verify exact rings + solver.
- [x] CLEAN arc boundary — ε retract + filler REMOVED (see bottom section, 2026-07-01).

## ROUTE A — PROVEN (2026-07-01, Vadim's cylinder idea)

`_routeA_PROVEN.py`: build the gap as concentric cylinder-sector cells (2K radial × S
angular) as OCC surfaces IN THE SAME MODEL as the iron, `occ.fragment` everything, then
set each gap cell transfinite (arcs → M+1 nodes, radial edges → 2 nodes, surface
transfinite). Result: gap meshes as EXACTLY 2K+1 radial levels, uniform Gap/(2K), and
the whole thing is ONE conforming mesh — **fragment gives conformity for free, no manual
weld.** This is why route A beats route B (weld): the node-mismatch wall never appears.

Measured (r_ro=12.1, r_si=12.3, K=2): radial levels [12.1, 12.15, 12.2, 12.25, 12.3] = 4
uniform rings. One conforming mesh (2073 tris). Cell identification after fragment needs
care (found 72 of 96 — some merged with iron, but rings still exact).

### Integration (route A, for _build_sliding_band_meshes)
- Per half: rotor gap cells r_ro→mid, stator gap cells mid→r_si (seams don't cross mid).
- Build cells in the SAME OCC model as that half's iron (build_mesh_from_polygons), add
  to the fragment, set transfinite. Slip ring stays uniform at mid for the sliding.
- Free mode (structured_gap=False) unchanged.

## ROUTE A — IMPLEMENTED (2026-07-01)

Implemented in `fem_solver_2d.py` behind `structured_gap`. What it took beyond the proof:

1. **ε retract (the key enabler).** In the REAL motor the gap-facing iron is a FUZZY
   CadQuery polygon (hundreds of vertices ≈ r_ro / r_si), not the proof's clean OCC
   circle.  Those vertices land on the cells' inner/outer arcs and subdivide each cell
   into 5–10 corners → `setTransfiniteSurface` rejects them (needs 3/4).  Fix: pull the
   iron ε=10 µm OFF the arcs (`rotor ∩ disk(r_ro−ε)`, `stator − disk(r_si+ε)`).  Then
   ALL cells are clean 4-corner quads and mesh transfinite → EXACTLY 2K uniform rings.
   The stator (teeth/slots) needs the full 10 µm; 5 µm left ~half the stator cells
   subdivided.  ε is magnetically tiny (0.08 % of radius) and torque is FLAT vs ε.
2. **in_band / out_band exclude the gap ring.**  Subtract a clean annulus (`disk(mid)−
   disk(r_ro)`), drop chord/circle slivers, 10 µm simplify (else the OCC converter's
   loop-closure fails after the sector clip).  This stops the coarse air from
   subdividing the cells' mid arcs.
3. **ε bridge filler.**  The retract opens a µm void iron↔cells → non-conforming crack →
   DEAD field (torque 0).  `_build_structured_gap_cells` also emits a thin FREE-meshed
   filler layer (rotor r_ro−ε→r_ro, stator r_si→r_si+ε) that shares the cells' clean arc
   and meets the fuzzy iron → conforming.  Each filler cell is tagged with the material
   BEHIND it (iron under a tooth / between poles, air in a slot mouth / pole gap) —
   blanket-iron shorts the slot openings.  Restored in BOTH build paths
   (`build_mesh_from_polygons` classifier AND `_stitch_full_half` mirror-reclassify).

### Verified (40 mm 12s/14p, full_ring production path)
- Rings EXACT: gap_layers 1/2/3 → 2/4/6 uniform rings, spacing Gap/(2K).
- Slip ring UNIFORM on the global grid for both halves (n_slip nodes; ×2 on the full
  disk mirror), so the sliding coupling `_ring()` is untouched.
- Mesh CONFORMING: transient runs, field alive.
- Free mode (structured_gap=False) byte-for-byte unchanged (every structured branch is
  gated on the flag / the spec being present).

### TORQUE — converges toward free with more rings (RESOLUTION, not a leak)
Mean torque vs free (full_ring, 40 mm 12s/14p, I=60 A, γ=10°):

    gap_layers=2 → 0.4263 vs 0.5616  (−24.1 %)
    gap_layers=3 → 0.4459 vs 0.5613  (−20.6 %)
    gap_layers=4 → 0.4668 vs 0.5620  (−16.9 %)

The deficit shrinks MONOTONICALLY as the ring count rises → it is a numerical
RESOLUTION effect, not a flux leak.  The free adaptive mesh concentrates elements at the
tooth tips (where the gap field varies fastest); the structured mesh spreads them
UNIFORMLY, so it needs more radial layers to resolve the tooth-tip fringing to the same
accuracy.  The discretization is consistent (converges to the same answer).  Ruled out:
ε (torque flat vs ε: −20.9 %@8 µm, −19.2 %@15 µm), material composition (rotor iron area
177.0 vs 177.4, magnet area identical), moving-vs-merged coupling (−23 % even with both
merged).  No-load psi_A is −8 %; Arkkio torque ∝ B_r·B_φ ∝ flux² doubles that to ≈−16 %.

To close it further (optional, for torque-accuracy parity at low ring counts):
- preserve the tooth-tip taper: retract only the smooth stator OD, not the teeth (the
  `stator − disk(r_si+ε)` cut blunts the tips), or snap the tip arcs to the cell grid;
- or grade the cells (finer near the iron) instead of uniform — but that breaks the
  "exactly 2K UNIFORM rings" requirement, so it is a separate mode.

For the current goal (ANSYS-style uniform structured gap, behind an experimental toggle,
default off) the mesh is correct and the torque is convergent; users wanting torque
parity raise the Air-gap-layers slider.

## ROUTE A — CLEAN ARC BOUNDARY (2026-07-01): ε retract + filler REMOVED

The deferred next step ("snap the tip arcs to the cell grid", above) is now DONE.  The
ε-retract and the free-meshed bridge filler are both gone; the iron's gap-facing boundary
conforms DIRECTLY to the cells' transfinite arc.

### Mechanism
`_iron_arc_ring_occ` (fem_solver_2d.py): when a `structured_gap_spec` is present, the
gap-facing edge of EVERY domain that touches the half's gap ring is emitted in OCC as
**circle arcs coincident with the gap cells' arc**, instead of the fuzzy CadQuery polyline:
- ROTOR half: rotor OD (r_ro) — a clean full circle; also the rotor pocket air that
  reaches r_ro.
- STATOR half: stator bore (r_si) — tooth-tip arcs alternating with slot-mouth openings;
  also the slot-mouth air (out_band) that reaches r_si.

Each maximal run of on-ring polygon vertices (|r−r_ring|<30 µm) is replaced by a chain of
arcs whose endpoints are **snapped to the uniform seam grid** (angles 2π·k/(S·n_sectors)),
using a shared memoized point-adder so the arc endpoints reuse the SAME OCC point tags the
cells place at each seam.  `occ.fragment` then merges the coincident arc + seam points, so
every gap cell keeps exactly 4 corners → `setTransfiniteSurface` accepts it → EXACT uniform
rings.  Slot mouths stay OPEN: between two snapped tooth-tip arcs the boundary lifts
radially into the slot as LINES (no arc seals the mouth), so the mouth cell's outer arc is
a free gap↔slot interface (flux crosses).

So `eps = 0` in the spec: `_simplify_polys` skips the iron/air retract clips, and
`_build_structured_gap_cells` emits NO filler; `_stitch_full_half`'s ε-ring material
restore is inert (gated on eps>0).  `_SG_EPS_OVERRIDE` still forces the legacy retract path
for A/B debugging.

Why this works where the earlier attempt didn't: the STATOR slip ring at `mid` needs
uniform seams, and a cell shares its top/bottom seams, so the r_si arc also has uniform
seams — but the tooth/mouth *corners* need not be seams; SNAPPING each tooth-tip run's
endpoints to the nearest seam keeps every bore vertex on a seam, so no cell subdivides
(the snap perturbs tooth-edge angular position by ≤½ a seam, ≈2.5°, a tiny geometry
approximation — not a mesh failure).

### Verified (40 mm 12s/14p, full_ring production path, n_sectors=−1)
- **NO filler strips** at either boundary (rendered PNGs at gl=1/2/3).
- Rings EXACT: gl=1/2/3 → 2/4/6 uniform gap rings [12.1..12.3], spacing Gap/(2K).
  Both halves: 96/96 gap cells transfinite, **0 skipped, 0 filler**.
- Slip ring at mid uniform: 1008 nodes on the global 2πj/N grid (full disk) → sliding
  coupling `_ring()` untouched.
- Material classification clean: gap [12.1,12.3] = all air; iron right up to r_ro/r_si;
  slot mouths = air, tooth tips = iron (no iron slot-short, no 2ε gap widening).
- Free mode (structured_gap=False) BYTE-FOR-BYTE unchanged (T_avg=0.58163274 identical to
  pre-work c419618, every digit).

### TORQUE — deficit essentially GONE (the retract was blunting the tooth tips)
Mean torque vs free (full_ring, 40 mm 12s/14p, I=60 A, γ=10°):

    gap_layers=2 → 0.5581 vs 0.5778  (−3.4 %   was −24.1 %)
    gap_layers=3 → 0.5490 vs 0.5816  (−5.6 %   was −20.6 %)
    gap_layers=4 → 0.5719 vs 0.5781  (−1.1 %   was −16.9 %)

Removing the ε retract (which the OLD note above suspected of blunting the tooth tips,
`stator − disk(r_si+ε)`) collapsed the −17..−24 % deficit to ≈−1..−6 %.  So the deficit was
NOT mainly a uniform-resolution effect — it was the retract eating the tooth-tip taper +
the 2ε gap widening.  Losses match (~152 W both); structured ripple is lower (uniform
rings).

### Residual compromise
Tooth-tip / slot-mouth angular corners are snapped to the seam grid (≤½ seam ≈ 2.5° at
S=36/wedge).  For finer slot-opening fidelity, bias `_structured_gap_sm` toward smaller M
(more seams) — the rings stay exact; only the surface count rises.  The rotor side has no
approximation at all (its OD is a full circle already on every seam).

### Files
- `_iron_arc_ring_occ`, `_gap_edge_occ`, cell-builder `getP` sharing — fem_solver_2d.py.
- Proofs: `_routeA_arc_rotor_proof.py` (72/72 rotor), `_routeA_arc_stator_proof.py`
  (72/72 stator incl. mouths).  Verification: `_z_torque_cmp.py`, `_z_render_gap.py`,
  `_z_free_regress.py` (drive the full-disk path on a stable 40 mm config via `_use40.py`
  so tests never touch the user's live on-disk config).

---

# STRUCTURED SLOT INTERIOR — route A extended to the copper/enamel/liner (2026-07-02)

Same route-A idea (structured OCC cells + `occ.fragment` for automatic
conformity + `setTransfiniteSurface`), extended from the air gap to the STATOR
SLOT INTERIOR.  Solves the user's complaint: the thin insulation in the slot —
slot **liner** (coil↔iron, ~0.06 mm), wire **enamel** + inter-wire spacing — was
free-meshed into razor slivers (measured **408 tris <5° at 40 mm**, worst angle
1.42°).  Magnetically minor (µr=1) but it wrecks the THERMAL solve (the liner is
the copper→iron heat barrier) and violates "mesh = geometry".

Gated on a new `structured_slot` flag (default OFF); the free path is
**byte-for-byte unchanged** (free regression T_avg=0.58163274, every digit).

## Why the slot is EASIER than the gap
The slot interior is a **cartesian tensor grid** in the un-rotated frame (before
`cadquery_geometry`'s per-slot `_affine_rotate`): wires are axis-aligned
rectangles stacked in y; the enamel envelope is the copper grown `wire_spacing/2`
(so it also holds the inter-wire gaps + margins); the liner is an `ins_w` U-band
on the three iron-facing sides (OPEN at the gap side).  So there are NO arcs —
every cell is a straight-edged quad.  No ε retract, no arc-snapping (all that gap
machinery was to make a FUZZY circular iron boundary conform).

## Mechanism (fem_solver_2d.py)
1. **`_slot_grid_columns(geo_cfg)`** — recomputes the exact cartesian grid of one
   slot column, MIRRORING `cadquery_geometry._build_insulation_polys` /
   `get_2d_polygons` (same `right_x`, `top_y_c`, `n_fit` feasibility clamp).  Each
   cell is tagged copper / enamel (`DOM_WIRE_INS`) / liner (`DOM_SLOT_INS`) by its
   centre, plus the wire step for copper.
2. **`_build_structured_slot_cells`** — emits each cell as an OCC quad via a
   SHARED memoized point-adder (so neighbouring cells and both sides of every
   copper/enamel/liner interface reuse the SAME OCC points → conforming within
   the block).  Copper cells carry the per-coil current tag
   `DOM_COIL_BASE + (col*n_fit + step)`, matching `coil_polys` order, so
   `build_materials` injects the right (phase, direction) J into every wire.
   Built in the SAME model as the iron → `occ.fragment` makes the teeth/yoke
   conform automatically (no weld, no overlap buffer).
3. **`_slot_block_footprint`** — union of every column's liner-outer envelope,
   carved out of `out_band`/`air_gap` so the free air STOPS at the block (else
   `fragment` slices µm slivers where out_band's copper-hole boundary nearly
   coincides with a cell edge).
4. **Post-fragment transfinite pass** — set each slot cell transfinite.  Two
   things were essential to get ZERO slivers:
   - **Global element size `h`, not per-cell.**  Node count on every edge =
     `round(len/h)+1` for ONE global `h` (= the thinnest insulation feature,
     capped by `_SS_MAXDIV`).  A per-CELL aspect target made a fat copper cell and
     a thin liner cell DISAGREE on their SHARED edge (3 vs 18 nodes) → gmsh
     sheared one cell's grid into slivers (the "diagonal parallelogram" failure).
     One global `h` ⇒ a shared edge gets the same count from both sides
     (conformity) and opposite edges match automatically.  Feature-relative: a
     bigger motor's cells are longer → more sub-cells at the same quality, so the
     block quality is IDENTICAL at 40 mm and 450 mm.
   - **Explicit ordered corners** to `setTransfiniteSurface(surf,"Left",corners)`,
     found by walking the boundary curves (chain by shared endpoint).
5. **`DOM_WIRE_INS`/`DOM_SLOT_INS`** registered in `build_materials` (µr=1, inert)
   so the FE assembly gives them air-like stiffness instead of SKIPPING them (an
   unassembled tag = a hole in the matrix).
6. **Full-ring stitch** (`_stitch_full_half`): `_split_polys_for_sliding_band`
   now carries `wire_insulation`/`slot_insulation` to the stator half, and the
   stitch RECLASSIFIES the enamel/liner cells back to their material (else they
   default to DOM_OUTER, which the thermal solve drops → liner barrier lost).
   Gated on `structured_slot` so the free stitch is byte-identical.
7. **Thermal** (`routes/simulation.py`): when the mesh carries the insulation
   domains, the liner gets its true low `k` and the copper gets PURE winding `k`
   (the liner is a real meshed barrier now, not the lumped series `slot_k_eff` —
   else double-counted).  Falls back byte-identically to the lumped model when
   absent.

## Verified
- **Slot INSULATION block quality (40 mm AND 450 mm, full-ring production path,
  structured_gap OFF): min interior angle 16.7°, aspect ≤ 3.5, ZERO tris <15°**
  (was 408 tris <5°, worst 1.4°).  Identical at both scales (feature-relative).
  Isolated cell build (no fragment): min angle 18.6°.
- **EM sane** (40 mm full-disk, I=60 A, γ=12°, free vs structured slot):
  T_avg 0.5437 → 0.5591 Nm (+2.8 %), V_peak 7.83 → 8.09 V (+3.3 %), P_cu identical,
  torque ripple 6.0 % → **0.3 %** (uniform copper mesh removes per-wire mesh
  asymmetry).  Physics preserved (insulation inert; the small rise is better
  slot-current resolution).
- **Thermal sane**: steady solve on the structured mesh converges to finite
  T_max with the winding hotter than the iron (the meshed liner barrier working),
  resolving the copper→iron gradient directly instead of smearing it.
- **Free path byte-identical** with `structured_slot=False` (T_avg=0.58163274).

## Residual compromise
- The **slot-mouth air** (between the block's flat bottom `eb` and the curved
  bore) and the tooth-tip / far-field air are still FREE-meshed (a few slivers,
  as in the baseline) — they are AIR (µr=1; the mouth air is dropped/air in the
  thermal solve), NOT the insulation.  The hard criterion (clean insulation) is
  met.  Structuring the curved mouth would need the gap route-A arc machinery.
- `structured_slot` + `structured_gap` TOGETHER: the block picks up a few slivers
  from the gap cells' interaction near the bore (block clean when gap is off,
  which is the production default).  Both flags are experimental/off by default.

## Files
- `_slot_grid_columns`, `_slot_block_footprint`, `_build_structured_slot_cells`,
  the `structured_slot` branch in `build_mesh_from_polygons`, the transfinite
  pass, `DOM_WIRE_INS`/`DOM_SLOT_INS` — fem_solver_2d.py.  Thermal k —
  routes/simulation.py `get_thermal_field2d`.
- Verification: `_slot_quality.py` (full-ring block quality per scale),
  `_slot_build_test.py` (isolated sector build), `_slot_only.py` (bare cells),
  `_slot_em_cmp.py` (torque/back-EMF A/B), `_slot_thermal_cmp.py` (meshed liner),
  `_slot_proof.py` (before/after renders), all via `_use40.py`.
