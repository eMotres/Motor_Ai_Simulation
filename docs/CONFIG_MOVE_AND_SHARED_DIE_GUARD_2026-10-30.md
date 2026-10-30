# Shared-die guard and configuration move — 2026-10-30

## 1. An edit on a shared die moves the active configuration to its own die

On 2026-10-29 (07:03–10:09 UTC) the owner edited his live L100 on the unlocked die `CIANO56 400_80 vadim 3`
(magnet_height 18.8 → 23, slot_height 19.3 → 22, magnet_down 2 → 2.5, stator_inner_radius 174.2 → 171.5).
`sync_active_die_geometry()` wrote every save into `die.yaml`, so the die's five other configurations started building
the new lamination under results computed on the old one.

Now (`routes/family.py`):

| die after a geometry save | what happens |
|---|---|
| locked | nothing (as before) |
| unlocked, only the active configuration | the snapshot adopts the edit (as before) |
| unlocked, other configurations too, and `die_shape_diffs` is not empty | **the snapshot is not written**; the active configuration moves to its own die |

`die_shape_diffs(die_geo, live_geo)` lists every die-level INPUT key that differs (not a configuration key, not a
derived copy, not an unused knob), plus the DERIVED slot width (wire column + 2 spacings + 2 liner): a configuration
whose slot is wider than its die's (7.74 on a die stamped 7.6) is not on that die, and the duty save refuses it with
409 "build differs". The slot-width case also moves on a plain load of such a configuration.

The move (`_transition_config_to_own_die`): reuse a die of the caller's write layer (shared for a verified admin, the
workspace otherwise) that already is the live lamination on every die key; else mint `<family base> <user> <n>`
(`CIANO56 400_80 vadim 6`) with the live geometry and `derived_from` provenance. Only the active configuration is
copied into it (duties, runs). The source die and all its configurations are not written. The context records
`transitioned_from = {die, diffs, reason: "shared_die", others: [...]}`; the strip shows "↷ moved to its own die" and
the tooltip names the other configurations.

## 2. Move / copy a configuration to another die — `POST /api/family/config/{die}/{cfg}/move`

Admin only (verified admin with layering). Body `{"target_die": "...", "mode": "move" | "copy", "dry_run": false}`;
`?dry_run=1` works too. A worker route (`roles.WORKER_PATTERNS`, `deploy/nginx.conf`): it writes the shared catalog.
UI: the catalog's configuration row, "⇄ move" / "⇉ copy to die" (admins): asks for the target, shows the dry run,
then applies.

One transaction (`motor_ai_sim/config_move.py`, the shape of `die_rename.py`). Every rewritten file is first copied to
`<catalog root>/.config_moves/<stamp> <die>~<cfg>/files/`; a move's source files go to `.../source/` last instead of
being deleted; `manifest.json` lists every change and backup. `users.json` is private and never copied there. A failed
step undoes the earlier ones (HTTP 500 "rolled back").

| store | move | copy |
|---|---|---|
| catalog: `<cfg>.yaml` (`die`), `runs/<cfg>/`, `.npz` meta `die`, `.history/<die>/<cfg>.*` | moved | copied |
| `.duty_results.json` `results` + `history`: shared, every workspace, legacy per-die ledger | re-keyed | copied |
| `.family_context.json` of every workspace (`die`, `released_from`) | re-keyed | — |
| `.panel_settings.json` (`<die>\|<cfg>`), `user_motors.json` (`src`) | re-keyed | — |
| catalog cards / presets (`die`, name `<die> <cfg>`) | re-keyed | — |
| `users.json`: `motors.configs`, `motors.sources`, `motors.default` | re-keyed | — |
| access through the old die (die grant / selected clients) | kept as a `<target>/<cfg>` grant | — |
| projects: `linked_motors`, `motor_history`, `grant_removal_candidates`, `project_motor_grants`, snapshots' name/scope, `index.json`; audit `motor.move` | re-keyed | joins via `development.autolink` |
| production, motor_tests, CAD folders, CAD rebuild request | moved | listed under `manual` |

Scope: the shared stores (accounts, projects, production, tests, CAD, other workspaces) follow only for a die of the
shared catalog (always with layering off). Refused with 409: the target has a configuration of that name (or runs,
results, folders under it), the dies are in different layers, the configuration holds a product revision (move; a copy
is allowed), a data move of either die is open, a job runs on the loaded configuration.

Not rewritten: append-only audit logs, frozen snapshot content, job records, stored Configure answers (stamps change,
recomputed), `derived_from` of other dies. `manual`: `cooling_options.yaml` entries of the source die, passport and
propeller-S1 folders of the configuration.

### Dry-run answer

```json
{"ok": true, "mode": "move", "dry_run": true, "scope": "shared catalog", "backup": null,
 "from": {"die": "CIANO56 400_80 vadim 3", "config": "L100"},
 "to":   {"die": "CIANO56 400_80 vadim 4", "config": "L100"},
 "changes": [
  {"store": "catalog", "where": ".../dies/CIANO56 400_80 vadim 3/L100.yaml", "change": "configuration moved to 'CIANO56 400_80 vadim 4' (die back-reference -> 'CIANO56 400_80 vadim 4')"},
  {"store": "duty_results", "where": ".../shared/.duty_results.json", "change": "results + per-kind history re-keyed", "exists": true, "rows": {"results": 12, "history": 4}},
  {"store": "user_grants", "where": "client@example.com", "change": "configuration grant CIANO56 400_80 vadim 3/L100 -> CIANO56 400_80 vadim 4/L100"},
  {"store": "projects", "where": "erium/project.json", "change": "linked motor / history / grant ledger rows (4)"},
  {"store": "catalog", "where": ".../L100.yaml", "change": "source configuration removed from 'CIANO56 400_80 vadim 3' (kept in the backup folder)"}],
 "manual": [{"store": "cooling_options", "where": ".../cooling_options.yaml", "change": "..."}],
 "build_check": {"target_vs_source": [], "config_vs_target": [], "stale_duties": {}}}
```

`build_check`: `target_vs_source` = die-level differences between the two laminations; `config_vs_target` = where the
configuration is not the target die (non-empty: duty saves there answer 409); `stale_duties` = duties whose saved
build differs from the target build.

Tests: `tests/test_die_shared_snapshot_guard.py`, `tests/test_config_move.py`.
