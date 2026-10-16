# Fusion 360 scripts for motor_ai_sim — how to use them (2026-09-25)

Three scripts for Fusion 360, each in its own folder under `scripts/`, each
launched from the Fusion **Utilities → Scripts and Add-Ins → "+"** dialog
(add once, afterward just double-click to run). The exchange format is
the same 6-column CSV as before (`Name, Unit, Expression, Value,
Comment, Favorite`, the format used by the Fusion "Parameter I/O" add-in). The backend
(`/api/fusion/params.csv` and `/api/fusion/import`) and the buttons on the Geometry tab
("⇩ Fusion CSV" / "⇧ Fusion CSV") already existed and were not changed — what's new
here is only on the Fusion side.

## Where to copy them

Each of the three folders is a separate script for Fusion:

- `scripts/fusion360_rename_params/`
- `scripts/fusion360_export_params/`
- `scripts/fusion360_import_params/`

plus a shared module (NOT added to Fusion by itself, but must sit
next to them, since the scripts import it):

- `scripts/fusion_param_common.py`

In Fusion: **Utilities → Scripts and Add-Ins → Scripts → "+"** → point it
to the folder you need (e.g. `scripts/fusion360_rename_params`). Fusion will
pick up the `.manifest` on its own and show the name/description. Repeat for all three
folders. `fusion_param_common.py` does not need to be copied into Fusion separately — the three
scripts find it themselves one level above their own folder
(`scripts/fusion_param_common.py`), so the whole `scripts/` folder must
stay on disk next to them (do not move a single script folder away from
the rest of the repository).

## Order of use

### 1) `fusion360_rename_params` — rename the old parameters (once)

Only needed for the **old** Fusion model, where the parameters are named
the old way (`stator_up_r`, `slot_h`, `magnet_h`, ...). Open the model in Fusion,
run the script:

**Main rule (owner, 2026-09-25): nothing is ever deleted, and
only ONE existing formula is ever changed.** Three kinds of changes, and nothing
else:

1. **Rename** (27 of 33 parameters) — just changes the `Name`,
   the value is untouched. Fusion itself updates every formula that
   referenced the old name (this is done by Fusion's own API — nothing here
   is rewritten by hand).
2. **Create** — a new parameter for the 6 that have no old
   equivalent at all (`num_slots_per_segment`, `shaft_height`, `sleeve_thickness`),
   or only when the old name is absent (`stator_fillet_r1` ← `stator_r1`,
   `rotor_fill_r` ← `rotor_r1`), plus always `stator_diameter` and
   `magnet_lamination` (see item 3) — with a value taken from the running
   application (`http://localhost:8001`; if the API doesn't answer, the
   built-in fallback values are used) and an explicit unit in the formula
   (`"12 mm"`, never a bare number for a length).
3. **The one formula that changes:** `stator_up_r` is **never
   renamed**. Instead `stator_diameter` is created
   (`"12 mm"` — value = `stator_up_r` × 2, it was a radius, now it's a diameter), and
   `stator_up_r`'s formula is the ONLY thing that changes: `"stator_diameter / 2"`. The name
   `stator_up_r` stays the same, so every other parameter that already
   referenced it (`motor_d`, `stator_mid_r`, `stator_down_r`, ...) is
   left completely untouched — nothing needs to change for them.

   `magnet_lamination` is created in a similar way (value from `mag_step`:
   0 if `mag_step` equals the motor length — meaning no lamination; otherwise
   as-is), but **`mag_step` is not touched at all** — no rename, no
   new formula. Why: `mag_step` has no single clean formula that
   would always be correct — it must equal the motor length when
   `magnet_lamination = 0`, and equal `magnet_lamination` in every other
   case, and Fusion's expressions have no conditional operator for that
   kind of switch. The value is simply read once when the script runs.

Before applying anything, the script **checks its own plan**: if it turns out to
contain anything other than rename / create / that one formula — the script
will refuse to do anything and change nothing (a safeguard in case of a bug in
the script itself).

If both the old and the new name already exist at the same time — the script
does NOT touch that parameter (conflict, requires a manual decision).

**REPAIR for a model broken by the FIRST version of this script (commit e65e16e,
2026-09-25).** That version, by mistake, simply renamed `stator_up_r` →
`stator_diameter`, keeping the RADIUS value (e.g. 6 mm instead of the
12 mm diameter), and `mag_step` → `magnet_lamination`, keeping the motor length (40 mm)
instead of 0. The current version of the script now detects such a model on its own
(`stator_diameter` present, `stator_up_r` absent, and either some other formula
uses `stator_diameter` as a radius without dividing by 2, or its
value is too small for a diameter; similarly for
`magnet_lamination` == motor length when `mag_step` is absent) and
repairs it itself: renames `stator_diameter` back to `stator_up_r`
(Fusion updates the dependent formulas on its own) and `magnet_lamination` back to
`mag_step`, showing this as a separate "REPAIR" block in the preview
dialog — and only then runs the normal plan (creating
`stator_diameter`/`magnet_lamination` again, correctly this time). If the model
is already fine — the repair does not run, and the normal plan is built as before.

**If the model already has the new (canonical) names** — just skip
this step, the script will show "Nothing to do".

### 2) `fusion360_import_params` — pull geometry FROM the application INTO Fusion

(This is the renamed/refactored `fusion360_sync_params` — same logic,
just under a new name and in a new folder; the old folder
`scripts/fusion360_sync_params/` is also left working, in case it's already
registered in someone's Fusion — but going forward it's better to use the new one.)

Requires that **the application (API) is running** on `localhost:8001`.
Run the script in an open Fusion model — it will pull the current geometry
of the active machine from the application and update/create the parameters under the canonical
names. A summary dialog (updated / created / unchanged / errors) at the
end.

If the API is unavailable (2026-09-25) — the script will ask whether to
import a local Parameter I/O CSV file instead (either its own export or the
add-in's own export — both formats are read, columns are located by name, not
by position).

### 3) `fusion360_export_params` — export geometry FROM Fusion INTO the application

The reverse direction. Run it in an open Fusion model — the script will read
the 33 canonical geometric parameters (it first tries the canonical name;
if it's absent, it takes the old legacy one and recomputes it itself using the same rules
as `fusion360_rename_params`), and offer to save a CSV file (a "Save
as" dialog, the same 6-column CSV format).

From there — like any regular Parameter I/O CSV: load it into the application via the
**"⇧ Fusion CSV"** button on the Geometry tab (a preview of the
changes will be shown before applying it, as usual), or
`POST` it to `/api/fusion/import`. This way of receiving the file in the application already
existed, nothing new was added there.

**Pre-write check (2026-09-25).** Before saving the CSV, the script
computes the same derived radii as the application (stator_outer =
D/2, stator_inner, rotor_outer, rotor_inner, shaft_inner), and checks that
they are positive and decreasing, plus cross-checks `stator_diameter` against
`stator_up_r` (if both are present in the model — it must be exactly ×2). If
something doesn't add up (e.g. the model hasn't been repaired yet — see the
section on `fusion360_rename_params` above), the script does NOT write the file silently: it shows
which check failed, and asks whether to cancel or write
anyway. Also: if `magnet_lamination` already exists under its own name and
still equals the motor length (a typical trace of the first version of the script),
0 is exported instead of that value.

## Summary: typical workflow

- **New model** (or already renamed): `fusion360_import_params` →
  edit in Fusion → `fusion360_export_params` → load the CSV in the application.
- **Old model with old names**: first run
  `fusion360_rename_params` once, then as above.

## Where things live (for reference, not required reading before use)

- Name-mapping table (rationale for each parameter, including
  the contested cases, already approved by the owner on 2026-09-25):
  `docs/FUSION_PARAMETER_MAP_2026-09-25.md`.
- The executable source of truth for the map/conversions: `scripts/fusion_param_common.py`.
- A human-readable copy of the same table: `config/fusion_param_map.yaml`,
  section `legacy_fusion_names_approved_2026_09_25`.
- Offline CSV converter (for a file exported not from the Fusion script but
  from the Parameter I/O add-in directly): `scripts/fusion_param_rename.py`
  (`python scripts/fusion_param_rename.py IN.csv OUT.csv --dry-run`).
  Prints a full "before -> after" diff for every changed row; the
  output row count is always `input + created` — if a row were ever lost,
  the script refuses to write the file (`FusionRenameError`) rather than silently emitting it.
- Tests (run only these, not the whole suite):
  `pytest tests/test_fusion_param_rename.py tests/test_fusion_param_roundtrip.py
  tests/test_fusion_params_csv.py tests/test_fusion_v1_repair_and_export_guard.py`
  (the last one covers repairing a v1-broken model, the export guard, and the CSV-fallback
  import path).
