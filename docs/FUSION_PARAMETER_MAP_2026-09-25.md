# Fusion 360 parameter correspondence — owner's older Fusion models (2026-09-25)

Source: `C:\Users\vadim\Downloads\ExportedParameters.csv` — a Fusion 360 "Parameter
I/O" export (Name, Unit, Expression, Value, Comments, Favorite), 65 user
parameters, from a Fusion model the owner built before motor_ai_sim's own
naming existed.

Canonical side (never renamed): `MotorGeometryParams`
(`src/motor_ai_sim/geometry/motor_geometry.py`), the `geometry_schema` in
`config/motor_config.yaml`, `DERIVED_GEOMETRY_NAMES`
(`src/motor_ai_sim/routes/_validation.py`), and the live Fusion round-trip at
`src/motor_ai_sim/routes/fusion.py` + `scripts/fusion360_sync_params/`.

**Status: PROPOSAL — not yet applied anywhere.** This is item 1 of the task
only (correspondence table for the owner to review); the CSV converter script,
the in-Fusion rename script, and their tests come after the owner confirms or
corrects the rows below, especially the "unsure" ones. The live
`config/fusion_param_map.yaml` `map:` section (read by the running
`/api/fusion` route) has **not** been touched — this table lives in a new,
inert section of that file that nothing reads yet.

Method: every row's canonical guess is derived from (a) the old Fusion
**expression** — several old names are defined *in terms of* other old names,
and those formulas algebraically match our own `derived_params` formulas
closely enough to identify primaries with good confidence — and (b) name/unit/
meaning similarity where the old parameter is a leaf constant with no
supporting formula. Where the evidence conflicts or is too weak, the row is
"unsure" and listed first.

Counts: **20 exact, 29 likely, 16 unsure** (65 total). Of the 65, **25** have
no solver counterpart at all (mechanical parts, tolerances, or pure sketch-
construction helpers) and get a proposed `mech_`-prefixed name, Fusion-only.

---

## UNSURE — needs the owner's confirmation (16 rows)

| Old name | Old value | Evidence | Candidate(s) | Why unsure |
|---|---|---|---|---|
| `rotor_r` | 12.00 mm | `motor_d / 2` | `mech_rotor_shell_r` (Fusion-only) | Scale conflicts with the `rotor_up_r`/`rotor_down_r` chain (3.358 / 1.358 mm) despite the name "rotor". Tied to the *overall* diameter, not the magnet rotor — looks like an outer rotor-can/bell radius, a resolver-rotor radius, or a stray unused parameter. |
| `rotor_down_h` | 0.50 mm | leaf constant, never referenced by another row's expression | `rotor_house_height` | Name fits "rotor housing" positionally (comes right after the magnet in the `rotor_h`/`rotor_down_r` chain), but it's an orphan — nothing in the file proves it, and `r_housing_h` is a second, competing candidate. |
| `r_housing_h` | 0.30 mm | leaf constant, never referenced by another row's expression | `rotor_house_height` **or** `sleeve_thickness` | Name literally contains "housing", favoring `rotor_house_height`; but 0.3 mm is also a very plausible magnet-retaining-sleeve thickness (`sleeve_thickness`, currently 0 in our default config). Pick one meaning with `rotor_down_h` above — they can't both be `rotor_house_height`. |
| `wire_N` | 3 (unitless) | feeds `coil_w = ins_w*2 + (wire_h + wire_dist_x) * wire_N` | `num_wires_per_slot` (best guess) — or `wire_split` / `wire_parallel` | Our schema has three distinct wire-count concepts (stack count, transposed-strip split, parallel paths); the old model has only one count feeding one formula, so which axis it represents isn't determinable from the CSV alone. |
| `mag_step` | 40.00 mm (= `stator_w`) | leaf-equal-to-stack-length | `magnet_lamination` | Plausible but semantics look **inverted**: ours uses `0 = solid magnet`, a full slice length for no slicing; old uses the *full stack length* for (presumably) no slicing. Needs the owner to confirm the axial-magnet-segmentation intent of this old model. |
| `mag_sh` | 0.10 mm | leaf constant | `magnet_up_gap` (weak) | Value is in the right range, but "sh" as likely "shim"/"shell" doesn't clearly mean "radial gap from rotor OD to magnet top". Low confidence. |
| `slot_r` | 0.10 mm | leaf constant | `stator_fillet_r1` (air-gap corner fillet) | Two old names (`slot_r`, `slot_up_r`) are both 0.10 mm and both plausible for the one air-gap-side fillet we have. Can't tell from the CSV which is the slot-bottom vs. slot-mouth fillet, or whether one is actually the OUTER fillet instead. |
| `slot_up_r` | 0.10 mm | leaf constant | `stator_fillet_r1` (air-gap corner fillet) | Same conflict as `slot_r` above — only one of the two (or neither) should map here. |
| `tooth_r` | **−0.10 mm** | `wire_beng_r - 0.5 mm` | none identified | Negative radius — looks like an unused/broken leftover formula rather than a live dimension. Possibly meant a ~0.1 mm tooth-tip fillet analogous to `stator_fillet_r1`, but the sign is wrong for that reading. |
| `AWG` | 1.40 mm | `1.2 mm + 0.2 mm` | none identified (`mech_wire_insulated_od`?) | Named like American Wire Gauge (normally a unitless gauge number) but carries a millimetre value. Our winding model is rectangular (`wire_width` × `wire_height`); this may be an insulated round-wire OD with no counterpart, or leftover from a round-wire version of the design. |
| `sealing_ring_d` | 0.90 mm | leaf constant | `mech_sealing_ring_w`? | 0.9 mm is small for most O-ring/seal *diameters* — may actually be a groove **width**, not a diameter. Fusion-only regardless; flagged for a sanity check on what it dimensions. |
| `bearing_6005_d` | 30.00 mm | leaf constant | `mech_bearing_6005_seat_d` | A "6005" bearing's standard bore is 25 mm (OD 47 mm); 30 mm matches neither. Likely a seat/shoulder diameter rather than the bearing bore itself — needs the owner to say which. |
| `ins_gap` | 0.10 mm | leaf constant | `mech_insulation_gap`? | A second, distinct insulation dimension alongside `ins_w` (0.05 mm). We only have one `insulation_thickness` parameter, so it's unclear whether either, both, or neither corresponds to it. |
| `slot_hs1` | 0.02 (unitless) | leaf constant | `mech_slot_hs1`? | `slot_hs` (0.11) matches our `slot_hs` exactly; `slot_hs1`'s relationship to it (a margin? a secondary/derived variant?) is not stated anywhere in the file. |
| `slot_ds` | 0.80 (unitless) | leaf constant | `mech_slot_ds`? | No obvious schema counterpart; possibly a slot-depth scale ratio, but nothing in the file confirms it. |
| `coil_w` | 0.85 mm (derived) | `ins_w*2 + (wire_h + wire_dist_x) * wire_N` | `mech_coil_bundle_w`? vs. our derived `slot_width` | Stacks `wire_N` wires along the `wire_h` (height) axis; our own derived `slot_width = wire_width + 2*wire_spacing_x + 2*insulation_thickness` is a single-wire cross-section along X with no count multiplier. The two formulas measure different things — axis correspondence unclear, tied to the `wire_N` ambiguity above. |

---

## EXACT matches (20) — same name/meaning, same units, no conversion

| Old name | Canonical | Unit | Evidence |
|---|---|---|---|
| `magnet_h` | `magnet_height` | mm | leaf constant, name/meaning identical |
| `gap` | `air_gap` | mm | leaf constant; `rotor_up_r = stator_down_r - gap` matches our derived `rotor_outer_radius = stator_inner_radius - air_gap` term-for-term |
| `slot_h` | `slot_height` | mm | leaf constant, used identically in `stator_down_r`'s formula as ours in `stator_inner_radius` |
| `core_h` | `core_thickness` | mm | leaf constant, same role as above |
| `stator_down_r` | `stator_inner_radius` (derived) | mm | `stator_up_r - slot_h - core_h` ≡ our `stator_outer_radius - core_thickness - slot_height` (same terms, order swapped — algebraically identical) |
| `magnet_fill_up` | `magnet_fill_up` | ratio | leaf constant, identical name, same 0–1 ratio meaning |
| `magnet_fill_down` | `magnet_fill_down` | ratio | leaf constant, identical name, same 0–1 ratio meaning |
| `N1` | `num_seg` | count | `angle_wire = (360/(N1*6))*1deg` and `angle_mag = (360/(N1*Nm))*1deg` — `N1` plays exactly the role of `num_seg` in `num_slots = num_seg * num_slots_per_segment` (here with a hardcoded 6) and `num_poles = num_seg * num_poles_per_segment` |
| `Nm` | `num_poles_per_segment` | count | from the same `angle_mag` evidence: `N1 * Nm` = 10 poles, matching `num_seg * num_poles_per_segment = num_poles` |
| `angle_wire` | `angle_slot` (derived) | deg | `360 / (N1*6)` = `360 / num_slots` — exactly our derived `angle_slot` |
| `angle_mag` | `angle_pole` (derived) | deg | `360 / (N1*Nm)` = `360 / num_poles` — exactly our derived `angle_pole` |
| `wire_dist_y` | `wire_spacing_y` | mm | leaf constant, name/meaning identical |
| `wire_dist_x` | `wire_spacing_x` | mm | leaf constant, name/meaning identical |
| `slot_hs` | `slot_hs` (hidden/excluded from our own Fusion export) | ratio | leaf constant, identical name; note this is the one our own system already deliberately excludes (`FUSION_EXCLUDED_NAMES`, owner 2026-09-14) |
| `teeth_w` | `tooth_width` | mm | leaf constant, name/meaning identical ("tooth width at the stator outer radius") |
| `tooth2_w` | `tooth2_width` | mm | leaf constant, name/meaning identical |
| `mag_down_h` | `magnet_down_height` | mm | leaf constant, name/meaning identical |
| `wire_h` | `wire_height` | mm | leaf constant, name/meaning identical |
| `wire_w` | `wire_width` | mm | leaf constant, name/meaning identical |
| `rotor_up_r` | `rotor_outer_radius` (derived) | mm | `stator_down_r - gap` ≡ our derived `stator_inner_radius - air_gap` term-for-term |

---

## LIKELY matches (29) — plausible, some inference, no hard conflict

| Old name | Canonical | Unit | Evidence / conversion |
|---|---|---|---|
| `stator_up_r` | `stator_diameter` | mm | leaf constant "12 mm / 2" = 6.00; feeds `motor_d = (stator_up_r + housing_h)*2` and `stator_down_r/stator_mid_r`, playing the role of our derived `stator_outer_radius = stator_diameter/2`. **Conversion: radius → diameter, ×2.** |
| `rotor_h` | `magnet_height` | mm | leaf constant, `rotor_h = magnet_h` (duplicate alias); used in `rotor_down_r = rotor_up_r - rotor_h`, the same role `magnet_height` plays in our `rotor_inner_radius` formula |
| `rotor_w` | `motor_length` | mm | `= stator_w`; stack length applied to a rotor-side sketch dimension |
| `magnet_w` | `motor_length` | mm | `= rotor_w`; same stack-length chain |
| `stator_w` | `motor_length` | mm | leaf constant "40 mm"; the root of the `rotor_w`/`magnet_w`/`mag_step` equality chain, most representative single source for our axial `motor_length` |
| `stator_mid_r` | `mech_stator_mid_r` (Fusion-only, derived) | mm | `stator_up_r - slot_h - core_h/2` — a mid-yoke construction radius with no counterpart in our schema. Canonical expression: `stator_outer_radius - slot_height - core_thickness/2` |
| `rotor_down_r` | `rotor_inner_radius` (derived) | mm | `rotor_up_r - rotor_h` reaches the same "after the magnet" radius as our `rotor_inner_radius = rotor_outer_radius - magnet_height - rotor_house_height`, but the old formula omits a separate housing term (see `rotor_down_h`/`r_housing_h` above) |
| `glue` | `mech_magnet_glue_gap` (Fusion-only) | mm | leaf constant "0.1 mm" — adhesive bond-line gap, a manufacturing tolerance not modeled by the solver |
| `angle` | `mech_segment_angle_deg` (Fusion-only, derived) | deg | `(360/N1)*1deg` = `360 / num_seg` — a sketch construction angle (segment pitch), no counterpart among our exported derived fields |
| `resolver_w` | `mech_resolver_w` (Fusion-only) | mm | leaf constant "16 mm" — resolver width, mechanical only |
| `ins_w` | `insulation_thickness` | mm | leaf constant "0.05 mm"; name differs ("width" vs. "thickness") but meaning (insulation layer around the slot) matches |
| `mag_r` | `magnet_fill_radius` | mm | leaf constant "0.2 mm"; name pattern ("mag" + "r" = magnet radius) matches schema label "Magnet Fillet Radius" |
| `housing_h` | `mech_housing_h` (Fusion-only) | mm | leaf constant "6 mm", feeds `motor_d`; overall housing wall, not modeled by the solver |
| `arc` | `mech_slot_arc_w` (Fusion-only, derived) | mm | `stator_down_r*2*PI*(angle_wire/360deg) - teeth_w` — slot arc-width construction helper. Canonical expression: `stator_inner_radius * 2 * PI * (angle_slot/360deg) - tooth_width` |
| `bolt_head_d` | `mech_bolt_head_d` (Fusion-only) | mm | leaf constant "8.8 mm" |
| `offset_core` | `mech_offset_core` (Fusion-only) | mm | leaf constant "0.006 mm" — CAD fit tolerance |
| `dov_d` | `mech_dowel_d` (Fusion-only) | mm | leaf constant "3 mm"; "dov" reads as "dowel" |
| `wire_beng_r` | `mech_wire_bend_r` (Fusion-only, derived) | mm | `wire_h * 2` — wire bend-radius manufacturing constraint. Canonical expression: `2 * wire_height` |
| `bolt_h` | `mech_bolt_h` (Fusion-only) | mm | leaf constant "3 mm" |
| `bolt_d` | `mech_bolt_d` (Fusion-only) | mm | leaf constant "2.5 mm" |
| `offset_tooth` | `mech_offset_tooth` (Fusion-only) | mm | leaf constant "0.009 mm" — CAD fit tolerance |
| `offset_rotor` | `mech_offset_rotor` (Fusion-only) | mm | leaf constant "0.025 mm" |
| `offset_bearing` | `mech_offset_bearing` (Fusion-only) | mm | leaf constant "0.04 mm" |
| `offset_radiator` | `mech_offset_radiator` (Fusion-only) | mm | leaf constant "0.15 mm" |
| `offset_holder` | `mech_offset_holder` (Fusion-only) | mm | leaf constant "0.08 mm" |
| `stator_r` | `stator_fillet_r` | mm | leaf constant "0.5 mm"; the "other" fillet name, complementing `slot_r`/`slot_up_r` (air-gap side) as the outer-corner one |
| `mag_hole` | `rotor_hole` | ratio | leaf constant "0.7"; unitless ratio, name meaning ("hole ratio") matches schema label "Rotor Hole" |
| `cut_down` | `cut_width` | mm | leaf constant "1 mm"; name differs ("down" vs. "width") but both describe the same slot-opening "cut" feature |

---

## Fusion-only, no solver counterpart (mechanical / tolerances)

These get a canonical name in our style under the `mech_` prefix so the
correspondence table is complete, but they are **not** solver geometry keys
and will never be written into `config/motor_config.yaml`'s `geometry:`
section:

`mech_motor_od` (← `motor_d`), `mech_housing_h` (← `housing_h`),
`mech_magnet_glue_gap` (← `glue`), `mech_segment_angle_deg` (← `angle`),
`mech_resolver_w` (← `resolver_w`), `mech_stator_mid_r` (← `stator_mid_r`),
`mech_slot_arc_w` (← `arc`), `mech_wire_bend_r` (← `wire_beng_r`),
`mech_bolt_head_d` (← `bolt_head_d`), `mech_bolt_h` (← `bolt_h`),
`mech_bolt_d` (← `bolt_d`), `mech_dowel_d` (← `dov_d`),
`mech_offset_core` (← `offset_core`), `mech_offset_tooth` (← `offset_tooth`),
`mech_offset_rotor` (← `offset_rotor`), `mech_offset_bearing` (← `offset_bearing`),
`mech_offset_radiator` (← `offset_radiator`), `mech_offset_holder` (← `offset_holder`).

Plus the seven "unsure but almost certainly Fusion-only" rows from the top
table (`mech_bearing_6005_seat_d`, `mech_sealing_ring_w`,
`mech_insulation_gap`, `mech_slot_hs1`, `mech_slot_ds`,
`mech_wire_insulated_od` for `AWG`, `mech_coil_bundle_w` for `coil_w`) —
listed there rather than here because their exact meaning still needs the
owner's confirmation, not just their canonical spelling.

---

## Machine-readable form

The same table (all 65 rows, with `confidence`, `fusion_only`, `derived`,
the old `expression`, and a `canonical_expression` for every derived row) is
in `config/fusion_param_map.yaml` under the new `legacy_fusion_names_2026_09_25`
key. That key is **not** read by `src/motor_ai_sim/routes/fusion.py` (which
only reads `map:`) — it is inert data for the owner to review and for the
future converter/rename scripts to consume once confirmed.
