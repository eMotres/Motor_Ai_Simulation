# CAD generator: parametric FreeCAD motor models from the simulation store

One parameter source: the simulation die/config (`/srv/motres/shared/dies/<die>/{die.yaml, L*.yaml}` and the
materials library). The generator reads it, never writes it, and builds the motor model with FreeCAD 1.1 on the server.

**Tool versions (owner 2026-10-18): FreeCAD 1.1.3 only**, on the server (image `motres-cad:1.1.3-*`, the current tag in
`/etc/motres/cad-image`) and on any workstation; **KiCad 10.0** for boards. Never fall back to FreeCAD 1.0.2. The scripts'
"1.0.2 / 1.1" notes describe the version-safe code, they are not an instruction. A locally built model is finished only after
the GUI finish (GuiDocument.xml: visibility, colours, camera); without it FreeCAD opens it with no 3-D view.

Roadmap: (1) model done, (2) this generator + server build per die/config, (3) BOM (done, section below), (4) STEP download + the web "3D model" menu (done, section below: view for
everyone, STEP download under NDA), (5) 3-D load FEM.

## Layout

| Path | What |
|---|---|
| `cad/motor_templates/segmented_flatwire/` | the FreeCAD template (PartDesign + Draft arrays), version-safe for FreeCAD 1.0.2 and 1.1. `TEMPLATE_VERSION` in its `__init__.py` goes into every manifest. |
| `cad/reference/` (**private**: only the three tool scripts stay here; the data lives in `eMotres/motor-ai-sim-private` `cad/reference/`, see "Private reference files") | Fusion reference profile (`CIANO14_40_12.json`, the open Fusion model), its parameter values, `Coil_L20_fusion.step` (read-only Fusion export used for the coil check), `fusion_mechanics/` (read-only Fusion STEP + body/occurrence JSON of Shaft, 618_8, Stator_holder(+screws), Stator_holder_back, Ring, Prop_fix, 91292A032), `add_mechanics_rows.py` (adds the stage-3 rows to a profile) |
| `cad/fc_scripts/` | scripts that run inside FreeCAD: `build_model.py`, `gui_finish.py` (headless GUI: colours, camera), `check_gui.py`, `export_step.py`, `solids_dump.py`, `coil_vs_reference.py`, `mechanics_vs_reference.py` (every mechanical part vs Fusion), `owner_edits.py` (Params edits on a built model + gate + fit) |
| `src/motor_ai_sim/cad/` | `store.py` (store -> profile, name mapping, fingerprints, owner sync-back diff), `manifest.py`, `runner.py`, `build.py` (CLI). Stdlib + PyYAML only. |
| `cad/run_build.py` | launcher without the FEM stack (used by the image) |
| `deploy/Dockerfile.cad` | FreeCAD 1.1.3 (conda-forge) in its own image, `motres-cad:1.1.3` |

Name mapping is the one of `GET /api/fusion/params.csv`: `config/fusion_param_map.yaml`, `DERIVED_GEOMETRY_NAMES`
(`routes/_validation.py`), `FUSION_EXCLUDED_NAMES` (`routes/fusion.py`) and `geometry_schema` (`config/motor_config.yaml`).
Derived values are never CAD inputs; `wire_split` / `wire_parallel` have no CAD geometry and are listed as "sim only" in
`pull_report.json`.

History: moved from the `CIANO14_40_motor_model` repo (template commit 8ef2e35, `tools/sim_sync.py`, export and
validation scripts). That repo keeps its history and the local owner files.

## Private reference files (owner decision 2026-10-19)

All 3-D models are private data. The files that used to be in `cad/reference/` (Fusion reference profiles and parameter dumps,
`Coil_L20_fusion.step`, `fusion_mechanics/*`, `phase_board/*.step`, `designs/`) are NOT in this repository any more; they are
in the private repository `eMotres/motor-ai-sim-private` under the same path `cad/reference/`. The three tool scripts
(`add_mechanics_rows.py`, `add_phase_board_rows.py`, `make_die_reference.py`) stay here.

`store.find_reference_dir()` looks for the folder in this order and takes the first one that holds `CIANO14_40_12.json`:

1. `$MOTRES_CAD_REFERENCE_DIR` (the `cad/reference` folder itself),
2. `$MOTOR_AI_SIM_PRIVATE_DATA/cad/reference` (a checkout of the private repository, the one variable of the app),
3. `<repo>/cad/reference` (what the CAD image bakes in),
4. `<repo>/../motor-ai-sim-private/cad/reference` (a sibling checkout),
5. `/srv/motres/private_assets/cad/reference` (the server).

The builder hands the folder to FreeCAD as `$FC_CAD_REFERENCE` (the phase board STEP is found there). Without the files a build stops with
a message that names the folders it searched.

**CAD image (server).** The image gets the reference files from a SECOND build context named `private`; the build fails when it has no
`cad/reference`. The image carries 3-D data: it is private and is never pushed to a public registry.

```bash
# /srv/motres/private_assets = a checkout of eMotres/motor-ai-sim-private
#   (until a deploy key exists: a copy of cad/reference from the previous deploy, see deploy/README.md "Private assets")
cd /opt/motres/app    # the merged tree
docker build -f deploy/Dockerfile.cad --build-context private=/srv/motres/private_assets \
  --build-arg GIT_SHA=$(git rev-parse HEAD) -t motres-cad:1.1.3-<tag> .
echo motres-cad:1.1.3-<tag> > /etc/motres/cad-image        # after a smoke build
```

A local build is the same with `--build-context private=<path of the motor-ai-sim-private checkout>` (Docker Engine 23+ / BuildKit).
Without Docker: `MOTOR_AI_SIM_PRIVATE_DATA=<checkout> python cad/run_build.py build ...`.
What the two contexts send, without building: `python scripts/cad_context_listing.py --private <checkout>`.

## Rebuild

On the server (the image has no network need; the store is mounted read-only):

```bash
docker run --rm --user 10001:10001 --cpus 2 \
  -v /srv/motres/shared/dies:/store/dies:ro \
  -v /srv/motres/shared/materials_library.yaml:/store/materials_library.yaml:ro \
  -v /srv/motres/shared/bearings_library.yaml:/store/bearings_library.yaml:ro \
  -v /srv/motres/shared/cad:/out \
  motres-cad:1.1.3 build --die "CIANO14 40 new" --config L12 --out-root /out
```

Wrap it in `nice -n 19 ionice -c3` and do not take the compute-lane locks. Without Docker, with FreeCAD installed:
`python -m motor_ai_sim.cad.build --die ... --config ... --out-dir DIR` (or `python cad/run_build.py build ...`;
`--freecadcmd` / `$FREECADCMD` / PATH finds the binary). `--dry-run` writes only the profile. `--profile-file P --out-dir D`
builds from an explicit profile (reference builds, tests).

Exit code 0 ok, 2 bad input, 3 refused by a check (pre-check of the validity domain, health gate, visibility rules,
"opens ready to look at" check, STEP re-import check), 4 crash. The build goes into a temporary sibling directory and
replaces the old output only when every check passed.

## Outputs: `/srv/motres/shared/cad/<die>/<config>/`

| File | |
|---|---|
| `<motor_id>.FCStd` | the parametric model; `<motor_id>` = `<die with underscores>_<config>`, e.g. `CIANO14_40_new_L12` |
| `<motor_id>_active.step` | full STEP: Stator/Core, Winding/Coils/Coil_n, Rotor/RotorCore, Magnets/Magnet_nn (22 solids) |
| `<motor_id>_datums.json` | frame, axial and radial datums, end face of every coil lead |
| `manifest.json` | die, config, `param_hash` (CAD parameters + materials + winding), `geo_sig` / `geo_sig_hash` (the `_geoSig` dialect of `routes.presets`), SHA-256 of every store file used, FreeCAD version, template name / version / content hash, git sha, build time, check results, SHA-256 of every output |
| `params.json`, `pull_report.json` | the profile the template was fed; simulation vs CAD parameter table |
| `build_report.json`, `step_check.json`, `gui_report.json`, `gui_check.json` | evidence of the checks |

The model opens ready to look at: bodies Core, Coil, RotorCore, Magnet, their Parts and the two arrays visible (a body
shows its Tip only); sketches, intermediate features, origins, axes, planes and the Params sheet hidden; colours from the single palette
(`palette.py`: see "One colour palette" below; magnets alternate by index); isometric camera fitted to
the model in `GuiDocument.xml`. The colours and the camera are written by a headless FreeCAD GUI step
(`QT_QPA_PLATFORM=offscreen`; `MOTRES_CAD_XVFB=1` uses `xvfb-run`); the rules are checked again through the GUI
(`check_gui.py`) before the output is accepted.

## Mechanics (owner-repo PLAN stage 3, 2026-10-12)

Built like the Fusion model CIANO14 40 (read-only MCP dump 2026-10-12, `cad/reference/fusion_mechanics/`), in the App::Part
`Mechanics` (`motor_templates/segmented_flatwire/mechanics.py`):

| Body (Fusion component) | How it is built | Fusion features |
|---|---|---|
| `Shaft` | revolve of the half section + drive-flat pocket | Sketch3 profile 5, Revolve3, Extrude15 (Sketch10), Chamfer7 (in the sketch) |
| `Bearing_618_8` (Part: `Bearing_outer_ring`, `Bearing_inner_ring`, `Bearing_ball` + `Balls`) and the link `Bearing_618_8_back` | revolves (edge radii, ball groove) | 618_8 import; d / D / B from the config's bearing card |
| `Stator_holder` | revolve, window between the spokes (pocket), boss to the stator face, seat into the pocket between the core legs, seat chamfers (pocket), x `holder_bolt_count` | Sketch3 profile 0, Sketch2 profiles 16 / 9-11, Extrude42/45/47, Chamfer23, C-Pattern |
| `Stator_holder_back` | revolve, through window, bosses / seats with M2 tap holes, six M3 holes in the A_H face, seat chamfers, spoke-end fillets (pocket), x `holder_bolt_count` | Sketch3 profile 6, Sketch2, Sketch7, Extrude41/42/43/47, Chamfer23, Fillet27 |
| `Ring` + link `Ring_back` | revolve | Sketch3 profile 2, occurrences Ring:1 / Ring:3 |
| `Prop_fix` | double-D hub on the flats + plate, hole faces offset by the clearance in the sketch | Sketch2, Extrude1/2, OffsetFaces1 |
| `Screw` + `Screws` | M2 socket head screw on the bolt circle, polar array | 91292A032 in Stator_holder |

Fusion builds every turned part from one axial sketch (Sketch3); a PartDesign body owns its sketches, so each body has its
own half section, all dimensioned from the same Params rows: the Sketch3 dimension names (`named_parameters_sketch3`) and
the named derived rows of the axial chain (`named_parameters_mechanics`: `holder_face_z = motor_length/2 + ring_h +
ring_holder_gap`, `bearing_out_z`, `front_holder_end_z`, `shaft_journal_end_z`, `back_holder_end_z` (A_H at
`-back_holder_end_z`), ...).  Role `mechanics` = drives the mechanical parts, CAD only (no simulation key); it is live now
(`LIVE_ROLES`).  Fusion user parameters that no motor feature uses are role `info`.  Values from the simulation where it
has them: `motor_length` (chain), rotor bore (`shaft_seat_r = rotor_down_r - rotor_house_height` = `rotor_inner_radius`),
`shaft_height` (CIANO14 40 since 2026-10-16: the bore is the Fusion `shaft_bore_r` = 3 mm, Ø6, whatever the as-built seat; it equals the simulation `shaft_inner_radius` = 5 - 2), the bearing card of the config
(`bearings.A.card` -> `bearings_library.yaml` d / D / B -> `bearing_d/D/B`; standard 618/8 8x16x4 if the store has no
card, stated in `pull_report.json`), the shaft material (`materials.shaft`).

Chamfers / fillets that Fusion applies to edges are sketch-based pockets here (no topological edge references: they survive
every Params edit).  Every mechanical sketch is fully constrained (0 DOF, no redundant / partially redundant constraint);
symmetric profiles are one dimensioned half + `Symmetric` (+ `Equal` for arcs / mirrored lines).  The health gate checks
the bodies, the array counts (`Screws` = `holder_bolt_count`, `Balls` = `bearing_ball_count`), the links and the extents
the Params demand (stale geometry), and `health.fit()` writes the interference / contact table (`build_report.json`
`fit`): contacts that must touch without overlap (shaft / rotor bore, shaft / bearing bores, bearings / holder seats,
holders and rings / stator, Prop_fix / shaft), clearances (holders, rings, screws vs coils / core / rotor) and the screw
thread engagement in the back bosses (reported).  An overlap above 0.5 mm3 refuses the build.

The STEP has `Mechanics/...` (36 solids, 58 in all) and the datums JSON a `mechanics` block (A_H measured on the solid,
bearing seats, holder faces, shaft journals / flats / thread, bolt circle and angles, M3 holes, rings, Prop_fix, screws).

Axial parameters (Fusion Sketch3 dimension names, not renamed; owner to confirm the meaning):

| Param (Fusion dim) | What it is | L20 |
|---|---|---|
| `motor_length` (d7582) | stack, stator end faces at +-motor_length/2 | 20 |
| `ring_h` (d11985), `ring_holder_gap` (d11987) | GFRP ring length from the stator face; gap ring -> holder flange | 1.8, 0.1 |
| `holder_face_z` (derived) | holder inner faces = bearing inner faces = shaft shoulder (front) / 10 -> 8 step (back) | 11.9 |
| `holder_flange_t` (d11698) | holder flange thickness (screw head seat front, boss start back) | 1.2 |
| `bearing_w` (d11703, = card B) | bearing width = seat depth | 4 |
| `holder_lip_t` (d11700), `holder_lip_r` (d11702) | lip behind each bearing, bore 14.6 | 0.8, 7.3 |
| `shaft_front_overhang` (d11704) | journal beyond the front holder -> Prop_fix seat (z 20.7) | 4 |
| `shaft_thread_len`, `shaft_tip_len` (d11705/6) | section next to the journal and threaded end; both Ø4.901 since 2026-10-16 (one tip diameter, `shaft_thread_r` = `shaft_tip_r` = 2.4505) | 8, 8 |
| `back_holder_len` (d11729) | back holder cylinder; A_H = -(holder_face_z + holder_flange_t + this) | 7 |
| `back_holder_ref_r` (d11730), `back_holder_wall` (d11731, driven) | outer r = ref + (ref - seat r 8) = 12 | 10, 2 |
| `holder_hub_wall` (d11752) | front hub r 8 -> 8.8 (also the spoke-root circle) | 0.8 |
| `shaft_shoulder_len`, `shaft_shoulder_r` (d11689/95) | 9.4 shoulder against the front inner ring | 0.5, 4.7 |
| `shaft_flange_step` (d11885) | flange r = rotor seat + 2 (front rotor stop, z 10..11.4; Ø13.9 at the as-built seat Ø9.9; owner 2026-10-16: 3 -> 2, balancing adds material on the rotor) | 2 |
| `shaft_bore_r` (d11692, 3 mm; was seat r - shaft_height = 2.95 as built), `shaft_bore_tip_angle` (d11685) | bore 6, 45 deg end cone, apex at the journal end | 3, 45 deg |
| `coil_holder_clearance` (d11980), `ring_t` (d11983) | flange r = coil corner 13.233 + 0.4; ring radial thickness | 0.4, 0.8 |
| `ax_core_ref_inset` (d9633) | construction line only (role info) | 0.9 |

Not dimensioned in Fusion (Sketch2 is not fully constrained), read as 14.843191 / 1.460021 and rounded by the owner
2026-10-13 (Fusion not dimensioned): `holder_bolt_r` 14.85, `holder_window_r` 1.46.  Fusion's Chamfer7 at the journal end chamfers one of the two arcs left by the flats (edge picked after
Extrude15); the template chamfers both.  Screw length: `holder_screw_req = 2 holder_face_z + holder_flange_t` (L20 25, L12 17), `holder_screw_l` = `holder_screw_req` rounded UP to an even mm, `2 * ceil(holder_screw_req / 2 mm - 1e-6) * 1 mm` (M2 and M2.5 alike; **owner 2026-10-19**; template A.7.5).  The tip must stay `screw_tip_margin`
(0.2 mm, as the L12 M2x18 has) inside the back flange outer face: `holder_screw_max = 2 holder_flange_z [+ holder_rib_h on the 85] - screw_tip_margin`
(template A.6.1, 2026-10-15 introduced the tip limit; until A.7.4 the length was the LONGEST stock length, even mm up to 20, then 22, 25, 30, that fits under it).  L12 M2x18, 85 L13 M2.5x20, 85 L15 M2.5x22 and CIANO14 50 L15 M2x20 (clamp 19.9) are unchanged; **CIANO14 40 L20 is now M2x26** (clamp 25, was M2x25), L12 with a 16 mm
stack M2x22.  When the rounded length exceeds the tip limit the pre-check refuses in words (e.g. a 40 mm die with `screw_tip_margin` 0.5 mm and a 22 mm stack: needs 27 -> 28, the tip limit is 27.7).  The thread engagement stays REPORTED against 1.5 x bolt_d (L20's 2.9 mm is below it as built).  The BOM row names the chosen
length and checks it against the stock list (22 and 26 are catalogue sizes).  `fit()` writes
`screw_check` to `build_report.json` (tip z, margin to the back flange outer face - the tip must stay inside the flange, else
the build is refused - and the thread engagement in the back holder, reported against 1.5 x bolt_d).
Coil straight length (owner 2026-10-13): `coil_straight_len = motor_length + 1 mm` (L12 13, L20 21; each stack length has its
own coil), `coil_leg_length = coil_straight_len`, `coil_straight_extra = coil_straight_len - motor_length`.

Verification of the mechanics (FreeCAD 1.1.3 server builds; the same numbers on 1.0.2):

| Part vs Fusion (`mechanics_vs_reference.py`, reference profile) | dV | max surface deviation |
|---|---|---|
| Stator_holder, Stator_holder_back, Ring x2, Prop_fix, 618/8 rings x4 | <= 1e-6 | <= 1e-5 mm |
| Shaft | -0.010 % | 0.141 mm (Fusion chamfers one arc of the journal end only; M5 cosmetic thread 4.901 vs 4.90) |
| 618/8 balls x2 | -0.013 % | 0.88 mm (Fusion joins the balls by r 0.01 tori into one body) |
| Screws x6 | +0.18 % | 0.18 mm (no 0.2 mm socket countersink) |

44/44 sketches fully constrained, no redundant constraints; 58 STEP solids (re-import volume equal); models open ready to
look at.  A_H measured on the back holder: z -20.1 (L20) / -16.1 (L12 = `Z_AH` of the full-stack study R42), area
118.532 mm2 (= Fusion).  Fit table (L12, L20): every contact touches without overlap; holders-coils 0.89 mm, screws-core
0.33 mm, rotor-holders 1.83 mm; screw thread engagement 2.9 mm; inherited from Fusion: the front ring overlaps the coil
end-turn bridges by 0.0056 mm3 (ring outer r = coil corner r).  L12 against `CIANO14_40_system_R42_no_motor_nominal.step`:
Stator_holder_back touches HS_PLATE on A_H, no mechanical overlap.  Owner edits (`owner_edits.py`, L12 and L20):
`motor_length` 16 / 24, `shaft_height` 1.5, `rotor_house_height` 1.3, `bearing_B` 5, `holder_bolt_r` 15, `bolt_head_d` 4.2,
`ring_h` 2 + `ring_holder_gap` 0.2 rebuild and pass the gate and the fit; `holder_bolt_r` 18 and `shaft_height` 0.5 are
refused in words.  L12 with `motor_length` 24 builds (re-tested 2026-10-13 on the A.4.0 image and on the stock-screw image, with
the old and the new coil straight rule: the old "13 solids" refusal no longer reproduces).  With the stock-rounded screw
(2026-10-13) L12 -> 16 was refused (the required 21 mm rounded up to M2 x 25, whose tip broke through the back flange); since A.6.1 the screw
follows the stack and L12 -> 16 builds with M2 x 22 (17 -> 18, 25 -> 25, 29 -> 30 unchanged); a stack with no stock length between the clamp length and the
tip limit (40 mm die: 22 mm) is still refused in words.

Updating the owner repo (`CIANO14_40_motor_model`): motor_ai_sim's `cad/` is the one source.  `tools/update_from_motor_ai_sim.py`
there copies `cad/motor_templates/segmented_flatwire/`, `cad/reference/` and the fc scripts from a motor_ai_sim checkout
(path argument, default `../motor_ai_sim`), records the source commit in `motor_templates/SOURCE.json`, and never touches
the owner's models or `params/`; the models in `models/` are refreshed from the server build (`Swap_in_new_models.cmd` if
FreeCAD has them open).

## CIANO14 50 (die "CIANO14 50 edited", config L15; template A.7.5, owner 2026-10-19)

The 50 mm motor has no Fusion model; its reference profile `cad/reference/CIANO14_50_edited.json` (found by `store.reference_for(die)`) is the CIANO14 40
reference + the rows of `cad/reference/designs/CIANO14_50_edited_design.json` (bearing 61901-2Z 12x24x6, shaft tip M6, holder flange 1.8, spokes 6, back holder
window of the board, M2x20 screws, ...: every row has its reason), made by `python cad/reference/make_die_reference.py designs/CIANO14_50_edited_design.json CIANO14_50_edited.json`
(rerun after any change of the 40 reference).  The simulation values are laid on top by `make_profile`; `deviations` (rotor_fill_r 0.2 mm, not built by the template) are applied after them and
shown as "DEVIATION, not modelled" in `pull_report.json`.  The bearing comes from the card named in `L15.yaml` (`bearings_library.yaml` 61901-2Z).

**Phase board (replaceable file).**  `profile.phase_board` = `{step, pins, copper_volume}`: the board STEP is read from `cad/reference/phase_board/<step>` (in the CAD image
`/app/cad/reference/phase_board/CIANO14_50_phase_board_revC_colour.step`; `$FC_PHASE_BOARD_STEP` overrides).  A revised board is a drop-in file of the same name followed by an image rebuild; the gate
checks the N-side copper layer against the coil lead end (`phase_board_z` chain), `pins: false` = no pin / J13 checks (CIANO50 board: phase holes, the 12 AWG leads are soldered in), `copper_volume` = the wide
volume range [80, 260] mm3 in which a full-board copper layer is searched.  Only a board STEP goes to the server: no vendor 3-D models.

The GUI finish shows the Tip of the array bases `Screw` / `Bearing_ball`, so the link arrays Screws / Balls are visible (all models).

## How the owner edits

1. Open `<motor_id>.FCStd` (copy it first; FreeCAD 1.1 GUI: `C:\Program Files\FreeCAD 1.1\bin\FreeCAD.exe`).
2. Edit rows with role **input** in the `Params` sheet (like Fusion user parameters), Recompute, run the
   `CheckMotorParams` macro (says in words if the template cannot build the values).
3. Sync-back proposal: `python -m motor_ai_sim.cad.build back --die "CIANO14 40 new" --config L12 --fcstd COPY.FCStd
   --dies-dir <store snapshot> --propose --out-dir sync` writes the differences against the store, a Fusion Parameter I/O
   CSV and a `.patch.yaml`. Nothing is written to the store.
4. The owner confirms the proposal; the orchestrator applies it to `die.yaml` (affects every config of the die) or the
   config's `geometry_overrides`.
5. Server rebuild (above). The new manifest carries the new `param_hash`.

## CAD-only assembly clearances (owner 2026-10-14)

The 3-D model carries small offsets around the magnets and at the fits: `offset_rotor` (the magnet pocket offset; the pocket
fillet is `magnet_fill_radius + offset_rotor`), `offset_core`, `offset_tooth`, `offset_bearing`, `offset_radiator`,
`offset_holder`. They exist so that a magnet and the other parts can surely be inserted at our manufacturing tolerances.
**The simulation deliberately does not include them**; they make no practical difference to the results.

* `motor_ai_sim.cad.store.CAD_ONLY_CLEARANCES` lists them. In the review report of `make_profile` and of `back` they appear
  under `assembly_clearances`, classified "CAD-only assembly clearance (not simulated by design)".
* They are never a difference against the store, never in `cad_only_inputs` or `mechanics`, never in a proposal
  (`.patch.yaml`, Fusion CSV), and `primary_rows` never offers them as a simulation row.
* Masses (`masses.compute_masses`, the component card) use the nominal magnet; the clearance is air.
* Test: `tests/test_cad_generator.py::test_assembly_clearances_are_cad_only_never_a_difference_or_a_proposal`.
  Private rule: `motor-ai-sim-private/docs/DESIGN_RULES.md` F11.

## Sketch conventions (owner rules 2026-10-12)

* **Half plus mirror.** Core sector, rotor pole and magnet sketches dimension only the right half, against the symmetry axis
  (half angles from the axis and half widths from the axis, as in the Fusion rotor sketch). The left half carries no
  dimensions: one `Symmetric` constraint (about the sketch Y axis) per node and `Equal` radius per arc
  (`SK.mirror_chain`). The coil has one leg per sketch (the legs are separate features so the body stays one solid after every
  step), so its dimensions are one-sided by construction. No constraint name ends in `_L` / `_R` (a unit test checks).
* **One radius per Fusion radius.** `stator_fillet_r1` (tooth-tip fillet) is the only dimension of the four small core fillets
  (foot fillets and the mirrored ones are `Equal`); `stator_fillet_r` is the yoke fillet; `magnet_fill_radius` is the only
  dimension of all six magnet fillets. The rotor pocket fillet (`magnet_fill_radius + offset_rotor`) and the notch (driven by
  tangency, as in Fusion) are different radii and stay separate.
* **Named derived rows for counts and computed lengths** (role `derived`, comment in the Params sheet): `core_sectors`
  (= num_seg x 3), `coil_count`, `rotor_poles` (= num_seg x num_poles_per_segment), `magnet_count`, `coil_layer_copies`,
  `coil_layer_span`, `coil_layer_pitch`, `coil_leg_length`, `coil_x_in`, `coil_top_y`, `coil_bridge_length`, `coil_lead_total`.
  Features reference them (`Params.core_sectors`), never an inline formula.

Renamed sketch constraints (old -> new):

| Sketch | Old | New |
|---|---|---|
| Core sector | `tooth_half_width_R/L`, `slot_top_y_R/L`, `coil_w_R/L`, `cut_width_R/L`, `tooth2_width_R/L` | `tooth_half_width`, `slot_top_y`, `coil_w`, `cut_width`, `tooth2_width` |
| | `tooth_tip_fillet_R/L`, `foot_fillet_R/L` | `stator_fillet_r1` (the other three are `Equal`) |
| | `stator_fillet_r_R/L`, `foot_r_R/L` | `stator_fillet_r`, `foot_r` |
| | `sector_boundary_angle_R/L` (angle from the horizontal) | `sector_half_angle` (boundary to the sector axis = `angle_wire`) |
| Rotor pole | `rotor_down_r_R/L`, `rotor_up_r_U_R/L`, `rotor_up_r_R/L` | `rotor_down_r`, `rotor_up_r_U`, `rotor_up_r` |
| | `magnet_fill_down_angle_R/L`, `magnet_fill_up_angle_R/L` | `magnet_fill_down_angle`, `magnet_fill_up_angle` (half angles from the pole axis) |
| | `half_pole_angle_R/L`, `lip_x_R/L` | `half_pole_angle`, `lip_x` |
| | `magnet_down_height_R/L`, `offset_rotor_lo/up_R/L`, `pocket_fillet_r_R/L`, `fillet_centre_r_R/L` | same names without the suffix |
| Magnet | `rotor_down_r_R/L`, `rotor_up_r_R/L`, `magnet_fill_*_angle_R/L`, `magnet_side_angle_R/L`, `magnet_down_height_R/L` | `rotor_down_r`, `rotor_up_r`, `magnet_fill_down_angle`, `magnet_fill_up_angle`, `half_pole_angle`, `magnet_down_height` |
| | `magnet_fillet_1/2/3_R/L` | `magnet_fill_radius` (the other five are `Equal`) |
| Coil (one leg per sketch) | `wire_x_in_R/L`, `wire_width_R/L`, `wire_height_R/L`, `layer_top_y_R/L`, `bend_axis_x_R/L` | same names without the suffix |

## Verification (2026-10-12)

FreeCAD 1.1.3 in the server image vs the FreeCAD 1.0.2 builds of the old repo (old sketches, `CIANO14_40_motor_model`
models and STEP). Compared per solid with `python -m motor_ai_sim.cad.compare` (22 solids each): largest differences

| | volume (relative) | bounding box (mm) | centre of mass (mm) |
|---|---|---|---|
| L12 FCStd, server vs local | 1e-15 | 7e-14 | 8e-15 |
| L12 STEP | 5e-14 | 1e-12 | 5e-13 |
| L20 FCStd | 2e-15 | 7e-14 | 1e-14 |
| L20 STEP | 4e-14 | 1e-12 | 5e-13 |
| Fusion reference profile build (`CIANO14_40_12`) vs local reference model | 2e-15 | 7e-14 | 1e-14 |

Coil vs Fusion (`Coil_L20_fusion.step`): volume +0.0644 % (641.8743 vs 641.4613 mm3), max surface deviation 0.0266 mm,
the same numbers as the local 1.0.2 run. Every sketch is at 0 DOF with no redundant or conflicting constraints
(`Sketch.solve() == 0`, `FullyConstrained`; the health gate refuses otherwise), the STEP re-import check passes
(22 solids, volume = model volume), and the model opens ready to look at (`gui_check.json`).
Typical Params edits rebuild (tooth_width, stator_fillet_r / stator_fillet_r1, magnet_fill_radius, motor_length,
magnet_height, air_gap, magnet_fill_up) and one change of `stator_fillet_r1` / `magnet_fill_radius` moves every fillet of that
radius. Unit tests: `tests/test_cad_generator.py` (12) pass in the deployed API image.

## What stage 3 / 4 need

* Stage 3 (BOM): read `manifest.json` + `<motor_id>_datums.json` + `build_report.json` volumes; materials are in
  `params.json` (`profile.materials`, card data in `profile.material_cards`).
* Stage 4 (STEP download): done, see "3D model menu" below; the manifest's `param_hash` is the cache key.
* Rebuild trigger: compare `geo_sig_hash` / `param_hash` of the store with `manifest.json`; rebuild on mismatch.

## Bill of materials (roadmap stage 3)

Written next to the STEP by every store build: `<motor_id>_BOM.csv` (always) and `<motor_id>_BOM.xlsx` (when openpyxl is installed),
plus the evidence `<motor_id>_bom_model_dump.json`; the manifest has a `bom` block (line count, masses, flags) and lists the files with their
SHA-256.  Skip with `--no-bom`.  A BOM failure never refuses a build.

| Piece | What |
|---|---|
| `cad/fc_scripts/bom_dump.py` | FreeCAD script: reads the model AS SAVED (no recompute) and dumps every body, placed copy, array and imported part with its volume, bounding box and solid volumes, plus the Params sheet. Generic: parts added to the template later appear without editing the script. |
| `src/motor_ai_sim/cad/bom.py` | the BOM (pure data, stdlib; openpyxl only for the xlsx): lamination sheets from stack length x stacking factor / sheet thickness (`round(L x k_f / t)`, k_f and density from the lamination card), magnets, coils with conductor length per coil and total, wire stock item, shaft, holders, rings, Prop_fix, bearings, M2 screws with a stock-length check (ISO 4762 lengths), phase board, pins, and the consumables the model or the simulation names (otherwise "to be specified"). Masses = model volume x density; the comparison with the simulation `mass_components` of the config's last saved duty explains the difference. Objects no rule knows are listed as UNCLASSIFIED, never dropped. |
| `src/motor_ai_sim/cad/bom_store.py` | assembles the inputs from the store files the generator already reads (effective machine, materials library, bearings library, `wire_stock.yaml`) and writes the files. |

The BOM carries no commercial data; callers can add columns / sheets through `write_csv(extra_columns=...)` and `write_xlsx(extra_sheets=...)`.
Tests: `tests/test_cad_bom.py` (dumps of the server-built L12 / L20 models in `tests/fixtures/`).


## Electronics: the phase (coil-connection) board (2026-10-13)

App::Part `Electronics` -> `PhaseBoard` (Part::Feature): the coloured STEP of the frozen PCB release rev C
(`cad/reference/phase_board/CIANO14_40_phase_board_revC_colour.step`, README next to it; 110 solids: FR4, four 6 oz copper layers,
mask, 3 phase pins CIANO40-PIN-C rev 7, 6 notch walls).  Imported once at build time, NOT parametric (the shape is stored in the
model, the STEP is not needed to open it); the **placement** is parametric, a pure translation:

| Params row | Role | Value |
|---|---|---|
| `phase_board_n_offset` | mechanics | 2.525 mm: N-side copper surface above the coil lead end datum (the board grows toward the motor, N stays fixed) |
| `phase_board_n_z0` | mechanics | -0.21 mm: Z of the N copper in the STEP frame (property of the STEP) |
| `phase_board_x`, `phase_board_y` | mechanics | -100, +100 mm: KiCad absolute board centre (100, -100) -> motor axis |
| `phase_board_z` | derived | `-(coil_leg_length / 2 + coil_lead_total) + phase_board_n_offset - phase_board_n_z0`: L12 -12.615, L20 -16.615 |

`Electronics.Placement.Base.x/y/z` are expressions of these rows, so the board follows `motor_length`.  Gate: placement = rows,
N copper measured on the solid = lead end + offset, pin J13 at (6.606, 12.964) +-0.01 (r 14.55 at 63 deg).  The STEP colours
(mask green, FR4, ENIG gold, pins / walls grey) are looked up per face through the GUI importer and stored as the `DiffuseColor` of the
view object (GuiDocument.xml); `check_gui.py` verifies them.  `build_report.json` -> `phase_board`: minimum clearance and overlap
of the board (and of its pins alone) against Coils, Core, RotorCore, Magnets, Shaft, both holders, Rings, Bearings, Screws, Prop_fix, and
for each of the 12 coil lead ends the overlap / clearance of the lead stub against the board (reported, not refused: the pin tail is
provisional); the manifest carries a summary.  The datums JSON has a `phase_board` block.  The active-part STEP is unchanged (58 solids).

## Screws and threads (2026-10-13)

The 6 M2 socket head screws were always in `Mechanics/Screws` and visible (hex socket AF 1.5, depth 1, conical bottom; only the
0.2 mm mouth countersink of Fusion is not modelled); they were dark grey (0.30) and small, now nickel-plated (`screw` in `palette.py`).  Fusion's
threads, read from the Fusion model (`fusion_source/sketches/*_features_bodies.json`, 2026-10-12): `Thread9` M5x0.8 6g on the
shaft, `Thread12` M3x0.5 6H and `Thread14` M2x0.4 6H in the back holder are all **cosmetic** (`modeled = False`, full length), and
the screw component is `91292A032_NO THREADS`.  They are reproduced the same way: the geometry stays the core / tap diameter
(shaft r 2.4505, tap holes), the thread faces carry the host colour darkened by `THREAD_DARKEN` in `palette.py` (checked by `check_gui.py`)
and the datums JSON lists them (`mechanics.threads`).  Volumes and the 1.0.2 / 1.1 compatibility are unchanged (no geometry change).

### Thread texture in the web viewer (owner 2026-10-15)

The CAD model and the STEP keep every thread **smooth** (a plain cylinder, light STEP); the thread size lives as data and the web viewer
draws the relief as a texture, like Fusion's cosmetic thread.  `export_models.py` splits the threaded cylindrical face of a screw shank
(`Screw_n`, external), a tapped hole (`Stator_holder_back`, internal: the M2 / M2.5 screw taps and the six M3 holes) and the shaft thread
(`Shaft`, external M5; on the ring carrier the internal M3 hub holes) into its own glTF node `<solid>_thread<k>` in the same group (same
material, so the same colour) and gives the node `extras.thread`:

```json
{"thread": {"kind": "external", "d_mm": 2.0, "pitch_mm": 0.4, "length_mm": 24.6, "standard": "ISO 261", "designation": "M2x0.4",
            "radius_mm": 1.0, "axis": [0, 0, 1], "origin": [7.425, 12.86, -11.5], "hand": "right"}}
```

`d_mm` is the nominal size, found from the modelled cylinder (shank radius -> nearest ISO coarse size, tapping-hole radius -> the size whose
minor diameter D1 is nearest) and `pitch_mm` comes from the ISO 261 coarse table in `cad/fc_scripts/threads.py` (the template states no
pitch); `axis` is a unit vector (first non-zero component positive), `origin` the axis point where the threaded `length_mm` starts.  A
radius that matches no coarse size gets no record (a smooth face).  The BOM rows of the screws and of the back holder state the same thread
(`thread M2x0.4 (ISO 261)`).  Faces that are threaded over their whole length keep exactly the triangles they had: `geometry_sha256` (asset extras and `_models3d.json`) is a hash
over the sorted triangles per group; `FC_THREADS=0` writes the file without the thread nodes for comparison.

**Where the thread is (fix 2026-10-15, owner: "the thread must be at the end").** A thread node is exactly the threaded axial band, and
`extras.thread` states it (`origin` + `length_mm` along `axis`; for an axis along Z also `z_range_mm`, the threaded z interval):

| Part | Threaded band |
|---|---|
| Screw shank | the ISO 4762 / DIN 912 thread length b = 2 d + 12 mm (M2 16, M2.5 17, M3 18; for lengths up to 125 mm) measured from the screw TIP, clipped to the shank face, never under the head; a screw whose plain part under the head would be shorter than 2 pitches (length <= b + 2 P) is fully threaded.  CIANO14 40 L20 (M2 x 25): z -11.5 .. 4.1, 9 mm plain under the head. |
| Tapped hole (back holder, ring-carrier hub holes) | the whole hole face = the tapped depth the template models |
| Shaft | the free END: the `shaft_tip_r` section (z `shaft_thread_end_z` .. `shaft_front_end_z`, CIANO14 40: 28.7 .. 36.7), the part the propeller nut runs on.  The `shaft_thread_r` section next to the journal (z 20.7 .. 28.7) passes through the propeller plate's clearance hole and is NOT threaded.  Since 2026-10-16 the CIANO14 40 has ONE tip diameter (Ø4.901 = `shaft_thread_r` = `shaft_tip_r`): `build_shaft` drops the zero-length step and the two sections are one cylinder; the GLB thread rule is a z band (`zband`: `shaft_thread_end_z` .. `shaft_front_end_z`), so the texture stays at the end.  The FCStd GUI colouring is per face and colours the whole Ø4.901 cylinder.  The GUI cosmetic colouring (`view.thread_face_indices`, checked by `check_gui.py`) and the `thread` block of the datums JSON (`neck` = the smooth section) follow the same face. |

The rest of a cut face stays smooth in the solid's own node: the shank cylinder is cut at the end of the thread by `glb_writer.split_by_slab`
(winding kept, cut edges share one new vertex, no area or volume lost), so the geometry is the original plus the vertices of the cut.

The viewer (`web/src/lib/threadTexture.ts`, `components/model3d/modelViewer.ts`) gives each such mesh cylindrical texture coordinates
(u = angle round the axis, v = axial distance / pitch, so the pitch is exact) and ONE shared procedurally generated normal map (128 x 128,
mipmapped, nothing loaded from outside): a right-hand ISO profile as the diagonal pattern h(v - u); `normalScale` corrects the helix
angle to the radius.  Colour, metalness and roughness stay those of the file; a GLB without the extras renders as before.  In the NDA view
the pointer over a thread shows its size ("M2x0.4, ISO 261").  Old GLBs get thread nodes after the next CAD rebuild of the motor.

## 3D model menu: viewer model, access rule, download log (2026-10-13)

One model for everybody (owner decision 2026-10-13: "look and rotate for all, download under NDA"): every signed-in user who may see a
motor can view, rotate, section, explode and measure the full model, copper board included; the STEP download is for accounts flagged
"NDA signed" and the owner / admin only.  (Earlier drafts had a simplified pre-NDA "envelope" variant; it is gone, nothing builds or serves it.)

`cad/fc_scripts/export_models.py` runs after the STEP export of every store build (skip with `--no-models`) and writes, next to the STEP,
two more files that the manifest lists with SHA-256 (`models3d` block: triangle count, tessellation, worst mesh-vs-solid volume error):

| File | What |
|---|---|
| `<motor_id>_full.glb` | the model for the web viewer: one coloured node per part (`Core`, `Coils/Coil_nn`, `Rotor`, `Magnets/Magnet_nn`, `Shaft`, `Bearings_front/back`, `Holder_front/back`, `Ring_front/back`, `Screws/Screw_n`, `Prop_fix`, `PhaseBoard_FR4/copper/other/pins`); node `extras` carry `family` (the viewer's toggle) and `explode` (axial factor of the exploded view, in motor lengths). Colours = `view.COLORS`. |
| `<motor_id>_models3d.json` | evidence of the export (per-solid mesh volume check) |

`glb_writer.py` (pure Python, no numpy) writes the GLB: millimetres, +Z = motor axis, smooth normals inside a face and hard edges between
faces; per-solid triangle budgets keep balls, screws, pins and board copper small (L12: 4.6 MB, 135 k triangles).  The export refuses the build
if a mesh differs from its solid by more than 3 % in volume (measured 0.4 %) or if the model has a board without copper.

Routes (`routes/model3d.py`, `model3d.py`), all authenticated, motor access = `motor_access` (granted die or per-die visibility; an ungranted or
unbuilt motor is a 404):

| Route | |
|---|---|
| `GET /api/model3d/models` | the built models of the caller's motors and `can_download` (no BOM, no prices) |
| `GET /api/model3d/<die>/<config>/model.glb` | the GLB, for every caller who may see the motor |
| `GET /api/model3d/<die>/<config>/model.step` | `<motor_id>_active.step`, 403 without NDA; header and file name stamped with the customer name and the date; one line in the download log |
| `GET /api/model3d/downloads` | admin: the download log, newest first |

The download rule is `model3d.can_download`, the only place: the owner / admin and accounts with `nda.signed`.  The NDA flag is a field of the
user record (`nda: {signed, date, by}`), set in Admin -> Users -> user drawer (switch + date; `PATCH /api/auth/users/<email>` with `nda_signed`,
`nda_date`; audited as `user.nda`).  The web tab shows the Download button to those accounts and the note "Download after NDA" (with a tooltip) to the rest.

STEP stamp: the HEADER section is rewritten (description, file name, author = customer, organisation = MOTRES d.o.o., a comment line with the
template version and build date), the DATA section is copied byte for byte; file name `<motor_id>_<customer-slug>_<YYYYMMDD>.step`.
Download log: `cad_downloads.jsonl` beside `users.json` (0600): user, name, time, file, motor, die/config, template version, build time,
parameter hash, SHA-256 of the source file and of the stamped file, IP.  A download whose line cannot be written is refused (503).

Deploying: the CAD image must be rebuilt from this commit (`export_models.py`, `glb_writer.py` are under `cad/`), then rebuild the configs
(`docker run ... motres-cad:<tag> build --die ... --config ...`, same command as above; a rebuild replaces the whole output directory, so old
`<motor_id>_envelope.*` files disappear); the API only reads `<shared>/cad`.

## Ring-carrier variant and the CIANO28 85 (template A.5.0, 2026-10-14)

The CIANO28 85 20SW1200 / L13 is built by the same template as the CIANO14 40; what differs is selected by profile rows (a profile
without them builds exactly as before; the CIANO14 40 volumes were checked bit-identical against A.4.0).

| Row(s) | What it switches |
|---|---|
| `rotor_key_count`, `rotor_key_w/h/offset`, `rotor_fill_r` | rotor keys in the bore (Fusion Extrude5 / Fillet7 / OffsetFaces6 / C-Pattern5): one sketch, Pad, PolarPattern |
| `magnet_lower_fill_r` | magnet bottom / knee fillets differ from the top fillets (`magnet_fill_radius`) |
| `carrier_*` (e.g. `carrier_bore_r`) | mechanics variant `ring_carrier` (`mechanics_carrier.py`): front `Shaft` (hub, 4 M3 holes) + `Shaft_rear`, rim key tabs and slots, no Prop_fix; gate / fit / precheck / datums / BOM have carrier branches |
| `bearing_seal_*` | 2RS1 bearing: seal notch + lip groove + cone in the outer ring (revolved cuts) and two seals (volume-equivalent L section) |
| `holder_spoke_angle`, `holder_window_rot`, `holder_rib_*`, `holder_bolt_count` | holders with 6 spokes on 12 sectors, spoke ribs |
| `board_*` | parametric phase board: ring plate + 24 lead slots + 24 lead sleeves (`electronics.build_board_plate`), follows the coil leads |

Per-die Fusion reference: `cad/reference/<die slug>.json` (+ `<slug>_fusion_values.json`) is used instead of the CIANO14 40 reference
(`store.reference_for`); its bearing rows stay when the store has no bearing card for the die.

Volumes vs the Fusion bodies (Fusion-values profile): Core 0.000 %, RotorCore +0.006 %, Magnet 0.000 %, Coil +0.345 %, Shaft +0.005 %,
Shaft_rear +0.007 %, Stator_holder +0.005 %, Stator_holder_back +0.241 %, Ring 0.000 %, bearing outer / inner ring +0.006 % / 0.000 %, seal
+0.030 %, balls -0.021 %, Screw +0.004 % (CIANO14 repo `validation/ciano28_85_13/`).  Not modelled: the Fusion slot-edge fillet of the
carrier (r 0.15), the cone stack of the bearing seal, copper layers of the 85 board (envelope only).

## CIANO28 150_35 (template A.7.3, 2026-10-16)

The 150 mm motor CIANO28 150_35 new / L35 is built by the same template. Reference profile: `cad/reference/CIANO28_150_35_new.json` (+ `_fusion_values.json`),
read-only from the open Fusion design "CIANO28 150" on 2026-10-16 (dump and tools in the private model repo, `fusion_source/CIANO28_150_35/`,
`tools/build_params_150.py`, `tools/build_mech_150.py`). Every row carries its Fusion source; formulas are their own source.

What the template gained for it (existing profiles build the same geometry as A.7.2; checked 2026-10-16 on CIANO14 40 L12 / L20 and CIANO28 85 L13 / L15:
volumes to 1e-6, bounding boxes and solid counts identical):

* **Axial magnet slices** (`mag_step`, `magnet_slices` = motor_length / mag_step; Fusion R-Pattern1): the Draft array `MagnetStack` (z) sits inside `MagnetArray`
  (polar): 7 x 28 = 196 solids. `magnet_lamination` (10 mm) is the simulation's loss-model slice, not a CAD parameter (pre-check no longer ties it to the sleeve).
* **Bridge-aware coil transition** (`coil_bridge_length > 1`): the -z end turn is a loft through sections on a real centre line with the Fusion bend axes;
  the half-annulus path of the 40 / 85 is kept for `coil_bridge_length = 0`.
* **Occupancy mechanics** (row `occupancy_variant`, `mechanics_occupancy.py`): Rotor_holder1 / 2 (bodies Shaft / Shaft_rear), Stator_holder and Stator_holder_back
  are cast aluminium parts with 10-20 features each. Each is modelled as rings of the cylindrical grid of the Fusion body (rows `<part>_r0/_r1/_z0/_z1/_fill_<k>`,
  measured by `tools/fc_scripts/occupancy.py`): a full ring is an annulus pad, a partial ring one sector of angular width fill x 360 / n patterned n times.
  The volume, z stations, envelope and mass distribution of every part match Fusion (<= 0.02 %); pockets, ribs, holes and the real outline inside a ring do not.
  The simplified model keeps them as built.
* **Three screw families** (`screwA` 91290A137 x 12 on R72.2, `screwB` M3 x 30 front, `screwC` M3 x 6 back (head down)): the existing screw builder takes a row prefix.
* The 61811-2RZ bearing and its shields use the 85's bearing rows (new values), the phase board is the plate board (envelope; the Fusion PCB 150 layers are NOT
  modelled, its mass is in the BOM from the Fusion bodies).
* Gate / fit / STEP datums / GLB / BOM have an occupancy branch. Fit kind `approx`: the rings stand in for pocketed parts, so rotor keys, the bearing seat and the coil
  end turns overlap them a little; the overlap is reported and refused above 2.5 % of the smaller part. The screws are not tested against the holders (their holes are not modelled).

Not modelled (disclosed in the BOM / report): fan plates (Fan_200_24s), the 72 alumina insulation bodies (Insulation_ceramic), (until A.7.6: pockets / ribs / holes of the four
holders - since A.7.6 they are the Fusion bodies), the bearing shield detail, slot-edge fillets, screw threads and head knurling, the PCB layers. The Fusion component "M3 x 0.5 x 30" has a 34 mm shank (the
BOM flags the length for confirmation).

## CIANO28 150_35: the holders are the real Fusion bodies, no gaps (template A.7.6, 2026-10-19)

Owner 2026-10-19 (looking at the server build): "gaps everywhere" - the Stator_holder flange was a set of arc segments with gaps, the back holder
had 12 big windows and hairline radial slits, both rotor holders (Shaft, Shaft_rear) had radial hairline slits and notches at the windows.
**Cause**: the volume-equivalent occupancy rings of A.7.3. A ring with `fill` < 1 is ONE sector of angular width fill x 360 / n patterned n times,
so every partial ring is a set of sectors with gaps between them (fill 0.9655 -> a 1.1 deg slit; fill 0.0246 -> ribs; the 12 windows of the front
holder are the empty angle of the fill 0.39 / 0.29 / 0.22 rings). None of it is in the Fusion design: its holders are lightened lattices of closed
pockets (the web between the pockets is continuous, the rim is a closed ring with 12 screw holes) and the rotor holders have only round holes and
the 12 magnet-sector windows.

**Fix**: when the profile has `profile.fusion_parts` the four aluminium parts are the Fusion bodies (read-only STEP export of the open design,
`fusion_source/CIANO28_150_35/mechanics/{Rotor_holder1,Rotor_holder2,Stator_holder,Stator_holder_back}.step`, dump 2026-10-16): imported once (the
biggest solid of each STEP; the Stator_holder STEP also carries its 12 screws), set as the BaseFeature of the same bodies `Shaft`, `Shaft_rear`,
`Stator_holder`, `Stator_holder_back`, so names, solids, gate, fit, BOM, STEP tree and GLB are unchanged. Nothing else moves: volumes are the Fusion
volumes (Shaft 48825.9, Shaft_rear 27786.9, Stator_holder 46239.5, Stator_holder_back 79820.2 mm3), the z extents are the old ring extents.
Only the axial position follows `motor_length` (a shift of +-(motor_length - 35) / 2 at build time); the shape is NOT parametric.
Fusion's own B-rep trips FreeCAD's BRepCheck on 12 pocket faces (outer wire "self-intersecting") and 2 large planar faces ("intersecting wires") of each
stator holder: tolerance-level wire errors, the volume matches Fusion to 5e-6, booleans, meshing and STEP export work.  Repairing the faces made the
shape valid in memory but not after a copy (the simplified / STEP exporters copy), so the import keeps the STEP as it is and records the verdict
(`SourceValid`, `SourceCheck` on `<part>_Fusion`); the gate and the simplified exporter accept an invalid imported solid only when the STEP itself is
invalid, it has one closed shell and the Fusion volume.  A valid source (both rotor holders) must stay valid.

* STEP location: `$FC_FUSION_PARTS/<file>`, `$FC_CAD_REFERENCE/fusion_parts/CIANO28_150_35/<file>` (the private data repository, see "Private reference
  files"), `cad/reference/fusion_parts/CIANO28_150_35/<file>` or (owner repo) `fusion_source/CIANO28_150_35/mechanics/<file>`.
  The files are owner design data and this repository is public: they live ONLY in `eMotres/motor-ai-sim-private` `cad/reference/fusion_parts/CIANO28_150_35/`
  (`cad/reference/fusion_parts/` is also git-ignored here) and travel into the CAD image with the `private` build context. A missing STEP REFUSES the build;
  there is no silent fall-back to rings.
* The occupancy rows (`<part>_r0_k ...`) stay in the profile as the measured record, role `info` (not live, so the role check does not call them
  dead); `test_occupancy_rings_reproduce_the_fusion_body_volume` still ties them to the Fusion volumes. Profiles without `fusion_parts` still build rings.
* Gate: each part must be one valid solid, its volume must equal the imported solid and be within 0.1 % of `fusion_parts.volumes_mm3` (the Fusion body
  volume); the z extent invariant is kept.
* The lightening pockets and screw holes of the holders are Fusion design features and are now in the model (they are not gaps of the template).

## CIANO14 40: Shaft, Prop_fix and both stator holders are the Fusion v19 bodies (template A.7.7, 2026-10-19)

Owner 2026-10-19: the 3-D view of the 40 mm production module showed the OLD shaft design ("with the hole"): the Ø6 bore broke into the drive flats.
He fixed the shaft in Fusion (document `CIANO14 40`, version 19: the bore is 1.5 mm shallower) and ruled that **everything in production comes ONLY
from Fusion**. The template shaft differed from Fusion (bore bottom, flange Ø15.9 instead of Ø13.9 in the old server build, thread section Ø4.95
instead of Ø4.901) and so did the `Prop_fix` hole (4.99 vs 4.901).

**Mechanism**: the same `profile.fusion_parts` as the 150 (A.7.6), now in the standard mechanics too (`fusion_parts.py`, `mechanics.build_mechanics`).
The four parts are the read-only STEP exports of the Fusion components, imported once (biggest solid of the STEP) as the BaseFeature of the same
PartDesign bodies, so names, solids, gate, fit, BOM, STEP tree and GLB code are unchanged. Per part:

| part | at another `motor_length` |
|---|---|
| `Stator_holder`, `Prop_fix` | rigid shift +(motor_length - ref) / 2 (`fusion_parts.shift`) |
| `Stator_holder_back` | rigid shift -(motor_length - ref) / 2 |
| `Shaft` | **stretch** (`fusion_parts.stretch`): cut-and-shift in the constant Ø9.9 rotor-seat section at z = 0; the part behind moves -d/2, the part in front +d/2 and the extruded section fills the gap (a slab is removed for d < 0). Volume = Fusion volume + d x section area (48.70 mm2 per mm: Ø9.9 x Ø6 tube). This is the production drawing rule (`production/CIANO14_40/shaft_drawing.py`, note 10): L20 = L12 + 8 mm in the Ø9.9 section |

* **Reference length**: the STEP files were exported with the design at `motor_length` 12 (the open design was at 12 when read, 2026-10-19 11:58), so
  `ref_motor_length` = 12 and L12 is the Fusion body as it is; L20 is derived by the stretch. The derivation was checked against the earlier L20 dump
  of the same Fusion version (shaft 1917.559 mm3 = 1527.939 + 8 x 48.70).
* **Only the die it was drawn for**: `fusion_parts.dies` = `["CIANO14 40 new"]`. The CIANO14 40 reference is the fall-back of every die without its own
  reference; `store.restrict_fusion_parts` drops the Fusion bodies for any other die (they build the template parts).
* **Private files**: the STEP files (`Shaft.step`, `Prop_fix.step`, `Stator_holder.step`, `Stator_holder_back.step`) and `fusion_export_manifest.json` (document
  version, motor_length, volumes, bounding boxes) are owner design data and live ONLY in `eMotres/motor-ai-sim-private`
  `cad/reference/fusion_parts/CIANO14_40/` (folder `CIANO28_150_35/` holds the 150 files). Lookup order (`fusion_parts.find_fusion_part`):
  `$FC_FUSION_PARTS/<file>`, `$FC_FUSION_PARTS/<dir>/<file>`, `$FC_CAD_REFERENCE/fusion_parts/<dir>/<file>` (the builder sets `FC_CAD_REFERENCE` to the
  private `cad/reference`; in the CAD image it is `/app/cad/reference`, copied from the `private` build context), `cad/reference/fusion_parts/<dir>/<file>`.
  A missing file REFUSES the build; there is no fall-back to the template geometry.
* **Rows**: the 48 Params rows that only the replaced template geometry read (`shaft_*`, `prop_fix_*`, `holder_*`, `back_holder_*` ...) stay in the profile
  (`fusion_parts.replaced_rows`); `build_model.py` lists them in `roles_check.replaced_by_fusion` instead of refusing the build ("dead by design"). Editing them in
  the Params sheet changes nothing: change the part in Fusion and export it again.
* **Gate**: each part is one valid solid whose volume equals the imported solid and the Fusion volume (+ stretch) within 0.1 %; the z-extent invariants
  against the bearing / holder rows stay (they tie the Fusion bodies to the positions the rest of the template uses); the flange-radius and the holder
  feature-count invariants are the template's and are skipped for Fusion bodies. The fit table (contact / clearance pairs) is unchanged and decides.
* **Threads** stay cosmetic: the radii of the cylinders that carry them are measured on the Fusion part (`fusion_parts.thread_radii`: shaft tip 2.4505,
  back-holder taps 0.8115 / 1.2645) and read by `view.thread_face_indices` (colour) and `export_models.thread_rules` (glTF thread nodes).
* **No phase pins** on the board (owner rule) - unchanged: the template drops pins on an imported board.
* Rebuild: `MOTOR_AI_SIM_PRIVATE_DATA=<private checkout> python cad/run_build.py build --die "CIANO14 40 new" --config L12|L20` (the server runs the same
  inside the CAD image built with `--build-context private=...`).

## The GFRP rings: full model only (template A.7.4, 2026-10-16)

Template A.7.1 (#216, 2026-10-15) removed the glass-fibre `Ring` (body) and its `Ring_back` (link) from every construction. The owner only wanted
them out of the SIMPLIFIED model: in the FULL model the two rings fix the coils. A.7.4 restores everything A.7.1 removed for the full model
(`mechanics.py` build_ring + link, `health.py` MECH_BODIES / extents / fit pairs / the `ring_inner_r` pre-check, `electronics.py` "Rings" fit group,
`frameless.py` OMITTED_OBJECTS, `view.py` / `palette.*`, the STEP tree, datums `mechanics.rings`, viewer groups `Ring_front` / `Ring_back`, the BOM row
"Ring (glass-fibre)", the `GFRP` density, the profile rows `ring_t`, `ring_inner_r`, `ring_back_dz` and material `Ring`, the editable `ring_t`, the
part `rings` of the part tree / Materials Full / full mass / `ROTATING_KEYS`, the web names and `fusion_mechanics/Ring.*`).

* Rings exist only where the reference profile has the ring rows: CIANO14 40 (L12, L20) and CIANO28 85 (L13, L15). The CIANO28 150_35 port (occupancy
  variant) has no ring; `mechanics.solids()` lists `Ring_front` / `Ring_back` only when the `Ring` body exists.
* The SIMPLIFIED model omits them: `simplified.OMITTED_MECH = ("Ring_front", "Ring_back")`, applied by `export_simplified.py` to the STEP, the GLB
  (no `Ring_*` group) and the JSON (`solids.mechanics` without them, `omitted` lists them). The simplified JSON's `mass` stays the mass of the REAL
  (full) model, labelled as such.
* Nothing else moves: `holder_face_z = motor_length / 2 + ring_h + ring_holder_gap` was kept by A.7.1, so holders, bearings, shaft, screws and board
  are where they were in A.7.0 - A.7.3; the space between stator face and holder flange is filled by the ring again.

## Frameless variant: the kit for robots (template A.6.0, 2026-10-15)

One parametric model, two assemblies (owner decision 2026-10-15): the CIANO28 85 20SW1200 / L13 becomes a private configuration whose
3-D model is a **frameless kit** - **stator with coils and the phase board + rotor with magnets**. Not included: housing, stator holders,
rotor carriers / hub, bearings, shaft, screws, the glass-fibre rings that belong to the housing, the propeller plate. The public CIANO14 40
(L12 / L20) keeps the full model. The kit is not a second hand-made model: the same Params sheet drives the same parts, the housing and
the rotating mechanics are simply not built.

**The option.** `cad_variant: full | frameless` is a top-level key of the configuration yaml (`L13.yaml`, next to `role`, `winding`,
`materials`); absent = `full`. `python -m motor_ai_sim.cad.build ... --variant frameless|full` overrides it for one build. An unknown
value is refused in words (CLI exit 2; the catalog write refuses it with 422). The profile carries `profile.cad_variant` only for a kit, and the
manifest `param_hash` includes the variant only for a kit, so every full build keeps its hash (no rebuild of full models). Output file names are
unchanged (`<motor_id>_active.step`, `<motor_id>_full.glb`, `<motor_id>_BOM.csv`) so the viewer and download routes need no change; the
manifest (`cad_variant`), the datums JSON (`cad_variant`, `frameless` block), the GLB (`asset.extras.cad_variant`), the BOM (header row, T1
label "TOTAL mass of the frameless kit", an `E` row) and `GET /api/model3d/models` (`assembly`) say which one it is.

**Built (CIANO28 85 L13, FreeCAD 1.1.3, 214 s vs 482 s for the full model).**

| STEP part | Content | Solids |
|---|---|---|
| `Stator / Core` | stator lamination stack | 1 |
| `Winding / Coils / Coil_1..12` | coils (the enamel is part of the conductor model, as in the full model) | 12 |
| `Rotor / RotorCore` | rotor lamination stack; bore and the 4 keys exactly as in the full model | 1 |
| `Magnets / Magnet_01..28` | magnets (a sleeve joins them when a profile has `sleeve_thickness` > 0; L13 has none) | 28 |
| `Electronics / PhaseBoard_001..025` | phase board: ring plate + 24 lead sleeves (the CIANO14 40 would give the imported board's solids) | 25 |

67 solids (the full model: 114). No `Mechanics`, no bearings, shaft, carrier, holders, rings, screws. The GLB has the groups `Core`, `Coils`, `Rotor`,
`Magnets`, `Magnets_S`, `PhaseBoard_FR4`, `PhaseBoard_pins` (4.5 MB, 126 k triangles); the web viewer builds its part list from the GLB groups,
so no housing part is listed.

**Mounting datums** (`Mounting_datums`, an `App::DocumentObjectGroup`; hidden by default; every value bound to Params by an expression and checked
by the gate; also in the datums JSON `frameless` block): planes `Datum_Stator_front_face` / `_back_face` (z = +-motor_length / 2), `Datum_Mid_plane`,
`Datum_Rotor_front_face` / `_back_face`, `Datum_Coil_lead_end`, `Datum_Phase_board_top` / `_bottom` (plate board) or `Datum_Phase_board_origin`
(imported board); the axis `Datum_Motor_axis` (+Z through the origin); properties `Stator_outer_diameter`, `Stator_bore_diameter`, `Rotor_outer_diameter`,
`Rotor_bore_diameter` (the customer's hub seat, keys excluded), `Air_gap`, `Stator_stack_length`, `Rotor_stack_length`, `Rotor_key_count` / `_width` / `_height`.
Frame: +Z = motor axis, origin at the axial centre of the stator stack. L13: stator OD 85, rotor bore 49.2, stack 13, air gap 0.3, board faces at
z = -13.5 / -15.5, coil lead end z = -17.75. The CAD-only clearances (`offset_*`) stay CAD-only: the rotor pocket offset is in the kit's rotor as in the full model.

**Health checks.** The gate refuses a frameless model that contains any housing object (`Mechanics`, `Shaft`, `Stator_holder`, `Screws` ... :
`frameless.OMITTED_OBJECTS`) and checks the datums against Params. The pre-check does not judge the holder / bearing / shaft domain of a kit (it
still judges stator, rotor, magnets and coils); the mechanical fit table is not built. Rows that only the omitted parts use are reported in
`build_report.json` -> `roles_check.omitted_with_housing` (97 of 169 declared rows on the 85) and are not "dead rows" in a kit; a row
the kit does use but the Params roles do not declare still refuses the build.

**BOM, mass, lists.** The BOM lists the kit lines only (stator and rotor laminations, magnets, coils, wire, phase board, consumables); T1 is the kit
mass (L13 BOM view 395.6 g; the full BOM 501.4 g). `modules.motor_mass.full_mass` reads `cad_variant` from the configuration: the "full motor
mass" of a frameless configuration IS the kit mass with `scope = "frameless kit"` (`FullMass.scope` / `.kit`, `mass_summary`, `full_part_list`,
the catalog tree `mass.scope`, Configure `mass_scope`, the report cover "Mass (frameless kit)", the datasheet rows, the Materials tab total
"Frameless kit mass"): the app's active parts without the simulation shaft tube + the BOM's phase board / pins / wire enamel; every BOM row of the
housing is ignored even when an older full BOM is still on the disk (so the mass is right before the kit is rebuilt). With no BOM the kit mass is
partial exactly as before ("partial mass (no CAD model yet)") and still has no shaft. Real L13 numbers (server die + config, this build):
**kit 394.2 g, full motor 469.4 g**, active (EM) mass 396.8 g unchanged; rotating mass of the kit 134.6 g (rotor iron + magnets). Configure's fixed
mass may therefore be slightly negative for a kit (no shaft tube); `motorScaling.ts` accepts that.

**Rendering.** `python scripts/render_cad_glb.py MODEL.glb OUT.png [--view iso|front|back|side]` draws the viewer GLB with the palette colours, opaque
(a numpy software rasteriser; the FreeCAD GUI and VTK give black images without an OpenGL context).

**Server.** The CAD image on the server is built from the repo (`deploy/Dockerfile.cad`): after merge, build a new image tag from the merged commit,
then rebuild L13 with it (steps in the PR description). Until the API carries this code, `cad_variant` in a configuration is ignored by the mass and
the lists (a full BOM stays in use); the CAD build of an old image ignores the key and builds the full model.

## Simplified model: envelope only, downloadable at once (template A.7.0, 2026-10-15)

Owner decision: every motor has a SIMPLIFIED model that any signed-in user can download immediately; the real model (STEP, BOM, parameters,
FreeCAD file) stays behind the NDA and the applicant verification. The generator writes it for EVERY config - full and frameless alike -
next to the real one (`cad/fc_scripts/export_simplified.py`, rules and the hygiene gate in `motor_templates/segmented_flatwire/simplified.py`, run by
`build.py` after the BOM; `--no-simplified` skips it; a failed gate writes nothing and never refuses the real build, the manifest `simplified` block says why).

| Output | What |
|---|---|
| `<motor_id>_simplified.step` | envelope-only STEP |
| `<motor_id>_simplified.glb` | the same for the web tools (palette colours) |
| `<motor_id>_simplified.json` | tiny: envelope (bbox, size), mounting interface, board outline + terminal positions, solid counts, **total mass** of the real model (the BOM total, scope `full motor` / `frameless kit`) |

What it is made of:

* **Stator + rotor + magnets = ONE monolithic solid.** The stator outer circle and the end faces, and the rotor bore with its keys (the shaft / hub interface)
  exactly as built; everything inside is FILLED - slots, teeth, yoke cut-outs, magnet pockets, magnet shapes, the air gap. Its faces are the outer
  cylinder, two end planes and one face per edge of the bore profile; its volume is the envelope volume.
* **Every coil = ONE thick turn:** the winding's U-shaped outline in the plane of a middle turn, extruded from the bottom turn to the top turn (end turn
  included) plus the two terminal stubs where the first and the last turn leave it. No single turns, no wire. The blocks lie inside the filled slots:
  the solids overlap by design (an envelope model, not an assembly for interference checks). **Closure (template A.7.2):** the winding steps over to the next turn through a sloping transition at the rear end turn, so the section of ONE turn is open there (legs ending apart, a notch and a gap - the defect the owner saw on the live 40 mm model, 2026-10-15) while the real coil is closed at both ends. The block outline is therefore the union of the sections of all inner turns in the turn planes AND the gap planes between them; the first / last turn only add the lead stubs. `simplified.check_coil` (part of the export gate) proves it on every build: the mid section of the block is one region with exactly two closed wires (outline + tooth opening), the end-turn bar at the rear and at the front is ONE piece, and the real coil lies inside the block (< 0.2 % of its volume outside). The synthetic test builds a coil whose turns carry only half of the rear bar and shows that the old one-turn block fails the rear test ("REAR end turn is open ... 2 pieces").
* **Every bearing = ONE ring** d x D x B (no balls, cage or seals).
* **The phase board = ONE plain solid piece with holes** (owner amendment 2026-10-15): the board outline extruded to the board thickness (body, mask and copper
  together, not the pins), with its through holes - mounting holes, the holes the coil terminals pass through, windows, notches of the outline. No pins,
  components, copper, pads, silkscreen, mask or imported board body (the terminal positions go into the JSON; the coil terminal stubs may still pass through the
  holes). The outline and holes are the closed wires of the board body sliced at mid thickness.
* **As built:** shaft or rotor carrier, stator holders (outer shape and mounting holes), Prop_fix, screws (full variant). A frameless kit has
  the monolith, the coil blocks and the board only.
* **Omitted:** the glass-fibre coil rings `Ring_front` / `Ring_back` (full model only, template A.7.4; `simplified.OMITTED_MECH`, listed in the JSON `omitted`).

**Patent hygiene.** The model must not disclose the AeroStator Core internals (slot / yoke topology, magnet pocket geometry, coil construction).
`simplified.check()` is a gate the export runs before it writes anything: the monolith is one solid with one shell (an internal cavity would be a
second shell), has at most 3 + (bore profile edges) faces, only planes and cylinders, no cylinder between the bore and the outer circle, a volume equal to the
envelope volume within 1e-6; every coil block is one solid with at most 60 faces; the board (`simplified.check_board`) is one solid with one shell, planes and cylinders only, at most
2 + (outline and hole edges) faces and a volume of net outline area x thickness. Tests: `tests/test_cad_simplified.py` runs a synthetic slotted /
pocketed motor with a notched PCB (copper layers, pins, silkscreen) through the real builders in FreeCAD and proves that the gate refuses an un-filled stator,
a coil of single turns, and a board with pins, a curved extra or fused components.

**Viewing** (owner 2026-10-15, replaces "one model for everybody"): WITHOUT the NDA a caller sees ONLY the simplified model - `GET /api/model3d/<die>/<config>/simplified_view.glb` (any signed-in caller who may see the motor; not stamped, not logged: viewing is not downloading; `Cache-Control: private, max-age=60`); WITH it (the gate of the full STEP: `model3d.can_download` = NDA signed AND verification VERIFIED, owner and admin always) the full model with the part tree and every tool. The server is the gate: `model.glb`, `/parts` and `GET /api/family/die/<die>/part_tree` answer **403 `nda_required`** (after the 404 of a hidden or ungranted motor); `GET /api/model3d/models` carries `view: full | simplified | none` per entry (`none` + `view_reason` when the simplified files are not built: never a fallback to full). The web viewers load the route that matches `view`; the Motor Assembly tree, section, explode and measure exist only for `full`; a one-line note "Simplified model - sign the NDA for the full construction" (EN + zh-CN) sits next to the Sign NDA button.

**Access** (`routes/model3d.py`): `GET /api/model3d/<die>/<config>/simplified.step | .glb | .json` - any signed-in account that may see the motor
(`motor_access.may_see_config`, so a hidden configuration is the same 404 as for the viewer); NO NDA and NO verification; anonymous 401. The STEP header is
rewritten ("SIMPLIFIED - envelope only", the download id, the recipient, the mass of the real model); every download (STEP, GLB, JSON) is logged in
`cad_downloads.jsonl` with user, file, version, sha256 (`kind: simplified_step | simplified_glb | simplified_json`, `nda: false`) and can be traced like any
other (`/api/model3d/trace`). `GET /api/model3d/models` lists `simplified_available`, `simplified_step_bytes`, `simplified_glb_bytes`. The full STEP and every
Geometry Full file keep the NDA + verification gate. UI: "Download simplified STEP" in the 3-D model tab toolbar and in the Geometry Full Files menu
(always enabled for a signed-in user once the files exist), EN + zh-CN.

**Server.** The CAD runtime builds it with the real model: rebuild the `motres-cad` image from the merged tree (`export_simplified.py` is under `cad/`), then
rebuild every config (the rebuild worker / `docker run ... build --die ... --config ...` as above); until a config is rebuilt its list entry says
`simplified_available: false` and the button is disabled. Rebuilding also refreshes the manifest with the new file list.

## One colour palette (2026-10-14)

`cad/motor_templates/segmented_flatwire/palette.py` (+ `palette.json`) is the single source of the colours of the FreeCAD GUI finish and
of the GLB exporter (sRGB values; glTF base colours are written linear).  Everything is opaque; the GUI finish refuses a model with a visible
transparent body (`view.check_opaque`).


## Geometry tab "Full": CAD-only edits, the rebuild queue and the part tree (owner 2026-10-15)

The Geometry tab has a switch **Active | Full** (the same state as Materials). Active is the electromagnetic construction. Full shows
the CAD model, THE part tree and the construction parameters of this template that the simulation does not read - walls, shafts,
holders, rings, bearings, phase board, screws, leads, `offset_rotor` - editable.

**Where an edit lives.** `<config>.yaml` gets a `cad_overrides:` block (`{parameter: number}`, plus `bearing_card: <card of
bearings_library.yaml>`). Nothing else reads it: the electromagnetic solver, the passport and `_geoSig` ignore it (a test pins that
the EM rows of the profile and the geometry signature are identical with and without it). `cad/store.py` reads it in
`load_effective`, applies it in `make_profile` AFTER the simulation rows (the expression a parameter had is kept as
`reference_expression`, so an override can be reverted from the built `params.json`), and records `cad_overrides_hash` in the
manifest. Editable names are exactly `src/motor_ai_sim/cad_params.py` `CATALOGUE` (group, unit, limits, EN / zh-CN tooltip) intersected
with the `mechanics` / `input` rows of the built model; the catalogue never contains a name the simulation maps.

**Validation.** `PUT /api/family/config/{die}/{cfg}/cad_params` checks ranges and integers, then applies the edit to the built model's
parameter set, evaluates every expression and runs the template's own `health.precheck` (loaded by path; the API image copies that one
file). The violations come back in the template's words (422) and nothing is saved. The build repeats the pre-check and the health gate;
a refusal at build time is shown in the model's badge. Permission = every other die/configuration write (`require_catalog_write` +
`_require_die_write`: the admin on the shared catalog, any registered account in its own workspace copy under the layering); a save is
written to the admin audit (`cad.params`).

**Rebuild.** The API image has no FreeCAD. A save (or `POST .../cad_rebuild`) writes `<cad root>/.rebuild/<id>.json` (state `pending`).
The worker next to the CAD runtime builds the pending requests of the shared catalog, one at a time at low priority:

```bash
python -m motor_ai_sim.cad.rebuild_queue run --root /srv/motres/shared/cad          # one pass; --loop polls every 30 s
python -m motor_ai_sim.cad.rebuild_queue list --root /srv/motres/shared/cad         # what waits
```

(inside the `motres-cad` image, with the store mounted as in "Rebuild" above). Requests of a user's workspace die are listed but not
built unless the worker is given `--include-layer`. The UI compares the manifest's `cad_overrides_hash` with the saved overrides: a model
that does not carry them yet stays on screen with the badge "rebuild queued" / "rebuilding" / "model out of date" (with a "Rebuild now"
button) / "rebuild failed" (with the build's message); when the new model passes every check the badge goes away and the viewer loads
the new GLB (it reloads on a changed `glb_sha256`).

**THE part tree** is `src/motor_ai_sim/part_tree.py`: ids, EN / zh-CN names, groups and order of every part, and the GLB nodes that draw it.
Geometry Full, Materials Full, the 3-D model's parts list (`GET /api/model3d/{die}/{config}/parts`, names only, after the NDA), the
component card (`parts[].part_id`, `group`) and the BOM export all read it. `GET /api/family/die/{die}/part_tree` serves the full form.

**Files menu (Full).** `GET /api/model3d/{die}/{config}/` `model.step`, `bom.csv`, `bom.xlsx` (part list with materials and masses, no prices
or suppliers), `params.csv` (the full parameter set, EM + CAD-only, Fusion Parameter I/O dialect; the download id is written into every
Comment) and `model.fcstd` (engineer / admin with the NDA only). All are NDA-gated for non-admins, stamped and logged in
`cad_downloads.jsonl` (`kind` = step / bom_csv / bom_xlsx / params_csv / fcstd). CAD parameters come back in through
`POST /api/family/config/{die}/{cfg}/cad_params/import?dry_run=1|0` (Fusion CSV or FreeCAD file; electromagnetic rows are ignored).
