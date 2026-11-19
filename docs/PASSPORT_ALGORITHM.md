# Motor passport algorithm — specification v1.1 (revised after review)

Status: **revised after the independent review of 2026-09-30** (Codex Astra,
review of commit 5044638: direction accepted for a bounded Ø40 pilot, computation
baseline **not approved**). Every review finding is dispositioned in §14 —
fixed / planned / needs owner decision — and the review's gates are §15.
Inline corrections carry the finding id in brackets, e.g. [P14]. Docs only
except the three legacy extraction bugs, now fixed in PR #81
(`fix/passport-loss-bugs`). Pilot machine: **CIANO14 40 new / L12** (Ø40, 12 slots /
14 poles, star, "2S", F52SH_120C magnets, 20SW1200 steel).

Evidence base: the Configure audit of 2026-09-30 (FEM of the tuned knobs vs the
tuner, the γ sweeps and timings below; sandbox runs on the production server
image, deployed code `101aa45`), PR #49 mesher S2/S3 (`docs/MESHER_TRANSITION.md`,
`docs/MESHER_COMPARISON_2026-09-29.md` on `feat/gmsh-parity`), PR #63
(`docs/EDDY_SHAFT_SETTLE_2026-09-29.md`), PR #66 (Cholesky), PR #51
(independent materials). Every measured number in this document names the run
it comes from; every number without a source is marked **estimate**.

Conventions [P03]: γ is the current angle from the q-axis (γ > 0 = negative
i_d, i.e. toward field weakening), identical to the solver's `gamma_deg` and to
ANSYS `el_deg`. I is the terminal RMS current (phase in star, LINE in delta).
The dq frame is **amplitude-invariant, peak**, in the MOTORING equivalent star:
`i_d = −√2·I·sin γ`, `i_q = √2·I·cos γ`; ψd, ψq are phase peak flux linkages;
T = (3/2)·p·(ψd·i_q − ψq·i_d); P = (3/2)(v_d·i_d + v_q·i_q). Generator
operation keeps the same frame with γ in (90°, 270°) and negative T — never a
sign flip of the stored map. A delta machine is stored as its equivalent star
(I = line current, V = winding voltage/√3, R and L = winding/3, winding-to-line
phase shift of 30° applied to angles and waveforms, power conserved); the
delta zero-sequence (triplen) circulation is NOT in the equivalent star and is
carried as its own loss term (the solver's `P_cu_circulating_W`). The
convention version is stored with the passport (`convention_rev`). N is wire
rows per slot; electrical turns = N / wire_parallel × wire_split. "NI" =
ampere-turns per slot. Torque: see B1 — the stored mean-torque METHOD is an
explicit field of every point.

---

## 1. Scope and goal

The client flow this passport serves:

1. **Request** (web Configure or MCP `start_design`): torque/power, speed,
   bus voltage, cooling, duty, envelope.
2. **Machine with the right parameters:** the nearest catalog or own machine
   that has a *current* passport, scaled by stack length, turns, wire height,
   connection and operating point.
3. **Knobs limited to validated ranges:** every knob has an envelope derived
   from FEM check points (section 9). Inside it the tuner answers instantly
   from the passport; outside it the tuner refuses loudly with the quantity
   that is not trustworthy and why.
4. **FEM confirmation** of the tuned point: one click for users with FEM
   rights; a "Request calculation" for everybody else (section 10).

Accuracy priorities (owner, 2026-09-30):

| Quantity | Target vs the converged FEM reference |
|---|---|
| Mean torque | ≤ 1 % physical (vs an independent reference, B1) — interpolation, mesh, time and settle errors are allocated inside it (§9) [N33] |
| Torque ripple (peak-to-peak / mean) | ≤ max(0.5 pp, 10 % of the value) |
| Flux linkage ψd, ψq (interpolation target, drives torque) | ≤ 0.5 % of \|ψ\| |
| Terminal voltage / EMF (drives the voltage limit) | ≤ 1 % |
| TOTAL loss | ≤ 5 % |
| Individual loss groups | no own tolerance, EXCEPT groups that set a thermal limit: decided by thermal sensitivity dT_limit/dP_group against the remaining thermal margin, not by watt share [P07] |
| Near-zero quantities | absolute floors: torque 0.05 % of rated, ripple 0.05 pp, cogging 0.1 % of rated torque (N·m), losses 0.1 % of the total [N33] |

## 2. Inputs and fingerprints

A passport is a function of exactly these inputs; each is hashed and stored
with it:

| Input | Source | In the fingerprint |
|---|---|---|
| Geometry (all primaries, derived fields recomputed by `merge_geo_override`) | die + configuration `geometry_overrides` | `geometry_sig` = `routes.simulation._geometry_fingerprint` of the resolved machine |
| Materials (per part) and the library card VALUES | configuration `materials` + `materials_library.yaml` | `materials_sig` = hash of the resolved card dicts actually used (not the names) |
| Winding: coils/phase, connection, parallel paths, star/delta, wire_parallel, wire_split | configuration `winding` | `winding_sig` |
| End-winding model (geometric k_end or a measured one; see 5.3) | configuration | in `winding_sig` |
| Magnet temperatures of the grid (hot, cold) and coil temperature reference | section 8 | in `thermal_sig` |
| Bus (pack v_min/v_nom/v_max, controller class) | configuration `battery`, Controller tab | `bus_sig` (only the PWM block and the voltage-limit map depend on it) |
| Cooling | thermal settings | not in the passport's EM part; the thermal limit is recomputed live |
| Computation baseline (section 3.0): code commit / image digest, mesher and version, mesh rule version, solver settings | the run | `baseline_sig` |

**Three identities [P29].** (1) the BASE machine a passport was measured on
(its signatures above); (2) the SUPPORTED KNOB DOMAIN it may be scaled into
(the validated cells of §9); (3) the EVALUATED INSTANCE the tuner shows (base
signatures + knob values + operating point + temperatures). Matching compares
(1) exactly; the knobs live in (2), never in the geometry hash.

**Signature construction [P28].** One immutable, canonical, resolved job
snapshot per passport: every number normalised, schema-versioned, hashed; it
includes the winding phase/sign map, terminal connection, mode, part states,
end/segmentation/contact models, the calibrated d-axis, temperatures and the
magnet state. `routes.simulation._geometry_fingerprint` is NOT that hash (it
mixes the raw live config and returns "nofp" on failure) — the snapshot hash
fails closed. The snapshot is handed to EVERY job of the passport (base, grid,
PWM, checks), so no sub-solve can inherit the panel's context.

**Stale rules.** A passport block is *current* only when the signatures it
depends on equal today's. Otherwise:
- geometry, winding topology, or any material CARD VALUE it used changed →
  the dependent blocks are **stale**: no numbers from them, "passport out of
  date", a regeneration request (section 10). [P29: a card-value change is
  never "older baseline"]
- only the bus changed → the static grid stays valid; the PWM block and the
  voltage map are recomputed; the loss grid stays valid ONLY where its stored
  control trajectory (id, iq, T, state) is still the one the new bus requires,
  else those points need FEM [P10];
- only the computation baseline changed (solver/mesher version, NOT material
  values) → usable under an explicit compatibility policy per baseline pair,
  flagged "older baseline", queued for the recompute.

Matching a machine to a passport is by these signatures ONLY. Today four
different name/cross-section rules coexist (ConfiguratorPanel `pick`, the
family exhibit filter, the datasheet card lookup, agent_designs
`_reference_card`); all four are replaced by the signature match.

## 3. Static grid (ψ-map) — torque, flux, voltage

### 3.0 Computation baseline v1 (fix before the global recompute)

Each item: **decided**, or **needs owner decision / evidence**.

| # | Item | Proposal | State |
|---|---|---|---|
| B1 | Torque method [P01, P02] | Policy stays energy / flux-linkage, but the v1 text overstated what ships: the loaded waveform is the replacement MEAN plus the raw Maxwell AC part (`fem_solver_2d.py:10225–10313`, `sb_postproc.py:401–576`), and the space-vector mean the eddy/demag/PWM runs use is not certified virtual work. v1.1: every stored point carries its mean- and ripple-method identifiers; a published loaded point refuses a silent raw-Maxwell fallback; independent mean-torque evidence (terminal work over an integer period, lossless demag-off current-driven case; port-work / solved-loss / storage balance for eddy runs) and mesh + angular convergence of the loaded ripple are gates. The dq check T_ψ vs T is a frame/unit check only, not an independent torque check. | **needs evidence** |
| B2 | Mesher | Triangle CDT (non-commercial, AGPL-incompatible) vs the gmsh backend of PR #49. S3 evidence: L155 and L180 pass every limit; L13 passes at 0.61 mm; field quantities agree ≤ 0.04 % with demag off. Open: (a) L13 default 1.22 → 0.61 mm; (b) candidate c04 magnet eddy −12 %; (c) gmsh wall/RSS tails ≤ 2.08× on sub-0.05 mm fillets. Under the owner's loss rule (only the TOTAL at 5 %), (b) is **0.025 W** on a machine whose total loss is watts to tens of watts — irrelevant for the total, so it no longer blocks; torque there is within ±0.06 % and ripple is the item to check (c04 +4.5 pp ripple — **that** fails the ripple target and needs the boundary-refinement check). (c) is a cost item, not an accuracy item. | **needs owner decision**: switch the passport baseline to gmsh (recommended, licence), accepting (c); c04 ripple needs one boundary-refinement run as evidence |
| B3 | Mesh sizing rule | One rule for every machine, from physical scales (memory `mesh-follows-physical-scales`): air gap ≥ 2 element layers across the mechanical gap (structured belt); conductive solids (magnets, sleeve, shaft) first layer h1 = δ/k with δ the skin depth at the highest harmonic that carries ≥ 1 % of the group's loss, growth 1.5, ≥ 16 cells per wavelength (the shipped `SB_SKIN_*` settings, converged on L155: ×4 finer layers move shaft +0.11 %, sleeve ≤ 0.02 %); copper: one element across a strand is the shipped setting — resolution for AC copper must be proven (section 6.3: the two AC routes still differ 1.6× on L155); iron: cell size ≤ min(tooth tip, slot opening)/3, bulk capped at the machine-class size; slip grid set by the time step. Convergence proof per machine CLASS (Ø40-class, 85 mm, 150 mm, Ø200): three levels with a geometric refinement ratio (the L13 study showed the current floor 0.12 mm² makes "0.305 mm" mesh at 0.53 mm, so Richardson is invalid there — the floor and the slip grid must be refined together). | **needs evidence**: one geometric three-level study per class (night) |
| B4 | Time integration [P05] | Steps per electrical period: 72 with eddy (solver default, `simulation/eddy_steps.py`); ripple-grade = the shipped **9 samples per cogging cycle** (6 can under-read a sinusoid's p-p by 13.4 %), then verified by a phase-shifted and an angularly refined waveform; the actual symmetry period is established per machine (paired teeth / winding symmetry can break the Ns/p assumption) and the snapped angles/steps are recorded. Settle rule 3.0.1. BDF2. | **needs evidence** (symmetry, ripple convergence) and **owner decision** (settle rule) |
| B5 | Solver | P2, merged structured belt, Newton, nonlinear residual ≤ 1e-7, Cholesky (PARDISO mtype 2, PR #66) for SPD systems. | **decided** |
| B6 | Eddy start and settle | Magnets: static start; shaft/sleeve: pole-pair image mean start + TP-EEC (PR #63). | **decided** (merged) |
| B7 | Demag [G08, P09] | **Default: today's full pre-pass** with the Br ratchet frozen during warm-up. 3.0.2 is a research proposal, enabled per topology only after qualification. Maps are fitted at a SPECIFIED fixed retention state (state hash + initialisation stored); damage is a boundary / separate passport state, never interpolated across; the current limit is an (id, iq, T) safe surface, not a scalar I. | **decided** (default); shortcut **needs evidence** |
| B8 | Materials library | PR #51 (every value from an independent source; 14 cards `verify: true`; N52UH supplier data pending). A card change → `materials_sig` changes → every passport using that card becomes "older baseline" and is queued. | **needs owner decision**: merge #51 before the recompute; N52UH supplier data |
| B9 | Reproducibility [G30] | Store with every passport: source-tree digest incl. dirty state, immutable image digest and mounts, the complete inputs/settings/environment (`SB_*`, threads, BLAS), library versions (gmsh 4.15.2 / triangle, scikit-fem, pypardiso), actual mesh and tags hash, angle grid, initial states (Br, warm seeds), raw run ids and FAILED runs. Bitwise mesh repeatability (PR #49 test) is separate from tolerance-based output repeatability (threaded FEM is not bitwise; Cholesky round-off). Cache keys versioned; blocks promoted atomically only when complete and verified. Full-precision data everywhere — display rounds (fixed in PR #81 for the dq outputs and the passport). | **decided**; implemented **before the pilot** (M0), not after |
| B10 | TDM / parallel-in-time | Optional later speed-up if the eddy transients still dominate after B4. Not a blocker. | **later** |

#### 3.0.1 Settle rule proposal (owner priorities) — revised [P06, P07]

v1 used adjacent-period changes; the review showed they do not bound the
remaining transient: with a slow mode τ = 21 periods (λ = 0.9535) the
remaining tail is 20.5× the latest change, so a 0.5 % change can hide 10 %.
Revised rule — stop when ALL hold:
1. the state-period residual (PR #63 gauge) or a validated slow-mode tail
   bound (inverse-iteration λ of the operator) bounds the REMAINING change of
   mean torque below 0.1 % and of ripple below 0.2 pp, on phase-aligned
   waveforms (means and p-p alone can hide beats);
2. the summed REMAINING watts of all groups (tail bound, not last change) is
   below 0.5 % of the total loss;
3. thermal criticality overrides watt share: every group whose sensitivity
   dT_limit/dP_group times its remaining-watt bound exceeds 10 % of the
   remaining thermal margin keeps the strict gauge — no blanket magnet /
   sleeve / shaft percentages;
4. a small group may be waived only with a bounded remaining-watt estimate and
   a low thermal impact, and is reported "not settled, bound X W";
5. after any accelerator jump (TP-EEC) the history is reset and ≥ 4 continuous
   original-march periods are verified, as PR #63 requires; failed nonlinear
   frames are never waived.

Measured basis (PR #63, L155 rated 36 steps): T, ripple and V_peak within
2e-5 of the asymptote, P_mag/P_sleeve/P_cu within 0.002 %, total 3820.34 W;
the march ran 614 frames (17 periods) because the SHAFT (4.0 W = 0.1 % of the
total) has a slow wall mode (τ = 21 periods). Under the rule above the shaft
no longer holds the run. **Estimate:** 614 → 150–220 frames (4–6 periods;
−65 to −75 %), to be measured by replaying the stored per-period gauge series
of the L155, L13 and Ø40 duties — now with the tail-bound rule, so the saving
is smaller than v1 estimated and must be measured. **Not safe** for PWM runs
whose carrier deltas are themselves the answer (they keep the strict gauge).

#### 3.0.2 Demag shortcut — research proposal, NOT the default [G08]

Review verdict: the repair in v1 is an ALL-magnet orbit-minimum method, not a
one-magnet solve; LCM arithmetic of ideal cogging / six-fold periods does not
prove the actual geometry, winding phase/sign, material and magnet-state
invariance; two passes do not prove the nonlinear fixed point. Default stays
the full pre-pass (B7). To qualify the shortcut per topology: derive the
actual combined space/time symmetry and magnet orbits; map LOCAL coordinates,
magnetisation vectors and volumes (not raw element indices); iterate to a
stable Br and re-settle the eddy state after every change; compare full
histories at rated, peak, deep FW and hot, plus an integer-slot fixture.
The v1 argument is kept below for the record.

**Argument.** Under a steady sinusoidal current the armature MMF is
synchronous with the rotor, so every pole sees the same field history shifted
by one pole pitch in time; the six-step pattern of a balanced three-phase MMF
repeats every 60° electrical. Irreversible demag is a ratchet: once the worst
field position has passed a magnet, its Br map no longer changes. So the final
Br map of ONE magnet after its worst 60° electrical (plus a margin) is the
final map of every magnet, copied by rotation (pole pitch) and sign
alternation (N/S).

**Where the plain argument breaks, and the fix.**
- Fractional-slot machines (12s/10p, 12s/14p, 24s/28p — all our machines):
  a magnet's local field is the synchronous fundamental plus the slot-
  permeance modulation, whose period for ONE magnet is a slot pitch
  (360°·p/Ns electrical: 210° on 12s/14p, 150° on 12s/10p), longer than
  60°. But the whole machine configuration repeats every cogging cycle
  360°/(lcm(Ns, 2p)/p) (30° on all three topologies) with the magnets
  RELABELLED. So over a window W = lcm(cogging cycle, 60°) electrical
  (= 60° on all our machines) the SET of magnets together passes through
  every (MMF phase, slot position) state that any single magnet meets in a
  full period. Procedure: march the window once with all magnets live,
  record per element the worst working point, then set each magnet's final
  Br map = the element-wise minimum over its relabelling orbit (elements
  mapped by rotation and N/S mirror). Because a demagnetised magnet changes
  its neighbours' field, repeat one more window with the combined map and
  re-apply the minimum (expected to converge in 2 passes — **needs
  evidence**).
- The ratchet model must be "Br set by the worst field ever seen" (per
  element, irreversible); any rate or history dependence beyond the minimum
  breaks the min-combination.
- PWM ripple adds a carrier-frequency field; the worst instant may not be on
  the fundamental's worst position (demag under PWM stays on the full march).
- Non-steady current (load steps, the ratchet during a duty change) — the
  method is for the steady passport points only.

**Window.** W = lcm(360°·p/lcm(Ns, 2p), 60°) electrical — 60° for 12s/14p,
12s/10p and 24s/28p — plus the eddy warm-up the run already needs (the Br
ratchet stays frozen during warm-up, as today), sampled at the run's steps
(≥ 12 per window).

**Validation (needs evidence).** Compare per-element Br(min) map, T, ripple
and EMF between (a) today's full pre-pass and (b) the 60° window + orbit
minimum (one and two passes), on
L13 peak (magnets ~210 °C, 76 % EMF loss), the `p2_demag` regression fixture,
and one integer-slot case if one exists (none in the catalog today — build a
24s/4p test fixture). Targets: T ≤ 1 %, ripple per section 1, Br(min) map
difference ≤ 0.5 % of Br on 99 % of magnet elements. Gives the TDM/periodic
solve a fixed magnet state immediately and shortens the plain march today.

### 3.1 Grid in ampere-turn space

Current levels (at the passport's base winding): **7 levels**
I/I0 ∈ {0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 2.5} capped at the demag knee of the
hot magnet (section 8), I0 = the machine's rated current. Stored in NI so one
grid serves every turns/connection combination (NI_eq = fN·fConn·I).

Per level, the γ set:
1. **MTPA location**: 3 points at γ̂−5°, γ̂, γ̂+5° around the prediction γ̂
   (the previous level's γ_MTPA; first level γ̂ = 0), parabola fit; one extra
   point if the vertex falls outside the bracket.
2. **Field-weakening arm**: 4 points γ ∈ {35, 50, 65, 80}° (above γ_MTPA).
3. One γ = −5° point at the two highest levels (saturation shifts the ridge).

Total ≈ 7 × 7 + 2 = **51 static points** + 8 off-grid check points.

**Measured on the audit machines** (static points, 6 rotor positions over ONE cogging cycle = 30° electrical, demag off, rotor eddy off, duty mesh; 5 γ × 4 levels + FW arm {50, 65, 80}° at 1× and 2× I0):

| I/I0 | 40 mm γ_MTPA | 40 mm T_MTPA [N·m] | L155 γ_MTPA | L155 T_MTPA [N·m] |
|---|---:|---:|---:|---:|
| 0.5 | 2.6° | 0.2993 | 1.1° | 93.94 |
| 1.0 | 5.4° | 0.5902 | 2.6° | 184.1 |
| 1.5 | 7.9° | 0.8635 | 3.9° | 268.8 |
| 2.0 | 10.7° | 1.104 | 5.1° | 347.1 |

Both are **SPM-like** (γ_MTPA ≤ 11°, Ld ≈ Lq: 40 mm Ld/Lq 0.0092/0.0099 mH,
L155 0.060/0.062 mH winding). γ_MTPA moves ~2.7°/I0 (40 mm) and ~1.3°/I0 (L155)
with current — the ridge is flat (the sampled γ_MTPA ± 10° are within 1.6 % of the vertex), so the 3-point
parabola is sufficient and the extra point is rarely needed. Note the saved
duties run at γ = 10° (40 mm, −0.4 % torque vs MTPA) and γ = 15° (L155:
T(15°) = 179.7 vs 184.1 N·m, **−2.4 % torque at rated current**).

### 3.2 Rotor positions per point

Loaded torque of a three-phase machine has its lowest ripple harmonic at 6·f_e;
the slot/pole ripple sits at multiples of lcm(Ns, 2p)/p · f_e (12 per period
on 12s/14p and 12s/10p). The static point therefore samples **60° electrical
(1/6 period) at 12 positions** (5° steps) — to be re-based on the shipped
9-samples-per-cogging-cycle rule and the machine's PROVEN symmetry period
[P05]; the 60° window must be compared against a full proven period before it
is used; mean torque and ψd, ψq free of
the 6th-harmonic bias a 30° window can carry (the audit's 30° windows gave
0.58918 / 0.58923 N·m with 6 / 12 positions on the Ø40 — the window, not the
count, is the open question; the 60° choice is to be confirmed on the pilot), ripple resolved at ≥ 6 samples per slotting cycle. Ripple-
grade machines with a finer ripple order use lcm-based spacing
(6 samples per slotting cycle over 60°).

Measured wall time of one static point (4 threads, duty mesh):

| Machine | 6 positions | 12 positions |
|---|---:|---:|
| 40 mm (mesh 1.0 mm) | 5.2 s | 6.3 s |
| L155 (mesh 4.0 mm) | 7.8 s | 12.5 s |
| single-thread, first point in a fresh process (includes the one-time d-axis calibration + ψ_PM probe) | 32.9 s (Ø40), 47.7 s (L155) | |

(A voltage or iron loss read off a 60° window is meaningless — voltage is
computed from ψ, iron loss comes from the loss grid.)

### 3.3 Stored quantities per point

T_avg, T_ripple (p-p/mean), ψd, ψq, i_d, i_q, the Br retention (volume-
weighted) and the worst-element corner diagnostic, the settled flags, the
run id. Voltage is NOT stored as a sampled peak (the coarse L155 passport of
the audit read its no-load EMF 14 % low because the peak of a 6-step
waveform was taken) — the terminal voltage is computed.

### 3.4 Interpolation

Measured on both machines with 31 points (4 levels × 5 γ + a 3-angle FW arm at
2 levels) and 5 off-grid check points:

| Scheme | 40 mm max error (T / ψd / ψq) | L155 max error |
|---|---|---|
| (I, γ), Clough–Tocher cubic | 6.8 % / 0.46 % / 1.2 % | 6.7 % / 1.4 % / 1.7 % |
| (I, γ), linear | 28.9 % / 1.4 % / 5.1 % | 27.8 % / 1.9 % / 7.1 % |
| **(i_d, i_q), Clough–Tocher cubic** | **0.19 % / 0.21 % / 0.06 %** | **0.17 % / 0.14 % / 0.12 %** |
| (i_d, i_q), linear | 0.98 % / 2.2 % / 0.37 % | 0.71 % / 0.54 % / 0.52 % |

(ψ errors relative to |ψ|; the large (I, γ) errors are at deep FW where the
torque itself is small.) **Decision: interpolate ψd(i_d, i_q) and
ψq(i_d, i_q) with a C¹ cubic (Clough–Tocher on the scattered grid, or a
bicubic on a resampled regular (i_d, i_q) grid), and compute torque from them
AND from the stored energy torque; the two must agree ≤ 0.5 % at every grid
point (self-check).** Target ψ ≤ 0.5 % at every check point.

### 3.5 MTPA / field-weakening map

For each requested (speed n, torque T*):
- electrical speed ω = 2π·n/60·p;
- V_ph(i_d, i_q) = √[(R·i_d − ω·ψq)² + (R·i_q + ω·ψd)²] (steady state, ψ from
  3.4, R at the winding temperature, section 8);
- voltage limit V_ph ≤ m_max·V_dc/√3, m_max = 1.0 (SVPWM linear) minus a
  margin (proposal 0.95) — per the bus minimum v_min for the guaranteed
  map, v_nom for the typical one;
- current limit I ≤ min(I_thermal(duty, cooling), I_demag(hot knee)/(fN·fConn));
- below base speed: MTPA point on the ridge; above: the minimum-|i| point on
  the torque contour that satisfies the voltage limit (FW); beyond the last
  FW angle of the grid (80°) or the grid's lowest ψ: **refuse** ("needs deeper
  field weakening than characterised").

The MTPA-only variant (comparison row, owner's first proposal): 7 levels × 3–4
points = 21–28 static points, but the map ends at the base speed: 40 mm
MTPA at rated current reaches the bus at ≈ 13 000 rpm on v_min 18.0 V and
≈ 16 000 rpm on v_nom 22.2 V (estimate from the measured ψ: 0.739 V/krpm
phase peak + R drop); L155 ≈ 18 700 rpm on v_min 549.6 V and ≈ 25 500 rpm on
v_nom 750.4 V (29.6 V/krpm line peak at MTPA). Above those speeds the
MTPA-only passport must refuse; the rated 40 mm point (13 000 rpm) is already
at the v_min limit, so MTPA-only is **not sufficient** for the pilot.

### 3.6 Torque method and self-check

Energy / flux-linkage torque (B1). Every static point also yields
T_ψ = 3/2·p·(ψd·i_q − ψq·i_d); |T_ψ − T_energy| ≤ 0.5 % is a stored check
(the solver's `dq_torque_check_pct` already computes it).

## 4. Loss grid

**Grid [G11, P10]:** 5 speeds {0.25·n0, 0.5·n0, n0, 1.5·n0, n_max} (n_max in
rpm = the mechanical limit, section 5.5; sorted and de-duplicated after the
limits, infeasible points flagged) + a zero/low-speed anchor, × 3 currents {low 0.5·I0, rated I0, peak = the current
limit}, each a **settled coupled-eddy transient** (strands resolved, sleeve
and shaft in the solve) at the operating angle for that point: γ_MTPA below
base speed, the FW angle from 3.5 above it. ≈ 15 transients. **This grid is
a control TRAJECTORY, not a general loss map [P10]:** every point stores its
actual (id, iq, T, magnet state); reuse is restricted to that trajectory
(with validated corrections), and targeted FW / id samples are added where a
bus, turns, length or temperature change moves the FW angle at fixed n/NI;
off the covered trajectory the tuner requests FEM. P = a(I)·f^k(I) is only a
local interpolant between locally comparable waveforms, never a law through
FW and never a log fit through zero/noise; losses are stored as component
integrals and local spectra sufficient for flux changes, and interpolated
positively bounded [G11].

**Recorded per point:** iron (stator/rotor, hysteresis/eddy/excess split from
the core-loss surface), magnet eddy, sleeve, shaft, AC copper (coupled route)
and DC copper, the settled flags, the harmonic spectrum of the stator flux
density in 3 probe regions (tooth, yoke, tooth tip — used for f-scaling
between points).

**Interpolation:** in (n, I) with the loss per group scaled between grid
points by its own frequency law fitted on the neighbouring speed points
(P = a(I)·f^k(I), k fitted per group per current row; hysteresis ~f,
eddy/AC ~f², excess ~f^1.5), never extrapolated past n_max; bilinear in I on
the fitted a(I).

Today's passport sweep (for comparison): 6 speeds × 3 currents = 18 points,
12 steps, mesh +1 mm, ONE un-settled period, rotor eddy post-processed (no
sleeve at all, shaft 20× low: L155 0.23 W post-processed vs 4.4 W coupled),
copper AC from the analytic slab route, fixed γ0. For delta machines the
stored AC factor is 3× low and the tuner then drops AC copper entirely (audit
§3).

Measured cost of one settled coupled-eddy transient (36 steps, 4 threads, duty
mesh): **40 mm 91.6 s, L155 677.8 s** (+demag: 236 s / 784 s).
Single-thread: Ø40 79.0 s (faster than 4 threads — these systems are too small to gain from threading); L155 not measured (estimate 1.0–1.5× the 4-thread time).

**Validation:** 3 off-grid (n, I) points per machine, e.g. (0.75·n0, 0.75·I0),
(1.25·n0, 1.25·I0), (0.35·n0, peak); targets total loss ≤ 5 %, thermal-limit
groups ≤ 10 %.

## 5. Stack length

5.1 **2-D is exactly ∝ L** for T, ψ, iron and 2-D copper (audit: T/EMF law
error 0.0 % at 0.5× and 2× L on both machines). Everything that is not ∝ L is
listed below.

5.2 **3-D factors** at 4 stack lengths {0.5, 0.75, 1.0, 1.5 (or 2.0)}·L0
(night): k_flux(L) (EMF, ψ_PM), **k_T(L)** (torque, Stage B virtual work —
k_T ≠ k_flux: 0.9795 vs 0.952 on the Ø40), k_L(L) (inductance, 1.065 on
12 mm). Interpolate in 1/L (end effects scale with the end-to-length ratio),
clamp outside the measured range and flag. The current tuner applies k_flux
to torque — wrong by ~2.9 % on the Ø40.

5.3 **End windings analytic [P14 — corrected; FIXED in PR #81]:** use ONE
convention. One-side equivalent (the solver's, `field_ops`: copper volume =
active × k_end): `ell = (k_end0 − 1)·L0`, `R(L) = C·(L + ell)`. Or full turn:
`l_end = 2·(k_end0 − 1)·L0`, `R(L) = C_turn·(2L + l_end)`. v1 mixed `2L + l_end`
with `l_end = (k_end0 − 1)·L0` — a factor-two error. End share e = 1 − 1/k_end0.
Ø40 check (k_end 2.19): e = 0.543379, ell = 14.28 mm (full turn 28.56 mm),
R(L/2)/R0 = 0.771689, R(2L)/R0 = 1.456621 (pinned in
`tests/test_passport_loss_fixes.py`). No FEM is needed to split a declared
k_end. A change of the coil BUNDLE (wire width, split, tooth width) changes
ell: the geometric model recomputes it per solve; a measured factor is
labelled measured and must be re-measured. Never pass a fixed k_end to a solve
at a different L (audit: `endWindFrac = 0` on 7 of 14 passports, copper
−35.21 % / +37.30 % at 0.5× / 2× L on the Ø40).

5.4 **Magnet eddy vs L:** axial segmentation factor. The deployed solver
already applies an L-dependent factor on L155 (P_mag 87.4 → 51.6 → 162.3 W at
L0, L0/2, 2L0: ×0.59 / ×1.86, not ×0.5 / ×2); the tuner assumes ∝ L
(law error −15 % / +8 %). Store the factor's formula and inputs (segment
length, magnet width, skin depth) in the passport and apply it analytically.

5.5 **Mechanical critical speed and stress** re-evaluated per L (the rotor
gets longer): first bending critical speed (existing mechanical module) ≥
1.2 × n_max; sleeve/rotor stress SF per the averaged-stress convention. n_max
of the knob = min(bearing speed rating, critical/1.2, stress limit).

5.6 **Mechanical losses** (the tuner's η is at the shaft, memory
`one-efficiency-at-the-shaft`): bearings — SKF frictional-moment model per
card and lubricant (`bearings.friction`, `bearings.py:441`; cards in
`config/bearings_library.yaml`; loads from the rotor mass and the beam
split, `mech_losses.machine_mech_losses`, `mech_losses.py:334`); windage —
Couette gap (Taylor-number regime, Wendt/Bilgen–Boulos) + two end faces
(`bearings.windage`, `bearings.py:639`), gap term ∝ L, faces independent of L;
bore-air self-pumping is a cooling model (`simulation/cooling_models.py:656`
`bore_air`), not a drag term today. All analytic, no FEM, scale with L and n
directly. Validation that exists: the 150 mm windage cross-check in the
`windage` docstring and the bearing model's validated 150 mm case
(`bearings.friction`, ν = 30 mm²/s). A machine with no bearings: loss
"unknown", never 0.

## 6. Turns and wire thickness

6.1 **DC copper:** exact analytically (R ∝ turns × mean turn length / (strand
area × parallel paths)), at the winding temperature (section 8).

6.2 **Torque, ψ, voltage:** exact through the NI grid (T(NI), ψ scales with
turns at the same NI); the audit shows law errors ≤ 1.2 % on torque at
N ×0.71…×1.5.

6.3 **AC copper — hybrid method.**
- From each loss-grid transient store the **slot leakage + rotor-harmonic
  field per conductor position per unit NI** (B_x, B_y at each wire row
  centre, complex amplitudes per harmonic). It depends on NI, rotor position
  and saturation — not on how NI is split into turns.
- For any (N, h): number of layers m = N rows (single column; per column if
  split), strip of fixed width w and height h, ξ = h/δ(f_k, T). Per harmonic
  k, the loss in a rectangular strip in a uniform transverse field is the
  Dowell/Ferreira formula: P = (w·L·ρ/δ)·[F(ξ)·|I_k|²/(w²) … + G(ξ)·|H_k|²]
  (skin term F, proximity term G), summed over rows with each row's own H_k
  from the stored field map.
- **Where it breaks:** parallel strands in hand with circulating currents
  (strand bonding "parallel" and "series" modes — the loss then depends on the
  transposition, which the solver's strand-bonding model handles and the
  formula does not); h ≳ δ (ξ > ~1.5: field inside the strip no longer uniform
  — the formula remains, the field map interpolation does not); rows moved
  into a different field region by the knob (the audit: the 8th row on the
  Ø40 sits nearer the slot opening, AC copper ×3.6 where N·h³ predicts ×1.14 —
  the per-row field map captures exactly this, provided the grid machine's
  map covers that row position: store the field over the WHOLE slot height,
  not only the occupied rows); deep saturation (field per NI not linear).
- **Consistency first.** The two AC routes still disagree: Ø40 rated coupled
  3.88 W vs analytic-slab 4.03 W (+4 %), **L155 rated coupled 554 W vs
  analytic-slab 897 W (+62 %)** (audit runs `m40_base_duty`, `l155_base_duty`
  vs `*_base`); the 2× of 2026-09-10 on L180 is reduced but not gone. Before
  anything is fitted, the coupled route must be proven converged on L155
  (strand mesh one element across a 1 mm strip; refine ×2, ×4) and the
  analytic route retired or recalibrated.
- **Validation matrix:** h ∈ {0.7, 1, 1.3}·h0 × N ∈ {0.67, 1, 1.5}·N0 (slot
  permitting) × 2 speeds at rated torque (constant NI); a 6–8-run subset with
  resolved strands. Targets AC copper ≤ 10 %, total loss ≤ 5 %. Audit subset
  on the Ø40 (coupled, strands resolved, constant NI):

| point (h mm, N rows, I A, rpm) | FEM AC copper [W] | tuner AC [W] | AC error | FEM total [W] | tuner total | total error |
|---|---:|---:|---:|---:|---:|---:|
| 0.6, 7, 40.7, 13 000 (base) | 3.88 | 3.63 | −7 % | 74.3 | 74.2 | −0.2 % |
| 0.6, 7, 40.7, 19 500 | 8.49 | 8.17 | −4 % | 90.9 | 89.3 | −1.8 % |
| 0.42, 7, 40.7, 13 000 | 0.62 | 1.24 | +102 % | 96.1 | 96.8 | +0.7 % |
| 0.74, 7, 40.7, 13 000 | 26.7 | 6.81 | −74 % | 86.2 | 66.3 | −23 % |
| 0.74, 7, 40.7, 19 500 | 57.7 | 15.3 | −73 % | 129.3 | 85.4 | −34 % |
| 0.6, 5, 56.9, 13 000 | 0.99 | 2.59 | +161 % | 94.8 | 96.5 | +1.7 % |
| 0.6, 8, 35.6, 13 000 | 14.4 | 4.15 | −71 % | 77.6 | 67.4 | −13 % |
| 0.6, 8, 35.6, 19 500 | 31.2 | 9.33 | −70 % | 106.4 | 83.2 | −22 % |

(coupled strand eddy, duty mesh 1 mm, 36 steps, demag on; the tuner is today's
N·h³·L proximity law on the passport's analytic cuAC grid, end-winding bug
patched.) The AC copper jumps ×7 (h 0.6 → 0.74) and ×3.7 (7 → 8 rows) when the
winding stack reaches the top of the slot (Ø40: 5.98 mm usable; 7 × 0.84 =
5.88 mm, 8 × 0.70 = 5.60 mm), where the rotor/slot-opening field is strong —
exactly what a per-row field map captures and a global N·h³ law cannot.
Where the winding stays low in the slot the relative AC error is large but
the watts are small (total within 2 %).

6.4 **Rotor-side losses** (magnets, sleeve, shaft) depend on the stator MMF
(NI) and the rotor field, not on N at the same NI — EXCEPT through the PWM
ripple current (I_ripple ∝ V_bus/(f_sw·L), L ∝ turns²), which pwmDeltas
already scales. FEM pair (audit): N = 5 rows at the SAME NI (I = 56.9 A vs 7 rows at 40.66 A), Ø40, 13 000 rpm,
post-processed rotor eddy: magnet −0.22 %, shaft −3 % (of 1 mW), rotor iron
0.0 %, stator iron +1.2 %, torque −0.9 % — rotor-side losses are unchanged
with N at constant NI, as stated.

## 7. PWM

Two carriers of the controller class at the rated point + the two rpm
neighbours at the reference carrier; resolution-matched sine baselines;
ripple current measured as √(I_rms² − I1²) on winding currents (as
`passport_pwm.py` today). Scaling [G18 — FIXED in PR #81 for the tuner]: the
current-ripple ratio rr = (V_bus/V_bus0)(f_sw0/f_sw)/(L/L0) is NOT the field
ripple when the turns change: the field-driven deltas (magnet, iron, torque
ripple) scale with the AMPERE-TURN ripple ratio a·rr, a = fN·fConn (at fixed
NI, 2× turns → L ×4, current ripple ×1/4, NI ripple ×1/2, quadratic field loss
×1/4 — v1's rr² said ×1/16). Copper HF loss is a spectral conductor-impedance
problem, not ∝ R_dc (the tuner keeps I_ripple²·R as a flagged first-order
stand-in). L for the ripple is the DIFFERENTIAL inductance at the operating
point [P20], not the frozen-permeability secant `ldq0`. [G19] The two carriers
change amplitude and frequency together: the fitted slope is a local carrier
interpolant, valid only in a validated neighbourhood, not a transferable
amplitude exponent for bus/turns. Record effective carrier and modulation,
the actual solved id/iq, the DC component and the harmonic spectra; match the
sine baseline to the PWM run's SOLVED fundamental vector, temperature and
state, and report the residual mismatch; judge samples per carrier as well as
the pulse ratio (≥ 16); do not add p-p ripple increments without phase
justification. Add one current row (0.5·I0) and one FW point.

## 8. Temperature

8.1 **Magnet temperature points.**
- **HOT** = the design magnet temperature from the duty or the coupled thermal
  result; the main static grid (3.1) and the loss grid (4) run here.
- **COLD = 20 °C, mandatory**: no-load (ψ_PM, EMF/KV, cogging with
  `cogging_quality` sampling), the MTPA line at 4 current levels
  (3 γ points each), and the short-circuit/characteristic current
  (i_d where ψd = 0 on the d-axis: 3 static points).
- Optional MID temperature (e.g. (hot+20)/2) at 2 levels to prove linearity.
- Fit k_ψ(T) = ψ(T)/ψ(T_hot) on no-load and on the MTPA line per level, and
  the torque factor k_T(T, I). Plain Br(T) scaling is wrong where the iron is
  saturated (a colder, stronger magnet saturates the teeth: k_T(I) < k_Br at
  high current) and near the demag knee (retention changes with T) — hence
  measured per level, not assumed.

8.2 **What the cold run is for [P22 — corrected]:** cold magnets give the MAX
EMF and therefore the MIN Kv. (a) The cold-magnet BUS-CROSSING speed
= cold no-load Kv × V_min (report convention `report.py:8142–8152`, now
labelled "cold-magnet bus-crossing speed" in PR #81) — a threshold above
which an uncontrolled machine drives current into the pack, NOT a mechanical
runaway; (b) credible mechanical overspeed is a separate calculation with the
drive and prime-mover conditions (hot/damaged flux, max bus, FW, load); (c)
cold waveform overvoltage at n_max from the resolved line-line waveform peak;
(d) max Kt cold; (e) steady short-circuit current from BOTH dq equations with
R (ψd = 0 is only the characteristic current), a transient peak only after a
dynamic analysis; (f) the cold voltage-limit / FW boundary, which needs cold
negative-id / FW check points, not only the cold MTPA line [P21]. Torque
factors go on Kt/Km, flux factors on Kv, each exactly once (fixed in PR #81:
`routes/coupled.py` cold block uses k_T when measured, k_flux as a declared
stand-in otherwise).

8.3 **Demag vs temperature:** the hot knee is binding for NdFeB; the static
grid's retention column is at the hot temperature; the current limit is the
hot knee. The worst-element averaged-demag rule stays.

8.4 **Copper:** R(T) = R20·(1 + α_Cu(T − 20)), analytic. Configure closes the
loop: losses → lumped thermal (cooling mode) → T_winding, T_magnet →
R(T), ψ(T) → losses; fixed-point iteration, typically 3–5 iterations to
< 1 K; divergence (thermal runaway) is reported as such.

8.5 **Other temperature effects:** iron loss vs T — neglected (< 2 % over
20–150 °C for the SiFe grades, stated); magnet conductivity σ(T) — applied
(magnet eddy ∝ σ for resistance-limited eddy, ~−0.1 %/K); sleeve/shaft σ(T)
applied the same way.

8.6 **Existing code:** cold constants block `routes/coupled.py:1321-1500`
(`_cold_constants`, `COLD_CONSTANTS_C = 20`, KV walked by the card's
reversible coefficient because `noload_psi_pm` is at the card temperature);
cold card lookup `report._cold_br_card` (`report.py:5967`); magnet card
temperature model `materials.MagnetMaterial.at_temperature`
(`materials.py:355`, Br and H_knee via α, β on the intrinsic curve); the
solver's `magnet_temp_c` argument (`routes/simulation.py`, get_fem_transient).

8.7 **Motor constants are rated COLD** (20 °C magnets and winding), from the
mandatory cold run: Kv [rpm/V, no-load, per LINE peak volt] ÷ k_3d;
Kt [N·m/A RMS] at a LOW current (0.25·I0, the linear limit) AND at the
rated current (with saturation), ×k_T — in delta per LINE amp; Km =
T/√P_cu,DC at 20 °C; ψ_PM [Wb, phase peak]; R [Ω, phase and line-to-line,
20 °C]; Ld/Lq [mH, bench small-signal at I≈0 and chord at rated]. Hot values
shown separately (Kt hot at the rated point). Conventions per memory
`report-conventions-2026-09-11`.

## 9. Validation and envelopes

- Static grid: 8 off-grid check points per machine (4 near the MTPA ridge,
  2 in deep FW, 2 at the highest current). Pass: ψ ≤ 0.5 %, T ≤ 1 %, ripple
  per section 1.
- Loss grid: 3 off-grid (n, I) points. Pass: total ≤ 5 %.
- Knob envelopes [P32]: NOT the hull of passed corners (interior failures
  exist — saturation, the slot-top rows, FW). Validate local cells /
  trajectories with interior checks and per-quantity bounds; one confirmed
  point extends only its supported neighbourhood. Audit baseline of TODAY's tuner (full table in the
  audit report): torque ≤ 1.2 % over L 0.5–2×, N 0.71–1.5×, h ±30 %, n
  0.5–1.5×, I 0.5–1.3× (both machines); total loss within 5 % ONLY for
  n 0.5–1.5× and I 0.5–1.3× on the Ø40; nowhere on L155 (delta AC bug).
- ONE outside-domain policy [P32]: no guaranteed number; optionally a
  clearly labelled estimate with the "extrapolated — needs FEM" badge and the
  reason, plus "Request calculation" carrying the exact immutable request
  inputs and baseline.

## 10. User workflow without FEM rights

(a) warn: which quantity is untrustworthy and why; (b) "Request calculation of
this point": machine signatures, knob values, operating point, user, reason →
an **FEM request record**; (c) Admin tab approve/decline (or auto-approve within
a per-user quota); approved → the job queue (Priority.DUTY by day, night window
if heavy); result stored and keyed by (passport signatures, knobs, point) so it
is never recomputed; in-app notice + optional e-mail; (d) catalog machine: the
confirmed point extends that machine's validated envelope for everyone; own
draft: private to the workspace; (e) MCP without `simulate`: answer
`status: needs_fem_request` + a `request_calculation` tool.

Reusable today: the job queue with priorities and owner (`src/motor_ai_sim/
jobs.py:136`), per-user daily quota (`agent_designs.check_quota`,
`agent_keys.take_quota`), MCP scopes (`mcp_app.py:48-67`, `simulate` scope),
the Admin inbox pattern (`support_store` + `web/src/components/admin/
VisitorRequests.tsx`), e-mail (`notify.py`, SMTP 587, optional), agent drafts
and their sandbox solve (`agent_designs.build_sandbox_config`, `simulate`).
New: an "FEM rights" flag per user (roles today are anon/user/admin, motor
grants per die — `auth.py:67`, `users.py:577-596`), the request store +
Admin section, the result cache keyed by signatures, the envelope-extension
write-back, the MCP tool.

## 11. Compute budget per machine

Measured (4 threads, nice 19, production image; audit 2026-09-30) and
derived. "6 × 1-thread" assumes six independent single-thread processes
(measured: a single-thread coupled-eddy transient on the Ø40 took 79.0 s vs 91.6 s on 4 threads, so per-point cost is taken as equal to the 4-thread value, ×1.5 as the pessimistic bound for L155; each process pays its one-time d-axis calibration, 33 s Ø40 / 48 s L155).

| Block | Points | Ø40 serial (4 thr) | L155 serial (4 thr) | Ø40 6×1-thr | L155 6×1-thr | Night? |
|---|---:|---:|---:|---:|---:|---|
| Static grid hot (12 positions) | 51 + 8 checks | 59 × 6.3 s = 6 min | 59 × 12.5 s = 12 min | 1–1.6 min | 2–3 min | no |
| Static cold set | 4×3 MTPA + 3 SC + no-load | ~16 × 6.3 s + cogging ≈ 3 min | ≈ 5 min | | | no |
| Loss grid (settled coupled eddy) | 15 + 3 checks | 18 × 91.6 s = 27 min | 18 × 678 s = 3.4 h | 4–5 min | 34–51 min | L155 yes |
| PWM quick | 3 seeds + 4 PWM + 4 baselines | estimate 20–40 min | estimate 2–4 h | | | yes |
| 3-D Stage A, 4 lengths | 4 | 30–60 min (catalog.py comment) | 1–3 h (estimate) | — | — | yes |
| AC validation subset | 6–8 | 8 × 92–240 s ≈ 20 min | 8 × 680 s ≈ 1.5 h | | | L155 yes |
| MTPA-only variant (comparison) | 21–28 | 3 min | 6 min | | | no |

Today's passport for comparison: 3 base + 4–9 current + 18 loss-grid
transients + PWM; audit timing of the coarse L155 variant 587 s (4 thr).

## 12. Differences from today and migration

| Today | Passport v1 |
|---|---|
| `passport.py`: one γ, current sweep in I, loss grid 6×3 un-settled, post-processed rotor eddy, analytic AC, fixed k_end at 1.5 L | ψ-map in (i_d, i_q) over 7 NI levels with MTPA + FW, settled coupled loss grid 5×3 at the operating angle, hybrid AC, analytic end windings |
| `motorScaling.ts`: T(NI) at fixed γ, EMF sampled peak × laws, no voltage limit, k_flux on torque, N·h³ proximity, no mechanical losses, clamp-less extrapolation of losses | T/ψ from the map, V from ψ, MTPA/FW with refusal, k_T for torque, per-row AC, bearings + windage, envelopes with refusal |
| `agent_designs.start_design`: duty headline, linear laws, wire height rescaled with turns, no losses | the same engine as the tuner (one TS/Python twin with a shared test vector), FEM request path |
| no fingerprint; four name/section matching rules | signatures (section 2) |

Migration steps (each with its verification):
1. **M1 pilot Ø40 CIANO14 40 new / L12**: generate v1 (hot + cold static,
   loss grid, AC subset) by day (all Ø40 runs are minutes); compare against the
   audit corners; targets of section 1.
2. M2 signatures + reproducibility stamp in the passport record; stale badge.
3. M3 tuner v1 (ψ-map reader, voltage-limit map, refusal, mechanical losses,
   cold constants) behind a flag, with a Python twin and a shared test vector.
4. M4 FEM-request workflow (section 10).
5. M5 computation baseline decisions B2, B3, B4, B7, B8 closed.
6. M6 global recompute (night, one machine per night for the Ø200 class).
7. M7 start_design on the same engine; retire the old passport and the four
   matching rules.

## 13. Open questions for the reviewer

1. (i_d, i_q) Clough–Tocher vs a regular bicubic resample: C¹ continuity of
   the MTPA derivative matters for the ridge search — is a monotone scheme
   needed near the demag knee?
2. The voltage-limit margin m_max (0.95?) and whether the guaranteed map uses
   v_min with the cold magnet (proposed) or v_nom.
3. The Dowell/Ferreira per-row formulation: is a 1-D uniform-field-per-row
   model adequate for the Ø200 1 × 9 mm strip (w/h = 9, ξ up to ~1.5 at 20 kHz
   harmonics), or must the 2-D strip field be kept?
4. The demag one-magnet window (3.0.2): is 60° + one slotting cycle enough on
   fractional-slot machines with 2 permeance classes of magnets?
5. Settle rule (3.0.1): is 0.5 % total-loss change over two periods tight
   enough to keep the final total within 5 % for slow modes with τ ≈ 20
   periods (the L155 shaft)? (Answer depends on the group's share — see rule 4.)
6. 3-D: interpolate k(L) in 1/L — adequate for the 12 mm Ø40 where the end
   region is a large fraction of the stack?
7. Whether AC copper at FW angles needs its own loss-grid points (the slot
   field changes with i_d).


## 14. Disposition of the independent review (2026-09-30)

Legend: **fixed** = in code (PR #81) or in this text with the change named;
**planned** = specified here, implementation step named; **owner** = needs an
owner decision; **evidence** = needs runs before it can close.

| ID | Topic | Disposition |
|---|---|---|
| P01 | torque method claim, raw-Maxwell AC, certification | **fixed** (text, B1: method ids stored, no silent fallback) · **evidence**: independent mean-torque and loaded-ripple convergence (M1) |
| P02 | dq self-check not independent; mean of products | **fixed** (text, B1/3.6: frame check only) · **planned**: keep angle waveforms / complex torque harmonics; average instantaneous products under ripple (M1) |
| P03 | peak dq convention, delta mapping, triplen | **fixed** (Conventions) · **planned**: `convention_rev` field (M0) |
| P04 | grid coverage, MTPA bracketing, fit shape | **planned** (M1): zero/low-current and axis anchors at hot AND cold; explicit quadrant bounds; bracket MTPA until T falls on both sides and confirm the fitted vertex; refine cells from withheld centres/edges; Jacobian symmetry / passivity check; no smoothing across demag boundaries |
| P05 | angular sampling, 13.4 % p-p, symmetry | **fixed** (text, B4, 3.2) · **evidence**: symmetry and 60° vs full-period comparison (M1) |
| P06 | settle rule does not bound the tail | **fixed** (text, 3.0.1 tail-bound rule) · **owner**: adopt the revised rule · **evidence**: replay of stored gauges |
| P07 | thermal-critical groups vs watt share | **fixed** (text, §1 table and 3.0.1 rule 3) |
| G08 | demag shortcut unproven | **fixed** (text: full pre-pass default; 3.0.2 = research) · **evidence** before enabling per topology |
| P09 | demag state/history in the map | **fixed** (text, B7) · **planned** (M1): state hash, (id, iq, T) safe surface |
| P10 | loss grid is a trajectory | **fixed** (text, §2 stale rules and §4) · **planned** (M1): store id/iq/T/state per point, targeted FW samples |
| G11 | f-law through FW, n_max units, zero anchors | **fixed** (text, §4) · **planned** (M3) |
| P12 | conditional 2-D scaling laws | **planned** (M3): implement ψ' = a·b·ψ0(a·i'), T' = b·T0(a·i'), L_diff' = a²·b·L_diff0(a·i'); validate conductor-distribution changes; audit's ≤ 1.2 % torque error is over the 1 % target — restrict or correct |
| P13 | k_flux vs k_T, consistent 3-D factors | **fixed** in PR #81 for the cold constants (k_T when measured; stand-in declared) · **planned** (M5): consistent corrected co-energy / flux / differential-L; intermediate short lengths; load/FW dependence · **owner**: whether Kt keeps the k_flux stand-in until k_T is measured |
| P14 | end-winding factor of two | **fixed** (PR #81 + §5.3) |
| G15 | magnet segmentation factor is relative, unvalidated | **planned** (M5): store raw 2-D watts, relative factor, a separately validated absolute end-return correction; 3-D eddy evidence for thermal-critical rotor losses |
| P16 | hybrid copper formulation incomplete | **planned** (M4): complete unit-consistent strip impedance with transport and external fields separated; fields by NI/angle with PM and armature parts, not a universal B/NI |
| G17 | 1-D strip validity, L155 reference | **evidence** (M4): both field orientations and slot-top positions vs resolved strands; settle the L155 554 vs 897 W AC copper first |
| G18 | PWM turns scaling 4× low | **fixed** (PR #81, tuner a·rr) and §7 |
| G19 | two-carrier fit transfer, baseline matching | **planned** (M6): §7 text; restrict fits to validated neighbourhoods, match to the solved fundamental |
| P20 | frozen secant L ≠ differential L | **planned** (M1): separate frozen decomposition, chord, true differential (tangent solve or symmetric perturbations with Br fixed) and terminal L; never reuse `ldq0` as differential |
| P21 | cold FW coverage, temperature models | **fixed** (text 8.2) · **planned** (M1): cold negative-id / FW checks; sourced temperature-dependent intrinsic curves; replace the card-temperature no-load probe by the actual cold no-load solve (`routes/coupled.py:1389`) |
| P22 | Kv/EMF wording, runaway, SC current, k_flux on Kt | **fixed** (PR #81: torque factor on Kt/Km, flux on Kv once; "bus-crossing speed" labels) and §8.2 · **planned**: mechanical overspeed and dynamic SC as separate calculations |
| G23 | thermal loop closure, σ(T) | **planned** (M3): heat-balance residual and stable-root check, damped / bracketed solve before declaring runaway; conductivity laws/tensors; bound the neglected iron-loss temperature effect |
| P24 | voltage margin, loss ledger | **owner**: controller margin from the actual controller/loaded pack (m = 0.95 is a placeholder) · **planned** (M3): signed loss ledger — conversion vs net torque, each loss subtracted once, motor/generator/no-load/stall accounting; unknown bearing loss → unknown shaft η |
| G25 | mechanical-loss inputs and validity | **planned** (M3): store support/load/preload/lubrication/seal/air/clearance/flow inputs; bound omitted pumping/drag; modes with supports and attached inertia per L |
| G26 | mesher parity is not convergence | **evidence** (B2/B3): L13 cogging/ripple convergence, c04, c10, c19 ripple outliers under the combined tolerance; c04's 0.025 W magnet eddy is not a blocker unless thermal-sensitive |
| G27 | skin-mesh spectral rule | **planned** (B3): conservative start in the material frame, bound omitted spectral loss, full layer depth/count/growth, tangential wavelength, residual per frame |
| P28 | fingerprint is not a pure resolved hash | **fixed** (text §2) · **planned** (M0) · partly **fixed** in PR #81 (star_delta forwarded to every passport sub-solve) |
| P29 | material change vs older baseline; identities | **fixed** (text §2) · **planned** (M0, before the pilot) |
| G30 | reproducibility record | **fixed** (text B9) · **planned** (M0) · full-precision storage **fixed** in PR #81 |
| G31 | integrated baseline, nine physics-baseline failures, budget | **owner/evidence**: freeze ONE integrated baseline and disposition the nine failures without re-pinning (M2); re-budget at 72/108 steps incl. 3-D k_T/k_L, cold FW, checks, retries; measure pilot cost before assuming a 6-worker speed-up |
| P32 | envelopes as hull | **fixed** (text §9) · **planned** (M7) |
| N33 | counts, interpolation definition, error allocation | **fixed** (text §1 floors) · **planned**: count every solve incl. vertex checks and retries; define the current interpolation; allocate mesh/time/settle/interpolation/model error inside the 1 % |

The three legacy bugs (review table): end-winding factor of two, delta AC
normalisation + missing star_delta forwarding + separate DC/AC watts, EMF via
the flux (ω·|ψ1|) with waveform peak and THD kept separately — **all fixed in
PR #81**, with the before/after FEM check in its description.

## 15. Gates (from the review)

**Before accepting the Ø40 pilot** (diagnostic runs may precede acceptance):
1. Freeze and hash pilot inputs and the actual baseline before any run; make
   connection, winding, mode, temperatures and part states explicit; record
   methods and full-precision data (P28–P29). → M0
2. Repair all three extraction bugs incl. the factor-two end length and the
   missing connection forwarding; keep separate fundamental / waveform
   voltages (P14 and the bug table). → **done, PR #81**
3. Define dq / terminal conventions and differential L; establish independent
   mean-torque and loaded-ripple evidence; verify the static window / angular
   sampling (P01–P05, P20).
4. Retain the full demag pre-pass and proven state / tail settling until
   replacements are qualified; define the fixed-retention map state and
   thermal-sensitive rotor convergence (P06–P09).
5. Validate hot / cold / FW / voltage / demag and loss trajectories for the
   exact knobs exposed, incl. the audited failing top-slot wire/turn corners;
   define shaft power accounting and local envelopes (P10, P12, P16–P17, P21,
   P24, P32).
6. Qualify 3-D and PWM corrections, or explicitly leave those blocks
   unavailable and restrict every dependent pilot claim (P13, G18–G19); the
   mandatory cold constants still need their own correct definitions.

**Before the global recompute (~20 machines):**
1. Close the applicable P findings on ONE integrated baseline; explain the nine
   physics-baseline failures without hiding changes; pin mesher / materials /
   settings.
2. Establish actual mesh / time / boundary convergence over the relevant skin
   and geometry regimes, incl. L13 cogging/ripple and c04 / c10 / c19; mesher
   parity alone is insufficient.
3. Qualify loss reuse across FW / bus / turns / L / T, large-strand hybrid
   copper, absolute rotor heating and segmentation; close source-data gaps in
   consequential magnet cards.
4. Qualify PWM spectral / MMF scaling, matched baselines, 3-D flux / torque /
   differential-L and thermal / mechanical constraints where material; an
   explicitly disabled unvalidated demag shortcut need not block.
5. Enforce immutable provenance, atomic promotion, versioned invalidation and
   the request / envelope rules; re-estimate compute at the actual resolution
   before scheduling the batch.

**Nice to have:** periodic / time-parallel acceleration; throughput
optimisation after the qualified baseline is measured; adaptive grids; extra
3-D lengths where existing checks already bound the error.

**Revised migration order:** M0 provenance snapshot + signatures + convention
and reproducibility fields (before any pilot run) → M1 Ø40 pilot (hot + cold
static map with anchors and FW, differential L, loss trajectory, independent
torque evidence) → M2 integrated baseline frozen, nine failures dispositioned
→ M3 tuner v1 (conditional laws, voltage map, loss ledger, thermal loop,
mechanical losses) → M4 hybrid AC copper after the AC reference is settled →
M5 3-D consistent factors + segmentation → M6 PWM requalification → M7
envelopes + FEM-request workflow → global recompute.
