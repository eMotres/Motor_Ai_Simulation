# Motor (die) names and the safe rename — 2026-10-26

Owner decision 2026-10-26: motor names are short and readable, "normal names, just add the user's suffix".

## 1. The naming rule

A new variant is called `<family base> <user suffix> <n>`:

| source | first variant | next ones |
|---|---|---|
| `CIANO56 400_80 Ø400 48s56p opt3` | `CIANO56 400_80 vadim` | `… vadim 2`, `… vadim 3` |
| `CIANO14 50 edited` | `CIANO14 50 vadim` | `CIANO14 50 vadim 2` |

* **Family base** (`die_names.family_base`, twin `web/src/lib/dieNameRule.ts`): the leading family token
  (letters with an optional trailing number: `CIANO56`, `CIANO`, `CILN28`, `M1`) plus ONE size token that follows
  it (a number or numbers joined by `_ - . x`: `400_80`, `85`, `150_35`). Everything after it — `Ø400`, `48s56p`,
  `opt3`, `edited`, `new`, `20RSW175`, an earlier user suffix and its number — is dropped. A legacy name that starts
  with a size and a unit keeps both (`100 mm · 24s-28p mid-torque` -> `100 mm`).
* **User suffix** (`die_names.user_suffix`): the first name of the account's profile, lower-case; else the local
  part of the e-mail address; ASCII only (Cyrillic is transliterated, accents dropped), at most 16 characters.
* **n**: the first free number; the first variant has none.
* Existing names are never renamed automatically. The parameters (diameter, slots/poles) are visible next to the name.

Every creator uses the one helper: `POST /api/family/die` (new die from the live geometry),
`POST /api/family/die/{die}/duplicate`, `POST /api/family/save_as_new_die`, and the automatic die transition
(`_die_transition_name`). The name in the request is optional; without it the server proposes the rule's name. The
web dialogs open pre-filled with `GET /api/family/die_name?source=<die>` (editable).

## 2. Safe rename — `PATCH /api/family/die/{die}`

A rename is ONE transaction (`die_rename.py`): when a step fails, the steps already done are undone and the answer
is HTTP 500 "rolled back". `?dry_run=1` changes nothing and lists every place the rename would touch.

| store | what follows the rename |
|---|---|
| catalog | the folder, `die.yaml` name, every configuration's `die`, `derived_from.die` of other dies, `.history/<die>/` |
| `duty_results.json` | the results and the per-kind history (`history`) of the die; the legacy per-die ledger inside the folder |
| duty fields | the `die` in the meta of every stored `.npz` field file (the folder travels with the die) |
| active context | `.family_context.json` (`die`, `released_from`, `transitioned_from.die`) |
| workspace memory | the per-motor Thermal fields (`.panel_settings.json` `by_motor`), the source link of "My motors" copies |
| catalog cards / presets | cards and presets named after the die (`<die>` or `<die> <config>`); ids unchanged |
| accounts (`users.json`) | `motors.dies`, `motors.configs` (`<die>/<config>`), `motors.default.die` of every account |
| `die_access.json` | the public / selected-clients entry |
| development projects | `linked_motors`, `motor_history`, `project_motor_grants` (key and `grant_key`), `grant_removal_candidates`, the motor index (`index.json`), the access scope of the motor snapshots; a `motor.rename` audit line; the audit fallback of the Restore list follows it |
| production | the `production/<die>/` folder, the `die` field of every record, an audit line per production |
| CAD | `<cad root>/<die>/` and its manifests, the rebuild requests |
| admin audit | one `die.rename` entry |

Scope: the stores of the caller's workspace always follow. The shared catalog's stores (accounts, access list,
projects, production, CAD) follow only when the SHARED die is renamed — a private copy in a workspace does not
retarget what everybody else's grants and projects mean by that name.

Not rewritten, by design: append-only audit logs, the frozen content of project snapshots, run payload sidecars
(opaque) and the live last-solve caches.

Reported, not rewritten (`manual` in the answer): deploy-managed files that still name the die — the repository /
shared `cooling_options.yaml`, the passport folders, the propeller S1 model folders. Move those with the deploy.

Refused (HTTP 409): a rename onto an existing die, of the shared baseline motor every account is granted
(`users._BASELINE_DIE`), of a die in an unfinished open/private data move, or onto an existing production / CAD folder.

Tests: `tests/test_die_rename.py`, `tests/test_die_names.py`, `web/src/lib/__tests__/dieNameRule.test.mjs`.
