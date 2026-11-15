# Own propeller calculation module: plan (stage 1, 2026-11-15)

Status: PLAN ONLY. No feature code. Written by Claude Sonnet 5.5 (US server session) for the coordinator's review.
Basis of every statement is marked: **[code]** read in this repository today, **[doc]** an existing repository document,
**[lit]** published literature or project pages from memory of the field (not re-fetched unless a link is given),
**[web]** looked up in this session (link given), **[est]** an estimate of ours with no measurement behind it.
Items marked *to confirm* must be checked against the primary source before anything is built on them.

## Decision of the owner (2026-11-15) and the resulting order

Owner, via the coordinator: «let us start with BEMT, and for developing our own propeller move to CFD». This plan is therefore
ordered as two tracks:

- **Track A (first): the BEMT module** - thrust, torque, axial and swirl velocity by radius, cooling air at the motor band,
  and a calibration hook for the thrust stand. Sections 1-4 and 6A.
- **Track B (second, later): CFD as the tool to DESIGN our own propeller** at rotating-blade level (geometry generation, mesh, solver
  on us1, optimisation loop). It is NOT a checker run next to every BEMT result. Section 5 and 6B.
- **What track A must already provide for track B** (the interfaces are fixed in track A so that B needs no rework): section 6C.

Reading order for the review: 6A (what is built first), 6C (the contract with B), then 6B. After the coordinator's review, stage A0 may start as PRs.
Wherever sections 1-5 below say "CFD attached later as a checker of the cooling flow", read it as superseded by this decision; the
actuator-disc cooling case stays described only as one possible later use of the track B tools.

## 0. What exists and where it is weak

**[code]** `src/motor_ai_sim/propeller.py`: catalogue of 18 propellers (`config/propellers/`; 11 `torque_measured`,
2 `thrust_measured_power_estimated`, 5 `geometry_only`, so 5 cannot be used at all); static C_T/C_P power law in rpm fitted to vendor
bench tables; air speed at the motor `v = factor * sqrt(2T/(rho*A))` (`cooling_air_speed_ms`, `position_factor`). The default
`behind_hub` is 0.4; `disc_plane` 0.5; `developed_wake` 1.0. The factor enters the thermal solver as ONE housing air speed
(`routes/thermal.py::_apply_air_speed_source`, `cooling_options.py`).

**[doc]** `docs/PROPELLER_S1_BENCH_2026-10-12.md`: for one installation (open frame, 10.5x5 3-blade) a factor of 0.53 +/- 0.04 was
fitted on two continuous bench runs; it rests on the passport losses being right (a 25 W error moves it to 0.84; 100 W: no
factor fits). So the one calibrated number we have is conditional, and a second installation (housed Ø85 frame) is still at the
unmeasured 0.4.

Weak points, in order of how much they can move a temperature:
1. One scalar for the whole motor; the real air at the motor is a radial profile with swirl, and it differs on the end face, on the
   hub side and in the gap of an open frame.
2. The factor is an installation property that nobody can predict from the propeller: it needs the flow in the hub region.
3. Static hover only: no climb, no forward flight (the drone use case spends most of its time there).
4. Only propellers with published tables; five catalogue entries are unusable.

## 1. Methods without CFD

Run times are for one operating point on one core **[est]** unless stated. "Accuracy" is what the literature supports for
small (Ø 0.2-0.5 m, Re 3e4-2e5) propellers; none of it has been reproduced by us yet, which is what section 4 is for.

| # | Method | Needs as input | Gives | Expected accuracy | Run time | Mature open-source code, licence |
|---|---|---|---|---|---|---|
| M0 | Momentum (actuator disc) - have | D, thrust (from table), rho | mean induced velocity, ideal wake | Thrust-to-power ideal; real propellers need figure of merit 0.5-0.75 for small props **[lit]**. Gives NO radial profile and no swirl | microseconds | in-house |
| M1 | Blade-element momentum (BEMT), tip and hub loss (Prandtl), high-induction correction (Buhl/Glauert), swirl included | radial stations: r, chord, twist, airfoil; polar cl/cd(alpha, Re, Mach); hub radius | thrust, torque, axial and tangential induced velocity per annulus at the disc, J-sweep (climb, forward axial flow) | Good at design/moderate loading **[lit]**; small-Re propellers are limited by the POLAR, not the method. Typical C_T/C_P errors of several to ~10-15 % at low Re unless polars are tuned or measured **[lit, est]** | ms | CCBlade (NREL, Apache-2.0 *to confirm*; Python+Fortran, designed for turbines, used for propellers); XROTOR (Drela/MIT, GPL *to confirm*; standalone binary); QProp (Drela, GPL-style *to confirm*; standalone); QBlade (v2 is under the Academic Public License per [web: qblade forum / Wikipedia](https://sourceforge.net/projects/qblade), older versions GPL: NOT usable in a closed product). **Recommendation: write our own ~400-line NumPy BEMT** (the algorithm is textbook, Ning 2014 residual form is robust) and use the open codes only as independent cross-checks run as separate programs |
| M2 | Lifting line / vortex lattice (steady, prescribed or relaxed wake) | full 3-D blade geometry incl. sweep, twist, chord | loading incl. 3-D effects, induced velocity off the disc plane | Better tip/sweep effects than M1; polars still needed for viscous drag | 0.1-10 s | OpenVSP/VSPAERO (NASA Open Source Agreement 1.3 *to confirm*; has a rotor/propeller mode), AVL (Drela, GPL, plates not rotors), PyVLM-type small codes; each must be run as a separate program |
| M3 | Free-wake vortex methods (lifting line or panel with a free helical wake) | as M2 + wake discretisation | wake geometry, contraction, velocity field in the near wake and behind the hub | Best non-CFD answer for the near-wake velocity field **[lit]**; sensitive to wake model constants; hover is the hardest case (wake collapse/instability) | 1 s - minutes | DUST (Politecnico di Milano; open-source, licence *to confirm*, check LGPL vs. other), OpenFAST/AeroDyn-FVW (Apache-2.0 *to confirm*), RotorPy-like research codes |
| M4 | Surrogate fits on public test data (e.g. C_T, C_P as functions of J and Re per geometry class: D, pitch/D, blades) | public databases | thrust/power for a prop with only D/pitch/blades known | Within the scatter of the data for props near the training set; no extrapolation to unusual geometry **[lit]**. Gives no velocity profile | microseconds | In-house fit. Data: see below |

**Data for M4 and for validating M1.**
- UIUC Propeller Data Site (Selig et al.): measured static and wind-tunnel data of many small propellers, citation form given on the
  site **[web: https://m-selig.ae.illinois.edu/props/propDB.html]**. I found NO explicit licence or terms-of-use text there: use
  requires asking the University (contact on the site) and citing; until answered, treat the data as "read for validation, not
  redistributed in the repository".
- APC performance files (apcprop.com): APC states the performance data are COMPUTER-GENERATED (vortex-theory-based estimates), not
  measurements **[web: https://www.apcprop.com/technical-information/file-downloads]**; their terms and conditions were not readable in
  this session. Consequence: APC files must never be used as "measured" ground truth; the measured APC data are the UIUC ones.
  Redistribution needs the APC terms read first (*to confirm*).
- Our own measured data: the catalogue's vendor tables (11 torque-measured entries) and the bench run `motres_bench_hq1050_3`.

**Free-software licence rule used in this plan.** A closed commercial product may call a GPL program as a SEPARATE process on a
server we control, but must not link it or copy its code into our modules; LGPL may be linked dynamically; Apache/MIT/BSD are free
to use with notices. Academic-licence software is out. Every candidate needs a one-line licence confirmation by the owner/coordinator
(legal) before a dependency is added.

## 2. What the use needs most, and where each method honestly stops

| Need (in priority order) | Delivered by | Where it is honest to stop |
|---|---|---|
| A. Axial and swirl velocity in the radial band of the motor right behind the hub (cooling) | M1 gives the induced axial and swirl velocity ONLY at the blade stations (r > hub/root radius). The motor body sits at r < about 0.15-0.3 R, where the blade carries little or no load: BEMT has no information there. M3 (free wake) gives a velocity field there but the hub/spinner/motor body blockage and the separated wake behind the hub are not in it. | Honest ceiling without CFD: the profile ABOVE the hub radius and the swirl (M1/M3), plus ONE calibrated scalar for the hub-shadow region (the position factor, now with a stated physical interpretation: ratio of the measured air speed at the motor to the BEMT wake speed at r = hub radius). Below that, only measurement (section 4) or CFD (section 5). |
| B. Thrust and torque for props without tables | M1 with a geometry (section 3) and Re-dependent polars; M4 for a quick check from D, pitch, blades | M1 results without a measured point on the same propeller are labelled "model, not measured" with the validation spread from section 4; never printed as a bench number. |
| C. Climb / forward-flight inflow | M1 with axial free-stream (J sweep); non-axial flow (cruise with propeller disc at an angle, side wind) is out of scope for M1 | Axial flow only. Inflow at an angle needs M3 or CFD. |

Why not simply keep M0: the momentum model cannot tell a 2-blade from a 3-blade, the pitch, or the radial profile, and cannot give
swirl; the same thrust gives the same cooling speed for any propeller of that diameter. The honest gain of M1 for cooling is
therefore (a) the swirl and radial profile outside the hub, (b) a physical anchor for the factor, (c) forward flight.

## 3. Geometry source and schema `propeller-2`

Chord/twist/airfoil sources, in order of trust:
1. **Measured** (3-D scan or caliper station measurements of our own propellers; the owner's team has the HQ1050-3 and the test
   propellers of the thrust stand). Needs a person; the CAD/scan tools are on the notebook, not on this server.
2. **Published geometry**: the UIUC site publishes blade geometry (chord and twist vs. r/R) for many APC and other small propellers
   *to confirm per propeller*; this is the source for the validation set.
3. **Vendor data**: T-Motor publishes diameter, pitch, blade count only **[doc: PROPELLER_CATALOG_2026-10-05.md]**; no chord/twist. So
   the 18 catalogue propellers have NO blade geometry today. Stage A2 therefore cannot validate on the catalogue until (1) is done for at least a few of them.
4. **Inferred** from D, pitch, blades with a stated generic planform (a class template). Allowed only with `estimated: true`, a
   method note, and the label "generic blade" in every output.

Schema `propeller-2` (a superset; `propeller-1` files stay valid and keep their exact behaviour):
```yaml
schema: propeller-2
# everything of propeller-1 unchanged, plus:
blade:
  geometry_source: measured | published | vendor | inferred
  source_ref: ...            # URL/file, access date
  estimated: false           # true for 'inferred'; then 'method' is required
  hub_radius_mm: ...
  root_cutout_mm: ...
  stations:                  # >= 8, increasing r
    - {r_mm: ..., chord_mm: ..., twist_deg: ..., airfoil: clarky | e387 | naca4412 | flatplate | ..., thickness_rel: ...}
  polars:
    source: neuralfoil | xfoil_table | measured | flatplate_model
    re_ref: ...
polars_dir: ...             # optional tables under config/propellers/_polars/
```
Loader rule: `propeller-1` -> exactly today's path; `propeller-2` without `blade` behaves as `propeller-1`; the BEMT is selectable only
when `blade` is complete. A missing number is never filled silently.

Polars: the weak link of M1 at Re 3e4-2e5. Options: NeuralFoil (MIT licence *to confirm*; surrogate trained on XFOIL data, gives cl/cd/Re
dependence) or pre-computed XFOIL tables (XFOIL is GPL: run offline, store the tables as data, which is outside the linking
question *to confirm with legal*), plus a Viterna post-stall extension. Measured section polars for these Re are scarce **[lit]**.

## 4. Validation plan (no fit and validation on the same points)

Principle: any parameter tuned (a polar correction, the position factor) is tuned on set T and judged on a DISJOINT set V; the report
prints both and the spread, and nothing is called "validated" without a stated V.

1. **Static C_T, C_P (M1 vs. measurement).**
   - V1 = catalogue props with `torque_measured` tables (11) once their blade geometry exists (section 3 route 1). Where a prop has
     several tables (different test motors), tune on one table and judge on another.
   - T/V2 = UIUC measured props with published geometry: tune the single global polar correction (if any) here, judge on V1.
   - Report: relative error of thrust and of torque vs. rpm per prop, mean and worst case. Acceptance numbers are a PROPOSAL for the
     coordinator (e.g. thrust and torque within 10 % at the design rpm), to be set after the first spread is seen; I give no
     accuracy figure for our code before that.
2. **Advance ratio / climb.** UIUC wind-tunnel J sweeps on props with published geometry (disjoint from V1). Same tuning rule.
3. **Wake velocity (cooling).** Only a measurement can judge it: the thrust stand being designed by the US thruststand session:
   anemometer behind the hub at 2-3 radial positions and axial distances + a thermocouple on the winding. Use: (a) measured speed
   vs. BEMT wake speed at r = hub radius gives the physical factor and its dependence on rpm/propeller; (b) the winding temperature
   vs. the thermal model gives the end-to-end check. Needs from that session: anemometer type and uncertainty, positions, rpm
   steps, air temperature and pressure log. **Not yet available; this plan assumes none of its numbers.**
4. **Independent cross-check of the code.** Run CCBlade or XROTOR (separate program) on 2-3 geometries and compare with our BEMT:
   checks the algorithm, not the physics.
5. **Regression of today's behaviour.** M0 results are pinned by test before any refactor (section 6A, stage A0).

## 5. CFD: the design tool of track B (second, later)

Per the owner's decision CFD is used to DESIGN our own propeller, not to check each run. The options below are ordered for that use:
the rotating-blade (MRF) case is the design workhorse, the actuator-disc case is only the cheap helper (and the one possible later
cooling-flow study).

Where it would run: on us1 through `motres-agent-job` (**[doc]** server agent rules: no network, job folder only, 1-16 cpus / 8-48 GB per
job, 25 of 32 threads for all agent jobs). Limitation: the job container is the DEPLOYED APPLICATION image, which has no CFD solver
and no pytest; a CFD image needs the owner's decision (new image, size, licence review). CFD is therefore a stage after a decision,
not a quiet addition.

Options:
- **Actuator disc / blade-element source in a steady RANS of the motor surroundings** (recommended first CFD step): the propeller is a
  momentum/force source on a disc; the motor body, housing, spinner and the hub shadow are meshed. OpenFOAM has actuation-disc and
  blade-element rotor-disc sources among its `fvOptions` (*to confirm names in the version used*); SU2 has an actuator-disc boundary
  condition. The BEMT thrust/torque distribution from M1 is the natural input. Gives: the air speed over the motor surfaces,
  directly usable as zone-wise film coefficients.
- **MRF (rotating zone) with resolved blades**: more physical near the hub, 3-D, much heavier.
- **Sliding-mesh unsteady**: research-grade, not for routine use.

Solver licences (closed-product rule above): OpenFOAM GPL-3 (external executable on the server, never linked: ok *to confirm*), SU2
LGPL-2.1 (*to confirm*), Code_Saturne GPL, PyFR BSD (GPU, needs a GPU we do not have on us1). Meshing: Gmsh (GPL; separate process) or
snappyHexMesh (part of OpenFOAM).

Effort and compute **[est, no measurement]**: axisymmetric (2-D wedge) actuator-disc case, 0.2-0.5 M cells: 15-60 min on 4 cpus; full 3-D
actuator-disc with the real motor housing, 3-8 M cells: 2-6 h on 16 cpus; MRF 3-D 10-20 M cells: 8-24 h on 16 cpus. Build effort: a
templated case generator + post-processor mapping to zone speeds, about 2-3 PRs plus a validation PR against the thrust-stand data;
the first working case in a week of focused work **[est]**, plus the CFD-image decision.

What CFD adds over M1-M3: the flow in the hub shadow and around the motor body (the exact region M1 cannot reach), per-surface air
speeds, effect of the housing/end caps and of the motor sitting inside or behind a duct. What it does NOT add: better thrust/torque
than M1 with good polars at these Reynolds numbers (RANS transition modelling is itself uncertain at Re < 1e5 **[lit]**). CFD also needs
the same thrust-stand measurement to be believed.

## 6. Stages (PR-sized), tests, UI, stored results

### 6A. Track A - BEMT module (first)

| Stage | Content | Tests |
|---|---|---|
| A0 | Introduce an interface `InflowModel` (input: propeller, rpm, rho, V_inf, radius band; output: axial/swirl speed per radius band, flags, model id). Momentum model re-implemented behind it, bit-identical. Model id `momentum-1` stamped in the result payload of `air_speed_for_thermal`. | Golden test: all 18 catalogue entries x 5 rpm give exactly today's numbers; existing propeller tests untouched |
| A1 | Schema `propeller-2` loader + validation; `propeller-1` untouched; docs in `config/propellers/README.md`. No new physics. | Loader tests: v1 file unchanged, v2 without `blade` = v1, bad station order/missing fields refused with a named field |
| A2 | BEMT kernel (`propeller_bemt.py`), flat-plate+Viterna polar provider, NeuralFoil-or-table provider behind the same interface; static thrust/torque/inflow profile. Not wired to the thermal solver yet. | Analytic limits (zero pitch, ideal-propeller momentum limit), convergence at high induction, symmetry, monotonic C_T(J); cross-check vs. CCBlade/XROTOR output stored as small fixtures |
| A3 | Validation report script + the first geometry set (UIUC published geometry after the terms question is answered; our scanned props when available). Writes a Markdown report with the spreads of section 4. | Script run on fixtures; report numbers are never asserted as "good", only recomputed |
| A4 | Forward flight / climb (J sweep) in the same kernel; API field `v_inf`. | Zero-V_inf equals stage A2; wind-tunnel fixtures V2 |
| A5 | Cooling inflow: radial profile and swirl at the motor band; mapping to the thermal solver as zone speeds (needs `routes/thermal.py` to accept more than one speed, which is a thermal-solver change and its own review) and the position factor re-defined relative to the BEMT hub-radius wake speed. Calibrated factor stored per installation with its basis. | Thermal golden tests unchanged when the model is `momentum-1`; new tests only for the new model |
| A6 | Thrust-stand calibration hook: loader for the stand's logs, factor fit with uncertainty, validation on held-out runs. Depends on the thruststand session. | Synthetic log round trip |


### 6C. What track A must already provide for track B (the contract)

Fixed in A so that B never needs a schema or kernel rewrite:
1. **Geometry schema = the CFD input.** `propeller-2` `blade.stations` (r, chord, twist, airfoil, thickness) plus hub radius, root
   cut-out, blade count and rotation sense are the COMPLETE parametric definition of a blade. A1 must also state the interpolation
   rule between stations (spline type, monotone twist/chord), because the CAD generator of track B must rebuild the same surface the
   BEMT saw. Add in A1 an optional `blade.sections` (airfoil coordinate files per station, referenced, not inlined) and `blade.pitch_axis`
   (the line the twist is applied about; default quarter chord); without them the CAD surface and the BEMT blade differ.
2. **Airfoil polars as data, with provenance.** A2 stores polars under `config/propellers/_polars/<airfoil>/<Re>.csv`
   (alpha, cl, cd, cm), each file stating source (NeuralFoil/XFOIL/measured), Re, Mach, transition setting, and its valid alpha range.
   The same airfoil coordinates feed the CFD sections, so one airfoil id means one shape in both tools.
3. **A design-variable set.** A2 publishes `design_variables(propeller) -> list[{name, lo, hi, unit}]`: the radial control points of
   chord and twist (a small number, e.g. 5-7 points each, with a smooth spline through them), airfoil choice per zone, hub radius,
   diameter, blade count. Bounds come from manufacturing limits (to be supplied) and are part of the geometry file, not of the optimiser.
   A candidate is always a valid `propeller-2` dictionary, so any tool can evaluate it.
4. **A cheap objective function.** A2/A4 expose `evaluate(propeller, rpm, rho, v_inf) -> {thrust, torque, power, efficiency,
   figure_of_merit, axial/swirl profile, flags}` with a stable, documented signature and a model id. In track B this is the
   pre-screening level: the optimiser explores with BEMT and only the finalists go to CFD (the multi-fidelity loop).
5. **A result record format** shared by BEMT and CFD: one JSON per evaluation with the geometry hash, operating point, tool and
   version, model id, outputs, and `estimated`/`validated_against` fields, so the two fidelities can be compared and the BEMT error
   against CFD (and against the thrust stand) is a recorded number, not an impression.
6. **Validation data as shared fixtures** (A3): the measured propellers become the CFD validation set for B1 (the CFD setup must first
   reproduce measured thrust and torque on props we can scan, before it is trusted on a new design).

### 6B. Track B - CFD for designing our own propeller (second, after A has run on real props)

Starts only when A3 has produced a validation spread and the owner/coordinator agrees. Sizes are PR-sized; compute figures are
**[est]** and must be replaced by measurements in B1.

| Stage | Content | Tests / acceptance |
|---|---|---|
| B0 (decision) | Choose solver and image: OpenFOAM (MRF / sliding mesh, GPL-3, separate process) or SU2 (LGPL-2.1); build a CFD job image for `motres-agent-job` (the deployed app image has no solver); licence check; Gmsh or snappyHexMesh for meshing. | Image builds; a trivial case runs through the launcher with no network |
| B1 | Reproduce measured data: from a `propeller-2` file generate the blade CAD (CadQuery lofted sections, same station rule as 6C-1), a periodic single-blade-passage mesh with MRF zone, boundary layer settings, solver case; run the static case of a propeller with measured thrust/torque (A3 set). Record mesh-independence (3 meshes) and the error vs. measurement. | Case generator unit tests (geometry hash round trip, mesh quality limits, blade volume vs. CAD); the first measured-vs-CFD table. No design use before this exists |
| B2 | Post-processing: thrust, torque, efficiency, and the velocity/swirl field behind the hub written into the shared result record; comparison with BEMT per case (the BEMT error becomes a number). | Parser tests on stored solver output; record schema test |
| B3 | Geometry generation from design variables (6C-3) with validity gates (no self-intersection, minimum thickness, manufacturing bounds); batch case creation. | Property tests over random valid/invalid variable vectors |
| B4 | Job orchestration on us1: many single-passage cases in parallel inside the 80 % CPU budget, restart/resume, failure classification (diverged, bad mesh), results written to `~/jobs/<tag>/` and collected by the coordinator; nothing written to the catalog from the server. | Dry-run of the orchestration with a stub solver; one real small batch |
| B5 | Optimisation loop: BEMT-screened candidates (thousands) -> CFD for finalists (tens); surrogate or CMA-ES over the design variables; objective and constraints set by the owner (e.g. efficiency at a rpm/thrust point, noise proxy tip speed, structural limit); every evaluation stored in the shared record format. | Loop test on analytic objectives; a reproducible small design run |
| B6 | Design hand-over: selected geometry to `propeller-2` (with `geometry_source: designed`), CFD and BEMT numbers attached with their basis, hook to the thrust-stand test of the manufactured prototype. | Schema test; prototype test plan |

Compute estimate for B **[est]**: one single-passage MRF case with 1-2 M cells, 30-90 min on 4 cpus; a design iteration of 20 finalists
therefore 10-30 cpu-hours, i.e. about an hour of wall time with the 25-thread budget. Full-annulus unsteady runs are not planned.
What B does NOT replace: the thrust-stand test of a real prototype, and the thermal model of the motor.

**UI** (rules of AGENTS.md): at most ONE short line in Configure, e.g. "Air model: momentum" / "Air model: blade-element", with the
details (inputs, validated spread, "generic blade" flag, extrapolation warning) in the tooltip. No caption lines, no ranges on screen;
new strings in EN and the zh-CN mirror.

**Stored results.** Nothing silent: (1) the default stays `momentum-1` until the owner decides otherwise; (2) every thermal result
carries the air model id and factor already or from stage A0 on (to be located in stage A0: where the result stamp is written); (3) a result
computed with an older model is shown as "computed with <model>", and a change of the model marks dependent results "needs re-run" instead
of rewriting them; (4) the bench-calibrated 0.53 stays attached to its installation (open frame, Ø40 die L12) and is not overwritten by any new
model; (5) no change of a stored die, configuration or catalogue number from this module; (6) geometry signatures and result stamps are not touched.

## 7. Open questions for the coordinator / owner
1. Licence confirmations (one line each): CCBlade, XROTOR, QProp, DUST, OpenVSP, NeuralFoil, OpenFOAM, SU2; and a written answer from the
   University of Illinois and from APC on use of their data files.
2. Who produces blade geometry for the catalogue propellers (scan on the notebook side)? Without it stage A2 can only be validated on UIUC props.
3. Is a CFD image on us1 acceptable (disk, licence review), and who owns it?
4. Whether the zone-wise air speed (stage A5) may change the thermal solver, or the module must keep delivering one scalar.
5. Acceptance thresholds for the BEMT (section 4) after the first spread is known.
6. Track B inputs the owner must give before B3/B5: the design target (thrust/rpm/diameter/blade count), manufacturing method and its limits (minimum thickness, material, tolerances), and whether noise or structural strength enters the objective.
7. Ownership of the measured propellers for scanning (A1/A3 and B1 both depend on them).
