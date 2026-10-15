# Configuration-level visibility

Owner decision 2026-10-15: *the die stays ONE for all; we hide the configuration.*
A single configuration of a public die can be private (first use: CIANO28 85 20SW1200 / L13,
whose 3-D model becomes a frameless robot kit; an identical public L15 is created separately).

## The rule

`visibility: private` as a top-level line of the configuration yaml (absent or anything else = public).

```
may_see_config(access, die, cfg) = may_see_die(access, die)
                                   AND ( admin
                                         OR the configuration is public
                                         OR "<die>/<cfg>" is in the account's CONFIG grants )
```

* Per-user CONFIG grants are `motors.configs = ["<die>/<config>"]` in `users.json`, next to the die grants.
  An `all` grant, a die grant or a public die does **not** open a private configuration.
* Admins always see everything. The anonymous exhibit never sees a private configuration.
* A hidden configuration is **indistinguishable from a non-existent one**: 404 (never 403) with the
  text the route gives a missing configuration, not listed, not counted, not named in any list.
* The caller's own workspace copy of a die (layering on) is theirs and is not hidden.
* One helper, `motor_access.may_see_config`; low-level readers use `motor_access.hidden_for_request`
  (`family.config_doc`, `duty_results.get`); outside a request (CLI, nightly scripts) nothing is hidden.
* An unreadable configuration file counts as private (fail closed).

## Where it is enforced

| Surface | How |
|---|---|
| `GET /api/family/tree` (rows, `config_count`, `cards`), MCP `list_machines`, `check_fit`, `get_machine_performance`, `list_catalog(dies)`, `get_catalog_entry(dies)`, `start_design` candidates | the tree omits the configuration (the memo key carries admin-ness and the config grants) |
| every `/api/family/...` route with `{die}/{cfg}` in the path or `?config=` in the query | `config_path_gate` (a dependency on the whole app, GET/HEAD) + `_require_die_access(die, auth, cfg)` in the route |
| every family write by die+config (rename, duplicate, delete, lock, default duty, battery, controller, bearings, duty create/rename/delete/duplicate, activate, reattach, duty_result, duty_run, history restore) | `_require_die_write(die, who, cfg)` |
| `GET /api/family/die/{die}/part_materials`, `GET /api/family/history/{die}`, `POST /history/restore` | in-route: the answer a missing configuration gets |
| `GET /api/family/context`, `GET /api/me/last_motor`, `GET /api/me` (`defaultMotor`) | a hidden configuration reads as deleted / null |
| copy-on-write, die duplicate, community listing, publish | the hidden file (and its `runs/`) is never forked or copied to a caller who cannot see it |
| datasheet / report downloads | `/api/family/datasheet/{die}/{cfg}`, `/api/family/report/{die}/{cfg}` |
| solve routes (thermal, duty cycle, coupled, mechanical, controller) that read a configuration by name | they go through `family.config_doc` / `duty_results.get`, which answer "missing" for a hidden one |
| Fusion / FreeCAD parameter export | works on the machine loaded in the caller's own workspace (no die + configuration parameter); a hidden configuration cannot be loaded into it (payload, activate, card load and preset apply are gated) |
| `/api/controller/*`, `/api/bearings/losses`, `/api/system/graph`, `/api/propellers/cooling-options`, `/api/propellers/{id}/s1` | in-route, with that route's own "missing" answer (empty settings, `has_bearings:false`, status unknown ...) |
| `/api/model3d/models`, `/api/model3d/{die}/{cfg}/model.glb`, `model.step` | listing and files |
| catalog cards `/api/catalog`, `/references`, `/{id}/passport`, `/{id}/configure_context` (also its sibling-configuration presets), `/{id}/load`, `/{id}/duplicate` | cards are tied to a configuration by the name `"<die> <config>"` |
| presets `/api/presets`, `/{id}`, `/{id}/apply`, `/{id}/open`, `/api/static3d/*?preset=` | the presets of a hidden card (by card link or name) |
| `/api/catalog/cards/{kind}/used_by` | machines the caller may not see are not named |
| `/api/components`, `/api/components/{id}`, `POST /api/kernel/run` (`component.library`) | motor, controller and pack cards follow their machine's die **and configuration** |
| open-data publishing (`data_sources.move_blockers`) | a die with a private configuration cannot go to the public repository |
| prerendered landing, `llms.txt`, `.well-known/mcp.json`, sitemap | audited: static, no catalog content |
| assistant / tickets | the snapshot is what the browser already holds; nothing is read from the catalog |

`tests/test_config_visibility.py` carries a sweep over every route that names a die + configuration and fails when a new
one is added without being covered.

## Setting a configuration private

Admin UI: **Admin → Motors access →** click the die row (for L13: *CIANO28 85 20SW1200*) → in the drawer, under
**Configurations**, switch **Private** on for **L13** (takes effect at once; the die is untouched).
Letting an account open it: **Admin → Users →** the user → **Motors →** under **Private configurations** tick
*CIANO28 85 20SW1200 / L13* → **Save**.

API: `PUT /api/admin/dies/{die}/configs/{cfg}/access` `{"visibility": "private"}`;
`PUT /api/admin/users/{email}/motors` with `"configs": ["<die>/<config>"]` (key absent = keep, `[]` = clear).

On the server, without the UI (one line; no restart needed, the file's mtime is the cache key):

```
f="$(find /srv/motres -path '*/dies/CIANO28 85 20SW1200/L13.yaml' -not -path '*/workspaces/*' -not -path '*/published/*' | head -1)"; test -n "$f" && { grep -q '^visibility:' "$f" || sed -i '1i visibility: private' "$f"; }; grep -n '^visibility' "$f"
```

Reverse: delete that line (or switch the toggle off).

## Known edges

* A user who already **forked** the die into their own workspace (copy-on-write from an earlier edit) keeps their own copy of
  the configuration: it is theirs. Forks of the die can be listed with
  `find /srv/motres/workspaces -path '*/dies/CIANO28 85 20SW1200/L13.yaml'`.
* A saved MCP design or a "My motors" snapshot made earlier from the configuration belongs to its owner and stays theirs.
* Order of checks for a write with a malformed body: the hidden configuration answers 404 before the body is validated.
