# `wire_split`: current meaning, slot width, die transitions (2026-11-09)

## Current meaning (unchanged, owner decision 2026-09-08)

`wire_split = N` lays N strips of `wire_width` side by side across the slot, `2 x wire_spacing_x` apart;
`wire_width` is ONE strip. The N strips of a row are **consecutive series turns** (so untransposed
side-by-side strips carry no circulating current):

- `turns_per_coil = (num_wires_per_slot / wire_parallel) x wire_split`
- `n_parallel_effective = n_parallel x wire_parallel` (the split divides no current)
- `conductors_per_slot = num_wires_per_slot x wire_split`

So `num_wires_per_slot = 17`, `wire_width 3.2`, `wire_split 2` is 34 turns and doubles the ampere-turns
against one 17-turn bar. To get the same MMF back, halve the current or the rows.

The alternative reading (strips in PARALLEL within one turn, to cut AC loss without adding turns) is
implemented on branch `wip/wire-split-parallel` and is NOT active; it needs an owner decision.

## Slot width

The derivation is `N x wire_width + (N-1) x 2 x wire_spacing_x` (`winding.winding_footprint_mm`) plus the usual
`2 x wire_spacing_x + 2 x insulation_thickness`. The only width a split adds is the insulation between its
strips: cutting a 6.4 mm bar into two 3.2 mm strips with `wire_spacing_x = 0.05` widens the column, and the
derived slot width, by exactly 0.1 mm (7.0 -> 7.1 mm). On a shared or locked die that is a lamination change.

## A geometry edit that creates a die says so

An edit that changes the die's lamination moves the active configuration to another die and mints one when no
die matches (`family.sync_active_die_geometry`). The interactive UI keeps that (default).

`PUT /api/geometry` answers `die_transition` (`null`, or `{reason, from_die, to_die, created, config, diffs}`).
A scripted caller sends `allow_die_transition: false` (body or `?allow_die_transition=false`): if the edit would
move the configuration to another die, the answer is **409** with the die it would have created and nothing is
written or created.

## Project links and die transitions

A configuration created in a die joins a project only if that project already holds a configuration of the SAME
die. Exception: an automatic die TRANSITION (an edit of the lamination of an already linked configuration) is the
same customer motor, edited, so the configuration it lands in the new die keeps the link
(`autolink_new_config(..., transition=True)`; the new die's `derived_from.origin` is `transition`). An explicit die
duplicate (`origin: duplicate`), "save as new die", a rename-copy and configurations created in them never inherit a
link.
