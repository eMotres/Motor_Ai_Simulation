# Configuration-level access

Owner decision 2026-10-15: *the die stays ONE for all; we hide the configuration.*
Extended 2026-10-16: *restrict access on BOTH levels - the die and each configuration.* Concrete case:
die *CIANO28 85 20SW1200* stays **Private**, *L15* is open to every signed-in account, *L13* stays hidden.

## The rule

Each configuration has its own status, stored as one top-level line of its yaml:

| Status | yaml | Who sees the configuration |
|---|---|---|
| **As motor** (`inherit`, default) | no `visibility:` line | exactly who sees the die itself (admin, `all`, die grant, die public, die selected clients) |
| **Private** | `visibility: private` | admins + accounts holding the `"<die>/<cfg>"` grant |
| **Public** | `visibility: public` | every signed-in account, even when the die is Private |
| **Selected clients** | `visibility: private` + >= 1 account holding the grant | those accounts (the grants ARE the client list - one store) |

```
die_open(access, die)          = admin / all-mode / die grant / die public / die selected incl. the caller
config_visible(access, die, c) = admin
                                 OR "<die>/<c>" is in the account's CONFIG grants
                                 OR status public
                                 OR (status inherit AND die_open(access, die))
may_see_die(access, die)       = die_open(access, die) OR the caller can see >= 1 configuration of the die
```

* A die opened **only through its configurations** shows only those configurations everywhere (tree, catalog cards,
  Configure, MCP, 3-D models, payload / datasheet routes): an *As motor* sibling of such a die stays hidden.
* Per-user CONFIG grants are `motors.configs = ["<die>/<config>"]` in `users.json`, next to the die grants; they are
  edited from Admin · Motors access (Selected clients) and from Admin · Users · Motors. An `all` grant, a die grant or a
  public die does **not** open a private configuration.
* Admins always see everything. The anonymous exhibit never sees a private configuration; public and *As motor*
  configurations behave for it as before.
* A hidden configuration is **indistinguishable from a non-existent one**: 404 (never 403) with the
  text the route gives a missing configuration, not listed, not counted, not named in any list.
* The caller's own workspace copy of a die (layering on) is theirs and is not hidden. In the published (community)
  layer, which every registered account may read, only `private` hides a configuration.
* One rule, `motor_access.config_visible`; `may_see_config`, `config_allowed`, `may_see_config_file`,
  `drop_private_config_cards` and the low-level `hidden_for_request` (`family.config_doc`, `duty_results.get`) all go
  through it; outside a request (CLI, nightly scripts) nothing is hidden.
* An unreadable configuration file counts as private (fail closed); so does a `visibility: private` line right after a
  UTF-8 BOM. An unknown value reads as *As motor*, never as more open than the die.

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

## Setting a configuration's access

Admin UI: **Admin → Motors access →** expand the die row (arrow before the name) → click the configuration row
(for L15: *CIANO28 85 20SW1200 / L15*) → **As motor / Private / Public / Selected clients** (+ the e-mails) → **Save**.
The die row's chip reads e.g. *Private · 1 open* when the die is private but a configuration is open (tooltip: which).
Letting one account open a private configuration also works from **Admin → Users →** the user → **Motors →**
under **Private configurations**.

API: `PUT /api/admin/dies/{die}/configs/{cfg}/access` with `{"status": "inherit" | "private" | "public" | "selected",
"clients": [...]}`. `selected` needs >= 1 registered e-mail (422 otherwise), writes `visibility: private` and makes
exactly those accounts hold the grant (added to them, removed from every other account); `private` removes the grant
from every account; `inherit` / `public` leave the grants as they are. The older body `{"visibility": "private" |
"public"}` is still accepted (its `public` = no line = `inherit`). `GET /api/admin/dies` rows carry
`config_access: [{name, status, clients}]`.

On the server, without the UI (one line; no restart needed, the file's mtime is the cache key):

```
f="$(find /srv/motres -path '*/dies/CIANO28 85 20SW1200/L15.yaml' -not -path '*/workspaces/*' -not -path '*/published/*' | head -1)"; test -n "$f" && { sed -i '/^visibility:/d' "$f"; sed -i '1i visibility: public' "$f"; }; grep -n '^visibility' "$f"
```

(`visibility: private` for Private; delete the line for *As motor*.)

## Known edges

* A user who already **forked** the die into their own workspace (copy-on-write from an earlier edit) keeps their own copy of
  the configuration: it is theirs. Forks of the die can be listed with
  `find /srv/motres/workspaces -path '*/dies/CIANO28 85 20SW1200/L13.yaml'`.
* A saved MCP design or a "My motors" snapshot made earlier from the configuration belongs to its owner and stays theirs.
* Order of checks for a write with a malformed body: the hidden configuration answers 404 before the body is validated.
