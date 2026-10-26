# Tests section: bench data against the simulation (2026-10-26)

Owner 2026-10-26: «да, делай раздел Tests; нужно сделать отдельные подменю Parameters, Free-run, Loads».

## Where

- Store: `<shared>/motor_tests/<die>/<config>/tests.json` + `audit.jsonl` (sibling of `<shared>/production`, same folder
  naming; `MOTRES_MOTOR_TESTS_ROOT` overrides). Renamed with the die (`die_rename.step_motor_tests`).
- Production: sub-tab **Tests** (Parameters | Free-run | Loads). The factory (production `oem`, NDA gate) adds tests;
  production owner / engineer and global admins edit and delete them and set the customer flag. Every change is audited
  (`GET /api/motor-tests/{die}/{config}/audit`).
- Development: a **Tests** button on the project variant, read-only. Engineers and the project owner always; customers only
  when `tests_visible_to_customer` is on (default off).
- Report (docx / pdf): "Tests vs simulation", one table per test, only when the configuration has tests and the viewer may
  see them.
- Documents: a test names production documents by id; no new file store.

## Conversions (`src/motor_ai_sim/motor_tests/compare.py`)

| quantity | model value |
|---|---|
| R line-line | R_phase (stored, at its coil temperature) x (234.5 + T_meas) / (234.5 + T_coil), x2 star / x2/3 delta, + 2 leads (rho_Cu20 L / A, AWG or mm²). The phase board and joints are not modelled. |
| L with rotor | 3-pair mean = Ld + Lq, amplitude sqrt(2/3 Σdev²) = abs(Lq − Ld); model = incremental Ld/Lq of the lowest-current duty (star equivalent in delta). Frequency above 100 Hz is flagged. |
| L without rotor | a stored stator-only duty (name "stator only" / "no rotor"), else "no model point". |
| EMF | no-load duty V_LL peak / rms x n / n0 x Br(T_test) / Br(T_model); Br from the grade's cards (20/100/150 C ...), linear between cards. Measured peak = Vpp / 2. |
| no-load loss | core (hyst ~n, eddy ~n², excess ~n^1.5; P_core ~n^1.5 without the split) + P_solid ~n² (P_solid already holds P_mag), x (Br ratio)², + bearings (SKF, when set) + windage. Remainder = "unexplained". Fit P = a n + b n² (krpm) for both. |
| loads | the duty within 2 % speed and 3 % current; else "no model point" and (admin) one click adds the duty via `POST /api/family/duty`. |

Check numbers (200 mm stator, 2026-10-18 / 10-26): R model 16.62 + 0.67 = 17.29 vs 18.03 mOhm (−4.1 %); fitted
Ld 42.8 uH, Lq 61.6 uH from 113.1 / 85.6 / 114.4 uH. Tests: `tests/test_motor_tests_compare.py`,
`tests/test_motor_tests_routes.py`, `web/src/modules/tests/__tests__/tableModel.test.mjs`.

The bench data of the closed motor itself is in the private repository (`docs/tests_seed/`), not here.
