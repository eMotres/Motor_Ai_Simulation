# Propeller catalogue

One YAML per propeller: `config/propellers/<vendor>/<model>.yaml`. Read by
`src/motor_ai_sim/propeller.py` and served by `GET /api/propellers`. On the server
`<shared>/propellers/` is read as well and wins per `id` (same rule as the device
cards and the passport store). Files whose name starts with `_` are ignored.

Rules: never invent a number. A number that is not published carries
`estimated: true` plus the method. Electrical input power is never presented as
shaft power.

## Schema (`schema: propeller-1`)

```yaml
schema: propeller-1
id: tmotor_p12x4              # unique, used in the API and in cooling_options.yaml
vendor: T-MOTOR
model: P12*4
series: ...
status: current | legacy_label   # legacy_label = appears only as a test-table label
product_url: ...                 # null for legacy_label
source_urls: [...]
accessed: '2026-10-05'
geometry:
  diameter_in: 12.0
  diameter_mm: 304.8
  disc_diameter_mm: 267.0        # optional: the vendor's stated disc diameter; when present it is
                                 # the D used for C_T / C_P and for the slipstream disc area
  pitch_in: 4.0
  pitch_mm: 101.6
  blades: 2                      # null when the vendor does not state it
material: ...
mass_g | mass_single_blade_g | mass_package_g: ...
hub: {bore_mm, diameter_mm, center_thickness_mm, mount, ...}
published_limits: {optimum_rpm, recommended_rpm, thrust_limit_kg, max_rpm, ...}
performance:
  data_quality: torque_measured | thrust_measured_power_estimated | geometry_only
  static_hover_only: true
  test_density_kg_m3: 1.225      # density the bench numbers are reduced with
  power_estimate:                # only for thrust_measured_power_estimated
    estimated: true
    cp_ref: 0.0246
    rpm_exponent: 0.0
    rel_spread: 0.038
    method: ...
    basis: [...]
  tables:                        # one per published test table (one motor / voltage)
    - id: ...
      source_url: ...
      accessed: '2026-10-05'
      test_motor: MN2806 KV400
      prop_label_in_source: P12*4
      identity: exact | older_cf_label_not_pooled | assumed_same_geometry
      use_in_fit: true           # false = stored for reference, never fitted
      torque_resolution_Nm: 0.01 # last printed digit of the torque column (null: no torque column)
      columns_published: [...]
      note: ...
      rows:
        - {thr: 40, V: 23.59, I: 0.76, Pel: 17.85, rpm: 3080, thrust_g: 230, torque_Nm: 0.04}
        # thr = throttle %, V/I/Pel = electrical input as published, torque_Nm null when not published,
        # Pel_suspect: true marks a published electrical value that disagrees with V x I
notes: [...]
```

## How the numbers are used

- `C_T = T / (rho n^2 D^4)` from thrust and rpm, `C_P = 2 pi tau / (rho n^2 D^5)` (= P / (rho n^3 D^5), P = 2 pi n tau) from the
  published TORQUE, both reduced at `test_density_kg_m3`. Rows below 30 % of the highest tested
  rpm are skipped, and so are torque rows below `5 x torque_resolution_Nm`.
- Fit: `C(n) = C_ref (n / n_ref)^k`, Huber-weighted on log C; `k` is kept only if it is more than
  2.5 standard errors from zero. Outside the tested rpm range C is held at the edge value and the
  API says `extrapolated`.
- `Pel` (electrical power) is stored for the record and for the motor+ESC efficiency cross-check; it
  is never used as shaft power.
- `geometry_only` entries are listed but `selectable: false`: the model raises instead of guessing.

## T-Motor entries (access date 2026-10-05)

See `docs/PROPELLER_CATALOG_2026-10-05.md` for the data-quality table, the assumptions and what was
rejected. Sources are T-Motor's own store (`store.tmotor.com`, product and motor pages with test
tables) and `uav-en.tmotor.com` (FPV propeller pages).

## Server install (do not edit live files from a branch)

1. Copy `config/propellers/` to `<shared>/propellers/` (it overrides the repo copy per `id`).
2. Copy `config/cooling_options.yaml` to `<shared>/cooling_options.yaml` (overrides per die).
3. Restart the API (Python has no `--reload`); `GET /api/propellers` and
   `GET /api/propellers/cooling-options?die=CIANO14%2040%20new` confirm it.
