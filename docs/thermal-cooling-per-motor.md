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

A Thermal panel save writes `by_motor` for the motor the panel names (`motor` in the
`PUT /api/panel_settings/thermal` body) or, without it, the account's loaded motor.
A save never touches another motor's record.

## Loading a motor (`PUT /api/me/last_motor`)

When the motor changed, the cooling fields (`thermal_settings.COOLING_KEYS`) become,
in priority order:

1. the account's own `by_motor` record for that motor (`saved`);
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
  the catalogue's loaded motor; the duty-cycle solve uses its duty's die / config.
  A body that sends its own `thermal_settings` is used as sent (`source: request`).
* No record for that motor: rule 2 or 3 above, never another motor's fields; the
  result's `cooling_source.note` says which.

The result carries `cooling_source`; the field result already carries `cooling.frame`
and `cooling.outer.mode`, which the Thermal panel compares with its fields (the
"solved with ..., fields now ... - re-Solve" warning).
