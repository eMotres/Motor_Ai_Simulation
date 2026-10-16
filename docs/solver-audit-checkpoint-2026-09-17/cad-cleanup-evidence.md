# Bounded cleanup of the canonical mouth primitive

This is an isolated candidate. No live source, solver formula, tolerance,
global arc sampler, or generic shared-boundary welding algorithm was changed.

## Why a generic intersection or angle rule was rejected

The mouth-only candidate exposed 48 short-edge runs on the 100 mm fixture:
24 distinct geometric runs, each appearing once in the stator and once in
`out_band[0].hole0` with reversed traversal and exchanged exterior neighbors.
All 24 pairs agree point-for-point (comparison rounded at 1e-10 mm).

For a representative final run, the two points are
`(42.029018274128894, -9.45)` and
`(42.029018274128894, -9.45920022638908)` mm. Its external supports intersect
157.962 micrometers away from the nearest represented point, exceeding the
25 micrometer weld budget. That intersection is not a sharp feature already
represented by the sampled polygon. Extrapolating it is inadmissible. Selecting
the strongest turn instead is also not generally justified: such a rule could
erase a real asymmetric chamfer.

## Why the source-frame intervention is narrower

The actual CAD construction supplies an unambiguous frame and analytic tangent
point. The local circle center is `(11.261641505225734, 42.029018274128894)` mm,
radius `1.8116415052257349` mm. Its tangent to the vertical trapezoid wall is
the original point `p2 = (9.45, 42.029018274128894)` mm. An unchanged 27-segment
circle does not include the angle-pi sample and creates a join at
`(9.462249671758183, 42.029018274128894)` mm.

Existing cleanup in this **source frame** retains p2 and drops that sampled
join. The resulting complete primitive is then mirrored and rotated. This
does not infer symmetry from an arbitrary finished polygon or change the
generic sanitizer's representative-selection semantics.

The candidate accepts cleanup only if the output is valid, nonempty, has
positive area, preserves component/hole counts, every resolved weld-map move
is within the existing epsilon, and complete boundary Hausdorff distance is
within the same epsilon. The latter also covers collinear deletion and repair
operations absent from the weld map. A failed certificate retains the original
primitive; those fallback cases are not claimed to have solved final export
symmetry.

## Measured prototype, motor_100mm

| Measurement | Result |
|---|---:|
| Local moved vertex | 0.0122496717582 mm |
| Local boundary Hausdorff change | 0.0122289472523 mm |
| Existing weld epsilon | 0.025 mm |
| Local cutter area increase | 0.0012881677752 mm² |
| Local vertices before/after | 24 / 23 |
| Final global stator/out-band short runs before/after | 48 / 0 |
| Final stator quarter-turn symmetric difference | 0 mm² |
| Final stator quarter-turn boundary Hausdorff distance | about 3.5e-14 mm |
| Final stator change against mouth-only export | -0.416231836 mm² |
| Final stator boundary Hausdorff change | about 0.009200219 mm |

All stator boundary vertices still occur in the shared out-band boundary.
Canonical cleanup is cyclic/reversal independent for this primitive. Repeated
local and final-stator cleanup preserve point sets and shape.

The final area change is larger than 24 times the local cutter area change.
Downstream Boolean/fillet operations respond to the changed corner structure.
This is a bounded systematic geometric correction, **not** merely floating
point roundoff. Mesh correspondence and torque comparisons remain separate
validation gates.

## Preserved evidence and tests

* Original live CAD: `original/cadquery_geometry.py`, SHA256
  `E2D29B692D245D672EFD19D9A5947140E0F9A8FFED30A24DF2B055A690F4BCDB`.
* Mouth-only candidate: `original/cadquery_geometry-mouth-only.py`, SHA256
  `291B64CDD78E9BB418F1C9114950BBF039CD01C326C73C64EBDC56CE434A667C`.
* Guarded cleanup candidate: `src/motor_ai_sim/cadquery_geometry.py`, SHA256
  `17D39AF02B85E5CB228C4F82A9C2903A35D61FB58552FB6E31441FEC58239EBA`.
* Process-only proof and raw result: `original/check_canonical_mouth_cleanup-prototype.py`
  and `canonical-cleanup-results/motor_100mm.json`.
* Current permanent tests: `tests/test_stator_mouth_symmetry.py` and
  `tests/test_stator_local_cleanup.py`.

Tests cover unchanged arc sampling, exact retained analytic tangent, rotated
and mirrored primitive copies, zero/negative/tiny radii, over-budget close
chains, unmapped boundary and topology changes, cyclic traversal/reversal,
idempotence, both export routes, six historical fixtures' shared boundaries
and topology, and the actual 100 mm stator's quarter-turn equivalence.

Historical CAD polygon-quality/geometry/validation gates are already copied
unchanged. Parent-observed Windows socketpair/network-guard failures are
environmental. The default fixture's unrelated rotor local-edge ratio 3.90%
versus a 4% threshold reproduced on both the original and mouth-only source.
