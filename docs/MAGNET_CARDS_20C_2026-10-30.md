# One 20 °C magnet card per grade (2026-10-30)

Owner 2026-10-30: «нужно сделать в каталоге магниты все с одинаковой температурой 20 °C, а в моделировании
просто вводить нужную температуру, но не больше той, для которой они предназначены».

## Cards: old → new

| former card (still resolves) | new card | temperature it stands for | grade limit |
|---|---|---|---|
| N52UH_20C, N52UH_100C, N52UH_150C (+ Arnold_N52UH_100C/150C) | **N52UH** | 20 / 100 / 150 °C | 180 °C (UH) |
| N45EH_150C, N45EH_180C | **N45EH** | 150 / 180 °C | 200 °C (EH) |
| F45SH_120C | **F45SH** | 120 °C | 150 °C (SH) |
| F52SH_30C, F52SH_80C, F52SH_120C | **F52SH** | 30 / 80 / 120 °C | 150 °C (SH) |
| Fe16N2_optimistic, Fe16N2_lab_best | unchanged (already 20 °C, no grade class) | — | none |

Each new card holds the 20 °C sheet values (Br, mu_rec, sigma, density, alpha(Br), beta(HcJ) referred to 20 °C,
`max_working_temp_c`) and a `demag_model` that GENERATES the 2nd-quadrant curve at any temperature
(`materials.MagnetMaterial._derived_at`):

* `knee_quadratic` (N52UH, F52SH, F45SH): linear recoil (mu_rec 1.05) to the knee at 0.97·HcJ, quadratic collapse to
  J = 0 at HcJ, one point at 1.03·HcJ (J = −Br/2). This is exactly the generator behind the 2026-09-29 cards.
* `tanh_knee` (N45EH, no maker sheet): J = J_recoil·½(1 − tanh((|H|/HcJ − 0.941)/0.04)), truncated at its B = 0
  crossing (the solver's knee). The shape was fitted to the two hand-made cards it replaces (they share one
  J/J_recoil(|H|/HcJ) curve).

Per-temperature data used as a check: the pre-2026-09-29 Ansys N52UH pair (Br 1.30/1.22 T, knees −1155/−730 kA/m
at 100/150 °C) implies alpha −0.112 and beta −0.46 %/K at 20 °C — within 10 % of the Arnold sheet's −0.12/−0.51,
which are kept. The old F45SH_120C (Br 1.19 T) sits 0.2 % above the derived 1.188 T.

## Proof the physics did not move

Derived card vs the former card, at the former card's temperature (`tests/test_magnet_cards_20c.py`, against
`tests/data/magnet_cards_pre_20c.json` = the library at 3341754e):

| former card | → | Br old / new [T] | Hc [A/m] | knee old / new [kA/m] | mu_rec | alpha / beta [%/K] | curve |
|---|---|---|---|---|---|---|---|
| N52UH_20C | N52UH @ 20 °C | 1.4300 | 1083769 | −1990.0 | 1.050 | −0.1200 / −0.5100 | identical |
| N52UH_100C | N52UH @ 100 °C | 1.2927 | 979728 | −1178.1 | 1.050 | −0.1327 / −0.8615 | identical |
| N52UH_150C | N52UH @ 150 °C | 1.2069 | 914701 | −670.6 | 1.050 | −0.1422 / −1.5134 | identical |
| F45SH_120C | F45SH @ 120 °C | 1.1880 | 900362 | −740.3 | 1.050 | −0.1364 / −1.1505 | identical |
| F52SH_30C | F52SH @ 30 °C | 1.4227 | 1078252 | −1504.4 | 1.050 | −0.1215 / −0.5820 | identical |
| F52SH_80C | F52SH @ 80 °C | 1.3363 | 1012771 | −1066.6 | 1.050 | −0.1293 / −0.8209 | identical |
| F52SH_120C | F52SH @ 120 °C | 1.2672 | 960386 | −716.4 | 1.050 | −0.1364 / −1.2222 | identical |
| N45EH_150C | N45EH @ 150 °C | 1.1480 / 1.1482 (+0.017 %) | 870180 | −856.3 / −856.2 (−0.010 %) | 1.050 | −0.1352 / −1.0843 | max \|ΔB\| 4.8 mT |
| N45EH_180C | N45EH @ 180 °C | 1.1020 / 1.1016 (−0.036 %) | 834881 | −619.0 / −618.9 (−0.022 %) | 1.050 | −0.1409 / −1.6071 | max \|ΔB\| 4.1 mT |

sigma, density, k, cp, CTE, strengths and T_max are the same on every row.

FEM spot check (local, one machine: the pinned 30 mm 12s14p `GEO_30MM` of `tests/test_physics_regression.py`, P2,
12 steps, 1.4 mm mesh, 60 A, 15 000 rpm; gmsh mesh backend because netgen is blocked on the workstation — the same
backend for both sides; the OLD card solved by the same code through a `?mat=` props override):

| case | magnet | T_avg [N·m] | ripple [%] | P_cu [W] | P_fe [W] | V_peak [V] | demag Br mean / min |
|---|---|---|---|---|---|---|---|
| p2_load | F45SH_120C, old card | 0.410229 | 0.4323 | 110.479 | 4.6180 | 7.3562 | — |
| p2_load | F45SH typed 120 °C | 0.410229 | 0.4323 | 110.479 | 4.6180 | 7.3562 | — (bit-identical) |
| p2_demag | N45EH_180C, old card | 0.368221 | 0.5996 | 109.945 | 4.2852 | 7.2819 | 0.7147 / 0.0584 |
| p2_demag | N45EH_180C, former name (derived) | 0.367763 (−0.12 %) | 0.6154 | 109.941 | 4.2816 | 7.2809 | 0.7123 / 0.0563 |
| p2_demag | N45EH typed 180 °C | 0.367763 | 0.6154 | 109.941 | 4.2816 | 7.2809 | 0.7123 / 0.0563 |

The former name and the new form (grade + typed temperature) are the same solve; the N45EH difference is the fitted
curve shape, felt only through irreversible demagnetisation (−0.3 point of mean Br kept at this deep-demag point).

### Differences that DO exist (reported, not hidden)

1. **N45EH** — the rows above (fitted shape; ≤ 0.04 % Br, ≤ 0.02 % knee, ≤ 4.8 mT on the curve).
2. **A typed temperature on a former card** (since #495, 2026-10-29, and every coupled-loop pass): the old code
   STRETCHED the stored hot curve through the intrinsic transform; the new code regenerates it from 20 °C.
   Br and knee are identical; the curve's recoil slope was 1.028–1.040 and is now the card's 1.050. Only the
   irreversible-demagnetisation numbers (demag on) read that slope.

   | duty | Br old / new | knee old / new [kA/m] | curve recoil mu old / new |
   |---|---|---|---|
   | N52UH_150C typed 90 °C | 1.3099 / 1.3099 | −1279.6 / −1279.6 | 1.028 / 1.050 |
   | N52UH_150C typed 120 °C | 1.2584 / 1.2584 | −975.1 / −975.1 | 1.036 / 1.050 |
   | F52SH_120C typed 80 °C | 1.3363 / 1.3363 | −1066.6 / −1066.6 | 1.035 / 1.050 |
   | F45SH_120C typed 90 °C | 1.2366 / 1.2366 | −995.8 / −995.8 | 1.038 / 1.050 |
   | N45EH_150C typed 120 °C | 1.1946 / 1.1948 | −914.8 / −905.5 | 1.040 / 1.051 |
3. **The no-load ψ_PM probe now runs at the run's magnet temperature** (`noload_psi_pm(magnet_temp_c=…)`,
   summary `psi_pm_magnet_temp_c`). It used to run at the card's own temperature whatever the run's was, so a run
   with a typed / loop temperature subtracted a ψ_PM at another temperature in the chord Ld, the dq torque check
   and the no-load KV. Unchanged for a run without a magnet temperature. The report's KV rows and the runaway
   check walk from the probe temperature (old runs without the stamp: from their own card's temperature, as before).
4. **Runaway check of an F52SH machine**: the cold reference was the F52SH_30C card (no 20 °C card existed); it is
   now F52SH at 20 °C — Br 1.4400 vs 1.4227 T, runaway speed 1.2 % lower (the correct, cold side).

## The limit

* Simulation field: a typed magnet temperature above the grade's `max_working_temp_c` turns the field red
  ("Above the grade's 180 °C limit") and Run waits; the API refuses it too (`routes.simulation._typed_magnet_temp_guard`,
  422 with the reason). Blank = the 20 °C card as quoted.
* Coupled loop: the temperature it computes is never clamped; the result carries
  `magnet_over_limit: {temp_c, limit_c, card, basis}` and each EM summary `magnet_limit_c` / `magnet_over_limit`;
  the Magnet T tile is judged against the grade limit.

## Migration (server data — run by the orchestrator, NOT by this PR)

`scripts/migrate_magnet_cards_20c.py` (logic in `src/motor_ai_sim/magnet_migration.py`). Order on the server:

1. Deploy the code. Nothing breaks: the shared library still holds the former cards and they still resolve.
2. Check the shared library is the repo's previous copy (no admin edits that a replace would lose):
   `diff /srv/motres/shared/materials_library.yaml <(git show 3341754e:config/materials_library.yaml)`.
3. Dry run: `python scripts/migrate_magnet_cards_20c.py --root /srv/motres`.
4. Apply with the new library in the same run (refused without it — a configuration renamed to a grade the shared
   library does not hold would stop resolving):
   `python scripts/migrate_magnet_cards_20c.py --root /srv/motres --apply --library /opt/motres/app/config/materials_library.yaml`.
   Every changed file is copied to `<file>.bak-magnets20c-<UTC>` first. A second dry run must report 0 files.

What it changes: `materials.magnet` of every catalog/published/workspace configuration and of every duty, plus the
duty's `mesh['sim.magnetTempC']` (and `runs.<drive>.settings`) where not already set; "my motors" payloads; it
re-stamps the configuration's stored results onto the new build signature. What it leaves (and lists): frozen
product revisions, the live `workspaces/*/motor_config.yaml` (its temperature lives in the browser; the former name
resolves exactly and the next duty load replaces it), stored run records (they name the card a past run used), and
`<grade>_<T>C` names that were never library cards.
