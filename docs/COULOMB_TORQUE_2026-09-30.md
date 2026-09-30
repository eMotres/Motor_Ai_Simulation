# Coulomb virtual-work torque — 2026-09-30

Branch `feat/coulomb-virtual-work-torque` (from `pre-migration-freeze-2026-09-15`).
Module `src/motor_ai_sim/simulation/virtual_work_torque.py`, tests
`tests/test_virtual_work_torque.py`. Why: Astra review P01/P02/P05. The shipped loaded
waveform is an energy / terminal-work mean plus the raw Maxwell (Arkkio) AC, and nothing
independent checks that AC. The owner asked for Coulomb's method directly.

## 1. Method

The torque on the rotor is T = dW'/dθ at constant current. The discrete A-formulation
solution minimises F(A, θ) = W(A, θ) − fᵀA, and min_A F = −W'. By the envelope theorem:

    T = dW'/dθ = −∂F/∂θ |A fixed

So one solve is enough: move the nodes of a layer of elements virtually, keep the nodal
potentials fixed, and differentiate the element integrals (J.L. Coulomb, IEEE Trans.
Magn. 19(6), 1983).

**Why the layer must be pure air.** Every element outside the layer either stays still
(stator side) or moves rigidly with the rotor. A rigid rotation leaves the element's
energy unchanged. It also leaves its magnet and current source terms unchanged, because
M and J turn with the element. So only the deformed elements contribute. If they are air
(ν = ν0, no J, no M, σ = 0), fᵀA does not depend on θ and the energy density is exactly
ν0|∇A|²/2. The formula therefore needs no ∂ν/∂B term, even when the iron is saturated.

**Virtual displacement.** Per mechanical radian the displacement is
u(x) = φ(x)·(−y, x). Here φ = 1 on the rotor side of the layer and 0 on its stator side.
φ is interpolated with the vertex (P1) shape functions and is linear in r in between.

**Jacobian derivative.** Let J = ∂x/∂ξ. The solver's meshes are straight-sided `MeshTri`,
so the geometry map is affine for both the P1 and the P2 field element. A P2 mid-side
node stays at its edge midpoint: it moves by the average of its two vertices, so by half
when only one vertex moves. With D = ∇u = Σ_k u_k ⊗ ∇N_k (constant per element):

    ∂J/∂s = D J,   ∂(∇A)/∂s = −Dᵀ∇A,   ∂|J|/∂s = |J| tr D

For one air element with stack length L:

    T_e = L ν0 ∫_e [ ∇A·(D∇A) − ½|∇A|² tr D ] dΩ
        = L ν0 ∫_e [ a(A_x² − A_y²) + 2b A_x A_y ] dΩ,
    a = (D_xx − D_yy)/2,   b = (D_xy + D_yx)/2

Only the symmetric, trace-free part of D survives. A rigid rotation has a = b = 0 and
contributes exactly zero. ∇A comes from the field's own basis: linear per element for P2,
constant for P1. The integral uses that basis' quadrature, which is exact for both
integrands. Sign: positive means counter-clockwise torque on the rotor, which is the
Arkkio evaluator's convention.

**Relation to Arkkio.** In polar form with φ = φ(r), the integrand is r·B_r·B_θ·(−φ')/μ0.
With a linear φ(r), the continuum Coulomb integral IS the Arkkio integral. The discrete
difference is that Coulomb uses the P1 interpolant of φ on the actual mesh. It is
therefore the exact derivative of the discrete co-energy along one mesh deformation.
Arkkio instead weights by the nominal radii and picks elements by centroid.

**Layers on the sliding band.** `sliding_band_layers` builds two disjoint layers on the
stitched two-half mesh:

* **rotor side:** rotor-half gap air, from the outermost rotating metal node (iron,
  magnet or sleeve) to the rotor-half copy of the slip circle;
* **stator side:** stator-half gap air, from the stator-half copy of the slip circle to
  the innermost stator-iron node.

The slip circle bounds both layers and is never inside one. φ is pinned exactly on the
two copies of the slip ring, so both copies of every welded node always move together.
Every element the field deforms is checked against an air mask: an air tag
(DOM_AIR / AIRGAP / BAND / OUTER), base ν exactly ν0, and not saturable. Any other
element in the layer raises `CoulombLayerError`. The rotor half lives in the rotor frame,
and the layers use radii only, so the rotor angle does not enter.

T is linear in φ. The mean of the two layer torques is therefore the Coulomb torque of
φ̄ = (φ_r + φ_s)/2, which spans the whole gap (1 → ½ at the slip circle → 0), and that
mean is the reported `coulomb_Nm`. The difference between the two layers is the built-in
self-check. It is zero in the continuum, so on a mesh it measures discretisation error.

**Scope.** The formula uses only the solved A of the frame. Eddy currents, irreversible
demag, voltage/PWM drive and imposed current need no special case: the layer carries no
conductor, and the stress in the gap air is the force on everything inside it. A sector
model is multiplied by the same `NS` the Maxwell path uses. A layer node on a radial cut
moves tangentially by the same rotation as its anti-periodic partner, so the energy stays
1/NS of the full ring's.

## 2. Integration

* `frame_torques(evaluator, A)` is the shared per-frame post-processing: a pure function
  of the prepared evaluator (mesh, element, layers) and the frame's A. It returns
  `maxwell_Nm` (bit-identical to the old `_torque2(A) * NS`), `coulomb_rotor_side_Nm`,
  `coulomb_stator_side_Nm`, `coulomb_Nm` (their mean) and `coulomb_layer_diff_Nm`. The
  evaluator is built once per mesh by `prepare_sliding_band_frame_torques`. **A
  time-periodic (TDM) frame producer calls the same two functions.**
* The P2 frame loop calls `frame_torques` where it used to call `_torque2`. The two new
  per-frame series (`_Tc2`, `_Tc2l`) are in `_v2_lists`, so they are trimmed with the
  settling prefix.
* New result keys, at full precision: `T_coulomb_series`, `T_avg_coulomb_Nm`,
  `T_ripple_pp_coulomb`, and `coulomb_torque`. `coulomb_torque` holds the two layer
  series, `layer_self_check` (max |T_r − T_s|, mean difference, relative to the mean and
  to the p-p), the Coulomb harmonics, the layer sizes, the requested method, and
  `unavailable_reason`. `torque_method_diagnostics` gains `coulomb_mean_Nm` and the
  Maxwell/space-vector/terminal-work minus Coulomb differences. The raw series
  `torque_coulomb_Nm` is in `P2_transient_sample_history`.
* Option `torque_method`: a keyword of `fem_transient_sliding_band` and
  `em_transient_eval`; if not given, `simulation.torque_method` from the config is used.
  `"hybrid_maxwell_ac"` is the default and keeps the current behaviour bit for bit.
  `"coulomb"` makes the reported `T_em_Nm`, `T_avg_Nm`, the ripple and `T_harm_*` come
  from Coulomb, with `torque_method = T_mean_method = "coulomb_virtual_work"`. If the
  layers cannot be built, the run logs a warning, keeps the hybrid, and records why.

## 3. Validation

All real-machine runs were done in a server sandbox: throwaway `motres-api:test`
containers, nice 19, ionice idle, 4 threads, solver-direct through `em_transient_eval`.
Inputs were read-only copies of workspace `c309c100cd421858`. The machines:

* Ø40: `CIANO14 40 new / L12` rated, 42.78 A, γ 10°, 13 000 rpm, sector 2;
* L13: `CIANO28 85 20SW1200 / L13` rated, 25.88 A, γ 2°, 1 000 rpm, sector 4;
* L155: `CIANO10 200 opt / L155 motor` rated 1x9 mm, 562.1 A line, Δ, γ 15°, sector 2,
  with a sleeve.

Each ran with its duty's own mesh settings. "gl" means gap layers and "ring" means
slip-ring nodes per electrical period. Unless a row says otherwise, runs were
magnetostatic with imposed current, eddy off and demag off, sampled at every ring node
over one electrical period. The harness scripts are not in the repo; the
finite-difference one is adapted, with the method unchanged, from the paused study's
`scripts/ripple_validation/ripple_ref.py` (branch `study/torque-ripple-validation`).
Every number is at full precision in the run JSONs.

### 3.1 Analytic and unit cases (`tests/test_virtual_work_torque.py`, workstation, < 5 s)

| check | P1 | P2 |
|---|---|---|
| Coulomb vs the central FD of the discrete co-energy, along the SAME mesh-deformation family (slotted iron stator, salient iron rotor, currents both sides) | 5.4e-9 rel | −5.9e-9 rel |
| energy conservation ∫T dθ vs ΔW′ over 0.08 rad (Simpson, 17 points, same family) | 1.3e-7 rel | 9.9e-8 rel |
| analytic: two sinusoidal current annuli in air (p = 2, A = 0 on both circles), closed-form mutual co-energy derivative; mesh ×1 / ×2 / ×4 | −2.4e-2 / −6.2e-3 / −1.6e-3 | −5.8e-3 / −1.4e-3 / −3.6e-4 |
| layer independence, same analytic case, ×1 / ×2 / ×4 | 2.5e-3 / 6.4e-5 / 1.4e-5 | 1.1e-5 / 6.4e-7 / 2.1e-8 |
| layer independence, slotted/salient machine, ×1 / ×2 (P2) | | 2.5e-3 / 3.6e-5 |

The first two rows show that the formula is the exact Jacobian derivative, down to the
finite-difference noise. The analytic error falls as O(h²), which is the polygonal
approximation of the circles.

### 3.2 Against a finite-difference virtual-work reference at identical rotor states

The reference is frozen-current co-energy virtual work. The rotor is moved ±1 and ±2
slip nodes at the centre frame's current vector, and T = the 4th-order Richardson of
dW′/dθ, where W′ = −N_s·L·Φ(A) is the functional the pointwise Newton minimises
(the solver's own B-H law). The angles are 13 over one full electrical period (8 on
L155), with distinct cogging phases. The table gives differences to that reference in
N·m: the mean offset, the max |Δ| per sample, and the RMS of Δ after its mean is
removed (the AC error).

| case | FD mean | FD p-p (13 pts) | Coulomb Δmean / max / AC-rms | raw Maxwell Δmean / max / AC-rms | L2-mortar diag Δmean / max / AC-rms |
|---|---|---|---|---|---|
| Ø40 rated, gl1 ring 144 (shipped) | 0.625201 | 0.040337 | −5.8e-4 / 2.6e-3 / 1.3e-3 | −2.5e-3 / 4.6e-3 / 1.3e-3 | +2.6e-8 / 4.8e-5 / 2.6e-5 |
| Ø40 rated, gl1 ring 432 | 0.625331 | 0.040392 | −9.2e-4 / 3.8e-3 / 1.7e-3 | −3.3e-3 / 6.2e-3 / 1.8e-3 | +6.3e-6 / 1.4e-4 / 3.9e-5 |
| Ø40 rated, gl3 ring 288 | 0.625103 | 0.041278 | −1.1e-4 / 5.6e-4 / 2.8e-4 | −2.2e-3 / 2.6e-3 / 2.9e-4 | +1.1e-6 / 9.8e-6 / 5.2e-6 |
| Ø40 no-load (cogging), gl1 ring 144 | −0.000070 | 0.068726 | −2.4e-4 / 3.2e-3 / 1.5e-3 | +2.1e-4 / 3.6e-3 / 1.5e-3 | −4.7e-7 / 5.5e-5 / 2.5e-5 |
| L13 rated, gl1 ring 120 | 4.967367 | 0.260020 | −1.4e-3 / 2.0e-3 / 3.2e-4 | −4.1e-2 / 4.3e-2 / 1.1e-3 | −9.1e-5 / 4.2e-4 / 1.7e-4 |
| L155 rated, gl1 ring 216 | 186.478717 | 2.464345 | −1.8e-2 / 2.5e-2 / 3.6e-3 | −2.9e-1 / 3.0e-1 / 2.1e-3 | +2.6e-4 / 3.1e-3 / 1.7e-3 |

On L13 and L155, Coulomb matches the reference to ≤ 0.15 % of the p-p (AC) and
≤ 0.03 % of the mean. The raw Maxwell mean is 0.83 % low on L13. On Ø40, at the shipped
gl1 mesh, Coulomb and Maxwell carry the same AC error against the reference, about
3 % of the p-p RMS. At gl3 that error drops five-fold. The L2-mortar diagnostic tracks
the reference almost exactly, because both are derivatives along the same sliding path
(§3.4).

### 3.3 Full electrical period (every ring node), terminal-work mean, self-check

p-p is the raw peak-to-peak of the full series. The terminal-work mean is the eligible
all-bin mean (imposed current, no eddy/demag, integer window). The self-check is
max |T_rotor-side − T_stator-side| / p-p.

| case | terminal-work mean | Coulomb mean (Δ vs TW) | Maxwell mean (Δ) | Coulomb p-p (%) | Maxwell p-p (%) | mortar p-p (%) | self-check |
|---|---|---|---|---|---|---|---|
| Ø40 rated gl1 r144 (shipped) | 0.625095 | 0.624404 (−0.11 %) | 0.622455 (−0.42 %) | 0.046308 (7.42) | 0.046131 (7.41) | 0.053717 (8.59) | 19.4 % — flagged |
| Ø40 rated gl1 r432 | 0.625218 | 0.624152 (−0.17 %) | 0.621785 (−0.55 %) | 0.046099 (7.39) | 0.045982 (7.40) | 0.054971 (8.79) | 18.9 % — flagged |
| Ø40 rated gl3 r288 | 0.624943 | 0.624811 (−0.021 %) | 0.622730 (−0.35 %) | 0.042308 (6.77) | 0.042085 (6.76) | 0.042818 (6.85) | 3.0 % |
| Ø40 rated gl5 r288 | 0.624933 | 0.624868 (−0.011 %) | 0.622785 (−0.34 %) | 0.041956 (6.71) | 0.041737 (6.70) | 0.042095 (6.74) | 0.66 % |
| Ø40 no-load gl1 r144 | 0 | −0.000314 | 0.000136 | 0.067401 | 0.067156 | 0.070606 | 11.7 % — flagged |
| Ø40 no-load gl3 r288 | 0 | −0.000089 | 0.000484 | 0.065832 | 0.065593 | 0.065578 | 2.2 % |
| L13 rated gl1 r120 | 4.965708 | 4.964364 (−0.027 %) | 4.924424 (−0.83 %) | 0.270355 (5.45) | 0.267908 (5.44) | 0.271440 (5.47) | 1.2 % |
| L155 rated gl1 r216 | 186.394583 | 186.375991 (−0.010 %) | 186.101786 (−0.16 %) | 2.794989 (1.50) | 2.787841 (1.50) | 2.788895 (1.50) | 1.6 % |

**Mesh convergence (Ø40).** The ring density has no effect: ring 144 and ring 432 at
gl1 give the same numbers. Gap layers dominate. From gl1 to gl3 to gl5, Coulomb ripple
goes 7.42 → 6.77 → 6.71 %, Maxwell 7.41 → 6.76 → 6.70 %, and mortar/FD
8.59 → 6.85 → 6.74 %. All three methods converge to the same ≈ 6.7 %, and the
harmonics converge with them (h6 0.0192 → 0.0188 N·m). The shipped gl1 mesh is
therefore not ripple-converged on Ø40 with any method. Against gl5, Coulomb and Maxwell
are 0.7 pp (+10 %) high there, and mortar/FD are 1.9 pp (+28 %) high. The self-check
flags exactly the unconverged meshes (19 % and 12 % of p-p), and reads 0.7 % at gl5.
No-load cogging behaves the same way. At gl1, Coulomb is 0.0674 N·m p-p and mortar
0.0706 N·m; at gl3 they are 0.0658 and 0.0656 N·m.

This also resolves the paused ripple study's observation that "Maxwell reads ~16 % low
on Ø40". Its reference, frozen-current FD on the gl1 mesh, is the method with the
largest gl1 error. Maxwell is not low against the converged ripple; it is 10 % high.

### 3.4 Coulomb vs the existing L2-mortar virtual-work diagnostic

`_p2_virtual_work_torque` (SB_P2_VIRTUAL_WORK=1) is −N_s·L·r·∂A/∂θ, where r is the
pointwise residual and ∂A/∂θ is the continuous-angle L2 slip-projection derivative. It
is the virtual work of sliding the rotor trace along the stator trace, which is the
solver's own motion. That is why it equals the frozen-current FD reference and the
terminal-work mean almost exactly.

* **Accuracy.** The two are equivalent on converged meshes. At gl5, mortar and
  Coulomb differ by an AC-RMS of 9.1e-5 N·m (0.2 % of p-p). On an under-resolved gap
  they are not equivalent, and the mortar is further from the converged ripple:
  +28 % against +10 % on Ø40 gl1. Neither can be promoted as "the truth" on that mesh.
* **Coverage.** The mortar is gated to imposed-current, Newton-accepted, saturable,
  no-eddy/no-demag/no-frozen-ν frames. Every client run (eddy, demag, voltage/PWM)
  therefore gets no mortar value. Coulomb needs only A and has no gate. On eddy + demag
  runs it closes the power balance (§3.5).
* **Cost.** Coulomb costs 0.17–1.2 ms per layer call (two calls per frame), against
  0.37–0.94 s per frame solve: ≤ 0.4 %. The Arkkio evaluator costs 0.3–2.5 ms. The
  mortar needs the pointwise stiffness at A (a memo hit on Newton frames) plus a
  trace-mass solve per frame.
* **Self-check.** Coulomb has one (two disjoint layers). The mortar has none.

Verdict: Coulomb is the better production method. The mortar is a good independent
diagnostic of the sliding-path derivative and stays as it is. Nothing is duplicated:
the mortar lives on the slip interface, and Coulomb deliberately excludes that
interface.

### 3.5 Eddy + demag (the product's duty settings, 1 period)

| case | reported | Coulomb mean | space-vector mean (hybrid) | power balance with Coulomb T | with the hybrid mean |
|---|---|---|---|---|---|
| Ø40 rated, eddy + rotor eddy + demag, `torque_method="coulomb"` | coulomb_virtual_work 0.616657, p-p 0.044219 (7.17 %) | 0.616657 | 0.623015 | field 847.9 W = T·ω 839.5 + eddy 7.88 + 0.56 W (0.063 % of P_in) | residual −8.1 W (−0.91 %) |
| L13 rated, eddy + rotor eddy + demag (default method) | energy_mean+maxwell_ripple 0.923043 | 0.787236 | 0.923043 | field 96.97 W = T·ω 82.44 + eddy 13.01 + 1.52 W (0.43 %) | residual −12.7 W (−3.56 %) |

The `torque_method="coulomb"` switch works end to end. The reported waveform, mean,
ripple, harmonics and method identifier are Coulomb's. Under eddy currents the
space-vector mean is the air-gap power over ω. It includes the rotor eddy loss, so it
over-reads the shaft torque by P_rotor/ω. The gap-stress (Coulomb) torque does not, and
it is the one that closes the solver's own power balance. On the L13 duty the
difference is 15 %. That duty's magnets run at 210.7 °C with duty demag on. In this
sandbox the magnets demagnetise heavily: 4.97 N·m without demag, 0.79 N·m with it. That
needs its own look; see the note in §5.

The layer self-check on these runs is 2.0 % (Ø40) and 1.2 % (L13).

### 3.6 Default behaviour unchanged

The default `hybrid_maxwell_ac` run (Ø40 rated gl1) was compared with the unmodified
base code. The difference in T_em, T_maxwell, ψ and I is ≤ 8e-14 N·m, which is the same
as two runs of the same code (multi-threaded MKL). Maxwell is evaluated with the same
arithmetic (`sector × NS`).

## 4. Recommendation: make Coulomb the default — YES, with two conditions

Why:

1. It is the only torque method that is valid on every run the product ships:
   imposed-current, eddy, demag, voltage/PWM and no-load cogging. It is one method for
   the mean and the ripple, with one method identifier, as P01 asks. There is no
   silent raw-Maxwell fallback at low current.
2. Mean: within 0.01–0.03 % of the terminal-work mean on converged meshes (0.11 % on
   the shipped Ø40 gl1). Raw Maxwell is 0.16–0.83 % low.
3. Ripple: its AC equals the Maxwell AC to 0.13–0.34 % of the p-p (RMS) on every case,
   so today's ripple numbers do not move. It matches the FD reference to ≤ 0.15 % of the
   p-p on L13 and L155, and to 0.7 % on Ø40 at gl3. Unlike Maxwell, it reports its own
   mesh error.
4. Under eddy currents it gives the torque on the rotor, and it closes the power balance
   where the hybrid does not (§3.5).

Conditions:

* **Small-gap machines need a finer gap mesh for ripple-grade numbers, whatever the
  method.** Ø40 at gap layers 1 over-reads the converged ripple by 0.7 pp (7.42 vs
  6.71 %), just outside the owner's max(0.5 pp, 10 %) target. Use gap layers ≥ 3 there.
  Its cost was 0.555 s/frame at gl3 against 0.511 s/frame at gl1. When
  `ripple_mesh_limited` is set (layer difference > 10 % of the p-p), the ripple should
  be shown as unconverged.
* **Published torques of eddy runs will drop by P_rotor/ω** (Ø40 −1.0 %, L13 duty
  −15 %). That is physically right, since it is the torque on the shaft side of the
  gap, but it changes numbers that are already published. The owner should decide the
  switch with that list in hand.

## 5. Notes and provenance

* L13 ran with the shared materials library `/srv/motres/shared/materials_library.yaml`
  (2026-09-16), which is the one the paused study used. With the branch's current
  `config/materials_library.yaml` (F52SH_120C re-audited 2026-09-29), the L13 rated
  duty's magnet temperature of 210.7 °C is refused: "+91 K past the card's reference
  takes Hcj below 0". This is a separate finding about that duty, not about torque.
  Ø40 and L155 used the branch library.
* Pre-existing, not from this change: `tests/test_solver_guards.py::
  test_every_per_frame_series_is_in_the_settle_trim_tuple` fails on the base branch too
  (`_eec_lam`, `_six_psi`, `_six_rows`).
* Code: branch `feat/coulomb-virtual-work-torque` from `pre-migration-freeze-2026-09-15`
  at bd29c6c. Server sandbox `/opt/motres/compute/coulomb-20260930` (deleted after the
  runs). Model: Claude Opus 5.5, no delegation, no escalation.
