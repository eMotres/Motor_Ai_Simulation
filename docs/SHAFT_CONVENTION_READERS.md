# Shaft-power convention: reader inventory

PR #900 (marker `summary["shaft_convention"] == "fe_on_shaft_k3d_v1"`, `src/motor_ai_sim/shaft_convention.py`).
Every file in `src` and `web/src` (tests and locales excluded) that matches
`P_shaft_W|P_shaft_net_W|T_shaft_Nm|torque_per_mass|power_per_mass|torque_to_mass|efficiency(_shaft|_pct)|eta_shaft|eta_em|P_mech_W`.
"Reads the marker" = the file references `shaft_convention` / `shaft_conv` / a `shaft_convention` helper. The first matching
line and the number of matching lines are given so a reviewer can sample the file.

Not matched by the pattern but changed: `web/src/components/compare/PointsConfigure.tsx` (steps table marks earlier-convention
rows when a table mixes them).

Regenerate: the `git grep` above, then classify each file (this table, 155 files, 2026-11-26).

| file:first line (matches) | reads the marker | disposition |
|---|---|---|
| `src/motor_ai_sim/agent_designs.py:1098` (4) | yes | yes (fixed r4 C6): marked run keeps shaft_conv; torque labelled 2-D field, power labelled shaft before bearings/windage; shaft efficiency at the coupling (generator E/(P+X)) |
| `src/motor_ai_sim/assistant_estimate.py:208` (1) | no | no: independent analytic/scaling model from passport constants, never reads a stored marked P - not needed |
| `src/motor_ai_sim/charge_analytic.py:244` (5) | no | no: independent analytic/scaling model from passport constants, never reads a stored marked P - not needed |
| `src/motor_ai_sim/configure_anchor.py:78` (1) | no | no: lists or forwards stored values unchanged - not needed |
| `src/motor_ai_sim/contracts/adapters.py:199` (2) | no | no: the full raw summary (with its marker) travels; scalar-only exports of field torque beside shaft power need an explicit basis before any cross-run comparison - follow-up |
| `src/motor_ai_sim/contracts/conformance/drivetrain.py:104` (8) | no | no: contract/IR pass-through of named fields (no arithmetic on P/T) - not needed |
| `src/motor_ai_sim/contracts/drivetrain_ir.py:8` (9) | no | no: contract/IR pass-through of named fields (no arithmetic on P/T) - not needed |
| `src/motor_ai_sim/contracts/result_ir.py:31` (1) | no | no: contract/IR pass-through of named fields (no arithmetic on P/T) - not needed |
| `src/motor_ai_sim/contracts/_selfcheck.py:105` (3) | no | no: contract/IR pass-through of named fields (no arithmetic on P/T) - not needed |
| `src/motor_ai_sim/coupled_continuous_rating.py:11` (17) | yes | yes |
| `src/motor_ai_sim/coupled_pwm_final.py:174` (15) | yes | yes |
| `src/motor_ai_sim/datasheet.py:383` (23) | yes | yes |
| `src/motor_ai_sim/design_agent/prompt.py:10` (2) | no | no: independent analytic/scaling model from passport constants, never reads a stored marked P - not needed |
| `src/motor_ai_sim/design_agent/tools.py:81` (6) | no | no: independent analytic/scaling model from passport constants, never reads a stored marked P - not needed |
| `src/motor_ai_sim/development/motors.py:29` (24) | yes | yes |
| `src/motor_ai_sim/development/motor_view.py:182` (16) | yes | yes |
| `src/motor_ai_sim/development/sim_view.py:106` (1) | no | no: lists or forwards stored values unchanged - not needed |
| `src/motor_ai_sim/development/spec_order.py:255` (1) | no | no: lists or forwards stored values unchanged - not needed |
| `src/motor_ai_sim/digest/collect.py:254` (1) | no | no: lists or forwards stored values unchanged - not needed |
| `src/motor_ai_sim/digest/readable.py:118` (10) | yes | yes |
| `src/motor_ai_sim/duty_results.py:110` (7) | yes | yes |
| `src/motor_ai_sim/gears/gearbox.py:325` (11) | no | no: independent gearbox model (its own mesh / bearing losses on given torques) - not needed |
| `src/motor_ai_sim/gears/loads.py:113` (1) | no | no: independent gearbox model (its own mesh / bearing losses on given torques) - not needed |
| `src/motor_ai_sim/gears/losses.py:3` (5) | no | no: independent gearbox model (its own mesh / bearing losses on given torques) - not needed |
| `src/motor_ai_sim/gears/planetary_equilibrium.py:19` (3) | no | no: independent gearbox model (its own mesh / bearing losses on given torques) - not needed |
| `src/motor_ai_sim/gears/presets.py:131` (1) | no | no: independent gearbox model (its own mesh / bearing losses on given torques) - not needed |
| `src/motor_ai_sim/gears/schema.py:52` (1) | no | no: independent gearbox model (its own mesh / bearing losses on given torques) - not needed |
| `src/motor_ai_sim/geometry_validation.py:15` (1) | no | no: lists or forwards stored values unchanged - not needed |
| `src/motor_ai_sim/inverter/igbt.py:985` (8) | no | no: inverter loss model (electrical side only) - not needed |
| `src/motor_ai_sim/inverter/losses.py:48` (26) | no | no: inverter loss model (electrical side only) - not needed |
| `src/motor_ai_sim/mcp_app.py:217` (4) | no | no: one stored value per motor, no second factor, no cross-motor ranking - label follow-up |
| `src/motor_ai_sim/mcp_discovery.py:108` (4) | no | no: one stored value per motor, no second factor, no cross-motor ranking - label follow-up |
| `src/motor_ai_sim/mcp_tools.py:10` (6) | yes | yes |
| `src/motor_ai_sim/mech_losses.py:30` (2) | no | no: bearing/windage or heat paths from rpm and loads, not from P_shaft - not needed |
| `src/motor_ai_sim/modules/component_adapters.py:130` (1) | no | no: contract/IR pass-through of named fields (no arithmetic on P/T) - not needed |
| `src/motor_ai_sim/modules/component_plugins/gearbox_single_stage.py:18` (1) | no | no: contract/IR pass-through of named fields (no arithmetic on P/T) - not needed |
| `src/motor_ai_sim/modules/drivetrain/adapters.py:109` (2) | no | no: contract/IR pass-through of named fields (no arithmetic on P/T) - not needed |
| `src/motor_ai_sim/modules/drivetrain/system.py:67` (5) | no | no: contract/IR pass-through of named fields (no arithmetic on P/T) - not needed |
| `src/motor_ai_sim/modules/motor_mass.py:739` (4) | yes | yes |
| `src/motor_ai_sim/modules/optimization.py:26` (1) | no | no: ranks points of ONE run; every FEM eval carries the marker and the eval cache key is v8 - not needed |
| `src/motor_ai_sim/modules/surrogate.py:27` (2) | no | no: ranks points of ONE run; every FEM eval carries the marker and the eval cache key is v8 - not needed |
| `src/motor_ai_sim/motor_tests/compare.py:49` (2) | no | no: pass-through, no arithmetic on P/T/eta - not needed |
| `src/motor_ai_sim/motor_tests/comparison.py:749` (15) | yes | yes: a marked duty is taken as stored (no second k); the basis quotes the run's own shaft_k3d (r4), never today's passport |
| `src/motor_ai_sim/motor_tests/model_source.py:220` (12) | yes | yes: row carries shaft_conv / shaft_k3d; compact rows decoded with summary_from_compact (r4 C1); a marked zero shaft efficiency stays 0 (r4 C2) |
| `src/motor_ai_sim/optimization/design_eval.py:6` (11) | no | no: ranks points of ONE run; every FEM eval carries the marker and the eval cache key is v8 - not needed |
| `src/motor_ai_sim/optimization/doe.py:6` (3) | no | no: ranks points of ONE run; every FEM eval carries the marker and the eval cache key is v8 - not needed |
| `src/motor_ai_sim/optimization/eval_log.py:268` (2) | no | no: ranks points of ONE run; every FEM eval carries the marker and the eval cache key is v8 - not needed |
| `src/motor_ai_sim/optimization/optimizer.py:30` (3) | no | no: ranks points of ONE run; every FEM eval carries the marker and the eval cache key is v8 - not needed |
| `src/motor_ai_sim/optimization/pareto.py:8` (9) | no | no: ranks points of ONE run; every FEM eval carries the marker and the eval cache key is v8 - not needed |
| `src/motor_ai_sim/optimization/refine_proc.py:196` (10) | yes | yes |
| `src/motor_ai_sim/optimization/surrogate.py:12` (1) | no | no: ranks points of ONE run; every FEM eval carries the marker and the eval cache key is v8 - not needed |
| `src/motor_ai_sim/optimize_headless.py:413` (1) | yes | yes |
| `src/motor_ai_sim/part_states.py:16` (1) | no | no: lists or forwards stored values unchanged - not needed |
| `src/motor_ai_sim/passport.py:552` (1) | no | no: independent passport model with its own loss balance; a marked run is never attached to an old passport (routes/family.py) - follow-up |
| `src/motor_ai_sim/passport_store.py:8` (1) | no | no: independent passport model with its own loss balance; a marked run is never attached to an old passport (routes/family.py) - follow-up |
| `src/motor_ai_sim/passport_v1/card.py:162` (13) | no | no: independent passport model with its own loss balance; a marked run is never attached to an old passport (routes/family.py) - follow-up |
| `src/motor_ai_sim/passport_v1/fullcard.py:369` (27) | no | no: independent passport model with its own loss balance; a marked run is never attached to an old passport (routes/family.py) - follow-up |
| `src/motor_ai_sim/passport_v1/jobs.py:134` (1) | no | no: independent passport model with its own loss balance; a marked run is never attached to an old passport (routes/family.py) - follow-up |
| `src/motor_ai_sim/passport_v1/losses.py:20` (5) | no | no: independent passport model with its own loss balance; a marked run is never attached to an old passport (routes/family.py) - follow-up |
| `src/motor_ai_sim/passport_v1/pwm_parts.py:71` (1) | no | no: independent passport model with its own loss balance; a marked run is never attached to an old passport (routes/family.py) - follow-up |
| `src/motor_ai_sim/passport_v1/report_md.py:127` (6) | no | no: independent passport model with its own loss balance; a marked run is never attached to an old passport (routes/family.py) - follow-up |
| `src/motor_ai_sim/passport_v1/snapshot.py:262` (2) | yes | yes (r4): stored_summary carries shaft_convention / shaft_k3d / op_mode on a marked summary only (earlier snapshots keep their shape) |
| `src/motor_ai_sim/passport_v1/stage3.py:589` (8) | no | no: independent passport model with its own loss balance; a marked run is never attached to an old passport (routes/family.py) - follow-up |
| `src/motor_ai_sim/propeller_bemt.py:612` (6) | no | no: independent analytic/scaling model from passport constants, never reads a stored marked P - not needed |
| `src/motor_ai_sim/propeller_design_api.py:412` (1) | no | no: independent analytic/scaling model from passport constants, never reads a stored marked P - not needed |
| `src/motor_ai_sim/public_motors.py:15` (3) | no | no: one stored value per motor, no second factor, no cross-motor ranking - label follow-up |
| `src/motor_ai_sim/report_customer.py:318` (1) | yes | yes |
| `src/motor_ai_sim/report.py:661` (184) | yes | yes |
| `src/motor_ai_sim/routes/admin.py:935` (1) | no | no: lists or forwards stored values unchanged - not needed |
| `src/motor_ai_sim/routes/bearings.py:136` (1) | no | no: bearing/windage or heat paths from rpm and loads, not from P_shaft - not needed |
| `src/motor_ai_sim/routes/controller.py:186` (34) | yes | yes: torque_Nm = the coupling torque on a marked record (test) |
| `src/motor_ai_sim/routes/coupled.py:359` (43) | yes | yes |
| `src/motor_ai_sim/routes/family.py:1396` (8) | yes | yes |
| `src/motor_ai_sim/routes/mechanical.py:273` (1) | yes | yes |
| `src/motor_ai_sim/routes/optimization.py:5` (99) | yes | yes |
| `src/motor_ai_sim/routes/presets.py:216` (14) | yes | yes |
| `src/motor_ai_sim/routes/saved_sims.py:4` (1) | no | no: whole-summary storage keeps the supplied marker - not needed |
| `src/motor_ai_sim/routes/simulation.py:353` (42) | yes | yes |
| `src/motor_ai_sim/routes/thermal.py:6601` (1) | no | no: bearing/windage or heat paths from rpm and loads, not from P_shaft - not needed |
| `src/motor_ai_sim/shaft_convention.py:6` (14) | yes | yes |
| `src/motor_ai_sim/simulation/battery.py:118` (1) | no | no: producer (solver kernel); the marker is stamped by the summary builder after it - not needed |
| `src/motor_ai_sim/simulation/cooling_models.py:472` (8) | no | no: producer (solver kernel); the marker is stamped by the summary builder after it - not needed |
| `src/motor_ai_sim/simulation/eddy_solver_2d.py:433` (2) | no | no: producer (solver kernel); the marker is stamped by the summary builder after it - not needed |
| `src/motor_ai_sim/simulation/fem_solver_2d.py:1375` (10) | no | no: producer (solver kernel); the marker is stamped by the summary builder after it - not needed |
| `src/motor_ai_sim/simulation/postprocess.py:308` (4) | no | no: producer (solver kernel); the marker is stamped by the summary builder after it - not needed |
| `src/motor_ai_sim/simulation/sb_domains.py:38` (1) | no | no: producer (solver kernel); the marker is stamped by the summary builder after it - not needed |
| `src/motor_ai_sim/simulation/solver_2d.py:6` (6) | no | no: producer (solver kernel); the marker is stamped by the summary builder after it - not needed |
| `src/motor_ai_sim/simulation/time_periodic.py:1469` (1) | no | no: producer (solver kernel); the marker is stamped by the summary builder after it - not needed |
| `src/motor_ai_sim/support_catalog.py:23` (5) | no | no: one stored value per motor, no second factor, no cross-motor ranking - label follow-up |
| `src/motor_ai_sim/support_prompt.py:52` (4) | no | no: one stored value per motor, no second factor, no cross-motor ranking - label follow-up |
| `src/motor_ai_sim/sweep_rebuild.py:43` (6) | no | no: ranks points of ONE run; every FEM eval carries the marker and the eval cache key is v8 - not needed |
| `src/motor_ai_sim/terminals.py:195` (10) | no | no: adds the terminal loss to the summary's own (eta, P) pair, no second k; a zero eta falls back with the explicit 'electromagnetic' basis - not needed |
| `src/motor_ai_sim/thermal_heat_paths.py:473` (1) | no | no: bearing/windage or heat paths from rpm and loads, not from P_shaft - not needed |
| `web/src/components/catalog/CatalogByDie.tsx:67` (2) | yes | yes |
| `web/src/components/catalog/FamilyCatalog.tsx:106` (1) | no | no: one stored value per motor, no second factor, no cross-motor ranking - label follow-up |
| `web/src/components/common/ActiveFamilyStrip.tsx:55` (10) | yes | yes |
| `web/src/components/common/motorSettings.ts:74` (2) | yes | yes |
| `web/src/components/compare/addToCompare.ts:168` (2) | no | no: whole-summary storage keeps the supplied marker - not needed |
| `web/src/components/compare/ChargePanel.tsx:114` (1) | no | no: independent analytic/scaling model from passport constants, never reads a stored marked P - not needed |
| `web/src/components/compare/ComparePanel.tsx:155` (6) | yes | yes |
| `web/src/components/compare/ConfiguratorPanel.tsx:7` (18) | no | no: independent analytic/scaling model from passport constants, never reads a stored marked P - not needed |
| `web/src/components/compare/EfficiencyMap.tsx:2` (8) | no | no: independent analytic/scaling model from passport constants, never reads a stored marked P - not needed |
| `web/src/components/compare/PerformanceCharts.tsx:5` (5) | no | no: independent analytic/scaling model from passport constants, never reads a stored marked P - not needed |
| `web/src/components/compare/PropellerLoadChart.tsx:3` (1) | no | no: independent analytic/scaling model from passport constants, never reads a stored marked P - not needed |
| `web/src/components/controller/compareRows.ts:81` (2) | no | no: reads p_ac_W / torque_Nm from routes/controller.py, which reads the marker |
| `web/src/components/controller/controllerApi.ts:132` (2) | no | no: type declarations / viewers; the marker travels in the same object |
| `web/src/components/controller/ControllerPanel.tsx:495` (4) | no | no: reads p_ac_W / torque_Nm from routes/controller.py, which reads the marker |
| `web/src/components/landing/PublicMotors.tsx:3` (1) | no | no: one stored value per motor, no second factor, no cross-motor ranking - label follow-up |
| `web/src/components/simulation/coupledApi.ts:140` (11) | no | no: type declarations / viewers; the marker travels in the same object |
| `web/src/components/simulation/FemAnimationViewer.tsx:69` (4) | no | no: type declarations / viewers; the marker travels in the same object |
| `web/src/components/simulation/FemFieldChart.tsx:245` (3) | no | no: single-run diagnostics copy the numeric efficiency; no arithmetic; its efficiency label does not name the basis - follow-up |
| `web/src/components/simulation/fem-types.ts:20` (2) | no | no: type declarations / viewers; the marker travels in the same object |
| `web/src/components/simulation/SimulationPanel.tsx:163` (14) | yes | yes |
| `web/src/components/simulation/SummaryTable.tsx:5` (24) | yes | yes |
| `web/src/components/sweep/AutoOptimizePanel.tsx:513` (11) | no | no: one optimiser run = one convention (eval cache v8) - not needed |
| `web/src/components/sweep/AutoPointsTable.tsx:65` (1) | no | no: one optimiser run = one convention (eval cache v8) - not needed |
| `web/src/components/sweep/DescentPanel.tsx:35` (35) | no | no: one optimiser run = one convention (eval cache v8) - not needed |
| `web/src/components/sweep/DOEPanel.tsx:4` (2) | no | no: one optimiser run = one convention (eval cache v8) - not needed |
| `web/src/components/sweep/Scatter3D.tsx:2` (1) | no | no: one optimiser run = one convention (eval cache v8) - not needed |
| `web/src/components/sweep/SweepConfigPanel.tsx:495` (2) | no | no: one optimiser run = one convention (eval cache v8) - not needed |
| `web/src/components/sweep/SweepStudyPanel.tsx:2` (38) | yes | yes |
| `web/src/components/system/systemComposeTypes.ts:311` (2) | no | no: contract/IR pass-through of named fields (no arithmetic on P/T) - not needed |
| `web/src/components/thermal/dutyCycleTorque.ts:75` (2) | yes | yes |
| `web/src/components/thermal/heatPaths.ts:482` (1) | no | no: bearing/windage or heat paths from rpm and loads, not from P_shaft - not needed |
| `web/src/components/thermal/ThermalPanel.tsx:1815` (1) | no | no: bearing/windage or heat paths from rpm and loads, not from P_shaft - not needed |
| `web/src/lib/appliedAutoSave.ts:32` (1) | no | no: pass-through of stored fields to a component that reads the marker - not needed |
| `web/src/lib/apply3d.ts:5` (16) | yes | yes |
| `web/src/lib/catalogByDie.ts:15` (7) | yes | yes |
| `web/src/lib/configuratorDrive.ts:10` (4) | no | no: independent analytic/scaling model from passport constants, never reads a stored marked P - not needed |
| `web/src/lib/configuratorFlags.ts:3` (2) | no | no: independent analytic/scaling model from passport constants, never reads a stored marked P - not needed |
| `web/src/lib/configuratorTiles.ts:5` (14) | no | no: independent analytic/scaling model from passport constants, never reads a stored marked P - not needed |
| `web/src/lib/dutyEfficiency.ts:1` (3) | yes | yes |
| `web/src/lib/dutyLocalApply.ts:459` (5) | yes | yes (r4 C5): a marked duty without its full summary is not rebuilt from requested targets; asks for a re-solve |
| `web/src/lib/emResultTiles.ts:27` (23) | yes | yes |
| `web/src/lib/endWindingPin.ts:8` (1) | no | no: pass-through of stored fields to a component that reads the marker - not needed |
| `web/src/lib/fullMass.ts:23` (6) | no | no: pass-through of stored fields to a component that reads the marker - not needed |
| `web/src/lib/generatorCharge.ts:113` (4) | no | no: independent analytic/scaling model from passport constants, never reads a stored marked P - not needed |
| `web/src/lib/landingMotors.ts:12` (3) | no | no: one stored value per motor, no second factor, no cross-motor ranking - label follow-up |
| `web/src/lib/motorScaling.ts:5` (10) | no | no: independent analytic/scaling model from passport constants, never reads a stored marked P - not needed |
| `web/src/lib/propellerDesign.ts:124` (1) | no | no: independent analytic/scaling model from passport constants, never reads a stored marked P - not needed |
| `web/src/lib/propellerLoadChart.ts:2` (6) | no | no: independent analytic/scaling model from passport constants, never reads a stored marked P - not needed |
| `web/src/lib/resultsPanel.ts:69` (2) | yes | yes |
| `web/src/lib/shaftBalance.ts:6` (13) | yes | yes |
| `web/src/lib/shaftCoupling.ts:8` (1) | yes | yes |
| `web/src/lib/simPoints.ts:40` (4) | no | no: pass-through of stored fields to a component that reads the marker - not needed |
| `web/src/lib/sweepApply3d.ts:18` (2) | yes | yes |
| `web/src/lib/sweepSettings.ts:120` (1) | yes | yes |
| `web/src/modules/development/comparisonRows.ts:62` (4) | yes | yes |
| `web/src/modules/development/motorGroups.ts:53` (3) | yes | yes: averageEfficiency never averages across two conventions (test) |
| `web/src/modules/development/pointsLimited.ts:262` (1) | no | no: pass-through of stored fields to a component that reads the marker - not needed |
| `web/src/modules/development/pointsOnly.ts:34` (2) | no | no: pass-through of stored fields to a component that reads the marker - not needed |
| `web/src/modules/development/types.ts:45` (2) | no | no: type declarations / viewers; the marker travels in the same object |
| `web/src/modules/gears/types.ts:45` (1) | no | no: independent gearbox model (its own mesh / bearing losses on given torques) - not needed |
| `web/src/stores/motorStore.ts:121` (4) | yes | yes |
| `web/src/types/motor.ts:191` (4) | no | no: type declarations / viewers; the marker travels in the same object |
