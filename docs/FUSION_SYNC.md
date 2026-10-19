# Fusion ↔ AeroStator sync — design (Stage 1, DRAFT for owner approval)

Status: **draft, 2026-10-19**. No server endpoint is coded before the owner approves this document.
Motor-specific details (document ids, component map, the first round-trip plan) live in the private repository:
`motor-ai-sim-private/docs/FUSION_SYNC_CIANO14_50.md`.

## 1. Roles

| Tool | Owns | Never does |
|---|---|---|
| **Fusion** | Every piece of geometry a human edits: bodies, sketches, user parameters, production drawings. | — |
| **AeroStator** | Physics and optimisation: the die/config EM parameters, losses, thermal, mechanics checks. | Never edits a Fusion document by itself. |
| **FreeCAD (server)** | Automatic assembly and rendering *from Fusion exports*: 3-D view, GLB, STEP, sections, BOM, masses. | Nobody edits FreeCAD files. No geometry is invented there. |

Two directions, one add-in, two buttons:

```
 Fusion (owner edits, saves version N)
   │  [Export to AeroStator]  STEP per component + parameters.json + sketch DXFs + manifest (doc, version N)
   ▼
 AeroStator server  ── stores bundle vN (immutable) ── queues a CAD rebuild from the Fusion bodies
   │                   every derived file is stamped "Fusion <doc> vN"
   │  optimiser / engineer changes EM parameters of the die/config
   ▼
 Fusion  [Import from AeroStator]  diff old → new, owner accepts, model rebuilds, owner saves vN+1
```

## 2. Scope of Stage 1

- **One motor: CIANO14 50** (AeroStator die `CIANO14 50 edited`, config `L15`; Fusion document `CIANO14 50`).
- An **allow-list** of dies decides where the sync may act (`FUSION_SYNC_DIES`, Stage 1 = `["CIANO14 50 edited"]`).
  Every other die, and in particular the frozen 40 mm die `CIANO14 40 new` (mould ordered), is refused by every
  sync route with 403 `fusion_sync_not_enabled`. The existing 40 mm `fusion_parts` profile is not touched.
- No change to the existing `/api/fusion/params.*` and `/api/fusion/import` routes (Geometry tab buttons keep working).

## 3. The Fusion add-in "AeroStator Sync"

Location (public repo, code only): `fusion/AeroStatorSync/` — `AeroStatorSync.py` (Fusion entry: `run`/`stop`,
commands, dialogs), `AeroStatorSync.manifest`, `resources/` (icons), `lib/` (pure Python, no `adsk` import: bundle
assembly, manifest, diff, units, HTTP client — unit-tested outside Fusion with pytest).

Install once: Fusion → Utilities → Add-Ins → Scripts and Add-Ins → Add-Ins → "+" → the `fusion/AeroStatorSync` folder,
tick "Run on Startup". Two buttons appear in a panel **AeroStator** on the Solid tab (and Utilities).

Settings (first run, button "Settings"): server URL (default `https://aerostator.com`), the sync key (§6), optional
mirror folder (§5.4). The key is stored with Windows DPAPI in `%APPDATA%\MOTRES\fusion_sync\key.dpapi`, never in the
document and never in plain text.

### 3.1 Export to AeroStator (one click)

1. **Guards** (refuse with a message, nothing is sent):
   - the active document has **unsaved changes** (`isModified`): "Save a version first — the export must be exactly
     version N". This makes the version number a true name of the geometry.
   - the document is not linked to a die/config: the first export opens a picker (die/config list from the server,
     allow-listed dies only) and stores the link on the server (§5.1).
2. **Collect** (read-only; the document is not modified):
   - `manifest.json` (schema `motres-fusion-bundle/1`): document name, lineage URN, `version` = `dataFile.versionNumber`,
     Fusion build, add-in version, user, time, units (mm), frame (Fusion root frame, Z = motor axis), the linked
     die/config, and `sha256` + size of every file below.
   - `parameters.json`: **all** user parameters — name, expression, unit, value **in the parameter's own unit** (the API
     gives cm internally; converted here), comment.
   - `parts/<Component>.step`: one STEP **per component** that carries solid bodies, in component coordinates
     (`exportManager.createSTEPExportOptions(file, component)`), plus for each component: bodies (name, volume, area,
     bbox, face count, material, appearance) and **every occurrence with its world matrix**. Components, not bodies, are
     the unit: body names in our models are generic (`Body12`), component names are the part names (`Shaft`, `Core`,
     `Magnet`, `Coil`, `Stator_holder`, …). This is the format of the 40 mm export (`fusion_export_manifest.json`),
     generalised to all components.
   - `assembly.step`: the whole root component (reference for sections and visual checks).
   - `sketches/<Component>__<Sketch>.dxf`: the sketches listed in the link's `sketches` (lamination, magnet, coil
     cross-sections; e.g. `Core/Sketch1`, `Rotor/Sketch1`, `Magnet/Sketch1`, `Coil/Sketch1`), written with
     `exportManager.createDXFSketchExportOptions`. A listed sketch that is missing is an error, not a silent skip.
3. **Upload** one zip `"<doc>_v<N>.zip"` to `POST /api/fusion/sync/bundles` (§5.2). The add-in shows the server answer:
   stored / already stored, the parameter diff Fusion ↔ AeroStator (§5.3), and "CAD rebuild queued".

### 3.2 Import from AeroStator

1. Fetch `GET /api/fusion/sync/{die}/{config}/params.json` (§5.2): the EM (primary geometry) parameters of **that
   die/config** — not the browser's open machine — under their Fusion names, with units.
2. **Name resolution.** Canonical names first (`stator_diameter`, `magnet_height`, …). If the document still uses the
   legacy names (`stator_up_r`, `magnet_h`, `stator_w`, …) the add-in does **not** write to them; it says so and offers
   the already approved rename (`scripts/fusion360_rename_params`, owner decision 2026-09-25 — renames, creates, and the
   one formula `stator_up_r = stator_diameter / 2`). After the rename, the import runs on canonical names.
3. **Diff dialog**: a table `parameter | Fusion now | AeroStator | unit`, only changed rows, each with a tick box (all
   ticked). Rows whose Fusion expression is a **formula** (references another parameter) are shown but not ticked by
   default: writing a number would break the owner's formula; he decides.
4. **Apply** on OK: each ticked parameter's `expression` is set with an explicit unit (`"50 mm"`, counts unitless),
   compared in the parameter's own unit so an unchanged value never dirties the timeline. All changes go in one
   transaction: if the timeline reports errors after recompute the dialog lists the failing features and offers
   **Undo all**. The add-in **never saves**: the owner reviews and saves a new Fusion version, then exports it.

### 3.3 Add-in status and Fusion API findings (2026-10-19)

Built: `fusion/AeroStatorSync/` (entry `AeroStatorSync.py`, package `aerostator_sync/`: `params`, `bundle`, `client`,
`keystore`, `fusion_collect`), unit tests `tests/test_fusion_sync_addin_lib.py`. The export was run through the local
Fusion MCP on the CIANO14 50 document; every part STEP matched the Fusion body volumes (FreeCAD read-back).

Fusion API behaviour the code works around:

- A component STEP silently leaves out **hidden** geometry (light bulb off). Switching bulbs on and back off marks the
  document modified, so hidden components are copied (TemporaryBRepManager) into a temporary design, exported from
  there, and that design is closed unsaved. Fusion has no invisible documents, so it shows for a moment.
- A component STEP includes the component's sub-occurrences (e.g. the holder STEP also carries its screws); the
  manifest lists the component's own bodies, and the server matches solids to them by volume.
- The DXF sketch export needs a writable script context even though it changes nothing.
- `importToTarget` (STEP import) always goes into the **active** document, whatever target component is passed.
- An occurrence (component) pattern whose count is an expression shows a "not an exact integer" warning while its
  document is not active; the copies are still placed correctly.

## 4. Server: rebuild from Fusion bodies

### 4.1 Template A.8 "Fusion assembly"

Today (A.7.7) only `fusion_parts.files` (Shaft, Prop_fix, holders of the 40 mm) replace template parts; the active
parts (core, rotor, magnets, coils) are still built parametrically by FreeCAD. A.8 extends `fusion_parts` with
`"source": "bundle"`:

```json
"fusion_parts": {
  "source": "bundle",
  "dies": ["CIANO14 50 edited"],
  "component_map": {"Core": "Core", "Rotor": "RotorCore", "Magnet": "Magnet", "Coil": "Coil",
                    "Shaft": "Shaft", "Stator_holder": "Stator_holder", "Stator_holder_back": "Stator_holder_back",
                    "Ring": "Ring", "Prop_fix": "Prop_fix", "618_8": "Bearing", "...": "..."},
  "ignore": ["Null", "PCB 40 serial_new"]
}
```

- The bundle is found by the build from the link (§5.1): `FC_FUSION_BUNDLE=<bundle dir>`.
- Every mapped component is imported **once** (largest solid = the part, as `fusion_parts.import_fusion_part` does) and
  placed at **each occurrence matrix** (14 magnets, 6 coils, …). Template body names are kept through `component_map`,
  so BOM, masses, GLB, STEP tree, sections and the Production tab work unchanged.
- No stretch / shift: the bundle is exported at the length the Fusion model has. The A.7.7 stretch rule stays only for
  the 40 mm profile.
- **Gates:** each imported solid is closed and its volume equals the Fusion body volume in the manifest (1e-6 relative,
  after the canonical STEP round trip); every component of the map is present; unmapped components with solids are
  listed in the build report (not silently dropped); the fit check (interferences) runs as today.
- A component missing from the bundle refuses the build. There is no fall-back to template geometry.
- **Physics is unaffected**: the EM/thermal solvers keep using the die's parametric 2-D geometry. The bundle's
  parameters are compared with it (§5.3), never written into it.

### 4.2 Stamping

Every derived file carries the Fusion source:

| File | Stamp |
|---|---|
| `manifest.json` (build) | new block `fusion: {document, urn, version, bundle_sha256, exported_at}` |
| `*.step` | `FILE_DESCRIPTION` + `FILE_NAME` originating system: `Fusion 'CIANO14 50' v3 → AeroStator CAD A.8` |
| `*.glb` | `asset.extras.fusion` (same block) |
| BOM `.csv` / `.xlsx` | header line / title cell `Source: Fusion 'CIANO14 50' v3` |
| sections, production drawings (PNG/PDF) | title block field `Fusion v3` |
| DXF re-exports | `999` comment line with the same text |

`model3d.model_info` returns the block, so the 3-D and Production tabs can show it.

## 5. Server: API and storage

### 5.1 Storage (server only, per die)

```
/srv/motres/shared/fusion/<die slug>/
  link.json                  {document_urn, document_name, die, config, sketches[], linked_by, linked_at}
  bundles/v<N>/              unpacked bundle (immutable) + bundle.zip
  latest.json                {version, sha256, exported_at, exported_by}
  audit.jsonl                one line per link / upload / import fetch
```

No die.yaml or config file is written by the sync: the die store, its hashes and the EM results are untouched.

### 5.2 Routes (`src/motor_ai_sim/routes/fusion_sync.py`, prefix `/api/fusion/sync`)

| Route | What |
|---|---|
| `GET  /link?urn=` | the die/config a Fusion document is linked to, or 404 |
| `PUT  /link` | link a document to an allow-listed die/config (first export) |
| `GET  /targets` | allow-listed dies and their configs (picker of the first export) |
| `POST /bundles` | upload a bundle zip (multipart). Checks: allow-list, link matches, schema, every `sha256`, zip-slip and size limits (≤ 200 MB), STEP/DXF/JSON file types only. Same version + same content → 200 `already_stored`; same version + different content → 409 (a Fusion version is immutable). Stores, writes `latest.json`, computes §5.3, queues the CAD rebuild (`rebuild_queue.request(..., fusion_version=N)`). |
| `GET  /bundles?die=` | list of stored versions |
| `GET  /{die}/{config}/params.json` | Import source: the EM parameters of that die/config under Fusion names (`config/fusion_param_map.yaml`), units, and the die's recorded Fusion version |
| `GET  /{die}/{config}/status` | version guard (§5.3) |

**Auth.** All routes need an admin. The add-in cannot hold a browser session, so it uses an **agent key** (`emk_…`,
created by the owner in "Access for agents") with a new scope **`fusion:sync`**. Today agent keys are accepted only on
`/mcp`; a new dependency `require_fusion_sync` accepts on these routes only: a valid, active key with scope
`fusion:sync` whose owner is an admin — or a normal admin session (browser / tests). The static `ADMIN_API_TOKEN` is
**not** accepted (it is read-only by rule). Every call is written to `audit.jsonl` and the MCP audit.

### 5.3 Version guard and parameter diff

`status` answers, for a die/config:

- `latest_export` — the newest bundle (version, time, who);
- `model_built_from` — `manifest.fusion.version` of the current CAD model;
- `rebuild` — queue state (pending / building / failed + tail);
- `param_diff` — the EM parameters where the latest bundle and the die/config disagree
  (`magnet_height: Fusion 5.7, AeroStator 7.0`), via the same name map and unit rules as the import;
- `aerostator_changed_since_export` — the die/config files' sha256 differ from the ones recorded at the last export
  (an engineer or the optimiser changed EM parameters → "Import into Fusion pending").

Warnings (one short line + tooltip, owner UI rule):

| Where | Shown when |
|---|---|
| 3-D tab (`Model3DPanel.tsx`, the existing `<Alert>` slot) | model built from Fusion vN but latest export is vM; rebuild failed; `param_diff` not empty |
| Production tab (`ProductionPage.tsx`, beside the readiness bar) | same, plus "drawings are from Fusion vN" |

`rebuild_queue.status()` also marks a model stale when `latest.version != manifest.fusion.version` (today it only
compares `cad_overrides_hash`).

### 5.4 Private repository copy

3-D models belong in `eMotres/motor-ai-sim-private` (owner rule 2026-10-19). The server cannot push there. Two
options (owner decision, §8 Q3):

- **A (recommended):** the add-in's optional *mirror folder* = the local private-repo checkout
  `cad/reference/fusion_bundles/<die slug>/v<N>/`; an agent commits it on a branch for review. Nothing in Fusion waits
  for git.
- **B:** server only (bundles backed up with `/srv/motres/shared`); the private repo gets copies on request.

## 6. Safety rules (all enforced in code, all tested)

1. Allow-list of dies; the 40 mm die is never on it in Stage 1.
2. The add-in never saves a Fusion document; Import only after the owner's OK in the diff dialog.
3. Export refuses a document with unsaved changes.
4. Bundles are immutable; a version number never changes content.
5. The sync never writes die.yaml / config files; Fusion values are compared with AeroStator, not adopted. Adopting
   Fusion values into a die (Fusion → AeroStator parameters) is a separate, explicit Stage 2 action through the
   existing validated geometry write path.
6. No fall-back to template geometry when a Fusion part is missing.
7. Agents use the Fusion MCP read-only unless the owner allows writes for a named test.

## 7. Tests (targeted, never the whole suite)

- `tests/test_fusion_sync_addin_lib.py` — bundle assembly from fake components, manifest/sha256, unit conversion
  (cm → parameter unit), diff (formula rows unticked), legacy-name detection.
- `tests/test_fusion_sync_routes.py` — TestClient with a temp shared root: allow-list (40 mm → 403), auth (no key,
  wrong scope, non-admin key, revoked key → 401/403), link, upload idempotent / 409, zip-slip and bad sha refused,
  params.json of a named die/config, status and stale detection, rebuild request written.
- `tests/test_cad_fusion_bundle.py` — profile with `source: bundle` → component map, occurrence placement, volume
  gate, missing component refusal (FreeCAD; runs only in the server sandbox CAD image).
- Round trip on CIANO14 50 (manual, owner present): Export v1 → server rebuild → change one parameter in AeroStator →
  Import → Fusion updates → owner saves v2 → Export v2 → guard clears.

## 8. Open questions for the owner

1. **The Fusion document "CIANO14 50" (v1) is still the 40 mm geometry with the legacy parameter names**; AeroStator
   `CIANO14 50 edited / L15` differs in almost every EM parameter (table in the private doc). Should the first round
   trip be: approved rename → Import from AeroStator (L15 values) → you fix what the timeline breaks → save v2?
2. **Testing in Fusion:** may an agent run the add-in on "CIANO14 50" through the Fusion MCP with writes (parameter
   changes in the open session, then Undo / close without saving), or do you prefer a copy "CIANO14 50 sync-test"?
3. **Private repo copy:** option A (mirror folder + agent commit, recommended) or B (server only)?
4. **Auth:** agent key with the new scope `fusion:sync`, admin accounts only — OK?
5. **Active parts from Fusion too** (core, rotor, magnets, coils placed from Fusion bodies in the 3-D model; the EM
   solver keeps its own parametric 2-D geometry) — OK?
