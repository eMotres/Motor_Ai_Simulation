# Torque-ripple validation — status 2026-09-30 (PAUSED, work in progress)

**Status: paused on the owner's decision after the method was built and smoke-tested.
No converged ripple numbers exist yet. Nothing below is a validated result.**
The study scripts live in `scripts/ripple_validation/`, and the three smoke runs are in
`scripts/ripple_validation/results_smoke/`. The server sandbox
(`/opt/motres/compute/ripple-20260930`) was deleted and its containers killed. Two
full-resolution runs (Ø40 rated and L155 rated, ring 432) were killed part-way, at
650/2164 frames and about 550/2164 frames. They saved nothing, because the runner writes
its output only at the end.

## Question

The shipped loaded torque waveform is a replacement mean plus the raw Maxwell (Arkkio)
AC (`sb_postproc.hybrid_torque`, `fem_solver_2d.py` ~10225–10313). The replacement mean
is the terminal-work mean when the run is eligible, and the space-vector mean otherwise.
Astra review P01/P05 asks whether that AC, and therefore the reported ripple, is accurate.
Acceptance targets: ripple within max(0.5 pp, 10 % relative), and mean within 1 %.

## Reference method (implemented, verified on smoke runs)

`scripts/ripple_validation/ripple_ref.py` runs the production P2 sliding-band transient.
The run is magnetostatic, with imposed current, and uses the duty's own mesh settings.
The frame loop is patched in memory, and nothing in `src/` is changed. Two anchors are
source-transformed: the `m_shift` line and the `Ist = _src.mean_over(_fb)` line. This
lets each frame be placed at any slip-ring node with any **frozen** current vector.

At every frame the script evaluates the discrete functional that the pointwise Newton
actually minimises:

    Phi(A) = 1/2 A·K_const·A + Σ_sat Σ_q w(|B_q|) dx_q − f·A,
    w(B) = ∫0^B H_eff(s) ds,   H_eff = B/(μ0 μ_r(B)),

where μ_r(B) is the solver's own law, `field_ops._mu_r_from_bh_vec`, clamped at ≥ 1. The
same quadrature (`sb.dx`) and the same RHS `f` are used, including the demag-updated
`f_mag2`. The machine co-energy is W' = −N_s·L·Phi.

* **Reference A — frozen-current virtual work.** The rotor is moved by ±1 and ±2 slip
  nodes at the centre's current vector. T = 4th-order Richardson of the two central
  differences of W'. Each integer shift keeps its own weld projection, so this is the
  discrete FEMM/ANSYS-style co-energy derivative. It does not depend on a continuous-angle
  P'.
* **Reference B — trajectory energy balance.** T = dW'/dθ along the path minus
  n_par·Σ ψ_p di_p/dθ, using spectral derivatives. It needs no extra solve, but it needs a
  finely sampled, uniform full period. It is not usable at 12 samples per period (aliased).
* Recorded at the same frames: raw Maxwell, ψ_abc, i_abc, and the existing L2-mortar
  virtual-work diagnostic (`SB_P2_VIRTUAL_WORK=1`).
* Demag-on runs: the solver's settling period runs with the ratchet active. The ratchet is
  then switched off for the scheduled frames, so all methods see one fixed irreversible Br
  state.

**Self-checks that passed.** In every smoke run the central difference of W' with respect
to a ±1 % current scale reproduces n_par·Σ i·ψ. The relative difference was 7.6e-6 on
Ø40, −1.6e-6 on L155 and −2.5e-6 on L13. The energy functional is therefore consistent
with the solved field and the flux linkages. All frames were accepted by the pointwise
Newton (residual < 1e-7). The ±1-node and Richardson estimates differ by ≤ 0.0005 N·m on
Ø40, 0.025 N·m on L155 and 0.0035 N·m on L13, so ±1/±2 nodes is fine enough.

Cost of each magnetostatic frame, 2 threads, sandbox: Ø40 0.23 s on the shipped ring
(18.8k dofs) and 0.45 s on ring 432 (39.7k dofs). L155 about 0.6 s. L13 about 0.35 s.
A full reference (432 centres × 5 frames) costs 15–25 min per case.

## Smoke results (12 centres per electrical period — 1 per cogging cycle; NOT converged)

Machines: shipped ring, gap layers 1, static (no eddy), demag off. Means are over the 12
centres. At this sampling the 12th electrical order aliases into the mean and every p-p
is a 12-point p-p, so this table compares methods **at identical rotor states** only. It
is not a ripple value.

| machine / duty | ring/period | method | mean N·m | p-p N·m (12 pts) | p-p % |
|---|---|---|---|---|---|
| Ø40 CIANO14 40 new / L12 rated (42.78 A, γ 10°) | 144 | frozen-current VW (A) | 0.624994 | 0.04401 | 7.04 |
| | | L2-mortar VW diagnostic | 0.625005 | 0.04405 | 7.05 |
| | | raw Maxwell | 0.622542 | 0.03690 | 5.93 |
| | | shipped (terminal-work mean + Maxwell AC) | 0.625690 | 0.03690 | 5.90 |
| L155 rated 1x9 mm (562.1 A line, Δ, γ 15°) | 216 | frozen-current VW (A) | 186.727 | 1.2743 | 0.682 |
| | | L2-mortar VW diagnostic | 186.727 | 1.2731 | 0.682 |
| | | raw Maxwell | 186.433 | 1.2732 | 0.683 |
| | | shipped | 187.117 | 1.2732 | 0.680 |
| L13 CIANO28 85 20SW1200 / L13 rated (25.88 A, γ 2°, magnets 210.7 °C) | 120 | frozen-current VW (A) | 4.96800 | 0.1317 | 2.65 |
| | | L2-mortar VW diagnostic | 4.96803 | 0.1323 | 2.66 |
| | | raw Maxwell | 4.92667 | 0.1311 | 2.66 |
| | | shipped | 4.96885 | 0.1311 | 2.64 |

These are preliminary observations. Each needs the full-resolution runs before anyone
acts on it:

1. **Maxwell AC vs the co-energy AC at identical states.** On L155 the RMS difference was
   0.00054 N·m, and on L13 it was 0.00034 N·m. Both are negligible against the targets.
   **Ø40 is the exception:** the RMS difference was 0.0022 N·m, and the 12-point p-p is
   5.93 % for Maxwell against 7.04 % for virtual work. That is −1.1 pp, or −16 % relative,
   which is outside max(0.5 pp, 10 %). The small machine with a 0.2 mm gap is where
   Maxwell ripple is suspect. This must be confirmed on a full period and on the finer
   ring.
2. **Maxwell mean bias vs virtual work:** Ø40 −0.39 %, L155 −0.16 %, L13 −0.83 %. The
   old ~37 % over-read of the Maxwell mean (July memory note) does not appear on the
   current P2 merged band.
3. **The existing L2-mortar virtual-work diagnostic tracks the rigorous frozen-current
   virtual work** to within 0.00001–0.0006 N·m per sample on all three machines. If this
   holds at full resolution, it is the cheapest route to an energy-consistent ripple:
   one extra sparse product per frame and no extra solve.
4. **60° electrical symmetry.** At 30° spacing, samples 60° apart agree to 7.6e-7 N·m on
   L13 and 0.0012 N·m on L155, but differ by 0.0048 N·m (Maxwell) on Ø40. A 60° window is
   not yet proven for Ø40 (P05/G08), so the reference must use full electrical periods.
5. **Shipped angular resolution is set by the ring, not by the 9/cycle rule.** The ring
   has 144, 120 and 216 nodes per period on Ø40, L13 and L155. The 108-step request
   therefore snaps to 144 (12/cycle), 120 (10/cycle) and 108 (9/cycle). The ring caps
   sampling below 36 per cogging cycle on every machine, so the angular study needs a
   denser ring (`SB_SLIP_PER_PERIOD`), and that also changes the band mesh. All three
   stators are paired-tooth: their fundamental cogging order is **6** electrical, not 12.

## What is left

Queued before the pause, none of it run. Commands are in the scripts' usage lines:

| run | purpose |
|---|---|
| d40 rated / peak, ring 432, all centres, ±1/±2 VW + current check | full-period reference; mean, p-p, h6/h12/h18/h24 for A, B, Maxwell, shipped, mortar |
| d40 rated / peak, ring 144 | the same comparison on the shipped mesh |
| d40 rated, ring 864 (stride 2); ring 432 with gap layers 3; ring 432 with mesh ×0.7 | mesh convergence (tangential band, radial band, global) |
| L155 rated, ring 432 and ring 216 | reference and shipped mesh |
| L13 rated ring 432 and ring 120, demag off and on (settle 144/120, then frozen Br) | including demag |
| no-load ring 432 for each machine | cogging in N·m (T = dW'/dθ, spectral, with ±1/±2 VW on every 4th centre) |
| `shipped_run.py` per duty (eddy + rotor eddy + duty demag, 108 steps) | the number the product actually reports, and the size of the eddy effect on the Maxwell AC |

The angular-resolution study (6/9/12/18/36 per cycle, every phase offset) needs no extra
runs. `report_tables.py` subsamples the ring-432 centres (`maxwell_subsampled`,
`energy_subsampled`).

The recommendation and the proposed flag are still open, but here is the candidate. If
the full runs confirm observation 3, the minimal change is a flag, not a default switch:
`ripple_method="mortar_vw"` / env `SB_RIPPLE_VW=1`. On eligible frames (imposed current,
no eddy, Newton accepted) it would take the AC from the existing
`T_em_virtual_work_diagnostic_Nm` instead of raw Maxwell, and it would record the method
identifier as P01 asks. If observation 3 fails, the fallback is the co-energy functional
above, evaluated per frame with reference B (trajectory spectral) on an integer-period
uniform window. Eddy and demag frames still have no energy reference. The eddy effect has
to be bounded separately with the `shipped_run.py` comparison.

## Provenance

Code: branch `study/torque-ripple-validation` from `origin/pre-migration-freeze-2026-09-15`
at `bd29c6c`. Solver source was uploaded unchanged to the sandbox. Duty inputs were
read-only copies of workspace `c309c100cd421858`: die.yaml and duty yaml for
`CIANO14 40 new/L12`, `CIANO28 85 20SW1200/L13` and `CIANO10 200 opt/L155 motor`, plus
the shared `materials_library.yaml` of 2026-09-30. Mesh hashes are in the result JSONs:
Ø40 `ac06a3c93a16`, L155 `3d0a8a0892c4`. The d-axis was auto-calibrated in each run
(Ø40 60.0°, L155 120.0°). Server: motres-api:test containers, nice 19, ionice idle,
2 threads each, at most 2 containers. Model: Claude Opus 5.5, no escalation, no
delegation.
