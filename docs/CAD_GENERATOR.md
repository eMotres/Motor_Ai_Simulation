# CAD generator: parametric FreeCAD motor models from the simulation store

One parameter source: the simulation die/config (`/srv/motres/shared/dies/<die>/{die.yaml, L*.yaml}` and the
materials library). The generator reads it, never writes it, and builds the motor model with FreeCAD 1.1 on the server.

Roadmap: (1) model done, (2) this generator + server build per die/config, (3) BOM (done, section below), (4) STEP download + the web "3D model" menu (done, section below: view for
everyone, STEP download under NDA), (5) 3-D load FEM.

## Layout

| Path | What |
|---|---|
| `cad/motor_templates/segmented_flatwire/` | the FreeCAD template (PartDesign + Draft arrays), version-safe for FreeCAD 1.0.2 and 1.1. `TEMPLATE_VERSION` in its `__init__.py` goes into every manifest. |
| `cad/reference/` | Fusion reference profile (`CIANO14_40_12.json`, the open Fusion model), its parameter values, `Coil_L20_fusion.step` (read-only Fusion export used for the coil check), `fusion_mechanics/` (read-only Fusion STEP + body/occurrence JSON of Shaft, 618_8, Stator_holder(+screws), Stator_holder_back, Ring, Prop_fix, 91292A032), `add_mechanics_rows.py` (adds the stage-3 rows to a profile) |
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
`shaft_height` (bore `shaft_bore_r = shaft_seat_r - shaft_height` = `shaft_inner_radius`), the bearing card of the config
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
| `shaft_thread_len`, `shaft_tip_len` (d11705/6) | M5 section (4.95) and tip (4.9) lengths | 8, 8 |
| `back_holder_len` (d11729) | back holder cylinder; A_H = -(holder_face_z + holder_flange_t + this) | 7 |
| `back_holder_ref_r` (d11730), `back_holder_wall` (d11731, driven) | outer r = ref + (ref - seat r 8) = 12 | 10, 2 |
| `holder_hub_wall` (d11752) | front hub r 8 -> 8.8 (also the spoke-root circle) | 0.8 |
| `shaft_shoulder_len`, `shaft_shoulder_r` (d11689/95) | 9.4 shoulder against the front inner ring | 0.5, 4.7 |
| `shaft_flange_step` (d11885) | flange r = rotor bore 5 + 3 (front rotor stop, z 10..11.4) | 3 |
| `shaft_bore_r` (d11692, = seat r - shaft_height), `shaft_bore_tip_angle` (d11685) | bore 6, 45 deg end cone, apex at the journal end | 3, 45 deg |
| `coil_holder_clearance` (d11980), `ring_t` (d11983) | flange r = coil corner 13.233 + 0.4; ring radial thickness | 0.4, 0.8 |
| `ax_core_ref_inset` (d9633) | construction line only (role info) | 0.9 |

Not dimensioned in Fusion (Sketch2 is not fully constrained), read as 14.843191 / 1.460021 and rounded by the owner
2026-10-13 (Fusion not dimensioned): `holder_bolt_r` 14.85, `holder_window_r` 1.46.  Fusion's Chamfer7 at the journal end chamfers one of the two arcs left by the flats (edge picked after
Extrude15); the template chamfers both.  Screw length: `holder_screw_req = 2 holder_face_z + holder_flange_t` (L20 25, L12 17), `holder_screw_l` = that rounded UP to
a stock M2 length (even mm up to 20, then 25, 30, ...; owner 2026-10-13: L12 M2x18, L20 M2x25 = 91292A032).  `fit()` writes
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
(2026-10-13) L12 -> 16 is refused in words: the required 21 mm rounds up to M2 x 25 and its tip would break through the back
flange (no stock M2 length fits); 17 -> 18, 25 -> 25, 29 -> 30 do fit.

Updating the owner repo (`CIANO14_40_motor_model`): motor_ai_sim's `cad/` is the one source.  `tools/update_from_motor_ai_sim.py`
there copies `cad/motor_templates/segmented_flatwire/`, `cad/reference/` and the fc scripts from a motor_ai_sim checkout
(path argument, default `../motor_ai_sim`), records the source commit in `motor_templates/SOURCE.json`, and never touches
the owner's models or `params/`; the models in `models/` are refreshed from the server build (`Swap_in_new_models.cmd` if
FreeCAD has them open).

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
