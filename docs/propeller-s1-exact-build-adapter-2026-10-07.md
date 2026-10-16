# Propeller S1 rating adapter for an exact saved build

**Status:** read-only design audit; no solver, route, UI, service, or FEM changes made.  
**Scope:** `scratch_s1_20261006/default40-release` source snapshot.  
**Conclusion:** the code has reusable single-point EM/thermal and S1-current-rating seams, plus a propeller torque model. It does not yet calculate a qualified propeller-loaded maximum continuous RPM. Treat any future result as an unpublished candidate until every required gate is explicit and true for the same operating point and exact saved build.

## Current behavior and reusable seams

`routes/coupled.py::_em_run(body, *, coil_temp_c, magnet_temp_c, ...)` (around line 3532) calls the canonical `routes.simulation.get_fem_transient` path. `coupled.py::_thermal_solve(body, cooling, *, coil_temp_c, magnet_temp_c, rpm, ...)` (around line 3876) calls the shared `routes.thermal.solve_thermal_field`. These are the suitable per-point solve seams: run EM at a proposed current and RPM, form a thermal field using that run's losses, feed temperatures back, and repeat to thermal convergence. Do not duplicate their field or loss calculations.

`coupled.py::_s1_verify(...)` (around line 7307) verifies a continuous-current estimate with real EM and thermal passes, at its supplied RPM. `_continuous_rating_for_loop(...)` (around line 7493) obtains a steady map and rates continuous current at that same RPM. The ordinary coupled `solve_to="continuous"` path also rates the requested RPM; it is not an RPM search and has no prop torque target. It cannot alone support the requested propeller S1 point.

The future adapter therefore needs an outer operating-point search. At each trial RPM, it must find a thermally/electrically steady EM torque-capability point under the saved drive/pack and exact materials, and compare the resulting motor torque with the load torque from `propeller.torque_Nm(prop, rpm, rho)` (`propeller.py:520`). The existing `propeller.equilibrium_rpm(...)` (`propeller.py:659`) intersects a supplied motor torque-speed envelope with the prop curve, but its documented motor curve is a passport feasible envelope. It does not produce that curve from the temperature-dependent coupled solver, so it is useful only after actual qualified coupled points exist and only within their supported range. Do not extrapolate a coupled torque envelope or treat a passport curve as the live qualification.

The existing propeller `operating_point`, `torque_Nm`, `rpm_for_torque`, `equilibrium_rpm`, and `air_speed_for_thermal` helpers are the authoritative prop-model seams. Preserve each helper's validity/extrapolation indicators. Model assumptions include the configured propeller data, air density/ambient, and slipstream position; the module documents simplifying wake/position factors and excludes forward-flight/climb effects. A result must state that basis and refuse qualification outside supported prop data unless a separately validated policy explicitly permits it.

## Exact saved-build boundary

The future job must be created only from a server-side, authenticated, active saved simulation build. Do not accept arbitrary Configure-edited geometry, winding, current, material, battery, drive, or mesh as a request to qualify a new build. Configure may request/display a result, but it must not itself manufacture the authoritative build snapshot.

Before enqueueing, resolve and require all of these to agree: authenticated owner; `/api/me/last_motor` selection (`ref_id`, die, config); `family._read_ctx()` active context; active duty/build snapshot; and the loaded simulation configuration. If the reference/configuration cannot be resolved to a saved immutable build, refuse. Do not activate, load, save, or mutate the user's current machine as a side effect. Copy the resolved canonical snapshot into a job payload at enqueue time; a worker must never reread mutable “current” configuration halfway through a run.

`routes.simulation._config_physics_fingerprint(with_request_materials=True)` (around line 1409) is a useful existing subfingerprint: it covers config geometry/winding/materials/magnet/part states, live geometry, and normalized request material override. `_geometry_fingerprint()` (around line 1461) is explicitly geometry-only. Neither is a complete saved-build identity. `_coupled_canonical()` (around line 4599) adds the normalized run body and resolved cooling, but it still is not an owner/ref/build identity record. Build a canonical full identity object, then hash its canonical serialization; retain the unhashed components in the result for audit.

The full identity should include at least:

- authenticated owner scope, reference ID, die/config, active duty and immutable saved-build/snapshot revision or content hash;
- live geometry and geometry source; winding turns, wire dimensions, connection/parallel strands; all part inclusion/reference states;
- material assignments **and resolved material-card contents/source versions**, including magnets, plus request-scoped override;
- exact mesh definition and solve discretization/settings that can affect the EM or thermal fields;
- drive/controller device and parallel count, switching/modulation/deadtime/device losses where applicable, battery chemistry/cells and min/nom/max voltage;
- cooling source/mode, ambient/air density, propeller ID plus canonical propeller definition/content hash, propeller position, and thermal boundary settings;
- code/solver/model version identifiers and operating-envelope policy version.

`material_context.set_request_materials()` / `get_request_materials()` (`material_context.py`) is request-scoped ContextVar state. An asynchronous worker must install the frozen material override for each job, retain/reset the ContextVar token in `finally`, and fail closed if resolved materials do not match the enqueued identity. Ambient process/session state is not a reliable job input.

Existing `thermal_settings.cooling_fields()` (`thermal_settings.py:106`) maps propeller source to cooling fields; `propeller_cooling_context_issue()` (`:329`) guards the account + last-motor selection + active die/config + allowed prop assignment. That guard is useful admission control but does not cover geometry, winding, materials, mesh, drive/pack, duty, prop data hash, or position. It is not a qualification key. The current mapper does not carry an independently selected propeller position through to the thermal solve; either freeze the established default position and record it, or add explicit propagation before claiming that position is selectable.

## Search and qualification workflow

1. **Freeze and admit.** Resolve the active saved build, validate the owner/ref/context and allowed assigned propeller, resolve all source records, hash the complete identity, and persist a `queued` candidate record. Fail before compute on any mismatch or missing identity component.
2. **Search only inside a declared domain.** Derive RPM/current bounds from saved drive, battery, and motor constraints. At each trial, use the canonical coupled EM/thermal seams, with the same full identity and actual prop cooling basis. Iterate current/temperature and control/drive state until their existing convergence gates settle. Obtain motor torque from the actual EM result, not a copied passport number. Evaluate the propeller load torque at that same RPM/ambient using the canonical prop helper. Search the feasible intersection (or bracket and refine it); record raw solve IDs/hashes, requests, outputs, convergence evidence, prop torque/power, and whether interpolation/extrapolation occurred at every accepted point. No assumed monotonicity or extrapolation beyond the modeled/solved bracket without explicit evidence.
3. **Apply gates to the same final point.** Require explicit successful steady-state/thermal convergence, EM nonlinear convergence, requested eddy/demagnetization settlement, torque equilibrium within a declared tolerance, controller/device current and junction-temperature limits when a drive is present, pack voltage/current/power feasibility, winding/magnet/bearing temperatures against resolved material or saved limits, and mechanical rotor/sleeve safety factor at the final RPM, torque, and solved temperatures. Any false, missing, unknown, stale, or mismatched gate means `qualified=false`; retain the candidate and the reason, but do not expose it as a Configure-ready rating.
4. **Recheck identity and publish atomically.** Before finalizing, recompute the current active saved-build identity. If it differs from the job snapshot, persist a stale/unqualified result. Write a separate immutable qualified-operating-result record keyed by the complete identity and policy/source versions. Do not overwrite passport records, coupled “last” state, normal duty results, or the active simulation configuration.

There is no existing unified electrical/thermal/mechanical qualification gate. `configure_limits.current_limit()` (`configure_limits.py:49`) and `thermal_limits()` (`:214`) provide some card/config limits, but a missing limit is not evidence of safety. The winding limit is a class-H default; magnet limit can be absent. Battery/controller values are Configure inputs, not proof that the solved operating point satisfies them. Require resolved limits with provenance for any bound needed by the proposed maximum.

`mechanical.limit_speed()` (`routes/mechanical.py:1157`) is a useful structural analysis seam, but its torque is held constant through its own speed search and its inputs include interference, contacts, temperatures, mesh, and target safety factor. Its result is not automatically coupled to the prop torque curve or final coupled temperature. Reuse only by invoking/validating it for the actual matched point and frozen build, with explicit structural limits. The coupled route's `run_rotor_stress_at` hook is also available (`coupled.py` around 4237), but the sleeve hoop estimate in `simulation.py::_sleeve_hoop` is explicitly a lower-bound estimate and does not include magnet pressure; it cannot be used as the structural pass gate. Bearings need a saved material/rating and validated speed/load/temperature bound. If bearing rating or another required strength limit is missing, the candidate remains unqualified; do not invent a default.

## Persistence and Configure contract

Use a separate collection/table for propeller-loaded continuous operating candidates/results, rather than passport, motor catalog, coupled history, or per-user thermal settings. A record should carry the full unhashed identity and its hash, immutable request/config snapshot, status, solved RPM/current/torque/power, prop curve point and basis, every gate with value/limit/source, raw EM/thermal/mechanical solve references and content hashes, solver/build version, timestamps, and refusal/staleness reason. Writes should be immutable/versioned; only a completed record whose gates are all explicit `true` may have a publishable status.

Configure should ask for the server's latest result matching the *currently selected saved reference/config/build* and complete identity hash. Exact equality is required, including prop source hash, drive/pack, cooling basis, materials, and mesh. If the user has unsaved edits or any field differs, do not silently reuse the result or treat it as a rating; show that no matching qualified result exists. Keep legacy/manual paths unchanged. Never reinterpret a fixed-RPM S1 current rating as a propeller maximum RPM.

## Non-FEM verification seams

- Unit-test identity canonicalization using immutable fixture snapshots: one-field changes to geometry, `parts` state, winding, material content/override, mesh, drive/controller, battery cells/voltage, ambient/position, prop source content, owner, ref/config, or duty must change the identity; canonical-equivalent inputs must not.
- Test admission with patched auth/account/family/config resolvers: active exact saved build enqueues; wrong owner/ref, inactive context, changed loaded geometry, absent saved revision, forbidden prop, unresolved material/limit, or unsaved Configure edits refuse without calling a solver.
- Inject fake EM, thermal, prop, and mechanical callbacks into a pure search/orchestrator seam. Test a bracketed torque crossing, no crossing, out-of-domain/extrapolated prop point, nonmonotone/insufficient samples, each false/missing gate, interruption, stale identity at completion, and atomic persistence. Assert each callback receives the same frozen identity and point temperatures/current/RPM as applicable. No FEM or live API is needed.
- Test Configure’s result matcher with exact identity, one-field mismatch, stale/pending/failed/unqualified record, and legacy/no-result behavior. Ensure a failed or incomplete candidate never renders as a maximum rating.

**Audit limits:** this note proposes integration boundaries, not a validated propeller S1 algorithm. No operating points were solved for this task. Parent review and a separately validated search/gate policy are required before implementation or any qualified claim.

## D85 assigned-propeller source-domain audit (read-only)

The fetched D85 propeller inputs are the four current T-Motor G-series YAMLs under `scratch_s1_20261006/d85_finish_20261007/propeller_inputs/`. Their measured rows marked `use_in_fit: true` span these source RPMs: G30×10.5 2679–4654 rpm; G32×11 2485–4397 rpm (the separate 48 V/KV130 table is explicitly `use_in_fit: false`); G36×11.5 2595–4328 rpm; G40×13.1 1843–3546 rpm (across its included 50/60 V tables). These are raw included-row spans, not claims that every point survived the fit's outlier filter; the runtime's actual `Propeller.rpm_range` is the intersection of fitted C_T and C_P ranges and must be read from the unchanged `_build()` result for the exact source hash.

`propeller.py::CoeffFit.value()` clamps coefficients to their fitted edge outside `rpm_min..rpm_max` and returns `extrapolated=true`; `coefficients()` and `operating_point()` expose that flag. Therefore, for the candidate low-speed region around 1000–2400 rpm, G30/G32/G36 are below their included measured rows throughout or almost throughout; G40 is also extrapolated below its lower fitted boundary (whose exact value depends on the fit). A computed load torque in this region is an edge-held model extrapolation, not a measured low-RPM curve. Preserve and surface the flag; do not qualify a maximum from these propeller estimates without a separately validated low-RPM model or in-domain supporting data.

The YAML `published_limits.optimum_rpm` intervals (G30 1300–3000; G32 1200–3000; G36/G40 1000–2800 rpm) are labeled optimum operating intervals, not mechanical maximum-RPM limits. Their thrust limits and stated ambient intervals likewise do not establish an assembly structural speed limit. All four source entries state static-hover-only coefficients and ISA sea-level test density 1.225 kg/m³ is assumed because the cited tests omit ambient pressure/temperature. The operating-point helper can recalculate density from ambient/altitude, but that remains the catalogue model's density scaling and wake/slipstream assumption, not an ambient-specific prop test.

For identity provenance, the copied production `propeller.py` SHA-256 is `0a431a2bdbc0ec45d65b139d35db43ebcaf35d7754fc9092394749b73de95494`; the four YAML SHA-256 values supplied with the source audit are: G30 `61490ad6b0a16512b1a833be0a3a7f4dcd43c5cb4df8f4ee71b9257392f933e3`, G32 `2915e99db5ef4780ee7ffd8be4f01957903863e665b3e20cd0576dfad88a8dbe`, G36 `1b38efbddb50b3213f099c843b6eecddf0d1255cc5fc2e4b2ace0085a1677e86`, G40 `da08590382e784fc8221deba58914a4053f8a7186f7ac4bfe49270764727e8c3`. Bind a result to the exact YAML content and implementation hash; assigned propeller ID alone is insufficient.

### Independent numeric-domain check

Parent executed the unchanged local `_build()` and `operating_point()` against these four copied production inputs, without FEM or an API. The LF-normalized local module hash equals the production hash above. Actual fitted CT/CP intersection domains equal the source spans:

| Propeller | Fitted RPM domain | Model torque at 1000 RPM, N m | At 2000 RPM, N m | 2000 RPM extrapolated? |
|---|---:|---:|---:|---|
| G30x10.5 | 2679–4654 | 0.449564 | 1.798256 | yes |
| G32x11 | 2485–4397 | 0.553553 | 2.214211 | yes |
| G36x11.5 | 2595–4328 | 0.741205 | 2.964820 | yes |
| G40x13.1 | 1843–3546 | 1.230506 | 5.020068 | no |

All four 1000 RPM points are extrapolated. `power_estimated=false` records measured-torque-based CP fitting; it does **not** override `extrapolated=true` for the evaluated point. These are calculated catalogue-model values, not motor/propeller assembly measurements or S1-qualified points. Static hover, ISA 1.225 kg/m3, and default behind-hub factor 0.4 were retained. Full values, flags, source paths and hashes are in `scratch_s1_20261006/d85_finish_20261007/propeller_numeric_domain_audit.json`. Manufacturer optimum RPM intervals are not a structural maximum. A future search must preserve these separate evidence categories.
