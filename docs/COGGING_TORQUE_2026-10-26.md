# Cogging torque as a parameter of every configuration (2026-10-26)

Owner, 2026-10-26: «это важный параметр во все моторы — амплитуда или полуамплитуда зубцового момента».
Decision (orchestrator): the headline is the **peak-to-peak** cogging torque in N·m; the tooltip carries the
half-amplitude and the share of the configuration's continuous (rated) torque.

## What it is and where it lives

Cogging is a property of the machine (slots x poles x magnet card x geometry), not of a duty: it is the torque the
magnets make on the slotted stator with no current. The app refuses a duty with 0 A (`op.current_rpm_positive`), so it
is a **configuration-level measurement**: the `cogging:` block of `config/dies/<die>/<cfg>.yaml`, beside `battery` and
`bearings`. Code: `src/motor_ai_sim/cogging.py` (method, pure numbers, the stored block and its view),
`src/motor_ai_sim/routes/cogging.py` (routes), `scripts/cogging_run.py` (headless call).

## The method (one rule, no second method)

* sliding-band P2 transient, current drive at exactly I = 0, `daxis_deg` given (no calibration solve), eddy, rotor eddy
  and demagnetisation off, `torque_method="coulomb"`;
* `sampling_purpose="cogging_quality"`, 12 raw samples per cogging cycle over ONE electrical period, which always holds
  a whole number of cogging cycles: cycles per electrical period = LCM(slots, poles) / pole pairs (12s/14p: 84 / 7 = 12,
  so 144 frames);
* the configuration's own Mesh settings (default duty's Mesh block; the machine's saved Mesh config when it has none;
  `mesh_size_mm` overrides for a deliberately coarse check), sector count clamped to a divisor of gcd(slots, poles);
* torque = Coulomb virtual work (owner 2026-09-30, the project's energy-consistent torque). The flux-linkage (Clarke)
  torque cannot serve here: at I = 0 it is identically zero. The Maxwell band over-reads (torque methodology note).
* magnet card = the configuration's, at the temperature stated on the result (default: the card as the library quotes
  it, i.e. its reference temperature; `magnet_temp_c` corrects Br/Hc along the card's own coefficients).

## Decision recorded: frozen permeability is NOT the default

The brief asked for frozen permeability (`frozen_nu`, the comment in `fem_solver_2d.py` calls it "the industry-standard
method for honest cogging"). Measured on a 40 mm 12s/14p configuration, coarse mesh 2.0 mm, 144 frames, 2 sectors:

| iron | pk-pk | mean | spectrum (electrical harmonic: amplitude) | runtime (SuperLU, 2 threads) |
|---|---|---|---|---|
| frozen at frame 0 | 0.699 N·m | +0.022 N·m | 2: 0.298, 1: 0.072, 4: 0.040, 3: 0.032 (cogging order 12: 0.009) | 63 s |
| re-converged at every position | 0.064 N·m | -0.0002 N·m | 6: 0.032, 12 (= order 84): 0.0045, 18: 0.0019 | 342 s |

A symmetric 12-slot machine produces only multiples of 84 per mechanical turn (12 and its multiples per electrical
period). The frozen run is dominated by electrical harmonics 1, 2, 4 that it cannot produce: the saturation pattern of
frame 0 is frozen into the stator teeth and breaks the slot symmetry (the old reason for `frozen_nu`, the Picard
plateau of 5-8 N·m, is gone with the Newton saturation solve). So the default is `frozen_nu=False`; the option stays for
comparison and the block records which one made the number.

The same number came out of the new endpoint on a sandbox API (`POST /api/simulation/cogging/run`, `save: true`, mesh 2.0 mm,
PARDISO off): pk-pk 0.06365 N·m, half-amplitude 0.03183 N·m, 10.2 % of the rated duty's 0.623 N·m, fundamental (order 84)
amplitude 0.0045 N·m, 371 s. It is a COARSE-mesh number (the configuration's own mesh is 1.0 mm); confirm it on that mesh
before quoting it to anyone.

Open question, not settled here: the re-converged run's largest line is harmonic 6 (order 42 = LCM/2), not 12 (order
84). Check with a full-ring run (`n_sectors=-1`) whether the 2-sector wedge adds it.

## What a stored number is guaranteed to be (review fixes, 2026-10-26)

* The solver must say `torque_method == "coulomb_virtual_work"`; if the Coulomb set-up failed and it returned the
  Maxwell/hybrid series, the run raises and nothing is stored.
* The label carries the DELIVERED samples per cycle (the solver snaps the frame count to a slip-ring divisor);
  fewer than asked for (12) is refused, `mesh.steps_per_period` is the delivered count (`steps_requested` beside it).
  A run whose saturation solve did not converge (`picard_converged` false) is refused; `converged` is stored.
* Only a run with `die`, `config` and `build_sig` can be saved; a live-machine run never is. A pending run (16 newest, in
  memory) belongs to the account that solved it.
* `save` with any override (frozen permeability, `mesh_size_mm`, `magnet_temp_c`, `samples_per_cycle`) is an
  administrator's decision (403 for others, before the solve); an admin's saved override is stored in `overrides` and
  named in the tooltip, the datasheet note and the report note.
* Bounds: `mesh_size_mm` 0.1..20 and not below the machine's smallest element, `samples_per_cycle` 6..48, at most 720 frames.
* The percentage is against the VERIFIED S1 torque (the datasheet's checks: feasible and `verified is True`) and says
  "of the continuous torque"; without one it is against the rated duty's solved torque and says "of the rated duty
  torque (<source>)".
* The ordinary Configure shows the tile only while the loaded machine is the reference configuration, unscaled.

## Stored block, staleness

`cogging: {pkpk_Nm, half_amp_Nm, mean_Nm, fundamental_order, fundamental_amp_Nm, dominant_order, method, frozen_nu,
magnet_card, magnet_temp_c, mesh, geo_hash, build_sig, computed_at, runtime_s, solver, waveform{angle_deg, torque_Nm}}`.
`build_sig` is `routes.family._build_sig` (die geometry + configuration overrides + winding + materials + part states),
the same fingerprint duty results carry. A different signature marks the block `stale` (shown amber, never dropped); a
materials adoption that keeps the build moves it with `_restamp_results`. The share of the continuous torque is computed
at read time against the S1 search's torque (`coupled.continuous_rating.power.T_em_Nm`), else the rated duty's solved
torque, else not claimed.

## Where it is shown (one tile, "Cogging torque (pk-pk)", never without a value)

Ordinary Configure (`references[].cogging`), points-limited Configure (`comparison.cogging_*` in the project points feed),
project comparison table, Motors row (family tree `cogging`), datasheet (`Cogging torque, peak-to-peak (N·m)` next to
the torque ripple), PDF/docx report (the torque table and the duty comparison). Strings: `configure.cogging*` in the
`controller` namespace, EN and zh-CN.

## Calls

```
POST /api/simulation/cogging/run   {"die": "...", "config": "...", "save": true, "mesh_size_mm": null, "magnet_temp_c": null,
                                    "frozen_nu": false, "samples_per_cycle": 12}      # no die/config = the live machine
PUT  /api/simulation/cogging/{die}/{cfg}   {"cogging_id": "..."}    # save a finished run (the 16 newest are kept in memory)
GET  /api/simulation/cogging/{die}/{cfg}?waveform=true
python scripts/cogging_run.py --die "<die>" --config <cfg> --save            # in-process, no login
python scripts/cogging_run.py --api https://aerostator.com --token "$T" --die ... --config ... --save
```
The prefix `/api/simulation` is already the compute worker's route (deploy stage 2), so no nginx change is needed.
