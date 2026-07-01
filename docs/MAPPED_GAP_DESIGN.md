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
