# Thermal cooling belongs to the motor

2026-10-15. The Thermal tab's cooling fields are remembered per motor
(`die|config`), not once per account.

## Why

After the 40 mm propeller motor (open frame, bore air 20 m/s) the 200 mm
liquid-cooled L155 was solved with the open frame and the 40 mm bore air: loading
a motor moved only `coolMode` and the propeller source, everything else stayed
from the previous motor.

## Store

`config/.panel_settings.json` -> `thermal` -> `<email>`:

```
settings      the last-used fields (kept; everything that read it still works)
motor         "<die>|<config>" the last-used fields belong to
source        saved | config | application
by_motor      "<die>|<config>" -> { settings, updated_at }   (the account's own record)
```

A Thermal panel save names the motor its fields belong to (`motor` in the
`PUT /api/panel_settings/thermal` body); `thermal_settings.apply_thermal_save` decides:

* a record for that motor is written only when a COOLING field changed against what the
  panel held for it (or the motor already has a record), so a view / iteration-limit /
  comparison-row edit keeps a default a default;
* the fields become the active (last-used) settings only when that motor is the loaded
  one, so a delayed save of motor A cannot replace motor B's settings;
* a save that names no motor (an old client) never writes a record and, once the entry
  knows its motor, changes only the account-level fields.

`maxIter`, the view and the comparison rows are the account's, not the motor's.

## Loading a motor (`PUT /api/me/last_motor`)

The motor is initialised when the entry does not already belong to it (a new account's
first load included) or its catalogue reference changed; an entry from before motors were
recorded is claimed unchanged when its own motor is loaded again. The cooling fields
(`thermal_settings.COOLING_KEYS`) are then built on the panel defaults
(`CANONICAL_COOLING`), never on the previous motor's fields, from, in priority order:

1. the account's own `by_motor` record for that motor (`saved`); a field it lacks is
   filled from 2 / 3;
2. the configuration's `thermal_defaults:` block (`config`), laid over 3;
3. the application's rule (`application`): robot -> robotics, aviation / marine ->
   liquid, propeller -> air from the propeller. The frame is RESET to `housed`
   (`open` only for a propeller configuration whose yaml has `frame: open`) and the
   bore to `none` (`still` for robotics); they are not inherited.

Loading the same motor again keeps manual edits.

### `thermal_defaults:` in `<die>/<config>.yaml`

Optional; the same string fields the panel uses (numbers are accepted):

```yaml
thermal_defaults:
  coolMode: liquid
  fluid: water
  tIn: 40
  flowLpm: 20
  frame: housed
  boreMode: none
```

Unknown keys are ignored with a log line. The propeller binding is not settable here
(only `airSpeedSource: manual`).

## Solves

`thermal_settings.motor_thermal_settings(authorization, die, config)` returns the
fields of the motor being SOLVED and `{source, motor, application, note}`:

* `POST /api/coupled/run` reads the motor named by `die` / `config` in the body, else
  the catalogue's family context (a duty is not required), else the account's last
  loaded motor; the controller's cooling inheritance uses the controller request's motor; the duty-cycle solve uses its duty's die / config.
  A body that sends its own `thermal_settings` is used as sent (`source: request`).
* No record for that motor: rule 2 or 3 above, never another motor's fields; the
  result's `cooling_source.note` says which.

The result carries `cooling_source`; the field result already carries `cooling.frame`
and `cooling.outer.mode`, which the Thermal panel compares with its fields (the
"solved with ..., fields now ... - re-Solve" warning).

## Browser recovery is a read

A tab that cannot establish which motor its cooling fields belong to (a failed, empty, sparse or
unbound settings read) asks `GET /api/me/last_motor/cooling` for the COMPLETE cooling of the
motor the account has loaded. It writes nothing and never changes the account's selection (the
`PUT /api/me/last_motor` would reselect a stale tab's motor). The answer is adopted only when the
account's selection equals the tab's (die, configuration and reference), the selection generation
has not moved and no newer follow was adopted; otherwise the tab is stale and its solves are
refused ("this tab shows ... - reload"). With no motor loaded, a successful read is adopted as it
is (an unbound record is returned complete, with the solver's meaning for absent fields).
