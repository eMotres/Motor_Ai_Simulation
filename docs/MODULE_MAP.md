# AeroStator functional module map and post-launch extraction plan

Baseline: **2026-10-15, `124f5cbf4dbbe8687bb68846563cdefb62b1b81b`**, worktree `C:\Users\vadim\Projects\mas_modmap`. Prepared by **GPT-6 Astra**, static analysis, no escalation. This is an ownership proposal, not a refactor. Preserve the Sunday 18 October 14:00 freeze and Monday 19 October launch; begin extraction after launch stabilisation.

## 1. Evidence and scope

The initial worktree was clean. Inspection used `git ls-files`, `git grep`, source reading, newline counts and Python AST parsing. **No application imports, tests, servers, API calls, solves, builds or commits.** `wc -l` failed with Windows sandbox `CreateFileMapping` error 5; Python byte-newline counting supplies the same counting convention. Blank lines and comments count.

| Scope | Source files | Lines |
|---|---:|---:|
| `src/` | 358 | 234,431 |
| `web/src/` | 500 | 109,017 |
| `tests/` | 362 | 121,255 |
| `scripts/` | 87 | 22,074 |
| Whole tracked repository | 1,479 | 502,339 |

The repository total also includes CAD/deployment/experiment/root scripts. Source extensions: `.py`, `.ts`, `.tsx`, `.js`, `.mjs`, `.cjs`, `.css`, `.sh`, `.ps1`, `.FCMacro`, `.html`. JSON/YAML/CSV, notebooks and Markdown are data/documents, excluded from the source census. **36 files exceed 1,500 lines**, including three tests and one sandbox script; section 5 lists every one. Static extraction found **451 route decorator declarations** and **276 `from ...routes...` imports**. These are declaration/import counts, not unique deployed endpoints or 276 proven violations.

Evidence companions: `MODULE_MAP.inventory.json` contains all source counts/hashes and giant top-level symbols; `MODULE_MAP.state_evidence.json` contains source literals/store registrations, not live state; `MODULE_MAP.routes.json` contains route declarations/imports. No private records or credentials were inspected. Production and private-repository contents were not audited. Private design-edit rules are not triggered: no motor, die, CAD design or board is edited.

Exact path abbreviations: **B/** = `src/motor_ai_sim/`; **W/** = `web/src/`; **T/** = `tests/`. Globs group current files. Named tests are existing review targets, **not passed tests**. All line anchors refer to this baseline. Proposed services/DTOs/public entry points are not represented as existing implementations.

## 2. Fourteen functional modules

Keep one Python distribution and one web deployment initially. A module is an ownership boundary, not a microservice. Feature UI belongs to its domain; M14 owns the shell and shared presentation only. Existing `modules/` is a registration/SDK layer, not already a clean implementation of this map.

### M01 — Geometry, materials and mesh construction

**Purpose:** validated machine parameters, stator/rotor/winding construction, material assignment, cross-sections and mesh generation. Construction and feasibility arithmetic belong together. Meshing is a separate internal subpackage shared by the physics domains.

**Current files:** B/`geometry/`, `cadquery_geometry.py` (3,932), `geometry_validation.py` (1,682), `geometry_constraints.py`, `geometry_proposal.py`, `services/geometry_service.py`, `simulation/geometry_2d.py`, `simulation/mesher.py` (4,419), `simulation/geo_mesh.py` (2,959), `simulation/geo_mesh_gmsh.py`, `simulation/geo_mesh_netgen.py`, `simulation/gmsh_worker*.py`, `simulation/iron_template.py`, `mesh_settings.py`, `materials.py`, `materials_store.py`, `material_context.py`, `part_materials.py`, `part_states.py`, `part_tree.py`, `wire_stock.py`, `winding.py`, `winding_sets.py`; geometry/construction/wire routes and material/winding/mesh handlers in `api.py`. W/`components/geometry/`, `parameters/`, `materials/`, `mesh/` (`MeshPanel.tsx`: 1,503), `viewer3d/`, `lib/slotMouthFit.ts`, `lib/meshSettings.ts`, geometry slices of `stores/motorStore.ts`. T/`test_geometry_validation.py`, `test_slot_mouth_infeasible.py`, `test_stator_cutter_union.py`, `test_stator_outline_symmetry.py`, `test_geo_mesh_netgen.py`, `test_materials_private_overlay.py`, `test_wire_split_geometry.py`.

**State:** S01–S03 and geometry namespaces in S14/S18. **Current public seams:** `get_current_geometry`, `update_current_geometry`, `validate_parameter_values`, `validate_geometry`, `slot_cut_limits`, `CadQueryMotor`, `build_mesh_from_polygons`, `geo_mesh_halves`, `part_materials.resolve`; geometry/material/winding/mesh routes. **Target:** immutable validated geometry/material/mesh snapshots, build and validation functions preserving arrays and tolerances. **Dependents:** M02–M07, M10–M12 and their UI.

### M02 — Electromagnetics, eddy, demagnetisation and PWM field solve

**Purpose:** P2 magnetostatics/transients, circuit stepping, time-periodic eddy, demag and field/loss/torque results. PWM waveform physics lives here; device/drive selection belongs to M05.

**Current files:** B/`simulation/fem_solver_2d.py` (13,809), `eddy_solver_2d.py`, `time_periodic.py` (1,684), `demag.py`, `drive.py`, `pwm.py`, `p2_*.py`, `dc_orbit.py`, `moving_band.py`, `sb_*.py`, `field_ops.py`, `virtual_work_torque.py`, `torque_*.py`, `losses.py`, `per_coil.py`, `excitation.py`, `static3d/`, `end3d_factors.py`, `core_loss_surface.py`; B/`routes/simulation.py` (8,331, mixed with M12/M13), `routes/static3d.py`. W/`components/simulation/` (`SimulationPanel.tsx`: 3,268; `TransientCharts.tsx`: 2,169), `components/static3d/`, `lib/emRunPayload.ts`, `lib/emResultTiles.ts`. T/`test_physics_regression.py`, `test_demag_reproducible.py`, `test_tdm_fem.py`, `test_pwm_dc_orbit_fem.py`, `test_raw_torque_payload.py`, `test_run_ledger.py`.

**State:** S04, simulation S13, EM S14/S18. **Current seams:** `fem_field2d`, `em_transient_eval`, `fem_transient_sliding_band`, `solve_magnetostatics_p2`; `/api/simulation/physics/fem_transient`, `/api/simulation/physics/fem_field2d`, `/api/static3d/*`. **Target:** `solve_em(request, context)`, `get_loss_snapshot(run_id, identity)`, `get_em_run`, identity-checked `attach_run_annotation`. **Dependents:** M03, M05, M06, M10–M12.

### M03 — Thermal and cooling

**Purpose:** cooling interpretation, boundary conditions, steady temperature, thermal networks, duty-cycle integration and thermal limits.

**Current files:** B/`thermal_settings.py`, `cooling_options.py`, `thermal_capacities.py`, `thermal_heat_paths.py`, `thermal_duty_cycle.py` (2,696), `simulation/cooling_models.py` (2,063), `simulation/thermal_solver_2d.py`, `routes/thermal.py` (8,820). W/`stores/thermalStore.ts`, `components/thermal/` (`ThermalPanel.tsx`: 1,801), `lib/thermalEstimate.ts`. T/`test_thermal_routes.py` (1,590), `test_thermal_cooling_models.py`, `test_cooling_follows_application.py`, `test_thermal_propeller_source.py`, `test_thermal_duty_cycle.py`, `test_thermal_loss_reuse.py`; W/`components/thermal/__tests__/coolingPayload.test.mjs`.

**State:** S05, thermal S13/S14/S18. **Current seams:** `cooling_fields`, `cooling_issue`, `settle_application_cooling`, `solve_thermal_field` (currently inside a router), `network_from_steady`, `integrate_profile`; `/api/thermal/field`, `/api/thermal/em_run`, `/api/thermal/duty_cycle`. **Target:** `resolve_cooling(source, machine, caller)` returning a typed spec plus provenance, and `solve_thermal(loss_snapshot, cooling, machine)`. Preserve consumer-specific fallback policies during extraction; unifying differing defaults is a separate behaviour change. **Dependents:** M04–M06, M11, M12.

### M04 — Mechanical, mass and bearings

**Purpose:** stress/contact/retention, modal/critical speed, gap checks, mass/inertia and mechanical losses.

**Current files:** B/`simulation/mechanical/` (`rotor_stress.py`: 3,793; `contact.py`: 2,556), `routes/mechanical.py` (2,537), `bearings.py`, `masses.py`, `mech_losses.py`, `retention.py`, `routes/bearings.py`; mass assembly currently also in `modules/motor_mass.py`. W/`stores/mechanicalStore.ts`, `components/mechanical/`, `lib/machineBearings.ts`, `lib/mechThermalTemps.ts`. T/`test_mechanical_rotor_stress.py`, `test_mechanical_contact.py`, `test_mechanical_limit_speed.py`, `test_mechanical_thermal.py`, `test_full_motor_mass.py`, `test_full_motor_mass_server_bom.py`.

**State:** S06, mechanical S13/S14/S18. **Current seams:** `solve_rotor_stress`, `solve_contact`, `compute_masses`, `run_rotor_stress_at`, `run_modes_at`, `run_critical_speeds_at`; `/api/mechanical/rotor_stress`, `/limit_speed`, `/modes`, `/critical_speeds` under the same prefix. **Target:** explicit loads/temperatures/materials in, immutable mechanical results out; M07 supplies authorised BOM geometry measurements. **Dependents:** M02 summaries, M03 heat sources, M06, M11, M12.

### M05 — Controller, inverter and electrical supply

**Purpose:** devices, bridge topology, controller losses/limits, ripple, SPICE, battery/link behaviour and the motor-drive contract.

**Current files:** B/`inverter/` (`losses.py`: 1,896), `routes/controller.py` (1,687), `simulation/battery.py`, `charge_analytic.py`; charge orchestration also in `routes/simulation.py`. W/`components/controller/`, `components/compare/BatteryPanel.tsx`, `ChargePanel.tsx`, `lib/machineBattery.ts`, `lib/generatorCharge.ts`. T/`test_controller.py` (2,305), `test_controller_svpwm.py`, `test_inverter_coupling.py`, `test_inverter_ripple.py`, `test_spice_harness.py`, `test_pwm_in_controller.py`.

**State:** S07 and controller S14. Saved controller/battery definitions are M06 catalog snapshots whose semantics M05 validates. **Current seams:** `solve_controller`, `standalone_coils`, inverter drive-source/coupling adapters; `/api/controller/devices`, `/topologies`, `/settings`, `/point`, `/solve`. **Target:** `resolve_drive`, `evaluate_controller`, controller cooling adapter accepting M03's typed result. **Dependents:** M02, M06, M11, M12.

### M06 — Catalog, passports, Configure and reports

**Purpose:** die/config/duty lifecycle, reference/published/user catalogs, passports, Configure calculations and PDF/DOCX reports. Use distinct internal storage, passport, Configure and rendering subpackages.

**Current files:** B/`routes/family.py` (6,179), catalog/presets/my_motors/catalog_cards routes, `catalog/`, `catalog_sources.py`, `data_sources.py`, `data_publish.py`, `applications.py`, `configure_limits.py`, `passport.py`, `passport_pwm.py`, `passport_store.py`, `passport_v1/`, `propeller.py`, `propeller_s1.py` (1,664), `report.py` (19,827), `report_docx.py` (1,777), `datasheet.py` (2,249), report/propeller routes. W/`components/catalog/` (`FamilyCatalog.tsx`: 1,509), `catalogBrowser/`, `compare/` (`ConfiguratorPanel.tsx`: 2,440), `lib/configurator*.ts`, `referencePassports.ts`, `family*.ts`, `duty*.ts`. T/`test_passport_store.py`, `test_passport_v1.py`, `test_propeller_s1.py`, `test_configure_limits.py`, `test_family_duty_runs.py`, `test_duty_load_no_silent_die_write.py`, `test_report.py` (9,660).

**State:** S08 and catalog S14/S18. **Current seams:** `config_doc`, `config_duties`, `duty_entry`, `generate_passport`, `propeller_s1.solve_point`, `s1_rating`, `gather_report_data`, `build_motor_report`, `build_motor_report_docx`; `/api/family/*`, `/api/catalog/*`, `/api/my_motors/*`, report routes. **Target:** caller-filtered `resolve_machine(ref)` snapshots, explicit lifecycle commands, immutable `ReportSnapshot` assembled from run IDs. Resource cards project M01/M04/M05 data; they do not own copies of those libraries. **Dependents:** engineering selection, M07, M10–M12, shell navigation.

### M07 — CAD generator and model artifacts

**Purpose:** reproducible full/simplified STEP/GLB/FCStd, parameter roundtrip, manifests and internal BOM. Simulation construction stays M01.

**Current files:** B/`cad/`, `cad_params.py`, `cad_param_labels.py`, `model3d.py`, `freecad_io.py`, `fusion360_controller.py`, `services/dimension_sheet.py` (1,874), model3d/freecad/fusion routes; repository `cad/motor_templates/`, `cad/fc_scripts/`, `cad/run_build.py`. W/`components/model3d/`, common FreeCAD/Fusion roundtrip widgets, `lib/cadParams.ts`, `lib/model3d.ts`. T/`test_cad_generator.py`, `test_cad_simplified_access.py`, `test_cad_bom.py`, `test_model3d.py`, `test_fusion_param_roundtrip.py`.

**State:** S09 and CAD S14; permission/trace records belong to M08. **Current seams:** `cad.store.load_effective`, CAD build/manifest/CLI, parameter projections; `/api/model3d/{die}/{config}/model.glb`, `/model.step`, `/simplified.step`; FreeCAD/Fusion routes. **Target:** generator consumes versioned M06/M01 snapshots, returns hash-addressed artifact records, roundtrip generates explicit proposals. **Dependents:** M04 mass, M06, M11, model UI. Internal BOM/pricing never enters public payloads.

### M08 — Access, identity, roles, NDA, verification and grants

**Purpose:** authentication/sessions, operation policy, config visibility, grants, NDA, applicant verification, agent/OAuth credentials and guarded downloads.

**Current files:** B/`auth.py`, `users.py`, `sessions.py`, `auth_email.py`, `email_domain.py`, `motor_access.py`, `nda.py`, `verification/`, `agent_keys.py`, `oauth.py`, `client_ip.py`, `log_redaction.py`, `admin_audit.py`, `data_sharing.py`; policy also in workspace/model3d/family code; auth_local/nda/verification/agent_keys/oauth/data_sharing routes. W/`contexts/AuthContext.tsx`, `lib/apiAuth.ts`, `accessUi.ts`, `localAuth.ts`, `nda.ts`, `verification.ts`, model NDA/verification widgets. T/`test_auth_sessions.py`, `test_config_visibility.py`, `test_motor_access_grants.py`, `test_nda.py`, `test_verification_flow.py`, `test_verification_engine.py`, `test_cad_simplified_access.py`.

**State:** S10. Entire `users.json`, including profile fields, belongs here; M09 requests changes through its API. **Current seams:** `resolve_user`, `require_admin`, `catalog_access`, `may_see_config`, `require_config`, `set_config_visibility`, `verification.access_ok`; auth/NDA/verification/OAuth routes. **Target:** `authorize(subject, action, resource)`, explicit identity/grant/verification commands, injected catalog metadata reader. **Dependents:** all entry adapters, M06/M07 disclosure, M09 lifecycle, M11 tools, M13 request binding.

### M09 — Accounts, analytics, communication and support

**Purpose:** account preferences/export/deletion, activity/usage analytics, consent, newsletter/mailing and support.

**Current files:** B/`account_lifecycle.py`, `activity.py`, `usage_stats.py`, `job_usage.py`, `comm_prefs.py`, `newsletter.py`, `mailing.py`, `notify.py`, `watchdog_notify.py`, `support_store.py`, `ticket_store.py`, `support_context.py`, `support_prompt.py`; account/account_data/admin/admin_analytics/activity/comm_prefs/newsletter/support routes. W/`components/auth/`, `components/admin/`, `components/support/`, consent widgets, `lib/analytics.ts`, `commPrefsApi.ts`, `lastMotor.ts`. T/`test_activity.py`, `test_admin_analytics.py`, `test_comm_prefs.py`, `test_newsletter.py`, `test_data_protection.py`, `test_support_assistant.py`, `test_last_motor_preference.py`.

**State:** S11, no session/credential ownership. **Current seams:** `/api/me`, `/api/me/preferences`, `/api/me/last_motor`, account-data/support/admin routes; `comm_prefs.erase` and `export_for` already illustrate owner-provided lifecycle operations. **Target:** lifecycle coordinator calls each owner's export/erase; preference selection references never mutate the active machine implicitly. **Dependents:** M08 account views, M13 usage, M06 selection, shell/admin/support UI.

### M10 — Optimisation and campaigns

**Purpose:** scan/DOE/descent/CMA-ES, surrogate screening, rejection/final validation and evaluation provenance.

**Current files:** B/`optimization/`, `routes/optimization.py` (7,821), `sweep_journal.py`, `sweep_resume.py`, `routes/sweep_config.py`. W/`components/sweep/`, optimisation slices of `stores/motorStore.ts`, `lib/optVarRanges.ts`, `optVarsHistory.ts`. T/`test_optimizer_final_validation.py`, `test_optimizer_honesty.py`, `test_eval_cache_key_noop_overrides.py`, `test_sweep_resume.py`, `test_auto_optimization.py`, `test_screening_descent.py`.

**State:** S12, sweep S13, optimisation S14/S18. **Current seams:** `optimization/refine_proc.py` worker evaluation, `design_eval` screening, `/api/optimization/*`, `/api/sweep_config`. **Target:** campaign service, one `evaluate_candidate` adapter preserving the existing FEM path, injected M13 scheduling. **Dependents:** optimisation UI, M11 module/tools, M06 explicit candidate save.

### M11 — System graph, contracts and component SDK

**Purpose:** versioned geometry/material/mesh/result/component/assembly contracts, conformance, plugins, capability dispatch, component catalog and view-only graph. MCP tools are entry adapters subject to M08.

**Current files:** B/`contracts/`, `modules/` (except identified domain implementations), `system_graph.py`, `mcp_app.py`, `mcp_tools.py`, `mcp_discovery.py`, `agent_designs.py`; modules/components/kernel/system_graph/agent_designs/mcp_discovery routes. W/`components/system/`, `modules/moduleTabs.ts`, `lib/systemGraph*.ts`, `agentDrafts.ts`. T/`test_component_sdk.py`, `test_component_card.py`, `test_assembly_ir.py`, `test_system_graph.py`, `test_mcp_stage1.py`, `test_mcp_stage3.py`.

**State:** S15; registrations/contracts in code. **Current seams:** `ModuleManifest`, `ComponentCard`, `ResultIR`, conformance gates, registry/kernel, component/system graph/MCP routes. **Target:** contracts and injected capability ports; domain adapters wired by composition root. `system_graph.py` explicitly implements stage 1/view-only, reading manifests/source positions and existing status; it is not a general graph executor. **Dependents:** domain adapters, System UI, external agents and future product composition.

### M12 — Coupled workflows and run records

**Purpose:** EM/thermal/controller/mechanical orchestration, convergence/limit policy, S1/time-to-limit, coherent run identity and filed duty results/fields.

**Current files:** B/`routes/coupled.py` (7,835), `coupled_continuous_rating.py`, `coupled_time_to_limit.py`, `coupled_duty_cycle.py`, `duty_results.py` (1,697), `duty_fields.py`, `duty_refile.py`, `run_recording.py`, `run_history.py`. W/`components/simulation/coupledApi.ts`, coupled controls in Simulation/Thermal panels, `lib/dutyRuns.ts`, time-to-limit/continuous-rating chips. T/`test_coupled.py`, `test_coupled_pwm.py`, `test_coupled_cancel.py`, `test_coupled_run_ledger_reuse.py`, `test_coupled_mechanical_step.py`, `test_duty_results_index.py`, `test_run_history.py`, `test_last_store_persist_race.py`.

**State:** S16 and coupled S14. Domain last-result stores remain M02–M05; filed cross-domain records belong here. **Current seams:** `routes.coupled.run`, `duty_results.record/get/note_*`, duty-fields adapters, `run_history.history_for`; `/api/coupled/run`, `/last`, `/continuous_rating`, history routes. **Target:** `run_coupled`, `RunRepository`, explicit domain ports and run-ID annotation. **Dependents:** M06, M10 where applicable, M11, engineering UI.

### M13 — Workspace, persistence, compute and transport

**Purpose:** tenant context, path roots, atomic I/O, composition/request adapters, queues, progress/cancellation, budgets and remote compute.

**Current files:** B/`workspace.py` (1,779), `config.py`, `json_store.py`, `safe_paths.py`, `private_files.py`, `private_data.py`, `api.py`, `api_errors.py`, `startup_checks.py`, `jobs.py`, `field_jobs.py`, `progress.py`, `solve_pool.py` (1,718), `solve_pool_child.py`, `compute_nodes.py`, `cluster_monitor.py`; jobs/compute/cluster/panel_settings/saved_sims routes; deployment workers. W/common progress/compute widgets and request plumbing. T/`test_workspace.py`, `test_workspace_layers.py`, `test_workspace_state.py`, `test_workspace_workers.py`, `test_jobs.py`, `test_solve_pool.py`, `test_config_redirect_is_complete.py`, `test_compute_nodes.py`.

**State:** S17. Generic adapters are physical writers on behalf of named owners, never alternative semantic owners. **Current seams:** `workspace`, `use_workspace`, `bind`, `ws_map`, `json_store.mutate_json`, jobs/progress, `solve_pool.call/run_command`, job/compute/cluster routes. **Target:** infrastructure accepts M06 catalog and M08 caller policy through ports instead of importing routes. **Dependents:** all storage/compute users.

### M14 — Web shell and common presentation

**Purpose:** navigation/layout/localisation, common widgets, browser transport and transient view preferences; feature semantics stay with M01–M12.

**Current files:** W/`App.tsx`, `main.tsx`, `theme.ts`, `index.css`, `i18n/`, `locales/`, `stores/uiStore.ts`, `components/landing/`, presentation-only `components/common/`, shared formatting/diagnostics; B/`link_pages.py`. Tests: W/`i18n/__tests__/`, `components/landing/__tests__/`, `lib/__tests__/accessUi.test.mjs`.

**State:** S18. **Current seams:** app composition, React props/hooks, i18n formatters, navigation descriptors. **Target:** shell imports feature `public.ts` entry points; permission display consumes server decisions and never substitutes for enforcement. **Dependents:** all feature UI and application entry.

## 3. Single-owner state register

These are **target authoritative owners**, including state currently accessed by several areas. A separately named immutable snapshot is owned by its repository and carries its source revision; it is never a second writable master. Mixed physical files use disjoint key ownership. M13 provides atomic read-modify-write mechanics while the named owner validates the mutation. No generic storage helper becomes a second semantic owner.

Roots: **WSP** = `workspace.root()` (user workspace or process/local config); **SH** = `workspace.shared_root()`; **ID** = process identity config beside `users.json`; **PUB** = `published_root()`; **PRIVATE** = configured private overlay; **LEGAL** = `nda.legal_root()`. ID and WSP differ on layered deployments. See B/`workspace.py:443`, `:616`, `:633`, `:719`, `:959`. No production directory was enumerated.

| ID | Artifact or disjoint key namespace | One owner | Current source evidence / rule |
|---|---|---|---|
| S01 | WSP `motor_config.yaml`: `geometry`, `derived_params`, `geometry_schema`, `parameter_groups`, `materials`, `custom_materials`, `winding`, `mesh`, part-state extensions | M01 | B/`config.py:167`, `api.py:549`, `api.py:1311`. `simulation` is S04, `sweep_whitelist` S12. M13 performs whole-file atomic writes on an owner's behalf. |
| S02 | config/SH `materials_library.yaml`, `part_materials.yaml`, `wire_stock.yaml`, `bh_export/*`; PRIVATE `config/materials_overrides.yaml`; their caches | M01 | B/`materials.py:41`, `:81`, `part_materials.py:67`, `wire_stock.py:33`. Private overlays keep their disclosure restrictions. |
| S03 | `logs/geometry_audit.jsonl`, geometry mesh disk cache | M01 | B/`audit.py:58`, `routes/geometry.py:775`. Preset backup snapshots generated by audit code are instead S08, M06. |
| S04 | WSP `.last_transient.json`, `.last_transient_field.pkl`, `.run_ledger/*`, `.daxis_cache.json`, `.warm_cache.npz`, `.bench_ldq.json`; staged-3D cache; config `end_effect_3d.json`, `stage_d_kt.json`, `end_effect_passports.json`; `motor_config.yaml.simulation` | M02 | B/`routes/simulation.py:2061`, `:3135`, `:3338`, `:6315`; `simulation/fem_solver_2d.py:995`, `:3006`; `routes/static3d.py:65`. Only EM publishes/mutates these; other domains request snapshots. |
| S05 | WSP `.last_thermal.pkl`, `.thermal_loss_maps.pkl`, `.thermal_coupled_passes.pkl`; config/SH `cooling_options.yaml` | M03 | B/`routes/thermal.py:1385`, `:2004`, `:2133`, `cooling_options.py:48`. Thermal loss-map copies are immutable derivatives linked to the EM run identity. |
| S06 | WSP `.last_mechanical.pkl`; config/SH `bearings_library.yaml`; mechanical mesh/result/mass caches | M04 | B/`routes/mechanical.py:320`, `bearings.py:77`, `modules/motor_mass.py:117`. Original CAD measurements remain M07; calculated mass projections belong here. |
| S07 | config/SH/PRIVATE `devices/*.yaml`, `devices/spice/**`, SPICE output tables and controller result history | M05 | B/`inverter/devices.py:479`, `inverter/spice/models.py:56`, `inverter/spice/harness.py:146`, `routes/controller.py:57`. Saved catalog controller inputs are S08 snapshots validated by M05. |
| S08 | WSP/SH/source/PUB `dies/<die>/die.yaml`, `<cfg>.yaml`, histories; `motor_catalog.json`, `motor_presets.json`, preset backup snapshots, `user_motors.json`, `.family_context.json`; `passports/`, `passport_specs/`, `propeller_s1/`, `propellers/`; `catalog_overrides.yaml`, `data_moves.json`, `data_moves_audit.jsonl`, `shared_die_tombstones.json`, PUB `.owner.json`; passport campaign snapshots/stage records | M06 | B/`routes/family.py:346`, `:3583`, `:5662`, `:5974`; `catalog_sources.py:50`, `data_publish.py:240`, `workspace.py:494`, `passport_store.py:39`. **Excluded:** config `visibility` key (S10), filed run payloads/fields (S16). Saved duty/controller/battery/cooling blocks are versioned catalog input snapshots, not live panel settings. |
| S09 | Configured/SH `cad/<die>/<cfg>/*`: full/simplified GLB/STEP, FCStd, manifests, params/build reports, internal BOM; CAD rebuild queue; `fusion_param_map.yaml`, CAD reference profiles/roundtrip proposals | M07 | B/`model3d.py:56`, `cad/build.py:152`, `cad/store.py:194`, `cad/rebuild_queue.py:42`. Download trace/permission records are excluded, S10. |
| S10 | ID `users.json` (all fields), `.sessions.json`, `agent_keys.json`, `oauth_grants.json`, `die_access.json`, `admin_audit.jsonl`, `mcp_audit.jsonl`; auth events; LEGAL NDA templates/signatures/PDFs, verification `events.jsonl`, `cases/`, `documents/`, `index.json`, `data_sharing_audit.jsonl`; ID `cad_downloads.jsonl`; catalog config `visibility` key | M08 | B/`users.py:47`, `motor_access.py:175`, `:441`, `nda.py:281`, `verification/store.py:47`, `model3d.py:64`. M09 requests profile/deletion operations, M06 requests visibility operations; neither rewrites this state. |
| S11 | WSP `.user_prefs.json` including per-user last-motor selection; `account_deletions.json`, `deleted_subjects.jsonl`, `newsletter.json`, `comm_prefs.json`, `comm_prefs_history.jsonl`; support `access_requests.json`, `tickets.json`, chat-day JSONL; `config/mail/*`, campaign send logs; `activity.sqlite`; usage/activity/storage and usage-rollup tables in cluster `history.sqlite` | M09 | B/`routes/account.py:49`, `account_lifecycle.py:112`, `comm_prefs.py:83`, `support_store.py:86`, `ticket_store.py:50`, `mailing.py:289`, `activity.py:61`, `job_usage.py:508`, `usage_stats.py:76`. Selection is a reference, not catalog content. M13 owns other cluster tables and DB migration coordination. |
| S12 | WSP `sweep_config.json`, `.sweep_journal.json`, `.last_scan.json`, `.scan_cache.jsonl`, `.eval_rate.json`, `.last_descent.json`, `.opt_dataset.jsonl`, `.doe_dataset.jsonl`, descent archives/saved optimiser results; `motor_config.yaml.sweep_whitelist`; tracked `FINAL_candidates.json`, `stage_c_*.json`, sweep/verify study snapshots | M10 | B/`routes/optimization.py:115`, `:228`, `:843`, `:3123`, `:3241`, `:5444`, `optimization/doe.py:48`. Preserve rejected evaluations. `stage_d_kt.json` is explicitly S04/M02. |
| S13a | WSP `.panel_settings.json` → `thermal/<caller>` including settings and timestamp | M03 | B/`routes/panel_settings.py:32`, `:45`, `:97`. Thermal schema/migrations belong to Thermal. |
| S13b | Same file → `mechanical/<caller>` | M04 | Same generic storage port, independent owner. |
| S13c | Same file → `simulation/<caller>` | M02 | Never infer ownership from the file's generic transport route. |
| S13d | Same file → `mesh/<caller>` | M01 | Duty activation calls M01's command to update this subtree. |
| S13e | Same file → `sweep/<caller>` | M10 | No unregistered panel names; common container lock is M13 infrastructure. |
| S14 | Runtime caches/flags and `.run_history/<kind>/*` | **By disjoint domain namespace below** | B/`workspace.py:311`, `:1691`, `run_history.py:241`. Generic storage/protocol is not semantic ownership. |
| S15 | WSP `agent_designs/<id>/design.json`, draft sandbox and job references; SDK registry/component derived caches | M11 | B/`agent_designs.py:49`, `:926`, `modules/component_library.py`. Draft sandbox is a separate snapshot, not active workspace config. No saved user-authored graph store was identified; assign M11 when introduced. |
| S16 | WSP/SH/source `.duty_results.json`; `dies/<die>/runs/<cfg>/<duty>.<drive>.json.gz`; duty `fields/<kind>.npz`; `.last_coupled.json`; coupled history, recording-suppression context and run journal | M12 | B/`duty_results.py:119`, `duty_fields.py:586`, `routes/family.py:1281`, `routes/coupled.py:296`, `routes/simulation.py:3186`. M06 owns duty definitions; M12 owns filed result records including publication copies. |
| S17 | WSP `.jobs.json`, `saved_simulations.json`; queue/progress/worker state; `user_nodes.json`, `remote_jobs.json`, `node_prefs.json`, `node_audit.jsonl`, `shared_ledger.jsonl`, `remote_results/`; cluster `nodes.json`, resource-history tables; workspace registry and request binding | M13 | B/`jobs.py:131`, `routes/saved_sims.py:45`, `compute_nodes.py:68`, `cluster_monitor.py:77`. Exclude M09 usage tables (S11); one DB migration coordinator calls table-owner migrations. |
| S18 | Browser locale/theme/navigation/scroll/diagnostic/display preferences | M14 | W/`stores/uiStore.ts`, `i18n/persist.ts`, `lib/scrollMemory.ts`, `lib/diag.ts`. Actual consent records remain M08/M09. Feature mirrors have the owners below, not M14. |

S14 and feature browser mirrors are partitioned as follows (each row is a single owner):

| Namespace | Owner |
|---|---|
| `geometry.*`, geometry-service caches; browser `mesh.*`, `mat.*`, geometry editor | M01 |
| `simulation.*`, `fem_solver_2d.*`, `static3d.*`; EM history, browser `sim.*` | M02 |
| `thermal.*`; thermal history, browser `therm.*` | M03 |
| `mechanical.*`, derived mass; mechanical history, browser mechanical fields | M04 |
| `controller.*`, `controller.solve` history, controller UI fields | M05 |
| `family.*`, passport/catalog projection caches, browser `family.*` and Configure selection draft | M06 |
| `freecad.*`, CAD artifact/preview cache | M07 |
| Credential/authorization caches and auth UI session mirror | M08 |
| Account/analytics/support UI caches and last-motor preference mirror | M09 |
| `optimization.*`, sweep/optimiser browser fields | M10 |
| SDK registry, component-card and graph-description caches | M11 |
| `coupled.*`, coupled history | M12 |
| Workspace container eviction/locks, queue/progress context | M13 |

Backups/seeds inherit the source owner's restrictions. New nested keys must receive explicit ownership before extraction; there is no shared mutable miscellaneous domain. This is a migration contract, not a claim that present code already enforces it.

## 4. Fifteen priority boundary violations today

“Violation” means coupling against the proposed boundaries, **not** a demonstrated numerical error or security exploit. Preserve existing guards while removing multiple implementations.

| # | Evidence at baseline | Boundary and risk | Extraction seam |
|---|---|---|---|
| 1 | B/`routes/coupled.py:4911`; B/`routes/thermal.py:8110`; B/`routes/controller.py:583`, `:596`, `:658` | Cooling mapping is partly shared, but effective-source policy differs: coupled/duty-cycle settle application and propeller context; Controller chooses live/saved thermal state by mode and reconstructs saved fields. | M03 typed cooling snapshot/provenance; retain named compatibility policies until parity is proven. |
| 2 | B/`geometry_validation.py:505`, `:544`; B/`cadquery_geometry.py:1850`, `:2834`; W/`lib/slotMouthFit.ts:43` | Builder, feasibility guard and browser separately implement slot/wire arithmetic. Validation documents the 15 October lobe incident at `:729`; current guards exist, duplication remains. | M01 common slot primitives, server-provided bounds or generated browser fixtures; unchanged point order/tolerances. |
| 3 | B/`thermal_settings.py:110`, `:566`; W/`stores/thermalStore.ts:382` | Python mirrors browser cooling mapping; thermal settings also reach into panel transport `_load/_save` and lock. | M03 schema/mapping, M13 atomic panel storage port, Python/TS payload parity fixtures. |
| 4 | B/`routes/coupled.py:4519`–`:4538` | Coupled reads EM `_last_transient_ref` and calls private `_save_last_transient`. A timestamp guard exists, but workflow knows another domain's cache/store. | M02 identity-checked `attach_run_annotation`, preserving rejection if a newer run landed. |
| 5 | B/`routes/thermal.py:1838`–`:1857`, `:1258` | Thermal traverses/warm-loads EM field snapshots and interprets EM fingerprint/cache-key internals. | M02 loss-snapshot query with explicit match/miss reasons. |
| 6 | B/`report.py:326`–`:419` | Reporting reads four domains' private last-result stores and sometimes warms persistence. | Read-only owner projections into M06 ReportSnapshot, preserving machine/duty guards. |
| 7 | B/`modules/solvers.py:100`, `:126`, `:168`, `:219` | SDK imports HTTP routers to perform physics, inheriting route defaults/import side effects. | Domain service called by both route and SDK adapters. |
| 8 | B/`modules/motor_mass.py:126`–`:146`, `:455` | SDK mass code loads/merges die/config YAML, selects material fallback and keys its own file cache. | M06 resolved snapshot + M04 mass service + authorised M07 measurements. |
| 9 | B/`cad/store.py:325`–`:347` | CAD independently merges die/config/material files. Hashes prove provenance but do not centralise merge policy. | M06 explicit machine snapshot; preserve CAD compatibility reader until equivalence. |
| 10 | B/`account_lifecycle.py:323`–`:371` | Account deletion mutates Sessions private store and rewrites agent-key/OAuth schemas itself. | M08 erase/export methods, M09 lifecycle coordination, identical retention. |
| 11 | B/`workspace.py:951`, `:1130`, `:1239` | Infrastructure looks up a family-router override, scans catalog precedence and implements caller/admin policy. | M13 primitives plus injected M06 resolver/M08 policy; retain test compatibility shims initially. |
| 12 | B/`routes/family.py:4264`, `:4276`, `:4314` | Activation writes context and synchronises mesh config across owners inside a router. Source documents wrong-machine mesh at `:4271`. | Explicit validated activation coordinator, same mutation/failure order. |
| 13 | B/`routes/geometry.py:172`, `:198`, `:338` | Geometry PUT calls family locking/transition and Simulation cache invalidation through routes. | M06 edit coordinator → M01 command → explicit domain invalidation ports. |
| 14 | W/`lib/dutyLocalApply.ts:260`, `:340`, `:366`; W/`stores/motorStore.ts:985`–`:1013` | Catalog load writes Simulation/material/family browser keys; motor store constructs solver payloads from several feature stores. | Feature `applySnapshot`/`getRequest` ports and user/workspace-scoped load coordinator. |
| 15 | B/`duty_results.py:699`; B/`duty_fields.py:839`; B/`duty_refile.py:141` | Three result/field/refile paths read `.family_context.json` directly instead of using the catalog context owner. Each knows the filename and record schema. | M06 context snapshot API with explicit workspace/die/config/duty identity; preserve background sandbox context and missing-record behaviour. |

## 5. Every source file above 1,500 lines

Counts are newline counts at the baseline. Destinations are proposed internal responsibilities, not already-created files. Mixed-owner giants are split by responsibility; numerical kernels are extracted last.

| Current source file | Lines | Owner after extraction | Concrete split |
|---|---:|---|---|
| `src/motor_ai_sim/report.py` | 19,827 | M06 | `gather_report_data`, `_Source`, `duty_em_source`, `duty_map_provenance` -> snapshot/provenance; `*_rows`, `duty_warnings`, limit clauses -> domain presentation modules; `_map_png`, `_*_png` -> figures; `_table`, `_para`, page functions and `build_motor_report` -> PDF renderer. Move live-result reads behind M12/domain ports; do not recompute physics in renderers. |
| `src/motor_ai_sim/simulation/fem_solver_2d.py` | 13,809 | M02 | `FEMMaterial`, `FEMResult`, `build_materials` -> inputs; `solve_magnetostatics*` -> static; `noload_*`, `frozen_permeability_*`, `_calibrate_daxis` -> calibration; `_warm_cache_*` -> warm-start store; `_fem_transient_sliding_band_once` -> staged transient implementation (schedule, assembly, TDM/demag loop, output). First extract independent helpers, retaining the large loop until closure dependencies are explicit; no rearranged reductions. |
| `tests/test_report.py` | 9,660 | M06/M12 tests | Extract existing fixtures to report-specific support; split TestWarningRules, provenance/foreign-machine guards, geometry fingerprints, PWM/controller, duty-cycle/limits, PDF/Word/layout groups into separate files. Preserve assertions and isolation; avoid placing expensive fixtures in global conftest. |
| `src/motor_ai_sim/routes/thermal.py` | 8,820 | M03/M12 | `_cooling_bc`, `_bore_bc`, `_validate_field_params` -> M03 request/boundaries; `_snapshot_loss_entry`, `latest_em_run` -> M02 loss-snapshot adapter; `solve_thermal_field` -> M03 service; `_thermal_polys`, `_retag_thermal_domains` -> thermal geometry adapter; `_persist_last`, loss/pass caches -> M03 repository; `solve_coupled` -> M12 compatibility workflow; `duty_cycle` -> thin route + M03 service. |
| `src/motor_ai_sim/routes/simulation.py` | 8,331 | M02/M05/M13 | `_effective_*`, `_config_physics_fingerprint`, `_field_snap_key` -> M02 input identity; `get_fem_transient` -> thin route + EM service; `_ledger_*`, `_save_last_transient`, field snapshot functions -> EM repository; `_charge_outer_loops` -> M05 charge service; `build_fem_mesh*` -> M01 adapter; `_build_transient_summary` -> M02 projection; cancellation/progress plumbing -> M13 ports. |
| `src/motor_ai_sim/routes/coupled.py` | 7,835 | M12 | `_preflight`, `_coupled_canonical` -> request/identity; `_ControllerLoop`, `_pwm_final_passes` -> drive-loop adapter; `_em_run`, `_thermal_solve`, `_mechanical_step`, `_modal_steps` -> domain ports; `_run` -> workflow; `_continuous_rating_for_loop`, `_s1_verify`, `_cr_*` -> rating workflow; `_cycle_*`, `_ttl_*` -> duty/limit adapters; `_remember_last`, history/annotation -> repository. |
| `src/motor_ai_sim/routes/optimization.py` | 7,821 | M10/M13 | `_eval_cache_key`, `_store_eval` -> evaluation repository; `_subprocess_eval` -> evaluator; `_scan_worker`, `_descent_worker`, `_cmaes_worker`, `_auto_worker`, `_screen_worker` -> separate campaign services; `_finalize_standard_shortlist` -> final validation; `_optimizer_slot_*` -> M13 scheduling adapter; request models and progress/cancel endpoints remain thin. |
| `src/motor_ai_sim/routes/family.py` | 6,179 | M06/M08/M12 | `config_doc`, `_load_yaml`, `_save_yaml`, source/layer resolution -> M06 repository; `activate`, `sync_active_die_geometry`, mesh sync -> activation coordinator; access/visibility checks -> M08 ports; `_store_run`, `_read_run_payload`, refile/delete runs -> M12; publish/history/CRUD -> separate M06 services; controller/bearing setters delegate validation to owners. |
| `src/motor_ai_sim/simulation/mesher.py` | 4,419 | M01 | `_simplify_polys`, `_repair_needles*`, `_preflight_near_touching` -> preflight; `_split_polys_for_sliding_band`, `_build_sliding_band_meshes` -> sliding-band construction; structured gap/wire builders -> structured mesh; `_trace_*` -> diagnostics; `_build_mesh_from_polygons_impl` -> backend orchestration; stitching/periodic helpers -> topology. Preserve backend calls and numbering. |
| `src/motor_ai_sim/cadquery_geometry.py` | 3,932 | M01 | `_arc_*`, sanitization/welding -> primitives; pocket/fillet helpers -> rotor geometry; `_strip_columns`, `_strip_span`, slot primitives -> winding/stator; `CadQueryMotor._create_*` -> solid builders; `get_2d_polygons`/`get_2d_mesh_data` -> cross-section builder; `get_mesh_data`/extrusion/export -> views; `CadQueryCache` -> repository facade. Keep 2D/3D tolerances and order pinned. |
| `src/motor_ai_sim/simulation/mechanical/rotor_stress.py` | 3,793 | M04 | `part_mech`, `resolve_part_materials`, `part_C`, `thermal_eigenstrain` -> constitutive/material adapter; `build_rotor_mesh` -> mechanical mesh; `assemble_plane_stress`, `recover_stress`, `solve_plane_stress` -> elasticity; `torque_load`, `bore_hold`, traction -> loads; safety-factor/retention functions -> criteria; `solve_rotor_stress` -> orchestration and projection. |
| `web/src/components/simulation/SimulationPanel.tsx` | 3,268 | M02/M05/M12 | `loadWinding`/`applyWinding` -> M01 hook; `loadBattery`/`saveBattery`/`autoSaveController` -> M05 hook; `launchRun`, `simPhysicsPatch`, `restoreFromDuty` -> M02 request hook; `handleRun`, polling and coupled state -> M12 hook; JSX sections -> controls/results. Preserve effect and cancellation order. |
| `src/motor_ai_sim/simulation/geo_mesh.py` | 2,959 | M01 | `_coil_pslg`, `_wire_template`, `_stack_patch` -> conductor meshing; `_build_pslg`, sliver/cusp repair -> PSLG; backend selection/provenance -> backend adapter; `_mesh_stator_*`, `_mesh_rotor_*` -> parts; `skin_layer_radii`, `_skin_patch` -> skin layers; `geo_mesh_halves` -> facade. No geometry or mesh-ID drift. |
| `src/motor_ai_sim/thermal_duty_cycle.py` | 2,696 | M03 | `Segment`, `Profile`, `normalise_spec` -> schema; `Network`, `network_from_steady`, conductances -> network construction; `integrate_profile`, `steady_state`, `periodic_steady_state` -> integrator; `time_to_limit(s)`, `allowable_ed`, `ed_vs_cycle` -> limits; decimation/projections -> output. Do not deduplicate correlations in this extraction. |
| `src/motor_ai_sim/simulation/mechanical/contact.py` | 2,556 | M04 | `ContactSpec`, `parse_contacts` -> schema; `build_contact_system`, interface facets -> assembly; `_solver`, rigid-body/component helpers -> linear support; `_seat_*`, `solve_contact` -> contact iteration; `facet_report`, `interface_forces` -> results. Preserve active-set and seating order. |
| `src/motor_ai_sim/routes/mechanical.py` | 2,537 | M04 | `_default_torque_nm`, temperature/material adapters -> input resolution; `run_rotor_stress_at`, `run_modes_at`, `run_critical_speeds_at`, limit-speed orchestration -> services; cache/last/history functions -> repository; retain route serializers and progress adapter. |
| `web/src/components/compare/ConfiguratorPanel.tsx` | 2,440 | M06 | reference/context loading and selection -> hook; `saveDraft`/`openDraft` -> M11 adapter; `updateBattery`, `setDrive` -> M05-facing hook; `updateCool`/S1 query -> M03/M06 adapter; `applyPreset`, save/load config -> local draft service; controls/charts/tiles -> view components. |
| `tests/test_controller.py` | 2,305 | M05 tests | Split device-card/loss arithmetic, bridge topology/current sharing, cooling inheritance, route/store integration and report adapter tests; share existing small fixtures only. |
| `src/motor_ai_sim/datasheet.py` | 2,249 | M06 | `_render_cross_section`/SVG helpers -> drawing; `_calc_mech_terms`, `_build_calculator`, interpolation helpers -> existing calculator adapter; `build_datasheet` -> document assembly. Preserve formula text/numeric coefficients separately from any future calculator correction. |
| `web/src/components/simulation/TransientCharts.tsx` | 2,169 | M02 | `persistLastTransient`, `loadLastTransient` -> scoped result-store adapter; request/poll/cancel effects inside `TransientCharts` -> run hook; waveform/voltage/torque/loss chart JSX -> focused views; retain shared payload DTO and existing downsampling. |
| `src/motor_ai_sim/simulation/cooling_models.py` | 2,063 | M03 | `FluidProps`, `air_properties`, Nusselt/radiation functions -> correlations; `outer_*`, `bore_*`, `taylor_couette_gap` -> surfaces; `shaft_ends_path`, `mount_path`, `bearing_path`, end-winding/slot/rotor-end paths -> thermal paths. Keep equations and units unchanged. |
| `scripts/coupled_inverter_sandbox_run.py` | 1,920 | M12/M13 tooling | `install_write_guard`, `guard_sandbox`, `_redirect` -> sandbox harness; `load_machine`, `duty_thermal_settings`, `body_for` -> explicit input adapter; `coupled_pwm`, `one_duty` -> campaign runner; `save_to_real_catalog` -> separate explicit publication command. Preserve write barriers; do not execute this script during the audit. |
| `src/motor_ai_sim/inverter/losses.py` | 1,896 | M05/M03 | `ColdPlate`, `AirForcedCooling`, `AirStillCooling`, `_build_cooling` -> M05 cooling-network adapter using M03 correlation API; `_leg_losses` -> device losses; `_ripple_block` -> ripple adapter; `limit_rows` -> limits; `solve_controller` -> service. The device thermal network remains M05-owned. |
| `src/motor_ai_sim/services/dimension_sheet.py` | 1,874 | M07 | `_dim_*`, `_leader`, layout/label measurements -> drawing primitives; `_draw_stator_detail`, `_draw_rotor_detail`, `_draw_ring_inset`, `_draw_wire_inset`, `_draw_axial_view` -> views; `build_dimension_sheet` -> composition, consuming M01 geometry. |
| `web/src/components/thermal/ThermalPanel.tsx` | 1,801 | M03/M12 | `CoupledSection` -> M12 feature; `surfaceSub`, `surfaceTip`, part/tile/column definitions -> M03 display model; input/progress/history effects -> Thermal hook; `ThermalPanel` -> layout and map/path/result sections. |
| `src/motor_ai_sim/workspace.py` | 1,779 | M13/M06/M08 | `BoundedStore`, `WorkspaceState`, StateMapping/List/Lock -> M13 state primitives; roots/registry/bind/executor -> workspace context; `layers`, `iter_dies`, resolve/tombstone/published helpers -> M06 policy/repository adapters; caller verification/admin helpers -> M08; middleware remains transport composition. |
| `src/motor_ai_sim/report_docx.py` | 1,777 | M06 | `_table`, `_picture*`, `_rich`, footer/style helpers -> Word primitives; `_machine`, `_em_detail`, `_thermal_detail`, `_controller`, `_mech_detail`, `_duty_cycle` -> renderers of the same report model as PDF; `build_motor_report_docx` -> orchestration. |
| `src/motor_ai_sim/solve_pool.py` | 1,718 | M13 | `SolvePool`, Ticket/budgets -> admission; `_Child`, `_WinJob`, process-tree functions -> platform runner; frame read/write -> protocol; `capture_context`, `context_fingerprint` -> context DTO; `call`, `run_command` -> dispatcher; `em_transient_eval`, `apply_fem_echo` -> M02 adapter registered by composition root. |
| `src/motor_ai_sim/duty_results.py` | 1,697 | M12 | store path/locks/read/write/get/index/rename/forget -> repository; `record`, alternate-carrier filing -> service; `compact_thermal`, `compact_mechanical`, `compact_controller`, `compact_coupled`, PWM/duty-cycle projections -> explicit versioned serializers; `active_context` calls M06 port. Preserve existing serialized bytes/fields. |
| `src/motor_ai_sim/routes/controller.py` | 1,687 | M05 | `_duty_defaults`, `_config_doc_for`, `_controller_settings_for` -> M06 adapter; `_thermal_*` -> M03 compatibility adapter; `_build_request`, `_standalone_request` -> input service; `_history_key`, summaries -> repository; solve/device/schematic endpoints become thin. |
| `src/motor_ai_sim/simulation/time_periodic.py` | 1,684 | M02 | `FrameFactor`, `gmres_right` -> linear algebra; `TdmFrame`, `TimePeriodicEddy` -> periodic core; demag march/image/Br functions -> demag adapter; `window_gate`, `state_closure`, `period_map_checks`, invariance checks -> acceptance. Keep convergence and demag ratchet timing fixed. |
| `src/motor_ai_sim/geometry_validation.py` | 1,682 | M01 | `Violation`, `ValidationResult`, `GeometryInvalid` -> result schema; `slot_cut_limits`, `split_width_error`, `slot_mouth_infeasibility` -> shared stator feasibility; topology/parameter checks -> parameter validation; `validate_polygons`, stator pieces/outline checks -> constructed-geometry validation; facade preserves error codes/messages. |
| `src/motor_ai_sim/propeller_s1.py` | 1,664 | M06 with M03/M05 ports | `load_model`, `validate_model` -> model repository/schema; grid/interpolation/dq/voltage functions -> existing passport evaluator; `resolve_drive`, `resolve_pack`, board limits -> M05 adapter; cooling/calibration/temperatures -> M03 model adapter; `solve_point`, `s1_rating` -> Configure application workflow. Keep analytic/FEM provenance distinct. |
| `tests/test_thermal_routes.py` | 1,590 | M03 tests | Separate field/mesh invariants, cooling paths and budget, EM-source/reuse/refusal, persistence/foreign-machine, coupled compatibility and mechanical-loss input groups; retain solve markers and fixture scope. |
| `web/src/components/catalog/FamilyCatalog.tsx` | 1,509 | M06 | `subscribeFamilyCatalogRefresh`/`load` -> query hook; `mutate`, CRUD/retire/publish/lock commands -> catalog client; `runExport`/picture selections -> report feature; `applyDuty` -> typed activation coordinator; `ApplicationBadge`, `ReportRing`, tree rows -> presentation. |
| `web/src/components/mesh/MeshPanel.tsx` | 1,503 | M01 | `FemMeshCanvas`, `CollocationPreview` -> preview components; `estimatePoints` -> preview utility; `rememberActiveDieSoon`, field persistence/save/load effects -> mesh settings hook; `MeshPanel` -> controls/layout. Keep mesh request and user-visible estimates unchanged. |

## 6. Concrete enforcement, initially report-only

`pyproject.toml` currently declares Ruff/MyPy/pytest but no module-boundary tool. `web/eslint.config.js` has TypeScript/React rules; `web/package.json` has no boundary checker. Add enforcement **after launch**, without changing existing lint/test exit behaviour. Do not enforce independence between all physics domains: legitimate typed data dependencies must remain possible.

### Dependency rules

1. HTTP/CLI/MCP adapters and the composition root may call domain public APIs; domain implementation must not call HTTP routes or import the application object.
2. M11 contracts/conformance are the bottom layer: no concrete solver, repository, route, user store or web framework imports. SDK registry/kernel may use injected capabilities; built-in adapters live above contracts.
3. M13 storage/context primitives cannot import catalog, access or physics implementations. The composition root injects policy/worker handlers. Existing workspace policy imports receive migration findings, not immediate failures.
4. Cross-domain imports use `public.py` (Python) or `public.ts` (web). No foreign `_private` names, runtime stores, serializers or implementation classes. Re-export only stable contracts; do not conceal private store objects behind a public alias.
5. Pure M01–M05 kernels take explicit inputs. Their orchestration adapters may depend on each other through typed ports (e.g. M03 consumes M02 loss snapshots); kernels must not load current machine/panel/account state.
6. M12 coordinates calls through M01–M05 ports; it never owns their caches. M06 rendering consumes snapshots and never triggers a solve. M08 authorises before domain I/O; hiding a UI control is not a permission boundary.
7. Every persistent path/key has one owner from section 3; only that owner calls its repository mutation API. Generic M13 I/O is allowed only from registered repositories. No new direct `Path/read_text/open/json/yaml/localStorage` state access outside those adapters.

### Python: a small AST checker as the import-linter equivalent

Use a project-owned `scripts/check_module_boundaries.py` reading TOML, not importing the application. This avoids forcing the flat legacy layout into a misleading global layer order. The following is the **proposed exact config schema**, not a built-in Ruff or import-linter schema:

```toml
[tool.module_boundaries]
root = "src/motor_ai_sim"
package = "motor_ai_sim"
mode = "report"
baseline = "docs/architecture/import-baseline.json"
output = "artifacts/module-boundaries.json"

[[tool.module_boundaries.forbidden]]
id = "contracts-no-implementation"
sources = ["motor_ai_sim.contracts", "motor_ai_sim.contracts.*"]
targets = ["motor_ai_sim.routes*", "motor_ai_sim.api",
           "motor_ai_sim.simulation*", "motor_ai_sim.inverter*",
           "motor_ai_sim.modules*", "motor_ai_sim.users",
           "motor_ai_sim.workspace", "fastapi*"]

[[tool.module_boundaries.forbidden]]
id = "kernels-no-http"
sources = ["motor_ai_sim.geometry*", "motor_ai_sim.simulation*",
           "motor_ai_sim.inverter*", "motor_ai_sim.thermal_duty_cycle",
           "motor_ai_sim.thermal_capacities"]
targets = ["motor_ai_sim.routes*", "motor_ai_sim.api", "fastapi*"]

[[tool.module_boundaries.forbidden]]
id = "report-no-route-internals"
sources = ["motor_ai_sim.report", "motor_ai_sim.report_docx",
           "motor_ai_sim.datasheet"]
targets = ["motor_ai_sim.routes*"]

[[tool.module_boundaries.forbidden]]
id = "sdk-no-routes"
sources = ["motor_ai_sim.modules*", "motor_ai_sim.system_graph"]
targets = ["motor_ai_sim.routes*", "motor_ai_sim.api"]

[[tool.module_boundaries.forbidden]]
id = "storage-primitives-no-domain"
sources = ["motor_ai_sim.json_store", "motor_ai_sim.safe_paths",
           "motor_ai_sim.private_files"]
targets = ["motor_ai_sim.routes*", "motor_ai_sim.api",
           "motor_ai_sim.simulation*", "motor_ai_sim.inverter*",
           "motor_ai_sim.report", "motor_ai_sim.users"]

[tool.module_boundaries.future]
# New packages are introduced incrementally; none is created by this audit.
prefix = "motor_ai_sim.features"
public_leaf = "public"
modules = ["geometry", "em", "thermal", "mechanical", "controller",
           "catalog", "cad", "access", "accounts", "optimization",
           "sdk", "runs", "platform", "shell"]
deny_foreign_private_names = true
deny_cross_module_cycles = true
```

Implementation contract for the checker:

- Parse every tracked Python source using `ast.parse`; include nested/lazy and `TYPE_CHECKING` imports. Resolve relative imports against package paths without importing code. For `from x import y`, consider both `x` and `x.y`, so `from motor_ai_sim import routes` is caught. Handle `__init__.py` correctly. Constant-string `importlib.import_module` is an edge; unresolved dynamic imports are a separate warning, not silently ignored.
- Match the `sources`/`targets` with case-sensitive `fnmatchcase`. Emit rule ID, source, target, imported names, file and line. Identify `alias._name` accesses after imported-module aliases as well as `from x import _name`; otherwise the worst cache violations evade the checker.
- For `features.<A>` → `features.<B>` where A differs from B, require the target to be exactly `features.<B>.public`, and forbid imported names beginning `_`. Permit shared SDK contract leaves explicitly. Record and report cycles in the resolved cross-domain graph; dependency injection removes service cycles while bidirectional data flow can remain in M12.
- Maintain a reviewed legacy **symbol/file owner manifest** because `api.py`, `workspace.py`, `routes/family.py`, `modules/motor_mass.py` and mixed UI stores have more than one responsibility. Whole-file defaults may help discovery; do not pretend they prove symbol ownership. After a symbol is moved, remove its legacy exception.
- Report-only mode always exits zero for architectural findings and saves JSON/text. Parse/config/tool crashes produce a separate `tool_error` artifact and visible CI warning. Later `--fail-new` rejects new edges relative to a reviewed baseline keyed by `(rule, source symbol, target)`, not unstable line numbers; stale exceptions are reported. Existing debt is not an unlimited wildcard allowance.

Illustrative future CI job (all files/commands below are proposals):

```yaml
module-boundaries-report:
  runs-on: ubuntu-latest
  continue-on-error: true
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-python@v5
      with:
        python-version: '3.11'
    - run: python scripts/check_module_boundaries.py --report-only
    - uses: actions/upload-artifact@v4
      if: always()
      with:
        name: module-boundaries
        path: artifacts/module-boundaries.*
```

Pin action/tool versions to the repository's reviewed versions when implementing; this audit did not install or execute them. The isolated job is non-blocking; existing required checks stay unchanged. After migration, remove `continue-on-error` for the new-edge gate only.

### Web: resolved-file boundaries with dependency-cruiser

Add a reviewed, pinned development dependency after launch. Keep `eslint .` unchanged. A dedicated `web/.dependency-cruiser.cjs` can use warning-level rules, resolving relative imports as well as aliases:

```javascript
const names = ['geometry', 'em', 'thermal', 'mechanical', 'controller',
  'catalog', 'cad', 'access', 'accounts', 'optimization', 'sdk',
  'runs', 'platform', 'shell'];

module.exports = {
  forbidden: [
    { name: 'no-cycles', severity: 'warn',
      from: {}, to: { circular: true } },
    ...names.map(name => ({
      name: `${name}-public-entry-only`, severity: 'warn',
      from: { path: `^src/features/${name}/` },
      to: {
        path: '^src/features/',
        pathNot: `^src/features/${name}/|^src/features/[^/]+/public\\.ts$`,
      },
    })),
    { name: 'shared-ui-no-features', severity: 'warn',
      from: { path: '^src/shared/ui/' },
      to: { path: '^src/features/' } },
    { name: 'thermal-no-em-store-internals', severity: 'warn',
      from: { path: '^src/(components/thermal/|stores/thermalStore\\.ts$)' },
      to: { path: '^src/(stores/motorStore\\.ts$|components/simulation/)' } },
    { name: 'catalog-no-direct-physics-stores', severity: 'warn',
      from: { path: '^src/components/(catalog|compare)/' },
      to: { path: '^src/stores/(thermal|mechanical|motor)Store\\.ts$' } },
  ],
  options: {
    doNotFollow: { path: 'node_modules' },
    tsConfig: { fileName: 'tsconfig.app.json' },
  },
};
```

Proposed `package.json` script: `"boundaries:report": "depcruise --config .dependency-cruiser.cjs --output-type json src"`. CI redirects output to an artifact in a separate non-blocking step/job. Check config compatibility against the chosen dependency-cruiser release at implementation time; this is not a claimed successful lint run. Keep type-only imports visible initially because DTO ownership also matters. Add one concrete allowlist entry per required legacy type bridge rather than disabling a whole feature rule.

Import checks alone do **not** enforce state ownership. Add a read-only scan of Python I/O calls and TypeScript `localStorage`/`sessionStorage` calls, mapped to the section 3 manifest; constant paths/keys can be checked, dynamic ones must produce unresolved findings. Browser storage access should ultimately occur only in the owning feature's persistence adapter. A server access contract additionally checks authorization-to-I/O ordering through focused tests/review, since an import graph cannot prove it.

## 7. Per-module review checklists

Use these for every extraction PR, including agent-written work. The common gate is unchanged inputs, unchanged ordering/defaults, bit-identical numerical payloads in the same deterministic environment. The named tests in section 2 are candidates for the implementing PR, not permission to run a broad suite now.

### M01 — Geometry/materials/mesh

- Constructed polygon coordinates, ordering, holes, tags, areas and mesh nodes/connectivity are identical for unchanged inputs.
- Stator slot/wire feasibility uses the same primitives as construction; impossible parameters fail before drawing/solving.
- Units, derived slot/pole counts, fillets, weld tolerances and backend selection remain unchanged.
- Material/B-H/temperature/anisotropy sources and part accounting states retain provenance and private-overlay precedence.
- Only M01 mutates live geometry/material/winding/mesh namespaces; saved catalog snapshots remain separate.
- Cache identity includes geometry, materials and mesh-relevant options; invalidation preserves tenant scope and last-result semantics.

### M02 — EM

- Arrays, torque/loss/scalar outputs and refusal/convergence flags are bit-identical in a pinned environment; no changed reductions or iteration order.
- Energy/flux-linkage torque remains the reported quantity; Maxwell diagnostic is not substituted.
- P2 basis, sampling/window selection, eddy settling, demag ratchet and voltage/current normalisation stay fixed.
- Request operating point and actual solved current/drive remain explicit, never borrowed from another duty.
- EM exclusively owns run/calibration/warm caches; keys preserve material, temperature, geometry, winding, drive, mesh and solver identity.
- `record:false`, cancellation and foreign-machine queries cannot overwrite current/other-tenant results.

### M03 — Thermal

- One effective cooling snapshot records application, source, units, caller/motor context and all used fields.
- Legacy missing/blank/unused-field behaviour is preserved; changing fallback policy is a separate PR.
- Loss maps match their EM run, geometry, materials and operating point; no hidden fresh EM solve.
- Heat budget, nodal capacities, path accounting, temperatures and duty-cycle/limit results stay bit-identical.
- Thermal owns its settings/result/cache writes; Controller and Coupled consume public cooling/loss interfaces.
- Cache keys distinguish every active boundary/path option, while omitting unused fields exactly as today.

### M04 — Mechanical

- Stress fields, contacts, modes, limit speed, mass/inertia and mechanical loss numbers remain bit-identical.
- Displayed stresses stay averaged; safety factors use exactly the printed stress and correct strength/criterion.
- Torque, temperatures, carrier frequency, bearing cards and retention assumptions have explicit source identities.
- Contact active-set/seating/rigid-body constraints and mesh order remain unchanged; runaway/refusal is never hidden.
- Complete/partial/unknown mass remains explicit; private BOM cost fields never enter mass outputs.
- Mechanical cache/history keys include geometry, loads, temperature/material context and solver settings; only M04 writes them.

### M05 — Controller/supply

- Bridge/parallel-path currents, device losses, ripple, junction temperatures and bus results remain bit-identical.
- Selected device, topology, voltage, carrier, dead time and modulation travel together into EM and result records.
- Cooling is inherited through a provenance-bearing M03 projection; legacy live/saved fallback precedence stays unchanged.
- Standalone estimates and coupled solved-current results remain visibly distinct; neither silently replaces the other.
- Voltage/current/thermal/drive feasibility refusals and SPICE model provenance remain intact.
- Only M05 owns device and controller result state; cache keys include the actual drive/cooling/device revision.

### M06 — Catalog/passports/Configure/reporting

- Shared/private/workspace/published resolution, config visibility and ownership precedence are unchanged and fail closed.
- Loading a duty does not silently rewrite a die; locks, explicit choices and automatic transition rules remain intact.
- Passport/Configure estimates retain source geometry/build length, interpolation bounds, drive variant and validation labels.
- Report snapshot uses matching machine/duty/run identities; no foreign last-result data and no report-triggered solve.
- PDF/DOCX numbering, tables, warnings, solved current and per-duty source labels remain equivalent; numeric rows are unchanged.
- M06 owns catalog definitions/projections only; results go through M12, visibility through M08, material/device mutations through their owners.

### M07 — CAD

- Identical canonical inputs produce unchanged geometry/topology, dimensional values and artifact manifest content apart from explicitly non-semantic container metadata.
- Full/simplified outputs remain distinct; simplified envelope cannot include internal geometry.
- All source parameters/materials/build revisions remain hashed and linked to generated artifacts.
- Roundtrip produces an explicit validated proposal; it cannot silently mutate a die or active simulation.
- Full/private artifacts are gated by M08; BOM/supplier costs never reach public routes/reports/catalog payloads.
- Rebuild queue, cache and artifact paths are owned by M07 and contained within the selected build root.

### M08 — Access

- Server policy is applied to list/detail/export/history/download paths, not merely UI navigation.
- Anonymous/user/engineer/admin, workspace ownership and per-config grants retain the same decision matrix.
- NDA acceptance alone does not imply applicant verification; full versus simplified model policies remain distinct.
- Session/credential failures and unknown config visibility fail closed without falling into another workspace.
- Only Access writes identity/grants/verification state; lifecycle callers use owner operations, keeping audit/retention semantics.
- Private data, tokens and verification documents never escape through caches, logs, support context or public projections.

### M09 — Accounts/analytics/communication

- Preference and last-motor records are scoped to the authenticated user; selecting a reference does not load the owner's live machine.
- Export/deletion invokes owner APIs and preserves consent/security retention and pseudonymisation rules.
- Mailing uses current consent, unsubscribe and campaign deduplication; extraction sends no messages.
- Analytics/activity/job-use counters and aggregates preserve units, attribution and privacy boundaries.
- Support context is minimised and permission-filtered; drafts and confirmed requests stay separate.
- M09 exclusively writes account/communication/support/usage namespaces; credentials remain M08-owned.

### M10 — Optimisation

- The same candidate/seed/evaluation order yields bit-identical FEM results, decisions and ranking; no algorithm tuning in extraction.
- Every authoritative evaluation follows the existing `refine_proc` path; unconverged/infeasible candidates remain rejected.
- Screening/estimated values cannot be promoted as final validated FEM results.
- Cache keys preserve all effective inputs, no-op override semantics and source/solver identity.
- Failures/rejected evaluations and campaign provenance remain recorded; nothing is pruned merely to simplify storage.
- Resume/cancel/resource budgeting and active-machine isolation remain intact; only explicit save applies a candidate.

### M11 — SDK/system graph

- Contract versions, units, coordinate frames, signal conventions and conformance checks remain stable.
- Component cards retain complete/partial/unknown/placeholder distinctions and source hashes.
- SDK calls the same domain service as HTTP; no second physics implementation or route-private imports.
- System graph remains read-only and its source anchors/loop descriptions match the actual code after moves.
- External/MCP access uses the same M08 decision; draft sandboxes never mutate the active machine.
- Registry/component caches include source/config visibility context; future persisted assembly state has one explicit owner.

### M12 — Coupled/runs

- EM/thermal/controller/mechanical order, stopping rules, tolerances, warm-start timing and final passes remain unchanged.
- Numerical results and convergence/limit classifications are bit-identical; no extra solve is introduced by extraction.
- Each filed result carries one coherent geometry/material/duty/drive/solver identity and the actual used inputs.
- Run annotation uses identity-checked owner APIs; a newer last result is never overwritten by an old workflow.
- `record:false`, cancellation, background jobs and error paths preserve existing no-write guarantees.
- S1/time-to-limit/duty-cycle provenance and serializer fields are retained; only M12 mutates filed cross-domain records.

### M13 — Platform

- Workspace/caller/material/recording context propagates across threads and worker processes with no cross-tenant fallback.
- Writes are contained, atomic and locked at the correct scope; shared catalog remains read-only outside explicit owner commands.
- Worker limits, priorities, cancellation/process-tree cleanup and restart/resume semantics are unchanged.
- Infrastructure imports no concrete policy/solver implementation except registered composition adapters.
- Cache eviction does not erase authoritative last/run records; worker echo cannot overwrite unrelated state.
- Queue/progress/remote-compute records retain IDs, timestamps, status and usage attribution; no secrets appear in artifacts.

### M14 — Web shell

- Role/navigation/localisation behaviour and existing EN/ZH parity remain unchanged.
- Features export public hooks/components/types; common UI does not import feature stores or business rules.
- Browser state is user/workspace scoped; sign-out/switch-user cannot restore another user's machine or results.
- Inputs/refusals/progress/cancel remain accessible and truthful; no estimates become solved values through formatting.
- The shell stores presentation preferences only; consent and engineering state remain owner-controlled.
- UI decomposition preserves hook/effect order, request count, payloads and stale-result indicators.

## 8. Post-launch order, small PRs and estimates

Estimates are **engineering effort**, not elapsed solver time or a launch commitment. One day means roughly 6 focused engineering hours. Ranges include code review and focused verification; heavy physics baselines need separately scheduled sandbox capacity. Every listed batch is split into the indicated small PRs; avoid “move everything” PRs. Runtime outputs must remain unchanged. Where a current inconsistency is discovered, retain compatibility behaviour and file a separate bug change rather than silently correcting it in a move.

| Order | Reviewable batch / PR size | Effort | Why first / completion evidence |
|---|---|---|---|
| 0 | Baseline and ownership manifest; 1 PR with no runtime changes | 1–2 days | Save deterministic input/output fixtures, source/solver versions, state-path manifest and exact route payloads. Record environment and existing nondeterminism before claiming byte equality. |
| 1 | Report-only Python/web rules; 1 PR | 1–2 days | Visible debt list and artifacts; no existing CI gate altered. Checker self-fixtures cover relative/lazy imports and aliases without importing the app. |
| 2 | Cooling boundary; 3 PRs: extract current storage port; lift existing mappers with compatibility policies; switch one caller at a time | 3–5 days | Highest-value repeated incident area. Compare Thermal/Coupled/Controller effective requests across manual/air/liquid/robotics/propeller and per-user saved contexts; unchanged payloads before solver validation. |
| 3 | Stator/slot feasibility boundary; 2–3 PRs: pure primitives, validator/builder callers, browser bounds/parity | 2–4 days | 15 October construction incident. Exact polygon/node hashes and invalid-case errors for wire width/split, slots, fillets and symmetry; if duplicates disagree, stop consolidation and separate the bug decision. |
| 4 | EM result/loss-snapshot port; 3 PRs: wrap current store, switch Thermal, switch Coupled annotations | 3–5 days | Removes foreign cache inspection/mutation while keeping cache key bytes and last-run protection. Check stale/foreign/missing run and `record:false` paths. |
| 5 | Catalog machine snapshot and activation port; 3–4 PRs: snapshot reader, CAD reader, mass reader, activation coordinator | 4–7 days | One read policy replaces repeated merges; preserve caller filtering, private overlays, locks and duty geometry choices. Snapshot bytes and mesh-setting propagation match current entry points. |
| 6 | Access/lifecycle ports; 2–3 PRs: erase/export owner operations, visibility/metadata port, workspace policy injection | 3–5 days | Reduces direct identity-store rewrites and protects launch-sensitive permissions. Existing decision matrix, denial responses, audit writes and deletion retention must remain identical. Independent critical-security review. |
| 7 | Run repository and report snapshot; 3–4 PRs: serializers unchanged, results/fields port, snapshot adapter, renderer consumers | 4–6 days | Removes private last-store reads and protects report provenance. Existing report fixtures, numeric rows, source identity and PDF/DOCX semantic parity. |
| 8 | Web domain state ports; 3–5 PRs: duty apply, Simulation/Transient hooks, Thermal hooks, Configure/catalog, Mesh | 4–7 days | Removes browser key ownership leaks. Compare requests, local persistence keys, restore/selection and stale-result behaviour with existing frontend fixtures. No control redesign. |
| 9 | Thin thermal/controller/mechanical routes; 4–6 PRs, one callable family at a time | 4–7 days | Move current callable bodies behind domain APIs; routes and SDK adapt to them. API schemas/errors and domain outputs unchanged. |
| 10 | Optimisation scheduling/repository extraction; 3–4 PRs | 3–5 days | Preserve one evaluation path, cache semantics, campaign resume/cancel and rejection logging. Replay small fixed candidate fixtures; no exploratory sweeps. |
| 11 | SDK/contracts/composition cleanup; 2–3 PRs | 2–4 days | Remove route imports from capabilities, update source-linked graph, expose public component adapters. Contract/conformance and graph fixtures unchanged except source locations. |
| 12 | Remaining report/datasheet/CAD renderer splits; 3–5 PRs | 3–5 days | Move stable presentation helpers before numerical kernels; verify numeric/text/table/figure ownership and internal-data exclusions. |
| 13 | Numerical giants: EM/transient, mesh, cooling, mechanical/contact; 8–15 PRs, one cohesive helper group per PR | 10–20 days | Last because closure/order/cache changes can alter physics. Keep original expressions/reductions and solve order; require Sol review plus Astra assessment for physics-critical solver changes. |
| 14 | Make new-edge contracts blocking; remove expired compatibility shims; 2–3 PRs | 2–3 days | Only after extracted modules are clean. Preserve explicit baseline debt for untouched legacy areas; no mass rename or state-format migration. |

Total planning range: **49–87 engineer-days**, approximately **43–63 PRs** across the full program, not work to squeeze into launch week. The first high-value slice through order 4 is **10–18 days**. Re-estimate after that slice; the 9,574-line transient implementation inside `fem_solver_2d.py` makes the final kernel batch particularly uncertain. Estimates assume existing fixtures can be reused and do not claim all tests are fast or deterministic.

### Acceptance protocol for every extraction

1. Pin interpreter/library/BLAS/mesher versions, thread count, input bytes, geometry/material hashes, solver settings and seeds. Capture old/new in isolated, non-live workspaces. No baseline regeneration to make a move pass.
2. Compare numerical arrays by shape/dtype and exact bytes (including NaN representation where persisted), scalar payloads exactly, cache keys and authoritative state deltas. Existing run IDs/wall-clock durations may be recorded separately as nondeterministic metadata; do not exclude any physics/provenance field without review.
3. Compare route schemas/status/errors, permissions, storage paths, cancellation and no-write paths. Check that the extraction did not start a solve/import-time worker or change source precedence.
4. A pre-existing nondeterministic solver must first get a reproducible baseline/environment; tolerance-only agreement is **not** evidence of bit identity. Defer that extraction if exact comparison cannot be established. Different solver-commit provenance is expected and recorded, while input-to-output linkage is preserved.
5. Sol independently reviews physics-critical/safety-critical solver changes and Astra makes the final acceptance assessment; ordinary implementation can use Luna under the project's routing policy. No commits/pushes delegated without explicit assignment.
6. Rollback is reverting the path-limited extraction PR: retain old import compatibility wrappers and state formats until callers migrate. A numerical fix, new default, cache-key redesign, new privacy policy or storage migration is a separate reviewed change.

No validation in this protocol was run for this document. The present deliverable verifies static evidence and document completeness only.
