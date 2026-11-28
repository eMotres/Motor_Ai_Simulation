# Shaft-power convention: reader inventory

PR #900: `shaft_convention == "fe_on_shaft_k3d_v1"`; compact records use `shaft_conv == 1`.
Verified 2026-11-26 by reading each of the original 109 no-marker paths and their producers/callers where needed.
The reasons below distinguish field torque, mechanical output, shaft eddy heat, independent passport models, and pass-throughs that actually preserve provenance.
This is a static reader audit, not a new validation of solver physics or a browser/FEM run.

Coverage is enforced by `tests/test_shaft_reader_inventory.py`: source files under `src` and `web/src`,
extensions `.py`, `.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`, `.cjs`; excludes `__tests__`, locales and test/spec files.
Search: distinctive motor metric tokens (including T_em_Nm/T_avg_Nm and comments/types); generic efficiency and compact td/eff/torque require summary/point access patterns. Bare efficiency prose/labels and unrelated gear/propeller/fin access are excluded.
The table is the only file allowlist. New matching files require review and a reason; the guard does not prove correctness or detect every new path inside an existing file.
First matching line and number of matching lines are navigation aids; the marker column reports a direct marker/helper reference, not correctness by itself.

Original no-marker rows: 84 confirmed, 25 fixed; 15 previously absent files added (3 fixed). Total: 170 files. r6: 35 bare marker-aware dispositions plus 3 annotated ones now have path-specific reasons; Compare corrected; 12 additional reader files reviewed. Total: 182 files (historical rows remain listed even if the narrower scan no longer matches).

## Display rule for the basis words (r7, coordinator decision 2026-11-26)

An UNMARKED record shown on its own keeps exactly its pre-PR text and shape (digest lines and headline, support-bot
context, design-bot fit rows, passport cooling card, point chips and tooltips, public motor hover, controller tile and
comparison, Auto / Descent / Sweep / 3-D labels and columns, draft headline hover, field-chart efficiency tile, landing
data). The basis label, column or suffix ("Earlier shaft basis", "Field torque", "Shaft power", "core
loss on the shaft", and their zh-CN texts) appears only (a) on marked records and (b) on both kinds when marked and
unmarked records are shown together in one table, chart or list. Accepted exception: an unmarked EM row without its own
efficiency does not borrow one from another record. Web helper: `web/src/lib/shaftBasisLabel.ts`; Python: the
`mixed` argument of `digest.readable.result_line` / `headline` and `support_catalog.render_config`, and the
both-kinds check in `mcp_tools.check_fit` and `passport_v1.fullcard._cooling_html`.

| file:first line (matches) | reads the marker | disposition |
|---|---|---|
| `src/motor_ai_sim/agent_designs.py:1095` (9) | yes | Fixed in `src/motor_ai_sim/agent_designs.py:1156`: marked headline carries field-torque / pre-bearing power / coupling-efficiency bases; parent mechanical loss and included flag agree. |
| `src/motor_ai_sim/assistant_estimate.py:160` (2) | no | Confirmed: estimate() uses propeller demand and an explicitly assumed SYSTEM_EFFICIENCY; no saved EM summary input. |
| `src/motor_ai_sim/charge_analytic.py:244` (5) | no | Confirmed: machine_point() computes its own passport T*omega and loss model; charge_point consumes that analytic pair, never a marked summary. |
| `src/motor_ai_sim/cogging.py:146` (4) | no | Confirmed: cogging rated-torque reference uses solved field torque only for cogging percentage normalization, with source description; no shaft-power or efficiency projection. |
| `src/motor_ai_sim/configure_anchor.py:78` (1) | no | Confirmed: thermal anchor reads P_shaft_W as rotor eddy heat alongside magnet heat; it does not read mechanical shaft output. |
| `src/motor_ai_sim/contracts/_selfcheck.py:97` (2) | no | Confirmed: executable contract selfcheck uses a literal synthetic unmarked fixture and checks scalar/series round-trip; no saved run selection. |
| `src/motor_ai_sim/contracts/adapters.py:192` (5) | yes | Fixed in `src/motor_ai_sim/contracts/adapters.py:205`: standalone ScalarResults now carries marked convention and explicit field-torque / pre-bearing power and efficiency bases; raw summary remains intact. |
| `src/motor_ai_sim/contracts/conformance/drivetrain.py:104` (8) | no | Confirmed: conformance validates independent MotorPerformanceIR efficiency maps with mandatory basis, transmission losses and propeller efficiency, not EM summaries. |
| `src/motor_ai_sim/contracts/drivetrain_ir.py:8` (11) | no | Confirmed: EfficiencyMap requires its own basis; shaft/transmission interpolation consumes that independent IR, not summary P/T. |
| `src/motor_ai_sim/contracts/result_ir.py:31` (1) | no | Confirmed: ScalarResults allows extra metadata; adapter now attaches marked scalar bases without altering unmarked schema defaults. |
| `src/motor_ai_sim/coupled_continuous_rating.py:432` (22) | yes | Confirmed: rating reference retains convention and drag terms; projected points rebuild the same basis before shaft_view. |
| `src/motor_ai_sim/coupled_pwm_final.py:174` (14) | yes | Confirmed: changed iron loss invokes shaft_convention.rederive before coupling efficiency, retaining the run basis. |
| `src/motor_ai_sim/datasheet.py:73` (14) | yes | Confirmed: signed marked motor power is retained and mixed duty columns identify their shaft-power basis. |
| `src/motor_ai_sim/design_agent/prompt.py:10` (2) | no | Confirmed: prompt text instructs agents to quote tool numbers; no numeric reader or conversion. |
| `src/motor_ai_sim/design_agent/tools.py:82` (3) | yes | Confirmed: list_motors retains duty basis; compact_fit preserves basis actually produced by mcp_tools.check_fit for fits and rejected rows (regression tested). r7: an answer of unmarked rows alone carries no basis key (pre-PR shape). |
| `src/motor_ai_sim/development/motor_view.py:627` (21) | yes | Confirmed: compact reads require complete marked context; overlays replace convention and mechanical-loss metadata with the selected loop. |
| `src/motor_ai_sim/development/motors.py:29` (21) | yes | Fixed in `src/motor_ai_sim/development/motors.py:763`: old primary summary/compact result cannot borrow marked coupled power, torque or efficiency; marked records use one coupling basis. |
| `src/motor_ai_sim/development/sim_view.py:107` (1) | yes | Fixed in `src/motor_ai_sim/development/sim_view.py:107`: _duty_row copies shaft_conv with efficiency to the point switcher; requested duty P/T remain targets. |
| `src/motor_ai_sim/development/spec_order.py:255` (1) | no | Confirmed: specification-entry instruction text only; no solved metric is projected or recomputed. |
| `src/motor_ai_sim/digest/collect.py:254` (1) | no | Confirmed: headline/result_line delegate whole kinds records to digest.readable, now basis-aware through R2. r7: one variant's lines are checked for mixed bases first (`line_basis`); `headline(kinds, mixed)` passes it on. r8: variant detection uses line_basis(result=True), matching the held override; owner-report detection keeps the default mechanical selection. Two-duty EN/zh regression executes collect_project. |
| `src/motor_ai_sim/digest/readable.py:125` (10) | yes | Fixed in `src/motor_ai_sim/digest/readable.py:136`: selected marked record supplies coupling power/torque/efficiency; motor and generator labels are bilingual; legacy missing EM efficiency does not borrow coupled efficiency. r7: an unmarked line / headline alone keeps its pre-PR text; the basis words only on marked lines and, with `mixed=True`, on unmarked ones. r8: _result_context shares thermal selection and held override with result_line; both basis guards include torque and generator output. Owner headlines retain their original selection. |
| `src/motor_ai_sim/duty_results.py:1747` (7) | yes | Confirmed: compact_em_run stores shaft_conv plus typed shaft_context only for marked summaries; legacy shape is preserved. |
| `src/motor_ai_sim/gears/gearbox.py:146` (39) | no | Confirmed: gearbox efficiencies come from signed gear-port powers, mesh losses or explicit catalogue assumptions; no marked EM-summary keys are consumed. |
| `src/motor_ai_sim/gears/loads.py:113` (1) | no | Confirmed: mechanical stage result dataclass describes power_in/out and gearbox efficiency; no motor summary conversion. |
| `src/motor_ai_sim/gears/losses.py:3` (21) | no | Confirmed: gear mesh/planetary balances calculate their own signed port powers from explicit mechanical inputs; no EM-summary read. |
| `src/motor_ai_sim/gears/planetary_equilibrium.py:19` (8) | no | Confirmed: planetary equilibrium uses explicit gear member torques/speeds and mesh efficiencies, never stored motor efficiency. |
| `src/motor_ai_sim/gears/presets.py:41` (14) | no | Confirmed: gearbox acceptance compares reverse gear efficiency to a preset limit; it does not compare motor conventions. |
| `src/motor_ai_sim/gears/schema.py:52` (1) | no | Confirmed: metric availability classifies gear-result names/flags at zero speed; it neither reads nor changes motor P/T. |
| `src/motor_ai_sim/geometry_validation.py:15` (1) | no | Confirmed: efficiency appears only in the geometry validator documentation, not a result reader. |
| `src/motor_ai_sim/inverter/controller_catalog.py:208` (1) | no | Confirmed: controller catalogue returns manufacturer typical efficiency, not a motor simulation result. |
| `src/motor_ai_sim/inverter/igbt.py:986` (5) | no | Confirmed: IGBT controller computes inverter efficiency and multiplies the resolved duty efficiency; route sources now retain the duty basis, no k is applied here. |
| `src/motor_ai_sim/inverter/losses.py:1061` (9) | no | Confirmed: MOSFET controller uses explicit p_ac and resolved efficiency_shaft; route supplies basis to output sources; fin efficiency is unrelated heat transfer. |
| `src/motor_ai_sim/mcp_app.py:217` (4) | no | Confirmed: MCP tool descriptions and wrappers delegate complete results to mcp_tools/agent_designs, which carry basis. |
| `src/motor_ai_sim/mcp_discovery.py:108` (5) | no | Confirmed: discovery contains capability descriptions only, no numerical summary projection. |
| `src/motor_ai_sim/mcp_tools.py:392` (4) | yes | Confirmed: duty projection carries shaft_conv; check_fit labels fits and rejected rows and groups rankings by convention. r7: check_fit names the basis on marked rows, and on unmarked rows only when the answer holds both kinds. |
| `src/motor_ai_sim/mech_losses.py:30` (2) | no | Confirmed: bearing/windage producer and stored mechanical-loss adapter; consumes rpm/loads/temperature or stored loss terms, not shaft power. |
| `src/motor_ai_sim/modules/component_adapters.py:130` (1) | no | Confirmed: motor card embeds the complete MotorPerformanceIR produced from the independent S1 grid, retaining its basis. |
| `src/motor_ai_sim/modules/component_plugins/gearbox_single_stage.py:18` (1) | no | Confirmed: reference gearbox plugin constructs its own explicit-loss TransmissionIR and basis; no EM summary. |
| `src/motor_ai_sim/modules/drivetrain/adapters.py:109` (2) | no | Confirmed: motor_ir_from_s1_model interpolates its own torque/loss grid and names sine/reference-temperature shaft basis; no marked summary. |
| `src/motor_ai_sim/modules/drivetrain/system.py:67` (7) | no | Confirmed: drivetrain system evaluates named MotorPerformanceIR/TransmissionIR/propulsor data and their bases, not EM result summaries. |
| `src/motor_ai_sim/modules/motor_mass.py:739` (7) | yes | Confirmed: full-mass ratios use stored shaft torque for marked motors and corrected field torque for generators; no second k. |
| `src/motor_ai_sim/modules/optimization.py:26` (1) | no | Confirmed: module manifest text plus run_one result returned whole; the refined result retains marker and metrics. |
| `src/motor_ai_sim/modules/surrogate.py:37` (1) | no | Confirmed: surrogate module asks for an independent analytic prediction, labels physics=surrogate; no stored summary projection. |
| `src/motor_ai_sim/motor_tests/compare.py:49` (2) | no | Confirmed: comparison wrapper returns the full comparison report; model_source/comparison supply the run-specific shaft basis. |
| `src/motor_ai_sim/motor_tests/comparison.py:910` (6) | yes | Confirmed: marked duties use stored torque/efficiency and quote the run shaft_k3d; current passport k is not reapplied. |
| `src/motor_ai_sim/motor_tests/model_source.py:37` (16) | yes | Fixed in `src/motor_ai_sim/motor_tests/model_source.py:37`: marked bench rows derive coupling torque only from stored shaft power and rpm; absent coupling efficiency stays absent when mechanical-loss metadata exists. |
| `src/motor_ai_sim/optimization/design_eval.py:169` (16) | no | Confirmed: evaluate_design uses an independent analytic DesignMetrics producer; no stored marked summary or cache is read. |
| `src/motor_ai_sim/optimization/doe.py:127` (3) | yes | Fixed in `src/motor_ai_sim/optimization/doe.py:130`: DOE compact records now retain shaft_conv; one call evaluates one fresh sample set and overwrites its dataset. |
| `src/motor_ai_sim/optimization/eval_log.py:268` (1) | no | Confirmed: baseline_distance scores raw metrics against the same worker baseline; eval_log retains caller row fields and routes stamp shaft_conv. |
| `src/motor_ai_sim/optimization/optimizer.py:31` (3) | no | Confirmed: analytic search consumes DesignMetrics from design_eval only; generic Pareto primitive is now called per convention by sweep_rebuild. |
| `src/motor_ai_sim/optimization/pareto.py:140` (13) | yes | Fixed: baseline selects candidates of its convention; without a baseline each convention gets its own front and no cross-basis dominance. |
| `src/motor_ai_sim/optimization/refine_proc.py:428` (18) | yes | Confirmed: producer stamps its shaft balance and compact metrics together; optimizer field torque remains explicitly separate. |
| `src/motor_ai_sim/optimization/surrogate.py:92` (6) | yes | Fixed: selects the newest appended convention before operating filtering, reports dropped_other_convention, and retains build_matrix mixed-basis guard. |
| `src/motor_ai_sim/optimize_headless.py:326` (4) | yes | Confirmed: memo cache identity includes SHAFT_CONVENTION, preventing reuse of an earlier-basis evaluation. |
| `src/motor_ai_sim/part_states.py:16` (1) | no | Confirmed: efficiency appears only in part-state policy documentation; mass/heat participation is not a shaft-power reader. |
| `src/motor_ai_sim/passport.py:419` (2) | no | Confirmed: passport extractor builds torque/flux/copper-loss grids from raw solver data; its sole efficiency hit is explanatory text. |
| `src/motor_ai_sim/passport_pwm.py:556` (3) | no | Confirmed: passport PWM anchors compare raw field torque and EM loss deltas only; no shaft power or efficiency is synthesized from them. |
| `src/motor_ai_sim/passport_store.py:8` (1) | no | Confirmed: store reads/writes complete independent passport documents, preserving all existing fields; hit is format documentation. |
| `src/motor_ai_sim/passport_v1/card.py:111` (35) | no | Confirmed: card uses jobs.scalars raw field torque and its own explicit T*omega-minus-friction model; duty comparison compares field T and EM losses, retains stored summary. |
| `src/motor_ai_sim/passport_v1/fullcard.py:369` (32) | yes | Fixed in `src/motor_ai_sim/passport_v1/fullcard.py:370`: cooling_block now recognizes marked S1 reference, uses stored coupling watts/torque without k again, and renders its basis; other passport maps retain their independent labelled model. r7: the cooling table's power-basis column only when a marked S1 row is present (an unmarked row then reads 'earlier shaft basis'). |
| `src/motor_ai_sim/passport_v1/jobs.py:104` (2) | no | Confirmed: scalars() consumes raw solver arrays/T_avg, computes loss groups (P_shaft_W means eddy heat); no marked summary is consumed. |
| `src/motor_ai_sim/passport_v1/losses.py:20` (4) | no | Confirmed: independent passport loss interpolation and explicitly documented legacy T*omega efficiency formula; inputs originate in raw solver grids, not marked summaries. |
| `src/motor_ai_sim/passport_v1/pwm_parts.py:71` (1) | no | Confirmed: PWM loss extraction reads shaft/sleeve eddy watts only to isolate magnet heat; no shaft-output arithmetic. |
| `src/motor_ai_sim/passport_v1/report_md.py:127` (7) | no | Confirmed: report_md renders the independent passport card with explicit formulas; stored duty comparison is field torque/loss only, not efficiency or shaft power. |
| `src/motor_ai_sim/passport_v1/snapshot.py:261` (5) | yes | Confirmed: stored_summary carries shaft_convention, shaft_k3d and op_mode only for marked snapshots; old shape is retained. |
| `src/motor_ai_sim/passport_v1/stage3.py:589` (7) | no | Confirmed: motor_point uses passport hot map/loss trajectory and stated friction; PWM variants build on that independent model, no marked summary P/T. |
| `src/motor_ai_sim/propeller_bemt.py:612` (6) | no | Confirmed: blade-element propeller efficiency is thrust*flight-speed/propeller-power, independent of motor EM results. |
| `src/motor_ai_sim/propeller_design.py:475` (1) | no | Confirmed: propeller flight efficiency derives thrust*flight speed/shaft load power; independent of motor EM summary. |
| `src/motor_ai_sim/propeller_design_api.py:412` (2) | no | Confirmed: propeller run projection reads propulsive efficiency from propeller_design flight results, not motor efficiency. |
| `src/motor_ai_sim/propeller_mission.py:329` (1) | no | Confirmed: P_shaft_W here is the PROPELLER's aerodynamic shaft power from its own static curve (Glauert forward-flight model), not a motor-model output; it reads no motor shaft-convention field and is independent of the FE / k3d shaft convention. |
| `src/motor_ai_sim/public_motors.py:43` (2) | yes | Fixed in `src/motor_ai_sim/public_motors.py:43`: public allowlist carries marked duty efficiency basis without changing requested duty P/T; landing tooltip shows the basis. |
| `src/motor_ai_sim/report.py:251` (144) | yes | Confirmed: summary/PWM overlays retain the selected run convention; shaft_view branches on that marker without a second factor. |
| `src/motor_ai_sim/report_customer.py:235` (6) | yes | Confirmed: requirement torque uses marked coupling torque and names its boundary; legacy corrected-field path is retained. |
| `src/motor_ai_sim/routes/admin.py:935` (1) | no | Confirmed: seeded support-message example text only; no efficiency metric reader. |
| `src/motor_ai_sim/routes/bearings.py:136` (1) | no | Confirmed: bearings route computes mechanical losses from speed and bearing assignment; efficiency appears only in documentation. |
| `src/motor_ai_sim/routes/controller.py:186` (30) | yes | Fixed in `src/motor_ai_sim/routes/controller.py:400`: marked duty defaults withhold missing coupling torque and carry efficiency basis into response sources; explicit user efficiency overrides remain independent. |
| `src/motor_ai_sim/routes/coupled.py:1217` (50) | yes | Confirmed: stored EM keys include complete convention context; marked held torque and coupling powers use that run basis. |
| `src/motor_ai_sim/routes/family.py:1887` (6) | yes | Confirmed: marked saved summaries keep their original factor; a later passport prompts recomputation instead of silently attaching k. |
| `src/motor_ai_sim/routes/gears_drivetrain.py:52` (12) | yes | Confirmed: the saved motor point is read with its marker; a marked summary gives the coupling torque P_shaft_net_W / omega (omega from the saved speed, blocks first; needs a positive speed) when it carries P_shaft_net_W, else T_shaft_Nm, with torque_basis shaft, a marked summary without a recognised shaft key is reported missing (shaft_key_unrecognised), an unmarked one gives the field torque labelled field_legacy; no k is applied here; chain efficiency is withheld unless the required point matches the saved one. The removed names T_coupling_Nm and T_shaft_em_Nm do not exist in shaft_convention.CONVENTION_KEYS and are not read. |
| `src/motor_ai_sim/routes/mechanical.py:264` (5) | yes | Confirmed: marked load torque resolves coupling torque before explicit shaft fallback; unmarked field-torque selection is unchanged. |
| `src/motor_ai_sim/routes/optimization.py:436` (73) | yes | Fixed in `src/motor_ai_sim/routes/optimization.py:436`: compact points retain marked stored shaft watts; baseline-line metadata carries convention; objective and solver algebra unchanged. r7: auto warm start ranks ONE convention: marked rows once the baseline or any row of the machine is marked, an all-unmarked history keeps its earlier warm start. r8: full/compact/line baseline markers are recognized; marked selection with an unmarked baseline is refused with a warning before scoring. Behavioral tests cover matching, mixed, refusal and unchanged unmarked ordering/filtering/scoring. |
| `src/motor_ai_sim/routes/presets.py:288` (16) | yes | Confirmed: projections retain signed marked motor power and add shaft_conv to numbers-only saved comparison rows. |
| `src/motor_ai_sim/routes/saved_sims.py:4` (1) | no | Confirmed: save stores the arbitrary complete results dictionary; restore returns it whole, retaining convention. |
| `src/motor_ai_sim/routes/simulation.py:2339` (48) | yes | Confirmed: cached-summary refresh explicitly keeps the old or current convention; marked coupling net power remains signed. |
| `src/motor_ai_sim/routes/thermal.py:6601` (3) | no | Confirmed: thermal route efficiency is shaft-stub fin effectiveness tanh(mL)/mL, not motor conversion efficiency. |
| `src/motor_ai_sim/shaft_convention.py:6` (22) | yes | Confirmed: central marker/context helpers keep the power-basis fields together when compacting, overlaying or resolving coupling torque. |
| `src/motor_ai_sim/simulation/battery.py:118` (1) | no | Confirmed: battery documentation says pack resistance loss is separate; no shaft summary read. |
| `src/motor_ai_sim/simulation/cooling_models.py:472` (9) | no | Confirmed: efficiency is fin effectiveness from thermal geometry/conductivity; no mechanical output or motor eta. |
| `src/motor_ai_sim/simulation/eddy_solver_2d.py:433` (2) | no | Confirmed: P_shaft_W in eddy solver return documentation is dissipated shaft heat, not output power. |
| `src/motor_ai_sim/simulation/fem_solver_2d.py:15381` (7) | no | Confirmed: raw field solver produces torque/eddy arrays; efficiency hits are diagnostics/comments, marked power is formed downstream. |
| `src/motor_ai_sim/simulation/postprocess.py:320` (1) | no | Confirmed: compute_efficiency is independent raw-scalar legacy postprocessing; no marked summary argument or caller reconstruction from marked T. |
| `src/motor_ai_sim/simulation/sb_domains.py:38` (1) | no | Confirmed: efficiency appears only in sampling-grid documentation, not in reader code. |
| `src/motor_ai_sim/simulation/solver_2d.py:223` (1) | no | Confirmed: analytic dry-run returns absent torque/power/efficiency until a field solve; does not read marked records. |
| `src/motor_ai_sim/simulation/time_periodic.py:1469` (1) | no | Confirmed: convergence compares raw torque/ripple/conductor-loss windows; efficiency appears only in documentation. |
| `src/motor_ai_sim/support_catalog.py:160` (1) | yes | Fixed in `src/motor_ai_sim/support_catalog.py:160`: each rated/other-duty efficiency phrase now names its own convention; requested operating points are unchanged. r7: the efficiency suffix on marked duties, and on unmarked ones only when the block quotes both kinds. |
| `src/motor_ai_sim/support_prompt.py:52` (4) | no | Confirmed: UI/support instruction strings only; numerical motor context comes from support_catalog with per-duty basis. |
| `src/motor_ai_sim/sweep_rebuild.py:42` (13) | yes | Fixed in `src/motor_ai_sim/sweep_rebuild.py:192`: reconstruction keeps marker, rejects wrong-convention cache, groups Pareto fronts by convention; absent shaft P stays absent. |
| `src/motor_ai_sim/sweep_resume.py:234` (1) | no | Confirmed: resume carries requested efficiency threshold in worker kwargs and restores whole scan point dictionaries; no numeric projection. |
| `src/motor_ai_sim/terminals.py:242` (8) | yes | Fixed in `src/motor_ai_sim/terminals.py:279`: marked terminal budgets use one coupling power/efficiency pair, preserve zero, refuse missing coupling eta, and carry basis; old arithmetic unchanged. |
| `src/motor_ai_sim/thermal_duty_cycle.py:532` (1) | yes | Confirmed: thermal-duty-cycle adapter separates mechanical watts in thermal nodes for callers; no shaft-output reader. |
| `src/motor_ai_sim/thermal_heat_paths.py:473` (2) | no | Confirmed: heat-path annotation quotes fin efficiency from thermal stub output, unrelated to motor shaft convention. |
| `web/src/components/catalog/CatalogByDie.tsx:237` (2) | yes | Confirmed: mixed catalog blocks identify earlier-basis power/torque/efficiency cells and sort convention groups separately. |
| `web/src/components/catalog/FamilyCatalog.tsx:106` (1) | no | Confirmed: local DutyResult type only; FamilyCatalog passes whole duty objects to marker-aware catalog views, no runtime stripping. |
| `web/src/components/common/ActiveFamilyStrip.tsx:74` (5) | yes | Confirmed: marked stored shaft power bypasses legacy abs(P)*k and the saved duty result carries shaft_conv. |
| `web/src/components/common/motorSettings.ts:74` (3) | yes | Confirmed: numbers-only summary projection copies stored power and adds shaft_conv for marked runs. |
| `web/src/components/compare/ChargePanel.tsx:114` (1) | no | Confirmed: ChargePanel renders generatorCharge analytic passport result; it never consumes marked FEM output. |
| `web/src/components/compare/ComparePanel.tsx:189` (11) | yes | Fixed: marked Power reads stored P_mech_W, PD uses stored mass-basis ratios without field-power fallback, and torque tooltip says Field torque. |
| `web/src/components/compare/ConfiguratorPanel.tsx:1218` (12) | yes | Fixed in `web/src/components/compare/ConfiguratorPanel.tsx:2286`: draft FEM headline tooltip names field torque, pre-bearing power and coupling efficiency; scaleMotor paths remain independent. r7: the draft headline tooltip only for a marked headline. |
| `web/src/components/compare/EfficiencyMap.tsx:2` (8) | no | Confirmed: efficiency cells call scaleMotor on passport/knobs and read that independent analytic result, not saved summaries. |
| `web/src/components/compare/PerformanceCharts.tsx:40` (4) | no | Confirmed: speed curves call scaleMotor for each rpm, so power/efficiency and battery input share the same analytic source. |
| `web/src/components/compare/PointsConfigure.tsx:177` (3) | yes | Confirmed: PointsConfigure explicitly identifies comparison/panel markers and labels mixed-convention steps without ranking them. |
| `web/src/components/compare/PropellerLoadChart.tsx:3` (1) | no | Confirmed: PropellerLoadChart consumes propeller_s1 rows through propellerLoadChart helpers, not marked EM summaries. |
| `web/src/components/compare/addToCompare.ts:120` (2) | no | Confirmed: readShownSummary/emSummaryIfLive return the complete saved summary and add-to-compare stores it whole. |
| `web/src/components/controller/ControllerPanel.tsx:903` (1) | yes | Fixed in `web/src/components/controller/ControllerPanel.tsx:903`: wall-to-shaft efficiency tooltip shows the stored route source basis; no numeric or inverter-loss arithmetic changes. r7: an unmarked record keeps its pre-PR tile tip; the comparison columns come from `controllerCompareColumns`. |
| `web/src/components/controller/compareRows.ts:66` (1) | yes | Fixed in `web/src/components/controller/compareRows.ts:66`: stacked controller comparisons carry a visible shaft-basis input column from route sources; numeric efficiency remains the supplied result. r7: only marked rows carry `shaft_basis`; the column is shown only when a stacked row is marked. |
| `web/src/components/controller/controllerApi.ts:132` (7) | no | Confirmed: ControllerResult includes sources/point Record dictionaries and retains the route basis; other hits are thermal fin settings. |
| `web/src/components/landing/PublicMotors.tsx:59` (1) | yes | Fixed in `web/src/components/landing/PublicMotors.tsx:59`: public card tooltip names the rated efficiency convention carried by landingMotors. r7: the basis in the hover on marked motors, and on unmarked ones only when the list holds both kinds. |
| `web/src/components/mechanical/api.ts:823` (6) | yes | Fixed in `web/src/components/mechanical/api.ts:9`: mechanical stored-torque readers refuse marked missing coupling power/speed instead of falling back to field torque. |
| `web/src/components/simulation/FemAnimationViewer.tsx:36` (10) | yes | Fixed in `web/src/components/simulation/FemAnimationViewer.tsx:70`: animation payload carries marked basis and refuses missing marked power instead of Tfield*omega; unmarked fallback remains. |
| `web/src/components/simulation/FemFieldChart.tsx:243` (5) | yes | Fixed in `web/src/components/simulation/FemFieldChart.tsx:245`: field snapshot preserves marker, missing marked power stays NaN, diagnostic efficiency tooltip names basis. r7: efficiency tile tooltip names the basis only on a marked run. |
| `web/src/components/simulation/SimulationPanel.tsx:164` (6) | yes | Confirmed: marked motor load fitting selects stored shaft torque when available; earlier/generator fitting retains its field-torque basis. |
| `web/src/components/simulation/SummaryTable.tsx:48` (13) | yes | Confirmed: summary type preserves convention and passes the whole summary plus mechanical-loss block to marker-aware tile/balance readers. |
| `web/src/components/simulation/coupledApi.ts:70` (11) | no | Confirmed: coupledApi preserves the full coupling object; rating power has explicit basis/torque_basis and controllerTerm names wall-to-shaft efficiency, no T-to-P reconstruction. |
| `web/src/components/simulation/fem-types.ts:15` (3) | yes | Fixed in `web/src/components/simulation/fem-types.ts:21`: FEM payload type admits optional shaft_convention for field/animation diagnostic readers. |
| `web/src/components/sweep/AutoOptimizePanel.tsx:413` (23) | yes | Fixed in `web/src/components/sweep/AutoOptimizePanel.tsx:1`: optimizer cloud uses field-torque helper that refuses marked density reconstruction; field torque is labelled; table carries basis. r7: field-torque labels, cloud tooltip basis and design/best tooltips only when a marked design is on the chart / in the pair. |
| `web/src/components/sweep/AutoPointsTable.tsx:33` (5) | yes | Fixed in `web/src/components/sweep/AutoPointsTable.tsx:4`: table reads stored marked watts, shows missing as dash and explicit field-torque/shaft-power columns. r7: 'Field torque' / 'Shaft power' headers only when a marked point is in the table. 2026-11-28 (owner): no basis column; a table that mixes the bases carries one warning chip (`auto-shaft-basis-mixed`) above it. |
| `web/src/components/sweep/DOEPanel.tsx:141` (1) | yes | Confirmed: DOE importance uses a homogeneous fresh sample set; generic surrogate selects one basis before training. |
| `web/src/components/sweep/DescentPanel.tsx:44` (27) | yes | Fixed in `web/src/components/sweep/DescentPanel.tsx:44`: restored load-line overlays are split and labelled by basis before interpolation; scatter projections carry marker/power and tooltips show basis. r7: load-line split / names, baseline legend tooltip and point tooltip basis only when a marked design is on the chart. r8: load-line, picked and interpolated rated scatter payloads retain the marker; history metric tooltip labels its displayed rows only when any is marked. Actual projections/interpolation/formatters executed in both locales. Broader scatter mixed-scope follow-up remains unchanged. |
| `web/src/components/sweep/Scatter3D.tsx:15` (6) | yes | Fixed in `web/src/components/sweep/Scatter3D.tsx:15`: 3D scatter point type and hover retain/name each point convention; no scaling of the supplied td/eff. r7: hover basis on marked points, and on unmarked ones only when the cloud holds both kinds. |
| `web/src/components/sweep/SweepConfigPanel.tsx:495` (2) | no | Confirmed: renders variable-importance weights, not shaft quantities; upstream surrogate filters to the selected convention. |
| `web/src/components/sweep/SweepStudyPanel.tsx:136` (51) | yes | Fixed: marked missing power stays missing and Apply requires stored power; unmarked stored zero retains the historical T*omega fallback. r7: '—' for missing values only on marked rows; unmarked cells keep their pre-PR text. r8: chart rows carry source convention; tooltip labels marked/mixed rendered rows. 2026-11-28 (owner): the table has no basis column and its export none; a result that mixes the bases carries the warning chip (`sweep-shaft-basis-mixed`) above the chart. Old-only columns/text are preserved; actual projections/columns/formatters tested in both locales. |
| `web/src/components/system/systemComposeTypes.ts:311` (2) | no | Confirmed: static component palette defines a reference gearbox parameter named efficiency, not a motor-result reader. |
| `web/src/components/thermal/ThermalPanel.tsx:1804` (3) | no | Confirmed: ThermalPanel displays fin effectiveness and heat conductance from thermal results, not shaft output. |
| `web/src/components/thermal/dutyCycleTorque.ts:31` (6) | yes | Confirmed: marked duty-cycle load uses coupling power divided by speed, including stored friction once; old torque path remains. |
| `web/src/components/thermal/heatPaths.ts:471` (2) | no | Confirmed: heatPaths formats shaft-stub fin effectiveness, not mechanical efficiency. |
| `web/src/components/thermal/types.ts:141` (2) | no | Confirmed: thermal type interfaces describe fin effectiveness; no mechanical output/efficiency reader. |
| `web/src/lib/agentDrafts.ts:57` (2) | yes | Fixed in `web/src/lib/agentDrafts.ts:57`: DraftHeadline admits marker and torque/power bases; bestDraftResult returns selected headline whole. |
| `web/src/lib/appliedAutoSave.ts:32` (1) | no | Confirmed: auto-save forwards metrics Record unchanged in request body; no key allowlist discards marker. |
| `web/src/lib/apply3d.ts:13` (19) | yes | Confirmed: marked shaft-side quantities bypass scaling while field quantities retain k; the application stamp prevents a second application. |
| `web/src/lib/autoPointPower.ts:3` (4) | yes | Fixed in `web/src/lib/autoPointPower.ts:3`: pure compact-point reader returns stored marked watts and field torque; unmarked formulas preserved. |
| `web/src/lib/catalogByDie.ts:83` (3) | yes | Confirmed: row projection preserves shaft_conv and power/torque/efficiency sorting first separates convention groups. |
| `web/src/lib/configuratorDrive.ts:10` (4) | no | Confirmed: drive interpolation consumes independent passport PWM variants; no marked FEM summaries are attached to those legacy models. |
| `web/src/lib/configuratorFlags.ts:3` (2) | no | Confirmed: feature flag documentation only; no result read. |
| `web/src/lib/configuratorTiles.ts:222` (4) | no | Confirmed: tuner tile arithmetic takes scaleMotor shaft power and passport PWM loss model; no saved marked summary input. |
| `web/src/lib/dutyEfficiency.ts:4` (2) | yes | Confirmed: marked non-generating motor power at or below zero yields zero efficiency; delivered-power and legacy formulas remain. |
| `web/src/lib/dutyLocalApply.ts:455` (5) | yes | Confirmed: marked compact duty without its full summary requests a re-solve instead of inventing achieved power from targets. |
| `web/src/lib/emResultTiles.ts:22` (19) | yes | Confirmed: marked torque and power tiles share the coupling boundary; field torque and unknown mechanical losses are labelled in tooltips. |
| `web/src/lib/endWindingPin.ts:8` (1) | no | Confirmed: copper end-turn request pinning only; efficiency hit is explanatory text. |
| `web/src/lib/fullMass.ts:23` (6) | no | Confirmed: perMass selects existing ratios by mass availability; complete summary remains with marker-aware emResultTiles caller. |
| `web/src/lib/generatorCharge.ts:113` (4) | no | Confirmed: generatorCharge accepts ScaledResult from scaleMotor and computes battery charging from its independent passport balance. |
| `web/src/lib/landingMotors.ts:9` (2) | yes | Fixed in `web/src/lib/landingMotors.ts:9`: public-row parser preserves optional shaft_conv so the card can label efficiency; old numeric formatting unchanged. |
| `web/src/lib/motorScaling.ts:198` (7) | no | Confirmed: scaleMotor is an independent passport/knob analytic producer; consumes no marked run summary or coupling record. |
| `web/src/lib/propellerDesign.ts:124` (1) | no | Confirmed: frontCost compares propeller thrust per shaft watt and cooling air, not motor summary efficiency. |
| `web/src/lib/propellerLoadChart.ts:2` (6) | no | Confirmed: chart derives propeller S1 shaft/input efficiency from the independent S1 model; no field-torque power substitution. |
| `web/src/lib/resultsPanel.ts:72` (3) | yes | Confirmed: panel projection carries its summary marker and removes a borrowed compact marker when the panel summary is unmarked. |
| `web/src/lib/shaftBalance.ts:6` (9) | yes | Confirmed: marked balance preserves signed net motor power and applies stored friction once; unmarked clamping remains unchanged. |
| `web/src/lib/shaftCoupling.ts:3` (3) | yes | Confirmed: coupling-torque helper accepts marked summaries only and derives from stored power, speed and mechanical-loss metadata. |
| `web/src/lib/shaftBasisLabel.ts:3` (2) | yes | Fixed in `web/src/lib/shaftBasisLabel.ts:13`: added in r7, the one display rule for the basis words (`isMarked`, `basesMixed`, `anyMarked`, `basisTag`); reads only the marker fields, no metric. |
| `web/src/lib/simPoints.ts:41` (4) | yes | Fixed in `web/src/lib/simPoints.ts:41`: efficiency tooltip appends translated run basis; duty target power/torque unchanged; both production callers supply translations. r7: the basis in the tooltip on marked efficiencies, and on unmarked ones only when the configuration's points (or this tooltip) mix bases. |
| `web/src/lib/sweepApply3d.ts:12` (6) | yes | Confirmed: marked points skip further k scaling and transfer complete convention keys into applied summaries. |
| `web/src/lib/sweepSettings.ts:118` (4) | yes | Confirmed: convention resolver reads full or compact marker; comparability rejects mixed groups before ranking. |
| `web/src/modules/development/comparisonRows.ts:69` (3) | yes | Confirmed: solved comparison projection includes shaftConv beside its unchanged displayed numbers. |
| `web/src/modules/development/motorGroups.ts:54` (2) | yes | Confirmed: averageEfficiency returns no average when result duties carry two conventions. |
| `web/src/modules/development/pointsLimited.ts:262` (1) | no | Confirmed: configureTileKey maps tile IDs to labels and mass scope; it does not strip the point object or convert numbers. |
| `web/src/modules/development/pointsOnly.ts:52` (1) | no | Confirmed: tileValue reads stored comparison numbers; PointsConfigure retains full point/panel and labels mixed conventions. |
| `web/src/modules/development/types.ts:45` (2) | no | Confirmed: type-only declarations keep comparison and panel summary as Records; runtime values/marker are not projected away. |
| `web/src/modules/gears/drivetrain.ts:34` (6) | yes | Confirmed: displays the route's motor point with its torque_basis chip (shaft / field legacy) and the marker; no arithmetic on motor quantities in the client. |
| `web/src/modules/gears/components/Results.tsx:54` (1) | no | Confirmed: gear Results table renders gear efficiency fields with availability, never EM output. |
| `web/src/modules/gears/types.ts:45` (2) | no | Confirmed: gearbox result interfaces describe independent gear efficiency; no motor EM summary fields. |
| `web/src/modules/gears/view.ts:9` (1) | no | Confirmed: gearbox view reads metric_availability for gear efficiency; not motor summary convention. |
| `web/src/modules/tests/components/TestsPanel.tsx:496` (1) | no | Confirmed: TestsPanel renders bench powerSummary/tableModel results; simulated rows retain comparison group basis, no raw marked summary read. |
| `web/src/modules/tests/tableModel.ts:404` (2) | no | Confirmed: bench powerSummary uses measured torque; powerRow model torque comes from marker-aware model_source/comparison coupling torque, with group basis. |
| `web/src/stores/motorStore.ts:121` (6) | yes | Confirmed: picked optimizer point and fallback best metrics both carry their source marker into the numbers-only snapshot. |
| `web/src/types/motor.ts:190` (4) | no | Confirmed: DesignMetrics declaration only; motorStore retains whole backend points and marker-aware Sweep reads actual runtime fields. |
| `src/motor_ai_sim/duty_fields.py:445` (1) | no | Confirmed: field snapshot copies raw T_avg_Nm with field quantities for mechanical diagnostics, without shaft-power synthesis. |
| `src/motor_ai_sim/passport_v1/psimap.py:249` (1) | no | Confirmed: interpolation samples raw solver T_avg_Nm for field torque/flux maps, not marked shaft summaries. |
| `src/motor_ai_sim/passport_v1/stages.py:146` (6) | no | Confirmed: MTPA bracket/confirmation diagnostics compare raw field torque from passport jobs; no shaft output or efficiency reconstruction. |
| `src/motor_ai_sim/simulation/static3d/band2d.py:12` (1) | no | Confirmed: T_avg_Nm appears in documentation of raw 2-D versus frozen-current 3-D field-torque comparison only. |
| `web/src/components/catalog/MyDesigns.tsx:107` (1) | no | Confirmed: saved design metrics render field T_avg_Nm and ripple only; no shaft power, efficiency or convention ranking. |
| `web/src/components/simulation/TransientCharts.tsx:92` (8) | no | Confirmed: raw torque curves, field-average diagnostics and harmonic percentages use raw transient data; no shaft output is synthesized. |
| `web/src/lib/designs.ts:16` (1) | no | Confirmed: saved-design type declares raw T_avg_Nm and ripple; storage forwards complete design documents. |
| `web/src/lib/rippleAccuracy.ts:18` (1) | no | Confirmed: RippleRefined type admits raw field T_avg_Nm for ripple-quality provenance; no shaft-power reader. |
| `web/src/modules/development/components/LoadSection.tsx:15` (4) | no | Confirmed: point torque is a manually entered load requirement; no simulated motor summary is consumed. |
| `web/src/modules/development/components/specWizard/OperatingChart.tsx:14` (2) | no | Confirmed: chart points show specification torque requirements and durations, not solved motor torque. |
| `web/src/modules/development/components/specWizard/operatingPoints.ts:157` (3) | no | Confirmed: chart scaling reads requested specification/mission torque coordinates only, independent of solver convention. |
| `web/src/modules/development/components/specWizard/SpecTable.tsx:71` (1) | no | Confirmed: table renders specification point torque and power requirements, not achieved motor summary values. |
