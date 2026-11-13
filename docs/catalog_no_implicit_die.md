# Catalog: no die is created implicitly

Owner rule 2026-11-12: the application never creates a new die (catalog lamination entry) unless the user
explicitly saves one. Line numbers refer to `src/motor_ai_sim/routes/family.py` on trunk commit 9b84122c
(before this change).

## Paths that created a die, and what they do now

| # | Where | Trigger | Created | Explicit request? | Now |
|---|-------|---------|---------|-------------------|-----|
| 1 | `_transition_config_to_own_die` (~4960-5000), reached from `_move_duty_save` (~5094) in `_upsert_duty` (~3694) | `POST /duty` with `from_current`, live machine differs from the die in a die input or in the DERIVED slot width (wire width / spacing / split / liner) | die `<base> <user> <n>` (`derived_from.origin = "transition"`) + copy of the configuration | No (a result save). This is the `vadim 3 -> vadim 8` fork | Slot-width-only difference: result saved into the existing configuration (it owns the wire stack in `geometry_overrides`). Real die-input difference: moves only into an EXISTING die that is this lamination; otherwise 409 `new_die_requires_explicit_save`, nothing written |
| 2 | `_transition_config_to_own_die`, reached from `sync_active_die_geometry` (~5300) | `PUT /api/geometry` on an unlocked active die that carries other configurations, edit reshapes the lamination | die `<base> <user> <n>` | No (a geometry edit) | No die minted: the context is released (the strip offers "Save as new die"), the live machine is kept, the report carries `blocked`, `released`, `code` |
| 3 | `_auto_transition_die` (~4800), reached from `sync_active_die_geometry` (~5240) | `PUT /api/geometry` changes a die identity key (Ø, segments, slots, poles) | die + copy of all documents, `derived_from.origin = "transition"` | No | Same as 2 |
| 4 | `save_as_new_die` (~5930, `derived_from` ~5965) | `POST /api/family/save_as_new_die` | die from live machine | Yes (strip button, sweep-apply dialog) | Unchanged |
| 5 | `duplicate_die` (~3250, `derived_from` ~3277) | `POST /die/{die}/duplicate` | copy of a die | Yes | Unchanged |
| 6 | `create_die` (~2664) | `POST /die` | empty die | Yes | Unchanged |
| 7 | `die_rename.py` | explicit rename | renamed die | Yes | Unchanged |

`_caller_die_name` (~4563) only names a die for an explicit request (duplicate, save as new die, create); it
creates nothing.

An explicit way to still mint a die from a geometry edit: `PUT /api/geometry` with `allow_new_die: true`
(body or query). The web client never sends it.

## Web client
Every call to a die-creating route (`FamilyCatalog.tsx`, `MotorsCatalog.tsx`, `ActiveFamilyStrip.tsx`
save-as-new-die, `SweepStudyPanel.tsx` "apply on a new die" after the user chooses it) is a user click. Nothing
relied on the implicit fork except the strip showing the "moved off die" note, which now never appears for a
duty save; the duty-save error text now shows a structured refusal's `message`.

`development/autolink.py` follows `derived_from` only through an explicit `copied_from`/die ancestry; it
needs no change (fewer derived dies, no new assumptions).

## Residual (owner decision)
`_transition_config_to_own_die` still COPIES a configuration into an EXISTING die that already is the live
lamination (cases 1-3 with a matching die, no die created). That is a configuration copy into another die,
not a die creation; it can also be refused if the owner prefers.

## Slot width of a configuration differs from its die: a warning (owner 2026-11-13)
Case 1 above saves a slot-width-only difference into the configuration. So that this is never silent,
`config_slot_mismatch` (`routes/family.py`) compares the slot width the die derives with the one the
configuration's wire stack derives (wire width / split / spacing, liner). A difference above
`SLOT_MISMATCH_WARN_MM` (0.005 mm, the micron rounding of a round-wire proxy stack) is reported, never refused:

- `POST /api/family/duty` answers `warnings: [{code: "slot_width_differs_from_die", die_slot_mm, config_slot_mm, message}]`;
- `GET /api/family/context` carries `slot_mismatch` (same object, `null` when the slot is the die's);
- the active-motor strip shows one line `slot 3.667 mm ≠ die 3.24 mm` with the message as a tooltip, and
  repeats it in the "saved to ..." line.

Tests: `tests/test_slot_mismatch_warning.py`.
