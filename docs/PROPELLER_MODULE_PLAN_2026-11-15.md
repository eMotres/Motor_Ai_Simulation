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
  site **[web: https://m-selig.ae.illinois.edu/props/propDB.html]**. I found NO explicit licence or terms-of-use text there (only the
  footer "(c) 1994-2026 UIUC Applied Aerodynamics Group" and a requested citation; the only contact is the webmaster address on the page); use
  requires asking the University in writing and citing; until answered, treat the data as "read for validation, not
  redistributed in the repository".
- APC performance files (apcprop.com): web search results of other APC pages describe the performance data as computer generated
  (vortex-theory-based estimates), not measured; this was NOT found on the downloads page, the terms page or the contact page when
  those were read on 2026-11-15 (https://www.apcprop.com/technical-information/file-downloads, /terms-conditions/, /contact/), so the
  claim is unconfirmed and APC is asked in writing. Terms page (dated 2003, Landing Products Inc.): site content is copyrighted;
  download or print is allowed "solely for your own non-commercial use" or to order products; "any other use ... is strictly
  prohibited, unless authorized". It does not mention the data files by name. Consequence: until APC answers, APC files are not used
  as "measured" ground truth and are not used or redistributed in a commercial product; the measured APC data are the UIUC ones.
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

## 8. Own-propeller design tool (owner 2026-11-15): stages P1-P4, multi-objective from the start

Owner: start the software for our own 10 / 11 / 12 / 13 inch propeller (parametric model + a GUI that shows the optimisation
parameters), and: "not only thrust per unit power, but also more flow near the motor, to improve cooling". Runs after A2, on A1/A2.

**Objectives (two, never merged into one weighted number).**
1. Thrust per shaft watt at the motor's rated point (under its torque limit and a tip-speed limit).
2. Cooling air at the motor: axial speed (and swirl) averaged over the radius band the motor occupies behind the hub
   (`AirField.air_speed_ms` of `bemt-1`, A2). When the thermal link exists (A5) objective 2 becomes the film coefficient / winding
   temperature of the motor at its duty point; until then the band air speed itself is used and the screen says so.
The result is a **Pareto front** (non-dominated runs). The cost is stated as data: efficiency lost per m/s gained at the motor, read
off the front; the owner picks the point on the front.

**Design variables.** Besides the outer-blade set of 6C-3, the inner blade is a design-variable group of its own: hub radius, root
cut-out, root chord and twist (control points inside r/R < 0.3), hub / spinner shape. The root barely carries thrust but sets the flow
at the motor, so these variables are the main lever for objective 2.

**Honest limit, kept visible in every result.** The blade-element model is weakest exactly at the root (stall, hub blockage, no hub
flow: `band_inside_hub_not_modelled` / `band_partly_inside_hub` already flag this in `bemt-1`). Objective 2 is therefore a model value
that must be checked with the thrust-stand anemometer behind the hub (A6) and, later, with CFD (track B) before it is trusted. The flag
"root flow not validated" stays on the result, the front and the GUI until both checks exist; a design is never advertised by objective 2 alone.

**Stages.**
| Stage | Content | Tests |
|---|---|---|
| P1 | Parametric blade family 10 / 11 / 12 / 13 in (explicit scaling rule between diameters), variable groups outer / inner, bounds in the geometry file, every candidate a valid `propeller-2` dict; `evaluate()` returns both objectives, the profile with the motor band, constraints; operating points of the 40 mm motors (L12 6S, L20 12S) with torque / rpm limits read-only from the catalog | Candidate validity over random vectors, scaling rule checks, objectives against A2 values, bounds respected |
| P2 | Routes: list family, get / set variables of a draft, evaluate, sweep one variable; drafts in the user's workspace, never in the shared catalog | Route tests; draft isolation |
| P3 | GUI page: planform and chord / twist curves live from the variables, inputs with bounds, result tiles (thrust, torque, power, efficiency, air speed at the motor), curves versus rpm with the motor's torque limit, both objectives side by side, radial velocity profile with the motor band marked, Pareto front of the runs with pick-a-point, runs table (variables, objectives, constraints met); 3-D preview if the existing viewer is cheap to reuse; banner "BEMT, generic airfoil polars, not validated" | Front-end tests of the data shaping; EN + zh-CN key sets equal; no caption lines |
| P4 | Multi-objective optimiser (NSGA-II-type or scalarisation sweeps) over the variables with `bemt-1`; objectives / constraints selectable; every evaluation stored in the shared result record (6C-5) | Optimiser on analytic problems; reproducibility with a seed; the front is non-dominated |

**Inputs from the owner before P3 (not invented):** target thrust and rpm per diameter, blade count, material / manufacturing limits
(minimum thickness), folding or fixed hub and its interface to our motors.

## 9. Airfoil selection and manufacturability of the own propeller (owner 2026-11-15)

Owner: the optimal airfoil must be chosen, but it must be producible by a standard method in China. Added to the designer scope
(P1-P4): airfoil per radial zone is an optimisation variable, **manufacturability is a hard constraint**, and the optimiser works on the
airfoil AS MANUFACTURED. Every limit below carries its source; where only a mould shop can answer, the number is NOT invented but is
listed as a factory question (section 9.6).

### 9.1 Reynolds and Mach range (computed first) [calc]
Re = rho W c / mu, rho = 1.225 kg/m3, mu = 1.789e-5 Pa s (Sutherland, 15 C), W = omega r (static), c = chord. Placeholder planform
(NOT a design): c/R = 0.20 at r/R 0.25, 0.22 at 0.7, 0.12 at 0.95; rpm 3000-9000; Ø10 and Ø13 in.

| Ø in | rpm | r/R | W m/s | c mm | Re | Mach |
|---|---|---|---|---|---|---|
| 10 | 3000 | 0.25 | 10 | 25 | 1.7e4 | 0.03 |
| 10 | 6000 | 0.70 | 56 | 28 | 1.1e5 | 0.16 |
| 10 | 9000 | 0.95 | 114 | 15 | 1.2e5 | 0.33 |
| 13 | 3000 | 0.25 | 13 | 33 | 2.9e4 | 0.04 |
| 13 | 6000 | 0.70 | 73 | 36 | 1.8e5 | 0.21 |
| 13 | 9000 | 0.70 | 109 | 36 | 2.7e5 | 0.32 |
| 13 | 9000 | 0.95 | 148 | 20 | 2.0e5 | 0.43 |

So Re is about **2e4 - 3e5** over the whole family and Mach stays below about 0.45. Consequences: the root (Re 2e4-9e4) is in the regime where
laminar separation bubbles dominate (small rotors run at chord Re of 1e4-1e5 and the boundary layer separates even at low incidence;
sources: TU Delft propeller experiment https://research.tudelft.nl/files/113578113/1.J060611.pdf, Politecnico di Torino thesis
https://webthesis.biblio.polito.it/33289/1/tesi.pdf, RANS-on-LSB study in Drones 2024 https://doaj.org/article/f24d0180e2e449ce9daebfc9a2609cb6; all read
only as search summaries). Therefore polars MUST be Reynolds-dependent (TablePolar of A2 interpolates in log Re; the flat-plate polar is not
enough for airfoil choice) and a candidate whose polar is outside its tabulated Re range is flagged, not extrapolated silently.

### 9.2 Airfoil library
A library of candidate low-Reynolds airfoils with coordinates and polars at 3-4 Reynolds numbers in the range above. Families to consider
(public, designed for Re 1e4-5e5): Selig / Donovan / Fraser sections (S1223, S1210, S4083 ...), E387 / FX 63-137 type, thin cambered
plates (the classic small-rotor choice), Clark Y as a reference, and our own thickened-trailing-edge versions. Coordinates source:
the UIUC Airfoil Data Site (https://m-selig.ae.illinois.edu/ads.html, about 1650 airfoils, Selig format, "identify the source"). **Terms:**
no licence or terms-of-use statement was found for the site; individual airfoils belong to their designers and may carry their own terms
(search results only) - the same written question as for the propeller database (UIUC, already sent 2026-11-15) must cover the airfoil
coordinates, and each family used is recorded with its terms in the library file before it ships. A shape generated by our own
parametrisation (e.g. a few control points of camber and thickness) has no such issue and is the fallback. Polars: computed offline with
a panel/boundary-layer code at the listed Re (tool and licence to be chosen and recorded; XFOIL is GPL, used as a separate program offline
and only its output tables stored) and checked against published low-Re wind-tunnel polars where they exist. No polar is used without its
source and Re in the file header (A2 CSV format).

### 9.3 Production methods for this size (standard in China) and what each limits
Three routes, as named by the owner. General limits below are from public vendor/design guides (generic injection moulding, not
propeller-specific); the propeller-specific numbers are NOT available publicly in the sources found and are factory questions.

**(a) Injection-moulded fibre-filled polymer (glass- or carbon-filled nylon), steel mould.**
- Wall thickness: Protolabs gives 0.76-2.92 mm for nylon and 0.90-2.54 mm for long-fibre reinforced grades
  (https://www.protolabs.co.uk/resources/white-papers/designing-for-mouldability-volume-1/); ZetarMold suggests 2-3 mm and uniform walls for
  glass-filled nylon (https://zetarmold.com/?p=53961). A solid blade section of 10-13 in with 8-12 % thickness at the tip is far thinner than
  those general ranges at the tip; whether the shop can fill it is a factory question.
- Draft: sources differ - at least 0.5 deg (https://fathommfg.com/app/uploads/2025/12/Design-Guidelines-for-Injection-Molding.pdf), minimum 2 deg
  (https://facfox.com/docs/kb/injection-molding-design-tips), glass-filled nylon 1.5 deg minimum, preferably 2-3 deg (ZetarMold). A blade has
  both surfaces as the cavity faces, so draft is relative to the parting direction: limits the blade twist and the camber that can be released
  from a two-plate mould (see parting line).
- Glass-filled material shrinks anisotropically and warps (Protolabs, same paper): flow direction along the blade decides twist change
  after moulding. Tolerance standard: ISO 20457:2018 (replaces DIN 16742), classes depend on material and process; the standard also covers
  shrinkage anisotropy (https://www.dinmedia.de/en/standard/din-iso-20457/314652216) - the class the factory can guarantee is a factory question.
- Trailing-edge thickness, leading-edge radius: no usable number in public sources found; factory question.

**(b) Compression-moulded carbon-fibre prepreg in a matched two-part metal mould.**
- Process facts: prepreg compression moulding, reported cycle 8 min at 140 C and 8 MPa forming pressure; excess resin squeezes out at the
  parting line and a closely fitting tool gives the right fibre-to-resin ratio; near-net shape needs only flash removal
  (https://www.compositesworld.com:443/articles/prepreg-compression-molding-makes-its-commercial-debut).
- Draft 2-3 deg recommended for release and tool life (https://www.easycomposites.co.uk/learning/casting-a-two-part-compression-mould).
- A closed two-part tool cannot release undercuts: complex closed geometry needs a bladder or other process (same source) - for a
  propeller this means no undercut, a single planar or simple stepped parting line, and a limit on reflexed camber.
- Minimum trailing-edge thickness, minimum thickness ratio, fibre-ply drop-off at a thin edge, surface finish and chord/twist tolerance: no
  public numbers found for propeller blades; factory questions. Known propeller makers (e.g. Mejzlik with a compression-moulding line,
  per CompositesWorld) do this, which shows feasibility, not limits.

**(c) CNC-machined moulds** (aluminium or steel) for both routes. Limits come from the cutter: smallest ball-nose radius sets the
trailing-edge radius and the inner corner radii, tool reach limits deep thin edges, and polishing sets the surface finish. Numbers:
factory questions.

### 9.4 Manufacturability as hard constraints (design rule)
Constraints enter the optimiser as hard bounds on the airfoil variables and are applied BEFORE evaluation:
1. minimum trailing-edge thickness t_te,min (the airfoil is thickened to it; the polar is computed for the thickened shape);
2. minimum thickness ratio t/c along the blade and an absolute minimum thickness in mm (wall limit of the process);
3. minimum leading-edge radius;
4. draft against the parting direction and a planar / simple parting line: no undercut; twist the mould can release; limit on reflex camber
   and cusped trailing edges;
5. fillet / corner radii from the cutter;
6. structural minimum (9.5).
Values come from a per-process **manufacturing profile** in the geometry file (`blade.manufacturing`: process name, each limit with its source
and `confirmed_by`), empty until the factory answers; an unconfirmed limit makes the result carry the flag `manufacturing_limits_unconfirmed`
and the GUI shows it. No default number is shipped.

### 9.5 Robustness to tolerance, and the structural check
- **Tolerance robustness:** each candidate is re-evaluated with the shape perturbed inside the factory's tolerance (chord, twist, thickness,
  trailing edge thickened to the minimum) - at least the corner cases and a small sample; the reported performance is the worst case and
  the spread, not the ideal value. Tolerance values: factory question (ISO 20457 class or the shop's own).
- **Objective on the airfoil as manufactured:** the optimiser never sees the ideal coordinates.
- **Per-zone report in the GUI (Propellers page):** root / mid / tip: chosen airfoil, thickness ratio, trailing-edge thickness, which
  manufacturing limit is active, and the performance cost of each active limit (re-run with the limit relaxed in a what-if, never shown as a
  guarantee).
- **Structural hook:** centrifugal and bending stress at the root for the chosen material at maximum rpm, safety factor 3 (owner rule: mechanics
  always checked). Inputs: material properties of the chosen filled polymer / CFRP (supplier data sheet, direction-dependent), maximum rpm
  (runaway), mass distribution from the geometry. Computed by a beam model of the blade (centrifugal tension + bending from the BEMT loads
  with the lever from the section centroid) first; FEM later; failure of the check is a hard constraint. Fibre orientation in moulded parts
  makes the allowable stress uncertain: a factory/material question.

### 9.6 Factory questions (the owner is at the factory; short list)
1. Which process do you recommend for a propeller of 10-13 in: injection moulding of glass/carbon-filled nylon, compression moulding of carbon prepreg, or both? Minimum order quantity and tooling cost per process (for the decision only, no price talk with customers).
2. Minimum trailing-edge thickness you can mould reliably, for each process (mm).
3. Minimum blade thickness / wall at the tip and the minimum thickness ratio t/c along the blade; can fibre-filled material fill a 1 mm section over a 10 mm chord?
4. Minimum leading-edge radius.
5. Draft angle required; is a planar parting line along the chord enough, or must it follow the blade? How much twist (root to tip, degrees) and camber can the mould release without slides? Is reflex camber or a cusped trailing edge possible?
6. Surface finish achievable on the blade surface (Ra) and polishing options.
7. Typical tolerance on chord, twist angle, thickness and trailing-edge thickness; which ISO 20457 class do you guarantee; typical twist change after moulding (warpage) for glass-filled nylon.
8. Material: which filled grades do you use (glass %, carbon %), and the data sheet with strength and modulus along and across the flow direction; creep and humidity effects.
9. Can you mould the hub with a metal insert / what hub interfaces (bore, keyway, flange) are standard; folding versus fixed hub options.
10. Balancing: do you balance each propeller; what residual unbalance is typical.
11. Do you hold sample moulds / existing airfoil sections for propellers of this size that we may use (and under what terms)?
12. Lead time and cost of a CNC prototype mould for a first set of four diameters.

### 9.7 What changes in the stages
- A2 (done) polars are Re-dependent through `TablePolar`; the library and its terms are a new data stage (call it P0) before P1: airfoil library file format
  (`config/propellers/_airfoils/<family>/<name>.dat` + terms record), a manufacturing-profile schema, and the manufactured-shape function (thicken to
  t_te,min, enforce minimum thickness) with tests.
- P1 adds the per-zone airfoil variables and the manufacturing constraints; P4 optimises on the manufactured shape with the tolerance-robust objective
  and the structural constraint; P3 shows the per-zone limits and their cost.

### 9.8 Standard production route and blade count (owner request, checked 2026-11-15)
Owner: mass-produced props of this class use injection moulding of plastic, standard 10 in props look three-bladed; check, and compare 2 and 3 blades.
Sources are retailer pages, vendor pages and blogs (search summaries), not tests; no Chinese factory-specific source was found.
- **Process:** injection-moulded glass-fibre-reinforced nylon is the common material for 10 in multirotor props: HQProp MacroQuad 10x5x3 and 10x5.5x3
  (3 blades, glass-fibre-reinforced nylon, https://www.readymaderc.com/products/details/86483-hqprop-hq-macroquad-prop-10x5-5x3-1cw-1ccw-black-glass-fiber-reinforced-nylon),
  Gemfan 1051 folding in 2- or 3-blade versions (https://www.myfpvstore.com/nzwi), Gemfan 1050W-3 (reinforced glass-fibre nylon, 3 blades);
  most FPV props are polycarbonate, reinforced nylon or a blend (https://www.droneblog.com/2-blade-vs-3-blade-prop-drone-which-is-better/ and retailer guides).
  APC, a long-established maker, injection-moulds long-glass-fibre composite (fibres stay oriented along the span), mould halves milled on CNC machines, only the
  shaft hole is drilled afterwards (https://www.apcprop.com/technical-information/manufacturing/). A PA66-CF40 compound supplier markets its grade for
  propellers with blade thickness below 1 mm (vendor claim, https://www.carbonele.com/pa66-cf40-compounds-for-drone-propellers). So injection moulding with CNC-milled steel moulds
  is the standard route; compression-moulded carbon prepreg is the premium route (larger props, e.g. Mejzlik-type makers).
- **Blade count:** 3-blade 10 in props are common in the FPV / cinelifter class, but 2-blade versions of the same size exist (Gemfan 1051 both). Our own catalogue
  has both at 10 and 13 in: 3-blade T-Motor FPV 10x5, HQ1050-3 (bench propeller), FPV 13x10 and 13x12; 2-blade CF 10x3.3 and P13x4.4 (config/propellers).
  Claims on efficiency disagree and none comes from a controlled same-diameter test in the sources found: one trade source says three blades give the same
  or slightly better efficiency and about 10-15 % more thrust at the same diameter (circuitdigest.com article), another argues efficiency rises as blade count
  falls (droneblog.com); neither gives data. Treat both as unverified.
- **Consequence for the designer:** blade count is a design variable with the values {2, 3} (not a fixed choice). Fair comparison rule: compare 2 and 3 blades
  at EQUAL diameter and EQUAL thrust (or equal shaft power), each re-optimised (chord and twist), because simply adding a blade at the same chord changes the
  solidity. The comparison reports thrust per shaft watt, the air speed at the motor band, tip speed, root stress and the moulding constraints (a 3-blade mould is
  a bigger, more complex tool; the number of blades changes the hub). The 2-vs-3 result of the BEMT is a model value (generic polars, not validated) until the
  thrust-stand data exist; the stand should test one 2-blade and one 3-blade prop of the same diameter first.
