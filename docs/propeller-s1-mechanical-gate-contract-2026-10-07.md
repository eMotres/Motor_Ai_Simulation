# Propeller S1 mechanical gate: source contract

Date: 2026-10-07  
Scope: source audit of the release tree at
`scratch_s1_20261006/default40-release` and a pure evidence mapper. The mapper
does not invoke solver routes; no mechanical solve or FEM run was started.

## Finding

The existing mechanics solver does produce an averaged-stress minimum safety
factor at each solved mechanical load case. The project already has an
acceptance threshold: `motor_ai_sim.report.SF_ACCEPT = 2.0` at
`scratch_s1_20261006/default40-release/src/motor_ai_sim/report.py:1661`. The
report calls it “Where a rotor is judged”; it drives the part safety-factor
warning at `:5217-5226` and the safety-factor chart at `:3501`. This audit
therefore does not introduce a new target or use the limit-speed search's
default. The private gate must receive the existing constant's explicit
value and exact source-manifest hash as policy evidence, and bind that policy
to the result.

The canonical coupled response still does not carry enough evidence to turn
the value into an S1 pass/fail by itself: it keeps only a compact `sf_min`,
limiting-part name and a p05 diagnostic, while the per-part material criteria
and strengths are dropped. The current request/compact block does not carry
the threshold source hash. Therefore the gate remains **unknown** until the
existing report policy and a complete, same-point, same-build mechanical
result are bound together.

This is a provenance and threshold gap, not evidence that the rotor fails.
`sf_min`, `sf_min_p05`, and `sf_min_unaveraged` are distinct reported values;
only the first is the minimum averaged safety factor. Do not promote the p05
or raw element minimum to the governing factor.

## Source behavior

- `src/motor_ai_sim/routes/coupled.py:4160-4260` implements
  `_mechanical_step`. It is opt-in (`body.mechanical`), takes per-part
  temperatures selected from the converged thermal field, resolves actual run
  RPM and electromagnetic torque, and invokes the unchanged
  `routes.mechanical.run_rotor_stress_at` helper. `_bulk_temp` prefers the
  component average and has a maximum-value fallback; a verifier must apply
  that same documented selection to the captured field rather than trusting
  only the compact temperature echo. With no sleeve, the solver documents
  that temperatures do not load the rotor model; they only change retaining
  band fit pressure.
- `src/motor_ai_sim/routes/coupled.py:4245-4260` reduces the helper result to
  `ok`, `temps_c`, `rpm`, primary case name, `sf_min`, `sf_min_part`,
  `sf_min_p05`, growth/gap fields and elapsed time. It drops `cases[*].parts`,
  material strengths/criteria, mesh, contacts and solver convergence evidence.
  The coupled response's `coupling.mechanical.ok` means the hook returned a result; it
  is not itself a safety verdict.
- `src/motor_ai_sim/routes/mechanical.py:486-516` keys stress results by
  geometry fingerprint, material assignments and override names, RPM,
  overspeed factor, interference, mesh/order, case/load/torque, temperatures,
  contacts and symmetry. This is useful source behavior for cache provenance,
  but a cache hit still needs its key tied to the candidate's complete frozen
  identity.
- `src/motor_ai_sim/simulation/mechanical/rotor_stress.py:2959-2968` defines
  the averaging convention: element fields are area-weighted onto nodes of
  their own part, not across material boundaries. At `:3073-3139`, each
  part's averaged safety factor is computed with its material-specific failure
  criterion; the result carries `stress_convention: "averaged"`, the
  criterion, strength and governing stress. Steel uses von Mises against
  yield, magnets use principal tensile stress (and compressive strength when
  present), and a composite sleeve uses hoop-fibre tension (plus transverse
  strength when supplied). At `:3232-3305`, `sf_min` is the minimum of
  per-part `averaged` values; `sf_min_unaveraged` and `sf_min_p05` are separate
  diagnostics.
- `src/motor_ai_sim/simulation/mechanical/rotor_stress.py:2770-2779` shows
  the case binding: single mode names exactly one case by RPM; three-case mode
  uses standstill, rated at the requested RPM, and overspeed. For a one-point
  S1 candidate, require single-case mode or verify that the selected rated
  case's recorded RPM exactly matches the candidate. Do not take the
  overspeed case as the candidate operating point.
- `src/motor_ai_sim/simulation/mechanical/rotor_stress.py:351-410` builds
  mechanical properties from resolved material records / request overrides.
  `:3549-3674` returns the actual part temperatures, material properties,
  contacts, mesh, symmetry, cases and per-part evidence in the full mechanics
  result. These are absent from the compact coupled mechanical block.
- `src/motor_ai_sim/routes/mechanical.py:1186-1191` defines `target_sf` for
  the separate limit-speed search, defaulting to 1.0 and describing it as the
  rotor's structural limit. That parameter belongs to a search API; it is not
  a global S1 margin or an accepted motor design policy. The coupled block
  does not return this target.

## Mechanical evidence mapper

The smallest sound next step is to use the existing opt-in mechanical hook
inside the same converged coupled point, without changing its physics, and
preserve its full mechanical result plus the coupled run's exact input,
thermal-field and accepted source-manifest provenance in the private
evaluation record. The caller must pass the report policy value (2.0) and
`report.py` source hash explicitly; the mapper must not default to either. The
compact `coupling.mechanical.sf_min` alone is insufficient for an auditable gate.

The mapper follows the actual response nesting: `coupling` contains
`solve_to`, `mode`, `converged`, `runaway`, and the compact mechanical block;
the route does not duplicate these fields at top level. `_mechanical_step`
rounds its compact torque echo to three decimal places, so that value is
compared with the exact source rounding while the complete mechanics request
and result remain bound to the unrounded EM-scaled torque. Each per-part row
must also satisfy `strength_mpa / governing_stress_mpa == safety_factor`
within the explicit comparison tolerance. Matching copied safety-factor
fields cannot pass if the reported stress contradicts that relationship.

The pure mapper implemented for this increment is
`coupled_propeller_s1_mechanical_evidence.map_mechanical_evidence`. It expects
the exact private envelope described by its API, including the full unchanged
mechanics-helper result (hashed separately), the accepted source-manifest
entries, exact mechanical request/material hashes, thermal-field component
observations, and the explicit `SF_ACCEPT` policy. The currently published
bridge envelope does not yet supply that full mechanics result or evidence;
until it does, the mapper deliberately returns `unknown`. Its tests exercise
source-shaped fakes only and do not validate a physical point.

The mapper emits a separate `mechanical_within_limit` state and evidence
record:

```json
{
  "status": "unknown | within_limit | outside_limit",
  "target_sf": 2.0,
  "policy": {"name": "motor_ai_sim.report.SF_ACCEPT", "source_sha256": "sha256"},
  "observed_sf_min_averaged": null,
  "limiting_part": null,
  "case": null,
  "rpm": null,
  "part_temps_c": {},
  "geometry_identity": "sha256",
  "material_identity": "sha256",
  "mechanical_result_sha256": "sha256",
  "coupled_run_sha256": "sha256",
  "mechanical_request_sha256": "sha256",
  "source_manifest_sha256": "sha256",
  "thermal_field_sha256": "sha256",
  "stress_convention": "averaged",
  "criterion_by_part": {},
  "reason": ""
}
```

`target_sf` must be supplied explicitly from the report's established
`SF_ACCEPT` value of 2.0, with the matching `report.py` source hash. Do not
silently borrow the limit-speed search's default of 1.0. A returned factor at
or above the report threshold is not a claim of lifetime, fatigue, impact, or
manufacturing qualification; the source model is a static mechanical solve.

The mechanical torque input is the value computed by
`routes.coupled._shaft_torque_nm`: `T_em_avg_Nm` multiplied by positive
`end3d.k_flux` when present, otherwise the EM mean. It is not the propeller
torque reconstructed from delivered shaft power; preserve the EM torque,
mechanics-applied torque and propeller-required torque as separate quantities.
The verifier binds its thermal-field hash to the actual `response.thermal`
payload and verifies the captured component readings match that payload. It
then applies `_bulk_temp`'s `avg`, falling back to `max`, and rounds to
0.01 °C.

Return `unknown` if any of these checks fail: mechanical step absent/refused;
case/RPM mismatch; raw coupled or mechanical provenance not bound to the same
geometry, resolved material records, contacts, mesh, mechanical settings,
candidate torque and temperatures; no full part/material evidence; missing or
non-finite factor/target; averaging convention absent or not `averaged`;
cache evidence cannot be tied to its complete key; fallback-contact changes
are unapproved for the intended configuration; or contact/mechanical solve is
not a complete successful result. Return `outside_limit` only when a complete
same-point result is below the explicit target. Otherwise return
`within_limit` only when the same evidence is complete and `sf_min >= target`.

The current coupled block does not expose contact convergence, solver
iterations/residuals, the full case's `rpm`, per-part factors/criteria,
material strengths or actual materials, mesh settings, contact set, or the
mechanical cache key. A future integration must either preserve those fields
from the unchanged helper result and independently bind the cache key, or
perform an isolated, unchanged helper invocation with the identical inputs
and retain its full result. A second solve with merely similar temperatures
or a separately read Mechanical panel is not same-point proof.

## Minimal verification before enabling this gate

Use only a no-FEM mapper test first: provide a captured source-shaped full
mechanical response and coupled input envelope; test exact requested RPM,
all per-part temperatures, geometry/material/contact/mesh identity, primary
case selection, averaged convention, and explicit target comparison. Include
refusal cases for absent per-part details, p05-only/raw-only factors,
non-finite values, mismatched build/cache provenance, stale RPM/temperature,
missing target, and contact fallback. Then run one bounded private coupled
point with the unchanged mechanics hook only after its run is approved; retain
the raw request/response and source hashes. No solver tolerance or material
data should be changed to make a gate pass.

## Validation

The focused no-FEM mapper tests passed: `40 passed` in
`tests/test_propeller_s1_mechanical_evidence.py`; `git diff --check` also
passed for the owned mapper, test and note. The tests use source-shaped fake
responses. No thermal solve, stress solve, FEM, API, or server command was
run. Model: GPT-6; no escalation.
