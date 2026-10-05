# Propeller catalogue, propeller load and slipstream cooling (2026-10-05)

Owner request (2026-10-05): the Ø40 motors (die "CIANO14 40 new", L12 6S and L20 12S) are drone
motors, air-cooled only, with the propeller's own slipstream as the only airflow. So the cooling air
speed must be computed from rpm and the chosen propeller, the load must come from the propeller, and
the catalogue needs T-Motor propellers of 10, 11, 12 and 13 in.

Code: `src/motor_ai_sim/propeller.py`, `cooling_options.py`, `routes/propellers.py`; data:
`config/propellers/tmotor/*.yaml` (schema in `config/propellers/README.md`),
`config/cooling_options.yaml`; tests: `tests/test_propeller_catalog.py`.

## What T-Motor publishes (access date 2026-10-05)

Sources: `store.tmotor.com` product pages (geometry, mass, limits), the test tables on
`store.tmotor.com` motor pages (MN2806, MN3110, MN3508, MN3510, MN4004, MN4006, MN4010, U3) and the FPV
propeller pages on `uav-en.tmotor.com`. The store search is disallowed by robots.txt and was not used.

| entry | D x P (in) | blades | data in the catalogue |
|---|---|---|---|
| P12*4 (Polish CF) | 12 x 4 | 2 | **torque** (MN2806, 2 motors) |
| P13*4.4 (Polish CF) | 13 x 4.4 | 2 | **torque** (MN2806, 2 motors) |
| FPV 10*5 | 10 x 5 (disc 267 mm) | 3 | **torque** (3115/3120, 5 motors-voltages) |
| FPV 13*10 | 13 x 10 | 3 | **torque** (4215 KV320) |
| FPV 13*12 | 13 x 12 | 3 | **torque** (4220 KV350) |
| CF 10*3.3 (test-table label) | 10 x 3.3 | 2 | thrust measured, **electrical power only** -> C_P estimated |
| CF 11*3.7 (test-table label) | 11 x 3.7 | 2 | thrust measured, **electrical power only** -> C_P estimated |
| MS1101 (polymer) | 11 x 4.2 | 2 | geometry only |
| MS1302 (polymer) | 13 x 5 | 2 | geometry only |
| MF1302 (folding polymer) | 13.4 x 4.8 | n/s | geometry only |
| T12*6 Black (fixed wing) | 12 x 6 | n/s | geometry only |
| T13*6.5 Black (fixed wing) | 13 x 6.5 | n/s | geometry only |

There is no 10 in propeller in T-Motor's multirotor store: the 10 in entries are the FPV 10*5 and the
older "10*3.3CF" that exists only as a test-table label. The 11 in size is likewise only MS1101 (no
data) and the "11*3.7CF" label. The AT-series motor tables use APC propellers and were not used.

## Data-quality findings (why some choices were made)

1. **"Power (W)" in T-Motor tables is electrical input (motor + ESC), not shaft power.** Shaft power is
   taken only from published torque (tau * omega). On the torque tables it is 61-78 % of the electrical
   column, which is the overall motor + ESC + rig efficiency; the resulting figure of merit
   (C_T^1.5 / (sqrt(2) C_P) = 0.56-0.64) is physically sensible, which supports trusting the torque.
2. **Where only electrical power exists, shaft power is not guessed from it.** For CF 10*3.3 and
   11*3.7, C_T is measured from thrust, and C_P is an explicit estimate: the older CF series 13*4.4 ..
   17*5.8 (torque published on MN4004/MN4006, all with pitch/diameter about 0.33) have C_P medians within
   +-3.8 % of 0.0246, so that value is transferred. Flagged `estimated: true`, uncertainty set to 15 %.
3. **"12*4CF" / "13*4.4CF" (older CF labels) are not the Polish P12*4 / P13*4.4.** On the same size
   their C_T is 8-10 % lower and their C_P about 8 % higher, so they are not pooled. The MN4004/MN4006
   "13*4.4 CF" torque rows are stored in the P13*4.4 file as reference (`use_in_fit: false`).
4. **Source errors found and kept visible**: three electrical power values in the FPV 10*5 / 3115 900KV
   table (14.2, 16.4, 18.6 W at 60/70/80 % throttle with 24 V x 24-50 A) are flagged `Pel_suspect` and
   never used; one MN4006 100 % row (13*4.4 CF) shows +27 % thrust for +3 % rpm against the row before it, so its rpm
   is probably mis-recorded (reference tables only, not fitted). The fit is Huber-weighted and rejects points beyond 3.5 robust sigma.
5. Torque is printed to 0.01 N*m; rows below 0.05 N*m are not used for C_P. Rows below 30 % of the
   highest rpm are skipped (bench noise at 10 % throttle).
6. The tests were run at unstated ambient conditions (one FPV table says 25 degC). Coefficients are
   reduced at ISA sea level (1.225 kg/m3); a +-3 % density uncertainty carries straight into C_T, C_P.
7. Tested rpm ranges are narrow for the P-series (P12*4 up to 6450 rpm, P13*4.4 up to 6060 rpm). A
   Ø40 motor can spin them faster; the coefficient is then held at its edge value and every answer
   says `extrapolated: true`.

## Model

Static (hover) coefficients `T = C_T rho n^2 D^4`, `P = C_P rho n^3 D^5`, `tau = P / (2 pi n)`, with
`C(n) = C_ref (n/n_ref)^k` fitted per prop (k kept only if > 2.5 standard errors). Fit quality on the
torque props: C_T rms 0.2-1.2 %, C_P rms 0.8-4.3 % (P13*4.4's C_P exponent sits at the -0.1 bound: torque
quantisation limits what the slope can say, and the whole trend is 6 % over the tested range).

**Cooling air.** Developed slipstream `v_wake = sqrt(2 T / (rho A))` (momentum theory, A = pi D^2 / 4).
The air at the motor is `factor x v_wake`; the factor is an **engineering assumption to be calibrated**:
`developed_wake` 1.0, `disc_plane` 0.5 (induced velocity at the disc), `behind_hub` 0.4 (default: disc
plane x 0.8 hub blockage). Calibrate against a hover bench run with a thermocouple on the winding.
Sensitivity (Ø45 housing, 25 degC, P12*4 at 6000 rpm: v_wake 14 m/s): factor 0.2 / 0.4 / 0.6 gives
h = 28 / 41 / 51 W/m2K, so a factor error of -50 % / +50 % is about -30 % / +25 % in h. The film itself
is the existing air mode (`cooling_models.outer_air`, Churchill-Bernstein cross-flow); an axial wash
over a motor body is not the cross-flow it was built for, which is a second, untested assumption.

**Operating point.** `rpm_for_torque` inverts the propeller torque; `equilibrium_rpm` takes the
motor's torque-speed capability (arrays from the passport) and returns the highest rpm where the motor
still has the propeller's torque, with `limited_by` = `torque_equilibrium` | `motor_speed_limit` |
`no_motor_torque`.

**Thermal hook.** `solve_thermal_field(..., air_speed_source="propeller", propeller_id=..,
propeller_position=..)` (cooling_mode `air` only) replaces `air_speed_mps` by the slipstream speed at
the call's own `rpm` and `ambient_temp`; the answer carries `cooling_air_source`. The default
(`manual`) returns the typed speed untouched, so every other mode is bit-identical (pinned by
`test_thermal_default_source_is_the_identity`). The `/api/thermal/field` route wrapper was **not**
changed (its history key and restore dict would need the new fields); the UI step adds them. The
Configure tab's analytical estimate lives in the browser (`web/src/lib/thermalEstimate.ts`), so
`GET /api/propellers/{id}/point?rpm=..&housing_d_mm=..` serves it the air speed and the film
coefficient from the same backend functions.

## Per-motor cooling options

`config/cooling_options.yaml`: die "CIANO14 40 new" -> `cooling_options: [propeller_air]` and the allowed
propeller ids (the selectable 10-13 in entries). A die with no entry is unrestricted. The die's
`die.yaml` lives on the server and was not touched. Server install: copy `config/propellers/` to
`<shared>/propellers/` and `config/cooling_options.yaml` to `<shared>/cooling_options.yaml`, restart the
API, check `GET /api/propellers/cooling-options?die=CIANO14 40 new`.

## What the UI step needs

- List: `GET /api/propellers` (`selectable`, `power_data`, `data_quality`, `rpm_range_tested`) and
  `GET /api/propellers/cooling-options?die=&config=`: show only "Air - propeller" and the allowed props
  for the Ø40 family; badge `power_data: estimated`; grey out `selectable: false`.
- Per rpm: `GET /api/propellers/{id}/point?rpm=&temp_c=&housing_d_mm=` -> `air_speed_ms`, `film`,
  `torque_Nm`, `shaft_power_W`, `extrapolated`.
- The motor torque-speed array for `equilibrium_rpm` has to be built from the passport (it carries
  rpm / current points per variant, not a torque curve): derive torque = Kt-based or from the card's
  scaled torque, then call `propeller.equilibrium_rpm` (a thin route is trivial to add once the array
  source is chosen).
- Pass `air_speed_source`, `propeller_id`, `propeller_position` through `/api/thermal/field`, `/coupled`
  and the duty-cycle route.

## Configure UI (2026-10-05, branch feat/configure-propeller)

- `GET /api/propellers/{id}/series?rpm_max=&n=&temp_c=&housing_d_mm=`: the `/point` arithmetic on an rpm
  grid (thrust, torque, shaft power, cooling air, `extrapolated` per sample, housing film h per sample from
  `cooling_models.outer_air`). Configure asks once per (propeller, ambient, housing) and interpolates, so the
  load, the cooling and the knob zones come from the same backend functions.
- `GET /api/catalog/{id}/configure_context` carries `cooling` (die, config, `cooling_options`, allowed
  propellers) and `thermal_limits` (winding: class H 180 degC, a stated default; magnet: the assigned magnet
  card's `max_working_temp_c`, `null` when it has none, then the web falls back to 150 degC and says so).
- The motor torque -> current inversion is done in the browser with the passport (`scaleMotor`), by bisection;
  `equilibrium_rpm` is not used (the knob IS the rpm).
- The housing is treated as at most as hot as the winding limit: above it the housing tile reads `> 180`.
- PWM extra loss heats the housing but is not put on the winding or the magnet hot-spot (its location is unknown).
- Server install is unchanged: copy `config/propellers/` and `config/cooling_options.yaml` into `<shared>/`.
