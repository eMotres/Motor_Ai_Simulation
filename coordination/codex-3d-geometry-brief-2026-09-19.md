# Brief for Codex — real 3-D motor geometry (2026-09-19)

Owner: «запустить Codex для достройки 3-D геометрии мотора, чтобы она была
реальной, а не только для электромагнитного моделирования». Builds on the
hand-over in `coordination/motor-parametric-handoff-2026-09-18/` (read all
three files first) and on the rules every agent must follow:
`AGENTS.md` → `C:\Users\vadim\Projects\AGENT_RULES_motor_ai_sim.md` and
`C:\Users\vadim\Projects\AGENT_DATASET_RULES.md` (log every finished task).

## Goal
One authoritative parametric description of the whole machine from which
BOTH the 2-D/3-D electromagnetic model (unchanged) and a real, manufacturable
assembly are derived: stator stack, winding with end turns, rotor core +
magnets + retaining sleeve (or bridges), stepped shaft, bearings from the
bearing card, end shields, housing per cooling mode (air fins / liquid jacket /
robotics housing with mount flange), fasteners — with masses, contacts and
semantic surfaces that the thermal (heat paths), mechanical (rotor stress,
contacts, modal) and cost models consume, and STEP/FreeCAD export + drawings.

## Non-goals (do not touch)
- The electromagnetic solvers and their geometry (`simulation/geometry_2d.py`,
  `cadquery_geometry.py` 2-D sections, meshing, torque/loss physics).
- Report physics; the report only gains consistent masses/labels (see the
  open findings #7/#8 in `…scratchpad\l13_review_2026-09-19.md`: mass rows
  must add up; a sleeve with 0 thickness is "not installed").
- Manufacturing tolerances are not inferred from STEP shape — they are
  parameters with defaults in the spec.

## Existing assets to audit before designing (grounded, not assumed)
- `src/motor_ai_sim/cadquery_geometry.py` — CadQuery 2-D/3-D builders and
  the viewer meshes (`routes/geometry.py` 3-D viewer mesh cache).
- `freecad_io.py`, `routes/freecad.py` — FreeCAD export (macro-dxf bundle);
  workstation constraint: Windows App Control blocks native `.pyd` (OCP,
  earcut) locally → CadQuery/OCP run natively only on the Linux server; local
  path = FreeCAD macro bundle. Design the builder so it runs in both places.
- `scripts/fusion360_sync_params/` + `/api/fusion` — six-column parameter I/O.
- `static3d/` — the 3-D end-effect program (one sector, end windings).
- `mechanical/` — rotor stress, contacts, magnet retention joint (judged on
  sleeve–magnet), modal, critical speeds, bearings (SKF model, cards in the
  bearings library).
- `thermal_heat_paths.py`, `routes/thermal.py` (robotics housing: still air +
  radiation ε, mount W/K, end faces, bore, shaft stubs — its surfaces must
  become the assembly's real surfaces), `thermal_capacities.py`.
- Mass model in `report.py` (parts included / reference / excluded;
  customer parts such as the shaft).
- Reference assembly: `C:/Users/vadim/Downloads/CIANO14 40_12.step` (139
  solids) and `eMotres/hardware/motor_parametric_review/` inspection.

## Deliverables, in order (each a separate commit + a journal entry in `coordination/codex.md`)
0. **Audit + gap plan** (no code): what each module above really provides,
   what the reference STEP contains (parts, bearings, shaft steps, housing,
   fasteners, masses), and a grounded architecture for the pilot
   CIANO14 40_12 — one spec file, derivation to detailed geometry, semantic
   surfaces, contacts, materials, BOM, stale-result tracking.
1. **Authoritative spec** `config/dies/<die>/assembly.yaml` (or a schema in
   `src/motor_ai_sim/assembly/`): housing type + dimensions, end shields,
   bearing cards + seats, shaft steps/keyway/customer flag, sleeve thickness
   & material, end-turn geometry, fasteners, tolerances with defaults —
   derived from the existing `motor_config.yaml` / die yaml, never duplicating
   what the EM geometry already defines (single source per parameter).
2. **Detailed geometry builder** producing the assembly solids (CadQuery on
   the server, FreeCAD-macro path locally), each part with material, mass,
   semantic surface tags (housing_outer, end_face_A/B, bore, mount_flange,
   bearing_seat_A/B, shaft_stub_A/B, end_turns_A/B …) and contacts.
3. **Consumers**: masses → report mass rows (active / without customer parts /
   full / used for N·m/kg) and the cost model; surfaces → heat-path areas of
   the robotics/liquid/air housings; bearing seats → mechanical; a
   `stale_geometry` flag when the spec changed after a solve.
4. **Export**: STEP + FreeCAD project, a BOM (part, material, mass, drawing
   ref), 2-D drawing references; round-trip check against the reference STEP
   for the pilot (bounding boxes, part count by class, bearing positions,
   masses within a stated tolerance).
5. **Web**: the assembly in the 3-D tab (parts toggle, section view), the
   spec editable where the EM geometry is not (housing, bearings, shaft).

## Acceptance for the pilot (CIANO14 40_12)
- Assembly builds from the spec without manual CAD steps; masses of stator
  iron/copper/magnets agree with the report's rows within 1 %; bearings and
  shaft match the reference STEP positions within 0.2 mm; the robotics heat
  paths read the housing/end-face/mount areas from the assembly, and the L13
  thermal answers do not move unless the areas differ (state the difference).
- Tests for the spec schema, the derivation, the surface tagging, the BOM
  and the export; `python -m pytest tests -q` for the touched areas green;
  no change in `tests/physics_baseline.json`.

## Working rules (short)
Path-limited commits on `pre-migration-freeze-2026-09-15`, never `git add
-A`/`stash`; the owner's local API on port 8001 is read-only (no restart, no
loads); no long solves by day on the workstation; no console windows;
journal every step in `coordination/codex.md`; log each finished task to the
dataset (`AGENT_DATASET_RULES.md`). Report in Russian to the owner at the end
of each deliverable with what was verified and what is assumed.
