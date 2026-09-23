# Solver guards after the 2026-09-23 review (eddy body count, history snapshot, sleeve trim)

Follow-ups from the pre-deploy review of d785a4c / e896b31 / 2162716. Three
small, exact changes in `src/motor_ai_sim/simulation/fem_solver_2d.py` and
`sb_postproc.py`; tests in `tests/test_solver_guards.py`.

## 1. Eddy conductor-body count guard (`check_eddy_conductor_bodies`)

Since e896b31 the coupled eddy solve imposes `∫J dΩ = ±I_branch` per meshed
conductor body (`Iunit = ±1`, `docs/eddy-series-current-normalization-2026-09-23.md`).
The slot's ampere-turns are therefore `bodies_in_slot × I_branch`, equal to
the magnetostatic source's `n_wires × I_branch` ONLY when the mesh holds
exactly `n_wires = num_wires_per_slot × wire_split` bodies per slot
(`winding.conductors_per_slot`). The old area-weighted `Iunit` stayed right
when the CAD dropped, merged or clipped a body; the new one is wrong by one
branch current per missing body, silently. So the count is now checked where
the bodies are built (`_coil_con`, both element orders read their currents
off it), before any current is imposed:

* per slot: body count == `n_wires` — the count does not depend on
  `wire_parallel` (strands in hand are rows of their own), nor on the bonding
  mode (transposed / parallel / series paths group the bodies AFTER they are
  built; they never change their number);
* a body whose slot cannot be read off its material name is refused;
* a sector model must hold `num_slots / n_sectors` whole slots.

On mismatch: `RuntimeError` naming the slot, expected and found, and the
result carries `eddy_conductor_check` (`n_slots_meshed × n_wires = n_bodies`,
`area_spread_max_rel`, `area_warnings`).

**Areas — decided from the physics.** The constraint does not assume equal
areas: `∫J = I` holds for a body of any section; the area enters only that
body's DC reference `I²/S` and the eddy redistribution inside it. An unequal
area is therefore not a wrong ampere-turn — it is a body that is not the
wire the parameters describe (every conductor of a slot is the same
`wire_width × wire_height` strip). Two tiers, judged against the slot's
median conductor area:

* spread > 5 %: a clipped / shrunk stack (the CAD does that to fit the slot,
  `geometry_validation` already reports it as a WARNING, the machine solves
  with less copper than described) — logged and recorded, not refused;
* a body under 50 % of the median: not a strip of that wire (a sliver, a body
  cut by the sector boundary); a full branch current imposed on it is not the
  machine — refused.

Measured on the real meshes today: Ø30 mm fixture 0.17 % spread (Codex's
saved number), Ø200 L155 at 6 mm mesh 0.087 % (6 slots × 10 = 60 bodies on
the two-sector model). Neither tier fires on a healthy drawing.

## 2. `snapshot_scalar_history` can no longer fail a finished solve

It raised `TypeError` on any sample `np.isscalar` rejects, including a 0-d
numpy array, and the call was unguarded — a bookkeeping copy could throw a
finished solve away. Now numpy scalars and one-element arrays are scalars
(`.item()`), a genuinely vector channel is left out of `samples` and named in
`skipped_series` with the reason (`non-scalar sample at index i: ndarray
shape (100,)`), and the solver wraps both the snapshot and the retained-window
metadata in `try/except` that logs and leaves the history absent.
`retained_window_metadata` carries `skipped_series` into the record. The
retention test that asserted the `TypeError` now asserts the skip.

## 3. Settle-trim list `_v2_lists` — missing per-frame series

`_ed_sl` (sleeve `σ∫E²` per frame) was not in `_v2_lists`, so on voltage /
PWM eddy runs `P_sleeve` was averaged over the settling frames too, and
`P_tot_ser2 = zip(P_cu, P_fe, P_mag, P_shaft, P_sleeve)` paired the trimmed
series with the sleeve's SETTLING prefix. Enumerating every `.append` inside
the P2 frame loop against the tuple (now a test) found one more:

* `_bgap2` — air-gap mean |B| per frame, exported as `B_gap_mean_T` and
  averaged by the summary builder; it too averaged the settling frames and
  came out 132 frames long on a 12-frame window.

Both are in `_v2_lists` and in the raw scalar snapshot
(`eddy_sleeve_power_W`, `air_gap_B_mean_T`). Everything else the loop appends
is either trimmed already or is not a per-frame series (`_frames2` keyframes,
`_inc_rows`, the `_pic_fallback` / `_pic_unconv` frame-INDEX lists — those
keep untrimmed indices, a diagnostic only —, `_v_bpsi`, `_warm_ks` /
`_warm_solid`, `dc_anchor_A` events); the test lists them explicitly.

### Old → new on a sleeved voltage-drive eddy case

Ø200 CIANO10 200 opt / L155 motor (M40X_UD_60 sleeve 2.5 mm, σ = 100 S/m),
solver-direct, sandboxed config, BelowNormal, 2 threads: `drive="voltage"`,
380 V peak / 25° el, 14 200 rpm, delta, 2P, eddy + rotor_eddy, 12 steps per
period, 6 mm / 0.5 mm mesh, 2 sectors, magnets 104.2 °C, coil 97.8 °C;
252 frames solved (120 voltage-settling + 12 reported + eddy warm-up).
"Before" = git worktree of 68de0ca; "after" = this working tree.

| | before (68de0ca) | after |
|---|---:|---:|
| `P_sleeve_solve_W` | **8.2311** (mean of 132 frames, first 0.085 W) | **8.3252** (12 reported frames) |
| `P_sleeve_eddy_W` series length | 132 | 12 |
| `B_gap_mean_T` series length / mean | 132 / 0.93304 T | 12 / 0.93302 T |
| `P_mag_solve_W` / `P_shaft_solve_W` | 71.449 / 7.352 | 71.449 / 7.352 |
| `P_cu_ac_solve_W` | 605.16 | 605.16 |
| `T_avg_Nm` | 185.278 | 185.278 |

The sleeve loss moves +1.1 % (+0.094 W) on this case; every other solved
number is bit-identical (the last sleeve frame is 7.8685 W in both), which
is the proof that only the averaging window changed. `P_fe` differs between
the two legs (1362.9 vs 1296.1 W) because of Codex's core-loss candidate
commits between 68de0ca and HEAD, not this change. On PWM runs with a longer
settling prefix the sleeve delta is expected to be larger; L155/L180 client
reports that quote `P_sleeve` on a voltage/PWM duty should be regenerated.

## Tests

`tests/test_solver_guards.py` (17 tests, no FEM): body-count guard on
synthetic `_coil_con` rows (whole slots, wire_split strips, dropped body,
merged bodies with the right total copper, unreadable slot, sector slot
count, 0.17 % spread clean, clipped stack warned, half body refused, call
site by AST); snapshot accepts numpy scalars / 0-d / `(1,)` arrays, skips a
vector channel with a reason, the reason reaches `retained_window_metadata`,
the solver call is guarded; every `.append` inside the P2 frame loop is in
`_v2_lists` or in the explicit not-per-frame list, `_ed_sl` and `_bgap2`
present, both in the raw snapshot.

Run: `python -m pytest tests/test_solver_guards.py tests/test_p2_eddy_current_normalization.py tests/test_transient_sample_retention.py tests/test_p2_strand_conservation.py tests/test_wire_parallel.py tests/test_wire_split_geometry.py tests/test_physics_regression.py -q`.
