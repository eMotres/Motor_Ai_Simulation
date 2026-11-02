# PROGRESS — stator outer notches on/off (+ sleeve-contact OD cooling)

Branch: `feat/stator-outer-notches-toggle` (from `origin/pre-migration-freeze-2026-09-15`).
WIP checkpoint 2026-11-03 (owner shutdown). No PR yet. Do not merge.

## Decisions
- Flag `stator_outer_notches` (bool). ABSENT = ON, and the canonical stored form
  of ON is ABSENT (`motor_geometry.canonical_outer_notches`): only `false` is ever
  written. Keeps every die / config / motor_config.yaml block and every hash over
  them (`_geometry_fingerprint`, `_build_sig`, poly/CAD caches, passports) unchanged.
- Whole-lamination dicts (stator_diameter, core_thickness, slot_height,
  tooth_width, tooth2_width, cut_width all present) that are silent on the flag
  mean ON and do not inherit a previous machine's `false`
  (`merge_outer_notches`, used in `_map_api_to_cadquery`/`set_parameters`,
  `merge_geo_override`, `update_current_geometry`, the geometry PUT yaml write).
  Partial overrides (sweep candidates) inherit the live flag like any key.
- GET /api/geometry does NOT fill the flag (absence is its default), so the
  browser's `geo=` stays byte-identical for notched machines.
- Die-change: the flag is a die-level key. `die_shape_diffs` reports it (bool,
  absent = on) so the existing transition (`_transition_config_to_own_die`,
  `_matching_lamination`) handles it; locked dies refuse it via the existing
  423 lock check (canonical value special-cased: absent = true).
- Notch-free stators route to the geometry-driven mesher (iron template has the
  V-notch built into its blocks; `template_solver_halves` refuses OFF).
- Validation for OFF: slot fit limit = pocket outer wall (`cut_x - tooth2_width`)
  vs `stator_inner_radius*sin(slot_angle/2)`; notch-only knobs (cut_width,
  tooth2_width, stator_fillet_r) never named as causes; one-piece message has no
  notch wording.
- geoSig (py `_geo_sig` + web `geoSignature`): stamped `stator_outer_notches:0`
  only when OFF; report `_GEO_SIG_ABSENT_MEANS` = 1.0; duty diffs see it.
- Thermal (owner correction 2026-11-03): default unchanged. New explicit choice
  `stator_od_cooling: direct (default, today's model) | sleeve_contact`.
  sleeve_contact = series G of contact (HOUSING_CONTACT_H x contact arcs x L) +
  radial Al sleeve wall (default 1.5 mm, k 160) + the existing outer BC film on
  2*pi*(r+t)*L, spread over the new `outer_od` facet set (facets on the OD circle).

## Done (code in this commit)
- `geometry/motor_geometry.py`: flag helpers; canonical form in MotorGeometryParams.
- `cadquery_geometry.py`: all three stator builders (CadQuery `_create_stator`,
  `get_2d_mesh_data`, `get_2d_polygons`) honour the flag; OD fillet skipped when
  OFF; polys dict carries `stator_outer_notches`; `stator_od_contact()` (closed
  form, falls back to measuring the polygon for degenerate notches) and
  `measure_od_contact()`.
- `geometry_validation.py`, `masses.py` (section cache key), `fem_solver_2d.py`
  (d-axis symmetric keys), `simulation/geometry_2d.py`, `simulation/mesher.py`,
  `simulation/iron_template.py`, `routes/_validation.py` (SCHEMA_FALLBACK bool
  entry, label "Outer notches", tooltip), `routes/geometry.py` (PUT canonical +
  merge; GET no fill; bool never optimizable), `services/geometry_service.py`,
  `routes/family.py` (lock canon, die_shape_diffs, duty diffs),
  `routes/simulation.py` (fingerprint canonical), `routes/presets.py` +
  `report.py` + `web/src/components/common/geoSig.ts` (signature),
  `routes/fusion.py` (flag excluded from CSV), `mcp_tools.py` (denylist).
- `simulation/thermal_solver_2d.py`: `outer_od` facet set (unused by default).
- `simulation/cooling_models.py`: `sleeve_contact_path()` + defaults.

## Verified
- Old (origin) vs new builder, every die in the main checkout's config/dies
  (14 dies, flag absent AND flag true): stator/rotor/coil/insulation polygons
  and the stator display mesh byte-identical (WKB sha1) — 0 mismatches
  (scratch script, not committed).
- OFF builds one Polygon on every die. CILN28 stator area ON 8238.045 mm2,
  OFF 10422.557 mm2 (+26.52 % stator iron section/mass).
- OD contact fraction (closed form = measured on the polygon, 14 dies agree to
  1e-4). CILN28 ON = 0.6047, OFF = 1.0.
- Module imports OK. NO pytest run yet.

## Left
1. routes/thermal.py: plumb `stator_od_cooling`, `od_sleeve_wall_mm`,
   `od_sleeve_k` through `_validate_field_params` (refuse unknown value; refuse
   sleeve_contact with heat_path housing/both), `_field_cache_key` (append only
   when sleeve_contact), the core solve signature + both GET routes' Query
   params + the request-key list (~line 8272); in the pass loop, when
   sleeve_contact: contact fraction from `stator_od_contact(g, _polys["stator"])`,
   G = `sleeve_contact_path(..., h_out=h_eff)`, surface outer =
   `{"name": "outer", "facets": "outer_od", "G_W_per_K": G, "symmetry_mult": sym,
   "t_sink": t_sink}`; payload `cooling["stator_od"]` block + read-only
   `stator_od_contact_fraction`.
2. thermal_settings.py `cooling_fields` / `cooling_issue` / `cooling_words`
   (send only when sleeve_contact), coupled_continuous_rating key list (~244),
   duty_results cooling keys, web thermalStore + ThermalPanel select (Direct film |
   Sleeve contact; wall mm, k) and contact fraction shown read-only next to the
   housing contact tooltip.
3. Web Geometry tab: checkbox for `type: 'bool'` params in
   `web/src/components/sweep/ParameterVariationTable.tsx` (checked = value !== false,
   send true/false on Recalculate, no sweep toggle); `web/src/lib/slotMouthFit.ts`
   OFF arithmetic (mirror slot_cut_limits).
4. Tests (new file, e.g. tests/test_stator_outer_notches.py): absent==true==pinned
   area/vertex count for public dies (CIANO14 30_10, 100 mm) + optional private
   dies via env dir; OFF = one polygon, outer boundary full circle of R, area >
   ON, slots unchanged; cache keys (poly key, masses `_area_key`,
   `_geometry_fingerprint`) differ ON/OFF and absent==true; validation passes
   OFF; contact fraction ON/OFF; sleeve_contact G ratio ON/OFF = fraction ratio
   (contact term); thermal default byte-identical (compare payload numbers with
   and without the new code path). Then run touched test files one by one:
   test_stator_outline_symmetry, test_geometry_validation, test_masses,
   test_fusion_definitions, test_die_derived_geometry, test_slot_mouth_infeasible,
   test_cad_polygon_quality, thermal route tests (no FEM campaigns).
5. Web: `npx tsc -p tsconfig.app.json --noEmit` in web/, plus geoSig/slotMouthFit tests.
6. Not covered / to report: FreeCAD parametric template
   `cad/motor_templates/segmented_flatwire` always sketches the notch (Full CAD
   construction); `housing_contact_g` (robotics heat-path contact, reported only)
   still uses the full OD pi*D*L — left as is per owner (default unchanged);
   liquid/air modes keep the film on the notch walls (owner confirmed correct).
7. PR into `pre-migration-freeze-2026-09-15`, no merge, no deploy.
