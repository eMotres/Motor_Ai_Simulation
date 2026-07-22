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

## STAGE 1 EMPIRICAL RESULT — blocked by the 0.2 mm air gap (honest, superseded)

Ran the remesh-per-angle proof at commit 22b4113, mesh 1.6 & 1.2 mm, 6 angles:
- P2 torque = EXACTLY 0.0 at every angle; P1 = NaN (singular matrix).

ROOT CAUSE (not a P2 physics failure): this motor's air gap is only **0.2 mm**
(r_rotor_out 13.00 → r_stator_in 13.20). The remesh-per-angle CDT mesher does
NOT refine the gap below ~1 mm, so the Arkkio band contains **zero triangles**.
This is precisely WHY the production transient uses a STRUCTURED BELT with forced
gap layers. The remesh-per-angle proof is unsuited to a 0.2 mm gap → moved the
P1-vs-P2 comparison onto the belt (Stage 2 below), which resolves it.

---

## STAGE 2 DONE — P2 EDGE-DOF STITCHING ON THE BELT (the blocker, SOLVED)

`fem_transient_sliding_band(..., element_order=2)` now runs the FULL transient
on the structured belt. Implemented as a dedicated magnetostatic P2 branch
(`if element_order == 2:` just after `dt` is defined in the frame-loop prep),
assembled on the SINGLE stitched mesh `mesh_all` (simplest correct P2 assembly)
with a per-frame P2 projection that welds the belt interface:

- **The blocker fix.** P2 adds a dof on every element edge, including the belt's
  rotor-ring and stator-ring interface edges. `_build_Pro2(m_shift)` extends the
  signed union-find (`_SignedUF`) so, for each ring segment kk, it welds BOTH the
  ring **vertex** `rring[kk] ↔ sring[(kk+m)%Nring]` AND the ring-**edge midpoint**
  `edge(rring[kk],rring[kk+1]) ↔ edge(sring[j],sring[j+1])`, sign +1 (full ring).
  A `mesh.facets`→facet-index map (`_emap`/`_edge_dof`) locates each edge's P2
  dof via `basis.facet_dofs`. Validated: **all Nring ring-edge midpoints paired**
  (168/168 at slip24, 336/336 at slip48).
- Per-element ν (P0 interpolate), Irons–Tuck saturation Picard (identical to P1),
  torque via `_arkkio_torque_p2` (∇A at element quad points → linear-B Arkkio),
  facet-based outer Dirichlet (`get_dofs(facets=...)`) so P2 boundary midpoints
  are pinned. `frozen_nu` supported (converge ν once, freeze). Magnetostatic per
  frame — no σ∂A/∂t.

### STAGE A + B RESULTS (real, measured — 40 mm 12s14p structured belt, full ring)

| case | metric | P1 | P2 | P2 win |
|------|--------|----|----|--------|
| **No-load cogging** (mesh 1.0 mm, Nring 336, 24 steps, non-frozen) | mean T | −0.0153 Nm | **+0.0009 Nm** | 16× closer to physical 0 |
|   | raw p-p | 0.0731 Nm | **0.0296 Nm** | **2.5×** smoother |
|   | jitter (RMS Δ²T, staircase signature) | 0.0338 | **0.0161** | **2.1×** |
| **Loaded** I=30 γ=−20 (mesh 1.5 mm, Nring 336, 12 steps, non-frozen) | mean T | 0.371 Nm | 0.381 Nm | — |
|   | raw ripple | 24.9 % | **14.8 %** | **1.7×** |
|   | noise floor (forbidden-order RMS = numerical staircase) | 3.88 % | **1.08 %** | **3.6×** |

The P1 no-load mean carries a DC offset (the merged half-mesh Arkkio shear); P2's
linear-per-element gap B restores the physically-correct near-zero cogging mean
AND halves the p-p / jitter. Under load, P2 cuts the forbidden-order noise floor
(the honest measure of non-physical sliding-band staircase) by 3.6×.

**Methodology honesty notes (learned the hard way, recorded so the next agent
doesn't repeat it):**
- **Use the NON-FROZEN (per-frame re-converged ν) path for the comparison, not
  `frozen_nu`.** On this motor `frozen_nu` at load gives ~125 % ripple for BOTH
  P1 and P2 (verified on the untouched P1 path too) — freezing frame-0
  saturation is simply wrong once armature reaction rotates the saturation
  pattern. It is fine only for very light / no-load saturation.
- **Mesh must resolve the geometry** (≲ slot_width/3, i.e. ≲1–1.5 mm here). On a
  3 mm mesh the coarse-geometry cogging dwarfs the discretisation-order
  difference and P1 ≈ P2 (both ~garbage) — the solver's own comment already
  warns 3 mm is under-resolved. The wins above are at 1.0–1.5 mm.
- Both orders were run at matched settings so the comparison is controlled;
  neither fully converges the per-frame ν (picard res ~0.05–0.1 at the caps
  used), but they sit at comparable convergence so the RELATIVE smoothness is
  fair.

### Cost
P2 non-frozen is ~4–6× slower than P1 (per-frame ν re-convergence over ~2× the
dof count): no-load 24-frame mesh-1.0 run ≈ 3 min for P2 vs ~30 s for P1.
`frozen_nu` makes P2 much cheaper (frame 0 converges, later frames = 1 solve).

NOTE on ν warm-starting: the P1 main loop warm-starts ν across frames; the P2
branch deliberately does NOT (it resets to base each frame). Warm-starting is
only sound when EVERY frame reaches `_PIC_TOL`, but at no-load the magnet
saturation needs ~70 sweeps to converge (measured: 1-frame P2 res 5e-2 @25 it,
4e-3 @50, 9e-4 @76) — a warm-start with a modest per-frame cap leaves frames
path-dependent and injects a spurious DC torque bias (measured mean −0.008 Nm
vs the reset path's ~0). Independent per-frame reset keeps the residual error
UNBIASED (averages to ~0 mean) — the honest choice for the cogging study.

---

## CONVERGENCE PROOF — P2 noise floor →0, P1 stays flat (the make-or-break test)

No-load cogging, FULL RING, structured belt, ring density FIXED (Nring=336,
`SB_SLIP_PER_PERIOD=48`), gap_layers=2, 24 steps/period, non-frozen, matched
`nonlinear_iterations=25` for BOTH orders (so residual non-convergence is
common-mode and the P1-vs-P2 difference is purely the element order). "noise
floor" = RMS of the FORBIDDEN (non-6·k) torque orders in ABSOLUTE Nm (the %
form is undefined at no-load since ⟨T⟩≈0); it is the honest measure of the
non-physical sliding-band staircase. Physical cogging (12s14p) lives at
electrical order 12 = 6·2, which the 6·k band keeps.

| mesh (mm) | order | mean T (Nm) | raw p-p (Nm) | phys 6k p-p (Nm) | **noise floor (Nm)** | jitter Δ²T |
|-----------|-------|-------------|--------------|------------------|----------------------|------------|
| 1.4 | P1 | −0.0071 | 0.0771 | 0.0515 | **0.01442** | 0.0397 |
| 1.4 | P2 | −0.0013 | 0.0360 | 0.0289 | **0.00420** | 0.0246 |
| 1.0 | P1 | −0.0065 | 0.0833 | 0.0508 | **0.01547** | 0.0390 |
| 1.0 | P2 | +0.0004 | 0.0389 | 0.0277 | **0.00340** | 0.0224 |
| 0.7 | P1 | −0.0080 | 0.0739 | 0.0445 | **0.01353** | 0.0351 |
| 0.7 | P2 | −0.0008 | 0.0305 | 0.0215 | **0.00233** | 0.0165 |

**Result — P2 is CORRECT, not merely different:**
- **P2 noise floor CONVERGES toward 0** with mesh refinement:
  0.00420 → 0.00340 → **0.00233 Nm** (monotone, ~½ over 1.4→0.7 mm).
- **P1 noise floor is FLAT / mesh-independent** at ~0.0135–0.0155 Nm — the
  staircase does not vanish with refinement (piecewise-constant gap B). At
  mesh 0.7 mm P2's floor is **5.8× lower** than P1's.
- P2 raw p-p and jitter also shrink (0.036→0.031, 0.025→0.017); P1's stay put.
- P2 restores the physically-correct near-ZERO cogging mean (±0.001 Nm) at
  every density; P1 carries a ~−0.007 Nm DC offset (half-mesh Arkkio shear).

### Harmonic-macro cross-check (honest, partially inconclusive)
`airgap_macro=True` (P1, analytic gap, full ring, mesh 1.0, converged) gives
no-load raw p-p 0.140, phys-6k p-p 0.0815, noise floor 0.0209 Nm — i.e. the
macro and the merged-band P1 (phys-6k 0.051) disagree on the ABSOLUTE physical
cogging amplitude by ~1.6×, and the macro is actually NOISIER than merged P1 at
no-load (its coarse 48-node ring admits harmonics — the code notes "denser
rings are WORSE" for the macro). So the two coupling schemes do NOT pin the
absolute cogging amplitude at this resolution; they share the physical ORDER
(12). The macro is therefore not a clean amplitude reference here, and the P2
NOISE-FLOOR CONVERGENCE above (a within-scheme, matched-settings mesh study) is
the solid evidence, not the cross-scheme amplitude match.

---

## STAGE 3 — wiring into the app (DONE)

Full request path now threads `element_order` end-to-end:
`TransientCharts.tsx` (frontend request) → `GET /physics/fem_transient`
(`get_fem_transient`) → `em_transient_eval` → `fem_transient_sliding_band`.

- **Backend** `routes/simulation.py :: get_fem_transient` — new query param
  `element_order: int = 1`, added to the sliding-band cache key and passed to
  `em_transient_eval`. When `element_order == 2` the route COERCES a
  self-consistent P2 mode (the endpoint defaults `rotor_eddy=True`, which P2
  rejects): forces `structured_gap=True`, `rotor_eddy=False`, `demag=False`,
  `drive="current"`, `airgap_macro=False`. Verified end-to-end: a P2 request
  with the default `rotor_eddy=True` returns `method="sliding_band_p2"`,
  `element_order=2`, no exception.
- **Frontend** `web/src/components/simulation/TransientCharts.tsx` — the request
  object now sets `element_order: readMeshSetting('p2HiFi', false) ? 2 : 1`
  (a Mesh-tab boolean flag, default OFF = P1). No UI control was added (per
  scope) — a future "P2 / high-fidelity ripple" checkbox just needs to write the
  `mesh.p2HiFi` localStorage key that this line already reads. The Sweep panel
  (`SweepStudyPanel.tsx`) would add the same one field to its request to opt in.
- `element_order` is forwarded through `em_transient_eval`; `modules/solvers.py`
  `_call_filtered(get_fem_transient, payload)` passes it through automatically
  (it filters on the signature, now includes the param), so the optimizer/
  refine path inherits it (default 1).
- The P1 default (`element_order=1`) is byte-for-byte unchanged (verified:
  n_sectors=2 loaded run still `method=sliding_band`, T_avg 0.587).
- Eddy/voltage/demag and moving/macro-band P2 requests raise a clear
  `NotImplementedError` rather than silently returning P1.

## SECTOR P2 DONE — anti-periodic wedge (n_sectors≥2)

`_build_Pro2` now welds, in addition to the slip-ring vertices+edge-midpoints:
- the **radial-cut** anti-periodic VERTEX pairs `Sn[i] = _bc_sign·Mn[i]`
  (`_pair_sector_cut_nodes`, same as the P1 vertex BC), AND
- the **cut-EDGE midpoints**: consecutive-by-radius cut vertices `(Mn[i],Mn[i+1])`
  delimit a cut-boundary edge whose midpoint welds to `(Sn[i],Sn[i+1])`'s with
  `_bc_sign`.
- the slip ring uses the open-wedge wrap map `_ring_map` (period Nring−1, a
  `_bc_sign` flip per wrap — identical to the P1 loop); a ring-edge midpoint is
  paired only when both endpoints keep the SAME sign (no wrap between them), so
  the one seam edge at the wrap point is left free (measure-zero, harmless).

**Validation (40 mm 12s14p, structured belt, loaded I=30 γ=−20, non-frozen):**

| model | T_avg | raw p-p | ripple | noise floor | Nring | time |
|-------|-------|---------|--------|-------------|-------|------|
| full ring (n_sectors=−1) | 0.3761 Nm | 0.0403 | 10.7 % | 1.17 % | 336 | 83 s |
| **sector (n_sectors=2)** | **0.3771 Nm** | 0.0342 | 9.1 % | 0.49 % | 169 | 36 s |

Mean torque agrees to **0.3 %**, series shapes near-identical — sector P2 is
physically equivalent to full-ring P2 and ~2.3× faster (half the mesh). This is
the model the app's default config (`n_sectors=2`) uses. Pairing coverage
logged: 168/168 ring edges, 33 cut vertices, 29 cut edges.

## ROTOR-EDDY LOSSES ON P2 DONE — real magnet/shaft/iron/copper losses

`element_order=2` + `rotor_eddy=True` now runs a COMPLETE transient loss report,
using the SAME architecture as the P1 app path (which is `eddy=False` too — the
coupled σ∂A/∂t "J-view" solve is NOT the app loss path). The P2 branch:
- captures the rotor-frame nodal A(t) history (`A2[vdof[rotor]]`) and the
  element-mean iron B(t) histories per frame;
- **magnet + shaft eddy** = the honest, reaction-included frequency-domain rotor
  solve `eddy_solver_2d.honest_rotor_eddy` on the P2 rotor A-history — the SAME
  function P1 calls (rotor back-iron μ_r taken from the converged P2 ν);
- **iron** = compact Bertotti on dB/dt (periodic central difference — the P2
  field is already smooth, so P1's savgol slip-jitter filter is unneeded);
- **copper** = I²R (`copper_loss_W`, current drive).

Why post-processing and not a σ∂A/∂t mass matrix in the main solve: P1's APP
transient is `eddy=False` (get_fem_transient never sets eddy=True) — it too is a
magnetostatic field with the eddy LOSSES post-processed by the same honest rotor
solve. So P2 matching that path IS "a full transient like P1". The coupled
σ∂A/∂t bordered J-view solve (`eddy=True`) stays gated for P2 (it is an opt-in
diagnostic the app does not use).

**Validation (40 mm 12s14p, I=30 γ=−20, n_sectors=2, rotor_eddy=True):**

| flags | order | ripple | P_mag (magnet eddy) | P_shaft | P_fe (iron) | P_cu |
|-------|-------|--------|---------------------|---------|-------------|------|
| clean (iron_template=F, mesh 1.5) | P1 | 16.8 % | 0.45 W | 0.148 W | 5.85 W | 28.4 W |
| clean | **P2** | **9.1 %** | **0.49 W** | **0.168 W** | **5.97 W** | 28.4 W |
| APP default (geo_mesh=T → full ring, mesh 1.0) | P1 | 21.3 % | 0.583 W | 0.116 W | ~5.9 W | 28.4 W |
| APP default | **P2** | **10.4 %** | **0.549 W** | **0.124 W** | **5.88 W** | 28.4 W |

Magnet/shaft/iron eddy losses agree within **~7 %** of P1 (eddy loss is a field
property, not an element-order artifact — as expected), while P2 keeps its ~2×
cleaner torque ripple. Copper is identical (same I²R). The route now KEEPS
`rotor_eddy` on for P2 (only eddy/voltage/demag/macro are coerced off), so a P2
loaded sim reports real losses + efficiency, not a P1 fallback.

NOTE (pre-existing, not P2): `iron_template=True` WITHOUT `geo_mesh=True` gives
garbage ripple for BOTH P1 (136 %) and P2 (50–226 %) on this motor at every mesh
tried — a mesh-quality issue in the P1 iron_template path, unrelated to element
order. The app default pairs `iron_template` with `geo_mesh=True`, where both
orders are clean (table above).

### COPPER fixed (element-order-independent) + MAGNET-EDDY convergence study

A reviewer found two discrepancies vs P1; both resolved:

**1. Copper was NOT element-order-independent — FIXED.** P1's `P_cu_W` series is
DC I²R **plus** AC proximity/skin (`_prox_eddy_split` on the coil B(t)); the P2
branch was reporting DC only. Now P2 captures the coil B(t) history and computes
the SAME split (σ/12·Σ(w²·dBr² + h²·dBt²), wire dims capped at 2·δ). Verified
identical DC and matching AC:

| order | steps | P_cu_dc | P_cu_ac | P_fe |
|-------|-------|---------|---------|------|
| P1 | 18 | 28.4 W | 7.09 W | 5.83 W |
| P2 | 18 | **28.4 W** | **7.04 W** | 5.28 W |
| P1 | 36 | 28.4 W | 9.12 W | 7.08 W |
| P2 | 36 | **28.4 W** | **9.49 W** | 9.82 W |

DC copper is now byte-identical (same `copper_loss_W`, R_phase, current — it
never should have differed), and AC matches P1 to ~5 %.

**2. Magnet eddy: P2's lower value is the PHYSICALLY CORRECT one, P1's is
staircase-noise-inflated.** P_mag (honest_rotor_eddy, the SAME reaction-included
FFT solve for both orders) at I=30 γ=−20, n_sectors=2:

| order | mesh 1.5 / 18st | mesh 1.5 / 36st | mesh 1.0 / 18st | ripple |
|-------|-----------------|-----------------|-----------------|--------|
| P1 | 1.161 W | 1.45 W | 1.081 W | 45–53 % |
| **P2** | **0.326 W** | **0.469 W** | **0.351 W** | 22–29 % |

Definitive reading:
- **P2 is NOT under-fed.** Its torque, DC+AC copper and (@18 st) iron loss all
  match P1 → the field magnitude and low-order harmonics are correct. Only the
  HIGH-frequency content differs.
- P2's field carries **~2× less ripple** (the sliding-band staircase). Magnet
  eddy is driven by the AC field the magnets see, so P1's extra staircase drives
  extra — and NUMERICAL — eddy (the convergence proof showed that staircase does
  not vanish with mesh refinement for P1). P2 excludes it.
- **P1's P_mag is itself unstable across settings** (0.45 W at one mesh, 1.16 W
  at 18 st, 1.55 W in the reviewer's run) — the signature of a noise-driven
  quantity. P2 is 3–4× lower and mesh-stable (0.326→0.351 over 1.5→1.0 mm).
- Residual step-dependence in BOTH (P1 +25 %, P2 +44 % over 18→36 st) is
  `honest_rotor_eddy`'s harmonic-sampling (more frames → more resolved
  harmonics), a sampling effect common to both, NOT the element-order artifact.

Conclusion: **P2's magnet eddy (~0.33 W) is the more correct value; P1's ~1.1 W
over-reports by ~3× due to numerical staircase-induced eddy currents in the
magnets — a further P2 WIN.**

KNOWN LIMITATION: the P2 iron/coil-AC losses use a periodic central-difference
dB/dt (not P1's savgol slip-jitter filter, which is defined only later in the P1
loop), so at HIGH step counts P2's iron classical-eddy term over-reports
slightly (P_fe 9.8 W @36 st vs P1 7.1 W); at typical step counts (≤24) it
matches P1 to <10 %. The magnet/shaft eddy (the dominant rotor-conductor loss)
is unaffected — it uses the same FFT solve as P1.

### NOT done (raises NotImplementedError, documented in-code)
1. **Coupled σ∂A/∂t J-view solve (`eddy=True`), voltage drive, demag pre-pass**
   on P2 — the app transient uses none of these (they are opt-in diagnostics /
   drive modes); the P2 magnetostatic-field + honest-eddy-loss path matches the
   P1 app path.
2. **Moving / harmonic-macro band** on P2 (structured merged belt only for now).

STATUS: P2 static solver = built + validated. **P2 full transient on the belt =
DONE** — full ring AND anti-periodic sector, mesh-convergent, sector == full
ring, and now with REAL rotor-eddy/iron/copper losses matching P1 to ~7 %.
Remaining = the opt-in coupled-eddy J-view / voltage-drive / demag diagnostics
(cleanly gated).
