# CAD generator: parametric FreeCAD motor models from the simulation store

One parameter source: the simulation die/config (`/srv/motres/shared/dies/<die>/{die.yaml, L*.yaml}` and the
materials library). The generator reads it, never writes it, and builds the motor model with FreeCAD 1.1 on the server.

Roadmap: (1) model done, (2) this generator + server build per die/config, (3) BOM, (4) STEP download (simplified
envelope STEP before the NDA, full STEP after), (5) 3-D load FEM.

## Layout

| Path | What |
|---|---|
| `cad/motor_templates/segmented_flatwire/` | the FreeCAD template (PartDesign + Draft arrays), version-safe for FreeCAD 1.0.2 and 1.1. `TEMPLATE_VERSION` in its `__init__.py` goes into every manifest. |
| `cad/reference/` | Fusion reference profile (`CIANO14_40_12.json`, the open Fusion model), its parameter values, `Coil_L20_fusion.step` (read-only Fusion export used for the coil check) |
| `cad/fc_scripts/` | scripts that run inside FreeCAD: `build_model.py`, `gui_finish.py` (headless GUI: colours, camera), `check_gui.py`, `export_step.py`, `solids_dump.py`, `coil_vs_reference.py` |
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
shows its Tip only); sketches, intermediate features, origins, axes, planes and the Params sheet hidden; core grey,
coils copper, rotor iron dark, magnets one colour (the template does not know the polarity); isometric camera fitted to
the model in `GuiDocument.xml`. The colours and the camera are written by a headless FreeCAD GUI step
(`QT_QPA_PLATFORM=offscreen`; `MOTRES_CAD_XVFB=1` uses `xvfb-run`); the rules are checked again through the GUI
(`check_gui.py`) before the output is accepted.

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
* Stage 4 (STEP download): a route that serves the `.step` of a die/config after checking the user's NDA state. The
  simplified envelope STEP before the NDA is not generated yet (would be a small template-side export: stator and rotor
  envelopes, no coils / magnets); the manifest's `param_hash` is the cache key.
* Rebuild trigger: compare `geo_sig_hash` / `param_hash` of the store with `manifest.json`; rebuild on mismatch.
