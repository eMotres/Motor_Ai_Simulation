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

## Verification (2026-10-12)

See the PR description; evidence files `verify_*.json` next to the builds on the server (`/opt/motres/compute/cad-build/`).

## What stage 3 / 4 need

* Stage 3 (BOM): read `manifest.json` + `<motor_id>_datums.json` + `build_report.json` volumes; materials are in
  `params.json` (`profile.materials`, card data in `profile.material_cards`).
* Stage 4 (STEP download): a route that serves the `.step` of a die/config after checking the user's NDA state. The
  simplified envelope STEP before the NDA is not generated yet (would be a small template-side export: stator and rotor
  envelopes, no coils / magnets); the manifest's `param_hash` is the cache key.
* Rebuild trigger: compare `geo_sig_hash` / `param_hash` of the store with `manifest.json`; rebuild on mismatch.
