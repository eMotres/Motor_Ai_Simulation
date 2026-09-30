# Gap-layer study cases (march reference) — 2026-09-30

These cases compare air-gap layers per side = 1 / 2 / 3. `gap_layers` counts the element
rows on EACH side of the slip circle, so 3 per side means 6 rows across the gap. The
compared quantities are torque (Coulomb mean), ripple, the Coulomb layer self-check, the
loss groups, the frames to settle and the wall time. Everything that defines a case is in
`scripts/gap_layers_study/`, so a time-periodic (TDM) run can solve the identical cases.

## Inputs (`scripts/gap_layers_study/inputs/`)

The inputs are read-only copies of workspace `c309c100cd421858`, taken 2026-09-30.

| case | files | duty |
|---|---|---|
| `d40` | `d40/die.yaml`, `d40/L12.yaml` (CIANO14 40 new / L12) | `rated`: 42.78 A, γ 10°, 13 000 rpm, sectors 2, mesh 1 mm, star |
| `l13` | `l13/die.yaml`, `l13/L13.yaml` (CIANO28 85 20SW1200 / L13) | `rated`: 25.88 A, γ 2°, 1 000 rpm, sectors 4, mesh 1.22 mm, magnets 210.7 °C |
| `l155` | `l155/die.yaml`, `l155/L155 motor.yaml` (CIANO10 200 opt) | `rated 1x9 mm`: 562.067 A line, Δ, γ 15°, 14 200 rpm, sectors 2, mesh 4 mm, sleeve |

* `motor_config.yaml`: the workspace config. The case's geometry, materials and winding
  are written over it per run (`coul_common.setup`).
* Materials library:
  * `d40` and `l155` use `config/materials_library.yaml` at commit `54f33f2`.
  * `l13` uses `inputs/materials_library_shared.yaml`, the server's
    `/srv/motres/shared/materials_library.yaml` of 2026-09-16. The current library
    refuses L13's 210.7 °C magnet temperature (F52SH_120C, Hcj < 0).
  * The only edit to the copies is that three Russian quotes in comments were
    translated (English-only repo). No value changed.
* Solve settings come from each duty's own `mesh` block: mesh size, min size, outer air,
  sectors and component mesh. `coul_common.setup` has the exact mapping.
* **Excitation:** imposed sinusoidal current (`drive="current"`), winding current
  = line / √3 in delta.
* **Physics, "eddy" rows:** eddy + rotor eddy + demag on, as shipped. The warm-up runs
  to the solver's own settle rule.

## Cases and exact commands (`scripts/gap_layers_study/jobs.txt`)

Line format: `docker -e flags | TAG script args`.

| TAG | machine | gap layers/side | steps/period | physics |
|---|---|---|---|---|
| `e_d40_gl1` / `gl2` / `gl3` | d40 | 1 / 2 / 3 | 48 | eddy + rotor eddy + demag |
| `e_l13_gl1` / `gl2` / `gl3` | l13 | 1 / 2 / 3 | 60 | eddy + rotor eddy + demag |
| `e_l155_gl1` / `gl2` / `gl3` | l155 | 1 / 2 / 3 | 72 | eddy + rotor eddy + demag |
| `s_d40_gl2_r288`, `s_d40_nl_gl2_r288` | d40 | 2, ring 288 | 288 | static, rated / no-load |

Why these step counts: they divide the slip ring at every level (the ring grows with the
gap layers). Ø40: 144 / 192 / 240 nodes per period at gl 1 / 2 / 3. L13: 120 at every
level. L155: 216 / 288 / 336. The L155 gl3 run snaps 72 to 84.

`SB_GAP_LAYERS_MIN=1` switches off the production floor of 3 layers per side
(`sb_domains.GAP_LAYERS_MIN`), so levels 1 and 2 really run. Code without the floor
(`54f33f2`) ignores the variable.

```sh
# server sandbox (nice 19, ionice idle, 4 threads; never the live API)
D=/opt/motres/compute/<your-sandbox>
mkdir -p $D/code $D/in $D/out
cp -r scripts/gap_layers_study/inputs/* $D/in/
cp config/materials_library.yaml config/wire_stock.yaml config/end_effect_3d.json $D/in/
cp scripts/gap_layers_study/*.py $D/code/
cp scripts/gap_layers_study/job.sh scripts/gap_layers_study/runq.sh scripts/gap_layers_study/jobs.txt $D/
cp -r src $D/src                      # the code under test
cd $D && D=$D nohup sh runq.sh > runq.out 2>&1 &
# one case by hand:
D=$D EXTRA_ENV="-e SB_GAP_LAYERS_MIN=1" sh $D/job.sh e_d40_gl1 coul_run.py \
    --machine d40 --gl 1 --steps 48 --eddy --demag
# table:
python scripts/gap_layers_study/gaptable.py $D/out/e_*.json
```

Each run writes `out/<TAG>.json` with every scalar of the result, the per-frame torque
series (Maxwell, Coulomb and both Coulomb rings), the loss series, the mesh size
(`meshes`), `n_frames_solved`, `eddy_warmup_frames` and `wall_s`. `gaptable.py` prints
one row per run.
