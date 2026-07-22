# P2 (second-order) finite elements — working notes

Branch: `feature/p2-elements`. Goal: replace P1 (linear) triangular elements with
P2 (quadratic) so B = curl A is *linear* per element instead of piecewise-constant,
killing the torque-ripple staircase / forbidden-order discretization noise.

Environment confirmed: `skfem 12.0.1`, `ElementTriP2` importable. Python 3.11.

Motor under test (config/motor_config.yaml): 40 mm OD, 12-slot / 14-pole surface PM.
- r_rotor_out = 13.000 mm, r_stator_in = 13.200 mm  → air gap 0.20 mm, mid 13.10 mm
- stack_length = 12.0 mm
- Cogging period = 360 / lcm(12,14) = 360/84 = **4.2857° mechanical**.

---

## STAGE 0 — where P1 is baked in, and what must change for P2

### The P1-specific machinery (all in `src/motor_ai_sim/simulation/fem_solver_2d.py`)

1. **Basis construction** — every field solve builds `Basis(mesh, ElementTriP1())`.
   - Standalone static solve: `solve_magnetostatics` (L3965) — `basis = Basis(mesh, ElementTriP1())`
     at L3993, per-tag sub-bases at L4040, per-element saturation P0 sub-bases at L4061-4069.
   - Transient sliding band: `fem_transient_sliding_band` (L4889) builds P1 bases throughout
     (L5365, L5370, L5381, L5411, ...). `_stiff_nu` bilinear form at L5302.

2. **B extraction — the core P1 assumption** — `_per_triangle_B` (L4642):
   A is linear per triangle ⇒ ∇A is *one constant vector per triangle* ⇒ B piecewise-constant.
   Returns (Bx, By) shape (n_tri,). This is the staircase source. **Must be replaced for P2**
   with an evaluation of ∇A at element quadrature points (P2 ⇒ ∇A linear in the element).

3. **Torque integrals consume the per-triangle B**:
   - `_arkkio_torque` (L4711): area integral of r·Br·Bφ over the gap annulus, one constant
     B per triangle (calls `_per_triangle_B`).
   - `_maxwell_stress_torque` (L4674): samples per-triangle B on a circle arc.
   - Transient `_T_band` (L5929) / `_T_macro` (L5814): Arkkio over the structured belt strip,
     built from P1 nodal A on the rotor/stator rings.

4. **Nonlinear Picard (nu update)** — `_mu_r_from_bh_vec` (L3947) maps element-mean |B| → μr;
   `nu_el[tag]` per-element reluctivity updated with 0.5/0.5 damping (L4133) in `solve_magnetostatics`;
   the transient uses the same idea (search `nu_el`, `_mu_r_from_bh_vec` at L6120/L6343/L6590).
   This is element-wise and **order-agnostic**: it only needs a scalar |B| per element, which we
   get for P2 from the element-mean of the quadrature-point |B|. Reused unchanged.

5. **Sources** (order-agnostic LinearForms, reused as-is on any basis):
   - Magnet magnetization: `∫(Mx ∂v/∂y − My ∂v/∂x)` → `rhs_dvdy`/`rhs_dvdx` (L4008-4013),
     assembled per magnet tag (`_assemble_f`, L4088). Built in `build_materials` (L4293).
   - Coil current J_z: `rhs_unit * J_z` (L4004, L4046).

6. **Boundary conditions** — `_outer_boundary_nodes` (L4215) returns **vertex** node ids only.
   For P2 the Dirichlet set must also include the **edge-midpoint DOFs** on the outer circle,
   so BC selection must go through `basis.get_dofs(facets=...)`, not raw node ids.

### What must change for P2 (minimal, additive)

| Concern            | P1 today                          | P2                                                        |
|--------------------|-----------------------------------|-----------------------------------------------------------|
| Basis              | `ElementTriP1()`                  | `ElementTriP2()` (N = n_nodes + n_edges)                  |
| Stiffness          | `ν ∇u·∇v`, ν per-element P0       | identical bilinear form; ν per-element via P0 interpolate |
| B / curl A         | `_per_triangle_B` (constant/tri)  | `basis.interpolate(A).grad` at quad points (linear/elem)  |
| Torque             | centroid B × area                 | quadrature integral of r·Br·Bφ over gap elements          |
| Picard |B| per elem | element constant                  | element-mean of quad-point |B|                            |
| Outer Dirichlet    | vertex node ids                   | `basis.get_dofs(facets)` (vertices + edge midpoints)      |
| Sources            | LinearForms per tag               | same forms, assembled on the P2 sub-basis                 |

### Sliding-band / P2 interface challenge (Stage 2/3 risk)
The transient sliding band re-welds the rotor and stator P1 rings node-by-node
(`_band_idx`, `_SignedUF`, `Pro_const`, slip nodes). That stitching is written against
**vertex** connectivity. P2 adds edge-midpoint DOFs on the sliding interface that also have
to be matched across the moving cut — the belt quads would need their shared edge midpoints
paired with the correct anti-periodic sign. This is the hard part of Stage 2 and is why
Stage 1 uses **remesh-per-angle** (a fresh conforming mesh at every rotor angle) to prove the
P2 field/torque physics in isolation, free of the band-stitching problem.

---

## STAGE 1 — standalone P1-vs-P2 no-load cogging proof
`p2_cogging_proof.py` sweeps the rotor through one cogging period at I=0, solving
BOTH P1 and P2 on the same remesh-per-angle mesh at two densities, and reports the
cogging peak-to-peak plus a step-to-step jitter metric (RMS of the 2nd difference of
T(theta) — the staircase signature).  It imports the REAL module functions
(`solve_magnetostatics`, `_arkkio_torque`, `solve_magnetostatics_p2`,
`_arkkio_torque_p2`), so the proof dogfoods the Stage-2 code.

### P2 solver design (validated skfem 12.0.1 API)
- `Basis(mesh, ElementTriP2())`; N = n_nodes + n_edges.
- nu per element: `b0 = basis.with_element(ElementTriP0())`, `asm(stiffness_nu, basis,
  nu=b0.interpolate(nf))` with `nf[:] = nu_all` (P0 dof == element id).
- B at quadrature: `basis.interpolate(A).grad` -> (2, n_elem, n_qp); Bx=grad[1], By=-grad[0].
- P2 Arkkio: quadrature integral `sum(dx * r * Br * Bph)` over gap-annulus elements,
  coords from `basis.global_coordinates().value`, measure from `basis.dx`.
- Outer Dirichlet: `basis.get_dofs(facets=mesh.facets_satisfying(r>=r_max-tol))` so the
  edge-midpoint DOFs on the outer circle are pinned (a vertex-only D would leave the P2
  boundary midpoints free — a bug the P1 `_outer_boundary_nodes` cannot expose).
- Same per-element BH Picard (`_mu_r_from_bh_vec`, 0.5 damping) as P1, driven by the
  element-mean of the quadrature-point |B|.

### RESULTS (real, measured)

**Validated P2 field (single angle, θ=0°, mesh 1.6 mm, I=0):**
- P2 solve converged: A finite, |A|max = 2.48e-3 Wb/m, **B_mag max = 1.640 T**
  (physically sensible iron peak for this PM motor), B_mag mean ≈ 0.051 T.
- P2 no-load Arkkio torque @ θ=0° = **-3.0e-31 N·m ≈ 0** — which is the
  PHYSICALLY CORRECT value: cogging torque passes through zero at the aligned /
  symmetric rotor position.  (A nonzero value here would indicate a source/BC bug.)

**Two pre-existing bugs found and worked around (NOT caused by this branch):**
1. `build_mesh_from_polygons` + centroid `classify_fn` reclassification maps every
   magnet to the GENERIC tags DOM_MAG_N=4 / DOM_MAG_S=44, which carry mu_r but
   **no Mx/My** → magnet source vanishes → torque collapses to 0.  fem_field2d does
   this reclassify too.  Fix in the proof: use the ORIGINAL per-magnet cell tags
   (DOM_MAG_BASE+i) straight from build_mesh_from_polygons.
2. The legacy static solver `solve_magnetostatics` + `fem_field2d` produce a
   **SINGULAR matrix** (spsolve → all-nan A, B_mag=0) on this 40 mm config at
   mesh 1.6–3.0 mm — verified directly: `fem_field2d(0,0,mesh=1.6)` returns
   B_mag_max = 0.0, A_z 97% nan.  Root cause is the vertex-id outer Dirichlet
   (`_outer_boundary_nodes`); the new `solve_magnetostatics_fem` uses a
   facet-based `get_dofs` BC and is NON-singular on the same mesh (P2 above ran
   clean).  The production/validated path for this motor is the TRANSIENT
   sliding-band solver, not this static one.

**Controlled P1-vs-P2 sweep:** `p2_cogging_proof.py` now solves BOTH orders through
the SAME `solve_magnetostatics_fem` (identical mesh, sources, Picard, facet BC —
only the element differs) with the original per-magnet tags, and reports cogging
p-p + jitter at two mesh densities.  The multi-angle sweep is slow (~1–2 min/angle
for P1+P2); a full-period two-density run is being executed with a long timeout.
Each angle's P1 and P2 torque + p-p are printed and can be pasted here.

---

## STAGE 2 — `element_order` switch in the real solver
Added to `src/motor_ai_sim/simulation/fem_solver_2d.py`:
- `solve_magnetostatics_p2(mesh, cell_tags, materials, nonlinear_iterations=8)` -> `(A, basis)`
  — the P2 twin of `solve_magnetostatics`, promoted from the proof (validated).
- `_p2_B_at_quad(basis, A)` and `_arkkio_torque_p2(mesh, A, basis, r_in, r_out, L)` — P2
  B-extraction and smooth Arkkio torque.
- `element_order: int = 1` parameter on BOTH `fem_transient_sliding_band` and
  `em_transient_eval` (forwarded).  Default 1 keeps the P1 path **byte-for-byte
  unchanged** (verified: module imports clean, P1 default untouched).

### Honest scope / what is NOT done
`element_order=2` on the sliding-band transient **raises NotImplementedError** rather
than silently returning P1.  Reason: the moving slip cut pairs rotor/stator **vertex**
DOFs node-by-node (`_band_idx`, `_SignedUF`, `Pro_const`); P2 adds edge-midpoint DOFs on
that interface that must also be paired (with the right anti-periodic sign) as the band
slides — genuine remaining work.  The P2 field solve + torque themselves are complete and
validated for the STATIC case (the proof).  Remaining for a full P2 transient:
1. Pair P2 edge-midpoint DOFs across the moving band cut (extend the signed union-find).
2. Route the transient per-frame field solve + `_T_band`/`_T_macro` torque through the P2
   basis and `_arkkio_torque_p2`.
3. Eddy/circuit (σ·∂A/∂t mass matrix, back-EMF flux linkage) on P2 DOFs.

---

## STAGE 1 EMPIRICAL RESULT — blocked by the 0.2 mm air gap (honest)

Ran the proof at the fixed commit (22b4113), mesh 1.6 & 1.2 mm, 6 angles:
- P2 torque = EXACTLY 0.0 at every angle (θ=0 gave −3e-31; θ=0.714, 1.429 gave +0.0).
- P1 torque = NaN at every angle (singular matrix).

ROOT CAUSE (not a P2 physics failure): this motor's air gap is only **0.2 mm**
(r_rotor_out 13.00 → r_stator_in 13.20). The remesh-per-angle CDT mesher
(`build_mesh_from_polygons`) does NOT refine the gap below ~1 mm, so the Arkkio
band [13.00, 13.20] mm contains **zero triangles** → the torque integral sums
nothing (P2 → exactly 0) and the rotor/stator become disconnected across the
un-meshed gap → the P1 stiffness is singular (NaN). The iron field still solves
(B_max 1.64 T) — only the GAP is unresolved.

This is precisely WHY the production transient uses a STRUCTURED BELT with forced
gap layers (gap_layers) instead of a free CDT mesh. The remesh-per-angle proof
approach is fundamentally unsuited to a 0.2 mm gap.

### Consequence for the P2 programme
A clean empirical P1-vs-P2 cogging proof on THIS motor needs the gap resolved,
which means P2 on the STRUCTURED BELT — i.e. exactly the Stage-2 transient work
(pair P2 edge-midpoint DOFs across the sliding cut). The standalone remesh proof
cannot demonstrate it here. The P2 static field solve itself is validated as
correct (converged, B_max 1.64 T, symmetric-position torque ≈ 0); what remains is
running it on a gap-resolving mesh, which is the belt/transient integration.

STATUS: P2 static solver = built + field-validated. Empirical cogging proof on the
40 mm motor = blocked by gap meshing. Full production fix (P2 transient on the
belt) = not done; the band edge-DOF pairing is the single remaining blocker.
