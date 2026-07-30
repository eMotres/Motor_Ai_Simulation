# P2 solver trials across the whole motor range — 2026-07-30

The P2 sliding-band solver was built and tuned on the 30/40 mm 12s/14p machine.
This is a **measurement campaign**, not a fix: the same canonical entry point
(`simulation.fem_solver_2d.em_transient_eval`) was run on **every machine the
repo stores**, to find where it breaks or degrades.

* Harness: `scripts/solver_trials.py` (one subprocess per motor, JSON-lines
  append so a crash loses nothing, `--list` / `--report` / `--coil-audit`).
* Raw results: `scripts/_solver_trials_results.jsonl` (23 runs, 2 h 0 min of
  wall time).
* Nothing under `src/` was changed. Every finding below is stated with the
  geometry + operating point that reproduces it.

---

## TL;DR

**No hard failures anywhere.** 23 runs across 30 → 200 mm, three slot/pole
topologies (12s/14p, 24s/20p, 24s/28p) and three symmetry sectors (NS=1/2/4):
**zero exceptions, zero mesh failures, zero timeouts, zero unconverged frames,
zero Picard fallback frames, zero step snapping.** Newton landed at ≤1e-7
against a 1e-3 tolerance on every frame of every machine. The solver is also
**bit-reproducible** across processes and **sector-independent** (full ring
matches the 90° wedge to 0.008 %).

What the campaign *did* find is four things that are invisible from any single
machine:

| # | Finding | Severity |
|---|---|---|
| **F1** | With `eddy=True`, the irreversible-demagnetisation de-rating is computed and reported but **never reaches the solved field**. Same magnet, demag on/off: **0.0 %** torque change with eddy on, **−69 %** with eddy off. | **bug** |
| **F2** | `em_transient_eval` has **no rpm argument** — speed is read from the global config, so any per-request evaluation (optimizer included) silently runs at the shared config's speed. | **API gap** |
| **F3** | Same for the **winding connection**. The one entry whose connection is stored *and* self-consistent reproduces its catalog torque to **−1.1 %** once `I/n_parallel` is applied, and over-reads **+96 %** without it. | **API gap** |
| **F4/F5** | Two internal cross-checks disagree by load-independent, geometry-dependent factors: copper-DC **0.83…2.14×**, energy-vs-Maxwell mean torque **0.2…19.4 %**. Neither is a wedge artifact. | **honesty** |

---

## Protocol (identical for every motor)

P2 sliding-band transient via `em_transient_eval`, current drive:
`n_steps_per_period=24`, `n_periods=1`, `element_order=2`, `structured_gap=True`,
`iron_template=True`, `geo_mesh=True`, `demag=True`, `eddy=True`,
`rotor_eddy=True`, `gap_layers=2` (≥2 element layers per **half** gap, so the
gap always keeps ≥2 layers regardless of its width), `min_size_mm=0.3`,
`outer_air_factor=1.2`, `n_sectors = gcd(slots, poles)`.

* **Mesh target**: `1.4 mm × D/150`, clamped to `[0.5, 2.0]` → 0.5 mm at 30/40 mm,
  0.93 at 100, 1.4 at 150, 1.87 at 200. This is a **ceiling**: the solver
  additionally auto-refines to `min(slot_width, tooth_width)/2`. Air-gap
  resolution is independent of it (`gap_layers`).
* **Motor set**: union of the 12 presets in `config/motor_presets.json` and the
  9 catalog entries in `config/motor_catalog.json`, deduped by geometry, plus the
  **ACTIVE `config/motor_config.yaml`** as the control. Catalog entries carry no
  geometry of their own (they reference a preset), so they contribute the
  **reference performance numbers**. Two preset pairs are byte-identical
  geometry: `my_motor` ≡ `ciano14_30_10`, `m200_20kw_base` ≡ `my_m200_20kw_base`;
  the ACTIVE config is byte-identical to `ciano14_40_12_fe₁₆n₂`. → **11 distinct
  machines**, plus 12 diagnostic variants.
* **Operating point** comes from the entry itself (`max_current`, `rpm`,
  `phase_offset_deg`); every entry stored all three, so the `J_coil ≤ 35 A/mm²`
  fallback was never used. Recorded per run, together with the resulting J.
* Geometry reaches the solver **only** through `geo_override`, materials only
  through `set_request_materials`. The user's config file was never written
  (sha256 verified per run).

---

## Results

Baseline runs (one per distinct machine, smallest first):

| motor | size mm | slots/poles | operating point | T_avg N·m | ripple % | η % | V_peak | gates a/b/c/d/e | wall s |
|---|---|---|---|---|---|---|---|---|---|
| `my_motor` (≡`ciano14_30_10`) | 30×10 | 12s/14p NS=2 | 32 A, 15000 rpm, γ=+6° | 0.2122 | 1.57 | 92.18 | 5.5 | P/P/P/P/P | 183 |
| `my_motor_40mm` | 30×10 | 12s/14p NS=2 | 32 A, 15000 rpm, γ=+10° | 0.2315 | 3.19 | 92.57 | 6.1 | P/P/P/P/P | 229 |
| `ACTIVE_config` (control) | 40×12 | 12s/14p NS=2 | 44 A, 13000 rpm, γ=+10° | 0.7479 | 6.05 | 92.56 | 15.0 | P/**F**/**F**/P/P | 343 |
| `ciano14_40_12_fe₁₆n₂` | 40×12 | 12s/14p NS=2 | 44 A, 13000 rpm, γ=+10° | 0.7479 | 6.05 | 92.56 | 15.0 | P/**F**/**F**/P/P | 298 |
| `ciano14_40_12_fe₁₆n₂` (later geom) | 40×12 | 12s/14p NS=2 | 44 A, 13000 rpm, γ=+10° | 0.6074 | 9.94 | 93.47 | 10.6 | P/P/P/P/P | 232 |
| `motor_40mm` | 40×12 | 12s/14p NS=2 | 35 A, 12000 rpm, γ=−42° | 0.3086 | 35.22 | 91.27 | 10.5 | P/**F**/**F**/P/P | 184 |
| `motor_100mm` | 100×15 | 24s/28p NS=4 | 46 A, 3800 rpm, γ=−36° | 4.8451 | 9.39 | 91.80 | 36.1 | P/P/P/P/P | 270 |
| `ciano20_150_35` | 150×35 | 24s/20p NS=4 | 100 A, 3950 rpm, γ=+26° | 59.8444 | 2.88 | 94.92 | 132.2 | P/P/P/P/P | 292 |
| `my_baseline` | 150×35 | 24s/28p NS=4 | 85 A, 3950 rpm, γ=0° | 53.6195 | 9.82 | 95.03 | 176.5 | P/**F**/**F**/P/P | 249 |
| `m200_20kw_base` (≡`my_m200…`) | 200×45 | 24s/28p NS=4 | 168 A, 2000 rpm, γ=0° | 205.5302 | 6.49 | 90.78 | 362.1 | P/**F**/**F**/P/P | 477 |
| `m200_20kw_lowripple` | 200×45 | 24s/28p NS=4 | 120 A, 2000 rpm, γ=0° | 180.1246 | 3.48 | 93.91 | 335.5 | P/**F**/**F**/P/P | 626 |
| `m200_20kw_opt` | 200×45 | 24s/28p NS=4 | 168 A, 2000 rpm, γ=−10° | 185.4695 | 10.14 | 90.08 | 347.4 | P/**F**/**F**/P/P | 414 |

Gates: **(a)** nonlinear converged on every frame · **(b)** energy-mean vs
Maxwell-mean torque within 5 % · **(c)** copper-DC arithmetic · **(d)** demag map
not saturated to zero · **(e)** no exception / mesh failure.

Diagnostic variants:

| variant | what it changes | T_avg N·m | ripple % | η % | note |
|---|---|---|---|---|---|
| `ciano20_150_35@coilI` | I → I/2 (its stored `2S-2P`) | 30.2347 | 6.03 | 97.08 | **−1.1 % vs its catalog 30.574** |
| `my_baseline@coilI` | I → I/4 (its stored `4P`) | 14.1152 | 64.27 | 97.04 | −44 % vs catalog 25.28 |
| `m200_20kw_base@coilI` | I → I/2 (hypothesis; none stored) | 123.5442 | 1.90 | 95.73 | +27.8 % vs preset 96.66 |
| `my_baseline@NS1` | full ring instead of 90° wedge | 53.6154 | 9.81 | 95.03 | **matches NS=4 to 0.008 %** |
| `…fe₁₆n₂@Fe16N2_lab_best` | magnet → Fe16N2 (per-request override) | 0.6128 | 9.93 | 93.52 | map: 98 % of magnet de-rated |
| `…@Fe16N2_lab_best@demagOFF` | demag off, eddy on | 0.6128 | 9.99 | 93.52 | **identical to demag on** |
| `…@Fe16N2_lab_best@eddyOFF` | demag on, eddy **off** | 0.1871 | 36.47 | 82.12 | **−69 % torque** |
| `…@Fe16N2_lab_best@demagOFF@eddyOFF` | both off | 0.6121 | 10.10 | 93.49 | the reference for the pair above |

---

## Findings, by severity

### F1 — `demag=True` is a no-op when `eddy=True` (bug)

The single most consequential result. Same geometry (40 mm 12s/14p, 44 A,
13000 rpm, γ=+10°), same magnet (`Fe16N2_lab_best` via the per-request material
override), only the two flags change:

| eddy | demag | T_avg N·m | V_peak | η % | demag map |
|---|---|---|---|---|---|
| on | off | 0.61280 | 10.704 | 93.522 | — |
| on | **on** | 0.61280 | 10.703 | 93.522 | 1278/1302 magnet elems de-rated, Br_min 0.136, **mean over the de-rated set 0.369** |
| off | off | 0.61210 | 10.694 | 93.491 | — |
| off | **on** | **0.18710** | **6.820** | **82.122** | 1279/1302 de-rated, Br_min 0.020, mean 0.240 |

With the coupled σ·∂A/∂t solve **off**, honouring the de-rating costs **69 % of
the torque** and 36 % of the back-EMF — the physically expected result for a
magnet that has lost most of its remanence over 98 % of its volume. With the
coupled solve **on**, the identical de-rating changes the reported torque,
back-EMF, iron loss and efficiency by **nothing** (4+ significant figures
identical), while the UI-facing demag map still reports the magnet as ruined.

Code site: `fem_solver_2d.py` ~line 3100, inside the frame loop —

```python
if _dmst.update(_bxe_d[nst:], _bye_d[nst:]) and _dm_pass < 11:
    _mx_all[nst:] = _Mx_glob * _br_glob
    ...
    f = f_mag2 if eddy else (f_mag2 + Ist['A']*f_coil2['A'] + ...)
    _bff2 = np.asarray(Pro.T @ f).ravel()[_free2]
```

The re-assembled right-hand side is written to `_bff2`, which is the **non-eddy**
frame's system vector; the eddy branch solves its own bordered `(A, U, i)`
Newton system that is not rebuilt here. So the de-rated `_br_glob` never enters
the field that gets reported.

Impact: **the P2 default configuration is `eddy=True`** (this campaign, the
Simulation route and `refine_proc` all run it), and the ACTIVE 40 mm design has
`demag: true` in its config. Every such run pays ~2× the wall time (210 s vs
111 s on this machine) for a demag map that does not influence a single reported
number. It also means demag-constrained optimization on P2 is optimizing against
a constraint the physics never felt.

Reproducer: `SOLVER_TRIALS_EDDY=0/1 SOLVER_TRIALS_DEMAG=0/1 python
scripts/solver_trials.py --only "ciano14_40_12_fe₁₆n₂@Fe16N2_lab_best"`.

### F2 — the canonical entry point has no speed argument (API gap)

`em_transient_eval(...)` takes `I_phase_rms` and `gamma_deg` but **not rpm**.
`fem_transient_sliding_band` reads it from the global config
(`rpm = float(sim.get("rpm", 3950))`, `f_elec = rpm·poles/2/60`). So
`geo_override` can move the geometry to another machine while the speed stays
whatever the shared config holds.

Measured on the 30 mm control (its entry says 15000 rpm; the global config held
13000 rpm at the time):

| rpm actually used | f_elec | T_avg | P_fe | V_peak | η |
|---|---|---|---|---|---|
| 13000 (global config) | 1516.7 Hz | 0.2122 | 2.08 W | 4.80 V | 91.32 % |
| 15000 (entry's own) | 1750.0 Hz | 0.2122 | 2.60 W | 5.48 V | 92.18 % |

Torque is unaffected (magnetostatic, current-driven) but iron loss is off by
−20 %, back-EMF by −12.5 % and efficiency by 0.86 pp. This is not hypothetical
for the optimizer: `refine_proc.run_one` reads `cfg["simulation"]["rpm"]` for its
own ω and then relies on the solver reading the same global value — correct only
as long as the active config's speed *is* the candidate's speed. The harness
works around it by patching the in-memory config inside its throwaway
subprocess (the YAML's sha256 is verified unchanged after every run).

### F3 — no winding-connection argument either (API gap)

The FEM only ever sees `I_coil = I_phase / n_parallel`, and `n_parallel` comes
from the global config's `winding` block. There is no per-request channel for it
(unlike `geo=` and `mat=`), so a trial of a stored machine drives `n_parallel ×`
its intended coil MMF. The evidence is unambiguous:

| entry | stored connection | as stored (n_par=1) | at I/n_parallel | catalog T |
|---|---|---|---|---|
| `ciano20_150_35` | `2S-2P` (n_par 2) | 59.844 N·m (**+95.7 %**) | **30.235 N·m (−1.1 %)** | 30.574 |
| `my_baseline` | `4P` (n_par 4) | 53.620 N·m (+112.1 %) | 14.115 N·m (−44.2 %) | 25.28 |
| `m200_20kw_base` | *none stored* | 205.530 N·m (+112.6 %) | 123.544 N·m at I/2 (+27.8 %) | 96.66 |

`ciano20_150_35` is the campaign's cleanest validation of the solver itself: a
150 mm 24s/20p machine at its own stored operating point reproduces its stored
torque to **1.1 %** once the connection is applied. It also shows the cost of the
gap — the same run without it is a factor 1.96 out, which is exactly its two
parallel paths. `my_baseline`'s stored `4P` is *not* consistent with its stored
torque (25.28 N·m sits between our 21.25 A and 85 A results, i.e. ≈2 parallel
paths, not 4), and the 200 mm presets store no connection at all.

Note the global `winding` block itself is internally inconsistent for the
24-slot machines: `n_coils_per_phase: 4` with `n_series: 2, n_parallel: 1`
(2 × 1 ≠ 4).

### F4 — copper-DC cross-check: two routes to the same watts differ 0.83…2.14×

Gate (c) has two identities. The first holds **exactly** on every machine:
`3·I²·R_phase == P_cu_dc_W` to 0.0000 % (R is derived from it). The second — the
code's own stated cross-check, "two independent routes to the same watts" — does
not:

* analytic active-length DC = `P_cu_dc_W / k_end` (`field_ops.copper_loss_W`:
  ρ_Cu(T)·J²·V_cu with V_cu from the **nominal** wire rectangle);
* solved active-length DC = `P_cu_dc_2d_solve_W` = `Σ_b I_b²/S_b` with
  `S_b = σ_Cu(T)·A_meshed_coil` (the coupled eddy solve's own constraint rows).

| machine | wires/slot × h | solve / analytic |
|---|---|---|
| `my_motor_40mm` 30 mm | 6 × 0.5 | 1.023 |
| `motor_100mm` | 10 × 0.65 | 1.028 |
| `…fe₁₆n₂` (7-wire geom) | 7 × 0.6 | 1.037 |
| `ciano20_150_35` | 14 × 0.7 | 1.049 |
| `my_motor` 30 mm | 6 × 0.5 | 1.072 |
| `motor_40mm` | 8 × 0.6 | 1.234 |
| `m200_20kw_base` / `_opt` | 18 × 0.8 | 1.366 |
| `…fe₁₆n₂` (9-wire geom) | 9 × 0.5 | 1.440 |
| `m200_20kw_lowripple` | 20 × 1.0 | **2.139** |
| `my_baseline` | 14 × 0.6, split 2 | **0.827** |

The ratio is **identical at two different currents** on all three machines that
were run twice (`ciano20` 4.87 % at 100 A and 50 A; `my_baseline` 17.35 % at
85 A and 21.25 A; `m200` 36.60 % at 168 A and 84 A) and **identical at NS=1 and
NS=4**. Both routes scale as I², so this is a pure **conductance** disagreement:
a fixed geometric property of each design, not a load or symmetry effect.

Partial mechanism, measured: `--coil-audit` builds the 2-D polygons and compares
the CAD copper section against the nominal `n_wires × wire_width × wire_height`.
Two designs come out **25 % short** — `motor_40mm` 0.742 and
`m200_20kw_lowripple` 0.748 (all others 1.0000) — i.e. the CAD clips wires the
analytic loss assumes are full rectangles. Those are also two of the three worst
ratios, but the direction/magnitude does not close the gap for `m200_20kw_base`
(area ratio 1.0000, ratio 1.366) or `my_baseline` (area 1.0000, ratio 0.827), so
a second mechanism is present — the remaining suspects are the σ(T) used for
`S_b` versus ρ(T) in `copper_loss_W`, and `wire_split` (the only design below 1.0
is the only one with `wire_split: 2`).

Consequence for reported numbers: the reported copper is
`P_cu_dc(analytic, with k_end) + (solve_total − solve_dc2d)`. The AC increment is
a difference **within** the solve, so it is self-consistent; but the reported DC
and the solve's DC describe different resistances, and the loss-density map is
closed with `P_cu_end_winding_W = max(0, P_cu_dc2 − P_cu_dc2d_avg2)` — which is
clamped to 0 exactly on the designs where the solve's DC is the larger of the
two (`my_baseline` here).

### F5 — energy-mean vs Maxwell-mean torque: 0.2…19.4 %, per geometry

`T_avg_Nm` is the energy/flux-linkage mean (correct DC) with the Maxwell AC
re-centred on it; `T_avg_maxwell_Nm` is the raw Arkkio mean. They measure the
same physics two ways, so the gate asked for 5 %:

| machine | Δ (energy vs Maxwell) | sign |
|---|---|---|
| `my_motor_40mm`, `ciano20_150_35`, `motor_100mm`, `…fe₁₆n₂` (7-wire) | 0.24 – 0.96 % | — |
| `my_motor` 30 mm | 1.99 % | Maxwell high |
| `motor_40mm` (γ=−42°) | 8.26 % | Maxwell **low** |
| `my_baseline` 150 mm | 10.38 % | Maxwell low |
| `m200_20kw_base` / `_opt` | 10.45 / 11.23 % | Maxwell high |
| `…fe₁₆n₂` (9-wire geom) | 16.32 % | Maxwell high |
| `m200_20kw_lowripple` | **19.37 %** | Maxwell high |

Three things were ruled out by measurement:

* **not a sector/wedge artifact** — `my_baseline@NS1` (full ring) gives 10.37 %
  against the wedge's 10.38 %, with T_avg matching to 0.008 %;
* **not saturation/load** — the divergence is the same at two currents
  (`my_baseline` 10.38 % at 85 A, 10.87 % at 21.25 A; `m200` 10.45 % at 168 A,
  10.82 % at 84 A; `ciano20` 0.62 % / 0.85 %);
* **not scale** — the 200 mm is a linear 4/3 copy of the 150 mm and inherits the
  same ≈10 %, while the 100 mm (same 24s/28p topology) sits at 0.84 %.

So it is a per-geometry constant of unknown origin, and it matters twice over:
the reported **ripple percentage** is a Maxwell peak-to-peak normalised on the
energy mean, so wherever these two disagree by 10-19 % the ripple % carries the
same bias. The historical note (Maxwell-on-band over-reading ~35-37 % under
load) is *not* what is being seen here — the sign flips between machines, and the
worst case is a low-current 200 mm design, not a heavily loaded one.

### F6 — an unknown material name silently changes the physics

One run (`my_baseline@coilI`, first attempt) caught the live config holding
`magnet: N42SH`, which is not in `config/materials_library.yaml`. The solve did
**not** fail: it logged
`Magnet material 'N42SH' lookup failed: … Available: [...]` and fell back to the
analytic magnet — no BH curve and, notably, **no demag knee**, so the demag map
came back empty and the shaft-eddy loss came out **63.9 W instead of 7.0 W**
(9×). Re-running the identical case with `F45SH_120C` restored it. A typo'd or
stale material name therefore produces plausible-looking numbers rather than an
error, and only a WARNING in the log distinguishes them.

### F7 — stored reference performance is largely not reproducible (data, not solver)

Deltas of our T_avg against each entry's stored torque, worst first:

| entry | stored | ours | Δ | reading |
|---|---|---|---|---|
| `my_motor_40mm` | 0.444 | 0.2315 | **−47.9 %** | the catalog entry `cat_my_motor_40mm` is a 30 mm design carrying the 40 mm entry's numbers verbatim (0.444 N·m / 558 W / 91.4 % appear on both) — a copy-paste, not a solver error (energy and Maxwell agree to 0.24 %) |
| `m200_20kw_base` | 96.66 | 205.53 | +112.6 % | no winding connection stored (F3); ~2 parallel paths would put it at 123.5 (+27.8 %), still not a match |
| `my_baseline` | 25.28 | 53.62 | +112.1 % | stored `4P` inconsistent with stored torque (F3) |
| `ciano20_150_35` | 30.574 | 59.84 | +95.7 % | **explained**: −1.1 % once its `2S-2P` is applied |
| `m200_20kw_opt` / `_lowripple` | 95.19 / 105.2 | 185.5 / 180.1 | +94.8 / +71.2 % | as `m200_20kw_base` |
| `motor_40mm` | 0.444 | 0.3086 | −30.5 % | at γ=−42°; `docs/SOLVER_VALIDATION_2026-06-28.md` converged this machine to 0.565 N·m at 38 A/γ=−32°, a different operating point |
| `motor_100mm` | 6.0 | 4.845 | −19.2 % | energy and Maxwell agree (−19.2 / −19.9 %), so this is the reference, not the torque method. 6.0/4.845 = 1.24 — consistent with a reference produced by an over-reading path |
| `ciano14_40_12_fe₁₆n₂` | 0.691 | 0.6074 | −12.1 % | the preset named for Fe16N2 **stores no material assignment**, so it runs on F45SH; its stored number came from an Fe16N2 run (see `config/saved_simulations.json`, "Fe16N2 lab-best · 42 A · demag") that the preset cannot reproduce |
| `my_motor` | 0.212 | 0.2122 | **+0.1 %** | pinned control, exact |

Also: `my_motor` and `ciano14_30_10` are **byte-identical geometry at an
identical operating point**, yet the catalog stores 0.212 vs 0.242 N·m for them
(14 % apart). Ours reproduces the first. Three catalog entries store
`ripple_pct: 0`, which the measurements show to be a placeholder rather than a
result (`motor_40mm` measures 35.2 % raw ripple at its own operating point, with
a clean order-6 spectrum and a 0.00 % numerical noise floor — real ripple, not
solver hash).

### F8 — timing

Total 2 h 0 min for 23 runs; per-run 102 – 987 s. Wall time tracks the number of
coil domains and DOFs, not the diameter:

* `m200_20kw_lowripple` **626 s** — the campaign's slowest baseline run, and the
  design with the most coil domains (480 = 24 slots × 20 wires).
* `my_baseline@NS1` **987 s** vs 249 s at NS=4 — 4.0× for 4× the domain, i.e. the
  wedge saves exactly what it should, and (F5) costs nothing in accuracy.
* `demag=True` **doubles** the wall time (210 s vs 111 s on the 40 mm) — and per
  F1, buys nothing when `eddy=True`.
* `eddy=False, demag=True` (574 s) is **slower than** `eddy=True, demag=True`
  (210 s), because the demag re-solve loop only actually runs in the non-eddy
  branch. That timing asymmetry is itself a symptom of F1.
* No run came near the 30 min kill threshold; nothing was killed.

### F9 — smaller observations

* **Demag map shape is inconsistent**: a run with nothing past the knee returns
  either an all-ones map (`motor_100mm`, `ciano20_150_35`) or **no map at all**
  (`my_baseline@coilI`), so a consumer must handle both.
* `skfem.io.meshio: Failure to parse tags from meshio` appears twice per build on
  the 24-slot machines (4 occurrences total, `motor_100mm` and `ciano20_150_35`),
  never on the 12-slot ones. Harmless as far as the results show, but it is an
  unexplained difference in the mesh import path between topologies.
* **Reproducibility (positive)**: `my_motor` was run twice in independent
  processes → identical to all reported digits (T 0.2122, ripple 1.5668, η
  92.176 %). `ACTIVE_config` and its byte-identical preset twin
  `ciano14_40_12_fe₁₆n₂` → identical (T 0.7479, ripple 6.05, η 92.556 %) while
  differing 343 s vs 298 s in wall time.
* **Step snapping never triggered**: 24 steps/period divides the slip-node grid
  (192 nodes/period on the 12s/14p machines, 120 on the 24s/28p) on every
  machine, so no run silently ran at a different time resolution.

---

## Campaign hazards worth knowing

* **The input files moved under the campaign.** `config/motor_config.yaml`,
  `motor_presets.json` and `motor_catalog.json` were rewritten by another process
  (running backend / open UI) at least three times during the 2 h: the 40 mm
  design's `max_current` went 42 → 44 A and its wire section 2.2×0.5 → 2.2×0.6
  (9 wires → 7), and the magnet assignment briefly became a non-existent
  material (F6). `motor_ai_sim.config` follows the file's mtime (1 s probe), so a
  long solve can pick up an edit mid-flight. Consequences: the two
  `ciano14_40_12_fe₁₆n₂` rows in the table are the **same key on two different
  geometries** (and the gate b/c failures follow the geometry, not the key), and
  every record after the mid-campaign edits stores its own `geometry_used` +
  `geometry_sha256` so drift is auditable. The verified-unchanged geometries
  (`motor_40mm`, `motor_100mm`, `ciano20_150_35`) are marked MATCH against their
  records. Later runs used a frozen snapshot via `SOLVER_TRIALS_INPUT_DIR`.
* **Materials always come from the live config**, not from the entry: no preset
  or catalog entry stores a material assignment, so every baseline run used
  `B15AHV950M` steel + `F45SH_120C` magnets regardless of what the entry is named
  after. The per-request override channel itself works — the Fe16N2 variant took
  effect and drove the demag map from 63 to 1278 de-rated elements.
* **`n_parallel = 1`** (from the global config) on every baseline run; see F3.

## Reproducers

```bash
# the plan, and the whole campaign (smallest first, 30 min kill per motor)
python scripts/solver_trials.py --list
python scripts/solver_trials.py

# F1 — demag is a no-op with the coupled eddy solve, and −69 % without it
SOLVER_TRIALS_DEMAG=1 SOLVER_TRIALS_EDDY=1 python scripts/solver_trials.py --only "ciano14_40_12_fe₁₆n₂@Fe16N2_lab_best"
SOLVER_TRIALS_DEMAG=0 SOLVER_TRIALS_EDDY=1 python scripts/solver_trials.py --only "ciano14_40_12_fe₁₆n₂@Fe16N2_lab_best"
SOLVER_TRIALS_DEMAG=1 SOLVER_TRIALS_EDDY=0 python scripts/solver_trials.py --only "ciano14_40_12_fe₁₆n₂@Fe16N2_lab_best"
SOLVER_TRIALS_DEMAG=0 SOLVER_TRIALS_EDDY=0 python scripts/solver_trials.py --only "ciano14_40_12_fe₁₆n₂@Fe16N2_lab_best"

# F3 — the connection is the whole +96 %
python scripts/solver_trials.py --only ciano20_150_35
python scripts/solver_trials.py --only ciano20_150_35@coilI

# F4 — CAD copper section vs the nominal one the loss formula uses
python scripts/solver_trials.py --coil-audit

# F5 — the divergence is not the anti-periodic wedge
SOLVER_TRIALS_NS=1 python scripts/solver_trials.py --only my_baseline

# summarise everything collected so far
python scripts/solver_trials.py --report
```

Worst-case single reproducers, as geometry + operating point:

* **F1**: 40 mm 12s/14p, `motor_length` 12, `air_gap` 0.2, 7×(2.2×0.6) wires,
  44 A rms, 13000 rpm, γ=+10°, magnet `Fe16N2_lab_best`, `demag=True`,
  `eddy=True` → demag map claims Br_mean 0.369 over 98 % of the magnet, torque
  identical to `demag=False`.
* **F4**: `m200_20kw_lowripple` — 200 mm 24s/28p, 45 mm stack, 20×(6.1434×1.0)
  wires, 120 A, 2000 rpm, γ=0 → solve DC 2.14× the analytic DC.
* **F5**: same machine → 19.37 % energy-vs-Maxwell mean torque divergence.
