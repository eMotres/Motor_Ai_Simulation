# Geometry parameter definitions: our builder = Fusion 360 (2026-10-30)

Owner, 2026-10-30: «почему у нас геометрия с Fusion не совпадает, вроде совершенно одинаковый
алгоритм построения». Fusion is the design truth. The names were shared, but three of them
were measured differently, so the same numbers drew a different machine. To reproduce Fusion's
outline, CIANO20 200_50 needed `tooth2_width` 5.697 / `magnet_fill_up` 0.473 /
`magnet_fill_down` 0.903 where Fusion has 5.8 / 0.45 / 0.9.

Sources compared: our builder `src/motor_ai_sim/cadquery_geometry.py` (`_create_stator`,
`_create_magnets`, `_create_rotor`, `get_2d_mesh_data`, `get_2d_polygons`), the structured mesher
`simulation/iron_template.py`, the validator `geometry_validation.slot_cut_limits` and its web
twin `web/src/lib/slotMouthFit.ts`. On the Fusion side: the FreeCAD template that reproduces the
Fusion sketches constraint for constraint (`cad/motor_templates/segmented_flatwire/core.py`,
`magnet.py`, `rotor.py`, `common.py`), the Fusion parameter dumps (legacy names, mapped by
`scripts/fusion_param_common.py`) and the Fusion B-rep outlines of two built models.

## What each code measured

Stator sector, wound tooth on +Y (x to the right):

```
      x = 0        tw/2          tw/2+coil_w          leg outer side       sector edge (angle_wire)
        |  tooth     |   slot (coil)   |    half leg      |      notch air      /
        |            |                 |<-- tooth2_width ->|                    /
  OLD:  leg outer x = tw/2 + 2·ins + column + 2·dx + tooth2      (one wire_spacing_x too many)
  NOW:  leg outer x = tw/2 + coil_w + tooth2,  coil_w = column + 2·ins + dx   (Fusion `coil_w`)
```

Magnet, pole frame (magnet axis on +Y), right half; P4 = bottom corner, K = knee, C = top corner:

```
             U (rotor OD, angle fill_up·pitch/2)          OLD                     NOW (Fusion)
            /                                             C on the top arc at     side K→U, C = where it
       C  _/___ top arc r = OD − magnet_up_gap            angle fill_up·pitch/2   meets the top arc
         /
        /   upper side
       K                                                  K radial above P4       K = P4 + down_height
       |  foot (magnet_down_height)                       (same angle as P4)      along the HALF-POLE
       P4 ─── bottom chord, r = rotor_down_r                                      line (angle pitch/2)
          (angle fill_down·pitch/2 from the axis)
```

| key | old builder | Fusion (and the builder now) |
|---|---|---|
| `tooth2_width` | leg width + one extra `wire_spacing_x` | the leg's own width from the slot wall (`tooth2_w`) |
| `magnet_fill_down` | knee on the radial line of P4 | P4 only; the knee comes from the foot direction |
| `magnet_down_height` | foot measured RADIALLY | foot measured along the half-pole line (`Distance mlo`, `Parallel` to the pole line) |
| `magnet_fill_up` | angle of the TOP CORNER on the top arc | angle of the OD point U the upper side aims at (`magnet_fill_up_angle` on `rotor_up_r`) |
| `rotor_hole` | lip x = OD·sin(fill_up·hole·pitch/2) | same formula; its value follows `magnet_fill_up` |

Checked and **the same** in both: `stator_diameter`, `slot_height`, `core_thickness` (slot top is
the chord y = R_out − core, `DistanceY`), `num_seg` / `num_slots_per_segment` /
`num_poles_per_segment`, `air_gap` (rotor OD = bore − gap; the sleeve sits inside the gap),
`tooth_width` (x = ±tw/2), `cut_width` (notch radius from the tangency on the sector edge: the
same algebra), `insulation_thickness` / `wire_width` / `wire_spacing_x` / `wire_spacing_y` /
`wire_height` / `num_wires_per_slot` (coil x1 = tw/2 + ins + dx/2, rows from the slot top),
`magnet_height` (rotor_down_r = OD − magnet_height), `rotor_house_height`, `magnet_up_gap`
(top arc), `magnet_lamination` (axial), `stator_fillet_r` (yoke corners), `stator_fillet_r1`
(tooth tip / foot corners), `rotor_fill_r` (pole tips), `motor_length`. `slot_hs` is unused in
both; `shaft_height`, `sleeve_thickness`, `wire_split` exist only here.

Not shared keys, so not ported; they stay as listed differences:
* Fusion's magnet may round its bottom and knee corners (CIANO20: 0.2 mm; template row
  `magnet_lower_fill_r`); ours rounds the two top corners only (≤ 0.065 mm at the bottom corner).
* Fusion's rotor pocket is the magnet offset by `offset_rotor` (glue clearance, CIANO20 0.045 mm);
  ours is the magnet itself (≤ 0.07 mm at the pocket top).
* Model values that are not definitions: CIANO20's Fusion stator has SHARP tooth tips
  (`stator_fillet_r1` 0, the die has 0.2); the second model's magnet sketch does not use its
  `mag_down_h` (the die correctly has `magnet_down_height` 0).

## Proof against Fusion's outlines

Overlay of the polygons the solver meshes on the Fusion B-rep face outlines (max distance between
the boundaries, both directions, after the best rotation; scripts in the session notes):

| model | build | stator | magnets | rotor poles |
|---|---|---|---|---|
| CIANO20 200_50 (24s20p Ø200) | old code, tuned 5.697 / 0.473 / 0.903 | 0.105 mm | 0.084 mm, 10.9 mm² | 0.183 mm |
| | old code, Fusion's 5.8 / 0.45 / 0.9 | 0.122 mm | 0.233 mm, 82.6 mm² | 0.279 mm |
| | **new code, Fusion's values** (r1 0) | **0.028 mm** | **0.065 mm, 2.0 mm²** | **0.069 mm** |
| Ø150 36s42p Fusion model | old code, hand-tuned values | 0.025 mm | 0.102 mm, 3.0 mm² | 0.059 mm |
| | **new code, Fusion's values** | **0.029 mm** | **0.102 mm, 3.0 mm²** | **0.059 mm** |

What is left is the non-shared items above (bottom-corner fillet, glue offset) and polygon
faceting. The CIANO20 leg's outer side is at x = 19.300 mm in Fusion and in the new build; the
old fit's 5.697 put it at 19.297. Translating the Ø150 model's hand-tuned values with
`fusion_definitions.translate_geometry` gives Fusion's own numbers back to 1e-5 (tooth2 within
the 0.008 mm of the hand fit).

The CIANO20 bench/model cogging gap (~3.7 vs ~1.5 N·m) is relevant here: with Fusion's
`rotor_hole` 0.7 the pocket lips are **0.18 mm per side narrower** than in the stored model
(the stored 0.473 × 0.7 opened them wider than the built motor).

## Stored values: the migration

`scripts/migrate_fusion_definitions.py` (dry-run by default; the orchestrator runs it on the
server). Per die, configuration and workspace `motor_config.yaml` it prints `key: old -> new` and
the outline deviation from the old build BEFORE (code deployed, values not translated) and AFTER:

* `tooth2_width` += `wire_spacing_x` (exact);
* `magnet_fill_down` / `magnet_fill_up` / `rotor_hole`: the top corners and the lips are kept
  exactly; the foot cannot be reproduced exactly when `magnet_down_height` > 0 and
  `magnet_fill_down` < 1 (radial vs half-pole direction), so `fill_down` is chosen for the least
  symmetric-difference area. Typical residual 0.01–0.05 mm in the foot, magnet area −0.05 % (Ø150)
  to −0.9 % (Ø30);
* a configuration that overrides a lamination input (its own `wire_spacing_x`, say) gets its own
  translated value, ADDED to its overrides when it did not carry the key;
* `--fusion-values pins.json` writes Fusion's values for dies with a Fusion reference, or keeps a
  die as stored (`"as_stored"`) whose values were typed in from Fusion verbatim
  (`POST /api/fusion/import` copies values by name and leaves no trace, so only a person can say
  which dies those are);
* one-shot per root (manifest `.fusion_definitions_migrated.json`); stored results are never
  touched. The report lists the machines whose outline changes (rerun) and those whose stamps move
  only.

Order on the server: stop the API, put the code in place, run the dry run, review, `--apply`,
start the API. A die the new code wrote and then translated again would be wrong.

JSON stores that carry these keys (catalog cards, presets, drafts, sweep records) are listed, not
rewritten. Optimizer bounds for `magnet_fill_up` / `magnet_fill_down` keep their numbers; their
meaning moved by the same few percent.
