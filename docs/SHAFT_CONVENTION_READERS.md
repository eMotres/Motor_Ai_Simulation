# Shaft-power convention: reader inventory

PR #900: `shaft_convention == "fe_on_shaft_k3d_v1"`; compact records use `shaft_conv == 1`.
Verified 2026-11-26 by reading each of the original 109 no-marker paths and their producers/callers where needed.
The reasons below distinguish field torque, mechanical output, shaft eddy heat, independent passport models, and pass-throughs that actually preserve provenance.
This is a static reader audit, not a new validation of solver physics or a browser/FEM run.

Coverage is enforced by `tests/test_shaft_reader_inventory.py`: source files under `src` and `web/src`,
extensions `.py`, `.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`, `.cjs`; excludes `__tests__`, locales and test/spec files.
Search (including comments/types): `P_shaft_W|P_shaft_net_W|P_mech_W|T_shaft_Nm|T_em_avg_Nm|T_coupling_Nm|efficiency|torque_density|shaft_conv|torque_per_mass|power_per_mass|torque_to_mass|eta_shaft|eta_em`.
The table is the only file allowlist. New matching files require review and a reason; the guard does not prove correctness or detect every new path inside an existing file.
First matching line and number of matching lines are navigation aids; the marker column reports a direct marker/helper reference, not correctness by itself.

Original no-marker rows: 84 confirmed, 25 fixed; 15 previously absent files added (3 fixed). Total: 170 files. Original marker-aware rows retained, with touched paths updated.

| file:first line (matches) | reads the marker | disposition |
|---|---|---|
| `src/motor_ai_sim/agent_designs.py:1095` (12) | yes | Fixed in `src/motor_ai_sim/agent_designs.py:1156`: marked headline carries field-torque / pre-bearing power / coupling-efficiency bases; parent mechanical loss and included flag agree. |
| `src/motor_ai_sim/assistant_estimate.py:160` (2) | no | Confirmed: estimate() uses propeller demand and an explicitly assumed SYSTEM_EFFICIENCY; no saved EM summary input. |
| `src/motor_ai_sim/charge_analytic.py:244` (5) | no | Confirmed: machine_point() computes its own passport T*omega and loss model; charge_point consumes that analytic pair, never a marked summary. |
| `src/motor_ai_sim/cogging.py:308` (1) | no | Confirmed: cogging rated-torque reference uses solved field torque only for cogging percentage normalization, with source description; no shaft-power or efficiency projection. |
| `src/motor_ai_sim/configure_anchor.py:78` (1) | no | Confirmed: thermal anchor reads P_shaft_W as rotor eddy heat alongside magnet heat; it does not read mechanical shaft output. |
| `src/motor_ai_sim/contracts/_selfcheck.py:105` (3) | no | Confirmed: executable contract selfcheck uses a literal synthetic unmarked fixture and checks scalar/series round-trip; no saved run selection. |
| `src/motor_ai_sim/contracts/adapters.py:192` (6) | yes | Fixed in `src/motor_ai_sim/contracts/adapters.py:205`: standalone ScalarResults now carries marked convention and explicit field-torque / pre-bearing power and efficiency bases; raw summary remains intact. |
| `src/motor_ai_sim/contracts/conformance/drivetrain.py:104` (8) | no | Confirmed: conformance validates independent MotorPerformanceIR efficiency maps with mandatory basis, transmission losses and propeller efficiency, not EM summaries. |
| `src/motor_ai_sim/contracts/drivetrain_ir.py:8` (11) | no | Confirmed: EfficiencyMap requires its own basis; shaft/transmission interpolation consumes that independent IR, not summary P/T. |
| `src/motor_ai_sim/contracts/result_ir.py:31` (1) | no | Confirmed: ScalarResults allows extra metadata; adapter now attaches marked scalar bases without altering unmarked schema defaults. |
| `src/motor_ai_sim/coupled_continuous_rating.py:11` (24) | yes | yes |
| `src/motor_ai_sim/coupled_pwm_final.py:174` (17) | yes | yes |
| `src/motor_ai_sim/datasheet.py:73` (32) | yes | yes |
| `src/motor_ai_sim/design_agent/prompt.py:10` (2) | no | Confirmed: prompt text instructs agents to quote tool numbers; no numeric reader or conversion. |
| `src/motor_ai_sim/design_agent/tools.py:81` (9) | yes | Fixed in `src/motor_ai_sim/design_agent/tools.py:82`: list_motors and compact_fit retain the MCP duty/fit basis; propeller rows independently use S1 shaft/input power. |
| `src/motor_ai_sim/development/motor_view.py:182` (25) | yes | yes |
| `src/motor_ai_sim/development/motors.py:29` (32) | yes | Fixed in `src/motor_ai_sim/development/motors.py:763`: old primary summary/compact result cannot borrow marked coupled power, torque or efficiency; marked records use one coupling basis. |
| `src/motor_ai_sim/development/sim_view.py:106` (2) | yes | Fixed in `src/motor_ai_sim/development/sim_view.py:107`: _duty_row copies shaft_conv with efficiency to the point switcher; requested duty P/T remain targets. |
| `src/motor_ai_sim/development/spec_order.py:255` (1) | no | Confirmed: specification-entry instruction text only; no solved metric is projected or recomputed. |
| `src/motor_ai_sim/digest/collect.py:254` (1) | no | Confirmed: headline/result_line delegate whole kinds records to digest.readable, now basis-aware through R2. |
| `src/motor_ai_sim/digest/readable.py:118` (16) | yes | Fixed in `src/motor_ai_sim/digest/readable.py:136`: selected marked record supplies coupling power/torque/efficiency; motor and generator labels are bilingual; legacy missing EM efficiency does not borrow coupled efficiency. |
| `src/motor_ai_sim/duty_results.py:110` (10) | yes | yes |
| `src/motor_ai_sim/gears/gearbox.py:146` (39) | no | Confirmed: gearbox efficiencies come from signed gear-port powers, mesh losses or explicit catalogue assumptions; no marked EM-summary keys are consumed. |
| `src/motor_ai_sim/gears/loads.py:113` (1) | no | Confirmed: mechanical stage result dataclass describes power_in/out and gearbox efficiency; no motor summary conversion. |
| `src/motor_ai_sim/gears/losses.py:3` (21) | no | Confirmed: gear mesh/planetary balances calculate their own signed port powers from explicit mechanical inputs; no EM-summary read. |
| `src/motor_ai_sim/gears/planetary_equilibrium.py:19` (8) | no | Confirmed: planetary equilibrium uses explicit gear member torques/speeds and mesh efficiencies, never stored motor efficiency. |
| `src/motor_ai_sim/gears/presets.py:41` (14) | no | Confirmed: gearbox acceptance compares reverse gear efficiency to a preset limit; it does not compare motor conventions. |
| `src/motor_ai_sim/gears/schema.py:52` (1) | no | Confirmed: metric availability classifies gear-result names/flags at zero speed; it neither reads nor changes motor P/T. |
| `src/motor_ai_sim/geometry_validation.py:15` (1) | no | Confirmed: efficiency appears only in the geometry validator documentation, not a result reader. |
| `src/motor_ai_sim/inverter/controller_catalog.py:208` (1) | no | Confirmed: controller catalogue returns manufacturer typical efficiency, not a motor simulation result. |
| `src/motor_ai_sim/inverter/igbt.py:985` (9) | no | Confirmed: IGBT controller computes inverter efficiency and multiplies the resolved duty efficiency; route sources now retain the duty basis, no k is applied here. |
| `src/motor_ai_sim/inverter/losses.py:48` (36) | no | Confirmed: MOSFET controller uses explicit p_ac and resolved efficiency_shaft; route supplies basis to output sources; fin efficiency is unrelated heat transfer. |
| `src/motor_ai_sim/mcp_app.py:217` (4) | no | Confirmed: MCP tool descriptions and wrappers delegate complete results to mcp_tools/agent_designs, which carry basis. |
| `src/motor_ai_sim/mcp_discovery.py:108` (5) | no | Confirmed: discovery contains capability descriptions only, no numerical summary projection. |
| `src/motor_ai_sim/mcp_tools.py:10` (10) | yes | yes |
| `src/motor_ai_sim/mech_losses.py:30` (2) | no | Confirmed: bearing/windage producer and stored mechanical-loss adapter; consumes rpm/loads/temperature or stored loss terms, not shaft power. |
| `src/motor_ai_sim/modules/component_adapters.py:130` (1) | no | Confirmed: motor card embeds the complete MotorPerformanceIR produced from the independent S1 grid, retaining its basis. |
| `src/motor_ai_sim/modules/component_plugins/gearbox_single_stage.py:18` (1) | no | Confirmed: reference gearbox plugin constructs its own explicit-loss TransmissionIR and basis; no EM summary. |
| `src/motor_ai_sim/modules/drivetrain/adapters.py:109` (2) | no | Confirmed: motor_ir_from_s1_model interpolates its own torque/loss grid and names sine/reference-temperature shaft basis; no marked summary. |
| `src/motor_ai_sim/modules/drivetrain/system.py:67` (7) | no | Confirmed: drivetrain system evaluates named MotorPerformanceIR/TransmissionIR/propulsor data and their bases, not EM result summaries. |
| `src/motor_ai_sim/modules/motor_mass.py:739` (7) | yes | yes |
| `src/motor_ai_sim/modules/optimization.py:26` (1) | no | Confirmed: module manifest text plus run_one result returned whole; the refined result retains marker and metrics. |
| `src/motor_ai_sim/modules/surrogate.py:27` (2) | no | Confirmed: surrogate module asks for an independent analytic prediction, labels physics=surrogate; no stored summary projection. |
| `src/motor_ai_sim/motor_tests/compare.py:49` (2) | no | Confirmed: comparison wrapper returns the full comparison report; model_source/comparison supply the run-specific shaft basis. |
| `src/motor_ai_sim/motor_tests/comparison.py:749` (21) | yes | yes: a marked duty is taken as stored (no second k); the basis quotes the run's own shaft_k3d (r4), never today's passport |
| `src/motor_ai_sim/motor_tests/model_source.py:37` (21) | yes | Fixed in `src/motor_ai_sim/motor_tests/model_source.py:37`: marked bench rows derive coupling torque only from stored shaft power and rpm; absent coupling efficiency stays absent when mechanical-loss metadata exists. |
| `src/motor_ai_sim/optimization/design_eval.py:6` (11) | no | Confirmed: evaluate_design uses an independent analytic DesignMetrics producer; no stored marked summary or cache is read. |
| `src/motor_ai_sim/optimization/doe.py:6` (4) | yes | Fixed in `src/motor_ai_sim/optimization/doe.py:130`: DOE compact records now retain shaft_conv; one call evaluates one fresh sample set and overwrites its dataset. |
| `src/motor_ai_sim/optimization/eval_log.py:268` (2) | no | Confirmed: baseline_distance scores raw metrics against the same worker baseline; eval_log retains caller row fields and routes stamp shaft_conv. |
| `src/motor_ai_sim/optimization/optimizer.py:30` (4) | no | Confirmed: analytic search consumes DesignMetrics from design_eval only; generic Pareto primitive is now called per convention by sweep_rebuild. |
| `src/motor_ai_sim/optimization/pareto.py:8` (14) | yes | Fixed in `src/motor_ai_sim/optimization/pareto.py:143`: marked field torque and shaft density are never inferred from each other; report excludes opposite-convention candidates from dominance ranking. |
| `src/motor_ai_sim/optimization/refine_proc.py:196` (13) | yes | yes |
| `src/motor_ai_sim/optimization/surrogate.py:12` (4) | yes | Fixed in `src/motor_ai_sim/optimization/surrogate.py:100`: accumulated-dataset training refuses mixed conventions before filtering/training; homogeneous old data keeps its old path. |
| `src/motor_ai_sim/optimize_headless.py:326` (4) | yes | yes |
| `src/motor_ai_sim/part_states.py:16` (1) | no | Confirmed: efficiency appears only in part-state policy documentation; mass/heat participation is not a shaft-power reader. |
| `src/motor_ai_sim/passport.py:552` (1) | no | Confirmed: passport extractor builds torque/flux/copper-loss grids from raw solver data; its sole efficiency hit is explanatory text. |
| `src/motor_ai_sim/passport_pwm.py:556` (3) | no | Confirmed: passport PWM anchors compare raw field torque and EM loss deltas only; no shaft power or efficiency is synthesized from them. |
| `src/motor_ai_sim/passport_store.py:8` (1) | no | Confirmed: store reads/writes complete independent passport documents, preserving all existing fields; hit is format documentation. |
| `src/motor_ai_sim/passport_v1/card.py:162` (15) | no | Confirmed: card uses jobs.scalars raw field torque and its own explicit T*omega-minus-friction model; duty comparison compares field T and EM losses, retains stored summary. |
| `src/motor_ai_sim/passport_v1/fullcard.py:369` (34) | yes | Fixed in `src/motor_ai_sim/passport_v1/fullcard.py:370`: cooling_block now recognizes marked S1 reference, uses stored coupling watts/torque without k again, and renders its basis; other passport maps retain their independent labelled model. |
| `src/motor_ai_sim/passport_v1/jobs.py:134` (1) | no | Confirmed: scalars() consumes raw solver arrays/T_avg, computes loss groups (P_shaft_W means eddy heat); no marked summary is consumed. |
| `src/motor_ai_sim/passport_v1/losses.py:20` (5) | no | Confirmed: independent passport loss interpolation and explicitly documented legacy T*omega efficiency formula; inputs originate in raw solver grids, not marked summaries. |
| `src/motor_ai_sim/passport_v1/pwm_parts.py:71` (1) | no | Confirmed: PWM loss extraction reads shaft/sleeve eddy watts only to isolate magnet heat; no shaft-output arithmetic. |
| `src/motor_ai_sim/passport_v1/report_md.py:127` (7) | no | Confirmed: report_md renders the independent passport card with explicit formulas; stored duty comparison is field torque/loss only, not efficiency or shaft power. |
| `src/motor_ai_sim/passport_v1/snapshot.py:261` (6) | yes | yes (r4): stored_summary carries shaft_convention / shaft_k3d / op_mode on a marked summary only (earlier snapshots keep their shape) |
| `src/motor_ai_sim/passport_v1/stage3.py:589` (8) | no | Confirmed: motor_point uses passport hot map/loss trajectory and stated friction; PWM variants build on that independent model, no marked summary P/T. |
| `src/motor_ai_sim/propeller_bemt.py:612` (6) | no | Confirmed: blade-element propeller efficiency is thrust*flight-speed/propeller-power, independent of motor EM results. |
| `src/motor_ai_sim/propeller_design.py:475` (1) | no | Confirmed: propeller flight efficiency derives thrust*flight speed/shaft load power; independent of motor EM summary. |
| `src/motor_ai_sim/propeller_design_api.py:412` (2) | no | Confirmed: propeller run projection reads propulsive efficiency from propeller_design flight results, not motor efficiency. |
| `src/motor_ai_sim/public_motors.py:15` (5) | yes | Fixed in `src/motor_ai_sim/public_motors.py:43`: public allowlist carries marked duty efficiency basis without changing requested duty P/T; landing tooltip shows the basis. |
| `src/motor_ai_sim/report.py:251` (224) | yes | yes |
| `src/motor_ai_sim/report_customer.py:235` (5) | yes | yes |
| `src/motor_ai_sim/routes/admin.py:935` (1) | no | Confirmed: seeded support-message example text only; no efficiency metric reader. |
| `src/motor_ai_sim/routes/bearings.py:136` (1) | no | Confirmed: bearings route computes mechanical losses from speed and bearing assignment; efficiency appears only in documentation. |
| `src/motor_ai_sim/routes/controller.py:186` (44) | yes | Fixed in `src/motor_ai_sim/routes/controller.py:400`: marked duty defaults withhold missing coupling torque and carry efficiency basis into response sources; explicit user efficiency overrides remain independent. |
| `src/motor_ai_sim/routes/coupled.py:359` (65) | yes | yes |
| `src/motor_ai_sim/routes/family.py:1396` (13) | yes | yes |
| `src/motor_ai_sim/routes/mechanical.py:264` (5) | yes | yes |
| `src/motor_ai_sim/routes/optimization.py:5` (119) | yes | Fixed in `src/motor_ai_sim/routes/optimization.py:436`: compact points retain marked stored shaft watts; baseline-line metadata carries convention; objective and solver algebra unchanged. |
| `src/motor_ai_sim/routes/presets.py:216` (19) | yes | yes |
| `src/motor_ai_sim/routes/saved_sims.py:4` (1) | no | Confirmed: save stores the arbitrary complete results dictionary; restore returns it whole, retaining convention. |
| `src/motor_ai_sim/routes/simulation.py:353` (56) | yes | yes |
| `src/motor_ai_sim/routes/thermal.py:6601` (3) | no | Confirmed: thermal route efficiency is shaft-stub fin effectiveness tanh(mL)/mL, not motor conversion efficiency. |
| `src/motor_ai_sim/shaft_convention.py:6` (25) | yes | yes |
| `src/motor_ai_sim/simulation/battery.py:118` (1) | no | Confirmed: battery documentation says pack resistance loss is separate; no shaft summary read. |
| `src/motor_ai_sim/simulation/cooling_models.py:472` (9) | no | Confirmed: efficiency is fin effectiveness from thermal geometry/conductivity; no mechanical output or motor eta. |
| `src/motor_ai_sim/simulation/eddy_solver_2d.py:433` (2) | no | Confirmed: P_shaft_W in eddy solver return documentation is dissipated shaft heat, not output power. |
| `src/motor_ai_sim/simulation/fem_solver_2d.py:1375` (10) | no | Confirmed: raw field solver produces torque/eddy arrays; efficiency hits are diagnostics/comments, marked power is formed downstream. |
| `src/motor_ai_sim/simulation/postprocess.py:300` (5) | no | Confirmed: compute_efficiency is independent raw-scalar legacy postprocessing; no marked summary argument or caller reconstruction from marked T. |
| `src/motor_ai_sim/simulation/sb_domains.py:38` (1) | no | Confirmed: efficiency appears only in sampling-grid documentation, not in reader code. |
| `src/motor_ai_sim/simulation/solver_2d.py:6` (8) | no | Confirmed: analytic dry-run returns absent torque/power/efficiency until a field solve; does not read marked records. |
| `src/motor_ai_sim/simulation/time_periodic.py:1469` (1) | no | Confirmed: convergence compares raw torque/ripple/conductor-loss windows; efficiency appears only in documentation. |
| `src/motor_ai_sim/support_catalog.py:23` (6) | yes | Fixed in `src/motor_ai_sim/support_catalog.py:160`: each rated/other-duty efficiency phrase now names its own convention; requested operating points are unchanged. |
| `src/motor_ai_sim/support_prompt.py:52` (4) | no | Confirmed: UI/support instruction strings only; numerical motor context comes from support_catalog with per-duty basis. |
| `src/motor_ai_sim/sweep_rebuild.py:43` (11) | yes | Fixed in `src/motor_ai_sim/sweep_rebuild.py:192`: reconstruction keeps marker, rejects wrong-convention cache, groups Pareto fronts by convention; absent shaft P stays absent. |
| `src/motor_ai_sim/sweep_resume.py:234` (1) | no | Confirmed: resume carries requested efficiency threshold in worker kwargs and restores whole scan point dictionaries; no numeric projection. |
| `src/motor_ai_sim/terminals.py:195` (19) | yes | Fixed in `src/motor_ai_sim/terminals.py:279`: marked terminal budgets use one coupling power/efficiency pair, preserve zero, refuse missing coupling eta, and carry basis; old arithmetic unchanged. |
| `src/motor_ai_sim/thermal_duty_cycle.py:532` (1) | yes | Confirmed: thermal-duty-cycle adapter separates mechanical watts in thermal nodes for callers; no shaft-output reader. |
| `src/motor_ai_sim/thermal_heat_paths.py:473` (2) | no | Confirmed: heat-path annotation quotes fin efficiency from thermal stub output, unrelated to motor shaft convention. |
| `web/src/components/catalog/CatalogByDie.tsx:67` (4) | yes | yes |
| `web/src/components/catalog/FamilyCatalog.tsx:106` (1) | no | Confirmed: local DutyResult type only; FamilyCatalog passes whole duty objects to marker-aware catalog views, no runtime stripping. |
| `web/src/components/common/ActiveFamilyStrip.tsx:55` (12) | yes | yes |
| `web/src/components/common/motorSettings.ts:74` (3) | yes | yes |
| `web/src/components/compare/ChargePanel.tsx:114` (1) | no | Confirmed: ChargePanel renders generatorCharge analytic passport result; it never consumes marked FEM output. |
| `web/src/components/compare/ComparePanel.tsx:155` (10) | no | yes |
| `web/src/components/compare/ConfiguratorPanel.tsx:7` (21) | yes | Fixed in `web/src/components/compare/ConfiguratorPanel.tsx:2286`: draft FEM headline tooltip names field torque, pre-bearing power and coupling efficiency; scaleMotor paths remain independent. |
| `web/src/components/compare/EfficiencyMap.tsx:2` (8) | no | Confirmed: efficiency cells call scaleMotor on passport/knobs and read that independent analytic result, not saved summaries. |
| `web/src/components/compare/PerformanceCharts.tsx:5` (5) | no | Confirmed: speed curves call scaleMotor for each rpm, so power/efficiency and battery input share the same analytic source. |
| `web/src/components/compare/PointsConfigure.tsx:177` (3) | yes | Confirmed: PointsConfigure explicitly identifies comparison/panel markers and labels mixed-convention steps without ranking them. |
| `web/src/components/compare/PropellerLoadChart.tsx:3` (1) | no | Confirmed: PropellerLoadChart consumes propeller_s1 rows through propellerLoadChart helpers, not marked EM summaries. |
| `web/src/components/compare/addToCompare.ts:120` (4) | no | Confirmed: readShownSummary/emSummaryIfLive return the complete saved summary and add-to-compare stores it whole. |
| `web/src/components/controller/ControllerPanel.tsx:103` (5) | yes | Fixed in `web/src/components/controller/ControllerPanel.tsx:903`: wall-to-shaft efficiency tooltip shows the stored route source basis; no numeric or inverter-loss arithmetic changes. |
| `web/src/components/controller/compareRows.ts:66` (3) | yes | Fixed in `web/src/components/controller/compareRows.ts:66`: stacked controller comparisons carry a visible shaft-basis input column from route sources; numeric efficiency remains the supplied result. |
| `web/src/components/controller/controllerApi.ts:132` (7) | no | Confirmed: ControllerResult includes sources/point Record dictionaries and retains the route basis; other hits are thermal fin settings. |
| `web/src/components/landing/PublicMotors.tsx:3` (2) | yes | Fixed in `web/src/components/landing/PublicMotors.tsx:59`: public card tooltip names the rated efficiency convention carried by landingMotors. |
| `web/src/components/mechanical/api.ts:825` (4) | yes | Fixed in `web/src/components/mechanical/api.ts:9`: mechanical stored-torque readers refuse marked missing coupling power/speed instead of falling back to field torque. |
| `web/src/components/simulation/FemAnimationViewer.tsx:69` (5) | yes | Fixed in `web/src/components/simulation/FemAnimationViewer.tsx:70`: animation payload carries marked basis and refuses missing marked power instead of Tfield*omega; unmarked fallback remains. |
| `web/src/components/simulation/FemFieldChart.tsx:243` (5) | yes | Fixed in `web/src/components/simulation/FemFieldChart.tsx:245`: field snapshot preserves marker, missing marked power stays NaN, diagnostic efficiency tooltip names basis. |
| `web/src/components/simulation/SimulationPanel.tsx:163` (15) | yes | yes |
| `web/src/components/simulation/SummaryTable.tsx:5` (28) | yes | yes |
| `web/src/components/simulation/coupledApi.ts:140` (12) | no | Confirmed: coupledApi preserves the full coupling object; rating power has explicit basis/torque_basis and controllerTerm names wall-to-shaft efficiency, no T-to-P reconstruction. |
| `web/src/components/simulation/fem-types.ts:20` (3) | yes | Fixed in `web/src/components/simulation/fem-types.ts:21`: FEM payload type admits optional shaft_convention for field/animation diagnostic readers. |
| `web/src/components/sweep/AutoOptimizePanel.tsx:500` (17) | yes | Fixed in `web/src/components/sweep/AutoOptimizePanel.tsx:1`: optimizer cloud uses field-torque helper that refuses marked density reconstruction; field torque is labelled; table carries basis. |
| `web/src/components/sweep/AutoPointsTable.tsx:33` (4) | yes | Fixed in `web/src/components/sweep/AutoPointsTable.tsx:4`: table reads stored marked watts, shows missing as dash and explicit field-torque/shaft-power/basis columns. |
| `web/src/components/sweep/DOEPanel.tsx:4` (3) | yes | Fixed in `web/src/components/sweep/DOEPanel.tsx:141`: DOE UI renders importance weights, not mechanical values; mixed-convention training refusal is shown as a bilingual validation error instead of a misleading insufficient-samples message. |
| `web/src/components/sweep/DescentPanel.tsx:37` (44) | yes | Fixed in `web/src/components/sweep/DescentPanel.tsx:44`: restored load-line overlays are split and labelled by basis before interpolation; scatter projections carry marker/power and tooltips show basis. |
| `web/src/components/sweep/Scatter3D.tsx:2` (3) | yes | Fixed in `web/src/components/sweep/Scatter3D.tsx:15`: 3D scatter point type and hover retain/name each point convention; no scaling of the supplied td/eff. |
| `web/src/components/sweep/SweepConfigPanel.tsx:495` (2) | no | Confirmed: SweepConfig uses importance weights to select variable controls, not mechanical metrics; mixed training is refused upstream. |
| `web/src/components/sweep/SweepStudyPanel.tsx:2` (45) | yes | Fixed in `web/src/components/sweep/SweepStudyPanel.tsx:142`: all three marked power fallbacks preserve missing values; Apply requires stored power; numeric zero is not replaced by field T*omega. |
| `web/src/components/system/systemComposeTypes.ts:311` (2) | no | Confirmed: static component palette defines a reference gearbox parameter named efficiency, not a motor-result reader. |
| `web/src/components/thermal/ThermalPanel.tsx:1804` (3) | no | Confirmed: ThermalPanel displays fin effectiveness and heat conductance from thermal results, not shaft output. |
| `web/src/components/thermal/dutyCycleTorque.ts:31` (6) | yes | yes |
| `web/src/components/thermal/heatPaths.ts:471` (2) | no | Confirmed: heatPaths formats shaft-stub fin effectiveness, not mechanical efficiency. |
| `web/src/components/thermal/types.ts:141` (2) | no | Confirmed: thermal type interfaces describe fin effectiveness; no mechanical output/efficiency reader. |
| `web/src/lib/agentDrafts.ts:57` (2) | yes | Fixed in `web/src/lib/agentDrafts.ts:57`: DraftHeadline admits marker and torque/power bases; bestDraftResult returns selected headline whole. |
| `web/src/lib/appliedAutoSave.ts:32` (1) | no | Confirmed: auto-save forwards metrics Record unchanged in request body; no key allowlist discards marker. |
| `web/src/lib/apply3d.ts:5` (23) | yes | yes |
| `web/src/lib/autoPointPower.ts:3` (2) | yes | Fixed in `web/src/lib/autoPointPower.ts:3`: pure compact-point reader returns stored marked watts and field torque; unmarked formulas preserved. |
| `web/src/lib/catalogByDie.ts:15` (10) | yes | yes |
| `web/src/lib/configuratorDrive.ts:10` (4) | no | Confirmed: drive interpolation consumes independent passport PWM variants; no marked FEM summaries are attached to those legacy models. |
| `web/src/lib/configuratorFlags.ts:3` (2) | no | Confirmed: feature flag documentation only; no result read. |
| `web/src/lib/configuratorTiles.ts:5` (14) | no | Confirmed: tuner tile arithmetic takes scaleMotor shaft power and passport PWM loss model; no saved marked summary input. |
| `web/src/lib/dutyEfficiency.ts:1` (4) | yes | yes |
| `web/src/lib/dutyLocalApply.ts:455` (7) | yes | yes (r4 C5): a marked duty without its full summary is not rebuilt from requested targets; asks for a re-solve |
| `web/src/lib/emResultTiles.ts:22` (31) | yes | yes |
| `web/src/lib/endWindingPin.ts:8` (1) | no | Confirmed: copper end-turn request pinning only; efficiency hit is explanatory text. |
| `web/src/lib/fullMass.ts:23` (6) | no | Confirmed: perMass selects existing ratios by mass availability; complete summary remains with marker-aware emResultTiles caller. |
| `web/src/lib/generatorCharge.ts:113` (4) | no | Confirmed: generatorCharge accepts ScaledResult from scaleMotor and computes battery charging from its independent passport balance. |
| `web/src/lib/landingMotors.ts:9` (5) | yes | Fixed in `web/src/lib/landingMotors.ts:9`: public-row parser preserves optional shaft_conv so the card can label efficiency; old numeric formatting unchanged. |
| `web/src/lib/motorScaling.ts:5` (10) | no | Confirmed: scaleMotor is an independent passport/knob analytic producer; consumes no marked run summary or coupling record. |
| `web/src/lib/propellerDesign.ts:124` (1) | no | Confirmed: frontCost compares propeller thrust per shaft watt and cooling air, not motor summary efficiency. |
| `web/src/lib/propellerLoadChart.ts:2` (6) | no | Confirmed: chart derives propeller S1 shaft/input efficiency from the independent S1 model; no field-torque power substitution. |
| `web/src/lib/resultsPanel.ts:69` (4) | yes | yes |
| `web/src/lib/shaftBalance.ts:6` (16) | yes | yes |
| `web/src/lib/shaftCoupling.ts:3` (3) | yes | yes |
| `web/src/lib/simPoints.ts:40` (7) | yes | Fixed in `web/src/lib/simPoints.ts:41`: efficiency tooltip appends translated run basis; duty target power/torque unchanged; both production callers supply translations. |
| `web/src/lib/sweepApply3d.ts:12` (7) | yes | yes |
| `web/src/lib/sweepSettings.ts:118` (5) | yes | yes |
| `web/src/modules/development/comparisonRows.ts:62` (6) | yes | yes |
| `web/src/modules/development/motorGroups.ts:53` (5) | yes | yes: averageEfficiency never averages across two conventions (test) |
| `web/src/modules/development/pointsLimited.ts:262` (1) | no | Confirmed: configureTileKey maps tile IDs to labels and mass scope; it does not strip the point object or convert numbers. |
| `web/src/modules/development/pointsOnly.ts:34` (2) | no | Confirmed: tileValue reads stored comparison numbers; PointsConfigure retains full point/panel and labels mixed conventions. |
| `web/src/modules/development/types.ts:45` (2) | no | Confirmed: type-only declarations keep comparison and panel summary as Records; runtime values/marker are not projected away. |
| `web/src/modules/gears/components/Results.tsx:54` (1) | no | Confirmed: gear Results table renders gear efficiency fields with availability, never EM output. |
| `web/src/modules/gears/types.ts:45` (2) | no | Confirmed: gearbox result interfaces describe independent gear efficiency; no motor EM summary fields. |
| `web/src/modules/gears/view.ts:9` (1) | no | Confirmed: gearbox view reads metric_availability for gear efficiency; not motor summary convention. |
| `web/src/modules/tests/components/TestsPanel.tsx:496` (1) | no | Confirmed: TestsPanel renders bench powerSummary/tableModel results; simulated rows retain comparison group basis, no raw marked summary read. |
| `web/src/modules/tests/tableModel.ts:310` (1) | no | Confirmed: bench powerSummary uses measured torque; powerRow model torque comes from marker-aware model_source/comparison coupling torque, with group basis. |
| `web/src/stores/motorStore.ts:121` (6) | yes | yes |
| `web/src/types/motor.ts:191` (4) | no | Confirmed: DesignMetrics declaration only; motorStore retains whole backend points and marker-aware Sweep reads actual runtime fields. |
