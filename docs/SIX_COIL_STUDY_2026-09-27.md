# Six-coil study: H-bridge per coil vs one three-phase inverter, L155 (2026-09-27)

This is Controller module Stage 3, parts 2 and 3 (`docs/CONTROLLER_MODULE_2026-09-22.md` §8).
Machine: **CIANO10 200 opt / L155 motor**, duty **rated 1×9 mm**: 14 200 rpm,
562.1 A line, γ 15°, delta, 2P (the two coils of a phase in parallel,
162.25 A rms per coil). The work was done by an agent (claude-opus-5-5) while
the owner was away. The orchestrator made the decisions below.

## 0 · Answer first

| rated, equal copper loss, 750.4 V | T | ripple | motor loss | inverter loss (devices) | η wall→shaft |
|---|---|---|---|---|---|
| (a) one 3-phase inverter, sine | 187.94 Nm | 1.57 % | 6.20 kW | 4.15 kW (18) | **96.25 %** |
| (b) H-bridge per coil, sine, 60° phasing (= (a) in the FEM) | 187.94 Nm | 1.57 % | 6.20 kW | 4.41 kW (24) | 96.16 % |
| (c) H-bridge per coil + optimal 3rd (5.1 % @ 58°) | 188.47 Nm (+0.28 %) | 2.02 % | 6.23 kW | 4.41 kW (24) | 96.17 % |
| optimised waveform (5/7/11/13, no 3rd), H-bridge **or** 3-phase | 187.93 Nm | **0.21 %** | 6.20 kW | 4.41 / 4.15 kW | 96.16 / **96.25 %** |
| H-bridge, coil 1 open (others unchanged / × 1.2) | −16.5 % / −0.6 % | 41 % | — | — | UMP 0.7 / 0.9 kN mean |

**Recommendation.** On L155, an H-bridge per coil buys no efficiency or
torque. Its only exclusive lever, the 3rd harmonic, gives +0.28 % torque at
equal I²R and costs +27 W of motor loss (magnet loss +12 %), so η is
unchanged. The removed delta circulating current is worth 1 W. The one large
gain found, torque ripple cut 7× (1.57 % → 0.21 %) by 0.2–0.4 % of
5th/7th/11th/13th current, uses no zero-sequence harmonic, so **one
three-phase inverter can impose it just as well**. Its 11th/13th parts sit at
13–15 kHz, beyond a current loop at a 24 kHz carrier. With the 5th/7th
alone (static-fit waveform, not refined), ripple falls to 1.0 %.

Costed correctly for the 2P winding (each coil carries I/2), the H-bridge is
far cheaper than the Controller doc's §6 said: 4.41 kW with 24 devices, not
7.26 kW with 96. Still, with the same 24 devices a three-phase inverter loses
3.40 kW. **Build one three-phase inverter for L155, and put the
ripple-cancelling 5th/7th injection in its current controller.** The H-bridge
per coil is justified only by fault tolerance. With six bridges an open coil
leaves 83 % torque, or about 99 % with +20 % current, but at 41 % torque
ripple and up to 1.4 kN of UMP until a post-fault current set is optimised.
That optimisation is the one study left open here.

## 1 · Decisions (orchestrator, owner away)

* **Equal copper loss.** Every drive is compared at the same coil rms current
  (162.25 A per coil), so DC I²R is identical. A harmonic added to a coil
  takes its share out of the fundamental. AC copper loss is solved and
  reported, not held equal.
* **Same DC link: 750.4 V.** A three-phase SVPWM bridge can put at most V_dc
  peak line-to-line across a delta phase (linear up to m = 2/√3, PR #6). An
  H-bridge puts ±V_dc across its own coil. On this 2P winding a coil sees the
  full phase voltage, so both topologies have the same **750 V peak per coil**.
  The H-bridge gives no extra voltage here. What it gives is freedom of
  waveform.
* **γ stays at the duty's 15°.** The d-axis is fixed at the measured 120.014°
  for every run, so each run differs only in its current waveform.
* **FEM settings.** The optimiser ran on the fast "static" setting: no coupled
  strand eddy, no rotor eddy, no demag, 36 steps, ½ sector, about 75 s per
  run. The candidates were then solved on the duty's **production** setting:
  coupled strand eddy, rotor eddy, demag, 36 steps, ½ sector, about 12.5 min
  per run. The fault case and UMP need the full ring (§4). Everything ran
  solver-direct in a sandbox config at Normal priority, 6 BLAS threads,
  sequentially. The live API and config/ were not touched.

## 2 · Stage 2: per-coil currents in the unchanged solver

`src/motor_ai_sim/simulation/per_coil.py` provides an `ExcitationSource`
(`PerCoilCurrentSource`) for `fem_transient_sliding_band(excitation=...)`.
`fem_solver_2d.py` is not edited.

**Why three channels are enough.** The winding has 12 slots, 10 poles, single
layer, 6 coils. Written in each coil's own orientation, the coils sit at
**0/60/120/180/240/300° el.** (A+, B−, C+, A−, B+, C−). Coil k+3 is 180° el.
away from coil k and is wound the other way. So for any rotor-synchronous
waveform `f` with **only odd harmonics**, `−f(x+180°) = f(x)`: coil k+3 needs
exactly the current its partner already carries. The six coil currents
therefore fold onto the solver's A/B/C channels with no loss of generality:
`channel_X = f(θe + δ_X)`.

This includes the **triplens**, which are zero-sequence, so a three-wire
machine cannot impose them. The source checks the pairing on every call and
refuses even harmonics. Even harmonics would break the pairing, make no mean
torque against the odd-symmetric EMF, and only add loss and UMP.

**Open coil.** `open_coils_layout()` sets the open coil's slot direction to 0
in the resolved layout, which zeroes its source; its partner keeps driving the
channel. The solver's ψ map still sums the open coil's sides into its phase,
so on that run the flux-linkage torque and the phase voltage are not used.
Torque there is the air-gap Maxwell mean, scaled against the healthy
full-ring run. Copper loss is taken from the coil currents. The coupled
strand-eddy solve has to be off.

**UMP.** `gap_forces()` integrates Maxwell stress over the stator-side gap
ring. That is every stator element below the first iron or copper node,
65.45…65.80 mm, 2160 elements; its area matches the annulus within 0.16 %.
The field is the per-element B the solver already returns per frame
(`return_frames`), and the formula is the force version of Arkkio's torque:
`F = L/(r_o−r_i)·∫[(B·r̂)B − ½|B|² r̂]/μ0 dS`. Cross-check on the full ring:
the same integral with `r·B_r·B_θ` gives 185.75 / 187.04 Nm against the
solver's own Maxwell 185.80 / 187.07 Nm. The healthy-drive UMP is 3·10⁻⁴ N,
which is zero. A ½-sector model cannot carry a net force at all, so UMP is
computed on full-ring runs only.

### Verification of (a)

The same run was solved twice: once with the stock `drive=current` source and
once with `PerCoilCurrentSource` carrying a pure sine.

| setting | T stock | T per-coil | max \|ΔT(t)\| | max \|ΔV_A(t)\| | losses |
|---|---|---|---|---|---|
| static, 36 steps | 187.08163 Nm | 187.08163 Nm | 4.9·10⁻¹¹ Nm | 5.2·10⁻¹⁰ V | identical to all printed digits |
| production, 36 steps | 187.93906 Nm | 187.93906 Nm | 8.4·10⁻¹¹ Nm | 6.5·10⁻¹⁰ V | identical (P_mag 92.3 W, P_cu 2269.3 W, P_fe 1445.3 W) |

The per-coil path reproduces the standard run to floating-point round-off.
The production number also matches the stored ripple-study runs (187.934 Nm,
P_mag 92.31 W).

**(b) per-coil sine = (a).** With 60° per-coil phasing, an H-bridge sine
drive puts on every coil exactly the current the delta puts there. The FEM
cannot tell the two apart, and the run above is both. The delta's own
disadvantage, the circulating triplen current, is measured by the solver on
this machine at **9.6 A → 1 W** (L0 = 0.11 mH). Removing it is worth nothing.
The 0.2 kW figure in older notes is obsolete.

## 3 · Stage 3: waveform optimisation

**Variables.** Per-coil harmonics `z_h = a_h·e^{jψ_h}` (fraction of the
fundamental, phase in the harmonic's own cycles) for h = 3, 5, 7, 11, 13, at
equal coil rms.

**Optimiser.** A response surface built from FEM probes:

* **3rd.** Mean torque T = T0 + g·x + q|x|², from 4 probes (a = 0.10 at
  ψ = 0/90/180/270°). This gives the optimum x* = −g/2q in closed form.
* **5/7/11/13.** The 6th and 12th torque-ripple phasors are linear in z_h
  (R = R0 + αz + βz̄), from 2 probes each (a = 0.02 for the 5th/7th, 0.01 for
  the 11th/13th, ψ = 0/90°). A minimum-norm least-squares solve then gives the
  z_h that cancel R6 and R12.
* **Production refinement.** One Newton step on the production physics: the
  residual comes from the production run, the Jacobian from the static probes.

**FEM evaluations: 25 of the 30 allowed.** 13 static probes including the
centre, 4 static confirmations, 2 static 108-step checks, 2 static peak-duty
checks, and 4 production confirmations. The (a)/(b) reference runs are not
counted.

**Fit coefficients.** Static physics, T0 = 187.082 Nm.

* **3rd.** g = (5.32, 8.55) Nm per unit, q = −98.7 Nm per unit². Optimum:
  a3 = **5.10 %**, ψ3 = **58.1°**, predicted +0.257 Nm.
* **Ripple sensitivity.** |∂R6/∂z5| ≈ |∂R6/∂z7| ≈ |∂R12/∂z11| ≈ |∂R12/∂z13|
  ≈ 184 Nm per unit, which is ≈ T0: an h-th current harmonic beating against
  the fundamental EMF gives a torque harmonic of about T·a_h. So cancelling
  1.5 Nm of ripple takes less than 1 % harmonic current.
* **Linearity.** Checked at the unused 3rd-harmonic probes. The 12th is
  predicted within 3 %, the 6th within 20 % at a = 0.10, and the confirmation
  runs close the rest.

### Static physics (optimiser), rated, 36 steps

| case | waveform (a_h %, ψ_h °) | T (terminal work) | ΔT | ripple p-p | R6 | R12 | P_cu,ac | P_fe | V coil peak |
|---|---|---|---|---|---|---|---|---|---|
| (a) = (b) sine | — | 187.082 | — | 1.25 % | 0.977 | 0.659 | 1057.6 | 1437.9 | 424.2 V |
| (c) + 3rd | 3: 5.10 % @ 58.1° | 187.343 | **+0.14 %** | 1.21 % | 1.201 | 0.682 | 1080.9 | 1446.8 | 433.2 V |
| ripple, 5/7/11/13 | 5: 0.263 @ 228; 7: 0.264 @ 203; 11: 0.177 @ 136; 13: 0.177 @ 111 | 187.090 | +0.00 % | **0.07 %** | 0.000 | 0.000 | 1058.5 | 1438.1 | 424.3 V |
| ripple, 5/7 only | 5: 0.265 @ 229; 7: 0.264 @ 202 | 187.090 | +0.00 % | 0.61 % | 0.000 | 0.653 | 1058.3 | 1438.0 | 419.4 V |
| 3rd + ripple | all of the above, re-solved | 187.350 | +0.14 % | 0.12 % | 0.046 | 0.013 | 1082.5 | 1446.8 | 425.3 V |
| (a) sine, 108 steps | — | 187.082 | — | 1.46 % | 0.963 | 0.615 | | | |
| ripple, 108 steps | as above | 187.091 | — | **0.30 %** | 0.014 | 0.048 | | | |

DC copper loss is 1659.6 W in every row. The 108-step pair shows that the
36-step peak-to-peak under-reads, as the ripple study found. What is left
after cancellation is the 18th harmonic, 0.21 Nm, which only the 17th/19th
current harmonics (20–22 kHz) could reach.

**Peak duty (20 000 rpm, 770 A line), static.** Sine: 239.18 Nm, coil peak
**619 V**. With the same 3rd harmonic: 239.88 Nm (+0.29 %), coil peak
**653 V**, motor loss +117 W. The per-coil voltage limit of 750 V does not bind
at rated or at peak.

### Production physics (the duty's own settings), rated, 36 steps

| case | T | ΔT | T Maxwell | ripple p-p | R6 | R12 | P_cu (dc+ac) | P_fe | P_magnet | P_shaft+sleeve | motor loss (2-D) | V coil peak |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| (a) = (b) sine | 187.939 | — | 184.191 | 1.57 % | 1.476 | 0.683 | 2269.3 | 1445.3 | 92.3 | 15.2 | 3822 | 436.6 V |
| (c) + 3rd (static optimum) | **188.467**¹ | **+0.28 %** | 184.330 (+0.08 %) | 2.02 % | 1.939 | 0.699 | 2278.4 | 1452.1 | **103.5** | 15.0 | 3849 | 441.6 V |
| ripple, static optimum | 187.936 | −0.00 % | 184.197 | 0.72 % | 0.547 | 0.123 | 2270.1 | 1445.5 | 92.3 | 15.2 | 3823 | 436.3 V |
| ripple, 5/7 only (static) | 187.936 | −0.00 % | 184.197 | 1.02 % | 0.546 | 0.680 | 2270.0 | 1445.4 | 92.3 | 15.2 | 3823 | 432.3 V |
| **ripple, one Newton step** | 187.932 | −0.00 % | 184.199 | **0.21 %** | 0.002 | 0.002 | 2270.6 | 1445.5 | 92.3 | 15.2 | 3824 | 434.6 V |

¹ **The production torque mean needed a correction, and this matters beyond
this study.** On eddy/demag runs the solver reports the flux-linkage
*space-vector* mean, `(3/2)·p·n_par·⟨ψα iβ − ψβ iα⟩`. The Clarke α/β
components drop the zero-sequence term, so a triplen current's torque is
invisible to it: it printed 187.544 Nm (−0.21 %) for (c). Adding the omitted
term `3·n_par·⟨i0·dψ0/dt⟩/ω_m` gives 188.467 Nm (the zero-sequence share is
0.922 Nm). On the static runs, space-vector plus zero sequence reproduces the
solver's terminal-work mean to every printed digit, which validates the
correction. For a three-wire drive i0 = 0 and the reported mean is right; for
any per-coil drive with triplens it is not.

**The final optimised waveform** (production physics, Newton-refined), in
percent of the fundamental and the harmonic's own phase:
**5th 0.398 % @ 239.1°, 7th 0.399 % @ 213.2°, 11th 0.183 % @ 146.1°,
13th 0.182 % @ 122.1°, no 3rd.** It cuts torque ripple from 1.57 % to 0.21 %
at equal copper loss, +2 W of motor loss and unchanged mean torque. **None of
these harmonics is zero-sequence.** A single three-phase inverter can impose
exactly the same waveform; it is not a reason for an H-bridge per coil.

## 4 · Fault: one coil open (coil 1, A+)

All three runs are full ring, 36 steps, with rotor eddy on and coupled strand
eddy and demag off. The strand solve cannot run with an open coil, so the
healthy full-ring run uses the same settings and serves as the baseline.
Torque is the Maxwell mean (the ψ of the faulted phase is not usable, §2)
scaled to the production reference. The ring integral reproduces the
solver's Maxwell mean to 0.015 % on both fault runs.

| full ring | coils | T | ripple p-p | torque 2nd harmonic | UMP mean / max | UMP make-up | P_magnet | P_fe |
|---|---|---|---|---|---|---|---|---|
| healthy | 6 × 162.25 A | 187.94 Nm (ref.) | 1.25 % | 0 | **0 N** (3·10⁻⁴) | — | 78.8 W | 1437.8 W |
| coil 1 open, others unchanged | 5 × 162.25 A | 156.89 Nm (**−16.5 %**) | **41 %** | 32.6 Nm | **737 N / 1198 N** | 567 N fixed + 588 N at −2 f_e | 77.9 W | 1459.9 W |
| coil 1 open, others × 1.2 | 5 × 194.7 A | 186.79 Nm (−0.6 %) | **41 %** | 38.7 Nm | **889 N / 1443 N** | 685 N fixed + 706 N at −2 f_e | 102.1 W | 1472.4 W |

What an open coil does to this machine:

* **Mean torque.** It drops by 1/6 and comes back almost entirely with
  +20 % current in the other five coils. Copper loss rises by 1.2² · 5/6 =
  +20 %.
* **Torque ripple.** It is 41 % peak-to-peak, a 2nd-harmonic pulsation of
  33–39 Nm. Scaling the currents does not remove it. Removing it needs an
  unbalanced post-fault current set: different amplitudes and phases for the
  five coils, which only per-coil bridges can impose.
* **UMP.** 0.7–1.4 kN of net radial pull appears, a fixed part plus a part
  rotating backwards at 2 f_e (2 367 Hz at rated). The rotor weight is of the
  order of 0.1–0.2 kN, so this is a bearing and critical-speed load case.
  It is not in the bearing model (`bearings.py`, `_UMP_NOTE`).
* **Magnet loss.** It hardly changes uncompensated (−1 %). With compensation
  it rises by 30 %.

Optimising the post-fault current set was not in this brief's budget. The
machinery for it exists (`PerCoilCurrentSource(coil_scale=...)` plus
`gap_forces`). The next step would add per-coil amplitude and phase variables
for the five live coils, with objectives 2nd-harmonic torque → 0 and
UMP → minimum.

## 5 · Inverter side and the final comparison

### The 2P correction to §6 of the Controller doc

`inverter.losses.solve_controller` treats the coils as SERIES: for an
H-bridge it takes the coil current to be the phase current and the coil
voltage to be the phase voltage divided by coils per phase. **L155 is 2P.**
Each coil carries **half** the phase current (157 A, not 314 A) at the
**full** phase voltage.

The Controller doc's §6 table (H-bridge 4 × 24 = 96 devices, 7.26 kW,
η 97.40 %) therefore costed the H-bridge at twice its real current. In this
study the H-bridge is requested with `i_phase_rms_A = I_phase/2` at the same
AC power and power factor. The model's own `v_coil = P/(pf·3·i·coils)` then
comes out equal to the phase voltage, which is right for 2P. The code in
`inverter/` is not changed (another agent owns it). **Follow-up:** the route
or `solve_controller` should read the winding's `n_parallel` and do this
itself.

Settings: IMCQ120R004M2H, 750.4 V, 24 kHz, 0.5 µs, 18/0 V, R_G 2.3 Ω. N is
the smallest parallel count with T_j ≤ 150 °C. Default coldplate: water-glycol
50/50, 8 L/min, 65 °C, 40 channels.

| duty | topology | N | devices | conduction | 3rd quadrant | switching | total | T_j | η_inv |
|---|---|---|---|---|---|---|---|---|---|
| rated | one 3-phase inverter (delta) | 3 | 18 | 1930 W | 145 W | 2078 W | **4152 W** | 132 °C | 98.50 % |
| rated | H-bridge per coil, **series reading (as §6)** | 4 | 96 | 2046 W | 276 W | 4937 W | 7258 W | 140 °C | 97.40 % |
| rated | **H-bridge per coil, 2P (I/2)** | 1 | 24 | 1873 W | 160 W | 2376 W | **4409 W** | 128 °C | 98.41 % |
| rated | one 3-phase inverter, 24 devices (same silicon) | 4 | 24 | 1279 W | 134 W | 1985 W | **3398 W** | 114 °C | 98.77 % |
| peak | one 3-phase inverter (delta) | 5 | 30 | 2372 W | 192 W | 2945 W | **5510 W** | 138 °C | 98.76 % |
| peak | H-bridge per coil, series reading (as §6) | 3 | 72 | 5595 W | 440 W | 6932 W | 12967 W | 147 °C¹ | 97.13 % |
| peak | **H-bridge per coil, 2P (I/2)** | 2 | 48 | 1817 W | 205 W | 3324 W | **5346 W** | 127 °C | 98.80 % |
| peak | one 3-phase inverter, 48 devices (same silicon) | 8 | 48 | 1254 W | 173 W | 2791 W | **4218 W** | 114 °C | 99.05 % |

¹ Only on the doubled plate (80 channels, 16 L/min).

With the right coil current, the H-bridge per coil is **not** the expensive
topology §6 made it: at rated it costs +6 % inverter loss for +33 %
devices, and at peak it is 3 % *below* the three-phase inverter's minimum
sizing. Given the same silicon, though, the three-phase inverter wins again,
by 1.0 kW at rated and 1.1 kW at peak. Two things put it ahead. It has 6
switch positions to the H-bridge's 24, so each device's current-independent
switching energy is paid four times less often. And paralleled devices share
the current, which cuts conduction loss with the square.

### Final comparison, rated

Motor numbers are anchored to the duty's stored coupled record (266.0 kW
shaft, 272.2 kW AC, so 6.2 kW of motor loss including 3-D, bearings and
windage). The FEM supplies only the change in torque (flux-linkage, zero
sequence included) and in motor loss. Ripple is 36-step peak-to-peak
(108 steps reads about 1.2× higher for sine, §3). The fault rows are
approximate: their loss change comes from the full-ring runs without strand
eddy, and the inverter loss is 5/6 of the healthy one (the compensated row is
at 1.2× current).

| case | T | ΔT | ripple | motor loss | inverter loss | devices | T_j | η motor | **η wall→shaft** |
|---|---|---|---|---|---|---|---|---|---|
| (a) one 3-phase inverter, sine | 187.94 | — | 1.57 % | 6200 W | 4152 W | 18 | 132 °C | 97.72 % | **96.25 %** |
| (b) H-bridge per coil, sine | 187.94 | — | 1.57 % | 6200 W | 4409 W | 24 | 128 °C | 97.72 % | 96.16 % |
| (c) H-bridge per coil, sine + optimal 3rd | 188.47 | +0.28 % | 2.02 % | 6227 W | 4409 W | 24 | 128 °C | 97.72 % | 96.17 % |
| (d) H-bridge per coil, ripple-cancelling waveform | 187.93 | −0.00 % | **0.21 %** | 6202 W | 4409 W | 24 | 128 °C | 97.72 % | 96.16 % |
| (a′) one 3-phase inverter, the SAME ripple-cancelling waveform | 187.93 | −0.00 % | **0.21 %** | 6202 W | 4152 W | 18 | 132 °C | 97.72 % | **96.25 %** |
| (a″) one 3-phase inverter, sine, 24 devices | 187.94 | — | 1.57 % | 6200 W | 3398 W | 24 | 114 °C | 97.72 % | **96.52 %** |
| (f) H-bridge, coil 1 open, others unchanged | 156.89 | −16.5 % | 41 % | 5768 W | 3674 W | 24 | 128 °C | 97.47 % | 95.92 % |
| (f′) H-bridge, coil 1 open, others × 1.2 | 186.79 | −0.6 % | 41 % | 6801 W | 5572 W | 24 | 161 °C | 97.49 % | 95.53 % |

(f′) exceeds the 150 °C design target at N = 1. It stays under the 175 °C
datasheet limit, and N = 2 would fix it.

On the fault itself: with ONE three-phase inverter, any device failure stops
the whole drive. An open coil in a 2P phase leaves its parallel partner
carrying the whole phase current, twice its rating, unless the controller
derates. With six H-bridges a device fault removes one coil, and the machine
keeps 83 % of its torque at rated current, or about 99 % at +20 % current for
as long as the thermal margin allows. It does so with 41 % torque ripple and
up to 1.4 kN of UMP until a post-fault current set is optimised (§4).

## 6 · Scripts and reproduction

* `scripts/six_coil_study.py plan <plan.json> <workdir>` runs cases one by
  one, each in its own process with a sandbox config (it refuses to write the
  live config). `run <spec.json> <out.json>` runs one case. Every result keeps
  its series, the spec and the module path.
* `scripts/six_coil_fit.py <workdir>` is the Stage-3 response-surface fit and
  its candidates.
* `scripts/six_coil_inverter.py` is the inverter comparison, including the 2P
  correction.
* `tests/test_per_coil.py` covers the algebra only, no FEM: 60° phasing, pure
  sine = stock drive to 1e-9 A, triplen pairing, even harmonics refused, equal
  rms, open-coil layout, zero UMP for a symmetric gap field.
* The raw results (JSON per case, frames .npz for the full-ring runs) are in
  the agent's scratchpad (`sixcoil/`), not in the repository.
