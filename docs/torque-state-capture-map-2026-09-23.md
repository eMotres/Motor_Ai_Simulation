# P2 torque energy-state capture map — 2026-09-23

This is a read-only map for the next energy-closure diagnostic; it does not
certify a periodic state or change solver behavior.

## Existing artifacts and gap

- `scratchpad/torque_full_state_20260923_run04/full_p2_state.npz` contains full
  P2 `A`, quadrature `Bx/By`, mesh/basis geometry and four frames (indices
  0, 1, 48, 49). `raw_waveforms.npz` and the raw result also preserve scalar
  current, linkage, torque, time and angle history. The public result includes
  phase currents/linkages and mechanical-angle samples, but not full `A` per
  frame or a same-run material/PM certificate.
- `scratchpad/torque_material_20260923_run06/assembled_materials.npz` and
  `assembled_curves.json` hold `nu_base2`, `_sat2` element IDs/B-H curves,
  `_mx_all/_my_all`, and `f_mag2`. This is assembly-only: it has no solved
  frame. Run04/run06 mesh and quadrature were verified identical, but the
  arrays were captured in separate invocations.
- `scratchpad/torque_energy_reconstruction_20260923.py` and
  `scratchpad/torque_full_state_periodicity_20260923.py` are read-only
  reconstructions/comparisons. Their endpoint energy differences are useful
  observations, not a certified closure tolerance.

## Exact in-solve capture point

In `src/motor_ai_sim/simulation/fem_solver_2d.py`, capture after the demag
re-solve loop has accepted its final `A2` and before the frame torque/linkage
bookkeeping (around lines 6576–6578). At that point, `A2` is the full P2 state;
the current frame's effective PM source/material state and mesh are still in
scope. Save/copy `A2`, actual source currents, effective PM arrays/source,
`nu_base2`, `_sat2` curves and element IDs, quadrature/basis identity, mesh tags,
and convergence metadata together, with hashes. If demag is enabled, capture
the post-update magnet state; the proposed lossless imposed-current gate should
instead disable demag and eddy physics explicitly.

`k` is the schedule/frame index, not an angle. `theta_eff = m_shift * spacing`
is in degrees; `_theta_samples` stores `math.radians(theta_eff)`. Preserve the
actual signed mechanical angle in radians alongside both indices. In the
two-segment 180° sector, one electrical period is `2π / pole_pairs`; the rotor
comparison must map by the negative of this rotation, account for scalar `A`
anti-periodic cut sign, rotate the B vector into the comparison frame, and
permute rotor magnet/material tags. The prior run04 mapping found magnet tags
100–106 cycle under this map; raw same-index or same-tag comparison is invalid.

Capture should be diagnostic-only and avoid altering/removing any transient
samples. The opt-in implementation is `simulation/p2_state_capture.py` and the
frame hook is after the accepted frame's `_psi2(A2)` and phase-current appends.
Use it around a direct solver invocation:

```python
from motor_ai_sim.simulation.p2_state_capture import capture_p2_states

captured = []
with capture_p2_states(
        lambda meta: meta["frame_index"] in {0, 48}, captured.append):
    result = fem_transient_sliding_band(...)
```

Only selected frames construct the snapshot. It includes read-only copies of
`A_z`, `Bx_quad_T`/`By_quad_T` (from one `b2.interpolate(A2).grad` evaluation),
quadrature weights, mesh and cell tags, basis maps, materials, PM/coil sources,
currents/linkages, actual angles/time, mode flags and raw convergence fields.
The consumer owns persistence; the hook writes no files and sets no periodicity,
settling, energy-closure, or torque-certification flags. Warm-up frames with
negative `k` are skipped before reaching this hook; their solved fields are not
captured. The hook is therefore a selected reported-frame snapshot, not a full
transient history dump.
