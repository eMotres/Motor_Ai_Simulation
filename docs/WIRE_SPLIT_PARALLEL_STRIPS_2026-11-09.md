# `wire_split` = strips in parallel within one turn (2026-11-09)

Owner intent: cut the flat wire by its width into strips to cut the AC (proximity) loss.
A split adds **no turn**.

## Meaning

`wire_split = N` lays N strips of `wire_width` side by side across the slot, `2 x wire_spacing_x`
apart. `wire_width` is ONE strip. The N strips of a wire row are N **parallel** conductors of
**one** turn.

| quantity | rule |
|---|---|
| `turns_per_coil` | `num_wires_per_slot / wire_parallel` (the split is not in it) |
| `conductors_per_slot` | `num_wires_per_slot x wire_split` (drawn, meshed, separately excited strips) |
| `n_parallel_effective` | `n_parallel x wire_parallel x wire_split` (the current divider) |
| current in one strip | `I_phase / n_parallel_effective` |
| slot MMF | `conductors x strip current` = `turns x I_coil` (the unsplit machine's) |
| psi, back-EMF, Kt, KV | unchanged by the split |
| R_phase, DC copper | those of the same copper in parallel (N strips of the same total section: unchanged against the bar) |
| solved AC copper | falls with the strip width (width-direction proximity term ~ strip^2) |
| J, `A_phase_mm2` | per strip: `I / n_parallel_effective / A_strip`; phase section = `A_strip x n_parallel_effective` |

The strips are treated as **equal-current** conductors (each is a drawn, meshed conductor with its own
imposed `1/N` current, i.e. ideally transposed). The circulating current that unequal leakage-flux
linkage would drive between untransposed side-by-side strips is not solved.

## What was wrong

From 2026-09-08 to 2026-11-09 the strips were counted as consecutive **series** turns
(`turns_per_coil x N`, `n_parallel_effective` without N). `PUT /api/geometry
{"num_wires_per_slot": 17, "wire_width": 3.2, "wire_split": 2}` read back `turns_per_coil` 34 and
doubled the ampere-turns.

## Slot width

The slot width derivation is unchanged: `N x wire_width + (N-1) x 2 x wire_spacing_x` (the wire
column, `winding.winding_footprint_mm`) plus the usual `2 x wire_spacing_x + 2 x insulation_thickness`.
The only width a split adds is the insulation between its strips: a 6.4 mm bar cut into two 3.2 mm
strips with `wire_spacing_x = 0.05` widens the column by exactly 0.1 mm. Keep `wire_spacing_x` at the
real strip insulation. That 0.1 mm is a lamination change on a die that is shared or locked, see below.

## A geometry edit that creates a die says so

An edit that changes the die's lamination (a die-defining key, or the derived slot width of a die
shared with other configurations) moves the active configuration to another die and mints one when no
die matches (`family.sync_active_die_geometry`). The interactive UI keeps that behaviour (default).

`PUT /api/geometry` now answers `die_transition` in every response (`null` when the save stayed in the
active die, else `{reason, from_die, to_die, created, config, diffs}`).

A scripted caller sends `allow_die_transition: false` (JSON body field, or `?allow_die_transition=false`):
if the edit would move the configuration to another die, the answer is **409** with the die it would
have created (`detail.die_transition`) and **nothing is written or created**.

## Consumers checked

`winding.py` (helpers), `fem_solver_2d.py` (n_wires = conductors, n_parallel = effective), `field_ops.py`
(`copper_loss_W`), `solver_2d.py` (analytical loss inputs), `routes/simulation.py` (J, A_phase, summary),
`api.py` (`/api/winding/config`: `amp_turns_per_slot = turns x I_coil`), `optimization/design_eval.py`
(turns, paths and the conductor count of its copper loss), `datasheet.py`, `passport.py`, `report.py`,
`routes/thermal.py` (copper heat counts every strip), `masses.py`, CAD (`cadquery_geometry.py`),
web (`motorScaling.ts`, `ConfiguratorPanel.tsx`). Stored passports of a split machine were measured
under the series reading and are not migrated.

## Open

The die-transition refusal is a pre-write check on the geometry the save will produce (the stored
derived fields refreshed); the constraint clamp that runs after the write is not part of the check.
If a clamp moves a die-level key after the check, the post-write sync still honours
`allow_die_transition: false` (nothing is created; the response says `die_transition.blocked`).
